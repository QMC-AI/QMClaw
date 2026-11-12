"""dsl.helpers - 画布配置里的旧式 `{{x}}` 与 IR 的 `$x` 之间的转换。

单独成一个模块，是为了让 `compile.py` 与画布编译器共用同一套规则，
不出现"这边转、那边不转"的分裂。
"""

import difflib
import re
from typing import Any, Iterable, Optional

SIMPLE_LEGACY_RE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
INNER_LEGACY_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


# 作者常写的同义/缩写字段 → 规范字段名（`hint` 先查这张表）。
#
# 为什么不能只靠 difflib：`retry` → `retries` 差 3 个字符（y→ie + s），
# 而 `retryDelay` 字面更像"共享前缀"，最近邻会把作者引向**另一套机制**
# （`onError=retry` 的退避），而不是他真正想写的 `retries`。
# 实测证据：实验二里模型写 `retry 2`，拿到 "是不是 'retryDelay'？" 之后
# 连续 5 轮都没改成 `retries 2`——提示指错方向比没提示更糟。
FIELD_ALIASES = {
    "retry": "retries",
    "retrys": "retries",
    "retrycount": "retries",
    "retries_count": "retries",
    "maxretries": "retries",
    "max_retries": "retries",
    "nretries": "retries",
    "retry_count": "retries",
}


def suggest(word: str, candidates: Iterable[str], cutoff: float = 0.6,
            limit: int = 1) -> Optional[str]:
    """拼错时给一个候选（`mesure` → `measure`）。

    只做"最近邻"，不做语义猜测；找不到足够接近的就返回 None，
    免得给出误导性的建议反而增加认知负担。

    顺序：先查 `FIELD_ALIASES`（同义/缩写），再试"前缀延伸"（取最短），
    最后才交给 difflib。
    """
    if not word:
        return None
    pool = [c for c in candidates if c]
    word = str(word)
    alias = FIELD_ALIASES.get(word)
    if alias and alias in pool:
        return alias
    extends = sorted((c for c in pool if c.startswith(word) and c != word), key=len)
    if extends:
        return extends[0]
    matches = difflib.get_close_matches(word, pool, n=limit, cutoff=cutoff)
    return matches[0] if matches else None


def hint(word: str, candidates: Iterable[str], cutoff: float = 0.6) -> str:
    """生成一段"（是不是 'x'？）"的后缀，没有候选就返回空串。"""
    found = suggest(word, candidates, cutoff)
    return f"（是不是 {found!r}？）" if found else ""


def to_ref(value: Any) -> Any:
    """`{{c1}}` → `$c1`；`{{c1.dataset}}` → `$c1.dataset`；`{{nodes.c1.x}}` 保持原样。

    `{{nodes.c1.contrast}}` 这种写法依赖画布的渲染上下文，IR 里没有对应语义，
    保留原样交给 `_resolve_vars` 的兼容路径处理（并在 validate 里报 E_LEGACY_INTERP）。
    """
    if isinstance(value, str):
        whole = SIMPLE_LEGACY_RE.match(value)
        if whole:
            return "$" + whole.group(1)

        def repl(match):
            path = match.group(1)
            if path.startswith("nodes."):
                return match.group(0)
            return "$" + path

        return INNER_LEGACY_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: to_ref(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_ref(v) for v in value]
    return value


def legacy_count(value: Any) -> int:
    if isinstance(value, str):
        return len(re.findall(r"\{\{[^{}]+\}\}", value))
    if isinstance(value, dict):
        return sum(legacy_count(v) for v in value.values())
    if isinstance(value, list):
        return sum(legacy_count(v) for v in value)
    return 0
