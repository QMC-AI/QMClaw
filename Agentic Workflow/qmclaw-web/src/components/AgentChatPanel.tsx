"use client";

/**
 * Quantum Control Agent Chat Panel
 * Natural language interface with progress updates
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../lib/api";
import { useModelStore } from "../store/modelStore";
import MemoryPanel from "./MemoryPanel";
import AgentToolsPanel from "./AgentToolsPanel";

type AgentView = "chat" | "memory" | "tools";

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

/** 会话摘要（POST /api/agent/memory/sessions 的 sessions[] 元素） */
interface AgentSessionSummary {
  sessionId: string;
  rounds: number;
  firstAt?: string;
  lastAt?: string;
  taskPreview?: string;
  qubit?: string | null;
  model?: string | null;
  status?: string | null;
}

const AGENT_MODES = [
  { value: "react", label: "ReAct", desc: "Think-Act-Observe 循环" },
  { value: "plan_and_execute", label: "Plan & Execute", desc: "先规划后执行" },
  { value: "reflexion", label: "Reflexion", desc: "执行后反思纠错" },
];

// 与 page.tsx / lib/api.ts 同一取值方式。图表若以 /plots/xxx.png 相对路径返回，
// 需要拼上后端地址才能被 <img> 加载。
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";

/** 当前会话在 localStorage 的键（刷新恢复用；与 Hermes 的 hermes_session_id 互不干扰） */
const SESSION_STORAGE_KEY = "agent_session_id";

/** 新会话 id：优先 uuid，退化为时间戳 + 随机串 */
function newSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `agent_${crypto.randomUUID()}`;
  }
  return `agent_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * 会话回放 payload → ChatMessage[]。
 * 优先用契约里的 messages（user/assistant），缺失时回退到 episodes
 * （老数据只有 task / steps / response / metrics 的形态）。
 */
function sessionToMessages(payload: any): ChatMessage[] {
  const out: ChatMessage[] = [];

  const msgs = Array.isArray(payload?.messages) ? payload.messages : [];
  if (msgs.length > 0) {
    for (const m of msgs) {
      if (!m) continue;
      if (m.role === "user") {
        out.push({ role: "user", content: String(m.content ?? "") });
      } else if (m.role === "assistant" || m.role === "agent") {
        out.push({
          role: "agent",
          content: String(m.content ?? ""),
          steps: Array.isArray(m.steps) ? m.steps : [],
          results: m.results || {},
          status: "done",
        });
      }
    }
    return out;
  }

  const episodes = Array.isArray(payload?.episodes) ? payload.episodes : [];
  for (const ep of episodes) {
    const task = typeof ep?.task === "string" ? ep.task : "";
    if (!task.trim()) continue;   // 空 task 是脏数据，回放不带上
    out.push({ role: "user", content: task });
    out.push({
      role: "agent",
      content: ep?.response || "执行完成",
      steps: Array.isArray(ep?.steps) ? ep.steps : [],
      results: ep?.results || ep?.metrics || {},
      status: "done",
    });
  }
  return out;
}

/** 下一轮的 round_index（1 起递增）：优先 episode.roundIndex 最大值 + 1 */
function sessionNextRound(payload: any, messages: ChatMessage[]): number {
  const episodes = Array.isArray(payload?.episodes) ? payload.episodes : [];
  const indices = episodes
    .map((e: any) => e?.roundIndex)
    .filter((n: any): n is number => typeof n === "number");
  if (indices.length > 0) return Math.max(...indices) + 1;
  return messages.filter((m) => m.role === "user").length + 1;
}

function formatSessionTime(timestamp?: string): string {
  if (!timestamp) return "";
  const t = new Date(timestamp).getTime();
  if (Number.isNaN(t)) return "";
  const diffMin = Math.floor((Date.now() - t) / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay} 天前`;
  return new Date(t).toLocaleDateString();
}

const SESSION_STATUS_STYLE: Record<string, { color: string; label: string }> = {
  success: { color: "#22c55e", label: "成功" },
  failed: { color: "#f87171", label: "失败" },
  mixed: { color: "#f59e0b", label: "混合" },
  running: { color: "#38bdf8", label: "运行中" },
};

