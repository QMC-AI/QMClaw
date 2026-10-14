# -*- coding: utf-8 -*-
r"""
agent_service 会话回放链路（B2）图表落盘 / 回放单元测试 — 不依赖运行中的微服务

背景：10-13 的「会话持久 + 刷新恢复」在图表上漏了三层（落盘 / 回放 / 前端映射）。
本文件覆盖前两层的服务端契约（前端映射由 tsc 类型检查兜底）：

1. 用例 A：_record_episode 一轮带 charts 的对话后，**磁盘 episode 文件里含 charts**
   且含 dataset_id 键（string 数组契约，不是对象）。
2. 用例 B：GET /memory/sessions/{id} 回放 → assistant 消息带 charts，user 消息不带；
   与 episodes[i]["charts"] 逐项一致（回放链路口径统一）。
3. 用例 C：charts 为空 / 缺失 / 脏数据时不崩，落盘为 []、回放返回 []。
4. 用例 D（B3 口径字段）：`metrics.experiment_executed` / `metrics.blocked_reason` ——
   写闸门拦住的那一轮 status 仍是 success，但口径字段必须说清"没真执行 + 为什么"，
   避免用户看到"实验成功"却没有图。status 语义不改。

隔离：EPISODES_DIR / SKILLS_DIR / INDEX_FILE 全部指到用后即删的临时目录，
绝不读写真实 config/memory/。

运行: & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_agent_episode_charts.py -q
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


# ── 测试基础设施（照抄 tests/test_memory_sessions.py 的隔离模式）───────────────

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
        root = parent / f"qmclaw-epcharts-{uuid.uuid4().hex}"
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


OFFLINE_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
ONLINE_PLOT = "/plots/2026-10-13/t1_q10lu12.png"


def _make_task(session_id="sess_charts", round_index=0, steps=None) -> AgentTask:
    task = AgentTask(task_id="task_charts", message="跑一下 t1 实验 q10lu12", mode="react")
    task.context = {"session_id": session_id, "round_index": round_index}
    task.model = "deepseek-flash"
    task.steps = steps if steps is not None else [
        {"tool": "run_experiment", "input": {"experiment_name": "t1"}, "observation": "status=ok"},
    ]
    return task


# ── 用例 A：落盘 ─────────────────────────────────────────────────────────────

def test_record_episode_persists_charts_and_dataset_id(env):
    """带 charts 的一轮结束后，磁盘 episode 含 charts（string 数组）与 dataset_id 键"""
    svc, _ = env
    task = _make_task(steps=[
        {"tool": "run_experiment", "input": {"experiment_name": "t1"},
         "observation": "status=ok", "metadata": {"dataset_id": "ds-2026-10-13-q10lu12"}},
    ])

    ep_id = svc._record_episode(task, {
        "success": True,
        "response": "T1 = 55.6 µs",
        "iterations": 2,
        "duration_ms": 1234,
        "charts": [OFFLINE_PNG, ONLINE_PLOT],
    })
    assert ep_id

    on_disk = json.loads(
        (Path(memory_store.EPISODES_DIR) / f"{ep_id}.json").read_text(encoding="utf-8")
    )
    # 图表自包含落盘，且仍是 string 数组（契约不变）
    assert on_disk["charts"] == [OFFLINE_PNG, ONLINE_PLOT]
    assert all(isinstance(c, str) for c in on_disk["charts"])
    # dataset_id 键存在（本轮 steps 的 metadata 里有 → 取到真值）
    assert "dataset_id" in on_disk
    assert on_disk["dataset_id"] == "ds-2026-10-13-q10lu12"
    # 主干字段没被补写破坏
    assert on_disk["response"] == "T1 = 55.6 µs"
    assert on_disk["sessionId"] == "sess_charts"
    # metrics 追加了本轮"有没有真执行"的口径键，原有 iterations/duration_ms 不动
    assert on_disk["metrics"] == {
        "iterations": 2, "duration_ms": 1234,
        "experiment_executed": True, "blocked_reason": None,
    }

    # memory_store 读回口径一致（回放读的就是这个接口）
    assert memory_store.get_episode(ep_id)["charts"] == [OFFLINE_PNG, ONLINE_PLOT]


def test_dataset_id_backfill_is_optional_and_never_raises(env):
    """steps 里捞不到 dataset_id 时：键存在、值为 None，不抛异常"""
    svc, _ = env
    task = _make_task(steps=[{"tool": "get_qubits", "input": {}, "observation": "返回 3 项"}])
    ep_id = svc._record_episode(task, {"success": True, "response": "ok", "charts": [ONLINE_PLOT]})

    on_disk = json.loads(
        (Path(memory_store.EPISODES_DIR) / f"{ep_id}.json").read_text(encoding="utf-8")
    )
    assert "dataset_id" in on_disk
    assert on_disk["dataset_id"] is None
    assert on_disk["charts"] == [ONLINE_PLOT]

    # 显式传入的 dataset_id 优先于 steps 扫描
    ep_id2 = svc._record_episode(
        _make_task(session_id="sess_charts", round_index=1),
        {"success": True, "response": "ok", "charts": [ONLINE_PLOT], "dataset_id": "ds-explicit"},
    )
    assert memory_store.get_episode(ep_id2)["dataset_id"] == "ds-explicit"


# ── 用例 B：回放 ─────────────────────────────────────────────────────────────

def test_session_replay_carries_charts(env):
    """GET /memory/sessions/{id} → assistant 消息带 charts，user 消息不带"""
    svc, _ = env
    ep0 = svc._record_episode(_make_task(round_index=0), {
        "success": True, "response": "r0", "charts": [OFFLINE_PNG],
    })
    ep1 = svc._record_episode(_make_task(round_index=1), {
        "success": True, "response": "r1", "charts": [ONLINE_PLOT, OFFLINE_PNG],
    })
    assert ep0 and ep1

    res = svc.handle_request("GET", "/memory/sessions/sess_charts", {}, {})
    assert res["success"] is True
    msgs = res["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

    # assistant 带 charts，且与 episodes 里的一致（回放链路口径统一）
    assert msgs[1]["charts"] == [OFFLINE_PNG]
    assert msgs[3]["charts"] == [ONLINE_PLOT, OFFLINE_PNG]
    assert msgs[1]["charts"] == res["episodes"][0]["charts"]
    assert msgs[3]["charts"] == res["episodes"][1]["charts"]
    # user 消息不加 charts（与线上契约一致）
    assert "charts" not in msgs[0]
    assert "charts" not in msgs[2]


def test_session_replay_legacy_episode_without_charts_key(env):
    """老数据（episode 无 charts 键）回放 → assistant 消息 charts 为 []，不报错"""
    svc, _ = env
    legacy = {
        "id": "ep_legacy_no_charts",
        "timestamp": "2026-01-01T10:00:00",
        "task": "老数据一轮",
        "qubit": None,
        "mode": "react",
        "steps": [],
        "status": "success",
        "metrics": {},
        "tags": [],
        "usedSkills": [],
        "reflection": None,
        "skillCreated": None,
        "archived": False,
        "sessionId": "sess_legacy",
        "roundIndex": 0,
        "model": "m0",
        "response": "老回复",
    }
    assert "charts" not in legacy
    (Path(memory_store.EPISODES_DIR) / "ep_legacy_no_charts.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )
    memory_store._update_index(
        lambda index: index["episodes"].insert(0, {
            "id": "ep_legacy_no_charts", "timestamp": legacy["timestamp"],
            "qubit": None, "status": "success",
            "sessionId": "sess_legacy", "roundIndex": 0, "task_preview": legacy["task"],
        })
    )

    res = svc.handle_request("GET", "/memory/sessions/sess_legacy", {}, {})
    assert res["success"] is True
    assert res["messages"][1]["charts"] == []


# ── 用例 C：空 / 脏 charts 不崩 ──────────────────────────────────────────────

def test_empty_and_dirty_charts_do_not_break(env):
    """charts 缺失 / 空列表 / 脏元素 → 落盘 [] 或已过滤，回放一律返回 []"""
    svc, _ = env

    # 缺失（失败轮次走的就是这条路径：payload 里没有 charts 键）
    ep_a = svc._record_episode(_make_task(round_index=0), {"success": False, "error": "boom"})
    assert memory_store.get_episode(ep_a)["charts"] == []

    # 空列表
    ep_b = svc._record_episode(_make_task(round_index=1), {"success": True, "charts": []})
    assert memory_store.get_episode(ep_b)["charts"] == []

    # 脏元素（None / 数字 / dict）被过滤，只留字符串
    ep_c = svc._record_episode(
        _make_task(round_index=2),
        {"success": True, "charts": [None, 42, {"path": "x"}, ONLINE_PLOT]},
    )
    assert memory_store.get_episode(ep_c)["charts"] == [ONLINE_PLOT]

    # 整轮 charts 非列表（脏数据）也不能抛
    ep_d = svc._record_episode(_make_task(round_index=3), {"success": True, "charts": "oops"})
    assert memory_store.get_episode(ep_d)["charts"] == []

    # 回放：无论上面哪种，assistant 消息都返回 list（空则为 []），不崩
    res = svc.handle_request("GET", "/memory/sessions/sess_charts", {}, {})
    assert res["success"] is True
    assistants = [m for m in res["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 4
    for m in assistants:
        assert isinstance(m["charts"], list)
    assert assistants[0]["charts"] == []
    assert assistants[1]["charts"] == []
    assert assistants[2]["charts"] == [ONLINE_PLOT]
    assert assistants[3]["charts"] == []


# ── 用例 D（B3）：本轮实验到底有没有真的执行 ─────────────────────────────────
#
# 背景：写闸门关闭时，agent 侧的 run_experiment 在调用服务前就被拒（FORBIDDEN /
# status="read_only"），但该轮 agent 轮次确实成功结束了 -> status="success"，
# metrics 只有 iterations/duration_ms，用户看到"实验成功"却没有图。
# 现在把"有没有真执行"单独记进 metrics，**status 语义一字未改**。

BLOCKED_OBS = "失败: 当前为只读联调，禁止向真机下发实验"
OFFLINE_OBS = 'status=offline_simulated | (离线模拟) | metrics={"t1": 5.56e-05}'
ONLINE_OBS = 'status=success | metrics={"t1": 5.56e-05}'


def _episode_on_disk(ep_id) -> dict:
    return json.loads(
        (Path(memory_store.EPISODES_DIR) / f"{ep_id}.json").read_text(encoding="utf-8")
    )


def test_blocked_round_is_not_executed_but_status_stays_success(env):
    """a) 被写闸门拦下的一轮：executed=False / blocked_reason="FORBIDDEN"，status 仍 success"""
    svc, _ = env
    task = _make_task(steps=[
        {"tool": "run_experiment", "input": {"experiment_name": "t1", "target": "q10lu12"},
         "observation": BLOCKED_OBS},
    ])

    ep_id = svc._record_episode(task, {
        "success": True, "response": "当前为只读联调，未下发实验", "charts": [],
        "iterations": 1, "duration_ms": 800,
    })
    on_disk = _episode_on_disk(ep_id)

    # 口径字段说清了"没真执行 + 为什么"
    assert on_disk["metrics"]["experiment_executed"] is False
    assert on_disk["metrics"]["blocked_reason"] == "FORBIDDEN"
    # 「不改 status 语义」：agent 轮次成功结束就还是 success
    assert on_disk["status"] == "success"
    # 既有键原样保留
    assert on_disk["metrics"]["iterations"] == 1
    assert on_disk["metrics"]["duration_ms"] == 800
    assert on_disk["charts"] == []


def test_successful_offline_round_is_marked_executed(env):
    """b) 离线成功的一轮：executed=True / blocked_reason=None"""
    svc, _ = env
    for i, obs in enumerate((ONLINE_OBS, OFFLINE_OBS)):
        task = _make_task(round_index=i, steps=[
            {"tool": "run_experiment", "input": {"experiment_name": "t1"}, "observation": obs},
        ])
        ep_id = svc._record_episode(task, {
            "success": True, "response": "T1 = 55.6 µs", "charts": [OFFLINE_PNG],
            "iterations": 3, "duration_ms": 900,
        })
        m = _episode_on_disk(ep_id)["metrics"]
        assert m["experiment_executed"] is True
        assert m["blocked_reason"] is None
        assert m["iterations"] == 3 and m["duration_ms"] == 900

    # 没有图表但 observation 明确说成功 -> 仍算执行过（成功迹象不只看图）
    ep_id = svc._record_episode(_make_task(round_index=2, steps=[
        {"tool": "run_experiment", "input": {}, "observation": OFFLINE_OBS},
    ]), {"success": True, "response": "ok", "charts": []})
    assert _episode_on_disk(ep_id)["metrics"]["experiment_executed"] is True


def test_chat_only_round_is_not_executed_and_not_blocked(env):
    """c) 纯聊天轮次（无实验工具）：(False, None) —— 没尝试，不算被拦"""
    svc, _ = env
    task = _make_task(steps=[
        {"tool": "get_qubits", "input": {}, "observation": "返回 3 项"},
        {"tool": "get_memory_stats", "input": {}, "observation": "episodes=12"},
    ])
    ep_id = svc._record_episode(task, {"success": True, "response": "有 3 个 qubit"})
    m = _episode_on_disk(ep_id)["metrics"]

    assert m["experiment_executed"] is False
    assert m["blocked_reason"] is None
    # 纯函数口径一致
    assert AgentService._experiment_outcome(task.steps, []) == (False, None)

    # 只读查询（list_/get_）不算"尝试下发实验"：既没执行也没被拦 -> (False, None)。
    # 收紧前用子串 "experiment" 匹配，list_experiments 会被误判成实验类步骤而落到
    # UNKNOWN，误导用户以为"试过但说不清"。
    assert AgentService._experiment_outcome(
        [{"tool": "list_experiments", "observation": "返回 1 项"}], []) == (False, None)
    assert AgentService._experiment_outcome(
        [{"tool": "get_experiment_schema", "observation": "返回 t1/rabi"}], []) == (False, None)


def test_dirty_steps_never_raise(env):
    """d) 脏数据：steps 非 list / 元素非 dict / observation 非 str -> 不抛异常"""
    svc, _ = env

    # 纯函数：各种脏 shape 只要求"不抛异常 + 形状合法"
    dirty = [
        (None, None),
        ("oops", None),
        ({"tool": "run_experiment"}, "oops"),
        ([None, 42, "x", [], {"tool": 123}, {"no_tool": 1}], None),
        ([{"tool": "run_experiment", "observation": None}], 0),
        ([{"tool": "run_experiment", "observation": {"status": "success"}}], {"a": 1}),
        ([{"tool": "run_experiment", "observation": BLOCKED_OBS}], "oops"),
        ([{"tool": "run_code", "observation": ["x"]}], [None, 42]),
    ]
    for steps, charts in dirty:
        out = AgentService._experiment_outcome(steps, charts)
        assert isinstance(out, tuple) and len(out) == 2
        executed, reason = out
        assert isinstance(executed, bool)
        assert reason in (None, "FORBIDDEN", "UNKNOWN")
        # 脏 charts 绝不能被当成"执行成功"
        if executed is True and not isinstance(charts, (list, tuple)):
            raise AssertionError(f"charts={charts!r} 不该被判为已执行")

    # 有尝试但两个迹象都没命中 -> UNKNOWN（保守，不冒充成功）
    assert AgentService._experiment_outcome(
        [{"tool": "run_experiment", "observation": "ok"}], []) == (False, "UNKNOWN")
    # 被拦迹象 -> FORBIDDEN；且脏 charts 不覆盖被拦判定
    assert AgentService._experiment_outcome(
        [{"tool": "run_experiment", "observation": BLOCKED_OBS}], None) == (False, "FORBIDDEN")

    # 落盘路径：脏 steps 也不许把 _record_episode 带崩
    dirty_task = _make_task()
    dirty_task.steps = "oops"                       # steps 非 list
    ep_id = svc._record_episode(dirty_task, {"success": True, "response": "ok", "charts": "oops"})
    assert ep_id
    on_disk = _episode_on_disk(ep_id)
    assert on_disk["metrics"]["experiment_executed"] is False
    assert on_disk["metrics"]["blocked_reason"] is None
    assert on_disk["charts"] == []

    # observation 非 str 的混合元素
    dirty_task2 = _make_task(round_index=1)
    dirty_task2.steps = [None, 42, {"tool": "run_experiment", "observation": 3.14}]
    ep_id2 = svc._record_episode(dirty_task2, {"success": True, "response": "ok"})
    assert _episode_on_disk(ep_id2)["metrics"]["blocked_reason"] == "UNKNOWN"

