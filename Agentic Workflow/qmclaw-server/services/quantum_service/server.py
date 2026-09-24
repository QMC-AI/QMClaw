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
from typing import Any, Dict, List, Optional

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config, FallbackManager, ConnectionState
from ..common.offline_data_provider import OfflineDataProvider
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
        elif path == "/switch_session":
            return self._handle_switch_session(data)
        elif path == "/sessions":
            return self._handle_sessions()
        elif path == "/session_tree":
            return self._handle_session_tree(data)
        elif path == "/qubit/params":
            return self._handle_qubit_params(data)
        elif path == "/qubit/set_params":
            return self._handle_qubit_set_params(data)
        elif path == "/mode":
            return self._handle_mode(data)
        elif path == "/datasets":
            return self._handle_datasets(data)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        labrad_ok = self._labrad is not None and self._labrad.connected
        fallback_info = self._fallback.get_status_info()
        return {
            "status": "healthy" if labrad_ok else "degraded",
            "service": "quantum_service",
            "labrad_connected": labrad_ok,
            "fallback_mode": self._fallback.is_degraded,
            "fallback_state": fallback_info["state"],
            "fallback_message": self._get_fallback_message(),
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "running_tasks": len(self._running_tasks),
            "mode": self._get_mode(),
            "offline_available": self._offline_provider is not None and self._offline_provider.is_available,
            **self._connection_fields(),
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
        session_path = data.get("session_path")
        if session_path:
            if isinstance(session_path, str):
                session_path = json.loads(session_path)

        timeout = data.get("timeout", 60)

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

    def _handle_execute(self, data: Dict[str, Any]) -> Dict[str, Any]:
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

        # 写闸门：在线执行需显式翻开 allow_execute
        if not self._actuation_flags()["allow_execute"]:
            return {
                "task_id": task_id,
                "status": "read_only",
                "error": "当前为只读联调，禁止向真机下发实验",
                "hint": "如需执行请将 fallback_config.json 的 quantum_service.actuation.allow_execute 设为 true，"
                        "或设置环境变量 QMCLAW_ALLOW_EXECUTE=1（须人工确认并记录）",
                "actuation_allowed": False,
            }

        # 降级模式下拒绝执行实验
        if self._fallback.is_degraded:
            return {
                "task_id": task_id,
                "status": "degraded",
                "error": "LabRAD 不可用，无法执行实验",
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            return {
                "task_id": task_id,
                "status": "error",
                "error": "Not connected to LabRAD",
            }

        # 尝试获取 busy 锁
        if not self._try_acquire_busy(task_id):
            return {
                "task_id": task_id,
                "status": "busy",
                "error": "Another task is currently executing",
                "current_task": self._current_task_id,
            }

        # 打印完整执行代码
        _log(f"=" * 60)
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
            try:
                with self._labrad_lock:
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

                    # 执行代码
                    exec_result = {}
                    try:
                        exec(code, exec_globals, exec_result)
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

            except Exception as e:
                result_container["status"] = "error"
                result_container["error"] = f"Lock error: {e}"

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

        # 释放 busy 锁
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
        session_path = data.get("session_path")
        if not session_path:
            return {"success": False, "error": "session_path is required"}

        if isinstance(session_path, str):
            session_path = json.loads(session_path)

        if not self._ensure_connected():
            return {"success": False, "error": "Not connected to LabRAD"}

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
                return {"success": False, "error": "Session switch failed"}

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

            qobj = s[qname]
            params = {}

            # Helper function to safely get parameter
            def get_param(obj, key, default=None):
                try:
                    val = getattr(obj, key, None)
                    if val is not None:
                        return float(val)
                except:
                    pass
                return default

            def get_nested_param(obj, parent, child, default=None):
                try:
                    parent_obj = getattr(obj, parent, None)
                    if parent_obj is not None:
                        val = getattr(parent_obj, child, None)
                        if val is not None:
                            return float(val)
                except:
                    pass
                return default

            # Basic parameters
            params["f10"] = get_param(qobj, 'f10')
            params["fread"] = get_param(qobj, 'fread')
            params["fc"] = get_param(qobj, 'fc')
            params["f21"] = get_param(qobj, 'f21')
            params["bias_z"] = get_param(qobj, 'bias_z')

            # PiGate parameters
            params["PiGate.amp"] = get_nested_param(qobj, 'PiGate', 'amp')
            params["PiGate.length"] = get_nested_param(qobj, 'PiGate', 'length')
            params["PiGate.alpha"] = get_nested_param(qobj, 'PiGate', 'alpha')
            params["PiGate.zpa"] = get_nested_param(qobj, 'PiGate', 'zpa')

            # PiHalf parameters
            params["PiHalf.amp"] = get_nested_param(qobj, 'PiHalf', 'amp')
            params["PiHalf.length"] = get_nested_param(qobj, 'PiHalf', 'length')
            params["PiHalf.alpha"] = get_nested_param(qobj, 'PiHalf', 'alpha')
            params["PiHalf.zpa"] = get_nested_param(qobj, 'PiHalf', 'zpa')

            # ReadIn parameters
            params["ReadIn.power"] = get_nested_param(qobj, 'ReadIn', 'power')
            params["ReadIn.length"] = get_nested_param(qobj, 'ReadIn', 'length')
            params["ReadIn.ring_power"] = get_nested_param(qobj, 'ReadIn', 'ring_power')
            params["ReadIn.ring_length"] = get_nested_param(qobj, 'ReadIn', 'ring_length')
            params["ReadIn.zpa"] = get_nested_param(qobj, 'ReadIn', 'zpa')

            # ReadOut parameters
            params["ReadOut.amp"] = get_nested_param(qobj, 'ReadOut', 'amp')
            params["ReadOut.length"] = get_nested_param(qobj, 'ReadOut', 'length')
            params["ReadOut.window_type"] = get_nested_param(qobj, 'ReadOut', 'window_type')

            # Discriminator parameters
            params["discriminator.center0"] = get_nested_param(qobj, 'discriminator', 'center0')
            params["discriminator.center1"] = get_nested_param(qobj, 'discriminator', 'center1')
            params["discriminator.measure_f0"] = get_nested_param(qobj, 'discriminator', 'measure_f0')
            params["discriminator.measure_f1"] = get_nested_param(qobj, 'discriminator', 'measure_f1')
            params["discriminator.method"] = get_nested_param(qobj, 'discriminator', 'method')
            params["discriminator.radius0"] = get_nested_param(qobj, 'discriminator', 'radius0')
            params["discriminator.threshold"] = get_nested_param(qobj, 'discriminator', 'threshold')

            return {
                "name": qname,
                "session_path": self._labrad.session_path,
                "params": params,
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
                "success": False,
                "status": "read_only",
                "error": "当前为只读联调，禁止修改 qubit 参数",
                "hint": "如需改参请将 fallback_config.json 的 quantum_service.actuation.allow_set_params 设为 true，"
                        "或设置环境变量 QMCLAW_ALLOW_SET_PARAMS=1（须人工确认并记录）",
                "actuation_allowed": False,
            }

        if self._fallback.is_degraded:
            return {
                "success": False,
                "error": "LabRAD 不可用，无法设置参数",
                "fallback_message": self._get_fallback_message(),
            }

        if not self._ensure_connected():
            return {"success": False, "error": "Not connected to LabRAD"}

        with self._labrad_lock:
            s = self._labrad.s
            if not s or qname not in s.keys():
                return {"success": False, "error": f"Qubit {qname} not found"}

            qobj = s[qname]
            updated = []
            errors = []

            for key, value in params.items():
                if value is None:
                    continue
                try:
                    parts = key.split(".")
                    if len(parts) == 1:
                        setattr(qobj, key, value)
                        updated.append(key)
                    elif len(parts) == 2:
                        parent = getattr(qobj, parts[0], None)
                        if parent is not None:
                            setattr(parent, parts[1], value)
                            updated.append(key)
                        else:
                            errors.append(f"{key}: parent not found")
                except Exception as e:
                    errors.append(f"{key}: {str(e)}")

            return {
                "success": len(errors) == 0,
                "name": qname,
                "updated": updated,
                "errors": errors if errors else None,
            }

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        labrad_ok = self._labrad is not None and self._labrad.connected
        fallback_info = self._fallback.get_status_info()

        # 判断是否应该报告为健康
        # - LabRAD 已连接: healthy
        # - 离线模式: healthy (degraded 是预期状态)
        # - 自动模式 + 离线数据可用: healthy
        # - 在线模式 + LabRAD 未连接: degraded (真正的异常)
        if labrad_ok:
            is_healthy = True
        elif self._get_mode() == self.Mode.OFFLINE:
            is_healthy = True  # 离线模式，degraded 是预期状态
        elif self._get_mode() == self.Mode.AUTO and self._offline_provider is not None:
            is_healthy = True  # 自动模式有离线数据兜底
        else:
            is_healthy = False  # 在线模式但 LabRAD 不可用，才是异常

        return {
            "status": "healthy" if is_healthy else "degraded",
            "service": "quantum_service",
            "labrad_connected": labrad_ok,
            "fallback_mode": self._fallback.is_degraded,
            "fallback_state": fallback_info["state"],
            "init_started": self._init_started,
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "running_tasks": len(self._running_tasks),
            "session_path": self._labrad.session_path if self._labrad else None,
            "fallback_message": self._get_fallback_message(),
            "mode": self._get_mode(),
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
