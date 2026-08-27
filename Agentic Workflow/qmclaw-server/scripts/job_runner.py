"""
Job Runner Server - Persistent subprocess with pre-initialized backend.

Architecture:
  1. Start up, initialize LabRAD/backend once (20s)
  2. Enter event loop — read Base64-encoded code from stdin
  3. Execute code, write JSON result to stdout
  4. Repeat step 2-3 (no re-init needed)

Cancellation: poll a CANCEL_FILE every 0.5s during execution.

Usage:
  echo "<base64-code>" | python job_runner.py <job_id>
  # Or for persistent mode (no re-init):
  python job_runner.py --interactive
"""

import os
import sys
import json
import base64
import time
import signal
import traceback
import tempfile
import urllib.parse

# PLOTS_DIR shared with Express server
PLOTS_DIR = os.environ.get("PLOTS_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "..", "qmclaw-web", "public", "plots"))
from io import StringIO


def _sanitize_string(s):
    """Remove or replace surrogate characters that can't be encoded as UTF-8."""
    if not isinstance(s, str):
        return str(s) if s is not None else ""
    try:
        # Test if string can be encoded - if not, sanitize it
        s.encode('utf-8')
        return s
    except UnicodeEncodeError:
        # Remove lone surrogates and any other problematic characters
        result = []
        for char in s:
            try:
                # Try to encode each character - lone surrogates will fail
                char.encode('utf-8')
                result.append(char)
            except UnicodeEncodeError:
                pass  # Skip problematic characters
        return ''.join(result)


# ── Paths ─────────────────────────────────────────────────────────────────────

# Default to relative paths from script location, can be overridden via env vars
_DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
BACKEND_DIR = os.environ.get("BACKEND_DIR", os.path.join(_DEFAULT_ROOT, "measure_scripts", "measure_scripts", "sq_workflow"))
MEASURE_SCRIPTS = os.environ.get("MEASURE_SCRIPTS", os.path.join(_DEFAULT_ROOT, "measure_scripts", "measure_scripts"))

sys.path.insert(0, MEASURE_SCRIPTS)
sys.path.insert(0, BACKEND_DIR)

# ── IPython patch (suppress UI) ───────────────────────────────────────────────

class _FakeIp:
    def run_line_magic(self, n, l): pass
    def run_cell(self, c): pass

import IPython.core.getipython
IPython.core.getipython.get_ipython = lambda: _FakeIp()

os.environ["SDL_VIDEODRIVER"] = "dummy"

# ── Session Config ─────────────────────────────────────────────────────────────
# Import session config module
SESSION_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "session.json")

