"use client";

import { useState, useEffect } from "react";

type ExpType =
  | "spectroscopy"
  | "s21"
  | "iqraw"
  | "t1"
  | "xeb"
  | "ramsey"
  | "piamp"
  | "s21_dis"
  | "allxy"
  | "single_shot"
  | "pulsed_spec"
  | "swap"
  | "drag_calibrate";

type CommandType = "command" | "plot" | "analyze";

interface CollapsibleCommandProps {
  type: CommandType;
  command: string;
  onCommandChange?: (cmd: string) => void;
  onRun?: () => void;
  onSave?: () => void;
  disabled?: boolean;
  isRunning?: boolean;
  isSaving?: boolean;
  showRunButton?: boolean;
  showSaveButton?: boolean;
}

// 颜色配置
const TYPE_CONFIG: Record<
  CommandType,
  { color: string; label: string; icon: string }
> = {
  command: { color: "#22c55e", label: "COMMAND", icon: "▶" },
  plot: { color: "#3b82f6", label: "PLOT CMD", icon: "📊" },
  analyze: { color: "#f59e0b", label: "ANALYZE", icon: "📈" },
};

export default function CollapsibleCommand({
  type,
  command,
  onCommandChange,
  onRun,
  onSave,
  disabled,
  isRunning,
  isSaving,
  showRunButton = true,
  showSaveButton = true,
}: CollapsibleCommandProps) {
  const [expanded, setExpanded] = useState(false);
  const [localCommand, setLocalCommand] = useState(command);

  const config = TYPE_CONFIG[type];

  // Sync when command prop changes
  useEffect(() => {
    setLocalCommand(command);
  }, [command]);

  const handleRun = () => {
    if (disabled || !localCommand.trim()) return;
    onRun?.();
  };

  const handleSave = () => {
    if (disabled || !onSave) return;
    onSave();
  };

  return (
    <div
      style={{
        border: "1px solid #1e293b",
        borderLeft: `3px solid ${config.color}`,
        borderRadius: "0.5rem",
        background: "#0a0f1a",
        overflow: "hidden",
      }}
    >
      {/* Header bar - always visible */}
      <div
        style={{
          padding: "0.5rem 0.75rem",
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          background: "#0f172a",
          cursor: "pointer",
          userSelect: "none",
        }}
        onClick={() => setExpanded(!expanded)}
      >
        {/* Type label */}
        <span
          style={{
            fontSize: "0.65rem",
            fontWeight: 700,
            color: config.color,
            letterSpacing: "0.1em",
            minWidth: "70px",
          }}
        >
          {config.icon} {config.label}
        </span>

        {/* Command preview - only visible when collapsed */}
        {!expanded && (
          <span
            style={{
              flex: 1,
              fontFamily: "monospace",
              fontSize: "0.75rem",
              color: config.color,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {localCommand || "（未设置）"}
          </span>
        )}

        {/* Expand/collapse indicator */}
        <span
          style={{
            fontSize: "0.7rem",
            color: "#64748b",
            marginLeft: "auto",
          }}
        >
          {expanded ? "▲" : "▼"}
        </span>
      </div>

      {/* Expandable content */}
      {expanded && (
        <div style={{ padding: "0.75rem" }}>
          {/* Command editor */}
          <textarea
            value={localCommand}
            onChange={(e) => {
              setLocalCommand(e.target.value);
              onCommandChange?.(e.target.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                handleRun();
              }
            }}
            disabled={disabled}
            style={{
              width: "100%",
              minHeight: "50px",
              background: "#0f172a",
              color: config.color,
              border: "1px solid #1e293b",
              borderRadius: "0.25rem",
              padding: "0.5rem",
              fontFamily: "monospace",
              fontSize: "0.8rem",
              resize: "vertical",
              outline: "none",
              boxSizing: "border-box",
            }}
            placeholder={`Enter ${type} command...`}
          />

          {/* Hint */}
          <div
            style={{
              fontSize: "0.6rem",
              color: "#475569",
              marginTop: "0.25rem",
              marginBottom: "0.5rem",
            }}
          >
            Ctrl+Enter to run
          </div>

          {/* Buttons */}
          <div style={{ display: "flex", gap: "0.5rem" }}>
            {showRunButton && (
              <button
                onClick={handleRun}
                disabled={disabled || isRunning || !localCommand.trim()}
                style={{
                  padding: "0.4rem 1rem",
                  borderRadius: "0.25rem",
                  border: "none",
                  background:
                    disabled || isRunning || !localCommand.trim()
                      ? "#334155"
                      : config.color,
                  color:
                    disabled || isRunning || !localCommand.trim()
                      ? "#64748b"
                      : "#fff",
                  cursor:
                    disabled || isRunning || !localCommand.trim()
                      ? "not-allowed"
                      : "pointer",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                }}
              >
                {isRunning ? "⏳" : config.icon}{" "}
                {isRunning ? "Running..." : "Run"}
              </button>
            )}

            {showSaveButton && onSave && (
              <button
                onClick={handleSave}
                disabled={disabled || isSaving}
                style={{
                  padding: "0.4rem 0.75rem",
                  borderRadius: "0.25rem",
                  border: `1px solid ${config.color}`,
                  background: "transparent",
                  color: disabled || isSaving ? "#64748b" : config.color,
                  cursor: disabled || isSaving ? "not-allowed" : "pointer",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                }}
              >
                {isSaving ? "..." : "💾"} {isSaving ? "Saving..." : "Save"}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
