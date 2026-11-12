#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase4_check.py — 阶段 4：Metric Registry + 比特状态机接入 goal 判定。

离线自检，不连 LabRAD、不执行真机实验：
  1. metrics 层：resolve_threshold / available_levels / criteria_metric_name
  2. goal 层：criteria + level 校验、registry 阈值填充、criteriaRef 记录
  3. 文本 DSL：criteria/level 解析与打印往返
  4. executor：outcome 带 qubit_state，detail 带 criteriaRef

用法：D:/anaconda3/envs/lqcs/python.exe _phase4_check.py
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
print("_phase4_check — 阶段 4：Metric Registry + 状态机接入 goal 判定")
print("=" * 72)

# ── 1. metrics 层 ────────────────────────────────────────────────────
from services.common import metrics as mx  # noqa: E402

check("resolve_threshold contrast/min → 0.2",
      mx.resolve_threshold("contrast", "min").get("value") == 0.2,
      str(mx.resolve_threshold("contrast", "min")))
check("resolve_threshold contrast/good → 0.7",
      mx.resolve_threshold("contrast", "good").get("value") == 0.7)
check("resolve_threshold contrast/usable 别名 → unusable 档",
      mx.resolve_threshold("contrast", "usable").get("threshold_key") == "unusable")
check("resolve_threshold dmu_sig/target → alive 1.5",
      mx.resolve_threshold("dmu_sig", "target").get("value") == 1.5)
check("resolve_threshold t1/min → min_us 20.0",
      mx.resolve_threshold("t1", "min").get("value") == 20.0)
check("resolve_threshold xeb/gate → min_gate_fidelity 0.99",
      mx.resolve_threshold("xeb", "gate").get("value") == 0.99)
r_unknown = mx.resolve_threshold("no_such_metric", "min")
check("未知指标返回 error 而不抛异常",
      bool(r_unknown.get("error")) and r_unknown.get("value") is None, str(r_unknown))
r_bad = mx.resolve_threshold("contrast", "no_such_level")
check("未知档位返回 error 而不抛异常",
      bool(r_bad.get("error")) and r_bad.get("value") is None, str(r_bad))
lv = mx.available_levels("contrast")
check("available_levels 列出 contrast 三档",
      set(lv.get("levels") or []) >= {"unusable", "weak_but_usable", "good"}, str(lv))
check("criteria_metric_name 解析 $c1.contrast",
      mx.criteria_metric_name("$c1.contrast") == "contrast")
check("criteria_metric_name 解析 T1_us → t1",
      mx.criteria_metric_name("T1_us") == "t1")
check("resolve_threshold 结果带 ref",
      mx.resolve_threshold("contrast", "good").get("ref") == "contrast.good")

# ── 2. goal 层 ───────────────────────────────────────────────────────
from services.workflow_service.dsl import goal as goal_mod  # noqa: E402
from services.workflow_service.dsl import const as dsl_const  # noqa: E402

check("GOAL_SUCCESS_FIELDS 含 criteria/level",
      "criteria" in dsl_const.GOAL_SUCCESS_FIELDS and "level" in dsl_const.GOAL_SUCCESS_FIELDS)

info, errs = goal_mod._resolve_criteria({"criteria": "contrast", "level": "good"})
check("_resolve_criteria 解析 registry 阈值",
      info.get("value") == 0.7 and info.get("ref") == "contrast.good", str(info))
check("_resolve_criteria 无错", not errs, str(errs))

info2, errs2 = goal_mod._resolve_criteria({"criteria": "contrast"})
check("criteria 无 level 时默认 min 档",
      info2.get("threshold_key") == "unusable" and info2.get("value") == 0.2, str(info2))

info3, errs3 = goal_mod._resolve_criteria({"level": "good"})
check("level 单独出现报错 E_GOAL_CRITERIA",
      any(e[0] == "E_GOAL_CRITERIA" for e in errs3), str(errs3))

info4, errs4 = goal_mod._resolve_criteria({"criteria": "no_such_metric"})
check("未知指标报错 E_GOAL_CRITERIA",
      any(e[0] == "E_GOAL_CRITERIA" for e in errs4), str(errs4))

info5, errs5 = goal_mod._resolve_criteria({"criteria": "contrast", "level": "good", "value": 0.9})
check("显式 value 优先且 value_source=explicit",
      info5.get("value") == 0.9 and info5.get("value_source") == "explicit", str(info5))

BASE_IR = {
    "meta": {},
    "env": {"qubit": "q2ld3"},
    "budget": {"maxDatasets": 10, "maxWrites": 10},
    "pipeline": [
        {"op": "measure", "as": "r1", "fn": "iqraw", "qubit": "q2ld3",
         "args": {}, "params": {"do_plot": False}},
    ],
}


def _ir_with_goal(success: dict) -> dict:
    ir = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in BASE_IR.items()}
    ir["goal"] = {"objective": "提升对比度", "success": success, "maxRounds": 1}
    return ir


errs_ok = goal_mod.goal_errors(_ir_with_goal(
    {"metric": "r1.contrast", "operator": ">=", "criteria": "contrast", "level": "good"}))