def _load_session_config():
    """Load session config from JSON file."""
    default = {'user': 'LQHL', 'path': ['test', '20260324']}
    try:
        if os.path.exists(SESSION_CONFIG_FILE):
            with open(SESSION_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config.get('session', default)
    except (json.JSONDecodeError, IOError):
        pass
    return default

def _save_session_config(user: str, path: list):
    """Save session config to JSON file."""
    config_dir = os.path.dirname(SESSION_CONFIG_FILE)
    os.makedirs(config_dir, exist_ok=True)
    config = {'session': {'user': user, 'path': path}}
    with open(SESSION_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def _get_full_session_path():
    """Get full LabRAD session path from config."""
    cfg = _load_session_config()
    return ['', cfg['user']] + cfg['path']

# ── Rules Config ───────────────────────────────────────────────────────────────
# Load rules from config file
RULES_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "rules.json")

def _load_rules_config():
    """Load rules config from JSON file."""
    default_result = {"rules": []}
    try:
        if os.path.exists(RULES_CONFIG_FILE):
            with open(RULES_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            # Return the full config with 'rules' key for consistency
            return {"rules": config.get('rules', [])}
    except (json.JSONDecodeError, IOError):
        pass
    return default_result

def _save_rules_config(rules: list):
    """Save rules config to JSON file."""
    config_dir = os.path.dirname(RULES_CONFIG_FILE)
    os.makedirs(config_dir, exist_ok=True)
    config = {'rules': rules}
    with open(RULES_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

# Load rules at module init
_default_rules = _load_rules_config()
print(f"INIT: Loaded {len(_default_rules)} rules from config file", file=sys.stderr, flush=True)

# ── Backend initialization (done once at startup) ─────────────────────────────

print("INIT: Starting backend initialization...", file=sys.stderr, flush=True)
sys.stderr.flush()

_cxn = _s = _sq = _data = _qter = _BasicTuner = _generate_qubit = _backend = None
_current_session_path = []  # Track current session path for qubit reloading

def reload_qubits(session_path):
    """Reload qubits for the given session path."""
    global _s, _data, _qter, _backend, _current_session_path
    import labrad
    from lqms.pyle.workflow import switchSession
    from lqms.utils.save_path import get_info_path
    from lqms.data_process import dataAnalysisCore as dc

    print(f"RELOAD_QUBITS: Starting reload for path={session_path}", file=sys.stderr, flush=True)
    _current_session_path = session_path

    try:
        # Switch session
        if session_path:
            # Extract user from path (e.g., ['', 'LQHL', 'test', '20260324'] -> 'LQHL')
            user = session_path[1] if len(session_path) > 1 else 'LQHL'
        else:
            user = 'LQHL'

        print(f"RELOAD_QUBITS: Switching to session path={session_path}, user={user}", file=sys.stderr, flush=True)

        # Create new session switcher
        _s = switchSession(_cxn, user=user)
        print(f"RELOAD_QUBITS: Created new session switcher, _s keys count={len(list(_s.keys()))}", file=sys.stderr, flush=True)

        # Reload info and data lab
        info_path = get_info_path(_s)
        print(f"RELOAD_QUBITS: info_path={info_path}, exists={os.path.exists(info_path)}", file=sys.stderr, flush=True)
        info = dc.InfoBase(info_path) if os.path.exists(info_path) else None
        _data = dc.DataLab(session_path, _cxn.data_vault, dv_type='data_vault')

        # Also update _qter's data reference if it exists
        if _qter is not None and hasattr(_qter, 'data'):
            _qter.data = _data
            print(f"RELOAD_QUBITS: Updated _qter.data to new DataLab", file=sys.stderr, flush=True)

        # Regenerate qubits from the new session's info
        if _generate_qubit:
            from lqms.measure import generate_qubit, generate_coupler
            print(f"RELOAD_QUBITS: Calling generate_qubit with session={session_path}", file=sys.stderr, flush=True)
            all_qubits = generate_qubit({'s': _s}, info=info, sample=_s)
            all_couplers = generate_coupler({'s': _s}, info=info, sample=_s)
            print(f"RELOAD_QUBITS: generate_qubit returned {len(all_qubits)} qubits: {list(all_qubits.keys())[:10]}...", file=sys.stderr, flush=True)

            # Inject qubits into _s registry
            for qname, qobj in all_qubits.items():
                _s._set(qname, qobj)

            print(f"RELOAD_QUBITS: Injected {len(all_qubits)} qubits into _s", file=sys.stderr, flush=True)

        # Count qubits in _s after reload
        q_keys = [k for k in _s.keys() if k.startswith('q')]
        print(f"RELOAD_QUBITS: Final _s has {len(q_keys)} qubits", file=sys.stderr, flush=True)

        return True
    except Exception as e:
        import traceback as tb
        print(f"RELOAD_QUBITS ERROR: {tb.format_exc()}", file=sys.stderr, flush=True)
        return False

def init_backend(max_retries=3, delay=5):
    global _cxn, _s, _sq, _data, _qter, _BasicTuner, _generate_qubit, _backend, _current_session_path
    last_error = None
    for attempt in range(max_retries):
        try:
            import backend
            _backend = backend
            from backend import cxn, s, sq, data, qter, BasicTuner, generate_qubit
            _cxn = cxn; _s = s; _sq = sq; _data = data
            _qter = qter; _BasicTuner = BasicTuner; _generate_qubit = generate_qubit

            # Load session path from config file
            _current_session_path = _get_full_session_path()
            print(f"INIT: Config session path = {_current_session_path}", file=sys.stderr, flush=True)

            # Reload _data with the configured session path
            from lqms.data_process import dataAnalysisCore as dc
            _data = dc.DataLab(_current_session_path, _cxn.data_vault, dv_type='data_vault')
            print(f"INIT: Backend ready — sq={sq}, session={_current_session_path}", file=sys.stderr, flush=True)
            return True
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                print(f"INIT WARNING: attempt {attempt+1} failed ({e}), retrying in {delay}s...", file=sys.stderr, flush=True)
                import time; time.sleep(delay)
            else:
                print(f"INIT ERROR: {traceback.format_exc()}", file=sys.stderr, flush=True)
    print(f"FATAL: Backend init failed after {max_retries} attempts: {last_error}", file=sys.stderr, flush=True)
    return False

if not init_backend():
    sys.exit(1)

print("INIT: Ready to accept jobs", file=sys.stderr, flush=True)

# ── Cancellation ──────────────────────────────────────────────────────────────

def check_cancel(job_id):
    flag = os.path.join(os.environ.get("TEMP", "/tmp"), f"qmclaw_cancel_{job_id}.flag")
    return os.path.exists(flag)

# ── Job execution ─────────────────────────────────────────────────────────────

def run_job(code_b64, job_id):
    code = base64.b64decode(code_b64).decode("utf-8", errors="replace")

    if check_cancel(job_id):
        return {"status": "cancelled", "stdout": "", "stderr": "Cancelled before execution", "error": ""}

    stdout_buf = StringIO()
    stderr_buf = StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    result_status = "success"
    result_error = ""

    # Poll for cancellation during long-running experiments
    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf

        exec_globals = {
            "__name__": "__job__",
            "cxn": _cxn, "s": _s, "sq": _sq, "data": _data,
            "qter": _qter, "BasicTuner": _BasicTuner,
            "generate_qubit": _generate_qubit,
            "backend": _backend,
        }

        # Get qubits from backend module (where generate_qubit injects them)
        # The backend module has qubits like q10lu1, q10lu10 etc. injected by generate_qubit
        if _backend:
            for _n in dir(_backend):
                if _n.startswith('q') and not _n.startswith('qq'):
                    obj = getattr(_backend, _n)
                    # Accept actual qubit objects or RegistryWrapper objects
                    if hasattr(obj, 'qName') or hasattr(obj, 'regs') or 'RegistryWrapper' in str(type(obj)):
                        exec_globals[_n] = obj

        # Also try to get qubits from _s registry (for session-switched qubits)
        if _s:
            qubit_count = 0
            for _n in _s.keys():
                if _n.startswith('q') and _n not in exec_globals:
                    try:
                        obj = _s[_n]
                        # Accept qubit-like objects
                        if hasattr(obj, 'qName') or hasattr(obj, 'regs') or 'RegistryWrapper' in str(type(obj)):
                            exec_globals[_n] = obj
                            qubit_count += 1
                    except:
                        pass
            print(f"EXEC: Added {qubit_count} qubits from _s registry", file=sys.stderr)

        # Debug: check q10lu1
        if 'q10lu1' in exec_globals:
            val = exec_globals['q10lu1']
            print(f"EXEC: q10lu1 type={type(val).__name__}", file=sys.stderr)
        else:
            print(f"EXEC: q10lu1 NOT in exec_globals", file=sys.stderr)

        # Debug: log session info before execution
        print(f"EXEC DEBUG: _current_session_path={_current_session_path}", file=sys.stderr, flush=True)
        print(f"EXEC DEBUG: _data session={getattr(_data, 'session', 'N/A')}", file=sys.stderr, flush=True)

        exec(code, exec_globals)

        sys.stdout = old_out
        sys.stderr = old_err

    except KeyboardInterrupt:
        sys.stdout = old_out
        sys.stderr = old_err
        result_status = "cancelled"
        result_error = "Job cancelled by user"

    except SystemExit as e:
        sys.stdout = old_out
        sys.stderr = old_err
        result_status = "exited"
        result_error = str(e)

    except Exception as e:
        sys.stdout = old_out
        sys.stderr = old_err
        result_status = "error"
        result_error = traceback.format_exc()

    return {
        "status": result_status,
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "error": result_error,
    }

# ── Workflow execution ─────────────────────────────────────────────────────────

def resolve_template(value, node_results, workflow_ctx):
    """Recursively resolve {{...}} templates in a value."""
    if isinstance(value, str):
        import re
        def replacer(m):
            path = m.group(1).strip()
            # node_results["n2"]["result"]["metrics"]["F0"]
            # Template: {{nodes.n2.F0}} or {{nodes.n2.stdout}} or {{F0}}
            if path.startswith("nodes."):
                parts = path[6:].split(".", 1)
                nid = parts[0]
                attr = parts[1] if len(parts) > 1 else "stdout"
                node = node_results.get(nid, {})
                result = node.get("result", {})
                # Try direct attribute first
                val = result.get(attr, None)
                if val is not None:
                    return str(val)
                # Try inside metrics sub-object (for analyze nodes)
                if attr != "metrics":
                    mval = result.get("metrics", {}).get(attr, None)
                    if mval is not None:
                        return str(mval)
                # Fallback: check workflow_ctx for "nodes.nX.attr" key (metrics stored flat)
                ctx_key = f"nodes.{nid}.{attr}"
                if ctx_key in workflow_ctx:
                    return str(workflow_ctx[ctx_key])
                return m.group(0)
            elif path in workflow_ctx:
                return str(workflow_ctx[path])
            return m.group(0)
        return re.sub(r"\{\{([^}]+)\}\}", replacer, value)
    elif isinstance(value, dict):
        return {k: resolve_template(v, node_results, workflow_ctx) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_template(v, node_results, workflow_ctx) for v in value]
    return value


def run_workflow_node(node, node_results, workflow_ctx, check_cancel_fn):
    """Execute a single workflow node and return its result with input/output details."""
    node_id = node["id"]
    node_type = node.get("type", "experiment")
    config = node.get("config", {})

    print(f"WORKFLOW_NODE: {node_id} ({node_type})", file=sys.stderr, flush=True)

    # Track execution time
    start_time = time.time()

    if check_cancel_fn and check_cancel_fn("workflow"):
        return {"nodeId": node_id, "type": node_type, "status": "cancelled", "stdout": "", "error": "Workflow cancelled"}

    # Build node input information
    node_input = {
        "config": dict(config),  # Original config
        "resolvedContext": {},    # Resolved template values
    }

    # Resolve template variables in config
    for key, value in config.items():
        resolved = resolve_template(value, node_results, workflow_ctx)
        node_input["resolvedContext"][key] = resolved

    # Collect upstream results references
    upstream_results = {}
    depends = node.get("depends", [])
    for dep_id in depends:
        if dep_id in node_results:
            dep_result = node_results[dep_id].get("result", {})
            upstream_results[dep_id] = {
                "status": dep_result.get("status", "unknown"),
                "metrics": dep_result.get("metrics", {}),
                "stdout": dep_result.get("stdout", "")[:200] if dep_result.get("stdout") else "",  # Truncate for storage
            }
    node_input["upstreamResults"] = upstream_results

    result = {"nodeId": node_id, "type": node_type, "status": "running", "stdout": "", "stderr": "", "error": "", "plotPath": None}

    # Build exec globals (same as run_job) — available to all node types
    w_exec_globals = {
        "__name__": "__job__",
        "cxn": _cxn, "s": _s, "sq": _sq, "data": _data,
        "qter": _qter, "BasicTuner": _BasicTuner,
        "generate_qubit": _generate_qubit,
        "backend": _backend,
    }
    if _backend:
        for _n in dir(_backend):
            if _n.startswith("q") and not _n.startswith("qq"):
                w_exec_globals[_n] = getattr(_backend, _n)

    try:
        if node_type == "experiment":
            fn_name = config.get("fn", "")
            qubit = config.get("qubit", "{{qubit}}")
            qubit = resolve_template(qubit, node_results, workflow_ctx)
            params = resolve_template(config.get("params", {}), node_results, workflow_ctx)

            # Debug: check qubit availability
            print(f"WORKFLOW_DEBUG: qubit={qubit}, _s={type(_s).__name__ if _s else None}", file=sys.stderr, flush=True)

            # Convert qubit name to qubit object if needed
            qubit_obj = qubit
            if isinstance(qubit, str):
                # Try to get qubit object from _s registry
                if _s and qubit in _s:
                    qubit_obj = _s[qubit]
                    print(f"WORKFLOW_DEBUG: resolved qubit '{qubit}' to object {type(qubit_obj).__name__}", file=sys.stderr, flush=True)
                # Also try _backend
                elif _backend and hasattr(_backend, qubit):
                    qubit_obj = getattr(_backend, qubit)
                    print(f"WORKFLOW_DEBUG: resolved qubit '{qubit}' from _backend", file=sys.stderr, flush=True)
                else:
                    q_keys = [k for k in (_s.keys() if _s else []) if k.startswith('q')]
                    print(f"WORKFLOW_DEBUG: qubit '{qubit}' NOT found in _s! Available: {q_keys[:10]}...", file=sys.stderr, flush=True)

            # Always set _current_qubit in exec globals
            w_exec_globals["_current_qubit"] = qubit_obj

            # Build function call code - use _current_qubit for qubit object
            params_str = ", ".join(f"{k}={repr(v)}" for k, v in params.items())
            call_code = f"{fn_name}(_current_qubit, {params_str})"
            print(f"WORKFLOW_EXEC: {call_code}", file=sys.stderr, flush=True)

            # Capture output
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            old_out, old_err = sys.stdout, sys.stderr
            try:
                sys.stdout = stdout_buf
                sys.stderr = stderr_buf
                exec(call_code, w_exec_globals)
                sys.stdout = old_out
                sys.stderr = old_err
            except:
                sys.stdout = old_out
                sys.stderr = old_err
                raise

            # Get plot
            import matplotlib.pyplot as plt
            fig = plt.gcf()
            plot_path = None
            if fig and fig.get_size_inches().prod() > 0:
                import matplotlib
                matplotlib.use("Agg")
                _plots_dir = os.environ.get("QMCLAW_PLOTS_DIR", PLOTS_DIR)
                os.makedirs(_plots_dir, exist_ok=True)
                _path = os.path.join(_plots_dir, f"wf_{node_id}.png")
                fig.savefig(_path, dpi=150, bbox_inches="tight")
                plot_path = _path
                print(f"QMCLAW_PLOT:{_path}", file=sys.stderr, flush=True)
                plt.close(fig)

            result["status"] = "completed"
            result["stdout"] = stdout_buf.getvalue()
            result["plotPath"] = plot_path
            # Parse metrics from stdout
            try:
                result["metrics"] = parse_metrics(stdout_buf.getvalue())
            except:
                result["metrics"] = {}

        elif node_type == "quality_gate":
            # Pass/fail on a metric from a previous node
            ref = config.get("ref", "")
            metric_name = config.get("metric", "SNR")
            threshold = float(config.get("threshold", 0))
            direction = config.get("direction", "above")  # "above" or "below"
            ref_result = node_results.get(ref, {}).get("result", {})
            metrics = ref_result.get("metrics", {})
            value = metrics.get(metric_name, None)

            result["type"] = "quality_gate"
            if value is None:
                result["status"] = "failed"
                result["stdout"] = f"Metric '{metric_name}' not found in {ref}. Available: {list(metrics.keys())}"
            elif direction == "above" and value >= threshold:
                result["status"] = "passed"
                result["stdout"] = config.get("pass_msg", f"✅ {metric_name}={value:.4f} >= {threshold} — PASSED")
                result["metrics"] = {"value": value, "threshold": threshold, "decision": "pass"}
            elif direction == "below" and value <= threshold:
                result["status"] = "passed"
                result["stdout"] = config.get("pass_msg", f"✅ {metric_name}={value:.4f} <= {threshold} — PASSED")
                result["metrics"] = {"value": value, "threshold": threshold, "decision": "pass"}
            else:
                result["status"] = "failed"
                result["stdout"] = config.get("fail_msg", f"❌ {metric_name}={value:.4f} {'>=' if direction=='above' else '<='} {threshold} — FAILED")
                result["metrics"] = {"value": value, "threshold": threshold, "decision": "fail"}

        elif node_type == "adjust_params":
            # Update a qubit parameter based on previous analysis
            param = config.get("param", "")
            value = config.get("value", "")
            qubit = config.get("qubit", "{{qubit}}")
            qubit = resolve_template(qubit, node_results, workflow_ctx)
            # Try to parse value as a template or literal
            resolved_value = resolve_template(str(value), node_results, workflow_ctx)
            result["type"] = "adjust_params"
            try:
                # Build Python code to set the parameter
                adj_code = f"s['{qubit}'].{param} = {resolved_value}"
                print(f"ADJUST_PARAMS: {adj_code}", file=sys.stderr, flush=True)
                exec(adj_code, w_exec_globals)
                result["status"] = "completed"
                result["stdout"] = f"Set {qubit}.{param} = {resolved_value}"
                result["metrics"] = {"param": param, "value": resolved_value}
            except Exception as e:
                result["status"] = "failed"
                result["error"] = f"Failed to set {qubit}.{param}: {e}"

        elif node_type == "decision":
            # Smart LLM-based decision node
            mode = config.get("mode", "analysis")  # 'analysis' | 'intent'
            rules_context_id = config.get("rulesContextId", "")  # ID of context node with rules
            intent_prompt = config.get("intentPrompt", "分析需求并生成实验列表")
            qubit = config.get("qubit", "{{qubit}}")
            qubit = resolve_template(qubit, node_results, workflow_ctx)

            # Output variable names (for downstream node reference)
            # Default to "symptom" and "recommendations", but can be customized
            symptom_output_var = config.get("symptomOutputVar", "symptom")
            recommendations_output_var = config.get("recommendationsOutputVar", "recommendations")

            # Initialize output fields with defaults (will be updated after LLM call)
            result["symptom"] = ""
            result["recommendations"] = "[]"
            result["reasoning"] = ""
            result["matchedRules"] = []

            # LLM configuration
            model = config.get("model", "gpt-4o")
            provider = config.get("_modelProvider", "")  # Resolved from model registry
            base_url = config.get("_modelBaseUrl", "")   # Custom base URL if set
            temperature = float(config.get("temperature", 0.3))
            max_tokens = int(config.get("maxTokens", 1000))
            system_prompt = config.get("systemPrompt", "")

            # Infer provider from model name if not explicitly set
            if not provider:
                provider = infer_provider_from_model(model)
                print(f"WORKFLOW_DEBUG: Inferred provider '{provider}' from model '{model}'", file=sys.stderr, flush=True)

            # Get rules: first try Context node, then fall back to config file
            rules = []
            rules_source = "config file"
            print(f"WORKFLOW_DEBUG: decision node rules handling, rules_context_id={rules_context_id}", file=sys.stderr, flush=True)
            print(f"WORKFLOW_DEBUG: node_results keys={list(node_results.keys())}", file=sys.stderr, flush=True)
            if rules_context_id:
                context_node_result = node_results.get(rules_context_id, {})
                print(f"WORKFLOW_DEBUG: context_node_result type={type(context_node_result)}, value={context_node_result}", file=sys.stderr, flush=True)
                if not isinstance(context_node_result, dict):
                    context_node_result = {}
                context_node_config = context_node_result.get("result", {}).get("config", {})
                rules_config = context_node_config.get("rules", "[]")
                print(f"WORKFLOW_DEBUG: rules_config type={type(rules_config)}, value={str(rules_config)[:200]}", file=sys.stderr, flush=True)
                try:
                    if isinstance(rules_config, str):
                        rules_data = json.loads(rules_config)
                    else:
                        rules_data = rules_config
                    print(f"WORKFLOW_DEBUG: rules_data type={type(rules_data)}", file=sys.stderr, flush=True)
                    if isinstance(rules_data, dict):
                        rules = rules_data.get("rules", [])
                    elif isinstance(rules_data, list):
                        rules = rules_data
                    else:
                        rules = []
                    if rules:
                        rules_source = f"context node {rules_context_id}"
                        print(f"WORKFLOW_DEBUG: Loaded {len(rules)} rules from context node {rules_context_id}", file=sys.stderr, flush=True)
                    else:
                        print(f"WORKFLOW_DEBUG: Context node {rules_context_id} has no rules, using config file", file=sys.stderr, flush=True)
                except Exception as e:
                    print(f"WORKFLOW_DEBUG: Failed to parse rules from {rules_context_id}: {e}, using config file", file=sys.stderr, flush=True)

            # Fall back to config file rules if no rules from Context node
            if not rules:
                rules = _default_rules.get("rules", [])
                rules_source = "config file"
                print(f"WORKFLOW_DEBUG: Using {len(rules)} rules from config file", file=sys.stderr, flush=True)

            # Build context from previous results (Analyze nodes) - plain text format
            metrics_context = ""
            metrics_data = {}
            for nid, nres in node_results.items():
                if nid.startswith("_") or not isinstance(nres, dict):
                    continue  # Skip internal keys like _workflow_nodes
                r = nres.get("result", {})
                metrics = r.get("metrics", {})
                if metrics:
                    metrics_context += f"\n[{nid}] metrics:"
                    for k, v in metrics.items():
                        metrics_context += f"\n  - {k}={v}"
                    metrics_data[nid] = metrics
                stdout = r.get("stdout", "")
                if stdout and len(stdout) < 500:
                    metrics_context += f"\n[{nid}] output: {stdout}"

            # Get API key based on provider
            api_key = get_api_key_for_provider(provider)
            print(f"WORKFLOW_DEBUG: API key check - provider='{provider}', model='{model}', api_key_provided={bool(api_key)}", file=sys.stderr, flush=True)
            if not api_key:
                result["status"] = "skipped"
                result["stdout"] = f"No API key set for provider '{provider}', skipping LLM decision"
                result["error"] = f"LLM decision skipped - no API key for provider '{provider}'. Please set the appropriate environment variable."
            else:
                # Build system prompt based on mode
                if not system_prompt:
                    if mode == "analysis":
                        system_prompt = f"""你是一个量子比特校准助手。你的任务是根据实验数据分析当前量子比特的状态，并给出实验建议。

## 可用的实验函数：
- sq.iqraw: IQ 原始数据测量，用于评估 SNR 和分离度
- sq.s21: 腔体透射测量，用于验证腔体耦合状态
- sq.ramsey_df: Ramsey 退相干测量，用于诊断 T2
- sq.piamp: π 脉冲幅度测量，用于优化门保真度
- sq.xeb: 交叉熵基准测试，用于测量门保真度

## 规则库（来自 {rules_source}）：
{json.dumps(rules, ensure_ascii=False, indent=2)}

## 你的任务：
1. 分析输入的实验指标，找出匹配的症状
2. 根据规则推荐合适的实验
3. 用 LLM 综合分析，给出诊断结论
4. 返回结构化的推荐列表

## 返回格式（必须严格遵循）：
{{
  "symptom": "当前状态描述，如：SNR=1.2偏低，T2=420ns偏短",
  "recommendations": [
    {{
      "fn": "sq.iqraw",
      "qubit": "{qubit}",
      "params": {{"do_plot": true}},
      "reason": "推荐原因"
    }}
  ],
  "reasoning": "详细的分析推理过程",
  "matchedRules": ["匹配的规则名称列表"]
}}"""
                    else:  # intent mode
                        system_prompt = f"""你是一个量子实验规划助手。你的任务是将用户的自然语言需求转化为具体的实验序列。

## 可用的实验函数：
- sq.iqraw: IQ 原始数据测量，用于评估 SNR 和分离度
- sq.s21: 腔体透射测量，用于验证腔体耦合状态
- sq.ramsey_df: Ramsey 退相干测量，用于诊断 T2
- sq.piamp: π 脉冲幅度测量，用于优化门保真度
- sq.xeb: 交叉熵基准测试，用于测量门保真度

## 返回格式（必须严格遵循）：
{{
  "intent": "用户需求摘要",
  "recommendations": [
    {{
      "fn": "实验函数名",
      "qubit": "{qubit}",
      "params": {{"do_plot": true}},
      "reason": "为什么需要这个实验"
    }}
  ],
  "reasoning": "规划理由"
}}"""

                # Build user message - plain text format
                if mode == "analysis":
                    user_message = f"""实验分析结果:{metrics_context}

请分析当前量子比特状态并给出实验建议。"""
                else:
                    user_message = intent_prompt

                decision = smart_decision(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    provider=provider,
                    base_url=base_url
                )

                # Parse decision response - recommendations should be JSON string
                decision_result = decision if isinstance(decision, dict) else {}
                symptom = decision_result.get("symptom", "")
                recommendations = decision_result.get("recommendations", [])
                reasoning = decision_result.get("reasoning", "")
                matched_rules = decision_result.get("matchedRules", [])
                debug_info = decision_result.get("_debug", {}) if isinstance(decision_result, dict) else {}

                # Store recommendations as JSON string (for Experiment node batchConfig)
                recommendations_json = json.dumps(recommendations, ensure_ascii=False)
                reasoning_str = reasoning if isinstance(reasoning, str) else str(reasoning)

                result["status"] = "completed"
                # Include full conversation details for debugging
                result["conversation"] = {
                    "model": model,
                    "provider": provider,
                    "temperature": temperature,
                    "maxTokens": max_tokens,
                    "systemPrompt": system_prompt,
                    "userMessage": user_message,
                    "metricsContext": metrics_context,
                    "rules": rules,
                    "messagesSent": debug_info.get("messages_sent", []),
                    "rawResponse": decision,
                    "symptom": symptom,
                    "recommendations": recommendations,  # Array for UI display
                    "recommendationsJson": recommendations_json,  # JSON string for batchConfig
                    "reasoning": reasoning_str,
                    "matchedRules": matched_rules,
                }
                result["stdout"] = json.dumps({
                    "symptom": symptom,
                    "recommendations": recommendations,  # Array in stdout for parse_metrics
                    "reasoning": reasoning_str,
                    "matchedRules": matched_rules
                }, ensure_ascii=False, indent=2)
                result["symptom"] = symptom
                result["recommendations"] = recommendations_json  # JSON string - Experiment node will parse
                result["reasoning"] = reasoning_str
                result["matchedRules"] = matched_rules
                result["metrics"] = {
                    "mode": mode,
                    "rulesMatched": len(matched_rules),
                    "recommendationsCount": len(recommendations)
                }

        elif node_type == "experiment":
            fn_name = config.get("fn", "")
            qubit = config.get("qubit", "{{qubit}}")
            qubit = resolve_template(qubit, node_results, workflow_ctx)
            params = resolve_template(config.get("params", {}), node_results, workflow_ctx)
            batch_config_str = config.get("batchConfig", None)

            # Custom plotting and analysis settings
            plot_command = config.get("plotCommand", "")
            analysis_prompt = config.get("analysisPrompt", "分析这个量子比特实验图像，描述你看到的波形特征和质量")
            auto_analyze = config.get("autoAnalyze", True)

            # LLM model settings for analysis
            model = config.get("model", "gpt-4o")
            provider = config.get("_modelProvider", "")
            base_url = config.get("_modelBaseUrl", "")
            temperature = float(config.get("temperature", 0.3))

            # Debug: check qubit availability
            print(f"WORKFLOW_DEBUG: qubit={qubit}, _s={type(_s).__name__ if _s else None}", file=sys.stderr, flush=True)

            # Convert qubit name to qubit object if needed
            qubit_obj = qubit
            if isinstance(qubit, str):
                # Try to get qubit object from _s registry
                if _s and qubit in _s:
                    qubit_obj = _s[qubit]
                    print(f"WORKFLOW_DEBUG: resolved qubit '{qubit}' to object {type(qubit_obj).__name__}", file=sys.stderr, flush=True)
                # Also try _backend
                elif _backend and hasattr(_backend, qubit):
                    qubit_obj = getattr(_backend, qubit)
                    print(f"WORKFLOW_DEBUG: resolved qubit '{qubit}' from _backend", file=sys.stderr, flush=True)
                else:
                    q_keys = [k for k in (_s.keys() if _s else []) if k.startswith('q')]
                    print(f"WORKFLOW_DEBUG: qubit '{qubit}' NOT found in _s! Available: {q_keys[:10]}...", file=sys.stderr, flush=True)

            # Always set _current_qubit in exec globals
            w_exec_globals["_current_qubit"] = qubit_obj

            # Build the experiment call code (for display)
            params_str = ", ".join(f"{k}={repr(v)}" for k, v in params.items())
            call_code = f"{fn_name}(_current_qubit, {params_str})"

            # Check for batch experiment from Decision node
            batch_experiments = None
            if batch_config_str:
                try:
                    batch_data = json.loads(batch_config_str)
                    if isinstance(batch_data, dict) and "recommendations" in batch_data:
                        batch_experiments = batch_data.get("recommendations", [])
                    elif isinstance(batch_data, list):
                        batch_experiments = batch_data
                    print(f"WORKFLOW_DEBUG: Batch experiments from Decision node: {len(batch_experiments or [])}", file=sys.stderr, flush=True)
                except json.JSONDecodeError as e:
                    print(f"WORKFLOW_DEBUG: Failed to parse batchConfig: {e}", file=sys.stderr, flush=True)

            def run_experiment_with_analysis(exp_fn, exp_qubit, exp_params, exp_idx, exp_reason=""):
                """Run a single experiment and optionally analyze the plot."""
                exp_result = {
                    "fn": exp_fn,
                    "qubit": exp_qubit,
                    "reason": exp_reason,
                    "params": exp_params,
                    "plotCommand": plot_command,
                    "analysisPrompt": analysis_prompt,
                }

                # Resolve qubit name to qubit object if needed
                exp_qubit_obj = exp_qubit
                if isinstance(exp_qubit, str):
                    if _s and exp_qubit in _s:
                        exp_qubit_obj = _s[exp_qubit]
                    elif _backend and hasattr(_backend, exp_qubit):
                        exp_qubit_obj = getattr(_backend, exp_qubit)
                    else:
                        q_keys = [k for k in (_s.keys() if _s else []) if k.startswith('q')]
                        print(f"WORKFLOW_DEBUG: batch qubit '{exp_qubit}' NOT found! Available: {q_keys[:5]}...", file=sys.stderr, flush=True)

                # Set the qubit object in exec globals for this experiment
                w_exec_globals["_current_qubit"] = exp_qubit_obj

                # Build call code for this experiment
                exp_params_str = ", ".join(f"{k}={repr(v)}" for k, v in exp_params.items())
                exp_call_code = f"{exp_fn}(_current_qubit, {exp_params_str})"
                exp_result["callCode"] = exp_call_code

                print(f"WORKFLOW_EXEC[{exp_idx}]: {exp_call_code}", file=sys.stderr, flush=True)

                # Capture output
                stdout_buf = StringIO()
                stderr_buf = StringIO()
                old_out, old_err = sys.stdout, sys.stderr
                exp_stdout = ""
                try:
                    sys.stdout = stdout_buf
                    sys.stderr = stderr_buf
                    exec(exp_call_code, w_exec_globals)
                    sys.stdout = old_out
                    sys.stderr = old_err
                    exp_stdout = stdout_buf.getvalue()
                except:
                    sys.stdout = old_out
                    sys.stderr = old_err
                    exp_stdout = stdout_buf.getvalue() + f"\nError: {traceback.format_exc()}"
                    print(f"WORKFLOW_ERROR: {traceback.format_exc()}", file=sys.stderr, flush=True)

                exp_result["stdout"] = exp_stdout

                # Parse metrics
                exp_result["metrics"] = parse_metrics(exp_stdout)

                # Get plot and run analysis
                import matplotlib.pyplot as plt
                fig = plt.gcf()
                exp_plot_path = None
                if fig and fig.get_size_inches().prod() > 0:
                    import matplotlib
                    matplotlib.use("Agg")
                    _plots_dir = os.environ.get("QMCLAW_PLOTS_DIR", PLOTS_DIR)
                    os.makedirs(_plots_dir, exist_ok=True)
                    _path = os.path.join(_plots_dir, f"wf_{node_id}_exp_{exp_idx}.png")
                    fig.savefig(_path, dpi=150, bbox_inches="tight")
                    exp_plot_path = _path
                    print(f"QMCLAW_PLOT:{_path}", file=sys.stderr, flush=True)
                    plt.close(fig)

                exp_result["plotPath"] = exp_plot_path

                # Run custom plotting command if specified
                if plot_command and exp_plot_path:
                    try:
                        # The plotting command should use the existing figure
                        # Re-open the saved plot for any additional plotting
                        img = plt.imread(exp_plot_path)
                        plt.figure()
                        plt.imshow(img)
                        exec(plot_command, {"plt": plt, "np": __import__("numpy"), "img": img})
                        # Save the modified plot
                        modified_path = _path.replace(".png", "_annotated.png")
                        plt.savefig(modified_path, dpi=150, bbox_inches="tight")
                        plt.close()
                        exp_result["modifiedPlotPath"] = modified_path
                        print(f"QMCLAW_MODIFIED_PLOT:{modified_path}", file=sys.stderr, flush=True)
                    except Exception as plot_err:
                        print(f"WORKFLOW_PLOT_ERROR: {plot_err}", file=sys.stderr, flush=True)
                        exp_result["plotError"] = str(plot_err)

                # Run LLM analysis if enabled
                if auto_analyze and exp_plot_path:
                    api_key = get_api_key_for_provider(provider)
                    if api_key:
                        try:
                            analysis_result = llm_analyze_image(
                                exp_plot_path,
                                analysis_prompt,
                                api_key,
                                model,
                                "",
                                provider,
                                base_url
                            )
                            exp_result["analysis"] = {
                                "prompt": analysis_prompt,
                                "result": analysis_result.get("analysis", "") if isinstance(analysis_result, dict) else "",
                                "model": model,
                                "provider": provider,
                                "messagesSent": analysis_result.get("_debug", {}).get("messages_sent", []) if isinstance(analysis_result, dict) else [],
                                "rawResponse": analysis_result,
                            }
                            print(f"WORKFLOW_ANALYSIS: {analysis_result.get('analysis', '')[:200] if isinstance(analysis_result, dict) else 'N/A'}", file=sys.stderr, flush=True)
                        except Exception as analysis_err:
                            print(f"WORKFLOW_ANALYSIS_ERROR: {analysis_err}", file=sys.stderr, flush=True)
                            exp_result["analysisError"] = str(analysis_err)

                return exp_result

            # Store results for each experiment
            all_results = []
            all_metrics = {}

            # Execute single or batch experiments
            if batch_experiments:
                # Batch execution: run experiments sequentially
                for i, exp_config in enumerate(batch_experiments):
                    exp_fn = exp_config.get("fn", fn_name)
                    exp_qubit = resolve_template(exp_config.get("qubit", qubit), node_results, workflow_ctx)
                    exp_params = resolve_template(exp_config.get("params", {}), node_results, workflow_ctx)
                    exp_reason = exp_config.get("reason", "")

                    exp_result = run_experiment_with_analysis(exp_fn, exp_qubit, exp_params, i, exp_reason)
                    all_results.append(exp_result)
                    all_metrics[f"exp_{i}"] = exp_result.get("metrics", {})

                # Use last experiment's plot
                plot_path = all_results[-1].get("plotPath") if all_results else None

                # Compile overall results
                result["type"] = "batch_experiment"
                result["batchResults"] = all_results
                result["status"] = "completed"
                result["callCode"] = f"# Batch of {len(batch_experiments)} experiments"
                result["stdout"] = "\n".join([f"[{r['fn']}] {r.get('reason', '')}\n{r['stdout']}" for r in all_results])
                result["plotPath"] = plot_path
                result["metrics"] = all_metrics
            else:
                # Single experiment (original logic)
                exp_result = run_experiment_with_analysis(fn_name, qubit, params, 0)
                all_results.append(exp_result)
                all_metrics["exp_0"] = exp_result.get("metrics", {})

                result["status"] = "completed"
                result["callCode"] = call_code
                result["plotCommand"] = plot_command
                result["analysisPrompt"] = analysis_prompt
                result["stdout"] = exp_result["stdout"]
                result["plotPath"] = exp_result.get("plotPath")
                result["modifiedPlotPath"] = exp_result.get("modifiedPlotPath")
                result["metrics"] = exp_result.get("metrics", {})
                result["analysis"] = exp_result.get("analysis")
                result["plotError"] = exp_result.get("plotError")
                result["analysisError"] = exp_result.get("analysisError")

        elif node_type == "notify":
            # Send notification via webhook
            channel = config.get("channel", "feishu")
            trigger = config.get("trigger", "always")
            template = config.get("template", "Workflow completed")

            result["type"] = "notify"

            # Check trigger condition
            should_send = False
            if trigger == "always":
                should_send = True
            elif trigger == "on-success":
                # Send only if no errors so far
                should_send = not any(
                    nr.get("result", {}).get("status") in ("error", "failed")
                    for nid, nr in node_results.items()
                    if not nid.startswith("_") and nid != node_id
                )
            elif trigger == "on-fail":
                should_send = any(
                    nr.get("result", {}).get("status") in ("error", "failed")
                    for nid, nr in node_results.items()
                    if not nid.startswith("_") and nid != node_id
                )

            if not should_send:
                result["status"] = "skipped"
                result["stdout"] = f"Skipped: trigger={trigger} condition not met"
            else:
                if channel == "feishu":
                    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")
                    if not webhook_url:
                        result["status"] = "skipped"
                        result["stdout"] = "FEISHU_WEBHOOK_URL not configured"
                        result["error"] = "Environment variable FEISHU_WEBHOOK_URL not set"
                    else:
                        workflow_ctx_copy = dict(workflow_ctx)
                        workflow_ctx_copy["workflow_name"] = workflow_ctx_copy.get(
                            "workflow_name", "QmClaw Workflow"
                        )
                        notify_result = send_feishu_notification(
                            template, workflow_ctx_copy, node_results, webhook_url
                        )
                        if notify_result["success"]:
                            result["status"] = "completed"
                            result["stdout"] = f"Notification sent via {channel}"
                            result["metrics"] = {"channel": channel, "trigger": trigger}
                        else:
                            result["status"] = "failed"
                            result["error"] = notify_result["error"]
                            result["stdout"] = f"Failed to send notification: {notify_result['error']}"
                else:
                    result["status"] = "failed"
                    result["error"] = f"Unsupported channel: {channel}"

        elif node_type == "analyze":
            # Analyze node: extract metrics from previous experiment node OR historical DataVault data
            source = config.get("source", "realtime")  # "realtime" | "historical"
            ref = config.get("ref", "")

            if source == "historical":
                # Historical analysis: query DataVault for past experiments
                qubit = config.get("qubit", "{{qubit}}")
                qubit = resolve_template(qubit, node_results, workflow_ctx)
                experiment_type = config.get("experimentType", "")  # e.g., "iqraw", "s21", "piamp"
                time_range = config.get("timeRange", "count")  # "count" | "days"
                time_value = int(config.get("timeValue", 10))  # N experiments or N days
                analysis_commands = config.get("analysisCommands", {})  # { "iqraw": "code", ... }

                print(f"WORKFLOW_ANALYZE: Historical analysis for qubit={qubit}, type={experiment_type}", file=sys.stderr, flush=True)

                # Query historical data from DataVault
                historical_data = _query_historical_data(
                    qubit=qubit,
                    experiment_type=experiment_type,
                    time_range=time_range,
                    time_value=time_value,
                    _data=_data
                )

                all_metrics = {}
                analysis_results = []
                stats = {}
                trends = {}

                for entry in historical_data:
                    dataset_idx = entry.get("dataset_idx")
                    exp_type = entry.get("experiment_type", experiment_type)
                    timestamp = entry.get("timestamp", "")

                    # Load this dataset for analysis
                    try:
                        if dataset_idx is not None:
                            _data.loadDataset(int(dataset_idx))
                    except Exception as load_err:
                        print(f"WORKFLOW_ANALYZE: Failed to load dataset {dataset_idx}: {load_err}", file=sys.stderr, flush=True)
                        continue

                    # Get analysis command for this experiment type
                    analysis_cmd = analysis_commands.get(exp_type, "")

                    # Get metrics from data object
                    exp_metrics = {}
                    try:
                        if hasattr(_data, 'parameters'):
                            params = _data.parameters
                            # Try to extract common metrics
                            for key in ['SNR', 'F0', 'F1', 'separation', 'T1', 'T2', 'pi_amp', 'gate_fidelity']:
                                if key in params:
                                    exp_metrics[key] = float(params[key])
                    except Exception as e:
                        print(f"WORKFLOW_ANALYZE: Failed to extract metrics: {e}", file=sys.stderr, flush=True)

                    # Execute custom analysis if provided
                    if analysis_cmd:
                        stdout_buf = StringIO()
                        stderr_buf = StringIO()
                        old_out, old_err = sys.stdout, sys.stderr
                        try:
                            sys.stdout = stdout_buf
                            sys.stderr = stderr_buf
                            analysis_globals = {
                                "__name__": "__analysis__",
                                "data": _data,
                                "qter": _qter,
                                "dp": _data,
                                "plt": plt,
                            }
                            exec(analysis_cmd, analysis_globals)
                            sys.stdout = old_out
                            sys.stderr = old_err
                        except Exception as exec_err:
                            sys.stdout = old_out
                            sys.stderr = old_err
                            print(f"WORKFLOW_ANALYZE: Failed to execute analysis for {exp_type}: {exec_err}", file=sys.stderr, flush=True)

                        analysis_output = stdout_buf.getvalue()
                        parsed_metrics = parse_metrics(analysis_output)
                        exp_metrics.update(parsed_metrics)

                    # Store metrics with timestamp for trend analysis
                    for k, v in exp_metrics.items():
                        key = f"{exp_type}_{k}" if exp_type else k
                        if key not in trends:
                            trends[key] = []
                        trends[key].append({"timestamp": timestamp, "value": v})
                        all_metrics[f"{key}_latest"] = v

                    analysis_results.append({
                        "datasetIdx": dataset_idx,
                        "experimentType": exp_type,
                        "timestamp": timestamp,
                        "metrics": exp_metrics
                    })

                # Calculate statistics for each metric
                for key, values in trends.items():
                    if not values:
                        continue
                    numeric_values = [v["value"] for v in values if isinstance(v["value"], (int, float))]
                    if numeric_values:
                        import numpy as np
                        arr = np.array(numeric_values)
                        stats[key] = {
                            "mean": float(np.mean(arr)),
                            "std": float(np.std(arr)),
                            "min": float(np.min(arr)),
                            "max": float(np.max(arr)),
                            "count": len(numeric_values),
                            "latest": float(numeric_values[-1]) if numeric_values else None,
                            "first": float(numeric_values[0]) if numeric_values else None,
                            # Trend: positive = increasing, negative = decreasing
                            "trend": float(numeric_values[-1] - numeric_values[0]) / abs(numeric_values[0]) if numeric_values[0] != 0 else 0,
                        }

                # Generate summary
                summary_parts = []
                for key, stat in stats.items():
                    if stat["count"] > 1:
                        trend_str = "上升" if stat["trend"] > 0.05 else "下降" if stat["trend"] < -0.05 else "稳定"
                        summary_parts.append(f"{key}: {trend_str} (最新={stat['latest']:.3f}, 均值={stat['mean']:.3f}, N={stat['count']})")

                summary = " | ".join(summary_parts) if summary_parts else "无有效历史数据"
                if not summary_parts:
                    summary = f"分析了 {len(analysis_results)} 条历史记录，但未提取到有效指标"

                result["status"] = "completed"
                result["stdout"] = json.dumps({
                    "stats": stats,
                    "trends": trends,
                    "summary": summary,
                    "analysisResults": analysis_results
                }, ensure_ascii=False, indent=2)
                result["metrics"] = all_metrics
                result["analysisResults"] = analysis_results
                result["stats"] = stats
                result["summary"] = summary

            else:
                # Realtime analysis (original behavior): analyze previous experiment node
                experiments_to_analyze = config.get("experimentsToAnalyze", [])  # Empty = all

                # Load experiment configs for analysis commands
                exp_configs = _load_experiment_configs()

                ref_result = node_results.get(ref, {}).get("result", {})

                # Get batch results or single experiment result
                batch_results = ref_result.get("batchResults", [])
                if not batch_results:
                    # Single experiment result
                    fn = ref_result.get("callCode", "")
                    exp_fn = fn.split("(")[0] if "(" in fn else fn
                    batch_results = [{
                        "fn": exp_fn,
                        "metrics": ref_result.get("metrics", {})
                    }]

                all_metrics = {}
                analysis_results = []

                for i, exp in enumerate(batch_results):
                    fn = exp.get("fn", "")
                    exp_key = fn.split(".")[-1] if "." in fn else fn

                    # Check if this experiment should be analyzed
                    if experiments_to_analyze and exp_key not in experiments_to_analyze:
                        continue

                    # Get analysis command from config
                    exp_config = exp_configs.get("experiments", {}).get(exp_key, {})
                    analysis_cmd = config.get("analysisConfig", {}).get(exp_key) or exp_config.get("defaultAnalysisCommand", "")

                    if not analysis_cmd:
                        # No analysis command, just use existing metrics
                        exp_metrics = exp.get("metrics", {})
                        for k, v in exp_metrics.items():
                            all_metrics[f"{exp_key}_{k}"] = v
                        analysis_results.append({
                            "expIndex": i,
                            "fn": fn,
                            "analysisOutput": "",
                            "metrics": exp_metrics
                        })
                        continue

                    # Load latest dataset
                    try:
                        _data.loadDataset(-1)
                    except Exception as load_err:
                        print(f"WORKFLOW_ANALYZE: Failed to load dataset: {load_err}", file=sys.stderr, flush=True)

                    # Execute analysis command
                    stdout_buf = StringIO()
                    stderr_buf = StringIO()
                    old_out, old_err = sys.stdout, sys.stderr
                    try:
                        sys.stdout = stdout_buf
                        sys.stderr = stderr_buf
                        # Build exec globals with available modules
                        analysis_globals = {
                            "__name__": "__analysis__",
                            "data": _data,
                            "qter": _qter,
                            "dp": _data,  # Alias for data_process
                            "plt": plt,
                        }
                        exec(analysis_cmd, analysis_globals)
                        sys.stdout = old_out
                        sys.stderr = old_err
                    except Exception as exec_err:
                        sys.stdout = old_out
                        sys.stderr = old_err
                        print(f"WORKFLOW_ANALYZE: Failed to execute analysis for {exp_key}: {exec_err}", file=sys.stderr, flush=True)

                    analysis_output = stdout_buf.getvalue()

                    # Parse metrics from output
                    metrics = parse_metrics(analysis_output)

                    # Also check metricsToExtract from config
                    metrics_to_extract = exp_config.get("metricsToExtract", [])
                    if metrics_to_extract:
                        # Filter parsed metrics to only include specified ones
                        filtered_metrics = {}
                        for k in metrics_to_extract:
                            if k in metrics:
                                filtered_metrics[k] = metrics[k]
                            else:
                                # Try to find metric with different casing
                                for pk, pv in metrics.items():
                                    if pk.upper() == k.upper():
                                        filtered_metrics[k] = pv
                                        break
                        metrics = filtered_metrics

                    # Add prefix to metrics to avoid collisions
                    for k, v in metrics.items():
                        all_metrics[f"{exp_key}_{k}"] = v

                    analysis_results.append({
                        "expIndex": i,
                        "fn": fn,
                        "analysisOutput": analysis_output,
                        "metrics": metrics
                    })

                if all_metrics or analysis_results:
                    result["status"] = "completed"
                    result["stdout"] = json.dumps(all_metrics, indent=2)
                    result["metrics"] = all_metrics
                    result["analysisResults"] = analysis_results
                else:
                    result["status"] = "failed"
                    result["error"] = f"No metrics found in node '{ref}' or no analysis commands configured"

            ref_result = node_results.get(ref, {}).get("result", {})

            # Get batch results or single experiment result
            batch_results = ref_result.get("batchResults", [])
            if not batch_results:
                # Single experiment result
                fn = ref_result.get("callCode", "")
                exp_fn = fn.split("(")[0] if "(" in fn else fn
                batch_results = [{
                    "fn": exp_fn,
                    "metrics": ref_result.get("metrics", {})
                }]

            all_metrics = {}
            analysis_results = []

            for i, exp in enumerate(batch_results):
                fn = exp.get("fn", "")
                exp_key = fn.split(".")[-1] if "." in fn else fn

                # Check if this experiment should be analyzed
                if experiments_to_analyze and exp_key not in experiments_to_analyze:
                    continue

                # Get analysis command from config
                exp_config = exp_configs.get("experiments", {}).get(exp_key, {})
                analysis_cmd = config.get("analysisConfig", {}).get(exp_key) or exp_config.get("defaultAnalysisCommand", "")

                if not analysis_cmd:
                    # No analysis command, just use existing metrics
                    exp_metrics = exp.get("metrics", {})
                    for k, v in exp_metrics.items():
                        all_metrics[f"{exp_key}_{k}"] = v
                    analysis_results.append({
                        "expIndex": i,
                        "fn": fn,
                        "analysisOutput": "",
                        "metrics": exp_metrics
                    })
                    continue

                # Load latest dataset
                try:
                    _data.loadDataset(-1)
                except Exception as load_err:
                    print(f"WORKFLOW_ANALYZE: Failed to load dataset: {load_err}", file=sys.stderr)

                # Execute analysis command
                stdout_buf = StringIO()
                stderr_buf = StringIO()
                old_out, old_err = sys.stdout, sys.stderr
                try:
                    sys.stdout = stdout_buf
                    sys.stderr = stderr_buf
                    # Build exec globals with available modules
                    analysis_globals = {
                        "__name__": "__analysis__",
                        "data": _data,
                        "qter": _qter,
                        "dp": _data,  # Alias for data_process
                        "plt": plt,
                    }
                    exec(analysis_cmd, analysis_globals)
                    sys.stdout = old_out
                    sys.stderr = old_err
                except Exception as exec_err:
                    sys.stdout = old_out
                    sys.stderr = old_err
                    print(f"WORKFLOW_ANALYZE: Failed to execute analysis for {exp_key}: {exec_err}", file=sys.stderr)

                analysis_output = stdout_buf.getvalue()

                # Parse metrics from output
                metrics = parse_metrics(analysis_output)

                # Also check metricsToExtract from config
                metrics_to_extract = exp_config.get("metricsToExtract", [])
                if metrics_to_extract:
                    # Filter parsed metrics to only include specified ones
                    filtered_metrics = {}
                    for k in metrics_to_extract:
                        if k in metrics:
                            filtered_metrics[k] = metrics[k]
                        else:
                            # Try to find metric with different casing
                            for pk, pv in metrics.items():
                                if pk.upper() == k.upper():
                                    filtered_metrics[k] = pv
                                    break
                    metrics = filtered_metrics

                # Add prefix to metrics to avoid collisions
                for k, v in metrics.items():
                    all_metrics[f"{exp_key}_{k}"] = v

                analysis_results.append({
                    "expIndex": i,
                    "fn": fn,
                    "analysisOutput": analysis_output,
                    "metrics": metrics
                })

            if all_metrics or analysis_results:
                result["status"] = "completed"
                result["stdout"] = json.dumps(all_metrics, indent=2)
                result["metrics"] = all_metrics
                result["analysisResults"] = analysis_results
            else:
                result["status"] = "failed"
                result["error"] = f"No metrics found in node '{ref}' or no analysis commands configured"

        elif node_type == "code":
            # Code execution node: sandboxed Python execution
            code = config.get("code", "")
            timeout = int(config.get("timeout", 30))
            return_var = config.get("returnVariable", "result")

            if not code:
                result["status"] = "failed"
                result["error"] = "No code provided"
                return result

            # Parse workflow variables for use in code
            variables = {}
            for key, value in workflow_ctx.items():
                if key.startswith("nodes."):
                    parts = key.split(".")
                    if len(parts) >= 3:
                        var_name = parts[-1]
                        variables[var_name] = value
                elif key not in ("workflow_name",):
                    variables[key] = value

            # Also parse from node_results
            for nid, nres in node_results.items():
                if nid.startswith("_") or not isinstance(nres, dict):
                    continue  # Skip internal keys like _workflow_nodes
                r = nres.get("result", {})
                if r.get("metrics"):
                    for k, v in r["metrics"].items():
                        variables[f"{nid}_{k}"] = v

            # Build safe globals with allowed modules
            try:
                import numpy as np
                import datetime
                allowed_modules = {
                    "json": json,
                    "re": __import__("re"),
                    "math": __import__("math"),
                    "np": np,
                    "numpy": np,
                    "datetime": datetime,
                    "time": __import__("time"),
                    "random": __import__("random"),
                    "collections": __import__("collections"),
                    "itertools": __import__("itertools"),
                    "functools": __import__("funtools") if "functools" not in dir() else __import__("functools"),
                }
            except ImportError as e:
                print(f"WORKFLOW_CODE: Failed to import modules: {e}", file=sys.stderr)
                allowed_modules = {"json": json, "re": __import__("re")}

            # Build restricted builtins
            safe_builtins = {
                "len": len, "range": range, "str": str, "int": int, "float": float,
                "bool": bool, "list": list, "dict": dict, "tuple": tuple, "set": set,
                "print": print, "enumerate": enumerate, "zip": zip, "map": map,
                "filter": filter, "sorted": sorted, "sum": sum, "min": min, "max": max,
                "abs": abs, "round": round, "isinstance": isinstance,
                "hasattr": hasattr, "getattr": getattr, "setattr": setattr,
                "type": type, "ord": ord, "chr": chr, "hex": hex, "bin": bin,
                "open": None,  # Disabled for security
            }

            exec_globals = {
                "__builtins__": safe_builtins,
                return_var: None,
                **allowed_modules,
                **variables,
            }

            # Execute code with timeout
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            old_out, old_err = sys.stdout, sys.stderr

            try:
                sys.stdout = stdout_buf
                sys.stderr = stderr_buf

                # Use threading for timeout (signal doesn't work on Windows in all cases)
                import threading
                import queue

                result_queue = queue.Queue()
                error_queue = queue.Queue()

                def run_code():
                    try:
                        exec(code, exec_globals)
                        result_queue.put(("success", exec_globals.get(return_var)))
                    except Exception as e:
                        error_queue.put(e)

                thread = threading.Thread(target=run_code)
                thread.daemon = True
                thread.start()
                thread.join(timeout=timeout)

                if thread.is_alive():
                    # Timeout
                    result["status"] = "error"
                    result["stdout"] = stdout_buf.getvalue()
                    result["stderr"] = f"Timeout after {timeout} seconds"
                    result["error"] = f"Code execution timed out after {timeout}s"
                elif not error_queue.empty():
                    # Error occurred
                    err = error_queue.get()
                    result["status"] = "error"
                    result["stdout"] = stdout_buf.getvalue()
                    result["stderr"] = stderr_buf.getvalue()
                    result["error"] = str(err)
                else:
                    # Success
                    sys.stdout = old_out
                    sys.stderr = old_err
                    result_value = result_queue.get_nowait() if not result_queue.empty() else exec_globals.get(return_var)
                    result["status"] = "completed"
                    result["stdout"] = stdout_buf.getvalue()
                    result["stderr"] = stderr_buf.getvalue()
                    result["result"] = _serialize_for_json(result_value)
                    result["resultType"] = type(result_value).__name__ if result_value is not None else "None"
            except Exception as e:
                result["status"] = "error"
                result["stdout"] = stdout_buf.getvalue()
                result["stderr"] = stderr_buf.getvalue()
                result["error"] = str(e)
            finally:
                sys.stdout = old_out
                sys.stderr = old_err

        elif node_type == "context":
            # Context node stores variables and rules for other nodes to use
            variables = config.get("variables", {})
            rules = config.get("rules", "[]")

            # Store context in node_results so other nodes can access it
            result["status"] = "completed"
            result["stdout"] = f"Context loaded: {len(variables)} variables"
            result["config"] = {"variables": variables, "rules": rules}
            result["metrics"] = {"variablesCount": len(variables)}

        elif node_type == "print":
            msg = resolve_template(config.get("message", ""), node_results, workflow_ctx)
            print(f"WORKFLOW_PRINT: {msg}", file=sys.stderr, flush=True)
            result["status"] = "completed"
            result["stdout"] = msg

        else:
            result["status"] = "failed"
            result["error"] = f"Unknown node type: {node_type}"

    except Exception as e:
        result["status"] = "error"
        # Handle RayTaskError specially - extract the cause
        error_str = str(e)
        if "RayTaskError" in type(e).__name__ or "RayTaskError" in error_str:
            # Try to extract the actual cause
            import re
            cause_match = re.search(r"RayTaskError[^:]*:\s*(.+?)(?:\n|$)", error_str)
            if cause_match:
                error_str = cause_match.group(1).strip()
            # Also check for cause in nested exceptions
            if hasattr(e, 'cause') and e.cause:
                error_str = f"{type(e.cause).__name__}: {e.cause}"
        result["error"] = f"{type(e).__name__}: {error_str}"
        result["stderr"] = traceback.format_exc()
        # Ensure error is printed to stderr for debugging
        print(f"WORKFLOW_ERROR: {type(e).__name__}: {error_str}", file=sys.stderr, flush=True)
        print(traceback.format_exc(), file=sys.stderr, flush=True)

    # Add input information to result
    result["input"] = node_input

    # Add duration
    duration_ms = int((time.time() - start_time) * 1000)
    result["duration"] = duration_ms

    return result


def send_feishu_notification(template: str, workflow_ctx: dict, node_results: dict, webhook_url: str) -> dict:
    """Send notification via Feishu webhook.

    The template supports {{nodes.n1.metric}} variable substitution.
    """
    try:
        import urllib.request
        import urllib.error

        # Resolve template variables
        message_text = resolve_template(template, node_results, workflow_ctx)
        workflow_name = workflow_ctx.get("workflow_name", "QmClaw Workflow")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

        # Build Feishu card message
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"🔔 {workflow_name}"
                    },
                    "template": "blue"
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": f"**Time**: {timestamp}\n**Message**: {message_text}"
                        }
                    },
                    {
                        "tag": "hr"
                    },
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "**Metrics Summary**"
                        }
                    },
                    {
                        "tag": "div",
                        "fields": []
                    }
                ]
            }
        }

        # Add metrics to card (limit to first 5)
        fields = []
        for nid, nr in list(node_results.items())[:5]:
            if nid.startswith("_"): continue
            metrics = nr.get("result", {}).get("metrics", {})
            if metrics:
                metric_text = " | ".join(
                    f"{k}: {v:.3f}" if isinstance(v, float) else f"{k}: {v}"
                    for k, v in list(metrics.items())[:3]
                )
                fields.append({
                    "is_short": True,
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{nid}**\n{metric_text}"
                    }
                })
        if fields:
            card["card"]["elements"][-1]["fields"] = fields

        # Send webhook
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(card).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as resp:
            response_data = resp.read().decode("utf-8")
            print(f"FEISHU_NOTIFY: {response_data}", file=sys.stderr, flush=True)
            return {"success": True, "response": response_data}

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        print(f"FEISHU_ERROR: HTTP {e.code} - {error_body}", file=sys.stderr, flush=True)
        return {"success": False, "error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        print(f"FEISHU_ERROR: {e}", file=sys.stderr, flush=True)
        return {"success": False, "error": str(e)}


def get_api_key_for_provider(provider: str) -> str:
    """Get API key for a provider from environment variables."""
    provider_keys = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    env_var = provider_keys.get(provider, "OPENAI_API_KEY")
    return os.environ.get(env_var, "")


def infer_provider_from_model(model: str) -> str:
    """Infer the provider from the model name if not explicitly set."""
    if not model:
        return ""
    model_lower = model.lower()
    if "minimax" in model_lower or "m2." in model_lower or "m2_7" in model_lower:
        return "minimax"
    elif "deepseek" in model_lower:
        return "deepseek"
    elif "claude" in model_lower or "anthropic" in model_lower:
        return "anthropic"
    elif "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower or "o4" in model_lower:
        return "openai"
    return ""


def get_openai_client(api_key: str, provider: str = "", base_url: str = ""):
    """Create an OpenAI-compatible client based on provider."""
    import openai

    if base_url:
        # Custom base URL provided
        return openai.OpenAI(api_key=api_key, base_url=base_url)

    if provider == "minimax":
        # MiniMax requires special header format, use httpx directly
        return None  # Will be handled specially
    elif provider == "deepseek":
        return openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
    elif provider == "anthropic":
        # Anthropic uses OpenAI SDK with their base URL
        return openai.OpenAI(api_key=api_key, base_url="https://anthropic.ai/v1")
    else:
        # Default OpenAI
        return openai.OpenAI(api_key=api_key)


def _get_minimax_key() -> str:
    """Get MiniMax API key - defined at module level to avoid local variable issues."""
    return os.environ.get("MINIMAX_API_KEY", "")


def call_minimax_api(messages: list, model: str, api_key: str, temperature: float = 0.3, max_tokens: int = 500) -> dict:
    """Call MiniMax API using Anthropic-compatible endpoint.

    MiniMax provides an Anthropic-compatible API at https://api.minimaxi.com/anthropic
    See: https://platform.minimaxi.com/document/Guides/Authentication/anthropic-compatible-api
    """
    import httpx

    # Debug: Log the model and api_key (masked)
    print(f"[MiniMax Debug] model={model}", file=sys.stderr, flush=True)
    print(f"[MiniMax Debug] api_key provided: {bool(api_key)}, prefix={api_key[:15] if api_key else 'EMPTY'}...", file=sys.stderr, flush=True)

    # MiniMax Anthropic-compatible API endpoint
    # Model names: MiniMax-Text-01, MiniMax-M2.7, etc.
    minimax_api_base = "https://api.minimaxi.com/anthropic"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "x-api-key": api_key,
    }

    # Debug: Print headers (masked)
    masked_headers = {k: (v[:20]+"..." if k == "Authorization" and len(v) > 20 else v) for k, v in headers.items()}
    print(f"[MiniMax Debug] headers={masked_headers}", file=sys.stderr, flush=True)

    # Convert OpenAI-style messages to Anthropic format
    anthropic_messages = []
    system_content = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_content = _sanitize_string(msg.get("content", ""))
        else:
            anthropic_messages.append({
                "role": msg.get("role", "user"),
                "content": _sanitize_string(msg.get("content", ""))
            })

    payload = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": max_tokens,
    }
    if temperature != 0.3:  # Only include if non-default
        payload["temperature"] = temperature
    if system_content:
        payload["system"] = system_content

    try:
        with httpx.Client(timeout=60.0) as client:
            print(f"[MiniMax Debug] Sending POST to {minimax_api_base}/v1/messages", file=sys.stderr, flush=True)
            response = client.post(
                f"{minimax_api_base}/v1/messages",
                headers=headers,
                json=payload,
            )
            print(f"[MiniMax Debug] Response status={response.status_code}", file=sys.stderr, flush=True)
            if response.status_code != 200:
                print(f"[MiniMax Debug] Response body={_sanitize_string(response.text[:500])}", file=sys.stderr, flush=True)
            response.raise_for_status()
            result = response.json()

            # Convert Anthropic response to OpenAI-style format for compatibility
            # MiniMax may return multiple content blocks (thinking, text, etc.)
            content = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    content = _sanitize_string(block.get("text", ""))
                    break
            # Safely handle usage which might be a list or dict
            usage_data = result.get("usage", {})
            if isinstance(usage_data, dict):
                usage = {
                    "prompt_tokens": usage_data.get("input_tokens", 0),
                    "completion_tokens": usage_data.get("output_tokens", 0),
                    "total_tokens": usage_data.get("input_tokens", 0) + usage_data.get("output_tokens", 0),
                }
            else:
                usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                print(f"[MiniMax Debug] usage is not a dict: {type(usage_data)}", file=sys.stderr)

            return {"choices": [{"message": {"content": content}}], "usage": usage}
    except httpx.HTTPStatusError as e:
        error_body = _sanitize_string(e.response.text[:500])
        print(f"[MiniMax Debug] HTTP error: {e.response.status_code}, body={error_body}", file=sys.stderr, flush=True)
        raise Exception(f"MiniMax API error: {e.response.status_code} - {error_body}")
    except Exception as e:
        error_str = _sanitize_string(str(e))
        print(f"[MiniMax Debug] Exception: {error_str}", file=sys.stderr, flush=True)
        raise Exception(f"MiniMax API error: {error_str}")


