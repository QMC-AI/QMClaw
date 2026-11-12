"""模型 baseUrl 占位符展开的离线单测。

不依赖 dotenv / 服务启动：直接按文件路径加载 env_expand，避免 import
services.common 时触发全局 Config 初始化。
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "env_expand", _SERVER_DIR / "services" / "common" / "env_expand.py"
)
env_expand = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(env_expand)

VAR = "QMCLAW_DEEPSEEK_BASE_URL"
PLACEHOLDER = "${" + VAR + "}"


class TestEnvExpand(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get(VAR)
        os.environ.pop(VAR, None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop(VAR, None)
        else:
            os.environ[VAR] = self._old

    def test_expand_when_set(self):
        os.environ[VAR] = "http://127.0.0.1:9999/v1"
        self.assertEqual(env_expand.expand_env(PLACEHOLDER), "http://127.0.0.1:9999/v1")
        self.assertEqual(
            env_expand.resolve_model_base_url(
                {"provider": "deepseek", "baseUrl": PLACEHOLDER}
            ),
            "http://127.0.0.1:9999/v1",
        )

    def test_unset_falls_back_to_provider_default(self):
        self.assertEqual(env_expand.expand_env(PLACEHOLDER), "")
        self.assertEqual(
            env_expand.resolve_model_base_url(
                {"provider": "deepseek", "baseUrl": PLACEHOLDER}
            ),
            "https://api.deepseek.com/v1",
        )
        self.assertEqual(
            env_expand.resolve_model_base_url(
                {"provider": "openai", "baseUrl": "${MISSING_VAR}"}
            ),
            "https://api.openai.com/v1",
        )

    def test_literal_and_empty_preserved(self):
        self.assertEqual(
            env_expand.resolve_model_base_url(
                {"provider": "openai", "baseUrl": "https://example.test/v1"}
            ),
            "https://example.test/v1",
        )
        self.assertEqual(
            env_expand.resolve_model_base_url({"provider": "openai"}), ""
        )

    def test_expand_model_configs_in_place(self):
        data = {
            "models": [
                {"provider": "deepseek", "baseUrl": PLACEHOLDER},
                {"provider": "openai", "baseUrl": "https://example.test/v1"},
            ]
        }
        env_expand.expand_model_configs(data)
        self.assertEqual(data["models"][0]["baseUrl"], "https://api.deepseek.com/v1")
        self.assertEqual(data["models"][1]["baseUrl"], "https://example.test/v1")


if __name__ == "__main__":
    unittest.main()
