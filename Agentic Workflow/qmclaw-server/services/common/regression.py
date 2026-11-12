"""回放回归（QMClaw Architecture Roadmap §2.6 Replay → CI 覆盖真机链路）。

思路
----
真机 fixture 里同时记着两件事：

* **真机当时的结果**（``registry_after``）：厂商实验实际把哪些键改成了什么；
* **仿真结果**（``simulate(code, registry_before)``）：我们以为它会改什么。

两者的差就是**厂商语义漂移**。把这套比对做成一个可批量跑的回归，
CI 每次跑一遍：有漂移就红，等于"厂商换了行为/我们的语义模型过期了"会立刻暴露，
而不是等真机跑偏了才发现。

本模块**不连设备**，纯离线；fixture 缺失时给出 ``empty`` 结论而不是假装通过。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional

from .replay import ReplayStore, replay_fixture

# 漂移种类 → 人话
DRIFT_LABELS = {
    "simulated_only": "仿真以为会改，真机没改",
    "recorded_only": "真机改了，仿真没预测到",
    "value_mismatch": "两边都改了，但值不一样",
}


def classify_fixture(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """回放单个 fixture，返回一条可读的用例结论。"""
    result = replay_fixture(fixture)
    drift = result.get("drift") or []
    kinds: Dict[str, int] = {}
    for item in drift:
        kind = str(item.get("kind") or "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
    notes = list(result.get("notes") or [])
    # 真机 fixture 若没有 registry 快照，值级比对根本没发生 —— 结论"无漂移"是**弱结论**，
    # 必须明说，否则 CI 会把"没比"当成"比过了"。
    if not (fixture.get("registry_after") or {}):
        notes.append("fixture 没有 registry_after 快照：未做值级比对（无漂移 ≠ 一致）")
    return {
        "fixture_id": fixture.get("fixture_id") or result.get("fixture_id"),
        "created_at": fixture.get("created_at"),
        "source": fixture.get("source"),
        "qubits": list(fixture.get("qubits") or []),
        "dataset_ids": list(fixture.get("dataset_ids") or []),
        "code_head": str(fixture.get("code") or "").strip().splitlines()[:1],
        "ok": bool(result.get("ok")),
        "drift_count": len(drift),
        "drift_kinds": kinds,
        "drift": drift,
        "simulated_diff": result.get("simulated_diff") or {},
        "recorded_diff": result.get("recorded_diff") or {},
        "has_registry": bool(fixture.get("registry_after") or {}),
        "notes": notes,
    }


def run_regression(
    fixtures: Iterable[Dict[str, Any]],
    *,
    max_drift: int = 0,
) -> Dict[str, Any]:
    """批量回放，返回一份回归结论。

    ``max_drift``：允许的漂移条数上限（默认 0 = 一条漂移就红）。
    """
    cases: List[Dict[str, Any]] = []
    for fixture in fixtures or []:
        try:
            cases.append(classify_fixture(fixture))
        except Exception as exc:  # 单条 fixture 坏了 shouldn't 拖垮整轮回归
            cases.append({
                "fixture_id": fixture.get("fixture_id"),
                "ok": False,
                "error": repr(exc),
                "drift_count": 0,
                "drift": [],
            })
    total = len(cases)
    broken = [c for c in cases if c.get("error")]
    drifted = [c for c in cases if not c.get("error") and c.get("drift_count", 0) > 0]
    passed = [c for c in cases if not c.get("error") and not c.get("drift_count", 0)]
    drift_total = sum(c.get("drift_count", 0) for c in cases if not c.get("error"))
    verdict = "green"
    if total == 0:
        verdict = "empty"
    elif broken:
        verdict = "broken"
    elif drift_total > max_drift:
        verdict = "drift"
    return {
        "verdict": verdict,
        "total": total,
        "passed": len(passed),
        "drifted": len(drifted),
        "broken": len(broken),
        "drift_total": drift_total,
        "max_drift": max_drift,
        "cases": cases,
        "notes": [
            "empty = 还没有真机 fixture；回归未覆盖真机链路，不等于通过",
            "drift = 仿真与真机不一致（厂商语义变了，或我们的语义模型过期）",
        ],
    }


def load_fixtures(
    directory: Optional[str] = None,
    limit: int = 50,
    store: Optional[ReplayStore] = None,
) -> List[Dict[str, Any]]:
    """从 fixture 目录读回 fixture 全量内容（list 只给摘要，这里补齐）。

    可以传入外部 ``store``，保证列目录和读内容用的是**同一个**实例 ——
    否则两个实例各自的降级状态可能不同，报告就会指错目录。
    """
    store = store or ReplayStore(directory)
    fixtures: List[Dict[str, Any]] = []
    for item in store.list_fixtures(limit=limit):
        fid = item.get("fixture_id") if isinstance(item, dict) else None
        if not fid:
            continue
        full = store.load(fid)
        if isinstance(full, dict):
            fixtures.append(full)
    return fixtures


def regression_from_directory(
    directory: Optional[str] = None,
    limit: int = 50,
    max_drift: int = 0,
) -> Dict[str, Any]:
    """端到端：读目录 → 回放 → 出结论。"""
    store = ReplayStore(directory)
    requested = directory or store.directory
    fixtures = load_fixtures(directory, limit=limit, store=store)
    report = run_regression(fixtures, max_drift=max_drift)
    read_dir = store.read_directory
    report["requested_directory"] = requested
    report["directory"] = read_dir
    report["degraded_store"] = bool(store.degraded)
    if store.degraded:
        report.setdefault("notes", []).append(
            "目标目录不可写，已降级到临时目录：读到的 fixture 可能不是服务端写的那批"
            "（用 QMCLAW_REPLAY_FIXTURE_DIR 显式指定同一目录）"
        )
    if read_dir != requested:
        report.setdefault("notes", []).append(
            "实际读取目录与请求目录不一致（%s ≠ %s）：读到的不是你要的那批 fixture"
            % (read_dir, requested)
        )
    return report


def format_report(report: Dict[str, Any]) -> str:
    """人读的报告（CLI 用）。"""
    lines: List[str] = []
    verdict = report.get("verdict")
    lines.append("=" * 68)
    lines.append("QMClaw 回放回归（fixture → 仿真 vs 真机）")
    lines.append("=" * 68)
    lines.append(f"目录   : {report.get('directory') or '(默认)'}")
    requested = report.get("requested_directory")
    if requested and requested != report.get("directory"):
        lines.append(f"请求目录: {requested}")
        lines.append("  !! 实际读取目录 ≠ 请求目录 —— 读到的不是你要的那批 fixture")
    if report.get("degraded_store"):
        lines.append("  !! 目标目录不可写，实际读的是临时目录 —— 与服务端写的可能不是同一批 fixture")
    weak = [c for c in (report.get("cases") or []) if not c.get("has_registry")]
    if weak:
        lines.append(
            "  !! %s/%s 条 fixture 没有 registry 快照（无漂移 ≠ 一致，属弱结论）"
            % (len(weak), len(report.get("cases") or []))
        )
    lines.append(
        "结论   : %s（用例 %s，通过 %s，漂移 %s，坏例 %s，漂移条数 %s）"
        % (
            verdict, report.get("total"), report.get("passed"),
            report.get("drifted"), report.get("broken"), report.get("drift_total"),
        )
    )
    for case in report.get("cases") or []:
        head = (case.get("code_head") or [""])[0]
        if case.get("error"):
            lines.append(f"  [BROKEN] {case.get('fixture_id')}: {case['error']}")
            continue
        mark = "OK " if case.get("drift_count", 0) == 0 else "DRIFT"
        lines.append(
            f"  [{mark}] {case.get('fixture_id')} qubits={case.get('qubits')} "
            f"datasets={case.get('dataset_ids')} drift={case.get('drift_count')}"
        )
        if head:
            lines.append(f"         {head}")
        for item in (case.get("drift") or [])[:10]:
            kind = str(item.get("kind") or "unknown")
            lines.append(
                "           - %s %s：%s" % (
                    kind, item.get("key"), DRIFT_LABELS.get(kind, "")
                )
            )
    for note in report.get("notes") or []:
        lines.append(f"  注: {note}")
    lines.append("=" * 68)
    return "\n".join(lines)


__all__ = [
    "DRIFT_LABELS",
    "classify_fixture",
    "format_report",
    "load_fixtures",
    "regression_from_directory",
    "run_regression",
]
