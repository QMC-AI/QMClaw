"""比特状态机（QMClaw Architecture Roadmap §2.4）。

状态序列（单向推进，不回退；只有拿到更强的证据才前进）：
    unknown → freq_locked → amp_calibrated → state_discriminated
            → coherence_measured → characterized

判据来源：指标一律走 `services.common.metrics`（阈值来自 `config/lab_criteria.json`），
本模块只负责"证据 → 状态"。

纯函数、无 IO，可离线单测。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.common.metrics import evaluate, load_criteria

STATES: List[str] = [
    "unknown",
    "freq_locked",
    "amp_calibrated",
    "state_discriminated",
    "coherence_measured",
    "characterized",
]

STATE_INDEX = {name: i for i, name in enumerate(STATES)}

STATE_DESCRIPTIONS = {
    "unknown": "还没有任何可信证据（频率/幅度/对比度都没有确认过）",
    "freq_locked": "比特频率已确认并锁定（f10 稳定、spectroscopy/pidf 有峰）",
    "amp_calibrated": "π 脉冲幅度已标定（幅度扫描找到最优，回到基态的幅度已知）",
    "state_discriminated": "态区分度达标（contrast ≥ unusable 门槛，可读出 0/1）",
    "coherence_measured": "相干时间已测出且达标（T1、T2* 均过门槛）",
    "characterized": "表征完成（含 XEB 门保真度）",
}

NEXT_ACTION = {
    "unknown": "先用 /qubit/capabilities 看清可用能力，再用 /qubit/probe_transition 扫频找跃迁（真机动作需先向用户提问授权）",
    "freq_locked": "做幅度扫描（piamp 序列）标定 π 脉冲幅度",
    "amp_calibrated": "采 iqraw 测态区分度（contrast）",
    "state_discriminated": "测 T1（固定 zpa）与 Ramsey T2*",
    "coherence_measured": "跑 XEB 得到门保真度",
    "characterized": "比特表征完成；后续按需复核（读出链慢漂时重测 contrast）",
}

#: 每种证据由哪个实验产出、看哪份数据集（评审时"缺什么、该跑哪个实验"直接查这张表）。
EVIDENCE_SOURCES: Dict[str, Dict[str, Any]] = {
    "freq_locked": {
        "produced_by": ["sq.spectroscopy", "sq.spectroscopy_iq", "sq.pidf", "sq.ramsey_df"],
        "note": "频率类实验成功且出了数据集；`sq.spectroscopy` 必须显式给 freq，否则退化成标量崩",
    },
    "amp_calibrated": {
        "produced_by": ["sq.piamp", "sq.set_pi", "sq.time_rabi"],
        "note": "幅度扫描找到最优点；`sq.set_pi` 在 tbuffer=0 时不可用于标定",
    },
    "contrast": {
        "produced_by": ["sq.iqraw"],
        "note": "态区分度，判据见 config/lab_criteria.json 的 contrast 三档（0.2/0.4/0.7）",
    },
    "t1_us": {
        "produced_by": ["sq.t1"],
        "note": "`sq.t1` 默认跑 zpa×时间二维扫描，只测一个 zpa 必须显式传固定 zpa",
    },
    "t2_star_us": {
        "produced_by": ["sq.ramsey_t2", "sq.spinecho_t2"],
        "note": "T2* 不低于 1 us 才算可达标（阈值在 lab_criteria.json）",
    },
    "xeb_p": {
        "produced_by": ["sq.xeb"],
        "note": "单比特随机基准；p 与每周期 Pauli 误差由 process_xeb 给出",
    },
    "xeb_gate_fidelity": {
        "produced_by": ["sq.xeb"],
        "note": "门保真度门槛 0.99；与 xeb_p 同源，二选一即可",
    },
}

#: 每个状态"再往前一步"缺哪些证据（顺序即建议的补齐顺序）。
MISSING_FOR_STATE: Dict[str, List[str]] = {
    "unknown": ["freq_locked"],
    "freq_locked": ["amp_calibrated"],
    "amp_calibrated": ["contrast"],
    "state_discriminated": ["t1_us", "t2_star_us"],
    "coherence_measured": ["xeb_p", "xeb_gate_fidelity"],
    "characterized": [],
}


def evidence_plan(state: Optional[str] = None) -> Dict[str, Any]:
    """返回"当前状态缺什么证据、该跑哪个实验"。

    ``state`` 为 None 时返回全表（供文档/前端/自检引用）。
    """
    if state is None:
        return {
            "sources": {k: dict(v) for k, v in EVIDENCE_SOURCES.items()},
            "missing_by_state": {k: list(v) for k, v in MISSING_FOR_STATE.items()},
        }
    if state not in MISSING_FOR_STATE:
        raise ValueError(f"unknown state: {state}")
    return {
        "state": state,
        "missing": [
            {"key": key, **dict(EVIDENCE_SOURCES.get(key, {}))}
            for key in MISSING_FOR_STATE[state]
        ],
        "next_action": NEXT_ACTION.get(state, ""),
    }


def _flag(evidence: Dict[str, Any], key: str) -> bool:
    val = evidence.get(key)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "ok", "locked", "done")
    return False


def derive_state(
    evidence: Optional[Dict[str, Any]] = None,
    criteria: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """由证据推导比特当前状态。

    evidence 可用键：
      freq_locked(bool) / amp_calibrated(bool)
      contrast(float) / dmu_sig(float)
      t1_us(float) / t2_star_us(float)
      xeb_p(float) / xeb_gate_fidelity(float)
    """
    evidence = dict(evidence or {})
    criteria = criteria or load_criteria()

    reasons: List[str] = []
    metrics: Dict[str, Any] = {}

    # 1) 频率锁定
    freq_ok = _flag(evidence, "freq_locked")
    if freq_ok:
        reasons.append("freq_locked=True：频率已确认并锁定")

    # 2) π 脉冲幅度标定（显式标记，或"已有可用对比度"隐含说明幅度已可复现）
    amp_ok = _flag(evidence, "amp_calibrated")
    contrast = evidence.get("contrast")
    contrast_verdict = None
    if contrast is not None:
        contrast_verdict = evaluate("contrast", contrast, criteria)
        metrics["contrast"] = contrast_verdict
        if contrast_verdict["verdict"] != "unusable":
            amp_ok = True
            reasons.append(
                "contrast=%.4f（%s）→ π 脉冲幅度已能稳定分辨 0/1，视为 amp_calibrated"
                % (float(contrast), contrast_verdict["verdict"])
            )
    elif _flag(evidence, "amp_calibrated"):
        reasons.append("amp_calibrated=True")

    # 3) 态区分
    state_ok = bool(contrast_verdict and contrast_verdict["ok"])
    if state_ok:
        reasons.append(
            "contrast=%.4f 达门槛 → state_discriminated" % float(contrast)
        )

    # 4) 相干时间
    t1 = evidence.get("t1_us")
    t2 = evidence.get("t2_star_us")
    t1_eval = t2_eval = None
    if t1 is not None:
        t1_eval = evaluate("t1", t1, criteria)
        metrics["t1"] = t1_eval
    if t2 is not None:
        t2_eval = evaluate("t2_star", t2, criteria)
        metrics["t2_star"] = t2_eval
    coherence_ok = bool(
        t1_eval and t1_eval["ok"] and t2_eval and t2_eval["ok"]
    )
    if coherence_ok:
        reasons.append("T1=%.2fus 且 T2*=%.2fus 达门槛 → coherence_measured" % (float(t1), float(t2)))

    # 5) XEB 表征
    xeb_p = evidence.get("xeb_p")
    xeb_gf = evidence.get("xeb_gate_fidelity")
    xeb_eval = None
    if xeb_p is not None or xeb_gf is not None:
        xeb_eval = evaluate("xeb", {"p": xeb_p, "gate_fidelity": xeb_gf}, criteria)
        metrics["xeb"] = xeb_eval
    characterized = bool(coherence_ok and xeb_eval and xeb_eval["ok"])
    if characterized:
        reasons.append("XEB 门保真度达门槛 → characterized")

    # 状态推进（取满足的最深状态）
    if characterized:
        state = "characterized"
    elif coherence_ok:
        state = "coherence_measured"
    elif state_ok:
        state = "state_discriminated"
    elif amp_ok:
        state = "amp_calibrated"
    elif freq_ok:
        state = "freq_locked"
    else:
        state = "unknown"

    missing: List[str] = []
    if state == "unknown":
        missing.append("freq_locked")
    if state == "freq_locked":
        missing.append("amp_calibrated")
    if state == "amp_calibrated":
        missing.append("contrast")
    if state == "state_discriminated":
        missing.append("t1_us/t2_star_us")
    if state == "coherence_measured":
        missing.append("xeb_p")

    return {
        "state": state,
        "state_index": STATE_INDEX[state],
        "description": STATE_DESCRIPTIONS[state],
        "reasons": reasons,
        "missing": missing,
        "missing_detail": [
            {"key": key, **dict(EVIDENCE_SOURCES.get(key, {}))}
            for key in MISSING_FOR_STATE.get(state, [])
        ],
        "next_action": NEXT_ACTION[state],
        "metrics": metrics,
        "criteria_version": criteria.get("version"),
    }


def state_table() -> Dict[str, Any]:
    """状态机全表（供文档/前端/自检引用）。"""
    return {
        "states": list(STATES),
        "descriptions": dict(STATE_DESCRIPTIONS),
        "next_action": dict(NEXT_ACTION),
        "evidence_sources": {k: dict(v) for k, v in EVIDENCE_SOURCES.items()},
        "missing_by_state": {k: list(v) for k, v in MISSING_FOR_STATE.items()},
    }
