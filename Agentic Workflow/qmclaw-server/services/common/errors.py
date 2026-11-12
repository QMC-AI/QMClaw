"""services/common/errors.py — 统一错误语义（纯标准库，可离线单测）

现状问题（docs/ARCHITECTURE_ROADMAP.md §3 问题⑦）：同一类失败在不同服务里用不同的
status 字符串表达（degraded / stuck / quota_exceeded / needs_human / not_achieved /
budget_exhausted / blocked / read_only），调用方无法用一套逻辑处理。

本模块给出**错误码表 + 统一信封**：
- `CANONICAL_STATUSES`：规范状态集合（5 个），所有失败响应的 `status` 只可能是它们之一
- `ERROR_CODES`：错误码 → {status（规范值）, legacy_status（旧值）, http, hint, retryable}
- `error_envelope(code, message=None, **extra)`：生成统一响应体
- `normalize_error(payload)`：给任意错误响应补 status / reasonCode / error_code（幂等）
- `canonical_status(status)` / `is_canonical_status(status)`：给调用方的判定工具

两套词表的分工（见 docs/OPERATIONS.md §3.8）：
1. **规范状态**（本模块负责）：ok / degraded / queued / blocked / failed —— 失败与终态语义
2. **执行生命周期**（不在本模块负责，原样保留）：success / busy / offline /
   offline_simulated / idle / healthy / running / completed / cancelled / rejected /
   pending —— 过程语义，前端 `qmclaw-web/src/app/page.tsx` 直接依赖其中的 success/busy/offline

约束：不 import 任何服务模块；不改任何现有服务的返回结构（只在缺失时**追加**字段），
因此可以安全地逐服务接入。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# --- 规范状态（失败/终态语义） ------------------------------------------------
CANONICAL_STATUSES = ("ok", "degraded", "queued", "blocked", "failed")

# 执行生命周期状态：这些值表示"过程"，不是"错误结论"，不做状态改写。
_LIFECYCLE_STATUSES = frozenset(
    {
        "success",
        "simulated",
        "offline",
        "offline_simulated",
        "offline_no_simulator",
        "busy",
        "idle",
        "healthy",
        "running",
        "completed",
        "cancelled",
        "rejected",
        "pending",
    }
)


ERROR_CODES: Dict[str, Dict[str, Any]] = {
    # --- 治理 / 授权 ---
    "task_required": {
        "status": "blocked",
        "legacy_status": "blocked",
        "http": 409,
        "retryable": False,
        "hint": "真机实验必须带 task_id：先 POST :3009/task，或直接走 :3008/workflows/run_ir",
    },
    "quota_exceeded": {
        "status": "blocked",
        "legacy_status": "quota_exceeded",
        "http": 429,
        "retryable": False,
        "hint": "任务额度不足：请释放/退款已有数据集，或新建任务",
    },
    "not_authorized": {
        "status": "blocked",
        "legacy_status": "not_authorized",
        "http": 403,
        "retryable": False,
        "hint": "缺少授权票据：请先在 :3009 为该写清单申请 ticket",
    },
    "needs_human": {
        "status": "blocked",
        "legacy_status": "needs_human",
        "http": 409,
        "retryable": False,
        "hint": "需要人工确认后才能继续",
    },
    # --- 设备可用性 ---
    "read_only": {
        "status": "blocked",
        "legacy_status": "read_only",
        "http": 403,
        "retryable": False,
        "hint": "写闸门关闭：由人工把 allow_execute / allow_set_params 置为 true",
    },
    "degraded": {
        "status": "degraded",
        "legacy_status": "degraded",
        "http": 503,
        "retryable": True,
        "hint": "LabRAD 不可用：可 POST :3004/connect 或 :3003/connect 尝试恢复",
    },
    "stuck": {
        "status": "degraded",
        "legacy_status": "stuck",
        "http": 503,
        "retryable": False,
        "hint": "上一次执行超时后工作线程可能仍在真机运行：人工确认现场安全后 reset_stuck",
    },
    "not_connected": {
        "status": "degraded",
        "legacy_status": "not_connected",
        "http": 503,
        "retryable": True,
        "hint": "未连接 LabRAD：请 POST :3003/connect",
    },
    # --- 执行 ---
    "timeout": {
        "status": "failed",
        "legacy_status": "timeout",
        "http": 504,
        "retryable": True,
        "hint": "硬件通道等待超时：请查看 :3003/jobs 与队列状态",
    },
    "validation_error": {
        "status": "failed",
        "legacy_status": "invalid",
        "http": 400,
        "retryable": False,
        "hint": "请求参数不合法",
    },
    "not_found": {
        "status": "failed",
        "legacy_status": "not_found",
        "http": 404,
        "retryable": False,
        "hint": "对象不存在（比特/数据集/任务）",
    },
    "internal_error": {
        "status": "failed",
        "legacy_status": "error",
        "http": 500,
        "retryable": False,
        "hint": "服务内部错误，详见 error 字段",
    },
}

# 历史 status 字符串 → 错误码（用于 normalize_error 归一化既有响应）
_STATUS_ALIASES: Dict[str, str] = {
    "blocked": "task_required",
    "read_only": "read_only",
    "degraded": "degraded",
    "degraded_permanent": "degraded",
    "stuck": "stuck",
    "timeout": "timeout",
    "quota_exceeded": "quota_exceeded",
    "not_authorized": "not_authorized",
    "needs_human": "needs_human",
    "not_connected": "not_connected",
    "invalid": "validation_error",
    "not_found": "not_found",
    "error": "internal_error",
}

# 历史 status 字符串 → 规范状态（回答"该显示什么"；_STATUS_ALIASES 回答"为什么"）
_LEGACY_STATUS_MAP: Dict[str, str] = {
    alias: ERROR_CODES[code]["status"] for alias, code in _STATUS_ALIASES.items()
}
_LEGACY_STATUS_MAP.update({"failed": "failed", "degraded_permanent": "degraded"})


def is_canonical_status(status: Any) -> bool:
    """status 是否已经是规范状态。"""
    return isinstance(status, str) and status in CANONICAL_STATUSES


def canonical_status(status: Any) -> Optional[str]:
    """把历史 status 归一到规范状态。

    - 已是规范状态 → 原样返回
    - 命中历史表 → 返回对应的规范状态
    - 执行生命周期状态 → 返回 None（不改写）
    - 其他 → 返回 None（未知，交由调用方决定）
    """
    if not isinstance(status, str) or not status:
        return None
    if status in CANONICAL_STATUSES:
        return status
    if status in _LEGACY_STATUS_MAP:
        return _LEGACY_STATUS_MAP[status]
    return None


def error_envelope(
    code: str,
    message: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """生成统一错误信封。

    字段：
    - `status`：规范状态（CANONICAL_STATUSES 之一）
    - `reasonCode`：错误码（新增的规范字段）
    - `error_code`：错误码（兼容旧消费者）
    - `legacyStatus`：旧 status 原文（仅当与规范值不同时出现，便于排查/回溯）

    Args:
        code: ERROR_CODES 中的错误码；未知码自动登记为 internal_error 形态。
        message: 人类可读的具体信息（覆盖默认 hint）。
        extra: 追加字段（如 task_id / job_id / fallback_message）。
    """
    spec = ERROR_CODES.get(code)
    requested_code = code
    if spec is None:
        # 未知错误码 → 退化为 internal_error，但保留原请求码便于排查
        spec = ERROR_CODES["internal_error"]
        code = "internal_error"
    envelope: Dict[str, Any] = {
        "status": spec["status"],
        "reasonCode": code,
        "error_code": code,
        "error": message or spec["hint"],
        "hint": spec["hint"],
        "retryable": bool(spec.get("retryable", False)),
        "http_status": int(spec.get("http", 500)),
    }
    legacy = spec.get("legacy_status")
    if legacy and legacy != spec["status"]:
        envelope["legacyStatus"] = legacy
    if requested_code != code:
        envelope["requested_code"] = requested_code
    envelope.update(extra)
    return envelope


def normalize_error(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """给既有错误响应补齐 status（规范值）/ reasonCode / error_code / hint（幂等）。

    只在 payload 看起来是错误响应（有 error、有 error_code，或 status 命中历史错误词表）时生效。
    执行生命周期状态（success / busy / offline / completed …）保持原值不改写。
    """
    if not isinstance(payload, dict):
        return payload
    status = payload.get("status")
    raw_status = status if isinstance(status, str) else ""
    is_error = bool(payload.get("error")) or raw_status in _STATUS_ALIASES
    if not is_error and not payload.get("error_code"):
        return payload
    code = payload.get("error_code")
    if not code:
        code = _STATUS_ALIASES.get(raw_status, "internal_error")
    spec = ERROR_CODES.get(code, ERROR_CODES["internal_error"])
    canonical = spec["status"]
    # 状态归一：执行生命周期状态不改写；其余非规范状态改写为规范值并保留旧值。
    if raw_status not in _LIFECYCLE_STATUSES and not is_canonical_status(raw_status):
        if raw_status:
            payload.setdefault("legacyStatus", raw_status)
        payload["status"] = canonical
    else:
        payload.setdefault("status", canonical)
    payload.setdefault("reasonCode", code)
    payload["error_code"] = code
    payload.setdefault("hint", spec["hint"])
    payload.setdefault("retryable", bool(spec.get("retryable", False)))
    payload.setdefault("http_status", int(spec.get("http", 500)))
    return payload


def describe_codes() -> Dict[str, Any]:
    """给端点/文档用的错误码表快照。"""
    return {
        "version": "1.1.0",
        "canonical_statuses": list(CANONICAL_STATUSES),
        "codes": {k: dict(v) for k, v in ERROR_CODES.items()},
    }
