# QMClaw Skills 用户手册

## 目录

1. [安装](#1-安装)
2. [Skill 列表](#2-skill-列表)
3. [使用方式](#3-使用方式)
4. [实验接口](#4-实验接口)
5. [数据分析与质量评估](#5-数据分析与质量评估)
6. [配置参考](#6-配置参考)
7. [常见问题](#7-常见问题)

---

## 1. 安装

### 方式一：手动安装（推荐）

```bash
# 克隆项目
git clone https://github.com/YOUR_USERNAME/qmclaw-skills.git
cd qmclaw-skills

# 安装 skills 到 Claude Code
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

### 方式二：插件市场安装（Claude Code）

```bash
claude plugin marketplace add YOUR_GITHUB/qmclaw-skills
claude plugin install qmclaw-skills@qmclaw-skills
```

---

## 2. Skill 列表

| Skill | 版本 | 用途 | 触发关键词 |
|-------|------|------|------------|
| QMClaw框架概述 | v1.0 | 框架架构、六层栈、三层决策 | "QMClaw", "框架介绍" |
| QMClaw三层决策架构 | v1.0 | L1/L2/L3 决策层 | "决策", "RuleEngine" |
| QMClaw单比特调校工作流 | v1.0 | 15步校准流程 | "校准", "calibration" |
| QMClaw工具调度与后端接口 | v1.0 | 工具白名单、MCP | "工具调度", "后端" |
| QMClaw数据分析与质量评估 | v1.0 | T1/Rabi/XEB/质量指标 | "数据分析", "保真度" |
| 量子比特单比特校准标定 | v1.0 | 完整校准标定流程 | "标定", "单比特" |
| 量子实验绘图与分析 | v1.0 | 绘图、拟合、可视化 | "绘图", "可视化" |

---

## 3. 使用方式

### 3.1 在 Claude Code 中使用

安装完成后，用自然语言触发：

```
执行单比特校准工作流。
如何进行 T1 拟合和 XEB 保真度计算？
QMClaw 的决策架构是什么？
```

### 3.2 在 Python 中使用

```python
import sys
sys.path.insert(0, "path/to/qmclaw-skills")

from new_ctrl.task import call_interface, get_data
from new_templates import S21_template

# 执行 S21 实验
tid = call_interface(
    workflow=S21_template,
    qubits=["Q0"],
    frequency_start=-40e6,
    frequency_end=40e6,
    frequency_sample_num=101,
)

# 获取实验数据
data = get_data(tid)
print(data["data"]["s21"])
```

---

## 4. 实验接口

### 4.1 13 个标准实验

| 实验 | 工具函数 | 主要参数 |
|------|----------|----------|
| S21 | `s21` | `qubits`, `frequency_start`, `frequency_end` |
| Rabi | `rabi` | `qubits`, `drive_amp`, `width` |
| Ramsey | `ramsey` | `qubits`, `delta`, `delay` |
| T1 | `t1` | `qubits`, `delay` |
| Spectrum | `spectrum` | `qubits`, `freq`, `drive_amp` |
| Spectrum2D | `spectrum_2d` | `qubits`, `freq`, `bias` |
| S21vsFlux | `s21vsflux` | `qubits_scan`, `freq`, `read_bias` |
| SingleShot | `singleshot` | `qubits` |
| DRAG | `drag` | `qubits`, `lamb`, `pulsePair` |
| OptPiPulse | `opt_pipulse` | `qubits`, `N_list`, `amp_list` |
| PowerShift | `powershift` | `qubits`, `power`, `freq` |
| Delta | `delta` | `qubits`, `N_list`, `delta_list` |
| RB | `rb` | `qubits`, `gate`, `cycle` |

### 4.2 通用接口

```python
from new_ctrl.task import call_interface, get_data, query_param, update_param

# 执行实验
tid = call_interface(workflow=..., qubits=["Q0"])

# 获取历史数据
data = get_data(tid)

# 读写参数
update_param("Q0.frequency", 6.5e9)
value = query_param("Q0.frequency")
```

---

## 5. 数据分析与质量评估

### 5.1 质量指标

| 指标 | 说明 |
|------|------|
| SNR | 信噪比 |
| Visibility | 条纹可见度 |
| T1 | 能量弛豫时间 |
| Fidelity | 门保真度 |

### 5.2 分析示例

```python
# T1 指数衰减拟合
from scipy.optimize import curve_fit

def exp_decay(t, A, T1, C):
    return A * np.exp(-t / T1) + C

popt, pcov = curve_fit(exp_decay, delay, population)
T1_fit = popt[1]
```

---

## 6. 配置参考

### 6.1 MCP 配置 (`.mcp.json`)

```json
{
    "mcpServers": {
        "quantum-service": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-http", "http://127.0.0.1:8008/mcp"]
        }
    }
}
```

### 6.2 Mock 模式

未对接实际硬件时，`_execute_workflow()` 会返回模拟数据，可用于：
- 测试和验证流程
- 给新用户 demo
- CI/CD 自动化测试

---

## 7. 常见问题

### Q: 安装 skill 后不生效？
A: 新开一个 Claude Code session，或者手动把 `skills/` 目录复制到 `~/.claude/skills/`。

### Q: 没有 quantum 硬件能用吗？
A: 可以。`_execute_workflow()` 提供了 Mock 实现，返回模拟实验数据。

### Q: 如何对接自己的测控系统？
A: 修改 `new_ctrl/task.py` 中的 `_execute_workflow()`，按 workflow name 分发到你的硬件调用。

---

> 如有问题请提 Issue: https://github.com/YOUR_USERNAME/qmclaw-skills/issues
