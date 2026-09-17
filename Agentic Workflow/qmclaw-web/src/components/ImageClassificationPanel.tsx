"use client";

/**
 * Image Classification Panel
 * Full-panel tab for image classification with model management and training
 */

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface ClassificationResult {
  imagePath: string;
  label: string;
  confidence: number;
  margin: number;
  needReview: boolean;
  probClass0?: number;
  probClass1?: number;
  error?: string;
}

interface ModelInfo {
  exists: boolean;
  modelPath?: string;
  fileSizeMB?: number;
  accuracy?: number;
  f1Score?: number;
  totalPredictions?: number;
  error?: string;
}

interface Stats {
  totalPredictions: number;
  accuracy?: number;
  f1Score?: number;
}

export default function ImageClassificationPanel() {
  // ── State ─────────────────────────────────────────────────────────────────
  const [folderPath, setFolderPath] = useState("");
  const [backend, setBackend] = useState("pytorch");
  const [reviewThreshold, setReviewThreshold] = useState(0.75);
  const [marginThreshold, setMarginThreshold] = useState(0.15);

  const [results, setResults] = useState<ClassificationResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Model info
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [loadingModel, setLoadingModel] = useState(false);

  // Stats
  const [stats, setStats] = useState<Stats | null>(null);

  // Training
  const [training, setTraining] = useState(false);
  const [trainEpochs, setTrainEpochs] = useState(20);
  const [trainBatchSize, setTrainBatchSize] = useState(32);
  const [trainImbalanceMode, setTrainImbalanceMode] = useState("weighted");
  const [trainResult, setTrainResult] = useState<{ success?: boolean; finalValAccuracy?: number; finalValF1?: number; error?: string } | null>(null);

  // ── Fetch model info ──────────────────────────────────────────────────────
  const fetchModelInfo = useCallback(async () => {
    setLoadingModel(true);
    try {
      const data = await api.getModelInfo() as ModelInfo;
      setModelInfo(data);
    } catch (e: any) {
      setModelInfo({ exists: false, error: e.message });
    } finally {
      setLoadingModel(false);
    }
  }, []);

  // ── Fetch stats ───────────────────────────────────────────────────────────
  const fetchStats = useCallback(async () => {
    try {
      const data = await api.getClassificationStats(24) as Stats & { error?: string };
      if (data.error) return;
      setStats(data);
    } catch {
      // ignore
    }
  }, []);

  // ── Initial load ──────────────────────────────────────────────────────────
  useEffect(() => {
    fetchModelInfo();
    fetchStats();
  }, [fetchModelInfo, fetchStats]);

  // ── Classify folder ───────────────────────────────────────────────────────
  const handleClassifyFolder = async () => {
    if (!folderPath.trim()) {
      setError("请输入图片目录路径");
      return;
    }
    setError(null);
    setClassifying(true);
    setResults([]);
    try {
      const data = await api.classifyImages({
        folderPath: folderPath.trim(),
        backend,
        reviewThreshold,
        marginThreshold,
      }) as { results?: ClassificationResult[]; error?: string };
      if (data.error) {
        setError(data.error);
        return;
      }
      setResults(data.results || []);
    } catch (e: any) {
      setError("分类失败: " + e.message);
    } finally {
      setClassifying(false);
    }
  };

  // ── Refresh model status ──────────────────────────────────────────────────
  const handleRefreshModel = () => {
    fetchModelInfo();
    fetchStats();
  };

  // ── Train model ───────────────────────────────────────────────────────────
  const handleTrain = async () => {
    setError(null);
    setTraining(true);
    setTrainResult(null);
    try {
      const data = await api.trainModel({
        epochs: trainEpochs,
        batchSize: trainBatchSize,
        imbalanceMode: trainImbalanceMode,
      }) as { success?: boolean; finalValAccuracy?: number; finalValF1?: number; error?: string };
      setTrainResult(data);
      if (data.success) {
        fetchModelInfo();
        fetchStats();
      }
    } catch (e: any) {
      setTrainResult({ error: e.message });
    } finally {
      setTraining(false);
    }
  };

  // ── Computed stats ────────────────────────────────────────────────────────
  const class0Count = results.filter(r => r.label === "class_0").length;
  const class1Count = results.filter(r => r.label === "class_1").length;
  const needReviewCount = results.filter(r => r.needReview).length;
  const errorCount = results.filter(r => r.label === "error").length;

  const confidenceValues = results.filter(r => r.label !== "error").map(r => r.confidence);
  const avgConfidence = confidenceValues.length > 0
    ? confidenceValues.reduce((a, b) => a + b, 0) / confidenceValues.length
    : 0;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "1fr 320px",
      gap: "16px",
      height: "100%",
      overflow: "hidden",
    }}>
      {/* ── Left: Main content ─────────────────────────────────────────────── */}
      <div style={{ display: "flex", flexDirection: "column", gap: "16px", overflow: "auto" }}>

        {/* Header */}
        <div style={{
          background: "#1e293b",
          borderRadius: "8px",
          padding: "16px",
          border: "1px solid #334155",
        }}>
          <h2 style={{ margin: "0 0 4px 0", fontSize: "14px", color: "#e2e8f0", fontWeight: 600 }}>
            🧠 图像分类
          </h2>
          <p style={{ margin: 0, fontSize: "11px", color: "#64748b" }}>
            使用 ResNet18 模型对实验图像进行二分类（class_0 / class_1）
          </p>
        </div>

        {/* ── Input section ──────────────────────────────────────────────── */}
        <div style={{
          background: "#1e293b",
          borderRadius: "8px",
          padding: "16px",
          border: "1px solid #334155",
        }}>
          <h3 style={{ margin: "0 0 12px 0", fontSize: "12px", color: "#94a3b8", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            📂 分类参数
          </h3>

          <div style={{ marginBottom: "12px" }}>
            <label style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", display: "block" }}>
              图片目录路径
            </label>
            <input
              type="text"
              value={folderPath}
              onChange={(e) => setFolderPath(e.target.value)}
              placeholder="D:\Documents\实验图片"
              style={{
                width: "100%",
                padding: "8px 12px",
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: "6px",
                color: "#e2e8f0",
                fontSize: "12px",
                fontFamily: "monospace",
                boxSizing: "border-box",
              }}
              onKeyDown={(e) => e.key === "Enter" && handleClassifyFolder()}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "8px", marginBottom: "12px" }}>
            <div>
              <label style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", display: "block" }}>推理后端</label>
              <select
                value={backend}
                onChange={(e) => setBackend(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: "#0f172a",
                  border: "1px solid #334155",
                  borderRadius: "6px",
                  color: "#e2e8f0",
                  fontSize: "12px",
                }}
              >
                <option value="pytorch">PyTorch</option>
                <option value="onnx">ONNX Runtime</option>
                <option value="quantized">INT8 Quantized</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", display: "block" }}>复核阈值</label>
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={reviewThreshold}
                onChange={(e) => setReviewThreshold(parseFloat(e.target.value) || 0.75)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: "#0f172a",
                  border: "1px solid #334155",
                  borderRadius: "6px",
                  color: "#e2e8f0",
                  fontSize: "12px",
                  boxSizing: "border-box",
                }}
              />
            </div>

            <div>
              <label style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", display: "block" }}>Margin 阈值</label>
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={marginThreshold}
                onChange={(e) => setMarginThreshold(parseFloat(e.target.value) || 0.15)}
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: "#0f172a",
                  border: "1px solid #334155",
                  borderRadius: "6px",
                  color: "#e2e8f0",
                  fontSize: "12px",
                  boxSizing: "border-box",
                }}
              />
            </div>
          </div>

          {error && (
            <div style={{
              padding: "8px 12px",
              background: "#3a1a1a",
              border: "1px solid #7f1d1d",
              borderRadius: "6px",
              fontSize: "11px",
              color: "#f87171",
              marginBottom: "12px",
            }}>
              ❌ {error}
            </div>
          )}

          <button
            onClick={handleClassifyFolder}
            disabled={classifying}
            style={{
              padding: "8px 20px",
              background: classifying ? "#1e3a5f" : "#0369a1",
              border: "none",
              borderRadius: "6px",
              color: "#e2e8f0",
              fontSize: "12px",
              fontWeight: 600,
              cursor: classifying ? "not-allowed" : "pointer",
              opacity: classifying ? 0.7 : 1,
            }}
          >
            {classifying ? "⏳ 分类中..." : "🔍 开始分类"}
          </button>
        </div>

        {/* ── Results table ──────────────────────────────────────────────── */}
        {results.length > 0 && (
          <div style={{
            background: "#1e293b",
            borderRadius: "8px",
            padding: "16px",
            border: "1px solid #334155",
            flex: 1,
            overflow: "auto",
          }}>
            <h3 style={{ margin: "0 0 12px 0", fontSize: "12px", color: "#94a3b8", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              📊 分类结果 ({results.length} 张图片)
            </h3>

            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #334155" }}>
                    <th style={{ textAlign: "left", padding: "6px 8px", color: "#64748b", fontWeight: 600 }}>文件名</th>
                    <th style={{ textAlign: "center", padding: "6px 8px", color: "#64748b", fontWeight: 600 }}>标签</th>
                    <th style={{ textAlign: "center", padding: "6px 8px", color: "#64748b", fontWeight: 600 }}>置信度</th>
                    <th style={{ textAlign: "center", padding: "6px 8px", color: "#64748b", fontWeight: 600 }}>Margin</th>
                    <th style={{ textAlign: "center", padding: "6px 8px", color: "#64748b", fontWeight: 600 }}>需复核</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #1e293b" }}>
                      <td style={{ padding: "6px 8px", color: "#94a3b8", fontFamily: "monospace", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={r.imagePath}>
                        {r.imagePath.split(/[/\\]/).pop()}
                      </td>
                      <td style={{ textAlign: "center", padding: "6px 8px" }}>
                        {r.label === "error" ? (
                          <span style={{ color: "#f87171" }}>❌ error</span>
                        ) : r.label === "class_1" ? (
                          <span style={{ color: "#f87171", fontWeight: 600 }}>🔴 class_1</span>
                        ) : r.label === "class_0" ? (
                          <span style={{ color: "#38bdf8", fontWeight: 600 }}>🔵 class_0</span>
                        ) : (
                          <span style={{ color: "#94a3b8" }}>{r.label}</span>
                        )}
                      </td>
                      <td style={{ textAlign: "center", padding: "6px 8px", color: r.label === "error" ? "#f87171" : "#22c55e", fontFamily: "monospace" }}>
                        {r.label === "error" ? "—" : `${(r.confidence * 100).toFixed(1)}%`}
                      </td>
                      <td style={{ textAlign: "center", padding: "6px 8px", color: "#94a3b8", fontFamily: "monospace" }}>
                        {r.label === "error" ? "—" : r.margin.toFixed(3)}
                      </td>
                      <td style={{ textAlign: "center", padding: "6px 8px" }}>
                        {r.needReview ? (
                          <span style={{ color: "#f59e0b", fontSize: "14px" }}>⚠️</span>
                        ) : (
                          <span style={{ color: "#22c55e", fontSize: "14px" }}>✅</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* ── Right: Sidebar ─────────────────────────────────────────────────── */}
      <div style={{ display: "flex", flexDirection: "column", gap: "12px", overflow: "auto" }}>

        {/* Model status */}
        <div style={{
          background: "#1e293b",
          borderRadius: "8px",
          padding: "14px",
          border: "1px solid #334155",
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
            <h3 style={{ margin: 0, fontSize: "12px", color: "#94a3b8", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              📦 模型状态
            </h3>
            <button
              onClick={handleRefreshModel}
              disabled={loadingModel}
              style={{
                padding: "3px 8px",
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: "4px",
                color: "#94a3b8",
                fontSize: "10px",
                cursor: loadingModel ? "not-allowed" : "pointer",
              }}
            >
              {loadingModel ? "..." : "🔄"}
            </button>
          </div>

          {modelInfo ? (
            modelInfo.error ? (
              <div style={{ fontSize: "11px", color: "#f87171" }}>❌ {modelInfo.error}</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "11px" }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#64748b" }}>文件</span>
                  <span style={{ color: "#22c55e", fontFamily: "monospace" }}>
                    {modelInfo.exists ? "✅ 已加载" : "❌ 未找到"}
                  </span>
                </div>
                {modelInfo.fileSizeMB && (
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#64748b" }}>大小</span>
                    <span style={{ color: "#e2e8f0", fontFamily: "monospace" }}>{modelInfo.fileSizeMB} MB</span>
                  </div>
                )}
                {modelInfo.accuracy != null && (
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#64748b" }}>准确率</span>
                    <span style={{ color: "#22c55e", fontFamily: "monospace" }}>{(modelInfo.accuracy * 100).toFixed(1)}%</span>
                  </div>
                )}
                {modelInfo.f1Score != null && (
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#64748b" }}>F1 分数</span>
                    <span style={{ color: "#22c55e", fontFamily: "monospace" }}>{modelInfo.f1Score.toFixed(3)}</span>
                  </div>
                )}
                {modelInfo.totalPredictions != null && (
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ color: "#64748b" }}>预测次数</span>
                    <span style={{ color: "#e2e8f0", fontFamily: "monospace" }}>{modelInfo.totalPredictions}</span>
                  </div>
                )}
                <div style={{ fontSize: "10px", color: "#475569", fontFamily: "monospace", marginTop: "4px", wordBreak: "break-all" }}>
                  {modelInfo.modelPath}
                </div>
              </div>
            )
          ) : (
            <div style={{ fontSize: "11px", color: "#64748b" }}>加载中...</div>
          )}
        </div>

        {/* Stats */}
        <div style={{
          background: "#1e293b",
          borderRadius: "8px",
          padding: "14px",
          border: "1px solid #334155",
        }}>
          <h3 style={{ margin: "0 0 10px 0", fontSize: "12px", color: "#94a3b8", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            📈 统计 (24h)
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "11px" }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "#64748b" }}>class_0</span>
              <span style={{ color: "#38bdf8", fontFamily: "monospace" }}>{results.length > 0 ? class0Count : (stats?.totalPredictions || 0)}</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "#64748b" }}>class_1</span>
              <span style={{ color: "#f87171", fontFamily: "monospace" }}>{results.length > 0 ? class1Count : "—"}</span>
            </div>
            {results.length > 0 && (
              <>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#64748b" }}>需复核</span>
                  <span style={{ color: "#f59e0b", fontFamily: "monospace" }}>{needReviewCount}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#64748b" }}>错误</span>
                  <span style={{ color: "#f87171", fontFamily: "monospace" }}>{errorCount}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ color: "#64748b" }}>平均置信度</span>
                  <span style={{ color: "#22c55e", fontFamily: "monospace" }}>{(avgConfidence * 100).toFixed(1)}%</span>
                </div>
              </>
            )}
          </div>
        </div>

        {/* Training */}
        <div style={{
          background: "#1e293b",
          borderRadius: "8px",
          padding: "14px",
          border: "1px solid #334155",
        }}>
          <h3 style={{ margin: "0 0 10px 0", fontSize: "12px", color: "#94a3b8", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
            🏋️ 训练入口
          </h3>

          <div style={{ marginBottom: "10px" }}>
            <label style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", display: "block" }}>Epochs</label>
            <input
              type="number"
              min="1"
              max="200"
              value={trainEpochs}
              onChange={(e) => setTrainEpochs(parseInt(e.target.value) || 20)}
              style={{
                width: "100%",
                padding: "6px 10px",
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: "6px",
                color: "#e2e8f0",
                fontSize: "12px",
                boxSizing: "border-box",
              }}
            />
          </div>

          <div style={{ marginBottom: "10px" }}>
            <label style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", display: "block" }}>Batch Size</label>
            <input
              type="number"
              min="1"
              max="256"
              value={trainBatchSize}
              onChange={(e) => setTrainBatchSize(parseInt(e.target.value) || 32)}
              style={{
                width: "100%",
                padding: "6px 10px",
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: "6px",
                color: "#e2e8f0",
                fontSize: "12px",
                boxSizing: "border-box",
              }}
            />
          </div>

          <div style={{ marginBottom: "12px" }}>
            <label style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", display: "block" }}>Imbalance Mode</label>
            <select
              value={trainImbalanceMode}
              onChange={(e) => setTrainImbalanceMode(e.target.value)}
              style={{
                width: "100%",
                padding: "6px 10px",
                background: "#0f172a",
                border: "1px solid #334155",
                borderRadius: "6px",
                color: "#e2e8f0",
                fontSize: "12px",
              }}
            >
              <option value="weighted">Weighted Sampler</option>
              <option value="focal">Focal Loss</option>
              <option value="oversample">Oversample</option>
            </select>
          </div>

          {trainResult && (
            <div style={{
              padding: "8px 10px",
              background: trainResult.error ? "#3a1a1a" : "#0f3a1a",
              borderRadius: "6px",
              fontSize: "10px",
              marginBottom: "10px",
              color: trainResult.error ? "#f87171" : "#22c55e",
            }}>
              {trainResult.error
                ? `❌ ${trainResult.error}`
                : `✅ 训练完成\n准确率: ${trainResult.finalValAccuracy != null ? (trainResult.finalValAccuracy * 100).toFixed(1) + "%" : "—"}\nF1: ${trainResult.finalValF1?.toFixed(3) || "—"}`
              }
            </div>
          )}

          <button
            onClick={handleTrain}
            disabled={training}
            style={{
              width: "100%",
              padding: "8px",
              background: training ? "#1e3a5f" : "#7c3aed",
              border: "none",
              borderRadius: "6px",
              color: "#e2e8f0",
              fontSize: "12px",
              fontWeight: 600,
              cursor: training ? "not-allowed" : "pointer",
              opacity: training ? 0.7 : 1,
            }}
          >
            {training ? "⏳ 训练中..." : "🚀 开始训练"}
          </button>
        </div>
      </div>
    </div>
  );
}
