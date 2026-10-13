# -*- coding: utf-8 -*-
r"""
memory_store 会话（session）能力单元测试 — 不依赖运行中的微服务

覆盖（P1-T1 契约）：
1. create_episode 的 sessionId / roundIndex / model / response 字段（实体+索引）
2. 多轮分组（1 个会话 3 轮）与摘要字段口径
3. get_session_episodes 排序：roundIndex 全非 null 按 roundIndex，否则按 timestamp
4. 空 task / 纯空白 task / 非字符串 task 的脏数据过滤（list_sessions 与
   get_session_episodes 同口径，rounds == len(episodes)）
5. 老数据退化：sessionId 缺失 / null 的 episode 各自成单轮会话
6. limit 截断与 lastAt 倒序
7. update_episode_fields 合并字段、id 不存在返回 False
8. 10 线程并发 create_episode 后 index 条目一个不少（_INDEX_LOCK）

隔离：每个测试把 EPISODES_DIR / SKILLS_DIR / INDEX_FILE 全部指到用后即删的临时目录，
绝不读写真实 config/memory/。

运行: & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_memory_sessions.py -q
"""

import json
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))
from services.common import memory_store


# ── 测试基础设施 ──────────────────────────────────────────────────────────────

def _new_case_dir() -> Path:
    """新建一个隔离用的临时目录（含 episodes/ 与 skills/ 子目录），用完即删。

    依次尝试系统临时目录、tests/、仓库根：本机 DSH 沙箱对 %TEMP% 的二级目录和
    tests/ 下的新目录一律 WinError 5，会一路退到仓库根；换任何环境总有一个可用。
    全程不触碰真实 config/memory/。
    """
    candidates = [
        Path(tempfile.gettempdir()),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parents[1],
    ]
    last_err = None
    for parent in candidates:
        root = parent / f"qmclaw-memtest-{uuid.uuid4().hex}"
        try:
            (root / "episodes").mkdir(parents=True)
            (root / "skills").mkdir()
            return root
        except OSError as e:
            last_err = e
            shutil.rmtree(root, ignore_errors=True)
    raise AssertionError(f"无法创建测试临时目录: {last_err}")


