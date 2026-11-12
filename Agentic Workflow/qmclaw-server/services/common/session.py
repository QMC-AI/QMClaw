"""services/common/session.py — 会话路径的唯一实现（roadmap §3 #2/#3 收敛）

现状（收敛前）：

- `quantum_service/server.py` 的 `_handle_connect` / `_handle_switch_session`
  各自 `json.loads(session_path)` —— 传一个坏字符串就抛 `JSONDecodeError`，
  接口回 500 而不是一条可读的校验错误；
- `analysis_service/server.py` 的 `_handle_switch_session` 同样裸 `json.loads`，
  而 `_handle_connect` **完全忽略**请求里的 session_path；
- "读 `config/session.json` → `['', user] + path`，缺文件回
  `['', 'LQHL', 'test', '20260324']`" 这段逻辑在 `labrad_client.py` 与
  `analysis_service/server.py` 里各有一份**逐字重复**，改一处就会漂。

本模块把这三件事收成一个实现：

- `parse_session_path(value)`：接受列表 / 元组 / JSON 字符串 / `a/b/c`，
  统一校验，返回 `(path, error)`，**永不抛异常**；
- `normalize_session_path(path)` / `session_key(path)` / `same_session(a, b)`；
- `load_session_config(config_path=None)`：读 `config/session.json`，缺失或损坏
  时回默认值并说明来源（`config` / `default`）。

约定：纯标准库；不连 LabRAD、不碰设备；坏输入只回错误串。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

# 与历史行为保持一致：没有配置时的默认会话
DEFAULT_SESSION: List[str] = ["", "LQHL", "test", "20260324"]

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config" / "session.json"


def _clean_parts(parts: Sequence[Any]) -> Tuple[List[str], str]:
    out: List[str] = []
    for index, item in enumerate(parts):
        if not isinstance(item, str):
            return [], f"session_path 第 {index} 段不是字符串：{item!r}"
        text = item.strip()
        if text == "" and index > 0:
            return [], f"session_path 第 {index} 段为空"
        out.append(text)
    if not out:
        return [], "session_path 不能为空数组"
    if all(part == "" for part in out):
        return [], "session_path 至少要有一段非空"
    return out, ""


def parse_session_path(value: Any) -> Tuple[Optional[List[str]], str]:
    """解析请求里的 ``session_path``（列表 / 元组 / JSON 字符串 / ``a/b/c``）。

    返回 ``(path, error)``：

    - ``value`` 为空（``None`` / 空串）：``(None, "")`` —— 调用方按"未指定"处理；
    - 合法：``(list[str], "")``；
    - 非法：``(None, "人读原因")``，调用方应回 ``validation_error`` 而不是抛异常。
    """
    if value is None:
        return None, ""
    if isinstance(value, (list, tuple)):
        parts, error = _clean_parts(value)
        return (parts, "") if not error else (None, error)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, ""
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                return None, f"session_path 不是合法 JSON 数组：{exc}"
            if not isinstance(parsed, (list, tuple)):
                return None, f"session_path 解析后不是数组：{type(parsed).__name__}"
            return parse_session_path(list(parsed))
        # "LQHL/test/2025" 或 "/LQHL/test/2025"
        parts, error = _clean_parts(text.split("/"))
        return (parts, "") if not error else (None, error)
    return None, f"session_path 类型不支持：{type(value).__name__}"


def normalize_session_path(path: Any) -> List[str]:
    """把各种写法折成标准列表；无法解析时返回默认会话（配合 ``parse`` 先校验）。"""
    parsed, error = parse_session_path(path)
    if error or parsed is None:
        return list(DEFAULT_SESSION)
    return parsed


def session_key(path: Any) -> str:
    """稳定的会话标识（用于比较 / 日志），形如 ``/LQHL/test/2025``。"""
    return "/".join(normalize_session_path(path))


def same_session(a: Any, b: Any) -> bool:
    """两个会话写法是否指向同一个会话（坏输入一律判不同，不猜）。"""
    left, lerr = parse_session_path(a)
    right, rerr = parse_session_path(b)
    if lerr or rerr or left is None or right is None:
        return False
    return "/".join(left) == "/".join(right)


def load_session_config(
    config_path: Optional[Any] = None,
) -> Tuple[List[str], str]:
    """读 ``config/session.json``，返回 ``(session_path, source)``。

    ``source`` 为 ``"config"``（读到配置）或 ``"default"``（文件缺失/损坏，
    或配置里的 path 不合法）。**从不抛异常**：连不上设备的锅不该由配置解析背。
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return list(DEFAULT_SESSION), "default"
    if not isinstance(data, dict):
        return list(DEFAULT_SESSION), "default"
    section = data.get("session") or {}
    if not isinstance(section, dict):
        return list(DEFAULT_SESSION), "default"
    user = section.get("user") or "LQHL"
    tail = section.get("path")
    if tail is None:  # 没写 path 才用历史默认尾段；写了空数组视为配置错误
        tail = ["test", "20260324"]
    if not isinstance(user, str):
        return list(DEFAULT_SESSION), "default"
    if not isinstance(tail, (list, tuple)) or not tail:
        return list(DEFAULT_SESSION), "default"
    candidate = ["", user] + [str(part) for part in tail]
    parsed, error = parse_session_path(candidate)
    if error or parsed is None:
        return list(DEFAULT_SESSION), "default"
    return parsed, "config"
