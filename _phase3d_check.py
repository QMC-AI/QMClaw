#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase3d_check.py — 阶段 3 第二批③：回放层（厂商语义仿真 + fixture 录制/回放）。

离线自检，不连 LabRAD、不执行真机实验：
  1. services/common/replay.py 存在且导出语义表
  2. 语义表覆盖关键厂商函数，且每个条目字段完整
  3. parse_calls 能从实验代码里抠出调用序列
  4. simulate：spectroscopy 缺 freq → validation_error + IndexError 提示
  5. simulate：t1 缺 zpa → 提示"二维扫描"
  6. simulate：pidf 会平移 f10；iqraw/s21 会标出自动回写
  7. simulate：未知函数 → warning；无副作用时 diff 为空
  8. ReplayStore：save / load / list 往返
  9. replay_fixture：仿真与真机记录的漂移检测
 10. :3003/qubit/replay 端点四种动作（处理器级调用，不起服务）

用法：D:/anaconda3/envs/lqcs/python.exe _phase3d_check.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
WARN = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def warn(name: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  [WARN] {name} {detail}")


print("=" * 72)
print("_phase3d_check — 阶段 3 第二批③：回放层（厂商语义仿真）")
print("=" * 72)

# ── 1. 模块存在 ───────────────────────────────────────────────────────
replay_path = os.path.join(SERVER, "services", "common", "replay.py")
check("services/common/replay.py 存在", os.path.exists(replay_path), replay_path)

try:
    from services.common import replay as rp
except Exception as exc:  # pragma: no cover
    print(f"  [FAIL] 导入 services.common.replay 失败: {exc}")
    print(f"\n_phase3d_check: PASS={PASS} FAIL={FAIL + 1} WARN={WARN}")
    sys.exit(1)

# ── 2. 语义表覆盖 ─────────────────────────────────────────────────────
sem = rp.VENDOR_SEMANTICS
check("语义表非空", isinstance(sem, dict) and len(sem) > 0)
required_fns = ["spectroscopy", "spectroscopy_auto", "s21", "iqraw", "pidf",
                "piamp", "set_pi", "ramsey_df", "ramsey_t2", "t1", "xeb"]
for fn in required_fns:
    check(f"语义表覆盖 sq.{fn}", fn in sem)
for fn, entry in sem.items():
    for field in ("summary", "auto_writes", "requires", "defaults", "note"):
        if field not in entry:
            check(f"sq.{fn} 语义条目缺字段 {field}", False)
check("语义条目字段齐全", True)
check("KNOWN_SQ_FUNCTIONS 已排序导出", rp.KNOWN_SQ_FUNCTIONS == sorted(sem.keys()))
check("spectroscopy 必填 freq", "freq" in sem["spectroscopy"].get("requires", []))
check("t1 必填 zpa", "zpa" in sem["t1"].get("requires", []))
check("s21 自动回写 fread", "fread" in sem["s21"].get("auto_writes", []))
check("iqraw 自动回写 discriminator.center0",
      any("center0" in k for k in sem["iqraw"].get("auto_writes", [])))
check("spectroscopy 自动回写 f10/f21",
      "f10" in sem["spectroscopy"].get("auto_writes", [])
      and "f21" in sem["spectroscopy"].get("auto_writes", []))

# ── 3. parse_calls ────────────────────────────────────────────────────
code = """
sq.ramsey_df(q7ld3, do_plot=False)
sq.set_pi(q7ld3, do_plot=False)
res = sq.iqraw(q7ld3, states=None, auto=True, update=True, do_plot=False)
"""
calls = rp.parse_calls(code)
fns = [c["fn"] for c in calls]
check("parse_calls 解析出 3 个调用", len(calls) == 3, str(fns))
check("parse_calls 顺序正确", fns == ["ramsey_df", "set_pi", "iqraw"], str(fns))
check("parse_calls 识别比特名", calls[0].get("qubit") == "q7ld3")
check("parse_calls 识别 kwargs", calls[2].get("kwargs", {}).get("do_plot") == "False",
      str(calls[2].get("kwargs")))

# ── 4. spectroscopy 缺 freq → validation_error ───────────────────────
# registry 走真实口径：单比特摊平键（与 :3003/qubit/registry 一致）
reg = {"f10": 3.65, "f21": 3.45, "fread": 6.63,
       "pigate.amp": 0.96, "pigate.alpha": -0.5,
       "discriminator.center0": [-1.0, 2.0], "discriminator.center1": [3.0, 4.0]}
# 按比特嵌套的形态也要能定位（用于 from / to 快照）
reg_nested = {"q7ld3": dict(reg)}
sim = rp.simulate("sq.spectroscopy(q7ld3, do_plot=False)", reg)
check("缺 freq → ok=False", sim.get("ok") is False)
err = sim.get("error") or {}
check("缺 freq → error code=validation_error", err.get("code") == "validation_error", str(err))
check("缺 freq → 提示含 IndexError", "IndexError" in str(err.get("hint", "")),
      str(err.get("hint")))

# ── 5. t1 缺 zpa → 二维扫描提示 ──────────────────────────────────────
sim = rp.simulate("sq.t1(q7ld3, do_plot=False)", reg)
err = sim.get("error") or {}
check("t1 缺 zpa → ok=False", sim.get("ok") is False)
check("t1 缺 zpa → error code=validation_error", err.get("code") == "validation_error")
check("t1 缺 zpa → 提示提到 zpa/二维扫描",
      "zpa" in str(err.get("hint", "")) and "扫描" in str(err.get("hint", "")),
      str(err.get("hint")))

# ── 6. pidf 平移 f10；iqraw/s21 标出自动回写 ──────────────────────────
sim = rp.simulate("sq.pidf(q7ld3, df=0.05, do_plot=False, update=False)", reg)
after = sim.get("registry_after", {})
check("pidf → f10 被平移到 3.70", abs(float(after.get("f10", 0)) - 3.70) < 1e-6,
      str(after.get("f10")))
check("pidf → diff 含 f10", "f10" in (sim.get("diff") or {}))

sim_nested = rp.simulate("sq.pidf(q7ld3, df=0.05, do_plot=False, update=False)", reg_nested)
check("pidf（嵌套 registry）→ f10 被平移到 3.70",
      abs(float(sim_nested.get("registry_after", {}).get("q7ld3.f10", 0)) - 3.70) < 1e-6,
      str(sim_nested.get("registry_after", {}).get("q7ld3.f10")))
check("pidf（嵌套 registry）→ diff 含 q7ld3.f10",
      "q7ld3.f10" in (sim_nested.get("diff") or {}), str(sim_nested.get("diff")))

sim = rp.simulate("sq.iqraw(q7ld3, do_plot=False)", reg)
writes = [w.get("key") for w in (sim.get("calls") or [{}])[0].get("auto_writes", [])]
check("iqraw → 标出 discriminator.center0 自动回写",
      any("center0" in k for k in writes), str(writes))

sim = rp.simulate("sq.s21(q7ld3, do_plot=False)", reg)
writes = [w.get("key") for w in (sim.get("calls") or [{}])[0].get("auto_writes", [])]
check("s21 → 标出 fread 自动回写", "fread" in writes, str(writes))

# ── 7. 未知函数 / 无副作用 ────────────────────────────────────────────
sim = rp.simulate("sq.not_a_real_fn(q7ld3)", reg)
check("未知函数 → 有 warning", bool(sim.get("warnings")), str(sim.get("warnings")))
check("未知函数 → 调用标记 known=False",
      (sim.get("calls") or [{}])[0].get("known") is False)
sim = rp.simulate("sq.xeb(q7ld3, k=10, stats=50)", reg)
check("无副作用实验 → diff 为空", not (sim.get("diff") or {}), str(sim.get("diff")))
check("无副作用实验 → ok=True", sim.get("ok") is True)

# ── 8. ReplayStore 往返 ──────────────────────────────────────────────
tmpdir = tempfile.mkdtemp(prefix="qmclaw-phase3d-")
store = rp.ReplayStore(tmpdir)
fixture = rp.record_fixture(
    code="sq.iqraw(q7ld3, do_plot=False)",
    registry_before=reg,
    registry_after=reg,
    qubits=["q7ld3"],
    dataset_ids=[567],
    run_id="run-test",
)
path = store.save(fixture)
check("ReplayStore.save 返回路径", bool(path), str(path))
loaded = store.load(fixture["fixture_id"])
check("ReplayStore.load 能读回", loaded is not None)
check("读回的 fixture 保留 fixture_id",
      (loaded or {}).get("fixture_id") == fixture["fixture_id"])
listing = store.list_fixtures(10)
check("ReplayStore.list_fixtures 列出 1 条", len(listing) == 1, str(listing))
check("列表项含 call_count", isinstance((listing or [{}])[0].get("call_count"), int))

# ── 9. replay_fixture 漂移检测 ────────────────────────────────────────
recorded = dict(reg)
recorded["f10"] = 3.99  # 真机实际被改成了别的值
fx = rp.record_fixture(
    code="sq.pidf(q7ld3, df=0.05, do_plot=False, update=False)",
    registry_before=reg,
    registry_after=recorded,
)
rp2 = rp.replay_fixture(fx)
drift = rp2.get("drift") or []
check("replay_fixture 检出 value_mismatch 漂移",
      any(d.get("kind") == "value_mismatch" for d in drift), str(drift))
check("replay_fixture ok=False（检出漂移）", rp2.get("ok") is False)
check("replay_fixture recorded_diff 由 registry 快照实算",
      abs(float((rp2.get("recorded_diff") or {}).get("f10", {}).get("to", 0)) - 3.99) < 1e-6,
      str(rp2.get("recorded_diff")))

# ── 10. :3003/qubit/replay 端点（处理器级） ───────────────────────────
try:
    from services.quantum_service.server import QuantumService

    svc = QuantumService(port=3998)
    resp = svc._handle_qubit_replay({"action": "simulate",
                                     "code": "sq.iqraw(q7ld3, do_plot=False)",
                                     "registry": reg})
    check("端点 simulate → success", resp.get("success") is True, str(resp)[:200])
    check("端点 simulate → 返回 parsed_calls", isinstance(resp.get("parsed_calls"), list))
    check("端点 simulate → 返回 diff 键", "diff" in resp)

    resp = svc._handle_qubit_replay({"action": "simulate"})
    check("端点 simulate 缺 code → success=False", resp.get("success") is False)

    resp = svc._handle_qubit_replay({"action": "list", "limit": 5})
    check("端点 list → success", resp.get("success") is True, str(resp)[:200])
    check("端点 list → 含 fixtures 列表", isinstance(resp.get("fixtures"), list))

    resp = svc._handle_qubit_replay({"action": "record",
                                     "code": "sq.iqraw(q7ld3, do_plot=False)",
                                     "qubits": ["q7ld3"],
                                     "dataset_ids": [567]})
    check("端点 record → 返回 fixture_id", bool(resp.get("fixture_id")), str(resp)[:200])

    resp = svc._handle_qubit_replay({"action": "replay",
                                     "fixture_id": resp.get("fixture_id")})
    check("端点 replay → success", resp.get("success") is True, str(resp)[:200])
    check("端点 replay → 返回 drift 字段", "drift" in resp)

    resp = svc._handle_qubit_replay({"action": "replay", "fixture_id": "fx-not-exist"})
    check("端点 replay 找不到 fixture → success=False", resp.get("success") is False)
except Exception as exc:  # pragma: no cover
    check("量子服务处理器级调用回放端点", False, str(exc))

print("=" * 72)
print(f"_phase3d_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 72)
sys.exit(1 if FAIL else 0)
