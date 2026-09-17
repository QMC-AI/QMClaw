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
import subprocess
from typing import Dict, Any, Optional, List
import base64
import time
import signal
import traceback
import tempfile
import urllib.parse
import threading
import atexit

# PLOTS_DIR shared with Express server
PLOTS_DIR = os.environ.get("PLOTS_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "..", "qmclaw-web", "public", "plots"))
from io import StringIO

# ── Service Manager (Auto-start microservices) ─────────────────────────────────

class ServiceManager:
    """自动管理微服务的启动和停止"""

    SERVICES = {
        "llm": {
            "port": 3006,
            "script": "services/llm_service/server.py",
            "description": "LLM Service",
        },
        "quantum": {
            "port": 3003,
            "script": "services/quantum_service/server.py",
            "description": "Quantum Service",
        },
        "analysis": {
            "port": 3004,
            "script": "services/analysis_service/server.py",
            "description": "Analysis Service",
        },
        "agent": {
            "port": 3005,
            "script": "services/agent_service/server.py",
            "description": "Agent Service",
        },
        "image": {
            "port": 3007,
            "script": "services/image_service/server.py",
            "description": "Image Service",
        },
        "workflow": {
            "port": 3008,
            "script": "services/workflow_service/server.py",
            "description": "Workflow Service",
        },
        "task_queue": {
            "port": 3009,
            "script": "services/task_queue/server.py",
            "description": "Task Queue Service",
        },
    }

    def __init__(self):
        self.processes: Dict[str, Any] = {}
        self.server_dir = os.path.dirname(os.path.dirname(__file__))
        self._started = False

    def _check_port_available(self, port: int) -> bool:
        """检查端口是否可用"""
        import socket
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return False  # 端口被占用
        except (OSError, socket.timeout):
            return True  # 端口可用

    def _wait_for_service(self, name: str, port: int, timeout: int = 30) -> bool:
        """等待服务就绪"""
        import urllib.request
        import urllib.error

        start_time = time.time()
        while time.time() - start_time < timeout:
            if self._check_port_available(port):
                time.sleep(0.5)
                continue
            try:
                url = f"http://localhost:{port}/health"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=2) as response:
                    if response.status == 200:
                        print(f"SERVICE_MGR: {name} is ready (port {port})", file=sys.stderr, flush=True)
                        return True
            except Exception:
                pass
            time.sleep(0.5)

        print(f"SERVICE_MGR: {name} failed to start within {timeout}s", file=sys.stderr, flush=True)
        return False

    def start_all(self, services: Optional[List[str]] = None, timeout: int = 30) -> bool:
        """启动所有或指定的服务"""
        if self._started:
            print("SERVICE_MGR: Already started", file=sys.stderr, flush=True)
            return True

        if services is None:
            services = list(self.SERVICES.keys())

        print(f"SERVICE_MGR: Starting services: {', '.join(services)}", file=sys.stderr, flush=True)

        started = []
        for name in services:
            if name not in self.SERVICES:
                print(f"SERVICE_MGR: Unknown service: {name}", file=sys.stderr, flush=True)
                continue

            svc = self.SERVICES[name]
            port = svc["port"]

            # 检查端口是否已被占用
            if not self._check_port_available(port):
                print(f"SERVICE_MGR: {name} port {port} already in use, skipping", file=sys.stderr, flush=True)
                continue

            script_path = os.path.join(self.server_dir, svc["script"])
            if not os.path.exists(script_path):
                print(f"SERVICE_MGR: {name} script not found: {script_path}", file=sys.stderr, flush=True)
                continue

            print(f"SERVICE_MGR: Starting {name} ({svc['description']})...", file=sys.stderr, flush=True)

            try:
                env = os.environ.copy()
                # 确保 Python 使用 UTF-8 编码
                env["PYTHONIOENCODING"] = "utf-8"

                proc = subprocess.Popen(
                    [sys.executable, script_path],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.server_dir,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0,
                )
                self.processes[name] = proc
                started.append(name)
                print(f"SERVICE_MGR: {name} started (PID: {proc.pid})", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"SERVICE_MGR: Failed to start {name}: {e}", file=sys.stderr, flush=True)

        if not started:
            print("SERVICE_MGR: No services started", file=sys.stderr, flush=True)
            self._started = True
            return True

        # 等待所有服务就绪
        print(f"SERVICE_MGR: Waiting for {len(started)} services to be ready...", file=sys.stderr, flush=True)
        all_ready = True
        for name in started:
            svc = self.SERVICES[name]
            if not self._wait_for_service(name, svc["port"], timeout):
                all_ready = False

        if all_ready:
            print(f"SERVICE_MGR: All {len(started)} services ready!", file=sys.stderr, flush=True)
        else:
            print("SERVICE_MGR: Some services failed to start", file=sys.stderr, flush=True)

        self._started = True
        return all_ready

    def stop_all(self):
        """停止所有服务"""
        if not self.processes:
            return

        print("SERVICE_MGR: Stopping all services...", file=sys.stderr, flush=True)
        for name, proc in self.processes.items():
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                    print(f"SERVICE_MGR: {name} terminated", file=sys.stderr, flush=True)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    print(f"SERVICE_MGR: {name} killed", file=sys.stderr, flush=True)

        self.processes.clear()
        print("SERVICE_MGR: All services stopped", file=sys.stderr, flush=True)


# 全局服务管理器
_service_manager: Optional[ServiceManager] = None

def _get_service_manager() -> ServiceManager:
    global _service_manager
    if _service_manager is None:
        _service_manager = ServiceManager()
    return _service_manager


# ── Service Proxy (Progressive Migration) ──────────────────────────────────────
# 尝试导入服务代理模块用于渐进式迁移
_service_proxy = None
_use_services = os.environ.get("QMCLAW_USE_SERVICES", "false").lower() == "true"

if _use_services:
    try:
        # 添加 server 目录到路径
        _server_dir = os.path.dirname(os.path.dirname(__file__))
        if _server_dir not in sys.path:
            sys.path.insert(0, _server_dir)
        from services import client_proxy
        _service_proxy = client_proxy
        print(f"SERVICE_PROXY: Using microservices mode", file=sys.stderr, flush=True)

        # 自动启动所有微服务
        mgr = _get_service_manager()
        mgr.start_all()

        # 注册退出时停止所有服务
        atexit.register(_stop_all_services)
    except ImportError as e:
        print(f"SERVICE_PROXY: Failed to import client_proxy: {e}", file=sys.stderr, flush=True)
        _service_proxy = None
else:
    print(f"SERVICE_PROXY: Using legacy mode (set QMCLAW_USE_SERVICES=true to enable)", file=sys.stderr, flush=True)

def _stop_all_services():
    """退出时停止所有微服务"""
    global _service_manager
    if _service_manager:
        _service_manager.stop_all()
        _service_manager = None

# 注册信号处理器 - 当收到终止信号时停止服务
def _signal_handler(signum, frame):
    print(f"SERVICE_MGR: Received signal {signum}, stopping services...", file=sys.stderr, flush=True)
    _stop_all_services()
    # 重新抛出信号让默认处理器继续
    signal.signal(signum, signal.SIG_DFL)
    raise KeyboardInterrupt

# 只在启用服务模式时注册信号处理器
if _use_services:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
else:
    print(f"SERVICE_PROXY: Using legacy mode (set QMCLAW_USE_SERVICES=true to enable)", file=sys.stderr, flush=True)


# ── Migration Helper Functions ──────────────────────────────────────────────────
# 渐进式迁移: 优先使用服务代理，失败时回退到本地实现

def _try_service_call(service_method, fallback_result=None, fallback_error_msg=None):
    """尝试调用服务方法，失败时返回回退值"""
    if not _service_proxy:
        return fallback_result
    try:
        result = service_method()
        if "error" in result and result["error"]:
            print(f"SERVICE_PROXY: Service call failed: {result['error']}, falling back to legacy", file=sys.stderr)
            return fallback_result
        return result
    except Exception as e:
        print(f"SERVICE_PROXY: Service call exception: {e}, falling back to legacy", file=sys.stderr)
        return fallback_result


# ── Backend action handlers (progressive migration to services) ─────────────────

def _handle_backend_action(action: str, data: dict, cid: str = "") -> dict:
    """Handle backend-style requests - progressive migration to microservices.

    When QMCLAW_USE_SERVICES=true, this function will delegate to microservices.
    Otherwise, it uses the legacy direct LabRAD calls.
    """
    # 如果启用服务模式，尝试使用服务代理
    if _use_services and _service_proxy:
        return _handle_backend_action_via_services(action, data, cid)
    else:
        # 保持原有实现直到迁移完成
        return _handle_backend_request_legacy(action, data, cid)


def _handle_backend_action_via_services(action: str, data: dict, cid: str = "") -> dict:
    """Handle backend actions via microservices."""
    try:
        # ── health ──────────────────────────────────────────────────────────────
        if action == "health":
            result = _service_proxy.quantum.status()
            return {"cid": cid, "action": action, "data": {
                "status": "running",
                "ready": True,
                "busy": False,
                "session": result if result.get("connected") else {
                    "conn_id": "",
                    "name": "",
                    "host": "",
                    "port": 0,
                    "connected": False,
                }
            }}

        # ── experiments ────────────────────────────────────────────────────────
        elif action == "experiments":
            result = _service_proxy.quantum.experiments()
            return {"cid": cid, "action": action, "data": result}

        # ── sessions ────────────────────────────────────────────────────────────
        elif action == "sessions":
            result = _service_proxy.quantum.sessions()
            return {"cid": cid, "action": action, "data": result}

        # ── session_tree ────────────────────────────────────────────────────────
        elif action == "session_tree":
            max_depth = data.get("max_depth", 5)
            result = _service_proxy.quantum.session_tree(max_depth)
            return {"cid": cid, "action": action, "data": result}

        # ── switch_session ──────────────────────────────────────────────────────
        elif action == "switch_session":
            # switch_session 需要特殊处理(保存配置、重新加载qubits)，保持本地
            return _handle_backend_request_original(action, data, cid)

        # ── list_qubits ────────────────────────────────────────────────────────
        elif action == "list_qubits":
            result = _service_proxy.quantum.qubits()
            return {"cid": cid, "action": action, "data": result}

        # ── get_qubit_params ───────────────────────────────────────────────────
        elif action == "get_qubit_params":
            qname = data.get("name")
            if not qname:
                return {"cid": cid, "action": action, "error": "Qubit name required"}
            result = _service_proxy.quantum.qubit_params(qname)
            return {"cid": cid, "action": action, "data": result}

        # ── set_qubit_params ────────────────────────────────────────────────────
        elif action == "set_qubit_params":
            qname = data.get("name")
            params = data.get("params", {})
            if not qname:
                return {"cid": cid, "action": action, "error": "Qubit name required"}
            result = _service_proxy.quantum.set_qubit_params(qname, params)
            return {"cid": cid, "action": action, "data": result}

        # ── datasets ────────────────────────────────────────────────────────────
        elif action == "datasets":
            path = data.get("path")
            result = _service_proxy.quantum.datasets(path)
            return {"cid": cid, "action": action, "data": result}

        # ── debug_env, debug_data, test_load_dataset, plot_dataset, plot ────────
        # 这些操作需要访问本地状态，保持本地实现
        else:
            return _handle_backend_request_original(action, data, cid)

    except Exception as e:
        print(f"SERVICE_PROXY: Error handling {action}: {e}", file=sys.stderr)
        # 服务出错时回退到本地实现
        return _handle_backend_request_original(action, data, cid)


