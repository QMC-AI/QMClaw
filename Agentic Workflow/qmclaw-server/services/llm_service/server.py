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

    @staticmethod
    def _mask(secret: Optional[str]) -> str:
        """脱敏显示密钥：只暴露长度，绝不打印内容"""
        if not secret:
            return "(not set)"
        return f"SET (len={len(secret)})"

    def _init_providers(self):
        """初始化 Provider 客户端"""
        # 只报告密钥是否存在，不打印内容
        _log(f"API keys: minimax={self._mask(config.get('minimax_api_key'))} "
             f"openai={self._mask(config.get('openai_api_key'))} "
             f"anthropic={self._mask(config.get('anthropic_api_key'))} "
             f"deepseek={self._mask(config.get('deepseek_api_key'))}")

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
        """处理聊天请求

        可选 tools / tool_choice：透传给支持原生 function calling 的 provider
        （deepseek / openai 同为 OpenAI 兼容形状）。传了 tools 时，响应里会多出
        tool_calls 与 finish_reason，由调用方（agent_service）驱动工具循环。
        """
        messages = data.get("messages", [])
        model = data.get("model", "minimax")
        temperature = data.get("temperature", 0.7)
        max_tokens = data.get("max_tokens", 4096)
        tools = data.get("tools") or None
        tool_choice = data.get("tool_choice")

        if not messages:
            return {"error": "messages is required"}

        _log(f"Chat request: model={model}, messages={len(messages)}, tools={len(tools) if tools else 0}")

        try:
            # 根据 model 调用不同的 provider
            if model.startswith("minimax") or model == "minimax":
                if tools:
                    _log("WARNING: MiniMax provider does not support native tool calling; "
                         "proceeding without tools")
                result = self._call_minimax(messages, temperature, max_tokens)
            elif model.startswith("gpt") or model.startswith("openai"):
                result = self._call_openai(messages, model, temperature, max_tokens,
                                           tools=tools, tool_choice=tool_choice)
            elif model.startswith("deepseek"):
                result = self._call_deepseek(messages, temperature, max_tokens, model,
                                             tools=tools, tool_choice=tool_choice)
            else:
                # 未知 model：回退到唯一配置了 key 的 provider，而不是硬走 MiniMax
                if config.get("deepseek_api_key"):
                    _log(f"Unknown model '{model}', falling back to deepseek")
                    result = self._call_deepseek(messages, temperature, max_tokens, "deepseek-flash",
                                                 tools=tools, tool_choice=tool_choice)
                else:
                    _log(f"Unknown model '{model}', falling back to minimax")
                    result = self._call_minimax(messages, temperature, max_tokens)

            # 更新统计
            with self._request_lock:
                self._request_count += 1
                if "usage" in result:
                    self._total_tokens += result["usage"].get("total_tokens", 0)

            duration = time.time() - start_time
            _log(f"Chat completed: duration={duration:.2f}s, "
                 f"tokens={result.get('usage', {}).get('total_tokens', 0)}, "
                 f"tool_calls={len(result.get('tool_calls') or [])}")

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

    def _call_openai(self, messages: List[Dict], model: str, temperature: float,
                     max_tokens: int, tools: List[Dict] = None,
                     tool_choice: Any = None) -> Dict[str, Any]:
        """调用 OpenAI API（与 DeepSeek 同为 OpenAI 兼容形状，支持原生工具调用）"""
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
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

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
        tool_calls = self._normalize_tool_calls(message)

        parsed = {
            "content": message.get("content", "") or "",
            "usage": {
                "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
                "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            },
            "raw": result,
        }
        if tool_calls:
            parsed["tool_calls"] = tool_calls
            parsed["assistant_message"] = message
        parsed["finish_reason"] = choice.get("finish_reason")
        return parsed

    def _call_deepseek(self, messages: List[Dict], temperature: float,
                       max_tokens: int, model: str = "deepseek-flash",
                       tools: List[Dict] = None,
                       tool_choice: Any = None) -> Dict[str, Any]:
        """调用 DeepSeek API

        model 由调用方传入。旧版本这里硬编码 "deepseek-chat"，
        导致上层选什么模型都被忽略（deepseek-chat 实际是 deepseek-flash 的别名）。

        tools 非空时走原生 function calling：响应里会带 tool_calls。
        messages 原样透传（原生工具调用要求 assistant 的 tool_calls 消息与其后的
        role="tool" 消息成对出现，由调用方负责构造）。
        """
        import urllib.request
        import urllib.error

        api_key = config.get("deepseek_api_key")

        if not api_key:
            raise ValueError("DeepSeek API key not configured")

        url = "https://api.deepseek.com/chat/completions"

        # 只接受 deepseek 系列，避免把别的 provider 的 model id 传过去
        if not model or not model.startswith("deepseek"):
            model = "deepseek-flash"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice

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

    @staticmethod
    def _normalize_tool_calls(message: Dict) -> List[Dict[str, Any]]:
        """把 API 返回的 tool_calls 归一化成 [{id, name, arguments(dict)}]

        API 给的 arguments 是 **JSON 字符串**，这里解析成 dict 方便上层直接调用工具。
        解析失败时保留原始串并标记 parse_error，由上层决定如何回灌给模型。
        """
        out: List[Dict[str, Any]] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments")
            args: Any = raw_args
            parse_error = None
            if isinstance(raw_args, str):
                if not raw_args.strip():
                    args = {}
                else:
                    try:
                        args = json.loads(raw_args)
                    except Exception as e:
                        args = {}
                        parse_error = f"{type(e).__name__}: {e}"
            elif raw_args is None:
                args = {}
            if not isinstance(args, dict):
                parse_error = parse_error or f"arguments is {type(args).__name__}, expected object"
                args = {}
            item = {
                "id": tc.get("id") or "",
                "name": fn.get("name") or "",
                "arguments": args,
                "arguments_raw": raw_args,
            }
            if parse_error:
                item["parse_error"] = parse_error
            out.append(item)
        return out

    def _parse_deepseek_response(self, result: Dict) -> Dict[str, Any]:
        """解析 DeepSeek 响应"""
        choices = result.get("choices", [])
        if not choices:
            return {"error": "No choices in response", "raw": result}

        choice = choices[0]
        message = choice.get("message", {})
        tool_calls = self._normalize_tool_calls(message)

        parsed = {
            "content": message.get("content", "") or "",
            "usage": {
                "prompt_tokens": result.get("usage", {}).get("prompt_tokens", 0),
                "completion_tokens": result.get("usage", {}).get("completion_tokens", 0),
                "total_tokens": result.get("usage", {}).get("total_tokens", 0),
            },
            "raw": result,
        }
        if tool_calls:
            parsed["tool_calls"] = tool_calls
            # 供上层把 assistant 消息原样回灌进 messages（协议要求）
            parsed["assistant_message"] = message
        parsed["finish_reason"] = choice.get("finish_reason")
        return parsed

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
