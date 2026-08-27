# Quality Standards — Shared Reference

Standard quality thresholds and assessment criteria for quantum qubit calibration.

## Core Quality Metrics

| Metric | Symbol | Unit | Excellent | Good | Fair | Poor | Description |
|--------|--------|------|-----------|------|------|------|-------------|
| **SNR** | SNR | dB | > 10 | 5-10 | 2-5 | < 2 | Signal-to-noise ratio |
| **Visibility** | vis | - | > 0.8 | 0.5-0.8 | 0.2-0.5 | < 0.2 | IQ separation |
| **T1** | T1 | μs | > 100 | 50-100 | 20-50 | < 20 | Relaxation time |
| **T2*** | T2s | μs | > 50 | 30-50 | 15-30 | < 15 | Ramsey coherence |
| **XEB Fidelity** | f_xeb | - | > 0.995 | 0.99-0.995 | 0.98-0.99 | < 0.98 | Gate fidelity |
| **RB Fidelity** | f_rb | - | > 0.999 | 0.999-0.9999 | 0.99-0.999 | < 0.99 | RB gate fidelity |

## Quality Color Code

| Level | Color | Hex | Action |
|-------|-------|-----|--------|
| Excellent | Green | `#00FF00` | Continue monitoring |
| Good | Yellow | `#FFFF00` | Periodic check |
| Fair | Orange | `#FFA500` | Address soon |
| Poor | Red | `#FF0000` | Immediate action required |

## Quality Assessment Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Quality Check                                              │
│  ├── Measure metrics (T1, Visibility, SNR, Fidelity)        │
│  ├── Compare against thresholds                             │
│  └── Assign quality level                                   │
├─────────────────────────────────────────────────────────────┤
│  If Excellent → Continue normal operation                   │
│  If Good → Schedule periodic review                         │
│  If Fair → Investigate cause, plan remediation              │
│  If Poor → Halt operations, recalibrate immediately         │
└─────────────────────────────────────────────────────────────┘
```

## Metric Collection Methods

| Metric | Collection Method | Tool/Function |
|--------|-------------------|---------------|
| SNR | `sq.iqraw()` → `qter.fitData()` | SNR from IQ fit |
| Visibility | `sq.iqraw()` → `qter.fitData()` | `result[1][-2]` |
| T1 | `sq.t1()` → `dp.T1()` | Exponential fit |
| T2* | `sq.ramsey_df()` → fit | Ramsey decay fit |
| XEB | `px.XEB()` | Clifford fidelity |
| RB | Randomized Benchmarking | Gate fidelity |

## Threshold Adjustments

Thresholds may vary based on:
- **Device type**: Different qubit designs have different limits
- **Experimental conditions**: Temperature, magnetic field
- **Measurement parameters**: Integration time, averaging

Always verify thresholds against your specific device specifications.