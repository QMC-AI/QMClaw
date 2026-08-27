"""
Quantum Worker Entry Point - JSON-RPC over stdin/stdout.

This module provides a JSON-RPC interface for the quantum control layer,
allowing a Node.js process to interact with LabRAD and run experiments.

Usage:
    echo '{"jsonrpc":"2.0","method":"init_session","params":{"user":"LQHL"},"id":1}' | python entry.py

Mock mode (no hardware):
    echo '{"jsonrpc":"2.0","method":"init_session","params":{"user":"LQHL","mock":true},"id":1}' | python entry.py
"""

import json
import sys
import time
import random
from typing import Any, Dict, Optional

from .model import (
    QubitMetrics,
    QubitParameterManager,
    QubitOptimizationFramework,
    TestResult,
)
from .optimizer import (
    OptimizationStrategy,
    ScanDirection,
    SingleParameterConfig,
    SingleParameterOptimizer,
)


# Mock data generators
def _mock_spectroscopy_data(n_points: int = 100) -> list:
    """Generate mock spectroscopy data (Lorentzian dip)."""
    freq = [4.0 + i * 0.0001 for i in range(n_points)]
    amp = [0.5 - 0.4 / (1 + ((f - 4.5) ** 2 / 0.001) + random.uniform(-0.01, 0.01) for f in freq]
    phase = [random.uniform(-0.1, 0.1) for _ in freq]
    i_data = [random.gauss(0, 0.05) for _ in freq]
    q_data = [random.gauss(0, 0.05) for _ in freq]
    # Flatten as [freq, amp, phase, I, Q, prob]
    prob = [0.1 + 0.8 / (1 + ((f - 4.5) ** 2 / 0.001) for f in freq]
    return [[freq[i], amp[i], phase[i], i_data[i], q_data[i], prob[i]] for i in range(n_points)]


def _mock_iqraw_data(n_shots: int = 1000) -> list:
    """Generate mock IQ raw data (two Gaussian clusters)."""
    data = []
    for _ in range(n_shots // 2):
        data.append([random.gauss(0.2, 0.1), random.gauss(0.15, 0.1), random.gauss(-0.2, 0.1), random.gauss(-0.1, 0.1)])
    for _ in range(n_shots // 2):
        data.append([random.gauss(-0.3, 0.12), random.gauss(-0.25, 0.11), random.gauss(0.1, 0.09), random.gauss(0.05, 0.08)])
    return data


class QuantumWorker:
    """
    JSON-RPC handler for quantum control operations.

    Manages LabRAD connections, qubit objects, and provides methods for
    running experiments and optimization tasks. Supports mock mode for
    development without real hardware.
    """

    def __init__(self):
        self.cxn = None
        self.s = None
        self._qubits: Dict[str, Any] = {}
        self._current_session: Optional[str] = None
        self._mock_mode: bool = False

    def init_session(self, user: str = "LQHL", mock: bool = False) -> Dict[str, Any]:
        """
        Initialize LabRAD session connection.

        Args:
            user: LabRAD username.
            mock: If True, use mock mode without real hardware.

        Returns:
            dict: Session info with status and available qubits.
        """
        self._mock_mode = mock

        if mock:
            self._qubits = {f"q{i+1}ru{j}": None for i in range(3) for j in range(1, 5)}
            self._current_session = user
            return {
                "status": "connected",
                "user": user,
                "qubits": sorted(self._qubits.keys()),
                "session": "mock",
                "mock": True,
            }

        try:
            import labrad
            from lqms.pyle.workflow import switchSession
            from lqms.measure.basic import BasicTuner

            self.cxn = labrad.connect()
            self.s = switchSession(self.cxn, user=user)
            BasicTuner._sample = self.s
            self._qubits = self._discover_qubits()
            self._current_session = user
            return {
                "status": "connected",
                "user": user,
                "qubits": sorted(self._qubits.keys()),
                "session": str(self.s),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _discover_qubits(self) -> Dict[str, Any]:
        """Discover available qubits from the registry."""
        qubits = {}
        if self.s is not None:
            try:
                for key in self.s.keys():
                    if key.startswith("q"):
                        qubits[key] = self.s[key]
            except Exception:
                pass
        return qubits

    def list_qubits(self) -> Dict[str, Any]:
        """List available qubits."""
        if not self._qubits:
            self._qubits = self._discover_qubits()

        qubit_info = {}
        for name in sorted(self._qubits.keys()):
            if self._mock_mode:
                qubit_info[name] = {"f10": 4.5 + random.uniform(-0.1, 0.1), "fread": 6.5 + random.uniform(-0.05, 0.05)}
            else:
                qobj = self._qubits.get(name)
                try:
                    qubit_info[name] = {
                        "f10": float(qobj.regs.get("f10", 0)),
                        "fread": float(qobj.regs.get("fread", 0)),
                    }
                except Exception:
                    qubit_info[name] = {"f10": None, "fread": None}

        return {"qubits": qubit_info}

    def get_qubit(self, name: str) -> Optional[Any]:
        """Get qubit object by name."""
        if not self._qubits:
            self._qubits = self._discover_qubits()
        return self._qubits.get(name)

    def run_experiment(
        self, qubit_name: str, experiment: str = "spectroscopy", params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Run a single experiment on a qubit.

        In mock mode, returns simulated data.
        """
        if qubit_name not in self._qubits:
            return {"status": "error", "message": f"Qubit {qubit_name} not found"}

        params = params or {}
        time.sleep(0.5)  # Simulate experiment time

        # Mock data generation
        if self._mock_mode:
            if experiment == "spectroscopy":
                data = _mock_spectroscopy_data()
            elif experiment == "iqraw":
                data = _mock_iqraw_data()
            elif experiment == "ramsey":
                data = [[i, 0.5 + 0.3 * random.gauss(0, 1) * (1 if i % 2 == 0 else -1) + 0.1 * random.uniform(-1, 1) for i in range(50)]
            elif experiment == "t1":
                data = [[i * 1000, 0.9 * (0.5 + 0.5 * (0.95 ** (i * 1000 / 10000)) + random.uniform(-0.02, 0.02) for i in range(16)]]
            elif experiment == "s21":
                data = [[4.0 + i * 0.0002, 0.5 - 0.3 / (1 + ((f - 4.8) ** 2 / 0.0001) + random.uniform(-0.01, 0.01) for i, f in enumerate([4.0 + i * 0.0002 for i in range(100)])]
                data = [[d[0], d[1], random.uniform(-0.05, 0.05), random.gauss(0, 0.02), random.gauss(0, 0.02), d[1]] for d in data]
            elif experiment == "xeb":
                m_vals = [10, 30, 50, 70, 100, 150, 200]
                data = [[float(m), 0.9 + random.uniform(-0.05, 0.02) * (0.5 ** (m / 200)) for m in m_vals]
                data = [[d[0], d[1]] for d in data]
            else:
                data = [[i, random.uniform(0.3, 0.7)] for i in range(50)]
            return {"status": "success", "data": data}

        # Real hardware path
        import numpy as np
        from lqms.measure.tuners import sq_nodes

        qobj = self._qubits.get(qubit_name)
        if qobj is None:
            return {"status": "error", "message": f"Qubit {qubit_name} not found"}

        try:
            exp_map = {
                "spectroscopy": sq_nodes.spectroscopy,
                "s21": sq_nodes.s21,
                "ramsey": sq_nodes.ramsey_df,
                "iqraw": sq_nodes.iqraw,
                "t1": sq_nodes.t1,
                "xeb": sq_nodes.xeb,
            }

            exp_func = exp_map.get(experiment)
            if exp_func is None:
                return {"status": "error", "message": f"Unknown experiment: {experiment}"}

            exp_func(qobj, do_plot=False, **params)

            # Load result
            data = self._load_latest_dataset()
            return {"status": "success", "data": data.tolist() if data is not None else []}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _load_latest_dataset(self):
        """Load the latest dataset from LabRAD data vault."""
        try:
            from lqms.data_process import dc

            if self.cxn is not None:
                dv = self.cxn.data_vault
                data = dc.DataLab(self.s, dv)
                data.loadDataset(-1)
                return data.data if hasattr(data, "data") else None
        except Exception:
            pass
        return None

    def measure_metrics(self, qubit_name: str) -> Dict[str, Any]:
        """Measure qubit performance metrics."""
        if qubit_name not in self._qubits:
            return {"status": "error", "message": f"Qubit {qubit_name} not found"}

        if self._mock_mode:
            return {
                "status": "success",
                "metrics": {
                    "readout_fidelity": random.uniform(0.85, 0.98),
                    "single_qubit_gate_fidelity": random.uniform(0.95, 0.995),
                    "t1": random.uniform(30, 80),
                },
            }

        qobj = self._qubits.get(qubit_name)
        if qobj is None:
            return {"status": "error", "message": f"Qubit {qubit_name} not found"}

        try:
            manager = QubitParameterManager(qobj, initial_order=-1)
            metrics = manager.measure_metrics()
            return {
                "status": "success",
                "metrics": {
                    "readout_fidelity": metrics.readout_fidelity,
                    "single_qubit_gate_fidelity": metrics.single_qubit_gate_fidelity,
                    "t1": metrics.t1,
                },
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def run_optimization(
        self,
        qubit_name: str,
        parameter_name: str = "ReadIn.power",
        strategy: str = "LINEAR_SCAN",
        initial_value: float = -38.0,
        value_range: tuple = (-46.0, -30.0),
        step_size: float = 1.0,
    ) -> Dict[str, Any]:
        """Run single-parameter optimization on a qubit."""
        if qubit_name not in self._qubits:
            return {"status": "error", "message": f"Qubit {qubit_name} not found"}

        if self._mock_mode:
            # Simulate optimization iterations
            iterations = []
            best_idx = random.randint(5, 15)
            best_value = initial_value + best_idx * step_size

            for i in range(16):
                metrics = {
                    "readout_fidelity": 0.9 + 0.05 * (1 - abs(i - best_idx) / 16),
                    "single_qubit_gate_fidelity": 0.98 + 0.01 * (1 - abs(i - best_idx) / 16),
                    "t1": 50 + 10 * (1 - abs(i - best_idx) / 16),
                }
                iterations.append({
                    "iteration": i,
                    "parameter_value": initial_value + i * step_size,
                    "metrics": metrics,
                    "improvement": "improved" if i > 0 else "unchanged",
                })
                time.sleep(0.2)

            return {
                "status": "success",
                "best_value": best_value,
                "best_metrics": iterations[best_idx]["metrics"],
                "optimization_time": sum(it["iteration"] * 0.2 for it in iterations),
                "test_count": len(iterations),
            }

        qobj = self._qubits.get(qubit_name)
        if qobj is None:
            return {"status": "error", "message": f"Qubit {qubit_name} not found"}

        try:
            manager = QubitParameterManager(qobj, initial_order=-1)
            framework = QubitOptimizationFramework(manager)

            strategy_map = {
                "LINEAR_SCAN": OptimizationStrategy.LINEAR_SCAN,
                "BINARY_SEARCH": OptimizationStrategy.BINARY_SEARCH,
                "GRADIENT_ASCENT": OptimizationStrategy.GRADIENT_ASCENT,
            }
            opt_strategy = strategy_map.get(strategy, OptimizationStrategy.LINEAR_SCAN)

            config = SingleParameterConfig(
                parameter_name=parameter_name,
                initial_value=initial_value,
                value_range=value_range,
                step_size=step_size,
                scan_direction=ScanDirection.BOTH,
                optimization_strategy=opt_strategy,
            )

            optimizer = SingleParameterOptimizer(framework, config)
            result = optimizer.run_optimization()

            return {
                "status": "success",
                "best_value": result["best_value"],
                "best_metrics": {
                    "readout_fidelity": result["best_metrics"].readout_fidelity,
                    "single_qubit_gate_fidelity": result["best_metrics"].single_qubit_gate_fidelity,
                    "t1": result["best_metrics"].t1,
                },
                "optimization_time": result.get("optimization_time", 0),
                "test_count": result.get("test_count", 0),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def close(self) -> Dict[str, str]:
        """Close LabRAD connection."""
        try:
            if self.cxn is not None:
                self.cxn.disconnect()
                self.cxn = None
                self.s = None
            self._qubits = {}
            self._current_session = None
            self._mock_mode = False
            return {"status": "closed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


# Global worker instance
_worker = QuantumWorker()


def handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Handle a JSON-RPC request."""
    method = request.get("method")
    params = request.get("params", {}) or {}
    req_id = request.get("id")

    method_map = {
        "init_session": lambda p: _worker.init_session(**p),
        "list_qubits": lambda p: _worker.list_qubits(),
        "get_qubit": lambda p: {"qubit": p.get("name"), "exists": _worker.get_qubit(p.get("name")) is not None},
        "run_experiment": lambda p: _worker.run_experiment(**p),
        "measure_metrics": lambda p: _worker.measure_metrics(**p),
        "run_optimization": lambda p: _worker.run_optimization(**p),
        "close": lambda p: _worker.close(),
    }

    handler = method_map.get(method)
    if handler is None:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": f"Method not found: {method}"},
            "id": req_id,
        }

    try:
        result = handler(params)
        return {"jsonrpc": "2.0", "result": result, "id": req_id}
    except Exception as e:
        return {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": req_id}


def main():
    """Main loop: read JSON-RPC requests from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            print(json.dumps(response), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None,
            }), flush=True)


if __name__ == "__main__":
    main()
