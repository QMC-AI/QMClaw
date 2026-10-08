"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "../lib/api";

interface VariantType {
  id: string;
  name: string;
  description: string;
  params: Array<{
    name: string;
    type: string;
    min?: number;
    max?: number;
    default?: number | string | boolean;
    description?: string;
    options?: string[];
  }>;
  category: string;
}

interface VariantResult {
  variant_id: string;
  source_name: string;
  qubit: string;
  experiment_type: string;
  params: Record<string, number | string | boolean>;
  preview?: Record<string, any>;
}

interface VariantGeneratorProps {
  sourceDataset: {
    id: string;
    name: string;
    qubit?: string;
    experiment_type?: string;
  };
  onClose: () => void;
  onPlotVariant?: (variantId: string) => void;
}

export default function VariantGenerator({ sourceDataset, onClose, onPlotVariant }: VariantGeneratorProps) {
  const [variantTypes, setVariantTypes] = useState<VariantType[]>([]);
  const [selectedType, setSelectedType] = useState<string>("");
  const [params, setParams] = useState<Record<string, number | string | boolean>>({});
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<VariantResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [plotting, setPlotting] = useState(false);
  const [plotImage, setPlotImage] = useState<string | null>(null);

  // Debug: log sourceDataset when it changes
  useEffect(() => {
    console.log('[VariantGenerator] sourceDataset:', sourceDataset);
  }, [sourceDataset]);

  // Load variant types on mount
  useEffect(() => {
    const loadTypes = async () => {
      try {
        const res = await api.getVariantTypes() as { success: boolean; types?: VariantType[]; error?: string };
        if (res.success && res.types) {
          setVariantTypes(res.types);
          // Select first type by default
          if (res.types.length > 0) {
            const first = res.types[0];
            setSelectedType(first.id);
            // Set default params
            const defaults: Record<string, number | string | boolean> = {};
            first.params.forEach(p => {
              defaults[p.name] = p.default ?? (p.min ?? 0);
            });
            setParams(defaults);
          }
        } else if (res.error) {
          setError(res.error);
        }
      } catch (e: any) {
        setError(e.message || "Failed to load variant types");
      }
    };
    loadTypes();
  }, []);

  // Update params when variant type changes
  const handleTypeChange = useCallback((typeId: string) => {
    setSelectedType(typeId);
    setResult(null);
    setPlotImage(null);

    const variant = variantTypes.find(v => v.id === typeId);
    if (variant) {
      const defaults: Record<string, number | string | boolean> = {};
      variant.params.forEach(p => {
        defaults[p.name] = p.default ?? (p.type === "float" ? p.min ?? 0 : "");
      });
      setParams(defaults);
    }
  }, [variantTypes]);

  // Handle param change
  const handleParamChange = useCallback((name: string, value: number | string | boolean) => {
    setParams(prev => ({ ...prev, [name]: value }));
  }, []);

  // Generate variant
  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    setResult(null);
    setPlotImage(null);

    try {
      const res = await api.generateVariant({
        source_dataset_id: sourceDataset.id,
        variant_type: selectedType,
        params,
      }) as {
        success: boolean;
        variant_id?: string;
        source_name?: string;
        qubit?: string;
        experiment_type?: string;
        params?: Record<string, number | string | boolean>;
        preview?: Record<string, any>;
        error?: string;
      };

      if (res.success && res.variant_id) {
        setResult(res as VariantResult);
      } else {
        setError(res.error || "Failed to generate variant");
      }
    } catch (e: any) {
      setError(e.message || "Failed to generate variant");
    } finally {
      setGenerating(false);
    }
  };

  // Plot variant
  const handlePlot = async () => {
    if (!result?.variant_id) return;

    setPlotting(true);
    try {
      const res = await api.plotVariant(result.variant_id) as {
        success: boolean;
        image?: string;
        error?: string;
      };

      if (res.success && res.image) {
        setPlotImage(res.image);
        if (onPlotVariant) {
          onPlotVariant(result.variant_id);
        }
      } else {
        setError(res.error || "Failed to plot variant");
      }
    } catch (e: any) {
      setError(e.message || "Failed to plot variant");
    } finally {
      setPlotting(false);
    }
  };

  const selectedVariant = variantTypes.find(v => v.id === selectedType);

  return (
    <div style={{
      position: "fixed",
      inset: 0,
      background: "rgba(0, 0, 0, 0.7)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      zIndex: 1000,
    }}>
      <div style={{
        background: "#1e293b",
        borderRadius: "0.5rem",
        padding: "1.5rem",
        width: "500px",
        maxHeight: "90vh",
        overflow: "auto",
        boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.5)",
      }}>
        {/* Header */}
        <div style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "1rem",
          borderBottom: "1px solid #334155",
          paddingBottom: "0.75rem",
        }}>
          <div>
            <h2 style={{ margin: 0, fontSize: "1.1rem", color: "#e2e8f0" }}>
              🔬 生成数据变体
            </h2>
            <p style={{ margin: "0.25rem 0 0", fontSize: "0.75rem", color: "#64748b" }}>
              基于: {sourceDataset.name}
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "1px solid #334155",
              borderRadius: "0.25rem",
              color: "#94a3b8",
              cursor: "pointer",
              padding: "0.25rem 0.5rem",
              fontSize: "0.875rem",
            }}
          >
            ✕
          </button>
        </div>

        {/* Error message */}
        {error && (
          <div style={{
            background: "#451a1a",
            border: "1px solid #ef4444",
            borderRadius: "0.25rem",
            padding: "0.5rem",
            marginBottom: "1rem",
            fontSize: "0.8rem",
            color: "#f87171",
          }}>
            ❌ {error}
          </div>
        )}

        {/* Variant Type Selection */}
        <div style={{ marginBottom: "1rem" }}>
          <label style={{ display: "block", fontSize: "0.75rem", color: "#94a3b8", marginBottom: "0.35rem" }}>
            变体类型
          </label>
          <select
            value={selectedType}
            onChange={(e) => handleTypeChange(e.target.value)}
            style={{
              width: "100%",
              padding: "0.5rem",
              background: "#0f172a",
              border: "1px solid #334155",
              borderRadius: "0.25rem",
              color: "#e2e8f0",
              fontSize: "0.875rem",
            }}
          >
            {variantTypes.map(vt => (
              <option key={vt.id} value={vt.id}>
                {vt.name} ({vt.category})
              </option>
            ))}
          </select>
          {selectedVariant && (
            <p style={{ margin: "0.35rem 0 0", fontSize: "0.7rem", color: "#64748b" }}>
              {selectedVariant.description}
            </p>
          )}
        </div>

        {/* Parameters */}
        {selectedVariant && selectedVariant.params.length > 0 && (
          <div style={{ marginBottom: "1rem" }}>
            <label style={{ display: "block", fontSize: "0.75rem", color: "#94a3b8", marginBottom: "0.5rem" }}>
              参数设置
            </label>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
              {selectedVariant.params.map(param => (
                <div key={param.name} style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem" }}>
                    <span style={{ color: "#94a3b8" }}>{param.name}</span>
                    <span style={{ color: "#64748b" }}>
                      {typeof params[param.name] === 'number'
                        ? Number(params[param.name]).toFixed(param.type === "float" ? 2 : 0)
                        : String(params[param.name] || '')}
                    </span>
                  </div>

                  {param.type === "float" || param.type === "int" ? (
                    <input
                      type="range"
                      min={param.min ?? 0}
                      max={param.max ?? 100}
                      step={param.type === "float" ? 0.01 : 1}
                      value={Number(params[param.name] || (param.min ?? 0))}
                      onChange={(e) => handleParamChange(param.name, param.type === "int" ? parseInt(e.target.value) : parseFloat(e.target.value))}
                      style={{ width: "100%", accentColor: "#38bdf8" }}
                    />
                  ) : param.type === "select" && param.options ? (
                    <select
                      value={String(params[param.name] || param.default || param.options[0])}
                      onChange={(e) => handleParamChange(param.name, e.target.value)}
                      style={{
                        padding: "0.35rem",
                        background: "#0f172a",
                        border: "1px solid #334155",
                        borderRadius: "0.2rem",
                        color: "#e2e8f0",
                        fontSize: "0.8rem",
                      }}
                    >
                      {param.options.map(opt => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type={param.type === "int" ? "number" : "text"}
                      value={String(params[param.name] || "")}
                      onChange={(e) => handleParamChange(
                        param.name,
                        param.type === "int" ? parseInt(e.target.value) || 0 : e.target.value
                      )}
                      style={{
                        padding: "0.35rem",
                        background: "#0f172a",
                        border: "1px solid #334155",
                        borderRadius: "0.2rem",
                        color: "#e2e8f0",
                        fontSize: "0.8rem",
                      }}
                    />
                  )}

                  {param.description && (
                    <span style={{ fontSize: "0.65rem", color: "#475569" }}>{param.description}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Generate Button */}
        <button
          onClick={handleGenerate}
          disabled={generating || !selectedType}
          style={{
            width: "100%",
            padding: "0.6rem",
            background: generating ? "#334155" : "#8b5cf6",
            border: "none",
            borderRadius: "0.35rem",
            color: "#fff",
            fontSize: "0.875rem",
            fontWeight: 600,
            cursor: generating ? "not-allowed" : "pointer",
            marginBottom: "1rem",
          }}
        >
          {generating ? "⏳ 生成中..." : "🔬 生成变体"}
        </button>

        {/* Result */}
        {result && (
          <div style={{
            background: "#0f172a",
            borderRadius: "0.35rem",
            padding: "1rem",
            marginBottom: "1rem",
          }}>
            <h3 style={{ margin: "0 0 0.5rem", fontSize: "0.875rem", color: "#22c55e" }}>
              ✅ 变体已生成
            </h3>
            <div style={{ fontSize: "0.75rem", color: "#94a3b8", marginBottom: "0.75rem" }}>
              <div>ID: {result.variant_id}</div>
              <div>Qubit: {result.qubit} | 类型: {result.experiment_type}</div>
              <div>参数: {JSON.stringify(result.params)}</div>
            </div>

            {/* Preview */}
            {result.preview && Object.keys(result.preview).length > 0 && (
              <div style={{
                background: "#1e293b",
                borderRadius: "0.25rem",
                padding: "0.5rem",
                marginBottom: "0.75rem",
                fontSize: "0.7rem",
                fontFamily: "monospace",
              }}>
                <div style={{ color: "#64748b", marginBottom: "0.25rem" }}>Preview:</div>
                <pre style={{ margin: 0, color: "#94a3b8", overflow: "auto" }}>
                  {JSON.stringify(result.preview, null, 2)}
                </pre>
              </div>
            )}

            {/* Plot Button */}
            <button
              onClick={handlePlot}
              disabled={plotting}
              style={{
                width: "100%",
                padding: "0.5rem",
                background: plotting ? "#334155" : "#3b82f6",
                border: "none",
                borderRadius: "0.25rem",
                color: "#fff",
                fontSize: "0.8rem",
                cursor: plotting ? "not-allowed" : "pointer",
              }}
            >
              {plotting ? "⏳ 绘图..." : "📊 绘图预览"}
            </button>

            {/* Plot Image */}
            {plotImage && (
              <div style={{ marginTop: "0.75rem" }}>
                <img
                  src={plotImage}
                  alt="Variant plot"
                  style={{
                    width: "100%",
                    borderRadius: "0.25rem",
                    border: "1px solid #334155",
                  }}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
