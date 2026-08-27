"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

// Parameter categories
type ParamCategory = {
  name: string;
  icon: string;
  params: string[];
};

const PARAM_CATEGORIES: ParamCategory[] = [
  {
    name: "Basic",
    icon: "📡",
    params: ["f10", "fread", "fc", "f21", "bias_z"],
  },
  {
    name: "PiGate",
    icon: "🔲",
    params: ["PiGate.amp", "PiGate.length", "PiGate.alpha", "PiGate.zpa"],
  },
  {
    name: "PiHalf",
    icon: "◧",
    params: ["PiHalf.amp", "PiHalf.length", "PiHalf.alpha", "PiHalf.zpa"],
  },
  {
    name: "ReadIn",
    icon: "📥",
    params: ["ReadIn.power", "ReadIn.length", "ReadIn.ring_power", "ReadIn.ring_length", "ReadIn.zpa"],
  },
  {
    name: "ReadOut",
    icon: "📤",
    params: ["ReadOut.amp", "ReadOut.length", "ReadOut.window_type"],
  },
  {
    name: "Discriminator",
    icon: "⚖️",
    params: [
      "discriminator.center0", "discriminator.center1",
      "discriminator.measure_f0", "discriminator.measure_f1",
      "discriminator.method", "discriminator.radius0", "discriminator.threshold"
    ],
  },
];

// Parameter metadata for display
const PARAM_META: Record<string, { label: string; unit?: string }> = {
  f10: { label: "Qubit Freq", unit: "GHz" },
  fread: { label: "Readout Freq", unit: "GHz" },
  fc: { label: "Coupler Freq", unit: "GHz" },
  f21: { label: "f21 Freq", unit: "GHz" },
  bias_z: { label: "Bias Z", unit: "V" },
  "PiGate.amp": { label: "Amplitude", unit: "" },
  "PiGate.length": { label: "Length", unit: "ns" },
  "PiGate.alpha": { label: "DRAG Alpha", unit: "" },
  "PiGate.zpa": { label: "ZPA", unit: "" },
  "PiHalf.amp": { label: "Amplitude", unit: "" },
  "PiHalf.length": { label: "Length", unit: "ns" },
  "PiHalf.alpha": { label: "DRAG Alpha", unit: "" },
  "PiHalf.zpa": { label: "ZPA", unit: "" },
  "ReadIn.power": { label: "Power", unit: "dBm" },
  "ReadIn.length": { label: "Length", unit: "ns" },
  "ReadIn.ring_power": { label: "Ring Power", unit: "dBm" },
  "ReadIn.ring_length": { label: "Ring Length", unit: "ns" },
  "ReadIn.zpa": { label: "ZPA", unit: "" },
  "ReadOut.amp": { label: "Amplitude", unit: "" },
  "ReadOut.length": { label: "Length", unit: "ns" },
  "ReadOut.window_type": { label: "Window Type", unit: "" },
  "discriminator.center0": { label: "Center 0", unit: "" },
  "discriminator.center1": { label: "Center 1", unit: "" },
  "discriminator.measure_f0": { label: "Measure f0", unit: "GHz" },
  "discriminator.measure_f1": { label: "Measure f1", unit: "GHz" },
  "discriminator.method": { label: "Method", unit: "" },
  "discriminator.radius0": { label: "Radius 0", unit: "" },
  "discriminator.threshold": { label: "Threshold", unit: "" },
};

interface QubitParamsPanelProps {
  qubitName: string;
  onClose: () => void;
  onSaved?: () => void;
}

