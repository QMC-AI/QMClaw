/**
 * Process Pool - Manages Python worker processes
 */

import { PythonBridge } from "./python-bridge";
import { WorkerStatus } from "../types";

export class ProcessPool {
  private workers: Map<string, PythonBridge> = new Map();
  private busyWorkers: Set<string> = new Set();
  private maxWorkers: number;
  private workerPath: string;

  constructor(maxWorkers: number = 2, workerPath?: string) {
    this.maxWorkers = maxWorkers;
    this.workerPath = workerPath || "";
  }

  /**
   * Initialize the process pool
   */
  async initialize(): Promise<void> {
    const promises: Promise<void>[] = [];

    for (let i = 0; i < this.maxWorkers; i++) {
      const workerId = `worker-${i}`;
      const bridge = new PythonBridge(this.workerPath);

      promises.push(
        bridge.start().then(() => {
          this.workers.set(workerId, bridge);
          console.log(`[ProcessPool] ${workerId} started`);
        }).catch((err) => {
          console.error(`[ProcessPool] ${workerId} failed to start:`, err);
        })
      );
    }

    await Promise.allSettled(promises);
  }

  /**
   * Get an available worker (least busy)
   */
  async acquireWorker(): Promise<{ workerId: string; bridge: PythonBridge } | null> {
    // Find idle workers
    const idleWorkers = Array.from(this.workers.entries()).filter(
      ([id]) => !this.busyWorkers.has(id)
    );

    if (idleWorkers.length > 0) {
      const [workerId, bridge] = idleWorkers[0];
      this.busyWorkers.add(workerId);
      return { workerId, bridge };
    }

    // Wait for a worker to become available
    return new Promise((resolve) => {
      const checkInterval = setInterval(() => {
        const available = Array.from(this.workers.entries()).filter(
          ([id]) => !this.busyWorkers.has(id)
        );
        if (available.length > 0) {
          clearInterval(checkInterval);
          const [workerId, bridge] = available[0];
          this.busyWorkers.add(workerId);
          resolve({ workerId, bridge });
        }
      }, 1000);

      // Timeout after 30 seconds
      setTimeout(() => {
        clearInterval(checkInterval);
        resolve(null);
      }, 30000);
    });
  }

  /**
   * Release a worker back to the pool
   */
  releaseWorker(workerId: string): void {
    this.busyWorkers.delete(workerId);
  }

  /**
   * Get status of all workers
   */
  getStatus(): WorkerStatus[] {
    const statuses: WorkerStatus[] = [];

    for (const [id, bridge] of this.workers.entries()) {
      statuses.push({
        id,
        status: this.busyWorkers.has(id) ? "busy" : "idle",
        lastHeartbeat: Date.now(),
      });
    }

    return statuses;
  }

  /**
   * Shutdown all workers
   */
  async shutdown(): Promise<void> {
    const promises: Promise<void>[] = [];

    for (const [id, bridge] of this.workers.entries()) {
      promises.push(
        bridge.stop().then(() => {
          console.log(`[ProcessPool] ${id} stopped`);
        })
      );
    }

    await Promise.allSettled(promises);
    this.workers.clear();
    this.busyWorkers.clear();
  }

  /**
   * Get worker count
   */
  get size(): number {
    return this.workers.size;
  }
}