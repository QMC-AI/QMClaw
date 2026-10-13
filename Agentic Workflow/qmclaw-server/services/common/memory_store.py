"""
Memory Store - 长期记忆存储模块

提供 Episode（任务记忆）和 Skill（技能）的持久化存储。
使用 JSON 文件存储，便于用户查看和编辑。

目录结构:
    config/memory/
    ├── episodes/      # 每次任务的完整记录
    ├── skills/       # 自动学习的技能
    └── index.json    # 索引（快速查询用）

来源说明：本模块是**唯一权威实现**。scripts/memory_store.py 已收敛为转发层（G3），
它把 <qmclaw-server> 加入 sys.path 后 `from services.common.memory_store import *`，
因此两种导入路径读写的是同一份实现、同一份磁盘格式，不会再 schema 分叉。
"""

import os
import json
import uuid
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Any
from pathlib import Path

# 记忆存储根目录
# 本模块位于 <qmclaw-server>/services/common/，而记忆目录固定为 <qmclaw-server>/config/memory。
# 原实现写的是 dirname(__file__)/../config —— 那是站在 scripts/ 下的正确写法，
# 搬到 services/common/ 后会解析成 services/config（错的）。
# 这里改为按服务根解析，不依赖 cwd。
_SERVER_ROOT = Path(__file__).resolve().parents[2]  # .../qmclaw-server
MEMORY_BASE = str(_SERVER_ROOT / "config" / "memory")
EPISODES_DIR = os.path.join(MEMORY_BASE, "episodes")
SKILLS_DIR = os.path.join(MEMORY_BASE, "skills")
INDEX_FILE = os.path.join(MEMORY_BASE, "index.json")

# 确保目录存在
os.makedirs(EPISODES_DIR, exist_ok=True)
os.makedirs(SKILLS_DIR, exist_ok=True)

# config/memory 下索引与实体文件的读写都在这一把锁内完成（可重入）。
# index.json 是无锁的 load→modify→save：并发写会互相覆盖，导致"实体文件在、
# 索引条目没了"的孤儿。多线程 HTTP 服务器下这个竞态是必现的。
_INDEX_LOCK = threading.RLock()


def _load_index() -> Dict:
    """加载索引文件（持锁读，保证读到的是完整 JSON，而不是别人写了一半的文件）"""
    with _INDEX_LOCK:
        try:
            if os.path.exists(INDEX_FILE):
                with open(INDEX_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"MemoryStore: Failed to load index: {e}")
        return {
            "episodes": [],      # [{id, timestamp, qubit, status}]
            "skills": [],        # [{id, name, status}]
            "last_updated": None
        }


def _save_index(index: Dict):
    """保存索引文件（持锁写）"""
    with _INDEX_LOCK:
        index["last_updated"] = datetime.now().isoformat()
        try:
            with open(INDEX_FILE, 'w', encoding='utf-8') as f:
                json.dump(index, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"MemoryStore: Failed to save index: {e}")


def _update_index(mutator: Callable[[Dict], None]) -> Dict:
    """索引的原子读-改-写。

    mutator 直接原地修改传进来的 index（返回值被忽略）；整个
    load → mutate → save 在一次加锁内完成，并发调用不会互相覆盖。
    """
    with _INDEX_LOCK:
        index = _load_index()
        mutator(index)
        _save_index(index)
        return index


# ── Lifecycle（P4-T7：提升 / 遗忘） ──────────────────────────────────────────
#
# episode 生命周期：active（活跃）→ archived（已归档）。
# 老数据没有 lifecycle 字段（交付包里的 14 条都是），读取口一律按 active 兼容；
# archived 与 lifecycle 两个字段保持同步（历史实现只写 archived，新实现两者都写）。

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_ARCHIVED = "archived"


def _episode_lifecycle(ep: Dict) -> str:
    """取 episode 生命周期；字段缺失 / 非法时按老数据规则退化。"""
    lc = ep.get("lifecycle")
    if lc in (LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED):
        return lc
    return LIFECYCLE_ARCHIVED if ep.get("archived") else LIFECYCLE_ACTIVE


