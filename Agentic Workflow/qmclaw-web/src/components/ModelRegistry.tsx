/**
 * Model Registry Component - Full-featured model management with integrated chat testing
 *
 * Tabs: Models | Chat Test | History
 */

"use client";

import { useState, useEffect, useRef } from 'react';
import { useModelStore } from '../store/modelStore';
import { api } from '../lib/api';
import type { LLMModel, ChatSession, ChatMessage } from '../types/model';

interface Props {
  onClose: () => void;
}

type Tab = 'models' | 'chat' | 'history';

/** Parse thinking tags from MiniMax/M2.7 responses */
function parseThinkingContent(content: string): { thinking: string | null; answer: string } {
  // Use string regex to avoid SWC compilation issues with special characters
  const thinkRegex = new RegExp('<think>([\\s\\S]*?)</think>', '');
  const thinkMatch = content.match(thinkRegex);
  if (thinkMatch) {
    const thinking = thinkMatch[1].trim();
    const removeThinkRegex = new RegExp('<think>[\\s\\S]*?</think>', 'g');
    const answer = content.replace(removeThinkRegex, '').trim();
    return { thinking, answer };
  }
  return { thinking: null, answer: content };
}

/** Render message content with thinking tags styled differently */
function MessageContent({ content }: { content: string }) {
  const { thinking, answer } = parseThinkingContent(content);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {thinking && (
        <div style={{
          background: '#1a2744',
          border: '1px solid #334155',
          borderRadius: '8px',
          padding: '8px 10px',
          fontSize: '11px',
          color: '#94a3b8',
          fontStyle: 'italic',
        }}>
          <div style={{ color: '#38bdf8', fontWeight: 500, marginBottom: '4px', fontStyle: 'normal' }}>
            🤖 Thinking:
          </div>
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.4' }}>{thinking}</div>
        </div>
      )}
      {answer && (
        <div style={{ whiteSpace: 'pre-wrap' }}>{answer}</div>
      )}
    </div>
  );
}

export default function ModelRegistry({ onClose }: Props) {
  const { models, isLoading, error, fetchModels, addModel, updateModel, deleteModel } = useModelStore();
  const [activeTab, setActiveTab] = useState<Tab>('models');
  const [editingModel, setEditingModel] = useState<LLMModel | null>(null);

  useEffect(() => {
    fetchModels();
  }, []);

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      background: 'rgba(0,0,0,0.7)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 100,
    }}>
      <div style={{
        width: '900px',
        maxHeight: '85vh',
        background: '#0f172a',
        border: '1px solid #334155',
        borderRadius: '12px',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}>
        {/* Header */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 20px',
          background: '#1e293b',
          borderBottom: '1px solid #334155',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span style={{ fontSize: '20px' }}>🤖</span>
            <h2 style={{ margin: 0, fontSize: '18px', color: '#e2e8f0' }}>Model Registry</h2>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#64748b',
              cursor: 'pointer',
              fontSize: '20px',
              padding: '4px 8px',
            }}
          >
            ✕
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', borderBottom: '1px solid #1e293b' }}>
          {(['models', 'chat', 'history'] as Tab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                flex: 1,
                padding: '12px',
                background: activeTab === tab ? '#1e293b' : 'transparent',
                border: 'none',
                borderBottom: activeTab === tab ? '2px solid #38bdf8' : '2px solid transparent',
                color: activeTab === tab ? '#38bdf8' : '#64748b',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: 600,
                textTransform: 'capitalize',
              }}
            >
              {tab === 'models' && '📋 '}
              {tab === 'chat' && '💬 '}
              {tab === 'history' && '📜 '}
              {tab}
            </button>
          ))}
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
          {isLoading && <div style={{ textAlign: 'center', color: '#64748b', padding: '40px' }}>Loading...</div>}
          {error && <div style={{ padding: '12px', background: '#3a1e1e', borderRadius: '8px', color: '#f87171', marginBottom: '16px' }}>{error}</div>}

          {activeTab === 'models' && (
            <ModelsTab
              models={models}
              onEdit={setEditingModel}
              onDelete={deleteModel}
              onAdd={() => setEditingModel({} as LLMModel)}
            />
          )}

          {activeTab === 'chat' && (
            <ChatTab models={models.filter(m => m.enabled)} />
          )}

          {activeTab === 'history' && (
            <HistoryTab models={models} />
          )}
        </div>

        {/* Model Edit Modal */}
        {editingModel && (
          <ModelEditModal
            model={editingModel}
            onSave={async (data) => {
              if (editingModel.id) {
                await updateModel(editingModel.id, data);
              } else {
                await addModel(data);
              }
              setEditingModel(null);
            }}
            onClose={() => setEditingModel(null)}
          />
        )}
      </div>
    </div>
  );
}

