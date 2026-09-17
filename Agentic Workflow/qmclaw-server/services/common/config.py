"""
services/common/config.py - 配置管理

统一管理服务配置，支持环境变量和配置文件。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class Config:
    """配置管理类"""

    _instance = None
    _config: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._config:
            self._load_config()

    def _load_config(self):
        """加载配置"""
        # 从环境变量加载
        self._config = {
            # LLM API Keys
            "minimax_api_key": os.environ.get("MINIMAX_API_KEY", ""),
            "minimax_group_id": os.environ.get("MINIMAX_GROUP_ID", ""),
            "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
            "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        }

        # 尝试从配置文件加载
        server_dir = Path(__file__).parent.parent.parent
        config_file = server_dir / "config" / "model_configs.json"

        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._config.update(data)
            except Exception as e:
                print(f"[Config] Failed to load {config_file}: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """设置配置值"""
        self._config[key] = value

    def update(self, data: Dict[str, Any]):
        """批量更新配置"""
        self._config.update(data)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self._config.copy()


# 全局实例
config = Config()
