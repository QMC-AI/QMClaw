/**
 * qmclaw-server TypeScript types
 */

/** Job status in the task queue */
export enum JobStatus {
  PENDING = "pending",
  RUNNING = "running",
  COMPLETED = "completed",
  FAILED = "failed",
  CANCELLED = "cancelled",
}

/** Types of jobs that can be queued */
export type JobType = "experiment" | "optimization" | "batch";

/** Base job interface */
export interface BaseJob {
  id: string;
  type: JobType;
  status: JobStatus;
  createdAt: number;
  updatedAt: number;
  qubit: string;
}

/** Experiment job - single experiment execution */
export interface ExperimentJob extends BaseJob {
  type: "experiment";
  experiment: ExperimentType;
  params: Record<string, unknown>;
  result?: ExperimentResult;
}

/** Optimization job - parameter optimization */
export interface OptimizationJob extends BaseJob {
  type: "optimization";
  parameter: string;
  strategy: OptimizationStrategy;
  initialValue: number;
  valueRange: [number, number];
  stepSize: number;
  result?: OptimizationResult;
  iterations?: OptimizationIteration[];
}

/** Batch job - multiple experiments */
export interface BatchJob extends BaseJob {
  type: "batch";
  jobs: string[]; // Job IDs
  results: Record<string, ExperimentResult>;
}

/** Experiment types available */
export type ExperimentType =
  | "spectroscopy"
  | "s21"
  | "ramsey"
  | "iqraw"
  | "t1"
  | "xeb"
  | "pi_pulse"
  | "custom";

/** Optimization strategies */
export type OptimizationStrategy =
  | "LINEAR_SCAN"
  | "BINARY_SEARCH"
  | "GRADIENT_ASCENT";

/** Qubit metrics */
export interface QubitMetrics {
  readout_fidelity: number;
  single_qubit_gate_fidelity: number;
  t1: number;
}

/** Experiment result */
export interface ExperimentResult {
  data: number[][];
  metrics?: QubitMetrics;
  timestamp: number;
}

/** Single optimization iteration */
export interface OptimizationIteration {
  iteration: number;
  parameterValue: number;
  metrics: QubitMetrics;
  improvement: "improved" | "degraded" | "unchanged";
}

/** Optimization result */
export interface OptimizationResult {
  bestValue: number;
  bestMetrics: QubitMetrics;
  optimizationTime: number;
  testCount: number;
}

/** Session info from LabRAD */
export interface SessionInfo {
  status: "connected" | "disconnected" | "error";
  user?: string;
  qubits?: string[];
  session?: string;
  message?: string;
}

/** Worker status */
export interface WorkerStatus {
  id: string;
  status: "idle" | "busy" | "error";
  currentJob?: string;
  lastHeartbeat: number;
}

/** WebSocket events */
export interface WSClientMessage {
  type: "subscribe" | "unsubscribe";
  channel: string;
}

export interface WSServerMessage {
  type: "job:progress" | "job:complete" | "job:failed" | "data:update" | "metrics:update";
  jobId: string;
  data: unknown;
}