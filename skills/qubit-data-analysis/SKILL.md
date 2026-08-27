---
name: qubit-data-analysis
description: >
  量子比特实验数据分析指导。使用 qter.fitData() 提取参数、可视化绘图、质量判定。支持 S21、IQraw、Ramsey、T1、T2、XEB 等实验的数据处理。
  Triggers: '数据分析', 'data analysis', 'fitData', '实验结果分析', 'qubit data', '测控数据处理'
---

# 量子比特实验数据分析

量子测控实验的数据分析流程：数据获取 -> 参数提取 -> 质量判定 -> 可视化绘图。

---

## 数据获取方式

### 1. 执行实验后加载数据集

```python
# 在同一个 labrad 连接中
data.loadDataset(-1)  # -1 表示最后数据集

# 获取参数
params = data.parameters  # 字典，可访问 create_time, config 等
items = data.items()      # 字典的键值对
```

### 2. 获取原始数据

```python
d = data.get_data()
# d[0] = X轴数据（如 ZPA）
# d[1] = Y轴数据（如 频率）
# d[2] = S21数据矩阵
```

---

## fitData 参数提取

### 通用调用方式

```python
result = qter.fitData(-1, collect=True, do_plot=False)
# 返回格式：[{'variables': [('name', 'unit'), ...]}, array of values, ...]
```

**注意**：fitData(-1) 会改变当前 dataset（绑定 -1 → loadDataset() 后的值）

---

### IQraw 数据分析

```python
result = qter.fitData(-1, collect=True, do_plot=False)

# 返回值结构：
# result[1][0] = freq (频率)
# result[1][1] = separation (分离度)
# result[1][2] = SNR (信噪比)
# result[1][-2] = visibilityMax (可见度)
# result[1][-1] = position (分割位置/threshold)

visibility = result[1][-2]
snr = result[1][2]
```

**质量标准**：
| SNR | visibility | 质量 |
|-----|-----------|------|
| > 3 | > 0.8 | Excellent |
| 2-3 | 0.5-0.8 | Good |
| 1-2 | 0.2-0.5 | Fair |
| <1 | < 0.2 | Poor |

---

### T1 数据分析

```python
result = qter.fitData(-1, collect=True, do_plot=False)

# 返回值结构：
# result[1][0] = zpa (Z偏置)
# result[1][1] = T1 (弛豫时间，μs)
# result[1][2] = freq (频率)

t1 = result[1][1][0]  # T1值（μs）
```

**质量标准**：
| T1 (μs) | 质量 |
|---------|------|
| > 100 | Excellent |
| 50-100 | Good |
| 20-50 | Fair |
| < 20 | Poor |

---

### XEB 数据分析

```python
xeb_res = px.XEB(data, [-1], collect=True)
xeb_fid = 1 - xeb_res[data.dataset_num]['error_Pauli_per_cycle']/1.5
```

**质量标准**：
| Fidelity | 质量 |
|----------|------|
| > 0.99 | Excellent |
| 0.95-0.99 | Good |
| 0.90-0.95 | Fair |
| < 0.90 | Poor |

---

## 绘图（重要！）

### 核心问题

**`data.plotDataset()` 在 headless 模式下不能真正渲染，会生成空白图！**

### 一维数据绘图（S21曲线）

```python
import matplotlib
matplotlib.use('Agg')  # 关键：使用非交互式后端
import matplotlib.pyplot as plt

qobj = q11ld4
sq.s21(qobj, update=False, do_plot=False)
result = qter.fitData(-1, collect=True, do_plot=True)

plt.savefig(r'D:\lqcs\measure_scripts\sq_workflow\result.png', dpi=150)
plt.close('all')
```

### 二维数据绘图（ZPA实验）

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

qobj = q11ld4
sq.s21_zpa2d(qobj)
d = data.get_data()  # d[0]=ZPA, d[1]=频率, d[2]=S21数据

plt.figure(figsize=(10,6))
plt.pcolormesh(d[0], d[1], d[2].T, shading='auto')
plt.colorbar(label='S21')
plt.xlabel('ZPA')
plt.ylabel('Frequency (GHz)')
plt.title(data.dataset_name)
plt.savefig(r'D:\lqcs\measure_scripts\sq_workflow\result.png', dpi=150)
plt.close('all')
```

---

## 科研绘图（sciplot-academic）

### 安装

```bash
pip install sciplot-academic
```

### 使用

```python
import sciplot as sp

# 1. 选择期刊样式
sp.style('nature')  # Nature/Science
sp.style('ieee')   # IEEE
sp.style('aps')    # APS Physical Review

# 2. 创建图表
fig, ax = sp.new_figure(figsize=(7, 5))
ax.plot(x, y, linewidth=1.5, label='Data')
ax.set_xlabel('Frequency (GHz)', fontsize=11)
ax.set_ylabel('S21 (dB)', fontsize=11)

# 3. 添加面板标签
sp.add_panel_labels([ax], labels=['a'])

# 4. 保存
sp.save(fig, r'workspace\output\figure', formats=('svg', 'pdf'), dpi=1200)
```

### 量子测控常用

| 实验 | 推荐函数 |
|------|---------|
| S21 曲线 | `sp.plot()` / `sp.plot_multi()` |
| T1 衰减 | `sp.plot()` + 误差棒 |
| 热力图（ZPA scan） | `sp.plot_heatmap()` |
| 门保真度对比 | `sp.plot_grouped_bar()` |

---

## 实验分析汇总

| 实验名称 | 分析函数 | 分析后绘图 |
|---------|---------|-----------|
| IQraw | qter.fitData | fitFunc.IQ_raw |
| Ramsey df | qter.fitData | fitFunc.ramsey |
| S21zpa2d | qter.fitData | fitFunc.s21_zpa2d |
| S21 | data.plotDataset | 直接绘图 |
| Spectroscopy | data.plotDataset | 直接绘图 |
| PiPulse | data.plotDataset | 直接绘图 |
| PiAmpFine | data.plotDataset | 直接绘图 |
| AlphaFine | data.plotDataset | 直接绘图 |
| T1 | data.plotDataset | 直接绘图 |
| T2 | data.plotDataset | 直接绘图 |
| S21_dis | data.plotDataset | 直接绘图 |
| IQcenter | data.plotDataset | 直接绘图 |
| XEB | data.plotDataset | 直接绘图 |
| TimingXYZ | data.plotDataset | 直接绘图 |

---

## Python环境

```python
# 激活测控Python环境
python = r"C:\Users\lqcs\Programs\Python\Python311\python.exe"

# 常用模块
from lqms.data_process import dataAnalysisCore as qter
from lqms.data_process import pyle_analysis as px
from lqms.data_process import data as data
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
```

---

## 常见问题

**Q: fitData 返回 None？**
A: S21 是 2D 数据，fitData 无法处理。用 data.plotDataset 直接绘图。

**Q: 绘制的图是空白的？**
A: 一定要用 `matplotlib.use('Agg')` 设置非交互式后端。

**Q: 如何提取具体参数值？**
A: 根据实验类型访问 result[1] 的不同索引。详见上方各实验的分析说明。
