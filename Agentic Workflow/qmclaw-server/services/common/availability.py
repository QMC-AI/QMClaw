"""真机可用性语义（唯一实现）。

背景（roadmap §3 #3）：``:3003`` 与 ``:3004`` 的健康检查各自手写一套判断——
"LabRAD 连上就 healthy、离线模式算 healthy、在线模式但没连上才算 degraded"这段
逻辑在两个服务里逐字重复；更糟的是两个服务还各有一份**永远走不到**的
``_handle_health``（``/health`` 在 ``base_service`` 的 HTTP 层就被拦下并走
``get_health()``），那份死代码的判据与活的那份**不一致**（analysis 的死版本
不含 stuck、也不认 offline_provider）。

于是把"可用性"提成一个显式对象：

    availability = {
        "state": "ok" | "degraded" | "unavailable",
        "reasonCode": None | "stuck" | "offline_mode" | "offline_fallback"
                            | "not_connected" | "initializing",
        "message": 人能看懂的一句话,
        "device": "labrad" | "datalab",
        "deviceConnected": bool,
        "mode": "online" | "offline" | "auto",
        "fallbackState": str,
        "sessionPath": [...] | None,
        "busy": bool,
        "ready": bool,
        "offlineAvailable": bool,
        "stuck": bool,
    }

语义约定：

* ``ok`` —— 真机链路可用（连上了；或离线模式下按配置就是预期行为）。
* ``degraded`` —— 还能应答，但真机能力受限（卡死闸门、在途请求、降级重连中）。
* ``unavailable`` —— 真机能力不可用，只能走离线/回放。

**与 ``status`` 的关系**：``status`` 是历史字段（``healthy`` / ``degraded``），
前端与多个服务按它取值，不能删。``derive_legacy_status()`` 把它从 ``state``
推出来，保证只有一个事实源；``unavailable`` 仍映射为 ``degraded``，
因为老消费者把非 ``healthy`` 一律当"不可用"，而 HTTP 码历史上就是 503。
"""

from typing import Any, Dict, Iterable, List, Optional

#: 可用性状态（与 ``services/common/errors.py`` 的规范状态**不是**一套：
#: 这里只描述"真机能力"，不描述"这次请求的结局"）。
AVAILABILITY_STATES = ("ok", "degraded", "unavailable")

#: 原因码（稳定字符串，前端/台账可直接比较）。
REASON_OK = None
REASON_STUCK = "stuck"
REASON_OFFLINE_MODE = "offline_mode"
REASON_OFFLINE_FALLBACK = "offline_fallback"
REASON_NOT_CONNECTED = "not_connected"
REASON_INITIALIZING = "initializing"

REASON_CODES = (
    REASON_STUCK,
    REASON_OFFLINE_MODE,
    REASON_OFFLINE_FALLBACK,
    REASON_NOT_CONNECTED,
    REASON_INITIALIZING,
)

#: 原因码 → 人的话。
REASON_MESSAGES = {
    REASON_STUCK: "上一个任务超时后仍占用通道，需人工确认现场安全后 reset_stuck",
    REASON_OFFLINE_MODE: "服务处于强制离线模式，不访问真机",
    REASON_OFFLINE_FALLBACK: "真机未连接，已自动降级到离线数据",
    REASON_NOT_CONNECTED: "真机未连接（在线模式下属于异常）",
    REASON_INITIALIZING: "服务正在初始化连接",
}

_NORMALIZED_MODES = {
    "online": "online",
    "offline": "offline",
    "auto": "auto",
    "": "auto",
    None: "auto",
}


def normalize_mode(mode: Any) -> str:
    """把 ``Mode`` 枚举 / 字符串归一成 ``online`` / ``offline`` / ``auto``。

    认不出来的一律按 ``auto``（最保守：既不假装在线，也不假装离线）。
    """
    if mode is None:
        return "auto"
    value = getattr(mode, "value", mode)
    if isinstance(value, str):
        return _NORMALIZED_MODES.get(value.strip().lower(), "auto")
    return "auto"


