"""dsl.textdsl.printer - `.qwf` 文本打印器（`parser` 的逆运算）。

为什么需要它：画布的导入导出要改成 `.qwf` 格式，而 `.qwf` 之前**只有解析器、
没有打印器**——IR 出来之后没人能把它写回文本。

设计约束：
- **往返必须等价**：`parse(print_qwf(ir))` 的结果要和 `ir` 语义相同
  （`ir.canonical` 逐字节相同，或数值上相等）。为此：
  - 字符串一律区分「引用」(`$x.y`) 与「字面量」；字面量默认加引号，
    只有确定能被 lexer 原样读回的裸标识符才不加引号。
  - `budget` / `policy` 的键要反向映射回 `.qwf` 的写法
    （`maxWallSeconds` → `max_wall`，`readOnly` → `read_only`）。
  - 时长在 IR 里是**秒数**，打印时按人读的写法还原（1800 → `30m`）。
- 打印器**不校验**语义（那是 `validate.py` 的活），但会拒绝明显无法表达的 op
  （`call` 目前没有语法），抛 `DSLSyntaxError` 而不是悄悄丢掉。
"""

import re
from typing import Any, Dict, List, Optional

from .lexer import DSLSyntaxError

# ── 键名反向映射（IR 名 → `.qwf` 写法）────────────────────────────────────────
# 只有 `budget` / `policy` 需要：它们的键在 `.qwf` 里是另一套名字，
# 而普通语句块的键原样往返（`_alias` 是单向的，打印 IR 名即可）。
BUDGET_QWF_KEYS = {
    "maxWallSeconds": "max_wall",
    "maxWrites": "max_writes",
    "maxDatasets": "max_datasets",
    "maxLlmCalls": "max_llm",
    "maxScanPoints": "max_scan_points",
}
POLICY_QWF_KEYS = {"readOnly": "read_only"}

