"""
services/base/base_service.py - 服务基类

所有 Python 服务都应继承此类，以获得统一的服务生命周期管理、
健康检查、请求处理等功能。
"""

import atexit
import json
import os
import queue
import sys
import time
import signal
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 请求处理耗时超过该秒数时打印 SLOW 日志（可用 service.slow_request_seconds 覆盖）
SLOW_REQUEST_SECONDS = 5.0


# 异步日志：请求路径上的日志绝不能阻塞。终端窗口/管道的缓冲区一旦被写满，
# write() 会**永久阻塞**（没有任何可用的超时机制），而 log_message() 在
# send_response() 内部被调用 —— 于是整个服务连 /health 都不再应答，端口
# 仍然 LISTENING、客户端看到的是 TCP 连上后就一直等到超时。2026-11-03 的
# 三次"quantum_service 假死"就是这个形态（3 条 CLOSE_WAIT、CPU 不涨）。
# 现在改为：日志进有界队列，单独线程消费；队列满就丢日志并计数。
_LOG_QUEUE: "queue.Queue[str]" = queue.Queue(maxsize=20000)
_LOG_STATE = {"writer": None, "drops": 0, "blocked": False}
_LOG_GUARD = threading.Lock()


def _log_writer() -> None:
    """日志消费线程：唯一允许写 stderr 的地方。它被卡住也只丢日志，不影响请求。"""
    stream = None
    while True:
        msg = _LOG_QUEUE.get()
        if msg is None:
            return
        try:
            if stream is None:
                stream = getattr(sys.stderr, "buffer", None)
            if stream is not None:
                stream.write((msg + "\n").encode("utf-8"))
                stream.flush()
            else:
                print(msg, flush=True)
        except (OSError, ValueError, AttributeError):
            # 管道断开 / 被替换成普通对象 —— 换下一条时重新探测
            stream = None
        except Exception:
            stream = None


def _ensure_log_writer() -> None:
    if _LOG_STATE["writer"] is not None:
        return
    with _LOG_GUARD:
        if _LOG_STATE["writer"] is not None:
            return
        thread = threading.Thread(target=_log_writer, name="qm-log-writer", daemon=True)
        thread.start()
        _LOG_STATE["writer"] = thread


def _stop_log_writer(timeout: float = 3.0) -> None:
    """进程退出前让日志线程收工（塞一个哨兵 + 短暂 join）。

    不显式收工的话，daemon 线程会在解释器 finalize 时仍握着 stderr 的缓冲锁，
    CPython 直接抛 `Fatal Python error: _enter_buffered_busy: could not acquire
    lock for <_io.BufferedWriter name='<stderr>'> at interpreter shutdown`。
    长跑的服务进程一般不会走到这里，但**短命脚本**（自测、CLI 探针）每次退出都会
    撞上这个致命错误，把正常结果盖掉。
    """
    thread = _LOG_STATE.get("writer")
    if thread is None or not thread.is_alive():
        return
    try:
        _LOG_QUEUE.put_nowait(None)   # 哨兵：_log_writer 见到 None 就 return
    except queue.Full:
        return
    thread.join(timeout)


atexit.register(_stop_log_writer)


def log_stats() -> Dict[str, Any]:
    """运行时自检状态，供 /health 暴露（日志通道 + 在途请求）。"""
    return {
        "log_queue": _LOG_QUEUE.qsize(),
        "log_dropped": _LOG_STATE["drops"],
        "log_writer_alive": bool(_LOG_STATE["writer"] and _LOG_STATE["writer"].is_alive()),
        "inflight": inflight_snapshot(),
    }


def _safe_print(msg: str, flush: bool = True):
    """安全打印 - **绝不抛异常、也绝不阻塞**。

    日志写入有界队列由后台线程消费。终端缓冲区写满时，只有消费线程会卡住，
    队列满了之后的日志直接丢弃（计数见 ``log_stats()``），请求路径照常返回。
    """
    _ensure_log_writer()
    try:
        _LOG_QUEUE.put_nowait(msg)
    except queue.Full:
        _LOG_STATE["drops"] += 1


