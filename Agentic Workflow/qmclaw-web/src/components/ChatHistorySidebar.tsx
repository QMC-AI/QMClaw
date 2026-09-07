"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface ChatSession {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  modelId: string;
  modelName?: string;
  messageCount?: number;
}

interface Props {
  currentSessionId?: string;
  onSelectSession: (sessionId: string) => void;
  onNewSession: () => void;
}

export default function ChatHistorySidebar({ currentSessionId, onSelectSession, onNewSession }: Props) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    try {
      const data = await api.listChatSessions() as ChatSession[];
      setSessions(data || []);
    } catch (e) {
      console.error("Failed to load sessions:", e);
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const handleDelete = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    if (!confirm("Delete this chat session?")) return;
    setDeletingId(sessionId);
    try {
      await api.deleteChatSession(sessionId);
      setSessions(prev => prev.filter(s => s.id !== sessionId));
      if (currentSessionId === sessionId) {
        onSelectSession("");
      }
    } catch (e) {
      console.error("Failed to delete session:", e);
    } finally {
      setDeletingId(null);
    }
  };

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
        <span>💬 CHAT HISTORY</span>
        <button
          onClick={() => { loadSessions(); }}
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

      {/* New chat button */}
      <button
        onClick={onNewSession}
        style={{
          margin: "0.5rem",
          padding: "0.4rem 0.75rem",
          background: "#1e3a5f",
          border: "1px solid #38bdf8",
          borderRadius: "0.375rem",
          color: "#38bdf8",
          cursor: "pointer",
          fontSize: "0.75rem",
          fontWeight: 600,
          flexShrink: 0,
        }}
      >
        + New Chat
      </button>

      {/* Session list */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {sessions.length === 0 && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            No chat history yet
          </div>
        )}
        {sessions.map((session) => (
          <div
            key={session.id}
            onClick={() => onSelectSession(session.id)}
            style={{
              padding: "0.5rem 0.75rem",
              borderBottom: "1px solid #1e293b",
              cursor: "pointer",
              background: session.id === currentSessionId ? "#1e3a5f" : "transparent",
              transition: "background 0.15s",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: "0.75rem",
                  color: session.id === currentSessionId ? "#38bdf8" : "#e2e8f0",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  marginBottom: "0.25rem",
                }}>
                  {session.title || "Untitled Chat"}
                </div>
                <div style={{ fontSize: "0.65rem", color: "#475569", display: "flex", gap: "0.5rem" }}>
                  <span>{formatTime(session.updatedAt || session.createdAt)}</span>
                  {session.modelName && <span>{session.modelName}</span>}
                </div>
              </div>
              <button
                onClick={(e) => handleDelete(e, session.id)}
                disabled={deletingId === session.id}
                title="Delete session"
                style={{
                  padding: "0.15rem 0.3rem",
                  background: "transparent",
                  border: "1px solid #334155",
                  borderRadius: "0.2rem",
                  color: deletingId === session.id ? "#475569" : "#f87171",
                  cursor: deletingId === session.id ? "not-allowed" : "pointer",
                  fontSize: "0.6rem",
                  flexShrink: 0,
                }}
              >
                {deletingId === session.id ? "..." : "✕"}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
