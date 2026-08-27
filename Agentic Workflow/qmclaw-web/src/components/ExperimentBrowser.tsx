"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

type ExpInfo = { name: string; fullName: string; doc: string };

export default function ExperimentBrowser() {
  const [experiments, setExperiments] = useState<ExpInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const loadExperiments = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.listExperiments() as { experiments: ExpInfo[] };
      setExperiments(res.experiments || []);
    } catch (e: any) {
      setError(e.message || "Failed to load experiments");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadExperiments(); }, [loadExperiments]);

  const filtered = filter
    ? experiments.filter((e) => e.name.includes(filter) || e.doc.includes(filter))
    : experiments;

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
        fontSize: "0.7rem", fontWeight: 600,
        color: "#475569", letterSpacing: "0.1em",
        borderBottom: "1px solid #1e293b",
        background: "#0f172a",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
       <span>🔬 LABRAD EXPERIMENTS</span>
        <span style={{ color: "#38bdf8", cursor: "pointer", fontWeight: 400 }} onClick={loadExperiments}>
          ↻ {experiments.length} fns
        </span>
      </div>

      {/* Filter */}
      <div style={{ padding: "0.4rem 0.75rem", borderBottom: "1px solid #1e293b" }}>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter experiments..."
          style={{
            width: "100%", padding: "0.3rem 0.5rem",
            background: "#1e293b", color: "#e2e8f0",
            border: "1px solid #334155", borderRadius: "0.25rem",
            fontFamily: "monospace", fontSize: "0.7rem",
            boxSizing: "border-box",
          }}
        />
      </div>

      {/* List */}
      <div style={{ maxHeight: "320px", overflow: "auto" }}>
        {loading && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            Loading...
          </div>
        )}
        {error && (
          <div style={{ padding: "0.75rem", color: "#f87171", fontSize: "0.7rem" }}>{error}</div>
        )}
        {!loading && !error && filtered.map((exp) => (
          <div
            key={exp.name}
            onClick={() => setSelected(exp.name === selected ? null : exp.name)}
            style={{
              padding: "0.4rem 0.75rem",
              borderBottom: "1px solid #1e293b",
              cursor: "pointer",
              background: exp.name === selected ? "#1e3a5f" : "transparent",
            }}
          >
            <div style={{
              fontFamily: "monospace", fontSize: "0.72rem",
              color: exp.name === selected ? "#38bdf8" : "#94a3b8",
            }}>
              {exp.fullName}
            </div>
            {exp.doc && (
              <div style={{
                fontSize: "0.6rem", color: "#475569", marginTop: "0.1rem",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {exp.doc}
              </div>
            )}
          </div>
        ))}
        {!loading && !error && filtered.length === 0 && (
          <div style={{ padding: "1rem", color: "#334569", fontSize: "0.75rem", textAlign: "center" }}>
            No experiments match filter
          </div>
        )}
      </div>
    </div>
  );
}