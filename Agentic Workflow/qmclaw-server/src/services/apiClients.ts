/**
 * src/services/apiClients.ts - TypeScript 服务客户端
 *
 * 简化的前端 API 客户端，调用 Express 网关
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:3002';

interface ApiResponse<T = unknown> {
  ok: boolean;
  data?: T;
  error?: string;
}

async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;

  try {
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || `API error: ${response.status}`);
    }

    return data as T;
  } catch (err: any) {
    console.error(`[API] ${endpoint} failed:`, err.message);
    throw err;
  }
}

// ── LLM 客户端 ───────────────────────────────────────────────────────────────

export const llmClient = {
  chat: (messages: Array<{ role: string; content: string }>, model = 'minimax', temperature = 0.7) =>
    apiRequest<{ content: string; usage?: Record<string, number> }>('/api/llm/chat', {
      method: 'POST',
      body: JSON.stringify({ messages, model, temperature }),
    }),

  models: () =>
    apiRequest<{ models: Array<{ id: string; name: string; provider: string }> }>('/api/llm/models'),

  stats: () =>
    apiRequest<{ total_requests: number; total_tokens: number }>('/api/llm/stats'),
};

// ── 测控服务客户端 ───────────────────────────────────────────────────────────

export const quantumClient = {
  connect: (sessionPath?: string[], timeout = 60) =>
    apiRequest<{ success: boolean; error?: string }>('/api/quantum/connect', {
      method: 'POST',
      body: JSON.stringify({ session_path: sessionPath, timeout }),
    }),

  status: () =>
    apiRequest<{ connected: boolean; session_path?: string[]; qubit_count?: number }>('/api/quantum/status'),

  qubits: () =>
    apiRequest<{ qubits: Array<{ name: string; f10?: number; fread?: number }> }>('/api/quantum/qubits'),

  experiments: () =>
    apiRequest<{ experiments: Array<{ name: string; fullName: string; doc: string }> }>('/api/quantum/experiments'),

  execute: (code: string, taskId?: string, timeout = 300) =>
    apiRequest<{ task_id: string; status: string; result?: unknown; error?: string }>('/api/quantum/execute', {
      method: 'POST',
      body: JSON.stringify({ code, task_id: taskId, timeout }),
    }),

  switchSession: (sessionPath: string[]) =>
    apiRequest<{ success: boolean; session_path?: string[] }>('/api/quantum/switch_session', {
      method: 'POST',
      body: JSON.stringify({ session_path: sessionPath }),
    }),

  sessions: () =>
    apiRequest<{ current: unknown; sessions: Array<{ name: string; path: string[] }> }>('/api/quantum/sessions'),

  sessionTree: (maxDepth = 5) =>
    apiRequest<{ tree: Array<{ name: string; path: string[]; hasChildren: boolean }> }>(
      `/api/quantum/session_tree?max_depth=${maxDepth}`
    ),

  qubitParams: (name: string) =>
    apiRequest<{ name: string; params: Record<string, number | null> }>(
      `/api/quantum/qubit/params?name=${encodeURIComponent(name)}`
    ),

  setQubitParams: (name: string, params: Record<string, number | null>) =>
    apiRequest<{ success: boolean; updated: string[]; errors?: string[] }>('/api/quantum/qubit/set_params', {
      method: 'POST',
      body: JSON.stringify({ name, params }),
    }),

  datasets: (path?: string) =>
    apiRequest<{ datasets: string[]; current_path?: string[] }>(
      path ? `/api/quantum/datasets?path=${encodeURIComponent(path)}` : '/api/quantum/datasets'
    ),
};

// ── 分析服务客户端 ───────────────────────────────────────────────────────────

export const analysisClient = {
  plot: (jobId: string, command?: string, datasetIndex = -1) =>
    apiRequest<{ success: boolean; plotPath?: string; plotUrl?: string; error?: string }>('/api/analysis/plot', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, command, dataset_index: datasetIndex }),
    }),

  plotHistorical: (name: string, path: string[], command?: string) =>
    apiRequest<{ success: boolean; plotPath?: string; error?: string }>('/api/analysis/plot/historical', {
      method: 'POST',
      body: JSON.stringify({ name, path, command }),
    }),

  stats: (datasetIndex = -1, axis?: number) =>
    apiRequest<{ success: boolean; stats?: Record<string, unknown> }>('/api/analysis/stats', {
      method: 'POST',
      body: JSON.stringify({ dataset_index: datasetIndex, axis }),
    }),

  datasets: (datasetPath?: string) =>
    apiRequest<{ success: boolean; datasets: unknown[] }>(`/api/analysis/datasets?path=${datasetPath || ''}`),
};

// ── Agent 服务客户端 ─────────────────────────────────────────────────────────

export const agentClient = {
  chat: (message: string, mode = 'react', context: Record<string, unknown> = {}) =>
    apiRequest<{ success: boolean; response?: string; error?: string }>('/api/agent/chat', {
      method: 'POST',
      body: JSON.stringify({ message, mode, context }),
    }),

  tasks: (status?: string) =>
    apiRequest<{ tasks: Array<{ task_id: string; status: string; message: string }> }>(
      status ? `/api/agent/tasks?status=${status}` : '/api/agent/tasks'
    ),

  tools: () =>
    apiRequest<{ tools: Array<{ name: string; description: string }> }>('/api/agent/tools'),
};

// ── 图像服务客户端 ───────────────────────────────────────────────────────────

export const imageClient = {
  classifySingle: (imagePath: string, threshold = 0.75) =>
    apiRequest<{ class?: string; confidence?: number; error?: string }>('/api/image/classify/single', {
      method: 'POST',
      body: JSON.stringify({ imagePath, threshold }),
    }),

  classifyFolder: (folderPath: string, threshold = 0.75, margin = 0.15) =>
    apiRequest<{ total?: number; stats?: Record<string, number>; results?: unknown[] }>('/api/image/classify/folder', {
      method: 'POST',
      body: JSON.stringify({ folderPath, threshold, margin }),
    }),

  train: (epochs = 20, batchSize = 32, imbalanceMode = 'weighted') =>
    apiRequest<{ success: boolean; message?: string }>('/api/image/train', {
      method: 'POST',
      body: JSON.stringify({ epochs, batchSize, imbalanceMode }),
    }),

  modelInfo: () =>
    apiRequest<{ model_path: string; model_loaded: boolean; classes: string[] }>('/api/image/model/info'),
};

// ── 工作流服务客户端 ─────────────────────────────────────────────────────────

export const workflowClient = {
  list: () =>
    apiRequest<{ workflows: Array<{ id: string; name: string; status: string }> }>('/api/workflow/list'),

  create: (name: string, nodes: Array<{ id: string; type: string; config: Record<string, unknown>; depends?: string[] }>) =>
    apiRequest<{ success: boolean; workflow_id?: string }>('/api/workflow/create', {
      method: 'POST',
      body: JSON.stringify({ name, nodes }),
    }),

  run: (workflowId: string, context: Record<string, unknown> = {}) =>
    apiRequest<{ success: boolean; workflow_id?: string; status?: string }>('/api/workflow/run', {
      method: 'POST',
      body: JSON.stringify({ workflow_id: workflowId, context }),
    }),

  status: (workflowId: string) =>
    apiRequest<{ id: string; name: string; status: string; nodes: unknown[] }>('/api/workflow/status', {
      method: 'POST',
      body: JSON.stringify({ workflow_id: workflowId }),
    }),

  cancel: (workflowId: string) =>
    apiRequest<{ success: boolean }>('/api/workflow/cancel', {
      method: 'POST',
      body: JSON.stringify({ workflow_id: workflowId }),
    }),
};

// ── 任务队列客户端 ───────────────────────────────────────────────────────────

export const taskQueueClient = {
  submit: (type: string, payload: Record<string, unknown>, priority = 5, timeout = 300) =>
    apiRequest<{ success: boolean; task_id?: string }>('/api/tasks/submit', {
      method: 'POST',
      body: JSON.stringify({ type, payload, priority, timeout }),
    }),

  status: (taskId: string) =>
    apiRequest<{ task_id: string; status: string; result?: unknown; error?: string }>('/api/tasks/status', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId }),
    }),

  list: (status?: string, type?: string, limit = 100) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (type) params.set('type', type);
    params.set('limit', String(limit));
    return apiRequest<{ tasks: unknown[]; count: number }>(`/api/tasks/list?${params}`);
  },

  cancel: (taskId: string) =>
    apiRequest<{ success: boolean }>('/api/tasks/cancel', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId }),
    }),

  stats: () =>
    apiRequest<{ total: number; by_status: Record<string, number>; by_type: Record<string, number> }>('/api/tasks/stats'),
};

// ── 服务健康检查 ─────────────────────────────────────────────────────────────

export const healthClient = {
  all: () =>
    apiRequest<{
      services: Record<string, { reachable: boolean; data?: unknown }>;
    }>('/api/services/health'),
};

// ── 默认导出 ─────────────────────────────────────────────────────────────────

export default {
  llm: llmClient,
  quantum: quantumClient,
  analysis: analysisClient,
  agent: agentClient,
  image: imageClient,
  workflow: workflowClient,
  tasks: taskQueueClient,
  health: healthClient,
};
