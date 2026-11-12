"""services/common/profile.py — 设备画像 Capability Profile（阶段 3 第一批）

问题（docs/ARCHITECTURE_ROADMAP.md §2.7）：session path、比特名、`xy` DAC 通道、读出频率
现在全硬编码在各个 qwf / 脚本里，换一片芯片就得重写一遍。

画像文件：`config/device_profile.json`
```json
{
  "version": "1.0.0",
  "updated_at": "2026-11-09T00:00:00Z",
  "session": ["", "LQHL", "test", "20251103_LD_SQ"],
  "qubits": {
    "q2ld3": {
      "xy_dac": "DAC_LQ 761", "z_dac": "DAC_LQ 762", "readout_adc": "ADC_LQ 760",
      "f10": 3.6219, "f21": 3.4219, "fread": 6.725531, "fc_read": 6.843,
      "bias_z": -1.0, "pi_gate_amp": 1.229, "pi_gate_alpha": -0.7878
    }
  }
}
```

本模块只做**加载 / 校验 / 合并 / 查询**，不碰设备：
- `load_profile(path=None)`：读画像（默认 `<server>/config/device_profile.json`）；
- `validate_profile(profile)`：返回问题清单（缺字段、类型不对、f10/f21 不一致等）；
- `merge_registry(profile, name, registry)`：把一次 `/qubit/registry` 的返回并进画像；
- `qubit_entry(profile, name)`、`profile_report(profile=None)`：给端口层与端点用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# 画像里每个比特建议具备的字段（缺失只告警，不算致命）
EXPECTED_FIELDS = (
    "xy_dac",
    "z_dac",
    "readout_adc",
    "f10",
    "f21",
    "fread",
    "fc_read",
    "bias_z",
    "pi_gate_amp",
    "pi_gate_alpha",
)

# 数值字段（用于一致性检查）
_NUMERIC_FIELDS = ("f10", "f21", "fread", "fc_read", "bias_z", "pi_gate_amp", "pi_gate_alpha")

# ── 运行期引用（阶段 13 / roadmap §2.7）─────────────────────────────────────
# `.qwf` 里可以写 `$profile.f10` / `$profile.pi_gate_amp`，值来自本画像。
# 键名**与画像字段同名**：多一层改名就多一层"文档与代码不一致"的机会。
PROFILE_ENV_FIELDS = (
    "f10",
    "f21",
    "fread",
    "fc",
    "fc_read",
    "bias_z",
    "pi_gate_amp",
    "pi_gate_alpha",
    "read_out_amp",
    "read_in_power",
    "timing_xy",
    "xy_dac",
    "z_dac",
    "readout_adc",
    "readin_dac",
)

# 元信息键：`$profile.qubit` 告诉流程"这份画像说的是哪颗比特"，
# 其余三个用于审计（流程报告里能看出用的是哪一版画像）。
PROFILE_ENV_META = ("qubit", "version", "updated_at", "source")

# 解析出来的来源标记（`$profile.source`）
PROFILE_ENV_SOURCE = "device_profile"


def _server_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def default_profile_path() -> Path:
    return _server_dir() / "config" / "device_profile.json"


def load_profile(path: Optional[str] = None) -> Dict[str, Any]:
    """读取设备画像；文件缺失或损坏时返回空画像（不抛异常）。"""
    target = Path(path) if path else default_profile_path()
    if not target.exists():
        return {"version": "0.0.0", "session": [], "qubits": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {"version": "0.0.0", "session": [], "qubits": {}}
    if not isinstance(data, dict):
        return {"version": "0.0.0", "session": [], "qubits": {}}
    data.setdefault("version", "0.0.0")
    data.setdefault("session", [])
    data.setdefault("qubits", {})
    return data


def save_profile(profile: Dict[str, Any], path: Optional[str] = None) -> str:
    """写回画像文件，返回实际写入路径。"""
    target = Path(path) if path else default_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def validate_profile(profile: Optional[Dict[str, Any]] = None) -> List[str]:
    """校验画像，返回问题清单（空列表 = 无问题）。"""
    profile = profile if profile is not None else load_profile()
    issues: List[str] = []
    qubits = profile.get("qubits") or {}
    if not qubits:
        issues.append("画像里没有任何比特条目")
    for name, entry in qubits.items():
        if not isinstance(entry, dict):
            issues.append(f"{name}: 条目不是对象")
            continue
        for field in EXPECTED_FIELDS:
            if field not in entry:
                issues.append(f"{name}: 缺字段 {field}")
        for field in _NUMERIC_FIELDS:
            value = entry.get(field)
            if value is not None and not isinstance(value, (int, float)):
                issues.append(f"{name}.{field}: 不是数值（{value!r}）")
        f10 = entry.get("f10")
        f21 = entry.get("f21")
        if isinstance(f10, (int, float)) and isinstance(f21, (int, float)):
            # f10-f21 理论上 ≈ 0.2 GHz，但个别比特实测 0.17（器件差异），容忍 0.06
            if abs((f10 - f21) - 0.2) > 0.06:
                issues.append(f"{name}: f10-f21 = {f10 - f21:.4f}（期望约 0.2）")
    return issues


def merge_registry(
    profile: Dict[str, Any],
    name: str,
    registry: Dict[str, Any],
    *,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """把一次 `/qubit/registry` 的返回并进画像。

    默认 `overwrite=False`：只补缺失的键，不覆盖已有值（不臆造字段）。
    `overwrite=True`：用设备当前值覆盖画像里的旧值——用于"画像自动刷新"，
    否则历史画像里的旧 f10/fread 会一直盖住设备当前状态。
    """
    qubits = profile.setdefault("qubits", {})
    entry = dict(qubits.get(name) or {})
    reg = registry.get("registry") if isinstance(registry, dict) else None
    reg = reg if isinstance(reg, dict) else (registry if isinstance(registry, dict) else {})

    def _put(field: str, value: Any) -> None:
        if value is None:
            return
        if overwrite:
            entry[field] = value
        else:
            entry.setdefault(field, value)

    def _get(*keys):
        for key in keys:
            if key in reg:
                return reg[key]
        return None

    channels = reg.get("channels") or {}
    xy = channels.get("xy") if isinstance(channels, dict) else None
    z = channels.get("z") if isinstance(channels, dict) else None
    readout_ch = channels.get("readout") if isinstance(channels, dict) else None
    readin_ch = channels.get("readin") if isinstance(channels, dict) else None
    if isinstance(xy, list) and xy:
        _put("xy_dac", " ".join(str(x) for x in xy))
    if isinstance(z, list) and z:
        _put("z_dac", " ".join(str(x) for x in z))
    if isinstance(readout_ch, list) and readout_ch:
        _put("readout_adc", " ".join(str(x) for x in readout_ch))
    if isinstance(readin_ch, list) and readin_ch:
        _put("readin_dac", " ".join(str(x) for x in readin_ch))

    for key, field in (
        ("f10", "f10"),
        ("f21", "f21"),
        ("fread", "fread"),
        ("fc_read", "fc_read"),
        ("bias_z", "bias_z"),
        ("fc", "fc"),
    ):
        _put(field, _get(key))

    pi_gate = reg.get("PiGate") if isinstance(reg.get("PiGate"), dict) else {}
    if pi_gate.get("amp") is not None:
        _put("pi_gate_amp", pi_gate["amp"])
    if pi_gate.get("alpha") is not None:
        _put("pi_gate_alpha", pi_gate["alpha"])

    read_in = reg.get("ReadIn") if isinstance(reg.get("ReadIn"), dict) else {}
    if read_in.get("power") is not None:
        _put("read_in_power", read_in["power"])
    read_out = reg.get("ReadOut") if isinstance(reg.get("ReadOut"), dict) else {}
    if read_out.get("amp") is not None:
        _put("read_out_amp", read_out["amp"])

    timing = reg.get("timing") if isinstance(reg.get("timing"), dict) else {}
    if timing.get("xy") is not None:
        _put("timing_xy", timing["xy"])

    qubits[name] = entry
    return profile


def qubit_entry(profile: Optional[Dict[str, Any]], name: str) -> Dict[str, Any]:
    """取某个比特的画像条目（不存在返回空 dict）。"""
    profile = profile if profile is not None else load_profile()
    entry = (profile.get("qubits") or {}).get(name)
    return dict(entry) if isinstance(entry, dict) else {}


def profile_env_fields() -> List[str]:
    """`$profile.<key>` 允许出现的全部键（元信息在前，画像字段在后）。"""
    return list(PROFILE_ENV_META) + list(PROFILE_ENV_FIELDS)


def profile_env(qubit: Optional[str], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把某个比特的画像条目折成可在 DSL 里引用的扁平字典。

    返回值直接喂给执行器的 env（`$profile.f10`）；**只放真有的字段**，
    不补默认值 —— 画像缺 f10 时宁可让 `$profile.f10` 报"不在画像里"，
    也不要拿 0.0 去扫一段无意义的频率。

    画像缺失、比特不在画像里、字段不是基础类型 → 返回空 dict（绝不抛异常）。
    调用方（执行器）据此给一条 warning，流程本身仍可运行。
    """
    name = (qubit or "").strip() if isinstance(qubit, str) else ""
    if not name:
        return {}
    profile = profile if isinstance(profile, dict) else load_profile()
    entry = qubit_entry(profile, name)
    if not entry:
        return {}
    env: Dict[str, Any] = {"qubit": name, "source": PROFILE_ENV_SOURCE}
    for key in ("version", "updated_at"):
        value = profile.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            env[key] = value
    for field in PROFILE_ENV_FIELDS:
        value = entry.get(field)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float, str)):
            env[field] = value
    return env


def profile_report(profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """给端点用的画像摘要（含校验问题清单）。"""
    profile = profile if profile is not None else load_profile()
    qubits = profile.get("qubits") or {}
    return {
        "version": profile.get("version", "0.0.0"),
        "updated_at": profile.get("updated_at"),
        "session": profile.get("session", []),
        "path": str(default_profile_path()),
        "qubit_count": len(qubits),
        "qubits": sorted(qubits.keys()),
        "issues": validate_profile(profile),
        "complete": len(qubits) > 0,
    }
