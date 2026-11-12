"""services/quantum_service/writer.py — 单写者内核（阶段 2 第三批）

单写者原则（docs/ARCHITECTURE_ROADMAP.md §2.1）：**整个系统对硬件只能有一个写入者**。
现状 `task_queue` 已实现"硬件通道并发恒 1"，但 `:3003/execute` 是旁路——直接进 LabRAD，
不经队列/票据/lint，治理层因此只是"可选装饰"。

本模块提供**可离线单测**的单写者内核：

    writer = SingleWriter()
    writer.start()
    result = writer.submit(op="execute", fn=lambda: do_experiment())

行为要点：
- 真正只有一个 worker 线程消费 FIFO 队列 → 硬件操作天然串行；
- `submit()` 返回统一结果信封：{"status": "success"|"error"|"timeout", "result", "error", ...}；
  失败分支用 `error_envelope()` 生成（status 为规范状态 ok/degraded/queued/blocked/failed，
  并带 reasonCode）；
- 单个 op 抛异常不会打断 worker（隔离），后续 op 继续执行；
- 超时只影响调用方的等待，不杀 worker（与本仓库"实验超时标记 stuck、由人工确认"的既有约定一致）。

三种模式（配置 `quantum_service.actuation.writer_mode`，每请求读取 → 可热切）：
- ``off``     —— 不启用单写者（保持现状，默认）；
- ``shadow``  —— 只观测：记录"本应经由单写者"的操作，不改变执行路径（**默认**）；
- ``enforce`` —— 真正经由唯一 worker 执行。

默认 ``shadow`` 是刻意的：升级不能打断用户正在跑的真机实验（见 §4.2.1 真机实验前置授权协议）。
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from ..common.errors import error_envelope


WRITER_MODES = ("off", "shadow", "enforce")

DEFAULT_WRITER_MODE = "shadow"


def resolve_writer_mode(flags: Optional[Dict[str, Any]] = None) -> str:
    """从写闸门配置里解析单写者模式。

    Args:
        flags: `get_actuation_flags("quantum_service")` 的返回值；
               也接受含 `writer_mode` 键的普通 dict。

    Returns:
        "off" / "shadow" / "enforce" 之一（非法值回落到默认 shadow）。
    """
    flags = flags or {}
    raw = flags.get("writer_mode") or flags.get("single_writer_mode") or DEFAULT_WRITER_MODE
    mode = str(raw).strip().lower()
    return mode if mode in WRITER_MODES else DEFAULT_WRITER_MODE


class _Job:
    """一次待执行的操作。"""

    __slots__ = ("job_id", "op", "fn", "event", "result", "enqueued_at", "started_at", "finished_at")

    def __init__(self, job_id: str, op: str, fn: Callable[[], Any]):
        self.job_id = job_id
        self.op = op
        self.fn = fn
        self.event = threading.Event()
        self.result: Optional[Dict[str, Any]] = None
        self.enqueued_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None


class SingleWriter:
    """唯一写入者：单 worker 线程 + FIFO 队列。"""

    def __init__(self, name: str = "hardware-writer"):
        self.name = name
        self._queue: "queue.Queue[Optional[_Job]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {
            "submitted": 0,
            "completed": 0,
            "failed": 0,
            "timed_out": 0,
            "rejected": 0,
            "max_queue_depth": 0,
        }
        self._shadow: List[Dict[str, Any]] = []

    # ---------- 生命周期 ----------

    def start(self) -> "SingleWriter":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._queue.put(None)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    # ---------- 提交 ----------

    def submit(
        self,
        fn: Callable[[], Any],
        *,
        op: str = "execute",
        timeout: Optional[float] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """把一次硬件操作交给唯一 worker 执行。

        Args:
            fn: 无参可调用，返回值原样放进 result["result"]。
            op: 操作名（审计用），如 "execute" / "set_params"。
            timeout: 调用方等待上限（秒）；None 表示一直等。
            task_id: 关联任务号（审计用）。

        Returns:
            {"status", "result", "error", "job_id", "op", "task_id", "duration"}。
            status ∈ {"success"} ∪ 规范失败状态（failed / blocked，见 services/common/errors.py）。
        """
        job = _Job(f"w-{uuid.uuid4().hex[:12]}", op, fn)
        with self._lock:
            self._counters["submitted"] += 1
            depth = self._queue.qsize()
            if depth > self._counters["max_queue_depth"]:
                self._counters["max_queue_depth"] = depth

        if self._stop.is_set():
            with self._lock:
                self._counters["rejected"] += 1
            return {
                **error_envelope("not_authorized", "单写者已停止，无法接收新的硬件操作"),
                "job_id": job.job_id,
                "op": op,
                "task_id": task_id,
                "result": None,
                "duration": 0.0,
            }

        self._queue.put(job)

        wait_s = None if timeout is None else float(timeout)
        finished = job.event.wait(timeout=wait_s)
        if not finished:
            with self._lock:
                self._counters["timed_out"] += 1
            return {
                **error_envelope(
                    "timeout",
                    f"等待单写者执行超时（{wait_s:.0f}s），操作可能仍在真机上进行",
                ),
                "job_id": job.job_id,
                "op": op,
                "task_id": task_id,
                "result": None,
                "duration": time.time() - job.enqueued_at,
            }
        return job.result or {
            **error_envelope("internal_error", "单写者未返回结果"),
            "job_id": job.job_id,
            "op": op,
            "task_id": task_id,
            "result": None,
            "duration": 0.0,
        }

    # ---------- 影子观测 ----------

    def observe(self, op: str, task_id: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
        """影子模式：记录"本次操作本应经由单写者"，不改变执行路径。"""
        entry = {
            "op": op,
            "task_id": task_id,
            "at": time.time(),
        }
        entry.update(extra)
        with self._lock:
            self._shadow.append(entry)
            if len(self._shadow) > 500:
                del self._shadow[:-500]
        return entry

    def shadow_summary(self) -> Dict[str, Any]:
        with self._lock:
            entries = list(self._shadow)
        by_op: Dict[str, int] = {}
        for entry in entries:
            key = str(entry.get("op") or "unknown")
            by_op[key] = by_op.get(key, 0) + 1
        return {"observed": len(entries), "by_op": by_op, "entries": entries[-20:]}

    # ---------- worker ----------

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:  # 停止信号
                break
            job.started_at = time.time()
            try:
                value = job.fn()
                job.result = {
                    "status": "success",
                    "error": None,
                    "result": value,
                    "job_id": job.job_id,
                    "op": job.op,
                    "duration": time.time() - job.started_at,
                }
                with self._lock:
                    self._counters["completed"] += 1
            except Exception as exc:  # noqa: BLE001 — 单个 op 失败不能带走 worker
                job.result = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "result": None,
                    "job_id": job.job_id,
                    "op": job.op,
                    "duration": time.time() - job.started_at,
                }
                with self._lock:
                    self._counters["failed"] += 1
            finally:
                job.finished_at = time.time()
                job.event.set()

    # ---------- 观测 ----------

    def stats(self, mode: str = DEFAULT_WRITER_MODE) -> Dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
        return {
            "name": self.name,
            "mode": mode,
            "running": bool(self._thread and self._thread.is_alive()),
            "queued": self._queue.qsize(),
            **counters,
        }
