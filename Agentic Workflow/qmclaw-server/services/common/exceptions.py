"""
services/common/exceptions.py - 公共异常定义

所有服务共享的异常类型。
"""


class ServiceError(Exception):
    """服务基类异常"""
    def __init__(self, message: str, code: str = "SERVICE_ERROR", details: dict = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self):
        return {
            "error": self.message,
            "code": self.code,
            "details": self.details,
        }


class ConfigError(ServiceError):
    """配置错误"""
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, "CONFIG_ERROR", details)


class APIError(ServiceError):
    """API 调用错误"""
    def __init__(self, message: str, status_code: int = 500, details: dict = None):
        super().__init__(message, "API_ERROR", details)
        self.status_code = status_code


class AuthenticationError(ServiceError):
    """认证错误"""
    def __init__(self, message: str = "Authentication failed", details: dict = None):
        super().__init__(message, "AUTH_ERROR", details)


class RateLimitError(ServiceError):
    """限流错误"""
    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60, details: dict = None):
        super().__init__(message, "RATE_LIMIT", details)
        self.retry_after = retry_after


class TimeoutError(ServiceError):
    """超时错误"""
    def __init__(self, message: str = "Request timeout", timeout: int = 60, details: dict = None):
        super().__init__(message, "TIMEOUT", details)
        self.timeout = timeout


class ModelNotFoundError(ServiceError):
    """模型未找到"""
    def __init__(self, model_name: str, details: dict = None):
        super().__init__(f"Model not found: {model_name}", "MODEL_NOT_FOUND", details)
        self.model_name = model_name


class ProviderNotAvailableError(ServiceError):
    """Provider 不可用"""
    def __init__(self, provider: str, details: dict = None):
        super().__init__(f"Provider not available: {provider}", "PROVIDER_NOT_AVAILABLE", details)
        self.provider = provider
