"""
services/hermes_service/skills.py - QMClaw Skills 动态管理模块

支持 Skills 的列表、启用/禁用、查看内容。
Skills 存储在 data/skills/ 目录下。
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class SkillInfo:
    """技能信息"""
    name: str
    category: str
    description: str
    content: str  # 完整内容
    path: str
    enabled: bool = True
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    is_custom: bool = False  # 是否用户自定义


@dataclass
class SkillsState:
    """技能状态"""
    skills: list[SkillInfo] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.skills)

    @property
    def enabled_count(self) -> int:
        return sum(1 for s in self.skills if s.enabled)

    @property
    def custom_count(self) -> int:
        return sum(1 for s in self.skills if s.is_custom)

    def by_category(self) -> dict[str, list[SkillInfo]]:
        cats: dict[str, list[SkillInfo]] = {}
        for s in self.skills:
            cats.setdefault(s.category, []).append(s)
        return cats

    def category_counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.by_category().items()}


def _get_data_dir() -> Path:
    """获取 QMClaw Skills 数据目录"""
    current = Path(__file__).parent  # hermes_service
    current = current.parent  # services
    current = current.parent  # qmclaw-server
    data_dir = current / "data" / "skills"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _get_config_path() -> Path:
    """获取 Skills 配置路径"""
    current = Path(__file__).parent  # hermes_service
    current = current.parent  # services
    current = current.parent  # qmclaw-server
    config_dir = current / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "skills_config.json"


def _parse_skill_md(path: Path) -> dict:
    """从 SKILL.md 文件提取内容"""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return {"description": "", "content": ""}

    info = {"description": "", "content": content}

    # 提取 YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for line in fm.split("\n"):
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip().strip("'\"")
                if key in ("name", "description", "version", "author"):
                    info[key] = val

    # 如果没有 description，从第一段非标题内容提取
    if "description" not in info or not info["description"]:
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                info["description"] = stripped[:120]
                break

    return info


def _detect_custom(skill_mtime: datetime, bulk_timestamps: set[int]) -> bool:
    """检测是否为用户自定义技能"""
    skill_minute = int(skill_mtime.timestamp()) // 60
    return skill_minute not in bulk_timestamps


def collect_skills() -> SkillsState:
    """收集所有 Skills"""
    skills_dir = _get_data_dir()
    config_path = _get_config_path()

    # 加载配置（启用状态等）
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}

    enabled_skills = config.get("enabled_skills", [])
    disabled_skills = config.get("disabled_skills", [])

    # 收集所有 SKILL.md 文件
    skills: list[SkillInfo] = []
    mtimes: list[int] = []

    for skill_md in skills_dir.rglob("SKILL.md"):
        stat = skill_md.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime)
        mtime_minute = int(stat.st_mtime) // 60

        # 解析路径获取 category 和 name
        rel = skill_md.relative_to(skills_dir)
        parts = rel.parts[:-1]  # 移除 SKILL.md
        if len(parts) >= 1:
            category = parts[0]
            name = parts[-1]
        else:
            category = "uncategorized"
            name = skill_md.stem

        # 解析内容
        meta = _parse_skill_md(skill_md)

        # 检查启用状态
        skill_name = meta.get("name", name)
        enabled = skill_name not in disabled_skills

        skills.append(SkillInfo(
            name=skill_name,
            category=category,
            description=meta.get("description", ""),
            content=meta.get("content", ""),
            path=str(skill_md),
            enabled=enabled,
            created_at=datetime.fromtimestamp(stat.st_ctime),
            modified_at=mtime,
            is_custom=False,  # 暂不支持自定义检测
        ))
        mtimes.append(mtime_minute)

    # 检测批量安装时间戳
    if mtimes:
        from collections import Counter
        counter = Counter(mtimes)
        bulk_timestamps = {t for t, count in counter.items() if count >= 3}
        for skill in skills:
            if skill.modified_at:
                skill.is_custom = _detect_custom(skill.modified_at, bulk_timestamps)

    return SkillsState(skills=skills)


def get_skill_content(name: str) -> dict:
    """获取单个 Skill 的内容"""
    state = collect_skills()
    for skill in state.skills:
        if skill.name == name:
            return {
                "ok": True,
                "skill": {
                    "name": skill.name,
                    "category": skill.category,
                    "description": skill.description,
                    "content": skill.content,
                    "path": skill.path,
                    "enabled": skill.enabled,
                    "modified_at": skill.modified_at.isoformat() if skill.modified_at else None,
                }
            }
    return {"ok": False, "error": f"Skill not found: {name}"}


def toggle_skill(name: str, enabled: bool) -> dict:
    """启用/禁用 Skill"""
    config_path = _get_config_path()
    config: dict = {}
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            config = {}

    disabled_skills = set(config.get("disabled_skills", []))
    enabled_skills = set(config.get("enabled_skills", []))

    if enabled:
        disabled_skills.discard(name)
        enabled_skills.add(name)
    else:
        disabled_skills.add(name)
        enabled_skills.discard(name)

    config["disabled_skills"] = list(disabled_skills)
    config["enabled_skills"] = list(enabled_skills)

    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"ok": True, "name": name, "enabled": enabled}


def get_skills_state() -> dict:
    """获取完整 Skills 状态"""
    state = collect_skills()

    # 转换 by_category 中的 SkillInfo 为字典
    by_category_serializable = {}
    for cat, skill_list in state.by_category().items():
        by_category_serializable[cat] = [
            {
                "name": s.name,
                "category": s.category,
                "description": s.description,
                "enabled": s.enabled,
                "is_custom": s.is_custom,
                "modified_at": s.modified_at.isoformat() if s.modified_at else None,
            }
            for s in skill_list
        ]

    return {
        "skills": [
            {
                "name": s.name,
                "category": s.category,
                "description": s.description,
                "enabled": s.enabled,
                "is_custom": s.is_custom,
                "modified_at": s.modified_at.isoformat() if s.modified_at else None,
            }
            for s in state.skills
        ],
        "total": state.total,
        "enabled_count": state.enabled_count,
        "custom_count": state.custom_count,
        "by_category": by_category_serializable,
        "category_counts": state.category_counts(),
    }
