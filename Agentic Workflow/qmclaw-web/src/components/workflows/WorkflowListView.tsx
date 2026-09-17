"use client";

import { useState, useEffect } from "react";
import { api } from "../../lib/api";

export interface StoredWorkflow {
  id: string;
  name: string;
  version: number;
  createdAt: string;
  updatedAt: string;
  nodes?: Array<{ id: string; type: string; position?: { x: number; y: number }; config?: Record<string, unknown> }>;
  edges?: Array<{ id: string; source: string; target: string }>;
}

interface Props {
  onSelectWorkflow: (workflow: StoredWorkflow) => void;
  onCreateNew: (name: string, description?: string) => void;
}

const NODE_TYPE_ICONS: Record<string, string> = {
  experiment: "🔬",
  quality_gate: "✅",
  decision: "🧠",
  analyze: "📊",
  adjust_params: "⚙️",
  image_analysis: "🖼",
  image_classification: "🧠",
  notify: "📢",
  code: "🐍",
  print: "📝",
  while: "🔄",
  parallel: "⚡",
  context: "📦",
};

const CARD_COLORS = [
  { border: "#3b82f6", bg: "linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)" },
  { border: "#22c55e", bg: "linear-gradient(135deg, #1a3d2e 0%, #0f172a 100%)" },
  { border: "#f59e0b", bg: "linear-gradient(135deg, #3d2e1a 0%, #0f172a 100%)" },
  { border: "#8b5cf6", bg: "linear-gradient(135deg, #2e1a3d 0%, #0f172a 100%)" },
  { border: "#ec4899", bg: "linear-gradient(135deg, #3d1a2e 0%, #0f172a 100%)" },
  { border: "#06b6d4", bg: "linear-gradient(135deg, #1a3d3d 0%, #0f172a 100%)" },
];

