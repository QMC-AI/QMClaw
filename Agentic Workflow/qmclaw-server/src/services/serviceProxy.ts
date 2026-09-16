/**
 * src/services/serviceProxy.ts - Express 服务代理
 *
 * 统一代理请求到各微服务
 * 设计原则：简单、可靠、易维护
 */

import { Router, Request, Response } from 'express';

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

  try {
    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort(), timeout);

    const response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
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
    const result = await proxyToService('workflow', '/workflows/run', 'POST', req.body, 300000);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/status', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/status', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  });

  proxy.post('/workflow/cancel', async (req, res) => {
    const result = await proxyToService('workflow', '/workflows/cancel', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
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
