/**
 * Parallel Node - Container for parallel execution
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const ParallelNode = memo(({ data, selected }: Props) => {
  const mode = String(data?.config?.mode || 'auto');
  const waitFor = String(data?.config?.waitFor || 'all');

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#64748b';
    }
  };

  const statusColor = getStatusColor();

  return (
    <div
      style={{
        background: '#1e293b',
        border: `2px dashed ${statusColor}`,
        borderRadius: '12px',
        padding: '16px',
        minWidth: '280px',
        minHeight: '120px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
        position: 'relative',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 10, height: 10 }} />

      <div style={{
        display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px',
        padding: '6px 10px', background: '#0f172a', borderRadius: '6px',
        position: 'absolute', top: '-16px', left: '16px',
      }}>
        <span style={{ fontSize: '14px' }}>⚡</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Parallel</span>
        <span style={{ fontSize: '10px', color: '#64748b', marginLeft: '8px' }}>{mode} | wait: {waitFor}</span>
      </div>

      <div style={{
        position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
        fontSize: '12px', color: '#475569', pointerEvents: 'none',
      }}>
        Drop nodes here
      </div>

      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 10, height: 10 }} />

      {data?.status === 'running' && (
        <div style={{ position: 'absolute', bottom: '8px', right: '8px', fontSize: '10px', color: '#38bdf8' }}>
          🔄 Running...
        </div>
      )}
    </div>
  );
});

ParallelNode.displayName = 'ParallelNode';

export default ParallelNode;
