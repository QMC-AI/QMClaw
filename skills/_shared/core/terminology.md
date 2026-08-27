# Terminology — Shared Reference

Consistent terminology for QMClaw skills.

## Abbreviations

| Abbreviation | Full Term | Description |
|--------------|-----------|-------------|
| **Qubit** | Quantum Bit | Basic unit of quantum information |
| **QED** | Quantum Electrodynamics | Cavity-QED system |
| **Transmon** | Transmon Qubit | Josephson junction + capacitor |
| **S21** | Transmission S-parameter | Cavity readout signal |
| **Rabi** | Rabi Oscillation | Oscillations between qubit states |
| **Ramsey** | Ramsey Interference | Frequency measurement technique |
| **T1** | Relaxation Time | |0⟩ → |1⟩ relaxation time |
| **T2** | Coherence Time | Dephasing time |
| **XEB** | Cross-Resonance Error Benchmarking | Gate fidelity measurement |
| **RB** | Randomized Benchmarking | Randomized gate fidelity |
| **IQ** | In-phase/Quadrature | Readout signal components |
| **ZPA** | Z Pulse Amplitude | Z control pulse amplitude |
| **f10** | Qubit Frequency | 0-1 transition frequency |
| **fread** | Readout Frequency | Cavity readout frequency |
| **Pi** | π Pulse | 180° rotation pulse |
| **Pi/2** | π/2 Pulse | 90° rotation pulse |
| **MCP** | Model Context Protocol | Tool calling protocol |
| **RAG** | Retrieval Augmented Generation | Knowledge retrieval method |
| **L1/L2/L3** | Layer 1/2/3 | QMClaw decision layers |

## Chinese-English Mapping

| English | 中文 | Context |
|---------|------|---------|
| Calibration | 校准 | Parameter tuning |
| Characterization | 表征 | Device characterization |
| Tune-up | 调校 | Experimental tune-up |
| Benchmarking | 标定 | Performance verification |
| Readout | 读取 | State measurement |
| Spectroscopy | 能谱 | Frequency measurement |
| Discriminator | 判别器 | State classifier |
| Visibility | 区分度 | Readout separation |
| Fidelity | 保真度 | Measurement accuracy |
| Gate | 门 | Quantum gate operation |

## Parameter Units

| Parameter | Unit | Symbol |
|-----------|------|--------|
| Frequency | Gigahertz | GHz |
| Frequency | Megahertz | MHz |
| Time | Microseconds | μs |
| Time | Nanoseconds | ns |
| Power | Decibel-milliwatts | dBm |
| Voltage | Volts | V |
| Amplitude | (normalized) | - |

## Skill Naming Convention

| Prefix | Purpose | Example |
|--------|---------|---------|
| `qmclaw-` | QMClaw framework skills | `qmclaw-framework-overview` |
| `quantum-` | Application skills | `quantum-calibration` |

## Version Notation

| Version | Status | Description |
|---------|--------|-------------|
| v1.0.0 | Draft | Initial release, rules defined |
| v1.1.0 | Beta | Tested on examples, edge cases may remain |
| v2.0.0 | Stable | Validated, rules settled |