"""P2 端到端干跑自检：ask → needs_human；decide → choice → if 分支。

跑法：
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
      D:/anaconda3/envs/lqcs/python.exe D:/qmclaw/_p2_flow_check.py
"""
import sys
from pathlib import Path

ROOT = Path(r"D:/qmclaw")
SERVER = ROOT / "Agentic Workflow/qmclaw-server"
sys.path.insert(0, str(SERVER))

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


from services.workflow_service.dsl import executor as EX
from services.workflow_service.dsl.textdsl import parser as TP


class FakeSvc:
    """最小 service 替身：够 IrExecutor 跑起来。

    - `_workflows` / `_cancel_events`：prepare() 要登记
    - `_execute_node`：真跑时把节点交给 server 的 `_exec_ask`
    - `_plan_ir`：返回 None，跳过 preflight 的硬件估算
    """

    def __init__(self, ask_payload=None):
        self._workflows = {}
        self._cancel_events = {}
        self._workflows_lock = None
        self.ask_payload = ask_payload or {"question": "选哪个 zpa?",
                                           "options": ["0", "0.5"]}
        from services.workflow_service import server as SRV
        self._srv = SRV.WorkflowService.__new__(SRV.WorkflowService)
        self._srv._log = lambda m: None

    def _execute_node(self, workflow, node_id, context):
        return self._srv._exec_ask(FakeNode(self.ask_payload), {})

    def _plan_ir(self, ir):
        return None


class FakeNode:
    def __init__(self, cfg):
        self.config = cfg
        self.depends = []
        self.status = None
        self.result = None
        self.started_at = None
        self.completed_at = None
        self.error = None


# ── A. ask 干跑：走通 ask 节点 ────────────────────────────────────────────
print("[A] ask 干跑")
ir_ask = TP.parse("""
workflow w_ask {
  env { qubit "q2ld3" }
  ask "请选择 zpa 扫描范围" as a1 { options ["0", "0.5"] }
  say "asked: ${a1.question}"
}
""")
ex = EX.IrExecutor(FakeSvc(), ir_ask, {}, dry_run=True)
ex.run()
check("ask 干跑不炸", ex.status in ("completed", "needs_human"), ex.status)
check("ask 绑定 question", (ex.bindings.get("a1") or {}).get("question") == "请选择 zpa 扫描范围",
      ex.bindings.get("a1"))

# ── B. decide 干跑：choice 驱动 if 分支 ──────────────────────────────────
print("[B] decide → choice → if")
ir_dec = TP.parse("""
workflow w_dec {
  env { qubit "q2ld3" }
  decide "选下一步" as d1 { mode "analysis" choices ["pidf", "set_pi"] }
  if d1.choice == "pidf" {
    say "走 pidf"
  } else {
    say "走 set_pi"
  }
}
""")
ex2 = EX.IrExecutor(FakeSvc(), ir_dec, {}, dry_run=True)
ex2.run()
check("decide 干跑不炸", ex2.status == "completed", ex2.status)
check("decide 产出 choice", (ex2.bindings.get("d1") or {}).get("choice") in ("pidf", "set_pi"),
      ex2.bindings.get("d1"))
# say 节点的输出在 bindings 里（audit 不带 stdout）
say_out = " ".join(str((b or {}).get("stdout") or "") for b in ex2.bindings.values())
check("if 分支按 choice 走", "走 pidf" in say_out or "走 set_pi" in say_out, say_out)
choice2 = (ex2.bindings.get("d1") or {}).get("choice")
check("分支与 choice 一致",
      ("走 pidf" in say_out) == (choice2 == "pidf"), f"{choice2} / {say_out}")

# ── C. 字符串比较（_compare 扩展） ───────────────────────────────────────
print("[C] _compare 字符串")
ex3 = EX.IrExecutor.__new__(EX.IrExecutor)
ex3.bindings = {"d1": {"choice": "pidf"}}
ex3.legacy = {}
ex3.env = {}
check("== 字符串", ex3._compare({"metric": "$d1.choice", "operator": "==", "value": "pidf"}) is True)
check("!= 字符串", ex3._compare({"metric": "$d1.choice", "operator": "!=", "value": "set_pi"}) is True)
check("in 集合", ex3._compare({"metric": "$d1.choice", "operator": "in",
                               "value": ["pidf", "set_pi"]}) is True)
check("not in 集合", ex3._compare({"metric": "$d1.choice", "operator": "not in",
                                   "value": ["set_pi"]}) is True)
check("contains", ex3._compare({"metric": "$d1.choice", "operator": "contains",
                                "value": "id"}) is True)

# ── D. 真跑模式：ask 节点应抛 NeedsHuman ─────────────────────────────────
print("[D] 真跑 ask → NeedsHuman")

ir_ask2 = TP.parse("""
workflow w_ask2 {
  env { qubit "q2ld3" }
  ask "选哪个 zpa?" as a1 { options ["0", "0.5"] }
}
""")
ex4 = EX.IrExecutor(FakeSvc(), ir_ask2, {}, dry_run=False)
ex4.run()
check("真跑 ask → status needs_human", ex4.status == "needs_human", ex4.status)
check("真跑 ask → needs_human 载荷", (ex4.needs_human or {}).get("question") == "选哪个 zpa?",
      ex4.needs_human)
out4 = ex4._compute_outcome()
check("真跑 ask → outcome needs_human", out4.get("status") == "needs_human", out4)

print()
print(f"==== PASS={PASS} FAIL={FAIL} ====")
sys.exit(1 if FAIL else 0)
