/**
 * src/services/serviceProxy.ts - Express 服务代理
 *
 * 统一代理请求到各微服务
 * 设计原则：简单、可靠、易维护
 */

import { Router, Request, Response } from 'express';
import { AsyncLocalStorage } from 'async_hooks';

// ─────────────────────────────────────────────────────────────────────────────
// traceId 贯穿（阶段 3）
// 每个进入 /api 的请求生成或透传 X-Trace-Id，代理到微服务时带上同一 trace，
// 并在响应头回写，便于把 Express → workflow → task_queue → quantum 串起来。
// ─────────────────────────────────────────────────────────────────────────────

const TRACE_HEADER = 'X-Trace-Id';
const TRACE_MAX_LEN = 64;

const traceStorage = new AsyncLocalStorage<string>();

/** 清洗 trace id：只保留字母数字与 -_.:，最长 64 */
export function normalizeTraceId(value: unknown): string {
  if (typeof value !== 'string') return '';
  const raw = value.trim();
  if (!raw) return '';
  const safe = raw.replace(/[^A-Za-z0-9\-_.:]/g, '');
  return safe.slice(0, TRACE_MAX_LEN);
}

/** 生成新的 trace id */
export function newTraceId(): string {
  const hex = Array.from({ length: 12 }, () =>
    Math.floor(Math.random() * 16).toString(16)).join('');
  return `t-${hex}`;
}

/** 从请求头取 trace id（数组取首个） */
export function traceIdFromRequest(req: Request): string {
  const raw = req.headers['x-trace-id'];
  const first = Array.isArray(raw) ? raw[0] : raw;
  return normalizeTraceId(first);
}

// ─────────────────────────────────────────────────────────────────────────────
// 类型定义
// ─────────────────────────────────────────────────────────────────────────────

