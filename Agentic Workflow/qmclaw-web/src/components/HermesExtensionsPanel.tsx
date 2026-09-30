"use client";

/**
 * HermesExtensionsPanel - Refactored with hubui design patterns
 *
 * Design principles from hermes-hudui:
 * - CSS variables for theming
 * - CapacityBar for memory display
 * - Card-based layouts with status indicators
 * - Inline editing with hover-reveal controls
 * - Category bar charts for skills
 * - Two-column layouts
 */

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

// ── Theme Colors (matching hubui) ─────────────────────────────────────────────

const THEME = {
  'bg-deep': '#0f172a',
  'bg-surface': '#1e293b',
  'bg-panel': '#0f172a',
  'bg-hover': '#1e293b',
  'primary': '#38bdf8',
  'primary-dim': '#0ea5e9',
  'accent': '#fbbf24',
  'text': '#e2e8f0',
  'text-dim': '#64748b',
  'border': 'rgba(56, 189, 248, 0.25)',
  'success': '#22c55e',
  'warning': '#f59e0b',
  'error': '#ef4444',
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface MemoryEntry {
  text: string;
  category: string;
  char_count: number;
}

interface MemoryState {
  entries: MemoryEntry[];
  total_chars: number;
  max_chars: number;
  capacity_pct: number;
  entry_count: number;
  count_by_category: Record<string, number>;
}

interface SkillInfo {
  name: string;
  category: string;
  description: string;
  enabled: boolean;
  is_custom: boolean;
  modified_at: string | null;
}

interface CronJob {
  id: string;
  name: string;
  task_type: string;
  schedule: string;
  prompt: string;
  qubit: string | null;
  enabled: boolean;
  state: string;
  last_run_at: string | null;
  last_status: string | null;
  last_error: string | null;
  model: string | null;
  skills: string[];
  next_run_at: string | null;
}

interface CronJobResult {
  type: string;
  job_id: string;
  job_name: string;
  task_type: string;
  success: boolean;
  error: string | null;
  last_run_at: string;
}

// ── Utility Functions ─────────────────────────────────────────────────────────

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  const now = new Date();
  const diff = Math.floor((now.getTime() - date.getTime()) / 1000);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function truncate(text: string, limit: number): string {
  if (!text || text.length <= limit) return text || '';
  return text.slice(0, limit) + '...';
}

// ── Shared Components ─────────────────────────────────────────────────────────

function CapacityBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  const level = pct > 90 ? 'critical' : pct > 70 ? 'warn' : 'ok';

  const barColor = level === 'critical' ? THEME.error : level === 'warn' ? THEME.warning : THEME.success;

  return (
    <div style={{ marginBottom: '0.75rem' }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        fontSize: '13px',
        marginBottom: '4px',
        color: THEME['text-dim']
      }}>
        <span>{label}</span>
        <span>
          <span style={{ color: THEME.primary }}>{value.toLocaleString()}</span>
          <span style={{ color: THEME['text-dim'] }}>/{max.toLocaleString()} ({pct.toFixed(0)}%)</span>
        </span>
      </div>
      <div style={{
        height: '6px',
        background: THEME['bg-surface'],
        borderRadius: '3px',
        overflow: 'hidden'
      }}>
        <div style={{
          width: `${Math.min(pct, 100)}%`,
          height: '100%',
          background: barColor,
          transition: 'width 0.3s'
        }} />
      </div>
    </div>
  );
}

function Panel({ title, children, className = '' }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <div style={{
      background: THEME['bg-deep'],
      border: `1px solid ${THEME.border}`,
      borderRadius: '8px',
      padding: '0',
      overflow: 'hidden',
    }} className={className}>
      <div style={{
        padding: '12px 16px',
        borderBottom: `1px solid ${THEME.border}`,
        fontSize: '14px',
        fontWeight: 600,
        color: THEME.primary,
      }}>
        {title}
      </div>
      <div style={{ padding: '16px' }}>
        {children}
      </div>
    </div>
  );
}

// ── Memory Section ────────────────────────────────────────────────────────────

