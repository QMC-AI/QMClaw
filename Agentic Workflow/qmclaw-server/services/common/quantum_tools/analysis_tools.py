# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Analysis 工具

提供数据分析和可视化相关的 MCP 工具。
通过 HTTP 调用 Analysis Service 执行分析操作。
"""

import json
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from .base import ParameterSchema, QuantumTool, ToolResult, ToolSchema
from .errors import ErrorCode


def _http_call(url: str, data: dict = None, method: str = "GET") -> dict:
    """发送 HTTP 请求"""
    import urllib.request
    import urllib.error

    try:
        if method == "GET":
            req = urllib.request.Request(url)
        else:
            payload = json.dumps(data or {}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method=method
            )

        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
            return {"error": error_body.get("error", f"HTTP {e.code}: {e.reason}")}
        except Exception:
            return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


class PlotOfflineTool(QuantumTool):
    """绘制离线数据图表工具"""

    def __init__(
        self,
        plot_func: Callable[[str, str], Dict[str, Any]],
    ):
        """
        Args:
            plot_func: 绘图函数，签名为 (dataset_id: str, command: str) -> Dict
        """
        self._plot_func = plot_func

    @property
    def name(self) -> str:
        return "analysis_plot_offline"

    @property
    def description(self) -> str:
        return """Plot offline experimental data.

RETURNS: JSON object with 'image' field containing base64-encoded PNG image
or 'image_url' field containing plot file path (e.g., '/plots/xxx.png').
The frontend will automatically display the image when these fields are present.

Example response:
  {"success": true, "image": "data:image/png;base64,...", "dataset_id": "xxx"}
  {"success": true, "image_url": "/plots/temp_abc123.png"}"""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "dataset_id": ParameterSchema(
                    name="dataset_id",
                    type="string",
                    description="Dataset ID to plot",
                    required=True,
                ),
                "command": ParameterSchema(
                    name="command",
                    type="string",
                    description="Matplotlib command for custom plotting (e.g., 'plt.plot(x, y)')",
                    required=False,
                    default="",
                ),
            },
        )

    def execute(
        self,
        dataset_id: str,
        command: str = "",
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """绘制离线数据"""
        if not dataset_id:
            return ToolResult.err(
                "dataset_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._plot_func(dataset_id, command)
            return ToolResult.ok(data=result)
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.EXECUTION_ERROR.value,
                dataset_id=dataset_id,
            )


class GetStatsTool(QuantumTool):
    """获取数据集统计信息工具"""

    def __init__(
        self,
        stats_func: Callable[[Optional[int], Optional[str]], Dict[str, Any]],
    ):
        """
        Args:
            stats_func: 统计获取函数，签名为 (dataset_index: int, axis: str) -> Dict
        """
        self._stats_func = stats_func

    @property
    def name(self) -> str:
        return "analysis_get_stats"

    @property
    def description(self) -> str:
        return """Get statistical summary (mean, std, min, max) for a dataset.
Returns statistics for x and y axes."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "dataset_index": ParameterSchema(
                    name="dataset_index",
                    type="number",
                    description="Dataset index to analyze",
                    required=False,
                ),
                "axis": ParameterSchema(
                    name="axis",
                    type="string",
                    description="Axis to analyze ('x', 'y', or 'both')",
                    required=False,
                    default="both",
                    enum=["x", "y", "both"],
                ),
            },
        )

    def execute(
        self,
        dataset_index: int = 0,
        axis: str = "both",
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """获取统计数据"""
        try:
            result = self._stats_func(dataset_index, axis)
            return ToolResult.ok(data=result)
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.EXECUTION_ERROR.value,
            )


