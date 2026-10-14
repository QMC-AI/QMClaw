# -*- coding: utf-8 -*-
r"""
agent_service L0 工作记忆（P2-T5）单元测试 — 不依赖运行中的微服务、不联网

覆盖：
1. 历史轮注入：同会话的历史 user/assistant 插在 system 与当前 user 之间
2. 窗口溢出压缩：保留第 1 轮 + 最近 window_size 轮，中间折叠成**一条**
   role=system 的 "[记忆压缩] 此前 N 轮：" 消息（每轮截 120 字符、' | ' 连接）
3. TTL 惰性淘汰：updated_at 超龄的会话在下次访问时被丢弃，历史不再回灌
4. 同一会话两次调用之间上下文连续（monkeypatch _call_llm 可控返回）；
   不同会话互不串味；失败轮不写工作记忆
5. memory 块缺失 / 字段非法时回退默认值；AgentService 实例同样拿到默认值

隔离：EPISODES_DIR / SKILLS_DIR / INDEX_FILE 全部指到用后即删的临时目录，
绝不读写真实 config/memory/（照抄 tests/test_agent_sessions.py 的准备方式）。

运行: & "D:\semester4\deploy\qmclaw\qmclaw\python.exe" -m pytest tests/test_working_memory.py -q
"""

import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))
from services.common import memory_store
from services.agent_service import server as agent_server
from services.agent_service.server import (
    WORKING_MEMORY_DEFAULTS,
    AgentService,
    AgentTask,
    load_memory_config,
)


# ── 测试基础设施 ──────────────────────────────────────────────────────────────

def _new_case_dir() -> Path:
    """新建隔离临时目录（含 episodes/ 与 skills/），用完即删。"""
    candidates = [
        Path(tempfile.gettempdir()),
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parents[1],
    ]
    last_err = None
    for parent in candidates:
        root = parent / f"qmclaw-wmemory-{uuid.uuid4().hex}"
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
    """临时记忆目录 + 一个不联网的 AgentService 实例（含独立工作记忆）"""
    root = _new_case_dir()
    monkeypatch.setattr(memory_store, "EPISODES_DIR", str(root / "episodes"))
    monkeypatch.setattr(memory_store, "SKILLS_DIR", str(root / "skills"))
    monkeypatch.setattr(memory_store, "INDEX_FILE", str(root / "index.json"))
    memory_store.init_memory()
    try:
        yield AgentService(port=3005), root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class _FakeLLM:
    """可控的 _call_llm 替身：记录每次收到的 messages，直接给最终回复（无 tool_calls）"""

    def __init__(self, reply: str = "收到"):
        self.reply = reply
        self.calls = []

    def __call__(self, messages, model=None, temperature=0.7, tools=None):
        self.calls.append([dict(m) for m in messages])
        return {"content": self.reply, "tool_calls": []}


def _task(message, session_id="s1", task_id="task_x"):
    t = AgentTask(task_id=task_id, message=message)
    t.context = {"session_id": session_id, "round_index": 0}
    return t


def _contents(msgs):
    return [m.get("content") for m in msgs]


# ── 1. 历史轮注入 ────────────────────────────────────────────────────────────

def test_history_injected_between_system_and_current_user(env, monkeypatch):
    svc, _ = env
    llm = _FakeLLM("第二轮回答")
    monkeypatch.setattr(svc, "_call_llm", llm)

    svc._wm_append_turn("s1", "第一轮问题", "第一轮回答")
    res = svc._react_reason(_task("第二轮问题", session_id="s1"))

    assert res["success"] is True
    sent = llm.calls[0]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[0]["content"] == svc._system_prompt
    assert _contents(sent[1:]) == ["第一轮问题", "第一轮回答", "第二轮问题"]

    # 返回给前端的 messages 只含本轮 user（历史不外泄，与改动前一致）
    assert [m["role"] for m in res["messages"]] == ["user"]
    assert _contents(res["messages"]) == ["第二轮问题"]
    assert res["response"] == "第二轮回答"

    # 无历史的会话：/ 空 working memory → 仍是 system + user 两条
    empty_llm = _FakeLLM("新会话回答")
    monkeypatch.setattr(svc, "_call_llm", empty_llm)
    svc._react_reason(_task("全新问题", session_id="s_new"))
    assert [m["role"] for m in empty_llm.calls[0]] == ["system", "user"]


