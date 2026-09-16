"use client";

import { useState, useEffect, useCallback } from "react";
import { api, WorkflowRun } from "../lib/api";

interface Props {
  currentWorkflowId?: string;
  onSelectWorkflow: (workflowId: string) => void;
}

export default function WorkflowHistorySidebar({ currentWorkflowId, onSelectWorkflow }: Props) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);

  const loadRuns = useCallback(async () => {
    try {
      const result = await api.listWorkflowRuns() as { runs?: WorkflowRun[]; error?: string };
      // 后端返回 {runs: [...], count: X}，兼容直接返回数组的情况
      const data = (result?.runs || result as unknown as WorkflowRun[] || []);
      setRuns(Array.isArray(data) ? data : []);
    } catch (e) {
      console.error("Failed to load workflow runs:", e);
      setRuns([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  const formatTime = (iso: string) => {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays < 7) return `${diffDays}d ago`;
    return d.toLocaleDateString();
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "completed": return "#22c55e";
      case "passed": return "#22c55e";
      case "failed": return "#f87171";
      case "running": return "#38bdf8";
      case "cancelled": return "#f59e0b";
      default: return "#64748b";
    }
  };

  // Group runs by workflow name
  const groupedRuns = runs.reduce((acc, run) => {
    const name = run.workflowName || "Unknown Workflow";
    if (!acc[name]) acc[name] = [];
    acc[name].push(run);
    return acc;
  }, {} as Record<string, WorkflowRun[]>);

  if (loading) {
    return (
      <div style={{
        border: "1px solid #1e293b",
        borderRadius: "0.5rem",
        background: "#0a0f1a",
        overflow: "hidden",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}>
        <span style={{ color: "#64748b", fontSize: "0.75rem" }}>Loading...</span>
      </div>
    );
  }

  return (
    <div style={{
      border: "1px solid #1e293b",
      borderRadius: "0.5rem",
      background: "#0a0f1a",
      overflow: "hidden",
      height: "100%",
      display: "flex",
      flexDirection: "column",
    }}>
      {/* Header */}
      <div style={{
        padding: "0.5rem 0.75rem",
        fontSize: "0.7rem", fontWeight: 600,
        color: "#475569", letterSpacing: "0.1em",
        borderBottom: "1px solid #1e293b",
        background: "#0f172a",
        display: "flex", justifyContent: "space-between", alignItems: "center",
        flexShrink: 0,
      }}>
        <span>📊 WORKFLOW HISTORY</span>
        <button
          onClick={() => { loadRuns(); }}
          title="Refresh"
          style={{
            padding: "0.1rem 0.3rem",
            background: "transparent", border: "1px solid #334155",
            borderRadius: "0.2rem", color: "#64748b", cursor: "pointer",
            fontSize: "0.55rem",
          }}
        >
          ↻
        </button>
      </div>

      {/* Runs list */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {runs.length === 0 && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            No workflow runs yet
          </div>
        )}

        {Object.entries(groupedRuns).map(([workflowName, workflowRuns]) => (
          <div key={workflowName}>
            {/* Workflow group header */}
            <div style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.65rem",
              fontWeight: 600,
              color: "#64748b",
              background: "#0f172a",
              borderBottom: "1px solid #1e293b",
            }}>
              {workflowName} ({workflowRuns.length})
            </div>

            {/* Runs in this group */}
            {workflowRuns.slice(0, 5).map((run) => (
              <div
                key={run.id}
                onClick={() => onSelectWorkflow(run.id)}
                style={{
                  padding: "0.4rem 0.75rem",
                  borderBottom: "1px solid #1e293b",
                  cursor: "pointer",
                  background: run.id === currentWorkflowId ? "#1e3a5f" : "transparent",
                  transition: "background 0.15s",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                    <span style={{
                      width: "6px", height: "6px",
                      borderRadius: "50%",
                      background: getStatusColor(run.status),
                      flexShrink: 0,
                    }} />
                    <span style={{
                      fontSize: "0.7rem",
                      fontFamily: "monospace",
                      color: run.id === currentWorkflowId ? "#38bdf8" : "#94a3b8",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}>
                      {run.id.slice(0, 12)}...
                    </span>
                  </div>
                  <span style={{ fontSize: "0.6rem", color: "#475569" }}>
                    {formatTime(new Date(run.startedAt).toISOString())}
                  </span>
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
