"use client";

import { useState, useCallback, useEffect } from "react";
import { api } from "../lib/api";

interface AnalysisCommandEditorProps {
  expType: string;
  initialCommand: string;
  metricsToExtract: string[];
  onCommandChange?: (command: string) => void;
  onMetricsChange?: (metrics: string[]) => void;
  onSaveToConfig?: (command: string, metrics: string[]) => Promise<void>;
  disabled?: boolean;
  onLog?: (msg: string, isError?: boolean) => void;
}

export default function AnalysisCommandEditor({
  expType,
  initialCommand,
  metricsToExtract,
  onCommandChange,
  onMetricsChange,
  onSaveToConfig,
  disabled = false,
  onLog,
}: AnalysisCommandEditorProps) {
  const [command, setCommand] = useState(initialCommand);
  const [metrics, setMetrics] = useState<string[]>(metricsToExtract);
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<{
    success: boolean;
    stdout?: string;
    metrics?: Record<string, number>;
    error?: string;
  } | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  // Sync command and metrics when experiment type changes (props update)
  useEffect(() => {
    setCommand(initialCommand);
    setMetrics(metricsToExtract);
    setResult(null);
  }, [expType, initialCommand, metricsToExtract]);

  const handleCommandChange = (value: string) => {
    setCommand(value);
    onCommandChange?.(value);
    setResult(null);
  };

  const handleMetricsChange = (value: string) => {
    const newMetrics = value.split(",").map(m => m.trim()).filter(m => m);
    setMetrics(newMetrics);
    onMetricsChange?.(newMetrics);
  };

  const handleRun = async () => {
    if (!command.trim() || isRunning) return;

    setIsRunning(true);
    setResult(null);
    onLog?.(`▶ Running analysis for ${expType}...`);

    try {
      const res = await api.runAnalysis(command, expType);

      if (res.success) {
        setResult(res);
        onLog?.(`✅ Analysis completed`);

        if (res.metrics && Object.keys(res.metrics).length > 0) {
          const metricsStr = Object.entries(res.metrics)
            .map(([k, v]) => `${k}=${typeof v === 'number' ? v.toFixed(4) : v}`)
            .join(", ");
          onLog?.(`📊 Metrics: ${metricsStr}`);
        }
      } else {
        setResult({ success: false, error: res.error || "Unknown error" });
        onLog?.(`❌ Analysis failed: ${res.error}`, true);
      }
    } catch (e: any) {
      setResult({ success: false, error: e.message });
      onLog?.(`❌ ${e.message}`, true);
    } finally {
      setIsRunning(false);
    }
  };

  const handleSave = async () => {
    if (!onSaveToConfig) return;

    setIsSaving(true);
    try {
      await onSaveToConfig(command, metrics);
      onLog?.(`✅ Saved analysis config for ${expType}`);
    } catch (e: any) {
      onLog?.(`❌ Save failed: ${e.message}`, true);
    } finally {
      setIsSaving(false);
    }
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
        fontSize: "0.7rem",
        fontWeight: 600,
        color: "#475569",
        letterSpacing: "0.1em",
        borderBottom: "1px solid #1e293b",
        background: "#0f172a",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}>
        <span>📊 ANALYZE COMMAND ({expType})</span>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={handleSave}
            disabled={isSaving || disabled}
            style={{
              padding: "0.2rem 0.5rem",
              background: isSaving ? "#334155" : "#3b82f6",
              border: "none",
              borderRadius: "0.25rem",
              color: isSaving ? "#64748b" : "#fff",
              cursor: isSaving ? "not-allowed" : "pointer",
              fontSize: "0.65rem",
              fontWeight: 600,
            }}
          >
            {isSaving ? "..." : "💾 Save"}
          </button>
          <button
            onClick={handleRun}
            disabled={isRunning || disabled || !command.trim()}
            style={{
              padding: "0.2rem 0.5rem",
              background: isRunning ? "#334155" : "#22c55e",
              border: "none",
              borderRadius: "0.25rem",
              color: isRunning ? "#64748b" : "#fff",
              cursor: isRunning ? "not-allowed" : "pointer",
              fontSize: "0.65rem",
              fontWeight: 600,
            }}
          >
            {isRunning ? "⏳" : "▶"} Run
          </button>
        </div>
      </div>

      {/* Content */}
      <div style={{ padding: "0.75rem" }}>
        {/* Analysis Command Input */}
        <div style={{ marginBottom: "0.75rem" }}>
          <label style={{
            display: "block",
            fontSize: "0.7rem",
            color: "#94a3b8",
            marginBottom: "0.25rem",
          }}>
            Python Command
          </label>
          <textarea
            value={command}
            onChange={(e) => handleCommandChange(e.target.value)}
            rows={4}
            placeholder={`e.g., qter.fitData(-1, collect=True, do_plot=False)\ndp.T1(data)\ndp.Ramsey(data)`}
            disabled={disabled}
            style={{
              width: "100%",
              padding: "0.5rem",
              background: "#0f172a",
              border: "1px solid #334155",
              borderRadius: "0.25rem",
              color: "#22c55e",
              fontFamily: "monospace",
              fontSize: "0.75rem",
              resize: "vertical",
              boxSizing: "border-box",
            }}
          />
          <div style={{
            fontSize: "0.65rem",
            color: "#64748b",
            marginTop: "0.25rem",
          }}>
            Available: data, qter, dp, plt, np
          </div>
        </div>

        {/* Metrics to Extract */}
        <div style={{ marginBottom: "0.75rem" }}>
          <label style={{
            display: "block",
            fontSize: "0.7rem",
            color: "#94a3b8",
            marginBottom: "0.25rem",
          }}>
            Metrics to Extract (comma-separated)
          </label>
          <input
            type="text"
            value={metrics.join(", ")}
            onChange={(e) => handleMetricsChange(e.target.value)}
            placeholder="SNR, F0, F1, separation, T1, T2"
            disabled={disabled}
            style={{
              width: "100%",
              padding: "0.4rem 0.5rem",
              background: "#0f172a",
              border: "1px solid #334155",
              borderRadius: "0.25rem",
              color: "#22c55e",
              fontFamily: "monospace",
              fontSize: "0.75rem",
              boxSizing: "border-box",
            }}
          />
        </div>

        {/* Result Display */}
        {result && (
          <div style={{
            border: `1px solid ${result.success ? "#22c55e" : "#ef4444"}`,
            borderRadius: "0.25rem",
            background: result.success ? "#0f1a0f" : "#1a0f0f",
            padding: "0.5rem",
            marginTop: "0.5rem",
          }}>
            <div style={{
              fontSize: "0.7rem",
              fontWeight: 600,
              color: result.success ? "#22c55e" : "#ef4444",
              marginBottom: "0.25rem",
            }}>
              {result.success ? "✅ Success" : "❌ Error"}
            </div>

            {result.success && result.metrics && Object.keys(result.metrics).length > 0 && (
              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
                gap: "0.5rem",
                marginBottom: "0.5rem",
              }}>
                {Object.entries(result.metrics).map(([key, value]) => (
                  <div key={key} style={{
                    padding: "0.25rem 0.5rem",
                    background: "#0f172a",
                    borderRadius: "0.25rem",
                  }}>
                    <div style={{ fontSize: "0.65rem", color: "#64748b" }}>{key}</div>
                    <div style={{ fontSize: "0.8rem", color: "#22c55e", fontFamily: "monospace" }}>
                      {typeof value === 'number' ? value.toFixed(4) : String(value)}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {result.stdout && (
              <details>
                <summary style={{
                  fontSize: "0.65rem",
                  color: "#64748b",
                  cursor: "pointer",
                  marginBottom: "0.25rem",
                }}>
                  Output ({result.stdout.length} chars)
                </summary>
                <pre style={{
                  fontSize: "0.7rem",
                  color: "#22c55e",
                  fontFamily: "monospace",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  maxHeight: "100px",
                  overflow: "auto",
                  background: "#0a0f1a",
                  padding: "0.5rem",
                  borderRadius: "0.25rem",
                }}>
                  {result.stdout}
                </pre>
              </details>
            )}

            {result.error && (
              <div style={{
                fontSize: "0.7rem",
                color: "#ef4444",
                fontFamily: "monospace",
              }}>
                {result.error}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
