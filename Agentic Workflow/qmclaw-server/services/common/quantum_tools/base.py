# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
量子工具基类和统一返回格式

提供统一的数据结构和抽象基类，用于定义量子测控工具。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, TypeVar

T = TypeVar('T')


@dataclass
class ParameterSchema:
    """参数 schema 定义"""
    name: str
    type: str  # "string", "number", "boolean", "array", "object"
    description: str = ""
    required: bool = False
    default: Any = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum: Optional[List[Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling 格式"""
        result = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
        }
        if self.default is not None:
            result["default"] = self.default
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.enum is not None:
            result["enum"] = self.enum
        return result


@dataclass
class ReturnSchema:
    """返回 schema 定义"""
    type: str = "object"
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "description": self.description,
        }


@dataclass
class ToolSchema:
    """工具 schema 定义"""
    name: str
    description: str
    parameters: Dict[str, ParameterSchema] = field(default_factory=dict)
    returns: ReturnSchema = field(default_factory=ReturnSchema)

    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling schema 格式"""
        properties = {}
        required = []

        for name, param in self.parameters.items():
            properties[name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                properties[name]["default"] = param.default
            if param.minimum is not None:
                properties[name]["minimum"] = param.minimum
            if param.maximum is not None:
                properties[name]["maximum"] = param.maximum
            if param.enum is not None:
                properties[name]["enum"] = param.enum
            if param.required:
                required.append(name)

        result = {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
            }
        }
        if required:
            result["parameters"]["required"] = required

        return result


@dataclass
class ToolResult(Generic[T]):
    """统一返回格式

    所有量子测控工具的返回值都使用此格式。

    Attributes:
        success: 操作是否成功
        data: 返回数据（成功时）
        error: 错误信息（失败时）
        error_code: 错误码
        metadata: 元数据（如执行时间、数据源等）
    """
    success: bool
    data: Optional[T] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, data: T, **metadata) -> 'ToolResult[T]':
        """创建成功结果"""
        return cls(success=True, data=data, metadata=metadata)

    @classmethod
    def err(cls, error: str, error_code: str = "INTERNAL_ERROR", **metadata) -> 'ToolResult':
        """创建错误结果"""
        return cls(success=False, error=error, error_code=error_code, metadata=metadata)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            "success": self.success,
        }
        if self.success:
            result["data"] = self.data
        else:
            result["error"] = self.error
            result["error_code"] = self.error_code

        if self.metadata:
            result["metadata"] = self.metadata

        return result

    def to_api_response(self, request_id: str = "") -> Dict[str, Any]:
        """转换为 API 响应格式（包含 request_id 和 timestamp）"""
        return {
            "success": self.success,
            "data": self.data if self.success else None,
            "error": {
                "code": self.error_code,
                "message": self.error,
            } if not self.success else None,
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "metadata": self.metadata if self.metadata else None,
        }


class QuantumTool(ABC, Generic[T]):
    """量子工具基类

    所有量子测控工具必须继承此类并实现：
    - name: 工具名称
    - description: 工具描述
    - schema: 工具参数 schema
    - execute: 工具执行逻辑
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述（用于 LLM 理解工具用途）"""
        pass

    @property
    def schema(self) -> ToolSchema:
        """工具参数 schema"""
        return ToolSchema(name=self.name, description=self.description)

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult[T]:
        """执行工具

        Args:
            **kwargs: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        pass

    def get_openai_definition(self) -> Dict[str, Any]:
        """获取 OpenAI function calling 格式的工具定义"""
        return {
            "type": "function",
            "function": self.schema.to_openai_schema(),
        }


@dataclass
class ExperimentResult:
    """实验结果数据结构"""
    id: str
    experiment_name: str
    status: str  # "success", "failed", "running", "pending"
    started_at: str = ""
    completed_at: str = ""
    target: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    results: Dict[str, Any] = field(default_factory=dict)
    arrays: Dict[str, List[float]] = field(default_factory=dict)
    plots: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    notes: str = ""
    # 离线链路：命中的历史数据集。有了它才能让 analysis_service 用
    # /plot/offline/v2 复现 experiments 页那张图（同源同版式）。
    dataset_id: Optional[str] = None
    source_data: Optional[str] = None
    synthetic: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "experiment_name": self.experiment_name,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "target": self.target,
            "params": self.params,
            "results": self.results,
            "arrays": self.arrays,
            "plots": self.plots,
            "error": self.error,
            "notes": self.notes,
            "dataset_id": self.dataset_id,
            "source_data": self.source_data,
            "synthetic": self.synthetic,
        }


@dataclass
class QubitInfo:
    """量子比特信息"""
    name: str
    qubit_type: str = ""
    session_path: List[str] = field(default_factory=list)
    params: Dict[str, float] = field(default_factory=dict)
    available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qubit_type": self.qubit_type,
            "session_path": self.session_path,
            "params": self.params,
            "available": self.available,
        }
