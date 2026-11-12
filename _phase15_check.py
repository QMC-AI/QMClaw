#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase15_check.py — 阶段 15：异步执行补齐 RunRecord（producer 化的前置）。

背景：`/execute` 早就能带 `async=true` 立即回 `job_id`，但外层 `_handle_execute`
在**提交时**就落 RunRecord —— 拿到的只是 `{status:"queued"}`，于是异步执行的记录
永远缺 dataset_ids / metrics / verdict。前端或脚本如果改用异步，审计链就断了。

本阶段把"提交"和"完成"拆成两条同 run_id 的记录：
  - 提交：status=queued + job_id + trace_id（可追溯"谁在排队"）
  - 完成：worker 跑完用真实结果补齐并追加（status/dataset_ids/metrics/verdict/diff）
  - 读侧（`/qubit/runs`、`/qubit/state`）按 run_id 去重取最后一条

离线自检（不连设备、不写真实台账）：
  1. `collapse_records` 语义（同 run 后写覆盖、顺序、匿名记录、不改原对象）
  2. `RunRecord.job_id` 只在有值时落盘
  3. `_complete_record`：状态/错误码/数据集归属/指标/verdict/落盘/回放
  4. `_handle_async_submission`：queued 记录 + job 挂载 + 回包契约
  5. `_finalize_async_job`：补齐后清掉挂载、异常不外抛
  6. `/qubit/runs` 与 `evidence_from_records` 的 run_id 去重
  7. 源码顺序契约：worker 在**执行前**取基线、**执行后**补记录

