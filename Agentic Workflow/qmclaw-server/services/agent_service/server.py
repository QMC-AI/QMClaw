"""
services/agent_service/server.py - Agent 服务

提供量子智能体功能：
- QuantumAgent - 量子实验执行智能体
- ReAct 推理引擎
- 工具注册和管理
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
    context: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    messages: List[AgentMessage] = field(default_factory=list)


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
    """

    def __init__(self, port: int = 3005):
        cfg = ServiceConfig(
            name="agent_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # 工具注册表
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

        _log("Agent service initialized")

    def _setup_paths(self):
        """设置 Python 路径"""
        import sys as _sys
        from pathlib import Path as _Path

        root = _Path(__file__).parent.parent.parent.parent
        sq_workflow = root / "measure_scripts" / "measure_scripts" / "sq_workflow"
        measure_scripts = root / "measure_scripts" / "measure_scripts"

        for _path in [str(sq_workflow), str(measure_scripts)]:
            if _path not in _sys.path:
                _sys.path.insert(0, _path)

    def register_tool(self, tool: Tool):
        """注册工具"""
        self._tools[tool.name] = tool
        _log(f"Registered tool: {tool.name}")

    def set_quantum_client(self, client):
        """设置测控服务客户端"""
        self._quantum_client = client

        # 注册工具
        self.register_tool(QuantumExperimentTool(self._execute_experiment))
        self.register_tool(GetQubitsTool(self._get_qubits))
        self.register_tool(ListExperimentsTool(self._list_experiments))

        _log("Quantum client set, tools registered")

    def _execute_experiment(self, code: str) -> Dict[str, Any]:
        """执行实验"""
        if self._quantum_client:
            # 直接调用 LabRAD
            with self._quantum_client.lock:
                sq = self._quantum_client.sq
                s = self._quantum_client.s
                local_vars = {"sq": sq, "s": s, "__builtins__": __builtins__}
                exec_result = {}
                exec(code, local_vars, exec_result)
                return exec_result
        return {"error": "Quantum client not connected"}

    def _get_qubits(self) -> List[Dict[str, Any]]:
        """获取量子比特"""
        if self._quantum_client:
            return self._quantum_client.get_qubits()
        return []

    def _list_experiments(self) -> List[Dict[str, str]]:
        """列出实验"""
        if self._quantum_client:
            return self._quantum_client.list_experiments()
        return []

    def _call_llm(self, messages: List[Dict], model: str = "minimax", temperature: float = 0.7) -> Dict[str, Any]:
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

            # 调用 LLM
            response = self._call_llm(messages)
            if "error" in response:
                return {"error": response["error"]}

            assistant_content = response.get("content", "")

            # 添加助手回复
            messages.append({"role": "assistant", "content": assistant_content})
            task.messages.append(AgentMessage(role="assistant", content=assistant_content))

            # 检查是否完成（没有工具调用）
            if "Action:" not in assistant_content and "Action:" not in assistant_content:
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
                continue

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

        return {
            "success": False,
            "error": "Max iterations reached",
            "iterations": iteration,
            "messages": [{"role": m.role, "content": m.content} for m in task.messages],
        }

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理 Agent 请求"""
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
        else:
            raise ValueError(f"Unknown path: {path}")

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
        mode = data.get("mode", "react")
        context = data.get("context", {})
        task_id = data.get("task_id", f"task_{int(time.time() * 1000)}")

        if not message:
            return {"error": "message is required"}

        _log(f"Processing chat: task_id={task_id}, mode={mode}")

        # 创建任务
        task = AgentTask(
            task_id=task_id,
            message=message,
            mode=mode,
            context=context,
        )

        with self._tasks_lock:
            self._tasks[task_id] = task
            task.status = TaskStatus.RUNNING

        try:
            # 执行推理
            if mode == "react":
                result = self._react_reason(task)
            else:
                result = {"error": f"Unknown mode: {mode}"}

            task.status = TaskStatus.COMPLETED if result.get("success") else TaskStatus.FAILED
            task.result = result
            task.completed_at = time.time()

            return {
                "success": result.get("success", False),
                "task_id": task_id,
                "response": result.get("response", ""),
                "error": result.get("error"),
                "iterations": result.get("iterations", 0),
                "messages": result.get("messages", []),
            }

        except Exception as e:
            _log(f"Chat error: {e}\n{traceback.format_exc()}")
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
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
        return {"tools": tools, "count": len(tools)}

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
