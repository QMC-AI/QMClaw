"""services/common/dataset_ids.py — 数据集编号归属（roadmap §2.3 / §3 #8）

问题：`:3003/execute` 的 stdout 只有少数实验（spectroscopy 类）会打印
``dataset: N``；``sq.iqraw`` / ``sq.pidf`` / ``sq.t1`` 等只把数据写进 DataVault
就返回，事后无法回答"这次执行产出了哪几份数据"。

两条互补来源（本模块只做纯计算，**不碰 LabRAD**）：

1. **stdout 解析**：厂商打印的 ``dataset: N``（兼容历史行为）。
2. **DataVault 目录差集**：执行前后各列一次 session 目录，新增编号即本次产出。
   不需要厂商配合，也不依赖 stdout 格式 —— 这是真正补上缺口的那条。

设计约束：

- 纯标准库，可离线单测 / 可在 CI 跑；
- 任何解析或探测失败都返回空结果 + 诊断字符串，**绝不抛异常**
  （审计是增强，不能打断真机执行）。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple

# 厂商打印格式：`dataset: 536` / `Dataset 536` / `dataset#536`（大小写不敏感）
_DATASET_TEXT_RE = re.compile(r"dataset\s*[:#]?\s*(\d{1,8})", re.IGNORECASE)

# DataVault 文件名格式：`00536 - q2ld3: iqraw`（前导编号 = 数据集号）
_DATASET_FILE_RE = re.compile(r"^\s*0*(\d{1,8})(?=\s|[-_ ]|$|\.)")

MAX_IDS = 50


def parse_dataset_ids(text: Any) -> List[int]:
    """从 stdout/日志文本里抽出厂商打印的数据集编号（去重、保序）。"""
    if not isinstance(text, str) or not text:
        return []
    out: List[int] = []
    for raw in _DATASET_TEXT_RE.findall(text):
        try:
            num = int(raw)
        except (TypeError, ValueError):
            continue
        if num > 0 and num not in out:
            out.append(num)
    return out[:MAX_IDS]


def _as_strings(payload: Any) -> List[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, (list, tuple, set)):
        return [item for item in payload if isinstance(item, str)]
    return []


def iter_dataset_numbers(payload: Any) -> List[int]:
    """从 ``cxn.data_vault.dir(session)`` 的返回值里抽数据集编号（升序去重）。

    兼容三种形状：

    - ``([dirs], [files])`` —— LabRAD DataVault 的标准返回（取两者里的编号名）；
    - 扁平字符串列表 —— 某些包装层只回文件列表；
    - ``None`` / 不可解析对象 —— 返回 ``[]``（调用方据此判定"探测不可用"）。
    """
    candidates: List[str] = []
    if isinstance(payload, (list, tuple)) and len(payload) == 2 and all(
        isinstance(part, (list, tuple)) for part in payload
    ):
        for part in payload:
            candidates.extend(_as_strings(part))
    else:
        candidates.extend(_as_strings(payload))

    out: List[int] = []
    for name in candidates:
        match = _DATASET_FILE_RE.match(name)
        if not match:
            continue
        try:
            num = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if num > 0 and num not in out:
            out.append(num)
    return sorted(out)


def new_dataset_numbers(
    before: Optional[Sequence[int]], after: Optional[Sequence[int]]
) -> List[int]:
    """执行前后目录编号差集 —— 本次执行新增的数据集。

    任一侧未知（``None``，例如探测失败）就返回 ``[]``：宁可少记，
    也不能把"上次遗留的数据集"错记成本次产出。
    """
    if before is None or after is None:
        return []
    before_set = {int(x) for x in before if isinstance(x, (int, float))}
    out = sorted({int(x) for x in after if isinstance(x, (int, float))} - before_set)
    return out[:MAX_IDS]


def merge_dataset_ids(*groups: Optional[Iterable[Any]]) -> List[int]:
    """合并多来源编号（stdout + 目录差集），升序去重、上限 ``MAX_IDS``。"""
    merged = set()
    for group in groups:
        if not group:
            continue
        for item in group:
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                value = int(item)
            elif isinstance(item, str) and item.strip().isdigit():
                value = int(item.strip())
            else:
                continue
            if value > 0:
                merged.add(value)
    return sorted(merged)[:MAX_IDS]


def probe_dataset_numbers(
    get_dir: Callable[..., Any], session: Any = None
) -> Tuple[List[int], str]:
    """安全地列一次 DataVault 目录。

    参数
    ----
    get_dir : 可调用对象，签名为 ``get_dir(session)`` 或 ``get_dir()``
              （服务里传 ``lambda s: cxn.data_vault.dir(s)``；单测里传假的）。
    session : 传给 ``get_dir`` 的 session 路径；为 ``None`` 时按无参调用。

    返回 ``(ids, error)``；失败时 ``ids == []`` 且 ``error`` 为一句人读原因。
    """
    try:
        payload = get_dir() if session is None else get_dir(session)
    except Exception as exc:  # noqa: BLE001 - 探测失败绝不外抛
        return [], f"{type(exc).__name__}: {exc}"[:200]
    if payload is None:
        return [], "目录返回 None（连接或 session 不可用）"
    # 空目录是合法结果（首次执行前还没有数据），不当作错误
    return iter_dataset_numbers(payload), ""


def read_session_dataset_numbers(
    change_dir: Callable[[Any], Any],
    list_dir: Callable[[], Any],
    session: Any = None,
) -> Tuple[List[int], str]:
    """cd 到 session 再列目录，返回 ``(ids, error)``。

    真机实测（2026-11-10）：LabRAD 的 ``data_vault.dir`` **不接受 session 参数**，
    裸连接停在 ``['']`` 根目录，``dir(['', 'LQHL', 'test', ...])`` 静默返回 ``([], [])``。
    于是"目录差集"永远为空，``dataset_ids`` 一直缺号。正确做法与
    ``quantum_service/labrad_client.py`` 连接时一致：先 ``cd('')`` 回根，再
    ``cd(session 去掉前导空段)``，然后无参 ``dir()``。整个过程只读。

    失败（cd 或 dir 抛异常）回 ``([], 原因)``，绝不外抛。
    """
    try:
        change_dir("")
        parts = list(session or [])
        if parts and parts[0] == "":
            parts = parts[1:]
        if parts:
            change_dir(parts)
        payload = list_dir()
    except Exception as exc:  # noqa: BLE001 - 探测失败绝不外抛
        return [], f"{type(exc).__name__}: {exc}"[:200]
    if payload is None:
        return [], "目录返回 None（连接或 session 不可用）"
    return iter_dataset_numbers(payload), ""


def dataset_source_label(stdout_ids: Sequence[int], probed_ids: Sequence[int]) -> str:
    has_stdout = bool(stdout_ids)
    has_probe = bool(probed_ids)
    if has_stdout and has_probe:
        return "stdout+datavault"
    if has_probe:
        return "datavault"
    if has_stdout:
        return "stdout"
    return "unknown"
