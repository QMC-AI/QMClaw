"""services/common/runrecord.py — RunRecord 一等公民（纯标准库，可离线单测）

RunRecord 是"一次实验执行"的统一证据载体（docs/ARCHITECTURE_ROADMAP.md §2.3）：

    {runId, program/IR, inputs, snapshotBefore, datasetIds, snapshotAfter, diff,
     metrics, verdict}

为什么需要它（现状缺口）：
- :3003/execute 的 stdout 不打印数据集编号，事后无法把"那次执行"和"那份数据"对上；
- 直发 :3003/execute 绕过 task_queue → 没有 manifest、没有票据、没有审计；
- 厂商函数会静默改寄存器（fread/f10 auto-update），没有前后快照就无法回溯。

设计约束：
- 纯标准库：不 import 任何服务模块、不碰 LabRAD，可在 CI / 影子实例直接跑。
- 写盘失败不抛异常：审计不能因为写盘问题打断真机执行（只记 warning）。
- 目录不可写时自动降级到系统临时目录。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def new_run_id(prefix: str = "run") -> str:
    """生成一个短且可读的唯一 run id。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def program_fingerprint(code: Any) -> str:
    """程序指纹：同一段实验代码 → 同一个哈希（用于判定"是不是同一段程序"）。"""
    if not isinstance(code, str):
        code = "" if code is None else str(code)
    return hashlib.sha256(code.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class RunRecord:
    """一次实验执行的证据载体。"""

    run_id: str = field(default_factory=new_run_id)
    source: str = "execute"          # execute | run_ir | port
    governed: bool = False            # 是否经过 task_queue 治理（带 task_id）
    task_id: Optional[str] = None
    trace_id: Optional[str] = None    # 贯穿链路的 traceId（阶段 3）
    program: str = ""
    program_hash: str = ""
    qubits: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)
    status: str = "unknown"
    dataset_ids: List[int] = field(default_factory=list)
    # 数据集编号从哪儿来的（stdout / datavault / stdout+datavault / unknown）：
    # 只靠 stdout 的实验会漏号，目录差集补上后可信度不同，事后要能区分。
    dataset_source: str = "unknown"
    # 直发执行的程序清单（services/common/program_manifest.py）：函数序列、
    # 厂商可能静默回写的键、缺必填参数的预判。
    manifest: Optional[Dict[str, Any]] = None
    # 异步执行（producer 化）时对应的通道 job：一次执行先落一条 queued 记录，
    # 跑完再追加一条完成记录（同一个 run_id，读侧用 collapse_records 去重）。
    job_id: Optional[str] = None
    snapshot_before: Optional[Dict[str, Any]] = None
    snapshot_after: Optional[Dict[str, Any]] = None
    diff: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    verdict: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    error_code: Optional[str] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "run_id": self.run_id,
            "source": self.source,
            "governed": self.governed,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "program_hash": self.program_hash,
            "qubits": list(self.qubits),
            "status": self.status,
            "dataset_ids": list(self.dataset_ids),
            "dataset_source": self.dataset_source,
            "metrics": dict(self.metrics),
            "verdict": self.verdict,
            "warnings": list(self.warnings),
            "error_code": self.error_code,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "inputs": self.inputs,
            "diff": self.diff,
        }
        if self.snapshot_before is not None:
            data["snapshot_before"] = self.snapshot_before
        if self.snapshot_after is not None:
            data["snapshot_after"] = self.snapshot_after
        if self.program:
            # 程序原文可能很长，只在显式要求时保留（默认只留指纹）
            data["program_length"] = len(self.program)
        if self.manifest is not None:
            data["manifest"] = self.manifest
        if self.job_id:
            data["job_id"] = self.job_id
        return data

    @classmethod
    def from_execute(
        cls,
        code: str,
        *,
        task_id: Optional[str] = None,
        qubits: Optional[List[str]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
    ) -> "RunRecord":
        """从一次 :3003/execute 请求构造 RunRecord（尚未执行完，status=pending）。"""
        governed = bool(task_id and str(task_id).strip())
        rec = cls(
            source="execute",
            governed=governed,
            task_id=task_id,
            trace_id=trace_id,
            program=code or "",
            program_hash=program_fingerprint(code),
            qubits=list(qubits or []),
            inputs=dict(inputs or {}),
            status="pending",
        )
        if not governed:
            rec.warnings.append(
                "shadow: 未携带 task_id，本次执行未经过 task_queue 治理"
                "（额度/票据/审计均未生效）；正式路径见 :3008/workflows/run_ir"
            )
        return rec


class RunRecordWriter:
    """RunRecord 的追加式落盘器（jsonl，每行一条）。

    - 目录不存在则创建；不可写则降级到系统临时目录（审计不打断执行）。
    - 线程安全（服务是多线程的）。
    """

    def __init__(self, directory: Optional[str] = None):
        self._lock = threading.Lock()
        self._dir = directory or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data",
            "run-records",
        )
        self._active_dir = self._dir
        self._degraded = False

    @property
    def directory(self) -> str:
        return self._active_dir

    def _ensure_dir(self) -> str:
        try:
            os.makedirs(self._active_dir, exist_ok=True)
            probe = os.path.join(self._active_dir, ".probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return self._active_dir
        except Exception:
            if not self._degraded:
                self._degraded = True
                self._active_dir = os.path.join(tempfile.gettempdir(), "qmclaw-run-records")
                try:
                    os.makedirs(self._active_dir, exist_ok=True)
                except Exception:
                    pass
            return self._active_dir

    def append(self, record: RunRecord) -> Optional[str]:
        """写入一条记录；返回文件路径，失败返回 None（不抛异常）。"""
        try:
            path = self._ensure_dir()
            if not path:
                return None
            fname = os.path.join(path, f"{time.strftime('%Y%m%d')}.jsonl")
            with self._lock:
                with open(fname, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
            return fname
        except Exception:
            return None

    def list_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        """读取最近若干条记录（倒序）。"""
        try:
            path = self._ensure_dir()
            if not path or not os.path.isdir(path):
                return []
            out: List[Dict[str, Any]] = []
            for fname in sorted(os.listdir(path), reverse=True):
                if not fname.endswith(".jsonl"):
                    continue
                with open(os.path.join(path, fname), "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            continue
                if len(out) >= limit:
                    break
            out.reverse()
            return out[: max(0, int(limit))]
        except Exception:
            return []


def collapse_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 run_id 去重，保留**最后一条**（同一 run 会先落 queued、后落完成态）。

    - 没有 run_id 的记录原样保留（旧数据/外部写入）；
    - 保持原有时间顺序：被后续覆盖的记录移动到"最后一条"的位置；
    - 不修改传入对象。
    """
    if not isinstance(records, list):
        return []
    out: List[Optional[Dict[str, Any]]] = []
    index: Dict[str, int] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        run_id = rec.get("run_id")
        if run_id and run_id in index:
            out[index[run_id]] = None  # 旧的那条让位给最后一次写入
        if run_id:
            index[run_id] = len(out)
        out.append(rec)
    return [rec for rec in out if rec is not None]


# ── 审计查询过滤（阶段 18）───────────────────────────────────────────────────
# 记录里的 status 是执行生命周期值（success/error/timeout/…），规范状态要看
# 错误码；这里给出唯一映射，避免每个调用方各写一份 if-else。
_SUCCESS_STATUSES = ("success", "completed", "ok", "queued", "running")
_FAILED_STATUSES = ("error", "failed", "timeout", "cancelled", "rejected")


def record_canonical_status(record: Dict[str, Any]) -> Optional[str]:
    """一条 RunRecord 的规范状态（`ok`/`blocked`/`degraded`/`failed`）。

    优先级：错误码 → 生命周期值；认不出返回 None（不做猜测）。
    """
    if not isinstance(record, dict):
        return None
    code = record.get("error_code") or record.get("reasonCode")
    if code:
        try:
            from .errors import canonical_status

            mapped = canonical_status(str(code))
        except Exception:
            mapped = None
        if mapped:
            return mapped
        return "failed"
    status = str(record.get("status") or "").strip().lower()
    if status in _SUCCESS_STATUSES:
        return "ok"
    if status in _FAILED_STATUSES:
        return "failed"
    if status in ("blocked", "degraded"):
        return status
    return None


def _as_epoch(value: Any) -> Optional[float]:
    """数字或 ISO 时间串 → epoch 秒；认不出返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if _is_number(value):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone

        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        # 不带时区的 ISO 串按 UTC 解释：审计查询要可复现，不能随机器本地时区漂移。
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _record_time(record: Dict[str, Any]) -> Optional[float]:
    for key in ("finished_at", "started_at", "created_at"):
        value = _as_epoch(record.get(key))
        if value is not None:
            return value
    return None


def _as_qubit_set(value: Any) -> set:
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value).replace(",", " ").split()
    return {str(item).strip() for item in items if str(item).strip()}


def filter_records(
    records: List[Dict[str, Any]],
    qubit: Any = None,
    status: Any = None,
    source: Any = None,
    dataset_id: Any = None,
    trace_id: Any = None,
    since: Any = None,
    until: Any = None,
) -> List[Dict[str, Any]]:
    """按审计口径过滤 RunRecord（纯函数，顺序不变，不修改输入）。

    - `qubit`：单值或列表/逗号分隔；记录 `qubits` 任一提及即命中（全局实验 qubits 为空则不命中）。
    - `status`：规范状态（ok/failed/blocked/degraded）或原始生命周期值（success/error/…）。
    - `source`：`execute` / `workflow` / … 精确匹配。
    - `dataset_id`：该数据集号出现在 `dataset_ids` 里。
    - `trace_id`：精确匹配。
    - `since`/`until`：epoch 秒或 ISO 串，按 `finished_at → started_at → created_at` 比较。
    """
    if not isinstance(records, list):
        return []
    qubits = _as_qubit_set(qubit)
    status_text = str(status).strip().lower() if status not in (None, "") else ""
    source_text = str(source).strip() if source not in (None, "") else ""
    trace_text = str(trace_id).strip() if trace_id not in (None, "") else ""
    dataset = None
    if dataset_id not in (None, ""):
        try:
            dataset = int(dataset_id)
        except (TypeError, ValueError):
            dataset = -1  # 给了非数字就必然不命中，而不是忽略过滤条件
    since_epoch = _as_epoch(since)
    until_epoch = _as_epoch(until)

    out: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if qubits:
            own = _as_qubit_set(record.get("qubits"))
            if not (own & qubits):
                continue
        if status_text:
            raw = str(record.get("status") or "").strip().lower()
            canonical = record_canonical_status(record)
            if status_text not in (raw, canonical):
                continue
        if source_text and str(record.get("source") or "").strip() != source_text:
            continue
        if trace_text and str(record.get("trace_id") or "").strip() != trace_text:
            continue
        if dataset is not None:
            ids = record.get("dataset_ids") or []
            hits = []
            for item in ids if isinstance(ids, list) else [ids]:
                try:
                    hits.append(int(item))
                except (TypeError, ValueError):
                    continue
            if dataset not in hits:
                continue
        if since_epoch is not None or until_epoch is not None:
            when = _record_time(record)
            if when is None:
                continue  # 时间不可判定时不放进时间窗（宁缺毋错）
            if since_epoch is not None and when < since_epoch:
                continue
            if until_epoch is not None and when > until_epoch:
                continue
        out.append(record)
    return out



def diff_snapshots(
    before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """两次 registry 快照的差异（只报告变化的键）。"""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {}
    bkeys, akeys = set(before.keys()), set(after.keys())
    changed: Dict[str, Any] = {}
    for key in sorted(bkeys & akeys):
        if before[key] != after[key]:
            changed[key] = {"from": before[key], "to": after[key]}
    added = {k: after[k] for k in sorted(akeys - bkeys)}
    removed = {k: before[k] for k in sorted(bkeys - akeys)}
    result: Dict[str, Any] = {"changed": changed}
    if added:
        result["added"] = added
    if removed:
        result["removed"] = removed
    return result


# 结果里可能带的可判定指标名（与 dsl/executor.py 的 keep 列表保持一致）
RUN_METRIC_KEYS = (
    "contrast",
    "p1_i",
    "p1_x",
    "separation",
    "snr",
    "dmu_sig",
    "T1_us",
    "T2_us",
    "R_squared",
    "xeb_p",
    "gate_fidelity",
)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_metrics(result: Any) -> Dict[str, Any]:
    """从 execute 结果里挑出可判定的数值指标。

    只取"结果里真的写了"的键，**不猜不补**：`sq.iqraw` 的 stdout 里没有数据集号，
    同理没有对比度就不该编一个出来。
    """
    metrics: Dict[str, Any] = {}
    if not isinstance(result, dict):
        return metrics
    nested = result.get("metrics")
    if isinstance(nested, dict):
        for key, value in nested.items():
            if _is_number(value):
                metrics[str(key)] = value
    for key in RUN_METRIC_KEYS:
        value = result.get(key)
        if _is_number(value):
            metrics.setdefault(key, value)
    return metrics


def derive_verdict(
    metrics: Optional[Dict[str, Any]], status: Optional[str] = None
) -> Optional[str]:
    """用 Metric Registry 给一次执行下整体结论：`ok` / `warn` / `fail`。

    - 没有可判定指标 → None（不编造结论）
    - status 明确失败 → `fail`
    - 全部指标达标 → `ok`；全部不达标 → `fail`；部分达标 → `warn`
    """
    if status and str(status).lower() in ("failed", "error", "timeout"):
        pass  # 仍先看指标，但最终以失败为准
    if not isinstance(metrics, dict) or not metrics:
        return "fail" if status and str(status).lower() in ("failed", "error", "timeout") else None
    try:
        from services.common.metrics import evaluate_all

        evaluated = evaluate_all(metrics)
    except Exception:
        return None
    flags = [bool(item.get("ok")) for item in evaluated.values() if isinstance(item, dict)]
    if not flags:
        return None
    if all(flags):
        return "ok"
    if not any(flags):
        return "fail"
    return "warn"
