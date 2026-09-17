"""
services/llm_service/ - LLM 推理服务

提供统一的 LLM 调用接口，支持多个 Provider。
"""

from .server import LLMService

__all__ = ["LLMService"]
