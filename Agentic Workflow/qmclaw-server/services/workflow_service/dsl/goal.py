"""goal 块（IR v1.3）：把"这条流水线要达成什么"写进 IR 本身。

设计依据见 `docs/DSL_DESIGN.md` §13.2：

- `goal` 是**语法糖**：lowering 成已有的 `repeat + if/else + assert`，
  执行器（`executor.py`）零改动就能跑一条**有界**的自主循环；
- lowering 发生在 `ir.normalize()` 里 —— 三个前端（.qwf / eDSL / 画布）
  共用同一条路径，"三种写法编译出同一份 IR"这条验收标准才成立；
- goal 非法时**不 lowering**（原 pipeline 原样保留），由 `validate.py`
  报 `E_GOAL_*`，避免拿半合法的 IR 去碰真机。

本模块**不 import ir.py**（ir.py 要 import 本模块做 lowering，会成环），
所以诊断以 `(code, message, path)` 三元组返回，由 validate.py 转成 `ir.diag`。

IR 形态（顶层字段，已进 `TOP_FIELDS`）::

    "goal": {
      "objective": "复现 q4ld3 的 iqraw 对比度并判断是否漂移",   # 人读
      "success": {"metric": "$c1.contrast", "operator": ">=", "value": 0.6},
      "maxRounds": 3,                                            # 有界自主循环
      "onAchieved": "continue"                                   # 默认不因未达成而失败
    }
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from .const import GOAL_FIELDS, GOAL_SUCCESS_FIELDS, OPS

# 有界自主循环的硬上限（与 repeat.maxRounds 的 1..20 一致）
GOAL_MAX_ROUNDS_LIMIT = 20


# ── 小工具 ────────────────────────────────────────────────────────────────────
def _metric_root(metric: Any) -> str:
    """`$r1.contrast` → `r1`（引用根，也就是绑定名）。"""
    text = str(metric or "").strip()
    if text.startswith("$"):
        text = text[1:]
    return text.split(".", 1)[0].strip()


def _tmpl(metric: Any) -> str:
    """`$r1.contrast` → `${r1.contrast}`（say 模板用的插值写法）。"""
    return "${" + str(metric or "").lstrip("$") + "}"


def collect_bindings(stmts: Iterable[Dict[str, Any]]) -> List[str]:
    """收集 pipeline 里所有 `as` 绑定名（含 repeat/if 内部）。"""
    out: List[str] = []

    def walk(block: Iterable[Dict[str, Any]]) -> None:
        for st in block or []:
            if not isinstance(st, dict):
                continue
            name = st.get("as")
            if isinstance(name, str) and name:
                out.append(name)
            for key in ("body", "then", "else"):
                if isinstance(st.get(key), list):
                    walk(st[key])

    walk(stmts)
    return out


def _measure_writes(st: Dict[str, Any]) -> bool:
    """这条 measure 会不会改参数（结构性猜测）。

    真判据在后端适配器的 `WRITE_EFFECTS` 表里（`always` 档连 `update=False`
    都关不掉）；本模块拿不到适配器，只在**没有**显式 `update=False` 时按
    "会写"处理——宁可高估额度占用，也不要低估。
    """
    args = st.get("args")
    if not isinstance(args, dict):
        args = st.get("params") if isinstance(st.get("params"), dict) else {}
    if isinstance(args, dict) and args.get("update") is False:
        return False
    return True


def count_rounds_steps(stmts: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
    """返回 (measure 条数, 可能写参数的 measure 条数)——按"一轮"计。"""
    measures = writes = 0

    def walk(block: Iterable[Dict[str, Any]]) -> None:
        nonlocal measures, writes
        for st in block or []:
            if not isinstance(st, dict):
                continue
            if st.get("op") == "measure":
                measures += 1
                if _measure_writes(st):
                    writes += 1
            for key in ("body", "then", "else"):
                if isinstance(st.get(key), list):
                    walk(st[key])

    walk(stmts)
    return measures, writes


def has_repeat(stmts: Iterable[Dict[str, Any]]) -> bool:
    """pipeline 里有没有 repeat（用来判断 IR 是否已经 lowering 过）。"""
    for st in stmts or []:
        if not isinstance(st, dict):
            continue
        if st.get("op") == "repeat":
            return True
        for key in ("body", "then", "else"):
            if isinstance(st.get(key), list) and has_repeat(st[key]):
                return True
    return False


# ── 判据引用（Metric Registry）────────────────────────────────────────────────
def _resolve_criteria(success: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Tuple[str, str, str]]]:
    """解析 `goal.success` 里的判据引用，返回 `(info, errors)`。

    支持两种写法：
      · `criteria` + `level`（不给 value）→ 阈值从 `config/lab_criteria.json` 取
      · `criteria` + `level` + `value`     → 显式 value 优先，但仍然记下 registry 引用供核对

    info 字段：criteria / level / threshold_key / registry_value / ref /
               value / value_source / explicit_value
    """
    from .registry import resolve_threshold  # 绝对导入层，兼容顶层 `import dsl`

    errors: List[Tuple[str, str, str]] = []
    criteria_name = success.get("criteria")
    level = success.get("level")
    raw_value = success.get("value", success.get("threshold"))
    has_value = isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool)

    if criteria_name is None:
        if level is not None:
            errors.append((
                "E_GOAL_CRITERIA",
                "goal.success.level 需要配合 goal.success.criteria 使用",
                "goal.success.level",
            ))
        return (
            {
                "criteria": None, "level": None, "threshold_key": None,
                "registry_value": None, "ref": None,
                "value": raw_value if has_value else None,
                "value_source": "explicit" if has_value else "missing",
                "explicit_value": has_value,
            },
            errors,
        )

    criteria_name = str(criteria_name).strip()
    if not criteria_name:
        errors.append((
            "E_GOAL_CRITERIA",
            "goal.success.criteria 必须是非空指标名（如 contrast / dmu_sig / t1 / xeb）",
            "goal.success.criteria",
        ))
        return ({}, errors)

    level_name = str(level).strip() if level is not None else "min"
    resolved = resolve_threshold(criteria_name, level_name)
    if resolved.get("error"):
        errors.append((
            "E_GOAL_CRITERIA",
            str(resolved["error"]),
            "goal.success.criteria",
        ))
        return ({}, errors)

    registry_value = resolved.get("value")
    return (
        {
            "criteria": criteria_name,
            "level": level_name,
            "threshold_key": resolved.get("threshold_key"),
            "registry_value": registry_value,
            "ref": resolved.get("ref"),
            "value": raw_value if has_value else registry_value,
            "value_source": "explicit" if has_value else "registry",
            "explicit_value": has_value,
        },
        errors,
    )


# ── 校验 ──────────────────────────────────────────────────────────────────────
def goal_errors(ir: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """goal 块的语义检查，返回 `(code, message, path)` 三元组列表。

    空列表 = 合法。`validate.py` 把它转成诊断条目。
    """
    errors: List[Tuple[str, str, str]] = []
    if not isinstance(ir, dict):
        return errors
    goal = ir.get("goal")
    if goal is None:
        return errors
    if not isinstance(goal, dict):
        return [("E_GOAL_OBJECTIVE", "goal 必须是对象 {objective, success, maxRounds, onAchieved}", "goal")]

    for key in sorted(goal):
        if key not in GOAL_FIELDS:
            errors.append((
                "E_GOAL_OBJECTIVE",
                f"goal 不支持字段 {key!r}（支持 {sorted(GOAL_FIELDS)}）",
                f"goal.{key}",
            ))

    objective = goal.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        errors.append((
            "E_GOAL_OBJECTIVE",
            "goal.objective 必须是非空字符串（人读的目标描述，也是授权时给人看的）",
            "goal.objective",
        ))

    success = goal.get("success")
    if not isinstance(success, dict):
        errors.append((
            "E_GOAL_METRIC",
            "goal.success 必须是对象 {metric, operator, value}",
            "goal.success",
        ))
        return errors

    for key in sorted(success):
        if key not in GOAL_SUCCESS_FIELDS:
            errors.append((
                "E_GOAL_METRIC",
                f"goal.success 不支持字段 {key!r}（支持 {sorted(GOAL_SUCCESS_FIELDS)}）",
                f"goal.success.{key}",
            ))

    metric = success.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        errors.append((
            "E_GOAL_METRIC",
            "goal.success.metric 必须是非空引用（如 $c1.contrast）",
            "goal.success.metric",
        ))
    else:
        root = _metric_root(metric)
        names = set(collect_bindings(ir.get("pipeline") or []))
        env_names = set((ir.get("env") or {})) | set((ir.get("context") or {}))
        if root and root not in names and root not in env_names and root != "env":
            errors.append((
                "E_GOAL_METRIC",
                f"goal.success.metric 引用的 {root!r} 在 pipeline 里没有对应绑定"
                f"（可用绑定：{sorted(names) or '无'}）",
                "goal.success.metric",
            ))

    operator = success.get("operator", success.get("op"))
    if operator is not None and str(operator) not in OPS:
        errors.append((
            "E_GOAL_METRIC",
            f"goal.success.operator={operator!r} 不是支持的比较运算符 {sorted(OPS)}",
            "goal.success.operator",
        ))

    criteria_info, criteria_errors = _resolve_criteria(success)
    errors.extend(criteria_errors)

    value = success.get("value", success.get("threshold"))
    has_value = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not has_value:
        if criteria_info.get("value_source") == "registry":
            # 阈值改由 Metric Registry 提供，不算错
            pass
        else:
            errors.append((
                "E_GOAL_METRIC",
                f"goal.success.value 必须是数值（当前 {value!r}）；"
                f"或给出 criteria + level 让阈值取自 Metric Registry",
                "goal.success.value",
            ))

    rounds = goal.get("maxRounds", 1)
    if not isinstance(rounds, int) or isinstance(rounds, bool) or not (1 <= rounds <= GOAL_MAX_ROUNDS_LIMIT):
        errors.append((
            "E_GOAL_BUDGET_MISMATCH",
            f"goal.maxRounds 必须是 1..{GOAL_MAX_ROUNDS_LIMIT} 的整数（当前 {rounds!r}）",
            "goal.maxRounds",
        ))
        return errors

    measures, writes = count_rounds_steps(ir.get("pipeline") or [])
    budget = ir.get("budget") or {}
    max_datasets = budget.get("maxDatasets")
    if isinstance(max_datasets, (int, float)) and measures:
        need = rounds * measures
        if need > max_datasets:
            errors.append((
                "E_GOAL_BUDGET_MISMATCH",
                f"goal.maxRounds({rounds}) × 每轮 {measures} 条实验 = {need} 次采集，"
                f"超过 budget.maxDatasets={max_datasets:g}",
                "goal.maxRounds",
            ))
    max_writes = budget.get("maxWrites")
    if isinstance(max_writes, (int, float)) and writes:
        need = rounds * writes
        if need > max_writes:
            errors.append((
                "E_GOAL_BUDGET_MISMATCH",
                f"goal.maxRounds({rounds}) × 每轮 {writes} 条会写参数的实验 = {need} 次写，"
                f"超过 budget.maxWrites={max_writes:g}",
                "goal.maxRounds",
            ))
    return errors


# ── lowering ──────────────────────────────────────────────────────────────────
def lower_goal(ir: Dict[str, Any]) -> Dict[str, Any]:
    """把 `goal` 块 lowering 成 `repeat + if/else + assert`（**语法糖**）。

    幂等：lowering 过的 IR（`meta.goal_lowered=True`）再进一次不会套两层循环。
    goal 非法时**原样返回**（不 lowering），由 validate 报 `E_GOAL_*`。
    """
    if not isinstance(ir, dict):
        return ir
    goal = ir.get("goal")
    if not isinstance(goal, dict) or not goal:
        return ir
    meta = ir.get("meta") or {}
    if meta.get("goal_lowered"):
        return ir
    if goal_errors(ir):
        # 非法 goal 不 lowering：宁可原样留着让 validate 报错，
        # 也不要拿半合法的 IR 去碰真机。
        return ir

    success = goal.get("success") or {}
    metric = str(success.get("metric"))
    operator = str(success.get("operator") or success.get("op") or ">=")
    criteria_info, _ = _resolve_criteria(success)
    value = criteria_info.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = success.get("value", success.get("threshold"))
    rounds = int(goal.get("maxRounds") or 1)
    body = list(ir.get("pipeline") or [])

    root = _metric_root(metric)
    names = set(collect_bindings(body))
    # 尾部 assert 要在循环外读到成功判据，所以把判据引用到的绑定导出出来
    export = [root] if root in names else []

    tmpl = _tmpl(metric)
    lowered = [
        {
            "op": "repeat",
            "as": "goal_loop",
            "maxRounds": rounds,
            "stopWhen": {"metric": metric, "op": operator, "value": value},
            "export": export,
            "body": body,
        },
        {
            "op": "if",
            "as": "goal_branch",
            "cond": {"metric": metric, "operator": operator, "threshold": value},
            "then": [{
                "op": "say", "as": "goal_reached",
                "template": f"GOAL reached: {tmpl} ({operator} {value})",
            }],
            "else": [{
                "op": "say", "as": "goal_missed",
                "template": f"GOAL not reached: {tmpl} (need {operator} {value})",
            }],
        },
        {
            "op": "assert", "as": "goal_assert",
            "metric": metric, "operator": operator, "threshold": value,
            # 未达成**不**让整条流水线失败：目标没达成是结论，不是事故
            "onFail": "continue",
        },
    ]
    if criteria_info.get("ref"):
        lowered[-1]["criteriaRef"] = criteria_info.get("ref")

    out = dict(ir)
    out["pipeline"] = lowered
    out_meta = dict(out.get("meta") or {})
    out_meta["goal_source"] = goal
    out_meta["goal_lowered"] = True
    if criteria_info.get("criteria"):
        out_meta["goal_criteria_ref"] = dict(criteria_info)
    out["meta"] = out_meta
    return out


def goal_rounds(ir: Dict[str, Any]) -> int:
    """这条 IR 的目标循环轮数（未 lowering 时取 `goal.maxRounds`）。"""
    if not isinstance(ir, dict):
        return 1
    goal = ir.get("goal")
    if isinstance(goal, dict):
        try:
            return max(1, int(goal.get("maxRounds") or 1))
        except (TypeError, ValueError):
            return 1
    return 1
