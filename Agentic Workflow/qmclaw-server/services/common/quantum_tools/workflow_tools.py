# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Workflow 工具

提供工作流管理相关的 MCP 工具。
通过 HTTP 调用 Workflow Service 执行工作流操作。
"""

import json
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

        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
            return {"error": error_body.get("error", f"HTTP {e.code}: {e.reason}")}
        except Exception:
            return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


class ListWorkflowsTool(QuantumTool):
    """列出工作流工具"""

    def __init__(
        self,
        lister: Callable[[], Dict[str, Any]],
    ):
        """
        Args:
            lister: 工作流列表获取函数，签名为 () -> Dict
        """
        self._lister = lister

    @property
    def name(self) -> str:
        return "workflow_list"

    @property
    def description(self) -> str:
        return """List all available workflows.
Returns workflow IDs, names, status, and node counts."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={},
        )

    def execute(self, **kwargs) -> ToolResult[List[Dict[str, Any]]]:
        """列出工作流"""
        try:
            result = self._lister()
            # workflow service 返回格式可能没有 success 字段
            if result.get("success") or ("workflows" in result and "count" in result):
                return ToolResult.ok(data=result.get("workflows", []))
            else:
                return ToolResult.err(
                    result.get("error", "Failed to list workflows"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )


class CreateWorkflowTool(QuantumTool):
    """创建工作流工具"""

    def __init__(
        self,
        creator: Callable[[str, List[Dict]], Dict[str, Any]],
    ):
        """
        Args:
            creator: 工作流创建函数，签名为 (name: str, nodes: List[Dict]) -> Dict
        """
        self._creator = creator

    @property
    def name(self) -> str:
        return "workflow_create"

    @property
    def description(self) -> str:
        return """Create a new workflow with specified nodes.
Each node should have: id, type, config, and depends."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "name": ParameterSchema(
                    name="name",
                    type="string",
                    description="Workflow name",
                    required=True,
                ),
                "nodes": ParameterSchema(
                    name="nodes",
                    type="array",
                    description="List of workflow nodes",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        name: str,
        nodes: List[Dict[str, Any]],
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """创建工作流"""
        if not name:
            return ToolResult.err(
                "name is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        if not nodes:
            return ToolResult.err(
                "nodes cannot be empty",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._creator(name, nodes)
            if result.get("success"):
                return ToolResult.ok(data={
                    "workflow_id": result.get("workflow_id"),
                    "name": result.get("name"),
                    "node_count": result.get("node_count"),
                })
            else:
                return ToolResult.err(
                    result.get("error", "Failed to create workflow"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )


class RunWorkflowTool(QuantumTool):
    """运行工作流工具"""

    def __init__(
        self,
        runner: Callable[[str, Dict], Dict[str, Any]],
    ):
        """
        Args:
            runner: 工作流运行函数，签名为 (workflow_id: str, context: Dict) -> Dict
        """
        self._runner = runner

    @property
    def name(self) -> str:
        return "workflow_run"

    @property
    def description(self) -> str:
        return """Run an existing workflow.
Provides context variables that will be available to workflow nodes."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to run",
                    required=True,
                ),
                "context": ParameterSchema(
                    name="context",
                    type="object",
                    description="Context variables for the workflow",
                    required=False,
                    default={},
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        context: Dict[str, str] = None,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """运行工作流"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        context = context or {}

        try:
            result = self._runner(workflow_id, context)
            if result.get("success"):
                return ToolResult.ok(data={
                    "workflow_id": result.get("workflow_id"),
                    "status": result.get("status"),
                    "run_id": result.get("run_id"),
                })
            else:
                return ToolResult.err(
                    result.get("error", "Failed to run workflow"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


class GetWorkflowStatusTool(QuantumTool):
    """获取工作流状态工具"""

    def __init__(
        self,
        status_getter: Callable[[str], Dict[str, Any]],
    ):
        """
        Args:
            status_getter: 状态获取函数，签名为 (workflow_id: str) -> Dict
        """
        self._status_getter = status_getter

    @property
    def name(self) -> str:
        return "workflow_status"

    @property
    def description(self) -> str:
        return """Get the status of a workflow run.
Returns node statuses and any available results."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to check status",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """获取工作流状态"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._status_getter(workflow_id)
            if result.get("success"):
                return ToolResult.ok(data=result)
            else:
                return ToolResult.err(
                    result.get("error", "Failed to get workflow status"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                    workflow_id=workflow_id,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


class CancelWorkflowTool(QuantumTool):
    """取消工作流工具"""

    def __init__(
        self,
        canceller: Callable[[str], Dict[str, Any]],
    ):
        """
        Args:
            canceller: 取消函数，签名为 (workflow_id: str) -> Dict
        """
        self._canceller = canceller

    @property
    def name(self) -> str:
        return "workflow_cancel"

    @property
    def description(self) -> str:
        return """Cancel a running workflow."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to cancel",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """取消工作流"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._canceller(workflow_id)
            if result.get("success"):
                return ToolResult.ok(data={
                    "workflow_id": result.get("workflow_id"),
                    "status": "cancelled",
                })
            else:
                return ToolResult.err(
                    result.get("error", "Failed to cancel workflow"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


class GetWorkflowStatsTool(QuantumTool):
    """获取工作流统计信息工具"""

    def __init__(
        self,
        stats_getter: Callable[[str], Dict[str, Any]],
    ):
        """
        Args:
            stats_getter: 统计获取函数，签名为 (workflow_id: str) -> Dict
        """
        self._stats_getter = stats_getter

    @property
    def name(self) -> str:
        return "workflow_stats"

    @property
    def description(self) -> str:
        return """Get execution statistics for a workflow.
Returns total runs, completed, failed counts, and average duration."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self.name,
            description=self.description,
            parameters={
                "workflow_id": ParameterSchema(
                    name="workflow_id",
                    type="string",
                    description="Workflow ID to get stats for",
                    required=True,
                ),
            },
        )

    def execute(
        self,
        workflow_id: str,
        **kwargs,
    ) -> ToolResult[Dict[str, Any]]:
        """获取工作流统计"""
        if not workflow_id:
            return ToolResult.err(
                "workflow_id is required",
                error_code=ErrorCode.VALIDATION_ERROR.value,
            )

        try:
            result = self._stats_getter(workflow_id)
            if result.get("success"):
                return ToolResult.ok(data=result)
            else:
                return ToolResult.err(
                    result.get("error", "Failed to get workflow stats"),
                    error_code=ErrorCode.EXECUTION_ERROR.value,
                )
        except Exception as e:
            return ToolResult.err(
                str(e),
                error_code=ErrorCode.INTERNAL_ERROR.value,
                workflow_id=workflow_id,
            )


