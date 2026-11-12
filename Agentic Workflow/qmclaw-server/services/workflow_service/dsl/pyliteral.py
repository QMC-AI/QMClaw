"""把配置值渲染成合法的 Python 字面量（含嵌套结构）。

从 `server.py` 抽出来，供 lqcs 适配器与画布路径共用，避免两份实现漂移。

除标量/容器外，额外支持一种**表达式形式**：``{"$arange": [start, stop, step]}``
→ ``__import__("numpy").arange(start, stop, step)``。官方标定流程几乎每步都要传
扫描区间（``freq=np.arange(6.5, 7.1, 0.001)``），而 JSON 里没有数组构造器。
"""
from __future__ import annotations

from typing import Any


def py_literal(value: Any) -> str:
    if isinstance(value, dict) and set(value) == {"$arange"}:
        nums = value["$arange"]
        if not isinstance(nums, (list, tuple)) or len(nums) not in (2, 3):
            raise ValueError("$arange takes [start, stop] or [start, stop, step]")
        parts = ", ".join(py_literal(n) for n in nums)
        return f'__import__("numpy").arange({parts})'
    if isinstance(value, (str, int, float, bool)) or value is None:
        return repr(value)
    if isinstance(value, (dict, list, tuple)):
        # repr 对 dict/list/tuple 生成的是 Python 字面量，True/False/None 都能正确表达
        return repr(value)
    raise ValueError(f"unsupported parameter type: {type(value).__name__}")
