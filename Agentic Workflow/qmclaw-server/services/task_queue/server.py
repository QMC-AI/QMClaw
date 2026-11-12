"""
services/task_queue/server.py - 任务队列服务

提供分布式任务队列功能：
- 任务提交和调度
- 优先级队列
- 任务状态追踪
- 重试机制
"""

import json
import time
import threading
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
from enum import Enum
from collections import defaultdict

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common.errors import error_envelope, normalize_error
from ..common.trace import ensure_trace as _ensure_trace
from .lane import GATED_TYPES, HardwareLane
from .ledger import TaskLedger, manifest_hash, is_never_delegable

# 实验执行下游：task_queue 是硬件通道的唯一入口，真正的脉冲由 quantum_service 下发
QUANTUM_URL = __import__("os").environ.get("QMCLAW_QUANTUM_URL", "http://127.0.0.1:3003")


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[task_queue] {msg}")


def _summarize_code(code: str, limit: int = 70) -> str:
    """从实验代码里挑一行能看懂的话，给顶栏徽标显示。

    实验代码前面常带几个辅助函数（`def _qm_ds_max(): ...`），直接取第一行会显示成
    `def _qm_ds_max():`，完全看不出在跑什么。所以优先找 `sq.xxx(...)` 那一行，
    找不到再退回第一条"像语句"的行。
    """
    fallback = ""
    for raw in code.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not fallback and not line.startswith(("def ", "import ", "from ", '"""', "'''")):
            fallback = line
        if "sq." in line and "(" in line and not line.startswith(("def ", "import ", "from ")):
            fallback = line
            break
    text = fallback or code.splitlines()[0].strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


class TaskType(Enum):
    """任务类型"""
    EXPERIMENT = "experiment"
    ANALYSIS = "analysis"
    AGENT_CHAT = "agent_chat"
    WORKFLOW = "workflow"
    IMAGE_CLASSIFY = "image_classify"
    GENERAL = "general"


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"       # 等待中
    QUEUED = "queued"         # 已入队
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 失败
    CANCELLED = "cancelled"   # 已取消
    TIMEOUT = "timeout"       # 超时


@dataclass
class Task:
    """任务定义"""
    id: str
    type: TaskType
    payload: Dict[str, Any]
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 5  # 1-10, 10 最高
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    timeout: int = 300  # 秒
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        d = asdict(self)
        d["type"] = self.type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Task":
        """从字典创建"""
        d["type"] = TaskType(d["type"])
        d["status"] = TaskStatus(d["status"])
        return cls(**d)


class PriorityQueue:
    """优先级队列实现（内存版，Redis 可替换）"""

    def __init__(self):
        self._queues: Dict[int, List[Task]] = defaultdict(list)
        self._task_map: Dict[str, Task] = {}
        self._lock = threading.Lock()

    def enqueue(self, task: Task) -> bool:
        """入队"""
        with self._lock:
            if task.id in self._task_map:
                return False
            self._task_map[task.id] = task
            self._queues[task.priority].append(task)
            # 按创建时间排序
            self._queues[task.priority].sort(key=lambda t: t.created_at)
            return True

    def dequeue(self) -> Optional[Task]:
        """出队（最高优先级最早的任务）"""
        with self._lock:
            # 从高优先级到低优先级查找
            for priority in range(10, 0, -1):
                if self._queues[priority]:
                    task = self._queues[priority].pop(0)
                    return task
            return None

    def get(self, task_id: str) -> Optional[Task]:
        """获取任务"""
        with self._lock:
            return self._task_map.get(task_id)

    def update(self, task: Task) -> bool:
        """更新任务"""
        with self._lock:
            if task.id not in self._task_map:
                return False
            self._task_map[task.id] = task
            return True

    def remove(self, task_id: str) -> bool:
        """移除任务"""
        with self._lock:
            if task_id not in self._task_map:
                return False
            task = self._task_map.pop(task_id)
            # 从优先级队列中移除
            if task.priority in self._queues:
                try:
                    self._queues[task.priority].remove(task)
                except ValueError:
                    pass
            return True

    def list_by_status(self, status: TaskStatus) -> List[Task]:
        """按状态列出任务"""
        with self._lock:
            return [t for t in self._task_map.values() if t.status == status]

    def list_by_type(self, task_type: TaskType) -> List[Task]:
        """按类型列出任务"""
        with self._lock:
            return [t for t in self._task_map.values() if t.type == task_type]

    def list_all(self) -> List[Task]:
        """列出所有任务"""
        with self._lock:
            return list(self._task_map.values())

    def size(self) -> int:
        """队列大小"""
        with self._lock:
            return sum(len(q) for q in self._queues.values())


