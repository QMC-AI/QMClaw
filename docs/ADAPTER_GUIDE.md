# 测控系统适配指南

本指南帮助你将自己的超导量子测控系统接入 QMClaw 框架。

---

## 核心原理：只需改 1 个函数

QMClaw 的实验调用链如下：

```
mcp_tools_new.py  (@mcp.tool)          ← 接收参数，单位换算
    ↓
new_ctrl/tools/s21.py                   ← 调 call_interface
    ↓
new_ctrl/task.py → call_interface()     ← 生成 tid，存结果
    ↓
new_ctrl/task.py → _execute_workflow()  ← 【你要改的唯一函数】
    ↓
你的测控系统 (labrad / artiq / quark / 自研)
```

**你只需要修改 `new_ctrl/task.py` 中的 `_execute_workflow()` 函数**，其他部分无需改动。

---

## 第 1 步：理解 `_execute_workflow` 的输入输出

```python
def _execute_workflow(workflow, tid, **kwargs):
    """
    Args:
        workflow: 实验模板对象，如 S21_template, rabi_template
            - workflow.name: 实验名（如 "S21", "Rabi"）
            - workflow.signal: 信号类型（如 "iq_avg", "population"）
        tid: 实验记录 ID（字符串），用于标识本次实验
        **kwargs: 实验参数，如 qubits, frequency_start, delay 等
    
    Returns:
        dict，包含两个键：
        {
            "data": { ... },   # 必选，实验原始数据
            "meta": { ... },   # 可选，扫描轴/shots 等元信息
        }
    """
```

### `data` 字段（必选）

实验返回的原始数据，键名与实验类型对应：

| 实验类型 (workflow.name) | data 键名 | 数据类型 | 说明 |
|--------------------------|-----------|----------|------|
| `S21` | `s21` | `list[complex]` | S21 传输参数（复数） |
| `Rabi` | `population` | `list[float]` | 量子态占据率随驱动幅度变化 |
| `T1` | `population` | `list[float]` | 占据率随延迟时间衰减 |
| `Ramsey` | `population` | `list[float]` | 占据率随延迟时间振荡衰减 |
| `SingleShot` | `iq` | `list[list[complex]]` | 单发 IQ 散点数据 |
| `Spectrum` | `population` | `list[float]` | 占据率随频率变化 |
| `Spectrum2D` | `population` | `list[list[float]]` | 占据率 vs 频率×偏置 |
| `PowerShift` | `iq_avg` | `list[list[complex]]` | IQ 平均值 vs 功率×频率 |
| `S21vsFlux` | `iq_avg` | `list[list[complex]]` | IQ 平均值 vs 偏置×频率 |
| `DRAG` | `population` | `list[float]` | 占据率 vs DRAG 系数 |
| `OptPiPulse` | `population` | `list[float]` | 占据率 vs 脉冲幅度 |
| `Delta` | `population` | `list[float]` | 占据率 vs 频率偏移 |
| `RB` | `population` | `list[float]` | 保真度 vs 序列长度 |

### `meta` 字段（可选）

实验的元信息，供 `get_data()` 取回后使用：

```python
meta = {
    "freq": {"def": [...], "param": "$gate.Measure.Q0.params.frequency"},
    "delay": {"def": [...], "param": "$gate.R.Q0.params.delay"},
    "qubits": ["Q0", "Q1"],
    "shots": 1024,
    "signal": "population",
    "axis": {...},          # 扫描轴定义
    "other": {...},         # 其他实验参数
}
```

---

## 第 2 步：按实验名分发到你的测控系统

在 `_execute_workflow()` 中，使用 `workflow.name` 区分实验，调用你的测控系统函数：

```python
def _execute_workflow(workflow, tid, **kwargs):
    wf_name = getattr(workflow, 'name', str(workflow))
    
    # ── 适配你的测控系统 ──────────────────────────────
    # 以下以 labrad/sq 框架为例，替换成你自己的测控系统调用
    
    if wf_name == 'S21':
        # 你的测控系统的 S21 实现函数
        return your_system.run_s21(
            qubits=kwargs['qubits'],
            frequency_start=kwargs['frequency_start'],
            frequency_end=kwargs['frequency_end'],
            frequency_sample_num=kwargs['frequency_sample_num'],
        )
    
    elif wf_name == 'Rabi':
        return your_system.run_rabi(
            qubits=kwargs['qubits'],
            drive_amp=kwargs['drive_amp'],
            width=kwargs['width'],
        )
    
    elif wf_name == 'T1':
        return your_system.run_t1(
            qubits=kwargs['qubits'],
            delay=kwargs['delay'],
        )
    
    # ... 其他实验
    
    else:
        raise ValueError(f"Unknown experiment: {wf_name}")
```

