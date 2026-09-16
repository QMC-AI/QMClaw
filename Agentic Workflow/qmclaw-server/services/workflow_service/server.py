"""
services/workflow_service/server.py - 工作流服务

提供工作流执行和管理功能：
- 节点调度
- 依赖解析
- 并行执行
- 结果收集
- 运行历史持久化
"""

import json
import time
import threading
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[workflow_service] {msg}")


# ── 历史记录数据目录配置 ─────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).parent.parent.parent
DATA_DIR = ROOT_DIR / "data" / "workflow-runs"


def _ensure_data_dir():
    """确保数据目录存在"""
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _log(f"Created data directory: {DATA_DIR}")


class NodeStatus(Enum):
    """节点状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WorkflowNode:
    """工作流节点"""
    id: str
    type: str  # experiment, analysis, decision, llm, etc.
    config: Dict[str, Any] = field(default_factory=dict)
    depends: List[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class Workflow:
    """工作流"""
    id: str
    name: str
    nodes: Dict[str, WorkflowNode] = field(default_factory=dict)
    status: NodeStatus = NodeStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    context: Dict[str, Any] = field(default_factory=dict)


# ── 历史记录数据类 ────────────────────────────────────────────────────────────────

@dataclass
class WorkflowRunNodeInput:
    """工作流运行节点输入"""
    config: Dict[str, Any] = field(default_factory=dict)
    resolvedContext: Dict[str, Any] = field(default_factory=dict)
    upstreamResults: Optional[Dict[str, Any]] = None


@dataclass
class WorkflowRunNodeOutput:
    """工作流运行节点输出"""
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    metrics: Optional[Dict[str, float]] = None
    plotPath: Optional[str] = None
    conversation: Optional[Dict[str, Any]] = None
    recommendations: Optional[List[Any]] = None
    symptom: Optional[str] = None
    reasoning: Optional[str] = None
    matchedRules: Optional[List[str]] = None


@dataclass
class WorkflowRunNode:
    """工作流运行节点"""
    nodeId: str
    nodeType: str
    status: str  # pending/running/completed/failed/skipped
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None
    duration: Optional[int] = None  # milliseconds
    input: WorkflowRunNodeInput = field(default_factory=WorkflowRunNodeInput)
    output: WorkflowRunNodeOutput = field(default_factory=WorkflowRunNodeOutput)


@dataclass
class WorkflowRun:
    """工作流运行记录"""
    id: str  # run_{workflowId}_{timestamp}
    workflowId: str
    workflowName: str
    status: str  # completed/failed/cancelled
    startedAt: str  # ISO timestamp
    completedAt: str  # ISO timestamp
    totalDuration: int  # milliseconds
    context: Dict[str, str] = field(default_factory=dict)
    nodes: List[Dict[str, Any]] = field(default_factory=list)


class WorkflowService(BaseService):
    """工作流服务

    核心功能:
    - 工作流创建和执行
    - 节点依赖解析
    - 并行执行调度
    - 结果收集
    - 运行历史持久化
    """

    def __init__(self, port: int = 3008):
        cfg = ServiceConfig(
            name="workflow_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # 工作流存储
        self._workflows: Dict[str, Workflow] = {}
        self._workflows_lock = threading.Lock()

        # 执行器
        self._executors: Dict[str, callable] = {}
        self._execution_lock = threading.Lock()

        # 历史记录锁
        self._runs_lock = threading.Lock()

        # 测控服务地址
        self._quantum_service_url = "http://localhost:3003"
        self._analysis_service_url = "http://localhost:3004"
        self._llm_service_url = "http://localhost:3006"

        # 确保数据目录存在
        _ensure_data_dir()

        _log("Workflow service initialized")

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

    def register_executor(self, node_type: str, executor: callable):
        """注册节点执行器"""
        self._executors[node_type] = executor
        _log(f"Registered executor for: {node_type}")

    def _resolve_dependencies(self, workflow: Workflow) -> Dict[str, Set[str]]:
        """解析节点依赖关系"""
        dependency_graph: Dict[str, Set[str]] = {}  # node_id -> set of dependent node_ids

        for node_id, node in workflow.nodes.items():
            dependency_graph[node_id] = set()
            for dep_id in node.depends:
                if dep_id in workflow.nodes:
                    dependency_graph[node_id].add(dep_id)

        return dependency_graph

    def _get_ready_nodes(self, workflow: Workflow) -> List[str]:
        """获取就绪的节点（所有依赖已完成）"""
        ready = []
        for node_id, node in workflow.nodes.items():
            if node.status != NodeStatus.PENDING:
                continue
            # 检查所有依赖是否完成
            deps_completed = all(
                workflow.nodes[dep_id].status == NodeStatus.COMPLETED
                for dep_id in node.depends
                if dep_id in workflow.nodes
            )
            if deps_completed:
                ready.append(node_id)
        return ready

    def _execute_node(self, workflow: Workflow, node_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个节点"""
        node = workflow.nodes[node_id]
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()

        _log(f"Executing node {node_id} (type={node.type})")

        try:
            # 获取执行器
            executor = self._executors.get(node.type)
            if not executor:
                # 默认执行器：通过 HTTP 调用服务
                result = self._execute_via_http(node)
            else:
                result = executor(node, context)

            node.result = result
            node.status = NodeStatus.COMPLETED
            node.completed_at = time.time()

            # 更新上下文
            context[node_id] = result

            _log(f"Node {node_id} completed")
            return result

        except Exception as e:
            _log(f"Node {node_id} failed: {e}\n{traceback.format_exc()}")
            node.status = NodeStatus.FAILED
            node.error = str(e)
            node.completed_at = time.time()
            return {"error": str(e)}

    def _execute_via_http(self, node: WorkflowNode) -> Dict[str, Any]:
        """通过 HTTP 调用服务执行节点"""
        import urllib.request
        import urllib.error

        node_type = node.type
        config = node.config

        if node_type == "experiment":
            url = f"{self._quantum_service_url}/execute"
            payload = {"code": config.get("code", "")}
        elif node_type == "analysis":
            url = f"{self._analysis_service_url}/analyze"
            payload = {"type": config.get("analysis_type", "basic")}
        elif node_type == "llm":
            url = f"{self._llm_service_url}/chat"
            payload = {
                "messages": config.get("messages", []),
                "model": config.get("model", "minimax"),
                "temperature": config.get("temperature", 0.7),
            }
        else:
            return {"error": f"Unknown node type: {node_type}"}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result
        except Exception as e:
            return {"error": str(e)}

    # ── 历史记录 CRUD ───────────────────────────────────────────────────────────

    def _list_runs(self, workflow_id: Optional[str] = None, workflow_name: Optional[str] = None) -> List[Dict]:
        """列出运行记录，支持按 workflowId 或 workflowName 筛选"""
        with self._runs_lock:
            runs = []
            for file_path in DATA_DIR.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        run = json.load(f)
                    # 筛选
                    if workflow_id and run.get("workflowId") != workflow_id:
                        if workflow_name and run.get("workflowName") != workflow_name:
                            continue
                    runs.append(run)
                except Exception as e:
                    _log(f"Failed to read run file {file_path}: {e}")
            # 按完成时间倒序
            runs.sort(key=lambda r: r.get("completedAt", ""), reverse=True)
            return runs

    def _get_run(self, run_id: str) -> Optional[Dict]:
        """获取单条运行记录"""
        with self._runs_lock:
            file_path = DATA_DIR / f"{run_id}.json"
            if not file_path.exists():
                return None
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                _log(f"Failed to read run {run_id}: {e}")
                return None

    def _save_run(self, run: Dict) -> Dict:
        """保存运行记录"""
        with self._runs_lock:
            file_path = DATA_DIR / f"{run['id']}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(run, f, ensure_ascii=False, indent=2)
            _log(f"Saved run: {run['id']}")
            return run

    def _delete_run(self, run_id: str) -> bool:
        """删除运行记录"""
        with self._runs_lock:
            file_path = DATA_DIR / f"{run_id}.json"
            if not file_path.exists():
                return False
            file_path.unlink()
            _log(f"Deleted run: {run_id}")
            return True

    def _delete_runs_by_workflow(self, workflow_id: str) -> int:
        """删除某工作流的所有运行记录"""
        with self._runs_lock:
            deleted = 0
            for file_path in DATA_DIR.glob("*.json"):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        run = json.load(f)
                    if run.get("workflowId") == workflow_id:
                        file_path.unlink()
                        deleted += 1
                except Exception:
                    pass
            _log(f"Deleted {deleted} runs for workflow: {workflow_id}")
            return deleted

    def _get_stats(self, workflow_id: str) -> Dict:
        """获取工作流统计信息"""
        runs = self._list_runs(workflow_id=workflow_id)
        if not runs:
            return {
                "totalRuns": 0,
                "completedRuns": 0,
                "failedRuns": 0,
                "avgDuration": 0,
            }
        completed = [r for r in runs if r.get("status") == "completed"]
        failed = [r for r in runs if r.get("status") == "failed"]
        durations = [r.get("totalDuration", 0) for r in runs if r.get("totalDuration", 0) > 0]
        avg_duration = sum(durations) / len(durations) if durations else 0
        return {
            "totalRuns": len(runs),
            "completedRuns": len(completed),
            "failedRuns": len(failed),
            "avgDuration": avg_duration,
            "lastRunAt": runs[0].get("completedAt") if runs else None,
        }

    def _persist_workflow_run(self, workflow_id: str, context: Dict[str, Any]):
        """将工作流运行结果持久化到磁盘"""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            _log(f"Cannot persist: workflow {workflow_id} not found")
            return

        started_at = workflow.created_at * 1000  # 转换为毫秒
        completed_at = (workflow.completed_at or time.time()) * 1000
        run_id = f"run_{workflow_id}_{int(started_at)}"

        # 构建节点列表
        nodes = []
        for node_id, node in workflow.nodes.items():
            # 从上下文中提取节点输出
            node_output = context.get(node_id, {})
            if isinstance(node_output, dict):
                result = node_output
            else:
                result = {"stdout": str(node_output)}

            nodes.append({
                "nodeId": node_id,
                "nodeType": node.type,
                "status": node.status.value if hasattr(node.status, 'value') else str(node.status),
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(workflow.created_at)),
                "completedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(workflow.completed_at or time.time())),
                "duration": int((node.completed_at - node.started_at) * 1000) if node.started_at and node.completed_at else None,
                "input": {
                    "config": node.config,
                    "resolvedContext": {},
                },
                "output": {
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "error": result.get("error"),
                    "metrics": result.get("metrics"),
                    "plotPath": result.get("plotPath"),
                    "conversation": result.get("conversation"),
                    "recommendations": result.get("recommendations"),
                    "symptom": result.get("symptom"),
                    "reasoning": result.get("reasoning"),
                    "matchedRules": result.get("matchedRules"),
                },
            })

        run = {
            "id": run_id,
            "workflowId": workflow_id,
            "workflowName": workflow.name,
            "status": workflow.status.value if hasattr(workflow.status, 'value') else str(workflow.status),
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(workflow.created_at)),
            "completedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(workflow.completed_at or time.time())),
            "totalDuration": int(completed_at - started_at),
            "context": workflow.context,
            "nodes": nodes,
        }

        self._save_run(run)
        _log(f"Persisted workflow run: {run_id}")

    def _run_workflow_async(self, workflow_id: str, context: Dict[str, Any]):
        """异步执行工作流"""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return

        _log(f"Starting workflow {workflow_id}")

        # 初始化工作流上下文（确保在 except 块中可用）
        workflow_context = dict(context)

        try:
            # 拓扑排序确定执行顺序
            sorted_nodes = self._topological_sort(workflow)

            # 执行节点
            for node_id in sorted_nodes:
                # 检查是否已取消
                if workflow.status == NodeStatus.CANCELLED:
                    break

                # 等待依赖完成
                self._wait_for_dependencies(workflow, node_id)

                # 执行节点
                result = self._execute_node(workflow, node_id, workflow_context)

                # 如果节点失败，工作流失败
                if workflow.nodes[node_id].status == NodeStatus.FAILED:
                    workflow.status = NodeStatus.FAILED
                    break

            # 检查工作流是否完成
            if workflow.status == NodeStatus.PENDING:
                all_completed = all(
                    node.status in (NodeStatus.COMPLETED, NodeStatus.CANCELLED)
                    for node in workflow.nodes.values()
                )
                workflow.status = NodeStatus.COMPLETED if all_completed else NodeStatus.FAILED

        except Exception as e:
            _log(f"Workflow {workflow_id} error: {e}")
            workflow.status = NodeStatus.FAILED

        workflow.completed_at = time.time()
        _log(f"Workflow {workflow_id} finished: {workflow.status.value}")

        # 持久化运行记录
        self._persist_workflow_run(workflow_id, workflow_context)

    def _topological_sort(self, workflow: Workflow) -> List[str]:
        """拓扑排序"""
        visited = set()
        result = []

        def visit(node_id: str):
            if node_id in visited:
                return
            visited.add(node_id)
            # 先访问依赖
            for dep_id in workflow.nodes[node_id].depends:
                if dep_id in workflow.nodes:
                    visit(dep_id)
            result.append(node_id)

        for node_id in workflow.nodes:
            visit(node_id)

        return result

    def _wait_for_dependencies(self, workflow: Workflow, node_id: str):
        """等待依赖完成（用于并行执行场景）"""
        # 在单线程模式下，这里可以添加简单的轮询
        # 在实际并行执行中，可以使用事件/信号量
        pass

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理工作流请求"""
        if path == "/health":
            return self._handle_health()
        elif path == "/workflows":
            return self._handle_workflows(data, method)
        elif path == "/workflows/create":
            return self._handle_create_workflow(data)
        elif path == "/workflows/run":
            return self._handle_run_workflow(data)
        elif path == "/workflows/status":
            return self._handle_workflow_status(data)
        elif path == "/workflows/cancel":
            return self._handle_cancel_workflow(data)
        # 历史记录相关路由 - GET
        elif path == "/runs" or path.startswith("/runs?"):
            return self._handle_list_runs(query)
        elif path.startswith("/runs/"):
            # /runs/<run_id> 或 /runs/stats/<workflow_id>
            parts = path.split("/")
            if len(parts) >= 3:
                if parts[2] == "stats" and len(parts) >= 4:
                    return self._handle_get_stats(parts[3])
                elif parts[2] == "workflow" and len(parts) >= 4:
                    return self._handle_get_stats(parts[3])  # workflow stats
                else:
                    return self._handle_get_run(parts[2])
            raise ValueError(f"Invalid path: {path}")
        elif path == "/runs/delete":
            return self._handle_delete_run(data)
        # 历史记录相关路由 - DELETE
        elif method == "DELETE" and path.startswith("/runs/workflow/"):
            parts = path.split("/")
            if len(parts) >= 4:
                return self._handle_delete_runs_by_workflow(parts[3])
            raise ValueError(f"Invalid path: {path}")
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy",
            "service": "workflow_service",
            "workflow_count": len(self._workflows),
            "executor_types": list(self._executors.keys()),
        }

    def _handle_workflows(self, data: Dict[str, Any], method: str) -> Dict[str, Any]:
        """获取工作流列表"""
        with self._workflows_lock:
            workflows = []
            for wf_id, wf in self._workflows.items():
                workflows.append({
                    "id": wf.id,
                    "name": wf.name,
                    "status": wf.status.value,
                    "node_count": len(wf.nodes),
                    "created_at": wf.created_at,
                    "completed_at": wf.completed_at,
                })
            return {"workflows": workflows, "count": len(workflows)}

    def _handle_create_workflow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """创建工作流"""
        name = data.get("name", "Unnamed Workflow")
        nodes_data = data.get("nodes", [])

        workflow_id = f"wf_{int(time.time() * 1000)}"

        # 创建节点
        nodes = {}
        for node_data in nodes_data:
            node_id = node_data.get("id", f"node_{len(nodes)}")
            nodes[node_id] = WorkflowNode(
                id=node_id,
                type=node_data.get("type", "unknown"),
                config=node_data.get("config", {}),
                depends=node_data.get("depends", []),
            )

        workflow = Workflow(id=workflow_id, name=name, nodes=nodes)

        with self._workflows_lock:
            self._workflows[workflow_id] = workflow

        _log(f"Created workflow {workflow_id} with {len(nodes)} nodes")

        return {
            "success": True,
            "workflow_id": workflow_id,
            "name": name,
            "node_count": len(nodes),
        }

    def _handle_run_workflow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """运行工作流"""
        workflow_id = data.get("workflow_id")
        context = data.get("context", {})

        if not workflow_id:
            return {"error": "workflow_id is required"}

        with self._workflows_lock:
            workflow = self._workflows.get(workflow_id)
            if not workflow:
                return {"error": "Workflow not found"}

            workflow.status = NodeStatus.RUNNING

        # 在后台线程执行
        thread = threading.Thread(
            target=self._run_workflow_async,
            args=(workflow_id, context)
        )
        thread.daemon = True
        thread.start()

        return {
            "success": True,
            "workflow_id": workflow_id,
            "status": "running",
        }

    def _handle_workflow_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取工作流状态"""
        workflow_id = data.get("workflowId") or data.get("workflow_id")

        if not workflow_id:
            return {"error": "workflowId is required"}

        with self._workflows_lock:
            workflow = self._workflows.get(workflow_id)
            if not workflow:
                return {"error": "Workflow not found"}

            nodes = []
            for node_id, node in workflow.nodes.items():
                nodes.append({
                    "id": node.id,
                    "type": node.type,
                    "status": node.status.value,
                    "depends": node.depends,
                    "result": node.result,
                    "error": node.error,
                    "started_at": node.started_at,
                    "completed_at": node.completed_at,
                })

            return {
                "id": workflow.id,
                "name": workflow.name,
                "status": workflow.status.value,
                "nodes": nodes,
                "created_at": workflow.created_at,
                "completed_at": workflow.completed_at,
            }

    def _handle_cancel_workflow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """取消工作流"""
        workflow_id = data.get("workflowId") or data.get("workflow_id")

        if not workflow_id:
            return {"error": "workflowId is required"}

        with self._workflows_lock:
            workflow = self._workflows.get(workflow_id)
            if not workflow:
                return {"error": "Workflow not found"}

            workflow.status = NodeStatus.CANCELLED

            # 取消所有待执行节点
            for node in workflow.nodes.values():
                if node.status == NodeStatus.PENDING:
                    node.status = NodeStatus.CANCELLED

        return {"success": True, "workflow_id": workflow_id}

    # ── 历史记录 API 处理函数 ─────────────────────────────────────────────────────

    def _handle_list_runs(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """GET /runs?workflowId=xxx&workflowName=xxx"""
        workflow_id = None
        workflow_name = None
        if "workflowId" in query:
            workflow_id = query["workflowId"][0]
        if "workflowName" in query:
            workflow_name = query["workflowName"][0]
        runs = self._list_runs(workflow_id=workflow_id, workflow_name=workflow_name)
        return {"runs": runs, "count": len(runs)}

    def _handle_get_run(self, run_id: str) -> Dict[str, Any]:
        """GET /runs/<id>"""
        run = self._get_run(run_id)
        if run is None:
            return {"error": "Run not found"}
        return run

    def _handle_delete_run(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """DELETE /runs/delete (body: {runId: xxx})"""
        run_id = data.get("runId")
        if not run_id:
            return {"error": "runId is required"}
        deleted = self._delete_run(run_id)
        return {"success": deleted, "runId": run_id}

    def _handle_delete_runs_by_workflow(self, workflow_id: str) -> Dict[str, Any]:
        """DELETE /runs/workflow/<workflowId>"""
        deleted = self._delete_runs_by_workflow(workflow_id)
        return {"success": True, "workflowId": workflow_id, "deleted": deleted}

    def _handle_get_stats(self, workflow_id: str) -> Dict[str, Any]:
        """GET /runs/stats/<workflowId>"""
        stats = self._get_stats(workflow_id)
        return stats

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        return {
            "status": "healthy",
            "service": "workflow_service",
            "workflow_count": len(self._workflows),
        }


def main():
    """主入口"""
    service = WorkflowService(port=3008)
    run_service(service)


if __name__ == "__main__":
    main()
