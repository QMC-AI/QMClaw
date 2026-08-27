"""
Single-parameter optimization algorithms for qubit calibration.

This module provides optimization strategies for tuning individual qubit parameters
such as readout power, frequency, amplitude, etc. It supports three optimization
algorithms: binary search, linear scan, and gradient ascent.

Classes:
    ScanDirection: Enum for scan direction (INCREASE/DECREASE/BOTH).
    OptimizationStrategy: Enum for optimization method.
    SingleParameterConfig: Configuration dataclass for optimization.
    SingleParameterOptimizer: Orchestrates single-parameter optimization.

Example:
    >>> config = SingleParameterConfig(
    ...     parameter_name="ReadIn.power",
    ...     initial_value=-38,
    ...     value_range=(-46, -30),
    ...     step_size=1,
    ...     optimization_strategy=OptimizationStrategy.LINEAR_SCAN
    ... )
    >>> optimizer = SingleParameterOptimizer(framework, config)
    >>> result = optimizer.run_optimization()
"""

import numpy as np
from dataclasses import dataclass
from typing import Callable, List, Dict, Any, Optional
from enum import Enum
import time

from .model import QubitOptimizationFramework, QubitMetrics, TestResult


class ScanDirection(Enum):
    """
    Direction for parameter scan.

    Values:
        INCREASE: Scan only in increasing direction.
        DECREASE: Scan only in decreasing direction.
        BOTH: Scan in both directions from initial value.
    """
    INCREASE = "increase"
    DECREASE = "decrease"
    BOTH = "both"


class OptimizationStrategy(Enum):
    """
    Optimization algorithm to use.

    Values:
        BINARY_SEARCH: Bisection search (efficient for unimodal functions).
        LINEAR_SCAN: Exhaustive linear search (thorough but slow).
        GRADIENT_ASCENT: Hill climbing using finite-difference gradient.
    """
    BINARY_SEARCH = "binary_search"
    LINEAR_SCAN = "linear_scan"
    GRADIENT_ASCENT = "gradient_ascent"


@dataclass
class SingleParameterConfig:
    """
    Configuration for single-parameter optimization.

    Attributes:
        parameter_name (str): Name of qubit parameter to optimize (e.g., "ReadIn.power").
        initial_value (float): Starting value for the optimization.
        value_range (tuple): (min, max) bounds for the parameter.
        step_size (float): Step size / precision for scanning.
        scan_direction (ScanDirection): Direction to scan (default BOTH).
        optimization_strategy (OptimizationStrategy): Algorithm to use (default LINEAR_SCAN).
        max_iterations (int): Maximum iterations (default 50).
        improvement_threshold (float): Threshold for considering improvement (default 0.001).
    """
    parameter_name: str
    initial_value: float
    value_range: tuple
    step_size: float
    scan_direction: ScanDirection = ScanDirection.BOTH
    optimization_strategy: OptimizationStrategy = OptimizationStrategy.LINEAR_SCAN
    max_iterations: int = 50
    improvement_threshold: float = 0.001

    def validate(self):
        """Validate configuration parameters."""
        if self.value_range[0] >= self.value_range[1]:
            raise ValueError("参数范围无效: min 必须小于 max")
        if self.step_size <= 0:
            raise ValueError("步长必须大于0")
        if self.initial_value is None:
            self.initial_value = (self.value_range[0] + self.value_range[1]) / 2