用法：D:/anaconda3/envs/lqcs/python.exe _phase15_check.py
"""
from __future__ import annotations

import ast
import os
import sys
import threading
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
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


from services.common import runrecord as RR  # noqa: E402
from services.common import state_evidence as SE  # noqa: E402
from services.quantum_service import server as QS  # noqa: E402

SERVER_SRC = open(os.path.join(SERVER, "services", "quantum_service", "server.py"),
                  encoding="utf-8").read()
RUNRECORD_SRC = open(os.path.join(SERVER, "services", "common", "runrecord.py"),
                     encoding="utf-8").read()


class _FakeWriter:
    """拦截落盘：记录每次 append 的对象，不碰磁盘。"""

    appended: list = []

    def __init__(self, *args, **kwargs):
        self.directory = "<fake>"

    def append(self, record):
        _FakeWriter.appended.append(record.to_dict() if hasattr(record, "to_dict") else record)
        return "<fake>.jsonl"

    def list_records(self, limit: int = 50):
        return _FakeWriter.appended[-limit:]


print("=" * 72)
print("阶段 15：异步执行补齐 RunRecord")
print("=" * 72)

# ── 1. collapse_records ─────────────────────────────────────────────────────
print("\n[1] collapse_records（同 run_id 后写覆盖）")
a = {"run_id": "r1", "status": "queued"}
b = {"run_id": "r2", "status": "success"}
c = {"run_id": "r1", "status": "success", "dataset_ids": [7]}
out = RR.collapse_records([a, b, c])
check("同 run_id 只留一条", len(out) == 2, str(out))
check("保留的是最后一条（完成态）", out[1].get("status") == "success", str(out))
check("顺序按最后一次出现", [r.get("run_id") for r in out] == ["r2", "r1"], str(out))
check("不改动传入对象", a.get("status") == "queued")
check("匿名记录原样保留", [r.get("x") for r in RR.collapse_records([{"x": 1}, a])] == [1, None])
check("非 list 输入返回空", RR.collapse_records(None) == [] and RR.collapse_records("x") == [])
check("忽略非 dict 项", RR.collapse_records([a, "junk", None]) == [a])

# ── 2. RunRecord.job_id ─────────────────────────────────────────────────────
print("\n[2] RunRecord.job_id")
rec = RR.RunRecord(source="execute", run_id="run-x", status="queued")
check("没 job_id 时不写这个键", "job_id" not in rec.to_dict(), str(rec.to_dict().keys()))
rec.job_id = "job_123"
check("有 job_id 时落盘", rec.to_dict().get("job_id") == "job_123")
check("job_id 在 runrecord.py 里有字段声明", "job_id: Optional[str] = None" in RUNRECORD_SRC)

# ── 3. _complete_record ─────────────────────────────────────────────────────
print("\n[3] _complete_record（同步/异步共用）")
_orig_writer = RR.RunRecordWriter
RR.RunRecordWriter = _FakeWriter
try:
    class _Stub:
        _extract_dataset_ids = lambda self, result: [11]          # noqa: E731
        _probe_dataset_numbers = lambda self: [11, 12]            # noqa: E731
        _safe_registry_snapshot = lambda self, q: None            # noqa: E731
        _maybe_record_replay_fixture = lambda self, *a, **k: None  # noqa: E731

    from services.common.runrecord import RunRecord

    _FakeWriter.appended = []
    r = RunRecord(source="execute", run_id="run-a", status="pending", qubits=["q1"],
                  task_id="t1", trace_id="tr1")
    res = {"status": "success", "stdout": "", "metrics": {"contrast": 0.74}}
    QS.QuantumService._complete_record(_Stub(), r, res, [11], None, "sq.iqraw(q1)",
                                            "tr1", "t1")
    d = _FakeWriter.appended[-1]
    check("状态写回 success", d.get("status") == "success", str(d.get("status")))
    check("数据集 = stdout ∪ 差集（去重升序）", d.get("dataset_ids") == [11, 12], str(d.get("dataset_ids")))
    check("metrics 提取", d.get("metrics", {}).get("contrast") == 0.74, str(d.get("metrics")))
    check("verdict 由 Metric Registry 给（good/ok）", bool(d.get("verdict")), str(d.get("verdict")))
    check("finished_at 被填", d.get("finished_at") is not None)
    check("trace_id 保留", d.get("trace_id") == "tr1")

    _FakeWriter.appended = []
    r2 = RunRecord(source="execute", run_id="run-b", status="pending", qubits=["q1"])
    QS.QuantumService._complete_record(
        _Stub(), r2, {"status": "failed", "error": "boom", "error_code": "timeout"}, None, None,
        "", None, None)
    d2 = _FakeWriter.appended[-1]
    check("失败态写回 failed", d2.get("status") == "failed", str(d2.get("status")))
    check("错误码保留", d2.get("error_code") == "timeout", str(d2.get("error_code")))
    check("错误摘要截断入库", d2.get("error") == "boom")
    check("探测失败（before=None）→ 只用 stdout 的号，不猜差集",
          d2.get("dataset_ids") == [11], str(d2.get("dataset_ids")))

    _FakeWriter.appended = []
    r3 = RunRecord(source="execute", run_id="run-c", status="pending")
    QS.QuantumService._complete_record(_Stub(), r3, "not-a-dict", None, None, "", None, None)
    check("非 dict 结果不落账也不抛", _FakeWriter.appended == [])
finally:
    RR.RunRecordWriter = _orig_writer

# ── 4. _handle_async_submission ────────────────────────────────────────────
print("\n[4] _handle_async_submission（提交即落 queued）")
RR.RunRecordWriter = _FakeWriter
try:
    class _AsyncStub:
        def __init__(self):
            self._jobs = {"job_1": {"job_id": "job_1", "status": "queued"}}
            self._jobs_lock = threading.Lock()

    stub = _AsyncStub()
    _FakeWriter.appended = []
    r = RunRecord(source="execute", run_id="run-q", status="pending", qubits=["q1"])
    queued_result = {"task_id": "t9", "job_id": "job_1", "status": "queued", "async": True}
    resp = QS.QuantumService._handle_async_submission(
        stub, r, queued_result, "sq.iqraw(q1)", "tr9", True, None, True)
    d = _FakeWriter.appended[-1]
    check("queued 记录 status=queued", d.get("status") == "queued", str(d.get("status")))
    check("queued 记录带 job_id", d.get("job_id") == "job_1", str(d.get("job_id")))
    check("queued 记录没有 finished_at", d.get("finished_at") is None)
    check("job 挂上了 run_record", stub._jobs["job_1"].get("run_record") is r)
    check("job 记住 run_id", stub._jobs["job_1"].get("run_id") == "run-q")
    check("回包带 run_id", resp.get("run_id") == "run-q", str(resp.get("run_id")))
    check("回包带 poll 提示", resp.get("poll") == "/jobs/job_1", str(resp.get("poll")))
    check("回包保留 job_id 与 queued 状态",
          resp.get("job_id") == "job_1" and resp.get("status") == "queued")
    check("未受治理时给 governance_hint", "governance_hint" in resp)
    check("trace 由服务端生成时给 trace_hint", "trace_hint" in resp)
finally:
    RR.RunRecordWriter = _orig_writer

# ── 5. _finalize_async_job ─────────────────────────────────────────────────
print("\n[5] _finalize_async_job（跑完补齐）")
captured = {}


class _FinalStub:
    def _complete_record(self, record, result, before, pre, code, trace, task):
        captured.update(record=record, result=result, before=before, pre=pre,
                        code=code, trace=trace, task=task)


_final_stub = _FinalStub()
job = {"job_id": "job_9", "run_record": RunRecord(source="execute", run_id="run-9"),
       "record_code": "sq.t1(q1, zpa=0)", "record_trace_id": "tr", "record_task_id": "t"}
QS.QuantumService._finalize_async_job(_final_stub, job, {"status": "success"}, [1], None)
check("用真实结果补记录", captured.get("result", {}).get("status") == "success")
check("补齐时带执行前基线", captured.get("before") == [1])
check("补齐时带代码与 trace", captured.get("code", "").startswith("sq.t1")
      and captured.get("trace") == "tr")
check("补齐后清掉挂载", "run_record" not in job and "record_code" not in job)


class _BoomStub:
    def _complete_record(self, *a, **k):
        raise RuntimeError("boom")


try:
    QS.QuantumService._finalize_async_job(
        _BoomStub(), {"job_id": "j", "run_record": RunRecord(source="execute")}, {}, None, None)
    check("补齐异常不外抛", True)
except Exception as exc:  # pragma: no cover
    check("补齐异常不外抛", False, repr(exc))
check("没有 run_record 时直接返回",
      QS.QuantumService._finalize_async_job(_FinalStub(), {"job_id": "j"}, {}, None, None) is None)

# ── 6. 读侧去重 ────────────────────────────────────────────────────────────
print("\n[6] 读侧按 run_id 去重")
recs = [
    {"run_id": "r1", "status": "queued", "started_at": 1, "qubits": ["q1"],
     "program": "sq.iqraw(q1)"},
    {"run_id": "r1", "status": "success", "started_at": 1, "finished_at": 5,
     "qubits": ["q1"], "program": "sq.iqraw(q1)", "dataset_ids": [9],
     "metrics": {"contrast": 0.71}},
]
ev = SE.evidence_from_records(recs, "q1")
check("queued + 完成记录 → considered 只算 1", ev.get("considered") == 1, str(ev.get("considered")))
check("证据来自完成记录", ev.get("evidence", {}).get("contrast") == 0.71, str(ev.get("evidence")))
check("来源指向同一 run", ev.get("sources", {}).get("contrast", {}).get("run_id") == "r1")

ev2 = SE.evidence_from_records([recs[0]], "q1")
check("只有 queued 记录 → 不产生证据且计入 ignored",
      ev2.get("evidence") == {} and ev2.get("ignored") == 1, str(ev2))

check("_prefer_record 偏向有数据集的一条",
      SE._prefer_record({"run_id": "x", "status": "queued"},
                        {"run_id": "x", "status": "success", "dataset_ids": [1],
                         "finished_at": 2}).get("status") == "success")
check("/qubit/runs 用了 collapse_records",
      "collapse_records(created_order)" in SERVER_SRC)
check("/qubit/runs 先翻成时间顺序再去重",
      "list(reversed(writer.list_records(limit=scan)))" in SERVER_SRC)

# ── 7. worker 顺序契约 ─────────────────────────────────────────────────────
print("\n[7] 源码顺序契约（执行前取基线、执行后补记录）")
run_job = SERVER_SRC.split("def _run_job", 1)[1].split("def _evict_jobs", 1)[0]
check("_run_job 在读代码前取数据集基线", run_job.index("datasets_before = self._probe_dataset_numbers()")
      < run_job.index("self._run_exec_task("))
check("_run_job 在跑完后补记录",
      run_job.index("self._finalize_async_job(") > run_job.index("self._run_exec_task("))
check("异步提交不再提前 probe（is_async 守卫）",
      "datasets_before = None if is_async else self._probe_dataset_numbers()" in SERVER_SRC)
check("异步提交不提前取 registry 快照",
      "(not is_async) and _replay_auto_record() == \"full\"" in SERVER_SRC)
check("同步路径同样走 _complete_record",
      "result = self._complete_record(" in SERVER_SRC)
check("异步分支在同步落账之前返回",
      SERVER_SRC.index("if isinstance(result, dict) and result.get(\"async\") and result.get(\"job_id\")")
      < SERVER_SRC.index("result = self._complete_record("))

try:
    ast.parse(SERVER_SRC)
    check("server.py 语法 OK", True)
except SyntaxError as exc:  # pragma: no cover
    check("server.py 语法 OK", False, str(exc))

OPS = os.path.join(ROOT, "docs", "OPERATIONS.md")
ops = open(OPS, encoding="utf-8").read() if os.path.exists(OPS) else ""
check("OPERATIONS 写了 §3.18 异步记录", "异步执行的 RunRecord" in ops or "producer" in ops)
check("OPERATIONS §4 表登记 _phase15_check.py", "_phase15_check.py" in ops)
RUNNER = os.path.join(ROOT, "run_all_checks.py")
runner = open(RUNNER, encoding="utf-8").read() if os.path.exists(RUNNER) else ""
check("run_all_checks.py OFFLINE 含 _phase15_check.py", "_phase15_check.py" in runner)

print("\n" + "=" * 72)
print(f"PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  -", name)
print("=" * 72)
sys.exit(1 if FAIL else 0)
