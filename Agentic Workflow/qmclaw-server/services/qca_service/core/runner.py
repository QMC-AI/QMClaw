# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""QMClaw-specific experiment runner using quantum_service API."""

import json
from pathlib import Path
from typing import Any, Optional


# QMClaw quantum service URL
QUANTUM_SERVICE_URL = "http://localhost:3003"


def run_experiment(
    name: str,
    params: dict,
    scripts_dir: Path,
    timeout: int = 300,
) -> dict:
    """Run an experiment via QMClaw quantum_service.

    Args:
        name: Experiment name (function name)
        params: Parameters to pass to experiment
        scripts_dir: Directory containing experiment scripts
        timeout: Timeout in seconds (default 300)

    Returns:
        Result dictionary with status, results, arrays, plots, metadata

    Raises:
        ValueError: If experiment not found or validation fails
        RuntimeError: If execution fails
    """
    # Import discovery here to avoid circular imports
    from .discovery import get_experiment_schema
    from .models import ExperimentSchema, ParameterSpec

    # Get experiment schema
    schema = get_experiment_schema(name, scripts_dir)
    if schema is None:
        raise ValueError(f"Experiment not found: {name}")

    # Validate required parameters
    for param in schema.parameters:
        if param.required and param.name not in params:
            raise ValueError(f"Missing required parameter: {param.name}")

    # Build the experiment code for QMClaw
    # QMClaw uses sq.* functions (e.g., sq.t1, sq.spectroscopy)
    code = _build_experiment_code(name, params)

    # Execute via quantum_service
    try:
        import urllib.request
        import urllib.error

        # 阶段 16a：/execute 默认异步，这里要的是结果，显式 wait
        payload = {"code": code, "wait": True}
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            f"{QUANTUM_SERVICE_URL}/execute",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))

            # 阶段 6：quantum_service 的失败状态统一为 "failed"（旧值 "error" 仍兼容）
            if result.get("status") in ("error", "failed"):
                raise RuntimeError(result.get("error", "Unknown error"))

            return _parse_result(result)

    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to connect to quantum_service: {e}")


def _build_experiment_code(experiment_name: str, params: dict) -> str:
    """Build experiment code from name and params."""
    param_parts = []
    for key, value in params.items():
        if isinstance(value, str):
            param_parts.append(f"{key}='{value}'")
        else:
            param_parts.append(f"{key}={value}")

    params_str = ", ".join(param_parts)

    # Map experiment names to QMClaw functions
    # t1_measurement -> sq.t1
    # ramsey_measurement -> sq.ramsey_df
    # spectroscopy -> sq.spectroscopy
    func_map = {
        "t1_measurement": "sq.t1",
        "ramsey_measurement": "sq.ramsey_df",
        "spectroscopy": "sq.spectroscopy",
    }

    func_name = func_map.get(experiment_name, f"sq.{experiment_name}")
    return f"{func_name}({params_str})"


def _parse_result(result: dict) -> dict:
    """Parse execution result into standard format."""
    parsed = {
        "status": "success" if result.get("success", True) else "failed",
        "results": {},
        "arrays": {},
        "plots": [],
    }

    # Extract results if available
    if "result" in result:
        res = result["result"]
        if isinstance(res, dict):
            parsed["results"] = res

    # Extract stdout/stderr
    if "stdout" in result:
        parsed["stdout"] = result["stdout"]
    if "stderr" in result:
        parsed["stderr"] = result["stderr"]

    if "error" in result:
        parsed["error"] = result["error"]
        parsed["status"] = "failed"

    return parsed


# Re-export from vendor's discovery and models
from .discovery import discover_experiments, get_experiment_schema
from .models import ExperimentSchema, ParameterSpec, ExperimentResult
