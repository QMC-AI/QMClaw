"""
Memory Manager - 分级记忆召回门面（P3-T6 召回升级）

把长期记忆的召回从"关键词包含 + 裸取最近 N 条"升级为**分层 + 打分 + 预算**：

    L3 技能（skills）   ← memory_store.match_skills（已按命中数/成功率排序）
    L2 情景（episodes） ← 命中 qubit / 实验类型的历史任务
    L1 会话（sessions） ← 命中 qubit / 实验类型的会话摘要行

打分（纯函数、可复现，不依赖随机、不依赖调用顺序）：

    score = qubit 命中(+2)
          + 实验类型命中(+2，词表见 EXPERIMENT_TYPES)
          + 状态加权（status == "failed" 时 +1.5，教训比成功经验更值得看）
          + 时间衰减 exp(-Δ天 / 30)

    无 timestamp 的条目不计衰减分（也没有时间原因），
    同分排序按 timestamp 新 → 旧；仍并列时维持候选收集顺序（索引顺序 = 新在前）。

预算：context 文本总长不超过 budget_chars。所有候选按分数从高到低装填，
      装不下的条目 id 记录在 recall_meta["truncated"]，**不静默丢弃**。
      recall_meta["scores"] 则列出**全部被打分的候选**（含被截断的），便于解释。

返回形状兼容 reflection_engine.recall_for_task 的旧字段
（hasMemory / relevantSkills / recentEpisodes / qubitHistory / context），
另加 sessionSummaries（L1 召回结果）与 recall_meta（可解释元数据）。

用法：
    from services.common.memory_manager import recall
    r = recall("对 q10lu8 运行 t1", "q10lu8", budget_chars=2000)
"""

import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# 兼容"作为包内模块"与"作为独立脚本"两种导入方式（与 reflection_engine 同风格）
try:
    from . import memory_store
except ImportError:  # 作为独立脚本运行（无包上下文）
    import memory_store  # type: ignore


# ── 常量（打分权重与词表，改动即影响可复现结果，故集中在此） ──────────────────

QUBIT_BONUS = 2.0        # qubit 命中
TYPE_BONUS = 2.0         # 实验类型命中（每种/每次命中只加一次）
FAILED_BONUS = 1.5       # 失败教训加权

DEFAULT_BUDGET_CHARS = 2000
DECAY_HALF_LIFE_DAYS = 30.0

# 实验类型词表（小写子串匹配，与 match_skills 的关键词风格一致）
EXPERIMENT_TYPES: Tuple[str, ...] = (
    "s21", "spectroscopy", "ramsey", "t1", "rabi", "iqraw", "piamp",
    "single_shot", "xeb", "allxy", "swap", "drag", "powershift", "delta",
)

# 候选规模上限：只保留索引中最新的若干条 episode 参与打分，防止记忆目录膨胀后
# 单次召回把整个目录读进来。索引顺序为"新在前"。
MAX_EPISODE_CANDIDATES = 100
# 完全没有命中时（无 qubit、无实验类型词）退化为"最近的 N 条"
FALLBACK_RECENT = 3

_HEADERS = {
    "skills": "📚 相关已学技能:",
    "sessions": "🗂 相关会话摘要:",
}

_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


# ── 纯函数：时间与词表 ────────────────────────────────────────────────────────

