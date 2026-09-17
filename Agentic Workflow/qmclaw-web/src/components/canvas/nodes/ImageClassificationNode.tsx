/**
 * Image Classification Node - Classify experiment images with ML model
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const ImageClassificationNode = memo(({ data, selected }: Props) => {
  const qubit = String(data?.config?.qubit || '');
  const experimentType = String(data?.config?.experimentType || 'spectroscopy');
  const backend = String(data?.config?.backend || 'pytorch');
  const metrics = data?.metrics || {};

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return metrics.needReview ? '#f59e0b' : '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#475569';
    }
  };

  const statusColor = getStatusColor();
  const label = metrics.label || '';
  const confidence = metrics.confidence != null ? metrics.confidence : 0;
  const margin = metrics.margin != null ? metrics.margin : 0;
  const needReview = metrics.needReview ?? false;
  const imagePath = metrics.imagePath || data?.imagePath || '';

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
        <span style={{ fontSize: '14px' }}>🧠</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Image Classification</span>
        {data?.status === 'running' && (
          <span style={{ fontSize: '10px', color: '#38bdf8', animation: 'pulse 1s infinite' }}>● Running</span>
        )}
      </div>

      <div style={{ fontSize: '11px', color: '#38bdf8', fontFamily: 'monospace', marginBottom: '4px' }}>
        {experimentType}
      </div>

      <div style={{ fontSize: '11px', fontFamily: 'monospace', marginBottom: '6px' }}>
        {qubit ? (
          <span style={{ color: '#94a3b8' }}>{qubit}</span>
        ) : (
          <span style={{ color: '#f59e0b' }}>⚠️ No qubit</span>
        )}
      </div>

      <div style={{
        fontSize: '9px',
        color: '#38bdf8',
        fontFamily: 'monospace',
        marginBottom: '6px',
        padding: '3px 6px',
        background: '#1e293b',
        borderRadius: '4px',
        display: 'inline-block',
      }}>
        ⚙ {backend}
      </div>

      {data?.status === 'completed' && metrics.label && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', fontFamily: 'monospace' }}>
          <div style={{ color: needReview ? '#f59e0b' : '#22c55e', fontWeight: 600 }}>
            {label === 'class_1' ? '🔴 class_1' : label === 'class_0' ? '🔵 class_0' : label}
          </div>
          <div style={{ color: '#94a3b8' }}>
            conf: {(confidence * 100).toFixed(1)}% | margin: {margin.toFixed(3)}
          </div>
          {needReview && (
            <div style={{ color: '#f59e0b', marginTop: '4px' }}>⚠️ 需要复核</div>
          )}
          {imagePath && (
            <div style={{ color: '#64748b', marginTop: '4px', fontSize: '9px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '170px' }}>
              📷 {imagePath.split(/[/\\]/).pop()}
            </div>
          )}
        </div>
      )}

      {data?.status === 'failed' && data?.error && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#f87171' }}>
          ❌ {String(data.error).slice(0, 60)}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />
    </div>
  );
});

ImageClassificationNode.displayName = 'ImageClassificationNode';

export default ImageClassificationNode;
