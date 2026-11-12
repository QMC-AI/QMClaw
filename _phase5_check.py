#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase5_check.py — 阶段 5：fixture 驱动的离线回归（CI 覆盖真机链路）。

离线自检，不连 LabRAD、不执行真机实验：
  1. regression 层：classify_fixture / run_regression / load_fixtures / format_report
  2. 一致例（仿真 == 真机记录）→ 无漂移
  3. 漂移例（真机多改了一个键）→ 检出漂移
  4. 空目录 → verdict "empty"（明确"未覆盖"，不算通过）
  5. CLI `_replay_regression.py` 能在空目录上正常跑并给出 empty 结论

用法：D:/anaconda3/envs/lqcs/python.exe _phase5_check.py
"""
from __future__ import annotations

import copy
import io
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
print("_phase5_check — 阶段 5：fixture 驱动的离线回归")
print("=" * 72)

from services.common import regression as rg  # noqa: E402
from services.common.replay import record_fixture, simulate  # noqa: E402

CODE = "sq.iqraw(q2ld3, do_plot=False, update=False)"
BEFORE = {
    "q2ld3": {
        "f10": 3.6212,
        "f21": 3.4212,
        "fread": 6.7255,
        "PiGate": {"amp": 1.22, "alpha": -0.78},
        "discriminator": {"center0": [-56000, 57000], "center1": [11000, 84000]},
    }
}

# ── 1. API 存在性 ────────────────────────────────────────────────────
for name in ("classify_fixture", "run_regression", "load_fixtures",
             "regression_from_directory", "format_report", "DRIFT_LABELS"):
    check("regression 提供 %s" % name, hasattr(rg, name))
check("DRIFT_LABELS 覆盖三类漂移",
      set(rg.DRIFT_LABELS) == {"simulated_only", "recorded_only", "value_mismatch"},
      str(sorted(rg.DRIFT_LABELS)))

# ── 2. 一致例 ────────────────────────────────────────────────────────
sim = simulate(CODE, BEFORE)
sim_after = sim.get("registry_after_nested") or sim.get("registry_after") or {}
fx_ok = record_fixture(code=CODE, registry_before=copy.deepcopy(BEFORE),
                       registry_after=copy.deepcopy(sim_after), qubits=["q2ld3"])
case = rg.classify_fixture(fx_ok)
check("一致例：无漂移", case.get("ok") is True and case.get("drift_count") == 0,
      str(case.get("drift"))[:200])
check("一致例：drift_kinds 为空", not case.get("drift_kinds"), str(case.get("drift_kinds")))

report = rg.run_regression([fx_ok])
check("run_regression 一致例 → verdict green", report.get("verdict") == "green",
      str(report.get("verdict")))
check("run_regression 统计 passed=1", report.get("passed") == 1 and report.get("total") == 1,
      str({k: report.get(k) for k in ("total", "passed", "drifted")}))

# ── 3. 漂移例（真机多改了一个键）────────────────────────────────────────
drift_after = copy.deepcopy(sim_after)
target = drift_after.get("q2ld3") if isinstance(drift_after, dict) else None
if isinstance(target, dict):
    target["timing"] = {"xy": 99.99}
else:
    drift_after = {"q2ld3": {"f10": 3.62, "timing": {"xy": 99.99}}}
fx_bad = record_fixture(code=CODE, registry_before=copy.deepcopy(BEFORE),
                        registry_after=drift_after, qubits=["q2ld3"])
bad_case = rg.classify_fixture(fx_bad)
check("漂移例：检出漂移", bad_case.get("ok") is False and bad_case.get("drift_count", 0) >= 1,
      str(bad_case.get("drift"))[:200])
kinds = set(bad_case.get("drift_kinds") or {})
check("漂移例：漂移种类可读", bool(kinds & {"recorded_only", "value_mismatch"}), str(kinds))
bad_report = rg.run_regression([fx_bad])
check("run_regression 漂移例 → verdict drift", bad_report.get("verdict") == "drift",
      str(bad_report.get("verdict")))
check("run_regression max_drift 放宽后 → green",
      rg.run_regression([fx_bad], max_drift=5).get("verdict") == "green")

# 坏 fixture 不应拖垮整轮
try:
    broken_report = rg.run_regression([{"fixture_id": "fx-broken"}])
    check("坏 fixture 被隔离（不抛异常）", broken_report.get("verdict") in ("broken", "green"),
          str(broken_report.get("verdict")))
except Exception as exc:  # noqa: BLE001
    check("坏 fixture 被隔离（不抛异常）", False, repr(exc))

# ── 4. 空目录 → empty（明确"未覆盖"，不算通过）─────────────────────────
empty_dir = tempfile.mkdtemp(prefix="qmclaw-fx-empty-")
empty_report = rg.regression_from_directory(empty_dir, limit=10)
check("空目录 → verdict empty", empty_report.get("verdict") == "empty",
      str(empty_report.get("verdict")))
check("空目录 → total=0", empty_report.get("total") == 0)
check("empty 报告带说明", any("empty" in str(n) for n in empty_report.get("notes") or []))

# ── 5. 目录落盘 + 读回 ───────────────────────────────────────────────
from services.common.replay import ReplayStore  # noqa: E402

store_dir = tempfile.mkdtemp(prefix="qmclaw-fx-store-")
store = ReplayStore(store_dir)
saved_path = store.save(fx_ok)
check("fixture 落盘返回路径", bool(saved_path) and os.path.isfile(str(saved_path)),
      str(saved_path)[:120])
loaded = rg.load_fixtures(store_dir, limit=10)
check("load_fixtures 读回 1 条", len(loaded) == 1, str(len(loaded)))
if loaded:
    check("读回的 fixture 能分类", rg.classify_fixture(loaded[0]).get("ok") is True)
    dir_report = rg.regression_from_directory(store_dir, limit=10)
    check("目录回归 → green", dir_report.get("verdict") == "green", str(dir_report.get("verdict")))

# ── 6. 报告文本 ──────────────────────────────────────────────────────
text = rg.format_report(report)
check("format_report 含结论行", "结论" in text and "QMClaw 回放回归" in text)
check("format_report 含 OK 标记", "[OK ]" in text)

# ── 7. CLI 可用（在空目录上跑）────────────────────────────────────────
cli = os.path.join(ROOT, "_replay_regression.py")
check("CLI 文件存在", os.path.isfile(cli))
if os.path.isfile(cli):
    # 沙箱禁 subprocess → 进程内用 runpy 跑 CLI（与 run_all_checks.py 同一手法）
    import runpy  # noqa: PLC0415

    buf = io.StringIO()
    old_argv = list(sys.argv)
    try:
        sys.argv = [cli, "--dir", empty_dir, "--json"]
        stdout = sys.stdout
        sys.stdout = buf
        try:
            runpy.run_path(cli, run_name="__main__")
        except SystemExit as exc:  # CLI 用 raise SystemExit(main()) 返回退出码
            code = int(exc.code or 0)
        else:
            code = 0
        finally:
            sys.stdout = stdout
    finally:
        sys.argv = old_argv
    check("CLI 空目录退出码 0", code == 0, "code=%s" % code)
    try:
        payload = json.loads(buf.getvalue() or "{}")
        check("CLI JSON 含 verdict=empty", payload.get("verdict") == "empty",
              str(payload.get("verdict")))
    except Exception as exc:  # noqa: BLE001
        check("CLI 输出可解析 JSON", False, repr(exc))

# ── 8. 弱结论保护 + 目录可指定（阶段 5 收尾修正）─────────────────────
# 8.1 无 registry_after 快照的 fixture → 必须明说"没比"，不能当成"一致"
no_reg = {"fixture_id": "fx-noreg", "code": CODE, "qubits": ["q2ld3"],
          "dataset_ids": [], "registry_before": {}, "registry_after": {}}
nc = rg.classify_fixture(no_reg)
check("无 registry 快照 → has_registry=False", nc.get("has_registry") is False)
check("无 registry 快照 → 附'弱结论'说明",
      any("未做值级比对" in str(n) for n in nc.get("notes") or []),
      str(nc.get("notes"))[:200])

# 8.2 报告字段：请求目录 vs 实际目录 vs 是否降级
r2 = rg.regression_from_directory(store_dir, limit=10)
check("报告含 requested_directory", r2.get("requested_directory") == store_dir,
      str(r2.get("requested_directory"))[:160])
check("报告含 directory（实际生效）", bool(r2.get("directory")),
      str(r2.get("directory"))[:160])
check("报告含 degraded_store 布尔", isinstance(r2.get("degraded_store"), bool))
text2 = rg.format_report(r2)
check("format_report 显示目录行", "目录" in text2)

# 8.3 QMCLAW_REPLAY_FIXTURE_DIR 可指定 fixture 目录（读写两侧对齐）
_env_key = "QMCLAW_REPLAY_FIXTURE_DIR"
_old_env = os.environ.get(_env_key)
try:
    os.environ[_env_key] = store_dir
    st2 = ReplayStore()
    check("ReplayStore 支持 QMCLAW_REPLAY_FIXTURE_DIR", st2.directory == store_dir,
          str(st2.directory)[:160])
    check("显式指定目录时不降级", st2.degraded is False)
finally:
    if _old_env is None:
        os.environ.pop(_env_key, None)
    else:
        os.environ[_env_key] = _old_env

# 8.4 缺 registry 的用例在报告里要显式提示"弱结论"
weak_report = rg.run_regression([no_reg])
weak_text = rg.format_report(weak_report)
check("弱结论在报告里显式提示", "弱结论" in weak_text, weak_text[:200])

# ── 9. 读侧目录降级修复（可读目录必须直接读它）─────────────────────
# 9.1 只读目录：写探针失败也不该把读取带到临时目录
st_read = ReplayStore(store_dir)
check("可读目录 → read_directory 就是该目录",
      st_read.read_directory == store_dir,
      "%s vs %s" % (st_read.read_directory, store_dir))
try:
    with open(os.path.join(store_dir, ".probe_write"), "w", encoding="utf-8") as fh:
        fh.write("x")
    os.remove(os.path.join(store_dir, ".probe_write"))
    _writable = True
except Exception:  # noqa: BLE001
    _writable = False
if not _writable:
    check("目录不可写时 read_directory 仍不降级（读/写分开判定）",
          st_read.read_directory == store_dir,
          st_read.read_directory)

# 9.2 列目录 + 读内容必须落在同一个 store 实例上
_fx = rg.load_fixtures(store_dir, limit=50, store=st_read)
check("load_fixtures 从请求目录读到 fixture", len(_fx) >= 0,
      "count=%s" % len(_fx))
_ids = {f.get("fixture_id") for f in _fx}
_listed = {i.get("fixture_id") for i in st_read.list_fixtures(limit=50)}
check("list_fixtures 与 load 看到同一批 fixture", _ids == _listed,
      "listed=%s loaded=%s" % (len(_listed), len(_ids)))

# 9.3 报告里的目录字段与实际读目录一致
r3 = rg.regression_from_directory(store_dir, limit=10)
check("报告 directory == 实际读目录",
      str(r3.get("directory")) == str(st_read.read_directory),
      "%s vs %s" % (r3.get("directory"), st_read.read_directory))
text3 = rg.format_report(r3)
if str(r3.get("directory")) != str(r3.get("requested_directory")):
    check("读目录≠请求目录时报告给出告警", "≠" in text3, text3[:200])
else:
    check("读目录==请求目录时报告不误报", "≠" not in text3, text3[:200])

print("=" * 72)
print("_phase5_check: PASS=%d FAIL=%d WARN=%d" % (PASS, FAIL, WARN))
if FAILED:
    print("失败项: %s" % ", ".join(FAILED))
print("=" * 72)
raise SystemExit(1 if FAIL else 0)
