"use client";

/**
 * Memory Panel - Long-term Memory and Reflection Interface
 *
 * Provides:
 * - View episode history (past tasks)
 * - View learned skills
 * - Memory statistics
 * - Manual reflection trigger
 * - Skill management (delete)
 */

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface Episode {
  id: string;
  timestamp: string;
  task: string;
  qubit?: string;
  status: string;
  metrics?: Record<string, number>;
  reflection?: {
    summary?: string;
    quality?: number;
    lessons?: string[];
    suggestions?: string[];
  };
  tags?: string[];
}

interface Skill {
  id: string;
  name: string;
  description?: string;
  triggerKeywords?: string[];
  successCount?: number;
  failureCount?: number;
  status?: string;
  createdAt?: string;
}

interface MemoryStats {
  episodeCount: number;
  skillCount: number;
  successRate: number;
  storageMB?: number;
  lastUpdated?: string;
}

type Tab = "episodes" | "skills" | "stats";

export default function MemoryPanel() {
  const [activeTab, setActiveTab] = useState<Tab>("episodes");
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [selectedEpisode, setSelectedEpisode] = useState<Episode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchEpisodes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.memoryListEpisodes(50) as any;
      if (result.error) {
        setError(result.error);
      } else {
        setEpisodes(result.episodes || []);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.memoryListSkills() as any;
      if (result.error) {
        setError(result.error);
      } else {
        setSkills(result.skills || []);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const result = await api.memoryStats() as any;
      if (!result.error) {
        setStats(result);
      }
    } catch (e: any) {
      console.error("Failed to fetch stats:", e);
    }
  }, []);

  const fetchEpisodeDetail = useCallback(async (episodeId: string) => {
    try {
      const result = await api.memoryGetEpisode(episodeId) as any;
      if (!result.error && result.episode) {
        setSelectedEpisode(result.episode);
      }
    } catch (e: any) {
      console.error("Failed to fetch episode detail:", e);
    }
  }, []);

  const handleArchiveEpisode = async (episodeId: string) => {
    if (!confirm("归档此任务记录？归档后仍可查看，但不会出现在默认列表中。")) return;
    try {
      await api.memoryArchiveEpisode(episodeId);
      fetchEpisodes();
      if (selectedEpisode?.id === episodeId) {
        setSelectedEpisode(null);
      }
    } catch (e: any) {
      alert("归档失败: " + e.message);
    }
  };

  const handleDeleteSkill = async (skillId: string) => {
    if (!confirm("删除此技能？此操作不可撤销。")) return;
    try {
      await api.memoryDeleteSkill(skillId);
      fetchSkills();
    } catch (e: any) {
      alert("删除失败: " + e.message);
    }
  };

  const handleTriggerReflection = async (episodeId: string) => {
    try {
      const result = await api.memoryReflect(episodeId) as any;
      if (result.error) {
        alert("反思失败: " + result.error);
      } else {
        alert("反思完成！\n\n" + (result.report || ""));
        fetchEpisodes();
      }
    } catch (e: any) {
      alert("反思失败: " + e.message);
    }
  };

  useEffect(() => {
    if (activeTab === "episodes") {
      fetchEpisodes();
    } else if (activeTab === "skills") {
      fetchSkills();
    } else if (activeTab === "stats") {
      fetchStats();
    }
  }, [activeTab, fetchEpisodes, fetchSkills, fetchStats]);

  const renderTab = (tab: Tab, label: string, icon: string) => (
    <button
      onClick={() => setActiveTab(tab)}
      style={{
        padding: "8px 16px",
        background: activeTab === tab ? "#1e3a5f" : "transparent",
        border: "none",
        borderBottom: activeTab === tab ? "2px solid #38bdf8" : "2px solid transparent",
        color: activeTab === tab ? "#38bdf8" : "#64748b",
        fontSize: "13px",
        fontWeight: 600,
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: "6px",
      }}
    >
      {icon} {label}
    </button>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "12px" }}>
      {/* Header with tabs */}
      <div style={{
        background: "#1e293b",
        borderRadius: "8px",
        padding: "8px 16px",
        border: "1px solid #334155",
        display: "flex",
        alignItems: "center",
        gap: "4px",
      }}>
        <span style={{ fontSize: "14px", fontWeight: 700, color: "#e2e8f0", marginRight: "16px" }}>
          🧠 记忆中心
        </span>
        {renderTab("episodes", "任务记录", "📋")}
        {renderTab("skills", "技能库", "🎯")}
        {renderTab("stats", "统计", "📊")}
      </div>

      {/* Error display */}
      {error && (
        <div style={{
          padding: "12px",
          background: "#7f1d1d",
          borderRadius: "8px",
          color: "#fca5a5",
          fontSize: "13px",
        }}>
          ⚠️ {error}
          <button
            onClick={() => setError(null)}
            style={{
              marginLeft: "12px",
              padding: "4px 8px",
              background: "#991b1b",
              border: "none",
              borderRadius: "4px",
              color: "#fff",
              cursor: "pointer",
              fontSize: "11px",
            }}
          >
            关闭
          </button>
        </div>
      )}

      {/* Content area */}
      <div style={{
        flex: 1,
        overflowY: "auto",
        display: "flex",
        gap: "12px",
      }}>
        {/* Main content */}
        <div style={{ flex: 1, overflowY: "auto" }}>
          {loading ? (
            <div style={{ textAlign: "center", color: "#64748b", padding: "40px" }}>
              加载中...
            </div>
          ) : activeTab === "episodes" ? (
            <EpisodesList
              episodes={episodes}
              onSelect={fetchEpisodeDetail}
              onArchive={handleArchiveEpisode}
              onReflect={handleTriggerReflection}
            />
          ) : activeTab === "skills" ? (
            <SkillsList
              skills={skills}
              onDelete={handleDeleteSkill}
            />
          ) : (
            <StatsView stats={stats} />
          )}
        </div>

        {/* Detail panel */}
        {selectedEpisode && (
          <div style={{
            width: "400px",
            background: "#1e293b",
            borderRadius: "8px",
            border: "1px solid #334155",
            padding: "16px",
            overflowY: "auto",
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
            }}>
              <span style={{ fontSize: "13px", fontWeight: 700, color: "#e2e8f0" }}>
                📋 任务详情
              </span>
              <button
                onClick={() => setSelectedEpisode(null)}
                style={{
                  padding: "4px 8px",
                  background: "transparent",
                  border: "1px solid #334155",
                  borderRadius: "4px",
                  color: "#64748b",
                  cursor: "pointer",
                  fontSize: "11px",
                }}
              >
                关闭
              </button>
            </div>

            <DetailView episode={selectedEpisode} />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────────

function EpisodesList({
  episodes,
  onSelect,
  onArchive,
  onReflect,
}: {
  episodes: Episode[];
  onSelect: (id: string) => void;
  onArchive: (id: string) => void;
  onReflect: (id: string) => void;
}) {
  if (episodes.length === 0) {
    return (
      <div style={{ textAlign: "center", color: "#475569", padding: "60px 20px" }}>
        <div style={{ fontSize: "48px", marginBottom: "12px" }}>📭</div>
        <div style={{ fontSize: "14px" }}>暂无任务记录</div>
        <div style={{ fontSize: "12px", color: "#334155", marginTop: "8px" }}>
          执行测控任务后，记录会自动保存在这里
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      {episodes.map((ep) => (
        <div
          key={ep.id}
          style={{
            background: "#1e293b",
            borderRadius: "8px",
            border: "1px solid #334155",
            padding: "12px 16px",
            cursor: "pointer",
            transition: "border-color 0.15s",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.borderColor = "#475569")}
          onMouseLeave={(e) => (e.currentTarget.style.borderColor = "#334155")}
          onClick={() => onSelect(ep.id)}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div style={{ flex: 1 }}>
              <div style={{
                fontSize: "13px",
                color: "#e2e8f0",
                marginBottom: "4px",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}>
                {ep.task}
              </div>
              <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                {ep.qubit && (
                  <span style={{
                    padding: "2px 6px",
                    background: "#7c3aed",
                    borderRadius: "4px",
                    fontSize: "10px",
                    color: "#e2e8f0",
                  }}>
                    {ep.qubit}
                  </span>
                )}
                <span style={{
                  padding: "2px 6px",
                  background: ep.status === "success" ? "#166534" : "#7f1d1d",
                  borderRadius: "4px",
                  fontSize: "10px",
                  color: ep.status === "success" ? "#86efac" : "#fca5a5",
                }}>
                  {ep.status === "success" ? "✅ 成功" : "⚠️ 失败"}
                </span>
                {ep.reflection?.quality && (
                  <span style={{ fontSize: "10px", color: "#64748b" }}>
                    质量: {ep.reflection.quality}%
                  </span>
                )}
                <span style={{ fontSize: "10px", color: "#475569" }}>
                  {new Date(ep.timestamp).toLocaleString("zh-CN")}
                </span>
              </div>
            </div>
            <div style={{ display: "flex", gap: "4px", marginLeft: "8px" }}>
              <button
                onClick={(e) => { e.stopPropagation(); onReflect(ep.id); }}
                title="重新反思"
                style={{
                  padding: "4px 8px",
                  background: "#0f172a",
                  border: "1px solid #334155",
                  borderRadius: "4px",
                  color: "#64748b",
                  cursor: "pointer",
                  fontSize: "10px",
                }}
              >
                🤔
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onArchive(ep.id); }}
                title="归档"
                style={{
                  padding: "4px 8px",
                  background: "#0f172a",
                  border: "1px solid #334155",
                  borderRadius: "4px",
                  color: "#64748b",
                  cursor: "pointer",
                  fontSize: "10px",
                }}
              >
                📦
              </button>
            </div>
          </div>
          {ep.reflection?.summary && (
            <div style={{
              marginTop: "8px",
              padding: "8px",
              background: "#0f172a",
              borderRadius: "4px",
              fontSize: "11px",
              color: "#94a3b8",
            }}>
              💡 {ep.reflection.summary}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function SkillsList({
  skills,
  onDelete,
}: {
  skills: Skill[];
  onDelete: (id: string) => void;
}) {
  if (skills.length === 0) {
    return (
      <div style={{ textAlign: "center", color: "#475569", padding: "60px 20px" }}>
        <div style={{ fontSize: "48px", marginBottom: "12px" }}>🎯</div>
        <div style={{ fontSize: "14px" }}>暂无已学技能</div>
        <div style={{ fontSize: "12px", color: "#334155", marginTop: "8px" }}>
          成功完成任务后，智能体会自动提取技能
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      {skills.map((skill) => {
        const successRate = skill.successCount && skill.failureCount
          ? (skill.successCount / (skill.successCount + skill.failureCount)) * 100
          : 100;

        return (
          <div
            key={skill.id}
            style={{
              background: "#1e293b",
              borderRadius: "8px",
              border: "1px solid #334155",
              padding: "12px 16px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{ fontSize: "13px", fontWeight: 600, color: "#e2e8f0", marginBottom: "4px" }}>
                  {skill.name}
                </div>
                {skill.description && (
                  <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "8px" }}>
                    {skill.description}
                  </div>
                )}
                {skill.triggerKeywords && skill.triggerKeywords.length > 0 && (
                  <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", marginBottom: "8px" }}>
                    {skill.triggerKeywords.map((kw, i) => (
                      <span
                        key={i}
                        style={{
                          padding: "2px 6px",
                          background: "#7c3aed",
                          borderRadius: "4px",
                          fontSize: "10px",
                          color: "#e2e8f0",
                        }}
                      >
                        {kw}
                      </span>
                    ))}
                  </div>
                )}
                <div style={{ display: "flex", gap: "12px", fontSize: "10px", color: "#64748b" }}>
                  <span>✅ {skill.successCount || 0} 成功</span>
                  <span>❌ {skill.failureCount || 0} 失败</span>
                  <span>📈 {successRate.toFixed(0)}% 成功率</span>
                </div>
              </div>
              <button
                onClick={() => onDelete(skill.id)}
                title="删除技能"
                style={{
                  padding: "4px 8px",
                  background: "transparent",
                  border: "1px solid #334155",
                  borderRadius: "4px",
                  color: "#64748b",
                  cursor: "pointer",
                  fontSize: "10px",
                }}
              >
                🗑️
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function StatsView({ stats }: { stats: MemoryStats | null }) {
  if (!stats) {
    return (
      <div style={{ textAlign: "center", color: "#475569", padding: "60px 20px" }}>
        加载中...
      </div>
    );
  }

  const statCards = [
    { label: "任务记录", value: stats.episodeCount, icon: "📋", color: "#38bdf8" },
    { label: "已学技能", value: stats.skillCount, icon: "🎯", color: "#a78bfa" },
    { label: "成功率", value: `${stats.successRate}%`, icon: "📈", color: "#34d399" },
    { label: "存储大小", value: stats.storageMB ? `${stats.storageMB} MB` : "N/A", icon: "💾", color: "#fbbf24" },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "12px" }}>
      {statCards.map((card) => (
        <div
          key={card.label}
          style={{
            background: "#1e293b",
            borderRadius: "8px",
            border: "1px solid #334155",
            padding: "20px",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: "32px", marginBottom: "8px" }}>{card.icon}</div>
          <div style={{
            fontSize: "24px",
            fontWeight: 700,
            color: card.color,
            fontFamily: "monospace",
          }}>
            {card.value}
          </div>
          <div style={{ fontSize: "12px", color: "#64748b", marginTop: "4px" }}>
            {card.label}
          </div>
        </div>
      ))}

      {stats.lastUpdated && (
        <div
          style={{
            gridColumn: "1 / -1",
            background: "#1e293b",
            borderRadius: "8px",
            border: "1px solid #334155",
            padding: "12px",
            textAlign: "center",
            fontSize: "11px",
            color: "#475569",
          }}
        >
          最后更新: {new Date(stats.lastUpdated).toLocaleString("zh-CN")}
        </div>
      )}
    </div>
  );
}

function DetailView({ episode }: { episode: Episode }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      {/* Task */}
      <div>
        <div style={{ fontSize: "11px", color: "#64748b", marginBottom: "4px" }}>任务</div>
        <div style={{ fontSize: "13px", color: "#e2e8f0" }}>{episode.task}</div>
      </div>

      {/* Meta */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        {episode.qubit && (
          <span style={{
            padding: "2px 8px",
            background: "#7c3aed",
            borderRadius: "4px",
            fontSize: "11px",
            color: "#e2e8f0",
          }}>
            {episode.qubit}
          </span>
        )}
        <span style={{
          padding: "2px 8px",
          background: episode.status === "success" ? "#166534" : "#7f1d1d",
          borderRadius: "4px",
          fontSize: "11px",
          color: episode.status === "success" ? "#86efac" : "#fca5a5",
        }}>
          {episode.status === "success" ? "✅ 成功" : "⚠️ 失败"}
        </span>
        <span style={{ fontSize: "11px", color: "#475569" }}>
          {new Date(episode.timestamp).toLocaleString("zh-CN")}
        </span>
      </div>

      {/* Metrics */}
      {episode.metrics && Object.keys(episode.metrics).length > 0 && (
        <div>
          <div style={{ fontSize: "11px", color: "#64748b", marginBottom: "4px" }}>关键指标</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "4px" }}>
            {Object.entries(episode.metrics).map(([k, v]) => (
              <div
                key={k}
                style={{
                  background: "#0f172a",
                  borderRadius: "4px",
                  padding: "4px 8px",
                  fontSize: "11px",
                }}
              >
                <span style={{ color: "#64748b" }}>{k}:</span>
                <span style={{ color: "#22c55e", marginLeft: "4px", fontFamily: "monospace" }}>
                  {typeof v === "number" ? v.toFixed(4) : v}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reflection */}
      {episode.reflection && (
        <div>
          <div style={{ fontSize: "11px", color: "#64748b", marginBottom: "4px" }}>💭 反思</div>
          <div style={{ background: "#0f172a", borderRadius: "8px", padding: "12px" }}>
            {episode.reflection.summary && (
              <div style={{ fontSize: "12px", color: "#e2e8f0", marginBottom: "8px" }}>
                {episode.reflection.summary}
              </div>
            )}
            {episode.reflection.quality && (
              <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "8px" }}>
                质量评分: {episode.reflection.quality}/100
              </div>
            )}
            {episode.reflection.lessons && episode.reflection.lessons.length > 0 && (
              <div style={{ marginTop: "8px" }}>
                <div style={{ fontSize: "10px", color: "#64748b", marginBottom: "4px" }}>📚 教训</div>
                {episode.reflection.lessons.map((lesson, i) => (
                  <div key={i} style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "2px" }}>
                    • {lesson}
                  </div>
                ))}
              </div>
            )}
            {episode.reflection.suggestions && episode.reflection.suggestions.length > 0 && (
              <div style={{ marginTop: "8px" }}>
                <div style={{ fontSize: "10px", color: "#64748b", marginBottom: "4px" }}>💡 建议</div>
                {episode.reflection.suggestions.map((sug, i) => (
                  <div key={i} style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "2px" }}>
                    • {sug}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tags */}
      {episode.tags && episode.tags.length > 0 && (
        <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
          {episode.tags.map((tag, i) => (
            <span
              key={i}
              style={{
                padding: "2px 6px",
                background: "#334155",
                borderRadius: "4px",
                fontSize: "10px",
                color: "#94a3b8",
              }}
            >
              {tag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