# ── 2. 窗口溢出压缩 ──────────────────────────────────────────────────────────

def test_window_overflow_keeps_first_and_recent_folds_middle(env, monkeypatch):
    svc, _ = env
    svc._memory_config["window_size"] = 2
    llm = _FakeLLM("最终回复")
    monkeypatch.setattr(svc, "_call_llm", llm)

    for i in range(5):                                   # 5 轮历史
        svc._wm_append_turn("s1", f"问题{i}", f"回答{i}")

    svc._react_reason(_task("当前问题", session_id="s1"))
    sent = llm.calls[0]

    # 5 轮 = 第1轮(问题0) + 中间2轮折叠 + 最近2轮(问题3/问题4)
    assert [m["role"] for m in sent] == [
        "system", "user", "assistant", "system", "user", "assistant", "user", "assistant", "user",
    ]
    assert _contents(sent[1:3]) == ["问题0", "回答0"]
    folded = sent[3]
    assert folded["role"] == "system"
    assert folded["content"].startswith("[记忆压缩] 此前 2 轮：")
    assert "问题1" in folded["content"] and "回答1" in folded["content"]
    assert "问题2" in folded["content"]
    assert " | " in folded["content"]
    assert "问题3" not in folded["content"] and "问题0" not in folded["content"]
    assert _contents(sent[4:]) == ["问题3", "回答3", "问题4", "回答4", "当前问题"]


def test_no_compression_when_window_not_overflowed(env):
    svc, _ = env
    svc._memory_config["window_size"] = 10
    for i in range(11):                                  # 第1轮 + 最近10轮 = 全部
        svc._wm_append_turn("s1", f"问题{i}", f"回答{i}")
    msgs = svc._wm_history_messages("s1")
    assert len(msgs) == 22                               # 11 轮 × 2 条，无压缩消息
    assert all(m["role"] in ("user", "assistant") for m in msgs)
    assert not any("[记忆压缩]" in (m["content"] or "") for m in msgs)


def test_folded_round_text_truncated_to_120_chars(env):
    svc, _ = env
    svc._memory_config["window_size"] = 1
    svc._wm_append_turn("s1", "首轮问题", "首轮回答")
    svc._wm_append_turn("s1", "X" * 300, "Y" * 300)      # 中间轮：超长
    svc._wm_append_turn("s1", "最近问题", "最近回答")

    msgs = svc._wm_history_messages("s1")
    folded = next(m for m in msgs if m["role"] == "system")
    assert folded["content"].startswith("[记忆压缩] 此前 1 轮：")
    body = folded["content"].split("：", 1)[1]
    assert len(body) <= 120
    assert body.startswith("X" * 100)                    # 纯截断，没有调 LLM 改写


# ── 3. TTL 惰性淘汰 ──────────────────────────────────────────────────────────

def test_ttl_lazy_eviction(env, monkeypatch):
    svc, _ = env
    svc._memory_config["session_ttl"] = 100
    svc._wm_append_turn("s1", "旧问题", "旧回答")
    assert "s1" in svc._working_memory

    # 未超龄：仍然回灌
    assert len(svc._wm_history_messages("s1")) == 2

    # 人为把 updated_at 拨回超龄 → 下次访问即淘汰（无后台线程参与）
    svc._working_memory["s1"]["updated_at"] = time.time() - 101
    assert svc._wm_history_messages("s1") == []
    assert "s1" not in svc._working_memory

    # 淘汰后新一轮：历史为空，且 updated_at 是新的
    llm = _FakeLLM("淘汰后回答")
    monkeypatch.setattr(svc, "_call_llm", llm)
    svc._react_reason(_task("淘汰后问题", session_id="s1"))
    assert [m["role"] for m in llm.calls[0]] == ["system", "user"]
    assert time.time() - svc._working_memory["s1"]["updated_at"] < 5
    assert len(svc._working_memory["s1"]["turns"]) == 2

    # 另一个会话不受影响（各会话独立计时）
    svc._wm_append_turn("s2", "问题", "回答")
    assert len(svc._wm_history_messages("s2")) == 2


