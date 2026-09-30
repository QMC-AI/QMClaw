"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface Session {
  id: string;
  title: string;
  last_active?: string;
  message_count: number;
}

interface Props {
  onSelectSession?: (sessionId: string) => void;
  onNewSession?: () => void;
}

export default function HermesSessionsSidebar({ onSelectSession, onNewSession }: Props) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Read current session from localStorage directly
  const getCurrentSessionId = useCallback(() => {
    return localStorage.getItem('hermes_session_id') || '';
  }, []);

  const [currentSessionId, setCurrentSessionId] = useState(getCurrentSessionId);

  // Listen for session changes to update the sidebar's view of current session
  useEffect(() => {
    const handleStorageChange = () => {
      setCurrentSessionId(getCurrentSessionId());
    };
    const handleSessionChange = () => {
      setCurrentSessionId(getCurrentSessionId());
    };

    window.addEventListener('storage', handleStorageChange);
    window.addEventListener('hermes-session-change', handleSessionChange);
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('hermes-session-change', handleSessionChange);
    };
  }, [getCurrentSessionId]);

  const loadSessions = useCallback(async () => {
    try {
      const res = await api.hermesGetSessions();
      setSessions(res.sessions || []);
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

  // Listen for message sent events to refresh
  useEffect(() => {
    const handleMessageSent = () => {
      loadSessions();
    };
    window.addEventListener('hermes-message-sent', handleMessageSent);
    return () => window.removeEventListener('hermes-message-sent', handleMessageSent);
  }, [loadSessions]);

  const handleDelete = async (e: React.MouseEvent, sessionId: string) => {
    e.stopPropagation();
    if (!confirm("Delete this session?")) return;
    setDeletingId(sessionId);
    try {
      await api.hermesDeleteSession(sessionId);
      setSessions(prev => prev.filter(s => s.id !== sessionId));
      // Read current session from localStorage to avoid stale closure
      const currentId = localStorage.getItem('hermes_session_id') || '';
      if (currentId === sessionId) {
        // Switch to a different session or create new one
        const remaining = sessions.filter(s => s.id !== sessionId);
        if (remaining.length > 0) {
          handleSelectSession(remaining[0].id);
        } else {
          handleNewSession();
        }
      }
    } catch (e) {
      console.error("Failed to delete session:", e);
    } finally {
      setDeletingId(null);
    }
  };

  const handleSelectSession = (sessionId: string) => {
    localStorage.setItem('hermes_session_id', sessionId);
    setCurrentSessionId(sessionId);  // Update local state immediately
    window.dispatchEvent(new CustomEvent('hermes-session-change', { detail: sessionId }));
    onSelectSession?.(sessionId);
  };

  const handleNewSession = () => {
    const newId = `hermes_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    localStorage.setItem('hermes_session_id', newId);
    setCurrentSessionId(newId);  // Update local state immediately
    window.dispatchEvent(new CustomEvent('hermes-session-change', { detail: newId }));
    onNewSession?.();
  };

  const formatTime = (timestamp?: string) => {
    if (!timestamp) return "";
    try {
      const date = new Date(timestamp);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return "Just now";
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;
      const diffDays = Math.floor(diffHours / 24);
      if (diffDays < 7) return `${diffDays}d ago`;
      return date.toLocaleDateString();
    } catch {
      return "";
    }
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
        <span>💬 HERMES SESSIONS</span>
        <button
          onClick={loadSessions}
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
        onClick={handleNewSession}
        style={{
          margin: "0.5rem",
          padding: "0.4rem 0.75rem",
          background: "#052e16",
          border: "1px solid #22c55e",
          borderRadius: "0.375rem",
          color: "#22c55e",
          cursor: "pointer",
          fontSize: "0.75rem",
          fontWeight: 600,
          flexShrink: 0,
        }}
      >
        + New Session
      </button>

      {/* Session list */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {sessions.length === 0 && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            No sessions yet
          </div>
        )}
        {sessions.map((session) => (
          <div
            key={session.id}
            onClick={() => handleSelectSession(session.id)}
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
                  {session.title || session.id.slice(0, 16)}
                </div>
                <div style={{ fontSize: "0.65rem", color: "#475569", display: "flex", gap: "0.5rem" }}>
                  <span>{session.message_count} msgs</span>
                  <span>{formatTime(session.last_active)}</span>
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
