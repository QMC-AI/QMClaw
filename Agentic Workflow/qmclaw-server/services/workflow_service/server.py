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
import os
import re
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
from ..common.trace import ensure_trace as _ensure_trace

# analyze 节点的 command 会被 analysis_service 当 Python 执行，只放行拟合入口
from .dsl.const import ANALYZE_CMD_RE as _ANALYZE_CMD_RE
# S7（2026-11-04）：lqcs 专有逻辑（拼代码 / 跑 job / 解析标记）下沉到适配器，
# server 只负责"节点包装"（取 config、解析 {{}}、校验、套状态）。
from .dsl.backends.lqcs import (
    DATASET_PROBE_POST as _lqcs_probe_post,
    DATASET_PROBE_PRE as _lqcs_probe_pre,
    LqcsAdapter as _LqcsAdapter,
    annotate as _lqcs_annotate,
    build_contrast_code as _contrast_snippet,
    build_experiment_code as _build_experiment_code,
)
from .dsl.pyliteral import py_literal as _py_literal


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[workflow_service] {msg}")


def _status_text(status: Any) -> str:
    """枚举/字符串统一成字符串。

    用字符串比较而不是 `in (NodeStatus.X, ...)`：如果状态是由**另一份模块实例**的
    NodeStatus 赋值的（`-m` 启动时包名推断不一致会出现），枚举成员身份不相等，
    按枚举比较会静默失配。
    """
    return str(getattr(status, "value", status))


# ── 注入到 quantum_service 执行上下文的代码片段 ─────────────────────────────────
#
# 实现在 `dsl/backends/lqcs.py`（S7 已下沉）；这里保留旧名字做兼容，
# 单测与历史调用方仍在引用。
_DATASET_PROBE_PRE = _lqcs_probe_pre
_DATASET_PROBE_POST = _lqcs_probe_post
_DATASET_PROBE_SNIPPET = _DATASET_PROBE_POST


# ── experiment 节点参数校验 ─────────────────────────────────────────────────────
#
# 节点配置最终会被拼成 Python 源码交给 quantum_service 的 exec 执行（见
# `_execute_via_http`）。工作流 JSON 来自前端，也可能来自 LLM 生成的节点，
# 所以 fn / qubit / 参数名必须严格校验，所有取值必须走 repr 生成字面量：
#   - fn 只允许 `sq.<snake_case>`，杜绝 `__import__('os').system(...)`
#   - qubit 只允许字母数字下划线百分号连字符，杜绝 `x', ...)#` 闭合括号
#   - 参数值一律 `repr()`：`json.dumps` 会生成 true/false/null，不是合法 Python
#     （嵌套 dict/list 里含 bool/None 时尤为致命）