export default function QubitParamsPanel({ qubitName, onClose, onSaved }: QubitParamsPanelProps) {
  const [params, setParams] = useState<Record<string, number | null>>({});
  const [editParams, setEditParams] = useState<Record<string, string>>({});
  const [sessionPath, setSessionPath] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set(["Basic", "PiGate"]));
  const [hasChanges, setHasChanges] = useState(false);

  // Load qubit parameters
  const loadParams = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getQubitParams(qubitName) as { name: string; sessionPath: string[]; params: Record<string, number | null> };
      setParams(data.params || {});
      setSessionPath(data.sessionPath || []);
      // Initialize edit values
      const edits: Record<string, string> = {};
      for (const [key, value] of Object.entries(data.params || {})) {
        edits[key] = value !== null ? String(value) : "";
      }
      setEditParams(edits);
      setHasChanges(false);
    } catch (e: any) {
      setError(e.message || "Failed to load parameters");
    } finally {
      setLoading(false);
    }
  }, [qubitName]);

  useEffect(() => {
    loadParams();
  }, [loadParams]);

  // Handle input change
  const handleChange = (key: string, value: string) => {
    setEditParams(prev => ({ ...prev, [key]: value }));
    // Check if value changed from original
    const original = params[key];
    const newVal = value === "" ? null : parseFloat(value);
    setHasChanges(newVal !== original);
  };

  // Toggle category expansion
  const toggleCategory = (name: string) => {
    setExpandedCategories(prev => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  // Save parameters
  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      // Convert string values to numbers
      const updateParams: Record<string, number | null> = {};
      for (const [key, value] of Object.entries(editParams)) {
        updateParams[key] = value === "" ? null : parseFloat(value);
      }

      const result = await api.setQubitParams(qubitName, updateParams) as {
        success: boolean;
        errors?: string[];
      };

      if (result.success) {
        // Reload to get confirmed values
        await loadParams();
        onSaved?.();
      } else if (result.errors?.length) {
        setError(`Errors: ${result.errors.join(", ")}`);
      }
    } catch (e: any) {
      setError(e.message || "Failed to save parameters");
    } finally {
      setSaving(false);
    }
  };

  // Discard changes
  const handleDiscard = () => {
    const edits: Record<string, string> = {};
    for (const [key, value] of Object.entries(params)) {
      edits[key] = value !== null ? String(value) : "";
    }
    setEditParams(edits);
    setHasChanges(false);
  };

  return (
    <div style={{
      position: "fixed",
      top: 0, left: 0, right: 0, bottom: 0,
      background: "rgba(0,0,0,0.7)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: 2000,
    }}>
      <div style={{
        background: "#0f172a",
        border: "1px solid #334155",
        borderRadius: "0.75rem",
        width: "500px",
        maxHeight: "80vh",
        display: "flex",
        flexDirection: "column",
        boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
      }}>
        {/* Header */}
        <div style={{
          padding: "1rem",
          borderBottom: "1px solid #334155",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}>
          <div>
            <div style={{ fontSize: "0.65rem", color: "#6366f1", fontWeight: 600, letterSpacing: "0.1em" }}>
              QUBIT PARAMETERS
            </div>
            <div style={{ fontSize: "1rem", color: "#e2e8f0", fontFamily: "monospace", marginTop: "0.25rem" }}>
              ⚙️ {qubitName}
            </div>
            {sessionPath.length > 0 && (
              <div style={{ fontSize: "0.7rem", color: "#64748b", fontFamily: "monospace", marginTop: "0.15rem" }}>
                📂 {sessionPath.slice(1).join(" > ")}  {/* Skip empty first element */}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "#64748b",
              cursor: "pointer",
              fontSize: "1.25rem",
              padding: "0.25rem",
            }}
          >
            ✕
          </button>
        </div>

        {/* Error message */}
        {error && (
          <div style={{
            padding: "0.75rem 1rem",
            background: "#7f1d1d",
            color: "#fca5a5",
            fontSize: "0.8rem",
          }}>
            ❌ {error}
          </div>
        )}

        {/* Content */}
        <div style={{ flex: 1, overflow: "auto", padding: "0.5rem" }}>
          {loading ? (
            <div style={{ padding: "2rem", textAlign: "center", color: "#64748b" }}>
              ⏳ Loading parameters...
            </div>
          ) : (
            PARAM_CATEGORIES.map(cat => {
              const catParams = cat.params.filter(p => p in editParams);
              if (catParams.length === 0) return null;
              const isExpanded = expandedCategories.has(cat.name);

              return (
                <div key={cat.name} style={{ marginBottom: "0.5rem" }}>
                  {/* Category header */}
                  <div
                    onClick={() => toggleCategory(cat.name)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "0.5rem",
                      padding: "0.5rem 0.75rem",
                      background: "#1e293b",
                      borderRadius: "0.375rem",
                      cursor: "pointer",
                    }}
                  >
                    <span style={{ fontSize: "0.9rem" }}>{cat.icon}</span>
                    <span style={{ flex: 1, fontSize: "0.8rem", fontWeight: 600, color: "#94a3b8" }}>
                      {cat.name}
                    </span>
                    <span style={{ color: "#64748b", fontSize: "0.7rem" }}>
                      {isExpanded ? "▼" : "▶"}
                    </span>
                  </div>

                  {/* Category params */}
                  {isExpanded && (
                    <div style={{ padding: "0.5rem 0" }}>
                      {catParams.map(key => {
                        const meta = PARAM_META[key] || { label: key, unit: "" };
                        const value = editParams[key] || "";

                        return (
                          <div key={key} style={{
                            display: "flex",
                            alignItems: "center",
                            padding: "0.35rem 0.5rem",
                            gap: "0.5rem",
                          }}>
                            <div style={{ flex: 1, fontSize: "0.75rem", color: "#94a3b8" }}>
                              {meta.label}
                              {meta.unit && (
                                <span style={{ color: "#475569", marginLeft: "0.25rem" }}>
                                  ({meta.unit})
                                </span>
                              )}
                            </div>
                            <input
                              type="text"
                              value={value}
                              onChange={e => handleChange(key, e.target.value)}
                              style={{
                                width: "120px",
                                padding: "0.35rem 0.5rem",
                                background: "#0f172a",
                                border: "1px solid #334155",
                                borderRadius: "0.25rem",
                                color: "#e2e8f0",
                                fontFamily: "monospace",
                                fontSize: "0.75rem",
                                textAlign: "right",
                              }}
                            />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: "1rem",
          borderTop: "1px solid #334155",
          display: "flex",
          justifyContent: "flex-end",
          gap: "0.5rem",
        }}>
          <button
            onClick={onClose}
            style={{
              padding: "0.5rem 1rem",
              background: "transparent",
              border: "1px solid #334155",
              borderRadius: "0.375rem",
              color: "#94a3b8",
              cursor: "pointer",
              fontSize: "0.8rem",
            }}
          >
            Cancel
          </button>
          {hasChanges && (
            <button
              onClick={handleDiscard}
              style={{
                padding: "0.5rem 1rem",
                background: "transparent",
                border: "1px solid #f59e0b",
                borderRadius: "0.375rem",
                color: "#f59e0b",
                cursor: "pointer",
                fontSize: "0.8rem",
              }}
            >
              Discard
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saving || !hasChanges}
            style={{
              padding: "0.5rem 1rem",
              background: hasChanges ? "#22c55e" : "#1e293b",
              border: "1px solid #22c55e",
              borderRadius: "0.375rem",
              color: hasChanges ? "#fff" : "#64748b",
              cursor: hasChanges ? "pointer" : "not-allowed",
              fontSize: "0.8rem",
              fontWeight: 600,
            }}
          >
            {saving ? "⏳ Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
