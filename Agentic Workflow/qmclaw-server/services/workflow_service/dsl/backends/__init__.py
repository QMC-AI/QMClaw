"""后端适配器注册表。

刻意保持极简：一个 dict + 四个函数。没有插件发现、没有 entry_points、
没有动态 import 字符串——实验室机可能是离线的，出问题时要能一眼看懂。

    from .backends import list_backends, get_adapter
    get_adapter("lqcs").capabilities()
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import (
    BackendAdapter,
    CancelToken,
    CapabilityError,
    RawResult,
    RunRequest,
)
from .lqcs import LqcsAdapter

_REGISTRY: Dict[str, BackendAdapter] = {}


def register(adapter: BackendAdapter) -> BackendAdapter:
    if not isinstance(adapter, BackendAdapter):
        raise TypeError(f"adapter 必须继承 BackendAdapter，收到: {type(adapter).__name__}")
    _REGISTRY[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> BackendAdapter:
    key = (name or "lqcs").strip().lower()
    if key in _REGISTRY:
        return _REGISTRY[key]
    if key == "lqcs":
        return register(LqcsAdapter())
    raise CapabilityError(
        f"未知后端: {name!r}；已注册: {', '.join(sorted(_REGISTRY)) or '(空)'}。"
        " 新增后端请实现 dsl.backends.base.BackendAdapter 并在此注册（见 docs/BACKEND_ADAPTERS.md）"
    )


def list_backends() -> List[str]:
    if "lqcs" not in _REGISTRY:
        register(LqcsAdapter())
    return sorted(_REGISTRY)


def describe(name: Optional[str] = None) -> Any:
    if name:
        return get_adapter(name).describe()
    if "lqcs" not in _REGISTRY:
        register(LqcsAdapter())
    return {key: adapter.describe() for key, adapter in sorted(_REGISTRY.items())}


__all__ = [
    "BackendAdapter", "RunRequest", "RawResult", "CancelToken", "CapabilityError",
    "LqcsAdapter", "register", "get_adapter", "list_backends", "describe",
]
