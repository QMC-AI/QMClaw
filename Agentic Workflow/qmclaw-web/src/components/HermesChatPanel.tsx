"use client";

/**
 * Hermes Agent Chat Panel
 * Quantum control interface powered by Hermes-Agent
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../lib/api";

interface HermesMessage {
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  tool_calls?: Array<{
    name: string;
    arguments: Record<string, unknown>;
  }>;
  tool_results?: Array<{
    name: string;
    result: string;
    success: boolean;
  }>;
  error?: string;
  status?: "pending" | "running" | "done" | "error";
}

interface HermesModel {
  id: string;
  name: string;
  provider?: string;
}

const DEFAULT_HERMES_MODEL = "minimax/MiniMax-M2.7";

const HERMES_TOOLSETS = [
  { id: "web", label: "Web", desc: "Search and browse" },
  { id: "vision", label: "Vision", desc: "Image analysis" },
  { id: "terminal", label: "Terminal", desc: "Command execution", disabled: true },
  { id: "computer_use", label: "Computer", desc: "Desktop control", disabled: true },
];

export default function HermesChatPanel() {
  const [messages, setMessages] = useState<HermesMessage[]>([]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState(DEFAULT_HERMES_MODEL);
  const [running, setRunning] = useState(false);
  const [sessionId] = useState(() => `hermes_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`);
  const [enabledToolsets, setEnabledToolsets] = useState<string[]>(["web"]);
  const [models, setModels] = useState<HermesModel[]>([]);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Fetch available models
  useEffect(() => {
    api.hermesGetModels()
      .then((res: any) => setModels(res.models || []))
      .catch(() => {
        // Fallback to hardcoded models
        setModels([
          { id: "MiniMax-M2.7", name: "MiniMax M2.7", provider: "minimax" },
          { id: "claude-3-5-sonnet-20241022", name: "Claude 3.5 Sonnet", provider: "anthropic" },
          { id: "deepseek-chat", name: "DeepSeek Chat", provider: "deepseek" },
        ]);
      });
  }, []);

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const toggleToolset = (toolset: string) => {
    setEnabledToolsets(prev =>
      prev.includes(toolset)
        ? prev.filter(t => t !== toolset)
        : [...prev, toolset]
    );
  };

  const handleSend = useCallback(async () => {
    if (!input.trim() || running) return;
    const userMsg = input.trim();
    setInput("");
    setError(null);

    // Add user message
    setMessages(prev => [...prev, { role: "user", content: userMsg }]);

    // Add placeholder assistant message
    setMessages(prev => [...prev, {
      role: "assistant",
      content: "⏳ Initializing Hermes agent...",
      status: "pending",
    }]);

    setRunning(true);
    const msgIndex = messages.length + 1; // Index of assistant message

    try {
      // Use streaming API
      let accumulatedContent = "";
      let currentThinking = "";
      let finalResult: any = null;

      for await (const event of api.hermesChatStream(userMsg, model, sessionId)) {
        const { type, data } = event;

        if (type === "status") {
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: data.message || "Processing...",
                status: "running",
              };
            }
            return newMessages;
          });
        } else if (type === "thinking") {
          currentThinking = data.content || "";
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                thinking: currentThinking,
              };
            }
            return newMessages;
          });
        } else if (type === "response") {
          accumulatedContent += data.content || "";
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: accumulatedContent,
                status: "running",
              };
            }
            return newMessages;
          });
        } else if (type === "tool_call") {
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              const tool_calls = [...(newMessages[msgIndex].tool_calls || []), {
                name: data.tool,
                arguments: data.args || {},
              }];
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                tool_calls,
              };
            }
            return newMessages;
          });
        } else if (type === "done") {
          finalResult = data;
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: data.final_response || data.response || accumulatedContent || "Done",
                status: "done",
                tool_results: data.tool_results,
              };
            }
            return newMessages;
          });
        } else if (type === "error") {
          setError(data.error || "Unknown error");
          setMessages(prev => {
            const newMessages = [...prev];
            if (newMessages[msgIndex]) {
              newMessages[msgIndex] = {
                ...newMessages[msgIndex],
                content: `❌ Error: ${data.error || "Unknown error"}`,
                status: "error",
                error: data.error,
              };
            }
            return newMessages;
          });
        }
      }

      // If we didn't get a done event
      if (!finalResult && !error) {
        setMessages(prev => {
          const newMessages = [...prev];
          if (newMessages[msgIndex]) {
            newMessages[msgIndex] = {
              ...newMessages[msgIndex],
              content: accumulatedContent || "Completed",
              status: "done",
            };
          }
          return newMessages;
        });
      }

    } catch (err: any) {
      setError(err.message);
      setMessages(prev => {
        const newMessages = [...prev];
        if (newMessages[msgIndex]) {
          newMessages[msgIndex] = {
            ...newMessages[msgIndex],
            content: `❌ Error: ${err.message}`,
            status: "error",
            error: err.message,
          };
        }
        return newMessages;
      });
    } finally {
      setRunning(false);
    }
  }, [input, running, model, sessionId, messages.length, error]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div style={{
      display: "flex",
      flexDirection: "column",
      height: "100%",
      background: "#0f172a",
      color: "#e2e8f0",
    }}>
      {/* Header with model selection and toolsets */}
      <div style={{
        padding: "0.75rem 1rem",
        borderBottom: "1px solid #1e293b",
        display: "flex",
        flexWrap: "wrap",
        gap: "0.75rem",
        alignItems: "center",
      }}>
        {/* Model selector */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ fontSize: "0.7rem", color: "#64748b" }}>Model:</span>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={running}
            style={{
              background: "#1e293b",
              color: "#e2e8f0",
              border: "1px solid #334569",
              borderRadius: "0.375rem",
              padding: "0.25rem 0.5rem",
              fontSize: "0.75rem",
            }}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
          </select>
        </div>

        {/* Toolset toggles */}
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          {HERMES_TOOLSETS.map((toolset) => (
            <button
              key={toolset.id}
              onClick={() => toggleToolset(toolset.id)}
              disabled={running || toolset.disabled}
              style={{
                padding: "0.25rem 0.5rem",
                fontSize: "0.7rem",
                borderRadius: "0.375rem",
                border: "1px solid",
                cursor: toolset.disabled ? "not-allowed" : "pointer",
                opacity: toolset.disabled ? 0.5 : enabledToolsets.includes(toolset.id) ? 1 : 0.6,
                borderColor: enabledToolsets.includes(toolset.id) ? "#22c55e" : "#334569",
                background: enabledToolsets.includes(toolset.id) ? "#052e16" : "#1e293b",
                color: enabledToolsets.includes(toolset.id) ? "#22c55e" : "#94a3b8",
              }}
            >
              {toolset.label}
            </button>
          ))}
        </div>

        {/* Session info */}
        <div style={{ marginLeft: "auto", fontSize: "0.65rem", color: "#475569" }}>
          Session: {sessionId.slice(0, 16)}...
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div style={{
          padding: "0.5rem 1rem",
          background: "#450a0a",
          color: "#fca5a5",
          fontSize: "0.75rem",
          borderBottom: "1px solid #7f1d1d",
        }}>
          ❌ {error}
        </div>
      )}

      {/* Messages area */}
      <div style={{
        flex: 1,
        overflow: "auto",
        padding: "1rem",
      }}>
        {messages.length === 0 && (
          <div style={{
            textAlign: "center",
            color: "#475569",
            padding: "2rem",
            fontSize: "0.85rem",
          }}>
            <div style={{ fontSize: "2rem", marginBottom: "0.5rem" }}>🤖</div>
            <div>Hermes Agent ready</div>
            <div style={{ fontSize: "0.75rem", marginTop: "0.5rem" }}>
              Ask questions about quantum experiments, qubit calibration, or general tasks.
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} style={{ marginBottom: "1rem" }}>
            {/* User message */}
            {msg.role === "user" && (
              <div style={{
                background: "#1e3a5f",
                borderRadius: "0.75rem",
                padding: "0.75rem 1rem",
                maxWidth: "85%",
                marginLeft: "auto",
                fontSize: "0.85rem",
                lineHeight: 1.5,
              }}>
                {msg.content}
              </div>
            )}

            {/* Assistant message */}
            {msg.role === "assistant" && (
              <div style={{
                background: "#1e293b",
                borderRadius: "0.75rem",
                padding: "0.75rem 1rem",
                maxWidth: "85%",
                fontSize: "0.85rem",
                lineHeight: 1.5,
              }}>
                {/* Thinking block */}
                {msg.thinking && (
                  <div style={{
                    background: "#0f172a",
                    border: "1px solid #334569",
                    borderRadius: "0.5rem",
                    padding: "0.5rem 0.75rem",
                    marginBottom: "0.75rem",
                    fontSize: "0.75rem",
                    color: "#94a3b8",
                  }}>
                    <div style={{ color: "#64748b", marginBottom: "0.25rem" }}>💭 Thinking:</div>
                    <div style={{ fontFamily: "monospace" }}>{msg.thinking}</div>
                  </div>
                )}

                {/* Content */}
                <div style={{ whiteSpace: "pre-wrap" }}>{msg.content}</div>

                {/* Tool calls */}
                {msg.tool_calls && msg.tool_calls.length > 0 && (
                  <div style={{ marginTop: "0.75rem" }}>
                    <div style={{ color: "#64748b", fontSize: "0.7rem", marginBottom: "0.25rem" }}>
                      🔧 Tool calls:
                    </div>
                    {msg.tool_calls.map((tc, j) => (
                      <div key={j} style={{
                        background: "#0f172a",
                        borderRadius: "0.375rem",
                        padding: "0.375rem 0.5rem",
                        marginTop: "0.25rem",
                        fontSize: "0.75rem",
                        fontFamily: "monospace",
                      }}>
                        <span style={{ color: "#22c55e" }}>{tc.name}</span>
                        {Object.keys(tc.arguments).length > 0 && (
                          <span style={{ color: "#64748b" }}>
                            {" "}({JSON.stringify(tc.arguments).slice(0, 100)}
                            {JSON.stringify(tc.arguments).length > 100 ? "..." : ""})
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {/* Status indicator */}
                {msg.status === "pending" && (
                  <div style={{ color: "#64748b", fontSize: "0.75rem", marginTop: "0.5rem" }}>
                    ⏳ Starting...
                  </div>
                )}
                {msg.status === "running" && (
                  <div style={{ color: "#38bdf8", fontSize: "0.75rem", marginTop: "0.5rem" }}>
                    ⚡ Running...
                  </div>
                )}
                {msg.status === "error" && (
                  <div style={{ color: "#ef4444", fontSize: "0.75rem", marginTop: "0.5rem" }}>
                    ❌ Error
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div style={{
        padding: "0.75rem 1rem",
        borderTop: "1px solid #1e293b",
        display: "flex",
        gap: "0.5rem",
      }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about quantum experiments, qubit calibration..."
          disabled={running}
          style={{
            flex: 1,
            background: "#1e293b",
            border: "1px solid #334569",
            borderRadius: "0.5rem",
            padding: "0.5rem 0.75rem",
            color: "#e2e8f0",
            fontSize: "0.85rem",
            resize: "none",
            minHeight: "2.5rem",
            maxHeight: "6rem",
            fontFamily: "inherit",
          }}
          rows={1}
        />
        <button
          onClick={handleSend}
          disabled={running || !input.trim()}
          style={{
            padding: "0.5rem 1rem",
            background: running ? "#334569" : "#3b82f6",
            color: "#fff",
            border: "none",
            borderRadius: "0.5rem",
            cursor: running ? "not-allowed" : "pointer",
            fontSize: "0.85rem",
            fontWeight: 500,
          }}
        >
          {running ? "..." : "Send"}
        </button>
      </div>
    </div>
  );
}
