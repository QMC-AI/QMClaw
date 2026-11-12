#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase11_check.py — 阶段 11：比特状态机可自查（证据从台账来）。

对应 docs/ARCHITECTURE_ROADMAP.md §2.4：
> 每态定义**进入条件 + 证据数据集 + 判据**；推进由规则决定…
> 收益：标定从"知识在人脑里"变成"状态在系统里"，能回答"q7ld3 卡在哪一态、缺什么证据"。

阶段 2 只做了前半：`qubit_state.derive_state(evidence)` 会判"给证据 → 在哪一态"，
但证据要调用方自己填 —— 等于"标定记录还在人脑里"。本阶段补另一半：

  - `services/common/state_evidence.py`：把 RunRecord 台账翻成证据，保留
    run_id / 数据集号 / 函数名 / 时间；失败或没数据集号的记录只计数不留证据；
    同一指标两次读数差超 20% 记 `conflicts`（读出链慢漂就该在这儿冒头）。
  - `qubit_state`：新增 `EVIDENCE_SOURCES`（哪个实验产出哪种证据）与
    `MISSING_FOR_STATE`（每个状态缺什么），`derive_state` 输出 `missing_detail`。
  - `POST/GET :3003/qubit/state`：读台账 + 可选显式证据 → 状态 + 缺什么 + 下一步。

离线自检，不连 LabRAD：
  1. 台账解析（manifest 优先 / program 回退 / 比特匹配 / 成功与数据集门槛）
  2. 证据推导（频率/幅度标志、指标映射、新证据覆盖旧证据、冲突判定、来源字段）
  3. 证据来源表与缺失表（覆盖全部 7 种证据、6 个状态单调）
  4. 端点接线与只读性（不得出现 execute / 写寄存器调用）

用法：D:/anaconda3/envs/lqcs/python.exe _phase11_check.py
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
print("_phase11_check — 阶段 11：比特状态机可自查")
print("=" * 72)

from services.common import qubit_state as qs  # noqa: E402
from services.common import state_evidence as se  # noqa: E402


def rec(**kw):
    base = {
        "run_id": "run-1",
        "qubits": ["q4ld3"],
        "status": "success",
        "dataset_ids": [2669],
        "dataset_source": "datavault",
        "metrics": {},
        "finished_at": 1000.0,
        "manifest": {"functions": ["iqraw"]},
        "trace_id": "t-1",
    }
    base.update(kw)
    return base


# ── 1. 台账解析 ───────────────────────────────────────────────────────────
check("函数名: manifest 优先", se.functions_of(rec(manifest={"functions": ["t1"]})) == ["t1"])
check("函数名: 无 manifest 时回退解析 program",
      se.functions_of({"program": "sq.iqraw(q4ld3, do_plot=False)\nprint(1)"}) == ["iqraw"])
check("函数名: 多个调用按顺序",
      se.functions_of({"program": "sq.pidf(q)\nsq.iqraw(q)"}) == ["pidf", "iqraw"])
check("函数名: 空记录返回空表", se.functions_of({}) == [])

check("比特匹配: 名字命中", se._matches_qubit({"qubits": ["q4ld3"]}, "q4ld3", ["iqraw"]))
check("比特匹配: 别的比特不算", not se._matches_qubit({"qubits": ["q1ld3"]}, "q4ld3", ["iqraw"]))
check("比特匹配: 全局实验无 qubits 也算",
      se._matches_qubit({"qubits": []}, "q4ld3", ["s21"]))
check("比特匹配: 非全局实验无 qubits 不算",
      not se._matches_qubit({"qubits": []}, "q4ld3", ["iqraw"]))
check("比特匹配: 不指定比特时全收", se._matches_qubit({"qubits": []}, None, []))

# ── 2. 证据推导 ───────────────────────────────────────────────────────────
d = se.evidence_from_records([rec(metrics={"contrast": 0.74})], qubit="q4ld3")
check("证据: iqraw 记录给出 contrast", d["evidence"].get("contrast") == 0.74)
check("证据: contrast 来源带 run_id 与数据集",
      d["sources"]["contrast"]["run_id"] == "run-1"
      and d["sources"]["contrast"]["dataset_ids"] == [2669])
check("证据: iqraw 不算频率证据", "freq_locked" not in d["evidence"])
check("证据: considered 计数", d["considered"] == 1 and d["ignored"] == 0)

d = se.evidence_from_records([rec(manifest={"functions": ["spectroscopy"]})], qubit="q4ld3")
check("证据: spectroscopy 成功 → freq_locked", d["evidence"].get("freq_locked") is True)
check("证据: freq_locked 来源函数", d["sources"]["freq_locked"]["functions"] == ["spectroscopy"])

d = se.evidence_from_records([rec(manifest={"functions": ["piamp"]})], qubit="q4ld3")
check("证据: piamp 成功 → amp_calibrated", d["evidence"].get("amp_calibrated") is True)
check("证据: piamp 也给 freq（pidf 一类的频率证据不算在内）",
      "freq_locked" not in d["evidence"])

