"""lqcs 适配器：拼 `sq.*` 代码 → 提交 quantum job → 轮询 → 解析标记。

S7（2026-11-04）把原来散在 `server.py` 里的三块 lqcs 专有逻辑搬到这里：

1. **拼代码**：`build_experiment_code()` / `build_contrast_code()`（含数据集探针）；
2. **跑 job**：`LqcsAdapter.submit_and_wait()`（异步提交 + 每秒轮询 + 超时/取消时
   `POST /jobs/{id}/abandon` 真正放弃任务）；
3. **解析标记**：`annotate()`（`QMCLAW_DATASET` / `QMCLAW_CONTRAST` / `_ERR`）。

`server.py` 现在只做"节点包装"：取 config、解析 `{{}}`、校验、调用本适配器、套状态。
行为与搬迁前逐字一致（错误文案、日志格式、超时余量都没动），验收线是
`dsl_selftest.py` 与 `dsl_live_check.py`。

函数名以 `dir(lqms.measure.tuners.sq_nodes)` 为准（20 个对象，2026-11-04 实测），
不是 v1.0 API 手册里的大写版次（`sq.Ramsey_T2` / `sq.CPMG_T2` / `sq.XEB` 在真机上不存在）。
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..const import SAFE_ARGNAME_RE, SAFE_FN_RE, SAFE_QUBIT_RE
from ..pyliteral import py_literal
from .base import BackendAdapter, CancelToken, RawResult, RunRequest

# 真机可调用对象（顺序与 sq_nodes 一致，便于对照）
SQ_NODES = [
    "iqraw", "piamp", "pidf", "pulse_shape", "pulse_shape_ramsey",
    "ramsey_df", "ramsey_t2", "s21", "s21_dis", "s21_power2d", "s21_zpa2d",
    "set_pi", "spectroscopy", "spectroscopy_auto", "spinecho_t2", "t1",
    "test_timing_xyz", "xeb",
]

# IR `experiment` 词表 → lqcs 函数名（缺省同名）
EXPERIMENT_MAP = {name: name for name in SQ_NODES}

# 已知不在 sq_nodes 里、只能在工作流外做的步骤
NOT_IN_SQ_NODES = {
    "base_slope": "第 17 步需 qobj.set_piLen(1000,1000) + bassex.PulseShapeFreq() + ps.base_slope()",
}

# quantum_service 会把每个比特名注入 exec 的全局变量里（`exec_globals[qname] = qobj`），
# 所以 `sq.iqraw(q2ld3)` 里的 `q2ld3` 必须是**裸标识符**，写成字符串会得到
# `AttributeError: 'str' object has no attribute 'qName'`。
SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ── 扫描点数估算 ─────────────────────────────────────────────────────────────
#
# 为什么需要：lqcs 里凡是 `xxx=None` 都有**默认展开量**。最典型的是 `sq.t1`：
#   `delay=None → np.array([0,1,2,4,...,84])*1000`（16 点）
#   `zpa=None   → np.linspace(-0.5, 0.5, 201)`（201 点）→ 合成 3216 点的二维扫描。
# 一次"我只想测个 T1"就会变成几千点，真机跑到 600 s 超时甚至把服务拖进 stuck。
# 所以点数必须在**下发前**估算出来。
#
# 下面这张表全部来自 2026-11-04 对
# `D:/anaconda3/envs/lqcs/Lib/site-packages/lqms/measure/tuners/sq_nodes.py` 的实测：
# 值 = 该参数不给时厂商默认展开出的点数。给标量 = 1 点；给数组/$arange = 数组长度。
DEFAULT_SCAN_AXES: Dict[str, Dict[str, int]] = {
    "sq.t1": {"delay": 16, "zpa": 201},
    "sq.ramsey_df": {"delay": 51},
    "sq.ramsey_t2": {"delay": 100},
    "sq.spinecho_t2": {"delay": 150},
    "sq.s21": {"freq": 20},
    "sq.s21_zpa2d": {"zpa": 16, "freq": 20},
    "sq.s21_power2d": {"power": 13, "freq": 20},
    "sq.s21_dis": {"freq": 40},
    "sq.pidf": {"df": 21},
    "sq.pidf21": {"df": 21},
    "sq.time_rabi": {"length": 250},
    "sq.pulse_shape": {"delay": 26},
    "sq.xeb": {"m": 9, "k": 30},
    "sq.iqraw": {"states": 2},
    "sq.test_timing_xyz": {"delay": 1},
    "sq.spectroscopy": {"freq": 1},
    "sq.spectroscopy_auto": {"freq_scan": 33, "zpa_scan": 40},
}

# 这些函数没有隐式扫描轴（单点或由寄存器决定）
NO_SCAN_AXES = {"sq.piamp", "sq.set_pi", "sq.pulse_shape_ramsey", "sq.iqcenter",
                "sq.spectroscopy_iq", "sq.impa_gain"}


def _arange_points(nums) -> int:
    """`{"$arange": [start, stop, step]}` 会展开多少点（与 np.arange 一致）。"""
    try:
        vals = [float(x) for x in nums]
    except (TypeError, ValueError):
        return 1
    if len(vals) == 2:
        return max(1, int(math.ceil(abs(vals[1] - vals[0]))))
    if len(vals) >= 3:
        step = abs(vals[2])
        if step <= 0:
            return 1
        return max(1, int(math.ceil(abs(vals[1] - vals[0]) / step - 1e-9)))
    return 1


def count_points(value) -> int:
    """一个参数值会展开成多少个采样点。"""
    if value is None:
        return 1
    if isinstance(value, dict):
        if "$arange" in value:
            return _arange_points(value.get("$arange") or [])
        if "range" in value:
            return _arange_points(value.get("range") or [])
        return 1
    if isinstance(value, (list, tuple)):
        return max(1, len(value))
    return 1


def estimate_points(fn: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """估算一次实验的扫描点数。

    返回 `{"points", "axes", "defaults", "fn", "note"}`：
    - `axes` 是每个扫描轴最终展开的点数（显式给值按实际算，否则用厂商默认值）；
    - `points` 是各轴点数的乘积（多轴即二维扫描）；
    - `defaults` 标出哪些轴用了厂商默认值（这类轴最危险）。
    """
    params = params or {}
    name = str(fn or "")
    if "." not in name:
        name = f"sq.{name}"
    defaults = DEFAULT_SCAN_AXES.get(name)
    if defaults is None and name in NO_SCAN_AXES:
        defaults = {}
    if defaults is None:
        # 未知函数：不做拦截式估算，只报出来让上层决定
        return {
            "points": 0,
            "axes": {},
            "defaults": [],
            "fn": name,
            "note": f"未知实验 {name}，无法估算扫描点数（不拦截）",
        }

    axes: Dict[str, int] = {}
    used_defaults: list = []
    for axis, default_n in defaults.items():
        if axis in params and params[axis] is not None:
            axes[axis] = count_points(params[axis])
        else:
            axes[axis] = default_n
            used_defaults.append(axis)

    points = 1
    for n in axes.values():
        points *= max(1, int(n))

    note = ""
    if used_defaults:
        note = ("以下轴用了厂商默认值（可能悄悄展开成几百上千点）："
                + "、".join(used_defaults))
    return {
        "points": points,
        "axes": axes,
        "defaults": used_defaults,
        "fn": name,
        "note": note,
    }


# ── 写入语义：哪些实验会自己改参数 ──────────────────────────────────────────────
#
# lqms 的实验分三档，差别不在文档里、在源码里，所以写死成表：
#
#   always      调用处没把 `update` 传下去（或被调方根本没有 update 参数）
#               → 无论你传 update=False 都会落盘，只能靠"不跑它"来避免
#   default_on  签名 `update=True` 且真的传下去了 → 默认写，传 update=False 可关
#   none        函数体里没有 update_* 调用 → 不写
#
# `evidence` 是复核依据（`sq_nodes.py` 行号），`docs/deploy_diff_tools/lqms_write_audit.py`
# 可以从源码重新生成这份事实表；selftest 里的 G3 组负责盯住"表与源码不再一致"。
WRITE_EFFECTS: Dict[str, Dict[str, Any]] = {
    # ── always：关不掉 ────────────────────────────────────────────────────
    "sq.s21_dis": {
        "mode": "always", "writes": ["fread"],
        "evidence": "sq_nodes.py:399 签名无 update；:422 update_fread(...) 未传 update"
                    "（传 update=False 会被 **refresh_kwargs 吞掉，照样写）",
    },
    "sq.set_pi": {
        "mode": "always", "writes": ["PiGate.amp", "PiGate.alpha"],
        "evidence": "sq_nodes.py:603 签名无 update；:625 update_amp / :637 update_alpha 无条件；"
                    "base_experiment.py:2490-2497 update_amp 写 [(gate,'amp')]，"
                    ":2561-2567 update_alpha 写 [(gate,'alpha')]，"
                    "gate 由 :2383/:2520 {'X':'PiGate','X/2':'PiHalf'} 决定"
                    "（真机 2026-11-04 实测：gate=X 写 PiGate.*，gate=X/2 写 PiHalf.*）",
    },
    "sq.iqcenter": {
        "mode": "always",
        "writes": ["fread", "ReadIn.power", "ReadIn.zpa", "ReadIn.length",
                   "timing.adc_start", "ReadIn.ring_power", "fc_read"],
        "evidence": "sq_nodes.py:836 调 update_optimal_params；base_experiment.py:1773 该函数"
                    "无 update 参数；:1795-1848 按 best_params 写 fread / ReadIn.power / "
                    "ReadIn.zpa / ReadIn.length / timing.adc_start / ReadIn.ring_power / fc_read",
    },
    # ── default_on：默认写，显式 update=False 可关 ─────────────────────────
    "sq.piamp": {"mode": "default_on", "flag": "update",
                 "writes": ["PiGate.amp", "PiHalf.amp"],
                 "evidence": "sq_nodes.py:57 update_amp(..., update=update)，签名默认 True；"
                             "base_experiment.py:581-587 update_para(keys=[('PiGate','amp'),"
                             "('PiHalf','amp')])（真机 2026-11-04 实测两个都写）"},
    "sq.pidf": {"mode": "default_on", "flag": "update", "writes": ["f10", "f21"],
                "evidence": "sq_nodes.py:92 update_f10(..., update=update)，签名默认 True；"
                            "base_experiment.py:611-617 update_para(keys=['f10','f21'])"
                            "（改 f10 会带着 f21 一起改）"},
    "sq.pidf21": {"mode": "default_on", "flag": "update", "writes": ["f21"],
                  "evidence": "sq_nodes.py:132 update_f21(..., update=update)，签名默认 True"},
    "sq.ramsey_df": {"mode": "default_on", "flag": "update", "writes": ["f10", "f21"],
                     "evidence": "sq_nodes.py:167 update_f10(..., update=update)，签名默认 True；"
                                 "base_experiment.py:951-962 keys=['f10','f21']（同样带着 f21）"},
    "sq.s21": {"mode": "default_on", "flag": "update", "writes": ["fread"],
               "evidence": "sq_nodes.py:209 update_fread(data, update=update)，签名默认 True"},
    "sq.s21_power2d": {"mode": "default_on", "flag": "update", "writes": ["ReadIn.power"],
                       "evidence": "sq_nodes.py:278 update_power(..., update=update)，签名默认 True；"
                                   "base_experiment.py:721-731 update_para(keys=[('ReadIn','power')])"},
    "sq.spectroscopy": {"mode": "default_on", "flag": "update", "writes": ["f10", "f21"],
                        "evidence": "sq_nodes.py:312 update_f10(..., update=update)，签名默认 True；"
                                    "base_experiment.py:868-872 update_para(keys=['f10','f21'])"},
    "sq.iqraw": {"mode": "default_on", "flag": "update",
                 "writes": ["discriminator.center0", "discriminator.center1",
                            "discriminator.radius0", "discriminator.method"],
                 "evidence": "sq_nodes.py:382 update_iq_zero(update=update)；:386 if update: "
                             "update_iq(auto=auto)；update_iq(base_experiment.py:1114) 无 update 参数；"
                             ":1137-1146 update_iq_zero 写 center0/radius0/method，"
                             ":1125-1127 update_iq 写 center0/center1"},
    "sq.test_timing_xyz": {"mode": "default_on", "flag": "update",
                           "writes": ["timing.xy", "timing.z"],
                           "evidence": "sq_nodes.py:527-530 update=='xy'|True→update_xy，"
                                       "=='z'→update_z；默认 update='xy'"},
    # ── none：不写 ────────────────────────────────────────────────────────
    "sq.t1": {"mode": "none", "writes": [],
              "evidence": "sq_nodes.py:451 有 update 参数但函数体无 update_* 调用"},
    "sq.ramsey_t2": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:540 同理"},
    "sq.spinecho_t2": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:573 同理"},
    "sq.time_rabi": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:327 无 update 语义"},
    "sq.s21_zpa2d": {"mode": "none", "writes": [],
                     "evidence": "sq_nodes.py:239 update_zeff(...) 被注释掉"},
    "sq.spectroscopy_auto": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:426 无 update_*"},
    "sq.spectroscopy_iq": {"mode": "none", "writes": [], "evidence": "sq_nodes.py 无 update_*"},
    "sq.xeb": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:672 无 update_*"},
    "sq.pulse_shape": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:699 无 update_*"},
    "sq.pulse_shape_ramsey": {"mode": "none", "writes": [],
                              "evidence": "sq_nodes.py:743 无 update_*"},
    "sq.impa_gain": {"mode": "none", "writes": [], "evidence": "sq_nodes.py:756 空函数"},
}


# 真机 stdout 里写参数长这样：
#   __setitem__ amp to 0.27 from 0.0 in path ['', 'LQHL', 'test', '20251103_LD_SQ', 'q2ld3', 'PiGate']
# 参数名只有末一段（'amp'），能定位到具体键的是路径末端的**容器**（'PiGate'）；
# 而直接挂在比特上的参数路径停在比特这一层：
#   __setitem__ f10 to 4.08 from 3.20 in path ['', 'LQHL', 'test', '20251103_LD_SQ', 'q2ld3']
# 容器名是厂商知识（LabRAD 注册表里的子字典），住适配器，不进执行器。
WRITE_CONTAINERS = {"PiGate", "PiHalf", "discriminator", "timing", "ReadIn"}


def write_key(name: str, path: Optional[List[str]] = None) -> str:
    """真机 stdout 的一次写入 → 写入语义表里用的键名。

    `'PiGate'` + `'amp'` → `'PiGate.amp'`；路径停在比特层的 `'f10'` → `'f10'`。
    """
    name = str(name or "")
    if name.startswith("qobj."):
        name = name[5:]
    path = list(path or [])
    if not path:
        return name
    tail = str(path[-1])
    if len(path) == 1:
        return tail          # 老格式：path 本身就是键名
    if tail in WRITE_CONTAINERS:
        return f"{tail}.{name}"
    return name


# 「静默改频率」的看护表：这些键一旦被挪动，后续实验的基准就变了。
# 阈值是绝对值，单位跟参数本身一致（f10/f21/fread 都是 GHz 量级，0.001 ≈ 1 MHz）。
# 2026-11-04 真机实测：`sq.spectroscopy` 把 f10 从 3.2037 挪到 4.0884（Δ0.885），
# 就是这一类：节点 completed、门禁全红，但真正的原因在这一行里。
DRIFT_WATCH = {
    "f10": {"abs": 0.001},
    "f21": {"abs": 0.001},
    "fread": {"abs": 0.001},
    "fc_read": {"abs": 0.001},
}


def drift_threshold(key: str) -> Optional[Dict[str, Any]]:
    """这个键被挪动多少算"危险漂移"。不在看护表里 → None（只记录，不报警）。"""
    spec = DRIFT_WATCH.get(str(key or ""))
    return dict(spec) if spec else None


def write_effects(fn: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """这条实验下发后会不会自己改参数、改哪些。

    `effective=True` 表示**这次真的会写**（已把 `update=False` 这种显式关闭算进去）。
    未知函数返回 `mode="unknown"`、`effective=False`：不拦，但要报出来。
    """
    params = params or {}
    name = str(fn or "")
    if "." not in name:
        name = f"sq.{name}"
    spec = WRITE_EFFECTS.get(name)
    if spec is None:
        return {"fn": name, "mode": "unknown", "writes": [], "effective": False,
                "reason": f"未知实验 {name} 的写入语义（不拦，建议补进 WRITE_EFFECTS）"}
    mode = spec["mode"]
    writes = list(spec.get("writes") or [])
    flag = spec.get("flag")
    reason = spec.get("evidence", "")
    if name == "sq.set_pi":
        # 写哪个门由 gate 决定：X → PiGate，X/2 → PiHalf（base_experiment.py:2383/:2520）
        gate_name = {"X": "PiGate", "X/2": "PiHalf"}.get(str(params.get("gate") or "X"), "PiGate")
        writes = [f"{gate_name}.amp", f"{gate_name}.alpha"]
    if mode == "none" or not writes:
        return {"fn": name, "mode": mode, "writes": [], "effective": False, "reason": reason}
    if mode == "default_on" and flag:
        val = params.get(flag, True)
        if val is False:
            return {"fn": name, "mode": mode, "writes": [], "effective": False,
                    "reason": f"调用方显式 {flag}=False，本次不写"}
    if mode == "always" and params.get(flag or "update") is False:
        reason = f"该函数忽略 {flag or 'update'}=False，仍会写。" + reason
    return {"fn": name, "mode": mode, "writes": writes, "effective": True, "reason": reason}


# ── 运行时长：一次实验大概要跑多久 ──────────────────────────────────────────────
#
# 模型：`seconds ≈ overhead_s + points * sec_per_point`，`adaptive` 表示运行时长由
# 自适应扫描/迭代决定，名义点数只是下限（`AutoScanSMT` 那类），此时用 `floor_s`。
#
# 证据等级分三种，写在 `source` 里，不要混：
#   measured  本机真机实测过（2026-11-04 D1 段）
#   family    同族实验的粗估，误差可达 2–3×
#   adaptive  由自适应迭代决定，只能给下界
RUNTIME_ESTIMATES: Dict[str, Dict[str, Any]] = {
    "sq.set_pi": {"overhead_s": 30, "sec_per_point": 0.0, "source": "measured 30.2s (2026-11-04)"},
    "sq.s21_dis": {"overhead_s": 5, "sec_per_point": 0.7, "source": "measured 33.2s/40pt"},
    "sq.piamp": {"overhead_s": 12, "sec_per_point": 0.0, "source": "measured 12.1s"},
    "sq.pidf": {"overhead_s": 12, "sec_per_point": 0.0, "source": "measured 12.1s/21pt"},
    "sq.s21": {"overhead_s": 6, "sec_per_point": 0.9, "source": "family"},
    "sq.spectroscopy": {"overhead_s": 6, "sec_per_point": 1.0, "source": "family"},
    "sq.s21_zpa2d": {"overhead_s": 8, "sec_per_point": 0.5, "source": "family"},
    "sq.s21_power2d": {"overhead_s": 8, "sec_per_point": 0.6, "source": "family"},
    "sq.iqraw": {"overhead_s": 20, "sec_per_point": 0.0, "source": "family"},
    "sq.pidf21": {"overhead_s": 12, "sec_per_point": 0.0, "source": "family"},
    "sq.ramsey_df": {"overhead_s": 20, "sec_per_point": 0.6, "source": "family"},
    "sq.time_rabi": {"overhead_s": 30, "sec_per_point": 0.3, "source": "family"},
    "sq.t1": {"overhead_s": 10, "sec_per_point": 0.8, "source": "family"},
    "sq.ramsey_t2": {"overhead_s": 20, "sec_per_point": 0.6, "source": "family"},
    "sq.spinecho_t2": {"overhead_s": 20, "sec_per_point": 0.6, "source": "family"},
    "sq.xeb": {"overhead_s": 30, "sec_per_point": 3.0, "source": "family（stats×m×k 决定）"},
    "sq.iqcenter": {"overhead_s": 60, "sec_per_point": 1.0, "source": "family"},
    "sq.test_timing_xyz": {"overhead_s": 30, "sec_per_point": 0.0, "source": "family"},
    "sq.impa_gain": {"overhead_s": 1, "sec_per_point": 0.0, "source": "空函数"},
    "sq.spectroscopy_iq": {"overhead_s": 20, "sec_per_point": 0.0, "source": "family"},
    # 自适应：点数不等于轮数，只能给下界
    "sq.pulse_shape": {"overhead_s": 20, "sec_per_point": 12.0, "adaptive": True, "floor_s": 300,
                       "source": "adaptive AutoScanSMT；2026-11-04 实测 26 点 > 300s"},
    "sq.pulse_shape_ramsey": {"overhead_s": 20, "sec_per_point": 12.0, "adaptive": True,
                              "floor_s": 300, "source": "adaptive AutoScanSMT"},
    "sq.spectroscopy_auto": {"overhead_s": 60, "sec_per_point": 1.0, "adaptive": True,
                             "floor_s": 240, "source": "adaptive（两轴自适应）"},
}


def estimate_runtime(fn: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """估算一次实验要跑多久（秒）。未知函数返回 `seconds=0` 表示"不估计、不拦"。"""
    name = str(fn or "")
    if "." not in name:
        name = f"sq.{name}"
    spec = RUNTIME_ESTIMATES.get(name)
    if spec is None:
        return {"fn": name, "seconds": 0.0, "points": 0, "adaptive": False,
                "confidence": "unknown", "source": f"未知实验 {name}，不估计时长"}
    pts = int(estimate_points(name, params).get("points") or 0)
    seconds = float(spec.get("overhead_s") or 0) + pts * float(spec.get("sec_per_point") or 0)
    floor = float(spec.get("floor_s") or 0)
    if floor and seconds < floor:
        seconds = floor
    return {
        "fn": name,
        "seconds": round(seconds, 1),
        "points": pts,
        "adaptive": bool(spec.get("adaptive")),
        "confidence": "adaptive" if spec.get("adaptive") else spec.get("source", "").split(" ")[0],
        "source": spec.get("source", ""),
    }


# ── 注入到 quantum_service 执行上下文的代码片段 ─────────────────────────────────
#
# 这两个片段都包在函数里再 `del` 掉：quantum_service 的 /execute 会把 exec 后
# 的局部变量原样回传并 json 序列化，顶层赋值 numpy 对象会让整个响应 500。

# 实验前：记录当前 session 的最大数据集编号。
# 实验后：只有编号变大才算本次实验真的写了数据；否则报 ERR，避免拿上一次的旧号
# 去喂下游 contrast/analysis（"看起来成功、实际分析的是旧数据"最难查）。
DATASET_PROBE_PRE = '''
def _qm_ds_max():
    """当前 session 下最大的数据集编号；读不到时返回 -1。"""
    try:
        _dirs, _files = cxn.data_vault.dir()
    except Exception:
        return -1
    _best = 0
    for _entry in _files:
        _s = str(_entry)
        if " - " not in _s:
            continue
        _head = _s.split(" - ")[0]
        if _head.isdigit() and int(_head) > _best:
            _best = int(_head)
    return _best


_qm_ds_before = _qm_ds_max()
'''

DATASET_PROBE_POST = '''
def _qm_dataset_probe(_before):
    """回报本次实验新产生的数据集编号（必须是新号）。"""
    try:
        _after = _qm_ds_max()
    except Exception as exc:
        print("QMCLAW_DATASET_ERR", type(exc).__name__, exc)
        return
    if _after > _before:
        print("QMCLAW_DATASET", _after)
    else:
        print("QMCLAW_DATASET_ERR", "no new dataset", "before=%s" % _before)


_qm_dataset_probe(_qm_ds_before)
del _qm_dataset_probe
del _qm_ds_before
del _qm_ds_max
'''

# 兼容旧名字
DATASET_PROBE_SNIPPET = DATASET_PROBE_POST


# 有些实验函数**不接受** `do_plot`（服务端会统一注入 do_plot=False，见
# `services/workflow_service/server.py` 的 `params.setdefault("do_plot", False)`）。
# 名单来自真机实测，不是猜的——在 :3003 里跑：
#   POST /execute {"code": "import inspect; print(inspect.signature(sq.pulse_shape))"}
# 实测结果（2026-11-04）：
#   pulse_shape (qobjs, step_height=-0.5)                              ← 无 do_plot
#   xeb (qobjs, m=None, k=30, gate='reference', tbuffer=0, stats=300)  ← 无 do_plot
#   spectroscopy_auto (qobj, zpa_scan=None, freq_scan=None, sb_freq=-0.3, **refresh_kwargs)
#       ← 只有 **refresh_kwargs，do_plot 会被吞进 refresh_kwargs 再传给 info.refresh()，
#         属于"能过签名检查但语义错"，同样要剥掉
#   t1 / spectroscopy / s21 / set_pi / piamp ... 都显式有 do_plot：保留
NO_DO_PLOT = frozenset({
    "sq.pulse_shape",
    "sq.pulse_shape_ramsey",
    "sq.spectroscopy_auto",
    "sq.xeb",
})


def _strip_unsupported_do_plot(fn: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """目标函数不认 `do_plot` 时把它去掉，避免 `TypeError: unexpected keyword argument`。"""
    if fn.strip() in NO_DO_PLOT and params and "do_plot" in params:
        out = dict(params)
        out.pop("do_plot", None)
        return out
    return params


def build_experiment_code(fn: str, qubit: str, params: Dict[str, Any],
                          report_dataset: bool = True) -> str:
    """按 `fn + qubit + params` 拼出可执行代码（含数据集探针）。

    校验仍然在调用方（server 的 experiment 分支）做，这里只负责拼接，
    遇到非法参数名直接抛 `ValueError`（与搬迁前的行为一致）。
    """
    arg_strs = []
    params = _strip_unsupported_do_plot(fn, params or {})
    if qubit:
        # 裸标识符 → 命中 quantum_service 注入的全局比特对象；
        # 名字含 %/-(如 DataVault 目录名) 时退回字符串，交给实验函数自己处理。
        arg_strs.append(qubit if SAFE_IDENT_RE.match(qubit) else repr(qubit))
    for k, v in (params or {}).items():
        if not SAFE_ARGNAME_RE.match(str(k)):
            raise ValueError(f"invalid parameter name {k!r}")
        arg_strs.append(f"{k}={py_literal(v)}")
    call_code = f"{fn}({', '.join(arg_strs)})"
    if not report_dataset:
        return call_code
    return DATASET_PROBE_PRE + call_code + "\n" + DATASET_PROBE_SNIPPET


def build_contrast_code(dataset_id: Optional[int], qubit: str, kind: str) -> str:
    """生成"读取数据集并计算读出对比度"的代码片段。

    口径与实验室 `qubit_sensing/tools/calibrate.py` 的 `stage_check` 一致：
    把 shots 投影到 discriminator 的 center0->center1 轴上，阈值取两点中点，
    对比度 = |P(1|prep X) - P(1|prep I)|。
    """
    q = (qubit or "").strip()
    ds_literal = "None" if dataset_id in (None, "") else str(int(dataset_id))
    return f'''
def _qm_contrast():
    _ds = {ds_literal}
    if _ds is None:
        _dirs, _files = cxn.data_vault.dir()
        _best = None
        for _entry in _files:
            _s = str(_entry)
            if " - " not in _s:
                continue
            _head = _s.split(" - ")[0]
            if not _head.isdigit():
                continue
            if {kind!r}.lower() not in _s.lower():
                continue
            _num = int(_head)
            if _best is None or _num > _best:
                _best = _num
        _ds = _best
    if _ds is None:
        print("QMCLAW_CONTRAST_ERR", "no dataset found")
        return
    data.loadDataset(int(_ds))
    a = np.asarray(data.data)
    if a.ndim != 2 or a.shape[1] < 5:
        print("QMCLAW_CONTRAST_ERR", "unexpected dataset shape", a.shape,
              "| contrast 只能处理 IQraw 形状(shape[1]>=5)；"
              "Ramsey/T1/XEB 等请改用 analyze 节点(qter.fitData)")
        return
    cxn.registry.cd(list(data.session) + [{q!r}, "discriminator"])
    c0 = np.asarray(cxn.registry.get("center0"), dtype=float)
    c1 = np.asarray(cxn.registry.get("center1"), dtype=float)
    u = (c1 - c0) / np.linalg.norm(c1 - c0)
    thr = 0.5 * ((c0 + c1) @ u)
    bi = a[:, [1, 2]]
    bx = a[:, [3, 4]]
    p1i = float(np.mean(bi @ u > thr))
    p1x = float(np.mean(bx @ u > thr))
    sep = float(np.linalg.norm(bx.mean(0) - bi.mean(0)))
    sig = float(np.sqrt((bi.std(0) ** 2 + bx.std(0) ** 2) / 2).mean())
    print("QMCLAW_CONTRAST", int(_ds), round(p1i, 4), round(p1x, 4),
          round(abs(p1x - p1i), 4), round(sep, 1), round(sig, 1),
          round(sep / sig if sig else 0.0, 3), int(len(a)))


_qm_contrast()
del _qm_contrast
'''


def annotate(kind: str, config: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """把注入片段打印的标记解析成结构化字段（原 `server._annotate_result`）。

    - `experiment`：从 `QMCLAW_DATASET <n>` 补出 dataset_id
    - `contrast`：从 `QMCLAW_CONTRAST ...` 补出 contrast/SNR/metrics/passed
    """
    if not isinstance(result, dict):
        # 真机偶尔会回一个非 dict 的 result（None、列表、甚至整段文本）。
        # 这里统一包成契约形状，别把类型问题漏给调用方。
        result = {"status": "completed", "stdout": "" if result is None else str(result)}
    stdout = result.get("stdout") or ""
    config = config or {}

    # 数据集探针报错 = 本次实验没写出数据 → 节点判失败。
    # 否则下游 contrast/analysis 会拿"上一次实验的旧数据集"继续跑，
    # 看起来全绿，实际分析的是历史数据。
    # 只认**行首**的标记：stdout 里还回显了整段下发代码（`[Quantum] exec: ...`），
    # 而代码文本本身就含有 print("QMCLAW_DATASET_ERR", ...)，用子串搜索会误命中
    # 把"实验成功"误判成"没写出数据"（已在真机探活中踩到）。
    marker_lines = [
        line.strip() for line in stdout.splitlines() if line.strip().startswith("QMCLAW_")
    ]
    marker_text = "\n".join(marker_lines)

    err_line = next(
        (line for line in marker_lines if line.startswith("QMCLAW_DATASET_ERR")), None
    )
    if kind == "experiment" and err_line is not None:
        result["error"] = (
            "experiment produced no dataset: "
            f"{err_line[len('QMCLAW_DATASET_ERR'):].strip()}"
        )
        result["status"] = "failed"
        return result

    if kind == "experiment":
        m = re.search(r"QMCLAW_DATASET\s+(\d+)", marker_text)
        if m:
            result["dataset_id"] = int(m.group(1))

    elif kind == "contrast":
        m = re.search(
            r"QMCLAW_CONTRAST\s+(\d+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)",
            marker_text,
        )
        if m:
            dataset_id = int(m.group(1))
            metrics = {
                "p1_i": float(m.group(2)),
                "p1_x": float(m.group(3)),
                "contrast": float(m.group(4)),
                "separation": float(m.group(5)),
                "sigma": float(m.group(6)),
                "snr": float(m.group(7)),
                "shots": int(m.group(8)),
            }
            threshold = float(config.get("threshold", 0.4) or 0.4)
            result.update(
                {
                    "dataset_id": dataset_id,
                    "metrics": metrics,
                    "contrast": metrics["contrast"],
                    "passed": metrics["contrast"] >= threshold,
                    "threshold": threshold,
                }
            )
        else:
            err = re.search(r"QMCLAW_CONTRAST_ERR\s+(.+)", marker_text)
            if err:
                result["error"] = err.group(1).strip()
    return result


class LqcsAdapter(BackendAdapter):
    name = "lqcs"
    IMPLEMENTED = True

    def __init__(self, base_url: str = "http://127.0.0.1:3003", timeout: float = 300.0,
                 logger: Callable[[str], None] = None, submit_timeout: float = 30.0,
                 queue_url: str = ""):
        self.base_url = (base_url or "http://127.0.0.1:3003").rstrip("/")
        self.default_timeout = float(timeout or 300.0)
        # 提交（POST /execute）只等这么久：实验本体是异步 job，这里等的是"服务端受理"。
        # 真机日志阻塞那次就是靠它 30 s 快速失败，避免和节点超时混在一起。
        self.submit_timeout = float(submit_timeout or 30.0)
        # 硬件通道仲裁服务（task_queue :3009）：非空时实验经它排队，不直接打 quantum。
        # 空字符串 = 直连 quantum（兼容旧部署 / 队列没起来时的降级）。
        self.queue_url = (queue_url or "").rstrip("/")
        # 当前工作流 id：租约续期靠它（task_queue 用 workflowId 认领租约）
        self.workflow_id = ""
        # 当前 trace id（阶段 3）：随每次提交透传到 task_queue / quantum，
        # 让"谁发起的这次实验"在 Express → workflow → queue → quantum 全链可追。
        self.trace_id = ""
        self._log = logger or (lambda _msg: None)

    def _trace_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.trace_id:
            headers["X-Trace-Id"] = self.trace_id
        return headers

    def _queue_post(self, path: str, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
        body = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.queue_url}{path}",
            data=body,
            headers=self._trace_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    # ── 能力声明 ──────────────────────────────────────────────────────────
    def capabilities(self) -> Dict[str, Any]:
        return {
            "vendor": "lqcs",
            "version": ">=2.0",            # lqms 包版本以真机为准，这里只是口径
            "experiments": list(SQ_NODES),
            "fitters": ["t1", "t2", "t2star", "ramsey", "iq_discriminate",
                        "spectroscopy", "rabi", "xeb"],
            "sweeps": ["abs", "relative", "arange"],
            "params": ["registry"],        # 参数住在 qobj.regs，走 registry 而非显式读写
            "control": ["async_job", "cancel", "retry", "lock"],
            # 明确声明"没有"的东西，避免上层误以为能规划
            "missing": sorted(NOT_IN_SQ_NODES),
        }

    def devices(self) -> list:
        # 真机设备树要连 LabRAD 才能列；由 server 的 /devices 补
        return []

    def estimate_points(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """估算一次实验的扫描点数（写给执行器的预算闸门用）。"""
        return estimate_points(fn, params)

    def write_effects(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """这条实验会不会自己改参数、改哪些（写入语义表见模块级 `WRITE_EFFECTS`）。"""
        return write_effects(fn, params)

    def write_key(self, name: str, path: Optional[List[str]] = None) -> str:
        """真机 stdout 的一次写入 → 写入语义表里的键名（容器名是厂商知识）。"""
        return write_key(name, path)

    def drift_threshold(self, key: str) -> Optional[Dict[str, Any]]:
        """这个键被挪动多少算"危险漂移"（见模块级 `DRIFT_WATCH`）。"""
        return drift_threshold(key)

    def estimate_runtime(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """这条实验预计跑多久（秒），见模块级 `RUNTIME_ESTIMATES`。"""
        return estimate_runtime(fn, params)

    def health(self) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "backend": self.name, "quantum": data}
        except Exception as exc:
            return {"ok": False, "backend": self.name, "error": str(exc)[:200]}

    # ── job 生命周期 ──────────────────────────────────────────────────────
    def abandon(self, job_id: str) -> None:
        """放弃一个 quantum job（取消或超时都用它）。失败不影响主流程。"""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/jobs/{job_id}/abandon",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception as exc:
            self._log(f"[Quantum] failed to abandon job {job_id}: {exc}")

    def submit_and_wait(self, code: str, timeout: float, token: CancelToken = None,
                        node_id: str = "", kind: str = "", config: Dict[str, Any] = None,
                        grace: float = 15.0) -> Dict[str, Any]:
        """以异步 job 方式跑一段实验代码，返回节点结果 dict（行为与搬迁前一致）。

        流程：POST /execute {async:true} → 拿 job_id → 轮询 GET /jobs/{id}
        → 成功/失败/超时分别落到 completed / failed / timeout；
        超时或取消时 POST /jobs/{id}/abandon 真正放弃这次任务。
        `kind` 给了就对结果做标记解析（`annotate`），否则返回原样。
        """
        node_timeout = float(timeout or self.default_timeout)
        label = node_id or "-"
        # 走队列时拿到的是 task_id，直连时是 quantum 的 job_id；下面统一叫 handle
        handle = ""
        mode = "quantum"
        if self.queue_url:
            try:
                submitted = self._queue_post("/submit", {
                    "type": "experiment",
                    "payload": {"code": code, "timeout": node_timeout,
                                "workflowId": self.workflow_id},
                    "timeout": node_timeout,
                    "priority": 5,
                    "metadata": {"workflowId": self.workflow_id, "nodeId": label,
                                 "traceId": self.trace_id},
                }, timeout=self.submit_timeout)
            except Exception as exc:
                # 队列不可用 → 直连 quantum（否则单点故障会让整个 DSL 停摆）
                self._log(f"[Queue] submit failed ({exc}); falling back to direct quantum")
                submitted = {"error": str(exc)}
            if submitted.get("error"):
                self._log(f"[Queue] rejected: {submitted['error']}")
            else:
                handle = submitted.get("task_id") or ""
                if handle:
                    mode = "queue"
                    self._log(f"[Queue] task {handle} queued for node {label} "
                              f"(pos {submitted.get('queue_position')}, timeout {node_timeout:.0f}s)")

        if not handle:
            body = json.dumps({
                "code": code,
                "async": True,
                "timeout": node_timeout,
                "trace_id": self.trace_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/execute",
                data=body,
                headers=self._trace_headers(),
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.submit_timeout) as resp:
                    started = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                return {"error": f"failed to start quantum job: {exc}", "status": "failed"}

            handle = started.get("job_id") or ""
            if not handle:
                # 服务端不支持异步（老版本 quantum_service）：按同步结果处理
                return annotate(kind, config or {}, started) if kind else started
            self._log(f"[Quantum] job {handle} started for node {label} (timeout {node_timeout:.0f}s)")

        job_id = handle
        deadline = time.time() + node_timeout + max(0.0, float(grace))
        while True:
            time.sleep(1.0)
            if token is not None and token.is_set():
                # 取消 → 放弃这个 job，避免真机上任务继续跑
                self._cancel(mode, handle)
                return {"error": "cancelled", "status": "cancelled"}
            try:
                if mode == "queue":
                    job = self._queue_post("/status", {"task_id": handle}, timeout=15)
                else:
                    with urllib.request.urlopen(f"{self.base_url}/jobs/{handle}", timeout=15) as resp:
                        job = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                return {"error": f"failed to query {mode} status for {handle}: {exc}", "status": "failed"}

            status = str(job.get("status") or "").lower()
            if status in ("success", "completed"):
                result = job.get("result")
                if not isinstance(result, dict):
                    result = {"status": "completed", "stdout": str(result or "")}
                result.setdefault("status", "completed")
                result.setdefault("job_id", handle)
                if mode == "queue":
                    result.setdefault("queue_task_id", handle)
                return annotate(kind, config or {}, result) if kind else result
            if status in ("error", "failed", "cancelled"):
                return {"error": job.get("error") or f"{mode} task failed", "status": "failed"}
            if status == "timeout":
                return {
                    "error": job.get("error") or f"quantum job timed out after {node_timeout:.0f}s",
                    "status": "timeout",
                }
            if status == "abandoned":
                return {"error": job.get("error") or "quantum job abandoned", "status": "failed"}
            if time.time() > deadline:
                # 超时：真正放弃这次任务（服务端会标记 stuck，禁止后续并发下发）
                self._cancel(mode, handle)
                return {
                    "error": f"node timed out after {node_timeout:.0f}s ({mode} {handle} abandoned)",
                    "status": "timeout",
                }

    def _cancel(self, mode: str, handle: str) -> None:
        """超时/取消时收尾：队列任务走 /cancel，直连 job 走 /jobs/{id}/abandon。"""
        if not handle:
            return
        if mode == "queue":
            try:
                self._queue_post("/cancel", {"task_id": handle}, timeout=10)
            except Exception as exc:
                self._log(f"[Queue] failed to cancel task {handle}: {exc}")
            return
        self.abandon(handle)

    # ── 语义接口（v2 契约；当前 server 走上面的低层方法） ────────────────────
    def run(self, req: RunRequest, token: CancelToken = None) -> RawResult:
        fn = req.experiment if "." in req.experiment else f"sq.{EXPERIMENT_MAP.get(req.experiment, req.experiment)}"
        if not SAFE_FN_RE.match(fn):
            return RawResult(ok=False, status="failed", error=f"invalid experiment fn {fn!r}")
        if req.qubit and not SAFE_QUBIT_RE.match(req.qubit):
            return RawResult(ok=False, status="failed", error=f"invalid qubit {req.qubit!r}")
        params = {}
        for key, value in (req.params or {}).items():
            # v2 的 params 是"规范名 → 数量"；这里把数量拆成裸值，单位换算留给上层
            params[key] = value.get("value") if isinstance(value, dict) and "value" in value else value
        try:
            code = build_experiment_code(fn, req.qubit, params, report_dataset=True)
        except ValueError as exc:
            return RawResult(ok=False, status="failed", error=str(exc))
        raw = self.submit_and_wait(code, req.timeout or self.default_timeout, token,
                                   kind="experiment", config={"threshold": 0.4})
        ok = not raw.get("error") and str(raw.get("status")) not in ("failed", "timeout", "cancelled")
        return RawResult(
            ok=ok,
            status=str(raw.get("status") or ("completed" if ok else "failed")),
            data_ref=raw.get("dataset_id"),
            artifacts={"plotPath": raw["plotPath"]} if raw.get("plotPath") else {},
            stdout=raw.get("stdout") or "",
            error=raw.get("error") or "",
            meta={"vendor": "lqcs", "native_id": raw.get("job_id") or raw.get("task_id")},
        )

    def read_contrast(self, dataset_id: Optional[int], qubit: str, kind: str = "IQraw",
                      threshold: float = 0.4, timeout: float = None,
                      token: CancelToken = None) -> Dict[str, Any]:
        code = build_contrast_code(dataset_id, qubit, kind)
        return self.submit_and_wait(code, timeout or self.default_timeout, token,
                                    kind="contrast", config={"threshold": threshold})
