"use client";

/**
 * Quantum Control Agent Chat Panel
 * Natural language interface with progress updates
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../lib/api";
import { useModelStore } from "../store/modelStore";

interface AgentStep {
  thought?: string;
  tool: string;
  input: Record<string, unknown>;
  observation?: string;
  reflection?: string;
  retried?: boolean;
}

interface ChatMessage {
  role: "user" | "agent";
  content: string;
  steps?: AgentStep[];
  results?: Record<string, number>;
  charts?: string[];
  reflectionReport?: string;
  memoryContext?: {
    relevantSkills?: any[];
    recentEpisodes?: any[];
  };
  error?: string;
  status?: "pending" | "running" | "done" | "error";
  progress?: string;  // Current progress description
}

const AGENT_MODES = [
  { value: "react", label: "ReAct", desc: "Think-Act-Observe 循环" },
  { value: "plan_and_execute", label: "Plan & Execute", desc: "先规划后执行" },
  { value: "reflexion", label: "Reflexion", desc: "执行后反思纠错" },
];

export default function AgentChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("react");
  const [running, setRunning] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const models = useModelStore((s) => s.models);
  const fetchModels = useModelStore((s) => s.fetchModels);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  const textModels = models.filter((m) => m.enabled && m.capabilities.includes("text"));

  useEffect(() => {
    if (textModels.length > 0 && !selectedModel) {
      setSelectedModel(textModels[0].id);
    }
  }, [textModels, selectedModel]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const updateMessage = useCallback((index: number, update: Partial<ChatMessage>) => {
    setMessages(prev => {
      const newMessages = [...prev];
      if (newMessages[index]) {
        newMessages[index] = { ...newMessages[index], ...update };
      }
      return newMessages;
    });
  }, []);

  const handleSend = useCallback(async () => {
    if (!input.trim() || running) return;
    const userMsg = input.trim();
    setInput("");

    // Add user message
    setMessages(prev => [...prev, { role: "user", content: userMsg }]);

    // Add placeholder agent message
    setMessages(prev => [
      ...prev,
      {
        role: "agent",
        content: "⏳ 正在启动任务...",
        status: "pending",
        steps: [],
      },
    ]);

    setRunning(true);

    try {
      const msgIndex = messages.length + 1;

      // Update to running state
      updateMessage(msgIndex, {
        status: "running",
        content: "🔄 正在执行...",
        progress: "初始化",
      });

      // Use the streaming endpoint (which collects all events)
      const events = await api.agentChatSSE(userMsg, mode, { model_id: selectedModel });

      // Process events and update UI progressively
      let finalResponse = "";
      let reflectionReport = "";
      let steps: AgentStep[] = [];
      let results: Record<string, number> = {};
      let charts: string[] = [];

      for (const event of events) {
        const { type, data } = event;

        switch (type) {
          case "memory":
            if (data.hasMemory && data.context) {
              updateMessage(msgIndex, {
                content: `📚 召回相关记忆:\n${data.context}`,
              });
            }
            break;

          case "thought":
            updateMessage(msgIndex, {
              progress: "LLM 思考中",
              content: "🤔 LLM 正在思考下一步操作...",
            });
            break;

          case "step":
            const stepNum = data.step;
            const newStep: AgentStep = {
              tool: data.tool,
              input: data.input,
              observation: data.observation,
            };
            steps = [...steps, newStep];
            updateMessage(msgIndex, {
              steps: [...steps],
              content: `⏳ 执行中 (Step ${stepNum})...`,
              progress: `执行 Step ${stepNum}: ${data.tool}`,
            });
            break;

          case "final":
            finalResponse = data.content || "执行完成";
            updateMessage(msgIndex, {
              content: finalResponse,
              progress: "完成",
            });
            break;

          case "reflection":
            reflectionReport = data.report || "";
            updateMessage(msgIndex, {
              reflectionReport,
              content: finalResponse || "执行完成，正在反思...",
              progress: "反思中",
            });
            break;

          case "done":
            // Final result
            finalResponse = data.response || finalResponse;
            results = data.results || {};
            charts = data.charts || [];
            updateMessage(msgIndex, {
              content: finalResponse || "执行完成",
              steps: data.steps || steps,
              results,
              charts,
              reflectionReport: reflectionReport || data.reflection_report,
              memoryContext: data.memoryContext,
              status: "done",
            });
            break;

          case "error":
            updateMessage(msgIndex, {
              content: `❌ 错误: ${data.error}`,
              error: data.error,
              status: "error",
            });
            break;
        }

        // Small delay to allow UI updates between events
        await new Promise(resolve => setTimeout(resolve, 50));
      }

      // Ensure final state is set
      updateMessage(msgIndex, {
        status: "done",
        content: finalResponse || "执行完成",
        steps: steps.length > 0 ? steps : undefined,
        results: Object.keys(results).length > 0 ? results : undefined,
      });

    } catch (e: any) {
      const msgIndex = messages.length;
      updateMessage(msgIndex, {
        content: `请求失败: ${e.message}`,
        error: e.message,
        status: "error",
      });
    } finally {
      setRunning(false);
    }
  }, [input, mode, selectedModel, running, messages.length, updateMessage]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
      {/* Header */}
      <div style={{
        background: "#1e293b",
        borderRadius: "8px",
        padding: "12px 16px",
        border: "1px solid #334155",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        flexWrap: "wrap",
      }}>
        <span style={{ fontSize: "14px", fontWeight: 700, color: "#e2e8f0" }}>🤖 量子测控智能体</span>

        {/* Mode selector */}
        <div style={{ display: "flex", gap: "6px" }}>
          {AGENT_MODES.map((m) => (
            <button
              key={m.value}
              onClick={() => setMode(m.value)}
              title={m.desc}
              style={{
                padding: "4px 10px",
                borderRadius: "6px",
                border: "1px solid",
                borderColor: mode === m.value ? "#38bdf8" : "#334155",
                background: mode === m.value ? "#1e3a5f" : "#0f172a",
                color: mode === m.value ? "#38bdf8" : "#64748b",
                fontSize: "11px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {m.label}
            </button>
          ))}
        </div>

        {/* Model selector */}
        <select
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          style={{
            padding: "4px 10px",
            background: "#0f172a",
            border: "1px solid #334155",
            borderRadius: "6px",
            color: "#e2e8f0",
            fontSize: "11px",
            marginLeft: "auto",
          }}
        >
          {textModels.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </div>

      {/* Chat area */}
      <div style={{
        flex: 1,
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        padding: "4px",
      }}>
        {messages.length === 0 && (
          <div style={{
            textAlign: "center",
            color: "#475569",
            fontSize: "13px",
            marginTop: "40px",
          }}>
            <div style={{ fontSize: "32px", marginBottom: "8px" }}>🔬</div>
            <div>输入自然语言指令开始测控任务</div>
            <div style={{ fontSize: "11px", marginTop: "8px", color: "#334155" }}>
              示例：「对 q10lu1 做 T1 实验并分析结果」
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i}>
            {msg.role === "user" ? (
              /* User bubble */
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <div style={{
                  maxWidth: "75%",
                  padding: "10px 14px",
                  background: "#0369a1",
                  borderRadius: "12px 12px 4px 12px",
                  color: "#e2e8f0",
                  fontSize: "13px",
                }}>
                  {msg.content}
                </div>
              </div>
            ) : (
              /* Agent bubble */
              <div>
                {/* Status indicator */}
                {msg.status === "running" && (
                  <div style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    marginBottom: "8px",
                    fontSize: "12px",
                    color: "#38bdf8",
                  }}>
                    <div style={{
                      width: "8px", height: "8px",
                      background: "#38bdf8", borderRadius: "50%",
                      animation: "pulse 1s infinite",
                    }} />
                    {msg.progress || "执行中..."}
                  </div>
                )}

                <div style={{
                  padding: "10px 14px",
                  background: "#1e293b",
                  borderRadius: "12px 12px 12px 4px",
                  color: "#e2e8f0",
                  fontSize: "13px",
                  border: "1px solid #334155",
                  whiteSpace: "pre-wrap",
                }}>
                  {msg.content}
                </div>

                {/* Steps */}
                {msg.steps && msg.steps.length > 0 && (
                  <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
                    {msg.steps.map((step, si) => (
                      <div key={si} style={{
                        background: "#0f172a",
                        border: "1px solid #1e293b",
                        borderRadius: "8px",
                        overflow: "hidden",
                        fontSize: "11px",
                      }}>
                        <div style={{
                          padding: "6px 10px",
                          background: "#1e293b",
                          display: "flex",
                          alignItems: "center",
                          gap: "6px",
                          borderBottom: "1px solid #0f172a",
                        }}>
                          <span style={{ color: "#38bdf8", fontWeight: 700 }}>[Step {si + 1}]</span>
                          <span style={{
                            padding: "1px 6px",
                            background: "#7c3aed",
                            borderRadius: "4px",
                            color: "#e2e8f0",
                            fontSize: "10px",
                            fontFamily: "monospace",
                          }}>
                            {step.tool}
                          </span>
                          {step.retried && (
                            <span style={{ color: "#f59e0b", fontSize: "10px" }}>↻ 重试</span>
                          )}
                        </div>
                        <div style={{ padding: "6px 10px" }}>
                          {step.thought && (
                            <div style={{ color: "#94a3b8", marginBottom: "4px" }}>
                              💭 {step.thought}
                            </div>
                          )}
                          <div style={{ color: "#64748b", fontFamily: "monospace", fontSize: "10px" }}>
                            Input: {JSON.stringify(step.input)}
                          </div>
                          {step.observation && (
                            <div style={{ color: "#22c55e", marginTop: "4px", fontFamily: "monospace" }}>
                              → {typeof step.observation === "object"
                                ? JSON.stringify(step.observation)
                                : String(step.observation)}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Results card */}
                {msg.results && Object.keys(msg.results).length > 0 && (
                  <div style={{
                    marginTop: "8px",
                    background: "#0f172a",
                    border: "1px solid #22c55e40",
                    borderRadius: "8px",
                    padding: "10px 14px",
                  }}>
                    <div style={{ fontSize: "11px", color: "#22c55e", fontWeight: 700, marginBottom: "6px" }}>
                      📊 执行结果
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: "6px" }}>
                      {Object.entries(msg.results).map(([k, v]) => (
                        <div key={k} style={{
                          background: "#1e293b",
                          borderRadius: "6px",
                          padding: "6px 10px",
                          textAlign: "center",
                        }}>
                          <div style={{ color: "#64748b", fontSize: "10px" }}>{k}</div>
                          <div style={{ color: "#22c55e", fontSize: "13px", fontWeight: 700, fontFamily: "monospace" }}>
                            {typeof v === "number" ? v.toFixed(4) : v}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Reflection report */}
                {msg.reflectionReport && (
                  <div style={{
                    marginTop: "8px",
                    background: "#0f172a",
                    border: "1px solid #f59e0b40",
                    borderRadius: "8px",
                    padding: "10px 14px",
                    fontSize: "12px",
                    color: "#e2e8f0",
                    whiteSpace: "pre-wrap",
                    maxHeight: "300px",
                    overflowY: "auto",
                  }}>
                    {msg.reflectionReport}
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
        background: "#1e293b",
        borderRadius: "8px",
        padding: "12px",
        border: "1px solid #334155",
        display: "flex",
        gap: "8px",
        alignItems: "flex-end",
      }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="下达测控任务指令，如：对 q10lu1 做 T1 实验并分析..."
          disabled={running}
          style={{
            flex: 1,
            background: "#0f172a",
            border: "1px solid #334155",
            borderRadius: "8px",
            padding: "8px 12px",
            color: "#e2e8f0",
            fontSize: "13px",
            resize: "none",
            minHeight: "44px",
            maxHeight: "120px",
            fontFamily: "inherit",
            outline: "none",
          }}
          rows={1}
        />
        <button
          onClick={handleSend}
          disabled={running || !input.trim()}
          style={{
            padding: "8px 16px",
            background: running ? "#1e3a5f" : "#0369a1",
            border: "none",
            borderRadius: "8px",
            color: "#e2e8f0",
            fontSize: "13px",
            fontWeight: 600,
            cursor: running ? "not-allowed" : "pointer",
            opacity: running ? 0.6 : 1,
          }}
        >
          {running ? "..." : "➤"}
        </button>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}

