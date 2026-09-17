/**
 * Node Config Panel - Inline configuration editor for selected node
 */

import { memo, useState, useEffect } from 'react';
import { useWorkflowStore, NodeType, WorkflowNode } from '../../store/workflowStore';
import { useModelStore } from '../../store/modelStore';

interface Props {
  nodeId: string | null;
  onClose: () => void;
  onRunNode: (nodeId: string) => void;
}

const NodeConfigPanel = memo(({ nodeId, onClose, onRunNode }: Props) => {
  const nodes = useWorkflowStore((state) => state.nodes);
  const updateNode = useWorkflowStore((state) => state.updateNode);
  const lastResults = useWorkflowStore((state) => state.execution.lastExecutionResults);
  const nodeResults = useWorkflowStore((state) => state.execution.nodeResults);

  const node = nodeId ? nodes.find((n) => n.id === nodeId) : null;

  // Get conversation for LLM nodes
  const nodeResult = nodeId ? nodeResults[nodeId] : undefined;
  // For experiment nodes, use the full result; for LLM nodes, use conversation
  const conversation = nodeResult && node?.data.type === 'experiment'
    ? nodeResult
    : nodeId ? nodeResults[nodeId]?.conversation : undefined;

  // Build available variables list
  const availableVariables = Object.entries(lastResults).flatMap(([nodeId, metrics]) =>
    Object.entries(metrics || {}).map(([key, value]) => ({
      key: `nodes.${nodeId}.${key}`,
      value,
    }))
  );
  // Add variables from context nodes
  const contextNodes = nodes.filter((n) => n.data.type === 'context');
  contextNodes.forEach((ctxNode) => {
    const vars = ctxNode.data.config?.variables || {};
    Object.entries(vars).forEach(([key, value]) => {
      availableVariables.push({ key, value });
    });
  });

  const [localConfig, setLocalConfig] = useState<Record<string, unknown>>({});
  const [activeTab, setActiveTab] = useState<'config' | 'result'>('config');

  useEffect(() => {
    if (node) {
      setLocalConfig({ ...node.data.config });
    }
  }, [node?.id, node?.data?.config]);

  if (!node) return null;

  const handleConfigChange = (key: string, value: unknown) => {
    const newConfig = { ...localConfig, [key]: value };
    setLocalConfig(newConfig);
    updateNode(node.id, { config: newConfig });
  };

  const nodeTypeLabels: Record<NodeType, string> = {
    experiment: '🔬 Experiment',
    quality_gate: '✅ Quality Gate',
    decision: '🧠 LLM Decision',
    analyze: '📊 Analyze',
    adjust_params: '⚙️ Adjust Params',
    image_analysis: '🖼 Image Analysis',
    image_classification: '🧠 Image Classification',
    print: '📝 Print',
    parallel: '⚡ Parallel',
    while: '🔄 While Loop',
    notify: '📢 Notify',
    context: '📦 Context',
    code: '🐍 Code',
  };

  const nodeType = node.data.type;
  const lastResult = lastResults[nodeId || ''];

  return (
    <div
      style={{
        position: 'absolute',
        top: '16px',
        right: '16px',
        width: '320px',
        background: '#0f172a',
        border: '1px solid #1e293b',
        borderRadius: '12px',
        zIndex: 20,
        boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 16px',
        background: '#1e293b',
        borderBottom: '1px solid #334155',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '16px' }}>{nodeTypeLabels[nodeType]}</span>
          <span style={{
            fontSize: '11px',
            color: '#64748b',
            fontFamily: 'monospace',
            padding: '2px 6px',
            background: '#0f172a',
            borderRadius: '4px',
          }}>
            {node.id}
          </span>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#64748b',
            cursor: 'pointer',
            fontSize: '16px',
            padding: '4px 8px',
          }}
        >
          ✕
        </button>
      </div>

      {/* Tabs */}
      <div style={{
        display: 'flex',
        borderBottom: '1px solid #1e293b',
      }}>
        <button
          onClick={() => setActiveTab('config')}
          style={{
            flex: 1,
            padding: '10px',
            background: activeTab === 'config' ? '#1e293b' : 'transparent',
            border: 'none',
            borderBottom: activeTab === 'config' ? '2px solid #38bdf8' : '2px solid transparent',
            color: activeTab === 'config' ? '#38bdf8' : '#64748b',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
          }}
        >
          Config
        </button>
        <button
          onClick={() => setActiveTab('result')}
          style={{
            flex: 1,
            padding: '10px',
            background: activeTab === 'result' ? '#1e293b' : 'transparent',
            border: 'none',
            borderBottom: activeTab === 'result' ? '2px solid #38bdf8' : '2px solid transparent',
            color: activeTab === 'result' ? '#38bdf8' : '#64748b',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
          }}
        >
          Result
        </button>
      </div>

      {/* Content */}
      <div style={{
        padding: '16px',
        maxHeight: 'calc(100vh - 300px)',
        overflowY: 'auto',
      }}>
        {activeTab === 'config' ? (
          <ConfigForm
            nodeType={nodeType}
            config={localConfig}
            allNodes={nodes}
            onChange={handleConfigChange}
            availableVariables={availableVariables}
          />
        ) : (
          <ResultView
            nodeId={nodeId}
            nodeType={node?.data.type}
            lastResult={lastResult}
            nodeStatus={node?.data.status}
            nodeMetrics={node?.data.metrics}
            nodeError={node?.data.error}
            conversation={conversation}
            nodeResult={nodeResult}
            nodeConfig={node?.data.config}
          />
        )}
      </div>

      {/* Footer */}
      <div style={{
        display: 'flex',
        gap: '8px',
        padding: '12px 16px',
        background: '#1e293b',
        borderTop: '1px solid #334155',
      }}>
        <button
          onClick={() => onRunNode(node.id)}
          style={{
            flex: 1,
            padding: '10px',
            background: '#22c55e',
            border: 'none',
            borderRadius: '6px',
            color: '#fff',
            cursor: 'pointer',
            fontSize: '12px',
            fontWeight: 600,
          }}
        >
          ▶ Run Node
        </button>
      </div>
    </div>
  );
});

NodeConfigPanel.displayName = 'NodeConfigPanel';

// ── Variable Input with autocomplete ─────────────────────────────────

interface VariableInputProps {
  value: string;
  onChange: (value: string) => void;
  availableVariables: Array<{ key: string; value?: unknown }>;
  placeholder?: string;
  isTextArea?: boolean;
  rows?: number;
  label?: string;
}

