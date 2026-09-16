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
from typing import Any, Dict, List, Optional

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config


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
    """

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
        """启动时初始化 - 同步完成 LabRAD 连接"""
        _log("Initializing Quantum service...")

        # 延迟导入避免启动时卡住
        try:
            from .labrad_client import LabRADClient
            self._labrad = LabRADClient()
            _log("LabRADClient created")

            # 启动时同步连接 LabRAD
            _log("Connecting to LabRAD...")
            success = self._labrad.initialize()
            if success:
                _log("LabRAD connected successfully")
                self._init_started = True
            else:
                _log(f"LabRAD connection failed: {self._labrad._init_error}")
                self._init_started = False
                # 连接失败时抛出异常，让服务启动失败
                raise Exception(f"Failed to connect to LabRAD: {self._labrad._init_error}")

        except Exception as e:
            _log(f"Failed to initialize Quantum service: {e}")
            raise

    def _ensure_connected(self) -> bool:
        """确保 LabRAD 已连接"""
        if self._labrad is None:
            return False

        if self._labrad.connected:
            return True

        # 尝试连接
        _log("Connecting to LabRAD...")
        success = self._labrad.initialize()
        if success:
            _log("LabRAD connected successfully")
        else:
            _log(f"LabRAD connection failed: {self._labrad._init_error}")

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
        elif path == "/datasets":
            return self._handle_datasets(data)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        labrad_ok = self._labrad is not None and self._labrad.connected
        return {
            "status": "healthy" if labrad_ok else "degraded",
            "service": "quantum_service",
            "labrad_connected": labrad_ok,
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "running_tasks": len(self._running_tasks),
        }

    def _handle_connect(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """手动触发连接"""
        session_path = data.get("session_path")
        if session_path:
            if isinstance(session_path, str):
                session_path = json.loads(session_path)

        timeout = data.get("timeout", 60)

        with self._labrad_lock:
            if self._labrad is None:
                return {"success": False, "error": "LabRAD client not initialized"}

            success = self._labrad.initialize(session_path, timeout)

            if success:
                return {
                    "success": True,
                    "message": "Connected to LabRAD",
                    "session_path": self._labrad.session_path,
                    "qubit_count": len(self._labrad.qubits),
                }
            else:
                return {
                    "success": False,
                    "error": self._labrad._init_error or "Unknown error",
                }

    def _handle_status(self) -> Dict[str, Any]:
        """获取连接状态"""
        if self._labrad is None:
            return {
                "connected": False,
                "initialized": False,
                "session_path": None,
                "qubit_count": 0,
            }

        return {
            "connected": self._labrad.connected,
            "initialized": self._labrad._initialized,
            "session_path": self._labrad.session_path,
            "qubit_count": len(self._labrad.qubits) if self._labrad.connected else 0,
            "init_error": self._labrad._init_error,
        }

    def _handle_qubits(self) -> Dict[str, Any]:
        """获取量子比特列表"""
        if not self._ensure_connected():
            return {"error": "Not connected to LabRAD", "qubits": [], "sessionPath": []}

        with self._labrad_lock:
            qubits = self._labrad.get_qubits()
            session_path = self._labrad.session_path
            return {"qubits": qubits, "count": len(qubits), "sessionPath": session_path}

    def _handle_experiments(self) -> Dict[str, Any]:
        """获取可用实验列表"""
        if not self._ensure_connected():
            return {"error": "Not connected to LabRAD", "experiments": []}

        with self._labrad_lock:
            experiments = self._labrad.list_experiments()
            return {"experiments": experiments, "count": len(experiments)}

    def _handle_execute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """执行实验代码

        带 busy 锁，防止并发执行。
        如果正在执行，返回 busy 状态（降级服务）。
        """
        if not self._ensure_connected():
            return {"error": "Not connected to LabRAD"}

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
        if not self._ensure_connected():
            return {"error": "Not connected to LabRAD", "sessions": []}

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

        if not self._ensure_connected():
            _log("session_tree: LabRAD not connected")
            return {"error": "Not connected to LabRAD", "tree": []}

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

    def _handle_datasets(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """获取数据集列表"""
        path = data.get("path")

        _log(f"datasets: path={path}, type={type(path)}")

        if not self._ensure_connected():
            return {"error": "Not connected to LabRAD", "datasets": [], "groups": []}

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
                }
            except Exception as e:
                _log(f"datasets: error = {e}")
                return {"error": str(e), "datasets": [], "groups": [], "path": ""}

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        labrad_ok = self._labrad is not None and self._labrad.connected
        return {
            "status": "healthy" if labrad_ok else "degraded",
            "service": "quantum_service",
            "labrad_connected": labrad_ok,
            "init_started": self._init_started,
            "ready": not self._busy.is_set(),
            "busy": self._busy.is_set(),
            "current_task": self._current_task_id,
            "running_tasks": len(self._running_tasks),
            "session_path": self._labrad.session_path if self._labrad else None,
        }

    def shutdown(self):
        """关闭服务"""
        if self._labrad:
            self._labrad.shutdown()
        super().shutdown()


def main():
    """主入口"""
    service = QuantumService(port=3003)
    run_service(service)


if __name__ == "__main__":
    main()
