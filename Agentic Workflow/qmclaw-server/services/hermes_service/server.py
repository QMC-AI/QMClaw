"""
services/hermes_service/server.py - Hermes 微服务

提供 Hermes Agent 功能：
- 聊天对话
- 工具调用
- SSE 流式输出
- 会话管理
- 量子测控工具集成
"""

import json
import os
import sys
import time
import threading
import traceback
from typing import Any, Dict, List, Optional
from pathlib import Path
from io import StringIO

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import config

# 导入量子工具集
try:
    from .quantum_toolset import (
        QuantumMCPServer,
        create_quantum_toolset,
        setup_hermes_tools,
        QUANTUM_SERVICE_URL as HERMES_QUANTUM_SERVICE_URL,
    )
    QUANTUM_TOOLSET_AVAILABLE = True
except ImportError as e:
    QUANTUM_TOOLSET_AVAILABLE = False
    _safe_print(f"[hermes_service] Warning: Quantum toolset not available: {e}")


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[hermes_service] {msg}")


# ── Hermes Paths ──────────────────────────────────────────────────────────────

def _find_qmclaw_root():
    """Search upward for QMClaw root directory.

    From: D:\Documents\QMClaw\Agentic Workflow\qmclaw-server\services\hermes_service\server.py
    To:   D:\Documents\QMClaw  (5 levels up)
    """
    current = Path(__file__).parent  # hermes_service
    for _ in range(6):  # 5 levels up + 1 for safety
        vendor_path = current / "vendor" / "hermes-agent"
        if vendor_path.exists():
            return str(current)
        parent = current.parent
        if parent == current:  # Reached root
            break
        current = parent
    return None


_QMCLAW_ROOT = _find_qmclaw_root()
if not _QMCLAW_ROOT:
    # Fallback: assume standard layout
    # From: .../qmclaw-server/services/hermes_service/server.py
    # To:   D:\Documents\QMClaw
    _QMCLAW_ROOT = str(Path(__file__).parent.parent.parent.parent.parent)

_HERMES_AGENT_PATH = os.path.join(_QMCLAW_ROOT, "vendor", "hermes-agent")

# Load .env file for API keys
try:
    from dotenv import load_dotenv
    env_path = Path(_QMCLAW_ROOT) / "Agentic Workflow" / "qmclaw-server" / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
        _log(f"Loaded .env from {env_path}")
    else:
        _log(f"WARNING: .env not found at {env_path}")
except ImportError:
    _log("WARNING: python-dotenv not installed, API keys may not be loaded")

_log(f"Hermes paths initialized:")
_log(f"  _QMCLAW_ROOT: {_QMCLAW_ROOT}")
_log(f"  _HERMES_AGENT_PATH: {_HERMES_AGENT_PATH}")

# Debug: Check environment variables
_log(f"Environment check at startup:")
_log(f"  os.environ MINIMAX_API_KEY: {'***' if os.environ.get('MINIMAX_API_KEY') else '(not set)'}")

# Add hermes-agent to path
if os.path.exists(_HERMES_AGENT_PATH):
    if _HERMES_AGENT_PATH not in sys.path:
        sys.path.insert(0, _HERMES_AGENT_PATH)
    _log(f"Added hermes-agent to sys.path")
else:
    _log(f"WARNING: hermes-agent path not found: {_HERMES_AGENT_PATH}")

# ── Hermes Imports ──────────────────────────────────────────────────────────────

HERMES_AVAILABLE = False
AIAgent = None

try:
    from run_agent import AIAgent as _AIAgent
    AIAgent = _AIAgent
    HERMES_AVAILABLE = True
    _log("Successfully imported AIAgent from hermes-agent")
except ImportError as e:
    _log(f"Failed to import AIAgent: {e}")
    _log(f"Import traceback: {traceback.format_exc()}")


# ── Model Config ───────────────────────────────────────────────────────────────

def _load_model_configs() -> List[Dict[str, Any]]:
    """Load model configurations from model_configs.json."""
    config_dir = os.path.join(_QMCLAW_ROOT, "Agentic Workflow", "qmclaw-server", "config")
    config_file = os.path.join(config_dir, "model_configs.json")

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get("models", [])
    except Exception as e:
        _log(f"Failed to load model configs: {e}")
        return []