# ---------------------------------------------------------------- 请求看门狗
# 假死故障（端口 LISTENING、所有接口超时、CLOSE_WAIT 堆积）只能靠"卡住时
# 把栈落盘"来定位：服务已经不应答了，任何接口都取不到信息，终端也可能是
# 写不进去的那一头。所以这里在进程内起一个独立线程，定期检查在途请求，
# 超过阈值就把**全部线程栈**写到 data/debug/ 下的文件里。
_INFLIGHT: Dict[str, float] = {}
_INFLIGHT_GUARD = threading.Lock()
_WATCHDOG_STARTED = {"flag": False}


def _inflight_begin(key: str) -> None:
    with _INFLIGHT_GUARD:
        _INFLIGHT[key] = time.time()


def _inflight_end(key: str) -> None:
    with _INFLIGHT_GUARD:
        _INFLIGHT.pop(key, None)


def inflight_snapshot() -> Dict[str, float]:
    """在途请求 {key: 已耗时秒}，供 /health 暴露。"""
    now = time.time()
    with _INFLIGHT_GUARD:
        return {k: round(now - t, 1) for k, t in _INFLIGHT.items()}


def _dump_all_stacks(reason: str) -> str:
    """把所有线程栈写入 data/debug/ 并返回文本（自身不抛异常）。"""
    import io as _io
    import traceback as _tb

    buf = _io.StringIO()
    buf.write(reason + "\n")
    with _INFLIGHT_GUARD:
        for key, started in _INFLIGHT.items():
            buf.write(f"inflight {key} for {time.time() - started:.1f}s\n")
    for tid, frame in sys._current_frames().items():
        buf.write(f"--- thread {tid} ---\n")
        _tb.print_stack(frame, file=buf)
    text = buf.getvalue()
    try:
        dump_dir = Path(__file__).resolve().parent.parent.parent / "data" / "debug"
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_path = dump_dir / f"slow_request_{int(time.time())}.txt"
        dump_path.write_text(text, encoding="utf-8", errors="replace")
        text += f"[file] {dump_path}\n"
    except Exception:
        pass
    return text


def start_request_watchdog(service_name: str) -> None:
    """启动请求看门狗（幂等）。阈值用 QMCLAW_REQ_WATCHDOG，默认 90 秒。"""
    if _WATCHDOG_STARTED["flag"]:
        return
    _WATCHDOG_STARTED["flag"] = True
    threshold = float(os.environ.get("QMCLAW_REQ_WATCHDOG", "90"))

    def _loop() -> None:
        while True:
            time.sleep(15)
            now = time.time()
            with _INFLIGHT_GUARD:
                stuck = [k for k, started in _INFLIGHT.items() if now - started > threshold]
            if not stuck:
                continue
            _safe_print(_dump_all_stacks(
                f"[{service_name}][WATCHDOG] request(s) in flight over "
                f"{threshold:.0f}s: {stuck} — dumping all thread stacks"
            ))
            with _INFLIGHT_GUARD:
                for key in stuck:
                    _INFLIGHT.pop(key, None)

    threading.Thread(target=_loop, name="qm-req-watchdog", daemon=True).start()


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


