"""
Memory Store - **遗留兼容层**（G3：单实现收敛）

权威实现只有一个：
    <qmclaw-server>/services/common/memory_store.py

本文件不再包含任何实现代码，只做两件事：
1) 把 <qmclaw-server> 挂到 sys.path，然后 `from services.common.memory_store import *`；
2) 显式重导出原脚本的公开名字，保证老的 `from memory_store import ...`
   （scripts/reflection_engine.py、scripts/job_runner.py 等）继续可用。

这样两种导入路径读写的是同一份实现、同一份磁盘格式，不会再出现 schema 分叉
（此前 scripts/ 与 services/common/ 各有一份拷贝，加字段必须改两处）。

注意（路径隔离）：本模块里的 MEMORY_BASE / EPISODES_DIR / SKILLS_DIR / INDEX_FILE
只是从权威模块复制过来的**值**，函数体引用的是权威模块的全局变量。
测试要隔离路径请 monkeypatch `services.common.memory_store` 模块上的同名属性，
改本模块的属性不影响函数行为。
"""

import sys
from pathlib import Path

_SERVER_ROOT = Path(__file__).resolve().parents[1]  # .../qmclaw-server
if str(_SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVER_ROOT))

from services.common.memory_store import *  # noqa: F401,F403,E402

# 显式重导出原脚本的公开名字（可读性 + 静态检查；与上面指向同一批对象）
from services.common.memory_store import (  # noqa: F401,E402
    MEMORY_BASE, EPISODES_DIR, SKILLS_DIR, INDEX_FILE,
    LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED,
    generate_episode_id, create_episode, get_episode,
    update_episode_reflection, update_episode_fields,
    list_episodes, archive_episode, run_maintenance,
    get_recent_episodes_for_qubit, get_failed_episodes,
    list_sessions, get_session_episodes,
    generate_skill_id, create_skill, get_skill, list_skills,
    match_skills, update_skill_usage, delete_skill,
    get_memory_stats, init_memory,
)

# 私有辅助也转发：老代码可能摸过 memory_store._load_index / _update_index 之类，
# 且 memory_manager 的独立脚本回退分支会用 getattr(memory_store, "_iter_live_episodes")。
# 名字以下划线开头，`import *` 不会带过来，必须显式赋值。
from services.common.memory_store import (  # noqa: F401,E402
    _INDEX_LOCK, _load_index, _save_index, _update_index,
    _iter_live_episodes, _episode_lifecycle, _normalize_episode, _parse_ts,
    _list_episode_files, _read_episode_file,
)
