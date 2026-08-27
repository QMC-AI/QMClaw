# QMClaw Single-Qubit Tune-up Workflow

**What it does** — Complete 15-step single-qubit calibration workflow with state machine transitions, Panel batch processing, and Markov prediction for automated experiment sequence generation.

**Version**: v1.0.0  
**Status**: Stable  
**Trigger Keywords**: 调校, 工作流, workflow, tune-up, 状态机, Panel, Markov

---

## 15-Step Calibration Workflow

### Phase 1: Readout Cavity (Steps 1-3)

1. **S21 Scan** - Confirm readout cavity frequency
2. **S21 Power 2D** - Optimize readout power
3. **S21 ZPA 2D** - Z bias tuning

### Phase 2: Qubit Frequency (Steps 4-8)

4. **Spectroscopy** - Identify qubit frequency (f10)
5. **IQ Raw** - Verify readout separation (✓)
6. **Pi Amp Scan** - Find π pulse amplitude
7. **Pi DF** - f10 fine-tuning (✓)
8. **Ramsey DF** - Frequency refinement (✓)

### Phase 3: Gate Calibration (Steps 9-15)

9. **S21 Dispersive** - Dispersive shift readout tuning
10. **Set Pi (X)** - X gate fine-tune (✓)
11. **Set Pi (X/2)** - X/2 gate fine-tune (✓)
12. **Timing XYZ** - Synchronization (✓)
13. **Pulse Shape** - Pulse correction (✓)
14. **Spec Auto** - f10-ZPA relationship (✓)
15. **T1 Measurement** - Relaxation time (✓)

---

## State Machine

```
┌──────────────────────────────────────────────────────────────┐
│                        STATE FLOW                            │
└──────────────────────────────────────────────────────────────┘

initialized → cavity_found → spec_done → iq_calibrated → 
    ↓
rabi_measured → pi_calibrated → freq_tuned → pulse_shape_done → 
    ↓
t1_measured → xeb_verified → complete

FAIL: Any state can transition to initialized for retry
```

### State Transitions

| Current State | Next State | Condition |
|---------------|------------|-----------|
| initialized | cavity_found | S21 resonance found |
| cavity_found | spec_done | f10 identified |
| spec_done | iq_calibrated | Visibility > 0.3 |
| iq_calibrated | rabi_measured | Pi amp found |
| rabi_measured | pi_calibrated | Pi pulse optimized |
| pi_calibrated | freq_tuned | Detuning < 0.1 MHz |
| freq_tuned | t1_measured | T1 > 20μs |
| t1_measured | complete | All metrics met |

---

## Panel Batch Processing

### Panel Configuration

| Parameter | Value |
|-----------|-------|
| Panel size | 10-20 qubits |
| Parallelism | Within panel |
| Sequential | Between panels |
| Aggregation | Result merge |

### Execution Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Panel 1 (Q1-Q20)                                          │
│  ├── Parallel: Calibrate Q1, Q2, ..., Q20                  │
│  └── Aggregate: Merge results                              │
├─────────────────────────────────────────────────────────────┤
│  Panel 2 (Q21-Q40)                                         │
│  ├── Parallel: Calibrate Q21, Q22, ..., Q40                │
│  └── Aggregate: Merge results                              │
├─────────────────────────────────────────────────────────────┤
│  ...                                                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Markov Prediction

| Metric | Value |
|--------|-------|
| Prediction accuracy | 71.6% |
| Reduction in experiments | ~30% |
| Application | Experiment sequence |

Markov prediction uses historical data to predict the next optimal experiment, reducing unnecessary measurements and speeding up calibration.