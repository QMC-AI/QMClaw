# QMClaw 介绍及架构

## 项目简介

QMClaw（Quantum Measurement Claw）是一个可扩展的通用量子比特测控技能框架，以 `SKILL.md` 为中心的可复用指令集。

项目旨在降低超导量子比特调试的门槛，将传统测量控制、数据分析和智能决策封装为标准化技能，供 AI 助手和实验人员调用。

---

## 架构总览

QMClaw 采用**六层执行栈**和**三层决策架构**：

```
┌──────────────────────────────────────┐
│           Skill 层 (SKILL.md)        │  ← 对外接口：自然语言触发
├──────────────────────────────────────┤
│       MCP 工具层 (@mcp.tool)         │  ← 13 个标准实验工具
├──────────────────────────────────────┤
│      new_ctrl (call_interface)       │  ← 底层测控接口
├──────────────────────────────────────┤
│     new_templates (实验模板)         │  ← 实验元信息定义
├──────────────────────────────────────┤
│       数据分析 & 拟合层              │  ← T1/Rabi/XEB 等
├──────────────────────────────────────┤
│       可视化 & 报告层                │  ← matplotlib/plotly
└──────────────────────────────────────┘
```

### 三层决策架构

| 层 | 名称 | 说明 |
|----|------|------|
| L1 | RuleEngine | 基于规则的确定性决策（快速、可复现） |
| L2 | Learned Rules | 学习型规则，从历史数据中提取模式 |
| L3 | LLM Fallback | 大模型兜底，处理未知/复杂场景 |

---

## 核心模块

### 1. 测控层 (`new_ctrl`)

- `call_interface(workflow, **kwargs)` — 驱动测控系统执行实验，返回 tid
- `get_data(rid)` — 根据实验记录 ID 获取数据
- `query_param(key)` / `update_param(key, value)` — 参数读写

### 2. 实验工具层 (`new_ctrl/tools`)

13 个标准实验：S21, Rabi, Ramsey, T1, Spectrum, Spectrum2D, S21vsFlux, SingleShot, DRAG, OptPiPulse, PowerShift, Delta, RB

### 3. 实验模板层 (`new_templates`)

每个实验对应一个模板对象，包含实验元信息（名称、信号类型、默认参数等）。

### 4. 技能层 (`skills/`)

7 个可安装的 Skill 包：
- `qmclaw-framework-overview` — 框架概述
- `qmclaw-decision-architecture` — 决策架构
- `qmclaw-tuneup-workflow` — 单比特调校工作流
- `qmclaw-data-analysis` — 数据分析与质量评估
- `qmclaw-tool-scheduling` — 工具调度与后端接口
- `quantum-calibration` — 量子比特校准标定
- `quantum-experiment-plotter` — 量子实验绘图与分析

---

## 数据流

```
用户/AI 发出实验请求
       ↓
mcp_tools_new.py (@mcp.tool 接收参数，单位换算)
       ↓
new_ctrl/tools/s21.py (调 call_interface)
       ↓
new_ctrl/task.py (_execute_workflow → 驱动硬件)
       ↓
返回 tid
       ↓
get_data(tid) → 数据分析/拟合 → 可视化/报告
       ↓
LLM 评估结果 → 决策下一步 → 循环
```

---

## 设计原则

1. **技能即文档**：每个 Skill 以 `SKILL.md` 为中心，自描述、可安装
2. **接口统一**：所有实验遵循 `@mcp.tool` → `call_interface` → `_execute_workflow` 的调用链
3. **松耦合**：测控层（`new_ctrl`）和技能层（`skills/`）相互独立，可单独更新
4. **Mock 友好**：`_execute_workflow()` 提供 Mock 实现，无需实际硬件即可 demo 和测试
