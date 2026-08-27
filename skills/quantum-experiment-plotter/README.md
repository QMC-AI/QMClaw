# Quantum Experiment Plotter

**What it does** — Visualization toolkit for quantum experiment data including 1D line plots (S21, Rabi, T1), 2D heatmaps (ZPA Scan, Power Scan), with support for Nature/IEEE/APS/Springer academic styles and SVG+PDF export.

**Version**: v1.0.0  
**Status**: Stable  
**Trigger Keywords**: 绘图, 可视化, plot, figure, 图表, scientific figure

---

## Overview

| Capability | Description |
|------------|-------------|
| 1D Line Plots | S21, Rabi oscillation, T1 decay, Ramsey |
| 2D Heatmaps | ZPA vs Frequency, Power Scan |
| Scatter Plots | IQ blob separation |
| Multi-panel | Scientific figure composition |
| Academic Styles | Nature, IEEE, APS, Springer |

---

## Quick Start

```python
import matplotlib
matplotlib.use('Agg')  # Headless mode
import matplotlib.pyplot as plt
import sciplot as sp

# Set academic style
sp.style('nature')  # or 'ieee', 'aps', 'springer'

# Create figure
fig, ax = sp.new_figure(figsize=(7, 5))
ax.plot(freq, s21, linewidth=1.5)

# Labels
ax.set_xlabel('Frequency (GHz)', fontsize=11)
ax.set_ylabel('S21 (dB)', fontsize=11)

# Panel label
sp.add_panel_labels([ax], labels=['a'])

# Export
sp.save(fig, 's21_result', formats=('svg', 'pdf'))
plt.close('all')
```

---

## Chart Types

### 1D Line Plot (S21, Rabi, T1)

```python
fig, ax = sp.new_figure(figsize=(7, 5))
ax.plot(x_data, y_data, 'b-', linewidth=1.5)
ax.set_xlabel('X Label', fontsize=11)
ax.set_ylabel('Y Label', fontsize=11)
sp.save(fig, 'output_name', formats=('svg', 'pdf'))
```

### 2D Heatmap (ZPA Scan)

```python
d = data.get_data()  # Required in headless mode
fig, ax = sp.new_figure(figsize=(10, 6))
mesh = ax.pcolormesh(d[0], d[1], d[2].T, shading='auto', cmap='RdBu_r')
plt.colorbar(mesh, label='S21 (dB)', ax=ax)
ax.set_xlabel('ZPA', fontsize=11)
ax.set_ylabel('Frequency (GHz)', fontsize=11)
sp.save(fig, 'zpa_heatmap', formats=('svg', 'pdf'))
```

### IQ Scatter Plot

```python
fig, ax = sp.new_figure(figsize=(6, 6))
ax.scatter(iq_0[:, 0], iq_0[:, 1], alpha=0.5, label='|0⟩')
ax.scatter(iq_1[:, 0], iq_1[:, 1], alpha=0.5, label='|1⟩')
ax.legend()
ax.set_xlabel('I', fontsize=11)
ax.set_ylabel('Q', fontsize=11)
sp.save(fig, 'iq_blobs', formats=('svg',))
```

---

## Journal Styles

```python
sp.style('nature')    # Nature, Science: 7.0 × 5.0"
sp.style('ieee')      # IEEE: 3.5 × 3.0"
sp.style('aps')       # APS: 3.4 × 2.8"
sp.style('springer')  # Springer: 3.3 × 2.5"
```

### Style Differences

| Aspect | nature | ieee | aps |
|--------|--------|------|-----|
| Font | Arial | Times | Arial |
| Size | 7×5" | 3.5×3" | 3.4×2.8" |
| Label | 11pt | 9pt | 10pt |

---

## Academic Figure Rules

### Mandatory rcParams

```python
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans', 'Liberation Sans']
plt.rcParams['svg.fonttype'] = 'none'  # Text stays as <text> nodes
```

### Export Requirements

| Format | Use | DPI/Quality |
|--------|-----|-------------|
| SVG | Primary, editable | Vector |
| PDF | Publication | Vector |
| PNG | Preview | 1200 DPI |

---

## Headless Mode Data Collection

```python
# ❌ WRONG - Returns blank image
data.plotDataset()

# ✅ CORRECT - Get raw data
d = data.get_data()
# d[0] = X axis, d[1] = Y axis, d[2] = Data matrix

ax.pcolormesh(d[0], d[1], d[2].T, shading='auto')
```

---

## Multi-Panel Figures

```python
fig, axes = plt.subplots(2, 2, figsize=(10, 8))
sp.add_panel_labels(axes.flat, labels=['a', 'b', 'c', 'd'])
sp.save(fig, 'multi_panel', formats=('svg', 'pdf'))
```