"""结果数量注册表（IR v2 预留，见 `docs/BACKEND_ADAPTERS.md`）。

三条约定：

1. **键名跨后端固定**：`T1` 永远叫 `T1`，不出现 `T1_us` / `T1_s`。
2. **单位放在数量里**：`{"value": 62.3, "unit": "us"}`；注册表只声明"这个键属于哪个量纲、
   规范单位是什么"，比较前由 `coerce()` 换算到规范单位。
3. **厂商键不进注册表**：`f10_ghz`、`contrast_pct` 这类带单位后缀的旧键只在 v1 兼容层里
   存在，v2 一律走 `quantities`。

v1 的 IR 仍然用扁平指标（`$a1.T1_us`），本模块目前只服务于"预留接口"与未来的适配器；
把 v1 指标自动映射到 v2 数量的逻辑放在 `legacy_to_quantity()`，等 S8 真正切换时再接线。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# name -> (dimension, canonical unit)
QUANTITIES: Dict[str, Tuple[str, str]] = {
    # 时间
    "T1": ("time", "us"),
    "T2": ("time", "us"),
    "T2star": ("time", "us"),
    "T2echo": ("time", "us"),
    "pi_len": ("time", "ns"),
    "delay": ("time", "us"),
    # 频率
    "f10": ("freq", "GHz"),
    "fread": ("freq", "GHz"),
    "fringe": ("freq", "MHz"),
    # 比例 / 无量纲
    "contrast": ("ratio", ""),
    "snr": ("ratio", ""),
    "fidelity": ("ratio", ""),
    "separation": ("ratio", ""),
    "r_squared": ("ratio", ""),
    "pi_amp": ("ratio", ""),
    "alpha": ("ratio", ""),
    # 电压 / 功率
    "zpa": ("voltage", "V"),
    "power": ("power", "dBm"),
}

# dimension -> {unit: factor to canonical}
UNIT_FACTORS: Dict[str, Dict[str, float]] = {
    "time": {"ns": 1e-3, "us": 1.0, "ms": 1e3, "s": 1e6},
    "freq": {"Hz": 1e-9, "kHz": 1e-6, "MHz": 1e-3, "GHz": 1.0},
    "voltage": {"uV": 1e-6, "mV": 1e-3, "V": 1.0},
    "power": {"dBm": 1.0},
    "ratio": {"": 1.0, "pct": 0.01},
}

# v1 扁平指标 → (v2 数量名, 单位)。只用于迁移期提示，不参与 v1 校验。
LEGACY_METRICS: Dict[str, Tuple[str, str]] = {
    "T1_us": ("T1", "us"),
    "T2_us": ("T2", "us"),
    "T2star_us": ("T2star", "us"),
    "T2echo_us": ("T2echo", "us"),
    "f10_ghz": ("f10", "GHz"),
    "fread_ghz": ("fread", "GHz"),
    "fringe_mhz": ("fringe", "MHz"),
    "R_squared": ("r_squared", ""),
    "pi_amp": ("pi_amp", ""),
    "contrast": ("contrast", ""),
    "snr": ("snr", ""),
    "fidelity": ("fidelity", ""),
}


def dimension_of(name: str) -> Optional[str]:
    entry = QUANTITIES.get(name)
    return entry[0] if entry else None


def canonical_unit(name: str) -> Optional[str]:
    entry = QUANTITIES.get(name)
    return entry[1] if entry else None


def factor(dimension: str, unit: str) -> float:
    table = UNIT_FACTORS.get(dimension)
    if not table:
        raise ValueError(f"未知量纲: {dimension}")
    if unit not in table:
        raise ValueError(f"量纲 {dimension} 不支持单位 {unit!r}（可用: {', '.join(sorted(table))}）")
    return table[unit]


def coerce(name: str, value: float, unit: str) -> float:
    """把 `value` 从 `unit` 换算到该数量的规范单位。"""
    dim = dimension_of(name)
    if dim is None:
        raise ValueError(f"未注册的数量名: {name}")
    return float(value) * factor(dim, unit)


def same_dimension(a: str, b: str) -> bool:
    dim = dimension_of(a)
    return dim is not None and dim == dimension_of(b)


def normalize(quantity: Dict[str, Any], name: str) -> Dict[str, Any]:
    """校验并归一一个数量字典：`{value, unit}` → `{value(规范单位), unit(规范单位)}`。"""
    if not isinstance(quantity, dict) or "value" not in quantity:
        raise ValueError(f"数量必须是 {{value, unit}} 字典，收到: {quantity!r}")
    unit = quantity.get("unit", canonical_unit(name) or "")
    out = {
        "value": coerce(name, quantity["value"], unit),
        "unit": canonical_unit(name) or unit,
    }
    if "uncertainty" in quantity:
        out["uncertainty"] = coerce(name, quantity["uncertainty"], unit)
    return out


def legacy_to_quantity(metric: str, value: float) -> Optional[Dict[str, Any]]:
    """把 v1 扁平指标转成 v2 数量；不认识的键返回 None。"""
    entry = LEGACY_METRICS.get(metric)
    if entry is None or value is None:
        return None
    name, unit = entry
    return {"value": coerce(name, value, unit), "unit": canonical_unit(name)}


def all_names() -> list:
    return sorted(QUANTITIES)
