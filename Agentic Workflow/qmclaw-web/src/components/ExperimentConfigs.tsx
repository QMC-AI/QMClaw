"use client";

import { useState, useEffect } from "react";
import { api, ExperimentConfig } from "../lib/api";

interface ExperimentConfigsProps {
  onClose?: () => void;
}

export default function ExperimentConfigs({ onClose }: ExperimentConfigsProps) {
  const [configs, setConfigs] = useState<Record<string, ExperimentConfig>>({});
  const [loading, setLoading] = useState(true);
  const [editingType, setEditingType] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<ExperimentConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    loadConfigs();
  }, []);

  const loadConfigs = async () => {
    setLoading(true);
    try {
      const data = await api.getExperimentConfigs();
      if (data.configs) {
        const configs = data.configs as unknown as Record<string, Record<string, ExperimentConfig>>;
        // The API returns { configs: { experiments: {...} } }
        if (configs.experiments) {
          setConfigs(configs.experiments);
        }
      }
    } catch (e: any) {
      setMessage("Failed to load configs: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = (type: string) => {
    setEditingType(type);
    setEditForm({ ...configs[type] });
  };

  const handleSave = async () => {
    if (!editingType || !editForm) return;
    setSaving(true);
    try {
      await api.updateExperimentConfig(editingType, editForm);
      await loadConfigs();
      setEditingType(null);
      setEditForm(null);
      setMessage("Config saved successfully");
    } catch (e: any) {
      setMessage("Failed to save: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditingType(null);
    setEditForm(null);
  };

  const handleDelete = async (type: string) => {
    // Reset to default config
    setSaving(true);
    try {
      const defaultConfigs: Record<string, ExperimentConfig> = {
        spectroscopy: {
          name: "Spectroscopy",
          description: "VNA spectroscopy — broad frequency scan to find qubit resonance",
          function: "sq.spectroscopy",
          defaultPlotCommand: "plt.title('Spectroscopy')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('S21 (dB)')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: [],
        },
        s21: {
          name: "Cavity S21",
          description: "Cavity S21 — narrowband frequency scan around cavity resonance",
          function: "sq.s21",
          defaultPlotCommand: "plt.title('Cavity S21')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('S21 (dB)')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: [],
        },
        iqraw: {
          name: "IQ Raw",
          description: "Acquire raw I/Q data for qubit state discrimination",
          function: "sq.iqraw",
          defaultPlotCommand: "plt.title('IQ Raw Data')\nplt.xlabel('I')\nplt.ylabel('Q')\nplt.grid(True)\nplt.axis('equal')",
          defaultAnalysisCommand: "qter.fitData(-1, collect=True, do_plot=False)",
          metricsToExtract: ["SNR", "F0", "F1", "separation"],
        },
        t1: {
          name: "T1 Relaxation",
          description: "Measure qubit relaxation time via variable delay pulse sequence",
          function: "sq.t1",
          defaultPlotCommand: "plt.title('T1 Relaxation')\nplt.xlabel('Delay (ns)')\nplt.ylabel('Population')\nplt.grid(True)",
          defaultAnalysisCommand: "dp.T1(data)",
          metricsToExtract: ["T1"],
        },
        ramsey: {
          name: "Ramsey",
          description: "Ramsey with detuning — measure T2* dephasing time",
          function: "sq.ramsey",
          defaultPlotCommand: "plt.title('Ramsey Interference')\nplt.xlabel('Delay (ns)')\nplt.ylabel('Population')\nplt.grid(True)",
          defaultAnalysisCommand: "dp.Ramsey(data)",
          metricsToExtract: ["T2", "detuning"],
        },
        piamp: {
          name: "Pi Pulse Amplitude",
          description: "Calibrate π-pulse amplitude for X gate via Rabi oscillation",
          function: "sq.piamp",
          defaultPlotCommand: "plt.title('Pi Pulse Calibration')\nplt.xlabel('Amplitude')\nplt.ylabel('Population')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: ["pi_amplitude"],
        },
        xeb: {
          name: "Cross-Entropy Benchmarking",
          description: "Measure single-qubit gate fidelity",
          function: "sq.xeb",
          defaultPlotCommand: "plt.title('Cross-Entropy Benchmarking')\nplt.xlabel('Cycles')\nplt.ylabel('Fidelity')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: ["gate_fidelity", "error_per_cycle"],
        },
        s21_dis: {
          name: "S21 Dispersive Shift",
          description: "Measure cavity transmission shift vs qubit state",
          function: "sq.s21_dis",
          defaultPlotCommand: "plt.title('Dispersive Shift')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('S21 (dB)')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: ["dispersive_shift"],
        },
        allxy: {
          name: "AllXY",
          description: "Characterize all 21 gate error combinations",
          function: "sq.allxy",
          defaultPlotCommand: "plt.title('AllXY Characterization')\nplt.xlabel('Gate Pair')\nplt.ylabel('Fidelity')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: ["average_fidelity", "leakage"],
        },
        single_shot: {
          name: "Single-shot Fidelity",
          description: "Measure qubit readout fidelity in single-shot regime",
          function: "sq.single_shot",
          defaultPlotCommand: "plt.title('Single Shot Fidelity')\nplt.xlabel('I')\nplt.ylabel('Q')\nplt.grid(True)\nplt.axis('equal')",
          defaultAnalysisCommand: "",
          metricsToExtract: ["readout_fidelity"],
        },
        pulsed_spec: {
          name: "Pulsed Spectroscopy",
          description: "Qubit spectroscopy with pump pulse for higher SNR",
          function: "sq.pulsed_spec",
          defaultPlotCommand: "plt.title('Pulsed Spectroscopy')\nplt.xlabel('Frequency (Hz)')\nplt.ylabel('Population')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: ["qubit_frequency"],
        },
        swap: {
          name: "SWAP",
          description: "Characterize SWAP gate for two-qubit operations",
          function: "sq.swap",
          defaultPlotCommand: "plt.title('SWAP Characterization')\nplt.xlabel('Duration (ns)')\nplt.ylabel('Fidelity')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: ["swap_fidelity"],
        },
        drag_calibrate: {
          name: "DRAG Calibration",
          description: "Optimize DRAG coefficient for leakage suppression",
          function: "sq.drag_calibrate",
          defaultPlotCommand: "plt.title('DRAG Calibration')\nplt.xlabel('DRAG Coefficient')\nplt.ylabel('Fidelity')\nplt.grid(True)",
          defaultAnalysisCommand: "",
          metricsToExtract: ["optimal_drag"],
        }
      };
      await api.updateExperimentConfig(type, defaultConfigs[type]);
      await loadConfigs();
      setMessage("Config reset to default");
    } catch (e: any) {
      setMessage("Failed to reset: " + e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{
      position: "fixed",
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      background: "rgba(0,0,0,0.8)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: 1000,
    }}>
      <div style={{
        background: "#0f172a",
        border: "1px solid #334155",
        borderRadius: "0.5rem",
        width: "90%",
        maxWidth: "900px",
        maxHeight: "90vh",
        display: "flex",
        flexDirection: "column",
      }}>
        {/* Header */}
        <div style={{
          padding: "1rem",
          borderBottom: "1px solid #334155",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}>
          <h2 style={{ margin: 0, fontSize: "1.1rem", color: "#e2e8f0" }}>
            ⚙️ Experiment Configurations
          </h2>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            {message && (
              <span style={{ color: "#22c55e", fontSize: "0.8rem" }}>{message}</span>
            )}
            <button
              onClick={onClose}
              style={{
                background: "transparent",
                border: "1px solid #475569",
                color: "#94a3b8",
                padding: "0.25rem 0.75rem",
                borderRadius: "0.25rem",
                cursor: "pointer",
              }}
            >
              Close
            </button>
          </div>
        </div>

        {/* Content */}
        <div style={{
          flex: 1,
          overflow: "auto",
          padding: "1rem",
        }}>
          {loading ? (
            <div style={{ textAlign: "center", color: "#64748b" }}>Loading...</div>
          ) : editingType && editForm ? (
            /* Edit Form */
            <div>
              <h3 style={{ margin: "0 0 1rem 0", color: "#e2e8f0", fontSize: "1rem" }}>
                Edit: {configs[editingType]?.name}
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <div>
                  <label style={{ display: "block", color: "#94a3b8", fontSize: "0.8rem", marginBottom: "0.25rem" }}>
                    Name
                  </label>
                  <input
                    type="text"
                    value={editForm.name}
                    onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                    style={{
                      width: "100%",
                      background: "#1e293b",
                      border: "1px solid #334155",
                      color: "#e2e8f0",
                      padding: "0.5rem",
                      borderRadius: "0.25rem",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", color: "#94a3b8", fontSize: "0.8rem", marginBottom: "0.25rem" }}>
                    Experiment Function
                  </label>
                  <input
                    type="text"
                    value={editForm.function}
                    onChange={(e) => setEditForm({ ...editForm, function: e.target.value })}
                    style={{
                      width: "100%",
                      background: "#1e293b",
                      border: "1px solid #334155",
                      color: "#e2e8f0",
                      padding: "0.5rem",
                      borderRadius: "0.25rem",
                      fontFamily: "monospace",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", color: "#94a3b8", fontSize: "0.8rem", marginBottom: "0.25rem" }}>
                    Description
                  </label>
                  <input
                    type="text"
                    value={editForm.description}
                    onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                    style={{
                      width: "100%",
                      background: "#1e293b",
                      border: "1px solid #334155",
                      color: "#e2e8f0",
                      padding: "0.5rem",
                      borderRadius: "0.25rem",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", color: "#94a3b8", fontSize: "0.8rem", marginBottom: "0.25rem" }}>
                    Default Plot Command
                  </label>
                  <textarea
                    value={editForm.defaultPlotCommand}
                    onChange={(e) => setEditForm({ ...editForm, defaultPlotCommand: e.target.value })}
                    rows={8}
                    style={{
                      width: "100%",
                      background: "#1e293b",
                      border: "1px solid #334155",
                      color: "#22d3ee",
                      padding: "0.5rem",
                      borderRadius: "0.25rem",
                      fontFamily: "monospace",
                      fontSize: "0.8rem",
                      resize: "vertical",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", color: "#94a3b8", fontSize: "0.8rem", marginBottom: "0.25rem" }}>
                    Default Analyse Command
                  </label>
                  <textarea
                    value={String(editForm.defaultAnalysisCommand || "")}
                    onChange={(e) => setEditForm({ ...editForm, defaultAnalysisCommand: e.target.value })}
                    rows={6}
                    style={{
                      width: "100%",
                      background: "#1e293b",
                      border: "1px solid #334155",
                      color: "#22c55e",
                      padding: "0.5rem",
                      borderRadius: "0.25rem",
                      fontFamily: "monospace",
                      fontSize: "0.8rem",
                      resize: "vertical",
                    }}
                    placeholder="e.g., qter.fitData(-1, collect=True, do_plot=False)"
                  />
                  <div style={{ color: "#64748b", fontSize: "0.7rem", marginTop: "0.25rem" }}>
                    Analysis command executed after experiment with do_plot=False, collect=True
                  </div>
                </div>
                <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end" }}>
                  <button
                    onClick={handleCancel}
                    style={{
                      background: "transparent",
                      border: "1px solid #475569",
                      color: "#94a3b8",
                      padding: "0.5rem 1rem",
                      borderRadius: "0.25rem",
                      cursor: "pointer",
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    style={{
                      background: "#3b82f6",
                      border: "none",
                      color: "white",
                      padding: "0.5rem 1rem",
                      borderRadius: "0.25rem",
                      cursor: saving ? "not-allowed" : "pointer",
                      opacity: saving ? 0.6 : 1,
                    }}
                  >
                    {saving ? "Saving..." : "Save"}
                  </button>
                </div>
              </div>
            </div>
          ) : (
            /* Config List */
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #334155" }}>
                  <th style={{ textAlign: "left", padding: "0.5rem", color: "#94a3b8", fontSize: "0.75rem" }}>Type</th>
                  <th style={{ textAlign: "left", padding: "0.5rem", color: "#94a3b8", fontSize: "0.75rem" }}>Name</th>
                  <th style={{ textAlign: "left", padding: "0.5rem", color: "#94a3b8", fontSize: "0.75rem" }}>Function</th>
                  <th style={{ textAlign: "left", padding: "0.5rem", color: "#94a3b8", fontSize: "0.75rem" }}>Plot</th>
                  <th style={{ textAlign: "left", padding: "0.5rem", color: "#94a3b8", fontSize: "0.75rem" }}>Analysis</th>
                  <th style={{ textAlign: "right", padding: "0.5rem", color: "#94a3b8", fontSize: "0.75rem" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(configs).map(([type, config]) => (
                  <tr key={type} style={{ borderBottom: "1px solid #1e293b" }}>
                    <td style={{ padding: "0.5rem", color: "#64748b", fontSize: "0.8rem", fontFamily: "monospace" }}>{type}</td>
                    <td style={{ padding: "0.5rem", color: "#e2e8f0", fontSize: "0.8rem" }}>{config.name}</td>
                    <td style={{ padding: "0.5rem", color: "#22d3ee", fontSize: "0.8rem", fontFamily: "monospace" }}>{config.function}</td>
                    <td style={{ padding: "0.5rem", color: "#64748b", fontSize: "0.75rem", maxWidth: "150px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {config.defaultPlotCommand?.split('\n')[0] || "(no command)"}
                    </td>
                    <td style={{ padding: "0.5rem", color: config.defaultAnalysisCommand ? "#22c55e" : "#475569", fontSize: "0.75rem", maxWidth: "150px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {String(config.defaultAnalysisCommand || "").split('(')[0] || "(none)"}
                    </td>
                    <td style={{ padding: "0.5rem", textAlign: "right" }}>
                      <button
                        onClick={() => handleEdit(type)}
                        style={{
                          background: "#3b82f6",
                          border: "none",
                          color: "white",
                          padding: "0.25rem 0.5rem",
                          borderRadius: "0.25rem",
                          cursor: "pointer",
                          fontSize: "0.75rem",
                          marginRight: "0.25rem",
                        }}
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(type)}
                        style={{
                          background: "transparent",
                          border: "1px solid #ef4444",
                          color: "#ef4444",
                          padding: "0.25rem 0.5rem",
                          borderRadius: "0.25rem",
                          cursor: "pointer",
                          fontSize: "0.75rem",
                        }}
                      >
                        Reset
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
