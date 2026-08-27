/**
 * Template Library - Side panel for managing node templates
 *
 * Shows saved templates, allows saving current node as template,
 * and dragging templates onto the canvas.
 */

import { memo, useState, useEffect } from 'react';
import { api } from '../../../lib/api';
import { useWorkflowStore, NodeType } from '../../../store/workflowStore';

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
  onClose?: () => void;
}

const TemplateLibrary = memo(({ onClose }: Props) => {
  const [templates, setTemplates] = useState<NodeTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveForm, setSaveForm] = useState({
    name: '',
    tags: '',
  });

  const nodes = useWorkflowStore((state) => state.nodes);
  const selectedNodes = useWorkflowStore((state) => state.selectedNodes);
  const addNode = useWorkflowStore((state) => state.addNode);

  // Load templates on mount
  useEffect(() => {
    loadTemplates();
  }, []);

  const loadTemplates = async () => {
    setLoading(true);
    try {
      const data = await api.listTemplates();
      // Add createdAt/updatedAt if missing
      const templates: NodeTemplate[] = data.map((t: any) => ({
        ...t,
        createdAt: t.createdAt || new Date().toISOString(),
        updatedAt: t.updatedAt || new Date().toISOString(),
      }));
      setTemplates(templates);
    } catch (e: any) {
      console.error('Failed to load templates:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleAddTemplate = (template: NodeTemplate) => {
    // Add node from template
    addNode(template.type as NodeType, {
      x: 300 + Math.random() * 200,
      y: 200 + Math.random() * 200,
    });
    // Note: The config will be default. User can edit after.
    console.log('Added template:', template.name);
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
    } catch (e: any) {
      alert('Failed to save template: ' + e.message);
    }
  };

  const handleDeleteTemplate = async (id: string) => {
    if (!confirm('Delete this template?')) return;
    try {
      await api.deleteTemplate(id);
      await loadTemplates();
    } catch (e: any) {
      alert('Failed to delete template: ' + e.message);
    }
  };

  return (
    <div style={{
      position: 'absolute',
      bottom: '16px',
      left: '16px',
      width: '280px',
      maxHeight: '300px',
      background: '#0f172a',
      border: '1px solid #1e293b',
      borderRadius: '8px',
      zIndex: 10,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '8px 12px',
        background: '#1e293b',
        borderBottom: '1px solid #334155',
      }}>
        <span style={{
          fontSize: '11px',
          fontWeight: 600,
          color: '#94a3b8',
          letterSpacing: '0.05em',
        }}>
          📋 TEMPLATES ({templates.length})
        </span>
        <div style={{ display: 'flex', gap: '4px' }}>
          <button
            onClick={() => setShowSaveDialog(true)}
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
          {onClose && (
            <button
              onClick={onClose}
              style={{
                padding: '4px 8px',
                background: 'transparent',
                border: 'none',
                color: '#64748b',
                cursor: 'pointer',
                fontSize: '12px',
              }}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Templates list */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '8px',
      }}>
        {loading ? (
          <div style={{ color: '#64748b', fontSize: '11px', textAlign: 'center', padding: '20px' }}>
            Loading...
          </div>
        ) : templates.length === 0 ? (
          <div style={{ color: '#475569', fontSize: '11px', textAlign: 'center', padding: '20px' }}>
            <div>No templates yet</div>
            <div style={{ marginTop: '4px', fontSize: '10px' }}>
              Select a node and click + Save
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {templates.map((template) => (
              <div
                key={template.id}
                style={{
                  padding: '8px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
                onClick={() => handleAddTemplate(template)}
              >
                <div style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}>
                  <span style={{
                    fontSize: '11px',
                    fontWeight: 600,
                    color: '#e2e8f0',
                  }}>
                    {template.name}
                  </span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleDeleteTemplate(template.id);
                    }}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: '#64748b',
                      cursor: 'pointer',
                      fontSize: '10px',
                    }}
                  >
                    ✕
                  </button>
                </div>
                <div style={{
                  fontSize: '10px',
                  color: '#64748b',
                  marginTop: '2px',
                }}>
                  {template.type} · v{template.version}
                </div>
                {template.tags.length > 0 && (
                  <div style={{
                    display: 'flex',
                    gap: '4px',
                    flexWrap: 'wrap',
                    marginTop: '4px',
                  }}>
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
            ))}
          </div>
        )}
      </div>

      {/* Save Dialog */}
      {showSaveDialog && (
        <div style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0,0,0,0.8)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 20,
        }}>
          <div style={{
            background: '#0f172a',
            border: '1px solid #1e293b',
            borderRadius: '8px',
            padding: '16px',
            width: '90%',
          }}>
            <div style={{ fontSize: '13px', fontWeight: 600, color: '#e2e8f0', marginBottom: '12px' }}>
              Save as Template
            </div>
            <input
              type="text"
              placeholder="Template name"
              value={saveForm.name}
              onChange={(e) => setSaveForm({ ...saveForm, name: e.target.value })}
              style={{
                width: '100%',
                padding: '6px 10px',
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '4px',
                color: '#e2e8f0',
                fontSize: '11px',
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
                padding: '6px 10px',
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '4px',
                color: '#e2e8f0',
                fontSize: '11px',
                marginBottom: '12px',
                boxSizing: 'border-box',
              }}
            />
            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                onClick={handleSaveTemplate}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: '#22c55e',
                  border: 'none',
                  borderRadius: '4px',
                  color: '#fff',
                  cursor: 'pointer',
                  fontSize: '11px',
                  fontWeight: 600,
                }}
              >
                Save
              </button>
              <button
                onClick={() => setShowSaveDialog(false)}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#94a3b8',
                  cursor: 'pointer',
                  fontSize: '11px',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
});

TemplateLibrary.displayName = 'TemplateLibrary';

export default TemplateLibrary;