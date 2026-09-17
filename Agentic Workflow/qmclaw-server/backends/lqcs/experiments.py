"""
backends/lqcs/experiments.py - Experiment module wrapper for LQCS backend.

Provides a clean interface to sq.* experiment functions while maintaining
backward compatibility with the existing API.
"""

from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class ExperimentConfig:
    """Configuration for an experiment."""

    name: str
    description: str
    function: str  # e.g., 'sq.spectroscopy'
    default_params: Dict[str, Any]
    metrics_to_extract: List[str]


class ExperimentModule:
    """Wrapper for the sq experiment module.

    Provides a consistent interface to experiment functions while
    allowing for middleware like caching, metrics collection, etc.
    """

    # Known experiments with their default configurations
    EXPERIMENTS: Dict[str, ExperimentConfig] = {
        "spectroscopy": ExperimentConfig(
            name="Spectroscopy",
            description="VNA spectroscopy - broad frequency scan to find qubit resonance",
            function="sq.spectroscopy",
            default_params={"spec_amp": 1, "update": False, "do_plot": True},
            metrics_to_extract=["f10"],
        ),
        "s21": ExperimentConfig(
            name="Cavity S21",
            description="Cavity S21 - narrowband frequency scan around cavity resonance",
            function="sq.s21",
            default_params={"update": False, "do_plot": True},
            metrics_to_extract=["fread"],
        ),
        "iqraw": ExperimentConfig(
            name="IQ Raw",
            description="Acquire raw I/Q data for qubit state discrimination",
            function="sq.iqraw",
            default_params={"do_plot": True},
            metrics_to_extract=["SNR", "F0", "F1", "separation"],
        ),
        "t1": ExperimentConfig(
            name="T1 Relaxation",
            description="Measure qubit relaxation time via variable delay pulse sequence",
            function="sq.t1",
            default_params={"zpa": 0, "do_plot": True},
            metrics_to_extract=["T1"],
        ),
        "ramsey_df": ExperimentConfig(
            name="Ramsey with Detuning",
            description="Ramsey with detuning - measure T2* dephasing time",
            function="sq.ramsey_df",
            default_params={"do_plot": True},
            metrics_to_extract=["T2", "detuning", "f10"],
        ),
        "piamp": ExperimentConfig(
            name="Pi Pulse Amplitude",
            description="Calibrate pi-pulse amplitude for X gate via Rabi oscillation",
            function="sq.piamp",
            default_params={"amp": 3, "update": False, "do_plot": True},
            metrics_to_extract=["pi_amplitude"],
        ),
        "xeb": ExperimentConfig(
            name="Cross-Entropy Benchmarking",
            description="Measure single-qubit gate fidelity",
            function="sq.xeb",
            default_params={"do_plot": True},
            metrics_to_extract=["gate_fidelity", "error_per_cycle"],
        ),
        "s21_dis": ExperimentConfig(
            name="S21 Dispersive Shift",
            description="Measure cavity transmission shift vs qubit state",
            function="sq.s21_dis",
            default_params={"do_plot": True, "update": False},
            metrics_to_extract=["dispersive_shift"],
        ),
        "allxy": ExperimentConfig(
            name="AllXY",
            description="Characterize all 21 gate error combinations",
            function="sq.allxy",
            default_params={"do_plot": True},
            metrics_to_extract=["average_fidelity"],
        ),
        "single_shot": ExperimentConfig(
            name="Single-shot Fidelity",
            description="Measure qubit readout fidelity in single-shot regime",
            function="sq.single_shot",
            default_params={"do_plot": True},
            metrics_to_extract=["readout_fidelity"],
        ),
        "pulsed_spec": ExperimentConfig(
            name="Pulsed Spectroscopy",
            description="Qubit spectroscopy with pump pulse for higher SNR",
            function="sq.pulsed_spec",
            default_params={"do_plot": True},
            metrics_to_extract=["qubit_frequency"],
        ),
        "swap": ExperimentConfig(
            name="SWAP",
            description="Characterize SWAP gate for two-qubit operations",
            function="sq.swap",
            default_params={"do_plot": True},
            metrics_to_extract=["swap_fidelity"],
        ),
        "drag_calibrate": ExperimentConfig(
            name="DRAG Calibration",
            description="Optimize DRAG coefficient for leakage suppression",
            function="sq.drag_calibrate",
            default_params={"do_plot": True},
            metrics_to_extract=["optimal_drag"],
        ),
        "cr_calibrate": ExperimentConfig(
            name="CR Calibration",
            description="Cross-resonance gate calibration",
            function="sq.cr_calibrate",
            default_params={"do_plot": True},
            metrics_to_extract=["cr_fidelity"],
        ),
        "cz_calibrate": ExperimentConfig(
            name="CZ Calibration",
            description="CZ gate calibration",
            function="sq.cz_calibrate",
            default_params={"do_plot": True},
            metrics_to_extract=["cz_fidelity"],
        ),
        "set_pi": ExperimentConfig(
            name="Set Pi Pulse",
            description="Set pi pulse parameters",
            function="sq.set_pi",
            default_params={"gate": "X", "do_plot": True},
            metrics_to_extract=[],
        ),
        "pidf": ExperimentConfig(
            name="PID Feedback",
            description="PID-based qubit parameter feedback",
            function="sq.pidf",
            default_params={"do_plot": True},
            metrics_to_extract=[],
        ),
    }

    def __init__(self, sq_module: Any):
        """Initialize with the actual sq module.

        Args:
            sq_module: The lqms sq_nodes module
        """
        self._sq = sq_module

    def __getattr__(self, name: str) -> Callable:
        """Get experiment function by name.

        Delegates to the underlying sq module while providing
        a consistent interface.
        """
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

        if hasattr(self._sq, name):
            return getattr(self._sq, name)

        raise AttributeError(f"Experiment '{name}' not found in sq module")

    def list_experiments(self) -> List[str]:
        """List all available experiment names."""
        return [
            name
            for name in dir(self._sq)
            if not name.startswith("_") and callable(getattr(self._sq, name))
        ]

    def get_experiment_info(self, name: str) -> Optional[ExperimentConfig]:
        """Get configuration info for an experiment."""
        return self.EXPERIMENTS.get(name)

    def get_all_experiments(self) -> Dict[str, ExperimentConfig]:
        """Get all experiment configurations."""
        return self.EXPERIMENTS

    def run(self, name: str, qubit: Any, **params) -> Any:
        """Run an experiment by name with parameters.

        Args:
            name: Experiment name (e.g., 'spectroscopy')
            qubit: Qubit object
            **params: Experiment parameters

        Returns:
            Experiment result (typically None, plot is created)
        """
        if not hasattr(self._sq, name):
            raise ValueError(f"Unknown experiment: {name}")

        func = getattr(self._sq, name)
        return func(qubit, **params)

    def get_experiment_signature(self, name: str) -> Optional[str]:
        """Get experiment function signature as string."""
        if not hasattr(self._sq, name):
            return None
        func = getattr(self._sq, name)
        import inspect

        try:
            sig = inspect.signature(func)
            return f"sq.{name}{sig}"
        except (ValueError, TypeError):
            return f"sq.{name}(...)"