class GenerateVariantTool(QuantumTool):
    """生成数据变体工具"""

    def __init__(
        self,
        variant_func: Callable[[str, str, Dict, Optional[int]], Dict[str, Any]],
    ):
        """
        Args:
            variant_func: 变体生成函数，签名为 (source_dataset_id, variant_type, params, seed) -> Dict
        """
        self._variant_func = variant_func

    @property
    def name(self) -> str:
        return "analysis_generate_variant"

    @property
    def description(self) -> str:
        return """Generate a variant of existing data for analysis purposes.
Useful for data augmentation or simulation."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "source_dataset_id": ParameterSchema(
                    name="source_dataset_id",
                    type="string",
                    description="Source dataset ID to generate variant from",
                    required=True,
                ),
                "variant_type": ParameterSchema(
                    name="variant_type",
                    type="string",
                    description="Type of variant: 'noise', 'shift', 'scale', 'jitter', 'permute'",
                    required=True,
                    enum=["noise", "shift", "scale", "jitter", "permute"],
                ),
                "params": ParameterSchema(
                    name="params",
                    type="object",
                    description="Variant generation parameters",
                    required=False,
                    default={},
                ),
                "seed": ParameterSchema(
                    name="seed",
                    type="number",
                    description="Random seed for reproducibility",
                    required=False,
                ),
            },
        )

    def execute(
        self,
        source_dataset_id: str,
        variant_type: str,
        params: Dict[str, Any] = None,
        seed: int = None,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """生成变体"""
        if not source_dataset_id:
            return ToolResult.err(
                "source_dataset_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        if not variant_type:
            return ToolResult.err(
                "variant_type is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        params = params or {}

        try:
            result = self._variant_func(source_dataset_id, variant_type, params, seed)
            return ToolResult.ok(data=result)
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.EXECUTION_ERROR.value,
                source_dataset_id=source_dataset_id,
                variant_type=variant_type,
            )


class AnalysisExecuteTool(QuantumTool):
    """执行分析代码工具"""

    def __init__(
        self,
        execute_func: Callable[[str, int], Dict[str, Any]],
        is_connected: Callable[[], bool] = None,
    ):
        """
        Args:
            execute_func: 代码执行函数，签名为 (code: str, timeout: int) -> Dict
            is_connected: 连接检查函数
        """
        self._execute_func = execute_func
        self._is_connected = is_connected or (lambda: True)

    @property
    def name(self) -> str:
        return "analysis_execute"

    @property
    def description(self) -> str:
        return """Execute Python code for data analysis.
