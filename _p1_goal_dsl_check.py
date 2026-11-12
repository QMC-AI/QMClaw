"""P1 收尾①自检：文本 DSL 的 `goal` 块（parser + printer）+ 往返等价 + lowering 生效。

纯离线，不连服务、不连硬件。
    parse → goal 结构检查 → print → 再 parse（往返等价）→ normalize(lowering) → validate
"""
import ast
import glob
import os
import sys

ROOT = r"D:/qmclaw/Agentic Workflow/qmclaw-server/services/workflow_service"
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

from dsl import normalize, validate  # noqa: E402
from dsl import parse_qwf  # noqa: E402
from dsl.textdsl.printer import ir_to_qwf  # noqa: E402

FAIL = []

GOAL_QWF = """workflow goal_dsl_test {
  meta { name "goal_dsl_test" }

  env { qubit "q4ld3" }

  budget { max_wall 300  max_writes 3  max_datasets 3 }

  goal {
    objective "复现 q4ld3 的 iqraw 对比度并判断是否漂移"
    success {
      metric   r1.contrast
      operator >=
      value    0.6
      tolerance 0.02
    }
    max_rounds   3
    on_achieved "对比度达标"
  }

  measure as c1 {
    fn      sq.iqraw
    qubit   $env.qubit
    timeout 120
  }
  read c1 as r1 { kind IQraw  threshold 0.4 }
}
"""


def check(name, cond, extra=""):
    mark = "OK " if cond else "FAIL"
    if not cond:
        FAIL.append(name)
    print(f"[{mark}] {name} {extra}")


# ── 1. 语法检查 ──────────────────────────────────────────────────
for rel in ["textdsl/parser.py", "textdsl/printer.py"]:
    p = os.path.join(ROOT, "dsl", rel)
    try:
        ast.parse(open(p, encoding="utf-8").read(), filename=p)
        check(f"ast.parse {rel}", True)
    except SyntaxError as e:
        check(f"ast.parse {rel}", False, str(e))

# ── 2. 解析 goal 块 ─────────────────────────────────────────────
try:
    ir = parse_qwf(GOAL_QWF)
    check("parse_qwf 解析 goal 文本", True)
except Exception as e:  # noqa: BLE001
    check("parse_qwf 解析 goal 文本", False, repr(e))
    ir = {}

goal = ir.get("goal") or {}
check("goal.objective 解析", goal.get("objective", "").startswith("复现"), repr(goal.get("objective")))
check("goal.maxRounds 解析", goal.get("maxRounds") == 3, repr(goal.get("maxRounds")))
check("goal.onAchieved 解析", goal.get("onAchieved") == "对比度达标", repr(goal.get("onAchieved")))
success = goal.get("success") or {}
check("goal.success.metric 带 $ 前缀", success.get("metric") == "$r1.contrast", repr(success.get("metric")))
check("goal.success.operator", success.get("operator") == ">=", repr(success.get("operator")))
check("goal.success.value", success.get("value") == 0.6, repr(success.get("value")))
check("goal.success.tolerance", success.get("tolerance") == 0.02, repr(success.get("tolerance")))

# ── 3. printer 往返等价 ─────────────────────────────────────────
try:
    text = ir_to_qwf(ir)
    check("ir_to_qwf 输出含 goal 块", "goal {" in text, repr([l for l in text.splitlines() if "goal" in l]))
    check("ir_to_qwf 输出 max_rounds", "max_rounds" in text)
    check("ir_to_qwf operator 不加引号", "operator >=" in text)
    ir2 = parse_qwf(text)
    g1 = (ir.get("goal") or {})
    g2 = (ir2.get("goal") or {})
    check("往返后 goal 完全一致", g1 == g2, f"{g1} != {g2}")
    def _strip_src(stmts):
        # source 里是行号/列号，重排版后必然不同，语义比较时要忽略
        return [{k: v for k, v in (s or {}).items() if k != "source"}
                for s in (stmts or [])]

    check("往返后 pipeline 一致",
          _strip_src(ir.get("pipeline")) == _strip_src(ir2.get("pipeline")),
          f"{_strip_src(ir.get('pipeline'))} != {_strip_src(ir2.get('pipeline'))}")
except Exception as e:  # noqa: BLE001
    check("printer 往返", False, repr(e))

# ── 4. normalize 时 lowering 生效 ───────────────────────────────
try:
    norm = normalize(ir)
    ops = [s.get("op") for s in norm.get("pipeline", [])]
    check("lowering 出 repeat/if/assert", ops == ["repeat", "if", "assert"], str(ops))
    loop = (norm.get("pipeline") or [{}])[0]
    check("repeat.maxRounds=3", loop.get("maxRounds") == 3, str(loop.get("maxRounds")))
    check("repeat.stopWhen 绑定判据",
          (loop.get("stopWhen") or {}).get("metric") == "$r1.contrast",
          str(loop.get("stopWhen")))
    check("top-level goal 仍在（供 validate 用）", "goal" in norm)
    check("meta.goal_lowered", (norm.get("meta") or {}).get("goal_lowered") is True)
except Exception as e:  # noqa: BLE001
    check("normalize lowering", False, repr(e))

# ── 5. validate 无错 ───────────────────────────────────────────
try:
    out, diags = validate(ir)
    errs = [d for d in diags if d.get("level") != "warning"]
    check("validate 无 error", not errs, str([(d.get("code"), d.get("message")) for d in errs]))
except Exception as e:  # noqa: BLE001
    check("validate", False, repr(e))

# ── 6. 既有 .qwf 回归 ───────────────────────────────────────────
for f in sorted(glob.glob(r"D:/qmclaw/*.qwf")):
    try:
        r = parse_qwf(open(f, encoding="utf-8").read())
        out, diags = validate(r)
        errs = [d for d in diags if d.get("level") != "warning"]
        check(f"回归 {os.path.basename(f)}", not errs,
              str([(d.get("code"), d.get("message")) for d in errs]))
    except Exception as e:  # noqa: BLE001
        check(f"回归 {os.path.basename(f)}", False, repr(e))

print("\n=== 汇总 ===")
print(f"FAIL {len(FAIL)}" + (f": {FAIL}" if FAIL else "（全部通过）"))
sys.exit(1 if FAIL else 0)
