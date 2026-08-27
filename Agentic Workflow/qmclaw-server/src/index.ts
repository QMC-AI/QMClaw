/**
 * qmclaw-server - Main Entry Point
 *
 * Architecture:
 *   Browser → Express (:3002) → Python subprocess (LabRAD + lqms)
 *                 ↓
 *              Single persistent subprocess (avoids 20s re-init per job)
 */

// Load environment variables from .env file (override existing to ensure .env takes precedence)
import dotenv from "dotenv";
import path from "path";
dotenv.config({
  override: true,
  path: path.join(__dirname, "..", ".env"),
});

// Debug: log loaded env vars
console.log("[DEBUG] MINIMAX_API_KEY loaded:", process.env.MINIMAX_API_KEY ? "YES (length=" + process.env.MINIMAX_API_KEY.length + ")" : "NO");

import express from "express";
import { createServer } from "http";
import cors from "cors";
import { spawn } from "child_process";
import { generateJobId } from "./queue/job-types";
import * as fs from "fs";
import { loadExperimentConfigs, saveExperimentConfigs, getExperimentConfig, updateExperimentConfig, ExperimentConfig } from "./services/experimentConfigService";

const PORT = process.env.PORT || 3002;
const PLOTS_DIR = process.env.PLOTS_DIR || path.join(__dirname, "..", "..", "qmclaw-web", "public", "plots");
const PYTHON_BIN = process.env.PYTHON_BIN || "python";
const SESSION_CONFIG_FILE = path.join(__dirname, "..", "config", "session.json");

// ── Session Config Helpers ────────────────────────────────────────────────────

interface SessionConfig {
  session: {
    user: string;
    path: string[];
  };
}

function loadSessionConfig(): { user: string; path: string[] } {
  const defaultConfig = { user: 'LQHL', path: ['test', '20260324'] };
  try {
    if (fs.existsSync(SESSION_CONFIG_FILE)) {
      const content = fs.readFileSync(SESSION_CONFIG_FILE, 'utf-8');
      const config: SessionConfig = JSON.parse(content);
      return config.session || defaultConfig;
    }
  } catch {
    // ignore parse errors
  }
  return defaultConfig;
}

function saveSessionConfig(user: string, pathSegments: string[]): void {
  const configDir = path.dirname(SESSION_CONFIG_FILE);
  if (!fs.existsSync(configDir)) {
    fs.mkdirSync(configDir, { recursive: true });
  }
  const config: SessionConfig = { session: { user, path: pathSegments } };
  fs.writeFileSync(SESSION_CONFIG_FILE, JSON.stringify(config, null, 2), 'utf-8');
}

function getDefaultSessionPath(): string {
  const cfg = loadSessionConfig();
  return `${cfg.user}/${cfg.path.join('/')}`;
}

// ── Known experiment functions (from sq module) ────────────────────────────────
// These are static — the actual sq.* functions are defined in the LabRAD backend.
// User can browse/run any sq.* function via the experiments tab.
const KNOWN_EXPERIMENTS = [
  { name: "spectroscopy", fullName: "sq.spectroscopy", doc: "VNA spectroscopy scan" },
  { name: "s21", fullName: "sq.s21", doc: "Cavity S21 frequency scan" },
  { name: "iqraw", fullName: "sq.iqraw", doc: "IQ raw data for qubit state discrimination" },
  { name: "t1", fullName: "sq.t1", doc: "T1 relaxation measurement" },
  { name: "ramsey_df", fullName: "sq.ramsey_df", doc: "Ramsey with detuning scan" },
  { name: "piamp", fullName: "sq.piamp", doc: "Pi pulse amplitude calibration" },
  { name: "xeb", fullName: "sq.xeb", doc: "Cross-entropy benchmarking" },
  { name: "s21_dis", fullName: "sq.s21_dis", doc: "S21 dispersive shift measurement" },
  { name: "allxy", fullName: "sq.allxy", doc: "AllXY gate characterization" },
  { name: "cr_calibrate", fullName: "sq.cr_calibrate", doc: "Cross-resonance calibration" },
  { name: "single_shot", fullName: "sq.single_shot", doc: "Single-shot fidelity measurement" },
  { name: "pulsed_spec", fullName: "sq.pulsed_spec", doc: "Pulsed spectroscopy" },
  { name: "swap", fullName: "sq.swap", doc: "SWAP gate characterization" },
  { name: "cz_calibrate", fullName: "sq.cz_calibrate", doc: "CZ gate calibration" },
  { name: "drag_calibrate", fullName: "sq.drag_calibrate", doc: "DRAG pulse calibration" },
];

// ── Job store ────────────────────────────────────────────────────────────────

type JobEntry = {
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  stdout: string;
  stderr: string;
  error: string;
  submittedAt: number;
  completedAt?: number;
  plotPath?: string;
};

const jobResults = new Map<string, JobEntry>();

// Track all job IDs in order (for job list display)
const jobHistory: string[] = [];

// ── Flask request correlation (type: "flask" messages → HTTP responses) ───────
type FlaskPendingEntry = {
  resolve: (data: unknown) => void;
  reject: (err: Error) => void;
};
const flaskPendingRequests = new Map<string, FlaskPendingEntry>();

/** Send a Flask-style message to the subprocess and resolve via correlation ID */
async function sendFlaskRequest(action: string, data: Record<string, unknown>, timeoutMs = 30_000): Promise<unknown> {
  await ensureSubprocess();
  if (!pyProc || !pyProc.stdin) throw new Error("Worker not running");

  const cid = "f" + Date.now() + Math.random().toString(36).slice(2, 8);
  const msg = JSON.stringify({ type: "flask", cid, action, data }) + "\n";
  console.log(`[Flask Request] cid=${cid} action=${action}`);

  return new Promise((resolve, reject) => {
    flaskPendingRequests.set(cid, { resolve, reject });
    try {
      console.log(`[Flask Request] Writing: ${msg.trim().slice(0, 200)}`);
      if (!pyProc || !pyProc.stdin) throw new Error("Worker stdin not available");
      const ok = pyProc.stdin.write(msg);
      console.log(`[Flask Request] stdin.write ok=${ok}`);
    } catch (err: any) {
      flaskPendingRequests.delete(cid);
      reject(err);
    }
    // Timeout
    setTimeout(() => {
      if (flaskPendingRequests.has(cid)) {
        flaskPendingRequests.delete(cid);
        reject(new Error("Flask request timeout"));
      }
    }, timeoutMs);
  });
}

// ── Express setup ────────────────────────────────────────────────────────────

const app = express();
app.use(cors());
app.use(express.json());

// ── Persistent subprocess ────────────────────────────────────────────────────

// One long-running Python subprocess; all jobs run through it.
// Avoids re-initializing LabRAD connection (20+ seconds) per job.

let pyProc: ReturnType<typeof spawn> | null = null;
let pyProcReady = false;
let pyProcBuffer = "";  // accumulates subprocess stdout

