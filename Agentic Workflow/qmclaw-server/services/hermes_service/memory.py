"""
services/hermes_service/memory.py - QMClaw 记忆管理模块

基于 hermes-hudui 的记忆管理实现，但使用 QMClaw 独立存储。
支持 Agent 记忆和用户画像两种类型。
"""

import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

# 记忆条目分隔符
ENTRY_DELIMITER = "\n§\n"

# 记忆目标类型
MemoryTarget = Literal["memory", "user"]

# 默认容量限制
MEMORY_MAX_CHARS = 2200
USER_MAX_CHARS = 1375

# 分类模式
CORRECTION_PATTERNS = [
    r"gotcha", r"don't", r"caught", r"wrong", r"verify before",
    r"supersedes", r"not usable", r"doesn't work", r"won't help",
    r"not yet confirmed", r"was stuck", r"may need manual",
]

ENVIRONMENT_PATTERNS = [
    r"WSL", r"Ubuntu", r"installed", r"configured", r"version",
    r"SSD", r"GPU", r"RTX", r"backend", r"systemd", r"API_KEY",
    r"provider", r"build:", r"tok/s",
]

TODO_PATTERNS = [
    r"TODO:", r"needs to", r"not yet",
]

PROJECT_PATTERNS = [
    r"project", r"~/projects/", r"repo", r"agent",
]

PREFERENCE_PATTERNS = [
    r"preferred", r"expects", r"familiar with", r"interested in",
    r"push back", r"voice-to-text", r"phonetic", r"platform:",
    r"switched to", r"long-time", r"default model",
]

# 平台检测和锁实现
import platform
_IS_WINDOWS = platform.system() == "Windows"

if _IS_WINDOWS:
    try:
        import portalocker
        _HAS_PORTALOCKER = True
    except ImportError:
        _HAS_PORTALOCKER = False
else:
    import fcntl
    _HAS_PORTALOCKER = False

# 线程锁（跨平台保底）
_lock = threading.Lock()


@dataclass
class MemoryEntry:
    """记忆条目"""
    text: str
    category: str  # environment, correction, preference, project, todo, other
    char_count: int = 0

    def __post_init__(self):
        self.char_count = len(self.text)


@dataclass
class MemoryState:
    """记忆状态"""
    entries: list[MemoryEntry] = field(default_factory=list)
    total_chars: int = 0
    max_chars: int = 0
    source: str = ""  # "memory" or "user"

    @property
    def capacity_pct(self) -> float:
        """容量百分比"""
        return (self.total_chars / self.max_chars * 100) if self.max_chars > 0 else 0

    @property
    def entry_count(self) -> int:
        """条目数量"""
        return len(self.entries)

    def count_by_category(self) -> dict[str, int]:
        """按分类统计"""
        from collections import Counter
        return dict(Counter(e.category for e in self.entries))


def _get_data_dir() -> Path:
    """获取 QMClaw 数据目录"""
    # 从 hermes_service/server.py 到 qmclaw-server 需要向上 4 层
    current = Path(__file__).parent  # hermes_service
    current = current.parent  # services
    current = current.parent  # qmclaw-server
    data_dir = current / "data" / "memories"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _memory_path(target: MemoryTarget) -> Path:
    """返回 MEMORY.md 或 USER.md 的路径"""
    data_dir = _get_data_dir()
    if target == "user":
        return data_dir / "USER.md"
    return data_dir / "MEMORY.md"


def _lock_path(target: MemoryTarget) -> Path:
    """返回 .md.lock 文件路径"""
    return _memory_path(target).with_suffix(".md.lock")


def _categorize(text: str, source: str) -> str:
    """对记忆条目进行分类"""
    lower = text.lower()

    # 1. 修正最高优先级
    for p in CORRECTION_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return "correction"

    # 2. 用户偏好 (仅 user source)
    if source == "user":
        for p in PREFERENCE_PATTERNS:
            if re.search(p, text, re.IGNORECASE):
                return "preference"

    # 3. TODO
    for p in TODO_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return "todo"

    # 4. 项目
    for p in PROJECT_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return "project"

    # 5. 环境
    for p in ENVIRONMENT_PATTERNS:
        if re.search(p, text, re.IGNORECASE):
            return "environment"

    return "other"


def _read_entries(target: MemoryTarget) -> list[str]:
    """从记忆文件读取并分割条目"""
    path = _memory_path(target)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return []
    if not content:
        return []
    return [p.strip() for p in content.split("§") if p.strip()]


