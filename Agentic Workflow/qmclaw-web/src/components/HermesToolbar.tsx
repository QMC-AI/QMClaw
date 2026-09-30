"use client";

/**
 * HermesToolbar - Right-top sidebar for Hermes extensions
 * Displays Memory, Skills, and Cron tabs in a compact sidebar format
 * With detail/edit modals for each entry
 */

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

// ── Theme Colors ──────────────────────────────────────────────────────────────

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
  id: string;
  title: string;
  content: string;
  body: string;
  created: string | null;
  updated: string | null;
}

interface MemoryState {
  entries: MemoryEntry[];
  total: number;
  total_chars?: number;
  max_chars?: number;
  capacity_pct?: number;
  entry_count?: number;
  count_by_category?: Record<string, number>;
}

interface SkillInfo {
  name: string;
  category: string;
  description: string;
  enabled: boolean;
  is_custom: boolean;
  modified_at: string | null;
  path?: string;
  file_size?: number;
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
  deliver?: string;
  script?: string;
  workdir?: string;
  continuity?: boolean;
  no_agent?: boolean;
  monitor_script?: string;
  monitor_url?: string;
  created_at?: string;
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

// ── Reusable Modal Component ──────────────────────────────────────────────────

interface ModalProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: string;
}

function Modal({ title, onClose, children, width = "min(500px, 90vw)" }: ModalProps) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.7)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        background: THEME['bg-surface'],
        border: `1px solid ${THEME.border}`,
        borderRadius: "12px",
        padding: "20px",
        width,
        maxHeight: "80vh",
        overflow: "auto",
      }}>
        <div style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "16px",
          paddingBottom: "12px",
          borderBottom: `1px solid ${THEME.border}`,
        }}>
          <span style={{ fontWeight: 700, color: THEME.text, fontSize: "14px" }}>{title}</span>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: THEME['text-dim'],
              cursor: "pointer",
              fontSize: "18px",
              padding: "0 4px",
            }}
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ── Memory Section ────────────────────────────────────────────────────────────

