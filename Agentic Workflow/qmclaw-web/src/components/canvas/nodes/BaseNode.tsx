/**
 * BaseNode - Shared wrapper for all workflow node types
 * Handles common styling, status colors, handles, and animations
 */

import { ReactNode } from 'react';
import { Handle, Position } from '@xyflow/react';
import { useNodeExecutionStyle, NodeStatus } from '@/hooks/useNodeExecutionStyle';

interface Props {
  id: string;
  type: string;
  selected?: boolean;
  status?: NodeStatus;
  icon: string;
  title: string;
  subtitle?: string;
  children?: ReactNode;
  statusIndicator?: ReactNode;  // e.g., "● Running" text
  resultPreview?: ReactNode;     // e.g., metrics, errors, etc.
}

export default function BaseNode({
  id,
  type,
  selected,
  status,
  icon,
  title,
  subtitle,
  children,
  statusIndicator,
  resultPreview,
}: Props) {
  const { statusColor, statusClass } = useNodeExecutionStyle(status, selected);

  return (
    <div
      className={statusClass}
      style={{
        background: '#0f172a',
        border: `2px solid ${statusColor}`,
        borderRadius: '8px',
        padding: '12px',
        minWidth: '180px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.3s ease',
      }}
    >
      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>{icon}</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', fontFamily: 'monospace' }}>
          {title}
        </span>
        {statusIndicator}
      </div>

      {subtitle && (
        <div style={{ fontSize: '11px', fontFamily: 'monospace', color: '#94a3b8' }}>
          {subtitle}
        </div>
      )}

      {children}

      {resultPreview}
    </div>
  );
}
