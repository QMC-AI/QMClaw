"""
services/base/service_registry.py - 服务注册表

管理所有服务的配置和状态，支持服务发现。
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional


class ServiceRegistry:
    """服务注册表

    管理服务配置，支持从配置文件加载。
    """

    _instance = None
    _lock = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._services: Dict[str, Dict] = {}
        self._config_path: Optional[Path] = None
        self._initialized = True

    def load_config(self, config_path: Optional[str] = None) -> bool:
        """从配置文件加载服务配置

        Args:
            config_path: 配置文件路径，默认使用 config/services.json

        Returns:
            是否加载成功
        """
        if config_path is None:
            # 默认路径: .../qmclaw-server/config/services.json
            server_dir = Path(__file__).parent.parent.parent
            config_path = server_dir / "config" / "services.json"

        self._config_path = Path(config_path)

        if not self._config_path.exists():
            print(f"[ServiceRegistry] Config not found: {self._config_path}")
            return False

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._services = data.get("services", {})
            print(f"[ServiceRegistry] Loaded {len(self._services)} services")
            return True
        except Exception as e:
            print(f"[ServiceRegistry] Failed to load config: {e}")
            return False

    def register(self, name: str, host: str, port: int, health_path: str = "/health", enabled: bool = True):
        """注册服务

        Args:
            name: 服务名称
            host: 服务主机
            port: 服务端口
            health_path: 健康检查路径
            enabled: 是否启用
        """
        self._services[name] = {
            "host": host,
            "port": port,
            "health_path": health_path,
            "enabled": enabled,
        }

    def unregister(self, name: str):
        """注销服务"""
        if name in self._services:
            del self._services[name]

    def get(self, name: str) -> Optional[Dict]:
        """获取服务配置"""
        return self._services.get(name)

    def list_services(self, enabled_only: bool = True) -> Dict[str, Dict]:
        """列出所有服务"""
        if enabled_only:
            return {k: v for k, v in self._services.items() if v.get("enabled", True)}
        return self._services.copy()

    def get_url(self, name: str) -> Optional[str]:
        """获取服务 URL"""
        service = self.get(name)
        if service:
            return f"http://{service['host']}:{service['port']}"
        return None

    def save_config(self):
        """保存配置到文件"""
        if self._config_path:
            try:
                with open(self._config_path, "w", encoding="utf-8") as f:
                    json.dump({"services": self._services}, f, indent=2, ensure_ascii=False)
                print(f"[ServiceRegistry] Config saved to {self._config_path}")
            except Exception as e:
                print(f"[ServiceRegistry] Failed to save config: {e}")

    @classmethod
    def get_instance(cls) -> 'ServiceRegistry':
        """获取单例实例"""
        return cls()


# 全局实例
registry = ServiceRegistry.get_instance()