function MemorySectionCompact() {
  const [memory, setMemory] = useState<MemoryState | null>(null);
  const [user, setUser] = useState<MemoryState | null>(null);
  const [activeTab, setActiveTab] = useState<'memory' | 'user'>('memory');
  const [loading, setLoading] = useState(true);
  const [selectedEntry, setSelectedEntry] = useState<MemoryEntry | null>(null);
  const [editText, setEditText] = useState('');
  const [editCategory, setEditCategory] = useState('');
  const [showEditModal, setShowEditModal] = useState(false);
  const [isEditing, setIsEditing] = useState(false);

  const fetchMemory = useCallback(async () => {
    try {
      const data = await api.hermesGetMemory();
      setMemory({ entries: data.entries || [], total: data.total || 0 });
      setUser({ entries: [], total: 0 }); // user memories not implemented in local mode
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

  const openDetailModal = (entry: MemoryEntry) => {
    setSelectedEntry(entry);
    setEditText(entry.content || '');
    setEditCategory(entry.title || '');
    setIsEditing(false);
    setShowEditModal(true);
  };

  const startEditing = () => {
    setIsEditing(true);
  };

  const cancelEditing = () => {
    if (selectedEntry) {
      setEditText(selectedEntry.content || '');
      setEditCategory(selectedEntry.title || '');
    }
    setIsEditing(false);
  };

  const saveEdit = async () => {
    if (selectedEntry && editText.trim()) {
      await handleEdit(selectedEntry.content || '', editText.trim());
      setShowEditModal(false);
      setSelectedEntry(null);
    }
  };

  const confirmDelete = async () => {
    if (selectedEntry) {
      await handleDelete(selectedEntry.content || '');
      setShowEditModal(false);
      setSelectedEntry(null);
    }
  };

  const current = activeTab === 'memory' ? memory : user;
  const maxChars = activeTab === 'memory' ? 2200 : 1375;

  if (loading) {
    return <div style={{ color: THEME['text-dim'], fontSize: '12px', textAlign: 'center', padding: '20px' }}>加载中...</div>;
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Sub-tabs */}
      <div style={{ display: 'flex', gap: '4px', marginBottom: '12px' }}>
        <button
          onClick={() => setActiveTab('memory')}
          style={{
            flex: 1,
            padding: '6px 8px',
            fontSize: '11px',
            background: activeTab === 'memory' ? THEME.primary : THEME['bg-surface'],
            color: activeTab === 'memory' ? THEME['bg-deep'] : THEME['text-dim'],
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
          }}
        >
          Agent 记忆
        </button>
        <button
          onClick={() => setActiveTab('user')}
          style={{
            flex: 1,
            padding: '6px 8px',
            fontSize: '11px',
            background: activeTab === 'user' ? THEME.accent : THEME['bg-surface'],
            color: activeTab === 'user' ? THEME['bg-deep'] : THEME['text-dim'],
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
          }}
        >
          用户画像
        </button>
      </div>

      {/* Capacity bar */}
      <div style={{ marginBottom: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginBottom: '4px', color: THEME['text-dim'] }}>
          <span>容量</span>
          <span>
            <span style={{ color: THEME.primary }}>{current?.total_chars || 0}</span>
            <span style={{ color: THEME['text-dim'] }}>/{maxChars}</span>
          </span>
        </div>
        <div style={{ height: '4px', background: THEME['bg-surface'], borderRadius: '2px' }}>
          <div style={{
            width: `${Math.min(((current?.total_chars || 0) / maxChars) * 100, 100)}%`,
            height: '100%',
            background: THEME.primary,
            borderRadius: '2px',
          }} />
        </div>
      </div>

      {/* Entries list */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {current?.entries.map((entry, i) => (
          <div key={i} style={{
            padding: '8px',
            marginBottom: '6px',
            background: THEME['bg-panel'],
            borderLeft: `2px solid ${THEME.border}`,
            borderRadius: '4px',
            fontSize: '11px',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
              <span style={{ color: THEME.primary, fontWeight: 600 }}>{entry.title}</span>
              <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                <span style={{ color: THEME['text-dim'] }}>{entry.content?.length || 0}c</span>
                <button
                  onClick={() => openDetailModal(entry)}
                  style={{
                    background: THEME['bg-hover'],
                    border: 'none',
                    color: THEME['primary'],
                    cursor: 'pointer',
                    fontSize: '10px',
                    padding: '2px 6px',
                    borderRadius: '3px',
                  }}
                >
                  📖
                </button>
              </div>
            </div>
            <div style={{ color: THEME.text }}>{truncate(entry.body || entry.content, 80)}</div>
          </div>
        ))}
        {(!current?.entries || current.entries.length === 0) && (
          <div style={{ color: THEME['text-dim'], fontSize: '11px', textAlign: 'center', padding: '20px' }}>暂无记忆</div>
        )}
      </div>

      {/* Add form */}
      <div style={{ marginTop: '8px' }}>
        <textarea
          id="memory-input"
          placeholder="添加记忆..."
          style={{
            width: '100%',
            minHeight: '40px',
            padding: '6px',
            background: THEME['bg-deep'],
            color: THEME.text,
            border: `1px solid ${THEME.border}`,
            borderRadius: '4px',
            fontSize: '11px',
            resize: 'none',
            outline: 'none',
          }}
        />
        <button
          onClick={async () => {
            const input = document.getElementById('memory-input') as HTMLTextAreaElement;
            const text = input?.value?.trim();
            if (text) {
              await handleAdd(text);
              input.value = '';
            }
          }}
          style={{
            width: '100%',
            marginTop: '6px',
            padding: '6px',
            fontSize: '11px',
            background: THEME.primary,
            color: THEME['bg-deep'],
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
          }}
        >
          + 添加
        </button>
      </div>

      {/* Detail/Edit Modal */}
      {showEditModal && selectedEntry && (
        <Modal title="记忆详情" onClose={() => setShowEditModal(false)}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div>
              <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>分类</label>
              {isEditing ? (
                <input
                  type="text"
                  value={editCategory}
                  onChange={e => setEditCategory(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: THEME['bg-deep'],
                    color: THEME.text,
                    border: `1px solid ${THEME.border}`,
                    borderRadius: '4px',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
              ) : (
                <span style={{ fontSize: '12px', color: THEME.primary }}>{selectedEntry.title}</span>
              )}
            </div>
            <div>
              <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>内容</label>
              {isEditing ? (
                <textarea
                  value={editText}
                  onChange={e => setEditText(e.target.value)}
                  style={{
                    width: '100%',
                    minHeight: '120px',
                    padding: '8px',
                    background: THEME['bg-deep'],
                    color: THEME.text,
                    border: `1px solid ${THEME.border}`,
                    borderRadius: '4px',
                    fontSize: '12px',
                    resize: 'vertical',
                    outline: 'none',
                  }}
                />
              ) : (
                <div style={{
                  padding: '8px',
                  background: THEME['bg-deep'],
                  borderRadius: '4px',
                  fontSize: '12px',
                  color: THEME.text,
                  maxHeight: '200px',
                  overflowY: 'auto',
                  whiteSpace: 'pre-wrap',
                }}>
                  {selectedEntry.content}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: '8px', fontSize: '11px', color: THEME['text-dim'] }}>
              <span>字符数: {selectedEntry.content?.length || 0}</span>
            </div>
            <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
              {isEditing ? (
                <>
                  <button
                    onClick={saveEdit}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME.primary,
                      color: THEME['bg-deep'],
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                      fontWeight: 600,
                    }}
                  >
                    保存
                  </button>
                  <button
                    onClick={cancelEditing}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME['bg-hover'],
                      color: THEME.text,
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                    }}
                  >
                    取消
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={startEditing}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME.primary,
                      color: THEME['bg-deep'],
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                      fontWeight: 600,
                    }}
                  >
                    编辑
                  </button>
                  <button
                    onClick={confirmDelete}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME.error,
                      color: '#fff',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                    }}
                  >
                    删除
                  </button>
                </>
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── Skills Section ────────────────────────────────────────────────────────────

function SkillsSectionCompact() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSkill, setSelectedSkill] = useState<SkillInfo | null>(null);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [skillContent, setSkillContent] = useState<string | null>(null);

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

  const openDetailModal = async (skill: SkillInfo) => {
    setSelectedSkill(skill);
    setShowDetailModal(true);
    setSkillContent(null); // Reset while loading
    // Fetch full content
    try {
      const data = await api.hermesGetSkill(skill.name);
      setSkillContent(data.content || null);
    } catch {
      setSkillContent(null);
    }
  };

  const confirmDelete = async () => {
    if (selectedSkill) {
      try {
        await api.hermesDeleteSkill(selectedSkill.name);
        fetchSkills();
        setShowDetailModal(false);
        setSelectedSkill(null);
      } catch (err) {
        console.error("Failed to delete skill:", err);
      }
    }
  };

  if (loading) {
    return <div style={{ color: THEME['text-dim'], fontSize: '12px', textAlign: 'center', padding: '20px' }}>加载中...</div>;
  }

  // Group by category
  const byCategory: Record<string, SkillInfo[]> = {};
  skills.forEach(skill => {
    if (!byCategory[skill.category]) {
      byCategory[skill.category] = [];
    }
    byCategory[skill.category].push(skill);
  });

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Summary */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', fontSize: '11px' }}>
        <span style={{ color: THEME.primary }}>{skills.length} 总计</span>
        <span style={{ color: THEME.success }}>{skills.filter(s => s.enabled).length} 启用</span>
      </div>

      {/* Skills list */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {Object.entries(byCategory).map(([category, categorySkills]) => (
          <div key={category} style={{ marginBottom: '12px' }}>
            <div style={{ fontSize: '11px', color: THEME['text-dim'], marginBottom: '6px', fontWeight: 600 }}>
              {category} ({categorySkills.length})
            </div>
            {categorySkills.map((skill) => (
              <div key={skill.name} style={{
                padding: '6px 8px',
                marginBottom: '4px',
                background: THEME['bg-panel'],
                borderLeft: `2px solid ${skill.enabled ? THEME.success : THEME['text-dim']}`,
                borderRadius: '4px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ fontSize: '11px', fontWeight: 600, color: THEME.text }}>{skill.name}</span>
                    <button
                      onClick={() => openDetailModal(skill)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: THEME['primary'],
                        cursor: 'pointer',
                        fontSize: '10px',
                        padding: '2px',
                      }}
                    >
                      📖
                    </button>
                  </div>
                  <button
                    onClick={() => handleToggle(skill)}
                    style={{
                      padding: '2px 6px',
                      fontSize: '10px',
                      background: skill.enabled ? THEME['bg-hover'] : THEME.success,
                      color: skill.enabled ? THEME['text-dim'] : '#fff',
                      border: 'none',
                      borderRadius: '3px',
                      cursor: 'pointer',
                    }}
                  >
                    {skill.enabled ? '禁用' : '启用'}
                  </button>
                </div>
                <div style={{ fontSize: '10px', color: THEME['text-dim'], marginTop: '2px' }}>
                  {truncate(skill.description, 50)}
                </div>
              </div>
            ))}
          </div>
        ))}
        {skills.length === 0 && (
          <div style={{ color: THEME['text-dim'], fontSize: '11px', textAlign: 'center', padding: '20px' }}>暂无 Skills</div>
        )}
      </div>

      {/* Detail Modal */}
      {showDetailModal && selectedSkill && (
        <Modal title={`🛠️ Skill: ${selectedSkill.name}`} onClose={() => setShowDetailModal(false)} width="min(700px, 90vw)">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {/* Meta info header */}
            <div style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: '12px',
              padding: '8px 12px',
              background: THEME['bg-deep'],
              borderRadius: '6px',
              fontSize: '11px',
            }}>
              <div>
                <span style={{ color: THEME['text-dim'] }}>分类: </span>
                <span style={{ color: THEME.primary }}>{selectedSkill.category}</span>
              </div>
              <div>
                <span style={{ color: THEME['text-dim'] }}>状态: </span>
                <span style={{ color: selectedSkill.enabled ? THEME.success : THEME['text-dim'] }}>
                  {selectedSkill.enabled ? '已启用' : '已禁用'}
                </span>
              </div>
              <div>
                <span style={{ color: THEME['text-dim'] }}>自定义: </span>
                <span style={{ color: selectedSkill.is_custom ? THEME.accent : THEME['text-dim'] }}>
                  {selectedSkill.is_custom ? '是' : '否'}
                </span>
              </div>
              <div>
                <span style={{ color: THEME['text-dim'] }}>修改: </span>
                <span>{timeAgo(selectedSkill.modified_at)}</span>
              </div>
              {selectedSkill.path && (
                <div style={{ color: THEME['text-dim'], wordBreak: 'break-all' }}>
                  路径: {selectedSkill.path}
                </div>
              )}
            </div>

            {/* Main content area - SKILL.md */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                <label style={{ fontSize: '12px', color: THEME.primary, fontWeight: 600 }}>
                  📄 SKILL.md 内容
                </label>
                {skillContent && (
                  <button
                    onClick={() => navigator.clipboard.writeText(skillContent)}
                    style={{
                      padding: '2px 8px',
                      fontSize: '10px',
                      background: THEME['bg-hover'],
                      color: THEME['text-dim'],
                      border: 'none',
                      borderRadius: '3px',
                      cursor: 'pointer',
                    }}
                  >
                    📋 复制
                  </button>
                )}
              </div>
              {skillContent ? (
                <div style={{
                  padding: '12px',
                  background: THEME['bg-deep'],
                  borderRadius: '6px',
                  fontSize: '12px',
                  color: THEME.text,
                  maxHeight: '400px',
                  overflowY: 'auto',
                  whiteSpace: 'pre-wrap',
                  fontFamily: '"SF Mono", "Monaco", "Inconsolata", "Roboto Mono", monospace',
                  lineHeight: '1.6',
                  border: `1px solid ${THEME.border}`,
                }}>
                  {skillContent}
                </div>
              ) : (
                <div style={{
                  padding: '20px',
                  background: THEME['bg-deep'],
                  borderRadius: '6px',
                  textAlign: 'center',
                  color: THEME['text-dim'],
                  fontSize: '12px',
                }}>
                  加载中...
                </div>
              )}
            </div>

            {/* Quick description below */}
            {selectedSkill.description && (
              <div>
                <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>
                  简短描述
                </label>
                <div style={{
                  padding: '8px',
                  background: THEME['bg-deep'],
                  borderRadius: '4px',
                  fontSize: '12px',
                  color: THEME.text,
                  borderLeft: `3px solid ${THEME.primary}`,
                }}>
                  {selectedSkill.description}
                </div>
              </div>
            )}

            {/* Action buttons */}
            <div style={{ display: 'flex', gap: '8px', marginTop: '8px', paddingTop: '12px', borderTop: `1px solid ${THEME.border}` }}>
              <button
                onClick={() => handleToggle(selectedSkill)}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: selectedSkill.enabled ? THEME['bg-hover'] : THEME.success,
                  color: selectedSkill.enabled ? THEME.text : '#fff',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '12px',
                  fontWeight: 500,
                }}
              >
                {selectedSkill.enabled ? '⏸️ 禁用' : '▶️ 启用'}
              </button>
              <button
                onClick={confirmDelete}
                style={{
                  flex: 1,
                  padding: '8px',
                  background: THEME.error,
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: 'pointer',
                  fontSize: '12px',
                }}
              >
                🗑️ 删除
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── Cron Section ──────────────────────────────────────────────────────────────

function CronSectionCompact() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [notification, setNotification] = useState<CronJobResult | null>(null);
  const [selectedJob, setSelectedJob] = useState<CronJob | null>(null);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState<Partial<CronJob>>({});

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

  useEffect(() => {
    fetchJobs();

    // WebSocket listener for cron job results
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`ws://localhost:3013?session_id=hermes_toolbar`);

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

    return () => ws.close();
  }, [fetchJobs]);

  const handleToggle = async (job: CronJob) => {
    if (job.enabled) {
      await api.hermesPauseCronJob(job.id);
    } else {
      await api.hermesResumeCronJob(job.id);
    }
    fetchJobs();
  };

  const handleRun = async (jobId: string) => {
    await api.hermesRunCronJob(jobId);
    fetchJobs();
  };

  const openDetailModal = (job: CronJob) => {
    setSelectedJob(job);
    setEditForm({
      name: job.name,
      schedule: job.schedule,
      prompt: job.prompt,
      skills: job.skills,
      model: job.model,
      deliver: job.deliver,
      script: job.script,
      workdir: job.workdir,
    });
    setIsEditing(false);
    setShowDetailModal(true);
  };

  const startEditing = () => {
    setIsEditing(true);
  };

  const cancelEditing = () => {
    if (selectedJob) {
      setEditForm({
        name: selectedJob.name,
        schedule: selectedJob.schedule,
        prompt: selectedJob.prompt,
        skills: selectedJob.skills,
        model: selectedJob.model,
        deliver: selectedJob.deliver,
        script: selectedJob.script,
        workdir: selectedJob.workdir,
      });
    }
    setIsEditing(false);
  };

  const saveEdit = async () => {
    if (selectedJob) {
      try {
        await api.hermesUpdateCronJob(selectedJob.id, {
          name: editForm.name,
          schedule: editForm.schedule,
          prompt: editForm.prompt,
          skills: editForm.skills,
          model: editForm.model || undefined,
          deliver: editForm.deliver || undefined,
          script: editForm.script || undefined,
          workdir: editForm.workdir || undefined,
        });
        fetchJobs();
        setShowDetailModal(false);
        setSelectedJob(null);
      } catch (err) {
        console.error("Failed to update cron job:", err);
      }
    }
  };

  const confirmDelete = async () => {
    if (selectedJob) {
      try {
        await api.hermesDeleteCronJob(selectedJob.id);
        fetchJobs();
        setShowDetailModal(false);
        setSelectedJob(null);
      } catch (err) {
        console.error("Failed to delete cron job:", err);
      }
    }
  };

  const updateEditForm = (field: keyof CronJob, value: unknown) => {
    setEditForm(prev => ({ ...prev, [field]: value }));
  };

  if (loading) {
    return <div style={{ color: THEME['text-dim'], fontSize: '12px', textAlign: 'center', padding: '20px' }}>加载中...</div>;
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Notification */}
      {notification && (
        <div style={{
          padding: '8px',
          marginBottom: '12px',
          background: notification.success ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
          borderRadius: '4px',
          fontSize: '11px',
          color: notification.success ? THEME.success : THEME.error,
        }}>
          {notification.success ? '✓' : '✗'} {notification.job_name}
        </div>
      )}

      {/* Summary */}
      <div style={{ display: 'flex', gap: '12px', marginBottom: '12px', fontSize: '11px' }}>
        <span>总计: <span style={{ color: THEME.text }}>{jobs.length}</span></span>
        <span style={{ color: THEME.success }}>活跃: <span style={{ color: THEME.text }}>{jobs.filter(j => j.enabled).length}</span></span>
      </div>

      {/* Jobs list */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {jobs.map((job) => (
          <div key={job.id} style={{
            padding: '8px',
            marginBottom: '8px',
            background: THEME['bg-panel'],
            borderLeft: `3px solid ${job.enabled ? THEME.success : THEME['text-dim']}`,
            borderRadius: '4px',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: THEME.primary }}>{job.name}</span>
                <button
                  onClick={() => openDetailModal(job)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: THEME['primary'],
                    cursor: 'pointer',
                    fontSize: '10px',
                    padding: '2px',
                  }}
                >
                  📖
                </button>
              </div>
              <div style={{ display: 'flex', gap: '4px' }}>
                <button
                  onClick={() => handleRun(job.id)}
                  style={{
                    padding: '2px 6px',
                    fontSize: '10px',
                    background: THEME.primary,
                    color: THEME['bg-deep'],
                    border: 'none',
                    borderRadius: '3px',
                    cursor: 'pointer',
                  }}
                >
                  运行
                </button>
                <button
                  onClick={() => handleToggle(job)}
                  style={{
                    padding: '2px 6px',
                    fontSize: '10px',
                    background: job.enabled ? THEME['bg-hover'] : THEME.success,
                    color: job.enabled ? THEME['text-dim'] : '#fff',
                    border: 'none',
                    borderRadius: '3px',
                    cursor: 'pointer',
                  }}
                >
                  {job.enabled ? '暂停' : '恢复'}
                </button>
              </div>
            </div>
            <div style={{ fontSize: '10px', color: THEME['text-dim'] }}>
              {job.schedule} · {timeAgo(job.last_run_at)}
              {job.last_status && (
                <span style={{ marginLeft: '4px', color: job.last_status === 'success' ? THEME.success : THEME.error }}>
                  {job.last_status === 'success' ? '✓' : '✗'}
                </span>
              )}
            </div>
            {job.last_error && (
              <div style={{ fontSize: '10px', color: THEME.error, marginTop: '2px' }}>错误: {truncate(job.last_error, 30)}</div>
            )}
          </div>
        ))}
        {jobs.length === 0 && (
          <div style={{ color: THEME['text-dim'], fontSize: '11px', textAlign: 'center', padding: '20px' }}>暂无定时任务</div>
        )}
      </div>

      {/* Detail Modal */}
      {showDetailModal && selectedJob && (
        <Modal title={`定时任务: ${selectedJob.name}`} onClose={() => setShowDetailModal(false)} width="min(600px, 90vw)">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {/* Status Info */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '11px' }}>
              <div>
                <span style={{ color: THEME['text-dim'] }}>ID: </span>
                <span style={{ color: THEME.text, fontFamily: 'monospace' }}>{selectedJob.id}</span>
              </div>
              <div>
                <span style={{ color: THEME['text-dim'] }}>状态: </span>
                <span style={{ color: selectedJob.enabled ? THEME.success : THEME['text-dim'] }}>
                  {selectedJob.enabled ? '运行中' : '已暂停'}
                </span>
              </div>
              <div>
                <span style={{ color: THEME['text-dim'] }}>上次运行: </span>
                <span>{timeAgo(selectedJob.last_run_at)}</span>
              </div>
              <div>
                <span style={{ color: THEME['text-dim'] }}>下次运行: </span>
                <span>{timeAgo(selectedJob.next_run_at)}</span>
              </div>
            </div>

            {/* Form Fields */}
            <div>
              <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>名称</label>
              {isEditing ? (
                <input
                  type="text"
                  value={editForm.name || ''}
                  onChange={e => updateEditForm('name', e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: THEME['bg-deep'],
                    color: THEME.text,
                    border: `1px solid ${THEME.border}`,
                    borderRadius: '4px',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
              ) : (
                <span style={{ fontSize: '12px', color: THEME.text }}>{selectedJob.name}</span>
              )}
            </div>

            <div>
              <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>Schedule (Cron)</label>
              {isEditing ? (
                <input
                  type="text"
                  value={editForm.schedule || ''}
                  onChange={e => updateEditForm('schedule', e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: THEME['bg-deep'],
                    color: THEME.text,
                    border: `1px solid ${THEME.border}`,
                    borderRadius: '4px',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
              ) : (
                <span style={{ fontSize: '12px', color: THEME.primary, fontFamily: 'monospace' }}>{selectedJob.schedule}</span>
              )}
            </div>

            <div>
              <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>Prompt</label>
              {isEditing ? (
                <textarea
                  value={editForm.prompt || ''}
                  onChange={e => updateEditForm('prompt', e.target.value)}
                  style={{
                    width: '100%',
                    minHeight: '80px',
                    padding: '8px',
                    background: THEME['bg-deep'],
                    color: THEME.text,
                    border: `1px solid ${THEME.border}`,
                    borderRadius: '4px',
                    fontSize: '12px',
                    resize: 'vertical',
                    outline: 'none',
                  }}
                />
              ) : (
                <div style={{
                  padding: '8px',
                  background: THEME['bg-deep'],
                  borderRadius: '4px',
                  fontSize: '12px',
                  color: THEME.text,
                  maxHeight: '100px',
                  overflowY: 'auto',
                  whiteSpace: 'pre-wrap',
                }}>
                  {selectedJob.prompt || '无'}
                </div>
              )}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
              <div>
                <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>模型</label>
                {isEditing ? (
                  <input
                    type="text"
                    value={editForm.model || ''}
                    onChange={e => updateEditForm('model', e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px',
                      background: THEME['bg-deep'],
                      color: THEME.text,
                      border: `1px solid ${THEME.border}`,
                      borderRadius: '4px',
                      fontSize: '12px',
                      outline: 'none',
                    }}
                  />
                ) : (
                  <span style={{ fontSize: '12px', color: THEME.text }}>{selectedJob.model || '默认'}</span>
                )}
              </div>
              <div>
                <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>交付方式</label>
                {isEditing ? (
                  <input
                    type="text"
                    value={editForm.deliver || ''}
                    onChange={e => updateEditForm('deliver', e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px',
                      background: THEME['bg-deep'],
                      color: THEME.text,
                      border: `1px solid ${THEME.border}`,
                      borderRadius: '4px',
                      fontSize: '12px',
                      outline: 'none',
                    }}
                  />
                ) : (
                  <span style={{ fontSize: '12px', color: THEME.text }}>{selectedJob.deliver || 'local'}</span>
                )}
              </div>
            </div>

            <div>
              <label style={{ fontSize: '11px', color: THEME['text-dim'], display: 'block', marginBottom: '4px' }}>Skills</label>
              {isEditing ? (
                <input
                  type="text"
                  value={(editForm.skills || []).join(', ')}
                  onChange={e => updateEditForm('skills', e.target.value.split(',').map(s => s.trim()).filter(Boolean))}
                  placeholder="逗号分隔多个 skills"
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: THEME['bg-deep'],
                    color: THEME.text,
                    border: `1px solid ${THEME.border}`,
                    borderRadius: '4px',
                    fontSize: '12px',
                    outline: 'none',
                  }}
                />
              ) : (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                  {(selectedJob.skills || []).length > 0 ? (
                    selectedJob.skills.map((skill, i) => (
                      <span key={i} style={{
                        padding: '2px 8px',
                        background: THEME['bg-hover'],
                        borderRadius: '3px',
                        fontSize: '11px',
                        color: THEME.primary,
                      }}>
                        {skill}
                      </span>
                    ))
                  ) : (
                    <span style={{ fontSize: '12px', color: THEME['text-dim'] }}>无</span>
                  )}
                </div>
              )}
            </div>

            {selectedJob.last_error && (
              <div>
                <label style={{ fontSize: '11px', color: THEME.error, display: 'block', marginBottom: '4px' }}>最近错误</label>
                <div style={{
                  padding: '8px',
                  background: 'rgba(239, 68, 68, 0.1)',
                  borderRadius: '4px',
                  fontSize: '11px',
                  color: THEME.error,
                  maxHeight: '60px',
                  overflowY: 'auto',
                  whiteSpace: 'pre-wrap',
                }}>
                  {selectedJob.last_error}
                </div>
              </div>
            )}

            {/* Action Buttons */}
            <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
              {isEditing ? (
                <>
                  <button
                    onClick={saveEdit}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME.primary,
                      color: THEME['bg-deep'],
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                      fontWeight: 600,
                    }}
                  >
                    保存
                  </button>
                  <button
                    onClick={cancelEditing}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME['bg-hover'],
                      color: THEME.text,
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                    }}
                  >
                    取消
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={startEditing}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME.primary,
                      color: THEME['bg-deep'],
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                      fontWeight: 600,
                    }}
                  >
                    编辑
                  </button>
                  <button
                    onClick={confirmDelete}
                    style={{
                      flex: 1,
                      padding: '8px',
                      background: THEME.error,
                      color: '#fff',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '12px',
                    }}
                  >
                    删除
                  </button>
                </>
              )}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── Main Toolbar Component ────────────────────────────────────────────────────

export default function HermesToolbar() {
  const [activeTab, setActiveTab] = useState<'memory' | 'skills' | 'cron'>('memory');

  return (
    <div style={{
      flex: 1,
      minHeight: '200px',
      background: THEME['bg-deep'],
      border: `1px solid ${THEME.border}`,
      borderRadius: '8px',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Tab buttons */}
      <div style={{
        display: 'flex',
        borderBottom: `1px solid ${THEME.border}`,
        flexShrink: 0,
      }}>
        {[
          { id: 'memory', label: '💾 记忆', shortLabel: '记忆' },
          { id: 'skills', label: '🛠️ Skills', shortLabel: 'Skills' },
          { id: 'cron', label: '⏰ 定时任务', shortLabel: '定时' },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as typeof activeTab)}
            style={{
              flex: 1,
              padding: '10px 8px',
              fontSize: '11px',
              background: activeTab === tab.id ? THEME['bg-surface'] : 'transparent',
              color: activeTab === tab.id ? THEME.primary : THEME['text-dim'],
              border: 'none',
              borderBottom: activeTab === tab.id ? `2px solid ${THEME.primary}` : '2px solid transparent',
              cursor: 'pointer',
              fontWeight: activeTab === tab.id ? 600 : 400,
              whiteSpace: 'nowrap',
            }}
          >
            {tab.shortLabel}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: '12px', overflow: 'hidden', minHeight: 0 }}>
        {activeTab === 'memory' && <MemorySectionCompact />}
        {activeTab === 'skills' && <SkillsSectionCompact />}
        {activeTab === 'cron' && <CronSectionCompact />}
      </div>
    </div>
  );
}
