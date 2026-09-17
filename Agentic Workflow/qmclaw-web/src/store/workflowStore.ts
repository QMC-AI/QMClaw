/**
 * Workflow Canvas Store - Zustand state management
 *
 * Manages:
 * - Canvas nodes and connections
 * - Selection state
 * - Execution state
 * - Undo/redo history
 * - Node templates
 */

import { create } from 'zustand';

// ── Types ──────────────────────────────────────────────────────────────────────

export type NodeType =
  | 'experiment'
  | 'quality_gate'
  | 'decision'
  | 'analyze'
  | 'adjust_params'
  | 'image_analysis'
  | 'image_classification'
  | 'print'
  | 'parallel'
  | 'while'
  | 'notify'
  | 'context'
  | 'code';

export interface WorkflowNodeData {
  label: string;
  type: NodeType;
  config: Record<string, unknown>;
  status?: 'idle' | 'running' | 'completed' | 'failed' | 'passed' | 'skipped';
  metrics?: Record<string, number>;
  error?: string;
  templateId?: string;
  // Result fields (populated after execution)
  result?: Record<string, unknown>;
  symptom?: string;
  recommendations?: Array<{
    fn: string;
    qubit?: string;
    params?: Record<string, unknown>;
    reason?: string;
  }>;
  matchedRules?: string[];
  // Index signature for React Flow compatibility
  [key: string]: unknown;
}

export interface WorkflowNode {
  id: string;
  position: { x: number; y: number };
  data: WorkflowNodeData;
  selected?: boolean;
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
  type?: string;
  animated?: boolean;
  data?: {
    type: 'dependency' | 'condition-pass' | 'condition-fail';
    label?: string;
  };
}

export interface ExecutionState {
  workflowId: string | null;
  status: 'idle' | 'running' | 'paused' | 'completed' | 'failed';
  currentNodeId: string | null;
  completedNodes: string[];
  nodeResults: Record<string, {
    status: string;
    stdout: string;
    metrics?: Record<string, number>;
    plotPath?: string;
    error?: string;
    // Call code executed
    callCode?: string;
    // Experiment-specific fields
    plotCommand?: string;
    analysisPrompt?: string;
    analysis?: ExperimentAnalysisResult;
    modifiedPlotPath?: string;
    plotError?: string;
    analysisError?: string;
    batchResults?: Array<{
      fn: string;
      qubit: string;
      reason: string;
      params: Record<string, unknown>;
      callCode: string;
      stdout: string;
      plotPath?: string;
      modifiedPlotPath?: string;
      metrics?: Record<string, number>;
      analysis?: ExperimentAnalysisResult;
      plotError?: string;
      analysisError?: string;
    }>;
    // LLM Decision node fields (also used for experiment analysis)
    conversation?: {
      model?: string;
      provider?: string;
      systemPrompt?: string;
      prompt?: string;
      context?: string;
      messagesSent?: Array<{role: string; content: string}>;
      rawResponse?: Record<string, unknown>;
      decision?: string;
      reasoning?: string;
      analysis?: string;
      // Decision node specific fields
      symptom?: string;
      recommendations?: Array<{
        fn: string;
        qubit?: string;
        params?: Record<string, unknown>;
        reason?: string;
      }>;
      matchedRules?: string[];
    };
    // Decision node direct fields (alternative to conversation)
    symptom?: string;
    recommendations?: Array<{
      fn: string;
      qubit?: string;
      params?: Record<string, unknown>;
      reason?: string;
    }>;
    matchedRules?: string[];
  }>;
  lastExecutionResults: Record<string, Record<string, unknown>>;
}

export interface ExperimentAnalysisResult {
  prompt: string;
  result: string;
  model: string;
  provider: string;
  messagesSent?: Array<{role: string; content: string}>;
  rawResponse?: Record<string, unknown>;
}

export interface NodeTemplate {
  id: string;
  name: string;
  type: NodeType;
  config: Record<string, unknown>;
  tags: string[];
  author: string;
  version: string;
  createdAt: string;
  updatedAt: string;
}

// ── History for undo/redo ─────────────────────────────────────────────────────