/** Spawn the persistent Python subprocess and set up I/O handlers */
function ensureSubprocess(): Promise<void> {
  if (pyProc) return Promise.resolve();

  return new Promise((resolve, reject) => {
    const scriptPath = path.join(process.cwd(), "scripts", "job_runner.py");
    pyProc = spawn(PYTHON_BIN, [scriptPath, "--interactive"], {
      cwd: path.join(process.cwd(), "scripts"),
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      env: {
        ...process.env,
        OPENAI_API_KEY: process.env.OPENAI_API_KEY || "",
        MINIMAX_API_KEY: process.env.MINIMAX_API_KEY || "",
        MINIMAX_GROUP_ID: process.env.MINIMAX_GROUP_ID || "",
        ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY || "",
        DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY || "",
        PLOTS_DIR: PLOTS_DIR.replace(/\\/g, "\\\\"),
        PYTHONUNBUFFERED: "1",
      },
    });

    pyProc.stderr?.on("data", (data: Buffer) => {
      const raw = data.toString();
      const lines = raw.split("\n");
      for (const line of lines) {
        if (!line.trim()) continue;
        if (!pyProcReady && line.startsWith("INIT")) {
          process.stderr.write(line + "\n");
        }
        // Accept READY on any line (not just startsWith) to handle partial chunks
        if (!pyProcReady && line.includes("READY")) {
          pyProcReady = true;
          console.log("[qmclaw] Python worker ready");
          resolve();
        }
        // Always print MiniMax debug messages
        if (line.includes("[MiniMax Debug]")) {
          console.log(line);
        }
      }
    });

    pyProc.stdout?.on("data", (data: Buffer) => {
      pyProcBuffer += data.toString();
      // Process line-by-line JSONL
      const lines = pyProcBuffer.split("\n");
      pyProcBuffer = lines.pop() ?? ""; // keep incomplete line in buffer
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const obj = JSON.parse(line);
          // Workflow progress: {"type":"workflow_progress", ...}
          if (obj.type === "workflow_progress") {
            handleWorkflowProgress(obj as { workflowId: string; nodeId: string; status: string });
            continue;
          }
          // Workflow result: has workflowId field
          if (obj.workflowId) {
            handleWorkflowResult(obj as WorkflowResultMsg);
            continue;
          }
          // Flask result: correlation ID based dispatch
          if (obj.type === "flask_result" || (obj.cid && obj.action && !obj.workflowId)) {
            console.log(`[Flask Result] cid=${obj.cid} action=${obj.action} hasError=${!!obj.error}`);
            handleFlaskResult(obj as { cid: string; action: string; data?: unknown; error?: string });
            continue;
          }
          // Job result: has status field
          handleSubprocessResult(obj as { status: string; stdout: string; stderr: string; error: string });
        } catch { /* ignore parse errors during init */ }
      }
    });

    pyProc.on("error", (err) => {
      console.error("[qmclaw] Python subprocess error:", err.message);
      pyProc = null;
      pyProcReady = false;
    });

    pyProc.on("close", (code) => {
      console.warn(`[qmclaw] Python subprocess exited with code ${code}`);
      pyProc = null;
      pyProcReady = false;
    });

    // Timeout if READY not received within 90s (LabRAD + Ray init)
    setTimeout(() => {
      if (!pyProcReady) {
        console.error("[qmclaw] Python worker init timeout - no READY received");
        pyProc?.kill();
        pyProc = null;
        reject(new Error("Worker init timeout"));
      }
    }, 90_000);
  });
}

// ── Workflow result handlers ──────────────────────────────────────────────────

type NodeResult = {
  status: string; type: string; stdout: string; stderr: string;
  error: string; plotPath?: string; metrics?: Record<string, number>;
};

type WorkflowResultMsg = {
  status: string; workflowId: string; workflowName: string;
  stdout: string; stderr: string; error: string;
  nodeResults: Record<string, { result: NodeResult }>;
};

function handleWorkflowProgress(progress: { workflowId: string; nodeId: string; status: string }): void {
  console.log(`[qmclaw] workflow progress: ${progress.workflowId} / ${progress.nodeId} = ${progress.status}`);
  const wf = workflowResults.get(progress.workflowId);
  if (!wf) return;
  // Initialize node if not present
  if (!wf.nodes[progress.nodeId]) {
    wf.nodes[progress.nodeId] = { status: "pending", type: "unknown", stdout: "", error: "" };
  }
  wf.nodes[progress.nodeId].status = progress.status;
}

function handleWorkflowResult(result: WorkflowResultMsg): void {
  console.log(`[qmclaw] workflow result: ${result.workflowId} = ${result.status}`);
  const wf = workflowResults.get(result.workflowId);
  if (!wf) { console.warn(`[qmclaw] unknown workflow: ${result.workflowId}`); return; }

  // Parse node results
  wf.nodes = {};
  const nodeResults: Record<string, any> = {};
  if (result.nodeResults) {
    for (const [nodeId, nr] of Object.entries(result.nodeResults)) {
      const r = nr.result;
      // Extract plot path from stdout
      let plotPath = r.plotPath;
      let stdout = r.stdout || "";
      if (stdout && !plotPath) {
        const m = stdout.match(/QMCLAW_PLOT:([^\n]+)/);
        if (m) { plotPath = m[1]; stdout = stdout.replace(/QMCLAW_PLOT:[^\n]+\n?/g, ""); }
      }
      wf.nodes[nodeId] = {
        status: r.status || "unknown",
        type: r.type || "unknown",
        stdout,
        error: r.error || "",
        plotPath,
        metrics: r.metrics,
      };
      // Build full node result with input/output for persistence
      nodeResults[nodeId] = {
        status: r.status || "unknown",
        type: r.type || "unknown",
        stdout,
        stderr: r.stderr || "",
        error: r.error || "",
        plotPath,
        metrics: r.metrics,
        duration: r.duration,
        input: r.input,
        conversation: r.conversation,
        recommendations: r.recommendations,
        symptom: r.symptom,
        reasoning: r.reasoning,
        matchedRules: r.matchedRules,
      };
    }
  }

  wf.status = result.status === "completed" || result.status === "passed" ? "completed" : result.status === "error" ? "failed" : "failed";
  wf.completedAt = Date.now();

  // Parse context from stdout JSON
  try {
    const parsed = JSON.parse(result.stdout);
    wf.context = parsed.context || {};
  } catch { /* ignore */ }

  // Persist workflow run to disk
  try {
    const runStatus = wf.status === "completed" ? "completed" as const : "failed" as const;
    createWorkflowRun({
      workflowId: result.workflowId,
      workflowName: result.workflowName || wf.name,
      status: runStatus,
      startedAt: wf.submittedAt,
      completedAt: wf.completedAt,
      context: wf.context,
      nodeResults,
    });
    console.log(`[qmclaw] workflow run persisted: run_${result.workflowId}_${wf.submittedAt}`);
  } catch (err) {
    console.error(`[qmclaw] Failed to persist workflow run:`, err);
  }
}

function handleSubprocessResult(result: { status: string; stdout: string; stderr: string; error: string }): void {
  console.log(`[qmclaw] handleSubprocessResult: ${JSON.stringify(result).slice(0, 100)}`);
  // Find the oldest running job (persistent subprocess, so FIFO queue)
  let found = false;
  for (const [jobId, entry] of jobResults.entries()) {
    if (entry.status === "running") {
      found = true;
      // This job is waiting for a result
      let plotPath: string | undefined;
      let stdout = result.stdout || "";
      if (stdout) {
        const plotMatch = stdout.match(/QMCLAW_PLOT:([^\n]+)/);
        if (plotMatch) {
          plotPath = plotMatch[1];
          stdout = stdout.replace(/QMCLAW_PLOT:[^\n]+\n?/g, "");
        }
      }
      jobResults.set(jobId, {
        ...entry,
        status: result.status === "success" ? "completed" : result.status === "cancelled" ? "cancelled" : "failed",
        stdout,
        stderr: result.stderr || "",
        error: result.error || "",
        plotPath,
        completedAt: Date.now(),
      });
      return; // one result per call
    }
  }
  if (!found) console.log(`[qmclaw] handleSubprocessResult: no running job found, result=${JSON.stringify(result).slice(0, 80)}`);
}