Use numpy, scipy, matplotlib for analysis operations.
Available: data (numpy array from current dataset)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "code": ParameterSchema(
                    name="code",
                    type="string",
                    description="Python code to execute for analysis",
                    required=True,
                ),
                "timeout": ParameterSchema(
                    name="timeout",
                    type="number",
                    description="Maximum execution time in seconds",
                    required=False,
                    default=60,
                    minimum=1,
                    maximum=600,
                ),
            },
        )

    def execute(
        self,
        code: str,
        timeout: int = 60,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """执行分析代码"""
        if not self._is_connected():
            return ToolResult.err(
                "Analysis service not connected",
                error_code=ErrorCode.LABRAD_ERROR.value,
            )

        if not code or not code.strip():
            return ToolResult.err(
                "Code cannot be empty",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        start_time = time.time()

        try:
            result = self._execute_func(code, timeout)
            elapsed = time.time() - start_time

            if result.get("status") == "success":
                return ToolResult.ok(
                    data={
                        "result": result,
                        "elapsed_seconds": elapsed,
                    },
                    execution_time=elapsed,
                )
            else:
                return ToolResult.err(
                    result.get("error", "Execution failed"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                    stdout=result.get("stdout"),
                    stderr=result.get("stderr"),
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.EXECUTION_ERROR.value,
                traceback=traceback.format_exc(),
            )


class ListVariantsTool(QuantumTool):
    """列出数据变体工具"""

    def __init__(
        self,
        list_func: Callable[[Optional[str]], Dict[str, Any]],
    ):
        """
        Args:
            list_func: 变体列表获取函数，签名为 (dataset_id: str) -> Dict
        """
        self._list_func = list_func

    @property
    def name(self) -> str:
        return "analysis_list_variants"

    @property
    def description(self) -> str:
        return """List all variants generated from a dataset.
Returns variant IDs, types, and creation timestamps."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "dataset_id": ParameterSchema(
                    name="dataset_id",
                    type="string",
                    description="Dataset ID to list variants for",
                    required=False,
                ),
            },
        )

    def execute(
        self,
        dataset_id: str = None,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """列出变体"""
        try:
            result = self._list_func(dataset_id)
            return ToolResult.ok(data=result)
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )


class GetLatestDatasetTool(QuantumTool):
    """获取最新数据集工具"""

    def __init__(
        self,
        getter: Callable[[], Dict[str, Any]],
    ):
        """
        Args:
            getter: 获取最新数据集函数，签名为 () -> Dict
        """
        self._getter = getter

    @property
    def name(self) -> str:
        return "analysis_get_latest_dataset"

    @property
    def description(self) -> str:
        return """Get the most recent experimental dataset.
Returns dataset ID, name, qubit, and experiment type for plotting."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={},
        )

    def execute(self, **kwargs) -> ToolResult[Dict[str, Any]]:
        """获取最新数据集"""
        try:
            result = self._getter()
            if result.get("success"):
                return ToolResult.ok(data=result.get("dataset", {}))
            else:
                return ToolResult.err(
                    result.get("error", "Failed to get latest dataset"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )


def setup_analysis_tools(
    registry,
    analysis_service_url: str = "http://localhost:3004"
) -> None:
    """设置 Analysis 工具到注册中心

    Args:
        registry: 工具注册中心实例
        analysis_service_url: Analysis Service 地址
    """

    def is_connected() -> bool:
        """检查 Analysis Service 是否可用"""
        result = _http_call(f"{analysis_service_url}/health")
        return result.get("status") == "healthy"

    def plot_offline(dataset_id: str, command: str = "") -> Dict[str, Any]:
        """绘制离线数据"""
        data = {"dataset_id": dataset_id}
        if command:
            data["command"] = command
        result = _http_call(f"{analysis_service_url}/plot/offline/v2", data, "POST")
        return result

    def get_stats(dataset_index: int = 0, axis: str = "both") -> Dict[str, Any]:
        """获取统计信息"""
        result = _http_call(
            f"{analysis_service_url}/stats?dataset_index={dataset_index}&axis={axis}"
        )
        return result

    def generate_variant(
        source_dataset_id: str,
        variant_type: str,
        params: Dict,
        seed: int = None
    ) -> Dict[str, Any]:
        """生成数据变体"""
        data = {
            "source_dataset_id": source_dataset_id,
            "variant_type": variant_type,
            "params": params,
        }
        if seed is not None:
            data["seed"] = seed
        result = _http_call(f"{analysis_service_url}/variants/generate", data, "POST")
        return result

    def execute_analysis_code(code: str, timeout: int = 60) -> Dict[str, Any]:
        """执行分析代码"""
        result = _http_call(
            f"{analysis_service_url}/execute",
            {"code": code, "timeout": timeout},
            "POST"
        )
        return result

    def list_variants(dataset_id: str = None) -> Dict[str, Any]:
        """列出变体"""
        url = f"{analysis_service_url}/variants"
        if dataset_id:
            url += f"?dataset_id={dataset_id}"
        result = _http_call(url)
        return result

    def get_latest_dataset() -> Dict[str, Any]:
        """获取最新的数据集"""
        result = _http_call(f"{analysis_service_url}/datasets?limit=1")
        if result.get("success") and result.get("datasets"):
            datasets = result["datasets"]
            if datasets:
                return {"success": True, "dataset": datasets[0]}
        return {"success": False, "error": "No datasets available"}

    # 注册工具
    registry.register_tool(PlotOfflineTool(plot_offline))
    registry.register_tool(GetStatsTool(get_stats))
    registry.register_tool(GenerateVariantTool(generate_variant))
    registry.register_tool(AnalysisExecuteTool(execute_analysis_code, is_connected))
    registry.register_tool(ListVariantsTool(list_variants))
    registry.register_tool(GetLatestDatasetTool(get_latest_dataset))
    registry.register_tool(ImageDisplayGuideTool())


class ImageDisplayGuideTool(QuantumTool):
    """图片显示指南工具

    当需要展示图表时，工具返回结果必须包含以下字段之一，
    前端会自动识别并显示图片。
    """

    @property
    def name(self) -> str:
        return "analysis_image_display_guide"

    @property
    def description(self) -> str:
        return """IMAGE DISPLAY GUIDE - Read this before returning images!

When returning image data in tool results, the frontend supports these JSON formats:

1. BASE64 FORMAT (preferred for generated images):
   {"success": true, "image": "data:image/png;base64,<base64_data>"}

2. FILE URL FORMAT (for saved plots):
   {"success": true, "image_url": "/plots/temp_abc123.png"}
   or
   {"success": true, "plotUrl": "/plots/output.png"}

3. NESTED FORMAT (wrapped in data field):
   {"success": true, "data": {"image": "data:image/png;base64,..."}}

IMPORTANT: Do NOT use Markdown image syntax like ![](url) in text.
The frontend extracts images ONLY from tool result JSON fields listed above."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={},
        )

    def execute(self, **kwargs) -> ToolResult[Dict[str, Any]]:
        """返回图片显示规范"""
        return ToolResult.ok(data={
            "guide": "Image display guide",
            "formats": [
                {"type": "base64", "field": "image", "example": "data:image/png;base64,..."},
                {"type": "url", "field": "image_url", "example": "/plots/xxx.png"},
                {"type": "url", "field": "plotUrl", "example": "/plots/xxx.png"},
                {"type": "nested", "field": "data.image", "example": "..."},
            ],
            "note": "Only JSON fields are recognized. Markdown syntax is NOT supported."
        })
