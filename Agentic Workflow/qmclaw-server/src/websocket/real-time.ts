/**
 * WebSocket Server - Real-time data push to clients
 */

import { Server as HttpServer } from "http";
import { Server, Socket } from "socket.io";
import { TaskQueue } from "../queue/task-queue";
import { BaseJob, WSServerMessage } from "../types";

type ClientMessage = {
  type: "subscribe" | "unsubscribe";
  channel: string;
  jobId?: string;
};

export class RealtimeServer {
  private io: Server;
  private queue: TaskQueue;
  private subscriptions: Map<string, Set<string>> = new Map(); // channel -> socket IDs

  constructor(httpServer: HttpServer, queue: TaskQueue) {
    this.queue = queue;
    this.io = new Server(httpServer, {
      cors: {
        origin: "*",
        methods: ["GET", "POST"],
      },
    });

    this.setupHandlers();

    // Subscribe to all job updates
    this.queue.subscribeAll((job) => {
      this.broadcastJobUpdate(job);
    });
  }

  private setupHandlers(): void {
    this.io.on("connection", (socket: Socket) => {
      console.log(`[WebSocket] Client connected: ${socket.id}`);

      // Handle subscription requests
      socket.on("message", (data: string) => {
        try {
          const msg: ClientMessage = JSON.parse(data);
          this.handleMessage(socket, msg);
        } catch (err) {
          console.error("[WebSocket] Failed to parse message:", err);
        }
      });

      socket.on("disconnect", () => {
        console.log(`[WebSocket] Client disconnected: ${socket.id}`);
        this.removeSocketFromAllSubscriptions(socket.id);
      });
    });
  }

  private handleMessage(socket: Socket, msg: ClientMessage): void {
    switch (msg.type) {
      case "subscribe":
        this.subscribe(socket, msg.channel, msg.jobId);
        break;
      case "unsubscribe":
        this.unsubscribe(socket, msg.channel, msg.jobId);
        break;
    }
  }

  private subscribe(socket: Socket, channel: string, jobId?: string): void {
    const subscriptionKey = jobId ? `${channel}:${jobId}` : channel;

    if (!this.subscriptions.has(subscriptionKey)) {
      this.subscriptions.set(subscriptionKey, new Set());
    }
    this.subscriptions.get(subscriptionKey)!.add(socket.id);

    socket.join(subscriptionKey);
    console.log(`[WebSocket] ${socket.id} subscribed to ${subscriptionKey}`);
  }

  private unsubscribe(socket: Socket, channel: string, jobId?: string): void {
    const subscriptionKey = jobId ? `${channel}:${jobId}` : channel;

    const subs = this.subscriptions.get(subscriptionKey);
    if (subs) {
      subs.delete(socket.id);
      if (subs.size === 0) {
        this.subscriptions.delete(subscriptionKey);
      }
    }

    socket.leave(subscriptionKey);
    console.log(`[WebSocket] ${socket.id} unsubscribed from ${subscriptionKey}`);
  }

  private removeSocketFromAllSubscriptions(socketId: string): void {
    for (const [key, subs] of this.subscriptions.entries()) {
      subs.delete(socketId);
      if (subs.size === 0) {
        this.subscriptions.delete(key);
      }
    }
  }

  private broadcastJobUpdate(job: BaseJob): void {
    let eventType: WSServerMessage["type"];

    switch (job.status) {
      case "running":
        eventType = "job:progress";
        break;
      case "completed":
        eventType = "job:complete";
        break;
      case "failed":
        eventType = "job:failed";
        break;
      default:
        return;
    }

    const message: WSServerMessage = {
      type: eventType,
      jobId: job.id,
      data: job,
    };

    // Broadcast to job-specific channel
    this.io.to(`${job.type}:${job.id}`).emit("message", JSON.stringify(message));

    // Also broadcast to job type channel
    this.io.to(job.type).emit("message", JSON.stringify(message));
  }

  /**
   * Push data update for an experiment
   */
  pushDataUpdate(jobId: string, data: unknown): void {
    const message: WSServerMessage = {
      type: "data:update",
      jobId,
      data,
    };
    this.io.to(`experiment:${jobId}`).emit("message", JSON.stringify(message));
  }

  /**
   * Push metrics update for an optimization
   */
  pushMetricsUpdate(jobId: string, metrics: unknown): void {
    const message: WSServerMessage = {
      type: "metrics:update",
      jobId,
      data: metrics,
    };
    this.io.to(`optimization:${jobId}`).emit("message", JSON.stringify(message));
  }

  /**
   * Get connected client count
   */
  getClientCount(): number {
    return this.io.engine.clientsCount;
  }
}