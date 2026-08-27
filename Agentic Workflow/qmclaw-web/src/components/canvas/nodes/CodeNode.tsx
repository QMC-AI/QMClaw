/**
 * Code Node - Sandboxed Python execution
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const CodeNode = memo(({ data, selected }: Props) => {
  const code = String(data?.config?.code || '# Write code here');
  const timeout = data?.config?.timeout ?? 30;

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#475569';
    }
  };

  const statusColor = getStatusColor();

  // Get first few lines of code for preview
  const codePreview = code.split('\n').slice(0, 3).join('\n');

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
      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>🐍</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Code</span>
        <span style={{ fontSize: '10px', color: '#64748b', marginLeft: 'auto' }}>⏱️ {timeout}s</span>
      </div>

      <div style={{
        fontSize: '10px',
        color: '#22d3ee',
        fontFamily: 'monospace',
        whiteSpace: 'pre-wrap',
        maxHeight: '50px',
        overflow: 'hidden',
        background: '#1e293b',
        padding: '4px',
        borderRadius: '4px',
      }}>
        {codePreview || '# Empty code'}
        {(code.split('\n').length > 3 || codePreview.length > 60) && '...'}
      </div>

      {data?.result && (
        <div style={{
          marginTop: '8px',
          padding: '4px',
          background: '#1e293b',
          borderRadius: '4px',
          fontSize: '10px',
        }}>
          <span style={{ color: '#64748b' }}>Result: </span>
          <span style={{ color: '#22c55e' }}>
            {typeof data.result === 'object' ? JSON.stringify(data.result).slice(0, 30) : String(data.result).slice(0, 30)}
          </span>
        </div>
      )}
    </div>
  );
});

CodeNode.displayName = 'CodeNode';

export default CodeNode;
