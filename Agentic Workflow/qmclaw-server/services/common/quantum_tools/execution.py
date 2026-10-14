# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
实验执行工具

提供实验执行相关的量子工具实现。
"""

import json
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .base import ExperimentResult, ParameterSchema, QuantumTool, ToolResult, ToolSchema
from .errors import (
    ErrorCode,
    ExperimentNotFoundError,
    ExecutionError,
    LabRADError,
    QMClawError,
    TimeoutError,
)


class RunCodeTool(QuantumTool):
    """直接执行代码工具

    直接执行 Python 代码字符串，调用 LabRAD 实验函数。
    """

    def __init__(
        self,
        executor: Callable[[str], Dict[str, Any]],
        is_connected: Callable[[], bool] = None,
        actuation_flags: Callable[[], Dict[str, bool]] = None,
        is_offline: Optional[Callable[[], bool]] = None,
    ):
        """
        Args:
            executor: 代码执行函数，签名为 (code: str) -> Dict[str, Any]
            is_connected: 连接检查函数，签名为 () -> bool
            actuation_flags: 写闸门查询，签名为 () -> {"allow_execute": bool, ...}
            is_offline: 离线模式查询，签名为 () -> bool。默认 False（按在线处理），
                即不传该参数时写闸门行为与改动前完全一致。
        """
        self._executor = executor
        self._is_connected = is_connected or (lambda: True)
        self._actuation_flags = actuation_flags or (lambda: {"allow_execute": True, "allow_set_params": True})
        # 离线豁免：与 quantum_service/server.py 的离线判定口径对齐
        # （服务端先判 `if self._is_offline_mode: return self._execute_offline_simulation(...)`，
        # 离线模拟不碰硬件、不受写闸门限制）。真机在线仍严格受闸门约束。
        self._is_offline = is_offline or (lambda: False)

    @property
    def name(self) -> str:
        return "run_code"

    @property
    def description(self) -> str:
        return """Execute Python code to run quantum experiments.
Use sq.* functions to execute experiments, e.g., sq.iqraw(), sq.t1(), sq.spectroscopy().
Returns the experiment result or error message."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "code": ParameterSchema(
                    name="code",
                    type="string",
                    description="Python code to execute. Use sq.* functions like sq.iqraw(), sq.t1().",
                    required=True,
                ),
                "timeout": ParameterSchema(
                    name="timeout",
                    type="number",
                    description="Maximum execution time in seconds",
                    required=False,
                    default=300,
                    minimum=1,
                    maximum=3600,
                ),
            },
        )

    def execute(self, code: str, timeout: int = 300, **kwargs) -> ToolResult[Dict[str, Any]]:
        """执行代码"""
        # 写闸门：仅在非离线时强制。离线模拟不碰硬件，与服务端
        # quantum_service/server.py 的离线优先判定保持同一口径；
        # 真机在线（_is_offline() 为 False）闸门文案/错误码/status 一字未改。
        if not self._actuation_flags().get("allow_execute", True) and not self._is_offline():
            return ToolResult.err(
                "当前为只读联调，禁止向真机下发实验",
                error_code=ErrorCode.FORBIDDEN.value,
                hint="服务端写闸门 actuation.allow_execute 已关闭。只读阶段仅允许查询；"
                     "如需执行请人工翻开闸门并记录。",
                status="read_only",
                actuation_allowed=False,
            )

        if not self._is_connected():
            return ToolResult.err(
                "Not connected to LabRAD",
                error_code=ErrorCode.LABRAD_ERROR.value,
                hint="Call connect() first or check if LabRAD server is running",
            )

        if not code or not code.strip():
            return ToolResult.err(
                "Code cannot be empty",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        start_time = time.time()

        try:
            result = self._executor(code)
            elapsed = time.time() - start_time

            # 服务端结构化拒绝（只读/卡死）原样透传，保留 hint 供 agent 解释
            if isinstance(result, dict) and result.get("status") in ("read_only", "stuck"):
                return ToolResult.err(
                    result.get("error") or result.get("status"),
                    error_code=ErrorCode.VALIDATION_ERROR.value,
                    hint=result.get("hint"),
                    status=result.get("status"),
                    actuation_allowed=result.get("actuation_allowed"),
                    stuck=result.get("stuck"),
                )

            return ToolResult.ok(
                data={
                    "result": result,
                    "elapsed_seconds": elapsed,
                },
                execution_time=elapsed,
                source="labrad",
            )

        except TimeoutError:
            return ToolResult.err(
                f"Execution timed out after {timeout}s",
                error_code=ErrorCode.TIMEOUT.value,
                timeout=timeout,
            )

        except SyntaxError as e:
            return ToolResult.err(
                f"Syntax error: {e}",
                error_code=ErrorCode.EXECUTION_ERROR.value,
                code=code,
                line=e.lineno,
                hint="Check the Python syntax",
            )

        except NameError as e:
            return ToolResult.err(
                f"Name error: {e}",
                error_code=ErrorCode.EXECUTION_ERROR.value,
                code=code,
                hint="Check variable and function names",
            )

        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.EXECUTION_ERROR.value,
                traceback=traceback.format_exc(),
            )


