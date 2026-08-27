# QMClaw Three-Layer Decision Architecture

**What it does** — Explains QMClaw's three-layer hybrid decision architecture: L1 RuleEngine for exact matching, L2 Learned Rules for pattern matching, and L3 LLM Fallback for complex reasoning.

**Version**: v1.0.0  
**Status**: Stable  
**Trigger Keywords**: 决策, 三层决策, RuleEngine, 规则引擎, L1, L2, L3

---

## Architecture Overview

QMClaw uses a three-layer decision architecture that combines:
- **Speed**: Sub-millisecond response for routine tasks
- **Intelligence**: LLM-powered reasoning for complex cases
- **Learning**: Automatic rule extraction and promotion

## Layer Comparison

| Layer | Speed | Cost | Accuracy | Use Case |
|-------|-------|------|----------|----------|
| L1 RuleEngine | < 1μs | $0 | 100% | Exact match |
| L2 Learned Rules | < 1ms | $0 | 95%+ | Pattern match |
| L3 LLM Fallback | ~300ms | API | Variable | Complex reasoning |

## L1: RuleEngine Layer

- **Purpose**: Exact match of known situations
- **Content**: Expert knowledge, physics rules, device specifications
- **Speed**: Sub-microsecond
- **Example**: If T1 < 20μs → "Poor" quality flag

## L2: Learned Rules Layer

- **Purpose**: Pattern matching from historical data
- **Source**: L3 success cases promoted to rules
- **Speed**: Sub-millisecond
- **Example**: "Low visibility + specific f10 range → adjust readout power"

## L3: LLM Fallback Layer

- **Purpose**: Complex reasoning for novel situations
- **Capabilities**: Natural language understanding, multi-step reasoning
- **Speed**: ~300ms
- **Example**: "Qubit showing unexpected behavior → analyze and diagnose"

## L3→L2 Rule Promotion

```
L3 Success Case → Pattern Recognition → Rule Extraction → Validation → L2 Rule
```

### Promotion Criteria

| Criterion | Threshold |
|-----------|-----------|
| Success rate | > 95% |
| Sample count | > 100 cases |
| Pattern stability | No drift over time |

## RuleEngine API

```python
# Condition functions
def frequency_in_range(f10, target, tolerance=0.01):
    return abs(f10 - target) <= tolerance

def visibility_above_threshold(vis, threshold=0.5):
    return vis >= threshold

def t1_above_minimum(t1, minimum=20):
    return t1 >= minimum

# Action functions
def escalate_to_llm(reason):
    return {'action': 'escalate', 'expected_layer': 'L3'}

def apply_calibration(params):
    return {'action': 'calibrate', 'params': params}
```