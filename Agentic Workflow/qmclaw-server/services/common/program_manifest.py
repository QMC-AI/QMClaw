"""services/common/program_manifest.py — 直发执行的"程序清单"（roadmap §2.3 / §3 #8）

正式路径（:3008/workflows/run_ir）有 IR + 授权清单；但直发 ``:3003/execute``
历史上什么都没有 —— 事后只能看一段代码字符串。本模块把代码折成一份**确定性**
（不含时间戳、同码同输出）的清单，随 RunRecord 落盘：

    {program_hash, qubits, functions, calls[], auto_writes[], warnings[], version}

其中每条 ``call`` 用 ``services.common.replay.VENDOR_SEMANTICS`` 补齐
"会不会静默改寄存器""缺哪个必填参数会崩"，于是"这段代码会被厂商改了什么"
在执行前就可回答，而不是等寄存器被悄悄写坏。

约束：纯标准库 + 纯函数；解析不出任何调用时返回空 calls（不猜）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from services.common.replay import VENDOR_SEMANTICS, parse_calls
from services.common.runrecord import program_fingerprint

MANIFEST_VERSION = "1.0"


def build_program_manifest(
    code: Any,
    qubits: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """把一段实验代码折成程序清单（确定性输出，可离线单测）。"""
    text = code if isinstance(code, str) else ("" if code is None else str(code))
    calls_in = parse_calls(text)

    calls: List[Dict[str, Any]] = []
    warnings: List[str] = []
    functions: List[str] = []
    auto_writes: List[str] = []

    for index, call in enumerate(calls_in):
        fn = str(call.get("fn") or "")
        kwargs = call.get("kwargs") or {}
        semantics = VENDOR_SEMANTICS.get(fn)
        if fn and fn not in functions:
            functions.append(fn)

        missing: List[str] = []
        sem_writes: List[str] = []
        if semantics is None:
            warnings.append(f"sq.{fn}: 厂商语义表未覆盖，无法预判副作用")
        else:
            requires = list(semantics.get("requires") or [])
            missing = [name for name in requires if name not in kwargs]
            for name in missing:
                warnings.append(f"sq.{fn}: 缺必填参数 {name}（真机上会报错）")
            for key in semantics.get("auto_writes") or []:
                if key not in auto_writes:
                    auto_writes.append(key)
            sem_writes = list(semantics.get("auto_writes") or [])

        calls.append(
            {
                "index": index,
                "fn": fn,
                "qubit": call.get("qubit"),
                "kwargs": {str(k): str(v) for k, v in kwargs.items()},
                "auto_writes": sem_writes,
                "missing_required": missing,
                "summary": (semantics or {}).get("summary", ""),
                "known": semantics is not None,
            }
        )

    return {
        "version": MANIFEST_VERSION,
        "program_hash": program_fingerprint(text),
        "qubits": [str(q) for q in (qubits or [])],
        "functions": functions,
        "calls": calls,
        "auto_writes": auto_writes,
        "warnings": warnings,
    }


def summarize_manifest(manifest: Optional[Dict[str, Any]], limit: int = 5) -> str:
    """一行摘要，给日志/响应里用；空清单返回空串（不编造）。"""
    if not isinstance(manifest, dict):
        return ""
    functions = manifest.get("functions") or []
    if not functions:
        return ""
    shown = ", ".join(str(f) for f in functions[:limit])
    if len(functions) > limit:
        shown += f" …(+{len(functions) - limit})"
    writes = manifest.get("auto_writes") or []
    suffix = f"; 可能静默回写 {', '.join(str(w) for w in writes)}" if writes else ""
    return f"{shown}{suffix}"
