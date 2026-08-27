# QMClaw Framework Overview

**What it does** — Provides a complete introduction to the QMClaw scalable quantum measurement and control framework, covering its six-layer execution stack, three-layer decision architecture, multi-agent model, and panel-based grouping strategy.

**Version**: v1.0.0  
**Status**: Stable  
**Trigger Keywords**: QMClaw, 框架介绍, 架构, 六层栈, 三层决策, 整体介绍

---

## Overview

QMClaw (Quantum Measurement and Control) is a scalable general-purpose framework for superconducting transmon qubit calibration and characterization. It combines:

- **Six-layer execution stack** for modular architecture
- **Three-layer decision architecture** for intelligent automation
- **Multi-agent coordination** for parallel processing
- **Panel-based grouping** for million-qubit scaling

---

## Six-Layer Execution Stack

| Layer | Name | Function | Example |
|-------|------|----------|---------|
| L6 | Natural Language Interface | User commands | WeChat, CLI |
| L5 | Analysis & Report | Data fitting, reporting | T1, XEB |
| L4 | Workflow Orchestration | Experiment execution | 15-step calibration |
| L3 | Tool Scheduling | Tool dispatch | Center Agent |
| L2 | Knowledge & Rules | RAG, rule learning | L3→L2 promotion |
| L1 | Hardware Interface | Instruments/simulators | LabRad |

---

## Three-Layer Decision Architecture

| Layer | Speed | Cost | Use Case |
|-------|-------|------|----------|
| L1 RuleEngine | < 1μs | $0 | Exact match |
| L2 Learned Rules | < 1ms | $0 | Pattern match |
| L3 LLM Fallback | ~300ms | API cost | Complex reasoning |

---

## Multi-Agent Model

```
┌─────────────────────────────────────────────────────────────┐
│                    Center Agent                             │
│  ├── Task decomposition                                     │
│  ├── Route distribution                                     │
│  └── Result aggregation                                     │
├─────────────────────────────────────────────────────────────┤
│  Sub-Agents                                                 │
│  ├── Measurement Agent                                      │
│  ├── Calibration Agent                                      │
│  └── Analysis Agent                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Performance

| Metric | Value |
|--------|-------|
| Qubits calibrated per run | 73 |
| Automated steps | 2476 |
| Markov prediction accuracy | 71.6% |
| LLM speedup | 500,000x |