check("有 criteria 时省略 value 不报错",
      not any(e[0] == "E_GOAL_METRIC" for e in errs_ok), str(errs_ok))

errs_bad = goal_mod.goal_errors(_ir_with_goal(
    {"metric": "r1.contrast", "operator": ">=", "criteria": "nope"}))
check("未知 criteria 在 goal_errors 里报 E_GOAL_CRITERIA",
      any(e[0] == "E_GOAL_CRITERIA" for e in errs_bad), str(errs_bad))

errs_none = goal_mod.goal_errors(_ir_with_goal(
    {"metric": "r1.contrast", "operator": ">="}))
check("既无 value 又无 criteria 仍报 E_GOAL_METRIC",
      any(e[0] == "E_GOAL_METRIC" for e in errs_none), str(errs_none))

lowered = goal_mod.lower_goal(_ir_with_goal(
    {"metric": "r1.contrast", "operator": ">=", "criteria": "contrast", "level": "good"}))
lowered_nodes = lowered.get("pipeline") or []
assert_node = None
for node in lowered_nodes:
    if isinstance(node, dict) and node.get("as") == "goal_assert":
        assert_node = node
check("lowering 后 goal_assert 阈值取自 registry (0.7)",
      bool(assert_node) and assert_node.get("threshold") == 0.7, str(assert_node))
check("lowering 后 assert 带 criteriaRef",
      bool(assert_node) and assert_node.get("criteriaRef") == "contrast.good", str(assert_node))
check("lowering 后 meta 记录 goal_criteria_ref",
      isinstance((lowered.get("meta") or {}).get("goal_criteria_ref"), dict))

# ── 3. 文本 DSL 往返 ────────────────────────────────────────────────
from services.workflow_service.dsl.textdsl import parser as tparser  # noqa: E402
from services.workflow_service.dsl.textdsl import printer as tprinter  # noqa: E402

check("parser.SUCCESS_KEYS 含 criteria/level",
      "criteria" in tparser._Parser.SUCCESS_KEYS and "level" in tparser._Parser.SUCCESS_KEYS)

QWF = """
workflow demo {
  env {
    qubit   "q2ld3"
  }
  goal {
    objective "提升对比度"
    success {
      metric   r1.contrast
      operator >=
      criteria contrast
      level    good
    }
    max_rounds 2
  }
  measure as c1 {
    fn      sq.iqraw
    qubit   $env.qubit
  }
  read c1 as r1 { kind IQraw threshold 0.4 }
}
"""


def _parse(text: str):
    return tparser.parse(text)


parsed = None
try:
    parsed = _parse(QWF)
except Exception as exc:  # pragma: no cover - 诊断用
    check("文本 DSL 解析 criteria/level", False, repr(exc))

if parsed is not None:
    succ = ((parsed.get("goal") or {}).get("success") or {})
    check("解析出的 success.criteria == contrast", succ.get("criteria") == "contrast", str(succ))
    check("解析出的 success.level == good", succ.get("level") == "good", str(succ))
    printed = tprinter.print_qwf(parsed)
    check("打印结果含 criteria contrast", "criteria" in printed and "contrast" in printed)
    check("打印结果含 level good", "level" in printed and "good" in printed)

# ── 4. executor outcome ─────────────────────────────────────────────
from services.workflow_service.dsl.executor import IrExecutor  # noqa: E402
import inspect as _inspect  # noqa: E402

src = _inspect.getsource(IrExecutor._compute_outcome)
check("executor 导入 qubit_state", "qubit_state" in src and "derive_state" in src)
check("executor 输出 criteriaRef", "criteriaRef" in src)

ex = IrExecutor.__new__(IrExecutor)
ex.ir = {
    "meta": {"goal_lowered": True, "goal_criteria_ref": {"ref": "contrast.good"}},
    "goal": {"objective": "x", "success": {"metric": "r1.contrast", "operator": ">=",
                                         "value": 0.7, "criteria": "contrast", "level": "good"}},
    "pipeline": [],
}
ex.bindings = {
    "r1": {"metrics": {"contrast": 0.62, "T1_us": 40.0, "T2_us": 3.0, "snr": 1.2}},
    "goal_assert": {"metric": "r1.contrast", "operator": ">=", "threshold": 0.7,
                    "value": 0.62, "passed": False, "criteriaRef": "contrast.good"},
}
ex.status = "completed"
ex.error = ""
ex.needs_human = None
ex.gate_notes = []
ex.side_effect_reports = []
out = IrExecutor._compute_outcome(ex)
check("outcome 状态为 not_achieved", out.get("status") == "not_achieved", str(out.get("status")))
check("outcome detail 带 criteriaRef", (out.get("detail") or {}).get("criteriaRef") == "contrast.good",
      str(out.get("detail")))
check("outcome 带 qubit_state", isinstance(out.get("qubit_state"), dict), str(out.get("qubit_state")))
qs = out.get("qubit_state") or {}
check("qubit_state 含 state 字段", bool(qs.get("state")), str(qs))
check("qubit_state 含 next_action", "next_action" in qs, str(qs))

print("=" * 72)
print(f"_phase4_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("FAILED:")
    for name in FAILED:
        print(f"  - {name}")
print("=" * 72)
sys.exit(1 if FAIL else 0)
