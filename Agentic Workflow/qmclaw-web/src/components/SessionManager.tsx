"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

type SessionInfo = { name: string; path: string[] };
type TreeNode = { name: string; path: string[]; hasChildren: boolean };
type CurrentInfo = { conn_id: string; name: string; host: string; port: number; connected: boolean };

const DEFAULT_SESSION_KEY = "qmclaw.defaultSession";

// ── Tree Node Component ─────────────────────────────────────────────────────────

function TreeNode({
  node,
  level,
  expandedNodes,
  onToggle,
  onSelect,
  selectedPath,
}: {
  node: TreeNode;
  level: number;
  expandedNodes: Set<string>;
  onToggle: (path: string[]) => void;
  onSelect: (path: string[]) => void;
  selectedPath: string[];
}) {
  const pathKey = node.path.join("/");
  const isExpanded = expandedNodes.has(pathKey);
  const isSelected = pathKey === selectedPath.join("/");

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          cursor: "pointer",
          padding: "0.35rem 0.75rem",
          paddingLeft: `${0.75 + level * 1}rem`,
          background: isSelected ? "#2d1f5e" : "transparent",
          borderLeft: isSelected ? "2px solid #6366f1" : "2px solid transparent",
        }}
        onMouseEnter={e => !isSelected && (e.currentTarget.style.background = "#1e293b")}
        onMouseLeave={e => !isSelected && (e.currentTarget.style.background = "transparent")}
        onClick={() => onSelect(node.path)}
      >
        {node.hasChildren && (
          <span
            style={{
              width: "1rem",
              cursor: "pointer",
              color: "#64748b",
              fontSize: "0.7rem",
              marginRight: "0.25rem",
            }}
            onClick={e => { e.stopPropagation(); onToggle(node.path); }}
          >
            {isExpanded ? "▼" : "▶"}
          </span>
        )}
        {!node.hasChildren && <span style={{ width: "1.25rem" }} />}
        <span style={{
          fontSize: "0.8rem",
          color: isSelected ? "#a78bfa" : "#e2e8f0",
          fontFamily: "monospace",
        }}>
          {node.hasChildren ? "📁" : "📄"} {node.name}
        </span>
      </div>
      {isExpanded && node.hasChildren && (
        <ChildrenLoader
          parentPath={node.path}
          level={level + 1}
          expandedNodes={expandedNodes}
          onToggle={onToggle}
          onSelect={onSelect}
          selectedPath={selectedPath}
        />
      )}
    </>
  );
}

// ── Lazy Load Children Component ───────────────────────────────────────────────

