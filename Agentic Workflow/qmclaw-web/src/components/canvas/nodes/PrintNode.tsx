/**
 * Print Node - Log a message
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const PrintNode = memo(({ data, selected }: Props) => {
  const message = String(data?.config?.message || 'Step completed');

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
        minWidth: '180px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>📝</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Print</span>
      </div>

      <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', whiteSpace: 'pre-wrap', maxHeight: '60px', overflow: 'hidden' }}>
        "{message.slice(0, 60)}{message.length > 60 ? '...' : ''}"
      </div>
    </div>
  );
});

PrintNode.displayName = 'PrintNode';

export default PrintNode;
