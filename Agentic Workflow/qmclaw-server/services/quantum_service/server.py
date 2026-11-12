"""
services/quantum_service/server.py - 测控执行服务

提供量子测控实验的执行接口：
- LabRAD 连接管理
- 实验执行
- Qubit 管理
- Session 切换
"""

import json
import time
import threading
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 必须在任何 pyplot / lqms 导入之前固定非交互后端。
# 本机 matplotlib 默认后端是 qtagg；在线执行路径里实验函数带 do_plot=True 时，
# 会在工作线程里拉起 Qt GUI，把整个服务卡死（进程仍 LISTENING，但 accept 不再响应）。
# 2026-11-03 q2ld3 iqraw 实测：do_plot=False 只要 3.2 s，do_plot=True 请求 400 s 未返回。
import matplotlib

matplotlib.use("Agg", force=True)

from ..base import BaseService, ServiceConfig, run_service, _safe_print, log_stats
from ..common import setup_logging, config, FallbackManager, ConnectionState
from ..common.errors import error_envelope, normalize_error
from ..common.availability import build_availability, derive_legacy_status
from ..common.offline_data_provider import OfflineDataProvider
from ..common.plotting import serialized_plot
from .offline_simulator import ExperimentSimulator


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[quantum_service] {msg}")


class QuantumService(BaseService):
    """测控执行服务

    核心功能:
    - LabRAD 连接管理
    - 实验代码执行（带 busy 锁，防止并发）
    - Qubit 参数查询
    - Session 切换
    - 离线模式支持
    """

    # 运行模式枚举
    class Mode:
        ONLINE = "online"      # 强制使用 LabRAD
        OFFLINE = "offline"    # 强制使用离线数据
        AUTO = "auto"          # 自动切换（LabRAD断开时自动离线）

    def __init__(self, port: int = 3003):
        cfg = ServiceConfig(
            name="quantum_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # LabRAD 客户端
        self._labrad: Optional[Any] = None
        self._labrad_lock = threading.Lock()

        # 执行锁（防止并发执行实验）
        self._busy = threading.Event()
        self._busy_lock = threading.Lock()
        self._current_task_id: Optional[str] = None

        # 执行超时后标记卡死：守护线程可能仍在真机上跑，必须拒绝后续写操作
        self._exec_stuck: Optional[str] = None

        # 单写者内核（阶段 2 第三批）：唯一 worker 线程消费所有硬件操作。
        # 默认 shadow 模式——只观测，不改变现有执行路径（不打断用户正在跑的真机实验）。
        from services.quantum_service.writer import SingleWriter

        self._writer = SingleWriter()
        self._writer.start()

        # 执行中的任务
        self._running_tasks: Dict[str, Dict[str, Any]] = {}

        # 初始化状态
        self._init_started = False

        # 降级模式管理
        config_path = Path(__file__).parent.parent.parent / "config" / "fallback_config.json"
        fb_config = FallbackManager.load_fallback_config(config_path).get("quantum_service", {})
        self._fallback = FallbackManager(
            service_name="quantum_service",
            config_path=config_path,
            fallback_data_path=fb_config.get("fallback_data_path", "data/fallback/quantum"),
            max_retry_attempts=fb_config.get("retry", {}).get("max_attempts", 5),
            base_retry_delay=fb_config.get("retry", {}).get("base_delay", 2),
            max_retry_delay=fb_config.get("retry", {}).get("max_delay", 30),
        )

        # 降级模式下的缓存数据
        self._fallback_qubits: List[Dict[str, Any]] = []
        self._fallback_experiments: List[Dict[str, Any]] = []

        # 离线模式管理
        offline_config = fb_config.get("offline_mode", {})
        self._offline_mode_enabled = offline_config.get("enabled", True)
        self._offline_data_path = offline_config.get("data_path", "data/offline_data")
        self._offline_auto_switch = offline_config.get("auto_switch_on_disconnect", True)

        # 运行模式：online/offline/auto，从配置读取默认值
        default_mode = offline_config.get("default_mode", "auto")
        self._mode = default_mode if default_mode in (self.Mode.ONLINE, self.Mode.OFFLINE, self.Mode.AUTO) else self.Mode.AUTO
        self._mode_lock = threading.Lock()

        # 离线数据提供器
        self._offline_provider: Optional[OfflineDataProvider] = None

        # 实验模拟器（离线模式使用）
        self._simulator: Optional[ExperimentSimulator] = None

        # 异步执行任务（job）：/execute 带 async=true 时立即返回 job_id
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        self._max_jobs = int(__import__("os").environ.get("QMCLAW_MAX_JOBS", "100"))

        # 单消费者通道：硬件通道并发度恒为 1，撞车时排队而不是报 busy
        self._job_queue = __import__("collections").deque()
        self._job_cond = threading.Condition()
        self._lane_stop = threading.Event()
        self._lane_thread: Optional[threading.Thread] = None
        self._job_events: Dict[str, threading.Event] = {}

    def _start_lane(self) -> None:
        if self._lane_thread and self._lane_thread.is_alive():
            return
        self._lane_stop.clear()
        self._lane_thread = threading.Thread(
            target=self._lane_worker, name="qm-hw-lane", daemon=True
        )
        self._lane_thread.start()

    def _is_busy(self) -> bool:
        """检查是否正在执行任务"""
        return self._busy.is_set()

    def _try_acquire_busy(self, task_id: str) -> bool:
        """尝试获取执行锁"""
        with self._busy_lock:
            if self._busy.is_set():
                return False
            self._busy.set()
            self._current_task_id = task_id
            return True

    def _release_busy(self):
        """释放执行锁"""
        with self._busy_lock:
            self._busy.clear()
            self._current_task_id = None

    def before_start(self):
        """启动时初始化 - LabRAD 连接失败时进入降级模式"""
        _log("Initializing Quantum service...")

        # 加载降级数据
        self._load_fallback_data()

        # 硬件通道 worker：所有 job（含同步 /execute）都由它逐条执行
        self._start_lane()

        # 初始化离线数据提供器
        self._init_offline_provider()

        # 打印当前模式
        _log(f"Mode: {self._get_mode()}")

        # 离线模式下跳过 LabRAD 连接尝试
        if self._is_offline_mode:
            _log("Offline mode: skipping LabRAD connection")
            self._fallback.set_state(ConnectionState.DEGRADED_PERMANENT)
            return

        # 延迟导入避免启动时卡住
        try:
            from .labrad_client import LabRADClient
            self._labrad = LabRADClient()
            _log("LabRADClient created")

            # 启动时同步连接 LabRAD
            _log("Connecting to LabRAD...")
            self._fallback.set_state(ConnectionState.CONNECTING)
            success = self._labrad.initialize()
            if success:
                _log("LabRAD connected successfully")
                self._init_started = True
                self._fallback.on_connection_success()
            else:
                _log(f"LabRAD connection failed: {self._labrad._init_error}")
                self._init_started = False
                self._fallback.on_connection_failed(self._labrad._init_error)
                # 不抛出异常，而是进入降级模式并启动重连
                self._fallback.start_reconnect_timer(self._do_reconnect)

        except Exception as e:
            _log(f"Failed to initialize Quantum service: {e}")
            self._init_started = False
            self._fallback.on_connection_failed(str(e))
            self._fallback.start_reconnect_timer(self._do_reconnect)

    def _do_reconnect(self) -> bool:
        """执行重连"""
        # 离线模式或自动模式下 LabRAD 不可用时，跳过重连
        # 只有在强制在线模式(ONLINE)时才继续重连
        with self._mode_lock:
            if self._mode == self.Mode.OFFLINE:
                _log("Skipping reconnect: offline mode enabled")
                self._fallback._cancel_retry_timer()
                return False
            if self._mode == self.Mode.AUTO and not self._labrad:
                # AUTO 模式下且从未成功连接过，跳过重连
                _log("Skipping reconnect: auto mode, no previous successful connection")
                self._fallback._cancel_retry_timer()
                return False

        if self._labrad is None:
            try:
                from .labrad_client import LabRADClient
                self._labrad = LabRADClient()
            except Exception as e:
                self._fallback._last_error = str(e)
                return False

        try:
            success = self._labrad.initialize()
            if success:
                self._init_started = True
                self._load_fallback_data()  # 刷新缓存
                return True
            else:
                self._fallback._last_error = self._labrad._init_error
                return False
        except Exception as e:
            self._fallback._last_error = str(e)
            return False

    def _load_fallback_data(self):
        """加载降级数据"""
        try:
            qubits_data = self._fallback.get_fallback_data("qubits.json")
            if qubits_data:
                self._fallback_qubits = qubits_data if isinstance(qubits_data, list) else []
                _log(f"Loaded {len(self._fallback_qubits)} fallback qubits")

            experiments_data = self._fallback.get_fallback_data("experiments.json")
            if experiments_data:
                self._fallback_experiments = experiments_data if isinstance(experiments_data, list) else []
                _log(f"Loaded {len(self._fallback_experiments)} fallback experiments")
        except Exception as e:
            _log(f"Failed to load fallback data: {e}")

    def _init_offline_provider(self):
        """初始化离线数据提供器和实验模拟器"""
        if not self._offline_mode_enabled:
            _log("Offline mode is disabled")
            return

        try:
            # 解析离线数据路径
            offline_path = Path(__file__).parent.parent.parent / self._offline_data_path
            self._offline_provider = OfflineDataProvider(str(offline_path))

            if self._offline_provider.load():
                summary = self._offline_provider.get_summary()
                _log(f"Offline data loaded: {summary['total_datasets']} datasets, {summary['total_qubits']} qubits")

                # 初始化实验模拟器
                self._simulator = ExperimentSimulator(self._offline_provider)
                _log("Experiment simulator initialized")
            else:
                _log(f"Failed to load offline data from {offline_path}")
                self._offline_provider = None
        except Exception as e:
            _log(f"Failed to initialize offline provider: {e}")
            self._offline_provider = None

    @property
    def _is_offline_mode(self) -> bool:
        """检查是否处于离线模式"""
        with self._mode_lock:
            if self._mode == self.Mode.OFFLINE:
                return True
            if self._mode == self.Mode.ONLINE:
                return False
            # AUTO 模式：LabRAD 不可用时自动离线
            return not self._ensure_connected()

    def _set_mode(self, mode: str) -> bool:
        """设置运行模式

        Args:
            mode: "online", "offline", 或 "auto"

        Returns:
            是否设置成功
        """
        if mode not in (self.Mode.ONLINE, self.Mode.OFFLINE, self.Mode.AUTO):
            return False

        with self._mode_lock:
            old_mode = self._mode
            self._mode = mode
            _log(f"Mode changed: {old_mode} -> {mode}")
        return True

    def _get_mode(self) -> str:
        """获取当前运行模式"""
        with self._mode_lock:
            return self._mode

    def _actuation_flags(self) -> Dict[str, bool]:
        """写闸门状态（allow_execute / allow_set_params）"""
        from services.common.config import get_actuation_flags

        return get_actuation_flags("quantum_service")

    def _data_source(self) -> str:
        """当前数据来源：labrad / offline / fallback"""
        if self._labrad is not None and self._labrad.connected:
            return "labrad"
        if self._is_offline_mode:
            return "offline"
        return "fallback"

    def _connection_fields(self) -> Dict[str, Any]:
        """健康/状态接口共用的连接与闸门字段"""
        from services.common.config import get_labrad_params

        params = get_labrad_params()
        flags = self._actuation_flags()
        return {
            "data_source": self._data_source(),
            "actuation_allowed": flags["allow_execute"] or flags["allow_set_params"],
            "allow_execute": flags["allow_execute"],
            "allow_set_params": flags["allow_set_params"],
            "require_task": flags.get("require_task", False),
            "labrad_host": params["host"],
            "labrad_port": params["port"],
        }

    @staticmethod
    def _json_safe(obj: Any) -> Any:
        """把结果里的 numpy 类型递归转成可 JSON 序列化的原生类型。

        防御性措施：一旦结果里混进 ndarray/np.float64，base_service 的
        json.dumps 会抛 "Object of type ndarray is not JSON serializable"，
        整个响应退化成 500 —— 而错误信息还带在响应体里，排查起来很绕。
        """
        try:
            import numpy as _np
        except ImportError:
            return obj
        if isinstance(obj, dict):
            return {k: QuantumService._json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [QuantumService._json_safe(v) for v in obj]
        if isinstance(obj, _np.ndarray):
            return obj.ravel().tolist()
        if isinstance(obj, _np.generic):
            return obj.item()
        return obj

    def _execute_offline_simulation(self, code: str, task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        离线模式模拟执行

        策略:
        1. 检查模拟器是否可用
        2. 解析实验类型和 qubit
        3. 查找匹配的离线数据
        4. 生成模拟输出
        """
        # 检查模拟器是否可用
        if self._simulator is None:
            return {
                "task_id": task_id,
                "status": "offline_no_simulator",
                "stdout": "[OFFLINE] Simulator not available",
                "stderr": "",
                "error": "实验模拟器未初始化",
            }

        _log(f"[OFFLINE SIM] Task: {task_id}")
        _log(f"[OFFLINE SIM] Code: {code[:100]}...")

        try:
            # 执行模拟
            result = self._simulator.simulate(code)

            # 添加任务 ID
            result["task_id"] = task_id

            # 添加绘图路径（如果有数据）
            if result.get("data"):
                plot_path = self._generate_simulation_plot(result)
                if plot_path:
                    result["plotPath"] = plot_path

            _log(f"[OFFLINE SIM] Result: status={result.get('status')}, "
                 f"exp_type={result.get('exp_type')}, "
                 f"qubit={result.get('qubit')}")

            return self._json_safe(result)

        except Exception as e:
            _log(f"[OFFLINE SIM] Error: {e}\n{traceback.format_exc()}")
            return {
                "task_id": task_id,
                "status": "offline_simulated",
                "stdout": f"[OFFLINE SIMULATION] Error: {str(e)}",
                "stderr": traceback.format_exc(),
                "error": str(e),
                "simulated": True,
            }

    @serialized_plot
    def _generate_simulation_plot(self, result: Dict[str, Any]) -> Optional[str]:
        """生成模拟实验的绘图"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from io import BytesIO
            import base64

            # 标题里带中文（"(合成数据)"），必须先配 CJK 字体，
            # 否则 matplotlib 渲染成方框。与实验室自己的脚本
            # measure_scripts/.../sq_workflow/backend.py 用的是同一组字体。
            plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
            plt.rcParams['axes.unicode_minus'] = False

            plot_data = result.get("data")
            if not plot_data:
                return None

            # 确定保存路径
            _qmclaw_server_dir = Path(__file__).parent.parent.parent
            plots_dir = _qmclaw_server_dir.parent / "qmclaw-web" / "public" / "plots"
            plots_dir.mkdir(parents=True, exist_ok=True)

            # 创建图表
            plt.close('all')
            fig, ax = plt.subplots(1, 1, figsize=(10, 6))

            data_type = plot_data.get("type", "line")

            if data_type == "line":
                x = plot_data.get("x", [])
                y = plot_data.get("y", [])
                if x and y:
                    ax.plot(x, y, 'b.-', markersize=3)

            elif data_type == "scatter":
                x = plot_data.get("x", [])
                y = plot_data.get("y", [])
                if x and y:
                    ax.scatter(x, y, alpha=0.5, s=10)

            # 设置标签
            ax.set_xlabel(plot_data.get("xlabel", "X"))
            ax.set_ylabel(plot_data.get("ylabel", "Y"))
            ax.set_title(plot_data.get("title", f"{result.get('qubit') or 'unknown'}: {result.get('exp_type', 'Unknown')} (合成数据)"))
            ax.grid(True, alpha=0.3)
            plt.tight_layout()

            # 保存
            filename = f"offline_sim_{result.get('exp_type', 'unknown')}_{int(time.time() * 1000)}.png"
            plot_path = plots_dir / filename
            fig.savefig(str(plot_path), dpi=150, bbox_inches='tight')
            plt.close(fig)

            # 返回相对 URL 路径，而不是绝对文件路径
            # 前端通过 Express 服务器访问 /plots/ 路径
            return f"/plots/{filename}"

        except Exception as e:
            _log(f"[OFFLINE SIM] Plot error: {e}")
            return None

    def _ensure_connected(self) -> bool:
        """确保 LabRAD 已连接

        离线模式或永久降级时返回 False；客户端未创建时懒创建后再连。
        """
        if self._mode == self.Mode.OFFLINE:
            return False

        if self._fallback.is_permanently_degraded:
            return False

        if self._labrad is None:
            try:
                from .labrad_client import LabRADClient
                self._labrad = LabRADClient()
            except Exception as e:
                _log(f"Failed to create LabRAD client: {e}")
                self._fallback.on_connection_failed(str(e))
                return False

        if self._labrad.connected:
            return True

        # 尝试连接
        _log("Connecting to LabRAD...")
        success = self._labrad.initialize()
        if success:
            _log("LabRAD connected successfully")
            self._init_started = True
            self._fallback.on_connection_success()
        else:
            _log(f"LabRAD connection failed: {self._labrad._init_error}")
            self._fallback.on_connection_failed(self._labrad._init_error)

        return success

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理测控请求"""
        start_time = time.time()

        # GET 查询参数并入 data（body 优先）。
        # 之前 query 被整个丢掉，导致文档里写的 `GET /qubit/profile?refresh=1`、
        # `GET /qubit/profile?name=q7ld3` 之类的用法其实不生效（refresh 恒为 False）。
        if query:
            merged = dict(data or {})
            for key, values in query.items():
                if key in merged:
                    continue
                if isinstance(values, list):
                    if not values:
                        continue
                    merged[key] = values[0]
                else:
                    merged[key] = values
            data = merged

        # 路由
        if path == "/health":
            return self._handle_health()
        elif path == "/connect":
            return self._handle_connect(data)
        elif path == "/status":
            return self._handle_status()
        elif path == "/qubits":
            return self._handle_qubits()
        elif path == "/experiments":
            return self._handle_experiments()
        elif path == "/execute":
            return self._handle_execute(data)
        elif path == "/jobs" or path.startswith("/jobs/"):
            return self._handle_jobs(path, data)
        elif path == "/switch_session":
            return self._handle_switch_session(data)
        elif path == "/sessions":
            return self._handle_sessions()
        elif path == "/session_tree":
            return self._handle_session_tree(data)
        elif path == "/qubit/params":
            return self._handle_qubit_params(data)
        elif path == "/qubit/registry":
            return self._handle_qubit_registry(data)
        elif path == "/qubit/set_params":
            return self._handle_qubit_set_params(data)
        elif path == "/qubit/apply_dataset":
            return self._handle_qubit_apply_dataset(data)
        elif path == "/qubit/capabilities":
            return self._handle_qubit_capabilities(data)
        elif path == "/qubit/probe_transition":
            return self._handle_qubit_probe_transition(data)
        elif path == "/qubit/runs":
            return self._handle_qubit_runs(data)
        elif path == "/qubit/assess":
            return self._handle_qubit_assess(data)
        elif path == "/qubit/state":
            return self._handle_qubit_state(data)
        elif path == "/qubit/writer":
            return self._handle_qubit_writer(data)
        elif path == "/qubit/profile":
            return self._handle_qubit_profile(data)
        elif path == "/qubit/replay":
            return self._handle_qubit_replay(data)
        elif path == "/mode":
            return self._handle_mode(data)
        elif path == "/datasets":
            return self._handle_datasets(data)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查（HTTP 层通常已被 base_service 拦下，这里只做委托）。

        早前这里另写了一份判据，与 ``get_health`` 不一致（不认 offline_provider、
        字段集也不同）。现在只有 ``get_health`` 一份。
        """
        return self.get_health()

    def _handle_qubit_assess(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """比特状态 + 指标判定（纯计算，**不碰硬件**）。

        请求：{"name": "q2ld3", "evidence": {"freq_locked": true, "contrast": 0.765,
                                            "t1_us": 72.2, "t2_star_us": 6.58, "xeb_p": 0.9965}}
        返回：{"success": true, "name": ..., "state": {...}, "metrics": {...}}

        阈值全部来自 config/lab_criteria.json（指标唯一真源），本端点不定义新阈值。
        """
        from services.common.metrics import evaluate_all
        from services.common.qubit_state import derive_state

        name = data.get("name") or data.get("qubit")
        evidence = data.get("evidence")
        if evidence is None:
            # 允许平铺传参：{"name":..., "contrast":0.7, "t1_us":...}
            evidence = {
                k: v
                for k, v in (data or {}).items()
                if k not in ("name", "qubit", "evidence")
            }
        if not isinstance(evidence, dict):
            from services.common.errors import error_envelope

            return {
                "success": False,
                **error_envelope("validation_error", "evidence must be an object"),
            }
        try:
            state = derive_state(evidence)
            metrics = evaluate_all(evidence)
        except Exception as exc:  # noqa: BLE001 - 统一错误语义
            from services.common.errors import error_envelope

            return {"success": False, **error_envelope("validation_error", str(exc))}
        return {
            "success": True,
            "name": name,
            "state": state,
            "metrics": metrics,
            "criteria_version": state.get("criteria_version"),
        }

    def _handle_qubit_state(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """比特状态自查：从执行台账推导证据 → 状态 → 缺什么证据（**只读，不碰硬件**）。

        请求：{"name": "q4ld3", "limit": 200, "evidence": {"freq_locked": true}}
        返回：{"success": true, "name": ..., "state": {...}, "evidence": {...},
               "evidence_sources": {...}, "conflicts": [...], "records": {...},
               "plan": {...}}

        与 `/qubit/assess` 的分工：那个只吃调用方给的证据（纯判定），这个负责
        "证据从哪来"——读 RunRecord 台账，把真跑过的实验翻成证据，并保留每条证据的
        run_id / 数据集号 / 函数名，评审能点回原始数据。显式传入的 `evidence` 覆盖台账值。
        """
        from services.common.qubit_state import derive_state, evidence_plan
        from services.common.runrecord import RunRecordWriter
        from services.common.state_evidence import evidence_from_records, merge_evidence

        name = data.get("name") or data.get("qubit")
        if not name:
            return {
                "success": False,
                **error_envelope("validation_error", "name (qubit) is required"),
            }
        try:
            limit = int(data.get("limit", 200))
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 2000))

        records = RunRecordWriter().list_records(limit=limit)
        derived = evidence_from_records(records, qubit=str(name))
        overrides = data.get("evidence")
        if not isinstance(overrides, dict):
            overrides = None
        evidence, sources = merge_evidence(derived, overrides)
        state = derive_state(evidence)

        return {
            "success": True,
            "name": str(name),
            "state": state,
            "evidence": evidence,
            "evidence_sources": sources,
            "conflicts": derived.get("conflicts", []),
            "records": {
                "considered": derived.get("considered", 0),
                "ignored": derived.get("ignored", 0),
                "ignored_runs": derived.get("ignored_runs", []),
                "limit": limit,
            },
            "plan": evidence_plan(state["state"]),
        }

    def _handle_qubit_capabilities(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """能力探测：当前安装里哪些实验函数真正可用。

        背景：本机 lqms 被 .pyd 遮蔽（sq_nodes.pyd 2026-04-21 遮蔽 sq_nodes.py 2026-09-08），
        导致 sq.spectroscopy_iq / time_rabi / iqcenter 等源码里有、运行时没有，
        且**重启服务无效**。本端点用静态 AST 比对给出清单（只读、不碰硬件）。
        """
        from services.common.capabilities import capability_report

        report = capability_report()
        return {"success": True, **report}

    def _handle_qubit_probe_transition(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """探测比特跃迁频率（Qubit Port 语义操作）。

        请求：{"name": "q7ld3", "df": 0.3, "method": "pidf", "task_id": "..."}
        默认走 sq.pidf(update=False)：不让厂商函数回写 f10/fread（避免静默副作用）。
        受 allow_execute 写闸门与硬件通道约束。
        """
        from .port import QubitPort

        qubit = data.get("name") or data.get("qubit")
        if not qubit:
            return {"success": False, "error": "name (qubit) is required"}
        try:
            df = float(data.get("df", 0.3))
        except (TypeError, ValueError):
            return {"success": False, "error": "df must be a number"}
        port = QubitPort(self)
        result = port.probe_transition(
            qubit,
            df=df,
            task_id=data.get("task_id"),
            timeout=int(data.get("timeout", 300)),
            method=str(data.get("method", "pidf")),
        )
        return result

    def _handle_qubit_runs(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """最近的执行审计记录（RunRecord 列表，可按条件过滤）。

        用于回答"那次实验对应哪份数据 / 有没有绕过治理"。
        过滤条件（阶段 18）：`qubit`、`status`（规范状态或生命周期值）、`source`、
        `dataset_id`、`trace_id`、`since`/`until`（epoch 秒或 ISO 串）。
        """
        from services.common.runrecord import (
            RunRecordWriter,
            collapse_records,
            filter_records,
        )
        from services.common.governance import summarize_governance

        try:
            limit = int(data.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 2000))
        criteria = {
            "qubit": data.get("qubit") or data.get("qubits"),
            "status": data.get("status"),
            "source": data.get("source"),
            "dataset_id": data.get("dataset_id") or data.get("dataset"),
            "trace_id": data.get("trace_id") or data.get("traceId"),
            "since": data.get("since"),
            "until": data.get("until"),
        }
        active = {k: v for k, v in criteria.items() if v not in (None, "", [])}
        writer = RunRecordWriter()
        # 一次异步执行会先落 queued、跑完再追加完成记录（同一个 run_id）；
        # 读侧按 run_id 取最后一条，否则同一执行会出现两次。
        # list_records 是"新→旧"，先翻成时间顺序再去重，最后再翻回来保持响应顺序。
        # 有过滤条件时多读一些再筛（否则一条老 trace 会被 limit 截掉、查不到）。
        scan = limit * 10 if active else limit * 3
        scan = max(1, min(scan, 5000))
        created_order = list(reversed(writer.list_records(limit=scan)))
        records = collapse_records(created_order)
        if active:
            records = filter_records(records, **criteria)
        records = records[-limit:]
        records.reverse()

        return {
            "success": True,
            "count": len(records),
            "directory": writer.directory,
            "governance": summarize_governance(records),
            "trace_id": active.get("trace_id"),
            "filters": active,
            "records": records,
        }

    def _handle_qubit_writer(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """单写者状态（阶段 2 第三批）。

        返回当前模式、队列深度、影子观测统计与最近 RunRecord 的治理覆盖率。
        """
        from services.quantum_service.writer import resolve_writer_mode

        mode = resolve_writer_mode(self._actuation_flags())
        stats = self._writer.stats(mode=mode)
        shadow = self._writer.shadow_summary()
        return {
            "success": True,
            "mode": mode,
            "modes": ["off", "shadow", "enforce"],
            "stats": stats,
            "shadow": shadow,
            "hint": (
                "writer_mode 在 config/fallback_config.json 的 quantum_service.actuation 下配置，"
                "每请求读取（热生效）；默认 shadow 只观测不改变执行路径"
            ),
        }

    def _handle_qubit_profile(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """设备画像（阶段 3 第一批；阶段 3 后续 ② 支持自动刷新）。

        读 `config/device_profile.json`（只读文件，不连设备、不下发实验）：
        - `?name=<qubit>` 返回该比特的画像条目；
        - 不带 name 返回画像摘要（版本、session、比特列表、校验问题）；
        - `?refresh=1` 额外从 `/qubit/registry` 回填画像（只读寄存器，不改硬件），
          结果写到 data 目录快照文件，**不改 config 目录**（配置目录由人维护）。
        """
        from services.common.profile import load_profile, profile_report, qubit_entry

        profile = load_profile()
        name = str(data.get("name") or data.get("qubit") or "").strip()

        refresh = str(data.get("refresh") or "").strip().lower() in ("1", "true", "yes", "on")
        if refresh:
            refresh_result = self._refresh_device_profile(profile, name)
            profile = refresh_result["profile"]
            if name:
                entry = qubit_entry(profile, name)
                return {
                    "success": True,
                    "name": name,
                    "entry": entry,
                    "session": profile.get("session", []),
                    "refreshed": refresh_result["refreshed"],
                    "snapshot_path": refresh_result["snapshot_path"],
                    "errors": refresh_result["errors"],
                }
            report = profile_report(profile)
            return {
                "success": True,
                **report,
                "refreshed": refresh_result["refreshed"],
                "snapshot_path": refresh_result["snapshot_path"],
                "errors": refresh_result["errors"],
            }

        if name:
            entry = qubit_entry(profile, name)
            if not entry:
                return {
                    "success": False,
                    "error": f"画像里没有比特 {name}",
                    "available": profile_report(profile)["qubits"],
                }
            return {"success": True, "name": name, "entry": entry, "session": profile.get("session", [])}
        report = profile_report(profile)
        return {"success": True, **report}

    def _refresh_device_profile(
        self,
        profile: Dict[str, Any],
        only: str = "",
    ) -> Dict[str, Any]:
        """从 /qubit/registry 回填设备画像，写 data 目录快照（不改 config）。

        只读寄存器，不下发实验，不改硬件。
        """
        import json as _json
        import os as _os
        import tempfile as _tempfile

        from services.common.profile import merge_registry

        qubits = list((profile.get("qubits") or {}).keys())
        if only:
            qubits = [only]
        refreshed: List[str] = []
        errors: List[str] = []
        for qubit in qubits:
            try:
                payload = self._handle_qubit_registry({"name": qubit})
                # 注意：_handle_qubit_registry 成功时返回 {"name","session_path","registry","source"}，
                # **没有 success 字段**；早期这里误判 payload["success"]，导致真机上画像刷新全部
                # 被当成"读取失败"，profile 永远不会更新。
                if not isinstance(payload, dict) or payload.get("error"):
                    errors.append(
                        f"{qubit}: registry 读取失败"
                        + (f"（{payload.get('error')}）" if isinstance(payload, dict) else "")
                    )
                    continue
                if not isinstance(payload.get("registry"), dict):
                    errors.append(f"{qubit}: registry 内容为空")
                    continue
                merge_registry(profile, qubit, payload, overwrite=True)
                refreshed.append(qubit)
            except Exception as exc:  # 读不到就跳过，不影响其它比特
                errors.append(f"{qubit}: {exc}")

        profile["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # 快照写到 data 目录（可写、可随时重生成）；data 目录不可写时降级到临时目录
        snapshot_path = ""
        server_dir = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        )
        candidates = [
            _os.path.join(server_dir, "data", "device_profile_snapshot.json"),
            _os.path.join(_tempfile.gettempdir(), "qmclaw-device-profile",
                         "device_profile_snapshot.json"),
        ]
        for candidate in candidates:
            try:
                _os.makedirs(_os.path.dirname(candidate), exist_ok=True)
                with open(candidate, "w", encoding="utf-8") as fh:
                    _json.dump(profile, fh, ensure_ascii=False, indent=2)
                snapshot_path = candidate
                break
            except Exception as exc:
                errors.append(f"快照写入失败({candidate}): {exc}")
        if not snapshot_path:
            snapshot_path = ""

        return {
            "profile": profile,
            "refreshed": refreshed,
            "errors": errors,
            "snapshot_path": snapshot_path,
        }

    def _get_replay_store(self):
        """整个服务共用一个 ReplayStore 实例。

        必须是**同一个实例**：写侧一旦降级（目录不可写 → 临时目录），
        读侧也要跟着读那个目录；每次新建实例会让"刚录的 fixture 读不到"。
        """
        from services.common.replay import ReplayStore

        store = getattr(self, "_replay_store_obj", None)
        if store is None:
            store = ReplayStore()
            self._replay_store_obj = store
        return store

    def _handle_qubit_replay(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """回放层（阶段 3 第二批 ③）：离线仿真厂商语义 + fixture 录制/回放。

        四种动作（body 的 `action` 或 `mode` 字段）：
        - `simulate`（默认）：给一段实验代码 + 可选 registry，**不连设备**推演
          "这段代码会改哪些寄存器 / 会展开成什么扫描 / 会报什么错"。
        - `record`：把一次真实执行录成 fixture（code + registry 前后快照 + 数据集号）。
        - `list`：列出已录制的 fixture。
        - `replay`：回放某个 fixture，比对仿真结果与真机当时的结果，报告厂商语义漂移。

        设计目的（docs/ARCHITECTURE_ROADMAP.md §2.6）：让 CI 也能覆盖真机链路——
        厂商函数会静默改寄存器（spectroscopy→f10/f21、s21→fread、iqraw→discriminator），
        这些语义必须能被离线断言。
        """
        from services.common.replay import (
            ReplayStore,
            parse_calls,
            record_fixture,
            replay_fixture,
            simulate,
        )

        action = str(data.get("action") or data.get("mode") or "simulate").strip().lower()
        store = self._get_replay_store()

        if action == "list":
            return {
                "success": True,
                "directory": store.directory,
                "fixtures": store.list_fixtures(int(data.get("limit", 50) or 50)),
            }

        if action == "record":
            fixture = record_fixture(
                code=str(data.get("code") or ""),
                registry_before=data.get("registry_before"),
                registry_after=data.get("registry_after"),
                qubits=data.get("qubits"),
                dataset_ids=data.get("dataset_ids"),
                run_id=data.get("run_id"),
                trace_id=data.get("trace_id"),
                task_id=data.get("task_id"),
                source=str(data.get("source") or "execute"),
                status=str(data.get("status") or "unknown"),
                stdout=str(data.get("stdout") or ""),
            )
            path = store.save(fixture)
            return {
                "success": path is not None,
                "fixture_id": fixture["fixture_id"],
                "path": path,
                "directory": store.directory,
                "recorded": True,
            }

        if action == "replay":
            fixture_id = str(data.get("fixture_id") or "").strip()
            fixture = store.load(fixture_id) if fixture_id else None
            if not fixture:
                return {
                    "success": False,
                    "error": f"找不到 fixture {fixture_id}",
                    "available": [f.get("fixture_id") for f in store.list_fixtures(50)],
                }
            return {"success": True, **replay_fixture(fixture)}

        # simulate
        code = str(data.get("code") or "")
        if not code:
            return {"success": False, "error": "code is required"}
        result = simulate(
            code,
            data.get("registry"),
            session=data.get("session"),
        )
        return {
            "success": True,
            "action": "simulate",
            "parsed_calls": parse_calls(code),
            **result,
        }

    def _handle_mode(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理模式切换请求

        切到 online/auto 时清掉永久降级并实际尝试连接，响应带 connected 与失败原因，
        不再静默假成功。
        """
        # GET 请求（无 mode 参数）返回当前状态
        if "mode" not in data:
            return {
                "success": True,
                "mode": self._get_mode(),
                "connected": self._labrad is not None and self._labrad.connected,
                "offline_available": self._offline_provider is not None and self._offline_provider.is_available,
            }

        mode = data["mode"]
        if not self._set_mode(mode):
            return {
                "success": False,
                "error": f"Invalid mode: {mode}",
                "current_mode": self._get_mode(),
            }

        result: Dict[str, Any] = {
            "success": True,
            "mode": self._get_mode(),
            "offline_available": self._offline_provider is not None and self._offline_provider.is_available,
        }

        if mode == self.Mode.OFFLINE:
            result["connected"] = False
            return result

        # 清掉 DEGRADED_PERMANENT，否则 _ensure_connected 第一行就 return False
        if self._fallback.is_degraded:
            self._fallback.reset()

        connected = self._ensure_connected()
        result["connected"] = connected
        if not connected:
            result["success"] = False
            result["error"] = self._labrad._init_error if self._labrad and self._labrad._init_error else (
                self._fallback.last_error or "LabRAD connection failed"
            )
        return result

    def _get_fallback_message(self) -> str:
        """获取降级模式提示信息"""
        if self._fallback.is_connected:
            return ""
        if self._fallback.is_permanently_degraded:
            return f"LabRAD 不可用（已停止重连）。最后错误：{self._fallback.last_error or '未知错误'}"
        if self._fallback.is_degraded:
            return f"LabRAD 连接中断，正在尝试重连（{self._fallback.retry_count}/{self._fallback._max_retry_attempts}）..."
        return "LabRAD 未连接"

    def _handle_connect(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """手动触发连接或重连"""
        from ..common.session import parse_session_path

        session_path, session_error = parse_session_path(data.get("session_path"))
        if session_error:
            return {
                **error_envelope("validation_error", session_error, success=False),
            }

        timeout = data.get("timeout", 60)

        # 人工确认现场安全后，允许清除卡死标记
        if data.get("reset_stuck"):
            if self._exec_stuck:
                _log(f"[STUCK] Cleared by operator request: {self._exec_stuck}")
                self._exec_stuck = None
            self._release_busy()

        with self._labrad_lock:
            # 如果已经连接且用户没有指定新路径，直接返回成功
            if self._labrad is not None and self._labrad.connected and session_path is None:
                return {
                    "success": True,
                    "message": "Already connected to LabRAD",
                    "session_path": self._labrad.session_path,
                    "qubit_count": len(self._labrad.qubits),
                }

            # 手动重连（重置重试计数）
            if self._labrad is None:
                try:
                    from .labrad_client import LabRADClient
                    self._labrad = LabRADClient()
                except Exception as e:
                    return {"success": False, "error": f"Failed to create LabRAD client: {e}"}

            # 重置降级状态
            self._fallback.reset()
            self._fallback.set_state(ConnectionState.CONNECTING)

            success = self._labrad.initialize(session_path, timeout)

            if success:
                self._init_started = True
                self._fallback.on_connection_success()
                self._load_fallback_data()
                return {
                    "success": True,
                    "message": "Connected to LabRAD",
                    "session_path": self._labrad.session_path,
                    "qubit_count": len(self._labrad.qubits),
                }
            else:
                error_msg = self._labrad._init_error or "Unknown error"
                self._fallback.on_connection_failed(error_msg)
                self._fallback.start_reconnect_timer(self._do_reconnect)
                return {
                    "success": False,
                    "error": error_msg,
                    "fallback_mode": True,
                    "fallback_message": self._get_fallback_message(),
                }

    def _handle_status(self) -> Dict[str, Any]:
        """获取连接状态"""
        fallback_info = self._fallback.get_status_info()
        extra = self._connection_fields()
        if self._labrad is None:
            return {
                "connected": False,
                "initialized": False,
                "session_path": None,
                "qubit_count": len(self._fallback_qubits),
                "fallback_mode": self._fallback.is_degraded,
                "fallback_state": fallback_info["state"],
                "last_error": self._fallback.last_error,
                **extra,
            }

        return {
            "connected": self._labrad.connected,
            "initialized": self._labrad._initialized,
            "session_path": self._labrad.session_path,
            "qubit_count": len(self._labrad.qubits) if self._labrad.connected else len(self._fallback_qubits),
            "init_error": self._labrad._init_error,
            "fallback_mode": self._fallback.is_degraded,
            "fallback_state": fallback_info["state"],
            "last_error": self._fallback.last_error,
            "ray_ready": self._labrad.ray_ready,
            **extra,
        }

    def _handle_qubits(self) -> Dict[str, Any]:
        """获取量子比特列表"""
        # 离线模式优先返回离线数据
        if self._is_offline_mode and self._offline_provider is not None:
            qubits = self._offline_provider.get_qubits()
            return {
                "qubits": qubits,
                "count": len(qubits),
                "source": "offline",
                "mode": self._get_mode(),
            }

        if self._fallback.is_degraded:
            # 降级模式返回本地数据
            return {
                "qubits": self._fallback_qubits,
                "count": len(self._fallback_qubits),
                "source": "fallback",
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            # 未连接也返回降级数据
            return {
                "qubits": self._fallback_qubits,
                "count": len(self._fallback_qubits),
                "source": "fallback",
                "fallback_message": self._get_fallback_message(),
            }

        with self._labrad_lock:
            qubits = self._labrad.get_qubits()
            session_path = self._labrad.session_path
            return {"qubits": qubits, "count": len(qubits), "sessionPath": session_path, "source": "live"}

    def _handle_experiments(self) -> Dict[str, Any]:
        """获取可用实验列表"""
        # 离线模式优先返回离线数据
        if self._is_offline_mode and self._offline_provider is not None:
            experiments = self._offline_provider.get_experiments()
            return {
                "experiments": experiments,
                "count": len(experiments),
                "source": "offline",
                "mode": self._get_mode(),
            }

        if self._fallback.is_degraded:
            # 降级模式返回本地数据
            return {
                "experiments": self._fallback_experiments,
                "count": len(self._fallback_experiments),
                "source": "fallback",
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            # 未连接也返回降级数据
            return {
                "experiments": self._fallback_experiments,
                "count": len(self._fallback_experiments),
                "source": "fallback",
                "fallback_message": self._get_fallback_message(),
            }

        with self._labrad_lock:
            experiments = self._labrad.list_experiments()
            return {"experiments": experiments, "count": len(experiments), "source": "live"}

    def _handle_datasets(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取数据集列表"""
        path = data.get("path")
        qubit = data.get("qubit")
        experiment_type = data.get("experiment_type")
        date = data.get("date")

        # 离线模式返回离线数据
        if self._is_offline_mode and self._offline_provider is not None:
            datasets = self._offline_provider.get_datasets(
                qubit=qubit,
                experiment_type=experiment_type,
                date=date
            )
            return {
                "datasets": [
                    {
                        "id": ds.id,
                        "name": ds.name,
                        "qubit": ds.qubit,
                        "experiment_type": ds.experiment_type,
                        "date": ds.date,
                        "file_size": ds.file_size,
                    }
                    for ds in datasets
                ],
                "count": len(datasets),
                "source": "offline",
            }

        # 在线模式返回 LabRAD 数据
        _log(f"datasets: path={path}, type={type(path)}")

        if self._fallback.is_degraded:
            return {
                "error": "LabRAD 不可用",
                "datasets": [],
                "groups": [],
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            return {
                "error": "Not connected to LabRAD",
                "datasets": [],
                "groups": [],
                "fallback_message": self._get_fallback_message(),
            }

        with self._labrad_lock:
            dv = self._labrad.dv
            try:
                clean_path = []  # 默认空路径
                if path:
                    # path may be string like "LQHL/test" or array
                    if isinstance(path, str):
                        path = path.strip('/').split('/')
                    # 先 cd 到根目录，再 cd 到目标路径（绝对路径）
                    dv.cd('')  # go to root first
                    clean_path = path[1:] if path and path[0] == '' else path
                    _log(f"datasets: clean_path={clean_path}")
                    if clean_path:
                        dv.cd(clean_path)
                else:
                    dv.cd('')  # go to root
                dirs = dv.dir()
                _log(f"datasets: dirs[0]={dirs[0]}, dirs[1]={dirs[1][:10] if dirs[1] else []}...")
                datasets = dirs[1] if len(dirs) > 1 else []
                _log(f"datasets: found {len(datasets)} datasets")
                # Return path as string for frontend compatibility
                current_path = clean_path if clean_path else ['']
                path_str = '/'.join(current_path)

                # Convert to Dataset format for frontend
                datasets_formatted = [
                    {
                        "id": ds_name,
                        "name": ds_name,
                        "path": path_str + '/' + ds_name if path_str else ds_name,
                    }
                    for ds_name in sorted(datasets)
                ]

                return {
                    "datasets": datasets_formatted,
                    "groups": sorted(dirs[0]) if dirs[0] else [],
                    "path": path_str,
                    "current_path": path_str,
                    "source": "live",
                }
            except Exception as e:
                _log(f"datasets: error = {e}")
                return {"error": str(e), "datasets": [], "groups": [], "path": ""}

    def _submit_job(self, code: str, task_id: str, timeout,
                    wait_event: Optional[threading.Event] = None) -> str:
        """提交执行任务，立即返回 job_id（进入单消费者通道，不立刻执行）。

        为什么是"排队"而不是"起线程 + busy 锁直接拒"：硬件通道只有一条，
        撞车时把调用方打回 `busy` 会逼上层自己重试拼顺序，谁都拼不出全局最优。
        这里统一排成 FIFO，由唯一的 worker 线程逐个执行——并发度恒为 1，
        第二个提交者拿到的是"排队中 + 位置"，而不是失败。

        调用方通过 `GET /jobs/{job_id}` 轮询；超时可
        `POST /jobs/{job_id}/abandon` 放弃（线程无法强杀，但会标记 stuck 并
        拒绝后续下发，避免真机上并发两条指令）。
        """
        job_id = f"job_{int(time.time() * 1000)}_{__import__('uuid').uuid4().hex[:6]}"
        job: Dict[str, Any] = {
            "job_id": job_id,
            "task_id": task_id,
            "status": "queued",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
            "abandoned": False,
            "code": code,
            "timeout": timeout,
        }
        with self._jobs_lock:
            self._jobs[job_id] = job
            self._evict_jobs()
            # 同步调用方的完成信号必须在入队**之前**登记，否则快实验可能在
            # 登记前就跑完，事件永远等不到（会白白等到超时）
            if wait_event is not None:
                self._job_events[job_id] = wait_event
        self._enqueue_job(job_id)
        _log(f"[JOB] queued {job_id} for task {task_id} (pos={self._job_position(job_id)})")
        return job_id

    # ── 单消费者通道 ──────────────────────────────────────────────────────
    def _enqueue_job(self, job_id: str) -> None:
        with self._job_cond:
            self._job_queue.append(job_id)
            self._job_cond.notify_all()

    def _job_position(self, job_id: str) -> int:
        with self._job_cond:
            try:
                return list(self._job_queue).index(job_id)
            except ValueError:
                return -1

    def _lane_worker(self) -> None:
        """唯一的执行线程：一次只跑一条实验，跑完再取下一个。"""
        _log("[LANE] quantum hardware lane worker started")
        while not self._lane_stop.is_set():
            with self._job_cond:
                while not self._job_queue and not self._lane_stop.is_set():
                    self._job_cond.wait(0.5)
                if self._lane_stop.is_set():
                    return
                job_id = self._job_queue.popleft()
            job = self._jobs.get(job_id)
            if job is None or job.get("abandoned"):
                if job is not None:
                    job["status"] = "abandoned"
                    job["finished_at"] = time.time()
                continue
            self._run_job(job)

    def _run_job(self, job: Dict[str, Any]) -> None:
        task_id = job.get("task_id")
        code = job.get("code")
        timeout = job.get("timeout")
        job["status"] = "running"
        job["started_at"] = time.time()

        # 服务已被判定卡死（上一条实验超时后 worker 仍在真机上跑）→ 拒绝放行
        if self._exec_stuck:
            job["status"] = "failed"
            job["error"] = f"service stuck: {self._exec_stuck}"
            job["finished_at"] = time.time()
            return

        if not self._try_acquire_busy(task_id):
            job["status"] = "failed"
            job["error"] = "another task is currently executing"
            job["finished_at"] = time.time()
            return
        record = job.get("run_record")
        datasets_before = None
        pre_snapshot = None
        if record is not None:
            # 数据集基线与 registry 前快照必须在**真正下发实验前**取（异步排队期间
            # 别的 job 可能已经产出数据集 / 改过寄存器）。
            datasets_before = self._probe_dataset_numbers()
            try:
                from services.common.config import _replay_auto_record

                if _replay_auto_record() == "full" and getattr(record, "qubits", None):
                    pre_snapshot = self._safe_registry_snapshot(record.qubits[0])
            except Exception:
                pre_snapshot = None
        try:
            res = self._run_exec_task(code, task_id, timeout)
        except Exception as exc:
            self._release_busy()
            res = {
                **error_envelope("internal_error", f"{type(exc).__name__}: {exc}"),
                "stdout": "",
                "stderr": "",
            }
        job["result"] = res
        job["error"] = res.get("error")
        job["status"] = res.get("status") or "completed"
        job["finished_at"] = time.time()
        if job["abandoned"]:
            job["status"] = "abandoned"
        job.pop("code", None)
        if record is not None:
            self._finalize_async_job(job, res, datasets_before, pre_snapshot)
        event = self._job_events.get(job["job_id"])
        if event is not None:
            event.set()

    def _evict_jobs(self) -> None:
        """内存回收：只保留最近 N 个 job（调用方持有 _jobs_lock）。"""
        limit = self._max_jobs
        if limit <= 0 or len(self._jobs) <= limit:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.get("finished_at")),
            key=lambda j: j.get("finished_at") or 0,
        )
        for job in finished[: max(0, len(self._jobs) - limit)]:
            self._jobs.pop(job["job_id"], None)

    def _handle_jobs(self, path: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """查询 / 放弃异步执行任务。

        - GET  /jobs             → 任务列表（不含 result 明细）
        - GET  /jobs/{id}        → 单个任务状态与结果
        - POST /jobs/{id}/abandon → 放弃任务
        """
        parts = [p for p in path.split("/") if p]
        with self._jobs_lock:
            if len(parts) <= 1:
                jobs = sorted(self._jobs.values(),
                              key=lambda j: j.get("created_at") or 0, reverse=True)
                return {"jobs": [
                    {k: v for k, v in j.items() if k not in ("result", "run_record")}
                    for j in jobs
                ]}
            job = self._jobs.get(parts[1])

        if job is None:
            return error_envelope("not_found", f"unknown job {parts[1]}")

        action = parts[2] if len(parts) > 2 else ""
        if action == "abandon":
            job["abandoned"] = True
            if job.get("status") in ("queued", "running"):
                job["status"] = "abandoned"
                job["error"] = "abandoned by caller"
                # 执行线程可能仍在真机上跑：标记 stuck，禁止后续下发
                self._exec_stuck = (
                    f"Job {job['job_id']} abandoned while running; "
                    "worker thread may still be running on hardware"
                )
                _log(f"[STUCK] {self._exec_stuck}")
            return {"job_id": job["job_id"], "status": job["status"], "abandoned": True}

        payload = dict(job)
        # 阶段 16a：默认异步后 /jobs/{id} 是取结果的主入口，必须能直接对上审计与 trace，
        # 否则调用方还要切到 /qubit/runs 去猜哪个 run_id 是自己这一条。
        payload.setdefault("run_id", job.get("run_id"))
        # trace_id 优先取记录上的；老 job（或记录未写）退回结果里带的那份。
        payload["trace_id"] = job.get("record_trace_id") or (payload.get("result") or {}).get("trace_id")
        payload.pop("record_code", None)
        payload.pop("record_trace_id", None)
        payload.pop("record_task_id", None)
        # 运行期 job 上挂着 RunRecord 对象（`_attach_record` 写入、worker 结束时
        # `_finalize_job_record` 才 pop）——它不可 JSON 序列化，不摘掉就会让
        # `GET /jobs/{id}` 在**实验进行中**直接 500，调用方（:3008 DSL 轮询）
        # 拿不到非 JSON 响应就把节点判成 failed，真机实验却还在跑。
        payload.pop("run_record", None)
        # 排队中的 job 要能看到"前面还有几条"，否则调用方只能盲等
        if payload.get("status") == "queued":
            payload["queue_position"] = self._job_position(payload["job_id"])
        payload["lane"] = self._lane_snapshot()
        # result 里可能混进 LabRAD 对象/符号：直接 dict(job) 会把整个接口打成 500，
        # 调用方连"任务其实成功了"都看不到（真机探活踩过）。
        if "result" in payload:
            try:
                json.dumps(payload["result"])
            except (TypeError, ValueError) as exc:
                payload["result"] = {
                    "note": "result not JSON-serializable, dropped",
                    "reason": str(exc)[:200],
                }
        return payload

    def _lane_snapshot(self) -> Dict[str, Any]:
        """硬件通道观测：排队长度、当前 job、卡死标记。"""
        with self._job_cond:
            queued = list(self._job_queue)
        return {
            "queue_length": len(queued),
            "queued_jobs": queued[:20],
            "running_task": self._current_task_id,
            "busy": self._busy.is_set(),
            "stuck": self._exec_stuck is not None,
            "concurrency": 1,
        }

    def _handle_execute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """/execute 入口：包一层 RunRecord 审计（单写者原则的影子期实现）。

        现状问题（docs/ARCHITECTURE_ROADMAP.md §3 问题①/⑧）：
        直发 /execute 绕过 task_queue → 没有 manifest/票据/审计；stdout 也不打印
        数据集编号，事后对不上"那次执行"与"那份数据"。

        本包装（**不改变现有行为**，不拦截任何请求）：
        - 每次执行生成一条 RunRecord（含程序指纹、是否受治理、结果摘要）；
        - 未带 task_id 的请求标记 governed=false 并附 warning（影子审计）；
        - 响应里追加 run_id 与 warnings，供调用方追溯；
        - 审计写盘失败不影响执行结果。

        真正的"硬拦截"由 require_task 闸门控制（默认关，可热切），不在此强制。
        """
        from services.common.dataset_ids import (
            dataset_source_label,
            merge_dataset_ids,
            new_dataset_numbers,
        )
        from services.common.runrecord import (
            RunRecord,
            RunRecordWriter,
            derive_verdict,
            diff_snapshots,
            extract_metrics,
        )
        from services.common.trace import ensure_trace

        code = data.get("code") or ""
        explicit_task = data.get("task_id")
        # traceId：调用方可带 trace_id/traceId 透传，没带就生成一个并回传，
        # 让"谁下发的 → 跑了什么 → 出了哪个数据集"能按一条 id 串起来。
        trace_id, trace_is_new = ensure_trace(data)
        # 执行契约（阶段 16a）：wait / async / 默认三态。默认值来自配置
        # （quantum_service.actuation.execute_default，环境变量 QMCLAW_EXECUTE_DEFAULT 优先）。
        # 老调用方显式写 async 的照旧；都没表态的按默认（默认 async = producer 化）。
        from services.common.exec_policy import describe_execute_policy, resolve_execute_mode

        exec_mode = resolve_execute_mode(data)
        is_async = exec_mode == "async"
        # 下游 `_handle_execute_impl` 只认 data["async"]，这里把解析结果钉进去，
        # 免得"默认 async"在内外两层各判一次、判出不同答案。
        call_data = dict(data)
        call_data["async"] = is_async
        record = RunRecord.from_execute(
            code,
            task_id=explicit_task,
            qubits=self._extract_qubit_names(code),
            inputs={
                "timeout": data.get("timeout", 300),
                "async": is_async,
                "execute_mode": exec_mode,
            },
            trace_id=trace_id,
        )
        # --- 程序清单（阶段 7：直发侧也有 manifest，roadmap §2.3/§3 #8）------
        # 纯本地计算：把代码折成函数序列 + 厂商可能静默回写的键 + 缺必填参数。
        # 不读设备、不下发实验，异常也不影响执行。
        try:
            from services.common.program_manifest import build_program_manifest

            record.manifest = build_program_manifest(code, record.qubits)
        except Exception:
            record.manifest = None
        result = None

        # --- 单写者（阶段 2 第三批）-----------------------------------------
        # mode 由 config/fallback_config.json 的 quantum_service.actuation.writer_mode
        # 决定，每请求读取 → 可热切：
        #   shadow（默认）：只记录"本应经由单写者"，执行路径完全不变；
        #   enforce：真正把实验交给唯一 worker 线程串行执行；
        #   off：关闭。
        from services.quantum_service.writer import resolve_writer_mode

        mode = resolve_writer_mode(self._actuation_flags())

        # full 模式需要"执行前"的 registry 快照，必须在真正下发实验之前取。
        # 异步提交时此刻还没轮到这个 job 上机，快照与数据集基线都会过期，
        # 交给 worker 在真正开始执行前再取（见 _run_job）。
        pre_snapshot = None
        try:
            from services.common.config import _replay_auto_record

            if (not is_async) and _replay_auto_record() == "full" and record.qubits:
                pre_snapshot = self._safe_registry_snapshot(record.qubits[0])
        except Exception:
            pre_snapshot = None

        # 数据集归属（阶段 7）：执行前列一次 DataVault 目录，执行后差集即本次产出。
        # 只读、单次 dir 调用（毫秒级），失败返回 None → 本次不记编号，不猜。
        datasets_before = None if is_async else self._probe_dataset_numbers()

        if mode == "shadow":
            result = self._handle_execute_impl(call_data)
            self._writer.observe(
                "execute",
                task_id=explicit_task,
                run_id=record.run_id,
                governed=record.governed,
            )
        elif mode == "enforce":
            # 真正的单写者：实验本体交给唯一 worker 串行执行。
            # 注意 _handle_execute_impl 内部还会把任务排进"通道 lane"，此处是外层串行化，
            # 不会与 lane 的并发恒 1 冲突（lane 只保证硬件独占，这里保证调用序列）。
            enforced = self._writer.submit(
                lambda: self._handle_execute_impl(call_data),
                op="execute",
                timeout=None,
                task_id=explicit_task,
            )
            if enforced.get("status") == "success":
                result = enforced.get("result")
            else:
                result = normalize_error({
                    "status": enforced.get("status") or "error",
                    "error": enforced.get("error") or "单写者执行失败",
                })
        else:  # off
            result = self._handle_execute_impl(call_data)
        if isinstance(result, dict):
            result.setdefault("writer_mode", mode)

        # 异步提交（producer 化）：立即回 job_id，审计记录在 worker 跑完时补齐。
        if isinstance(result, dict) and result.get("async") and result.get("job_id"):
            return self._handle_async_submission(
                record, result, code, trace_id, trace_is_new, explicit_task,
                not record.governed,
            )

        # 同步路径：结果已经拿到，一次性补齐记录并落盘（与异步共用 _complete_record）。
        result = self._complete_record(
            record, result, datasets_before, pre_snapshot, code, trace_id, explicit_task
        )

        if isinstance(result, dict):
            result = dict(result)
            result["run_id"] = record.run_id
            result["governed"] = record.governed
            result["trace_id"] = trace_id
            result["execute_mode"] = "sync"
            if record.dataset_ids:
                result["dataset_ids"] = list(record.dataset_ids)
                result["dataset_source"] = record.dataset_source
            if record.warnings:
                result["warnings"] = list(result.get("warnings") or []) + record.warnings
            if not record.governed:
                result["governance_hint"] = (
                    "本次执行未带 task_id，未经过 task_queue 额度/票据/审计；"
                    "正式路径见 :3008/workflows/run_ir"
                )
            if trace_is_new:
                result.setdefault(
                    "trace_hint",
                    "本次 trace_id 由服务端生成（调用方未透传 trace_id）",
                )

        return result

    def _complete_record(
        self,
        record: Any,
        result: Any,
        datasets_before: Any,
        pre_snapshot: Optional[Dict[str, Any]],
        code: str,
        trace_id: Optional[str],
        task_id: Optional[str],
    ) -> Any:
        """把一次执行的结果写回 RunRecord 并落盘（同步路径与异步 job 共用）。

        覆盖：状态/错误码、数据集归属（stdout ∪ DataVault 差集）、可判定指标与
        verdict、registry 前后快照与 diff（仅 replay full）、审计落盘、回放 fixture。
        任何异常都不影响执行结果。
        """
        from services.common.dataset_ids import (
            dataset_source_label,
            merge_dataset_ids,
            new_dataset_numbers,
        )
        from services.common.runrecord import (
            RunRecordWriter,
            derive_verdict,
            diff_snapshots,
            extract_metrics,
        )

        if not isinstance(result, dict):
            return result
        status = str(result.get("status") or ("success" if not result.get("error") else "error"))
        record.status = status
        if result.get("error"):
            record.error = str(result.get("error"))[:2000]
            record.error_code = str(result.get("error_code") or "internal_error")

        # 数据集归属：stdout 里厂商打印的编号 ∪ DataVault 目录差集。
        # 后者补上 sq.iqraw/sq.pidf/sq.t1 这些"只写盘不说话"的实验。
        stdout_ids = self._extract_dataset_ids(result)
        probed_ids = new_dataset_numbers(datasets_before, self._probe_dataset_numbers())
        record.dataset_ids = merge_dataset_ids(stdout_ids, probed_ids)
        record.dataset_source = dataset_source_label(stdout_ids, probed_ids)

        # 结果里有判定指标就记账，并用 Metric Registry 给 verdict —— 不编造。
        record.metrics = extract_metrics(result)
        record.verdict = derive_verdict(record.metrics, status=record.status)
        record.finished_at = time.time()

        post_snapshot = None
        if pre_snapshot is not None and record.qubits:
            post_snapshot = self._safe_registry_snapshot(record.qubits[0])
            if post_snapshot:
                record.snapshot_before = pre_snapshot
                record.snapshot_after = post_snapshot
                try:
                    record.diff = diff_snapshots(pre_snapshot, post_snapshot)
                except Exception:
                    record.diff = {}

        try:
            RunRecordWriter().append(record)
        except Exception:
            pass

        self._maybe_record_replay_fixture(
            code, record, result, trace_id, task_id, pre_snapshot, post_snapshot
        )
        return result

    def _handle_async_submission(
        self,
        record: Any,
        result: Dict[str, Any],
        code: str,
        trace_id: Optional[str],
        trace_is_new: bool,
        task_id: Optional[str],
        governed_hint: bool,
    ) -> Dict[str, Any]:
        """异步提交（producer 化）：先落一条 queued 记录，跑完由 worker 补完成记录。

        返回给调用方的最小契约：`{status:"queued", job_id, run_id, trace_id, poll}`；
        真正的结果只能通过 `GET /jobs/{job_id}`（或 `GET /qubit/runs/{run_id}`）取。
        """
        from services.common.runrecord import RunRecordWriter

        job_id = str(result.get("job_id") or "")
        record.job_id = job_id
        record.status = "queued"
        record.finished_at = None

        # 把记录挂到 job 上，worker 跑完时用真实结果补齐（含数据集、metrics）。
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["run_record"] = record
                job["run_id"] = record.run_id
                job["record_code"] = code
                job["record_trace_id"] = trace_id
                job["record_task_id"] = task_id

        try:
            RunRecordWriter().append(record)
        except Exception:
            pass

        result = dict(result)
        result["run_id"] = record.run_id
        result["trace_id"] = trace_id
        result["governed"] = record.governed
        result["execute_mode"] = "async"
        result["poll"] = f"/jobs/{job_id}"
        result["hint"] = (
            "异步执行中：结果用 GET /jobs/{job} 轮询（或用 GET /qubit/runs 按 run_id 查审计）"
        ).format(job=job_id)
        if record.warnings:
            result["warnings"] = list(result.get("warnings") or []) + list(record.warnings)
        if governed_hint:
            result["governance_hint"] = (
                "本次执行未带 task_id，未经过 task_queue 额度/票据/审计；"
                "正式路径见 :3008/workflows/run_ir"
            )
        if trace_is_new:
            result.setdefault(
                "trace_hint", "本次 trace_id 由服务端生成（调用方未透传 trace_id）"
            )
        return result

    def _finalize_async_job(self, job: Dict[str, Any], result: Any,
                            datasets_before: Any, pre_snapshot: Any) -> None:
        """worker 跑完一条异步 job 后补齐 RunRecord（异常只记日志，不影响 job 结果）。

        除了落审计，还要把**完成态结果回写给 job["result"]**：默认异步之后，
        `GET /jobs/{id}` 就是调用方拿结果的唯一入口，如果它还只是 worker 的原始
        返回值（没有 run_id/dataset_ids/execute_mode），异步契约就等于没 producer 化。
        """
        record = job.pop("run_record", None)
        if record is None:
            return
        # trace_id 留在 job 上（不 pop）：/jobs/{id} 要用它对上审计记录。
        trace_id = job.get("record_trace_id")
        code = str(job.pop("record_code", "") or "")
        task_id = job.pop("record_task_id", None)
        try:
            completed = self._complete_record(
                record, result, datasets_before, pre_snapshot, code, trace_id, task_id
            )
            if isinstance(completed, dict) and isinstance(job.get("result"), dict):
                enriched = dict(completed)
                enriched.setdefault("run_id", record.run_id)
                enriched.setdefault("trace_id", trace_id)
                enriched.setdefault("dataset_ids", list(getattr(record, "dataset_ids", None) or []))
                enriched.setdefault("dataset_source", getattr(record, "dataset_source", "unknown"))
                enriched.setdefault("execute_mode", "async")
                job["result"] = enriched
        except Exception as exc:  # pragma: no cover - 审计增强不允许打断执行
            _log(f"[JOB] run record finalize failed for {job.get('job_id')}: {exc}")

    def _maybe_record_replay_fixture(
        self,
        code: str,
        record: Any,
        result: Any,
        trace_id: Optional[str],
        task_id: Optional[str],
        registry_before: Optional[Dict[str, Any]] = None,
        registry_after: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """按 replay_auto_record 模式自动录 fixture；返回 fixture 路径（未录返回 None）。

        任何异常都吞掉——录制是审计增强，绝不能影响真机实验的结果。
        """
        try:
            from services.common.config import _replay_auto_record

            mode = _replay_auto_record()
            if mode == "off":
                return None

            from services.common.replay import ReplayStore, record_fixture

            qubits = list(getattr(record, "qubits", None) or [])
            dataset_ids = list(getattr(record, "dataset_ids", None) or [])
            stdout = ""
            if isinstance(result, dict):
                stdout = str(result.get("stdout") or result.get("output") or "")[:4000]

            if registry_after is None and mode == "full" and qubits:
                registry_after = self._safe_registry_snapshot(qubits[0])

            fixture = record_fixture(
                code=str(code or ""),
                registry_before=registry_before,
                registry_after=registry_after,
                qubits=qubits,
                dataset_ids=dataset_ids,
                run_id=getattr(record, "run_id", None),
                trace_id=trace_id,
                task_id=task_id,
                source="execute",
                status=str(getattr(record, "status", None) or "unknown"),
                stdout=stdout,
            )
            path = self._get_replay_store().save(fixture)
            if path and isinstance(result, dict):
                result["replay_fixture"] = fixture.get("fixture_id")
            return path
        except Exception:
            return None

    def _safe_registry_snapshot(self, qubit: str) -> Optional[Dict[str, Any]]:
        """取一个比特的 registry 快照；失败返回 None（不影响主流程）。

        注意：`_handle_qubit_registry` 返回的是 `{"name", "session_path", "registry", "source"}`
        —— **没有 "success" 键**（成功与否看有没有 "error"）。早前这里按 `payload.get("success")`
        判断，导致快照永远是 None，`full` 模式下 fixture 的 registry_before/after 全是 {}，
        值级漂移检测形同虚设。
        """
        try:
            payload = self._handle_qubit_registry({"name": qubit})
            if not isinstance(payload, dict):
                return None
            if payload.get("error"):
                return None
            reg = payload.get("registry")
            if isinstance(reg, dict) and reg:
                return reg
            return None
        except Exception:
            return None

    @staticmethod
    def _extract_qubit_names(code: Any) -> List[str]:
        """从实验代码里粗略提取比特名（sq.xxx(<name>, ...) 形式）。"""
        import re as _re

        if not isinstance(code, str):
            return []
        found = _re.findall(r"sq\.[A-Za-z_0-9]+\(\s*([A-Za-z_0-9]+)\s*[,)]", code)
        seen: List[str] = []
        for name in found:
            if name not in seen:
                seen.append(name)
        return seen[:20]

    def _probe_dataset_numbers(self) -> Optional[List[int]]:
        """列一次当前 session 的 DataVault 目录（只读，失败返回 None）。

        用于"执行前后取差集 → 本次产出了哪些数据集"（roadmap §2.3/§3 #8）。
        约束：

        - 单次 ``data_vault.dir`` 调用，毫秒级，不写设备；
        - 连接不可用 / session 不可用 / 任何异常 → 返回 ``None``（本次不记编号）；
        - 需要临时关闭时设环境变量 ``QMCLAW_DATASET_PROBE=0``。
        """
        try:
            import os as _os

            if str(_os.environ.get("QMCLAW_DATASET_PROBE", "1")).strip() in ("0", "false", "off"):
                return None
            labrad = self._labrad
            cxn = getattr(labrad, "_cxn", None)
            if cxn is None or not getattr(labrad, "connected", False):
                return None
            from services.common.dataset_ids import read_session_dataset_numbers

            data_vault = cxn.data_vault
            ids, err = read_session_dataset_numbers(
                data_vault.cd,
                data_vault.dir,
                getattr(labrad, "session_path", None) or [],
            )
            if err:
                return None
            return ids
        except Exception:
            return None

    @staticmethod
    def _extract_dataset_ids(result: Any) -> List[int]:
        """从执行结果里尽力提取数据集编号（stdout 里常见 'Dataset: 123'）。"""
        import re as _re

        if not isinstance(result, dict):
            return []
        text = ""
        for key in ("stdout", "output", "result", "log"):
            val = result.get(key)
            if isinstance(val, str):
                text += "\n" + val
        nums = _re.findall(r"dataset[:\s#]*(\d{2,6})", text, flags=_re.I)
        out: List[int] = []
        for n in nums:
            try:
                val = int(n)
            except ValueError:
                continue
            if val not in out:
                out.append(val)
        return out[:50]

    def _handle_execute_impl(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """执行实验代码

        带 busy 锁，防止并发执行。
        离线模拟不碰硬件、不受写闸门限制；在线执行受 actuation.allow_execute 约束。
        """
        code = data.get("code")
        task_id = data.get("task_id", f"task_{int(time.time() * 1000)}")
        timeout = data.get("timeout", 300)

        if not code:
            return {"error": "code is required"}

        # 离线模式下执行模拟实验（不碰硬件，不受闸门限制）
        if self._is_offline_mode:
            return self._execute_offline_simulation(code, task_id, data)

        # 上次执行超时后守护线程可能仍在跑，拒绝后续执行
        if self._exec_stuck:
            return {
                **error_envelope("stuck", self._exec_stuck, task_id=task_id),
                "hint": "上一次执行超时后工作线程可能仍在真机上运行，已拒绝并发下发。"
                        "请人工确认现场安全后重启服务，或调用 /connect 并带 reset_stuck=true。",
            }

        # 写闸门：在线执行需显式翻开 allow_execute
        if not self._actuation_flags()["allow_execute"]:
            return {
                **error_envelope(
                    "read_only",
                    "当前为只读联调，禁止向真机下发实验",
                    task_id=task_id,
                    actuation_allowed=False,
                ),
                "hint": "如需执行请将 fallback_config.json 的 quantum_service.actuation.allow_execute 设为 true，"
                        "或设置环境变量 QMCLAW_ALLOW_EXECUTE=1（须人工确认并记录）",
            }

        # 治理闸门（默认关，可热切）：require_task=true 时，真机执行必须带 task_id，
        # 以保证每次实验都经过 task_queue 的额度/票据/审计（单写者原则，见
        # docs/ARCHITECTURE_ROADMAP.md §2.1）。不带 task_id 的直发请求一律拒绝。
        from services.common.governance import evaluate_require_task

        blocked = evaluate_require_task(data, self._actuation_flags())
        if blocked is not None:
            blocked = dict(blocked)
            blocked["task_id"] = task_id
            return blocked

        # 降级模式下拒绝执行实验
        if self._fallback.is_degraded:
            return {
                **error_envelope(
                    "degraded",
                    "LabRAD 不可用，无法执行实验",
                    task_id=task_id,
                    fallback_message=self._get_fallback_message(),
                ),
            }

        if not self._ensure_connected():
            return {
                **error_envelope("not_connected", "Not connected to LabRAD", task_id=task_id),
            }

        # 异步模式：立即返回 job_id + 排队位置，由通道 worker 执行，调用方轮询 /jobs/{id}
        # 这样调用方超时可以真正“放弃”任务，而不是客户端断开、服务端照跑
        if data.get("async"):
            job_id = self._submit_job(code, task_id, timeout)
            return {
                "task_id": task_id,
                "job_id": job_id,
                "status": "queued",
                "queue_position": self._job_position(job_id),
                "async": True,
            }

        # 同步模式：同样排进通道，再等它跑完（保持旧的调用契约）
        # 以前是"抢不到 busy 锁就回 busy"，会把并发方逼成自己重试；现在排队。
        event = threading.Event()
        job_id = self._submit_job(code, task_id, timeout, wait_event=event)
        try:
            try:
                wait_s = float(timeout) + 30.0
            except (TypeError, ValueError):
                wait_s = 330.0
            finished = event.wait(timeout=wait_s)
        finally:
            self._job_events.pop(job_id, None)
        job = self._jobs.get(job_id) or {}
        if not finished:
            return {
                **error_envelope(
                    "timeout",
                    f"waited {wait_s:.0f}s in hardware lane without finishing",
                    task_id=task_id,
                    job_id=job_id,
                    queue_position=self._job_position(job_id),
                ),
            }
        return job.get("result") or normalize_error({
            "task_id": task_id,
            "job_id": job_id,
            "status": job.get("status") or "error",
            "error": job.get("error") or "",
        })

    def _run_exec_task(self, code: str, task_id: str, timeout) -> Dict[str, Any]:
        """真正执行实验代码：同步路径与异步 job 的 worker 共用这一段。

        调用方必须已经拿到 busy 锁（同步路径在 `_handle_execute` 里拿，
        异步路径在 job worker 里拿）；正常结束时本方法负责释放 busy。
        """
        _log(f"=" * 60)
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 300.0
        _log(f"[EXECUTE] Task: {task_id}")
        _log(f"[EXECUTE] Code:\n{code}")
        _log(f"[EXECUTE] Timeout: {timeout}s")
        _log(f"=" * 60)

        # 结果容器
        result_container: Dict[str, Any] = {
            "status": "idle",
            "stdout": "",
            "stderr": "",
            "error": "",
        }

        # 重定向 stdout/stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        string_out = __import__("io").StringIO()
        string_err = __import__("io").StringIO()
        sys.stdout = string_out
        sys.stderr = string_err

        def _execute():
            # 看门狗必须在**拿锁之前**启动：最常见的卡死位置恰恰是"等 LabRAD 锁"
            # 或"取执行上下文"（qubits/couplers 刷新要走 LabRAD），两者都在 exec 之前。
            # 之前把它放在 exec 前面几行，卡在这些位置时就什么也打不出来（已踩过一次）。
            _real_stderr = sys.__stderr__
            _watchdog_seconds = float(__import__("os").environ.get("QMCLAW_TASK_WATCHDOG", "120"))
            _phase = {"name": "waiting for labrad lock"}

            def _dump_stacks():
                try:
                    import io as _io

                    buf = _io.StringIO()
                    buf.write(
                        f"[Quantum][WATCHDOG] task {task_id} still running after "
                        f"{_watchdog_seconds:.0f}s at phase={_phase['name']!r}, "
                        f"dumping thread stacks: code={code[:120]!r}\n"
                    )
                    for tid, frame in sys._current_frames().items():
                        buf.write(f"--- thread {tid} ---\n")
                        traceback.print_stack(frame, file=buf)
                    report = buf.getvalue()
                    # 先落盘：终端可能已经彻底卡死（假死那一类故障），文件是唯一可回收的证据
                    try:
                        _dump_dir = Path(__file__).parent.parent.parent / "data" / "debug"
                        _dump_dir.mkdir(parents=True, exist_ok=True)
                        _dump_file = _dump_dir / f"quantum_watchdog_{task_id}_{int(time.time())}.txt"
                        _dump_file.write_text(report, encoding="utf-8", errors="replace")
                        report += f"\n[file] {_dump_file}\n"
                    except Exception:
                        pass
                    print(report, file=_real_stderr, flush=True)
                    _real_stderr.flush()
                    # 同时走 _log：终端用的是日志过滤器，且 stdout/stderr 可能被重定向，
                    # 只打 stderr 会出现"看起来什么都没发生"。
                    for line in report.splitlines()[:200]:
                        _log(line)
                except Exception:
                    pass

            _watchdog = threading.Timer(_watchdog_seconds, _dump_stacks)
            _watchdog.daemon = True
            _watchdog.start()
            try:
                with self._labrad_lock:
                    _phase["name"] = "labrad lock held; building exec globals"
                    # 获取执行上下文变量（参考 agent_runner_server.py）
                    cxn = self._labrad.cxn
                    s = self._labrad.s
                    sq = self._labrad.sq
                    data = self._labrad.data
                    qter = self._labrad.qter
                    sample = self._labrad.s

                    # 从 BasicTuner 获取配置
                    BasicTuner = self._labrad._BasicTuner
                    generate_qubit = self._labrad._generate_qubit
                    generate_coupler = self._labrad._generate_coupler

                    # 构建执行上下文（参考 agent_runner_server.py 第247-261行）
                    exec_globals = {
                        "__name__": "__agent__",
                        # 基础对象
                        "cxn": cxn,
                        "s": s,
                        "sq": sq,
                        "data": data,
                        "qter": qter,
                        # numpy 供实验命令里的扫参使用，例如
                        # sq.spectroscopy(q, freq=np.arange(-0.1, 0.1, 0.002) + q.regs.f10)
                        "np": __import__("numpy"),
                        "BasicTuner": BasicTuner,
                        "generate_qubit": generate_qubit,
                        "generate_coupler": generate_coupler,
                        "__builtins__": __builtins__,
                    }

                    # 注入所有 q* 变量（来自量子比特和耦合器）
                    all_qobjs = self._labrad.qubits.copy()
                    all_qobjs.update(self._labrad.couplers)
                    for qname, qobj in all_qobjs.items():
                        exec_globals[qname] = qobj

                # 执行上下文已构建完毕，这里**主动离开 LabRAD 锁**再跑实验。
                # 理由：实验动辄几十秒到几分钟，若整段持锁，`/health`、`/jobs`、
                # 参数读取全部会被堵死——这正是"服务假死、端口还在 LISTENING"的成因。
                # 硬件独占由 busy 锁保证（同一时刻只允许一个任务）。
                _phase["name"] = "exec"

                # 执行代码
                # 注意：不能写成 exec(code, exec_globals, exec_result) 这种"两个命名空间"形式。
                # 分离 globals/locals 会让顶层定义（例如注入片段里的 _qm_ds_max）只落在 locals，
                # 而函数体内的名字查找走 globals → NameError: name '_qm_ds_max' is not defined，
                # 于是数据集探针每次都打 QMCLAW_DATASET_ERR：实验明明成功写出数据，却被判失败。
                _ns_before = set(exec_globals)
                # 便于排查"前端到底下发了什么命令"（例如 spectroscopy 漏传 freq）。
                # 只打前 400 字符：整段代码动辄几千字符，直接把终端/管道写满就会
                # 让写日志变成阻塞操作（假死的另一种成因）。
                _code_preview = code if len(code) <= 400 else code[:400] + f"... <{len(code)} chars total>"
                _log(f"[Quantum] exec: {_code_preview}")
                try:
                    exec(code, exec_globals)

                    # 只回传本次代码新定义的变量，且**只回传能 JSON 序列化的**。
                    # 单命名空间后，lqms 内部往 globals 里塞的东西（LabRAD Symbol、qubit 对象）
                    # 也会出现在这里，直接把 `/jobs/{id}` 打成
                    # `{"error": "Object of type Symbol is not JSON serializable"}` 500。
                    def _json_safe(value) -> bool:
                        if value is None or isinstance(value, (str, int, float, bool)):
                            return True
                        if isinstance(value, (list, tuple)):
                            return all(_json_safe(item) for item in value)
                        if isinstance(value, dict):
                            return all(
                                isinstance(key, str) and _json_safe(item)
                                for key, item in value.items()
                            )
                        return False

                    exec_result = {
                        key: value
                        for key, value in exec_globals.items()
                        if key not in _ns_before
                        and not key.startswith("_")
                        and _json_safe(value)
                    }
                    result_container["status"] = "success"
                    result_container["result"] = exec_result if exec_result else "executed"
                    _log(f"Task {task_id} executed successfully")
                except SyntaxError as e:
                    result_container["status"] = "error"
                    result_container["error"] = f"Syntax error: {e}"
                except NameError as e:
                    result_container["status"] = "error"
                    result_container["error"] = f"Name error: {e}"
                except Exception as e:
                    result_container["status"] = "error"
                    result_container["error"] = traceback.format_exc()
                    _log(f"Task {task_id} error: {e}")
                finally:
                    try:
                        _watchdog.cancel()
                    except Exception:
                        pass

            except Exception as e:
                result_container["status"] = "error"
                result_container["error"] = f"Lock error: {e}"
                try:
                    _watchdog.cancel()
                except Exception:
                    pass

        # 启动执行线程
        exec_thread = threading.Thread(target=_execute)
        exec_thread.daemon = True
        exec_thread.start()
        exec_thread.join(timeout=timeout)

        # 恢复 stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr

        # 获取输出
        result_container["stdout"] = string_out.getvalue()
        result_container["stderr"] = string_err.getvalue()

        # 检查是否超时
        if exec_thread.is_alive():
            result_container["status"] = "timeout"
            result_container["error"] = f"Execution timed out after {timeout}s"
            # 标记卡死并保留 busy：守护线程仍在真机上跑，绝不能放行第二条指令
            self._exec_stuck = (
                f"Task {task_id} timed out after {timeout}s; "
                "worker thread may still be running on hardware"
            )
            _log(f"[STUCK] {self._exec_stuck}")
            return {
                **error_envelope(
                    "timeout",
                    result_container["error"],
                    task_id=task_id,
                    stdout=result_container["stdout"],
                    stderr=result_container["stderr"],
                    stuck=True,
                ),
                "hint": "服务已标记卡死，后续执行/改参将被拒绝，直到人工 reset_stuck 或重启。",
            }

        # 释放 busy 锁（仅正常结束时）
        self._release_busy()

        # 打印执行结果摘要
        status = result_container["status"]
        stdout_len = len(result_container["stdout"])
        stderr_len = len(result_container["stderr"])
        _log(f"=" * 60)
        _log(f"[RESULT] Task: {task_id} | Status: {status}")
        _log(f"[RESULT] stdout: {stdout_len} chars | stderr: {stderr_len} chars")
        if status == "error" and result_container["error"]:
            _log(f"[RESULT] Error: {result_container['error'][:500]}")
        # 打印 stdout 最后几行
        stdout_lines = result_container["stdout"].strip().split('\n')
        if stdout_lines and stdout_lines[-1]:
            last_lines = stdout_lines[-3:] if len(stdout_lines) >= 3 else stdout_lines
            for line in last_lines:
                _log(f"[STDOUT] {line}")
        # 打印 stderr 最后几行
        stderr_lines = result_container["stderr"].strip().split('\n')
        if stderr_lines and stderr_lines[-1]:
            last_lines = stderr_lines[-3:] if len(stderr_lines) >= 3 else stderr_lines
            for line in last_lines:
                _log(f"[STDERR] {line}")
        _log(f"=" * 60)

        return {
            "task_id": task_id,
            "status": result_container["status"],
            "stdout": result_container["stdout"],
            "stderr": result_container["stderr"],
            "error": result_container["error"],
            "result": result_container.get("result"),
        }

    def _handle_switch_session(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """切换会话"""
        from ..common.session import parse_session_path

        session_path, session_error = parse_session_path(data.get("session_path"))
        if session_error:
            return {
                **error_envelope("validation_error", session_error, success=False),
            }
        if session_path is None:
            return {
                **error_envelope("validation_error", "session_path is required", success=False),
            }

        if not self._ensure_connected():
            return {
                **error_envelope("not_connected", "Not connected to LabRAD", success=False),
            }

        with self._labrad_lock:
            success = self._labrad.switch_session(session_path)

            if success:
                return {
                    "success": True,
                    "message": "Session switched",
                    "session_path": session_path,
                    "qubit_count": len(self._labrad.qubits),
                }
            else:
                return {
                    **error_envelope("internal_error", "Session switch failed", success=False),
                }

    def _handle_sessions(self) -> Dict[str, Any]:
        """获取会话列表"""
        if self._fallback.is_degraded:
            return {
                "error": "LabRAD 不可用",
                "sessions": [],
                "current": None,
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            return {
                "error": "Not connected to LabRAD",
                "sessions": [],
                "current": None,
                "fallback_message": self._get_fallback_message(),
            }

        with self._labrad_lock:
            dv = self._labrad.dv
            if dv is None:
                return {"error": "DataVault not available", "sessions": [], "current": None}

            try:
                dv.cd('')  # go to root first
                current_path = dv.pwd()
                _log(f"sessions: root pwd={current_path}")
            except Exception as e:
                _log(f"sessions: pwd error: {e}")
                current_path = "unknown"

            try:
                dirs = dv.dir()
                _log(f"sessions: root dirs[0]={dirs[0]}, dirs[1]={dirs[1][:5] if dirs[1] else []}...")
                groups = [d for d in dirs[0] if not d.startswith('.')]
                sessions = [{"name": g, "path": ['', g]} for g in sorted(groups)]
            except Exception as e:
                _log(f"sessions: dir error: {e}")
                groups = []
                sessions = []

            _log(f"sessions: found {len(sessions)} sessions: {sessions[:3]}...")

            return {
                "current": {
                    "conn_id": str(id(self._labrad)),
                    "name": self._labrad.name,
                    "host": self._labrad.host,
                    "port": self._labrad.port,
                    "connected": self._labrad.connected,
                    "current_dv_path": current_path,
                },
                "sessions": sessions,
            }

    def _handle_session_tree(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取会话目录树"""
        max_depth = data.get("max_depth", 5)

        if self._fallback.is_degraded:
            _log("session_tree: running in degraded mode")
            return {
                "error": "LabRAD 不可用",
                "tree": [],
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            _log("session_tree: LabRAD not connected")
            return {
                "error": "Not connected to LabRAD",
                "tree": [],
                "fallback_message": self._get_fallback_message(),
            }

        with self._labrad_lock:
            dv = self._labrad.dv
            if dv is None:
                _log("session_tree: dv is None")
                return {"error": "DataVault not available", "tree": []}

            _log(f"session_tree: Getting tree from DataVault, max_depth={max_depth}")

            # 测试 DataVault 基本操作
            try:
                _log(f"session_tree: dv type = {type(dv)}")
                _log(f"session_tree: dv str = {str(dv)[:100]}")

                # 尝试 pwd
                try:
                    current = dv.pwd()
                    _log(f"session_tree: current path = {current}")
                except Exception as pwd_err:
                    _log(f"session_tree: pwd error = {pwd_err}")

                # 尝试 cd 到根目录
                try:
                    dv.cd('')
                    pwd_after_cd = dv.pwd()
                    _log(f"session_tree: after cd('') pwd = {pwd_after_cd}")
                except Exception as cd_err:
                    _log(f"session_tree: cd('') error = {cd_err}")

                # 尝试 dir
                try:
                    dirs = dv.dir()
                    _log(f"session_tree: dir() = {dirs}")
                    _log(f"session_tree: dirs[0] (dirs) = {dirs[0]}")
                    _log(f"session_tree: dirs[1] (files) = {dirs[1][:5] if dirs[1] else []}...")
                except Exception as dir_err:
                    _log(f"session_tree: dir() error = {dir_err}")

            except Exception as e:
                _log(f"session_tree: debug error = {e}")

            def get_dir_tree(path: List[str], depth: int = 0) -> List[Dict[str, Any]]:
                """递归获取目录树"""
                if depth >= max_depth:
                    return []
                result = []
                try:
                    # 先 cd 到根目录
                    dv.cd('')
                    # 处理路径 - 去掉开头的空字符串
                    clean_path = path[1:] if path and path[0] == '' else path
                    if clean_path:
                        dv.cd(clean_path)

                    # 检查当前路径
                    try:
                        current = dv.pwd()
                        _log(f"session_tree: pwd={current}")
                    except Exception as pwd_err:
                        _log(f"session_tree: pwd error: {pwd_err}")

                    dirs = dv.dir()
                    _log(f"session_tree: path={path}, dirs[0]={dirs[0]}, dirs[1]={dirs[1][:5] if dirs[1] else []}...")

                    for name in sorted(dirs[0]):
                        if name.startswith('.'):
                            continue
                        child_path = path + [name] if path else ['', name]
                        try:
                            dv.cd('')  # go to root first
                            dv.cd(child_path[1:] if child_path[0] == '' else child_path)
                            subdirs = dv.dir()[0]
                            has_children = any(not d.startswith('.') for d in subdirs)
                            dv.cd('')  # go back to root
                            if clean_path:
                                dv.cd(clean_path)
                        except:
                            has_children = False
                        result.append({
                            "name": name,
                            "path": child_path,
                            "hasChildren": has_children,
                        })
                except Exception as e:
                    _log(f"session_tree error at {path}: {e}")
                return result

            tree = get_dir_tree([''])
            _log(f"session_tree: Returning {len(tree)} top-level entries")

        return {"tree": tree}

    def _handle_qubit_params(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取量子比特参数"""
        qname = data.get("name")
        if not qname:
            return {"error": "Qubit name required"}

        if self._fallback.is_degraded:
            # 降级模式下尝试从本地数据获取
            for q in self._fallback_qubits:
                if q.get("name") == qname:
                    return {
                        "name": qname,
                        "session_path": [],
                        "params": q,
                        "source": "fallback",
                        "fallback_message": self._get_fallback_message(),
                    }
            return {
                "error": f"Qubit {qname} not found in fallback data",
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            return {"error": "Not connected to LabRAD"}

        with self._labrad_lock:
            s = self._labrad.s
            if not s or qname not in s.keys():
                return {"error": f"Qubit {qname} not found"}

            # 唯一读路径：registry 树（与 /qubit/registry 同源），只投影历史键集
            tree, reg_error = self._read_registry_tree(qname)
            if not reg_error and tree:
                flat = self._flatten_registry_tree(tree)
                params = {
                    key: self._legacy_param_value(flat.get(key))
                    for key in self.PARAM_KEYS
                    if key in flat
                }
                return {
                    "name": qname,
                    "session_path": self._labrad.session_path,
                    "params": params,
                    "params_source": "registry",
                }

            # registry 打不开时退回 qubit 对象投影（老路径），并说明来源
            qobj = s[qname]
            return {
                "name": qname,
                "session_path": self._labrad.session_path,
                "params": self._params_from_qubit_object(qobj),
                "params_source": "qubit_object",
                "registry_error": reg_error or "registry 为空",
            }

    # `/qubit/params` 的历史键集：面板按这些键展示/回写，**不要**随手加键
    # （前端会把整个 params 原样 parseFloat 回写，非数值键会变成 NaN 写进 registry）。
    PARAM_KEYS: Tuple[str, ...] = (
        "f10",
        "fread",
        "fc",
        "f21",
        "bias_z",
        "PiGate.amp",
        "PiGate.length",
        "PiGate.alpha",
        "PiGate.zpa",
        "PiHalf.amp",
        "PiHalf.length",
        "PiHalf.alpha",
        "PiHalf.zpa",
        "ReadIn.power",
        "ReadIn.length",
        "ReadIn.ring_power",
        "ReadIn.ring_length",
        "ReadIn.zpa",
        "ReadOut.amp",
        "ReadOut.length",
        "ReadOut.window_type",
        "discriminator.center0",
        "discriminator.center1",
        "discriminator.measure_f0",
        "discriminator.measure_f1",
        "discriminator.method",
        "discriminator.radius0",
        "discriminator.threshold",
    )

    @staticmethod
    def _legacy_param_value(value: Any) -> Optional[float]:
        """registry 值 → `/qubit/params` 的历史形状（数值或 None）。

        老实现用 `float()` 强转：字符串（`window_type`/`method`）与列表
        （`discriminator.center0`）都变成 None。这里保持同样的可见结果，
        免得前端面板把字符串 `parseFloat` 成 NaN 再写回 registry。
        """
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _params_from_qubit_object(qobj: Any) -> Dict[str, Any]:
        """从厂商 qubit 对象投影老键集（registry 不可用时的退路）。"""

        def get_param(obj: Any, key: str) -> Optional[float]:
            try:
                val = getattr(obj, key, None)
                if val is not None:
                    return float(val)
            except Exception:  # noqa: BLE001
                pass
            return None

        def get_nested_param(obj: Any, parent: str, child: str) -> Optional[float]:
            try:
                parent_obj = getattr(obj, parent, None)
                if parent_obj is not None:
                    val = getattr(parent_obj, child, None)
                    if val is not None:
                        return float(val)
            except Exception:  # noqa: BLE001
                pass
            return None

        params: Dict[str, Any] = {
            "f10": get_param(qobj, "f10"),
            "fread": get_param(qobj, "fread"),
            "fc": get_param(qobj, "fc"),
            "f21": get_param(qobj, "f21"),
            "bias_z": get_param(qobj, "bias_z"),
        }
        for parent in ("PiGate", "PiHalf"):
            for child in ("amp", "length", "alpha", "zpa"):
                params[f"{parent}.{child}"] = get_nested_param(qobj, parent, child)
        for child in ("power", "length", "ring_power", "ring_length", "zpa"):
            params[f"ReadIn.{child}"] = get_nested_param(qobj, "ReadIn", child)
        for child in ("amp", "length", "window_type"):
            params[f"ReadOut.{child}"] = get_nested_param(qobj, "ReadOut", child)
        for child in (
            "center0",
            "center1",
            "measure_f0",
            "measure_f1",
            "method",
            "radius0",
            "threshold",
        ):
            params[f"discriminator.{child}"] = get_nested_param(qobj, "discriminator", child)
        return params

    # ── D2：只读 registry 快照端点 ────────────────────────────────────────
    @staticmethod
    def _unwrap_registry_value(v: Any) -> Any:
        """registry 值 -> JSON 友好类型（numpy 标量 / 容器 / 其它转字符串）。"""
        try:
            import numpy as np
        except Exception:
            np = None
        if np is not None:
            if isinstance(v, np.floating):
                return float(v)
            if isinstance(v, np.integer):
                return int(v)
            if isinstance(v, np.bool_):
                return bool(v)
        if isinstance(v, (list, tuple)):
            return [QuantumService._unwrap_registry_value(x) for x in v]
        if isinstance(v, dict):
            return {str(k): QuantumService._unwrap_registry_value(x) for k, x in v.items()}
        if isinstance(v, (int, float, str, bool)) or v is None:
            return v
        return str(v)

    def _registry_handle(self, qname: str):
        """拿某个 qubit 的 registry wrapper。

        `cxn.registry_wrapper` 在 labrad.client.Client 上不存在；正确入口是
        `lqms.pyle.registry_wrapper2.RegistryWrapper(cxn, session)[qubit]`
        （registry_wrapper2 是编译好的 .pyd，只能用不能改）。
        """
        from lqms.pyle import registry_wrapper2

        cxn = getattr(self._labrad, "_cxn", None)
        if cxn is None:
            raise RuntimeError("LabRAD 连接句柄不可用")
        session = self._labrad.session_path or []
        return registry_wrapper2.RegistryWrapper(cxn, session)[qname]

    # ── registry 读取（唯一实现：/qubit/registry、/qubit/params 共用）──────
    def _read_registry_tree(self, qname: str) -> Any:
        """读某个 qubit 的完整 registry 树，返回 `(tree, error)`。

        **调用方必须已持有 `self._labrad_lock` 且已确认连接**（本函数不加锁，
        避免和 `_handle_qubit_params` 的锁嵌套）。单个键读失败不整体失败，
        该键记为 `<unreadable: ...>`，其余照常返回。
        """
        try:
            reg = self._registry_handle(qname)
        except Exception as e:  # noqa: BLE001
            return None, f"registry open failed: {e}"

        out: Dict[str, Any] = {}
        try:
            keys = list(reg.keys())
        except Exception as e:  # noqa: BLE001
            return None, f"registry keys failed: {e}"

        for k in sorted(keys):
            try:
                v = reg[k]
            except Exception as e:  # noqa: BLE001
                out[str(k)] = f"<unreadable: {e}>"
                continue
            if hasattr(v, "keys"):
                # registry_wrapper2 的 keys() 只返回键名，取值要用 v[name]
                try:
                    out[str(k)] = {
                        str(sk): self._unwrap_registry_value(v[sk]) for sk in v.keys()
                    }
                except Exception as e:  # noqa: BLE001
                    out[str(k)] = f"<unreadable: {e}>"
            else:
                out[str(k)] = self._unwrap_registry_value(v)
        return out, ""

    def _handle_qubit_registry(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """只读：摊平某个 qubit 的完整 registry（含 timing.* 等 /qubit/params 拿不到的键）。

        D2：以前 `reg_snapshot.py` 自己连 LabRAD，绕过了本服务的串行锁、忙/卡死判断、
        写闸门与 P3 审计链。现在统一走这个端点。
        """
        qname = data.get("name")
        if not qname:
            return {"error": "Qubit name required"}

        if self._fallback.is_degraded:
            return {
                "error": "LabRAD 不可用（降级模式），无法读取 registry",
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            return {"error": "Not connected to LabRAD"}

        with self._labrad_lock:
            out, error = self._read_registry_tree(qname)
            if error:
                return {"error": error, "name": qname}
            return {
                "name": qname,
                "session_path": self._labrad.session_path,
                "registry": out,
                "source": "labrad",
            }

    def _handle_qubit_set_params(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """设置量子比特参数"""
        qname = data.get("name")
        params = data.get("params", {})

        if not qname:
            return {"success": False, "error": "Qubit name required"}

        # 写闸门：只读联调期间禁止改参
        if not self._actuation_flags()["allow_set_params"]:
            return {
                **error_envelope(
                    "read_only",
                    "当前为只读联调，禁止修改 qubit 参数",
                    success=False,
                    actuation_allowed=False,
                ),
                "hint": "如需改参请将 fallback_config.json 的 quantum_service.actuation.allow_set_params 设为 true，"
                        "或设置环境变量 QMCLAW_ALLOW_SET_PARAMS=1（须人工确认并记录）",
            }

        if self._exec_stuck:
            return {
                **error_envelope("stuck", self._exec_stuck, success=False),
                "hint": "上一次执行超时后工作线程可能仍在真机上运行，已拒绝改参。",
            }

        if self._fallback.is_degraded:
            return {
                **error_envelope(
                    "degraded",
                    "LabRAD 不可用，无法设置参数",
                    success=False,
                    fallback_message=self._get_fallback_message(),
                ),
            }

        if not self._ensure_connected():
            return {
                **error_envelope("not_connected", "Not connected to LabRAD", success=False),
            }

        with self._labrad_lock:
            s = self._labrad.s
            if not s or qname not in s.keys():
                return {"success": False, "error": f"Qubit {qname} not found"}

            res = self._write_qubit_params(qname, params)

            return {
                "success": len(res["errors"]) == 0,
                "name": qname,
                "updated": res["updated"],
                "errors": res["errors"] if res["errors"] else None,
            }

    # ── 寄存器写入（set_params / apply_dataset 共用） ─────────────────────
    def _write_qubit_params(self, qname: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """把 {key: value} 写进 qubit 寄存器；键形如 'f10' / 'PiGate.amp' / 'timing.xy'。

        先写 qubit 对象（厂商对象的正常路径），拿不到的键（timing.* / channels.* /
        correction.* 只存在于 registry）再退回 registry 写 —— 厂商实验改的就是 registry。
        """
        s = self._labrad.s
        if not s or qname not in s.keys():
            return {"updated": [], "errors": [f"Qubit {qname} not found"]}

        qobj = s[qname]
        updated: List[str] = []
        errors: List[str] = []

        for key, value in params.items():
            if value is None:
                continue
            qobj_ok = False
            qobj_err = None
            try:
                parts = key.split(".")
                if len(parts) == 1:
                    setattr(qobj, key, value)
                    qobj_ok = True
                elif len(parts) == 2:
                    parent = getattr(qobj, parts[0], None)
                    if parent is not None:
                        setattr(parent, parts[1], value)
                        qobj_ok = True
                    else:
                        qobj_err = f"{key}: parent not found on qubit object"
                else:
                    qobj_err = f"{key}: unsupported key depth"
            except Exception as e:
                qobj_err = f"{key}: {str(e)}"

            if qobj_ok:
                updated.append(key)
                continue

            # D2：qubit 对象上拿不到的键（timing.* / correction.* / channels.*）
            # 退回 registry 写 —— 厂商实验改的就是 registry。
            try:
                reg = self._registry_handle(qname)
                if "." in key:
                    parent, child = key.split(".", 1)
                    reg[parent][child] = value
                else:
                    reg[key] = value
                updated.append(key)
            except Exception as e:
                errors.append(f"{qobj_err or key}; registry write failed: {e}")

        return {"updated": updated, "errors": errors}

    # ── 数据集参数快照写回（D2 的延伸：现场对齐 / 实验后回滚） ─────────────
    @staticmethod
    def _flatten_registry_tree(tree: Dict[str, Any]) -> Dict[str, Any]:
        """registry 树 -> 扁平 {key: value}（'PiGate.amp' 这种两点键）。"""
        flat: Dict[str, Any] = {}
        for k, v in tree.items():
            if isinstance(v, dict):
                for sk, sv in v.items():
                    flat[f"{k}.{sk}"] = sv
            else:
                flat[str(k)] = v
        return flat

    @staticmethod
    def _values_equal(a: Any, b: Any) -> bool:
        """数值按相对误差比，列表逐元素比，其余按字符串比。"""
        if isinstance(a, bool) or isinstance(b, bool):
            return str(a) == str(b)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            try:
                return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
            except Exception:  # noqa: BLE001
                return False
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            return len(a) == len(b) and all(
                QuantumService._values_equal(x, y) for x, y in zip(a, b))
        return str(a) == str(b)

    def _read_dataset_parameters(self, dataset_id: int) -> Dict[str, Any]:
        """读数据集 parameters（厂商实验把整份寄存器快照写在这里）。"""
        from lqms.data_process import dataAnalysisCore as dc

        cxn = getattr(self._labrad, "_cxn", None)
        if cxn is None:
            raise RuntimeError("LabRAD 连接句柄不可用")
        session = self._labrad.session_path or []
        dl = dc.DataLab(session, cxn.data_vault, dv_type="data_vault")
        dl.loadDataset(int(dataset_id))
        params = getattr(dl, "parameters", None) or {}
        return {str(k): self._unwrap_registry_value(v) for k, v in params.items()}

    def _handle_qubit_apply_dataset(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """把数据集里记录的寄存器快照写回量子比特（实验后对齐现场 / 回滚）。

        厂商实验（s21_zpa2d / iqraw / pidf …）会静默改寄存器 —— 例如 s21 的
        auto-update 回写 fread、宽频 pidf 把 f10 写到错误频点，而数据集的
        `parameters` 里留有实验当时的完整 registry 快照（键形如 `q7ld3.f10`）。
        本端点取出该比特前缀的键，与当前 registry 比对后**只写回有差异的键**。

        请求体：
          name      必填，比特名（如 q7ld3）
          dataset   必填，数据集编号（如 1032）
          skip      可选，跳过的键前缀列表，默认 ["discriminator."]
                    （discriminator 中心是测量拟合产物，会被 iqraw 自动重算，
                      默认不写回；要写回就把它加进 include）
          include   可选，强制写回的键前缀列表（覆盖 skip，并且即使值相同也重写）
          dry_run   可选，true 时只 diff 不写
        """
        qname = data.get("name")
        dataset_id = data.get("dataset")
        if not qname:
            return {"success": False, "error": "Qubit name required"}
        if dataset_id is None:
            return {"success": False, "error": "Dataset id required"}

        try:
            dataset_id = int(dataset_id)
        except (TypeError, ValueError):
            return {"success": False, "error": f"Invalid dataset id: {dataset_id!r}"}

        skip = list(data.get("skip") or ["discriminator."])
        include = list(data.get("include") or [])
        dry_run = bool(data.get("dry_run", False))

        # 写闸门：只读联调期间禁止改参
        if not self._actuation_flags()["allow_set_params"]:
            return {
                **error_envelope(
                    "read_only",
                    "当前为只读联调，禁止修改 qubit 参数",
                    success=False,
                    actuation_allowed=False,
                ),
                "hint": "如需改参请将 fallback_config.json 的 quantum_service.actuation.allow_set_params 设为 true，"
                        "或设置环境变量 QMCLAW_ALLOW_SET_PARAMS=1（须人工确认并记录）",
            }

        if self._exec_stuck:
            return {
                **error_envelope("stuck", self._exec_stuck, success=False),
                "hint": "上一次执行超时后工作线程可能仍在真机上运行，已拒绝改参。",
            }

        if self._fallback.is_degraded:
            return {
                **error_envelope(
                    "degraded",
                    "LabRAD 不可用，无法写回数据集参数",
                    success=False,
                    fallback_message=self._get_fallback_message(),
                ),
            }

        if not self._ensure_connected():
            return {
                **error_envelope("not_connected", "Not connected to LabRAD", success=False),
            }

        with self._labrad_lock:
            try:
                params = self._read_dataset_parameters(dataset_id)
            except Exception as e:
                return {"success": False, "error": f"read dataset failed: {e}",
                        "name": qname, "dataset": dataset_id}

            prefix = f"{qname}."
            snapshot = {k[len(prefix):]: v for k, v in params.items() if k.startswith(prefix)}
            if not snapshot:
                return {
                    "success": False,
                    "error": f"数据集 {dataset_id} 里没有 {prefix}* 键",
                    "name": qname, "dataset": dataset_id,
                    "dataset_keys": sorted(params.keys())[:20],
                }

            try:
                reg = self._registry_handle(qname)
                current: Dict[str, Any] = {}
                for k in sorted(reg.keys()):
                    try:
                        v = reg[k]
                    except Exception as e:  # noqa: BLE001
                        current[str(k)] = f"<unreadable: {e}>"
                        continue
                    if hasattr(v, "keys"):
                        try:
                            for sk in v.keys():
                                current[f"{k}.{sk}"] = self._unwrap_registry_value(v[sk])
                        except Exception as e:  # noqa: BLE001
                            current[str(k)] = f"<unreadable: {e}>"
                    else:
                        current[str(k)] = self._unwrap_registry_value(v)
            except Exception as e:
                return {"success": False, "error": f"registry open failed: {e}",
                        "name": qname, "dataset": dataset_id}

            todo: Dict[str, Any] = {}
            unchanged: List[str] = []
            skipped: List[str] = []
            for k, v in sorted(snapshot.items()):
                forced = any(k.startswith(p) for p in include)
                if any(k.startswith(p) for p in skip) and not forced:
                    skipped.append(k)
                    continue
                if not forced and k in current and self._values_equal(current[k], v):
                    unchanged.append(k)
                    continue
                todo[k] = v

            result = {
                "success": True,
                "name": qname,
                "dataset": dataset_id,
                "dry_run": dry_run,
                "compared": len(snapshot),
                "to_apply": len(todo),
                "unchanged": len(unchanged),
                "skipped": skipped,
                "changes": {k: {"from": current.get(k, "<缺失>"), "to": v} for k, v in todo.items()},
                "session_path": self._labrad.session_path,
            }

            if dry_run or not todo:
                result["updated"] = []
                result["errors"] = None
                result["note"] = "dry-run，未写回" if dry_run else "没有需要写回的差异"
                return result

            res = self._write_qubit_params(qname, todo)
            result["updated"] = res["updated"]
            result["errors"] = res["errors"] or None
            result["success"] = not res["errors"]

            # 写后校验：重新摊平 registry 再比
            try:
                reg2 = self._registry_handle(qname)
                after: Dict[str, Any] = {}
                for k in sorted(reg2.keys()):
                    try:
                        v = reg2[k]
                    except Exception as e:  # noqa: BLE001
                        after[str(k)] = f"<unreadable: {e}>"
                        continue
                    if hasattr(v, "keys"):
                        try:
                            for sk in v.keys():
                                after[f"{k}.{sk}"] = self._unwrap_registry_value(v[sk])
                        except Exception as e:  # noqa: BLE001
                            after[str(k)] = f"<unreadable: {e}>"
                    else:
                        after[str(k)] = self._unwrap_registry_value(v)
                bad = [k for k, v in todo.items() if not self._values_equal(after.get(k), v)]
                result["verified"] = len(todo) - len(bad)
                result["mismatch"] = bad
            except Exception as e:  # noqa: BLE001
                result["verified"] = None
                result["verify_error"] = str(e)

            return result

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态

        可用性判据只有一份：``services/common/availability.py``。
        这里只负责把本服务的现场字段喂进去，并把 ``availability`` 连同历史
        ``status`` / ``labrad_connected`` 等字段一起回给调用方（老消费者不受影响）。
        """
        labrad_ok = self._labrad is not None and self._labrad.connected
        fallback_info = self._fallback.get_status_info()
        busy = self._busy.is_set()
        stuck = self._exec_stuck is not None
        availability = build_availability(
            device="labrad",
            device_connected=labrad_ok,
            mode=self._get_mode(),
            fallback_state=fallback_info["state"],
            offline_available=self._offline_provider is not None
            and self._offline_provider.is_available,
            stuck=stuck,
            busy=busy,
            session_path=self._labrad.session_path if self._labrad else None,
            initializing=not self._init_started,
        )

        return {
            "status": derive_legacy_status(availability),
            "availability": availability,
            "service": "quantum_service",
            "labrad_connected": labrad_ok,
            "fallback_mode": self._fallback.is_degraded,
            "fallback_state": fallback_info["state"],
            "init_started": self._init_started,
            "ready": not busy,
            "busy": busy,
            "current_task": self._current_task_id,
            "running_tasks": len(self._running_tasks),
            "session_path": self._labrad.session_path if self._labrad else None,
            "fallback_message": self._get_fallback_message(),
            "mode": self._get_mode(),
            "lane": self._lane_snapshot(),
            "stuck": stuck,
            "stuck_reason": self._exec_stuck,
            **log_stats(),
            **self._connection_fields(),
        }

    def shutdown(self):
        """关闭服务"""
        # 停止重连定时器
        self._fallback._cancel_retry_timer()
        if self._labrad:
            self._labrad.shutdown()
        super().shutdown()


def main():
    """主入口"""
    service = QuantumService(port=3003)
    run_service(service)


if __name__ == "__main__":
    main()