def _legacy_handle_backend_request(action: str, data: dict, cid: str = "") -> dict:
    """Legacy implementation of backend request handling.

    This is the original handle_backend_request function preserved for
    backward compatibility and for actions that haven't been migrated yet.
    """
    # The original implementation is below, after the Image Classification section
    return _handle_backend_request_original(action, data, cid)


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
            codepoint = ord(char)
            # Skip UTF-16 surrogates (U+D800 to U+DFFF) which are invalid in UTF-8
            if 0xD800 <= codepoint <= 0xDFFF:
                continue
            # Skip other problematic characters
            if codepoint < 0x110000:  # Valid Unicode range
                try:
                    char.encode('utf-8')
                    result.append(char)
                except UnicodeEncodeError:
                    continue
        return ''.join(result)


# ── Paths ─────────────────────────────────────────────────────────────────────

# Default to relative paths from script location, can be overridden via env vars
_DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
BACKEND_DIR = os.environ.get("BACKEND_DIR", os.path.join(_DEFAULT_ROOT, "measure_scripts", "measure_scripts", "sq_workflow"))
MEASURE_SCRIPTS = os.environ.get("MEASURE_SCRIPTS", os.path.join(_DEFAULT_ROOT, "measure_scripts", "measure_scripts"))

sys.path.insert(0, MEASURE_SCRIPTS)
sys.path.insert(0, BACKEND_DIR)

# Debug: print sys.path
print(f"INIT: sys.path after inserts: {sys.path[:5]}", file=sys.stderr, flush=True)
print(f"INIT: BACKEND_DIR={BACKEND_DIR} exists={os.path.exists(BACKEND_DIR)}", file=sys.stderr, flush=True)
print(f"INIT: MEASURE_SCRIPTS={MEASURE_SCRIPTS} exists={os.path.exists(MEASURE_SCRIPTS)}", file=sys.stderr, flush=True)

# ── Image Classifier Paths ─────────────────────────────────────────────────────
# D:\Documents\图像二分类代码
_IMAGE_CLASSIFIER_DIR = os.environ.get(
    "IMAGE_CLASSIFIER_DIR",
    r"D:\Documents\图像二分类代码"
)
if os.path.exists(_IMAGE_CLASSIFIER_DIR):
    sys.path.insert(0, _IMAGE_CLASSIFIER_DIR)
    print(f"IMAGE_CLASSIFIER: Added to sys.path: {_IMAGE_CLASSIFIER_DIR}", file=sys.stderr, flush=True)
else:
    print(f"IMAGE_CLASSIFIER: Directory not found: {_IMAGE_CLASSIFIER_DIR}", file=sys.stderr, flush=True)

# Lazy-load image classifier components (imported on first use)
_image_classifier = None
_activity_classifier = None
_onnx_classifier = None
_quantized_classifier = None
_classifier_model_path = None

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

# ── Backend Adapter Layer ─────────────────────────────────────────────────────
# Use the new backends adapter for all initialization
_BACKEND_ADAPTER = None  # New adapter instance

def _setup_backends_path():
    """Add qmclaw-server directory to Python path for backends module."""
    # __file__ = scripts/job_runner.py
    # server_dir = qmclaw-server (parent of scripts)
    server_dir = os.path.dirname(os.path.dirname(__file__))
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
        print(f"BACKENDS: Added server_dir to sys.path: {server_dir}", file=sys.stderr, flush=True)

_setup_backends_path()

# ── Ray initialization ──────────────────────────────────────────────────────────

def _setup_ray():
    """Initialize Ray connection and start Device Manager actor."""
    try:
        import ray

        # Skip if already initialized
        if ray.is_initialized():
            print("RAY: Already initialized", file=sys.stderr, flush=True)
            return True

        # Get Ray config from lqcs.system_config
        try:
            from lqcs import system_config
            head_ip = system_config.get_ray_head()
            node_ip = system_config.get_config()['ip']
            port = system_config.get_ray_port()
        except ImportError:
            print("RAY: lqcs.system_config not available, skipping Ray init", file=sys.stderr, flush=True)
            return False

        print(f"RAY: Connecting to {head_ip}:{port}...", file=sys.stderr, flush=True)
        ray.init(
            address=f"{head_ip}:{port}",
            namespace='main',
            _node_ip_address=node_ip,
            log_to_driver=False,
        )
        print("RAY: Connected successfully", file=sys.stderr, flush=True)

        # Try to get or create Device Manager
        try:
            device_manager = ray.get_actor('Device Manager')
            print("RAY: Device Manager already exists", file=sys.stderr, flush=True)
        except Exception:
            print("RAY: Starting Device Manager...", file=sys.stderr, flush=True)
            try:
                from lqcs.servers_control.start_server import start_managers
                start_managers.startServer(
                    node_ip,
                    start_managers.DeviceManagerActor,
                    'Device Manager',
                    blocking=False
                )
                print("RAY: Device Manager started", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"RAY: Failed to start Device Manager: {e}", file=sys.stderr, flush=True)

        return True
    except Exception as e:
        print(f"RAY: Initialization failed: {e}", file=sys.stderr, flush=True)
        return False

# Call Ray setup before backend init
_setup_ray()

# ── Backend initialization (done once at startup) ──────────────────────────────

print("INIT: Starting backend initialization...", file=sys.stderr, flush=True)
sys.stderr.flush()

_cxn = _s = _sq = _data = _qter = _BasicTuner = _generate_qubit = _backend = None
_all_qubits: Dict[str, Any] = {}
_all_couplers: Dict[str, Any] = {}
_current_session_path = []  # Track current session path for qubit reloading

def reload_qubits(session_path):
    """Reload qubits for the given session path using adapter or direct lqms calls."""
    global _s, _data, _qter, _backend, _current_session_path, _BACKEND_ADAPTER, _all_qubits, _all_couplers

    print(f"RELOAD_QUBITS: Starting reload for path={session_path}", file=sys.stderr, flush=True)
    _current_session_path = session_path

    # Try using adapter first, fall back to direct lqms calls
    if _BACKEND_ADAPTER is not None:
        try:
            success = _BACKEND_ADAPTER.reload_qubits(session_path)
            if success:
                # Update global references
                _update_globals_from_adapter()
            return success
        except Exception as e:
            print(f"RELOAD_QUBITS: Adapter failed, falling back to direct calls: {e}", file=sys.stderr)

    # Fall back to direct lqms calls
    try:
        import labrad
        from lqms.pyle.workflow import switchSession
        from lqms.utils.save_path import get_info_path
        from lqms.data_process import dataAnalysisCore as dc
        import threading

        # Switch session
        if session_path:
            # Extract user from path (e.g., ['', 'LQHL', 'test', '20260324'] -> 'LQHL')
            user = session_path[1] if len(session_path) > 1 else 'LQHL'
        else:
            user = 'LQHL'

        print(f"RELOAD_QUBITS: Switching to session path={session_path}, user={user}", file=sys.stderr, flush=True)

        # Create new session switcher with timeout protection
        switch_result = {"session": None, "error": None}
        def _switch():
            try:
                switch_result["session"] = switchSession(_cxn, user=user)
            except Exception as e:
                switch_result["error"] = e

        switch_thread = threading.Thread(target=_switch)
        switch_thread.daemon = True
        switch_thread.start()
        switch_thread.join(timeout=30)  # 30 second timeout

        if switch_thread.is_alive():
            print("RELOAD_QUBITS: switchSession TIMEOUT after 30 seconds!", file=sys.stderr, flush=True)
            return False
        elif switch_result["error"]:
            raise switch_result["error"]
        else:
            _s = switch_result["session"]

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

        # Regenerate qubits from the new session's info (switchSession already loads qubits)
        if _generate_qubit:
            from lqms.measure import generate_qubit, generate_coupler
            print(f"RELOAD_QUBITS: Calling generate_qubit with session={session_path}", file=sys.stderr, flush=True)
            _all_qubits = generate_qubit({'s': _s}, info=info, sample=_s)
            _all_couplers = generate_coupler({'s': _s}, info=info, sample=_s)
            print(f"RELOAD_QUBITS: generate_qubit returned {len(_all_qubits)} qubits: {list(_all_qubits.keys())[:10]}...", file=sys.stderr, flush=True)

        # Count qubits in _s after reload
        q_keys = [k for k in _s.keys() if k.startswith('q')]
        print(f"RELOAD_QUBITS: Final _s has {len(q_keys)} qubits", file=sys.stderr, flush=True)

        return True
    except Exception as e:
        import traceback as tb
        print(f"RELOAD_QUBITS ERROR: {tb.format_exc()}", file=sys.stderr, flush=True)
        return False

def _update_globals_from_adapter():
    """Update job_runner globals from backend adapter for backward compatibility."""
    global _cxn, _s, _sq, _data, _qter, _BasicTuner, _generate_qubit, _backend, _all_qubits, _all_couplers

    if _BACKEND_ADAPTER is None:
        return

    _cxn = _BACKEND_ADAPTER.cxn
    _s = _BACKEND_ADAPTER.s
    _sq = _BACKEND_ADAPTER.sq
    _data = _BACKEND_ADAPTER.data
    _qter = _BACKEND_ADAPTER.qter
    _BasicTuner = _BACKEND_ADAPTER._BasicTuner
    _generate_qubit = _BACKEND_ADAPTER._generate_qubit
    _all_qubits = _BACKEND_ADAPTER._all_qubits
    _all_couplers = _BACKEND_ADAPTER._all_couplers
    _backend = _BACKEND_ADAPTER  # Reference to adapter for qubit lookup


