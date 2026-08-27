/**
 * Python Bridge - Communication with Python Worker via stdin/stdout JSON-RPC
 */

import { spawn, ChildProcess } from "child_process";
import * as path from "path";
import {
  ExperimentType,
  OptimizationStrategy,
  QubitMetrics,
  SessionInfo,
} from "../types";

export interface JsonRpcRequest {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
  id: number;
}

export interface JsonRpcResponse {
  jsonrpc: "2.0";
  result?: Record<string, unknown>;
  error?: { code: number; message: string };
  id: number;
}

export class PythonBridge {
  private process: ChildProcess | null = null;
  private pendingRequests: Map<number, {
    resolve: (value: unknown) => void;
    reject: (reason: unknown) => void;
  }> = new Map();
  private requestId = 0;
  private workerPath: string;

  constructor(workerPath?: string) {
    this.workerPath = workerPath || path.join(__dirname, "..", "python", "workflow", "entry.py");
  }

  /**
   * Start the Python worker process
   */
  start(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.process = spawn("python", [this.workerPath], {
          stdio: ["pipe", "pipe", "pipe"],
          env: { ...process.env },
        });

        this.process.stdout?.on("data", (data: Buffer) => {
          this.handleResponse(data.toString());
        });

        this.process.stderr?.on("data", (data: Buffer) => {
          console.error("[Python Worker stderr]:", data.toString());
        });

        this.process.on("error", (err) => {
          console.error("[Python Worker error]:", err);
          reject(err);
        });

        this.process.on("exit", (code) => {
          console.log(`[Python Worker exited with code ${code}]`);
          this.process = null;
        });

        // Give it a moment to start
        setTimeout(resolve, 500);
      } catch (err) {
        reject(err);
      }
    });
  }

  /**
   * Stop the Python worker process
   */
  stop(): Promise<void> {
    return new Promise((resolve) => {
      if (this.process) {
        this.process.kill();
        this.process = null;
      }
      resolve();
    });
  }

  /**
   * Send a JSON-RPC request and wait for response
   */
  async sendRequest(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>> {
    if (!this.process) {
      throw new Error("Python worker not running");
    }

    const id = ++this.requestId;
    const request: JsonRpcRequest = {
      jsonrpc: "2.0",
      method,
      params,
      id,
    };

    return new Promise((resolve, reject) => {
      this.pendingRequests.set(id, { resolve: resolve as (value: unknown) => void, reject });

      const message = JSON.stringify(request) + "\n";
      this.process?.stdin?.write(message, (err) => {
        if (err) {
          this.pendingRequests.delete(id);
          reject(err);
        }
      });

      // Timeout after 60 seconds
      setTimeout(() => {
        if (this.pendingRequests.has(id)) {
          this.pendingRequests.delete(id);
          reject(new Error(`Request ${method} timed out`));
        }
      }, 60000);
    });
  }

  /**
   * Handle incoming JSON-RPC response
   */
  private handleResponse(data: string): void {
    const lines = data.split("\n").filter((line) => line.trim());

    for (const line of lines) {
      try {
        const response: JsonRpcResponse = JSON.parse(line);

        if (response.id && this.pendingRequests.has(response.id)) {
          const { resolve, reject } = this.pendingRequests.get(response.id)!;
          this.pendingRequests.delete(response.id);

          if (response.error) {
            reject(new Error(response.error.message));
          } else {
            resolve(response.result);
          }
        }
      } catch (err) {
        console.error("[Python Bridge] Failed to parse response:", err);
      }
    }
  }

  // Convenience methods for common operations

  async initSession(user: string): Promise<SessionInfo> {
    return this.sendRequest("init_session", { user }) as unknown as Promise<SessionInfo>;
  }

  async listQubits(): Promise<{ qubits: Record<string, { f10: number; fread: number }> }> {
    return this.sendRequest("list_qubits") as Promise<{ qubits: Record<string, { f10: number; fread: number }> }>;
  }

  async runExperiment(
    qubit: string,
    experiment: ExperimentType,
    params?: Record<string, unknown>
  ): Promise<{ status: string; data?: number[][]; message?: string }> {
    return this.sendRequest("run_experiment", { qubit_name: qubit, experiment, params }) as Promise<{
      status: string;
      data?: number[][];
      message?: string;
    }>;
  }

  async measureMetrics(qubit: string): Promise<{ status: string; metrics?: QubitMetrics; message?: string }> {
    return this.sendRequest("measure_metrics", { qubit_name: qubit }) as Promise<{
      status: string;
      metrics?: QubitMetrics;
      message?: string;
    }>;
  }

  async runOptimization(
    qubit: string,
    parameter: string,
    strategy: OptimizationStrategy,
    initialValue: number,
    valueRange: [number, number],
    stepSize: number
  ): Promise<{
    status: string;
    bestValue?: number;
    bestMetrics?: QubitMetrics;
    optimizationTime?: number;
    testCount?: number;
    message?: string;
  }> {
    return this.sendRequest("run_optimization", {
      qubit_name: qubit,
      parameter_name: parameter,
      strategy,
      initial_value: initialValue,
      value_range: valueRange,
      step_size: stepSize,
    }) as Promise<{
      status: string;
      bestValue?: number;
      bestMetrics?: QubitMetrics;
      optimizationTime?: number;
      testCount?: number;
      message?: string;
    }>;
  }

  async close(): Promise<{ status: string }> {
    return this.sendRequest("close") as Promise<{ status: string }>;
  }
}