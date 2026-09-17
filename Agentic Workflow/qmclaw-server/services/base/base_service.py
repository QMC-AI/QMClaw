"""
services/base/base_service.py - 服务基类

所有 Python 服务都应继承此类，以获得统一的服务生命周期管理、
健康检查、请求处理等功能。
"""

import json
import os
import sys
import time
import signal
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


def _safe_print(msg: str, flush: bool = True):
    """安全打印 - 使用 UTF-8 编码"""
    try:
        sys.stderr.buffer.write((msg + "\n").encode("utf-8"))
        if flush:
            sys.stderr.buffer.flush()
    except AttributeError:
        print(msg, flush=flush)


class ServiceStatus(Enum):
    """服务状态"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class ServiceConfig:
    """服务配置"""
    name: str
    host: str = "localhost"
    port: int = 3000
    health_check_interval: int = 30  # 秒
    request_timeout: int = 60  # 秒
    max_workers: int = 4


class BaseHTTPRequestHandler(BaseHTTPRequestHandler):
    """通用 HTTP 请求处理器"""

    def __init__(self, *args, service: 'BaseService', **kwargs):
        self.service = service
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """统一日志格式"""
        _safe_print(f"[{self.service.config.name}] {args[0]}")

    def do_GET(self):
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 健康检查
        if path == "/health":
            self.send_health_response()
            return

        # 主路由
        try:
            result = self.service.handle_request("GET", path, {}, query)
            self.send_json_response(200, result)
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})

    def do_POST(self):
        """处理 POST 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_json_response(400, {"error": "Invalid JSON"})
            return

        try:
            result = self.service.handle_request("POST", path, data, {})
            self.send_json_response(200, result)
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})

    def do_DELETE(self):
        """处理 DELETE 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        # 读取请求体（DELETE 通常也可能有 body）
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        try:
            result = self.service.handle_request("DELETE", path, data, {})
            self.send_json_response(200, result)
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})

    def send_json_response(self, status_code: int, data: Dict[str, Any]):
        """发送 JSON 响应"""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def send_health_response(self):
        """发送健康检查响应"""
        health = self.service.get_health()
        status_code = 200 if health["status"] == "healthy" else 503
        self.send_json_response(status_code, health)


class BaseService(ABC):
    """服务基类

    使用方法:
    1. 继承此类
    2. 实现 handle_request 方法
    3. 在 main 中创建实例并调用 run()
    """

    def __init__(self, config: ServiceConfig):
        self.config = config
        self.status = ServiceStatus.STOPPED
        self.start_time: Optional[float] = None
        self.request_count = 0
        self.error_count = 0
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False

        # 注册信号处理器
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """处理终止信号"""
        _safe_print(f"[{self.config.name}] Received signal {signum}, shutting down...")
        self.stop()

    @abstractmethod
    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理请求 - 子类必须实现

        Args:
            method: HTTP 方法 (GET, POST, etc.)
            path: 请求路径
            data: 请求体数据 (POST)
            query: 查询参数 (GET)

        Returns:
            响应数据
        """
        pass

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态 - 子类可覆盖"""
        uptime = time.time() - self.start_time if self.start_time else 0
        return {
            "status": "healthy" if self.status == ServiceStatus.RUNNING else "unhealthy",
            "service": self.config.name,
            "uptime": uptime,
            "request_count": self.request_count,
            "error_count": self.error_count,
        }

    def before_start(self):
        """启动前钩子 - 子类可覆盖"""
        _safe_print(f"[{self.config.name}] Starting...")

    def after_start(self):
        """启动后钩子 - 子类可覆盖"""
        _safe_print(f"[{self.config.name}] Started on {self.config.host}:{self.config.port}")

    def before_stop(self):
        """停止前钩子 - 子类可覆盖"""
        _safe_print(f"[{self.config.name}] Stopping...")

    def after_stop(self):
        """停止后钩子 - 子类可覆盖"""
        _safe_print(f"[{self.config.name}] Stopped")

    def run(self):
        """运行服务"""
        self.status = ServiceStatus.STARTING
        self._running = True

        try:
            self.before_start()

            # 创建 HTTP 服务器
            handler = lambda *args, **kwargs: BaseHTTPRequestHandler(*args, service=self, **kwargs)
            self._server = HTTPServer((self.config.host, self.config.port), handler)
            self._server.timeout = 1  # 1秒超时用于优雅关闭

            self.start_time = time.time()
            self.status = ServiceStatus.RUNNING
            self.after_start()

            # 主事件循环
            while self._running:
                try:
                    self._server.handle_request()
                    self.request_count += 1
                except Exception as e:
                    # 超时和其他异常处理
                    if "timeout" not in str(e).lower():
                        _safe_print(f"[{self.config.name}] Error: {e}")

        except Exception as e:
            self.status = ServiceStatus.ERROR
            _safe_print(f"[{self.config.name}] Error: {e}")
            raise
        finally:
            self._cleanup()

    def stop(self):
        """停止服务"""
        if not self._running:
            return

        self.status = ServiceStatus.STOPPING
        self._running = False
        self.before_stop()
        self._cleanup()
        self.after_stop()

    def _cleanup(self):
        """清理资源"""
        if self._server:
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None


def run_service(service: BaseService):
    """运行服务的便捷函数"""
    try:
        service.run()
    except KeyboardInterrupt:
        _safe_print(f"[{service.config.name}] Interrupted")
    except Exception as e:
        _safe_print(f"[{service.config.name}] Fatal error: {e}")
        sys.exit(1)
