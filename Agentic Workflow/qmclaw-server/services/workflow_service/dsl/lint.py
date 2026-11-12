"""qwf 离线体检器（DSL lint）。

为什么要它：DSL 节点语义散落在 5 个 1000+ 行文件里，写错一条 qwf 只能靠
「起服务 → parse → validate → dry」三连才发现，代价高。lint 把**已知会炸的写法**
在离线状态下直接指出来。

规则分三级：
  error   —— 一定会失败（缺 budget、引用不存在的绑定、read 用错数据集类型……）
  warning —— 能跑，但很可能不是你想要的（写寄存器类实验、decide 没给 choices……）
  info    —— 提醒（实验次数、预计数据集数、预计写的键……）

用法（服务内）：
    from .lint import lint_ir, lint_text
    report = lint_text(text)

用法（命令行，离线、不起服务）：
    python D:/qmclaw/qwf_lint.py xxx.qwf
"""
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# lqms.measure.tuners.sq_nodes 里真实存在的实验函数（2026-11-06 实测签名）。
# 名字不在这里的（如 allxy / single_shot / pulsed_spec / swap / drag_calibrate）
# 在 sq_nodes 里**没有同名函数**，写进 qwf 必然失败。
SQ_NODE_FNS: Set[str] = {
    "sq.iqraw", "sq.piamp", "sq.pidf", "sq.pidf21", "sq.ramsey_df", "sq.ramsey_t2",
    "sq.s21", "sq.s21_dis", "sq.s21_power2d", "sq.s21_zpa2d", "sq.set_pi",
    "sq.spectroscopy", "sq.spectroscopy_auto", "sq.spectroscopy_iq", "sq.spinecho_t2",
    "sq.t1", "sq.test_timing_xyz", "sq.xeb", "sq.pulse_shape", "sq.pulse_shape_ramsey",
    "sq.time_rabi", "sq.impa_gain", "sq.iqcenter",
}

# read（contrast）节点能处理的数据集类型。contrast 的实现
# （dsl/backends/lqcs.py build_contrast_code）只认 IQraw 的数据形状
# （shape[1] >= 5，取 a[:, [1,2]] 与 a[:, [3,4]] 投影到 discriminator 轴）。
# 对 Ramsey / T1 数据集用 read → 运行期报 "unexpected dataset shape"。
READ_KINDS: Set[str] = {"IQraw"}

# 数据形状分类（D5-①「按数据形状自动选节点」）：
# sq.iqraw 产出 IQraw 形状（shape (N,5)），read/contrast 能直接处理；
# 其余实验（Ramsey / T1 / XEB / S21 / 扫频…）产出的是别的形状，
# 只能用 analyze 节点（qter.fitData）拟合，不能喂给 contrast。
IQRAW_FNS: Set[str] = {"sq.iqraw"}
NON_IQRAW_FNS: Set[str] = {f for f in SQ_NODE_FNS if f not in IQRAW_FNS}

# 派生键：厂商按刚采数据重算的量（读出判别中心），不需要票据授权
DERIVED_PREFIXES = ("discriminator.",)

# 厂商坑（roadmap §3 #4）：这几个函数不传 freq 时厂商 API 会把 freq 当成标量
# qobj.regs.f10，扫描序列构造直接 IndexError（实测 sq.spectroscopy）。
FREQ_REQUIRED_FNS: Set[str] = {"sq.spectroscopy"}

# 单轴扫描点数经验上限：再多基本是"默认扫描展开"没写死，容易整段占满通道。
MAX_SWEEP_POINTS = 400

# ── 厂商语义风险（roadmap §3 #4）─────────────────────────────────────────────
# 默认回写的开关参数名（WRITE_EFFECTS 里 default_on 类实验用这个 flag）
UPDATE_FLAG = "update"
# sq.pidf 的 df 低于这个值时盖不住真实频率偏差：实测 35.7 MHz 偏差下默认 0.03 直接扫空，
# 需要 ≥0.05（±50 MHz、21 点、约 8 s）。
MIN_PIDF_DF = 0.05
# 已由专用规则覆盖的必填参数（E_T1_NO_ZPA / E_SPEC_NO_FREQ 的报错更有针对性），
# 通用规则跳过它们，避免同一处报两条 error。
_REQUIREMENT_COVERED: Set[Tuple[str, str]] = {
    ("sq.t1", "zpa"),
    ("sq.spectroscopy", "freq"),
}


