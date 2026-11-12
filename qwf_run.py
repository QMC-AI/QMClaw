"""qwf_run.py - 用 .qwf 文件跑一次量子测控实验（唯一推荐入口，2026-11-05 起）。

流程与真机 D1 一致，只是入口换成任意 .qwf：
    parse_qwf(.qwf) -> validate(requireWrites) -> 成本估算 -> run_ir -> 轮询 -> 汇报

用法：
    python qwf_run.py t1_q4ld3.qwf              # 只看计划（只解析+校验）
    python qwf_run.py t1_q4ld3.qwf --dry        # 干跑（不碰硬件）
    python qwf_run.py t1_q4ld3.qwf --if yes     # 真跑（写 DataVault）

P3 额度/票据（`docs/DSL_DESIGN.md` §13.4）：
    python qwf_run.py x.qwf --request-id 20261105-a      # 归属 auto-20261105-a，默认只能跑 1 次实验
    python qwf_run.py x.qwf --create-task --experiments 3 # 新建 task（3 次实验额度）再跑
    python qwf_run.py x.qwf --task t-20261105-213000      # 用已有 task 的额度

生成 .qwf 用 `qwf_gen.py`，例：
    python qwf_gen.py --fn sq.t1 --qubit q4ld3 --param zpa=0.0 --param delay=0,84000,6000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:3008"
Q_BASE = "http://127.0.0.1:3003"
QUEUE_BASE = "http://127.0.0.1:3009"


_QW_HINT = "<flow>.qwf"


def post(base, path, payload, timeout=120):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(base, path, timeout=30):
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def nodes_of(status):
    nodes = status.get("nodes") or status.get("workflow", {}).get("nodes") or {}
    if isinstance(nodes, list):
        return {str(n.get("id")): n for n in nodes}
    return {str(k): v for k, v in nodes.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("qwf")
    ap.add_argument("--qubit", default="")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--dry", action="store_true", help="干跑，不碰硬件")
    ap.add_argument("--if", dest="confirm", default="", help="真跑需 --if yes")
    # P3：额度作用域 = task（一次用户请求）。都不给 → 不受额度管辖（旧行为）
    ap.add_argument("--task", default="", help="归属已有的 task id（跨 qwf 累计额度）")
    ap.add_argument("--create-task", action="store_true",
                    help="先新建一个 task，再用它的额度跑")
    ap.add_argument("--request-id", default="",
                    help="归属 auto-<requestId>（同一请求多次重跑共享额度）")
    ap.add_argument("--experiments", type=int, default=1,
                    help="--create-task 时的实验额度（默认 1）")
    ap.add_argument("--objective", default="", help="task 目标描述（--create-task 用）")
    ap.add_argument("--queue", default=QUEUE_BASE, help="task_queue 地址")
    ap.add_argument("--sign-scope", default="",
                    help="签发票据并授权写：'f10:0.05,PiGate.amp:0.1'（键:允许最大变化量）"
                         "或 'auto'（用写清单里各键自带的 maxDelta）。需要 --task/--create-task")
    ap.add_argument("--sign-limit", type=int, default=1, help="票据里每个键允许的次数（默认 1）")
    ap.add_argument("--sign-by", default="human", help="票据签发人（默认 human）")
    args = ap.parse_args()

    global _QW_HINT
    _QW_HINT = args.qwf

    text = Path(args.qwf).read_text(encoding="utf-8")
    print(f"=== .qwf: {args.qwf}  ({len(text)} chars)")

    # 1) 文本前端 → IR
    parsed = post(args.base, "/workflows/parse_qwf", {"text": text})
    if not parsed.get("ok", True) or parsed.get("error"):
        print(f"  解析失败：{parsed.get('error')}")
        return 1
    ir = parsed.get("ir") or {}
    stmts = [st.get("op") for st in ir.get("pipeline", [])]
    print(f"  IR: id={ir.get('id')}  statements={stmts}")

    ctx = {}
    if args.qubit:
        ctx["qubit"] = args.qubit
    else:
        env = ir.get("env") or {}
        if env.get("qubit"):
            ctx["qubit"] = env["qubit"]
        if env.get("session"):
            ctx["session"] = env["session"]
    print(f"  context={ctx}")

    # 1.5) P3：确定 task（额度作用域）——一次用户请求一个 task，重跑共享额度
    task_id = args.task
    if args.create_task:
        created = post(args.queue, "/task", {
            "objective": args.objective or f"qwf_run {Path(args.qwf).stem}",
            "budgets": {"experiments": max(1, args.experiments)},
            "qubits": [ctx["qubit"]] if ctx.get("qubit") else [],
        }, timeout=30)
        task_id = created.get("taskId") or (created.get("task") or {}).get("id") or ""
        if not task_id:
            print(f"  建 task 失败：{created}")
            return 1
        print(f"  task 已建：{task_id}  额度={created.get('budgets') or created.get('task', {}).get('budgets')}")
    if task_id:
        ctx["taskId"] = task_id
        info = get(args.queue, f"/task/{task_id}")
        remaining = (info.get("remaining") or {})
        print(f"  task={task_id}  剩余={remaining}")
    elif args.request_id:
        ctx["requestId"] = args.request_id
        print(f"  归属 auto-{args.request_id}（该请求默认只有 1 次实验额度）")

    # 2) 静态校验（含点数量纲/预算/引用顺序）
    val = post(args.base, "/workflows/validate", {"ir": ir, "requireWrites": True})
    print(f"  validate: ok={val.get('ok')}")
    summary = val.get("summary")
    if isinstance(summary, dict):
        print("  " + json.dumps({k: summary.get(k) for k in
                                 ("hash", "statements", "points", "writes", "wallSeconds")},
                                ensure_ascii=False))
        billing = summary.get("billing") or summary.get("cost")
        if billing:
            print("  billing: " + json.dumps(billing, ensure_ascii=False)[:600])
    if not val.get("ok"):
        for err in (val.get("errors") or [])[:6]:
            print(f"    - {err.get('code')} {err.get('message')}")
        print(val.get("summaryText") or "")
        return 1
    print(val.get("summaryText") or "")

    # 2.5) 写清单（P3 §13.5）：把"这次会静默改哪些寄存器"提前亮出来
    manifest_writes = []
    manifest_hash = ""
    try:
        man = post(args.base, "/workflows/manifest", {"ir": ir}, timeout=60)
        if man.get("ok"):
            manifest_writes = (man.get("manifest") or {}).get("writes") or []
            manifest_hash = man.get("manifestHash") or ""
            desc = ", ".join(
                f"{w.get('key')}@{w.get('qubit')}×{w.get('count')}"
                + (f" ≤{w.get('maxDelta')}" if w.get("maxDelta") is not None else " 无上界")
                for w in manifest_writes) or "（无写入）"
            print(f"  写清单 {manifest_hash}: {desc}")
        else:
            print(f"  写清单计算失败：{man.get('error')}")
    except Exception as exc:      # 服务未更新到这个版本时忽略（不影响主流程）
        print(f"  写清单不可用（{exc}）")

    # 2.6) 授权票据（P3 §13.5）：把"允许改哪些键、改多少"一次签好
    if args.sign_scope:
        if not _sign_scope(args, task_id, manifest_hash, manifest_writes, ctx):
            return 1

    # 3) 硬件闸门
    qh = get(Q_BASE, "/health")
    print(f"  quantum: connected={qh.get('connected')} stuck={qh.get('stuck')} "
          f"actuation_allowed={qh.get('actuation_allowed')}")

    if args.dry:
        print("\n[干跑] 不碰硬件")
        launch = post(args.base, "/workflows/run_ir",
                      {"ir": ir, "context": ctx, "dryRun": True})
        if not launch.get("ok"):
            print(f"  启动失败：{launch.get('error')}")
            return 1
        wid = launch.get("workflowId")
        return _poll(args.base, wid, min(args.timeout, 120), 1.0, verbose=False)

    if args.confirm != "yes":
        print("\n（只打印计划。真跑加 `--if yes`，会写 DataVault 并驱动真机）")
        return 0

    wid = f"wf_qwf_{int(time.time())}"
    print(f"\n[真跑] workflowId={wid}")
    launch = post(args.base, "/workflows/run_ir",
                  {"ir": ir, "context": ctx, "workflowId": wid})
    if not launch.get("ok"):
        print(f"  启动失败：{launch.get('error')}")
        return 1
    if launch.get("status") == "queued":
        # 硬件通道被别的流程占着：不报错、不重试，排队等租约
        print(f"  已排队：前面还有 {launch.get('queuePosition')} 个流程"
              f"（lease={launch.get('leaseId')}）")
    else:
        print(f"  已拿到硬件通道（lease={launch.get('leaseId') or 'local-lock'}）")
    return _poll(args.base, wid, args.timeout, 5.0, verbose=True)


def _is_derived_key(key):
    """派生键：厂商按刚采数据重算的量（读出判别中心），不需要票据。

    与 `services/task_queue/ledger.py` 的 DERIVED_KEY_PREFIXES 保持一致。
    """
    if not key:
        return False
    return str(key).startswith("discriminator.")


def _sign_scope(args, task_id, manifest_hash, manifest_writes, ctx) -> bool:
    """建 task → 落写清单 → 签票据，一条命令搞定（P3 §13.5）。

    `--sign-scope "f10:0.05,PiGate.amp:0.1"`：键:允许的最大变化量
    `--sign-scope auto`                     ：用写清单里各键自带的 maxDelta
    """
    if not task_id:
        print("  ✗ --sign-scope 需要一个 task（加 --create-task 或 --task <id>）")
        return False
    if not manifest_writes:
        print("  ✗ 这次流程没有写清单（无写入），不用签票")
        return False

    # 1) 把写清单落到 task 上（票据必须绑定 manifestHash）
    saved = post(args.queue, f"/task/{task_id}/manifest",
                 {"manifest": {"writes": manifest_writes}}, timeout=30)
    mh = saved.get("manifestHash") or manifest_hash
    if not mh:
        print(f"  ✗ 写清单落盘失败：{saved}")
        return False
    print(f"  写清单已挂到 task：{mh}")

    # 2) 组 scope
    scope = []
    derived_writes = []
    spec = args.sign_scope.strip()
    if spec.lower() == "auto":
        for w in manifest_writes:
            key = w.get("key")
            if _is_derived_key(key):
                derived_writes.append(key)
                continue
            md = w.get("maxDelta")
            if md is None:
                print(f"  ! {key} 在写清单里没有幅度上界，跳过（不可代理）")
                continue
            scope.append({"cls": "write", "qubit": w.get("qubit"),
                          "keys": [key], "limit": args.sign_limit,
                          "maxDelta": {key: md}})
    else:
        for item in spec.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                print(f"  ✗ '{item}' 缺最大变化量，写成 键:数值（例如 {item}:0.05）")
                return False
            key, val = item.split(":", 1)
            key, val = key.strip(), val.strip()
            if _is_derived_key(key):
                print(f"  ! {key} 是派生键（厂商按数据重算），不需要票据，已忽略")
                continue
            try:
                delta = float(val)
            except ValueError:
                print(f"  ✗ '{item}' 的数值不是数字")
                return False
            scope.append({"cls": "write", "keys": [key],
                          "limit": args.sign_limit, "maxDelta": {key: delta}})
    if derived_writes:
        print(f"  · 派生键（无需票据）：{derived_writes}")
    if not scope:
        if derived_writes and len(derived_writes) == len(manifest_writes):
            print("  ✓ 这次流程只写派生键，不需要票据，跳过签票")
            return True
        print("  ✗ 没有可签发的 scope")
        return False

    # 3) 签票
    ticket = post(args.queue, f"/task/{task_id}/ticket", {
        "manifestHash": mh,
        "scope": scope,
        "grantedBy": args.sign_by,
        "expiresInSeconds": 24 * 3600,
    }, timeout=30)
    if not ticket.get("ok"):
        print(f"  ✗ 签票失败：{ticket.get('error') or ticket}")
        return False
    tinfo = ticket.get("ticket") or {}
    print(f"  ✓ 票据已签发：{tinfo.get('id')}  scope="
          f"{[s.get('keys') for s in scope]}")
    print("    （签票后写入管辖立即生效：越界的键会被 needs_human 拦下）")
    return True


def _poll(base, wid, timeout, interval, verbose=True):
    started = time.time()
    last = 0.0
    while True:
        time.sleep(interval)
        status = post(base, "/workflows/status", {"workflowId": wid})
        state = status.get("status")
        el = time.time() - started
        lease = status.get("lease") or {}
        if verbose and el - last >= 15:
            last = el
            ns = nodes_of(status)
            done = sum(1 for n in ns.values()
                       if str(n.get("status")) in ("completed", "failed", "skipped"))
            cur = [k for k, n in ns.items() if str(n.get("status")) == "running"]
            queue_note = ""
            if lease.get("status") == "queued":
                queue_note = f"  [排队中，前面 {lease.get('position')} 个流程]"
            print(f"  [{el:6.1f}s] {state}  节点 {done}/{len(ns)}"
                  + (f"  运行中: {','.join(cur)}" if cur else "") + queue_note)
        if state in ("completed", "failed", "cancelled", "budget_exceeded",
                     "needs_human", "quota_exceeded"):
            break
        if el > timeout:
            print(f"  ! 超过 {timeout}s，发取消")
            post(base, "/workflows/cancel", {"workflowId": wid})
            break

    status = post(base, "/workflows/status", {"workflowId": wid})
    print(f"\n  终态 {status.get('status')}，用时 {time.time() - started:.1f}s")
    print("  节点：")
    for nid, node in nodes_of(status).items():
        res = node.get("result") or {}
        print(f"    {nid:<18} {str(node.get('status')):<10} "
              f"dataset={node.get('dataset_id') or res.get('dataset_id')}")
        if node.get("error"):
            print(f"       error: {node['error']}")
        for key in ("contrast", "p1_i", "p1_x", "snr", "message", "value"):
            if isinstance(res, dict) and key in res:
                print(f"       {key} = {res[key]}")
    out = status.get("outputs") or status.get("bindings")
    if out:
        print("  outputs: " + json.dumps(out, ensure_ascii=False)[:1500])
    if status.get("error"):
        print(f"  workflow error: {status['error']}")
    if status.get("status") == "needs_human":
        nh = status.get("needs_human") or {}
        if not nh:
            for node in nodes_of(status).values():
                res = node.get("result") or {}
                if res.get("needs_human") or res.get("question"):
                    nh = res
                    break
        print("\n  ⏸ 等人拍板 (needs_human)：")
        print(f"     问题: {nh.get('question') or nh.get('message') or '?'}")
        opts = nh.get("options") or []
        if opts:
            print("     可选项: " + " / ".join(str(o) for o in opts))
    oc = status.get("outcome")
    if oc:
        print(f"\n  outcome: {oc.get('status')}"
              + (f" — {oc.get('reason')}" if oc.get("reason") else ""))
    if status.get("status") == "quota_exceeded":
        print("\n  ⛔ task 额度用尽（被拦下的那次实验没有下发硬件）。要再跑：")
        print(f"     python qwf_run.py {_QW_HINT} --create-task --experiments N")
    return 0 if status.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
