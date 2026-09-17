/**
 * qmclaw-server - Main Entry Point
 *
 * Architecture:
 *   Browser → Express (:3002) → Microservices (LLM, Quantum, Analysis, Agent, Image, Workflow, TaskQueue)
 *
 * Modes:
 *   - Microservices mode (default): /api/* routes proxy to microservices
 *   - Legacy mode (QMCLAW_USE_SERVICES=false): uses job_runner.py subprocess
 */

import { createServiceProxy } from "./services/serviceProxy";

// ── Mode Configuration ────────────────────────────────────────────────────────
// 微服务模式：Express 只做网关，/api/* 路由代理到微服务
// Legacy 模式：Express 启动 job_runner.py 子进程，处理 /job/* 等路由
// 配置位置: config/services.json -> mode.use_microservices

import * as fs from "fs";
import path from "path";

// Load service configuration for mode setting
const servicesConfigPath = path.join(__dirname, "..", "config", "services.json");
let servicesConfig: { mode?: { use_microservices?: boolean } } = {};
try {
  servicesConfig = JSON.parse(fs.readFileSync(servicesConfigPath, "utf-8"));
} catch (e) {
  console.warn("[Config] Failed to load services.json:", e);
}

// Default to true, can be overridden by env var for quick testing
const USE_MICROSERVICES = process.env.QMCLAW_USE_SERVICES === "false"
  ? false
  : (servicesConfig.mode?.use_microservices ?? true);
console.log("[Server] Mode:", USE_MICROSERVICES ? "Microservices" : "Legacy (job_runner.py)");
console.log("[Server] Mode config source:", process.env.QMCLAW_USE_SERVICES ? "ENV" : "services.json");

// Load environment variables from .env file (override existing to ensure .env takes precedence)
import dotenv from "dotenv";
const envPath = path.join(__dirname, "..", ".env");
console.log("[Debug] Loading .env from:", envPath);
dotenv.config({
  override: true,
  path: envPath,
});

// Debug: log loaded env vars
console.log("[Debug] MINIMAX_API_KEY loaded:", process.env.MINIMAX_API_KEY ? "YES (length=" + process.env.MINIMAX_API_KEY.length + ")" : "NO");
console.log("[Debug] CWD:", process.cwd());
console.log("[Debug] __dirname:", __dirname);

import express from "express";
import { createServer } from "http";
import cors from "cors";
import { generateJobId } from "./queue/job-types";
import { loadExperimentConfigs, saveExperimentConfigs, getExperimentConfig, updateExperimentConfig, ExperimentConfig } from "./services/experimentConfigService";

// ── Legacy Mode Only Imports ───────────────────────────────────────────────────
let spawn: typeof import("child_process").spawn | null = null;
let pyProc: ReturnType<ReturnType<typeof import("child_process").spawn>> | null = null;
let pyProcReady = false;
let sseBuffer = "";
let bufferedSseCid = "";

if (!USE_MICROSERVICES) {
  spawn = require("child_process").spawn;
}

// ══════════════════════════════════════════════════════════════════════════════
// Logging Filter Configuration
// ══════════════════════════════════════════════════════════════════════════════
// 日志过滤器：控制哪些日志信息显示在终端
// true = 显示该日志，false = 隐藏该日志
// 格式：line.includes("[Tag]") || - 方便查看和修改
//
// TypeScript 端使用 [标签] 格式
// Python 端使用 INIT: / WORKFLOW_NODE: / QMCLAW_PLOT: 等格式

/**
 * 日志过滤器 - 调试模式：打印所有日志
 *
 * 设置 ENABLE_LOG_FILTER = true 启用过滤（生产模式）
 * 设置 ENABLE_LOG_FILTER = false 打印所有日志（调试模式）
 */
const ENABLE_LOG_FILTER = false;  // 调试模式：false = 打印所有日志

