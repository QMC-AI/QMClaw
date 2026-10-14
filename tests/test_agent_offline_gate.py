# -*- coding: utf-8 -*-
"""
agent 侧写闸门的离线豁免（B1）

背景：agent 侧 `RunCodeTool` / `RunExperimentTool` 在调用服务**之前**就按写闸门
拦死，而服务端 `quantum_service/server.py` 是**先判离线**再判闸门（离线模拟不碰
硬件、不受写闸门限制）—— 两侧口径不一致，导致离线模式跑实验永远 read_only、
charts 恒空。

本测试锁定修复后的 4 种组合 × 2 个工具，外加一条真实 HTTP 验收：

    1. 在线 + 闸门关 -> FORBIDDEN / read_only（真机安全，行为与改动前一致）
    2. 离线 + 闸门关 -> 放行（走到 executor）
    3. 在线 + 闸门开 -> 放行
    4. 离线 + 闸门开 -> 放行

运行:
    D:/semester4/deploy/qmclaw/qmclaw/python.exe -m pytest tests/test_agent_offline_gate.py -q
"""

import sys
import unittest
import urllib.request
import json as _json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from services.common.quantum_tools.execution import RunCodeTool, RunExperimentTool  # noqa: E402

GATE_CLOSED = lambda: {"allow_execute": False, "allow_set_params": False}  # noqa: E731
GATE_OPEN = lambda: {"allow_execute": True, "allow_set_params": True}  # noqa: E731

QUANTUM_SERVICE_URL = "http://localhost:3003"


def _code_executor(result):
    """返回一个假 executor，记录是否被调用过。"""
    calls = []

    def executor(code):
        calls.append(code)
        return result

    return executor, calls


def _make_run_code(allow_execute: bool, offline: bool, result=None):
    executor, calls = _code_executor(result or {"status": "success"})
    tool = RunCodeTool(
        executor=executor,
        is_connected=lambda: True,
        actuation_flags=lambda: {
            "allow_execute": allow_execute,
            "allow_set_params": allow_execute,
        },
        is_offline=lambda: offline,
    )
    return tool, calls


def _make_run_experiment(allow_execute: bool, offline: bool, result=None):
    executor, calls = _code_executor(result or {"status": "success"})
    tool = RunExperimentTool(
        code_builder=lambda name, params: f"sq.{name}(q10lu8, do_plot=True)",
        executor=executor,
        is_connected=lambda: True,
        actuation_flags=lambda: {
            "allow_execute": allow_execute,
            "allow_set_params": allow_execute,
        },
        is_offline=lambda: offline,
    )
    return tool, calls


class TestRunCodeToolOfflineGate(unittest.TestCase):
    """RunCodeTool：4 种组合"""

    def test_online_gate_closed_is_rejected(self):
        """在线 + 闸门关 -> FORBIDDEN / read_only，且绝不调用 executor"""
        tool, calls = _make_run_code(allow_execute=False, offline=False)
        result = tool.execute("sq.t1(q10lu8, do_plot=True)")

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "FORBIDDEN")
        self.assertEqual(result.metadata.get("status"), "read_only")
        self.assertIs(result.metadata.get("actuation_allowed"), False)
        self.assertEqual(result.error, "当前为只读联调，禁止向真机下发实验")
        self.assertTrue(result.metadata.get("hint"))
        self.assertEqual(calls, [], "闸门关闭时不应下发任何代码")

    def test_offline_gate_closed_is_allowed(self):
        """离线 + 闸门关 -> 放行（豁免），executor 必须被调用"""
        tool, calls = _make_run_code(allow_execute=False, offline=True)
        result = tool.execute("sq.t1(q10lu8, do_plot=True)")

        self.assertTrue(result.success, f"离线应放行，实际: {result.to_dict()}")
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(result.metadata.get("status"), "read_only")

    def test_online_gate_open_is_allowed(self):
        """在线 + 闸门开 -> 放行"""
        tool, calls = _make_run_code(allow_execute=True, offline=False)
        result = tool.execute("sq.t1(q10lu8, do_plot=True)")

        self.assertTrue(result.success, f"闸门开应放行，实际: {result.to_dict()}")
        self.assertEqual(len(calls), 1)

    def test_offline_gate_open_is_allowed(self):
        """离线 + 闸门开 -> 放行"""
        tool, calls = _make_run_code(allow_execute=True, offline=True)
        result = tool.execute("sq.t1(q10lu8, do_plot=True)")

        self.assertTrue(result.success)
        self.assertEqual(len(calls), 1)


