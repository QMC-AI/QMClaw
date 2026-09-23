# SPDX-FileCopyrightText: Copyright (c) 2026 QMClaw. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Agent Service 适配器

将现有的自定义 Tool 类适配到统一的 ToolRegistry。
通过 HTTP 调用 quantum_service 执行实验，实现与设备的解耦。
"""

import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.quantum_tools import (
    GetQubitParamsTool,
    GetQubitsTool,
    ListExperimentsTool,
    RunCodeTool,
    RunExperimentTool,
    ListHistoryTool,
    ToolRegistry,
    ToolResult,
)


# quantum_service 地址
QUANTUM_SERVICE_URL = "http://localhost:3003"

# analysis_service 地址：用于复现 experiments 页那张图
# （/plot/offline/v2 由 offline_data_lab.OfflineQubitUpdater.fitData 绘图）
ANALYSIS_SERVICE_URL = "http://localhost:3004"

# 服务根：<qmclaw-server>（本文件在 services/agent_service/ 下）
_SERVER_ROOT = Path(__file__).resolve().parents[2]

# experiment_configs.json 的 defaultCommand 模板缓存
_EXPERIMENT_TEMPLATES: Dict[str, str] = {}
_EXPERIMENT_TEMPLATES_LOADED = False


def _load_experiment_templates() -> Dict[str, str]:
    """读取 config/experiment_configs.json 的 defaultCommand 模板（带缓存）

    返回 {experiment_key: "sq.t1({qubit}, do_plot=True)"}。
    这是该配置文件里的权威调用约定；配置 key 与真实函数名可能不同
    （例如 ramsey -> sq.ramsey_df），以模板为准可自动修正。
    读取失败时返回空 dict，由 build_experiment_code 回退。
    """
    global _EXPERIMENT_TEMPLATES_LOADED
    if _EXPERIMENT_TEMPLATES_LOADED:
        return _EXPERIMENT_TEMPLATES
    _EXPERIMENT_TEMPLATES_LOADED = True
    try:
        cfg = _SERVER_ROOT / "config" / "experiment_configs.json"
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, info in (data.get("experiments") or {}).items():
            cmd = (info or {}).get("defaultCommand")
            if cmd:
                _EXPERIMENT_TEMPLATES[key] = cmd
    except Exception:
        pass
    return _EXPERIMENT_TEMPLATES


# experiment_configs.json 的 defaultPlotCommand 模板缓存
_EXPERIMENT_PLOT_COMMANDS: Dict[str, str] = {}
_EXPERIMENT_PLOT_COMMANDS_LOADED = False


def _load_experiment_plot_commands() -> Dict[str, str]:
    """读取 config/experiment_configs.json 的 defaultPlotCommand（带缓存）

    返回 {experiment_key: "qter.fitData({exp_num})"}。
    experiments 页画图用的就是这个字段，agent 要出"同款图"必须读同一份配置，
    否则两边各写一套版式、迟早漂移。
    """
    global _EXPERIMENT_PLOT_COMMANDS_LOADED
    if _EXPERIMENT_PLOT_COMMANDS_LOADED:
        return _EXPERIMENT_PLOT_COMMANDS
    _EXPERIMENT_PLOT_COMMANDS_LOADED = True
    try:
        cfg = _SERVER_ROOT / "config" / "experiment_configs.json"
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, info in (data.get("experiments") or {}).items():
            cmd = (info or {}).get("defaultPlotCommand")
            if cmd:
                _EXPERIMENT_PLOT_COMMANDS[key] = cmd
    except Exception:
        pass
    return _EXPERIMENT_PLOT_COMMANDS


def fetch_experiment_plot(
    dataset_id: str,
    experiment_name: str = "",
    analysis_service_url: str = ANALYSIS_SERVICE_URL,
) -> Optional[str]:
    """向 analysis_service 取"experiments 页同款"的实验图。

    走的就是 experiments 页那条链路的同一个接口
    （POST /plot/offline/v2 → _handle_plot_offline_v2 → OfflineQubitUpdater.fitData），
    因此出来的图与页面上点出来的逐字节相同。

    Args:
        dataset_id: 本次实验命中的离线数据集 id（来自模拟器，经工具层透传）
        experiment_name: 实验 key，用于取 defaultPlotCommand

    Returns:
        Base64 data URL（"data:image/png;base64,..."）；任何失败都返回 None，
        由调用方降级 —— 画不出图不应该让整轮对话失败。
    """
    if not dataset_id:
        return None

    # 命令取自已确认的配置。模板里的 {exp_num} 是 DataVault 序号，
    # 而这里只有 dataset_id，v2 会先 loadDatasetById 再执行命令，
    # 所以把 {exp_num} 参数整个去掉（qter.fitData({exp_num}) -> qter.fitData()）。
    # xeb 那种命令里 {exp_num} 出现在列表参数中，去掉后会语法错误，
    # 这种情形回退到 v2 自己的默认命令。
    command = _load_experiment_plot_commands().get(experiment_name or "", "")
    if "{exp_num}" in command:
        candidate = command.replace("({exp_num})", "()").replace("({exp_num},", "(")
        command = candidate if "{exp_num}" not in candidate else ""
    if not command:
        command = "qter.fitData(do_plot=True)"

    result = _http_call(
        f"{analysis_service_url}/plot/offline/v2",
        {"dataset_id": dataset_id, "command": command},
        "POST",
    )
    if not result.get("success"):
        return None
    image = result.get("image")
    return image if isinstance(image, str) and image.startswith("data:image") else None


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
        # 必须把响应体带出来 —— 服务的 500 里通常写了真实原因（base_service 会
        # send_json_response(500, {"error": str(e)})），只报状态码会让问题无法定位。
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        detail = f"HTTP {e.code}: {e.reason}"
        if body:
            detail += f" | body={body[:800]}"
        return {"error": detail}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def setup_agent_tools(
    registry: ToolRegistry,
    quantum_service_url: str = QUANTUM_SERVICE_URL
) -> ToolRegistry:
    """设置 agent_service 的工具到注册中心

    通过 HTTP 调用 quantum_service 执行实验，实现与设备的解耦。

    Args:
        registry: 工具注册中心实例
        quantum_service_url: quantum_service 地址

    Returns:
        注册了工具的 ToolRegistry 实例
    """

    def is_connected() -> bool:
        """检查 quantum_service 是否可用"""
        result = _http_call(f"{quantum_service_url}/health")
        return result.get("labrad_connected", False) or result.get("status") == "healthy"

    def get_qubits() -> List[Dict[str, Any]]:
        """获取量子比特列表"""
        result = _http_call(f"{quantum_service_url}/qubits")
        if "qubits" in result:
            return result["qubits"]
        return []

    def get_qubit_params(name: str) -> Optional[Dict[str, Any]]:
        """获取量子比特参数"""
        result = _http_call(
            f"{quantum_service_url}/qubit/params",
            {"name": name},
            "POST"
        )
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
        """执行代码"""
        result = _http_call(
            f"{quantum_service_url}/execute",
            {"code": code},
            "POST"
        )
        # 离线模式返回 offline_simulated，同样视为执行成功
        if result.get("status") in ("success", "offline_simulated"):
            return result
        return {"error": result.get("error", "Unknown error")}

    def build_experiment_code(name: str, target: str, params: Dict[str, Any]) -> str:
        """按 config/experiment_configs.json 的 defaultCommand 模板生成实验代码。

        模板形如 "sq.t1({qubit}, do_plot=True)"，是该配置文件里的权威调用约定。
        旧实现生成的是 "sq.t1(target='q10lu12')"，有两个问题：
          1. qubit 传成了关键字参数，而离线模拟器的解析正则期望**位置参数**，
             会把 "target='q10lu12'" 整串当成 qubit 名
          2. 函数名硬拼 "sq.<name>"，对 ramsey 是错的（模板里是 sq.ramsey_df）
        """
        template = _load_experiment_templates().get(name)
        qubit_arg = target or ""
        if not template:
            # 模板缺失时回退，仍保持位置参数 + do_plot 的形式
            template = f"sq.{name}({{qubit}}, do_plot=True)"

        code = template.replace("{qubit}", qubit_arg)

        # 追加调用方额外传入的参数（模板里已出现的键不重复加）
        extra = {
            k: v for k, v in (params or {}).items()
            if k != "target" and f"{k}=" not in code
        }
        if extra and code.endswith(")"):
            arg_str = ", ".join(f"{k}={v!r}" for k, v in extra.items())
            code = code[:-1] + f", {arg_str})"
        return code

    def list_history(last_n: int, type: str = None) -> List[Dict[str, Any]]:
        """查询实验历史"""
        # 通过 quantum_service 获取
        return []

    # 注册工具
    registry.register_tool(GetQubitsTool(get_qubits))
    registry.register_tool(GetQubitParamsTool(get_qubit_params, get_qubits))
    registry.register_tool(ListExperimentsTool(list_experiments))
    registry.register_tool(RunCodeTool(execute_code, is_connected))
    # 注意 RunExperimentTool 的契约是：
    #   code_builder(name, params) -> 代码字符串
    #   executor(代码字符串) -> 执行结果
    # 旧实现把 run_experiment 当成 code_builder 传进来，而它返回的是结果 dict，
    # 于是 executor 把 dict 当代码 POST 给 quantum_service，服务端 code[:100]
    # 直接抛 "unhashable type: 'slice'"。
    # 现在传真正的代码构造器，由 RunExperimentTool 负责把执行结果包装成
    # ExperimentResult（离线字段映射见 quantum_tools/execution.py）。
    registry.register_tool(RunExperimentTool(
        lambda name, params: build_experiment_code(
            name, (params or {}).get("target"), params or {}
        ),
        execute_code,
        is_connected=is_connected
    ))
    registry.register_tool(ListHistoryTool(list_history))

    return registry


class AgentServiceAdapter:
    """Agent Service 适配器

    将 agent_service 工具适配到统一的 ToolRegistry。
    通过 HTTP 调用 quantum_service。
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
        setup_agent_tools(self._registry, self._quantum_service_url)

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