_SAFE_FN_RE = re.compile(r"^sq\.[a-z][a-z0-9_]*$")
_SAFE_QUBIT_RE = re.compile(r"^[A-Za-z0-9_%\-]+$")
_SAFE_ARGNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# quantum_service 会把每个比特名注入 exec 的全局变量里（`exec_globals[qname] = qobj`），
# 所以 `sq.iqraw(q2ld3)` 里的 `q2ld3` 必须是**裸标识符**，写成字符串会得到
# `AttributeError: 'str' object has no attribute 'qName'`。
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# `_py_literal` 已下沉到 `dsl/pyliteral.py`（S7），顶部 import 为 `_py_literal`。


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
    SKIPPED = "skipped"
    BUDGET_EXCEEDED = "budget_exceeded"
    NEEDS_HUMAN = "needs_human"
    # P3：task 账本额度耗尽（跨 qwf 累计，与 IR 自身的 BUDGET_EXCEEDED 区分）
    QUOTA_EXCEEDED = "quota_exceeded"


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
    settings: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


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
        # 硬件互斥：同一时刻只允许一个工作流驱动真机（`_execution_lock` 是旧名）
        # 本地锁只在拿不到 task_queue 租约时兜底（队列没起来时系统仍要能用）
        self._hardware_lock = threading.Lock()
        self._execution_lock = self._hardware_lock
        # 硬件通道仲裁在 task_queue（:3009）：workflow 级租约 + 实验级串行
        self._queue_service_url = os.environ.get(
            "QMCLAW_QUEUE_URL", "http://localhost:3009")
        # 排队等待租约的 workflow（workflowId -> {...}），/workflows/status 会读它
        self._leased_workflows: Dict[str, Dict[str, Any]] = {}
        self._leased_lock = threading.Lock()

        # 历史记录锁
        self._runs_lock = threading.Lock()

        # 测控服务地址
        self._quantum_service_url = "http://localhost:3003"
        self._analysis_service_url = "http://localhost:3004"
        self._llm_service_url = "http://localhost:3006"
        self._image_service_url = "http://localhost:3007"
        self._qubitclient_service_url = "http://localhost:3010"

        # 解释执行器运行期状态
        self._cancel_events: Dict[str, threading.Event] = {}
        self._default_node_timeout = float(os.environ.get("QMCLAW_NODE_TIMEOUT", "300"))
        self._max_workflows_in_memory = int(os.environ.get("QMCLAW_MAX_WORKFLOWS", "50"))

        # 本地节点执行器（不经过 HTTP 的“控制类”节点）
        self._builtin_executors: Dict[str, callable] = {
            "context": self._exec_context,
            "print": self._exec_print,
            "quality_gate": self._exec_quality_gate,
            "adjust_params": self._exec_adjust_params,
            "notify": self._exec_notify,
            "code": self._exec_code,
            "ask": self._exec_ask,
        }

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

    # ── 结果判读 / 变量解析 / 控制类节点 ──────────────────────────────────────

    @staticmethod
    def _classify_result(result: Dict[str, Any]) -> tuple:
        """判断节点结果是成功还是失败。

        以前 `_execute_via_http` 出错时只返回 `{"error": ...}`，节点照样被标记
        COMPLETED，工作流带着错误继续往下跑（实测 piamp 超时后 e3 仍然执行）。
        这里统一把“带 error / status=failed|error|timeout / success=false”判为失败。
        """
        if not isinstance(result, dict):
            return True, None
        err = result.get("error")
        status = str(result.get("status") or "").lower()
        if err:
            return False, str(err)
        if status in ("failed", "error", "timeout", "cancelled"):
            return False, str(result.get("error") or f"node status={status}")
        if result.get("success") is False:
            return False, str(result.get("error") or "success=false")
        return True, None

    def _resolve_vars(self, value: Any, context: Dict[str, Any], workflow: "Workflow" = None) -> Any:
        """解析 `{{name}}` 引用：运行上下文 → 上游节点结果（含 metrics / variables）。"""
        if not isinstance(value, str) or "{{" not in value:
            return value

        def _lookup(name: str) -> Optional[str]:
            src = context or {}
            # 画布导出的模板写作 {{nodes.c1.contrast}}：节点 id 是 context 的键，
            # 与 {{c1.contrast}} 只差一个前缀，这里统一剥掉，避免"展开了但全是空串"。
            if name.startswith("nodes."):
                name = name[len("nodes."):]
            # 带点路径：先按"节点 id.字段"精确取，再退回旧的全上下文模糊匹配
            if "." in name:
                head, _, tail = name.partition(".")
                res = src.get(head)
                if isinstance(res, dict):
                    for bucket in (res, res.get("metrics") or {}, res.get("variables") or {}):
                        if isinstance(bucket, dict) and tail in bucket and not isinstance(bucket[tail], dict):
                            return str(bucket[tail])
            if name in src and not isinstance(src[name], dict):
                return str(src[name])
            for res in src.values():
                if not isinstance(res, dict):
                    continue
                for bucket in (res, res.get("metrics") or {}, res.get("variables") or {}):
                    if isinstance(bucket, dict) and name in bucket and not isinstance(bucket[name], dict):
                        return str(bucket[name])
            return None

        def _repl(match):
            found = _lookup(match.group(1).strip())
            return found if found is not None else ""

        return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", _repl, value)

    def _node_timeout(self, node: "WorkflowNode") -> float:
        """节点超时（秒）：节点 config.timeout > 工作流 settings.nodeTimeout > 默认值。"""
        try:
            if node.config.get("timeout"):
                return float(node.config["timeout"])
        except Exception:
            pass
        workflow = getattr(node, "_workflow", None)
        try:
            if workflow is not None and workflow.settings.get("nodeTimeout"):
                return float(workflow.settings["nodeTimeout"])
        except Exception:
            pass
        return self._default_node_timeout

    def _exec_context(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """context 节点：把 config.variables 合并进运行上下文并解析 {{}}。"""
        variables = {}
        for k, v in (node.config.get("variables") or {}).items():
            variables[k] = self._resolve_vars(v, context)
        for k, v in variables.items():
            context.setdefault(k, v)
        lines = [f"[CONTEXT] {k} = {v}" for k, v in variables.items()]
        return {"status": "completed", "variables": variables, "stdout": "\n".join(lines)}

    def _exec_print(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """print 节点：渲染模板；没有模板时汇总上游节点结果。"""
        template = node.config.get("message") or node.config.get("text") or node.config.get("template")
        if template:
            rendered = self._resolve_vars(str(template), context)
            return {"status": "completed", "stdout": rendered, "message": rendered}

        lines = ["[SUMMARY]"]
        for dep_id in node.depends or []:
            res = context.get(dep_id) or {}
            if not isinstance(res, dict):
                continue
            parts = [f"{dep_id}:"]
            if res.get("dataset_id"):
                parts.append(f"dataset={res['dataset_id']}")
            if res.get("contrast") is not None:
                parts.append(f"contrast={res['contrast']}")
            for mk, mv in (res.get("metrics") or {}).items():
                parts.append(f"{mk}={mv}")
            if res.get("error"):
                parts.append(f"error={res['error']}")
            lines.append("  " + " ".join(parts))
        if len(lines) == 1:
            lines.append("  (no upstream results)")
        return {"status": "completed", "stdout": "\n".join(lines)}

    def _exec_quality_gate(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """quality_gate：从上游结果取指标与阈值比较。

        config: {metric: 'contrast', threshold: 0.4, operator: '>=', failWorkflow: true}
        """
        cfg = node.config or {}
        metric = str(cfg.get("metric") or "contrast")
        operator = str(cfg.get("operator") or ">=")
        try:
            threshold = float(cfg.get("threshold", 0.4))
        except (TypeError, ValueError):
            threshold = 0.4

        value = cfg.get("value")
        if value is None:
            candidates = list(node.depends or []) + [k for k in context if k not in (node.depends or [])]
            for dep_id in candidates:
                res = context.get(dep_id)
                if not isinstance(res, dict):
                    continue
                for bucket in (res, res.get("metrics") or {}):
                    if isinstance(bucket, dict) and bucket.get(metric) is not None:
                        value = bucket[metric]
                        break
                if value is not None:
                    break
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                value = None
        if value is None:
            return {"status": "failed", "error": f"quality_gate: metric '{metric}' not found upstream"}

        ops = {
            ">=": value >= threshold,
            ">": value > threshold,
            "<=": value <= threshold,
            "<": value < threshold,
            "==": abs(float(value) - threshold) < 1e-9,
        }
        passed = bool(ops.get(operator, value >= threshold))
        out = {
            "status": "completed",
            "passed": passed,
            "value": value,
            "threshold": threshold,
            "operator": operator,
            "metric": metric,
            "metrics": {metric: value, "threshold": threshold, "passed": passed},
            "stdout": f"[QUALITY GATE] {metric}={value} {operator} {threshold} -> {'PASS' if passed else 'FAIL'}",
        }
        if not passed and cfg.get("failWorkflow", True):
            out["status"] = "failed"
            out["error"] = f"quality gate failed: {metric}={value} {operator} {threshold}"
        return out

    def _exec_adjust_params(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """adjust_params：暂不下发硬件，只记录建议值。"""
        changes = {k: self._resolve_vars(v, context) for k, v in (node.config.get("params") or {}).items()}
        lines = [f"[ADJUST] {k} -> {v}" for k, v in changes.items()] or ["[ADJUST] no params"]
        return {"status": "completed", "params": changes, "stdout": "\n".join(lines)}

    def _exec_ask(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """ask 节点：目标歧义澄清 —— 向人提问，把流程停在 needs_human。

        这不是 LLM 问答（那是 `llm` 节点）。`ask` 的语义是"这一步需要人来拍板"，
        因此返回 `status="needs_human"`，由执行器把工作流收成 needs_human，
        再由 outcome 契约告诉上层"还差一个人类的输入"。
        """
        question = str(node.config.get("question") or node.config.get("prompt") or "")
        options = node.config.get("options") or []
        if isinstance(options, str):
            options = [options]
        options = [str(o) for o in options]
        blocking = bool(node.config.get("blocking", True))
        if not question:
            return {
                "status": "failed",
                "error": "ask 节点缺少 question（目标歧义澄清必须写清要问什么）",
            }
        msg = f"[NEEDS_HUMAN] {question}"
        if options:
            msg += "（可选：" + " / ".join(options) + "）"
        _log(msg)
        return {
            "status": "needs_human",
            "needs_human": True,
            "question": question,
            "options": options,
            "blocking": blocking,
            "message": question,
            "stdout": msg,
        }

    def _exec_notify(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        msg = self._resolve_vars(str(node.config.get("message", "")), context)
        _log(f"[notify] {msg}")
        return {"status": "completed", "stdout": f"[NOTIFY] {msg}", "message": msg}

    def _exec_code(self, node: "WorkflowNode", context: Dict[str, Any]) -> Dict[str, Any]:
        """code 节点：默认拒绝在服务进程里执行任意代码（安全）。"""
        if not node.config.get("allowLocalCode"):
            return {
                "status": "failed",
                "error": "code 节点默认禁用；如确需在服务进程执行，请设置 allowLocalCode=true",
            }
        try:
            local_ns: Dict[str, Any] = {}
            exec(str(node.config.get("code", "")), {"__builtins__": __builtins__, "context": context}, local_ns)
            body = "\n".join(f"{k} = {v}" for k, v in local_ns.items() if not k.startswith("__"))
            return {"status": "completed", "stdout": body}
        except Exception as e:
            return {"status": "failed", "error": f"code node failed: {e}"}

    def _execute_node(self, workflow: Workflow, node_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个节点（按 `config.retries` / `config.retryDelay` 重试）。"""
        node = workflow.nodes[node_id]
        try:
            retries = max(0, int(node.config.get("retries", 0) or 0))
        except (TypeError, ValueError):
            retries = 0
        try:
            retry_delay = max(0.0, float(node.config.get("retryDelay", 2) or 0))
        except (TypeError, ValueError):
            retry_delay = 2.0
        attempts = 1 + retries
        result: Dict[str, Any] = {}

        for attempt in range(attempts):
            result = self._execute_node_once(workflow, node_id, context)
            if node.status != NodeStatus.FAILED or attempt + 1 >= attempts:
                break
            _log(f"Node {node_id} failed (attempt {attempt + 1}/{attempts}): {node.error}; "
                 f"retrying in {retry_delay}s")
            # 分片睡眠，取消/超时时能及时退出
            cancel_event = self._cancel_events.get(workflow.id)
            slept = 0.0
            while slept < retry_delay:
                if cancel_event is not None and cancel_event.is_set():
                    return result
                time.sleep(min(0.5, retry_delay - slept))
                slept += 0.5
            node.status = NodeStatus.RUNNING
            node.error = None
        return result

    def _execute_node_once(self, workflow: Workflow, node_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个节点（单次尝试）"""
        node = workflow.nodes[node_id]
        node.status = NodeStatus.RUNNING
        node.started_at = time.time()

        _log(f"Executing node {node_id} (type={node.type})")

        try:
            executor = self._executors.get(node.type)
            builtin = self._builtin_executors.get(node.type)
            node._workflow = workflow
            if executor:
                result = executor(node, context)
            elif builtin:
                result = builtin(node, context)
            else:
                result = self._execute_via_http(node, context, workflow=workflow)

            ok, err = self._classify_result(result)
            node.result = result
            cancelled = isinstance(result, dict) and str(result.get("status") or "").lower() == "cancelled"
            needs_human = (isinstance(result, dict)
                           and (result.get("needs_human") is True
                                or str(result.get("status") or "").lower() == "needs_human"))
            if cancelled:
                # 取消不是“失败”：节点标 cancelled，工作流由 cancel_event 收尾
                node.status = NodeStatus.CANCELLED
                node.error = None
            elif needs_human:
                # 等人拍板也不是“失败”：单独一种状态，由 outcome 契约往上冒
                node.status = NodeStatus.NEEDS_HUMAN
                node.error = None
            elif ok:
                node.status = NodeStatus.COMPLETED
            else:
                node.status = NodeStatus.FAILED
                node.error = err
            node.completed_at = time.time()
            context[node_id] = result

            _log(f"Node {node_id} {node.status.value}" + (f": {err}" if err else ""))
            return result

        except Exception as e:
            _log(f"Node {node_id} failed: {e}\n{traceback.format_exc()}")
            node.status = NodeStatus.FAILED
            node.error = str(e)
            node.completed_at = time.time()
            return {"error": str(e)}

    def _execute_via_http(self, node: WorkflowNode, context: Dict[str, Any] = None,
                          workflow: "Workflow" = None) -> Dict[str, Any]:
        """通过 HTTP 调用服务执行节点"""
        import urllib.request
        import urllib.error

        node_type = node.type
        config = node.config

        if node_type == "experiment":
            url = f"{self._quantum_service_url}/execute"
            # 由 fn + qubit + params 拼出可执行代码，并解析 {{变量}} 引用
            raw_fn = str(config.get("fn", "sq.iqraw") or "").strip()
            if not _SAFE_FN_RE.match(raw_fn):
                return {
                    "error": f"invalid experiment fn {raw_fn!r}: expected 'sq.<snake_case>'",
                    "status": "failed",
                }
            fn = raw_fn
            raw_qubit = str(config.get("qubit", "") or "").strip()
            qubit = str(self._resolve_vars(raw_qubit, context or {})).strip()
            if "{{" in raw_qubit and not qubit:
                return {
                    "error": f"unresolved qubit variable in {raw_qubit!r}",
                    "status": "failed",
                }
            if qubit and not _SAFE_QUBIT_RE.match(qubit):
                return {
                    "error": f"invalid qubit {qubit!r}: allowed [A-Za-z0-9_%-]",
                    "status": "failed",
                }
            params = {k: self._resolve_vars(v, context or {}) for k, v in (config.get("params") or {}).items()}
            # 服务端执行一律不在工作线程里画图：do_plot=True 会拉起 GUI 把微服务卡死
            params.setdefault("do_plot", False)
            try:
                # 拼代码（含数据集探针）已下沉到 lqcs 适配器：S7 起 server 只做节点包装
                code = _build_experiment_code(
                    fn, qubit, params, bool(config.get("reportDataset", True))
                )
            except ValueError as exc:
                return {"error": str(exc), "status": "failed"}
            # 实验节点走异步 job 接口：节点超时时能真正“放弃”这次任务，
            # 而不是像以前那样只是客户端断开、服务端照跑（真机上两条指令并发很危险）
            return self._run_quantum_job(node, code)

        elif node_type == "contrast":
            # 读出对比度节点：读回 iqraw 数据集，按 discriminator 轴算 P(1|prep) 对比度
            url = f"{self._quantum_service_url}/execute"
            raw_qubit = str(config.get("qubit", "{{qubit}}") or "").strip()
            qubit = str(self._resolve_vars(raw_qubit, context or {})).strip()
            if not qubit or not _SAFE_QUBIT_RE.match(qubit):
                return {"error": f"invalid qubit for contrast: {qubit!r}", "status": "failed"}
            dataset_id = config.get("datasetId")
            # 没显式指定时沿用上游实验回报的 dataset_id（context 里按节点 id 存结果）
            if not dataset_id:
                for dep_id in getattr(node, "depends", []) or []:
                    dep_result = (context or {}).get(dep_id) or {}
                    if isinstance(dep_result, dict) and dep_result.get("dataset_id"):
                        dataset_id = dep_result["dataset_id"]
                        break
            if dataset_id not in (None, ""):
                try:
                    dataset_id = int(dataset_id)
                except (TypeError, ValueError):
                    return {"error": f"invalid datasetId {dataset_id!r}", "status": "failed"}
                if dataset_id <= 0:
                    return {"error": f"invalid datasetId {dataset_id!r}", "status": "failed"}
            kind = str(config.get("kind") or "IQraw").strip()
            if not _SAFE_ARGNAME_RE.match(kind):
                return {"error": f"invalid dataset kind {kind!r}", "status": "failed"}
            # 读出对比度同样要占用测控通道 → 也走异步 job，超时可放弃
            return self._run_quantum_job(node, _contrast_snippet(dataset_id, qubit, kind))

        elif node_type in ("analysis", "analyze"):
            # Analyze 节点：用 qter.fitData 对上游实验数据做统计拟合
            dataset_id = str(config.get("datasetId", "") or "")
            if not dataset_id:
                # 多级回溯：沿 depends 链向上找 dataset_id（支持 experiment -> image_analysis -> analyze 这类链路）
                visited = set()
                frontier = list(getattr(node, "depends", []) or [])
                while frontier and not dataset_id:
                    cur = frontier.pop(0)
                    if cur in visited:
                        continue
                    visited.add(cur)
                    cur_result = (context or {}).get(cur) or {}
                    if isinstance(cur_result, dict) and cur_result.get("dataset_id"):
                        dataset_id = str(cur_result["dataset_id"])
                        break
                    # 沿节点图继续向上回溯（原先引用了未定义的 workflow，NameError 被吞掉）
                    if workflow is not None and workflow.nodes.get(cur):
                        wf_node = workflow.nodes[cur]
                        if getattr(wf_node, "depends", None):
                            frontier.extend(wf_node.depends)
            if not dataset_id:
                return {"error": "dataset_id not found from upstream experiment", "status": "failed"}
            command = str(config.get("command", "qter.fitData(do_plot=True)"))
            # 白名单：command 会被 analysis_service 当 Python 执行，只放行拟合入口
            if not _ANALYZE_CMD_RE.match(command):
                return {
                    "error": f"analyze command not allowed: {command[:60]!r} "
                             f"(must start with qter.fitData()",
                    "status": "failed",
                }
            fit_payload = {"dataset_id": dataset_id, "command": command}
            fit_data = json.dumps(fit_payload).encode("utf-8")
            # 路由：活数据 id 是纯数字实验编号（"894"）→ 在线端点（LabRAD DataLab）；
            # 离线 HDF5 索引 id 形如 "20251103_00485" → 离线端点。
            # 在线失败（analysis_service 没连上 LabRAD 等）时回落离线端点再试一次。
            is_live_id = dataset_id.isdigit()
            endpoints = (["/plot/online/v2", "/plot/offline/v2"] if is_live_id else ["/plot/offline/v2"])
            if is_live_id:
                # analysis_service 启动时若抢 LabRAD 超时会进入 degraded_permanent（data_source=offline），
                # 此时在线端点必然失败。先看健康状态，若没连上就把它切回 online 再拟合；
                # 切不回来也不拦（后面还有离线端点兜底）。
                try:
                    with urllib.request.urlopen(f"{self._analysis_service_url}/health", timeout=10) as _h:
                        _hh = json.loads(_h.read().decode("utf-8"))
                    if not _hh.get("datalab_connected", False):
                        _mode_req = urllib.request.Request(
                            f"{self._analysis_service_url}/mode",
                            data=json.dumps({"mode": "online"}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(_mode_req, timeout=60) as _r:
                            _log(f"[analyze] analysis_service switched to online: {_r.read().decode('utf-8')[:120]}")
                    else:
                        _log("[analyze] analysis_service already online")
                except Exception as e:
                    _log(f"[analyze] switch analysis_service to online failed: {e}")
            fit_result = None
            last_err = ""
            for ep in endpoints:
                fit_req = urllib.request.Request(
                    f"{self._analysis_service_url}{ep}",
                    data=fit_data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(fit_req, timeout=120) as resp:
                        fit_result = json.loads(resp.read().decode("utf-8"))
                except Exception as e:
                    last_err = f"{ep}: {e}"
                    _log(f"[analyze] fit request to {ep} failed: {e}")
                    continue
                if fit_result.get("success"):
                    break
                last_err = f"{ep}: {fit_result.get('error', 'fitData failed')}"
                _log(f"[analyze] fit request to {ep} returned error: {last_err}")
                fit_result = None
            if fit_result is None:
                return {"error": f"qter.fitData failed: {last_err}", "status": "failed"}
            # 把拟合图 base64 存为文件，返回 plotPath（与 experiment 节点一致）
            import base64 as _b64
            plot_path = None
            img_data_url = fit_result.get("image", "") or ""
            if isinstance(img_data_url, str) and img_data_url.startswith("data:image/png;base64,"):
                try:
                    b64part = img_data_url.split(",", 1)[1]
                    web_public = Path(__file__).parent.parent.parent.parent / "qmclaw-web" / "public"
                    plots_dir = web_public / "plots"
                    plots_dir.mkdir(parents=True, exist_ok=True)
                    fname = f"analyze_{int(time.time()*1000)}.png"
                    with open(plots_dir / fname, "wb") as _fh:
                        _fh.write(_b64.b64decode(b64part))
                    plot_path = f"/plots/{fname}"
                except Exception as _se:
                    _log(f"[analyze] save fit image failed: {_se}")
            fit_metrics = fit_result.get("fit_metrics", {}) or {}
            # 只透出标量指标：IR 的 assert 要按名字取（$a_t1.T1_us / $a_t2.T2_us），
            # 而拟合结果里可能带曲线/数组，全塞进节点结果会把 status 撑爆。
            # 原先只认 T1_us / R_squared，导致 T2、contrast 之类的阈值 gate 写不出来。
            metrics_out = {
                "dataset_id": dataset_id,
                "qubit": fit_result.get("qubit", ""),
                "experiment_type": fit_result.get("experiment_type", ""),
            }
            for _k, _v in fit_metrics.items():
                if isinstance(_v, bool):
                    continue
                if isinstance(_v, (int, float)):
                    metrics_out[_k] = _v
            t1_us = metrics_out.get("T1_us")
            r2 = metrics_out.get("R_squared")
            stdout_lines = [
                f"[ANALYZE] qter.fitData on {dataset_id}",
                f"Qubit: {fit_result.get('qubit', '')}",
                f"Experiment: {fit_result.get('experiment_type', '')}",
                f"Dataset: {fit_result.get('dataset_name', '')}",
            ]
            for _k, _v in metrics_out.items():
                if _k in ("dataset_id", "qubit", "experiment_type"):
                    continue
                stdout_lines.append(f"{_k} = {_v}")
            return {
                "status": "completed",
                "plotPath": plot_path,
                "dataset_id": dataset_id,
                "qubit": fit_result.get("qubit", ""),
                "experiment_type": fit_result.get("experiment_type", ""),
                "dataset_name": fit_result.get("dataset_name", ""),
                "metrics": metrics_out,
                "stdout": "\n".join(stdout_lines),
            }
        elif node_type == "decision":
            # LLM Decision 节点：收集上游实验结果 → 调 Ising 校准 LLM → 推断下一步实验
            ctx = context or {}
            dep_ids = getattr(node, "depends", []) or []
            summary_lines = []
            for dep_id in dep_ids:
                dep_result = ctx.get(dep_id) or {}
                if not isinstance(dep_result, dict):
                    continue
                tag = dep_id
                if dep_result.get("qubit"):
                    summary_lines.append(f"[{tag}] qubit={dep_result.get('qubit')}")
                if dep_result.get("experiment_type") or dep_result.get("exp_type"):
                    summary_lines.append(f"[{tag}] experiment={dep_result.get('experiment_type') or dep_result.get('exp_type')}")
                if dep_result.get("dataset_id"):
                    summary_lines.append(f"[{tag}] dataset_id={dep_result.get('dataset_id')}")
                m = dep_result.get("metrics") or {}
                if isinstance(m, dict):
                    for k in ("T1_us", "R_squared", "t1_us", "SNR", "F0", "F1", "pi_amp", "separation", "decay_visible", "confidence"):
                        if m.get(k) is not None:
                            summary_lines.append(f"[{tag}] {k}={m.get(k)}")
                if dep_result.get("symptom"):
                    summary_lines.append(f"[{tag}] symptom={dep_result.get('symptom')}")
                if dep_result.get("description"):
                    summary_lines.append(f"[{tag}] description={str(dep_result.get('description'))[:300]}")
                if dep_result.get("stdout"):
                    summary_lines.append(f"[{tag}] stdout={str(dep_result.get('stdout'))[:400]}")
            # 有 choices 但没有上游结果时，不是错误：决策节点退化成"纯选择"
            # （意图来自 intentPrompt，选项来自 choices），照样能产出 choice 驱动分支。
            if not summary_lines:
                choices_preview = config.get("choices") or []
                if isinstance(choices_preview, str):
                    choices_preview = [choices_preview]
                if not choices_preview:
                    return {"error": "decision node requires upstream experiment/analysis results",
                            "status": "failed"}
                summary_lines.append("[options] " + json.dumps(
                    [str(c) for c in choices_preview], ensure_ascii=False))
                _intent0 = str(config.get("intentPrompt", "") or "")
                if _intent0:
                    summary_lines.append(f"[intent] {_intent0}")
            experiment_summary = "\n".join(summary_lines)

            # 读取决策模型配置（config/qubitclient.json 的 llm_decision 段；
            # 支持 openai / anthropic 两种协议，key 优先 api_key，其次 api_key_env）
            from .dsl.llm import load_llm_config
            ld = load_llm_config("llm_decision")
            api_key = ld.get("api_key", "")
            base_url = ld.get("base_url", "")
            model = ld.get("model", "")
            provider = str(ld.get("provider") or "openai")
            if not api_key or not base_url:
                return {"error": "llm_decision not configured in qubitclient.json", "status": "failed"}

            mode = str(config.get("mode", "analysis"))
            intent_prompt = str(config.get("intentPrompt", "") or "")
            system_prompt = str(config.get("systemPrompt", "") or "")
            temperature = float(config.get("temperature", 0.3) or 0.3)
            max_tokens = int(config.get("maxTokens", 1200) or 1200)
            # 可选：给定离散选项时，模型必须挑一个（产出 `choice`，可驱动 if 分支）
            choices = config.get("choices") or []
            if isinstance(choices, str):
                choices = [choices]
            choices = [str(c) for c in choices]

            if not system_prompt:
                system_prompt = (
                    "You are an expert quantum calibration assistant for superconducting qubits. "
                    "Given the latest experiment results, diagnose the qubit state and decide the NEXT experiment to run. "
                    "Supported experiment functions: sq.t1, sq.ramsey, sq.ramsey_df, sq.iqraw, sq.s21, sq.s21_dis, "
                    "sq.spectroscopy, sq.pulsed_spec, sq.piamp, sq.xeb, sq.allxy, sq.single_shot, sq.swap, sq.drag_calibrate. "
                    "Respond ONLY with JSON: "
                    '{"symptom": "<short diagnosis>", "reasoning": "<why>", "recommendations": [{"fn": "sq.xxx", "reason": "<why this experiment>", "params": {}}]} '
                    "Give 1-3 recommendations ordered by priority."
                )
            if choices:
                system_prompt = (
                    system_prompt.rstrip()
                    + "\n\nYou must choose exactly one option from: "
                    + json.dumps(choices, ensure_ascii=False)
                    + '\nRespond ONLY with JSON: {"choice": "<one option>", "symptom": "<short diagnosis>", '
                      '"reasoning": "<why>", "recommendations": [{"fn": "sq.xxx", "reason": "<why>", "params": {}}]}'
                )
            if mode == "intent":
                user_content = (
                    f"User intent: {intent_prompt}\n\n"
                    f"Current experiment context:\n{experiment_summary}\n\n"
                    "Decide the next experiments to fulfill the user intent."
                )
            else:
                user_content = (
                    f"Latest experiment results:\n{experiment_summary}\n\n"
                    "Diagnose the qubit state and recommend the next experiment(s) to run."
                )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            from .dsl.llm import chat as llm_chat
            content, llm_err = llm_chat(
                ld, messages, temperature=temperature, max_tokens=max_tokens)
            if not content:
                return {"error": f"decision LLM call failed: {llm_err}",
                        "status": "failed", "provider": provider}

            # 解析 JSON（模型可能带 markdown 围栏或多余文字）
            import re as _re
            symptom = ""
            reasoning = ""
            choice = ""
            recommendations = []
            json_str = content.strip()
            m_fence = _re.search(r"```(?:json)?\s*([\s\S]*?)```", json_str)
            if m_fence:
                json_str = m_fence.group(1).strip()
            try:
                parsed = json.loads(json_str)
            except Exception:
                try:
                    start = json_str.find("{")
                    end = json_str.rfind("}")
                    if start != -1 and end > start:
                        parsed = json.loads(json_str[start:end + 1])
                    else:
                        parsed = {}
                except Exception:
                    parsed = {}
            if isinstance(parsed, dict):
                symptom = str(parsed.get("symptom", "") or "")
                reasoning = str(parsed.get("reasoning", "") or "")
                choice = str(parsed.get("choice", "") or "")
                recs = parsed.get("recommendations") or []
                if isinstance(recs, list):
                    for rec in recs:
                        if isinstance(rec, dict) and rec.get("fn"):
                            recommendations.append({
                                "fn": str(rec.get("fn")),
                                "reason": str(rec.get("reason", "") or ""),
                                "params": rec.get("params") or {},
                            })
            if not recommendations:
                # fallback：把原文当 reasoning
                reasoning = content

            if choice and choices and choice not in choices:
                # 模型给了表外的选项：不算数，交给上层走 needs_human/兜底分支
                stdout_choice = f"[DECISION] choice '{choice}' not in allowed options"
                choice = ""
            else:
                stdout_choice = f"[DECISION] choice={choice}" if choice else ""

            stdout_lines = [
                f"[DECISION] model={model}",
                f"Mode: {mode}",
                stdout_choice,
                f"Symptom: {symptom}" if symptom else "[DECISION] no symptom parsed",
                f"Recommendations: {len(recommendations)}",
            ]
            for i, rec in enumerate(recommendations):
                stdout_lines.append(f"  {i+1}. {rec['fn']} - {rec['reason']}")

            conversation = {
                "model": model,
                "symptom": symptom,
                "reasoning": reasoning,
                "choice": choice,
                "recommendations": recommendations,
                "matchedRules": [],
            }
            return {
                "status": "completed",
                "conversation": conversation,
                "symptom": symptom,
                "reasoning": reasoning,
                "choice": choice,
                "choices": choices,
                "recommendations": recommendations,
                "model": model,
                "metrics": {"recommendation_count": len(recommendations)},
                "stdout": "\n".join(stdout_lines),
            }

        elif node_type == "llm":
            url = f"{self._llm_service_url}/chat"
            payload = {
                "messages": config.get("messages", []),
                "model": config.get("model", "minimax"),
                "temperature": config.get("temperature", 0.7),
            }
        elif node_type == "image_classification":
            # 图像分类：优先用配置 imagePath；否则取依赖节点结果中的 plotPath
            image_path = str(config.get("imagePath", "") or "")
            if not image_path:
                for dep_id in getattr(node, "depends", []) or []:
                    dep_result = (context or {}).get(dep_id) or {}
                    plot_path = dep_result.get("plotPath")
                    if plot_path:
                        plot_path = str(plot_path)
                        if plot_path.startswith("/plots/"):
                            web_public = Path(__file__).parent.parent.parent.parent / "qmclaw-web" / "public"
                            image_path = str(web_public / plot_path.lstrip("/"))
                        else:
                            image_path = plot_path
                        break
            threshold = float(config.get("reviewThreshold", 0.75) or 0.75)
            payload = {"imagePath": image_path, "threshold": threshold}
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self._image_service_url}/classify/single",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as response:
                    raw = json.loads(response.read().decode("utf-8"))
            except Exception as e:
                return {"error": f"image classification failed: {e}"}
            if "error" in raw:
                return {"error": raw["error"], "status": "failed"}
            cls_name = str(raw.get("class", "unknown"))
            conf = float(raw.get("confidence", 0.0))
            probs = raw.get("probabilities", {}) or {}
            p0 = float(probs.get("bad", 0.0))
            p1 = float(probs.get("good", 0.0))
            label = "class_1" if cls_name == "good" else ("class_0" if cls_name == "bad" else cls_name)
            need_review = bool(raw.get("needs_review", False))
            metrics = {
                "label": label,
                "confidence": conf,
                "margin": abs(p1 - p0),
                "needReview": need_review,
            }
            return {
                "status": "completed",
                "class": cls_name,
                "label": label,
                "confidence": conf,
                "margin": abs(p1 - p0),
                "needReview": need_review,
                "needs_review": need_review,
                "probabilities": probs,
                "imagePath": image_path,
                "metrics": metrics,
                "stdout": (
                    f"[IMAGE CLASSIFICATION] {image_path}\n"
                    f"Class: {cls_name} (label={label})\n"
                    f"Confidence: {conf:.3f}\n"
                    f"Margin: {abs(p1 - p0):.3f}\n"
                    f"Need review: {need_review}"
                ),
            }

        elif node_type == "image_analysis":
            # 图像分析(VLM)：取上游实验 plot，转 base64 调 qubitclient 提取参数+描述
            image_path = str(config.get("imagePath", "") or "")
            if not image_path:
                for dep_id in getattr(node, "depends", []) or []:
                    dep_result = (context or {}).get(dep_id) or {}
                    plot_path = dep_result.get("plotPath")
                    if plot_path:
                        plot_path = str(plot_path)
                        if plot_path.startswith("/plots/"):
                            web_public = Path(__file__).parent.parent.parent.parent / "qmclaw-web" / "public"
                            image_path = str(web_public / plot_path.lstrip("/"))
                        else:
                            image_path = plot_path
                        break
            if not image_path or not Path(image_path).exists():
                return {"error": f"imagePath not found: {image_path}", "status": "failed"}
            import base64 as _b64
            try:
                with open(image_path, "rb") as fh:
                    b64img = _b64.b64encode(fh.read()).decode("utf-8")
            except Exception as e:
                return {"error": f"failed to read image: {e}", "status": "failed"}
            family = str(config.get("experimentFamily", config.get("family", "t1")) or "t1")
            vlm_payload = {"image": f"data:image/png;base64,{b64img}", "experiment_family": family, "language": "zh"}
            vlm_data = json.dumps(vlm_payload).encode("utf-8")

            def _post_vlm(path):
                req = urllib.request.Request(
                    f"{self._qubitclient_service_url}{path}",
                    data=vlm_data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode("utf-8"))

            params, desc = {}, {}
            try:
                params = (_post_vlm("/extract_params") or {}).get("result", {}) or {}
            except Exception as e:
                params = {"error": str(e)}
            try:
                desc = (_post_vlm("/describe") or {}).get("result", {}) or {}
            except Exception as e:
                desc = {"error": str(e)}

            t1_us = params.get("T1_us")
            features = desc.get("main_features", "") if isinstance(desc, dict) else ""
            # 透传上游实验的 dataset_id（便于 analyze 节点衔接）
            up_dataset_id = ""
            for dep_id in getattr(node, "depends", []) or []:
                dep_result = (context or {}).get(dep_id) or {}
                if isinstance(dep_result, dict) and dep_result.get("dataset_id"):
                    up_dataset_id = str(dep_result["dataset_id"])
                    break
            metrics = {
                "t1_us": t1_us,
                "decay_visible": params.get("decay_visible") if isinstance(params, dict) else None,
                "plot_type": desc.get("plot_type") if isinstance(desc, dict) else None,
            }
            stdout_lines = [
                f"[IMAGE ANALYSIS] {image_path}",
                f"Experiment family: {family}",
            ]
            if up_dataset_id:
                stdout_lines.append(f"Dataset: {up_dataset_id}")
            if t1_us is not None:
                stdout_lines.append(f"T1 = {t1_us} us")
            if isinstance(params, dict) and params.get("decay_visible") is not None:
                stdout_lines.append(f"Decay visible: {params.get('decay_visible')}")
            if features:
                stdout_lines.append(f"Description: {features}")
            return {
                "status": "completed",
                "t1_us": t1_us,
                "decay_visible": params.get("decay_visible") if isinstance(params, dict) else None,
                "description": features,
                "raw_params": params,
                "raw_description": desc,
                "imagePath": image_path,
                "dataset_id": up_dataset_id,
                "metrics": metrics,
                "stdout": "\n".join(stdout_lines),
            }

        else:
            if node_type in ("while", "parallel"):
                return {
                    "status": "failed",
                    "error": (
                        f"节点类型 '{node_type}' 尚未实现：解释器目前按拓扑序串行执行单次往返。"
                        "循环请在工作流外层重复运行，或用 quality_gate 做出口判断。"
                    ),
                }
            return {"status": "failed", "error": f"Unknown node type: {node_type}"}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        node_timeout = self._node_timeout(node)
        try:
            with urllib.request.urlopen(req, timeout=node_timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._annotate_result(node, result)
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, TimeoutError) or "timed out" in str(reason):
                return {"error": f"node timed out after {node_timeout:.0f}s: {reason}", "status": "timeout"}
            return {"error": f"service unreachable ({url}): {reason}", "status": "failed"}
        except TimeoutError as e:
            return {"error": f"node timed out after {node_timeout:.0f}s: {e}", "status": "timeout"}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    @property
    def _lqcs(self) -> "_LqcsAdapter":
        """lqcs 适配器（懒建；每次访问同步当前 quantum 地址与默认超时）。

        用 property 而不是普通属性：单测与 `_handle_run_ir` 会改写
        `self._quantum_service_url`，适配器必须跟着走，否则会发往旧地址。
        """
        adapter = getattr(self, "_lqcs_adapter", None)
        if adapter is None:
            adapter = _LqcsAdapter(logger=_log)
            self._lqcs_adapter = adapter
        adapter.base_url = str(self._quantum_service_url).rstrip("/")
        adapter.default_timeout = float(self._default_node_timeout)
        # 子实验一律经 task_queue 排队（硬件通道只有一条）；队列地址为空时适配器
        # 自动退回直连 quantum
        adapter.queue_url = str(self._queue_service_url).rstrip("/")
        return adapter

    def _estimate_scan_points(self, fn, params) -> Optional[Dict[str, Any]]:
        """把"这次实验会扫多少点"交给当前后端适配器估算（IR 执行器的预算闸门用）。

        厂商的默认展开量（如 `sq.t1` 的 `zpa=None` → 201 点）只有适配器知道，
        server 与执行器都不猜；拿不到适配器或适配器不支持就返回 None（不拦）。
        """
        try:
            adapter = self._lqcs
        except Exception:
            return None
        est = getattr(adapter, "estimate_points", None)
        if not callable(est):
            return None
        try:
            return est(fn, params or {})
        except Exception as exc:      # 估算失败不能挡住执行
            _log(f"[Workflow] estimate_points failed for {fn}: {exc}")
            return None

    def _plan_ir(self, ir: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """编译期成本估算：每步扫多少点、跑多久、会不会自己改参数（执行器下发前闸门用）。

        三张表都在适配器里（厂商知识），这里只负责把适配器递进去；拿不到就返回
        None，执行器按"未知、不拦"处理。
        """
        try:
            adapter = self._lqcs
        except Exception:
            return None
        try:
            from .dsl.plan import plan_ir
            return plan_ir(ir, adapter)
        except Exception as exc:      # 统计失败不能挡住执行
            _log(f"[Workflow] plan_ir failed: {exc}")
            return None

    def _write_key(self, name: str, path: Optional[List[str]] = None) -> Optional[str]:
        """真机 stdout 的一次写入 → 写入语义表里用的键名。

        容器名（'PiGate' / 'discriminator' / …）是厂商知识，住在适配器里；
        执行器只把参数名和注册表路径递过来。
        """
        try:
            adapter = self._lqcs
        except Exception:
            return None
        try:
            return adapter.write_key(name, path)
        except Exception:
            return None

    def _drift_threshold(self, key: str) -> Optional[Dict[str, Any]]:
        """这个键被挪动多少算"危险漂移"（看护表在适配器里）。"""
        try:
            adapter = self._lqcs
        except Exception:
            return None
        try:
            return adapter.drift_threshold(key)
        except Exception:
            return None

    def _cancel_event_for(self, node: "WorkflowNode") -> Optional[threading.Event]:
        """取节点所属工作流的取消事件（没有就返回 None）。"""
        workflow = getattr(node, "_workflow", None)
        if workflow is None:
            return None
        return self._cancel_events.get(workflow.id)

    def _abandon_quantum_job(self, job_id: str, base: str) -> None:
        """放弃一个 quantum job（取消或超时都用它）。失败不影响主流程。

        S7 起实现在 `LqcsAdapter.abandon()`；这里只把 `base` 同步给适配器
        （单测会改写 `svc._quantum_service_url`，适配器必须跟着走）。
        """
        adapter = self._lqcs
        if base:
            adapter.base_url = str(base).rstrip("/")
        adapter.abandon(job_id)

    def _run_quantum_job(self, node: "WorkflowNode", code: str) -> Dict[str, Any]:
        """以异步 job 方式跑一个实验节点（S7 起实现下沉到 lqcs 适配器）。

        流程：POST /execute {async:true} → 拿 job_id → 轮询 GET /jobs/{id}
        → 成功/失败/超时分别落到 completed / failed / timeout；
        超时或取消会 POST /jobs/{id}/abandon 真正放弃这次任务。
        """
        return self._lqcs.submit_and_wait(
            code,
            self._node_timeout(node),
            self._cancel_event_for(node),
            node_id=node.id,
            kind=node.type,
            config=node.config or {},
        )

    def _annotate_result(self, node: "WorkflowNode", result: Dict[str, Any]) -> Dict[str, Any]:
        """把注入片段打印的标记解析成结构化字段（S7 起实现在 lqcs 适配器）。

        - experiment 节点：从 `QMCLAW_DATASET <n>` 补出 dataset_id；探针报 ERR 判失败
        - contrast 节点：从 `QMCLAW_CONTRAST ...` 补出 contrast/SNR/metrics/passed
        """
        return _lqcs_annotate(node.type, node.config or {}, result)

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

    def _persist_workflow_run(self, workflow_id: str, context: Dict[str, Any],
                              extra: Dict[str, Any] = None):
        """将工作流运行结果持久化到磁盘"""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            _log(f"Cannot persist: workflow {workflow_id} not found")
            # D1-③：落档失败必须**看得见**，不能静默 return（调用方会兜底写
            # run-persist-failed-*.json，否则 run 级 JSON 就凭空消失了）。
            return {"ok": False, "error": f"workflow {workflow_id} not found"}

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
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(node.started_at or workflow.created_at)),
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
            "context": context,
            "nodes": nodes,
        }
        if extra:
            # DSL 侧审计字段（irHash / ir / usage / statements / dryRun …）
            for key, value in extra.items():
                run.setdefault(key, value)

        self._save_run(run)
        _log(f"Persisted workflow run: {run_id}")
        return {"ok": True, "runId": run_id}

    def _run_workflow_async(self, workflow_id: str, context: Dict[str, Any]):
        """异步执行工作流：硬件互斥 → 单轮/多轮执行 → 收尾持久化。"""
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return

        _log(f"Starting workflow {workflow_id}")

        # 初始化工作流上下文（确保在 except 块中可用）
        workflow_context = dict(context or {})
        # 复用 cancel 事件，但不复用“已经被置位”的旧事件：否则轮循环一进来就 break，
        # 工作流会停在 pending（同一个 workflow_id 被重跑时会踩到）
        cancel_event = self._cancel_events.get(workflow_id)
        if cancel_event is None or cancel_event.is_set():
            cancel_event = threading.Event()
            self._cancel_events[workflow_id] = cancel_event

        # 硬件互斥：真机只有一个测控通道，两个工作流同时下发会互相踩设备状态
        if not self._hardware_lock.acquire(blocking=False):
            workflow.status = NodeStatus.FAILED
            workflow.error = "another workflow is running (hardware busy)"
            workflow.completed_at = time.time()
            for node in workflow.nodes.values():
                if node.status == NodeStatus.PENDING:
                    node.status = NodeStatus.SKIPPED
            _log(f"Workflow {workflow_id} rejected: hardware busy")
            self._persist_workflow_run(workflow_id, workflow_context)
            return

        try:
            # 多轮重复：settings.repeat = {maxRounds: N, stopWhen: {metric, op, threshold}}
            repeat = workflow.settings.get("repeat") or {}
            try:
                max_rounds = max(1, min(int((repeat or {}).get("maxRounds", 1) or 1), 20))
            except (TypeError, ValueError):
                max_rounds = 1
            stop_when = (repeat or {}).get("stopWhen")

            for round_idx in range(max_rounds):
                if cancel_event.is_set():
                    break
                if round_idx > 0:
                    self._reset_nodes(workflow)
                if max_rounds > 1:
                    _log(f"Workflow {workflow_id} round {round_idx + 1}/{max_rounds}")
                self._run_round(workflow, workflow_context, cancel_event)
                if workflow.status in (NodeStatus.FAILED, NodeStatus.CANCELLED):
                    break
                if stop_when and self._stop_condition_met(stop_when, workflow_context):
                    _log(f"Workflow {workflow_id} stopWhen satisfied after round {round_idx + 1}")
                    break
        except Exception as e:
            _log(f"Workflow {workflow_id} error: {e}\n{traceback.format_exc()}")
            workflow.status = NodeStatus.FAILED
            workflow.error = str(e)
        finally:
            workflow.completed_at = time.time()
            self._hardware_lock.release()

        _log(f"Workflow {workflow_id} finished: {workflow.status.value}")

        # 持久化运行记录
        self._persist_workflow_run(workflow_id, workflow_context)

    def _run_round(self, workflow: "Workflow", workflow_context: Dict[str, Any],
                   cancel_event: threading.Event):
        """执行一轮：按拓扑序串行跑所有节点。"""
        workflow_id = workflow.id
        try:
            # 拓扑排序确定执行顺序
            sorted_nodes = self._topological_sort(workflow)

            # 执行节点（顺序执行：真机只有一个测控通道）
            for node_id in sorted_nodes:
                if cancel_event.is_set() or workflow.status == NodeStatus.CANCELLED:
                    break

                # 上游失败/取消 → 本节点跳过（拓扑序保证依赖已先执行）
                # 例外：上游显式设置了 continueOnError，表示“它失败也继续”，此时放行下游
                # 例外二：本节点带条件边时，条件来源节点不算阻塞 —— 由条件判断决定
                #        （condition-fail 的语义就是“上游失败时才执行我”）
                cond_sources = self._condition_sources(workflow, node_id)
                blocked = []
                for dep in workflow.nodes[node_id].depends:
                    if dep not in workflow.nodes:
                        continue
                    if dep in cond_sources:
                        continue
                    dep_node = workflow.nodes[dep]
                    if dep_node.status not in (NodeStatus.FAILED, NodeStatus.CANCELLED, NodeStatus.SKIPPED):
                        continue
                    if dep_node.status == NodeStatus.FAILED and (
                        dep_node.config.get("continueOnError") or workflow.settings.get("continueOnError")
                    ):
                        continue
                    blocked.append(dep)
                if blocked:
                    workflow.nodes[node_id].status = NodeStatus.SKIPPED
                    _log(f"Node {node_id} skipped: upstream {blocked} not completed")
                    continue

                # 条件边（condition-pass / condition-fail）：不满足则跳过本节点
                if not self._conditions_met(workflow, node_id, workflow_context):
                    workflow.nodes[node_id].status = NodeStatus.SKIPPED
                    continue

                self._execute_node(workflow, node_id, workflow_context)

                if workflow.nodes[node_id].status == NodeStatus.FAILED:
                    if workflow.nodes[node_id].config.get("continueOnError") or workflow.settings.get("continueOnError"):
                        _log(f"Node {node_id} failed but continueOnError=true, continuing")
                        continue
                    workflow.status = NodeStatus.FAILED
                    workflow.error = workflow.nodes[node_id].error or f"node {node_id} failed"
                    _log(f"Workflow {workflow_id} failed at node {node_id}")
                    break

            # 收尾状态
            if cancel_event.is_set():
                workflow.status = NodeStatus.CANCELLED
            elif workflow.status in (NodeStatus.PENDING, NodeStatus.RUNNING):
                all_done = all(
                    node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)
                    for node in workflow.nodes.values()
                )
                workflow.status = NodeStatus.COMPLETED if all_done else NodeStatus.FAILED

        except ValueError as e:
            # 图结构错误（如成环）属于“用户配置问题”，只记一行，不打整段栈
            _log(f"Workflow {workflow_id} invalid: {e}")
            workflow.status = NodeStatus.FAILED
            workflow.error = str(e)
        except Exception as e:
            _log(f"Workflow {workflow_id} error: {e}\n{traceback.format_exc()}")
            workflow.status = NodeStatus.FAILED
            workflow.error = str(e)
        finally:
            # 未执行的节点标记结束状态，避免前端永远显示 pending
            for node in workflow.nodes.values():
                if node.status == NodeStatus.PENDING:
                    node.status = (
                        NodeStatus.CANCELLED if workflow.status == NodeStatus.CANCELLED
                        else NodeStatus.SKIPPED
                    )

    def _reset_nodes(self, workflow: "Workflow") -> None:
        """多轮执行时把节点状态清空（保留节点定义）。"""
        for node in workflow.nodes.values():
            node.status = NodeStatus.PENDING
            node.result = None
            node.error = None
            node.started_at = None
            node.completed_at = None

    def _condition_sources(self, workflow: "Workflow", node_id: str) -> set:
        """本节点条件边引用的上游节点 id 集合。"""
        sources = set()
        conditions = workflow.nodes[node_id].config.get("conditions") or []
        if not isinstance(conditions, list):
            return sources
        for cond in conditions:
            if isinstance(cond, dict):
                src = cond.get("from") or cond.get("source")
                if src:
                    sources.add(src)
        return sources

    def _conditions_met(self, workflow: "Workflow", node_id: str,
                        context: Dict[str, Any]) -> bool:
        """条件边：`config.conditions = [{from, on: pass|fail}]` 全部满足才执行。

        前端把 condition-pass / condition-fail 边转成这个字段；不满足时下游节点
        标记 SKIPPED（与“上游失败导致跳过”区分），而不是让整个工作流失败。
        """
        conditions = workflow.nodes[node_id].config.get("conditions") or []
        if not isinstance(conditions, list):
            return True
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            src = cond.get("from") or cond.get("source")
            if not src:
                continue
            on = str(cond.get("on") or cond.get("type") or "pass").lower()
            src_node = workflow.nodes.get(src)
            src_result = context.get(src) if isinstance(context, dict) else None
            failed = (src_node is not None and src_node.status in (
                NodeStatus.FAILED, NodeStatus.CANCELLED, NodeStatus.SKIPPED))
            if isinstance(src_result, dict):
                failed = failed or bool(src_result.get("error"))
                if src_result.get("passed") is False:
                    failed = True
            if on in ("pass", "condition-pass", "success"):
                if failed:
                    _log(f"Node {node_id} condition not met: {src} did not pass")
                    return False
            else:
                if not failed:
                    _log(f"Node {node_id} condition not met: {src} did not fail")
                    return False
        return True

    def _stop_condition_met(self, stop_when: Any, context: Dict[str, Any]) -> bool:
        """多轮停止条件：在 context 里找指标（含 metrics 内），与阈值比较。"""
        if not isinstance(stop_when, dict):
            return False
        metric = str(stop_when.get("metric") or "").strip()
        if not metric:
            return False
        op = str(stop_when.get("op") or ">=").strip()
        try:
            threshold = float(stop_when.get("threshold"))
        except (TypeError, ValueError):
            return False
        value = None
        for key in (metric, metric.replace("metrics.", "")):
            if key in context and not isinstance(context[key], (dict, list)):
                value = context[key]
                break
        if value is None:
            for res in context.values():
                if isinstance(res, dict):
                    bucket = res.get("metrics") or {}
                    if isinstance(bucket, dict) and metric in bucket:
                        value = bucket[metric]
                        break
                    if metric in res and not isinstance(res[metric], (dict, list)):
                        value = res[metric]
                        break
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        return {
            ">=": v >= threshold, ">": v > threshold,
            "<=": v <= threshold, "<": v < threshold,
            "==": v == threshold,
        }.get(op, v >= threshold)

    def _topological_sort(self, workflow: Workflow) -> List[str]:
        """拓扑排序（带环检测）。

        原来的实现遇到环会静默地按 DFS 顺序输出，结果节点在依赖未完成时就执行。
        现在直接抛错，由调用方把工作流标记为失败并给出环上的节点。
        """
        visited: Set[str] = set()
        visiting: List[str] = []
        result: List[str] = []

        def visit(node_id: str):
            if node_id in visited:
                return
            if node_id in visiting:
                cycle = " -> ".join(visiting[visiting.index(node_id):] + [node_id])
                raise ValueError(f"Workflow has a cycle: {cycle}")
            visiting.append(node_id)
            for dep_id in workflow.nodes[node_id].depends:
                if dep_id in workflow.nodes:
                    visit(dep_id)
            visiting.pop()
            visited.add(node_id)
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
        # ── DSL / IR（见 docs/DSL_DESIGN.md）────────────────────────────────
        elif path == "/workflows/validate":
            return self._handle_validate_ir(data)
        elif path == "/workflows/run_ir":
            return self._handle_run_ir(data)
        elif path == "/workflows/manifest":
            return self._handle_manifest(data)
        elif path == "/workflows/ir_from_canvas":
            return self._handle_ir_from_canvas(data)
        elif path == "/workflows/parse_qwf":
            return self._handle_parse_qwf(data)
        elif path == "/workflows/lint":
            return self._handle_lint(data)
        elif path == "/workflows/qwf_examples":
            return self._handle_qwf_examples(data)
        elif path == "/workflows/qwf_to_canvas":
            return self._handle_qwf_to_canvas(data)
        elif path == "/workflows/canvas_to_qwf":
            return self._handle_canvas_to_qwf(data)
        elif path == "/workflows/stmt_to_qwf":
            return self._handle_stmt_to_qwf(data)
        elif path == "/workflows/saved":
            if method == "POST":
                return self._handle_saved_workflow_save(data)
            return self._handle_saved_workflows(data, method)
        elif path.startswith("/workflows/saved/"):
            rest = path[len("/workflows/saved/"):].strip("/")
            if method == "DELETE":
                return self._handle_saved_workflow_delete(rest)
            return self._handle_saved_workflow_get(rest)
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

    # ── DSL / IR 入口（见 docs/DSL_DESIGN.md §8）──────────────────────────────
    def _handle_validate_ir(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/validate {ir, backend?, vendorCheck?}`：只校验，不执行、不碰硬件。

        `backend` 给了就按该后端的能力表检查 `requirements.capabilities`
        （当前只有 lqcs 一个实现，见 docs/BACKEND_ADAPTERS.md）。
        """
        from .dsl import format_diags
        from .dsl import validate as dsl_validate

        ir = data.get("ir") or {}
        normalized, diags = dsl_validate(
            ir,
            require_writes=bool(data.get("requireWrites", True)),
            backend=(data.get("backend") or None),
            vendor_check=bool(data.get("vendorCheck", False)),
        )
        errors = [d for d in diags if d.get("level") == "error"]
        warnings = [d for d in diags if d.get("level") != "error"]
        return {
            "ok": not errors,
            "ir": normalized,
            "diags": diags,
            "errors": errors,
            "warnings": warnings,
            "summary": format_diags(diags),
        }

    def _handle_manifest(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/manifest {ir | text}`：算写清单 + 票据可用的哈希（P3，§13.5）。

        返回的 `manifest.writes` 已经展开成**账本口径**：
        `{key, qubit, count, maxDelta, delegable}`，与
        `services/task_queue/ledger.py:manifest_hash` 的取哈希字段一一对应，
        所以同一份 IR 在两边算出的 `manifestHash` 必然相同。
        """
        from .dsl import format_diags
        from .dsl import parse_qwf
        from .dsl import validate as dsl_validate
        from .dsl.manifest import collect_manifest, manifest_errors, manifest_summary

        ir = data.get("ir")
        if not ir:
            text = str(data.get("text") or "")
            if not text.strip():
                return {"ok": False, "error": "需要 ir 或 text"}
            try:
                ir = parse_qwf(text)
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        normalized, diags = dsl_validate(ir, require_writes=True)
        errors = [d for d in diags if d.get("level") == "error"]
        if errors:
            return {"ok": False, "error": errors[0].get("message"), "errors": errors,
                    "diags": diags, "summary": format_diags(diags)}

        raw = collect_manifest(normalized, adapter=getattr(self, "_lqcs", None))
        env = normalized.get("env") or {}

        def _resolve_qubit(q: Any) -> Any:
            # 写清单在编译期算，`$env.qubit` 还没被 context 替换；用 IR 自己的 env 落成具体比特
            if isinstance(q, str) and q.startswith("$env."):
                return env.get(q[5:]) or q
            return q

        writes: List[Dict[str, Any]] = []
        for item in raw.get("writes") or []:
            qubits = item.get("qubits") or [None]
            for qubit in qubits:
                writes.append({
                    "key": item.get("key"),
                    "qubit": _resolve_qubit(qubit),
                    "count": int(item.get("max_count") or item.get("count") or 1),
                    "maxDelta": item.get("bound"),
                    "delegable": bool(item.get("delegable")),
                })
        manifest = {"writes": writes,
                    "qubits": [_resolve_qubit(q) for q in (raw.get("qubits") or [])],
                    "totalWrites": raw.get("total_writes") or 0,
                    "neverDelegable": raw.get("never_delegable") or [],
                    "unbounded": raw.get("unbounded") or []}
        try:
            from ..task_queue.ledger import manifest_hash
            digest = manifest_hash(manifest)
        except Exception as exc:  # 账本模块不可用时仍给出清单，只是没有哈希
            digest = None
            diags = list(diags) + [{"level": "warning",
                                    "message": f"manifestHash 计算失败：{exc}"}]
        return {
            "ok": not manifest_errors(normalized, adapter=getattr(self, "_lqcs", None)),
            "manifest": manifest,
            "manifestHash": digest,
            "summary": manifest_summary(raw),
            "diags": diags,
        }

    def _handle_parse_qwf(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/parse_qwf {text}`：文本 DSL → IR（不校验，便于看报错行）。"""
        from .dsl import parse_qwf

        try:
            ir = parse_qwf(str(data.get("text") or ""))
        except Exception as exc:  # 语法错误带行列信息
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "ir": ir}

    def _handle_lint(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/lint {text}`：文本 DSL → 体检报告（纯静态，不碰硬件）。

        返回 {ok, errors[], warnings[], infos[], summary, counts}。
        也接受 {"ir": {...}} 直接体检已有 IR。
        """
        from .dsl import lint_ir, lint_text

        try:
            if data.get("ir"):
                report = lint_ir(data["ir"])
            else:
                report = lint_text(str(data.get("text") or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc),
                    "errors": [{"code": "E_LINT", "message": str(exc)}],
                    "warnings": [], "infos": [],
                    "summary": {}, "counts": {"error": 1, "warning": 0, "info": 0}}
        return report

    # ── `.qwf` ⇄ 画布（见 docs/DSL_DESIGN.md §7.2）───────────────────────────
    def _qwf_examples_dir(self) -> Path:
        return ROOT_DIR / "data" / "workflows" / "examples"

    def _handle_qwf_examples(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/qwf_examples`：列出 `data/workflows` 下的 `.qwf`。

        画布的"示例工作流"下拉用这个接口——示例是**文本 DSL**，
        前端拿文本后调 `/workflows/qwf_to_canvas` 转成画布节点。
        """
        from .dsl import parse_qwf

        include_text = bool(data.get("includeText"))
        directory = self._qwf_examples_dir()
        items: List[Dict[str, Any]] = []
        if directory.exists():
            for path in sorted(directory.glob("*.qwf")):
                try:
                    text = path.read_text(encoding="utf-8")
                except Exception as exc:
                    items.append({"name": path.stem, "path": path.name,
                                  "error": str(exc)})
                    continue
                entry: Dict[str, Any] = {
                    "name": path.stem,
                    "path": path.name,
                    "source": "example",
                }
                try:
                    ir = parse_qwf(text)
                    meta = ir.get("meta") or {}
                    entry.update({
                        "id": ir.get("id"),
                        "title": meta.get("name") or path.stem,
                        "description": meta.get("description") or "",
                        "tags": meta.get("tags") or [],
                        "nodeCount": len(ir.get("pipeline") or []),
                        "ops": sorted({s.get("op") for s in ir.get("pipeline") or []
                                       if isinstance(s, dict)}),
                    })
                except Exception as exc:
                    entry["error"] = str(exc)
                if include_text:
                    entry["text"] = text
                items.append(entry)
        return {"ok": True, "examples": items, "count": len(items)}

    def _handle_qwf_to_canvas(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/qwf_to_canvas {text|path}`：`.qwf` → 画布节点图。

        `path` 相对 `data/workflows/examples`；给了 `text` 就用文本。
        """
        from .dsl import parse_qwf
        from .dsl.canvas_ir import ir_to_canvas

        text = data.get("text")
        source = "text"
        if not text and data.get("path"):
            name = str(data["path"])
            candidate = self._qwf_examples_dir() / Path(name).name
            if not candidate.exists():
                candidate = ROOT_DIR / "data" / "workflows" / Path(name).name
            if not candidate.exists():
                return {"ok": False, "error": f"找不到 .qwf 文件：{name}"}
            text = candidate.read_text(encoding="utf-8")
            source = str(candidate)
        if not text:
            return {"ok": False, "error": "需要 text 或 path"}
        try:
            ir = parse_qwf(str(text))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        try:
            canvas = ir_to_canvas(ir)
        except Exception as exc:
            return {"ok": False, "error": f"IR → 画布失败：{exc}"}
        canvas["source"] = source
        return {"ok": True, "canvas": canvas, "ir": ir, "qwf": canvas.get("qwf")}

    def _handle_canvas_to_qwf(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/canvas_to_qwf {name,nodes,edges,context,workflow,baseIr}`。

        画布 → `.qwf` 文本。给了 `baseIr`（画布打开时的原始 IR）就做等价判定，
        不等价时返回 `diff` 清单——前端据此阻止导出。
        """
        from .dsl.canvas_ir import canvas_to_qwf as _canvas_to_qwf

        doc = {
            "id": data.get("id") or data.get("name") or "workflow",
            "name": data.get("name") or data.get("id") or "workflow",
            "nodes": data.get("nodes") or [],
            "edges": data.get("edges") or [],
            "context": data.get("context") or {},
            "workflow": data.get("workflow") or {},
        }
        try:
            result = _canvas_to_qwf(doc, base_ir=data.get("baseIr") or data.get("base_ir"))
        except Exception as exc:
            return {"ok": False, "error": f"画布 → .qwf 失败：{exc}"}
        return result

    # ── 存档（`.qwf` 文件，见 qwf_store.py）────────────────────────────────
    def _handle_stmt_to_qwf(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/stmt_to_qwf {stmt}`：单条 IR 语句 → `.qwf` 片段。

        画布节点的 "DSL" 标签页用它显示"这一行长什么样"。
        """
        from .dsl.textdsl.printer import stmt_to_qwf

        stmt = data.get("stmt")
        if not isinstance(stmt, dict):
            return {"ok": False, "error": "需要 stmt 对象"}
        try:
            return {"ok": True, "text": stmt_to_qwf(stmt)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _handle_saved_workflows(self, data: Dict[str, Any], method: str) -> Dict[str, Any]:
        """`GET /workflows/saved`：列出存档（`data/workflows/*.qwf`）与示例。"""
        from . import qwf_store

        include_examples = bool((data or {}).get("includeExamples", True))
        items = qwf_store.list_workflows(include_examples=include_examples)
        return {"ok": True, "workflows": items, "count": len(items)}

    def _handle_saved_workflow_get(self, wf_id: str) -> Dict[str, Any]:
        """`GET /workflows/saved/<id>`：读一个存档/示例（含 `.qwf` 原文与 IR）。"""
        from . import qwf_store
        from .dsl.canvas_ir import ir_to_canvas

        found = qwf_store.read_workflow(wf_id)
        if not found:
            return {"ok": False, "error": f"找不到工作流：{wf_id}"}
        canvas = ir_to_canvas(found["ir"])
        return {"ok": True, "id": found["id"], "name": found["name"],
                "text": found["text"], "ir": found["ir"], "canvas": canvas,
                "source": found["source"], "path": found["path"]}

    def _handle_saved_workflow_save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/saved`：把画布存成 `.qwf`。

        存档是**语义**（IR 文本），不是画布快照；布局由前端自动布局重算。
        """
        from . import qwf_store
        from .dsl.canvas_ir import canvas_to_qwf as _canvas_to_qwf

        name = str(data.get("name") or data.get("id") or "workflow")
        doc = {
            "id": data.get("id") or name,
            "name": name,
            "nodes": data.get("nodes") or [],
            "edges": data.get("edges") or [],
            "context": data.get("context") or {},
            "workflow": data.get("workflow") or {},
        }
        result = _canvas_to_qwf(doc, base_ir=data.get("baseIr") or data.get("base_ir"))
        if not result.get("ok"):
            return {"ok": False, "error": "; ".join(result.get("errors") or ["转换失败"]),
                    "errors": result.get("errors") or []}
        if not result.get("stable", True):
            # 存档必须是语义的完整快照：画布表达不了这份 IR 时拒绝落盘
            return {"ok": False,
                    "error": "画布无法无损表达当前工作流，存档被拒绝（导出会丢语义）",
                    "unstableDiff": result.get("unstableDiff") or []}
        saved = qwf_store.write_workflow(doc["id"], result["ir"])
        saved["equivalent"] = result.get("equivalent", True)
        saved["diff"] = result.get("diff", [])
        saved["changed"] = result.get("changed", False)
        saved["stable"] = True
        saved["ir"] = result["ir"]
        return saved

    def _handle_saved_workflow_delete(self, wf_id: str) -> Dict[str, Any]:
        """`DELETE /workflows/saved/<id>`：删存档。"""
        from . import qwf_store
        return qwf_store.delete_workflow(wf_id)

    def _handle_ir_from_canvas(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/ir_from_canvas {name,nodes,context,settings}`：画布 → IR。"""
        from .dsl import ir_from_canvas

        try:
            ir = ir_from_canvas(
                data.get("name") or "canvas_workflow",
                data.get("nodes") or [],
                data.get("context") or {},
                data.get("settings") or {},
                data.get("edges") or [],
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "ir": ir}

    # ------------------------------------------------------------------
    # 硬件通道租约（task_queue :3009）
    # ------------------------------------------------------------------
    def _queue_get(self, path: str, timeout: float = 10.0) -> Dict[str, Any]:
        """GET 到 task_queue（:3009）；任何失败都返回 `{"error": ...}`，不抛。

        P3 的额度闸门要读"剩余多少"，所以需要读接口；
        读失败时调用方一律按"读不到就不拦"处理（fail-open），并留 notes。
        """
        import urllib.error
        import urllib.request

        url = f"{str(self._queue_service_url).rstrip('/')}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            return {"error": f"HTTP {exc.code}: {exc.read()[:200]!r}"}
        except Exception as exc:
            return {"error": str(exc)}

    def _queue_post(self, path: str, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
        """POST 到 task_queue；任何失败都返回 `{"error": ...}`，不抛。"""
        import urllib.error
        import urllib.request

        url = f"{str(self._queue_service_url).rstrip('/')}{path}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload or {}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            return {"error": f"HTTP {exc.code}: {exc.read()[:200]!r}"}
        except Exception as exc:                     # 队列没起来 / 网络异常
            return {"error": str(exc)}

    def _lease_acquire(self, workflow_id: str,
                       metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # 默认独占：整段工作流期间不让别的实验插进来（`sq.pidf`/`sq.iqraw`/`sq.s21_dis`
        # 都会静默改写寄存器，中途插入会让标定作废）。要"节点边界让路"就显式开
        # QMCLAW_YIELD_BETWEEN_NODES=1。
        yield_between = str(os.environ.get("QMCLAW_YIELD_BETWEEN_NODES", "0")).lower() in (
            "1", "true", "yes", "on")
        # metadata 只用于前端展示（流程名/比特），不参与调度
        return self._queue_post("/lease", {
            "workflow_id": workflow_id,
            "priority": 5,
            "yield_between_nodes": yield_between,
            "owner": "workflow_service",
            "metadata": metadata or {},
        }, timeout=10.0)

    def _lease_release(self, lease_id: str) -> None:
        if not lease_id:
            return
        try:
            self._queue_post("/lease/release", {"lease_id": lease_id}, timeout=10.0)
        except Exception:
            pass

    def _lease_status(self, lease_id: str) -> Dict[str, Any]:
        if not lease_id:
            return {}
        return self._queue_post("/lease/status", {"lease_id": lease_id}, timeout=10.0)

    def _leased_snapshot(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        with self._leased_lock:
            item = self._leased_workflows.get(workflow_id)
            return dict(item) if item else None

    def _handle_run_ir(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """`POST /workflows/run_ir {ir, context, dryRun}`：校验后后台执行，立即返回 workflowId。

        执行在守护线程里跑，进度用现有 `/workflows/status` 轮询（节点由 IrExecutor 预建）。
        """
        from .dsl import IrExecutor, format_diags, ir_hash
        from .dsl import validate as dsl_validate
        from .dsl.dryrun import DryRunProvider

        dry_run = bool(data.get("dryRun") or data.get("dry_run"))
        ir = data.get("ir") or {}
        normalized, diags = dsl_validate(ir, require_writes=bool(
            data.get("requireWrites", not dry_run)))
        errors = [d for d in diags if d.get("level") == "error"]
        if errors:
            return {
                "ok": False,
                "error": errors[0].get("message"),
                "errors": errors,
                "diags": diags,
                "summary": format_diags(diags),
            }

        provider = None
        if dry_run:
            provider = DryRunProvider(mode=str(data.get("dryMode") or "synthetic"))
        # traceId（阶段 3）：请求头 > 请求体 > 新生成，随任务下发到 task_queue/quantum
        trace_id, _trace_new = _ensure_trace(data, headers=getattr(self, "headers", None))
        wf_context = dict(data.get("context") or {})
        wf_context.setdefault("trace_id", trace_id)
        executor = IrExecutor(
            self,
            normalized,
            context=wf_context,
            dry_run=dry_run,
            dry_provider=provider,
            workflow_id=data.get("workflowId") or data.get("workflow_id"),
        )
        try:
            executor.prepare()
        except Exception as exc:
            return {"ok": False, "error": f"prepare failed: {exc}", "diags": diags}

        workflow = self._workflows.get(executor.workflow_id)

        def _start_executor(lease_id: str = "") -> None:
            if workflow is not None:
                workflow.status = NodeStatus.RUNNING
            with self._leased_lock:
                item = self._leased_workflows.get(executor.workflow_id)
                if item is not None:
                    item["status"] = "running"
            # 子实验提交时带 workflowId，task_queue 据此给租约续期
            try:
                self._lqcs.workflow_id = executor.workflow_id
                self._lqcs.trace_id = trace_id
            except Exception:
                pass

            def _run():
                try:
                    executor.run()
                except Exception as exc:
                    _log(f"IR workflow {executor.workflow_id} crashed: {exc}\n{traceback.format_exc()}")
                finally:
                    if not dry_run:
                        if lease_id:
                            # 租约交还：task_queue 据此让下一个排队 workflow 进来
                            self._lease_release(lease_id)
                        else:
                            self._hardware_lock.release()
                    with self._leased_lock:
                        self._leased_workflows.pop(executor.workflow_id, None)

            threading.Thread(target=_run, daemon=True,
                             name=f"ir-{executor.workflow_id}").start()

        # 干跑不碰硬件，不用排队
        if dry_run:
            _start_executor()
            return {
                "ok": True,
                "workflowId": executor.workflow_id,
                "status": "running",
                "dryRun": True,
                "irHash": ir_hash(normalized),
                "trace_id": trace_id,
                "warnings": [d for d in diags if d.get("level") != "error"],
            }

        # 排队列表里要显示"是哪个流程、动哪个比特"，否则只是一串 wf_1793…
        # IR 是扁平的 pipeline 列表，比特可能写成字面量也可能写成 $env.qubit
        _ir_env = normalized.get("env") or {}
        _qubits = set()
        for _st in normalized.get("pipeline") or []:
            if not isinstance(_st, dict):
                continue
            _q = _st.get("qubit")
            if isinstance(_q, str) and _q.startswith("$env."):
                _q = _ir_env.get(_q[len("$env."):])
            if isinstance(_q, str) and _q and not _q.startswith("$"):
                _qubits.add(_q)
        _lease_meta = {
            "workflowName": (getattr(workflow, "name", "") or "") if workflow is not None else "",
            "workflowId": executor.workflow_id,
            "qubits": sorted(_qubits),
        }
        acquired = self._lease_acquire(executor.workflow_id, metadata=_lease_meta)
        if acquired.get("error"):
            # task_queue 不可用 → 退回本地互斥锁，保持旧行为（宁可硬拒也不并发打脉冲）
            _log(f"[Workflow] lease acquire failed ({acquired['error']}); "
                 f"falling back to local hardware lock")
            if not self._hardware_lock.acquire(blocking=False):
                return {"ok": False, "error": "another workflow is running (hardware busy)",
                        "lease": {"mode": "local_lock", "error": acquired.get("error")}}
            _start_executor()
            return {
                "ok": True,
                "workflowId": executor.workflow_id,
                "status": "running",
                "dryRun": False,
                "irHash": ir_hash(normalized),
                "trace_id": trace_id,
                "lease": {"mode": "local_lock"},
                "warnings": [d for d in diags if d.get("level") != "error"],
            }

        if acquired.get("granted"):
            lease_id = acquired.get("lease_id") or ""
            _start_executor(lease_id)
            return {
                "ok": True,
                "workflowId": executor.workflow_id,
                "status": "running",
                "dryRun": False,
                "irHash": ir_hash(normalized),
                "trace_id": trace_id,
                "leaseId": lease_id,
                "lease": {"mode": "queue", "id": lease_id, "priority": 5},
                "warnings": [d for d in diags if d.get("level") != "error"],
            }

        # 未拿到租约：立刻返回排队态，由后台轮询线程接手启动
        lease_id = acquired.get("lease_id") or ""
        position = acquired.get("position")
        with self._leased_lock:
            self._leased_workflows[executor.workflow_id] = {
                "status": "queued",
                "leaseId": lease_id,
                "position": position,
                "queuedAt": time.time(),
            }
        if workflow is not None:
            workflow.status = NodeStatus.PENDING

        def _wait_then_start():
            deadline = time.time() + float(os.environ.get("QMCLAW_LEASE_WAIT", "7200"))
            current = lease_id          # 租约重排后要跟着换，否则一直问旧 id
            while time.time() < deadline:
                time.sleep(1.0)
                # 被取消 / 被移出排队表 → 立刻退出，绝不把已取消的流程跑起来
                if self._leased_snapshot(executor.workflow_id) is None:
                    return
                st = self._lease_status(current)
                if st.get("granted"):
                    _start_executor(current)
                    return
                if st.get("error") or st.get("status") in ("expired", "released"):
                    # 租约丢了：重新排一次（队列重启/租约过期）
                    again = self._lease_acquire(executor.workflow_id, metadata=_lease_meta)
                    if again.get("granted"):
                        _start_executor(again.get("lease_id") or "")
                        return
                    lease_id_new = again.get("lease_id") or ""
                    if lease_id_new:
                        current = lease_id_new
                        with self._leased_lock:
                            item = self._leased_workflows.get(executor.workflow_id)
                            if item is not None:
                                item["leaseId"] = lease_id_new
                                item["position"] = again.get("position")
                    else:
                        time.sleep(2.0)     # 队列还没起来，别空转打满 CPU
                    continue
                with self._leased_lock:
                    item = self._leased_workflows.get(executor.workflow_id)
                    if item is not None and st.get("position") is not None:
                        item["position"] = st.get("position")
            _log(f"[Workflow] gave up waiting for lease of {executor.workflow_id}")
            if workflow is not None:
                workflow.status = NodeStatus.CANCELLED
            with self._leased_lock:
                self._leased_workflows.pop(executor.workflow_id, None)

        threading.Thread(target=_wait_then_start, daemon=True,
                         name=f"lease-{executor.workflow_id}").start()
        return {
            "ok": True,
            "workflowId": executor.workflow_id,
            "status": "queued",
            "dryRun": False,
            "irHash": ir_hash(normalized),
            "trace_id": trace_id,
            "leaseId": lease_id,
            "queuePosition": position,
            "lease": {"mode": "queue", "id": lease_id, "position": position},
            "warnings": [d for d in diags if d.get("level") != "error"],
        }

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
        settings = data.get("settings") or {}

        workflow_id = data.get("workflow_id") or f"wf_{int(time.time() * 1000)}"

        # 创建节点
        nodes = {}
        for node_data in nodes_data:
            node_id = node_data.get("id", f"node_{len(nodes)}")
            node_config = dict(node_data.get("config") or {})
            # 条件边（condition-pass / condition-fail）由前端单独下发，合并进 config
            conditions = node_data.get("conditions")
            if conditions:
                node_config["conditions"] = conditions
            nodes[node_id] = WorkflowNode(
                id=node_id,
                type=node_data.get("type", "unknown"),
                config=node_config,
                depends=node_data.get("depends", []),
            )

        with self._workflows_lock:
            # 同一毫秒内连续创建会撞 ID（wf_{ms}）：撞了就加后缀。
            # 同时清掉旧 ID 可能遗留的 cancel 事件 —— 否则新一轮一启动就被判“已取消”，
            # 直接跳出轮循环，状态停在 pending。
            base_id = workflow_id
            suffix = 1
            while workflow_id in self._workflows:
                suffix += 1
                workflow_id = f"{base_id}_{suffix}"
            self._cancel_events.pop(workflow_id, None)
            workflow = Workflow(id=workflow_id, name=name, nodes=nodes, settings=settings)
            self._workflows[workflow_id] = workflow
            self._evict_old_workflows()

        _log(f"Created workflow {workflow_id} with {len(nodes)} nodes")

        return {
            "success": True,
            "workflow_id": workflow_id,
            "name": name,
            "node_count": len(nodes),
        }

    def _evict_old_workflows(self) -> None:
        """内存回收：只保留最近 N 个已结束的工作流（调用方持有锁）。"""
        try:
            limit = self._max_workflows_in_memory
        except AttributeError:
            return
        if limit <= 0 or len(self._workflows) <= limit:
            return
        finished = [
            wf for wf in self._workflows.values()
            if _status_text(wf.status) in ("completed", "failed", "cancelled",
                                           "budget_exceeded", "needs_human")
        ]
        finished.sort(key=lambda w: w.created_at or 0)
        for wf in finished[: max(0, len(self._workflows) - limit)]:
            self._workflows.pop(wf.id, None)
            self._cancel_events.pop(wf.id, None)

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
                # 把最常用的几个结果字段提到顶层，省得调用方每次翻 result（探活脚本、
                # 前端节点卡片都只想要 dataset/passed 这类一眼可见的值）。
                res = node.result if isinstance(node.result, dict) else {}
                nodes.append({
                    "id": node.id,
                    "type": node.type,
                    "status": node.status.value,
                    "depends": node.depends,
                    "result": node.result,
                    "error": node.error,
                    "started_at": node.started_at,
                    "completed_at": node.completed_at,
                    "dataset_id": res.get("dataset_id"),
                    "passed": res.get("passed"),
                    "contrast": res.get("contrast"),
                })

            return {
                "ok": True,
                "id": workflow.id,
                "workflowId": workflow.id,
                "name": workflow.name,
                "status": workflow.status.value,
                # 排队态：调用方据此显示"前面还有几条"（不在队列里则为 None）
                "lease": self._leased_snapshot(workflow.id),
                "nodes": nodes,
                "settings": workflow.settings,
                # 结局契约（§13.6）：achieved / not_achieved / budget_exhausted /
                # quota_exceeded / needs_human / error / cancelled
                "outcome": (workflow.settings or {}).get("outcome"),
                # 等人拍板时的提问（ask 节点）：question + options
                "needs_human": (workflow.settings or {}).get("needs_human"),
                "error": workflow.error,
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

            # 先通知执行线程（运行中的节点在下一个节点边界退出）
            self._cancel_events.setdefault(workflow_id, threading.Event()).set()

            if _status_text(workflow.status) in ("completed", "failed",
                                                 "cancelled", "budget_exceeded",
                                                 "needs_human"):
                return {"success": True, "workflow_id": workflow_id,
                        "status": _status_text(workflow.status),
                        "note": "workflow already finished"}

            workflow.status = NodeStatus.CANCELLED

            # 取消所有待执行节点
            for node in workflow.nodes.values():
                if node.status == NodeStatus.PENDING:
                    node.status = NodeStatus.CANCELLED

        # 还在排队等租约 → 撤销排队并交还租约，否则等待线程到点会把已取消的
        # 流程真的跑起来
        with self._leased_lock:
            item = self._leased_workflows.pop(workflow_id, None)
        if item and item.get("leaseId"):
            self._lease_release(item["leaseId"])

        _log(f"Cancelled workflow {workflow_id}")
        return {"success": True, "workflow_id": workflow_id, "status": "cancelled"}

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
        with self._leased_lock:
            queued = [{"workflowId": wf, **item}
                      for wf, item in self._leased_workflows.items()]
        return {
            "status": "healthy",
            "service": "workflow_service",
            "workflow_count": len(self._workflows),
            # 等硬件租约的工作流（running 的不在这里）
            "queued_workflows": queued,
        }


def main():
    """主入口"""
    service = WorkflowService(port=3008)
    run_service(service)


if __name__ == "__main__":
    main()
