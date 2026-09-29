"""
Memory Store - 长期记忆存储模块

提供 Episode（任务记忆）和 Skill（技能）的持久化存储。
使用 JSON 文件存储，便于用户查看和编辑。

目录结构:
    config/memory/
    ├── episodes/      # 每次任务的完整记录
    ├── skills/       # 自动学习的技能
    └── index.json    # 索引（快速查询用）

来源说明：本模块由 scripts/memory_store.py 移植而来（原文件是微服务化之前
monolithic job_runner.py 的遗留物，现无任何活跃路径引用）。
磁盘格式与原版**完全一致**，因此两种模式的数据可以互相读取。
"""

import os
import json
import uuid
import threading
from datetime import datetime
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
    mode: str = "react"
) -> Dict:
    """创建并保存一个新的 Episode"""
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
        "reflection": None,      # 反思报告，稍后填充
        "skillCreated": None,   # 如果生成了技能，记录 ID
        "archived": False
    }

    # 实体文件与索引在同一次加锁内写完，避免出现"有文件、无索引"的孤儿
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")

    def _append_to_index(index: Dict):
        index["episodes"].insert(0, {
            "id": episode_id,
            "timestamp": episode["timestamp"],
            "qubit": qubit,
            "status": status,
            "task_preview": task[:50] if len(task) > 50 else task
        })

    with _INDEX_LOCK:
        with open(episode_file, 'w', encoding='utf-8') as f:
            json.dump(episode, f, indent=2, ensure_ascii=False)
        _update_index(_append_to_index)

    print(f"MemoryStore: Created episode {episode_id}", flush=True)
    return episode


def get_episode(episode_id: str) -> Optional[Dict]:
    """获取单个 Episode 详情"""
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")
    if not os.path.exists(episode_file):
        return None
    try:
        with open(episode_file, 'r', encoding='utf-8') as f:
            return json.load(f)
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
    """归档 Episode（软删除）"""
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")

    with _INDEX_LOCK:
        episode = get_episode(episode_id)
        if not episode:
            return False

        episode["archived"] = True
        episode["archivedAt"] = datetime.now().isoformat()

        with open(episode_file, 'w', encoding='utf-8') as f:
            json.dump(episode, f, indent=2, ensure_ascii=False)

        # 从索引移除
        def _drop_from_index(index: Dict):
            index["episodes"] = [e for e in index["episodes"] if e["id"] != episode_id]

        _update_index(_drop_from_index)

    return True


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
    """根据任务描述匹配技能"""
    task_lower = task.lower()
    matched = []

    index = _load_index()
    for s in index.get("skills", []):
        skill = get_skill(s["id"])
        if not skill or skill.get("status") != "active":
            continue

        # 检查关键词匹配
        for kw in skill.get("triggerKeywords", []):
            if kw.lower() in task_lower or task_lower in kw.lower():
                matched.append(skill)
                break

    return matched


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

    return {
        "episodeCount": episode_count,
        "skillCount": skill_count,
        "orphanCount": orphan_count,
        "storageBytes": total_size,
        "storageMB": round(total_size / (1024 * 1024), 2),
        "successRate": round(success_rate * 100, 1),
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