def _normalize_episode(ep: Optional[Dict]) -> Optional[Dict]:
    """兼容老数据：原地补齐 lifecycle / archived / promoted_to（所有读取口统一走这里）。

    - 无 lifecycle：按 archived 推断（archived=True → archived，否则 active）
    - 无 promoted_to：按 None（尚未提升成技能）
    - archived 与 lifecycle 强制同步
    """
    if not isinstance(ep, dict):
        return ep
    lc = _episode_lifecycle(ep)
    ep["lifecycle"] = lc
    ep["archived"] = (lc == LIFECYCLE_ARCHIVED)
    if "promoted_to" not in ep:
        ep["promoted_to"] = None
    return ep


def _list_episode_files() -> List[str]:
    """EPISODES_DIR 下全部 episode 实体文件路径（*.json，排序保证确定）。"""
    if not os.path.isdir(EPISODES_DIR):
        return []
    return [os.path.join(EPISODES_DIR, f) for f in sorted(os.listdir(EPISODES_DIR))
            if f.endswith(".json")]


def _read_episode_file(path: str) -> Optional[Dict]:
    """按路径读一个 episode 实体（已做老数据补字段）。读不了返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _normalize_episode(json.load(f))
    except Exception as e:
        print(f"MemoryStore: Failed to read episode file {path}: {e}")
        return None


def _write_episode_file(path: str, episode: Dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(episode, f, indent=2, ensure_ascii=False)


def _parse_ts(value: Any) -> Optional[datetime]:
    """解析 ISO 时间戳；缺失 / 非法返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ── Episode 管理 ──────────────────────────────────────────────────────────────

