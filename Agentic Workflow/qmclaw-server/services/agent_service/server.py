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
        # 工具的具体参数由原生 function calling 的 schema 携带，这里只补行为约束 ——
        # 旧版本在这里逐条列工具名并教"写 sq.* 代码"，但从未教过 Action: 协议，
        # 与运行时实际依赖的协议不符，导致模型无法触发任何工具。
        self._system_prompt = """你是一个量子测控智能体，负责帮用户执行量子实验并解读结果。

工作方式:
1. 用户要求"运行/执行某个实验"时，调用 run_experiment 工具
   - experiment_name: 取自 list_experiments 的实验名（例如 t1、ramsey、s21、spectroscopy）
   - target: 用户指定的量子比特名（例如 q10lu12）
2. 需要确认有哪些比特或实验时，调用 get_qubits / list_experiments
3. 工具返回后，用中文简要说明这次实验做了什么、关键数值是多少
4. 如果工具结果标注了"合成数据"或"离线模拟"，必须在回复里如实告知用户，
   不要把它当成真实硬件测量结果

注意:
- 不要自己编写 sq.* 代码，一律通过 run_experiment 工具执行
- 量子比特名称形如 q10lu12（q + 行列号），实验名用小写
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

    def _call_llm(self, messages: List[Dict], model: str = "deepseek-flash",
                  temperature: float = 0.7,
                  tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """调用 LLM 服务

        tools 非空时走原生 function calling；llm_service 会把响应里的 tool_calls
        归一化成 [{id, name, arguments(dict)}] 并附带 assistant_message 供回灌。
        """
        import urllib.request
        import urllib.error

        payload = {
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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

    @staticmethod
    def _compact_for_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
        """压缩工具结果再喂给 LLM：曲线数组只留点数，避免白烧 token"""
        try:
            p = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            return payload
        d = p.get("data")
        if isinstance(d, dict):
            arrays = d.get("arrays")
            if isinstance(arrays, dict):
                d["arrays"] = {
                    k: (f"<{len(v)} points, omitted>" if isinstance(v, list) else v)
                    for k, v in arrays.items()
                }
        return p

    @staticmethod
    def _summarize_tool_result(payload: Dict[str, Any]) -> str:
        """生成给人看的工具结果摘要（写入 steps[].observation）"""
        if not payload.get("success"):
            return f"失败: {payload.get('error')}"

        d = payload.get("data")
        meta = payload.get("metadata") or {}
        bits: List[str] = []

        # 不同工具的 data 形状不同：实验类返回 dict，get_qubits/list_experiments
        # 返回的是 list。这里必须按类型分派，否则 list.get 会直接抛异常。
        if isinstance(d, list):
            bits.append(f"返回 {len(d)} 项")
        elif isinstance(d, dict):
            bits.append(f"status={d.get('status') or 'ok'}")
            if d.get("target"):
                bits.append(f"target={d['target']}")
            results = d.get("results") or {}
            if results:
                bits.append("metrics=" + json.dumps(results, ensure_ascii=False))
            if d.get("error"):
                bits.append(f"error={d['error']}")
        elif d is not None:
            bits.append(str(d)[:200])

        if meta.get("synthetic"):
            bits.append("警告: 该 qubit 无历史数据，以下为合成数据")
        elif meta.get("simulated"):
            bits.append("(离线模拟)")

        stdout = (meta.get("stdout") or "").strip()
        if stdout:
            tail = [ln for ln in stdout.splitlines() if ln.strip()][-1:]
            if tail:
                bits.append(tail[0])

        return " | ".join(bits) if bits else "ok"

    # metrics 的 key -> (前端显示标签, 缩放系数)
    # 前端 AgentChatPanel 渲染 results 时走 v.toFixed(4)，因此必须换算成可读量级 ——
    # 否则 T1=5.56e-5（秒）会被显示成 "0.0001"，精度全丢。
    _METRIC_DISPLAY = {
        "t1": ("T1 (µs)", 1e6),
        "t2": ("T2 (µs)", 1e6),
        "peak_freq": ("峰值频率 (GHz)", 1e-9),
        "peak_value": ("峰值幅度", 1.0),
        "gate_fidelity": ("门保真度", 1.0),
        "average_fidelity": ("平均保真度", 1.0),
        "swap_fidelity": ("SWAP 保真度", 1.0),
        "error_per_cycle": ("单周期误差", 1.0),
        "separation": ("IQ 分离度", 1.0),
        "pi_amplitude": ("π 脉冲幅度", 1.0),
        "optimal_drag": ("最优 DRAG", 1.0),
    }

    @staticmethod
    def _finalize_response(result: Dict[str, Any]) -> str:
        """组装最终回复文本。

        合成数据时**强制**补一条提示，而不是依赖模型自觉说明 ——
        这是给用户看的关键免责信息。
        """
        text = (result.get("response") or "").strip()
        if result.get("synthetic") and "合成" not in text:
            warn = ("⚠️ 该 qubit 在离线数据中没有对应的实验历史记录，"
                    "以下结果由模拟器按通用参数合成，仅供流程验证，不代表真实硬件测量。")
            text = f"{warn}\n\n{text}" if text else warn
        return text

    @classmethod
    def _to_display_results(cls, metrics: Dict[str, Any]) -> Dict[str, float]:
        """把工具返回的 metrics 换算成前端可直接显示的量"""
        out: Dict[str, float] = {}
        for k, v in (metrics or {}).items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            label, scale = cls._METRIC_DISPLAY.get(k, (k, 1.0))
            try:
                out[label] = round(float(v) * scale, 6)
            except Exception:
                continue
        return out

    def _react_reason(self, task: AgentTask) -> Dict[str, Any]:
        """ReAct 推理循环（原生 function calling 版本）

        旧实现依赖文本 "Action: tool(args)" 协议 + eval 解析参数，但 system prompt
        从未教会模型这个格式 —— 模型只会吐它原生的 tool-call 标记，解析不到就被当成
        最终答案返回，导致工具从不执行。现改为把 registry 里的 OpenAI 工具定义直接
        交给模型，用响应中的 tool_calls 驱动循环。
        """
        max_iterations = 10
        iteration = 0

        tools = self._registry.get_definitions()
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task.message},
        ]

        # 汇集给前端的结果（steps 已在 task.steps 里，这里收数值与图表）
        collected_metrics: Dict[str, Any] = {}
        collected_charts: List[str] = []
        saw_synthetic = False
        saw_simulated = False

        def _final(success: bool, **extra) -> Dict[str, Any]:
            return {
                "success": success,
                "iterations": iteration,
                "messages": [{"role": m.get("role"), "content": m.get("content") or ""}
                             for m in messages if m.get("role") in ("user", "assistant")],
                "metrics": collected_metrics,
                "charts": collected_charts,
                "synthetic": saw_synthetic,
                "simulated": saw_simulated,
                **extra,
            }

        while iteration < max_iterations:
            iteration += 1
            _log(f"ReAct iteration {iteration}/{max_iterations}")

            response = self._call_llm(messages, model=task.model, tools=tools)
            if response.get("error"):
                task.steps.append({"tool": "(llm)", "input": {}, "observation": response["error"]})
                return _final(False, error=response["error"])

            content = response.get("content") or ""
            tool_calls = response.get("tool_calls") or []

            if not tool_calls:
                # 没有工具调用 —— 这就是最终回复
                task.messages.append(AgentMessage(role="assistant", content=content))
                return _final(True, response=content)

            _log(f"Model requested {len(tool_calls)} tool call(s)")

            # 协议要求：带 tool_calls 的 assistant 消息必须原样回灌，
            # 其后紧跟 role="tool" 的结果消息
            assistant_msg = response.get("assistant_message")
            if not assistant_msg:
                assistant_msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": tc.get("id") or "",
                            "type": "function",
                            "function": {
                                "name": tc.get("name") or "",
                                "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            messages.append(assistant_msg)
            task.messages.append(AgentMessage(role="assistant", content=content))

            for tc in tool_calls:
                name = tc.get("name") or ""
                args = tc.get("arguments") or {}

                if tc.get("parse_error"):
                    payload: Dict[str, Any] = {
                        "success": False,
                        "error": f"Invalid tool arguments JSON: {tc['parse_error']}",
                        "arguments_raw": tc.get("arguments_raw"),
                    }
                else:
                    _log(f"Executing tool: {name}({json.dumps(args, ensure_ascii=False)})")
                    try:
                        payload = self._registry.execute(name, args).to_dict()
                    except Exception as e:
                        payload = {"success": False, "error": f"Tool execution error: {e}"}

                task.steps.append({
                    "tool": name,
                    "input": args,
                    "observation": self._summarize_tool_result(payload),
                })

                # 汇集数值与图表（供前端 results / charts）
                if payload.get("success"):
                    d = payload.get("data") or {}
                    meta = payload.get("metadata") or {}
                    if isinstance(d, dict):
                        metrics = d.get("results")
                        if isinstance(metrics, dict) and metrics:
                            collected_metrics.update(metrics)
                        for p in (d.get("plots") or []):
                            path = p.get("path") if isinstance(p, dict) else p
                            if path and path not in collected_charts:
                                collected_charts.append(path)
                    if meta.get("synthetic"):
                        saw_synthetic = True
                    if meta.get("simulated"):
                        saw_simulated = True

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id") or "",
                    "content": json.dumps(self._compact_for_llm(payload), ensure_ascii=False),
                })

        return _final(False, error="Max iterations reached")

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
                "response": self._finalize_response(result),
                "error": result.get("error"),
                "iterations": result.get("iterations", 0),
                "messages": result.get("messages", []),
                # 前端 AgentChatPanel 期望的三个字段（此前一个都没返回，
                # 导致步骤区与结果卡片永远不渲染）
                "steps": task.steps,
                "results": self._to_display_results(result.get("metrics") or {}),
                "charts": result.get("charts") or [],
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
