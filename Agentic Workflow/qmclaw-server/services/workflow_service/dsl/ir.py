"""dsl.ir - IR v1 的结构校验、规范化与哈希。

设计要点：
- 结构校验（本模块）只回答"这份 JSON 的形状对不对"，不碰语义；
  语义校验在 `validate.py`。两者分开，是为了让 `/workflows/validate` 能给出
  分层的错误列表（先结构后语义），而不是一锅粥。
- 一切校验都是**纯函数**：不启动服务、不连硬件、不读磁盘。
- 未知字段 = 错误（S8）。拼错的 `threshhold` 静默忽略，会造成"阈值没生效"的
  静默错误，这比报错难查一个数量级。
"""

import hashlib
import itertools
import json
from typing import Any, Dict, List

from .const import (
    ALL_OPS,
    BUDGET_FIELDS,
    COMMON_FIELDS,
    CONTROL_OPS,
    DEFAULT_BUDGET,
    IR_VERSION,
    OP_ASSERT,
    OP_BODY_KEY,
    OP_FIELDS,
    OP_MEASURE,
    POLICY_FIELDS,
    SAFE_WORKFLOW_ID_RE,
    TOP_FIELDS,
)
from .goal import lower_goal
from .helpers import hint


def diag(code: str, message: str, path: str = "", line=None, col=None,
         level: str = "error") -> Dict[str, Any]:
    """构造一条诊断。level=error 时 validate 失败，warning 只提示。"""
    return {
        "code": code,
        "level": level,
        "message": message,
        "path": path,
        "line": line,
        "col": col,
    }


