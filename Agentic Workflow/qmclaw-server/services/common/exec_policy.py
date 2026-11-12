"""执行契约策略：`/execute` 默认同步还是异步（阶段 16a，roadmap §2.1）。

**背景**：阶段 15 之后异步执行已经可审计（提交 queued + 完成态两条同 run_id 记录），
但默认仍是同步 —— 长实验会占住一个 HTTP 连接，客户端断开后服务端照跑，调用方也拿不到
`job_id` 去"放弃/继续等"。生产者语义应该是"提交即返回、结果另取"
（方案见 `docs/PLAN_PRODUCER_DEFAULT.md`）。

**三态解析**（`resolve_execute_mode`）：

| 请求字段 | 语义 |
|---|---|
| `wait: true` 或 `async: false` | 旧同步行为（脚本/调试显式选择） |
| `async: true` | 异步 |
| 都不写 | 读默认（配置 → 环境变量优先，默认 `async`） |

**为什么 `wait` 优先于 `async`**：`wait` 是后来引入的"我要结果"的显式表达；
老调用方可能同时带着 `async:true`（历史写法）又想恢复同步，此时以 `wait` 为准更符合直觉。

**为什么默认可以做成 async 而不破坏前端**：前端"直接执行"走 agent/Express，
消费方在阶段 16a 全部显式传 `wait` 或轮询（见 `docs/OPERATIONS.md` §3.19），
默认值只影响"没表态"的新调用方。
"""

from typing import Any, Dict, Optional

EXECUTE_MODES = ("sync", "async")
DEFAULT_EXECUTE_MODE = "async"
EXECUTE_MODE_ENV = "QMCLAW_EXECUTE_DEFAULT"

_TRUTHY = {"1", "true", "yes", "on", "y"}
_FALSY = {"0", "false", "no", "off", "n"}


def as_bool(value: Any) -> Optional[bool]:
    """把请求里的布尔样值解释成 True/False；认不出返回 None（**不要用裸 bool()**）。

    HTTP JSON 里 `"false"` 是字符串，裸 `bool("false")` 是 True —— 老代码就踩过这个坑，
    导致 `async:"false"` 反而走了异步。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUTHY:
            return True
        if low in _FALSY:
            return False
    return None


def normalize_mode(value: Any) -> Optional[str]:
    """把 `"sync"`/`"async"`（大小写、空白容错）规整；非法返回 None。"""
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    return mode if mode in EXECUTE_MODES else None


def configured_execute_default(config: Optional[Dict[str, Any]] = None) -> str:
    """默认执行模式：环境变量 `QMCLAW_EXECUTE_DEFAULT` → 配置 → `async`。

    - 环境变量非法值**忽略**（继续看配置），不让一个拼错的值把服务切成意外模式；
    - `config` 可注入（自检用），不传则读 `config/fallback_config.json` 的
      `quantum_service.actuation.execute_default`；
    - 读配置失败一律回默认，绝不抛异常（这是请求热路径）。
    """
    import os

    env_val = os.environ.get(EXECUTE_MODE_ENV)
    env_mode = normalize_mode(env_val)
    if env_mode:
        return env_mode

    try:
        if config is None:
            from services.common.config import _load_fallback_service_config

            config = _load_fallback_service_config("quantum_service")
        actuation = (config or {}).get("actuation") or {}
        mode = normalize_mode(actuation.get("execute_default"))
        if mode:
            return mode
    except Exception:
        pass
    return DEFAULT_EXECUTE_MODE


def resolve_execute_mode(data: Any, default: Optional[str] = None) -> str:
    """按请求字段决定本次执行是 `"sync"` 还是 `"async"`。

    判定顺序（先看显式表态，再看 `async`，最后才用默认）：
    `wait` → `async` → default（`default` 不传则读配置）。
    """
    payload = data if isinstance(data, dict) else {}

    wait = as_bool(payload.get("wait"))
    if wait is True:
        return "sync"
    if wait is False:
        return "async"

    async_flag = as_bool(payload.get("async"))
    if async_flag is True:
        return "async"
    if async_flag is False:
        return "sync"

    return normalize_mode(default) or configured_execute_default()


def describe_execute_policy() -> Dict[str, Any]:
    """给 /health、/jobs 之类的地方用的自描述（运维排查"为什么这次是 queued"）。"""
    return {
        "default": configured_execute_default(),
        "modes": list(EXECUTE_MODES),
        "env": EXECUTE_MODE_ENV,
        "override": {
            "wait": "true → 同步等待（旧契约）",
            "async": "true → 提交即返回 job_id",
        },
        "poll": "GET /jobs/{job_id}",
    }