def _get_model_api_key(provider: str) -> str:
    """Get API key for a provider from environment."""
    _log(f"_get_model_api_key called with provider: '{provider}'")

    provider_key_map = {
        "minimax": "MINIMAX_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }

    # Normalize provider name
    provider_lower = provider.lower().strip()
    env_key = provider_key_map.get(provider_lower)

    _log(f"  provider_lower: '{provider_lower}', looking for env_key: '{env_key}'")

    if env_key:
        # Check os.environ directly (not via hermes's dotenv loader)
        api_key = os.environ.get(env_key, "")
        _log(f"  Direct os.environ[{env_key}]: {'***' if api_key else '(empty)'}")
        if api_key:
            return api_key

    # Also check if hermes-agent's env file has it
    try:
        from hermes_cli.config import get_env_value_prefer_dotenv
        if env_key:
            hermes_key = get_env_value_prefer_dotenv(env_key)
            _log(f"  hermes get_env_value_prefer_dotenv[{env_key}]: {'***' if hermes_key else '(empty)'}")
            if hermes_key:
                return hermes_key
    except Exception as e:
        _log(f"  hermes env lookup failed: {e}")

    _log(f"  No API key found for provider '{provider}'")
    return ""


# ── Session Manager ────────────────────────────────────────────────────────────

class SessionManager:
    """Manages Hermes Agent sessions."""

    def __init__(self):
        self._sessions: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, model_config: Dict[str, Any],
                      enabled_toolsets: List[str] = None,
                      disabled_toolsets: List[str] = None) -> Any:
        """Get existing session or create new one."""
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]

            # Create new agent
            api_key = _get_model_api_key(model_config.get("provider", ""))
            base_url = model_config.get("baseUrl", "")
            model = model_config.get("modelId", "")

            _log(f"Creating new session: {session_id}")
            _log(f"  model: {model}, provider: {model_config.get('provider')}")
            _log(f"  base_url: {base_url}, api_key_set: {bool(api_key)}")

            agent = AIAgent(
                base_url=base_url,
                api_key=api_key,
                model=model,
                session_id=session_id,
                enabled_toolsets=enabled_toolsets if enabled_toolsets else None,
                disabled_toolsets=disabled_toolsets if disabled_toolsets else ["terminal", "computer_use"],
                skip_memory=False,
                skip_background_review=True,
                quiet_mode=True,
            )

            self._sessions[session_id] = agent
            return agent

    def remove(self, session_id: str):
        """Remove a session."""
        with self._lock:
            if session_id in self._sessions:
                try:
                    self._sessions[session_id].close()
                except Exception:
                    pass
                del self._sessions[session_id]
                _log(f"Removed session: {session_id}")


# ── Hermes Service ──────────────────────────────────────────────────────────────

