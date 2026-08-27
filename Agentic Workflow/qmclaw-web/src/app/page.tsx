"use client";

/**
 * qmclaw Dashboard - Quantum Measurement & Calibration Workflow
 *
 * Architecture:
 *   Browser → Express (:3002) → Python subprocess (LabRAD + lqms)
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { api, Metrics, ExperimentConfig } from "../lib/api";
import EditableCommand from "../components/EditableCommand";
import PlotCommandEditor from "../components/PlotCommandEditor";
import JobManager from "../components/JobManager";
import WorkflowDesigner from "../components/WorkflowDesigner";
import { CompactSessionManager } from "../components/SessionManager";
import DatasetBrowser from "../components/DatasetBrowser";
import ServiceControlPanel from "../components/ServiceControlPanel";
import QubitParamsPanel from "../components/QubitParamsPanel";
import ModelRegistry from "../components/ModelRegistry";
import ExperimentConfigs from "../components/ExperimentConfigs";
import { useModelStore } from "../store/modelStore";

// Plots directory (must match Express server)
// Uses relative path from project root, or PLOTS_DIR env var
const PLOTS_DIR = process.env.PLOTS_DIR || "/plots";

// ── Server health check (status dots + hardware status) ──────────────────────────────────────────

interface QuickStatus {
  labrad: string;
  ray: string;
  datavault: string;
  message: string;
}

function StatusDot({ label, status }: { label: string; status: string }) {
  const colors: Record<string, string> = {
    ok: "#22c55e",
    warning: "#f59e0b",
    error: "#ef4444",
  };
  const color = colors[status] || "#64748b";
  return (
    <span title={`${label}: ${status}`} style={{ color, fontSize: "0.75rem" }}>
      {label} {status === "ok" ? "✅" : status === "warning" ? "⚠️" : "❌"}
    </span>
  );
}

async function checkHealth(
  setServerOk: (v: boolean) => void,
  setFlaskOk: (v: boolean) => void,
  setQuickStatus: (s: QuickStatus | null) => void
) {
  try {
    const health = await api.ping() as {
      express: string; flask: { status: string; ready: boolean } | "unreachable";
    };
    setServerOk(true);
    setFlaskOk(health.flask !== "unreachable" && health.flask?.ready === true);
    // Also fetch quick hardware status
    try {
      const qs = await api.getQuickStatus() as QuickStatus;
      setQuickStatus(qs);
    } catch {
      setQuickStatus(null);
    }
  } catch {
    setServerOk(false);
    setFlaskOk(false);
    setQuickStatus(null);
  }
}

// ── localStorage keys ──────────────────────────────────────────────────────────
const STORAGE_KEYS = {
  qubits: "qmclaw.qubits",
  selectedQubit: "qmclaw.selectedQubit",
  selectedExp: "qmclaw.selectedExp",
  defaultSession: "qmclaw.defaultSession",
};

function loadSavedArray(key: string, fallback: string[]): string[] {
  try {
    const saved = localStorage.getItem(key);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch { /* ignore */ }
  return fallback;
}

function loadSavedStr(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) || fallback;
  } catch { return fallback; }
}

function saveStr(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

function saveArray(key: string, value: string[]) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
}

type Tab = "experiments" | "jobs" | "workflow" | "services";
type ExpType = "spectroscopy" | "s21" | "iqraw" | "t1" | "xeb" | "ramsey" | "piamp" | "s21_dis" | "allxy" | "single_shot" | "pulsed_spec" | "swap" | "drag_calibrate";

// ── Default values (used for SSR and fallback) ────────────────────────────────
const defaultQubits: string[] = [];  // Will be loaded from backend
const defaultSelectedQubit = "";     // Will be set after loading qubits
const defaultSelectedExp: ExpType = "spectroscopy";

// ── ActionBtn ─────────────────────────────────────────────────────────────────

