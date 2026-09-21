"""
services/agent_service/server.py - Agent 服务

提供量子智能体功能：
- QuantumAgent - 量子实验执行智能体
- ReAct 推理引擎
- 工具注册和管理
- 统一量子测控工具集成
"""

import json
import time
import threading
import sys
import traceback
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config

# 记忆与反思（由 scripts/ 移植而来，磁盘格式与旧版一致）
from ..common import memory_store
from ..common import reflection_engine

# 导入统一量子工具
from ..common.quantum_tools import ToolRegistry, ToolResult
from .adapter import AgentServiceAdapter, setup_agent_tools


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[agent_service] {msg}")


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentMessage:
    """智能体消息"""
    role: str  # system, user, assistant
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentTask:
    """智能体任务"""
    task_id: str
    message: str
    mode: str = "react"
    model: str = "deepseek-flash"
    context: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    messages: List[AgentMessage] = field(default_factory=list)
    # ReAct 每一轮的结构化记录，最终写入记忆 episode 的 steps
    steps: List[Dict[str, Any]] = field(default_factory=list)


class Tool:
    """工具基类"""
    name: str = ""
    description: str = ""

    def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


class QuantumExperimentTool(Tool):
    """量子实验工具"""

    def __init__(self, executor):
        self.name = "run_experiment"
        self.description = "Execute a quantum experiment. Parameters: code (experiment code), qubit (qubit name)"
        self._executor = executor

    def execute(self, code: str, qubit: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """执行量子实验"""
        try:
            # 调用测控服务
            result = self._executor(code)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}


class GetQubitsTool(Tool):
    """获取量子比特工具"""

    def __init__(self, qubit_getter):
        self.name = "get_qubits"
        self.description = "Get list of available qubits with their parameters. No parameters needed."
        self._getter = qubit_getter

    def execute(self, **kwargs) -> Dict[str, Any]:
        """获取量子比特列表"""
        try:
            qubits = self._getter()
            return {"success": True, "qubits": qubits}
        except Exception as e:
            return {"success": False, "error": str(e)}


