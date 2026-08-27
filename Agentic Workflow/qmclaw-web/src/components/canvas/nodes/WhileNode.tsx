/**
 * While Loop Node - Loop until condition is met
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const WhileNode = memo(({ data, selected }: Props) => {
  const condition = String(data?.config?.condition || '{{nodes.n1.SNR}} < 2.0');
  const maxIterations = data?.config?.maxIterations ?? 10;
  const timeout = data?.config?.timeout ?? 300;

  const shortCondition = condition.replace(/\{\{([^}]+)\}\}/g, '$1').slice(0, 25);

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#a78bfa';
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
        minWidth: '220px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>🔄</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>While Loop</span>
        <span style={{ fontSize: '10px', color: '#a78bfa', marginLeft: 'auto' }}>max: {maxIterations}</span>
      </div>

      <div style={{ fontSize: '11px', color: '#a78bfa', fontFamily: 'monospace', marginBottom: '4px', padding: '6px', background: '#1e1b4b', borderRadius: '4px' }}>
        {shortCondition}{condition.length > 25 ? '...' : ''}
      </div>

      <div style={{ display: 'flex', gap: '16px', fontSize: '10px', color: '#64748b', fontFamily: 'monospace' }}>
        <span>⏱ {timeout}s</span>
        <span>🔁 {maxIterations}x</span>
      </div>

      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />

      {data?.status === 'running' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#38bdf8', textAlign: 'center' }}>
          🔄 Iterating...
        </div>
      )}
    </div>
  );
});

WhileNode.displayName = 'WhileNode';

export default WhileNode;