class SingleParameterOptimizer:
    """
    Single-parameter optimizer using configurable optimization strategy.

    Supports binary search (efficient for unimodal), linear scan (thorough),
    and gradient ascent (hill climbing). Each strategy runs parameter test
    sequences via the QubitOptimizationFramework and tracks best results.

    Attributes:
        config (SingleParameterConfig): Optimization configuration.
        best_value (float): Best parameter value found.
        best_metrics (QubitMetrics): Metrics at best value.
        optimization_history (list): Record of all optimization iterations.

    Example:
        >>> optimizer = SingleParameterOptimizer(framework, config)
        >>> result = optimizer.run_optimization()
        >>> print(f"Best value: {result['best_value']}")
    """

    def __init__(
        self,
        qubit_framework: QubitOptimizationFramework,
        config: SingleParameterConfig
    ):
        self.framework = qubit_framework
        self.config = config
        self.optimization_history: List[Dict[str, Any]] = []
        self.best_metrics: Optional[QubitMetrics] = None
        self.best_value: Optional[float] = None

    def create_parameter_test(self, param_value: float) -> Callable:
        """Create a test function that sets a specific parameter value."""
        def test_function(qubit_manager):
            qubit_manager.update_parameters({self.config.parameter_name: param_value})
            return {
                "parameter_name": self.config.parameter_name,
                "parameter_value": param_value,
                "timestamp": time.time()
            }
        return test_function

    def linear_scan_optimization(self) -> Dict[str, Any]:
        """Linear scan optimization - exhaustive search within range."""
        min_val, max_val = self.config.value_range
        best_value = self.config.initial_value
        best_metrics = self.framework.qubit_manager.current_metrics

        # Generate scan points
        if self.config.scan_direction == ScanDirection.BOTH:
            values_to_test = [best_value]
            current = best_value + self.config.step_size
            while current <= max_val:
                values_to_test.append(current)
                current += self.config.step_size

            current = best_value - self.config.step_size
            while current >= min_val:
                values_to_test.append(current)
                current -= self.config.step_size

        elif self.config.scan_direction == ScanDirection.INCREASE:
            values_to_test = np.arange(
                best_value, max_val + self.config.step_size, self.config.step_size
            ).tolist()
        else:  # DECREASE
            values_to_test = np.arange(
                best_value, min_val - self.config.step_size, -self.config.step_size
            ).tolist()

        # Filter and sort
        values_to_test = [v for v in values_to_test if min_val <= v <= max_val]
        values_to_test = sorted(set(values_to_test))

        for i, value in enumerate(values_to_test):
            test_func = self.create_parameter_test(value)
            result = self.framework.run_test_sequence(
                [test_func],
                f"linear_scan_{self.config.parameter_name}_{i}"
            )

            current_metrics = result["final_metrics"]

            # Update best
            if self.is_improvement(best_metrics, current_metrics):
                best_metrics = self._dict_to_metrics(current_metrics)
                best_value = value

            self.optimization_history.append({
                "iteration": i,
                "parameter_value": value,
                "metrics": current_metrics,
                "improvement": result["improvement"]
            })

        self.best_value = best_value
        self.best_metrics = best_metrics
        return {"best_value": best_value, "best_metrics": best_metrics}

    def binary_search_optimization(self) -> Dict[str, Any]:
        """Binary search optimization - efficient for unimodal functions."""
        low, high = self.config.value_range
        best_value = self.config.initial_value
        best_metrics = self.framework.qubit_manager.current_metrics

        iteration = 0
        while (high - low) > self.config.step_size and iteration < self.config.max_iterations:
            iteration += 1
            mid = (low + high) / 2

            # Test mid point
            test_func = self.create_parameter_test(mid)
            result = self.framework.run_test_sequence(
                [test_func],
                f"binary_search_{self.config.parameter_name}_{iteration}"
            )
            mid_metrics = self._dict_to_metrics(result["final_metrics"])

            # Test left point
            left = low + (mid - low) / 4
            test_func_left = self.create_parameter_test(left)
            result_left = self.framework.run_test_sequence(
                [test_func_left],
                f"binary_search_left_{iteration}"
            )
            left_metrics = self._dict_to_metrics(result_left["final_metrics"])

            # Test right point
            right = mid + (high - mid) / 4
            test_func_right = self.create_parameter_test(right)
            result_right = self.framework.run_test_sequence(
                [test_func_right],
                f"binary_search_right_{iteration}"
            )
            right_metrics = self._dict_to_metrics(result_right["final_metrics"])

            # Find best
            metrics_list = [
                (left, left_metrics),
                (mid, mid_metrics),
                (right, right_metrics)
            ]
            best_point, best_point_metrics = max(
                metrics_list,
                key=lambda x: self.metrics_score(x[1])
            )

            # Update search interval
            if best_point == left:
                high = mid
            elif best_point == right:
                low = mid
            else:
                low = left
                high = right

            # Update global best
            if self.is_improvement(best_metrics, best_point_metrics):
                best_metrics = best_point_metrics
                best_value = best_point

            self.optimization_history.append({
                "iteration": iteration,
                "parameter_value": best_point,
                "metrics": best_point_metrics.to_dict(),
                "search_interval": (low, high)
            })

        self.best_value = best_value
        self.best_metrics = best_metrics
        return {"best_value": best_value, "best_metrics": best_metrics}

    def gradient_ascent_optimization(self) -> Dict[str, Any]:
        """Gradient ascent optimization - hill climbing."""
        current_value = self.config.initial_value
        best_value = current_value
        best_metrics = self.framework.qubit_manager.current_metrics

        iteration = 0
        learning_rate = self.config.step_size

        while iteration < self.config.max_iterations:
            iteration += 1

            # Calculate gradient (finite difference)
            gradient = self.calculate_gradient(current_value, learning_rate)

            if abs(gradient) < 1e-10:  # Convergence
                break

            # Update parameter value
            new_value = current_value + learning_rate * gradient
            new_value = float(np.clip(new_value, *self.config.value_range))

            # Test new value
            test_func = self.create_parameter_test(new_value)
            result = self.framework.run_test_sequence(
                [test_func],
                f"gradient_{self.config.parameter_name}_{iteration}"
            )
            new_metrics = self._dict_to_metrics(result["final_metrics"])

            # Update best
            if self.is_improvement(best_metrics, new_metrics):
                best_metrics = new_metrics
                best_value = new_value

            current_value = new_value

            self.optimization_history.append({
                "iteration": iteration,
                "parameter_value": current_value,
                "metrics": new_metrics.to_dict(),
                "gradient": gradient
            })

        self.best_value = best_value
        self.best_metrics = best_metrics
        return {"best_value": best_value, "best_metrics": best_metrics}

    def calculate_gradient(self, current_value: float, delta: float) -> float:
        """Calculate gradient using finite difference."""
        # Test current point
        test_func_current = self.create_parameter_test(current_value)
        result_current = self.framework.run_test_sequence(
            [test_func_current],
            "gradient_current"
        )
        current_score = self.metrics_score(self._dict_to_metrics(result_current["final_metrics"]))

        # Test forward point
        forward_value = current_value + delta
        if forward_value <= self.config.value_range[1]:
            test_func_forward = self.create_parameter_test(forward_value)
            result_forward = self.framework.run_test_sequence(
                [test_func_forward],
                "gradient_forward"
            )
            forward_score = self.metrics_score(self._dict_to_metrics(result_forward["final_metrics"]))
        else:
            forward_score = current_score

        gradient = (forward_score - current_score) / delta
        return gradient

    def metrics_score(self, metrics: QubitMetrics) -> float:
        """Calculate composite performance score from metrics."""
        weights = {
            'readout_fidelity': 0.4,
            'single_qubit_gate_fidelity': 0.4,
            't1': 0.2
        }
        score = (
            metrics.readout_fidelity * weights['readout_fidelity']
            + metrics.single_qubit_gate_fidelity * weights['single_qubit_gate_fidelity']
            + (metrics.t1 / 100.0) * weights['t1']
        )
        return score

    def is_improvement(self, old_metrics: QubitMetrics, new_metrics: QubitMetrics) -> bool:
        """Check if new metrics represent an improvement."""
        old_score = self.metrics_score(old_metrics)
        new_score = self.metrics_score(new_metrics)
        return new_score > old_score + self.config.improvement_threshold

    def _dict_to_metrics(self, metrics_dict: Dict[str, float]) -> QubitMetrics:
        """Convert dict to QubitMetrics."""
        if isinstance(metrics_dict, QubitMetrics):
            return metrics_dict
        return QubitMetrics(
            readout_fidelity=metrics_dict.get('readout_fidelity', 0),
            single_qubit_gate_fidelity=metrics_dict.get('single_qubit_gate_fidelity', 0),
            t1=metrics_dict.get('t1', 0)
        )

    def run_optimization(self) -> Dict[str, Any]:
        """Run the optimization with configured strategy."""
        # Select strategy
        if self.config.optimization_strategy == OptimizationStrategy.BINARY_SEARCH:
            result = self.binary_search_optimization()
        elif self.config.optimization_strategy == OptimizationStrategy.GRADIENT_ASCENT:
            result = self.gradient_ascent_optimization()
        else:
            result = self.linear_scan_optimization()

        elapsed_time = time.time()

        return {
            "best_value": result['best_value'],
            "best_metrics": result['best_metrics'].to_dict() if isinstance(result['best_metrics'], QubitMetrics) else result['best_metrics'],
            "optimization_time": elapsed_time,
            "test_count": len(self.optimization_history)
        }

    def get_optimization_report(self) -> Dict[str, Any]:
        """Get detailed optimization report."""
        return {
            "parameter_name": self.config.parameter_name,
            "optimization_strategy": self.config.optimization_strategy.value,
            "best_value": self.best_value,
            "best_metrics": self.best_metrics.to_dict() if self.best_metrics else None,
            "optimization_history": [
                {**h, "metrics": h["metrics"].to_dict() if isinstance(h.get("metrics"), QubitMetrics) else h.get("metrics")}
                for h in self.optimization_history
            ],
            "test_count": len(self.optimization_history)
        }