class ListExperimentsTool(Tool):
    """列出实验工具"""

    def __init__(self, experiment_lister):
        self.name = "list_experiments"
        self.description = "List available quantum experiments. Parameters: filter (optional experiment name filter)"
        self._lister = experiment_lister

    def execute(self, filter: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """列出实验"""
        try:
            experiments = self._lister()
            if filter:
                experiments = [e for e in experiments if filter.lower() in e["name"].lower()]
            return {"success": True, "experiments": experiments}
        except Exception as e:
            return {"success": False, "error": str(e)}


class AgentService(BaseService):
    """Agent 服务

    核心功能:
    - QuantumAgent - 量子实验执行智能体
    - ReAct 推理引擎
    - 工具注册和管理
    - 统一量子测控工具（通过 adapter 集成）
    """

    def __init__(self, port: int = 3005):
        cfg = ServiceConfig(
            name="agent_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # 统一工具适配器
        self._adapter = AgentServiceAdapter()

        # 统一工具注册中心
        self._registry = self._adapter.get_registry()

        # 工具注册表（保留旧接口以兼容）
        self._tools: Dict[str, Tool] = {}

        # 测控服务客户端
        self._quantum_client = None

        # 任务存储
        self._tasks: Dict[str, AgentTask] = {}
        self._tasks_lock = threading.Lock()

        # LLM 服务地址
        self._llm_service_url = "http://localhost:3006"

        # 默认系统提示
        self._system_prompt = """你是一个量子测控智能体，可以帮助用户执行量子实验和分析结果。

可用工具:
- run_experiment: 执行量子实验代码
- get_qubits: 获取可用量子比特列表
- list_experiments: 列出可用的量子实验

执行实验时，你需要:
1. 理解用户的实验需求
2. 确定要使用的量子比特
3. 编写或选择合适的实验代码
4. 执行实验并等待结果
5. 分析结果并给出建议

重要提示:
- 实验代码应该是有效的 Python 代码
- 使用 sq.* 函数来执行实验
- 量子比特名称格式如 q10lu1
"""

        # quantum_service 地址（通过 HTTP 调用）
        self._quantum_service_url = "http://localhost:3003"

        # 初始化 adapter（通过 HTTP 调用 quantum_service）
        self._adapter = AgentServiceAdapter(self._quantum_service_url)
        self._registry = self._adapter.get_registry()

        _log("Agent service initialized with unified quantum tools (HTTP to quantum_service)")

    def register_tool(self, tool: Tool):
        """注册工具"""
        self._tools[tool.name] = tool
        _log(f"Registered tool: {tool.name}")

    def _call_llm(self, messages: List[Dict], model: str = "deepseek-flash", temperature: float = 0.7) -> Dict[str, Any]:
        """调用 LLM 服务"""
        import urllib.request
        import urllib.error

        payload = {
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": 4096,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._llm_service_url}/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result
        except Exception as e:
            return {"error": str(e)}

    def _react_reason(self, task: AgentTask) -> Dict[str, Any]:
        """ReAct 推理循环"""
        max_iterations = 10
        iteration = 0

        # 构建初始消息
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task.message},
        ]

        while iteration < max_iterations:
            iteration += 1
            _log(f"ReAct iteration {iteration}/{max_iterations}")

            # 本轮的结构化记录（最终写入 episode.steps）
            step: Dict[str, Any] = {
                "iteration": iteration,
                "assistant": "",
                "action": None,
                "observation": None,
            }

            # 调用 LLM
            response = self._call_llm(messages, model=task.model)
            if "error" in response:
                step["error"] = response["error"]
                task.steps.append(step)
                return {"error": response["error"]}

            assistant_content = response.get("content", "")
            step["assistant"] = assistant_content

            # 添加助手回复
            messages.append({"role": "assistant", "content": assistant_content})
            task.messages.append(AgentMessage(role="assistant", content=assistant_content))

            # 检查是否完成（没有工具调用）
            if "Action:" not in assistant_content and "Action:" not in assistant_content:
                step["final"] = True
                task.steps.append(step)
                return {
                    "success": True,
                    "response": assistant_content,
                    "iterations": iteration,
                    "messages": [{"role": m.role, "content": m.content} for m in task.messages],
                }

            # 解析工具调用
            action_text = ""
            for line in assistant_content.split("\n"):
                if line.startswith("Action:"):
                    action_text = line.replace("Action:", "").strip()
                    break

            if not action_text:
                step["final"] = True
                step["note"] = "no Action: line found"
                task.steps.append(step)
                continue

            step["action"] = action_text

            # 解析工具和参数
            try:
                # 简单解析: tool_name(arg1=value1, arg2=value2)
                if "(" in action_text:
                    tool_name = action_text.split("(")[0].strip()
                    args_str = action_text.split("(")[1].rstrip(")").strip()
                    args = {}

                    # 解析参数
                    if args_str:
                        for arg in args_str.split(","):
                            if "=" in arg:
                                key, value = arg.split("=", 1)
                                args[key.strip()] = eval(value.strip())

                    # 执行工具
                    if tool_name in self._tools:
                        tool_result = self._tools[tool_name].execute(**args)
                    else:
                        tool_result = {"error": f"Unknown tool: {tool_name}"}

                else:
                    tool_name = action_text.strip()
                    if tool_name in self._tools:
                        tool_result = self._tools[tool_name].execute()
                    else:
                        tool_result = {"error": f"Unknown tool: {tool_name}"}

            except Exception as e:
                tool_result = {"error": f"Tool execution error: {e}"}

            # 添加工具结果
            tool_result_str = json.dumps(tool_result, ensure_ascii=False)
            observation = f"Observation: {tool_result_str}"
            messages.append({"role": "user", "content": observation})
            task.messages.append(AgentMessage(role="user", content=observation))

            # 收尾本轮步骤（action 已在上面记过，这里补 observation）
            step["observation"] = tool_result_str[:2000]
            task.steps.append(step)

        return {
            "success": False,
            "error": "Max iterations reached",
            "iterations": iteration,
            "messages": [{"role": m.role, "content": m.content} for m in task.messages],
        }

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理 Agent 请求"""
        # API v1 路由
        if path.startswith("/api/v1/"):
            return self._handle_api_v1(path, method, data)

        # 路由
        if path == "/health":
            return self._handle_health()
        elif path == "/chat":
            return self._handle_chat(data)
        elif path == "/chat/stream":
            return self._handle_chat_stream(data)
        elif path == "/tasks":
            return self._handle_tasks(query)
        elif path == "/tasks/status":
            return self._handle_task_status(data)
        elif path == "/tools":
            return self._handle_tools()
        elif path == "/memory" or path.startswith("/memory/"):
            # 记忆与反思。Express 侧 /api/agent/memory/* 会原样代理到这里。
            return self._handle_memory(path, method, data, query)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_api_v1(self, path: str, method: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理 API v1 请求"""
        import uuid

        # /api/v1/tools - 列出所有工具
        if path == "/api/v1/tools" and method == "GET":
            return {
                "success": True,
                "data": self._registry.get_definitions(),
                "count": len(self._registry.list_tools()),
                "request_id": f"req_{uuid.uuid4().hex[:12]}",
            }

        # /api/v1/tools/{name} - 获取工具 schema
        if path.startswith("/api/v1/tools/") and method == "GET":
            tool_name = path.split("/api/v1/tools/")[1]
            schema = self._registry.get_schema(tool_name)
            if schema:
                return {
                    "success": True,
                    "data": schema,
                    "request_id": f"req_{uuid.uuid4().hex[:12]}",
                }
            return {
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Tool '{tool_name}' not found",
                },
            }

        # /api/v1/tools/{name} - 执行工具
        if path.startswith("/api/v1/tools/") and method == "POST":
            tool_name = path.split("/api/v1/tools/")[1]
            result = self._registry.execute(tool_name, data)
            return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

        # /api/v1/qubits - 获取量子比特
        if path == "/api/v1/qubits" and method == "GET":
            result = self._registry.execute("get_qubits", {})
            return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

        # /api/v1/experiments - 列出实验
        if path == "/api/v1/experiments" and method == "GET":
            result = self._registry.execute("list_experiments", {})
            return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

        # /api/v1/health
        if path == "/api/v1/health":
            return {
                "success": True,
                "data": {
                    "status": "healthy",
                    "service": "agent_service",
                    "tool_count": len(self._registry.list_tools()),
                    "connected": self._quantum_client is not None and self._quantum_client.connected,
                },
                "request_id": f"req_{uuid.uuid4().hex[:12]}",
            }

        raise ValueError(f"Unknown API path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy",
            "service": "agent_service",
            "tool_count": len(self._tools),
            "task_count": len(self._tasks),
        }

    def _handle_chat(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理聊天请求"""
        message = data.get("message")
        base_url = data.get("base_url")
        enabled_toolsets = data.get("enabled_toolsets", [])
        session_id = data.get("session_id")
        mode = data.get("mode", "react")
        context = data.get("context", {})
        task_id = data.get("task_id", f"task_{int(time.time() * 1000)}")

        # 模型解析优先级：
        #   1. 顶层 model（legacy 后端 / 直接调用 microservice 时使用）
        #   2. context.model_id（前端 AgentChatPanel 走的就是这条）
        #   3. 默认 deepseek-flash
        # 注意：前端下拉框传的是 model_configs.json 里的内部 id（如 model_default_001），
        # 不是 API 模型名。那种 id 会原样转给 llm_service 的兜底分支，由它选可用 provider。
        model = data.get("model") or context.get("model_id") or "deepseek-flash"

        _log(f"DEBUG _handle_chat: message={message[:100] if message else None}...")
        _log(f"DEBUG _handle_chat: model={model}, base_url={base_url}, enabled_toolsets={enabled_toolsets}")
        _log(f"DEBUG _handle_chat: session_id={session_id}, mode={mode}")

        # 检查是否是 Hermes 请求（带有 base_url 或 enabled_toolsets）
        if base_url or enabled_toolsets:
            _log("Detected Hermes-style request, but Hermes is not implemented in agent_service")
            return {
                "error": "Hermes not implemented in microservice mode. Please use legacy mode or implement hermes_service.",
                "hint": "Set USE_MICROSERVICES=false in index.ts to use legacy backend"
            }

        if not message:
            return {"error": "message is required"}

        _log(f"Processing chat: task_id={task_id}, mode={mode}")

        # 创建任务
        task = AgentTask(
            task_id=task_id,
            message=message,
            mode=mode,
            model=model,
            context=context,
        )

        with self._tasks_lock:
            self._tasks[task_id] = task
            task.status = TaskStatus.RUNNING

        try:
            # 执行推理
            started_at = time.time()
            if mode == "react":
                result = self._react_reason(task)
            else:
                result = {"error": f"Unknown mode: {mode}"}

            task.status = TaskStatus.COMPLETED if result.get("success") else TaskStatus.FAILED
            task.result = result
            task.completed_at = time.time()

            payload = {
                "success": result.get("success", False),
                "task_id": task_id,
                "response": result.get("response", ""),
                "error": result.get("error"),
                "iterations": result.get("iterations", 0),
                "messages": result.get("messages", []),
            }

            # 记录这一轮对话到记忆（失败不影响返回）
            payload["duration_ms"] = int((time.time() - started_at) * 1000)
            episode_id = self._record_episode(task, payload)
            if episode_id:
                payload["episode_id"] = episode_id

            return payload

        except Exception as e:
            _log(f"Chat error: {e}\n{traceback.format_exc()}")
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            # 失败的一轮同样记入记忆，便于前端看到失败案例
            self._record_episode(task, {"success": False, "error": str(e)})
            return {"error": str(e), "task_id": task_id}

    def _handle_chat_stream(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理流式聊天（简单版本，实际使用 SSE）"""
        # 对于 HTTP 轮询模式，返回任务 ID
        message = data.get("message")
        mode = data.get("mode", "react")
        task_id = f"task_{int(time.time() * 1000)}"

        if not message:
            return {"error": "message is required"}

        # 创建任务
        task = AgentTask(
            task_id=task_id,
            message=message,
            mode=mode,
        )

        with self._tasks_lock:
            self._tasks[task_id] = task
            task.status = TaskStatus.RUNNING

        # 在后台线程执行
        def run_task():
            try:
                if mode == "react":
                    result = self._react_reason(task)
                else:
                    result = {"error": f"Unknown mode: {mode}"}

                task.status = TaskStatus.COMPLETED if result.get("success") else TaskStatus.FAILED
                task.result = result
                task.completed_at = time.time()
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.completed_at = time.time()

        thread = threading.Thread(target=run_task)
        thread.daemon = True
        thread.start()

        return {
            "task_id": task_id,
            "status": "pending",
            "streaming": True,
        }

    def _handle_tasks(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """获取任务列表"""
        status_filter = query.get("status", [None])[0] if query.get("status") else None

        with self._tasks_lock:
            tasks = []
            for task_id, task in self._tasks.items():
                if status_filter and task.status.value != status_filter:
                    continue
                tasks.append({
                    "task_id": task.task_id,
                    "message": task.message[:100],
                    "mode": task.mode,
                    "status": task.status.value,
                    "created_at": task.created_at,
                    "completed_at": task.completed_at,
                })

        return {"tasks": tasks, "count": len(tasks)}

    def _handle_task_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取任务状态"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"error": "Task not found"}

            return {
                "task_id": task.task_id,
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
                "completed_at": task.completed_at,
            }

    def _handle_tools(self) -> Dict[str, Any]:
        """获取工具列表"""
        tools = []
        for name, tool in self._tools.items():
            tools.append({
                "name": tool.name,
                "description": tool.description,
            })

        # 获取统一工具定义
        unified_tools = self._registry.get_definitions()

        return {
            "tools": tools,
            "count": len(tools),
            "unified_tools": unified_tools,
            "unified_count": len(unified_tools),
        }

    # ── 记忆与反思 (Memory & Reflection) ─────────────────────────────────────
    #
    # Express 侧 /api/agent/memory/* 会原样代理到下面这些路径。
    # 数据落在 <qmclaw-server>/config/memory/，格式与 scripts/memory_store.py 完全一致。

    @staticmethod
    def _q(query: Dict[str, List[str]], key: str, default=None):
        """从 query dict（值为 list）里取单个值"""
        v = (query or {}).get(key)
        if isinstance(v, (list, tuple)):
            return v[0] if v else default
        return v if v is not None else default

    def _handle_memory(self, path: str, method: str, data: Dict[str, Any],
                       query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理 /memory/* 请求"""
        data = data or {}

        # /memory/episodes — 列表（Express 用 POST 传参）
        if path == "/memory/episodes":
            try:
                limit = int(data.get("limit") or self._q(query, "limit", 50))
            except (TypeError, ValueError):
                limit = 50
            qubit = data.get("qubit") or self._q(query, "qubit")
            status = data.get("status") or self._q(query, "status")
            episodes = memory_store.list_episodes(limit=limit, qubit=qubit, status=status)
            return {"success": True, "episodes": episodes, "count": len(episodes)}

        # /memory/episodes/{id} — 详情 / 归档
        if path.startswith("/memory/episodes/"):
            episode_id = path[len("/memory/episodes/"):].strip("/")
            if method == "DELETE":
                ok = memory_store.archive_episode(episode_id)
                return {"success": ok, "episode_id": episode_id, "archived": ok}
            episode = memory_store.get_episode(episode_id)
            if not episode:
                return {"success": False, "error": f"Episode not found: {episode_id}"}
            return {"success": True, "episode": episode}

        # /memory/skills — 列表
        if path == "/memory/skills":
            status = data.get("status") or self._q(query, "status")
            skills = memory_store.list_skills(status=status)
            return {"success": True, "skills": skills, "count": len(skills)}

        # /memory/skills/{id} — 详情 / 删除
        if path.startswith("/memory/skills/"):
            skill_id = path[len("/memory/skills/"):].strip("/")
            if method == "DELETE":
                ok = memory_store.delete_skill(skill_id)
                return {"success": ok, "skill_id": skill_id}
            skill = memory_store.get_skill(skill_id)
            if not skill:
                return {"success": False, "error": f"Skill not found: {skill_id}"}
            return {"success": True, "skill": skill}

        # /memory/stats — 统计
        if path == "/memory/stats":
            stats = memory_store.get_memory_stats()
            stats["success"] = True
            return stats

        # /memory/recall — 按任务召回相关记忆
        if path == "/memory/recall":
            task = data.get("task") or data.get("message") or ""
            qubit = data.get("qubit") or self._q(query, "qubit")
            try:
                recalled = reflection_engine.recall_for_task(task, qubit)
            except Exception as e:
                _log(f"recall failed: {e}")
                recalled = {"hasMemory": False, "context": "", "error": str(e)}
            return {"success": True, **recalled}

        # /memory/reflect — 对某次任务做反思（可选回填到 episode）
        if path == "/memory/reflect":
            episode_id = data.get("episode_id")
            task = data.get("task") or data.get("message") or ""
            result_data = data.get("result") or {}
            try:
                reflection = reflection_engine.reflect_after_task(
                    task=task,
                    status=result_data.get("status", "success"),
                    steps=result_data.get("steps", []),
                    metrics=result_data.get("metrics", {}),
                    qubit=data.get("qubit") or result_data.get("qubit"),
                    mode=result_data.get("mode", "react"),
                )
            except Exception as e:
                _log(f"reflect failed: {e}")
                return {"success": False, "error": str(e)}
            if episode_id:
                memory_store.update_episode_reflection(episode_id, reflection)
            return {"success": True, "reflection": reflection, "episode_id": episode_id}

        return {"success": False, "error": f"Unknown memory path: {path}"}

    def _record_episode(self, task: "AgentTask", result: Dict[str, Any]) -> Optional[str]:
        """把一轮对话写入记忆。

        一轮 = 一次 /chat 调用。ReAct 的每次迭代存进该 episode 的 steps。
        写盘失败绝不能影响对话返回，所以整体 try/except 包住。
        """
        try:
            steps = getattr(task, "steps", None) or []
            status = "success" if result.get("success") else "failed"

            # 从用户消息里粗提 qubit 名，便于前端按 qubit 过滤（失败就算了）
            qubit = None
            try:
                import re as _re
                m = _re.search(r"\bq\d+[a-z]+\d+\b", task.message or "")
                if m:
                    qubit = m.group(0)
            except Exception:
                pass

            episode = memory_store.create_episode(
                task=task.message,
                steps=steps,
                status=status,
                metrics={
                    "iterations": result.get("iterations", 0),
                    "duration_ms": result.get("duration_ms", 0),
                },
                qubit=qubit,
                tags=[],
                used_skills=[],
                mode=task.mode,
            )

            # 回填会话信息，让"每一轮"能串成一次会话
            episode_id = episode.get("id")
            if episode_id:
                self._annotate_episode(
                    episode_id,
                    session_id=task.context.get("session_id") or task.task_id,
                    round_index=task.context.get("round_index"),
                    model=getattr(task, "model", None),
                    response=result.get("response", ""),
                )
            _log(f"Memory: recorded episode {episode_id} (status={status}, steps={len(steps)})")
            return episode_id
        except Exception as e:
            _log(f"Memory: failed to record episode: {e}")
            return None

    @staticmethod
    def _annotate_episode(episode_id: str, session_id=None, round_index=None,
                          model=None, response=None) -> None:
        """给已落盘的 episode 补写 session/round/model/response 字段。

        memory_store 的原始 schema 没有这几个字段 —— 但「记录每一轮对话」
        需要能区分"同一会话的第 N 轮"，所以在这里补上，不改动原模块。
        """
        try:
            ep = memory_store.get_episode(episode_id)
            if not ep:
                return
            ep["sessionId"] = session_id
            ep["roundIndex"] = round_index
            ep["model"] = model
            ep["response"] = response
            import os as _os
            fp = _os.path.join(memory_store.EPISODES_DIR, f"{episode_id}.json")
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(ep, f, indent=2, ensure_ascii=False)
        except Exception as e:
            _log(f"Memory: annotate failed: {e}")

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        return {
            "status": "healthy",
            "service": "agent_service",
            "tool_count": len(self._tools),
            "task_count": len(self._tasks),
            "llm_service": self._llm_service_url,
        }


def main():
    """主入口"""
    service = AgentService(port=3005)
    run_service(service)


if __name__ == "__main__":
    main()
