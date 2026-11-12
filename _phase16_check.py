#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase16_check.py — 阶段 16a：`/execute` 默认 producer 化（同步契约靠显式 wait）。

背景：阶段 15 之后异步执行已经可审计，但默认仍是同步 —— 长实验占住一个 HTTP 连接，
客户端断开后服务端照跑，调用方也拿不到 job_id 去"放弃/继续等"。
本阶段把默认改成异步（`{status:"queued", job_id, run_id, trace_id, poll}`），
同时**保留**旧的同步契约：`wait:true` / `async:false` 显式选择。

离线自检（不连设备、不写真实台账）：
  1. `as_bool` / `normalize_mode`：字符串 "false" 不能被裸 bool() 当 True
  2. `resolve_execute_mode`：wait → async → 默认 的判定矩阵
  3. `configured_execute_default`：环境变量优先、非法值回落、配置注入、读配置异常回落
  4. `describe_execute_policy` 契约字段
  5. 服务端接线：`_handle_execute` 用解析结果并把 async 钉进 `_handle_execute_impl`
  6. `/jobs/{id}` 回包能对上 run_id / trace_id，且不泄漏内部 record_* 字段
  7. 消费方改造：`_poll_job` 在桩服务器上 queued → completed（含超时与失败）
  8. 全仓 `/execute` 调用点扫描：必须显式表态（wait/async）或走轮询助手
  9. 配置落盘：`config/fallback_config.json` 的 execute_default 与代码默认一致

