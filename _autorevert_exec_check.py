"""D3（P3.5）executor 接入自检：实验前后确实拍了快照、报告确实进了 run 记录。

不碰真机、不起服务：用假的服务对象 + 假的 SideEffectWatcher，
验证 executor 的 `_dispatch` 在 OP_MEASURE 真机路径上：
  · 下发前调 snapshot(qubit)、跑完调 after(qubit, before, node)；
  · side_effect_reports 有一份报告；
  · 报告进了 run 记录 extra 的 sideEffects；
  · gateNotes 里有副作用摘要；
  · 干跑路径不拍快照。

跑法：
  PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
    D:/anaconda3/envs/lqcs/python.exe D:/qmclaw/_autorevert_exec_check.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:/qmclaw")
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))

TMP = Path(tempfile.mkdtemp(prefix="p3-arv-"))
os.environ["QMCLAW_TASK_DATA_DIR"] = str(TMP)

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


from services.workflow_service.dsl import executor as EX  # noqa: E402


class FakeWatcher:
    """假的副作用观察者：记录调用，不碰 HTTP。"""
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.policy = {"enabled": True, "mode": "shadow"}

    def snapshot(self, qubit):
        FakeWatcher.calls.append(("snapshot", qubit))
        return {"f10": 3.62}

    def after(self, qubit, before, node_name=""):
        FakeWatcher.calls.append(("after", qubit, node_name))
        return {"node": node_name, "qubit": qubit, "mode": "shadow",
                "changes": [{"key": "f10", "before": 3.62, "after": 4.08}],
                "restored": [], "skipped": [], "notes": ["影子模式"]}


class FakeSvc:
    def __init__(self):
        self._workflows = {}
        self._cancel_events = {}
        self.calls = []
        self.persisted = None
        self._quantum_service_url = "http://localhost:3003"
        self._queue_service_url = "http://localhost:3009"
        from services.workflow_service import server as SRV
        self._srv = SRV.WorkflowService.__new__(SRV.WorkflowService)
        self._srv._log = lambda m: None

    def _queue_post(self, path, payload, timeout=10.0):
        return {}

    def _queue_get(self, path, timeout=10.0):
        return {}

    def _execute_node(self, workflow, node_id, context):
        self.calls.append(node_id)
        return {"ok": True, "status": "completed", "dataset": 901}

    def _plan_ir(self, ir):
        return None

    def _persist_workflow_run(self, workflow_id, context, extra=None):
        self.persisted = {"workflowId": workflow_id, "extra": extra}
        return {"ok": True, "runId": "run_arv"}


def make_ir():
    return {
        "irVersion": "1.0",
        "id": "arv",
        "env": {"qubit": "q2ld3"},
        "pipeline": [{"op": "measure", "fn": "sq.iqraw", "qubit": "q2ld3", "as": "m1"}],
    }


def main():
    print("=== D3 executor 接入自检 ===")
    EX.SideEffectWatcher = FakeWatcher

    # 1) 真机路径：拍快照 + 出报告
    FakeWatcher.calls = []
    svc = FakeSvc()
    ex = EX.IrExecutor(svc, make_ir(), {"qubit": "q2ld3"})
    ex.run()
    snaps = [c for c in FakeWatcher.calls if c[0] == "snapshot"]
    afters = [c for c in FakeWatcher.calls if c[0] == "after"]
    check("实验前拍了快照", len(snaps) == 1 and snaps[0][1] == "q2ld3", FakeWatcher.calls)
    check("实验后做了副作用比对", len(afters) == 1 and afters[0][2] == "m1", FakeWatcher.calls)
    check("报告进了 side_effect_reports", len(ex.side_effect_reports) == 1, ex.side_effect_reports)
    check("gateNotes 里有副作用摘要",
          any(n.get("gate") == "autorevert" for n in ex.gate_notes), ex.gate_notes)
    check("run 记录 extra 带 sideEffects",
          isinstance(svc.persisted, dict)
          and "sideEffects" in (svc.persisted.get("extra") or {}),
          svc.persisted)
    check("_report() 带 sideEffects", "sideEffects" in ex._report())

    # 2) 干跑路径：不拍快照
    FakeWatcher.calls = []
    svc2 = FakeSvc()
    ex2 = EX.IrExecutor(svc2, make_ir(), {"qubit": "q2ld3"}, dry_run=True)
    ex2.run()
    check("干跑不拍快照", not FakeWatcher.calls, FakeWatcher.calls)

    print(f"\nPASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