def _write_entries(target: MemoryTarget, entries: list[str]) -> None:
    """原子写入条目到记忆文件"""
    path = _memory_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = ENTRY_DELIMITER.join(entries) + "\n" if entries else ""

    # 原子写入：先写临时文件，再 rename
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(tmp, str(path))
    except Exception:
        if fd >= 0:
            os.close(fd)
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _with_lock(target: MemoryTarget, fn):
    """持有记忆文件锁时执行 fn（跨平台）"""
    lock = _lock_path(target)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.touch(exist_ok=True)

    # 使用线程锁保底（确保跨平台）
    with _lock:
        if _IS_WINDOWS and _HAS_PORTALOCKER:
            # Windows + portalocker
            try:
                with open(lock, 'r+b') as lf:
                    portalocker.lock(lf, portalocker.LOCK_EX)
                    return fn()
            except Exception:
                # fallback: 只用线程锁
                return fn()
        elif not _IS_WINDOWS:
            # Unix: 使用 fcntl
            with open(lock, 'r') as lf:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                return fn()
        else:
            # 其他情况只用线程锁
            return fn()


def collect_memory() -> tuple[MemoryState, MemoryState]:
    """收集记忆和用户画像状态

    Returns:
        (memory_state, user_state)
    """
    memory_entries = []
    memory_content = ""

    # 解析 MEMORY.md
    memory_path = _memory_path("memory")
    if memory_path.exists():
        memory_content = memory_path.read_text(encoding="utf-8")
        raw_entries = [p.strip() for p in memory_content.split("§") if p.strip()]
        memory_entries = [MemoryEntry(text=p, category=_categorize(p, "memory")) for p in raw_entries]

    memory_state = MemoryState(
        entries=memory_entries,
        total_chars=len(memory_content),
        max_chars=MEMORY_MAX_CHARS,
        source="memory",
    )

    # 解析 USER.md
    user_entries = []
    user_content = ""

    user_path = _memory_path("user")
    if user_path.exists():
        user_content = user_path.read_text(encoding="utf-8")
        raw_entries = [p.strip() for p in user_content.split("§") if p.strip()]
        user_entries = [MemoryEntry(text=p, category=_categorize(p, "user")) for p in raw_entries]

    user_state = MemoryState(
        entries=user_entries,
        total_chars=len(user_content),
        max_chars=USER_MAX_CHARS,
        source="user",
    )

    return memory_state, user_state


def add_entry(target: MemoryTarget, content: str) -> dict:
    """添加新记忆条目"""
    content = content.strip()
    if not content:
        raise ValueError("content cannot be empty")

    def do():
        entries = _read_entries(target)
        for e in entries:
            if e == content:
                raise ValueError("Duplicate entry")
        entries.append(content)
        _write_entries(target, entries)
        return {"ok": True, "entry_count": len(entries)}

    return _with_lock(target, do)


def edit_entry(target: MemoryTarget, old_text: str, new_content: str) -> dict:
    """编辑记忆条目 (通过 old_text 子串匹配)"""
    new_content = new_content.strip()
    if not new_content:
        raise ValueError("content cannot be empty")

    def do():
        entries = _read_entries(target)
        matches = [i for i, e in enumerate(entries) if old_text in e]
        if not matches:
            raise ValueError("No entry matches old_text")
        if len(matches) > 1:
            raise ValueError("Multiple entries match — use a more specific old_text")
        entries[matches[0]] = new_content
        _write_entries(target, entries)
        return {"ok": True, "entry_count": len(entries)}

    return _with_lock(target, do)


def delete_entry(target: MemoryTarget, old_text: str) -> dict:
    """删除记忆条目 (通过 old_text 子串匹配)"""
    def do():
        entries = _read_entries(target)
        matches = [i for i, e in enumerate(entries) if old_text in e]
        if not matches:
            raise ValueError("No entry matches old_text")
        if len(matches) > 1:
            raise ValueError("Multiple entries match — use a more specific old_text")
        entries.pop(matches[0])
        _write_entries(target, entries)
        return {"ok": True, "entry_count": len(entries)}

    return _with_lock(target, do)


def get_memory_state() -> dict:
    """获取完整记忆状态"""
    memory, user = collect_memory()
    return {
        "memory": {
            "entries": [{"text": e.text, "category": e.category, "char_count": e.char_count} for e in memory.entries],
            "total_chars": memory.total_chars,
            "max_chars": memory.max_chars,
            "source": memory.source,
            "capacity_pct": memory.capacity_pct,
            "entry_count": memory.entry_count,
            "count_by_category": memory.count_by_category(),
        },
        "user": {
            "entries": [{"text": e.text, "category": e.category, "char_count": e.char_count} for e in user.entries],
            "total_chars": user.total_chars,
            "max_chars": user.max_chars,
            "source": user.source,
            "capacity_pct": user.capacity_pct,
            "entry_count": user.entry_count,
            "count_by_category": user.count_by_category(),
        },
    }
