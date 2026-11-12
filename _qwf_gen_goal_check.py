"""qwf_gen.py goal 模式（native 顶层块）自检。

覆盖：
1. qwf_gen 生成的 .qwf 能被文本 DSL 解析；
2. goal 块解析正确（objective/success/maxRounds/onAchieved）；
3. normalize() 做 lowering → repeat + if/else + assert；
4. validate() 无 error；
5. manifest 写集合枚举正确（iqraw → discriminator.*，derived）；
6. plan 按 rounds 放大预算；
7. expanded 老写法仍然可解析、可 lowering（等价性）。

跑法（工作区根 D:/qmclaw）：
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 D:/anaconda3/envs/lqcs/python.exe _qwf_gen_goal_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server" / "services" / "workflow_service"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(ROOT))

import qwf_gen  # noqa: E402
from dsl import normalize, parse_qwf, plan_ir, validate  # noqa: E402
from dsl import manifest as manifest_mod  # noqa: E402
from dsl.textdsl.printer import ir_to_qwf  # noqa: E402

FAILS = []
OKS = []


def check(name, cond, detail=""):
    (OKS if cond else FAILS).append(name)
    print(("[OK]   " if cond else "[FAIL] ") + name + (f"  —— {detail}" if detail and not cond else ""))


def _args(**kw):
    import argparse

    base = dict(
        fn="sq.iqraw", qubit="q4ld3", param=[], name="", title="", out="", stamp="check",
        max_wall="10m", max_writes="4", session="20251103_LD_SQ", plot="", timeout="120",
        read="IQraw", say="", threshold="0.4", assert_="", note="", goal_objective="复现 q4ld3 的 iqraw 对比度",
        success_metric="contrast", success_op=">=", success_value="0.6", max_rounds="3",
        goal_style="native",
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _norm(text):
    ir = parse_qwf(text)
    return ir, normalize(ir)


def main() -> int:
    # 1) native 生成的 .qwf 可解析
    text = qwf_gen.build(_args(goal_style="native"))
    check("native 生成的 .qwf 含 goal 顶层块", "  goal {" in text, text[:400])
    check("native 不再手写 repeat 展开", "repeat max" not in text)
    ir = parse_qwf(text)
    goal = ir.get("goal") or {}
    check("goal.objective 非空", bool((goal.get("objective") or "").strip()), repr(goal.get("objective")))
    check("goal.maxRounds == 3", goal.get("maxRounds") == 3, repr(goal.get("maxRounds")))
    check("goal.success.metric == $r1.contrast", goal.get("success", {}).get("metric") == "$r1.contrast",
          repr(goal.get("success")))
    check("goal.success.operator == >=", goal.get("success", {}).get("operator") == ">=",
          repr(goal.get("success")))
    check("goal.success.value == 0.6", goal.get("success", {}).get("value") == 0.6,
          repr(goal.get("success")))

    # 2) lowering
    ir_n, norm = _norm(text)
    ops = [s.get("op") for s in norm.get("pipeline", [])]
    check("normalize 后 lowering 出 repeat/if/assert", ops == ["repeat", "if", "assert"], repr(ops))
    rep = next((s for s in norm["pipeline"] if s.get("op") == "repeat"), {})
    check("repeat.maxRounds == 3", rep.get("maxRounds") == 3, repr(rep.get("maxRounds")))
    check("repeat.stopWhen 判据正确",
          (rep.get("stopWhen") or {}).get("metric") == "$r1.contrast"
          and (rep.get("stopWhen") or {}).get("value") == 0.6, repr(rep.get("stopWhen")))
    check("repeat.export 含 r1", "r1" in (rep.get("export") or []), repr(rep.get("export")))
    ast_ = next((s for s in norm["pipeline"] if s.get("op") == "assert"), {})
    check("assert.onFail == continue", ast_.get("onFail") == "continue", repr(ast_))
    check("meta.goal_lowered == True", (norm.get("meta") or {}).get("goal_lowered") is True,
          repr(norm.get("meta")))

    # 3) validate 无 error
    _, diags = validate(norm)
    errs = [d for d in diags if (d.get("level") or "error") == "error"]
    check("validate 无 error", not errs, repr(errs)[:500])

    # 4) manifest 写集合
    man = manifest_mod.collect_manifest(norm)
    keys = sorted(w["key"] for w in man["writes"])
    check("manifest 写出 4 个 discriminator 键",
          keys == ["discriminator.center0", "discriminator.center1", "discriminator.method", "discriminator.radius0"],
          repr(keys))
    check("manifest 全为派生键（可代理）", all(w.get("derived") and w.get("delegable") for w in man["writes"]),
          repr(man["writes"]))
    check("manifest 轮数 == 3", man.get("rounds") == 3, repr(man.get("rounds")))

    # 5) plan 按 rounds 放大（带真实后端适配器，writeSteps 才有值）
    from dsl.backends.lqcs import LqcsAdapter
    plan = plan_ir(norm, adapter=LqcsAdapter())
    tot = plan.get("totals") or {}
    check("plan rounds == 3", tot.get("rounds") == 3, repr(tot))
    check("plan writeSteps == 3", tot.get("writeSteps") == 3, repr(tot))
    ir1 = parse_qwf(qwf_gen.build(_args(goal_style="native", max_rounds="1")))
    plan1 = plan_ir(normalize(ir1), adapter=LqcsAdapter())
    pts1 = (plan1.get("totals") or {}).get("points", 0)
    check("plan points 按轮数放大 3 倍", tot.get("points", 0) == 3 * pts1, f"{tot.get('points')} vs 3*{pts1}")

    # 6) 打印往返
    text2 = ir_to_qwf(norm)
    ir2 = parse_qwf(text2)
    check("往返后 goal 仍在", bool((ir2.get("goal") or {}).get("objective")), repr(ir2.get("goal")))

    # 7) expanded 老写法仍可用且等价
    text_exp = qwf_gen.build(_args(goal_style="expanded"))
    ir_exp = parse_qwf(text_exp)
    norm_exp = normalize(ir_exp)
    ops_exp = [s.get("op") for s in norm_exp.get("pipeline", [])]
    check("expanded 写法 lowering 出同样三件套", ops_exp == ["repeat", "if", "assert"], repr(ops_exp))
    rep_exp = next((s for s in norm_exp["pipeline"] if s.get("op") == "repeat"), {})
    check("expanded 的 stopWhen 与 native 一致",
          (rep_exp.get("stopWhen") or {}) == (rep.get("stopWhen") or {}),
          f"{rep_exp.get('stopWhen')} vs {rep.get('stopWhen')}")
    _, diags_exp = validate(norm_exp)
    errs_exp = [d for d in diags_exp if (d.get("level") or "error") == "error"]
    check("expanded 写法 validate 无 error", not errs_exp, repr(errs_exp)[:400])

    print(f"\n=== {len(OKS)} OK / {len(FAILS)} FAIL ===")
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
