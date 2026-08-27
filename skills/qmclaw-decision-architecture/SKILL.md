---
name: qmclaw-decision-architecture
description: >-
  QMClaw 三层决策架构详细说明。
  L1 RuleEngine（精确匹配，<1μs）、L2 Learned Rules（规则推理，<1ms）、L3 LLM Fallback（通用推理，~300ms）。
  包含 RuleEngine API、条件函数、规则管理、性能对比。
  触发词：决策、三层决策、RuleEngine、规则引擎、L1、L2、L3。
version: 1.0.0
author: QMClaw Contributors
---

# QMClaw Three-Layer Decision Architecture — Router

## Routing Protocol

### 1. Load the architecture overview

Read this SKILL.md to understand the three-layer decision design.

### 2. Generate explanation

Provide:
1. Architecture overview and rationale
2. Layer-by-layer breakdown
3. RuleEngine implementation
4. Performance metrics

## Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  L3: LLM Fallback Layer                                     │
│  ├── Use case: New problems, no rule match                 │
│  ├── Speed: ~300ms                                         │
│  ├── Cost: API cost                                        │
│  └── Output: One-time solution                             │
├─────────────────────────────────────────────────────────────┤
│  L2: Learned Rules Layer                                    │
│  ├── Source: L3 success case promotion                     │
│  ├── Speed: <1ms                                           │
│  ├── Cost: $0                                              │
│  └── Output: Reusable rule                                 │
├─────────────────────────────────────────────────────────────┤
│  L1: RuleEngine Layer                                       │
│  ├── Content: Expert knowledge, physics rules              │
│  ├── Speed: <1μs                                           │
│  ├── Cost: $0                                              │
│  └── Output: Deterministic response                        │
└─────────────────────────────────────────────────────────────┘
```

## Performance Comparison

| Layer | Speed | Cost | Accuracy | Use Case |
|-------|-------|------|----------|----------|
| L1 | < 1μs | $0 | 100% | Exact match |
| L2 | < 1ms | $0 | 95%+ | Pattern match |
| L3 | ~300ms | API | Variable | Complex reasoning |