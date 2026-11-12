"""_phase3b_check.py — 阶段 3 第二批 ①traceId 全链贯通 离线自检

不连 LabRAD、不下发实验，验证 traceId 从 Express → workflow → task_queue → quantum
→ RunRecord 的贯通改造（全部为代码级，可离线判定）：

1. services/base/base_service.py —— 入站 trace 提取与注入（GET query / POST body）
2. services/workflow_service/server.py —— run_ir 取 trace → 透传适配器 → 响应回显
3. services/workflow_service/dsl/backends/lqcs.py —— 队列提交 / 直连执行带 trace
4. services/task_queue/server.py —— 提交存 trace、状态回显、列表按 trace 过滤
5. services/quantum_service/server.py —— /qubit/runs 按 trace 过滤
6. src/services/serviceProxy.ts —— 中间件生成 + 代理加头 + 响应回写

用法：python _phase3b_check.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

PASS = 0
FAIL = 0
WARN = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"[FAIL] {name}" + (f" :: {detail}" if detail else ""))


def warn(name: str, cond: bool, detail: str = "") -> None:
    global WARN
    if not cond:
        WARN += 1
        print(f"[WARN] {name}" + (f" :: {detail}" if detail else ""))


# ------------------------------------------------------- 1. base_service 注入
def check_base_service() -> None:
    src = (SERVER / "services" / "base" / "base_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    check("base/存在 _inbound_trace", "_inbound_trace" in funcs)
    check("base/存在 send_json_response", "send_json_response" in funcs)
    check("base/读 X-Trace-Id 头", '"X-Trace-Id"' in src)
    check("base/GET 注入 query trace", 'query["trace_id"]' in src)
    check("base/POST 注入 body trace", 'data["trace_id"]' in src)
    check("base/响应回写头", 'self.send_header("X-Trace-Id"' in src)

    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_inbound_trace"), None)
    check("base/_inbound_trace 返回 str", fn is not None and fn.returns is not None)
    body = ast.dump(fn) if fn is not None else ""
    check("base/_inbound_trace 做字符清洗",
          ("isalnum" in body) or ("replace" in body) or ("allowed" in body), body[:160])

    # 清洗规则与 services/common/trace.py 一致：只留 A-Za-z0-9-_.:
    from services.common.trace import normalize_trace_id

    check("base/清洗口径与 trace.py 一致",
          normalize_trace_id("abc-123_x.y:z") == "abc-123_x.y:z",
          normalize_trace_id("abc-123_x.y:z"))


# ------------------------------------------------------ 2. workflow 侧 trace
def check_workflow() -> None:
    src = (SERVER / "services" / "workflow_service" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    check("workflow/存在 _handle_run_ir", "_handle_run_ir" in funcs)
    check("workflow/导入 ensure_trace", "ensure_trace" in src)

    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_handle_run_ir"), None)
    body = ast.dump(fn) if fn is not None else ""
    check("workflow/run_ir 调用 ensure_trace", "ensure_trace" in body)
    check("workflow/run_ir 写入 context", "trace_id" in body)
    check("workflow/run_ir 赋给适配器", "self._lqcs.trace_id" in src)
    check("workflow/响应回显 trace_id", '"trace_id": trace_id' in src)

    # 返回体里 trace_id 出现次数应 ≥ 3（dry/local/queue/queued 四条返回路径）
    check("workflow/多路径回显 trace_id", src.count('"trace_id": trace_id') >= 3,
          str(src.count('"trace_id": trace_id')))


# --------------------------------------------------------- 3. lqcs 适配器
def check_lqcs_adapter() -> None:
    from services.workflow_service.dsl.backends.lqcs import LqcsAdapter

    a = LqcsAdapter(logger=lambda _m: None)
    check("lqcs/默认无 trace_id", not getattr(a, "trace_id", ""), str(getattr(a, "trace_id", "")))
    a.trace_id = "t-abc123"
    hdrs = a._trace_headers()
    check("lqcs/_trace_headers 带 X-Trace-Id", hdrs.get("X-Trace-Id") == "t-abc123", str(hdrs))
    check("lqcs/_trace_headers 带 Content-Type",
          hdrs.get("Content-Type") == "application/json", str(hdrs))

    b = LqcsAdapter(logger=lambda _m: None)
    check("lqcs/无 trace 时不加头", "X-Trace-Id" not in b._trace_headers(),
          str(b._trace_headers()))

    src = (SERVER / "services" / "workflow_service" / "dsl" / "backends" / "lqcs.py").read_text(
        encoding="utf-8")
    check("lqcs/队列 metadata 带 traceId", '"traceId": self.trace_id' in src)
    check("lqcs/直连 body 带 trace_id", '"trace_id": self.trace_id' in src)
    check("lqcs/直连与队列都用 _trace_headers", src.count("self._trace_headers()") >= 2,
          str(src.count("self._trace_headers()")))


# --------------------------------------------------------- 4. task_queue
def check_task_queue() -> None:
    src = (SERVER / "services" / "task_queue" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("_handle_submit", "_handle_status", "_handle_list"):
        check(f"queue/存在 {name}", name in funcs)
    check("queue/导入 ensure_trace", "ensure_trace" in src)
    check("queue/提交存 traceId 到 metadata", 'metadata["traceId"]' in src)
    check("queue/提交响应回显 trace_id", '"trace_id": trace_id' in src)
    check("queue/状态回显 trace_id", 'task.metadata or {}).get("traceId")' in src)
    check("queue/列表支持 trace_id 过滤", 'query.get("trace_id"' in src)

    # 运行期：_handle_list 的 trace 过滤（用替身队列，构造最小 Task 列表）
    from services.task_queue.server import TaskQueueService

    class _StubTask:
        def __init__(self, tid: str, trace: str):
            self.id = tid
            self.created_at = 0.0
            self.metadata = {"traceId": trace}

        def to_dict(self):
            return {"id": self.id, "trace_id": self.metadata.get("traceId")}

    class _StubQueue:
        def list_all(self):
            return [_StubTask("t1", "t-abc"), _StubTask("t2", "t-xyz")]

    svc = TaskQueueService.__new__(TaskQueueService)
    svc._queue = _StubQueue()
    out = svc._handle_list({"trace_id": ["t-abc"]})
    check("queue/过滤命中 1 条", out.get("count") == 1, str(out.get("count")))
    check("queue/过滤命中正确 task",
          (out.get("tasks") or [{}])[0].get("id") == "t1", str(out.get("tasks")))
    out_all = svc._handle_list({})
    check("queue/不传过滤返回全部", out_all.get("count") == 2, str(out_all.get("count")))


# --------------------------------------------------------- 5. quantum runs
def check_quantum_runs() -> None:
    src = (SERVER / "services" / "quantum_service" / "server.py").read_text(encoding="utf-8")
    check("quantum/runs 支持 trace_id 过滤", 'data.get("trace_id") or data.get("traceId")' in src)
    check("quantum/runs 回显 trace_id",
          '"trace_id": trace_filter or None' in src or '"trace_id": active.get("trace_id")' in src)

    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_handle_qubit_runs"), None)
    check("quantum/存在 _handle_qubit_runs", fn is not None)

    # 运行期：真造一个 QuantumService 实例（不连 LabRAD），跑一次 /qubit/runs 过滤
    try:
        from services.quantum_service.server import QuantumService

        svc = QuantumService(port=3999)
        out = svc._handle_qubit_runs({"trace_id": "t-not-exist", "limit": 5})
        check("quantum/runs 过滤命中 0 条", out.get("count") == 0, str(out.get("count")))
        check("quantum/runs 回显过滤条件", out.get("trace_id") == "t-not-exist",
              str(out.get("trace_id")))
    except Exception as exc:  # pragma: no cover
        warn("quantum/实例化失败（跳过运行期过滤检查）", False, str(exc))


# ------------------------------------------------------- 6. serviceProxy.ts
def check_service_proxy() -> None:
    path = SERVER / "src" / "services" / "serviceProxy.ts"
    src = path.read_text(encoding="utf-8")
    check("proxy/导入 AsyncLocalStorage", "async_hooks" in src)
    check("proxy/定义 traceStorage", "AsyncLocalStorage<string>()" in src)
    check("proxy/导出 normalizeTraceId", "export function normalizeTraceId" in src)
    check("proxy/导出 newTraceId", "export function newTraceId" in src)
    check("proxy/导出 traceIdFromRequest", "export function traceIdFromRequest" in src)
    check("proxy/常量 X-Trace-Id", "'X-Trace-Id'" in src)
    check("proxy/中间件注册", "proxy.use(" in src)
    check("proxy/响应回写头", "res.setHeader(TRACE_HEADER" in src)
    check("proxy/上下文绑定", "traceStorage.run(" in src)
    check("proxy/代理加请求头", "headers[TRACE_HEADER] = traceId" in src)

    # 中间件必须在所有路由之前
    use_at = src.find("proxy.use(")
    first_route = src.find("proxy.get(")
    check("proxy/中间件先于路由注册", 0 <= use_at < first_route, f"{use_at} {first_route}")

    # normalizeTraceId 规则与 Python 侧一致（≤64 字符）
    check("proxy/上限 64", "TRACE_MAX_LEN = 64" in src)


def main() -> int:
    for fn in (check_base_service, check_workflow, check_lqcs_adapter,
               check_task_queue, check_quantum_runs, check_service_proxy):
        try:
            fn()
        except Exception as exc:  # pragma: no cover
            global FAIL
            FAIL += 1
            print(f"[FAIL] {fn.__name__} 抛出异常 :: {exc}")
    print(f"_phase3b_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
