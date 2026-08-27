/**
 * Context Node - Define workflow variables like qubit
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const ContextNode = memo(({ data, selected }: Props) => {
  const variables = data?.config?.variables || {};

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#64748b';
    }
  };

  const statusColor = getStatusColor();
  const variableEntries = Object.entries(variables);

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
        <span style={{ fontSize: '14px' }}>📦</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', fontFamily: 'monospace' }}>
          Context
        </span>
        {variableEntries.length > 0 && (
          <span style={{
            fontSize: '10px',
            padding: '2px 6px',
            background: '#1e293b',
            borderRadius: '4px',
            color: '#38bdf8',
          }}>
            {variableEntries.length} vars
          </span>
        )}
      </div>

      {variableEntries.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {variableEntries.map(([key, value]: [string, any]) => (
            <div key={key} style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '4px 8px',
              background: '#1e293b',
              borderRadius: '4px',
              fontSize: '11px',
              fontFamily: 'monospace',
            }}>
              <span style={{ color: '#94a3b8' }}>{key}</span>
              <span style={{ color: '#22c55e' }}>{String(value)}</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{
          fontSize: '10px',
          color: '#f59e0b',
          padding: '8px',
          background: '#3a2a1a',
          borderRadius: '4px',
          textAlign: 'center',
        }}>
          ⚠️ No variables defined
        </div>
      )}
    </div>
  );
});

ContextNode.displayName = 'ContextNode';

export default ContextNode;
