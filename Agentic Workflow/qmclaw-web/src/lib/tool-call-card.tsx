"use client";

/**
 * Tool Call Card Component
 *
 * Displays tool calls in a collapsible card with status indication.
 * Based on hermes-hudui ToolCallCard implementation.
 */

import { useState } from 'react';

// QMClaw color scheme
const colors = {
  bgSurface: '#1e293b',
  bgDeep: '#0f172a',
  border: '#334155',
  text: '#e2e8f0',
  textDim: '#64748b',
  success: '#22c55e',
  warning: '#f59e0b',
  error: '#ef4444',
};

// ── Types ─────────────────────────────────────────────────────────────────────

export interface ToolCall {
  name: string;
  arguments: Record<string, unknown>;
  status?: 'pending' | 'running' | 'done' | 'error';
  result?: string;
}

// ── Status Badge ────────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status?: string }) {
  const getStatusStyle = () => {
    switch (status) {
      case 'running':
      case 'pending':
        return { color: colors.warning, label: '' };  // 运行中不显示文字
      case 'done':
        return { color: colors.success, label: '✓' };
      case 'error':
        return { color: colors.error, label: '✗' };
      default:
        return { color: colors.textDim, label: '' };
    }
  };

  const { color, label } = getStatusStyle();

  return (
    <span
      style={{
        color,
        fontSize: '0.65rem',
        fontWeight: 500,
      }}
    >
      {label}
    </span>
  );
}

// ── Arguments Display ───────────────────────────────────────────────────────────

function ArgumentsDisplay({ args }: { args: Record<string, unknown> }) {
  const [expanded, setExpanded] = useState(false);
  const argsStr = JSON.stringify(args, null, 2);
  const isLong = argsStr.length > 300;

  return (
    <div>
      <div
        style={{
          color: colors.textDim,
          fontSize: '0.7rem',
          marginBottom: '0.25rem',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span>arguments:</span>
        {isLong && (
          <button
            onClick={() => setExpanded(!expanded)}
            style={{
              background: 'transparent',
              border: 'none',
              color: colors.textDim,
              cursor: 'pointer',
              fontSize: '0.65rem',
              padding: '0',
            }}
          >
            {expanded ? '▲ collapse' : '▼ expand'}
          </button>
        )}
      </div>
      <pre
        style={{
          background: colors.bgDeep,
          padding: '0.5rem',
          borderRadius: '0.25rem',
          fontSize: '0.7rem',
          fontFamily: 'monospace',
          color: colors.text,
          overflowX: 'auto',
          maxHeight: expanded ? 'none' : '150px',
          margin: 0,
          border: `1px solid ${colors.border}`,
        }}
      >
        {argsStr}
      </pre>
    </div>
  );
}

// ── Result Display ─────────────────────────────────────────────────────────────

function ResultDisplay({ result, isError }: { result?: string; isError?: boolean }) {
  if (!result) return null;

  const [expanded, setExpanded] = useState(false);
  const isLong = result.length > 300;

  return (
    <div>
      <div
        style={{
          color: isError ? colors.error : colors.success,
          fontSize: '0.7rem',
          marginBottom: '0.25rem',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span>{isError ? 'error:' : 'result:'}</span>
        {isLong && (
          <button
            onClick={() => setExpanded(!expanded)}
            style={{
              background: 'transparent',
              border: 'none',
              color: colors.textDim,
              cursor: 'pointer',
              fontSize: '0.65rem',
              padding: '0',
            }}
          >
            {expanded ? '▲ collapse' : '▼ expand'}
          </button>
        )}
      </div>
      <pre
        style={{
          background: colors.bgDeep,
          padding: '0.5rem',
          borderRadius: '0.25rem',
          fontSize: '0.7rem',
          fontFamily: 'monospace',
          color: isError ? colors.error : colors.text,
          overflowX: 'auto',
          maxHeight: expanded ? 'none' : '150px',
          margin: 0,
          border: `1px solid ${colors.border}`,
        }}
      >
        {result}
      </pre>
    </div>
  );
}

// ── ToolCallCard Component ──────────────────────────────────────────────────────

interface ToolCallCardProps {
  toolCall: ToolCall;
  index: number;
}

export default function ToolCallCard({ toolCall, index }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);

  const { name, arguments: args = {}, status, result } = toolCall;

  const getBorderColor = () => {
    switch (status) {
      case 'running':
      case 'pending':
        return colors.warning;
      case 'done':
        return colors.success;
      case 'error':
        return colors.error;
      default:
        return colors.textDim;
    }
  };

  const isError = status === 'error';

  return (
    <div
      style={{
        marginTop: '0.5rem',
        background: colors.bgSurface,
        borderRadius: '0.375rem',
        border: `1px solid ${colors.border}`,
        borderLeft: `3px solid ${getBorderColor()}`,
        overflow: 'hidden',
      }}
    >
      {/* Header - always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          width: '100%',
          padding: '0.5rem 0.75rem',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ color: getBorderColor(), fontSize: '0.85rem' }}>
            {status === 'running' || status === 'pending' ? '⏳' : status === 'error' ? '✗' : '🔧'}
          </span>
          <span style={{ color: colors.text, fontSize: '0.75rem', fontWeight: 600 }}>
            {name}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <StatusBadge status={status} />
          <span style={{ color: colors.textDim, fontSize: '0.7rem' }}>
            #{index + 1}
          </span>
          <span style={{ color: colors.textDim, fontSize: '0.7rem' }}>
            {expanded ? '▼' : '▶'}
          </span>
        </div>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div style={{ padding: '0 0.75rem 0.75rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {/* Arguments */}
          {Object.keys(args).length > 0 && <ArgumentsDisplay args={args} />}

          {/* Result */}
          {result && <ResultDisplay result={result} isError={isError} />}

          {/* No data placeholder */}
          {Object.keys(args).length === 0 && !result && (
            <div style={{ color: colors.textDim, fontSize: '0.7rem', fontStyle: 'italic' }}>
              No arguments or results
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── ToolCallsList Component ────────────────────────────────────────────────────

interface ToolCallsListProps {
  toolCalls: ToolCall[];
  title?: string;
}

export function ToolCallsList({ toolCalls, title = 'Tool Calls' }: ToolCallsListProps) {
  if (!toolCalls || toolCalls.length === 0) {
    return null;
  }

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <div style={{
        color: colors.textDim,
        fontSize: '0.7rem',
        marginBottom: '0.5rem',
        display: 'flex',
        alignItems: 'center',
        gap: '0.25rem',
      }}>
        <span>🔧</span>
        <span>{title}</span>
        <span>({toolCalls.length})</span>
      </div>
      {toolCalls.map((toolCall, index) => (
        <ToolCallCard key={`${toolCall.name}-${index}`} toolCall={toolCall} index={index} />
      ))}
    </div>
  );
}
