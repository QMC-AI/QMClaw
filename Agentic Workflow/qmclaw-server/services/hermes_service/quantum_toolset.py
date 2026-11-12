# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Hermes Service 量子工具集

创建 MCP 工具服务器，将量子测控工具暴露给 hermes-agent。
通过 HTTP 调用 quantum_service 执行实验。
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# 尝试导入 MCP SDK
try:
    from mcp.server import Server
    from mcp.types import Tool, TextContent
    from mcp.server.stdio import stdio_server
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    Server = None
    Tool = None
    TextContent = None

from ..common.quantum_tools import ToolRegistry


# quantum_service 地址
QUANTUM_SERVICE_URL = "http://localhost:3003"


def _http_call(url: str, data: dict = None, method: str = "GET") -> dict:
    """发送 HTTP 请求到 quantum_service"""
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
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def setup_hermes_tools(registry: ToolRegistry, quantum_service_url: str = QUANTUM_SERVICE_URL) -> ToolRegistry:
    """设置 hermes_service 的工具到注册中心

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
        func_name = f"sq.{name}"
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
        # hermes_service 不直接管理历史
        return []

    # 注册工具
    registry.register_tool(GetQubitsTool(get_qubits))
    registry.register_tool(GetQubitParamsTool(get_qubit_params, get_qubits))
    registry.register_tool(ListExperimentsTool(list_experiments))
    registry.register_tool(RunCodeTool(execute_code, is_connected))
    registry.register_tool(ListHistoryTool(list_history))

    return registry


class QuantumMCPServer:
    """量子测控 MCP 服务器

    提供 MCP 协议接口，将量子测控工具暴露给外部调用者（如 hermes-agent）。
    """

    def __init__(self, tool_registry=None, quantum_service_url: str = QUANTUM_SERVICE_URL):
        """
        Args:
            tool_registry: 量子工具注册中心，默认创建新的
            quantum_service_url: quantum_service 地址
        """
        self._quantum_service_url = quantum_service_url
        if tool_registry is None:
            self._registry = ToolRegistry()
            setup_hermes_tools(self._registry, quantum_service_url)
        else:
            self._registry = tool_registry
        self._server_name = "qmclaw-quantum"

    @property
    def registry(self) -> ToolRegistry:
        """获取工具注册中心"""
        return self._registry

    def get_mcp_tools(self) -> List[Dict[str, Any]]:
        """获取 MCP 格式的工具定义

        Returns:
            MCP 格式的工具定义列表
        """
        tools = []
        for name in self._registry.list_tools():
            tool = self._registry.get_tool(name)
            if tool:
                schema = tool.schema
                tools.append({
                    "name": f"qmclaw_{name}",
                    "description": tool.description,
                    "inputSchema": {
                        "type": "object",
                        "properties": self._schema_to_properties(schema),
                        "required": [
                            name for name, param in schema.parameters.items()
                            if param.required
                        ] if schema.parameters else [],
                    },
                })
        return tools

    def _schema_to_properties(self, schema) -> Dict[str, Any]:
        """将 schema 转换为 MCP inputSchema properties"""
        properties = {}
        for name, param in schema.parameters.items():
            properties[name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                properties[name]["default"] = param.default
            if param.enum:
                properties[name]["enum"] = param.enum
            if param.minimum is not None:
                properties[name]["minimum"] = param.minimum
            if param.maximum is not None:
                properties[name]["maximum"] = param.maximum
        return properties

    async def handle_list_tools(self) -> List[Tool]:
        """处理工具列表请求"""
        if not MCP_AVAILABLE:
            return []

        mcp_tools = []
        for tool_def in self.get_mcp_tools():
            mcp_tools.append(
                Tool(
                    name=tool_def["name"],
                    description=tool_def["description"],
                    inputSchema=tool_def["inputSchema"],
                )
            )
        return mcp_tools

    async def handle_call_tool(
        self,
        name: str,
        arguments: Dict[str, Any]
    ) -> List[TextContent]:
        """处理工具调用请求"""
        # 移除 qmclaw_ 前缀
        if name.startswith("qmclaw_"):
            tool_name = name[8:]  # 移除 "qmclaw_"
        else:
            tool_name = name

        # 执行工具
        result = self._registry.execute(tool_name, arguments)

        # 转换为 MCP 响应格式
        response = {
            "success": result.success,
            "tool": tool_name,
            "request_id": f"req_{uuid.uuid4().hex[:12]}",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        if result.success:
            response["data"] = result.data
        else:
            response["error"] = result.error
            response["error_code"] = result.error_code

        if result.metadata:
            response["metadata"] = result.metadata

        return [TextContent(type="text", text=json.dumps(response, ensure_ascii=False, indent=2))]

    def create_server(self) -> Optional[Any]:
        """创建 MCP 服务器实例"""
        if not MCP_AVAILABLE:
            return None

        server = Server(self._server_name)

        @server.list_tools()
        async def list_tools():
            return await self.handle_list_tools()

        @server.call_tool()
        async def call_tool(name: str, arguments: Dict[str, Any]):
            return await self.handle_call_tool(name, arguments)

        return server


class HermesQuantumBridge:
    """Hermes 量子工具桥接器

    直接桥接到 hermes-agent 的工具系统。
    """

    def __init__(self, hermes_registry=None, quantum_service_url: str = QUANTUM_SERVICE_URL):
        """
        Args:
            hermes_registry: hermes 的工具注册中心
            quantum_service_url: quantum_service 地址
        """
        self._hermes_registry = hermes_registry
        self._quantum_service_url = quantum_service_url
        self._qmclaw_registry = ToolRegistry()
        setup_hermes_tools(self._qmclaw_registry, quantum_service_url)

    @property
    def qmclaw_registry(self) -> ToolRegistry:
        """获取 QMClaw 工具注册中心"""
        return self._qmclaw_registry

    def register_to_hermes(self, hermes_registry):
        """将 QMClaw 工具注册到 hermes

        Args:
            hermes_registry: hermes 的工具注册中心
        """
        if hermes_registry is None:
            return

        self._hermes_registry = hermes_registry

        # 注册每个 QMClaw 工具
        for name in self._qmclaw_registry.list_tools():
            tool = self._qmclaw_registry.get_tool(name)
            if tool:
                schema = tool.schema.to_openai_schema()
                self._hermes_registry.register(
                    name=f"qmclaw_{name}",
                    toolset="quantum",
                    schema=schema,
                    handler=self._create_handler(name),
                    emoji="🔬",
                )

    def _create_handler(self, tool_name: str):
        """创建工具处理器"""
        def handler(args, **kwargs):
            result = self._qmclaw_registry.execute(tool_name, args)
            return json.dumps(result.to_dict(), ensure_ascii=False)
        return handler

    def get_quantum_toolset_config(self) -> Dict[str, Any]:
        """获取量子工具集配置

        返回可用于 hermes 配置的工具集定义。
        """
        tools = []
        for name in self._qmclaw_registry.list_tools():
            tools.append(f"qmclaw_{name}")

        return {
            "name": "quantum",
            "description": "Quantum measurement and control tools",
            "tools": tools,
        }


def create_quantum_toolset(quantum_service_url: str = QUANTUM_SERVICE_URL) -> Dict[str, Any]:
    """便捷函数：创建量子工具集定义"""
    registry = ToolRegistry()
    setup_hermes_tools(registry, quantum_service_url)

    tools = []
    for name in registry.list_tools():
        tools.append(f"qmclaw_{name}")

    return {
        "name": "quantum",
        "description": "Quantum measurement and control tools for LabRAD experiments",
        "tools": tools,
        "tool_count": len(tools),
    }


async def run_mcp_server():
    """运行 MCP 服务器（用于独立测试）"""
    if not MCP_AVAILABLE:
        print("MCP not available, cannot run server")
        return

    server = QuantumMCPServer()
    mcp_server = server.create_server()

    if mcp_server:
        async with stdio_server() as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )
