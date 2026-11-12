"""P3 离线自检：task 账本额度、写清单哈希、授权票据、executor 闸门。

跑法：
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
      D:/anaconda3/envs/lqcs/python.exe D:/qmclaw/_p3_check.py

不碰真机、不启服务：TaskQueueService 的 `/task*` 路由用未绑定方法直接调，
executor 的 `_queue_post` 由 FakeSvc 转发到同一个路由。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"D:/qmclaw")
SERVER = ROOT / "Agentic Workflow/qmclaw-server"
sys.path.insert(0, str(SERVER))

TMP = Path(tempfile.mkdtemp(prefix="p3-ledger-"))
os.environ["QMCLAW_TASK_DATA_DIR"] = str(TMP)

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


from services.task_queue.ledger import TaskLedger, manifest_hash
from services.task_queue.server import TaskQueueService
from services.workflow_service.dsl import executor as EX


class QueueShim(TaskQueueService):
    """把 `/task*` 路由接到一个真实账本上（跳过 HTTP 与线程池）。

    必须**继承** `TaskQueueService`：`_handle_task` 会调 `self._task_charge` 等
    兄弟方法，换成鸭子类型替身会 AttributeError（而闸门是 fail-open 的，
    AttributeError 会被吞成"跳过"，让自检假绿）。
    """

    def __init__(self, ledger):
        self._ledger = ledger

    def handle_request(self, method, path, data, query):
        return TaskQueueService._handle_task(self, method, path, data, query)


# ── A. 账本：额度 ─────────────────────────────────────────────────────────
print("[A] 账本额度")
led = TaskLedger(str(TMP / "A"))
task = led.create(objective="复测一次 iqraw")
tid = task["id"]
check("默认 experiments=1", task["budgets"]["experiments"] == 1, task["budgets"])
check("默认 datasets 不卡死 goal 循环", task["budgets"]["datasets"] >= 10, task["budgets"])

first = led.charge(tid, "experiments", 1)
check("第一次实验额度可用", first.get("ok") is True, first)
second = led.charge(tid, "experiments", 1)
check("第二次超额被拦", second.get("reason") == "quota_exceeded", second)
check("超额不记账", led.get(tid)["usage"]["experiments"] == 1, led.get(tid)["usage"])

# ── B. 写清单 + 哈希稳定性 ────────────────────────────────────────────────
print("[B] 写清单与哈希")
manifest = {"writes": [
    {"key": "PiGate.amp", "qubit": "q2ld3", "count": 1, "maxDelta": 0.05, "delegable": True},
    {"key": "PiGate.alpha", "qubit": "q2ld3", "count": 1, "maxDelta": 0.2, "delegable": True},
    {"key": "timing.xy", "qubit": "q2ld3", "count": 1, "maxDelta": 1.0, "delegable": False},
]}
saved = led.save_manifest(tid, manifest)
check("清单落库", saved.get("ok") is True, saved)
h1 = saved.get("manifestHash")
h2 = manifest_hash({"writes": list(reversed(manifest["writes"]))})
check("哈希与顺序无关", h1 == h2, (h1, h2))
check("哈希是 sha256:", str(h1).startswith("sha256:"), h1)
check("改幅度会改哈希",
      manifest_hash({"writes": [dict(manifest["writes"][0], maxDelta=0.5)]}) != h1)

# ── C. 票据签发 ───────────────────────────────────────────────────────────
print("[C] 票据签发")
bad = led.issue_ticket(tid, h1, [{"keys": ["ReadOut.amp"], "maxDelta": {"ReadOut.amp": 0.1}}])
check("scope 超出写清单被拒", "超出写清单" in str(bad.get("error")), bad)
big = led.issue_ticket(tid, h1, [{"keys": ["PiGate.amp"], "maxDelta": {"PiGate.amp": 9.0}}])
check("maxDelta 大于清单被拒", "大于写清单" in str(big.get("error")), big)
ok_ticket = led.issue_ticket(tid, h1, [
    {"keys": ["PiGate.amp", "PiGate.alpha"],
     "maxDelta": {"PiGate.amp": 0.05, "PiGate.alpha": 0.2}, "limit": 5},
    {"keys": ["timing.xy"], "maxDelta": {"timing.xy": 1.0}, "limit": 1},
])
ticket_id = (ok_ticket.get("ticket") or {}).get("id")
check("签票成功", ok_ticket.get("ok") is True and str(ticket_id).startswith("tk-"), ok_ticket)

# ── D. 授权判定 ───────────────────────────────────────────────────────────
print("[D] 授权判定")
in_scope = led.authorize(tid, {"keys": ["PiGate.amp"], "qubit": "q2ld3", "deltas": {"PiGate.amp": 0.01}})
check("票据内放行", in_scope.get("ok") is True, in_scope)
check("放行来自代理票据", str(in_scope.get("authorizedBy")).startswith("agent-delegated:"), in_scope)
oversize = led.authorize(tid, {"keys": ["PiGate.amp"], "qubit": "q2ld3", "deltas": {"PiGate.amp": 0.9}})
check("超过 maxDelta 要人拍板", oversize.get("needsHuman") is True, oversize)
never = led.authorize(tid, {"keys": ["timing.xy"], "qubit": "q2ld3", "deltas": {"timing.xy": 0.1}})
check("timing.* 命中不可代理地板", never.get("needsHuman") is True and never.get("neverDelegable") == ["timing.xy"], never)
outside = led.authorize(tid, {"keys": ["ReadOut.amp"], "qubit": "q2ld3", "deltas": {}})
check("清单外的键被拦", outside.get("needsHuman") is True, outside)
# D1-①：票只覆盖部分键 → primary reason = partial_scope（不再记成两条事件）
partial = led.authorize(tid, {"keys": ["PiGate.amp", "ReadOut.amp"], "qubit": "q2ld3", "deltas": {}})
# 阶段 6 起 ledger 的 reasonCode 是规范错误码（not_authorized / unknown_task …），
# 领域侧的原因码挪到 detailReasonCode；两种都认，兼容旧行为。
check("部分覆盖 → partial_scope",
      partial.get("reasonCode") == "partial_scope" or partial.get("detailReasonCode") == "partial_scope",
      partial)
check("部分覆盖仍要人拍板", partial.get("needsHuman") is True, partial)
report = led.report(tid)
metrics = report.get("metrics") or {}
check("报表含三个指标", {"越界率", "授权缺失率", "代理放行率"} <= set(metrics), metrics)
check("报表统计到动作", metrics.get("actions", 0) >= 4, metrics)
# D1-①：一次 authorize 只记一条事件（actions == 授权调用次数）
_auth = [e for e in (report.get("events") or []) if e.get("kind") == "authorize"]
check("一次授权只记一条事件", len(_auth) == metrics.get("actions"), (len(_auth), metrics.get("actions")))
check("越界率 = 被拦/总动作", abs(metrics.get("越界率", 0) -
      round(metrics.get("blocked", 0) / metrics["actions"], 4)) < 1e-6, metrics)
check("授权缺失率含 partial_scope", metrics.get("授权缺失率", 0) > 0, metrics)

# ── E. executor 闸门 ──────────────────────────────────────────────────────
print("[E] executor 闸门")
shim = QueueShim(led)


def make_ir():
    return {
        "irVersion": "1.0",
        "id": "p3_gate",
        "env": {"qubit": "q2ld3"},
        "pipeline": [{"op": "measure", "fn": "sq.set_pi", "qubit": "q2ld3", "as": "m1"}],
    }


class FakeSvc:
    def __init__(self, shim):
        self._shim = shim
        self._workflows = {}
        self._cancel_events = {}
        self.calls = []
        self.allow = True
        self.persisted = None
        from services.workflow_service import server as SRV
        self._srv = SRV.WorkflowService.__new__(SRV.WorkflowService)
        self._srv._log = lambda m: None

    def _queue_post(self, path, payload, timeout=10.0):
        return self._shim.handle_request("POST", path, payload, {})

    def _queue_get(self, path, timeout=10.0):
        return self._shim.handle_request("GET", path, {}, {})

    def _execute_node(self, workflow, node_id, context):
        self.calls.append(node_id)
        return {"ok": True, "status": "completed", "dataset": 900 + len(self.calls)}

    def _plan_ir(self, ir):
        return None

    def _persist_workflow_run(self, workflow_id, context, extra=None):
        self.persisted = {"workflowId": workflow_id, "extra": extra}
        return {"ok": True, "runId": "run_test"}


# E1 无 taskId → 完全不受管辖（既有 .qwf 行为不变）
svc = FakeSvc(shim)
ex = EX.IrExecutor(svc, make_ir(), {"qubit": "q2ld3"})
ex.run()
check("无 taskId 时照常执行", ex.status == "completed" and len(svc.calls) == 1, (ex.status, svc.calls))
check("无 taskId 时不产生闸门记录", ex.gate_notes == [], ex.gate_notes)

# E2 带 taskId：第一次放行
task2 = led.create(objective="P3 闸门", task_id="t-p3-gate")
svc2 = FakeSvc(shim)
ex2 = EX.IrExecutor(svc2, make_ir(), {"qubit": "q2ld3", "taskId": "t-p3-gate"})
ex2.run()
check("带 taskId 首次执行成功", ex2.status == "completed", (ex2.status, ex2.error))
check("首次真的下发了硬件", len(svc2.calls) == 1, svc2.calls)
check("首次消耗 1 次实验额度", led.get("t-p3-gate")["usage"]["experiments"] == 1,
      led.get("t-p3-gate")["usage"])
check("数据集额度被占 1 个", len(led.get("t-p3-gate")["usage"]["datasets"]) == 1,
      led.get("t-p3-gate")["usage"]["datasets"])
check("数据集编号被回填（非 None）",
      all(d is not None for d in led.get("t-p3-gate")["usage"]["datasets"]),
      led.get("t-p3-gate")["usage"]["datasets"])
check("机时被记账 wallSeconds>0", float(led.get("t-p3-gate")["usage"]["wallSeconds"]) > 0,
      led.get("t-p3-gate")["usage"]["wallSeconds"])

# E3 同一 task 第二次 → 额度超额，不再下发
svc3 = FakeSvc(shim)
ex3 = EX.IrExecutor(svc3, make_ir(), {"qubit": "q2ld3", "taskId": "t-p3-gate"})
ex3.run()
check("同 task 第二次被额度拦住", ex3.outcome.get("status") == "quota_exceeded", ex3.outcome)
check("超额时一次硬件都没发", svc3.calls == [], svc3.calls)
check("配额节点状态为 quota_exceeded", ex3.workflow.status == ex3._NS.QUOTA_EXCEEDED,
      ex3.workflow.status)
_rep = led.report("t-p3-gate")
check("报表记录了被拦次数 quotaBlocked>=1",
      int(_rep.get("metrics", {}).get("quotaBlocked", 0)) >= 1, _rep.get("metrics"))
check("报表里的额度拦截进入总拦截数",
      int(_rep.get("metrics", {}).get("总拦截", 0)) >= 1, _rep.get("metrics"))

# E4 有票据但不覆盖写键 → needs_human，不下发
led.create(objective="票据不完整", task_id="t-p3-ticket", budgets={"experiments": 5})
full = {"writes": [
    {"key": "PiGate.amp", "qubit": "q2ld3", "count": 1, "maxDelta": 0.05, "delegable": True},
    {"key": "PiGate.alpha", "qubit": "q2ld3", "count": 1, "maxDelta": 0.2, "delegable": True},
]}
full_saved = led.save_manifest("t-p3-ticket", full)
led.issue_ticket("t-p3-ticket", full_saved["manifestHash"],
                 [{"keys": ["PiGate.amp"], "maxDelta": {"PiGate.amp": 0.05}, "limit": 3}])
svc4 = FakeSvc(shim)
ex4 = EX.IrExecutor(svc4, make_ir(), {"qubit": "q2ld3", "taskId": "t-p3-ticket"})
ex4.run()
check("票据不覆盖写键 → needs_human", ex4.outcome.get("status") == "needs_human", ex4.outcome)
check("未授权时一次硬件都没发", svc4.calls == [], svc4.calls)
check("needs_human 带出问题", bool((ex4.needs_human or {}).get("question")), ex4.needs_human)
# D1-②：被 needs_human 拦下的节点**不扣额度**、数据集占位要回收
_u4 = led.get("t-p3-ticket")["usage"]
check("被拦节点不扣实验额度", _u4.get("experiments") == 0, _u4)
check("被拦节点回收数据集占位", _u4.get("datasets") == [], _u4)

# E5 enforce=true 且 task 无票据 → needs_human
task5 = led.create(objective="强制授权", task_id="t-p3-enforce",
                   budgets={"experiments": 5})
led.save_manifest("t-p3-enforce", {"writes": [
    {"key": "PiGate.amp", "qubit": "q2ld3", "count": 1, "maxDelta": 0.05, "delegable": True},
    {"key": "PiGate.alpha", "qubit": "q2ld3", "count": 1, "maxDelta": 0.2, "delegable": True},
]})
svc5 = FakeSvc(shim)
ex5 = EX.IrExecutor(svc5, make_ir(), {"qubit": "q2ld3", "taskId": "t-p3-enforce",
                                      "enforceTickets": True})
ex5.run()
check("强制模式下无票据 → needs_human", ex5.outcome.get("status") == "needs_human", ex5.outcome)
check("强制模式未下发硬件", svc5.calls == [], svc5.calls)
_u5 = led.get("t-p3-enforce")["usage"]
check("强制模式被拦也不扣额度", _u5.get("experiments") == 0, _u5)
check("强制模式回收数据集占位", _u5.get("datasets") == [], _u5)

# ── F. 额度退款（refund） ────────────────────────────────────────────────
print("[F] 额度退款")
led.create(objective="退款", task_id="t-p3-refund", budgets={"experiments": 3, "datasets": 3})
led.charge("t-p3-refund", "experiments", 2)
led.charge("t-p3-refund", "datasets", 2)          # 两个 None 占位
led.record_dataset("t-p3-refund", 1234)            # 回填其中一个
_ref = led.refund("t-p3-refund", "experiments", 1)
check("退还 1 次实验额度", _ref.get("refunded") == 1, _ref)
check("实验额度回到 1", led.get("t-p3-refund")["usage"]["experiments"] == 1,
      led.get("t-p3-refund")["usage"])
_ref2 = led.refund("t-p3-refund", "datasets", 1)
check("回收 1 个数据集占位", _ref2.get("refunded") == 1, _ref2)
_ds = led.get("t-p3-refund")["usage"]["datasets"]
check("已回填编号的数据集不回收", 1234 in _ds and len(_ds) == 1, _ds)

# ── G. D1-③/④：落档兜底 + 审计链补齐 ─────────────────────────────────────
print("[G] 落档兜底与审计链")


class FailingSvc(FakeSvc):
    def _persist_workflow_run(self, workflow_id, context, extra=None):
        raise RuntimeError("磁盘写不进去")


svcF = FailingSvc(shim)
exF = EX.IrExecutor(svcF, make_ir(), {"qubit": "q2ld3", "taskId": "t-p3-gate"})
exF.run()
check("落档失败被标记 persistFailed", exF.persist_failed is True, exF.persist_failed)
check("落档失败进 warnings", any("persist failed" in w for w in exF.warnings), exF.warnings)
_fb_dir = exF._persist_fallback_dir()
_fb = []
for _d in exF._persist_fallback_dirs():
    _fb += list(_d.glob("run-persist-failed-*.json"))
check("写了兜底文件 run-persist-failed-*.json", len(_fb) >= 1, [str(d) for d in exF._persist_fallback_dirs()])

svcP = FakeSvc(shim)
exP = EX.IrExecutor(svcP, make_ir(), {"qubit": "q2ld3", "taskId": "t-p3-gate"})
exP.run()
_extra = (svcP.persisted or {}).get("extra") or {}
for _k in ("taskId", "manifestHash", "ticketIds", "quotaBlocked", "gateNotes"):
    check(f"run 记录含 {_k}", _k in _extra, sorted(_extra))
check("taskId 写对", _extra.get("taskId") == "t-p3-gate", _extra.get("taskId"))
check("正常路径不标 persistFailed", exP.persist_failed is False, exP.persist_failed)

print(f"\nPASS={PASS}  FAIL={FAIL}")
sys.exit(0 if FAIL == 0 else 1)
