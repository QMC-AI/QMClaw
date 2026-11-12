"""services/task_queue/lane.py - 硬件通道单槽位调度内核

为什么是两层粒度（而不是二选一）：

  执行层 = 单个测控实验。硬件通道是物理资源，同一毫秒只能有一条脉冲序列在飞，
          所以真正的互斥单位必须是"一个实验"，通道并发度恒为 1。

  调度层 = qwf 任务（租约）。标定结果的可信度依赖序列不被插入：一次 `sq.pidf`
          能把 f10 挪走几十 MHz，`sq.iqraw` 每次都会重写 discriminator 中心，
          `sq.s21_dis` 甚至会无条件改写 fread。别的实验若插进一条 18 步流程中间，
          前面几步的标定就静默作废（不报错，只给出自洽的错数字）。
          所以工作流以"租约"的形式整段占住通道。

  两者结合：租约在存续期间持有通道优先权，其子实验排在队首；租约结束后单实验才进。
  租约默认整段独占，`yieldBetweenNodes=True` 时才允许别的单实验在"当前没有本租约
  子任务在排队"的空隙（即节点边界，此时没有脉冲在飞）插进来。

本模块只管调度，不认识 HTTP、不认识 Task 类的具体字段（通过回调注入）。
"""

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

# 只有真正占用硬件通道的任务类型会被闸住；分析/闲聊/图像分类不碰硬件，不排队
GATED_TYPES = frozenset({"experiment"})


class Lease:
    """硬件租约：一个工作流整段独占硬件通道的预约。"""

    __slots__ = (
        "id", "workflow_id", "priority", "created_at", "granted_at",
        "expires_at", "ttl", "yield_between_nodes", "status", "owner", "metadata",
    )

    def __init__(self, workflow_id: str, priority: int = 5, ttl: float = 600.0,
                 yield_between_nodes: bool = False, owner: str = "",
                 metadata: Optional[Dict[str, Any]] = None):
        self.id = f"lease_{uuid.uuid4().hex[:10]}"
        self.workflow_id = str(workflow_id or "")
        self.priority = int(priority or 5)
        self.created_at = time.time()
        self.granted_at: Optional[float] = None
        self.ttl = float(ttl or 600.0)
        self.expires_at: Optional[float] = None
        self.yield_between_nodes = bool(yield_between_nodes)
        self.status = "pending"          # pending | active | released | expired
        self.owner = owner or ""
        self.metadata = metadata or {}

    # ── 生命周期 ────────────────────────────────────────────────────────
    def grant(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.status = "active"
        self.granted_at = now
        self.expires_at = now + self.ttl

    def renew(self, now: Optional[float] = None) -> None:
        """续期：每提交一个子实验、每次心跳都会续，避免长流程被误判超时。"""
        if self.status == "active":
            now = time.time() if now is None else now
            self.expires_at = now + self.ttl

    def close(self, status: str) -> None:
        if self.status in ("active", "pending"):
            self.status = status
            self.expires_at = None

    @property
    def alive(self) -> bool:
        return self.status in ("pending", "active")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lease_id": self.id,
            "workflow_id": self.workflow_id,
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "ttl": self.ttl,
            "yield_between_nodes": self.yield_between_nodes,
            "owner": self.owner,
            "metadata": self.metadata,
            "remaining_s": (max(0.0, self.expires_at - time.time())
                            if self.expires_at else None),
        }


def _sort_key_lease(lease: Lease):
    return (-lease.priority, lease.created_at)


