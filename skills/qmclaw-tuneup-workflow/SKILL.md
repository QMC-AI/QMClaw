---
name: qmclaw-tuneup-workflow
description: >-
  QMClaw 单比特调校工作流完整说明。
  15 步标准校准流程、状态机转换、Panel 批量校准、Markov 预测。
  支持自动实验序列生成、并行执行、结果聚合。
  触发词：调校、工作流、workflow、tune-up、状态机、Panel、Markov。
version: 1.0.0
author: QMClaw Contributors
---

# QMClaw Single-Qubit Tune-up Workflow — Router

## Routing Protocol

### 1. Detect workflow type

- `single-qubit` — Single qubit 15-step calibration
- `panel-batch` — Multiple qubits in parallel
- `auto-sequence` — Markov-predicted sequence

### 3. Execute workflow

Load relevant reference and execute.

## 15-Step Calibration State Machine

```
initialized → cavity_found → spec_done → iq_calibrated → 
rabi_measured → pi_calibrated → freq_tuned → pulse_shape_done → 
t1_measured → xeb_verified → complete
```

| State | Step | Condition to Enter |
|-------|------|-------------------|
| initialized | - | Start |
| cavity_found | 1-3 | S21 resonance confirmed |
| spec_done | 4 | f10 identified |
| iq_calibrated | 5 | Visibility > 0.3 |
| rabi_measured | 6 | Pi amplitude found |
| pi_calibrated | 7-8 | Pi pulse optimized |
| freq_tuned | 7-8 | Ramsey detuning < 0.1 MHz |
| pulse_shape_done | 13 | Shape corrected |
| t1_measured | 15 | T1 > 20μs |
| xeb_verified | - | XEB fidelity > 0.98 |
| complete | All | All metrics met |

## Panel Batch Calibration

- Group qubits into panels of 10-20
- Parallel execution within panel
- Sequential execution between panels
- Result aggregation and reporting

## Markov Prediction

- Predict next experiment from history
- 71.6% prediction accuracy
- Reduces unnecessary experiments