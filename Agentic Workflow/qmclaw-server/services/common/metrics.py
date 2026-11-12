"""指标判定的唯一入口（QMClaw Architecture Roadmap §2.5 Metric Registry）。

设计原则：
* **阈值不在本模块写死** —— 全部来自 `config/lab_criteria.json`（指标唯一真源）。
* 本模块只做三件事：取阈值 → 判 verdict → 给下一步建议。
* 纯函数、无 IO 副作用（除了读 json），可离线单测。

用法：
    from services.common.metrics import evaluate, evaluate_contrast
    evaluate("contrast", 0.765)
    evaluate_contrast(0.28)          # -> {"verdict": "weak_but_usable", ...}
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_ROOT = os.path.dirname(os.path.dirname(_HERE))  # .../qmclaw-server
DEFAULT_CRITERIA_PATH = os.path.join(SERVER_ROOT, "config", "lab_criteria.json")

_CACHE: Dict[str, Dict[str, Any]] = {}


def load_criteria(path: Optional[str] = None) -> Dict[str, Any]:
    """读取 lab_criteria.json（带进程内缓存，path 不同则分开缓存）。"""
    target = os.path.abspath(path or DEFAULT_CRITERIA_PATH)
    cached = _CACHE.get(target)
    if cached is None:
        with open(target, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        _CACHE[target] = cached
    return cached


def metric_def(criteria: Dict[str, Any], name: str) -> Dict[str, Any]:
    metrics = criteria.get("metrics") or {}
    if name not in metrics:
        raise KeyError(
            "未知指标 %r；可用指标：%s" % (name, ", ".join(sorted(metrics.keys())))
        )
    return metrics[name]


def _result(
    metric: str,
    value: Any,
    verdict: str,
    thresholds: Dict[str, Any],
    ok: bool,
    next_step: str,
    criteria: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "metric": metric,
        "value": value,
        "verdict": verdict,
        "ok": bool(ok),
        "thresholds": thresholds,
        "next_step": next_step,
        "criteria_version": criteria.get("version"),
    }


def contrast_from_p1(p1_i: float, p1_x: float) -> float:
    """contrast = |P(1|prep X) - P(1|prep I)|（lab_criteria.metrics.contrast.definition）。"""
    return abs(float(p1_x) - float(p1_i))


def evaluate_contrast(value: float, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    criteria = criteria or load_criteria()
    d = metric_def(criteria, "contrast")
    th = d.get("thresholds") or {}
    v = float(value)
    unusable = float(th.get("unusable", 0.2))
    weak = float(th.get("weak_but_usable", 0.4))
    good = float(th.get("good", 0.7))
    semantics = d.get("threshold_semantics") or {}
    if v < unusable:
        verdict, ok, nxt = "unusable", False, semantics.get("unusable", "")
    elif v < weak:
        verdict, ok, nxt = "weak_but_usable", True, semantics.get("weak_but_usable", "")
    else:
        verdict, ok, nxt = "good", True, semantics.get("good", "")
    return _result("contrast", v, verdict, th, ok, nxt, criteria)


def evaluate_dmu_sig(value: float, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    criteria = criteria or load_criteria()
    d = metric_def(criteria, "dmu_sig")
    th = d.get("thresholds") or {}
    v = float(value)
    alive = float(th.get("alive", 1.5))
    ambiguous = float(th.get("ambiguous", 0.5))
    if v >= alive:
        verdict, ok, nxt = "alive", True, "无监督判据通过；用 contrast 复核"
    elif v >= ambiguous:
        verdict, ok, nxt = "ambiguous", False, "接近门槛，建议延长采样或加驱动"
    else:
        verdict, ok, nxt = "no_response", False, "疑似无可见跃迁：先用 /qubit/probe_transition 扫频"
    return _result("dmu_sig", v, verdict, th, ok, nxt, criteria)


def evaluate_t1(value_us: float, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    criteria = criteria or load_criteria()
    d = metric_def(criteria, "t1")
    th = d.get("thresholds") or {}
    v = float(value_us)
    min_us = float(th.get("min_us", 20.0))
    ok = v >= min_us
    verdict = "good" if ok else "too_short"
    nxt = "" if ok else "T1 偏短：检查读出误判与 π 脉冲幅度"
    return _result("t1", v, verdict, th, ok, nxt, criteria)


def evaluate_t2_star(value_us: float, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    criteria = criteria or load_criteria()
    d = metric_def(criteria, "t2_star")
    th = d.get("thresholds") or {}
    v = float(value_us)
    min_us = float(th.get("min_us", 1.0))
    ok = v >= min_us
    verdict = "good" if ok else "too_short"
    nxt = "" if ok else "T2* 偏短：检查 detuning 与脉冲带宽"
    return _result("t2_star", v, verdict, th, ok, nxt, criteria)


def evaluate_xeb(
    p: Optional[float] = None,
    gate_fidelity: Optional[float] = None,
    criteria: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    criteria = criteria or load_criteria()
    d = metric_def(criteria, "xeb")
    th = d.get("thresholds") or {}
    min_p = float(th.get("min_p", 0.99))
    min_gf = float(th.get("min_gate_fidelity", 0.99))
    problems = []
    if p is not None and float(p) < min_p:
        problems.append("p=%.4f < %.4f" % (float(p), min_p))
    if gate_fidelity is not None and float(gate_fidelity) < min_gf:
        problems.append("gate_fidelity=%.4f < %.4f" % (float(gate_fidelity), min_gf))
    ok = not problems
    verdict = "good" if ok else "poor"
    nxt = "" if ok else "门保真度不足：" + "；".join(problems)
    return _result("xeb", {"p": p, "gate_fidelity": gate_fidelity}, verdict, th, ok, nxt, criteria)


def evaluate(metric: str, value: Any, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """按指标名分发。value 语义：标量（contrast/dmu_sig/t1/t2_star）或 xeb 的 dict。"""
    criteria = criteria or load_criteria()
    name = str(metric).strip()
    if name == "contrast":
        return evaluate_contrast(value, criteria)
    if name == "dmu_sig":
        return evaluate_dmu_sig(value, criteria)
    if name == "t1":
        return evaluate_t1(value, criteria)
    if name == "t2_star":
        return evaluate_t2_star(value, criteria)
    if name == "xeb":
        if isinstance(value, dict):
            return evaluate_xeb(value.get("p"), value.get("gate_fidelity"), criteria)
        return evaluate_xeb(value, None, criteria)
    raise KeyError("未知指标 %r；可用指标：%s" % (name, ", ".join(sorted((criteria.get("metrics") or {}).keys()))))


# ── 阈值档位解析（阶段 4：让 goal 判定引用 Metric Registry 而不是硬编码）────────
# 每个指标的"档位别名" → lab_criteria.json 里的 thresholds 键。
# 别名让调用方可以说 "min / target / good" 这种人话，registry 仍是唯一真源。
THRESHOLD_ALIASES: Dict[str, Dict[str, str]] = {
    "contrast": {
        "min": "unusable", "usable": "unusable", "unusable": "unusable",
        "weak": "weak_but_usable", "weak_but_usable": "weak_but_usable",
        "target": "good", "good": "good",
    },
    "dmu_sig": {
        "min": "ambiguous", "ambiguous": "ambiguous",
        "target": "alive", "alive": "alive",
    },
    "t1": {"min": "min_us", "min_us": "min_us"},
    "t2_star": {"min": "min_us", "min_us": "min_us"},
    "xeb": {
        "min": "min_p", "min_p": "min_p",
        "gate": "min_gate_fidelity", "min_gate_fidelity": "min_gate_fidelity",
    },
    "s21_dip": {
        "min": "min_depth_db", "min_depth_db": "min_depth_db",
        "offset": "max_offset_from_fread_mhz",
        "max_offset_from_fread_mhz": "max_offset_from_fread_mhz",
    },
}

# 绑定里的指标名 → lab_criteria 里的指标名（goal.success.criteria 可省，用这个推）
CRITERIA_NAME_ALIASES: Dict[str, str] = {
    "contrast": "contrast",
    "dmu_sig": "dmu_sig",
    "snr": "dmu_sig",
    "t1": "t1",
    "t1_us": "t1",
    "t2": "t2_star",
    "t2_us": "t2_star",
    "t2_star": "t2_star",
    "xeb": "xeb",
    "xeb_p": "xeb",
    "p": "xeb",
    "gate_fidelity": "xeb",
    "s21_dip": "s21_dip",
    "depth_db": "s21_dip",
}


def criteria_metric_name(ref: Any) -> str:
    """`$c1.contrast` / `T1_us` → registry 里的指标名（`contrast` / `t1`）。"""
    text = str(ref or "").strip()
    if text.startswith("$"):
        text = text[1:]
    tail = text.split(".", 1)[1] if "." in text else text
    tail = tail.strip()
    if tail in CRITERIA_NAME_ALIASES:
        return CRITERIA_NAME_ALIASES[tail]
    # 大小写不敏感：`T1_us` / `t1_us` 都归到 `t1`
    return CRITERIA_NAME_ALIASES.get(tail.lower(), tail)


def available_levels(metric: str, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """某个指标在 registry 里有哪些档位（人可读）。"""
    criteria = criteria or load_criteria()
    name = str(metric or "").strip()
    metrics = criteria.get("metrics") or {}
    if name not in metrics:
        return {"metric": name, "levels": [], "error": "未知指标 %r" % name}
    thresholds = metrics[name].get("thresholds") or {}
    return {
        "metric": name,
        "levels": sorted(thresholds.keys()),
        "aliases": sorted(THRESHOLD_ALIASES.get(name, {}).keys()),
        "error": None,
    }


def resolve_threshold(
    metric: str,
    level: str = "min",
    criteria: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """按指标名 + 档位取出 registry 里的阈值。

    返回 {metric, level, threshold_key, value, ref, source, error}；
    取不到时 value=None 且 error 说明原因（不抛异常——goal 校验要的是诊断不是崩）。
    """
    criteria = criteria or load_criteria()
    name = str(metric or "").strip()
    out: Dict[str, Any] = {
        "metric": name,
        "level": str(level or "min"),
        "threshold_key": None,
        "value": None,
        "ref": None,
        "source": "lab_criteria.json",
        "error": None,
    }
    metrics = criteria.get("metrics") or {}
    if name not in metrics:
        out["error"] = "未知指标 %r；可用指标：%s" % (
            name, ", ".join(sorted(metrics.keys())))
        return out
    thresholds = metrics[name].get("thresholds") or {}
    key = THRESHOLD_ALIASES.get(name, {}).get(out["level"], out["level"])
    if key not in thresholds:
        out["error"] = "指标 %r 没有阈值档 %r（可用：%s）" % (
            name, key, ", ".join(sorted(thresholds.keys())))
        return out
    out["threshold_key"] = key
    out["value"] = thresholds[key]
    out["ref"] = "%s.%s" % (name, key)
    return out


def evaluate_all(
    evidence: Dict[str, Any], criteria: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """批量判定一批证据里的所有已知指标（缺项跳过）。"""
    criteria = criteria or load_criteria()
    out: Dict[str, Any] = {}
    for name, value in (evidence or {}).items():
        if value is None:
            continue
        try:
            out[name] = evaluate(name, value, criteria)
        except KeyError:
            continue
    return out
