"""一键跑通种子工作流 wf_contrast_tuning（态区分迭代调优）。

在真实硬件上按 workflow 页面的同一条路径执行：
  context → iqraw → contrast → piamp → iqraw → contrast → pidf → iqraw → contrast → piamp → iqraw → contrast → print

用法：
  python docs/deploy_diff_tools/run_contrast_workflow.py [--qubit q2ld3] [--timeout 900]

只走 Express :3002 的 /api/workflow/run（与前端按钮完全同一条链路），不直连 :3008。
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

# Windows 控制台默认 GBK，工作流名里有 emoji，输出会炸
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXPRESS = "http://127.0.0.1:3002"
ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "Agentic Workflow/qmclaw-server/data/workflows/wf_contrast_tuning.json"


def post(path, payload, timeout=30):
    req = urllib.request.Request(
        EXPRESS + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qubit", default="q2ld3")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    wf = json.loads(SEED.read_text(encoding="utf-8"))
    deps = {}
    for e in wf["edges"]:
        deps.setdefault(e["target"], []).append(e["source"])

    nodes = []
    context = {}
    for n in wf["nodes"]:
        cfg = dict(n["config"])
        if n["type"] == "context":
            context.update({k: str(v) for k, v in (cfg.get("variables") or {}).items()})
            continue
        nodes.append(
            {
                "id": n["id"],
                "type": n["type"],
                "depends": deps.get(n["id"], []),
                "config": cfg,
            }
        )
    context["qubit"] = args.qubit

    print(f"[run] workflow={wf['name']} qubit={args.qubit} nodes={len(nodes)}")
    res = post("/api/workflow/run", {"name": wf["name"], "nodes": nodes, "context": context})
    wf_id = res.get("workflowId") or res.get("workflow_id") or res.get("id")
    print("[run] workflowId =", wf_id)
    if not wf_id:
        print("[run] submit failed:", res)
        return 1

    started = time.time()
    last = ""
    final = None
    while time.time() - started < args.timeout:
        time.sleep(3)
        try:
            st = post("/api/workflow/status", {"workflowId": wf_id}, timeout=30)
        except Exception as e:  # 服务短暂不可达时继续等
            print(f"[poll] {type(e).__name__}: {e}")
            continue
        status = st.get("status")
        raw = st.get("nodes") or {}
        if isinstance(raw, list):
            raw = {n.get("nodeId") or n.get("id"): n for n in raw}
        line = " | ".join(f"{k}:{(v.get('status') if isinstance(v, dict) else v)}" for k, v in raw.items())
        if line != last:
            print(f"[{time.time() - started:5.0f}s] {status} :: {line}")
            last = line
        if status in ("completed", "failed", "cancelled", "error"):
            final = st
            break

    if not final:
        print("[run] TIMEOUT waiting for workflow")
        return 2

    print("\n===== per-node results =====")
    raw = final.get("nodes") or {}
    if isinstance(raw, list):
        raw = {n.get("nodeId") or n.get("id"): n for n in raw}
    for nid, node in raw.items():
        if not isinstance(node, dict):
            continue
        out = node.get("output") or node.get("result") or {}
        if isinstance(out, dict) and "stdout" in out and isinstance(out.get("stdout"), str):
            out = dict(out)
            out["stdout"] = out["stdout"][-200:]
        m = (out.get("metrics") if isinstance(out, dict) else None) or {}
        if node.get("nodeType") == "contrast" or node.get("type") == "contrast":
            print(
                f"{nid:<6} contrast={out.get('contrast')} passed={out.get('passed')} "
                f"dataset={out.get('dataset_id')} metrics={json.dumps(m, ensure_ascii=False)}"
            )
        elif node.get("nodeType") == "experiment" or node.get("type") == "experiment":
            print(f"{nid:<6} experiment dataset={out.get('dataset_id')} err={out.get('error')}")
        else:
            print(f"{nid:<6} {node.get('nodeType') or node.get('type')} -> {str(out)[:160]}")

    contrasts = [
        (nid, (n.get("output") or n.get("result") or {}).get("contrast"))
        for nid, n in raw.items()
        if isinstance(n, dict) and (n.get("nodeType") == "contrast" or n.get("type") == "contrast")
    ]
    print("\n===== contrast 轨迹 =====")
    for nid, c in contrasts:
        print(f"  {nid}: {c}")
    print("\nfinal status:", final.get("status"))
    return 0 if final.get("status") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
