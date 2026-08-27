/**
 * Debug Toolbar - Execution controls for workflow canvas
 */

import { useState, useRef, useEffect } from 'react';
import { useWorkflowStore, NodeType } from '../../../store/workflowStore';
import { api } from '../../../lib/api';

interface NodeTemplate {
  id: string;
  name: string;
  type: string;
  config: Record<string, unknown>;
  tags: string[];
  author: string;
  version: string;
  createdAt: string;
  updatedAt: string;
}

interface Props {
  onRunWorkflow: () => void;
  onRunSelected: () => void;
  onStop: () => void;
  onSave: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onAutoLayout?: () => void;
  onToggleSearch?: () => void;
  onExport?: () => void;
  onImport?: () => void;
  onLoad?: (workflowId: string) => void;
  onLog?: (msg: string, isError?: boolean) => void;
}

export default function DebugToolbar({
  onRunWorkflow,
  onRunSelected,
  onStop,
  onSave,
  onUndo,
  onRedo,
  onAutoLayout,
  onToggleSearch,
  onExport,
  onImport,
  onLoad,
  onLog,
}: Props) {
  const [showList, setShowList] = useState(false);

  const [showTemplates, setShowTemplates] = useState(false);
  const [workflows, setWorkflows] = useState<Array<{
    id: string;
    name: string;
    version: number;
    updatedAt: string;
  }>>([]);
  const [loading, setLoading] = useState(false);
  const [templates, setTemplates] = useState<NodeTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveForm, setSaveForm] = useState({ name: '', tags: '' });
  const dropdownRef = useRef<HTMLDivElement>(null);
  const templateRef = useRef<HTMLDivElement>(null);

  const execution = useWorkflowStore((state) => state.execution);
  const selectedNodes = useWorkflowStore((state) => state.selectedNodes);
  const nodes = useWorkflowStore((state) => state.nodes);
  const addNode = useWorkflowStore((state) => state.addNode);

  const isRunning = execution.status === 'running';
  const hasSelection = selectedNodes.length > 0;

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowList(false);
      }
      if (templateRef.current && !templateRef.current.contains(e.target as Node)) {
        setShowTemplates(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Load workflow list
  const loadWorkflowList = async () => {
    setLoading(true);
    try {
      const list = await api.listSavedWorkflows() as Array<{
        id: string;
        name: string;
        version: number;
        updatedAt: string;
      }>;
      setWorkflows(list);
    } catch (e: any) {
      onLog?.('Failed to load workflow list: ' + e.message, true);
    } finally {
      setLoading(false);
    }
  };

  const handleShowList = async () => {
    if (!showList) {
      await loadWorkflowList();
    }
    setShowList(!showList);
  };

  const handleLoadWorkflow = (id: string) => {
    onLoad?.(id);
    setShowList(false);
  };

  // ── Template handling ────────────────────────────────────────────────────────

  const loadTemplates = async () => {
    setTemplatesLoading(true);
    try {
      const data = await api.listTemplates();
      const loadedTemplates: NodeTemplate[] = data.map((t: any) => ({
        ...t,
        createdAt: t.createdAt || new Date().toISOString(),
        updatedAt: t.updatedAt || new Date().toISOString(),
      }));
      setTemplates(loadedTemplates);
    } catch (e: any) {
      console.error('Failed to load templates:', e);
    } finally {
      setTemplatesLoading(false);
    }
  };

  const handleToggleTemplates = async () => {
    if (!showTemplates) {
      await loadTemplates();
    }
    setShowTemplates(!showTemplates);
    setShowList(false);
  };

  const handleAddTemplate = (template: NodeTemplate) => {
    addNode(template.type as NodeType, {
      x: 300 + Math.random() * 200,
      y: 200 + Math.random() * 200,
    });
    setShowTemplates(false);
    onLog?.(`Added template: ${template.name}`);
  };

  const handleDeleteTemplate = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm('Delete this template?')) return;
    try {
      await api.deleteTemplate(id);
      await loadTemplates();
    } catch (e: any) {
      alert('Failed to delete template: ' + e.message);
    }
  };

  const handleSaveTemplate = async () => {
    if (selectedNodes.length !== 1) {
      alert('Select exactly one node to save as template');
      return;
    }
    const node = nodes.find(n => n.id === selectedNodes[0]);
    if (!node) return;

    try {
      await api.saveTemplate({
        name: saveForm.name || node.id,
        type: node.data.type,
        config: node.data.config,
        tags: saveForm.tags.split(',').map(t => t.trim()).filter(Boolean),
        author: 'current-user',
      });
      setShowSaveDialog(false);
      setSaveForm({ name: '', tags: '' });
      await loadTemplates();
      onLog?.('Template saved successfully');
    } catch (e: any) {
      alert('Failed to save template: ' + e.message);
    }
  };

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        background: '#0f172a',
        border: '1px solid #1e293b',
        borderRadius: '8px',
        padding: '8px 16px',
        boxShadow: '0 4px 20px rgba(0,0,0,0.3)',
        pointerEvents: 'auto',
      }}
    >
      {/* Load Workflow Dropdown */}
      <div ref={dropdownRef} style={{ position: 'relative' }}>
        <button
          onClick={handleShowList}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 12px',
            background: showList ? '#1e3a5f' : '#1e293b',
            border: showList ? '1px solid #38bdf8' : '1px solid #334155',
            borderRadius: '6px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
          }}
          title="Open saved workflows"
        >
          <span>📂</span>
          <span>Load</span>
        </button>

        {/* Dropdown list */}
        {showList && (
          <div style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: '4px',
            minWidth: '280px',
            maxHeight: '400px',
            overflow: 'auto',
            background: '#0f172a',
            border: '1px solid #1e293b',
            borderRadius: '6px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            zIndex: 100,
          }}>
            <div style={{
              padding: '8px 12px',
              borderBottom: '1px solid #1e293b',
              fontSize: '11px',
              color: '#64748b',
              fontWeight: 600,
            }}>
              SAVED WORKFLOWS
            </div>
            {loading ? (
              <div style={{ padding: '16px', textAlign: 'center', color: '#64748b', fontSize: '12px' }}>
                Loading...
              </div>
            ) : workflows.length === 0 ? (
              <div style={{ padding: '16px', textAlign: 'center', color: '#475569', fontSize: '12px' }}>
                No saved workflows
              </div>
            ) : (
              workflows.map((wf) => (
                <div
                  key={wf.id}
                  onClick={() => handleLoadWorkflow(wf.id)}
                  style={{
                    padding: '10px 12px',
                    borderBottom: '1px solid #1e293b',
                    cursor: 'pointer',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#1e293b')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <div style={{ fontSize: '12px', color: '#e2e8f0', fontWeight: 500 }}>
                    {wf.name}
                  </div>
                  <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px' }}>
                    v{wf.version} · {new Date(wf.updatedAt).toLocaleString()}
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>
      {/* Run buttons */}
      <div style={{ display: 'flex', gap: '6px' }}>
        {/* Run selected node */}
        <button
          onClick={onRunSelected}
          disabled={isRunning || !hasSelection}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 12px',
            background: hasSelection && !isRunning ? '#22c55e' : '#1e293b',
            border: 'none',
            borderRadius: '6px',
            color: hasSelection && !isRunning ? '#fff' : '#64748b',
            cursor: hasSelection && !isRunning ? 'pointer' : 'not-allowed',
            fontSize: '12px',
            fontWeight: 600,
          }}
          title="Run selected node (Ctrl+Enter)"
        >
          <span>▶</span>
          <span>Run Selected</span>
        </button>

        {/* Run full workflow */}
        <button
          onClick={() => {
            onLog?.('Run Workflow clicked, status: ' + execution.status);
            onRunWorkflow();
          }}
          disabled={isRunning}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 16px',
            background: isRunning ? '#1e293b' : '#38bdf8',
            border: '2px solid ' + (isRunning ? '#f87171' : '#22c55e'),
            borderRadius: '6px',
            color: isRunning ? '#64748b' : '#0f172a',
            cursor: isRunning ? 'not-allowed' : 'pointer',
            fontSize: '12px',
            fontWeight: 600,
          }}
          title={"Run full workflow, status: " + execution.status}
        >
          <span>▶</span>
          <span>Run Workflow {isRunning ? '(running...)' : ''}</span>
        </button>

        {/* Stop */}
        <button
          onClick={onStop}
          disabled={!isRunning}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '8px 12px',
            background: isRunning ? '#f87171' : '#1e293b',
            border: 'none',
            borderRadius: '6px',
            color: isRunning ? '#fff' : '#64748b',
            cursor: isRunning ? 'pointer' : 'not-allowed',
            fontSize: '12px',
            fontWeight: 600,
          }}
          title="Stop execution"
        >
          <span>⏹</span>
          <span>Stop</span>
        </button>
      </div>

      {/* Divider */}
      <div style={{
        width: '1px',
        height: '24px',
        background: '#1e293b',
        margin: '0 8px',
      }} />

      {/* Undo/Redo */}
      <div style={{ display: 'flex', gap: '4px' }}>
        <button
          onClick={onUndo}
          style={{
            padding: '6px 10px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '14px',
          }}
          title="Undo (Ctrl+Z)"
        >
          ↶
        </button>
        <button
          onClick={onRedo}
          style={{
            padding: '6px 10px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '14px',
          }}
          title="Redo (Ctrl+Y)"
        >
          ↷
        </button>
      </div>

      {/* Divider */}
      <div style={{
        width: '1px',
        height: '24px',
        background: '#1e293b',
        margin: '0 8px',
      }} />

      {/* Save */}
      <button
        onClick={onSave}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '8px 12px',
          background: '#1e293b',
          border: '1px solid #334155',
          borderRadius: '6px',
          color: '#94a3b8',
          cursor: 'pointer',
          fontSize: '12px',
          fontWeight: 600,
        }}
        title="Save workflow"
      >
        <span>💾</span>
        <span>Save</span>
      </button>

      {/* Divider */}
      <div style={{
        width: '1px',
        height: '24px',
        background: '#1e293b',
        margin: '0 8px',
      }} />

      {/* Export */}
      {onExport && (
        <button
          onClick={onExport}
          style={{
            padding: '6px 10px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '12px',
          }}
          title="Export workflow as .qmclaw.json file"
        >
          📤 Export
        </button>
      )}

      {/* Import */}
      {onImport && (
        <button
          onClick={onImport}
          style={{
            padding: '6px 10px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '12px',
          }}
          title="Import workflow from .qmclaw.json file"
        >
          📥 Import
        </button>
      )}

      {/* Divider */}
      <div style={{
        width: '1px',
        height: '24px',
        background: '#1e293b',
        margin: '0 8px',
      }} />

      {/* Auto Layout */}
      {onAutoLayout && (
        <button
          onClick={onAutoLayout}
          style={{
            padding: '6px 10px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '12px',
          }}
          title="Auto-arrange nodes"
        >
          📐 Layout
        </button>
      )}

      {/* Search */}
      {onToggleSearch && (
        <button
          onClick={onToggleSearch}
          style={{
            padding: '6px 10px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '4px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '12px',
          }}
          title="Search nodes"
        >
          🔍
        </button>
      )}

      {/* Template Dropdown */}
      <div ref={templateRef} style={{ position: 'relative' }}>
        <button
          onClick={handleToggleTemplates}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 10px',
            background: showTemplates ? '#1e3a5f' : '#1e293b',
            border: showTemplates ? '1px solid #38bdf8' : '1px solid #334155',
            borderRadius: '4px',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: '12px',
          }}
          title="Templates"
        >
          📋 Templates {templates.length > 0 && `(${templates.length})`}
        </button>

        {/* Template Dropdown */}
        {showTemplates && (
          <div style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: '4px',
            width: '300px',
            maxHeight: '400px',
            overflow: 'auto',
            background: '#0f172a',
            border: '1px solid #1e293b',
            borderRadius: '8px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            zIndex: 100,
          }}>
            {/* Header */}
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '10px 12px',
              borderBottom: '1px solid #1e293b',
            }}>
              <span style={{
                fontSize: '11px',
                color: '#64748b',
                fontWeight: 600,
              }}>
                📋 TEMPLATES
              </span>
              <button
                onClick={() => {
                  setShowSaveDialog(true);
                }}
                disabled={selectedNodes.length !== 1}
                style={{
                  padding: '4px 8px',
                  background: selectedNodes.length === 1 ? '#22c55e' : '#1e293b',
                  border: 'none',
                  borderRadius: '4px',
                  color: selectedNodes.length === 1 ? '#fff' : '#64748b',
                  cursor: selectedNodes.length === 1 ? 'pointer' : 'not-allowed',
                  fontSize: '10px',
                  fontWeight: 600,
                }}
                title="Save selected node as template"
              >
                + Save
              </button>
            </div>

            {/* Template list */}
            {templatesLoading ? (
              <div style={{ padding: '16px', textAlign: 'center', color: '#64748b', fontSize: '12px' }}>
                Loading...
              </div>
            ) : templates.length === 0 ? (
              <div style={{ padding: '20px', textAlign: 'center', color: '#475569', fontSize: '12px' }}>
                <div>No templates yet</div>
                <div style={{ marginTop: '4px', fontSize: '10px' }}>
                  Select a node and click + Save
                </div>
              </div>
            ) : (
              templates.map((template) => (
                <div
                  key={template.id}
                  onClick={() => handleAddTemplate(template)}
                  style={{
                    padding: '10px 12px',
                    borderBottom: '1px solid #1e293b',
                    cursor: 'pointer',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#1e293b')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <div style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0' }}>
                      {template.name}
                    </span>
                    <button
                      onClick={(e) => handleDeleteTemplate(template.id, e)}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        color: '#64748b',
                        cursor: 'pointer',
                        fontSize: '10px',
                        padding: '2px 6px',
                      }}
                    >
                      ✕
                    </button>
                  </div>
                  <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px' }}>
                    {template.type} · v{template.version}
                  </div>
                  {template.tags.length > 0 && (
                    <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginTop: '4px' }}>
                      {template.tags.map((tag: string, idx: number) => (
                        <span
                          key={idx}
                          style={{
                            fontSize: '9px',
                            padding: '1px 6px',
                            background: '#0f172a',
                            color: '#94a3b8',
                            borderRadius: '3px',
                          }}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {/* Save Template Dialog */}
        {showSaveDialog && (
          <div style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0,0,0,0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 200,
          }}>
            <div style={{
              background: '#0f172a',
              border: '1px solid #1e293b',
              borderRadius: '8px',
              padding: '20px',
              width: '300px',
            }}>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#e2e8f0', marginBottom: '16px' }}>
                Save as Template
              </div>
              <input
                type="text"
                placeholder="Template name"
                value={saveForm.name}
                onChange={(e) => setSaveForm({ ...saveForm, name: e.target.value })}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#e2e8f0',
                  fontSize: '12px',
                  marginBottom: '8px',
                  boxSizing: 'border-box',
                }}
              />
              <input
                type="text"
                placeholder="Tags (comma separated)"
                value={saveForm.tags}
                onChange={(e) => setSaveForm({ ...saveForm, tags: e.target.value })}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#e2e8f0',
                  fontSize: '12px',
                  marginBottom: '16px',
                  boxSizing: 'border-box',
                }}
              />
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  onClick={handleSaveTemplate}
                  style={{
                    flex: 1,
                    padding: '10px',
                    background: '#22c55e',
                    border: 'none',
                    borderRadius: '4px',
                    color: '#fff',
                    cursor: 'pointer',
                    fontSize: '12px',
                    fontWeight: 600,
                  }}
                >
                  Save
                </button>
                <button
                  onClick={() => {
                    setShowSaveDialog(false);
                    setSaveForm({ name: '', tags: '' });
                  }}
                  style={{
                    flex: 1,
                    padding: '10px',
                    background: '#1e293b',
                    border: '1px solid #334155',
                    borderRadius: '4px',
                    color: '#94a3b8',
                    cursor: 'pointer',
                    fontSize: '12px',
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Execution status */}
      {isRunning && (
        <>
          <div style={{
            width: '1px',
            height: '24px',
            background: '#1e293b',
            margin: '0 8px',
          }} />
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            padding: '6px 12px',
            background: '#1e3a5f',
            borderRadius: '4px',
            fontSize: '11px',
            color: '#38bdf8',
          }}>
            <span style={{
              width: '8px',
              height: '8px',
              background: '#38bdf8',
              borderRadius: '50%',
              animation: 'pulse 1s infinite',
            }} />
            {execution.currentNodeId ? `Running: ${execution.currentNodeId}` : 'Running...'}
          </div>
        </>
      )}
    </div>
  );
}
