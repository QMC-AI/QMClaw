---
name: qubit-calibration
description: >
  量子比特单比特标定流程指导。执行单比特校准实验序列：S21找腔 -> ZPA定位sweet spot -> Spectroscopy找f10 -> IQraw -> PiPulse -> PiAmpFine -> AlphaFine -> Ramsey -> T1/T2 -> XEB。
  Triggers: '单比特标定', 'qubit calibration', '比特校准流程', '量子比特初始化', '单比特实验步骤', 'calibrate qubit'
---

# 量子比特单比特标定流程

量子比特单比特标定是从"未知比特"到"可用比特"的标准实验序列。每一步都有明确目标和判定标准。

## 核心原则

1. **顺序执行**：严格按照流程顺序，后面的步骤依赖前面步骤的结果
2. **质量门限**：每步都有质量判定，不合格则返回上一步优化
3. **参数更新**：大部分实验支持 update=True 自动更新 qubit 参数

---

## 实验流程总览

| 步骤 | 实验 | 目标参数 | 质量标准 |
|------|------|---------|---------|
| 1 | S21 | fread（读取腔频率） | 找到腔 |
| 2 | S21_power2d | 读取功率范围 | 确定色散区间 |
| 3 | S21_zpa2d | bias_z（Z偏置） | 找到sweet spot |
| 4 | Spectroscopy | f10（比特频率） | 找到01跃迁 |
| 5 | IQraw | visibility, SNR | IQ分隔清晰 |
| 6 | PiAmp/PiPulse | pi_amp | Rabi振荡 |
| 7 | PiAmpFine | 精细pi振幅 | 精确控制 |
| 8 | AlphaFine | DRAG alpha | 误差抑制 |
| 9 | S21_dis | 微调fread | 色散位移 |
| 10 | Ramsey_df | 精细f10 | 频率精确 |
| 11 | T1 | T1时间 | > 50μs 合格 |
| 12 | T2/Ramsey | T2*相干时间 | > 20μs 合格 |
| 13 | XEB | 门保真度 | > 95% 合格 |

---

## 详细步骤说明

### 步骤1：S21 传输测量

**目的**：获取读取腔共振频率

**调用**：
```python
sq.s21(qobj, freq=np.arange(6.5, 7.1, 0.001), update=False)
```

**数据处理**：
```python
dp.findPhaseDiff(data, dataset, qubitNum=12, medfilt_para=15)
```

**判定**：观察到幅频特性曲线中的谷（谐振腔）

---

### 步骤2：读取功率扫描

**目的**：确定色散区间，选取合适读取功率

**调用**：
```python
sq.s21_power2d(qobj)
```

---

### 步骤3：ZPA 2D扫描（关键！）

**目的**：找到sweet spot（最优工作点）

**调用**：
```python
sq.s21_zpa2d(qobj)
```

**结果解读**：
- **凸点（Convex）**：ZPA ≈ **3**（右侧）= Sweet Point = 比特最高频率 = 色散位移最大
- **凹点（Concave）**：ZPA ≈ **-1.5**（左侧）= 比特最低频率点

---

### 步骤4：能谱扫描 Spectroscopy

**目的**：获取比特 0-1 跃迁频率 f10

**调用**：
```python
sq.spectroscopy(qobj, freq=np.arange(4.0, 3.0, -0.002))
```

**后续**：
```python
qobj.set_f10(3.5, non=-0.2, sb_freq=-0.15)
```

---

### 步骤5：IQ原始数据采集

**目的**：确认IQ分隔，获取读取可见度

**调用**：
```python
sq.iqraw(qobj)
```

**数据处理**：
```python
result = qter.fitData(-1, collect=True, do_plot=False)
visibility = result[1][-2]  # 可见度
snr = result[1][2]  # 信噪比
```

**质量标准**：
| SNR | visibility | 质量 |
|-----|-----------|------|
| > 3 | > 0.8 | Excellent |
| 2-3 | 0.5-0.8 | Good |
| 1-2 | 0.2-0.5 | Fair |
| <1 | < 0.2 | Poor |

---

### 步骤6：Rabi振荡 / PiAmp

**目的**：确定最优pi脉冲振幅

**调用**：
```python
sq.piamp(qobj, amp=2)  # amp是扫描范围
```

---

### 步骤7-8：精细校准 PiAmpFine / AlphaFine

**目的**：微调pi振幅和DRAG alpha参数

**调用**：
```python
sq.set_pi(qobjs, gate='X')   # Pi脉冲
sq.set_pi(qobjs, gate='X/2') # Pi/2脉冲
```

---

### 步骤9：色散位移 S21_dis

**目的**：微调读取频率fread

**调用**：
```python
sq.s21_dis(qobj)
```

---

### 步骤10：Ramsey精细调频

**目的**：精细调节f10

**调用**：
```python
sq.ramsey_df(qobj)
```

**判定**：Ramsey震荡周期≈200ns（5MHz条纹频率）

---

### 步骤11：T1测量

**目的**：获取能量弛豫时间

**调用**：
```python
sq.t1(qobj, zpa=np.arange(-1.0, 0.4, 0.02))
```

**数据处理**：
```python
result = qter.fitData(-1, collect=True, do_plot=False)
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

### 步骤12：T2相干时间测量

**目的**：获取T2*（Ramsey）和T2（Spin Echo）相干时间

**调用**：
```python
sq.ramsey_t2(qobj)      # T2*
sq.spinecho_t2(qobj, m=1)  # T2 (Spin Echo)
```

---

### 步骤13：XEB门保真度

**目的**：评估量子门质量

**调用**：
```python
sq.xeb(qobj)
```

**数据处理**：
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

## Python环境

```python
# 激活测控Python环境
python = r"C:\Users\lqcs\Programs\Python\Python311\python.exe"

# 常用模块导入
from lqms.measure.tuners import sq_nodes as sq
from lqms.data_process import dataAnalysisCore as qter
from lqms.data_process import pyle_analysis as px
from lqms.data_process import data as data
```

---

## MCP服务（可选）

已实现的 MCP 服务：quantum-calibration-mcp

| 工具名 | 说明 |
|--------|------|
| calibration_s21 | S21扫描 |
| calibration_spectroscopy | 能谱扫描 |
| calibration_iqraw | IQ分隔 |
| calibration_piamp | Pi振幅扫描 |
| calibration_ramsey | Ramsey调频 |
| calibration_t1 | T1测量 |
| calibration_full | 完整校准流程 |

---

## 常见问题

**Q: S21找不到腔？**
A: 检查接线、频率范围是否正确

**Q: IQ分隔不好？**
A: 优化pi门参数、f10、读取频率

**Q: T1很短？**
A: 检查ZPA是否在sweet spot，检查耦合

**Q: XEB保真度低？**
A: 先确保T1/T2足够，检查门参数是否准确
