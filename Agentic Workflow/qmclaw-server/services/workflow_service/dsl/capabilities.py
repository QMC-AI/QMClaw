"""后端能力词表与协商（IR v2 预留，见 `docs/BACKEND_ADAPTERS.md`）。

为什么需要这一层：现在 `validate` 只能判断"IR 自洽"，判断不了"目标后端会不会"。
换一套测控软件时，缺的不是语法，而是 `sq.set_pi`、`qter.fitData`、参数读写这些东西。
把"会什么"写成声明式的键，`validate` 就能在**下发之前**报 `E_CAPABILITY_MISSING`，
而不是等真机抛 `AttributeError`（`sq.spectroscopy(qobj)` 抛 `IndexError` 那次就是反面教材）。

能力键的形状（全部小写、点分层级）：

    experiment.<kind>       能跑某类实验        experiment.t1
    fit.<kind>              能拟合出某类数量    fit.t1
    sweep.abs / .relative   只支持绝对扫描 / 也支持 center+span
    param.read / .write     能读写设备参数
    control.async_job       提交后立刻返回句柄  control.cancel / .dry_run / .live_update

本模块**不做** IO：能力表由适配器的 `capabilities()` 给出，这里只负责比较与解释。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set

# 能力组（用于报错时分组展示）
CAP_GROUPS = ("experiment", "fit", "sweep", "param", "control")

# 通用能力全集（适配器可以只实现子集；这里列的是"词表"，不是"要求"）
KNOWN_CAPS: Set[str] = {
    # 实验种类：与 IR `run.experiment` 的受控词表一致
    "experiment.s21", "experiment.s21_power2d", "experiment.s21_zpa2d",
    "experiment.s21_dis", "experiment.spectroscopy", "experiment.spectroscopy_auto",
    "experiment.iqraw", "experiment.piamp", "experiment.pidf",
    "experiment.ramsey_df", "experiment.ramsey_t2", "experiment.spinecho_t2",
    "experiment.pulse_shape", "experiment.pulse_shape_ramsey",
    "experiment.set_pi", "experiment.test_timing_xyz", "experiment.t1",
    "experiment.xeb", "experiment.base_slope",
    # 拟合
    "fit.t1", "fit.t2", "fit.t2star", "fit.ramsey", "fit.iq_discriminate",
    "fit.spectroscopy", "fit.rabi", "fit.xeb",
    # 扫描
    "sweep.abs", "sweep.relative", "sweep.arange", "sweep.custom",
    # 参数
    "param.read", "param.write", "param.registry",
    # 控制面
    "control.async_job", "control.cancel", "control.dry_run",
    "control.live_update", "control.retry", "control.lock",
}

# 缺能力时给出的替代建议（只覆盖最常见的几个）
SUGGESTIONS: Dict[str, str] = {
    "experiment.set_pi": "用 x180 幅度扫描 + fit.rabi 手动标定 pi 脉冲",
    "experiment.base_slope": "在适配器外调用厂商脚本，或降级为 native 语句",
    "experiment.spectroscopy": "改用 experiment.s21 + fit.spectroscopy 定位 f10",
    "experiment.spectroscopy_auto": "拆成 spectroscopy + s21_power2d 两步",
    "sweep.relative": "把 center/span 换算成绝对 start/stop 再下发",
    "sweep.arange": "只支持等距扫描，IR 里显式写 start/stop/points",
    "control.cancel": "取消只能生效在语句边界（不再提交下一条）",
    "control.dry_run": "只能复放历史结果，不能合成",
    "param.write": "参数只能在实验内部更新（update=True），不能显式写",
}


def normalize(caps: Iterable[str]) -> Set[str]:
    return {str(c).strip().lower() for c in caps if str(c).strip()}


def manifest_caps(manifest: Dict[str, Any]) -> Set[str]:
    """从适配器的 `capabilities()` 返回值里取能力键。

    允许两种写法：`{"caps": [...]}` 或 `{"experiments": [...], "fitters": [...]}` 这类分组；
    分组的键会按 `experiment.*` / `fit.*` 前缀展开。
    """
    if not isinstance(manifest, dict):
        return set()
    caps = set(normalize(manifest.get("caps") or []))
    for kind, prefix in (("experiments", "experiment"), ("fitters", "fit"), ("sweeps", "sweep")):
        for item in manifest.get(kind) or []:
            item = str(item).strip().lower()
            if item:
                caps.add(item if "." in item else f"{prefix}.{item}")
    for flag in manifest.get("control") or []:
        caps.add(f"control.{str(flag).strip().lower()}")
    return caps


def missing(required: Iterable[str], manifest: Dict[str, Any]) -> List[str]:
    """返回缺失的能力键（保持声明顺序，便于稳定报错）。"""
    have = manifest_caps(manifest)
    out: List[str] = []
    for cap in required or []:
        cap = str(cap).strip().lower()
        if cap and cap not in have and cap not in out:
            out.append(cap)
    return out


def explain(missing_caps: Iterable[str]) -> List[str]:
    """把缺失能力翻译成"人话 + 建议"。"""
    lines = []
    for cap in missing_caps:
        group = cap.split(".", 1)[0]
        hint = SUGGESTIONS.get(cap)
        if hint:
            lines.append(f"{cap}（{group}）缺失 → 建议：{hint}")
        else:
            lines.append(f"{cap}（{group}）缺失 → 该后端未声明此能力")
    return lines


def unknown_caps(caps: Iterable[str]) -> List[str]:
    """词表外的能力键：允许（厂商自定义），但列出以便发现拼写错误。"""
    return sorted(c for c in normalize(caps) if c not in KNOWN_CAPS)


def describe(backend: str) -> Dict[str, Any]:
    """取某个后端的能力声明（懒加载，避免 dsl 包导入时就拉 backends）。"""
    from .backends import get_adapter

    return get_adapter(backend).capabilities()


def manifest_for(backend: str) -> Dict[str, Any]:
    return describe(backend)


# ── 厂商符号泄漏检查（v1 兼容期的提示，不改变 v1 的合法性） ────────────────────
VENDOR_SYMBOL_HINT = (
    "IR 里出现了厂商符号；v2 应写成 experiment/sweep/params/analysis，"
    "厂商绑定放进适配器（见 docs/BACKEND_ADAPTERS.md §3）"
)


def vendor_leak_diags(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    """扫描 v1 IR 里"住着厂商方言"的字段，返回 warning 级诊断。

    只做两件事，避免噪声：
      * `measure.fn` 形如 `sq.iqraw`（小写标识符 + 点）；
      * `analyze.command` 非空（纯 Python 表达式，必然绑定某个厂商的分析库）。
    """
    from .const import OP_ANALYZE, OP_MEASURE
    from .ir import iter_all_statements

    diags: List[Dict[str, Any]] = []
    pipeline = ir.get("pipeline") or [] if isinstance(ir, dict) else []
    for index, stmt in enumerate(iter_all_statements(pipeline)):
        op = stmt.get("op")
        if op == OP_MEASURE:
            fn = str(stmt.get("fn") or "")
            if "." in fn:
                diags.append({
                    "code": "W_VENDOR_LEAK", "level": "warning",
                    "message": f"measure.fn={fn!r} 是厂商函数名；{VENDOR_SYMBOL_HINT}",
                    "path": f"pipeline[{index}].fn", "line": stmt.get("source", {}).get("line"),
                    "col": stmt.get("source", {}).get("col"),
                })
        elif op == OP_ANALYZE:
            cmd = str(stmt.get("command") or "")
            if cmd:
                diags.append({
                    "code": "W_VENDOR_LEAK", "level": "warning",
                    "message": f"analyze.command={cmd[:40]!r} 是厂商分析表达式；v2 应写 analysis{{fit: ...}}",
                    "path": f"pipeline[{index}].command", "line": stmt.get("source", {}).get("line"),
                    "col": stmt.get("source", {}).get("col"),
                })
    return diags
