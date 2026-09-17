/**
 * Node Palette - Node creation panel
 *
 * Click to add nodes to the canvas.
 */

import { memo, useState } from 'react';
import { NodeType, useWorkflowStore } from '../../../store/workflowStore';

interface NodePaletteItemProps {
  type: NodeType;
  label: string;
  icon: string;
  description: string;
  onClick: () => void;
}

const NodePaletteItem = memo(({ type, label, icon, description, onClick }: NodePaletteItemProps) => {
  return (
    <div
      onClick={onClick}
      onDragStart={(e) => {
        e.dataTransfer.setData('application/reactflow', type);
        e.dataTransfer.effectAllowed = 'move';
      }}
      draggable
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '8px 12px',
        background: '#1e293b',
        border: '1px solid #334155',
        borderRadius: '6px',
        cursor: 'grab',
        transition: 'all 0.15s ease',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = '#38bdf8';
        e.currentTarget.style.background = '#1e3a5f';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = '#334155';
        e.currentTarget.style.background = '#1e293b';
      }}
    >
      <span style={{ fontSize: '16px' }}>{icon}</span>
      <div>
        <div style={{
          fontSize: '12px',
          fontWeight: 600,
          color: '#e2e8f0',
        }}>
          {label}
        </div>
        <div style={{
          fontSize: '10px',
          color: '#64748b',
        }}>
          {description}
        </div>
      </div>
    </div>
  );
});

NodePaletteItem.displayName = 'NodePaletteItem';

const nodeTypes: Omit<NodePaletteItemProps, 'onClick'>[] = [
  { type: 'context', label: 'Context', icon: '📦', description: 'Define variables' },
  { type: 'experiment', label: 'Experiment', icon: '🔬', description: 'Run sq.* experiment' },
  { type: 'quality_gate', label: 'Quality Gate', icon: '✅', description: 'Pass/fail threshold' },
  { type: 'analyze', label: 'Analyze', icon: '📊', description: 'Parse metrics' },
  { type: 'print', label: 'Print', icon: '📝', description: 'Log a message' },
  { type: 'while', label: 'While Loop', icon: '🔄', description: 'Loop until condition' },
  { type: 'parallel', label: 'Parallel', icon: '⚡', description: 'Parallel execution' },
  { type: 'decision', label: 'LLM Decision', icon: '🧠', description: 'AI-powered branch' },
  { type: 'adjust_params', label: 'Adjust Params', icon: '⚙️', description: 'Update qubit params' },
  { type: 'image_analysis', label: 'Image Analysis', icon: '🖼', description: 'Analyze experiment plot' },
  { type: 'image_classification', label: 'Image Classification', icon: '🧠', description: 'ML image classification' },
  { type: 'notify', label: 'Notify', icon: '📢', description: 'Send notification' },
  { type: 'code', label: 'Code', icon: '🐍', description: 'Run Python code' },
];

interface Props {
  collapsed?: boolean;
  onToggle?: () => void;
}

const NodePalette = memo(({ collapsed = false, onToggle }: Props) => {
  const addNode = require('../../../store/workflowStore').useWorkflowStore.getState().addNode;

  const handleAddNode = (type: NodeType) => {
    // Add node at center of canvas
    addNode(type, { x: 250, y: 200 });
  };

  if (collapsed) {
    return (
      <div
        style={{
          background: '#1e293b',
          border: '1px solid #334155',
          borderRadius: '8px',
          padding: '8px',
          cursor: 'pointer',
        }}
        onClick={onToggle}
        title="Expand node palette"
      >
        <span style={{ fontSize: '20px' }}>📦</span>
      </div>
    );
  }

  return (
    <div
      style={{
        background: '#0f172a',
        border: '1px solid #1e293b',
        borderRadius: '8px',
        padding: '12px',
        maxHeight: 'calc(100vh - 200px)',
        overflowY: 'auto',
        width: '200px',
      }}
    >
      {/* Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '12px',
      }}>
        <span style={{
          fontSize: '11px',
          fontWeight: 600,
          color: '#475569',
          textTransform: 'uppercase',
          letterSpacing: '0.1em',
        }}>
          Nodes
        </span>
        <button
          onClick={onToggle}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#64748b',
            cursor: 'pointer',
            fontSize: '14px',
          }}
          title="Collapse"
        >
          ◀
        </button>
      </div>

      {/* Node list */}
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
      }}>
        {nodeTypes.map((item) => (
          <NodePaletteItem
            key={item.type}
            {...item}
            onClick={() => handleAddNode(item.type)}
          />
        ))}
      </div>

      {/* Hint */}
      <div style={{
        marginTop: '12px',
        padding: '8px',
        background: '#1e293b',
        borderRadius: '6px',
        fontSize: '10px',
        color: '#64748b',
      }}>
        💡 Click or drag to add node
      </div>
    </div>
  );
});

NodePalette.displayName = 'NodePalette';

export default NodePalette;