def _sweep_points(value: Any) -> int:
    """猜一个扫描轴有多少点（只用于 lint 提示，猜不准就返回 0 = 不判定）。

    - list/tuple → 长度
    - 字符串里能认出 `arange(a, b, step)` / `linspace(a, b, n)` → 算出点数
    """
    if isinstance(value, (list, tuple)):
        return len(value)
    text = str(value or "")
    if not text:
        return 0
    m = re.search(r"linspace\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)", text)
    if m:
        try:
            return int(float(m.group(3)))
        except ValueError:
            return 0
    m = re.search(r"arange\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)(?:\s*,\s*([-\d.eE+]+))?", text)
    if m:
        try:
            start, stop = float(m.group(1)), float(m.group(2))
            step = float(m.group(3)) if m.group(3) else 1.0
            if step == 0:
                return 0
            return int(abs((stop - start) / step)) + 1
        except ValueError:
            return 0
    return 0

# read 节点产出的可引用字段（dsl/backends/lqcs.py annotate）
READ_ATTRS: Set[str] = {
    "contrast", "p1_i", "p1_x", "separation", "sigma", "snr", "shots",
    "passed", "threshold", "operator", "metric", "dataset",
}

# decide 节点产出的可引用字段（dsl/executor.py wrap_result）
DECIDE_ATTRS: Set[str] = {
    "choice", "choices", "symptom", "recommendations", "message",
    "conversation", "metrics", "status",
}

# 干跑 stub 里没有的字段：干跑 decide 只给 choices / choice，
# 引用 symptom 会在 --dry 时报 "undefined ref"。
DRYRUN_UNSAFE_ATTRS: Set[str] = {"symptom"}

# measure 节点产出的可引用字段
MEASURE_ATTRS: Set[str] = {"dataset", "ok", "status", "stdout", "metrics"}

# analyze 节点产出的可引用字段：executor 把 qter.fitData / px.XEB 的返回摊平进
# outcome.metrics 再挂到绑定上（含 Metric Registry 里的指标名）。
# 少登记会导致 `say "T1 = ${a1.T1_us}"` 被误报 E_UNKNOWN_BINDING。
ANALYZE_ATTRS: Set[str] = {
    "metrics", "status", "ok", "dataset", "stdout",
    "T1_us", "T2_us", "T1", "T2", "T2_star", "R_squared", "freq",
    "contrast", "p1_i", "p1_x", "separation", "snr",
    "xeb_p", "gate_fidelity", "error_Pauli_per_cycle", "p",
}

_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")


def _binding_attrs(op: str) -> Set[str]:
    if op == "read":
        return set(READ_ATTRS)
    if op == "decide":
        return set(DECIDE_ATTRS)
    if op == "analyze":
        return set(ANALYZE_ATTRS)
    return set(MEASURE_ATTRS)


class _Report:
    def __init__(self) -> None:
        self.errors: List[Dict[str, Any]] = []
        self.warnings: List[Dict[str, Any]] = []
        self.infos: List[Dict[str, Any]] = []

    def error(self, code: str, msg: str, line: Optional[int] = None, **extra: Any) -> None:
        self.errors.append({"code": code, "message": msg, "line": line, **extra})

    def warn(self, code: str, msg: str, line: Optional[int] = None, **extra: Any) -> None:
        self.warnings.append({"code": code, "message": msg, "line": line, **extra})

    def info(self, code: str, msg: str, line: Optional[int] = None, **extra: Any) -> None:
        self.infos.append({"code": code, "message": msg, "line": line, **extra})


def _line(stmt: Dict[str, Any]) -> Optional[int]:
    src = stmt.get("source") or {}
    return src.get("line") if isinstance(src, dict) else None


def _walk(stmts: List[Dict[str, Any]], in_branch: bool = False):
    """深度遍历 pipeline（含 if/else 与 repeat 体的嵌套）。

    in_branch 标记是否在条件分支内。repeat 体每轮都会执行，所以不额外标分支；
    只有 repeat 本身位于 if/else 里时才继承 True。
    """
    for st in stmts or []:
        if not isinstance(st, dict):
            continue
        yield st, in_branch
        for key in ("then", "else"):
            sub = st.get(key)
            if isinstance(sub, list):
                for item in _walk(sub, True):
                    yield item
        body = st.get("body")
        if isinstance(body, list):
            for item in _walk(body, in_branch):
                yield item


