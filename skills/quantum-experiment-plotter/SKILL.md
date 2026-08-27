---
name: quantum-experiment-plotter
description: >-
  量子实验数据绘图与分析技能包。
  一维曲线（S21、Rabi、T1）、二维热力图（ZPA Scan、Power Scan）。
  支持 Nature/IEEE/APS/Springer 学术图表样式，SVG+PDF 导出。
  触发词：绘图、可视化、plot、figure、图表、scientific figure。
version: 1.0.0
author: QMClaw Contributors
---

# Quantum Experiment Plotter — Router

## Routing Protocol

### 1. Detect plot type

- `1d-curve` — Line plots (S21, Rabi, T1)
- `2d-heatmap` — Heatmaps (ZPA Scan, Power Scan)
- `scatter` — Scatter plots (IQ blobs)
- `multi-panel` — Multi-panel scientific figures

### 3. Execute plotting

Apply academic style and export.

## Supported Chart Types

| Chart Type | Data | Example |
|------------|------|---------|
| Line plot | 1D | S21, Rabi, T1 decay |
| Heatmap | 2D | ZPA vs Frequency |
| Scatter | Points | IQ blob separation |
| Bar chart | Categorical | Fidelity comparison |
| Error bar | Mean ± std | Multiple measurements |

## Journal Styles

| Style | Journal | Size |
|-------|---------|------|
| `nature` | Nature, Science | 7.0 × 5.0" |
| `ieee` | IEEE | 3.5 × 3.0" |
| `aps` | APS | 3.4 × 2.8" |
| `springer` | Springer | 3.3 × 2.5" |

## Academic Figure Rules

### Required rcParams

```python
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans']
plt.rcParams['svg.fonttype'] = 'none'
```

### Export Format

- Primary: SVG (editable, publication-ready)
- Secondary: PDF (vector, high quality)
- Preview: PNG 1200 DPI

## Data Collection in Headless Mode

```python
# ❌ Wrong (blank in headless)
data.plotDataset()

# ✅ Correct
d = data.get_data()
ax.pcolormesh(d[0], d[1], d[2].T, shading='auto')
```