function MemoryEntryRow({
  entry,
  onEdit,
  onDelete,
}: {
  entry: MemoryEntry;
  onEdit: (oldText: string, newText: string) => void;
  onDelete: (text: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(entry.text);
  const [confirming, setConfirming] = useState(false);
  const [hovering, setHovering] = useState(false);

  const startEdit = () => {
    setEditText(entry.text);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setEditText(entry.text);
  };

  const saveEdit = () => {
    const trimmed = editText.trim();
    if (trimmed && trimmed !== entry.text) {
      onEdit(entry.text, trimmed);
    }
    setEditing(false);
  };

  const handleDelete = () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    onDelete(entry.text);
    setConfirming(false);
  };

  return (
    <div
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => { setHovering(false); if (!editing) setConfirming(false); }}
      style={{
        padding: '10px 12px',
        marginBottom: '8px',
        background: THEME['bg-panel'],
        borderLeft: '3px solid ' + (editing ? THEME.primary : THEME.border),
        borderRadius: '4px',
        fontSize: '13px',
        transition: 'all 0.15s',
      }}
    >
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        marginBottom: '4px'
      }}>
        <span style={{
          fontSize: '11px',
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          color: THEME.primary,
        }}>
          {entry.category}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {!editing && (
            <div style={{
              opacity: hovering ? 1 : 0,
              transition: 'opacity 0.15s',
              display: 'flex',
              gap: '8px'
            }}>
              <button
                onClick={startEdit}
                style={{
                  fontSize: '11px',
                  padding: '2px 6px',
                  background: 'transparent',
                  color: THEME.primary,
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                编辑
              </button>
              <button
                onClick={handleDelete}
                onMouseLeave={() => !editing && setConfirming(false)}
                style={{
                  fontSize: '11px',
                  padding: '2px 6px',
                  background: confirming ? THEME.error : 'transparent',
                  color: confirming ? '#fff' : THEME.error,
                  border: 'none',
                  cursor: 'pointer',
                  borderRadius: '2px',
                }}
              >
                {confirming ? '确认' : '删除'}
              </button>
            </div>
          )}
          <span style={{ color: THEME['text-dim'], fontSize: '11px' }}>
            {entry.char_count}c
          </span>
        </div>
      </div>

      {editing ? (
        <div>
          <textarea
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            autoFocus
            style={{
              width: '100%',
              minHeight: '60px',
              padding: '8px',
              background: THEME['bg-deep'],
              color: THEME.text,
              border: `1px solid ${THEME.border}`,
              borderRadius: '4px',
              fontSize: '13px',
              resize: 'vertical',
              outline: 'none',
            }}
          />
          <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
            <button
              onClick={saveEdit}
              style={{
                fontSize: '11px',
                padding: '4px 12px',
                background: THEME.primary,
                color: THEME['bg-deep'],
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
              }}
            >
              保存
            </button>
            <button
              onClick={cancelEdit}
              style={{
                fontSize: '11px',
                padding: '4px 12px',
                background: THEME['bg-hover'],
                color: THEME['text-dim'],
                border: `1px solid ${THEME.border}`,
                borderRadius: '4px',
                cursor: 'pointer',
              }}
            >
              取消
            </button>
          </div>
        </div>
      ) : (
        <div style={{ color: THEME.text, whiteSpace: 'pre-wrap' }}>{entry.text}</div>
      )}
    </div>
  );
}

function AddMemoryForm({ onAdd }: { onAdd: (content: string) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setLoading(true);
    await onAdd(trimmed);
    setText('');
    setOpen(false);
    setLoading(false);
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{
          width: '100%',
          padding: '8px',
          marginTop: '8px',
          fontSize: '12px',
          color: THEME['text-dim'],
          background: 'transparent',
          border: `1px dashed ${THEME.border}`,
          borderRadius: '4px',
          cursor: 'pointer',
        }}
      >
        + 添加新记忆
      </button>
    );
  }

  return (
    <div style={{ marginTop: '8px' }}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="输入记忆内容..."
        autoFocus
        style={{
          width: '100%',
          minHeight: '60px',
          padding: '8px',
          background: THEME['bg-deep'],
          color: THEME.text,
          border: `1px solid ${THEME.border}`,
          borderRadius: '4px',
          fontSize: '13px',
          resize: 'vertical',
          outline: 'none',
        }}
      />
      <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
        <button
          onClick={handleSubmit}
          disabled={loading || !text.trim()}
          style={{
            fontSize: '11px',
            padding: '4px 12px',
            background: text.trim() ? THEME.success : THEME['bg-hover'],
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: text.trim() ? 'pointer' : 'not-allowed',
            opacity: loading ? 0.7 : 1,
          }}
        >
          {loading ? '...' : '添加'}
        </button>
        <button
          onClick={() => { setOpen(false); setText(''); }}
          style={{
            fontSize: '11px',
            padding: '4px 12px',
            background: THEME['bg-hover'],
            color: THEME['text-dim'],
            border: `1px solid ${THEME.border}`,
            borderRadius: '4px',
            cursor: 'pointer',
          }}
        >
          取消
        </button>
      </div>
    </div>
  );
}

