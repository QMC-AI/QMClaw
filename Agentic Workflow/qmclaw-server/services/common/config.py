"""
services/common/config.py - 配置管理

统一管理服务配置，支持环境变量和配置文件。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _server_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_system_config() -> Dict[str, Any]:
    """读取 config/system.json，不存在或损坏时返回空 dict。"""
    config_file = _server_dir() / "config" / "system.json"
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Config] Failed to load {config_file}: {e}")
        return {}


def get_labrad_params() -> Dict[str, Any]:
    """LabRAD 连接参数。

    优先级：环境变量 LABRADHOST / LABRADPORT / LABRADTIMEOUT
           → config/system.json 的 lqcs.connection
           → 默认 localhost:7682 / 30s
    """
    connection = load_system_config().get("lqcs", {}).get("connection") or {}
    host = os.environ.get("LABRADHOST") or connection.get("host") or "localhost"
    port = int(os.environ.get("LABRADPORT") or connection.get("port") or 7682)
    timeout = int(os.environ.get("LABRADTIMEOUT") or connection.get("timeout") or 30)
    return {"host": host, "port": port, "timeout": timeout}


def labrad_connect():
    """按配置连接 LabRAD（host/port/timeout 可指向远程测控机）。"""
    import labrad

    params = get_labrad_params()
    return labrad.connect(
        host=params["host"], port=params["port"], timeout=params["timeout"]
    )


def get_labrad_paths() -> Dict[str, Optional[str]]:
    """config/system.json 的 lqcs.paths；值为 null 表示未显式配置。"""
    paths = load_system_config().get("lqcs", {}).get("paths") or {}
    return {
        "measure_scripts": paths.get("measure_scripts") or None,
        "backend_dir": paths.get("backend_dir") or None,
    }


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
