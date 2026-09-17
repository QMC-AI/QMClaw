"""
Hermes Agent Runner - Integration module for QMClaw.

This module provides the interface between the QMClaw backend and Hermes-Agent AIAgent.
It wraps AIAgent with QMClaw-specific quantum control tools.

Usage:
    from hermes_runner import HermesRunner
    runner = HermesRunner(model="anthropic/claude-sonnet-4.6", base_url="...")
    result = await runner.run("Run a qubit calibration")
"""

import os
import sys
import json
import asyncio
import logging
import threading
import traceback
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
from io import StringIO

logger = logging.getLogger(__name__)

# ── QMClaw Paths ──────────────────────────────────────────────────────────────
# Try to determine QMClaw root - go up from this file's location
# File: .../Agentic Workflow/qmclaw-server/scripts/hermes_runner.py
_THIS_FILE = os.path.abspath(__file__)
_THIS_DIR = os.path.dirname(_THIS_FILE)  # .../qmclaw-server/scripts
_SERVER_DIR = os.path.dirname(_THIS_DIR)  # .../qmclaw-server

# Fallback: search from CWD if __file__ doesn't resolve correctly
def _find_qmclaw_root(start_dir: str) -> str:
    """Search upward for QMClaw root (contains vendor/hermes-agent)."""
    current = start_dir
    for _ in range(10):  # Max 10 levels up
        vendor_path = os.path.join(current, "vendor", "hermes-agent")
        if os.path.exists(vendor_path):
            return current
        parent = os.path.dirname(current)
        if parent == current:  # Reached root
            break
        current = parent
    return None

# Try __file__ based calculation first
_QMCLAW_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))  # Up 3 levels from scripts/

# Verify and fallback if needed
vendor_test = os.path.join(_QMCLAW_ROOT, "vendor", "hermes-agent")
if not os.path.exists(vendor_test):
    # Try CWD-based search
    cwd_root = _find_qmclaw_root(os.getcwd())
    if cwd_root:
        _QMCLAW_ROOT = cwd_root
        print(f"[Hermes] Using CWD-based QMCLAW_ROOT: {_QMCLAW_ROOT}", file=sys.stderr, flush=True)
    else:
        print(f"[Hermes] WARNING: Could not find QMCLAW_ROOT, using file-based: {_QMCLAW_ROOT}", file=sys.stderr, flush=True)

CONFIG_DIR = os.environ.get("QMCLAW_CONFIG_DIR", os.path.join(_SERVER_DIR, "config"))
BACKEND_DIR = os.environ.get("BACKEND_DIR", os.path.join(os.path.dirname(os.path.dirname(_SERVER_DIR)), "measure_scripts", "measure_scripts", "sq_workflow"))
MEASURE_SCRIPTS = os.environ.get("MEASURE_SCRIPTS", os.path.join(os.path.dirname(os.path.dirname(_SERVER_DIR)), "measure_scripts", "measure_scripts"))

sys.path.insert(0, MEASURE_SCRIPTS)
sys.path.insert(0, BACKEND_DIR)

# ── Model Config Loader ────────────────────────────────────────────────────────

def load_model_configs() -> List[Dict[str, Any]]:
    """Load model configurations from model_configs.json."""
    config_file = os.path.join(CONFIG_DIR, "model_configs.json")
    print(f"[Hermes] Loading model configs from: {config_file}", file=sys.stderr, flush=True)
    print(f"[Hermes] CONFIG_DIR exists: {os.path.isdir(CONFIG_DIR)}", file=sys.stderr, flush=True)
    if os.path.isdir(CONFIG_DIR):
        print(f"[Hermes] Files in CONFIG_DIR: {os.listdir(CONFIG_DIR)[:10]}", file=sys.stderr, flush=True)
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("models", [])
    except Exception as e:
        print(f"[Hermes] Failed to load model configs: {e}", file=sys.stderr, flush=True)
        return []

def get_model_config(model_id: str = None, model_name: str = None) -> Optional[Dict[str, Any]]:
    """Get model config by id or name."""
    models = load_model_configs()
    for model in models:
        if not model.get("enabled", False):
            continue
        if model_id and model.get("id") == model_id:
            return model
        if model_name and model.get("name") == model_name:
            return model
    return None

