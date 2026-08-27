/**
 * Analyze Node - Parse metrics from previous node
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const AnalyzeNode = memo(({ data, selected }: Props) => {
  const ref = String(data?.config?.ref || '');
  const experimentsToAnalyze = (data?.config?.experimentsToAnalyze as string[]) || [];
  const allExpTypes = ['iqraw', 't1', 'ramsey', 'piamp', 'xeb', 's21', 'spectroscopy', 'allxy', 'single_shot', 's21_dis', 'pulsed_spec', 'swap', 'drag_calibrate'];

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#475569';
    }
  };

  const statusColor = getStatusColor();

  const showAll = experimentsToAnalyze.length === allExpTypes.length;

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
      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>📊</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Analyze</span>
      </div>

      <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', marginBottom: '4px' }}>
        ref: {ref || '(not set)'}
      </div>

      <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px' }}>
        {showAll || experimentsToAnalyze.length === 0
          ? '📦 All experiments'
          : `📦 ${experimentsToAnalyze.length} selected`}
      </div>

      {!showAll && experimentsToAnalyze.length > 0 && experimentsToAnalyze.length < allExpTypes.length && (
        <div style={{
          fontSize: '9px',
          color: '#22d3ee',
          fontFamily: 'monospace',
          background: '#1e293b',
          padding: '2px 4px',
          borderRadius: '2px',
          maxWidth: '180px',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {experimentsToAnalyze.slice(0, 4).join(', ')}
          {experimentsToAnalyze.length > 4 && ` +${experimentsToAnalyze.length - 4}`}
        </div>
      )}

      {data?.status === 'completed' && data?.metrics && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#22c55e', fontFamily: 'monospace' }}>
          {Object.keys(data.metrics).slice(0, 5).map((k: string) => (
            <div key={k}>{k}: {typeof data.metrics[k] === 'number' ? data.metrics[k].toFixed(2) : data.metrics[k]}</div>
          ))}
          {Object.keys(data.metrics).length > 5 && (
            <div style={{ color: '#64748b' }}>+{Object.keys(data.metrics).length - 5} more</div>
          )}
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

AnalyzeNode.displayName = 'AnalyzeNode';

export default AnalyzeNode;
