"""P1 离线自检：goal 块 lowering / 校验 / 预算放大。

不连服务、不连硬件，只跑纯函数：
    normalize → validate → plan
"""
import ast
import glob
import json
import os
import sys

ROOT = r"D:/qmclaw/Agentic Workflow/qmclaw-server/services/workflow_service"
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

from dsl import normalize, validate, plan_ir, format_plan  # noqa: E402
from dsl import parse_qwf  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    mark = "OK " if cond else "FAIL"
    if not cond:
        FAIL.append(name)
    print(f"[{mark}] {name} {extra}")


# ── 1. 语法检查（P1 改过的 5 个文件）──────────────────────────────
for rel in ["const.py", "ir.py", "validate.py", "plan.py", "goal.py"]:
    p = os.path.join(ROOT, "dsl", rel)
    try:
        ast.parse(open(p, encoding="utf-8").read(), filename=p)
        check(f"ast.parse {rel}", True)
    except SyntaxError as e:
        check(f"ast.parse {rel}", False, str(e))

# ── 2. goal lowering ────────────────────────────────────────────
IR = {
    "ir_version": 1,
    "id": "p1_selftest",
    "budget": {"maxWrites": 3, "maxDatasets": 3, "maxWallSeconds": 300},
    "goal": {
        "objective": "复现 q4ld3 的 iqraw 对比度并判断是否漂移",
        "success": {"metric": "$c1.contrast", "operator": ">=", "value": 0.6},
        "maxRounds": 3,
    },
    "pipeline": [
        {"op": "measure", "as": "c1", "fn": "sq.iqraw", "qubit": "q4ld3",
         "args": {"qubit": "q4ld3", "do_plot": False}},
        {"op": "read", "as": "r1", "kind": "IQraw", "dataset": "$c1", "threshold": 0.4},
    ],
}

norm = normalize(IR)
ops = [s.get("op") for s in norm["pipeline"]]
check("lowering 生成 repeat/if/assert", ops == ["repeat", "if", "assert"], str(ops))
loop = norm["pipeline"][0]
check("repeat.maxRounds=3", loop.get("maxRounds") == 3, str(loop.get("maxRounds")))
check("repeat.stopWhen 绑定判据",
      loop.get("stopWhen", {}).get("metric") == "$c1.contrast"
      and loop.get("stopWhen", {}).get("value") == 0.6,
      json.dumps(loop.get("stopWhen"), ensure_ascii=False))
check("repeat.export 含判据根绑定 c1", "c1" in (loop.get("export") or []), str(loop.get("export")))
check("assert onFail=continue（未达成不算失败）",
      norm["pipeline"][2].get("onFail") == "continue", str(norm["pipeline"][2].get("onFail")))
check("顶层 goal 仍保留（便于审计/报告）", isinstance(norm.get("goal"), dict))
check("meta.goal_lowered 标记", norm.get("meta", {}).get("goal_lowered") is True)

# 幂等：再 normalize 一次不能套两层循环
norm2 = normalize(norm)
check("lowering 幂等（不套两层）",
      [s.get("op") for s in norm2["pipeline"]] == ["repeat", "if", "assert"],
      str([s.get("op") for s in norm2["pipeline"]]))

# ── 3. validate ─────────────────────────────────────────────────
ir_v, diags = validate(IR)
errs = [d for d in diags if d.get("level") == "error"]
check("合法 goal 无 error", not errs, json.dumps(errs, ensure_ascii=False)[:400])

BAD = json.loads(json.dumps(IR))
BAD["goal"]["success"]["metric"] = "$nope.contrast"
_, diags = validate(BAD)
codes = [d["code"] for d in diags]
check("未知绑定 → E_GOAL_METRIC", "E_GOAL_METRIC" in codes, str(codes))

BAD2 = json.loads(json.dumps(IR))
BAD2["goal"]["maxRounds"] = 99
_, diags = validate(BAD2)
codes = [d["code"] for d in diags]
check("maxRounds 越界 → E_GOAL_BUDGET_MISMATCH", "E_GOAL_BUDGET_MISMATCH" in codes, str(codes))

BAD3 = json.loads(json.dumps(IR))
BAD3["goal"]["objective"] = "  "
_, diags = validate(BAD3)
codes = [d["code"] for d in diags]
check("空 objective → E_GOAL_OBJECTIVE", "E_GOAL_OBJECTIVE" in codes, str(codes))

BAD4 = json.loads(json.dumps(IR))
BAD4["budget"]["maxDatasets"] = 2  # 3 轮 × 1 实验 = 3 > 2
_, diags = validate(BAD4)
codes = [d["code"] for d in diags]
check("轮数×采集数 > maxDatasets → E_GOAL_BUDGET_MISMATCH",
      "E_GOAL_BUDGET_MISMATCH" in codes, str(codes))

BAD5 = json.loads(json.dumps(IR))
BAD5["goal"]["success"]["value"] = "0.6"  # 非数值
_, diags = validate(BAD5)
codes = [d["code"] for d in diags]
check("value 非数值 → E_GOAL_METRIC", "E_GOAL_METRIC" in codes, str(codes))

# ── 4. plan 轮数放大 ────────────────────────────────────────────
class FakeAdapter:
    def estimate_points(self, fn, params):
        return {"points": 3000, "note": "", "defaults": []}

    def write_effects(self, fn, params):
        return {"mode": "default_on", "effective": True, "writes": ["discriminator.center0"],
                "reason": "iqraw 默认重写判别中心"}

    def estimate_runtime(self, fn, params):
        return {"seconds": 20.0, "adaptive": False, "confidence": "high"}

plan = plan_ir(norm, FakeAdapter())
tot = plan["totals"]
check("plan steps 含 rounds=3", plan["steps"][0]["rounds"] == 3, str(plan["steps"][0].get("rounds")))
check("plan 点数按轮放大 (3000×3)", tot["points"] == 9000, str(tot["points"]))
check("plan 秒数按轮放大 (20×3)", tot["seconds"] == 60.0, str(tot["seconds"]))
check("plan writeSteps 按轮放大 (1×3)", tot["writeSteps"] == 3, str(tot["writeSteps"]))
print(format_plan(plan))

# 无 goal 的普通 IR：倍数必须是 1（回归保护）
plain = normalize({
    "ir_version": 1,
    "id": "p1_selftest_plain",
    "budget": {"maxWrites": 3, "maxDatasets": 3, "maxWallSeconds": 300},
    "pipeline": IR["pipeline"],
})
plan_plain = plan_ir(plain, FakeAdapter())
check("无 goal 时不放大", plan_plain["totals"]["points"] == 3000
      and plan_plain["totals"]["writeSteps"] == 1,
      json.dumps(plan_plain["totals"], ensure_ascii=False))

# ── 5. 既有 .qwf 回归（parse + validate）─────────────────────────
files = sorted(glob.glob(r"D:/qmclaw/*.qwf"))
for f in files[:5]:
    try:
        text = open(f, encoding="utf-8").read()
        ir = parse_qwf(text)
        ir2, ds = validate(ir)
        errs = [d for d in ds if d.get("level") == "error"]
        check(f"回归 {os.path.basename(f)}", not errs,
              json.dumps([d.get("code") for d in errs], ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        check(f"回归 {os.path.basename(f)}", False, f"{type(e).__name__}: {e}")

print("\n=== " + ("全部通过" if not FAIL else f"失败 {len(FAIL)}: {FAIL}") + " ===")
sys.exit(1 if FAIL else 0)
