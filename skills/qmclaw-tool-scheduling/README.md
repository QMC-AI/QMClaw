# QMClaw Tool Scheduling & Backend Interface

**What it does** — Tool scheduling layer including whitelist mechanism, MCP server integration, centralized scheduling with distributed execution, and backend adapters for instruments, simulators, and emulators.

**Version**: v1.0.0  
**Status**: Stable  
**Trigger Keywords**: 工具调度, 后端接口, MCP, Center Agent, Backend Adapter

---

## Architecture Overview

QMClaw uses a **centralized scheduling, distributed execution** multi-agent model:

| Component | Responsibility |
|-----------|---------------|
| Center Agent | Task decomposition, routing, result aggregation |
| Sub-Agents | Independent execution of specific tools |
| Tool Registry | Whitelist management and permission validation |
| Backend Adapters | Unified instrument/simulator interface |

---

## Tool Registry (Whitelist)

### Safety Design

```python
TOOL_WHITELIST = {
    # Measurement tools
    'sq.s21': {'permission': 'measurement', 'timeout': 30},
    'sq.spectroscopy': {'permission': 'measurement', 'timeout': 60},
    'sq.iqraw': {'permission': 'measurement', 'timeout': 15},
    
    # Calibration tools
    'sq.piamp': {'permission': 'calibration', 'timeout': 45},
    'sq.pidf': {'permission': 'calibration', 'timeout': 60},
    'sq.ramsey_df': {'permission': 'calibration', 'timeout': 120},
    
    # Analysis tools
    'qter.fitData': {'permission': 'analysis', 'timeout': 10},
    'dp.T1': {'permission': 'analysis', 'timeout': 5},
    
    # Control tools
    'switchSession': {'permission': 'control', 'timeout': 5},
    'update_registry': {'permission': 'control', 'timeout': 5},
}
```

---

## Backend Types

### Instrument Backend (Real Hardware)

```python
class InstrumentBackend:
    def measure_s21(self, freq_range, power):
        """S21 measurement with real VNA"""
        
    def apply_pulse(self, qubit, pulse_params):
        """Apply pulse with real RF source"""
```

### Simulator Backend

```python
class SimulatorBackend:
    def run_circuit(self, circuit, shots=1000):
        """Run quantum circuit on Qiskit Aer"""
```

### Emulator Backend

```python
class EmulatorBackend:
    def emulate(self, config):
        """QEMU-based hardware emulation"""
```

---

## MCP (Model Context Protocol)

Standardized backend interface protocol:

```python
class MCPAdapter:
    def connect(self, device_id):
        """Establish device connection"""
        
    def call(self, tool_name, params):
        """Execute tool call"""
        
    def disconnect(self, device_id):
        """Close connection"""
```

---

## Task Queue Management

| Priority | Use Case | Example |
|----------|----------|---------|
| HIGH | Real-time calibration | Emergency frequency adjustment |
| NORMAL | Standard experiments | Routine spectroscopy, Rabi |
| LOW | Background analysis | Data archival, report generation |