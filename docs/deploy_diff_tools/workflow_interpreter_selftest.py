#!/usr/bin/env python
"""workflow 解释执行器独立自测（不碰硬件、不依赖任何真实微服务）。

做法：起两个本地 mock HTTP 服务（quantum 的 /execute + /jobs/*、analysis 的
/plot/offline/v2），直接实例化 `WorkflowService`，把节点图喂给
`_run_workflow_async`，检查执行语义。

mock 的 quantum 服务是 job 感知的（和真机 quantum_service 一样）：
POST /execute {async:true} → 返回 job_id → GET /jobs/{id} 轮询 →
POST /jobs/{id}/abandon 放弃。这样测出来的才是真实链路。

覆盖用例：
  1. 成功链：context → experiment → contrast → analysis → print（含 dataset 传递）
  2. 节点失败 → 下游 SKIPPED、工作流 FAILED、错误可读
  3. 未知节点类型 → FAILED（以前是“报错但算成功”）
  4. 节点超时 → FAILED / status=timeout，且按节点级 timeout 生效
  5. 环检测 → FAILED 且错误里带环
  6. quality_gate 通过 / 不通过
  7. continueOnError=true → 失败节点后继续执行
  8. {{变量}} 解析：上游 metrics 也能引用
  9. cancel → 工作流 CANCELLED、剩余节点不再执行
 10. create/status/cancel 三个 API 的返回结构
 11. experiment 参数校验（fn / qubit / 参数名）→ 节点 FAILED，且不下发到测控
 12. 数据集探针：本次实验没写出新数据集 → experiment 节点 FAILED
 13. 硬件互斥：第二个工作流直接报 hardware busy
 14. 节点重试：前两次失败、第三次成功 → completed
 15. 条件边：condition-pass 不满足 → skipped；condition-fail 满足 → 执行
 16. 多轮 repeat：maxRounds=2 跑两轮；stopWhen 满足则提前停止

用法：
    python docs/deploy_diff_tools/workflow_interpreter_selftest.py
退出码 0 = 全部通过。
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER_DIR))

from services.workflow_service.server import (  # noqa: E402
    NodeStatus,
    WorkflowNode,
    WorkflowService,
)

RESULTS = []


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


# ── mock 服务 ────────────────────────────────────────────────────────────────

MOCK = {
    "dataset": 900,
    "slow": 4.0,
    "calls": [],
    "jobs": {},
    "flaky_left": 0,
}

NL = chr(10)  # 源码里避免写反斜杠转义，防止写文件时被二次转义


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload: dict, code: int = 200):
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # 取消用例里客户端提前断开是预期行为，不要在 mock 侧刷异常栈
            pass

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}


def _mock_exec(code: str, timeout) -> dict:
    """模拟 quantum_service 执行一段代码，返回 {status, stdout, stderr, error}。

    约定（和真机 quantum_service 的 result_container 同形）：
      - 成功：status="success"，stdout 里带 QMCLAW_DATASET / QMCLAW_CONTRAST 标记
      - 失败：status="error"，error 有值
      - 超时：status="timeout"，error 里带 "timed out"
    """
    fn = ""
    for cand in ("sq.boom", "sq.slow", "sq.nodataset", "sq.flaky", "sq.iqraw"):
        if cand in code:
            fn = cand
            break

    if fn == "sq.boom":
        return {"status": "error", "stdout": "", "stderr": "", "error": "simulated hardware failure"}

    if fn == "sq.flaky" and MOCK["flaky_left"] > 0:
        MOCK["flaky_left"] -= 1
        return {"status": "error", "stdout": "", "stderr": "", "error": "simulated transient failure"}

    try:
        limit = float(timeout)
    except (TypeError, ValueError):
        limit = 300.0
    duration = float(MOCK["slow"]) if fn == "sq.slow" else 0.0
    if duration > limit:
        # 服务端超时：像真机一样在 timeout 秒后给出 timeout 状态
        time.sleep(limit)
        return {
            "status": "timeout",
            "stdout": "",
            "stderr": "",
            "error": "execution timed out after %ds" % int(limit),
        }
    if duration:
        time.sleep(duration)

    if fn == "sq.nodataset":
        # 实验跑完但没写出新数据集 → 数据集探针报错
        return {
            "status": "success",
            "stdout": "QMCLAW_DATASET_ERR no new dataset before=%d" % MOCK["dataset"],
            "stderr": "",
            "error": "",
        }

    MOCK["dataset"] += 1
    ds = MOCK["dataset"]
    if "_qm_contrast" in code:
        # 口径与真实片段一致：QMCLAW_CONTRAST ds p1_i p1_x contrast sep sigma snr shots
        cds = ds
        marker = "_ds = "
        if marker in code:
            tail = code.split(marker, 1)[1].strip()
            token = tail.split()[0] if tail else ""
            if token.isdigit():
                cds = int(token)
        return {
            "status": "success",
            "stdout": "QMCLAW_CONTRAST %d 0.133 0.898 0.765 120064.8 47916.9 2.506 3000" % cds,
            "stderr": "",
            "error": "",
        }
    return {
        "status": "success",
        "stdout": "[mock] done" + NL + "QMCLAW_DATASET %d" % ds,
        "stderr": "",
        "error": "",
    }


def _mock_worker(job: dict, code: str, timeout):
    job["status"] = "running"
    res = _mock_exec(code, timeout)
    job["result"] = res
    job["error"] = res.get("error") or None
    job["status"] = res.get("status") or "completed"
    job["finished_at"] = time.time()
    if job.get("abandoned"):
        job["status"] = "abandoned"


class MockQuantum(_Quiet):
    """模拟 quantum_service：异步 job 接口（/execute + /jobs/*）。"""

    def do_POST(self):
        path = self.path.split("?")[0]
        if path.startswith("/jobs/") and path.endswith("/abandon"):
            job_id = path[len("/jobs/"):-len("/abandon")]
            job = MOCK["jobs"].get(job_id)
            if job is not None:
                job["abandoned"] = True
                if job.get("status") in ("queued", "running"):
                    job["status"] = "abandoned"
                    job["error"] = "abandoned by caller"
            self._json({"job_id": job_id, "status": (job or {}).get("status", "unknown"), "abandoned": True})
            return

        payload = self._read()
        code = str(payload.get("code", ""))
        timeout = payload.get("timeout")
        MOCK["calls"].append(code)

        if not payload.get("async"):
            # 老服务端（不支持异步）：直接同步返回
            self._json(_mock_exec(code, timeout))
            return

        job_id = "job_mock_%d" % (len(MOCK["jobs"]) + 1)
        job = {
            "job_id": job_id,
            "task_id": job_id,
            "status": "queued",
            "result": None,
            "error": None,
            "abandoned": False,
        }
        MOCK["jobs"][job_id] = job
        threading.Thread(target=_mock_worker, args=(job, code, timeout), daemon=True).start()
        self._json({"task_id": job_id, "job_id": job_id, "status": "queued", "async": True})

    def do_GET(self):
        path = self.path.split("?")[0]
        parts = [p for p in path.split("/") if p]
        job = None
        if len(parts) >= 2 and parts[-2] == "jobs":
            job = MOCK["jobs"].get(parts[-1])
        if job is None:
            self._json({"error": "unknown job"}, 404)
            return
        self._json(dict(job))


class MockAnalysis(_Quiet):
    def do_POST(self):
        payload = self._read()
        MOCK["calls"].append(f"analysis:{payload.get('dataset_id')}")
        self._json(
            {
                "success": True,
                "qubit": "q2ld3",
                "experiment_type": "T1",
                "dataset_name": f"mock {payload.get('dataset_id')}",
                "fit_metrics": {"T1_us": 42.5, "R_squared": 0.991},
            }
        )


def _serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ── 服务实例 ─────────────────────────────────────────────────────────────────

def make_service(quantum_port: int, analysis_port: int) -> WorkflowService:
    svc = WorkflowService(port=0)
    svc._quantum_service_url = f"http://127.0.0.1:{quantum_port}"
    svc._analysis_service_url = f"http://127.0.0.1:{analysis_port}"
    svc._persist_workflow_run = lambda *a, **k: None  # 不写运行记录
    return svc


def run_workflow(svc: WorkflowService, nodes, settings=None, context=None) -> str:
    created = svc._handle_create_workflow({"name": "selftest", "nodes": nodes, "settings": settings or {}})
    wid = created["workflow_id"]
    svc._run_workflow_async(wid, context or {})
    return wid


def run_workflow_bg(svc: WorkflowService, nodes, settings=None, context=None):
    """后台线程里跑一个工作流，返回 (workflow_id, thread)。"""
    created = svc._handle_create_workflow({"name": "selftest-bg", "nodes": nodes, "settings": settings or {}})
    wid = created["workflow_id"]
    th = threading.Thread(target=svc._run_workflow_async, args=(wid, context or {}), daemon=True)
    th.start()
    return wid, th


def status_of(svc, wid):
    st = svc._handle_workflow_status({"workflowId": wid})
    return st, {n["id"]: n for n in st["nodes"]}


def node(nid, ntype, depends=None, **config):
    return {"id": nid, "type": ntype, "depends": depends or [], "config": config}


def count_calls(marker: str) -> int:
    return sum(1 for c in MOCK["calls"] if marker in c)


# ── 用例 ─────────────────────────────────────────────────────────────────────

def case_success_chain(svc):
    nodes = [
        node("ctx", "context", variables={"qubit": "q2ld3"}),
        node("e1", "experiment", ["ctx"], fn="sq.iqraw", qubit="{{qubit}}"),
        node("c1", "contrast", ["e1"], qubit="{{qubit}}", kind="IQraw", threshold=0.4),
        node("a1", "analysis", ["c1"]),
        node("p1", "print", ["a1"]),
    ]
    wid = run_workflow(svc, nodes)
    st, by_id = status_of(svc, wid)
    check("1.1 成功链状态 completed", st["status"] == "completed", st["status"])
    check("1.2 全部节点 completed", all(n["status"] == "completed" for n in by_id.values()),
          {k: v["status"] for k, v in by_id.items()})
    e1 = by_id["e1"]["result"] or {}
    check("1.3 experiment 回传 dataset_id", isinstance(e1.get("dataset_id"), int), str(e1.get("dataset_id")))
    c1 = by_id["c1"]["result"] or {}
    check("1.4 contrast 解析出 0.765", abs(float(c1.get("contrast", 0)) - 0.765) < 1e-6, str(c1.get("contrast")))
    check("1.5 contrast passed", c1.get("passed") is True, str(c1.get("passed")))
    check("1.6 contrast 沿用了上游 dataset", c1.get("dataset_id") == e1.get("dataset_id"),
          f"{c1.get('dataset_id')} != {e1.get('dataset_id')}")
    a1 = by_id["a1"]["result"] or {}
    check("1.7 analysis 从上游拿到 dataset", str(a1.get("dataset_id")) == str(e1.get("dataset_id")),
          str(a1.get("dataset_id")))
    p1 = by_id["p1"]["result"] or {}
    check("1.8 print 汇总含 dataset", "dataset=" in str(p1.get("stdout", "")), str(p1.get("stdout"))[:80])
    code = MOCK["calls"][0] if MOCK["calls"] else ""
    check("1.9 experiment 代码带 do_plot=False", "do_plot=False" in str(code), str(code)[:120])


def case_failure_stops(svc):
    nodes = [
        node("e1", "experiment", [], fn="sq.boom", qubit="q2ld3"),
        node("e2", "experiment", ["e1"], fn="sq.iqraw", qubit="q2ld3"),
        node("p1", "print", ["e2"]),
    ]
    wid = run_workflow(svc, nodes)
    st, by_id = status_of(svc, wid)
    check("2.1 工作流 FAILED", st["status"] == "failed", st["status"])
    check("2.2 失败节点判 failed", by_id["e1"]["status"] == "failed", by_id["e1"]["status"])
    check("2.3 错误信息保留", "simulated" in str(by_id["e1"].get("error")), str(by_id["e1"].get("error")))
    check("2.4 下游 skipped（不再执行）", by_id["e2"]["status"] == "skipped", by_id["e2"]["status"])
    check("2.5 尾部节点不残留 pending", by_id["p1"]["status"] in ("skipped", "cancelled"), by_id["p1"]["status"])


def case_unknown_type(svc):
    nodes = [node("x1", "no_such_node_type")]
    wid = run_workflow(svc, nodes)
    st, by_id = status_of(svc, wid)
    check("3.1 未知类型判 failed", by_id["x1"]["status"] == "failed", by_id["x1"]["status"])
    check("3.2 错误里说明 unknown", "unknown" in str(by_id["x1"].get("error", "")).lower(),
          str(by_id["x1"].get("error"))[:80])
    check("3.3 工作流 FAILED", st["status"] == "failed", st["status"])


def case_timeout(svc):
    nodes = [node("s1", "experiment", [], fn="sq.slow", qubit="q2ld3", timeout=2)]
    t0 = time.time()
    wid = run_workflow(svc, nodes)
    elapsed = time.time() - t0
    st, by_id = status_of(svc, wid)
    check("4.1 超时判 failed", by_id["s1"]["status"] == "failed", by_id["s1"]["status"])
    check("4.2 错误里带 timed out", "timed out" in str(by_id["s1"].get("error", "")).lower(),
          str(by_id["s1"].get("error"))[:80])
    check("4.3 未阻塞到 300s", elapsed < 15, f"{elapsed:.1f}s")


def case_cycle(svc):
    nodes = [
        node("a", "experiment", ["b"], fn="sq.iqraw", qubit="q2ld3"),
        node("b", "experiment", ["a"], fn="sq.iqraw", qubit="q2ld3"),
    ]
    wid = run_workflow(svc, nodes)
    st = svc._handle_workflow_status({"workflowId": wid})
    check("5.1 环判 FAILED", st["status"] == "failed", st["status"])
    check("5.2 错误里带 cycle", "cycle" in str(st.get("error", "")).lower(), str(st.get("error"))[:80])
    check("5.3 没有节点被执行", all(n["status"] != "completed" for n in st["nodes"]),
          [n["status"] for n in st["nodes"]])


def case_quality_gate(svc):
    ok_nodes = [
        node("c1", "contrast", [], qubit="q2ld3", kind="IQraw", threshold=0.4),
        node("g1", "quality_gate", ["c1"], metric="contrast", threshold=0.5, operator=">="),
    ]
    wid = run_workflow(svc, ok_nodes)
    st, by_id = status_of(svc, wid)
    check("6.1 gate 通过", by_id["g1"]["status"] == "completed" and st["status"] == "completed",
          f"{by_id['g1']['status']}/{st['status']}")
    check("6.2 gate 输出 passed=True", (by_id["g1"]["result"] or {}).get("passed") is True,
          str((by_id["g1"]["result"] or {}).get("passed")))

    bad_nodes = [
        node("c1", "contrast", [], qubit="q2ld3", kind="IQraw", threshold=0.4),
        node("g1", "quality_gate", ["c1"], metric="contrast", threshold=0.9, operator=">="),
        node("p1", "print", ["g1"]),
    ]
    wid = run_workflow(svc, bad_nodes)
    st, by_id = status_of(svc, wid)
    check("6.3 gate 不通过 → FAILED", st["status"] == "failed", st["status"])
    check("6.4 gate 错误可读", "quality gate failed" in str(by_id["g1"].get("error", "")).lower(),
          str(by_id["g1"].get("error"))[:80])
    check("6.5 gate 下游 skipped", by_id["p1"]["status"] == "skipped", by_id["p1"]["status"])


def case_continue_on_error(svc):
    nodes = [
        node("e1", "experiment", [], fn="sq.boom", qubit="q2ld3", continueOnError=True),
        node("e2", "experiment", ["e1"], fn="sq.iqraw", qubit="q2ld3"),
    ]
    wid = run_workflow(svc, nodes)
    st, by_id = status_of(svc, wid)
    check("7.1 失败节点后继续执行", by_id["e2"]["status"] == "completed", by_id["e2"]["status"])
    check("7.2 失败节点本身仍是 failed", by_id["e1"]["status"] == "failed", by_id["e1"]["status"])


def case_var_resolution(svc):
    nodes = [
        node("ctx", "context", variables={"qubit": "q2ld3"}),
        node("c1", "contrast", ["ctx"], qubit="{{qubit}}", kind="IQraw", threshold=0.4),
        node("p1", "print", ["c1"], message="qubit={{qubit}} contrast={{contrast}}"),
    ]
    wid = run_workflow(svc, nodes)
    _, by_id = status_of(svc, wid)
    out = str((by_id["p1"]["result"] or {}).get("stdout", ""))
    check("8.1 context 变量解析", "qubit=q2ld3" in out, out[:80])
    check("8.2 上游 metrics 变量解析", "contrast=0.765" in out, out[:80])


def case_cancel(svc):
    MOCK["slow"] = 3.0
    nodes = [
        node("s1", "experiment", [], fn="sq.slow", qubit="q2ld3", timeout=30),
        node("e2", "experiment", ["s1"], fn="sq.iqraw", qubit="q2ld3"),
        node("p1", "print", ["e2"]),
    ]
    created = svc._handle_create_workflow({"name": "cancel", "nodes": nodes})
    wid = created["workflow_id"]
    worker = threading.Thread(target=svc._run_workflow_async, args=(wid, {}), daemon=True)
    worker.start()
    time.sleep(0.6)
    cancel_resp = svc._handle_cancel_workflow({"workflowId": wid})
    worker.join(timeout=20)
    check("9.1 cancel 返回 success", cancel_resp.get("success") is True, str(cancel_resp))
    st, by_id = status_of(svc, wid)
    check("9.2 工作流 CANCELLED", st["status"] == "cancelled", st["status"])
    check("9.3 后续节点未执行", by_id["e2"]["status"] == "cancelled", by_id["e2"]["status"])
    check("9.4 尾部节点不残留 pending", by_id["p1"]["status"] in ("cancelled", "skipped"), by_id["p1"]["status"])
    MOCK["slow"] = 4.0


def case_api_shape(svc):
    created = svc._handle_create_workflow(
        {"name": "shape", "nodes": [node("c1", "contrast", qubit="q2ld3")], "settings": {"nodeTimeout": 42}}
    )
    check("10.1 create 返回 workflow_id", bool(created.get("workflow_id")), str(created)[:80])
    wid = created["workflow_id"]
    st = svc._handle_workflow_status({"workflowId": wid})
    for key in ("ok", "id", "workflowId", "name", "status", "nodes"):
        check(f"10.2 status 含 {key}", key in st, str(list(st.keys())))
    check("10.3 settings 透传", st.get("settings", {}).get("nodeTimeout") == 42, str(st.get("settings")))
    svc._workflows[wid].status = NodeStatus.COMPLETED
    cancel_resp = svc._handle_cancel_workflow({"workflowId": wid})
    check("10.4 已结束的工作流 cancel 不报错", cancel_resp.get("success") is True, str(cancel_resp))


def case_param_validation(svc):
    """P0-b：非法 fn / qubit / 参数名必须在下发到测控之前就被挡掉。"""
    before = len(MOCK["calls"])
    cases = [
        ("11.1 非法 fn 被拒", "invalid experiment fn",
         node("bad_fn", "experiment", [], fn="rm -rf /", qubit="q2ld3")),
        ("11.2 非法 qubit 被拒", "invalid qubit",
         node("bad_q", "experiment", [], fn="sq.iqraw", qubit="q2ld3; drop")),
        ("11.3 非法参数名被拒", "invalid parameter name",
         node("bad_p", "experiment", [], fn="sq.iqraw", qubit="q2ld3", params={"not a name": 1})),
    ]
    all_failed = True
    for title, expect, nd in cases:
        # 每个非法参数单开一个工作流：工作流在第一个失败节点就收尾，
        # 多个非法节点放一起，后面的节点只会留成 skipped，测不到各自的报错文案
        wid = run_workflow(svc, [nd])
        st, by_id = status_of(svc, wid)
        nid = nd["id"]
        err = str(by_id[nid].get("error"))
        check(title, expect in err, err[:80])
        all_failed = all_failed and st["status"] == "failed"
    check("11.4 校验失败时不下发到测控", len(MOCK["calls"]) == before,
          f"{len(MOCK['calls']) - before} 次多余调用")
    check("11.5 三个工作流都 FAILED", all_failed, "至少一个不是 failed")


def case_dataset_probe(svc):
    """P1-b：实验没写出新数据集时，experiment 节点必须失败（否则下游分析的是旧数据）。"""
    nodes = [
        node("e1", "experiment", [], fn="sq.nodataset", qubit="q2ld3"),
        node("c1", "contrast", ["e1"], qubit="q2ld3", kind="IQraw", threshold=0.4),
    ]
    wid = run_workflow(svc, nodes)
    st, by_id = status_of(svc, wid)
    check("12.1 无新数据集 → 实验节点 failed", by_id["e1"]["status"] == "failed", by_id["e1"]["status"])
    check("12.2 错误里说明没产出数据集", "no dataset" in str(by_id["e1"].get("error")).lower(),
          str(by_id["e1"].get("error"))[:80])
    check("12.3 下游 contrast 被拦下", by_id["c1"]["status"] == "skipped", by_id["c1"]["status"])
    check("12.4 工作流 FAILED", st["status"] == "failed", st["status"])


def case_hardware_mutex(svc):
    """P0-c：真机只有一条测控通道，第二个工作流必须被拒。"""
    MOCK["slow"] = 3.0
    wid_a, th_a = run_workflow_bg(svc, [node("s1", "experiment", [], fn="sq.slow", qubit="q2ld3", timeout=30)])
    time.sleep(0.8)  # 让 A 先拿到硬件锁
    wid_b = run_workflow(svc, [node("b1", "experiment", [], fn="sq.iqraw", qubit="q2ld3")])
    st_b, by_b = status_of(svc, wid_b)
    check("13.1 第二个工作流被判失败", st_b["status"] == "failed", st_b["status"])
    check("13.2 错误里说明 hardware busy", "hardware busy" in str(st_b.get("error", "")).lower(),
          str(st_b.get("error"))[:80])
    check("13.3 第二个工作流的节点被跳过", by_b["b1"]["status"] == "skipped", by_b["b1"]["status"])
    th_a.join(timeout=30)
    st_a, _ = status_of(svc, wid_a)
    check("13.4 第一个工作流正常跑完", st_a["status"] == "completed", st_a["status"])
    MOCK["slow"] = 4.0


def case_node_retries(svc):
    """P1-a：节点级 retries —— 前两次失败、第三次成功 → 节点 completed。"""
    MOCK["flaky_left"] = 2
    before = count_calls("sq.flaky")
    nodes = [node("r1", "experiment", [], fn="sq.flaky", qubit="q2ld3", retries=2, retryDelay=0.1)]
    wid = run_workflow(svc, nodes)
    st, by_id = status_of(svc, wid)
    MOCK["flaky_left"] = 0
    check("14.1 重试后节点 completed", by_id["r1"]["status"] == "completed", by_id["r1"]["status"])
    check("14.2 工作流 completed", st["status"] == "completed", st["status"])
    check("14.3 实际调用了 3 次", count_calls("sq.flaky") - before == 3, count_calls("sq.flaky") - before)


def case_conditions(svc):
    """P1-a：条件边 —— condition-pass 不满足 → skipped；condition-fail 满足 → 执行。"""
    nodes = [
        # c1 的对比度 0.765 < 阈值 0.9 → result.passed=False，但节点本身 completed
        node("c1", "contrast", [], qubit="q2ld3", kind="IQraw", threshold=0.9),
        node("e_pass", "experiment", ["c1"], fn="sq.iqraw", qubit="q2ld3",
             conditions=[{"from": "c1", "on": "pass"}]),
        node("e_fail", "experiment", ["c1"], fn="sq.iqraw", qubit="q2ld3",
             conditions=[{"from": "c1", "on": "fail"}]),
    ]
    wid = run_workflow(svc, nodes)
    st, by_id = status_of(svc, wid)
    check("15.1 上游节点 completed", by_id["c1"]["status"] == "completed", by_id["c1"]["status"])
    check("15.2 上游 passed=False", (by_id["c1"]["result"] or {}).get("passed") is False,
          str((by_id["c1"]["result"] or {}).get("passed")))
    check("15.3 condition-pass 不满足 → skipped", by_id["e_pass"]["status"] == "skipped",
          by_id["e_pass"]["status"])
    check("15.4 condition-fail 满足 → 执行", by_id["e_fail"]["status"] == "completed",
          by_id["e_fail"]["status"])
    check("15.5 工作流 completed", st["status"] == "completed", st["status"])


def case_repeat(svc):
    """P1-a：多轮 repeat —— maxRounds 生效；stopWhen 满足时提前停止。"""
    before = count_calls("sq.iqraw")
    nodes = [
        node("ctx", "context", variables={"qubit": "q2ld3"}),
        node("e1", "experiment", ["ctx"], fn="sq.iqraw", qubit="{{qubit}}"),
    ]
    wid = run_workflow(svc, nodes, settings={"repeat": {"maxRounds": 2}})
    st, by_id = status_of(svc, wid)
    check("16.1 两轮跑了两次实验", count_calls("sq.iqraw") - before == 2, count_calls("sq.iqraw") - before)
    check("16.2 多轮后工作流 completed", st["status"] == "completed", st["status"])
    check("16.3 节点最终仍是 completed", by_id["e1"]["status"] == "completed", by_id["e1"]["status"])

    before2 = count_calls("_qm_contrast")
    stop_nodes = [node("c1", "contrast", [], qubit="q2ld3", kind="IQraw", threshold=0.4)]
    wid2 = run_workflow(svc, stop_nodes, settings={
        "repeat": {"maxRounds": 3, "stopWhen": {"metric": "contrast", "op": ">=", "threshold": 0.5}}
    })
    st2, _ = status_of(svc, wid2)
    check("16.4 stopWhen 满足后提前停止", count_calls("_qm_contrast") - before2 == 1,
          count_calls("_qm_contrast") - before2)
    check("16.5 提前停止的工作流 completed", st2["status"] == "completed", st2["status"])


def main() -> int:
    qsrv = _serve(MockQuantum)
    asrv = _serve(MockAnalysis)
    qport, aport = qsrv.server_address[1], asrv.server_address[1]
    print(f"mock quantum  : http://127.0.0.1:{qport}/execute (+ /jobs/*)")
    print(f"mock analysis : http://127.0.0.1:{aport}/plot/offline/v2\n")

    svc = make_service(qport, aport)

    cases = [
        ("成功链 / dataset 传递", case_success_chain),
        ("节点失败 / 下游跳过", case_failure_stops),
        ("未知节点类型", case_unknown_type),
        ("节点超时", case_timeout),
        ("环检测", case_cycle),
        ("quality gate", case_quality_gate),
        ("continueOnError", case_continue_on_error),
        ("变量解析", case_var_resolution),
        ("取消", case_cancel),
        ("API 结构", case_api_shape),
        ("参数校验", case_param_validation),
        ("数据集探针", case_dataset_probe),
        ("硬件互斥", case_hardware_mutex),
        ("节点重试", case_node_retries),
        ("条件边", case_conditions),
        ("多轮 repeat", case_repeat),
    ]
    for title, fn in cases:
        print(f"\n── {title} ──")
        try:
            fn(svc)
        except Exception as e:
            import traceback

            traceback.print_exc()
            check(f"{title} 用例异常", False, repr(e))

    qsrv.shutdown()
    asrv.shutdown()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(n, d) for n, ok, d in RESULTS if not ok]
    print(f"\n{'='*60}\n{passed}/{len(RESULTS)} passed")
    for name, detail in failed:
        print(f"  FAIL {name}  {detail}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