def llm_decide(prompt: str, context: str, api_key: str, model: str = "gpt-4o", temperature: float = 0.3, max_tokens: int = 500, system_prompt: str = "", provider: str = "", base_url: str = "") -> dict:
    """Call LLM to make a decision.

    Args:
        prompt: The user's question/prompt
        context: Additional context from previous nodes
        api_key: API key (OpenAI or MiniMax)
        model: Model to use (default: gpt-4o)
        temperature: Sampling temperature (default: 0.3)
        max_tokens: Maximum tokens in response (default: 500)
        system_prompt: Optional custom system prompt
        provider: Model provider (openai, minimax, deepseek, anthropic)
        base_url: Custom API base URL (optional)
    """
    try:
        # Default system prompt
        if not system_prompt:
            system_prompt = """You are a quantum experiment assistant. Based on the experiment results, decide whether to proceed with the next step.

Your task is to analyze the experiment data and make a decision. Respond with ONLY a JSON object:
{"decision": "proceed" or "stop" or "retry", "reasoning": "brief explanation of why"}

Guidelines:
- "proceed": The experiment succeeded and parameters look good
- "stop": There are serious problems that cannot be fixed automatically
- "retry": Minor issues that might be resolved by adjusting parameters"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {prompt}"}
        ]

        # Use MiniMax API directly if provider is minimax
        if provider == "minimax":
            result = call_minimax_api(messages, model, api_key, temperature, max_tokens)
            raw_response = result
            # Safely extract content from result
            if isinstance(result, dict):
                choices = result.get("choices", [])
                if isinstance(choices, list) and len(choices) > 0:
                    first_choice = choices[0]
                    if isinstance(first_choice, dict):
                        msg = first_choice.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else ""
                    else:
                        content = ""
                else:
                    content = ""
            else:
                content = ""
                print(f"LLM_DECIDE: result is not a dict: {type(result)}", file=sys.stderr)
        else:
            # Create the appropriate client for other providers
            client = get_openai_client(api_key, provider, base_url)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            raw_response = response.model_dump() if hasattr(response, 'model_dump') else {"raw_response": str(response)}
            content = response.choices[0].message.content

        # Try to parse JSON
        import re
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
            except (json.JSONDecodeError, ValueError) as e:
                print(f"LLM_DECIDE: JSON parse error: {e}, content={content[:200]}", file=sys.stderr)
                parsed = None
        else:
            parsed = None

        # Ensure parsed is a dict
        if not isinstance(parsed, dict):
            # If parsed is a list, wrap it
            if isinstance(parsed, list):
                parsed = {"decision": "proceed", "reasoning": f"Received list from LLM: {parsed}"}
            else:
                parsed = {"decision": "proceed", "reasoning": content if isinstance(content, str) else str(content)}

        # Include full conversation for debugging
        parsed["_debug"] = {
            "messages_sent": messages,
            "raw_response": raw_response,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        return parsed
    except Exception as e:
        return {"decision": "error", "reasoning": f"LLM error: {e}", "_debug": {"messages_sent": messages if 'messages' in dir() else [], "error": str(e)}}


def smart_decision(system_prompt: str, user_message: str, api_key: str, model: str = "gpt-4o", temperature: float = 0.3, max_tokens: int = 1000, provider: str = "", base_url: str = "") -> dict:
    """Call LLM for smart decision with structured output.

    Args:
        system_prompt: System prompt defining the task
        user_message: User's message with metrics/context
        api_key: API key
        model: Model to use
        temperature: Sampling temperature
        max_tokens: Maximum tokens in response
        provider: Model provider (openai, minimax, deepseek, anthropic)
        base_url: Custom API base URL
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]

    try:
        if provider == "minimax":
            result = call_minimax_api(messages, model, api_key, temperature, max_tokens)
            raw_response = result
            # Safely extract content from result
            if isinstance(result, dict):
                choices = result.get("choices", [])
                if isinstance(choices, list) and len(choices) > 0:
                    first_choice = choices[0]
                    if isinstance(first_choice, dict):
                        msg = first_choice.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else ""
                    else:
                        content = ""
                else:
                    content = ""
            else:
                content = ""
                print(f"SMART_DECISION: result is not a dict: {type(result)}", file=sys.stderr)
        else:
            client = get_openai_client(api_key, provider, base_url)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            raw_response = response.model_dump() if hasattr(response, 'model_dump') else {"raw_response": str(response)}
            content = response.choices[0].message.content

        # Try to parse JSON from response
        import re
        json_match = re.search(r"\{[\s\S]*\}", content)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
            except (json.JSONDecodeError, ValueError) as e:
                print(f"SMART_DECISION: JSON parse error: {e}, content={content[:200]}", file=sys.stderr)
                parsed = None
        else:
            parsed = None

        # Handle parsed result - ensure it's a dict
        if not isinstance(parsed, dict):
            # If parsed is a list (e.g., LLM returned bare array), wrap it
            if isinstance(parsed, list):
                parsed = {"recommendations": parsed, "symptom": "", "reasoning": content, "matchedRules": []}
            else:
                # Fallback: return raw content
                parsed = {
                    "symptom": "无法解析 LLM 返回",
                    "recommendations": [],
                    "reasoning": content if isinstance(content, str) else str(content),
                    "matchedRules": []
                }

        # Ensure required fields exist
        if "recommendations" not in parsed:
            parsed["recommendations"] = []
        if "reasoning" not in parsed:
            parsed["reasoning"] = ""
        if "symptom" not in parsed:
            parsed["symptom"] = ""

        # Include debug info
        parsed["_debug"] = {
            "messages_sent": messages,
            "raw_response": raw_response,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        return parsed
    except Exception as e:
        return {
            "symptom": "",
            "recommendations": [],
            "reasoning": f"LLM error: {e}",
            "matchedRules": [],
            "_debug": {"messages_sent": messages if 'messages' in dir() else [], "error": str(e)}
        }


def llm_analyze_image(image_path: str, prompt: str, api_key: str, model: str = "gpt-4o", system_prompt: str = "", provider: str = "", base_url: str = "") -> dict:
    """Call LLM to analyze an experiment plot image.

    Args:
        image_path: Path to the image file
        prompt: Analysis prompt
        api_key: API key (OpenAI or MiniMax)
        model: Model to use (default: gpt-4o)
        system_prompt: Optional system prompt
        provider: Model provider (openai, minimax, deepseek, anthropic)
        base_url: Custom API base URL (optional)
    """
    messages = []
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        import base64
        b64 = base64.b64encode(image_data).decode()

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        ]})

        # Use MiniMax API directly if provider is minimax
        if provider == "minimax":
            result = call_minimax_api(messages, model, api_key, temperature=0.3, max_tokens=800)
            raw_response = result
            # Safely extract content from result
            if isinstance(result, dict):
                choices = result.get("choices", [])
                if isinstance(choices, list) and len(choices) > 0:
                    first_choice = choices[0]
                    if isinstance(first_choice, dict):
                        msg = first_choice.get("message", {})
                        content = msg.get("content", "") if isinstance(msg, dict) else ""
                    else:
                        content = ""
                else:
                    content = ""
            else:
                content = ""
                print(f"LLM_ANALYZE_IMAGE: result is not a dict: {type(result)}", file=sys.stderr)
        else:
            # Create the appropriate client for other providers
            client = get_openai_client(api_key, provider, base_url)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=800,
            )
            content = response.choices[0].message.content
            raw_response = response.model_dump() if hasattr(response, 'model_dump') else {"raw_response": str(response)}

        return {
            "analysis": content,
            "model": model,
            "_debug": {
                "messages_sent": messages,
                "raw_response": raw_response,
            }
        }
    except Exception as e:
        return {"error": str(e), "_debug": {"messages_sent": messages, "error": str(e)}}


