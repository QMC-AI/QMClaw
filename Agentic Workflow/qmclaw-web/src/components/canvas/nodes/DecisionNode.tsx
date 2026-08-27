/**
 * Decision Node - LLM-powered branching decision
 */

import { memo } from 'react';
import { Handle, Position } from '@xyflow/react';

interface Props {
  data: any;
  selected: boolean;
}

const DecisionNode = memo(({ data, selected }: Props) => {
  const mode = String(data?.config?.mode || 'analysis');
  const model = String(data?.config?.model || 'gpt-4o');
  const temperature = Number(data?.config?.temperature || 0.3);

  // New output fields - handle both array and JSON string for recommendations
  const symptom = data?.result?.symptom || data?.result?.conversation?.symptom || '';
  let recommendations = data?.result?.recommendations || data?.result?.conversation?.recommendations || [];
  // Parse JSON string if recommendations is a string (from Experiment node batchConfig)
  if (typeof recommendations === 'string' && recommendations.startsWith('[')) {
    try {
      recommendations = JSON.parse(recommendations);
    } catch {
      recommendations = [];
    }
  }
  const reasoning = data?.result?.reasoning || data?.result?.conversation?.reasoning || '';
  const matchedRules = data?.result?.matchedRules || data?.result?.conversation?.matchedRules || [];

  const shortReasoning = reasoning.slice(0, 60) + (reasoning.length > 60 ? '...' : '');

  const getStatusColor = () => {
    switch (data?.status) {
      case 'running': return '#38bdf8';
      case 'completed': return '#22c55e';
      case 'failed': return '#f87171';
      case 'passed': return '#22c55e';
      default: return selected ? '#38bdf8' : '#ec4899';
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
        minWidth: '220px',
        boxShadow: selected ? `0 0 20px ${statusColor}40` : 'none',
        transition: 'all 0.2s ease',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: statusColor, width: 8, height: 8 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
        <span style={{ fontSize: '14px' }}>🧠</span>
        <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>LLM Decision</span>
        <span style={{
          fontSize: '9px',
          padding: '2px 6px',
          background: mode === 'analysis' ? '#1e3a5f' : '#3a1e5f',
          borderRadius: '4px',
          color: mode === 'analysis' ? '#38bdf8' : '#a78bfa',
        }}>
          {mode === 'analysis' ? '📊' : '🎯'}
        </span>
      </div>

      {/* Model info */}
      <div style={{
        fontSize: '10px',
        color: '#38bdf8',
        fontFamily: 'monospace',
        marginBottom: '6px',
        padding: '4px 8px',
        background: '#1e293b',
        borderRadius: '4px',
        display: 'flex',
        justifyContent: 'space-between',
      }}>
        <span>{model || 'Not selected'}</span>
        <span style={{ color: '#64748b' }}>T:{temperature}</span>
      </div>

      {/* Symptom */}
      {symptom && (
        <div style={{
          fontSize: '10px',
          color: '#f472b6',
          fontFamily: 'monospace',
          marginBottom: '6px',
          padding: '6px',
          background: '#4c0519',
          borderRadius: '4px',
          lineHeight: '1.4',
        }}>
          🎯 {symptom.slice(0, 50)}{symptom.length > 50 ? '...' : ''}
        </div>
      )}

      {/* Recommendations count */}
      {recommendations.length > 0 && (
        <div style={{
          fontSize: '10px',
          color: '#22c55e',
          fontFamily: 'monospace',
          marginBottom: '4px',
          padding: '4px 8px',
          background: '#1e3a2f',
          borderRadius: '4px',
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
        }}>
          📋 {recommendations.length} recommendation{recommendations.length > 1 ? 's' : ''}
        </div>
      )}

      {/* Matched rules */}
      {matchedRules.length > 0 && (
        <div style={{ fontSize: '9px', color: '#64748b', marginBottom: '4px', display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
          {matchedRules.slice(0, 3).map((rule: string, i: number) => (
            <span key={i} style={{
              padding: '1px 4px',
              background: '#1e293b',
              borderRadius: '2px',
              color: '#94a3b8',
            }}>
              {rule}
            </span>
          ))}
        </div>
      )}

      {/* Reasoning preview */}
      {reasoning && (
        <div style={{
          fontSize: '9px',
          color: '#94a3b8',
          fontFamily: 'monospace',
          marginBottom: '4px',
          padding: '4px 6px',
          background: '#1e293b',
          borderRadius: '4px',
          lineHeight: '1.3',
        }}>
          💭 {shortReasoning}
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '10px', color: '#22c55e', marginBottom: '4px' }}>YES</div>
          <Handle type="source" position={Position.Bottom} id="yes" style={{ background: '#22c55e', width: 8, height: 8 }} />
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '10px', color: '#f87171', marginBottom: '4px' }}>NO</div>
          <Handle type="source" position={Position.Bottom} id="no" style={{ background: '#f87171', width: 8, height: 8 }} />
        </div>
      </div>

      {data?.status === 'running' && (
        <div style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid #1e293b', fontSize: '10px', color: '#38bdf8', textAlign: 'center' }}>
          🤖 Thinking...
        </div>
      )}
    </div>
  );
});

DecisionNode.displayName = 'DecisionNode';

export default DecisionNode;
