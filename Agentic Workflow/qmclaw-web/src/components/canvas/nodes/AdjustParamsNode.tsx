/**
 * Adjust Params Node - Update qubit parameters dynamically
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const AdjustParamsNode = memo(({ data, selected }: Props) => {
  const param = String(data?.config?.param || 'fread');
  const value = String(data?.config?.value || '');
  const qubit = String(data?.config?.qubit || '{{qubit}}');

  const shortValue = value.slice(0, 25) + (value.length > 25 ? '...' : '');

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#f97316';
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
        minWidth: '200px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>⚙️</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Adjust Params</span>
      </div>

      <div style={{ fontSize: '11px', color: '#fb923c', fontFamily: 'monospace', marginBottom: '4px' }}>
        {param} =
      </div>

      <div style={{
        fontSize: '11px',
        color: '#22c55e',
        fontFamily: 'monospace',
        padding: '6px',
        background: '#14532d',
        borderRadius: '4px',
        marginBottom: '8px',
      }}>
        {shortValue || '(not set)'}
      </div>

      <div style={{ fontSize: '10px', color: '#64748b', fontFamily: 'monospace' }}>
        qubit: {qubit}
      </div>

      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />

      {data?.status === 'running' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#38bdf8', textAlign: 'center' }}>
          ⚙️ Applying...
        </div>
      )}

      {data?.status === 'completed' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#22c55e', textAlign: 'center' }}>
          ✓ Applied
        </div>
      )}

      {data?.status === 'failed' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#f87171', textAlign: 'center' }}>
          ✗ Failed
        </div>
      )}
    </div>
  );
});

AdjustParamsNode.displayName = 'AdjustParamsNode';

export default AdjustParamsNode;
