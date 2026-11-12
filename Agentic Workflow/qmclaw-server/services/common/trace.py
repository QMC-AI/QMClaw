"""services/common/trace.py — traceId 贯穿全链（阶段 3 第一批）

目标（docs/ARCHITECTURE_ROADMAP.md §2.7）：一条 `traceId` 贯穿
Express → workflow → task_queue → quantum → LabRAD，
让"前端下发了什么 → 服务端执行了什么 → 产出了哪个数据集"可以按一条 id 串起来。

设计约束：
- 纯函数、无外部依赖，可离线单测；
- 不强制：调用方没带 trace 时自动生成一个（返回给调用方，便于事后检索）；
- 透传优先：请求头 `X-Trace-Id` > 请求体 `trace_id` > 新生成。

用法：
    from services.common.trace import ensure_trace, trace_headers
    trace_id, is_new = ensure_trace(data, headers=None)
    response["trace_id"] = trace_id
    # 下游请求带上：headers=trace_headers(trace_id)
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Mapping, Optional, Tuple

TRACE_HEADER = "X-Trace-Id"
TRACE_FIELD = "trace_id"

# traceId 只允许易读字符，长度受限（便于日志 grep）
_MAX_LEN = 64


def new_trace_id(prefix: str = "t") -> str:
    """生成一个新的 traceId，形如 `t-9f3c1a2b4d5e`。"""
    safe = "".join(ch for ch in str(prefix) if ch.isalnum() or ch in "-_")[:8] or "t"
    return f"{safe}-{uuid.uuid4().hex[:12]}"


def normalize_trace_id(value: Any) -> Optional[str]:
    """清洗外部传入的 traceId；不合法返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = "".join(ch for ch in text if ch.isalnum() or ch in "-_.:").strip("-_.:")
    if not text:
        return None
    return text[:_MAX_LEN]


def ensure_trace(
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
    *,
    prefix: str = "t",
) -> Tuple[str, bool]:
    """取出（或生成）本次请求的 traceId。

    Args:
        data: 请求体（可能含 `trace_id`）。
        headers: 请求头（可能含 `X-Trace-Id`），大小写不敏感。
        prefix: 新生成时的前缀。

    Returns:
        `(trace_id, is_new)`；`is_new=True` 表示本次是新生成的（调用方上游没带）。
    """
    data = data or {}
    candidates = []
    if headers:
        for key, value in headers.items():
            if str(key).lower() == TRACE_HEADER.lower():
                candidates.append(value)
                break
    candidates.append(data.get(TRACE_FIELD))
    candidates.append(data.get("traceId"))

    for candidate in candidates:
        normalized = normalize_trace_id(candidate)
        if normalized:
            return normalized, False
    return new_trace_id(prefix), True


def trace_headers(trace_id: Optional[str]) -> Dict[str, str]:
    """构造下游请求头，把 traceId 传下去。"""
    normalized = normalize_trace_id(trace_id)
    return {TRACE_HEADER: normalized} if normalized else {}


def attach_trace(payload: Optional[Dict[str, Any]], trace_id: Optional[str]) -> Dict[str, Any]:
    """把 traceId 写进响应体（不覆盖已有值）。"""
    result = payload if isinstance(payload, dict) else {"result": payload}
    normalized = normalize_trace_id(trace_id)
    if normalized and TRACE_FIELD not in result:
        result[TRACE_FIELD] = normalized
    return result