def get_all_enabled_models() -> List[Dict[str, Any]]:
    """Get all enabled models from config."""
    models = load_model_configs()
    return [m for m in models if m.get("enabled", False)]

def get_model_api_key(provider: str) -> str:
    """Get API key for a provider."""
    provider_key_map = {
        "minimax": "MINIMAX_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    env_key = provider_key_map.get(provider.lower())
    if env_key:
        return os.environ.get(env_key, "")
    return ""

# ── Hermes Paths ──────────────────────────────────────────────────────────────
HERMES_AGENT_PATH = os.path.join(_QMCLAW_ROOT, "vendor", "hermes-agent")

print(f"[Hermes] Paths initialized:", file=sys.stderr, flush=True)
print(f"  _THIS_FILE: {_THIS_FILE}", file=sys.stderr, flush=True)
print(f"  _THIS_DIR: {_THIS_DIR}", file=sys.stderr, flush=True)
print(f"  _SERVER_DIR: {_SERVER_DIR}", file=sys.stderr, flush=True)
print(f"  _QMCLAW_ROOT: {_QMCLAW_ROOT}", file=sys.stderr, flush=True)
print(f"  HERMES_AGENT_PATH: {HERMES_AGENT_PATH}", file=sys.stderr, flush=True)
print(f"  HERMES_AGENT_PATH exists: {os.path.exists(HERMES_AGENT_PATH)}", file=sys.stderr, flush=True)

if os.path.exists(HERMES_AGENT_PATH):
    sys.path.insert(0, HERMES_AGENT_PATH)
    print(f"[Hermes] Added HERMES_AGENT_PATH to sys.path: {HERMES_AGENT_PATH}", file=sys.stderr, flush=True)
else:
    print(f"[Hermes] HERMES_AGENT_PATH does not exist: {HERMES_AGENT_PATH}", file=sys.stderr, flush=True)
    # List what's in vendor directory
    vendor_dir = os.path.join(_QMCLAW_ROOT, "vendor")
    if os.path.exists(vendor_dir):
        print(f"[Hermes] Contents of {vendor_dir}: {os.listdir(vendor_dir)}", file=sys.stderr, flush=True)

# Import Hermes Agent components - root-level modules (run_agent, model_tools, etc.)
# are available as top-level imports after adding HERMES_AGENT_PATH to sys.path
print(f"[Hermes] Attempting to import run_agent, sys.path[0]={sys.path[0] if sys.path else 'empty'}", file=sys.stderr, flush=True)
try:
    import run_agent
    print(f"[Hermes] Successfully imported run_agent: {run_agent}", file=sys.stderr, flush=True)
    from model_tools import get_tool_definitions, handle_function_call
    from toolsets import get_all_toolsets, get_toolset_info
    HERMES_AVAILABLE = True
    print(f"HERMES: Loaded from {HERMES_AGENT_PATH}", file=sys.stderr, flush=True)
except ImportError as e:
    HERMES_AVAILABLE = False
    print(f"HERMES: Not available - {e}", file=sys.stderr, flush=True)
    import traceback
    print(f"HERMES: Import traceback: {traceback.format_exc()}", file=sys.stderr, flush=True)

# ── Memory Provider ────────────────────────────────────────────────────────────

class QMClawMemoryProvider:
    """Session-isolated memory provider for QMClaw quantum experiments.

    This provider stores conversation history per session_id and provides
    context recall for relevant quantum control tasks.
    """

    pre_compress_checkpoint_api_version = 2  # v2 for fail-closed checkpoint

    def __init__(self):
        self._session_id = None
        self._hermes_home = None
        self._memory_store: Dict[str, List[Dict[str, Any]]] = {}  # session_id -> messages
        self._prefetch_cache: Dict[str, str] = {}  # session_id -> prefetch text
        self._initialized = False

    @property
    def name(self) -> str:
        return "qmclaw"

    def is_available(self) -> bool:
        """Always available since we manage our own storage."""
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        """Initialize memory provider for a session."""
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", os.path.expanduser("~/.qmclaw_hermes"))
        os.makedirs(self._hermes_home, exist_ok=True)

        # Load existing memory for this session
        self._load_session()
        self._initialized = True
        logger.info(f"QMClawMemoryProvider initialized for session: {session_id}")

    def _load_session(self) -> None:
        """Load memory from disk for this session."""
        if not self._session_id or not self._hermes_home:
            return
        mem_file = os.path.join(self._hermes_home, f"memory_{self._session_id}.json")
        try:
            if os.path.exists(mem_file):
                with open(mem_file, 'r', encoding='utf-8') as f:
                    self._memory_store[self._session_id] = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load memory: {e}")
            self._memory_store[self._session_id] = []

    def _save_session(self) -> None:
        """Save memory to disk for this session."""
        if not self._session_id or not self._hermes_home:
            return
        mem_file = os.path.join(self._hermes_home, f"memory_{self._session_id}.json")
        try:
            os.makedirs(self._hermes_home, exist_ok=True)
            with open(mem_file, 'w', encoding='utf-8') as f:
                json.dump(self._memory_store.get(self._session_id, []), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save memory: {e}")

    def unavailable_reason(self) -> str:
        return ""

    def system_prompt_block(self) -> str:
        """System prompt for quantum control context."""
        return """You are a quantum measurement and control assistant integrated with QMClaw.
You have access to quantum control tools through the sq.* function interface.
When users ask about qubit calibration, experiments, or measurements, use the available tools to help them."""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return relevant context from conversation history."""
        if not session_id:
            session_id = self._session_id or ""

        history = self._memory_store.get(session_id, [])
        if not history:
            return ""

        # Simple relevance: return recent turns related to the query
        relevant = []
        query_lower = query.lower()
        for msg in history[-10:]:  # Last 10 turns
            content = msg.get("content", "")
            if isinstance(content, str) and (query_lower in content.lower() or
                any(kw in content.lower() for kw in ["qubit", "calibration", "experiment", "measurement"])):
                relevant.append(content[:200])

        if relevant:
            return "Recent relevant context:\n" + "\n".join(f"- {r}" for r in relevant[:3])
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue background recall (no-op for in-memory storage)."""
        pass

    def recall_status(self):
        """Return recall status for indicator."""
        from agent.memory_provider import RecallStatus
        count = len(self._memory_store.get(self._session_id or "", []))
        return RecallStatus(provider_label=self.name, count=count)

    def sync_turn(self, user_content: str, assistant_content: str, *,
                  session_id: str = "", messages: Optional[List[Dict[str, Any]]] = None) -> None:
        """Persist a completed turn."""
        if not session_id:
            session_id = self._session_id or ""
        if not session_id:
            return

        if session_id not in self._memory_store:
            self._memory_store[session_id] = []

        self._memory_store[session_id].append({
            "timestamp": datetime.now().isoformat(),
            "user": user_content[:1000] if user_content else "",
            "assistant": assistant_content[:2000] if assistant_content else "",
        })

        # Keep only last 100 turns
        if len(self._memory_store[session_id]) > 100:
            self._memory_store[session_id] = self._memory_store[session_id][-100:]

        self._save_session()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas for quantum control functions."""
        return []  # Tools registered separately via Hermes tool system

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle memory-related tool calls."""
        raise NotImplementedError(f"QMClawMemoryProvider does not handle tool {tool_name}")

    def shutdown(self) -> None:
        """Clean shutdown - save any pending memory."""
        self._save_session()
        self._initialized = False

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Called at turn start."""
        pass

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Called at session end."""
        self._save_session()

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "",
                          reset: bool = False, rewound: bool = False, **kwargs) -> None:
        """Handle session switch."""
        if reset:
            self._memory_store[new_session_id] = []
        self._session_id = new_session_id
        self._load_session()

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Extract insights before compression."""
        if not messages:
            return ""

        # Extract key information about quantum experiments
        insights = []
        for msg in messages[-20:]:
            content = msg.get("content", "")
            if isinstance(content, str) and any(kw in content.lower() for kw in ["qubit", "calibration", "experiment"]):
                # Keep first 100 chars
                insights.append(content[:100])

        return " | ".join(insights[-5:]) if insights else ""

    def on_delegation(self, task: str, result: str, *, child_session_id: str = "", **kwargs) -> None:
        """Handle delegation observation."""
        pass

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """Return configuration schema."""
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Save configuration."""
        pass

    def on_memory_write(self, action: str, target: str, content: str,
                        metadata: Optional[Dict[str, Any]] = None) -> None:
        """Handle memory write event."""
        pass

    def backup_paths(self) -> List[str]:
        """Return backup paths outside hermes_home."""
        return []


# ── Tool Wrapper for QMClaw ───────────────────────────────────────────────────

class QMClawToolWrapper:
    """Wrapper to expose QMClaw quantum control functions as Hermes tools."""

    def __init__(self):
        self._sq = None
        self._s = None
        self._current_qubit = None
        self._all_qubits = {}

    def initialize(self):
        """Initialize QMClaw backend connection."""
        try:
            import labrad
            cxn = labrad.connect()
            self._s = cxn.servers
            # Import sq module
            import sq
            self._sq = sq

            # Get available qubits
            if hasattr(sq, 'get_qubits'):
                self._all_qubits = sq.get_qubits(cxn)
            elif hasattr(sq, 'qubits'):
                self._all_qubits = sq.qubits(cxn)

            logger.info(f"QMClawToolWrapper initialized with {len(self._all_qubits)} qubits")
            return True
        except Exception as e:
            logger.warning(f"QMClaw backend not available: {e}")
            return False

    def execute_sq_function(self, fn_name: str, qubit_name: str, **params) -> Dict[str, Any]:
        """Execute a sq.* quantum control function."""
        if not self._sq:
            return {"error": "QMClaw backend not initialized"}

        # Get qubit object
        qubit_obj = None
        if qubit_name in self._all_qubits:
            qubit_obj = self._all_qubits[qubit_name]
        elif self._current_qubit:
            qubit_obj = self._current_qubit

        if qubit_obj is None:
            return {"error": f"Qubit not found: {qubit_name}"}

        # Build function call
        full_fn_name = fn_name if fn_name.startswith("sq.") else f"sq.{fn_name}"

        try:
            # Get the function
            fn = self._sq
            for part in full_fn_name.split("."):
                fn = getattr(fn, part)

            # Execute
            stdout_buf = StringIO()
            stderr_buf = StringIO()
            old_out, old_err = sys.stdout, sys.stderr

            exec_globals = {
                "__builtins__": __builtins__,
                "sys": sys,
                "sq": self._sq,
                "_s": self._s,
                "_current_qubit": qubit_obj,
            }

            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            result = fn(qubit_obj, **params)
            sys.stdout = old_out
            sys.stderr = old_err

            stdout = stdout_buf.getvalue()
            return {"result": result, "stdout": stdout}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}