def _parse_ts(value) -> Optional[datetime]:
    """解析 ISO / 常见格式时间戳，失败返回 None（老数据可能缺 timestamp）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):          # 3.11 的 fromisoformat 不认 Z
        text = text[:-1]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for fmt in _TS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _days_between(now: datetime, ts: datetime) -> float:
    """now 与 ts 相差的天数（负数按 0 处理；时区信息统一丢弃，避免 naive/aware 混算）。"""
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    delta = (now - ts).total_seconds() / 86400.0
    return delta if delta > 0 else 0.0


def _types_in(text: str) -> List[str]:
    """文本里出现的实验类型（按 EXPERIMENT_TYPES 顺序，保持 reason 稳定）。"""
    low = (text or "").lower()
    return [t for t in EXPERIMENT_TYPES if t in low]


def _recency_bucket(days: float) -> str:
    """把天数转成人类可读的档位（reason 用）。"""
    if days < 1:
        return "24小时内"
    if days < 3:
        return "3天内"
    if days < 7:
        return "7天内"
    if days < 30:
        return "30天内"
    return f"{int(days)}天前"


def score_item(
    *,
    item_qubit: Optional[str],
    item_status: Optional[str],
    item_timestamp: Optional[str],
    item_text: str,
    query_types: List[str],
    qubit: Optional[str],
    now: datetime,
    keyword_hit: bool = False,
) -> Tuple[float, str]:
    """给单条候选打分。**纯函数**：同输入必得同输出。

    Returns:
        (score, reason) —— reason 形如 "qubit命中+实验类型(t1)+失败教训+3天内"
    """
    score = 0.0
    parts: List[str] = []

    if qubit and item_qubit == qubit:
        score += QUBIT_BONUS
        parts.append("qubit命中")

    hit_types = [t for t in query_types if t in (item_text or "").lower()]
    if hit_types:
        score += TYPE_BONUS
        parts.append("实验类型({})".format(",".join(hit_types)))
    elif keyword_hit:
        # 技能是靠关键词命中的，但任务里没有实验类型词：给个可读原因
        parts.append("关键词命中")

    if item_status == "failed":
        score += FAILED_BONUS
        parts.append("失败教训")

    ts = _parse_ts(item_timestamp)
    if ts is None:
        decay = 0.0                 # 无时间信息 → 不加衰减分，也不写时间原因
    else:
        days = _days_between(now, ts)
        decay = math.exp(-days / DECAY_HALF_LIFE_DAYS)
        parts.append(_recency_bucket(days))

    return score + decay, ("+".join(parts) if parts else "无明显匹配")


# ── 候选收集（只读 memory_store，不写任何文件） ──────────────────────────────

def _live_episodes() -> List[Dict]:
    """索引条目 ∧ 实体文件存在 ∧ 未归档的完整 episode（索引顺序 = 新在前）。"""
    iter_live = getattr(memory_store, "_iter_live_episodes", None)
    episodes = iter_live() if callable(iter_live) else memory_store.list_episodes(limit=500)
    return [ep for ep in episodes if not ep.get("archived", False)]


def _skill_candidates(task: str) -> List[Dict]:
    """L3：memory_store.match_skills 的结果（已按命中数、成功率排序）。"""
    task_lower = (task or "").lower()
    out: List[Dict] = []
    for skill in memory_store.match_skills(task or ""):
        keywords = [k for k in (skill.get("triggerKeywords") or [])
                    if isinstance(k, str) and k.strip()]
        matched_kw = next(
            (k for k in keywords if k.lower() in task_lower or task_lower in k.lower()), None)
        text = " ".join([skill.get("name") or "", skill.get("description") or ""] + keywords)
        out.append({
            "id": skill.get("id"),
            "tier": "skills",
            "entity": skill,
            "text": text,
            "qubit": None,                       # 技能没有 qubit 维度
            "status": skill.get("status"),
            "timestamp": (skill.get("lastUsed") or skill.get("createdAt")
                          or skill.get("lastSuccessAt")),
            "keyword_hit": bool(matched_kw),
        })
    return out


def _episode_candidates(qubit: Optional[str], query_types: List[str]) -> List[Dict]:
    """L2：qubit 或实验类型命中的 episode；全无命中时退化为最近 FALLBACK_RECENT 条。"""
    pool = _live_episodes()[:MAX_EPISODE_CANDIDATES]
    candidates: List[Dict] = []
    matched_any = False
    for ep in pool:
        text = " ".join([ep.get("task") or ""] + [str(t) for t in (ep.get("tags") or [])])
        has_match = bool((qubit and ep.get("qubit") == qubit) or
                         [t for t in query_types if t in text.lower()])
        matched_any = matched_any or has_match
        candidates.append({
            "id": ep.get("id"),
            "tier": "episodes",
            "entity": ep,
            "text": text,
            "qubit": ep.get("qubit"),
            "status": ep.get("status"),
            "timestamp": ep.get("timestamp"),
            "keyword_hit": has_match,
            "_matched": has_match,
        })
    if not matched_any:                      # 无任何命中 → 只看最近几条（低分兜底）
        return candidates[:FALLBACK_RECENT]
    return [c for c in candidates if c["_matched"]]


def _session_candidates(qubit: Optional[str], query_types: List[str]) -> List[Dict]:
    """L1：qubit 或实验类型命中的会话摘要；全无命中时退化为最近 FALLBACK_RECENT 个。"""
    pool = memory_store.list_sessions(limit=50)
    candidates: List[Dict] = []
    matched_any = False
    for session in pool:
        text = session.get("taskPreview") or ""
        has_match = bool((qubit and session.get("qubit") == qubit) or
                         [t for t in query_types if t in text.lower()])
        matched_any = matched_any or has_match
        candidates.append({
            "id": session.get("sessionId"),
            "tier": "sessions",
            "entity": session,
            "text": text,
            "qubit": session.get("qubit"),
            "status": session.get("status"),
            "timestamp": session.get("lastAt"),
            "keyword_hit": has_match,
            "_matched": has_match,
        })
    if not matched_any:
        return candidates[:FALLBACK_RECENT]
    return [c for c in candidates if c["_matched"]]


# ── 行渲染 ────────────────────────────────────────────────────────────────────

def _render_line(entry: Dict) -> str:
    """一条候选在 context 里占一行（带 reason，人可读、可解释）。"""
    reason = entry["reason"]
    entity = entry["entity"]
    if entry["tier"] == "skills":
        rate = entry["successRate"] * 100
        return f"   • {entity.get('name')} (成功率: {rate:.0f}%) — {reason}"
    if entry["tier"] == "episodes":
        ts = (entity.get("timestamp") or "")[:16]
        emoji = "✅" if entity.get("status") == "success" else "⚠️"
        preview = (entity.get("task") or "")[:60]
        return f"   • {ts} {emoji} {preview} — {reason}"
    ts = (entity.get("lastAt") or "")[:16]
    rounds = entity.get("rounds")
    preview = (entity.get("taskPreview") or "")[:60]
    return f"   • {ts} [{rounds}轮] {preview} — {reason}"


def _skill_success_rate(skill: Dict) -> float:
    sc = skill.get("successCount", 0) or 0
    fc = skill.get("failureCount", 0) or 0
    total = sc + fc
    return (sc / total) if total > 0 else 0.0


# ── 门面 ──────────────────────────────────────────────────────────────────────

def recall(
    task: str,
    qubit: str = None,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    now: Optional[datetime] = None,
) -> Dict:
    """分层召回与当前任务相关的记忆。

    Args:
        task: 当前任务描述。
        qubit: 当前比特（可选）。
        budget_chars: context 文本的字符预算上限（<=0 时 context 为空，全部记入 truncated）。
        now: 打分基准时间，**仅为测试/复现提供**；默认 datetime.now()。

    Returns:
        {
          "hasMemory": bool,               # context 非空即为 True
          "relevantSkills": [...],         # 旧字段：已装进 context 的 L3 技能
          "recentEpisodes": [...],         # 旧字段：已装进 context 的 L2 episode（完整实体）
          "qubitHistory": [...],           # 旧字段：该 qubit 最近的 episode 索引条目（最近 5 条）
          "context": str,                  # 供 prompt 使用的分级上下文（<= budget_chars）
          "sessionSummaries": [...],       # 新增：已装进 context 的 L1 会话摘要
          "recall_meta": {
              "scores": [{"id","score","reason","tier"}...],  # 全部被打分的候选（含被截断）
              "budget_chars": int,
              "used_chars": int,           # == len(context)
              "truncated": [id...],        # 预算装不下的候选（按分数降序）
              "tiers": {"skills": N, "episodes": N, "sessions": N}  # 各层**装进 context**的条数
          }
        }
    """
    task = task or ""
    try:
        budget = int(budget_chars)
    except (TypeError, ValueError):
        budget = DEFAULT_BUDGET_CHARS
    if isinstance(budget_chars, bool) or budget < 0:
        budget = 0
    base_now = now or datetime.now()

    query_types = _types_in(task)

    candidates = (_skill_candidates(task)
                  + _episode_candidates(qubit, query_types)
                  + _session_candidates(qubit, query_types))

    # ── 打分与排序（同分：timestamp 新 → 旧） ──
    entries: List[Dict] = []
    for cand in candidates:
        score, reason = score_item(
            item_qubit=cand["qubit"],
            item_status=cand["status"],
            item_timestamp=cand["timestamp"],
            item_text=cand["text"],
            query_types=query_types,
            qubit=qubit,
            now=base_now,
            keyword_hit=cand["keyword_hit"],
        )
        ts_dt = _parse_ts(cand["timestamp"])
        entry = dict(cand)
        entry["score"] = score
        entry["reason"] = reason
        entry["ts_dt"] = ts_dt
        if entry["tier"] == "skills":
            entry["successRate"] = _skill_success_rate(entry["entity"])
        entry["line"] = _render_line(entry)
        entries.append(entry)

    entries.sort(key=lambda e: (-e["score"],
                               -(e["ts_dt"].timestamp() if e["ts_dt"] else 0.0)))

    # ── 预算装填（高分 → 低分；装不下的记入 truncated） ──
    headers = dict(_HEADERS)
    headers["episodes"] = f"📋 {qubit or '同类'}任务的历史经验:"

    lines: List[str] = []
    used = 0
    emitted_tiers = set()
    included = {"skills": 0, "episodes": 0, "sessions": 0}
    truncated: List[str] = []

    for entry in entries:
        pending = ([headers[entry["tier"]]] if entry["tier"] not in emitted_tiers else []) \
            + [entry["line"]]
        cost_full = sum(len(part) + 1 for part in pending)
        if used + cost_full <= budget:
            lines.extend(pending)
            used += cost_full
            emitted_tiers.add(entry["tier"])
            included[entry["tier"]] += 1
            entry["packed"] = True
        elif used + len(entry["line"]) + 1 <= budget:
            # 标题挤不下但正文放得下：先放正文，标题留给后面的同层条目再试
            lines.append(entry["line"])
            used += len(entry["line"]) + 1
            included[entry["tier"]] += 1
            entry["packed"] = True
        else:
            truncated.append(entry["id"])
            entry["packed"] = False

    context = "\n".join(lines)

    relevant_skills = []
    for entry in entries:
        if entry["tier"] != "skills" or not entry["packed"]:
            continue
        skill = entry["entity"]
        relevant_skills.append({
            "id": skill.get("id"),
            "name": skill.get("name"),
            "keywords": skill.get("triggerKeywords", []),
            "successRate": entry["successRate"],
            "score": round(entry["score"], 4),
            "reason": entry["reason"],
        })

    recent_episodes = [e["entity"] for e in entries
                       if e["tier"] == "episodes" and e["packed"]]
    session_summaries = [e["entity"] for e in entries
                         if e["tier"] == "sessions" and e["packed"]]

    qubit_history = (memory_store.get_recent_episodes_for_qubit(qubit, limit=5)
                     if qubit else [])

    recall_meta = {
        "scores": [
            {
                "id": e["id"],
                "score": round(e["score"], 4),
                "reason": e["reason"],
                "tier": e["tier"],
            }
            for e in entries
        ],
        "budget_chars": budget,
        "used_chars": len(context),
        "truncated": truncated,
        "tiers": dict(included),
    }

    return {
        "hasMemory": bool(context),
        "relevantSkills": relevant_skills,
        "recentEpisodes": recent_episodes,
        "qubitHistory": qubit_history,
        "context": context,
        "sessionSummaries": session_summaries,
        "recall_meta": recall_meta,
    }


if __name__ == "__main__":  # 手动试跑：python -m services.common.memory_manager
    import json as _json
    print(_json.dumps(recall("对 q10lu8 运行 t1", "q10lu8"),
                      ensure_ascii=False, indent=2))
