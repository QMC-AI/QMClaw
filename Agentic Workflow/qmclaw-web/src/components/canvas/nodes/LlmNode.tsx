/**
 * LlmNode - ask（通用 LLM 调用）节点
 * 对应 .qwf 中的 `ask` 语句
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const LlmNode = memo(({ data, selected }: Props) => {
  const messages: string[] = (data?.config?.messages as string[]) || [];
  const model: string = String(data?.config?.model || '');
  const temperature: number = Number(data?.config?.temperature ?? 0.7);
  const alias: string = String(data?.alias || data?.dsl?.as || '');
  const note: string = String(data?.config?.note || data?.note || '');

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
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
        minWidth: '200px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>💬</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>LLM Call</span>
        {alias && (
          <span style={{ fontSize: '10px', color: '#22d3ee', fontFamily: 'monospace' }}>as {alias}</span>
        )}
      </div>

      <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', marginBottom: '4px' }}>
        model: {model || '(default)'}
      </div>
      <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', marginBottom: '4px' }}>
        temperature: {Number.isFinite(temperature) ? temperature : 0.7}
      </div>

      <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px' }}>
        {messages.length === 0
          ? '🗒 no messages'
          : `🗒 ${messages.length} message${messages.length > 1 ? 's' : ''}`}
      </div>

      {messages.length > 0 && (
        <div
          style={{
            fontSize: '9px',
            color: '#cbd5e1',
            fontFamily: 'monospace',
            background: '#1e293b',
            padding: '2px 4px',
            borderRadius: '2px',
            maxWidth: '200px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {String(messages[0]).slice(0, 60)}
          {messages.length > 1 && ` +${messages.length - 1}`}
        </div>
      )}

      {note && (
        <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px', fontStyle: 'italic' }}>
          {note.slice(0, 60)}
        </div>
      )}

      {data?.status === 'completed' && data?.result !== undefined && (
        <div
          style={{
            marginTop: '8px',
            paddingTop: '8px',
            borderTop: '1px solid #1e293b',
            fontSize: '10px',
            color: '#22c55e',
            fontFamily: 'monospace',
            maxWidth: '200px',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {typeof data.result === 'string' ? data.result : JSON.stringify(data.result).slice(0, 80)}
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

LlmNode.displayName = 'LlmNode';

export default LlmNode;
