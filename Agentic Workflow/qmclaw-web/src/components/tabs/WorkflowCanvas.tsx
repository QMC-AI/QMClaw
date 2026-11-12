/**
 * Workflow Canvas Tab - Canvas view container
 *
 * This is the new canvas-based workflow editor,
 * existing alongside the list view (WorkflowList).
 */

"use client";

import { useState, useCallback, useEffect } from 'react';
import WorkflowFlow from '../canvas/WorkflowFlow';
import NodeConfigPanel from '../panels/NodeConfigPanel';
import NodeSearch from '../canvas/controls/NodeSearch';
import DebugToolbar from '../canvas/controls/DebugToolbar';
import QwfInfoPanel from '../panels/QwfInfoPanel';
import { useWorkflowStore } from '../../store/workflowStore';
import { useAutoLayout } from '../../store/useAutoLayout';
import { useModelStore } from '../../store/modelStore';
import { api } from '../../lib/api';
import { toBackendCanvas, fromBackendCanvas, contextFromNodes } from '../../lib/qwfBridge';

interface Props {
  onLog: (msg: string, isError?: boolean) => void;
}

export default function WorkflowCanvas({ onLog }: Props) {
  const [configNodeId, setConfigNodeId] = useState<string | null>(null);
  const [showSearch, setShowSearch] = useState(false);
  const [showQwfInfo, setShowQwfInfo] = useState(false);
  const { computeLayout } = useAutoLayout();

  // Store state
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);
  const workflowId = useWorkflowStore((state) => state.workflowId);
  const workflowName = useWorkflowStore((state) => state.workflowName);
  const execution = useWorkflowStore((state) => state.execution);
  const resetExecution = useWorkflowStore((state) => state.resetExecution);
  const setExecuting = useWorkflowStore((state) => state.setExecuting);
  const setNodeResult = useWorkflowStore((state) => state.setNodeResult);
  const setExecutionComplete = useWorkflowStore((state) => state.setExecutionComplete);
  const loadWorkflow = useWorkflowStore((state) => state.loadWorkflow);
  const setWorkflowMeta = useWorkflowStore((state) => state.setWorkflowMeta);

  // Fetch models on mount (needed for LLM nodes like decision)
  const { fetchModels } = useModelStore();
  useEffect(() => { fetchModels(); }, [fetchModels]);

  // ── Helper: extract context from context nodes ─────────────────────────────────

  const getContextFromNodes = useCallback(() => {
    const contextNodes = nodes.filter((n) => n.data.type === 'context');
    const context: Record<string, string> = {};
    contextNodes.forEach((node) => {
      const vars = node.data.config?.variables || {};
      Object.entries(vars).forEach(([key, value]) => {
        context[key] = String(value);
      });
    });
    return context;
  }, [nodes]);

  // ── Run full workflow ────────────────────────────────────────────────────────

  const handleRunWorkflow = useCallback(async () => {
    console.log('[Frontend] handleRunWorkflow START');
    if (execution.status === 'running') return;

    // Get latest store state directly (force sync React Flow -> Store first)
    const store = useWorkflowStore.getState();

    // Directly sync React Flow data to store
    const canvasEl = document.querySelector('.react-flow');
    if (canvasEl) {
      window.dispatchEvent(new CustomEvent('qmclaw:force-sync'));
    }

    // Wait for state update
    await new Promise(resolve => setTimeout(resolve, 100));

    // Get latest nodes from store after sync
    const currentNodes = useWorkflowStore.getState().nodes;

    // Extract context from context nodes
    const context: Record<string, string> = {};
    currentNodes.filter((n) => n.data.type === 'context').forEach((node) => {
      const vars = node.data.config?.variables || {};
      Object.entries(vars).forEach(([key, value]) => {
        context[key] = String(value);
      });
    });

    // Validate all experiment nodes have qubit set
    const experimentNodes = currentNodes.filter((n) => n.data.type === 'experiment');
    const missingQubit = experimentNodes.find(
      (n) => {
        const qubitValue = String(n.data.config?.qubit || '');
        // Check if qubit is a variable reference or empty
        if (!qubitValue.trim()) return true;
        if (qubitValue.startsWith('{{') && qubitValue.endsWith('}}')) {
          // It's a variable reference - check if it exists in context
          const varName = qubitValue.slice(2, -2).trim();
          return !context[varName];
        }
        return false;
      }
    );
    if (missingQubit) {
      onLog(`⚠️ Cannot run workflow: Experiment node "${missingQubit.id}" is missing qubit parameter. Please add a Context node with "qubit" variable or set qubit directly.`, true);
      return;
    }

    onLog("▶ Running workflow: " + workflowName);
    console.log('[Frontend] handleRunWorkflow called, workflowName:', workflowName);
    onLog(`📦 Context: ${JSON.stringify(context)}`);
    onLog(`📋 画布节点：${currentNodes.length} 个 → 转成 IR 提交`);
    resetExecution();
    setWorkflowMeta(workflowId, workflowName);

    try {
      // 画布 → IR（`.qwf` 的语义表示），运行统一走 IR 执行器
      const st = useWorkflowStore.getState();
      const doc = toBackendCanvas(st.nodes, st.edges, {
        id: st.workflowId || undefined,
        name: st.workflowName,
        workflow: st.dsl.workflow,
        context: contextFromNodes(st.nodes),
      });
      const conv = await api.canvasToQwf(doc) as {
        ok: boolean;
        errors?: string[];
        ir?: unknown;
        stable?: boolean;
        unstableDiff?: string[];
      };
      if (!conv.ok) {
        setExecutionComplete('failed');
        onLog("❌ 画布无法转换为可执行的 IR：" + ((conv.errors || []).join('; ')), true);
        return;
      }
      if (conv.stable === false) {
        onLog("⚠️ 画布 → IR 存在转换损失，运行结果可能与画布不完全一致", true);
        (conv.unstableDiff || []).slice(0, 5).forEach((d) => onLog("   · " + d, true));
      }
      const res = await api.runIr({ ir: conv.ir, context }) as {
        ok: boolean;
        workflowId?: string;
        error?: string;
        warnings?: string[];
      };
      if (!res.ok || !res.workflowId) {
        setExecutionComplete('failed');
        onLog("❌ IR 提交失败：" + (res.error || 'unknown'), true);
        return;
      }
      (res.warnings || []).forEach((w) => onLog("⚠️ " + w, true));

      setWorkflowMeta(res.workflowId, workflowName);

      const result = await api.waitForWorkflow(
        res.workflowId,
        (status) => {
          // Update each node's status and log progress
          // 兼容后端两种返回结构：对象 {nodeId: node} 或 数组 [{nodeId, nodeType, status, output}]
          const rawNodes: any = status.nodes || {};
          const nodeMap: Record<string, any> = Array.isArray(rawNodes)
            ? rawNodes.reduce((acc: Record<string, any>, n: any) => {
                const key = n.nodeId || n.id || `node_${Object.keys(acc).length}`;
                acc[key] = n;
                return acc;
              }, {})
            : rawNodes;
          Object.entries(nodeMap).forEach(([nodeId, nodeStatus]) => {
            const raw = nodeStatus as any;
            const nested = raw.output && typeof raw.output === 'object' && !Array.isArray(raw.output)
              ? raw.output
              : (raw.result && typeof raw.result === 'object' && !Array.isArray(raw.result) ? raw.result : null);
            const output = nested || raw;
            const typedStatus = {
              status: raw.status,
              type: raw.type || raw.nodeType || '',
              stdout: output.stdout ?? raw.stdout ?? '',
              metrics: output.metrics ?? raw.metrics,
              error: output.error ?? raw.error,
              plotPath: output.plotPath ?? raw.plotPath,
            };
            if (typedStatus.status) {
              setNodeResult(nodeId, {
                status: typedStatus.status === 'completed' ? 'completed' : typedStatus.status,
                stdout: typedStatus.stdout || '',
                metrics: typedStatus.metrics,
                error: typedStatus.error,
                plotPath: typedStatus.plotPath,
                // 透传完整结果字段（conversation / symptom / recommendations 等）
                conversation: output.conversation ?? raw.conversation,
                symptom: output.symptom ?? raw.symptom,
                reasoning: output.reasoning ?? raw.reasoning,
                recommendations: output.recommendations ?? raw.recommendations,
                matchedRules: output.matchedRules ?? raw.matchedRules,
                t1_us: (output as any).t1_us ?? (raw as any).t1_us,
                decay_visible: (output as any).decay_visible ?? (raw as any).decay_visible,
                description: (output as any).description ?? (raw as any).description,
                qubit: (output as any).qubit ?? (raw as any).qubit,
                dataset_id: (output as any).dataset_id ?? (raw as any).dataset_id,
                experiment_type: (output as any).experiment_type ?? (raw as any).experiment_type,
                dataset_name: (output as any).dataset_name ?? (raw as any).dataset_name,
              });

              // Log node progress to output panel with detailed info
              const nodeIcon = typedStatus.type === 'print' ? '📝' :
                           typedStatus.type === 'experiment' ? '🔬' :
                           typedStatus.type === 'context' ? '📦' :
                           typedStatus.type === 'decision' ? '🧠' :
                           typedStatus.type === 'quality_gate' ? '✅' :
                           typedStatus.type === 'notify' ? '📢' :
                           typedStatus.type === 'code' ? '🐍' : '⬜';

              // Detailed status messages
              if (typedStatus.status === 'completed') {
                let detail = '';
                if (typedStatus.type === 'print' && typedStatus.stdout) {
                  detail = ` → "${typedStatus.stdout}"`;
                } else if (typedStatus.type === 'experiment' && typedStatus.metrics) {
                  const metricKeys = Object.keys(typedStatus.metrics).slice(0, 2);
                  if (metricKeys.length > 0) {
                    detail = ` → ${metricKeys.map(k => `${k}=${typedStatus.metrics![k].toFixed(3)}`).join(', ')}`;
                  }
                } else if (typedStatus.stdout && typedStatus.stdout.length < 80) {
                  detail = ` → ${typedStatus.stdout}`;
                }
                onLog(`  ${nodeIcon} [${nodeId}] ${typedStatus.type}: completed${detail}`);
              } else if (typedStatus.status === 'skipped') {
                const reason = typedStatus.error || typedStatus.stdout || 'condition not met';
                const reasonIcon = reason.includes('API key') ? '🔑' :
                                  reason.includes('condition') ? '⚙️' : '⏭️';
                onLog(`  ⏭️ [${nodeId}] ${typedStatus.type}: skipped ${reasonIcon}${reason}`);
              } else if (typedStatus.status === 'failed') {
                const errorMsg = typedStatus.error || 'unknown error';
                onLog(`  ❌ [${nodeId}] ${typedStatus.type}: failed - ${errorMsg}`, true);
              } else if (typedStatus.status === 'running') {
                onLog(`  ⏳ [${nodeId}] ${typedStatus.type}: running...`);
              }
            }
          });
        }
      );

      if (result.status === 'completed') {
        setExecutionComplete('completed');
        onLog("✅ Workflow completed");
      } else {
        setExecutionComplete('failed');
        onLog("❌ Workflow failed: " + result.status, true);
      }
    } catch (e: any) {
      setExecutionComplete('failed');
      onLog("❌ Error: " + e.message, true);
    }
  }, [execution.status, workflowName, workflowId, resetExecution, setWorkflowMeta, setNodeResult, setExecutionComplete, onLog]);

  // ── Run selected node ────────────────────────────────────────────────────────

  const handleRunSelected = useCallback(async () => {
    const selectedNodes = useWorkflowStore.getState().selectedNodes;
    if (selectedNodes.length !== 1) {
      onLog("⚠️ Select exactly one node to run", true);
      return;
    }

    const nodeId = selectedNodes[0];
    const node = nodes.find((n) => n.id === nodeId);
    if (!node) return;

    // Extract context from context nodes
    const context = getContextFromNodes();

    // Validate experiment node has qubit set (either directly or via variable)
    if (node.data.type === 'experiment') {
      const qubit = String(node.data.config.qubit || '');
      if (!qubit || qubit.trim() === '') {
        onLog("⚠️ Experiment node is missing qubit parameter. Please add a Context node with qubit variable or set qubit directly.", true);
        setNodeResult(nodeId, {
          status: 'failed',
          stdout: '',
          error: 'Missing qubit parameter. Please add a Context node with qubit variable.',
        });
        return;
      }
    }

    onLog("▶ Running node: " + nodeId);
    onLog(`📦 Context: ${JSON.stringify(context)}`);
    setExecuting(nodeId);

    try {
      const result = await api.runNode(
        {
          id: node.id,
          type: node.data.type,
          config: node.data.config,
        },
        context
      );

      const status = result.status === 'completed' ? 'completed' : result.status;
      setNodeResult(nodeId, {
        status,
        stdout: result.stdout || '',
        metrics: result.metrics,
        error: result.error,
        plotPath: result.plotPath,
        conversation: result.conversation,
      });

      if (result.status === 'completed' || result.status === 'passed') {
        setExecutionComplete('completed');
        onLog("✅ Node " + nodeId + " completed");
      } else if (result.status === 'failed') {
        setExecutionComplete('failed');
        onLog("❌ Node " + nodeId + " failed: " + (result.error || 'unknown'), true);
      }
    } catch (e: any) {
      setNodeResult(nodeId, {
        status: 'failed',
        stdout: '',
        error: e.message,
      });
      setExecutionComplete('failed');
      onLog("❌ Error: " + e.message, true);
    }
  }, [nodes, setExecuting, setNodeResult, setExecutionComplete, onLog, getContextFromNodes]);

  // ── Stop execution ──────────────────────────────────────────────────────────

  const handleStop = useCallback(async () => {
    if (!workflowId) return;

    try {
      await api.cancelWorkflow(workflowId);
      resetExecution();
      onLog("⏹ Workflow stopped");
    } catch (e: any) {
      onLog("❌ Stop failed: " + e.message, true);
    }
  }, [workflowId, resetExecution, onLog]);

  // ── Export workflow ──────────────────────────────────────────────────────────

  const handleExport = useCallback(async () => {
    // Force sync from React Flow to store before export
    // This ensures any pending changes are flushed
    const canvasEl = document.querySelector('.react-flow');
    if (canvasEl) {
      // Dispatch a custom event to trigger sync
      window.dispatchEvent(new CustomEvent('qmclaw:sync-edges'));
    }
    await new Promise((resolve) => setTimeout(resolve, 80));

    const st = useWorkflowStore.getState();
    const doc = toBackendCanvas(st.nodes, st.edges, {
      id: st.workflowId || undefined,
      name: st.workflowName,
      workflow: st.dsl.workflow,
      context: contextFromNodes(st.nodes),
    });

    try {
      const res = await api.canvasToQwf({
        ...doc,
        baseIr: st.dsl.baseIr ?? undefined,
      }) as {
        ok: boolean;
        errors?: string[];
        qwf?: string;
        stable?: boolean;
        unstableDiff?: string[];
        changed?: boolean;
        diff?: string[];
        error?: string;
      };

      if (!res.ok) {
        onLog("❌ 导出失败：" + ((res.errors || [res.error]).filter(Boolean).join('; ')), true);
        return;
      }
      // 语义等价：画布 → IR → 画布 → IR 必须稳定，否则导出会丢语义
      if (res.stable === false) {
        onLog("❌ 导出被阻止：画布无法无损表达当前工作流（导出会丢语义）", true);
        (res.unstableDiff || []).slice(0, 10).forEach((d) => onLog("   · " + d, true));
        return;
      }
      if (res.changed) {
        onLog(`ℹ️ 相对打开时的工作流有 ${res.diff?.length ?? 0} 处改动`);
      }

      const text = res.qwf || '';
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      a.download = `${(st.workflowName || 'workflow').replace(/[^a-zA-Z0-9_.-]/g, '_')}.qwf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      onLog(`📤 Exported: ${a.download}`);
    } catch (e: any) {
      onLog("❌ Export failed: " + e.message, true);
    }
  }, [onLog]);

  // ── Import workflow ──────────────────────────────────────────────────────────

  const handleImport = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.qwf,.json,.qmclaw.json';

    input.onchange = async (e: Event) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;

      try {
        const text = await file.text();

        // ── `.qwf`（新格式）：文本 → IR → 画布 ──────────────────────────────
        if (file.name.toLowerCase().endsWith('.qwf') || /^\s*workflow\s/.test(text)) {
          const res = await api.qwfToCanvas({ text }) as {
            ok: boolean;
            error?: string;
            canvas?: any;
            ir?: unknown;
            qwf?: string;
          };
          if (!res.ok || !res.canvas) {
            throw new Error(res.error || '解析 .qwf 失败');
          }
          const { nodes: storeNodes, edges: storeEdges } = fromBackendCanvas(res.canvas);
          const unsupported: string[] = res.canvas.unsupported || [];
          loadWorkflow(
            storeNodes as any,
            storeEdges as any,
            res.canvas.id || undefined,
            res.canvas.name || file.name.replace(/\.qwf$/i, ''),
            {
              baseIr: res.ir ?? null,
              qwf: res.qwf ?? text,
              source: 'import',
              workflow: res.canvas.workflow || {},
              unsupported,
            }
          );
          if (unsupported.length > 0) {
            onLog(`ℹ️ ${unsupported.length} 类语句画布暂不支持（已按原文保留）：${unsupported.join(', ')}`);
          }
          onLog(`📥 Imported .qwf: ${res.canvas.name || file.name}`);
          return;
        }

        // ── 老画布快照 JSON（兼容）──────────────────────────────────────────
        const workflowData = JSON.parse(text);

        // Validate structure
        if (!workflowData.nodes || !workflowData.edges) {
          throw new Error('Invalid workflow file format');
        }

        // Convert to store format
        const storeNodes = workflowData.nodes.map((n: {
          id: string;
          type: string;
          position: { x: number; y: number };
          config: Record<string, unknown>;
        }) => ({
          id: n.id,
          type: 'workflowNode' as const,
          position: n.position,
          data: {
            label: '',
            type: n.type,
            config: n.config || {},
            status: 'idle' as const,
          },
        }));

        const storeEdges = (workflowData.edges || []).map((e: {
          id?: string;
          source: string;
          target: string;
          sourceHandle?: string;
          type?: string;
        }) => ({
          id: e.id || `e_${e.source}_${e.target}`,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle,
          type: 'smoothstep' as const,
          animated: false,
          data: { type: e.type || 'dependency' },
        }));

        loadWorkflow(
          storeNodes as any,
          storeEdges as any,
          undefined,
          workflowData.name || 'Imported Workflow'
        );

        onLog(`📥 Imported: ${workflowData.name || 'workflow'}`);
      } catch (err: any) {
        onLog(`❌ Import failed: ${err.message}`, true);
      }
    };

    input.click();
  }, [loadWorkflow, onLog]);

  // ── Load workflow from server ───────────────────────────────────────────────

  const handleLoadWorkflow = useCallback(async (workflowId: string) => {
    onLog(`📂 Loading workflow ${workflowId}...`);

    try {
      const res = await api.getWorkflow(workflowId) as {
        ok: boolean;
        id: string;
        name: string;
        text?: string;
        ir?: unknown;
        canvas?: any;
        source?: string;
        error?: string;
      };
      if (!res.ok || !res.canvas) {
        throw new Error(res.error || '存档读取失败');
      }
      const { nodes: storeNodes, edges: storeEdges } = fromBackendCanvas(res.canvas);
      const unsupported: string[] = res.canvas.unsupported || [];
      loadWorkflow(
        storeNodes as any,
        storeEdges as any,
        res.id,
        res.name,
        {
          baseIr: res.ir ?? null,
          qwf: res.text ?? null,
          source: res.source || 'saved',
          workflow: res.canvas.workflow || {},
          unsupported,
        }
      );
      if (unsupported.length > 0) {
        onLog(`ℹ️ ${unsupported.length} 类语句画布暂不支持（已按原文保留）：${unsupported.join(', ')}`);
      }
      onLog(`✅ Loaded workflow: ${res.name}`);
    } catch (e: any) {
      onLog(`❌ Load failed: ${e.message}`, true);
    }
  }, [loadWorkflow, onLog]);

  // ── Save workflow ───────────────────────────────────────────────────────────

  const handleSave = useCallback(async () => {
    onLog("💾 Saving workflow...");

    const st = useWorkflowStore.getState();
    // 存档是 `.qwf`（语义），不是画布快照 —— 节点必须带上 `dsl`（原语句）
    const doc = toBackendCanvas(st.nodes, st.edges, {
      id: st.workflowId || undefined,
      name: st.workflowName,
      workflow: st.dsl.workflow,
      context: contextFromNodes(st.nodes),
    });
    const payload = { ...doc, baseIr: st.dsl.baseIr ?? undefined };
    // 本地备份（老 JSON 快照，仅作草稿兜底）
    const backup = {
      id: st.workflowId || undefined,
      name: st.workflowName,
      nodes: st.nodes.map((n) => ({
        id: n.id,
        type: n.data.type,
        position: n.position,
        config: n.data.config,
        // dsl（原语句）必须存进本地草稿，否则从草稿恢复后导出会丢语义
        dsl: n.data.dsl,
      })),
      edges: st.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle,
        type: e.data?.type || 'dependency',
      })),
    };

    try {
      const result = await api.saveWorkflow(payload) as {
        ok: boolean;
        id: string;
        name: string;
        version: number;
        error?: string;
        unstableDiff?: string[];
      };
      if (!result.ok) {
        onLog("❌ Save failed: " + (result.error || 'unknown'), true);
        (result.unstableDiff || []).slice(0, 10).forEach((d) => onLog("   · " + d, true));
        return;
      }
      setWorkflowMeta(result.id, result.name);
      onLog(`✅ Saved workflow: ${result.name} (v${result.version})`);

      // Also save to localStorage as backup
      const key = `qmclaw_workflow_${result.id}`;
      localStorage.setItem(key, JSON.stringify({ ...backup, id: result.id, name: result.name, savedAt: new Date().toISOString() }));
    } catch (e: any) {
      onLog("❌ Save failed: " + e.message, true);
      // Fallback to localStorage
      try {
        const key = `qmclaw_workflow_${workflowId || 'draft'}`;
        localStorage.setItem(key, JSON.stringify({ ...backup, savedAt: new Date().toISOString() }));
        onLog("✅ Saved to localStorage (offline mode)");
      } catch (e2: any) {
        onLog("❌ LocalStorage save also failed: " + e2.message, true);
      }
    }
  }, [workflowId, onLog, setWorkflowMeta]);

  // ── Config node ─────────────────────────────────────────────────────────────

  const handleConfigNode = useCallback((nodeId: string | null) => {
    setConfigNodeId(nodeId);
  }, []);

  // ── Auto-save to localStorage on changes ────────────────────────────────────

  // Track if we need to show "unsaved" indicator
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

  // Auto-save to localStorage whenever nodes or edges change
  useEffect(() => {
    if (nodes.length === 0) return; // Don't save empty workflow

    const workflowData = {
      version: '1.0',
      name: workflowName,
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.data.type,
        position: n.position,
        config: n.data.config,
        dsl: n.data.dsl,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle,
        type: e.data?.type || 'dependency',
      })),
      savedAt: new Date().toISOString(),
      autoSaved: true,
    };

    // Save to localStorage with workflow-specific key
    const key = workflowId ? `qmclaw_workflow_${workflowId}` : 'qmclaw_workflow_draft';
    localStorage.setItem(key, JSON.stringify(workflowData));

    // Also update the draft key
    localStorage.setItem('qmclaw_workflow_draft', JSON.stringify(workflowData));

    setHasUnsavedChanges(true);

    // Clear unsaved indicator after 2 seconds (simulating "saved")
    const timer = setTimeout(() => {
      setHasUnsavedChanges(false);
    }, 2000);

    return () => clearTimeout(timer);
  }, [nodes, edges, workflowId, workflowName]);

  // ── Load workflow on mount ──────────────────────────────────────────────────

  useEffect(() => {
    // Only auto-load if:
    // 1. Store has no nodes (empty workflow)
    // 2. workflowId is set (not a new workflow being created)
    // If workflowId is null, it means we just created a new workflow and should show empty canvas
    if (nodes.length > 0 || workflowId === null) return;

    // Check if there's a saved workflow in localStorage
    // 注意：草稿快照可能来自旧版本（没有 nodes/edges），逐个校验后再用，
    // 直接 `workflow.nodes.map` 会在老快照上抛 "Cannot read properties of undefined"。
    const keys = Object.keys(localStorage).filter((k) => k.startsWith('qmclaw_workflow_'));
    // 优先用当前 workflowId 对应的那份，其余按写入顺序兜底
    const preferred = `qmclaw_workflow_${workflowId}`;
    const ordered = [preferred, ...keys.filter((k) => k !== preferred)];

    for (const key of ordered) {
      const saved = localStorage.getItem(key);
      if (!saved) continue;
      let workflow: any = null;
      try {
        workflow = JSON.parse(saved);
      } catch {
        console.warn('[workflow] 跳过无法解析的本地草稿:', key);
        continue;
      }
      if (!workflow || !Array.isArray(workflow.nodes) || !Array.isArray(workflow.edges)) {
        console.warn('[workflow] 跳过格式不完整的本地草稿:', key);
        continue;
      }
      try {
        // Convert to store format
        const storeNodes = workflow.nodes.map((n: {
          id: string;
          type: string;
          position: { x: number; y: number };
          config: Record<string, unknown>;
          dsl?: unknown;
        }) => ({
          id: n.id,
          type: 'workflowNode' as const,
          position: n.position,
          data: {
            label: '',
            type: n.type,
            config: n.config,
            // dsl（原语句）必须一起恢复，否则导出 .qwf 会丢语义
            dsl: n.dsl as any,
            status: 'idle',
          },
        }));

        const storeEdges = workflow.edges.map((e: {
          id: string;
          source: string;
          target: string;
          sourceHandle?: string;
          type?: string;
        }) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle,
          type: 'smoothstep' as const,
          animated: false,
          data: { type: e.type || 'dependency' },
        }));

        loadWorkflow(storeNodes as any, storeEdges as any, key.replace('qmclaw_workflow_', ''), workflow.name);
        onLog("📂 Loaded workflow: " + workflow.name);
      } catch (e) {
        console.error('Failed to load workflow:', e);
      }
      return;
    }
  }, [loadWorkflow, onLog, nodes.length, workflowId]);

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        background: '#0a0f1a',
      }}
    >
      {/* Debug Toolbar - positioned at top center */}
      <div style={{
        position: 'absolute',
        top: '16px',
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: 100,
      }}>
        <DebugToolbar
          onRunWorkflow={handleRunWorkflow}
          onRunSelected={handleRunSelected}
          onStop={handleStop}
          onSave={handleSave}
          onUndo={() => {}}
          onRedo={() => {}}
          onAutoLayout={() => {
            computeLayout();
            onLog("📐 Auto layout applied");
          }}
          onToggleSearch={() => setShowSearch(!showSearch)}
          onExport={handleExport}
          onImport={handleImport}
          onLoad={handleLoadWorkflow}
          onToggleQwfInfo={() => setShowQwfInfo(!showQwfInfo)}
          onLog={onLog}
        />
      </div>

      {/* Canvas */}
      <WorkflowFlow
        onRunWorkflow={handleRunWorkflow}
        onRunSelected={handleRunSelected}
        onStop={handleStop}
        onSave={handleSave}
        onConfigNode={handleConfigNode}
        onAutoLayout={() => {
          computeLayout();
          onLog("📐 Auto layout applied");
        }}
        onToggleSearch={() => setShowSearch(!showSearch)}
        onExport={handleExport}
        onImport={handleImport}
        onLoad={handleLoadWorkflow}
        onLog={onLog}
        selectedNodeId={configNodeId}
      />

      {/* Config Panel */}
      {configNodeId && (
        <NodeConfigPanel
          nodeId={configNodeId}
          onClose={() => setConfigNodeId(null)}
          onRunNode={(nodeId) => {
            useWorkflowStore.getState().selectNode(nodeId);
            handleRunSelected();
          }}
        />
      )}

      {/* .qwf 信息面板 */}
      {showQwfInfo && <QwfInfoPanel onClose={() => setShowQwfInfo(false)} />}

      {/* Node Search */}
      {showSearch && <NodeSearch onClose={() => setShowSearch(false)} />}

      {/* Workflow name overlay */}
      <div
        style={{
          position: 'absolute',
          bottom: '16px',
          left: '50%',
          transform: 'translateX(-50%)',
          background: '#0f172a',
          border: '1px solid #1e293b',
          borderRadius: '6px',
          padding: '8px 16px',
          fontSize: '12px',
          color: '#94a3b8',
          zIndex: 5,
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}
      >
        <span style={{ color: '#38bdf8', fontWeight: 600 }}>{workflowName}</span>
        {workflowId && (
          <span style={{ color: '#64748b', fontFamily: 'monospace' }}>
            {workflowId.slice(0, 12)}...
          </span>
        )}
        <span style={{ color: '#475569' }}>
          {nodes.length} nodes | {edges.length} edges
        </span>
        {/* Auto-save indicator */}
        {hasUnsavedChanges && (
          <span
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              color: '#f59e0b',
              fontSize: '10px',
              animation: 'fadeIn 0.3s ease',
            }}
            title="Auto-saved to browser"
          >
            <span>💾</span>
            <span>auto-saved</span>
          </span>
        )}
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
      `}</style>
    </div>
  );
}
