# -*- coding: utf-8 -*-
"""
真机接通（C 组）单元测试 — 不依赖运行中的微服务

覆盖：
1. get_labrad_params 优先级（env > system.json > 默认）
2. labrad_connect 把 host/port/timeout 传给 labrad.connect
3. get_actuation_flags 默认全关、env 可覆盖
4. 写闸门：闸门关闭时 /execute 与 /qubit/set_params 被拒，不进模拟分支
5. 离线模拟不受闸门限制

运行: D:/semester4/deploy/qmclaw/qmclaw/python.exe tests/test_real_machine_unit.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))


class TestLabradParams(unittest.TestCase):
    def setUp(self):
        for key in ("LABRADHOST", "LABRADPORT", "LABRADTIMEOUT"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key in ("LABRADHOST", "LABRADPORT", "LABRADTIMEOUT"):
            os.environ.pop(key, None)

    def test_defaults_when_no_config(self):
        from services.common.config import get_labrad_params

        with patch("services.common.config.load_system_config", return_value={}):
            p = get_labrad_params()
        self.assertEqual(p["host"], "localhost")
        self.assertEqual(p["port"], 7682)
        self.assertEqual(p["timeout"], 30)

    def test_system_json_priority(self):
        from services.common.config import get_labrad_params

        cfg = {"lqcs": {"connection": {"host": "10.0.0.5", "port": 7683, "timeout": 45}}}
        with patch("services.common.config.load_system_config", return_value=cfg):
            p = get_labrad_params()
        self.assertEqual(p["host"], "10.0.0.5")
        self.assertEqual(p["port"], 7683)
        self.assertEqual(p["timeout"], 45)

    def test_env_overrides_system_json(self):
        from services.common.config import get_labrad_params

        cfg = {"lqcs": {"connection": {"host": "10.0.0.5", "port": 7683, "timeout": 45}}}
        os.environ["LABRADHOST"] = "192.168.1.9"
        os.environ["LABRADPORT"] = "8000"
        with patch("services.common.config.load_system_config", return_value=cfg):
            p = get_labrad_params()
        self.assertEqual(p["host"], "192.168.1.9")
        self.assertEqual(p["port"], 8000)
        self.assertEqual(p["timeout"], 45)

    def test_labrad_connect_passes_params(self):
        from services.common.config import labrad_connect

        mock_labrad = MagicMock()
        mock_labrad.connect.return_value = MagicMock()
        cfg = {"lqcs": {"connection": {"host": "ctrl.lab", "port": 7682, "timeout": 12}}}
        with patch("services.common.config.load_system_config", return_value=cfg), \
             patch.dict(sys.modules, {"labrad": mock_labrad}):
            labrad_connect()
        mock_labrad.connect.assert_called_once_with(
            host="ctrl.lab", port=7682, timeout=12
        )


class TestActuationFlags(unittest.TestCase):
    def tearDown(self):
        for key in ("QMCLAW_ALLOW_EXECUTE", "QMCLAW_ALLOW_SET_PARAMS"):
            os.environ.pop(key, None)

    def test_default_all_false(self):
        from services.common.config import get_actuation_flags

        flags = get_actuation_flags("quantum_service")
        self.assertFalse(flags["allow_execute"])
        self.assertFalse(flags["allow_set_params"])

    def test_env_override(self):
        from services.common.config import get_actuation_flags

        os.environ["QMCLAW_ALLOW_EXECUTE"] = "1"
        flags = get_actuation_flags("quantum_service")
        self.assertTrue(flags["allow_execute"])
        self.assertFalse(flags["allow_set_params"])

    def test_config_file_actuation(self):
        from services.common.config import get_actuation_flags

        fake = {
            "quantum_service": {
                "actuation": {"allow_execute": True, "allow_set_params": False}
            }
        }
        with patch(
            "services.common.config._load_fallback_service_config",
            return_value=fake["quantum_service"],
        ):
            flags = get_actuation_flags("quantum_service")
        self.assertTrue(flags["allow_execute"])
        self.assertFalse(flags["allow_set_params"])


class TestWriteGate(unittest.TestCase):
    """闸门关闭时写入口必须拒绝，且不进入离线模拟分支。"""

    def _make_service(self):
        from services.quantum_service.server import QuantumService

        svc = QuantumService.__new__(QuantumService)
        svc._mode = QuantumService.Mode.ONLINE
        svc._mode_lock = __import__("threading").Lock()
        svc._busy = __import__("threading").Event()
        svc._busy_lock = __import__("threading").Lock()
        svc._current_task_id = None
        svc._running_tasks = {}
        svc._exec_stuck = None
        svc._labrad = None
        svc._labrad_lock = __import__("threading").Lock()
        svc._init_started = False
        svc._simulator = None
        svc._offline_provider = MagicMock()
        svc._fallback = MagicMock()
        svc._fallback.is_degraded = False
        svc._fallback.is_permanently_degraded = False
        svc._fallback.is_connected = False
        svc._fallback.last_error = None
        svc._fallback_qubits = {}
        return svc

    def test_execute_rejected_when_gate_closed(self):
        svc = self._make_service()
        called = {"sim": False}

        def _sim(*a, **k):
            called["sim"] = True
            return {}

        svc._execute_offline_simulation = _sim

        with patch(
            "services.common.config.get_actuation_flags",
            return_value={"allow_execute": False, "allow_set_params": False},
        ), patch.object(
            type(svc), "_is_offline_mode", new_callable=lambda: property(lambda self: False)
        ):
            result = svc._handle_execute({"code": "sq.t1('q1')"})

        self.assertEqual(result["status"], "read_only")
        self.assertIn("hint", result)
        self.assertFalse(result["actuation_allowed"])
        self.assertFalse(called["sim"], "must not enter offline simulation branch")
        self.assertFalse(svc._busy.is_set(), "must not acquire busy lock")

    def test_set_params_rejected_when_gate_closed(self):
        svc = self._make_service()
        with patch(
            "services.common.config.get_actuation_flags",
            return_value={"allow_execute": False, "allow_set_params": False},
        ):
            result = svc._handle_qubit_set_params({"name": "q1", "params": {"f10": 5.0}})

        self.assertEqual(result["status"], "read_only")
        self.assertFalse(result["success"])
        self.assertIn("hint", result)

    def test_offline_simulation_bypasses_gate(self):
        svc = self._make_service()
        called = {"sim": False}

        def _sim(code, task_id, data):
            called["sim"] = True
            return {"status": "offline_simulated"}

        svc._execute_offline_simulation = _sim

        with patch(
            "services.common.config.get_actuation_flags",
            return_value={"allow_execute": False, "allow_set_params": False},
        ), patch.object(
            type(svc), "_is_offline_mode", new_callable=lambda: property(lambda self: True)
        ):
            result = svc._handle_execute({"code": "sq.t1('q1')"})

        self.assertTrue(called["sim"], "offline sim must run regardless of gate")
        self.assertEqual(result["status"], "offline_simulated")

    def test_stuck_blocks_execute(self):
        svc = self._make_service()
        svc._exec_stuck = "Task X timed out"

        with patch(
            "services.common.config.get_actuation_flags",
            return_value={"allow_execute": True, "allow_set_params": True},
        ), patch.object(
            type(svc), "_is_offline_mode", new_callable=lambda: property(lambda self: False)
        ):
            result = svc._handle_execute({"code": "sq.t1('q1')"})

        self.assertEqual(result["status"], "stuck")

    def test_health_exposes_data_source_and_gate(self):
        svc = self._make_service()
        svc._get_mode = lambda: "offline"
        svc._data_source = lambda: "offline"
        svc._connection_fields = lambda: {
            "data_source": "offline",
            "actuation_allowed": False,
            "allow_execute": False,
            "allow_set_params": False,
            "labrad_host": "localhost",
            "labrad_port": 7682,
        }
        health = svc._handle_health()
        self.assertEqual(health["data_source"], "offline")
        self.assertFalse(health["actuation_allowed"])
        self.assertEqual(health["labrad_host"], "localhost")
        self.assertIn("stuck", health)


class TestToolLayerGate(unittest.TestCase):
    def test_run_code_tool_rejects_when_gate_closed(self):
        from services.common.quantum_tools.execution import RunCodeTool

        tool = RunCodeTool(
            executor=lambda code: {"status": "success"},
            is_connected=lambda: True,
            actuation_flags=lambda: {"allow_execute": False, "allow_set_params": False},
        )
        result = tool.execute("sq.t1('q1')")
        self.assertFalse(result.success)
        meta = result.metadata or {}
        self.assertEqual(meta.get("status"), "read_only")
        self.assertTrue(meta.get("hint"))

    def test_run_code_tool_passes_read_only_from_server(self):
        from services.common.quantum_tools.execution import RunCodeTool

        tool = RunCodeTool(
            executor=lambda code: {
                "status": "read_only",
                "error": "只读",
                "hint": "翻开闸门",
            },
            is_connected=lambda: True,
            actuation_flags=lambda: {"allow_execute": True, "allow_set_params": True},
        )
        result = tool.execute("sq.t1('q1')")
        self.assertFalse(result.success)
        meta = result.metadata or {}
        self.assertEqual(meta.get("status"), "read_only")
        self.assertEqual(meta.get("hint"), "翻开闸门")


if __name__ == "__main__":
    unittest.main(verbosity=2)
