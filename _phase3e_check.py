#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase3e_check.py — 阶段 3 后续：①fixture 自动录制 ②设备画像自动刷新。

离线自检，不连 LabRAD、不执行真机实验：
  1. config 层：replay_auto_record 开关（默认 off / env 覆盖 / 非法值回落 / config 键）
  2. server.py 静态接线：自动录制分支 + profile refresh 分支存在
  3. 自动录制：off 不录；code_only 录出 fixture；full 采前后快照；异常隔离
  4. 画像刷新：refresh=1 回填 registry 字段、写 data 快照；不带 refresh 时不连设备

用法：D:/anaconda3/envs/lqcs/python.exe _phase3e_check.py
"""
from __future__ import annotations

import ast
import json
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
print("_phase3e_check — 阶段 3 后续：fixture 自动录制 + 画像自动刷新")
print("=" * 72)

# ── 1. config 层：replay_auto_record 开关 ─────────────────────────────
import importlib  # noqa: E402

cfg = importlib.import_module("services.common.config")  # noqa: E402

saved_env = os.environ.pop("QMCLAW_REPLAY_AUTO_RECORD", None)
# 说明：断言的是**代码级默认**（配置里没写该项时应回落 off），
# 而不是当前配置文件里的值——运维可能显式把 replay_auto_record 打开
# （例如录真机 fixture 时），那是合法配置，不该让自检红。
_orig_loader = cfg._load_fallback_service_config


def _loader_without_key(service_name):
    data = dict(_orig_loader(service_name) or {})
    act = dict(data.get("actuation") or {})
    act.pop("replay_auto_record", None)
    data["actuation"] = act
    return data


try:
    cfg._load_fallback_service_config = _loader_without_key
    check("配置未声明时默认模式为 off（不打扰用户真机实验）",
          cfg._replay_auto_record() == "off", cfg._replay_auto_record())
    check("代码常量 DEFAULT_REPLAY_RECORD_MODE 为 off",
          getattr(cfg, "DEFAULT_REPLAY_RECORD_MODE", None) == "off",
          str(getattr(cfg, "DEFAULT_REPLAY_RECORD_MODE", None)))

    os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = "code_only"
    check("env 覆盖为 code_only", cfg._replay_auto_record() == "code_only")

    os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = "full"
    check("env 覆盖为 full", cfg._replay_auto_record() == "full")

    os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = "bogus"
    check("非法 env 值回落 off", cfg._replay_auto_record() == "off")

    os.environ.pop("QMCLAW_REPLAY_AUTO_RECORD", None)
    cfg._load_fallback_service_config = _loader_without_key
    flags = cfg.get_actuation_flags("quantum_service")
    check("get_actuation_flags 含 replay_auto_record", "replay_auto_record" in flags,
          str(list(flags.keys())))
    check("配置未声明时 flags 里默认仍是 off", flags.get("replay_auto_record") == "off",
          str(flags.get("replay_auto_record")))
    cfg._load_fallback_service_config = _orig_loader

    fb = json.load(open(os.path.join(SERVER, "config", "fallback_config.json"),
                         encoding="utf-8"))
    act = (fb.get("quantum_service") or {}).get("actuation") or {}
    check("fallback_config.json 声明 replay_auto_record",
          "replay_auto_record" in act, str(list(act.keys())))
    check("fallback_config 里 replay_auto_record 取值合法",
          act.get("replay_auto_record") in ("off", "code_only", "full"),
          str(act.get("replay_auto_record")))
finally:
    cfg._load_fallback_service_config = _orig_loader
    if saved_env is not None:
        os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = saved_env
    else:
        os.environ.pop("QMCLAW_REPLAY_AUTO_RECORD", None)

# ── 2. server.py 静态接线 ─────────────────────────────────────────────
server_py = os.path.join(SERVER, "services", "quantum_service", "server.py")
src = open(server_py, encoding="utf-8").read()
tree = ast.parse(src)
funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

check("server.py 有 _maybe_record_replay_fixture", "_maybe_record_replay_fixture" in funcs)
check("server.py 有 _safe_registry_snapshot", "_safe_registry_snapshot" in funcs)
check("server.py 有 _refresh_device_profile", "_refresh_device_profile" in funcs)
check("_handle_execute 调用自动录制", "_maybe_record_replay_fixture" in src)
check("_handle_qubit_profile 支持 refresh", "refresh" in src and "_refresh_device_profile" in src)
check("server.py 引用 replay_auto_record", "replay_auto_record" in src)

# ── 3+4. 处理器级行为 ─────────────────────────────────────────────────
try:
    from services.quantum_service.server import QuantumService
    from services.common.runrecord import RunRecord
    from services.common.replay import ReplayStore

    svc = QuantumService(port=3997)

    # 用替身顶掉 registry 读取（不连 LabRAD）
    calls = {"n": 0}

    def fake_registry(data):
        calls["n"] += 1
        # 与真实 _handle_qubit_registry 的返回形状保持一致：
        # {"name","session_path","registry","source"} —— **没有 success 字段**
        # （早期实现误判 payload["success"]，导致快照恒为空、值级漂移检测从未发生）
        return {
            "name": (data or {}).get("name") if isinstance(data, dict) else None,
            "session_path": ["", "LQHL", "test", "20251103_LD_SQ"],
            "source": "registry",
            "registry": {
                "f10": 3.6501,
                "f21": 3.4501,
                "fread": 6.63282,
                "fc_read": 6.843,
                "bias_z": -1.0,
                "channels": {"xy": ["DAC_LQ 761"], "z": ["DAC_LQ 762"],
                             "readout": ["ADC_LQ 760"]},
                "PiGate": {"amp": 0.965, "alpha": -0.507},
                "timing": {"xy": 1.85},
            },
        }

    svc._handle_qubit_registry = fake_registry

    # 3. 自动录制：off → 不录
    os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = "off"
    rec = RunRecord.from_execute("sq.iqraw(q7ld3, do_plot=False)",
                                  qubits=["q7ld3"])
    rec.dataset_ids = [567]
    path = svc._maybe_record_replay_fixture("sq.iqraw(q7ld3, do_plot=False)", rec,
                                            {"status": "success"}, "t-abc", None)
    check("off 模式不录 fixture", path is None, str(path))

    # 3. code_only → 录出 fixture
    os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = "code_only"
    tmp_store = os.path.join(tempfile.gettempdir(), "qmclaw-phase3e-fixtures")
    import services.common.replay as rp
    orig_store_init = rp.ReplayStore.__init__

    def patched_init(self, directory=None):
        orig_store_init(self, directory or tmp_store)

    rp.ReplayStore.__init__ = patched_init
    try:
        path = svc._maybe_record_replay_fixture("sq.iqraw(q7ld3, do_plot=False)", rec,
                                                {"status": "success", "stdout": "ok"},
                                                "t-abc", "t-task")
        check("code_only 录出 fixture 文件", bool(path) and os.path.isfile(path), str(path))
        if path:
            with open(path, encoding="utf-8") as fh:
                fx = json.load(fh)
            check("fixture 含 code", "sq.iqraw" in str(fx.get("code") or ""))
            check("fixture 含 qubits", list(fx.get("qubits") or []) == ["q7ld3"],
                  str(fx.get("qubits")))
            check("fixture 含 dataset_ids", list(fx.get("dataset_ids") or []) == [567],
                  str(fx.get("dataset_ids")))
            check("fixture 含 trace_id", str(fx.get("trace_id") or "") == "t-abc",
                  str(fx.get("trace_id")))
            check("fixture 含 task_id", str(fx.get("task_id") or "") == "t-task",
                  str(fx.get("task_id")))
            check("code_only 不采 registry 快照",
                  not fx.get("registry_before") and not fx.get("registry_after"),
                  str(fx.get("registry_before")))
            check("fixture 可被 replay_fixture 回放", "drift" in rp.replay_fixture(fx))

        # full 模式：采前后快照
        os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = "full"
        before = svc._safe_registry_snapshot("q7ld3")
        check("_safe_registry_snapshot 返回 registry 内容",
              bool(before) and before.get("f10") == 3.6501, str(before)[:120])
        path2 = svc._maybe_record_replay_fixture("sq.iqraw(q7ld3, do_plot=False)", rec,
                                                 {"status": "success"}, "t-abc", None)
        check("full 模式录出 fixture", bool(path2), str(path2))

        # 异常隔离：让 save 抛异常
        orig_save = rp.ReplayStore.save

        def boom(self, fixture):
            raise RuntimeError("disk on fire")

        rp.ReplayStore.save = boom
        try:
            path3 = svc._maybe_record_replay_fixture("sq.iqraw(q7ld3, do_plot=False)", rec,
                                                     {"status": "success"}, "t-abc", None)
            check("录制异常被隔离（返回 None 不抛）", path3 is None, str(path3))
        finally:
            rp.ReplayStore.save = orig_save
    finally:
        rp.ReplayStore.__init__ = orig_store_init

    # 4. 画像刷新
    os.environ["QMCLAW_REPLAY_AUTO_RECORD"] = "off"
    before_calls = calls["n"]
    resp = svc._handle_qubit_profile({"refresh": "1", "name": "q7ld3"})
    check("profile refresh=1 → success", resp.get("success") is True, str(resp)[:200])
    check("profile refresh=1 → 回填了 q7ld3", "q7ld3" in (resp.get("refreshed") or []),
          str(resp.get("refreshed")))
    entry = resp.get("entry") or {}
    check("profile refresh=1 → entry 含 f10", isinstance(entry.get("f10"), (int, float)),
          str(entry)[:200])
    check("profile refresh=1 → entry 含 xy_dac", entry.get("xy_dac") == "DAC_LQ 761",
          str(entry.get("xy_dac")))
    check("profile refresh=1 → entry 含 pi_gate_amp",
          abs(float(entry.get("pi_gate_amp", 0)) - 0.965) < 1e-9,
          str(entry.get("pi_gate_amp")))
    check("profile refresh=1 → 写了快照路径", bool(resp.get("snapshot_path")),
          str(resp.get("snapshot_path")))
    if resp.get("snapshot_path"):
        check("快照文件存在", os.path.isfile(resp["snapshot_path"]))

    # 不带 refresh → 不读设备
    mid = calls["n"]
    resp2 = svc._handle_qubit_profile({})
    check("不带 refresh → 不触发 registry 读取", calls["n"] == mid,
          f"{mid} -> {calls['n']}")
    check("不带 refresh → 返回摘要", resp2.get("success") is True, str(resp2)[:200])

    resp3 = svc._handle_qubit_profile({"name": "q7ld3"})
    check("带 name 不带 refresh → 返回条目", bool(resp3.get("entry")), str(resp3)[:200])
    check("带 name 不带 refresh → 不含 refreshed 字段", "refreshed" not in resp3)

    # 5. GET 查询参数必须并入 data（之前 query 被整个丢掉，
    #    文档里写的 `GET /qubit/profile?refresh=1` 实际不生效）
    resp4 = svc.handle_request("GET", "/qubit/profile", {},
                               {"refresh": ["1"], "name": ["q7ld3"]})
    check("query refresh=1 被并入 data", "q7ld3" in (resp4.get("refreshed") or []),
          str(resp4.get("refreshed")))
    resp5 = svc.handle_request("GET", "/qubit/profile", {}, {"name": ["q7ld3"]})
    check("query name 被并入 data", bool(resp5.get("entry")), str(resp5)[:200])
except Exception as exc:  # pragma: no cover
    import traceback
    traceback.print_exc()
    check("量子服务处理器级调用（自动录制 + 画像刷新）", False, str(exc))

print("=" * 72)
print(f"_phase3e_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 72)
sys.exit(1 if FAIL else 0)
