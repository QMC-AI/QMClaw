# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Hermes MCP 工具集管理器

统一管理多个工具集（quantum, analysis, workflow）的注册和桥接。
"""

import json
from typing import Any, Callable, Dict, List, Optional

from ..common.quantum_tools import ToolRegistry

# 服务地址配置
QUANTUM_SERVICE_URL = "http://localhost:3003"
ANALYSIS_SERVICE_URL = "http://localhost:3004"
WORKFLOW_SERVICE_URL = "http://localhost:3008"


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
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


class ToolsetBridge:
    """工具集桥接器

    将 QMClaw 工具注册到 hermes-agent 的工具系统。
    """

    def __init__(
        self,
        name: str,
        tool_registry: ToolRegistry,
        emoji: str = "🔧",
    ):
        """
        Args:
            name: 工具集名称
            tool_registry: QMClaw 工具注册中心
            emoji: 工具集图标
        """
        self._name = name
        self._registry = tool_registry
        self._emoji = emoji
        self._hermes_registry = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def register_to_hermes(self, hermes_registry) -> None:
        """将工具注册到 hermes

        Args:
            hermes_registry: hermes 的工具注册中心
        """
        if hermes_registry is None:
            return

        self._hermes_registry = hermes_registry

        for tool_name in self._registry.list_tools():
            tool = self._registry.get_tool(tool_name)
            if tool:
                schema = tool.schema.to_openai_schema()
                # 工具名前缀格式: qmclaw_{toolset}_{name}
                full_name = f"qmclaw_{self._name}_{tool_name}"
                self._hermes_registry.register(
                    name=full_name,
                    toolset=self._name,
                    schema=schema,
                    handler=self._create_handler(tool_name),
                    emoji=self._emoji,
                )

    def _create_handler(self, tool_name: str):
        """创建工具处理器"""
        def handler(args, **kwargs):
            result = self._registry.execute(tool_name, args)
            return json.dumps(result.to_dict(), ensure_ascii=False)
        return handler

    def get_toolset_config(self) -> Dict[str, Any]:
        """获取工具集配置"""
        tools = []
        for name in self._registry.list_tools():
            tools.append(f"qmclaw_{self._name}_{name}")

        return {
            "name": self._name,
            "description": self._get_description(),
            "tools": tools,
            "tool_count": len(tools),
        }

    def _get_description(self) -> str:
        """获取工具集描述"""
        descriptions = {
            "quantum": "Quantum measurement and control tools for LabRAD experiments",
            "analysis": "Data analysis and visualization tools",
            "workflow": "Workflow automation and execution tools",
        }
        return descriptions.get(self._name, f"{self._name} tools")


class MCPBridgeManager:
    """MCP 桥接管理器

    管理多个工具集的桥接和注册。
    """

    def __init__(
        self,
        quantum_service_url: str = QUANTUM_SERVICE_URL,
        analysis_service_url: str = ANALYSIS_SERVICE_URL,
        workflow_service_url: str = WORKFLOW_SERVICE_URL,
    ):
        """
        Args:
            quantum_service_url: Quantum Service 地址
            analysis_service_url: Analysis Service 地址
            workflow_service_url: Workflow Service 地址
        """
        self._quantum_service_url = quantum_service_url
        self._analysis_service_url = analysis_service_url
        self._workflow_service_url = workflow_service_url

        self._bridges: Dict[str, ToolsetBridge] = {}
        self._initialized = False

    @property
    def bridges(self) -> Dict[str, ToolsetBridge]:
        return self._bridges

    def initialize(self) -> None:
        """初始化所有工具集"""
        if self._initialized:
            return

        # 1. Quantum 工具集
        self._setup_quantum_bridge()

        # 2. Analysis 工具集
        self._setup_analysis_bridge()

        # 3. Workflow 工具集
        self._setup_workflow_bridge()

        self._initialized = True

    def _setup_quantum_bridge(self) -> None:
        """设置 Quantum 工具集"""
        from ..common.quantum_tools import ToolRegistry
        from .quantum_toolset import setup_hermes_tools

        registry = ToolRegistry()
        setup_hermes_tools(registry, self._quantum_service_url)

        self._bridges["quantum"] = ToolsetBridge(
            name="quantum",
            tool_registry=registry,
            emoji="🔬",
        )

    def _setup_analysis_bridge(self) -> None:
        """设置 Analysis 工具集"""
        from ..common.quantum_tools import ToolRegistry
        from .analysis_tools import setup_analysis_tools

        registry = ToolRegistry()
        setup_analysis_tools(registry, self._analysis_service_url)

        self._bridges["analysis"] = ToolsetBridge(
            name="analysis",
            tool_registry=registry,
            emoji="📊",
        )

    def _setup_workflow_bridge(self) -> None:
        """设置 Workflow 工具集"""
        from ..common.quantum_tools import ToolRegistry
        from .workflow_tools import setup_workflow_tools

        registry = ToolRegistry()
        setup_workflow_tools(registry, self._workflow_service_url)

        self._bridges["workflow"] = ToolsetBridge(
            name="workflow",
            tool_registry=registry,
            emoji="⚙️",
        )

    def register_all(self, hermes_registry) -> None:
        """注册所有工具集到 hermes

        Args:
            hermes_registry: hermes 的工具注册中心
        """
        if not self._initialized:
            self.initialize()

        for name, bridge in self._bridges.items():
            bridge.register_to_hermes(hermes_registry)

    def get_all_toolset_configs(self) -> List[Dict[str, Any]]:
        """获取所有工具集配置"""
        if not self._initialized:
            self.initialize()

        configs = []
        for name, bridge in self._bridges.items():
            configs.append(bridge.get_toolset_config())

        return configs

    def get_tool_count(self) -> int:
        """获取总工具数"""
        if not self._initialized:
            self.initialize()

        return sum(len(b.registry.list_tools()) for b in self._bridges.values())

    def get_toolset_names(self) -> List[str]:
        """获取所有工具集名称"""
        return list(self._bridges.keys())


def create_mcp_bridge_manager(
    quantum_service_url: str = QUANTUM_SERVICE_URL,
    analysis_service_url: str = ANALYSIS_SERVICE_URL,
    workflow_service_url: str = WORKFLOW_SERVICE_URL,
) -> MCPBridgeManager:
    """便捷函数：创建 MCP 桥接管理器

    Args:
        quantum_service_url: Quantum Service 地址
        analysis_service_url: Analysis Service 地址
        workflow_service_url: Workflow Service 地址

    Returns:
        配置好的 MCPBridgeManager 实例
    """
    manager = MCPBridgeManager(
        quantum_service_url=quantum_service_url,
        analysis_service_url=analysis_service_url,
        workflow_service_url=workflow_service_url,
    )
    manager.initialize()
    return manager
