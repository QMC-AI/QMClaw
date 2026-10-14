# -*- coding: utf-8 -*-
r"""
memory_store 生命周期（提升 / 遗忘）单元测试 — 不依赖运行中的微服务

覆盖（P4-T7 + G3）：
1. 老数据（无 lifecycle / promoted_to / archived）读取按 active / False / None 兼容
2. 超期归档：archive_after_days=90 正常归档，archive_after_days=0 边界全归档
3. 超量 prune：实体文件与索引条目一起删除、orphanCount == 0、pruned 累计计数
4. 提升链：成功任务 + 反思报告确实产出技能 → 回标 episode.promoted_to
5. 失败任务不升技能（只反思）；成功但报告没产出技能/步骤也不升
6. G3：scripts/memory_store.py 是转发层（与权威实现同一批函数对象，无第二份实现），
   legacy `from memory_store import create_episode` 的写入-读取往返一致

隔离：每个测试把 EPISODES_DIR / SKILLS_DIR / INDEX_FILE 全部指到用后即删的临时目录，
绝不读写真实 config/memory/。

运行: & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_memory_lifecycle.py -q
"""

import importlib
import json
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))
from services.common import memory_store  # noqa: E402
from services.common import reflection_engine  # noqa: E402


# ── 测试基础设施（与 test_memory_sessions.py 同一套隔离方式） ─────────────────

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
    task=None,
    status="success",
    with_lifecycle=False,
    lifecycle=None,
    with_archived=True,
    archived=False,
    archived_at=None,
):
    """直接落盘一个 episode 实体 + 索引条目（可模拟老数据 / 已归档数据）。

    - with_lifecycle=False（默认）→ 实体不写 lifecycle，即"生命周期上线前"的老数据形态
    - with_archived=False        → 连 archived 键都不写（更老 / 更脏的形态）
    - archived_at                → 预置 archivedAt（prune 排序用）
    """
    ep = {
        "id": ep_id,
        "timestamp": timestamp,
        "task": task if task is not None else f"task {ep_id}",
        "qubit": None,
        "mode": "react",
        "steps": [{"action": f"step-{ep_id}"}],
        "status": status,
        "metrics": {},
        "tags": [],
        "usedSkills": [],
        "reflection": None,
        "skillCreated": None,
    }
    if with_archived:
        ep["archived"] = archived
    if with_lifecycle or lifecycle is not None:
        ep["lifecycle"] = lifecycle or ("archived" if archived else "active")
    if archived_at is not None:
        ep["archivedAt"] = archived_at

    with open(Path(memory_store.EPISODES_DIR) / f"{ep_id}.json", "w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False)

    def _append(index):
        index["episodes"].append({
            "id": ep_id,
            "timestamp": timestamp,
            "qubit": None,
            "status": status,
            "task_preview": ep["task"][:50],
        })

    memory_store._update_index(_append)
    return ep


# ── 1. 老数据兼容 ────────────────────────────────────────────────────────────

def test_legacy_episode_without_lifecycle_reads_as_active(mem):
    write_raw_episode("ep_old_bare", datetime.now().isoformat(), with_archived=False)
    write_raw_episode("ep_old_plain", datetime.now().isoformat())
    write_raw_episode("ep_old_archived", datetime.now().isoformat(), archived=True)

    bare = memory_store.get_episode("ep_old_bare")
    assert bare["lifecycle"] == "active"      # 两个字段都没有 → 按 active
    assert bare["archived"] is False
    assert bare["promoted_to"] is None        # 提升字段缺失 → None

    plain = memory_store.get_episode("ep_old_plain")
    assert plain["lifecycle"] == "active"
    assert plain["archived"] is False
    assert plain["promoted_to"] is None

    # 老数据的 archived=True（无 lifecycle）→ 推断为 archived，两字段同步
    arch = memory_store.get_episode("ep_old_archived")
    assert arch["lifecycle"] == "archived"
    assert arch["archived"] is True
    assert arch["promoted_to"] is None

    # stats 口径：老数据无 lifecycle 全部按 active 计
    assert memory_store.get_memory_stats()["lifecycle"] == {
        "active": 2, "archived": 1, "pruned": 0}

    # 新建 episode 一定带上两个新字段
    fresh = memory_store.create_episode("new one", [{"a": 1}], "success")
    assert fresh["lifecycle"] == "active"
    assert fresh["promoted_to"] is None


# ── 2. 超期归档（含 archive_after_days=0 边界） ──────────────────────────────

def test_archive_overdue_episodes_and_zero_day_boundary(mem):
    now = datetime.now()
    write_raw_episode("ep_ancient", (now - timedelta(days=200)).isoformat())
    write_raw_episode("ep_overdue", (now - timedelta(days=91)).isoformat())
    write_raw_episode("ep_fresh", (now - timedelta(days=10)).isoformat())
    write_raw_episode("ep_now", now.isoformat())

    r = memory_store.run_maintenance(archive_after_days=90, max_archived=200)
    assert r == {"archived": 2, "pruned": 0, "active": 2, "archived_remaining": 2}

    for eid in ("ep_ancient", "ep_overdue"):
        ep = memory_store.get_episode(eid)
        assert ep["lifecycle"] == "archived"
        assert ep["archived"] is True          # 两字段同步
        assert ep["archivedAt"]                # 归档时间落盘
    for eid in ("ep_fresh", "ep_now"):
        assert memory_store.get_episode(eid)["lifecycle"] == "active"

    # 归档后索引条目移除（沿用 archive_episode 语义）
    index = json.loads(Path(memory_store.INDEX_FILE).read_text(encoding="utf-8"))
    assert sorted(e["id"] for e in index["episodes"]) == ["ep_fresh", "ep_now"]
    assert memory_store.get_memory_stats()["lifecycle"] == {
        "active": 2, "archived": 2, "pruned": 0}

    # 边界：archive_after_days=0 → 剩余 active 全部立刻归档
    r2 = memory_store.run_maintenance(archive_after_days=0, max_archived=200)
    assert r2 == {"archived": 2, "pruned": 0, "active": 0, "archived_remaining": 4}
    assert memory_store.get_memory_stats()["lifecycle"] == {
        "active": 0, "archived": 4, "pruned": 0}

    # 再跑一次：没有 active 了，幂等
    r3 = memory_store.run_maintenance(archive_after_days=0)
    assert r3["archived"] == 0
    assert r3["active"] == 0
    assert r3["archived_remaining"] == 4


# ── 3. 超量 prune：文件 + 索引一起消失，不留孤儿 ─────────────────────────────

def test_prune_removes_entity_and_index_entry_leaving_no_orphan(mem):
    for i in range(1, 6):
        write_raw_episode(
            f"ep_arch{i}", "2026-01-01T00:00:00",
            lifecycle="archived", archived=True,
            archived_at=f"2026-01-0{i}T00:00:00",
        )
    assert memory_store.get_memory_stats()["lifecycle"]["archived"] == 5

    # archive_after_days 很大 → 本次不新归档；max_archived=2 → 删最老的 3 条
    r = memory_store.run_maintenance(archive_after_days=36500, max_archived=2)
    assert r == {"archived": 0, "pruned": 3, "active": 0, "archived_remaining": 2}

    episodes_dir = Path(memory_store.EPISODES_DIR)
    assert sorted(p.stem for p in episodes_dir.glob("*.json")) == ["ep_arch4", "ep_arch5"]

    index = json.loads(Path(memory_store.INDEX_FILE).read_text(encoding="utf-8"))
    assert sorted(e["id"] for e in index["episodes"]) == ["ep_arch4", "ep_arch5"]
    assert index["meta"]["pruned_total"] == 3          # 累计删除计数落在 index.meta

    stats = memory_store.get_memory_stats()
    assert stats["orphanCount"] == 0                   # 索引无残行
    assert stats["episodeCount"] == 2
    assert stats["lifecycle"] == {"active": 0, "archived": 2, "pruned": 3}

    # 归档数已在上限内 → 再跑不删，累计计数不变
    r2 = memory_store.run_maintenance(archive_after_days=36500, max_archived=2)
    assert r2["pruned"] == 0
    assert memory_store.get_memory_stats()["lifecycle"]["pruned"] == 3


# ── 4/5. 提升链（回标 promoted_to / 失败不升技能） ───────────────────────────

def _success_report():
    return {
        "summary": "T1 测量成功",
        "quality": 95,
        "successFactors": ["参数合适"],
        "failureFactors": [],
        "lessons": ["多次数平均降噪"],
        "suggestions": ["下次先查温控"],
        "skillSuggestion": {
            "shouldCreate": True,
            "name": "T1 优化技能",
            "keywords": ["t1", "q10lu1"],
            "description": "从成功任务沉淀",
        },
    }


def test_success_task_promotes_skill_and_backfills_promoted_to(mem, monkeypatch):
    report = _success_report()
    monkeypatch.setattr(reflection_engine, "analyze_task", lambda **kw: dict(report))

    steps = [{"tool": "run_experiment", "observation": {"T1": 42e-6}}]
    result = reflection_engine.reflect_after_task(
        task="对 q10lu1 执行 T1 实验", status="success", steps=steps,
        metrics={"T1": 42e-6}, qubit="q10lu1",
    )

    skill_id = result["skillCreated"]
    assert skill_id                                     # 成功 + 报告产出技能 → 升技能
    skill = memory_store.get_skill(skill_id)
    assert skill["name"] == "T1 优化技能"
    assert skill["sourceEpisodeId"] == result["episode"]["id"]

    # 提升链回标：episode.promoted_to（权威字段）与 skillCreated（兼容字段）
    ep = memory_store.get_episode(result["episode"]["id"])
    assert ep["promoted_to"] == skill_id
    assert ep["skillCreated"] == skill_id
    assert ep["status"] == "success"
    assert ep["reflection"]["skillSuggestion"]["shouldCreate"] is True
    assert [s["id"] for s in memory_store.list_skills()] == [skill_id]


def test_failed_task_reflects_but_never_promotes(mem, monkeypatch):
    report = _success_report()          # 报告里 shouldCreate=True 也不该升
    monkeypatch.setattr(reflection_engine, "analyze_task", lambda **kw: dict(report))

    result = reflection_engine.reflect_after_task(
        task="对 q10lu1 执行 T1 实验（失败）", status="failed",
        steps=[{"tool": "run_experiment", "observation": {"error": "timeout"}}],
        metrics={}, qubit="q10lu1",
    )

    assert result["skillCreated"] is None
    assert memory_store.list_skills() == []
    ep = memory_store.get_episode(result["episode"]["id"])
    assert ep["promoted_to"] is None
    assert ep["skillCreated"] is None
    # 反思报告照常产出并落盘（失败教训只反思）
    assert ep["reflection"]["lessons"] == ["多次数平均降噪"]
    assert ep["reflection"]["skillSuggestion"]["shouldCreate"] is True

    # 成功但没有可复用步骤 → 同样不升技能
    result2 = reflection_engine.reflect_after_task(
        task="空步骤的成功任务", status="success", steps=[], metrics={},
    )
    assert result2["skillCreated"] is None
    assert memory_store.get_episode(result2["episode"]["id"])["promoted_to"] is None
    assert memory_store.list_skills() == []


# ── 6. G3：scripts/memory_store.py 是转发层 ──────────────────────────────────

def test_scripts_memory_store_is_forwarder_with_roundtrip(mem):
    scripts_dir = SERVER / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        legacy = importlib.import_module("memory_store")
    finally:
        try:
            sys.path.remove(str(scripts_dir))
        except ValueError:
            pass

    # 确认导入的确实是 scripts/ 下的那一份（而不是别处同名模块）
    assert Path(legacy.__file__).resolve() == (scripts_dir / "memory_store.py").resolve()

    # 转发层：与权威实现是同一批函数对象 —— 不存在第二份实现（G3）
    for name in ("create_episode", "get_episode", "update_episode_reflection",
                 "update_episode_fields", "archive_episode", "run_maintenance",
                 "create_skill", "get_memory_stats", "init_memory"):
        assert getattr(legacy, name) is getattr(memory_store, name), name

    # legacy 导入路径下的 create_episode → get_episode 往返一致
    ep = legacy.create_episode("legacy round trip", [{"a": 1}], "success")
    got = legacy.get_episode(ep["id"])
    assert got is not None
    assert got["id"] == ep["id"]
    assert got["task"] == "legacy round trip"
    assert got["steps"] == [{"a": 1}]
    assert got["lifecycle"] == "active"
    assert got["promoted_to"] is None

    # 同一份实现：权威模块路径读到的就是 legacy 写的数据
    assert memory_store.get_episode(ep["id"])["task"] == "legacy round trip"
    # 且数据落在隔离目录里（真实 config/memory 未被触碰）
    assert (Path(memory_store.EPISODES_DIR) / f"{ep['id']}.json").is_file()

    # scripts/reflection_engine.py 的 `from memory_store import ...` 必须继续可用
    sys.path.insert(0, str(scripts_dir))
    try:
        legacy_reflection = importlib.import_module("reflection_engine")
    finally:
        try:
            sys.path.remove(str(scripts_dir))
        except ValueError:
            pass

    assert legacy_reflection.create_episode is memory_store.create_episode
    assert legacy_reflection.create_skill is memory_store.create_skill
    assert legacy_reflection.update_episode_reflection is memory_store.update_episode_reflection
