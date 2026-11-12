"""D4：派生键豁免（discriminator.*）的 HTTP 验证。

场景：
  1) 只写派生键 → 直接放行（by=derived），不需要票据。
  2) 派生键 + 真实键（f10 在票内）→ 只用真实键判定 → 放行。
  3) 派生键 + 真实键（f10 不在票内）→ 仍应被拦（partial_scope/out_of_scope），
     但拦截原因不能是 discriminator.*。
  4) 有 f10 票时，同流程里的 sq.iqraw（只写 discriminator.*）不应卡死流程。
"""
import json
import time
import urllib.request

TQ = "http://127.0.0.1:3019"


def post(path, payload):
    req = urllib.request.Request(
        TQ + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


PASS = 0
FAIL = 0


def check(name, cond, extra=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}   {extra}")


def new_task(tag):
    tid = post("/task", {"objective": f"derived-key probe {tag} {int(time.time()*1000)}"})["taskId"]
    return tid


DERIVED = ["discriminator.center0", "discriminator.center1", "discriminator.method", "discriminator.radius0"]

print("[1] 只写派生键 → 自动放行（不需要票据）")
tid = new_task("only-derived")
res = post("/task/authorize", {"taskId": tid, "qubit": "q2ld3", "keys": DERIVED, "deltas": {}, "enforce": True})
check("放行", res.get("ok") is True, res)
check("authorizedBy=derived", res.get("authorizedBy") == "derived", res)
check("reasonCode=derived_keys", res.get("reasonCode") == "derived_keys", res)
get = urllib.request.urlopen(TQ + "/task/" + tid + "/report", timeout=15)
task = json.loads(get.read().decode("utf-8"))
evs = [e for e in (task.get("events") or []) if e.get("kind") == "authorize"]
check("记了 1 条 authorize 事件", len(evs) == 1, evs)
check("事件 by=derived", evs and evs[0].get("by") == "derived", evs)
check("needsHuman=False", res.get("needsHuman") is False, res)

print("[2] 派生键 + 票内真实键 f10 → 放行")
tid = new_task("derived+f10")
mh = post("/task/%s/manifest" % tid, {"manifest": {"writes": [
    {"key": "f10", "qubit": "q2ld3", "maxDelta": 0.05},
    {"key": "discriminator.center0", "qubit": "q2ld3", "maxDelta": None},
]}})
hash_val = mh.get("manifestHash") or mh.get("hash")
tk = post("/task/%s/ticket" % tid, {"manifestHash": hash_val,
                                    "scope": [{"cls": "write", "qubit": "q2ld3", "keys": ["f10"], "maxDelta": {"f10": 0.05}, "limit": 1}],
                                    "grantedBy": "human"})
check("票据已签发", bool((tk.get("ticket") or {}).get("id") or tk.get("ticketId") or tk.get("id")), tk)
res = post("/task/authorize", {"taskId": tid, "qubit": "q2ld3",
                               "keys": ["f10"] + DERIVED, "deltas": {"f10": 0.01}})
check("放行", res.get("ok") is True, res)
check("authorizedBy 是 agent-delegated", str(res.get("authorizedBy", "")).startswith("agent-delegated"), res)

print("[3] 派生键 + 票外真实键 g1 → 拦截，原因不指向 discriminator.*")
res = post("/task/authorize", {"taskId": tid, "qubit": "q2ld3",
                               "keys": ["g1"] + DERIVED, "deltas": {}})
check("被拦", res.get("ok") is not True, res)
check("needsHuman=True", res.get("needsHuman") is True, res)
check("reasonCode 不在派生键上", "discriminator" not in json.dumps(res, ensure_ascii=False), res)

print("[4] 有 f10 票时 sq.iqraw（只写 discriminator.*）不卡死")
tid = new_task("iqraw-with-f10-ticket")
mh = post("/task/%s/manifest" % tid, {"manifest": {"writes": [
    {"key": "f10", "qubit": "q2ld3", "maxDelta": 0.05}]}})
hash_val = mh.get("manifestHash") or mh.get("hash")
post("/task/%s/ticket" % tid, {"manifestHash": hash_val,
                               "scope": [{"cls": "write", "qubit": "q2ld3", "keys": ["f10"], "maxDelta": {"f10": 0.05}, "limit": 1}],
                               "grantedBy": "human"})
res = post("/task/authorize", {"taskId": tid, "qubit": "q2ld3", "keys": DERIVED, "deltas": {}})
check("iqraw 不被拦", res.get("ok") is True, res)
check("needsHuman=False", res.get("needsHuman") is False, res)

print(f"\nPASS={PASS} FAIL={FAIL}")
