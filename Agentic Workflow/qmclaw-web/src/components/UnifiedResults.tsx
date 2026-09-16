"use client";

import { useState } from "react";

interface AnalysisResult {
  success: boolean;
  stdout?: string;
  stderr?: string;
  metrics?: Record<string, number | string>;
  error?: string;
}

interface UnifiedResultsProps {
  plotUrl?: string | null;
  plotLoading?: boolean;
  analysisResult?: AnalysisResult | null;
  llmSummary?: string | null;
  isSummarizing?: boolean;
  onDownloadPlot?: () => void;
  onAiSummary?: () => void;
}

export default function UnifiedResults({
  plotUrl,
  plotLoading,
  analysisResult,
  llmSummary,
  isSummarizing,
  onDownloadPlot,
  onAiSummary,
}: UnifiedResultsProps) {
  const [showStdout, setShowStdout] = useState(false);

  // 如果没有结果且没有绘图，保持空白
  if (!plotUrl && !plotLoading && !analysisResult && !llmSummary) {
    return (
      <div
        style={{
          border: "1px solid #1e293b",
          borderRadius: "0.5rem",
          background: "#0a0f1a",
          padding: "2rem",
          textAlign: "center",
          color: "#475569",
          fontSize: "0.75rem",
        }}
      >
        📊 执行命令后，结果将显示在这里
      </div>
    );
  }

  const hasPlot = plotUrl || plotLoading;
  const hasAnalysis = analysisResult;

  if (!hasPlot && !hasAnalysis && !llmSummary) {
    return null;
  }

  return (
    <div
      style={{
        border: "1px solid #1e293b",
        borderRadius: "0.5rem",
        background: "#0a0f1a",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "0.5rem 0.75rem",
          fontSize: "0.7rem",
          fontWeight: 600,
          color: "#475569",
          letterSpacing: "0.1em",
          borderBottom: "1px solid #1e293b",
          background: "#0f172a",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>📋 RESULTS</span>
        {plotUrl && (
          <button
            onClick={onDownloadPlot}
            style={{
              padding: "0.2rem 0.5rem",
              background: "transparent",
              border: "1px solid #334155",
              borderRadius: "0.25rem",
              color: "#94a3b8",
              cursor: "pointer",
              fontSize: "0.65rem",
            }}
          >
            📥 下载 PNG
          </button>
        )}
      </div>

      {/* Content - Left/Right Layout */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 350px",
          minHeight: "300px",
        }}
      >
        {/* Left: Plot Image */}
        {hasPlot ? (
          <div
            style={{
              padding: "0.75rem",
              borderRight: "1px solid #1e293b",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div
              style={{
                fontSize: "0.7rem",
                fontWeight: 600,
                color: "#3b82f6",
                marginBottom: "0.5rem",
              }}
            >
              📊 绘图结果
            </div>
            {plotLoading ? (
              <div
                style={{
                  background: "#0f172a",
                  borderRadius: "0.25rem",
                  padding: "2rem",
                  textAlign: "center",
                  color: "#64748b",
                  flex: 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                ⏳ 正在生成图像...
              </div>
            ) : plotUrl ? (
              <div
                style={{
                  background: "#0f172a",
                  borderRadius: "0.25rem",
                  padding: "0.5rem",
                  flex: 1,
                  overflow: "auto",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <img
                  src={plotUrl}
                  alt="Plot"
                  style={{
                    maxWidth: "100%",
                    maxHeight: "400px",
                    objectFit: "contain",
                  }}
                />
              </div>
            ) : null}
          </div>
        ) : (
          <div
            style={{
              padding: "0.75rem",
              borderRight: "1px solid #1e293b",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#475569",
              fontSize: "0.75rem",
            }}
          >
            📊 等待绘图...
          </div>
        )}

        {/* Right: Analysis + LLM Summary */}
        {hasPlot && (
          <div
            style={{
              padding: "0.75rem",
              overflow: "auto",
              maxHeight: "500px",
            }}
          >
            {/* Analysis Section */}
            {hasAnalysis ? (
              <div style={{ marginBottom: llmSummary ? "1rem" : 0 }}>
                <div
                  style={{
                    fontSize: "0.7rem",
                    fontWeight: 600,
                    color: "#f59e0b",
                    marginBottom: "0.5rem",
                  }}
                >
                  📈 分析结果
                </div>

                {/* Status */}
                <div
                  style={{
                    fontSize: "0.75rem",
                    color: analysisResult?.success ? "#22c55e" : "#ef4444",
                    marginBottom: "0.5rem",
                  }}
                >
                  {analysisResult?.success ? "✅ 成功" : "❌ 失败"}
                </div>

                {/* Metrics */}
                {analysisResult?.success &&
                  analysisResult.metrics &&
                  Object.keys(analysisResult.metrics).length > 0 && (
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fill, minmax(80px, 1fr))",
                        gap: "0.5rem",
                        marginBottom: "0.75rem",
                      }}
                    >
                      {Object.entries(analysisResult.metrics).map(([key, value]) => (
                        <div
                          key={key}
                          style={{
                            background: "#0f172a",
                            borderRadius: "0.25rem",
                            padding: "0.3rem 0.4rem",
                            borderLeft: "2px solid #f59e0b",
                          }}
                        >
                          <div style={{ fontSize: "0.55rem", color: "#64748b" }}>
                            {key}
                          </div>
                          <div
                            style={{
                              fontSize: "0.8rem",
                              color: "#22c55e",
                              fontFamily: "monospace",
                              fontWeight: 600,
                            }}
                          >
                            {typeof value === "number"
                              ? value.toFixed(4)
                              : String(value)}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                {/* Stdout Toggle */}
                {(analysisResult?.stdout || analysisResult?.error) && (
                  <details open={!!analysisResult.error}>
                    <summary
                      style={{
                        fontSize: "0.65rem",
                        color: "#64748b",
                        cursor: "pointer",
                        marginBottom: "0.25rem",
                      }}
                    >
                      {showStdout ? "▼" : "▶"} 输出详情
                    </summary>
                    {showStdout && (
                      <pre
                        style={{
                          fontSize: "0.65rem",
                          color: "#22c55e",
                          fontFamily: "monospace",
                          whiteSpace: "pre-wrap",
                          wordBreak: "break-all",
                          maxHeight: "100px",
                          overflow: "auto",
                          background: "#0a0f1a",
                          padding: "0.5rem",
                          borderRadius: "0.25rem",
                          border: "1px solid #1e293b",
                        }}
                      >
                        {analysisResult.error || analysisResult.stdout}
                      </pre>
                    )}
                  </details>
                )}
              </div>
            ) : (
              <div
                style={{
                  fontSize: "0.7rem",
                  fontWeight: 600,
                  color: "#f59e0b",
                  marginBottom: "0.5rem",
                }}
              >
                📈 分析结果
              </div>
            )}

            {/* No analysis placeholder */}
            {!hasAnalysis && (
              <div
                style={{
                  fontSize: "0.75rem",
                  color: "#475569",
                  background: "#0f172a",
                  padding: "0.5rem",
                  borderRadius: "0.25rem",
                  marginBottom: "0.5rem",
                }}
              >
                暂无分析结果
                <br />
                <span style={{ fontSize: "0.65rem" }}>
                  点击上方「ANALYZE」运行分析命令
                </span>
              </div>
            )}

            {/* LLM Summary Section */}
            {llmSummary && (
              <div
                style={{
                  marginTop: hasAnalysis ? "0.75rem" : 0,
                  paddingTop: hasAnalysis ? "0.75rem" : 0,
                  borderTop: hasAnalysis ? "1px solid #1e293b" : "none",
                }}
              >
                <div
                  style={{
                    fontSize: "0.7rem",
                    fontWeight: 600,
                    color: "#a78bfa",
                    marginBottom: "0.5rem",
                  }}
                >
                  🤖 AI Summary
                </div>
                <div
                  style={{
                    fontSize: "0.75rem",
                    color: "#e2e8f0",
                    lineHeight: 1.6,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    background: "#1e1b4b",
                    padding: "0.5rem",
                    borderRadius: "0.25rem",
                    border: "1px solid #6366f1",
                    maxHeight: "200px",
                    overflow: "auto",
                  }}
                >
                  {llmSummary}
                </div>
              </div>
            )}

            {/* AI Summary Button */}
            {onAiSummary && !llmSummary && (
              <button
                onClick={onAiSummary}
                disabled={isSummarizing}
                style={{
                  marginTop: "0.75rem",
                  padding: "0.4rem 0.75rem",
                  borderRadius: "0.25rem",
                  border: "none",
                  background: isSummarizing ? "#334155" : "#6366f1",
                  color: isSummarizing ? "#64748b" : "#fff",
                  fontSize: "0.75rem",
                  fontWeight: 600,
                  cursor: isSummarizing ? "not-allowed" : "pointer",
                  width: "100%",
                }}
              >
                {isSummarizing ? "⏳ 生成中..." : "🤖 AI Summary"}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