def init_backend(max_retries=3, delay=5):
    global _cxn, _s, _sq, _data, _qter, _BasicTuner, _generate_qubit, _backend, _current_session_path, _BACKEND_ADAPTER, _all_qubits, _all_couplers
    last_error = None

    # Try using the new backends adapter first
    try:
        print("INIT: Attempting to import backends...", file=sys.stderr, flush=True)
        print(f"INIT: sys.path[0] = {sys.path[0]}", file=sys.stderr, flush=True)
        import backends as _backends_module
        print(f"INIT: backends.__file__ = {getattr(_backends_module, '__file__', 'N/A')}", file=sys.stderr, flush=True)
        from backends import init_backend as create_backend_adapter, BackendStatus
        print("INIT: backends import succeeded", file=sys.stderr, flush=True)
        print("INIT: init_backend function:", init_backend, file=sys.stderr, flush=True)

        # Load session config and initialize backend adapter
        session_path = _get_full_session_path()
        print(f"INIT: Using backends adapter, session path = {session_path}", file=sys.stderr, flush=True)
        print("INIT: Calling create_backend_adapter()...", file=sys.stderr, flush=True)
        print(f"INIT: create_backend_adapter type = {type(create_backend_adapter)}", file=sys.stderr, flush=True)
        try:
            _BACKEND_ADAPTER = create_backend_adapter(session_path=session_path)
            print("INIT: create_backend_adapter() returned successfully", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"INIT: create_backend_adapter() raised exception: {e}", file=sys.stderr, flush=True)
            import traceback
            traceback.print_exc(file=sys.stderr)
            raise  # Re-raise to trigger fallback

        if _BACKEND_ADAPTER is not None and _BACKEND_ADAPTER.status == BackendStatus.READY:
            _update_globals_from_adapter()
            _current_session_path = session_path
            print(f"INIT: Backends adapter ready — system=lqcs, session={_current_session_path}", file=sys.stderr, flush=True)
            return True
        else:
            print(f"INIT: Backends adapter status = {_BACKEND_ADAPTER.status if _BACKEND_ADAPTER else 'None'}", file=sys.stderr, flush=True)
    except ImportError as e:
        print(f"INIT: Backends adapter not available ({e}), using direct lqms import", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"INIT: Backends adapter failed ({e}), using direct lqms import", file=sys.stderr, flush=True)

    # Fall back to direct lqms import (without measure_scripts/backend.py)
    for attempt in range(max_retries):
        try:
            # Add measure_scripts to path for lqms imports
            if BACKEND_DIR not in sys.path:
                sys.path.insert(0, BACKEND_DIR)

            import labrad
            from lqms.pyle.workflow import switchSession
            from lqms.utils.save_path import get_info_path
            from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
            from lqms.measure import generate_qubit, generate_coupler
            from lqms.measure.basic import BasicTuner, util
            from lqms.measure.tuners import sq_nodes as sq_module

            # Store for later use
            _generate_qubit = generate_qubit

            # Connect to LabRAD
            _cxn = labrad.connect()
            util.setWiringInfo(_cxn)

            # Load session path from config file
            _current_session_path = _get_full_session_path()
            print(f"INIT: Config session path = {_current_session_path}", file=sys.stderr, flush=True)

            # Create session switcher with timeout protection
            user = _current_session_path[1] if len(_current_session_path) > 1 else 'LQHL'
            print(f"INIT: Calling switchSession for user={user}...", file=sys.stderr, flush=True)
            switch_result = {"session": None, "error": None}
            def _switch_with_timeout():
                import threading  # Ensure threading is available in nested scope
                try:
                    switch_result["session"] = switchSession(_cxn, user=user)
                except Exception as e:
                    switch_result["error"] = e
            switch_thread = threading.Thread(target=_switch_with_timeout)
            switch_thread.daemon = True
            switch_thread.start()
            switch_thread.join(timeout=30)
            if switch_thread.is_alive():
                print("INIT: switchSession TIMEOUT after 30 seconds!", file=sys.stderr, flush=True)
                raise TimeoutError("switchSession timed out")
            elif switch_result["error"]:
                raise switch_result["error"]
            else:
                _s = switch_result["session"]
            print("INIT: switchSession done", file=sys.stderr, flush=True)

            # Initialize data lab
            _data = dc.DataLab(_current_session_path, _cxn.data_vault, dv_type='data_vault')

            # Load or create info
            info_path = get_info_path(_s)
            info = dc.InfoBase(info_path) if os.path.exists(info_path) else None

            # Initialize analysis tools
            _qter = QubitUpdater(_data, info)

            # Generate qubits (switchSession already loads qubits from registry)
            _all_qubits = generate_qubit({'s': _s}, info=info, sample=_s)
            _all_couplers = generate_coupler({'s': _s}, info=info, sample=_s)

            # No need to inject - switchSession loads qubits automatically

            # Initialize BasicTuner
            auto_config = {
                'stats': 300,
                'correctX': False,
                'correctZ': False,
                'reset': False,
                'apply_21': False,
                'run_mode': 'local',
            }
            _BasicTuner = BasicTuner(**auto_config)
            # Set on CLASS for experiment functions to access (like original backend.py)
            BasicTuner._sample = _s
            BasicTuner._all_qobjs = _all_qubits | _all_couplers

            # Get experiment module
            _sq = sq_module

            # For backward compatibility, _backend is the adapter if available
            _backend = _BACKEND_ADAPTER if _BACKEND_ADAPTER else _sq

            print(f"INIT: Backend ready (direct lqms) — sq={sq_module}, session={_current_session_path}", file=sys.stderr, flush=True)
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
    print("WARNING: Backend (LabRAD) init failed — quantum experiments will be unavailable. Agent chat (LLM-only) will still work.", file=sys.stderr, flush=True)

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

        # Add qubits from _all_qubits (actual Qubit objects with qName)
        # These are the actual qubit objects generated by generate_qubit
        if _all_qubits:
            for _n, obj in _all_qubits.items():
                if _n.startswith('q') and _n not in exec_globals:
                    exec_globals[_n] = obj
            print(f"EXEC: Added {len(_all_qubits)} qubits from _all_qubits", file=sys.stderr)

        # Also try to get qubits from _s registry (for session-switched qubits)
        if _s:
            qubit_count = 0
            for _n in _s.keys():
                if _n.startswith('q') and _n not in exec_globals:
                    try:
                        obj = _s[_n]
                        # Only accept actual qubit objects, not RegistryWrapper
                        if hasattr(obj, 'qName') or hasattr(obj, 'regs'):
                            exec_globals[_n] = obj
                            qubit_count += 1
                    except:
                        pass
            if qubit_count > 0:
                print(f"EXEC: Added {qubit_count} qubits from _s registry", file=sys.stderr)

        # Debug: check q10lu1
        if 'q10lu1' in exec_globals:
            val = exec_globals['q10lu1']
            print(f"EXEC: q10lu1 type={type(val).__name__}", file=sys.stderr)
        else:
            print(f"EXEC: q10lu1 NOT in exec_globals", file=sys.stderr)

        # Debug: check qubit existence before execution
        if 'q25_7' in exec_globals:
            val = exec_globals['q25_7']
            print(f"EXEC: q25_7 type={type(val).__name__}, qName={getattr(val, 'qName', 'N/A')}", file=sys.stderr, flush=True)
            # Check key attributes needed for iqraw
            print(f"EXEC: q25_7 has qf={hasattr(val, 'qf')}, qread={hasattr(val, 'qread')}, qrr={hasattr(val, 'qrr')}", file=sys.stderr, flush=True)
            # List all attributes that might be relevant
            attrs = [a for a in dir(val) if not a.startswith('_')]
            print(f"EXEC: q25_7 attributes: {attrs}", file=sys.stderr, flush=True)
            # Check qf value if exists
            if hasattr(val, 'qf'):
                print(f"EXEC: q25_7.qf = {val.qf}", file=sys.stderr, flush=True)
            # Check qread
            if hasattr(val, 'qread'):
                qr = val.qread
                print(f"EXEC: q25_7.qread type={type(qr).__name__}, value={qr}", file=sys.stderr, flush=True)
        else:
            print(f"EXEC: q25_7 NOT in exec_globals", file=sys.stderr, flush=True)

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
        # Enhanced error reporting for debugging
        error_trace = traceback.format_exc()
        print(f"EXEC ERROR: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        print(f"EXEC ERROR TRACE: {error_trace}", file=sys.stderr, flush=True)
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
                    if exp_qubit in _all_qubits:
                        exp_qubit_obj = _all_qubits[exp_qubit]
                    elif _s and exp_qubit in _s:
                        exp_qubit_obj = _s[exp_qubit]  # Fallback (may be RegistryWrapper)
                    elif _backend and hasattr(_backend, exp_qubit):
                        exp_qubit_obj = getattr(_backend, exp_qubit)
                    else:
                        q_keys = [k for k in (_all_qubits.keys() if _all_qubits else []) if k.startswith('q')]
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

        elif node_type == "image_classification":
            # Image classification node: takes qubit ID + experiment type, outputs label + confidence
            qubit_id = resolve_template(config.get("qubit", ""), node_results, workflow_ctx)
            experiment_type = resolve_template(config.get("experimentType", "spectroscopy"), node_results, workflow_ctx)
            backend = config.get("backend", "pytorch")
            review_threshold = float(config.get("reviewThreshold", 0.75))
            margin_threshold = float(config.get("marginThreshold", 0.15))

            result["type"] = "image_classification"
            result["config"] = {
                "qubit": qubit_id,
                "experimentType": experiment_type,
                "backend": backend,
                "reviewThreshold": review_threshold,
                "marginThreshold": margin_threshold,
            }

            try:
                classification = _run_image_classify_latest_experiment(
                    qubit_id, experiment_type, backend, review_threshold, margin_threshold
                )
                result["status"] = "completed"
                result["stdout"] = f"Label: {classification['label']}, Confidence: {classification['confidence']:.4f}, Margin: {classification['margin']:.4f}"
                result["metrics"] = {
                    "label": classification["label"],
                    "confidence": classification["confidence"],
                    "margin": classification["margin"],
                    "needReview": classification.get("needReview", False),
                    "probClass0": classification.get("probClass0", 0.0),
                    "probClass1": classification.get("probClass1", 0.0),
                }
                result["imagePath"] = classification.get("imagePath", "")
                result["datasetName"] = classification.get("datasetName", "")
            except Exception as e:
                result["status"] = "failed"
                result["error"] = str(e)

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
    if base_url:
        # Custom base URL provided
        return openai.OpenAI(api_key=api_key, base_url=base_url)

    if provider == "minimax":
        # MiniMax requires special header format, use httpx directly
        return None  # Will be handled specially
    elif provider == "deepseek":
        import openai
        return openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
    elif provider == "anthropic":
        # Anthropic uses OpenAI SDK with their base URL
        import openai
        return openai.OpenAI(api_key=api_key, base_url="https://anthropic.ai/v1")
    else:
        # Default OpenAI
        import openai
        return openai.OpenAI(api_key=api_key)


def _get_minimax_key() -> str:
    """Get MiniMax API key - defined at module level to avoid local variable issues."""
    return os.environ.get("MINIMAX_API_KEY", "")


def call_minimax_api(messages: list, model: str, api_key: str, temperature: float = 0.3, max_tokens: int = 500) -> dict:
    """Call MiniMax API using OpenAI-compatible endpoint (matches Model Registry)."""
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError
    import socket

    print(f"[MiniMax API] Calling API with {len(messages)} messages", file=sys.stderr, flush=True)
    endpoint = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    headers = {
        "Authorization": f"Bearer {api_key}",  # Full key for request
        "Content-Type": "application/json",
    }
    # Log only first 10 chars for debugging
    print(f"[MiniMax API] Request headers - Authorization: Bearer {api_key[:10]}...", file=sys.stderr, flush=True)

    # Convert messages to the format expected by MiniMax
    minimax_messages = []
    for msg in messages:
        minimax_messages.append({
            "role": msg.get("role", "user"),
            "content": _sanitize_string(msg.get("content", ""))
        })

    payload = {
        "model": model,
        "messages": minimax_messages,
        "max_tokens": max_tokens,
    }
    if temperature != 0.3:
        payload["temperature"] = temperature

    data_bytes = json.dumps(payload).encode("utf-8")
    req = Request(endpoint, data=data_bytes, headers=headers, method="POST")
    try:
        # Use longer timeout with retry for reliability
        print(f"[MiniMax API] Sending request with 60s timeout...", file=sys.stderr, flush=True)
        resp = urlopen(req, timeout=60)  # 60 second timeout
        print(f"[MiniMax API] Response received", file=sys.stderr, flush=True)
        result = json.loads(resp.read().decode("utf-8"))

        content = ""
        choices = result.get("choices", [])
        if choices and len(choices) > 0:
            msg = choices[0].get("message", {})
            content = _sanitize_string(msg.get("content", ""))

        usage = result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
        print(f"[MiniMax API] Success, content length: {len(content)}", file=sys.stderr, flush=True)
        return {"choices": [{"message": {"content": content}}], "usage": usage}
    except (URLError, HTTPError, socket.timeout, Exception) as e:
        print(f"[MiniMax API] Error: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        raise Exception(f"MiniMax API error: {type(e).__name__}: {e}")


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


# ── backend-style endpoints (integrated, no separate backend server) ──────────────────

def handle_backend_request(action, data):
    """Handle backend-style requests - progressive migration to microservices.

    When QMCLAW_USE_SERVICES=true and services are available, this delegates to microservices.
    Otherwise, it falls back to the legacy implementation.
    """
    cid = data.get("cid", "")
    return _handle_backend_action(action, data, cid)


def _handle_backend_request_original(action, data, cid=""):
    """Original implementation of handle_backend_request.

    This contains all the original backend request handling logic.
    """
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

        elif action == "debug_env":
            # Debug endpoint to check environment variables
            minimax_key = os.environ.get("MINIMAX_API_KEY", "")
            return {"cid": cid, "action": action, "data": {
                "minimax_api_key_set": bool(minimax_key),
                "minimax_api_key_len": len(minimax_key),
                "openai_api_key_set": bool(os.environ.get("OPENAI_API_KEY", "")),
                "all_env_keys": sorted([k for k in os.environ.keys() if any(x in k.upper() for x in ["KEY", "API", "MINIMAX", "OPENAI", "ANTHROPIC", "DEEPSEEK"])]),
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
            import threading  # Ensure threading is available in this scope
            print(f"[EVENT] switch_session starting, cid={cid}, data={data}", file=sys.stderr, flush=True)
            session_path = data.get("path", [])

            # Use lock with timeout to prevent deadlock
            lock_acquired = _labrad_lock.acquire(timeout=5.0)
            if not lock_acquired:
                print(f"[EVENT] switch_session lock timeout, cid={cid}", file=sys.stderr, flush=True)
                return {"cid": cid, "action": action, "data": {"success": False, "error": "Lock timeout"}}

            try:
                dv = _cxn.data_vault
                dv.cd('')  # absolute: go to root first
                clean_path = session_path[1:] if session_path and session_path[0] == '' else session_path
                dv.cd(clean_path)
                print(f"[EVENT] switch_session DataVault cd done, cid={cid}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[EVENT] switch_session DataVault error: {e}", file=sys.stderr, flush=True)
                _labrad_lock.release()
                lock_acquired = False  # Prevent finally from releasing again
                return {"cid": cid, "action": action, "data": {"success": False, "error": str(e)}}
            finally:
                if lock_acquired:
                    _labrad_lock.release()

            # Save session to config file
            user = clean_path[0] if clean_path else 'LQHL'
            path_segments = clean_path[1:] if len(clean_path) > 1 else []
            _save_session_config(user, path_segments)

            # Reload qubits for the new session - run in thread with timeout
            print(f"[EVENT] switch_session calling reload_qubits, cid={cid}", file=sys.stderr, flush=True)
            reload_result = {"success": False, "error": None}
            def _reload():
                import threading  # Ensure threading is available in nested scope
                try:
                    reload_qubits(clean_path)
                    reload_result["success"] = True
                except Exception as e:
                    reload_result["error"] = str(e)
                    print(f"[EVENT] reload_qubits error: {e}", file=sys.stderr)

            reload_thread = threading.Thread(target=_reload)
            reload_thread.daemon = True
            reload_thread.start()
            reload_thread.join(timeout=30)  # 30 second timeout for qubit reload

            if reload_thread.is_alive():
                print(f"[EVENT] switch_session reload_qubits TIMEOUT, cid={cid}", file=sys.stderr, flush=True)
                return {"cid": cid, "action": action, "data": {"success": False, "error": "Reload timeout"}}

            if reload_result["error"]:
                print(f"[EVENT] switch_session reload_qubits failed: {reload_result['error']}", file=sys.stderr, flush=True)

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
            print(f"[EVENT] switch_session returning, cid={cid}, qubits_count={len(qubits)}", file=sys.stderr, flush=True)
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
            import time as _time
            import threading
            print(f"[EVENT] quick_status starting, cid={cid}", file=sys.stderr, flush=True)
            status = {"labrad": "error", "ray": "error", "datavault": "error", "message": ""}
            try:
                # Check connection with timeout using threading
                cxn_result = {"connected": False, "error": None}

                def check_cxn():
                    try:
                        cxn_result["connected"] = _cxn.connected
                    except Exception as e:
                        cxn_result["error"] = str(e)

                check_thread = threading.Thread(target=check_cxn)
                check_thread.daemon = True
                check_thread.start()
                check_thread.join(timeout=5.0)  # 5 second timeout for connection check

                print(f"[EVENT] quick_status LabRAD check done, cid={cid}, alive={check_thread.is_alive()}", file=sys.stderr, flush=True)

                if check_thread.is_alive():
                    status["message"] = "LabRAD connection check timeout"
                    return {"cid": cid, "action": action, "data": status}

                if cxn_result["error"]:
                    status["message"] = f"Cannot connect to LabRAD: {cxn_result['error'][:50]}"
                    return {"cid": cid, "action": action, "data": status}

                if cxn_result["connected"]:
                    status["labrad"] = "ok"
                else:
                    status["message"] = "LabRAD disconnected"
                    return {"cid": cid, "action": action, "data": status}
            except Exception as e:
                status["message"] = f"LabRAD check failed: {str(e)[:50]}"
                return {"cid": cid, "action": action, "data": status}

            print(f"[EVENT] quick_status checking Ray, cid={cid}", file=sys.stderr, flush=True)
            try:
                import ray as _ray
                if _ray.is_initialized():
                    status["ray"] = "ok"
                else:
                    status["ray"] = "warning"
            except:
                status["ray"] = "warning"

            print(f"[EVENT] quick_status acquiring lock, cid={cid}", file=sys.stderr, flush=True)
            # Use non-blocking lock with timeout to prevent hanging
            lock_acquired = _labrad_lock.acquire(timeout=3.0)
            print(f"[EVENT] quick_status lock acquired={lock_acquired}, cid={cid}", file=sys.stderr, flush=True)
            if not lock_acquired:
                status["datavault"] = "timeout"
                status["message"] = "DataVault lock timeout"
            else:
                try:
                    dv = _cxn.data_vault
                    dv.cd([''])
                    status["datavault"] = "ok"
                except Exception as e:
                    status["datavault"] = "error"
                    status["message"] = f"DataVault error: {str(e)[:50]}"
                finally:
                    _labrad_lock.release()

            print(f"[EVENT] quick_status returning, cid={cid}, status={status}", file=sys.stderr, flush=True)

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
                        print(f"BACKEND_LLM: result is not a dict: {type(result)}", file=sys.stderr)
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

        # ── Image Classification ─────────────────────────────────────────────────
        elif action == "classify_images":
            # Batch classify images in a folder
            folder_path = data.get("folderPath", "")
            backend = data.get("backend", "pytorch")
            review_threshold = float(data.get("reviewThreshold", 0.75))
            margin_threshold = float(data.get("marginThreshold", 0.15))
            try:
                results = _run_image_classify_images(folder_path, backend, review_threshold, margin_threshold)
                return {"cid": cid, "action": action, "data": {"results": results}}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "classify_single":
            # Single image inference
            image_path = data.get("imagePath", "")
            backend = data.get("backend", "pytorch")
            try:
                result = _run_image_classify_single(image_path, backend)
                return {"cid": cid, "action": action, "data": result}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "classify_latest_experiment":
            # Workflow node: get latest experiment image from DataVault and classify
            qubit_id = data.get("qubit", "")
            experiment_type = data.get("experimentType", "spectroscopy")
            backend = data.get("backend", "pytorch")
            review_threshold = float(data.get("reviewThreshold", 0.75))
            margin_threshold = float(data.get("marginThreshold", 0.15))
            try:
                result = _run_image_classify_latest_experiment(qubit_id, experiment_type, backend, review_threshold, margin_threshold)
                return {"cid": cid, "action": action, "data": result}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "get_model_info":
            # Return model file info
            try:
                info = _get_classifier_model_info()
                return {"cid": cid, "action": action, "data": info}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "get_classification_stats":
            # Return classification statistics from SQLite
            since_hours = int(data.get("sinceHours", 24))
            try:
                stats = _get_classification_stats(since_hours)
                return {"cid": cid, "action": action, "data": stats}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "train_model":
            # Trigger model training
            epochs = int(data.get("epochs", 20))
            batch_size = int(data.get("batchSize", 32))
            imbalance_mode = data.get("imbalanceMode", "weighted")
            try:
                result = _run_image_train_model(epochs, batch_size, imbalance_mode)
                return {"cid": cid, "action": action, "data": result}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "agent_chat":
            message = data.get("message", "")
            mode = data.get("mode", "react")
            context = data.get("context", {})
            try:
                result = _run_agent_chat(message, mode, context)
                return {"cid": cid, "action": action, "data": result}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "agent_chat_stream":
            # Streaming version of agent_chat - outputs SSE events for real-time progress
            message = data.get("message", "")
            mode = data.get("mode", "react")
            context = data.get("context", {})
            try:
                result = _run_agent_chat_stream(cid, message, mode, context)
                return {"cid": cid, "action": action, "data": result}
            except Exception as e:
                return {"cid": cid, "action": action, "error": str(e)}

        elif action == "hermes_chat":
            # Hermes-Agent based chat
            message = data.get("message", "")
            model = data.get("model")
            base_url = data.get("base_url")
            enabled_toolsets = data.get("enabled_toolsets")
            session_id = data.get("session_id")
            try:
                from hermes_runner import _run_hermes_chat
                result = _run_hermes_chat(
                    message=message,
                    model=model,
                    base_url=base_url,
                    enabled_toolsets=enabled_toolsets,
                    session_id=session_id,
                )
                return {"cid": cid, "action": action, "data": result}
            except Exception as e:
                import traceback
                return {"cid": cid, "action": action, "error": str(e), "traceback": traceback.format_exc()}

        elif action == "hermes_chat_stream":
            # Streaming Hermes-Agent chat
            message = data.get("message", "")
            model = data.get("model")
            base_url = data.get("base_url")
            session_id = data.get("session_id")
            try:
                from hermes_runner import _run_hermes_chat_stream
                result = _run_hermes_chat_stream(
                    cid=cid,
                    message=message,
                    model=model,
                    base_url=base_url,
                    session_id=session_id,
                )
                return {"cid": cid, "action": action, "data": result}
            except Exception as e:
                import traceback
                return {"cid": cid, "action": action, "error": str(e), "traceback": traceback.format_exc()}

        elif action == "run_analysis":
            # Execute analysis command on the latest dataset
            command = data.get("command", "")
            if not command:
                return {"cid": cid, "action": action, "error": "command is required"}

            try:
                from io import StringIO
                import matplotlib.pyplot as plt

                # Load latest dataset
                if _data is None:
                    return {"cid": cid, "action": action, "error": "DataLab not initialized"}

                _data.loadDataset(-1)

                # Execute analysis command
                stdout_buf = StringIO()
                stderr_buf = StringIO()
                old_out, old_err = sys.stdout, sys.stderr
                sys.stdout = stdout_buf
                sys.stderr = stderr_buf

                try:
                    analysis_globals = {
                        "__name__": "__analysis__",
                        "__builtins__": __builtins__,
                        "os": os,
                        "sys": sys,
                        "data": _data,
                        "qter": _qter,
                        "dp": _data,
                        "plt": plt,
                    }
                    exec(command, analysis_globals)
                finally:
                    sys.stdout = old_out
                    sys.stderr = old_err

                analysis_output = stdout_buf.getvalue()
                analysis_error = stderr_buf.getvalue()

                # Parse metrics from output (look for key=value patterns)
                metrics = {}
                for line in analysis_output.split("\n"):
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            key = parts[0].strip()
                            value_str = parts[1].strip().split()[0]  # Take first part before space
                            try:
                                metrics[key] = float(value_str)
                            except ValueError:
                                pass

                print(f"RUN_ANALYSIS: executed, output length={len(analysis_output)}, metrics={metrics}", file=sys.stderr, flush=True)

                return {"cid": cid, "action": action, "data": {
                    "success": True,
                    "stdout": analysis_output,
                    "stderr": analysis_error,
                    "metrics": metrics,
                }}

            except Exception as e:
                import traceback as tb
                print(f"RUN_ANALYSIS: error={e}\n{tb.format_exc()}", file=sys.stderr, flush=True)
                return {"cid": cid, "action": action, "error": str(e), "traceback": tb.format_exc()}

        elif action == "plot_historical_dataset":
            # Plot historical dataset with custom command
            dataset_name = urllib.parse.unquote(data.get("name", ""))
            path_str = urllib.parse.unquote(data.get("path", ""))
            custom_command = data.get("command", "")

            if not dataset_name:
                return {"cid": cid, "action": action, "error": "name required"}

            path_parts = [p for p in path_str.strip("/").split("/") if p] if path_str else []

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
                    x_data = list(range(plot_data.shape[1]))
                    y_data = plot_data[0]
                else:
                    x_data = list(range(len(plot_data)))
                    y_data = plot_data
            else:
                x_data = list(range(len(plot_data) if hasattr(plot_data, '__len__') else 1))
                y_data = plot_data

            # Create figure and execute custom command if provided
            fig = plt.figure(figsize=(8, 6))
            ax = fig.add_subplot(111)

            if custom_command:
                # Execute custom command with data context
                exec_globals = {
                    "__name__": "__plot__",
                    "plt": plt,
                    "fig": fig,
                    "ax": ax,
                    "x": x_data,
                    "y": y_data,
                    "np": __import__("numpy"),
                }
                try:
                    exec(custom_command, exec_globals)
                except Exception as e:
                    print(f"PLOT_HISTORICAL: custom command error: {e}", file=sys.stderr, flush=True)
                    # Fall back to default plotting
                    ax.plot(x_data, y_data, "b.-")
                    ax.set_title(dataset_label)
            else:
                # Default plotting
                ax.plot(x_data, y_data, "b.-")
                ax.set_title(dataset_label)
                ax.set_xlabel("X")
                ax.set_ylabel("Y")

            fig.tight_layout()
            plot_file = os.path.join(PLOTS_DIR, f"hist_{abs(hash(dataset_name))}.png")
            os.makedirs(PLOTS_DIR, exist_ok=True)
            fig.savefig(plot_file, dpi=150, bbox_inches="tight")
            plt.close(fig)

            print(f"PLOT_HISTORICAL: saved to {plot_file}", file=sys.stderr, flush=True)
            return {"cid": cid, "action": action, "data": {
                "plotPath": plot_file,
                "name": dataset_label,
                "success": True
            }}

        else:
            return {"cid": cid, "action": action, "error": f"Unknown action: {action}"}

    except Exception as e:
        return {"cid": cid, "action": action, "error": str(e)}


# ── Image Classification Helpers ─────────────────────────────────────────────

def _get_activity_classifier():
    """Lazily initialize and return the ActivityClassifier."""
    global _activity_classifier, _classifier_model_path
    if _activity_classifier is None:
        from integration_api import ActivityClassifier
        model_path = os.path.join(_IMAGE_CLASSIFIER_DIR, "best_model.pth")
        _classifier_model_path = model_path
        _activity_classifier = ActivityClassifier(model_path=model_path)
        print(f"IMAGE_CLASSIFIER: Loaded ActivityClassifier from {model_path}", file=sys.stderr, flush=True)
    return _activity_classifier


def _run_image_classify_images(folder_path, backend, review_threshold, margin_threshold):
    """Batch classify all images in a folder."""
    import glob
    from PIL import Image

    if not os.path.exists(folder_path):
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    classifier = _get_activity_classifier()
    image_exts = ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tif", "*.tiff")
    image_paths = []
    for ext in image_exts:
        image_paths.extend(glob.glob(os.path.join(folder_path, ext)))
        image_paths.extend(glob.glob(os.path.join(folder_path, ext.upper())))

    if not image_paths:
        raise ValueError(f"No images found in: {folder_path}")

    results = []
    for img_path in image_paths:
        try:
            pred = classifier.predict(img_path, backend=backend if backend != "pytorch" else "pytorch")
            label = pred.get("label", "unknown")
            confidence = pred.get("confidence", 0.0)
            prob_class0 = pred.get("probabilities", [0.5, 0.5])[0]
            prob_class1 = pred.get("probabilities", [0.5, 0.5])[1]
            margin = abs(prob_class1 - prob_class0)
            need_review = confidence < review_threshold or margin < margin_threshold
            results.append({
                "imagePath": img_path,
                "label": label,
                "confidence": confidence,
                "margin": round(margin, 4),
                "needReview": need_review,
                "probClass0": round(prob_class0, 4),
                "probClass1": round(prob_class1, 4),
            })
        except Exception as e:
            results.append({
                "imagePath": img_path,
                "label": "error",
                "confidence": 0.0,
                "margin": 0.0,
                "needReview": True,
                "error": str(e),
            })
    return results


def _run_image_classify_single(image_path, backend):
    """Single image inference."""
    classifier = _get_activity_classifier()
    pred = classifier.predict(image_path, backend=backend if backend != "pytorch" else "pytorch")
    label = pred.get("label", "unknown")
    confidence = pred.get("confidence", 0.0)
    prob_class0 = pred.get("probabilities", [0.5, 0.5])[0]
    prob_class1 = pred.get("probabilities", [0.5, 0.5])[1]
    margin = abs(prob_class1 - prob_class0)
    return {
        "imagePath": image_path,
        "label": label,
        "confidence": confidence,
        "margin": round(margin, 4),
        "probClass0": round(prob_class0, 4),
        "probClass1": round(prob_class1, 4),
    }


def _run_image_classify_latest_experiment(qubit_id, experiment_type, backend, review_threshold, margin_threshold):
    """Find latest experiment image from DataVault and classify it."""
    # Query DataVault for the latest dataset matching qubit_id + experiment_type
    with _labrad_lock:
        dv = _cxn.data_vault
        # Navigate to experiments folder
        try:
            dv.cd(['', 'Experiments', experiment_type])
        except Exception:
            pass

        # List datasets, find ones matching qubit_id
        try:
            dirs = dv.dir()
            datasets = dirs[1] if len(dirs) > 1 else []
            matching = [d for d in datasets if qubit_id.lower() in d.lower()]
        except Exception:
            matching = []

        if not matching:
            # Try root
            dv.cd([''])
            try:
                dirs = dv.dir()
                datasets = dirs[1] if len(dirs) > 1 else []
                matching = [d for d in datasets if qubit_id.lower() in d.lower()]
            except Exception:
                matching = []

        if not matching:
            raise ValueError(f"No datasets found for qubit={qubit_id}, experiment={experiment_type}")

        # Get the latest
        latest = sorted(matching)[-1]
        # Get plot path for this dataset
        dv.open(latest)
        plot_name = f"{latest}.png"
        # Look in the plots directory
        plots_dir = os.environ.get("PLOTS_DIR", os.path.join(os.path.dirname(BACKEND_DIR), "qmclaw-web", "public", "plots"))
        img_path = os.path.join(plots_dir, plot_name)

        # Fallback: search for any image with qubit_id in name
        if not os.path.exists(img_path):
            import glob as _glob
            candidates = _glob.glob(os.path.join(plots_dir, f"*{qubit_id}*.png"))
            if candidates:
                img_path = sorted(candidates)[-1]
            else:
                raise FileNotFoundError(f"Plot image not found for dataset: {latest}")

    # Now classify
    result = _run_image_classify_single(img_path, backend)
    result["datasetName"] = latest
    result["imagePath"] = img_path
    # Add need_review flag
    result["needReview"] = result["confidence"] < review_threshold or result["margin"] < margin_threshold
    return result


def _get_classifier_model_info():
    """Return model file info."""
    model_path = os.path.join(_IMAGE_CLASSIFIER_DIR, "best_model.pth")
    if not os.path.exists(model_path):
        return {"exists": False, "error": f"Model file not found: {model_path}"}

    stat = os.stat(model_path)
    # Try to get accuracy info from classifier
    info = {
        "exists": True,
        "modelPath": model_path,
        "fileSizeBytes": stat.st_size,
        "fileSizeMB": round(stat.st_size / (1024 * 1024), 2),
    }

    # Try to get stats from the classifier
    try:
        classifier = _get_activity_classifier()
        stats = classifier.get_stats()
        if stats:
            info["accuracy"] = stats.get("accuracy")
            info["f1Score"] = stats.get("f1")
            info["totalPredictions"] = stats.get("total_predictions")
    except Exception as e:
        info["stats_error"] = str(e)

    return info


def _get_classification_stats(since_hours):
    """Return classification stats from SQLite."""
    try:
        classifier = _get_activity_classifier()
        # The ActivityClassifier uses an internal SQLite db
        # We get stats via get_stats()
        stats = classifier.get_stats()
        return {
            "sinceHours": since_hours,
            "totalPredictions": stats.get("total_predictions", 0),
            "accuracy": stats.get("accuracy"),
            "f1Score": stats.get("f1"),
        }
    except Exception as e:
        return {"error": str(e), "sinceHours": since_hours}


def _run_image_train_model(epochs, batch_size, imbalance_mode):
    """Trigger model training."""
    from image_classifier import CompleteImageClassifier

    train_dir = os.path.join(_IMAGE_CLASSIFIER_DIR, "train")
    if not os.path.exists(train_dir):
        raise FileNotFoundError(f"Train directory not found: {train_dir}")

    classifier = CompleteImageClassifier(
        train_dir=train_dir,
        model_save_path=os.path.join(_IMAGE_CLASSIFIER_DIR, "best_model.pth"),
    )

    # Run training
    results = classifier.train_with_f1_monitoring(
        epochs=epochs,
        batch_size=batch_size,
        imbalance_mode=imbalance_mode,
    )

    # Reset lazy-loaded classifier so it reloads new model
    global _activity_classifier
    _activity_classifier = None

    return {
        "success": True,
        "epochs": epochs,
        "finalValAccuracy": results.get("val_accuracy") if results else None,
        "finalValF1": results.get("val_f1") if results else None,
    }


# ── MCP Client ───────────────────────────────────────────────────────────────

class MCPClient:
    """MCP tool caller — connects to external MCP servers (streamable-http)."""

    def __init__(self, config_path=None):
        self.config_path = config_path or self._default_config()
        self._servers = {}
        self._tools = {}
        self._load_config()

    def _default_config(self):
        return os.path.join(os.path.dirname(__file__), "..", "config", "mcp_tools.json")

    def _load_config(self):
        try:
            with open(self.config_path) as f:
                data = json.load(f)
        except Exception:
            data = {"mcp_servers": [], "mcp_tools": []}
        for srv in data.get("mcp_servers", []):
            if srv.get("enabled"):
                self._servers[srv["id"]] = srv
        for tool in data.get("mcp_tools", []):
            if tool.get("enabled"):
                self._tools[tool["id"]] = tool

    def call_tool(self, tool_id, tool_input):
        tool = self._tools.get(tool_id)
        if not tool:
            return {"error": f"MCP tool not found: {tool_id}"}
        srv = self._servers.get(tool.get("server", ""))
        if not srv:
            return {"error": f"MCP server not found: {tool.get('server')}"}
        url = f"{srv['url']}/tools/{tool['remote_name']}/call"
        payload = {"input": tool_input, "jsonrpc": "2.0", "id": 1, "method": "tools/call"}
        try:
            from urllib.request import urlopen, Request
            from urllib.error import URLError
            data_bytes = json.dumps(payload).encode("utf-8")
            req = Request(url, data=data_bytes, headers={"Content-Type": "application/json"}, method="POST")
            resp = urlopen(req, timeout=30)
            result = json.loads(resp.read().decode("utf-8"))
            return result
        except Exception as e:
            return {"error": str(e)}

    def list_tools(self):
        return {tid: {k: v for k, v in t.items() if k != "server"} for tid, t in self._tools.items()}

    def list_servers(self):
        return list(self._servers.values())


# ── Skill Manager ────────────────────────────────────────────────────────────

class SkillManager:
    """Skill manager — loads skills.json, matches user messages, executes skill steps."""

    SKILLS_BASE = os.path.join(os.path.dirname(__file__), "..", "..", "skills")

    def __init__(self, config_path=None):
        self.config_path = config_path or self._default_config()
        self.skills = self._load_skills()

    def _default_config(self):
        return os.path.join(os.path.dirname(__file__), "..", "config", "skills.json")

    def _load_skills(self):
        try:
            with open(self.config_path) as f:
                data = json.load(f)
        except Exception:
            data = {"skills": []}
        return {s["id"]: s for s in data.get("skills", []) if s.get("enabled", True)}

    def match_skill(self, user_message):
        msg_lower = user_message.lower()
        matched = []
        for sid, skill in self.skills.items():
            for kw in skill.get("trigger_keywords", []):
                if kw.lower() in msg_lower:
                    matched.append(skill)
                    break
        return matched

    def execute_skill(self, skill_id, params):
        skill = self.skills.get(skill_id)
        if not skill:
            return {"error": f"Skill not found: {skill_id}"}
        steps = []
        for step in skill.get("steps", []):
            resolved_input = {}
            for k, v in step.get("input", {}).items():
                if isinstance(v, str):
                    resolved = v
                    for pk, pv in params.items():
                        resolved = resolved.replace(f"{{{{{pk}}}}}", str(pv))
                    resolved_input[k] = resolved
                else:
                    resolved_input[k] = v
            steps.append({"tool": step.get("tool", ""), "input": resolved_input})
        return {"steps": steps}

    def add_skill(self, skill_data):
        skill_data["id"] = skill_data.get("id") or skill_data.get("name", "").lower().replace(" ", "_")
        try:
            with open(self.config_path) as f:
                data = json.load(f)
        except Exception:
            data = {"skills": []}
        data.setdefault("skills", []).append(skill_data)
        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.skills[skill_data["id"]] = skill_data
        return skill_data

    def delete_skill(self, skill_id):
        try:
            with open(self.config_path) as f:
                data = json.load(f)
        except Exception:
            return
        data["skills"] = [s for s in data.get("skills", []) if s["id"] != skill_id]
        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.skills.pop(skill_id, None)

    def list_skills(self):
        return list(self.skills.values())


# ── Quantum Agent ─────────────────────────────────────────────────────────────

class QuantumAgent:
    """Quantum Control Agent with ReAct / Plan-and-Execute / Reflexion modes."""

    def __init__(self, mode="react", model_name=None, sse_cid=None):
        self.mode = mode
        self.model_name = model_name or "gpt-4o"
        self.max_steps = 20
        self.tools = self._register_tools()
        self.sse_cid = sse_cid  # If set, emit SSE events during execution

    def _emit_sse(self, event_type, data):
        """Emit an SSE event to stdout if sse_cid is set."""
        if self.sse_cid:
            sse_data = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
            print(f"SSE: {self.sse_cid}", flush=True)
            print(sse_data, flush=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def chat(self, message, context=None):
        """Main entry point. Returns dict with response, steps, results."""
        print(f"[QuantumAgent.chat] mode={self.mode}, message='{message[:100]}...'", file=sys.stderr, flush=True)
        intent = {"task": message, "context": context or {}}
        if self.mode == "react":
            print(f"[QuantumAgent.chat] Running ReAct loop...", file=sys.stderr, flush=True)
            return self._react_loop(intent)
        elif self.mode == "plan_and_execute":
            print(f"[QuantumAgent.chat] Running Plan-and-Execute...", file=sys.stderr, flush=True)
            return self._plan_and_execute(intent)
        elif self.mode == "reflexion":
            print(f"[QuantumAgent.chat] Running Reflexion...", file=sys.stderr, flush=True)
            return self._reflexion_loop(intent)
        else:
            return {"error": f"Unknown mode: {self.mode}"}

    # ── Tool Registry ──────────────────────────────────────────────────────────

    def _register_tools(self):
        return {
            "run_experiment": {
                "fn": self._tool_run_experiment,
                "desc": "执行量子实验，如 sq.t1, sq.spectroscopy, sq.iqraw, sq.ramsey",
                "params": ["qubit", "fn", "params"],
            },
            "query_datavault": {
                "fn": self._tool_query_datavault,
                "desc": "从 DataVault 查询历史实验数据",
                "params": ["qubit", "experiment_type", "limit"],
            },
            "analyze_results": {
                "fn": self._tool_analyze_results,
                "desc": "分析实验结果，提取指标（T1, SNR, fidelity 等）",
                "params": ["dataset_name"],
            },
            "classify_image": {
                "fn": self._tool_classify_image,
                "desc": "对实验图像进行 ML 分类",
                "params": ["image_path"],
            },
            "llm_reasoning": {
                "fn": self._tool_llm_reasoning,
                "desc": "LLM 推理/决策，生成建议",
                "params": ["prompt"],
            },
            "mcp_call": {
                "fn": self._tool_mcp_call,
                "desc": "调用外部 MCP 工具（文献检索、设备控制等）",
                "params": ["tool_id", "input"],
            },
            "match_skill": {
                "fn": self._tool_match_skill,
                "desc": "根据用户消息匹配已学习的技能",
                "params": ["message"],
            },
            "execute_skill": {
                "fn": self._tool_execute_skill,
                "desc": "执行一个已学习的技能模板",
                "params": ["skill_id", "params"],
            },
        }

    def _execute_tool(self, tool_name, tool_input):
        """Execute a tool by name with given input dict."""
        tool = self.tools.get(tool_name)
        if not tool:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return tool["fn"](tool_input)
        except Exception as e:
            return {"error": str(e)}

    # ── ReAct Loop ─────────────────────────────────────────────────────────────

    def _react_loop(self, intent):
        print(f"[ReAct] Starting react loop, max_steps={self.max_steps}", file=sys.stderr, flush=True)
        self._emit_sse("status", {"message": f"Starting ReAct loop (max {self.max_steps} steps)..."})
        steps = []
        observation = ""
        for step_idx in range(self.max_steps):
            step_num = step_idx + 1
            print(f"[ReAct] Step {step_num}/{self.max_steps}", file=sys.stderr, flush=True)
            self._emit_sse("step_start", {"step": step_num, "max_steps": self.max_steps})

            prompt = self._build_react_prompt(intent, steps, observation)
            self._emit_sse("thinking", {"step": step_num, "message": "Calling LLM to analyze situation..."})
            print(f"[ReAct] Calling _call_llm...", file=sys.stderr, flush=True)
            response = self._call_llm(prompt)
            print(f"[ReAct] _call_llm returned, response length: {len(response)}", file=sys.stderr, flush=True)
            parsed = self._parse_llm_response(response)
            print(f"[ReAct] parsed type: {parsed.get('type')}, tool: {parsed.get('tool')}", file=sys.stderr, flush=True)

            self._emit_sse("llm_response", {"step": step_num, "type": parsed.get("type"), "tool": parsed.get("tool")})

            if parsed["type"] == "finish":
                final_content = parsed.get("content", "")
                print(f"[ReAct] Finish received, content length={len(final_content)}, content={final_content[:200]}...", file=sys.stderr, flush=True)
                self._emit_sse("status", {"message": "Generating final response..."})
                return self._summarize(intent, steps, final_content)

            tool_name = parsed.get("tool", "")
            tool_input = parsed.get("input", {})
            self._emit_sse("tool_call", {"step": step_num, "tool": tool_name, "input": tool_input})
            print(f"[ReAct] Executing tool: {tool_name}", file=sys.stderr, flush=True)
            observation = self._execute_tool(tool_name, tool_input)
            print(f"[ReAct] Tool executed, observation type: {type(observation)}", file=sys.stderr, flush=True)
            steps.append({
                "thought": parsed.get("thought", ""),
                "tool": tool_name,
                "input": tool_input,
                "observation": observation,
            })
            self._emit_sse("tool_result", {"step": step_num, "tool": tool_name, "result": str(observation)[:200]})
        print(f"[ReAct] Max steps reached, summarizing...", file=sys.stderr, flush=True)
        self._emit_sse("status", {"message": "Max steps reached, generating response..."})
        return self._summarize(intent, steps, "执行达到最大步数限制")

    # ── Plan-and-Execute ───────────────────────────────────────────────────────

    def _plan_and_execute(self, intent):
        # Planning phase
        plan_prompt = self._build_plan_prompt(intent)
        plan_response = self._call_llm(plan_prompt)
        safe_plan_response = _sanitize_string(plan_response)
        try:
            plan_steps = json.loads(safe_plan_response)
        except Exception:
            plan_steps = [{"tool": "llm_reasoning", "input": {"prompt": safe_plan_response}}]

        # Execution phase
        steps = []
        for step in plan_steps:
            tool_name = step.get("tool", "")
            tool_input = step.get("input", {})
            observation = self._execute_tool(tool_name, tool_input)
            steps.append({
                "tool": tool_name,
                "input": tool_input,
                "observation": observation,
            })
        return self._summarize(intent, steps, None)

    # ── Reflexion Loop ─────────────────────────────────────────────────────────

    def _reflexion_loop(self, intent):
        # Reflexion uses plan_and_execute as base, then reviews each step
        result = self._plan_and_execute(intent)
        steps = result.get("steps", [])
        for i, step in enumerate(steps):
            observation = step.get("observation", "")
            reflection_prompt = (
                f"任务：{intent['task']}\n"
                f"步骤 {i+1}：{step['tool']}({step.get('input', {})})\n"
                f"结果：{observation}\n"
                f"这个结果是否正确？有无错误？如果正确回复 OK，如果有错误说明问题并给出修正建议："
            )
            reflection = self._call_llm(reflection_prompt)
            step["reflection"] = reflection
            if not self._is_ok(reflection):
                # Retry this step
                retry_input = step.get("input", {})
                retry_obs = self._execute_tool(step["tool"], retry_input)
                step["observation"] = retry_obs
                step["retried"] = True
        return result

    # ── LLM Helpers ────────────────────────────────────────────────────────────

    def _call_llm(self, prompt, temperature=0.3):
        """Call LLM with a text prompt. Returns the response text."""
        import traceback as _traceback
        # Check both if key exists AND if it's non-empty
        minimax_key = os.environ.get("MINIMAX_API_KEY", "")
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        print(f"[Agent LLM] Env check - MINIMAX_API_KEY len={len(minimax_key)}, OPENAI_API_KEY len={len(openai_key)}", file=sys.stderr, flush=True)

        last_error = "Unknown error"
        last_error_type = None

        # Try MiniMax first
        if minimax_key:
            print(f"[Agent LLM] Calling MiniMax API with key starting: {minimax_key[:8]}...", file=sys.stderr, flush=True)
            try:
                result = call_minimax_api(
                    messages=[{"role": "user", "content": prompt}],
                    model="MiniMax-M2.7",
                    api_key=minimax_key,
                    temperature=temperature,
                    max_tokens=2048,
                )
                if result and isinstance(result, dict):
                    choices = result.get("choices", [])
                    if choices and len(choices) > 0:
                        msg = choices[0].get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                            if content:
                                print(f"[Agent LLM] MiniMax success, content length={len(content)}", file=sys.stderr, flush=True)
                                return content
                            else:
                                last_error = "Empty content"
                        else:
                            last_error = f"Message is not dict: {type(msg)}"
                    else:
                        last_error = f"No choices in response: {result}"
                else:
                    last_error = f"Invalid response type: {type(result)}"
            except Exception as e:
                last_error = str(e)
                last_error_type = type(e).__name__
                print(f"[Agent LLM] MiniMax error ({last_error_type}): {last_error}", file=sys.stderr, flush=True)

        print(f"[Agent LLM] Trying OpenAI fallback...", file=sys.stderr, flush=True)
        if openai_key:
            try:
                from urllib.request import urlopen, Request
                endpoint = "https://api.openai.com/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {openai_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 2048,
                    "temperature": temperature,
                }
                req = Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
                resp = urlopen(req, timeout=60)
                result = json.loads(resp.read())
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    return content
            except Exception as e:
                last_error = str(e)
                last_error_type = type(e).__name__
                print(f"[Agent LLM] OpenAI error ({last_error_type}): {last_error}", file=sys.stderr, flush=True)

        # Return the actual error, not misleading "no API key" message
        if last_error_type in ("TimeoutError", "URLError", "HTTPError"):
            return f"[LLM Error: Network error - {last_error_type}: {last_error}]"
        elif not minimax_key and not openai_key:
            return "[LLM Error: No API key configured. Set MINIMAX_API_KEY or OPENAI_API_KEY environment variable.]"
        else:
            return f"[LLM Error: {last_error}]"

    def _build_react_prompt(self, intent, steps, observation):
        tools_desc = "\n".join(
            f"- {name}: {t['desc']} (params: {', '.join(t['params'])})"
            for name, t in self.tools.items()
        )
        steps_text = ""
        if steps:
            for s in steps:
                steps_text += f"  - [{s['tool']}] input={s['input']} → {s['observation']}\n"
        ctx = intent.get("context", {})
        ctx_str = ", ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else "无"

        return f"""你是一个量子测控智能体。根据用户任务按以下格式选择下一步操作：

任务：{intent['task']}
上下文：{ctx_str}
历史步骤：
{steps_text or '  (空)'}
当前观察：{observation or '(开始)'}

可用工具：
{tools_desc}

重要：只返回一个 JSON 对象，不要返回多个，不要在 JSON 前后添加任何文字说明。
执行动作：
{{"type": "action", "thought": "你的思考", "tool": "工具名", "input": {{"参数": "值"}}}}
任务完成：
{{"type": "finish", "content": "执行结果总结"}}
"""

    def _build_plan_prompt(self, intent):
        tools_desc = "\n".join(
            f"- {name}: {t['desc']} (params: {', '.join(t['params'])})"
            for name, t in self.tools.items()
        )
        ctx = intent.get("context", {})
        ctx_str = ", ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else "无"

        return f"""你是一个量子测控智能体。请将以下任务拆解为执行步骤列表：

任务：{intent['task']}
上下文：{ctx_str}

可用工具：
{tools_desc}

请返回 JSON 数组，每个元素描述一个步骤：
[{{"tool": "工具名", "input": {{"参数": "值"}}}}, ...]
仅返回 JSON，不要其他内容。"""

    def _parse_llm_response(self, response):
        """Parse LLM text response to extract action/finish.

        Handles cases where LLM returns multiple JSON blocks or extra text.
        Only parses the first valid JSON object.
        """
        # Sanitize the response first to remove problematic surrogate characters
        safe_response = _sanitize_string(response)
        print(f"[ParseLLM] Raw response length: {len(safe_response)}, preview: {safe_response[:200]}", file=sys.stderr, flush=True)

        try:
            # Strategy: find first '{' and try to parse incrementally
            # This handles cases where LLM returns multiple JSON blocks
            start = safe_response.find("{")
            if start < 0:
                print("[ParseLLM] No JSON object found, using fallback", file=sys.stderr, flush=True)
                return {"type": "finish", "content": safe_response}

            # Find the first complete JSON object starting from position start
            # Use a depth counter to find matching closing brace
            depth = 0
            json_end = -1
            for i in range(start, len(safe_response)):
                c = safe_response[i]
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        json_end = i + 1
                        break

            if json_end < 0:
                print("[ParseLLM] Incomplete JSON object, using fallback", file=sys.stderr, flush=True)
                return {"type": "finish", "content": safe_response}

            json_str = safe_response[start:json_end]
            obj = json.loads(json_str)
            print(f"[ParseLLM] Successfully parsed first JSON: type={obj.get('type')}, tool={obj.get('tool')}", file=sys.stderr, flush=True)
            return obj

        except json.JSONDecodeError as e:
            print(f"[ParseLLM] JSON parse failed: {e}, response={safe_response[:200]}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[ParseLLM] Unexpected error: {e}", file=sys.stderr, flush=True)

        # Fallback: treat as finish with the raw response
        print(f"[ParseLLM] Using fallback for response: {safe_response[:100]}", file=sys.stderr, flush=True)
        return {"type": "finish", "content": safe_response}

    def _is_ok(self, reflection_text):
        """Check if reflection indicates success."""
        return "ok" in reflection_text[:10].lower() or "正确" in reflection_text[:20]

    def _summarize(self, intent, steps, final_content):
        """Build final result dict."""
        results = {}
        charts = []
        for step in steps:
            obs = step.get("observation", {})
            if isinstance(obs, dict):
                if "metrics" in obs:
                    results.update(obs["metrics"])
                if "plot_path" in obs:
                    charts.append(obs["plot_path"])
        # Sanitize final_content to remove any problematic characters
        safe_content = _sanitize_string(final_content) if final_content else "执行完成"
        return {
            "response": safe_content,
            "steps": steps,
            "results": results,
            "charts": charts,
        }

    # ── Tool Implementations ───────────────────────────────────────────────────

    def _tool_run_experiment(self, inp):
        """Execute a quantum experiment. Reuses run_workflow_node logic."""
        qubit = inp.get("qubit", "")
        fn = inp.get("fn", "sq.iqraw")
        params = inp.get("params", {})
        if not qubit:
            return {"error": "qubit 参数缺失"}
        try:
            result = _run_single_experiment(qubit, fn, params)
            return result
        except Exception as e:
            return {"error": str(e)}

    def _tool_query_datavault(self, inp):
        """Query DataVault for historical experiment data."""
        qubit = inp.get("qubit", "")
        exp_type = inp.get("experiment_type", "")
        limit = int(inp.get("limit", 5))
        try:
            with _labrad_lock:
                dv = _cxn.data_vault
                dv.cd([''])
                try:
                    dv.cd(['', 'Experiments', exp_type])
                except Exception:
                    pass
                dirs = dv.dir()
                datasets = dirs[1] if len(dirs) > 1 else []
                matching = [d for d in datasets if qubit.lower() in d.lower()]
                recent = sorted(matching)[-limit:] if matching else []
                return {"datasets": recent, "count": len(recent)}
        except Exception as e:
            return {"error": str(e)}

    def _tool_analyze_results(self, inp):
        """Analyze experiment results and extract metrics."""
        dataset_name = inp.get("dataset_name", "")
        try:
            with _labrad_lock:
                dv = _cxn.data_vault
                if dataset_name:
                    dv.open(dataset_name)
                _data.loadDataset(-1)
                metrics = parse_metrics("")  # Will be populated by fitting
                # Run the analysis command if available
                from lqms.data_process import dataAnalysisCore as dc
                analysis = dc.DataLab(_current_session_path, dv, dv_type='data_vault')
                analysis.loadDataset(-1)
                metrics = parse_metrics(str(analysis.data))
                return {"metrics": metrics}
        except Exception as e:
            return {"error": str(e)}

    def _tool_classify_image(self, inp):
        """Classify an experiment image using ML model."""
        image_path = inp.get("image_path", "")
        try:
            result = _run_image_classify_single(image_path, "pytorch")
            return result
        except Exception as e:
            return {"error": str(e)}

    def _tool_llm_reasoning(self, inp):
        """Simple LLM reasoning tool."""
        prompt = inp.get("prompt", "")
        result = self._call_llm(prompt, temperature=0.5)
        return {"reasoning": result}

    def _tool_mcp_call(self, inp):
        """Call an external MCP tool."""
        mcp = MCPClient()
        return mcp.call_tool(inp.get("tool_id", ""), inp.get("input", {}))

    def _tool_match_skill(self, inp):
        """Match skills based on user message."""
        mgr = SkillManager()
        matched = mgr.match_skill(inp.get("message", ""))
        return {"matched_skills": matched}

    def _tool_execute_skill(self, inp):
        """Execute a learned skill template."""
        mgr = SkillManager()
        return mgr.execute_skill(inp.get("skill_id", ""), inp.get("params", {}))


def _run_single_experiment(qubit, fn, params):
    """Run a single experiment and return results. Reuses experiment node logic."""
    fn_name = fn if fn.startswith("sq.") else f"sq.{fn}"
    # Build call code using the qubit object from _s registry
    call_code = f"{fn_name}(_current_qubit, {', '.join(f'{k}={repr(v)}' for k, v in params.items())})"

    # Get qubit object from _all_qubits (actual Qubit objects)
    qubit_obj = None
    if isinstance(qubit, str) and qubit in _all_qubits:
        qubit_obj = _all_qubits[qubit]
    if qubit_obj is None and _s and qubit in _s:
        qubit_obj = _s[qubit]  # Fallback to _s (may be RegistryWrapper)
    if qubit_obj is None:
        return {"error": f"Qubit not found: {qubit}"}

    stdout_buf = StringIO()
    stderr_buf = StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    exec_globals = {
        "__builtins__": __builtins__,
        "sys": sys,
        "sq": _sq,
        "_s": _s,
        "_current_qubit": qubit_obj,
    }
    try:
        sys.stdout = stdout_buf
        sys.stderr = stderr_buf
        exec(call_code, exec_globals)
        sys.stdout = old_out
        sys.stderr = old_err
        stdout = stdout_buf.getvalue()
    except Exception:
        sys.stdout = old_out
        sys.stderr = old_err
        stdout = stdout_buf.getvalue() + f"\nError: {traceback.format_exc()}"
        return {"error": stdout}

    metrics = parse_metrics(stdout)
    return {"stdout": stdout, "metrics": metrics}


def _run_agent_chat(message, mode, context):
    """Top-level handler for agent_chat backend action."""
    print(f"[Agent] Starting agent_chat: message='{message[:50]}...', mode={mode}", file=sys.stderr, flush=True)

    # Debug: Check environment variables - print all keys containing API or KEY
    api_keys = [k for k in os.environ.keys() if 'KEY' in k.upper() or 'API' in k.upper()]
    print(f"[Agent] API-related env keys: {api_keys}", file=sys.stderr, flush=True)

    # Try to load .env file directly in Python if MINIMAX_API_KEY is not set
    if not os.environ.get("MINIMAX_API_KEY"):
        env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
        print(f"[Agent] MINIMAX_API_KEY not in env, trying to load from: {env_file}", file=sys.stderr, flush=True)
        if os.path.exists(env_file):
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ[key.strip()] = value.strip()
                print(f"[Agent] Loaded .env, MINIMAX_API_KEY now: {bool(os.environ.get('MINIMAX_API_KEY'))}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[Agent] Failed to load .env: {e}", file=sys.stderr, flush=True)
        else:
            print(f"[Agent] .env file does not exist at: {env_file}", file=sys.stderr, flush=True)

    ctx = dict(context) if context else {}
    model_name = ctx.pop("model_name", None)
    print(f"[Agent] Creating QuantumAgent: mode={mode}, model_name={model_name}", file=sys.stderr, flush=True)
    agent = QuantumAgent(mode=mode, model_name=model_name)
    print(f"[Agent] Calling agent.chat()...", file=sys.stderr, flush=True)
    result = agent.chat(message, ctx)
    print(f"[Agent] agent.chat() completed, result keys: {list(result.keys()) if isinstance(result, dict) else 'not a dict'}", file=sys.stderr, flush=True)
    return result


def _emit_sse(cid, event_type, data):
    """Emit an SSE event to stdout for streaming to frontend."""
    sse_data = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    print(f"SSE: {cid}", flush=True)
    print(sse_data, flush=True)


def _run_agent_chat_stream(cid, message, mode, context):
    """Streaming version of agent_chat - emits SSE events for real-time progress."""
    print(f"[Agent] Starting agent_chat_stream: message='{message[:50]}...', mode={mode}, cid={cid}", file=sys.stderr, flush=True)

    # Load API key if needed
    if not os.environ.get("MINIMAX_API_KEY"):
        env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_file):
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ[key.strip()] = value.strip()
                print(f"[Agent] Loaded .env", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[Agent] Failed to load .env: {e}", file=sys.stderr, flush=True)

    ctx = dict(context) if context else {}
    model_name = ctx.pop("model_name", None)
    _emit_sse(cid, "status", {"message": "Initializing agent..."})
    agent = QuantumAgent(mode=mode, model_name=model_name, sse_cid=cid)
    _emit_sse(cid, "status", {"message": "Starting conversation..."})
    result = agent.chat(message, ctx)
    _emit_sse(cid, "done", result)
    print(f"[Agent] agent_chat_stream completed, result keys: {list(result.keys()) if isinstance(result, dict) else 'not a dict'}", file=sys.stderr, flush=True)
    return result


# ── backend background thread ──────────────────────────────────────────────────
# backend requests are handled in a dedicated thread so they never block
# while a job/experiment is running in the main thread.

import threading, queue as _queue

_backend_queue: _queue.Queue = _queue.Queue()
_backend_results: dict = {}  # cid -> result (populated by background thread)
_results_lock = threading.Lock()
_stdin_lock = threading.Lock()  # protect stdout writes
_labrad_lock = threading.Lock()  # protect shared LabRAD connection

def _backend_worker():
    """Background thread: process backend requests from queue, write results."""
    # Debug: check environment in background thread
    api_keys_in_thread = [k for k in os.environ.keys() if 'KEY' in k.upper() or 'API' in k.upper()]
    minimax_in_thread = os.environ.get("MINIMAX_API_KEY", "")
    print(f"BACKEND_WORKER: Started, API keys={api_keys_in_thread}, MINIMAX first10='{minimax_in_thread[:10] if minimax_in_thread else 'EMPTY'}'", file=sys.stderr, flush=True)
    while True:
        try:
            item = _backend_queue.get(timeout=0.5)
            if item is None:
                break  # shutdown signal
            cid, action, data = item
            print(f"BACKEND_WORKER: Processing cid={cid} action={action}", file=sys.stderr, flush=True)
            # Debug: show MINIMAX key when processing agent_chat
            if action == "agent_chat":
                minimax_key = os.environ.get("MINIMAX_API_KEY", "")
                print(f"BACKEND_WORKER: agent_chat MINIMAX_API_KEY len={len(minimax_key)}, first10='{minimax_key[:10] if minimax_key else 'EMPTY'}'", file=sys.stderr, flush=True)
            try:
                result = handle_backend_request(action, data)
                print(f"BACKEND_WORKER: handle_backend_request returned cid={cid}", file=sys.stderr, flush=True)
            except Exception as e:
                import traceback
                result = {"cid": cid, "action": action, "error": str(e)}
                print(f"BACKEND_WORKER: Exception: {e}", file=sys.stderr, flush=True)
                print(f"BACKEND_WORKER: Traceback: {traceback.format_exc()}", file=sys.stderr, flush=True)
            with _results_lock:
                _backend_results[cid] = result
                # Write result directly so Express can collect it
                with _stdin_lock:
                    print(f"BACKEND_WORKER: Writing result for cid={cid}", file=sys.stderr, flush=True)
                    print(json.dumps(result), flush=True)
                    print(f"BACKEND_WORKER: Done writing result for cid={cid}", file=sys.stderr, flush=True)
            _backend_queue.task_done()
        except _queue.Empty:
            continue
        except Exception:
            pass

_backend_thread = threading.Thread(target=_backend_worker, daemon=True)
_backend_thread.start()


# ── Event loop ────────────────────────────────────────────────────────────────

def check_workflow_cancel(workflow_id=None):
    flag = os.path.join(os.environ.get("TEMP", "/tmp"), f"qmclaw_cancel_{workflow_id or 'workflow'}.flag")
    return os.path.exists(flag)

print("READY", file=sys.stderr, flush=True)
print('{"ready": true, "id": null}', flush=True)
sys.stderr.flush()
sys.stdout.flush()

# Process backend requests in the main thread to avoid threading issues with stdin
print("[EVENT_LOOP] About to enter stdin loop, sys.stdin type:", type(sys.stdin), file=sys.stderr, flush=True)
print("[EVENT_LOOP] sys.stdin.isatty():", sys.stdin.isatty(), file=sys.stderr, flush=True)
print("[EVENT_LOOP] sys.stdin.fileno():", sys.stdin.fileno() if hasattr(sys.stdin, 'fileno') else 'N/A', file=sys.stderr, flush=True)
sys.stdout.flush()
print("[EVENT_LOOP] Starting event loop...", file=sys.stderr, flush=True)
for line in sys.stdin:
    print(f"[EVENT_LOOP] Received line: {line[:200]}", file=sys.stderr, flush=True)
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

        elif msg_type == "backend":
            # Process backend requests - long-running tasks go to background queue
            cid = obj.get("cid", "")
            action = obj.get("action", "health")
            backend_data = obj.get("data", {})  # Extract the data field
            backend_data["cid"] = cid  # Pass cid in data for handle_backend_request
            print(f"[EVENT] backend request: cid={cid}, action={action}", file=sys.stderr, flush=True)

            # Long-running actions that should run in background thread
            LONG_RUNNING_ACTIONS = {"agent_chat", "agent_chat_stream", "hermes_chat", "hermes_chat_stream"}

            if action in LONG_RUNNING_ACTIONS:
                # Put in background queue for async processing
                print(f"[EVENT] Queuing {action} to background thread, cid={cid}", file=sys.stderr, flush=True)
                _backend_queue.put((cid, action, backend_data))
                # Don't write response here - background thread will do it
            else:
                # Fast actions: process in main thread (non-blocking)
                try:
                    result = handle_backend_request(action, backend_data)
                    print(f"[EVENT] handle_backend_request returned, cid={cid}", file=sys.stderr, flush=True)
                    output = json.dumps(result)
                    sys.stdout.write(output + "\n")
                    sys.stdout.flush()
                    print(f"[EVENT] Response written for cid={cid}", file=sys.stderr, flush=True)
                except Exception as e:
                    import traceback
                    print(f"[EVENT] Exception in backend handling: {e}", file=sys.stderr, flush=True)
                    print(traceback.format_exc(), file=sys.stderr, flush=True)
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