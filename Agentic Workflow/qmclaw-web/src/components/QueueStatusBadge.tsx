"use client";

/**
 * QueueStatusBadge — 顶栏的「硬件通道」状态徽标
 *
 * 数据来自 task_queue 的 /lane（经 Express :3002 /api/tasks/lane 代理）。
 * 语义：执行层是「单个实验」，调度层是「qwf 租约」——
 *   active_lease  = 当前占着通道的流程（整段独占）
 *   running_task  = 此刻真正在跑的那一个实验
 *   pending_leases= 排队等通道的流程，按 (-priority, created_at) 排序
 *
 * 只读展示 + 两个危险动作（取消排队/强制释放），都要二次确认。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, LaneSnapshot, LaneLease } from "../lib/api";

const POLL_MS = 1500;

function fmtAge(sec?: number | null): string {
  if (sec == null || !isFinite(sec) || sec < 0) return "—";
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m${s % 60}s`;
  return `${Math.floor(m / 60)}h${m % 60}m`;
}

function leaseName(l: LaneLease): string {
  return l.metadata?.workflowName || l.metadata?.workflowId || l.workflow_id || "(未命名)";
}

function Dot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: color,
        boxShadow: pulse ? `0 0 0 3px ${color}33` : undefined,
        animation: pulse ? "qsb-pulse 1.6s ease-in-out infinite" : undefined,
        flexShrink: 0,
      }}
    />
  );
}

export default function QueueStatusBadge() {
  const [lane, setLane] = useState<LaneSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [busyAction, setBusyAction] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getLane();
      if (data && (data as unknown as { error?: string }).error) {
        setError(String((data as unknown as { error?: string }).error));
      } else {
        setLane(data);
        setError(null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "lane fetch failed");
    }
    setNow(Date.now() / 1000);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  // 点外面收起浮层
  useEffect(() => {
    if (!open) return;
    const onDown = (ev: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(ev.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const active = lane?.active_lease ?? null;
  const pending = lane?.pending_leases ?? [];
  const running = lane?.running_task ?? null;
  const queuedExp = lane?.queue_size ?? 0;
  const pendingCount = pending.length;

  // ── 徽标文字与颜色 ──────────────────────────────────────────────────────
  let color = "#22c55e";
  let label = "通道空闲";
  let pulse = false;
  if (error) {
    color = "#f87171";
    label = "队列不可达";
  } else if (running) {
    color = "#38bdf8";
    pulse = true;
    const who = lane?.running_label || (active ? leaseName(active) : "单实验");
    label = `运行中 · ${who}`;
  } else if (pendingCount > 0) {
    color = "#f59e0b";
    label = `排队 ${pendingCount}`;
  }

  // ── 危险动作 ────────────────────────────────────────────────────────────
  const cancelFlow = async (l: LaneLease) => {
    if (!window.confirm(
      `取消流程「${leaseName(l)}」？\n\n` +
      `· 已在下单排队：会撤销排队并交还租约\n` +
      `· 正在运行：会在当前节点结束后停止，之后的节点全部跳过`,
    )) return;
    setBusyAction(true);
    try {
      const r = await api.cancelWorkflow(l.workflow_id);
      if (r && (r.error || r.ok === false || r.success === false)) {
        window.alert(`取消失败：${r.error ?? JSON.stringify(r)}`);
      }
      await refresh();
    } finally {
      setBusyAction(false);
    }
  };

  const forceRelease = async (l: LaneLease) => {
    if (!window.confirm(
      `强制释放租约 ${l.lease_id}？\n\n` +
      `只有在确认持有者已经死了/卡住时才这么做。\n` +
      `持有者若还活着，它下一次续期会失败、后续子实验会被挡。`,
    )) return;
    setBusyAction(true);
    try {
      await api.releaseLease(l.lease_id);
      await refresh();
    } finally {
      setBusyAction(false);
    }
  };

  // ── 渲染 ────────────────────────────────────────────────────────────────
  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <style>{`@keyframes qsb-pulse{0%,100%{opacity:1}50%{opacity:.35}}`}</style>

      <button
        onClick={() => setOpen((v) => !v)}
        title="硬件通道 / 任务队列"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.4rem",
          padding: "0.25rem 0.6rem",
          background: "#1e293b",
          border: `1px solid ${open ? "#38bdf8" : "#334155"}`,
          borderRadius: "0.375rem",
          color: "#cbd5e1",
          cursor: "pointer",
          fontSize: "0.7rem",
          fontWeight: 600,
          maxWidth: 320,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
      >
        <Dot color={color} pulse={pulse} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
        {pendingCount > 0 && running ? (
          <span style={{ color: "#f59e0b" }}>+{pendingCount}</span>
        ) : null}
        <span style={{ color: "#64748b", fontSize: "0.6rem" }}>{open ? "▴" : "▾"}</span>
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            width: 440,
            maxHeight: "70vh",
            overflowY: "auto",
            background: "#0b1220",
            border: "1px solid #1e293b",
            borderRadius: "0.5rem",
            boxShadow: "0 12px 32px rgba(0,0,0,.55)",
            zIndex: 2000,
            padding: "0.75rem",
            fontSize: "0.72rem",
            color: "#cbd5e1",
          }}
        >
          {/* 标题行 */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
            <strong style={{ color: "#e2e8f0", fontSize: "0.78rem" }}>硬件通道</strong>
            <span style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
              <span style={{ color: "#64748b", fontSize: "0.62rem" }}>
                {error ? "连接失败" : `已更新 ${new Date(now * 1000).toLocaleTimeString()}`}
              </span>
              <button
                onClick={refresh}
                style={{ background: "transparent", border: "1px solid #334155", borderRadius: 4, color: "#38bdf8", cursor: "pointer", fontSize: "0.62rem", padding: "0 0.3rem" }}
              >
                ⟳
              </button>
            </span>
          </div>

          {error && (
            <div style={{ background: "#2a1113", border: "1px solid #7f1d1d", borderRadius: 4, padding: "0.4rem 0.5rem", color: "#fca5a5", marginBottom: "0.5rem" }}>
              task_queue 连不上：{error}
              <div style={{ color: "#94a3b8", marginTop: 2 }}>
                检查 :3009 是否在跑（<code>GET :3009/health</code>）。
                <br />
                Express 的 <code>/api/tasks/lane</code> 代理缺失时会自动直连，所以这里只反映服务本身的状态。
              </div>
            </div>
          )}

          {/* 当前通道 */}
          <Section title="当前通道">
            {active ? (
              <div style={{ border: "1px solid #1e3a5f", background: "#0d1b2e", borderRadius: 4, padding: "0.45rem 0.55rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
                  <span style={{ color: "#38bdf8", fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {leaseName(active)}
                  </span>
                  <span style={{ color: "#64748b", fontSize: "0.62rem", flexShrink: 0 }}>{active.lease_id}</span>
                </div>
                <div style={{ color: "#94a3b8", marginTop: 2 }}>
                  已占用 {fmtAge(now - (active.granted_at ?? active.created_at))}
                  {active.remaining_s != null ? ` · 租约还剩 ${fmtAge(active.remaining_s)}` : ""}
                  {active.yield_between_nodes ? " · 允许节点间让路" : " · 整段独占"}
                </div>
                {active.metadata?.qubits?.length ? (
                  <div style={{ color: "#94a3b8" }}>比特：{active.metadata.qubits.join(", ")}</div>
                ) : null}
                <div style={{ color: "#64748b", fontSize: "0.62rem", marginTop: 2, wordBreak: "break-all" }}>
                  workflow {active.workflow_id}
                </div>
                <div style={{ display: "flex", gap: "0.35rem", marginTop: "0.4rem" }}>
                  <DangerBtn disabled={busyAction} onClick={() => cancelFlow(active)}>取消该流程</DangerBtn>
                  <GhostBtn disabled={busyAction} onClick={() => forceRelease(active)}>强制释放租约</GhostBtn>
                </div>
              </div>
            ) : (
              <div style={{ color: "#64748b" }}>无流程持有通道（单个实验仍可能正在执行）</div>
            )}

            <div style={{ marginTop: "0.35rem", color: running ? "#38bdf8" : "#64748b" }}>
              正在执行的实验：
              {running ? (
                <span>
                  <code style={{ color: "#e2e8f0" }}>{lane?.running_label || running}</code>
                  {lane?.running_label ? (
                    <span style={{ color: "#475569", fontSize: "0.62rem" }}> · {running}</span>
                  ) : null}
                </span>
              ) : "无"}
            </div>
          </Section>

          {/* 排队 */}
          <Section title={`排队中的流程（${pendingCount}）`}>
            {pending.length === 0 ? (
              <div style={{ color: "#64748b" }}>没有流程在排队</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
                {pending.map((l, i) => (
                  <div
                    key={l.lease_id}
                    style={{ display: "flex", alignItems: "center", gap: "0.45rem", border: "1px solid #1e293b", borderRadius: 4, padding: "0.35rem 0.45rem" }}
                  >
                    <span style={{ color: "#f59e0b", fontWeight: 700, width: 18, textAlign: "center", flexShrink: 0 }}>{i + 1}</span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ color: "#e2e8f0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {leaseName(l)}
                      </div>
                      <div style={{ color: "#64748b", fontSize: "0.62rem" }}>
                        已等 {fmtAge(now - l.created_at)} · 优先级 {l.priority} · {l.lease_id}
                      </div>
                    </span>
                    <DangerBtn disabled={busyAction} onClick={() => cancelFlow(l)}>取消</DangerBtn>
                  </div>
                ))}
              </div>
            )}
          </Section>

          {/* 统计 */}
          <Section title="其它">
            <div style={{ color: "#94a3b8" }}>
              排队中的单实验：<strong style={{ color: queuedExp ? "#f59e0b" : "#64748b" }}>{queuedExp}</strong>
              {" · "}已完成：{lane?.completed ?? 0}
              {" · "}过期租约：{lane?.expired_leases ?? 0}
            </div>
            <div style={{ color: "#64748b", fontSize: "0.62rem", marginTop: 2 }}>
              执行层限单个实验（并发 1）；调度层以 qwf 租约整段独占。
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "0.6rem" }}>
      <div style={{ color: "#64748b", fontSize: "0.62rem", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: "0.25rem" }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function DangerBtn({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: "#2a1113",
        border: "1px solid #7f1d1d",
        color: "#fca5a5",
        borderRadius: 4,
        padding: "0.12rem 0.4rem",
        fontSize: "0.62rem",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  );
}

function GhostBtn({ children, onClick, disabled }: { children: React.ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: "transparent",
        border: "1px solid #334155",
        color: "#94a3b8",
        borderRadius: 4,
        padding: "0.12rem 0.4rem",
        fontSize: "0.62rem",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        flexShrink: 0,
      }}
    >
      {children}
    </button>
  );
}
