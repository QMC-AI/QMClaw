/**
 * Workflow Run Service - 工作流运行历史持久化服务
 *
 * 管理工作流执行历史的保存、加载、列表等操作
 */

import * as fs from 'fs';
import * as path from 'path';

const DATA_DIR = path.join(process.cwd(), 'data', 'workflow-runs');

// Ensure data directory exists
function ensureDataDir(): void {
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
}

// ── Types ──────────────────────────────────────────────────────────────────────

export interface WorkflowRunNodeInput {
  config: Record<string, unknown>;         // 节点原始配置
  resolvedContext: Record<string, unknown>; // 解析后的变量值
  upstreamResults?: Record<string, {       // 前置节点结果引用
    status: string;
    metrics?: Record<string, number>;
    stdout?: string;
  }>;
}

export interface WorkflowRunNodeOutput {
  stdout: string;
  stderr: string;
  error?: string;
  metrics?: Record<string, number>;
  plotPath?: string;
  // LLM 节点特有字段
  conversation?: {
    messagesSent: Array<{ role: string; content: string }>;
    response?: string;
  };
  recommendations?: unknown[];
  symptom?: string;
  reasoning?: string;
  matchedRules?: string[];
}

export interface WorkflowRunNode {
  nodeId: string;
  nodeType: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  startedAt?: string;
  completedAt?: string;
  duration?: number;           // milliseconds
  input: WorkflowRunNodeInput;
  output: WorkflowRunNodeOutput;
}

export interface WorkflowRun {
  id: string;                  // run_wf123_1699876543210
  workflowId: string;           // 关联的工作流 ID
  workflowName: string;
  status: 'completed' | 'failed' | 'cancelled';
  startedAt: string;            // ISO timestamp
  completedAt: string;
  totalDuration: number;        // milliseconds
  context: Record<string, string>;  // 工作流上下文变量
  nodes: WorkflowRunNode[];
}

// ── CRUD Operations ─────────────────────────────────────────────────────────

/**
 * List all workflow runs, optionally filtered by workflowId or workflowName
 */
export function listWorkflowRuns(workflowId?: string, workflowName?: string): WorkflowRun[] {
  ensureDataDir();

  const files = fs.readdirSync(DATA_DIR).filter((f: string) => f.endsWith('.json'));
  const runs: WorkflowRun[] = [];

  for (const file of files) {
    try {
      const filePath = path.join(DATA_DIR, file);
      const content = fs.readFileSync(filePath, 'utf-8');
      const run = JSON.parse(content) as WorkflowRun;

      // Filter by workflowId OR workflowName if specified
      if (workflowId && run.workflowId !== workflowId) {
        // If workflowId doesn't match exactly, try to match by workflowName
        if (workflowName && run.workflowName !== workflowName) {
          continue;
        }
      }

      runs.push(run);
    } catch (e) {
      console.error(`[workflow-run-service] Failed to read ${file}:`, e);
    }
  }

  // Sort by completedAt descending (newest first)
  runs.sort((a, b) =>
    new Date(b.completedAt).getTime() - new Date(a.completedAt).getTime()
  );

  return runs;
}

/**
 * Get a workflow run by ID
 */
export function getWorkflowRun(id: string): WorkflowRun | null {
  ensureDataDir();

  const filePath = path.join(DATA_DIR, `${id}.json`);
  if (!fs.existsSync(filePath)) {
    return null;
  }

  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    return JSON.parse(content) as WorkflowRun;
  } catch (e) {
    console.error(`[workflow-run-service] Failed to read run ${id}:`, e);
    return null;
  }
}

/**
 * Save a workflow run (create new or update existing)
 */
export function saveWorkflowRun(run: WorkflowRun): WorkflowRun {
  ensureDataDir();

  const filePath = path.join(DATA_DIR, `${run.id}.json`);
  fs.writeFileSync(filePath, JSON.stringify(run, null, 2), 'utf-8');

  console.log(`[workflow-run-service] Saved run: ${run.id}`);

  return run;
}

/**
 * Create a new workflow run from a workflow result
 */