function ChildrenLoader({
  parentPath,
  level,
  expandedNodes,
  onToggle,
  onSelect,
  selectedPath,
}: {
  parentPath: string[];
  level: number;
  expandedNodes: Set<string>;
  onToggle: (path: string[]) => void;
  onSelect: (path: string[]) => void;
  selectedPath: string[];
}) {
  const [children, setChildren] = useState<TreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    // Fetch children of this node
    const pathStr = parentPath.slice(1).join("/");  // Remove empty first element for API
    console.log('[ChildrenLoader] Fetching children for path:', pathStr, 'full:', JSON.stringify(parentPath));

    api.listDatasets(pathStr || "LQHL")
      .then(res => {
        console.log('[ChildrenLoader] API response:', res);
        if (cancelled) return;
        const groups = (res as { groups?: string[] }).groups || [];
        console.log('[ChildrenLoader] Groups:', groups);
        const nodes: TreeNode[] = groups
          .filter((g: string) => !g.startsWith("."))
          .map((name: string) => ({
            name,
            path: [...parentPath, name],
            hasChildren: true,
          }));
        console.log('[ChildrenLoader] Created nodes:', nodes.length);
        setChildren(nodes);
        setLoading(false);
      })
      .catch(e => {
        console.error('[ChildrenLoader] Error:', e);
        if (!cancelled) {
          setError(String(e));
          setChildren([]);
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [parentPath]);

  if (loading) {
    return (
      <div style={{
        paddingLeft: `${1 + level}rem`,
        padding: "0.25rem 0.75rem",
        fontSize: "0.7rem",
        color: "#64748b",
      }}>
        ⏳ Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{
        paddingLeft: `${1 + level}rem`,
        padding: "0.25rem 0.75rem",
        fontSize: "0.7rem",
        color: "#f87171",
      }}>
        ❌ Error: {error}
      </div>
    );
  }

  if (children.length === 0) {
    return (
      <div style={{
        paddingLeft: `${1 + level}rem`,
        padding: "0.25rem 0.75rem",
        fontSize: "0.7rem",
        color: "#475569",
      }}>
        (empty)
      </div>
    );
  }

  return children.map(node => (
    <TreeNode
      key={node.path.join("/")}
      node={node}
      level={level}
      expandedNodes={expandedNodes}
      onToggle={onToggle}
      onSelect={onSelect}
      selectedPath={selectedPath}
    />
  ));
}

// ── Compact Session Manager (Header Component) ─────────────────────────────────

export function CompactSessionManager() {
  const [sessionTree, setSessionTree] = useState<TreeNode[]>([]);
  const [currentPath, setCurrentPath] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());

  // Load initial session tree (root level only)
  const loadSessionTree = useCallback(async () => {
    try {
      const res = await api.getSessionTree() as { tree: TreeNode[] };
      setSessionTree(res.tree || []);
    } catch (e) {
      console.error('[SessionManager] Load tree error:', e);
    }
  }, []);

  // Load current session path
  const loadCurrentPath = useCallback(async () => {
    try {
      const res = await api.listQubits() as { sessionPath: string[] };
      setCurrentPath(res.sessionPath || []);
    } catch (e) {
      console.error('[SessionManager] Load current path error:', e);
    }
  }, []);

  // Initial load
  useEffect(() => {
    loadSessionTree();
    loadCurrentPath();

    // Auto-switch to saved default session
    const savedDefault = localStorage.getItem(DEFAULT_SESSION_KEY);
    if (savedDefault) {
      try {
        const defaultPath = JSON.parse(savedDefault) as string[];
        if (defaultPath.length > 0) {
          api.switchSession(defaultPath).then(() => {
            loadCurrentPath();
            loadSessionTree();
          }).catch(() => {});
        }
      } catch {}
    }
  }, [loadSessionTree, loadCurrentPath]);

  const handleToggle = useCallback((path: string[]) => {
    setExpandedNodes(prev => {
      const next = new Set(prev);
      const key = path.join("/");
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  const handleSelect = useCallback(async (path: string[]) => {
    if (switching) return;
    setSwitching(true);
    console.log('[SessionManager] Switching to:', JSON.stringify(path));
    try {
      await api.switchSession(path);
      setCurrentPath(path);
      setOpen(false);
      // Save as default
      localStorage.setItem(DEFAULT_SESSION_KEY, JSON.stringify(path));
      // Notify other components
      window.dispatchEvent(new CustomEvent('qmclaw:session-changed', { detail: { path } }));
      console.log('[SessionManager] Session changed successfully');
    } catch (e) {
      console.error('[SessionManager] Switch error:', e);
      alert('Failed to switch session: ' + (e as Error).message);
    } finally {
      setSwitching(false);
    }
  }, [switching]);

  // Get display name for current path
  const currentPathName = currentPath.length > 0
    ? currentPath[currentPath.length - 1]
    : "Root";

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <button
        id="session-btn"
        onClick={() => { setOpen(!open); if (!open) { loadSessionTree(); loadCurrentPath(); } }}
        title={`Current: ${currentPath.join(" > ")}`}
        style={{
          display: "flex", alignItems: "center", gap: "0.4rem",
          padding: "0.35rem 0.8rem",
          background: open ? "#2d1f5e" : "#1e293b",
          color: "#a78bfa",
          border: "1px solid #6366f1",
          borderRadius: "0.375rem",
          cursor: "pointer",
          fontSize: "0.8rem",
          fontFamily: "monospace",
          fontWeight: 500,
          boxShadow: open ? "0 0 8px rgba(99, 102, 241, 0.4)" : "none",
          transition: "all 0.2s ease",
        }}
      >
        📁 {currentPathName}
        <span style={{ color: "#64748b", fontSize: "0.65rem" }}>▼</span>
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "100%", right: 0, marginTop: "0.5rem",
          background: "#0f172a", border: "1px solid #6366f1",
          borderRadius: "0.5rem", minWidth: "320px", maxHeight: "400px",
          zIndex: 1000, boxShadow: "0 8px 24px rgba(0,0,0,0.6)",
          display: "flex", flexDirection: "column",
        }}>
          {/* Header */}
          <div style={{
            padding: "0.6rem 0.75rem",
            borderBottom: "1px solid #334155",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <div style={{ fontSize: "0.6rem", color: "#6366f1", fontWeight: 600, letterSpacing: "0.1em" }}>
              DATA VAULT SESSION
            </div>
            <button
              onClick={async () => {
                // Refresh both tree and qubits
                await loadSessionTree();
                await loadCurrentPath();
                // Notify other components to reload qubits
                window.dispatchEvent(new CustomEvent('qmclaw:session-changed', { detail: { path: currentPath } }));
              }}
              style={{
                background: "transparent", border: "none",
                color: "#64748b", cursor: "pointer", fontSize: "0.8rem",
              }}
              title="Refresh qubits list"
            >
              ↻
            </button>
          </div>

          {/* Current path */}
          {currentPath.length > 0 && (
            <div style={{
              padding: "0.4rem 0.75rem",
              borderBottom: "1px solid #1e293b",
              background: "#0a0f1a",
              fontSize: "0.7rem",
            }}>
              <span style={{ color: "#475569" }}>Current: </span>
              <span style={{ color: "#a78bfa", fontFamily: "monospace" }}>
                {currentPath.join(" > ")}
              </span>
            </div>
          )}

          {/* Tree view */}
          <div style={{ flex: 1, overflow: "auto", padding: "0.3rem 0" }}>
            {switching && (
              <div style={{
                padding: "1rem", textAlign: "center", color: "#6366f1",
              }}>
                ⏳ Switching...
              </div>
            )}
            {!switching && sessionTree.map(node => (
              <TreeNode
                key={node.path.join("/")}
                node={node}
                level={0}
                expandedNodes={expandedNodes}
                onToggle={handleToggle}
                onSelect={handleSelect}
                selectedPath={currentPath}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Legacy SessionManager (keep for backward compatibility) ─────────────────────

export default function SessionManager({ onSessionChange }: { onSessionChange?: () => void }) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listSessions() as {
        current: CurrentInfo;
        sessions: SessionInfo[];
      };
      setSessions(res.sessions || []);
    } catch (e: any) {
      setError(e.message || "Failed to load sessions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  const handleSwitch = async (path: string[]) => {
    try {
      await api.switchSession(path);
      onSessionChange?.();
      await loadSessions();
    } catch (e: any) {
      setError(e.message || "Switch failed");
    }
  };

  return (
    <div style={{
      border: "1px solid #1e293b",
      borderRadius: "0.5rem",
      background: "#0a0f1a",
      overflow: "hidden",
    }}>
      <div style={{
        padding: "0.5rem 0.75rem",
        fontSize: "0.7rem", fontWeight: 600,
        color: "#475569", letterSpacing: "0.1em",
        borderBottom: "1px solid #1e293b",
        background: "#0f172a",
      }}>
        SESSION MANAGER
      </div>
      <div style={{ maxHeight: "200px", overflow: "auto" }}>
        {loading && <div style={{ padding: "1rem", color: "#334569", textAlign: "center" }}>Loading...</div>}
        {error && <div style={{ padding: "0.75rem", color: "#f87171" }}>{error}</div>}
        {!loading && sessions.map(s => (
          <div
            key={s.path.join("/")}
            onClick={() => handleSwitch(s.path)}
            style={{
              padding: "0.4rem 0.75rem",
              borderBottom: "1px solid #1e293b",
              cursor: "pointer",
            }}
          >
            <div style={{ fontFamily: "monospace", fontSize: "0.7rem", color: "#94a3b8" }}>
              📁 {s.name}
            </div>
            <div style={{ fontSize: "0.6rem", color: "#475569" }}>
              {s.path.join(" / ")}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
