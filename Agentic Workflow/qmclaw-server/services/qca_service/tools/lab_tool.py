# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""QCA Service tools - QMClaw-specific implementation.

This module provides tools for the Deep Agent that use QMClaw's quantum_service
instead of the vendor's subprocess execution.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import tool

# Add vendor directory to path for imports
# __file__ = .../services/qca_service/tools/lab_tool.py → repo root is parents[5]
import sys
VENDOR_DIR = Path(__file__).resolve().parents[5] / "vendor" / "Quantum-Calibration-Agent-Blueprint"
if not VENDOR_DIR.exists():
    raise RuntimeError(
        f"vendor QCA not found: {VENDOR_DIR} — "
        r"copy deploy\vendor\Quantum-Calibration-Agent-Blueprint to QMClaw\vendor\ first"
    )
if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

# Import vendor's core modules for discovery and models
from core import discovery, models
from core.models import ExperimentSchema, ParameterSpec, ExperimentResult

# QMClaw-specific imports
from .runner import run_experiment as qca_run_experiment

# Paths - use vendor's directories
VENDOR_DATA_DIR = VENDOR_DIR / "data"
VENDOR_SCRIPTS_DIR = VENDOR_DIR / "scripts"


def _get_dirs() -> tuple[Path, Path]:
    """Get scripts and data directories."""
    return VENDOR_SCRIPTS_DIR, VENDOR_DATA_DIR


@tool
def lab(
    action: str,
    experiment_name: Optional[str] = None,
    experiment_id: Optional[str] = None,
    array_name: Optional[str] = None,
    slice_start: Optional[int] = None,
    slice_end: Optional[int] = None,
    last_n: Optional[int] = None,
    filter_type: Optional[str] = None,
) -> dict[str, Any]:
    """Quantum calibration query tool. Get experiment info, history, and data.

    Actions:
    - info: Get full documentation including all experiments (START HERE)
    - list_experiments: List all available experiments
    - schema: Get detailed schema for a specific experiment
    - history_list: List past experiments
    - history_show: Get experiment details
    - list_arrays: List available arrays in an experiment
    - get_array: Get array data
    - get_stats: Get array statistics
    """
    scripts_dir, data_dir = _get_dirs()

    try:
        if action == "info":
            return _get_info()
        elif action == "list_experiments":
            return _list_experiments()
        elif action == "schema":
            return _get_schema(experiment_name)
        elif action == "history_list":
            return _history_list(last_n, filter_type)
        elif action == "history_show":
            return _history_show(experiment_id)
        elif action == "list_arrays":
            return _list_arrays(experiment_id)
        elif action == "get_array":
            return _get_array(experiment_id, array_name, slice_start, slice_end)
        elif action == "get_stats":
            return _get_stats(experiment_id, array_name)
        elif action == "run":
            return {"error": "Use the run_experiment tool to execute experiments"}
        else:
            return {
                "error": f"Unknown action: {action}",
                "valid_actions": [
                    "info",
                    "schema",
                    "history_list",
                    "history_show",
                    "list_arrays",
                    "get_array",
                    "get_stats",
                ],
            }
    except Exception as e:
        return {"error": str(e)}


@tool
def run_experiment(
    experiment_name: str,
    params: Optional[dict] = None,
    notes: str = "",
) -> dict[str, Any]:
    """Execute a quantum calibration experiment.

    Args:
        experiment_name: Name of the experiment to run
        params: Dictionary of experiment parameters
        notes: Optional notes to attach to the experiment run

    Returns experiment results including ID, status, and any generated data.
    """
    scripts_dir, data_dir = _get_dirs()
    return _run_experiment(experiment_name, params, notes, scripts_dir, data_dir)


