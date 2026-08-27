/**
 * Default Node - Fallback for unknown node types
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const DefaultNode = memo(({ data, selected }: Props) => {
  const type = data?.type || 'unknown';
  const label = data?.label || type;

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#475569';
    }
  };

  const statusColor = getStatusColor();

  return (
    <div
      style={{
        background: '#0f172a',
        border: `2px solid ${statusColor}`,
        borderRadius: '8px',
        padding: '12px',
        minWidth: '160px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>{label}</div>
      <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px', fontFamily: 'monospace' }}>{type}</div>

      {data?.status === 'failed' && data?.error && (
        <div style={{ marginTop: '8px', fontSize: '10px', color: '#f87171' }}>
          ❌ {String(data.error).slice(0, 50)}
        </div>
      )}
    </div>
  );
});

DefaultNode.displayName = 'DefaultNode';

export default DefaultNode;
