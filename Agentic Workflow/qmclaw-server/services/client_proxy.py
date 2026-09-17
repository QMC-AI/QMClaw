"""
services/client_proxy.py - 客户端代理模块

供 job_runner.py 等客户端调用微服务的统一接口。
避免直接调用微服务时重复实现 HTTP 逻辑。
"""

import os
import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional


# 服务端点配置
SERVICES = {
    "llm": {"host": "localhost", "port": 3006},
    "quantum": {"host": "localhost", "port": 3003},
    "analysis": {"host": "localhost", "port": 3004},
    "agent": {"host": "localhost", "port": 3005},
    "image": {"host": "localhost", "port": 3007},
    "workflow": {"host": "localhost", "port": 3008},
    "task_queue": {"host": "localhost", "port": 3009},
}


def _call_service(
    service: str,
    path: str,
    method: str = "GET",
    data: Any = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """调用微服务"""
    cfg = SERVICES.get(service)
    if not cfg:
        return {"error": f"Unknown service: {service}"}

    url = f"http://{cfg['host']}:{cfg['port']}{path}"

    try:
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    except urllib.error.URLError as e:
        return {"error": f"Service {service} unreachable: {e}"}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            return {"error": f"HTTP {e.code}: {err_body}"}
        except:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


# ── Quantum Service 代理 ───────────────────────────────────────────────────────

class QuantumProxy:
    """Quantum 服务代理"""

    @staticmethod
    def status() -> Dict[str, Any]:
        """获取连接状态"""
        return _call_service("quantum", "/status")

    @staticmethod
    def sessions() -> Dict[str, Any]:
        """获取会话列表"""
        return _call_service("quantum", "/sessions")

    @staticmethod
    def session_tree(max_depth: int = 5) -> Dict[str, Any]:
        """获取目录树"""
        return _call_service("quantum", "/session_tree", "POST", {"max_depth": max_depth})

    @staticmethod
    def qubits() -> Dict[str, Any]:
        """获取量子比特列表"""
        return _call_service("quantum", "/qubits")

    @staticmethod
    def qubit_params(name: str) -> Dict[str, Any]:
        """获取量子比特参数"""
        return _call_service("quantum", "/qubit/params", "POST", {"name": name})

    @staticmethod
    def set_qubit_params(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """设置量子比特参数"""
        return _call_service("quantum", "/qubit/set_params", "POST", {"name": name, "params": params})

    @staticmethod
    def datasets(path: Optional[str] = None) -> Dict[str, Any]:
        """获取数据集列表"""
        if path:
            return _call_service("quantum", "/datasets", "POST", {"path": path})
        return _call_service("quantum", "/datasets", "POST", {})

    @staticmethod
    def experiments() -> Dict[str, Any]:
        """获取实验列表"""
        return _call_service("quantum", "/experiments")

    @staticmethod
    def switch_session(session_path: List[str]) -> Dict[str, Any]:
        """切换会话"""
        return _call_service("quantum", "/switch_session", "POST", {"session_path": session_path})

    @staticmethod
    def execute(code: str, timeout: int = 300) -> Dict[str, Any]:
        """执行实验代码"""
        return _call_service("quantum", "/execute", "POST", {"code": code}, timeout=timeout)

    @staticmethod
    def health() -> Dict[str, Any]:
        """健康检查"""
        return _call_service("quantum", "/health")


# ── Analysis Service 代理 ──────────────────────────────────────────────────────

class AnalysisProxy:
    """Analysis 服务代理"""

    @staticmethod
    def plot(command: str = "", dataset_index: int = -1) -> Dict[str, Any]:
        """绘制最新数据集"""
        return _call_service("analysis", "/plot", "POST", {
            "command": command,
            "dataset_index": dataset_index,
        })

    @staticmethod
    def plot_historical(name: str, path: List[str], command: str = "") -> Dict[str, Any]:
        """绘制历史数据集"""
        return _call_service("analysis", "/plot/historical", "POST", {
            "name": name,
            "path": path,
            "command": command,
        })

    @staticmethod
    def stats(dataset_index: int = -1) -> Dict[str, Any]:
        """数据统计"""
        return _call_service("analysis", "/stats", "POST", {"dataset_index": dataset_index})

    @staticmethod
    def datasets() -> Dict[str, Any]:
        """获取数据集列表"""
        return _call_service("analysis", "/datasets", "GET")

    @staticmethod
    def analyze(analysis_type: str = "basic", **kwargs) -> Dict[str, Any]:
        """数据分析"""
        return _call_service("analysis", "/analyze", "POST", {
            "type": analysis_type,
            **kwargs,
        })

    @staticmethod
    def health() -> Dict[str, Any]:
        """健康检查"""
        return _call_service("analysis", "/health")


# ── LLM Service 代理 ───────────────────────────────────────────────────────────

class LLMProxy:
    """LLM 服务代理"""

    @staticmethod
    def chat(
        messages: List[Dict[str, str]],
        model: str = "minimax",
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """聊天完成"""
        return _call_service("llm", "/chat", "POST", {
            "messages": messages,
            "model": model,
            "temperature": temperature,
        }, timeout=120)

    @staticmethod
    def models() -> Dict[str, Any]:
        """获取模型列表"""
        return _call_service("llm", "/models")

    @staticmethod
    def stats() -> Dict[str, Any]:
        """获取统计信息"""
        return _call_service("llm", "/stats")

    @staticmethod
    def health() -> Dict[str, Any]:
        """健康检查"""
        return _call_service("llm", "/health")


# ── Agent Service 代理 ─────────────────────────────────────────────────────────

class AgentProxy:
    """Agent 服务代理"""

    @staticmethod
    def chat(
        message: str,
        mode: str = "react",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Agent 对话"""
        return _call_service("agent", "/chat", "POST", {
            "message": message,
            "mode": mode,
            "context": context or {},
        }, timeout=300)

    @staticmethod
    def chat_stream(
        message: str,
        mode: str = "react",
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Agent 流式对话"""
        return _call_service("agent", "/chat/stream", "POST", {
            "message": message,
            "mode": mode,
            "context": context or {},
        }, timeout=300)

    @staticmethod
    def tasks(status: Optional[str] = None) -> Dict[str, Any]:
        """获取任务列表"""
        path = "/tasks" if not status else f"/tasks?status={status}"
        return _call_service("agent", path)

    @staticmethod
    def tools() -> Dict[str, Any]:
        """获取工具列表"""
        return _call_service("agent", "/tools")

    @staticmethod
    def health() -> Dict[str, Any]:
        """健康检查"""
        return _call_service("agent", "/health")


# ── Workflow Service 代理 ──────────────────────────────────────────────────────

class WorkflowProxy:
    """Workflow 服务代理"""

    @staticmethod
    def list() -> Dict[str, Any]:
        """获取工作流列表"""
        return _call_service("workflow", "/list")

    @staticmethod
    def create(
        name: str,
        nodes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """创建工作流"""
        return _call_service("workflow", "/create", "POST", {
            "name": name,
            "nodes": nodes,
        })

    @staticmethod
    def run(workflow_id: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """运行工作流"""
        return _call_service("workflow", "/run", "POST", {
            "workflow_id": workflow_id,
            "context": context or {},
        }, timeout=600)

    @staticmethod
    def status(workflow_id: str) -> Dict[str, Any]:
        """获取工作流状态"""
        return _call_service("workflow", "/status", "POST", {
            "workflow_id": workflow_id,
        })

    @staticmethod
    def cancel(workflow_id: str) -> Dict[str, Any]:
        """取消工作流"""
        return _call_service("workflow", "/cancel", "POST", {
            "workflow_id": workflow_id,
        })

    @staticmethod
    def health() -> Dict[str, Any]:
        """健康检查"""
        return _call_service("workflow", "/health")


# ── Task Queue Service 代理 ────────────────────────────────────────────────────

class TaskQueueProxy:
    """Task Queue 服务代理"""

    @staticmethod
    def submit(
        task_type: str,
        payload: Dict[str, Any],
        priority: int = 5,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """提交任务"""
        return _call_service("task_queue", "/submit", "POST", {
            "type": task_type,
            "payload": payload,
            "priority": priority,
            "timeout": timeout,
        })

    @staticmethod
    def status(task_id: str) -> Dict[str, Any]:
        """获取任务状态"""
        return _call_service("task_queue", "/status", "POST", {
            "task_id": task_id,
        })

    @staticmethod
    def list(
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """获取任务列表"""
        params = f"?limit={limit}"
        if status:
            params += f"&status={status}"
        if task_type:
            params += f"&type={task_type}"
        return _call_service("task_queue", f"/list{params}")

    @staticmethod
    def cancel(task_id: str) -> Dict[str, Any]:
        """取消任务"""
        return _call_service("task_queue", "/cancel", "POST", {
            "task_id": task_id,
        })

    @staticmethod
    def retry(task_id: str) -> Dict[str, Any]:
        """重试任务"""
        return _call_service("task_queue", "/retry", "POST", {
            "task_id": task_id,
        })

    @staticmethod
    def stats() -> Dict[str, Any]:
        """获取统计信息"""
        return _call_service("task_queue", "/stats")

    @staticmethod
    def health() -> Dict[str, Any]:
        """健康检查"""
        return _call_service("task_queue", "/health")


# ── 便捷导出 ───────────────────────────────────────────────────────────────────

quantum = QuantumProxy
analysis = AnalysisProxy
llm = LLMProxy
agent = AgentProxy
workflow = WorkflowProxy
task_queue = TaskQueueProxy


def check_service_health(service_name: str) -> bool:
    """检查服务是否健康"""
    health = _call_service(service_name, "/health")
    return "error" not in health


def get_all_health() -> Dict[str, bool]:
    """获取所有服务健康状态"""
    return {
        name: check_service_health(name)
        for name in SERVICES.keys()
    }
