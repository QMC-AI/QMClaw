"""_phase2_check.py — 阶段 2 离线自检（不连 LabRAD、不碰真机）

覆盖：
1. services.common.runrecord   —— RunRecord 一等公民（id/指纹/治理标志/写读回/diff）
2. services.common.errors      —— 统一错误码表 / envelope / normalize 幂等
3. services.common.capabilities—— 能力探测（静态 AST，发现 .pyd 遮蔽）
4. services.quantum_service.port —— Qubit Port 骨架（假 service 离线测）
5. services.quantum_service.server —— AST 静态接线（3 个新路由 + RunRecord 包装）

用法：python _phase2_check.py
退出码：0 = 全绿；1 = 有 FAIL
"""
import ast
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))

PASS, FAIL, WARN = 0, 0, 0
LOG = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        LOG.append(f"PASS  {name}")
    else:
        FAIL += 1
        LOG.append(f"FAIL  {name}  {detail}")


def warn(name, detail=""):
    global WARN
    WARN += 1
    LOG.append(f"WARN  {name}  {detail}")


# ------------------------------------------------------------------ 1. RunRecord
from services.common.runrecord import (  # noqa: E402
    RunRecord,
    RunRecordWriter,
    diff_snapshots,
    new_run_id,
    program_fingerprint,
)

rid = new_run_id()
check("runrecord/id 形如 run-<hex>", rid.startswith("run-") and len(rid) > 10, rid)
check("runrecord/id 唯一", new_run_id() != rid)
fp = program_fingerprint("sq.iqraw(q2ld3, do_plot=False)")
check("runrecord/指纹长度16", len(fp) == 16, fp)
check("runrecord/指纹稳定", program_fingerprint("sq.iqraw(q2ld3, do_plot=False)") == fp)
check("runrecord/指纹随内容变", program_fingerprint("sq.iqraw(q3ld3)") != fp)

r_gov = RunRecord.from_execute("sq.iqraw(q2ld3)", task_id="t-1", qubits=["q2ld3"])
check("runrecord/有task_id→governed", r_gov.governed is True, str(r_gov.governed))
check("runrecord/有task_id→无shadow警告", not any("shadow" in w for w in r_gov.warnings), str(r_gov.warnings))
check("runrecord/qubits 记录", list(r_gov.qubits) == ["q2ld3"], str(r_gov.qubits))
check("runrecord/状态 pending", r_gov.status == "pending", r_gov.status)

r_ung = RunRecord.from_execute("sq.iqraw(q2ld3)")
check("runrecord/无task_id→未治理", r_ung.governed is False)
check("runrecord/无task_id→shadow 警告", any("shadow" in w for w in r_ung.warnings), str(r_ung.warnings))
d = r_ung.to_dict()
check("runrecord/to_dict 不含 program 原文", "program" not in d, str(list(d.keys())))
check("runrecord/to_dict 含 program_hash", bool(d.get("program_hash")))
check("runrecord/to_dict 含 program_length", d.get("program_length") == len("sq.iqraw(q2ld3)"), str(d.get("program_length")))

w = RunRecordWriter(directory=str(Path(tempfile.gettempdir()) / "qmclaw-phase2-records"))
w._ensure_dir()
rec = RunRecord.from_execute("sq.iqraw(q2ld3)", task_id="t-2")
rec.status = "success"
path = w.append(rec)
if path:
    check("runrecord/写入成功", os.path.exists(path), str(path))
    lines = [ln for ln in open(path, "r", encoding="utf-8").read().splitlines() if ln.strip()]
    check("runrecord/jsonl 可解析", bool(lines) and isinstance(json.loads(lines[-1]), dict))
    got = w.list_records(limit=5)
    check("runrecord/list_records 读回", isinstance(got, list) and len(got) >= 1, str(type(got)))
    if got:
        last = got[0]
        check("runrecord/读回含 run_id", bool(last.get("run_id")), str(last)[:120])
        check("runrecord/读回含 governed", "governed" in last, str(list(last.keys()))[:160])
else:
    warn("runrecord/写入目录不可用", "append 返回 None")