d = se.evidence_from_records(
    [rec(status="error", metrics={"contrast": 0.9}, manifest={"functions": ["iqraw"]})],
    qubit="q4ld3",
)
check("失败记录不产生证据", d["evidence"] == {} and d["ignored"] == 1)
check("失败记录留痕 run_id", d["ignored_runs"] == ["run-1"])

d = se.evidence_from_records([rec(dataset_ids=[])], qubit="q4ld3")
check("成功但没有数据集号 → 不作为证据", d["evidence"] == {} and d["ignored"] == 1)

d = se.evidence_from_records(
    [rec(run_id="new", finished_at=20.0, metrics={"contrast": 0.75}),
     rec(run_id="old", finished_at=10.0, metrics={"contrast": 0.74})],
    qubit="q4ld3",
)
check("新证据覆盖旧证据", d["evidence"]["contrast"] == 0.75 and d["sources"]["contrast"]["run_id"] == "new")
check("两次接近不报冲突", d["conflicts"] == [])

d = se.evidence_from_records(
    [rec(run_id="new", finished_at=20.0, metrics={"contrast": 0.75}),
     rec(run_id="old", finished_at=10.0, metrics={"contrast": 0.21})],
    qubit="q4ld3",
)
check("两次差超 20% → 报冲突", len(d["conflicts"]) == 1)
c = d["conflicts"][0]
check("冲突记录保留/丢弃两侧的 run", c["kept_run"] == "new" and c["dropped_run"] == "old")
check("冲突记录相对差", c["relative_delta"] > 0.2 and c["key"] == "contrast")

d = se.evidence_from_records(
    [rec(metrics={"T1_us": 72.2, "T2_us": 6.58, "xeb_p": 0.9965, "gate_fidelity": 0.9973})],
    qubit="q4ld3",
)
check("指标映射: T1_us → t1_us", d["evidence"]["t1_us"] == 72.2)
check("指标映射: T2_us → t2_star_us", d["evidence"]["t2_star_us"] == 6.58)
check("指标映射: xeb_p 与 gate_fidelity",
      d["evidence"]["xeb_p"] == 0.9965 and d["evidence"]["xeb_gate_fidelity"] == 0.9973)

d = se.evidence_from_records([rec(metrics={"contrast": "0.6"})], qubit="q4ld3")
check("字符串数值也认", d["evidence"]["contrast"] == 0.6)
d = se.evidence_from_records([rec(metrics={"contrast": True})], qubit="q4ld3")
check("布尔不算数值", "contrast" not in d["evidence"])

d = se.evidence_from_records([], qubit="q4ld3")
check("空台账不炸", d == {"evidence": {}, "sources": {}, "conflicts": [], "considered": 0,
                        "ignored": 0, "ignored_runs": []})
d = se.evidence_from_records([None, "junk", rec(metrics={"contrast": 0.5})], qubit="q4ld3")
check("垃圾条目跳过", d["evidence"].get("contrast") == 0.5)

d = se.evidence_from_records([rec(qubits=["q1ld3"], metrics={"contrast": 0.9})], qubit="q4ld3")
check("别的比特的记录不影响", d["evidence"] == {} and d["considered"] == 0)

# ── 3. 显式证据覆盖 ───────────────────────────────────────────────────────
derived = se.evidence_from_records([rec(metrics={"contrast": 0.5})], qubit="q4ld3")
ev, src = se.merge_evidence(derived, {"contrast": 0.8, "t1_us": 60.0})
check("覆盖: 显式值赢", ev["contrast"] == 0.8)
check("覆盖: 显式值来源标注 request", src["contrast"] == {"source": "request"})
check("覆盖: 新键也进来", ev["t1_us"] == 60.0)
check("覆盖: 台账证据保留", src["contrast"].get("source") == "request")
ev, src = se.merge_evidence(derived, {"contrast": None})
check("覆盖: None 不覆盖", ev["contrast"] == 0.5 and "run_id" in src["contrast"])
ev, src = se.merge_evidence(None, None)
check("覆盖: 空输入安全", ev == {} and src == {})

# ── 4. 证据来源表与缺失表 ─────────────────────────────────────────────────
check("来源表: 覆盖 7 种证据", len(qs.EVIDENCE_SOURCES) == 7, f"got={sorted(qs.EVIDENCE_SOURCES)}")
check("来源表: 每种都给产出的实验",
      all(v.get("produced_by") for v in qs.EVIDENCE_SOURCES.values()))
check("来源表: 每种都有说明", all(v.get("note") for v in qs.EVIDENCE_SOURCES.values()))
check("来源表: t1 说明提到固定 zpa",
      "zpa" in qs.EVIDENCE_SOURCES["t1_us"]["note"])
check("来源表: spectroscopy 说明提到 freq",
      "freq" in qs.EVIDENCE_SOURCES["freq_locked"]["note"])
check("缺失表: 与状态一一对应", set(qs.MISSING_FOR_STATE) == set(qs.STATES))
check("缺失表: characterized 无缺口", qs.MISSING_FOR_STATE["characterized"] == [])
check("缺失表: 未知态只缺频率", qs.MISSING_FOR_STATE["unknown"] == ["freq_locked"])
check("缺失表: 态区分后缺 T1/T2",
      qs.MISSING_FOR_STATE["state_discriminated"] == ["t1_us", "t2_star_us"])