function handleFlaskResult(result: { cid: string; action: string; data?: unknown; error?: string }): void {
  const entry = flaskPendingRequests.get(result.cid);
  if (!entry) { console.log(`[qmclaw] handleFlaskResult: unknown cid=${result.cid}`); return; }
  flaskPendingRequests.delete(result.cid);
  if (result.error) {
    entry.reject(new Error(result.error));
  } else {
    entry.resolve(result.data);
  }
}

/** Send a job to the persistent subprocess */
async function runSubprocess(jobId: string, wrappedCode: string): Promise<void> {
  console.log(`[qmclaw] runSubprocess: waiting for worker, jobId=${jobId}`);
  await ensureSubprocess();
  console.log(`[qmclaw] runSubprocess: worker ready, pyProc=${!!pyProc}, stdin=${!!pyProc?.stdin}`);

  if (!pyProc || !pyProc.stdin) {
    jobResults.set(jobId, { status: "failed", stdout: "", stderr: "", error: "Worker not running", submittedAt: jobResults.get(jobId)?.submittedAt ?? Date.now(), completedAt: Date.now() });
    return;
  }

  const b64 = Buffer.from(wrappedCode).toString("base64");
  const msg = JSON.stringify({ code: b64, jobId }) + "\n";

  jobResults.set(jobId, { status: "running", stdout: "", stderr: "", error: "", submittedAt: Date.now() });
  console.log(`[qmclaw] runSubprocess: submitting job ${jobId}`);

  try {
    const ok = pyProc.stdin.write(msg);
    console.log(`[qmclaw] stdin.write ok=${ok}, jobId=${jobId}`);
  } catch (err: any) {
    console.error(`[qmclaw] stdin.write error: ${err.message}`);
    jobResults.set(jobId, { status: "failed", stdout: "", stderr: "", error: err.message, submittedAt: jobResults.get(jobId)!.submittedAt, completedAt: Date.now() });
  }
}

/** Kill the persistent subprocess (cancels all running jobs) */
function killSubprocess(): void {
  if (pyProc) {
    pyProc.kill("SIGTERM");
    setTimeout(() => { try { pyProc?.kill("SIGKILL"); } catch { /* ignore */ } }, 500);
    pyProc = null;
    pyProcReady = false;
    pyProcBuffer = "";
  }
}

// ── Routes ───────────────────────────────────────────────────────────────────

/** POST /job — spawn a subprocess job, return jobId immediately */
interface JobSubmission {
  code?: string;
  plotCommand?: string;
  analysisPrompt?: string;
  autoAnalyze?: boolean;
  model?: string;
  _modelProvider?: string;
  _modelBaseUrl?: string;
  temperature?: number;
}

app.post("/job", async (req, res) => {
  const { code, plotCommand, analysisPrompt, autoAnalyze, model, _modelProvider, _modelBaseUrl, temperature } = req.body as JobSubmission;
  if (!code) { res.status(400).json({ error: "No code provided" }); return; }

  const jobId = generateJobId();
  const wrappedCode = wrapExperimentCode(jobId, code, { plotCommand, analysisPrompt, autoAnalyze, model, _modelProvider, _modelBaseUrl, temperature });

  jobResults.set(jobId, { status: "pending", stdout: "", stderr: "", error: "", submittedAt: Date.now() });
  jobHistory.unshift(jobId);
  if (jobHistory.length > 50) jobHistory.pop();

  // Fire and forget
  runSubprocess(jobId, wrappedCode).catch(console.error);

  res.json({ jobId, status: "pending" });
});

/** GET /job/:id — get job status/result */
app.get("/job/:jobId", (req, res) => {
  const job = jobResults.get(req.params.jobId);
  if (!job) { res.status(404).json({ error: "Job not found" }); return; }
  res.json(job);
});

/** DELETE /job/:jobId — cancel a running job */
app.delete("/job/:jobId", (req, res) => {
  const job = jobResults.get(req.params.jobId);
  if (!job) { res.status(404).json({ error: "Job not found" }); return; }

  const jobId = req.params.jobId;

  if (job.status === "pending" || job.status === "running") {
    // Write cancel flag so the subprocess notices on its next poll
    const cancelFile = path.join(process.env.TEMP || "/tmp", `qmclaw_cancel_${jobId}.flag`);
    require("fs").writeFileSync(cancelFile, "cancelled");

    // Kill subprocess so it picks up the cancel flag immediately
    killSubprocess();

    // All running/pending jobs get cancelled since subprocess dies
    for (const [id, entry] of jobResults.entries()) {
      if ((entry.status === "pending" || entry.status === "running") && id === jobId) {
        jobResults.set(id, { ...entry, status: "cancelled", completedAt: Date.now() });
      }
    }
    res.json({ jobId, status: "cancelled" });
  } else {
    jobResults.set(jobId, { ...job, status: "cancelled", completedAt: Date.now() });
    res.json({ jobId, status: "cancelled" });
  }
});

/** GET /jobs — list all jobs (newest first) */
app.get("/jobs", (_req, res) => {
  const jobs = jobHistory
    .map((id) => ({ id, ...jobResults.get(id) }))
    .filter((j) => j.status !== undefined);
  res.json(jobs);
});

/** GET /result/:jobId — legacy result endpoint */
app.get("/result/:jobId", (req, res) => {
  const result = jobResults.get(req.params.jobId);
  if (!result) { res.status(404).json({ error: "Job not found" }); return; }
  res.json(result);
});

/** GET /health */
app.get("/health", async (_req, res) => {
  try {
    const data = await sendFlaskRequest("health", {}, 5000) as {
      status: string; ready: boolean; busy: boolean;
      session: { conn_id: string; name: string; host: string; port: number; connected: boolean } | null;
    };
    res.json({
      express: "ok",
      subprocess: pyProcReady ? "ready" : (pyProc ? "initializing" : "stopped"),
      flask: data,
    });
  } catch {
    res.json({ express: "ok", subprocess: pyProcReady ? "ready" : "stopped", flask: "unreachable" });
  }
});

// ── Background runner (Flask proxy) ─────────────────────────────────────────