def build_availability(
    *,
    device: str,
    device_connected: bool,
    mode: Any = "auto",
    fallback_state: Optional[str] = None,
    offline_available: bool = False,
    stuck: bool = False,
    busy: bool = False,
    session_path: Optional[Iterable[Any]] = None,
    initializing: bool = False,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """按统一判据算出可用性对象。

    判据顺序（先命中先返回）：

    1. ``stuck`` → ``degraded`` / ``stuck``（闸门关着，比"没连上"更该被看到）
    2. ``device_connected`` → ``ok``
    3. ``mode == "offline"`` → ``ok`` / ``offline_mode``（离线是配置，不是故障）
    4. ``mode == "auto"`` 且 ``offline_available`` → ``ok`` / ``offline_fallback``
    5. ``mode == "auto"`` 且 ``fallback_state == "degraded_permanent"``
       → ``unavailable`` / ``not_connected``
    6. ``initializing`` → ``degraded`` / ``initializing``
    7. 其余 → ``unavailable`` / ``not_connected``
    """
    normalized_mode = normalize_mode(mode)
    fallback_state = fallback_state or "unknown"
    if session_path is None:
        session_list: Optional[List[Any]] = None
    elif isinstance(session_path, str):
        session_list = [session_path]
    else:
        session_list = list(session_path)

    state = "unavailable"
    reason: Optional[str] = None
    if stuck:
        state, reason = "degraded", REASON_STUCK
    elif device_connected:
        state, reason = "ok", REASON_OK
    elif normalized_mode == "offline":
        state, reason = "ok", REASON_OFFLINE_MODE
    elif normalized_mode == "auto" and offline_available:
        state, reason = "ok", REASON_OFFLINE_FALLBACK
    elif normalized_mode == "auto" and fallback_state == "degraded_permanent":
        state, reason = "unavailable", REASON_NOT_CONNECTED
    elif initializing:
        state, reason = "degraded", REASON_INITIALIZING
    else:
        state, reason = "unavailable", REASON_NOT_CONNECTED

    if message is None:
        message = REASON_MESSAGES.get(reason) if reason else "真机链路正常"

    return {
        "state": state,
        "reasonCode": reason,
        "message": message or "",
        "device": device,
        "deviceConnected": bool(device_connected),
        "mode": normalized_mode,
        "fallbackState": fallback_state,
        "sessionPath": session_list,
        "busy": bool(busy),
        "ready": not bool(busy),
        "offlineAvailable": bool(offline_available),
        "stuck": bool(stuck),
    }


def derive_legacy_status(availability: Dict[str, Any]) -> str:
    """把可用性对象折成历史 ``status``（``healthy`` / ``degraded``）。

    只有 ``ok`` 算 ``healthy``；``degraded`` 与 ``unavailable`` 都是 ``degraded``
    —— 与改动前完全一致，避免把已经为 503 的场景变成 200。
    """
    state = (availability or {}).get("state")
    return "healthy" if state == "ok" else "degraded"


def health_status_code(health: Dict[str, Any]) -> int:
    """``/health`` 的 HTTP 码：``healthy`` → 200，其余 → 503。

    优先看 ``availability.state``（新事实源），退回历史 ``status`` 字段。
    """
    availability = (health or {}).get("availability")
    if isinstance(availability, dict) and availability.get("state"):
        return 200 if availability.get("state") == "ok" else 503
    return 200 if (health or {}).get("status") == "healthy" else 503


def describe_availability() -> Dict[str, Any]:
    """给 ``/health`` 之外的自检、文档用的说明表（确定性，无时间戳）。"""
    return {
        "version": "1.0",
        "states": list(AVAILABILITY_STATES),
        "reasons": {
            "stuck": {"state": "degraded", "message": REASON_MESSAGES[REASON_STUCK]},
            "ok": {"state": "ok", "message": "真机链路正常"},
            "offline_mode": {
                "state": "ok",
                "message": REASON_MESSAGES[REASON_OFFLINE_MODE],
            },
            "offline_fallback": {
                "state": "ok",
                "message": REASON_MESSAGES[REASON_OFFLINE_FALLBACK],
            },
            "initializing": {
                "state": "degraded",
                "message": REASON_MESSAGES[REASON_INITIALIZING],
            },
            "not_connected": {
                "state": "unavailable",
                "message": REASON_MESSAGES[REASON_NOT_CONNECTED],
            },
        },
        "legacyStatus": {"ok": "healthy", "degraded": "degraded", "unavailable": "degraded"},
    }
