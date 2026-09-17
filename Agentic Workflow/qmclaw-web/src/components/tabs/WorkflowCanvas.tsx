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
import { useWorkflowStore } from '../../store/workflowStore';
import { useAutoLayout } from '../../store/useAutoLayout';
import { useModelStore } from '../../store/modelStore';
import { api } from '../../lib/api';

interface Props {
  onLog: (msg: string, isError?: boolean) => void;
}

export default function WorkflowCanvas({ onLog }: Props) {
  const [configNodeId, setConfigNodeId] = useState<string | null>(null);
  const [showSearch, setShowSearch] = useState(false);
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
    onLog(`📋 Submitting nodes: ${JSON.stringify(currentNodes.map(n => ({ id: n.id, type: n.data.type })))}`);
    resetExecution();
    setWorkflowMeta(workflowId, workflowName);

    try {
      const submitData = {
        name: workflowName,
        nodes: currentNodes.map((n) => ({
          id: n.id,
          type: n.data.type,
          depends: useWorkflowStore.getState().edges
            .filter((e) => e.target === n.id)
            .map((e) => e.source),
          config: n.data.config,
        })),
        context,
      };
      console.log('[Frontend] submitWorkflow data:', JSON.stringify(submitData));
      const res = await api.submitWorkflow(submitData) as { workflowId: string };

      setWorkflowMeta(res.workflowId, workflowName);

      const result = await api.waitForWorkflow(
        res.workflowId,
        (status) => {
          // Update each node's status and log progress
          Object.entries(status.nodes || {}).forEach(([nodeId, nodeStatus]) => {
            const typedStatus = nodeStatus as {
              status: string;
              type: string;
              stdout?: string;
              metrics?: Record<string, number>;
              error?: string;
              plotPath?: string;
            };
            if (typedStatus.status) {
              setNodeResult(nodeId, {
                status: typedStatus.status === 'completed' ? 'completed' : typedStatus.status,
                stdout: typedStatus.stdout || '',
                metrics: typedStatus.metrics,
                error: typedStatus.error,
                plotPath: typedStatus.plotPath,
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

      if (result.status === 'completed') {
        onLog("✅ Node " + nodeId + " completed");
      } else if (result.status === 'failed') {
        onLog("❌ Node " + nodeId + " failed: " + (result.error || 'unknown'), true);
      } else if (result.status === 'passed') {
        onLog("✅ Node " + nodeId + " passed");
      }
    } catch (e: any) {
      setNodeResult(nodeId, {
        status: 'failed',
        stdout: '',
        error: e.message,
      });
      onLog("❌ Error: " + e.message, true);
    }
  }, [nodes, setExecuting, setNodeResult, onLog, getContextFromNodes]);

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

  const handleExport = useCallback(() => {
    // Force sync from React Flow to store before export
    // This ensures any pending changes are flushed
    const canvasEl = document.querySelector('.react-flow');
    if (canvasEl) {
      // Dispatch a custom event to trigger sync
      window.dispatchEvent(new CustomEvent('qmclaw:sync-edges'));
    }

    const workflowData = {
      version: '1.0',
      exportedAt: new Date().toISOString(),
      name: workflowName,
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.data.type,
        position: n.position,
        config: n.data.config,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle,
        type: e.data?.type || 'dependency',
      })),
    };

    const jsonStr = JSON.stringify(workflowData, null, 2);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `${workflowName.replace(/[^a-zA-Z0-9]/g, '_')}_${Date.now()}.qmclaw.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    onLog(`📤 Exported: ${workflowName}.qmclaw.json`);
  }, [workflowName, nodes, edges, onLog]);

  // ── Import workflow ──────────────────────────────────────────────────────────

  const handleImport = useCallback(() => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,.qmclaw.json';

    input.onchange = async (e: Event) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;

      try {
        const text = await file.text();
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
      const workflow = await api.getWorkflow(workflowId) as {
        id: string;
        name: string;
        nodes: Array<{
          id: string;
          type: string;
          position: { x: number; y: number };
          config: Record<string, unknown>;
        }>;
        edges: Array<{
          id: string;
          source: string;
          target: string;
          sourceHandle?: string;
          type?: string;
        }>;
      };

      // Convert to store format
      const storeNodes = workflow.nodes.map((n) => ({
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

      const storeEdges = (workflow.edges || []).map((e) => ({
        id: e.id || `e_${e.source}_${e.target}`,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle,
        type: 'smoothstep' as const,
        animated: false,
        data: { type: e.type || 'dependency' },
      }));

      loadWorkflow(storeNodes as any, storeEdges as any, workflow.id, workflow.name);
      onLog(`✅ Loaded workflow: ${workflow.name}`);
    } catch (e: any) {
      onLog(`❌ Load failed: ${e.message}`, true);
    }
  }, [loadWorkflow, onLog]);

  // ── Save workflow ───────────────────────────────────────────────────────────

  const handleSave = useCallback(async () => {
    onLog("💾 Saving workflow...");

    const workflowData = {
      id: workflowId || undefined,
      name: workflowName,
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.data.type,
        position: n.position,
        config: n.data.config,
      })),
      edges: edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle,
        type: e.data?.type || 'dependency',
      })),
    };

    try {
      const result = await api.saveWorkflow(workflowData) as { id: string; name: string; version: number };
      setWorkflowMeta(result.id, result.name);
      onLog(`✅ Saved workflow: ${result.name} (v${result.version})`);

      // Also save to localStorage as backup
      const key = `qmclaw_workflow_${result.id}`;
      localStorage.setItem(key, JSON.stringify({ ...workflowData, savedAt: new Date().toISOString() }));
    } catch (e: any) {
      onLog("❌ Save failed: " + e.message, true);
      // Fallback to localStorage
      try {
        const key = `qmclaw_workflow_${workflowId || 'draft'}`;
        localStorage.setItem(key, JSON.stringify({ ...workflowData, savedAt: new Date().toISOString() }));
        onLog("✅ Saved to localStorage (offline mode)");
      } catch (e2: any) {
        onLog("❌ LocalStorage save also failed: " + e2.message, true);
      }
    }
  }, [workflowName, nodes, edges, workflowId, onLog, setWorkflowMeta]);

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
    const keys = Object.keys(localStorage).filter((k) => k.startsWith('qmclaw_workflow_'));
    if (keys.length > 0) {
      try {
        const saved = localStorage.getItem(keys[0]);
        if (saved) {
          const workflow = JSON.parse(saved);
          // Convert to store format
          const storeNodes = workflow.nodes.map((n: {
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
              config: n.config,
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

          loadWorkflow(storeNodes as any, storeEdges as any, keys[0].replace('qmclaw_workflow_', ''), workflow.name);
          onLog("📂 Loaded workflow: " + workflow.name);
        }
      } catch (e) {
        console.error('Failed to load workflow:', e);
      }
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
