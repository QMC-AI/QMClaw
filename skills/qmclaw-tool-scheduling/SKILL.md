---
name: qmclaw-tool-scheduling
description: >-
  QMClaw 工具调度与后端接口层。
  工具白名单机制、MCP 服务器集成、集中式调度分布式执行、后端适配器。
  支持仪器设备、量子模拟器、硬件模拟器多种后端类型。
  触发词：工具调度、后端接口、MCP、Center Agent、Backend Adapter。
version: 1.0.0
author: QMClaw Contributors
---

# QMClaw Tool Scheduling — Router

## Routing Protocol

### 1. Detect operation type

- `single-tool` — Execute single tool
- `tool-sequence` — Execute tool sequence
- `batch-execution` — Parallel batch execution
- `backend-switch` — Switch between backends

### 3. Execute with appropriate backend

Apply tool whitelist validation and MCP protocol.

## Tool Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Center Agent                             │
│  ├── Intent parsing                                         │
│  ├── Task decomposition                                     │
│  └── Result aggregation                                     │
├─────────────────────────────────────────────────────────────┤
│  Tool Registry (Whitelist)                                  │
│  ├── sq.s21, sq.spectroscopy, sq.iqraw                     │
│  ├── sq.piamp, sq.ramsey_df, sq.t1                         │
│  └── qter.fitData, dp.T1, px.XEB                           │
├─────────────────────────────────────────────────────────────┤
│  Backend Adapters                                           │
│  ├── InstrumentBackend (Real hardware)                     │
│  ├── SimulatorBackend (Qiskit, QuEra)                      │
│  └── EmulatorBackend (QEMU)                                │
└─────────────────────────────────────────────────────────────┘
```

## Tool Categories

| Category | Tools | Permission |
|----------|-------|------------|
| Measurement | `sq.s21`, `sq.spectroscopy`, `sq.iqraw` | measurement |
| Calibration | `sq.piamp`, `sq.pidf`, `sq.set_pi` | calibration |
| Analysis | `qter.fitData`, `dp.T1`, `px.XEB` | analysis |
| Control | `switchSession`, `update_registry` | control |

## Backend Types

| Type | Use Case | Examples |
|------|----------|----------|
| instrument | Real hardware | VNA, RF source, digitizer |
| simulator | Algorithm testing | Qiskit Aer, QuEraBloqade |
| emulator | Hardware emulation | QEMU-based emulation |