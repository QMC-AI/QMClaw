#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase9_check.py — 阶段 9：registry 读取收敛（/qubit/params 是 registry 的投影）。

对应 docs/ARCHITECTURE_ROADMAP.md §3 #2 剩下的"registry vs `/qubits/:name/params`
各有一套读写路径"：

  - 收敛前：`/qubit/registry` 用 `registry_wrapper2` 摊平全树；
    `/qubit/params` 另写一套 25 行的 `getattr`/`float()` 投影，两套读法会漂
    （例如 `f10` 两边都读，但 registry 才是厂商实验真正改的那份）；
  - 收敛后：`_read_registry_tree` 是唯一读路径，`/qubit/params` 只做**键集投影**，
    registry 打不开才退回 qubit 对象并标注 `params_source=qubit_object`。

**为什么不直接把全树吐给 `/qubit/params`**：前端 `QubitParamsPanel` 会把整个
`params` 原样 `parseFloat` 后回写 —— 多出来的 `channels.*`（对象）、
`correction.settling_amp`（数组）都会变成 `NaN` 写进 registry。键集必须钉死。

离线自检，不连 LabRAD：
  1. PARAM_KEYS 完整性（无重复、覆盖历史 28 键、与老实现一致）
  2. `_legacy_param_value` 的历史形状（数值 → float；字符串/数组/None → None）
  3. `_params_from_qubit_object` 的退路投影
  4. 源码级：两个端点共用 `_read_registry_tree`，params 不再自己 getattr 一遍

用法：D:/anaconda3/envs/lqcs/python.exe _phase9_check.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
WARN = 0
FAILED: list[str] = []

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def read(rel: str) -> str:
    with open(os.path.join(SERVER, rel), "r", encoding="utf-8") as fh:
        return fh.read()


print("=" * 72)
print("_phase9_check — 阶段 9：registry 读取收敛")
print("=" * 72)

from services.quantum_service.server import QuantumService  # noqa: E402

# ── 1. 键集 ───────────────────────────────────────────────────────────────
KEYS = QuantumService.PARAM_KEYS
check("PARAM_KEYS: 无重复", len(KEYS) == len(set(KEYS)))
check("PARAM_KEYS: 28 个历史键", len(KEYS) == 28, f"got={len(KEYS)}")
for key in (
    "f10",
    "fread",
    "fc",
    "f21",
    "bias_z",
    "PiGate.amp",
    "PiHalf.length",
    "ReadIn.ring_power",
    "ReadOut.window_type",
    "discriminator.center0",
    "discriminator.threshold",
):
    check(f"PARAM_KEYS: 含 {key}", key in KEYS)
check("PARAM_KEYS: 不含 timing.*（会写坏前端）", not any(k.startswith("timing.") for k in KEYS))
check("PARAM_KEYS: 不含 channels.*", not any(k.startswith("channels.") for k in KEYS))

# ── 2. 历史形状 ───────────────────────────────────────────────────────────
lv = QuantumService._legacy_param_value
check("value: 数字 → float", lv(3) == 3.0 and isinstance(lv(3), float))
check("value: numpy 风格浮点也能转", lv(1.25) == 1.25)
check("value: 字符串 → None（不把 'square' 写回）", lv("square") is None)
check("value: 列表 → None（center0 是数组）", lv([1.0, 2.0]) is None)
check("value: dict → None", lv({"a": 1}) is None)
check("value: bool 不算数值", lv(True) is None)
check("value: None → None", lv(None) is None)


# ── 3. qubit 对象退路 ─────────────────────────────────────────────────────
class _Sub:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _Q:
    f10 = 3.6219
    fread = 6.7255
    fc = 4.08
    f21 = 3.4219
    bias_z = -1.0
    PiGate = _Sub(amp=1.2, length=30, alpha=-0.7, zpa=0.0)
    PiHalf = _Sub(amp=0.6, length=15, alpha=-0.7, zpa=0.0)
    ReadIn = _Sub(power=-30, length=1500, ring_power=-30, ring_length=70, zpa=0.0)
    ReadOut = _Sub(amp=1.0, length=1500, window_type="square")
    discriminator = _Sub(center0=[1.0, 2.0], center1=[3.0, 4.0], threshold=0.5)


proj = QuantumService._params_from_qubit_object(_Q())
check("退路: 键集与 PARAM_KEYS 一致", set(proj) == set(KEYS), f"diff={set(proj) ^ set(KEYS)}")
check("退路: 基本参数", proj["f10"] == 3.6219 and proj["bias_z"] == -1.0)
check("退路: 嵌套参数", proj["PiGate.amp"] == 1.2 and proj["ReadIn.ring_length"] == 70.0)
check("退路: 字符串参数为 None（与老实现一致）", proj["ReadOut.window_type"] is None)
check("退路: 数组参数为 None", proj["discriminator.center0"] is None)

# ── 4. 接线（源码级，防回退）──────────────────────────────────────────────
src = read("services/quantum_service/server.py")
check("server: 有唯一的 _read_registry_tree", src.count("def _read_registry_tree") == 1)
check("server: registry 端点走唯一读路径",
      re.search(r"def _handle_qubit_registry[\s\S]{0,1500}?_read_registry_tree\(", src) is not None)
check("server: params 端点走唯一读路径",
      re.search(r"def _handle_qubit_params[\s\S]{0,2500}?_read_registry_tree\(", src) is not None)
check("server: params 用 PARAM_KEYS 投影", "for key in self.PARAM_KEYS" in src)
check("server: params 标注来源", '"params_source": "registry"' in src and '"params_source": "qubit_object"' in src)
check("server: params 不再自己写 getattr 链",
      not re.search(r"def _handle_qubit_params[\s\S]{0,3000}?get_nested_param\(", src))
check("server: 老投影已收敛成一个函数", src.count("def _params_from_qubit_object") == 1)
_start = src.index("def _read_registry_tree")
_next = src.index("\n    def ", _start + 10)
_body = src[_start:_next]
check("server: registry 读不加锁（由调用方持锁）", "with self._labrad_lock" not in _body)

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