export default function AgentChatPanel() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("react");
  const [running, setRunning] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");
  const [activeView, setActiveView] = useState<AgentView>("chat");
  const [sessions, setSessions] = useState<AgentSessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [restoring, setRestoring] = useState(false);
  const [sessionNotice, setSessionNotice] = useState("");
  const [sessionId, setSessionId] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // sessionId 的即时副本：发消息时不依赖闭包里的 state
  const sessionIdRef = useRef("");
  // 下一轮的 round_index（1 起）
  const roundRef = useRef(1);
  // 本地已重建消息的 sessionId：该 id 不再从后端拉取（新建 / 单轮退化时用）
  const skipRestoreIdRef = useRef<string | null>(null);

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

  // ── 会话：列表 / 回放 / 新建 ─────────────────────────────────────────────

  const loadSessions = useCallback(async () => {
    try {
      const res = await api.agentGetSessions(50);
      setSessions(Array.isArray(res?.sessions) ? res.sessions : []);
    } catch (e) {
      console.warn("加载会话列表失败:", e);
      setSessions([]);
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  /** 拉取并恢复某个会话的历史消息（assistant 的 steps/results 回到对应字段） */
  const restoreSession = useCallback(async (sid: string) => {
    setRestoring(true);
    try {
      const res = await api.agentGetSessionMessages(sid);
      if (res && res.success === false) {
        // 从未落盘的会话（例如刚 [+ New] 就刷新）：后端没有这个 session，
        // 属于正常空会话，不要弹「恢复失败」吓人。
        if (/not\s*found/i.test(String(res.error || ""))) {
          setMessages([]);
          roundRef.current = 1;
          return;
        }
        console.warn("恢复会话失败:", res.error);
        setSessionNotice(`会话恢复失败：${res.error || "未知错误"}`);
        setMessages([]);
        roundRef.current = 1;
        return;
      }
      const restored = sessionToMessages(res);
      roundRef.current = sessionNextRound(res, restored);
      setMessages(restored);
    } catch (e: any) {
      console.warn("恢复会话失败:", e);
      setSessionNotice(`会话恢复失败：${e?.message || e}`);
    } finally {
      setRestoring(false);
    }
  }, []);

  // 初始化：页面加载时若 localStorage 有当前会话 id 则复用它（下面会拉取恢复）
  useEffect(() => {
    let stored = "";
    try {
      stored = localStorage.getItem(SESSION_STORAGE_KEY) || "";
    } catch {
      /* localStorage 不可用则每次新建 */
    }
    if (stored) {
      sessionIdRef.current = stored;
      setSessionId(stored);
      return;
    }
    const sid = newSessionId();
    try {
      localStorage.setItem(SESSION_STORAGE_KEY, sid);
    } catch {
      /* ignore */
    }
    sessionIdRef.current = sid;
    skipRestoreIdRef.current = sid;   // 新会话没有历史，不拉取
    setSessionId(sid);
  }, []);

  // sessionId 变化 → 拉取恢复（刷新恢复 / 切换多轮会话）
  useEffect(() => {
    if (!sessionId) return;
    if (skipRestoreIdRef.current === sessionId) {
      skipRestoreIdRef.current = null;
      return;
    }
    restoreSession(sessionId);
  }, [sessionId, restoreSession]);

  const handleNewSession = useCallback(() => {
    if (running || restoring) return;   // 恢复途中不许换会话，避免旧会话的恢复结果覆盖新会话
    const sid = newSessionId();
    try {
      localStorage.setItem(SESSION_STORAGE_KEY, sid);
    } catch {
      /* ignore */
    }
    sessionIdRef.current = sid;
    skipRestoreIdRef.current = sid;
    setSessionId(sid);
    setMessages([]);
    roundRef.current = 1;
    setSessionNotice("");
  }, [running, restoring]);

  const handleSwitchSession = useCallback(async (session: AgentSessionSummary) => {
    if (running || restoring) return;   // 同一时刻只允许一次恢复，避免两次恢复互相覆盖
    setSessionNotice("");

    // 老数据退化：rounds == 1 的会话没有可延续的多轮上下文。
    // 语义 =「以该任务为起点新建会话」——换新 sessionId，把这一轮 task/response
    // 放进新会话上下文，不假装是在原会话里继续。
    if ((session.rounds ?? 0) <= 1) {
      setRestoring(true);
      try {
        const res = await api.agentGetSessionMessages(session.sessionId);
        const readFailed = !!res && res.success === false;
        const seeded = readFailed ? [] : sessionToMessages(res);

        const sid = newSessionId();
        try {
          localStorage.setItem(SESSION_STORAGE_KEY, sid);
        } catch {
          /* ignore */
        }
        sessionIdRef.current = sid;
        skipRestoreIdRef.current = sid;
        setSessionId(sid);
        setMessages(seeded);
        roundRef.current = seeded.filter((m) => m.role === "user").length + 1;

        if (readFailed) {
          setSessionNotice(`该会话读取失败：${res.error || "未知错误"}`);
        } else if (seeded.length > 0) {
          setSessionNotice("该会话只有一轮，已以这轮任务为起点新建会话（未延续原会话）");
        }
      } catch (e: any) {
        console.warn("读取单轮会话失败:", e);
        setSessionNotice(`读取单轮会话失败：${e?.message || e}`);
      } finally {
        setRestoring(false);
      }
      return;
    }

    try {
      localStorage.setItem(SESSION_STORAGE_KEY, session.sessionId);
    } catch {
      /* ignore */
    }
    sessionIdRef.current = session.sessionId;
    if (sessionId === session.sessionId) {
      restoreSession(session.sessionId);   // 同一会话再点一次：手动重放
    } else {
      setSessionId(session.sessionId);     // 触发恢复 effect
    }
  }, [running, restoring, sessionId, restoreSession]);

  const handleSend = useCallback(async () => {
    if (!input.trim() || running || restoring) return;
    const userMsg = input.trim();
    setInput("");

    // 确保有会话 id（理论上挂载时已生成）
    let sid = sessionIdRef.current;
    if (!sid) {
      sid = newSessionId();
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, sid);
      } catch {
        /* ignore */
      }
      sessionIdRef.current = sid;
      setSessionId(sid);
    }
    const roundIndex = roundRef.current;   // 本轮 round_index，1 起递增
    roundRef.current = roundIndex + 1;

    setSessionNotice("");

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

      // Use non-blocking agent_chat (runs in Python background thread)
      const result = await api.agentChat(userMsg, mode, {
        model_id: selectedModel,
        session_id: sid,
        round_index: roundIndex,
      });

      // Update message with result
      updateMessage(msgIndex, {
        content: result.response || "执行完成",
        steps: result.steps || [],
        results: result.results || {},
        charts: result.charts || [],
        reflectionReport: result.reflection_report,
        status: "done",
      });

      loadSessions();   // 本轮已落盘，刷新会话列表

    } catch (e: any) {
      // 占位 agent 气泡在 messages.length + 1（user 在 messages.length）——
      // 这里原本写成 messages.length，会把用户自己的消息覆盖成报错。
      const msgIndex = messages.length + 1;
      updateMessage(msgIndex, {
        content: `请求失败: ${e.message}`,
        error: e.message,
        status: "error",
      });
    } finally {
      setRunning(false);
    }
  }, [input, mode, selectedModel, running, restoring, messages.length, updateMessage, loadSessions]);

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

        {/* View toggle */}
        <div style={{ display: "flex", gap: "4px" }}>
          {(["chat", "memory", "tools"] as AgentView[]).map((v) => (
            <button
              key={v}
              onClick={() => setActiveView(v)}
              style={{
                padding: "4px 10px",
                borderRadius: "6px",
                border: "1px solid",
                borderColor: activeView === v ? "#38bdf8" : "#334155",
                background: activeView === v ? "#1e3a5f" : "#0f172a",
                color: activeView === v ? "#38bdf8" : "#64748b",
                fontSize: "11px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {v === "chat" ? "💬 Chat" : v === "memory" ? "📚 Memory" : "🛠 Tools"}
            </button>
          ))}
        </div>

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

      {/* Content area - switch between views */}
      {activeView === "chat" && (
      <div style={{ flex: 1, display: "flex", gap: "12px", minHeight: 0 }}>
        {/* Session sidebar */}
        <div style={{
          width: "210px",
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          background: "#0f172a",
          border: "1px solid #334155",
          borderRadius: "8px",
          overflow: "hidden",
        }}>
          <div style={{
            padding: "8px 10px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: "1px solid #1e293b",
            flexShrink: 0,
          }}>
            <span style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.08em", color: "#475569" }}>
              🧠 SESSIONS
            </span>
            <button
              onClick={loadSessions}
              title="刷新会话列表"
              style={{
                padding: "1px 5px",
                background: "transparent",
                border: "1px solid #334155",
                borderRadius: "4px",
                color: "#64748b",
                cursor: "pointer",
                fontSize: "10px",
              }}
            >
              ↻
            </button>
          </div>

          <button
            onClick={handleNewSession}
            disabled={running}
            style={{
              margin: "8px",
              padding: "5px 10px",
              background: "#052e16",
              border: "1px solid #22c55e",
              borderRadius: "6px",
              color: "#22c55e",
              cursor: running ? "not-allowed" : "pointer",
              opacity: running ? 0.6 : 1,
              fontSize: "11px",
              fontWeight: 600,
              flexShrink: 0,
            }}
          >
            + New
          </button>

          <div style={{
            padding: "0 8px 6px",
            fontSize: "9px",
            color: "#334155",
            fontFamily: "monospace",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flexShrink: 0,
          }} title={sessionId}>
            当前 {restoring ? "恢复中…" : sessionId ? `…${sessionId.slice(-8)}` : "—"}
          </div>

          <div style={{ flex: 1, overflowY: "auto" }}>
            {sessionsLoading && (
              <div style={{ padding: "12px", color: "#334155", fontSize: "11px", textAlign: "center" }}>
                加载中…
              </div>
            )}
            {!sessionsLoading && sessions.length === 0 && (
              <div style={{ padding: "12px", color: "#334155", fontSize: "11px", textAlign: "center" }}>
                暂无历史会话
              </div>
            )}
            {sessions.map((s) => {
              const active = s.sessionId === sessionId;
              const st = SESSION_STATUS_STYLE[s.status || ""] || { color: "#64748b", label: s.status || "-" };
              const legacy = (s.rounds ?? 0) <= 1;
              return (
                <div
                  key={s.sessionId}
                  onClick={() => handleSwitchSession(s)}
                  title={legacy
                    ? "单轮会话：点击 = 以该任务为起点新建会话"
                    : `${s.sessionId}（点击恢复该会话）`}
                  style={{
                    padding: "6px 10px",
                    borderBottom: "1px solid #1e293b",
                    cursor: running ? "not-allowed" : "pointer",
                    background: active ? "#1e3a5f" : "transparent",
                    transition: "background 0.15s",
                  }}
                >
                  <div style={{
                    fontSize: "11px",
                    color: active ? "#38bdf8" : "#e2e8f0",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    marginBottom: "3px",
                  }}>
                    {s.taskPreview || s.sessionId.slice(0, 16)}
                  </div>
                  <div style={{ fontSize: "9px", color: "#475569", display: "flex", gap: "6px", flexWrap: "wrap" }}>
                    <span>{s.rounds ?? 0} 轮</span>
                    {s.qubit && <span>{s.qubit}</span>}
                    <span style={{ color: st.color }}>{st.label}</span>
                    <span style={{ marginLeft: "auto" }}>{formatSessionTime(s.lastAt)}</span>
                  </div>
                  {legacy && (
                    <div style={{ fontSize: "9px", color: "#f59e0b", marginTop: "2px" }}>
                      单轮 → 以此为起点新建
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Chat column */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "12px", minWidth: 0, minHeight: 0 }}>
          {sessionNotice && (
            <div style={{
              background: "#1e293b",
              border: "1px solid #f59e0b40",
              borderRadius: "6px",
              padding: "6px 12px",
              fontSize: "11px",
              color: "#f59e0b",
              flexShrink: 0,
            }}>
              {sessionNotice}
            </div>
          )}

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

                    {/* Charts */}
                    {msg.charts && msg.charts.length > 0 && (
                      <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "8px" }}>
                        <div style={{ fontSize: "11px", color: "#38bdf8", fontWeight: 700 }}>
                          📈 实验图表
                        </div>
                        {msg.charts.map((chart, ci) => {
                          // 两种来源都要支持：
                          //   data:image/png;base64,...  —— experiments 页同款图
                          //   /plots/xxx.png            —— 离线模拟器自绘的图
                          const src = /^(data:|https?:)/.test(chart) ? chart : `${API_BASE}${chart}`;
                          return (
                            <div key={ci} style={{
                              background: "#0f172a",
                              border: "1px solid #1e293b",
                              borderRadius: "8px",
                              padding: "8px",
                            }}>
                              <img
                                src={src}
                                alt={`chart-${ci + 1}`}
                                style={{ maxWidth: "100%", borderRadius: "4px", display: "block" }}
                              />
                              <div style={{ marginTop: "6px", display: "flex", gap: "12px", fontSize: "10px" }}>
                                <a
                                  href={src}
                                  download={`chart-${ci + 1}.png`}
                                  style={{ color: "#38bdf8", textDecoration: "none" }}
                                >
                                  📥 下载 PNG
                                </a>
                                <a
                                  href={src}
                                  target="_blank"
                                  rel="noreferrer"
                                  style={{ color: "#94a3b8", textDecoration: "none" }}
                                >
                                  ↗ 新标签打开
                                </a>
                              </div>
                            </div>
                          );
                        })}
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
            flexShrink: 0,
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
              disabled={running || restoring}
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
              disabled={running || restoring || !input.trim()}
              style={{
                padding: "8px 16px",
                background: running || restoring ? "#1e3a5f" : "#0369a1",
                border: "none",
                borderRadius: "8px",
                color: "#e2e8f0",
                fontSize: "13px",
                fontWeight: 600,
                cursor: running || restoring ? "not-allowed" : "pointer",
                opacity: running || restoring ? 0.6 : 1,
              }}
            >
              {running ? "..." : "➤"}
            </button>
          </div>
        </div>
      </div>
      )}  {/* End of chat view */}

      {/* Memory view */}
      {activeView === "memory" && (
        <div style={{ flex: 1, overflow: "auto" }}>
          <MemoryPanel />
        </div>
      )}

      {/* Tools view */}
      {activeView === "tools" && (
        <div style={{ flex: 1, overflow: "auto" }}>
          <AgentToolsPanel />
        </div>
      )}

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
      `}</style>
    </div>
  );
}
