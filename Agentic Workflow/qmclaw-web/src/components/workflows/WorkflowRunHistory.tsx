"use client";

import { useState, useEffect } from "react";
import { api, WorkflowRun, WorkflowRunNode } from "../../lib/api";

interface Props {
  workflowId: string;
  workflowName: string;
  onBack: () => void;
}

export default function WorkflowRunHistory({ workflowId, workflowName, onBack }: Props) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState<WorkflowRun | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    loadRuns();
  }, [workflowId, workflowName]);

  async function loadRuns() {
    setLoading(true);
    try {
      // Pass both workflowId and workflowName for fallback matching
      // If workflowId doesn't match (job ID vs saved ID), workflowName will be used
      const data = await api.listWorkflowRuns(workflowId, workflowName) as WorkflowRun[];
      setRuns(data);
    } catch (err) {
      console.error("Failed to load runs:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleDeleteRun(e: React.MouseEvent, runId: string) {
    e.stopPropagation();
    if (!confirm("Delete this run record?")) return;
    setDeleting(runId);
    try {
      await api.deleteWorkflowRun(runId);
      setRuns(prev => prev.filter(r => r.id !== runId));
      if (selectedRun?.id === runId) {
        setSelectedRun(null);
      }
    } catch (err) {
      console.error("Failed to delete run:", err);
    } finally {
      setDeleting(null);
    }
  }

  function formatDuration(ms: number): string {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
  }

  function formatTime(iso: string): string {
    const d = new Date(iso);
    return d.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function getStatusColor(status: string): string {
    switch (status) {
      case "completed": return "#22c55e";
      case "passed": return "#22c55e";
      case "failed": return "#f87171";
      case "cancelled": return "#a78bfa";
      default: return "#64748b";
    }
  }

  function getNodeStatusColor(status: string): string {
    switch (status) {
      case "completed": return "#22c55e";
      case "passed": return "#22c55e";
      case "running": return "#38bdf8";
      case "failed": return "#f87171";
      case "error": return "#f87171";
      case "skipped": return "#a78bfa";
      default: return "#64748b";
    }
  }

  const nodeTypeIcons: Record<string, string> = {
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

  if (loading) {
    return (
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "200px",
        color: "#64748b",
      }}>
        Loading run history...
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px", height: "100%", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        display: "flex",
        gap: "12px",
        alignItems: "center",
        padding: "12px 16px",
        background: "#0f172a",
        borderRadius: "8px",
        border: "1px solid #1e293b",
      }}>
        <button
          onClick={onBack}
          style={{
            padding: "6px 12px",
            background: "#1e293b",
            border: "1px solid #334155",
            borderRadius: "6px",
            color: "#94a3b8",
            fontSize: "12px",
            cursor: "pointer",
          }}
        >
          ← Back
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "14px", fontWeight: 600, color: "#e2e8f0" }}>
            📊 {workflowName}
          </div>
          <div style={{ fontSize: "11px", color: "#64748b" }}>
            {runs.length} run{runs.length !== 1 ? "s" : ""} recorded
          </div>
        </div>
      </div>

      {/* Main content */}
      <div style={{
        display: "grid",
        gridTemplateColumns: selectedRun ? "300px 1fr" : "1fr",
        gap: "12px",
        flex: 1,
        minHeight: 0,
        overflow: "hidden",
      }}>
        {/* Run list */}
        <div style={{
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          overflowY: "auto",
          padding: "8px",
          background: "#0a0f1a",
          borderRadius: "8px",
          border: "1px solid #1e293b",
          // Custom scrollbar styling
          scrollbarWidth: "thin",
          scrollbarColor: "#334155 #0a0f1a",
        }}>
          {runs.length === 0 ? (
            <div style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: "8px",
              padding: "32px",
              color: "#475569",
              fontSize: "12px",
            }}>
              <span style={{ fontSize: "24px" }}>📊</span>
              <span>No runs recorded yet</span>
              <span style={{ fontSize: "10px" }}>Run this workflow to see history</span>
            </div>
          ) : (
            runs.map(run => {
              const isSelected = selectedRun?.id === run.id;
              const successCount = (run as any).nodes?.filter((n: any) => n.status === "completed" || n.status === "passed").length ?? 0;

              return (
                <div
                  key={run.id}
                  onClick={() => setSelectedRun(isSelected ? null : run)}
                  style={{
                    padding: "12px",
                    background: isSelected ? "#1e293b" : "#0f172a",
                    border: `1px solid ${isSelected ? "#38bdf8" : "#1e293b"}`,
                    borderRadius: "6px",
                    cursor: "pointer",
                    transition: "all 0.15s ease",
                  }}
                >
                  {/* Time and status */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                    <span style={{ fontSize: "12px", fontWeight: 600, color: "#e2e8f0" }}>
                      {formatTime(new Date(run.completedAt).toISOString())}
                    </span>
                    <span style={{
                      padding: "2px 8px",
                      borderRadius: "4px",
                      fontSize: "10px",
                      fontWeight: 600,
                      background: `${getStatusColor(run.status)}20`,
                      color: getStatusColor(run.status),
                    }}>
                      {run.status.toUpperCase()}
                    </span>
                  </div>

                  {/* Duration and node count */}
                  <div style={{ display: "flex", gap: "12px", fontSize: "11px", color: "#64748b", marginBottom: "8px" }}>
                    <span>⏱ {formatDuration((run as any).totalDuration)}</span>
                    <span>📦 {successCount}/{(run as any).nodes?.length ?? 0} nodes</span>
                  </div>

                  {/* Mini node status bar */}
                  <div style={{ display: "flex", gap: "2px" }}>
                    {(run as any).nodes?.slice(0, 10).map((node: any, i: number) => (
                      <div
                        key={i}
                        style={{
                          flex: 1,
                          height: "4px",
                          borderRadius: "2px",
                          background: getNodeStatusColor(node.status),
                        }}
                      />
                    ))}
                    {((run as any).nodes?.length ?? 0) > 10 && (
                      <span style={{ fontSize: "9px", color: "#64748b", marginLeft: "4px" }}>
                        +{((run as any).nodes?.length ?? 0) - 10}
                      </span>
                    )}
                  </div>

                  {/* Delete button */}
                  {isSelected && (
                    <button
                      onClick={(e) => handleDeleteRun(e, run.id)}
                      disabled={deleting === run.id}
                      style={{
                        marginTop: "8px",
                        padding: "4px 8px",
                        background: "transparent",
                        border: "1px solid #334155",
                        borderRadius: "4px",
                        color: deleting === run.id ? "#f87171" : "#64748b",
                        fontSize: "10px",
                        cursor: "pointer",
                        width: "100%",
                      }}
                    >
                      {deleting === run.id ? "Deleting..." : "🗑 Delete Run"}
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Node details panel */}
        {selectedRun && (
          <div style={{
            display: "flex",
            flexDirection: "column",
            gap: "8px",
            overflowY: "auto",
            padding: "12px",
            background: "#0a0f1a",
            borderRadius: "8px",
            border: "1px solid #1e293b",
            // Custom scrollbar styling
            scrollbarWidth: "thin",
            scrollbarColor: "#334155 #0a0f1a",
          }}>
            <div style={{ fontSize: "12px", fontWeight: 600, color: "#475569", marginBottom: "8px" }}>
              Node Details ({(selectedRun as any).nodes?.length ?? 0} nodes)
            </div>

            {(selectedRun as any).nodes?.map((node: any) => (
              <NodeDetailCard
                key={node.nodeId}
                node={node}
                nodeTypeIcons={nodeTypeIcons}
                getNodeStatusColor={getNodeStatusColor}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Node Detail Card ─────────────────────────────────────────────────────────

interface NodeDetailCardProps {
  node: any;
  nodeTypeIcons: Record<string, string>;
  getNodeStatusColor: (status: string) => string;
}

function NodeDetailCard({ node, nodeTypeIcons, getNodeStatusColor }: NodeDetailCardProps) {
  const [expanded, setExpanded] = useState(false);

  // Parse metrics from output
  const metrics = (node as any).output?.metrics || node.metrics || {};
  const hasMetrics = Object.keys(metrics).length > 0;

  return (
    <div style={{
      background: "#0f172a",
      border: "1px solid #1e293b",
      borderRadius: "6px",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          padding: "10px 12px",
          cursor: "pointer",
          background: expanded ? "#1e293b" : "transparent",
        }}
      >
        <span style={{
          fontSize: "10px",
          padding: "2px 6px",
          borderRadius: "4px",
          background: `${getNodeStatusColor(node.status)}20`,
          color: getNodeStatusColor(node.status),
          fontWeight: 600,
        }}>
          {node.status.toUpperCase()}
        </span>
        <span style={{ fontSize: "14px" }}>
          {nodeTypeIcons[(node as any).nodeType] || "📦"}
        </span>
        <span style={{ fontSize: "12px", fontWeight: 600, color: "#e2e8f0" }}>
          {node.nodeId}
        </span>
        <span style={{ fontSize: "10px", color: "#64748b" }}>
          {(node as any).nodeType}
        </span>
        {node.duration && (
          <span style={{ fontSize: "10px", color: "#64748b", marginLeft: "auto" }}>
            {node.duration}ms
          </span>
        )}
        <span style={{ fontSize: "10px", color: "#475569" }}>
          {expanded ? "▼" : "▶"}
        </span>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div style={{ padding: "12px", borderTop: "1px solid #1e293b" }}>
          {/* Metrics */}
          {hasMetrics && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "10px", color: "#475569", marginBottom: "6px" }}>METRICS</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {Object.entries(metrics).map(([key, value]) => (
                  <span
                    key={key}
                    style={{
                      padding: "2px 8px",
                      background: "#1e293b",
                      borderRadius: "4px",
                      fontSize: "10px",
                      fontFamily: "monospace",
                      color: "#94a3b8",
                    }}
                  >
                    {key}: {typeof value === "number" ? value.toFixed(4) : String(value)}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Input config */}
          {(node as any).input?.config && Object.keys((node as any).input.config).length > 0 && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "10px", color: "#475569", marginBottom: "6px" }}>INPUT CONFIG</div>
              <div style={{
                padding: "8px",
                background: "#1e293b",
                borderRadius: "4px",
                fontSize: "10px",
                fontFamily: "monospace",
                color: "#94a3b8",
                overflow: "auto",
                maxHeight: "120px",
              }}>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {JSON.stringify((node as any).input.config, null, 2)}
                </pre>
              </div>
            </div>
          )}

          {/* Resolved context */}
          {(node as any).input?.resolvedContext && Object.keys((node as any).input.resolvedContext).length > 0 && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "10px", color: "#475569", marginBottom: "6px" }}>RESOLVED VALUES</div>
              <div style={{
                padding: "8px",
                background: "#1e293b",
                borderRadius: "4px",
                fontSize: "10px",
                fontFamily: "monospace",
                color: "#38bdf8",
                overflow: "auto",
                maxHeight: "100px",
              }}>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {JSON.stringify((node as any).input.resolvedContext, null, 2)}
                </pre>
              </div>
            </div>
          )}

          {/* Output stdout */}
          {node.output?.stdout && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "10px", color: "#475569", marginBottom: "6px" }}>OUTPUT</div>
              <div style={{
                padding: "8px",
                background: "#1e293b",
                borderRadius: "4px",
                fontSize: "10px",
                fontFamily: "monospace",
                color: "#22c55e",
                overflow: "auto",
                maxHeight: "150px",
                whiteSpace: "pre-wrap",
              }}>
                {node.output.stdout}
              </div>
            </div>
          )}

          {/* Error */}
          {node.output?.error && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "10px", color: "#f87171", marginBottom: "6px" }}>ERROR</div>
              <div style={{
                padding: "8px",
                background: "#2d1e1e",
                borderRadius: "4px",
                fontSize: "10px",
                fontFamily: "monospace",
                color: "#f87171",
                overflow: "auto",
                maxHeight: "100px",
                whiteSpace: "pre-wrap",
              }}>
                {node.output.error}
              </div>
            </div>
          )}

          {/* Reasoning (for decision nodes) */}
          {node.output?.reasoning && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "10px", color: "#475569", marginBottom: "6px" }}>REASONING</div>
              <div style={{
                padding: "8px",
                background: "#1e293b",
                borderRadius: "4px",
                fontSize: "10px",
                color: "#e2e8f0",
                maxHeight: "100px",
                overflow: "auto",
              }}>
                {String(node.output.reasoning).slice(0, 500)}
                {String(node.output.reasoning).length > 500 && "..."}
              </div>
            </div>
          )}

          {/* Symptom (for decision nodes) */}
          {node.output?.symptom && (
            <div style={{ marginBottom: "12px" }}>
              <div style={{ fontSize: "10px", color: "#475569", marginBottom: "6px" }}>SYMPTOM</div>
              <div style={{
                padding: "8px",
                background: "#4c0519",
                borderRadius: "4px",
                fontSize: "10px",
                color: "#f472b6",
              }}>
                {String(node.output.symptom)}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