class TestRunExperimentToolOfflineGate(unittest.TestCase):
    """RunExperimentTool：4 种组合"""

    def test_online_gate_closed_is_rejected(self):
        """在线 + 闸门关 -> FORBIDDEN / read_only，且绝不调用 executor"""
        tool, calls = _make_run_experiment(allow_execute=False, offline=False)
        result = tool.execute("t1", {"target": "q10lu8"})

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "FORBIDDEN")
        self.assertEqual(result.metadata.get("status"), "read_only")
        self.assertIs(result.metadata.get("actuation_allowed"), False)
        self.assertEqual(result.error, "当前为只读联调，禁止向真机下发实验")
        self.assertTrue(result.metadata.get("hint"))
        self.assertEqual(calls, [], "闸门关闭时不应下发任何代码")

    def test_offline_gate_closed_is_allowed(self):
        """离线 + 闸门关 -> 放行（豁免），executor 必须被调用"""
        tool, calls = _make_run_experiment(allow_execute=False, offline=True)
        result = tool.execute("t1", {"target": "q10lu8"})

        self.assertTrue(result.success, f"离线应放行，实际: {result.to_dict()}")
        self.assertEqual(len(calls), 1)

    def test_offline_gate_closed_accepts_offline_simulated_status(self):
        """离线放行后，服务端 offline_simulated 结果应被当成成功"""
        tool, calls = _make_run_experiment(
            allow_execute=False,
            offline=True,
            result={
                "status": "offline_simulated",
                "simulated": True,
                "qubit": "q10lu8",
                "metrics": {"t1": 1.23e-5},
                "data": {"x": [0, 1], "y": [0.9, 0.4]},
                "plotPath": "/plots/offline_probe.png",
                "dataset_id": "ds-probe",
            },
        )
        result = tool.execute("t1", {"target": "q10lu8"})

        self.assertTrue(result.success, f"offline_simulated 应为成功: {result.to_dict()}")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.metadata.get("dataset_id"), "ds-probe")

    def test_online_gate_open_is_allowed(self):
        """在线 + 闸门开 -> 放行"""
        tool, calls = _make_run_experiment(allow_execute=True, offline=False)
        result = tool.execute("t1", {"target": "q10lu8"})

        self.assertTrue(result.success, f"闸门开应放行，实际: {result.to_dict()}")
        self.assertEqual(len(calls), 1)

    def test_offline_gate_open_is_allowed(self):
        """离线 + 闸门开 -> 放行"""
        tool, calls = _make_run_experiment(allow_execute=True, offline=True)
        result = tool.execute("t1", {"target": "q10lu8"})

        self.assertTrue(result.success)
        self.assertEqual(len(calls), 1)


# 真实服务返回体样本（2026-10-14 实跑 quantum_service /execute 离线模式截取，
# 字段与 services/agent_service/adapter.py::execute_code 的透传分支一致）。
# 该分支对 status in ("success","offline_simulated") 原样返回，所以把这份样本
# 喂给工具层等价于"服务端离线跑通后 agent 侧的完整链路"。
_REAL_OFFLINE_RESPONSE = {
    "task_id": "offline_gate_probe",
    "status": "offline_simulated",
    "simulated": True,
    "exp_type": "t1",
    "qubit": "q10lu8",
    "dataset_id": "t1__20261014_probe",
    "source_data": "offline_data/t1/t1__20261014_probe.h5",
    "plotPath": "/plots/offline_sim_t1_1791978991185.png",
    "metrics": {"t1": 1.4e-5},
    "data": {"type": "line", "x": [0.0, 1.0, 2.0], "y": [0.98, 0.6, 0.35]},
    "stdout": "",
}


class TestRecordedOfflineResponseReplay(unittest.TestCase):
    """用真实服务返回体样本回放 agent 侧链路（关闸门 + 离线）。"""

    def test_run_code_tool_replays_recorded_offline_response(self):
        tool, calls = _make_run_code(
            allow_execute=False, offline=True, result=_REAL_OFFLINE_RESPONSE
        )
        result = tool.execute("sq.t1(q10lu8, do_plot=True)")

        self.assertTrue(result.success, f"应放行并成功: {result.to_dict()}")
        self.assertEqual(len(calls), 1)
        payload = result.data.get("result") or {}
        self.assertEqual(payload.get("status"), "offline_simulated")
        self.assertEqual(payload.get("dataset_id"), "t1__20261014_probe")

    def test_run_experiment_tool_replays_recorded_offline_response(self):
        tool, calls = _make_run_experiment(
            allow_execute=False, offline=True, result=_REAL_OFFLINE_RESPONSE
        )
        result = tool.execute("t1", {"target": "q10lu8"})

        self.assertTrue(result.success, f"应放行并成功: {result.to_dict()}")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.data.get("status"), "success")
        self.assertEqual(result.data.get("dataset_id"), "t1__20261014_probe")
        self.assertEqual(result.data.get("plots")[0]["path"], _REAL_OFFLINE_RESPONSE["plotPath"])
        self.assertEqual(result.data.get("arrays", {}).get("x"), [0.0, 1.0, 2.0])