diff = diff_snapshots({"f10": 3.6, "x": 1}, {"f10": 3.7, "y": 2})
check("runrecord/diff changed", diff.get("changed", {}).get("f10", {}).get("to") == 3.7, str(diff))
check("runrecord/diff added", "y" in (diff.get("added") or {}), str(diff))
check("runrecord/diff removed", "x" in (diff.get("removed") or {}), str(diff))
check("runrecord/diff 空输入不炸", isinstance(diff_snapshots(None, None), dict))

# ------------------------- 1b. RunRecord 缺口补齐：metrics / verdict（阶段 3 第 2 条）
from services.common.runrecord import derive_verdict, extract_metrics  # noqa: E402

m = extract_metrics({"status": "success", "contrast": 0.75, "p1_i": 0.09, "dataset": "abc"})
check("runrecord/extract 取到数值指标", m.get("contrast") == 0.75 and m.get("p1_i") == 0.09, str(m))
check("runrecord/extract 不取非数值", "dataset" not in m, str(m))
check("runrecord/extract 兼容嵌套 metrics",
      extract_metrics({"metrics": {"T1_us": 72.5}}) == {"T1_us": 72.5},
      str(extract_metrics({"metrics": {"T1_us": 72.5}})))
check("runrecord/extract 空/None 不炸",
      extract_metrics(None) == {} and extract_metrics({}) == {} and extract_metrics("x") == {})
check("runrecord/extract 跳过 bool", extract_metrics({"contrast": True}) == {}, str(extract_metrics({"contrast": True})))
check("runrecord/verdict 无指标→None", derive_verdict({}) is None, str(derive_verdict({})))
check("runrecord/verdict 全达标→ok", derive_verdict({"contrast": 0.75}) == "ok", str(derive_verdict({"contrast": 0.75})))
check("runrecord/verdict 全不达标→fail", derive_verdict({"contrast": 0.05}) == "fail", str(derive_verdict({"contrast": 0.05})))
check("runrecord/verdict 部分达标→warn",
      derive_verdict({"contrast": 0.75, "t1": 5.0}) == "warn",
      str(derive_verdict({"contrast": 0.75, "t1": 5.0})))
check("runrecord/verdict 未知指标不参与", derive_verdict({"whatever": 1}) is None, str(derive_verdict({"whatever": 1})))

SRC_QS = (SERVER / "services" / "quantum_service" / "server.py").read_text(encoding="utf-8")
check("server 调用 extract_metrics", "extract_metrics(result)" in SRC_QS)
check("server 调用 derive_verdict", "derive_verdict(record.metrics" in SRC_QS)
check("server 落前后快照", "record.snapshot_before = pre_snapshot" in SRC_QS and "record.snapshot_after = post_snapshot" in SRC_QS)
check("server 落 diff", "diff_snapshots(pre_snapshot, post_snapshot)" in SRC_QS)
check("server 复用 after 快照喂 fixture", "pre_snapshot, post_snapshot" in SRC_QS)


# ------------------------------------------------------------------ 2. 错误码
from services.common.errors import (  # noqa: E402
    ERROR_CODES,
    describe_codes,
    error_envelope,
    normalize_error,
)

check("errors/码表非空", len(ERROR_CODES) >= 10, str(len(ERROR_CODES)))
for key in ("task_required", "quota_exceeded", "not_authorized", "needs_human",
            "degraded", "stuck", "timeout", "validation_error", "not_found",
            "internal_error"):
    check(f"errors/含码 {key}", key in ERROR_CODES)
check("errors/task_required 409", ERROR_CODES["task_required"]["http"] == 409)
check("errors/quota_exceeded 429", ERROR_CODES["quota_exceeded"]["http"] == 429)
check("errors/degraded 503", ERROR_CODES["degraded"]["http"] == 503)
check("errors/每码有 hint", all(bool(v.get("hint")) for v in ERROR_CODES.values()))

env = error_envelope("task_required")
check("errors/envelope 字段", set(["status", "error_code", "error", "hint", "retryable", "http_status"]) <= set(env.keys()), str(env))
check("errors/envelope code 正确", env.get("error_code") == "task_required")
check("errors/envelope 状态", env.get("status") == "blocked", str(env.get("status")))
check("errors/envelope http", env.get("http_status") == 409)

