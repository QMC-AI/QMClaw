/**
 * Image Analysis Node - Analyze experiment plots with LLM vision
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const ImageAnalysisNode = memo(({ data, selected }: Props) => {
  const prompt = String(data?.config?.prompt || 'Analyze this plot');
  const imagePath = String(data?.config?.imagePath || '');
  const model = String(data?.config?.model || 'gpt-4o');

  const shortPrompt = prompt.slice(0, 50) + (prompt.length > 50 ? '...' : '');
  const displayPath = imagePath.split('/').pop() || imagePath || '(not set)';

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#06b6d4';
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
        <span style={{ fontSize: '14px' }}>🖼</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Image Analysis</span>
      </div>

      <div style={{
        fontSize: '11px',
        color: '#22d3ee',
        fontFamily: 'monospace',
        marginBottom: '6px',
        padding: '6px',
        background: '#083344',
        borderRadius: '4px',
        lineHeight: '1.4',
      }}>
        {shortPrompt}
      </div>

      <div style={{ fontSize: '10px', color: '#64748b', fontFamily: 'monospace', display: 'flex', alignItems: 'center', gap: '4px' }}>
        <span>📷</span>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '150px' }}>
          {displayPath}
        </span>
      </div>

      <div style={{
        fontSize: '9px',
        color: '#38bdf8',
        fontFamily: 'monospace',
        marginTop: '6px',
        padding: '3px 6px',
        background: '#1e293b',
        borderRadius: '4px',
        display: 'inline-block',
      }}>
        🤖 {model}
      </div>

      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />

      {data?.status === 'running' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#38bdf8', textAlign: 'center' }}>
          👁 Analyzing...
        </div>
      )}

      {data?.status === 'completed' && data?.metrics && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#22c55e', fontFamily: 'monospace' }}>
          {Object.entries(data.metrics).slice(0, 2).map(([k, v]: [string, any]) => (
            <div key={k}>{k}: {String(v).slice(0, 20)}</div>
          ))}
        </div>
      )}
    </div>
  );
});

ImageAnalysisNode.displayName = 'ImageAnalysisNode';

export default ImageAnalysisNode;