function ActionBtn({ label, on, color, disabled }: { label: string; on: () => void; color?: string; disabled?: boolean }) {
  return (
    <button onClick={on} disabled={disabled} style={{
      padding: "0.35rem 0.75rem", borderRadius: "0.375rem",
      border: "1px solid #334155",
      background: disabled ? "#1e293b" : (color ? color : "#1e293b"),
      color: disabled ? "#475569" : "#e2e8f0",
      cursor: disabled ? "not-allowed" : "pointer",
      fontSize: "0.8rem", opacity: disabled ? 0.6 : 1,
    }}>
      {label}
    </button>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [serverOk, setServerOk] = useState(false);
  const [flaskOk, setFlaskOk] = useState(false);
  const [quickStatus, setQuickStatus] = useState<QuickStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  // Qubit list — persisted (SSR uses defaults, client hydrates from localStorage)
  const [qubits, setQubits] = useState<string[]>(defaultQubits);
  const [selectedQubit, setSelectedQubitState] = useState<string>(defaultSelectedQubit);
  const [newQubitName, setNewQubitName] = useState<string>("");

  // Qubit params panel state
  const [paramsQubit, setParamsQubit] = useState<string | null>(null);

  // Model registry modal
  const [showModelRegistry, setShowModelRegistry] = useState(false);

  // Experiment configs modal
  const [showExperimentConfigs, setShowExperimentConfigs] = useState(false);

  // Experiment configs loaded from backend
  const [experimentConfigs, setExperimentConfigs] = useState<Record<string, {
    name: string;
    description: string;
    function: string;
    defaultPlotCommand: string;
  }>>({});

  // Load experiment configs from backend on mount
  useEffect(() => {
    api.getExperimentConfigs().then(data => {
      if (data.success && data.configs) {
        // API returns { configs: { experiments: Record<string, Config> } }
        const configs = data.configs as unknown as Record<string, Record<string, ExperimentConfig>>;
        if (configs.experiments) {
          setExperimentConfigs(configs.experiments);
        }
      }
    }).catch(console.error);
  }, []);

  // Load models on mount — use getState() to avoid selector causing re-render loops
  useEffect(() => {
    useModelStore.getState().fetchModels();
  }, []);

  // Listen for model registry open event from child components
  useEffect(() => {
    const handler = () => setShowModelRegistry(true);
    window.addEventListener('qmclaw:open-model-registry', handler);
    return () => window.removeEventListener('qmclaw:open-model-registry', handler);
  }, []);

  // UI state
  const [activeTab, setActiveTab] = useState<Tab>("experiments");
  const [selectedExp, setSelectedExpState] = useState<ExpType>(defaultSelectedExp);
  const [logs, setLogs] = useState<string[]>([]);
  const [plotUrl, setPlotUrl] = useState<string | null>(null);
  const [analysisResult, setAnalysisResult] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [plotModified, setPlotModified] = useState(false);
  const [currentPlotCommand, setCurrentPlotCommand] = useState<string>("");
  const [autoAnalyze, setAutoAnalyze] = useState<boolean>(true);
  // Plot analysis state
  const [plotAnalysisOutput, setPlotAnalysisOutput] = useState<string | null>(null);
  const [llmSummary, setLlmSummary] = useState<string | null>(null);
  const [isSummarizing, setIsSummarizing] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);

  // Hydrate from localStorage after mount (fixes SSR mismatch)
  useEffect(() => {
    setMounted(true);
    setQubits(loadSavedArray(STORAGE_KEYS.qubits, defaultQubits));
    setSelectedQubitState(loadSavedStr(STORAGE_KEYS.selectedQubit, defaultSelectedQubit));
    setSelectedExpState(loadSavedStr(STORAGE_KEYS.selectedExp, defaultSelectedExp) as ExpType);
  }, []);

  const addLog = useCallback((msg: string, isError = false) => {
    const ts = new Date().toLocaleTimeString();
    setLogs((prev) => [...prev.slice(-120), "[" + ts + "] " + msg]);
  }, []);

  // Load qubits from backend when session changes
  const loadQubits = useCallback(async () => {
    try {
      const result = await api.listQubits() as {
        qubits: Array<{ name: string; f10?: number; fread?: number; bias_z?: number }>;
        sessionPath: string[];
      };
      if (result.qubits && result.qubits.length > 0) {
        const qubitNames = result.qubits.map(q => q.name);
        setQubits(qubitNames);
        // Auto-select first qubit if current selection is not in list
        setSelectedQubitState(prev => qubitNames.includes(prev) ? prev : qubitNames[0]);
        addLog(`Loaded ${qubitNames.length} qubits from session: ${result.sessionPath.join('/')}`);
      } else {
        setQubits([]);
        setSelectedQubitState("");
        addLog("No qubits found in current session", true);
      }
    } catch (e: any) {
      addLog(`Failed to load qubits: ${e.message}`, true);
    }
  }, [addLog]);

  // Load qubits on mount and when Flask becomes available
  useEffect(() => {
    if (flaskOk) {
      loadQubits();
    }
  }, [flaskOk, loadQubits]);

  // Listen for session changes from SessionManager
  useEffect(() => {
    const handleSessionChange = () => {
      loadQubits();
    };
    window.addEventListener('qmclaw:session-changed', handleSessionChange);
    return () => {
      window.removeEventListener('qmclaw:session-changed', handleSessionChange);
    };
  }, [loadQubits]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  useEffect(() => {
    checkHealth(setServerOk, setFlaskOk, setQuickStatus);
    const interval = setInterval(() => checkHealth(setServerOk, setFlaskOk, setQuickStatus), 30_000);
    return () => clearInterval(interval);
  }, []);

  // ── Persistence wrappers ────────────────────────────────────────────────
  const setSelectedQubit = (q: string) => {
    setSelectedQubitState(q);
    saveStr(STORAGE_KEYS.selectedQubit, q);
  };

  const setSelectedExp = (e: ExpType) => {
    setSelectedExpState(e);
    saveStr(STORAGE_KEYS.selectedExp, e);
  };

  // ── Add qubit ──────────────────────────────────────────────────────────
  const addQubit = () => {
    const name = newQubitName.trim();
    if (!name || qubits.includes(name)) return;
    const next = [...qubits, name];
    setQubits(next);
    saveArray(STORAGE_KEYS.qubits, next);
    setSelectedQubit(name);
    setNewQubitName("");
  };

  const removeQubit = (name: string) => {
    const next = qubits.filter((q) => q !== name);
    setQubits(next);
    saveArray(STORAGE_KEYS.qubits, next);
    if (selectedQubit === name) setSelectedQubit(next[0] || "");
  };

  // Mapping: UI button name → sq.* function name
  const sqMap: Record<ExpType, string> = {
    spectroscopy: "sq.spectroscopy",
    s21: "sq.s21",
    iqraw: "sq.iqraw",
    t1: "sq.t1",
    ramsey: "sq.ramsey_df",
    piamp: "sq.piamp",
    xeb: "sq.xeb",
    s21_dis: "sq.s21_dis",
    allxy: "sq.allxy",
    single_shot: "sq.single_shot",
    pulsed_spec: "sq.pulsed_spec",
    swap: "sq.swap",
    drag_calibrate: "sq.drag_calibrate",
  };

  // Experiment descriptions (shown when selected)
  const expDescriptions: Record<ExpType, string> = {
    spectroscopy: "VNA spectroscopy — broad frequency scan to find qubit resonance using VNA",
    s21: "Cavity S21 — narrowband frequency scan around cavity resonance (do_plot recommended)",
    iqraw: "IQ Raw — acquire raw I/Q data for qubit state discrimination. Outputs F0/F1/SNR/separation",
    t1: "T1 Relaxation — measure qubit relaxation time via variable delay pulse sequence",
    ramsey: "Ramsey with detuning — measure T2* dephasing time, fit oscillation frequency",
    piamp: "Pi Pulse Amplitude — calibrate π-pulse amplitude for X gate via Rabi oscillation",
    xeb: "Cross-entropy benchmarking — measure single-qubit gate fidelity (target >99%)",
    s21_dis: "S21 Dispersive Shift — measure cavity transmission shift vs qubit state",
    allxy: "AllXY — characterize all 21 gate error combinations (target >99%)",
    single_shot: "Single-shot fidelity — measure qubit readout fidelity in single-shot regime",
    pulsed_spec: "Pulsed spectroscopy — qubit spectroscopy with pump pulse for higher SNR",
    swap: "SWAP characterization — characterize SWAP gate for two-qubit operations",
    drag_calibrate: "DRAG calibration — optimize DRAG coefficient for leakage suppression",
  };

  const runExperiment = async (exp: ExpType, plotCmd?: string) => {
    if (running) return;
    setRunning(true);
    addLog("▶ " + exp + " on " + selectedQubit + "...");
    setJobId(null);
    setPlotUrl(null);
    setAnalysisResult(null);
    setAnalysisError(null);

    const sqFn = sqMap[exp] || exp;
    const code = sqFn + "(" + selectedQubit + ", do_plot=True)";

    try {
      const { jobId: id } = await api.runAsync(code, {
        plotCommand: plotCmd || currentPlotCommand,
        autoAnalyze,
      });
      setJobId(id);
      addLog("Job: " + id);

      const result = await api.waitForJob(id, (r) => {
        if (r.status === "running") addLog("  running...");
      });

      if (result.status === "completed") {
        addLog("✅ Done (stdout " + (result.stdout?.length || 0) + " chars)");
        const snippet = (result.stdout || "").slice(-200).replace(/\n/g, " | ");
        addLog("  → " + snippet);
        if (result.plotPath) setPlotUrl(api.plotUrl(id));

        // Parse analysis result from stdout
        const analysisMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS:(.+)/);
        if (analysisMatch) {
          try {
            const analysis = JSON.parse(analysisMatch[1]);
            setAnalysisResult(analysis);
            addLog("📊 Analysis: " + analysis.slice(0, 100) + "...");
          } catch {
            setAnalysisResult(analysisMatch[1]);
            addLog("📊 Analysis: " + analysisMatch[1].slice(0, 100) + "...");
          }
        }
        const analysisErrorMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS_ERROR:(.+)/);
        if (analysisErrorMatch) {
          setAnalysisError(analysisErrorMatch[1]);
          addLog("⚠️ Analysis error: " + analysisErrorMatch[1], true);
        }
      } else {
        addLog("❌ Failed: " + (result.error || result.status), true);
        if (result.stderr) addLog("  stderr: " + result.stderr.slice(0, 200), true);
      }
    } catch (e: any) {
      addLog("❌ " + e.message, true);
    } finally {
      setRunning(false);
      setJobId(null);
    }
  };

  // ── Run custom command ────────────────────────────────────────────────
  const runCustom = async (command: string, plotCmd?: string) => {
    if (running) return;
    setRunning(true);
    setPlotUrl(null);
    setAnalysisResult(null);
    setAnalysisError(null);
    addLog("▶ Custom: " + command.slice(0, 60) + "...");
    setJobId(null);

    try {
      const { jobId: id } = await api.runAsync(command, {
        plotCommand: plotCmd || currentPlotCommand,
        autoAnalyze,
      });
      setJobId(id);
      addLog("Job: " + id);

      const result = await api.waitForJob(id, (r) => {
        if (r.status === "running") addLog("  running...");
      });

      if (result.status === "completed") {
        addLog("✅ Done (stdout " + (result.stdout?.length || 0) + " chars)");
        const snippet = (result.stdout || "").slice(-200).replace(/\n/g, " | ");
        addLog("  → " + snippet);
        if (result.plotPath) setPlotUrl(api.plotUrl(id));

        // Parse analysis result from stdout
        const analysisMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS:(.+)/);
        if (analysisMatch) {
          try {
            const analysis = JSON.parse(analysisMatch[1]);
            setAnalysisResult(analysis);
            addLog("📊 Analysis: " + analysis.slice(0, 100) + "...");
          } catch {
            setAnalysisResult(analysisMatch[1]);
            addLog("📊 Analysis: " + analysisMatch[1].slice(0, 100) + "...");
          }
        }
        const analysisErrorMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS_ERROR:(.+)/);
        if (analysisErrorMatch) {
          setAnalysisError(analysisErrorMatch[1]);
          addLog("⚠️ Analysis error: " + analysisErrorMatch[1], true);
        }
      } else {
        addLog("❌ Failed: " + (result.error || result.status), true);
        if (result.stderr) addLog("  stderr: " + result.stderr.slice(0, 200), true);
      }
    } catch (e: any) {
      addLog("❌ " + e.message, true);
    } finally {
      setRunning(false);
      setJobId(null);
    }
  };

  // ── Plot with custom command ────────────────────────────────────────────
  const handlePlot = async (plotCommand: string) => {
    if (running) return;
    setRunning(true);
    setPlotUrl(null);
    setAnalysisResult(null);
    setAnalysisError(null);
    setPlotAnalysisOutput(null);
    setLlmSummary(null);
    addLog("📈 Plotting with custom command...");

    try {
      // Use the dedicated plot endpoint which properly handles the data object
      const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3002";
      const res = await fetch(`${API_BASE}/sessions/plot`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: plotCommand }),
      });

      const result = await res.json();

      if (result.success) {
        setPlotUrl(`/plots/${result.plot_filename}?t=${Date.now()}`);
        // Capture analysis output from the plot command
        if (result.analysis_output) {
          setPlotAnalysisOutput(result.analysis_output);
        }
        addLog("✅ Plot saved: " + result.dataset_name);
      } else {
        addLog("❌ Plot failed: " + (result.error || "Unknown error"), true);
      }
    } catch (e: any) {
      addLog("❌ " + e.message, true);
    } finally {
      setRunning(false);
    }
  };

  // ── AI Summary ───────────────────────────────────────────────────────────
  const handleAiSummary = async () => {
    if (!plotAnalysisOutput || isSummarizing) return;
    setIsSummarizing(true);
    try {
      const result = await api.analyzePlot({
        analysisOutput: plotAnalysisOutput,
      });
      if (result.success && result.content) {
        setLlmSummary(result.content);
        addLog("✅ AI Summary generated");
      } else {
        addLog("❌ AI Summary failed", true);
      }
    } catch (e: any) {
      addLog("❌ " + e.message, true);
    } finally {
      setIsSummarizing(false);
    }
  };

  // ── Save plot command to config ─────────────────────────────────────────
  const handleSavePlotCommand = async (expType: string, plotCommand: string) => {
    try {
      await api.updateExperimentConfig(expType, { defaultPlotCommand: plotCommand });
      // Reload configs to reflect the change
      const data = await api.getExperimentConfigs();
      if (data.success && data.configs) {
        const configs = data.configs as unknown as Record<string, Record<string, ExperimentConfig>>;
        if (configs.experiments) {
          setExperimentConfigs(configs.experiments);
        }
      }
      addLog(`✅ Plot command for ${expType} saved to config`);
    } catch (e: any) {
      addLog(`❌ Failed to save: ${e.message}`, true);
      throw e;
    }
  };

  // ── Measure all metrics ───────────────────────────────────────────────
  const measureAllMetrics = async () => {
    if (running) return;
    setRunning(true);
    setPlotUrl(null);
    addLog("⏱ Measuring metrics...");
    try {
      const m = await api.measureMetrics(selectedQubit) as Metrics;
      addLog(
        "📊 fid=" + (m.readout_fidelity ?? 0).toFixed(4) + " | " +
        "t1=" + (m.t1 ?? 0).toFixed(1) + "ns | " +
        "gate=" + ((m.gate_fidelity ?? 0) * 100).toFixed(2) + "%"
      );
    } catch (e: any) {
      addLog("❌ Metrics failed: " + e.message, true);
    } finally {
      setRunning(false);
    }
  };

  const expButtons: ExpType[] = ["spectroscopy", "s21", "iqraw", "t1", "ramsey", "piamp", "xeb", "s21_dis", "allxy", "single_shot", "pulsed_spec", "swap", "drag_calibrate"];

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div style={{ height: "100vh", background: "#0f172a", color: "#e2e8f0", fontFamily: "system-ui, sans-serif", display: "flex", flexDirection: "column", overflow: "hidden" }}>

      {/* Header */}
      <header style={{ padding: "0.75rem 1.5rem", borderBottom: "1px solid #1e293b", display: "flex", alignItems: "center", gap: "1rem", flexShrink: 0 }}>
        <span style={{ fontSize: "1.5rem" }}>⚡ qmclaw</span>
        <span style={{ color: "#64748b", fontSize: "0.875rem" }}>Quantum Measurement & Calibration</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
          <CompactSessionManager />
          <span title="Express server" style={{ color: serverOk ? "#22c55e" : "#ef4444", fontSize: "0.75rem" }}>
            Express {serverOk ? "✅" : "❌"}
          </span>
          <span title="Flask backend" style={{ color: flaskOk ? "#22c55e" : "#ef4444", fontSize: "0.75rem" }}>
            Flask {flaskOk ? "✅" : "❌"}
          </span>
          {/* Hardware status indicators */}
          {quickStatus && (
            <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", padding: "0.25rem 0.5rem", background: "#1e293b", borderRadius: "0.375rem" }}>
              <StatusDot label="LabRAD" status={quickStatus.labrad} />
              <StatusDot label="Ray" status={quickStatus.ray} />
              <StatusDot label="DataVault" status={quickStatus.datavault} />
            </div>
          )}
          {/* Model Registry button */}
          <button
            onClick={() => setShowModelRegistry(true)}
            style={{
              padding: "0.25rem 0.6rem",
              background: "#1e293b",
              border: "1px solid #334155",
              borderRadius: "0.375rem",
              color: "#38bdf8",
              cursor: "pointer",
              fontSize: "0.7rem",
              fontWeight: 600,
            }}
          >
            🤖 Models
          </button>
          {/* Experiment Configs button */}
          <button
            onClick={() => setShowExperimentConfigs(true)}
            style={{
              padding: "0.25rem 0.6rem",
              background: "#1e293b",
              border: "1px solid #334155",
              borderRadius: "0.375rem",
              color: "#38bdf8",
              cursor: "pointer",
              fontSize: "0.7rem",
              fontWeight: 600,
            }}
          >
            ⚙️ Exp Config
          </button>
        </div>
      </header>

      {/* Body */}
      <div style={{ display: "grid", gridTemplateColumns: "220px 1fr 280px", flex: 1, overflow: "hidden" }}>

        {/* ── Qubit sidebar ── */}
        <aside style={{ borderRight: "1px solid #1e293b", display: "flex", flexDirection: "column", overflow: "hidden" }}>
          <div style={{ padding: "0.75rem", flexShrink: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
              <span style={{ fontSize: "0.7rem", fontWeight: 600, color: "#475569", letterSpacing: "0.1em" }}>QUBITS</span>
              <button
                onClick={loadQubits}
                title="Reload qubits from current session"
                style={{
                  padding: "0.15rem 0.4rem",
                  background: "transparent", border: "1px solid #334155",
                  borderRadius: "0.2rem", color: "#64748b", cursor: "pointer",
                  fontSize: "0.6rem",
                }}
              >
                ↻
              </button>
            </div>
            <div style={{ overflow: "auto", maxHeight: "calc(100vh - 220px)", marginBottom: "0.5rem" }}>
              {qubits.length === 0 && !flaskOk && (
                <div style={{ padding: "0.5rem", color: "#475569", fontSize: "0.7rem", textAlign: "center" }}>
                  Loading qubits...
                </div>
              )}
              {qubits.length === 0 && flaskOk && (
                <div style={{ padding: "0.5rem", color: "#f59e0b", fontSize: "0.7rem", textAlign: "center" }}>
                  No qubits found.<br/>Switch session or add manually.
                </div>
              )}
              {qubits.map((q) => (
                <div key={q} style={{ display: "flex", alignItems: "center", gap: "0.25rem", marginBottom: "0.25rem" }}>
                  <button
                    onClick={() => setSelectedQubit(q)}
                    style={{
                      flex: 1, padding: "0.5rem 0.5rem", borderRadius: "0.375rem",
                      border: "1px solid",
                      borderColor: selectedQubit === q ? "#38bdf8" : "#1e293b",
                      background: selectedQubit === q ? "#1e3a5f" : "#0f172a",
                      color: selectedQubit === q ? "#38bdf8" : "#64748b",
                      cursor: "pointer", textAlign: "left",
                      fontFamily: "monospace", fontSize: "0.75rem",
                    }}
                  >
                    {q}
                  </button>
                  <button
                    onClick={() => setParamsQubit(q)}
                    title="View/edit parameters"
                    style={{
                      padding: "0.2rem 0.35rem", borderRadius: "0.25rem",
                      border: "1px solid #334155", background: "#1e293b",
                      color: "#6366f1", cursor: "pointer", fontSize: "0.7rem",
                      flexShrink: 0,
                    }}
                  >
                    ⚙
                  </button>
                  <button
                    onClick={() => removeQubit(q)}
                    title="Remove qubit"
                    style={{
                      padding: "0.2rem 0.35rem", borderRadius: "0.25rem",
                      border: "1px solid #334155", background: "#1e293b",
                      color: "#64748b", cursor: "pointer", fontSize: "0.6rem",
                      flexShrink: 0,
                    }}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
            <div style={{ display: "flex", gap: "0.25rem" }}>
              <input
                value={newQubitName}
                onChange={(e) => setNewQubitName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addQubit()}
                placeholder="q3ld6..."
                style={{
                  flex: 1, padding: "0.35rem 0.5rem",
                  background: "#1e293b", color: "#e2e8f0",
                  border: "1px solid #334155", borderRadius: "0.25rem",
                  fontFamily: "monospace", fontSize: "0.7rem",
                  minWidth: 0,
                }}
              />
              <button onClick={addQubit} style={{
                padding: "0.35rem 0.5rem", borderRadius: "0.25rem",
                border: "1px solid #334155", background: "#1e3a5f",
                color: "#38bdf8", cursor: "pointer", fontSize: "0.7rem",
                flexShrink: 0,
              }}>
                +
              </button>
            </div>
          </div>
        </aside>

        {/* ── Main content ── */}
        <main style={{ overflow: "auto", padding: "1rem", display: "flex", flexDirection: "column", gap: "1rem" }}>

          {/* Tab bar */}
          <div style={{ display: "flex", gap: "0.5rem", flexShrink: 0 }}>
            {(["experiments", "jobs", "workflow", "services"] as Tab[]).map((t) => (
              <button key={t} onClick={() => setActiveTab(t)} style={{
                padding: "0.4rem 1rem", borderRadius: "0.375rem", border: "none",
                background: activeTab === t ? "#38bdf8" : "#1e293b",
                color: activeTab === t ? "#0f172a" : "#94a3b8",
                fontWeight: 600, cursor: "pointer", textTransform: "capitalize", fontSize: "0.875rem",
              }}>
                {t}
              </button>
            ))}
          </div>

          {/* EXPERIMENTS TAB */}
          {activeTab === "experiments" && (
            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>

              {/* Qubit context */}
              <div style={{ color: "#94a3b8", fontSize: "0.875rem" }}>
                <span style={{ fontFamily: "monospace", color: "#38bdf8" }}>{selectedQubit}</span>
                {" — "}
                <span style={{ color: "#64748b" }}>{selectedExp}</span>
              </div>

              {/* All experiment buttons */}
              <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap" }}>
                {expButtons.map((e) => (
                  <button key={e} onClick={() => setSelectedExp(e)} style={{
                    padding: "0.4rem 0.875rem", borderRadius: "0.375rem",
                    border: "1px solid",
                    borderColor: selectedExp === e ? "#38bdf8" : "#334155",
                    background: selectedExp === e ? "#1e3a5f" : "#1e293b",
                    color: "#e2e8f0", cursor: "pointer",
                    textTransform: "capitalize", fontSize: "0.8rem",
                  }}>
                    {e.replace(/_/g, " ")}
                  </button>
                ))}
              </div>

              {/* Experiment description */}
              <div style={{
                padding: "0.5rem 0.75rem",
                background: "#0f172a",
                border: "1px solid #1e293b",
                borderRadius: "0.375rem",
                fontSize: "0.75rem",
                color: "#94a3b8",
              }}>
                <span style={{ color: "#38bdf8", fontWeight: 600 }}>sq.{sqMap[selectedExp]?.split(".")[1]}: </span>
                {expDescriptions[selectedExp]}
              </div>

              <EditableCommand
                qubit={selectedQubit}
                expType={selectedExp}
                onRun={(cmd) => runCustom(cmd)}
                disabled={running}
              />

              <PlotCommandEditor
                expType={selectedExp}
                initialPlotCommand={experimentConfigs[selectedExp]?.defaultPlotCommand || ""}
                onPlotCommandChange={(cmd) => setCurrentPlotCommand(cmd)}
                onPlot={(cmd) => handlePlot(cmd)}
                onSaveToConfig={handleSavePlotCommand}
                plotDisabled={running}
              />

              {/* Auto-analyze toggle */}
              <div style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                padding: "0.35rem 0.75rem",
                background: "#0f172a",
                border: "1px solid #1e293b",
                borderRadius: "0.375rem",
                fontSize: "0.75rem",
              }}>
                <input
                  type="checkbox"
                  id="auto-analyze"
                  checked={autoAnalyze}
                  onChange={(e) => setAutoAnalyze(e.target.checked)}
                  style={{ cursor: "pointer" }}
                />
                <label htmlFor="auto-analyze" style={{ color: "#94a3b8", cursor: "pointer" }}>
                  🤖 Auto-analyze plot with LLM
                </label>
              </div>

              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
                <button onClick={() => runExperiment(selectedExp)} disabled={running} style={{
                  padding: "0.6rem 2rem", borderRadius: "0.5rem", border: "none",
                  background: running ? "#334155" : "#22c55e",
                  color: running ? "#64748b" : "#fff",
                  fontWeight: 600, cursor: running ? "not-allowed" : "pointer", fontSize: "0.9rem",
                }}>
                  {running ? "⏳ Running..." : "▶ Run " + selectedExp.replace(/_/g, " ")}
                </button>
                <ActionBtn label="Measure Metrics" on={measureAllMetrics} color="#0ea5e9" disabled={running} />
                {expButtons.map((e) => (
                  <ActionBtn key={e} label={e.replace(/_/g, " ")} on={() => runExperiment(e)} disabled={running} />
                ))}
              </div>

              {plotUrl ? (
                <div style={{
                  marginTop: "0.5rem",
                  border: "1px solid #1e293b",
                  borderRadius: "0.5rem",
                  overflow: "hidden",
                  background: "#0a0f1a",
                }}>
                  <div style={{
                    padding: "0.5rem 0.75rem",
                    fontSize: "0.7rem", fontWeight: 600,
                    color: "#475569", letterSpacing: "0.1em",
                    borderBottom: "1px solid #1e293b",
                    background: "#0f172a",
                  }}>
                    📈 PLOT {jobId ? "(job: " + jobId.slice(0, 8) + "...)" : ""}
                  </div>
                  {/* Left-Right Layout: Image + Analysis */}
                  <div style={{
                    display: "grid",
                    gridTemplateColumns: plotAnalysisOutput ? "1fr 400px" : "1fr",
                    minHeight: "300px",
                  }}>
                    {/* Left: Plot Image */}
                    <div style={{ borderRight: plotAnalysisOutput ? "1px solid #1e293b" : "none" }}>
                      <img
                        src={plotUrl}
                        alt="Experiment plot"
                        style={{ display: "block", width: "100%", maxHeight: "70vh", objectFit: "contain" }}
                      />
                    </div>
                    {/* Right: Analysis Output + AI Summary */}
                    {plotAnalysisOutput && (
                      <div style={{ padding: "0.75rem", overflow: "auto", maxHeight: "70vh" }}>
                        {/* Analysis Output Header */}
                        <div style={{
                          fontSize: "0.7rem", fontWeight: 600,
                          color: "#475569", letterSpacing: "0.1em",
                          marginBottom: "0.5rem",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                        }}>
                          📊 ANALYSIS OUTPUT
                        </div>
                        {/* Analysis Output Content */}
                        <pre style={{
                          fontSize: "0.75rem",
                          color: "#22d3ee",
                          lineHeight: 1.5,
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-word",
                          background: "#0f172a",
                          padding: "0.75rem",
                          borderRadius: "0.25rem",
                          border: "1px solid #1e293b",
                          margin: 0,
                          maxHeight: "35vh",
                          overflow: "auto",
                        }}>
                          {plotAnalysisOutput}
                        </pre>
                        {/* AI Summary Button */}
                        <button
                          onClick={handleAiSummary}
                          disabled={isSummarizing}
                          style={{
                            marginTop: "0.75rem",
                            padding: "0.4rem 0.75rem",
                            borderRadius: "0.25rem",
                            border: "none",
                            background: isSummarizing ? "#334155" : "#6366f1",
                            color: isSummarizing ? "#64748b" : "#fff",
                            fontSize: "0.75rem",
                            fontWeight: 600,
                            cursor: isSummarizing ? "not-allowed" : "pointer",
                            display: "flex",
                            alignItems: "center",
                            gap: "0.5rem",
                            width: "100%",
                          }}
                        >
                          {isSummarizing ? (
                            <>⏳ Generating summary...</>
                          ) : (
                            <>🤖 AI Summary</>
                          )}
                        </button>
                        {/* LLM Summary Display */}
                        {llmSummary && (
                          <div style={{
                            marginTop: "0.75rem",
                            padding: "0.75rem",
                            background: "#1e1b4b",
                            borderRadius: "0.25rem",
                            border: "1px solid #6366f1",
                          }}>
                            <div style={{
                              fontSize: "0.7rem", fontWeight: 600,
                              color: "#a5b4fc", marginBottom: "0.5rem",
                            }}>
                              💡 AI Summary
                            </div>
                            <div style={{
                              fontSize: "0.8rem",
                              color: "#e2e8f0",
                              lineHeight: 1.6,
                              whiteSpace: "pre-wrap",
                            }}>
                              {llmSummary}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div style={{
                  marginTop: "0.5rem",
                  border: "1px dashed #1e293b",
                  borderRadius: "0.5rem",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  minHeight: "8rem", color: "#334569",
                  fontSize: "0.8rem",
                  flexDirection: "column", gap: "0.5rem",
                }}>
                  <span style={{ fontSize: "1.5rem" }}>📊</span>
                  <span>Run an experiment to see the plot here</span>
                </div>
              )}

              {/* Analysis result display */}
              {analysisResult && (
                <div style={{
                  marginTop: "0.5rem",
                  border: "1px solid #6366f1",
                  borderRadius: "0.5rem",
                  overflow: "hidden",
                  background: "#0a0f1a",
                }}>
                  <div style={{
                    padding: "0.5rem 0.75rem",
                    fontSize: "0.7rem", fontWeight: 600,
                    color: "#6366f1", letterSpacing: "0.1em",
                    borderBottom: "1px solid #1e293b",
                    background: "#1e1b4b",
                    display: "flex",
                    alignItems: "center",
                    gap: "0.5rem",
                  }}>
                    🤖 LLM ANALYSIS
                    <span style={{ fontSize: "0.65rem", color: "#a5b4fc", fontWeight: 400 }}>
                      {selectedExp}
                    </span>
                  </div>
                  <div style={{
                    padding: "0.75rem",
                    fontSize: "0.8rem",
                    color: "#e2e8f0",
                    lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                    maxHeight: "300px",
                    overflow: "auto",
                  }}>
                    {analysisResult}
                  </div>
                </div>
              )}

              {/* Analysis error display */}
              {analysisError && (
                <div style={{
                  marginTop: "0.5rem",
                  border: "1px solid #f59e0b",
                  borderRadius: "0.5rem",
                  overflow: "hidden",
                  background: "#0a0f1a",
                }}>
                  <div style={{
                    padding: "0.5rem 0.75rem",
                    fontSize: "0.7rem", fontWeight: 600,
                    color: "#f59e0b", letterSpacing: "0.1em",
                    borderBottom: "1px solid #1e293b",
                    background: "#451a03",
                  }}>
                    ⚠️ ANALYSIS ERROR
                  </div>
                  <div style={{
                    padding: "0.75rem",
                    fontSize: "0.8rem",
                    color: "#fca5a5",
                    lineHeight: 1.6,
                  }}>
                    {analysisError}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* JOBS TAB — DataVault history + Recent Jobs (side by side) */}
          {activeTab === "jobs" && (
            <div style={{ display: "flex", gap: "0.75rem", flex: 1, overflow: "hidden" }}>

              {/* DataVault browser (left half, fills full height) */}
              <DatasetBrowser />

              {/* Recent jobs (right half) */}
              <div style={{
                flex: 1, border: "1px solid #1e293b", borderRadius: "0.5rem",
                background: "#0a0f1a", overflow: "hidden",
                display: "flex", flexDirection: "column",
              }}>
                <div style={{
                  padding: "0.5rem 0.75rem",
                  fontSize: "0.7rem", fontWeight: 600,
                  color: "#475569", letterSpacing: "0.1em",
                  borderBottom: "1px solid #1e293b",
                  background: "#0f172a",
                  flexShrink: 0,
                }}>
                  📋 RECENT JOBS
                </div>
                <div style={{ flex: 1, overflow: "auto" }}>
                  <JobManager
                    currentJobId={jobId}
                    onJobSelect={(id) => { setJobId(id); setActiveTab("experiments"); }}
                  />
                </div>
              </div>

            </div>
          )}

          {/* WORKFLOW TAB */}
          {activeTab === "workflow" && (
            <div style={{ flex: 1, overflow: "hidden" }}>
              <WorkflowDesigner selectedQubit={selectedQubit} onLog={addLog} />
            </div>
          )}

          {/* SERVICES TAB */}
          {activeTab === "services" && (
            <ServiceControlPanel />
          )}
        </main>

        {/* ── Log panel ── */}
        <aside style={{
          borderLeft: "1px solid #1e293b",
          background: "#0a0f1a",
          display: "flex", flexDirection: "column",
          overflow: "hidden",
        }}>
          <div style={{
            padding: "0.75rem",
            fontSize: "0.7rem", fontWeight: 600,
            color: "#334569", letterSpacing: "0.1em",
            borderBottom: "1px solid #1e293b",
            flexShrink: 0,
          }}>
            OUTPUT {jobId ? "(job: " + jobId.slice(0, 8) + "...)" : ""}
          </div>
          <div style={{
            flex: 1, overflow: "auto",
            padding: "0.75rem",
            fontFamily: "monospace", fontSize: "0.7rem", lineHeight: 1.7,
          }}>
            {logs.map((line, i) => (
              <div key={i} style={{
                color: line.includes("❌") ? "#f87171" : line.includes("▶") ? "#38bdf8" : "#64748b",
                whiteSpace: "pre-wrap", wordBreak: "break-all",
              }}>
                {line}
              </div>
            ))}
            <div ref={logsEndRef} />
          </div>
        </aside>
      </div>

      {/* Qubit Parameters Panel Modal */}
      {paramsQubit && (
        <QubitParamsPanel
          qubitName={paramsQubit}
          onClose={() => setParamsQubit(null)}
          onSaved={() => {
            addLog(`Saved parameters for ${paramsQubit}`);
            setParamsQubit(null);
          }}
        />
      )}

      {/* Model Registry Modal */}
      {showModelRegistry && (
        <ModelRegistry onClose={() => setShowModelRegistry(false)} />
      )}

      {/* Experiment Configs Modal */}
      {showExperimentConfigs && (
        <ExperimentConfigs onClose={() => setShowExperimentConfigs(false)} />
      )}
    </div>
  );
}