class HermesService(BaseService):
    """Hermes Agent 微服务

    提供完整的 Hermes Agent 功能，包括：
    - AI 对话
    - 工具调用
    - SSE 流式输出
    - 会话管理
    - 量子测控工具（通过 HTTP 调用 quantum_service）
    """

    def __init__(self, port: int = 3012):
        cfg = ServiceConfig(
            name="hermes_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        self._session_manager = SessionManager()
        self._available_models = []
        self._hermes_available = HERMES_AVAILABLE

        # 初始化量子工具注册中心
        self._quantum_registry = None
        self._quantum_service_url = HERMES_QUANTUM_SERVICE_URL

    def before_start(self):
        """启动前初始化"""
        _log("Initializing Hermes service...")

        if not self._hermes_available:
            _log("WARNING: Hermes-Agent (AIAgent) not available")
            return

        # Load available models
        self._available_models = _load_model_configs()
        _log(f"Loaded {len(self._available_models)} model configurations")

        enabled_count = sum(1 for m in self._available_models if m.get("enabled", False))
        _log(f"Enabled models: {enabled_count}")

        # 初始化量子工具注册中心
        if QUANTUM_TOOLSET_AVAILABLE:
            try:
                from ..common.quantum_tools import ToolRegistry
                self._quantum_registry = ToolRegistry()
                setup_hermes_tools(self._quantum_registry, self._quantum_service_url)
                _log(f"Quantum tools registered: {len(self._quantum_registry.list_tools())} tools")
            except Exception as e:
                _log(f"Warning: Failed to setup quantum tools: {e}")

    def handle_request(self, method: str, path: str, data: Dict[str, Any],
                      query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理请求"""
        # 量子工具 API
        if path.startswith("/quantum-tools"):
            return self._handle_quantum_tools(path, method, data)

        # 路由
        if path == "/health":
            return self._handle_health()
        elif path == "/models":
            return self._handle_models()
        elif path == "/chat":
            return self._handle_chat(data)
        elif path == "/chat/stream":
            # SSE streaming - return the handler function for streaming
            raise NotImplementedError("Use _handle_chat_stream for SSE streaming")
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_chat_stream(self, data: Dict[str, Any], write_fn) -> None:
        """处理流式聊天请求（SSE）

        Args:
            data: 请求数据
            write_fn: SSE 写入回调函数 (sse_data: str) => None
        """
        if not self._hermes_available:
            write_fn("event: error\ndata: " + json.dumps({"error": "Hermes-Agent not available"}) + "\n\n")
            return

        message = data.get("message")
        model = data.get("model")
        base_url = data.get("base_url")
        enabled_toolsets = data.get("enabled_toolsets", [])
        disabled_toolsets = data.get("disabled_toolsets", ["terminal", "computer_use"])
        session_id = data.get("session_id", f"hermes_{int(time.time() * 1000)}")

        if not message:
            write_fn("event: error\ndata: " + json.dumps({"error": "message is required"}) + "\n\n")
            return

        _log(f"Stream request: session={session_id}, model={model}")
        _log(f"  message: {message[:100]}...")

        try:
            # 解析模型配置（与 agent 侧同一套语义）
            model_config = self._resolve_model_config(model)

            if not model_config:
                write_fn("event: error\ndata: " + json.dumps({"error": "No enabled model found"}) + "\n\n")
                return

            # Override base_url if provided
            if base_url:
                model_config = dict(model_config)
                model_config["baseUrl"] = base_url

            # Send status
            write_fn("event: status\ndata: " + json.dumps({"message": "Initializing agent..."}) + "\n\n")

            # Get or create session
            agent = self._session_manager.get_or_create(
                session_id, model_config, enabled_toolsets, disabled_toolsets
            )

            write_fn("event: status\ndata: " + json.dumps({"message": "Running conversation..."}) + "\n\n")

            # Run conversation with streaming
            def on_thinking(content: str):
                write_fn("event: thinking\ndata: " + json.dumps({"content": content}) + "\n\n")

            def on_tool_call(tool_name: str, args: Dict):
                write_fn("event: tool_call\ndata: " + json.dumps({"tool": tool_name, "args": args}) + "\n\n")

            def on_response(content: str):
                write_fn("event: response\ndata: " + json.dumps({"content": content}) + "\n\n")

            # Configure callbacks if supported
            if hasattr(agent, 'thinking_callback'):
                agent.thinking_callback = on_thinking
            if hasattr(agent, 'tool_start_callback'):
                agent.tool_start_callback = on_tool_call
            if hasattr(agent, 'stream_delta_callback'):
                agent.stream_delta_callback = on_response

            # Run conversation
            result = agent.run_conversation(message)

            _log(f"Stream completed: completed={result.get('completed')}")

            # Send final result
            write_fn("event: done\ndata: " + json.dumps({
                "completed": result.get("completed", False),
                "final_response": result.get("final_response", ""),
                "error": result.get("error"),
            }) + "\n\n")

        except Exception as e:
            _log(f"Stream error: {e}\n{traceback.format_exc()}")
            write_fn("event: error\ndata: " + json.dumps({"error": str(e)}) + "\n\n")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy" if self._hermes_available else "degraded",
            "service": "hermes_service",
            "hermes_available": self._hermes_available,
            "model_count": len(self._available_models),
            "session_count": len(self._session_manager._sessions),
        }

    def _resolve_model_config(self, model: Optional[str]) -> Optional[Dict[str, Any]]:
        """按模型标识符解析模型配置。

        与 agent 侧保持**同一套语义**：前端两个面板传的都是 model_configs.json 里的 `id`
        （agent 走 context.model_id → llm_service；hermes 走 model → 这里）。

        匹配优先级：id → modelId → name。
        原来的实现只匹配 modelId/name，而前端传的是 id，只能靠"id 恰好等于 modelId"侥幸命中。
        都匹配不到时退回第一个 enabled 的模型（与原行为一致）。
        """
        if model:
            for key in ("id", "modelId", "name"):
                for m in self._available_models:
                    if m.get(key) == model:
                        _log(f"Model '{model}' resolved via '{key}' -> provider={m.get('provider')}, "
                             f"modelId={m.get('modelId')}")
                        return m
            _log(f"Model '{model}' not found in model_configs.json, falling back to first enabled")

        for m in self._available_models:
            if m.get("enabled", False):
                return m
        return None

    def _handle_models(self) -> Dict[str, Any]:
        """获取可用模型列表"""
        enabled_models = [m for m in self._available_models if m.get("enabled", False)]
        return {
            "models": enabled_models,
            "count": len(enabled_models),
        }

    def _handle_quantum_tools(self, path: str, method: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理量子工具请求"""
        import uuid

        if not QUANTUM_TOOLSET_AVAILABLE or self._quantum_registry is None:
            return {
                "success": False,
                "error": "Quantum toolset not available",
            }

        # /quantum-tools - 列出工具
        if path == "/quantum-tools" and method == "GET":
            tools = self._quantum_registry.list_tools()
            return {
                "success": True,
                "data": {
                    "name": "quantum",
                    "description": "Quantum measurement and control tools",
                    "tool_count": len(tools),
                    "tools": [f"qmclaw_{t}" for t in tools],
                },
            }

        # /quantum-tools/execute - 执行工具
        if path == "/quantum-tools/execute" and method == "POST":
            tool_name = data.get("tool")
            args = data.get("args", {})

            if not tool_name:
                return {
                    "success": False,
                    "error": "tool is required",
                }

            # 移除 qmclaw_ 前缀
            if tool_name.startswith("qmclaw_"):
                tool_name = tool_name[8:]

            result = self._quantum_registry.execute(tool_name, args)
            return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

        # /quantum-tools/execute - 执行工具
        if path == "/quantum-tools/execute" and method == "POST":
            from ..common.quantum_tools import ToolRegistry
            registry = ToolRegistry.get_instance()

        return {
            "success": False,
            "error": f"Unknown path: {path}",
        }

    def _handle_chat(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理聊天请求"""
        if not self._hermes_available:
            return {"error": "Hermes-Agent not available", "completed": False}

        message = data.get("message")
        model = data.get("model")
        base_url = data.get("base_url")
        enabled_toolsets = data.get("enabled_toolsets", [])
        disabled_toolsets = data.get("disabled_toolsets", ["terminal", "computer_use"])
        session_id = data.get("session_id", f"hermes_{int(time.time() * 1000)}")

        if not message:
            return {"error": "message is required", "completed": False}

        _log(f"Chat request: session={session_id}, model={model}")
        _log(f"  message: {message[:100]}...")

        try:
            # 解析模型配置（与 agent 侧同一套语义）
            model_config = self._resolve_model_config(model)

            if not model_config:
                return {"error": "No enabled model found", "completed": False}

            # Override base_url if provided
            if base_url:
                model_config = dict(model_config)
                model_config["baseUrl"] = base_url

            # Get or create session
            agent = self._session_manager.get_or_create(
                session_id, model_config, enabled_toolsets, disabled_toolsets
            )

            # Run conversation
            _log(f"Running conversation...")
            result = agent.run_conversation(message)

            _log(f"Conversation completed: completed={result.get('completed')}")

            return {
                "completed": result.get("completed", False),
                "final_response": result.get("final_response", ""),
                "messages": result.get("messages", []),
                "error": result.get("error"),
            }

        except Exception as e:
            _log(f"Chat error: {e}\n{traceback.format_exc()}")
            return {
                "error": str(e),
                "completed": False,
            }


def main():
    """主入口"""
    service = HermesService(port=3012)
    run_service(service)


if __name__ == "__main__":
    main()
