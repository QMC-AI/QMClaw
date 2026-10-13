# -*- coding: utf-8 -*-
r"""
memory_manager 分级召回单元测试 — 不依赖运行中的微服务

覆盖（P3-T6 契约）：
1. 打分可复现：固定 now 后同输入两次 recall 结果完全一致
2. 预算截断：len(context) == used_chars <= budget_chars，装不下的记入 truncated
3. 空记忆：hasMemory=False，各列表为空
4. 旧形状回归：hasMemory/relevantSkills/recentEpisodes/qubitHistory/context 齐全
   （外加 sessionSummaries 与 recall_meta）
5. 失败教训权重高于同况成功经验（failed +1.5）
6. match_skills 排序：命中数优先，其次 successRate
7. reflection_engine.recall_for_task 委托门面且形状兼容（验收：q10lu8 + t1）

隔离：每个测试把 EPISODES_DIR / SKILLS_DIR / INDEX_FILE 全部指到用后即删的临时目录，
绝不读写真实 config/memory/。

运行: & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_memory_recall.py -q
"""

import json
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))
from services.common import memory_store, memory_manager, reflection_engine

# 固定基准时间：所有断言都基于它，保证"可复现"是真的可复现
NOW = datetime(2026, 3, 10, 12, 0, 0)

OLD_FIELDS = {"hasMemory", "relevantSkills", "recentEpisodes", "qubitHistory", "context"}
META_FIELDS = {"scores", "budget_chars", "used_chars", "truncated", "tiers"}


# ── 测试基础设施（照抄 tests/test_memory_sessions.py 的隔离模式） ──────────────

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
        root = parent / f"qmclaw-memrecall-{uuid.uuid4().hex}"
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


