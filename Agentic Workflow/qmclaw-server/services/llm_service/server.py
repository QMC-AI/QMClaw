"""
services/llm_service/server.py - LLM 服务入口

提供统一的 LLM 调用接口，支持多个 Provider。
"""

import json
import time
import threading
import sys
from typing import Any, Dict, List, Optional

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[llm_service] {msg}")


class LLMService(BaseService):
    """LLM 推理服务

    支持的 Provider:
    - minimax: MiniMax API
    - openai: OpenAI API
    - deepseek: DeepSeek API
    """

    def __init__(self, port: int = 3006):
        cfg = ServiceConfig(
            name="llm_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # Provider 客户端
        self._clients: Dict[str, Any] = {}
        self._request_lock = threading.Lock()

        # Token 统计
        self._total_tokens = 0
        self._request_count = 0

    def before_start(self):
        """启动前初始化"""
        _log("Initializing LLM service...")
        self._init_providers()

    def _init_providers(self):
        """初始化 Provider 客户端"""
        # MiniMax
        if config.get("minimax_api_key"):
            _log("MiniMax provider available")
        else:
            _log("WARNING: MiniMax API key not configured")

        # OpenAI
        if config.get("openai_api_key"):
            _log("OpenAI provider available")
        else:
            _log("WARNING: OpenAI API key not configured")

        # DeepSeek
        if config.get("deepseek_api_key"):
            _log("DeepSeek provider available")
        else:
            _log("WARNING: DeepSeek API key not configured")

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理 LLM 请求"""
        start_time = time.time()

        # 解析路径
        if path == "/chat":
            return self._handle_chat(data, start_time)
        elif path == "/models":
            return self._handle_models()
        elif path == "/stats":
            return self._handle_stats()
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_chat(self, data: Dict[str, Any], start_time: float) -> Dict[str, Any]:
        """处理聊天请求"""
        messages = data.get("messages", [])
        model = data.get("model", "minimax")
        temperature = data.get("temperature", 0.7)
        max_tokens = data.get("max_tokens", 4096)

        if not messages:
            return {"error": "messages is required"}

        _log(f"Chat request: model={model}, messages={len(messages)}")

        try:
            # 根据 model 调用不同的 provider
            if model.startswith("minimax") or model == "minimax":
                result = self._call_minimax(messages, temperature, max_tokens)
            elif model.startswith("gpt") or model.startswith("openai"):
                result = self._call_openai(messages, model, temperature, max_tokens)
            elif model.startswith("deepseek"):
                result = self._call_deepseek(messages, temperature, max_tokens)
            else:
                # 默认使用 MiniMax
                result = self._call_minimax(messages, temperature, max_tokens)

            # 更新统计
            with self._request_lock:
                self._request_count += 1
                if "usage" in result:
                    self._total_tokens += result["usage"].get("total_tokens", 0)

            duration = time.time() - start_time
            _log(f"Chat completed: duration={duration:.2f}s, tokens={result.get('usage', {}).get('total_tokens', 0)}")

            return result

        except Exception as e:
            _log(f"Chat error: {e}")
            return {"error": str(e)}

    def _handle_models(self) -> Dict[str, Any]:
        """获取可用模型列表"""
        models = []

        if config.get("minimax_api_key"):
            models.append({
                "id": "minimax",
                "name": "MiniMax",
                "provider": "minimax",
                "max_tokens": 4096,
            })

        if config.get("openai_api_key"):
            models.extend([
                {"id": "gpt-4o", "name": "GPT-4o", "provider": "openai", "max_tokens": 128000},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai", "max_tokens": 128000},
                {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "provider": "openai", "max_tokens": 128000},
            ])

        if config.get("deepseek_api_key"):
            models.append({
                "id": "deepseek-chat",
                "name": "DeepSeek Chat",
                "provider": "deepseek",
                "max_tokens": 16384,
            })

        return {"models": models}

    def _handle_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._request_lock:
            return {
                "total_requests": self._request_count,
                "total_tokens": self._total_tokens,
            }

    def _call_minimax(self, messages: List[Dict], temperature: float, max_tokens: int) -> Dict[str, Any]:
        """调用 MiniMax API"""
        import urllib.request
        import urllib.error

        api_key = config.get("minimax_api_key")
        group_id = config.get("minimax_group_id")

        if not api_key:
            raise ValueError("MiniMax API key not configured")

        url = "https://api.minimax.chat/v1/text/chatcompletion_v2"

        payload = {
            "model": "MiniMax-Text-01",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "GroupId": group_id,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._parse_minimax_response(result)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise Exception(f"MiniMax API error {e.code}: {error_body}")

    def _parse_minimax_response(self, result: Dict) -> Dict[str, Any]:
        """解析 MiniMax 响应"""
        choices = result.get("choices", [])
        if not choices:
            return {"error": "No choices in response", "raw": result}

        choice = choices[0]
        message = choice.get("message", {})

        return {
            "content": message.get("content", ""),
            "usage": {
                "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
                "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            },
            "raw": result,
        }

    def _call_openai(self, messages: List[Dict], model: str, temperature: float, max_tokens: int) -> Dict[str, Any]:
        """调用 OpenAI API"""
        import urllib.request
        import urllib.error

        api_key = config.get("openai_api_key")

        if not api_key:
            raise ValueError("OpenAI API key not configured")

        url = "https://api.openai.com/v1/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._parse_openai_response(result)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise Exception(f"OpenAI API error {e.code}: {error_body}")

    def _parse_openai_response(self, result: Dict) -> Dict[str, Any]:
        """解析 OpenAI 响应"""
        choices = result.get("choices", [])
        if not choices:
            return {"error": "No choices in response", "raw": result}

        choice = choices[0]
        message = choice.get("message", {})

        return {
            "content": message.get("content", ""),
            "usage": {
                "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
                "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            },
            "raw": result,
        }

    def _call_deepseek(self, messages: List[Dict], temperature: float, max_tokens: int) -> Dict[str, Any]:
        """调用 DeepSeek API"""
        import urllib.request
        import urllib.error

        api_key = config.get("deepseek_api_key")

        if not api_key:
            raise ValueError("DeepSeek API key not configured")

        url = "https://api.deepseek.com/chat/completions"

        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._parse_deepseek_response(result)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise Exception(f"DeepSeek API error {e.code}: {error_body}")

    def _parse_deepseek_response(self, result: Dict) -> Dict[str, Any]:
        """解析 DeepSeek 响应"""
        choices = result.get("choices", [])
        if not choices:
            return {"error": "No choices in response", "raw": result}

        choice = choices[0]
        message = choice.get("message", {})

        return {
            "content": message.get("content", ""),
            "usage": {
                "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
                "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            },
            "raw": result,
        }

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        return {
            "status": "healthy" if self.status.value == "running" else "unhealthy",
            "service": "llm_service",
            "providers": {
                "minimax": bool(config.get("minimax_api_key")),
                "openai": bool(config.get("openai_api_key")),
                "deepseek": bool(config.get("deepseek_api_key")),
            },
            "stats": {
                "total_requests": self._request_count,
                "total_tokens": self._total_tokens,
            },
        }


def main():
    """主入口"""
    service = LLMService(port=3006)
    run_service(service)


if __name__ == "__main__":
    main()
