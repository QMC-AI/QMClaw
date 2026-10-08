"use client";

/**
 * Reasoning Block Component
 *
 * Displays thinking/reasoning content in a collapsible card.
 * Based on hermes-hudui ReasoningBlock implementation.
 */

import { useState } from 'react';

// QMClaw color scheme
const colors = {
  primary: '#38bdf8',
  bgSurface: '#1e293b',
  bgDeep: '#0f172a',
  border: '#334155',
  text: '#e2e8f0',
  textDim: '#64748b',
  warning: '#f59e0b',
};

interface ReasoningBlockProps {
  content: string;
}

export default function ReasoningBlock({ content }: ReasoningBlockProps) {
  const [expanded, setExpanded] = useState(false);

  // Debug logging
  console.log("[ReasoningBlock] Render with content:", JSON.stringify(content?.substring(0, 100)));

  if (!content || content.trim() === '') {
    console.log("[ReasoningBlock] Empty content, not rendering");
    return null;
  }

  return (
    <div
      style={{
        borderLeft: `2px solid ${colors.warning}`,
        background: colors.bgSurface,
        borderRadius: '0.25rem',
        marginTop: '0.5rem',
        overflow: 'hidden',
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          width: '100%',
          padding: '0.5rem 0.75rem',
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span style={{ fontSize: '0.85rem' }}>🧠</span>
        <span
          style={{
            color: colors.warning,
            fontSize: '0.75rem',
            fontWeight: 500,
          }}
        >
          {expanded ? 'Thinking...' : 'Thinking (click to expand)'}
        </span>
        <span
          style={{
            color: colors.textDim,
            fontSize: '0.7rem',
            marginLeft: 'auto',
          }}
        >
          {expanded ? '▼' : '▶'}
        </span>
      </button>

      {expanded && (
        <div style={{ padding: '0 0.75rem 0.75rem' }}>
          <div
            style={{
              padding: '0.5rem 0.75rem',
              background: colors.bgDeep,
              borderRadius: '0.25rem',
              fontSize: '0.75rem',
              lineHeight: 1.6,
              color: colors.textDim,
              whiteSpace: 'pre-wrap',
              fontFamily: 'monospace',
              border: `1px solid ${colors.border}`,
            }}
          >
            {content}
          </div>
        </div>
      )}
    </div>
  );
}