function MemorySection() {
  const [memory, setMemory] = useState<MemoryState | null>(null);
  const [user, setUser] = useState<MemoryState | null>(null);
  const [activeTab, setActiveTab] = useState<'memory' | 'user'>('memory');
  const [loading, setLoading] = useState(true);

  const fetchMemory = useCallback(async () => {
    try {
      const data = await api.hermesGetMemory();
      setMemory(data.memory);
      setUser(data.user);
    } catch (err) {
      console.error("Failed to fetch memory:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMemory();
  }, [fetchMemory]);

  const handleAdd = async (content: string) => {
    await api.hermesAddMemory(activeTab, content);
    fetchMemory();
  };

  const handleEdit = async (oldText: string, newText: string) => {
    await api.hermesEditMemory(activeTab, oldText, newText);
    fetchMemory();
  };

  const handleDelete = async (text: string) => {
    await api.hermesDeleteMemory(activeTab, text);
    fetchMemory();
  };

  const current = activeTab === 'memory' ? memory : user;

  if (loading) {
    return (
      <Panel title="记忆">
        <div style={{ color: THEME['text-dim'], fontSize: '13px', textAlign: 'center', padding: '20px' }}>
          加载中...
        </div>
      </Panel>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
      {/* Agent Memory */}
      <div style={{ background: THEME['bg-deep'], border: `1px solid ${THEME.border}`, borderRadius: '8px', padding: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <span style={{ fontSize: '14px', fontWeight: 600, color: THEME.primary }}>Agent 记忆</span>
          {activeTab === 'memory' && (
            <span style={{ fontSize: '12px', color: THEME['text-dim'] }}>
              {memory?.entry_count || 0} 条
            </span>
          )}
        </div>
        {activeTab === 'memory' && (
          <>
            <CapacityBar value={memory?.total_chars || 0} max={memory?.max_chars || 2200} label="容量" />
            <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
              {memory?.entries.map((entry, i) => (
                <MemoryEntryRow key={i} entry={entry} onEdit={handleEdit} onDelete={handleDelete} />
              ))}
            </div>
            <AddMemoryForm onAdd={handleAdd} />
          </>
        )}
      </div>

      {/* User Profile */}
      <div style={{ background: THEME['bg-deep'], border: `1px solid ${THEME.border}`, borderRadius: '8px', padding: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <span style={{ fontSize: '14px', fontWeight: 600, color: THEME.accent }}>用户画像</span>
          {activeTab === 'user' && (
            <span style={{ fontSize: '12px', color: THEME['text-dim'] }}>
              {user?.entry_count || 0} 条
            </span>
          )}
        </div>
        {activeTab === 'user' && (
          <>
            <CapacityBar value={user?.total_chars || 0} max={user?.max_chars || 1375} label="容量" />
            <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
              {user?.entries.map((entry, i) => (
                <MemoryEntryRow key={i} entry={entry} onEdit={handleEdit} onDelete={handleDelete} />
              ))}
            </div>
            <AddMemoryForm onAdd={handleAdd} />
          </>
        )}
      </div>
    </div>
  );
}

// ── Skills Section ────────────────────────────────────────────────────────────

function SkillItem({ skill }: { skill: SkillInfo }) {
  return (
    <div style={{
      padding: '12px',
      marginBottom: '8px',
      background: THEME['bg-panel'],
      borderLeft: '3px solid ' + (skill.enabled ? THEME.success : THEME['text-dim']),
      borderRadius: '4px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <span style={{ fontSize: '14px', fontWeight: 600, color: THEME.text }}>{skill.name}</span>
          {skill.is_custom && (
            <span style={{
              marginLeft: '8px',
              fontSize: '10px',
              padding: '2px 6px',
              background: THEME.accent,
              color: THEME['bg-deep'],
              borderRadius: '2px',
            }}>
              自定义
            </span>
          )}
        </div>
        <span style={{
          fontSize: '11px',
          padding: '2px 8px',
          background: skill.enabled ? THEME.success : THEME['text-dim'],
          color: '#fff',
          borderRadius: '10px',
        }}>
          {skill.enabled ? '启用' : '禁用'}
        </span>
      </div>
      <div style={{ marginTop: '4px', fontSize: '12px', color: THEME['text-dim'] }}>
        {truncate(skill.description, 100)}
      </div>
      <div style={{ marginTop: '4px', fontSize: '11px', color: THEME['text-dim'] }}>
        {skill.category} · {skill.modified_at ? new Date(skill.modified_at).toLocaleDateString() : ''}
      </div>
    </div>
  );
}

function SkillsSection() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<SkillInfo | null>(null);
  const [skillContent, setSkillContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingContent, setLoadingContent] = useState(false);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  const fetchSkills = useCallback(async () => {
    try {
      const data = await api.hermesGetSkills();
      setSkills(data.skills || []);
    } catch (err) {
      console.error("Failed to fetch skills:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const handleToggle = async (skill: SkillInfo) => {
    try {
      await api.hermesToggleSkill(skill.name, !skill.enabled);
      fetchSkills();
    } catch (err) {
      console.error("Failed to toggle skill:", err);
    }
  };

  const handleViewContent = async (skill: SkillInfo) => {
    setSelectedSkill(skill);
    setLoadingContent(true);
    try {
      const data = await api.hermesGetSkill(skill.name);
      setSkillContent(data.skill?.content || '无内容');
    } catch (err) {
      console.error("Failed to fetch skill content:", err);
      setSkillContent('加载失败');
    } finally {
      setLoadingContent(false);
    }
  };

  // Group by category and count
  const byCategory: Record<string, SkillInfo[]> = {};
  skills.forEach(skill => {
    if (!byCategory[skill.category]) {
      byCategory[skill.category] = [];
    }
    byCategory[skill.category].push(skill);
  });

  const categoryCounts = Object.fromEntries(
    Object.entries(byCategory).map(([cat, items]) => [cat, items.length])
  );

  const sortedCategories = Object.entries(categoryCounts).sort((a, b) => b[1] - a[1]);
  const maxCount = sortedCategories[0]?.[1] || 1;

  if (loading) {
    return (
      <Panel title="Skills">
        <div style={{ color: THEME['text-dim'], fontSize: '13px', textAlign: 'center', padding: '20px' }}>
          加载中...
        </div>
      </Panel>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: selectedCategory ? '300px 1fr' : '1fr', gap: '16px' }}>
      {/* Category list with bar chart */}
      <div style={{ background: THEME['bg-deep'], border: `1px solid ${THEME.border}`, borderRadius: '8px', padding: '16px' }}>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
          <span style={{ fontSize: '12px', padding: '4px 10px', background: THEME['bg-panel'], color: THEME.primary, borderRadius: '4px' }}>
            {skills.length} 总计
          </span>
          <span style={{ fontSize: '12px', padding: '4px 10px', background: THEME['bg-panel'], color: THEME.accent, borderRadius: '4px' }}>
            {skills.filter(s => s.is_custom).length} 自定义
          </span>
          <span style={{ fontSize: '12px', color: THEME['text-dim'] }}>
            {sortedCategories.length} 分类
          </span>
        </div>

        {/* Category bar chart */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
          {sortedCategories.map(([cat, count]) => {
            const pct = (count / maxCount) * 100;
            const isSelected = selectedCategory === cat;
            return (
              <button
                key={cat}
                onClick={() => setSelectedCategory(isSelected ? null : cat)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '6px 8px',
                  background: isSelected ? THEME['bg-hover'] : 'transparent',
                  border: 'none',
                  borderLeft: isSelected ? `3px solid ${THEME.primary}` : '3px solid transparent',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                <span style={{
                  width: '120px',
                  fontSize: '13px',
                  color: isSelected ? THEME.primary : THEME.text,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {cat}
                </span>
                <div style={{ flex: 1, height: '6px', background: THEME['bg-surface'], borderRadius: '3px' }}>
                  <div style={{
                    width: `${pct}%`,
                    height: '100%',
                    background: isSelected ? THEME.primary : THEME['primary-dim'],
                    borderRadius: '3px',
                    transition: 'width 0.3s',
                  }} />
                </div>
                <span style={{
                  width: '24px',
                  fontSize: '12px',
                  textAlign: 'right',
                  color: isSelected ? THEME.primary : THEME['text-dim'],
                }}>
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Skills list */}
      <div style={{ background: THEME['bg-deep'], border: `1px solid ${THEME.border}`, borderRadius: '8px', padding: '16px' }}>
        <div style={{ fontSize: '14px', fontWeight: 600, color: THEME.primary, marginBottom: '12px' }}>
          {selectedCategory || '所有 Skills'}
        </div>
        <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
          {(selectedCategory ? byCategory[selectedCategory] || [] : skills).map((skill) => (
            <div key={skill.name} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
              <div style={{ flex: 1 }}>
                <SkillItem skill={skill} />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <button
                  onClick={() => handleViewContent(skill)}
                  style={{
                    fontSize: '11px',
                    padding: '4px 10px',
                    background: THEME.primary,
                    color: THEME['bg-deep'],
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                  }}
                >
                  查看
                </button>
                <button
                  onClick={() => handleToggle(skill)}
                  style={{
                    fontSize: '11px',
                    padding: '4px 10px',
                    background: skill.enabled ? THEME['bg-hover'] : THEME.success,
                    color: skill.enabled ? THEME['text-dim'] : '#fff',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                  }}
                >
                  {skill.enabled ? '禁用' : '启用'}
                </button>
              </div>
            </div>
          ))}
          {skills.length === 0 && (
            <div style={{ color: THEME['text-dim'], fontSize: '13px', textAlign: 'center', padding: '20px' }}>
              暂无 Skills
            </div>
          )}
        </div>
      </div>

      {/* Skill content modal */}
      {selectedSkill && (
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
          zIndex: 1000,
        }}>
          <div style={{
            background: THEME['bg-deep'],
            border: `1px solid ${THEME.border}`,
            borderRadius: '8px',
            width: '600px',
            maxHeight: '80vh',
            overflow: 'hidden',
          }}>
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '12px 16px',
              borderBottom: `1px solid ${THEME.border}`,
            }}>
              <span style={{ fontSize: '14px', fontWeight: 600, color: THEME.primary }}>{selectedSkill.name}</span>
              <button
                onClick={() => { setSelectedSkill(null); setSkillContent(null); }}
                style={{
                  fontSize: '18px',
                  background: 'transparent',
                  color: THEME['text-dim'],
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                ×
              </button>
            </div>
            <div style={{ padding: '16px', maxHeight: '60vh', overflowY: 'auto' }}>
              {loadingContent ? (
                <div style={{ color: THEME['text-dim'], fontSize: '13px' }}>加载中...</div>
              ) : (
                <pre style={{
                  whiteSpace: 'pre-wrap',
                  fontSize: '12px',
                  color: THEME.text,
                  fontFamily: 'monospace',
                  margin: 0,
                }}>
                  {skillContent}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Cron Section ──────────────────────────────────────────────────────────────

function CronJobCard({ job, onPause, onResume, onRun, onDelete }: {
  job: CronJob;
  onPause: () => void;
  onResume: () => void;
  onRun: () => void;
  onDelete: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [hovering, setHovering] = useState(false);

  const isPaused = job.state === 'paused' || !job.enabled;
  const isActive = job.enabled && !isPaused;

  const statusColor = isActive ? THEME.success : THEME['text-dim'];
  const statusText = isPaused ? '已暂停' : job.state || '活跃';

  return (
    <div
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      style={{
        padding: '16px',
        marginBottom: '12px',
        background: THEME['bg-panel'],
        borderLeft: `4px solid ${statusColor}`,
        borderRadius: '4px',
      }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center', marginBottom: '12px' }}>
        {/* Status indicator */}
        <span style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          background: statusColor,
        }} />

        {/* Job name */}
        <span style={{ fontSize: '14px', fontWeight: 600, color: THEME.primary }}>
          {job.name}
        </span>

        {/* Status badge */}
        <span style={{
          fontSize: '11px',
          padding: '2px 8px',
          background: THEME['bg-hover'],
          color: statusColor,
          borderRadius: '10px',
        }}>
          {statusText}
        </span>

        {/* Task type */}
        <span style={{
          fontSize: '11px',
          padding: '2px 8px',
          background: THEME['bg-surface'],
          color: THEME['text-dim'],
          borderRadius: '4px',
        }}>
          {job.task_type === 'quantum' ? '量子测控' : job.task_type === 'hermes' ? 'Hermes Agent' : '脚本'}
        </span>

        {/* Qubit */}
        {job.qubit && (
          <span style={{
            fontSize: '11px',
            padding: '2px 8px',
            background: THEME.accent,
            color: THEME['bg-deep'],
            borderRadius: '4px',
          }}>
            Q: {job.qubit}
          </span>
        )}

        {/* Action buttons */}
        <div style={{
          marginLeft: 'auto',
          display: 'flex',
          gap: '6px',
        }}>
          {isPaused ? (
            <button
              onClick={onResume}
              style={{
                fontSize: '11px',
                padding: '4px 10px',
                background: THEME.success,
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
              }}
            >
              恢复
            </button>
          ) : (
            <>
              <button
                onClick={onRun}
                style={{
                  fontSize: '11px',
                  padding: '4px 10px',
                  background: THEME.primary,
                  color: THEME['bg-deep'],
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                运行
              </button>
              <button
                onClick={onPause}
                style={{
                  fontSize: '11px',
                  padding: '4px 10px',
                  background: THEME['bg-hover'],
                  color: THEME['text-dim'],
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                暂停
              </button>
            </>
          )}
          {confirming ? (
            <>
              <button
                onClick={() => { onDelete(); setConfirming(false); }}
                style={{
                  fontSize: '11px',
                  padding: '4px 10px',
                  background: THEME.error,
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                确认
              </button>
              <button
                onClick={() => setConfirming(false)}
                style={{
                  fontSize: '11px',
                  padding: '4px 10px',
                  background: THEME['bg-hover'],
                  color: THEME['text-dim'],
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                取消
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              style={{
                fontSize: '11px',
                padding: '4px 10px',
                background: THEME['bg-hover'],
                color: THEME.error,
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                opacity: hovering ? 1 : 0.5,
              }}
            >
              删除
            </button>
          )}
        </div>
      </div>

      {/* Job details grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: '12px',
        fontSize: '12px',
      }}>
        <div>
          <div style={{ color: THEME['text-dim'], fontSize: '10px', textTransform: 'uppercase', marginBottom: '2px' }}>
            Cron 表达式
          </div>
          <div style={{ color: THEME.primary }}>{job.schedule}</div>
        </div>
        <div>
          <div style={{ color: THEME['text-dim'], fontSize: '10px', textTransform: 'uppercase', marginBottom: '2px' }}>
            上次运行
          </div>
          <div>
            {timeAgo(job.last_run_at)}
            {job.last_status && (
              <span style={{
                marginLeft: '4px',
                color: job.last_status === 'success' ? THEME.success : THEME.error,
              }}>
                {job.last_status === 'success' ? '✓' : '✗'}
              </span>
            )}
          </div>
        </div>
        <div>
          <div style={{ color: THEME['text-dim'], fontSize: '10px', textTransform: 'uppercase', marginBottom: '2px' }}>
            下次运行
          </div>
          <div>{job.next_run_at ? new Date(job.next_run_at).toLocaleString() : '-'}</div>
        </div>
        <div>
          <div style={{ color: THEME['text-dim'], fontSize: '10px', textTransform: 'uppercase', marginBottom: '2px' }}>
            模型
          </div>
          <div style={{ color: THEME.accent }}>{job.model || '-'}</div>
        </div>
      </div>

      {/* Error message */}
      {job.last_error && (
        <div style={{
          marginTop: '8px',
          padding: '6px 10px',
          background: 'rgba(239, 68, 68, 0.1)',
          borderRadius: '4px',
          fontSize: '12px',
          color: THEME.error,
        }}>
          错误: {job.last_error}
        </div>
      )}

      {/* Prompt preview */}
      {job.prompt && (
        <div style={{
          marginTop: '8px',
          fontSize: '12px',
          color: THEME['text-dim'],
        }}>
          {truncate(job.prompt, 150)}
        </div>
      )}
    </div>
  );
}

function CronSection() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [notification, setNotification] = useState<CronJobResult | null>(null);
  const [newJob, setNewJob] = useState({
    name: '',
    schedule: '',
    task_type: 'quantum' as 'quantum' | 'hermes' | 'script',
    prompt: '',
    qubit: ''
  });

  const fetchJobs = useCallback(async () => {
    try {
      const data = await api.hermesGetCronJobs();
      setJobs(data.jobs || []);
    } catch (err) {
      console.error("Failed to fetch cron jobs:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  // WebSocket listener for cron job results
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`ws://localhost:3013?session_id=hermes_extensions`);

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "cron_job_result") {
          setNotification(msg as CronJobResult);
          fetchJobs();
          setTimeout(() => setNotification(null), 5000);
        }
      } catch (e) {
        // Ignore parse errors
      }
    };

    ws.onerror = () => {
      // WebSocket error - silently ignore
    };

    return () => ws.close();
  }, [fetchJobs]);

  const handleCreate = async () => {
    if (!newJob.name.trim() || !newJob.schedule.trim()) return;
    try {
      await api.hermesCreateCronJob(newJob);
      setNewJob({ name: '', schedule: '', task_type: 'quantum', prompt: '', qubit: '' });
      setShowCreate(false);
      fetchJobs();
    } catch (err) {
      console.error("Failed to create job:", err);
    }
  };

  const handlePause = async (jobId: string) => {
    await api.hermesPauseCronJob(jobId);
    fetchJobs();
  };

  const handleResume = async (jobId: string) => {
    await api.hermesResumeCronJob(jobId);
    fetchJobs();
  };

  const handleRun = async (jobId: string) => {
    await api.hermesRunCronJob(jobId);
    fetchJobs();
  };

  const handleDelete = async (jobId: string) => {
    await api.hermesDeleteCronJob(jobId);
    fetchJobs();
  };

  if (loading) {
    return (
      <Panel title="定时任务">
        <div style={{ color: THEME['text-dim'], fontSize: '13px', textAlign: 'center', padding: '20px' }}>
          加载中...
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="定时任务">
      {/* Notification banner */}
      {notification && (
        <div style={{
          padding: '12px 16px',
          marginBottom: '16px',
          background: notification.success ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
          border: `1px solid ${notification.success ? THEME.success : THEME.error}`,
          borderRadius: '4px',
          fontSize: '13px',
        }}>
          <div style={{ fontWeight: 600, color: notification.success ? THEME.success : THEME.error }}>
            {notification.success ? '✓' : '✗'} {notification.job_name}
          </div>
          <div style={{ fontSize: '12px', marginTop: '4px', color: notification.success ? THEME.success : THEME.error, opacity: 0.8 }}>
            {notification.success ? '任务执行成功' : `执行失败: ${notification.error}`}
          </div>
        </div>
      )}

      {/* Summary */}
      <div style={{
        display: 'flex',
        gap: '16px',
        marginBottom: '16px',
        fontSize: '13px',
        color: THEME['text-dim'],
      }}>
        <span>总计: <span style={{ color: THEME.text }}>{jobs.length}</span></span>
        <span style={{ color: THEME.success }}>活跃: <span style={{ color: THEME.text }}>{jobs.filter(j => j.enabled).length}</span></span>
        <span style={{ color: THEME['text-dim'] }}>暂停: <span style={{ color: THEME.text }}>{jobs.filter(j => !j.enabled).length}</span></span>
      </div>

      {/* Job list */}
      <div style={{ maxHeight: '500px', overflowY: 'auto' }}>
        {jobs.map((job) => (
          <CronJobCard
            key={job.id}
            job={job}
            onPause={() => handlePause(job.id)}
            onResume={() => handleResume(job.id)}
            onRun={() => handleRun(job.id)}
            onDelete={() => handleDelete(job.id)}
          />
        ))}
        {jobs.length === 0 && !showCreate && (
          <div style={{ color: THEME['text-dim'], fontSize: '13px', textAlign: 'center', padding: '20px' }}>
            暂无定时任务
          </div>
        )}
      </div>

      {/* Create form */}
      {showCreate ? (
        <div style={{
          marginTop: '16px',
          padding: '16px',
          background: THEME['bg-panel'],
          borderRadius: '4px',
        }}>
          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '12px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>
              任务名称
            </label>
            <input
              value={newJob.name}
              onChange={(e) => setNewJob({ ...newJob, name: e.target.value })}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: THEME['bg-deep'],
                color: THEME.text,
                border: `1px solid ${THEME.border}`,
                borderRadius: '4px',
                fontSize: '13px',
                outline: 'none',
              }}
            />
          </div>
          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '12px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>
              Cron 表达式
            </label>
            <input
              value={newJob.schedule}
              onChange={(e) => setNewJob({ ...newJob, schedule: e.target.value })}
              placeholder="0 */6 * * *"
              style={{
                width: '100%',
                padding: '8px 12px',
                background: THEME['bg-deep'],
                color: THEME.text,
                border: `1px solid ${THEME.border}`,
                borderRadius: '4px',
                fontSize: '13px',
                outline: 'none',
              }}
            />
          </div>
          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '12px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>
              任务类型
            </label>
            <select
              value={newJob.task_type}
              onChange={(e) => setNewJob({ ...newJob, task_type: e.target.value as any })}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: THEME['bg-deep'],
                color: THEME.text,
                border: `1px solid ${THEME.border}`,
                borderRadius: '4px',
                fontSize: '13px',
                outline: 'none',
              }}
            >
              <option value="quantum">量子测控</option>
              <option value="hermes">Hermes Agent</option>
              <option value="script">自定义脚本</option>
            </select>
          </div>
          {newJob.task_type === 'quantum' && (
            <div style={{ marginBottom: '12px' }}>
              <label style={{ fontSize: '12px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>
                量子比特 (可选)
              </label>
              <input
                value={newJob.qubit}
                onChange={(e) => setNewJob({ ...newJob, qubit: e.target.value })}
                placeholder="Q1"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: THEME['bg-deep'],
                  color: THEME.text,
                  border: `1px solid ${THEME.border}`,
                  borderRadius: '4px',
                  fontSize: '13px',
                  outline: 'none',
                }}
              />
            </div>
          )}
          <div style={{ marginBottom: '12px' }}>
            <label style={{ fontSize: '12px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>
              执行内容
            </label>
            <textarea
              value={newJob.prompt}
              onChange={(e) => setNewJob({ ...newJob, prompt: e.target.value })}
              placeholder="输入要执行的指令..."
              rows={3}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: THEME['bg-deep'],
                color: THEME.text,
                border: `1px solid ${THEME.border}`,
                borderRadius: '4px',
                fontSize: '13px',
                resize: 'vertical',
                outline: 'none',
              }}
            />
          </div>
          <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
            <button
              onClick={() => setShowCreate(false)}
              style={{
                fontSize: '12px',
                padding: '8px 16px',
                background: THEME['bg-hover'],
                color: THEME['text-dim'],
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
              }}
            >
              取消
            </button>
            <button
              onClick={handleCreate}
              disabled={!newJob.name.trim() || !newJob.schedule.trim()}
              style={{
                fontSize: '12px',
                padding: '8px 16px',
                background: newJob.name.trim() && newJob.schedule.trim() ? THEME.success : THEME['bg-hover'],
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                cursor: newJob.name.trim() && newJob.schedule.trim() ? 'pointer' : 'not-allowed',
              }}
            >
              创建
            </button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setShowCreate(true)}
          style={{
            width: '100%',
            padding: '12px',
            marginTop: '16px',
            fontSize: '13px',
            color: THEME.primary,
            background: 'transparent',
            border: `1px dashed ${THEME.border}`,
            borderRadius: '4px',
            cursor: 'pointer',
          }}
        >
          + 创建新任务
        </button>
      )}
    </Panel>
  );
}

// ── Main Panel ────────────────────────────────────────────────────────────────

export default function HermesExtensionsPanel() {
  const [activeTab, setActiveTab] = useState<'memory' | 'skills' | 'cron'>('memory');

  return (
    <div style={{
      background: THEME['bg-deep'],
      borderRadius: '8px',
      padding: '16px',
      marginTop: '16px',
    }}>
      {/* Tab navigation */}
      <div style={{
        display: 'flex',
        gap: '8px',
        marginBottom: '16px',
      }}>
        {[
          { id: 'memory', label: '记忆', icon: '💾' },
          { id: 'skills', label: 'Skills', icon: '🛠️' },
          { id: 'cron', label: '定时任务', icon: '⏰' },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as typeof activeTab)}
            style={{
              padding: '8px 16px',
              fontSize: '13px',
              background: activeTab === tab.id ? THEME.primary : THEME['bg-surface'],
              color: activeTab === tab.id ? THEME['bg-deep'] : THEME['text-dim'],
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontWeight: activeTab === tab.id ? 600 : 400,
            }}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div>
        {activeTab === 'memory' && <MemorySection />}
        {activeTab === 'skills' && <SkillsSection />}
        {activeTab === 'cron' && <CronSection />}
      </div>
    </div>
  );
}
