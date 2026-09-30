"""
services/analysis_service/server.py - 数据分析服务

提供数据分析和绘图功能：
- DataLab 集成（参考 dp_config.py）
- 数据绘图
- 统计分析
- 实验代码执行（带 busy 锁，防止并发）
"""

import json
import time
import threading
import sys
import traceback
import base64
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config, FallbackManager, ConnectionState
from ..common.offline_data_provider import OfflineDataProvider
from ..common.offline_data_lab import OfflineDataLab, OfflineQubitUpdater
from ..common.offline_variant_generator import VariantGenerator


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[analysis_service] {msg}")


# 图表保存目录 - qmclaw-web 在 qmclaw-server 同级目录下
_qmclaw_server_dir = Path(__file__).parent.parent.parent  # .../qmclaw-server
PLOTS_DIR = _qmclaw_server_dir.parent / "qmclaw-web" / "public" / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)


class AnalysisService(BaseService):
    """数据分析服务

    核心功能（参考 dp_config.py 和 agent_runner_server.py）：
    - LabRAD 连接管理
    - DataLab 初始化
    - 数据加载和操作
    - 绘图生成
    - 统计分析
    - 实验代码执行（带 busy 锁）
    - 离线模式支持
    """

    # 运行模式枚举
    class Mode:
        ONLINE = "online"      # 强制使用 LabRAD
        OFFLINE = "offline"   # 强制使用离线数据
        AUTO = "auto"         # 自动切换

    def __init__(self, port: int = 3004):
        cfg = ServiceConfig(
            name="analysis_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # LabRAD 连接（参考 dp_config.py）
        self._cxn: Optional[Any] = None
        self._dv: Optional[Any] = None
        self._s: Optional[Any] = None
        self._data: Optional[Any] = None
        self._info: Optional[Any] = None
        self._qter: Optional[Any] = None
        self._labrad_lock = threading.Lock()

        # 执行锁（防止并发执行）
        self._busy = threading.Event()
        self._busy_lock = threading.Lock()
        self._current_task_id: Optional[str] = None

        # 绘图配置
        self._default_dpi = 150
        self._default_figsize = (10, 6)

        # 会话路径
        self._session_path: List[str] = []

        # 降级模式管理
        config_path = Path(__file__).parent.parent.parent / "config" / "fallback_config.json"
        fb_config = FallbackManager.load_fallback_config(config_path).get("analysis_service", {})
        self._fallback = FallbackManager(
            service_name="analysis_service",
            config_path=config_path,
            fallback_data_path=fb_config.get("fallback_data_path", "data/fallback/analysis"),
            max_retry_attempts=fb_config.get("retry", {}).get("max_attempts", 5),
            base_retry_delay=fb_config.get("retry", {}).get("base_delay", 2),
            max_retry_delay=fb_config.get("retry", {}).get("max_delay", 30),
        )

        # 降级模式下的缓存数据
        self._fallback_datasets: List[Dict[str, Any]] = []

        # 离线模式管理
        offline_config = fb_config.get("offline_mode", {})
        self._offline_mode_enabled = offline_config.get("enabled", True)
        self._offline_data_path = offline_config.get("data_path", "data/offline_data")

        # 运行模式：online/offline/auto，从配置读取默认值
        default_mode = offline_config.get("default_mode", "auto")
        self._mode = default_mode if default_mode in (self.Mode.ONLINE, self.Mode.OFFLINE, self.Mode.AUTO) else self.Mode.AUTO
        self._mode_lock = threading.Lock()

        # 离线数据提供器
        self._offline_provider: Optional[OfflineDataProvider] = None

        # 变体生成器
        self._variant_generator: Optional[VariantGenerator] = None

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

    def _find_qmclaw_root(self) -> Path:
        """找到 QMClaw 根目录"""
        current = Path(__file__).parent  # .../services/analysis_service
        for _ in range(10):
            if (current / "measure_scripts").exists():
                return current
            parent = current.parent
            if not parent or str(parent) == str(current):
                break
            current = parent
        return Path("D:/QMClaw")

    def _setup_paths(self):
        """设置 Python 路径"""
        root = self._find_qmclaw_root()
        sq_workflow = root / "measure_scripts" / "measure_scripts" / "sq_workflow"
        measure_scripts = root / "measure_scripts" / "measure_scripts"

        for _path in [str(sq_workflow), str(measure_scripts)]:
            if _path not in sys.path and os.path.exists(_path):
                sys.path.insert(0, _path)

    def before_start(self):
        """启动前初始化 - LabRAD 连接失败时进入降级模式"""
        _log("Initializing Analysis service...")

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

        root = self._find_qmclaw_root()
        self._setup_paths()

        # 创建 LabRAD 连接（参考 dp_config.py 第26-27行）
        try:
            import labrad
            from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
            from lqms.utils.save_path import get_info_path

            self._fallback.set_state(ConnectionState.CONNECTING)
            _log("Connecting to LabRAD...")
            self._cxn = labrad.connect()
            self._dv = self._cxn.data_vault
            _log("LabRAD connected")

            # 设置会话（参考 dp_config.py switch_session）
            self._session_path = self._load_session_config()
            user = self._session_path[1] if len(self._session_path) > 1 else "LQHL"

            from lqms.pyle import registry_wrapper2
            self._s = registry_wrapper2.RegistryWrapper(self._cxn, self._session_path)
            _log(f"Session switched to {user}")

            # 初始化 DataLab
            self._data = dc.DataLab(self._session_path, self._dv, dv_type="data_vault")
            _log(f"DataLab initialized")

            # 初始化 InfoBase
            try:
                info_path = get_info_path(self._s)
                self._info = dc.InfoBase(info_path)
                _log("InfoBase loaded")
            except Exception as e:
                _log(f"InfoBase not available: {e}")
                self._info = None

            # 初始化 QubitUpdater（参考 dp_config.py 第58行）
            try:
                self._qter = QubitUpdater(self._data, self._info)
                _log("QubitUpdater initialized")
            except Exception as e:
                _log(f"QubitUpdater not available: {e}")
                self._qter = None

            _log(f"Analysis service ready for session: {'/'.join(self._session_path)}")
            self._fallback.on_connection_success()

        except Exception as e:
            _log(f"Failed to connect to LabRAD: {e}")
            traceback.print_exc()
            self._fallback.on_connection_failed(str(e))
            self._fallback.start_reconnect_timer(self._do_reconnect)

    def _do_reconnect(self) -> bool:
        """执行重连"""
        # 离线模式或自动模式下 LabRAD 不可用时，跳过重连
        with self._mode_lock:
            if self._mode == self.Mode.OFFLINE:
                _log("Skipping reconnect: offline mode enabled")
                self._fallback._cancel_retry_timer()
                return False
            if self._mode == self.Mode.AUTO and self._data is None and not self._fallback.is_connected:
                # AUTO 模式下且从未成功连接过，跳过重连
                _log("Skipping reconnect: auto mode, no previous successful connection")
                self._fallback._cancel_retry_timer()
                return False

        try:
            import labrad
            from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
            from lqms.utils.save_path import get_info_path
            from lqms.pyle import registry_wrapper2

            self._cxn = labrad.connect()
            self._dv = self._cxn.data_vault
            self._session_path = self._load_session_config()
            self._s = registry_wrapper2.RegistryWrapper(self._cxn, self._session_path)
            self._data = dc.DataLab(self._session_path, self._dv, dv_type="data_vault")
            info_path = get_info_path(self._s)
            self._info = dc.InfoBase(info_path)
            self._qter = QubitUpdater(self._data, self._info)
            self._load_fallback_data()
            return True
        except Exception as e:
            self._fallback._last_error = str(e)
            return False

    def _load_fallback_data(self):
        """加载降级数据"""
        try:
            datasets_data = self._fallback.get_fallback_data("datasets.json")
            if datasets_data:
                self._fallback_datasets = datasets_data if isinstance(datasets_data, list) else []
                _log(f"Loaded {len(self._fallback_datasets)} fallback datasets")
        except Exception as e:
            _log(f"Failed to load fallback data: {e}")

    def _init_offline_provider(self):
        """初始化离线数据提供器和变体生成器"""
        if not self._offline_mode_enabled:
            _log("Offline mode is disabled")
            return

        try:
            offline_path = Path(__file__).parent.parent.parent / self._offline_data_path
            self._offline_provider = OfflineDataProvider(str(offline_path))

            if self._offline_provider.load():
                summary = self._offline_provider.get_summary()
                _log(f"Offline data loaded: {summary['total_datasets']} datasets")

                # 初始化变体生成器
                self._variant_generator = VariantGenerator(self._offline_provider)
                _log("Variant generator initialized")
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
            return self._data is None

    def _set_mode(self, mode: str) -> bool:
        """设置运行模式"""
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

    def _load_session_config(self) -> List[str]:
        """加载会话配置"""
        config_path = Path(__file__).parent.parent.parent / "config" / "session.json"

        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                session = config.get("session", {})
                user = session.get("user", "LQHL")
                path = session.get("path", ["test", "20260324"])
                return ["", user] + path
            except Exception:
                pass

        return ["", "LQHL", "test", "20260324"]

    def _switch_session(self, session_path: List[str]):
        """切换会话（参考 dp_config.py switch_session 函数）"""
        import labrad
        from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
        from lqms.utils.save_path import get_info_path
        from lqms.pyle import registry_wrapper2

        user = session_path[1] if len(session_path) > 1 else "LQHL"

        try:
            self._s = registry_wrapper2.RegistryWrapper(self._cxn, session_path)
        except Exception:
            self._s = None

        try:
            self._data = dc.DataLab(session_path, self._dv, dv_type="data_vault")
        except Exception:
            self._data = None

        try:
            info_path = get_info_path(self._s)
            self._info = dc.InfoBase(info_path)
        except Exception:
            self._info = None

        try:
            self._qter = QubitUpdater(self._data, self._info)
        except Exception:
            self._qter = None

        self._session_path = session_path

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理分析请求"""
        # 路由
        if path == "/health":
            return self._handle_health()
        elif path == "/connect":
            return self._handle_connect(data)
        elif path == "/switch_session":
            return self._handle_switch_session(data)
        elif path == "/execute":
            return self._handle_execute(data)
        elif path == "/plot":
            return self._handle_plot(data)
        elif path == "/plot/historical":
            return self._handle_plot_historical(data)
        elif path == "/plot/experiments":
            return self._handle_plot_experiments(data)
        elif path == "/stats":
            return self._handle_stats(data)
        elif path == "/analyze":
            return self._handle_analyze(data)
        elif path == "/datasets":
            return self._handle_datasets(query)
        elif path == "/load":
            return self._handle_load(data)
        elif path == "/mode":
            return self._handle_mode(data)
        elif path == "/datasets/offline":
            return self._handle_datasets_offline(query)
        elif path == "/plot/offline":
            return self._handle_plot_offline(data)
        elif path == "/plot/offline/v2":
            return self._handle_plot_offline_v2(data)
        elif path == "/variants/types":
            return self._handle_variant_types()
        elif path == "/variants/generate":
            return self._handle_generate_variant(data)
        elif path == "/variants/list":
            return self._handle_list_variants(query)
        elif path == "/variants/plot":
            return self._handle_plot_variant(data)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        has_data = self._data is not None
        fallback_info = self._fallback.get_status_info()
        return {
            "status": "healthy" if has_data else "degraded",
            "service": "analysis_service",
            "datalab_connected": has_data,
            "fallback_mode": self._fallback.is_degraded,
            "fallback_state": fallback_info["state"],
            "fallback_message": self._get_fallback_message(),
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "session_path": self._session_path,
            "mode": self._get_mode(),
            "offline_available": self._offline_provider is not None and self._offline_provider.is_available,
        }

    def _handle_mode(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理模式切换请求"""
        # GET 请求（无 mode 参数）返回当前状态
        if "mode" not in data:
            return {
                "success": True,
                "mode": self._get_mode(),
                "offline_available": self._offline_provider is not None and self._offline_provider.is_available,
            }

        mode = data["mode"]
        if not self._set_mode(mode):
            return {
                "success": False,
                "error": f"Invalid mode: {mode}",
                "current_mode": self._get_mode(),
            }

        return {
            "success": True,
            "mode": self._get_mode(),
            "offline_available": self._offline_provider is not None and self._offline_provider.is_available,
        }

    def _handle_datasets_offline(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """获取离线数据集列表"""
        if self._offline_provider is None:
            return {
                "error": "Offline data not available",
                "datasets": [],
            }

        qubit = query.get("qubit", [None])[0] if query.get("qubit") else None
        experiment_type = query.get("experiment_type", [None])[0] if query.get("experiment_type") else None
        date = query.get("date", [None])[0] if query.get("date") else None

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

    def _handle_plot_offline(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """从离线数据绘制图表"""
        job_id = data.get("job_id", f"offline_{int(time.time() * 1000)}")
        dataset_id = data.get("dataset_id")
        command = data.get("command", "")

        if not dataset_id:
            return {"error": "dataset_id is required", "plotPath": None}

        if self._offline_provider is None:
            return {"error": "Offline data not available", "plotPath": None}

        # 获取数据集信息
        ds_info = self._offline_provider.get_dataset(dataset_id)
        if not ds_info:
            return {"error": f"Dataset not found: {dataset_id}", "plotPath": None}

        # 加载 HDF5 数据
        hdf5_data = self._offline_provider.load_hdf5_data(ds_info.file_path)
        if not hdf5_data:
            return {"error": "Failed to load HDF5 data", "plotPath": None}

        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np

            fig = plt.figure(figsize=self._default_figsize)

            if hdf5_data.get("data"):
                data_dict = hdf5_data["data"]
                if "x" in data_dict and "y" in data_dict:
                    x = np.array(data_dict["x"])
                    y = np.array(data_dict["y"])
                elif "x" in data_dict:
                    x = np.arange(len(data_dict["x"]))
                    y = np.array(data_dict["x"])
                else:
                    return {"error": "No plottable data found in HDF5", "plotPath": None}
            else:
                return {"error": "No data found in HDF5", "plotPath": None}

            # 执行自定义命令或默认绘图
            if command and command.strip():
                try:
                    exec(command, {"plt": plt, "np": np, "x": x, "y": y, "fig": fig})
                except Exception as e:
                    _log(f"Plot command error: {e}")
                    plt.plot(x, y, 'b.-')
                    plt.grid(True)
            else:
                plt.plot(x, y, 'b.-')
                plt.xlabel('X')
                plt.ylabel('Y')
                plt.title(f'{ds_info.qubit}: {ds_info.experiment_type} ({ds_info.date})')
                plt.grid(True)

            plt.tight_layout()

            # 保存
            plot_path = PLOTS_DIR / f"{job_id}.png"
            fig.savefig(str(plot_path), dpi=self._default_dpi, bbox_inches='tight')
            plt.close(fig)

            _log(f"Offline plot saved: {plot_path}")

            return {
                "success": True,
                "plotPath": str(plot_path),
                "plotUrl": f"/plots/{job_id}.png",
                "dataset_id": dataset_id,
                "qubit": ds_info.qubit,
                "experiment_type": ds_info.experiment_type,
            }

        except Exception as e:
            _log(f"Offline plot error: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "plotPath": None}

    def _handle_plot_offline_v2(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        新版离线绘图接口

        支持 qter.fitData() 等在线绘图命令格式，
        使用 OfflineDataLab 模拟 DataLab 接口。

        请求格式:
        {
            "dataset_id": "20251005_00001",
            "command": "qter.fitData(collect=True, do_plot=True)"  // 可选
        }

        返回格式:
        {
            "success": true,
            "image": "data:image/png;base64,...",
            "dataset_name": "...",
            "qubit": "...",
            "experiment_type": "..."
        }
        """
        job_id = data.get("job_id", f"offline_v2_{int(time.time() * 1000)}")
        dataset_id = data.get("dataset_id")
        command = data.get("command", "qter.fitData(do_plot=True)")

        if not dataset_id:
            return {"error": "dataset_id is required", "success": False}

        if self._offline_provider is None:
            return {"error": "Offline data not available", "success": False}

        try:
            # 1. 初始化 OfflineDataLab
            offline_lab = OfflineDataLab(self._offline_provider)

            # 2. 加载数据集
            try:
                offline_lab.loadDatasetById(dataset_id)
            except ValueError as e:
                return {"error": str(e), "success": False}

            # 3. 获取数据集信息
            ds_info = offline_lab.get_current_info()
            if not ds_info:
                return {"error": f"Dataset not found: {dataset_id}", "success": False}

            # 4. 初始化 OfflineQubitUpdater
            qter = OfflineQubitUpdater(offline_lab, self._info)

            # 5. 执行绘图命令
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np
            from io import BytesIO

            # 确保是干净的 matplotlib 状态
            plt.close('all')

            # 执行绘图命令
            try:
                # 解析命令，提取参数
                cmd_result = self._execute_plot_command(command, {
                    "qter": qter,
                    "data": offline_lab,
                    "plt": plt,
                    "np": np,
                    "collect": "collect=True" in command,
                    "do_plot": "do_plot=True" in command or "do_plot" not in command,
                })
                _log(f"Plot command executed: {cmd_result}")
            except Exception as plot_err:
                _log(f"Plot command error: {plot_err}")
                # 使用默认绘图作为 fallback
                qter.fitData(collect=True, do_plot=True)

            # 获取所有打开的 figures
            fig_nums = plt.get_fignums()
            if not fig_nums:
                # 如果没有创建 figure，创建一个
                plt.figure()
                data = offline_lab.data
                if data is not None:
                    if data.ndim == 2 and data.shape[1] >= 2:
                        plt.plot(data[:, 0], data[:, 1], 'b.-')
                    else:
                        plt.plot(data.flatten(), 'b.-')
                plt.grid(True)
                plt.title(f"{ds_info.get('qubit', 'Unknown')}: {ds_info.get('experiment_type', '')}")
                fig_nums = plt.get_fignums()

            # 使用最后一个创建的 figure
            fig = plt.figure(fig_nums[-1] if fig_nums else 1)
            plt.tight_layout()

            # 6. 同时保存图像到临时文件和生成 base64
            import uuid
            import os

            # 生成 base64
            buffer = BytesIO()
            fig.savefig(buffer, format='png', dpi=self._default_dpi, bbox_inches='tight')
            buffer.seek(0)
            base64_image = base64.b64encode(buffer.read()).decode('utf-8')

            # 保存到 qmclaw-web/public/plots 目录
            plots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
                                      "qmclaw-web", "public", "plots")
            os.makedirs(plots_dir, exist_ok=True)

            # 生成唯一文件名
            filename = f"temp_{uuid.uuid4().hex[:8]}.png"
            filepath = os.path.join(plots_dir, filename)

            # 保存文件
            fig.savefig(filepath, format='png', dpi=self._default_dpi, bbox_inches='tight')
            plt.close(fig)

            # 返回短 URL (相对路径，供前端使用)
            image_url = f"/plots/{filename}"

            _log(f"Offline v2 plot saved to {filepath}")

            return {
                "success": True,
                "image_url": image_url,
                "image": f"data:image/png;base64,{base64_image}",
                "dataset_id": dataset_id,
                "dataset_name": ds_info.get("name", ""),
                "qubit": ds_info.get("qubit", ""),
                "experiment_type": ds_info.get("experiment_type", ""),
            }

        except Exception as e:
            _log(f"Offline v2 plot error: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "success": False}

    def _execute_plot_command(self, command: str, context: Dict[str, Any]) -> Any:
        """
        执行绘图命令

        Args:
            command: 绘图命令，如 "qter.fitData(collect=True, do_plot=True)"
            context: 执行上下文

        Returns:
            执行结果
        """
        # 解析命令中的参数
        params = {}
        param_match = re.search(r'qter\.fitData\((.*?)\)', command)
        if param_match:
            param_str = param_match.group(1)
            for part in param_str.split(','):
                part = part.strip()
                if '=' in part:
                    key, value = part.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # 转换布尔值
                    if value == 'True':
                        params[key] = True
                    elif value == 'False':
                        params[key] = False
                    else:
                        try:
                            params[key] = int(value)
                        except ValueError:
                            try:
                                params[key] = float(value)
                            except ValueError:
                                params[key] = value

        # 执行 qter.fitData
        qter = context.get("qter")
        if qter and hasattr(qter, 'fitData'):
            return qter.fitData(**params)

        return None

    # ══════════════════════════════════════════════════════════════════════════════
    # 变体生成 API
    # ══════════════════════════════════════════════════════════════════════════════

    def _handle_variant_types(self) -> Dict[str, Any]:
        """获取支持的变体类型列表"""
        if self._variant_generator is None:
            return {"error": "Variant generator not available", "types": []}

        return {
            "success": True,
            "types": self._variant_generator.list_variant_types(),
            "categories": self._variant_generator.list_categories(),
        }

    def _handle_generate_variant(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """生成数据变体"""
        source_id = data.get("source_dataset_id")
        variant_type = data.get("variant_type")
        params = data.get("params", {})
        seed = data.get("seed")

        _log(f"[Variant] Generate request: source_id={source_id}, variant_type={variant_type}")

        if not source_id:
            return {"error": "source_dataset_id is required", "success": False}

        if not variant_type:
            return {"error": "variant_type is required", "success": False}

        if self._variant_generator is None:
            _log("[Variant] Generator not initialized!")
            return {"error": "Variant generator not available", "success": False}

        try:
            result = self._variant_generator.generate_variant(
                source_dataset_id=source_id,
                variant_type=variant_type,
                params=params,
                seed=seed
            )

            _log(f"Variant generated: {result['variant_id']}")

            return {
                "success": True,
                "variant_id": result["variant_id"],
                "source_id": result["source_id"],
                "source_name": result["source_name"],
                "qubit": result["qubit"],
                "experiment_type": result["experiment_type"],
                "variant_type": result["variant_type"],
                "params": result["params"],
                "preview": result["preview"],
            }

        except Exception as e:
            _log(f"Variant generation error: {e}")
            return {"error": str(e), "success": False}

    def _handle_list_variants(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """列出已生成的变体"""
        if self._variant_generator is None:
            return {"error": "Variant generator not available", "variants": []}

        source_id = query.get("source_id", [None])[0] if query.get("source_id") else None

        try:
            variants = self._variant_generator.list_variants(source_id=source_id)
            return {
                "success": True,
                "variants": variants,
                "count": len(variants),
            }
        except Exception as e:
            return {"error": str(e), "success": False}

    def _handle_plot_variant(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """绘制变体数据"""
        variant_id = data.get("variant_id")

        if not variant_id:
            return {"error": "variant_id is required", "success": False}

        if self._variant_generator is None:
            return {"error": "Variant generator not available", "success": False}

        try:
            # 获取变体信息
            variant_info = self._variant_generator.get_variant(variant_id)
            if not variant_info:
                return {"error": f"Variant not found: {variant_id}", "success": False}

            # 加载变体数据
            variant_data = self._variant_generator.load_variant_data(variant_id)
            if not variant_data:
                return {"error": f"Failed to load variant data: {variant_id}", "success": False}

            # 绘图
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np
            from io import BytesIO

            plt.close('all')
            fig, ax = plt.subplots(1, 1, figsize=(10, 6))

            # 绘制数据
            if "x" in variant_data and "y" in variant_data:
                x = np.array(variant_data["x"])
                y = np.array(variant_data["y"])
                ax.plot(x, y, 'b.-', markersize=3, label=f"Variant: {variant_id}")
            elif "data" in variant_data:
                data = np.array(variant_data["data"])
                if data.ndim == 1:
                    ax.plot(data, 'b.-', markersize=3, label=f"Variant: {variant_id}")
                else:
                    ax.plot(data[:, 0], data[:, 1] if data.shape[1] > 1 else data[:, 0],
                            'b.-', markersize=3, label=f"Variant: {variant_id}")

            ax.set_xlabel('X')
            ax.set_ylabel('Y')
            ax.set_title(f"Variant: {variant_info.get('experiment_type', 'Unknown')} - {variant_info.get('qubit', '')}")
            ax.legend()
            ax.grid(True, alpha=0.3)
            plt.tight_layout()

            # 返回图像
            buffer = BytesIO()
            fig.savefig(buffer, format='png', dpi=self._default_dpi, bbox_inches='tight')
            plt.close(fig)
            buffer.seek(0)

            base64_image = base64.b64encode(buffer.read()).decode('utf-8')
            image_data_url = f"data:image/png;base64,{base64_image}"

            return {
                "success": True,
                "image": image_data_url,
                "variant_id": variant_id,
                "source_name": variant_info.get("source_name", ""),
                "qubit": variant_info.get("qubit", ""),
                "experiment_type": variant_info.get("experiment_type", ""),
                "params": variant_info.get("params", {}),
            }

        except Exception as e:
            _log(f"Plot variant error: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "success": False}

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
        with self._labrad_lock:
            try:
                self._setup_paths()

                # 如果已经连接，直接返回成功
                if self._data is not None and self._fallback.is_connected:
                    return {
                        "success": True,
                        "message": "Already connected to DataLab",
                        "session_path": self._session_path,
                    }

                # 重置降级状态
                self._fallback.reset()
                self._fallback.set_state(ConnectionState.CONNECTING)

                import labrad
                from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
                from lqms.utils.save_path import get_info_path
                from lqms.pyle import registry_wrapper2

                _log("Connecting to LabRAD...")
                self._cxn = labrad.connect()
                self._dv = self._cxn.data_vault
                _log("LabRAD connected")

                # 设置会话
                self._session_path = self._load_session_config()
                self._s = registry_wrapper2.RegistryWrapper(self._cxn, self._session_path)
                self._data = dc.DataLab(self._session_path, self._dv, dv_type="data_vault")

                info_path = get_info_path(self._s)
                self._info = dc.InfoBase(info_path)
                self._qter = QubitUpdater(self._data, self._info)

                _log(f"Analysis service connected for session: {'/'.join(self._session_path)}")
                self._fallback.on_connection_success()
                self._load_fallback_data()

                return {
                    "success": True,
                    "message": "Connected to DataLab",
                    "session_path": self._session_path,
                }

            except Exception as e:
                _log(f"Connection failed: {e}")
                traceback.print_exc()
                self._fallback.on_connection_failed(str(e))
                self._fallback.start_reconnect_timer(self._do_reconnect)
                return {
                    "success": False,
                    "error": str(e),
                    "fallback_mode": True,
                    "fallback_message": self._get_fallback_message(),
                }

    def _handle_switch_session(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """切换会话"""
        session_path = data.get("session_path")
        if not session_path:
            return {"success": False, "error": "session_path is required"}

        if isinstance(session_path, str):
            session_path = json.loads(session_path)

        with self._labrad_lock:
            try:
                self._switch_session(session_path)
                return {
                    "success": True,
                    "message": "Session switched",
                    "session_path": self._session_path,
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": str(e),
                }

    def _handle_execute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """执行分析代码（参考 agent_runner_server.py）"""
        # 离线模式下拒绝执行
        if self._is_offline_mode:
            return {
                "task_id": data.get("task_id", f"task_{int(time.time() * 1000)}"),
                "status": "offline",
                "error": "离线模式下无法执行代码。请使用在线模式。",
                "mode": self._get_mode(),
            }

        # 降级模式下拒绝执行
        if self._fallback.is_degraded:
            return {
                "task_id": data.get("task_id", f"task_{int(time.time() * 1000)}"),
                "status": "degraded",
                "error": "LabRAD 不可用，无法执行分析",
                "fallback_message": self._get_fallback_message(),
            }

        if self._data is None:
            return {
                "error": "DataLab not connected",
                "fallback_message": self._get_fallback_message(),
            }

        code = data.get("code")
        task_id = data.get("task_id", f"task_{int(time.time() * 1000)}")
        timeout = data.get("timeout", 300)

        if not code:
            return {"error": "code is required"}

        # 尝试获取 busy 锁
        if not self._try_acquire_busy(task_id):
            return {
                "task_id": task_id,
                "status": "busy",
                "error": "Another task is currently executing",
                "current_task": self._current_task_id,
            }

        _log(f"Executing task {task_id}: {code[:100]}...")

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
                    # 构建执行上下文（参考 dp_config.py 和 agent_runner_server.py）
                    exec_globals = {
                        "__name__": "__analysis__",
                        # 基础对象（参考 dp_config.py）
                        "cxn": self._cxn,
                        "dv": self._dv,
                        "s": self._s,
                        "data": self._data,
                        "info": self._info,
                        "qter": self._qter,
                        "__builtins__": __builtins__,
                    }

                    # 注入 numpy 和 matplotlib
                    import numpy as np
                    import matplotlib
                    matplotlib.use('Agg')
                    import matplotlib.pyplot as plt
                    exec_globals["np"] = np
                    exec_globals["plt"] = plt

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

        return {
            "task_id": task_id,
            "status": result_container["status"],
            "stdout": result_container["stdout"],
            "stderr": result_container["stderr"],
            "error": result_container["error"],
            "result": result_container.get("result"),
        }

    def _handle_plot(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """绘制最新数据集"""
        job_id = data.get("job_id", f"plot_{int(time.time() * 1000)}")
        command = data.get("command", "")
        dataset_index = data.get("dataset_index", -1)

        self._setup_paths()

        try:
            import matplotlib
            matplotlib.use('Agg')  # 非交互式后端
            import matplotlib.pyplot as plt
            import numpy as np

            if self._data is None:
                return {"error": "DataLab not connected", "plotPath": None}

            # 加载数据
            self._data.loadDataset(dataset_index)
            x = self._data.data[:, 0]
            y = self._data.data[:, 1] if self._data.data.shape[1] > 1 else self._data.data[:, 0]

            # 创建图表
            fig = plt.figure(figsize=self._default_figsize)

            if command and command.strip():
                # 执行自定义命令
                try:
                    exec(command, {"plt": plt, "np": np, "x": x, "y": y, "fig": fig})
                except Exception as e:
                    _log(f"Plot command error: {e}")
                    plt.plot(x, y, 'b.-')
                    plt.grid(True)
            else:
                plt.plot(x, y, 'b.-')
                plt.xlabel('X')
                plt.ylabel('Y')
                plt.title(f'Dataset: {getattr(self._data, "dataset_name", "Latest")}')
                plt.grid(True)

            plt.tight_layout()

            # 保存
            plot_path = PLOTS_DIR / f"{job_id}.png"
            fig.savefig(str(plot_path), dpi=self._default_dpi, bbox_inches='tight')
            plt.close(fig)

            _log(f"Plot saved: {plot_path}")

            return {
                "success": True,
                "plotPath": str(plot_path),
                "plotUrl": f"/plots/{job_id}.png",
            }

        except Exception as e:
            _log(f"Plot error: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "plotPath": None}

    def _handle_plot_historical(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """绘制历史数据集"""
        job_id = data.get("job_id", f"hist_{int(time.time() * 1000)}")
        name = data.get("name")
        path_segments = data.get("path", [])
        command = data.get("command", "")

        if not name:
            return {"error": "name is required", "plotPath": None}

        self._setup_paths()

        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np

            if self._data is None:
                return {"error": "DataLab not connected", "plotPath": None}

            # 处理 path - 支持字符串 "LQHL/test" 或数组 ["LQHL", "test"]
            if isinstance(path_segments, str):
                path_segments = path_segments.strip('/').split('/') if path_segments else []

            # 切换到指定目录
            full_path = [""] + path_segments if path_segments else [""]

            # 先切换 DataLab 到目标目录
            self._data.switch_session(full_path)

            # 查找数据集编号
            ds_num = self._data.find_ds_num(name)
            if not ds_num:
                return {"error": f"Dataset not found: {name}", "plotPath": None}

            # 加载数据集（第一个匹配的编号）
            self._data.loadDataset(ds_num[0] if isinstance(ds_num, list) else ds_num)
            x = self._data.data[:, 0]
            y = self._data.data[:, 1] if self._data.data.shape[1] > 1 else self._data.data[:, 0]

            # 创建图表
            fig = plt.figure(figsize=self._default_figsize)

            if command and command.strip():
                try:
                    exec(command, {"plt": plt, "np": np, "x": x, "y": y, "fig": fig})
                except Exception as e:
                    _log(f"Historical plot command error: {e}")
                    plt.plot(x, y, 'b.-')
                    plt.grid(True)
            else:
                plt.plot(x, y, 'b.-')
                plt.xlabel('X')
                plt.ylabel('Y')
                plt.title(f'Historical: {name}')
                plt.grid(True)

            plt.tight_layout()

            # 保存
            plot_path = PLOTS_DIR / f"{job_id}.png"
            fig.savefig(str(plot_path), dpi=self._default_dpi, bbox_inches='tight')
            plt.close(fig)

            _log(f"Historical plot saved: {plot_path}")

            return {
                "success": True,
                "plotPath": str(plot_path),
                "plotUrl": f"/plots/{job_id}.png",
                "datasetName": name,
            }

        except Exception as e:
            _log(f"Historical plot error: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "plotPath": None}

    def _parse_dataset_name(self, name: str) -> Dict[str, Any]:
        """解析数据集名称，返回实验元数据

        格式: "00657 - q21_11: IQraw"
        返回: {"exp_num": 657, "qubit": "q21_11", "exp_type": "iqraw"}
        """
        import re
        # 匹配格式: "00657 - q21_11: IQraw"
        match = re.match(r"(\d+)\s*-\s*(\S+):\s*(\S+)", name)
        if not match:
            raise ValueError(f"Invalid dataset name format: {name}")

        return {
            "exp_num": int(match.group(1)),
            "qubit": match.group(2),
            "exp_type": match.group(3).lower(),  # "IQraw" -> "iqraw"
        }

    def _get_experiment_config(self, exp_type: str) -> Optional[Dict[str, Any]]:
        """获取实验配置"""
        try:
            config_path = Path(__file__).parent.parent.parent / "config" / "experiment_configs.json"
            with open(config_path, "r", encoding="utf-8") as f:
                configs = json.load(f)
                return configs.get("experiments", {}).get(exp_type)
        except Exception as e:
            _log(f"Failed to load experiment config: {e}")
            return None

    def _substitute_plot_command(self, command: str, exp_num: int, qubit: str) -> str:
        """替换绘图命令中的占位符"""
        return command \
            .replace("{exp_num}", str(exp_num)) \
            .replace("{qubit}", qubit)

    def _handle_plot_experiments(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """根据实验类型绘图（新版）

        流程：
        1. 解析数据集名称获取 {exp_num, qubit, exp_type}
        2. 根据 exp_type 获取绘图配置
        3. 生成绘图命令并执行
        4. 返回 Base64 编码的图像
        """
        name = data.get("name")  # "00657 - q21_11: IQraw"
        path = data.get("path")  # "LQHL/test/20260822_device1"

        if not name:
            return {"error": "name is required"}

        self._setup_paths()

        try:
            # 1. 解析实验元数据
            parsed = self._parse_dataset_name(name)
            _log(f"Parsed dataset: exp_num={parsed['exp_num']}, qubit={parsed['qubit']}, exp_type={parsed['exp_type']}")

            # 2. 获取实验配置
            config = self._get_experiment_config(parsed["exp_type"])
            if not config:
                return {"error": f"Unknown experiment type: {parsed['exp_type']}"}

            # 3. 生成绘图命令
            plot_cmd = self._substitute_plot_command(
                config.get("defaultPlotCommand", "qter.fitData({exp_num})"),
                exp_num=parsed["exp_num"],
                qubit=parsed["qubit"]
            )
            _log(f"Plot command: {plot_cmd}")

            # 4. 确保 DataLab 已连接
            if self._data is None:
                return {"error": "DataLab not connected"}

            # 5. 确保 QubitUpdater 已初始化
            if self._qter is None:
                try:
                    from lqms.data_process import QubitUpdater
                    self._qter = QubitUpdater(self._data, self._info)
                    _log("QubitUpdater initialized on demand")
                except Exception as e:
                    _log(f"QubitUpdater initialization failed: {e}")

            # 6. 切换到目标目录
            if path:
                # path 可能是字符串 "LQHL/test/20260822_device1" 或数组 ["LQHL", "test", "20260822_device1"]
                if isinstance(path, str):
                    path_segments = path.strip('/').split('/') if path else []
                else:
                    path_segments = path
                full_path = [""] + path_segments
                self._data.switch_session(full_path)

            # 6. 加载指定编号的数据集
            self._data.loadDataset(parsed["exp_num"])

            # 7. 执行绘图（不创建 fig，让 qter.fitData 自己创建）
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np
            from io import BytesIO

            # 确保是干净的 matplotlib 状态
            plt.close('all')

            # 执行绘图命令（qter.fitData 会创建自己的 figure）
            try:
                exec(plot_cmd, {
                    "plt": plt,
                    "np": np,
                    "qter": self._qter,
                    "data": self._data,
                })
            except Exception as plot_err:
                _log(f"Plot command error: {plot_err}")
                # 使用默认绘图作为 fallback
                plt.figure()
                plt.plot(self._data.data[:, 0], self._data.data[:, 1] if self._data.data.shape[1] > 1 else self._data.data[:, 0], 'b.-')
                plt.grid(True)

            # 获取所有打开的 figures
            fig_nums = plt.get_fignums()
            if not fig_nums:
                return {"error": "No figure was created by the plot command"}

            # 使用最后一个创建的 figure（qter.fitData 通常创建新的）
            fig = plt.figure(fig_nums[-1])
            plt.tight_layout()

            # 8. 返回 Base64 编码的图像
            buffer = BytesIO()
            fig.savefig(buffer, format='png', dpi=self._default_dpi, bbox_inches='tight')
            plt.close(fig)
            buffer.seek(0)

            base64_image = base64.b64encode(buffer.read()).decode('utf-8')
            image_data_url = f"data:image/png;base64,{base64_image}"

            _log(f"Plot generated successfully for {name}")

            return {
                "success": True,
                "image": image_data_url,
                "exp_num": parsed["exp_num"],
                "qubit": parsed["qubit"],
                "exp_type": parsed["exp_type"],
                "dataset_name": name,
                "plot_command": plot_cmd,
            }

        except Exception as e:
            _log(f"Plot experiments error: {e}\n{traceback.format_exc()}")
            return {"error": str(e)}

    def _handle_stats(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """计算数据统计"""
        dataset_index = data.get("dataset_index", -1)
        axis = data.get("axis", None)  # None=全部, 0=x, 1=y

        self._setup_paths()

        try:
            if self._data is None:
                return {"error": "DataLab not connected"}

            # 加载数据
            self._data.loadDataset(dataset_index)

            result: Dict[str, Any] = {}

            if axis is None or axis == 0:
                x = self._data.data[:, 0]
                result["x"] = {
                    "mean": float(np.mean(x)),
                    "std": float(np.std(x)),
                    "min": float(np.min(x)),
                    "max": float(np.max(x)),
                    "count": len(x),
                }

            if axis is None or axis == 1:
                y = self._data.data[:, 1] if self._data.data.shape[1] > 1 else self._data.data[:, 0]
                result["y"] = {
                    "mean": float(np.mean(y)),
                    "std": float(np.std(y)),
                    "min": float(np.min(y)),
                    "max": float(np.max(y)),
                    "count": len(y),
                }

            return {"success": True, "stats": result}

        except Exception as e:
            _log(f"Stats error: {e}")
            return {"error": str(e)}

    def _handle_analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """分析数据集并返回结果"""
        dataset_index = data.get("dataset_index", -1)
        analysis_type = data.get("type", "basic")  # basic, peak, fit

        self._setup_paths()

        try:
            import numpy as np
            from scipy import signal, optimize

            if self._data is None:
                return {"error": "DataLab not connected"}

            # 加载数据
            self._data.loadDataset(dataset_index)
            x = self._data.data[:, 0]
            y = self._data.data[:, 1] if self._data.data.shape[1] > 1 else self._data.data[:, 0]

            result: Dict[str, Any] = {
                "dataset_name": getattr(self._data, "dataset_name", "Unknown"),
                "point_count": len(x),
                "x_range": [float(np.min(x)), float(np.max(x))],
                "y_range": [float(np.min(y)), float(np.max(y))],
            }

            if analysis_type == "basic":
                result["statistics"] = {
                    "x_mean": float(np.mean(x)),
                    "x_std": float(np.std(x)),
                    "y_mean": float(np.mean(y)),
                    "y_std": float(np.std(y)),
                    "y_peak": float(np.max(y)),
                    "y_valley": float(np.min(y)),
                    "y_peak_idx": int(np.argmax(y)),
                }

            elif analysis_type == "peak":
                # 峰值检测
                peaks, properties = signal.find_peaks(y, prominence=0.1)
                result["peaks"] = {
                    "indices": peaks.tolist(),
                    "x_values": x[peaks].tolist() if len(peaks) > 0 else [],
                    "y_values": y[peaks].tolist() if len(peaks) > 0 else [],
                    "count": len(peaks),
                }

            elif analysis_type == "fit":
                # 简单高斯拟合示例
                try:
                    def gaussian(x, amplitude, mean, sigma):
                        return amplitude * np.exp(-(x - mean)**2 / (2 * sigma**2))

                    # 初始估计
                    amplitude_est = np.max(y) - np.min(y)
                    mean_est = x[np.argmax(y)]
                    sigma_est = (np.max(x) - np.min(x)) / 10

                    popt, _ = optimize.curve_fit(gaussian, x, y,
                                                  p0=[amplitude_est, mean_est, sigma_est],
                                                  maxfev=5000)
                    result["fit"] = {
                        "amplitude": float(popt[0]),
                        "mean": float(popt[1]),
                        "sigma": float(popt[2]),
                        "fwhm": float(2.355 * popt[2]),
                    }
                except Exception as fit_err:
                    result["fit_error"] = str(fit_err)

            return {"success": True, "analysis": result}

        except Exception as e:
            _log(f"Analysis error: {e}\n{traceback.format_exc()}")
            return {"error": str(e)}

    def _handle_datasets(self, query: Dict[str, List[str]]) -> Dict[str, Any]:
        """列出数据集"""
        path = query.get("path", [""])[0] if query.get("path") else ""

        self._setup_paths()

        try:
            # 降级模式下返回本地数据
            if self._fallback.is_degraded:
                return {
                    "success": True,
                    "path": path,
                    "datasets": self._fallback_datasets,
                    "source": "fallback",
                    "fallback_message": self._get_fallback_message(),
                }

            if self._data is None:
                return {
                    "error": "DataLab not connected",
                    "datasets": self._fallback_datasets,
                    "source": "fallback",
                    "fallback_message": self._get_fallback_message(),
                }

            # 获取目录内容
            datasets = self._data.get_dir_contents(path)
            return {
                "success": True,
                "path": path,
                "datasets": datasets,
                "source": "live",
            }

        except Exception as e:
            _log(f"Datasets error: {e}")
            return {"error": str(e)}

    def _handle_load(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """加载数据集"""
        dataset_index = data.get("index", -1)
        name = data.get("name")

        self._setup_paths()

        try:
            if self._data is None:
                return {"error": "DataLab not connected"}

            if name:
                ds_num = self._data.find_ds_num(name)
                if not ds_num:
                    return {"error": f"Dataset not found: {name}"}
                self._data.loadDataset(ds_num[0] if isinstance(ds_num, list) else ds_num)
            else:
                self._data.loadDataset(dataset_index)

            return {
                "success": True,
                "dataset_name": getattr(self._data, "dataset_name", "Unknown"),
                "shape": list(self._data.data.shape),
                "columns": self._data.data.shape[1],
            }

        except Exception as e:
            _log(f"Load error: {e}")
            return {"error": str(e)}

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        has_data = self._data is not None
        fallback_info = self._fallback.get_status_info()

        # 判断是否应该报告为健康
        # - DataLab 已连接: healthy
        # - 离线模式: healthy (degraded 是预期状态)
        # - 自动模式 + 离线数据可用: healthy
        # - 在线模式 + DataLab 未连接: degraded (真正的异常)
        if has_data:
            is_healthy = True
        elif self._get_mode() == self.Mode.OFFLINE:
            is_healthy = True  # 离线模式，degraded 是预期状态
        elif self._get_mode() == self.Mode.AUTO and self._offline_provider is not None:
            is_healthy = True  # 自动模式有离线数据兜底
        else:
            is_healthy = False  # 在线模式但 DataLab 不可用，才是异常

        return {
            "status": "healthy" if is_healthy else "degraded",
            "service": "analysis_service",
            "datalab_connected": has_data,
            "fallback_mode": self._fallback.is_degraded,
            "fallback_state": fallback_info["state"],
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "session_path": self._session_path,
            "fallback_message": self._get_fallback_message(),
            "mode": self._get_mode(),
        }

    def shutdown(self):
        """关闭服务"""
        # 停止重连定时器
        self._fallback._cancel_retry_timer()
        if self._cxn:
            try:
                self._cxn.disconnect()
            except Exception:
                pass
        super().shutdown()


def main():
    """主入口"""
    service = AnalysisService(port=3004)
    run_service(service)


if __name__ == "__main__":
    main()
