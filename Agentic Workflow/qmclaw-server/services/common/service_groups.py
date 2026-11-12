"""服务进程组定义（端口税，路线图 §3 #6）。

现状：11 个微服务 = 11 个进程 + 10 个端口，改一处 `dsl/` 就要单独重启 `:3008`，
运维面太大。本模块给出"哪些服务可以合进同一个进程"的唯一事实来源，供
`scripts/start_group.py` 读取——启动脚本不再自己抄一份端口表。

判定"可合并"的三个条件（缺一不可）：

1. **无独占设备句柄**：`quantum`/`analysis` 各自持有 LabRAD 会话与 Ray 连接，
   合进一个进程后任何一边崩溃都会拖垮另一边，且 Ray 初始化在沙箱/受限环境会挂住。
2. **无全局单例副作用**：`llm`/`hermes`/`qca` 在 import 时读 `.env`、建模型客户端，
   同一进程里重复初始化会有互相覆盖的配置风险（`hermes` 还有 cron 线程）。
3. **端口不冲突**：同组内端口必须互不相同（`ports_of()` 会校验）。

当前只把 `workflow`（:3008）与 `task_queue`（:3009）标为可合并：两者都是纯
HTTP + 文件/内存状态，改 `dsl/` 或账本逻辑时一起重启，省一次手工操作。
**端口号一个都不改**——对外契约保持兼容；真正的"减端口"需要网关式路径前缀
（例如 `/workflows/*` 与 `/task/*` 合到 :3008），那属于契约变更，另行授权。

纯标准库、无副作用，可在自检里直接 import。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# 服务名 → 端口。与 scripts/start_all_services.py 的 SERVICES 保持一致；
# 自检 `_phase17_check.py` 会比对两处，防漂移。
SERVICE_PORTS: Dict[str, int] = {
    "quantum": 3003,
    "analysis": 3004,
    "agent": 3005,
    "llm": 3006,
    "image": 3007,
    "workflow": 3008,
    "task_queue": 3009,
    "qubitclient": 3010,
    "qca": 3011,
    "hermes": 3012,
}

# 服务名 → (模块路径, 类名, 构造参数名)。只有要"同进程启动"的服务才需要这一项。
SERVICE_ENTRYPOINTS: Dict[str, Tuple[str, str]] = {
    "workflow": ("services.workflow_service.server", "WorkflowService"),
    "task_queue": ("services.task_queue.server", "TaskQueueService"),
}

# 不能合进同一进程的理由（写清楚原因，别让后人靠猜）。
MERGE_BLOCKERS: Dict[str, str] = {
    "quantum": "独占 LabRAD 会话 + Ray 连接；崩溃会拖垮同进程其它服务",
    "analysis": "独占 LabRAD 会话；原生可执行后端不是线程安全的",
    "llm": "import 时读 .env 并建模型客户端，重初始化会互相覆盖配置",
    "agent": "持有 LLM 客户端与长任务线程池，与其它服务共享线程池会争用",
    "image": "依赖可选的重型图像库，缺库时会让整个进程起不来",
    "qubitclient": "依赖 VLM 客户端与外部网络超时策略",
    "qca": "内含 LLM 决策循环，长阻塞调用会占住同进程的 GIL/线程",
    "hermes": "自带 cron 线程与技能目录扫描，生命周期独立",
}

# 进程组：名字 → 成员。`merged=True` 表示可以同进程启动（见 start_group.py）。
GROUPS: Dict[str, Dict[str, object]] = {
    "core": {
        "members": ["workflow", "task_queue"],
        "merged": True,
        "description": "工作流 + 任务队列（改 dsl/ 或账本时一起重启）",
    },
    "lab": {
        "members": ["quantum", "analysis"],
        "merged": False,
        "description": "测控 + 分析（设备独占，只能各自独立进程）",
    },
}


def known_services() -> List[str]:
    """所有登记过的服务名（按端口升序，输出稳定）。"""
    return sorted(SERVICE_PORTS, key=lambda n: SERVICE_PORTS[n])


def port_of(service: str) -> Optional[int]:
    return SERVICE_PORTS.get(service)


def group_members(name: str) -> List[str]:
    """组名 → 成员列表；未知组返回空列表（不抛异常）。"""
    group = GROUPS.get(str(name or "").strip().lower())
    if not group:
        return []
    return list(group.get("members") or [])


def is_mergeable(service: str) -> bool:
    """该服务能否与其它服务同进程运行。"""
    return service in SERVICE_ENTRYPOINTS and service not in MERGE_BLOCKERS


def ports_of(members: List[str]) -> Dict[str, Optional[int]]:
    return {name: SERVICE_PORTS.get(name) for name in members}


def conflicts(members: List[str]) -> List[str]:
    """同组内端口冲突/未登记的服务名（空列表表示没问题）。"""
    seen: Dict[int, str] = {}
    problems: List[str] = []
    for name in members:
        port = SERVICE_PORTS.get(name)
        if port is None:
            problems.append(f"{name}: 未登记端口")
            continue
        if port in seen:
            problems.append(f"{name}: 端口 {port} 与 {seen[port]} 冲突")
        else:
            seen[port] = name
    return problems


def plan_group(name: str) -> Dict[str, object]:
    """给启动器用的执行计划：成员、端口、是否同进程、被阻塞的理由。

    纯函数，不启动任何进程——自检据此验证"计划"与"定义"一致。
    """
    members = group_members(name)
    group = GROUPS.get(str(name or "").strip().lower()) or {}
    merged = bool(group.get("merged")) and all(is_mergeable(m) for m in members)
    blockers = {m: MERGE_BLOCKERS[m] for m in members if m in MERGE_BLOCKERS}
    return {
        "group": str(name or "").strip().lower(),
        "members": members,
        "ports": ports_of(members),
        "merged": merged,
        "blockers": blockers,
        "conflicts": conflicts(members),
        "known": bool(members),
    }


def describe_groups() -> List[Dict[str, object]]:
    """所有组的可读摘要（顺序稳定，便于文档/自检对照）。"""
    out: List[Dict[str, object]] = []
    for name in sorted(GROUPS):
        plan = plan_group(name)
        out.append(
            {
                "group": name,
                "members": plan["members"],
                "ports": plan["ports"],
                "merged": plan["merged"],
                "description": GROUPS[name].get("description", ""),
            }
        )
    return out