# ── Hermes Agent Runner ──────────────────────────────────────────────────────

class HermesRunner:
    """Runner for Hermes-Agent AIAgent with QMClaw quantum control tools."""

    def __init__(
        self,
        base_url: str = None,  # Use from config if not provided
        api_key: str = None,
        model: str = None,  # Will use first enabled model from config if not provided
        session_id: str = None,
        enabled_toolsets: List[str] = None,
        disabled_toolsets: List[str] = None,
        skip_memory: bool = False,
        skip_background_review: bool = True,
        **kwargs
    ):
        """Initialize Hermes runner.

        Args:
            base_url: API endpoint URL (from model config if not provided)
            api_key: API key (from model config/provider env if not provided)
            model: Model name or id (from model_configs.json if not provided)
            session_id: Unique session identifier
            enabled_toolsets: List of toolsets to enable
            disabled_toolsets: List of toolsets to disable
            skip_memory: Skip memory provider initialization
            skip_background_review: Skip background skill/memory review
        """
        # Load model config - find by model name, id, or use first enabled model
        if not model:
            # Use first enabled model from config
            enabled_models = get_all_enabled_models()
            if enabled_models:
                model_config = enabled_models[0]
            else:
                raise ValueError("No enabled models found in model_configs.json")
        else:
            # Try to find model by name or id
            model_config = get_model_config(model_name=model) or get_model_config(model_id=model)
            if not model_config:
                # Handle "provider/modelId" format (e.g., "minimax/MiniMax-M2.7")
                model_part = model.split("/")[-1] if "/" in model else model
                models = load_model_configs()
                for m in models:
                    if m.get("enabled"):
                        model_id_lower = m.get("modelId", "").lower()
                        name_lower = m.get("name", "").lower()
                        model_part_lower = model_part.lower()
                        # Match against modelId or name, or direct model string
                        if (model_id_lower == model_part_lower or
                            name_lower == model_part_lower or
                            model_id_lower == model.lower() or
                            name_lower == model.lower()):
                            model_config = m
                            break
            if not model_config:
                raise ValueError(f"Model not found or disabled: {model}")

        self.model = model_config.get("modelId", model)
        self.model_name = model_config.get("name", model)
        self.provider = model_config.get("provider", "")
        self.base_url = base_url or model_config.get("baseUrl", "")

        print(f"[Hermes] Model config loaded:", file=sys.stderr, flush=True)
        print(f"  model: {self.model}", file=sys.stderr, flush=True)
        print(f"  model_name: {self.model_name}", file=sys.stderr, flush=True)
        print(f"  provider: {self.provider}", file=sys.stderr, flush=True)
        print(f"  base_url: {self.base_url}", file=sys.stderr, flush=True)
        print(f"  api_key set: {bool(api_key)}", file=sys.stderr, flush=True)

        self.temperature = model_config.get("config", {}).get("temperature", 0.3)
        self.max_tokens = model_config.get("config", {}).get("maxTokens", 500)

        # Get API key from provider env if not provided
        if not api_key:
            api_key = get_model_api_key(self.provider)
        self.api_key = api_key

        print(f"[Hermes] API key status: provider={self.provider}, key_set={bool(self.api_key)}, env_var_checked={self.provider.upper()}_API_KEY", file=sys.stderr, flush=True)

        self.session_id = session_id or f"qmclaw_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Toolset configuration
        self.enabled_toolsets = enabled_toolsets or []
        self.disabled_toolsets = disabled_toolsets or ["terminal"]  # Disable terminal by default for safety

        # Skip memory and background review
        self.skip_memory = skip_memory
        self.skip_background_review = skip_background_review

        # Additional kwargs
        self.kwargs = kwargs

        # Initialize components
        self._agent = None
        self._memory_provider = QMClawMemoryProvider() if not skip_memory else None
        self._tool_wrapper = QMClawToolWrapper()
        self._initialized = False

    def _init_agent(self):
        """Initialize the AIAgent instance."""
        if self._initialized:
            return

        try:
            print(f"[Hermes] _init_agent: HERMES_AVAILABLE={HERMES_AVAILABLE}", file=sys.stderr, flush=True)
            print(f"[Hermes] _init_agent: sys.path[:3]={sys.path[:3]}", file=sys.stderr, flush=True)

            from run_agent import AIAgent

            print(f"[Hermes] _init_agent: AIAgent imported successfully", file=sys.stderr, flush=True)

            # Prepare kwargs for AIAgent
            agent_kwargs = {
                "base_url": self.base_url,
                "api_key": self.api_key,
                "model": self.model,
                "session_id": self.session_id,
                "enabled_toolsets": self.enabled_toolsets if self.enabled_toolsets else None,
                "disabled_toolsets": self.disabled_toolsets if self.disabled_toolsets else None,
                "skip_memory": self.skip_memory,
                "skip_background_review": self.skip_background_review,
                "quiet_mode": True,  # Suppress verbose output
                **self.kwargs
            }

            # Remove None values
            agent_kwargs = {k: v for k, v in agent_kwargs.items() if v is not None}

            self._agent = AIAgent(**agent_kwargs)
            self._initialized = True
            logger.info(f"HermesRunner initialized: model={self.model}, session={self.session_id}")

        except ImportError as e:
            logger.error(f"Failed to import AIAgent: {e}")
            raise RuntimeError(f"Hermes-Agent not available: {e}")

    def initialize_backend(self) -> bool:
        """Initialize QMClaw backend connection for quantum tools."""
        return self._tool_wrapper.initialize()

    @property
    def agent(self):
        """Get the AIAgent instance (lazy initialization)."""
        if not self._initialized:
            self._init_agent()
        return self._agent

    def run_sync(self, message: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Run a synchronous conversation.

        Args:
            message: User message
            context: Optional context dict

        Returns:
            Dict with keys: completed, final_response, messages, etc.
        """
        print(f"[Hermes] run_sync: initializing agent...", file=sys.stderr, flush=True)
        self._init_agent()
        print(f"[Hermes] run_sync: agent initialized, running conversation...", file=sys.stderr, flush=True)

        try:
            # Run conversation
            result = self.agent.run_conversation(message)
            print(f"[Hermes] run_sync: conversation completed, result type={type(result)}", file=sys.stderr, flush=True)
            return result
        except Exception as e:
            print(f"[Hermes] run_sync: Error running conversation: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            return {
                "completed": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }

    async def run_async(self, message: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Run an async conversation.

        Args:
            message: User message
            context: Optional context dict

        Returns:
            Dict with keys: completed, final_response, messages, etc.
        """
        return await asyncio.to_thread(self.run_sync, message, context)

    def run_with_streaming(
        self,
        message: str,
        on_thinking: Callable[[str], None] = None,
        on_tool_call: Callable[[str, Dict], None] = None,
        on_response: Callable[[str], None] = None,
    ) -> Dict[str, Any]:
        """Run conversation with streaming callbacks.

        Args:
            message: User message
            on_thinking: Called with thinking/reasoning content
            on_tool_call: Called with (tool_name, args) for each tool call
            on_response: Called with response chunks

        Returns:
            Dict with final result
        """
        self._init_agent()

        # Set up callbacks
        if on_thinking:
            self.agent.thinking_callback = on_thinking
        if on_tool_call:
            self.agent.tool_start_callback = lambda tool_name, args: on_tool_call(tool_name, args)
        if on_response:
            self.agent.stream_delta_callback = on_response

        return self.run_sync(message)

    def close(self):
        """Clean up resources."""
        if self._agent:
            self._agent.close()
            self._agent = None
        if self._memory_provider:
            self._memory_provider.shutdown()
            self._memory_provider = None
        self._initialized = False


# ── Backend Action Handlers ──────────────────────────────────────────────────

def _run_hermes_chat(message: str, model: str = None, base_url: str = None,
                     enabled_toolsets: List[str] = None, session_id: str = None) -> Dict[str, Any]:
    """Handle hermes_chat backend action.

    This is called from the backend worker thread.
    """
    print(f"[Hermes] Starting hermes_chat: message='{message[:50]}...', model={model}",
          file=sys.stderr, flush=True)

    if not HERMES_AVAILABLE:
        error_msg = "Hermes-Agent not available (import failed at module load time)"
        print(f"[Hermes] {error_msg}", file=sys.stderr, flush=True)
        return {"completed": False, "error": error_msg}

    try:
        # Create runner - model config is loaded from model_configs.json
        runner = HermesRunner(
            base_url=base_url,  # Use from config if None
            api_key=None,  # Auto-detect from provider
            model=model,  # Use from config if None (will pick first enabled)
            session_id=session_id,
            enabled_toolsets=enabled_toolsets,
            disabled_toolsets=["terminal", "computer_use"],  # Safety: disable dangerous tools
        )

        print(f"[Hermes] Runner initialized: model={runner.model}, provider={runner.provider}, base_url={runner.base_url}, api_key_set={bool(runner.api_key)}",
              file=sys.stderr, flush=True)

        # Run conversation
        result = runner.run_sync(message)

        # Clean up
        runner.close()

        print(f"[Hermes] hermes_chat completed: completed={result.get('completed')}",
              file=sys.stderr, flush=True)

        return result

    except Exception as e:
        error_msg = f"Hermes chat error: {e}"
        print(f"[Hermes] {error_msg}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        return {
            "completed": False,
            "error": error_msg,
            "traceback": traceback.format_exc(),
        }


def _run_hermes_chat_stream(cid: str, message: str, model: str = None,
                             base_url: str = None, session_id: str = None) -> Dict[str, Any]:
    """Handle streaming hermes_chat backend action.

    This emits SSE events to stdout for real-time streaming to frontend.
    """
    print(f"[Hermes] Starting hermes_chat_stream: message='{message[:50]}...', cid={cid}",
          file=sys.stderr, flush=True)

    if not HERMES_AVAILABLE:
        error_msg = "Hermes-Agent not available (import failed at module load time)"
        print(f"[Hermes] {error_msg}", file=sys.stderr, flush=True)
        return {"completed": False, "error": error_msg}

    def emit(event_type: str, data: Any):
        """Emit SSE event with CID prefix for Express routing.

        Format: "SSE: {cid} | event: {type}\ndata: {json}\n\n"
        The "|" separates CID from SSE body for Express parsing.
        """
        json_data = json.dumps(data, ensure_ascii=False)
        # Use "|" as separator between CID and SSE body
        sys.stdout.write(f"SSE: {cid} | event: {event_type}\ndata: {json_data}\n\n")
        sys.stdout.flush()
        print(f"[Hermes] SSE emitted: {event_type}", file=sys.stderr, flush=True)

    try:
        # Create runner - model config is loaded from model_configs.json
        # No hardcoded model default - let HermesRunner pick from config if model is None
        runner = HermesRunner(
            base_url=base_url,  # Use from config if None
            api_key=None,  # Auto-detect from provider
            model=model,  # Use from config if None (will pick first enabled)
            session_id=session_id,
            disabled_toolsets=["terminal", "computer_use"],  # Safety
        )

        print(f"[Hermes] Runner initialized: model={runner.model}, provider={runner.provider}, base_url={runner.base_url}, api_key_set={bool(runner.api_key)}",
              file=sys.stderr, flush=True)

        emit("status", {"message": "Initializing Hermes agent..."})

        # Run with streaming callbacks
        def on_thinking(content: str):
            print(f"[Hermes] SSE: thinking callback fired: {content[:100]}...", file=sys.stderr, flush=True)
            try:
                emit("thinking", {"content": content})
                print(f"[Hermes] SSE: thinking event sent", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[Hermes] SSE: thinking emit error: {e}", file=sys.stderr, flush=True)

        def on_tool_call(tool_name: str, args: Dict):
            print(f"[Hermes] SSE: tool_call callback fired: {tool_name}", file=sys.stderr, flush=True)
            try:
                emit("tool_call", {"tool": tool_name, "args": args})
                print(f"[Hermes] SSE: tool_call event sent", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[Hermes] SSE: tool_call emit error: {e}", file=sys.stderr, flush=True)

        def on_response(content: str):
            print(f"[Hermes] SSE: response callback fired: {content[:100]}...", file=sys.stderr, flush=True)
            try:
                emit("response", {"content": content})
                print(f"[Hermes] SSE: response event sent", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[Hermes] SSE: response emit error: {e}", file=sys.stderr, flush=True)

        print(f"[Hermes] Starting run_with_streaming with callbacks set", file=sys.stderr, flush=True)
        try:
            final_result = runner.run_with_streaming(
                message,
                on_thinking=on_thinking,
                on_tool_call=on_tool_call,
                on_response=on_response,
            )
            print(f"[Hermes] run_with_streaming returned: {type(final_result)}, keys={final_result.keys() if isinstance(final_result, dict) else 'N/A'}", file=sys.stderr, flush=True)
            print(f"[Hermes] final_response preview: {str(final_result.get('final_response', ''))[:200]}", file=sys.stderr, flush=True)
            print(f"[Hermes] failed={final_result.get('failed')}, error={final_result.get('error', 'none')[:100] if final_result.get('error') else 'none'}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[Hermes] run_with_streaming exception: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            final_result = {"completed": False, "error": str(e), "traceback": traceback.format_exc()}

        emit("done", final_result)

        # Clean up
        runner.close()

        print(f"[Hermes] hermes_chat_stream completed: cid={cid}", file=sys.stderr, flush=True)

        return final_result

    except Exception as e:
        error_msg = f"Hermes streaming error: {e}"
        print(f"[Hermes] {error_msg}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
        emit("error", {"error": error_msg})
        return {
            "completed": False,
            "error": error_msg,
        }


# ── Module Test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Simple test
    print("Testing HermesRunner...")

    # Set API key for testing
    os.environ["MINIMAX_API_KEY"] = os.environ.get("MINIMAX_API_KEY", "test-key")

    try:
        # Create runner - will use first enabled model from model_configs.json
        runner = HermesRunner(
            model=None,  # Let config decide
            disabled_toolsets=["terminal", "computer_use"],
        )
        print(f"HermesRunner created: session_id={runner.session_id}")
        print(f"  model: {runner.model}")
        print(f"  provider: {runner.provider}")
        print(f"  base_url: {runner.base_url}")
        print(f"  api_key_set: {bool(runner.api_key)}")
        print("Module test passed!")
    except Exception as e:
        print(f"HermesRunner creation failed: {e}")
        import traceback
        traceback.print_exc()
