"use client";

import { useState, useEffect } from "react";
import { api } from "../lib/api";

type ExpType = "spectroscopy" | "s21" | "iqraw" | "t1" | "xeb" | "ramsey" | "piamp" | "s21_dis" | "allxy" | "single_shot" | "pulsed_spec" | "swap" | "drag_calibrate";

interface PlotCommandEditorProps {
  expType: ExpType;
  initialPlotCommand?: string;  // Loaded from config
  onPlotCommandChange?: (command: string) => void;
  onPlot?: (command: string) => void;
  plotDisabled?: boolean;
  onSaveToConfig?: (expType: string, plotCommand: string) => Promise<void>;  // Callback to save to config
}

export default function PlotCommandEditor({
  expType,
  initialPlotCommand,
  onPlotCommandChange,
  onPlot,
  plotDisabled,
  onSaveToConfig,
}: PlotCommandEditorProps) {
  const [expanded, setExpanded] = useState(false);
  const [currentCommand, setCurrentCommand] = useState(initialPlotCommand || "");
  const [saving, setSaving] = useState(false);
  const [showSaved, setShowSaved] = useState(false);

  // Sync when initialPlotCommand or expType changes
  useEffect(() => {
    setCurrentCommand(initialPlotCommand || "");
  }, [initialPlotCommand, expType]);

  // Notify parent of current plot command
  useEffect(() => {
    onPlotCommandChange?.(currentCommand);
  }, [currentCommand, onPlotCommandChange]);

  const handleSave = async () => {
    if (!onSaveToConfig) return;
    setSaving(true);
    try {
      await onSaveToConfig(expType, currentCommand);
      setShowSaved(true);
      setTimeout(() => setShowSaved(false), 2000);
    } catch (e) {
      console.error("Failed to save plot command:", e);
    } finally {
      setSaving(false);
    }
  };

  const handlePlot = () => {
    onPlot?.(currentCommand);
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
        <span>📝 PLOT COMMAND</span>
        <span style={{ color: "#334155", fontSize: "0.9rem" }}>{expanded ? "▲" : "▼"}</span>
      </div>

      {/* Expandable content */}
      {expanded && (
        <div style={{ padding: "0.75rem" }}>
          <textarea
            value={currentCommand}
            onChange={(e) => setCurrentCommand(e.target.value)}
            rows={8}
            style={{
              width: "100%",
              background: "#1e293b",
              border: "1px solid #334155",
              color: "#22d3ee",
              padding: "0.5rem",
              borderRadius: "0.25rem",
              fontFamily: "monospace",
              fontSize: "0.75rem",
              resize: "vertical",
            }}
            placeholder="Enter matplotlib commands (e.g., plt.title, plt.xlabel, etc.)"
          />
          <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem", justifyContent: "flex-end" }}>
            {showSaved && (
              <span style={{ color: "#22c55e", fontSize: "0.75rem", alignSelf: "center" }}>
                ✅ Saved!
              </span>
            )}
            <button
              onClick={handleSave}
              disabled={saving}
              style={{
                padding: "0.3rem 0.75rem",
                borderRadius: "0.25rem",
                border: "1px solid #475569",
                background: "transparent",
                color: saving ? "#64748b" : "#94a3b8",
                cursor: saving ? "not-allowed" : "pointer",
                fontSize: "0.7rem",
              }}
            >
              {saving ? "Saving..." : "💾 Save to Config"}
            </button>
            <button
              onClick={handlePlot}
              disabled={plotDisabled}
              style={{
                padding: "0.3rem 0.75rem",
                borderRadius: "0.25rem",
                border: "none",
                background: plotDisabled ? "#334155" : "#3b82f6",
                color: plotDisabled ? "#64748b" : "#fff",
                cursor: plotDisabled ? "not-allowed" : "pointer",
                fontSize: "0.7rem",
                fontWeight: 600,
              }}
            >
              📊 Plot
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