const VariableInput = memo(({
  value,
  onChange,
  availableVariables,
  placeholder,
  isTextArea = false,
  rows = 3,
}: VariableInputProps) => {
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [showVarPicker, setShowVarPicker] = useState(false);
  const [cursorPos, setCursorPos] = useState(0);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const newValue = e.target.value;
    onChange(newValue);
    setCursorPos(e.target.selectionStart || 0);
    // Show suggestions when user types {{
    const beforeCursor = newValue.substring(0, e.target.selectionStart || 0);
    setShowSuggestions(/\{\{[^}]*$/.test(beforeCursor));
  };

  const insertVariable = (varKey: string) => {
    const before = value.substring(0, cursorPos);
    const after = value.substring(cursorPos);
    const beforeMatch = before.match(/\{\{[^}]*$/);
    const insertAt = beforeMatch ? before.length - beforeMatch[0].length : cursorPos;
    const newValue = value.substring(0, insertAt) + `{{${varKey}}}` + value.substring(cursorPos);
    onChange(newValue);
    setShowSuggestions(false);
    setShowVarPicker(false);
  };

  const commonStyle: React.CSSProperties = {
    width: '100%',
    padding: '8px 10px',
    background: '#1e293b',
    border: '1px solid #334155',
    borderRadius: '6px',
    color: '#e2e8f0',
    fontSize: '11px',
    fontFamily: 'monospace',
    boxSizing: 'border-box',
    resize: 'vertical',
  };

  return (
    <div style={{ position: 'relative' }}>
      {isTextArea ? (
        <textarea
          value={value}
          onChange={handleChange}
          placeholder={placeholder}
          rows={rows}
          style={commonStyle}
        />
      ) : (
        <input
          type="text"
          value={value}
          onChange={handleChange}
          placeholder={placeholder}
          style={commonStyle}
        />
      )}

      {/* Variable suggestions dropdown (shows when typing {{) */}
      {showSuggestions && availableVariables.length > 0 && (
        <div style={{
          position: 'absolute',
          top: '100%',
          left: 0,
          right: 0,
          maxHeight: '150px',
          overflowY: 'auto',
          background: '#0f172a',
          border: '1px solid #334155',
          borderRadius: '4px',
          zIndex: 30,
          marginTop: '4px',
          boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
        }}>
          {availableVariables.slice(0, 8).map((v, idx) => (
            <div
              key={idx}
              onClick={() => insertVariable(v.key)}
              style={{
                padding: '6px 10px',
                fontSize: '10px',
                color: '#94a3b8',
                fontFamily: 'monospace',
                cursor: 'pointer',
                borderBottom: idx < 7 ? '1px solid #1e293b' : 'none',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = '#1e3a5f';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
              }}
            >
              {`{{${v.key}}}`}
              {v.value !== undefined && (
                <span style={{ marginLeft: '8px', color: '#475569' }}>
                  = {String(v.value)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Variable hints (when not editing) */}
      {!showSuggestions && value.includes('{{') && (
        <div style={{
          marginTop: '4px',
          fontSize: '10px',
          color: '#38bdf8',
          fontFamily: 'monospace',
        }}>
          💡 Variables: {value.match(/\{\{[^}]+\}\}/g)?.join(', ')}
        </div>
      )}

      {/* Always-visible variable picker button */}
      {availableVariables.length > 0 && (
        <div style={{ marginTop: '6px' }}>
          <button
            type="button"
            onClick={() => setShowVarPicker(!showVarPicker)}
            style={{
              padding: '3px 8px',
              background: showVarPicker ? '#1e3a5f' : '#1e293b',
              border: '1px solid #334155',
              borderRadius: '4px',
              color: '#38bdf8',
              cursor: 'pointer',
              fontSize: '9px',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            <span>📋</span>
            <span>{showVarPicker ? 'Hide Variables' : 'Insert Variable'}</span>
            <span style={{ color: '#64748b', fontSize: '8px' }}>({availableVariables.length})</span>
          </button>

          {/* Variable picker dropdown */}
          {showVarPicker && (
            <div style={{
              marginTop: '4px',
              padding: '8px',
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: '4px',
              maxHeight: '120px',
              overflowY: 'auto',
            }}>
              <div style={{ fontSize: '9px', color: '#64748b', marginBottom: '6px' }}>
                Click to insert variable:
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                {availableVariables.map((v, idx) => (
                  <div
                    key={idx}
                    onClick={() => insertVariable(v.key)}
                    style={{
                      padding: '4px 8px',
                      background: '#0f172a',
                      border: '1px solid #334155',
                      borderRadius: '3px',
                      cursor: 'pointer',
                      fontSize: '9px',
                      color: '#22c55e',
                      fontFamily: 'monospace',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = '#38bdf8';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = '#334155';
                    }}
                  >
                    {v.key}
                    {v.value !== undefined && (
                      <span style={{ marginLeft: '4px', color: '#64748b' }}>
                        ={String(v.value).slice(0, 15)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
});

VariableInput.displayName = 'VariableInput';

// ── Config Form ────────────────────────────────────────────────────────────────

interface ConfigFormProps {
  nodeType: NodeType;
  config: Record<string, unknown>;
  allNodes: WorkflowNode[];
  onChange: (key: string, value: unknown) => void;
  availableVariables?: Array<{ key: string; value?: unknown }>;
}

const ConfigForm = memo(({ nodeType, config, allNodes, onChange, availableVariables = [] }: ConfigFormProps) => {
  // Fetch models from store for LLM nodes
  const { models: registeredModels, fetchModels } = useModelStore();
  useEffect(() => { fetchModels(); }, []);
  const textModels = registeredModels.filter(m => m.enabled && m.capabilities.includes('text'));
  const visionModels = registeredModels.filter(m => m.enabled && m.capabilities.includes('vision'));

  // Batch config state for experiment nodes
  const batchConfigStr = config.batchConfig as string | null;
  const [showBatchConfig, setShowBatchConfig] = useState(!!batchConfigStr);
  const [batchText, setBatchText] = useState(batchConfigStr || '');

  // Context node state
  const variables = (config.variables as Record<string, string>) || {};
  const rulesStr = String(config.rules || '[]');
  const [newVarKey, setNewVarKey] = useState('');
  const [newVarValue, setNewVarValue] = useState('');
  const [editingRules, setEditingRules] = useState(false);
  const [rulesText, setRulesText] = useState(rulesStr);
  const [editingVar, setEditingVar] = useState<string | null>(null);
  const [editingVarValue, setEditingVarValue] = useState('');

  const renderField = (key: string, value: unknown, label?: string) => {
    const displayLabel = label || key;

    if (typeof value === 'boolean') {
      return (
        <div key={key} style={{ marginBottom: '12px' }}>
          <label style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            fontSize: '11px',
            color: '#94a3b8',
            marginBottom: '4px',
          }}>
            <input
              type="checkbox"
              checked={value}
              onChange={(e) => onChange(key, e.target.checked)}
              style={{ accentColor: '#38bdf8' }}
            />
            {displayLabel}
          </label>
        </div>
      );
    }

    if (typeof value === 'number') {
      return (
        <div key={key} style={{ marginBottom: '12px' }}>
          <label style={{
            display: 'block',
            fontSize: '11px',
            color: '#94a3b8',
            marginBottom: '4px',
          }}>
            {displayLabel}
          </label>
          <input
            type="number"
            value={value}
            onChange={(e) => onChange(key, parseFloat(e.target.value) || 0)}
            style={{
              width: '100%',
              padding: '8px 10px',
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: '6px',
              color: '#e2e8f0',
              fontSize: '12px',
              fontFamily: 'monospace',
              boxSizing: 'border-box',
            }}
          />
        </div>
      );
    }

    if (typeof value === 'object' && value !== null) {
      return (
        <div key={key} style={{ marginBottom: '12px' }}>
          <label style={{
            display: 'block',
            fontSize: '11px',
            color: '#94a3b8',
            marginBottom: '4px',
          }}>
            {displayLabel}
          </label>
          <textarea
            value={JSON.stringify(value, null, 2)}
            onChange={(e) => {
              try {
                onChange(key, JSON.parse(e.target.value));
              } catch {}
            }}
            style={{
              width: '100%',
              minHeight: '80px',
              padding: '8px 10px',
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: '6px',
              color: '#e2e8f0',
              fontSize: '11px',
              fontFamily: 'monospace',
              resize: 'vertical',
              boxSizing: 'border-box',
            }}
          />
        </div>
      );
    }

    // String field - use VariableInput for message/prompt/template
    const stringValue = String(value ?? '');
    const useVariableInput = ['message', 'prompt', 'template', 'condition', 'qubit'].includes(key);

    return (
      <div key={key} style={{ marginBottom: '12px' }}>
        <label style={{
          display: 'block',
          fontSize: '11px',
          color: '#94a3b8',
          marginBottom: '4px',
        }}>
          {displayLabel}
        </label>
        {useVariableInput ? (
          <VariableInput
            value={stringValue}
            onChange={(v) => onChange(key, v)}
            availableVariables={availableVariables}
            placeholder={
              key === 'message' ? 'Use {{nodes.n1.metric}}...' :
              key === 'qubit' ? '{{qubit}}' :
              key === 'fn' ? 'sq.iqraw' :
              ''
            }
            isTextArea={key === 'message' || key === 'prompt' || key === 'template' || key === 'condition'}
            rows={key === 'condition' ? 2 : 3}
          />
        ) : (
          <input
            type="text"
            value={stringValue}
            onChange={(e) => onChange(key, e.target.value)}
            placeholder={key === 'fn' ? 'sq.iqraw' : key === 'qubit' ? '{{qubit}}' : ''}
            style={{
              width: '100%',
              padding: '8px 10px',
              background: '#1e293b',
              border: '1px solid #334155',
              borderRadius: '6px',
              color: '#e2e8f0',
              fontSize: '12px',
              fontFamily: 'monospace',
              boxSizing: 'border-box',
            }}
          />
        )}
      </div>
    );
  };

  // Node-type specific fields
  switch (nodeType) {
    case 'experiment': {
      const qubitValue = String(config.qubit || '');
      const isQubitMissing = !qubitValue.trim();

      // Get upstream Decision nodes for batch config
      const decisionNodes = allNodes.filter((n) => n.data.type === 'decision');

      const handleSaveBatchConfig = () => {
        if (batchText.trim()) {
          onChange('batchConfig', batchText);
        } else {
          onChange('batchConfig', null);
        }
        setShowBatchConfig(false);
      };

      const handleLoadFromDecision = (decisionId: string) => {
        const decisionNode = allNodes.find((n) => n.id === decisionId);
        if (decisionNode && decisionNode.data.result) {
          const result = decisionNode.data.result;
          let recommendations = result.recommendations;
          // Parse JSON string if recommendations is a string
          if (typeof recommendations === 'string' && recommendations.startsWith('[')) {
            try {
              recommendations = JSON.parse(recommendations);
            } catch {
              recommendations = [];
            }
          }
          if (recommendations && Array.isArray(recommendations)) {
            const recommendationsJson = JSON.stringify({ recommendations }, null, 2);
            setBatchText(recommendationsJson);
            onChange('batchConfig', recommendationsJson);
          }
        }
      };

      return (
        <>
          {renderField('fn', config.fn || 'sq.iqraw')}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Qubit
            </label>
            <VariableInput
              value={qubitValue}
              onChange={(v) => onChange('qubit', v)}
              availableVariables={availableVariables}
              placeholder="e.g. {{qubit}} or q3ld4"
            />
            {isQubitMissing && (
              <div style={{
                marginTop: '6px',
                padding: '6px 10px',
                background: '#3a2a1a',
                borderRadius: '4px',
                fontSize: '10px',
                color: '#f59e0b',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}>
                ⚠️ Qubit is required for experiment to run
              </div>
            )}
          </div>
          {renderField('params', config.params || {})}

          {/* Batch Experiment Config */}
          <div style={{ marginTop: '16px', paddingTop: '12px', borderTop: '1px solid #334155' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>
                🔬 Batch Experiment
              </div>
              <button
                onClick={() => setShowBatchConfig(!showBatchConfig)}
                style={{
                  padding: '4px 8px',
                  background: showBatchConfig ? '#1e3a5f' : '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#38bdf8',
                  cursor: 'pointer',
                  fontSize: '10px',
                }}
              >
                {showBatchConfig ? 'Hide' : 'Configure'}
              </button>
            </div>

            {showBatchConfig && (
              <div>
                {/* Load from Decision node */}
                {decisionNodes.length > 0 && (
                  <div style={{ marginBottom: '8px' }}>
                    <label style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px', display: 'block' }}>
                      Load recommendations from Decision node:
                    </label>
                    <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                      {decisionNodes.map((n) => (
                        <button
                          key={n.id}
                          onClick={() => handleLoadFromDecision(n.id)}
                          style={{
                            padding: '4px 8px',
                            background: '#1e293b',
                            border: '1px solid #334155',
                            borderRadius: '4px',
                            color: '#38bdf8',
                            cursor: 'pointer',
                            fontSize: '10px',
                          }}
                        >
                          {n.id}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <textarea
                  value={batchText}
                  onChange={(e) => setBatchText(e.target.value)}
                  rows={10}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: '#0f172a',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    color: '#e2e8f0',
                    fontSize: '10px',
                    fontFamily: 'monospace',
                    resize: 'vertical',
                    boxSizing: 'border-box',
                  }}
                  placeholder={'{\n  "recommendations": [\n    {"fn": "sq.iqraw", "qubit": "{{qubit}}", "params": {"do_plot": true}, "reason": "..."}\n  ]\n}'}
                />
                <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
                  <button
                    onClick={handleSaveBatchConfig}
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
                    Save Batch Config
                  </button>
                  {batchConfigStr && (
                    <button
                      onClick={() => {
                        onChange('batchConfig', null);
                        setBatchText('');
                      }}
                      style={{
                        padding: '8px',
                        background: '#f87171',
                        border: 'none',
                        borderRadius: '4px',
                        color: '#fff',
                        cursor: 'pointer',
                        fontSize: '11px',
                      }}
                    >
                      Clear
                    </button>
                  )}
                </div>
              </div>
            )}

            {!showBatchConfig && batchConfigStr && (
              <div style={{
                padding: '8px',
                background: '#1e293b',
                borderRadius: '6px',
                fontSize: '10px',
                color: '#22c55e',
              }}>
                ✓ Batch config set ({JSON.parse(batchConfigStr).recommendations?.length || 0} experiments)
              </div>
            )}
          </div>

          {/* Plot Command & Analysis Settings */}
          <div style={{ marginTop: '16px', paddingTop: '12px', borderTop: '1px solid #334155' }}>
            <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600, marginBottom: '12px' }}>
              📊 Plot Command & Analysis
            </div>

            {/* Auto Analyze Toggle */}
            <div style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={config.autoAnalyze !== false}
                  onChange={(e) => onChange('autoAnalyze', e.target.checked)}
                  style={{ width: '14px', height: '14px' }}
                />
                <span style={{ fontSize: '11px', color: '#94a3b8' }}>Auto-analyze plot with LLM</span>
              </label>
            </div>

            {/* Analysis Prompt */}
            <div style={{ marginBottom: '12px' }}>
              <label style={{
                display: 'block',
                fontSize: '11px',
                color: '#94a3b8',
                marginBottom: '4px',
              }}>
                Analysis Prompt
              </label>
              <textarea
                value={String(config.analysisPrompt || '分析这个量子比特实验图像，描述你看到的波形特征和质量')}
                onChange={(e) => onChange('analysisPrompt', e.target.value)}
                rows={3}
                style={{
                  width: '100%',
                  padding: '8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  color: '#e2e8f0',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  resize: 'vertical',
                  boxSizing: 'border-box',
                }}
                placeholder="分析这个量子比特实验图像，描述你看到的波形特征和质量"
              />
            </div>

            {/* Plot Command */}
            <div style={{ marginBottom: '12px' }}>
              <label style={{
                display: 'block',
                fontSize: '11px',
                color: '#94a3b8',
                marginBottom: '4px',
              }}>
                Plot Command (matplotlib)
              </label>
              <textarea
                value={String(config.plotCommand || '')}
                onChange={(e) => onChange('plotCommand', e.target.value)}
                rows={3}
                style={{
                  width: '100%',
                  padding: '8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  color: '#a78bfa',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  resize: 'vertical',
                  boxSizing: 'border-box',
                }}
                placeholder="# e.g.: plt.title('IQ Raw Data'); plt.grid(True)"
              />
              <div style={{ fontSize: '9px', color: '#64748b', marginTop: '4px' }}>
                Executed after experiment plot is generated
              </div>
            </div>

            {/* Analyse Command */}
            <div style={{ marginBottom: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
                <label style={{
                  display: 'block',
                  fontSize: '11px',
                  color: '#94a3b8',
                }}>
                  Analyse Command
                </label>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <input
                    type="checkbox"
                    checked={config.autoRunAnalysis !== false}
                    onChange={(e) => onChange('autoRunAnalysis', e.target.checked)}
                    style={{ width: '14px', height: '14px', accentColor: '#38bdf8' }}
                  />
                  <span style={{ fontSize: '10px', color: '#64748b' }}>Auto-run</span>
                </div>
              </div>
              <textarea
                value={String(config.analysisCommand || '')}
                onChange={(e) => onChange('analysisCommand', e.target.value)}
                rows={3}
                style={{
                  width: '100%',
                  padding: '8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  color: '#22c55e',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  resize: 'vertical',
                  boxSizing: 'border-box',
                }}
                placeholder="e.g., qter.fitData(-1, collect=True, do_plot=False)"
              />
              <div style={{ fontSize: '9px', color: '#64748b', marginTop: '4px' }}>
                Executed with do_plot=False, collect=True after experiment
              </div>
            </div>

            {/* LLM Model Selection for Analysis */}
            {config.autoAnalyze !== false && (
              <div style={{ marginBottom: '12px' }}>
                <label style={{
                  display: 'block',
                  fontSize: '11px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}>
                  Analysis Model
                </label>
                <select
                  value={String(config.model || '')}
                  onChange={(e) => onChange('model', e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    background: '#1e293b',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    color: '#e2e8f0',
                    fontSize: '11px',
                    fontFamily: 'monospace',
                    boxSizing: 'border-box',
                  }}
                >
                  <option value="">Select model...</option>
                  {/* Add currently selected model if not in list */}
                  {!!config.model && !visionModels.find((m: any) => m.name === String(config.model)) && (
                    <option key="current-model" value={String(config.model)}>{String(config.model)} (current)</option>
                  )}
                  {visionModels.map((m: any) => (
                    <option key={m.id} value={m.name}>{m.name}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
        </>
      );
    }

    case 'quality_gate':
      return (
        <>
          {/* Ref node selector */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Reference Node
            </label>
            <select
              value={String(config.ref || '')}
              onChange={(e) => onChange('ref', e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '6px',
                color: '#e2e8f0',
                fontSize: '12px',
                fontFamily: 'monospace',
                boxSizing: 'border-box',
              }}
            >
              <option value="">Select node...</option>
              {allNodes.filter(n => n.id !== config.ref).map((n) => (
                <option key={n.id} value={n.id}>{n.id}</option>
              ))}
            </select>
          </div>
          {renderField('metric', config.metric || 'SNR')}
          {renderField('threshold', config.threshold ?? 1.5)}
          {/* Direction selector */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Direction
            </label>
            <div style={{ display: 'flex', gap: '8px' }}>
              {['above', 'below'].map((dir) => (
                <button
                  key={dir}
                  onClick={() => onChange('direction', dir)}
                  style={{
                    flex: 1,
                    padding: '8px',
                    background: config.direction === dir ? '#1e3a5f' : '#1e293b',
                    border: `1px solid ${config.direction === dir ? '#38bdf8' : '#334155'}`,
                    borderRadius: '6px',
                    color: config.direction === dir ? '#38bdf8' : '#94a3b8',
                    cursor: 'pointer',
                    fontSize: '11px',
                  }}
                >
                  {dir === 'above' ? '≥ Above' : '≤ Below'}
                </button>
              ))}
            </div>
          </div>
          {/* On fail strategy */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              On Fail
            </label>
            <div style={{ display: 'flex', gap: '4px' }}>
              {['stop', 'skip', 'retry'].map((strategy) => (
                <button
                  key={strategy}
                  onClick={() => onChange('onFail', strategy)}
                  style={{
                    flex: 1,
                    padding: '6px 8px',
                    background: config.onFail === strategy ? '#1e3a5f' : '#1e293b',
                    border: `1px solid ${config.onFail === strategy ? '#38bdf8' : '#334155'}`,
                    borderRadius: '4px',
                    color: config.onFail === strategy ? '#38bdf8' : '#64748b',
                    cursor: 'pointer',
                    fontSize: '10px',
                    textTransform: 'capitalize',
                  }}
                >
                  {strategy}
                </button>
              ))}
            </div>
          </div>
        </>
      );

    case 'analyze':
      const experimentsToAnalyze = (config.experimentsToAnalyze as string[]) || [];
      const allExpTypes = ['iqraw', 't1', 'ramsey', 'piamp', 'xeb', 's21', 'spectroscopy', 'allxy', 'single_shot', 's21_dis', 'pulsed_spec', 'swap', 'drag_calibrate'];
      const source = String(config.source || 'realtime'); // "realtime" | "historical"

      return (
        <>
          {/* Source Selection */}
          <div style={{ marginBottom: '16px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '8px',
            }}>
              Data Source
            </label>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                onClick={() => onChange('source', 'realtime')}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: source === 'realtime' ? '#1e3a5f' : '#1e293b',
                  border: source === 'realtime' ? '1px solid #38bdf8' : '1px solid #334155',
                  borderRadius: '6px',
                  color: source === 'realtime' ? '#38bdf8' : '#94a3b8',
                  fontSize: '11px',
                  cursor: 'pointer',
                }}
              >
                🔄 Realtime
              </button>
              <button
                onClick={() => onChange('source', 'historical')}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: source === 'historical' ? '#1e3a5f' : '#1e293b',
                  border: source === 'historical' ? '1px solid #38bdf8' : '1px solid #334155',
                  borderRadius: '6px',
                  color: source === 'historical' ? '#38bdf8' : '#94a3b8',
                  fontSize: '11px',
                  cursor: 'pointer',
                }}
              >
                📜 Historical
              </button>
            </div>
            <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px' }}>
              {source === 'realtime' ? '分析上一个实验节点的输出' : '从 DataVault 查询历史实验数据'}
            </div>
          </div>

          {source === 'historical' ? (
            /* Historical Analysis Config */
            <>
              {/* Qubit */}
              <div style={{ marginBottom: '12px' }}>
                <label style={{
                  display: 'block',
                  fontSize: '11px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}>
                  Qubit
                </label>
                <VariableInput
                  value={String(config.qubit || '{{qubit}}')}
                  onChange={(v) => onChange('qubit', v)}
                  availableVariables={availableVariables}
                  placeholder="q1"
                />
              </div>

              {/* Experiment Type */}
              <div style={{ marginBottom: '12px' }}>
                <label style={{
                  display: 'block',
                  fontSize: '11px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}>
                  Experiment Type
                </label>
                <select
                  value={String(config.experimentType || '')}
                  onChange={(e) => onChange('experimentType', e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    background: '#1e293b',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    color: '#e2e8f0',
                    fontSize: '12px',
                    fontFamily: 'monospace',
                    boxSizing: 'border-box',
                  }}
                >
                  <option value="">All experiment types</option>
                  {allExpTypes.map((expType) => (
                    <option key={expType} value={expType}>{expType}</option>
                  ))}
                </select>
                <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px' }}>
                  空值表示查询所有类型的实验
                </div>
              </div>

              {/* Time Range */}
              <div style={{ marginBottom: '12px' }}>
                <label style={{
                  display: 'block',
                  fontSize: '11px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}>
                  Time Range
                </label>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <select
                    value={String(config.timeRange || 'count')}
                    onChange={(e) => onChange('timeRange', e.target.value)}
                    style={{
                      width: '120px',
                      padding: '8px 10px',
                      background: '#1e293b',
                      border: '1px solid #334155',
                      borderRadius: '6px',
                      color: '#e2e8f0',
                      fontSize: '12px',
                      fontFamily: 'monospace',
                      boxSizing: 'border-box',
                    }}
                  >
                    <option value="count">最近 N 次</option>
                    <option value="days">最近 N 天</option>
                  </select>
                  <input
                    type="number"
                    value={Number(config.timeValue || 10)}
                    onChange={(e) => onChange('timeValue', parseInt(e.target.value) || 10)}
                    min={1}
                    max={1000}
                    style={{
                      flex: 1,
                      padding: '8px 10px',
                      background: '#1e293b',
                      border: '1px solid #334155',
                      borderRadius: '6px',
                      color: '#e2e8f0',
                      fontSize: '12px',
                      fontFamily: 'monospace',
                      boxSizing: 'border-box',
                    }}
                  />
                </div>
              </div>

              {/* Output Variables Info */}
              <div style={{
                padding: '12px',
                background: '#1e293b',
                borderRadius: '6px',
                marginBottom: '12px',
              }}>
                <div style={{ fontSize: '11px', fontWeight: 600, color: '#94a3b8', marginBottom: '8px' }}>
                  📤 输出变量
                </div>
                <div style={{ fontSize: '10px', color: '#64748b', fontFamily: 'monospace' }}>
                  <div>• stats.{'{metric}'}.mean  — 均值</div>
                  <div>• stats.{'{metric}'}.std   — 标准差</div>
                  <div>• stats.{'{metric}'}.min   — 最小值</div>
                  <div>• stats.{'{metric}'}.max   — 最大值</div>
                  <div>• stats.{'{metric}'}.trend — 趋势 (±%变化)</div>
                  <div>• stats.{'{metric}'}.latest — 最新值</div>
                  <div>• summary — 文字总结</div>
                </div>
              </div>
            </>
          ) : (
            /* Realtime Analysis Config */
            <>
              <div style={{ marginBottom: '12px' }}>
                <label style={{
                  display: 'block',
                  fontSize: '11px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}>
                  Reference Node
                </label>
                <select
                  value={String(config.ref || '')}
                  onChange={(e) => onChange('ref', e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px 10px',
                    background: '#1e293b',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    color: '#e2e8f0',
                    fontSize: '12px',
                    fontFamily: 'monospace',
                    boxSizing: 'border-box',
                  }}
                >
                  <option value="">Select experiment node...</option>
                  {allNodes.filter(n => n.data.type === 'experiment').map((n) => (
                    <option key={n.id} value={n.id}>{n.id} ({n.data.type})</option>
                  ))}
                </select>
              </div>

              <div style={{ marginBottom: '12px' }}>
                <label style={{
                  display: 'block',
                  fontSize: '11px',
                  color: '#94a3b8',
                  marginBottom: '4px',
                }}>
                  Experiments to Analyze
                </label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginBottom: '6px' }}>
                  <button
                    onClick={() => {
                      if (experimentsToAnalyze.length === allExpTypes.length) {
                        onChange('experimentsToAnalyze', []);
                      } else {
                        onChange('experimentsToAnalyze', [...allExpTypes]);
                      }
                    }}
                    style={{
                      padding: '4px 8px',
                      background: experimentsToAnalyze.length === allExpTypes.length ? '#1e3a5f' : '#1e293b',
                      border: `1px solid ${experimentsToAnalyze.length === allExpTypes.length ? '#38bdf8' : '#334155'}`,
                      borderRadius: '4px',
                      color: experimentsToAnalyze.length === allExpTypes.length ? '#38bdf8' : '#64748b',
                      cursor: 'pointer',
                      fontSize: '10px',
                    }}
                  >
                    {experimentsToAnalyze.length === allExpTypes.length ? '✓ All' : '+ All'}
                  </button>
                  {allExpTypes.map((expType) => (
                    <button
                      key={expType}
                      onClick={() => {
                        const current = experimentsToAnalyze;
                        const newList = current.includes(expType)
                          ? current.filter((e: string) => e !== expType)
                          : [...current, expType];
                        onChange('experimentsToAnalyze', newList);
                      }}
                      style={{
                        padding: '4px 8px',
                        background: experimentsToAnalyze.includes(expType) ? '#1e3a5f' : '#1e293b',
                        border: `1px solid ${experimentsToAnalyze.includes(expType) ? '#38bdf8' : '#334155'}`,
                        borderRadius: '4px',
                        color: experimentsToAnalyze.includes(expType) ? '#38bdf8' : '#64748b',
                        cursor: 'pointer',
                        fontSize: '10px',
                      }}
                    >
                      {experimentsToAnalyze.includes(expType) ? '✓' : '+'} {expType}
                    </button>
                  ))}
                </div>
                <div style={{ fontSize: '10px', color: '#64748b' }}>
                  Leave empty or select "All" to analyze all experiments
                </div>
              </div>
            </>
          )}
        </>
      );

    case 'while':
      return (
        <>
          {renderField('condition', config.condition || '{{nodes.n1.SNR}} < 2.0')}
          {renderField('maxIterations', config.maxIterations ?? 10)}
          {renderField('timeout', config.timeout ?? 300)}
        </>
      );

    case 'notify':
      return (
        <>
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Trigger
            </label>
            <div style={{ display: 'flex', gap: '4px' }}>
              {[
                { value: 'always', label: 'Always' },
                { value: 'on-success', label: 'On Success' },
                { value: 'on-fail', label: 'On Fail' },
              ].map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => onChange('trigger', opt.value)}
                  style={{
                    flex: 1,
                    padding: '6px 8px',
                    background: config.trigger === opt.value ? '#1e3a5f' : '#1e293b',
                    border: `1px solid ${config.trigger === opt.value ? '#38bdf8' : '#334155'}`,
                    borderRadius: '4px',
                    color: config.trigger === opt.value ? '#38bdf8' : '#64748b',
                    cursor: 'pointer',
                    fontSize: '10px',
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
          {renderField('template', config.template || '')}
        </>
      );

    case 'adjust_params':
      return (
        <>
          {renderField('param', config.param || 'fread')}
          {renderField('value', config.value || '')}
          {renderField('qubit', config.qubit || '{{qubit}}')}
        </>
      );

    case 'decision': {
      const mode = String(config.mode || 'analysis');
      const rulesContextId = String(config.rulesContextId || '');
      const intentPrompt = String(config.intentPrompt || '分析需求并生成实验列表');
      const systemPrompt = String(config.systemPrompt || '');
      const model = String(config.model || '');
      const temperature = Number(config.temperature ?? 0.3);
      const maxTokens = Number(config.maxTokens ?? 1000);
      const symptomOutputVar = String(config.symptomOutputVar || 'symptom');
      const recommendationsOutputVar = String(config.recommendationsOutputVar || 'recommendations');

      // Get all context nodes for rules selection
      const contextNodes = allNodes.filter((n) => n.data.type === 'context');

      return (
        <>
          {/* Mode Selection */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Mode
            </label>
            <div style={{ display: 'flex', gap: '8px' }}>
              <button
                onClick={() => onChange('mode', 'analysis')}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: mode === 'analysis' ? '#1e3a5f' : '#1e293b',
                  border: mode === 'analysis' ? '1px solid #38bdf8' : '1px solid #334155',
                  borderRadius: '6px',
                  color: mode === 'analysis' ? '#38bdf8' : '#94a3b8',
                  fontSize: '11px',
                  cursor: 'pointer',
                }}
              >
                📊 Analysis
              </button>
              <button
                onClick={() => onChange('mode', 'intent')}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: mode === 'intent' ? '#1e3a5f' : '#1e293b',
                  border: mode === 'intent' ? '1px solid #38bdf8' : '1px solid #334155',
                  borderRadius: '6px',
                  color: mode === 'intent' ? '#38bdf8' : '#94a3b8',
                  fontSize: '11px',
                  cursor: 'pointer',
                }}
              >
                🎯 Intent
              </button>
            </div>
            <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px' }}>
              {mode === 'analysis' ? '分析实验结果，给出诊断建议' : '将自然语言需求转化为实验列表'}
            </div>
          </div>

          {/* Rules Context Node (for analysis mode) */}
          {mode === 'analysis' && (
            <div style={{ marginBottom: '12px' }}>
              <label style={{
                display: 'block',
                fontSize: '11px',
                color: '#94a3b8',
                marginBottom: '4px',
              }}>
                Rules Source (Context Node)
              </label>
              <select
                value={rulesContextId}
                onChange={(e) => onChange('rulesContextId', e.target.value)}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  color: '#e2e8f0',
                  fontSize: '12px',
                  fontFamily: 'monospace',
                  boxSizing: 'border-box',
                }}
              >
                <option value="">Select Context node with rules...</option>
                {contextNodes.map((n) => (
                  <option key={n.id} value={n.id}>{n.id} (rules)</option>
                ))}
              </select>
              {contextNodes.length === 0 && (
                <div style={{
                  marginTop: '6px',
                  padding: '6px 10px',
                  background: '#3a2a1a',
                  borderRadius: '4px',
                  fontSize: '10px',
                  color: '#f59e0b',
                }}>
                  ⚠️ No Context nodes found. Add a Context node with rules first.
                </div>
              )}
            </div>
          )}

          {/* Intent Prompt (for intent mode) */}
          {mode === 'intent' && (
            <VariableInput
              value={intentPrompt}
              onChange={(v) => onChange('intentPrompt', v)}
              availableVariables={availableVariables}
              placeholder="用户的需求描述，如：帮我校准这个量子比特"
              isTextArea={true}
              rows={3}
              label="User Intent"
            />
          )}

          {/* Model selection */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Model
            </label>
            <select
              value={model}
              onChange={(e) => onChange('model', e.target.value)}
              style={{
                width: '100%',
                padding: '8px 10px',
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '6px',
                color: '#e2e8f0',
                fontSize: '12px',
                fontFamily: 'monospace',
                boxSizing: 'border-box',
              }}
            >
              <option value="">Select model...</option>
              {/* Add currently selected model if not in list (handles case where models haven't loaded yet) */}
              {model && !textModels.find(m => m.name === model) && (
                <option key="current-model" value={model}>{model} (current)</option>
              )}
              {textModels.map((m) => (
                <option key={m.id} value={m.name}>{m.name}</option>
              ))}
            </select>
            {textModels.length === 0 && (
              <div style={{
                marginTop: '6px',
                padding: '6px 10px',
                background: '#3a2a1a',
                borderRadius: '4px',
                fontSize: '10px',
                color: '#f59e0b',
              }}>
                ⚠️ No text-capable models registered. <button onClick={() => window.dispatchEvent(new CustomEvent('qmclaw:open-model-registry'))} style={{ background: 'none', border: 'none', color: '#38bdf8', cursor: 'pointer', textDecoration: 'underline', fontSize: '10px', padding: 0 }}>Open Model Registry</button>
              </div>
            )}
          </div>

          {/* Temperature */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              <span>Temperature</span>
              <span style={{ color: '#38bdf8' }}>{temperature}</span>
            </label>
            <input
              type="range"
              min="0"
              max="2"
              step="0.1"
              value={temperature}
              onChange={(e) => onChange('temperature', parseFloat(e.target.value))}
              style={{
                width: '100%',
                accentColor: '#38bdf8',
              }}
            />
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: '9px',
              color: '#475569',
              marginTop: '2px',
            }}>
              <span>Precise</span>
              <span>Creative</span>
            </div>
          </div>

          {/* Max Tokens */}
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Max Tokens
            </label>
            <input
              type="number"
              value={maxTokens}
              onChange={(e) => onChange('maxTokens', parseInt(e.target.value) || 1000)}
              style={{
                width: '100%',
                padding: '8px 10px',
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '6px',
                color: '#e2e8f0',
                fontSize: '12px',
                fontFamily: 'monospace',
                boxSizing: 'border-box',
              }}
            />
          </div>

          {/* Output Variable Names */}
          <div style={{
            marginBottom: '12px',
            padding: '12px',
            background: '#1e293b',
            borderRadius: '6px',
            border: '1px solid #334155',
          }}>
            <div style={{
              fontSize: '11px',
              fontWeight: 600,
              color: '#94a3b8',
              marginBottom: '8px',
            }}>
              📤 Output Variable Names
            </div>
            <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '8px' }}>
              Custom names for LLM outputs — downstream nodes can reference via {'{{nodes.{nodeId}.varName}}'}
            </div>

            {/* Symptom Output Variable */}
            <div style={{ marginBottom: '8px' }}>
              <label style={{
                display: 'block',
                fontSize: '10px',
                color: '#f472b6',
                marginBottom: '4px',
              }}>
                Symptom Variable
              </label>
              <input
                type="text"
                value={symptomOutputVar}
                onChange={(e) => onChange('symptomOutputVar', e.target.value || 'symptom')}
                placeholder="symptom"
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#e2e8f0',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                  boxSizing: 'border-box',
                }}
              />
            </div>

            {/* Recommendations Output Variable */}
            <div style={{ marginBottom: '8px' }}>
              <label style={{
                display: 'block',
                fontSize: '10px',
                color: '#22c55e',
                marginBottom: '4px',
              }}>
                Recommendations Variable
              </label>
              <input
                type="text"
                value={recommendationsOutputVar}
                onChange={(e) => onChange('recommendationsOutputVar', e.target.value || 'recommendations')}
                placeholder="recommendations"
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#e2e8f0',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                  boxSizing: 'border-box',
                }}
              />
            </div>

            {/* Reasoning Output Variable */}
            <div>
              <label style={{
                display: 'block',
                fontSize: '10px',
                color: '#38bdf8',
                marginBottom: '4px',
              }}>
                Reasoning Variable
              </label>
              <input
                type="text"
                value={String(config.reasoningOutputVar || 'reasoning')}
                onChange={(e) => onChange('reasoningOutputVar', e.target.value || 'reasoning')}
                placeholder="reasoning"
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#e2e8f0',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                  boxSizing: 'border-box',
                }}
              />
            </div>
          </div>

          {/* System Prompt */}
          <VariableInput
            value={systemPrompt}
            onChange={(v) => onChange('systemPrompt', v)}
            availableVariables={availableVariables}
            placeholder="Leave empty to use default prompt based on mode"
            isTextArea={true}
            rows={3}
            label="System Prompt (Optional)"
          />

          {/* API Key info */}
          <div style={{
            marginTop: '12px',
            padding: '8px',
            background: '#1e293b',
            borderRadius: '6px',
            fontSize: '10px',
            color: '#64748b',
          }}>
            💡 Uses API keys from Model Registry
          </div>
        </>
      );
    }

    case 'image_analysis': {
      const currentModel = String(config.model || '');

      return (
        <>
          {renderField('prompt', config.prompt || 'Analyze this plot', 'Analysis Prompt')}
          {renderField('imagePath', config.imagePath || '', 'Image Path (supports {{variable}})')}
          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '11px', color: '#94a3b8', marginBottom: '4px', display: 'block' }}>Model</label>
            <select
              value={currentModel}
              onChange={(e) => onChange('model', e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '6px',
                color: '#e2e8f0',
                fontSize: '12px',
              }}
            >
              <option value="">Select model...</option>
              {/* Add currently selected model if not in list */}
              {currentModel && !visionModels.find((m) => m.name === currentModel) && (
                <option key="current-model" value={currentModel}>{currentModel} (current)</option>
              )}
              {visionModels.map((m) => <option key={m.id} value={m.name}>{m.name}</option>)}
            </select>
            {visionModels.length === 0 && (
              <div style={{
                marginTop: '6px',
                padding: '6px 10px',
                background: '#3a2a1a',
                borderRadius: '4px',
                fontSize: '10px',
                color: '#f59e0b',
              }}>
                ⚠️ No vision-capable models registered. <button onClick={() => window.dispatchEvent(new CustomEvent('qmclaw:open-model-registry'))} style={{ background: 'none', border: 'none', color: '#38bdf8', cursor: 'pointer', textDecoration: 'underline', fontSize: '10px', padding: 0 }}>Open Model Registry</button>
              </div>
            )}
          </div>
          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '11px', color: '#94a3b8', marginBottom: '4px', display: 'block' }}>System Prompt</label>
            <VariableInput
              value={String(config.systemPrompt || '')}
              onChange={(v) => onChange('systemPrompt', v)}
              availableVariables={availableVariables}
              placeholder="You are an expert in quantum physics..."
              isTextArea={true}
              rows={3}
            />
          </div>
          <div style={{
            padding: '10px',
            background: '#1e293b',
            borderRadius: '6px',
            fontSize: '10px',
            color: '#64748b',
          }}>
            💡 Uses OPENAI_API_KEY / MINIMAX_API_KEY environment variable
          </div>
        </>
      );
    }

    case 'image_classification': {
      return (
        <>
          {renderField('qubit', config.qubit || '', 'Qubit ID (e.g. q10lu1)')}
          {renderField('experimentType', config.experimentType || 'spectroscopy', 'Experiment Type')}
          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '11px', color: '#94a3b8', marginBottom: '4px', display: 'block' }}>Backend</label>
            <select
              value={String(config.backend || 'pytorch')}
              onChange={(e) => onChange('backend', e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: '#1e293b',
                border: '1px solid #334155',
                borderRadius: '6px',
                color: '#e2e8f0',
                fontSize: '12px',
              }}
            >
              <option value="pytorch">PyTorch (default)</option>
              <option value="onnx">ONNX Runtime</option>
              <option value="quantized">INT8 Quantized</option>
            </select>
          </div>
          {renderField('reviewThreshold', String(config.reviewThreshold ?? 0.75), 'Review Threshold (0-1)')}
          {renderField('marginThreshold', String(config.marginThreshold ?? 0.15), 'Margin Threshold (0-1)')}
        </>
      );
    }

    case 'parallel':
      return (
        <>
          <div style={{
            padding: '12px',
            background: '#1e293b',
            borderRadius: '6px',
            fontSize: '11px',
            color: '#64748b',
          }}>
            <div>Mode: auto (parallel by dependency)</div>
            <div>Wait for: all (AND merge)</div>
          </div>
        </>
      );

    case 'code': {
      return (
        <>
          <div style={{ marginBottom: '12px' }}>
            <label style={{
              display: 'block',
              fontSize: '11px',
              color: '#94a3b8',
              marginBottom: '4px',
            }}>
              Python Code
            </label>
            <textarea
              value={String(config.code || '')}
              onChange={(e) => onChange('code', e.target.value)}
              rows={10}
              style={{
                width: '100%',
                padding: '8px',
                background: '#0f172a',
                border: '1px solid #334155',
                borderRadius: '6px',
                color: '#22d3ee',
                fontSize: '11px',
                fontFamily: 'monospace',
                resize: 'vertical',
                boxSizing: 'border-box',
              }}
              placeholder={`# Python code here\n# Available: json, re, math, np (numpy), datetime, time\n# Use {{variable}} syntax for workflow variables\n\nresult = {"status": "ok", "snr": {{nodes.exp_1.SNR}}}`}
            />
          </div>

          <div style={{ display: 'flex', gap: '12px', marginBottom: '12px' }}>
            <div style={{ flex: 1 }}>
              <label style={{
                display: 'block',
                fontSize: '11px',
                color: '#94a3b8',
                marginBottom: '4px',
              }}>
                Timeout (seconds)
              </label>
              <input
                type="number"
                value={Number(config.timeout ?? 30) as number}
                onChange={(e) => onChange('timeout', parseInt(e.target.value) || 30)}
                min={1}
                max={300}
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  color: '#e2e8f0',
                  fontSize: '12px',
                  boxSizing: 'border-box',
                }}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={{
                display: 'block',
                fontSize: '11px',
                color: '#94a3b8',
                marginBottom: '4px',
              }}>
                Return Variable
              </label>
              <input
                type="text"
                value={String(config.returnVariable || 'result')}
                onChange={(e) => onChange('returnVariable', e.target.value)}
                placeholder="result"
                style={{
                  width: '100%',
                  padding: '8px 10px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  color: '#e2e8f0',
                  fontSize: '12px',
                  fontFamily: 'monospace',
                  boxSizing: 'border-box',
                }}
              />
            </div>
          </div>

          <div style={{
            marginTop: '12px',
            padding: '8px',
            background: '#1e293b',
            borderRadius: '6px',
            fontSize: '10px',
            color: '#64748b',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '4px', color: '#94a3b8' }}>
              Available Modules: json, re, math, numpy (as np), datetime, time, random
            </div>
            <div style={{ color: '#f87171' }}>
              ❌ Forbidden: os, sys, subprocess, socket, file I/O, network access
            </div>
          </div>

          <div style={{
            marginTop: '12px',
            padding: '8px',
            background: '#1e293b',
            borderRadius: '6px',
            fontSize: '10px',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '4px', color: '#94a3b8' }}>
              Available Workflow Variables:
            </div>
            <div style={{ fontFamily: 'monospace', color: '#22d3ee', wordBreak: 'break-all' }}>
              {availableVariables.length > 0
                ? availableVariables.map(v => `{{${v.key}}}`).join(', ')
                : 'No variables available'}
            </div>
          </div>
        </>
      );
    }

    case 'context': {
      // Parse rules for display
      let rules: Array<{
        name: string;
        condition: string;
        symptom: string;
        recommendations: Array<{fn: string; reason: string; priority: number}>;
      }> = [];
      try {
        const parsed = JSON.parse(rulesStr);
        rules = parsed.rules || parsed || [];
      } catch {}

      const handleAddVariable = () => {
        if (newVarKey.trim()) {
          onChange('variables', { ...variables, [newVarKey.trim()]: newVarValue });
          setNewVarKey('');
          setNewVarValue('');
        }
      };

      const handleRemoveVariable = (key: string) => {
        const newVars = { ...variables };
        delete newVars[key];
        onChange('variables', newVars);
      };

      const handleSaveRules = () => {
        onChange('rules', rulesText);
        setEditingRules(false);
      };

      return (
        <>
          <div style={{
            marginBottom: '12px',
            padding: '8px',
            background: '#1e293b',
            borderRadius: '6px',
            fontSize: '11px',
            color: '#38bdf8',
          }}>
            💡 Define variables that other nodes can reference using {'{{variableName}}'}
          </div>

          {/* Existing variables */}
          {Object.entries(variables).map(([key, value]) => (
            <div key={key} style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              marginBottom: '8px',
              padding: '8px',
              background: '#1e293b',
              borderRadius: '4px',
            }}>
              <div style={{ fontSize: '12px', color: '#94a3b8', fontFamily: 'monospace', minWidth: '60px' }}>{key}</div>
              <input
                type="text"
                value={editingVar === key ? editingVarValue : String(value)}
                onChange={(e) => {
                  setEditingVar(key);
                  setEditingVarValue(e.target.value);
                }}
                onBlur={() => {
                  if (editingVar === key && editingVarValue !== value) {
                    onChange('variables', { ...variables, [key]: editingVarValue });
                  }
                  setEditingVar(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    if (editingVarValue !== value) {
                      onChange('variables', { ...variables, [key]: editingVarValue });
                    }
                    setEditingVar(null);
                  }
                  if (e.key === 'Escape') {
                    setEditingVar(null);
                    setEditingVarValue(String(value));
                  }
                }}
                style={{
                  flex: 1,
                  padding: '4px 6px',
                  background: '#0f172a',
                  border: '1px solid #334155',
                  borderRadius: '3px',
                  color: '#22c55e',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                }}
              />
              <button
                onClick={() => handleRemoveVariable(key)}
                style={{
                  padding: '4px 8px',
                  background: 'transparent',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#f87171',
                  cursor: 'pointer',
                  fontSize: '10px',
                }}
              >
                ✕
              </button>
            </div>
          ))}

          {/* Add new variable */}
          <div style={{ marginTop: '12px' }}>
            <div style={{ fontSize: '11px', color: '#64748b', marginBottom: '6px' }}>Add Variable</div>
            <div style={{ display: 'flex', gap: '6px', marginBottom: '6px' }}>
              <input
                type="text"
                placeholder="key"
                value={newVarKey}
                onChange={(e) => setNewVarKey(e.target.value)}
                style={{
                  flex: 1,
                  padding: '6px 8px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#e2e8f0',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                }}
              />
              <input
                type="text"
                placeholder="value"
                value={newVarValue}
                onChange={(e) => setNewVarValue(e.target.value)}
                style={{
                  flex: 1,
                  padding: '6px 8px',
                  background: '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#e2e8f0',
                  fontSize: '11px',
                  fontFamily: 'monospace',
                }}
              />
            </div>
            <button
              onClick={handleAddVariable}
              style={{
                width: '100%',
                padding: '6px',
                background: '#22c55e',
                border: 'none',
                borderRadius: '4px',
                color: '#fff',
                cursor: 'pointer',
                fontSize: '11px',
                fontWeight: 600,
              }}
            >
              + Add
            </button>
          </div>

          {/* Rules Section */}
          <div style={{ marginTop: '16px', paddingTop: '12px', borderTop: '1px solid #334155' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <div style={{ fontSize: '12px', color: '#94a3b8', fontWeight: 600 }}>
                📋 Rules (for Decision Node)
              </div>
              <button
                onClick={() => setEditingRules(!editingRules)}
                style={{
                  padding: '4px 8px',
                  background: editingRules ? '#1e3a5f' : '#1e293b',
                  border: '1px solid #334155',
                  borderRadius: '4px',
                  color: '#38bdf8',
                  cursor: 'pointer',
                  fontSize: '10px',
                }}
              >
                {editingRules ? 'Cancel' : 'Edit'}
              </button>
            </div>

            {editingRules ? (
              <div>
                <textarea
                  value={rulesText}
                  onChange={(e) => setRulesText(e.target.value)}
                  rows={12}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: '#0f172a',
                    border: '1px solid #334155',
                    borderRadius: '6px',
                    color: '#e2e8f0',
                    fontSize: '10px',
                    fontFamily: 'monospace',
                    resize: 'vertical',
                    boxSizing: 'border-box',
                  }}
                  placeholder={'[\n  {\n    "name": "snr_low",\n    "condition": "SNR < 1.5",\n    "symptom": "信噪比偏低",\n    "recommendations": [\n      {"fn": "sq.iqraw", "reason": "重新测量评估", "priority": 1}\n    ]\n  }\n]'}
                />
                <button
                  onClick={handleSaveRules}
                  style={{
                    marginTop: '8px',
                    width: '100%',
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
                  Save Rules
                </button>
              </div>
            ) : (
              <div>
                {rules.length === 0 ? (
                  <div style={{
                    padding: '12px',
                    background: '#1e293b',
                    borderRadius: '6px',
                    fontSize: '11px',
                    color: '#64748b',
                    textAlign: 'center',
                  }}>
                    No rules defined. Click Edit to add rules.
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {rules.map((rule, i) => (
                      <div key={i} style={{
                        padding: '8px',
                        background: '#1e293b',
                        borderRadius: '6px',
                        border: '1px solid #334155',
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                          <span style={{ fontSize: '11px', color: '#f472b6', fontWeight: 600 }}>{rule.name}</span>
                          <span style={{ fontSize: '10px', color: '#64748b' }}>if {rule.condition}</span>
                        </div>
                        <div style={{ fontSize: '10px', color: '#94a3b8', marginBottom: '4px' }}>{rule.symptom}</div>
                        <div style={{ fontSize: '10px', color: '#22c55e' }}>
                          → {rule.recommendations?.map((r) => r.fn).join(', ')}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      );
    }

    case 'print':
    default:
      return (
        <>
          {renderField('message', config.message || 'Step completed')}
        </>
      );
  }
});

ConfigForm.displayName = 'ConfigForm';

// ── Result View ────────────────────────────────────────────────────────────────

interface ExperimentAnalysisResult {
  prompt: string;
  result: string;
  model: string;
  provider: string;
  messagesSent?: Array<{role: string; content: string}>;
  rawResponse?: Record<string, unknown>;
}

interface ConversationResult {
  model?: string;
  provider?: string;
  systemPrompt?: string;
  prompt?: string;
  context?: string;
  messagesSent?: Array<{role: string; content: string}>;
  rawResponse?: Record<string, unknown>;
  decision?: string;
  reasoning?: string;
  // Analysis can be string (Decision node) or object (Experiment node)
  analysis?: string | ExperimentAnalysisResult;
  // Decision node specific fields
  symptom?: string;
  recommendations?: Array<{
    fn: string;
    qubit?: string;
    params?: Record<string, unknown>;
    reason?: string;
  }>;
  matchedRules?: string[];
  // Experiment node additional fields
  callCode?: string;
  plotCommand?: string;
  modifiedPlotPath?: string;
  plotError?: string;
  analysisError?: string;
  batchResults?: Array<{
    fn: string;
    qubit: string;
    reason: string;
    params: Record<string, unknown>;
    callCode: string;
    stdout: string;
    plotPath?: string;
    modifiedPlotPath?: string;
    metrics?: Record<string, number>;
    analysis?: ExperimentAnalysisResult;
    plotError?: string;
    analysisError?: string;
  }>;
}

interface ResultViewProps {
  nodeId: string | null;
  nodeType?: string;
  lastResult?: Record<string, unknown>;
  nodeStatus?: string;
  nodeMetrics?: Record<string, number>;
  nodeError?: string;
  conversation?: ConversationResult;
  nodeResult?: {
    stdout?: string;
    metrics?: Record<string, number>;
    callCode?: string;
    [key: string]: unknown;
  };
  nodeConfig?: Record<string, unknown>;
}

// Helper component for displaying key-value pairs
const KeyValueRow = ({ label, value, valueColor = '#22c55e' }: { label: string; value: string; valueColor?: string }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px', alignItems: 'flex-start' }}>
    <span style={{ fontSize: '10px', color: '#94a3b8', minWidth: '80px', flexShrink: 0 }}>{label}</span>
    <span style={{ fontSize: '10px', color: valueColor, fontFamily: 'monospace', textAlign: 'right', wordBreak: 'break-all' }}>
      {value}
    </span>
  </div>
);

// Section component with header
const ResultSection = ({ title, icon, children, accentColor = '#38bdf8' }: {
  title: string;
  icon: string;
  children: React.ReactNode;
  accentColor?: string;
}) => (
  <div style={{ marginBottom: '16px' }}>
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '6px',
      marginBottom: '8px',
      paddingBottom: '6px',
      borderBottom: `1px solid ${accentColor}33`,
    }}>
      <span style={{ fontSize: '12px' }}>{icon}</span>
      <span style={{ fontSize: '11px', color: accentColor, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        {title}
      </span>
    </div>
    {children}
  </div>
);

// Status badge component
const StatusBadge = ({ status }: { status?: string }) => {
  const colors: Record<string, { bg: string; text: string }> = {
    completed: { bg: '#1e3a2f', text: '#22c55e' },
    passed: { bg: '#1e3a2f', text: '#22c55e' },
    failed: { bg: '#3a1e1e', text: '#f87171' },
    running: { bg: '#1e3a5f', text: '#38bdf8' },
    idle: { bg: '#1e293b', text: '#64748b' },
    skipped: { bg: '#2d2d1e', text: '#f59e0b' },
  };
  const color = colors[status || 'idle'] || colors.idle;
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '4px',
      padding: '2px 8px',
      borderRadius: '4px',
      background: color.bg,
      color: color.text,
      fontSize: '10px',
      fontFamily: 'monospace',
      fontWeight: 600,
    }}>
      {status === 'running' && (
        <span style={{
          width: '6px',
          height: '6px',
          background: color.text,
          borderRadius: '50%',
          animation: 'pulse 1s infinite',
        }} />
      )}
      {status || 'idle'}
    </span>
  );
};

const ResultView = memo(({ nodeId, nodeType, lastResult, nodeStatus, nodeMetrics, nodeError, conversation, nodeResult, nodeConfig }: ResultViewProps) => {
  // Helper to extract node config for display
  const getNodeConfigForDisplay = (): Record<string, unknown> => {
    // This will be passed from parent or we can access it via store
    return {};
  };

  // Helper to format values
  const formatValue = (value: unknown): string => {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'object') return JSON.stringify(value, null, 2).slice(0, 200);
    return String(value);
  };

  // Helper to format metrics
  const formatMetrics = (metrics?: Record<string, number>) => {
    if (!metrics || Object.keys(metrics).length === 0) return null;
    return Object.entries(metrics).map(([key, val]) => (
      <KeyValueRow key={key} label={key} value={typeof val === 'number' ? val.toFixed(4) : String(val)} />
    ));
  };

  // Helper to format execution time from nodeResult
  const formatExecutionTime = () => {
    if (!nodeResult || typeof nodeResult !== 'object') return null;
    const result = nodeResult as Record<string, unknown>;
    const startedAt = result.startedAt as string | undefined;
    const completedAt = result.completedAt as string | undefined;
    const duration = result.duration as number | undefined;

    if (startedAt) {
      const start = new Date(startedAt);
      const timeStr = start.toLocaleTimeString();
      const dateStr = start.toLocaleDateString();

      return (
        <div style={{ fontSize: '10px', color: '#64748b' }}>
          <span style={{ marginRight: '8px' }}>🕐 {dateStr} {timeStr}</span>
          {duration !== undefined && (
            <span style={{ color: '#94a3b8' }}>({duration}ms)</span>
          )}
        </div>
      );
    }
    return null;
  };

  // Helper to extract context variables from conversation
  const getContextFromConversation = (): Record<string, string> => {
    if (!conversation?.context) return {};
    try {
      return JSON.parse(conversation.context);
    } catch {
      return {};
    }
  };

  // Render based on node type
  const renderInputOutput = () => {
    switch (nodeType) {
      case 'context': {
        // Context node: input is empty, output is the defined variables
        // Variables are stored in config.variables
        const configVariables = (nodeConfig?.variables || lastResult?.variables) as Record<string, string> | undefined;
        const variables = configVariables || {};

        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                (No input - Context nodes define variables)
              </div>
            </ResultSection>
            <ResultSection title="Output" icon="📦" accentColor="#22c55e">
              {Object.keys(variables).length > 0 ? (
                Object.entries(variables).map(([key, val]) => (
                  <KeyValueRow key={key} label={key} value={String(val)} />
                ))
              ) : (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  No variables defined yet
                </div>
              )}
            </ResultSection>
          </>
        );
      }

      case 'experiment': {
        // Experiment node: input = qubit, fn, params; output = metrics, analysis
        const callCode = nodeResult?.callCode || (conversation as any)?.batchResults?.[0]?.callCode;
        const batchResults = (conversation as any)?.batchResults;
        const analysisResult = conversation?.analysis;
        const hasBatch = batchResults && Array.isArray(batchResults) && batchResults.length > 0;

        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {callCode ? (
                <div style={{
                  padding: '8px',
                  background: '#0f172a',
                  borderRadius: '4px',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  color: '#38bdf8',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}>
                  {callCode}
                </div>
              ) : (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  Run the node to see input
                </div>
              )}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {/* Metrics */}
              {nodeMetrics && Object.keys(nodeMetrics).length > 0 && (
                <div style={{ marginBottom: '12px' }}>
                  <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px' }}>Metrics:</div>
                  {formatMetrics(nodeMetrics)}
                </div>
              )}

              {/* Batch summary */}
              {hasBatch && (
                <div style={{ marginBottom: '8px' }}>
                  <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px' }}>
                    Batch ({batchResults.length} experiments):
                  </div>
                  {batchResults.slice(0, 3).map((exp: any, i: number) => (
                    <div key={i} style={{
                      padding: '6px 8px',
                      background: '#0f172a',
                      borderRadius: '4px',
                      marginBottom: '4px',
                      fontSize: '10px',
                    }}>
                      <span style={{ color: '#38bdf8' }}>{i + 1}.</span>{' '}
                      <span style={{ fontFamily: 'monospace', color: '#e2e8f0' }}>{exp.fn}</span>
                      {exp.metrics && Object.keys(exp.metrics).length > 0 && (
                        <span style={{ color: '#22c55e', marginLeft: '8px' }}>
                          {Object.entries(exp.metrics).map(([k, v]) => `${k}=${(v as number).toFixed(2)}`).join(', ')}
                        </span>
                      )}
                    </div>
                  ))}
                  {batchResults.length > 3 && (
                    <div style={{ fontSize: '9px', color: '#64748b', textAlign: 'center' }}>
                      +{batchResults.length - 3} more...
                    </div>
                  )}
                </div>
              )}

              {/* Image Analysis Result */}
              {analysisResult && typeof analysisResult === 'object' && (analysisResult as ExperimentAnalysisResult).result && (
                <div>
                  <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px' }}>Analysis:</div>
                  <div style={{
                    padding: '8px',
                    background: '#1a1a2e',
                    borderRadius: '4px',
                    fontSize: '10px',
                    color: '#e2e8f0',
                    lineHeight: '1.4',
                    maxHeight: '150px',
                    overflow: 'hidden',
                  }}>
                    {(analysisResult as ExperimentAnalysisResult).result}
                  </div>
                </div>
              )}

              {!nodeMetrics && !hasBatch && !analysisResult && (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  Run the node to see output
                </div>
              )}
            </ResultSection>
          </>
        );
      }

      case 'quality_gate': {
        // Quality Gate: input = ref node metrics, output = pass/fail
        const ref = lastResult?.ref;
        const metric = lastResult?.metric;
        const threshold = lastResult?.threshold;
        const actualValue = lastResult?.actualValue;

        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              <KeyValueRow label="Reference" value={ref ? String(ref) : '—'} />
              <KeyValueRow label="Metric" value={metric ? String(metric) : '—'} />
              <KeyValueRow label="Threshold" value={threshold !== undefined ? String(threshold) : '—'} />
              {actualValue !== undefined && (
                <KeyValueRow label="Actual Value" value={String(actualValue)} valueColor="#38bdf8" />
              )}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontSize: '12px' }}>Result:</span>
                <StatusBadge status={nodeStatus} />
              </div>
            </ResultSection>
          </>
        );
      }

      case 'decision': {
        // Decision node: input = context/rules, output = symptom/recommendations
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {conversation?.model && <KeyValueRow label="Model" value={conversation.model} />}
              {conversation?.matchedRules && conversation.matchedRules.length > 0 && (
                <div style={{ marginTop: '4px' }}>
                  <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px' }}>Matched Rules:</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                    {conversation.matchedRules.map((rule, i) => (
                      <span key={i} style={{
                        padding: '2px 6px',
                        background: '#1e3a5f',
                        borderRadius: '3px',
                        fontSize: '9px',
                        color: '#38bdf8',
                      }}>
                        {rule}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {conversation?.symptom && (
                <div style={{ marginBottom: '8px' }}>
                  <div style={{ fontSize: '10px', color: '#f472b6', marginBottom: '4px' }}>🎯 Symptom:</div>
                  <div style={{
                    padding: '8px',
                    background: '#2d1a2e',
                    borderRadius: '4px',
                    fontSize: '10px',
                    color: '#e2e8f0',
                  }}>
                    {conversation.symptom}
                  </div>
                </div>
              )}

              {conversation?.recommendations && Array.isArray(conversation.recommendations) && conversation.recommendations.length > 0 && (
                <div>
                  <div style={{ fontSize: '10px', color: '#64748b', marginBottom: '4px' }}>Recommendations:</div>
                  {conversation.recommendations.map((rec: any, i: number) => (
                    <div key={i} style={{
                      padding: '6px 8px',
                      background: '#0f172a',
                      borderRadius: '4px',
                      marginBottom: '4px',
                      borderLeft: '2px solid #22c55e',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span style={{ fontSize: '9px', color: '#64748b' }}>{i + 1}.</span>
                        <span style={{ fontSize: '11px', fontFamily: 'monospace', color: '#22c55e' }}>{rec.fn}</span>
                      </div>
                      {rec.reason && (
                        <div style={{ fontSize: '9px', color: '#94a3b8', marginTop: '2px', marginLeft: '18px' }}>
                          {rec.reason}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {!conversation?.symptom && !conversation?.recommendations?.length && (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  Run the node to see recommendations
                </div>
              )}
            </ResultSection>
          </>
        );
      }

      case 'image_analysis': {
        // Image Analysis: input = imagePath/prompt, output = analysis result
        const analysis = conversation?.analysis;

        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {conversation?.prompt && <KeyValueRow label="Prompt" value={conversation.prompt} valueColor="#a78bfa" />}
              {conversation?.model && <KeyValueRow label="Model" value={conversation.model} />}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {analysis && typeof analysis === 'object' && (analysis as ExperimentAnalysisResult).result ? (
                <div style={{
                  padding: '10px',
                  background: '#1a1a2e',
                  borderRadius: '4px',
                  fontSize: '11px',
                  color: '#e2e8f0',
                  lineHeight: '1.5',
                }}>
                  {(analysis as ExperimentAnalysisResult).result}
                </div>
              ) : (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  Run the node to see analysis
                </div>
              )}
            </ResultSection>
          </>
        );
      }

      case 'print': {
        // Print node: input = message, output = stdout
        const printMessage = lastResult?.message as string | undefined;
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {printMessage && <KeyValueRow label="Message" value={String(printMessage)} valueColor="#94a3b8" />}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {nodeResult?.stdout || lastResult?.stdout ? (
                <div style={{
                  padding: '8px',
                  background: '#0f172a',
                  borderRadius: '4px',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  color: '#22c55e',
                  whiteSpace: 'pre-wrap',
                }}>
                  {nodeResult?.stdout || String(lastResult?.stdout || '')}
                </div>
              ) : (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  Run the node to see output
                </div>
              )}
            </ResultSection>
          </>
        );
      }

      case 'notify': {
        // Notify node: input = template, output = send status
        const notifyTemplate = lastResult?.template as string | undefined;
        const notifyTrigger = lastResult?.trigger as string | undefined;
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {notifyTemplate && <KeyValueRow label="Template" value={String(notifyTemplate)} valueColor="#94a3b8" />}
              {notifyTrigger && <KeyValueRow label="Trigger" value={String(notifyTrigger)} />}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              <StatusBadge status={nodeStatus} />
            </ResultSection>
          </>
        );
      }

      case 'image_classification': {
        // Image Classification: input = qubit/experiment_type, output = label/confidence/margin
        const config = (lastResult?.config || {}) as Record<string, unknown>;
        const metrics = lastResult?.metrics as { label?: string; confidence?: number; margin?: number; needReview?: boolean } | undefined;
        const imagePath = lastResult?.imagePath as string | undefined;
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {config.qubit ? <KeyValueRow label="Qubit" value={String(config.qubit)} /> : null}
              {config.experimentType ? <KeyValueRow label="Experiment" value={String(config.experimentType)} /> : null}
              {config.backend ? <KeyValueRow label="Backend" value={String(config.backend)} /> : null}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {metrics?.label && (
                <KeyValueRow
                  label="Label"
                  value={metrics.label === 'class_1' ? '🔴 class_1' : metrics.label === 'class_0' ? '🔵 class_0' : metrics.label}
                  valueColor={metrics.needReview ? '#f59e0b' : '#22c55e'}
                />
              )}
              {metrics?.confidence != null && <KeyValueRow label="Confidence" value={`${(metrics.confidence * 100).toFixed(1)}%`} />}
              {metrics?.margin != null && <KeyValueRow label="Margin" value={metrics.margin.toFixed(3)} />}
              {metrics?.needReview && (
                <div style={{ fontSize: '10px', color: '#f59e0b', padding: '4px 8px', background: '#3a2a1a', borderRadius: '4px', marginTop: '4px' }}>
                  ⚠️ 需要复核
                </div>
              )}
              {imagePath && (
                <div style={{ fontSize: '9px', color: '#64748b', marginTop: '6px', wordBreak: 'break-all' }}>
                  📷 {imagePath}
                </div>
              )}
              <StatusBadge status={nodeStatus} />
            </ResultSection>
          </>
        );
      }

      case 'adjust_params': {
        // Adjust Params: input = param/value, output = updated param
        const adjParam = lastResult?.param as string | undefined;
        const adjValue = lastResult?.value as string | undefined;
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {adjParam && <KeyValueRow label="Param" value={String(adjParam)} />}
              {adjValue && <KeyValueRow label="Value" value={String(adjValue)} valueColor="#38bdf8" />}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              <StatusBadge status={nodeStatus} />
            </ResultSection>
          </>
        );
      }

      case 'code': {
        // Code node: input = referenced variables, output = result
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {nodeResult?.callCode && (
                <div style={{
                  padding: '8px',
                  background: '#0f172a',
                  borderRadius: '4px',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  color: '#22d3ee',
                  whiteSpace: 'pre-wrap',
                  maxHeight: '100px',
                  overflow: 'hidden',
                }}>
                  {String(nodeResult.callCode).slice(0, 300)}
                </div>
              )}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {nodeMetrics && Object.keys(nodeMetrics).length > 0 ? (
                formatMetrics(nodeMetrics)
              ) : nodeResult?.stdout || lastResult?.stdout ? (
                <div style={{
                  padding: '8px',
                  background: '#0f172a',
                  borderRadius: '4px',
                  fontSize: '10px',
                  fontFamily: 'monospace',
                  color: '#22c55e',
                  whiteSpace: 'pre-wrap',
                }}>
                  {nodeResult?.stdout || String(lastResult?.stdout || '')}
                </div>
              ) : (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  Run the node to see output
                </div>
              )}
            </ResultSection>
          </>
        );
      }

      case 'while': {
        // While Loop: input = condition, output = iterations/final state
        const whileCondition = lastResult?.condition as string | undefined;
        const whileMaxIterations = lastResult?.maxIterations as number | undefined;
        const whileIterations = lastResult?.iterations as number | undefined;
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {whileCondition && <KeyValueRow label="Condition" value={String(whileCondition)} valueColor="#f59e0b" />}
              {whileMaxIterations !== undefined && <KeyValueRow label="Max Iterations" value={String(whileMaxIterations)} />}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {whileIterations !== undefined && (
                <KeyValueRow label="Iterations" value={String(whileIterations)} valueColor="#38bdf8" />
              )}
              <StatusBadge status={nodeStatus} />
            </ResultSection>
          </>
        );
      }

      case 'analyze': {
        // Analyze node: input = reference experiment, output = analysis
        const analyzeRef = lastResult?.ref as string | undefined;
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {analyzeRef && <KeyValueRow label="Reference" value={String(analyzeRef)} />}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              {nodeMetrics && Object.keys(nodeMetrics).length > 0 ? (
                formatMetrics(nodeMetrics)
              ) : (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  Run the node to see output
                </div>
              )}
            </ResultSection>
          </>
        );
      }

      case 'parallel': {
        // Parallel: input = dependencies, output = all results
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                (Dependencies determined by workflow edges)
              </div>
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              <StatusBadge status={nodeStatus} />
            </ResultSection>
          </>
        );
      }

      default: {
        // Generic fallback for unknown node types
        return (
          <>
            <ResultSection title="Input" icon="📥" accentColor="#64748b">
              {lastResult && Object.keys(lastResult).length > 0 ? (
                Object.entries(lastResult).map(([key, val]) => (
                  <KeyValueRow key={key} label={key} value={formatValue(val)} />
                ))
              ) : (
                <div style={{ fontSize: '10px', color: '#475569', fontStyle: 'italic' }}>
                  No input data
                </div>
              )}
            </ResultSection>

            <ResultSection title="Output" icon="📤" accentColor="#22c55e">
              <StatusBadge status={nodeStatus} />
            </ResultSection>
          </>
        );
      }
    }
  };

  const hasResult = lastResult || nodeMetrics || conversation;

  return (
    <div>
      {/* Status Section */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '8px 10px',
        background: '#1e293b',
        borderRadius: '6px',
        marginBottom: '12px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '10px', color: '#64748b' }}>Status:</span>
          <StatusBadge status={nodeStatus} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '2px' }}>
          {nodeMetrics && Object.keys(nodeMetrics).length > 0 && (
            <span style={{ fontSize: '10px', color: '#22c55e' }}>
              ✓ {Object.keys(nodeMetrics).length} metrics
            </span>
          )}
          {formatExecutionTime()}
        </div>
      </div>

      {/* Error */}
      {nodeError && (
        <div style={{
          padding: '12px',
          background: '#3a1e1e',
          borderRadius: '6px',
          marginBottom: '12px',
          borderLeft: '3px solid #f87171',
        }}>
          <div style={{ fontSize: '11px', color: '#f87171', marginBottom: '4px', fontWeight: 600 }}>
            Error
          </div>
          <div style={{ fontSize: '10px', color: '#f87171', fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
            {nodeError.slice(0, 300)}
          </div>
        </div>
      )}

      {/* Input/Output Sections */}
      {renderInputOutput()}

      {/* Raw Details (collapsible) */}
      {hasResult && (
        <details style={{ marginTop: '16px' }}>
          <summary style={{
            fontSize: '10px',
            color: '#64748b',
            cursor: 'pointer',
            padding: '4px 0',
            userSelect: 'none',
          }}>
            🔧 Raw Details
          </summary>
          <div style={{
            marginTop: '8px',
            padding: '8px',
            background: '#0f172a',
            borderRadius: '4px',
            fontSize: '9px',
            fontFamily: 'monospace',
            color: '#475569',
            whiteSpace: 'pre-wrap',
            maxHeight: '200px',
            overflow: 'auto',
          }}>
            {JSON.stringify({ lastResult, nodeMetrics, conversation }, null, 2)}
          </div>
        </details>
      )}

      {/* Empty state */}
      {!hasResult && !nodeError && (
        <div style={{
          textAlign: 'center',
          padding: '32px 16px',
          color: '#475569',
        }}>
          <div style={{ fontSize: '28px', marginBottom: '8px' }}>📊</div>
          <div style={{ fontSize: '12px', fontWeight: 500, marginBottom: '4px' }}>
            No execution result yet
          </div>
          <div style={{ fontSize: '11px' }}>
            Run this node to see input/output
          </div>
        </div>
      )}
    </div>
  );
});

ResultView.displayName = 'ResultView';

export default NodeConfigPanel;
