"""
services/quantum_service/labrad_client.py - LabRAD 客户端封装

封装 LabRAD 连接和常见操作，提供简化的接口。
"""

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# LabRAD 锁 - 用于线程安全
_labrad_lock = threading.Lock()


def _get_qmclaw_root() -> Path:
    """获取 QMClaw 根目录"""
    # 从当前文件向上查找 measure_scripts
    current = Path(__file__).parent.parent.parent  # .../services/quantum_service
    for _ in range(10):
        if (current / "measure_scripts").exists():
            return current
        parent = current.parent
        if not parent or str(parent) == str(current):
            break
        current = parent
    return Path("D:/QMClaw")


def _setup_paths():
    """设置 Python 路径"""
    root = _get_qmclaw_root()
    sq_workflow = root / "measure_scripts" / "measure_scripts" / "sq_workflow"
    measure_scripts = root / "measure_scripts" / "measure_scripts"

    for path in [str(sq_workflow), str(measure_scripts)]:
        if path not in sys.path and os.path.exists(path):
            sys.path.insert(0, path)


class LabRADClient:
    """LabRAD 客户端封装

    提供简化的 LabRAD 操作接口。
    """

    def __init__(self):
        self._cxn: Optional[Any] = None
        self._s: Optional[Any] = None
        self._sq: Optional[Any] = None
        self._data: Optional[Any] = None
        self._qter: Optional[Any] = None
        self._BasicTuner: Optional[Any] = None
        self._generate_qubit: Optional[Any] = None
        self._generate_coupler: Optional[Any] = None
        self._all_qubits: Dict[str, Any] = {}
        self._all_couplers: Dict[str, Any] = {}
        self._current_session_path: List[str] = []
        self._initialized = False
        self._init_error: Optional[str] = None

    @property
    def connected(self) -> bool:
        """检查是否已连接"""
        return self._cxn is not None and self._initialized

    @property
    def cxn(self):
        """获取 LabRAD 连接"""
        return self._cxn

    @property
    def s(self):
        """获取 Session 管理器"""
        return self._s

    @property
    def sq(self):
        """获取实验模块"""
        return self._sq

    @property
    def data(self):
        """获取 DataLab"""
        return self._data

    @property
    def qter(self):
        """获取 QubitUpdater"""
        return self._qter

    @property
    def qubits(self) -> Dict[str, Any]:
        """获取所有量子比特"""
        return self._all_qubits

    @property
    def couplers(self) -> Dict[str, Any]:
        """获取所有耦合器"""
        return self._all_couplers

    @property
    def session_path(self) -> List[str]:
        """获取当前会话路径"""
        return self._current_session_path

    @property
    def name(self) -> str:
        """获取连接名称"""
        return self._cxn.name if self._cxn else ""

    @property
    def host(self) -> str:
        """获取主机地址"""
        return self._cxn.host if self._cxn else ""

    @property
    def port(self) -> int:
        """获取端口"""
        return self._cxn.port if self._cxn else 0

    @property
    def dv(self):
        """获取 DataVault"""
        if self._cxn is None:
            print("[Quantum] dv: _cxn is None", flush=True)
            return None
        if not hasattr(self._cxn, 'data_vault'):
            print("[Quantum] dv: _cxn has no data_vault", flush=True)
            return None
        dv = self._cxn.data_vault
        print(f"[Quantum] dv: returning data_vault, type={type(dv).__name__}", flush=True)
        return dv

    @property
    def lock(self) -> threading.Lock:
        """获取线程锁"""
        return _labrad_lock

    def _setup_ray(self) -> bool:
        """初始化 Ray 连接并启动 Device Manager actor

        Returns:
            是否成功初始化
        """
        try:
            import ray

            # 如果已初始化，跳过
            if ray.is_initialized():
                print("[Quantum] Ray already initialized", flush=True)
                return True

            # 从 lqcs.system_config 获取 Ray 配置
            try:
                from lqcs import system_config
                head_ip = system_config.get_ray_head()
                node_ip = system_config.get_config()['ip']
                port = system_config.get_ray_port()
            except ImportError:
                print("[Quantum] lqcs.system_config not available, skipping Ray init", flush=True)
                return False

            print(f"[Quantum] Connecting to Ray at {head_ip}:{port}...", flush=True)
            ray.init(
                address=f"{head_ip}:{port}",
                namespace='main',
                _node_ip_address=node_ip,
                log_to_driver=False,
            )
            print("[Quantum] Ray connected successfully", flush=True)

            # 尝试获取或创建 Device Manager actor
            try:
                device_manager = ray.get_actor('Device Manager')
                print("[Quantum] Device Manager already exists", flush=True)
            except Exception:
                print("[Quantum] Starting Device Manager...", flush=True)
                try:
                    from lqcs.servers_control.start_server import start_managers
                    start_managers.startServer(
                        node_ip,
                        start_managers.DeviceManagerActor,
                        'Device Manager',
                        blocking=False
                    )
                    print("[Quantum] Device Manager started", flush=True)
                except Exception as e:
                    print(f"[Quantum] Failed to start Device Manager: {e}", flush=True)

            return True
        except Exception as e:
            print(f"[Quantum] Ray initialization failed: {e}", flush=True)
            return False

    def initialize(self, session_path: Optional[List[str]] = None, timeout: int = 60) -> bool:
        """初始化 LabRAD 连接

        Args:
            session_path: 会话路径，如 ['', 'LQHL', 'test', '20260324']
            timeout: 超时时间（秒）

        Returns:
            是否初始化成功
        """
        if self._initialized:
            return True

        _setup_paths()
        start_time = time.time()

        try:
            print("[Quantum] Importing labrad...", flush=True)
            import labrad

            print("[Quantum] Importing lqms modules...", flush=True)
            from lqms.utils.save_path import get_info_path
            from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
            from lqms.measure import generate_qubit, generate_coupler
            from lqms.measure.basic import BasicTuner, util
            from lqms.measure.tuners import sq_nodes as sq_module

            self._generate_qubit = generate_qubit
            self._generate_coupler = generate_coupler

            # 连接 LabRAD
            print("[Quantum] Connecting to LabRAD...", flush=True)
            self._cxn = labrad.connect()
            print(f"[Quantum] LabRAD connected! ID={self._cxn.ID}", flush=True)

            util.setWiringInfo(self._cxn)
            print("[Quantum] Wiring info set", flush=True)

            # 初始化 Ray（必须在实验执行前）
            self._setup_ray()

            # 加载会话配置
            if session_path is None:
                session_path = self._load_session_config()

            self._current_session_path = session_path
            user = session_path[1] if len(session_path) > 1 else "LQHL"

            # 创建会话
            print(f"[Quantum] Creating session for user={user}...", flush=True)
            from lqms.pyle.workflow import switchSession

            switch_result = {"session": None, "error": None}

            def _switch():
                try:
                    switch_result["session"] = switchSession(self._cxn, user=user)
                except Exception as e:
                    switch_result["error"] = e

            switch_thread = threading.Thread(target=_switch)
            switch_thread.daemon = True
            switch_thread.start()
            switch_thread.join(timeout=min(30, timeout - (time.time() - start_time)))

            if switch_thread.is_alive():
                raise TimeoutError("switchSession timed out")

            if switch_result["error"]:
                raise switch_result["error"]

            self._s = switch_result["session"]
            print("[Quantum] Session created", flush=True)

            # 初始化 DataLab
            print("[Quantum] Initializing DataLab...", flush=True)
            self._data = dc.DataLab(session_path, self._cxn.data_vault, dv_type="data_vault")

            # 跳过 InfoBase 加载以避免挂起
            info = None

            # 初始化 QubitUpdater
            print("[Quantum] Initializing QubitUpdater...", flush=True)
            self._qter = QubitUpdater(self._data, info)

            # 生成量子比特
            print("[Quantum] Generating qubits...", flush=True)
            self._all_qubits = generate_qubit(
                {"s": self._s}, info=info, sample=self._s
            )
            print(f"[Quantum] Generated {len(self._all_qubits)} qubits", flush=True)

            # 生成耦合器
            print("[Quantum] Generating couplers...", flush=True)
            self._all_couplers = generate_coupler(
                {"s": self._s}, info=info, sample=self._s
            )
            print(f"[Quantum] Generated {len(self._all_couplers)} couplers", flush=True)

            # 初始化 BasicTuner
            auto_config = {
                "stats": 300,
                "correctX": False,
                "correctZ": False,
                "reset": False,
                "apply_21": False,
                "run_mode": "local",
            }
            self._BasicTuner = BasicTuner(**auto_config)
            BasicTuner._sample = self._s
            BasicTuner._all_qobjs = self._all_qubits | self._all_couplers

            # 实验模块
            self._sq = sq_module

            self._initialized = True
            elapsed = time.time() - start_time
            print(f"[Quantum] Initialization completed in {elapsed:.1f}s", flush=True)
            return True

        except Exception as e:
            import traceback
            self._init_error = str(e)
            print(f"[Quantum] Initialization failed: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            return False

    def _load_session_config(self) -> List[str]:
        """加载会话配置"""
        config_path = Path(__file__).parent.parent.parent / "config" / "session.json"

        if config_path.exists():
            try:
                import json
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                session = config.get("session", {})
                user = session.get("user", "LQHL")
                path = session.get("path", ["test", "20260324"])
                return ["", user] + path
            except Exception:
                pass

        return ["", "LQHL", "test", "20260324"]

    def switch_session(self, session_path: List[str]) -> bool:
        """切换会话

        Args:
            session_path: 新的会话路径

        Returns:
            是否切换成功
        """
        with _labrad_lock:
            try:
                # 更新 DataVault 目录
                self._cxn.data_vault.cd("")
                clean_path = (
                    session_path[1:] if session_path and session_path[0] == "" else session_path
                )
                self._cxn.data_vault.cd(clean_path)

                # 切换会话
                from lqms.pyle.workflow import switchSession

                user = clean_path[0] if clean_path else "LQHL"
                self._s = switchSession(self._cxn, user=user)

                # 重新加载 DataLab
                from lqms.data_process import dataAnalysisCore as dc

                self._data = dc.DataLab(session_path, self._cxn.data_vault, dv_type="data_vault")

                # 更新 qter
                if self._qter is not None:
                    self._qter.data = self._data

                # 重新生成量子比特
                self.reload_qubits(session_path)

                self._current_session_path = session_path
                return True

            except Exception as e:
                print(f"[Quantum] Session switch failed: {e}", flush=True)
                return False

    def reload_qubits(self, session_path: Optional[List[str]] = None) -> bool:
        """重新加载量子比特"""
        if session_path is None:
            session_path = self._current_session_path

        try:
            from lqms.pyle.workflow import switchSession
            from lqms.utils.save_path import get_info_path
            from lqms.data_process import dataAnalysisCore as dc

            user = session_path[1] if len(session_path) > 1 else "LQHL"
            self._s = switchSession(self._cxn, user=user)

            # 重新加载
            info_path = get_info_path(self._s)
            info = dc.InfoBase(info_path) if os.path.exists(info_path) else None

            self._data = dc.DataLab(session_path, self._cxn.data_vault, dv_type="data_vault")

            if self._qter is not None:
                self._qter.data = self._data

            if self._generate_qubit:
                self._all_qubits = self._generate_qubit(
                    {"s": self._s}, info=info, sample=self._s
                )
                self._all_couplers = self._generate_coupler(
                    {"s": self._s}, info=info, sample=self._s
                )

                # 更新 BasicTuner
                from lqms.measure.basic import BasicTuner

                BasicTuner._sample = self._s
                BasicTuner._all_qobjs = self._all_qubits | self._all_couplers

            self._current_session_path = session_path
            return True

        except Exception as e:
            print(f"[Quantum] Qubit reload failed: {e}", flush=True)
            return False

    def get_qubits(self) -> List[Dict[str, Any]]:
        """获取量子比特列表"""
        qubits = []
        if self._s:
            for qname in sorted(self._s.keys()):
                if qname.startswith("q"):
                    try:
                        qobj = self._s[qname]
                        qubit_info: Dict[str, Any] = {"name": qname}
                        if hasattr(qobj, "regs"):
                            try:
                                qubit_info["f10"] = float(qobj.regs.f10)
                                qubit_info["fread"] = float(qobj.regs.fread)
                                qubit_info["bias_z"] = float(qobj.regs.bias_z)
                                qubit_info["f21"] = float(qobj.regs.f21)
                                qubit_info["fc"] = float(qobj.regs.fc)
                            except Exception:
                                pass
                        qubits.append(qubit_info)
                    except Exception:
                        qubits.append({"name": qname})
        return qubits

    def get_qubit(self, name: str) -> Optional[Any]:
        """获取单个量子比特"""
        if self._s and name in self._s:
            return self._s[name]
        return None

    def list_experiments(self) -> List[Dict[str, str]]:
        """列出所有实验函数"""
        experiments = []
        if self._sq:
            for name in dir(self._sq):
                if name.startswith("_") or name.startswith("qq"):
                    continue
                obj = getattr(self._sq, name)
                if not callable(obj):
                    continue
                doc = getattr(obj, "__doc__", None) or ""
                experiments.append({
                    "name": name,
                    "fullName": f"sq.{name}",
                    "doc": doc.strip().split("\n")[0][:120] if doc else "",
                })
        return sorted(experiments, key=lambda x: x["name"])

    def shutdown(self):
        """关闭连接"""
        if self._cxn:
            try:
                self._cxn.disconnect()
            except Exception:
                pass
        self._initialized = False