// ── Models Tab ────────────────────────────────────────────────────────────────

function ModelsTab({
  models,
  onEdit,
  onDelete,
  onAdd,
}: {
  models: LLMModel[];
  onEdit: (model: LLMModel) => void;
  onDelete: (id: string) => void;
  onAdd: () => void;
}) {
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <div style={{ fontSize: '13px', color: '#64748b' }}>
          {models.length} model(s) registered
        </div>
        <button onClick={onAdd} style={{
          padding: '8px 16px',
          background: '#22c55e',
          border: 'none',
          borderRadius: '6px',
          color: '#fff',
          cursor: 'pointer',
          fontSize: '13px',
          fontWeight: 600,
        }}>
          + Add Model
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {models.map((model) => (
          <div
            key={model.id}
            style={{
              padding: '16px',
              background: '#1e293b',
              borderRadius: '8px',
              border: '1px solid #334155',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 600, color: '#e2e8f0' }}>{model.name}</span>
                  <span style={{
                    fontSize: '10px',
                    padding: '2px 8px',
                    borderRadius: '4px',
                    background: model.enabled ? '#1e3a2f' : '#3a1e1e',
                    color: model.enabled ? '#22c55e' : '#f87171',
                  }}>
                    {model.enabled ? 'Enabled' : 'Disabled'}
                  </span>
                </div>
                <div style={{ fontSize: '12px', color: '#64748b', fontFamily: 'monospace' }}>
                  {model.provider} / {model.modelId}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  onClick={() => onEdit(model)}
                  style={{
                    padding: '6px 12px',
                    background: '#1e3a5f',
                    border: '1px solid #38bdf8',
                    borderRadius: '4px',
                    color: '#38bdf8',
                    cursor: 'pointer',
                    fontSize: '12px',
                  }}
                >
                  Edit
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Delete "${model.name}"?`)) onDelete(model.id);
                  }}
                  style={{
                    padding: '6px 12px',
                    background: 'transparent',
                    border: '1px solid #f87171',
                    borderRadius: '4px',
                    color: '#f87171',
                    cursor: 'pointer',
                    fontSize: '12px',
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
            <div style={{ marginTop: '8px', display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
              {model.capabilities.map((cap) => (
                <span
                  key={cap}
                  style={{
                    fontSize: '10px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: '#0f172a',
                    color: '#94a3b8',
                  }}
                >
                  {cap}
                </span>
              ))}
              {model.config.temperature !== undefined && (
                <span style={{ fontSize: '10px', padding: '2px 6px', background: '#0f172a', color: '#64748b' }}>
                  T: {model.config.temperature}
                </span>
              )}
            </div>
          </div>
        ))}
        {models.length === 0 && (
          <div style={{ textAlign: 'center', color: '#475569', padding: '60px 20px' }}>
            <div style={{ fontSize: '32px', marginBottom: '12px' }}>📋</div>
            <div>No models registered yet</div>
            <div style={{ fontSize: '12px', marginTop: '8px', color: '#64748b' }}>
              Click "Add Model" to register your first LLM
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Model Edit Modal ──────────────────────────────────────────────────────────

function ModelEditModal({
  model,
  onSave,
  onClose,
}: {
  model: LLMModel | null;
  onSave: (data: Omit<LLMModel, 'id' | 'createdAt' | 'updatedAt'>) => void;
  onClose: () => void;
}) {
  const [formData, setFormData] = useState({
    name: model?.name || '',
    provider: model?.provider || 'openai',
    modelId: model?.modelId || '',
    baseUrl: model?.baseUrl || '',
    enabled: model?.enabled ?? true,
    capabilities: model?.capabilities || ['text'] as ('text' | 'vision' | 'function_calling')[],
    config: {
      temperature: model?.config?.temperature ?? 0.3,
      maxTokens: model?.config?.maxTokens ?? 500,
      systemPrompt: model?.config?.systemPrompt || '',
    },
  });

  const inputStyle: React.CSSProperties = {
    width: '100%',
    padding: '10px 12px',
    background: '#0f172a',
    border: '1px solid #334155',
    borderRadius: '6px',
    color: '#e2e8f0',
    fontSize: '12px',
    boxSizing: 'border-box',
  };

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      background: 'rgba(0,0,0,0.5)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 110,
    }}>
      <div style={{
        width: '500px',
        maxHeight: '90vh',
        background: '#0f172a',
        border: '1px solid #334155',
        borderRadius: '12px',
        overflow: 'auto',
      }}>
        <div style={{
          padding: '16px 20px',
          background: '#1e293b',
          borderBottom: '1px solid #334155',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <h3 style={{ margin: 0, fontSize: '16px', color: '#e2e8f0' }}>
            {model?.id ? 'Edit Model' : 'Add Model'}
          </h3>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: '18px' }}>✕</button>
        </div>

        <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Name *</label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="GPT-4o Production"
              style={inputStyle}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Provider *</label>
            <select
              value={formData.provider}
              onChange={(e) => setFormData({ ...formData, provider: e.target.value as any })}
              style={inputStyle}
            >
              <option value="openai">OpenAI</option>
              <option value="minimax">MiniMax</option>
              <option value="anthropic">Anthropic</option>
              <option value="deepseek">DeepSeek</option>
            </select>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Model ID *</label>
            <input
              type="text"
              value={formData.modelId}
              onChange={(e) => setFormData({ ...formData, modelId: e.target.value })}
              placeholder="gpt-4o"
              style={inputStyle}
            />
            <div style={{ fontSize: '10px', color: '#64748b', marginTop: '4px' }}>
              The actual API model identifier (e.g., gpt-4o, claude-3-5-sonnet-20241022)
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Base URL (optional)</label>
            <input
              type="text"
              value={formData.baseUrl}
              onChange={(e) => setFormData({ ...formData, baseUrl: e.target.value })}
              placeholder="https://api.openai.com/v1"
              style={inputStyle}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '8px' }}>Capabilities</label>
            <div style={{ display: 'flex', gap: '16px' }}>
              {(['text', 'vision', 'function_calling'] as const).map((cap) => (
                <label key={cap} style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#94a3b8', fontSize: '12px', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={formData.capabilities.includes(cap)}
                    onChange={(e) => {
                      const newCaps = e.target.checked
                        ? [...formData.capabilities, cap]
                        : formData.capabilities.filter((c) => c !== cap);
                      setFormData({ ...formData, capabilities: newCaps });
                    }}
                  />
                  {cap}
                </label>
              ))}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <div>
              <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Temperature</label>
              <input
                type="number"
                value={formData.config.temperature}
                onChange={(e) => setFormData({
                  ...formData,
                  config: { ...formData.config, temperature: parseFloat(e.target.value) },
                })}
                min={0}
                max={2}
                step={0.1}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Max Tokens</label>
              <input
                type="number"
                value={formData.config.maxTokens}
                onChange={(e) => setFormData({
                  ...formData,
                  config: { ...formData.config, maxTokens: parseInt(e.target.value) || 500 },
                })}
                style={inputStyle}
              />
            </div>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: '12px', color: '#94a3b8', marginBottom: '6px' }}>Default System Prompt</label>
            <textarea
              value={formData.config.systemPrompt}
              onChange={(e) => setFormData({
                ...formData,
                config: { ...formData.config, systemPrompt: e.target.value },
              })}
              placeholder="Optional default system prompt for this model"
              rows={3}
              style={{ ...inputStyle, resize: 'vertical', minHeight: '60px', fontFamily: 'inherit' }}
            />
          </div>

          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#94a3b8', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={formData.enabled}
                onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
              />
              Enabled
            </label>
          </div>

          <div style={{
            padding: '12px',
            background: '#1e293b',
            borderRadius: '6px',
            fontSize: '11px',
            color: '#64748b',
          }}>
            💡 API Key: Configure in environment variables (OPENAI_API_KEY, MINIMAX_API_KEY, etc.)
          </div>

          <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end', marginTop: '8px' }}>
            <button onClick={onClose} style={{
              padding: '10px 20px',
              background: 'transparent',
              border: '1px solid #334155',
              borderRadius: '6px',
              color: '#94a3b8',
              cursor: 'pointer',
              fontSize: '13px',
            }}>
              Cancel
            </button>
            <button
              onClick={() => {
                if (!formData.name || !formData.modelId) {
                  alert('Name and Model ID are required');
                  return;
                }
                onSave(formData);
              }}
              style={{
                padding: '10px 20px',
                background: '#22c55e',
                border: 'none',
                borderRadius: '6px',
                color: '#fff',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: 600,
              }}
            >
              {model?.id ? 'Update' : 'Create'} Model
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Chat Tab ─────────────────────────────────────────────────────────────────

function ChatTab({ models }: { models: LLMModel[] }) {
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<ChatSession | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const currentModel = models.find(m => m.name === selectedModel);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSelectModel = async (modelName: string) => {
    setSelectedModel(modelName);
    setMessages([]);
    setError(null);

    const model = models.find(m => m.name === modelName);
    if (model) {
      try {
        const newSession = await api.createChatSession(model.id, model.name) as ChatSession;
        setSession(newSession);
      } catch (err: any) {
        console.error('Failed to create session:', err);
      }
    }
  };

  const handleSend = async () => {
    if (!input.trim() || !selectedModel || isLoading) return;

    const userMessage: ChatMessage = {
      role: 'user',
      content: input,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);
    setError(null);

    try {
      const result = await api.testModel({
        modelName: selectedModel,
        message: input,
        systemPrompt: currentModel?.config?.systemPrompt,
        temperature: currentModel?.config?.temperature,
        maxTokens: currentModel?.config?.maxTokens,
        sessionId: session?.id,
      });

      const assistantMessage: ChatMessage = {
        role: 'assistant',
        content: result.content,
        timestamp: new Date().toISOString(),
      };

      setMessages((prev) => [...prev, assistantMessage]);
    } catch (err: any) {
      setError(err.message || 'Failed to get response');
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Model selector */}
      <div style={{ marginBottom: '16px' }}>
        <select
          value={selectedModel}
          onChange={(e) => handleSelectModel(e.target.value)}
          style={{
            width: '100%',
            padding: '10px 12px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '6px',
            color: '#e2e8f0',
            fontSize: '13px',
          }}
        >
          <option value="">Select a model to test...</option>
          {models.map((model) => (
            <option key={model.id} value={model.name}>
              {model.name} ({model.provider})
            </option>
          ))}
        </select>
      </div>

      {/* Messages */}
      <div style={{
        flex: 1,
        minHeight: '300px',
        maxHeight: '400px',
        overflow: 'auto',
        background: '#1e293b',
        borderRadius: '8px',
        padding: '16px',
        marginBottom: '16px',
      }}>
        {!selectedModel && (
          <div style={{ textAlign: 'center', color: '#475569', marginTop: '120px' }}>
            Select a model above to start chatting
          </div>
        )}

        {selectedModel && messages.length === 0 && (
          <div style={{ textAlign: 'center', color: '#475569', marginTop: '120px' }}>
            Send a message to test the model
          </div>
        )}

        {messages.map((msg, idx) => (
          <div
            key={idx}
            style={{
              marginBottom: '16px',
              display: 'flex',
              flexDirection: 'column',
              alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
            }}
          >
            <div style={{
              maxWidth: '80%',
              padding: '12px 16px',
              borderRadius: '12px',
              background: msg.role === 'user' ? '#1e3a5f' : '#0f172a',
              color: '#e2e8f0',
              fontSize: '13px',
              lineHeight: '1.5',
              border: msg.role === 'assistant' ? '1px solid #334155' : 'none',
            }}>
              <MessageContent content={msg.content} />
            </div>
            <div style={{ fontSize: '10px', color: '#475569', marginTop: '4px' }}>
              {new Date(msg.timestamp).toLocaleTimeString()}
            </div>
          </div>
        ))}

        {isLoading && (
          <div style={{ textAlign: 'center', color: '#38bdf8', padding: '20px' }}>
            🤖 Thinking...
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Error */}
      {error && (
        <div style={{
          padding: '12px',
          background: '#3a1e1e',
          borderRadius: '8px',
          color: '#f87171',
          fontSize: '12px',
          marginBottom: '12px',
        }}>
          {error}
        </div>
      )}

      {/* Input */}
      <div style={{ display: 'flex', gap: '12px' }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyPress={handleKeyPress}
          placeholder={selectedModel ? 'Type your message...' : 'Select a model first'}
          disabled={!selectedModel || isLoading}
          style={{
            flex: 1,
            padding: '12px',
            background: '#1e293b',
            border: '1px solid #334155',
            borderRadius: '8px',
            color: '#e2e8f0',
            fontSize: '13px',
            resize: 'none',
            minHeight: '60px',
            fontFamily: 'inherit',
          }}
        />
        <button
          onClick={handleSend}
          disabled={!selectedModel || isLoading || !input.trim()}
          style={{
            padding: '12px 24px',
            background: selectedModel && !isLoading ? '#22c55e' : '#334155',
            border: 'none',
            borderRadius: '8px',
            color: '#fff',
            cursor: selectedModel && !isLoading ? 'pointer' : 'not-allowed',
            fontSize: '14px',
            fontWeight: 600,
            alignSelf: 'flex-end',
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}

// ── History Tab ───────────────────────────────────────────────────────────────

function HistoryTab({ models }: { models: LLMModel[] }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [selectedSession, setSelectedSession] = useState<ChatSession | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    loadSessions();
  }, []);

  const loadSessions = async () => {
    setIsLoading(true);
    try {
      const data = await api.listChatSessions() as ChatSession[];
      setSessions(data);
    } catch (err) {
      console.error('Failed to load sessions:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this chat session?')) return;
    try {
      await api.deleteChatSession(id);
      setSessions(sessions.filter(s => s.id !== id));
      if (selectedSession?.id === id) setSelectedSession(null);
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  };

  const handleLoadSession = async (id: string) => {
    try {
      const session = await api.getChatSession(id) as ChatSession;
      setSelectedSession(session);
    } catch (err) {
      console.error('Failed to load session:', err);
    }
  };

  return (
    <div style={{ display: 'flex', gap: '20px' }}>
      {/* Session list */}
      <div style={{ width: '280px', flexShrink: 0 }}>
        <div style={{ fontSize: '13px', color: '#64748b', marginBottom: '12px' }}>
          {sessions.length} session(s)
        </div>

        {isLoading && <div style={{ color: '#64748b' }}>Loading...</div>}

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {sessions.map((session) => (
            <div
              key={session.id}
              onClick={() => handleLoadSession(session.id)}
              style={{
                padding: '12px',
                background: selectedSession?.id === session.id ? '#1e3a5f' : '#1e293b',
                borderRadius: '6px',
                border: selectedSession?.id === session.id ? '1px solid #38bdf8' : '1px solid #334155',
                cursor: 'pointer',
              }}
            >
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#e2e8f0', marginBottom: '4px' }}>
                {session.modelName}
              </div>
              <div style={{ fontSize: '10px', color: '#64748b' }}>
                {session.messages.length} messages
              </div>
              <div style={{ fontSize: '10px', color: '#64748b' }}>
                {new Date(session.updatedAt).toLocaleString()}
              </div>
            </div>
          ))}
          {sessions.length === 0 && !isLoading && (
            <div style={{ color: '#475569', textAlign: 'center', padding: '40px 0' }}>
              No chat history yet
            </div>
          )}
        </div>
      </div>

      {/* Session detail */}
      <div style={{ flex: 1, background: '#1e293b', borderRadius: '8px', padding: '16px' }}>
        {!selectedSession && (
          <div style={{ color: '#475569', textAlign: 'center', marginTop: '100px' }}>
            Select a session to view
          </div>
        )}

        {selectedSession && (
          <>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600, color: '#e2e8f0' }}>
                  {selectedSession.modelName}
                </div>
                <div style={{ fontSize: '11px', color: '#64748b' }}>
                  {new Date(selectedSession.createdAt).toLocaleString()}
                </div>
              </div>
              <button
                onClick={() => handleDelete(selectedSession.id)}
                style={{
                  padding: '6px 12px',
                  background: 'transparent',
                  border: '1px solid #f87171',
                  borderRadius: '4px',
                  color: '#f87171',
                  cursor: 'pointer',
                  fontSize: '11px',
                }}
              >
                Delete
              </button>
            </div>

            <div style={{ maxHeight: '400px', overflow: 'auto' }}>
              {selectedSession.messages.map((msg, idx) => (
                <div
                  key={idx}
                  style={{
                    marginBottom: '12px',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start',
                  }}
                >
                  <div style={{
                    maxWidth: '90%',
                    padding: '10px 14px',
                    borderRadius: '10px',
                    background: msg.role === 'user' ? '#1e3a5f' : '#0f172a',
                    color: '#e2e8f0',
                    fontSize: '12px',
                    lineHeight: '1.5',
                    border: msg.role === 'assistant' ? '1px solid #334155' : 'none',
                  }}>
                    <MessageContent content={msg.content} />
                  </div>
                  <div style={{ fontSize: '9px', color: '#475569', marginTop: '2px' }}>
                    {msg.role} • {new Date(msg.timestamp).toLocaleTimeString()}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