class RunExperimentTool(QuantumTool):
    """执行命名实验工具

    通过实验名称和参数执行预定义的量子实验。
    """

    def __init__(
        self,
        code_builder: Callable[[str, Dict[str, Any]], str],
        executor: Callable[[str], Dict[str, Any]],
        schema_getter: Callable[[str], Optional[Dict[str, Any]]] = None,
        is_connected: Callable[[], bool] = None,
        actuation_flags: Callable[[], Dict[str, bool]] = None,
        is_offline: Optional[Callable[[], bool]] = None,
    ):
        """
        Args:
            code_builder: 代码构建函数，签名为 (name: str, params: Dict) -> str
            executor: 代码执行函数，签名为 (code: str) -> Dict[str, Any]
            schema_getter: 实验 schema 获取函数，签名为 (name: str) -> Optional[Dict]
            is_connected: 连接检查函数，签名为 () -> bool
            actuation_flags: 写闸门查询，签名为 () -> {"allow_execute": bool, ...}
            is_offline: 离线模式查询，签名为 () -> bool。默认 False（按在线处理），
                即不传该参数时写闸门行为与改动前完全一致。
        """
        self._code_builder = code_builder
        self._executor = executor
        self._schema_getter = schema_getter
        self._is_connected = is_connected or (lambda: True)
        self._actuation_flags = actuation_flags or (lambda: {"allow_execute": True, "allow_set_params": True})
        # 离线豁免：与 quantum_service/server.py 的离线判定口径对齐
        # （服务端先判 `if self._is_offline_mode: return self._execute_offline_simulation(...)`，
        # 离线模拟不碰硬件、不受写闸门限制）。真机在线仍严格受闸门约束。
        self._is_offline = is_offline or (lambda: False)

    @property
    def name(self) -> str:
        return "run_experiment"

    @property
    def description(self) -> str:
        return """Execute a quantum experiment by name.
Common experiments: t1, ramsey_df, spectroscopy, iqraw, piamp, single_shot.
Returns experiment results including measured data and parameters."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "experiment_name": ParameterSchema(
                    name="experiment_name",
                    type="string",
                    description="Experiment name (e.g., 't1', 'ramsey_df', 'spectroscopy')",
                    required=True,
                ),
                "params": ParameterSchema(
                    name="params",
                    type="object",
                    description="Experiment parameters as key-value pairs",
                    required=False,
                    default={},
                ),
                "target": ParameterSchema(
                    name="target",
                    type="string",
                    description="Target qubit name (e.g., 'q10lu1')",
                    required=False,
                ),
                "notes": ParameterSchema(
                    name="notes",
                    type="string",
                    description="Optional notes about this experiment run",
                    required=False,
                    default="",
                ),
                "timeout": ParameterSchema(
                    name="timeout",
                    type="number",
                    description="Maximum execution time in seconds",
                    required=False,
                    default=300,
                    minimum=1,
                    maximum=3600,
                ),
            },
        )

    def execute(
        self,
        experiment_name: str,
        params: Dict[str, Any] = None,
        target: str = None,
        notes: str = "",
        timeout: int = 300,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """执行实验"""
        if not experiment_name:
            return ToolResult.err(
                "experiment_name is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        if not self._actuation_flags().get("allow_execute", True) and not self._is_offline():
            return ToolResult.err(
                "当前为只读联调，禁止向真机下发实验",
                error_code=ErrorCode.FORBIDDEN.value,
                hint="服务端写闸门 actuation.allow_execute 已关闭。只读阶段仅允许查询；"
                     "如需执行请人工翻开闸门并记录。",
                status="read_only",
                actuation_allowed=False,
            )

        if not self._is_connected():
            return ToolResult.err(
                "Not connected to LabRAD",
                error_code=ErrorCode.LABRAD_ERROR.value,
            )

        params = params or {}

        # 如果指定了 target，添加到 params
        if target:
            params["target"] = target

        start_time = time.time()
        now = datetime.now(timezone.utc)
        exp_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{experiment_name}"

        try:
            # 构建代码
            code = self._code_builder(experiment_name, params)

            # 执行
            result = self._executor(code)
            elapsed = time.time() - start_time

            # 服务端结构化拒绝（只读/卡死）原样透传
            if isinstance(result, dict) and result.get("status") in ("read_only", "stuck"):
                return ToolResult.err(
                    result.get("error") or result.get("status"),
                    error_code=ErrorCode.FORBIDDEN.value,
                    hint=result.get("hint"),
                    status=result.get("status"),
                    actuation_allowed=result.get("actuation_allowed"),
                    stuck=result.get("stuck"),
                )

            # 解析结果
            # 离线模式返回 status="offline_simulated"，与在线 "success" 一样视为执行成功。
            # 原实现只认 "success"，导致离线跑通的实验被报成 failed。
            status = result.get("status", "failed")
            success = status in ("success", "offline_simulated")

            # 结果字段：在线走 result，离线走 metrics
            if result.get("result"):
                results = result["result"]
            else:
                results = result.get("metrics") or {}

            # 曲线数据 -> arrays；绘图路径 -> plots（沿用 ExperimentResult 既有字段）
            offline_data = result.get("data") or {}
            arrays: Dict[str, List[float]] = {}
            if isinstance(offline_data, dict) and offline_data.get("x") is not None:
                arrays = {
                    "x": offline_data.get("x", []),
                    "y": offline_data.get("y", []),
                }
            plots: List[Dict[str, Any]] = []
            if result.get("plotPath"):
                plots = [{
                    "path": result["plotPath"],
                    "title": result.get("exp_type", experiment_name),
                }]

            # 离线链路命中的历史数据集：模拟器已经产出（offline_simulator.simulate
            # 里 result["dataset_id"] / result["source_data"]），quantum_service 也
            # 原样透传了，但这里重建 ExperimentResult 时会丢，必须显式带回 ——
            # 上层要靠它去 analysis_service 复现 experiments 页的那张图。
            dataset_id = result.get("dataset_id")
            source_data = result.get("source_data")
            synthetic = bool(result.get("synthetic"))

            experiment_result = ExperimentResult(
                id=exp_id,
                experiment_name=experiment_name,
                status="success" if success else "failed",
                started_at=now.isoformat().replace("+00:00", "Z"),
                completed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                target=result.get("qubit") or target,
                params=params,
                results=results,
                arrays=arrays,
                plots=plots,
                error=result.get("error"),
                notes=notes,
                dataset_id=dataset_id,
                source_data=source_data,
                synthetic=synthetic,
            )

            return ToolResult.ok(
                data=experiment_result.to_dict(),
                execution_time=elapsed,
                source="offline" if result.get("simulated") else "labrad",
                simulated=bool(result.get("simulated")),
                synthetic=synthetic,
                dataset_id=dataset_id,
                source_data=source_data,
                stdout=result.get("stdout", ""),
                code=code,
            )

        except QMClawError:
            raise
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.EXECUTION_ERROR.value,
                experiment_name=experiment_name,
            )


class ListExperimentsTool(QuantumTool):
    """列出实验工具"""

    def __init__(
        self,
        lister: Callable[[], List[Dict[str, str]]],
    ):
        """
        Args:
            lister: 实验列表获取函数，签名为 () -> List[Dict]
        """
        self._lister = lister

    @property
    def name(self) -> str:
        return "list_experiments"

    @property
    def description(self) -> str:
        return "List all available quantum experiments. Returns experiment names and descriptions."

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "filter": ParameterSchema(
                    name="filter",
                    type="string",
                    description="Optional filter to search experiment names",
                    required=False,
                ),
            },
        )

    def execute(self, filter: str = None, **kwargs) -> ToolResult[List[Dict[str, Any]]]:
        """列出实验"""
        try:
            experiments = self._lister()

            if filter:
                filter_lower = filter.lower()
                experiments = [
                    e for e in experiments
                    if filter_lower in e.get("name", "").lower()
                    or filter_lower in e.get("doc", "").lower()
                ]

            return ToolResult.ok(data=experiments)

        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )


class GetExperimentSchemaTool(QuantumTool):
    """获取实验 Schema 工具"""

    def __init__(
        self,
        schema_getter: Callable[[str], Optional[Dict[str, Any]]],
        experiment_lister: Callable[[], List[Dict[str, str]]] = None,
    ):
        """
        Args:
            schema_getter: schema 获取函数，签名为 (name: str) -> Optional[Dict]
            experiment_lister: 实验列表获取函数，签名为 () -> List[Dict]
        """
        self._schema_getter = schema_getter
        self._experiment_lister = experiment_lister

    @property
    def name(self) -> str:
        return "get_experiment_schema"

    @property
    def description(self) -> str:
        return "Get the parameter schema for a specific experiment."

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "experiment_name": ParameterSchema(
                    name="experiment_name",
                    type="string",
                    description="Experiment name to get schema for",
                    required=True,
                ),
            },
        )

    def execute(self, experiment_name: str, **kwargs) -> ToolResult[Dict[str, Any]]:
        """获取实验 schema"""
        if not experiment_name:
            return ToolResult.err(
                "experiment_name is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        schema = self._schema_getter(experiment_name)

        if schema is None:
            # 尝试从实验列表获取
            available = []
            if self._experiment_lister:
                available = [e.get("name", "") for e in self._experiment_lister()]

            return ToolResult.err(
                f"Experiment '{experiment_name}' not found",
                error_code=ErrorCode.EXPERIMENT_NOT_FOUND.value,
                available_experiments=available,
                hint="Call list_experiments() to see available experiments",
            )

        return ToolResult.ok(data=schema)
