"""
Memory Store - 长期记忆存储模块

提供 Episode（任务记忆）和 Skill（技能）的持久化存储。
使用 JSON 文件存储，便于用户查看和编辑。

目录结构:
    config/memory/
    ├── episodes/      # 每次任务的完整记录
    ├── skills/       # 自动学习的技能
    └── index.json    # 索引（快速查询用）
"""

import os
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

# 记忆存储根目录
MEMORY_BASE = os.path.join(os.path.dirname(__file__), "..", "config", "memory")
EPISODES_DIR = os.path.join(MEMORY_BASE, "episodes")
SKILLS_DIR = os.path.join(MEMORY_BASE, "skills")
INDEX_FILE = os.path.join(MEMORY_BASE, "index.json")

# 确保目录存在
os.makedirs(EPISODES_DIR, exist_ok=True)
os.makedirs(SKILLS_DIR, exist_ok=True)


def _load_index() -> Dict:
    """加载索引文件"""
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
    """保存索引文件"""
    index["last_updated"] = datetime.now().isoformat()
    try:
        with open(INDEX_FILE, 'w', encoding='utf-8') as f:
            json.dump(index, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"MemoryStore: Failed to save index: {e}")


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

    # 保存到文件
    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")
    with open(episode_file, 'w', encoding='utf-8') as f:
        json.dump(episode, f, indent=2, ensure_ascii=False)

    # 更新索引
    index = _load_index()
    index["episodes"].insert(0, {
        "id": episode_id,
        "timestamp": episode["timestamp"],
        "qubit": qubit,
        "status": status,
        "task_preview": task[:50] if len(task) > 50 else task
    })
    _save_index(index)

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
    episode = get_episode(episode_id)
    if not episode:
        return False

    episode["reflection"] = reflection

    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")
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
    episode = get_episode(episode_id)
    if not episode:
        return False

    episode["archived"] = True
    episode["archivedAt"] = datetime.now().isoformat()

    episode_file = os.path.join(EPISODES_DIR, f"{episode_id}.json")
    with open(episode_file, 'w', encoding='utf-8') as f:
        json.dump(episode, f, indent=2, ensure_ascii=False)

    # 从索引移除
    index = _load_index()
    index["episodes"] = [e for e in index["episodes"] if e["id"] != episode_id]
    _save_index(index)

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

    # 保存到文件
    skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")
    with open(skill_file, 'w', encoding='utf-8') as f:
        json.dump(skill, f, indent=2, ensure_ascii=False)

    # 更新索引
    index = _load_index()
    index["skills"].insert(0, {
        "id": skill_id,
        "name": name,
        "status": "active"
    })
    _save_index(index)

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
    """列出所有技能"""
    index = _load_index()
    skills = index.get("skills", [])

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
    skill = get_skill(skill_id)
    if not skill:
        return

    if success:
        skill["successCount"] = skill.get("successCount", 0) + 1
        skill["lastSuccessAt"] = datetime.now().isoformat()
    else:
        skill["failureCount"] = skill.get("failureCount", 0) + 1

    skill["lastUsed"] = datetime.now().isoformat()

    skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")
    with open(skill_file, 'w', encoding='utf-8') as f:
        json.dump(skill, f, indent=2, ensure_ascii=False)


def delete_skill(skill_id: str) -> bool:
    """删除技能"""
    skill_file = os.path.join(SKILLS_DIR, f"{skill_id}.json")
    if not os.path.exists(skill_file):
        return False

    os.remove(skill_file)

    # 从索引移除
    index = _load_index()
    index["skills"] = [s for s in index["skills"] if s["id"] != skill_id]
    _save_index(index)

    return True


# ── 统计 ──────────────────────────────────────────────────────────────

def get_memory_stats() -> Dict:
    """获取记忆统计"""
    index = _load_index()

    episode_count = len(index.get("episodes", []))
    skill_count = len(index.get("skills", []))

    # 计算存储大小
    total_size = 0
    for dir_path in [EPISODES_DIR, SKILLS_DIR]:
        if os.path.exists(dir_path):
            for f in os.listdir(dir_path):
                fp = os.path.join(dir_path, f)
                if os.path.isfile(fp):
                    total_size += os.path.getsize(fp)

    # 成功率统计
    episodes = index.get("episodes", [])
    success_count = sum(1 for e in episodes if e.get("status") == "success")
    success_rate = success_count / len(episodes) if episodes else 0

    return {
        "episodeCount": episode_count,
        "skillCount": skill_count,
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
