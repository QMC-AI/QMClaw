/**
 * Contrast Node - 读出态区分度闸门
 *
 * 读回一个 IQraw 数据集，按 discriminator 轴投影算 P(1|prep I / X)，
 * contrast = |P(1|X) - P(1|I)|（与实验室 calibrate.py stage_check 同口径）。
 * datasetId 留空时自动沿用上游 experiment 节点回报的 dataset_id。
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const ContrastNode = memo(({ data, selected }: Props) => {
  const datasetId = data?.config?.datasetId;
  const qubit = String(data?.config?.qubit || '{{qubit}}');
  const kind = String(data?.config?.kind || 'IQraw');
  const threshold = Number(data?.config?.threshold ?? 0.4);

  const contrast = data?.metrics?.contrast ?? data?.result?.contrast;
  const hasValue = typeof contrast === 'number';

  const getStatusColor = () => {
    if (data?.status === 'running') return '#38bdf8';
    if (data?.status === 'failed') return '#f87171';
    if (data?.status === 'completed') {
      // 阈值以上绿、以下橙，比单纯的 completed 更有信息量
      return hasValue && contrast >= threshold ? '#22c55e' : '#f59e0b';
    }
    return selected ? '#38bdf8' : '#475569';
  };

  const statusColor = getStatusColor();
  const badge = !hasValue
    ? null
    : contrast >= threshold
      ? { text: '✓ 可用', color: '#22c55e' }
      : { text: '⚠ 偏低', color: '#f59e0b' };

  return (
    <div
      style={{
        background: '#0f172a',
        border: `2px solid ${statusColor}`,
        borderRadius: '8px',
        padding: '12px',
        minWidth: '190px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />
      <Handle type="source" position={Position.Bottom} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>🎯</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>Contrast</span>
        {data?.status === 'running' && (
          <span style={{ fontSize: '10px', color: '#38bdf8' }}>● Running</span>
        )}
      </div>

      <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', marginBottom: '4px' }}>
        {kind} #{datasetId || 'auto'}
      </div>

      <div style={{ fontSize: '11px', color: '#94a3b8', fontFamily: 'monospace', marginBottom: '4px' }}>
        {qubit} ≥ {threshold}
      </div>

      {hasValue && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontFamily: 'monospace' }}>
          <div style={{ fontSize: '15px', color: statusColor, fontWeight: 600 }}>
            {contrast.toFixed(3)}
            {badge && <span style={{ fontSize: '10px', marginLeft: '6px', color: badge.color }}>{badge.text}</span>}
          </div>
          <div style={{ fontSize: '10px', color: '#64748b' }}>
            P1(I)={Number(data.metrics?.p1_i ?? 0).toFixed(3)} P1(X)={Number(data.metrics?.p1_x ?? 0).toFixed(3)}
          </div>
          <div style={{ fontSize: '10px', color: '#64748b' }}>
            SNR={Number(data.metrics?.snr ?? 0).toFixed(2)} shots={data.metrics?.shots ?? '-'}
          </div>
        </div>
      )}

      {data?.error && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#f87171', fontFamily: 'monospace' }}>
          {String(data.error).slice(0, 80)}
        </div>
      )}
    </div>
  );
});

ContrastNode.displayName = 'ContrastNode';

export default ContrastNode;
