# -*- coding: utf-8 -*-
r"""
agent_service 会话端点（P1-T2）单元测试 — 不依赖运行中的微服务

覆盖：
1. POST /memory/sessions  → {"success", "count", "sessions"} 结构 + limit 生效
2. GET  /memory/sessions/{sessionId} → messages 两条/轮 + episodes 完整
3. 未知 / 空会话 → {"success": false, "error": ...}（not-found 风格）
4. _record_episode 走 memory_store.create_episode 直传会话字段，磁盘实体与索引都带
   sessionId/roundIndex/model/response；_annotate_episode 已删除

隔离：EPISODES_DIR / SKILLS_DIR / INDEX_FILE 全部指到用后即删的临时目录，
绝不读写真实 config/memory/。

运行: & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_agent_sessions.py -q
"""

import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))
from services.common import memory_store
from services.agent_service.server import AgentService, AgentTask


# ── 测试基础设施 ──────────────────────────────────────────────────────────────

def _new_case_dir() -> Path:
    """新建隔离临时目录（含 episodes/ 与 skills/），用完即删。

    依次尝试系统临时目录、tests/、仓库根：本机 DSH 沙箱对部分目录写操作是
    WinError 5，换任何环境总有一个可用。全程不触碰真实 config/memory/。
    """
    candidates = [
        Path(tempfile.gettempdir()),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parents[1],
    ]
    last_err = None
    for parent in candidates:
        root = parent / f"qmclaw-agentsess-{uuid.uuid4().hex}"
        try:
            (root / "episodes").mkdir(parents=True)
            (root / "skills").mkdir()
            return root
        except OSError as e:
            last_err = e
            shutil.rmtree(root, ignore_errors=True)
    raise AssertionError(f"无法创建测试临时目录: {last_err}")


@pytest.fixture
def env(monkeypatch):
    """临时记忆目录 + 一个不联网的 AgentService 实例"""
    root = _new_case_dir()
    monkeypatch.setattr(memory_store, "EPISODES_DIR", str(root / "episodes"))
    monkeypatch.setattr(memory_store, "SKILLS_DIR", str(root / "skills"))
    monkeypatch.setattr(memory_store, "INDEX_FILE", str(root / "index.json"))
    memory_store.init_memory()
    try:
        yield AgentService(port=3005), root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _seed(session_id, rounds):
    """按 (round_index, task, response, steps) 顺序造轮次，返回 episode id 列表"""
    ids = []
    for i, (task, response, steps) in enumerate(rounds):
        ep = memory_store.create_episode(
            task, steps, "success",
            session_id=session_id, round_index=i, model="m-test", response=response,
        )
        ids.append(ep["id"])
    return ids


# ── 1. POST /memory/sessions ─────────────────────────────────────────────────

def test_post_sessions_shape_and_limit(env):
    svc, _ = env
    _seed("s1", [("t0", "r0", [{"a": 0}]), ("t1", "r1", [{"a": 1}])])
    _seed("s2", [("t2", "r2", [])])

    res = svc.handle_request("POST", "/memory/sessions", {"limit": 50}, {})
    assert res["success"] is True
    assert res["count"] == len(res["sessions"]) == 2
    assert {s["sessionId"] for s in res["sessions"]} == {"s1", "s2"}
    s1 = next(s for s in res["sessions"] if s["sessionId"] == "s1")
    assert s1["rounds"] == 2
    assert s1["taskPreview"] == "t0"
    assert s1["model"] == "m-test"

    # limit 生效
    res1 = svc.handle_request("POST", "/memory/sessions", {"limit": 1}, {})
    assert res1["success"] is True
    assert res1["count"] == 1

    # 空记忆 → 空列表也算成功
    shutil.rmtree(Path(memory_store.EPISODES_DIR))
    Path(memory_store.EPISODES_DIR).mkdir()
    memory_store.init_memory()
    empty = svc.handle_request("POST", "/memory/sessions", {}, {})
    assert empty["success"] is True and empty["count"] == 0 and empty["sessions"] == []


# ── 2. GET /memory/sessions/{id} ─────────────────────────────────────────────

def test_get_session_messages_and_episodes(env):
    svc, _ = env
    steps0 = [{"action": "step-0"}]
    steps1 = [{"action": "step-1"}]
    ids = _seed("sX", [("task zero", "resp zero", steps0), ("task one", "resp one", steps1)])

    res = svc.handle_request("GET", "/memory/sessions/sX", {}, {})
    assert res["success"] is True
    assert res["sessionId"] == "sX"
    assert [e["id"] for e in res["episodes"]] == ids        # roundIndex 升序

    msgs = res["messages"]
    assert len(msgs) == 4                                    # 每轮 2 条
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

    assert msgs[0]["content"] == "task zero"
    assert msgs[0]["timestamp"] == res["episodes"][0]["timestamp"]
    assert msgs[1]["content"] == "resp zero"
    assert msgs[1]["steps"] == steps0
    assert msgs[1]["results"] == res["episodes"][0]["metrics"]
    assert msgs[1]["timestamp"] == msgs[0]["timestamp"]

    assert msgs[2]["content"] == "task one"
    assert msgs[3]["content"] == "resp one"
    assert msgs[3]["steps"] == steps1


def test_get_unknown_session_not_found(env):
    svc, _ = env
    res = svc.handle_request("GET", "/memory/sessions/no_such_session", {}, {})
    assert res["success"] is False
    assert "no_such_session" in res["error"]


# ── 3. _record_episode 直传会话字段（旁路已收编） ─────────────────────────────

def test_record_episode_passes_session_fields(env):
    svc, _ = env
    assert not hasattr(AgentService, "_annotate_episode")   # 方法已删除

    task = AgentTask(task_id="task_abc", message="record me", mode="react")
    task.context = {"session_id": "sess_9", "round_index": 3}
    task.model = "deepseek-flash"
    task.steps = [{"action": "x"}]

    ep_id = svc._record_episode(task, {"success": True, "response": "done",
                                       "iterations": 1, "duration_ms": 5})
    assert ep_id

    on_disk = json.loads(
        (Path(memory_store.EPISODES_DIR) / f"{ep_id}.json").read_text(encoding="utf-8")
    )
    assert on_disk["sessionId"] == "sess_9"
    assert on_disk["roundIndex"] == 3
    assert on_disk["model"] == "deepseek-flash"
    assert on_disk["response"] == "done"
    assert on_disk["steps"] == [{"action": "x"}]
    # 按键断言（不再用精确等值：B3 起 metrics 还会带轮次口径键，等值断言属变更检测器）
    metrics = on_disk["metrics"]
    assert metrics["iterations"] == 1
    assert metrics["duration_ms"] == 5
    # B3 轮次口径：本用例 steps 无实验工具 → 未尝试执行，也未被拦
    assert metrics["experiment_executed"] is False
    assert metrics["blocked_reason"] is None

    # 索引条目同样带 sessionId / roundIndex
    index = json.loads(Path(memory_store.INDEX_FILE).read_text(encoding="utf-8"))
    entry = next(e for e in index["episodes"] if e["id"] == ep_id)
    assert entry["sessionId"] == "sess_9"
    assert entry["roundIndex"] == 3

    # 无 session_id 时退回 task_id（语义不变）
    task2 = AgentTask(task_id="task_def", message="record me too")
    ep2 = svc._record_episode(task2, {"success": False, "response": "nope"})
    assert memory_store.get_episode(ep2)["sessionId"] == "task_def"
    assert [e["id"] for e in memory_store.get_session_episodes("task_def")] == [ep2]
