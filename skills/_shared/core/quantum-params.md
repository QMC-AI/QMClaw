# Quantum Parameters — Shared Reference

Standard parameter definitions for superconducting transmon qubits.

## Core Qubit Parameters

| Parameter | Symbol | Unit | Typical Range | Description |
|-----------|--------|------|---------------|-------------|
| **Readout Frequency** | `fread` | GHz | 6.0 ~ 7.0 | Readout cavity resonance |
| **Qubit Frequency** | `f10` | GHz | 2.6 ~ 5.0 | 0-1 transition frequency |
| **Coupling Frequency** | `fc` | GHz | 2.0 ~ 6.0 | Coupling cavity frequency |
| **Qubit Qubit 1-2** | `f21` | GHz | f10 - 0.2 | 1-2 transition frequency |
| **Bias Voltage** | `bias_z` | V | -2.0 ~ 2.0 | Z bias voltage |
| **Z Pulse Amplitude** | `zpa` | - | -1.0 ~ 1.0 | Z pulse amplitude |

## Gate Parameters

| Parameter | Symbol | Unit | Typical Range | Description |
|-----------|--------|------|---------------|-------------|
| **Pi Gate Amplitude** | `PiGate.amp` | V | 0.5 ~ 2.0 | X gate pulse amplitude |
| **Pi Gate Length** | `PiGate.length` | ns | 20 ~ 100 | X gate pulse duration |
| **Pi/2 Gate Amplitude** | `PiHalf.amp` | V | PiGate.amp / 2 | X/2 gate amplitude |
| **Readout Power** | `ReadIn.power` | dBm | -40 ~ 0 | Readout power |
| **Readout ZPA** | `ReadIn.zpa` | - | -1.0 ~ 1.0 | Readout Z pulse amplitude |

## Quality Parameters

| Parameter | Symbol | Unit | Good | Excellent | Description |
|-----------|--------|------|------|-----------|-------------|
| **Relaxation Time** | `T1` | μs | 50-100 | >100 | 0-1 relaxation time |
| **Coherence Time** | `T2*` | μs | 30-50 | >50 | Ramsey decoherence time |
| **Visibility** | `vis` | - | 0.5-0.8 | >0.8 | IQ readout separation |
| **XEB Fidelity** | `f_xeb` | - | 0.99-0.995 | >0.995 | Cross-resonance fidelity |

## Parameter Naming Conventions

| Convention | Example | Used in |
|------------|---------|---------|
| Lowercase with underscore | `bias_z` | Standard parameters |
| Nested dot notation | `qobj.PiGate.amp` | Nested objects |
| Method names | `discriminator.method` | Configuration |

## Example Access

```python
# Standard parameters
fread = float(qobj.fread)      # Readout frequency (GHz)
f10 = float(qobj.f10)          # Qubit frequency (GHz)
bias_z = float(qobj.bias_z)    # Z bias voltage

# Gate parameters
pi_amp = float(qobj.PiGate.amp)       # Pi amplitude
pi_length = float(qobj.PiGate.length) # Pi pulse length

# Readout parameters
readout_power = float(qobj.ReadIn.power)  # dBm
readout_zpa = float(qobj.ReadIn.zpa)      # Z pulse amplitude
```