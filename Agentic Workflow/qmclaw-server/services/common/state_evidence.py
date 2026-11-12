"""从执行台账推导比特状态证据（roadmap §2.4 的第二半）。

`services/common/qubit_state.py` 回答"给定证据，这个比特在哪一态"；
本模块回答"证据从哪儿来"——把 :3003 的 RunRecord 台账（真实跑过的实验）
翻译成 `derive_state()` 认的那几个键，并保留**每条证据的出处**（run_id / 数据集号 /
函数名 / 时间），因为标定评审时要能点回去看原始数据。

设计取舍：

* **只认成功的、有数据集的记录。** 失败记录（`status != success`）不产生证据，
  但会以 `ignored` 计数暴露出来，避免"跑了很多次却什么都没证明"。
* **新证据覆盖旧证据，但冲突要报。** 同一指标出现两次数值差超过 20% 时记
  `conflicts`——这正是读出链慢漂、频率被静默回写时该被看到的东西。
* **纯函数、无 IO。** 记录列表由调用方读进来（便于离线单测）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

#: 哪些厂商实验能证明"频率已确认"（跑成功且出了数据集）
FREQ_FUNCTIONS = ("spectroscopy", "spectroscopy_iq", "pidf", "ramsey_df")

#: 哪些能证明"π 脉冲幅度已标定"
AMP_FUNCTIONS = ("piamp", "pidf", "time_rabi", "rabi", "set_pi")

#: RunRecord.qubits 里没有本比特时，若函数是全局性的（看腔/看所有比特）也允许计入
GLOBAL_FUNCTIONS = ("s21", "s21_zpa2d", "s21_dis", "s21_power2d")

#: metrics 键 → 证据键（RunRecord.metrics 由 extract_metrics 填）
METRIC_KEYS = {
    "contrast": "contrast",
    "T1_us": "t1_us",
    "T2_us": "t2_star_us",
    "xeb_p": "xeb_p",
    "gate_fidelity": "xeb_gate_fidelity",
}

#: 冲突判定的相对差阈值（同一指标两次读数差超过它就要报）
CONFLICT_RELATIVE_TOLERANCE = 0.20

SUCCESS_STATUSES = ("success", "completed", "ok")


def functions_of(record: Dict[str, Any]) -> List[str]:
    """取出这条记录调过的厂商函数名（优先 manifest，退回 program 里的 ``sq.<fn>(``）。"""
    manifest = record.get("manifest") or {}
    fns = manifest.get("functions")
    if isinstance(fns, list) and fns:
        return [str(f) for f in fns if f]
    program = record.get("program") or ""
    if not program:
        return []
    import re

    return [m.group(1) for m in re.finditer(r"\bsq\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", program)]


def _record_time(record: Dict[str, Any]) -> float:
    for key in ("finished_at", "started_at"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _is_success(record: Dict[str, Any]) -> bool:
    return str(record.get("status") or "").strip().lower() in SUCCESS_STATUSES


def _prefer_record(prev: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """同一个 run_id 的两条记录，挑更"完整"的那条。

    异步执行会先落 status=queued（无数据集、无 finished_at），跑完再落完成记录。
    判据：有数据集 > 有完成时间 > 时间更晚。
    """
    def _score(rec: Dict[str, Any]) -> Tuple[int, int, float]:
        return (
            1 if (rec.get("dataset_ids") or []) else 0,
            1 if rec.get("finished_at") is not None else 0,
            _record_time(rec),
        )

    return new if _score(new) >= _score(prev) else prev


def _matches_qubit(record: Dict[str, Any], qubit: Optional[str], fns: List[str]) -> bool:
    if not qubit:
        return True
    qubits = record.get("qubits") or []
    if qubit in qubits:
        return True
    # 全局性实验（看腔/看全部比特）即便没带 qubits 也认
    return bool(set(fns) & set(GLOBAL_FUNCTIONS)) and not qubits


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def evidence_from_records(
    records: Iterable[Dict[str, Any]],
    qubit: Optional[str] = None,
) -> Dict[str, Any]:
    """把执行台账翻成证据。

    返回::

        {
          "evidence": {"freq_locked": True, "contrast": 0.74, ...},
          "sources":  {"contrast": {"run_id": ..., "dataset_ids": [..],
                                     "functions": ["iqraw"], "finished_at": ...}},
          "conflicts": [{"key": "contrast", "kept": 0.74, "dropped": 0.21,
                         "kept_run": ..., "dropped_run": ...}],
          "considered": 12,           # 与本比特相关且成功的记录数
          "ignored": 3,               # 与本比特相关但失败/无数据集的记录数
          "ignored_runs": ["run-...", ...],
        }
    """
    rows: List[Tuple[float, Dict[str, Any], List[str]]] = []
    ignored: List[str] = []
    # 一次异步执行会先落 queued、跑完再追加完成记录（同一个 run_id，阶段 15）：
    # 去重时优先保留"有数据集且有完成时间"的那条，避免 considered 虚高。
    deduped: Dict[str, Dict[str, Any]] = {}
    anonymous = 0
    for record in records or []:
        if not isinstance(record, dict):
            continue
        run_id = str(record.get("run_id") or "")
        if not run_id:
            anonymous += 1
            deduped[f"__anon_{anonymous}"] = record
            continue
        prev = deduped.get(run_id)
        deduped[run_id] = record if prev is None else _prefer_record(prev, record)
    for record in deduped.values():
        if not isinstance(record, dict):
            continue
        fns = functions_of(record)
        if not _matches_qubit(record, qubit, fns):
            continue
        if not _is_success(record) or not (record.get("dataset_ids") or []):
            # 失败、或成功却没有数据集号（证据链断裂）都不产生证据，但要留痕
            ignored.append(str(record.get("run_id") or "?"))
            continue
        rows.append((_record_time(record), record, fns))

    # 新的在前
    rows.sort(key=lambda item: item[0], reverse=True)

    evidence: Dict[str, Any] = {}
    sources: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []
    seen_values: Dict[str, Tuple[float, str]] = {}

    def _source(record: Dict[str, Any], fns: List[str]) -> Dict[str, Any]:
        return {
            "run_id": record.get("run_id"),
            "dataset_ids": list(record.get("dataset_ids") or []),
            "dataset_source": record.get("dataset_source"),
            "functions": list(fns),
            "finished_at": record.get("finished_at"),
            "status": record.get("status"),
            "trace_id": record.get("trace_id"),
        }

    for _, record, fns in rows:
        fn_set = set(fns)
        # 1) 频率 / 幅度：实验类型本身就是证据（成功 + 有数据集）
        if fn_set & set(FREQ_FUNCTIONS) and "freq_locked" not in evidence:
            evidence["freq_locked"] = True
            sources["freq_locked"] = _source(record, fns)
        if fn_set & set(AMP_FUNCTIONS) and "amp_calibrated" not in evidence:
            evidence["amp_calibrated"] = True
            sources["amp_calibrated"] = _source(record, fns)
        # 2) 数值指标
        metrics = record.get("metrics") or {}
        for metric_key, evidence_key in METRIC_KEYS.items():
            value = _number(metrics.get(metric_key))
            if value is None:
                continue
            if evidence_key in evidence:
                kept = _number(evidence[evidence_key])
                if kept is None or kept == 0:
                    continue
                relative = abs(value - kept) / abs(kept)
                if relative > CONFLICT_RELATIVE_TOLERANCE:
                    conflicts.append(
                        {
                            "key": evidence_key,
                            "kept": kept,
                            "dropped": value,
                            "relative_delta": round(relative, 4),
                            "kept_run": (sources.get(evidence_key) or {}).get("run_id"),
                            "dropped_run": record.get("run_id"),
                        }
                    )
                continue
            evidence[evidence_key] = value
            sources[evidence_key] = _source(record, fns)

    return {
        "evidence": evidence,
        "sources": sources,
        "conflicts": conflicts,
        "considered": len(rows),
        "ignored": len(ignored),
        "ignored_runs": ignored[:20],
    }


def merge_evidence(
    derived: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把调用方显式给的值叠在台账推导值上（显式的赢），返回 (evidence, sources)。

    显式值的 source 统一标成 ``{"source": "request"}``，评审时一眼能看出
    "这个数是人填的，不是某次实验测出来的"。
    """
    evidence = dict((derived or {}).get("evidence") or {})
    sources = dict((derived or {}).get("sources") or {})
    for key, value in (overrides or {}).items():
        if value is None:
            continue
        evidence[key] = value
        sources[key] = {"source": "request"}
    return evidence, sources
