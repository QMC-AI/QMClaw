# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
QCA Service 适配器

将 QCA Service 的 LangChain 工具适配到统一的 ToolRegistry。
通过 HTTP 调用 quantum_service 执行实验。
"""

import json
import urllib.request
import urllib.error
from typing import Any, Callable, Dict, List, Optional

from ..common.quantum_tools import (
    ExperimentNotFoundError,
    GetArrayDataTool,
    GetExperimentDataTool,
    GetQubitParamsTool,
    GetQubitsTool,
    GetStatsTool,
    ListArraysTool,
    ListExperimentsTool,
    ListHistoryTool,
    QubitNotFoundError,
    RunCodeTool,
    RunExperimentTool,
    ToolRegistry,
    ToolResult,
)


# quantum_service 地址
QUANTUM_SERVICE_URL = "http://localhost:3003"


# 实验名称映射 (QCA name -> sq.* function name)
EXPERIMENT_NAME_MAP = {
    "t1_measurement": "sq.t1",
    "ramsey_measurement": "sq.ramsey_df",
    "spectroscopy": "sq.spectroscopy",
    "rabi_oscillation": "sq.iqraw",
    "qubit_spectroscopy": "sq.spectroscopy",
    "resonator_spectroscopy": "sq.s21",
}


def _http_call(url: str, data: dict = None, method: str = "GET") -> dict:
    """发送 HTTP 请求到 quantum_service"""
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
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def setup_qca_tools(registry: ToolRegistry, quantum_service_url: str = QUANTUM_SERVICE_URL) -> ToolRegistry:
    """设置 qca_service 的工具到注册中心

    通过 HTTP 调用 quantum_service 执行实验。

    Args:
        registry: 工具注册中心实例
        quantum_service_url: quantum_service 地址

    Returns:
        注册了工具的 ToolRegistry 实例
    """
    from ..common.quantum_tools import (
        GetQubitsTool, GetQubitParamsTool, ListExperimentsTool,
        RunCodeTool, RunExperimentTool, ListHistoryTool
    )

    def is_connected() -> bool:
        """检查 quantum_service 是否可用"""
        result = _http_call(f"{quantum_service_url}/health")
        return result.get("status") == "healthy"

    def get_qubits() -> List[Dict[str, Any]]:
        """获取量子比特列表"""
        result = _http_call(f"{quantum_service_url}/qubits")
        if "qubits" in result:
            return result["qubits"]
        return []

    def get_qubit_params(name: str) -> Optional[Dict[str, Any]]:
        """获取量子比特参数"""
        result = _http_call(f"{quantum_service_url}/qubit/params", {"name": name}, "POST")
        if "params" in result:
            return result["params"]
        return None

    def list_experiments() -> List[Dict[str, str]]:
        """列出实验"""
        result = _http_call(f"{quantum_service_url}/experiments")
        if "experiments" in result:
            return result["experiments"]
        return []

    def execute_code(code: str) -> Dict[str, Any]:
        """执行代码（显式同步：阶段 16a 起 /execute 默认异步，工具调用要的是结果）"""
        result = _http_call(f"{quantum_service_url}/execute", {"code": code, "wait": True}, "POST")
        if result.get("status") == "success":
            return result
        return {"error": result.get("error", "Unknown error")}

    def run_experiment(name: str, params: Dict, notes: str = "") -> Dict[str, Any]:
        """执行命名实验"""
        # 构建代码
        func_name = EXPERIMENT_NAME_MAP.get(name, f"sq.{name}")
        param_str = ", ".join(f"{k}={repr(v)}" for k, v in params.items())
        code = f"{func_name}({param_str})"

        result = _http_call(
            f"{quantum_service_url}/execute", {"code": code, "wait": True}, "POST"
        )

        # 包装结果
        return {
            "id": result.get("task_id", ""),
            "status": result.get("status", "failed"),
            "results": result.get("result", {}),
            "error": result.get("error"),
        }

    def list_history(last_n: int, type: str = None) -> List[Dict[str, Any]]:
        """查询实验历史"""
        # QCA 使用自己的存储，这里返回空列表
        # 实际应由 QCA 的 storage 模块提供
        return []

    def get_experiment_data(exp_id: str) -> Optional[Dict[str, Any]]:
        """获取实验数据"""
        # QCA 使用自己的存储
        return None

    def list_arrays(exp_id: str) -> Optional[List[str]]:
        """列出数组"""
        # QCA 使用自己的存储
        return None

    def get_stats(exp_id: str, array_name: str) -> Optional[Dict[str, float]]:
        """获取统计"""
        # QCA 使用自己的存储
        return None

    # 注册工具
    registry.register_tool(GetQubitsTool(get_qubits))
    registry.register_tool(GetQubitParamsTool(get_qubit_params, get_qubits))
    registry.register_tool(ListExperimentsTool(list_experiments))
    registry.register_tool(RunCodeTool(execute_code, is_connected))
    registry.register_tool(RunExperimentTool(
        lambda name, params: run_experiment(name, params),
        execute_code,
        is_connected=is_connected
    ))
    registry.register_tool(ListHistoryTool(list_history))

    return registry


class QCAServiceAdapter:
    """QCA Service 适配器

    将 QCA Service 的 LangChain 工具适配到统一的 ToolRegistry。
    """

    def __init__(self, quantum_service_url: str = QUANTUM_SERVICE_URL):
        """
        Args:
            quantum_service_url: quantum_service 地址
        """
        self._quantum_service_url = quantum_service_url
        self._registry = ToolRegistry()
        self._setup_tools()

    def _setup_tools(self):
        """设置工具"""
        setup_qca_tools(self._registry, self._quantum_service_url)

    def set_quantum_service_url(self, url: str):
        """设置 quantum_service 地址"""
        self._quantum_service_url = url
        self._registry = ToolRegistry()
        self._setup_tools()

    def execute_tool(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """执行工具"""
        return self._registry.execute(name, args)

    def get_tools(self) -> List[Dict[str, Any]]:
        """获取工具定义列表"""
        return self._registry.get_definitions()

    def get_registry(self) -> ToolRegistry:
        """获取注册中心"""
        return self._registry


class QCAToolWrapper:
    """QCA LangChain 工具包装器

    将 QCA Service 的 @tool 装饰器函数包装为统一的 QuantumTool 接口。
    """

    def __init__(self, name: str, langchain_tool, registry: ToolRegistry = None):
        """
        Args:
            name: 工具名称
            langchain_tool: LangChain @tool 装饰的函数
            registry: 可选的工具注册中心
        """
        self._name = name
        self._tool = langchain_tool
        self._registry = registry or ToolRegistry()

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return getattr(self._tool, "description", "")

    def execute(self, **kwargs) -> ToolResult:
        """执行工具"""
        try:
            result = self._tool.invoke(kwargs)
            return ToolResult.ok(result)
        except ValueError as e:
            return ToolResult.err(str(e), error_code="VALIDATION_ERROR")
        except Exception as e:
            return ToolResult.err(str(e), error_code="TOOL_ERROR")


def wrap_qca_tool(name: str, langchain_tool, registry: ToolRegistry = None) -> QCAToolWrapper:
    """便捷函数：将 QCA LangChain 工具包装为统一接口"""
    wrapper = QCAToolWrapper(name, langchain_tool, registry)
    registry.register_tool(wrapper)
    return wrapper
