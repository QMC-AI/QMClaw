"""
Qubit calibration and optimization data models.

This module provides data classes and managers for qubit parameter calibration
and performance optimization. It tracks qubit metrics (readout fidelity, gate fidelity,
T1 time) and manages parameter backup/restore during optimization experiments.

Classes:
    TestResult: Enum for optimization test outcomes (IMPROVED/DEGRADED/UNCHANGED).
    QubitMetrics: Dataclass holding qubit performance metrics.
    QubitParameterManager: Manages qubit parameters, metrics, and state backup/restore.
    QubitOptimizationFramework: Orchestrates multi-step optimization test sequences.

Example:
    >>> manager = QubitParameterManager(qubit, initial_order)
    >>> framework = QubitOptimizationFramework(manager)
    >>> framework.run_test_sequence([try_optimization], "calibration_test")
"""

import numpy as np
from dataclasses import dataclass
from typing import Callable, List, Dict, Any, Optional
import time
from enum import Enum

# Note: 'from backend import *' is deferred - these will be set by the entry point
# when running inside the quantum worker context


class TestResult(Enum):
    """
    Result of a qubit optimization test sequence.

    Values:
        IMPROVED: At least one metric improved significantly.
        DEGRADED: Any metric degraded significantly.
        UNCHANGED: No significant change in any metric.
    """
    IMPROVED = "improved"
    DEGRADED = "degraded"
    UNCHANGED = "unchanged"