用法：D:/anaconda3/envs/lqcs/python.exe _phase16_check.py
"""
from __future__ import annotations

import ast
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
FAILED: list[str] = []

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(f"{name}{(' :: ' + detail) if detail else ''}")
        print(f"[FAIL] {name}{(' :: ' + detail) if detail else ''}")


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── 1/2/3/4 策略模块 ─────────────────────────────────────────────────────────
from services.common import exec_policy as EP  # noqa: E402

check("as_bool 真值", EP.as_bool(True) is True and EP.as_bool("true") is True
      and EP.as_bool("1") is True and EP.as_bool(1) is True)
check("as_bool 假值（关键：字符串 false 不是 True）",
      EP.as_bool(False) is False and EP.as_bool("false") is False
      and EP.as_bool("0") is False and EP.as_bool(0) is False)
check("as_bool 认不出 → None", EP.as_bool(None) is None and EP.as_bool("maybe") is None
      and EP.as_bool(2) is None and EP.as_bool([]) is None)
check("normalize_mode", EP.normalize_mode(" SYNC ") == "sync"
      and EP.normalize_mode("async") == "async" and EP.normalize_mode("queued") is None
      and EP.normalize_mode(1) is None)

m = EP.resolve_execute_mode
check("wait=true → sync", m({"wait": True}) == "sync")
check("wait=false → async", m({"wait": False}) == "async")
check("wait 优先于 async", m({"wait": True, "async": True}) == "sync")
check("async=true → async", m({"async": True}) == "async")
check("async=\"false\" → sync（字符串陷阱）", m({"async": "false"}) == "sync")
check("都没表态 → 默认 async",
      m({}, default="async") == "async" and EP.DEFAULT_EXECUTE_MODE == "async")
check("都没表态 + default=sync", m({}, default="sync") == "sync")
check("非 dict 输入不炸", m(None) in ("sync", "async") and m("x") in ("sync", "async"))

check("配置注入 sync", EP.configured_execute_default({"actuation": {"execute_default": "sync"}}) == "sync")
check("配置非法值 → 默认", EP.configured_execute_default(
    {"actuation": {"execute_default": "sometimes"}}) == EP.DEFAULT_EXECUTE_MODE)
check("配置缺失 → 默认", EP.configured_execute_default({}) == EP.DEFAULT_EXECUTE_MODE)
check("配置非 dict → 默认", EP.configured_execute_default({"actuation": None}) == EP.DEFAULT_EXECUTE_MODE)

_prev_env = os.environ.get(EP.EXECUTE_MODE_ENV)
try:
    os.environ[EP.EXECUTE_MODE_ENV] = "sync"
    check("环境变量优先于配置",
          EP.configured_execute_default({"actuation": {"execute_default": "async"}}) == "sync")
    os.environ[EP.EXECUTE_MODE_ENV] = "nonsense"
    check("环境变量非法 → 回落配置",
          EP.configured_execute_default({"actuation": {"execute_default": "sync"}}) == "sync")
finally:
    if _prev_env is None:
        os.environ.pop(EP.EXECUTE_MODE_ENV, None)
    else:
        os.environ[EP.EXECUTE_MODE_ENV] = _prev_env

pol = EP.describe_execute_policy()
check("describe_execute_policy 契约", isinstance(pol, dict) and pol.get("default") in ("sync", "async")
      and pol.get("env") == "QMCLAW_EXECUTE_DEFAULT" and pol.get("poll") == "GET /jobs/{job_id}")

# 配置落盘与代码默认一致（避免"代码默认 async、配置写 sync"这种静默分歧）
try:
    _cfg = json.loads(read(os.path.join(SERVER, "config", "fallback_config.json")))
    _cfg_mode = ((_cfg.get("quantum_service") or {}).get("actuation") or {}).get("execute_default")
    check("配置落盘 execute_default 合法且与默认一致",
          _cfg_mode == EP.DEFAULT_EXECUTE_MODE, f"config={_cfg_mode} code={EP.DEFAULT_EXECUTE_MODE}")
except Exception as exc:  # noqa: BLE001
    check("配置落盘 execute_default", False, str(exc))

# ── 5/6 服务端接线（源码契约，import 服务太依赖 LabRAD）───────────────────────
qs_path = os.path.join(SERVER, "services", "quantum_service", "server.py")
qs = read(qs_path)
tree = ast.parse(qs)
check("服务端语法 OK", True)
check("_handle_execute 走 resolve_execute_mode", "resolve_execute_mode(data)" in qs)
check("解析结果钉进 call_data", 'call_data["async"] = is_async' in qs)
check("_handle_execute_impl 只收 call_data", "_handle_execute_impl(data)" not in qs
      and qs.count("_handle_execute_impl(call_data)") == 3)
check("记录里落 execute_mode", '"execute_mode": exec_mode' in qs)
check("异步回包带 execute_mode", 'result["execute_mode"] = "async"' in qs)
check("同步回包带 execute_mode", 'result["execute_mode"] = "sync"' in qs)
check("/jobs 回包暴露 trace_id", 'payload["trace_id"] = job.get("record_trace_id")' in qs)
check("/jobs 回包暴露 run_id", 'payload.setdefault("run_id", job.get("run_id"))' in qs)
check("/jobs 不泄漏 record_* 内部字段",
      'payload.pop("record_code", None)' in qs
      and 'payload.pop("record_trace_id", None)' in qs
      and 'payload.pop("record_task_id", None)' in qs)
check("异步分支仍先于同步落账返回",
      qs.index("if isinstance(result, dict) and result.get(\"async\") and result.get(\"job_id\")")
      < qs.index("result = self._complete_record(\n            record, result, datasets_before"))

# ── 7 消费方：_poll_job 打桩服务器 ────────────────────────────────────────────
from services.agent_service import adapter as AD  # noqa: E402

_STATE = {"polls": 0, "mode": "ok"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_GET(self):  # noqa: N802
        if not self.path.startswith("/jobs/"):
            self.send_error(404)
            return
        _STATE["polls"] += 1
        if _STATE["mode"] == "fail":
            body = {"job_id": "job_x", "status": "failed", "error": "boom"}
        elif _STATE["mode"] == "slow":
            body = {"job_id": "job_x", "status": "running"}
        elif _STATE["polls"] < 2:
            body = {"job_id": "job_x", "status": "queued", "run_id": "r-1", "trace_id": "t-1"}
        else:
            body = {"job_id": "job_x", "status": "completed",
                    "result": {"status": "success", "dataset_ids": [42]}}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


_srv = HTTPServer(("127.0.0.1", 0), _Handler)
_port = _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_base = f"http://127.0.0.1:{_port}"

_STATE.update(polls=0, mode="ok")
_res = AD._poll_job("job_x", _base, timeout_s=10, interval_s=0.01)
check("_poll_job queued → completed 取到 result",
      isinstance(_res, dict) and _res.get("dataset_ids") == [42], str(_res))
check("_poll_job 至少轮询两次（先看到 queued）", _STATE["polls"] >= 2, str(_STATE["polls"]))

_STATE.update(polls=0, mode="fail")
_res = AD._poll_job("job_x", _base, timeout_s=5, interval_s=0.01)
check("_poll_job 失败终态回结构化错误",
      isinstance(_res, dict) and _res.get("status") == "failed" and "boom" in str(_res.get("error")))

_STATE.update(polls=0, mode="slow")
_res = AD._poll_job("job_x", _base, timeout_s=0.05, interval_s=0.01)
check("_poll_job 超时回 timeout 且不外抛",
      isinstance(_res, dict) and _res.get("status") == "timeout")
_srv.shutdown()

check("agent 适配器显式 wait 并带轮询兜底",
      '{"code": code, "wait": True}' in read(os.path.join(SERVER, "services", "agent_service", "adapter.py"))
      and "_poll_job(str(result[\"job_id\"]), quantum_service_url)" in read(
          os.path.join(SERVER, "services", "agent_service", "adapter.py")))
check("client_proxy 显式 wait",
      '"code": code, "wait": True' in read(os.path.join(SERVER, "services", "client_proxy.py")))
check("qca adapter 显式 wait",
      read(os.path.join(SERVER, "services", "qca_service", "adapter.py")).count('"wait": True') == 2)
check("qca runner 显式 wait",
      '"code": code, "wait": True' in read(os.path.join(SERVER, "services", "qca_service", "core", "runner.py")))
check("hermes toolset 显式 wait",
      read(os.path.join(SERVER, "services", "hermes_service", "quantum_toolset.py")).count('"wait": True') == 2)
check("hermes cron 显式 wait",
      '"wait": True' in read(os.path.join(SERVER, "services", "hermes_service", "cron.py")))
check("DSL lqcs 显式 async（原本就是 producer 路径）",
      '"async": True' in read(os.path.join(SERVER, "services", "workflow_service", "dsl", "backends", "lqcs.py")))
check("task_queue 显式 async + 轮询",
      '"async": True' in read(os.path.join(SERVER, "services", "task_queue", "server.py")))
check("workflow_service 实验节点走 submit_and_wait（异步）",
      "_lqcs.submit_and_wait(" in read(os.path.join(SERVER, "services", "workflow_service", "server.py")))

# ── 8 全仓 /execute 调用点扫描 ───────────────────────────────────────────────
_ALLOW_NO_FLAG = {
    # 这些文件里的 /execute 由助手函数或轮询封装统一处理，正文不直接表态
    os.path.join("services", "quantum_service", "server.py"),      # 服务端自身路由
    os.path.join("services", "analysis_service", "server.py"),     # analysis 的 /execute，与量子无关
    os.path.join("services", "common", "quantum_tools", "analysis_tools.py"),  # 同上（analysis 侧）
    os.path.join("src", "index.ts"),                               # Express 转发 analysis /execute
}
_unflagged: list[str] = []
for base, dirs, files in os.walk(os.path.join(SERVER, "services")):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", "data", "node_modules")]
    for fn in files:
        if not fn.endswith(".py"):
            continue
        full = os.path.join(base, fn)
        rel = os.path.relpath(full, SERVER)
        if rel in _ALLOW_NO_FLAG:
            continue
        text = read(full)
        if "/execute" not in text:
            continue
        try:
            mod = ast.parse(text)
        except SyntaxError:
            continue
        # 只认"真的拼 URL"的位置：字符串常量 "/execute" 或 f-string 里含 "/execute"。
        # 文档字符串/注释里提到路径不算调用点（否则满仓误报）。
        sites: list[int] = []
        for node in ast.walk(mod):
            if isinstance(node, ast.Constant) and node.value == "/execute":
                sites.append(node.lineno)
            elif isinstance(node, ast.JoinedStr):
                if any(isinstance(v, ast.Constant) and isinstance(v.value, str)
                       and "/execute" in v.value for v in node.values):
                    sites.append(node.lineno)
        lines = text.splitlines()
        for lineno in sorted(set(sites)):
            idx = lineno - 1
            if idx < 0 or idx >= len(lines):
                continue
            # 窗口放宽到 ±60 行：有的节点是"先拼 url、几十行后再交给 _run_quantum_job"
            window = "\n".join(lines[max(0, idx - 60): idx + 60])
            ok = ('"wait": True' in window or "'wait': True" in window
                  or '"async": True' in window or "_run_quantum_job" in window
                  or "submit_and_wait" in window or "_poll_job" in window)
            if not ok:
                _unflagged.append(f"{rel}:{lineno}")
check("所有 /execute 调用点都显式表态或走轮询助手",
      not _unflagged, "; ".join(_unflagged[:6]))

# ── 8b 异步完成态回写（真机验收时发现的两个缺陷）──────────────────────────────
_qs = read(os.path.join(SERVER, "services", "quantum_service", "server.py"))
check("finalize 用 get 取 trace_id（不 pop，轮询才拿得到）",
      'trace_id = job.get("record_trace_id")' in _qs)
check("finalize 把完成态结果回写 job['result']",
      'job["result"] = enriched' in _qs)
check("回写的字段含运行 id / 数据集 / 执行模式",
      'enriched.setdefault("run_id", record.run_id)' in _qs
      and 'enriched.setdefault("dataset_ids"' in _qs
      and 'enriched.setdefault("execute_mode", "async")' in _qs)
check("/jobs 回包 trace_id 有兜底（记录缺失时取 result 的）",
      'job.get("record_trace_id") or (payload.get("result") or {}).get("trace_id")' in _qs)
check("/jobs 回包不再泄漏 record_* 内部字段",
      'payload.pop("record_trace_id", None)' in _qs
      and 'payload.pop("record_code", None)' in _qs)


# ── 8c finalize 行为（打桩 job，不需要设备）──────────────────────────────────
def _stub_finalize():
    """构造最小 job，验证 finalize 后轮询能直接读到结果字段。"""
    import importlib
    import types as _types

    # server.py 用相对导入，必须按包路径导入（直接按文件 exec 会报
    # "attempted relative import with no known parent package"）。
    sys.path.insert(0, SERVER)
    try:
        mod = importlib.import_module("services.quantum_service.server")
    except Exception:
        return None
    svc = object.__new__(mod.QuantumService)
    record = _types.SimpleNamespace(
        run_id="run-probe", status="success", qubits=["q4ld3"], dataset_ids=[123],
        dataset_source="datavault",
    )

    def _fake_complete(rec, result, datasets_before, pre_snapshot, code, trace_id, task_id):
        rec.status = "success"
        return {"status": "success", "stdout": "ok"}

    svc._complete_record = _fake_complete
    job = {
        "job_id": "job-probe", "run_record": record, "record_code": "1+1",
        "record_trace_id": "t-probe", "record_task_id": None,
        "result": {"status": "success", "output": "1+1"},
    }
    svc._finalize_async_job(job, job["result"], [], None)
    payload = {"result": job.get("result")}
    payload["run_id"] = job.get("run_id")
    payload["trace_id"] = job.get("record_trace_id") or (payload.get("result") or {}).get("trace_id")
    return job, payload


_probe = _stub_finalize()
if _probe is None:
    check("finalize 打桩可执行（server.py 可导入）", False)
else:
    _job, _payload = _probe
    _res = _payload.get("result") or {}
    check("finalize 后 job.result 带 run_id", _res.get("run_id") == "run-probe")
    check("finalize 后 job.result 带 dataset_ids", _res.get("dataset_ids") == [123])
    check("finalize 后 job.result 带 execute_mode=async", _res.get("execute_mode") == "async")
    check("finalize 后 trace_id 仍留在 job 上（轮询可见）", _payload.get("trace_id") == "t-probe")
    check("finalize 后 job.result 保留 worker 原始输出", _res.get("stdout") == "ok")


# ── 9 文档在位 ───────────────────────────────────────────────────────────────
ops = read(os.path.join(ROOT, "docs", "OPERATIONS.md"))
check("OPERATIONS 有 §3.19", "### 3.19" in ops and "execute_default" in ops)
check("OPERATIONS §4 表含 _phase16_check.py", "_phase16_check.py" in ops)
plan = read(os.path.join(ROOT, "docs", "PLAN_PRODUCER_DEFAULT.md"))
check("方案文档保留（契约可查）", "producer" in plan.lower() and "wait" in plan)

print(f"\n_phase16_check: PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for item in FAILED:
        print("  -", item)
sys.exit(0 if FAIL == 0 else 1)
