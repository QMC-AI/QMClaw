"""编译期统计：一份 IR 下发前就能算出来的东西。

三件事，全部来自后端适配器的知识表（执行器不做任何厂商假设）：

1. **扫描点数**：`sq.t1` 不给 `delay` 会展开成 16×201 点——一次"测个 T1"能拖死真机；
2. **预计耗时**：`sq.pulse_shape` 名义 26 点，但走 `AutoScanSMT` 自适应，实测 >300 s，
   而脚本里写死的 `timeout 300s` 根本不够——这类矛盾必须在下发前暴露；
3. **写入语义**：有些实验会自动改参数（`always` 档连 `update=False` 都关不掉），
   这决定了"这条脚本会不会偷偷动 registry"。

`plan_ir()` 是纯计算，不连服务、不改 IR；执行器的下发前闸门和 dry-run 报告都消费它。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .const import OP_IF, OP_MEASURE, OP_REPEAT
from .goal import has_repeat


def _static_param(value: Any) -> Any:
    """静态化一个参数值。

    引用（`$ref` / `${...}`）在编译期无法确定，一律当 None：这样估算会走厂商默认
    展开（通常偏大），属于"宁可报大不报小"的保守方向。`$arange` 是字面量，保留。
    """
    if isinstance(value, str):
        return None if value.startswith("$") else value
    if isinstance(value, list):
        return [_static_param(v) for v in value]
    if isinstance(value, dict):
        return {k: _static_param(v) for k, v in value.items()}
    return value


def _unresolved(args: Dict[str, Any]) -> List[str]:
    return [k for k, v in (args or {}).items()
            if isinstance(v, str) and v.startswith("$")]


def _iter_with_rounds(stmts: Any, mult: int = 1):
    """遍历语句并带上"这条语句一轮流水线里最多跑几次"。

    `repeat` 的 body 会被执行 `maxRounds` 次 → 倍数相乘；
    `if` 的 then/else 各算一遍（互斥分支按并集算，保守方向）。

    v1.3 之前这里用 `iter_all_statements`，等价于倍数恒为 1；
    goal 块 lowering 之后，流水线顶多变成 `repeat{body: 原流水线}`，
    所以**必须**按轮数放大，否则 3 轮自主循环在预算里只算 1 次
    ——那正是 2026-11-05 事故里"18 个实验 = 18 份全新额度"的同一个坑。
    """
    for st in stmts or []:
        if not isinstance(st, dict):
            continue
        op = st.get("op")
        if op == OP_REPEAT:
            try:
                rounds = int(st.get("maxRounds") or 1)
            except (TypeError, ValueError):
                rounds = 1
            yield from _iter_with_rounds(st.get("body") or [], mult * max(1, rounds))
            continue
        if op == OP_IF:
            yield from _iter_with_rounds(st.get("then") or [], mult)
            yield from _iter_with_rounds(st.get("else") or [], mult)
            continue
        yield st, mult


def _fallback_rounds(ir: Dict[str, Any]) -> int:
    """IR 里还没 lowering（goal 非法）但顶层有 goal 时的兜底倍数。"""
    goal = ir.get("goal")
    if not isinstance(goal, dict):
        return 1
    try:
        rounds = int(goal.get("maxRounds") or 1)
    except (TypeError, ValueError):
        return 1
    if rounds <= 1:
        return 1
    if has_repeat(ir.get("pipeline") or []):
        return 1  # 已经按 repeat 算过了，别再乘一遍
    return max(1, rounds)


def plan_ir(ir: Dict[str, Any], adapter: Any = None) -> Dict[str, Any]:
    """把一份 IR 折成"下发前的成本估算"。

    返回 `{"steps", "totals", "warnings"}`；`adapter=None` 时只做结构遍历（各步数字为 0）。
    """
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    pipeline = list(ir.get("pipeline") or [])
    fallback = _fallback_rounds(ir)
    for st, rounds in _iter_with_rounds(pipeline, fallback):
        if not isinstance(st, dict) or st.get("op") != OP_MEASURE:
            continue
        raw_args = st.get("args")
        if raw_args is None:
            raw_args = st.get("params") or {}
        params = _static_param(raw_args or {})
        fn = st.get("fn")
        timeout = st.get("timeout")

        pts = adapter.estimate_points(fn, params) if adapter is not None else None
        eff = adapter.write_effects(fn, params) if adapter is not None else None
        rt = adapter.estimate_runtime(fn, params) if adapter is not None else None
        step = {
            "as": st.get("as"),
            "fn": fn,
            "rounds": rounds,
            "points": int((pts or {}).get("points") or 0),
            "pointsNote": (pts or {}).get("note") or "",
            "defaultAxes": (pts or {}).get("defaults") or [],
            "seconds": float((rt or {}).get("seconds") or 0.0),
            "adaptive": bool((rt or {}).get("adaptive")),
            "confidence": (rt or {}).get("confidence") or "",
            "timeout": timeout,
            "writeMode": (eff or {}).get("mode") or "",
            "writes": list((eff or {}).get("writes") or []),
            "writesEffective": bool((eff or {}).get("effective")),
            "writeReason": (eff or {}).get("reason") or "",
            "unresolved": _unresolved(raw_args or {}),
        }
        steps.append(step)

        if timeout and step["seconds"] > float(timeout):
            warnings.append(
                f"{step['as']}({fn}) 预计 {step['seconds']:g}s，超过语句 timeout {timeout}s"
                + ("；该实验走自适应扫描，名义点数只是下界" if step["adaptive"] else "")
            )
        if step["adaptive"] and step["seconds"] > 0:
            warnings.append(
                f"{step['as']}({fn}) 运行时长由自适应迭代决定，估计值只是下界（{step['seconds']:g}s）"
            )

    totals = {
        "points": sum(s["points"] * s["rounds"] for s in steps),
        "seconds": round(sum(s["seconds"] * s["rounds"] for s in steps), 1),
        "writeSteps": sum(s["rounds"] for s in steps if s["writesEffective"]),
        "writes": sorted({w for s in steps if s["writesEffective"] for w in s["writes"]}),
        "unestimated": [s["as"] for s in steps if s["seconds"] <= 0],
        "rounds": max([s["rounds"] for s in steps] or [1]),
    }
    if totals["writeSteps"]:
        warnings.append(
            f"{totals['writeSteps']} 条实验会自动改参数：{', '.join(totals['writes'])}"
            + (f"（含 {totals['rounds']} 轮放大）" if totals["rounds"] > 1 else "")
        )
    return {"steps": steps, "totals": totals, "warnings": warnings}


def format_plan(plan: Dict[str, Any]) -> str:
    """人看的表格（dry-run 与 CLI 共用）。"""
    lines = [
        f"{'#':<3}{'as':<14}{'实验':<22}{'点数':>6}{'预计秒':>8}{'timeout':>9}  写入",
    ]
    for i, s in enumerate(plan.get("steps") or [], 1):
        writes = ",".join(s["writes"]) if s["writesEffective"] else "-"
        if s["writeMode"] == "always" and s["writesEffective"]:
            writes += "(关不掉)"
        tmo = f"{s['timeout']:g}s" if s["timeout"] else "-"
        secs = f"{s['seconds']:g}" + ("≥" if s["adaptive"] else "")
        mult = int(s.get("rounds") or 1)
        if mult > 1:
            secs = f"{secs}×{mult}"
            writes += f" ×{mult}"
        lines.append(
            f"{i:<3}{str(s['as']):<14}{str(s['fn']):<22}{s['points']:>6}{secs:>8}{tmo:>9}  {writes}"
        )
    tot = plan.get("totals") or {}
    lines.append(
        f"合计：{tot.get('points', 0)} 点 / 约 {tot.get('seconds', 0):g}s / "
        f"{tot.get('writeSteps', 0)} 条会写参数"
    )
    for w in plan.get("warnings") or []:
        lines.append(f"  ! {w}")
    return "\n".join(lines)


def plan_checks(plan: Dict[str, Any], ir: Dict[str, Any]) -> Dict[str, List[str]]:
    """把计划折成"能不能下发"的判定：`{"errors": [...], "warnings": [...]}`。

    只有两条是硬错（会在真机上必然出事），其余都是提醒：
    - `policy.readOnly` 为真却有条目会写参数；
    - 计划写入条数超过 `budget.maxWrites`。
    预计耗时超 timeout **不算硬错**（可能故意想让它超时被砍），由执行器按语句处置。
    """
    errors: List[str] = []
    warnings: List[str] = list(plan.get("warnings") or [])
    policy = ir.get("policy") or {}
    if isinstance(policy, dict) and policy.get("readOnly"):
        bad = [s for s in plan.get("steps") or [] if s["writesEffective"]]
        if bad:
            detail = "、".join(f"{s['as']}({s['fn']}→{','.join(s['writes'])})" for s in bad)
            errors.append(
                f"policy.readOnly=true，但以下实验会自动改参数：{detail}"
                f"；把它们改成 update=False（若能关）或移出只读流程"
            )
    budget = ir.get("budget") or {}
    max_writes = budget.get("maxWrites")
    write_steps = int((plan.get("totals") or {}).get("writeSteps") or 0)
    if isinstance(max_writes, (int, float)) and write_steps > max_writes:
        errors.append(
            f"计划有 {write_steps} 条实验会写参数，超过 budget.maxWrites={max_writes:g}"
        )
    return {"errors": errors, "warnings": warnings}
