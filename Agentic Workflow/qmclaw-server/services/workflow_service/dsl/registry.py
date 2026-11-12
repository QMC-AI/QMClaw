"""Metric Registry 接入层（QMClaw Architecture Roadmap §2.4/§2.5）。

为什么需要这一层
----------------
`dsl` 包有两种加载方式：

* 正常运行时是 ``services.workflow_service.dsl``（相对导入可用）；
* 离线自检脚本会把 ``services/workflow_service`` 加进 ``sys.path`` 后
  直接 ``import dsl`` —— 这时 ``from ...common.metrics import ...`` 会越过
  顶层，报 ``attempted relative import beyond top-level package``。

所以这里统一改成**绝对导入** ``services.common.*``，并在需要时把
``qmclaw-server`` 根目录补进 ``sys.path``：两种加载方式拿到的是同一份判据，
阈值永远只有一个真源（``config/lab_criteria.json``）。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))        # services/workflow_service/dsl
_WORKFLOW = os.path.dirname(_HERE)                        # services/workflow_service
_SERVICES = os.path.dirname(_WORKFLOW)                    # services
_SERVER = os.path.dirname(_SERVICES)                      # qmclaw-server

if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

from services.common.metrics import (  # noqa: E402
    available_levels,
    criteria_metric_name,
    evaluate,
    evaluate_all,
    load_criteria,
    resolve_threshold,
)
from services.common.profile import (  # noqa: E402
    load_profile,
    profile_env,
    profile_env_fields,
    qubit_entry,
)
from services.common.qubit_state import derive_state, state_table  # noqa: E402

__all__ = [
    "available_levels",
    "criteria_metric_name",
    "derive_state",
    "evaluate",
    "evaluate_all",
    "load_criteria",
    "load_profile",
    "profile_env",
    "profile_env_fields",
    "qubit_entry",
    "resolve_threshold",
    "state_table",
]