def write_raw_episode(ep_id, timestamp, task, status="success", qubit=None,
                      session_id=None, round_index=None, response=None, tags=None):
    """直接落盘一个 episode 实体 + 索引条目（绕开 create_episode，便于指定 timestamp）。"""
    ep = {
        "id": ep_id,
        "timestamp": timestamp,
        "task": task,
        "qubit": qubit,
        "mode": "react",
        "steps": [{"action": f"step-{ep_id}"}],
        "status": status,
        "metrics": {},
        "tags": tags or [],
        "usedSkills": [],
        "sessionId": session_id,
        "roundIndex": round_index,
        "model": "m-test",
        "response": response,
        "reflection": None,
        "skillCreated": None,
        "archived": False,
    }
    with open(Path(memory_store.EPISODES_DIR) / f"{ep_id}.json", "w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False)

    entry = {
        "id": ep_id,
        "timestamp": timestamp,
        "qubit": qubit,
        "status": status,
        "sessionId": session_id,
        "roundIndex": round_index,
        "task_preview": task[:50],
    }

    def _append(index):
        index["episodes"].append(entry)

    memory_store._update_index(_append)
    return ep


def make_skill(name, keywords, success_extra=0, failure_extra=0):
    """建一个技能，并按需调整 successCount / failureCount（create_skill 初始 1/0）。"""
    skill = memory_store.create_skill(name=name, trigger_keywords=keywords, steps=[])
    for _ in range(success_extra):
        memory_store.update_skill_usage(skill["id"], success=True)
    for _ in range(failure_extra):
        memory_store.update_skill_usage(skill["id"], success=False)
    return memory_store.get_skill(skill["id"])


# ── 1. 打分可复现 ────────────────────────────────────────────────────────────

def test_recall_is_reproducible(mem):
    """同输入 + 同 now ⇒ 逐字段完全一致（打分是纯函数，不看挂钟）。"""
    write_raw_episode("ep_1", "2026-03-09T10:00:00", "对 q10lu8 运行 t1 实验",
                      status="failed", qubit="q10lu8", session_id="s1", round_index=0)
    write_raw_episode("ep_2", "2026-03-08T10:00:00", "对 q10lu8 运行 rabi 实验",
                      status="success", qubit="q10lu8", session_id="s1", round_index=1)
    write_raw_episode("ep_3", "2026-02-01T10:00:00", "对 q9lu3 运行 t1 实验",
                      status="success", qubit="q9lu3", session_id="s2", round_index=0)
    make_skill("t1-calibrate", ["t1", "calibrate"])

    task, qubit = "对 q10lu8 运行 t1 实验", "q10lu8"
    first = memory_manager.recall(task, qubit, 2000, now=NOW)
    second = memory_manager.recall(task, qubit, 2000, now=NOW)
    assert first == second

    # 纯函数本身也可复现（同一参数调用两次）
    kwargs = dict(item_qubit="q10lu8", item_status="failed",
                  item_timestamp="2026-03-08T12:00:00", item_text="对 q10lu8 运行 t1",
                  query_types=["t1"], qubit="q10lu8", now=NOW)
    assert memory_manager.score_item(**kwargs) == memory_manager.score_item(**kwargs)

    # 分数用真实衰减值：failed +1.5 会体现在 reason/score 上
    scores = {m["id"]: m for m in first["recall_meta"]["scores"]}
    assert scores["ep_1"]["score"] > 0
    assert "失败教训" in scores["ep_1"]["reason"]


# ── 2. 预算截断与 truncated 记录 ──────────────────────────────────────────────

def test_budget_truncation_records_truncated(mem):
    """小预算：context 不超预算；装不下的全部记入 truncated，一条都不静默丢。"""
    for i in range(6):
        write_raw_episode(f"ep_{i}", f"2026-03-0{i + 1}T10:00:00",
                          f"对 q10lu8 运行 t1 实验 第{i}轮", status="success",
                          qubit="q10lu8", session_id="s1", round_index=i)
    make_skill("t1-calibrate", ["t1"])

    small = memory_manager.recall("对 q10lu8 运行 t1 实验", "q10lu8",
                                  budget_chars=150, now=NOW)
    meta = small["recall_meta"]
    assert meta["budget_chars"] == 150
    assert meta["used_chars"] == len(small["context"]) <= 150
    assert meta["truncated"], "预算装不下的候选必须记录 id"

    packed_ids = ({s["id"] for s in small["relevantSkills"]}
                  | {e["id"] for e in small["recentEpisodes"]}
                  | {s["sessionId"] for s in small["sessionSummaries"]})
    assert packed_ids.isdisjoint(meta["truncated"])
    # 不丢条目的不变量：装进 context 的条数 + 截断条数 == 参与打分的候选条数
    assert sum(meta["tiers"].values()) + len(meta["truncated"]) == len(meta["scores"])
    assert sum(meta["tiers"].values()) == len(packed_ids)

    # 预算放大 → 全部装下，truncated 清空
    big = memory_manager.recall("对 q10lu8 运行 t1 实验", "q10lu8",
                                budget_chars=100000, now=NOW)
    assert big["recall_meta"]["truncated"] == []
    assert big["recall_meta"]["used_chars"] == len(big["context"]) < 100000
    assert len(big["context"]) > len(small["context"])
    assert sum(big["recall_meta"]["tiers"].values()) == len(big["recall_meta"]["scores"])

    # budget_chars=0 → 什么都不装，全部记入 truncated，hasMemory=False
    zero = memory_manager.recall("对 q10lu8 运行 t1 实验", "q10lu8",
                                 budget_chars=0, now=NOW)
    assert zero["context"] == ""
    assert zero["hasMemory"] is False
    assert zero["recall_meta"]["truncated"]
    assert zero["recall_meta"]["tiers"] == {"skills": 0, "episodes": 0, "sessions": 0}


# ── 3. 空记忆 ────────────────────────────────────────────────────────────────

def test_empty_memory_has_no_memory(mem):
    result = memory_manager.recall("对 q10lu8 运行 t1 实验", "q10lu8", now=NOW)
    assert result["hasMemory"] is False
    assert result["context"] == ""
    assert result["relevantSkills"] == []
    assert result["recentEpisodes"] == []
    assert result["qubitHistory"] == []
    assert result["sessionSummaries"] == []
    assert result["recall_meta"]["scores"] == []
    assert result["recall_meta"]["truncated"] == []
    assert result["recall_meta"]["used_chars"] == 0
    assert memory_store.match_skills("t1") == []


# ── 4. 旧形状回归 ────────────────────────────────────────────────────────────

def test_legacy_shape_regression(mem):
    write_raw_episode("ep_1", "2026-03-05T10:00:00", "对 q10lu8 运行 t1 实验",
                      status="failed", qubit="q10lu8", session_id="s1", round_index=0)
    make_skill("t1-calibrate", ["t1"])

    result = memory_manager.recall("对 q10lu8 运行 t1 实验", "q10lu8", now=NOW)
    assert OLD_FIELDS <= set(result.keys())
    assert {"sessionSummaries", "recall_meta"} <= set(result.keys())
    assert set(result["recall_meta"].keys()) == META_FIELDS

    assert result["hasMemory"] is True
    assert isinstance(result["context"], str) and result["context"]

    skill = result["relevantSkills"][0]
    assert {"id", "name", "keywords", "successRate"} <= set(skill.keys())
    assert isinstance(skill["successRate"], float)

    assert result["recentEpisodes"][0]["id"] == "ep_1"
    assert result["recentEpisodes"][0]["task"] == "对 q10lu8 运行 t1 实验"

    # qubitHistory 保持旧语义：该 qubit 最近的索引条目（按索引顺序）
    assert [e["id"] for e in result["qubitHistory"]] == ["ep_1"]

    # reason 人可读，含命中原因与时间档位
    meta = {m["id"]: m for m in result["recall_meta"]["scores"]}
    assert meta["ep_1"]["reason"] == "qubit命中+实验类型(t1)+失败教训+7天内"


# ── 5. 失败教训权重高于同况成功 ───────────────────────────────────────────────

def test_failed_lesson_outweighs_success(mem):
    write_raw_episode("ep_fail", "2026-03-08T10:00:00", "对 q10lu8 运行 t1 实验",
                      status="failed", qubit="q10lu8", session_id="s1", round_index=0)
    write_raw_episode("ep_ok", "2026-03-08T10:00:00", "对 q10lu8 运行 t1 实验",
                      status="success", qubit="q10lu8", session_id="s2", round_index=0)

    result = memory_manager.recall("对 q10lu8 运行 t1 实验", "q10lu8", budget_chars=5000, now=NOW)
    scores = {m["id"]: m for m in result["recall_meta"]["scores"]}

    assert scores["ep_fail"]["score"] - scores["ep_ok"]["score"] == pytest.approx(1.5, abs=1e-6)
    assert "失败教训" in scores["ep_fail"]["reason"]
    assert "失败教训" not in scores["ep_ok"]["reason"]
    # 排序：失败教训在前
    assert [e["id"] for e in result["recentEpisodes"]] == ["ep_fail", "ep_ok"]

    # 时间衰减确实在起作用：越新的分越高（同 status/qubit/类型）
    write_raw_episode("ep_old", "2025-01-01T10:00:00", "对 q10lu8 运行 t1 实验",
                      status="failed", qubit="q10lu8", session_id="s3", round_index=0)
    result2 = memory_manager.recall("对 q10lu8 运行 t1 实验", "q10lu8", budget_chars=5000, now=NOW)
    scores2 = {m["id"]: m for m in result2["recall_meta"]["scores"]}
    assert scores2["ep_fail"]["score"] > scores2["ep_old"]["score"]
    assert scores2["ep_old"]["reason"].endswith("天前")


# ── 6. match_skills 排序生效 ─────────────────────────────────────────────────

def test_match_skills_sorted_by_hits_then_success_rate(mem):
    multi = make_skill("multi-hit", ["t1", "rabi"])
    single = make_skill("single-hit", ["t1"])
    matched = memory_store.match_skills("对 q10lu8 运行 t1 与 rabi")
    assert [s["id"] for s in matched] == [multi["id"], single["id"]]

    # 命中数相同时按 successRate 降序
    good = make_skill("good-rate", ["xeb"], success_extra=8)     # 9/9 → 1.0
    bad = make_skill("bad-rate", ["xeb"], failure_extra=9)       # 1/10 → 0.1
    assert memory_store.get_skill(good["id"])["successCount"] == 9
    assert memory_store.get_skill(bad["id"])["failureCount"] == 9
    ranked = memory_store.match_skills("运行 xeb 实验")
    assert [s["id"] for s in ranked] == [good["id"], bad["id"]]

    # 旧签名兼容：仍是 list[Dict]，未命中返回空列表
    assert isinstance(ranked, list) and all(isinstance(s, dict) for s in ranked)
    assert memory_store.match_skills("完全无关的任务描述") == []


# ── 7. reflection_engine 委托门面（验收路径） ────────────────────────────────

def test_reflection_engine_recall_for_task_delegates(mem):
    write_raw_episode("ep_1", "2026-03-09T10:00:00", "对 q10lu8 运行 t1 实验",
                      status="failed", qubit="q10lu8", session_id="s1", round_index=0)
    write_raw_episode("ep_2", "2026-03-09T11:00:00", "对 q10lu8 运行 t1 校准",
                      status="success", qubit="q10lu8", session_id="s1", round_index=1)
    make_skill("t1-calibrate", ["t1"])

    result = reflection_engine.recall_for_task("对 q10lu8 运行 t1", "q10lu8")

    assert OLD_FIELDS <= set(result.keys())
    assert result["hasMemory"] is True
    assert result["recall_meta"]["budget_chars"] == 2000          # 默认预算
    assert result["recall_meta"]["used_chars"] == len(result["context"]) <= 2000
    assert result["recall_meta"]["tiers"]["episodes"] == 2
    assert result["recall_meta"]["tiers"]["skills"] == 1
    assert [e["id"] for e in result["recentEpisodes"]] == ["ep_1", "ep_2"]

    # 预算参数透传
    tiny = reflection_engine.recall_for_task("对 q10lu8 运行 t1", "q10lu8", budget_chars=80)
    assert tiny["recall_meta"]["budget_chars"] == 80
    assert len(tiny["context"]) <= 80
