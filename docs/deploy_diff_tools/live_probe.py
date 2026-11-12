"""live_probe.py - 真机单步探活：用 DSL 的 run_ir 跑**一个** measure，验证 async job 通道。

这是"最小代价"的真机验证：
  * 只下发 1 个实验（通常 3-10 秒）；
  * 全程轮询 :3003/health，用来回答"实验卡住时量子服务还活着吗"；
  * 节点超时由 IR 的 timeout 控制（默认 120s），超时后解释器会 abandon job。

会**写真实数据**（DataVault 里多一个 dataset），所以默认要求显式 `--yes`。

用法：
    python docs/deploy_diff_tools/live_probe.py --fn sq.iqraw --if yes
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def post(url, payload, timeout=30):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"__http_error": exc.code, "body": exc.read().decode("utf-8", "replace")[:300]}


def health(url, timeout=5):
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        return time.time() - started, data
    except Exception as exc:  # 超时/拒绝连接都算"没响应"
        return time.time() - started, {"__error": str(exc)[:80]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fn", default="sq.iqraw")
    ap.add_argument("--qubit", default="q2ld3")
    ap.add_argument("--timeout", type=int, default=120, help="节点超时（秒）")
    ap.add_argument("--workflow-base", default="http://127.0.0.1:3008")
    ap.add_argument("--quantum-health", default="http://127.0.0.1:3003/health")
    ap.add_argument("--if", dest="confirm", default="", help="确认真机写入，需传 yes")
    ap.add_argument("--args", default="", help='JSON 参数，如 \'{"freq":{"$arange":[4.0,3.0,-0.002]}}\'')
    args = ap.parse_args()

    if args.confirm != "yes":
        print("这会在真机上跑一次实验并写入数据。确认请加 --if yes")
        return 2

    node = {
        "op": "measure",
        "as": "c1",
        "fn": args.fn,
        "qubit": "$env.qubit",
        "timeout": args.timeout,
        "note": "live probe",
    }
    if args.args:
        node["args"] = json.loads(args.args)

    ir = {
        "ir_version": 1,
        "id": "live_probe",
        "env": {"qubit": args.qubit},
        "budget": {"maxWrites": 3, "maxWallSeconds": 600},
        "pipeline": [node],
    }

    started = time.time()
    print(f"[probe] {args.fn} on {args.qubit}, node timeout={args.timeout}s")
    run = post(args.workflow_base + "/workflows/run_ir",
               {"ir": ir, "dryRun": False, "workflowId": "wf_live_probe"})
    if not run.get("ok"):
        print(f"[probe] 启动失败: {json.dumps(run, ensure_ascii=False)[:400]}")
        return 1
    wid = run.get("workflowId")
    print(f"[probe] workflowId={wid} irHash={str(run.get('irHash'))[:20]}…")

    last_status = None
    health_log = []
    elapsed = 0.0
    while time.time() - started < args.timeout + 120:
        cost, h = health(args.quantum_health)
        health_log.append((round(time.time() - started, 1), round(cost, 2),
                           h.get("ready"), h.get("busy"), h.get("stuck"),
                           h.get("running_tasks")))
        status = post(args.workflow_base + "/workflows/status", {"workflowId": wid}, timeout=20)
        state = str(status.get("status"))
        if state != last_status:
            print(f"[{time.time() - started:6.1f}s] workflow={state}")
            last_status = state
        for node in (status.get("nodes") or []):
            node_state = str(node.get("status"))
            if node_state in ("completed", "failed", "cancelled", "skipped"):
                print(f"[{time.time() - started:6.1f}s] node {node.get('id')}={node_state}"
                      f" dataset={node.get('dataset_id')} err={str(node.get('error'))[:80]}")
        if state in ("completed", "failed", "cancelled", "budget_exceeded"):
            elapsed = time.time() - started
            print(f"\n[probe] 终态 {state}，用时 {elapsed:.1f}s")
            for node in (status.get("nodes") or []):
                print(f"  - {node.get('id')}: {node.get('status')} "
                      f"dataset={node.get('dataset_id')} error={node.get('error')}")
            break
        time.sleep(2.0)
    else:
        print("[probe] 轮询窗口用尽，未等到终态")

    print("\n[health] 量子服务活性（相对时间 / 响应秒 / ready / busy / stuck / running）")
    for row in health_log:
        flag = "  <-- 无响应" if row[1] >= 4.5 else ""
        print(f"  {row[0]:6.1f}s  {row[1]:.2f}s  ready={row[2]} busy={row[3]} "
              f"stuck={row[4]} running={row[5]}{flag}")

    slow = [r for r in health_log if r[1] >= 4.5]
    print(f"\n[结论] 慢响应 {len(slow)} 次；终态 {last_status}")
    return 0 if last_status == "completed" and not slow else 1


if __name__ == "__main__":
    sys.exit(main())
