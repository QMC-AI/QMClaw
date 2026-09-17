"use client";

import { useState, useEffect, useCallback } from "react";
import { api, WorkflowNode, WorkflowStatus } from "../lib/api";
import WorkflowCanvas from "./tabs/WorkflowCanvas";
import WorkflowListView, { StoredWorkflow as ListStoredWorkflow } from "./workflows/WorkflowListView";
import WorkflowRunHistory from "./workflows/WorkflowRunHistory";
import { useWorkflowStore, WorkflowNode as StoreNode, WorkflowEdge as StoreEdge } from "../store/workflowStore";

// ── Sub-tab type ───────────────────────────────────────────────────────────────
type WorkflowTab = "canvas" | "list" | "runs";

// ── Stored workflow type (matches backend API response) ────────────────────────
interface StoredWorkflow {
  id: string;
  name: string;
  version: number;
  createdAt: string;
  updatedAt: string;
  nodes: Array<{ id: string; type: string; position: { x: number; y: number }; config: Record<string, unknown> }>;
  edges: Array<{ id: string; source: string; target: string; sourceHandle?: string; type: string }>;
}

// ── Node type definitions ─────────────────────────────────────────────────────

const NODE_TYPES = [
  { value: "experiment", label: "🔬 Experiment", desc: "Run an sq.* experiment" },
  { value: "quality_gate", label: "✅ Quality Gate", desc: "Pass/fail on metric thresholds" },
  { value: "decision", label: "🧠 LLM Decision", desc: "LLM-powered branching decision" },
  { value: "analyze", label: "📊 Analyze", desc: "Parse metrics from previous node" },
  { value: "adjust_params", label: "⚙️ Adjust Params", desc: "Update qubit parameters" },
  { value: "image_analysis", label: "🖼 Image Analysis", desc: "LLM analysis of experiment plot" },
  { value: "image_classification", label: "🧠 Image Classification", desc: "ML classification of experiment image" },
  { value: "print", label: "📝 Print", desc: "Log a message" },
];

// ── Workflow templates based on PDF calibration flow ────────────────────────────

