/**
 * Job Types - Task queue job definitions
 */

import {
  JobStatus,
  JobType,
  BaseJob,
  ExperimentJob,
  OptimizationJob,
  BatchJob,
  ExperimentType,
  OptimizationStrategy,
  ExperimentResult,
  OptimizationResult,
  QubitMetrics,
} from "../types";

/**
 * Generate a unique job ID
 */
export function generateJobId(): string {
  return `job-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Create an experiment job
 */
export function createExperimentJob(
  qubit: string,
  experiment: ExperimentType,
  params: Record<string, unknown> = {}
): ExperimentJob {
  const now = Date.now();
  return {
    id: generateJobId(),
    type: "experiment",
    status: JobStatus.PENDING,
    createdAt: now,
    updatedAt: now,
    qubit,
    experiment,
    params,
  };
}

/**
 * Create an optimization job
 */
export function createOptimizationJob(
  qubit: string,
  parameter: string,
  strategy: OptimizationStrategy,
  initialValue: number,
  valueRange: [number, number],
  stepSize: number
): OptimizationJob {
  const now = Date.now();
  return {
    id: generateJobId(),
    type: "optimization",
    status: JobStatus.PENDING,
    createdAt: now,
    updatedAt: now,
    qubit,
    parameter,
    strategy,
    initialValue,
    valueRange,
    stepSize,
    iterations: [],
  };
}

/**
 * Create a batch job
 */
export function createBatchJob(qubit: string, jobIds: string[]): BatchJob {
  const now = Date.now();
  return {
    id: generateJobId(),
    type: "batch",
    status: JobStatus.PENDING,
    createdAt: now,
    updatedAt: now,
    qubit,
    jobs: jobIds,
    results: {},
  };
}

/**
 * Update job status
 */
export function updateJobStatus<T extends BaseJob>(
  job: T,
  status: JobStatus,
  additionalUpdates: Partial<T> = {}
): T {
  return {
    ...job,
    status,
    updatedAt: Date.now(),
    ...additionalUpdates,
  };
}

/**
 * Convert job to JSON for storage/transmission
 */
export function jobToJSON(job: BaseJob): string {
  return JSON.stringify(job);
}

/**
 * Parse job from JSON
 */
export function jobFromJSON(json: string): BaseJob {
  const parsed = JSON.parse(json);
  return parsed as BaseJob;
}
