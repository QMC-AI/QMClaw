/**
 * Node Search - Quick search and filter for nodes
 */

import { memo, useState, useMemo } from 'react';
import { useWorkflowStore } from '../../../store/workflowStore';

interface Props {
  onClose?: () => void;
}

const NodeSearch = memo(({ onClose }: Props) => {
  const [query, setQuery] = useState('');
  const [filterType, setFilterType] = useState<string>('all');
  const [filterStatus, setFilterStatus] = useState<string>('all');

  const nodes = useWorkflowStore((state) => state.nodes);
  const selectNode = useWorkflowStore((state) => state.selectNode);
  const setSelectedNodes = useWorkflowStore((state) => state.selectNode);

  // Get unique node types in canvas
  const nodeTypes = useMemo(() => {
    const types = new Set<string>();
    nodes.forEach(n => types.add(n.data.type));
    return ['all', ...Array.from(types)];
  }, [nodes]);

  // Filter and search
  const filteredNodes = useMemo(() => {
    let result = nodes;

    // Type filter
    if (filterType !== 'all') {
      result = result.filter(n => n.data.type === filterType);
    }

    // Status filter
    if (filterStatus !== 'all') {
      result = result.filter(n => n.data.status === filterStatus);
    }

    // Text search
    if (query.trim()) {
      const q = query.toLowerCase();
      result = result.filter(n =>
        n.id.toLowerCase().includes(q) ||
        n.data.label.toLowerCase().includes(q) ||
        JSON.stringify(n.data.config).toLowerCase().includes(q)
      );
    }

    return result;
  }, [nodes, query, filterType, filterStatus]);

  const handleSelect = (nodeId: string) => {
    selectNode(nodeId);
  };

  return (
    <div style={{
      position: 'absolute',
      top: '16px',
      right: '16px',
      width: '320px',
      maxHeight: '400px',
      background: '#0f172a',
      border: '1px solid #1e293b',
      borderRadius: '8px',
      zIndex: 15,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 12px',
        background: '#1e293b',
        borderBottom: '1px solid #334155',
      }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '8px',
        }}>
          <span style={{
            fontSize: '11px',
            fontWeight: 600,
            color: '#94a3b8',
            letterSpacing: '0.05em',
          }}>
            🔍 SEARCH NODES
          </span>
          {onClose && (
            <button
              onClick={onClose}
              style={{
                background: 'transparent',
                border: 'none',
                color: '#64748b',
                cursor: 'pointer',
                fontSize: '14px',
                padding: '2px 6px',
              }}
            >
              ✕
            </button>
          )}
        </div>

        {/* Search input */}
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by ID, label, or config..."
          style={{
            width: '100%',
            padding: '6px 10px',
            background: '#0f172a',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#e2e8f0',
            fontSize: '11px',
            fontFamily: 'monospace',
            boxSizing: 'border-box',
          }}
        />

        {/* Filters */}
        <div style={{
          display: 'flex',
          gap: '4px',
          marginTop: '6px',
        }}>
          <select
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
            style={{
              flex: 1,
              padding: '4px 8px',
              background: '#0f172a',
              border: '1px solid #334155',
              borderRadius: '4px',
              color: '#94a3b8',
              fontSize: '10px',
            }}
          >
            {nodeTypes.map(t => (
              <option key={t} value={t}>
                {t === 'all' ? 'All types' : t}
              </option>
            ))}
          </select>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            style={{
              flex: 1,
              padding: '4px 8px',
              background: '#0f172a',
              border: '1px solid #334155',
              borderRadius: '4px',
              color: '#94a3b8',
              fontSize: '10px',
            }}
          >
            <option value="all">All status</option>
            <option value="idle">Idle</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="passed">Passed</option>
          </select>
        </div>
      </div>

      {/* Results */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '8px',
      }}>
        {filteredNodes.length === 0 ? (
          <div style={{
            textAlign: 'center',
            color: '#475569',
            fontSize: '11px',
            padding: '20px',
          }}>
            No nodes match your search
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {filteredNodes.map(node => (
              <div
                key={node.id}
                onClick={() => handleSelect(node.id)}
                style={{
                  padding: '8px 10px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = '#38bdf8';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = '#334155';
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: '11px',
                    fontWeight: 600,
                    color: '#e2e8f0',
                    fontFamily: 'monospace',
                  }}>
                    {node.id}
                  </div>
                  <div style={{
                    fontSize: '10px',
                    color: '#64748b',
                    marginTop: '2px',
                  }}>
                    {node.data.type}
                  </div>
                </div>
                {node.data.status && node.data.status !== 'idle' && (
                  <span style={{
                    fontSize: '9px',
                    padding: '2px 6px',
                    borderRadius: '3px',
                    background:
                      node.data.status === 'completed' ? '#1e3a2f' :
                      node.data.status === 'failed' ? '#3a1e1e' :
                      node.data.status === 'passed' ? '#1e3a2f' :
                      node.data.status === 'running' ? '#1e3a5f' : '#1e293b',
                    color:
                      node.data.status === 'completed' ? '#22c55e' :
                      node.data.status === 'failed' ? '#f87171' :
                      node.data.status === 'passed' ? '#22c55e' :
                      node.data.status === 'running' ? '#38bdf8' : '#94a3b8',
                  }}>
                    {node.data.status}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{
        padding: '6px 12px',
        background: '#1e293b',
        borderTop: '1px solid #334155',
        fontSize: '10px',
        color: '#64748b',
        textAlign: 'center',
      }}>
        {filteredNodes.length} of {nodes.length} nodes
      </div>
    </div>
  );
});

NodeSearch.displayName = 'NodeSearch';

export default NodeSearch;