class HardwareLane:
    """硬件通道单槽位调度器。

    回调注入（避免本模块依赖 Task / BaseService）：
      pending_provider() -> List[task]     当前排队的"占用硬件"任务，已按优先级排序
      workflow_of(task)  -> str            任务所属工作流（租约匹配用）
      on_run(task)       -> None           真正执行（阻塞到实验结束）
    """

    def __init__(self, pending_provider: Callable[[], List[Any]],
                 workflow_of: Callable[[Any], str],
                 on_run: Callable[[Any], None],
                 default_ttl: float = 600.0, poll_interval: float = 0.25):
        self._pending = pending_provider
        self._workflow_of = workflow_of
        self._on_run = on_run
        self._default_ttl = float(default_ttl)
        self._poll = float(poll_interval)

        self._lock = threading.RLock()
        self._leases: Dict[str, Lease] = {}
        self._active_id: Optional[str] = None
        self._running_task_id: Optional[str] = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._expired_count = 0
        self._completed_count = 0

    # ── 生命周期 ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hw-lane", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        """有新任务 / 租约变化时叫醒调度线程。"""
        self._wake.set()

    # ── 租约 ────────────────────────────────────────────────────────────
    def acquire_lease(self, workflow_id: str, priority: int = 5,
                      ttl: Optional[float] = None, yield_between_nodes: bool = False,
                      owner: str = "", metadata: Optional[Dict[str, Any]] = None) -> Lease:
        lease = Lease(
            workflow_id=workflow_id,
            priority=priority,
            ttl=self._default_ttl if ttl is None else ttl,
            yield_between_nodes=yield_between_nodes,
            owner=owner,
            metadata=metadata,
        )
        with self._lock:
            self._leases[lease.id] = lease
            # 快路径：通道没被占、前面没有更早的租约、也没有已排队的单实验
            # → 立刻授予，调用方无须轮询就能直接开跑。
            # 若有单实验先到，则不抢：留给 _pick 按 (优先级, 到达时间) 公平裁决。
            if self._active_id is None and not self._leases_ahead(lease) \
                    and not list(self._pending() or []):
                self._grant_locked(lease, time.time())
        self.poke()
        return lease

    def lease(self, lease_id: str) -> Optional[Lease]:
        with self._lock:
            return self._leases.get(lease_id)

    def find_lease_by_workflow(self, workflow_id: str) -> Optional[Lease]:
        with self._lock:
            for lease in self._leases.values():
                if lease.workflow_id == workflow_id and lease.alive:
                    return lease
        return None

    def release_lease(self, lease_id: str) -> bool:
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                return False
            lease.close("released")
            if self._active_id == lease.id:
                self._active_id = None
        self.poke()
        return True

    def renew_for_workflow(self, workflow_id: str) -> Optional[Lease]:
        with self._lock:
            lease = self._active_locked()
            if lease is not None and lease.workflow_id == str(workflow_id or ""):
                lease.renew()
                return lease
        return None

    def lease_position(self, lease_id: str) -> int:
        """0 = 下一个拿到通道的租约；-1 = 不是 pending（已授予或已结束）。"""
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None or lease.status != "pending":
                return -1
            pending = sorted(
                (l for l in self._leases.values() if l.status == "pending"),
                key=_sort_key_lease,
            )
            for idx, item in enumerate(pending):
                if item.id == lease_id:
                    return idx
            return -1

    # ── 调度 ────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._pick()
            except Exception:                     # 调度故障不能让线程死掉
                task = None
            if task is None:
                self._wake.wait(self._poll)
                self._wake.clear()
                continue
            with self._lock:
                self._running_task_id = getattr(task, "id", "?")
            try:
                self._on_run(task)
                self._completed_count += 1
            except Exception:
                pass
            finally:
                with self._lock:
                    self._running_task_id = None
                self.poke()

    def _pick(self) -> Optional[Any]:
        with self._lock:
            now = time.time()
            self._expire_locked(now)
            if self._running_task_id is not None:
                return None

            pending = list(self._pending() or [])
            active = self._active_locked()

            if active is not None:
                mine = [t for t in pending
                        if self._workflow_of(t) == active.workflow_id]
                if mine:
                    return mine[0]
                if active.yield_between_nodes and pending:
                    # 节点边界让路：本租约此刻没有子任务在排队，允许单实验插进来
                    return pending[0]
                return None                       # 通道留给租约，别人不许插

            head_task = pending[0] if pending else None
            head_lease = self._head_pending_lease_locked()
            if head_lease is not None and (
                head_task is None or _sort_key_lease(head_lease) <=
                (-int(getattr(head_task, "priority", 5)),
                 float(getattr(head_task, "created_at", 0.0)))
            ):
                self._grant_locked(head_lease, now)
                return None                       # 等它的第一个子实验提交上来
            return head_task

    # ── 内部工具（调用方持有 _lock） ──────────────────────────────────────
    def _active_locked(self) -> Optional[Lease]:
        if self._active_id is None:
            return None
        lease = self._leases.get(self._active_id)
        if lease is None or lease.status != "active":
            self._active_id = None
            return None
        return lease

    def _leases_ahead(self, lease: Lease) -> bool:
        """是否还有更早/更高优先级的 pending 租约——有就不能插队授予。"""
        for other in self._leases.values():
            if other is lease or other.status != "pending":
                continue
            if _sort_key_lease(other) < _sort_key_lease(lease):
                return True
        return False

    def _head_pending_lease_locked(self) -> Optional[Lease]:
        pending = [l for l in self._leases.values() if l.status == "pending"]
        if not pending:
            return None
        return min(pending, key=_sort_key_lease)

    def _grant_locked(self, lease: Lease, now: float) -> None:
        lease.grant(now)
        self._active_id = lease.id

    def _expire_locked(self, now: float) -> None:
        for lease in list(self._leases.values()):
            if lease.status == "active" and lease.expires_at and now > lease.expires_at:
                lease.close("expired")
                self._expired_count += 1
                if self._active_id == lease.id:
                    self._active_id = None

    # ── 观测 ────────────────────────────────────────────────────────────
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            active = self._active_locked()
            pending_leases = sorted(
                (l for l in self._leases.values() if l.status == "pending"),
                key=_sort_key_lease,
            )
            return {
                "busy": self._running_task_id is not None,
                "running_task": self._running_task_id,
                "active_lease": active.to_dict() if active else None,
                "pending_leases": [l.to_dict() for l in pending_leases],
                "expired_leases": self._expired_count,
                "completed": self._completed_count,
                "gated_types": sorted(GATED_TYPES),
            }