# 裸标识符：只有这种形状的字面量才敢不加引号
_BARE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BARE_DOTTED_RE = re.compile(r"^sq\.[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = {"true", "false", "none"}

# 语句里放在 `{ ... }` 块里的公共字段（顺序即打印顺序）
_COMMON_ORDER = ("timeout", "retries", "retryDelay", "onError", "when", "effect",
                 "export", "note")


def _num(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if number == int(number):
        return str(int(number))
    text = repr(number)
    if "e" in text or "E" in text:      # lexer 的 NUMBER 不支持指数写法
        text = f"{number:.12f}".rstrip("0").rstrip(".")
    return text


def _quote(text: str) -> str:
    body = (str(text).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t"))
    return f'"{body}"'


def _bare_ok(text: str) -> bool:
    if text.startswith("$"):
        return False
    if text.lower() in _RESERVED:
        return False
    if _BARE_RE.match(text):
        return True
    # `sq.xxx` 是函数名，lexer 读回后不会被补 `$`
    return bool(_BARE_DOTTED_RE.match(text))


def _value(value: Any) -> str:
    """把 IR 里的一个值写回 `.qwf` 的字面量形式。"""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _num(value)
    if isinstance(value, str):
        return value if (value.startswith("$") or _bare_ok(value)) else _quote(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return _block(value)
    return _quote(str(value))


def _block(mapping: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, value in mapping.items():
        if isinstance(value, dict) and set(value.keys()) == {"$arange"}:
            nums = value["$arange"]
            parts.append(f"{key} range " + " ".join(_num(n) for n in nums))
        else:
            parts.append(f"{key} {_value(value)}")
    return "{ " + "  ".join(parts) + " }"


def _goal_block(goal: Dict[str, Any]) -> str:
    """`goal` 顶层块 → `.qwf` 写法（v1.3 目标契约）。"""
    parts: List[str] = []
    objective = goal.get("objective")
    if objective is not None:
        parts.append(f"objective {_value(objective)}")

    success = goal.get("success")
    if isinstance(success, dict) and success:
        sub: List[str] = []
        metric = success.get("metric")
        if metric is not None:
            sub.append(f"metric {_value(metric)}")
        operator = success.get("operator", success.get("op"))
        if operator is not None:
            # 比较符号是 lexer 的 OP token，不能加引号
            sub.append(f"operator {operator}")
        value = success.get("value", success.get("threshold"))
        if value is not None:
            sub.append(f"value {_value(value)}")
        tolerance = success.get("tolerance")
        if tolerance is not None:
            sub.append(f"tolerance {_value(tolerance)}")
        criteria = success.get("criteria")
        if criteria is not None:
            sub.append(f"criteria {_value(criteria)}")
        level = success.get("level")
        if level is not None:
            sub.append(f"level {_value(level)}")
        parts.append("success { " + "  ".join(sub) + " }")

    if goal.get("maxRounds") is not None:
        parts.append(f"max_rounds {_value(goal['maxRounds'])}")
    if goal.get("onAchieved") is not None:
        parts.append(f"on_achieved {_value(goal['onAchieved'])}")
    return "{ " + "  ".join(parts) + " }"


def _duration(seconds: Any) -> str:
    """秒 → 人读的时长（1800 → `30m`）。IR 里时长一律是秒。"""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return _value(seconds)
    value = float(seconds)
    if value == int(value):
        value = int(value)
        if value % 3600 == 0:
            return f"{value // 3600}h"
        if value % 60 == 0:
            return f"{value // 60}m"
        return f"{value}s"
    return f"{seconds}s"


def _stmt_block(stmt: Dict[str, Any], fields: List[str],
                extra: Optional[List[tuple]] = None) -> str:
    """语句块：`fields` 是普通字段（按给定顺序），`extra` 是 (键, 已格式化文本)。"""
    parts: List[str] = []
    for key in fields:
        if key in stmt and stmt[key] is not None:
            parts.append(f"{key} {_value(stmt[key])}")
    for key, text in (extra or []):
        parts.append(f"{key} {text}")
    return "{ " + "  ".join(parts) + " }"


def _common_fields(stmt: Dict[str, Any], skip=()) -> List[str]:
    return [k for k in _COMMON_ORDER if k not in skip and stmt.get(k) is not None]


def _timeout_extra(stmt: Dict[str, Any]) -> List[tuple]:
    if stmt.get("timeout") is None:
        return []
    return [("timeout", _duration(stmt["timeout"]))]


def _compare(spec: Any) -> str:
    """`{metric, operator, threshold}` 或 `{metric, op, value}` → 比较式文本。"""
    if isinstance(spec, str):
        return spec if spec.startswith("$") else (
            spec if _bare_ok(spec) else _quote(spec))
    if not isinstance(spec, dict):
        return _value(spec)
    metric = spec.get("metric")
    operator = spec.get("operator") or spec.get("op") or ">="
    threshold = spec.get("threshold")
    if threshold is None:
        threshold = spec.get("value")
    left = metric if (isinstance(metric, str) and
                      (metric.startswith("$") or _bare_ok(metric))) else _value(metric)
    right = _value(threshold)
    return f"{left} {operator} {right}"


def _stmt_lines(stmt: Dict[str, Any], indent: str) -> List[str]:
    """一条 IR 语句 → 若干行 `.qwf` 文本。"""
    op = stmt.get("op")
    alias = stmt.get("as")
    out: List[str] = []

    if op == "let":
        out.append(f"{indent}let {stmt.get('as')} = {_value(stmt.get('value'))}")
        return out

    if op == "say":
        template = stmt.get("template", stmt.get("message", ""))
        line = f"{indent}say {_value(template)}"
        if stmt.get("channel"):
            line += f" channel {stmt['channel']}"
        if alias:
            line += f" as {alias}"
        out.append(line)
        return out

    if op == "apply":
        line = f"{indent}apply {_block(stmt.get('params') or {})}"
        if alias:
            line += f" as {alias}"
        out.append(line)
        return out

    if op == "assert":
        line = f"{indent}assert {_compare(stmt)}"
        if stmt.get("onFail"):
            line += f" else {stmt['onFail']}"
        if alias:
            line += f" as {alias}"
        out.append(line)
        return out

    if op == "repeat":
        line = f"{indent}repeat max {int(stmt.get('maxRounds') or 1)}"
        if stmt.get("stopWhen"):
            line += f" stop_when {_compare(stmt['stopWhen'])}"
        if stmt.get("export"):
            names = stmt["export"]
            if isinstance(names, str):
                names = [names]
            line += " export " + ", ".join(str(n) for n in names)
        if alias:
            line += f" as {alias}"
        out.append(line + " {")
        for child in stmt.get("body") or []:
            out.extend(_stmt_lines(child, indent + "  "))
        out.append(indent + "}")
        return out

    if op == "if":
        out.append(f"{indent}if {_compare(stmt.get('cond'))} {{")
        for child in stmt.get("then") or []:
            out.extend(_stmt_lines(child, indent + "  "))
        if stmt.get("else"):
            out.append(indent + "} else {")
            for child in stmt.get("else") or []:
                out.extend(_stmt_lines(child, indent + "  "))
        out.append(indent + "}")
        return out

    if op == "measure":
        fields = ["fn", "qubit", "args", "params"]
        fields += _common_fields(stmt, skip=("timeout", "note"))
        fields.append("note")
        line = f"{indent}measure"
        if alias:
            line += f" as {alias}"
        out.append(line + " " + _stmt_block(stmt, fields, _timeout_extra(stmt)))
        return out

    if op in ("read", "analyze"):
        dataset = stmt.get("dataset")
        if isinstance(dataset, str) and dataset.startswith("$"):
            dataset_text = dataset
        elif isinstance(dataset, (int, float)) and not isinstance(dataset, bool):
            dataset_text = _num(dataset)
        else:
            dataset_text = _value(dataset)
        line = f"{indent}{op} {dataset_text}"
        if alias:
            line += f" as {alias}"
        fields = (["kind", "threshold", "qubit"] if op == "read" else ["command"])
        fields += _common_fields(stmt, skip=("timeout", "note"))
        fields.append("note")
        out.append(line + " " + _stmt_block(stmt, fields, _timeout_extra(stmt)))
        return out

    if op in ("decide", "ask"):
        if op == "decide":
            value = stmt.get("intent")
            extra = ["mode", "systemPrompt", "temperature", "maxTokens",
                     "choices"]
        else:
            # ask 的主值是 question（目标歧义澄清要问什么）；兼容老的 prompt/messages。
            value = stmt.get("question")
            if value is None:
                value = stmt.get("prompt")
            if value is None:
                value = stmt.get("messages")
            extra = ["options", "blocking", "model",
                     "temperature", "maxTokens", "messages"]
        line = f"{indent}{op} {_value(value)}"
        if alias:
            line += f" as {alias}"
        fields = list(extra)
        fields += _common_fields(stmt, skip=("timeout", "note"))
        fields.append("note")
        out.append(line + " " + _stmt_block(stmt, fields, _timeout_extra(stmt)))
        return out

    if op in ("classify", "vlm"):
        image = stmt.get("image")
        if isinstance(image, str) and image.startswith("$"):
            image_text = image
        elif isinstance(image, (int, float)) and not isinstance(image, bool):
            image_text = _num(image)
        else:
            image_text = _value(image)
        line = f"{indent}{op} {image_text}"
        if alias:
            line += f" as {alias}"
        fields = ["labels", "threshold", "family"]
        out.append(line + " " + _stmt_block(stmt, fields))
        return out

    if op == "call":
        raise DSLSyntaxError(
            "op `call` 目前没有 `.qwf` 语法，无法写回文本；"
            "请先把该调用展开成具体语句")

    raise DSLSyntaxError(f"未知 op {op!r}，无法写回 `.qwf`")


def ir_to_qwf(ir: Dict[str, Any]) -> str:
    """IR v1 → `.qwf` 文本。"""
    if not isinstance(ir, dict):
        raise DSLSyntaxError("IR 必须是字典")
    workflow_id = str(ir.get("id") or "workflow")
    lines: List[str] = [f"workflow {workflow_id} {{"]

    meta = ir.get("meta")
    if isinstance(meta, dict) and meta:
        lines.append("  meta " + _block(meta))

    env = ir.get("env")
    if isinstance(env, dict) and env:
        lines.append("  env " + _block(env))

    budget = ir.get("budget")
    if isinstance(budget, dict) and budget:
        parts = []
        for key, value in budget.items():
            if value is None:
                continue
            qwf_key = BUDGET_QWF_KEYS.get(key, key)
            if key == "maxWallSeconds":
                parts.append(f"{qwf_key} {_duration(value)}")
            else:
                parts.append(f"{qwf_key} {_value(value)}")
        if parts:
            lines.append("  budget { " + "  ".join(parts) + " }")

    policy = ir.get("policy")
    if isinstance(policy, dict) and policy:
        parts = []
        for key, value in policy.items():
            if value is None:
                continue
            qwf_key = POLICY_QWF_KEYS.get(key, key)
            parts.append(f"{qwf_key} {_value(value)}")
        if parts:
            lines.append("  policy { " + "  ".join(parts) + " }")

    goal = ir.get("goal")
    if isinstance(goal, dict) and goal:
        lines.append("  goal " + _goal_block(goal))

    bounds = ir.get("bounds")
    if isinstance(bounds, dict) and bounds:
        parts = []
        for key, value in bounds.items():
            if value is None:
                continue
            parts.append(f"{key} {_value(value)}")
        if parts:
            lines.append("  bounds { " + "  ".join(parts) + " }")

    for key in ("defaults", "context"):
        value = ir.get(key)
        if isinstance(value, dict) and value:
            lines.append(f"  {key} " + _block(value))

    for stmt in ir.get("pipeline") or []:
        lines.extend(_stmt_lines(stmt, "  "))

    lines.append("}")
    return "\n".join(lines) + "\n"


def print_qwf(ir: Dict[str, Any]) -> str:
    """`ir_to_qwf` 的别名（与 `parse` 对称）。"""
    return ir_to_qwf(ir)


def stmt_to_qwf(stmt: Dict[str, Any]) -> str:
    """单条语句 → `.qwf` 片段（画布上「这一行长什么样」用）。"""
    return "\n".join(_stmt_lines(stmt, ""))


__all__ = ["ir_to_qwf", "print_qwf", "stmt_to_qwf"]