---

## 第 3 步：实验名对照表

| `workflow.name` | 实验类型 | `kwargs` 关键参数 |
|------------------|----------|-------------------|
| `S21` | 腔频/比特频率 | `qubits`, `frequency_start`, `frequency_end`, `frequency_sample_num` |
| `Rabi` | Rabi 振荡 | `qubits`, `drive_amp`, `width` |
| `Ramsey` | Ramsey 干涉 | `qubits`, `delta`, `delay`, `stage`, `scale` |
| `T1` | T1 弛豫 | `qubits`, `delay` |
| `Spectrum` | 一维能谱 | `qubits`, `freq`, `drive_amp`, `duration` |
| `Spectrum2D` | 二维能谱 | `qubits`, `freq`, `bias`, `drive_amp`, `duration` |
| `S21vsFlux` | S21 vs Flux | `qubits_scan`, `qubits_read`, `freq`, `read_bias` |
| `SingleShot` | 单发读取 | `qubits`, `stage` |
| `DRAG` | DRAG 优化 | `qubits`, `lamb`, `stage`, `N_repeat`, `pulsePair` |
| `OptPiPulse` | 最优 π 脉冲 | `qubits`, `N_list`, `amp_list`, `delay` |
| `PowerShift` | 功率偏移 | `qubits`, `power`, `freq` |
| `Delta` | Delta 实验 | `qubits`, `N_list`, `delta_list`, `stage`, `delay` |
| `RB` | 随机基准 | `qubits`, `couplers`, `gate`, `cycle`, `size` |

---

## 第 4 步：不同测控框架的适配示例

### 示例 A：labrad + sq 框架

```python
import labrad
from lqms.pyle.workflow import switchSession
import sq

cxn = labrad.connect(host='localhost', port=7682)
s = switchSession(cxn, user='your_user')
qobj = s.your_qubit  # e.g., 'q11ld4'

def _execute_workflow(workflow, tid, **kwargs):
    wf_name = getattr(workflow, 'name', str(workflow))
    
    if wf_name == 'S21':
        sq.s21(qobj, freq=kwargs['frequency_start'])
        return {"data": {"s21": [...]}, "meta": {...}}
    
    elif wf_name == 'Rabi':
        sq.piamp(qobj, amp=kwargs['drive_amp'])
        return {"data": {"population": [...]}, "meta": {...}}
    
    # ... 其他实验
```

### 示例 B：artiq 框架

```python
from artiq.experiment import *
from artiq.coredevice import Core

def _execute_workflow(workflow, tid, **kwargs):
    wf_name = getattr(workflow, 'name', str(workflow))
    
    if wf_name == 'S21':
        # 调用 artiq 实验函数
        return artiq_s21(
            qubits=kwargs['qubits'],
            freq=kwargs['frequency_start'],
        )
    
    # ... 其他实验
```

### 示例 C：自研框架（直接 Python 函数）

```python
from my_lab.hardware import run_s21, run_rabi, run_t1

def _execute_workflow(workflow, tid, **kwargs):
    wf_name = getattr(workflow, 'name', str(workflow))
    
    if wf_name == 'S21':
        result = run_s21(
            qubits=kwargs['qubits'],
            freq=kwargs['frequency_start'],
        )
        return {
            "data": {"s21": result['s21']},
            "meta": {"freq": {"def": result['freq']}},
        }
    
    # ... 其他实验
```

---

## 第 5 步：验证你的适配

适配完成后，运行冒烟测试验证：

```bash
python tests/test_smoke.py
```

如果所有 7 个测试通过，说明你的适配正确。

---

## 常见问题

### Q: 我的测控系统函数名和 QMClaw 不一样怎么办？

A: 没关系。`_execute_workflow()` 是适配层，你在里面做映射：
```python
if wf_name == 'S21':
    return my_system.scan_cavity(...)  # 你的函数名可以不同
```

### Q: 我的测控系统返回的数据格式不一样怎么办？

A: 在 `_execute_workflow()` 里转换成 QMClaw 期望的格式（见第 1 步的 `data` 字段表）。

### Q: 我没有实际硬件，能测试吗？

A: 可以。`_execute_workflow()` 已经有 Mock 实现，返回模拟数据。直接运行 `python tests/test_smoke.py` 即可。
