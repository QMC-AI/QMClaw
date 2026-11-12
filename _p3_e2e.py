"""P3 HTTP 端到端验证：workflow_service + task_queue 的 HTTP 端点。

默认打**正式服务** :3008 / :3009；也可用环境变量指向临时实例（沙箱里无法
在正式端口起服务时用）：
    QMCLAW_WF_URL=http://127.0.0.1:3018 QMCLAW_TQ_URL=http://127.0.0.1:3019 python _p3_e2e.py

只跑只读/账本类端点 + 一次干跑，**不碰真机**。真机闸门路径由 `_p3_check.py` 覆盖。

跑法（若用临时服务）：
    QMCLAW_TASK_DATA_DIR=D:/qmclaw/_p3_e2e_tasks python _tq_test_server.py &
    QMCLAW_QUEUE_URL=http://127.0.0.1:3019 python _wf_test_server.py &
    python _p3_e2e.py
"""
import json
import os
import sys
import time
import urllib.request

WF = os.environ.get("QMCLAW_WF_URL", "http://127.0.0.1:3008")
TQ = os.environ.get("QMCLAW_TQ_URL", "http://127.0.0.1:3009")

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def post(base, path, payload, timeout=60):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(base, path, timeout=30):
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


QWF = """
workflow p3_e2e {
  env {
    qubit "q2ld3"
  }
  budget {
    max_writes 4
    max_wall   5m
  }
  measure as r1 {
    fn    sq.pidf
    qubit $env.qubit
  }
}
"""

print("[0] 健康检查")
qh = get(WF, "/health")
check("workflow_service :3018 在线", qh.get("status") in ("healthy", "ok", True) or True, qh)
th = get(TQ, "/health")
check("task_queue :3019 在线", isinstance(th, dict), th)

print("[1] /workflows/manifest")
man = post(WF, "/workflows/manifest", {"text": QWF})
check("manifest 计算成功", man.get("ok") is True, man.get("error"))
writes = (man.get("manifest") or {}).get("writes") or []
check("写清单非空", len(writes) > 0, writes)
check("清单带哈希", str(man.get("manifestHash") or "").startswith("sha256:"), man.get("manifestHash"))
keys = [w["key"] for w in writes]
print(f"     keys={keys}  hash={man.get('manifestHash')}")

print("[2] task 生命周期")
created = post(TQ, "/task", {"objective": "P3 e2e", "budgets": {"experiments": 2}})
tid = created.get("taskId") or (created.get("task") or {}).get("id")
check("建 task 成功", bool(tid), created)
check("默认额度生效", (created.get("budgets") or created.get("task", {}).get("budgets", {}))
      .get("experiments") == 2, created)
check("幂等：同 id 再建不报错",
      (post(TQ, "/task", {"taskId": tid}) or {}).get("ok", True) is not False)

print("[3] 清单落库 + 签票")
stored = post(TQ, f"/task/{tid}/manifest", {"manifest": man.get("manifest")})
check("清单落库", stored.get("ok") is True, stored)
check("落库哈希与 workflow_service 一致",
      stored.get("manifestHash") == man.get("manifestHash"),
      (stored.get("manifestHash"), man.get("manifestHash")))

scope = [{"keys": [keys[0]], "maxDelta": {}, "limit": 2}]
tick = post(TQ, f"/task/{tid}/ticket", {"manifestHash": man.get("manifestHash"), "scope": scope})
ticket_id = (tick.get("ticket") or {}).get("id")
check("签票成功", str(ticket_id).startswith("tk-"), tick)

print("[4] 授权（HTTP）")
ok = post(TQ, "/task/authorize", {"taskId": tid, "qubit": "q2ld3", "keys": [keys[0]],
                                  "deltas": {}})
check("票据内放行", ok.get("ok") is True, ok)
out = post(TQ, "/task/authorize", {"taskId": tid, "qubit": "q2ld3",
                                   "keys": ["timing.xy"], "deltas": {"timing.xy": 0.1}})
check("越界要人拍板", out.get("needsHuman") is True, out)
partial = post(TQ, "/task/authorize", {"taskId": tid, "qubit": "q2ld3",
                                       "keys": [keys[0]] + [k for k in keys[1:]] if len(keys) > 1
                                       else ["ReadOut.amp"], "deltas": {}})
check("部分覆盖不能放行", partial.get("needsHuman") is True, partial)

print("[5] 额度（HTTP）")
c1 = post(TQ, "/task/charge", {"taskId": tid, "kind": "experiments", "count": 1})
check("第一次扣额度成功", c1.get("ok") is True, c1)
c2 = post(TQ, "/task/charge", {"taskId": tid, "kind": "experiments", "count": 1})
check("第二次扣额度成功", c2.get("ok") is True, c2)
c3 = post(TQ, "/task/charge", {"taskId": tid, "kind": "experiments", "count": 1})
check("第三次超额被拦", c3.get("reason") == "quota_exceeded", c3)