class RedisBackend:
    """Redis 后端（可选，如果 Redis 可用）"""

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        self._host = host
        self._port = port
        self._db = db
        self._client = None
        self._available = False
        self._try_connect()

    def _try_connect(self):
        """尝试连接 Redis"""
        try:
            import redis
            self._client = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                decode_responses=True,
                socket_timeout=2,
            )
            self._client.ping()
            self._available = True
            _log("Redis backend connected")
        except Exception as e:
            self._available = False
            _log(f"Redis not available, using memory backend: {e}")

    @property
    def is_available(self) -> bool:
        return self._available

    def set(self, key: str, value: str, expire: Optional[int] = None) -> bool:
        """设置键值"""
        if not self._available:
            return False
        try:
            if expire:
                self._client.setex(key, expire, value)
            else:
                self._client.set(key, value)
            return True
        except Exception:
            return False

    def get(self, key: str) -> Optional[str]:
        """获取值"""
        if not self._available:
            return None
        try:
            return self._client.get(key)
        except Exception:
            return None

    def delete(self, key: str) -> bool:
        """删除键"""
        if not self._available:
            return False
        try:
            self._client.delete(key)
            return True
        except Exception:
            return False

    def lpush(self, key: str, value: str) -> int:
        """左推入列表"""
        if not self._available:
            return 0
        try:
            return self._client.lpush(key, value)
        except Exception:
            return 0

    def rpop(self, key: str) -> Optional[str]:
        """右弹出列表"""
        if not self._available:
            return None
        try:
            return self._client.rpop(key)
        except Exception:
            return None

    def llen(self, key: str) -> int:
        """列表长度"""
        if not self._available:
            return 0
        try:
            return self._client.llen(key)
        except Exception:
            return 0

    def keys(self, pattern: str) -> List[str]:
        """模式匹配键"""
        if not self._available:
            return []
        try:
            return self._client.keys(pattern)
        except Exception:
            return []


