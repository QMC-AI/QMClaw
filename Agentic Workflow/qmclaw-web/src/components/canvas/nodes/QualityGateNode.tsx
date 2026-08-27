/**
 * Quality Gate Node - Pass/fail on metric thresholds
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const QualityGateNode = memo(({ data, selected }: Props) => {
  const ref = String(data?.config?.ref || '');
  const metric = String(data?.config?.metric || 'SNR');
  const threshold = data?.config?.threshold ?? 1.5;
  const direction = data?.config?.direction || 'above';

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'passed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#f59e0b';
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
        <span style={{ fontSize: '14px' }}>✅</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Quality Gate</span>
      </div>

      <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', marginBottom: '4px' }}>
        {metric} {direction === 'above' ? '≥' : '≤'} {threshold}
      </div>

      <div style={{ fontSize: '10px', color: '#64748b', fontFamily: 'monospace' }}>
        ref: {ref || '(not set)'}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '12px' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '10px', color: '#22c55e', marginBottom: '4px' }}>PASS</div>
          <Handle type="source" position={Position.Bottom} id="pass" style={{ background: '#22c55e', width: 8, height: 8 }} />
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '10px', color: '#f87171', marginBottom: '4px' }}>FAIL</div>
          <Handle type="source" position={Position.Bottom} id="fail" style={{ background: '#f87171', width: 8, height: 8 }} />
        </div>
      </div>

      {data?.status === 'passed' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#22c55e', textAlign: 'center' }}>
          ✓ PASSED
        </div>
      )}
      {data?.status === 'failed' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#f87171', textAlign: 'center' }}>
          ✗ FAILED
        </div>
      )}
    </div>
  );
});

QualityGateNode.displayName = 'QualityGateNode';

export default QualityGateNode;
