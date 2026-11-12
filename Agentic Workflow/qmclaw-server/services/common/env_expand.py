"""模型配置中的环境变量展开。

`config/model_configs.json` 里不要写实验室内网地址，改写成占位符：

    "baseUrl": "${QMCLAW_DEEPSEEK_BASE_URL}"

启动时由环境变量注入真实地址。变量未设置时：
- 若原值是占位符，按 provider 回落到官方地址（deepseek/openai）；
- 若原值是普通字面量，原样保留。

这样版本库里的配置可以公开，真实地址只存在于本机 `.env` 或系统环境变量。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
}


def expand_env(value: str) -> str:
    """把字符串里的 ``${VAR}`` 替换为环境变量值；未设置的变量展开为空串。"""
    return _VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)


def resolve_model_base_url(model: Dict[str, Any]) -> str:
    """解析单个模型配置的 baseUrl（含未设置环境变量时的 provider 回落）。"""
    raw = str(model.get("baseUrl") or "")
    if not raw:
        return ""

    expanded = expand_env(raw).strip()
    if expanded:
        return expanded

    # 占位符没解析出来（变量未设置）→ 回落到 provider 官方地址
    if _VAR_RE.search(raw):
        return DEFAULT_BASE_URLS.get(str(model.get("provider") or ""), "")
    return ""


def expand_model_configs(data: Any) -> Any:
    """就地展开 ``{"models": [...]}`` 或模型列表里的 baseUrl，返回原对象。"""
    models = data.get("models") if isinstance(data, dict) else data
    if isinstance(models, list):
        for model in models:
            if isinstance(model, dict) and "baseUrl" in model:
                model["baseUrl"] = resolve_model_base_url(model)
    return data