def setup_workflow_tools(
    registry,
    workflow_service_url: str = "http://localhost:3008"
) -> None:
    """设置 Workflow 工具到注册中心

    Args:
        registry: 工具注册中心实例
        workflow_service_url: Workflow Service 地址
    """

    def list_workflows() -> Dict[str, Any]:
        """列出工作流"""
        result = _http_call(f"{workflow_service_url}/workflows")
        return result

    def create_workflow(name: str, nodes: List[Dict]) -> Dict[str, Any]:
        """创建工作流"""
        result = _http_call(
            f"{workflow_service_url}/workflows/create",
            {"name": name, "nodes": nodes},
            "POST"
        )
        return result

    def run_workflow(workflow_id: str, context: Dict) -> Dict[str, Any]:
        """运行工作流"""
        result = _http_call(
            f"{workflow_service_url}/workflows/run",
            {"workflow_id": workflow_id, "context": context},
            "POST"
        )
        return result

    def get_workflow_status(workflow_id: str) -> Dict[str, Any]:
        """获取工作流状态"""
        result = _http_call(
            f"{workflow_service_url}/workflows/status",
            {"workflowId": workflow_id},
            "POST"
        )
        return result

    def cancel_workflow(workflow_id: str) -> Dict[str, Any]:
        """取消工作流"""
        result = _http_call(
            f"{workflow_service_url}/workflows/cancel",
            {"workflowId": workflow_id},
            "POST"
        )
        return result

    def get_workflow_stats(workflow_id: str) -> Dict[str, Any]:
        """获取工作流统计"""
        result = _http_call(f"{workflow_service_url}/runs/stats/{workflow_id}")
        return result

    # 注册工具
    registry.register_tool(ListWorkflowsTool(list_workflows))
    registry.register_tool(CreateWorkflowTool(create_workflow))
    registry.register_tool(RunWorkflowTool(run_workflow))
    registry.register_tool(GetWorkflowStatusTool(get_workflow_status))
    registry.register_tool(CancelWorkflowTool(cancel_workflow))
    registry.register_tool(GetWorkflowStatsTool(get_workflow_stats))