class TestDefaultKeepsStrictGate(unittest.TestCase):
    """不传 is_offline 时默认按在线处理（向后兼容硬要求）"""

    def test_run_code_default_is_online(self):
        tool = RunCodeTool(
            executor=lambda code: {"status": "success"},
            is_connected=lambda: True,
            actuation_flags=GATE_CLOSED,
        )
        result = tool.execute("sq.t1(q10lu8)")
        self.assertFalse(result.success)
        self.assertEqual(result.metadata.get("status"), "read_only")

    def test_run_experiment_default_is_online(self):
        tool = RunExperimentTool(
            code_builder=lambda name, params: f"sq.{name}(q10lu8, do_plot=True)",
            executor=lambda code: {"status": "success"},
            is_connected=lambda: True,
            actuation_flags=GATE_CLOSED,
        )
        result = tool.execute("t1", {"target": "q10lu8"})
        self.assertFalse(result.success)
        self.assertEqual(result.metadata.get("status"), "read_only")


def _offline_http_executor(code: str):
    """真实 HTTP：把代码 POST 给运行中的 quantum_service /execute。

    走 urllib（与 services/agent_service/adapter.py._http_call 同款），
    不额外引入依赖。
    """
    payload = _json.dumps({"code": code, "task_id": "offline_gate_probe"}).encode("utf-8")
    req = urllib.request.Request(
        f"{QUANTUM_SERVICE_URL}/execute",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def _service_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{QUANTUM_SERVICE_URL}/health", timeout=5) as resp:
            _json.loads(resp.read().decode("utf-8"))
        return True
    except Exception:
        return False


def _service_is_offline() -> bool:
    try:
        with urllib.request.urlopen(f"{QUANTUM_SERVICE_URL}/health", timeout=5) as resp:
            return _json.loads(resp.read().decode("utf-8")).get("data_source") == "offline"
    except Exception:
        return False


def _service_data_source() -> str:
    """诊断用：quantum_service 当前 data_source；不可达时返回 "<unreachable>"。"""
    try:
        with urllib.request.urlopen(f"{QUANTUM_SERVICE_URL}/health", timeout=5) as resp:
            return str(_json.loads(resp.read().decode("utf-8")).get("data_source"))
    except Exception as e:
        return f"<unreachable: {type(e).__name__}>"


@unittest.skipUnless(_service_reachable(), f"quantum_service {QUANTUM_SERVICE_URL} 未运行")
class TestRealHttpOfflineProbe(unittest.TestCase):
    """in-process 真实 HTTP 验收：闸门关 + is_offline=True 时离线实验能真跑通。

    副作用：会让 quantum_service 生成 1 个离线图（
    qmclaw-web/public/plots/offline_sim_<exp>_<ts>.png），这是被测链路的正常产物。
    """

    def test_offline_gate_closed_reaches_offline_simulation(self):
        if not _service_is_offline():
            self.skipTest(
                f"quantum_service 当前 data_source={_service_data_source()}（非 offline），"
                "跳过真实离线 HTTP 验收"
            )

        tool = RunCodeTool(
            executor=_offline_http_executor,
            is_connected=lambda: True,
            actuation_flags=GATE_CLOSED,
            is_offline=lambda: True,
        )
        result = tool.execute("sq.t1(q10lu8, do_plot=True)")

        self.assertTrue(result.success, f"离线真实 HTTP 应成功: {result.to_dict()}")
        payload = result.data.get("result") or {}
        status = payload.get("status")
        self.assertIn(
            status,
            ("offline_simulated", "success"),
            f"期望 offline_simulated，实际 status={status}, payload={payload}",
        )
        self.assertNotEqual(
            result.metadata.get("status"),
            "read_only",
            "离线时 agent 侧不应再返回 read_only",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
