/**
 * API Client for qmclaw Express backend
 *
 * All API calls go to the Express server (:3002) which proxies to Python subprocess
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";

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

  // ── Experiments ──────────────────────────────────────────────────────────

  listExperiments: async () => {
    const res = await fetch(`${API_BASE}/experiments`);
    return res.json();
  },

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

  // ── Jobs ─────────────────────────────────────────────────────────────────

  runAsync: async (code: string, options?: {
    plotCommand?: string;
    analysisPrompt?: string;
    autoAnalyze?: boolean;
    model?: string;
    _modelProvider?: string;
    _modelBaseUrl?: string;
    temperature?: number;
  }) => {
    const res = await fetch(`${API_BASE}/job`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, ...options }),
    });
    return res.json();
  },

  waitForJob: async (jobId: string, onProgress?: (job: JobResult) => void): Promise<JobResult> => {
    return new Promise((resolve, reject) => {
      const poll = async () => {
        try {
          const res = await fetch(`${API_BASE}/job/${jobId}`);
          const job: JobResult = await res.json();
          if (onProgress) onProgress(job);
          if (job.status === "completed" || job.status === "failed" || job.status === "cancelled") {
            resolve(job);
          } else {
            setTimeout(poll, 1000);
          }
        } catch (e) {
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
    // Run a quick measurement sequence to get all metrics
    const code = `
import sq
q = s['${qubit}']
# Quick T1
try:
    sq.t1(q, do_plot=False)
    import re
    m = re.search(r'T1[:=s]+([0-9.]+)', sys.stdout.getvalue() if 'sys.stdout' in dir() else '')
except:
    pass
# Return mock metrics for now
print(f"readout_fidelity=0.95 t1=2500.0 gate_fidelity=0.992")
`;
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
  },

  // ── Sessions & DataVault ─────────────────────────────────────────────────

  listSessions: async () => {
    const res = await fetch(`${API_BASE}/sessions`);
    return res.json();
  },

  switchSession: async (path: string[]) => {
    const res = await fetch(`${API_BASE}/sessions/switch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    return res.json();
  },

  listDatasets: async (path: string) => {
    const res = await fetch(`${API_BASE}/datasets?path=${encodeURIComponent(path)}`);
    return res.json();
  },

  datasetPlotUrl: (name: string, path: string) =>
    `${API_BASE}/datasets/plot?name=${encodeURIComponent(name)}&path=${encodeURIComponent(path)}`,

  getSessionTree: async () => {
    const res = await fetch(`${API_BASE}/sessions/tree`);
    return res.json();
  },

  // ── Qubits ───────────────────────────────────────────────────────────────

  listQubits: async () => {
    const res = await fetch(`${API_BASE}/qubits`);
    return res.json();
  },

  getQubitParams: async (name: string) => {
    const res = await fetch(`${API_BASE}/qubits/${name}/params`);
    return res.json();
  },

  setQubitParams: async (name: string, params: Record<string, number | null>) => {
    const res = await fetch(`${API_BASE}/qubits/${name}/params`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  // ── Workflows ────────────────────────────────────────────────────────────

  submitWorkflow: async (data: {
    name?: string;
    nodes: WorkflowNode[];
    context?: Record<string, string>;
  }) => {
    const res = await fetch(`${API_BASE}/workflow`, {
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
          const res = await fetch(`${API_BASE}/workflow/${workflowId}`);
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
    const res = await fetch(`${API_BASE}/workflow/${workflowId}`, { method: "DELETE" });
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

  // ── Models (LLM Registry) ────────────────────────────────────────────────

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

  // ── Plot Analysis ────────────────────────────────────────────────────────

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

  // ── Analysis Command Execution ────────────────────────────────────────────────

  runAnalysis: async (command: string, expType?: string): Promise<{
    success: boolean;
    stdout?: string;
    stderr?: string;
    metrics?: Record<string, number>;
    error?: string;
  }> => {
    const r = await fetch(`${API_BASE}/api/experiments/run-analysis`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, expType }),
    });
    if (!r.ok) throw new Error(`API ${r.status}: ${await r.text()}`);
    return r.json();
  },

  // ── Image Classification ────────────────────────────────────────────────────

  classifyImages: async (params: {
    folderPath: string;
    backend?: string;
    reviewThreshold?: number;
    marginThreshold?: number;
  }) => {
    const res = await fetch(`${API_BASE}/api/classify/images`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  classifySingle: async (imagePath: string, backend = "pytorch") => {
    const res = await fetch(`${API_BASE}/api/classify/single`, {
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
    const res = await fetch(`${API_BASE}/api/classify/model-info`);
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
    const res = await fetch(`${API_BASE}/api/classify/train`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    return res.json();
  },

  // ── Quantum Agent ────────────────────────────────────────────────────────

  agentChat: async (message: string, mode = "react", context?: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode, context }),
    });
    return res.json();
  },

  /**
   * Streaming agent chat via SSE.
   * Returns an EventSource-compatible response.
   * Use with fetch + ReadableStream for client-side SSE parsing.
   */
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
            // Wait for data line
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

  // Simpler SSE wrapper that parses the complete SSE response
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

    // Parse SSE format properly: event: TYPE\ndata: JSON\n\n
    // Events are separated by \n\n, and JSON data can span multiple lines
    const eventBlocks = text.split(/\n\n(?=event:)/);

    for (const block of eventBlocks) {
      if (!block.trim()) continue;

      const lines = block.split("\n");
      let eventType = "";
      let jsonData = "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          jsonData = line.slice(6).trim();
        }
      }

      // Handle multi-line JSON by collecting all data: lines
      const allDataLines = block.match(/^data: (.+)$/gm) || [];
      jsonData = allDataLines.map(l => l.slice(6)).join("");

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
    const res = await fetch(`${API_BASE}/api/agent/memory/episodes/${episodeId}`, {
      method: "GET",
    });
    return res.json();
  },

  memoryArchiveEpisode: async (episodeId: string) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/episodes/${episodeId}`, {
      method: "DELETE",
    });
    return res.json();
  },

  memoryListSkills: async () => {
    const res = await fetch(`${API_BASE}/api/agent/memory/skills`, { method: "GET" });
    return res.json();
  },

  memoryDeleteSkill: async (skillId: string) => {
    const res = await fetch(`${API_BASE}/api/agent/memory/skills/${skillId}`, {
      method: "DELETE",
    });
    return res.json();
  },

  memoryStats: async () => {
    const res = await fetch(`${API_BASE}/api/agent/memory/stats`, { method: "GET" });
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

  exportSkills: async () => {
    const res = await fetch(`${API_BASE}/api/skills/export`);
    return res.json();
  },
};

// ── Re-export types ────────────────────────────────────────────────────────────
// (types are exported as interfaces above)