interface HistoryState {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

interface WorkflowStore {
  // Canvas state
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  selectedNodes: string[];

  // Workflow metadata
  workflowId: string | null;
  workflowName: string;
  workflowVersion: number;

  // Execution state
  execution: ExecutionState;

  // Templates
  templates: NodeTemplate[];

  // History for undo/redo
  history: HistoryState[];
  historyIndex: number;

  // ── Node ID counter ──────────────────────────────────────────────────────────
  nodeCounters: Record<NodeType, number>;

  // ── Actions ──────────────────────────────────────────────────────────────────

  // Node CRUD
  addNode: (type: NodeType, position: { x: number; y: number }) => WorkflowNode;
  updateNode: (id: string, data: Partial<WorkflowNodeData>) => void;
  deleteNode: (id: string) => void;
  duplicateNode: (id: string) => void;
  setNodes: (nodes: WorkflowNode[] | ((nodes: WorkflowNode[]) => WorkflowNode[])) => void;

  // Selection
  selectNode: (id: string, multi?: boolean) => void;
  clearSelection: () => void;

  // Edge CRUD
  addEdge: (source: string, target: string, type?: 'dependency' | 'condition-pass' | 'condition-fail', sourceHandle?: string) => void;
  deleteEdge: (id: string) => void;
  setEdges: (edges: WorkflowEdge[] | ((edges: WorkflowEdge[]) => WorkflowEdge[])) => void;

  // Execution
  setExecuting: (nodeId: string) => void;
  setNodeResult: (nodeId: string, result: ExecutionState['nodeResults'][string]) => void;
  setExecutionComplete: (status: 'completed' | 'failed') => void;
  resetExecution: () => void;

  // Workflow metadata
  setWorkflowMeta: (id: string | null, name: string) => void;

  // Templates
  addTemplate: (template: Omit<NodeTemplate, 'id' | 'createdAt' | 'updatedAt'>) => void;
  deleteTemplate: (id: string) => void;

  // History
  pushHistory: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: () => boolean;
  canRedo: () => boolean;

  // Load workflow
  loadWorkflow: (nodes: WorkflowNode[], edges: WorkflowEdge[], id?: string, name?: string) => void;

