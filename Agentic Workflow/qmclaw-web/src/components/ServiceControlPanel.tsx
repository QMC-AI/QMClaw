"use client";

/**
 * Service Control Panel
 * 服务控制面板 - 启动/停止测控服务
 */

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface ServiceInfo {
  port: number;
  running: boolean;
  pid: number | null;
  connected?: boolean;
  initialized?: boolean;
}

interface ServerStatus {
  labrad: ServiceInfo;
  ray: { initialized: boolean };
  services: Record<string, ServiceInfo>;
  overall: string;
  issues: string[];
}

export default function ServiceControlPanel() {
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getServerStatus() as ServerStatus;
      setStatus(data);
    } catch (e: any) {
      setError("获取状态失败: " + e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    // Refresh every 10 seconds
    const interval = setInterval(fetchStatus, 10000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const handleStart = async () => {
    setStarting(true);
    setError(null);
    try {
      const result = await api.startServices();
      if (result.success) {
        addLog("服务启动成功");
        fetchStatus();
      } else {
        addLog("服务启动完成，请检查日志");
      }
    } catch (e: any) {
      setError("启动失败: " + e.message);
    } finally {
      setStarting(false);
    }
  };

  const addLog = (msg: string) => {
    console.log("[ServiceControl]", msg);
  };

  const getStatusColor = (running: boolean | undefined) => {
    if (running === undefined) return "#64748b";
    return running ? "#22c55e" : "#ef4444";
  };

  const getStatusIcon = (running: boolean | undefined) => {
    if (running === undefined) return "⏳";
    return running ? "✅" : "❌";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h2 style={{ margin: 0, fontSize: "1.25rem", fontWeight: 600, color: "#e2e8f0" }}>
          🔧 服务控制
        </h2>
        <button
          onClick={fetchStatus}
          disabled={loading}
          style={{
            padding: "0.5rem 1rem",
            borderRadius: "0.375rem",
            border: "1px solid #334155",
            background: loading ? "#1e293b" : "#1e3a5f",
            color: "#38bdf8",
            cursor: loading ? "not-allowed" : "pointer",
            fontSize: "0.875rem",
          }}
        >
          {loading ? "刷新中..." : "🔄 刷新状态"}
        </button>
      </div>

      {/* Error message */}
      {error && (
        <div style={{
          padding: "0.75rem",
          background: "#451a1a",
          border: "1px solid #ef4444",
          borderRadius: "0.375rem",
          color: "#f87171",
          fontSize: "0.875rem",
        }}>
          {error}
        </div>
      )}

      {/* Overall status */}
      {status && (
        <div style={{
          padding: "0.75rem",
          background: status.overall === "ready" ? "#052e16" : status.overall === "partial" ? "#422006" : "#450a0a",
          border: `1px solid ${status.overall === "ready" ? "#22c55e" : status.overall === "partial" ? "#f59e0b" : "#ef4444"}`,
          borderRadius: "0.5rem",
        }}>
          <div style={{ fontSize: "0.875rem", fontWeight: 600, marginBottom: "0.5rem" }}>
            总体状态: {status.overall === "ready" ? "✅ 就绪" : status.overall === "partial" ? "⚠️ 部分就绪" : "❌ 未就绪"}
          </div>
          {status.issues.length > 0 && (
            <div style={{ fontSize: "0.75rem", color: "#f87171" }}>
              问题: {status.issues.join(", ")}
            </div>
          )}
        </div>
      )}

      {/* Service cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "1rem" }}>
        {/* LabRAD Server */}
        <ServiceCard
          name="LabRAD Server"
          icon="🔌"
          port={7682}
          status={status?.labrad?.running}
          pid={status?.labrad?.pid}
          description="量子测量主服务器"
          warning={!status?.labrad?.running ? "需要手动启动" : undefined}
        />

        {/* LabRAD Connection */}
        <ServiceCard
          name="LabRAD 连接"
          icon="🔗"
          port={7682}
          status={status?.labrad?.connected}
          description="Python 到 LabRAD 的连接"
          warning={status?.labrad?.running && !status?.labrad?.connected ? "连接已断开" : undefined}
        />

        {/* Ray */}
        <ServiceCard
          name="Ray"
          icon="⚡"
          port={6479}
          status={status?.ray?.initialized}
          description="分布式计算框架"
          warning={status?.ray?.initialized === false ? "未初始化" : undefined}
        />

        {/* Express Backend */}
        <ServiceCard
          name="Express 后端"
          icon="🖥️"
          port={3002}
          status={status?.services?.express?.running}
          pid={status?.services?.express?.pid}
          description="QmClaw API 服务器"
        />

        {/* Web Frontend */}
        <ServiceCard
          name="Next.js 前端"
          icon="🌐"
          port={3001}
          status={status?.services?.web?.running}
          pid={status?.services?.web?.pid}
          description="QmClaw Web 界面"
        />
      </div>

      {/* Start button */}
      <div style={{
        padding: "1rem",
        background: "#1e293b",
        borderRadius: "0.5rem",
        border: "1px solid #334155",
      }}>
        <div style={{ fontSize: "0.875rem", fontWeight: 600, marginBottom: "0.75rem", color: "#94a3b8" }}>
          启动测控服务
        </div>

        <div style={{ fontSize: "0.8rem", color: "#64748b", marginBottom: "1rem", lineHeight: 1.6 }}>
          点击下方按钮启动 Ray、设备管理器、微波源管理器等测控服务。
          <br />
          <strong style={{ color: "#f59e0b" }}>注意：</strong> LabRAD Server 需要手动启动（见下方说明）。
        </div>

        <button
          onClick={handleStart}
          disabled={starting}
          style={{
            padding: "0.75rem 2rem",
            borderRadius: "0.5rem",
            border: "none",
            background: starting ? "#334155" : "#22c55e",
            color: starting ? "#64748b" : "#fff",
            fontWeight: 600,
            cursor: starting ? "not-allowed" : "pointer",
            fontSize: "1rem",
          }}
        >
          {starting ? "⏳ 启动中..." : "🚀 启动服务"}
        </button>
      </div>

      {/* LabRAD Manual Start Guide */}
      <div style={{
        padding: "1rem",
        background: "#422006",
        borderRadius: "0.5rem",
        border: "1px solid #f59e0b",
      }}>
        <div style={{ fontSize: "0.875rem", fontWeight: 600, marginBottom: "0.75rem", color: "#fbbf24" }}>
          ⚠️ LabRAD Server 启动说明
        </div>
        <div style={{ fontSize: "0.8rem", color: "#fcd34d", lineHeight: 1.8 }}>
          <p style={{ margin: "0 0 0.5rem 0" }}>LabRAD 是 Java 服务器，无法通过按钮自动启动。请手动操作：</p>
          <ol style={{ margin: 0, paddingLeft: "1.5rem" }}>
            <li>打开一个新的终端</li>
            <li>运行以下命令：
              <code style={{
                display: "block",
                margin: "0.5rem 0",
                padding: "0.5rem",
                background: "#1e293b",
                borderRadius: "0.25rem",
                fontFamily: "monospace",
                fontSize: "0.75rem",
              }}>
                python -c "from lqcs.servers_control import run_server_control; run_server_control()"
              </code>
            </li>
            <li>在弹出的 PyQt 窗口中依次点击：
              <ul style={{ margin: "0.25rem 0", paddingLeft: "1rem" }}>
                <li><strong>labrad</strong> - LabRAD 管理器</li>
                <li><strong>datas</strong> - 数据存储服务</li>
                <li><strong>grapher</strong> - 绘图服务</li>
                <li><strong>registry editor</strong> - 参数编辑器</li>
                <li><strong>ray</strong> - 分布式服务框架</li>
                <li><strong>device manager</strong> - 板卡服务</li>
                <li><strong>uwave manager</strong> - 微波源服务</li>
              </ul>
            </li>
            <li>点击完成后，刷新本页查看状态</li>
          </ol>
        </div>
      </div>
    </div>
  );
}

// Service card component
function ServiceCard({
  name,
  icon,
  port,
  status,
  pid,
  description,
  warning,
}: {
  name: string;
  icon: string;
  port?: number;
  status?: boolean;
  pid?: number | null;
  description: string;
  warning?: string;
}) {
  const getStatusColor = (s: boolean | undefined) => {
    if (s === undefined) return "#64748b";
    return s ? "#22c55e" : "#ef4444";
  };

  return (
    <div style={{
      padding: "1rem",
      background: "#1e293b",
      borderRadius: "0.5rem",
      border: `1px solid ${warning ? "#f59e0b" : "#334155"}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.5rem" }}>
        <span style={{ fontSize: "1.25rem" }}>{icon}</span>
        <span style={{ fontSize: "0.9rem", fontWeight: 600, color: "#e2e8f0" }}>{name}</span>
        {status !== undefined && (
          <span style={{
            marginLeft: "auto",
            fontSize: "0.75rem",
            padding: "0.125rem 0.5rem",
            borderRadius: "1rem",
            background: getStatusColor(status),
            color: "#fff",
          }}>
            {status ? "运行中" : "已停止"}
          </span>
        )}
      </div>

      <div style={{ fontSize: "0.75rem", color: "#64748b", marginBottom: "0.5rem" }}>
        {description}
        {port && <span> (端口 {port})</span>}
      </div>

      {pid && (
        <div style={{ fontSize: "0.7rem", color: "#475569", fontFamily: "monospace" }}>
          PID: {pid}
        </div>
      )}

      {warning && (
        <div style={{
          marginTop: "0.5rem",
          fontSize: "0.7rem",
          color: "#f59e0b",
          padding: "0.25rem 0.5rem",
          background: "#422006",
          borderRadius: "0.25rem",
        }}>
          ⚠️ {warning}
        </div>
      )}
    </div>
  );
}