export function createWorkflowRun(params: {
  workflowId: string;
  workflowName: string;
  status: 'completed' | 'failed' | 'cancelled';
  startedAt: number;
  completedAt: number;
  context: Record<string, string>;
  nodeResults: Record<string, {
    status: string;
    type: string;
    stdout: string;
    stderr: string;
    error?: string;
    plotPath?: string;
    metrics?: Record<string, number>;
    duration?: number;
    input?: WorkflowRunNodeInput;
    conversation?: WorkflowRunNodeOutput['conversation'];
    recommendations?: unknown[];
    symptom?: string;
    reasoning?: string;
    matchedRules?: string[];
  }>;
}): WorkflowRun {
  const id = `run_${params.workflowId}_${params.startedAt}`;

  const nodes: WorkflowRunNode[] = Object.entries(params.nodeResults).map(([nodeId, result]) => ({
    nodeId,
    nodeType: result.type,
    status: result.status as WorkflowRunNode['status'],
    startedAt: new Date(params.startedAt).toISOString(),
    completedAt: new Date(params.completedAt).toISOString(),
    duration: result.duration,
    input: result.input || {
      config: {},
      resolvedContext: {},
    },
    output: {
      stdout: result.stdout || '',
      stderr: result.stderr || '',
      error: result.error,
      metrics: result.metrics,
      plotPath: result.plotPath,
      conversation: result.conversation,
      recommendations: result.recommendations,
      symptom: result.symptom,
      reasoning: result.reasoning,
      matchedRules: result.matchedRules,
    },
  }));

  const run: WorkflowRun = {
    id,
    workflowId: params.workflowId,
    workflowName: params.workflowName,
    status: params.status,
    startedAt: new Date(params.startedAt).toISOString(),
    completedAt: new Date(params.completedAt).toISOString(),
    totalDuration: params.completedAt - params.startedAt,
    context: params.context,
    nodes,
  };

  return saveWorkflowRun(run);
}

/**
 * Delete a workflow run
 */
export function deleteWorkflowRun(id: string): boolean {
  ensureDataDir();

  const filePath = path.join(DATA_DIR, `${id}.json`);
  if (!fs.existsSync(filePath)) {
    return false;
  }

  fs.unlinkSync(filePath);
  console.log(`[workflow-run-service] Deleted run: ${id}`);
  return true;
}

/**
 * Delete all runs for a specific workflow
 */
export function deleteWorkflowRunsByWorkflowId(workflowId: string): number {
  ensureDataDir();

  const files = fs.readdirSync(DATA_DIR).filter((f: string) => f.endsWith('.json'));
  let deletedCount = 0;

  for (const file of files) {
    try {
      const filePath = path.join(DATA_DIR, file);
      const content = fs.readFileSync(filePath, 'utf-8');
      const run = JSON.parse(content) as WorkflowRun;

      if (run.workflowId === workflowId) {
        fs.unlinkSync(filePath);
        deletedCount++;
      }
    } catch (e) {
      console.error(`[workflow-run-service] Failed to process ${file}:`, e);
    }
  }

  console.log(`[workflow-run-service] Deleted ${deletedCount} runs for workflow: ${workflowId}`);
  return deletedCount;
}

/**
 * Get statistics for a workflow
 */
export function getWorkflowStats(workflowId: string): {
  totalRuns: number;
  completedRuns: number;
  failedRuns: number;
  avgDuration: number;
  lastRunAt?: string;
} {
  const runs = listWorkflowRuns(workflowId);

  if (runs.length === 0) {
    return {
      totalRuns: 0,
      completedRuns: 0,
      failedRuns: 0,
      avgDuration: 0,
    };
  }

  const completedRuns = runs.filter(r => r.status === 'completed');
  const failedRuns = runs.filter(r => r.status === 'failed');
  const durations = runs.map(r => r.totalDuration).filter(d => d > 0);
  const avgDuration = durations.length > 0
    ? durations.reduce((a, b) => a + b, 0) / durations.length
    : 0;

  return {
    totalRuns: runs.length,
    completedRuns: completedRuns.length,
    failedRuns: failedRuns.length,
    avgDuration,
    lastRunAt: runs[0]?.completedAt,
  };
}