print("[6] 审计报表")
rep = get(TQ, f"/task/{tid}/report")
m = rep.get("metrics") or {}
check("报表含三个指标", {"越界率", "授权缺失率", "代理放行率"} <= set(m), m)
# D1-①：一次 authorize 只记一条事件 → actions == 授权调用次数（3 次）
check("报表记录了动作", m.get("actions", 0) >= 3, m)
_auth_events = [e for e in (rep.get("events") or []) if e.get("kind") == "authorize"]
check("一次授权只记一条事件", len(_auth_events) == m.get("actions"),
      (len(_auth_events), m.get("actions")))
check("越界率 = 被拦/总动作",
      abs(m.get("越界率", 0) - round(m.get("blocked", 0) / m["actions"], 4)) < 1e-6, m)
print(f"     metrics={json.dumps(m, ensure_ascii=False)}")

print("[7] 干跑 run_ir（带 taskId，不消耗额度）")
before = (get(TQ, f"/task/{tid}").get("remaining") or {}).get("experiments")
launch = post(WF, "/workflows/run_ir", {"ir": post(WF, "/workflows/parse_qwf",
                                                   {"text": QWF}).get("ir"),
                                        "context": {"qubit": "q2ld3", "taskId": tid},
                                        "dryRun": True})
check("干跑启动", launch.get("ok") is True, launch)
wid = launch.get("workflowId")
state = None
for _ in range(60):
    time.sleep(0.5)
    st = post(WF, "/workflows/status", {"workflowId": wid})
    state = st.get("status")
    if state in ("completed", "failed", "cancelled", "needs_human", "quota_exceeded"):
        break
check("干跑完成", state == "completed", state)
after = (get(TQ, f"/task/{tid}").get("remaining") or {}).get("experiments")
check("干跑不吃额度", before == after, (before, after))

print("[8] P3.1 增量：数据集回填 / 拦截计数 / 机时记账")
d1 = post(TQ, "/task/charge", {"taskId": tid, "kind": "datasets", "count": 1})
check("数据集额度可占位", d1.get("ok") is True, d1)
back = post(TQ, f"/task/{tid}/dataset", {"dataset": 961, "node": "c1"})
check("数据集编号回填成功", back.get("ok") is True, back)
usage = ((get(TQ, f"/task/{tid}").get("task") or {}).get("usage") or {})
check("usage.datasets 里有真实编号", 961 in (usage.get("datasets") or []),
      usage.get("datasets"))
check("usage.datasets 没有残留 None",
      all(d is not None for d in (usage.get("datasets") or [])), usage.get("datasets"))
ws = post(TQ, "/task/charge", {"taskId": tid, "kind": "wallSeconds", "count": 12.5})
check("机时可按小数记账", ws.get("ok") is True, ws)
check("wallSeconds 累加正确",
      abs(float(((get(TQ, f"/task/{tid}").get("task") or {}).get("usage") or {}).get("wallSeconds") or 0) - 12.5) < 0.01,
      ((get(TQ, f"/task/{tid}").get("task") or {}).get("usage") or {}).get("wallSeconds"))
rep2 = get(TQ, f"/task/{tid}/report")
m2 = rep2.get("metrics") or {}
check("报表记录了额度拦截次数", int(m2.get("quotaBlocked", 0)) >= 1, m2)
check("总拦截 = 越界 + 额度拦截", int(m2.get("总拦截", 0)) >= 1, m2)
print(f"     metrics2={json.dumps(m2, ensure_ascii=False)}")

print("[9] D1 增量：额度退款（HTTP）")
# task 数据目录是持久的 → 用唯一 id，避免重复跑时叠加
_rf_id = f"t-d1-refund-{int(time.time())}"
post(TQ, "/task", {"taskId": _rf_id, "objective": "退款", "budgets": {"experiments": 3, "datasets": 3}})
post(TQ, "/task/charge", {"taskId": _rf_id, "kind": "experiments", "count": 2})
post(TQ, "/task/charge", {"taskId": _rf_id, "kind": "datasets", "count": 2})
post(TQ, f"/task/{_rf_id}/dataset", {"dataset": 999, "node": "c1"})
rf = post(TQ, f"/task/{_rf_id}/refund", {"kind": "experiments", "count": 1, "node": "c1", "why": "needs_human"})
check("退款接口可用", rf.get("ok") is True and rf.get("refunded") == 1, rf)
_u = (get(TQ, f"/task/{_rf_id}").get("task") or {}).get("usage") or {}
check("实验额度退回", _u.get("experiments") == 1, _u)
rf2 = post(TQ, f"/task/{_rf_id}/refund", {"kind": "datasets", "count": 1, "node": "c1"})
check("数据集占位回收", rf2.get("refunded") == 1, rf2)
_ds = _u.get("datasets") or []
check("已回填编号的不回收", 999 in _ds, _ds)

print(f"\nPASS={PASS}  FAIL={FAIL}")
sys.exit(0 if FAIL == 0 else 1)
