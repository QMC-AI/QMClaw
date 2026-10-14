"""
services/agent_service/server.py - Agent 服务

提供量子智能体功能：
- QuantumAgent - 量子实验执行智能体
- ReAct 推理引擎
- 工具注册和管理
- 统一量子测控工具集成
"""

import json
import time
import threading
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config
from ..common.config import load_system_config

# 记忆与反思（由 scripts/ 移植而来，磁盘格式与旧版一致）
from ..common import memory_store
from ..common import reflection_engine

# 导入统一量子工具
from ..common.quantum_tools import ToolRegistry, ToolResult
from .adapter import AgentServiceAdapter, setup_agent_tools, fetch_experiment_plot


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[agent_service] {msg}")


# ── L0 工作记忆配置（P2-T5）───────────────────────────────────────────────────
#
# config/system.json 的 "memory" 块；块缺失 / 字段缺失 / 非法值一律回退下面这套
# 默认值 —— 配置文件坏掉绝不能把 agent 服务带崩。

WORKING_MEMORY_DEFAULTS: Dict[str, int] = {
    "window_size": 10,       # 除第 1 轮外，额外保留的最近轮数
    "summary_threshold": 20,  # 溢出折叠阈值（保留项，见 _wm_history_messages）
    "session_ttl": 86400,    # 会话空闲多久后惰性淘汰（秒）
}


def _positive_int(value: Any, default: int) -> int:
    """配置值收敛成正整数；bool / 非数字 / <=0 一律回退默认值。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if isinstance(value, float):
        return int(value) if value > 0 else default
    if isinstance(value, str):
        try:
            n = int(value.strip())
        except (TypeError, ValueError):
            return default
        return n if n > 0 else default
    return default


def load_memory_config(raw: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """读取工作记忆配置。

    raw 为 None 时读 config/system.json；显式传入 dict 时只用它（便于测试与复用）。
    任何异常都收敛成默认值，不向上抛。
    """
    if raw is None:
        try:
            raw = load_system_config()
        except Exception as e:  # 配置读取失败 → 默认值
            _log(f"Working memory: failed to load system config, using defaults: {e}")
            raw = {}
    block = raw.get("memory") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
    return {k: _positive_int(block.get(k), v) for k, v in WORKING_MEMORY_DEFAULTS.items()}


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AgentMessage:
    """智能体消息"""
    role: str  # system, user, assistant
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentTask:
    """智能体任务"""
    task_id: str
    message: str
    mode: str = "react"
    model: str = "deepseek-flash"
    context: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    messages: List[AgentMessage] = field(default_factory=list)
    # ReAct 每一轮的结构化记录，最终写入记忆 episode 的 steps
    steps: List[Dict[str, Any]] = field(default_factory=list)


class Tool:
    """工具基类"""
    name: str = ""
    description: str = ""

    def execute(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


class QuantumExperimentTool(Tool):
    """量子实验工具"""

    def __init__(self, executor):
        self.name = "run_experiment"
        self.description = "Execute a quantum experiment. Parameters: code (experiment code), qubit (qubit name)"
        self._executor = executor

    def execute(self, code: str, qubit: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """执行量子实验"""
        try:
            # 调用测控服务
            result = self._executor(code)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}


class GetQubitsTool(Tool):
    """获取量子比特工具"""

    def __init__(self, qubit_getter):
        self.name = "get_qubits"
        self.description = "Get list of available qubits with their parameters. No parameters needed."
        self._getter = qubit_getter

    def execute(self, **kwargs) -> Dict[str, Any]:
        """获取量子比特列表"""
        try:
            qubits = self._getter()
            return {"success": True, "qubits": qubits}
        except Exception as e:
            return {"success": False, "error": str(e)}


class ListExperimentsTool(Tool):
    """列出实验工具"""

    def __init__(self, experiment_lister):
        self.name = "list_experiments"
        self.description = "List available quantum experiments. Parameters: filter (optional experiment name filter)"
        self._lister = experiment_lister

    def execute(self, filter: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """列出实验"""
        try:
            experiments = self._lister()
            if filter:
                experiments = [e for e in experiments if filter.lower() in e["name"].lower()]
            return {"success": True, "experiments": experiments}
        except Exception as e:
            return {"success": False, "error": str(e)}


class AgentService(BaseService):
    """Agent 服务

    核心功能:
    - QuantumAgent - 量子实验执行智能体
    - ReAct 推理引擎
    - 工具注册和管理
    - 统一量子测控工具（通过 adapter 集成）
    """

    def __init__(self, port: int = 3005):
        cfg = ServiceConfig(
            name="agent_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # 统一工具适配器
        self._adapter = AgentServiceAdapter()

        # 统一工具注册中心
        self._registry = self._adapter.get_registry()

        # 工具注册表（保留旧接口以兼容）
        self._tools: Dict[str, Tool] = {}

        # 测控服务客户端
        self._quantum_client = None

        # 任务存储
        self._tasks: Dict[str, AgentTask] = {}
        self._tasks_lock = threading.Lock()

        # LLM 服务地址
        self._llm_service_url = "http://localhost:3006"

        # 默认系统提示
        # 工具的具体参数由原生 function calling 的 schema 携带，这里只补行为约束 ——
        # 旧版本在这里逐条列工具名并教"写 sq.* 代码"，但从未教过 Action: 协议，
        # 与运行时实际依赖的协议不符，导致模型无法触发任何工具。
        self._system_prompt = """你是一个量子测控智能体，负责帮用户执行量子实验并解读结果。

工作方式:
1. 用户要求"运行/执行某个实验"时，调用 run_experiment 工具
   - experiment_name: 取自 list_experiments 的实验名（例如 t1、ramsey、s21、spectroscopy）
   - target: 用户指定的量子比特名（例如 q10lu12）
