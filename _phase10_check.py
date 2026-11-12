#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase10_check.py — 阶段 10：真机可用性语义收敛（一份判据）。

对应 docs/ARCHITECTURE_ROADMAP.md §3 #3「真机可用性状态分散：`:3003` stale/stuck
vs `:3004` degraded_permanent」：

  - 收敛前：`:3003` 与 `:3004` 各自手写「连上就 healthy、离线模式算 healthy、
    在线模式没连上才算 degraded」同一段逻辑；
  - 更糟：两个服务各有一份**走不到**的 `_handle_health`（`/health` 在
    `base_service` 的 HTTP 层就被拦下、走 `get_health()`），死代码判据与活的那份
    不一致（analysis 的死版本不认 stuck / offline_provider）；
  - 收敛后：`services/common/availability.py` 是唯一判据，`get_health` 只喂字段，
    `_handle_health` 只做委托，HTTP 码由 `health_status_code()` 统一决定。

离线自检，不连 LabRAD / DataLab：
  1. 纯函数判据矩阵（stuck / 连上 / 离线模式 / 自动降级 / 初始化 / 未连接）
  2. 历史 `status` 与 HTTP 码的映射（老消费者不受影响）
  3. 归一化（Mode 枚举、字符串、None、未知识别）
  4. 源码级：两个服务都走唯一判据、死处理器只剩委托、base 用统一 HTTP 码

用法：D:/anaconda3/envs/lqcs/python.exe _phase10_check.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
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
print("_phase10_check — 阶段 10：真机可用性语义收敛")
print("=" * 72)

from services.common import availability as av  # noqa: E402

# ── 1. 常量与归一化 ───────────────────────────────────────────────────────
check("状态集: 三个取值且顺序固定", av.AVAILABILITY_STATES == ("ok", "degraded", "unavailable"))
check("原因码: 五个失败原因 + ok 为 None", len(av.REASON_CODES) == 5 and av.REASON_OK is None,
      f"got={av.REASON_CODES}")
check("原因码: 每个都有文案", all(av.REASON_MESSAGES.get(r) for r in av.REASON_CODES))

check("归一化: 字符串 online", av.normalize_mode("online") == "online")
check("归一化: 大写/空格也认", av.normalize_mode("  OFFLINE ") == "offline")
check("归一化: None → auto", av.normalize_mode(None) == "auto")
check("归一化: 空串 → auto", av.normalize_mode("") == "auto")
check("归一化: 未知识别 → auto（保守）", av.normalize_mode("bogus") == "auto")


class _FakeMode:
    def __init__(self, value):
        self.value = value


check("归一化: 枚举对象（取 .value）", av.normalize_mode(_FakeMode("offline")) == "offline")
check("归一化: 枚举值未知 → auto", av.normalize_mode(_FakeMode("weird")) == "auto")


# ── 2. 判据矩阵 ───────────────────────────────────────────────────────────
def build(**kw):
    base = dict(device="labrad", device_connected=False, mode="online")
    base.update(kw)
    return av.build_availability(**base)


a = build(device_connected=True)
check("连上 → ok", a["state"] == "ok" and a["reasonCode"] is None)
check("连上 → 文案为正常", a["message"] == "真机链路正常")

a = build(device_connected=True, stuck=True)
check("卡死优先于连上 → degraded", a["state"] == "degraded" and a["reasonCode"] == "stuck")
check("卡死 → stuck 标志为真", a["stuck"] is True)

a = build(mode="offline")
check("离线模式 + 没连 → ok（配置不是故障）",
      a["state"] == "ok" and a["reasonCode"] == "offline_mode")

a = build(mode="auto", offline_available=True)
check("自动模式 + 有离线数据 → ok", a["state"] == "ok" and a["reasonCode"] == "offline_fallback")

a = build(mode="auto", offline_available=False, fallback_state="degraded_permanent")
check("自动模式 + 无兜底 + 永久降级 → unavailable",
      a["state"] == "unavailable" and a["reasonCode"] == "not_connected")