def generate_episode_id() -> str:
    """生成唯一的 Episode ID"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_id = str(uuid.uuid4())[:8]
    return f"ep_{ts}_{short_id}"


def create_episode(
    task: str,
    steps: List[Dict],
    status: str,
    metrics: Dict[str, float] = None,
    qubit: str = None,
    tags: List[str] = None,
    used_skills: List[str] = None,
    mode: str = "react",
    session_id: str = None,
    round_index: int = None,
    model: str = None,
    response: str = None
) -> Dict:
    """创建并保存一个新的 Episode

    session_id / round_index / model / response 是会话（分级记忆）字段：
    即使为 None 也会写入实体与索引（response 只在实体），保持形状稳定，
    便于后续 update_episode_fields() 回填。老调用方不传这些参数也不受影响。
    """
    episode_id = generate_episode_id()

    episode = {
        "id": episode_id,
        "timestamp": datetime.now().isoformat(),
        "task": task,
        "qubit": qubit,
        "mode": mode,
        "steps": steps,
        "status": status,
        "metrics": metrics or {},
        "tags": tags or [],
        "usedSkills": used_skills or [],
        "sessionId": session_id,
        "roundIndex": round_index,
        "model": model,
        "response": response,
        "reflection": None,      # 反思报告，稍后填充
        "skillCreated": None,   # 如果生成了技能，记录 ID
        "archived": False,
        "lifecycle": LIFECYCLE_ACTIVE,   # P4-T7 生命周期：active / archived
        "promoted_to": None              # P4-T7 提升：沉淀成的 skill id（未提升为 None）
    }

    # 实体文件与索引在同一次加锁内写完，避免出现"有文件、无索引"的孤儿
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")

    def _append_to_index(index: Dict):
        index["episodes"].insert(0, {
            "id": episode_id,
            "timestamp": episode["timestamp"],
            "qubit": qubit,
            "status": status,
            "sessionId": session_id,
            "roundIndex": round_index,
            "task_preview": task[:50] if len(task) > 50 else task
        })

    with _INDEX_LOCK:
        with open(episode_file, 'w', encoding='utf-8') as f:
            json.dump(episode, f, indent=2, ensure_ascii=False)
        _update_index(_append_to_index)

    print(f"MemoryStore: Created episode {episode_id}", flush=True)
    return episode


def get_episode(episode_id: str) -> Optional[Dict]:
    """获取单个 Episode 详情。

    读取口对老数据做兼容：缺失的 lifecycle / archived / promoted_to 按
    active / False / None 补齐（见 _normalize_episode）。
    """
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")
    if not os.path.exists(episode_file):
        return None
    try:
        with open(episode_file, 'r', encoding='utf-8') as f:
            return _normalize_episode(json.load(f))
    except Exception as e:
        print(f"MemoryStore: Failed to load episode {episode_id}: {e}")
        return None


def update_episode_reflection(episode_id: str, reflection: Dict):
    """更新 Episode 的反思报告"""
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")

    # 与 archive_episode 同为实体文件的读-改-写，放在同一把锁内避免丢更新
    with _INDEX_LOCK:
        episode = get_episode(episode_id)
        if not episode:
            return False

        episode["reflection"] = reflection

        with open(episode_file, 'w', encoding='utf-8') as f:
            json.dump(episode, f, indent=2, ensure_ascii=False)

    return True


def update_episode_fields(episode_id: str, **fields) -> bool:
    """通用的 Episode 字段读-改-写：把 fields 合并进实体并写回。

    与 update_episode_reflection / archive_episode 同为实体文件的读-改-写，
    持 _INDEX_LOCK 互斥，避免并发丢更新。episode 不存在（或文件缺失）返回 False。
    只合并实体文件，不动 index 摘要条目。
    """
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")

    with _INDEX_LOCK:
        episode = get_episode(episode_id)
        if not episode:
            return False

        episode.update(fields)

        with open(episode_file, 'w', encoding='utf-8') as f:
            json.dump(episode, f, indent=2, ensure_ascii=False)

    return True


def list_episodes(
    limit: int = 50,
    qubit: str = None,
    status: str = None,
    include_archived: bool = False
) -> List[Dict]:
    """列出 Episodes（轻量列表，不包含完整 steps）"""
    index = _load_index()

    episodes = index.get("episodes", [])

    # 过滤
    if qubit:
        episodes = [e for e in episodes if e.get("qubit") == qubit]
    if status:
        episodes = [e for e in episodes if e.get("status") == status]
    if not include_archived:
        # 需要检查文件是否 archived
        filtered = []
        for e in episodes:
            ep = get_episode(e["id"])
            if ep and not ep.get("archived", False):
                filtered.append(e)
        episodes = filtered

    # 返回最新的
    return episodes[:limit]


def archive_episode(episode_id: str) -> bool:
    """归档 Episode（软删除）

    实体保留（archived=True / lifecycle="archived"，两字段同步），索引条目移除。
    """
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")

    with _INDEX_LOCK:
        episode = get_episode(episode_id)
        if not episode:
            return False

        episode["archived"] = True
        episode["lifecycle"] = LIFECYCLE_ARCHIVED
        episode["archivedAt"] = datetime.now().isoformat()

        with open(episode_file, 'w', encoding='utf-8') as f:
            json.dump(episode, f, indent=2, ensure_ascii=False)

        # 从索引移除
        def _drop_from_index(index: Dict):
            index["episodes"] = [e for e in index["episodes"] if e["id"] != episode_id]

        _update_index(_drop_from_index)

    return True


def run_maintenance(archive_after_days: int = 90, max_archived: int = 200) -> Dict:
    """提升 / 遗忘：归档超期 episode，并清理超量归档（P4-T7）。

    a) 归档：lifecycle == "active" 且 timestamp 距今超过 archive_after_days 天
       → lifecycle="archived"、archived=True、archivedAt=now（两字段同步），
       本次新归档的条目从索引移除（与 archive_episode 的既有语义一致）。
       archive_after_days=0 表示"立刻归档全部 active"（边界）。
    b) 清理：归档数 > max_archived 时，按 archivedAt（缺失则 timestamp）从最老开始
       删除；**实体文件与索引条目一起删**，不留索引残行（orphanCount 语义不被破坏）。
       本次删除数累加进 index["meta"]["pruned_total"]。
    c) 全程在一次 _update_index 内完成（持 _INDEX_LOCK 的原子读-改-写）。

    Returns:
        {"archived": 本次新归档数, "pruned": 本次删除数,
         "active": 剩余 active 数, "archived_remaining": 剩余 archived 数}
    """
    now = datetime.now()
    try:
        days = max(0, int(archive_after_days))
    except (TypeError, ValueError):
        days = 90
    try:
        keep = max(0, int(max_archived))
    except (TypeError, ValueError):
        keep = 200
    cutoff = now - timedelta(days=days)

    result = {"archived": 0, "pruned": 0, "active": 0, "archived_remaining": 0}

    def _mutate(index: Dict):
        active_ids: List[Any] = []
        archived: List[Dict] = []
        newly_archived_ids: List[Any] = []

        for fpath in _list_episode_files():
            ep = _read_episode_file(fpath)
            if not ep:
                continue
            if _episode_lifecycle(ep) == LIFECYCLE_ACTIVE:
                ts = _parse_ts(ep.get("timestamp"))
                if ts is not None and ts < cutoff:
                    ep["lifecycle"] = LIFECYCLE_ARCHIVED
                    ep["archived"] = True
                    ep["archivedAt"] = now.isoformat()
                    _write_episode_file(fpath, ep)
                    result["archived"] += 1
                    if ep.get("id"):
                        newly_archived_ids.append(ep["id"])
            if _episode_lifecycle(ep) == LIFECYCLE_ARCHIVED:
                archived.append({"id": ep.get("id"), "path": fpath, "ep": ep})
            else:
                active_ids.append(ep.get("id"))

        # a) 本次新归档的 episode 从索引移除（早已归档的条目不动，prune 时再清）
        if newly_archived_ids:
            drop_new = set(newly_archived_ids)
            index["episodes"] = [e for e in index.get("episodes", [])
                                 if e.get("id") not in drop_new]

        # b) 超量归档：最老的先删（实体 + 索引条目）
        pruned_ids: List[Any] = []
        if len(archived) > keep:
            archived.sort(key=lambda a: str(a["ep"].get("archivedAt")
                                             or a["ep"].get("timestamp") or ""))
            for a in archived[:len(archived) - keep]:
                try:
                    os.remove(a["path"])
                except OSError as e:
                    print(f"MemoryStore: Failed to prune {a['id']}: {e}")
                    continue
                result["pruned"] += 1
                if a["id"]:
                    pruned_ids.append(a["id"])

            # 若被删条目在索引里还有残行，一并清掉（否则会变成孤儿索引行）
            if pruned_ids:
                drop = set(pruned_ids)
                index["episodes"] = [e for e in index.get("episodes", [])
                                     if e.get("id") not in drop]

        if result["pruned"]:
            meta = index.setdefault("meta", {})
            meta["pruned_total"] = int(meta.get("pruned_total", 0) or 0) + result["pruned"]

        result["active"] = len(active_ids)
        result["archived_remaining"] = len(archived) - result["pruned"]

    _update_index(_mutate)

    print(f"MemoryStore: maintenance archived={result['archived']} "
          f"pruned={result['pruned']}", flush=True)
    return result


def get_recent_episodes_for_qubit(qubit: str, limit: int = 5) -> List[Dict]:
    """获取同比特最近的 Episodes"""
    index = _load_index()
    episodes = index.get("episodes", [])
    qubit_episodes = [e for e in episodes if e.get("qubit") == qubit]
    return qubit_episodes[:limit]


def get_failed_episodes(limit: int = 10) -> List[Dict]:
    """获取最近的失败 Episodes（用于分析）"""
    index = _load_index()
    episodes = index.get("episodes", [])
    failed = [e for e in episodes if e.get("status") == "failed"]
    return failed[:limit]


# ── 会话（Session）管理 ──────────────────────────────────────────────────────

def _iter_live_episodes() -> List[Dict]:
    """索引条目 ∧ 实体文件真实存在的完整 episode（按索引顺序去重）。

    与 get_memory_stats() 的有效口径一致：只看索引里有条目且文件还在的。
    """
    index = _load_index()
    out: List[Dict] = []
    seen = set()
    for e in index.get("episodes", []):
        eid = e.get("id")
        if not eid or eid in seen:
            continue
        seen.add(eid)
        ep = get_episode(eid)
        if ep:
            out.append(ep)
    return out


def _has_valid_task(ep: Dict) -> bool:
    """task 是否为有效字符串（非 None / 非纯空白）。

    空 task / 纯空白 task 的 episode 属脏数据：会话列表与会话回放都不该带上它，
    否则 list_sessions 的 rounds 与 get_session_episodes 的条数会对不上。
    """
    task = ep.get("task")
    return isinstance(task, str) and bool(task.strip())


def _session_key(ep: Dict) -> str:
    """episode 所属会话的 key。

    sessionId 缺失 / null / 空 的老数据退化：各自构成单轮会话，
    用该 episode 自己的 id 作为 sessionId。
    """
    sid = ep.get("sessionId")
    return sid if sid else ep.get("id")


def list_sessions(limit: int = 50) -> List[Dict]:
    """按 session 分组的会话摘要列表（最近有更新的会话在前）。

    只统计有效 episode（索引条目且文件真实存在），并过滤 task 为空 / 纯空白的
    脏数据。sessionId 缺失/null 的 episode 各自构成单轮会话（sessionId 取 episode id）。
    """
    groups: Dict[str, List[Dict]] = {}
    for ep in _iter_live_episodes():
        if not _has_valid_task(ep):
            continue
        groups.setdefault(_session_key(ep), []).append(ep)

    sessions: List[Dict] = []
    for sid, rounds in groups.items():
        # 组内按 timestamp 升序（ISO 字符串比较即可），并列时保持索引顺序
        ordered = sorted(rounds, key=lambda r: r.get("timestamp") or "")
        first, last = ordered[0], ordered[-1]

        statuses = {r.get("status") for r in ordered}
        if "success" in statuses and "failed" in statuses:
            status = "mixed"
        else:
            status = last.get("status")

        sessions.append({
            "sessionId": sid,
            "rounds": len(ordered),
            "firstAt": first.get("timestamp"),
            "lastAt": last.get("timestamp"),
            "taskPreview": (first.get("task") or "")[:50],   # 最早一轮 task 截 50 字符
            "qubit": next((r.get("qubit") for r in ordered if r.get("qubit")), None),
            "model": last.get("model"),                      # 最新一轮的 model
            "status": status
        })

    # 会话按 lastAt 倒序，截 limit（并列时保持稳定顺序）
    sessions.sort(key=lambda s: s.get("lastAt") or "", reverse=True)
    return sessions[:limit]


def get_session_episodes(session_id: str) -> List[Dict]:
    """返回一个会话的全部完整 episode（含 steps / response）。

    与 list_sessions 口径一致：过滤 task 为空 / 纯空白的脏数据，保证
    rounds == len(get_session_episodes(session_id)) 的不变量成立。

    排序规则（简单确定，不做混合插入排序）：
    - 组内 roundIndex 全部非 null → 按 roundIndex 升序；
    - 否则（全 null 或混有 null）→ 按 timestamp 升序。
    """
    rounds = [
        ep for ep in _iter_live_episodes()
        if _session_key(ep) == session_id and _has_valid_task(ep)
    ]

    if rounds and all(r.get("roundIndex") is not None for r in rounds):
        rounds.sort(key=lambda r: r.get("roundIndex"))
    else:
        rounds.sort(key=lambda r: r.get("timestamp") or "")
    return rounds


# ── Skill 管理 ──────────────────────────────────────────────────────────────

def generate_skill_id() -> str:
    """生成唯一的 Skill ID"""
    ts = datetime.now().strftime("%Y%m%d")
    short_id = str(uuid.uuid4())[:6]
    return f"skill_{ts}_{short_id}"


def create_skill(
    name: str,
    trigger_keywords: List[str],
    steps: List[Dict],
    source_episode_id: str = None,
    description: str = ""
) -> Dict:
    """创建新技能"""
    skill_id = generate_skill_id()

    skill = {
        "id": skill_id,
        "name": name,
        "description": description,
        "triggerKeywords": trigger_keywords,
        "steps": steps,
        "successCount": 1,
        "failureCount": 0,
        "status": "active",
        "source": "auto_learned",
        "sourceEpisodeId": source_episode_id,
        "createdAt": datetime.now().isoformat(),
        "lastUsed": None,
        "lastSuccessAt": datetime.now().isoformat()
    }

    # 实体文件与索引在同一次加锁内写完，避免出现"有文件、无索引"的孤儿
    skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")

    def _append_to_index(index: Dict):
        index["skills"].insert(0, {
            "id": skill_id,
            "name": name,
            "status": "active"
        })

    with _INDEX_LOCK:
        with open(skill_file, 'w', encoding='utf-8') as f:
            json.dump(skill, f, indent=2, ensure_ascii=False)
        _update_index(_append_to_index)

    print(f"MemoryStore: Created skill {skill_id}: {name}", flush=True)
    return skill


def get_skill(skill_id: str) -> Optional[Dict]:
    """获取技能详情"""
    skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")
    if not os.path.exists(skill_file):
        return None
    try:
        with open(skill_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"MemoryStore: Failed to load skill {skill_id}: {e}")
        return None


def list_skills(status: str = None) -> List[Dict]:
    """列出所有技能

    只返回**文件真实存在**的条目，与 get_memory_stats() 的 skillCount 口径一致。
    （原实现直接返回索引条目，索引有残留而文件缺失时会与 stats 不一致。）
    """
    index = _load_index()
    skills = [s for s in index.get("skills", [])
              if s.get("id") and os.path.isfile(os.path.join(SKILLS_DIR, f"{s['id']}.json"))]

    if status:
        # 需要加载详情检查状态
        filtered = []
        for s in skills:
            skill = get_skill(s["id"])
            if skill and skill.get("status") == status:
                filtered.append(s)
        return filtered

    return skills


def match_skills(task: str) -> List[Dict]:
    """根据任务描述匹配技能，并按相关度排序（P3-T6 召回升级）。

    排序规则（Python 稳定排序，同分保持索引顺序 = 新技能在前）：
    1. 关键词**命中数**降序（命中越多越相关）
    2. 其次 successRate = successCount / (successCount + failureCount) 降序
       （分母为 0 时按 0.0 处理）

    返回值仍是 list[Dict]（技能实体），签名与旧调用方完全兼容；
    只是顺序从"索引顺序"变成了"相关度顺序"。
    """
    task_lower = (task or "").lower()
    matched = []

    index = _load_index()
    for s in index.get("skills", []):
        skill = get_skill(s["id"])
        if not skill or skill.get("status") != "active":
            continue

        # 检查关键词匹配，并统计命中数
        hits = 0
        for kw in skill.get("triggerKeywords", []):
            if not isinstance(kw, str) or not kw:
                continue
            kw_lower = kw.lower()
            if kw_lower in task_lower or task_lower in kw_lower:
                hits += 1
        if hits:
            matched.append((hits, skill))

    def _success_rate(skill: Dict) -> float:
        sc = skill.get("successCount", 0) or 0
        fc = skill.get("failureCount", 0) or 0
        total = sc + fc
        return (sc / total) if total > 0 else 0.0

    matched.sort(key=lambda item: (-item[0], -_success_rate(item[1])))
    return [skill for _, skill in matched]


def update_skill_usage(skill_id: str, success: bool):
    """更新技能使用统计"""
    skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")

    # 实体文件的读-改-写需要互斥，否则并发调用会丢掉计数
    with _INDEX_LOCK:
        skill = get_skill(skill_id)
        if not skill:
            return

        if success:
            skill["successCount"] = skill.get("successCount", 0) + 1
            skill["lastSuccessAt"] = datetime.now().isoformat()
        else:
            skill["failureCount"] = skill.get("failureCount", 0) + 1

        skill["lastUsed"] = datetime.now().isoformat()

        with open(skill_file, 'w', encoding='utf-8') as f:
            json.dump(skill, f, indent=2, ensure_ascii=False)


def delete_skill(skill_id: str) -> bool:
    """删除技能"""
    skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")

    with _INDEX_LOCK:
        if not os.path.exists(skill_file):
            return False

        os.remove(skill_file)

        # 从索引移除
        def _drop_from_index(index: Dict):
            index["skills"] = [s for s in index["skills"] if s["id"] != skill_id]

        _update_index(_drop_from_index)

    return True


# ── 统计 ──────────────────────────────────────────────────────────────

def get_memory_stats() -> Dict:
    """获取记忆统计

    注意：计数只算**索引里且文件真实存在**的条目。
    原始实现直接 len(index["episodes"])，但 list_episodes() 要求文件存在才返回，
    两者在"索引有条目、文件缺失"时会不一致 —— 前端就会显示"有 3 条却一条都列不出来"。
    本仓库的 config/memory/index.json 就带着 3 条没有文件的历史条目（来自交付包）。
    """
    index = _load_index()

    def _live(entries, directory):
        n = 0
        for e in entries:
            eid = e.get("id")
            if eid and os.path.isfile(os.path.join(directory, f"{eid}.json")):
                n += 1
        return n

    all_episodes = index.get("episodes", [])
    all_skills = index.get("skills", [])
    episode_count = _live(all_episodes, EPISODES_DIR)
    skill_count = _live(all_skills, SKILLS_DIR)
    orphan_count = (len(all_episodes) - episode_count) + (len(all_skills) - skill_count)

    # 计算存储大小
    total_size = 0
    for dir_path in [EPISODES_DIR, SKILLS_DIR]:
        if os.path.exists(dir_path):
            for f in os.listdir(dir_path):
                fp = os.path.join(dir_path, f)
                if os.path.isfile(fp):
                    total_size += os.path.getsize(fp)

    # 成功率统计（同样只算文件存在的）
    live_episodes = [e for e in all_episodes
                     if e.get("id") and os.path.isfile(os.path.join(EPISODES_DIR, f"{e['id']}.json"))]
    success_count = sum(1 for e in live_episodes if e.get("status") == "success")
    success_rate = success_count / len(live_episodes) if live_episodes else 0

    # 生命周期计数（P4-T7）：直接扫实体文件 —— 归档后实体保留、索引条目移除，
    # 只看索引会漏掉全部 archived。老数据无 lifecycle 按 active 计。
    lifecycle = {"active": 0, "archived": 0, "pruned": 0}
    for fpath in _list_episode_files():
        ep = _read_episode_file(fpath)
        if not ep:
            continue
        lifecycle[_episode_lifecycle(ep)] += 1
    lifecycle["pruned"] = int((index.get("meta") or {}).get("pruned_total", 0) or 0)

    return {
        "episodeCount": episode_count,
        "skillCount": skill_count,
        "orphanCount": orphan_count,
        "storageBytes": total_size,
        "storageMB": round(total_size / (1024 * 1024), 2),
        "successRate": round(success_rate * 100, 1),
        "lifecycle": lifecycle,
        "lastUpdated": index.get("last_updated")
    }


def init_memory():
    """初始化记忆存储"""
    os.makedirs(EPISODES_DIR, exist_ok=True)
    os.makedirs(SKILLS_DIR, exist_ok=True)

    if not os.path.exists(INDEX_FILE):
        _save_index({"episodes": [], "skills": [], "last_updated": None})

    print(f"MemoryStore: Initialized at {MEMORY_BASE}", flush=True)


# 自动初始化
init_memory()
