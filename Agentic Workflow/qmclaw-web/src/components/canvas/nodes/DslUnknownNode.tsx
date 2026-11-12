/**
 * DslUnknownNode - 画布暂时无法表达的 DSL 语句（灰节点）
 *
 * 例如 repeat / if / call 等控制流语句。原始语句完整保存在 data.dsl.stmt 中，
 * 导出 .qwf 时会被原样带回，因此不会丢语义；节点本身不可在画布内编辑。
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

function describeStmt(stmt: any): string {
  if (stmt === null || stmt === undefined) return '(no source)';
  if (typeof stmt === 'string') return stmt;
  try {
    return JSON.stringify(stmt);
  } catch {
    return String(stmt);
  }
}

const DslUnknownNode = memo(({ data, selected }: Props) => {
  const op: string = String(data?.dsl?.op || 'dsl');
  const alias: string = String(data?.dsl?.as || data?.alias || '');
  const stmtText = describeStmt(data?.dsl?.stmt);
  const unsupported = data?.status === 'failed';

  const color = unsupported ? '#f87171' : selected ? '#38bdf8' : '#64748b';

  return (
    <div
      style={{
        background: '#0f172a',
        border: `2px dashed ${color}`,
        borderRadius: '8px',
        padding: '12px',
        minWidth: '220px',
        opacity: 0.92,
        boxShadow: selected ? `0 0 20px ${color}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="source" position={Position.Bottom} style={{ background: color, width: 8, height: 8 }} />
      <Handle type="target" position={Position.Top} style={{ background: color, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>🧱</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>DSL</span>
        <span style={{ fontSize: '10px', color: '#94a3b8', fontFamily: 'monospace' }}>{op}</span>
        {alias && (
          <span style={{ fontSize: '10px', color: '#22d3ee', fontFamily: 'monospace' }}>as {alias}</span>
        )}
      </div>

      <div style={{ fontSize: '10px', color: '#f59e0b', marginBottom: '6px' }}>
        ⚠️ 画布暂不支持该语句（原文保留，导出时原样带回）
      </div>

      <pre
        style={{
          margin: 0,
          fontSize: '9px',
          fontFamily: 'monospace',
          color: '#cbd5e1',
          background: '#1e293b',
          padding: '4px',
          borderRadius: '2px',
          maxWidth: '240px',
          maxHeight: '120px',
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}
      >
        {stmtText}
      </pre>
    </div>
  );
});

DslUnknownNode.displayName = 'DslUnknownNode';

export default DslUnknownNode;
