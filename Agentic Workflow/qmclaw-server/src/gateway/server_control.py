"""
QmClaw Server Control Module
集成 LabRAD 服务管理到 QmClaw

功能：
1. 检查 LabRAD 服务器状态
2. 启动/停止各个服务
3. 获取设备状态信息

Usage:
    from server_control import ServerController
    controller = ServerController()
    controller.start_all()
"""

import os
import sys
import time
import socket
import subprocess
import threading
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass
from enum import Enum

# Paths - can be overridden via environment variables
import os as _os
PYTHON = _os.environ.get("PYTHON_BIN", "python")
# LABRAD_BAT path is auto-detected by lqcs package, set explicitly if needed
LABRAD_BAT = _os.environ.get("LABRAD_BAT", "")

@dataclass
class ServiceInfo:
    name: str
    port: int
    status: str  # "stopped", "running", "error"
    process: Optional[int] = None
    message: str = ""

class ServiceStatus(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"

class ServerController:
    """测控服务器控制器"""

    # 服务端口映射
    SERVICES = {
        "labrad": {"port": 7682, "desc": "LabRAD 主服务器"},
        "datavault": {"port": None, "desc": "数据存储服务"},
        "grapher": {"port": None, "desc": "绘图服务"},
        "ray": {"port": None, "desc": "Ray 分布式框架"},
        "device_manager": {"port": None, "desc": "板卡服务"},
        "uwave_manager": {"port": None, "desc": "微波源服务"},
    }

    def __init__(self):
        self._status_callback: Optional[Callable] = None
        self._services: Dict[str, ServiceStatus] = {
            name: ServiceStatus.STOPPED for name in self.SERVICES
        }
        self._processes: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def set_status_callback(self, callback: Callable[[str, ServiceStatus, str], None]):
        """设置状态回调函数"""
        self._status_callback = callback

    def _update_status(self, service: str, status: ServiceStatus, message: str = ""):
        """更新服务状态"""
        with self._lock:
            self._services[service] = status
        if self._status_callback:
            self._status_callback(service, status, message)

    def check_port(self, port: int) -> tuple[bool, Optional[int]]:
        """检查端口是否被占用"""
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            for line in result.stdout.split("\n"):
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        return True, int(parts[-1])
            return False, None
        except Exception as e:
            return False, None

    def get_service_status(self, service: str) -> ServiceInfo:
        """获取单个服务状态"""
        info = self.SERVICES.get(service, {})
        port = info.get("port")

        if port:
            occupied, pid = self.check_port(port)
            status = "running" if occupied else "stopped"
            return ServiceInfo(
                name=service,
                port=port,
                status=status,
                process=pid,
                message=f"PID {pid}" if pid else ""
            )
        else:
            # 服务没有固定端口，通过进程检查
            return ServiceInfo(
                name=service,
                port=0,
                status=self._services.get(service, ServiceStatus.STOPPED).value,
                message=""
            )

    def get_all_status(self) -> Dict[str, ServiceInfo]:
        """获取所有服务状态"""
        return {name: self.get_service_status(name) for name in self.SERVICES}

    def is_labrad_running(self) -> bool:
        """检查 LabRAD 服务器是否运行"""
        occupied, _ = self.check_port(7682)
        return occupied

    def check_ray(self) -> bool:
        """检查 Ray 是否初始化"""
        try:
            import ray
            return ray.is_initialized()
        except:
            return False

    def check_labrad_connection(self) -> bool:
        """检查是否能连接到 LabRAD"""
        try:
            import labrad
            cxn = labrad.connect()
            connected = cxn.connected
            cxn.disconnect()
            return connected
        except Exception as e:
            print(f"LabRAD connection check failed: {e}")
            return False

    def start_labrad_server(self, timeout: int = 30) -> bool:
        """启动 LabRAD Java 服务器"""
        if self.is_labrad_running():
            print("LabRAD server already running")
            return True

        self._update_status("labrad", ServiceStatus.STARTING, "Starting LabRAD server...")

        try:
            # 使用 subprocess 启动 LabRAD 服务器
            # 注意：这里需要 Java 环境
            labrad_dir = os.path.dirname(LABRAD_BAT)

            # 启动 LabRAD 服务器（后台运行）
            process = subprocess.Popen(
                [LABRAD_BAT],
                cwd=labrad_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            self._processes["labrad"] = process

            # 等待服务器启动
            start_time = time.time()
            while time.time() - start_time < timeout:
                if self.is_labrad_running():
                    self._update_status("labrad", ServiceStatus.RUNNING, "LabRAD server started")
                    return True
                time.sleep(0.5)

            self._update_status("labrad", ServiceStatus.ERROR, "LabRAD server start timeout")
            return False

        except Exception as e:
            self._update_status("labrad", ServiceStatus.ERROR, str(e))
            return False

    def start_ray_node(self, blocking: bool = False) -> bool:
        """启动 Ray 节点"""
        self._update_status("ray", ServiceStatus.STARTING, "Starting Ray node...")

        try:
            from lqcs import system_config
            from lqcs.servers_control.start_server import ray_func

            head_ip = system_config.get_ray_head()
            node_ip = system_config.get_config()['ip']
            port = system_config.get_ray_port()

            import ray

            if not ray.is_initialized():
                ray.init(
                    address=f"{head_ip}:{port}",
                    namespace='main',
                    _node_ip_address=node_ip,
                    log_to_driver=False,
                )

            self._update_status("ray", ServiceStatus.RUNNING, "Ray node connected")
            return True

        except Exception as e:
            self._update_status("ray", ServiceStatus.ERROR, str(e))
            return False

    def start_device_manager(self) -> bool:
        """启动设备管理器"""
        self._update_status("device_manager", ServiceStatus.STARTING, "Starting device manager...")

        try:
            from lqcs.servers_control.start_server import start_managers
            from lqcs import system_config

            node_ip = system_config.get_config()['ip']

            # 使用线程启动，避免阻塞
            def run_manager():
                try:
                    start_managers.startServer(
                        node_ip,
                        start_managers.DeviceManagerActor,
                        'Device Manager',
                        blocking=False
                    )
                except Exception as e:
                    print(f"Device manager error: {e}")

            thread = threading.Thread(target=run_manager, daemon=True)
            thread.start()

            self._update_status("device_manager", ServiceStatus.RUNNING, "Device manager started")
            return True

        except Exception as e:
            self._update_status("device_manager", ServiceStatus.ERROR, str(e))
            return False

    def start_uwave_manager(self) -> bool:
        """启动微波源管理器"""
        self._update_status("uwave_manager", ServiceStatus.STARTING, "Starting uwave manager...")

        try:
            from lqcs.servers_control.start_server import start_uwave_server
            from lqcs import system_config

            node_ip = system_config.get_config()['ip']

            def run_manager():
                try:
                    start_uwave_server.startServer(
                        node_ip,
                        start_uwave_server.UwaveServerActor,
                        'Uwave Manager',
                        blocking=False
                    )
                except Exception as e:
                    print(f"Uwave manager error: {e}")

            thread = threading.Thread(target=run_manager, daemon=True)
            thread.start()

            self._update_status("uwave_manager", ServiceStatus.RUNNING, "Uwave manager started")
            return True

        except Exception as e:
            self._update_status("uwave_manager", ServiceStatus.ERROR, str(e))
            return False

    def start_data_store(self) -> bool:
        """启动数据存储服务"""
        self._update_status("datavault", ServiceStatus.STARTING, "Starting data store...")

        try:
            from lqcs.servers_control.start_server import start_data_store
            from lqcs import system_config

            node_ip = system_config.get_config()['ip']

            def run_service():
                try:
                    start_data_store.startServer(
                        node_ip,
                        start_data_store.DataStoreActor,
                        'Data Store',
                        blocking=False
                    )
                except Exception as e:
                    print(f"Data store error: {e}")

            thread = threading.Thread(target=run_service, daemon=True)
            thread.start()

            self._update_status("datavault", ServiceStatus.RUNNING, "Data store started")
            return True

        except Exception as e:
            self._update_status("datavault", ServiceStatus.ERROR, str(e))
            return False

    def start_grapher(self) -> bool:
        """启动绘图服务"""
        # Grapher 通常是 LabRAD 的一部分
        self._update_status("grapher", ServiceStatus.RUNNING, "Grapher integrated with LabRAD")
        return True

    def start_all(self, progress_callback: Optional[Callable] = None) -> Dict[str, bool]:
        """
        启动所有服务

        Args:
            progress_callback: 进度回调函数，签名 (service: str, status: str, message: str)

        Returns:
            每个服务的启动结果
        """
        results = {}

        # 1. 检查 LabRAD
        if progress_callback:
            progress_callback("labrad", "checking", "Checking LabRAD server...")

        if not self.is_labrad_running():
            if progress_callback:
                progress_callback("labrad", "starting", "Starting LabRAD server...")
            results["labrad"] = self.start_labrad_server()
        else:
            if progress_callback:
                progress_callback("labrad", "running", "LabRAD already running")
            results["labrad"] = True

        # 2. 启动 Ray
        if progress_callback:
            progress_callback("ray", "starting", "Starting Ray node...")

        if not self.check_ray():
            results["ray"] = self.start_ray_node()
        else:
            if progress_callback:
                progress_callback("ray", "running", "Ray already initialized")
            results["ray"] = True

        # 3. 启动其他服务
        for service, start_fn in [
            ("datavault", self.start_data_store),
            ("device_manager", self.start_device_manager),
            ("uwave_manager", self.start_uwave_manager),
            ("grapher", self.start_grapher),
        ]:
            if progress_callback:
                progress_callback(service, "starting", f"Starting {service}...")
            results[service] = start_fn()

        return results

    def stop_all(self):
        """停止所有服务"""
        # 停止 Ray
        try:
            import ray
            if ray.is_initialized():
                ray.shutdown()
        except:
            pass

        # 停止子进程
        for name, process in self._processes.items():
            try:
                process.terminate()
                process.wait(timeout=5)
            except:
                process.kill()

        self._processes.clear()

        for name in self.SERVICES:
            self._update_status(name, ServiceStatus.STOPPED)

    def get_system_status(self) -> dict:
        """获取系统综合状态"""
        return {
            "labrad": {
                "running": self.is_labrad_running(),
                "connected": self.check_labrad_connection(),
            },
            "ray": {
                "initialized": self.check_ray(),
            },
            "services": self.get_all_status(),
        }


# 单例实例
_controller: Optional[ServerController] = None

def get_controller() -> ServerController:
    """获取服务器控制器单例"""
    global _controller
    if _controller is None:
        _controller = ServerController()
    return _controller


if __name__ == "__main__":
    # 测试代码
    controller = ServerController()

    print("QmClaw Server Controller Test")
    print("=" * 50)

    # 检查状态
    status = controller.get_system_status()
    print(f"LabRAD Running: {status['labrad']['running']}")
    print(f"LabRAD Connected: {status['labrad']['connected']}")
    print(f"Ray Initialized: {status['ray']['initialized']}")

    print()
    print("Services:")
    for name, info in controller.get_all_status().items():
        print(f"  {name}: {info.status}")