a = build(initializing=True, fallback_state="disconnected")
check("初始化中 → degraded / initializing",
      a["state"] == "degraded" and a["reasonCode"] == "initializing")

a = build()
check("在线模式没连 → unavailable", a["state"] == "unavailable" and a["reasonCode"] == "not_connected")

a = build(device_connected=True, busy=True)
check("在途请求 → ready=False（不算不可用）", a["ready"] is False and a["state"] == "ok")

a = build(session_path=["", "LQHL", "test", "x"])
check("会话路径: 列表原样保留", a["sessionPath"] == ["", "LQHL", "test", "x"])
a = build(session_path="LQHL/test/x")
check("会话路径: 字符串包成列表", a["sessionPath"] == ["LQHL/test/x"])
a = build(session_path=None)
check("会话路径: None 保持 None", a["sessionPath"] is None)

a = build(device_connected=True)
for key in (
    "state",
    "reasonCode",
    "message",
    "device",
    "deviceConnected",
    "mode",
    "fallbackState",
    "sessionPath",
    "busy",
    "ready",
    "offlineAvailable",
    "stuck",
):
    check(f"字段: 含 {key}", key in a)

# ── 3. 历史兼容：status 与 HTTP 码 ────────────────────────────────────────
check("legacy: ok → healthy", av.derive_legacy_status({"state": "ok"}) == "healthy")
check("legacy: degraded → degraded", av.derive_legacy_status({"state": "degraded"}) == "degraded")
check("legacy: unavailable → degraded（老消费者只认两档）",
      av.derive_legacy_status({"state": "unavailable"}) == "degraded")
check("legacy: 坏输入不抛", av.derive_legacy_status(None) == "degraded")

check("HTTP: ok → 200", av.health_status_code({"availability": {"state": "ok"}}) == 200)
check("HTTP: degraded → 503", av.health_status_code({"availability": {"state": "degraded"}}) == 503)
check("HTTP: unavailable → 503", av.health_status_code({"availability": {"state": "unavailable"}}) == 503)
check("HTTP: 无 availability 退回 status",
      av.health_status_code({"status": "healthy"}) == 200
      and av.health_status_code({"status": "degraded"}) == 503)
check("HTTP: 空 payload → 503", av.health_status_code({}) == 503)

desc = av.describe_availability()
check("说明表: 版本与状态集", desc["version"] == "1.0" and desc["states"] == list(av.AVAILABILITY_STATES))
check("说明表: 覆盖所有原因码（含 ok）",
      set(desc["reasons"]) == set(av.REASON_CODES) | {"ok"})
check("说明表: 每个原因都落在合法状态里",
      all(v["state"] in av.AVAILABILITY_STATES for v in desc["reasons"].values()))
check("说明表: 确定输出（两次相等）", desc == av.describe_availability())

# ── 4. 接线（源码级，防回退）──────────────────────────────────────────────
q = read("services/quantum_service/server.py")
an = read("services/analysis_service/server.py")
base = read("services/base/base_service.py")

for name, src in (("quantum", q), ("analysis", an)):
    check(f"{name}: 引入唯一判据",
          "from ..common.availability import build_availability, derive_legacy_status" in src)
    check(f"{name}: get_health 返回 availability", '"availability": availability' in src)
    check(f"{name}: status 由 derive_legacy_status 推出",
          '"status": derive_legacy_status(availability),' in src)
    check(f"{name}: 不再手写 is_healthy 判据", "is_healthy = " not in src)
    check(f"{name}: 不再有裸 healthy/degraded 三元式",
          '"healthy" if ' not in src)

check("quantum: 死处理器只剩委托", "def _handle_health(self) -> Dict[str, Any]:" in q
      and q.count("return self.get_health()") >= 1)
check("analysis: 死处理器只剩委托", "def _handle_health(self) -> Dict[str, Any]:" in an
      and an.count("return self.get_health()") >= 1)
check("base: HTTP 码走统一函数", "health_status_code(health)" in base)
check("base: 有缺模块兜底", "except Exception:  # noqa: BLE001 - 老服务/离线包缺模块时不影响健康检查" in base)

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
