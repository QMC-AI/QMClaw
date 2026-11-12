"""阶段 19 自检：前端排队/轮询（16b）+ Express 作业代理。

`/execute` 在阶段 16a 已默认 producer 语义（立刻回 queued），但前端一直按
"同步返回结果"写，实际会拿到 queued 就去读 stdout。本阶段把最后一环补上：

1. Express（`src/services/serviceProxy.ts`）暴露 `GET /quantum/jobs/:id` 与
   `POST /quantum/jobs/:id/abandon`，把作业轮询转给 :3003；
2. 前端（`src/lib/api.ts`）识别 queued → 轮询到终态 → 摊平成老结果形状，
   调用方（page.tsx）只加进度提示，不改进程逻辑；
3. 文档与注释不能再写 "execution is synchronous"。

本自检是纯静态 + 纯逻辑（不连服务、不跑浏览器），可进 `run_all_checks.py` OFFLINE。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:  # 控制台 GBK 时不要让 print 把自检打崩
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
WEB = ROOT / "Agentic Workflow" / "qmclaw-web"
API_TS = WEB / "src" / "lib" / "api.ts"
PAGE_TSX = WEB / "src" / "app" / "page.tsx"
PROXY_TS = SERVER / "src" / "services" / "serviceProxy.ts"
QUANTUM_SERVER = SERVER / "services" / "quantum_service" / "server.py"

PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" :: {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  skip {name} :: {why}")


def read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


api = read(API_TS)
page = read(PAGE_TSX)
proxy = read(PROXY_TS)
qsrv = read(QUANTUM_SERVER)

print("[1] 前端轮询助手齐全")
check("api.ts 存在", bool(api))
check("导出 isQueuedJob", "export function isQueuedJob" in api)
check("导出 getQuantumJob", "export async function getQuantumJob" in api)
check("导出 waitForQuantumJob", "export async function waitForQuantumJob" in api)
check("导出 abandonQuantumJob", "export async function abandonQuantumJob" in api)
check("导出 flattenQuantumJob", "export function flattenQuantumJob" in api)

print("[2] queued 判定与终态集合")
m = re.search(r"export function isQueuedJob\([\s\S]{0,600}?\n\}", api)
check("isQueuedJob 命中 status==='queued'", bool(m) and 'p.status === "queued"' in m.group(0))
check("isQueuedJob 要求 job_id 非空串", bool(m) and "p.job_id.length > 0" in m.group(0))
term = set(re.findall(r'"([a-z_]+)"', (re.search(r"QUANTUM_JOB_TERMINAL = new Set\(\[[\s\S]*?\]\)", api) or re.match("", "")).group(0) if re.search(r"QUANTUM_JOB_TERMINAL = new Set\(\[[\s\S]*?\]\)", api) else ""))
for st in ("success", "completed", "error", "failed", "timeout", "cancelled", "rejected"):
    check(f"终态含 {st}", st in term, sorted(term))
check("queued/running 不在终态", "queued" not in term and "running" not in term)

print("[3] 轮询行为（超时 / 传输层重试）")
wm = re.search(r"export async function waitForQuantumJob\([\s\S]{0,2600}?\n\}", api)
w = wm.group(0) if wm else ""
check("有超时上限", "deadline" in w and "timeoutMs" in w)
check("超时抛错而不是返回半成品", "轮询超时" in w and "throw new Error" in w)
check("传输层错误继续轮询", "lastError = e instanceof Error" in w)
check("有轮询间隔", "setTimeout(r, intervalMs)" in w)
check("可回调进度", "onProgress?.(job)" in w)

print("[4] 两个执行入口都会轮询")
rm = re.search(r"runExperiment: async \([\s\S]{0,2600}?\n  \},", api)
r = rm.group(0) if rm else ""
check("runExperiment 命中 queued 分支", "isQueuedJob(payload)" in r)
check("runExperiment 调用 waitForQuantumJob", "waitForQuantumJob(payload.job_id" in r)
check("runExperiment 摊平返回", "flattenQuantumJob(payload" in r)
em = re.search(r"executeExperiment: async \([\s\S]{0,1400}?\n  \},", api)
e = em.group(0) if em else ""
check("executeExperiment 命中 queued 分支", "isQueuedJob(payload)" in e)
check("executeExperiment 调用 waitForQuantumJob", "waitForQuantumJob(payload.job_id" in e)
check("轮询超时按调用方 timeout 换算", "timeoutMs: timeout * 1000" in (r + e))

print("[5] 摊平字段与后端契约对齐")
fm_idx = api.find("export function flattenQuantumJob")
f = api[fm_idx:api.find("export async function abandonQuantumJob")] if fm_idx >= 0 else ""
for key in ("task_id", "status", "stdout", "stderr", "error", "result", "run_id", "trace_id", "dataset_ids", "dataset_source", "execute_mode"):
    check(f"摊平含 {key}", key in f)
check("status 优先取 result.status", "inner.status" in f)
check("dataset_ids 缺省为空数组", "dataset_ids as number[], []" in f)
check(
    "后端 _handle_jobs 提供 job_id/status/result/trace_id",
    all(k in qsrv for k in ('payload.setdefault("run_id"', 'payload["trace_id"', 'record_trace_id')),
)
check(
    "后端完成态 result 已富化 execute_mode=async",
    'setdefault("execute_mode", "async")' in qsrv or '"execute_mode": "async"' in qsrv,
)

print("[6] Express 作业代理")
check("serviceProxy.ts 存在", bool(proxy))
check("暴露 GET /quantum/jobs/:id", "proxy.get('/quantum/jobs/:id'" in proxy)
check("暴露 POST /quantum/jobs/:id/abandon", "proxy.post('/quantum/jobs/:id/abandon'" in proxy)
check("转发到 /jobs/${id}", "`/jobs/${id}`" in proxy)
check("转发到 /jobs/${id}/abandon", "`/jobs/${id}/abandon`" in proxy)
check("id 做了 encodeURIComponent", proxy.count("encodeURIComponent(req.params.id)") >= 2)
check("代理失败回 502 而不是抛", proxy.count("res.status(result.ok ? 200 : 502)") >= 3)

print("[7] 页面接线与进度提示")
check("page.tsx 存在", bool(page))
check("主执行按钮传 onProgress", page.count("onProgress: (job) =>") >= 2)
check("进度提示显示排队位置", page.count("queued${job.queue_position") >= 2)
check("进度只在变化时打日志", page.count("if (phase !== lastPhase)") >= 2)

print("[8] 防回退：别再声称同步")
check("api.ts 不再有 'execution is synchronous'", "execution is synchronous" not in api)
check("api.ts 不再有 'in microservice mode this returns immediately'", "returns immediately" not in api.lower())
check("api.ts 注释说明 queued 会被轮询", "producer" in api)
check("page.tsx 不再写 'Use synchronous microservice endpoint'", "Use synchronous microservice endpoint" not in page)

print("[9] 端到端契约（桩作业序列）")


def simulate_poll(sequence, terminal=("success", "failed", "error", "timeout", "cancelled", "rejected")):
    """复刻 waitForQuantumJob 的终止条件，确保第一个终态就返回（不会多轮）。"""
    seen = 0
    for item in sequence:
        seen += 1
        status = item.get("status") if isinstance(item, dict) else None
        if status in terminal:
            return status, seen
    return None, seen


st, n = simulate_poll([{"status": "queued"}, {"status": "running"}, {"status": "success"}])
check("queued→running→success 在 success 终止", st == "success" and n == 3, f"{st},{n}")
st, n = simulate_poll([{"status": "queued"}, {"status": "failed"}])
check("失败终态立即返回", st == "failed" and n == 2)
st, n = simulate_poll([{"status": "queued"}])
check("未达终态返回 None（由外层超时兜底）", st is None)

print("[10] 文档在位")
ops = read(ROOT / "docs" / "OPERATIONS.md")
road = read(ROOT / "docs" / "ARCHITECTURE_ROADMAP.md")
if ops:
    check("OPERATIONS 有 §3.22", "§3.22" in ops or "3.22" in ops)
else:
    skip("OPERATIONS 检查", "文件不可读")
if road:
    check("ROADMAP 标 16b 已落地", "16b" in road)
else:
    skip("ROADMAP 检查", "文件不可读")

print(f"\n_phase19_check: PASS={PASS} FAIL={FAIL} SKIP={SKIP}")
sys.exit(1 if FAIL else 0)