def parse_metrics(stdout: str) -> dict:
    """Parse metrics from experiment stdout."""
    metrics = {}
    import re
    # readout fidelity
    m = re.search(r"readout[_\s]fidelity[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["readout_fidelity"] = float(m.group(1))
    # T1
    m = re.search(r"T1[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["T1"] = float(m.group(1))
    # F0/F1 fidelity
    m = re.search(r"F0[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["F0"] = float(m.group(1))
    m = re.search(r"F1[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["F1"] = float(m.group(1))
    # SNR
    m = re.search(r"SNR[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["SNR"] = float(m.group(1))
    # separation
    m = re.search(r"Separation[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["separation"] = float(m.group(1))
    # T2
    m = re.search(r"T2\*?[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["T2"] = float(m.group(1))
    # detuning
    m = re.search(r"detuning[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["detuning"] = float(m.group(1))
    # pi_amplitude
    m = re.search(r"pi[_\s]?amplitude[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["pi_amplitude"] = float(m.group(1))
    # gate_fidelity
    m = re.search(r"gate[_\s]?fidelity[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["gate_fidelity"] = float(m.group(1))
    # dispersive_shift
    m = re.search(r"dispersive[_\s]?shift[:\s=]+([0-9.]+)", stdout, re.IGNORECASE)
    if m: metrics["dispersive_shift"] = float(m.group(1))
    return metrics


def _query_historical_data(qubit: str, experiment_type: str = "", time_range: str = "count", time_value: int = 10, _data=None) -> list:
    """Query historical experiment data from DataVault.

    Args:
        qubit: Qubit name to query
        experiment_type: Type of experiment (e.g., "iqraw", "s21") - empty for all
        time_range: "count" (last N experiments) or "days" (last N days)
        time_value: Number of experiments or days
        _data: DataLab instance for accessing DataVault

    Returns:
        List of historical entries, each with dataset_idx, experiment_type, timestamp
    """
    results = []

    # Try to query DataVault
    if _data is None:
        print("WORKFLOW_ANALYZE: No DataLab instance available, returning mock data", file=sys.stderr, flush=True)
        # Return mock data for testing
        import random
        import time as time_module
        for i in range(min(time_value, 10)):
            results.append({
                "dataset_idx": -(i + 1),
                "experiment_type": experiment_type or "mock",
                "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.localtime(time_module.time() - i * 3600)),
            })
        return results

    try:
        dv = _data.dv if hasattr(_data, 'dv') else None
        if dv is None:
            print("WORKFLOW_ANALYZE: DataVault not available, returning mock data", file=sys.stderr, flush=True)
            # Return mock data
            import random
            import time as time_module
            for i in range(min(time_value, 10)):
                results.append({
                    "dataset_idx": -(i + 1),
                    "experiment_type": experiment_type or "mock",
                    "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.localtime(time_module.time() - i * 3600)),
                })
            return results

        # Navigate to qubit directory in DataVault
        # DataVault structure: /{user}/{date}/{qubit}/{experiment}
        try:
            # Go to root and find our session
            dv.cd([''])
        except Exception:
            pass

        # Try to find datasets for this qubit
        # Look for datasets in current directory
        try:
            # List available datasets
            # DataVault typically stores: yyyy_mm_dd_HH_MM_SS experiment_name
            datasets = dv.dir()
            print(f"WORKFLOW_ANALYZE: Found datasets: {datasets[:20] if len(datasets) > 20 else datasets}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"WORKFLOW_ANALYZE: Failed to list datasets: {e}", file=sys.stderr, flush=True)

        # For now, return mock data since we don't know the exact DataVault structure
        import random
        import time as time_module
        base_time = time_module.time()
        for i in range(min(time_value, 10)):
            # Create a plausible dataset index (negative = relative to latest)
            results.append({
                "dataset_idx": -(i + 1),
                "experiment_type": experiment_type or ("iqraw" if i % 3 == 0 else "s21"),
                "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.localtime(base_time - i * 3600 * 24)),
            })

    except Exception as e:
        print(f"WORKFLOW_ANALYZE: Error querying DataVault: {e}", file=sys.stderr, flush=True)
        # Return mock data on error
        import random
        import time as time_module
        for i in range(min(time_value, 10)):
            results.append({
                "dataset_idx": -(i + 1),
                "experiment_type": experiment_type or "mock",
                "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S", time_module.localtime(time_module.time() - i * 3600)),
            })

    print(f"WORKFLOW_ANALYZE: Returning {len(results)} historical entries", file=sys.stderr, flush=True)
    return results


def _load_experiment_configs() -> dict:
    """Load experiment configurations from experiment_configs.json."""
    import json as _json
    config_path = Path(__file__).parent.parent / "config" / "experiment_configs.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception as e:
        print(f"Failed to load experiment_configs: {e}", file=sys.stderr)
        return {"experiments": {}}


def _serialize_for_json(obj):
    """Convert object to JSON-serializable format."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    elif isinstance(obj, (list, tuple)):
        return [_serialize_for_json(x) for x in obj]
    elif isinstance(obj, dict):
        return {k: _serialize_for_json(v) for k, v in obj.items()}
    elif hasattr(obj, "__dict__"):
        return _serialize_for_json(obj.__dict__)
    else:
        return str(obj)


def run_workflow_node_with_loop(node, node_results, workflow_ctx, check_cancel_fn, loop_state=None):
    """Execute a single workflow node, with While loop support.

    If the node is a 'while' type, execute the loop body iteratively until
    the condition is met or max iterations reached.
    """
    node_id = node["id"]
    node_type = node.get("type", "experiment")

    if node_type == "while":
        config = node.get("config", {})
        condition_str = config.get("condition", "")
        max_iterations = int(config.get("maxIterations", 10))
        timeout = int(config.get("timeout", 300))

        start_time = time.time()
        iteration = 0
        last_result = None
        condition_met = False

        print(f"WHILE_START: {node_id} condition={condition_str} max={max_iterations}", file=sys.stderr, flush=True)

        while iteration < max_iterations:
            # Check timeout
            if time.time() - start_time > timeout:
                print(f"WHILE_TIMEOUT: {node_id}", file=sys.stderr, flush=True)
                break

            # Check cancel
            if check_cancel_fn and check_cancel_fn("workflow"):
                return {
                    "nodeId": node_id, "type": "while",
                    "status": "cancelled",
                    "stdout": f"Cancelled at iteration {iteration}",
                    "error": "Workflow cancelled",
                    "metrics": {"iterations": iteration}
                }

            # Evaluate condition
            try:
                # Substitute {{nodes.x.metric}} in condition
                resolved_condition = resolve_template(condition_str, node_results, workflow_ctx)
                # Evaluate as Python expression
                condition_met = bool(eval(resolved_condition, {"__builtins__": {}}, {}))
                print(f"WHILE_ITER: {node_id} iter={iteration} condition='{resolved_condition}' met={condition_met}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"WHILE_COND_ERROR: {node_id} {e}", file=sys.stderr, flush=True)
                condition_met = False

            if condition_met:
                # Condition met - exit loop (success)
                return {
                    "nodeId": node_id, "type": "while",
                    "status": "completed",
                    "stdout": f"Condition met after {iteration} iterations",
                    "metrics": {"iterations": iteration, "condition": condition_str}
                }

            # Execute the body (next dependent nodes)
            # The while node's body is determined by nodes that depend on it
            body_nodes = [n for n in node_results.get("_workflow_nodes", [])
                         if node_id in (n.get("depends") or [])]
            # Execute each body node
            for body_node in body_nodes:
                if check_cancel_fn and check_cancel_fn("workflow"):
                    return {
                        "nodeId": node_id, "type": "while",
                        "status": "cancelled",
                        "stdout": f"Cancelled during iteration {iteration}",
                        "error": "Workflow cancelled",
                        "metrics": {"iterations": iteration}
                    }
                result = run_workflow_node(body_node, node_results, workflow_ctx, check_cancel_fn)
                # Update context
                node_results[body_node["id"]] = {"result": result}
                if result.get("metrics"):
                    for k, v in result["metrics"].items():
                        workflow_ctx[f"nodes.{body_node['id']}.{k}"] = v
                workflow_ctx[f"nodes.{body_node['id']}.status"] = result.get("status", "")
                # Emit progress for body node
                progress = {"type": "workflow_progress", "nodeId": body_node["id"], "status": result.get("status"), "nodeType": body_node.get("type", "unknown")}
                print(json.dumps(progress), flush=True)
                if result.get("status") in ("error", "cancelled"):
                    return {
                        "nodeId": node_id, "type": "while",
                        "status": "failed",
                        "stdout": f"Body node {body_node['id']} failed at iteration {iteration}",
                        "error": result.get("error", "Body node failed"),
                        "metrics": {"iterations": iteration}
                    }

            iteration += 1

        # Exit loop without meeting condition (could be timeout or max iterations)
        return {
            "nodeId": node_id, "type": "while",
            "status": "completed",
            "stdout": f"Loop ended after {iteration} iterations (max={max_iterations}, condition not met)",
            "metrics": {"iterations": iteration, "condition": condition_str, "loop_completed": True}
        }

    # Normal node execution
    return run_workflow_node(node, node_results, workflow_ctx, check_cancel_fn)


def run_workflow(workflow_json: str, workflow_id: str):
    """Execute a full workflow JSON with parallel scheduling and While loop support."""
    try:
        wf = json.loads(workflow_json)
    except Exception as e:
        return {"status": "error", "stdout": "", "stderr": "", "error": f"Invalid workflow JSON: {e}"}

    workflow_name = wf.get("name", "Unnamed")
    nodes = wf.get("nodes", [])
    initial_context = wf.get("context", {})

    # Build dependency graph
    node_map = {n["id"]: n for n in nodes}

    print(f"WORKFLOW_START: {workflow_name} ({workflow_id}), {len(nodes)} nodes", file=sys.stderr, flush=True)

    def wf_check_cancel(wf_id=None):
        flag = os.path.join(os.environ.get("TEMP", "/tmp"), f"qmclaw_cancel_{wf_id or workflow_id}.flag")
        return os.path.exists(flag)

    node_results = {"_workflow_nodes": nodes}  # Include all nodes for while loop body lookup
    workflow_ctx = dict(initial_context)
    completed = set()

    # Main execution loop with parallel scheduling
    max_iterations = len(nodes) * 3  # Allow retries for while loops
    for _iteration in range(max_iterations):
        if wf_check_cancel():
            break

        # Find nodes that can be executed now (all dependencies completed)
        ready_nodes = []
        for node in nodes:
            nid = node["id"]
            if nid in completed:
                continue
            deps = node.get("depends", [])
            if all(d in completed for d in deps):
                ready_nodes.append(node)

        if not ready_nodes:
            break  # No more progress possible

        # Execute ready nodes (in parallel conceptually - sequential for now)
        for node in ready_nodes:
            node_id = node["id"]
            result = run_workflow_node_with_loop(node, node_results, workflow_ctx, wf_check_cancel)
            node_results[node_id] = {"result": result}

            # Update workflow context with node outputs
            r = result
            workflow_ctx[f"nodes.{node_id}.status"] = r.get("status", "")
            workflow_ctx[f"nodes.{node_id}.stdout"] = r.get("stdout", "")
            workflow_ctx[f"nodes.{node_id}.plotPath"] = r.get("plotPath", "")
            workflow_ctx[f"nodes.{node_id}.error"] = r.get("error", "")
            # Copy metrics flat to context
            if r.get("metrics"):
                for k, v in r["metrics"].items():
                    workflow_ctx[f"nodes.{node_id}.{k}"] = v

            # Decision node: always store symptom and recommendations in context
            if node.get("type") == "decision":
                # Get custom output variable names from config
                output_config = node.get("config", {})
                symptom_var = output_config.get("symptomOutputVar", "symptom")
                recommendations_var = output_config.get("recommendationsOutputVar", "recommendations")
                reasoning_var = output_config.get("reasoningOutputVar", "reasoning")

                # Store symptom (always as string, even if empty)
                symptom_val = r.get("symptom", "") or ""
                workflow_ctx[f"nodes.{node_id}.{symptom_var}"] = symptom_val
                workflow_ctx[f"nodes.{node_id}.symptom"] = symptom_val  # Always also store as "symptom"

                # Store recommendations (always as JSON string, even if empty)
                rec_val = r.get("recommendations", "[]")
                if isinstance(rec_val, list):
                    rec_val = json.dumps(rec_val, ensure_ascii=False)
                elif not isinstance(rec_val, str):
                    rec_val = "[]"
                workflow_ctx[f"nodes.{node_id}.{recommendations_var}"] = rec_val
                workflow_ctx[f"nodes.{node_id}.recommendations"] = rec_val  # Always also store as "recommendations"

                # Store reasoning with custom variable name
                reasoning_val = r.get("reasoning", "") or ""
                workflow_ctx[f"nodes.{node_id}.{reasoning_var}"] = reasoning_val
                workflow_ctx[f"nodes.{node_id}.reasoning"] = reasoning_val  # Always also store as "reasoning"

                # Store matched rules if available
                matched_rules_val = r.get("matchedRules", [])
                if isinstance(matched_rules_val, list):
                    workflow_ctx[f"nodes.{node_id}.matchedRules"] = matched_rules_val
                workflow_ctx[f"nodes.{node_id}.matchedRules"] = json.dumps(matched_rules_val) if isinstance(matched_rules_val, list) else matched_rules_val

            # Emit progress
            node_type = node.get("type", "unknown")
            progress = {"type": "workflow_progress", "workflowId": workflow_id, "nodeId": node_id, "status": r.get("status"), "nodeType": node_type}
            print(json.dumps(progress), flush=True)

            # Determine if node completed successfully
            # "skipped" nodes are treated as completed for dependency purposes
            # (they don't block downstream nodes)
            if r.get("status") in ("completed", "passed", "skipped"):
                completed.add(node_id)
            elif r.get("status") in ("error", "cancelled"):
                # Remove _workflow_nodes from output before returning
                node_results_clean = {k: v for k, v in node_results.items() if k != "_workflow_nodes"}
                # Determine final status
                statuses = [nr["result"].get("status") for nr in node_results_clean.values()]
                ok_statuses = {"completed", "passed"}
                if all(s in ok_statuses for s in statuses):
                    final_status = "completed"
                else:
                    final_status = "failed"
                return {
                    "status": final_status,
                    "workflowId": workflow_id,
                    "workflowName": workflow_name,
                    "stdout": json.dumps({"nodes": node_results_clean, "context": workflow_ctx}),
                    "stderr": "",
                    "error": r.get("error", ""),
                    "nodeResults": node_results_clean,
                }
            # While loop nodes: mark as completed regardless of condition met
            elif node.get("type") == "while":
                completed.add(node_id)

    # Clean up _workflow_nodes from output
    node_results_clean = {k: v for k, v in node_results.items() if k != "_workflow_nodes"}

    # Determine final status
    if node_results_clean:
        statuses = [nr["result"].get("status") for nr in node_results_clean.values()]
        # "passed" from quality_gate and "skipped" are treated as success
        ok_statuses = {"completed", "passed", "skipped"}
        if all(s in ok_statuses for s in statuses):
            final_status = "completed"
        elif any(s in ("error", "cancelled") for s in statuses):
            final_status = "failed"
        else:
            final_status = "failed"
    else:
        final_status = "failed"

    return {
        "status": final_status,
        "workflowId": workflow_id,
        "workflowName": workflow_name,
        "stdout": json.dumps({"nodes": node_results_clean, "context": workflow_ctx}),
        "stderr": "",
        "error": "",
        "nodeResults": node_results_clean,
    }


# ── Flask-style endpoints (integrated, no separate Flask server) ──────────────────

def handle_flask_request(action, data):
    """Handle Flask-style requests using the pre-initialized LabRAD connection."""
    cid = data.get("cid", "")

    try:
        if action == "health":
            return {"cid": cid, "action": action, "data": {
                "status": "running", "ready": True, "busy": False,
                "session": {
                    "conn_id": str(_cxn.ID),
                    "name": _cxn.name,
                    "host": _cxn.host,
                    "port": _cxn.port,
                    "connected": _cxn.connected,
                }
            }}

        elif action == "experiments":
            experiments = []
            for name in dir(_sq):
                if name.startswith("_") or name.startswith("qq"):
                    continue
                obj = getattr(_sq, name)
                if not callable(obj):
                    continue
                doc = getattr(obj, "__doc__", None) or ""
                experiments.append({
                    "name": name,
                    "fullName": f"sq.{name}",
                    "doc": doc.strip().split("\n")[0][:120] if doc else "",
                })
            experiments.sort(key=lambda x: x["name"])
            return {"cid": cid, "action": action, "data": {"experiments": experiments}}

        elif action == "sessions":
            with _labrad_lock:
                dv = _cxn.data_vault
                dv.cd([''])
                dirs = dv.dir()
                groups = [d for d in dirs[0] if not d.startswith('.')]
                sessions = [{"name": g, "path": ['', g]} for g in sorted(groups)]
            return {"cid": cid, "action": action, "data": {
                "current": {
                    "conn_id": str(_cxn.ID),
                    "name": _cxn.name,
                    "host": _cxn.host,
                    "port": _cxn.port,
                    "connected": _cxn.connected,
                },
                "sessions": sessions,
            }}

        elif action == "session_tree":
            # Get full directory tree for DataVault
            def get_dir_tree(path, depth=0, max_depth=5):
                """Recursively get directory tree."""
                if depth >= max_depth:
                    return []
                result = []
                try:
                    dv.cd(path)
                    dirs = dv.dir()
                    for name in sorted(dirs[0]):
                        if name.startswith('.'):
                            continue
                        child_path = path + [name] if path else ['', name]
                        # Check if this directory has subdirectories
                        try:
                            dv.cd(child_path[1:] if child_path[0] == '' else child_path)
                            subdirs = dv.dir()[0]
                            has_children = any(not d.startswith('.') for d in subdirs)
                            dv.cd(path)  # Go back
                        except:
                            has_children = False
                        result.append({
                            "name": name,
                            "path": child_path,
                            "hasChildren": has_children,
                        })
                except Exception as e:
                    print(f"session_tree error at {path}: {e}", file=sys.stderr)
                return result

            with _labrad_lock:
                dv = _cxn.data_vault
                tree = get_dir_tree([''])
            return {"cid": cid, "action": action, "data": {"tree": tree}}

        elif action == "switch_session":
            session_path = data.get("path", [])
            with _labrad_lock:
                dv = _cxn.data_vault
                dv.cd('')  # absolute: go to root first
                clean_path = session_path[1:] if session_path and session_path[0] == '' else session_path
                dv.cd(clean_path)

            # Save session to config file
            user = clean_path[0] if clean_path else 'LQHL'
            path_segments = clean_path[1:] if len(clean_path) > 1 else []
            _save_session_config(user, path_segments)

            # Reload qubits for the new session
            reload_qubits(clean_path)
            # Get list of qubits for response
            qubits = []
            if _s:
                for qname in sorted(_s.keys()):
                    if qname.startswith('q'):
                        try:
                            qobj = _s[qname]
                            qubits.append({
                                "name": qname,
                                "f10": float(qobj.regs.f10) if hasattr(qobj, 'regs') else None,
                                "fread": float(qobj.regs.fread) if hasattr(qobj, 'regs') else None,
                            })
                        except:
                            qubits.append({"name": qname})
            return {"cid": cid, "action": action, "data": {"success": True, "path": session_path, "qubits": qubits}}

        elif action == "debug_data":
            # Debug: check _data object state
            data_info = {"_data_is_none": _data is None, "_current_session_path": str(_current_session_path)}
            if _data:
                try:
                    # Try to access the session info
                    data_info["has_session"] = hasattr(_data, 'session')
                    data_info["current_session_path"] = _current_session_path
                    # Try to get dataset count
                    try:
                        dv = _cxn.data_vault
                        dv.cd('')  # reset to root
                        dv.cd(_current_session_path)
                        datasets = dv.dir()
                        data_info["dataset_count"] = len(datasets[1]) if datasets and len(datasets) > 1 else 0
                        data_info["datasets"] = datasets[1][:5] if datasets and len(datasets) > 1 else []
                    except Exception as e:
                        data_info["dv_error"] = str(e)
                except Exception as e:
                    data_info["error"] = str(e)
            print(f"DEBUG_DATA: {json.dumps(data_info)}", file=sys.stderr, flush=True)
            return {"cid": cid, "action": action, "data": data_info}

        elif action == "test_load_dataset":
            # Test loading the latest dataset
            try:
                _data.loadDataset(-1)
                return {"cid": cid, "action": action, "data": {
                    "success": True,
                    "dataset_name": _data.dataset_name,
                    "data_shape": _data.data.shape if hasattr(_data, 'data') else None,
                }}
            except Exception as e:
                import traceback as tb
                return {"cid": cid, "action": action, "data": {
                    "success": False,
                    "error": str(e),
                    "traceback": tb.format_exc(),
                }}

        elif action == "plot_dataset":
            # Execute plot command on the latest dataset
            plot_command = data.get("command", "")
            try:
                import matplotlib.pyplot as plt
                import numpy as np
                from lqms.data_process import dataAnalysisCore as dc

                # Create a switch_session wrapper that updates job_runner's globals
                def switch_session(session):
                    global _data, _qter
                    _data = dc.DataLab(session, _cxn.data_vault, dv_type='data_vault')
                    if _qter is not None:
                        _qter.data = _data
                    return _data

                # Build full command: auto-switch session first, then user command
                full_command = f"switch_session({_current_session_path})\n{plot_command}"

                # Debug: log session info
                print(f"PLOT_DATASET: _current_session_path={_current_session_path}", file=sys.stderr, flush=True)
                print(f"PLOT_DATASET: _data={_data}, session={getattr(_data, 'session', 'N/A')}", file=sys.stderr, flush=True)
                print(f"PLOT_DATASET: _qter={_qter}, data={getattr(_qter, 'data', 'N/A')}", file=sys.stderr, flush=True)

                # Clear any existing figures
                plt.close('all')

                # Load latest dataset using _data (after switch_session updates it)
                _data.loadDataset(-1)
                print(f"PLOT_DATASET: _data.loadDataset loaded={_data.dataset_name}", file=sys.stderr, flush=True)
                _x = _data.data[:, 0]
                _y = _data.data[:, 1] if _data.data.ndim > 1 and _data.data.shape[1] > 1 else _data.data[:, 0]

                # Capture stdout for analysis output (qter.fitData, etc.)
                import io
                old_stdout = sys.stdout
                captured_output = io.StringIO()
                sys.stdout = captured_output

                # Create figure and apply custom commands
                _fig = plt.figure(figsize=(10, 6))
                if plot_command:
                    exec_globals = {
                        "plt": plt, "np": np,
                        "_x": _x, "_y": _y,
                        "data": _data,
                        "qter": _qter,
                        "switch_session": switch_session,
                    }
                    print(f"PLOT_DATASET: executing: {full_command[:200]}", file=sys.stderr, flush=True)
                    exec(full_command, exec_globals)
                    print(f"PLOT_DATASET: exec completed, fignums={plt.get_fignums()}", file=sys.stderr, flush=True)

                # Restore stdout and get captured output
                sys.stdout = old_stdout
                analysis_output = captured_output.getvalue()

                # If no plot was created, use default
                if not plt.get_fignums():
                    plt.plot(_x, _y, 'b.-')
                    plt.title(_data.dataset_name)
                    plt.xlabel('X')
                    plt.ylabel('Y')
                    plt.grid(True)
                    plt.tight_layout()

                # Save the plot with unique filename to avoid cache
                import time
                _plots_dir = PLOTS_DIR
                import os
                os.makedirs(_plots_dir, exist_ok=True)
                _plot_filename = f"plot_{int(time.time() * 1000)}.png"
                _path = os.path.join(_plots_dir, _plot_filename)
                plt.savefig(_path, dpi=150, bbox_inches='tight')
                plt.close('all')
                return {"cid": cid, "action": action, "data": {
                    "success": True,
                    "plot_path": _path,
                    "plot_filename": _plot_filename,
                    "dataset_name": _data.dataset_name,
                    "analysis_output": analysis_output,
                }}
            except Exception as e:
                # Ensure stdout is restored on error
                try:
                    sys.stdout = old_stdout
                except NameError:
                    pass  # old_stdout not defined yet
                import traceback as tb
                return {"cid": cid, "action": action, "data": {
                    "success": False,
                    "error": str(e),
                    "traceback": tb.format_exc(),
                }}

        elif action == "list_qubits":
            # List all qubits in current session
            qubits = []
            if _s:
                for qname in sorted(_s.keys()):
                    if qname.startswith('q'):
                        try:
                            qobj = _s[qname]
                            qubit_info = {"name": qname}
                            if hasattr(qobj, 'regs'):
                                try:
                                    qubit_info["f10"] = float(qobj.regs.f10)
                                    qubit_info["fread"] = float(qobj.regs.fread)
                                    qubit_info["bias_z"] = float(qobj.regs.bias_z)
                                except:
                                    pass
                            qubits.append(qubit_info)
                        except Exception as e:
                            qubits.append({"name": qname, "error": str(e)})
            return {"cid": cid, "action": action, "data": {"qubits": qubits, "sessionPath": _current_session_path}}

        elif action == "get_qubit_params":
            # Get all parameters for a specific qubit
            qname = data.get("name")
            if not qname:
                return {"cid": cid, "action": action, "error": "Qubit name required"}
            if not _s or qname not in _s.keys():
                return {"cid": cid, "action": action, "error": f"Qubit {qname} not found in current session"}

            qobj = _s[qname]
            params = {}

            # Helper function to safely get parameter
            def get_param(obj, key, default=None):
                try:
                    val = getattr(obj, key, None)
                    if val is not None:
                        return float(val)
                except:
                    pass
                return default

            def get_nested_param(obj, parent, child, default=None):
                try:
                    parent_obj = getattr(obj, parent, None)
                    if parent_obj is not None:
                        val = getattr(parent_obj, child, None)
                        if val is not None:
                            return float(val)
                except:
                    pass
                return default

            # Basic parameters
            params["f10"] = get_param(qobj, 'f10')
            params["fread"] = get_param(qobj, 'fread')
            params["fc"] = get_param(qobj, 'fc')
            params["f21"] = get_param(qobj, 'f21')
            params["bias_z"] = get_param(qobj, 'bias_z')

            # PiGate parameters
            params["PiGate.amp"] = get_nested_param(qobj, 'PiGate', 'amp')
            params["PiGate.length"] = get_nested_param(qobj, 'PiGate', 'length')
            params["PiGate.alpha"] = get_nested_param(qobj, 'PiGate', 'alpha')
            params["PiGate.zpa"] = get_nested_param(qobj, 'PiGate', 'zpa')

            # PiHalf parameters
            params["PiHalf.amp"] = get_nested_param(qobj, 'PiHalf', 'amp')
            params["PiHalf.length"] = get_nested_param(qobj, 'PiHalf', 'length')
            params["PiHalf.alpha"] = get_nested_param(qobj, 'PiHalf', 'alpha')
            params["PiHalf.zpa"] = get_nested_param(qobj, 'PiHalf', 'zpa')

            # ReadIn parameters
            params["ReadIn.power"] = get_nested_param(qobj, 'ReadIn', 'power')
            params["ReadIn.length"] = get_nested_param(qobj, 'ReadIn', 'length')
            params["ReadIn.ring_power"] = get_nested_param(qobj, 'ReadIn', 'ring_power')
            params["ReadIn.ring_length"] = get_nested_param(qobj, 'ReadIn', 'ring_length')
            params["ReadIn.zpa"] = get_nested_param(qobj, 'ReadIn', 'zpa')

            # ReadOut parameters
            params["ReadOut.amp"] = get_nested_param(qobj, 'ReadOut', 'amp')
            params["ReadOut.length"] = get_nested_param(qobj, 'ReadOut', 'length')
            params["ReadOut.window_type"] = get_nested_param(qobj, 'ReadOut', 'window_type')

            # Discriminator parameters
            params["discriminator.center0"] = get_nested_param(qobj, 'discriminator', 'center0')
            params["discriminator.center1"] = get_nested_param(qobj, 'discriminator', 'center1')
            params["discriminator.measure_f0"] = get_nested_param(qobj, 'discriminator', 'measure_f0')
            params["discriminator.measure_f1"] = get_nested_param(qobj, 'discriminator', 'measure_f1')
            params["discriminator.method"] = get_nested_param(qobj, 'discriminator', 'method')
            params["discriminator.radius0"] = get_nested_param(qobj, 'discriminator', 'radius0')
            params["discriminator.threshold"] = get_nested_param(qobj, 'discriminator', 'threshold')

            print(f"GET_QUBIT_PARAMS: {qname} - {len(params)} parameters", file=sys.stderr, flush=True)
            return {"cid": cid, "action": action, "data": {
                "name": qname,
                "sessionPath": _current_session_path,
                "params": params
            }}

        elif action == "set_qubit_params":
            # Set parameters for a specific qubit
            qname = data.get("name")
            params = data.get("params", {})
            if not qname:
                return {"cid": cid, "action": action, "error": "Qubit name required"}
            if not _s or qname not in _s.keys():
                return {"cid": cid, "action": action, "error": f"Qubit {qname} not found in current session"}

            qobj = _s[qname]
            updated = []
            errors = []

            for key, value in params.items():
                if value is None:
                    continue
                try:
                    parts = key.split(".")
                    if len(parts) == 1:
                        setattr(qobj, key, value)
                        updated.append(key)
                    elif len(parts) == 2:
                        parent = getattr(qobj, parts[0], None)
                        if parent is not None:
                            setattr(parent, parts[1], value)
                            updated.append(key)
                        else:
                            errors.append(f"{key}: parent not found")
                except Exception as e:
                    errors.append(f"{key}: {str(e)}")

            print(f"SET_QUBIT_PARAMS: {qname} - updated {len(updated)} params: {updated}", file=sys.stderr, flush=True)
            if errors:
                print(f"SET_QUBIT_PARAMS: errors: {errors}", file=sys.stderr, flush=True)

            return {"cid": cid, "action": action, "data": {
                "success": len(errors) == 0,
                "name": qname,
                "updated": updated,
                "errors": errors if errors else None
            }}

        elif action == "datasets":
            path_str = data.get("path", "LQHL/test/20260324")
            path_parts = [p for p in path_str.strip("/").split("/") if p]
            with _labrad_lock:
                dv = _cxn.data_vault
                dv.cd('')  # absolute: go to root first
                dv.cd(path_parts)
                dirs = dv.dir()
                groups = sorted([d for d in dirs[0] if not d.startswith('.')])
                datasets = []
                for name in dirs[1]:
                    datasets.append({"name": name, "id": name, "path": path_str})
            return {"cid": cid, "action": action, "data": {
                "path": path_str, "groups": groups, "datasets": datasets
            }}

        elif action == "plot":
            dataset_name = urllib.parse.unquote(data.get("name", ""))
            path_str = urllib.parse.unquote(data.get("path", "LQHL/test/20260324"))
            if not dataset_name:
                return {"cid": cid, "action": action, "error": "name required"}
            path_parts = [p for p in path_str.strip("/").split("/") if p]

            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            plot_data = None
            dataset_label = dataset_name
            with _labrad_lock:
                dv = _cxn.data_vault
                dv.cd('')
                dv.cd(path_parts)
                dirs = dv.dir()
                names = dirs[1]
                if dataset_name not in names:
                    return {"cid": cid, "action": action, "error": f"Dataset '{dataset_name}' not found in {names[:5]}..."}
                idx = names.index(dataset_name) + 1
                dv.open(idx)
                dv_dirs = dv.dir()
                dataset_label = dv_dirs[1][idx - 1]
                plot_data = dv.get()

            # Handle different data formats from LabRAD
            if hasattr(plot_data, '__iter__') and not isinstance(plot_data, str):
                if isinstance(plot_data, tuple) and len(plot_data) >= 2:
                    x_data, y_data = plot_data[0], plot_data[1]
                elif hasattr(plot_data, 'shape') and len(plot_data.shape) == 2:
                    # 2D array - take first row as y, use index as x
                    x_data = list(range(plot_data.shape[1]))
                    y_data = plot_data[0]
                else:
                    # Single array
                    x_data = list(range(len(plot_data)))
                    y_data = plot_data
            else:
                x_data = list(range(len(plot_data) if hasattr(plot_data, '__len__') else 1))
                y_data = plot_data

            fig = plt.figure(figsize=(8, 6))
            ax = fig.add_subplot(111)
            ax.plot(x_data, y_data, "b.-")
            ax.set_title(dataset_label)
            ax.set_xlabel("X")
            ax.set_ylabel("Y")
            fig.tight_layout()

            plot_file = os.path.join(PLOTS_DIR, f"dv_{abs(hash(dataset_name))}.png")
            os.makedirs(PLOTS_DIR, exist_ok=True)
            fig.savefig(plot_file, dpi=150, bbox_inches="tight")
            plt.close(fig)
            return {"cid": cid, "action": action, "data": {"plotPath": plot_file, "name": dataset_label}}

        elif action == "hardware_status":
            """Return hardware connection status by probing actual devices."""
            import time as _time
            result_data = {
                "overall": "ok",
                "timestamp": _time.strftime("%Y-%m-%d %H:%M:%S"),
                "services": {},
                "devices": {},
                "issues": [],
            }

            # 1. Check LabRAD
            try:
                result_data["services"]["labrad"] = {
                    "status": "ok" if _cxn.connected else "error",
                    "details": {
                        "connected": _cxn.connected,
                        "id": str(_cxn.ID),
                        "host": _cxn.host,
                        "port": _cxn.port,
                    }
                }
                if not _cxn.connected:
                    result_data["overall"] = "error"
                    result_data["issues"].append("LabRAD disconnected")
            except Exception as e:
                result_data["services"]["labrad"] = {"status": "error", "error": str(e)}
                result_data["overall"] = "error"

            # 2. Check Ray
            try:
                import ray as _ray
                if _ray.is_initialized():
                    result_data["services"]["ray"] = {"status": "ok", "details": {"initialized": True}}
                    try:
                        device_manager = _ray.get_actor('Device Manager')
                        result_data["services"]["ray"]["details"]["device_manager"] = "available"
                    except:
                        result_data["services"]["ray"]["details"]["device_manager"] = "not found"
                else:
                    result_data["services"]["ray"] = {"status": "warning", "error": "Ray not initialized"}
                    result_data["overall"] = "warning" if result_data["overall"] != "error" else "error"
            except Exception as e:
                result_data["services"]["ray"] = {"status": "error", "error": str(e)}
                result_data["overall"] = "warning" if result_data["overall"] != "error" else "error"

            # 3. Check DataVault
            try:
                with _labrad_lock:
                    dv = _cxn.data_vault
                    dv.cd([''])
                    root_dirs = dv.dir()
                    result_data["services"]["datavault"] = {
                        "status": "ok",
                        "details": {"accessible": True, "root_groups": root_dirs[0]}
                    }
            except Exception as e:
                result_data["services"]["datavault"] = {"status": "error", "error": str(e)}
                result_data["overall"] = "error"
                result_data["issues"].append(f"DataVault: {e}")

            # 4. Check qubit params (q3ld4 as example)
            try:
                qobj = _s['q3ld4']
                result_data["devices"]["qubit_params"] = {
                    "status": "ok",
                    "details": {
                        "f10": float(qobj.f10),
                        "fread": float(qobj.fread),
                        "bias_z": float(qobj.bias_z),
                        "read_power": float(qobj.ReadIn.power),
                    }
                }
            except Exception as e:
                result_data["devices"]["qubit_params"] = {"status": "warning", "error": str(e)}

            # 5. Check microwave/AWG params
            try:
                qobj = _s['q3ld4']
                result_data["devices"]["microwave"] = {
                    "status": "ok",
                    "details": {
                        "fread": float(qobj.fread),
                        "fread_unit": "GHz",
                        "f10": float(qobj.f10),
                        "f10_unit": "GHz",
                        "bias_z": float(qobj.bias_z),
                        "bias_unit": "V",
                    }
                }
                result_data["devices"]["awg"] = {
                    "status": "ok",
                    "details": {
                        "pi_amp": float(qobj.PiGate.amp),
                        "pi_length": float(qobj.PiGate.length),
                        "pi_alpha": float(qobj.PiGate.alpha),
                        "half_amp": float(qobj.PiHalf.amp),
                        "half_length": float(qobj.PiHalf.length),
                    }
                }
            except Exception as e:
                result_data["devices"]["microwave"] = {"status": "warning", "error": str(e)}
                result_data["devices"]["awg"] = {"status": "warning", "error": str(e)}

            return {"cid": cid, "action": action, "data": result_data}

        elif action == "quick_status":
            """Return quick status summary for header display."""
            status = {"labrad": "error", "ray": "error", "datavault": "error", "message": ""}
            try:
                if _cxn.connected:
                    status["labrad"] = "ok"
                else:
                    status["message"] = "LabRAD disconnected"
                    return {"cid": cid, "action": action, "data": status}
            except:
                status["message"] = "Cannot connect to LabRAD"
                return {"cid": cid, "action": action, "data": status}

            try:
                import ray as _ray
                if _ray.is_initialized():
                    status["ray"] = "ok"
                else:
                    status["ray"] = "warning"
            except:
                status["ray"] = "warning"

            try:
                with _labrad_lock:
                    dv = _cxn.data_vault
                    dv.cd([''])
                    status["datavault"] = "ok"
            except:
                status["datavault"] = "error"
                status["message"] = "DataVault inaccessible"

            if status["labrad"] == "ok" and status["ray"] == "ok" and status["datavault"] == "ok":
                status["message"] = "All systems ready"
            elif status["message"] == "":
                status["message"] = f"Issues: labrad={status['labrad']}, ray={status['ray']}, dv={status['datavault']}"

            return {"cid": cid, "action": action, "data": status}

        elif action == "llm_chat":
            # LLM chat for model testing
            provider = data.get("provider", "openai")
            model_id = data.get("modelId", "gpt-4o")
            messages = data.get("messages", [])
            temperature = float(data.get("temperature", 0.3))
            max_tokens = int(data.get("maxTokens", 500))
            base_url = data.get("baseUrl")

            try:
                content = ""
                usage = {}

                # Use MiniMax API directly if provider is minimax
                if provider == "minimax":
                    minimax_key = _get_minimax_key()
                    result = call_minimax_api(messages, model_id, minimax_key, temperature, max_tokens)
                    # Safely extract content from result
                    if isinstance(result, dict):
                        choices = result.get("choices", [])
                        if isinstance(choices, list) and len(choices) > 0:
                            first_choice = choices[0]
                            if isinstance(first_choice, dict):
                                msg = first_choice.get("message", {})
                                content = msg.get("content", "") if isinstance(msg, dict) else ""
                            else:
                                content = ""
                        else:
                            content = ""
                    else:
                        content = ""
                        print(f"FLASK_LLM: result is not a dict: {type(result)}", file=sys.stderr)
                else:
                    import openai

                    # Set up client based on provider
                    if provider == "deepseek":
                        client = openai.OpenAI(
                            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
                            base_url="https://api.deepseek.com/v1"
                        )
                    elif base_url:
                        # Custom base URL
                        api_key = os.environ.get("OPENAI_API_KEY", "")
                        client = openai.OpenAI(api_key=api_key, base_url=base_url)
                    else:
                        # Default OpenAI
                        client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

                    # Send request
                    response = client.chat.completions.create(
                        model=model_id,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )

                    content = response.choices[0].message.content or ""
                    if response.usage:
                        usage = {
                            "prompt_tokens": response.usage.prompt_tokens,
                            "completion_tokens": response.usage.completion_tokens,
                            "total_tokens": response.usage.total_tokens,
                        }

                return {"cid": cid, "action": action, "data": {"content": content, "usage": usage}}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        else:
            return {"cid": cid, "action": action, "error": f"Unknown action: {action}"}

    except Exception as e:
        return {"cid": cid, "action": action, "error": str(e)}


# ── Flask background thread ──────────────────────────────────────────────────
# Flask requests are handled in a dedicated thread so they never block
# while a job/experiment is running in the main thread.

import threading, queue as _queue

_flask_queue: _queue.Queue = _queue.Queue()
_flask_results: dict = {}  # cid -> result (populated by background thread)
_results_lock = threading.Lock()
_stdin_lock = threading.Lock()  # protect stdout writes
_labrad_lock = threading.Lock()  # protect shared LabRAD connection

def _flask_worker():
    """Background thread: process Flask requests from queue, write results."""
    print("FLASK_WORKER: Started", file=sys.stderr, flush=True)
    while True:
        try:
            item = _flask_queue.get(timeout=0.5)
            if item is None:
                break  # shutdown signal
            cid, action, data = item
            print(f"FLASK_WORKER: Processing cid={cid} action={action}", file=sys.stderr, flush=True)
            try:
                result = handle_flask_request(action, data)
                print(f"FLASK_WORKER: handle_flask_request returned cid={cid}", file=sys.stderr, flush=True)
            except Exception as e:
                import traceback
                result = {"cid": cid, "action": action, "error": str(e)}
                print(f"FLASK_WORKER: Exception: {e}", file=sys.stderr, flush=True)
                print(f"FLASK_WORKER: Traceback: {traceback.format_exc()}", file=sys.stderr, flush=True)
            with _results_lock:
                _flask_results[cid] = result
                # Write result directly so Express can collect it
                with _stdin_lock:
                    print(f"FLASK_WORKER: Writing result for cid={cid}", file=sys.stderr, flush=True)
                    print(json.dumps(result), flush=True)
                    print(f"FLASK_WORKER: Done writing result for cid={cid}", file=sys.stderr, flush=True)
            _flask_queue.task_done()
        except _queue.Empty:
            continue
        except Exception:
            pass

_flask_thread = threading.Thread(target=_flask_worker, daemon=True)
_flask_thread.start()


# ── Event loop ────────────────────────────────────────────────────────────────

def check_workflow_cancel(workflow_id=None):
    flag = os.path.join(os.environ.get("TEMP", "/tmp"), f"qmclaw_cancel_{workflow_id or 'workflow'}.flag")
    return os.path.exists(flag)

print("READY", file=sys.stderr, flush=True)

# Process Flask requests in the main thread to avoid threading issues with stdin
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue

    try:
        obj = json.loads(line)
        msg_type = obj.get("type", "job")

        if msg_type == "workflow":
            wf_b64 = obj.get("workflow", "")
            wf_json = base64.b64decode(wf_b64).decode("utf-8", errors="replace")
            wf_id = obj.get("workflowId", "unknown")
            result = run_workflow(wf_json, wf_id)
            flag = os.path.join(os.environ.get("TEMP", "/tmp"), f"qmclaw_cancel_{wf_id}.flag")
            if os.path.exists(flag):
                try: os.remove(flag)
                except: pass
            print(json.dumps(result), flush=True)

        elif msg_type == "run_node":
            # Single node execution (for debugging)
            node_json = obj.get("node", "{}")
            context = obj.get("context", {})
            try:
                if isinstance(node_json, str):
                    node_data = json.loads(node_json)
                else:
                    node_data = node_json
            except:
                node_data = {}

            node_id = node_data.get("id", "unknown")
            print(f"SINGLE_NODE: Executing {node_id}", file=sys.stderr, flush=True)

            # Build minimal node_results and workflow_ctx
            node_results = {}
            workflow_ctx = dict(context)

            # Execute the node
            result = run_workflow_node(node_data, node_results, workflow_ctx, check_workflow_cancel)
            print(json.dumps(result), flush=True)

        elif msg_type == "flask":
            # Process Flask requests in the main thread (non-blocking)
            cid = obj.get("cid", "")
            action = obj.get("action", "health")
            flask_data = obj.get("data", {})  # Extract the data field
            flask_data["cid"] = cid  # Pass cid in data for handle_flask_request
            try:
                result = handle_flask_request(action, flask_data)
                output = json.dumps(result)
                sys.stdout.write(output + "\n")
                sys.stdout.flush()
            except Exception as e:
                import traceback
                result = {"cid": cid, "action": action, "error": str(e)}
                output = json.dumps(result)
                sys.stdout.write(output + "\n")
                sys.stdout.flush()
            continue

        else:
            # Default: single job
            code_b64 = obj.get("code", "")
            job_id = obj.get("jobId", "unknown")
            result = run_job(code_b64, job_id)
            flag = os.path.join(os.environ.get("TEMP", "/tmp"), f"qmclaw_cancel_{job_id}.flag")
            if os.path.exists(flag):
                try: os.remove(flag)
                except: pass
            print(json.dumps(result), flush=True)

    except json.JSONDecodeError as e:
        err = json.dumps({"status": "error", "stdout": "", "stderr": "", "error": f"JSON parse error: {e}"})
        print(err, flush=True)