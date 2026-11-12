"""dsl.validate - IR 语义校验（S2/S3/S7/S8 的静态部分）。

不启动服务、不连硬件、不读数据：给定一份 JSON 就能回答"这份流程能不能跑"。
返回 (normalized_ir, diagnostics)，诊断里 level=error 表示不该执行。

与 `ir.validate_structure` 的分工：那个查形状，这个查语义（引用、量纲、预算）。
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import capabilities
from . import goal as goal_mod
from . import ir as ir_mod
from .helpers import hint
from .const import (
    ALL_OPS,
    ANALYZE_CMD_RE,
    DIMENSIONLESS,
    OP_ANALYZE,
    OP_APPLY,
    OP_ASK,
    OP_ASSERT,
    OP_CALL,
    OP_CLASSIFY,
    OP_DECIDE,
    OP_IF,
    OP_LET,
    OP_MEASURE,
    OP_READ,
    OP_REPEAT,
    OP_SAY,
    OP_VLM,
    OPS,
    SAFE_ARGNAME_RE,
    SAFE_FN_RE,
    SAFE_NAME_RE,
    SAFE_QUBIT_RE,
    UNIT_SUFFIXES,
)
from .ir import diag

REF_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z_][A-Za-z0-9_]*)*)$")
INTERP_RE = re.compile(r"\$\{([^}]+)\}")
LEGACY_RE = re.compile(r"\{\{[^{}]+\}\}")

# `$profile.<field>` 的合法字段（阶段 13）。优先问 services/common/profile.py，
# 拿不到（比如离线只拷了 dsl 目录）就退回这份静态副本 —— 校验宁可少报，不可误报。
_PROFILE_FIELDS_FALLBACK = (
    "qubit", "version", "updated_at", "source",
    "f10", "f21", "fread", "fc", "fc_read", "bias_z",
    "pi_gate_amp", "pi_gate_alpha", "read_out_amp", "read_in_power", "timing_xy",
    "xy_dac", "z_dac", "readout_adc", "readin_dac",
)


def _profile_fields() -> List[str]:
    try:
        from .registry import profile_env_fields
        fields = profile_env_fields()
        if fields:
            return list(fields)
    except Exception:  # noqa: BLE001 — 桥接层不可用时不该让校验失败
        pass
    return list(_PROFILE_FIELDS_FALLBACK)


# ── 引用解析 ──────────────────────────────────────────────────────────────────
def parse_ref(text: str) -> Optional[Tuple[str, List[str]]]:
    """`$c1.metrics.contrast` → ("c1", ["metrics", "contrast"])；不是引用则 None。"""
    if not isinstance(text, str):
        return None
    m = REF_RE.match(text.strip())
    if not m:
        return None
    path = [p for p in m.group(2).split(".") if p]
    return m.group(1), path


def refs_in(value: Any) -> List[Tuple[str, List[str]]]:
    """把一个字段值里出现的所有 `$ref` / `${...}` 收集出来。"""
    found: List[Tuple[str, List[str]]] = []
    if isinstance(value, str):
        direct = parse_ref(value)
        if direct is not None:
            found.append(direct)
        for inner in INTERP_RE.findall(value):
            parsed = parse_ref(inner if inner.strip().startswith("$") else "$" + inner.strip())
            if parsed is not None:
                found.append(parsed)
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(refs_in(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(refs_in(v))
    return found


# ── 作用域 ────────────────────────────────────────────────────────────────────
class Binding:
    __slots__ = ("name", "op", "effect", "path", "index")

    def __init__(self, name: str, op: str, effect: str, path: str, index: int):
        self.name = name
        self.op = op
        self.effect = effect
        self.path = path
        self.index = index


class Scope:
    def __init__(self, parent: "Scope" = None, name: str = "pipeline"):
        self.parent = parent
        self.name = name
        self.bindings: Dict[str, Binding] = {}
        # env 是全局的：子作用域必须共享父作用域的声明，否则控制流体内
        # 的 `$env.qubit` 会被误报成"未在 env/context 中声明"。
        self.env_names: Dict[str, Any] = parent.env_names if parent is not None else {}
        self.exported: Dict[str, Binding] = {}

    def lookup(self, name: str) -> Optional[Binding]:
        sc: Optional[Scope] = self
        while sc is not None:
            if name in sc.bindings:
                return sc.bindings[name]
            if name in sc.exported:
                return sc.exported[name]
            if name in sc.env_names:
                return Binding(name, "env", "compute", f"env.{name}", -1)
            sc = sc.parent
        return None

    def declare(self, name: str, op: str, effect: str, path: str, index: int) -> Optional[Binding]:
        if name in self.bindings or name in self.exported:
            return self.bindings.get(name) or self.exported.get(name)
        b = Binding(name, op, effect, path, index)
        self.bindings[name] = b
        return b

    def visible_names(self) -> List[str]:
        """当前作用域可见的所有绑定名（含父链与 env），用于"是不是写错了"的提示。"""
        names: List[str] = []
        sc: Optional[Scope] = self
        while sc is not None:
            names.extend(sc.bindings)
            names.extend(sc.exported)
            names.extend(sc.env_names)
            sc = sc.parent
        return names


# ── 类型 / 量纲 ───────────────────────────────────────────────────────────────
_READ_FIELDS = {"contrast": "number", "snr": "number", "dataset": "dataset",
                "separation": "number", "shots": "number"}
_MEASURE_FIELDS = {"dataset": "dataset", "dataset_id": "dataset", "ok": "bool",
                   "status": "string", "value": "unknown"}
_ANALYZE_FIELDS = {"dataset": "dataset", "T1_us": "number", "R_squared": "number",
                   "metrics": "object", "artifacts": "object"}
_GENERIC = {"dataset": "dataset", "ok": "bool", "status": "string", "metrics": "object",
            "artifacts": "object", "value": "unknown"}

_FIELD_KINDS = {
    OP_READ: _READ_FIELDS,
    OP_MEASURE: _MEASURE_FIELDS,
    OP_ANALYZE: _ANALYZE_FIELDS,
    OP_CLASSIFY: _GENERIC,
}


def field_kind(binding: Binding, path: List[str]) -> str:
    """尽量静态推断 `$ref` 末端的类型。推不出就 unknown（不误报）。"""
    if binding is None:
        return "unknown"
    if binding.op == "env":
        return "unknown"
    table = _FIELD_KINDS.get(binding.op, {})
    if not path:
        return "object"
    head = path[0]
    if head == "metrics" and len(path) > 1:
        return table.get(path[1], "unknown")
    return table.get(head, "unknown")


def unit_of(name: str) -> Optional[str]:
    """指标名的单位后缀 → 量纲。未知返回 None（不做判断，避免误报）。"""
    low = name.lower()
    if low in DIMENSIONLESS:
        return ""
    for suffix, dim in UNIT_SUFFIXES.items():
        if low.endswith(suffix):
            return dim
    return None


# ── 主校验 ────────────────────────────────────────────────────────────────────
def validate(ir: Any, require_writes: bool = True, manifest: Dict[str, Any] = None,
             backend: str = None, vendor_check: bool = False) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """校验并规范化。返回 (normalized, diagnostics)。

    可选的后端相关检查（都默认关闭，保证 v1 行为不变）：

    * `backend="lqcs"` 或显式 `manifest={...}`：按目标后端能力表检查 `requirements.capabilities`，
      缺失报 `E_CAPABILITY_MISSING`（v2 IR 才有该字段，v1 不触发）。
    * `vendor_check=True`：扫描 `measure.fn` / `analyze.command` 里的厂商符号，给 `W_VENDOR_LEAK`
      警告（v1 合法但不可移植；v2 会升级为 error）。
    """
    diags: List[Dict[str, Any]] = list(ir_mod.validate_structure(ir))
    if ir_mod.errors(diags):
        # 形状都不对就不做语义分析，避免级联噪声
        normalized = ir if isinstance(ir, dict) else {}
        return normalized, diags

    normalized = ir_mod.normalize(ir)

    # 目标契约（v1.3 / S9）：goal 块的语义检查。
    # normalize 里已经尝试 lowering（goal 非法时保持原样），所以这里既能看到
    # 原始 goal，也能看到 lowering 后的 pipeline。
    for code, message, path in goal_mod.goal_errors(normalized):
        diags.append(diag(code, message, path))

    # 写清单（v1.3 / §13）：把 IR 展开成"会写哪些键、写几次、幅度有没有上界"。
    # 缺上界的键 ⇒ 不能代理放行，必须人直批。
    # P1 阶段先以 warning 落地（不打断既有 .qwf）；P3 签发票据时再升 error。
    try:
        from . import manifest as manifest_mod
        for code, message, path in manifest_mod.manifest_errors(normalized):
            diags.append(diag(code, message, path, level="warning"))
    except Exception as exc:  # noqa: BLE001 — 清单算不出来不该拦住执行
        diags.append(diag("E_MANIFEST_UNBOUNDED",
                          f"写清单枚举失败：{exc!r}", "pipeline", level="warning"))

    root = Scope(None, "pipeline")
    root.env_names = dict(normalized.get("env") or {})
    root.env_names.update(normalized.get("context") or {})
    root.env_names.setdefault("env", None)  # `$env.qubit` 的 `env` 头
    root.env_names.setdefault("profile", None)  # `$profile.f10` 的 `profile` 头（阶段 13）

    _walk_block(normalized.get("pipeline") or [], root, "pipeline", diags)

    # 预算（S7 / §9.1）：含 write 却没有 maxWrites 直接拒绝
    writes = count_writes(normalized)
    max_writes = (normalized.get("budget") or {}).get("maxWrites")
    # 用 `is None` 而不是真值判断：`max_writes 0` 是"声明了但额度为零"，
    # 该报"额度不足"，不该报"未声明"（这条曾把实验二的一个真实失败原因指错方向）。
    if writes and require_writes and max_writes is None:
        diags.append(diag(
            "E_BUDGET_REQUIRED",
            f"流程含 {writes} 条 write 语句，但 budget.maxWrites 未声明（真机模式强制）",
            "budget.maxWrites",
        ))
    if max_writes is not None and writes > max_writes:
        diags.append(diag(
            "E_BUDGET_REQUIRED",
            f"budget.maxWrites={max_writes} 小于实际 write 语句数 {writes}"
            f"（每条 measure 记 1 次写；额度 0 时不能含 measure 语句）",
            "budget.maxWrites",
        ))

    # 扫描点数预算：给了就必须是正数（None = 未声明，不拦）
    max_points = (normalized.get("budget") or {}).get("maxScanPoints")
    if max_points is not None:
        bad = not isinstance(max_points, (int, float)) or isinstance(max_points, bool)
        if bad or max_points <= 0:
            diags.append(diag(
                "E_SCAN_POINTS",
                f"budget.maxScanPoints 必须是正数（当前 {max_points!r}）；"
                "留空表示不限制",
                "budget.maxScanPoints",
            ))

    # 旧式 {{}} 插值：允许（画布产出），但必须提示
    for st in iter_statements(normalized.get("pipeline") or []):
        if _has_legacy(st):
            diags.append(diag(
                "E_LEGACY_INTERP",
                f"{st.get('op')} 语句仍含 {{{{}}}} 旧式插值，执行时退化为模糊查找",
                st.get("as") or st.get("op"), level="warning",
            ))

    # ── 后端能力协商（v2 预留）──────────────────────────────────────────────
    if manifest is None and backend:
        manifest = capabilities.manifest_for(backend)
    if manifest is not None:
        required = (normalized.get("requirements") or {}).get("capabilities") or []
        missing = capabilities.missing(required, manifest)
        for line in capabilities.explain(missing):
            diags.append(diag("E_CAPABILITY_MISSING", line, "requirements.capabilities"))
    if vendor_check:
        diags.extend(capabilities.vendor_leak_diags(normalized))
    return normalized, diags


def _walk_block(stmts: List[Dict[str, Any]], scope: Scope, path: str, diags: List[Dict]) -> None:
    for idx, st in enumerate(stmts):
        _walk_stmt(st, scope, f"{path}[{idx}]", idx, diags)


def _walk_stmt(st: Dict[str, Any], scope: Scope, path: str, index: int, diags: List[Dict]) -> None:
    op = st.get("op")
    src = st.get("source") or {}
    line, col = (src.get("line"), src.get("col")) if isinstance(src, dict) else (None, None)

    def add(code, msg, sub="", level="error"):
        diags.append(diag(code, msg, f"{path}.{sub}" if sub else path, line, col, level=level))

    # 1) 引用必须先绑定（S2）。控制流子块在自己的作用域里单独校验，这里跳过，
    #    否则体外绑定看不见体内的 `$r5`，会误报 E_UNDEFINED_REF。
    for field, value in st.items():
        if field in ("op", "as", "source", "body", "then", "else", "export", "stopWhen"):
            continue
        for name, refpath in refs_in(value):
            if name == "env":
                if refpath and refpath[0] not in (scope.env_names or {}):
                    add("E_UNDEFINED_REF", f"$env.{'.'.join(refpath)} 未在 env/context 中声明")
                continue
            if name == "profile":
                # 画像字段名是固定的（services/common/profile.py 的 PROFILE_ENV_FIELDS）；
                # 写错字段名在运行期才会炸，这里先给一条 warning。
                if refpath and refpath[0] not in _profile_fields():
                    add("W_PROFILE_UNKNOWN_FIELD",
                        f"$profile.{'.'.join(refpath)} 不是画像字段（可用：{', '.join(_profile_fields())}）",
                        level="warning")
                continue
            b = scope.lookup(name)
            if b is None:
                add("E_UNDEFINED_REF",
                    f"${name} 未在之前的语句中绑定（引用只能向前指，不能指向后续语句）"
                    + hint(str(name), scope.visible_names(), cutoff=0.5))
                continue
            kind = field_kind(b, refpath)
            if field == "value" and kind in ("object",):
                add("E_TYPE_MISMATCH", f"${name}.{'.'.join(refpath)} 不是数值，不能用于比较")

    # 2) 逐 op 校验
    if op == OP_MEASURE:
        fn = st.get("fn")
        if not isinstance(fn, str) or not fn:
            add("E_MISSING_FIELD", "measure 缺少 fn（要跑哪个实验，如 sq.iqraw）", "fn")
        elif not SAFE_FN_RE.match(fn):
            add("E_BAD_FN", f"fn={fn!r} 需匹配 {SAFE_FN_RE.pattern}", "fn")
        q = st.get("qubit")
        if isinstance(q, str) and not parse_ref(q) and q and not SAFE_QUBIT_RE.match(q):
            add("E_BAD_QUBIT", f"qubit={q!r} 只允许 [A-Za-z0-9_%-]", "qubit")
        for key in ("args", "params"):
            for arg in (st.get(key) or {}):
                if not SAFE_ARGNAME_RE.match(str(arg)):
                    add("E_BAD_FN", f"参数名 {arg!r} 非法", key)
    elif op == OP_READ:
        kind = st.get("kind", "IQraw")
        if not isinstance(kind, str) or not SAFE_ARGNAME_RE.match(kind):
            add("E_BAD_FN", f"kind={kind!r} 非法", "kind")
        ds = st.get("dataset")
        if ds is None:
            add("E_MISSING_FIELD",
                "read 缺少 dataset（要读哪个数据集，如 $c1.dataset）", "dataset")
        elif not isinstance(ds, (int, str)):
            add("E_TYPE_MISMATCH", "dataset 必须是整数或 $ref", "dataset")
    elif op == OP_ANALYZE:
        if st.get("dataset") is None:
            add("E_MISSING_FIELD",
                "analyze 缺少 dataset（要拟合哪个数据集，如 $c1.dataset）", "dataset")
        cmd = st.get("command", "")
        if cmd and not ANALYZE_CMD_RE.match(str(cmd)):
            add("E_BAD_COMMAND",
                f"analyze command 只允许以 qter.fitData( 开头，实际 {str(cmd)[:40]!r}", "command")
    elif op == OP_ASSERT:
        if st.get("metric") is None:
            add("E_MISSING_FIELD",
                "assert 缺少 metric（要判哪个量，如 $r1.contrast）", "metric")
        operator = st.get("operator", ">=")
        if operator not in OPS:
            add("E_SCHEMA", f"operator={operator!r} 必须是 {'/'.join(OPS)}", "operator")
        if st.get("threshold") is None and st.get("value") is None:
            add("E_SCHEMA", "assert 缺少 threshold（比较右侧）", "threshold")
        on_fail = st.get("onFail", "halt")
        if on_fail not in ("halt", "continue", "skip"):
            add("E_SCHEMA",
                f"onFail={on_fail!r} 必须是 halt/continue/skip", "onFail")
        _check_compare(st, scope, add, "threshold")
    elif op == OP_SAY:
        if not isinstance(st.get("template", st.get("message", "")), str):
            add("E_TYPE_MISMATCH", "template 必须是字符串", "template")
    elif op == OP_APPLY:
        if not isinstance(st.get("params", {}), dict):
            add("E_TYPE_MISMATCH", "params 必须是对象", "params")
    elif op == OP_CALL:
        add("E_NOT_IMPLEMENTED", "v1 解析 call 但拒绝执行（子工作流在 P4）", level="warning")
    elif op in (OP_DECIDE, OP_ASK, OP_VLM, OP_LET):
        pass

    # 3) 控制流
    if op == OP_REPEAT:
        body_scope = Scope(scope, "repeat")
        body = st.get("body") or []
        _walk_block(body, body_scope, f"{path}.body", diags)
        stop = st.get("stopWhen")
        if stop is not None:
            if not isinstance(stop, dict):
                add("E_SCHEMA", "stopWhen 必须是 {metric, operator, value} 对象", "stopWhen")
            else:
                operator = stop.get("operator", ">=")
                if operator not in OPS:
                    add("E_SCHEMA", f"stopWhen.operator={operator!r} 非法", "stopWhen")
                # stopWhen 在每轮**结束后**求值，所以可以引用体内绑定；
                # 但第 1 步为了不误报跳过了 stopWhen，这里必须补一次存在性检查，
                # 否则 `$r99.contrast` 这种拼错会一路跑到运行时才炸。
                _check_refs_bound(stop, body_scope, add, "stopWhen")
                _check_compare(stop, body_scope, add, "value", path_suffix="stopWhen")
        for name in (st.get("export") or []):
            if not isinstance(name, str):
                continue
            b = body_scope.bindings.get(name)
            if b is None:
                add("E_UNDEFINED_REF", f"export 的 {name!r} 未在 repeat 体内绑定", "export")
            else:
                scope.exported[name] = b
        if not body:
            add("E_UNREACHABLE", "repeat 体为空，循环无意义", "body", level="warning")
    elif op == OP_IF:
        cond = st.get("cond")
        if isinstance(cond, bool):
            if cond is False:
                add("E_UNREACHABLE", "if 条件恒为假，then 分支永不执行", "cond", level="warning")
        elif isinstance(cond, str):
            pass  # 引用检查已在第 1 步
        elif isinstance(cond, dict):
            operator = cond.get("operator", ">=")
            if operator not in OPS:
                add("E_SCHEMA", f"if.cond.operator={operator!r} 非法", "cond")
            _check_compare(cond, scope, add, "value", path_suffix="cond")
        for key in ("then", "else"):
            child = Scope(scope, f"if.{key}")
            _walk_block(st.get(key) or [], child, f"{path}.{key}", diags)

    # 4) 登记绑定
    name = st.get("as")
    if name:
        if not isinstance(name, str) or not SAFE_NAME_RE.match(name):
            add("E_SCHEMA", f"as={name!r} 需匹配 {SAFE_NAME_RE.pattern}", "as")
        else:
            existing = scope.bindings.get(name) or scope.exported.get(name)
            if existing is not None:
                add("E_DUPLICATE_BINDING",
                    f"as={name!r} 在本作用域重复绑定（首次在 {existing.path}；repeat 每轮不要复用同名）")
            else:
                scope.declare(name, op or "?", st.get("effect") or "", path, index)


def _check_refs_bound(value: Any, scope: Scope, add, where: str) -> None:
    """确认字段里出现的所有 `$ref` 都已绑定。

    第 1 步有意跳过了 `stopWhen` / `body` / `then` / `else` / `export`（体内绑定
    在体外看不见），但这些字段本身仍需要一次存在性检查，否则 `$r99` 这类拼错
    会一路跑到运行时才炸，报出来的还是"取不到指标值"这种间接错误。
    """
    for name, _path in refs_in(value):
        if name == "env":
            continue
        if scope.lookup(name) is None:
            add("E_UNDEFINED_REF",
                f"${name} 未在之前的语句中绑定（{where} 只能引用已绑定的或同一个循环体内的）",
                where)


def _check_compare(st: Dict[str, Any], scope: Scope, add, value_key: str = "value",
                   path_suffix: str = "") -> None:
    """比较两侧的量纲（S3）。裸数字 + 带单位指标 → 警告，不阻塞。"""
    metric = st.get("metric")
    value = st.get(value_key)
    if value is None:  # assert/if.cond 用 threshold，repeat.stopWhen 用 value
        value = st.get("value" if value_key != "value" else "threshold")
    if isinstance(metric, str):
        parsed = parse_ref(metric) if metric.startswith("$") else None
        if parsed is None and metric.startswith("$"):
            add("E_UNDEFINED_REF", f"metric={metric!r} 不是合法引用", "metric")
            return
        metric_name = parsed[1][-1] if parsed and parsed[1] else metric
        dim = unit_of(metric_name) if metric_name else None
        # dim == "" 表示已知无量纲（contrast / R_squared…），它同样要拦：
        # 拿无量纲指标去比 `0.2us` 是明显写错，不能因为"没有后缀"就跳过。
        if dim is not None:
            value_dim = None
            if isinstance(value, dict):
                value_dim = value.get("unit")
                value = value.get("value")
            elif isinstance(value, str) and not value.startswith("$"):
                value_dim = value.rstrip("0123456789.")
            if value_dim is not None and value_dim not in (dim, ""):
                add("E_DIMENSION",
                    f"{metric_name} 是 {dim or '无量纲'}，但比较值单位是 {value_dim!r}")
            elif value_dim is None and dim:
                add("E_DIMENSION",
                    f"{metric_name} 带单位（{dim}），与裸数字比较；建议写 $ref 或显式单位",
                    level="warning")


# ── 遍历工具 ──────────────────────────────────────────────────────────────────
def iter_statements(stmts: Iterable[Dict[str, Any]]):
    for st in stmts:
        if not isinstance(st, dict):
            continue
        yield st
        if st.get("op") == OP_REPEAT:
            yield from iter_statements(st.get("body") or [])
        elif st.get("op") == OP_IF:
            yield from iter_statements(st.get("then") or [])
            yield from iter_statements(st.get("else") or [])


def count_writes(ir: Dict[str, Any]) -> int:
    return sum(1 for st in iter_statements(ir.get("pipeline") or []) if st.get("op") == OP_MEASURE)


def _has_legacy(st: Dict[str, Any]) -> bool:
    for k, v in st.items():
        if k in ("op", "as"):
            continue
        if isinstance(v, str) and LEGACY_RE.search(v):
            return True
        if isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, str) and LEGACY_RE.search(vv):
                    return True
    return False


def error_list(diags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [d for d in diags if d.get("level") == "error"]


def format_diags(diags: List[Dict[str, Any]]) -> str:
    lines = []
    for d in diags:
        loc = d.get("path") or "$"
        if d.get("line"):
            loc = f"line {d['line']}:{d.get('col', 0)} ({loc})"
        mark = "ERROR" if d.get("level") == "error" else "WARN "
        lines.append(f"[{mark}] {d['code']} {loc}: {d['message']}")
    return "\n".join(lines)