def canonical(ir: Dict[str, Any]) -> str:
    """规范化序列化：用于 irHash 与"三种前端编译出同一 IR"的逐字节比较。

    `source`（行号/列号）是编辑器元数据，不是语义：它由文本前端写入，
    eDSL 与画布没有，所以比较与哈希前必须剥掉，否则同一个程序会因写法不同而"不同"。
    """
    return json.dumps(_strip_source(ir), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _strip_source(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_source(v) for k, v in value.items() if k != "source"}
    if isinstance(value, list):
        return [_strip_source(v) for v in value]
    return value


def ir_hash(ir: Dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical(ir).encode("utf-8")).hexdigest()


def errors(diags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [d for d in diags if d.get("level") == "error"]


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_structure(ir: Any) -> List[Dict[str, Any]]:
    """结构校验。返回诊断列表（可能有多个错误，不是遇到第一个就停）。"""
    out: List[Dict[str, Any]] = []
    if not isinstance(ir, dict):
        out.append(diag("E_SCHEMA", f"IR 必须是 JSON 对象，实际是 {type(ir).__name__}", "$"))
        return out

    version = ir.get("ir_version")
    if version != IR_VERSION:
        out.append(diag(
            "E_IR_VERSION",
            f"ir_version={version!r} 不受支持，当前只支持 {IR_VERSION}（不做猜测性升级）",
            "ir_version",
        ))

    for key in sorted(set(ir) - TOP_FIELDS):
        out.append(diag("E_UNKNOWN_FIELD",
                        f"顶层未知字段 {key!r}{hint(str(key), TOP_FIELDS)}", key))

    wid = ir.get("id")
    if not isinstance(wid, str) or not wid:
        out.append(diag("E_SCHEMA", "缺少 id（工作流标识）", "id"))
    elif not SAFE_WORKFLOW_ID_RE.match(wid):
        out.append(diag("E_SCHEMA", f"id={wid!r} 需匹配 {SAFE_WORKFLOW_ID_RE.pattern}", "id"))

    pipeline = ir.get("pipeline")
    if not isinstance(pipeline, list) or not pipeline:
        out.append(diag("E_SCHEMA", "pipeline 必须是非空数组", "pipeline"))
        return out

    budget = ir.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            out.append(diag("E_SCHEMA", "budget 必须是对象", "budget"))
        else:
            for key in sorted(set(budget) - BUDGET_FIELDS):
                out.append(diag("E_UNKNOWN_FIELD", f"budget 未知字段 {key!r}", f"budget.{key}"))
            for key, val in budget.items():
                if val is not None and not _is_number(val):
                    out.append(diag("E_SCHEMA", f"budget.{key} 必须是数字", f"budget.{key}"))

    env = ir.get("env")
    if env is not None and not isinstance(env, dict):
        out.append(diag("E_SCHEMA", "env 必须是对象", "env"))

    # ── IR v2 预留字段（结构上先接受，执行层暂不消费）────────────────────────
    for key in ("backend", "device", "requirements"):
        val = ir.get(key)
        if val is not None and not isinstance(val, dict):
            out.append(diag("E_SCHEMA", f"{key} 必须是对象", key))
    backend = ir.get("backend")
    if isinstance(backend, dict) and not backend.get("name"):
        out.append(diag("E_SCHEMA", "backend 缺少 name（如 \"lqcs\"）", "backend.name"))

    meta = ir.get("meta")
    if meta is not None and not isinstance(meta, dict):
        out.append(diag("E_SCHEMA", "meta 必须是对象", "meta"))

    policy = ir.get("policy")
    if policy is not None:
        if not isinstance(policy, dict):
            out.append(diag("E_SCHEMA", "policy 必须是对象", "policy"))
        else:
            for key in sorted(set(policy) - POLICY_FIELDS):
                out.append(diag("E_UNKNOWN_FIELD", f"policy 未知字段 {key!r}", f"policy.{key}"))
            if "readOnly" in policy and not isinstance(policy["readOnly"], bool):
                out.append(diag("E_SCHEMA", "policy.readOnly 必须是布尔", "policy.readOnly"))

    out.extend(_validate_block(pipeline, "pipeline", top=True))
    return out


def _validate_block(stmts: Any, path: str, top: bool = False) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(stmts, list):
        return [diag("E_SCHEMA", f"{path} 必须是数组", path)]
    for idx, st in enumerate(stmts):
        out.extend(_validate_stmt(st, f"{path}[{idx}]"))
    return out


def _validate_stmt(st: Any, path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(st, dict):
        return [diag("E_SCHEMA", f"{path} 必须是对象", path)]

    src = st.get("source") or {}
    line, col = (src.get("line"), src.get("col")) if isinstance(src, dict) else (None, None)

    def add(code, msg, sub=""):
        out.append(diag(code, msg, f"{path}.{sub}" if sub else path, line, col))

    op = st.get("op")
    if not isinstance(op, str):
        add("E_SCHEMA", "缺少 op")
        return out
    if op not in ALL_OPS:
        add("E_UNKNOWN_OP",
            f"未知 op {op!r}{hint(str(op), ALL_OPS)}；v1 支持：{', '.join(sorted(ALL_OPS))}")
        return out

    allowed = COMMON_FIELDS | OP_FIELDS.get(op, frozenset())
    for key in sorted(set(st) - allowed):
        add("E_UNKNOWN_FIELD",
            f"{op} 语句不支持字段 {key!r}{hint(str(key), allowed)}", key)

    if "as" in st and not isinstance(st["as"], str):
        add("E_SCHEMA", "as 必须是字符串", "as")
    if "when" in st and not isinstance(st["when"], (str, bool)):
        add("E_SCHEMA", "when 必须是字符串引用或布尔", "when")
    if "timeout" in st and not _is_number(st["timeout"]):
        add("E_SCHEMA", "timeout 必须是秒数", "timeout")
    if "retries" in st and not isinstance(st["retries"], int):
        add("E_SCHEMA", "retries 必须是整数", "retries")

    on_error = st.get("onError")
    if on_error is not None and on_error not in ("halt", "continue", "retry"):
        add("E_SCHEMA", f"onError={on_error!r} 必须是 halt|continue|retry", "onError")

    if op in CONTROL_OPS:
        body_key = OP_BODY_KEY[op]
        for key in body_key:
            if key not in st:
                add("E_SCHEMA", f"{op} 缺少 {key}")
                continue
            out.extend(_validate_block(st[key], f"{path}.{key}"))
        if op == "repeat":
            mr = st.get("maxRounds")
            if not isinstance(mr, int) or not (1 <= mr <= 20):
                add("E_SCHEMA", f"repeat.maxRounds 必须是 1..20 的整数，实际 {mr!r}", "maxRounds")
        if op == "if":
            cond = st.get("cond")
            if not isinstance(cond, (str, bool, dict)):
                add("E_SCHEMA", "if.cond 必须是 $ref 字符串、布尔或 {operator,value} 对象", "cond")
    return out


def normalize(ir: Dict[str, Any]) -> Dict[str, Any]:
    """补默认值 + 补自动命名，返回新 IR（不修改入参）。"""
    out = json.loads(json.dumps(ir))  # 深拷贝，避免调用方被改
    out.setdefault("ir_version", IR_VERSION)
    out.setdefault("meta", {})
    out.setdefault("env", {})
    out.setdefault("context", {})
    defaults = {"nodeTimeout": 300, "retries": 0, "retryDelay": 2, "onError": "halt"}
    defaults.update(out.get("defaults") or {})
    out["defaults"] = defaults

    budget = dict(DEFAULT_BUDGET)
    budget.update(out.get("budget") or {})
    out["budget"] = budget

    _normalize_block(out.get("pipeline") or [])
    # goal 块在这里 lowering（语法糖 → repeat + if/else + assert）。
    # 放在 assign_names 之前，低级语句才拿到 s1,s2… 的统一编号；
    # goal 非法时 lower_goal 原样返回，由 validate 报 E_GOAL_*。
    out = lower_goal(out)
    assign_names(out)
    return out


def assign_names(ir: Dict[str, Any], prefix: str = "s") -> Dict[str, Any]:
    """给没有 `as` 的语句按**文档顺序**分配 `s1, s2, ...`。

    这一步必须在 validate 之前做：`as` 是作用域绑定的唯一来源，
    而且三个前端（.qwf / eDSL / 画布）都依赖同一套编号规则，
    否则"三种写法编译出同一份 IR"这条验收标准无从比较。
    """
    taken = set()
    for st in iter_all_statements(ir.get("pipeline")):
        name = st.get("as")
        if isinstance(name, str) and name:
            taken.add(name)

    counter = itertools.count(1)

    def walk(stmts: List[Dict[str, Any]]) -> None:
        for st in stmts or []:
            if not isinstance(st, dict):
                continue
            if not st.get("as"):
                while True:
                    candidate = f"{prefix}{next(counter)}"
                    if candidate not in taken:
                        break
                taken.add(candidate)
                st["as"] = candidate
            for key in OP_BODY_KEY.get(st.get("op"), ()):
                walk(st.get(key) or [])

    walk(ir.get("pipeline") or [])
    return ir


def _normalize_block(stmts: List[Dict[str, Any]]) -> None:
    for st in stmts:
        if not isinstance(st, dict):
            continue
        st.setdefault("effect", None)
        if st.get("op") == OP_ASSERT:
            # 默认"不通过就停"：显式写出来，三个前端才能逐字节比对
            st.setdefault("onFail", "halt")
        if st.get("op") == OP_MEASURE and "params" in st:
            # `args` / `params` 是同一件事（历史上有两种写法，eDSL 的 measure()
            # 只给 `params=`）：统一收敛到 `args`。否则同一段程序换个字段名就换了
            # irHash，"三前端一 IR"的验收标准会假失败（实验三对比时发现的）。
            args, params = st.get("args"), st.get("params")
            if isinstance(args, dict) and isinstance(params, dict):
                merged = dict(params)
                merged.update(args)
                st["args"] = merged
            elif args is None:
                st["args"] = params
            st.pop("params", None)
        for key in ("args", "params"):
            if isinstance(st.get(key), (dict, list)):
                st[key] = _norm_numbers(st[key])
        for key in OP_BODY_KEY.get(st.get("op"), ()):
            _normalize_block(st.get(key) or [])


def _norm_numbers(value: Any) -> Any:
    """把 `$arange` 里的整数值浮点数收敛成 int（`4.0` → `4`）。

    `.qwf` 的 `_number()` 本来就把 `4.0` 读成 `4`，而 eDSL 里 `arange(4.0, …)`
    是 float；不收敛的话"三前端产出同一份 IR"就只差这一个字符而失败。
    渲染成代码时两者都等价（`arange(4, 3, -0.002)`）。
    """
    if isinstance(value, dict):
        if set(value) == {"$arange"}:
            nums = value["$arange"]
            return {"$arange": [int(n) if isinstance(n, float) and n.is_integer() else n
                                for n in nums]}
        return {k: _norm_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm_numbers(v) for v in value]
    return value


def iter_all_statements(stmts: Any):
    """深度优先遍历所有语句（含 repeat/if 子块）。"""
    for st in stmts or []:
        if not isinstance(st, dict):
            continue
        yield st
        for key in OP_BODY_KEY.get(st.get("op"), ()):
            yield from iter_all_statements(st.get(key) or [])