check("缺失表: 相干后缺 XEB 两项",
      qs.MISSING_FOR_STATE["coherence_measured"] == ["xeb_p", "xeb_gate_fidelity"])
check("缺失表: 所有键都能在来源表里查到",
      all(k in qs.EVIDENCE_SOURCES for keys in qs.MISSING_FOR_STATE.values() for k in keys))

plan = qs.evidence_plan(None)
check("plan: 全表带 sources 与 missing_by_state",
      set(plan) == {"sources", "missing_by_state"})
plan = qs.evidence_plan("amp_calibrated")
check("plan: 单态给出缺什么", [m["key"] for m in plan["missing"]] == ["contrast"])
check("plan: 单态带产出实验", plan["missing"][0]["produced_by"] == ["sq.iqraw"])
check("plan: 单态带下一步", plan["next_action"] == qs.NEXT_ACTION["amp_calibrated"])
try:
    qs.evidence_plan("bogus")
    raised = False
except ValueError:
    raised = True
check("plan: 未知状态报错", raised)

# ── 5. derive_state 的 missing_detail ─────────────────────────────────────
state = qs.derive_state({"freq_locked": True})
check("derive: 只有频率 → freq_locked", state["state"] == "freq_locked")
check("derive: missing 保留老字符串", state["missing"] == ["amp_calibrated"])
check("derive: missing_detail 给实验",
      state["missing_detail"][0]["produced_by"] == qs.EVIDENCE_SOURCES["amp_calibrated"]["produced_by"])

state = qs.derive_state({"contrast": 0.74, "t1_us": 72.2, "t2_star_us": 6.58,
                         "xeb_p": 0.9965, "xeb_gate_fidelity": 0.9973})
check("derive: 全证据 → characterized", state["state"] == "characterized")
check("derive: characterized 无缺口", state["missing_detail"] == [])

state = qs.derive_state({"freq_locked": True, "contrast": 0.05})
check("derive: 弱对比度退回 freq_locked（幅度不可信）", state["state"] == "freq_locked")
check("derive: 退回后缺 amp_calibrated",
      [m["key"] for m in state["missing_detail"]] == ["amp_calibrated"])

state = qs.derive_state({"contrast": 0.05})
check("derive: 只有弱对比度连频率都不算 → unknown", state["state"] == "unknown")
check("derive: unknown 缺 freq_locked",
      [m["key"] for m in state["missing_detail"]] == ["freq_locked"])

table = qs.state_table()
check("state_table: 带证据来源表", "evidence_sources" in table and len(table["evidence_sources"]) == 7)
check("state_table: 带缺失表", table["missing_by_state"]["unknown"] == ["freq_locked"])

# 端到端：台账 → 证据 → 状态
runs = [
    rec(run_id="r3", finished_at=30.0, manifest={"functions": ["xeb"]},
        metrics={"xeb_p": 0.9965, "gate_fidelity": 0.9973}),
    rec(run_id="r2", finished_at=20.0, manifest={"functions": ["t1"]},
        metrics={"T1_us": 72.2, "T2_us": 6.58}),
    rec(run_id="r1", finished_at=10.0, manifest={"functions": ["iqraw"]},
        metrics={"contrast": 0.74}),
]
derived = se.evidence_from_records(runs, qubit="q4ld3")
ev, src = se.merge_evidence(derived, None)
state = qs.derive_state(ev)
check("端到端: 三次实验 → characterized", state["state"] == "characterized")
check("端到端: 证据来源可回溯到三次 run",
      {src["contrast"]["run_id"], src["t1_us"]["run_id"], src["xeb_p"]["run_id"]}
      == {"r1", "r2", "r3"})

# ── 6. 端点接线与只读性 ───────────────────────────────────────────────────
src_q = read("services/quantum_service/server.py")
check("server: 有 /qubit/state 路由", 'elif path == "/qubit/state":' in src_q)
check("server: 有唯一实现", src_q.count("def _handle_qubit_state") == 1)
check("server: 读台账", "_handle_qubit_state" in src_q and "RunRecordWriter().list_records" in src_q)
check("server: 用证据推导", "evidence_from_records(records" in src_q)
check("server: 显式证据可覆盖", "merge_evidence(derived, overrides)" in src_q)
check("server: 返回下一步计划", '"plan": evidence_plan(state["state"])' in src_q)
check("server: 缺 name 报 validation_error",
      "name (qubit) is required" in src_q)

_start = src_q.index("def _handle_qubit_state")
_next = src_q.index("\n    def ", _start + 10)
_body = src_q[_start:_next]
forbidden = ("self._execute(", "_write_qubit_params", "set_params", "apply_dataset", "_run_experiment")
check("server: 状态自查是只读的（不下发实验/不写寄存器）",
      not any(token in _body for token in forbidden),
      detail=str([t for t in forbidden if t in _body]))

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
