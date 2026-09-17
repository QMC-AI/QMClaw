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
from ..common import setup_logging, config


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
    """

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
        """启动前初始化（参考 dp_config.py）"""
        _log("Initializing Analysis service...")

        root = self._find_qmclaw_root()
        self._setup_paths()

        # 创建 LabRAD 连接（参考 dp_config.py 第26-27行）
        try:
            import labrad
            from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
            from lqms.utils.save_path import get_info_path

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

        except Exception as e:
            _log(f"Failed to connect to LabRAD: {e}")
            traceback.print_exc()

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
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        has_data = self._data is not None
        return {
            "status": "healthy" if has_data else "degraded",
            "service": "analysis_service",
            "datalab_connected": has_data,
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "session_path": self._session_path,
        }

    def _handle_connect(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """手动触发连接"""
        with self._labrad_lock:
            try:
                self._setup_paths()

                import labrad
                from lqms.data_process import dataAnalysisCore as dc
                from lqms.pyle import registry_wrapper2

                # 如果已经连接，直接返回成功
                if self._data is not None:
                    return {
                        "success": True,
                        "message": "Already connected to DataLab",
                        "session_path": self._session_path,
                    }

                _log("Connecting to LabRAD...")
                self._cxn = labrad.connect()
                self._dv = self._cxn.data_vault
                _log("LabRAD connected")

                # 设置会话
                self._session_path = self._load_session_config()
                self._switch_session(self._session_path)

                return {
                    "success": True,
                    "message": "Connected to DataLab",
                    "session_path": self._session_path,
                }

            except Exception as e:
                _log(f"Connection failed: {e}")
                traceback.print_exc()
                return {
                    "success": False,
                    "error": str(e),
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
        if self._data is None:
            return {"error": "DataLab not connected"}

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
            if self._data is None:
                return {"error": "DataLab not connected", "datasets": []}

            # 获取目录内容
            datasets = self._data.get_dir_contents(path)
            return {
                "success": True,
                "path": path,
                "datasets": datasets,
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
        return {
            "status": "healthy" if has_data else "degraded",
            "service": "analysis_service",
            "datalab_connected": has_data,
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "session_path": self._session_path,
        }

    def shutdown(self):
        """关闭服务"""
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
