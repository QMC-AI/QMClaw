"""express_dsl_check.py - 验证 Express(:3002) 到 workflow_service(:3008) 的 DSL 代理链。

只做**无副作用**的检查：parse / validate / ir_from_canvas 都是纯计算，
run_ir 一律带 `dryRun: true`（synthetic 假服务，不碰硬件、不写数据）。

用法：
    python docs/deploy_diff_tools/express_dsl_check.py
    python docs/deploy_diff_tools/express_dsl_check.py --base http://127.0.0.1:3002/api
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "Agentic Workflow" / "qmclaw-server" / "data" / "workflows" / "examples"

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  -> {detail}")


def post(base, path, payload, timeout=30):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        return {"__http_error": exc.code, "body": body}


def wait(base, workflow_id, timeout=60):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = post(base, "/workflow/status", {"workflowId": workflow_id}) or {}
        if str(last.get("status")) in ("completed", "failed", "cancelled", "budget_exceeded"):
            return last
        time.sleep(0.3)
    return last


def get(base, path, timeout=30):
    request = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        return {"__http_error": exc.code, "body": body}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3002/api")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    # E0 代理本身活着：借用已有的 list 路由
    print("[E0] 代理可达")
    check("E0.1 /workflow/list 返回列表",
          isinstance(get(base, "/workflow/list").get("workflows"), list),
          str(get(base, "/workflow/list"))[:160])

    text = (EXAMPLES / "t1.qwf").read_text(encoding="utf-8")

    # E1 parse_qwf
    print("[E1] POST /workflow/parse_qwf")
    parsed = post(base, "/workflow/parse_qwf", {"text": text})
    ir = parsed.get("ir") or {}
    check("E1.1 返回 IR", bool(ir.get("pipeline")), json.dumps(parsed, ensure_ascii=False)[:160])
    check("E1.2 语句数是 4", len(ir.get("pipeline") or []) == 4,
          f"{len(ir.get('pipeline') or [])}")
    check("E1.3 解析出不带路径的 say 模板",
          (ir.get("pipeline") or [{}])[-1].get("template", "").startswith("T1 ="),
          str((ir.get("pipeline") or [{}])[-1]))

    # E1.4 语法错误要带行列
    print("[E1b] 语法错误")
    bad = post(base, "/workflow/parse_qwf", {"text": "workflow x { measure as c1 { fn } }"})
    check("E1b.1 语法错误被拒", bad.get("ok") is False or "error" in bad,
          json.dumps(bad, ensure_ascii=False)[:160])

    # E2 validate
    print("[E2] POST /workflow/validate")
    good = post(base, "/workflow/validate", {"ir": ir})
    check("E2.1 合法 IR 通过", good.get("ok") is True, json.dumps(good, ensure_ascii=False)[:160])
    check("E2.2 返回规范化 IR", bool((good.get("ir") or {}).get("pipeline")),
          "缺少 ir 字段")
    check("E2.3 有一条量纲 warning（T1_us vs 20）",
          any(d.get("code") == "E_DIMENSION" for d in (good.get("diags") or [])),
          str(good.get("warnings")))

    broken = json.loads(json.dumps(ir))
    broken["pipeline"][1]["dataset"] = "$c9.dataset"
    bad_ir = post(base, "/workflow/validate", {"ir": broken})
    codes = {d.get("code") for d in (bad_ir.get("diags") or [])}
    check("E2.4 未绑定引用被拒", bad_ir.get("ok") is False, json.dumps(bad_ir, ensure_ascii=False)[:160])
    check("E2.5 错误码是 E_UNDEFINED_REF", "E_UNDEFINED_REF" in codes, str(codes))

    # E3 ir_from_canvas
    print("[E3] POST /workflow/ir_from_canvas")
    canvas = {
        "name": "canvas_check",
        "context": {"qubit": "q2ld3"},
        "nodes": [
            {"id": "ctx", "type": "context", "config": {"variables": {"qubit": "q2ld3"}}},
            {"id": "e1", "type": "experiment", "depends": ["ctx"],
             "config": {"fn": "sq.t1", "qubit": "{{qubit}}"}},
            {"id": "a1", "type": "analyze", "depends": ["e1"],
             "config": {"datasetId": "{{e1}}", "command": "qter.fitData(do_plot=False)"}},
            {"id": "g1", "type": "quality_gate", "depends": ["a1"],
             "config": {"metric": "T1_us", "threshold": 20, "operator": ">="}},
        ],
    }
    converted = post(base, "/workflow/ir_from_canvas", canvas)
    conv_ir = converted.get("ir") or {}
    ops = [s.get("op") for s in (conv_ir.get("pipeline") or [])]
    check("E3.1 画布转出 IR", bool(ops), json.dumps(converted, ensure_ascii=False)[:200])
    check("E3.2 op 序列是 let/measure/analyze/assert",
          ops == ["let", "measure", "analyze", "assert"], str(ops))
    check("E3.3 无 error 诊断",
          not [d for d in (converted.get("diags") or []) if d.get("level") == "error"],
          str(converted.get("diags")))

    # E4 run_ir（干跑）
    print("[E4] POST /workflow/run_ir（dryRun）")
    run = post(base, "/workflow/run_ir", {"ir": ir, "dryRun": True, "dryMode": "synthetic"})
    wid = run.get("workflowId") or run.get("workflow_id")
    check("E4.1 接受并返回 workflowId", bool(wid), json.dumps(run, ensure_ascii=False)[:200])
    check("E4.2 返回 irHash", str(run.get("irHash", "")).startswith("sha256:"), str(run.get("irHash"))[:40])
    check("E4.3 标了 dryRun", run.get("dryRun") is True, str(run.get("dryRun")))
    final = wait(base, wid) if wid else {}
    check("E4.4 干跑终态 completed", str(final.get("status")) == "completed", str(final.get("status")))
    nodes = final.get("nodes") or []
    if isinstance(nodes, dict):
        nodes = list(nodes.values())
    check("E4.5 4 个节点全 completed（measure/analyze/assert/say）",
          len(nodes) == 4 and all(str(n.get("status")) == "completed" for n in nodes),
          str([(n.get("id"), n.get("status")) for n in nodes]))

    # E5 非法 IR 在 run 入口也要拒
    print("[E5] run_ir 入口校验")
    refused = post(base, "/workflow/run_ir", {"ir": broken, "dryRun": True})
    check("E5.1 未绑定引用的 IR 被拒", refused.get("ok") is False,
          json.dumps(refused, ensure_ascii=False)[:200])
    check("E5.2 拒绝时给出错误码",
          "E_UNDEFINED_REF" in {d.get("code") for d in (refused.get("diags") or [])},
          str(refused.get("errors"))[:120])

    print(f"\n{PASSED}/{PASSED + FAILED} PASS")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