@pytest.fixture
def mem(monkeypatch):
    """把 memory_store 的三个路径全部指到临时目录（真实 config/memory/ 不可触碰）"""
    root = _new_case_dir()
    monkeypatch.setattr(memory_store, "EPISODES_DIR", str(root / "episodes"))
    monkeypatch.setattr(memory_store, "SKILLS_DIR", str(root / "skills"))
    monkeypatch.setattr(memory_store, "INDEX_FILE", str(root / "index.json"))
    memory_store.init_memory()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def write_raw_episode(
    ep_id,
    timestamp,
    task,
    status="success",
    qubit=None,
    session_id=None,
    round_index=None,
    model=None,
    response=None,
    steps=None,
    with_session_key=True,
):
    """直接落盘一个 episode 实体 + 索引条目（可模拟新旧两种历史数据形态）。

    with_session_key=False 时实体/索引都不带 sessionId/roundIndex 等键，
    即"分级记忆"上线前的老数据形态。
    """
    ep = {
        "id": ep_id,
        "timestamp": timestamp,
        "task": task,
        "qubit": qubit,
        "mode": "react",
        "steps": steps if steps is not None else [{"action": f"step-{ep_id}"}],
        "status": status,
        "metrics": {},
        "tags": [],
        "usedSkills": [],
        "reflection": None,
        "skillCreated": None,
        "archived": False,
    }
    if with_session_key:
        ep["sessionId"] = session_id
        ep["roundIndex"] = round_index
        ep["model"] = model
        ep["response"] = response

    with open(Path(memory_store.EPISODES_DIR) / f"{ep_id}.json", "w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False)

    entry = {
        "id": ep_id,
        "timestamp": timestamp,
        "qubit": qubit,
        "status": status,
        "task_preview": task[:50] if isinstance(task, str) else task,
    }
    if with_session_key:
        entry["sessionId"] = session_id
        entry["roundIndex"] = round_index

    def _append(index):
        index["episodes"].append(entry)

    memory_store._update_index(_append)
    return ep


# ── 1. create_episode 字段形状 ────────────────────────────────────────────────

def test_create_episode_session_fields_shape(mem):
    ep = memory_store.create_episode(
        "shape check", [{"a": 1}], "success",
        session_id="s0", round_index=0, model="m0", response="resp-0",
    )
    # 实体：4 个新字段即使 None 也写入（这里都非 None）
    assert ep["sessionId"] == "s0"
    assert ep["roundIndex"] == 0
    assert ep["model"] == "m0"
    assert ep["response"] == "resp-0"

    ep_none = memory_store.create_episode("no session", [], "failed")
    assert {"sessionId", "roundIndex", "model", "response"} <= set(ep_none.keys())
    assert ep_none["sessionId"] is None
    assert ep_none["roundIndex"] is None
    assert ep_none["model"] is None
    assert ep_none["response"] is None

    # 索引条目：新增 sessionId / roundIndex 两个键
    index = json.loads(Path(memory_store.INDEX_FILE).read_text(encoding="utf-8"))
    assert len(index["episodes"]) == 2
    for entry in index["episodes"]:
        assert {"sessionId", "roundIndex"} <= set(entry.keys())
    assert index["episodes"][0]["sessionId"] is None  # insert(0)，最新在前


# ── 2. 多轮分组（1 会话 3 轮） ───────────────────────────────────────────────

def test_multi_round_grouping(mem):
    write_raw_episode("ep_a", "2026-01-01T10:00:00", "first task do something",
                      status="success", qubit=None, session_id="s1",
                      round_index=0, model="m1", response="r1")
    write_raw_episode("ep_b", "2026-01-02T10:00:00", "second task",
                      status="failed", qubit="q-17", session_id="s1",
                      round_index=1, model="m2", response="r2")
    write_raw_episode("ep_c", "2026-01-03T10:00:00", "third task",
                      status="success", qubit="q-17", session_id="s1",
                      round_index=2, model="m3", response="r3")

    sessions = memory_store.list_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["sessionId"] == "s1"
    assert s["rounds"] == 3
    assert s["firstAt"] == "2026-01-01T10:00:00"
    assert s["lastAt"] == "2026-01-03T10:00:00"
    assert s["taskPreview"] == "first task do something"   # 最早一轮 task，截 50 字符
    assert s["qubit"] == "q-17"                            # 组内第一个非空 qubit
    assert s["model"] == "m3"                              # 最新一轮的 model
    assert s["status"] == "mixed"                          # success/failed 混合

    # 完整 episode（含 steps / response）可取回
    full = memory_store.get_session_episodes("s1")
    assert [e["id"] for e in full] == ["ep_a", "ep_b", "ep_c"]
    assert full[0]["steps"] == [{"action": "step-ep_a"}]
    assert full[0]["response"] == "r1"


def test_task_preview_truncated_to_50(mem):
    long_task = "x" * 80
    write_raw_episode("ep_long", "2026-01-05T10:00:00", long_task, session_id="sL")
    s = memory_store.list_sessions()[0]
    assert s["taskPreview"] == "x" * 50


# ── 3. roundIndex 排序 + 全 null 时 timestamp 排序 ───────────────────────────

def test_get_session_episodes_ordering(mem):
    # roundIndex 全部非 null → 按 roundIndex 升序（故意把 timestamp 写成相反顺序）
    write_raw_episode("ep_r2", "2026-02-03T10:00:00", "r2", session_id="sR", round_index=2)
    write_raw_episode("ep_r0", "2026-02-01T10:00:00", "r0", session_id="sR", round_index=0)
    write_raw_episode("ep_r1", "2026-02-02T10:00:00", "r1", session_id="sR", round_index=1)
    assert [e["id"] for e in memory_store.get_session_episodes("sR")] == \
        ["ep_r0", "ep_r1", "ep_r2"]

    # roundIndex 全部为 null → 按 timestamp 升序
    write_raw_episode("ep_tB", "2026-03-02T10:00:00", "tB", session_id="sT", round_index=None)
    write_raw_episode("ep_tA", "2026-03-01T10:00:00", "tA", session_id="sT", round_index=None)
    write_raw_episode("ep_tC", "2026-03-03T10:00:00", "tC", session_id="sT", round_index=None)
    assert [e["id"] for e in memory_store.get_session_episodes("sT")] == \
        ["ep_tA", "ep_tB", "ep_tC"]

    # 混有 null → 也按 timestamp 升序（规则确定，不做混合插入排序）
    write_raw_episode("ep_mB", "2026-04-02T10:00:00", "mB", session_id="sM", round_index=5)
    write_raw_episode("ep_mA", "2026-04-01T10:00:00", "mA", session_id="sM", round_index=None)
    assert [e["id"] for e in memory_store.get_session_episodes("sM")] == ["ep_mA", "ep_mB"]


# ── 4. 空 task 过滤 ──────────────────────────────────────────────────────────

def test_empty_task_episodes_filtered(mem):
    write_raw_episode("ep_blank", "2026-05-01T10:00:00", "", session_id="sE", round_index=0)
    write_raw_episode("ep_ws", "2026-05-02T10:00:00", "   ", session_id="sE", round_index=1)
    write_raw_episode("ep_good", "2026-05-03T10:00:00", "real task", session_id="sE", round_index=2)
    # 无 sessionId 且空 task → 整个退化会话都不该出现
    write_raw_episode("ep_dirty", "2026-05-04T10:00:00", "  ", session_id=None, round_index=None)

    sessions = memory_store.list_sessions()
    assert [s["sessionId"] for s in sessions] == ["sE"]
    s = sessions[0]
    assert s["rounds"] == 1
    assert s["firstAt"] == s["lastAt"] == "2026-05-03T10:00:00"
    assert s["taskPreview"] == "real task"
    assert s["status"] == "success"

    # 契约语义：会话回放（get_session_episodes）同样过滤空 task，口径与 list_sessions 一致
    full = memory_store.get_session_episodes("sE")
    assert [e["id"] for e in full] == ["ep_good"]
    assert s["rounds"] == len(full)          # rounds == len(episodes) 不变量
    # 退化脏会话（sessionId=None 且空 task）按自身 id 也取不回
    assert memory_store.get_session_episodes("ep_dirty") == []


def test_get_session_episodes_filters_empty_task_and_keeps_rounds_consistent(mem):
    """含空 task / 纯空白 / 非字符串 task 的会话：回放不返回它们，且 rounds 一致。"""
    write_raw_episode("ep_ok0", "2026-08-01T10:00:00", "round zero",
                      session_id="sF", round_index=0, model="m0", response="r0")
    write_raw_episode("ep_none", "2026-08-02T10:00:00", None, session_id="sF", round_index=1)
    write_raw_episode("ep_ok1", "2026-08-03T10:00:00", "  round one  ",
                      session_id="sF", round_index=2, model="m2", response="r2")

    full = memory_store.get_session_episodes("sF")
    assert [e["id"] for e in full] == ["ep_ok0", "ep_ok1"]
    # 排序仍按 roundIndex 升序，完整字段（steps / response / model）未受影响
    assert full[0]["steps"] == [{"action": "step-ep_ok0"}]
    assert full[0]["response"] == "r0"
    assert full[1]["model"] == "m2"

    sessions = memory_store.list_sessions()
    assert [s["sessionId"] for s in sessions] == ["sF"]
    assert sessions[0]["rounds"] == 2
    assert sessions[0]["rounds"] == len(full)
    assert sessions[0]["lastAt"] == "2026-08-03T10:00:00"


# ── 5. 老数据退化（sessionId 缺失各自成会话） ────────────────────────────────

def test_legacy_without_session_id_degrades_to_single_round_sessions(mem):
    write_raw_episode("ep_old1", "2026-06-01T10:00:00", "old one", with_session_key=False)
    write_raw_episode("ep_old2", "2026-06-02T10:00:00", "old two", with_session_key=False)
    write_raw_episode("ep_null", "2026-06-03T10:00:00", "null sid",
                      session_id=None, round_index=None)

    sessions = memory_store.list_sessions()
    # 各自构成单轮会话，sessionId 用 episode 自己的 id；按 lastAt 倒序
    assert [(s["sessionId"], s["rounds"]) for s in sessions] == \
        [("ep_null", 1), ("ep_old2", 1), ("ep_old1", 1)]
    for s in sessions:
        assert s["firstAt"] == s["lastAt"]
        assert s["model"] is None

    # 退化会话也能按 episode id 取回完整记录
    assert [e["id"] for e in memory_store.get_session_episodes("ep_old1")] == ["ep_old1"]
    assert memory_store.get_session_episodes("no_such_session") == []


# ── 6. limit 截断与 lastAt 倒序 ──────────────────────────────────────────────

def test_limit_and_lastat_desc(mem):
    write_raw_episode("a1", "2026-07-01T10:00:00", "a1", status="success",
                      session_id="sA", round_index=0, model="ma0")
    write_raw_episode("a2", "2026-07-02T10:00:00", "a2", status="success",
                      session_id="sA", round_index=1, model="ma1")
    # 起点早、终点晚
    write_raw_episode("b1", "2026-06-30T10:00:00", "b1", status="failed",
                      session_id="sB", round_index=0, model="mb0")
    write_raw_episode("b2", "2026-07-05T10:00:00", "b2", status="failed",
                      session_id="sB", round_index=1, model="mb1")
    write_raw_episode("c1", "2026-07-03T10:00:00", "c1", status="success",
                      session_id="sC", round_index=0, model="mc0")

    sessions = memory_store.list_sessions()
    assert [s["sessionId"] for s in sessions] == ["sB", "sC", "sA"]   # lastAt 倒序
    by_id = {s["sessionId"]: s for s in sessions}

    assert by_id["sB"]["rounds"] == 2
    assert by_id["sB"]["firstAt"] == "2026-06-30T10:00:00"
    assert by_id["sB"]["lastAt"] == "2026-07-05T10:00:00"
    assert by_id["sB"]["model"] == "mb1"        # 最新一轮的 model
    assert by_id["sB"]["status"] == "failed"    # 同为 failed → 取最新一轮 status
    assert by_id["sA"]["status"] == "success"

    assert [s["sessionId"] for s in memory_store.list_sessions(limit=2)] == ["sB", "sC"]
    assert [s["sessionId"] for s in memory_store.list_sessions(limit=1)] == ["sB"]
    assert memory_store.list_sessions(limit=0) == []
    assert len(memory_store.list_sessions(limit=99)) == 3


# ── 7. update_episode_fields ─────────────────────────────────────────────────

def test_update_episode_fields_merges_and_handles_missing(mem):
    ep = memory_store.create_episode(
        "merge me", [{"s": 1}], "success",
        session_id="sU", round_index=0, model="m0",
    )
    ok = memory_store.update_episode_fields(
        ep["id"], model="m1", response="done", reflection={"note": "x"},
    )
    assert ok is True

    got = memory_store.get_episode(ep["id"])
    assert got["model"] == "m1"
    assert got["response"] == "done"
    assert got["reflection"] == {"note": "x"}
    # 未提及的字段保持不变（是合并而不是覆盖整个实体）
    assert got["task"] == "merge me"
    assert got["sessionId"] == "sU"
    assert got["roundIndex"] == 0
    assert got["steps"] == [{"s": 1}]
    assert got["status"] == "success"

    # id 不存在（文件不存在）→ False
    assert memory_store.update_episode_fields("ep_does_not_exist", model="m2") is False
    assert memory_store.get_episode("ep_does_not_exist") is None


# ── 8. 并发 create_episode（_INDEX_LOCK） ────────────────────────────────────

def test_concurrent_create_episode_keeps_all_index_entries(mem):
    n = 10
    errors = []

    def worker(i):
        try:
            memory_store.create_episode(
                f"concurrent {i}", [{"i": i}], "success",
                session_id=f"s{i}", round_index=i, model="m", response=f"r{i}",
            )
        except Exception as exc:  # noqa: BLE001 - 测试里收集线程异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors

    index = json.loads(Path(memory_store.INDEX_FILE).read_text(encoding="utf-8"))
    entries = index["episodes"]
    assert len(entries) == n                     # 索引条目一个不少
    ids = {e["id"] for e in entries}
    assert len(ids) == n                         # 无重复 id
    for eid in ids:
        assert (Path(memory_store.EPISODES_DIR) / f"{eid}.json").is_file()
    # 会话字段也没在并发下丢失
    assert {e.get("sessionId") for e in entries} == {f"s{i}" for i in range(n)}