def _timed_request(method: str):
    """包装 do_GET/do_POST/do_DELETE：统计请求数，超阈值打印 SLOW 日志。

    服务器是多线程的，每个连接跑在自己的线程里，所以耗时统计和计数
    只有放在请求线程内才准确。
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            started = time.time()
            try:
                return func(self, *args, **kwargs)
            finally:
                elapsed = time.time() - started
                self.service.request_count += 1
                if elapsed > self.service.slow_request_seconds:
                    _safe_print(
                        f"[{self.service.config.name}] SLOW {method} {self.path} {elapsed:.3f}s"
                    )
        return wrapper
    return decorator


class BaseHTTPRequestHandler(BaseHTTPRequestHandler):
    """通用 HTTP 请求处理器"""

    def __init__(self, *args, service: 'BaseService', **kwargs):
        self.service = service
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """统一日志格式"""
        _safe_print(f"[{self.service.config.name}] {args[0]}")

    @_timed_request("GET")
    def do_GET(self):
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 入站 traceId：Express 代理透传 X-Trace-Id，注入 query 供服务按 trace 过滤
        trace_id = self._inbound_trace()
        if trace_id and "trace_id" not in query:
            query["trace_id"] = [trace_id]

        # 健康检查
        if path == "/health":
            self.send_health_response()
            return

        # 主路由
        _key = f"GET {path}"
        _inflight_begin(_key)
        try:
            result = self.service.handle_request("GET", path, {}, query)
            self.send_json_response(200, result)
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})
        finally:
            _inflight_end(_key)

    @_timed_request("POST")
    def do_POST(self):
        """处理 POST 请求"""
        parsed = urlparse(self.path)
        path = parsed.path

        # 检查是否需要 SSE 流式响应
        if path == "/chat/stream" and self.service.support_streaming():
            self.handle_streaming_request(path)
            return

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_json_response(400, {"error": "Invalid JSON"})
            return

        # 入站 traceId：注入 body，服务层用 services/common/trace.ensure_trace 取同一 trace
        trace_id = self._inbound_trace()
        if trace_id and isinstance(data, dict) \
                and not data.get("trace_id") and not data.get("traceId"):
            data["trace_id"] = trace_id

        _key = f"POST {path}"
        _inflight_begin(_key)
        try:
            result = self.service.handle_request("POST", path, data, {})
            self.send_json_response(200, result)
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})
        finally:
            _inflight_end(_key)

    def handle_streaming_request(self, path: str):
        """处理 SSE 流式请求"""
        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self.send_json_response(400, {"error": "Invalid JSON"})
            return

        # 发送 SSE 头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # 定义写入函数
        def write_fn(sse_data: str):
            try:
                self.wfile.write(sse_data.encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

        # 调用流式处理
        try:
            self.service.handle_stream_request(path, data, write_fn)
        except Exception as e:
            write_fn(f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n")

    @_timed_request("DELETE")
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

        _key = f"DELETE {path}"
        _inflight_begin(_key)
        try:
            result = self.service.handle_request("DELETE", path, data, {})
            self.send_json_response(200, result)
        except Exception as e:
            self.send_json_response(500, {"error": str(e)})
        finally:
            _inflight_end(_key)

    def _inbound_trace(self) -> str:
        """取入站 X-Trace-Id 并做安全清洗（只保留字母数字与 -_.:，最长 64）"""
        raw = (self.headers.get("X-Trace-Id") or "").strip()
        if not raw:
            return ""
        allowed = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_.:"
        safe = "".join(ch for ch in raw if ch in allowed)
        return safe[:64]

    def send_json_response(self, status_code: int, data: Dict[str, Any]):
        """发送 JSON 响应"""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        trace_id = self._inbound_trace()
        if trace_id:
            self.send_header("X-Trace-Id", trace_id)
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def send_health_response(self):
        """发送健康检查响应

        HTTP 码由 ``services/common/availability.py::health_status_code`` 决定：
        优先看新字段 ``availability.state``，老服务没有它时退回历史 ``status``。
        """
        health = self.service.get_health()
        try:
            from ..common.availability import health_status_code

            status_code = health_status_code(health)
        except Exception:  # noqa: BLE001 - 老服务/离线包缺模块时不影响健康检查
            status_code = 200 if health.get("status") == "healthy" else 503
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
        # 单请求耗时超过该秒数时打 SLOW 日志；子类可调小/调大
        self.slow_request_seconds = SLOW_REQUEST_SECONDS
        self._server: Optional[ThreadingHTTPServer] = None
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

    def support_streaming(self) -> bool:
        """是否支持 SSE 流式响应 - 子类可覆盖"""
        return False

    def handle_stream_request(self, path: str, data: Dict[str, Any], write_fn: Callable[[str], None]):
        """处理 SSE 流式请求 - 子类可覆盖

        Args:
            path: 请求路径
            data: 请求体数据
            write_fn: SSE 写入回调函数
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

            # 创建 HTTP 服务器：ThreadingHTTPServer 每个连接一个线程，
            # 单个慢请求（LLM 调用 / LabRAD 往返）不再堵死整个服务。
            handler = lambda *args, **kwargs: BaseHTTPRequestHandler(*args, service=self, **kwargs)
            self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler)
            self._server.timeout = 1  # 1秒超时用于优雅关闭

            self.start_time = time.time()
            self.status = ServiceStatus.RUNNING
            self.after_start()
            start_request_watchdog(self.config.name)

            # 主事件循环：handle_request() 收到连接后立刻交给工作线程返回，
            # 请求计数在请求线程内完成（见 _timed_request）。
            while self._running:
                try:
                    self._server.handle_request()
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
