"""P2 剩余三件事的自检：decide→choice / ask→needs_human / 文本 DSL 语法。

跑法：
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
      D:/anaconda3/envs/lqcs/python.exe D:/qmclaw/_p2_ask_choice_check.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(r"D:/qmclaw")
SERVER = ROOT / "Agentic Workflow/qmclaw-server" / "services" / "workflow_service"
sys.path.insert(0, str(SERVER.parent.parent))

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def load(mod_path, name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── 0. 语法检查 ──────────────────────────────────────────────────────────
print("[0] ast.parse")
for rel in ["dsl/const.py", "dsl/compile.py", "dsl/executor.py", "dsl/textdsl/parser.py",
            "dsl/textdsl/printer.py", "server.py"]:
    p = SERVER / rel
    try:
        ast.parse(p.read_text(encoding="utf-8"))
        check(f"ast {rel}", True)
    except Exception as exc:
        check(f"ast {rel}", False, repr(exc))

# ── 1. const 白名单 ──────────────────────────────────────────────────────
print("[1] const OP_FIELDS")
from services.workflow_service.dsl import const as C
check("OP_DECIDE 含 choices", "choices" in C.OP_FIELDS[C.OP_DECIDE])
check("OP_ASK 含 question", "question" in C.OP_FIELDS[C.OP_ASK])
check("OP_ASK 含 options", "options" in C.OP_FIELDS[C.OP_ASK])
check("OP_ASK 含 blocking", "blocking" in C.OP_FIELDS[C.OP_ASK])

# ── 2. compile：ask → type=ask；decide → choices 进 config ──────────────
print("[2] compile")
from services.workflow_service.dsl import compile as CP


def _build(stmt):
    return CP.build_node(stmt, "n1", resolve=lambda v: v, env={})


ask_node = _build({"op": "ask", "question": "用哪个 zpa?"})
check("ask(question) → type=ask",
      bool(ask_node) and ask_node.get("type") == "ask",
      ask_node)
check("ask(question) → config.question",
      bool(ask_node) and ask_node.get("config", {}).get("question") == "用哪个 zpa?")
check("ask(question) → config.blocking 默认 True",
      bool(ask_node) and ask_node.get("config", {}).get("blocking") is True)

llm_node = _build({"op": "ask", "messages": [{"role": "user", "content": "hi"}]})
check("ask(无 question) → 仍走 llm",
      bool(llm_node) and llm_node.get("type") == "llm", llm_node)

decide_node = _build({"op": "decide", "intent": "选下一步",
                      "choices": ["pidf", "set_pi"]})
check("decide → config.choices",
      bool(decide_node) and decide_node.get("config", {}).get("choices") == ["pidf", "set_pi"],
      decide_node)

# ── 3. 文本 DSL：decide/ask 语法 ────────────────────────────────────────
print("[3] 文本 DSL")
from services.workflow_service.dsl.textdsl import parser as TP
from services.workflow_service.dsl.textdsl import printer as PR

qwf = """
workflow w_ask {
  meta { name "ask demo" }
  env { qubit "q2ld3" }
  ask "请选择 zpa 扫描范围" as a1 { options ["0", "0.5"] blocking true }
  decide "下一步做什么" as d1 { mode "analysis" choices ["pidf", "set_pi"] }
}
"""
ir = TP.parse(qwf)
pipeline = ir.get("pipeline") or []
ask_stmt = next((s for s in pipeline if s.get("op") == "ask"), None)
decide_stmt = next((s for s in pipeline if s.get("op") == "decide"), None)
check("parse ask → question", bool(ask_stmt) and ask_stmt.get("question") == "请选择 zpa 扫描范围", ask_stmt)
check("parse ask → options", bool(ask_stmt) and ask_stmt.get("options") == ["0", "0.5"], ask_stmt)
check("parse ask → blocking", bool(ask_stmt) and ask_stmt.get("blocking") is True, ask_stmt)
check("parse decide → choices",
      bool(decide_stmt) and decide_stmt.get("choices") == ["pidf", "set_pi"], decide_stmt)

# printer 往返
out = PR.ir_to_qwf(ir)
ir2 = TP.parse(out)
ask2 = next((s for s in (ir2.get("pipeline") or []) if s.get("op") == "ask"), None)
decide2 = next((s for s in (ir2.get("pipeline") or []) if s.get("op") == "decide"), None)
check("printer 往返 ask.question", bool(ask2) and ask2.get("question") == "请选择 zpa 扫描范围", ask2)
check("printer 往返 ask.options", bool(ask2) and ask2.get("options") == ["0", "0.5"], ask2)
check("printer 往返 decide.choices",
      bool(decide2) and decide2.get("choices") == ["pidf", "set_pi"], decide2)

# ── 4. executor：NeedsHuman 异常 + 白名单 ───────────────────────────────
print("[4] executor")
from services.workflow_service.dsl import executor as EX
check("executor 有 NeedsHuman", hasattr(EX, "NeedsHuman"))
wrapped = EX.wrap_result({"status": "needs_human", "needs_human": True,
                           "question": "Q?", "options": ["a"]}, 0.0, 1.0)
check("wrap_result 保留 needs_human", wrapped.get("needs_human") is True)
check("wrap_result 保留 question", wrapped.get("question") == "Q?")
check("wrap_result 保留 options", wrapped.get("options") == ["a"])
check("wrap_result needs_human 不算失败", wrapped.get("ok") is True)
w2 = EX.wrap_result({"status": "completed", "choice": "pidf", "choices": ["pidf"]}, 0.0, 1.0)
check("wrap_result 保留 choice", w2.get("choice") == "pidf")
check("wrap_result 保留 choices", w2.get("choices") == ["pidf"])

# ── 5. server：_exec_ask / NodeStatus / decision choices ───────────────
print("[5] server")
srv_src = (SERVER / "server.py").read_text(encoding="utf-8")
check("NodeStatus.NEEDS_HUMAN 存在", "NEEDS_HUMAN = \"needs_human\"" in srv_src)
check("_exec_ask 存在", "def _exec_ask" in srv_src)
check("_builtin_executors 注册 ask", '"ask": self._exec_ask' in srv_src)
check("decision 分支读 choices", 'choices = config.get("choices")' in srv_src)
check("decision 校验 choice 白名单", "not in choices" in srv_src)
check("decision 返回 choice", '"choice": choice' in srv_src)

# 用假 service 调用 _exec_ask
from services.workflow_service import server as SRV


class FakeNode:
    def __init__(self, cfg):
        self.config = cfg
        self.depends = []


inst = SRV.WorkflowService.__new__(SRV.WorkflowService)
inst._log = lambda m: None
res = inst._exec_ask(FakeNode({"question": "选哪个?", "options": ["a", "b"]}), {})
check("_exec_ask → status needs_human", res.get("status") == "needs_human", res)
check("_exec_ask → needs_human True", res.get("needs_human") is True)
check("_exec_ask → question", res.get("question") == "选哪个?")
check("_exec_ask → options", res.get("options") == ["a", "b"])
res2 = inst._exec_ask(FakeNode({}), {})
check("_exec_ask 缺 question → failed", res2.get("status") == "failed", res2)

# ── 6. outcome：needs_human 判定 ───────────────────────────────────────
print("[6] outcome")
ex = EX.IrExecutor.__new__(EX.IrExecutor)
ex.ir = {"meta": {}}
ex.bindings = {"a1": {"status": "needs_human", "question": "Q?", "options": ["x"]}}
ex.status = "needs_human"
ex.error = "Q?"
ex.needs_human = {"question": "Q?", "options": ["x"]}
out = ex._compute_outcome()
check("outcome needs_human", out.get("status") == "needs_human", out)
check("outcome 带 question", (out.get("detail") or {}).get("question") == "Q?", out)

ex2 = EX.IrExecutor.__new__(EX.IrExecutor)
ex2.ir = {"meta": {}}
ex2.bindings = {}
ex2.status = "needs_human"
ex2.error = "等人"
ex2.needs_human = {"question": "选哪个 zpa?"}
out2 = ex2._compute_outcome()
check("outcome 仅靠 self.status 也能判 needs_human",
      out2.get("status") == "needs_human", out2)

# ── 7. 既有 .qwf 回归 ──────────────────────────────────────────────────
print("[7] 既有 .qwf 回归")
import importlib
V = importlib.import_module("services.workflow_service.dsl.validate")
for f in sorted(ROOT.glob("*.qwf")):
    try:
        ir = TP.parse_file(str(f))
        norm, diags = V.validate(ir, require_writes=False)
        errs = [d for d in diags if d.get("level") == "error"]
        check(f"validate {f.name}", not errs, errs[:1])
    except Exception as exc:
        check(f"validate {f.name}", False, repr(exc))

print()
print(f"==== PASS={PASS} FAIL={FAIL} ====")
sys.exit(1 if FAIL else 0)
