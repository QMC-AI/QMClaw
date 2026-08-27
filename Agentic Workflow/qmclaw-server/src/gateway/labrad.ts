/**
 * LabRAD Gateway - Manages LabRAD session and qubit connections
 */

import { PythonBridge } from "../worker/python-bridge";
import { ProcessPool } from "../worker/process-pool";
import { SessionInfo } from "../types";

export class LabradGateway {
  private pool: ProcessPool;
  private session: SessionInfo | null = null;
  private bridge: PythonBridge | null = null;

  constructor(pool: ProcessPool) {
    this.pool = pool;
  }

  /**
   * Initialize LabRAD session
   */
  async initSession(user: string): Promise<SessionInfo> {
    // Get a worker bridge
    const worker = await this.pool.acquireWorker();
    if (!worker) {
      throw new Error("No workers available");
    }

    try {
      this.bridge = worker.bridge;

      // Initialize session
      const result = await this.bridge.initSession(user);
      this.session = result as SessionInfo;

      return this.session;
    } finally {
      // Release worker back to pool (but keep it assigned)
      this.pool.releaseWorker(worker.workerId);
    }
  }

  /**
   * Get current session info
   */
  getSession(): SessionInfo | null {
    return this.session;
  }

  /**
   * Check if session is connected
   */
  isConnected(): boolean {
    return this.session?.status === "connected";
  }

  /**
   * Get available qubits
   */
  async listQubits(): Promise<Record<string, { f10: number; fread: number }>> {
    if (!this.bridge) {
      throw new Error("Session not initialized");
    }

    const result = await this.bridge.listQubits();
    return (result as any).qubits || {};
  }

  /**
   * Get the bridge for direct access
   */
  getBridge(): PythonBridge | null {
    return this.bridge;
  }

  /**
   * Close the session
   */
  async close(): Promise<void> {
    if (this.bridge) {
      await this.bridge.close();
      this.bridge = null;
    }
    this.session = null;
  }
}