2. 需要确认有哪些比特或实验时，调用 get_qubits / list_experiments
3. 工具返回后，用中文简要说明这次实验做了什么、关键数值是多少
4. 如果工具结果标注了"合成数据"或"离线模拟"，必须在回复里如实告知用户，
   不要把它当成真实硬件测量结果

注意:
- 不要自己编写 sq.* 代码，一律通过 run_experiment 工具执行
- 量子比特名称形如 q10lu12（q + 行列号），实验名用小写
"""

        # quantum_service 地址（通过 HTTP 调用）
        self._quantum_service_url = "http://localhost:3003"

        # L0 工作记忆（跨轮会话窗口）：session_id -> {"turns": [...], "updated_at": ts}
        # 纯进程内存，与 L1（memory_store 落盘的 episodes/skills）互不影响；
        # TTL 到期只在下次访问该会话时惰性淘汰，不启后台线程。
        self._working_memory: Dict[str, Dict[str, Any]] = {}
        self._wm_lock = threading.Lock()
        self._memory_config = load_memory_config()

        # 初始化 adapter（通过 HTTP 调用 quantum_service）
        self._adapter = AgentServiceAdapter(self._quantum_service_url)
        self._registry = self._adapter.get_registry()

        _log(
            "Working memory (L0) enabled: "
            f"window_size={self._memory_config['window_size']}, "
            f"summary_threshold={self._memory_config['summary_threshold']}, "
            f"session_ttl={self._memory_config['session_ttl']}s"
        )
        _log("Agent service initialized with unified quantum tools (HTTP to quantum_service)")

    def register_tool(self, tool: Tool):
        """注册工具"""
        self._tools[tool.name] = tool
        _log(f"Registered tool: {tool.name}")

    def _call_llm(self, messages: List[Dict], model: str = "deepseek-flash",
                  temperature: float = 0.7,
                  tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """调用 LLM 服务

        tools 非空时走原生 function calling；llm_service 会把响应里的 tool_calls
        归一化成 [{id, name, arguments(dict)}] 并附带 assistant_message 供回灌。
        """
        import urllib.request
        import urllib.error

        payload = {
            "messages": messages,
            "model": model,
            "temperature": temperature,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self._llm_service_url}/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def _compact_for_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
        """压缩工具结果再喂给 LLM：曲线数组只留点数，避免白烧 token"""
        try:
            p = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            return payload
        d = p.get("data")
        if isinstance(d, dict):
            arrays = d.get("arrays")
            if isinstance(arrays, dict):
                d["arrays"] = {
                    k: (f"<{len(v)} points, omitted>" if isinstance(v, list) else v)
                    for k, v in arrays.items()
                }
        return p

    @staticmethod
    def _summarize_tool_result(payload: Dict[str, Any]) -> str:
        """生成给人看的工具结果摘要（写入 steps[].observation）"""
        if not payload.get("success"):
            return f"失败: {payload.get('error')}"

        d = payload.get("data")
        meta = payload.get("metadata") or {}
        bits: List[str] = []

        # 不同工具的 data 形状不同：实验类返回 dict，get_qubits/list_experiments
        # 返回的是 list。这里必须按类型分派，否则 list.get 会直接抛异常。
        if isinstance(d, list):
            bits.append(f"返回 {len(d)} 项")
        elif isinstance(d, dict):
            bits.append(f"status={d.get('status') or 'ok'}")
            if d.get("target"):
                bits.append(f"target={d['target']}")
            results = d.get("results") or {}
            if results:
                bits.append("metrics=" + json.dumps(results, ensure_ascii=False))
            if d.get("error"):
                bits.append(f"error={d['error']}")
        elif d is not None:
            bits.append(str(d)[:200])

        if meta.get("synthetic"):
            bits.append("警告: 该 qubit 无历史数据，以下为合成数据")
        elif meta.get("simulated"):
            bits.append("(离线模拟)")

        stdout = (meta.get("stdout") or "").strip()
        if stdout:
            tail = [ln for ln in stdout.splitlines() if ln.strip()][-1:]
            if tail:
                bits.append(tail[0])

        return " | ".join(bits) if bits else "ok"

    # metrics 的 key -> (前端显示标签, 缩放系数)
    # 前端 AgentChatPanel 渲染 results 时走 v.toFixed(4)，因此必须换算成可读量级 ——
    # 否则 T1=5.56e-5（秒）会被显示成 "0.0001"，精度全丢。
    _METRIC_DISPLAY = {
        "t1": ("T1 (µs)", 1e6),
        "t2": ("T2 (µs)", 1e6),
        "peak_freq": ("峰值频率 (GHz)", 1e-9),
        "peak_value": ("峰值幅度", 1.0),
        "gate_fidelity": ("门保真度", 1.0),
        "average_fidelity": ("平均保真度", 1.0),
        "swap_fidelity": ("SWAP 保真度", 1.0),
        "error_per_cycle": ("单周期误差", 1.0),
        "separation": ("IQ 分离度", 1.0),
        "pi_amplitude": ("π 脉冲幅度", 1.0),
        "optimal_drag": ("最优 DRAG", 1.0),
    }

    @staticmethod
    def _finalize_response(result: Dict[str, Any]) -> str:
        """组装最终回复文本。

        合成数据时**强制**补一条提示，而不是依赖模型自觉说明 ——
        这是给用户看的关键免责信息。
        """
        text = (result.get("response") or "").strip()
        if result.get("synthetic") and "合成" not in text:
            warn = ("⚠️ 该 qubit 在离线数据中没有对应的实验历史记录，"
                    "以下结果由模拟器按通用参数合成，仅供流程验证，不代表真实硬件测量。")
            text = f"{warn}\n\n{text}" if text else warn
        return text

    @classmethod
    def _to_display_results(cls, metrics: Dict[str, Any]) -> Dict[str, float]:
        """把工具返回的 metrics 换算成前端可直接显示的量"""
        out: Dict[str, float] = {}
        for k, v in (metrics or {}).items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            label, scale = cls._METRIC_DISPLAY.get(k, (k, 1.0))
            try:
                out[label] = round(float(v) * scale, 6)
            except Exception:
                continue
        return out

    # ── L0 工作记忆（跨轮会话窗口 + 溢出压缩 + TTL）──────────────────────────
    #
    # 只保存最近若干轮的 user/assistant 原文（纯内存），供 _react_reason 回灌到
    # system 与当前 user 之间。L1 落盘记忆（episodes/skills）完全不受影响。

    def _wm_session_id(self, task: AgentTask) -> str:
        """会话键：与 _record_episode 保持一致，context.session_id 优先，退回 task_id。"""
        ctx = task.context or {}
        return ctx.get("session_id") or task.task_id

    def _wm_evict_stale(self, session_id: str) -> None:
        """TTL 惰性淘汰：只在该会话被访问时检查，顺带清掉其它已过期的会话。

        不启后台线程：淘汰完全由访问触发，避免会话表在长跑进程里无限增长。
        """
        ttl = self._memory_config["session_ttl"]
        now = time.time()
        with self._wm_lock:
            stale = [sid for sid, e in self._working_memory.items()
                     if (now - (e.get("updated_at") or 0)) > ttl]
            for sid in stale:
                del self._working_memory[sid]
        for sid in stale:
            _log(f"Working memory: evicted stale session {sid} (ttl={ttl}s, accessed={session_id})")

    def _wm_get_turns(self, session_id: str) -> List[Dict[str, str]]:
        """取该会话的历史轮次（副本）；过期的会话先被惰性淘汰。"""
        self._wm_evict_stale(session_id)
        with self._wm_lock:
            entry = self._working_memory.get(session_id)
            if not entry:
                return []
            return [dict(t) for t in (entry.get("turns") or [])]

    @staticmethod
    def _wm_round_text(round_turns: List[Dict[str, str]]) -> str:
        """一轮压成一行：user → assistant，换行拍平，最多 120 字符。"""
        text = " → ".join(
            (t.get("content") or "").strip()
            for t in round_turns
            if (t.get("content") or "").strip()
        )
        return text.replace("\n", " ")[:120]

    def _wm_history_messages(self, session_id: str) -> List[Dict[str, str]]:
        """把历史轮次转成可注入 messages 的片段。

        窗口规则：保留第 1 轮 + 最近 window_size 轮；中间部分折叠成**一条**
        role=system 的压缩消息（纯截断，不调 LLM）。
        """
        turns = self._wm_get_turns(session_id)
        if not turns:
            return []

        # turns = [user, assistant, user, assistant, ...] —— 每 2 条一轮
        rounds = [turns[i:i + 2] for i in range(0, len(turns), 2)]
        window = self._memory_config["window_size"]

        # 第 1 轮 + 最近 window 轮已经覆盖全部 → 没有中间部分可折叠
        if len(rounds) <= window + 1:
            return [dict(t) for r in rounds for t in r]

        head = rounds[0]
        tail = rounds[len(rounds) - window:]
        middle = rounds[1:len(rounds) - window]

        out: List[Dict[str, str]] = [dict(t) for t in head]
        out.append({
            "role": "system",
            "content": f"[记忆压缩] 此前 {len(middle)} 轮："
                       + " | ".join(self._wm_round_text(r) for r in middle),
        })
        for r in tail:
            out.extend(dict(t) for t in r)
        _log(f"Working memory: session {session_id} compressed "
             f"({len(rounds)} rounds -> keep first + last {window}, fold {len(middle)})")
        return out

    def _wm_append_turn(self, session_id: str, user_content: str,
                        assistant_content: str) -> None:
        """一轮成功结束后追加 user/assistant 两条并刷新 updated_at。"""
        self._wm_evict_stale(session_id)
        now = time.time()
        with self._wm_lock:
            entry = self._working_memory.get(session_id)
            if entry is None:
                entry = {"turns": [], "updated_at": now}
                self._working_memory[session_id] = entry
            entry["turns"].append({"role": "user", "content": user_content or ""})
            entry["turns"].append({"role": "assistant", "content": assistant_content or ""})
            entry["updated_at"] = now

    def _react_reason(self, task: AgentTask) -> Dict[str, Any]:
        """ReAct 推理循环（原生 function calling 版本）

        旧实现依赖文本 "Action: tool(args)" 协议 + eval 解析参数，但 system prompt
        从未教会模型这个格式 —— 模型只会吐它原生的 tool-call 标记，解析不到就被当成
        最终答案返回，导致工具从不执行。现改为把 registry 里的 OpenAI 工具定义直接
        交给模型，用响应中的 tool_calls 驱动循环。
        """
        max_iterations = 10
        iteration = 0

        tools = self._registry.get_definitions()

        # L0 工作记忆：同会话的历史轮次插在 system 与当前 user 之间
        session_id = self._wm_session_id(task)
        history = self._wm_history_messages(session_id)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt},
        ]
        messages.extend(history)
        round_start = len(messages)   # 历史结束处：_final 只回报本轮的 user/assistant
        messages.append({"role": "user", "content": task.message})

        # 汇集给前端的结果（steps 已在 task.steps 里，这里收数值与图表）
        collected_metrics: Dict[str, Any] = {}
        collected_charts: List[str] = []
        # 命中离线数据集时的 dataset_id：随 episode 落盘，事后可复原"这轮用的是哪份数据"
        collected_dataset_id: Optional[str] = None
        saw_synthetic = False
        saw_simulated = False

        def _final(success: bool, **extra) -> Dict[str, Any]:
            return {
                "success": success,
                "iterations": iteration,
                "messages": [{"role": m.get("role"), "content": m.get("content") or ""}
                             for m in messages[round_start:]
                             if m.get("role") in ("user", "assistant")],
                "metrics": collected_metrics,
                "charts": collected_charts,
                "dataset_id": collected_dataset_id,
                "synthetic": saw_synthetic,
                "simulated": saw_simulated,
                **extra,
            }

        while iteration < max_iterations:
            iteration += 1
            _log(f"ReAct iteration {iteration}/{max_iterations}")

            response = self._call_llm(messages, model=task.model, tools=tools)
            if response.get("error"):
                task.steps.append({"tool": "(llm)", "input": {}, "observation": response["error"]})
                return _final(False, error=response["error"])

            content = response.get("content") or ""
            tool_calls = response.get("tool_calls") or []

            if not tool_calls:
                # 没有工具调用 —— 这就是最终回复
                task.messages.append(AgentMessage(role="assistant", content=content))
                # 一轮成功结束：user/assistant 轮写入 L0 工作记忆（失败不写）
                self._wm_append_turn(session_id, task.message, content)
                return _final(True, response=content)

            _log(f"Model requested {len(tool_calls)} tool call(s)")

            # 协议要求：带 tool_calls 的 assistant 消息必须原样回灌，
            # 其后紧跟 role="tool" 的结果消息
            assistant_msg = response.get("assistant_message")
            if not assistant_msg:
                assistant_msg = {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": [
                        {
                            "id": tc.get("id") or "",
                            "type": "function",
                            "function": {
                                "name": tc.get("name") or "",
                                "arguments": json.dumps(tc.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            messages.append(assistant_msg)
            task.messages.append(AgentMessage(role="assistant", content=content))

            for tc in tool_calls:
                name = tc.get("name") or ""
                args = tc.get("arguments") or {}

                if tc.get("parse_error"):
                    payload: Dict[str, Any] = {
                        "success": False,
                        "error": f"Invalid tool arguments JSON: {tc['parse_error']}",
                        "arguments_raw": tc.get("arguments_raw"),
                    }
                else:
                    _log(f"Executing tool: {name}({json.dumps(args, ensure_ascii=False)})")
                    try:
                        payload = self._registry.execute(name, args).to_dict()
                    except Exception as e:
                        payload = {"success": False, "error": f"Tool execution error: {e}"}

                task.steps.append({
                    "tool": name,
                    "input": args,
                    "observation": self._summarize_tool_result(payload),
                })

                # 汇集数值与图表（供前端 results / charts）
                if payload.get("success"):
                    d = payload.get("data") or {}
                    meta = payload.get("metadata") or {}
                    if isinstance(meta, dict) and meta.get("dataset_id"):
                        collected_dataset_id = meta.get("dataset_id")
                    if isinstance(d, dict):
                        metrics = d.get("results")
                        if isinstance(metrics, dict) and metrics:
                            collected_metrics.update(metrics)

                        # 本次工具调用自己产出的图（离线模拟器自绘的那张）
                        tool_plots: List[str] = []
                        for p in (d.get("plots") or []):
                            path = p.get("path") if isinstance(p, dict) else p
                            if path:
                                tool_plots.append(path)

                        # 命中真实离线数据集时，改向 analysis_service 要
                        # "experiments 页同款"的图 —— 同一个接口
                        # (/plot/offline/v2)、同一套绘图代码，出来的图逐字节相同。
                        # 在线（data_source=labrad）时改走在线绘图端点，避免
                        # "已连真机仍展示离线图"。合成数据没有 dataset_id，取不到；
                        # 任何失败也返回 None，此时退回模拟器那张。
                        experiments_chart = None
                        if name == "run_experiment":
                            online = meta.get("source") == "labrad" or meta.get("data_source") == "labrad"
                            offline_hit = meta.get("dataset_id") and not meta.get("synthetic")
                            if online or offline_hit:
                                experiments_chart = fetch_experiment_plot(
                                    meta.get("dataset_id"),
                                    d.get("experiment_name") or "",
                                )

                        for path in ([experiments_chart] if experiments_chart else tool_plots):
                            if path not in collected_charts:
                                collected_charts.append(path)
                    if meta.get("synthetic"):
                        saw_synthetic = True
                    if meta.get("simulated"):
                        saw_simulated = True

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id") or "",
                    "content": json.dumps(self._compact_for_llm(payload), ensure_ascii=False),
                })

        return _final(False, error="Max iterations reached")

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理 Agent 请求"""
        # API v1 路由
        if path.startswith("/api/v1/"):
            return self._handle_api_v1(path, method, data)

        # 路由
        if path == "/health":
            return self._handle_health()
        elif path == "/chat":
            return self._handle_chat(data)
        elif path == "/chat/stream":
            return self._handle_chat_stream(data)
        elif path == "/tasks":
            return self._handle_tasks(query)
        elif path == "/tasks/status":
            return self._handle_task_status(data)
        elif path == "/tools":
            return self._handle_tools()
        elif path == "/memory" or path.startswith("/memory/"):
            # 记忆与反思。Express 侧 /api/agent/memory/* 会原样代理到这里。
            return self._handle_memory(path, method, data, query)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_api_v1(self, path: str, method: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理 API v1 请求"""
        import uuid

        # /api/v1/tools - 列出所有工具
        if path == "/api/v1/tools" and method == "GET":
            return {
                "success": True,
                "data": self._registry.get_definitions(),
                "count": len(self._registry.list_tools()),
                "request_id": f"req_{uuid.uuid4().hex[:12]}",
            }

        # /api/v1/tools/{name} - 获取工具 schema
        if path.startswith("/api/v1/tools/") and method == "GET":
            tool_name = path.split("/api/v1/tools/")[1]
            schema = self._registry.get_schema(tool_name)
            if schema:
                return {
                    "success": True,
                    "data": schema,
                    "request_id": f"req_{uuid.uuid4().hex[:12]}",
                }
            return {
                "success": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": f"Tool '{tool_name}' not found",
                },
            }

        # /api/v1/tools/{name} - 执行工具
        if path.startswith("/api/v1/tools/") and method == "POST":
            tool_name = path.split("/api/v1/tools/")[1]
            result = self._registry.execute(tool_name, data)
            return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

        # /api/v1/qubits - 获取量子比特
        if path == "/api/v1/qubits" and method == "GET":
            result = self._registry.execute("get_qubits", {})
            return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

        # /api/v1/experiments - 列出实验
        if path == "/api/v1/experiments" and method == "GET":
            result = self._registry.execute("list_experiments", {})
            return result.to_api_response(request_id=f"req_{uuid.uuid4().hex[:12]}")

        # /api/v1/health
        if path == "/api/v1/health":
            return {
                "success": True,
                "data": {
                    "status": "healthy",
                    "service": "agent_service",
                    "tool_count": len(self._registry.list_tools()),
                    "connected": self._quantum_client is not None and self._quantum_client.connected,
                },
                "request_id": f"req_{uuid.uuid4().hex[:12]}",
            }

        raise ValueError(f"Unknown API path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy",
            "service": "agent_service",
            "tool_count": len(self._tools),
            "task_count": len(self._tasks),
        }

    def _handle_chat(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理聊天请求"""
        message = data.get("message")
        base_url = data.get("base_url")
        enabled_toolsets = data.get("enabled_toolsets", [])
        session_id = data.get("session_id")
        mode = data.get("mode", "react")
        context = data.get("context", {})
        task_id = data.get("task_id", f"task_{int(time.time() * 1000)}")

        # 模型解析优先级：
        #   1. 顶层 model（legacy 后端 / 直接调用 microservice 时使用）
        #   2. context.model_id（前端 AgentChatPanel 走的就是这条）
        #   3. 默认 deepseek-flash
        # 注意：前端下拉框传的是 model_configs.json 里的内部 id（如 model_default_001），
        # 不是 API 模型名。那种 id 会原样转给 llm_service 的兜底分支，由它选可用 provider。
        model = data.get("model") or context.get("model_id") or "deepseek-flash"

        _log(f"DEBUG _handle_chat: message={message[:100] if message else None}...")
        _log(f"DEBUG _handle_chat: model={model}, base_url={base_url}, enabled_toolsets={enabled_toolsets}")
        _log(f"DEBUG _handle_chat: session_id={session_id}, mode={mode}")

        # 检查是否是 Hermes 请求（带有 base_url 或 enabled_toolsets）
        if base_url or enabled_toolsets:
            _log("Detected Hermes-style request, but Hermes is not implemented in agent_service")
            return {
                "error": "Hermes not implemented in microservice mode. Please use legacy mode or implement hermes_service.",
                "hint": "Set USE_MICROSERVICES=false in index.ts to use legacy backend"
            }

        if not message:
            return {"error": "message is required"}

        _log(f"Processing chat: task_id={task_id}, mode={mode}")

        # 创建任务
        task = AgentTask(
            task_id=task_id,
            message=message,
            mode=mode,
            model=model,
            context=context,
        )

        with self._tasks_lock:
            self._tasks[task_id] = task
            task.status = TaskStatus.RUNNING

        try:
            # 执行推理
            started_at = time.time()
            if mode == "react":
                result = self._react_reason(task)
            else:
                result = {"error": f"Unknown mode: {mode}"}

            task.status = TaskStatus.COMPLETED if result.get("success") else TaskStatus.FAILED
            task.result = result
            task.completed_at = time.time()

            payload = {
                "success": result.get("success", False),
                "task_id": task_id,
                "response": self._finalize_response(result),
                "error": result.get("error"),
                "iterations": result.get("iterations", 0),
                "messages": result.get("messages", []),
                # 前端 AgentChatPanel 期望的三个字段（此前一个都没返回，
                # 导致步骤区与结果卡片永远不渲染）
                "steps": task.steps,
                "results": self._to_display_results(result.get("metrics") or {}),
                "charts": result.get("charts") or [],
                # 本轮命中的离线数据集（合成数据/未命中为 None）：_record_episode
                # 把它写进 episode，回放时才知道这轮用的是哪份数据
                "dataset_id": result.get("dataset_id"),
            }

            # 记录这一轮对话到记忆（失败不影响返回）
            payload["duration_ms"] = int((time.time() - started_at) * 1000)
            episode_id = self._record_episode(task, payload)
            if episode_id:
                payload["episode_id"] = episode_id

            return payload

        except Exception as e:
            _log(f"Chat error: {e}\n{traceback.format_exc()}")
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            # 失败的一轮同样记入记忆，便于前端看到失败案例
            self._record_episode(task, {"success": False, "error": str(e)})
            return {"error": str(e), "task_id": task_id}

    def _handle_chat_stream(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理流式聊天（简单版本，实际使用 SSE）"""
        # 对于 HTTP 轮询模式，返回任务 ID
        message = data.get("message")
        mode = data.get("mode", "react")
        task_id = f"task_{int(time.time() * 1000)}"

        if not message:
            return {"error": "message is required"}

        # 创建任务
        task = AgentTask(
            task_id=task_id,
            message=message,
            mode=mode,
        )

        with self._tasks_lock:
            self._tasks[task_id] = task
            task.status = TaskStatus.RUNNING

        # 在后台线程执行
        def run_task():
            try:
                if mode == "react":
                    result = self._react_reason(task)
                else:
                    result = {"error": f"Unknown mode: {mode}"}

                task.status = TaskStatus.COMPLETED if result.get("success") else TaskStatus.FAILED
                task.result = result
                task.completed_at = time.time()
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.completed_at = time.time()

        thread = threading.Thread(target=run_task)
        thread.daemon = True
        thread.start()

        return {
            "task_id": task_id,
            "status": "pending",
            "streaming": True,
        }

    def _handle_tasks(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """获取任务列表"""
        status_filter = query.get("status", [None])[0] if query.get("status") else None

        with self._tasks_lock:
            tasks = []
            for task_id, task in self._tasks.items():
                if status_filter and task.status.value != status_filter:
                    continue
                tasks.append({
                    "task_id": task.task_id,
                    "message": task.message[:100],
                    "mode": task.mode,
                    "status": task.status.value,
                    "created_at": task.created_at,
                    "completed_at": task.completed_at,
                })

        return {"tasks": tasks, "count": len(tasks)}

    def _handle_task_status(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取任务状态"""
        task_id = data.get("task_id")
        if not task_id:
            return {"error": "task_id is required"}

        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return {"error": "Task not found"}

            return {
                "task_id": task.task_id,
                "status": task.status.value,
                "result": task.result,
                "error": task.error,
                "completed_at": task.completed_at,
            }

    def _handle_tools(self) -> Dict[str, Any]:
        """获取工具列表"""
        tools = []
        for name, tool in self._tools.items():
            tools.append({
                "name": tool.name,
                "description": tool.description,
            })

        # 获取统一工具定义
        unified_tools = self._registry.get_definitions()

        return {
            "tools": tools,
            "count": len(tools),
            "unified_tools": unified_tools,
            "unified_count": len(unified_tools),
        }

    # ── 记忆与反思 (Memory & Reflection) ─────────────────────────────────────
    #
    # Express 侧 /api/agent/memory/* 会原样代理到下面这些路径。
    # 数据落在 <qmclaw-server>/config/memory/；权威实现是 services/common/memory_store.py，
    # scripts/memory_store.py 已收敛为转发层（G3），两者共用同一份实现与磁盘格式。

    @staticmethod
    def _q(query: Dict[str, List[str]], key: str, default=None):
        """从 query dict（值为 list）里取单个值"""
        v = (query or {}).get(key)
        if isinstance(v, (list, tuple)):
            return v[0] if v else default
        return v if v is not None else default

    def _handle_memory(self, path: str, method: str, data: Dict[str, Any],
                       query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理 /memory/* 请求"""
        data = data or {}

        # /memory/episodes — 列表（Express 用 POST 传参）
        if path == "/memory/episodes":
            try:
                limit = int(data.get("limit") or self._q(query, "limit", 50))
            except (TypeError, ValueError):
                limit = 50
            qubit = data.get("qubit") or self._q(query, "qubit")
            status = data.get("status") or self._q(query, "status")
            episodes = memory_store.list_episodes(limit=limit, qubit=qubit, status=status)
            return {"success": True, "episodes": episodes, "count": len(episodes)}

        # /memory/episodes/{id} — 详情 / 归档
        if path.startswith("/memory/episodes/"):
            episode_id = path[len("/memory/episodes/"):].strip("/")
            if method == "DELETE":
                ok = memory_store.archive_episode(episode_id)
                return {"success": ok, "episode_id": episode_id, "archived": ok}
            episode = memory_store.get_episode(episode_id)
            if not episode:
                return {"success": False, "error": f"Episode not found: {episode_id}"}
            return {"success": True, "episode": episode}

        # /memory/sessions — 会话列表（POST {"limit":50}）
        if path == "/memory/sessions":
            try:
                limit = int(data.get("limit") or self._q(query, "limit", 50))
            except (TypeError, ValueError):
                limit = 50
            sessions = memory_store.list_sessions(limit=limit)
            _log(f"Memory: listed {len(sessions)} sessions (limit={limit})")
            return {"success": True, "count": len(sessions), "sessions": sessions}

        # /memory/sessions/{id} — 会话回放：完整 episode + 拼好的对话消息
        if path.startswith("/memory/sessions/"):
            session_id = path[len("/memory/sessions/"):].strip("/")
            episodes = memory_store.get_session_episodes(session_id)
            if not episodes:
                _log(f"Memory: session not found: {session_id}")
                return {"success": False, "error": f"Session not found: {session_id}"}
            messages = []
            for ep in episodes:
                ts = ep.get("timestamp")
                messages.append({
                    "role": "user",
                    "content": ep.get("task"),
                    "timestamp": ts,
                })
                messages.append({
                    "role": "assistant",
                    "content": ep.get("response"),
                    "steps": ep.get("steps") or [],
                    "results": ep.get("metrics") or {},
                    # 图表随消息回放（string 数组，老数据无此键 → []），
                    # 前端 sessionToMessages 直接映射，刷新/切会话后历史图不丢
                    "charts": ep.get("charts") or [],
                    "timestamp": ts,
                })
            _log(f"Memory: session {session_id} replayed ({len(episodes)} episodes)")
            return {"success": True, "sessionId": session_id,
                    "messages": messages, "episodes": episodes}

        # /memory/skills — 列表
        if path == "/memory/skills":
            status = data.get("status") or self._q(query, "status")
            skills = memory_store.list_skills(status=status)
            return {"success": True, "skills": skills, "count": len(skills)}

        # /memory/skills/{id} — 详情 / 删除
        if path.startswith("/memory/skills/"):
            skill_id = path[len("/memory/skills/"):].strip("/")
            if method == "DELETE":
                ok = memory_store.delete_skill(skill_id)
                return {"success": ok, "skill_id": skill_id}
            skill = memory_store.get_skill(skill_id)
            if not skill:
                return {"success": False, "error": f"Skill not found: {skill_id}"}
            return {"success": True, "skill": skill}

        # /memory/stats — 统计
        if path == "/memory/stats":
            # get_memory_stats() 已含 lifecycle（P4-T7：active/archived/pruned），这里原样透传
            stats = memory_store.get_memory_stats()
            stats["success"] = True
            return stats

        # /memory/maintenance/forget — 提升/遗忘：归档超期 episode + 清理超量归档（P4-T7）
        if path == "/memory/maintenance/forget":
            try:
                archive_after_days = int(data.get("archive_after_days")
                                         if data.get("archive_after_days") is not None
                                         else self._q(query, "archive_after_days", 90))
            except (TypeError, ValueError):
                archive_after_days = 90
            try:
                max_archived = int(data.get("max_archived")
                                   if data.get("max_archived") is not None
                                   else self._q(query, "max_archived", 200))
            except (TypeError, ValueError):
                max_archived = 200
            result = memory_store.run_maintenance(
                archive_after_days=archive_after_days,
                max_archived=max_archived,
            )
            _log(f"Memory: maintenance/forget archived={result.get('archived')} "
                 f"pruned={result.get('pruned')} "
                 f"(archive_after_days={archive_after_days}, max_archived={max_archived})")
            return {"success": True, **result}

        # /memory/recall — 按任务召回相关记忆（P3-T6：分层 + 打分 + 预算）
        if path == "/memory/recall":
            task = data.get("task") or data.get("message") or ""
            qubit = data.get("qubit") or self._q(query, "qubit")
            try:
                budget_chars = int(data.get("budget_chars") or 2000)
            except (TypeError, ValueError):
                budget_chars = 2000
            try:
                recalled = reflection_engine.recall_for_task(task, qubit, budget_chars)
            except Exception as e:
                _log(f"recall failed: {e}")
                recalled = {
                    "hasMemory": False, "context": "", "error": str(e),
                    "recall_meta": {
                        "scores": [], "budget_chars": budget_chars, "used_chars": 0,
                        "truncated": [],
                        "tiers": {"skills": 0, "episodes": 0, "sessions": 0},
                    },
                }
            # recalled 里已带 recall_meta（打分/预算/截断/分层计数），这里原样透传
            return {"success": True, **recalled}

        # /memory/reflect — 对某次任务做反思（可选回填到 episode）
        if path == "/memory/reflect":
            episode_id = data.get("episode_id")
            task = data.get("task") or data.get("message") or ""
            result_data = data.get("result") or {}
            try:
                reflection = reflection_engine.reflect_after_task(
                    task=task,
                    status=result_data.get("status", "success"),
                    steps=result_data.get("steps", []),
                    metrics=result_data.get("metrics", {}),
                    qubit=data.get("qubit") or result_data.get("qubit"),
                    mode=result_data.get("mode", "react"),
                )
            except Exception as e:
                _log(f"reflect failed: {e}")
                return {"success": False, "error": str(e)}
            if episode_id:
                memory_store.update_episode_reflection(episode_id, reflection)
            return {"success": True, "reflection": reflection, "episode_id": episode_id}

        return {"success": False, "error": f"Unknown memory path: {path}"}

    @staticmethod
    def _extract_dataset_id(steps: List[Dict[str, Any]]) -> Optional[str]:
        """从本轮 steps 里尽力捞出离线数据集 id（找不到返回 None，绝不抛异常）。

        steps 目前是 {tool, input, observation}，其中 observation 是给人看的字符串
        摘要，所以多数轮次捞不到 —— 返回 None 是正常结果，不视为错误。
        将来若把 metadata（dict）或 dict 形态的 observation / result 记进 steps，
        这里无需改动即可取到。
        """
        try:
            for step in steps or []:
                if not isinstance(step, dict):
                    continue
                for key in ("metadata", "observation", "result"):
                    blob = step.get(key)
                    if isinstance(blob, dict) and blob.get("dataset_id"):
                        return str(blob["dataset_id"])
                inp = step.get("input")
                if isinstance(inp, dict) and inp.get("dataset_id"):
                    return str(inp["dataset_id"])
        except Exception:
            pass
        return None

    # 实验/执行类工具名的识别关键字（判断本轮"有没有尝试下发实验"）
    # 只认**执行类**工具（run_experiment / run_code / execute_*）：名字里出现这些片段
    # 才算"尝试下发"。只读查询（list_experiments / get_experiment_schema 等 list_/get_ 前缀）
    # 只是问了一句，压根没尝试执行，不能算。
    _EXEC_TOOL_HINTS = ("run_experiment", "run_code", "execute")
    # 工具结果里"确实放行执行"的迹象
    _EXEC_SUCCESS_HINTS = ("status=success", "offline_simulated")
    # 工具结果里"被写闸门/只读联调拦下"的迹象
    _EXEC_BLOCKED_HINTS = ("只读联调", "FORBIDDEN", "read_only")

    @classmethod
    def _experiment_outcome(cls, steps: Any, charts: Any) -> Tuple[bool, Optional[str]]:
        """本轮实验到底有没有真的执行 —— 只补口径字段，不动 status 语义。

        返回 (executed, blocked_reason)：

        * 本轮没有实验/执行类步骤（tool 名不含 run_experiment/run_code/execute）
          -> ``(False, None)`` —— 压根没尝试，不算被拦
        * 有实验类步骤 + 命中放行迹象（observation 含 ``status=success`` /
          ``offline_simulated``，或 charts 非空）-> ``(True, None)``
        * 有实验类步骤 + 命中被拦迹象（observation 含 ``只读联调`` /
          ``FORBIDDEN`` / ``read_only``）-> ``(False, "FORBIDDEN")``
        * 有尝试但判不出成功也判不出被拦 -> ``(False, "UNKNOWN")``

        判定顺序照上面四条：放行迹象优先于被拦迹象（两者同时命中时按"执行过"算，
        因为 charts 非空只可能是真跑出了图）。blocked_reason 为 None 的两种含义
        由 executed 区分：``(False, None)`` = 没尝试，``(True, None)`` = 跑成了。

        动机：写闸门关闭时 agent 侧的 run_experiment/run_code 在调用服务前就被
        拒掉，而该轮 status 仍是 success（agent 轮次确实成功结束了），metrics 里
        又只有 iterations/duration_ms —— 用户会看到"实验成功"却没有图。这里把
        "有没有真执行"单独记成一个字段，status 的取值逻辑一字未改。

        纯函数 + 全程兜底：steps / charts / observation 是脏数据时不抛异常，
        一律按"判不出"处理（返回 UNKNOWN，不影响已落盘的主干字段）。
        """
        try:
            has_exec_step = False
            saw_success = False
            saw_blocked = False

            for step in steps if isinstance(steps, (list, tuple)) else []:
                if not isinstance(step, dict):
                    continue
                tool = step.get("tool")
                if not isinstance(tool, str):
                    continue
                if not any(h in tool.lower() for h in cls._EXEC_TOOL_HINTS):
                    continue

                has_exec_step = True
                obs = step.get("observation")
                if not isinstance(obs, str):
                    # observation 非字符串（脏数据，或将来换成 dict）-> 不参与判定
                    continue
                if any(h in obs for h in cls._EXEC_BLOCKED_HINTS):
                    saw_blocked = True
                if any(h in obs for h in cls._EXEC_SUCCESS_HINTS):
                    saw_success = True

            if not has_exec_step:
                return (False, None)

            has_charts = isinstance(charts, (list, tuple)) and any(
                isinstance(c, str) and c for c in charts
            )
            if saw_success or has_charts:
                return (True, None)
            if saw_blocked:
                return (False, "FORBIDDEN")
            return (False, "UNKNOWN")
        except Exception:
            # 判不出来就报 UNKNOWN，绝不因脏数据把这一轮的落盘带崩
            return (False, "UNKNOWN")

    def _record_episode(self, task: "AgentTask", result: Dict[str, Any]) -> Optional[str]:
        """把一轮对话写入记忆。

        一轮 = 一次 /chat 调用。ReAct 的每次迭代存进该 episode 的 steps。
        写盘失败绝不能影响对话返回，所以整体 try/except 包住。
        """
        try:
            steps = getattr(task, "steps", None) or []
            status = "success" if result.get("success") else "failed"

            # 从用户消息里粗提 qubit 名，便于前端按 qubit 过滤（失败就算了）
            qubit = None
            try:
                import re as _re
                m = _re.search(r"\bq\d+[a-z]+\d+\b", task.message or "")
                if m:
                    qubit = m.group(0)
            except Exception:
                pass

            # 本轮"实验到底有没有真的执行"的口径字段（status 语义不变）：
            # 追加进 metrics，用户就不会再看到 status=success 却既没图也没解释。
            executed, blocked_reason = self._experiment_outcome(steps, result.get("charts"))

            # 会话字段（sessionId / roundIndex / model / response）随实体+索引一次写入，
            # 由 memory_store 在 _INDEX_LOCK 内原子落盘 —— 不再事后 open() 补写。
            episode = memory_store.create_episode(
                task=task.message,
                steps=steps,
                status=status,
                metrics={
                    "iterations": result.get("iterations", 0),
                    "duration_ms": result.get("duration_ms", 0),
                    "experiment_executed": executed,
                    "blocked_reason": blocked_reason,
                },
                qubit=qubit,
                tags=[],
                used_skills=[],
                mode=task.mode,
                session_id=task.context.get("session_id") or task.task_id,
                round_index=task.context.get("round_index"),
                model=getattr(task, "model", None),
                response=result.get("response", ""),
            )

            episode_id = episode.get("id")

            # 图表 / 数据集随 episode 自包含落盘：create_episode 的签名不接受这两个
            # 字段，所以用受锁的读-改-写补写。charts 保持 string 数组契约
            # （离线 data:image/png;base64,...，在线 /plots/xxx.png），供
            # GET /memory/sessions/{id} 回放与前端 sessionToMessages 复原。
            try:
                raw_charts = result.get("charts")
                if not isinstance(raw_charts, (list, tuple)):
                    raw_charts = []          # 脏数据（字符串/None/dict）当空处理，别按字符拆
                charts = [c for c in raw_charts if isinstance(c, str)]
                dataset_id = result.get("dataset_id") or self._extract_dataset_id(steps)
                memory_store.update_episode_fields(
                    episode_id, charts=charts, dataset_id=dataset_id,
                )
            except Exception as e:
                # 补写失败只丢图表，不影响已落盘的主干字段与对话返回
                _log(f"Memory: failed to backfill charts for {episode_id}: {e}")

            _log(f"Memory: recorded episode {episode_id} (status={status}, steps={len(steps)})")
            return episode_id
        except Exception as e:
            _log(f"Memory: failed to record episode: {e}")
            return None

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        return {
            "status": "healthy",
            "service": "agent_service",
            "tool_count": len(self._tools),
            "task_count": len(self._tasks),
            "llm_service": self._llm_service_url,
        }


def main():
    """主入口"""
    service = AgentService(port=3005)
    run_service(service)


if __name__ == "__main__":
    main()
