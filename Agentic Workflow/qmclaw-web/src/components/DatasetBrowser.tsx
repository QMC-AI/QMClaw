"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

type Dataset = { id: string; name: string; path: string };

export default function DatasetBrowser() {
  const [path, setPath] = useState("/LQHL/test/20260324");
  const [groups, setGroups] = useState<string[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [labradAvailable, setLabradAvailable] = useState(true);
  const [selectedDs, setSelectedDs] = useState<Dataset | null>(null);
  const [sessionConfig, setSessionConfig] = useState<{ user: string; path: string[] } | null>(null);
  const [showSessionSwitcher, setShowSessionSwitcher] = useState(false);
  const [switchUser, setSwitchUser] = useState("");
  const [switchPath, setSwitchPath] = useState("");

  // Load session config on mount
  useEffect(() => {
    loadSessionConfig();
  }, []);

  // Check LabRAD availability before loading datasets
  const checkLabradAvailable = async (): Promise<boolean> => {
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";
    try {
      const res = await fetch(`${API_BASE}/hardware/quick`);
      if (res.ok) {
        const data = await res.json();
        return data.labrad === "ok";
      }
    } catch { /* ignore */ }
    return false;
  };

  const loadSessionConfig = async () => {
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";
    try {
      const res = await fetch(`${API_BASE}/sessions/config`);
      if (res.ok) {
        const data = await res.json();
        setSessionConfig({ user: data.user, path: data.path });
        setPath("/" + data.user + "/" + data.path.join("/"));
        setSwitchUser(data.user);
        setSwitchPath(data.path.join("/"));
      }
    } catch (e) {
      console.error("Failed to load session config:", e);
    }
  };

  const handleSwitchSession = async () => {
    if (!switchUser || !switchPath) return;
    const pathSegments = switchPath.split("/").filter(Boolean);
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";
    try {
      // First save to config file
      const saveRes = await fetch(`${API_BASE}/sessions/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user: switchUser, path: pathSegments }),
      });

      if (!saveRes.ok) {
        throw new Error("Failed to save config");
      }

      // Then switch session in job_runner.py (this updates the _data object)
      const switchRes = await fetch(`${API_BASE}/sessions/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user: switchUser, path: pathSegments }),
      });

      if (switchRes.ok) {
        await loadSessionConfig();
        setShowSessionSwitcher(false);
        // Reload datasets with new path
        loadDatasets("/" + switchUser + "/" + switchPath);
      } else {
        const errorData = await switchRes.json().catch(() => ({}));
        if (errorData.error?.includes("data_vault") || errorData.error?.includes("NoneType")) {
          setLabradAvailable(false);
          setError("LabRAD 服务器未连接，请先启动测控服务");
        } else {
          throw new Error(errorData.error || "Failed to switch session");
        }
      }
    } catch (e: any) {
      console.error("Failed to switch session:", e);
      setError(e.message);
    }
  };

  const loadDatasets = useCallback(async (p: string) => {
    // Check if LabRAD is available first
    const available = await checkLabradAvailable();
    setLabradAvailable(available);

    if (!available) {
      setLoading(false);
      setError("LabRAD 服务器未连接");
      return;
    }

    setLoading(true);
    setError("");
    try {
      const res = await api.listDatasets(p) as {
        path: string; groups: string[]; datasets: Dataset[];
      };
      setPath(res.path);
      setGroups(res.groups);
      setDatasets(res.datasets);
    } catch (e: any) {
      // Check for LabRAD-related errors
      if (e.message?.includes("data_vault") || e.message?.includes("NoneType")) {
        setLabradAvailable(false);
        setError("LabRAD 服务器未连接");
      } else {
        setError(e.message || "Failed to load datasets");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (labradAvailable) {
      loadDatasets(path);
    }
  }, [loadDatasets, path, labradAvailable]);

  const pathSegments = path ? path.replace(/^\/+|\/+$/g, '').split("/") : [];

  return (
    <div style={{
      border: "1px solid #1e293b",
      borderRadius: "0.5rem",
      background: "#0a0f1a",
      overflow: "hidden",
      display: "flex",
      flexDirection: "column",
      flex: 1,
      minHeight: 0,
    }}>
      {/* Header */}
      <div style={{
        padding: "0.5rem 0.75rem",
        fontSize: "0.7rem", fontWeight: 600,
        color: "#475569", letterSpacing: "0.1em",
        borderBottom: "1px solid #1e293b",
        background: "#0f172a",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span>📂 DATAVAULT BROWSER</span>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <span
            onClick={() => loadDatasets(path)}
            style={{ color: "#38bdf8", cursor: "pointer", fontWeight: 400 }}
            title="Refresh"
          >↻</span>
          <span
            onClick={() => setShowSessionSwitcher(!showSessionSwitcher)}
            style={{ color: "#a78bfa", cursor: "pointer", fontWeight: 400 }}
            title="Switch Session"
          >🎯</span>
        </div>
      </div>

      {/* Session Switcher Panel */}
      {showSessionSwitcher && (
        <div style={{
          padding: "0.5rem 0.75rem",
          borderBottom: "1px solid #1e293b",
          background: "#1e293b",
          display: "flex",
          flexDirection: "column",
          gap: "0.4rem",
        }}>
          <div style={{ fontSize: "0.65rem", color: "#94a3b8", marginBottom: "0.2rem" }}>
            Session Path
          </div>
          <div style={{ display: "flex", gap: "0.3rem", alignItems: "center" }}>
            <input
              type="text"
              value={switchUser}
              onChange={(e) => setSwitchUser(e.target.value)}
              placeholder="User (e.g. LQHL)"
              style={{
                width: "80px",
                padding: "0.25rem 0.4rem",
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: "0.25rem",
                color: "#94a3b8",
                fontSize: "0.7rem",
                fontFamily: "monospace",
              }}
            />
            <span style={{ color: "#475569" }}>/</span>
            <input
              type="text"
              value={switchPath}
              onChange={(e) => setSwitchPath(e.target.value)}
              placeholder="Path (e.g. test/20260324)"
              style={{
                flex: 1,
                padding: "0.25rem 0.4rem",
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: "0.25rem",
                color: "#94a3b8",
                fontSize: "0.7rem",
                fontFamily: "monospace",
              }}
            />
          </div>
          <div style={{ display: "flex", gap: "0.3rem" }}>
            <button
              onClick={handleSwitchSession}
              style={{
                padding: "0.2rem 0.6rem",
                background: "#6366f1",
                border: "none",
                borderRadius: "0.25rem",
                color: "#fff",
                fontSize: "0.65rem",
                cursor: "pointer",
              }}
            >
              Apply
            </button>
            <button
              onClick={() => setShowSessionSwitcher(false)}
              style={{
                padding: "0.2rem 0.6rem",
                background: "transparent",
                border: "1px solid #334155",
                borderRadius: "0.25rem",
                color: "#64748b",
                fontSize: "0.65rem",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Current Session Display */}
      <div style={{
        padding: "0.25rem 0.75rem",
        borderBottom: "1px solid #1e293b",
        fontSize: "0.6rem",
        color: "#64748b",
        fontFamily: "monospace",
        background: "#0f172a",
      }}>
        📍 {sessionConfig?.user || "LQHL"}/{sessionConfig?.path?.join("/") || "test/20260324"}
      </div>

      {/* Breadcrumb */}
      <div style={{
        padding: "0.3rem 0.75rem",
        borderBottom: "1px solid #1e293b",
        display: "flex", alignItems: "center", gap: "0.25rem",
        fontFamily: "monospace", fontSize: "0.65rem",
        overflow: "hidden",
      }}>
        <span
          onClick={() => loadDatasets("")}
          style={{ color: "#38bdf8", cursor: "pointer", flexShrink: 0 }}
        >
          /
        </span>
        {pathSegments.map((seg, i) => (
          <span key={i} style={{ display: "flex", alignItems: "center" }}>
            <span style={{ color: "#334569" }}>/</span>
            <span
              onClick={() => loadDatasets("/" + pathSegments.slice(0, i + 1).join("/"))}
              style={{ color: i === pathSegments.length - 1 ? "#94a3b8" : "#38bdf8", cursor: "pointer" }}
            >
              {seg}
            </span>
          </span>
        ))}
      </div>

      {/* Groups */}
      {!loading && groups.length > 0 && (
        <div style={{
          padding: "0.4rem 0.75rem",
          borderBottom: "1px solid #1e293b",
          display: "flex", flexWrap: "wrap", gap: "0.25rem",
        }}>
          {groups.map((g) => (
            <button
              key={g}
              onClick={() => loadDatasets((path ? path + "/" : "") + g)}
              style={{
                padding: "0.2rem 0.5rem",
                borderRadius: "0.25rem",
                border: "1px solid #334155",
                background: "#1e293b",
                color: "#94a3b8",
                cursor: "pointer",
                fontSize: "0.65rem",
                fontFamily: "monospace",
              }}
            >
              📁 {g}
            </button>
          ))}
        </div>
      )}

      {/* Error / LabRAD unavailable message */}
      {error && (
        <div style={{
          margin: "0.75rem",
          padding: "0.75rem",
          background: !labradAvailable ? "#422006" : "#451a1a",
          border: `1px solid ${!labradAvailable ? "#f59e0b" : "#ef4444"}`,
          borderRadius: "0.5rem",
        }}>
          {!labradAvailable ? (
            <>
              <div style={{ fontSize: "0.8rem", color: "#fbbf24", fontWeight: 600, marginBottom: "0.5rem" }}>
                ⚠️ LabRAD 服务器未连接
              </div>
              <div style={{ fontSize: "0.7rem", color: "#fcd34d", lineHeight: 1.6 }}>
                请在「服务控制」面板中启动测控服务后再使用 DataVault 功能。
              </div>
            </>
          ) : (
            <div style={{ fontSize: "0.7rem", color: "#f87171" }}>{error}</div>
          )}
        </div>
      )}

      {/* Datasets */}
      <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {loading && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            Loading...
          </div>
        )}
        {!loading && !error && !labradAvailable && (
          <div style={{ padding: "1rem", color: "#f59e0b", fontSize: "0.75rem", textAlign: "center" }}>
            请先启动测控服务
          </div>
        )}
        {!loading && !error && labradAvailable && datasets.map((ds) => (
          <div
            key={ds.id}
            onClick={() => setSelectedDs(selectedDs?.id === ds.id ? null : ds)}
            style={{
              padding: "0.35rem 0.75rem",
              borderBottom: "1px solid #1e293b",
              cursor: "pointer",
              background: selectedDs?.id === ds.id ? "#1e3a5f" : "transparent",
            }}
          >
            <div style={{
              fontFamily: "monospace", fontSize: "0.7rem",
              color: selectedDs?.id === ds.id ? "#38bdf8" : "#94a3b8",
            }}>
              📊 {ds.name}
            </div>
          </div>
        ))}
        {!loading && !error && datasets.length === 0 && groups.length === 0 && labradAvailable && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            No datasets in this folder
          </div>
        )}
      </div>

      {/* Selected dataset actions */}
      {selectedDs && (
        <div style={{
          borderTop: "1px solid #1e293b",
          padding: "0.5rem 0.75rem",
          background: "#0f172a",
          display: "flex",
          flexDirection: "column",
          gap: "0.35rem",
        }}>
          <div style={{
            fontSize: "0.65rem", color: "#475569",
            fontFamily: "monospace",
          }}>
            📊 {selectedDs.name}
          </div>
          <button
            onClick={() => {
              // Dispatch event to plot this dataset in the experiments page
              window.dispatchEvent(new CustomEvent("dataset:plot-in-experiments", {
                detail: {
                  name: selectedDs.id,
                  path: path,
                }
              }));
            }}
            style={{
              padding: "0.35rem 0.75rem",
              background: "#6366f1",
              border: "none",
              borderRadius: "0.25rem",
              color: "#fff",
              fontSize: "0.7rem",
              fontWeight: 600,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "0.4rem",
            }}
          >
            📊 在 Experiments 绘图
          </button>
        </div>
      )}
    </div>
  );
}