interface ServiceConfig {
  host: string;
  port: number;
  /** 默认超时时间 (毫秒) */
  defaultTimeout?: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// 配置
// ─────────────────────────────────────────────────────────────────────────────

const SERVICES: Record<string, ServiceConfig> = {
  llm: { host: 'localhost', port: 3006, defaultTimeout: 60000 },
  quantum: { host: 'localhost', port: 3003, defaultTimeout: 120000 },
  analysis: { host: 'localhost', port: 3004, defaultTimeout: 60000 },
  agent: { host: 'localhost', port: 3005, defaultTimeout: 300000 },
  image: { host: 'localhost', port: 3007, defaultTimeout: 300000 },
  workflow: { host: 'localhost', port: 3008, defaultTimeout: 300000 },
  task_queue: { host: 'localhost', port: 3009, defaultTimeout: 30000 },
  qubitclient: { host: 'localhost', port: 3010, defaultTimeout: 120000 },
  qca: { host: 'localhost', port: 3011, defaultTimeout: 300000 },
};

// ─────────────────────────────────────────────────────────────────────────────
// 熔断器 - 防止级联故障
// ─────────────────────────────────────────────────────────────────────────────

interface CircuitBreaker {
  failures: number;
  lastFailure: number;
  state: 'closed' | 'open' | 'half-open';
}

const CIRCUIT_BREAKER_THRESHOLD = 5;           // 失败 5 次后断路
const CIRCUIT_BREAKER_TIMEOUT = 30000;         // 30 秒后尝试半开
const circuitBreakers: Record<string, CircuitBreaker> = {};

// 获取熔断器状态
function getCircuitBreaker(name: string): CircuitBreaker {
  if (!circuitBreakers[name]) {
    circuitBreakers[name] = { failures: 0, lastFailure: 0, state: 'closed' };
  }
  return circuitBreakers[name];
}

// 记录失败
function recordFailure(name: string): void {
  const cb = getCircuitBreaker(name);
  cb.failures++;
  cb.lastFailure = Date.now();
  if (cb.failures >= CIRCUIT_BREAKER_THRESHOLD) {
    cb.state = 'open';
    console.log(`[CircuitBreaker] ${name}: OPEN (failures=${cb.failures})`);
  }
}

// 记录成功
function recordSuccess(name: string): void {
  const cb = getCircuitBreaker(name);
  cb.failures = 0;
  cb.state = 'closed';
}

// 检查是否可以请求
function canRequest(name: string): { allowed: boolean; reason?: string } {
  const cb = getCircuitBreaker(name);

  if (cb.state === 'closed') {
    return { allowed: true };
  }

  if (cb.state === 'open') {
    const elapsed = Date.now() - cb.lastFailure;
    if (elapsed >= CIRCUIT_BREAKER_TIMEOUT) {
      cb.state = 'half-open';
      console.log(`[CircuitBreaker] ${name}: HALF-OPEN (trying...)`);
      return { allowed: true };
    }
    return { allowed: false, reason: `Circuit open, retry in ${Math.ceil((CIRCUIT_BREAKER_TIMEOUT - elapsed) / 1000)}s` };
  }

  // half-open: allow one request to test
  return { allowed: true };
}

// ─────────────────────────────────────────────────────────────────────────────
// 核心代理
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 代理请求到指定服务（带熔断保护和超时）
 */
async function proxyToService(
  serviceName: string,
  path: string,
  method: 'GET' | 'POST' | 'DELETE',
  body?: unknown,
  timeoutMs?: number
): Promise<{ ok: boolean; data?: unknown; error?: string }> {
  const service = SERVICES[serviceName];

  if (!service) {
    return { ok: false, error: `Unknown service: ${serviceName}` };
  }

  // 检查熔断器
  const { allowed, reason } = canRequest(serviceName);
  if (!allowed) {
    return { ok: false, error: `Service unavailable: ${reason}` };
  }

  const url = `http://${service.host}:${service.port}${path}`;
  const timeout = timeoutMs ?? service.defaultTimeout ?? 30000;
  const traceId = traceStorage.getStore();

  try {
    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort(), timeout);

    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (traceId) headers[TRACE_HEADER] = traceId;

    const response = await fetch(url, {
      method,
      headers,
      body: body != null ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });

    clearTimeout(timeoutHandle);

    // 任何 HTTP 错误都记录为失败
    if (!response.ok) {
      recordFailure(serviceName);
      const data = await response.json().catch(() => ({}));
      return { ok: false, error: data.error || `HTTP ${response.status}` };
    }

    recordSuccess(serviceName);
    const data = await response.json();
    return { ok: true, data };
  } catch (err: unknown) {
    const error = err instanceof Error ? err : new Error(String(err));

    recordFailure(serviceName);

    if (error.name === 'AbortError') {
      return { ok: false, error: `Timeout after ${timeout}ms` };
    }

    return { ok: false, error: error.message };
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 路由
// ─────────────────────────────────────────────────────────────────────────────

export function createServiceProxy() {
  const proxy = Router();

  // ── traceId 中间件（必须最先注册，包裹全部 81 个路由）────────────────────
  proxy.use((req: Request, res: Response, next: (err?: unknown) => void) => {
    const traceId = traceIdFromRequest(req) || newTraceId();
    res.setHeader(TRACE_HEADER, traceId);
    traceStorage.run(traceId, () => next());
  });

  // ── LLM 服务 ───────────────────────────────────────────────────────────────

  proxy.post('/llm/chat', async (req, res) => {
    const result = await proxyToService('llm', '/chat', 'POST', req.body, 60000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/llm/models', async (_req, res) => {
    const result = await proxyToService('llm', '/models', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/llm/stats', async (_req, res) => {
    const result = await proxyToService('llm', '/stats', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 量子测控服务 ───────────────────────────────────────────────────────────

  proxy.post('/quantum/connect', async (req, res) => {
    const result = await proxyToService('quantum', '/connect', 'POST', req.body, 120000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/quantum/status', async (_req, res) => {
    const result = await proxyToService('quantum', '/status', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/quantum/qubits', async (_req, res) => {
    const result = await proxyToService('quantum', '/qubits', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/quantum/experiments', async (_req, res) => {
    const result = await proxyToService('quantum', '/experiments', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/quantum/execute', async (req, res) => {
    const result = await proxyToService('quantum', '/execute', 'POST', req.body, 300000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 作业轮询（阶段 19：/execute 默认 producer 化后，结果从这里取）──────
  // 后端默认返回 { status: "queued", job_id, run_id }，前端拿到 job_id 后轮询这里，
  // 完成态的 result 已带上 dataset_ids / trace_id / execute_mode（见 OPERATIONS §3.19）。
  proxy.get('/quantum/jobs/:id', async (req, res) => {
    const id = encodeURIComponent(req.params.id);
    const result = await proxyToService('quantum', `/jobs/${id}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/quantum/jobs/:id/abandon', async (req, res) => {
    const id = encodeURIComponent(req.params.id);
    const result = await proxyToService('quantum', `/jobs/${id}/abandon`, 'POST', req.body ?? {});
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/quantum/switch_session', async (req, res) => {
    const result = await proxyToService('quantum', '/switch_session', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/quantum/sessions', async (_req, res) => {
    const result = await proxyToService('quantum', '/sessions', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/quantum/session_tree', async (req, res) => {
    const maxDepth = parseInt((req.query.max_depth as string) ?? '5', 10);
    const result = await proxyToService('quantum', '/session_tree', 'POST', { max_depth: maxDepth });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/quantum/qubit/params', async (req, res) => {
    const name = req.query.name as string;
    if (!name) {
      return res.status(400).json({ error: 'Missing parameter: name' });
    }
    const result = await proxyToService('quantum', '/qubit/params', 'POST', { name });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/quantum/qubit/set_params', async (req, res) => {
    const result = await proxyToService('quantum', '/qubit/set_params', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  /** 把数据集里记录的寄存器快照写回量子比特（实验后对齐现场 / 回滚） */
  proxy.post('/quantum/qubit/apply_dataset', async (req, res) => {
    const result = await proxyToService('quantum', '/qubit/apply_dataset', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/quantum/datasets', async (req, res) => {
    const path = req.query.path as string | undefined;
    const result = await proxyToService('quantum', '/datasets', 'POST', { path: path ?? null });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 分析服务 ───────────────────────────────────────────────────────────────

  proxy.post('/analysis/plot', async (req, res) => {
    const result = await proxyToService('analysis', '/plot', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/analysis/plot/historical', async (req, res) => {
    const result = await proxyToService('analysis', '/plot/historical', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/analysis/plot/experiments', async (req, res) => {
    const result = await proxyToService('analysis', '/plot/experiments', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/analysis/stats', async (req, res) => {
    const result = await proxyToService('analysis', '/stats', 'POST', req.query);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/analysis/datasets', async (_req, res) => {
    const result = await proxyToService('analysis', '/datasets', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 离线数据绘图 (新版 - 支持 qter.fitData 风格) ───────────────────────────

  proxy.post('/analysis/plot/offline/v2', async (req, res) => {
    const result = await proxyToService('analysis', '/plot/offline/v2', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 变体生成 API ───────────────────────────────────────────────────────────────

  proxy.get('/analysis/variants/types', async (_req, res) => {
    const result = await proxyToService('analysis', '/variants/types', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/analysis/variants/generate', async (req, res) => {
    const result = await proxyToService('analysis', '/variants/generate', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/analysis/variants/list', async (req, res) => {
    const query = req.query;
    const sourceId = query.source_id as string | undefined;
    const path = sourceId ? `/variants/list?source_id=${encodeURIComponent(sourceId)}` : '/variants/list';
    const result = await proxyToService('analysis', path, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/analysis/variants/plot', async (req, res) => {
    const result = await proxyToService('analysis', '/variants/plot', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── Agent 服务 ─────────────────────────────────────────────────────────────

  proxy.post('/agent/chat', async (req, res) => {
    const result = await proxyToService('agent', '/chat', 'POST', req.body, 300000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/agent/tasks', async (_req, res) => {
    const result = await proxyToService('agent', '/tasks', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/agent/tools', async (_req, res) => {
    const result = await proxyToService('agent', '/tools', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 图像服务 ───────────────────────────────────────────────────────────────

  proxy.post('/image/classify/single', async (req, res) => {
    const result = await proxyToService('image', '/classify/single', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/image/classify/folder', async (req, res) => {
    const result = await proxyToService('image', '/classify/folder', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/image/train', async (req, res) => {
    const result = await proxyToService('image', '/train', 'POST', req.body, 600000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/image/model/info', async (_req, res) => {
    const result = await proxyToService('image', '/model/info', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 工作流服务 ─────────────────────────────────────────────────────────────

  proxy.get('/workflow/list', async (_req, res) => {
    const result = await proxyToService('workflow', '/workflows', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/create', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/create', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/run', async (req, res) => {
    const body = req.body || {};
    let workflowId = body.workflow_id || body.workflowId;
    // 前端直接提交 nodes（无 workflow_id）时，先自动创建工作流再运行
    if (!workflowId && Array.isArray(body.nodes)) {
      const created = await proxyToService('workflow', '/workflows/create', 'POST', {
        name: body.name || 'Untitled Workflow',
        nodes: body.nodes,
      });
      if (!created.ok || !created.data?.workflow_id) {
        res.status(502).json(created.data ?? { error: created.error || 'Failed to create workflow' });
        return;
      }
      workflowId = created.data.workflow_id;
    }
    if (!workflowId) {
      res.status(400).json({ error: 'workflow_id is required' });
      return;
    }
    const result = await proxyToService('workflow', '/workflows/run', 'POST', {
      workflow_id: workflowId,
      context: body.context || {},
    }, 300000);
    // 前端读取 workflowId（驼峰），后端返回 workflow_id（下划线）——补上驼峰字段
    if (result.ok) {
      const data: any = result.data || {};
      if (data.workflow_id && !data.workflowId) data.workflowId = data.workflow_id;
      res.status(200).json(data);
    } else {
      res.status(502).json(result.data ?? { error: result.error });
    }
  });

  proxy.post('/workflow/status', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/status', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/cancel', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/cancel', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── DSL（IR 层）────────────────────────────────────────────────────────────
  // validate / parse_qwf / ir_from_canvas 是纯计算，不碰硬件；
  // run_ir 只有显式传 dryRun:true 才安全，所以这里原样透传、不替调用方改默认值。

  proxy.post('/workflow/validate', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/validate', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/parse_qwf', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/parse_qwf', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/ir_from_canvas', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/ir_from_canvas', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── `.qwf` ⇄ 画布 ─────────────────────────────────────────────────────────
  // qwf_examples   列出 data/workflows/examples 下的 .qwf 示例
  // qwf_to_canvas  .qwf 文本 → 画布节点图（IR 是中表示）
  // canvas_to_qwf  画布节点图 → .qwf 文本（带语义等价判定）

  proxy.post('/workflow/qwf_examples', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/qwf_examples', 'POST', req.body || {});
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/qwf_to_canvas', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/qwf_to_canvas', 'POST', req.body || {});
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/canvas_to_qwf', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/canvas_to_qwf', 'POST', req.body || {});
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/stmt_to_qwf', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/stmt_to_qwf', 'POST', req.body || {});
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 工作流存档（`.qwf`）──────────────────────────────────────────────────
  // 存档统一是 `.qwf` 文本（服务端 data/workflows/*.qwf），
  // 画布快照 JSON 不再作为存档格式。

  proxy.get('/workflows', async (_req, res) => {
    const result = await proxyToService('workflow', '/workflows/saved', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/workflows/:id', async (req, res) => {
    const result = await proxyToService('workflow', `/workflows/saved/${encodeURIComponent(req.params.id)}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflows', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/saved', 'POST', req.body || {});
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.put('/workflows/:id', async (req, res) => {
    const body = { ...(req.body || {}), id: req.params.id };
    const result = await proxyToService('workflow', '/workflows/saved', 'POST', body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.delete('/workflows/:id', async (req, res) => {
    const result = await proxyToService('workflow', `/workflows/saved/${encodeURIComponent(req.params.id)}`, 'DELETE');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/run_ir', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/run_ir', 'POST', req.body);
    if (result.ok) {
      const data: any = result.data || {};
      // 与 /workflow/run 保持一致：前端读驼峰，后端给下划线
      if (data.workflow_id && !data.workflowId) data.workflowId = data.workflow_id;
      res.status(200).json(data);
    } else {
      res.status(502).json(result.data ?? { error: result.error });
    }
  });

  // ── 工作流历史记录 ─────────────────────────────────────────────────────────

  proxy.get('/workflow-runs', async (req, res) => {
    const query = new URLSearchParams(req.query as Record<string, string>).toString();
    const path = `/runs${query ? `?${query}` : ''}`;
    const result = await proxyToService('workflow', path, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/workflow-runs/stats/:workflowId', async (req, res) => {
    const result = await proxyToService('workflow', `/runs/stats/${req.params.workflowId}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.delete('/workflow-runs/workflow/:workflowId', async (req, res) => {
    const result = await proxyToService('workflow', `/runs/workflow/${req.params.workflowId}`, 'DELETE');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/workflow-runs/:runId', async (req, res) => {
    const result = await proxyToService('workflow', `/runs/${req.params.runId}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.delete('/workflow-runs/:runId', async (req, res) => {
    const result = await proxyToService('workflow', '/runs/delete', 'POST', { runId: req.params.runId });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 任务队列服务 ───────────────────────────────────────────────────────────

  proxy.post('/tasks/submit', async (req, res) => {
    const result = await proxyToService('task_queue', '/submit', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/tasks/status', async (req, res) => {
    const result = await proxyToService('task_queue', '/status', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/tasks/list', async (req, res) => {
    const query = new URLSearchParams(req.query as Record<string, string>).toString();
    const path = `/list${query ? `?${query}` : ''}`;
    const result = await proxyToService('task_queue', path, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/tasks/cancel', async (req, res) => {
    const result = await proxyToService('task_queue', '/cancel', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/tasks/stats', async (_req, res) => {
    const result = await proxyToService('task_queue', '/stats', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // 硬件通道（lane）：谁占着通道、谁在排队、租约还剩多久
  proxy.get('/tasks/lane', async (_req, res) => {
    const result = await proxyToService('task_queue', '/lane', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // 强制释放租约：只在确认持有者已死/卡住时用
  proxy.post('/tasks/lease/release', async (req, res) => {
    const result = await proxyToService('task_queue', '/lease/release', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── QubitClient 服务 (VLM 图像分析) ──────────────────────────────────────────

  proxy.get('/qubitclient/health', async (_req, res) => {
    const result = await proxyToService('qubitclient', '/health', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/qubitclient/families', async (_req, res) => {
    const result = await proxyToService('qubitclient', '/families', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qubitclient/describe', async (req, res) => {
    const result = await proxyToService('qubitclient', '/describe', 'POST', req.body, 180000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qubitclient/classify', async (req, res) => {
    const result = await proxyToService('qubitclient', '/classify', 'POST', req.body, 180000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qubitclient/reasoning', async (req, res) => {
    const result = await proxyToService('qubitclient', '/reasoning', 'POST', req.body, 180000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qubitclient/assess_fit', async (req, res) => {
    const result = await proxyToService('qubitclient', '/assess_fit', 'POST', req.body, 180000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qubitclient/extract_params', async (req, res) => {
    const result = await proxyToService('qubitclient', '/extract_params', 'POST', req.body, 180000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qubitclient/evaluate', async (req, res) => {
    const result = await proxyToService('qubitclient', '/evaluate', 'POST', req.body, 180000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qubitclient/analyze_full', async (req, res) => {
    const result = await proxyToService('qubitclient', '/analyze_full', 'POST', req.body, 300000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── QCA 服务 ───────────────────────────────────────────────────────────────

  proxy.get('/qca/capabilities', async (_req, res) => {
    const result = await proxyToService('qca', '/capabilities', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/qca/schema/:name', async (req, res) => {
    const result = await proxyToService('qca', `/schema/${req.params.name}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qca/run', async (req, res) => {
    const result = await proxyToService('qca', '/run', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qca/lab', async (req, res) => {
    const result = await proxyToService('qca', '/lab', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/qca/history', async (req, res) => {
    const last = req.query.last ? `?last=${req.query.last}` : '';
    const type = req.query.type ? `?type=${req.query.type}` : '';
    const query = last || type ? (last + type) : '';
    const result = await proxyToService('qca', `/history${query}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/qca/history/:id', async (req, res) => {
    const result = await proxyToService('qca', `/history/${req.params.id}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.get('/qca/workflows', async (_req, res) => {
    const result = await proxyToService('qca', '/workflows', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/qca/chat', async (req, res) => {
    const result = await proxyToService('qca', '/chat', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  // ── 健康检查 ───────────────────────────────────────────────────────────────

  proxy.get('/services/health', async (_req, res) => {
    const checks = await Promise.all(
      Object.keys(SERVICES).map(async (name) => {
        const result = await proxyToService(name, '/health', 'GET', undefined, 5000);
        return { name, reachable: result.ok };
      })
    );

    const services: Record<string, { reachable: boolean }> = {};
    for (const check of checks) {
      services[check.name] = { reachable: check.reachable };
    }

    res.json({ services });
  });

  // ── 熔断器状态 ───────────────────────────────────────────────────────────────

  proxy.get('/services/circuit-breaker', (_req, res) => {
    const status: Record<string, { state: string; failures: number; lastFailure: number }> = {};
    for (const [name, cb] of Object.entries(circuitBreakers)) {
      status[name] = {
        state: cb.state,
        failures: cb.failures,
        lastFailure: cb.lastFailure,
      };
    }
    res.json({ circuitBreakers: status });
  });

  return proxy;
}

export { SERVICES };
export default createServiceProxy;