def _get_info() -> dict:
    """Get comprehensive info: all experiments with full parameter schemas."""
    scripts_dir, _ = _get_dirs()
    experiments = discovery.discover_experiments(scripts_dir)

    experiment_docs = []
    for exp in experiments:
        params = []
        for p in exp.parameters:
            param_doc = {
                "name": p.name,
                "type": p.type,
                "default": p.default,
            }
            if p.range:
                param_doc["range"] = f"{p.range[0]} to {p.range[1]}"
            if p.required:
                param_doc["required"] = True
            params.append(param_doc)

        experiment_docs.append({
            "name": exp.name,
            "description": exp.description,
            "parameters": params,
        })

    return {
        "experiments": experiment_docs,
        "usage": {
            "run_experiment": "run_experiment(experiment_name='NAME', params={...})",
            "view_history": "lab(action='history_list', last_n=10)",
            "get_results": "lab(action='history_show', experiment_id='ID')",
            "get_data": "lab(action='get_array', experiment_id='ID', array_name='NAME')",
        },
    }


def _list_experiments() -> dict:
    """List all available experiments."""
    scripts_dir, _ = _get_dirs()
    experiments = discovery.discover_experiments(scripts_dir)
    return {
        "experiments": [
            {
                "name": exp.name,
                "description": exp.description,
                "parameter_count": len(exp.parameters),
            }
            for exp in experiments
        ]
    }


def _get_schema(experiment_name: Optional[str]) -> dict:
    """Get schema for a specific experiment."""
    scripts_dir, _ = _get_dirs()

    if not experiment_name:
        return {"error": "experiment_name is required for schema action"}

    schema = discovery.get_experiment_schema(experiment_name, scripts_dir)
    if not schema:
        available = discovery.discover_experiments(scripts_dir)
        return {
            "error": f"Experiment '{experiment_name}' not found",
            "available_experiments": [e.name for e in available],
        }

    return {
        "name": schema.name,
        "description": schema.description,
        "module_path": schema.module_path,
        "parameters": [
            {
                "name": p.name,
                "type": p.type,
                "required": p.required,
                "default": p.default,
                "range": list(p.range) if p.range else None,
            }
            for p in schema.parameters
        ],
    }


def _run_experiment(
    experiment_name: str,
    params: Optional[dict],
    notes: str,
    scripts_dir: Path,
    data_dir: Path,
) -> dict:
    """Run an experiment and store results."""
    if not experiment_name:
        return {"error": "experiment_name is required for run action"}
    if params is None:
        params = {}

    # Validate experiment exists
    schema = discovery.get_experiment_schema(experiment_name, scripts_dir)
    if not schema:
        available = discovery.discover_experiments(scripts_dir)
        return {
            "error": f"Experiment '{experiment_name}' not found",
            "available_experiments": [e.name for e in available],
        }

    # Generate experiment ID and timestamp
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat().replace("+00:00", "Z")
    exp_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{experiment_name}"

    # Run experiment using QMClaw quantum_service
    try:
        result_dict = qca_run_experiment(
            experiment_name,
            params,
            scripts_dir,
        )
    except ValueError as e:
        return {
            "error": str(e),
            "hint": f"Call lab(action='schema', experiment_name='{experiment_name}') to see valid parameters",
        }
    except Exception as e:
        return {"error": f"Experiment failed: {e}", "id": exp_id}

    # Extract target from params
    target = params.get("target")

    # Create ExperimentResult
    results_data = result_dict.get("results") or result_dict.get("data", {})
    experiment_result = ExperimentResult(
        id=exp_id,
        type=experiment_name,
        timestamp=timestamp,
        status=result_dict["status"],
        target=target,
        params=params,
        results=results_data,
        arrays=result_dict.get("arrays", {}),
        plots=result_dict.get("plots", []),
        notes=notes,
    )

    # Store results using vendor's storage module
    try:
        from core import storage
        storage.save_experiment(experiment_result, data_dir)
    except Exception as e:
        print(f"[QCA] Warning: Failed to save experiment: {e}")

    # Return summary
    return {
        "id": exp_id,
        "status": result_dict["status"],
        "timestamp": timestamp,
        "results": results_data,
        "arrays": list(result_dict.get("arrays", {}).keys()),
        "plots": [p["name"] for p in result_dict.get("plots", [])],
        "error": result_dict.get("error") if result_dict["status"] == "failed" else None,
    }


