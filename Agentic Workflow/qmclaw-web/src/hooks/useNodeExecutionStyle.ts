import { CSSProperties } from 'react';

export type NodeStatus = 'idle' | 'running' | 'completed' | 'failed' | 'passed' | 'skipped';

export function useNodeExecutionStyle(status?: NodeStatus, selected?: boolean) {
  const statusColor = (() => {
    switch (status) {
      case 'running': return '#22c55e';
      case 'completed':
      case 'passed': return '#22c55e';
      case 'failed': return '#f87171';
      case 'skipped': return '#f59e0b';
      default: return selected ? '#38bdf8' : '#475569';
    }
  })();

  const statusClass = status ? `node-status-${status}` : '';

  const containerStyle: CSSProperties = {
    background: '#0f172a',
    border: `2px solid ${statusColor}`,
    borderRadius: '8px',
    transition: 'all 0.3s ease',
  };

  return { statusColor, statusClass, containerStyle };
}
