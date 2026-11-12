"""_p2_outcome_check.py - P2 自检：outcome 契约（achieved / not_achieved / ...）。

覆盖：
  1) ast.parse：dsl/executor.py、workflow_service/server.py
  2) _compute_outcome 的 7 种判定
  3) _report 带 outcome 字段
  4) _finalize 把 outcome 写进 workflow.settings

运行：
  PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 D:/anaconda3/envs/lqcs/python.exe _p2_outcome_check.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

SERVER = pathlib.Path(r"D:/qmclaw/Agentic Workflow/qmclaw-server")
sys.path.insert(0, str(SERVER))

PASS, FAIL = 0, 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def make(ir_extra=None, status="completed", bindings=None, error=None):
    from services.workflow_service.dsl.executor import IrExecutor

    ir = {"ir_version": 1, "id": "t", "pipeline": []}
    if ir_extra:
        ir.update(ir_extra)
    ex = IrExecutor(None, ir, {}, dry_run=True)
    ex.status = status
    ex.error = error
    if bindings:
        ex.bindings.update(bindings)
    return ex


def main() -> int:
    print("== 1) ast.parse ==")
    for rel in ("services/workflow_service/dsl/executor.py",
                "services/workflow_service/server.py"):
        try:
            ast.parse((SERVER / rel).read_text(encoding="utf-8"))
            check(f"ast.parse {rel}", True)
        except Exception as exc:
            check(f"ast.parse {rel}", False, repr(exc))

    print("== 2) _compute_outcome 判定 ==")
    goal = {"objective": "对比度达标", "success": {"metric": "$r1.contrast",
                                                  "operator": ">=", "value": 0.6},
            "maxRounds": 3}

    ex = make()
    check("无 goal 且完成 → achieved", ex._compute_outcome()["status"] == "achieved",
          str(ex._compute_outcome()))

    ex = make({"goal": goal}, bindings={"goal_assert": {"passed": True, "metric": "contrast",
                                                         "value": 0.72, "threshold": 0.6}})
    got = ex._compute_outcome()
    check("有 goal 且门禁通过 → achieved", got["status"] == "achieved", str(got))

    ex = make({"goal": goal}, bindings={"goal_assert": {"passed": False, "metric": "contrast",
                                                         "value": 0.53, "threshold": 0.6}})
    got = ex._compute_outcome()
    check("有 goal 且门禁未过 → not_achieved", got["status"] == "not_achieved", str(got))
    check("not_achieved 带门禁细节",
          got["detail"].get("value") == 0.53 and got["detail"].get("threshold") == 0.6,
          str(got))

    ex = make({"goal": goal}, status="budget_exceeded", error="maxDatasets 3 耗尽")
    got = ex._compute_outcome()
    check("预算耗尽 → budget_exhausted", got["status"] == "budget_exhausted", str(got))

    ex = make({"goal": goal}, status="failed", error="executor crashed: boom")
    got = ex._compute_outcome()
    check("执行失败 → error", got["status"] == "error", str(got))

    ex = make({"goal": goal}, bindings={"ask1": {"status": "needs_human",
                                                  "message": "目标不明确：调大还是调小功率？"}})
    got = ex._compute_outcome()
    check("需要人工 → needs_human", got["status"] == "needs_human", str(got))

    ex = make({"goal": goal})
    got = ex._compute_outcome()
    check("有 goal 但门禁没跑 → not_achieved", got["status"] == "not_achieved", str(got))

    print("== 3) _report 带 outcome ==")
    ex = make({"goal": goal}, bindings={"goal_assert": {"passed": False}})
    rep = ex._report()
    check("_report 含 outcome", isinstance(rep.get("outcome"), dict), str(rep.get("outcome")))
    check("_report outcome 状态正确",
          (rep.get("outcome") or {}).get("status") == "not_achieved", str(rep.get("outcome")))

    print("== 4) _finalize 写 workflow.settings ==")

    class FakeWorkflow:
        def __init__(self):
            self.settings = {}
            self.nodes = {}
            self.status = "completed"
            self.error = None
            self.completed_at = None

    ex = make({"goal": goal}, bindings={"goal_assert": {"passed": True}})
    ex.workflow = FakeWorkflow()

    class FakeStatus:
        COMPLETED = "completed"
        CANCELLED = "cancelled"
        FAILED = "failed"
        SKIPPED = "skipped"
        RUNNING = "running"
        PENDING = "pending"
        BUDGET_EXCEEDED = "budget_exceeded"

    ex._NS = FakeStatus
    ex._finalize()
    check("workflow.settings['outcome'] 已写",
          (ex.workflow.settings.get("outcome") or {}).get("status") == "achieved",
          str(ex.workflow.settings))

    print(f"\n合计：PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