export default function WorkflowListView({ onSelectWorkflow, onCreateNew }: Props) {
  const [workflows, setWorkflows] = useState<StoredWorkflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [stats, setStats] = useState<Record<string, { totalRuns: number; completedRuns: number; avgDuration: number }>>({});
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [showNewModal, setShowNewModal] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");

  useEffect(() => {
    loadWorkflows();
  }, []);

  async function loadWorkflows() {
    setLoading(true);
    try {
      const wfs = await api.listSavedWorkflows() as StoredWorkflow[];
      setWorkflows(wfs);

      const statsMap: Record<string, { totalRuns: number; completedRuns: number; avgDuration: number }> = {};
      for (const wf of wfs) {
        try {
          const s = await api.getWorkflowStats(wf.id) as { totalRuns: number; completedRuns: number; avgDuration: number };
          statsMap[wf.id] = s;
        } catch {
          statsMap[wf.id] = { totalRuns: 0, completedRuns: 0, avgDuration: 0 };
        }
      }
      setStats(statsMap);
    } catch (err) {
      console.error("Failed to load workflows:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete(e: React.MouseEvent, workflowId: string) {
    e.stopPropagation();
    if (!confirm("Delete this workflow?")) return;
    setDeleting(workflowId);
    try {
      await api.deleteWorkflow(workflowId);
      setWorkflows(prev => prev.filter(w => w.id !== workflowId));
    } catch (err) {
      console.error("Failed to delete workflow:", err);
    } finally {
      setDeleting(null);
    }
  }

  async function handleDuplicate(e: React.MouseEvent, workflow: StoredWorkflow) {
    e.stopPropagation();
    try {
      const full = await api.getWorkflow(workflow.id);
      await api.saveWorkflow({
        name: workflow.name + " (copy)",
        nodes: full.nodes,
        edges: full.edges,
      });
      loadWorkflows();
    } catch (err) {
      console.error("Failed to duplicate workflow:", err);
    }
  }

  function handleRenameClick(e: React.MouseEvent, workflow: StoredWorkflow) {
    e.stopPropagation();
    setRenamingId(workflow.id);
    setRenameValue(workflow.name);
  }

  async function handleRenameSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!renamingId || !renameValue.trim()) {
      setRenamingId(null);
      return;
    }
    try {
      const full = await api.getWorkflow(renamingId);
      await api.saveWorkflow({
        id: renamingId,
        name: renameValue.trim(),
        nodes: full.nodes,
        edges: full.edges,
      });
      setWorkflows(prev => prev.map(w =>
        w.id === renamingId ? { ...w, name: renameValue.trim() } : w
      ));
      setRenamingId(null);
    } catch (err) {
      console.error("Failed to rename workflow:", err);
    }
  }

  function handleRenameKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setRenamingId(null);
    }
  }

  function getNodeTypes(nodes?: Array<{ type: string }>): string[] {
    const counts: Record<string, number> = {};
    nodes?.forEach(n => {
      counts[n.type] = (counts[n.type] || 0) + 1;
    });
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([type, count]) => `${NODE_TYPE_ICONS[type] || "📦"}${count}`);
  }

  function getCardColor(id: string): typeof CARD_COLORS[0] {
    const hash = id.split("").reduce((acc, c) => acc + c.charCodeAt(0), 0);
    return CARD_COLORS[hash % CARD_COLORS.length];
  }

  function formatTime(iso: string): string {
    const d = new Date(iso);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    if (days === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (days === 1) return "1d ago";
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  if (loading) {
    return (
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "200px",
        color: "#64748b",
        fontSize: "13px",
      }}>
        Loading...
      </div>
    );
  }

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      gap: "16px",
      height: "100%",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "10px 16px",
        background: "#0f172a",
        borderRadius: "8px",
        border: "1px solid #1e293b",
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "13px", fontWeight: 600, color: "#e2e8f0" }}>
            Workflows
          </span>
          <span style={{
            padding: "2px 8px",
            background: "#1e293b",
            borderRadius: "10px",
            fontSize: "11px",
            color: "#64748b",
          }}>
            {workflows.length}
          </span>
        </div>
        <button
          onClick={() => setShowNewModal(true)}
          style={{
            padding: "5px 14px",
            background: "#22c55e",
            border: "none",
            borderRadius: "5px",
            color: "#fff",
            fontSize: "12px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          + New
        </button>
      </div>

      {/* Workflow grid */}
      {workflows.length === 0 ? (
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "10px",
          padding: "40px",
          background: "#0a0f1a",
          borderRadius: "8px",
          border: "1px dashed #1e293b",
          color: "#475569",
          flex: 1,
        }}>
          <span style={{ fontSize: "28px" }}>📋</span>
          <span style={{ fontSize: "13px" }}>No workflows yet</span>
          <button
            onClick={() => setShowNewModal(true)}
            style={{
              padding: "6px 20px",
              background: "#1e293b",
              border: "1px solid #334155",
              borderRadius: "5px",
              color: "#94a3b8",
              fontSize: "12px",
              cursor: "pointer",
              marginTop: "8px",
            }}
          >
            Create one
          </button>
        </div>
      ) : (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
          gap: "8px",
          overflow: "auto",
          flex: 1,
          padding: "2px",
        }}>
          {workflows.map((wf, idx) => {
            const wfStats = stats[wf.id] || { totalRuns: 0, completedRuns: 0, avgDuration: 0 };
            const color = getCardColor(wf.id);
            const isHovered = hoveredId === wf.id;
            const nodeTypes = getNodeTypes(wf.nodes);

            return (
              <div
                key={wf.id}
                onClick={() => onSelectWorkflow(wf)}
                onMouseEnter={() => setHoveredId(wf.id)}
                onMouseLeave={() => setHoveredId(null)}
                style={{
                  padding: "12px",
                  background: isHovered ? "#1e293b" : "#0f172a",
                  border: `1px solid ${isHovered ? color.border : "#1e293b"}`,
                  borderLeft: `3px solid ${color.border}`,
                  borderRadius: "6px",
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                  display: "flex",
                  flexDirection: "column",
                  gap: "8px",
                  minHeight: "130px",
                }}
              >
                {/* Top row: name + actions */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                  {renamingId === wf.id ? (
                    <form onSubmit={handleRenameSubmit} style={{ flex: 1 }}>
                      <input
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onKeyDown={handleRenameKeyDown}
                        onBlur={handleRenameSubmit}
                        autoFocus
                        style={{
                          width: "100%",
                          padding: "2px 4px",
                          background: "#1e293b",
                          color: "#e2e8f0",
                          border: "1px solid #38bdf8",
                          borderRadius: "3px",
                          fontSize: "12px",
                          fontWeight: 600,
                          outline: "none",
                        }}
                      />
                    </form>
                  ) : (
                    <div style={{
                      fontSize: "12px",
                      fontWeight: 600,
                      color: "#e2e8f0",
                      flex: 1,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      paddingRight: "8px",
                    }}>
                      {wf.name}
                    </div>
                  )}
                  {isHovered && !renamingId && (
                    <div style={{ display: "flex", gap: "3px", flexShrink: 0 }}>
                      <button
                        onClick={(e) => handleRenameClick(e, wf)}
                        style={{
                          padding: "2px 5px",
                          background: "transparent",
                          border: "1px solid #334155",
                          borderRadius: "3px",
                          color: "#64748b",
                          fontSize: "9px",
                          cursor: "pointer",
                        }}
                      >
                        ✏️
                      </button>
                      <button
                        onClick={(e) => handleDuplicate(e, wf)}
                        style={{
                          padding: "2px 5px",
                          background: "transparent",
                          border: "1px solid #334155",
                          borderRadius: "3px",
                          color: "#64748b",
                          fontSize: "9px",
                          cursor: "pointer",
                        }}
                      >
                        📋
                      </button>
                      <button
                        onClick={(e) => handleDelete(e, wf.id)}
                        style={{
                          padding: "2px 5px",
                          background: "transparent",
                          border: "1px solid #334155",
                          borderRadius: "3px",
                          color: deleting === wf.id ? "#f87171" : "#64748b",
                          fontSize: "9px",
                          cursor: "pointer",
                        }}
                      >
                        {deleting === wf.id ? "..." : "✕"}
                      </button>
                    </div>
                  )}
                </div>

                {/* Node types row */}
                <div style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "2px",
                  fontSize: "10px",
                  color: "#94a3b8",
                }}>
                  {nodeTypes.length > 0 ? (
                    nodeTypes.slice(0, 6).map((icon, i) => (
                      <span
                        key={i}
                        style={{
                          padding: "1px 4px",
                          background: "#1e293b",
                          borderRadius: "2px",
                        }}
                      >
                        {icon}
                      </span>
                    ))
                  ) : (
                    <span style={{ color: "#475569", fontSize: "9px" }}>Empty</span>
                  )}
                </div>

                {/* Bottom row: stats */}
                <div style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  paddingTop: "4px",
                  borderTop: "1px solid #1e293b",
                }}>
                  <span style={{ fontSize: "9px", color: "#64748b" }}>
                    {formatTime(wf.updatedAt)}
                  </span>
                  <div style={{ display: "flex", gap: "6px", fontSize: "9px", color: "#64748b" }}>
                    {wfStats.totalRuns > 0 && (
                      <span style={{ color: "#22c55e" }}>
                        ✓ {wfStats.completedRuns}/{wfStats.totalRuns}
                      </span>
                    )}
                    <span>{wf.nodes?.length || 0}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* New Workflow Modal */}
      {showNewModal && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: "rgba(0,0,0,0.7)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={() => setShowNewModal(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "#0f172a",
              border: "1px solid #334155",
              borderRadius: "10px",
              padding: "24px",
              width: "380px",
              maxWidth: "90vw",
            }}
          >
            <h3 style={{
              margin: "0 0 16px 0",
              color: "#e2e8f0",
              fontSize: "16px",
              fontWeight: 600,
            }}>
              New Workflow
            </h3>

            <div style={{ marginBottom: "12px" }}>
              <label style={{
                display: "block",
                fontSize: "12px",
                color: "#94a3b8",
                marginBottom: "4px",
              }}>
                Name *
              </label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && newName.trim()) {
                    onCreateNew(newName.trim(), newDescription.trim() || undefined);
                    setShowNewModal(false);
                    setNewName("");
                    setNewDescription("");
                  }
                }}
                placeholder="My Workflow"
                autoFocus
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: "6px",
                  color: "#e2e8f0",
                  fontSize: "13px",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>

            <div style={{ marginBottom: "20px" }}>
              <label style={{
                display: "block",
                fontSize: "12px",
                color: "#94a3b8",
                marginBottom: "4px",
              }}>
                Description
              </label>
              <textarea
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                placeholder="Optional description..."
                rows={3}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: "6px",
                  color: "#e2e8f0",
                  fontSize: "13px",
                  outline: "none",
                  resize: "vertical",
                  fontFamily: "inherit",
                  boxSizing: "border-box",
                }}
              />
            </div>

            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
              <button
                onClick={() => {
                  setShowNewModal(false);
                  setNewName("");
                  setNewDescription("");
                }}
                style={{
                  padding: "8px 16px",
                  background: "transparent",
                  border: "1px solid #334155",
                  borderRadius: "6px",
                  color: "#94a3b8",
                  fontSize: "13px",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  if (newName.trim()) {
                    onCreateNew(newName.trim(), newDescription.trim() || undefined);
                    setShowNewModal(false);
                    setNewName("");
                    setNewDescription("");
                  }
                }}
                disabled={!newName.trim()}
                style={{
                  padding: "8px 16px",
                  background: newName.trim() ? "#3b82f6" : "#1e3a5f",
                  border: "none",
                  borderRadius: "6px",
                  color: newName.trim() ? "#fff" : "#64748b",
                  fontSize: "13px",
                  cursor: newName.trim() ? "pointer" : "not-allowed",
                }}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