const TEMPLATES: Record<string, { name: string; desc: string; nodes: WorkflowNode[] }> = {
  "quick-calibration": {
    name: "⚡ Quick Calibration",
    desc: "iqraw + analyze (2 min)",
    nodes: [
      { id: "n1", type: "experiment", config: { fn: "sq.iqraw", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "n2", type: "quality_gate", depends: ["n1"], config: { ref: "n1", metric: "SNR", threshold: 1.5, direction: "above" } },
      { id: "n3", type: "analyze", depends: ["n2"], config: { ref: "n1" } },
      { id: "n4", type: "print", depends: ["n3"], config: { message: "=== Quick Results ===\nF0: {{nodes.n3.F0}}\nF1: {{nodes.n3.F1}}\nSNR: {{nodes.n3.SNR}}\nSeparation: {{nodes.n3.separation}}" } },
    ],
  },
  "full-calibration": {
    name: "🎯 Full Single-Qubit Calibration",
    desc: "Complete 11-step calibration (30+ min)",
    nodes: [
      { id: "s1", type: "experiment", config: { fn: "sq.s21", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "s1a", type: "quality_gate", depends: ["s1"], config: { ref: "s1", metric: "peak_count", threshold: 1, direction: "above" } },
      { id: "s1b", type: "analyze", depends: ["s1a"], config: { ref: "s1" } },
      { id: "s5", type: "experiment", depends: ["s1b"], config: { fn: "sq.iqraw", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "s5a", type: "quality_gate", depends: ["s5"], config: { ref: "s5", metric: "SNR", threshold: 1.5, direction: "above" } },
      { id: "s5b", type: "analyze", depends: ["s5a"], config: { ref: "s5" } },
      { id: "s6", type: "experiment", depends: ["s5b"], config: { fn: "sq.piamp", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "s6a", type: "quality_gate", depends: ["s6"], config: { ref: "s6", metric: "pi_amp", threshold: 0, direction: "above" } },
      { id: "s6b", type: "analyze", depends: ["s6a"], config: { ref: "s6" } },
      { id: "s8", type: "experiment", depends: ["s6b"], config: { fn: "sq.ramsey_df", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "s8a", type: "quality_gate", depends: ["s8"], config: { ref: "s8", metric: "T2", threshold: 0, direction: "above" } },
      { id: "s8b", type: "analyze", depends: ["s8a"], config: { ref: "s8" } },
      { id: "s9", type: "experiment", depends: ["s8b"], config: { fn: "sq.s21_dis", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "s9a", type: "analyze", depends: ["s9"], config: { ref: "s9" } },
      { id: "s10", type: "experiment", depends: ["s9a"], config: { fn: "sq.iqraw", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "s10a", type: "analyze", depends: ["s10"], config: { ref: "s10" } },
      { id: "summary", type: "print", depends: ["s10a"], config: { message: "=== {{qubit}} Calibration Complete ===\nIQ SNR: {{nodes.s10a.SNR}}\nF0: {{nodes.s10a.F0}}\nF1: {{nodes.s10a.F1}}\nRamsey T2: {{nodes.s8b.T2}}us\nPiAmp: {{nodes.s6b.pi_amp}}" } },
    ],
  },
  "gate-characterization": {
    name: "🔬 Gate Characterization",
    desc: "XEB + CZXEB for gate fidelity",
    nodes: [
      { id: "n1", type: "experiment", config: { fn: "sq.xeb", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "n2", type: "analyze", depends: ["n1"], config: { ref: "n1" } },
      { id: "n3", type: "quality_gate", depends: ["n2"], config: { ref: "n1", metric: "gate_fidelity", threshold: 0.99, direction: "above" } },
      { id: "n4", type: "print", depends: ["n3"], config: { message: "Gate Fidelity: {{nodes.n2.gate_fidelity}}\nError per cycle: {{nodes.n2.error_per_cycle}}" } },
    ],
  },
  "auto-optimize": {
    name: "🔄 Auto-Optimize Loop",
    desc: "Iterate until metric threshold",
    nodes: [
      { id: "init", type: "print", config: { message: "Starting auto-optimization for {{qubit}}" } },
      { id: "e1", type: "experiment", depends: ["init"], config: { fn: "sq.iqraw", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "a1", type: "analyze", depends: ["e1"], config: { ref: "e1" } },
      { id: "g1", type: "quality_gate", depends: ["a1"], config: { ref: "e1", metric: "SNR", threshold: 2.0, direction: "above" } },
      { id: "e2", type: "experiment", depends: ["g1"], config: { fn: "sq.ramsey_df", qubit: "{{qubit}}", params: { do_plot: true } } },
      { id: "a2", type: "analyze", depends: ["e2"], config: { ref: "e2" } },
      { id: "g2", type: "quality_gate", depends: ["a2"], config: { ref: "e2", metric: "T2", threshold: 500, direction: "above" } },
      { id: "final", type: "print", depends: ["g2"], config: { message: "=== Optimization Complete ===\nIQ SNR: {{nodes.a1.SNR}}\nRamsey T2: {{nodes.a2.T2}}ns" } },
    ],
  },
};

type TemplateKey = keyof typeof TEMPLATES;

type Props = {
  selectedQubit: string;
  onLog: (msg: string, isError?: boolean) => void;
};

export default function WorkflowDesigner({ selectedQubit, onLog }: Props) {
  const [nodes, setNodes] = useState<WorkflowNode[]>(TEMPLATES["quick-calibration"].nodes);
  const [workflowName, setWorkflowName] = useState("⚡ Quick Calibration");
  const [workflowId, setWorkflowId] = useState<string | null>(null);
  const [wfStatus, setWfStatus] = useState<WorkflowStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<WorkflowTab>("list");
  const [currentWorkflowId, setCurrentWorkflowId] = useState<string | null>(null); // Saved workflow ID

  // Read workflowId from Zustand store (updated to execution job ID after running)
  const storeWorkflowId = useWorkflowStore((state) => state.workflowId);

  // Show runs tab button if either local or store workflow ID is set
  const hasWorkflow = currentWorkflowId || storeWorkflowId;

  // ── Load template ─────────────────────────────────────────────────────────

  const loadTemplate = (key: TemplateKey) => {
    const tmpl = TEMPLATES[key];
    // Convert to store format
    const storeNodes: StoreNode[] = tmpl.nodes.map((n, idx) => ({
      id: n.id,
      type: 'workflowNode' as const,
      position: { x: 250, y: idx * 120 },
      data: {
        label: '',
        type: n.type as any,
        config: { ...n.config },
        status: 'idle' as const,
      },
    }));

    // Convert depends to edges
    const storeEdges: StoreEdge[] = [];
    tmpl.nodes.forEach(n => {
      (n as any).depends?.forEach((depId: string) => {
        storeEdges.push({
          id: `e_${depId}_${n.id}`,
          source: depId,
          target: n.id,
          type: 'smoothstep' as const,
          animated: false,
          data: { type: 'dependency' as const },
        });
      });
    });

    // Load into Zustand store
    useWorkflowStore.getState().loadWorkflow(storeNodes, storeEdges, undefined, tmpl.name);
    setWorkflowName(tmpl.name);
    setCurrentWorkflowId(null);
    onLog("Template loaded: " + tmpl.name);
  };

  // ── Load saved workflow ──────────────────────────────────────────────────────

  const handleSelectWorkflow = useCallback(async (workflow: ListStoredWorkflow) => {
    try {
      // Load full workflow from backend
      const full = await api.getWorkflow(workflow.id) as StoredWorkflow;

      // Convert to store format (for WorkflowCanvas which uses Zustand)
      const storeNodes: StoreNode[] = full.nodes.map(n => ({
        id: n.id,
        type: 'workflowNode' as const,
        position: n.position || { x: 0, y: 0 },
        data: {
          label: '',
          type: n.type as any,
          config: n.config || {},
          status: 'idle' as const,
        },
      }));

      const storeEdges: StoreEdge[] = (full.edges || []).map(e => ({
        id: e.id || `e_${e.source}_${e.target}`,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle,
        type: 'smoothstep' as const,
        animated: false,
        data: { type: (e.type || 'dependency') as 'dependency' | 'condition-pass' | 'condition-fail' },
      }));

      // Load into Zustand store (used by WorkflowCanvas)
      useWorkflowStore.getState().loadWorkflow(storeNodes, storeEdges, full.id, full.name);

      // Also update local state for any components using it
      setWorkflowName(full.name);
      setCurrentWorkflowId(full.id);
      setActiveTab("canvas");
      onLog(`Loaded workflow: ${full.name}`);
    } catch (err: any) {
      onLog("Failed to load workflow: " + err.message, true);
    }
  }, [onLog]);

  // ── Create new workflow ──────────────────────────────────────────────────────

  const handleCreateNew = useCallback((name: string, description?: string) => {
    // Clear Zustand store and start with empty canvas
    useWorkflowStore.getState().resetCanvas();
    // Set the workflow name in the store (not just local state)
    useWorkflowStore.getState().setWorkflowMeta(null, name);
    setWorkflowName(name);
    setCurrentWorkflowId(null);
    setActiveTab("canvas");
    onLog(`Created new workflow: ${name}`);
  }, [onLog]);

  // ── Node management ─────────────────────────────────────────────────────

  const addNode = (type: string) => {
    const id = "n" + Date.now();
    const baseConfig: Record<string, Record<string, unknown>> = {
      experiment: { fn: "sq.s21", qubit: "{{qubit}}", params: { do_plot: true } },
      quality_gate: { ref: "", metric: "SNR", threshold: 1.5, direction: "above" },
      decision: { prompt: "Should we proceed?", context: "" },
      analyze: { ref: "", source: "realtime" },
      adjust_params: { param: "fread", value: "" },
      image_analysis: { prompt: "Analyze this plot", imagePath: "" },
      image_classification: { qubit: "", experimentType: "spectroscopy", backend: "pytorch", reviewThreshold: 0.75, marginThreshold: 0.15 },
      print: { message: "Step completed" },
    };
    const newNode: WorkflowNode = { id, type, config: baseConfig[type] || {} };
    setNodes(prev => [...prev, newNode]);
    setSelectedNode(id);
  };

  const removeNode = (id: string) => {
    setNodes(prev => {
      return prev
        .filter(n => n.id !== id)
        .map(n => ({ ...n, depends: (n.depends || []).filter((d: string) => d !== id) }));
    });
    if (selectedNode === id) setSelectedNode(null);
  };

  const updateNodeConfig = (id: string, key: string, value: unknown) => {
    setNodes(prev =>
      prev.map(n => n.id === id ? { ...n, config: { ...n.config, [key]: value } } : n)
    );
  };

  const addDependency = (fromId: string, toId: string) => {
    setNodes(prev =>
      prev.map(n =>
        n.id === toId && !(n.depends || []).includes(fromId)
          ? { ...n, depends: [...(n.depends || []), fromId] }
          : n
      )
    );
  };

  const removeDependency = (fromId: string, toId: string) => {
    setNodes(prev =>
      prev.map(n =>
        n.id === toId ? { ...n, depends: (n.depends || []).filter((d: string) => d !== fromId) } : n
      )
    );
  };

  // ── Submit workflow ───────────────────────────────────────────────────────

  const handleRun = async () => {
    if (running) return;
    setRunning(true);
    setWfStatus(null);
    onLog("▶ Running workflow: " + workflowName);
    try {
      const res = await api.submitWorkflow({
        name: workflowName,
        nodes: nodes.map(n => ({
          ...n,
          config: {
            ...n.config,
            qubit: n.config.qubit === "{{qubit}}" ? selectedQubit : n.config.qubit,
          },
        })),
        context: { qubit: selectedQubit },
      }) as { workflowId: string };
      setWorkflowId(res.workflowId);
      onLog("Workflow submitted: " + res.workflowId);
      const result = await api.waitForWorkflow(
        res.workflowId,
        (s) => {
          setWfStatus(s);
          const done = Object.values(s.nodes || {}).filter((n: any) => n.status === "completed").length;
          onLog("  " + done + "/" + nodes.length + " nodes done");
        }
      );
      setWfStatus(result);
      if (result.status === "completed") {
        onLog("✅ Workflow completed");
      } else {
        onLog("❌ Workflow failed: " + result.status, true);
      }
    } catch (e: any) {
      onLog("❌ Error: " + e.message, true);
    } finally {
      setRunning(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────

  const selectedNodeData = nodes.find(n => n.id === selectedNode);
  const completedCount = wfStatus ? Object.values(wfStatus.nodes || {}).filter((n: any) => n.status === "completed").length : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem", height: "100%", overflow: "hidden" }}>

      {/* Tab selector + Template bar */}
      <div style={{
        display: "flex", gap: "0.5rem", alignItems: "center",
        background: "#0f172a", padding: "0.5rem 0.75rem",
        borderRadius: "0.375rem", border: "1px solid #1e293b",
        flexShrink: 0, overflow: "hidden",
      }}>
        {/* Tab buttons */}
        <div style={{ display: "flex", gap: "4px", marginRight: "1rem" }}>
          <button
            onClick={() => setActiveTab("list")}
            style={{
              padding: "0.3rem 0.75rem",
              borderRadius: "0.25rem",
              border: activeTab === "list" ? "1px solid #38bdf8" : "1px solid #334155",
              background: activeTab === "list" ? "#1e3a5f" : "#1e293b",
              color: activeTab === "list" ? "#38bdf8" : "#94a3b8",
              cursor: "pointer",
              fontSize: "0.72rem",
              fontWeight: 600,
            }}
          >
            📋 List
          </button>
          <button
            onClick={() => setActiveTab("canvas")}
            style={{
              padding: "0.3rem 0.75rem",
              borderRadius: "0.25rem",
              border: activeTab === "canvas" ? "1px solid #38bdf8" : "1px solid #334155",
              background: activeTab === "canvas" ? "#1e3a5f" : "#1e293b",
              color: activeTab === "canvas" ? "#38bdf8" : "#94a3b8",
              cursor: "pointer",
              fontSize: "0.72rem",
              fontWeight: 600,
            }}
          >
            📐 Canvas
          </button>
          {hasWorkflow && (
            <button
              onClick={() => setActiveTab("runs")}
              style={{
                padding: "0.3rem 0.75rem",
                borderRadius: "0.25rem",
                border: activeTab === "runs" ? "1px solid #38bdf8" : "1px solid #334155",
                background: activeTab === "runs" ? "#1e3a5f" : "#1e293b",
                color: activeTab === "runs" ? "#38bdf8" : "#94a3b8",
                cursor: "pointer",
                fontSize: "0.72rem",
                fontWeight: 600,
              }}
            >
              📊 Runs
            </button>
          )}
        </div>

        {/* Divider */}
        <div style={{ width: "1px", height: "24px", background: "#1e293b" }} />

        {/* Template buttons (only show in list view) */}
        {activeTab === "list" && (
          <>
            <span style={{ fontSize: "0.7rem", color: "#475569", fontWeight: 600 }}>TEMPLATE:</span>
            {(Object.keys(TEMPLATES) as TemplateKey[]).map(key => {
              const tmpl = TEMPLATES[key];
              const isActive = workflowName === tmpl.name;
              return (
                <button
                  key={key}
                  onClick={() => loadTemplate(key)}
                  style={{
                    padding: "0.3rem 0.75rem",
                    borderRadius: "0.25rem",
                    border: isActive ? "1px solid #38bdf8" : "1px solid #334155",
                    background: isActive ? "#1e3a5f" : "#1e293b",
                    color: isActive ? "#38bdf8" : "#94a3b8",
                    cursor: "pointer",
                    fontSize: "0.72rem",
                    fontWeight: 600,
                  }}
                >
                  {tmpl.name}
                </button>
              );
            })}
          </>
        )}

        {activeTab === "canvas" && (
          <span style={{ fontSize: "0.7rem", color: "#64748b" }}>
            Drag nodes from the palette on the left to build your workflow
          </span>
        )}

        <div style={{ marginLeft: "auto", display: "flex", gap: "0.5rem" }}>
          <button
            onClick={handleRun}
            disabled={running || nodes.length === 0}
            style={{
              padding: "0.35rem 1.5rem",
              borderRadius: "0.25rem",
              border: "none",
              background: running ? "#334155" : "#22c55e",
              color: running ? "#64748b" : "#fff",
              cursor: running ? "not-allowed" : "pointer",
              fontSize: "0.8rem",
              fontWeight: 700,
            }}
          >
            {running ? "⏳ Running..." : "▶ Run Workflow"}
          </button>
        </div>
      </div>

      {/* Render based on active tab */}
      {activeTab === "list" ? (
        /* List View */
        <div style={{ flex: 1, overflow: "hidden" }}>
          <WorkflowListView
            onSelectWorkflow={handleSelectWorkflow}
            onCreateNew={handleCreateNew}
          />
        </div>
      ) : activeTab === "runs" ? (
        /* Run History View */
        <div style={{ flex: 1, overflow: "hidden" }}>
          {/* Read workflowId from Zustand store - it gets updated to the execution job ID after running */}
          <RunHistoryWrapper workflowName={workflowName} onBack={() => setActiveTab("canvas")} />
        </div>
      ) : (
        /* Canvas View - use WorkflowCanvas component */
        <div style={{ flex: 1, overflow: "hidden" }}>
          <WorkflowCanvas onLog={onLog} />
        </div>
      )}
    </div>
  );
}

// ── Run History Wrapper ───────────────────────────────────────────────────────
// Reads workflowId from Zustand store (updated to execution job ID after running)

function RunHistoryWrapper({ workflowName, onBack }: { workflowName: string; onBack: () => void }) {
  // Get workflowId from Zustand - this is updated by WorkflowCanvas after running
  const workflowId = useWorkflowStore((state) => state.workflowId);

  if (!workflowId) {
    return (
      <div style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "8px",
        height: "200px",
        color: "#475569",
        fontSize: "12px",
      }}>
        <span style={{ fontSize: "24px" }}>📊</span>
        <span>No workflow loaded</span>
        <span style={{ fontSize: "10px" }}>Load or run a workflow first</span>
      </div>
    );
  }

  return (
    <WorkflowRunHistory
      workflowId={workflowId}
      workflowName={workflowName}
      onBack={onBack}
    />
  );
}

// ── Config panel ─────────────────────────────────────────────────────────────

type ConfigPanelProps = {
  node: WorkflowNode;
  allNodes: WorkflowNode[];
  onUpdate: (key: string, val: unknown) => void;
  onAddDep: (from: string) => void;
  onRemoveDep: (from: string) => void;
  workflowId: string | null;
  nodeStatus?: { status: string; metrics?: Record<string, number>; plotPath?: string };
};

function ConfigPanel({ node, allNodes, onUpdate, onAddDep, onRemoveDep, workflowId, nodeStatus }: ConfigPanelProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      <div style={{ fontSize: "0.7rem", fontWeight: 600, color: "#475569", marginBottom: "0.25rem" }}>
        CONFIG — {node.id}
      </div>

      {/* Experiment */}
      {node.type === "experiment" ? (
        <>
          <CfgField label="Function" value={String(node.config.fn || "")} onChange={v => onUpdate("fn", v)} />
          <CfgField label="Qubit" value={String(node.config.qubit || "")} onChange={v => onUpdate("qubit", v)} />
          <div>
            <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.2rem" }}>Params (JSON)</div>
            <textarea
              value={JSON.stringify(node.config.params || {}, null, 2)}
              onChange={e => { try { onUpdate("params", JSON.parse(e.target.value)); } catch {} }}
              style={{
                width: "100%", minHeight: "80px",
                background: "#1e293b", color: "#e2e8f0",
                border: "1px solid #334155", borderRadius: "0.25rem",
                fontFamily: "monospace", fontSize: "0.7rem",
                padding: "0.4rem", resize: "vertical", boxSizing: "border-box",
              }}
            />
          </div>
        </>
      ) : null}

      {/* Quality gate */}
      {node.type === "quality_gate" ? (
        <>
          <CfgField label="Ref Node" value={String(node.config.ref || "")} onChange={v => onUpdate("ref", v)} />
          <CfgField label="Metric" value={String(node.config.metric || "")} onChange={v => onUpdate("metric", v)} placeholder="SNR, F0, T2, ..." />
          <CfgField label="Threshold" value={String(node.config.threshold ?? "")} onChange={v => onUpdate("threshold", parseFloat(v) || 0)} />
          <div>
            <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.2rem" }}>Direction</div>
            {["above", "below"].map(d => (
              <label key={d} style={{ display: "flex", alignItems: "center", gap: "0.3rem", fontSize: "0.7rem", color: "#94a3b8", cursor: "pointer", marginRight: "0.5rem" }}>
                <input
                  type="radio"
                  checked={node.config.direction === d}
                  onChange={() => onUpdate("direction", d)}
                />
                {d}
              </label>
            ))}
          </div>
          <CfgField label="Pass message" value={String(node.config.pass_msg || "")} onChange={v => onUpdate("pass_msg", v)} />
          <CfgField label="Fail message" value={String(node.config.fail_msg || "")} onChange={v => onUpdate("fail_msg", v)} />
        </>
      ) : null}

      {/* Analyze */}
      {node.type === "analyze" ? (
        <CfgField label="Reference Node" value={String(node.config.ref || "")} onChange={v => onUpdate("ref", v)} />
      ) : null}

      {/* Decision */}
      {node.type === "decision" ? (
        <>
          <CfgField label="Prompt" value={String(node.config.prompt || "")} onChange={v => onUpdate("prompt", v)} />
          <CfgField label="Context" value={String(node.config.context || "")} onChange={v => onUpdate("context", v)} />
        </>
      ) : null}

      {/* Print */}
      {node.type === "print" ? (
        <div>
          <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.2rem" }}>
            Message (use {"{{nodes.nX.metric}}"})
          </div>
          <textarea
            value={String(node.config.message || "")}
            onChange={e => onUpdate("message", e.target.value)}
            style={{
              width: "100%", minHeight: "100px",
              background: "#1e293b", color: "#e2e8f0",
              border: "1px solid #334155", borderRadius: "0.25rem",
              fontFamily: "monospace", fontSize: "0.7rem",
              padding: "0.4rem", resize: "vertical", boxSizing: "border-box",
            }}
          />
        </div>
      ) : null}

      {/* Adjust params */}
      {node.type === "adjust_params" ? (
        <>
          <CfgField label="Parameter" value={String(node.config.param || "")} onChange={v => onUpdate("param", v)} placeholder="fread, f10, pi_amp, ..." />
          <CfgField label="New Value" value={String(node.config.value || "")} onChange={v => onUpdate("value", v)} />
        </>
      ) : null}

      {/* Image analysis */}
      {node.type === "image_analysis" ? (
        <>
          <CfgField label="Prompt" value={String(node.config.prompt || "")} onChange={v => onUpdate("prompt", v)} />
          <CfgField label="Image Path" value={String(node.config.imagePath || "")} onChange={v => onUpdate("imagePath", v)} />
        </>
      ) : null}

      {/* Image classification */}
      {node.type === "image_classification" ? (
        <>
          <CfgField label="Qubit" value={String(node.config.qubit || "")} onChange={v => onUpdate("qubit", v)} />
          <CfgField label="Experiment Type" value={String(node.config.experimentType || "spectroscopy")} onChange={v => onUpdate("experimentType", v)} />
          <CfgField label="Backend" value={String(node.config.backend || "pytorch")} onChange={v => onUpdate("backend", v)} />
          <CfgField label="Review Threshold" value={String(node.config.reviewThreshold ?? 0.75)} onChange={v => onUpdate("reviewThreshold", parseFloat(v) || 0.75)} />
          <CfgField label="Margin Threshold" value={String(node.config.marginThreshold ?? 0.15)} onChange={v => onUpdate("marginThreshold", parseFloat(v) || 0.15)} />
        </>
      ) : null}

      {/* Dependencies */}
      <div style={{ marginTop: "0.5rem" }}>
        <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.3rem" }}>DEPENDENCIES</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.3rem" }}>
          {allNodes.filter(n => n.id !== node.id).map(n => {
            const isDep = (node.depends || []).includes(n.id);
            return (
              <button
                key={n.id}
                onClick={() => isDep ? onRemoveDep(n.id) : onAddDep(n.id)}
                style={{
                  padding: "0.2rem 0.5rem",
                  borderRadius: "0.25rem",
                  border: "1px solid",
                  borderColor: isDep ? "#22c55e" : "#334155",
                  background: isDep ? "#1e3a2f" : "#1e293b",
                  color: isDep ? "#22c55e" : "#64748b",
                  cursor: "pointer",
                  fontSize: "0.65rem",
                }}
              >
                {isDep ? "✓" : "+"} {n.id}
              </button>
            );
          })}
        </div>
      </div>

      {/* Node results */}
      {nodeStatus ? (
        <div style={{ marginTop: "0.5rem", padding: "0.5rem", background: "#0f172a", borderRadius: "0.25rem", border: "1px solid #1e293b" }}>
          <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.3rem" }}>RESULTS</div>
          <div style={{
            fontSize: "0.65rem",
            color: nodeStatus.status === "completed" ? "#22c55e" : "#f87171",
          }}>
            {nodeStatus.status}
          </div>
          {nodeStatus.metrics ? (
            <div style={{ fontSize: "0.65rem", fontFamily: "monospace", color: "#94a3b8", marginTop: "0.3rem" }}>
              {Object.entries(nodeStatus.metrics).map(([k, v]) => k + ": " + v).join(" | ")}
            </div>
          ) : null}
          {nodeStatus.plotPath ? (
            <img
              src={"/plot/" + workflowId + "?node=" + node.id}
              style={{ display: "block", width: "100%", maxHeight: "150px", objectFit: "contain", marginTop: "0.3rem" }}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// ── Helper: config field ─────────────────────────────────────────────────────

type CfgFieldProps = {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
};

function CfgField({ label, value, onChange, placeholder }: CfgFieldProps) {
  return (
    <div>
      <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.2rem" }}>{label}</div>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          width: "100%",
          padding: "0.35rem 0.5rem",
          background: "#1e293b",
          color: "#e2e8f0",
          border: "1px solid #334155",
          borderRadius: "0.25rem",
          fontFamily: "monospace",
          fontSize: "0.72rem",
          boxSizing: "border-box",
        }}
      />
    </div>
  );
}