n1 = normalize_error({"status": "degraded_permanent", "error": "boom"})
check("errors/normalize 别名映射", n1.get("error_code") == "degraded", str(n1))
check("errors/normalize 保留原字段", n1.get("error") == "boom")
n2 = normalize_error(n1)
check("errors/normalize 幂等", n2.get("error_code") == "degraded" and n2.get("status") == "degraded", str(n2))
check("errors/normalize 不改结构", set(n1.keys()) == set(n2.keys()))
desc = describe_codes()
check("errors/describe 有版本", bool(desc.get("version")))
check("errors/describe 有码表", bool(desc.get("codes")))

# ------------------------------------------------------------------ 3. 能力探测
from services.common.capabilities import (  # noqa: E402
    KNOWN_MISSING,
    capability_report,
    discover_shadowed,
    find_lqms_root,
    scan_defs,
    unavailable_experiments,
)

root = find_lqms_root()
check("capabilities/找到 lqms 根", bool(root), str(root))
if root:
    tuners = os.path.join(root, "measure", "tuners")
    shadowed = discover_shadowed(tuners)
    mods = [s["module"] for s in shadowed]
    check("capabilities/发现 sq_nodes 被遮蔽", "lqms.measure.tuners.sq_nodes" in mods, str(mods))
    sq_entry = next((s for s in shadowed if s["module"].endswith("sq_nodes")), None)
    if sq_entry:
        check("capabilities/源码比 pyd 新", sq_entry.get("source_is_newer") is True, str(sq_entry))
    defs = scan_defs(os.path.join(tuners, "sq_nodes.py"))
    check("capabilities/源码含 spectroscopy_iq", "spectroscopy_iq" in defs, str(defs[:8]))
    rep = capability_report(root)
    check("capabilities/report 结构", bool(rep.get("capabilities")), str(list(rep.keys())))
    check("capabilities/verdict degraded", rep.get("verdict") == "degraded", str(rep.get("verdict")))
    miss = (rep.get("capabilities", {}).get("lqms.measure.tuners.sq_nodes", {}) or {}).get("missing_in_runtime", [])
    for fn in ("spectroscopy_iq", "time_rabi", "iqcenter"):
        check(f"capabilities/missing 含 {fn}", fn in miss, str(miss))
    un = unavailable_experiments(rep)
    check("capabilities/unavailable 列表", "spectroscopy_iq" in un, str(un[:8]))
else:
    warn("capabilities/未找到 lqms", "sys.path 中没有 lqms")

check("capabilities/KNOWN_MISSING 列表", "spectroscopy_iq" in KNOWN_MISSING)

# ------------------------------------------------------------------ 4. Qubit Port
from services.quantum_service.port import QubitPort  # noqa: E402


class FakeService:
    """离线替身：记录被调用的方法并返回可控数据。"""

    def __init__(self):
        self.calls = []
        self._f10 = 3.62

    def _handle_qubit_registry(self, data):
        self.calls.append(("registry", dict(data)))
        return {"success": True, "registry": {"f10": self._f10, "fread": 6.63}}

    def _handle_qubit_set_params(self, data):
        self.calls.append(("set_params", dict(data)))
        return {"success": True, "updated": list((data.get("params") or {}).keys())}

    def _handle_execute(self, data):
        self.calls.append(("execute", dict(data)))
        # 模拟厂商静默回写 f10（auto-update）
        self._f10 = 3.70
        return {"success": True, "stdout": "dataset: 1234 done"}


svc = FakeService()
port = QubitPort(svc)

st = port.read_state("q2ld3")
check("port/read_state 成功", st.get("success") is True, str(st))
check("port/read_state 委派正确", svc.calls[-1] == ("registry", {"name": "q2ld3"}), str(svc.calls[-1]))
check("port/read_state 空名报错", port.read_state("").get("error_code") == "validation_error")

