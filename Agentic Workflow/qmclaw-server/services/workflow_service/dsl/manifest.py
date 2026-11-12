"""写清单（write manifest）：把一条 IR 展开成"这次会改哪些寄存器键、改几次、改多少"。

为什么要有这个模块（见 `docs/DSL_DESIGN.md` §13）：

授权的本质是回答"这次写操作的后果由谁承担"。**程序承担不了后果，只有动作能。**
厂商实验会在 IR 看不见的地方静默改寄存器（`SpectroscopyAuto` 把 f10 挪 +455 MHz、
`TimingXYZ` 把 timing.xy 从 72 ps 改成 826 ns → 比特直接报废），
所以"授权一份程序"是不可执行的，"授权一组动作"才可执行。

本模块给出三样东西：

1. **写集合**（哪些键会被写）——静态枚举，来自 `backends/lqcs.py:WRITE_EFFECTS`；
2. **次数上界**（每个键最多被写几次）——repeat 展开 × if/else 取并集；
3. **幅度上界**（每个键最多被改多少）——来源优先级：
   IR 顶层 `bounds` 显式声明 > `config/registry_bounds.json` 默认表 > 无。
   没有上界的键 ⇒ **不可代理放行**（E_MANIFEST_UNBOUNDED），必须人直批。

两类特殊键：

* **derived**（`discriminator.*`）：厂商代码按刚采到的数据重算，不是策略性写入，
  不需要人批，但要记在清单里（它本身就是"对比度会漂"的一个来源）。
* **neverDelegable**（`timing.*`，用户 2026-11-05 定）：它决定 π 脉冲落在读出窗口的
  哪一格，错了不会报警，只会让比特静默变哑 ⇒ 任何情况下都要人直批。

未知实验函数（WRITE_EFFECTS 里没有）、`apply`（任意参数写入）、`call`（另一个工作流）
⇒ 写集合无法静态枚举 ⇒ 同样报 E_MANIFEST_UNBOUNDED，不允许"看起来没事"蒙混过关。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .const import OP_APPLY, OP_CALL, OP_IF, OP_MEASURE, OP_REPEAT

# 用户 2026-11-05 定：timing.* 永不可代理（见 docs/DSL_DESIGN.md §13.4）
NEVER_DELEGABLE_PREFIXES: Tuple[str, ...] = ("timing.",)

# 厂商按数据重算的键：不需要人批，但要进清单
DERIVED_PREFIXES: Tuple[str, ...] = ("discriminator.",)

# 顶层 IR 字段：显式幅度上界 {"f10": 0.05, "PiGate.amp": 0.2}
BOUNDS_FIELD = "bounds"

# 默认表的位置：<server>/config/registry_bounds.json
_CONFIG_BOUNDS_RELPATH = os.path.join("config", "registry_bounds.json")


def _server_root() -> str:
    # dsl/manifest.py → workflow_service → services → <server>
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def load_config_bounds(root: Optional[str] = None) -> Dict[str, float]:
    """读 `config/registry_bounds.json` 默认表；读不到就当空表（= 全部无界）。"""
    base = root or _server_root()
    path = os.path.join(base, _CONFIG_BOUNDS_RELPATH)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    out: Dict[str, float] = {}
    if isinstance(raw, dict):
        source = raw.get("bounds") if isinstance(raw.get("bounds"), dict) else raw
        for key, val in (source or {}).items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                out[str(key)] = float(val)
    return out


def _never_delegable(key: str) -> bool:
    return any(str(key).startswith(pfx) for pfx in NEVER_DELEGABLE_PREFIXES)


def _derived(key: str) -> bool:
    return any(str(key).startswith(pfx) for pfx in DERIVED_PREFIXES)


def _iter_with_rounds(stmts: Iterable[Dict[str, Any]], mult: int = 1):
    """展开 repeat（body × maxRounds），if/else 两支都算（上界）。

    与 `plan.py` 的同名函数同口径；这里单独一份是为了不引入 plan ↔ manifest 的循环依赖。
    """
    for st in stmts or []:
        if not isinstance(st, dict):
            continue
        op = st.get("op")
        if op == OP_REPEAT:
            try:
                rounds = int(st.get("maxRounds") or 1)
            except (TypeError, ValueError):
                rounds = 1
            yield from _iter_with_rounds(st.get("body") or [], mult * max(rounds, 1))
        elif op == OP_IF:
            yield from _iter_with_rounds(st.get("then") or [], mult)
            yield from _iter_with_rounds(st.get("else") or [], mult)
        else:
            yield st, mult


def _measure_params(st: Dict[str, Any]) -> Dict[str, Any]:
    params = st.get("params")
    return params if isinstance(params, dict) else {}


def collect_manifest(ir: Dict[str, Any], adapter: Any = None) -> Dict[str, Any]:
    """枚举一条 IR 的写集合。

    返回：
        {
          "qubits": [...],          # 涉及的比特
          "rounds": int,           # 最大循环轮数
          "writes": [              # 每个被写的键
             {"key": "f10", "count": 3, "max_count": 3, "fns": ["sq.pidf"],
              "qubits": ["q4ld3"], "bound": 0.05, "bound_source": "ir"|"config"|"derived"|None,
              "delegable": True, "never_delegable": False, "derived": False}
          ],
          "total_writes": int,     # 键次数之和
          "unbounded": [...],      # 没有幅度上界的键
          "never_delegable": [...],
          "derived": [...],
          "unknown_fns": [...],    # WRITE_EFFECTS 里没有的函数（写集合不可枚举）
          "dynamic": bool,         # 是否存在写集合不可枚举的情况
        }
    """
    ir = ir if isinstance(ir, dict) else {}
    try:
        from .backends.lqcs import write_effects  # 局部导入：避免与 backends 包循环
    except Exception:  # noqa: BLE001 — 后端不可用时只报"不可枚举"
        write_effects = None

    ir_bounds = ir.get(BOUNDS_FIELD)
    cfg_bounds = load_config_bounds()

    per_key: Dict[str, Dict[str, Any]] = {}
    qubits: List[str] = []
    unknown_fns: List[str] = []
    rounds = 1

    for st, mult in _iter_with_rounds(ir.get("pipeline") or []):
        op = st.get("op")
        if op == OP_APPLY:
            # adjust_params：任意参数写入，写集合不可静态枚举
            label = "apply"
            if label not in unknown_fns:
                unknown_fns.append(label)
            continue
        if op == OP_CALL:
            label = "call"
            if label not in unknown_fns:
                unknown_fns.append(label)
            continue
        if op != OP_MEASURE:
            continue
        fn = str(st.get("fn") or "")
        qubit = st.get("qubit")
        if isinstance(qubit, str) and qubit and qubit not in qubits:
            qubits.append(qubit)
        if mult > rounds:
            rounds = mult
        if write_effects is None:
            if fn and fn not in unknown_fns:
                unknown_fns.append(fn)
            continue
        eff = write_effects(fn, _measure_params(st))
        if eff.get("mode") == "unknown":
            if fn and fn not in unknown_fns:
                unknown_fns.append(fn)
            continue
        if not eff.get("effective"):
            continue
        for key in eff.get("writes") or []:
            rec = per_key.setdefault(key, {
                "key": key, "count": 0, "max_count": 0, "fns": [],
                "qubits": [], "bound": None, "bound_source": None,
                "delegable": False, "never_delegable": _never_delegable(key),
                "derived": _derived(key), "reason": eff.get("reason") or "",
            })
            rec["count"] += 1
            rec["max_count"] += mult
            if fn and fn not in rec["fns"]:
                rec["fns"].append(fn)
            if isinstance(qubit, str) and qubit and qubit not in rec["qubits"]:
                rec["qubits"].append(qubit)

    writes: List[Dict[str, Any]] = []
    unbounded: List[str] = []
    never: List[str] = []
    derived_keys: List[str] = []
    for key in sorted(per_key):
        rec = per_key[key]
        bound = None
        source = None
        if isinstance(ir_bounds, dict) and key in ir_bounds:
            val = ir_bounds[key]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                bound, source = float(val), "ir"
        if bound is None and key in cfg_bounds:
            bound, source = cfg_bounds[key], "config"
        rec["bound"] = bound
        rec["bound_source"] = source
        rec["never_delegable"] = _never_delegable(key)
        rec["derived"] = _derived(key)
        rec["delegable"] = bool(
            (bound is not None or rec["derived"]) and not rec["never_delegable"]
        )
        if rec["never_delegable"]:
            never.append(key)
        elif rec["derived"]:
            derived_keys.append(key)
        elif bound is None:
            unbounded.append(key)
        writes.append(rec)

    dynamic = bool(unknown_fns)
    if dynamic:
        # 写集合不可枚举 ⇒ 无法给这些键封顶
        unbounded = sorted(set(unbounded) | set(unknown_fns))

    return {
        "qubits": qubits,
        "rounds": rounds,
        "writes": writes,
        "total_writes": sum(r["count"] for r in writes),
        "unbounded": unbounded,
        "never_delegable": never,
        "derived": derived_keys,
        "unknown_fns": unknown_fns,
        "dynamic": dynamic,
    }


def manifest_errors(ir: Dict[str, Any], adapter: Any = None) -> List[Tuple[str, str, str]]:
    """返回 (code, message, path) 三元组列表（供 validate.py 转成 diag）。"""
    man = collect_manifest(ir, adapter=adapter)
    out: List[Tuple[str, str, str]] = []
    if man["unknown_fns"]:
        out.append((
            "E_MANIFEST_UNBOUNDED",
            f"写集合不可静态枚举：{man['unknown_fns']}；"
            f"授权必须落到动作上（未知实验先补进 dsl/backends/lqcs.py:WRITE_EFFECTS，"
            f"apply/call 需展开成具体 measure）",
            "pipeline",
        ))
    if man["unbounded"]:
        out.append((
            "E_MANIFEST_UNBOUNDED",
            f"这些键没有幅度上界（不可代理放行，必须人直批）：{man['unbounded']}；"
            f"补 IR 顶层 bounds 块或 config/registry_bounds.json",
            "bounds",
        ))
    if man["never_delegable"]:
        out.append((
            "E_MANIFEST_UNBOUNDED",
            f"这些键属于 neverDelegable（不可代理）：{man['never_delegable']}",
            "pipeline",
        ))
    return out


def manifest_summary(man: Dict[str, Any]) -> str:
    """人读的一行摘要（写进 run_ir 的日志与票据）。"""
    if not man["writes"]:
        return "写集合：空（不写任何寄存器）"
    parts = []
    for rec in man["writes"]:
        if rec["bound"] is None:
            bound = "无界"
        elif rec["derived"]:
            bound = "派生"
        else:
            bound = f"±{rec['bound']}"
        parts.append(f"{rec['key']}×{rec['max_count']}({bound})")
    return f"写集合：{', '.join(parts)}"