@dataclass
class QubitMetrics:
    """
    Performance metrics for a single qubit.

    Attributes:
        readout_fidelity (float): Readout fidelity (0 to 1).
        single_qubit_gate_fidelity (float): Single-qubit gate fidelity (0 to 1).
        t1 (float): T1 relaxation time in nanoseconds.
    """
    readout_fidelity: float
    single_qubit_gate_fidelity: float
    t1: float

    def __str__(self):
        return f"读取保真度: {self.readout_fidelity:.4f}, 门保真度: {self.single_qubit_gate_fidelity:.4f}, T1: {self.t1:.2f} ns"

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary for JSON serialization."""
        return {
            "readout_fidelity": self.readout_fidelity,
            "single_qubit_gate_fidelity": self.single_qubit_gate_fidelity,
            "t1": self.t1,
        }


class QubitParameterManager:
    """
    Manages qubit parameters, metrics, and state backup/restore.

    Provides parameter update, measurement, and state management for qubit
    calibration workflows. Supports rollback to a backup state if optimization
    experiments degrade performance.

    Attributes:
        qobj: The qubit object being managed.
        current_params (dict): Current qubit parameter values.
        current_metrics (QubitMetrics): Current performance metrics.

    Example:
        >>> manager = QubitParameterManager(qubit, initial_order=123)
        >>> manager.measure_metrics()
        QubitMetrics(readout_fidelity=0.92, single_qubit_gate_fidelity=0.99, t1=45.3)
    """

    def __init__(self, qobj, initial_order: Any = None):
        """
        Initialize qubit parameter manager.

        Args:
            qobj: The qubit object.
            initial_order: Initial dataset order (optional).
        """
        self.qobj = qobj
        self.current_order = initial_order
        self.backup_order = initial_order
        self.current_metrics: Optional[QubitMetrics] = None
        self.backup_metrics: Optional[QubitMetrics] = None
        self.current_params: Dict[str, Any] = {}
        self.backup_params: Dict[str, Any] = {}

        # These will be injected by the worker context
        self._data = None
        self._s = None
        self._sq = None
        self._qter = None

    def set_context(self, data, s, sq, qter):
        """Set the LabRAD context (called by worker entry point)."""
        self._data = data
        self._s = s
        self._sq = sq
        self._qter = qter
        if data is not None and initial_order is not None:
            self.current_params = self.get_params(initial_order)
            self.backup_params = self.current_params.copy()

    def get_params(self, order: Any = None) -> Dict[str, Any]:
        """Get parameters from a dataset order."""
        if self._data is not None and order is not None:
            self._data.loadDataset(order)
            return self._data.parameters
        return {}

    def write_cfg(self, order: Any):
        """Write configuration back to qubit registers."""
        if self._data is None or self._s is None:
            return
        self._data.loadDataset(int(order))
        qobj_name = self._data.parameters.get('config', [None])[0]
        if qobj_name is None:
            return

        qobj_reg = self._s[qobj_name]
        dp = self._data.parameters

        # Write frequency parameters
        for key in ['fread', 'f10', 'fc', 'f21', 'bias_z']:
            if f'{qobj_name}.{key}' in dp:
                setattr(qobj_reg, key, dp[f'{qobj_name}.{key}'])

        # Write PiGate parameters
        for key in ['alpha', 'amp', 'length', 'zpa']:
            if f'{qobj_name}.PiGate.{key}' in dp:
                setattr(qobj_reg.PiGate, key, dp[f'{qobj_name}.PiGate.{key}'])

        # Write PiHalf parameters
        for key in ['alpha', 'amp', 'length', 'zpa']:
            if f'{qobj_name}.PiHalf.{key}' in dp:
                setattr(qobj_reg.PiHalf, key, dp[f'{qobj_name}.PiHalf.{key}'])

        # Write ReadIn parameters
        for key in ['length', 'power', 'ring_power', 'ring_length', 'zpa']:
            if f'{qobj_name}.ReadIn.{key}' in dp:
                setattr(qobj_reg.ReadIn, key, dp[f'{qobj_name}.ReadIn.{key}'])

        # Write ReadOut parameters
        for key in ['amp', 'length', 'window_type']:
            if f'{qobj_name}.ReadOut.{key}' in dp:
                setattr(qobj_reg.ReadOut, key, dp[f'{qobj_name}.ReadOut.{key}'])

        # Write discriminator parameters
        for key in ['center0', 'center1', 'measure_f0', 'measure_f1', 'method', 'radius0', 'threshold']:
            if f'{qobj_name}.discriminator.{key}' in dp:
                setattr(qobj_reg.discriminator, key, dp[f'{qobj_name}.discriminator.{key}'])

    def measure_metrics(self) -> QubitMetrics:
        """
        Measure current qubit performance metrics.

        Returns:
            QubitMetrics: The measured metrics (readout fidelity, gate fidelity, T1).
        """
        if self._sq is None or self._data is None or self._qter is None:
            # Return mock metrics if not in real context
            return QubitMetrics(
                readout_fidelity=0.85 + 0.1 * np.random.random(),
                single_qubit_gate_fidelity=0.95 + 0.04 * np.random.random(),
                t1=50 + 30 * np.random.random()
            )

        qobj = self.qobj

        # Readout fidelity
        self._sq.iqraw(qobj, do_plot=False)
        readout_fidelity = self._qter.fitData(-1, collect=True, do_plot=False)[1][-2][0]

        # T1 time
        self._sq.t1(qobj, zpa=0, do_plot=False)
        self._data.loadDataset(-1)
        t1_res = self._data.T1(self._data)
        t1 = t1_res[1][1][0]

        # Single gate fidelity
        self._sq.xeb(qobj, do_plot=False)
        self._data.loadDataset(-1)
        xeb_res = self._data.XEB(self._data, [-1], collect=True)
        gate_fidelity = 1 - xeb_res[self._data.dataset_num]['error_Pauli_per_cycle'] / 1.5

        self.current_order = self._data.dataset_num
        return QubitMetrics(
            readout_fidelity=readout_fidelity,
            single_qubit_gate_fidelity=gate_fidelity,
            t1=t1
        )

    def update_parameters(self, new_params: Dict[str, Any]):
        """Update qubit parameters using dot-notation keys."""
        qobj = self.qobj
        for parameter_name, param_value in new_params.items():
            para_list = parameter_name.split('.')
            if len(para_list) == 3:
                qobj.regs[para_list[0]][para_list[1]][para_list[2]] = param_value
            elif len(para_list) == 2:
                qobj.regs[para_list[0]][para_list[1]] = param_value
            elif len(para_list) == 1:
                qobj.regs[para_list[0]] = param_value
        self.current_params.update(new_params)

    def backup_current_state(self):
        """Backup the current state (params, metrics, order)."""
        self.backup_params = self.current_params.copy()
        self.backup_metrics = self.current_metrics
        self.backup_order = self.current_order

    def restore_backup_state(self):
        """Restore to the backup state."""
        self.current_params = self.backup_params.copy()
        self.current_metrics = self.backup_metrics
        self.write_cfg(self.backup_order)


class QubitOptimizationFramework:
    """
    Quantum qubit optimization framework.

    Orchestrates multi-step optimization test sequences, evaluating improvement
    and managing state rollback on degradation.

    Example:
        >>> framework = QubitOptimizationFramework(qubit_manager)
        >>> result = framework.run_test_sequence([test_func], "calibration")
    """

    def __init__(self, qubit_manager: QubitParameterManager):
        self.qubit_manager = qubit_manager
        self.test_history: List[Dict[str, Any]] = []

    def evaluate_improvement(
        self, old_metrics: QubitMetrics, new_metrics: QubitMetrics
    ) -> TestResult:
        """
        Evaluate performance improvement between two metric sets.

        Args:
            old_metrics: Previous metrics.
            new_metrics: New metrics after test.

        Returns:
            TestResult: IMPROVED, DEGRADED, or UNCHANGED.
        """
        fidelity_threshold = 0.001
        t1_threshold = 1.0

        readout_improved = new_metrics.readout_fidelity - old_metrics.readout_fidelity > fidelity_threshold
        gate_improved = new_metrics.single_qubit_gate_fidelity - old_metrics.single_qubit_gate_fidelity > fidelity_threshold
        t1_improved = new_metrics.t1 - old_metrics.t1 > t1_threshold

        readout_degraded = new_metrics.readout_fidelity - old_metrics.readout_fidelity < -fidelity_threshold
        gate_degraded = new_metrics.single_qubit_gate_fidelity - old_metrics.single_qubit_gate_fidelity < -fidelity_threshold
        t1_degraded = new_metrics.t1 - old_metrics.t1 < -t1_threshold

        if readout_degraded or gate_degraded or t1_degraded:
            return TestResult.DEGRADED
        elif readout_improved or gate_improved or t1_improved:
            return TestResult.IMPROVED
        else:
            return TestResult.UNCHANGED

    def run_test_sequence(
        self, test_sequence: List[Callable], test_name: str = "unnamed_test"
    ) -> Dict[str, Any]:
        """
        Run a sequence of optimization tests.

        Args:
            test_sequence: List of test functions to execute.
            test_name: Name identifier for this test sequence.

        Returns:
            dict: Test record with metrics, improvement status, and history.
        """
        # 1. Measure initial metrics
        initial_metrics = self.qubit_manager.measure_metrics()
        self.qubit_manager.current_metrics = initial_metrics

        # 2. Backup current state
        self.qubit_manager.backup_current_state()

        # 3. Execute test sequence
        test_results = []
        for i, test_func in enumerate(test_sequence):
            try:
                result = test_func(self.qubit_manager)
                test_results.append(result)
            except Exception as e:
                test_results.append({"status": "failed", "error": str(e)})

        # 4. Measure final metrics
        final_metrics = self.qubit_manager.measure_metrics()

        # 5. Evaluate improvement
        improvement = self.evaluate_improvement(initial_metrics, final_metrics)

        # 6. Rollback if degraded
        if improvement == TestResult.DEGRADED:
            self.qubit_manager.restore_backup_state()
            final_metrics = initial_metrics
        else:
            self.qubit_manager.current_metrics = final_metrics

        # 7. Record test history
        test_record = {
            "test_name": test_name,
            "timestamp": time.time(),
            "initial_metrics": initial_metrics.to_dict(),
            "final_metrics": final_metrics.to_dict(),
            "improvement": improvement.value,
            "test_results": test_results,
            "parameters_used": self.qubit_manager.current_params.copy()
        }
        self.test_history.append(test_record)

        return test_record

    def get_test_history(self) -> List[Dict[str, Any]]:
        """Get the optimization test history."""
        return self.test_history

    def print_summary(self):
        """Print optimization summary to console."""
        print("\n" + "=" * 50)
        print("量子比特优化框架总结")
        print("=" * 50)
        print(f"量子比特ID: {self.qubit_manager.qobj}")

        if self.qubit_manager.current_metrics:
            print(f"当前指标: {self.qubit_manager.current_metrics}")

        print(f"\n执行的测试数量: {len(self.test_history)}")
        improved_tests = [t for t in self.test_history if t["improvement"] == "improved"]
        print(f"性能提升的测试: {len(improved_tests)}")

        for i, test in enumerate(self.test_history):
            status = "✅" if test["improvement"] == "improved" else "❌"
            print(f"{i+1}. {test['test_name']} {status}")