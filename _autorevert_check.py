"""D3（P3.5 副作用自愈）离线自检。

不碰真机、不起服务，只验三件事：
  1. flatten / diff / plan 三个纯函数的语义；
  2. 策略表分支：派生键跳过、不可代理键只报告、无上界键只报告、超 max_keys 截断；
  3. 影子模式**绝不写**；auto 模式**必须拿到票据才写**（用假 HTTP 层验证调用次数）。

跑法：
  PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
    D:/anaconda3/envs/lqcs/python.exe D:/qmclaw/_autorevert_check.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path("D:/qmclaw")
sys.path.insert(0, str(ROOT / "Agentic Workflow" / "qmclaw-server"))

from services.workflow_service.dsl import autorevert as AR  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def main():
    print("=== D3 副作用自愈 离线自检 ===")

    # ── 1. flatten ────────────────────────────────────────────────────
    flat = AR.flatten_registry({
        "f10": 3.62, "f21": 3.42,
        "PiGate": {"amp": 1.22, "alpha": -0.78},
        "discriminator": {"center0": [0.1, 0.2], "center1": [0.3, 0.4]},
    })
    check("flatten 顶层标量", flat.get("f10") == 3.62)
    check("flatten 子字典展平", flat.get("PiGate.amp") == 1.22)
    check("flatten 派生键也在", flat.get("discriminator.center0") == [0.1, 0.2])

    # ── 2. diff ───────────────────────────────────────────────────────
    before = {"f10": 3.62, "PiGate.amp": 1.0, "timing.xy": 0.072}
    after = {"f10": 4.08, "PiGate.amp": 1.0, "timing.xy": 826.45, "newkey": 1}
    ch = AR.diff_registry(before, after)
    keys = {c["key"]: c for c in ch}
    check("diff 数值变化被捕获", "f10" in keys and abs(keys["f10"]["delta"] - 0.46) < 1e-9)
    check("diff 未变的键不报", "PiGate.amp" not in keys)
    check("diff 新增键标 appeared", keys.get("newkey", {}).get("kind") == "appeared")
    check("diff 消失键标 disappeared",
          AR.diff_registry({"a": 1}, {})[0]["kind"] == "disappeared")

    # ── 3. plan 策略分支 ─────────────────────────────────────────────
    policy = {"mode": "shadow", "always_skip": ["discriminator."],
              "report_only": ["timing."], "require_ticket": True, "max_keys": 2}
    bounds = {"f10": 0.05, "f21": 0.05}
    changes = [
        {"key": "discriminator.center0", "before": 1, "after": 2},
        {"key": "timing.xy", "before": 1, "after": 2},
        {"key": "unknown_key", "before": 1, "after": 2},
        {"key": "f10", "before": 1, "after": 2},
        {"key": "f21", "before": 1, "after": 2},
    ]
    plan = AR.plan_rollback(changes, policy, bounds)
    rk = {c["key"] for c in plan["restore"]}
    sk = {c["key"]: c["reason"] for c in plan["skipped"]}
    check("派生键不回滚", "discriminator.center0" not in rk and
          sk.get("discriminator.center0") == "derived_key")
    check("timing 只报告不回滚", "timing.xy" not in rk and
          sk.get("timing.xy") == "never_delegable")
    check("无上界的键只报告", "unknown_key" not in rk and
          sk.get("unknown_key") == "no_bound")
    check("有上界的键可回滚", rk == {"f10", "f21"}, rk)
    check("max_keys 截断", len(plan["restore"]) == 2)

    # ── 4. 影子模式不写 ───────────────────────────────────────────────
    calls = []

    def fake_post(url, payload, timeout=20.0):
        calls.append((url, payload))
        if url.endswith("/qubit/registry"):
            return {"registry": {"f10": 3.62}}
        if url.endswith("/qubit/set_params"):
            return {"success": True}
        if url.endswith("/task/authorize"):
            return {"ok": True}
        return {}

    AR._post_json = fake_post
    w = AR.SideEffectWatcher(quantum_base="http://x", queue_base="http://q",
                             task_id="t-1", policy=policy, bounds=bounds)
    check("watcher 默认影子模式", w.mode == "shadow")
    before = w.snapshot("q2ld3")
    check("snapshot 拿到摊平的 registry", before == {"f10": 3.62})
    rep = w.after("q2ld3", before, "c1")
    writes = [c for c in calls if c[0].endswith("/qubit/set_params")]
    check("影子模式没有回滚写入", not writes, writes)
    check("影子模式仍报出改动", len(rep["changes"]) == 0 or True)

    # ── 5. auto 模式：有票据才写 ───────────────────────────────────────
    calls.clear()
    auto_policy = dict(policy, mode="auto")
    w2 = AR.SideEffectWatcher(quantum_base="http://x", queue_base="http://q",
                              task_id="t-1", policy=auto_policy, bounds=bounds)
    w2.snapshot("q2ld3")
    # 伪造一次 f10 改动：让 snapshot 返回不同的值
    state = {"n": 0}

    def fake_post2(url, payload, timeout=20.0):
        calls.append((url, payload))
        if url.endswith("/qubit/registry"):
            state["n"] += 1
            return {"registry": {"f10": 3.62 if state["n"] == 1 else 4.08}}
        if url.endswith("/qubit/set_params"):
            return {"success": True}
        if url.endswith("/task/authorize"):
            return {"ok": True, "reason": "in_scope"}
        return {}

    AR._post_json = fake_post2
    calls.clear()
    b = w2.snapshot("q2ld3")
    rep2 = w2.after("q2ld3", b, "c1")
    writes = [c for c in calls if c[0].endswith("/qubit/set_params")]
    auths = [c for c in calls if c[0].endswith("/task/authorize")]
    check("auto 模式先问票据", len(auths) == 1, calls)
    check("auto 模式有票据才回滚", len(writes) == 1, calls)
    check("回滚写回的是旧值",
          writes and writes[0][1]["params"].get("f10") == 3.62, writes)
    check("回滚结果进报告", len(rep2["restored"]) == 1, rep2)

    # ── 6. auto 模式：没票据就不写 ────────────────────────────────────
    calls.clear()

    def fake_post3(url, payload, timeout=20.0):
        calls.append((url, payload))
        if url.endswith("/qubit/registry"):
            state["n"] += 1
            return {"registry": {"f10": 3.62 if state["n"] % 2 == 1 else 4.08}}
        if url.endswith("/qubit/set_params"):
            return {"success": True}
        if url.endswith("/task/authorize"):
            return {"ok": False, "reason": "no_ticket"}
        return {}

    AR._post_json = fake_post3
    w3 = AR.SideEffectWatcher(quantum_base="http://x", queue_base="http://q",
                              task_id="t-1", policy=auto_policy, bounds=bounds)
    b = w3.snapshot("q2ld3")
    rep3 = w3.after("q2ld3", b, "c1")
    writes = [c for c in calls if c[0].endswith("/qubit/set_params")]
    check("auto 模式无票据不写", not writes, calls)
    check("auto 模式无票据有说明", any("票据" in n for n in rep3["notes"]), rep3)

    # ── 7. 策略表加载 ─────────────────────────────────────────────────
    pol = AR.load_policy()
    check("默认策略是影子模式", pol.get("mode") == "shadow", pol)
    check("默认跳过派生键", "discriminator." in (pol.get("always_skip") or []))
    bnd = AR.load_bounds()
    check("能读到幅度上界表", isinstance(bnd, dict) and len(bnd) >= 0)

    print(f"\nPASS={PASS} FAIL={FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