def _refs(text: str) -> List[Tuple[str, str]]:
    """'${r1.contrast}' -> [('r1','contrast')]；'${d1.choice}' 同理。"""
    out = []
    for m in _TEMPLATE_RE.finditer(str(text or "")):
        body = m.group(1).strip()
        if not body:
            continue
        parts = body.split(".")
        out.append((parts[0], parts[1] if len(parts) > 1 else ""))
    return out


def _write_effects(fn: str) -> Optional[Dict[str, Any]]:
    try:
        from .backends.lqcs import WRITE_EFFECTS
    except Exception:
        return None
    return WRITE_EFFECTS.get(str(fn))


def _vendor_requires(fn: str) -> List[str]:
    """函数的必填参数，直接读运行期回放用的 `VENDOR_SEMANTICS`（不另抄一份）。

    `VENDOR_SEMANTICS` 的键是短名（`t1` / `spectroscopy`），而 qwf 里写的是 `sq.t1`。
    """
    try:
        from ...common.replay import VENDOR_SEMANTICS
    except Exception:
        return []
    short = str(fn or "").split(".", 1)[-1]
    spec = VENDOR_SEMANTICS.get(short) if isinstance(VENDOR_SEMANTICS, dict) else None
    if not isinstance(spec, dict):
        return []
    return [str(x) for x in (spec.get("requires") or [])]


def _as_flag(value: Any) -> Optional[bool]:
    """把 args 里的字面量解释成布尔；认不出（表达式/引用）返回 None = 不判定。"""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().strip("\"'").lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return None


