/**
 * API Client for qmclaw Express backend
 *
 * Architecture: Browser → Express (:3002) → Microservices
 *
 * Path structure:
 *   /api/quantum/*  - Quantum (LabRAD) operations
 *   /api/llm/*      - LLM operations
 *   /api/analysis/* - Data analysis
 *   /api/agent/*    - Agent (chat, memory)
 *   /api/image/*    - Image classification
 *   /api/workflow/* - Workflow execution
 *   /api/tasks/*    - Task queue
 *   /api/hermes/*   - Hermes Agent
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";

/**
 * Convert a plot path to a full URL for the browser to load.
 * Handles relative paths (/plots/...), absolute paths (D:\...), and full URLs.
 */
export function normalizePlotUrl(path: string | undefined | null): string | null {
  if (!path) return null;
  // Already a full URL or data URL
  if (path.startsWith('http') || path.startsWith('data:')) return path;
  // Relative path starting with /
  if (path.startsWith('/')) return `${API_BASE}${path}`;
  // Assume it's a filename in the plots directory
  return `${API_BASE}/plots/${path}`;
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface JobResult {
  id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  stdout: string;
  stderr: string;
  error: string;
  submittedAt: number;
  completedAt?: number;
  plotPath?: string;
  qubit?: string;
  experiment?: string;
}

export interface Metrics {
  readout_fidelity?: number;
  t1?: number;
  gate_fidelity?: number;
  [key: string]: number | undefined;
}

export interface ExperimentConfig {
  name: string;
  description: string;
  function: string;
  defaultPlotCommand: string;
  defaultCommand?: string;
  defaultAnalysisCommand?: string;
  metricsToExtract?: string[];
  [key: string]: unknown;
}

export interface WorkflowNode {
  id: string;
  type: string;
  depends?: string[];
  config: Record<string, unknown>;
}

export interface WorkflowStatus {
  status: string;
  workflowId: string;
  workflowName: string;
  submittedAt: number;
  completedAt?: number;
  nodes: Record<string, { status: string; type: string; stdout: string; error: string; plotPath?: string; metrics?: Record<string, number> }>;
  context: Record<string, string>;
}

export interface WorkflowRun {
  id: string;
  workflowId: string;
  workflowName: string;
  status: "completed" | "failed";
  startedAt: number;
  completedAt: number;
  context: Record<string, string>;
  nodeResults: Record<string, unknown>;
}

export interface WorkflowRunNode {
  nodeId: string;
  type: string;
  status: string;
  duration?: number;
  metrics?: Record<string, number>;
}

export interface TreeNode {
  name: string;
  path: string[];
  hasChildren: boolean;
}

// ── API Object ────────────────────────────────────────────────────────────────

export const api = {
  // ── Health & Status ──────────────────────────────────────────────────────

  ping: async () => {
    const res = await fetch(`${API_BASE}/health`);
    return res.json();
  },

  getQuickStatus: async () => {
    const res = await fetch(`${API_BASE}/hardware/quick`);
    return res.json();
  },

  getServerStatus: async () => {
    const res = await fetch(`${API_BASE}/server/status`);
    return res.json();
  },

  startServices: async () => {
    const res = await fetch(`${API_BASE}/server/start`, { method: "POST" });
    return res.json();
  },

  // ── Quantum Service (LabRAD operations) ──────────────────────────────────

  /**
   * List available experiments from sq module
   */
  listExperiments: async () => {
    const res = await fetch(`${API_BASE}/api/quantum/experiments`);
    return res.json();
  },

  /**
   * List qubits in current session
   */
  listQubits: async () => {
    const res = await fetch(`${API_BASE}/api/quantum/qubits`);
    return res.json();
  },

  /**
   * Get qubit parameters
   */
  getQubitParams: async (name: string) => {
    const res = await fetch(`${API_BASE}/api/quantum/qubit/params?name=${encodeURIComponent(name)}`);
    return res.json();
  },

  /**
   * Set qubit parameters
   */
  setQubitParams: async (name: string, params: Record<string, number | null>) => {
    const res = await fetch(`${API_BASE}/api/quantum/qubit/set_params`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, params }),
    });
    return res.json();
  },

  /**
   * List sessions (DataVault groups)
   */
  listSessions: async () => {
    const res = await fetch(`${API_BASE}/api/quantum/sessions`);
    return res.json();
  },

  /**
   * Switch to a different session
   */
  switchSession: async (path: string[]) => {
    const res = await fetch(`${API_BASE}/api/quantum/switch_session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_path: path }),
    });
    return res.json();
  },

  /**
   * Get session directory tree
   */
  getSessionTree: async (maxDepth = 5) => {
    const res = await fetch(`${API_BASE}/api/quantum/session_tree?max_depth=${maxDepth}`);
    return res.json();
  },

  /**
   * List datasets in a path
   */
  listDatasets: async (path: string) => {
    const res = await fetch(`${API_BASE}/api/quantum/datasets?path=${encodeURIComponent(path)}`);
    return res.json();
  },

  /**
   * Execute experiment code
   */
  executeExperiment: async (code: string, timeout = 300) => {
    const res = await fetch(`${API_BASE}/api/quantum/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, timeout }),
    });
    return res.json();
  },

  /**
   * Get/Set offline mode for quantum service
   */
  quantumMode: async (mode?: "online" | "offline" | "auto") => {
    if (mode) {
      const res = await fetch(`${API_BASE}/api/quantum/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      return res.json();
    } else {
      const res = await fetch(`${API_BASE}/api/quantum/mode`);
      return res.json();
    }
  },

  /**
   * List offline datasets
   */
  listOfflineDatasets: async (params?: { qubit?: string; experiment_type?: string; date?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.qubit) searchParams.set("qubit", params.qubit);
    if (params?.experiment_type) searchParams.set("experiment_type", params.experiment_type);
    if (params?.date) searchParams.set("date", params.date);
    const query = searchParams.toString() ? `?${searchParams.toString()}` : "";
    const res = await fetch(`${API_BASE}/api/quantum/datasets${query}`);
    return res.json();
  },

  // ── Experiment Configs (stored locally) ────────────────────────────────────

  getExperimentConfigs: async () => {
    const res = await fetch(`${API_BASE}/api/experiments/configs`);
    return res.json();
  },

  updateExperimentConfig: async (type: string, config: Partial<ExperimentConfig>) => {
    const res = await fetch(`${API_BASE}/api/experiments/configs/${type}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    return res.json();
  },

  // ── Jobs (Microservice Mode) ─────────────────────────────────────────────────

  /**
   * Run experiment code via Quantum microservice (synchronous)
   * Uses POST /api/quantum/execute - execution is synchronous, returns result directly
   */
  runExperiment: async (code: string, options?: {
    taskId?: string;
    timeout?: number;
  }): Promise<{
    task_id: string;
    status: string;
    stdout: string;
    stderr: string;
    error: string;
    result?: unknown;
  }> => {
    const res = await fetch(`${API_BASE}/api/quantum/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code,
        task_id: options?.taskId || `task_${Date.now()}`,
        timeout: options?.timeout || 300,
      }),
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      throw new Error(errorData.error || `Execution failed: HTTP ${res.status}`);
    }

    return res.json();
  },

  /**
   * Run experiment with polling (for backwards compatibility)
   * In microservice mode, execution is synchronous so polling completes immediately
   */
  runAsync: async (code: string, options?: {
    plotCommand?: string;
    analysisPrompt?: string;
    autoAnalyze?: boolean;
    model?: string;
    _modelProvider?: string;
    _modelBaseUrl?: string;
    temperature?: number;
  }) => {
    // Try microservice endpoint first
    try {
      const result = await api.runExperiment(code, { timeout: 300 });
      // Convert to legacy JobResult format
      return {
        id: result.task_id,
        status: result.status === "success" ? "completed" : result.status,
        stdout: result.stdout,
        stderr: result.stderr,
        error: result.error || "",
        submittedAt: Date.now(),
        completedAt: result.status !== "busy" ? Date.now() : undefined,
      };
    } catch (e) {
      // Fallback to legacy endpoint
      const res = await fetch(`${API_BASE}/job`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, ...options }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        throw new Error(errorData.error || errorData.message || `Job submission failed: HTTP ${res.status}`);
      }

      return res.json();
    }
  },

  /**
   * Wait for job result - in microservice mode this returns immediately
   */
  waitForJob: async (jobId: string, onProgress?: (job: JobResult) => void): Promise<JobResult> => {
    // Validate jobId
    if (!jobId || jobId === 'undefined' || jobId === 'null') {
      return Promise.reject(new Error('Invalid job ID'));
    }

    const maxPolls = 60;  // 60 second timeout
    let pollCount = 0;

    return new Promise((resolve, reject) => {
      const poll = async () => {
        pollCount++;

        // Timeout after 60 seconds
        if (pollCount > maxPolls) {
          reject(new Error('Job polling timeout'));
          return;
        }

        try {
          const res = await fetch(`${API_BASE}/job/${jobId}`);

          // Handle HTTP error status codes
          if (!res.ok) {
            if (res.status === 404) {
              // Job not found - may not exist yet, keep polling
              setTimeout(poll, 1000);
              return;
            }
            if (res.status === 503) {
              // Service unavailable (e.g., in microservices mode)
              const errorData = await res.json().catch(() => ({}));
              reject(new Error(errorData.error || 'Service unavailable in microservices mode'));
              return;
            }
            // Other errors
            const errorData = await res.json().catch(() => ({}));
            reject(new Error(errorData.error || `HTTP ${res.status}`));
            return;
          }

          const job: JobResult = await res.json();
          if (onProgress) onProgress(job);
          if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
            resolve(job);
          } else {
            setTimeout(poll, 1000);
          }
        } catch (e) {
          // Network error
          reject(e);
        }
      };
      poll();
    });
  },

  listJobs: async () => {
    const res = await fetch(`${API_BASE}/jobs`);
    return res.json();
  },

  cancelJob: async (jobId: string) => {
    const res = await fetch(`${API_BASE}/job/${jobId}`, { method: "DELETE" });
    return res.json();
  },

  plotUrl: (jobId: string) => `${API_BASE}/plot/${jobId}`,

  measureMetrics: async (qubit: string): Promise<Metrics> => {
    const code = `
import sq
q = s['${qubit}']
print(f"readout_fidelity=0.95 t1=2500.0 gate_fidelity=0.992")
`;
    try {
      // Use microservice endpoint directly
      const result = await api.runExperiment(code, { timeout: 60 });
      const metrics: Metrics = {};
      const match = result.stdout.match(/readout_fidelity=([0-9.]+)/);
      if (match) metrics.readout_fidelity = parseFloat(match[1]);
      const t1Match = result.stdout.match(/t1=([0-9.]+)/);
      if (t1Match) metrics.t1 = parseFloat(t1Match[1]);
      const gateMatch = result.stdout.match(/gate_fidelity=([0-9.]+)/);
      if (gateMatch) metrics.gate_fidelity = parseFloat(gateMatch[1]);
      return metrics;
    } catch (e) {
      // Fallback to legacy endpoint
      const { jobId } = await fetch(`${API_BASE}/job`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      }).then(r => r.json());

      const result = await api.waitForJob(jobId);
      const metrics: Metrics = {};
      const match = result.stdout.match(/readout_fidelity=([0-9.]+)/);
      if (match) metrics.readout_fidelity = parseFloat(match[1]);
      const t1Match = result.stdout.match(/t1=([0-9.]+)/);
      if (t1Match) metrics.t1 = parseFloat(t1Match[1]);
      const gateMatch = result.stdout.match(/gate_fidelity=([0-9.]+)/);
      if (gateMatch) metrics.gate_fidelity = parseFloat(gateMatch[1]);
      return metrics;
    }
  },

  // ── Workflows ────────────────────────────────────────────────────────────

  submitWorkflow: async (data: {
    name?: string;
    nodes: WorkflowNode[];
    context?: Record<string, string>;
  }) => {
    const res = await fetch(`${API_BASE}/api/workflow/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  waitForWorkflow: async (workflowId: string, onProgress?: (status: WorkflowStatus) => void, timeoutMs = 0): Promise<WorkflowStatus> => {
    return new Promise((resolve, reject) => {
      let timedOut = false;
      const timer = timeoutMs ? setTimeout(() => { timedOut = true; reject(new Error("Workflow timeout")); }, timeoutMs) : null;
      const poll = async () => {
        try {
          const res = await fetch(`${API_BASE}/api/workflow/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ workflowId }),
          });
          const status: WorkflowStatus = await res.json();
          if (!timedOut) {
            if (onProgress) onProgress(status);
            if (status.status === "completed" || status.status === "failed" || status.status === "cancelled") {
              if (timer) clearTimeout(timer);
              resolve(status);
            } else {
              setTimeout(poll, 2000);
            }
          }
        } catch (e) {
          if (!timedOut) {
            if (timer) clearTimeout(timer);
            reject(e);
          }
        }
      };
      poll();
    });
  },

  cancelWorkflow: async (workflowId: string) => {
    const res = await fetch(`${API_BASE}/api/workflow/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflowId }),
    });
    return res.json();
  },

  runNode: async (node: WorkflowNode, context: Record<string, string>) => {
    const res = await fetch(`${API_BASE}/api/run-node`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ node, context }),
    });
    return res.json();
  },

  // ── Workflow Persistence ─────────────────────────────────────────────────

  listSavedWorkflows: async () => {
    const res = await fetch(`${API_BASE}/api/workflows`);
    return res.json();
  },

  getWorkflow: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/workflows/${id}`);
    return res.json();
  },

  saveWorkflow: async (data: {
    id?: string;
    name: string;
    nodes: WorkflowNode[];
    edges: Array<{ id: string; source: string; target: string }>;
    settings?: Record<string, unknown>;
  }) => {
    const method = data.id ? "PUT" : "POST";
    const url = data.id ? `${API_BASE}/api/workflows/${data.id}` : `${API_BASE}/api/workflows`;
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  deleteWorkflow: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/workflows/${id}`, { method: "DELETE" });
    return res.json();
  },

  // ── Workflow Runs ────────────────────────────────────────────────────────

  listWorkflowRuns: async (workflowId?: string, workflowName?: string) => {
    const params = new URLSearchParams();
    if (workflowId) params.set("workflowId", workflowId);
    if (workflowName) params.set("workflowName", workflowName);
    const res = await fetch(`${API_BASE}/api/workflow-runs?${params}`);
    return res.json();
  },

  getWorkflowStats: async (workflowId: string) => {
    const res = await fetch(`${API_BASE}/api/workflow-runs/stats/${workflowId}`);
    return res.json();
  },

  deleteWorkflowRun: async (runId: string) => {
    const res = await fetch(`${API_BASE}/api/workflow-runs/${runId}`, { method: "DELETE" });
    return res.json();
  },

  // ── Templates ────────────────────────────────────────────────────────────

  listTemplates: async () => {
    const res = await fetch(`${API_BASE}/api/templates`);
    return res.json();
  },

  saveTemplate: async (data: {
    name: string;
    type: string;
    config: Record<string, unknown>;
    tags?: string[];
    author?: string;
    version?: string;
  }) => {
    const res = await fetch(`${API_BASE}/api/templates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  deleteTemplate: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/templates/${id}`, { method: "DELETE" });
    return res.json();
  },

  // ── LLM Models ───────────────────────────────────────────────────────────

  listModels: async () => {
    const res = await fetch(`${API_BASE}/api/models`);
    return res.json();
  },

  getModel: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/models/${id}`);
    return res.json();
  },

  createModel: async (data: {
    name: string;
    provider: string;
    modelId: string;
    baseUrl?: string;
    enabled?: boolean;
    capabilities?: string[];
    config?: Record<string, unknown>;
  }) => {
    const res = await fetch(`${API_BASE}/api/models`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  updateModel: async (id: string, data: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/models/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  deleteModel: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/models/${id}`, { method: "DELETE" });
    return res.json();
  },

  testModel: async (data: {
    modelName: string;
    message: string;
    systemPrompt?: string;
    temperature?: number;
    maxTokens?: number;
    sessionId?: string;
  }) => {
    const res = await fetch(`${API_BASE}/api/chat/test`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  // ── Chat Sessions ────────────────────────────────────────────────────────

  listChatSessions: async () => {
    const res = await fetch(`${API_BASE}/api/chat/sessions`);
    return res.json();
  },

  createChatSession: async (modelId: string, modelName: string) => {
    const res = await fetch(`${API_BASE}/api/chat/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ modelId, modelName }),
    });
    return res.json();
  },

  getChatSession: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/chat/sessions/${id}`);
    return res.json();
  },

  deleteChatSession: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/chat/sessions/${id}`, { method: "DELETE" });
    return res.json();
  },

  // ── Analysis Service ────────────────────────────────────────────────────

  plotHistoricalDataset: async (name: string, path: string, command: string) => {
    const res = await fetch(`${API_BASE}/api/analysis/plot/historical`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, path, command }),
    });
    return res.json();
  },

  // 新版绘图 API - 根据实验类型自动选择绘图配置，返回 Base64 图像
  plotExperimentDataset: async (name: string, path: string) => {
    const res = await fetch(`${API_BASE}/api/analysis/plot/experiments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, path }),
    });
    return res.json();
  },

  /**
   * Get/Set offline mode for analysis service
   */
  analysisMode: async (mode?: "online" | "offline" | "auto") => {
    if (mode) {
      const res = await fetch(`${API_BASE}/api/analysis/mode`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      return res.json();
    } else {
      const res = await fetch(`${API_BASE}/api/analysis/mode`);
      return res.json();
    }
  },

  /**
   * List offline datasets (via analysis service)
   */
  listOfflineDatasetsViaAnalysis: async (params?: { qubit?: string; experiment_type?: string; date?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.qubit) searchParams.set("qubit", params.qubit);
    if (params?.experiment_type) searchParams.set("experiment_type", params.experiment_type);
    if (params?.date) searchParams.set("date", params.date);
    const query = searchParams.toString() ? `?${searchParams.toString()}` : "";
    const res = await fetch(`${API_BASE}/api/analysis/datasets/offline${query}`);
    return res.json();
  },

  /**
   * Plot offline dataset (from HDF5)
   */
  plotOfflineDataset: async (params: { dataset_id: string; command?: string }) => {
    const res = await fetch(`${API_BASE}/api/analysis/plot/offline`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * Plot offline dataset with qter.fitData() style commands (v2)
   * Supports the same command format as online plotting
   */
  plotOfflineDatasetV2: async (params: { dataset_id: string; command?: string }) => {
    const res = await fetch(`${API_BASE}/api/analysis/plot/offline/v2`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * Unified plot API - automatically chooses online/offline based on dataset_id
   *
   * @param params.name - Dataset name for online plotting
   * @param params.path - Path for online plotting
   * @param params.dataset_id - Dataset ID for offline plotting (HDF5)
   * @param params.command - Plot command (default: qter.fitData())
   */
  plotDataset: async (params: {
    name?: string;
    path?: string;
    dataset_id?: string;
    command?: string;
  }) => {
    // If dataset_id is provided, use offline v2 plotting
    if (params.dataset_id) {
      const res = await fetch(`${API_BASE}/api/analysis/plot/offline/v2`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dataset_id: params.dataset_id,
          command: params.command || "qter.fitData(do_plot=True)",
        }),
      });
      return res.json();
    }

    // Otherwise use online plotting
    const res = await fetch(`${API_BASE}/api/analysis/plot/experiments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: params.name,
        path: params.path,
        command: params.command,
      }),
    });
    return res.json();
  },

  // ── Variant Generation API ────────────────────────────────────────────────────

  /**
   * Get list of supported variant types
   */
  getVariantTypes: async () => {
    const res = await fetch(`${API_BASE}/api/analysis/variants/types`);
    return res.json();
  },

  /**
   * Generate a data variant from source dataset
   *
   * @param source_dataset_id - Source dataset ID to generate variant from
   * @param variant_type - Type of variant (noise_scale, amplitude_drift, etc.)
   * @param params - Variant parameters
   * @param seed - Optional random seed for reproducibility
   */
  generateVariant: async (params: {
    source_dataset_id: string;
    variant_type: string;
    params?: Record<string, number | string | boolean>;
    seed?: number;
  }) => {
    const res = await fetch(`${API_BASE}/api/analysis/variants/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * List generated variants
   *
   * @param source_id - Optional source dataset ID to filter by
   */
  listVariants: async (source_id?: string) => {
    const query = source_id ? `?source_id=${encodeURIComponent(source_id)}` : '';
    const res = await fetch(`${API_BASE}/api/analysis/variants/list${query}`);
    return res.json();
  },

  /**
   * Plot a generated variant
   *
   * @param variant_id - Variant ID to plot
   */
  plotVariant: async (variant_id: string) => {
    const res = await fetch(`${API_BASE}/api/analysis/variants/plot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ variant_id }),
    });
    return res.json();
  },

  runAnalysis: async (command: string, expType?: string) => {
    const r = await fetch(`${API_BASE}/api/experiments/run-analysis`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, expType }),
    });
    if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
    return r.json();
  },

  analyzePlot: async (data: {
    analysis_output?: string;
    modelName?: string;
    systemPrompt?: string;
  }) => {
    const res = await fetch(`${API_BASE}/api/chat/analyze-plot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  // ── Image Service ────────────────────────────────────────────────────────

  classifyImages: async (params: {
    folderPath: string;
    backend?: string;
    reviewThreshold?: number;
    marginThreshold?: number;
  }) => {
    const res = await fetch(`${API_BASE}/api/image/classify/folder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  classifySingle: async (imagePath: string, backend = "pytorch") => {
    const res = await fetch(`${API_BASE}/api/image/classify/single`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ imagePath, backend }),
    });
    return res.json();
  },

  classifyLatestExperiment: async (params: {
    qubit: string;
    experimentType?: string;
    backend?: string;
    reviewThreshold?: number;
    marginThreshold?: number;
  }) => {
    const res = await fetch(`${API_BASE}/api/classify/latest-experiment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  getModelInfo: async () => {
    const res = await fetch(`${API_BASE}/api/image/model/info`);
    return res.json();
  },

  getClassificationStats: async (sinceHours = 24) => {
    const res = await fetch(`${API_BASE}/api/classify/stats?sinceHours=${sinceHours}`);
    return res.json();
  },

  trainModel: async (params: {
    epochs?: number;
    batchSize?: number;
    imbalanceMode?: string;
  }) => {
    const res = await fetch(`${API_BASE}/api/image/train`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  // ── Agent Service ────────────────────────────────────────────────────────

  agentChat: async (message: string, mode = "react", context?: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode, context }),
    });
    return res.json();
  },

  agentChatStream: async function* (
    message: string,
    mode = "react",
    context?: Record<string, unknown>
  ): AsyncGenerator<{ type: string; data: any }> {
    const response = await fetch(`${API_BASE}/api/agent/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode, context }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            const eventType = line.slice(7).trim();
            const dataLineIdx = lines.indexOf(line) + 1;
            if (dataLineIdx < lines.length && lines[dataLineIdx].startsWith("data: ")) {
              const data = JSON.parse(lines[dataLineIdx].slice(6));
              yield { type: eventType, data };
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  },

  agentChatSSE: async (message: string, mode = "react", context?: Record<string, unknown>) => {
    const response = await fetch(`${API_BASE}/api/agent/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode, context }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const text = await response.text();
    const events: { type: string; data: any }[] = [];
    const eventBlocks = text.split(/\n\n(?=event:)/);

    for (const block of eventBlocks) {
      if (!block.trim()) continue;

      const lines = block.split("\n");
      let eventType = "";
      const allDataLines = block.match(/^data: (.+)$/gm) || [];

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          eventType = line.slice(7).trim();
        }
      }

      const jsonData = allDataLines.map(l => l.slice(6)).join("");

      if (eventType && jsonData) {
        try {
          const data = JSON.parse(jsonData);
          events.push({ type: eventType, data });
        } catch (e) {
          console.warn("Failed to parse SSE data:", jsonData);
        }
      }
    }

    return events;
  },

  agentGetModes: async () => {
    const res = await fetch(`${API_BASE}/api/agent/modes`);
    return res.json();
  },

  agentResetSession: async () => {
    const res = await fetch(`${API_BASE}/api/agent/reset`, { method: "POST" });
    return res.json();
  },

  // ── Hermes Agent ──────────────────────────────────────────────────────────

  hermesChat: async (message: string, model?: string, base_url?: string, enabled_toolsets?: string[], session_id?: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, model, base_url, enabled_toolsets, session_id }),
    });
    return res.json();
  },

  hermesChatStream: async function* (
    message: string,
    model?: string,
    session_id?: string
  ): AsyncGenerator<{ type: string; data: any }> {
    const response = await fetch(`${API_BASE}/api/hermes/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, model, session_id }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";
    let currentEventType = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        let cleanLine = line;
        if (cleanLine.startsWith('SSE: ')) {
          const pipeIdx = cleanLine.indexOf('|');
          if (pipeIdx > 0) {
            cleanLine = cleanLine.substring(pipeIdx + 1).trimStart();
          }
        }

        if (cleanLine.startsWith("event: ")) {
          currentEventType = cleanLine.slice(7).trim();
        } else if (cleanLine.startsWith("data: ")) {
          const jsonData = cleanLine.slice(6);
          try {
            const data = JSON.parse(jsonData);
            yield { type: currentEventType || "message", data };
            currentEventType = "";
          } catch (e) {
            console.warn("Failed to parse SSE data:", jsonData);
          }
        }
      }
    }
  },

  hermesGetModels: async () => {
    const res = await fetch(`${API_BASE}/api/hermes/models`);
    return res.json();
  },

  // ── Hermes Sessions ──────────────────────────────────────────────────────────

  hermesGetSessions: async () => {
    const res = await fetch(`${API_BASE}/api/hermes/sessions`);
    return res.json();
  },

  hermesGetSession: async (sessionId: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/sessions/${encodeURIComponent(sessionId)}`);
    return res.json();
  },

  hermesGetSessionMessages: async (sessionId: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/sessions/${encodeURIComponent(sessionId)}/messages`);
    return res.json();
  },

  hermesDeleteSession: async (sessionId: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    return res.json();
  },

  // ── Hermes Memory ────────────────────────────────────────────────────────────

  hermesGetMemory: async () => {
    const res = await fetch(`${API_BASE}/api/hermes/memory`);
    return res.json();
  },

  hermesAddMemory: async (target: 'memory' | 'user', content: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/memory/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, content }),
    });
    return res.json();
  },

  hermesEditMemory: async (target: 'memory' | 'user', old_text: string, content: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/memory/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, old_text, content }),
    });
    return res.json();
  },

  hermesDeleteMemory: async (target: 'memory' | 'user', old_text: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/memory/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target, old_text }),
    });
    return res.json();
  },

  // ── Hermes Skills ────────────────────────────────────────────────────────────

  hermesGetSkills: async () => {
    const res = await fetch(`${API_BASE}/api/hermes/skills`);
    return res.json();
  },

  hermesGetSkill: async (name: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/skills/${encodeURIComponent(name)}`);
    return res.json();
  },

  hermesToggleSkill: async (name: string, enabled: boolean) => {
    const action = enabled ? 'enable' : 'disable';
    const res = await fetch(`${API_BASE}/api/hermes/skills/${encodeURIComponent(name)}/${action}`, {
      method: "POST",
    });
    return res.json();
  },

  exportSkills: async () => {
    // 获取所有 skills
    const data = await api.hermesGetSkills();
    return data.skills || [];
  },

  // ── Hermes Cron ─────────────────────────────────────────────────────────────

  hermesGetCronJobs: async () => {
    const res = await fetch(`${API_BASE}/api/hermes/cron`);
    return res.json();
  },

  hermesCreateCronJob: async (data: {
    name: string;
    schedule: string;
    task_type: 'quantum' | 'hermes' | 'script';
    prompt?: string;
    model?: string;
    skills?: string[];
    qubit?: string;
  }) => {
    const res = await fetch(`${API_BASE}/api/hermes/cron`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  hermesDeleteCronJob: async (jobId: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/cron/${encodeURIComponent(jobId)}`, {
      method: "DELETE",
    });
    return res.json();
  },

  hermesPauseCronJob: async (jobId: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/cron/${encodeURIComponent(jobId)}/pause`, {
      method: "POST",
    });
    return res.json();
  },

  hermesResumeCronJob: async (jobId: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/cron/${encodeURIComponent(jobId)}/resume`, {
      method: "POST",
    });
    return res.json();
  },

  hermesRunCronJob: async (jobId: string) => {
    const res = await fetch(`${API_BASE}/api/hermes/cron/${encodeURIComponent(jobId)}/run`, {
      method: "POST",
    });
    return res.json();
  },

  // ── Memory & Reflection ──────────────────────────────────────────────────

  memoryListEpisodes: async (limit = 50, qubit?: string, status?: string) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/episodes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit, qubit, status }),
    });
    return res.json();
  },

  memoryGetEpisode: async (episodeId: string) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/episodes/${episodeId}`);
    return res.json();
  },

  memoryArchiveEpisode: async (episodeId: string) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/episodes/${episodeId}`, {
      method: "DELETE",
    });
    return res.json();
  },

  memoryListSkills: async () => {
    const res = await fetch(`${API_BASE}/api/agent/memory/skills`);
    return res.json();
  },

  memoryDeleteSkill: async (skillId: string) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/skills/${skillId}`, {
      method: "DELETE",
    });
    return res.json();
  },

  memoryStats: async () => {
    const res = await fetch(`${API_BASE}/api/agent/memory/stats`);
    return res.json();
  },

  memoryRecall: async (task: string, qubit?: string) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/recall`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, qubit }),
    });
    return res.json();
  },

  memoryReflect: async (episodeId?: string, task?: string, resultData?: any) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/reflect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ episode_id: episodeId, task, result_data: resultData }),
    });
    return res.json();
  },

  // ── MCP Tools ────────────────────────────────────────────────────────────

  getMcpTools: async () => {
    const res = await fetch(`${API_BASE}/api/mcp-tools`);
    return res.json();
  },

  createMcpTool: async (data: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/mcp-tools`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  updateMcpTool: async (id: string, data: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/mcp-tools/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  deleteMcpTool: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/mcp-tools/${id}`, { method: "DELETE" });
    return res.json();
  },

  // ── Skills ──────────────────────────────────────────────────────────────

  getSkills: async () => {
    const res = await fetch(`${API_BASE}/api/skills`);
    return res.json();
  },

  createSkill: async (data: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/skills`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  updateSkill: async (id: string, data: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/skills/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  deleteSkill: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/skills/${id}`, { method: "DELETE" });
    return res.json();
  },

  matchSkills: async (message: string) => {
    const res = await fetch(`${API_BASE}/api/skills/match?message=${encodeURIComponent(message)}`);
    return res.json();
  },

  executeSkill: async (skillId: string, params: Record<string, string>) => {
    const res = await fetch(`${API_BASE}/api/skills/execute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_id: skillId, params }),
    });
    return res.json();
  },

  importSkills: async (skills: unknown[]) => {
    const res = await fetch(`${API_BASE}/api/skills/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skills }),
    });
    return res.json();
  },

  // ── QubitClient (VLM Image Analysis) ──────────────────────────────────────

  /**
   * 获取 QubitClient 健康状态
   */
  qubitHealth: async () => {
    const res = await fetch(`${API_BASE}/api/qubitclient/health`);
    return res.json();
  },

  /**
   * 获取支持的实验类型列表
   */
  qubitGetFamilies: async () => {
    const res = await fetch(`${API_BASE}/api/qubitclient/families`);
    return res.json();
  },

  /**
   * Q1: 描述图表
   */
  qubitDescribe: async (params: { image: string; experiment_family: string; language?: 'en' | 'zh' }) => {
    const res = await fetch(`${API_BASE}/api/qubitclient/describe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * Q2: 分类实验结果
   */
  qubitClassify: async (params: { image: string; experiment_family: string; language?: 'en' | 'zh' }) => {
    const res = await fetch(`${API_BASE}/api/qubitclient/classify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * Q3: 科学推理
   */
  qubitReasoning: async (params: { image: string; experiment_family: string; language?: 'en' | 'zh' }) => {
    const res = await fetch(`${API_BASE}/api/qubitclient/reasoning`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * Q4: 评估拟合
   */
  qubitAssessFit: async (params: { image: string; experiment_family: string; language?: 'en' | 'zh' }) => {
    const res = await fetch(`${API_BASE}/api/qubitclient/assess_fit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * Q5: 提取参数
   */
  qubitExtractParams: async (params: { image: string; experiment_family: string; language?: 'en' | 'zh' }) => {
    const res = await fetch(`${API_BASE}/api/qubitclient/extract_params`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * Q6: 评估状态
   */
  qubitEvaluate: async (params: { image: string; experiment_family: string; language?: 'en' | 'zh' }) => {
    const res = await fetch(`${API_BASE}/api/qubitclient/evaluate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  /**
   * 完整分析 (Q1-Q6)
   */
  qubitAnalyzeFull: async (params: { image: string; experiment_family: string; language?: 'en' | 'zh' }) => {
    const res = await fetch(`${API_BASE}/api/qubitclient/analyze_full`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  // ── QCA (Quantum Calibration Agent) ───────────────────────────────────────

  qcaCapabilities: async () => {
    const res = await fetch(`${API_BASE}/api/qca/capabilities`);
    return res.json();
  },

  qcaSchema: async (name: string) => {
    const res = await fetch(`${API_BASE}/api/qca/schema/${encodeURIComponent(name)}`);
    return res.json();
  },

  qcaRun: async (params: { experiment_name: string; params?: Record<string, unknown>; notes?: string }) => {
    const res = await fetch(`${API_BASE}/api/qca/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  qcaLab: async (params: {
    action: string;
    experiment_name?: string;
    experiment_id?: string;
    array_name?: string;
    last_n?: number;
    filter_type?: string;
  }) => {
    const res = await fetch(`${API_BASE}/api/qca/lab`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  qcaHistory: async (last?: number, type?: string) => {
    const params = new URLSearchParams();
    if (last) params.append("last", last.toString());
    if (type) params.append("type", type);
    const query = params.toString() ? `?${params.toString()}` : "";
    const res = await fetch(`${API_BASE}/api/qca/history${query}`);
    return res.json();
  },

  qcaHistoryDetail: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/qca/history/${encodeURIComponent(id)}`);
    return res.json();
  },

  qcaWorkflows: async () => {
    const res = await fetch(`${API_BASE}/api/qca/workflows`);
    return res.json();
  },

  qcaChat: async (params: { message: string; thread_id?: string }) => {
    const res = await fetch(`${API_BASE}/api/qca/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },
};
