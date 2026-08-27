"use client";

import { useState, useEffect, useRef } from "react";
import { api, JobResult } from "../lib/api";

interface JobEntry extends JobResult {
  id: string;
}

interface JobManagerProps {
  currentJobId: string | null;
  onJobSelect?: (jobId: string) => void;
}

function timeAgo(ms: number): string {
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "#94a3b8",
    running: "#38bdf8",
    completed: "#22c55e",
    failed: "#f87171",
    cancelled: "#f59e0b",
  };
  return (
    <span style={{
      display: "inline-block", width: "6px", height: "6px",
      borderRadius: "50%", background: colors[status] || "#94a3b8",
      marginRight: "0.4rem", flexShrink: 0,
    }} />
  );
}

function JobDetailPanel({ job }: { job: JobEntry }) {
  const hasPlot = job.plotPath && job.status === "completed";
  return (
    <div style={{
      borderTop: "1px solid #1e293b",
      background: "#0f172a",
      maxHeight: "320px",
      overflow: "auto",
    }}>
      {/* Job detail header */}
      <div style={{
        padding: "0.5rem 0.75rem",
        fontSize: "0.65rem", fontWeight: 600,
        color: "#475569", letterSpacing: "0.1em",
        borderBottom: "1px solid #1e293b",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span>JOB DETAIL — {job.id.slice(0, 16)}...</span>
        <span style={{ color: "#64748b" }}>{job.status}</span>
      </div>
      <div style={{ display: "flex", minHeight: 0 }}>
        {/* Left: stdout */}
        <div style={{ flex: 1, padding: "0.5rem 0.75rem", minWidth: 0, overflow: "auto" }}>
         <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.25rem" }}>STDOUT</div>
          <pre style={{
            fontFamily: "monospace", fontSize: "0.65rem",
            color: "#94a3b8", whiteSpace: "pre-wrap", wordBreak: "break-all",
            margin: 0, maxHeight: "200px", overflow: "auto",
          }}>
            {job.stdout || "(no output)"}
          </pre>
          {job.error && (
            <>
              <div style={{ fontSize: "0.65rem", color: "#f87171", marginTop: "0.5rem", marginBottom: "0.25rem" }}>ERROR</div>
              <pre style={{
                fontFamily: "monospace", fontSize: "0.6rem",
                color: "#f87171", whiteSpace: "pre-wrap", wordBreak: "break-all",
                margin: 0, maxHeight: "80px", overflow: "auto",
              }}>
                {job.error}
              </pre>
            </>
          )}
        </div>
        {/* Right: plot */}
        {hasPlot && (
          <div style={{
            width: "280px", flexShrink: 0,
            borderLeft: "1px solid #1e293b",
            padding: "0.5rem",
          }}>
            <div style={{ fontSize: "0.65rem", color: "#475569", marginBottom: "0.25rem" }}>PLOT</div>
            <img
              src={api.plotUrl(job.id)}
              alt="Job plot"
              style={{ display: "block", width: "100%", maxHeight: "240px", objectFit: "contain", borderRadius: "0.25rem" }}
              onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export default function JobManager({ currentJobId, onJobSelect }: JobManagerProps) {
  const [jobs, setJobs] = useState<JobEntry[]>([]);
  const [runningIds, setRunningIds] = useState<Set<string>>(new Set());
  const [expandedJob, setExpandedJob] = useState<string | null>(null);

  // Refresh job list every 1s while jobs are running
  const isAnyRunning = jobs.some((j) => j.status === "pending" || j.status === "running");

  useEffect(() => {
    const fetchJobs = async () => {
      try {
        const list = await api.listJobs();
        setJobs(list);
        setRunningIds(new Set(list.filter((j) => j.status === "pending" || j.status === "running").map((j) => j.id)));
      } catch { /* ignore */ }
    };
    fetchJobs();
    if (isAnyRunning) {
      const interval = setInterval(fetchJobs, 1000);
      return () => clearInterval(interval);
    }
  }, [isAnyRunning]);

  const handleCancel = async (jobId: string) => {
    try {
      await api.cancelJob(jobId);
      setJobs((prev) =>
        prev.map((j) => j.id === jobId ? { ...j, status: "cancelled" as const, completedAt: Date.now() } : j)
      );
    } catch (e: any) {
      console.error("Cancel failed:", e);
    }
  };

  const handleToggleExpand = (jobId: string) => {
    setExpandedJob((prev) => (prev === jobId ? null : jobId));
    onJobSelect?.(jobId);
  };

  const getElapsed = (job: JobEntry): string => {
    if (!job.submittedAt) return "";
    const end = job.completedAt || Date.now();
    const ms = end - job.submittedAt;
    if (ms < 1000) return "<1s";
    if (ms < 60000) return `${Math.floor(ms / 1000)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  };

  return (
    <div style={{
      border: "1px solid #1e293b",
      borderRadius: "0.5rem",
      background: "#0a0f1a",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        padding: "0.5rem 0.75rem",
        fontSize: "0.7rem", fontWeight: 600,
        color: "#475569", letterSpacing: "0.1em",
        borderBottom: "1px solid #1e293b",
        background: "#0f172a",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span>🔧 JOB MANAGER</span>
        <span style={{ color: "#38bdf8", cursor: "pointer", fontWeight: 400 }} onClick={() => api.listJobs().then(setJobs)}>
          ↻ {jobs.length} job{jobs.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Job list */}
      <div style={{ maxHeight: expandedJob ? "160px" : "300px", overflow: "auto" }}>
        {jobs.length === 0 && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            No jobs yet
          </div>
        )}
        {jobs.map((job) => (
          <div key={job.id}>
            <div
              onClick={() => handleToggleExpand(job.id)}
              style={{
                padding: "0.5rem 0.75rem",
                borderBottom: "1px solid #1e293b",
                cursor: "pointer",
                background: job.id === currentJobId ? "#1e3a5f" : "transparent",
                transition: "background 0.15s",
              }}
            >
              {/* Job row header */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", alignItems: "center", minWidth: 0, flex: 1 }}>
                  <StatusDot status={job.status} />
                  <span style={{
                    fontFamily: "monospace", fontSize: "0.7rem",
                    color: job.id === currentJobId ? "#38bdf8" : "#94a3b8",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>
                    {job.id.slice(0, 20)}...
                  </span>
                  {job.plotPath && job.status === "completed" && (
                    <span style={{ fontSize: "0.6rem", marginLeft: "0.4rem", flexShrink: 0 }}>📊</span>
                  )}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexShrink: 0 }}>
                  {job.submittedAt && (
                    <span style={{ fontSize: "0.65rem", color: "#475569" }}>
                      {job.status === "running" || job.status === "pending" ? `${getElapsed(job)}` : timeAgo(job.submittedAt)}
                    </span>
                  )}
                  {(job.status === "running" || job.status === "pending") && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleCancel(job.id); }}
                      title="Cancel job"
                      style={{
                        padding: "0.15rem 0.4rem",
                        fontSize: "0.6rem",
                        borderRadius: "0.2rem",
                        border: "1px solid #f87171",
                        background: "transparent",
                        color: "#f87171",
                        cursor: "pointer",
                      }}
                    >
                      ✕
                    </button>
                  )}
                 <span style={{ fontSize: "0.55rem", color: "#334569" }}>
                    {expandedJob === job.id ? "▲" : "▼"}
                  </span>
                </div>
              </div>

              {/* Progress bar for running/pending jobs */}
              {(job.status === "running" || job.status === "pending") && (
                <div style={{
                  height: "2px", background: "#1e293b",
                  marginTop: "0.4rem", borderRadius: "1px", overflow: "hidden",
                }}>
                  <div style={{
                    height: "100%",
                    width: job.status === "pending" ? "30%" : "70%",
                    background: "#38bdf8",
                    animation: job.status === "running" ? "pulse 1.5s ease-in-out infinite" : "none",
                  }} />
                </div>
              )}

              {/* Error message for failed/cancelled */}
              {job.status === "failed" && job.error && (
                <div style={{ fontSize: "0.65rem", color: "#f87171", marginTop: "0.2rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {job.error.split("\n").pop()?.slice(0, 60)}
                </div>
              )}
              {job.status === "cancelled" && (
                <div style={{ fontSize: "0.65rem", color: "#f59e0b", marginTop: "0.2rem" }}>
                  Cancelled
                </div>
              )}
            </div>

            {/* Expanded detail panel */}
            {expandedJob === job.id && (
              <JobDetailPanel job={job} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}