class TaskQueueService(BaseService):
    """任务队列服务

    核心功能:
    - 任务提交和管理
    - 优先级调度
    - 任务状态追踪
    - 重试机制
    """

    def __init__(self, port: int = 3009):
        cfg = ServiceConfig(
            name="task_queue",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # 内存队列
        self._queue = PriorityQueue()
        self._lock = threading.Lock()

        # Redis 后端（可选）
        self._redis = RedisBackend()

        # 任务处理器映射
        self._handlers: Dict[TaskType, callable] = {}

        # 运行中的任务
        self._running_tasks: Dict[str, threading.Thread] = {}

        # 持久化路径
        self._persist_dir = Path(__file__).parent.parent.parent.parent / "config" / "tasks"
        self._persist_dir.mkdir(parents=True, exist_ok=True)

        # 任务账本 + 授权票据（docs/DSL_DESIGN.md §13.3 / §13.4）
        self._ledger = TaskLedger()

        # 硬件通道调度内核：实验级执行（并发度 1）+ 工作流级租约
        self._lane = HardwareLane(
            pending_provider=self._pending_gated_tasks,
            workflow_of=self._workflow_of,
            on_run=self._run_task,
            default_ttl=float(__import__("os").environ.get("QMCLAW_LEASE_TTL", "900")),
        )

        _log("Task queue service initialized")
        _log(f"Redis available: {self._redis.is_available}")
        _log(f"Hardware lane: single slot, quantum={QUANTUM_URL}")

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def before_start(self):
        """启动调度内核；把上个进程留下的未完成硬件任务标记为作废。

        真机安全口径：重启后**不重放**任何排队/运行中的实验——上游调用方可能早已
        超时放弃，盲目重跑会在无人值守的情况下驱动硬件。所以只做记账。
        """
        self.register_handler(TaskType.EXPERIMENT, self._run_experiment_task)
        self._abort_orphan_tasks()
        self._lane.start()
        _log("Hardware lane started")

    def _abort_orphan_tasks(self):
        """把上次进程遗留的 queued/running 记录标记为失败（不重放）。"""
        if not self._persist_dir.exists():
            return
        count = 0
        for path in self._persist_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if raw.get("status") not in ("queued", "pending", "running"):
                continue
            raw["status"] = TaskStatus.FAILED.value
            raw["error"] = "aborted: task_queue restarted (hardware task not resumed)"
            raw["completed_at"] = time.time()
            try:
                path.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                count += 1
            except Exception as exc:
                _log(f"abort orphan failed {path.name}: {exc}")
        if count:
            _log(f"[RECOVER] marked {count} orphan hardware task(s) as failed (not resumed)")

    def _pending_gated_tasks(self) -> List[Task]:
        """给调度内核：当前排队等硬件的任务（高优先级、先到先服务）。"""
        return self._sorted_gated(TaskStatus.QUEUED)

    def _sorted_gated(self, status: TaskStatus) -> List[Task]:
        tasks = [t for t in self._queue.list_by_status(status)
                 if t.type.value in GATED_TYPES]
        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return tasks

    def _workflow_of(self, task: Task) -> str:
        wf = task.metadata.get("workflowId") if task.metadata else None
        if not wf and isinstance(task.payload, dict):
            wf = task.payload.get("workflowId")
        return str(wf or "")

    def _running_task(self) -> Optional[Task]:
        snap = self._lane.snapshot()
        rid = snap.get("running_task")
        return self._queue.get(rid) if rid else None

    def queue_position(self, task_id: str) -> int:
        """0 = 下一个拿到硬件的实验；-1 = 不在排队中。"""
        for idx, task in enumerate(self._sorted_gated(TaskStatus.QUEUED)):
            if task.id == task_id:
                return idx
        return -1


    def register_handler(self, task_type: TaskType, handler: callable):
        """注册任务处理器"""
        self._handlers[task_type] = handler
        _log(f"Registered handler for: {task_type.value}")

    def _generate_id(self) -> str:
        """生成任务 ID"""
        return f"task_{uuid.uuid4().hex[:12]}"

    def _persist_task(self, task: Task):
        """持久化任务：排队、运行、终态都落盘。

        排队态也要写，否则进程崩溃后无法区分"从未提交"和"提交了但没跑"，
        启动时就无法把遗留的硬件任务标记为作废（见 `_abort_orphan_tasks`）。
        """
        persist_path = self._persist_dir / f"{task.id}.json"
        try:
            with open(persist_path, "w", encoding="utf-8") as f:
                json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log(f"Persist error: {e}")

    def _run_task(self, task: Task):
        """执行任务（由硬件通道调度线程或通用线程池调用）。"""
        _log(f"Running task {task.id} (type={task.type.value})")

        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._queue.update(task)

        try:
            # 获取处理器
            handler = self._handlers.get(task.type)
            if not handler:
                # 默认处理器
                result = self._default_handler(task)
            else:
                result = handler(task)

            task.result = result
            task.status = TaskStatus.COMPLETED
            _log(f"Task {task.id} completed")

        except Exception as e:
            _log(f"Task {task.id} error: {e}\n{traceback.format_exc()}")
            task.error = str(e)

            # 只有不占硬件的任务才自动重试：重放一次真机实验是危险动作
            gated = task.type.value in GATED_TYPES
            if not gated and task.retry_count < task.max_retries:
                task.retry_count += 1
                task.status = TaskStatus.QUEUED
                _log(f"Task {task.id} will retry ({task.retry_count}/{task.max_retries})")
                self._queue.update(task)
            else:
                task.status = TaskStatus.FAILED

        finally:
            task.completed_at = time.time()
            self._queue.update(task)
            self._persist_task(task)

            # 移除运行记录
            with self._lock:
                self._running_tasks.pop(task.id, None)

    def _default_handler(self, task: Task) -> Dict[str, Any]:
        """默认任务处理器"""
        return {
            "message": f"Task {task.id} processed",
            "type": task.type.value,
            "payload": task.payload,
        }

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理请求。

        失败响应在这里统一补齐规范语义（status ∈ ok/degraded/queued/blocked/failed +
        reasonCode），详见 services/common/errors.py 与 docs/OPERATIONS.md §3.8。
        """
        return normalize_error(self._dispatch(method, path, data, query))

    def _dispatch(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        if path == "/health":
            return self._handle_health()
        elif path == "/submit":
            return self._handle_submit(data)
        elif path == "/status":
            return self._handle_status(data)
        elif path == "/list":
            return self._handle_list(query)
        elif path == "/cancel":
            return self._handle_cancel(data)
        elif path == "/retry":
            return self._handle_retry(data)
        elif path == "/stats":
            return self._handle_stats()
        elif path == "/poll":
            return self._handle_poll()
        elif path == "/complete":
            return self._handle_complete(data)
        elif path == "/lease":
            return self._handle_lease_acquire(data)
        elif path == "/lease/status":
            return self._handle_lease_status(data)
        elif path == "/lease/release":
            return self._handle_lease_release(data)
        elif path == "/lane":
            return self._handle_lane()
        elif path.startswith("/task"):
            return self._handle_task(method, path, data, query)
        else:
            raise ValueError(f"Unknown path: {path}")

    # ── task registry / 授权票据（docs/DSL_DESIGN.md §13.3、§13.4） ─────
    def _handle_task(self, method: str, path: str,
                     data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """`/task...` 路由。

        | 方法 | 路径 | 说明 |
        |---|---|---|
        | POST | `/task` | 建 task → `{objective, stopCondition, budgets, qubits}` |
        | GET  | `/task` | 列出 task |
        | GET  | `/task/{id}` | task 现状 + 剩余额度 |
        | POST | `/task/{id}/release` | 结束 task，冻结账本 |
        | POST | `/task/{id}/refund` | 退还预扣额度（`kind=experiments\|datasets`，闸门拦下时用） |
        | GET  | `/task/{id}/report` | 审计报表（越界率/授权缺失率/代理放行率） |
        | POST | `/task/{id}/manifest` | 保存写清单 → `{manifestHash}` |
        | GET  | `/task/{id}/manifest` | 写清单列表 |
        | GET  | `/task/{id}/manifest/{hash}` | 写清单全文（给人确认的那份） |
        | POST | `/task/{id}/ticket` | 签发票据（必须绑定 manifestHash） |
        | POST | `/task/{id}/ticket/{tid}/revoke` | 吊销票据 |
        | POST | `/task/charge` | 消耗额度（超额返回 `quota_exceeded`） |
        | POST | `/task/{id}/dataset` | 回填真实数据集号（额度是下发前扣的，那时还不知道号） |
        | POST | `/task/authorize` | 判定一次写动作是否在票据内 |
        """
        parts = [p for p in str(path).split("/") if p]
        # parts[0] == "task"
        if len(parts) == 1:
            if method == "POST":
                return self._task_create(data)
            if method == "GET":
                return {"tasks": self._ledger.list_tasks()}
            return {"error": f"method {method} not allowed on /task"}

        second = parts[1]
        if second == "charge" and method == "POST":
            return self._task_charge(data)
        if second == "authorize" and method == "POST":
            return self._task_authorize(data)

        task_id = second
        if len(parts) == 2:
            if method == "GET":
                task = self._ledger.get(task_id)
                if not task:
                    return {"error": f"unknown task {task_id}"}
                return {"task": task, "remaining": self._ledger.remaining(task)}
            return {"error": f"method {method} not allowed on /task/{task_id}"}

        action = parts[2]
        if action == "release" and method == "POST":
            return self._ledger.release(task_id)
        if action == "refund" and method == "POST":
            # D1-②：闸门把节点拦下时，把预扣的额度还回去
            return self._ledger.refund(
                task_id,
                kind=str(data.get("kind") or "experiments"),
                count=int(data.get("count") or 1),
                detail={"node": data.get("node"), "why": data.get("why")},
            )
        if action == "dataset" and method == "POST":
            dataset = data.get("dataset")
            if dataset is None:
                return {"error": "需要 dataset"}
            return self._ledger.record_dataset(task_id, dataset,
                                               {"node": data.get("node")})
        if action == "report" and method == "GET":
            return self._ledger.report(task_id)
        if action == "manifest":
            if len(parts) == 3 and method == "POST":
                return self._task_save_manifest(task_id, data)
            if len(parts) == 3 and method == "GET":
                task = self._ledger.get(task_id)
                if not task:
                    return {"error": f"unknown task {task_id}"}
                return {"manifests": [{"hash": m.get("hash"), "writes": m.get("writes")}
                                      for m in task.get("manifests") or []]}
            if len(parts) == 4 and method == "GET":
                manifest = self._ledger.get_manifest(task_id, parts[3])
                if not manifest:
                    return {"error": f"unknown manifest {parts[3]}"}
                return {"manifest": manifest}
        if action == "ticket":
            if len(parts) == 3 and method == "POST":
                return self._task_issue_ticket(task_id, data)
            if len(parts) == 5 and parts[4] == "revoke" and method == "POST":
                return self._ledger.revoke_ticket(task_id, parts[3])
        return {"error": f"unknown task route {path}"}

    def _task_create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        task = self._ledger.create(
            objective=str(data.get("objective") or ""),
            stop_condition=data.get("stopCondition") or {},
            budgets=data.get("budgets") or {},
            qubits=data.get("qubits") or [],
            task_id=data.get("taskId") or None,
        )
        _log(f"task created {task['id']} budgets={task.get('budgets')}")
        return {"ok": True, "taskId": task["id"], "task": task, "remaining": self._ledger.remaining(task)}

    def _task_charge(self, data: Dict[str, Any]) -> Dict[str, Any]:
        task_id = str(data.get("taskId") or "")
        if not task_id:
            return {**error_envelope("validation_error", "缺少 taskId"),
                    "ok": False, "reason": "unknown_task", "error": "缺少 taskId"}
        kind = str(data.get("kind") or "experiments")
        raw_count = data.get("count", 1)
        # wallSeconds 是秒数（可以是小数），别被 int() 抹成 0
        count = raw_count if kind == "wallSeconds" else int(raw_count or 1)
        result = self._ledger.charge(
            task_id,
            kind=kind,
            count=count,
            dataset=data.get("dataset"),
            detail=data.get("detail"),
        )
        if not result.get("ok"):
            _log(f"quota exceeded on task {task_id}: {result.get('reason')}")
        return result

    def _task_authorize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """判定一次写动作是否在票据内。

        **闸门启用条件**（§13.4）：task 里已经有可用票据，或调用方显式
        `enforce=true`（`ctx.enforceTickets` / `QMCLAW_ENFORCE_TICKETS=1`）。
        两者都不满足时放行并注明 `not_enforced` —— 日常调试不该被一张空票据表挡住，
        但只要人开始签票，管辖立刻生效。
        """
        task_id = str(data.get("taskId") or "")
        if not task_id:
            return {**error_envelope("not_found", "缺少 taskId"),
                    "ok": False, "reason": "unknown_task", "authorizedBy": "none", "needsHuman": True}
        keys = data.get("keys") or []
        enforce = bool(data.get("enforce"))
        task = self._ledger.get(task_id)
        if task is None:
            return {**error_envelope("not_found", f"unknown task {task_id}"),
                    "ok": False, "reason": "unknown_task", "authorizedBy": "none", "needsHuman": True}
        tickets = [t for t in (task.get("tickets") or []) if not t.get("revoked")]
        if not enforce and not tickets:
            self._ledger._record(task, "authorize", {"keys": keys, "ok": True, "by": "not_enforced"})
            self._ledger._write(task)
            return {"ok": True, "reason": "未启用票据管辖（task 无票据且未 enforce）",
                    "authorizedBy": "not_enforced", "needsHuman": False}
        result = self._ledger.authorize(task_id, {
            "keys": keys,
            "qubit": data.get("qubit"),
            "deltas": data.get("deltas") or {},
        })
        if not result.get("ok"):
            _log(f"write not authorized on task {task_id}: {result.get('reason')}")
        return result

    def _task_save_manifest(self, task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        manifest = data.get("manifest") if isinstance(data.get("manifest"), dict) else data
        if not isinstance(manifest, dict) or not manifest.get("writes"):
            return {"error": "缺少 manifest.writes"}
        result = self._ledger.save_manifest(task_id, manifest)
        if result.get("ok"):
            _log(f"manifest {result.get('manifestHash')} stored on task {task_id}")
        return result

    def _task_issue_ticket(self, task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        result = self._ledger.issue_ticket(
            task_id,
            manifest_hash_value=str(data.get("manifestHash") or ""),
            scope=data.get("scope") or [],
            granted_by=str(data.get("grantedBy") or "human"),
            expires_in_seconds=int(data.get("expiresInSeconds") or 24 * 3600),
            never_delegable=data.get("neverDelegable"),
        )
        if result.get("ok"):
            _log(f"ticket {result['ticket']['id']} issued on task {task_id}")
        return result

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查 / 通道观测"""
        running = self._running_task()
        return {
            "status": "healthy",
            "service": "task_queue",
            "queue_size": len(self._sorted_gated(TaskStatus.QUEUED)),
            "running_tasks": 1 if running else 0,
            "running_task": running.to_dict() if running else None,
            "redis_available": self._redis.is_available,
            "lane": self._lane.snapshot(),
            "quantum": QUANTUM_URL,
        }

    def _handle_lane(self) -> Dict[str, Any]:
        """通道状态（前端顶栏徽标用）：扁平结构，一次拿全。

        与 `/health` 的区别：`running_task` 是任务 id 字符串（不是整条任务），
        并附 `running_label` 供界面直接显示（从提交的 code 里取函数名）。
        """
        lane = self._lane.snapshot()
        running = self._running_task()
        label = ""
        if running is not None:
            payload = getattr(running, "payload", None) or {}
            code = (payload.get("code") or "").strip()
            if code:
                label = _summarize_code(code)
            else:
                label = str(getattr(running, "type", "") or "")
        return {
            "status": "healthy",
            "service": "task_queue",
            "busy": lane.get("busy", False),
            "running_task": lane.get("running_task"),
            "running_label": label,
            "running_task_detail": running.to_dict() if running is not None else None,
            "active_lease": lane.get("active_lease"),
            "pending_leases": lane.get("pending_leases", []),
            "expired_leases": lane.get("expired_leases", 0),
            "completed": lane.get("completed", 0),
            "gated_types": lane.get("gated_types", []),
            "queue_size": len(self._sorted_gated(TaskStatus.QUEUED)),
            "redis_available": self._redis.is_available,
            "quantum": QUANTUM_URL,
        }

    # ── 租约 ──────────────────────────────────────────────────────────────
    def _handle_lease_acquire(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """申请硬件租约。

        立即返回 `granted`（True=可直接开跑；False=排队，用 lease_id 轮询
        `/lease/status`）与 `position`（0 = 下一个拿到通道）。绝不阻塞等待，
        否则调用方的 HTTP 请求会被一条 20 分钟的流程挂死。
        """
        workflow_id = data.get("workflowId") or data.get("workflow_id")
        if not workflow_id:
            return {"error": "workflowId is required"}
        existing = self._lane.find_lease_by_workflow(workflow_id)
        if existing is not None and existing.status == "active":
            existing.renew()
            return {"success": True, "granted": True, "position": 0,
                    "lease": existing.to_dict(), "reused": True}

        lease = self._lane.acquire_lease(
            workflow_id=workflow_id,
            priority=min(10, max(1, int(data.get("priority", 5)))),
            ttl=data.get("ttl"),
            yield_between_nodes=bool(data.get("yieldBetweenNodes")
                                     or data.get("yield_between_nodes")),
            owner=str(data.get("owner") or ""),
            metadata=data.get("metadata") or {},
        )
        granted = lease.status == "active"
        position = 0 if granted else self._lane.lease_position(lease.id)
        _log(f"[LEASE] {lease.id} workflow={workflow_id} granted={granted} pos={position}")
        return {
            "success": True,
            "granted": granted,
            "position": position,
            "lease_id": lease.id,
            "lease": lease.to_dict(),
            "queued": 0 if granted else len(self._sorted_gated(TaskStatus.QUEUED)),
        }

    def _handle_lease_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        lease_id = data.get("lease_id") or data.get("leaseId")
        if not lease_id:
            return {"error": "lease_id is required"}
        lease = self._lane.lease(lease_id)
        if lease is None:
            return {"error": "lease not found", "lease_id": lease_id}
        return {
            "lease_id": lease.id,
            "granted": lease.status == "active",
            "status": lease.status,
            "position": self._lane.lease_position(lease.id),
            "lease": lease.to_dict(),
            "queue_size": len(self._sorted_gated(TaskStatus.QUEUED)),
        }

    def _handle_lease_release(self, data: Dict[str, Any]) -> Dict[str, Any]:
        lease_id = data.get("lease_id") or data.get("leaseId")
        if not lease_id:
            return {"error": "lease_id is required"}
        ok = self._lane.release_lease(lease_id)
        _log(f"[LEASE] release {lease_id} ok={ok}")
        return {"success": ok, "lease_id": lease_id}

    def _dispatch_background(self, task: Task) -> None:
        """非硬件任务（分析/闲聊/图像）直接起线程，不占通道。"""
        def _run():
            self._run_task(task)
        thread = threading.Thread(target=_run, daemon=True, name=f"task-{task.id}")
        with self._lock:
            self._running_tasks[task.id] = thread
        thread.start()

    def _handle_submit(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """提交任务"""
        task_type_str = data.get("type", "general")
        try:
            task_type = TaskType(task_type_str)
        except ValueError:
            return {"error": f"Invalid task type: {task_type_str}"}

        payload = data.get("payload", {})
        priority = min(10, max(1, data.get("priority", 5)))
        timeout = data.get("timeout", 300)
        gated = task_type.value in GATED_TYPES
        # 真机实验默认不自动重试：重放一次硬件实验是危险动作
        default_retries = 0 if gated else 3
        max_retries = int(data.get("max_retries", default_retries))
        metadata = data.get("metadata", {}) or {}
        # traceId（阶段 3）：请求头 > 请求体 > metadata.traceId > 新生成
        trace_id, _trace_new = _ensure_trace(data, headers=getattr(self, "headers", None))
        if not metadata.get("traceId"):
            metadata["traceId"] = trace_id
        else:
            trace_id = str(metadata.get("traceId"))

        # 创建任务
        task = Task(
            id=self._generate_id(),
            type=task_type,
            payload=payload,
            priority=priority,
            timeout=timeout,
            max_retries=max_retries,
            metadata=metadata,
        )

        # 入队
        if self._queue.enqueue(task):
            task.status = TaskStatus.QUEUED
            self._persist_task(task)
            wf = self._workflow_of(task)
            if wf:
                # 子实验提交 = 工作流还活着，给租约续期（长流程不会被误判超时）
                self._lane.renew_for_workflow(wf)
            if gated:
                # 硬件任务只登记，由通道调度线程按租约/优先级取
                self._lane.poke()
                position = self.queue_position(task.id)
            else:
                self._dispatch_background(task)
                position = -1
            _log(f"Task {task.id} submitted (type={task_type.value}, "
                 f"priority={priority}, workflow={wf or '-'}, pos={position})")

            return {
                "success": True,
                "task_id": task.id,
                "status": task.status.value,
                "gated": gated,
                "queue_position": position,
                "lane": self._lane.snapshot(),
                "trace_id": trace_id,
            }
        else:
            return {"error": "Failed to enqueue task"}

    def _handle_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取任务状态"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        return {
            "task_id": task.id,
            "type": task.type.value,
            "status": task.status.value,
            "priority": task.priority,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "result": task.result,
            "error": task.error,
            "retry_count": task.retry_count,
            "gated": task.type.value in GATED_TYPES,
            "queue_position": self.queue_position(task.id),
            "workflow_id": self._workflow_of(task) or None,
            "trace_id": (task.metadata or {}).get("traceId") or None,
            "lane": self._lane.snapshot() if task.status in
                    (TaskStatus.QUEUED, TaskStatus.RUNNING) else None,
        }

    def _handle_list(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """列出任务"""
        status_filter = query.get("status", [None])[0] if query.get("status") else None
        type_filter = query.get("type", [None])[0] if query.get("type") else None
        limit = int(query.get("limit", ["100"])[0]) if query.get("limit") else 100

        tasks = self._queue.list_all()

        # 过滤
        if status_filter:
            try:
                status_enum = TaskStatus(status_filter)
                tasks = [t for t in tasks if t.status == status_enum]
            except ValueError:
                pass

        if type_filter:
            try:
                type_enum = TaskType(type_filter)
                tasks = [t for t in tasks if t.type == type_enum]
            except ValueError:
                pass

        # trace_id 过滤（阶段 3）：按 metadata.traceId 精确匹配
        trace_filter = query.get("trace_id", [None])[0] if query.get("trace_id") else None
        if trace_filter:
            tasks = [t for t in tasks
                     if str((t.metadata or {}).get("traceId") or "") == str(trace_filter)]

        # 排序（最新优先）
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        tasks = tasks[:limit]

        return {
            "tasks": [t.to_dict() for t in tasks],
            "count": len(tasks),
        }

    def _handle_cancel(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """取消任务"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        if task.status == TaskStatus.RUNNING:
            return {"error": "Cannot cancel running task"}

        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        self._queue.update(task)
        self._persist_task(task)

        return {"success": True, "task_id": task_id}

    def _handle_retry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """重试任务"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        if task.status not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            return {"error": "Can only retry failed or cancelled tasks"}

        task.status = TaskStatus.PENDING
        task.retry_count = 0
        task.error = None
        task.result = None
        task.completed_at = None

        self._queue.enqueue(task)

        return {"success": True, "task_id": task_id}

    def _handle_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        tasks = self._queue.list_all()

        stats = {
            "total": len(tasks),
            "by_status": {},
            "by_type": {},
        }

        for task in tasks:
            # 按状态统计
            status = task.status.value
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            # 按类型统计
            task_type = task.type.value
            stats["by_type"][task_type] = stats["by_type"].get(task_type, 0) + 1

        # 计算平均执行时间
        completed = [t for t in tasks if t.completed_at and t.started_at]
        if completed:
            exec_times = [t.completed_at - t.started_at for t in completed]
            stats["avg_execution_time"] = sum(exec_times) / len(exec_times)
            stats["max_execution_time"] = max(exec_times)
            stats["min_execution_time"] = min(exec_times)

        return stats

    def _handle_poll(self) -> Dict[str, Any]:
        """轮询获取任务（外部工作者使用）。

        只发**不占硬件**的任务：占用硬件通道的任务必须由内部调度线程按租约顺序
        逐个下发，谁 poll 到谁跑会让通道并发度失效（这正是要修的问题）。
        """
        task = None
        while True:
            candidate = self._queue.dequeue()
            if candidate is None:
                return {"task": None}
            if candidate.type.value in GATED_TYPES:
                # 放回队列，交给调度线程
                self._queue.enqueue(candidate)
                return {"task": None, "note": "gated task runs via hardware lane"}
            if candidate.status != TaskStatus.QUEUED:
                continue
            task = candidate
            break

        self._dispatch_background(task)
        return {
            "task": task.to_dict(),
        }

    # ── 实验任务处理器：task_queue 是硬件通道的唯一入口 ────────────────────
    def _run_experiment_task(self, task: Task) -> Dict[str, Any]:
        """把一条实验代码交给 quantum_service 执行，并等它真正跑完。

        走 quantum 的异步 job 接口（`POST /execute {async:true}` + 轮询 `/jobs/{id}`），
        这样本服务超时后可以 `abandon`，不会出现"上层已放弃、硬件还在跑"。
        quantum 侧自己也有一条单消费者通道，作为最后一道防线。
        """
        import urllib.request

        payload = task.payload or {}
        code = payload.get("code")
        if not code:
            raise ValueError("payload.code is required for experiment task")
        timeout = float(payload.get("timeout") or task.timeout or 300.0)

        body = json.dumps({
            "code": code,
            "async": True,
            "timeout": timeout,
            "task_id": task.id,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{QUANTUM_URL}/execute",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                started = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"failed to submit experiment to quantum: {exc}")

        job_id = started.get("job_id")
        if not job_id:
            # quantum 回了同步结果（只读/降级/stuck）：原样上报，交给上层判定
            return started

        _log(f"[EXEC] task {task.id} -> quantum job {job_id} (timeout {timeout:.0f}s)")
        deadline = time.time() + timeout + 15.0
        while True:
            time.sleep(1.0)
            try:
                with urllib.request.urlopen(f"{QUANTUM_URL}/jobs/{job_id}", timeout=15) as resp:
                    job = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                raise RuntimeError(f"failed to query quantum job {job_id}: {exc}")

            status = str(job.get("status") or "").lower()
            if status in ("success", "completed"):
                result = job.get("result")
                if not isinstance(result, dict):
                    result = {"status": "completed", "stdout": str(result or "")}
                result.setdefault("status", "completed")
                result.setdefault("job_id", job_id)
                return result
            if status in ("error", "failed"):
                return {"status": "failed", "error": job.get("error") or "quantum job failed",
                        "job_id": job_id, "stdout": (job.get("result") or {}).get("stdout", "")
                        if isinstance(job.get("result"), dict) else ""}
            if status in ("timeout", "abandoned"):
                return {"status": status, "error": job.get("error") or f"quantum job {status}",
                        "job_id": job_id}
            if time.time() > deadline:
                self._abandon_quantum_job(job_id)
                return {"status": "timeout", "job_id": job_id,
                        "error": f"experiment timed out after {timeout:.0f}s (job abandoned)"}

    def _abandon_quantum_job(self, job_id: str) -> None:
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{QUANTUM_URL}/jobs/{job_id}/abandon",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception as exc:
            _log(f"[EXEC] abandon {job_id} failed: {exc}")

    def _handle_complete(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """标记任务完成（工作者调用）"""
        task_id = data.get("task_id")
        result = data.get("result")
        error = data.get("error")

        if not task_id:
            return {"error": "task_id is required"}

        task = self._queue.get(task_id)
        if not task:
            return {"error": "Task not found"}

        task.result = result
        task.error = error
        task.status = TaskStatus.COMPLETED if not error else TaskStatus.FAILED
        task.completed_at = time.time()

        self._queue.update(task)
        self._persist_task(task)

        return {"success": True, "task_id": task_id}

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态 / 硬件通道观测"""
        running = self._running_task()
        return {
            "status": "healthy",
            "service": "task_queue",
            "queue_size": len(self._sorted_gated(TaskStatus.QUEUED)),
            "running_tasks": 1 if running else 0,
            "running_task": running.to_dict() if running else None,
            "redis_available": self._redis.is_available,
            # 通道快照：谁占着硬件、谁在排队（前端/探活脚本据此显示"前面还有几条"）
            "lane": self._lane.snapshot(),
            "quantum": QUANTUM_URL,
        }


def main():
    """主入口"""
    service = TaskQueueService(port=3009)
    run_service(service)


if __name__ == "__main__":
    main()