check("port/apply_pulse 空 params 报错", port.apply_pulse("q2ld3", {}).get("error_code") == "validation_error")
check("port/apply_pulse 非法键报错", port.apply_pulse("q2ld3", {"  ": 1}).get("error_code") == "validation_error")
dry = port.apply_pulse("q2ld3", {"f10": 3.5}, dry_run=True)
check("port/apply_pulse dry_run 不写", dry.get("dry_run") is True and svc.calls[-1][0] != "set_params", str(dry))
res = port.apply_pulse("q2ld3", {"f10": 3.5})
check("port/apply_pulse 委派 set_params", svc.calls[-1] == ("set_params", {"name": "q2ld3", "params": {"f10": 3.5}}), str(svc.calls[-1]))

meas = port.measure("sq.iqraw(q2ld3, do_plot=False)", qubit="q2ld3")
check("port/measure 返回 registry_diff", "registry_diff" in meas, str(list(meas.keys())))
rd = meas.get("registry_diff", {})
check("port/measure diff 捕获 f10 变化", rd.get("changed", {}).get("f10", {}).get("to") == 3.70, str(rd))
check("port/measure 空 code 报错", port.measure("  ").get("error_code") == "validation_error")

pr = port.probe_transition("q7ld3", df=0.3)
check("port/probe 生成 pidf 代码", "sq.pidf(q7ld3" in pr.get("code", ""), str(pr.get("code")))
check("port/probe 强制 do_plot=False", "do_plot=False" in pr.get("code", ""))
check("port/probe 强制 update=False", "update=False" in pr.get("code", ""), str(pr.get("code")))
check("port/probe 非法 method 报错", port.probe_transition("q7ld3", method="xxx").get("error_code") == "validation_error")
iq = port.probe_transition("q7ld3", method="iqcenter")
check("port/probe iqcenter 能力缺失→not_found", iq.get("error_code") == "not_found", str(iq)[:160])
check("port/probe 能力缺失带提示", "重新编译" in json.dumps(iq, ensure_ascii=False), str(iq)[:200])
check("port/probe 空名报错", port.probe_transition("").get("error_code") == "validation_error")

check("port/program 空 IR 报错", port.program(None).get("error_code") == "validation_error")
pg = port.program({"nodes": []})
check("port/program 未注入 runner→not_found", pg.get("error_code") == "not_found", str(pg)[:160])
check("port/program 提示走 run_ir", "run_ir" in json.dumps(pg, ensure_ascii=False), str(pg)[:200])
port2 = QubitPort(FakeService(), program_runner=lambda ir, **o: {"success": True, "ran": True})
check("port/program 注入 runner 可用", port2.program({"nodes": [1]}).get("success") is True)

# ------------------------------------------------------------------ 5. server.py 静态接线
srv_path = SERVER / "services" / "quantum_service" / "server.py"
src = srv_path.read_text(encoding="utf-8")
try:
    tree = ast.parse(src)
    check("server/AST 可解析", True)
except SyntaxError as exc:
    tree = None
    check("server/AST 可解析", False, str(exc))

if tree is not None:
    func_names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fn in ("_handle_qubit_capabilities", "_handle_qubit_probe_transition",
               "_handle_qubit_runs", "_handle_execute", "_handle_execute_impl"):
        check(f"server/函数存在 {fn}", fn in func_names)
    for route in ("/qubit/capabilities", "/qubit/probe_transition", "/qubit/runs"):
        check(f"server/路由存在 {route}", route in src)
    check("server/引用 runrecord", "from services.common.runrecord import" in src)
    check("server/引用 capabilities", "from services.common.capabilities import" in src)
    check("server/import time", "import time" in src)
    check("server/执行包装已拆分", "_handle_execute_impl" in src)

    # 静态确认 execute 包装里带 RunRecord
    wrapper = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_execute":
            wrapper = node
    if wrapper is not None:
        body_src = ast.get_source_segment(src, wrapper) or ""
        check("server/包装创建 RunRecord", "RunRecord.from_execute" in body_src)
        check("server/包装回填 run_id", '"run_id"' in body_src or "'run_id'" in body_src)
        check("server/包装写 RunRecord", "RunRecordWriter" in body_src)
        check("server/包装不硬拦（无 raise/blocked）", "task_required" not in body_src, "包装内不应含硬拦截")

# ------------------------------------------------------------------ 汇总
print("\n".join(LOG))
print(f"\n_phase2_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
sys.exit(1 if FAIL else 0)