def _as_float(value: Any) -> Optional[float]:
    """把 args 里的字面量解释成数字；认不出返回 None = 不判定。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value if value is not None else "").strip().strip("\"'")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _load_profile_qubits() -> Dict[str, Dict[str, Any]]:
    """读设备画像（config/device_profile.json）；拿不到就返回空字典（不报错）。

    画像是「比特 → 通道 / 频率 / 脉冲参数」的唯一真源（services/common/profile.py）。
    qwf 里写死的比特名与画像对不上时，lint 要能指出来。
    """
    try:
        from ...common.profile import load_profile
        profile = load_profile()
    except Exception:
        return {}
    qubits = profile.get("qubits") if isinstance(profile, dict) else None
    return qubits if isinstance(qubits, dict) else {}


def _profile_checks(rep: "_Report", env: Dict[str, Any],
                    pipeline: List[Dict[str, Any]]) -> None:
    """画像一致性检查（阶段 3 第二批②：qwf/DSL 引用画像而不是写死名字）。"""
    qubits = _load_profile_qubits()
    if not qubits:
        rep.info("I_PROFILE_UNAVAILABLE",
                 "设备画像不可用（config/device_profile.json 缺失或读不出来）："
                 "无法核对 qwf 里的比特名是否与设备一致", None)
        return

    env_qubit = str(env.get("qubit") or "")
    if env_qubit and env_qubit not in qubits:
        rep.warn("W_PROFILE_UNKNOWN_QUBIT",
                 f"env.qubit = {env_qubit!r} 不在设备画像里"
                 f"（画像有 {len(qubits)} 个比特）",
                 None,
                 hint="名字写错，或换了芯片/会话：跑 "
                      "D:\\qmclaw\\_gen_device_profile.py --dry-run 重新生成画像",
                 known=sorted(qubits)[:20])
    elif env_qubit:
        entry = qubits.get(env_qubit) or {}
        rep.info("I_PROFILE_QUBIT",
                 f"画像里的 {env_qubit}：f10={entry.get('f10')} f21={entry.get('f21')} "
                 f"fread={entry.get('fread')} xy_dac={entry.get('xy_dac')} "
                 f"z_dac={entry.get('z_dac')} πamp={entry.get('pi_gate_amp')}", None)

    # 节点里写死的比特名（应为 $env.qubit，否则换片就失效）
    for st, _in_branch in _walk(pipeline):
        if st.get("op") not in ("measure", "read"):
            continue
        q = str(st.get("qubit") or "")
        if not q or q.startswith("$"):
            continue
        if env_qubit and q != env_qubit:
            rep.warn("W_PROFILE_HARDCODED_QUBIT",
                     f"节点把比特名写死成 {q!r}（env.qubit = {env_qubit!r}）："
                     "换片/换会话就会打到别的比特",
                     _line(st),
                     hint="写成 $env.qubit，名字由画像/会话提供")


def _profile_foreign_qubit_checks(rep: "_Report", env: Dict[str, Any],
                                  pipeline: List[Dict[str, Any]]) -> None:
    """参数里出现别的比特名（画像里存在的）→ 提醒可能是写错。"""
    qubits = _load_profile_qubits()
    if not qubits:
        return
    env_qubit = str(env.get("qubit") or "")
    token_re = re.compile(r"\bq\d+ld\d+\b")
    for st, _in_branch in _walk(pipeline):
        if st.get("op") not in ("measure", "read"):
            continue
        blob = json.dumps(st.get("args") or st.get("params") or {},
                          ensure_ascii=False, default=str)
        for name in sorted(set(token_re.findall(blob))):
            if name == env_qubit or name not in qubits:
                continue
            rep.warn("W_PROFILE_FOREIGN_QUBIT",
                     f"参数里出现了另一个比特 {name!r}"
                     f"（本流程 env.qubit = {env_qubit!r}，画像里 {name} 也存在）",
                     _line(st),
                     hint="确认是有意操作多比特；否则多半是复制 qwf 时忘了改名字")


def _profile_ref_checks(rep: "_Report", env: Dict[str, Any],
                        pipeline: List[Dict[str, Any]]) -> None:
    """`$profile.<field>` 的字段名与可用性（阶段 13 / roadmap §2.7）。

    画像进运行期以后，流程可以不再写死 f10/fread；代价是**字段名写错只有运行期才知道**。
    这里把画像实际具备的键与引用对照：缺字段 → warning，整份画像不可用 → info。
    """
    from .validate import refs_in  # 局部导入：validate 依赖本模块会成环
    try:
        from ...common.profile import profile_env_fields
        fields = set(profile_env_fields())
    except Exception:  # noqa: BLE001
        fields = set()
    used: List[str] = []
    for st, _in_branch in _walk(pipeline):
        for key, value in st.items():
            if key in ("source", "op"):
                continue
            for name, path in refs_in(value):
                if name != "profile" or not path:
                    continue
                field = path[0]
                if field not in used:
                    used.append(field)
                if fields and field not in fields:
                    rep.warn("W_PROFILE_UNKNOWN_FIELD",
                             f"$profile.{field} 不是画像字段（可用：{', '.join(sorted(fields))}）",
                             _line(st),
                             hint="字段名以 config/device_profile.json 的键为准；"
                                  "拼错的引用在运行期会报 UndefinedRef")
    if not used:
        return
    qubits = _load_profile_qubits()
    env_qubit = str(env.get("qubit") or "")
    if not qubits:
        rep.info("I_PROFILE_REFS",
                 f"流程引用画像字段 {', '.join(used)}，但离线读不到 config/device_profile.json"
                 f"（运行期是否可用取决于服务端画像）", _line(pipeline[0]) if pipeline else None)
    elif env_qubit and env_qubit not in qubits:
        rep.warn("W_PROFILE_UNKNOWN_QUBIT",
                 f"流程引用画像字段 {', '.join(used)}，但画像里没有 {env_qubit!r}"
                 f"（运行期 $profile.* 不可用）", _line(pipeline[0]) if pipeline else None)
    else:
        entry = (qubits.get(env_qubit) or {}) if env_qubit else {}
        missing = [f for f in used if entry and f not in entry]
        if missing:
            rep.warn("W_PROFILE_MISSING_FIELD",
                     f"{env_qubit} 的画像缺 {', '.join(missing)}（$profile.* 取不到值，"
                     f"可跑 GET /qubit/profile?refresh=1 刷新）",
                     _line(pipeline[0]) if pipeline else None)


def lint_ir(ir: Dict[str, Any]) -> Dict[str, Any]:
    """对 IR 做体检，返回 {ok, errors, warnings, infos, summary}。"""
    rep = _Report()
    if not isinstance(ir, dict):
        rep.error("E_IR", "IR 不是对象")
        return _to_dict(rep, {})

    pipeline = ir.get("pipeline") or []
    budget = ir.get("budget") or {}
    env = ir.get("env") or {}

    # ── budget 三件套（真机模式强制；不声明会被服务端拒）──────────────────
    missing = [k for k in ("maxWallSeconds", "maxWrites", "maxDatasets")
               if budget.get(k) is None]
    if not budget:
        rep.error("E_NO_BUDGET",
                  "没有 budget 块：真机模式要求同时声明 max_wall / max_writes / max_datasets")
    elif missing:
        rep.error("E_NO_BUDGET",
                  f"budget 缺少 {', '.join(missing)}（IR 键名）",
                  hint="qwf 里写 max_wall / max_writes / max_datasets")

    if not env.get("qubit"):
        rep.warn("W_NO_QUBIT", "env 里没有 qubit，measure 会用 $env.qubit 解析失败")

    # ── 设备画像一致性（阶段 3 第二批②）────────────────────────────────
    _profile_checks(rep, env, pipeline)
    _profile_foreign_qubit_checks(rep, env, pipeline)
    _profile_ref_checks(rep, env, pipeline)

    # ── 绑定表（先扫一遍，后面校验引用）──────────────────────────────────
    binds: Dict[str, Dict[str, Any]] = {}
    measure_fn: Dict[str, str] = {}
    for st, _in_branch in _walk(pipeline):
        op = st.get("op")
        name = st.get("as")
        if name and op in ("measure", "read", "decide", "analyze"):
            binds[name] = {"op": op, "attrs": _binding_attrs(op), "stmt": st}
            if op == "measure":
                measure_fn[name] = str(st.get("fn") or "")
        if op == "repeat":
            # repeat ... export r1：循环结束后 r1 对外可见
            for nm in st.get("export") or []:
                if nm and nm not in binds:
                    binds[nm] = {
                        "op": "export",
                        "attrs": set(READ_ATTRS) | set(MEASURE_ATTRS),
                        "stmt": st,
                    }

    # ── 逐条检查 ─────────────────────────────────────────────────────────
    experiments = 0
    branch_experiments = 0
    writes: List[str] = []
    decide_choices: Dict[str, List[str]] = {}
    loops: List[Dict[str, Any]] = []

    for st, in_branch in _walk(pipeline):
        op = st.get("op")
        line = _line(st)

        if op == "measure":
            if in_branch:
                branch_experiments += 1
            else:
                experiments += 1
            fn = str(st.get("fn") or "")
            if not fn:
                rep.error("E_NO_FN", "measure 没有 fn", line)
            elif fn not in SQ_NODE_FNS:
                rep.error("E_UNKNOWN_FN",
                          f"{fn} 在 lqms.measure.tuners.sq_nodes 里没有同名函数",
                          line,
                          hint="可用：" + ", ".join(sorted(SQ_NODE_FNS)))
            if st.get("do_plot") is not None:
                rep.error("E_DO_PLOT",
                          "measure 里不要写 do_plot：服务进程内画图会把 quantum 端点卡死"
                          "（matplotlib backend=qtagg），绘图统一交给 analysis 的 /plot",
                          line)
            if not st.get("timeout"):
                rep.warn("W_NO_TIMEOUT", f"{fn} 没有 timeout", line)

            eff = _write_effects(fn)
            if eff is None:
                if fn in SQ_NODE_FNS:
                    rep.warn("W_NO_WRITE_SEMANTICS",
                             f"{fn} 的写入语义未知（不在 WRITE_EFFECTS 表里），"
                             "闸门拦不住它可能改的键",
                             line)
            else:
                mode = eff.get("mode")
                keys = list(eff.get("writes") or [])
                derived = [k for k in keys
                           if any(str(k).startswith(p) for p in DERIVED_PREFIXES)]
                if mode == "none":
                    pass
                elif keys and len(derived) == len(keys):
                    rep.info("I_DERIVED_KEYS",
                             f"{fn} 只改派生键 {keys}（厂商按数据重算，不需要票据）", line)
                else:
                    writes.extend([k for k in keys if k not in derived])
                    rep.warn("W_WRITE_FN",
                             f"{fn} 会改寄存器 {keys}（mode={mode}）",
                             line,
                             keys=keys,
                             hint="需要写清单+票据；update=False 关不掉的（always）尤其危险")
                if fn == "sq.t1":
                    args = st.get("args") or st.get("params") or {}
                    if "zpa" not in args:
                        rep.error("E_T1_NO_ZPA",
                                  "sq.t1 必须显式给 zpa（固定值）：不传会展开成 zpa×时间二维扫描，"
                                  "极慢；1D 结果也没法用 qter.fitData 拟合（KeyError 'z pulse amplitude'）",
                                  line)

                # 厂商坑：freq=None 时厂商 API 退化成标量扫描，直接 IndexError
                # （base_experiment.prepare_dataset → sweeps.prepDataset → Dataset.__init__）。
                if fn in FREQ_REQUIRED_FNS:
                    args = st.get("args") or st.get("params") or {}
                    if "freq" not in args:
                        rep.error("E_SPEC_NO_FREQ",
                                  f"{fn} 必须显式给 freq：不传时厂商会把 freq 当成标量 "
                                  "qobj.regs.f10，扫描序列构造失败（IndexError: list index out of range）",
                                  line,
                                  hint="例：freq=np.arange(-0.1, 0.1, 0.002) + q2ld3.regs.f10")

                # 厂商坑：默认扫描展开。s21_zpa2d 不传 zpa 就是 16 层 × 每个 freq 点。
                # 上限优先取 IR 自己声明的 budget.maxScanPoints（与运行期预算同口径），
                # 没声明就用经验值；这里只是**静态预判**，不是替代运行期闸门。
                try:
                    declared = budget.get("maxScanPoints")
                    sweep_limit = int(float(declared)) if declared else MAX_SWEEP_POINTS
                except (TypeError, ValueError):
                    sweep_limit = MAX_SWEEP_POINTS
                if fn == "sq.s21_zpa2d":
                    args = st.get("args") or st.get("params") or {}
                    if "zpa" not in args:
                        rep.warn("W_ZPA2D_DEFAULT_ZPA",
                                 "sq.s21_zpa2d 没写 zpa：默认 np.arange(-3, 3.1, 0.4) = 16 层，"
                                 "每个 freq 点都要扫一遍（位图 experiment 里最贵的一类）",
                                 line,
                                 hint="要几张图就写几个 zpa，或显式给 zpa=[0]")
                    pts = _sweep_points(args.get("freq")) * _sweep_points(args.get("zpa"))
                    if pts > sweep_limit:
                        rep.warn("W_SWEEP_LARGE",
                                 f"扫描点数 ≈ {pts}（freq × zpa），超过上限 {sweep_limit}",
                                 line)
                else:
                    args = st.get("args") or st.get("params") or {}
                    pts = max((_sweep_points(v) for v in args.values()), default=0)
                    if pts > sweep_limit:
                        rep.warn("W_SWEEP_LARGE",
                                 f"单个扫描轴 ≈ {pts} 点，超过上限 {sweep_limit}"
                                 "（一个 iqraw 就可能占满几分钟的通道）",
                                 line)

            # ── 厂商语义风险（roadmap §3 #4）：都从后端自己的表驱动，不另抄一份 ──
            args = st.get("args") or st.get("params") or {}
            if not isinstance(args, dict):
                args = {}

            # ① 默认回写：default_on 类实验不写 update 就会静默改寄存器，
            #    "只是看一眼"的只读 workflow 也会悄悄改掉比特参数。
            if isinstance(eff, dict) and eff.get("mode") == "default_on":
                flag_key = str(eff.get("flag") or UPDATE_FLAG)
                eff_keys = list(eff.get("writes") or [])
                if flag_key not in args:
                    rep.warn("W_UPDATE_DEFAULT_ON",
                             f"{fn} 不写 {flag_key} 就默认回写 {eff_keys}："
                             "只做观测请显式关掉，否则读一次就改了寄存器",
                             line,
                             keys=eff_keys,
                             hint=f"例：{fn}(<qubit>, {flag_key}=False)")
                elif _as_flag(args.get(flag_key)) is not False:
                    rep.info("I_UPDATE_ON",
                             f"{fn} 显式 {flag_key}={args.get(flag_key)} → 会回写 {eff_keys}",
                             line,
                             keys=eff_keys)

            # ② sq.pidf 的 df 太小 → 扫空。默认 0.03 只覆盖 ±30 MHz，
            #    实测真实偏差 35.7 MHz 时整段扫空，pidf 会报"没找到跃迁"。
            if fn == "sq.pidf":
                if "df" not in args:
                    rep.warn("W_PIDF_DF_SMALL",
                             f"sq.pidf 没写 df：默认 0.03 只覆盖 ±30 MHz，"
                             f"偏差一大就扫空（建议 df ≥ {MIN_PIDF_DF}，即 ±50 MHz）",
                             line,
                             hint=f"例：args {{ df {MIN_PIDF_DF} }}")
                else:
                    df_val = _as_float(args.get("df"))
                    if df_val is not None and 0 < abs(df_val) < MIN_PIDF_DF:
                        rep.warn("W_PIDF_DF_SMALL",
                                 f"sq.pidf 的 df={df_val} 太小（只覆盖 ±{abs(df_val) * 1000:.0f} MHz）："
                                 f"偏差超过这个范围就扫空，建议 |df| ≥ {MIN_PIDF_DF}",
                                 line,
                                 hint=f"例：args {{ df {MIN_PIDF_DF} }}")

            # ③ 通用必填参数：与运行期回放共用 services/common/replay.py 的
            #    VENDOR_SEMANTICS（专用规则已覆盖的不重复报）。
            for req in _vendor_requires(fn):
                if (fn, req) in _REQUIREMENT_COVERED:
                    continue
                if req not in args:
                    rep.error("E_ARG_MISSING",
                              f"{fn} 必须显式给 {req}：缺省时厂商会退化成默认展开，"
                              "通常极慢或直接报错",
                              line)

        elif op == "read":
            kind = str(st.get("kind") or "IQraw")
            if kind not in READ_KINDS:
                rep.error("E_READ_KIND",
                          f"read 的 kind={kind} 不受支持：contrast 只能处理 "
                          f"{'/'.join(sorted(READ_KINDS))} 形状的数据集，"
                          "Ramsey/T1 数据集用 read 会报 'unexpected dataset shape'",
                          line,
                          hint="改用 analyze 节点（qter.fitData）或直接引用 measure 的 dataset")
            if st.get("threshold") is None:
                rep.info("I_READ_THRESHOLD", f"read 没写 threshold，默认 0.2", line)

            # D5-① 按数据形状自动选节点：read 只能吃 IQraw 形状的数据集
            ds_ref = str(st.get("dataset") or "")
            if ds_ref.startswith("$"):
                src = ds_ref[1:].split(".")[0]
                src_fn = measure_fn.get(src, "")
                if src_fn in NON_IQRAW_FNS:
                    rep.error("E_READ_SHAPE",
                              f"read 读的是 {src} 的数据集，来自 {src_fn}（不是 IQraw 形状）"
                              "→ contrast 算不了，请改用 analyze 节点（qter.fitData）",
                              line,
                              hint=f"sq.iqraw 之外的数据集一律走 analyze；{src_fn} 的参数用 "
                                   "analyze 节点的 command（qter.fitData）取")

        elif op == "repeat":
            loops.append({"maxRounds": st.get("maxRounds"),
                          "export": list(st.get("export") or [])})
            sw = st.get("stopWhen") or {}
            metric = str(sw.get("metric") or "")
            if metric.startswith("$"):
                nm = metric[1:].split(".")[0]
                if nm not in binds:
                    rep.error("E_UNKNOWN_BINDING",
                              f"repeat 的 stop_when 引用了不存在的绑定 '{nm}'", line)
            for nm in st.get("export") or []:
                if nm and nm not in binds:
                    rep.error("E_UNKNOWN_BINDING",
                              f"repeat 的 export 声明了 '{nm}'，但循环体里没有产生它的节点", line)

        elif op == "decide":
            choices = list(st.get("choices") or [])
            decide_choices[str(st.get("as"))] = choices
            if not choices:
                rep.warn("W_DECIDE_NO_CHOICES",
                         "decide 没有 choices：模型自由输出，choice 可能为空 → 所有分支都不执行",
                         line)
            mode = str(st.get("mode") or "analysis")
            if mode == "analysis" and st.get("intent"):
                rep.warn("W_INTENT_IGNORED",
                         "mode=analysis 时 intent 不会进模型提示词（只有 mode=intent 才会）",
                         line)
            for item in st.get("from") or []:
                if isinstance(item, str) and not item.startswith("$"):
                    rep.error("E_REF_NO_DOLLAR",
                              f"decide 的 from 元素 '{item}' 必须带 $ 前缀（$r1）",
                              line)
            for item in st.get("from") or []:
                if isinstance(item, str) and item.startswith("$"):
                    nm = item[1:].split(".")[0]
                    if nm not in binds:
                        rep.error("E_UNKNOWN_BINDING",
                                  f"decide 的 from 引用了不存在的绑定 '{nm}'", line)

        elif op == "if":
            cond = st.get("cond") or {}
            metric = str(cond.get("metric") or "")
            if metric.startswith("$"):
                nm = metric[1:].split(".")[0]
                attr = metric[1:].split(".")[1] if "." in metric[1:] else ""
                if nm not in binds:
                    rep.error("E_UNKNOWN_BINDING",
                              f"if 条件引用了不存在的绑定 '{nm}'", line)
                elif attr and attr not in binds[nm]["attrs"]:
                    rep.warn("W_UNKNOWN_ATTR",
                             f"'{metric}' 里的 '{attr}' 不是 {nm} 的已知字段",
                             line,
                             known=sorted(binds[nm]["attrs"]))
                if attr == "choice" and nm in decide_choices:
                    choices = decide_choices[nm]
                    val = cond.get("threshold")
                    if choices and val not in choices:
                        rep.error("E_IF_CHOICE",
                                  f"if 比较 '{metric}' == {val!r}，但 decide 的 choices 里没有这个值"
                                  f"（choices={choices}）→ 分支永远不会命中",
                                  line)

        elif op == "say":
            for nm, attr in _refs(st.get("template") or st.get("message") or ""):
                if nm not in binds:
                    rep.error("E_UNKNOWN_BINDING",
                              f"say 引用了不存在的绑定 '{nm}'", line)
                    continue
                if attr and attr not in binds[nm]["attrs"]:
                    rep.warn("W_UNKNOWN_ATTR",
                             f"say 里的 '{nm}.{attr}' 不是已知字段", line,
                             known=sorted(binds[nm]["attrs"]))
                if nm in decide_choices and attr in DRYRUN_UNSAFE_ATTRS:
                    rep.warn("W_DRYRUN_UNSAFE",
                             f"say 引用了 '{nm}.{attr}'：干跑 stub 没有这个字段，"
                             "--dry 会报 'undefined ref'",
                             line)

    # ── 汇总 ─────────────────────────────────────────────────────────────
    summary = {
        "experiments": experiments,
        "branchExperiments": branch_experiments,
        "writes": sorted(set(writes)),
        "expectedDatasets": experiments,
        "qubit": env.get("qubit"),
        "loops": loops,
    }
    loop_note = ("，含循环 " + ", ".join(
        f"{lp.get('maxRounds')} 轮(导出 {','.join(lp.get('export') or [])})"
        for lp in loops)) if loops else ""
    rep.info("I_SUMMARY",
             f"必经实验 {experiments} 次（分支内另有 {branch_experiments} 次，按分支命中）"
             f"，会写的键 {sorted(set(writes)) or '无'}{loop_note}", None)

    return _to_dict(rep, summary)


def _to_dict(rep: _Report, summary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": not rep.errors,
        "errors": rep.errors,
        "warnings": rep.warnings,
        "infos": rep.infos,
        "summary": summary,
        "counts": {"error": len(rep.errors), "warning": len(rep.warnings),
                   "info": len(rep.infos)},
    }


def lint_text(text: str) -> Dict[str, Any]:
    """文本 DSL → 体检报告（语法错误也算 error）。"""
    try:
        from . import parse_qwf
        ir = parse_qwf(text)
    except Exception as exc:
        return {
            "ok": False,
            "errors": [{"code": "E_SYNTAX", "message": str(exc), "line": None}],
            "warnings": [],
            "infos": [],
            "summary": {},
            "counts": {"error": 1, "warning": 0, "info": 0},
        }
    return lint_ir(ir)
