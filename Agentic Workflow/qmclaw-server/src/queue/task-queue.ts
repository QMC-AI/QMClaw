/**
 * Task Queue - In-memory task queue for job management
 */

import {
  BaseJob,
  JobStatus,
  ExperimentJob,
  OptimizationJob,
  ExperimentResult,
  OptimizationResult,
  QubitMetrics,
} from "../types";
import {
  createExperimentJob,
  createOptimizationJob,
  updateJobStatus,
} from "./job-types";

type JobListener = (job: BaseJob) => void;

export class TaskQueue {
  private jobs: Map<string, BaseJob> = new Map();
  private pendingQueue: string[] = []; // Job IDs in order
  private listeners: Map<string, JobListener[]> = new Map();

  /**
   * Add an experiment job to the queue
   */
  enqueueExperiment(
    qubit: string,
    experiment: string,
    params: Record<string, unknown> = {}
  ): ExperimentJob {
    const job = createExperimentJob(qubit, experiment as any, params);
    this.addJob(job);
    return job;
  }

  /**
   * Add an optimization job to the queue
   */
  enqueueOptimization(
    qubit: string,
    parameter: string,
    strategy: string,
    initialValue: number,
    valueRange: [number, number],
    stepSize: number
  ): OptimizationJob {
    const job = createOptimizationJob(
      qubit,
      parameter,
      strategy as any,
      initialValue,
      valueRange,
      stepSize
    );
    this.addJob(job);
    return job;
  }

  /**
   * Add a job to the queue
   */
  private addJob(job: BaseJob): void {
    this.jobs.set(job.id, job);
    this.pendingQueue.push(job.id);
    this.emit(job.id, job);
  }

  /**
   * Get the next pending job
   */
  dequeue(): BaseJob | null {
    while (this.pendingQueue.length > 0) {
      const jobId = this.pendingQueue.shift()!;
      const job = this.jobs.get(jobId);

      if (job && job.status === JobStatus.PENDING) {
        return updateJobStatus(job, JobStatus.RUNNING);
      }
    }
    return null;
  }

  /**
   * Get a job by ID
   */
  getJob(jobId: string): BaseJob | undefined {
    return this.jobs.get(jobId);
  }

  /**
   * Update a job's status
   */
  updateJob(jobId: string, updates: Partial<BaseJob>): BaseJob | undefined {
    const job = this.jobs.get(jobId);
    if (!job) return undefined;

    const updated = { ...job, ...updates, updatedAt: Date.now() } as BaseJob;
    this.jobs.set(jobId, updated);
    this.emit(jobId, updated);
    return updated;
  }

  /**
   * Mark a job as completed
   */
  completeJob(jobId: string, result: ExperimentResult | OptimizationResult): BaseJob | undefined {
    const job = this.jobs.get(jobId);
    if (!job) return undefined;

    const updated = {
      ...job,
      status: JobStatus.COMPLETED,
      updatedAt: Date.now(),
      result,
    } as any;
    this.jobs.set(jobId, updated);
    this.emit(jobId, updated);
    return updated;
  }

  /**
   * Mark a job as failed
   */
  failJob(jobId: string, error: string): BaseJob | undefined {
    const job = this.jobs.get(jobId);
    if (!job) return undefined;

    const updated = {
      ...job,
      status: JobStatus.FAILED,
      updatedAt: Date.now(),
      error,
    } as any;
    this.jobs.set(jobId, updated);
    this.emit(jobId, updated);
    return updated;
  }

  /**
   * Cancel a job
   */
  cancelJob(jobId: string): BaseJob | undefined {
    const job = this.jobs.get(jobId);
    if (!job) return undefined;

    if (job.status === JobStatus.PENDING) {
      return this.updateJob(jobId, { status: JobStatus.CANCELLED });
    }
    return job;
  }

  /**
   * Get all jobs
   */
  getAllJobs(): BaseJob[] {
    return Array.from(this.jobs.values());
  }

  /**
   * Get jobs by status
   */
  getJobsByStatus(status: JobStatus): BaseJob[] {
    return this.getAllJobs().filter((job) => job.status === status);
  }

  /**
   * Get jobs for a specific qubit
   */
  getJobsForQubit(qubit: string): BaseJob[] {
    return this.getAllJobs().filter((job) => job.qubit === qubit);
  }

  /**
   * Subscribe to job updates
   */
  subscribe(jobId: string, listener: JobListener): () => void {
    if (!this.listeners.has(jobId)) {
      this.listeners.set(jobId, []);
    }
    this.listeners.get(jobId)!.push(listener);

    // Return unsubscribe function
    return () => {
      const jobListeners = this.listeners.get(jobId);
      if (jobListeners) {
        const index = jobListeners.indexOf(listener);
        if (index !== -1) {
          jobListeners.splice(index, 1);
        }
      }
    };
  }

  /**
   * Emit job update to listeners
   */
  private emit(jobId: string, job: BaseJob): void {
    const jobListeners = this.listeners.get(jobId);
    if (jobListeners) {
      for (const listener of jobListeners) {
        try {
          listener(job);
        } catch (err) {
          console.error(`[TaskQueue] Listener error for job ${jobId}:`, err);
        }
      }
    }

    // Also emit to global listeners
    const globalListeners = this.listeners.get("*");
    if (globalListeners) {
      for (const listener of globalListeners) {
        try {
          listener(job);
        } catch (err) {
          console.error(`[TaskQueue] Global listener error:`, err);
        }
      }
    }
  }

  /**
   * Subscribe to all job updates
   */
  subscribeAll(listener: JobListener): () => void {
    return this.subscribe("*", listener);
  }

  /**
   * Clear completed/failed jobs older than a threshold
   */
  cleanup(maxAgeMs: number = 3600000): number {
    const threshold = Date.now() - maxAgeMs;
    let cleared = 0;

    for (const [jobId, job] of this.jobs.entries()) {
      if (
        (job.status === JobStatus.COMPLETED ||
          job.status === JobStatus.FAILED ||
          job.status === JobStatus.CANCELLED) &&
        job.updatedAt < threshold
      ) {
        this.jobs.delete(jobId);
        cleared++;
      }
    }

    return cleared;
  }

  /**
   * Get queue statistics
   */
  getStats(): {
    total: number;
    pending: number;
    running: number;
    completed: number;
    failed: number;
    cancelled: number;
  } {
    const jobs = this.getAllJobs();
    return {
      total: jobs.length,
      pending: jobs.filter((j) => j.status === JobStatus.PENDING).length,
      running: jobs.filter((j) => j.status === JobStatus.RUNNING).length,
      completed: jobs.filter((j) => j.status === JobStatus.COMPLETED).length,
      failed: jobs.filter((j) => j.status === JobStatus.FAILED).length,
      cancelled: jobs.filter((j) => j.status === JobStatus.CANCELLED).length,
    };
  }
}

// Singleton instance
export const taskQueue = new TaskQueue();