function shouldPrintLog(line: string): boolean {
  // 调试模式：打印所有日志
  if (!ENABLE_LOG_FILTER) {
    return true;
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // TypeScript 端
  // ══════════════════════════════════════════════════════════════════════════════
  if (
    line.includes("[Server]") ||  // ✅ Server - 服务启动、端口监听、路由注册
    line.includes("[Worker]") ||  // ✅ Worker - Python子进程管理
    line.includes("[Request]") ||  // ✅ Request - HTTP请求处理
    line.includes("[SSE]") ||  // ✅ SSE - Server-Sent Events
    line.includes("[Workflow]") ||  // ✅ Workflow - 工作流提交、执行
    line.includes("[Agent]") ||  // ✅ Agent - 量子智能体对话
    line.includes("[Hermes]") ||  // ✅ Hermes - Hermes Agent
    line.includes("[System]") ||  // ✅ System - LabRAD/Ray/硬件状态、WebSocket
    line.includes("[QuantumAgent]") ||  // ✅ QuantumAgent - Quantum Agent

    // ── 以下默认隐藏 ──────────────────────────────────────────────────────────
    // line.includes("[Database]") ||  // ❌ Database - 数据持久化
    // line.includes("[Backend]") ||  // ❌ Backend - 后端通信
    // line.includes("[Debug]") ||  // ❌ Debug - 开发调试
    // line.includes("[SSE Frontend]") ||  // ❌ SSE Frontend - 前端SSE事件
    // line.includes("[BACKEND_WORKER]") ||  // ❌ BACKEND_WORKER - 后端工作线程
    // line.includes("[MiniMax Debug]") ||  // ❌ MiniMax Debug - MiniMax调试
    // line.includes("[LQCS Backend]") ||  // ❌ LQCS Backend - LQCS后端初始化
    // line.includes("[backends init_backend]") ||  // ❌ backends init_backend - backends初始化
    // line.includes("INIT:") ||  // ❌ INIT: - 初始化日志
    false) {
    return true;
  }

  // ══════════════════════════════════════════════════════════════════════════════
  // Python 端
  // ══════════════════════════════════════════════════════════════════════════════
  if (
    line.includes("INIT: Backends adapter ready") ||  // ✅ INIT: Backends adapter ready - 后端就绪
    line.includes("INIT: LQCS Backend ready") ||  // ✅ INIT: LQCS Backend ready - LQCS后端就绪
    line.includes("READY") ||  // ✅ READY - 就绪信号
    line.includes("WORKFLOW_NODE:") ||  // ✅ WORKFLOW_NODE: - 工作流节点
    line.includes("WORKFLOW_EXEC:") ||  // ✅ WORKFLOW_EXEC: - 工作流执行
    line.includes("WORKFLOW_ERROR:") ||  // ✅ WORKFLOW_ERROR: - 工作流错误
    line.includes("WORKFLOW_ERR:") ||  // ✅ WORKFLOW_ERR: - 工作流错误
    line.includes("EXEC ERROR:") ||  // ✅ EXEC ERROR: - 执行错误
    line.includes("EXEC DEBUG:") ||  // ✅ EXEC DEBUG: - 执行调试
    line.includes("QMCLAW_PLOT:") ||  // ✅ QMCLAW_PLOT: - 绘图成功
    line.includes("QMCLAW_PLOT_ERROR:") ||  // ✅ QMCLAW_PLOT_ERROR: - 绘图错误
    line.includes("QMCLAW_ANALYSIS:") ||  // ✅ QMCLAW_ANALYSIS: - 分析结果
    line.includes("QMCLAW_ANALYSIS_ERROR:") ||  // ✅ QMCLAW_ANALYSIS_ERROR: - 分析错误
    line.includes("LLM:") ||  // ✅ LLM: - LLM调用
    line.includes("LLM_ERR:") ||  // ✅ LLM_ERR: - LLM错误
    line.includes("RELOAD_QUBITS:") ||  // ✅ RELOAD_QUBITS: - 量子比特重载
    line.includes("QmClaw Server Controller") ||  // ✅ QmClaw Server Controller - 服务控制
    line.includes("[EVENT]") ||  // ✅ [EVENT] - backend请求事件
    line.includes("[EVENT_LOOP]") ||  // ✅ [EVENT_LOOP] - 事件循环调试
    line.includes("[backends") ||  // ✅ [backends - backends模块日志
    line.includes("[LQCS Backend]") ||  // ✅ [LQCS Backend] - LQCS后端日志
    line.includes("TIMEOUT") ||  // ✅ TIMEOUT - 超时信息
    line.includes("SINGLE_NODE:") ||  // ✅ SINGLE_NODE: - 单节点执行
    line.includes(">>> FORCE LOG") ||  // ✅ FORCE LOG - 强制日志
    line.includes("INIT: Starting backend initialization") ||  // ✅ INIT: backend init开始

    // ── 以下默认隐藏 ──────────────────────────────────────────────────────────
    // line.includes("INIT:") ||  // ❌ INIT: - 初始化日志
    // line.includes("RAY:") ||  // ❌ RAY: - Ray分布式计算
    // line.includes("BACKENDS:") ||  // ❌ BACKENDS: - 后端适配器
    // line.includes("BACKEND:") ||  // ❌ BACKEND: - 后端通信
    // line.includes("EXEC:") ||  // ❌ EXEC: - 执行信息
    // line.includes("WORKFLOW_DEBUG:") ||  // ❌ WORKFLOW_DEBUG: - 工作流调试
    // line.includes("WORKFLOW_ANALYZE:") ||  // ❌ WORKFLOW_ANALYZE: - 分析请求
    // line.includes("WORKFLOW_ANALYSIS:") ||  // ❌ WORKFLOW_ANALYSIS: - 分析详情
    // line.includes("WORKFLOW_ANALYSIS_ERROR:") ||  // ❌ WORKFLOW_ANALYSIS_ERROR: - 分析错误
    // line.includes("WORKFLOW_PLOT_ERROR:") ||  // ❌ WORKFLOW_PLOT_ERROR: - 绘图错误
    // line.includes("[Agent LLM]") ||  // ❌ [Agent LLM] - Agent LLM详情
    // line.includes("[ReAct]") ||  // ❌ [ReAct] - ReAct推理
    // line.includes("QMCLAW_PLOT_MODIFIED:") ||  // ❌ QMCLAW_PLOT_MODIFIED: - 绘图修改
    // line.includes("QMCLAW_PLOT_FALLBACK:") ||  // ❌ QMCLAW_PLOT_FALLBACK: - 绘图回退
    // line.includes("QMCLAW_MODIFIED_PLOT:") ||  // ❌ QMCLAW_MODIFIED_PLOT: - 修改后绘图
    // line.includes("MemoryStore:") ||  // ❌ MemoryStore: - 内存系统
    false) {
    return true;
  }

  return false;
}

const PORT = process.env.PORT || 8080;
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

// ── JSONL Line Processor ────────────────────────────────────────────────────────

function processJsonlLine(line: string): void {
  // Skip non-JSON lines (e.g., LabRAD/lqms output like "TORCH_DEVCIE=cpu", "Registry Root is ...")
  if (!line.trim().startsWith('{')) {
    return;
  }

  try {
    const obj = JSON.parse(line);

    // Ready signal from backend - clear pending requests
    if (obj.ready === true) {
      console.log("[Worker] Backend ready signal received, clearing pending requests");
      // Dismiss all pending requests (timeouts will clean themselves up, just reject the promises)
      backendPendingRequests.forEach((handlers, cid) => {
        handlers.reject(new Error("Backend restarted, request cancelled"));
        console.log(`[Worker] Cancelled pending request: ${cid}`);
      });
      backendPendingRequests.clear();
      console.log(`[Worker] Cleared all pending requests`);
      return;
    }

    // Workflow progress
    if (obj.type === "workflow_progress") {
      handleWorkflowProgress(obj as { workflowId: string; nodeId: string; status: string });
      return;
    }
    // Workflow result
    if (obj.workflowId) {
      handleWorkflowResult(obj as WorkflowResultMsg);
      return;
    }
    // backend result
    if (obj.cid && obj.action) {
      if (obj.error) {
        console.log(`[Backend] Error cid=${obj.cid} action=${obj.action}: ${obj.error}`);
      }
      handlebackendResult(obj as { cid: string; action: string; data?: unknown; error?: string });
      return;
    }
    // Legacy job result
    handleSubprocessResult(obj as { status: string; stdout: string; stderr: string; error: string });
  } catch { /* ignore parse errors during init */ }
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
  qubit?: string;
  experiment?: string;
};

const jobResults = new Map<string, JobEntry>();

// Track all job IDs in order (for job list display)
const jobHistory: string[] = [];

// ── backend request correlation (type: "backend" messages → HTTP responses) ───────
type backendPendingEntry = {
  resolve: (data: unknown) => void;
  reject: (err: Error) => void;
};
const backendPendingRequests = new Map<string, backendPendingEntry>();

// ── Microservice Proxy Function ─────────────────────────────────────────────────

type ServiceName = 'quantum' | 'analysis' | 'agent' | 'hermes' | 'llm' | 'image' | 'workflow';

const SERVICE_PORTS: Record<ServiceName, number> = {
  quantum: 3003,
  analysis: 3004,
  agent: 3005,
  hermes: 3005,   // Hermes uses agent service port
  llm: 3006,
  image: 3007,
  workflow: 3008,
};

interface ProxyResult {
  ok: boolean;
  data?: unknown;
  error?: string;
}

/**
 * Proxy an HTTP request to a microservice.
 * @param service - The target microservice name
 * @param path - The API path (e.g., '/qubits', '/execute')
 * @param method - HTTP method
 * @param body - Request body (for POST/PUT requests)
 * @param isFormData - Whether body is FormData
 */
async function proxyToService(
  service: ServiceName,
  path: string,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE',
  body?: unknown,
  isFormData = false,
): Promise<ProxyResult> {
  const port = SERVICE_PORTS[service] || 3003;
  const url = `http://localhost:${port}${path}`;

  console.log(`[Proxy] ${method} ${service} -> ${url}`);

  try {
    const headers: Record<string, string> = {};
    if (!isFormData) {
      headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(url, {
      method,
      headers,
      body: isFormData ? (body as FormData) : (body ? JSON.stringify(body) : undefined),
      signal: AbortSignal.timeout(120_000), // 2 minute timeout for long operations
    });

    const contentType = response.headers.get('content-type') || '';

    // Handle streaming responses (SSE)
    if (contentType.includes('text/event-stream')) {
      const text = await response.text();
      return { ok: true, data: text };
    }

    // Parse JSON response
    if (contentType.includes('application/json')) {
      const data = await response.json();
      return { ok: response.ok, data };
    }

    // Handle other responses
    const text = await response.text();
    return {
      ok: response.ok,
      data: text,
      error: response.ok ? undefined : `HTTP ${response.status}: ${text.slice(0, 200)}`
    };
  } catch (err: any) {
    console.error(`[Proxy] Error calling ${service}:`, err.message);
    return {
      ok: false,
      error: `Failed to connect to ${service} service: ${err.message}`,
    };
  }
}

/** Send a backend-style message to the subprocess and resolve via correlation ID */
async function sendbackendRequest(action: string, data: Record<string, unknown>, timeoutMs = 30_000): Promise<unknown> {
  // 微服务模式下不支持 legacy backend 调用
  if (USE_MICROSERVICES) {
    throw new Error("Legacy backend not available in microservices mode");
  }

  await ensureSubprocess();
  if (!pyProc || !pyProc.stdin) throw new Error("Worker not running");

  const cid = "f" + Date.now() + Math.random().toString(36).slice(2, 8);
  const msg = JSON.stringify({ type: "backend", cid, action, data }) + "\n";
  console.log(`[Backend] ${action} (cid=${cid}), sending...`);

  return new Promise((resolve, reject) => {
    backendPendingRequests.set(cid, { resolve, reject });
    console.log(`[Backend] registered cid=${cid}, pending count=${backendPendingRequests.size}, pending=${JSON.stringify(Array.from(backendPendingRequests.keys()))}`);
    try {
      if (!pyProc || !pyProc.stdin) throw new Error("Worker stdin not available");
      const ok = pyProc.stdin.write(msg);
      console.log(`[Backend] stdin.write ok=${ok} for cid=${cid}`);
    } catch (err: any) {
      backendPendingRequests.delete(cid);
      console.log(`[Backend] stdin.write failed for cid=${cid}: ${err.message}`);
      reject(err);
    }
    // Timeout
    setTimeout(() => {
      if (backendPendingRequests.has(cid)) {
        console.log(`[Backend] timeout for cid=${cid}`);
        backendPendingRequests.delete(cid);
        reject(new Error("backend request timeout"));
      }
    }, timeoutMs);
  });
}

// ── Streaming support ───────────────────────────────────────────────────────────

type StreamCallback = (data: string) => void;
const activeStreams = new Map<string, StreamCallback>();

/**
 * Send a backend-style message for streaming.
 * SSE events from Python will be forwarded to the callback.
 * Returns a promise that resolves when streaming completes.
 */
async function sendbackendRequestStreaming(
  action: string,
  data: Record<string, unknown>,
  onEvent: StreamCallback,
  timeoutMs = 300_000
): Promise<unknown> {
  await ensureSubprocess();
  if (!pyProc || !pyProc.stdin) throw new Error("Worker not running");

  const cid = "stream_" + Date.now() + Math.random().toString(36).slice(2, 8);
  const msg = JSON.stringify({ type: "backend", cid, action, data }) + "\n";
  console.log(`[Backend] Stream cid=${cid} action=${action}`);

  // Register the stream callback
  activeStreams.set(cid, onEvent);

  return new Promise((resolve, reject) => {
    backendPendingRequests.set(cid, { resolve, reject });
    try {
      if (!pyProc || !pyProc.stdin) throw new Error("Worker stdin not available");
      const ok = pyProc.stdin.write(msg);
      console.log(`[Backend] Stream stdin.write ok=${ok}`);
    } catch (err: any) {
      activeStreams.delete(cid);
      backendPendingRequests.delete(cid);
      reject(err);
    }
    // Timeout
    setTimeout(() => {
      if (activeStreams.has(cid)) {
        activeStreams.delete(cid);
        backendPendingRequests.delete(cid);
        reject(new Error("backend streaming timeout"));
      }
    }, timeoutMs);
  });
}

// ── Express setup ────────────────────────────────────────────────────────────

const app = express();
// CORS配置：允许所有开发服务器来源
app.use(cors({
  origin: true,  // 允许所有来源（包括 localhost:3001, 127.0.0.1:8081 等）
  credentials: true,
}));
app.use(express.json());

// ── Service Proxy ───────────────────────────────────────────────────────────
// 代理请求到各微服务 (挂载到 /api 前缀)
app.use('/api', createServiceProxy());

// ── Legacy Mode Guard ───────────────────────────────────────────────────────
// 在微服务模式下，某些路由不可用
function requireLegacyMode(res: import("express").Response): boolean {
  if (USE_MICROSERVICES) {
    res.status(503).json({
      error: "Unavailable in microservices mode",
      message: "This endpoint requires legacy mode. Set QMCLAW_USE_SERVICES=false to enable.",
      mode: "microservices",
    });
    return false;
  }
  return true;
}

function requireSubprocess(res: import("express").Response): boolean {
  if (USE_MICROSERVICES) {
    return requireLegacyMode(res);
  }
  if (!pyProc || !pyProcReady) {
    res.status(503).json({
      error: "Subprocess not ready",
      message: "Python worker is initializing. Please try again later.",
      subprocess: pyProc ? "initializing" : "not_started",
    });
    return false;
  }
  return true;
}

// ── Persistent subprocess (Legacy Mode Only) ───────────────────────────────

// One long-running Python subprocess; all jobs run through it.
// Avoids re-initializing LabRAD connection (20+ seconds) per job.
// Note: pyProc, pyProcReady, sseBuffer, bufferedSseCid are declared at the top of the file

/** Spawn the persistent Python subprocess and set up I/O handlers */
function ensureSubprocess(): Promise<void> {
  // 微服务模式下不启动 subprocess
  if (USE_MICROSERVICES) {
    return Promise.reject(new Error("Subprocess not available in microservices mode"));
  }

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
    console.log("[Debug] Spawning Python with MINIMAX_API_KEY:", process.env.MINIMAX_API_KEY ? "SET (len=" + process.env.MINIMAX_API_KEY.length + ")" : "NOT SET");

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
          console.log("[Worker] Python worker ready");
          resolve();
        }
        // 过滤Python后端的日志输出
        if (shouldPrintLog(line)) {
          console.log(line);
        } else {
          // Debug: log when a line is filtered
          if (line.includes("[backends") || line.includes(">>> FORCE")) {
            console.log("[Debug-Filter] FILTERED:", line.substring(0, 80));
          }
        }
      }
    });

    console.log(`[Debug] Log filter initialized: ENABLE_LOG_FILTER=${ENABLE_LOG_FILTER}`);

    pyProc.stdout?.on("data", (data: Buffer) => {
      sseBuffer += data.toString();

      // Process complete lines (ending with \n)
      while (true) {
        const nlIdx = sseBuffer.indexOf('\n');
        if (nlIdx === -1) break; // no complete line

        const line = sseBuffer.substring(0, nlIdx);
        sseBuffer = sseBuffer.substring(nlIdx + 1);

        if (!line.trim()) continue;

        // Check for SSE event prefix: "SSE: <cid> |" - extract CID and SSE body
        if (line.startsWith('SSE: ')) {
          const pipeIdx = line.indexOf('|');
          if (pipeIdx > 0) {
            const cid = line.substring(6, pipeIdx).trim();
            const sseBody = line.substring(pipeIdx + 1).trimStart(); // Everything after "|"
            const callback = activeStreams.get(cid);
            if (callback) {
              // Build complete SSE: sseBody + remaining lines until \n\n
              let fullSSE = sseBody + '\n';
              // Check if we already have \n\n in what we received
              const bodyEndIdx = fullSSE.indexOf('\n\n');
              if (bodyEndIdx !== -1) {
                // Complete SSE in one go
                const body = fullSSE.substring(0, bodyEndIdx + 2);
                console.log(`[SSE Forward] cid=${cid} body=${body.substring(0, 80)}...`);
                callback(body);
              } else {
                // Need to wait for more data - buffer it
                sseBuffer = fullSSE;
                bufferedSseCid = cid; // Store CID for when we complete buffering
                // Check if the line we just received has the data and \n\n
                if (fullSSE.includes('\n\n')) {
                  const endIdx = sseBuffer.indexOf('\n\n');
                  const body = sseBuffer.substring(0, endIdx + 2);
                  sseBuffer = '';
                  bufferedSseCid = '';
                  console.log(`[SSE Forward] cid=${cid} body=${body.substring(0, 80)}...`);
                  callback(body);
                }
              }
            }
          }
          continue;
        }

        // If we have buffered SSE data, append this line
        if (sseBuffer) {
          sseBuffer += line + '\n';
          const endIdx = sseBuffer.indexOf('\n\n');
          if (endIdx !== -1) {
            const body = sseBuffer.substring(0, endIdx + 2);
            sseBuffer = '';
            // CID was stored separately when buffering started
            const cid = bufferedSseCid;
            bufferedSseCid = '';
            const callback = activeStreams.get(cid);
            if (callback) {
              console.log(`[SSE Forward] cid=${cid} buffered body=${body.substring(0, 80)}...`);
              callback(body);
            }
          }
          continue;
        }

        // Regular JSONL line
        processJsonlLine(line);
      }
    });

    pyProc.on("error", (err) => {
      console.log(`[Worker] Python subprocess error:`, err.message);
      pyProc = null;
      pyProcReady = false;
    });

    pyProc.on("close", (code) => {
      console.log(`[Worker] Python subprocess exited with code`, code);
      pyProc = null;
      pyProcReady = false;
    });

    // Timeout if READY not received within 90s (LabRAD + Ray init)
    setTimeout(() => {
      if (!pyProcReady) {
        console.log(`[Worker] Python worker init timeout - no READY received`);
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
  duration?: number; input?: string; conversation?: any;
  recommendations?: any[]; symptom?: string; reasoning?: string; matchedRules?: string[];
};

type WorkflowResultMsg = {
  status: string; workflowId: string; workflowName: string;
  stdout: string; stderr: string; error: string;
  nodeResults: Record<string, { result: NodeResult }>;
};

function handleWorkflowProgress(progress: { workflowId: string; nodeId: string; status: string }): void {
  console.log(`[Workflow] progress: ${progress.workflowId} / ${progress.nodeId} = ${progress.status}`);
  const wf = workflowResults.get(progress.workflowId);
  if (!wf) return;
  // Initialize node if not present
  if (!wf.nodes[progress.nodeId]) {
    wf.nodes[progress.nodeId] = { status: "pending", type: "unknown", stdout: "", error: "" };
  }
  wf.nodes[progress.nodeId].status = progress.status;
}

function handleWorkflowResult(result: WorkflowResultMsg): void {
  console.log(`[Workflow] result: ${result.workflowId} = ${result.status}`);
  const wf = workflowResults.get(result.workflowId);
  if (!wf) { console.log(`[Workflow] unknown workflow: ${result.workflowId}`); return; }

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
    console.log(`[Workflow] run persisted: run_${result.workflowId}_${wf.submittedAt}`);
  } catch (err) {
    console.log(`[Workflow] Failed to persist workflow run:`, err);
  }
}

function handleSubprocessResult(result: { status: string; stdout: string; stderr: string; error: string }): void {
  console.log(`[Backend] handleSubprocessResult: ${JSON.stringify(result).slice(0, 100)}`);
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
  if (!found) console.log(`[Backend] handleSubprocessResult: no running job found, result=${JSON.stringify(result).slice(0, 80)}`);
}

function handlebackendResult(result: { cid: string; action: string; data?: unknown; error?: string }): void {
  console.log(`[Backend] handlebackendResult: received cid=${result.cid}, action=${result.action}`);
  const entry = backendPendingRequests.get(result.cid);
  if (!entry) {
    // Log all pending cids for debugging
    const pendingCids = Array.from(backendPendingRequests.keys());
    console.log(`[Backend] handlebackendResult: unknown cid=${result.cid}, pending=${JSON.stringify(pendingCids)}`);
    // Also check if the cid is similar (typo or timing issue)
    return;
  }
  backendPendingRequests.delete(result.cid);
  console.log(`[Backend] handlebackendResult: matched cid=${result.cid}, resolving`);
  if (result.error) {
    entry.reject(new Error(result.error));
  } else {
    entry.resolve(result.data);
  }
}

/** Send a job to the persistent subprocess */
async function runSubprocess(jobId: string, wrappedCode: string): Promise<void> {
  console.log(`[Worker] runSubprocess: waiting for worker, jobId=${jobId}`);
  await ensureSubprocess();
  console.log(`[Worker] runSubprocess: worker ready, pyProc=${!!pyProc}, stdin=${!!pyProc?.stdin}`);

  if (!pyProc || !pyProc.stdin) {
    jobResults.set(jobId, { status: "failed", stdout: "", stderr: "", error: "Worker not running", submittedAt: jobResults.get(jobId)?.submittedAt ?? Date.now(), completedAt: Date.now() });
    return;
  }

  const b64 = Buffer.from(wrappedCode).toString("base64");
  const msg = JSON.stringify({ code: b64, jobId }) + "\n";

  jobResults.set(jobId, { status: "running", stdout: "", stderr: "", error: "", submittedAt: Date.now() });
  console.log(`[Worker] runSubprocess: submitting job ${jobId}`);

  try {
    const ok = pyProc.stdin.write(msg);
    console.log(`[Worker] stdin.write ok=${ok}, jobId=${jobId}`);
  } catch (err: any) {
    console.log(`[Worker] stdin.write error: ${err.message}`);
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
    sseBuffer = "";
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

/** Parse qubit and experiment type from code string */
function parseJobInfo(code: string): { qubit: string; experiment: string } {
  // Match patterns like: sq.t1(q10lu1, ...) or sq.t1( q10lu1, ...)
  const sqMatch = code.match(/sq\.(\w+)\s*\(\s*([a-zA-Z0-9_]+)/);
  if (sqMatch) {
    return { experiment: sqMatch[1], qubit: sqMatch[2] };
  }
  // Fallback: try to find any word after first parenthesis as qubit
  const fallbackQubit = code.match(/\(\s*([a-zA-Z0-9_]+)/)?.[1] || "unknown";
  return { qubit: fallbackQubit, experiment: "custom" };
}

app.post("/job", async (req, res) => {
  if (!requireSubprocess(res)) return;

  const { code, plotCommand, analysisPrompt, autoAnalyze, model, _modelProvider, _modelBaseUrl, temperature } = req.body as JobSubmission;
  if (!code) { res.status(400).json({ error: "No code provided" }); return; }

  console.log("[Debug] Received job code:", code);

  const jobId = generateJobId();
  const wrappedCode = wrapExperimentCode(jobId, code, { plotCommand, analysisPrompt, autoAnalyze, model, _modelProvider, _modelBaseUrl, temperature });
  const { qubit, experiment } = parseJobInfo(code);

  jobResults.set(jobId, { status: "pending", stdout: "", stderr: "", error: "", submittedAt: Date.now(), qubit, experiment });
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
  if (USE_MICROSERVICES) {
    // 微服务模式：检查各微服务健康状态
    res.json({
      express: "ok",
      mode: "microservices",
      port: PORT,
      microservices: {
        llm: "http://localhost:3006",
        quantum: "http://localhost:3003",
        analysis: "http://localhost:3004",
        agent: "http://localhost:3005",
        image: "http://localhost:3007",
        workflow: "http://localhost:3008",
        task_queue: "http://localhost:3009",
      },
    });
    return;
  }

  // Legacy 模式：调用 job_runner.py
  try {
    const data = await sendbackendRequest("health", {}, 5000) as {
      status: string; ready: boolean; busy: boolean;
      session: { conn_id: string; name: string; host: string; port: number; connected: boolean } | null;
    };
    res.json({
      express: "ok",
      mode: "legacy",
      subprocess: pyProcReady ? "ready" : (pyProc ? "initializing" : "stopped"),
      backend: data,
    });
  } catch {
    res.json({ express: "ok", mode: "legacy", subprocess: pyProcReady ? "ready" : "stopped", backend: "unreachable" });
  }
});

// ── Background runner (backend proxy) ─────────────────────────────────────────

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
  if (!requireSubprocess(res)) return;

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
  console.log(`[Workflow] submit: name=${body.name}, nodes=${body.nodes?.length}, nodeIds=${JSON.stringify(body.nodes?.map(n => ({id: n.id, type: n.type})))}`);
  const b64 = Buffer.from(wfJson).toString("base64");
  const msg = JSON.stringify({ type: "workflow", workflow: b64, workflowId }) + "\n";

  workflowResults.set(workflowId, { ...entry, status: "running" });
  console.log(`[Workflow] submitted ${workflowId}`);

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
  if (!requireSubprocess(res)) return;

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

// ── Workflow Runs API (微服务模式代理) ──────────────────────────────────────
// 注意：在微服务模式下，这些路由已通过 serviceProxy.ts 代理到 workflow_service
// 以下代码暂时保留作为参考，将来可以完全移除

// /** GET /api/workflow-runs — list all workflow runs, optionally filtered by workflowId or workflowName */
// app.get("/api/workflow-runs", (req, res) => {
//   try {
//     const workflowId = req.query.workflowId as string | undefined;
//     const workflowName = req.query.workflowName as string | undefined;
//     const runs = listWorkflowRuns(workflowId, workflowName);
//     res.json(runs);
//   } catch (err: any) {
//     res.status(500).json({ error: err.message });
//   }
// });

// /** GET /api/workflow-runs/stats/:workflowId — get run statistics for a workflow */
// app.get("/api/workflow-runs/stats/:workflowId", (req, res) => {
//   try {
//     const stats = getWorkflowStats(req.params.workflowId);
//     res.json(stats);
//   } catch (err: any) {
//     res.status(500).json({ error: err.message });
//   }
// });

// /** GET /api/workflow-runs/:runId — get a specific workflow run */
// app.get("/api/workflow-runs/:runId", (req, res) => {
//   try {
//     const run = getWorkflowRun(req.params.runId);
//     if (!run) {
//       res.status(404).json({ error: "Workflow run not found" });
//       return;
//     }
//     res.json(run);
//   } catch (err: any) {
//     res.status(500).json({ error: err.message });
//   }
// });

// /** DELETE /api/workflow-runs/:runId — delete a workflow run */
// app.delete("/api/workflow-runs/:runId", (req, res) => {
//   try {
//     const deleted = deleteWorkflowRun(req.params.runId);
//     if (!deleted) {
//       res.status(404).json({ error: "Workflow run not found" });
//       return;
//     }
//     res.json({ success: true });
//   } catch (err: any) {
//     res.status(500).json({ error: err.message });
//   }
// });

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

    // Call LLM via microservice
    const messages = [
      ...(systemPrompt ? [{ role: 'system' as const, content: systemPrompt }] : []),
      { role: 'user' as const, content: message },
    ];

    const result = await proxyToService('llm', '/chat', 'POST', {
      provider: model.provider,
      modelId: model.modelId,
      baseUrl: model.baseUrl,
      messages,
      temperature: temperature ?? model.config.temperature ?? 0.3,
      maxTokens: maxTokens ?? model.config.maxTokens ?? 500,
    }) as { content?: string; error?: string; usage?: Record<string, number> };

    if (!result.ok) {
      res.status(500).json({ error: result.error });
      return;
    }

    const resultData = result.data as { content?: string; error?: string; usage?: Record<string, number> };
    if (resultData.error) {
      res.status(500).json({ error: resultData.error });
      return;
    }

    // Save to session if sessionId provided
    if (sessionId) {
      addMessage(sessionId, { role: 'user', content: message });
      addMessage(sessionId, { role: 'assistant', content: resultData.content || '' });
    }

    res.json({
      content: resultData.content,
      model: modelName,
      modelId: model.modelId,
      usage: resultData.usage,
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

/** POST /api/experiments/run-analysis — run analysis command on latest dataset (microservice) */
app.post("/api/experiments/run-analysis", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { command, expType } = req.body as { command?: string; expType?: string };
    if (!command) {
      res.status(400).json({ error: "command is required" });
      return;
    }
    const result = await proxyToService('analysis', '/execute', 'POST', {
      command,
      expType: expType || ""
    });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { command, expType } = req.body as { command?: string; expType?: string };

      if (!command) {
        res.status(400).json({ error: "command is required" });
        return;
      }

      const result = await sendbackendRequest("run_analysis", {
        command,
        expType: expType || ""
      }) as { success: boolean; stdout?: string; stderr?: string; metrics?: Record<string, number>; error?: string };

      if (result.error) {
        res.status(500).json({ success: false, error: result.error });
        return;
      }

      res.json({
        success: true,
        stdout: result.stdout || "",
        metrics: result.metrics || {},
      });
    } catch (err: any) {
      res.status(500).json({ success: false, error: err.message });
    }
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

// ── backend-style endpoints (direct, no backend server needed) ──────────────────

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

// ── Image Classification (microservice) ─────────────────────────────────────────

/** POST /api/classify/images — batch classify images in a folder (microservice) */
app.post("/api/classify/images", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { folderPath, backend, reviewThreshold, marginThreshold } = req.body as {
      folderPath?: string; backend?: string; reviewThreshold?: number; marginThreshold?: number;
    };
    const result = await proxyToService('image', '/classify/batch', 'POST', {
      folderPath: folderPath || "",
      backend: backend || "pytorch",
      reviewThreshold: reviewThreshold ?? 0.75,
      marginThreshold: marginThreshold ?? 0.15,
    });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { folderPath, backend, reviewThreshold, marginThreshold } = req.body as {
        folderPath?: string; backend?: string; reviewThreshold?: number; marginThreshold?: number;
      };
      const data = await sendbackendRequest("classify_images", {
        folderPath: folderPath || "",
        backend: backend || "pytorch",
        reviewThreshold: reviewThreshold ?? 0.75,
        marginThreshold: marginThreshold ?? 0.15,
      }) as { results?: unknown[]; error?: string };
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/classify/single — single image inference (microservice) */
app.post("/api/classify/single", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { imagePath, backend } = req.body as { imagePath?: string; backend?: string };
    const result = await proxyToService('image', '/classify/single', 'POST', {
      imagePath: imagePath || "",
      backend: backend || "pytorch",
    });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { imagePath, backend } = req.body as { imagePath?: string; backend?: string };
      const data = await sendbackendRequest("classify_single", {
        imagePath: imagePath || "",
        backend: backend || "pytorch",
      }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/classify/latest-experiment — classify latest experiment image (microservice) */
app.post("/api/classify/latest-experiment", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { qubit, experimentType, backend, reviewThreshold, marginThreshold } = req.body as {
      qubit?: string; experimentType?: string; backend?: string; reviewThreshold?: number; marginThreshold?: number;
    };
    const result = await proxyToService('image', '/classify/latest', 'POST', {
      qubit: qubit || "",
      experimentType: experimentType || "spectroscopy",
      backend: backend || "pytorch",
      reviewThreshold: reviewThreshold ?? 0.75,
      marginThreshold: marginThreshold ?? 0.15,
    });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { qubit, experimentType, backend, reviewThreshold, marginThreshold } = req.body as {
        qubit?: string; experimentType?: string; backend?: string; reviewThreshold?: number; marginThreshold?: number;
      };
      const data = await sendbackendRequest("classify_latest_experiment", {
        qubit: qubit || "",
        experimentType: experimentType || "spectroscopy",
        backend: backend || "pytorch",
        reviewThreshold: reviewThreshold ?? 0.75,
        marginThreshold: marginThreshold ?? 0.15,
      }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/classify/stats — get classification statistics (microservice) */
app.get("/api/classify/stats", async (req, res) => {
  if (USE_MICROSERVICES) {
    const sinceHours = parseInt(req.query.sinceHours as string) || 24;
    const result = await proxyToService('image', `/stats?sinceHours=${sinceHours}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const sinceHours = parseInt(req.query.sinceHours as string) || 24;
      const data = await sendbackendRequest("get_classification_stats", { sinceHours }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/classify/model-info — get model file info (microservice) */
app.get("/api/classify/model-info", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('image', '/model-info', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("get_model_info", {}) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/classify/train — trigger model training (microservice) */
app.post("/api/classify/train", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { epochs, batchSize, imbalanceMode } = req.body as {
      epochs?: number; batchSize?: number; imbalanceMode?: string;
    };
    const result = await proxyToService('image', '/train', 'POST', {
      epochs: epochs ?? 20,
      batchSize: batchSize ?? 32,
      imbalanceMode: imbalanceMode || "weighted",
    });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { epochs, batchSize, imbalanceMode } = req.body as {
        epochs?: number; batchSize?: number; imbalanceMode?: string;
      };
      const data = await sendbackendRequest("train_model", {
        epochs: epochs ?? 20,
        batchSize: batchSize ?? 32,
        imbalanceMode: imbalanceMode || "weighted",
      }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

// ── Quantum Agent (microservice) ─────────────────────────────────────────────────

/** POST /api/agent/chat — send a message to the quantum agent */
app.post("/api/agent/chat", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { message, mode, context } = req.body as {
      message?: string; mode?: string; context?: Record<string, unknown>;
    };
    if (!message) { res.status(400).json({ error: "message is required" }); return; }
    const result = await proxyToService('agent', '/chat', 'POST', {
      message, mode: mode || "react", context: context || {},
    });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { message, mode, context } = req.body as {
        message?: string; mode?: string; context?: Record<string, unknown>;
      };
      if (!message) { res.status(400).json({ error: "message is required" }); return; }
      const data = await sendbackendRequest("agent_chat", {
        message, mode: mode || "react", context: context || {},
      }, 600_000) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/agent/chat/stream — streaming agent chat (SSE) */
app.post("/api/agent/chat/stream", async (req, res) => {
  if (USE_MICROSERVICES) {
    // Proxy to agent microservice streaming endpoint
    const { message, mode, context } = req.body as {
      message?: string; mode?: string; context?: Record<string, unknown>;
    };
    if (!message) { res.status(400).json({ error: "message is required" }); return; }

    // Set SSE headers
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders();

    // Proxy to agent service streaming endpoint
    const result = await proxyToService('agent', '/chat/stream', 'POST', {
      message, mode: mode || "react", context: context || {},
    }, false);

    if (!result.ok) {
      res.write(`event: error\ndata: ${JSON.stringify({ error: result.error })}\n\n`);
      res.end();
      return;
    }

    // Forward the streaming response
    res.write(result.data);
    res.end();
  } else {
    console.log('[SSE] /api/agent/chat/stream called');
    try {
      const { message, mode, context } = req.body as {
        message?: string; mode?: string; context?: Record<string, unknown>;
      };
      if (!message) { res.status(400).json({ error: "message is required" }); return; }

      res.setHeader("Content-Type", "text/event-stream");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      res.setHeader("X-Accel-Buffering", "no");
      res.flushHeaders();

      const cid = "stream_" + Date.now() + Math.random().toString(36).slice(2, 8);
      const msg = JSON.stringify({ type: "backend", cid, action: "agent_chat_stream", data: { message, mode: mode || "react", context: context || {} } }) + "\n";

      activeStreams.set(cid, (sseData: string) => { res.write(sseData); });

      backendPendingRequests.set(cid, {
        resolve: () => {
          console.log(`[SSE] Streaming completed for cid:`, cid);
          activeStreams.delete(cid);
          backendPendingRequests.delete(cid);
          res.end();
        },
        reject: (err: Error) => {
          console.log(`[SSE] Streaming error for cid:`, cid, err.message);
          activeStreams.delete(cid);
          backendPendingRequests.delete(cid);
          res.write(`event: error\ndata: ${JSON.stringify({ error: err.message })}\n\n`);
          res.end();
        }
      });

      if (pyProc?.stdin) { pyProc.stdin.write(msg); }

      setTimeout(() => {
        if (activeStreams.has(cid)) {
          console.log(`[SSE] Timeout for cid:`, cid);
          activeStreams.delete(cid);
          backendPendingRequests.delete(cid);
          res.write(`event: error\ndata: ${JSON.stringify({ error: "Timeout" })}\n\n`);
          res.end();
        }
      }, 600_000);
    } catch (err: any) {
      console.log(`[SSE] Error:`, err.message);
      res.write(`event: error\ndata: ${JSON.stringify({ error: err.message })}\n\n`);
      res.end();
    }
  }
});

/** GET /api/agent/modes — get available agent reasoning modes */
app.get("/api/agent/modes", (_req, res) => {
  if (USE_MICROSERVICES) {
    res.json({ modes: ["react", "plan_and_execute", "reflexion"] });
  } else {
    res.json({ modes: ["react", "plan_and_execute", "reflexion"] });
  }
});

/** POST /api/agent/reset — reset agent session (microservice) */
app.post("/api/agent/reset", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('agent', '/reset', 'POST');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    res.json({ success: true });
  }
});

/** GET /api/agent/debug-env — check environment variables (microservice) */
app.get("/api/agent/debug-env", async (_req, res) => {
  const expressEnv = {
    minimax: process.env.MINIMAX_API_KEY ? `SET (len=${process.env.MINIMAX_API_KEY.length})` : "NOT SET",
    openai: process.env.OPENAI_API_KEY ? "SET" : "NOT SET",
  };
  if (USE_MICROSERVICES) {
    const result = await proxyToService('agent', '/debug-env', 'GET');
    res.status(result.ok ? 200 : 502).json({ express: expressEnv, python: result.data ?? { error: result.error } });
  } else {
    try {
      const data = await sendbackendRequest("debug_env", {}) as Record<string, unknown>;
      res.json({ express: expressEnv, python: data });
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

// ── Memory & Reflection API (microservice) ─────────────────────────────────────────────────

/** POST /api/agent/memory/episodes — list episodes */
app.post("/api/agent/memory/episodes", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { limit, qubit, status } = req.body;
    const result = await proxyToService('agent', '/memory/episodes', 'POST', { limit, qubit, status });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { limit, qubit, status } = req.body;
      const data = await sendbackendRequest("memory_list_episodes", { limit, qubit, status }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/agent/memory/episodes/:id — get episode details */
app.get("/api/agent/memory/episodes/:id", async (req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('agent', `/memory/episodes/${req.params.id}`, 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("memory_get_episode", { episode_id: req.params.id }) as Record<string, unknown>;
      if (data.error) { res.status(404).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** DELETE /api/agent/memory/episodes/:id — archive episode */
app.delete("/api/agent/memory/episodes/:id", async (req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('agent', `/memory/episodes/${req.params.id}`, 'DELETE');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("memory_archive_episode", { episode_id: req.params.id }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/agent/memory/skills — list all skills */
app.get("/api/agent/memory/skills", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('agent', '/memory/skills', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("memory_list_skills", {}) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** DELETE /api/agent/memory/skills/:id — delete skill */
app.delete("/api/agent/memory/skills/:id", async (req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('agent', `/memory/skills/${req.params.id}`, 'DELETE');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("memory_delete_skill", { skill_id: req.params.id }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/agent/memory/stats — get memory stats */
app.get("/api/agent/memory/stats", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('agent', '/memory/stats', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("memory_stats", {}) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/agent/memory/recall — recall relevant memories */
app.post("/api/agent/memory/recall", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { task, qubit } = req.body;
    const result = await proxyToService('agent', '/memory/recall', 'POST', { task, qubit });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { task, qubit } = req.body;
      const data = await sendbackendRequest("memory_recall", { task, qubit }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/agent/memory/reflect — trigger reflection */
app.post("/api/agent/memory/reflect", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { episode_id, task, result_data } = req.body;
    const result = await proxyToService('agent', '/memory/reflect', 'POST', { episode_id, task, result_data });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { episode_id, task, result_data } = req.body;
      const data = await sendbackendRequest("memory_reflect", { episode_id, task, result_data }) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

// ── Hermes Agent API (microservice) ──────────────────────────────────────────────────────────

/** POST /api/hermes/chat — Hermes agent chat */
app.post("/api/hermes/chat", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { message, model, base_url, enabled_toolsets, session_id } = req.body as {
      message?: string; model?: string; base_url?: string; enabled_toolsets?: string[]; session_id?: string;
    };
    if (!message) { res.status(400).json({ error: "message is required" }); return; }
    const result = await proxyToService('hermes', '/chat', 'POST', {
      message, model, base_url, enabled_toolsets, session_id,
    });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { message, model, base_url, enabled_toolsets, session_id } = req.body as {
        message?: string; model?: string; base_url?: string; enabled_toolsets?: string[]; session_id?: string;
      };
      if (!message) { res.status(400).json({ error: "message is required" }); return; }
      const data = await sendbackendRequest("hermes_chat", {
        message, model, base_url, enabled_toolsets, session_id,
      }, 600_000) as Record<string, unknown>;
      if (data.error) { res.status(502).json({ error: data.error }); return; }
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/hermes/chat/stream — Streaming Hermes agent chat (SSE) */
app.post("/api/hermes/chat/stream", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { message, model, base_url, session_id } = req.body as {
      message?: string; model?: string; base_url?: string; session_id?: string;
    };
    if (!message) { res.status(400).json({ error: "message is required" }); return; }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders();

    const result = await proxyToService('hermes', '/chat/stream', 'POST', { message, model, base_url, session_id }, false);
    if (!result.ok) {
      res.write(`event: error\ndata: ${JSON.stringify({ error: result.error })}\n\n`);
      res.end();
      return;
    }
    res.write(result.data);
    res.end();
  } else {
    console.log('[Hermes SSE] /api/hermes/chat/stream called');
    try {
      const { message, model, base_url, session_id } = req.body as {
        message?: string; model?: string; base_url?: string; session_id?: string;
      };
      if (!message) { res.status(400).json({ error: "message is required" }); return; }

      res.setHeader("Content-Type", "text/event-stream");
      res.setHeader("Cache-Control", "no-cache");
      res.setHeader("Connection", "keep-alive");
      res.setHeader("X-Accel-Buffering", "no");
      res.flushHeaders();

      const cid = "hermes_stream_" + Date.now() + Math.random().toString(36).slice(2, 8);
      const msg = JSON.stringify({ type: "backend", cid, action: "hermes_chat_stream", data: { message, model, base_url, session_id } }) + "\n";

      activeStreams.set(cid, (sseData: string) => { res.write(sseData); });

      backendPendingRequests.set(cid, {
        resolve: () => {
          console.log(`[Hermes] Streaming completed for cid:`, cid);
          activeStreams.delete(cid);
          backendPendingRequests.delete(cid);
          res.end();
        },
        reject: (err: Error) => {
          console.log(`[Hermes] Streaming error for cid:`, cid, err.message);
          activeStreams.delete(cid);
          backendPendingRequests.delete(cid);
          res.write(`event: error\ndata: ${JSON.stringify({ error: err.message })}\n\n`);
          res.end();
        }
      });

      if (pyProc?.stdin) { pyProc.stdin.write(msg); }

      setTimeout(() => {
        if (activeStreams.has(cid)) {
          console.log(`[Hermes] Timeout for cid:`, cid);
          activeStreams.delete(cid);
          backendPendingRequests.delete(cid);
          res.write(`event: error\ndata: ${JSON.stringify({ error: "Timeout" })}\n\n`);
          res.end();
        }
      }, 600_000);
    } catch (err: any) {
      console.log(`[Hermes] Error:`, err.message);
      res.write(`event: error\ndata: ${JSON.stringify({ error: err.message })}\n\n`);
      res.end();
    }
  }
});

/** GET /api/hermes/models — Get available models for Hermes */
app.get("/api/hermes/models", (_req, res) => {
  const configPath = path.join(__dirname, "..", "config", "model_configs.json");
  let models: Array<{ id: string; name: string; provider: string; base_url?: string }> = [];

  try {
    const data = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    models = (data.models || [])
      .filter((m: any) => m.enabled)
      .map((m: any) => ({
        id: m.modelId || m.name,
        name: m.name,
        provider: m.provider,
        base_url: m.baseUrl || undefined,
      }));
  } catch (e) {
    console.error("Failed to load model configs:", e);
    models = [
      { id: "MiniMax-M2.7", name: "MiniMax M2.7", provider: "minimax", base_url: "https://api.minimaxi.com/" },
    ];
  }

  res.json({ models });
});

// ── MCP Tools CRUD ─────────────────────────────────────────────────────────────

const MCP_TOOLS_FILE = path.join(__dirname, "..", "config", "mcp_tools.json");

function loadMcpTools() {
  try {
    const raw = fs.readFileSync(MCP_TOOLS_FILE, "utf-8");
    return JSON.parse(raw);
  } catch { return { mcp_servers: [], mcp_tools: [] }; }
}

function saveMcpTools(data: unknown) {
  fs.writeFileSync(MCP_TOOLS_FILE, JSON.stringify(data, null, 2), "utf-8");
}

app.get("/api/mcp-tools", (_req, res) => {
  res.json({ success: true, ...loadMcpTools() });
});

app.post("/api/mcp-tools", (req, res) => {
  const data = loadMcpTools();
  const tool = req.body as Record<string, unknown>;
  if (!tool.id) { res.status(400).json({ error: "tool.id is required" }); return; }
  data.mcp_tools = data.mcp_tools || [];
  data.mcp_tools.push(tool);
  saveMcpTools(data);
  res.json({ success: true, tool });
});

app.put("/api/mcp-tools/:id", (req, res) => {
  const data = loadMcpTools();
  const idx = (data.mcp_tools as Record<string, unknown>[]).findIndex(t => t.id === req.params.id);
  if (idx === -1) { res.status(404).json({ error: "Tool not found" }); return; }
  data.mcp_tools[idx] = { ...data.mcp_tools[idx], ...req.body };
  saveMcpTools(data);
  res.json({ success: true, tool: data.mcp_tools[idx] });
});

app.delete("/api/mcp-tools/:id", (req, res) => {
  const data = loadMcpTools();
  data.mcp_tools = (data.mcp_tools as Record<string, unknown>[]).filter(t => t.id !== req.params.id);
  saveMcpTools(data);
  res.json({ success: true });
});

// ── Skills CRUD ───────────────────────────────────────────────────────────────

const SKILLS_FILE = path.join(__dirname, "..", "config", "skills.json");

function loadSkills() {
  try {
    const raw = fs.readFileSync(SKILLS_FILE, "utf-8");
    return JSON.parse(raw);
  } catch { return { skills: [] }; }
}

function saveSkills(data: unknown) {
  fs.writeFileSync(SKILLS_FILE, JSON.stringify(data, null, 2), "utf-8");
}

app.get("/api/skills", (_req, res) => {
  res.json({ success: true, skills: loadSkills().skills });
});

app.post("/api/skills", (req, res) => {
  const data = loadSkills();
  const skill = req.body as Record<string, unknown>;
  if (!skill.name) { res.status(400).json({ error: "skill.name is required" }); return; }
  skill.id = skill.id || String(skill.name).toLowerCase().replace(/\s+/g, "_");
  data.skills = data.skills || [];
  data.skills.push(skill);
  saveSkills(data);
  res.json({ success: true, skill });
});

app.put("/api/skills/:id", (req, res) => {
  const data = loadSkills();
  const idx = (data.skills as Record<string, unknown>[]).findIndex(s => s.id === req.params.id);
  if (idx === -1) { res.status(404).json({ error: "Skill not found" }); return; }
  data.skills[idx] = { ...data.skills[idx], ...req.body };
  saveSkills(data);
  res.json({ success: true, skill: data.skills[idx] });
});

app.delete("/api/skills/:id", (req, res) => {
  const data = loadSkills();
  data.skills = (data.skills as Record<string, unknown>[]).filter(s => s.id !== req.params.id);
  saveSkills(data);
  res.json({ success: true });
});

app.get("/api/skills/match", (req, res) => {
  const { message } = req.query as { message?: string };
  if (!message) { res.json({ success: true, matched: [] }); return; }
  const data = loadSkills();
  const msgLower = message.toLowerCase();
  const matched = (data.skills as Record<string, unknown>[]).filter(s => {
    const kws: string[] = (s.trigger_keywords as string[]) || [];
    return kws.some(kw => kw.toLowerCase().includes(msgLower) || msgLower.includes(kw.toLowerCase()));
  });
  res.json({ success: true, matched });
});

app.post("/api/skills/execute", (req, res) => {
  const { skill_id, params } = req.body as { skill_id?: string; params?: Record<string, string> };
  if (!skill_id) { res.status(400).json({ error: "skill_id is required" }); return; }
  const data = loadSkills();
  const skill = (data.skills as Record<string, unknown>[]).find(s => s.id === skill_id);
  if (!skill) { res.status(404).json({ error: "Skill not found" }); return; }
  const resolvedSteps = (skill.steps as Record<string, unknown>[]).map((step: Record<string, unknown>) => {
    const resolvedInput: Record<string, unknown> = {};
    const input = step.input as Record<string, unknown>;
    for (const [k, v] of Object.entries(input)) {
      if (typeof v === "string") {
        let resolved = v;
        for (const [pk, pv] of Object.entries(params || {})) {
          resolved = resolved.replace(new RegExp(`{{\\s*${pk}\\s*}}`, "g"), String(pv));
        }
        resolvedInput[k] = resolved;
      } else {
        resolvedInput[k] = v;
      }
    }
    return { tool: step.tool, input: resolvedInput };
  });
  res.json({ success: true, steps: resolvedSteps });
});

app.post("/api/skills/import", (req, res) => {
  const { skills } = req.body as { skills?: unknown[] };
  if (!Array.isArray(skills)) { res.status(400).json({ error: "skills array is required" }); return; }
  const data = loadSkills();
  for (const skill of skills) {
    const s = skill as Record<string, unknown>;
    s.id = s.id || String(s.name || "").toLowerCase().replace(/\s+/g, "_");
    data.skills = data.skills || [];
    data.skills.push(s);
  }
  saveSkills(data);
  res.json({ success: true, count: skills.length });
});

app.get("/api/skills/export", (_req, res) => {
  const data = loadSkills();
  res.setHeader("Content-Disposition", `attachment; filename="skills.json"`);
  res.setHeader("Content-Type", "application/json");
  res.json(data);
});

/** GET /sessions/config — get current session config (microservice) */
app.get("/sessions/config", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('quantum', '/session/config', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    const config = loadSessionConfig();
    const fullPath = ['', config.user, ...config.path];
    res.json({ user: config.user, path: config.path, fullPath });
  }
});

/** GET /sessions/status — debug: check session status in job_runner (microservice) */
app.get("/sessions/status", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('quantum', '/session/status', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const result = await sendbackendRequest("debug_data", {}) as Record<string, unknown>;
      res.json({
        debugData: result,
        configSession: loadSessionConfig(),
      });
    } catch (err: any) {
      res.status(502).json({ error: err.message });
    }
  }
});

/** GET /sessions/test-load — test loading the latest dataset (microservice) */
app.get("/sessions/test-load", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('quantum', '/session/test-load', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const result = await sendbackendRequest("test_load_dataset", {}) as Record<string, unknown>;
      res.json(result);
    } catch (err: any) {
      res.status(502).json({ error: err.message });
    }
  }
});

/** GET /sessions/diagnostic — full diagnostic info (microservice) */
app.get("/sessions/diagnostic", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('quantum', '/session/diagnostic', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const debugData = await sendbackendRequest("debug_data", {}) as Record<string, unknown>;
      const testLoad = await sendbackendRequest("test_load_dataset", {}) as Record<string, unknown>;
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
  }
});

/** POST /sessions/plot — plot the latest dataset with custom command (microservice) */
app.post("/sessions/plot", async (req, res) => {
  if (USE_MICROSERVICES) {
    const { command } = req.body as { command?: string };
    const result = await proxyToService('analysis', '/plot', 'POST', { command: command || "" });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { command } = req.body as { command?: string };
      const result = await sendbackendRequest("plot_dataset", { command: command || "" }) as Record<string, unknown>;
      res.json(result);
    } catch (err: any) {
      res.status(502).json({ error: err.message });
    }
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

/** GET /sessions — list DataVault sessions (microservice) */
app.get("/sessions", async (_req, res) => {
  if (USE_MICROSERVICES) {
    // Proxy to quantum service
    const result = await proxyToService('quantum', '/sessions', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("sessions", {}) as { current: unknown; sessions: unknown };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /sessions/switch — switch DataVault session (microservice) */
app.post("/sessions/switch", async (req, res) => {
  if (USE_MICROSERVICES) {
    const body = req.body as { user?: string; path?: string[] };
    let sessionPath: string[] = [];

    // Support both formats
    if (body.user && body.path) {
      sessionPath = ['', body.user, ...body.path];
    } else if (body.path && body.path.length >= 2) {
      sessionPath = body.path;
    }

    const result = await proxyToService('quantum', '/switch_session', 'POST', { session_path: sessionPath });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const body = req.body as { user?: string; path?: string[] };
      let user: string | undefined;
      let pathSegments: string[] | undefined;

      if (body.user && body.path) {
        user = body.user;
        pathSegments = body.path;
      } else if (body.path && body.path.length >= 2) {
        user = body.path[1];
        pathSegments = body.path.slice(2);
      }

      if (user && pathSegments) {
        saveSessionConfig(user, pathSegments);
      }

      const sessionPath = user && pathSegments ? ['', user, ...pathSegments] : [];
      const switchResult = await sendbackendRequest("switch_session", { path: sessionPath }) as { success: boolean; path: string[]; qubits?: Array<{ name: string; f10?: number; fread?: number }> };

      res.json(switchResult);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /qubits — list qubits in current session (microservice) */
app.get("/qubits", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('quantum', '/qubits', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("list_qubits", {}) as { qubits: Array<{ name: string; f10?: number; fread?: number; bias_z?: number; error?: string }>; sessionPath: string[] };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /sessions/tree — get full DataVault directory tree (microservice) */
app.get("/sessions/tree", async (req, res) => {
  if (USE_MICROSERVICES) {
    const maxDepth = parseInt((req.query.max_depth as string) ?? '5', 10);
    const result = await proxyToService('quantum', '/session_tree', 'POST', { max_depth: maxDepth });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("session_tree", {}) as { tree: Array<{ name: string; path: string[]; hasChildren: boolean }> };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /sessions/plot-historical — plot historical dataset with custom command (microservice) */
app.post("/sessions/plot-historical", async (req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('analysis', '/plot/historical', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const { name, path, command } = req.body;
      if (!name) { res.status(400).json({ error: "name required" }); return; }
      const result = await sendbackendRequest("plot_historical_dataset", { name, path, command }) as {
        success: boolean;
        plot_filename?: string;
        dataset_name?: string;
        analysis_output?: string;
        error?: string;
      };
      res.json(result);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** POST /api/analysis/plot/experiments — plot dataset by experiment type (returns Base64) */
app.post("/api/analysis/plot/experiments", async (req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('analysis', '/plot/experiments', 'POST', req.body);
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    // Legacy mode not supported for new endpoint
    res.status(501).json({ error: "Not implemented in legacy mode" });
  }
});

/** GET /qubits/:name/params — get qubit parameters (microservice) */
app.get("/qubits/:name/params", async (req, res) => {
  if (USE_MICROSERVICES) {
    const name = req.params.name;
    const result = await proxyToService('quantum', '/qubit/params', 'POST', { name });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const name = req.params.name;
      const data = await sendbackendRequest("get_qubit_params", { name }) as {
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
  }
});

/** PUT /qubits/:name/params — update qubit parameters (microservice) */
app.put("/qubits/:name/params", async (req, res) => {
  if (USE_MICROSERVICES) {
    const name = req.params.name;
    const result = await proxyToService('quantum', '/qubit/set_params', 'POST', { name, params: req.body });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const name = req.params.name;
      const params = req.body as Record<string, number | null>;
      const data = await sendbackendRequest("set_qubit_params", { name, params }) as {
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
  }
});

/** GET /datasets — list DataVault datasets in a path (microservice) */
app.get("/datasets", async (req, res) => {
  if (USE_MICROSERVICES) {
    const path = req.query.path as string | undefined;
    const result = await proxyToService('quantum', '/datasets', 'POST', { path: path ?? null });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const path = req.query.path as string | undefined;
      const data = await sendbackendRequest("datasets", { path: path || getDefaultSessionPath() }) as { path: string; groups: string[]; datasets: unknown[] };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /datasets/plot?name=...&path=... — generate historical dataset plot PNG (microservice) */
app.get("/datasets/plot", async (req, res) => {
  if (USE_MICROSERVICES) {
    const name = req.query.name as string;
    const path = req.query.path as string;
    if (!name) { res.status(400).json({ error: "name query param required" }); return; }
    const result = await proxyToService('analysis', '/plot', 'POST', { name, path });
    if (!result.ok || !result.data) {
      res.status(502).json({ error: result.error });
      return;
    }
    const plotPath = (result.data as { plotPath?: string }).plotPath;
    if (!plotPath || !fs.existsSync(plotPath)) { res.status(404).json({ error: "Plot file not found" }); return; }
    res.setHeader("Content-Type", "image/png");
    res.sendFile(plotPath);
  } else {
    const name = req.query.name as string;
    const datasetPath = req.query.path as string || getDefaultSessionPath();
    if (!name) { res.status(400).json({ error: "name query param required" }); return; }
    try {
      const result = await sendbackendRequest("plot", { name, path: datasetPath }) as { plotPath?: string; name?: string; error?: string };
      if (result.error || !result.plotPath) {
        res.status(500).json({ error: result.error || "Plot generation failed" });
        return;
      }
      if (!fs.existsSync(result.plotPath)) { res.status(404).json({ error: "Plot file not found" }); return; }
      res.setHeader("Content-Type", "image/png");
      res.sendFile(result.plotPath);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/quantum/session_tree — get session directory tree (microservice) */
app.get("/api/quantum/session_tree", async (req, res) => {
  if (USE_MICROSERVICES) {
    const maxDepth = parseInt(req.query.max_depth as string) || 5;
    const result = await proxyToService('quantum', '/session_tree', 'POST', { max_depth: maxDepth });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("session_tree", {}) as { tree: Array<{ name: string; path: string[]; hasChildren: boolean }> };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/quantum/datasets — list DataVault datasets in a path (microservice) */
app.get("/api/quantum/datasets", async (req, res) => {
  if (USE_MICROSERVICES) {
    const path = req.query.path as string | undefined;
    const result = await proxyToService('quantum', '/datasets', 'POST', { path: path ?? null });
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const path = req.query.path as string | undefined;
      const data = await sendbackendRequest("datasets", { path: path || getDefaultSessionPath() }) as { path: string; groups: string[]; datasets: unknown[] };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/quantum/qubits — get qubits (microservice) */
app.get("/api/quantum/qubits", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('quantum', '/qubits', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("qubits", {}) as { qubits: unknown[]; count: number };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /api/quantum/sessions — get sessions (microservice) */
app.get("/api/quantum/sessions", async (_req, res) => {
  if (USE_MICROSERVICES) {
    const result = await proxyToService('quantum', '/sessions', 'GET');
    res.status(result.ok ? 200 : 502).json(result.data ?? { error: result.error });
  } else {
    try {
      const data = await sendbackendRequest("sessions", {}) as { sessions: unknown[]; current: unknown };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /datasets — get detailed hardware connection status (microservice) */
app.get("/hardware/status", async (_req, res) => {
  if (USE_MICROSERVICES) {
    try {
      const quantumHealth = await fetch("http://localhost:3003/health", { signal: AbortSignal.timeout(3000) });
      const quantumData = quantumHealth.ok ? await quantumHealth.json() : {};

      res.json({
        overall: quantumData.labrad_connected ? "ok" : "degraded",
        timestamp: new Date().toISOString(),
        services: {
          labrad: quantumData.labrad_connected ? "connected" : "disconnected",
          datavault: quantumData.datalab_connected ? "connected" : "disconnected",
          quantum_service: "via_microservice",
        },
        devices: {},
        issues: quantumData.labrad_connected ? [] : ["LabRAD not connected"],
      });
    } catch (err: any) {
      res.status(502).json({ error: err.message });
    }
  } else {
    if (!requireSubprocess(res)) return;
    try {
      const data = await sendbackendRequest("hardware_status", {}) as {
        overall: string; timestamp: string;
        services: Record<string, unknown>; devices: Record<string, unknown>;
        issues: string[];
      };
      res.json(data);
    } catch (err: any) { res.status(502).json({ error: err.message }); }
  }
});

/** GET /hardware/quick — get quick status summary for header */
app.get("/hardware/quick", async (_req, res) => {
  if (USE_MICROSERVICES) {
    // 微服务模式：从各微服务汇总状态
    try {
      const quantumHealth = await fetch("http://localhost:3003/health", { signal: AbortSignal.timeout(3000) });
      const llmHealth = await fetch("http://localhost:3006/health", { signal: AbortSignal.timeout(3000) });
      const quantumData = quantumHealth.ok ? await quantumHealth.json() : {};
      const llmData = llmHealth.ok ? await llmHealth.json() : {};
      res.json({
        labrad: quantumData.labrad_connected ? "connected" : "disconnected",
        llm: llmData.status === "healthy" ? "ready" : "unavailable",
        ray: "via_quantum",
        datavault: quantumData.datalab_connected ? "connected" : "disconnected",
        message: `Microservices mode - Quantum: ${quantumData.labrad_connected ? 'connected' : 'disconnected'}`,
      });
    } catch (err: any) {
      res.json({
        labrad: "checking",
        llm: "unavailable",
        ray: "unknown",
        datavault: "unknown",
        message: "Microservices health check failed: " + err.message,
      });
    }
    return;
  }
  try {
    const data = await sendbackendRequest("quick_status", {}) as {
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
  console.log(`[Server] Listening on http://localhost:${PORT}`);
  console.log(`[Server] Integrated: experiments, sessions, datasets, plots (no backend needed)`);

  // Debug: list all registered routes
  const routes: string[] = [];
  app._router?.stack?.forEach((middleware: any) => {
    if (middleware.route) {
      routes.push(`${Object.keys(middleware.route.methods).join(',').toUpperCase()} ${middleware.route.path}`);
    } else if (middleware.name === 'router') {
      middleware.handle?.stack?.forEach((handler: any) => {
        if (handler.route) {
          routes.push(`${Object.keys(handler.route.methods).join(',').toUpperCase()} ${handler.route.path}`);
        }
      });
    }
  });
  console.log('[Server] Registered routes:', routes.filter(r => r.includes('agent')).join(', '));
});

// Debug endpoint to list all routes
app.get('/api/debug/routes', (_req, res) => {
  const routes: string[] = [];
  app._router?.stack?.forEach((middleware: any) => {
    if (middleware.route) {
      routes.push(`${Object.keys(middleware.route.methods).join(',').toUpperCase()} ${middleware.route.path}`);
    } else if (middleware.name === 'router') {
      middleware.handle?.stack?.forEach((handler: any) => {
        if (handler.route) {
          routes.push(`${Object.keys(handler.route.methods).join(',').toUpperCase()} ${handler.route.path}`);
        }
      });
    }
  });
  res.json({ routes });
});