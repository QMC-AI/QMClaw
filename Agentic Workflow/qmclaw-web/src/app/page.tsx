"use client";

/**
 * qmclaw Dashboard - Quantum Measurement & Calibration Workflow
 *
 * Architecture:
 *   Browser → Express (:3002) → Python subprocess (LabRAD + lqms)
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { api, Metrics, ExperimentConfig } from "../lib/api";
import CollapsibleCommand from "../components/CollapsibleCommand";
import UnifiedResults from "../components/UnifiedResults";
import JobsPanel from "../components/JobsPanel";
import WorkflowDesigner from "../components/WorkflowDesigner";
import { CompactSessionManager } from "../components/SessionManager";
import ImageClassificationPanel from "../components/ImageClassificationPanel";
import AgentChatPanel from "../components/AgentChatPanel";
import HermesChatPanel from "../components/HermesChatPanel";
import QubitParamsPanel from "../components/QubitParamsPanel";
import ModelRegistry from "../components/ModelRegistry";
import ExperimentConfigs from "../components/ExperimentConfigs";
import { useModelStore } from "../store/modelStore";
import ChatHistorySidebar from "../components/ChatHistorySidebar";
import WorkflowHistorySidebar from "../components/WorkflowHistorySidebar";

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
  setQuickStatus: (s: QuickStatus | null) => void
) {
  try {
    const health = await api.ping() as {
      express: string; backend: { status: string; ready: boolean } | "unreachable";
    };
    setServerOk(true);
    // Also fetch quick hardware status
    try {
      const qs = await api.getQuickStatus() as QuickStatus;
      setQuickStatus(qs);
    } catch {
      setQuickStatus(null);
    }
  } catch {
    setServerOk(false);
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

// Tab: experiments, workflow, agent, images, hermes
type Tab = "experiments" | "workflow" | "agent" | "images" | "hermes";
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
  const [quickStatus, setQuickStatus] = useState<QuickStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  // Qubit list — persisted (SSR uses defaults, client hydrates from localStorage)
  const [qubits, setQubits] = useState<string[]>(defaultQubits);
  const [selectedQubit, setSelectedQubitState] = useState<string>(defaultSelectedQubit);
  const [newQubitName, setNewQubitName] = useState<string>("");
  const [qubitSearch, setQubitSearch] = useState<string>("");
  const filteredQubits = qubits.filter(q => q.toLowerCase().includes(qubitSearch.toLowerCase()));

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
    defaultAnalysisCommand?: string;
    metricsToExtract?: string[];
  }>>({});

  // Load experiment configs from backend on mount
  useEffect(() => {
    api.getExperimentConfigs().then(data => {
      if (data.success && data.configs) {
        // API returns { configs: { experiments: Record<string, Config> } }
        const configs = data.configs as unknown as Record<string, Record<string, ExperimentConfig>>;
        if (configs.experiments) {
          setExperimentConfigs(configs.experiments);
          // Initialize commands from configs
          const exp = selectedExp as string;
          if (configs.experiments[exp]) {
            setCurrentRunCommand(configs.experiments[exp].defaultCommand || "");
            setCurrentPlotCommand(configs.experiments[exp].defaultPlotCommand || "");
            setCurrentAnalyzeCommand(configs.experiments[exp].defaultAnalysisCommand || "");
          }
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
  const [plotLoading, setPlotLoading] = useState(false);
  const [analysisResult, setAnalysisResult] = useState<{
    success: boolean;
    stdout?: string;
    stderr?: string;
    metrics?: Record<string, number | string>;
    error?: string;
  } | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [plotModified, setPlotModified] = useState(false);

  // Command states for collapsible commands
  const [currentRunCommand, setCurrentRunCommand] = useState<string>("");
  const [currentPlotCommand, setCurrentPlotCommand] = useState<string>("");
  const [currentAnalyzeCommand, setCurrentAnalyzeCommand] = useState<string>("");

  // Command running states
  const [isRunningCommand, setIsRunningCommand] = useState(false);
  const [isRunningPlot, setIsRunningPlot] = useState(false);
  const [isRunningAnalyze, setIsRunningAnalyze] = useState(false);

  // Command saving states
  const [isSavingCommand, setIsSavingCommand] = useState(false);
  const [isSavingPlot, setIsSavingPlot] = useState(false);
  const [isSavingAnalyze, setIsSavingAnalyze] = useState(false);

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
        addLog(`Loaded ${qubitNames.length} qubits${result.sessionPath ? ' from session: ' + result.sessionPath.join('/') : ''}`);
      } else {
        setQubits([]);
        setSelectedQubitState("");
        addLog("No qubits found in current session", true);
      }
    } catch (e: any) {
      addLog(`Failed to load qubits: ${e.message}`, true);
    }
  }, [addLog]);

  // Load qubits on mount and when backend becomes available
  useEffect(() => {
    if (serverOk) {
      loadQubits();
    }
  }, [serverOk, loadQubits]);

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
    checkHealth(setServerOk, setQuickStatus);
    const interval = setInterval(() => checkHealth(setServerOk, setQuickStatus), 30_000);
    return () => clearInterval(interval);
  }, []);

  // ── Plot historical dataset from DataVault ──────────────────────────────
  const handlePlotHistoricalDataset = async (name: string, path: string) => {
    if (running) {
      addLog("⚠️ Cannot plot while experiment is running", true);
      return;
    }
    setRunning(true);
    setIsRunningPlot(true);
    setPlotUrl(null);
    setPlotLoading(true);
    setAnalysisResult(null);
    setAnalysisError(null);
    setPlotAnalysisOutput(null);
    setLlmSummary(null);
    addLog(`📊 Plotting historical dataset: ${name}...`);

    try {
      // 使用新版 API - 自动根据实验类型选择绘图配置
      const result = await api.plotExperimentDataset(name, path);

      if (result.success && result.image) {
        // 直接使用 Base64 URL 显示图像
        setPlotUrl(result.image);
        addLog(`✅ Plotted ${result.exp_type} for qubit ${result.qubit} (exp #${result.exp_num})`);
      } else {
        addLog(`❌ Plot failed: ${result.error || "Unknown error"}`, true);
      }
    } catch (e: any) {
      addLog(`❌ ${e.message}`, true);
    } finally {
      setRunning(false);
      setIsRunningPlot(false);
      setPlotLoading(false);
    }
  };

  // Listen for "plot in experiments" event from DatasetBrowser
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { name: string; path: string };
      handlePlotHistoricalDataset(detail.name, detail.path);
    };
    window.addEventListener("dataset:plot-in-experiments", handler);
    return () => window.removeEventListener("dataset:plot-in-experiments", handler);
  }, [currentPlotCommand, running]);

  // ── Persistence wrappers ────────────────────────────────────────────────
  const setSelectedQubit = (q: string) => {
    setSelectedQubitState(q);
    saveStr(STORAGE_KEYS.selectedQubit, q);
  };

  const setSelectedExp = (e: ExpType) => {
    setSelectedExpState(e);
    saveStr(STORAGE_KEYS.selectedExp, e);
    // Update commands from configs
    if (experimentConfigs[e]) {
      setCurrentRunCommand(experimentConfigs[e].defaultCommand || "");
      setCurrentPlotCommand(experimentConfigs[e].defaultPlotCommand || "");
      setCurrentAnalyzeCommand(experimentConfigs[e].defaultAnalysisCommand || "");
    }
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
    setIsRunningCommand(true);
    addLog("▶ " + exp + " on " + selectedQubit + "...");
    setPlotUrl(null);
    setPlotLoading(true);
    setAnalysisResult(null);
    setAnalysisError(null);
    setLlmSummary(null);

    const sqFn = sqMap[exp] || exp;
    const code = sqFn + "(" + selectedQubit + ", do_plot=True)";

    try {
      // Use synchronous microservice endpoint
      const result = await api.runExperiment(code, { timeout: 300 });
      setTaskId(result.task_id);
      addLog("Task: " + result.task_id.slice(0, 12) + "...");

      if (result.status === "success") {
        addLog("✅ Done (stdout " + (result.stdout?.length || 0) + " chars)");
        const snippet = (result.stdout || "").slice(-200).replace(/\n/g, " | ");
        addLog("  → " + snippet);
        if (result.result && typeof result.result === 'object' && 'plotPath' in result.result) {
          setPlotUrl(result.result.plotPath as string);
        }

        // Parse analysis result from stdout
        const analysisMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS:(.+)/);
        if (analysisMatch) {
          try {
            const analysis = JSON.parse(analysisMatch[1]);
            setAnalysisResult({ success: true, stdout: analysis });
            addLog("📊 Analysis: " + analysis.slice(0, 100) + "...");
          } catch {
            setAnalysisResult({ success: true, stdout: analysisMatch[1] });
            addLog("📊 Analysis: " + analysisMatch[1].slice(0, 100) + "...");
          }
        }
        const analysisErrorMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS_ERROR:(.+)/);
        if (analysisErrorMatch) {
          setAnalysisResult({ success: false, error: analysisErrorMatch[1] });
          addLog("⚠️ Analysis error: " + analysisErrorMatch[1], true);
        }

        // Auto LLM summary if enabled
        if (autoAnalyze && plotAnalysisOutput && !llmSummary) {
          addLog("🤖 Auto-generating AI summary...");
          handleAiSummary();
        }
      } else if (result.status === "busy") {
        addLog("⏳ Service busy, another task is running", true);
      } else {
        addLog("❌ Failed: " + (result.error || result.status), true);
        if (result.stderr) addLog("  stderr: " + result.stderr.slice(0, 200), true);
      }
    } catch (e: any) {
      addLog("❌ " + e.message, true);
    } finally {
      setRunning(false);
      setIsRunningCommand(false);
      setPlotLoading(false);
    }
  };

  // ── Run custom command ────────────────────────────────────────────────
  const runCustom = async (command: string, plotCmd?: string) => {
    if (running) return;
    setRunning(true);
    setIsRunningCommand(true);
    setPlotUrl(null);
    setPlotLoading(true);
    setAnalysisResult(null);
    setAnalysisError(null);
    setLlmSummary(null);
    addLog("▶ Custom: " + command.slice(0, 60) + "...");

    try {
      // Use synchronous microservice endpoint
      const result = await api.runExperiment(command, { timeout: 300 });
      setTaskId(result.task_id);
      addLog("Task: " + result.task_id.slice(0, 12) + "...");

      if (result.status === "success") {
        addLog("✅ Done (stdout " + (result.stdout?.length || 0) + " chars)");
        const snippet = (result.stdout || "").slice(-200).replace(/\n/g, " | ");
        addLog("  → " + snippet);
        if (result.result && typeof result.result === 'object' && 'plotPath' in result.result) {
          setPlotUrl(result.result.plotPath as string);
        }

        // Parse analysis result from stdout
        const analysisMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS:(.+)/);
        if (analysisMatch) {
          try {
            const analysis = JSON.parse(analysisMatch[1]);
            setAnalysisResult({ success: true, stdout: analysis });
            addLog("📊 Analysis: " + analysis.slice(0, 100) + "...");
          } catch {
            setAnalysisResult({ success: true, stdout: analysisMatch[1] });
            addLog("📊 Analysis: " + analysisMatch[1].slice(0, 100) + "...");
          }
        }
        const analysisErrorMatch = (result.stdout || "").match(/QMCLAW_ANALYSIS_ERROR:(.+)/);
        if (analysisErrorMatch) {
          setAnalysisResult({ success: false, error: analysisErrorMatch[1] });
          addLog("⚠️ Analysis error: " + analysisErrorMatch[1], true);
        }

        // Auto LLM summary if enabled
        if (autoAnalyze && plotAnalysisOutput && !llmSummary) {
          addLog("🤖 Auto-generating AI summary...");
          handleAiSummary();
        }
      } else if (result.status === "busy") {
        addLog("⏳ Service busy, another task is running", true);
      } else {
        addLog("❌ Failed: " + (result.error || result.status), true);
        if (result.stderr) addLog("  stderr: " + result.stderr.slice(0, 200), true);
      }
    } catch (e: any) {
      addLog("❌ " + e.message, true);
    } finally {
      setRunning(false);
      setIsRunningCommand(false);
      setPlotLoading(false);
    }
  };

  // ── Plot with custom command ────────────────────────────────────────────
  const handlePlot = async (plotCommand: string) => {
    if (running) return;
    setRunning(true);
    setIsRunningPlot(true);
    setPlotUrl(null);
    setPlotLoading(true);
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
        setPlotUrl(`${result.plotUrl}?t=${Date.now()}`);
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
      setIsRunningPlot(false);
      setPlotLoading(false);
    }
  };

  // ── AI Summary ───────────────────────────────────────────────────────────
  const handleAiSummary = async () => {
    if (!plotAnalysisOutput || isSummarizing) return;
    setIsSummarizing(true);
    try {
      const result = await api.analyzePlot({
        analysis_output: plotAnalysisOutput,
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
    setIsSavingPlot(true);
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
    } finally {
      setIsSavingPlot(false);
    }
  };

  // ── Save run command to config ─────────────────────────────────────────────
  const handleSaveRunCommand = async (expType: string, command: string) => {
    setIsSavingCommand(true);
    try {
      await api.updateExperimentConfig(expType, {
        defaultCommand: command,
      });
      // Reload configs to reflect the change
      const data = await api.getExperimentConfigs();
      if (data.success && data.configs) {
        const configs = data.configs as unknown as Record<string, Record<string, ExperimentConfig>>;
        if (configs.experiments) {
          setExperimentConfigs(configs.experiments);
        }
      }
      addLog(`✅ Run command for ${expType} saved`);
    } catch (e: any) {
      addLog(`❌ Failed to save: ${e.message}`, true);
      throw e;
    } finally {
      setIsSavingCommand(false);
    }
  };

  // ── Save analysis command to config ─────────────────────────────────────
  const handleSaveAnalysisCommand = async (expType: string, analysisCommand: string, metrics: string[]) => {
    setIsSavingAnalyze(true);
    try {
      await api.updateExperimentConfig(expType, {
        defaultAnalysisCommand: analysisCommand,
        metricsToExtract: metrics,
      });
      // Reload configs to reflect the change
      const data = await api.getExperimentConfigs();
      if (data.success && data.configs) {
        const configs = data.configs as unknown as Record<string, Record<string, ExperimentConfig>>;
        if (configs.experiments) {
          setExperimentConfigs(configs.experiments);
        }
      }
      addLog(`✅ Analysis config for ${expType} saved`);
    } catch (e: any) {
      addLog(`❌ Failed to save: ${e.message}`, true);
      throw e;
    } finally {
      setIsSavingAnalyze(false);
    }
  };

  // ── Execute All: Run command → Plot → Analyze ───────────────────────────
  const handleExecuteAll = async () => {
    if (running) {
      addLog("⚠️ A task is already running", true);
      return;
    }

    // First run the experiment command
    addLog("▶ Execute All: Running experiment...");
    await runExperiment(selectedExp);

    // After experiment completes, run plot command
    if (!running && plotUrl) {
      addLog("▶ Execute All: Running plot...");
      await handlePlot(currentPlotCommand);

      // After plot completes, run analysis command
      if (!running && currentAnalyzeCommand) {
        addLog("▶ Execute All: Running analysis...");
        const result = await api.runAnalysis(currentAnalyzeCommand, selectedExp);
        if (result.success) {
          setAnalysisResult({
            success: true,
            stdout: result.stdout,
            metrics: result.metrics,
          });
          addLog("✅ Execute All: Complete!");
        } else {
          setAnalysisResult({
            success: false,
            error: result.error || "Unknown error",
          });
          addLog("❌ Execute All: Analysis failed", true);
        }
      }
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

        {/* ── Left sidebar: dynamic content based on active tab ── */}
        <aside style={{ borderRight: "1px solid #1e293b", display: "flex", flexDirection: "column", overflow: "hidden" }}>

          {/* Tab-specific content (top area) */}
          {activeTab === "experiments" && (
            <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", padding: "0.5rem" }}>
              <JobsPanel
                selectedQubit={selectedQubit}
                onSelectQubit={setSelectedQubit}
                qubits={qubits}
                onLoadQubits={loadQubits}
              />
            </div>
          )}

          {activeTab === "workflow" && (
            <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", padding: "0.5rem" }}>
              <WorkflowHistorySidebar
                onSelectWorkflow={(id) => { console.log("Select workflow:", id); }}
              />
            </div>
          )}

          {activeTab === "agent" && (
            <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", padding: "0.5rem" }}>
              <ChatHistorySidebar
                currentSessionId={""}
                onSelectSession={(id) => { console.log("Select session:", id); }}
                onNewSession={() => { console.log("New session"); }}
              />
            </div>
          )}

          {activeTab === "images" && (
            <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column", padding: "0.5rem" }}>
              {/* Images tab doesn't need sidebar content */}
            </div>
          )}

          {/* Compact Qubit selector (bottom, fixed height) - not for images tab */}
          {activeTab !== "images" && activeTab !== "experiments" && (
          <div style={{
            borderTop: "1px solid #1e293b",
            padding: "0.5rem",
            background: "#0a0f1a",
            flexShrink: 0,
          }}>
            {/* Qubit selector header with search */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.35rem" }}>
              <span style={{ fontSize: "0.65rem", fontWeight: 600, color: "#475569", letterSpacing: "0.1em" }}>QUBIT</span>
              <button
                onClick={loadQubits}
                title="Reload qubits"
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

            {/* Search input */}
            <input
              value={qubitSearch}
              onChange={(e) => setQubitSearch(e.target.value)}
              placeholder="🔍 Search qubit..."
              style={{
                width: "100%", padding: "0.3rem 0.4rem", marginBottom: "0.35rem",
                background: "#1e293b", color: "#e2e8f0",
                border: "1px solid #334155", borderRadius: "0.25rem",
                fontFamily: "monospace", fontSize: "0.7rem",
                boxSizing: "border-box",
              }}
            />

            {/* Selected qubit display */}
            <div style={{
              padding: "0.35rem 0.5rem", marginBottom: "0.35rem",
              background: selectedQubit ? "#1e3a5f" : "#1e293b",
              border: "1px solid", borderColor: selectedQubit ? "#38bdf8" : "#334155",
              borderRadius: "0.25rem",
              fontFamily: "monospace", fontSize: "0.75rem",
              color: selectedQubit ? "#38bdf8" : "#64748b",
              textAlign: "center",
            }}>
              {selectedQubit || "Select qubit"}
            </div>

            {/* Qubit grid (filtered) */}
            <div style={{ overflow: "auto", maxHeight: "80px", marginBottom: "0.35rem" }}>
              {filteredQubits.length === 0 && qubitSearch && (
                <div style={{ padding: "0.25rem", color: "#475569", fontSize: "0.65rem", textAlign: "center" }}>
                  No match
                </div>
              )}
              <div style={{ display: "flex", flexWrap: "wrap", gap: "0.2rem" }}>
                {filteredQubits.slice(0, 12).map((q) => (
                  <div key={q} style={{ display: "flex", alignItems: "center", gap: "0.15rem" }}>
                    <button
                      onClick={() => setSelectedQubit(q)}
                      style={{
                        padding: "0.2rem 0.4rem",
                        borderRadius: "0.2rem",
                        border: "1px solid",
                        borderColor: selectedQubit === q ? "#38bdf8" : "#1e293b",
                        background: selectedQubit === q ? "#1e3a5f" : "#0f172a",
                        color: selectedQubit === q ? "#38bdf8" : "#64748b",
                        cursor: "pointer",
                        fontFamily: "monospace", fontSize: "0.6rem",
                      }}
                    >
                      {q}
                    </button>
                    <button
                      onClick={() => setParamsQubit(q)}
                      title="Parameters"
                      style={{
                        padding: "0.15rem 0.25rem",
                        borderRadius: "0.2rem",
                        border: "1px solid #334155",
                        background: "#1e293b",
                        color: "#6366f1",
                        cursor: "pointer",
                        fontSize: "0.6rem",
                        flexShrink: 0,
                      }}
                    >
                      ⚙
                    </button>
                    <button
                      onClick={() => removeQubit(q)}
                      title="Remove"
                      style={{
                        padding: "0.15rem 0.25rem",
                        borderRadius: "0.2rem",
                        border: "1px solid #334155",
                        background: "#1e293b",
                        color: "#64748b",
                        cursor: "pointer",
                        fontSize: "0.6rem",
                        flexShrink: 0,
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            </div>

            {/* Add qubit input */}
            <div style={{ display: "flex", gap: "0.2rem" }}>
              <input
                value={newQubitName}
                onChange={(e) => setNewQubitName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addQubit()}
                placeholder="Add..."
                style={{
                  flex: 1, padding: "0.2rem 0.3rem",
                  background: "#1e293b", color: "#e2e8f0",
                  border: "1px solid #334155", borderRadius: "0.2rem",
                  fontFamily: "monospace", fontSize: "0.65rem",
                  minWidth: 0,
                }}
              />
              <button onClick={addQubit} style={{
                padding: "0.2rem 0.35rem", borderRadius: "0.2rem",
                border: "1px solid #334155", background: "#1e3a5f",
                color: "#38bdf8", cursor: "pointer", fontSize: "0.65rem",
                flexShrink: 0,
              }}>
                +
              </button>
            </div>
          </div>
          )}  {/* End of Qubit selector conditional */}
        </aside>

        {/* ── Main content ── */}
        <main style={{ overflow: "auto", padding: "1rem", display: "flex", flexDirection: "column", gap: "1rem" }}>

          {/* Tab bar */}
          <div style={{ display: "flex", gap: "0.5rem", flexShrink: 0 }}>
            {(["experiments", "workflow", "agent", "images", "hermes"] as Tab[]).map((t) => (
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

              {/* Collapsible Commands */}
              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                {/* Run Command */}
                <CollapsibleCommand
                  type="command"
                  command={currentRunCommand || sqMap[selectedExp] + "(" + selectedQubit + ", do_plot=True)"}
                  onCommandChange={setCurrentRunCommand}
                  onRun={() => runCustom(currentRunCommand || sqMap[selectedExp] + "(" + selectedQubit + ", do_plot=True)")}
                  onSave={() => handleSaveRunCommand(selectedExp, currentRunCommand)}
                  disabled={running}
                  isRunning={isRunningCommand}
                  isSaving={isSavingCommand}
                  showRunButton={true}
                  showSaveButton={true}
                />

                {/* Plot Command */}
                <CollapsibleCommand
                  type="plot"
                  command={currentPlotCommand || "qter.fitData({exp_num})"}
                  onCommandChange={setCurrentPlotCommand}
                  onRun={() => {
                    const cmd = currentPlotCommand || `qter.fitData({exp_num})`;
                    handlePlot(cmd);
                  }}
                  onSave={() => {
                    const cmd = currentPlotCommand || `qter.fitData({exp_num})`;
                    handleSavePlotCommand(selectedExp, cmd);
                  }}
                  disabled={running}
                  isRunning={isRunningPlot}
                  isSaving={isSavingPlot}
                  showRunButton={true}
                  showSaveButton={true}
                />

                {/* Analyze Command */}
                <CollapsibleCommand
                  type="analyze"
                  command={currentAnalyzeCommand || "qter.fitData({exp_num}, collect=True, do_plot=False)"}
                  onCommandChange={setCurrentAnalyzeCommand}
                  onRun={() => {
                    setIsRunningAnalyze(true);
                    const cmd = currentAnalyzeCommand || `qter.fitData({exp_num}, collect=True, do_plot=False)`;
                    api.runAnalysis(cmd, selectedExp).then((result) => {
                      if (result.success) {
                        setAnalysisResult({
                          success: true,
                          stdout: result.stdout,
                          metrics: result.metrics,
                        });
                        addLog("✅ Analysis completed");
                      } else {
                        setAnalysisResult({
                          success: false,
                          error: result.error || "Unknown error",
                        });
                        addLog("❌ Analysis failed: " + result.error, true);
                      }
                    }).finally(() => {
                      setIsRunningAnalyze(false);
                    });
                  }}
                  onSave={() => {
                    const cmd = currentAnalyzeCommand || `qter.fitData({exp_num}, collect=True, do_plot=False)`;
                    handleSaveAnalysisCommand(selectedExp, cmd, []);
                  }}
                  disabled={running}
                  isRunning={isRunningAnalyze}
                  isSaving={isSavingAnalyze}
                  showRunButton={true}
                  showSaveButton={true}
                />
              </div>

              {/* Action Buttons */}
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
                {/* Execute All Button */}
                <button
                  onClick={handleExecuteAll}
                  disabled={running}
                  style={{
                    padding: "0.6rem 1.5rem",
                    borderRadius: "0.5rem",
                    border: "none",
                    background: running ? "#334155" : "#8b5cf6",
                    color: running ? "#64748b" : "#fff",
                    fontWeight: 700,
                    cursor: running ? "not-allowed" : "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  ⚡ Execute All
                </button>

                {/* Run Selected Experiment */}
                <button
                  onClick={() => runExperiment(selectedExp)}
                  disabled={running}
                  style={{
                    padding: "0.6rem 1.5rem",
                    borderRadius: "0.5rem",
                    border: "none",
                    background: running ? "#334155" : "#22c55e",
                    color: running ? "#64748b" : "#fff",
                    fontWeight: 600,
                    cursor: running ? "not-allowed" : "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  {running ? "⏳ Running..." : "▶ Run " + selectedExp.replace(/_/g, " ")}
                </button>

                {/* Plot Button */}
                <button
                  onClick={() => {
                    if (running) return;
                    setIsRunningPlot(true);
                    const cmd = currentPlotCommand || `qter.fitData({exp_num})`;
                    api.runPlot(cmd, selectedExp).then((result) => {
                      if (result.success) {
                        setPlotUrl(result.image || null);
                        addLog("✅ Plot completed");
                      } else {
                        addLog("❌ Plot failed: " + result.error, true);
                      }
                    }).finally(() => {
                      setIsRunningPlot(false);
                    });
                  }}
                  disabled={running || isRunningPlot}
                  style={{
                    padding: "0.6rem 1rem",
                    borderRadius: "0.5rem",
                    border: "1px solid #3b82f6",
                    background: "transparent",
                    color: running || isRunningPlot ? "#64748b" : "#3b82f6",
                    fontWeight: 600,
                    cursor: running || isRunningPlot ? "not-allowed" : "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  {isRunningPlot ? "⏳ Plotting..." : "📊 Plot"}
                </button>

                {/* Analyze Button */}
                <button
                  onClick={() => {
                    if (running) return;
                    setIsRunningAnalyze(true);
                    api.runAnalysis(currentAnalyzeCommand, selectedExp).then((result) => {
                      if (result.success) {
                        setAnalysisResult({
                          success: true,
                          stdout: result.stdout,
                          metrics: result.metrics,
                        });
                        addLog("✅ Analysis completed");
                      } else {
                        setAnalysisResult({
                          success: false,
                          error: result.error || "Unknown error",
                        });
                        addLog("❌ Analysis failed: " + result.error, true);
                      }
                    }).finally(() => {
                      setIsRunningAnalyze(false);
                    });
                  }}
                  disabled={running || isRunningAnalyze}
                  style={{
                    padding: "0.6rem 1rem",
                    borderRadius: "0.5rem",
                    border: "1px solid #f59e0b",
                    background: "transparent",
                    color: running || isRunningAnalyze ? "#64748b" : "#f59e0b",
                    fontWeight: 600,
                    cursor: running || isRunningAnalyze ? "not-allowed" : "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  {isRunningAnalyze ? "⏳ Analyzing..." : "📈 Analyze"}
                </button>

                {/* Auto LLM Summary Checkbox */}
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "0.35rem",
                    cursor: "pointer",
                    fontSize: "0.75rem",
                    color: "#94a3b8",
                    padding: "0.4rem 0.75rem",
                    background: autoAnalyze ? "#1e1b4b" : "#1e293b",
                    borderRadius: "0.375rem",
                    border: "1px solid",
                    borderColor: autoAnalyze ? "#6366f1" : "#334155",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={autoAnalyze}
                    onChange={(e) => setAutoAnalyze(e.target.checked)}
                    style={{ cursor: "pointer" }}
                  />
                  🤖 LLM
                </label>
              </div>

              {/* Unified Results Display */}
              <UnifiedResults
                plotUrl={plotUrl}
                plotLoading={plotLoading}
                analysisResult={analysisResult}
                llmSummary={llmSummary}
                isSummarizing={isSummarizing}
                onDownloadPlot={() => {
                  if (plotUrl && plotUrl.startsWith('data:')) {
                    const link = document.createElement('a');
                    link.href = plotUrl;
                    link.download = `plot_${Date.now()}.png`;
                    link.click();
                  }
                }}
                onAiSummary={handleAiSummary}
              />
            </div>
          )}

          {/* WORKFLOW TAB */}
          {activeTab === "workflow" && (
            <div style={{ flex: 1, overflow: "hidden" }}>
              <WorkflowDesigner selectedQubit={selectedQubit} onLog={addLog} />
            </div>
          )}

          {/* SERVICES TAB */}
          {/* IMAGES TAB */}
          {activeTab === "images" && (
            <ImageClassificationPanel />
          )}

          {/* AGENT TAB */}
          {activeTab === "agent" && (
            <AgentChatPanel />
          )}

          {/* HERMES TAB */}
          {activeTab === "hermes" && (
            <HermesChatPanel />
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
            OUTPUT {taskId ? "(task: " + taskId.slice(0, 8) + "...)" : ""}
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