# ── 4. 同会话连续性 ──────────────────────────────────────────────────────────

def test_two_calls_same_session_are_continuous(env, monkeypatch):
    svc, _ = env
    llm = _FakeLLM("回答一")
    monkeypatch.setattr(svc, "_call_llm", llm)

    r1 = svc._react_reason(_task("问题一", session_id="sess"))
    assert r1["response"] == "回答一"

    llm.reply = "回答二"
    r2 = svc._react_reason(_task("问题二", session_id="sess"))
    assert r2["response"] == "回答二"

    # 第二次调用的 messages 里带着第一轮的 user/assistant
    assert [m["role"] for m in llm.calls[1]] == ["system", "user", "assistant", "user"]
    assert _contents(llm.calls[1][1:]) == ["问题一", "回答一", "问题二"]
    assert len(svc._working_memory["sess"]["turns"]) == 4

    # 不同会话之间不串味（新的 session key 历史为空）
    llm.reply = "别的话题"
    svc._react_reason(_task("另一个会话的问题", session_id="other"))
    assert [m["role"] for m in llm.calls[2]] == ["system", "user"]

    # 无 session_id 时退回 task_id 作会话键（与 _record_episode 一致）
    t = AgentTask(task_id="task_only", message="按 task_id 记")
    svc._react_reason(t)
    assert len(svc._wm_get_turns("task_only")) == 2


def test_failed_round_not_written_to_working_memory(env, monkeypatch):
    svc, _ = env

    def _boom(messages, model=None, temperature=0.7, tools=None):
        return {"error": "llm down"}

    monkeypatch.setattr(svc, "_call_llm", _boom)
    res = svc._react_reason(_task("会失败的问题", session_id="s1"))
    assert res["success"] is False
    assert svc._wm_get_turns("s1") == []


# ── 5. 配置外置与默认值 ──────────────────────────────────────────────────────

def test_memory_config_defaults_when_block_missing(monkeypatch):
    monkeypatch.setattr(agent_server, "load_system_config", lambda: {})
    assert load_memory_config() == WORKING_MEMORY_DEFAULTS

    # 块存在但不是 dict → 默认值
    monkeypatch.setattr(agent_server, "load_system_config", lambda: {"memory": "oops"})
    assert load_memory_config() == WORKING_MEMORY_DEFAULTS


def test_memory_config_bad_fields_fall_back(monkeypatch):
    bad = {"memory": {"window_size": "abc", "summary_threshold": -1, "session_ttl": None}}
    monkeypatch.setattr(agent_server, "load_system_config", lambda: bad)
    assert load_memory_config() == WORKING_MEMORY_DEFAULTS

    # 配置读取抛异常也不能崩
    def _raise():
        raise RuntimeError("config broken")

    monkeypatch.setattr(agent_server, "load_system_config", _raise)
    assert load_memory_config() == WORKING_MEMORY_DEFAULTS


def test_memory_config_partial_override_and_instance_defaults(monkeypatch):
    cfg = load_memory_config({"memory": {"window_size": 3}})
    assert cfg == {"window_size": 3, "summary_threshold": 20, "session_ttl": 86400}

    monkeypatch.setattr(agent_server, "load_system_config", lambda: {})
    assert AgentService(port=3005)._memory_config == WORKING_MEMORY_DEFAULTS


def test_repo_system_json_has_memory_block():
    """真实 config/system.json 的 memory 块就是 P2-T5 约定的那三个键"""
    cfg = json.loads((SERVER / "config" / "system.json").read_text(encoding="utf-8"))
    assert cfg["memory"] == {"window_size": 10, "summary_threshold": 20, "session_ttl": 86400}
    assert load_memory_config(cfg) == WORKING_MEMORY_DEFAULTS
