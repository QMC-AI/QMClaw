/**
 * Notify Node - Send message notification
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const NotifyNode = memo(({ data, selected }: Props) => {
  const channel = String(data?.config?.channel || 'feishu');
  const trigger = String(data?.config?.trigger || 'always');
  const template = String(data?.config?.template || '');

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      default: return selected ? '#38bdf8' : '#22c55e';
    }
  };

  const statusColor = getStatusColor();

  const triggerLabel = { always: '📢 Always', 'on-success': '✅ On Success', 'on-fail': '❌ On Fail' }[trigger] || trigger;
  const channelIcon = { feishu: '🔔', email: '📧', slack: '💬' }[channel] || '📢';

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
        <span style={{ fontSize: '14px' }}>{channelIcon}</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Notify</span>
        <span style={{ fontSize: '10px', color: '#22c55e', marginLeft: 'auto' }}>{channel.toUpperCase()}</span>
      </div>

      <div style={{ fontSize: '11px', color: '#94a3b8', marginBottom: '4px' }}>{triggerLabel}</div>

      {template && (
        <div style={{ fontSize: '10px', color: '#64748b', fontFamily: 'monospace', whiteSpace: 'pre-wrap', maxHeight: '40px', overflow: 'hidden' }}>
          {template.slice(0, 50)}{template.length > 50 ? '...' : ''}
        </div>
      )}

      {data?.status === 'completed' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#22c55e', textAlign: 'center' }}>
          ✓ Sent
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

NotifyNode.displayName = 'NotifyNode';

export default NotifyNode;
