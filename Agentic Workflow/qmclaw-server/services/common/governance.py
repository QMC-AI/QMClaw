"""services/common/governance.py — 治理闸门（纯函数，无副作用）

单写者原则（docs/ARCHITECTURE_ROADMAP.md §2.1）：真机实验应当只有一条受治理的入口。
本模块提供**可离线单测**的判定逻辑，供 :3003 的 /execute 使用：

    flags = get_actuation_flags("quantum_service")   # 每次请求读取 → 可热切
    blocked = evaluate_require_task(data, flags)
    if blocked: return blocked

设计约束：
- 纯函数：不 import 任何服务模块、不碰 LabRAD，可在 CI / 影子实例里直接跑。
- 默认放行：require_task 未配置时为 false，保持现状（直发可用），避免影响用户在跑的真机实验。
- 打开后：不带 task_id 的请求返回 status="blocked" + reason_code="task_required"，
  引导走 :3009/task 或 :3008/workflows/run_ir。
"""

from typing import Any, Dict, List, Optional

from .errors import error_envelope


REQUIRE_TASK_HINT = (
    "① 先 POST :3009/task 建任务，再带 task_id 执行；"
    "② 或直接走 :3008/workflows/run_ir（自动建 task 与 manifest）；"
    "③ 调试可临时把 fallback_config.json 的 "
    "quantum_service.actuation.require_task 设为 false（每请求读取，热生效）。"
)


def summarize_governance(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """统计一批 RunRecord 的治理覆盖率（单写者影子期的核心指标）。

    Args:
        records: RunRecord.to_dict() 的列表（字段 governed: bool）。

    Returns:
        {"total", "governed", "ungoverned", "coverage", "by_source"}；
        coverage = governed / total（total=0 时为 0.0）。
    """
    records = list(records or [])
    total = len(records)
    governed = 0
    by_source: Dict[str, int] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("governed"):
            governed += 1
        source = str(record.get("source") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
    ungoverned = total - governed
    return {
        "total": total,
        "governed": governed,
        "ungoverned": ungoverned,
        "coverage": (governed / total) if total else 0.0,
        "by_source": by_source,
        "note": "ungoverned>0 表示存在绕过 task_queue 的直发执行；打开 require_task 可归零",
    }


def evaluate_require_task(
    data: Optional[Dict[str, Any]],
    flags: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """判断一次 /execute 请求是否应被治理闸门拦下。

    Args:
        data: 请求体（可含 task_id）
        flags: 写闸门状态；缺失 require_task 键时视为 False（放行）

    Returns:
        None 表示放行；否则返回拒绝响应 dict（status=blocked, reason_code=task_required）。
    """
    if not flags or not flags.get("require_task", False):
        return None

    data = data or {}
    task_id = str(data.get("task_id") or "").strip()
    if task_id:
        return None

    return {
        **error_envelope("task_required", "已启用 require_task 闸门：真机实验必须带 task_id"),
        "reason_code": "task_required",
        "hint": REQUIRE_TASK_HINT,
        "actuation_allowed": True,
    }
