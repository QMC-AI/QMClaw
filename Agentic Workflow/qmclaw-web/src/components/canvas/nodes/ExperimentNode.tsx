/**
 * Experiment Node - Run an sq.* experiment
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const ExperimentNode = memo(({ data, selected }: Props) => {
  const fn = String(data?.config?.fn || 'sq.iqraw');
  const qubit = String(data?.config?.qubit || '{{qubit}}');
  const isQubitMissing = !data?.config?.qubit || String(data?.config?.qubit).trim() === '';

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      case 'passed': return '#22c55e';
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
        <span style={{ fontSize: '14px' }}>🔬</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', fontFamily: 'monospace' }}>
          Experiment
        </span>
        {data?.status === 'running' && (
          <span style={{ fontSize: '10px', color: '#38bdf8', animation: 'pulse 1s infinite' }}>
            ● Running
          </span>
        )}
      </div>

      <div style={{ fontSize: '11px', color: '#38bdf8', fontFamily: 'monospace', marginBottom: '4px' }}>
        {fn}
      </div>

      <div style={{ fontSize: '11px', fontFamily: 'monospace', display: 'flex', alignItems: 'center', gap: '4px' }}>
        {isQubitMissing ? (
          <span style={{ color: '#f59e0b' }}>⚠️ No qubit</span>
        ) : (
          <span style={{ color: '#94a3b8' }}>{qubit}</span>
        )}
      </div>

      {data?.status === 'completed' && data?.metrics && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#22c55e', fontFamily: 'monospace' }}>
          {Object.entries(data.metrics).slice(0, 3).map(([k, v]: [string, any]) => (
            <div key={k}>{k}: {typeof v === 'number' ? v.toFixed(3) : v}</div>
          ))}
        </div>
      )}

      {/* Plot command preview */}
      {data?.result?.plotCommand && (
        <div style={{
          marginTop: '8px',
          padding: '4px 6px',
          background: '#1e293b',
          borderRadius: '4px',
          fontSize: '9px',
          fontFamily: 'monospace',
          color: '#a78bfa',
        }}>
          📊 {String(data.result.plotCommand).slice(0, 40)}{String(data.result.plotCommand).length > 40 ? '...' : ''}
        </div>
      )}

      {/* Image analysis result */}
      {(data?.result?.analysis?.result || data?.analysis?.result) && (
        <div style={{
          marginTop: '6px',
          padding: '6px',
          background: '#1a1a2e',
          borderRadius: '4px',
          fontSize: '9px',
          color: '#94a3b8',
          lineHeight: '1.4',
          maxHeight: '60px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          <div style={{ color: '#a78bfa', marginBottom: '2px', fontSize: '8px' }}>🖼️ Analysis:</div>
          {(data.result?.analysis?.result || data.analysis?.result)?.slice(0, 100)}...
        </div>
      )}

      {/* Show executed call code */}
      {(data?.result?.callCode || data?.callCode) && (
        <div style={{
          marginTop: '6px',
          padding: '4px 6px',
          background: '#1e293b',
          borderRadius: '4px',
          fontSize: '9px',
          fontFamily: 'monospace',
          color: '#38bdf8',
        }}>
          ▶ {data.result?.callCode || data.callCode}
        </div>
      )}

      {/* Batch experiments summary */}
      {data?.result?.batchResults && (
        <div style={{
          marginTop: '6px',
          padding: '4px 6px',
          background: '#1e293b',
          borderRadius: '4px',
          fontSize: '9px',
          color: '#22c55e',
        }}>
          📋 {data.result.batchResults.length} experiment{data.result.batchResults.length > 1 ? 's' : ''} completed
        </div>
      )}

      {data?.status === 'failed' && data?.error && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#f87171' }}>
          ❌ {String(data.error).slice(0, 50)}
        </div>
      )}
    </div>
  );
});

ExperimentNode.displayName = 'ExperimentNode';

export default ExperimentNode;
