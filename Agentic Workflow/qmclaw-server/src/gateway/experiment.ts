/**
 * Experiment Gateway - Handles experiment execution via workers
 */

import { PythonBridge } from "../worker/python-bridge";
import { ProcessPool } from "../worker/process-pool";
import { TaskQueue } from "../queue/task-queue";
import {
  ExperimentJob,
  ExperimentType,
  ExperimentResult,
  JobStatus,
} from "../types";

export class ExperimentGateway {
  private pool: ProcessPool;
  private queue: TaskQueue;
  private bridge: PythonBridge | null = null;

  constructor(pool: ProcessPool, queue: TaskQueue) {
    this.pool = pool;
    this.queue = queue;
  }

  /**
   * Set the active bridge (from LabradGateway)
   */
  setBridge(bridge: PythonBridge): void {
    this.bridge = bridge;
  }

  /**
   * Run an experiment
   */
  async runExperiment(
    qubit: string,
    experiment: ExperimentType,
    params: Record<string, unknown> = {}
  ): Promise<ExperimentJob> {
    if (!this.bridge) {
      throw new Error("LabRAD session not initialized");
    }

    // Create job
    const job = this.queue.enqueueExperiment(qubit, experiment, params);

    try {
      // Run the experiment
      const result = await this.bridge.runExperiment(qubit, experiment, params);

      if (result.status === "success" && result.data) {
        const experimentResult: ExperimentResult = {
          data: result.data,
          timestamp: Date.now(),
        };
        this.queue.completeJob(job.id, experimentResult);
      } else {
        this.queue.failJob(job.id, result.message || "Unknown error");
      }
    } catch (err) {
      this.queue.failJob(job.id, err instanceof Error ? err.message : String(err));
    }

    return job as ExperimentJob;
  }

  /**
   * Get experiment result
   */
  getResult(jobId: string): ExperimentResult | undefined {
    const job = this.queue.getJob(jobId);
    if (job && job.type === "experiment") {
      return (job as ExperimentJob).result;
    }
    return undefined;
  }

  /**
   * Subscribe to experiment updates
   */
  subscribe(jobId: string, callback: (job: ExperimentJob) => void): () => void {
    return this.queue.subscribe(jobId, (job) => {
      callback(job as ExperimentJob);
    });
  }
}