  // Reset
  resetCanvas: () => void;
}

// ── Helper: generate node ID ──────────────────────────────────────────────────

const getNodeTypePrefix = (type: NodeType): string => {
  const prefixes: Record<NodeType, string> = {
    experiment: 'exp',
    quality_gate: 'gate',
    decision: 'dec',
    analyze: 'ana',
    adjust_params: 'adj',
    image_analysis: 'img',
    image_classification: 'icls',
    print: 'prt',
    parallel: 'par',
    while: 'loop',
    notify: 'ntf',
    context: 'ctx',
    code: 'code',
  };
  return prefixes[type];
};

// ── Helper: default node config ───────────────────────────────────────────────

const getDefaultNodeConfig = (type: NodeType): Record<string, unknown> => {
  const configs: Record<NodeType, Record<string, unknown>> = {
    experiment: {
      fn: 'sq.iqraw',
      qubit: '{{qubit}}',
      params: { do_plot: true },
      batchConfig: null,
      // Customizable plotting and analysis
      plotCommand: 'plt.show()',
      analysisCommand: '',
      analysisPrompt: '分析这个量子比特实验图像，描述你看到的波形特征和质量',
      autoAnalyze: true,
      autoRunAnalysis: false,
    },
    quality_gate: { ref: '', metric: 'SNR', threshold: 1.5, direction: 'above', onFail: 'stop' },
    decision: {
      mode: 'analysis',  // 'analysis' | 'intent'
      rulesContextId: '',  // ID of context node containing rules
      intentPrompt: '分析用户需求，生成对应的实验列表',
      conditions: {},  // { edgeId: conditionName }
      defaultOutput: '',  // default recommendation when no rules match
    },
    analyze: { ref: '', experimentsToAnalyze: [] },
    adjust_params: { param: 'fread', value: '' },
    image_analysis: { prompt: 'Analyze this plot', imagePath: '' },
    image_classification: { qubit: '', experimentType: 'spectroscopy', backend: 'pytorch', reviewThreshold: 0.75, marginThreshold: 0.15 },
    print: { message: 'Step completed' },
    parallel: { mode: 'auto', waitFor: 'all' },
    while: { condition: '{{nodes.n1.SNR}} < 2.0', maxIterations: 10, timeout: 300 },
    notify: { channel: 'feishu', trigger: 'always', template: '' },
    context: { variables: { qubit: 'q3ld4' } },
    code: {
      code: '# Python code here\n# Available: {{variable}}, np, json, re, math\nresult = {"status": "ok"}',
      timeout: 30,
      returnVariable: 'result',
    },
  };
  return configs[type];
};

// ── Helper: node label ────────────────────────────────────────────────────────

const getNodeLabel = (type: NodeType): string => {
  const labels: Record<NodeType, string> = {
    experiment: '🔬 Experiment',
    quality_gate: '✅ Quality Gate',
    decision: '🧠 LLM Decision',
    analyze: '📊 Analyze',
    adjust_params: '⚙️ Adjust Params',
    image_analysis: '🖼 Image Analysis',
    image_classification: '🧠 Image Classification',
    print: '📝 Print',
    parallel: '⚡ Parallel',
    while: '🔄 While Loop',
    notify: '📢 Notify',
    context: '📦 Context',
    code: '🐍 Code',
  };
  return labels[type];
};

// ── Store ─────────────────────────────────────────────────────────────────────

export const useWorkflowStore = create<WorkflowStore>((set, get) => ({
  // Initial state
  nodes: [],
  edges: [],
  selectedNodes: [],

  workflowId: null,
  workflowName: 'Untitled Workflow',
  workflowVersion: 1,

  execution: {
    workflowId: null,
    status: 'idle',
    currentNodeId: null,
    completedNodes: [],
    nodeResults: {},
    lastExecutionResults: {},
  },

  templates: [],

  history: [],
  historyIndex: -1,

  nodeCounters: {
    experiment: 0,
    quality_gate: 0,
    decision: 0,
    analyze: 0,
    adjust_params: 0,
    image_analysis: 0,
    image_classification: 0,
    print: 0,
    parallel: 0,
    while: 0,
    notify: 0,
    context: 0,
    code: 0,
  },

  // ── Node CRUD ────────────────────────────────────────────────────────────────

  addNode: (type, position) => {
    const counters = get().nodeCounters;
    counters[type]++;
    const prefix = getNodeTypePrefix(type);
    const id = `${prefix}_${counters[type]}`;

    const newNode: WorkflowNode = {
      id,
      position,
      data: {
        label: getNodeLabel(type),
        type,
        config: getDefaultNodeConfig(type),
        status: 'idle',
      },
    };

    set((state) => ({
      nodes: [...state.nodes, newNode],
      nodeCounters: counters,
    }));

    get().pushHistory();
    return newNode;
  },

  updateNode: (id, data) => {
    set((state) => ({
      nodes: state.nodes.map((node) =>
        node.id === id ? { ...node, data: { ...node.data, ...data } } : node
      ),
    }));
  },

  deleteNode: (id) => {
    get().pushHistory();
    set((state) => ({
      nodes: state.nodes.filter((node) => node.id !== id),
      edges: state.edges.filter((edge) => edge.source !== id && edge.target !== id),
      selectedNodes: state.selectedNodes.filter((nid) => nid !== id),
    }));
  },

  duplicateNode: (id) => {
    const node = get().nodes.find((n) => n.id === id);
    if (!node) return;

    const { type } = node.data;
    const counters = get().nodeCounters;
    counters[type]++;
    const prefix = getNodeTypePrefix(type);
    const newId = `${prefix}_${counters[type]}`;

    const newNode: WorkflowNode = {
      ...node,
      id: newId,
      position: { x: node.position.x + 50, y: node.position.y + 50 },
      data: { ...node.data },
      selected: false,
    };

    set((state) => ({
      nodes: [...state.nodes, newNode],
      nodeCounters: counters,
    }));

    get().pushHistory();
  },

  setNodes: (newNodes) => {
    set((state) => ({
      nodes: typeof newNodes === 'function' ? newNodes(state.nodes) : newNodes,
    }));
  },

  // ── Selection ────────────────────────────────────────────────────────────────

  selectNode: (id, multi = false) => {
    set((state) => {
      if (multi) {
        const isSelected = state.selectedNodes.includes(id);
        return {
          selectedNodes: isSelected
            ? state.selectedNodes.filter((nid) => nid !== id)
            : [...state.selectedNodes, id],
          nodes: state.nodes.map((node) => ({
            ...node,
            selected: isSelected ? node.id !== id : state.selectedNodes.includes(node.id) || node.id === id,
          })),
        };
      }
      return {
        selectedNodes: [id],
        nodes: state.nodes.map((node) => ({
          ...node,
          selected: node.id === id,
        })),
      };
    });
  },

  clearSelection: () => {
    set((state) => ({
      selectedNodes: [],
      nodes: state.nodes.map((node) => ({ ...node, selected: false })),
    }));
  },

  // ── Edge CRUD ────────────────────────────────────────────────────────────────

  addEdge: (source, target, type = 'dependency', sourceHandle) => {
    const newEdge: WorkflowEdge = {
      id: `e_${source}_${target}_${Date.now()}`,
      source,
      target,
      sourceHandle,
      type: 'smoothstep',
      animated: false,
      data: { type },
    };
    set((state) => ({
      edges: [...state.edges, newEdge],
    }));
    get().pushHistory();
  },

  deleteEdge: (id) => {
    get().pushHistory();
    set((state) => ({
      edges: state.edges.filter((edge) => edge.id !== id),
    }));
  },

  setEdges: (edges) => {
    set((state) => ({
      edges: typeof edges === 'function' ? edges(state.edges) : edges,
    }));
  },

  // ── Execution ────────────────────────────────────────────────────────────────

  setExecuting: (nodeId) => {
    set((state) => ({
      execution: {
        ...state.execution,
        status: 'running',
        currentNodeId: nodeId,
      },
      nodes: state.nodes.map((node) =>
        node.id === nodeId
          ? { ...node, data: { ...node.data, status: 'running' } }
          : node
      ),
    }));
  },

  setNodeResult: (nodeId, result) => {
    set((state) => {
      const newResults = { ...state.execution.nodeResults, [nodeId]: result };
      const newCompleted = result.status === 'completed' || result.status === 'passed'
        ? [...state.execution.completedNodes, nodeId]
        : state.execution.completedNodes;

      return {
        execution: {
          ...state.execution,
          nodeResults: newResults,
          completedNodes: newCompleted,
          currentNodeId: null,
          lastExecutionResults: {
            ...state.execution.lastExecutionResults,
            [nodeId]: result.metrics || {},
          },
        },
        nodes: state.nodes.map((node) =>
          node.id === nodeId
            ? {
                ...node,
                data: {
                  ...node.data,
                  status: result.status === 'completed' || result.status === 'passed' ? 'completed' : 'failed',
                  metrics: result.metrics,
                  error: result.error,
                  // Transfer experiment-specific fields
                  result: result,
                  // LLM decision fields
                  symptom: result.symptom,
                  recommendations: result.recommendations,
                  matchedRules: result.matchedRules,
                },
              }
            : node
        ),
      };
    });
  },

  setExecutionComplete: (status) => {
    set((state) => ({
      execution: {
        ...state.execution,
        status,
        currentNodeId: null,
      },
    }));
  },

  resetExecution: () => {
    set((state) => ({
      execution: {
        workflowId: null,
        status: 'idle',
        currentNodeId: null,
        completedNodes: [],
        nodeResults: {},
        lastExecutionResults: state.execution.lastExecutionResults, // Keep last results for variable preview
      },
      nodes: state.nodes.map((node) => ({
        ...node,
        data: { ...node.data, status: 'idle', metrics: undefined, error: undefined },
      })),
    }));
  },

  // ── Workflow metadata ────────────────────────────────────────────────────────

  setWorkflowMeta: (id, name) => {
    set({ workflowId: id, workflowName: name });
  },

  // ── Templates ────────────────────────────────────────────────────────────────

  addTemplate: (template) => {
    const now = new Date().toISOString();
    const newTemplate: NodeTemplate = {
      ...template,
      id: `tmpl_${Date.now()}`,
      createdAt: now,
      updatedAt: now,
    };
    set((state) => ({
      templates: [...state.templates, newTemplate],
    }));
    return newTemplate;
  },

  deleteTemplate: (id) => {
    set((state) => ({
      templates: state.templates.filter((t) => t.id !== id),
    }));
  },

  // ── History ──────────────────────────────────────────────────────────────────

  pushHistory: () => {
    set((state) => {
      const newHistory = state.history.slice(0, state.historyIndex + 1);
      newHistory.push({
        nodes: JSON.parse(JSON.stringify(state.nodes)),
        edges: JSON.parse(JSON.stringify(state.edges)),
      });
      // Keep only last 50 states
      if (newHistory.length > 50) newHistory.shift();
      return {
        history: newHistory,
        historyIndex: newHistory.length - 1,
      };
    });
  },

  undo: () => {
    const { history, historyIndex } = get();
    if (historyIndex <= 0) return;
    const prevState = history[historyIndex - 1];
    set({
      nodes: prevState.nodes,
      edges: prevState.edges,
      historyIndex: historyIndex - 1,
    });
  },

  redo: () => {
    const { history, historyIndex } = get();
    if (historyIndex >= history.length - 1) return;
    const nextState = history[historyIndex + 1];
    set({
      nodes: nextState.nodes,
      edges: nextState.edges,
      historyIndex: historyIndex + 1,
    });
  },

  canUndo: () => get().historyIndex > 0,
  canRedo: () => get().historyIndex < get().history.length - 1,

  // ── Load workflow ────────────────────────────────────────────────────────────

  loadWorkflow: (nodes, edges, id, name) => {
    // Update node counters
    const counters: Record<NodeType, number> = {
      experiment: 0, quality_gate: 0, decision: 0, analyze: 0,
      adjust_params: 0, image_analysis: 0, image_classification: 0, print: 0, parallel: 0,
      while: 0, notify: 0, context: 0, code: 0,
    };
    nodes.forEach((node) => {
      const type = node.data.type;
      const match = node.id.match(/^([a-z_]+)_(\d+)$/);
      if (match) {
        const num = parseInt(match[2], 10);
        if (num > counters[type]) counters[type] = num;
      }
    });

    set({
      nodes,
      edges,
      workflowId: id || null,
      workflowName: name || 'Untitled Workflow',
      nodeCounters: counters,
      selectedNodes: [],
      execution: {
        workflowId: id || null,
        status: 'idle',
        currentNodeId: null,
        completedNodes: [],
        nodeResults: {},
        lastExecutionResults: {},
      },
      history: [{ nodes, edges }],
      historyIndex: 0,
    });
  },

  // ── Reset ───────────────────────────────────────────────────────────────────

  resetCanvas: () => {
    set({
      nodes: [],
      edges: [],
      selectedNodes: [],
      workflowId: null,
      workflowName: 'Untitled Workflow',
      workflowVersion: 1,
      execution: {
        workflowId: null,
        status: 'idle',
        currentNodeId: null,
        completedNodes: [],
        nodeResults: {},
        lastExecutionResults: {},
      },
      history: [],
      historyIndex: -1,
      nodeCounters: {
        experiment: 0, quality_gate: 0, decision: 0, analyze: 0,
        adjust_params: 0, image_analysis: 0, image_classification: 0, print: 0, parallel: 0,
        while: 0, notify: 0, context: 0, code: 0,
      },
    });
  },
}));

// ── Selector hooks ────────────────────────────────────────────────────────────

export const useSelectedNodes = () => {
  const nodes = useWorkflowStore((state) => state.nodes);
  const selectedIds = useWorkflowStore((state) => state.selectedNodes);
  return nodes.filter((n) => selectedIds.includes(n.id));
};

export const useLastExecutionResults = () => {
  return useWorkflowStore((state) => state.execution.lastExecutionResults);
};