def _history_list(last_n: Optional[int] = None, filter_type: Optional[str] = None) -> dict:
    """List past experiments."""
    _, data_dir = _get_dirs()

    try:
        from core import storage
        experiments = storage.search_experiments(data_dir, type=filter_type, last=last_n)
        return {
            "count": len(experiments),
            "experiments": [
                {
                    "id": exp["id"],
                    "type": exp["type"],
                    "target": exp.get("target"),
                    "timestamp": exp["timestamp"],
                    "status": exp["status"],
                }
                for exp in experiments
            ],
        }
    except Exception as e:
        return {"error": str(e), "experiments": []}


def _history_show(experiment_id: Optional[str]) -> dict:
    """Get full details of an experiment."""
    _, data_dir = _get_dirs()

    if not experiment_id:
        return {"error": "experiment_id is required for history_show action"}

    try:
        from core import storage
        experiment = storage.load_experiment(experiment_id, data_dir)
        if not experiment:
            return {"error": f"Experiment '{experiment_id}' not found"}
        return experiment.to_dict()
    except Exception as e:
        return {"error": str(e)}


def _list_arrays(experiment_id: Optional[str]) -> dict:
    """List arrays in an experiment."""
    _, data_dir = _get_dirs()

    if not experiment_id:
        return {"error": "experiment_id is required for list_arrays action"}

    try:
        from core import storage
        arrays = storage.list_arrays(experiment_id, data_dir)
        if arrays is None:
            return {"error": f"Experiment '{experiment_id}' not found"}
        return {"arrays": arrays}
    except Exception as e:
        return {"error": str(e)}


def _get_array(
    experiment_id: Optional[str],
    array_name: Optional[str],
    slice_start: Optional[int] = None,
    slice_end: Optional[int] = None,
) -> dict:
    """Get array data with optional slicing."""
    _, data_dir = _get_dirs()

    if not experiment_id:
        return {"error": "experiment_id is required for get_array action"}
    if not array_name:
        return {"error": "array_name is required for get_array action"}

    try:
        from core import storage
        data = storage.get_array(
            experiment_id, array_name, data_dir, start=slice_start, end=slice_end
        )
        if data is None:
            arrays = storage.list_arrays(experiment_id, data_dir)
            if arrays is None:
                return {"error": f"Experiment '{experiment_id}' not found"}
            return {
                "error": f"Array '{array_name}' not found in experiment",
                "available_arrays": [a["name"] for a in arrays],
            }

        return {
            "array": array_name,
            "data": data,
            "length": len(data),
            "slice": (
                f"{slice_start or 0}:{slice_end or len(data)}"
                if slice_start or slice_end
                else "full"
            ),
        }
    except Exception as e:
        return {"error": str(e)}


def _get_stats(experiment_id: Optional[str], array_name: Optional[str]) -> dict:
    """Get statistics for an array."""
    _, data_dir = _get_dirs()

    if not experiment_id:
        return {"error": "experiment_id is required for get_stats action"}
    if not array_name:
        return {"error": "array_name is required for get_stats action"}

    try:
        from core import storage
        stats = storage.get_array_stats(experiment_id, array_name, data_dir)
        if stats is None:
            arrays = storage.list_arrays(experiment_id, data_dir)
            if arrays is None:
                return {"error": f"Experiment '{experiment_id}' not found"}
            return {
                "error": f"Array '{array_name}' not found in experiment",
                "available_arrays": [a["name"] for a in arrays],
            }
        return {"array": array_name, "stats": stats}
    except Exception as e:
        return {"error": str(e)}


# Export tools for use by the agent
__all__ = ["lab", "run_experiment"]
