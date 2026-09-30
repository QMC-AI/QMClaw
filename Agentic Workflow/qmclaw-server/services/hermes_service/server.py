"""
services/hermes_service/server.py - Hermes 微服务（完整 CLI 功能）

提供 Hermes Agent 功能：
- 聊天对话
- 工具调用（包括 Terminal）
- SSE 流式输出
- 会话管理（SQLite 持久化）
- Approval 确认（WebSocket）
- 量子测控工具集成

安全措施：
- Terminal 工作目录限制在项目目录内
- 所有危险操作需要 Approval 确认
- 120 秒 Approval 超时
"""

import json
import logging
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

# 导入 WebSocket Approval 管理器
from .approval_ws import WebSocketApprovalManager, APPROVAL_TIMEOUT

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

# 导入记忆、Skills、Cron 模块
from . import memory as memory_module
from . import skills as skills_module
from . import cron as cron_module


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[hermes_service] {msg}")


def _handle_tool_progress(emit_ws_event, *args, **kwargs):
    """处理工具进度回调 - 发送详细工具执行日志到前端

    Hermes 传递的参数:
    - "tool.started": event_type, function_name, preview, args
    - "tool.completed": event_type, function_name, None, None, duration=..., is_error=..., result=...
    """
    try:
        event_type = args[0] if args else kwargs.get('event_type', '')

        if event_type == "tool.started":
            # 工具开始执行
            function_name = args[1] if len(args) > 1 else kwargs.get('function_name', '')
            preview = args[2] if len(args) > 2 else kwargs.get('preview', '')
            tool_args = args[3] if len(args) > 3 else kwargs.get('args', {})

            emit_ws_event("tool_log", {
                "type": "start",
                "name": function_name,
                "preview": preview,
                "args": tool_args,
            })

        elif event_type == "tool.completed":
            # 工具执行完成
            function_name = args[1] if len(args) > 1 else kwargs.get('function_name', '')
            duration = kwargs.get('duration')
            is_error = kwargs.get('is_error', False)
            result = kwargs.get('result')

            # 简化结果预览（增加长度以便显示更多调试信息）
            result_preview = ""
            if result:
                result_str = str(result)
                result_preview = result_str[:500] + "..." if len(result_str) > 500 else result_str

            emit_ws_event("tool_log", {
                "type": "completed",
                "name": function_name,
                "duration": duration,
                "is_error": is_error,
                "result_preview": result_preview,
            })

    except Exception as e:
        _log(f"tool_progress_callback error: {e}")


# ── Image Display System Prompt ───────────────────────────────────────────────

IMAGE_DISPLAY_SYSTEM_PROMPT = """IMPORTANT: Image Display Guidelines

When returning image data in tool results, the frontend supports these JSON formats ONLY:

1. BASE64 FORMAT (preferred):
   {"success": true, "image": "data:image/png;base64,<base64_data>"}

2. FILE URL FORMAT:
   {"success": true, "image_url": "/plots/<filename>.png"}
   or {"success": true, "plotUrl": "/plots/<filename>.png"}

3. NESTED FORMAT:
   {"success": true, "data": {"image": "data:image/png;base64,..."}}

CRITICAL RULES:
- Do NOT use Markdown image syntax like ![](url) or ![alt](url) in text
- The frontend CANNOT parse images from Markdown or plain text
- Images are ONLY extracted from tool result JSON fields: image, image_url, plotUrl, data.image
- If returning a file path URL, ensure the file exists at that path
- Always return images as part of the tool result JSON, NOT as Markdown in the response text
"""


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


# 需要 asyncio 来创建协程（在所有代码之前导入）
import asyncio

# ── Session Manager ────────────────────────────────────────────────────────────

# 项目根目录（Terminal 限制在此目录内）
# 从 services/hermes_service/server.py 到 d:/Documents/QMClaw 需要向上5层
PROJECT_ROOT = Path(__file__).parents[4]


