/**
 * qwfBridge - 画布（store）与后端 `.qwf` 画布文档之间的转换层
 *
 * 两边的节点形状不一样：
 * - 前端 store：`{ id, position, data: { label, type, config, dsl? } }`
 * - 后端 IR 画布：`{ id, type, position, label, config, dsl, depends, conditions }`
 *
 * 所有 `.qwf` 相关的请求（导入 / 导出 / 保存 / 运行）都从这里过，
 * 免得每个调用点各写一遍字段映射。
 */

import type { WorkflowEdge, WorkflowNode } from '@/store/workflowStore';

export interface BackendCanvasNode {
  id: string;
  type: string;
  position?: { x: number; y: number };
  label?: string;
  config?: Record<string, unknown>;
  dsl?: { op?: string; as?: string | null; stmt?: unknown; merged?: boolean };
  depends?: string[];
  conditions?: unknown[];
}

export interface BackendCanvasEdge {
  id?: string;
  source: string;
  target: string;
  type?: string;
  data?: { type?: string };
}

export interface BackendCanvas {
  id?: string;
  name?: string;
  nodes: BackendCanvasNode[];
  edges: BackendCanvasEdge[];
  context?: Record<string, unknown>;
  workflow?: Record<string, unknown>;
  ir?: unknown;
  qwf?: string;
  unsupported?: string[];
}

export interface CanvasDoc {
  id?: string;
  name?: string;
  nodes: BackendCanvasNode[];
  edges: Array<{ id: string; source: string; target: string; type: string }>;
  context?: Record<string, unknown>;
  workflow?: Record<string, unknown>;
}

/** 边 → 每个节点的 `depends`（只算 dependency 边）。 */
export function edgesToDepends(
  nodes: Array<{ id: string }>,
  edges: Array<{ source: string; target: string; type?: string; data?: { type?: string } }>
): Record<string, string[]> {
  const depends: Record<string, string[]> = {};
  nodes.forEach((n) => { depends[n.id] = []; });
  edges.forEach((e) => {
    const kind = e.data?.type || e.type || 'dependency';
    if (kind.startsWith('condition')) return;
    if (!depends[e.target]) depends[e.target] = [];
    if (!depends[e.target].includes(e.source)) depends[e.target].push(e.source);
  });
  return depends;
}

/** store 节点/边 → 后端画布文档（`.qwf` 接口用这个形状）。 */
export function toBackendCanvas(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  opts: {
    id?: string;
    name?: string;
    workflow?: Record<string, unknown>;
    context?: Record<string, unknown>;
  } = {}
): CanvasDoc {
  const depends = edgesToDepends(nodes, edges);
  const backendNodes: BackendCanvasNode[] = nodes.map((n) => ({
    id: n.id,
    type: n.data.type,
    position: n.position,
    label: n.data.label,
    config: n.data.config || {},
    dsl: (n.data.dsl as BackendCanvasNode['dsl']) || undefined,
    depends: depends[n.id] || [],
    conditions: [],
  }));
  const backendEdges = edges.map((e, index) => ({
    id: e.id || `e_${e.source}_${e.target}_${index}`,
    source: e.source,
    target: e.target,
    type: e.data?.type || 'dependency',
  }));
  return {
    id: opts.id,
    name: opts.name,
    nodes: backendNodes,
    edges: backendEdges,
    context: opts.context || {},
    workflow: opts.workflow || {},
  };
}

/** 后端画布 → store 节点/边。 */
export function fromBackendCanvas(canvas: BackendCanvas): {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
} {
  const nodes: WorkflowNode[] = (canvas.nodes || []).map((n) => ({
    id: n.id,
    position: n.position || { x: 0, y: 0 },
    data: {
      label: n.label || n.type,
      type: n.type as WorkflowNode['data']['type'],
      config: n.config || {},
      dsl: n.dsl,
      status: 'idle',
    },
  }));
  const edges: WorkflowEdge[] = (canvas.edges || []).map((e, index) => ({
    id: e.id || `e_${e.source}_${e.target}_${index}`,
    source: e.source,
    target: e.target,
    type: 'smoothstep',
    animated: false,
    data: {
      type: (e.data?.type || e.type || 'dependency') as
        'dependency' | 'condition-pass' | 'condition-fail',
    },
  }));
  return { nodes, edges };
}

/**
 * 服务端存档/示例的返回形状。
 *
 * 新存档是 `.qwf`：`{ ok, id, name, text, ir, canvas, source }`；
 * 老的画布 JSON 存档是 `{ id, name, nodes, edges }`。
 * 两种都要能打开，所以统一在这里归一化。
 */
export interface StoredWorkflowPayload {
  ok?: boolean;
  id?: string;
  name?: string;
  text?: string;
  ir?: unknown;
  canvas?: BackendCanvas;
  source?: string;
  path?: string;
  error?: string;
  /** 老画布 JSON 存档 */
  nodes?: BackendCanvasNode[];
  edges?: BackendCanvasEdge[];
}

export interface NormalizedWorkflow {
  id: string;
  name: string;
  canvas: BackendCanvas;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  /** `.qwf` 原文（服务端存档没有时退回画布里带的） */
  qwf: string | null;
  /** 打开时的原始 IR（导出时用作语义等价基准） */
  ir: unknown | null;
  /** 工作流级字段：meta / env / budget / policy / defaults / context */
  workflow: Record<string, unknown>;
  /** 画布表达不了的 DSL 语句（repeat / if / call…） */
  unsupported: string[];
  source: string | null;
}

/** 服务端存档/示例 → 统一形状（新 `.qwf` 与老 JSON 都能进画布）。 */
export function normalizeStoredWorkflow(
  payload: StoredWorkflowPayload,
  fallbackId = ''
): NormalizedWorkflow {
  const hasCanvas = !!payload.canvas && Array.isArray(payload.canvas.nodes);
  const canvas: BackendCanvas = hasCanvas
    ? payload.canvas!
    : {
        id: payload.id,
        name: payload.name,
        nodes: payload.nodes || [],
        edges: payload.edges || [],
      };
  const { nodes, edges } = fromBackendCanvas(canvas);
  return {
    id: payload.id || canvas.id || fallbackId,
    name: payload.name || canvas.name || fallbackId,
    canvas,
    nodes,
    edges,
    qwf: payload.text ?? (typeof canvas.qwf === 'string' ? canvas.qwf : null),
    ir: payload.ir ?? canvas.ir ?? null,
    workflow: (canvas.workflow || {}) as Record<string, unknown>,
    unsupported: canvas.unsupported || [],
    source: payload.source ?? null,
  };
}

/** 从 store 节点里提取 context 变量（`context` 节点的 variables）。 */
export function contextFromNodes(nodes: WorkflowNode[]): Record<string, string> {
  const context: Record<string, string> = {};
  nodes
    .filter((n) => n.data.type === 'context')
    .forEach((node) => {
      const vars = (node.data.config?.variables || {}) as Record<string, unknown>;
      Object.entries(vars).forEach(([key, value]) => {
        context[key] = String(value);
      });
    });
  return context;
}
