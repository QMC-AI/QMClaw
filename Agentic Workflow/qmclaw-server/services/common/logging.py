"""
services/common/logging.py - 统一日志模块

所有服务使用统一的日志格式，便于聚合和分析。
"""

import logging
import sys
import threading
from datetime import datetime
from typing import Optional


class ServiceFormatter(logging.Formatter):
    """服务日志格式化器"""

    def __init__(self, service_name: str):
        super().__init__(
            fmt="%(asctime)s [%(levelname)s] [%(service)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        self.service_name = service_name

    def format(self, record):
        record.service = self.service_name
        return super().format(record)


def setup_logging(service_name: str, level: int = logging.INFO) -> logging.Logger:
    """设置日志

    Args:
        service_name: 服务名称
        level: 日志级别

    Returns:
        配置好的 logger
    """
    logger = logging.getLogger(service_name)
    logger.setLevel(level)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    # Console handler - 使用 UTF-8 编码避免 Windows 中文问题
    handler = logging.StreamHandler(sys.stderr)
    try:
        handler.stream = sys.stderr.buffer  # 使用 buffer 强制 UTF-8
    except AttributeError:
        pass  # Unix 系统不需要
    handler.setLevel(level)
    handler.setFormatter(ServiceFormatter(service_name))
    logger.addHandler(handler)

    return logger


class LogContext:
    """日志上下文管理器"""

    _local = threading.local()

    def __init__(self, **kwargs):
        self.context = kwargs

    def __enter__(self):
        if not hasattr(self._local, "context"):
            self._local.context = {}
        self._local.context.update(self.context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for key in self.context:
            self._local.context.pop(key, None)

    @classmethod
    def get_context(cls) -> dict:
        """获取当前上下文"""
        return getattr(cls._local, "context", {})


def log_request(logger: logging.Logger, method: str, path: str, **kwargs):
    """记录请求日志"""
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"{method} {path} {extra}".strip())


def log_response(logger: logging.Logger, method: str, path: str, status: int, duration: float, **kwargs):
    """记录响应日志"""
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"{method} {path} {status} {duration:.3f}s {extra}".strip())