class SessionManager:
    """Manages Hermes Agent sessions with full CLI features."""

    def __init__(self, ws_manager: WebSocketApprovalManager, hermes_config: dict):
        self._sessions: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._ws_manager = ws_manager
        self._hermes_config = hermes_config

    def get_or_create(self, session_id: str, model_config: Dict[str, Any],
                      enabled_toolsets: List[str] = None,
                      disabled_toolsets: List[str] = None) -> Any:
        """Get existing session or create new one with full CLI features."""
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]

            # Create new agent with full CLI features
            api_key = _get_model_api_key(model_config.get("provider", ""))
            base_url = model_config.get("baseUrl", "")
            model = model_config.get("modelId", "")

            _log(f"Creating new session: {session_id}")
            _log(f"  model: {model}, provider: {model_config.get('provider')}")
            _log(f"  base_url: {base_url}, api_key_set: {bool(api_key)}")
            _log(f"  project_root: {PROJECT_ROOT}")

            # Get full toolsets from config if not specified
            toolsets_list = enabled_toolsets if enabled_toolsets else self._hermes_config.get("toolsets", [])

            # 添加 QMClaw 自定义工具集
            qmclaw_toolsets = ["quantum", "analysis", "workflow"]
            for ts in qmclaw_toolsets:
                if ts not in toolsets_list:
                    toolsets_list = list(toolsets_list) + [ts]
            _log(f"  toolsets (with QMClaw): {toolsets_list}")

            # Create async approval handler
            async def approval_callback(command: str, description: str) -> str:
                """Approval callback - requests user confirmation via WebSocket."""
                return await self._ws_manager.request_approval(
                    session_id=session_id,
                    command=command,
                    description=description,
                )

            # Create clarify callback
            async def clarify_callback(question: str, choices=None, multi_select=False) -> str:
                """Clarify callback - requests user clarification via WebSocket."""
                return await self._ws_manager.handle_clarify(
                    session_id=session_id,
                    question=question,
                    choices=choices,
                    multi_select=multi_select,
                )

            # Create WebSocket event emitter (同步版本，避免 event loop 问题)
            import queue
            _event_queue: queue.Queue = queue.Queue()

            def emit_ws_event(event_type: str, data: Any):
                """Send event to WebSocket (同步版本，放入队列由单独线程处理)"""
                _event_queue.put((event_type, data))

            # 启动事件处理线程
            def _event_worker():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                while True:
                    try:
                        event_type, data = _event_queue.get(timeout=1)
                        if loop.is_closed():
                            break
                        try:
                            loop.run_until_complete(self._ws_manager.emit_event(session_id, event_type, data))
                        except Exception:
                            pass
                    except queue.Empty:
                        continue
                    except Exception:
                        pass

            import threading
            event_thread = threading.Thread(target=_event_worker, daemon=True)
            event_thread.start()

            # 注册 QMClaw MCP 工具到 hermes（必须在 AIAgent 创建之前）
            try:
                from tools.registry import registry as hermes_registry
                from .quantum_toolset import HermesQuantumBridge
                from ..common.quantum_tools.analysis_tools import setup_analysis_tools
                from ..common.quantum_tools.workflow_tools import setup_workflow_tools
                from ..common.quantum_tools import ToolRegistry

                # 注册 quantum 工具
                quantum_reg = ToolRegistry()
                setup_hermes_tools(quantum_reg, "http://localhost:3003")
                quantum_bridge = HermesQuantumBridge(hermes_registry, "http://localhost:3003")
                quantum_bridge.register_to_hermes(hermes_registry)
                _log(f"Registered quantum tools to hermes: {len(quantum_reg.list_tools())} tools")

                # 注册 analysis 工具 (name 已经是 "analysis_xxx" 格式)
                analysis_reg = ToolRegistry()
                setup_analysis_tools(analysis_reg, "http://localhost:3004")
                for name in analysis_reg.list_tools():
                    tool = analysis_reg.get_tool(name)
                    if tool:
                        schema = tool.schema.to_openai_schema()
                        # 工具名格式: qmclaw_{name} (name 已经是 "analysis_xxx")
                        hermes_registry.register(
                            name=f"qmclaw_{name}",
                            toolset="analysis",
                            schema=schema,
                            handler=lambda args, tn=name, reg=analysis_reg: json.dumps(reg.execute(tn, args).to_dict(), ensure_ascii=False),
                            emoji="📊",
                        )
                _log(f"Registered analysis tools to hermes: {len(analysis_reg.list_tools())} tools")

                # 注册 workflow 工具 (name 已经是 "workflow_xxx" 格式)
                workflow_reg = ToolRegistry()
                setup_workflow_tools(workflow_reg, "http://localhost:3008")
                for name in workflow_reg.list_tools():
                    tool = workflow_reg.get_tool(name)
                    if tool:
                        schema = tool.schema.to_openai_schema()
                        # 工具名格式: qmclaw_{name} (name 已经是 "workflow_xxx")
                        hermes_registry.register(
                            name=f"qmclaw_{name}",
                            toolset="workflow",
                            schema=schema,
                            handler=lambda args, tn=name, reg=workflow_reg: json.dumps(reg.execute(tn, args).to_dict(), ensure_ascii=False),
                            emoji="⚙️",
                        )
                _log(f"Registered workflow tools to hermes: {len(workflow_reg.list_tools())} tools")

            except Exception as e:
                _log(f"Warning: Failed to register MCP tools to hermes: {e}")

            _log(f"Creating AIAgent with ephemeral_system_prompt: {IMAGE_DISPLAY_SYSTEM_PROMPT[:100]}...")
            agent = AIAgent(
                base_url=base_url,
                api_key=api_key,
                model=model,
                provider=model_config.get("provider", ""),
                session_id=session_id,
                enabled_toolsets=toolsets_list if toolsets_list else None,
                disabled_toolsets=disabled_toolsets if disabled_toolsets else [],

                # Session persistence (use Hermes state.db)
                session_db=self._hermes_config.get("session_db"),
                platform="cli",

                # Full features
                skip_memory=False,
                skip_background_review=False,
                quiet_mode=False,  # Enable status output for callbacks

                # System prompt with image display guidelines
                ephemeral_system_prompt=IMAGE_DISPLAY_SYSTEM_PROMPT,

                # Clarify callback (for asking user questions)
                clarify_callback=clarify_callback,

                # Event callbacks (同步版本 - 放入队列)
                thinking_callback=lambda c: emit_ws_event("thinking", {"content": c}),
                reasoning_callback=lambda c: emit_ws_event("reasoning", {"content": c}),  # 实际的推理内容
                tool_start_callback=lambda n, a: emit_ws_event("tool_start", {"name": n, "args": a}),
                # Hermes calls tool_complete_callback(call_id, name, args, result)
                tool_complete_callback=lambda cid, n, a, r: emit_ws_event("tool_complete", {"call_id": cid, "name": n, "args": a, "result": r}),
                stream_delta_callback=lambda c: emit_ws_event("stream", {"content": c}),
                status_callback=lambda s: emit_ws_event("status", {"status": s}),

                # Tool progress callback - 发送详细工具执行日志
                tool_progress_callback=lambda *args, **kwargs: _handle_tool_progress(emit_ws_event, *args, **kwargs),
            )

            self._sessions[session_id] = agent
            return agent

    def get_session(self, session_id: str) -> Optional[Any]:
        """Get a session by ID."""
        with self._lock:
            return self._sessions.get(session_id)

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
    """Hermes Agent 微服务（完整 CLI 功能）

    提供完整的 Hermes Agent 功能，包括：
    - AI 对话
    - 工具调用（包括 Terminal）
    - SSE 流式输出
    - 会话管理（SQLite 持久化）
    - Approval 确认（WebSocket）
    - 量子测控工具（通过 HTTP 调用 quantum_service）
    """

    def __init__(self, port: int = 3012):
        cfg = ServiceConfig(
            name="hermes_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        self._hermes_available = HERMES_AVAILABLE
        self._available_models = []
        self._hermes_config = {}

        # WebSocket Approval 管理器
        self._ws_manager = WebSocketApprovalManager()

        # 初始化量子工具注册中心
        self._quantum_registry = None
        self._quantum_service_url = HERMES_QUANTUM_SERVICE_URL if QUANTUM_TOOLSET_AVAILABLE else None

        # WebSocket 服务器线程
        self._ws_thread = None

    def after_start(self):
        """启动后钩子 - 启动 WebSocket 服务器"""
        super().after_start()

        # 启动 WebSocket 服务器（用于 Approval）
        try:
            self._ws_thread = _start_websocket_thread(self._ws_manager, port=3013)
        except Exception as e:
            _log(f"Failed to start WebSocket server: {e}")

    def before_start(self):
        """启动前初始化"""
        _log("Initializing Hermes service (Full CLI mode)...")

        if not self._hermes_available:
            _log("WARNING: Hermes-Agent (AIAgent) not available")
            return

        # 1. 加载完整配置（与 CLI 相同）
        try:
            from hermes_cli.config import load_config
            hermes_config = load_config()
            _log("Loaded Hermes config from ~/.hermes/config.yaml")
        except Exception as e:
            _log(f"Failed to load Hermes config: {e}")
            hermes_config = {}

        # 2. 初始化 SessionDB（SQLite 持久化）
        session_db = None
        try:
            from hermes_state import SessionDB
            session_db = SessionDB()
            _log("SessionDB initialized: ~/.hermes/state.db")
        except Exception as e:
            _log(f"Failed to initialize SessionDB: {e}")

        # 3. 加载完整工具集（与 CLI 相同）
        toolsets = []
        try:
            from hermes_cli.tools_config import _get_platform_tools
            toolsets = sorted(_get_platform_tools(hermes_config, "cli"))
            _log(f"Loaded {len(toolsets)} toolsets from config")
        except Exception as e:
            _log(f"Failed to load toolsets: {e}")

        # 4. 启用 MCP 自动发现
        try:
            from hermes_cli.mcp_startup import ensure_mcp_discovery_before_agent_build
            ensure_mcp_discovery_before_agent_build(
                logger=_log,
                single_query=False
            )
            _log("MCP discovery completed")
        except Exception as e:
            _log(f"Warning: MCP discovery failed: {e}")

        # 保存配置
        self._hermes_config = {
            "config": hermes_config,
            "toolsets": toolsets,
            "session_db": session_db,
            "project_root": str(PROJECT_ROOT),
        }

        # 5. 初始化 SessionManager（使用完整配置）
        self._session_manager = SessionManager(
            ws_manager=self._ws_manager,
            hermes_config=self._hermes_config
        )

        # 6. Load available models
        self._available_models = _load_model_configs()
        _log(f"Loaded {len(self._available_models)} model configurations")

        enabled_count = sum(1 for m in self._available_models if m.get("enabled", False))
        _log(f"Enabled models: {enabled_count}")

        # 7. 初始化量子工具注册中心
        if QUANTUM_TOOLSET_AVAILABLE:
            try:
                from ..common.quantum_tools import ToolRegistry
                self._quantum_registry = ToolRegistry()
                setup_hermes_tools(self._quantum_registry, self._quantum_service_url)
                _log(f"Quantum tools registered: {len(self._quantum_registry.list_tools())} tools")
            except Exception as e:
                _log(f"Warning: Failed to setup quantum tools: {e}")

        # 8. 初始化 MCP 桥接器（Quantum + Analysis + Workflow）
        try:
            from .mcp_toolsets import MCPBridgeManager
            self._mcp_bridge = MCPBridgeManager(
                quantum_service_url=self._quantum_service_url,
                analysis_service_url="http://localhost:3004",
                workflow_service_url="http://localhost:3008",
            )
            self._mcp_bridge.initialize()
            _log(f"MCP Bridge initialized: {len(self._mcp_bridge.get_toolset_names())} toolsets, "
                 f"{self._mcp_bridge.get_tool_count()} total tools")
        except Exception as e:
            _log(f"Warning: Failed to setup MCP bridge: {e}")
            self._mcp_bridge = None

        # 9. 初始化 Cron 调度器
        try:
            cron_init_count = cron_module.init_cron()
            _log(f"Cron scheduler initialized: {cron_init_count} jobs scheduled")
        except Exception as e:
            _log(f"Warning: Failed to initialize cron scheduler: {e}")

    def support_streaming(self) -> bool:
        """支持 SSE 流式响应"""
        return True

    def handle_stream_request(self, path: str, data: Dict[str, Any], write_fn):
        """处理 SSE 流式请求"""
        if path == "/chat/stream":
            self._handle_chat_stream(data, write_fn)
        else:
            write_fn(f"event: error\ndata: {json.dumps({'error': f'Unknown stream path: {path}'})}\n\n")

    def handle_request(self, method: str, path: str, data: Dict[str, Any],
                      query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理请求"""
        # 量子工具 API
        if path.startswith("/quantum-tools"):
            return self._handle_quantum_tools(path, method, data)

        # WebSocket 连接升级（预留，实际由 gateway 处理）
        if path == "/ws":
            raise NotImplementedError("WebSocket upgrade handled by gateway")

        # 路由
        if path == "/health":
            return self._handle_health()
        elif path == "/models":
            return self._handle_models()
        elif path == "/chat":
            return self._handle_chat(data)
        elif path == "/sessions":
            return self._handle_list_sessions()
        elif path.startswith("/sessions/"):
            # /sessions/:id 或 /sessions/:id/messages 或 DELETE /sessions/:id
            parts = path.split("/")
            if len(parts) >= 3:
                session_id = parts[2]
                if method == "DELETE":
                    return self._handle_delete_session(session_id)
                elif len(parts) == 3:
                    return self._handle_get_session(session_id)
                elif len(parts) == 4 and parts[3] == "messages":
                    return self._handle_get_session_messages(session_id)

        # 记忆管理 API
        elif path == "/memory":
            return self._handle_memory(method, data)
        elif path == "/memory/add":
            return self._handle_memory_add(data)
        elif path == "/memory/edit":
            return self._handle_memory_edit(data)
        elif path == "/memory/delete":
            return self._handle_memory_delete(data)

        # Skills API
        elif path == "/skills":
            return self._handle_skills()
        elif path.startswith("/skills/"):
            # /skills/:name 或 /skills/:name/enable 或 /skills/:name/disable
            parts = path.split("/")
            if len(parts) >= 3:
                skill_name = "/".join(parts[2:])  # 支持带斜杠的名称
                if len(parts) == 3:
                    return self._handle_skill_get(skill_name)
                elif len(parts) == 4 and parts[3] == "enable":
                    return self._handle_skill_toggle(skill_name, True)
                elif len(parts) == 4 and parts[3] == "disable":
                    return self._handle_skill_toggle(skill_name, False)

        # Cron API
        elif path == "/cron":
            return self._handle_cron(method, data)
        elif path.startswith("/cron/"):
            # /cron/:id/pause, /cron/:id/resume, /cron/:id/run
            parts = path.split("/")
            if len(parts) >= 4:
                job_id = parts[2]
                action = parts[3]
                if action == "pause":
                    return self._handle_cron_pause(job_id)
                elif action == "resume":
                    return self._handle_cron_resume(job_id)
                elif action == "run":
                    return self._handle_cron_run(job_id)

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
        disabled_toolsets = data.get("disabled_toolsets", [])
        session_id = data.get("session_id", f"hermes_{int(time.time() * 1000)}")

        if not message:
            write_fn("event: error\ndata: " + json.dumps({"error": "message is required"}) + "\n\n")
            return

        _log(f"Stream request: session={session_id}, model={model}")
        _log(f"  message: {message[:100]}...")

        try:
            # Find model config
            model_config = None
            if model:
                for m in self._available_models:
                    if m.get("modelId") == model or m.get("name") == model:
                        model_config = m
                        break

            # Fallback: use first enabled model
            if not model_config:
                for m in self._available_models:
                    if m.get("enabled", False):
                        model_config = m
                        break

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

            # Run conversation - callbacks are set in SessionManager
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
            "session_count": len(self._session_manager._sessions) if hasattr(self._session_manager, '_sessions') else 0,
            "ws_connections": self._ws_manager.get_connection_count(),
            "project_root": str(PROJECT_ROOT),
            "toolsets": self._hermes_config.get("toolsets", []),
            "session_db_available": self._hermes_config.get("session_db") is not None,
        }

    def _handle_models(self) -> Dict[str, Any]:
        """获取可用模型列表"""
        enabled_models = [m for m in self._available_models if m.get("enabled", False)]
        return {
            "models": enabled_models,
            "count": len(enabled_models),
        }

    def _handle_quantum_tools(self, path: str, method: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理量子/分析/工作流工具请求"""
        import uuid

        # 初始化 registry（如果需要）
        if not hasattr(self, "_all_registries"):
            self._all_registries = {}

            # 添加 quantum registry（如果可用）
            if QUANTUM_TOOLSET_AVAILABLE and self._quantum_registry:
                self._all_registries["quantum"] = self._quantum_registry

            # 添加 analysis registry
            try:
                from ..common.quantum_tools import ToolRegistry
                from ..common.quantum_tools.analysis_tools import setup_analysis_tools
                analysis_reg = ToolRegistry()
                setup_analysis_tools(analysis_reg, "http://localhost:3004")
                self._all_registries["analysis"] = analysis_reg
            except Exception as e:
                _log(f"Warning: Failed to setup analysis tools: {e}")

            # 添加 workflow registry
            try:
                from ..common.quantum_tools import ToolRegistry
                from ..common.quantum_tools.workflow_tools import setup_workflow_tools
                workflow_reg = ToolRegistry()
                setup_workflow_tools(workflow_reg, "http://localhost:3008")
                self._all_registries["workflow"] = workflow_reg
            except Exception as e:
                _log(f"Warning: Failed to setup workflow tools: {e}")

        # 如果没有可用的 registry，返回错误
        if not self._all_registries:
            return {
                "success": False,
                "error": "No toolsets available",
            }

        # /quantum-tools - 列出所有工具
        if path == "/quantum-tools" and method == "GET":
            all_tools = {}
            for ns, reg in self._all_registries.items():
                if reg:
                    tools = reg.list_tools()
                    # 工具名已经是完整格式（如 "analysis_plot_offline"）
                    all_tools[ns] = {
                        "name": ns,
                        "description": f"{ns.capitalize()} tools",
                        "tool_count": len(tools),
                        "tools": [f"qmclaw_{t}" for t in tools],
                    }
            return {
                "success": True,
                "data": all_tools,
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

            # 解析工具名格式: qmclaw_{完整工具名}
            # 例如: qmclaw_workflow_list -> 直接使用 "workflow_list" 查找
            # 注意: "qmclaw_" 有 7 个字符
            if tool_name.startswith("qmclaw_"):
                tool_name = tool_name[7:]  # 移除 "qmclaw_" (7 chars)

                # 尝试在所有 registry 中查找
                for ns, reg in self._all_registries.items():
                    if reg and tool_name in reg.list_tools():
                        result = reg.execute(tool_name, args)
                        return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

            return {
                "success": False,
                "error": f"Tool not found: {tool_name}",
            }

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
        disabled_toolsets = data.get("disabled_toolsets", [])
        session_id = data.get("session_id", f"hermes_{int(time.time() * 1000)}")

        if not message:
            return {"error": "message is required", "completed": False}

        _log(f"Chat request: session={session_id}, model={model}")
        _log(f"  message: {message[:100]}...")

        try:
            # Find model config
            model_config = None
            if model:
                for m in self._available_models:
                    if m.get("modelId") == model or m.get("name") == model:
                        model_config = m
                        break

            # Fallback: use first enabled model
            if not model_config:
                for m in self._available_models:
                    if m.get("enabled", False):
                        model_config = m
                        break

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

            # Load conversation history from SessionDB
            conversation_history = None
            session_db = self._hermes_config.get("session_db")
            if session_db:
                try:
                    restored, _ = session_db.get_resume_conversations(session_id)
                    if restored:
                        conversation_history = restored
                        _log(f"Loaded {len(restored)} historical messages from SessionDB")
                except Exception as e:
                    _log(f"Failed to load history: {e}")

            # Run conversation
            _log(f"Running conversation...")
            result = agent.run_conversation(message, conversation_history=conversation_history)

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

    def _handle_list_sessions(self) -> Dict[str, Any]:
        """列出所有会话"""
        session_db = self._hermes_config.get("session_db")
        if not session_db:
            return {"sessions": [], "error": "SessionDB not available"}

        try:
            # 使用 list_sessions_rich 获取会话列表
            sessions = session_db.list_sessions_rich(
                limit=50,
                order_by_last_active=True,
                compact_rows=True,
            )

            # 简化返回格式
            result = []
            for s in sessions:
                result.append({
                    "id": s.get("id"),
                    "title": s.get("title") or s.get("id", "")[:16],
                    "created_at": s.get("created_at"),
                    "last_active": s.get("last_active"),
                    "message_count": s.get("message_count", 0),
                    "model": s.get("model"),
                    "end_reason": s.get("end_reason"),
                })

            return {"sessions": result}
        except Exception as e:
            _log(f"Failed to list sessions: {e}")
            return {"sessions": [], "error": str(e)}

    def _handle_get_session(self, session_id: str) -> Dict[str, Any]:
        """获取会话详情"""
        session_db = self._hermes_config.get("session_db")
        if not session_db:
            return {"session": None, "error": "SessionDB not available"}

        try:
            session = session_db.get_session(session_id)
            if not session:
                return {"session": None, "error": "Session not found"}

            return {
                "session": {
                    "id": session.get("id"),
                    "title": session.get("title"),
                    "created_at": session.get("created_at"),
                    "last_active": session.get("last_active"),
                    "message_count": session.get("message_count", 0),
                    "model": session.get("model"),
                    "end_reason": session.get("end_reason"),
                    "system_prompt": session.get("system_prompt"),
                }
            }
        except Exception as e:
            _log(f"Failed to get session {session_id}: {e}")
            return {"session": None, "error": str(e)}

    def _handle_get_session_messages(self, session_id: str) -> Dict[str, Any]:
        """获取会话消息"""
        session_db = self._hermes_config.get("session_db")
        if not session_db:
            return {"messages": [], "error": "SessionDB not available"}

        try:
            restored, display_history = session_db.get_resume_conversations(session_id)

            # 过滤掉 session_meta 类型的消息
            messages = [m for m in (restored or []) if m.get("role") != "session_meta"]

            return {"messages": messages}
        except Exception as e:
            _log(f"Failed to get messages for session {session_id}: {e}")
            return {"messages": [], "error": str(e)}

    def _handle_delete_session(self, session_id: str) -> Dict[str, Any]:
        """删除会话"""
        session_db = self._hermes_config.get("session_db")
        if not session_db:
            return {"success": False, "error": "SessionDB not available"}

        try:
            # 检查会话是否存在
            session = session_db.get_session(session_id)
            if not session:
                return {"success": False, "error": "Session not found"}

            # 删除会话（SessionDB 有 delete_session 方法）
            if hasattr(session_db, 'delete_session'):
                session_db.delete_session(session_id)
            elif hasattr(session_db, 'archive_session'):
                # 如果没有 delete，用 archive 代替
                session_db.archive_session(session_id)
            else:
                return {"success": False, "error": "Delete not supported"}

            _log(f"Deleted session: {session_id}")
            return {"success": True, "session_id": session_id}
        except Exception as e:
            _log(f"Failed to delete session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    # ── Memory API ────────────────────────────────────────────────────────────

    def _handle_memory(self, method: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取记忆状态"""
        if method == "GET":
            try:
                return memory_module.get_memory_state()
            except Exception as e:
                _log(f"Failed to get memory state: {e}")
                return {"error": str(e)}
        return {"error": "Method not allowed, use GET"}

    def _handle_memory_add(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """添加记忆"""
        try:
            target = data.get("target", "memory")
            content = data.get("content", "")
            return memory_module.add_entry(target, content)
        except ValueError as e:
            return {"error": str(e), "ok": False}
        except Exception as e:
            _log(f"Failed to add memory: {e}")
            return {"error": str(e), "ok": False}

    def _handle_memory_edit(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """编辑记忆"""
        try:
            target = data.get("target", "memory")
            old_text = data.get("old_text", "")
            content = data.get("content", "")
            return memory_module.edit_entry(target, old_text, content)
        except ValueError as e:
            return {"error": str(e), "ok": False}
        except Exception as e:
            _log(f"Failed to edit memory: {e}")
            return {"error": str(e), "ok": False}

    def _handle_memory_delete(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """删除记忆"""
        try:
            target = data.get("target", "memory")
            old_text = data.get("old_text", "")
            return memory_module.delete_entry(target, old_text)
        except ValueError as e:
            return {"error": str(e), "ok": False}
        except Exception as e:
            _log(f"Failed to delete memory: {e}")
            return {"error": str(e), "ok": False}

    # ── Skills API ────────────────────────────────────────────────────────────

    def _handle_skills(self) -> Dict[str, Any]:
        """获取 Skills 列表"""
        try:
            return skills_module.get_skills_state()
        except Exception as e:
            _log(f"Failed to get skills state: {e}")
            return {"error": str(e)}

    def _handle_skill_get(self, name: str) -> Dict[str, Any]:
        """获取 Skill 内容"""
        try:
            return skills_module.get_skill_content(name)
        except Exception as e:
            _log(f"Failed to get skill content: {e}")
            return {"error": str(e)}

    def _handle_skill_toggle(self, name: str, enabled: bool) -> Dict[str, Any]:
        """启用/禁用 Skill"""
        try:
            return skills_module.toggle_skill(name, enabled)
        except Exception as e:
            _log(f"Failed to toggle skill: {e}")
            return {"error": str(e)}

    # ── Cron API ──────────────────────────────────────────────────────────────

    def _handle_cron(self, method: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取或创建 Cron 任务"""
        if method == "GET":
            try:
                return cron_module.get_cron_state()
            except Exception as e:
                _log(f"Failed to get cron state: {e}")
                return {"error": str(e)}
        elif method == "POST":
            try:
                name = data.get("name", "")
                schedule = data.get("schedule", "")
                task_type = data.get("task_type", "quantum")
                prompt = data.get("prompt", "")
                model = data.get("model")
                skills = data.get("skills", [])
                qubit = data.get("qubit")  # 支持指定量子比特
                return cron_module.create_job(name, schedule, task_type, prompt, model, skills, qubit)
            except ValueError as e:
                return {"error": str(e), "ok": False}
            except Exception as e:
                _log(f"Failed to create cron job: {e}")
                return {"error": str(e), "ok": False}
        return {"error": "Method not allowed"}

    def _handle_cron_pause(self, job_id: str) -> Dict[str, Any]:
        """暂停任务"""
        try:
            return cron_module.pause_job(job_id)
        except ValueError as e:
            return {"error": str(e), "ok": False}
        except Exception as e:
            _log(f"Failed to pause cron job: {e}")
            return {"error": str(e), "ok": False}

    def _handle_cron_resume(self, job_id: str) -> Dict[str, Any]:
        """恢复任务"""
        try:
            return cron_module.resume_job(job_id)
        except ValueError as e:
            return {"error": str(e), "ok": False}
        except Exception as e:
            _log(f"Failed to resume cron job: {e}")
            return {"error": str(e), "ok": False}

    def _handle_cron_run(self, job_id: str) -> Dict[str, Any]:
        """立即运行任务"""
        try:
            return cron_module.run_job_now(job_id)
        except ValueError as e:
            return {"error": str(e), "ok": False}
        except Exception as e:
            _log(f"Failed to run cron job: {e}")
            return {"error": str(e), "ok": False}


def main():
    """主入口"""
    service = HermesService(port=3012)
    run_service(service)


async def _run_websocket_server(ws_manager: WebSocketApprovalManager, port: int = 3013):
    """运行 WebSocket 服务器（用于 Approval）"""
    import asyncio
    import websockets

    async def handler(websocket):
        # 新版 websockets 库通过 websocket.request.path 获取路径
        path = getattr(websocket, 'request', None) and getattr(websocket.request, 'path', '/')
        if not path:
            path = '/'

        # 解析 session_id
        session_id = None
        if "?" in path:
            query = path.split("?")[1]
            for param in query.split("&"):
                if param.startswith("session_id="):
                    session_id = param.split("=")[1]
                    break

        if not session_id:
            await websocket.close(4000, "session_id required")
            return

        await ws_manager.register(session_id, websocket)
        _log(f"WebSocket connected: session={session_id}")

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type", "")

                    if msg_type == "approval_response":
                        response = data.get("response", "deny")
                        await ws_manager.handle_response(session_id, response)
                    else:
                        _log(f"Unknown message type: {msg_type}")
                except json.JSONDecodeError:
                    _log(f"Invalid JSON: {message[:100]}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await ws_manager.unregister(session_id)
            _log(f"WebSocket disconnected: session={session_id}")

    _log(f"Starting WebSocket server on port {port}...")
    async with websockets.serve(handler, "localhost", port):
        _log(f"WebSocket server ready on ws://localhost:{port}")
        await asyncio.Future()  # Run forever


def _start_websocket_thread(ws_manager: WebSocketApprovalManager, port: int = 3013):
    """在单独线程中启动 WebSocket 服务器"""
    import threading

    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_websocket_server(ws_manager, port))

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    _log(f"WebSocket server thread started on port {port}")
    return thread


if __name__ == "__main__":
    main()
