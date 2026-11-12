"""dsl/llm.py - 统一的 LLM 调用（OpenAI 兼容 / Anthropic 兼容）。

用途：P2 的 `decide` 节点（分支决策）与 `ask` 节点（澄清）都要打 LLM，
不能让每个调用点各写一套 HTTP。

配置来源：`config/qubitclient.json` 的 `llm_decision` 段
```json
{"provider": "openai" | "anthropic",
 "base_url": "https://api.minimaxi.com/anthropic",
 "model": "MiniMax-M2.7",
 "api_key": "...",
 "api_key_env": "MINIMAX_API_KEY",
 "temperature": 0.2, "maxTokens": 1200}
```
取 key 的优先级：`api_key` → `api_key_env` 对应的环境变量（`.env` 由
`services/common/config.py` 载入）→ qubitclient.json 的 `llm` 段。

协议：
- openai   → POST {base_url}/chat/completions，Header `Authorization: Bearer <key>`
- anthropic→ POST {base_url}/v1/messages，Header `x-api-key: <key>` + `anthropic-version`

约定：**本模块不抛异常**，失败时返回 (None, err_str)，由调用方决定是
halt 还是降级（决策失败不等于流程失败，但必须记进 outcome）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TIMEOUT = 120


def server_root(root: Optional[Path] = None) -> Path:
    """`services/workflow_service/dsl/llm.py` → `qmclaw-server/`。

    路径层级：dsl → workflow_service → services → qmclaw-server（parents[3]）。
    """
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_llm_config(section: str = "llm_decision",
                    root: Optional[Path] = None) -> Dict[str, Any]:
    """读配置并把 api_key 解析成明文（优先 api_key → api_key_env → llm 段）。"""
    base = server_root(root)
    qcfg = _read_json(base / "config" / "qubitclient.json")
    cfg = dict(qcfg.get(section) or {})
    if not cfg:
        cfg = dict(qcfg.get("llm") or {})
        cfg.setdefault("provider", "openai")
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        env_name = str(cfg.get("api_key_env") or "").strip()
        if env_name:
            key = str(os.environ.get(env_name) or "").strip()
    if not key:
        key = str((qcfg.get("llm") or {}).get("api_key") or "").strip()
    cfg["api_key"] = key
    cfg.setdefault("provider", "openai")
    cfg.setdefault("temperature", 0.2)
    cfg.setdefault("maxTokens", 1200)
    return cfg


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str],
               timeout: int) -> Tuple[Optional[Dict[str, Any]], str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        return None, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def chat(cfg: Dict[str, Any], messages: List[Dict[str, Any]],
         temperature: Optional[float] = None, max_tokens: Optional[int] = None,
         timeout: int = DEFAULT_TIMEOUT) -> Tuple[Optional[str], str]:
    """打一次 LLM，返回 (content, err)；两者必有其一为空串/None。"""
    provider = str(cfg.get("provider") or "openai").lower()
    base_url = str(cfg.get("base_url") or "").strip()
    model = str(cfg.get("model") or "").strip()
    key = str(cfg.get("api_key") or "").strip()
    if not base_url or not model:
        return None, "llm_decision 未配置（缺 base_url / model）"
    if not key:
        return None, "llm_decision 未配置（缺 api_key / api_key_env）"
    temp = float(cfg.get("temperature", 0.2) if temperature is None else temperature)
    mtokens = int(cfg.get("maxTokens", 1200) if max_tokens is None else max_tokens)

    if provider == "anthropic":
        url = base_url.rstrip("/") + "/v1/messages"
        payload = {"model": model, "max_tokens": mtokens,
                   "temperature": temp, "messages": messages}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        raw, err = _post_json(url, payload, headers, timeout)
        if err:
            return None, err
        try:
            blocks = raw.get("content") or []
            text = "".join(str(b.get("text") or "") for b in blocks
                           if isinstance(b, dict))
            if not text:
                return None, "LLM 返回空内容"
            return text, ""
        except Exception as exc:
            return None, f"解析 anthropic 响应失败: {exc}"

    # openai 兼容
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages,
               "temperature": temp, "max_tokens": mtokens}
    headers = {"Authorization": "Bearer " + key}
    raw, err = _post_json(url, payload, headers, timeout)
    if err:
        return None, err
    try:
        content = raw["choices"][0]["message"]["content"]
    except Exception:
        return None, f"LLM 返回无 content：{str(raw)[:200]}"
    return str(content), ""


def extract_json(text: str) -> Dict[str, Any]:
    """从模型输出里抠 JSON（容忍 ```json 围栏和前后废话）。"""
    s = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if fence:
        s = fence.group(1).strip()
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        pass
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(s[start:end + 1])
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            pass
    return {}


__all__ = ["load_llm_config", "chat", "extract_json", "server_root"]
