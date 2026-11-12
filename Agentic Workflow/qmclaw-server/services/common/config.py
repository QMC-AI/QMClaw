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


def _load_fallback_service_config(service_name: str) -> Dict[str, Any]:
    config_file = _server_dir() / "config" / "fallback_config.json"
    if not config_file.exists():
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f).get(service_name, {})
    except Exception as e:
        print(f"[Config] Failed to load {config_file}: {e}")
        return {}


def get_actuation_flags(service_name: str = "quantum_service") -> Dict[str, bool]:
    """写闸门：是否允许执行实验 / 改 qubit 参数。

    优先级：环境变量 QMCLAW_ALLOW_EXECUTE / QMCLAW_ALLOW_SET_PARAMS
           → fallback_config.json 的 {service}.actuation
           → 默认全部 false（只读联调）
    """
    actuation = _load_fallback_service_config(service_name).get("actuation") or {}

    def _flag(env_key: str, config_key: str) -> bool:
        env_val = os.environ.get(env_key)
        if env_val is not None:
            return env_val.strip().lower() in ("1", "true", "yes", "on")
        return bool(actuation.get(config_key, False))

    return {
        "allow_execute": _flag("QMCLAW_ALLOW_EXECUTE", "allow_execute"),
        "allow_set_params": _flag("QMCLAW_ALLOW_SET_PARAMS", "allow_set_params"),
        # 治理闸门（可热切）：要求真机执行必须带 task_id，走 task_queue 的额度/票据/审计。
        # 默认 false = 保持现状（直发可用）；打开后 /execute 不带 task_id 一律拒绝。
        # 见 docs/ARCHITECTURE_ROADMAP.md §2.1 单写者原则。
        "require_task": _flag("QMCLAW_REQUIRE_TASK", "require_task"),
        # 单写者模式（可热切）：off / shadow / enforce。
        # 默认 shadow —— 只观测不改变执行路径，不打断用户正在跑的真机实验。
        # 见 docs/ARCHITECTURE_ROADMAP.md §2.1 单写者原则。
        "writer_mode": _writer_mode(),
        # 回放层自动录制（可热切）：off / code_only / full。
        # 默认 off —— 不额外读设备、不增加延迟，不打断用户正在跑的真机实验。
        # 见 docs/ARCHITECTURE_ROADMAP.md §2.6 回放层。
        "replay_auto_record": _replay_auto_record(),
    }


# 回放层自动录制的三种模式
REPLAY_RECORD_MODES = ("off", "code_only", "full")

DEFAULT_REPLAY_RECORD_MODE = "off"


def _replay_auto_record() -> str:
    """解析回放层自动录制模式：环境变量 → config → 默认 off。

    - off：完全不录（默认）
    - code_only：只录代码/比特/数据集号，不读设备
    - full：额外采 registry 前后快照（会增加 LabRAD 读，需评估对真机实验的影响）
    """
    env_val = os.environ.get("QMCLAW_REPLAY_AUTO_RECORD")
    if env_val is not None and env_val.strip():
        mode = env_val.strip().lower()
        return mode if mode in REPLAY_RECORD_MODES else DEFAULT_REPLAY_RECORD_MODE
    actuation = _load_fallback_service_config("quantum_service").get("actuation") or {}
    mode = str(actuation.get("replay_auto_record") or DEFAULT_REPLAY_RECORD_MODE).strip().lower()
    return mode if mode in REPLAY_RECORD_MODES else DEFAULT_REPLAY_RECORD_MODE


def _writer_mode() -> str:
    """解析单写者模式：环境变量 QMCLAW_WRITER_MODE → config → 默认 shadow。"""
    from services.quantum_service.writer import WRITER_MODES, DEFAULT_WRITER_MODE

    env_val = os.environ.get("QMCLAW_WRITER_MODE")
    if env_val is not None and env_val.strip():
        mode = env_val.strip().lower()
        return mode if mode in WRITER_MODES else DEFAULT_WRITER_MODE
    actuation = _load_fallback_service_config("quantum_service").get("actuation") or {}
    mode = str(actuation.get("writer_mode") or DEFAULT_WRITER_MODE).strip().lower()
    return mode if mode in WRITER_MODES else DEFAULT_WRITER_MODE


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
        # 先加载 qmclaw-server/.env（与 Express / hermes_service 同一文件），
        # 否则 Python 子进程只看得到系统环境变量，.env 里的 Key 会读不到。
        server_dir = Path(__file__).parent.parent.parent
        try:
            from dotenv import load_dotenv
            load_dotenv(server_dir / ".env")
        except Exception as e:
            print(f"[Config] load_dotenv failed: {e}")

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
                    # baseUrl 里的 ${VAR} 在加载时展开，配置入库存占位符即可
                    from .env_expand import expand_model_configs
                    expand_model_configs(data)
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
