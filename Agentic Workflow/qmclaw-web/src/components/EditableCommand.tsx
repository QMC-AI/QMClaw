"use client";

import { useState, useEffect } from "react";

type ExpType = "spectroscopy" | "s21" | "iqraw" | "t1" | "xeb" | "ramsey" | "piamp" | "s21_dis" | "allxy" | "single_shot" | "pulsed_spec" | "swap" | "drag_calibrate";

interface EditableCommandProps {
  qubit: string;
  expType: ExpType;
  onRun: (command: string) => void;
  disabled?: boolean;
}

const SQ_MAP: Record<ExpType, string> = {
  spectroscopy: "sq.spectroscopy",
  s21: "sq.s21",
  iqraw: "sq.iqraw",
  t1: "sq.t1",
  ramsey: "sq.ramsey_df",
  piamp: "sq.piamp",
  xeb: "sq.xeb",
  s21_dis: "sq.s21_dis",
  allxy: "sq.allxy",
  single_shot: "sq.single_shot",
  pulsed_spec: "sq.pulsed_spec",
  swap: "sq.swap",
  drag_calibrate: "sq.drag_calibrate",
};

function buildCommand(qubit: string, expType: ExpType): string {
  const fn = SQ_MAP[expType] || expType;
  return fn + "(" + qubit + ", do_plot=True)";
}

export default function EditableCommand({ qubit, expType, onRun, disabled }: EditableCommandProps) {
  const [expanded, setExpanded] = useState(false);
  const [command, setCommand] = useState(() => buildCommand(qubit, expType));

  // Sync when qubit or expType changes (unless manually edited while expanded)
  useEffect(() => {
    if (!expanded) setCommand(buildCommand(qubit, expType));
  }, [qubit, expType, expanded]);

  const handleRun = () => {
    if (!command.trim()) return;
    onRun(command.trim());
  };

  return (
    <div style={{
      border: "1px solid #1e293b",
      borderRadius: "0.5rem",
      background: "#0a0f1a",
      overflow: "hidden",
    }}>
      {/* Header bar */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          padding: "0.5rem 0.75rem",
          fontSize: "0.7rem", fontWeight: 600,
          color: "#475569", letterSpacing: "0.1em",
          borderBottom: expanded ? "1px solid #1e293b" : "none",
          background: "#0f172a",
          cursor: "pointer",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}
      >
        <span>▶ COMMAND</span>
        <span style={{ color: "#38bdf8", fontWeight: 400 }}>{expanded ? "▲ collapse" : "▼ expand"}</span>
      </div>

      {/* Code display / editor */}
      {expanded ? (
        <textarea
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleRun(); }}
          style={{
            width: "100%", minHeight: "60px",
            background: "#0f172a", color: "#22c55e",
            border: "none", outline: "none",
            fontFamily: "monospace", fontSize: "0.8rem",
            padding: "0.5rem 0.75rem",
            resize: "vertical", boxSizing: "border-box",
          }}
          placeholder="sq.iqraw(q3ld4, do_plot=True)"
        />
      ) : (
        <div
          onClick={() => setExpanded(true)}
          style={{
            padding: "0.5rem 0.75rem",
            fontFamily: "monospace", fontSize: "0.8rem",
            color: "#22c55e", cursor: "text",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}
        >
          {buildCommand(qubit, expType)}
        </div>
      )}

      {/* Run button row */}
      {expanded ? (
        <div style={{ padding: "0.4rem 0.75rem", borderTop: "1px solid #1e293b", display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            onClick={handleRun}
            disabled={disabled || !command.trim()}
            style={{
              padding: "0.4rem 1.5rem",
              borderRadius: "0.375rem",
              border: "none",
              background: disabled ? "#334155" : "#22c55e",
              color: disabled ? "#64748b" : "#fff",
              cursor: disabled ? "not-allowed" : "pointer",
              fontSize: "0.8rem", fontWeight: 600,
            }}
          >
            ▶ Run
          </button>
          <span style={{ fontSize: "0.65rem", color: "#475569" }}>Ctrl+Enter to run</span>
        </div>
      ) : null}
    </div>
  );
}