function wrapExperimentCode(jobId: string, code: string, config?: {
  plotCommand?: string;
  analysisPrompt?: string;
  autoAnalyze?: boolean;
  model?: string;
  _modelProvider?: string;
  _modelBaseUrl?: string;
  temperature?: number;
}): string {
  // Escape backslashes for Python raw string
  const plotsDir = PLOTS_DIR.replace(/\\/g, "\\\\");
  const plotCmd = config?.plotCommand?.replace(/\\/g, "\\\\").replace(/'/g, "\\'") || "";
  const analysisPrompt = config?.analysisPrompt?.replace(/\\/g, "\\\\").replace(/'/g, "\\'") || "分析这个量子比特实验图像，描述你看到的波形特征和质量";
  const autoAnalyze = config?.autoAnalyze !== false;  // default to true
  const autoAnalyzePy = autoAnalyze ? "True" : "False";  // Python boolean
  const model = config?.model || "gpt-4o";
  const modelProvider = config?._modelProvider || "";
  const modelBaseUrl = config?._modelBaseUrl || "";
  const temperature = config?.temperature ?? 0.3;

  return `
import os
import sys
import json
import base64
import matplotlib.pyplot as plt
import numpy as np

# Clear all existing figures to avoid getting stale plots from previous experiments
plt.close('all')

_plots_dir = r"${plotsDir}"
os.makedirs(_plots_dir, exist_ok=True)

${code}

# Fallback: if no plot was created, try to load data from DataVault and plot
_fig = plt.gcf()
_plot_created = False
if not _fig or _fig.get_size_inches().prod() == 0:
    # No plot created by the experiment, try to plot from DataVault
    try:
        if 'data' in dir() and hasattr(data, 'loadDataset'):
            data.loadDataset(-1)
            _x = data.data[:, 0]
            _y = data.data[:, 1] if data.data.shape[1] > 1 else data.data[:, 0]
            _fig = plt.figure(figsize=(10, 6))
            plt.plot(_x, _y, 'b.-')
            plt.title(f'DataVault: {getattr(data, "dataset_name", "Latest")}')
            plt.xlabel('X')
            plt.ylabel('Y')
            plt.grid(True)
            plt.tight_layout()
            _plot_created = True
    except Exception as _e:
        print(f"QMCLAW_FALLBACK_PLOT_ERROR: {_e}", file=sys.stderr)

_plot_modified = False
if _fig and _fig.get_size_inches().prod() > 0:
    # Execute custom plot commands if provided
    if """${plotCmd}""".strip():
        try:
            exec("""${plotCmd}""")
            _plot_modified = True
        except Exception as _e:
            print(f"QMCLAW_PLOT_ERROR: Plot command failed: {_e}", file=sys.stderr)

    # Save the plot (modified or original)
    _path = os.path.join(_plots_dir, "${jobId}.png")
    _fig.savefig(_path, dpi=150, bbox_inches='tight')
    print(f"QMCLAW_PLOT:{_path}")
    if _plot_modified:
        print(f"QMCLAW_PLOT_MODIFIED:True")
    if _plot_created:
        print(f"QMCLAW_PLOT_FALLBACK:True")
    plt.close(_fig)

    # Run LLM analysis if enabled
    if ${autoAnalyzePy}:
        try:
            _model = "${model}"
            _provider = "${modelProvider}"
            _base_url = "${modelBaseUrl}"
            _temp = ${temperature}
            _prompt = """${analysisPrompt}"""

            # Import LLM client based on provider
            if _provider == "minimax" or "minimax" in _model.lower():
                try:
                    from openai import OpenAI
                    _api_key = os.environ.get("MINIMAX_API_KEY", "")
                    _client = OpenAI(api_key=_api_key, base_url="https://api.minimax.chat/v1")
                    _response = _client.chat.completions.create(
                        model=_model,
                        messages=[{"role": "user", "content": [{"type": "text", "text": _prompt}, {"type": "image_url", "image_url": {"url": f"file://{_path}"}}]}],
                        temperature=_temp,
                    )
                    _analysis = _response.choices[0].message.content
                except Exception as _e:
                    _analysis = f"Analysis failed: {_e}"
            elif _provider == "deepseek" or "deepseek" in _model.lower():
                try:
                    from openai import OpenAI
                    _api_key = os.environ.get("DEEPSEEK_API_KEY", "")
                    _client = OpenAI(api_key=_api_key, base_url="https://api.deepseek.com")
                    _response = _client.chat.completions.create(
                        model=_model,
                        messages=[{"role": "user", "content": [{"type": "text", "text": _prompt}, {"type": "image_url", "image_url": {"url": f"file://{_path}"}}]}],
                        temperature=_temp,
                    )
                    _analysis = _response.choices[0].message.content
                except Exception as _e:
                    _analysis = f"Analysis failed: {_e}"
            elif _provider == "anthropic" or "claude" in _model.lower():
                try:
                    import anthropic
                    _api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                    _client = anthropic.Anthropic(api_key=_api_key)
                    with open(_path, "rb") as _img_file:
                        _img_data = base64.b64encode(_img_file.read()).decode()
                    _response = _client.messages.create(
                        model=_model,
                        max_tokens=1024,
                        messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _img_data}}, {"type": "text", "text": _prompt}]}]
                    )
                    _analysis = _response.content[0].text
                except Exception as _e:
                    _analysis = f"Analysis failed: {_e}"
            else:
                # Default to OpenAI
                try:
                    from openai import OpenAI
                    _api_key = os.environ.get("OPENAI_API_KEY", "")
                    _client = OpenAI(api_key=_api_key)
                    _response = _client.chat.completions.create(
                        model=_model,
                        messages=[{"role": "user", "content": [{"type": "text", "text": _prompt}, {"type": "image_url", "image_url": {"url": f"file://{_path}"}}]}],
                        temperature=_temp,
                    )
                    _analysis = _response.choices[0].message.content
                except Exception as _e:
                    _analysis = f"Analysis failed: {_e}"

            print(f"QMCLAW_ANALYSIS:{json.dumps(_analysis)}")
        except Exception as _e:
            print(f"QMCLAW_ANALYSIS_ERROR:{str(_e)}", file=sys.stderr)
`;
}


// ── Workflow store ────────────────────────────────────────────────────────────

type WorkflowNode = {
  id: string; type: string; depends?: string[]; config: Record<string, unknown>;
};

type WorkflowEntry = {
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  name: string; workflowId: string; submittedAt: number; completedAt?: number; error?: string;
  nodes: Record<string, { status: string; type: string; stdout: string; error: string; plotPath?: string; metrics?: Record<string, number> }>;
  context: Record<string, string>;
};

const workflowResults = new Map<string, WorkflowEntry>();
const workflowHistory: string[] = [];

// ── Workflow submission ────────────────────────────────────────────────────────

/** POST /workflow — submit a workflow for execution */
app.post("/workflow", async (req, res) => {
  const body = req.body as { name?: string; nodes?: WorkflowNode[]; context?: Record<string, string> };
  if (!body.nodes || !Array.isArray(body.nodes)) {
    res.status(400).json({ error: "Invalid workflow: nodes array required" });
    return;
  }

  const workflowId = generateJobId();
  const entry: WorkflowEntry = {
    status: "pending", name: body.name || "Unnamed Workflow",
    workflowId, submittedAt: Date.now(), nodes: {}, context: body.context || {},
  };
  workflowResults.set(workflowId, entry);
  workflowHistory.unshift(workflowId);
  if (workflowHistory.length > 50) workflowHistory.pop();

  // Send to subprocess as workflow message type
  await ensureSubprocess();
  if (!pyProc || !pyProc.stdin) {
    workflowResults.set(workflowId, { ...entry, status: "failed", completedAt: Date.now() });
    res.json({ workflowId, status: "failed", error: "Worker not running" });
    return;
  }

  const wfJson = JSON.stringify({ name: body.name, nodes: body.nodes, context: body.context || {} });
  console.log(`[qmclaw] workflow submit: name=${body.name}, nodes=${body.nodes?.length}, nodeIds=${JSON.stringify(body.nodes?.map(n => ({id: n.id, type: n.type})))}`);
  const b64 = Buffer.from(wfJson).toString("base64");
  const msg = JSON.stringify({ type: "workflow", workflow: b64, workflowId }) + "\n";

  workflowResults.set(workflowId, { ...entry, status: "running" });
  console.log(`[qmclaw] workflow: submitted ${workflowId}`);

  try {
    pyProc.stdin.write(msg);
  } catch (err: any) {
    workflowResults.set(workflowId, { ...entry, status: "failed", error: err.message, completedAt: Date.now() });
  }

  res.json({ workflowId, status: "pending" });
});

/** GET /workflow/:id — get workflow status */
app.get("/workflow/:workflowId", (req, res) => {
  const wf = workflowResults.get(req.params.workflowId);
  if (!wf) { res.status(404).json({ error: "Workflow not found" }); return; }
  res.json(wf);
});

/** DELETE /workflow/:id — cancel workflow */
app.delete("/workflow/:workflowId", (req, res) => {
  const wf = workflowResults.get(req.params.workflowId);
  if (!wf) { res.status(404).json({ error: "Workflow not found" }); return; }
  if (wf.status === "pending" || wf.status === "running") {
    const flagFile = path.join(process.env.TEMP || "/tmp", `qmclaw_cancel_${req.params.workflowId}.flag`);
    require("fs").writeFileSync(flagFile, "cancelled");
    killSubprocess();
    workflowResults.set(req.params.workflowId, { ...wf, status: "cancelled", completedAt: Date.now() });
  }
  res.json({ workflowId: req.params.workflowId, status: "cancelled" });
});

/** GET /workflows — list all workflows */
app.get("/workflows", (_req, res) => {
  const wfs = workflowHistory.map((id) => ({ id, ...workflowResults.get(id) })).filter((w) => w.status !== undefined);
  res.json(wfs);
});

// ── Workflow Persistence API ────────────────────────────────────────────────

import { listWorkflows, getWorkflow, saveWorkflow, deleteWorkflow, exportWorkflow, importWorkflow } from "./services/workflowService";
import { listWorkflowRuns, getWorkflowRun, saveWorkflowRun, createWorkflowRun, deleteWorkflowRun, getWorkflowStats } from "./services/workflowRunService";

/** GET /api/workflows — list saved workflows (persistent) */
app.get("/api/workflows", (_req, res) => {
  try {
    const workflows = listWorkflows();
    res.json(workflows);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** POST /api/workflows — save a workflow */
app.post("/api/workflows", (req, res) => {
  try {
    const { id, name, nodes, edges, settings } = req.body;
    const workflow = saveWorkflow(id || null, { name, nodes, edges, settings });
    res.json(workflow);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/workflows/:id — get a workflow */
app.get("/api/workflows/:id", (req, res) => {
  try {
    const workflow = getWorkflow(req.params.id);
    if (!workflow) {
      res.status(404).json({ error: "Workflow not found" });
      return;
    }
    res.json(workflow);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** PUT /api/workflows/:id — update a workflow */
app.put("/api/workflows/:id", (req, res) => {
  try {
    const { name, nodes, edges, settings } = req.body;
    const workflow = saveWorkflow(req.params.id, { name, nodes, edges, settings });
    res.json(workflow);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** DELETE /api/workflows/:id — delete a workflow */
app.delete("/api/workflows/:id", (req, res) => {
  try {
    const deleted = deleteWorkflow(req.params.id);
    if (!deleted) {
      res.status(404).json({ error: "Workflow not found" });
      return;
    }
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/workflows/:id/export — export workflow as JSON */
app.get("/api/workflows/:id/export", (req, res) => {
  try {
    const json = exportWorkflow(req.params.id);
    if (!json) {
      res.status(404).json({ error: "Workflow not found" });
      return;
    }
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Content-Disposition", `attachment; filename="${req.params.id}.json"`);
    res.send(json);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** POST /api/workflows/import — import workflow from JSON */
app.post("/api/workflows/import", (req, res) => {
  try {
    const { json } = req.body;
    if (!json) {
      res.status(400).json({ error: "JSON content required" });
      return;
    }
    const workflow = importWorkflow(json);
    if (!workflow) {
      res.status(400).json({ error: "Invalid workflow JSON" });
      return;
    }
    res.json(workflow);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Workflow Runs API ──────────────────────────────────────────────────────

/** GET /api/workflow-runs — list all workflow runs, optionally filtered by workflowId or workflowName */
app.get("/api/workflow-runs", (req, res) => {
  try {
    const workflowId = req.query.workflowId as string | undefined;
    const workflowName = req.query.workflowName as string | undefined;
    const runs = listWorkflowRuns(workflowId, workflowName);
    res.json(runs);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/workflow-runs/stats/:workflowId — get run statistics for a workflow */
app.get("/api/workflow-runs/stats/:workflowId", (req, res) => {
  try {
    const stats = getWorkflowStats(req.params.workflowId);
    res.json(stats);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/workflow-runs/:runId — get a specific workflow run */
app.get("/api/workflow-runs/:runId", (req, res) => {
  try {
    const run = getWorkflowRun(req.params.runId);
    if (!run) {
      res.status(404).json({ error: "Workflow run not found" });
      return;
    }
    res.json(run);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** DELETE /api/workflow-runs/:runId — delete a workflow run */
app.delete("/api/workflow-runs/:runId", (req, res) => {
  try {
    const deleted = deleteWorkflowRun(req.params.runId);
    if (!deleted) {
      res.status(404).json({ error: "Workflow run not found" });
      return;
    }
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Single Node Execution ──────────────────────────────────────────────

/** POST /api/run-node — execute a single node */
app.post("/api/run-node", async (req, res) => {
  try {
    let { node, context } = req.body;
    if (!node || !node.id) {
      res.status(400).json({ error: "Node object required" });
      return;
    }

    await ensureSubprocess();
    if (!pyProc || !pyProc.stdin) {
      res.status(503).json({ error: "Worker not running" });
      return;
    }

    // Resolve model name to modelId for LLM nodes (decision, image_analysis)
    const nodeType = node.type || node.data?.type;
    const config = node.config || node.data?.config || {};
    if ((nodeType === "decision" || nodeType === "image_analysis") && config.model) {
      try {
        const model = getModelByName(config.model);
        if (model) {
          // Store both name and resolved modelId/provider for execution
          config._modelName = config.model;
          config.model = model.modelId;
          config._modelProvider = model.provider;
          if (model.baseUrl) config._modelBaseUrl = model.baseUrl;
        }
      } catch {
        // Model lookup failed, use config.model as-is
      }
    }

    const msg = JSON.stringify({
      type: "run_node",
      node,
      context: context || {},
    }) + "\n";

    // Write to subprocess and read response
    const proc = pyProc; // capture for closure
    const result = await new Promise<any>((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error("Node execution timed out"));
      }, 300000); // 5 min timeout

      if (!proc) {
        reject(new Error("Worker not available"));
        return;
      }

      proc.stdin?.write(msg);

      // Listen for the response - this is a one-shot, need to handle carefully
      const onData = (data: Buffer) => {
        try {
          const lines = data.toString().split("\n");
          for (const line of lines) {
            if (!line.trim()) continue;
            const obj = JSON.parse(line);
            if (obj.nodeId === node.id) {
              clearTimeout(timeout);
              proc.stdout?.off("data", onData);
              resolve(obj);
              return;
            }
          }
        } catch {}
      };

      proc.stdout?.on("data", onData);
    });

    res.json(result);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Node Templates API ──────────────────────────────────────────────

import { listTemplates, saveTemplate, deleteTemplate } from "./services/workflowService";

/** GET /api/templates — list all node templates */
app.get("/api/templates", (_req, res) => {
  try {
    const templates = listTemplates();
    res.json(templates);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** POST /api/templates — create a template */
app.post("/api/templates", (req, res) => {
  try {
    const { name, type, config, tags, author, version } = req.body;
    if (!name || !type || !config) {
      res.status(400).json({ error: "Name, type, and config required" });
      return;
    }
    const template = saveTemplate({
      name,
      type,
      config,
      tags: tags || [],
      author: author || "anonymous",
      version: version || "1.0",
    });
    res.json(template);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** DELETE /api/templates/:id — delete a template */
app.delete("/api/templates/:id", (req, res) => {
  try {
    const deleted = deleteTemplate(req.params.id);
    if (!deleted) {
      res.status(404).json({ error: "Template not found" });
      return;
    }
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Model Registry API ───────────────────────────────────────────────────

import { listModels, getModel, getModelByName, saveModel, deleteModel, initializeDefaultModels } from "./services/modelService";
import { createSession, addMessage, getSession, listSessions, deleteSession, getSessionsByModel } from "./services/chatService";

// Initialize default models on startup
initializeDefaultModels();

/** GET /api/models — list all registered models */
app.get("/api/models", (_req, res) => {
  try {
    const models = listModels();
    res.json(models);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** POST /api/models — create a new model */
app.post("/api/models", (req, res) => {
  try {
    const { name, provider, modelId, baseUrl, enabled, capabilities, config } = req.body;
    if (!name || !provider || !modelId) {
      res.status(400).json({ error: "name, provider, and modelId are required" });
      return;
    }
    const model = saveModel(null, {
      name,
      provider,
      modelId,
      baseUrl,
      enabled: enabled !== false,
      capabilities: capabilities || ['text'],
      config: config || {},
    });
    res.json(model);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/models/:id — get a model by ID */
app.get("/api/models/:id", (req, res) => {
  try {
    const model = getModel(req.params.id);
    if (!model) {
      res.status(404).json({ error: "Model not found" });
      return;
    }
    res.json(model);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/models/name/:name — get a model by name */
app.get("/api/models/name/:name", (req, res) => {
  try {
    const model = getModelByName(decodeURIComponent(req.params.name));
    if (!model) {
      res.status(404).json({ error: "Model not found" });
      return;
    }
    res.json(model);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** PUT /api/models/:id — update a model */
app.put("/api/models/:id", (req, res) => {
  try {
    const existing = getModel(req.params.id);
    if (!existing) {
      res.status(404).json({ error: "Model not found" });
      return;
    }
    const { name, provider, modelId, baseUrl, enabled, capabilities, config } = req.body;
    const model = saveModel(req.params.id, {
      name: name ?? existing.name,
      provider: provider ?? existing.provider,
      modelId: modelId ?? existing.modelId,
      baseUrl: baseUrl !== undefined ? baseUrl : existing.baseUrl,
      enabled: enabled !== undefined ? enabled : existing.enabled,
      capabilities: capabilities ?? existing.capabilities,
      config: config ?? existing.config,
    });
    res.json(model);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** DELETE /api/models/:id — delete a model */
app.delete("/api/models/:id", (req, res) => {
  try {
    const deleted = deleteModel(req.params.id);
    if (!deleted) {
      res.status(404).json({ error: "Model not found" });
      return;
    }
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Chat Test API ──────────────────────────────────────────────────────

/** POST /api/chat/test — test a model with a message */
app.post("/api/chat/test", async (req, res) => {
  try {
    const { modelName, message, systemPrompt, temperature, maxTokens, sessionId } = req.body;

    if (!modelName || !message) {
      res.status(400).json({ error: "modelName and message are required" });
      return;
    }

    // Get model by name
    const model = getModelByName(modelName);
    if (!model) {
      res.status(404).json({ error: `Model "${modelName}" not found` });
      return;
    }

    // Get API key based on provider
    const apiKey = getAPIKeyForProvider(model.provider);
    if (!apiKey) {
      res.status(400).json({ error: `No API key configured for provider: ${model.provider}` });
      return;
    }

    // Call LLM via Python subprocess
    const messages = [
      ...(systemPrompt ? [{ role: 'system' as const, content: systemPrompt }] : []),
      { role: 'user' as const, content: message },
    ];

    const result = await sendFlaskRequest('llm_chat', {
      provider: model.provider,
      modelId: model.modelId,
      baseUrl: model.baseUrl,
      messages,
      temperature: temperature ?? model.config.temperature ?? 0.3,
      maxTokens: maxTokens ?? model.config.maxTokens ?? 500,
    }) as { content?: string; error?: string; usage?: Record<string, number> };

    if (result.error) {
      res.status(500).json({ error: result.error });
      return;
    }

    // Save to session if sessionId provided
    if (sessionId) {
      addMessage(sessionId, { role: 'user', content: message });
      addMessage(sessionId, { role: 'assistant', content: result.content || '' });
    }

    res.json({
      content: result.content,
      model: modelName,
      modelId: model.modelId,
      usage: result.usage,
    });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/chat/sessions — list all chat sessions */
app.get("/api/chat/sessions", (_req, res) => {
  try {
    const sessions = listSessions();
    res.json(sessions);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** POST /api/chat/sessions — create a new chat session */
app.post("/api/chat/sessions", (req, res) => {
  try {
    const { modelId, modelName } = req.body;
    if (!modelId || !modelName) {
      res.status(400).json({ error: "modelId and modelName are required" });
      return;
    }
    const session = createSession(modelId, modelName);
    res.json(session);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/chat/sessions/:id — get a chat session */
app.get("/api/chat/sessions/:id", (req, res) => {
  try {
    const session = getSession(req.params.id);
    if (!session) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    res.json(session);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/chat/sessions/model/:modelId — get sessions for a model */
app.get("/api/chat/sessions/model/:modelId", (req, res) => {
  try {
    const sessions = getSessionsByModel(req.params.modelId);
    res.json(sessions);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** DELETE /api/chat/sessions/:id — delete a chat session */
app.delete("/api/chat/sessions/:id", (req, res) => {
  try {
    const deleted = deleteSession(req.params.id);
    if (!deleted) {
      res.status(404).json({ error: "Session not found" });
      return;
    }
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** POST /api/chat/analyze-plot — LLM summarization of plot analysis output */
app.post("/api/chat/analyze-plot", async (req, res) => {
  try {
    const { analysis_output, modelName, systemPrompt } = req.body as {
      analysis_output?: string;
      modelName?: string;
      systemPrompt?: string;
    };

    if (!analysis_output) {
      res.status(400).json({ error: "analysis_output is required" });
      return;
    }

    // Default system prompt for plot analysis
    const defaultSystemPrompt = `你是一位量子计算测控实验数据分析专家。请分析测控实验的统计信息，用简洁的中文总结关键指标和建议。请提供：
1. 关键指标摘要
2. 数据质量评估
3. 建议（如有）`;

    // If modelName is provided, use it; otherwise use first available model
    let targetModelName = modelName;

    if (!targetModelName) {
      const models = await import("./services/modelService");
      const allModels = models.listModels() as Array<{ id: string; name: string; enabled: boolean }>;
      const enabledModel = allModels.find(m => m.enabled);
      if (enabledModel) {
        targetModelName = enabledModel.name;
      }
    }

    if (!targetModelName) {
      res.status(400).json({ error: "No model available. Please configure a model in Model Registry." });
      return;
    }

    // Get model by name
    const models = await import("./services/modelService");
    const model = models.getModelByName(targetModelName) as { id: string; name: string; provider: string; modelId: string; baseUrl?: string } | null;

    if (!model) {
      res.status(404).json({ error: `Model '${targetModelName}' not found` });
      return;
    }

    // Build messages
    const messages = [
      { role: "system", content: systemPrompt || defaultSystemPrompt },
      { role: "user", content: `请分析以下测控实验数据：\n\n${analysis_output}` },
    ];

    // Get API key for the provider
    const apiKey = getAPIKeyForProvider(model.provider);

    // Build request based on provider
    let endpoint = "";
    let requestBody: Record<string, unknown> = {};

    if (model.provider === "openai") {
      endpoint = (model.baseUrl || "https://api.openai.com/v1") + "/chat/completions";
      requestBody = {
        model: model.modelId,
        messages,
        temperature: 0.3,
      };
    } else if (model.provider === "deepseek") {
      endpoint = (model.baseUrl || "https://api.deepseek.com/v1") + "/chat/completions";
      requestBody = {
        model: model.modelId,
        messages,
        temperature: 0.3,
      };
    } else if (model.provider === "minimax") {
      endpoint = "https://api.minimax.chat/v1/text/chatcompletion_v2";
      requestBody = {
        model: model.modelId,
        messages,
        temperature: 0.3,
      };
    } else {
      res.status(400).json({ error: `Unsupported provider: ${model.provider}` });
      return;
    }

    // Make the API request
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiKey}`,
      },
      body: JSON.stringify(requestBody),
    });

    if (!response.ok) {
      const errorText = await response.text();
      res.status(502).json({ error: `API error: ${response.status} - ${errorText}` });
      return;
    }

    const result = await response.json() as { choices?: Array<{ message?: { content?: string } }>; content?: string };

    // Extract response content
    let content = "";
    if (result.choices && result.choices[0]?.message?.content) {
      content = result.choices[0].message.content;
    } else if (typeof result.content === "string") {
      content = result.content;
    }

    res.json({
      success: true,
      content,
      model: model.name,
      modelId: model.modelId,
    });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// Helper: Get API key for provider
function getAPIKeyForProvider(provider: string): string {
  switch (provider) {
    case 'openai':
      return process.env.OPENAI_API_KEY || '';
    case 'minimax':
      return process.env.MINIMAX_API_KEY || '';
    case 'anthropic':
      return process.env.ANTHROPIC_API_KEY || '';
    case 'deepseek':
      return process.env.DEEPSEEK_API_KEY || '';
    default:
      return process.env.OPENAI_API_KEY || '';
  }
}

// ── Flask-style endpoints (direct, no Flask server needed) ──────────────────

/** GET /experiments — list known sq.* experiment functions */
app.get("/experiments", (_req, res) => {
  res.json({ experiments: KNOWN_EXPERIMENTS });
});

/** GET /api/experiments/configs — get all experiment configurations */
app.get("/api/experiments/configs", (_req, res) => {
  try {
    const configs = loadExperimentConfigs();
    res.json({ success: true, configs });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /api/experiments/configs/:type — get experiment config by type */
app.get("/api/experiments/configs/:type", (req, res) => {
  try {
    const config = getExperimentConfig(req.params.type);
    if (!config) {
      res.status(404).json({ error: `Experiment type '${req.params.type}' not found` });
      return;
    }
    res.json({ success: true, type: req.params.type, config });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** PUT /api/experiments/configs/:type — update experiment config */
app.put("/api/experiments/configs/:type", (req, res) => {
  try {
    const config = req.body as Partial<ExperimentConfig>;
    const success = updateExperimentConfig(req.params.type, config);
    if (!success) {
      res.status(404).json({ error: `Experiment type '${req.params.type}' not found` });
      return;
    }
    res.json({ success: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Rules Config ───────────────────────────────────────────────────────────────

const RULES_CONFIG_FILE = path.join(__dirname, "..", "config", "rules.json");

function loadRulesConfig(): { rules: any[] } {
  const defaultRules = { rules: [] };
  try {
    if (fs.existsSync(RULES_CONFIG_FILE)) {
      const content = fs.readFileSync(RULES_CONFIG_FILE, 'utf-8');
      return JSON.parse(content);
    }
  } catch {
    // ignore parse errors
  }
  return defaultRules;
}

function saveRulesConfig(rules: any[]): void {
  const configDir = path.dirname(RULES_CONFIG_FILE);
  if (!fs.existsSync(configDir)) {
    fs.mkdirSync(configDir, { recursive: true });
  }
  const config = { rules };
  fs.writeFileSync(RULES_CONFIG_FILE, JSON.stringify(config, null, 2), 'utf-8');
}

/** GET /api/rules — get all rules */
app.get("/api/rules", (_req, res) => {
  try {
    const config = loadRulesConfig();
    res.json({ success: true, rules: config.rules });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** PUT /api/rules — update rules */
app.put("/api/rules", (req, res) => {
  try {
    const rules = req.body.rules as any[];
    if (!Array.isArray(rules)) {
      res.status(400).json({ error: "rules must be an array" });
      return;
    }
    saveRulesConfig(rules);
    res.json({ success: true, rules });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /sessions/config — get current session config */
app.get("/sessions/config", (_req, res) => {
  const config = loadSessionConfig();
  const fullPath = ['', config.user, ...config.path];
  res.json({ user: config.user, path: config.path, fullPath });
});

/** GET /sessions/status — debug: check session status in job_runner */
app.get("/sessions/status", async (_req, res) => {
  try {
    const result = await sendFlaskRequest("debug_data", {}) as Record<string, unknown>;
    res.json({
      debugData: result,
      configSession: loadSessionConfig(),
    });
  } catch (err: any) {
    res.status(502).json({ error: err.message });
  }
});

/** GET /sessions/test-load — test loading the latest dataset */
app.get("/sessions/test-load", async (_req, res) => {
  try {
    const result = await sendFlaskRequest("test_load_dataset", {}) as Record<string, unknown>;
    res.json(result);
  } catch (err: any) {
    res.status(502).json({ error: err.message });
  }
});

/** GET /sessions/diagnostic — full diagnostic info */
app.get("/sessions/diagnostic", async (_req, res) => {
  try {
    const debugData = await sendFlaskRequest("debug_data", {}) as Record<string, unknown>;
    const testLoad = await sendFlaskRequest("test_load_dataset", {}) as Record<string, unknown>;
    const config = loadSessionConfig();
    res.json({
      config,
      jobRunnerSession: debugData?.current_session_path,
      datasetCount: debugData?.dataset_count,
      testLoad,
    });
  } catch (err: any) {
    res.status(502).json({ error: err.message });
  }
});

/** POST /sessions/plot — plot the latest dataset with custom command */
app.post("/sessions/plot", async (req, res) => {
  try {
    const { command } = req.body as { command?: string };
    const result = await sendFlaskRequest("plot_dataset", { command: command || "" }) as Record<string, unknown>;
    res.json(result);
  } catch (err: any) {
    res.status(502).json({ error: err.message });
  }
});

/** POST /sessions/config — save session config (user and path segments) */
app.post("/sessions/config", (req, res) => {
  const { user, path } = req.body as { user?: string; path?: string[] };
  if (!user || !path) {
    res.status(400).json({ error: "user and path required" });
    return;
  }
  saveSessionConfig(user, path);
  res.json({ success: true, user, path });
});

/** GET /sessions — list DataVault sessions */
app.get("/sessions", async (_req, res) => {
  try {
    const data = await sendFlaskRequest("sessions", {}) as { current: unknown; sessions: unknown };
    res.json(data);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** POST /sessions/switch — switch DataVault session */
app.post("/sessions/switch", async (req, res) => {
  try {
    const body = req.body as { user?: string; path?: string[] };
    let user: string | undefined;
    let pathSegments: string[] | undefined;

    // Support both formats:
    // 1. { user: "LQHL", path: ["test", "20260324"] }
    // 2. { path: ["", "LQHL", "test", "20260324"] }
    if (body.user && body.path) {
      user = body.user;
      pathSegments = body.path;
    } else if (body.path && body.path.length >= 2) {
      // Extract from full path: ["", "LQHL", "test", "20260324"]
      user = body.path[1];
      pathSegments = body.path.slice(2);
    }

    // Save to config file
    if (user && pathSegments) {
      saveSessionConfig(user, pathSegments);
    }

    // Call job_runner.py to update _data object
    const sessionPath = user && pathSegments ? ['', user, ...pathSegments] : [];
    const switchResult = await sendFlaskRequest("switch_session", { path: sessionPath }) as { success: boolean; path: string[]; qubits?: Array<{ name: string; f10?: number; fread?: number }> };

    res.json(switchResult);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** GET /qubits — list qubits in current session */
app.get("/qubits", async (_req, res) => {
  try {
    const data = await sendFlaskRequest("list_qubits", {}) as { qubits: Array<{ name: string; f10?: number; fread?: number; bias_z?: number; error?: string }>; sessionPath: string[] };
    res.json(data);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** GET /sessions/tree — get full DataVault directory tree */
app.get("/sessions/tree", async (_req, res) => {
  try {
    const data = await sendFlaskRequest("session_tree", {}) as { tree: Array<{ name: string; path: string[]; hasChildren: boolean }> };
    res.json(data);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** GET /qubits/:name/params — get qubit parameters */
app.get("/qubits/:name/params", async (req, res) => {
  try {
    const name = req.params.name;
    const data = await sendFlaskRequest("get_qubit_params", { name }) as {
      name: string;
      params: Record<string, number | null>;
      error?: string;
    };
    if (data.error) {
      res.status(404).json({ error: data.error });
    } else {
      res.json(data);
    }
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** PUT /qubits/:name/params — update qubit parameters */
app.put("/qubits/:name/params", async (req, res) => {
  try {
    const name = req.params.name;
    const params = req.body as Record<string, number | null>;
    const data = await sendFlaskRequest("set_qubit_params", { name, params }) as {
      success: boolean;
      name: string;
      updated: string[];
      errors: string[] | null;
      error?: string;
    };
    if (data.error) {
      res.status(404).json({ error: data.error });
    } else {
      res.json(data);
    }
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** GET /datasets — list DataVault datasets in a path */
app.get("/datasets", async (req, res) => {
  try {
    const path = req.query.path as string | undefined;
    const data = await sendFlaskRequest("datasets", { path: path || getDefaultSessionPath() }) as { path: string; groups: string[]; datasets: unknown[] };
    res.json(data);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** GET /datasets/plot?name=...&path=... — generate historical dataset plot PNG */
app.get("/datasets/plot", async (req, res) => {
  const name = req.query.name as string;
  const datasetPath = req.query.path as string || getDefaultSessionPath();
  if (!name) { res.status(400).json({ error: "name query param required" }); return; }
  try {
    const result = await sendFlaskRequest("plot", { name, path: datasetPath }) as { plotPath?: string; name?: string; error?: string };
    if (result.error || !result.plotPath) {
      res.status(500).json({ error: result.error || "Plot generation failed" });
      return;
    }
    if (!fs.existsSync(result.plotPath)) { res.status(404).json({ error: "Plot file not found" }); return; }
    res.setHeader("Content-Type", "image/png");
    res.sendFile(result.plotPath);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** GET /hardware/status — get detailed hardware connection status */
app.get("/hardware/status", async (_req, res) => {
  try {
    const data = await sendFlaskRequest("hardware_status", {}) as {
      overall: string; timestamp: string;
      services: Record<string, unknown>; devices: Record<string, unknown>;
      issues: string[];
    };
    res.json(data);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** GET /hardware/quick — get quick status summary for header */
app.get("/hardware/quick", async (_req, res) => {
  try {
    const data = await sendFlaskRequest("quick_status", {}) as {
      labrad: string; ray: string; datavault: string; message: string;
    };
    res.json(data);
  } catch (err: any) { res.status(502).json({ error: err.message }); }
});

/** POST /server/start — start all measurement services */
app.post("/server/start", async (_req, res) => {
  try {
    const { spawn } = await import("child_process");
    const pyBin = process.env.PYTHON_BIN || "python";
    const scriptPath = path.join(process.cwd(), "scripts", "start_services.py");

    // Run the start_services.py script
    const result = await new Promise<{ stdout: string; stderr: string; code: number }>((resolve, reject) => {
      const proc = spawn(pyBin, [scriptPath], {
        cwd: path.join(process.cwd(), "scripts"),
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });

      let stdout = "", stderr = "";
      proc.stdout?.on("data", (d) => { stdout += d.toString(); });
      proc.stderr?.on("data", (d) => { stderr += d.toString(); });
      proc.on("close", (code) => resolve({ stdout, stderr, code: code || 0 }));
      proc.on("error", reject);
    });

    res.json({
      success: result.code === 0,
      stdout: result.stdout,
      stderr: result.stderr,
    });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

/** GET /server/status — get server control status */
app.get("/server/status", async (_req, res) => {
  try {
    const { spawn } = await import("child_process");
    const pyBin = process.env.PYTHON_BIN || "python";
    const scriptPath = path.join(process.cwd(), "scripts", "check_services.py");

    const result = await new Promise<{ stdout: string; stderr: string; code: number }>((resolve, reject) => {
      const proc = spawn(pyBin, [scriptPath], {
        cwd: path.join(process.cwd(), "scripts"),
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      });

      let stdout = "", stderr = "";
      proc.stdout?.on("data", (d) => { stdout += d.toString(); });
      proc.stderr?.on("data", (d) => { stderr += d.toString(); });
      proc.on("close", (code) => resolve({ stdout, stderr, code: code || 0 }));
      proc.on("error", reject);
    });

    let status;
    try {
      status = JSON.parse(result.stdout);
    } catch {
      status = { raw: result.stdout, error: "Failed to parse JSON" };
    }
    res.json(status);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// ── Plot serving ──────────────────────────────────────────────────────────────

/** Serve a plot PNG for a completed job */
app.get("/plot/:jobId", (req, res) => {
  const { jobId } = req.params;
  const job = jobResults.get(jobId);
  if (!job || !job.plotPath) { res.status(404).json({ error: "Plot not found" }); return; }
  const plotFile = path.basename(job.plotPath);
  const publicPath = path.join(process.cwd(), "..", "qmclaw-web", "public", "plots", plotFile);
  if (!fs.existsSync(publicPath)) { res.status(404).json({ error: "Plot file not on disk" }); return; }
  res.setHeader("Content-Type", "image/png");
  res.sendFile(publicPath);
});

// ── Start ─────────────────────────────────────────────────────────────────

createServer(app).listen(PORT, () => {
  console.log(`[qmclaw] Listening on http://localhost:${PORT}`);
  console.log(`[qmclaw] Integrated: experiments, sessions, datasets, plots (no Flask needed)`);
});