/**
 * Optimization Gateway - Handles optimization tasks via workers
 */

import { PythonBridge } from "../worker/python-bridge";
import { ProcessPool } from "../worker/process-pool";
import { TaskQueue } from "../queue/task-queue";
import {
  OptimizationJob,
  OptimizationStrategy,
  OptimizationResult,
  OptimizationIteration,
  JobStatus,
} from "../types";

export class OptimizationGateway {
  private queue: TaskQueue;
  private bridge: PythonBridge | null = null;

  constructor(_pool: ProcessPool, queue: TaskQueue) {
    this.queue = queue;
  }

  /**
   * Set the active bridge (from LabradGateway)
   */
  setBridge(bridge: PythonBridge): void {
    this.bridge = bridge;
  }

  /**
   * Start an optimization task
   */
  async startOptimization(
    qubit: string,
    parameter: string,
    strategy: OptimizationStrategy,
    initialValue: number,
    valueRange: [number, number],
    stepSize: number
  ): Promise<OptimizationJob> {
    if (!this.bridge) {
      throw new Error("LabRAD session not initialized");
    }

    // Create optimization job
    const job = this.queue.enqueueOptimization(
      qubit,
      parameter,
      strategy,
      initialValue,
      valueRange,
      stepSize
    );

    // Run optimization asynchronously
    this.runOptimization(job as OptimizationJob).catch((err) => {
      this.queue.failJob(job.id, err instanceof Error ? err.message : String(err));
    });

    return job as OptimizationJob;
  }

  /**
   * Internal optimization runner
   */
  private async runOptimization(job: OptimizationJob): Promise<void> {
    if (!this.bridge) return;

    try {
      const result = await this.bridge.runOptimization(
        job.qubit,
        job.parameter,
        job.strategy,
        job.initialValue,
        job.valueRange,
        job.stepSize
      );

      if (result.status === "success") {
        const optResult: OptimizationResult = {
          bestValue: result.bestValue!,
          bestMetrics: result.bestMetrics!,
          optimizationTime: result.optimizationTime!,
          testCount: result.testCount!,
        };
        this.queue.completeJob(job.id, optResult);
      } else {
        this.queue.failJob(job.id, result.message || "Optimization failed");
      }
    } catch (err) {
      this.queue.failJob(job.id, err instanceof Error ? err.message : String(err));
    }
  }

  /**
   * Get optimization result
   */
  getResult(jobId: string): OptimizationResult | undefined {
    const job = this.queue.getJob(jobId);
    if (job && job.type === "optimization") {
      return (job as OptimizationJob).result;
    }
    return undefined;
  }

  /**
   * Get optimization iterations
   */
  getIterations(jobId: string): OptimizationIteration[] {
    const job = this.queue.getJob(jobId);
    if (job && job.type === "optimization") {
      return (job as OptimizationJob).iterations || [];
    }
    return [];
  }

  /**
   * Cancel an optimization task
   */
  cancelOptimization(jobId: string): boolean {
    const job = this.queue.getJob(jobId);
    if (job && job.type === "optimization") {
      this.queue.cancelJob(jobId);
      return true;
    }
    return false;
  }

  /**
   * Subscribe to optimization updates
   */
  subscribe(jobId: string, callback: (job: OptimizationJob) => void): () => void {
    return this.queue.subscribe(jobId, (job) => {
      callback(job as OptimizationJob);
    });
  }
}