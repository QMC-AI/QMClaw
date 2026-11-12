"""P3.5 副作用自愈：快照 → diff → 计划 → 回滚。

背景（2026-11-05 事故）：用户只要求"再测一次 iqraw"，实际跑了 18 个独立 qwf，
其中厂商实验**静默改了寄存器**（f10 被改 +455 MHz、timing.xy 被改 0.072 → 826.45 ns），
对比度从 0.82 掉到 0.02，而当时系统对"实验顺手改了参数"这件事没有任何记录与兜底。

P3.5 的做法：
  1. 下发前拍一张 registry 快照（`POST :3003/qubit/registry`，D2 新增的只读端点）；
  2. 实验跑完再拍一张；
  3. diff 出被改动的键；
  4. 按策略表 `config/registry_rollback_policy.json` 决定哪些能自动回滚；
  5. 回滚本身**也要走票据**（`:3009/task/authorize`）——回滚是写操作，不能开后门。

默认是**影子模式**（只记录、不回滚），确认策略无误后再切自动。

模块刻意不依赖任何其他模块（只用标准库），方便离线自检。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

# `services/workflow_service/dsl/` → 上溯到 `qmclaw-server/`
ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "config"
POLICY_FILE = CONFIG_DIR / "registry_rollback_policy.json"
BOUNDS_FILE = CONFIG_DIR / "registry_bounds.json"

DEFAULT_POLICY: Dict[str, Any] = {
    "mode": "shadow",                 # shadow | auto
    "enabled": True,                  # 关掉就完全不拍快照（省两次 HTTP）
    "always_skip": ["discriminator."],  # 派生键：厂商重算的量，永不回滚
    "report_only": ["timing."],         # 不可代理的键：只报告，不回滚
    "require_ticket": True,             # 回滚也要有票据覆盖
    "max_keys": 32,                     # 一次最多回滚多少个键（防手抖）
    "bounds_file": "registry_bounds.json",
}


# ── 配置 ────────────────────────────────────────────────────────────────
def load_policy(path: Optional[str] = None) -> Dict[str, Any]:
    """读回滚策略表；文件不存在就用内置默认值（缺字段也用默认值补齐）。"""
    policy = dict(DEFAULT_POLICY)
    p = Path(path) if path else POLICY_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if k.startswith("_"):
                    continue
                policy[k] = v
    except Exception:
        pass
    env = os.environ.get("QMCLAW_AUTO_REVERT", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        policy["mode"] = "auto"
    elif env in ("0", "false", "no", "off"):
        policy["mode"] = "shadow"
    if os.environ.get("QMCLAW_AUTO_REVERT_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        policy["enabled"] = False
    return policy


def load_bounds(path: Optional[str] = None) -> Dict[str, float]:
    """读幅度上界表（`config/registry_bounds.json`）。"""
    p = Path(path) if path else BOUNDS_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        bounds = data.get("bounds") or {}
        return {str(k): float(v) for k, v in bounds.items()}
    except Exception:
        return {}


# ── registry 处理 ───────────────────────────────────────────────────────
def flatten_registry(registry: Any) -> Dict[str, Any]:
    """把嵌套 registry 摊平成 `{"f10": 3.62, "PiGate.amp": 1.2}`。"""
    out: Dict[str, Any] = {}
    if not isinstance(registry, dict):
        return out
    for key, value in registry.items():
        if isinstance(value, dict):
            for sub, subval in value.items():
                out[f"{key}.{sub}"] = subval
        else:
            out[str(key)] = value
    return out


def diff_registry(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """对比两张快照，返回被改动的键。

    只比较两边都存在且**类型可比较**的键；新增/消失的键单独标记（不自动回滚）。
    """
    changes: List[Dict[str, Any]] = []
    for key, after_val in (after or {}).items():
        if key not in (before or {}):
            changes.append({"key": key, "before": None, "after": after_val,
                            "delta": None, "kind": "appeared"})
            continue
        before_val = before[key]
        if isinstance(before_val, (int, float)) and isinstance(after_val, (int, float)):
            if abs(float(after_val) - float(before_val)) > 1e-12:
                changes.append({"key": key, "before": before_val, "after": after_val,
                                "delta": float(after_val) - float(before_val),
                                "kind": "changed"})
        elif before_val != after_val:
            changes.append({"key": key, "before": before_val, "after": after_val,
                            "delta": None, "kind": "changed"})
    for key, before_val in (before or {}).items():
        if key not in (after or {}):
            changes.append({"key": key, "before": before_val, "after": None,
                            "delta": None, "kind": "disappeared"})
    return changes


def _matches(key: str, prefixes: List[str]) -> bool:
    k = str(key or "")
    return any(k.startswith(p) for p in (prefixes or []))


def plan_rollback(changes: List[Dict[str, Any]], policy: Dict[str, Any],
                  bounds: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """按策略表把 diff 分成"可回滚"与"只报告"两堆。

    规则（与 `config/registry_rollback_policy.json` 一一对应）：
      · `always_skip` 前缀（discriminator.*）—— 派生量，永不回滚；
      · `report_only` 前缀（timing.*）—— 不可代理的键，只报告；
      · 不在幅度表里的键 —— 没有上界 ⇒ 不自动回滚（只报告，等人决定）；
      · 超出 `max_keys` —— 多出来的只报告。
    """
    bounds = bounds if bounds is not None else load_bounds()
    always_skip = list(policy.get("always_skip") or [])
    report_only = list(policy.get("report_only") or [])
    max_keys = int(policy.get("max_keys") or 32)

    restore: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for ch in changes or []:
        key = str(ch.get("key") or "")
        if _matches(key, always_skip):
            skipped.append({**ch, "reason": "derived_key"})
            continue
        if _matches(key, report_only):
            skipped.append({**ch, "reason": "never_delegable"})
            continue
        if key not in bounds:
            skipped.append({**ch, "reason": "no_bound"})
            continue
        if ch.get("before") is None:
            skipped.append({**ch, "reason": "no_before_value"})
            continue
        if len(restore) >= max_keys:
            skipped.append({**ch, "reason": "too_many_keys"})
            continue
        restore.append(ch)
    return {"restore": restore, "skipped": skipped}


# ── HTTP 侧 ─────────────────────────────────────────────────────────────
def _post_json(url: str, payload: Dict[str, Any], timeout: float = 20.0) -> Dict[str, Any]:
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def fetch_registry(quantum_base: str, qubit: str,
                   timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    """读某比特的完整 registry（摊平后）。失败返回 None（不阻塞主流程）。"""
    if not quantum_base or not qubit:
        return None
    try:
        data = _post_json(f"{quantum_base.rstrip('/')}/qubit/registry",
                          {"name": qubit}, timeout=timeout)
    except Exception:
        return None
    if data.get("error"):
        return None
    return flatten_registry(data.get("registry") or {})


def apply_params(quantum_base: str, qubit: str, params: Dict[str, Any],
                 timeout: float = 20.0) -> Dict[str, Any]:
    """把寄存器写回去（受 :3003 的 allow_set_params 写闸门约束）。"""
    return _post_json(f"{quantum_base.rstrip('/')}/qubit/set_params",
                      {"name": qubit, "params": params}, timeout=timeout)


def authorize(queue_base: str, task_id: str, keys: List[str], qubit: str,
              deltas: Optional[Dict[str, Any]] = None,
              timeout: float = 15.0) -> Dict[str, Any]:
    """回滚也要走票据：问 task_queue 这些键能不能写。"""
    if not queue_base or not task_id or not keys:
        return {"ok": False, "reason": "no_task_or_keys"}
    try:
        return _post_json(f"{queue_base.rstrip('/')}/task/authorize",
                           {"taskId": task_id, "keys": keys, "qubit": qubit,
                            "deltas": deltas or {}, "enforce": True,
                            "node": "autorevert"},
                           timeout=timeout)
    except Exception as exc:
        return {"ok": False, "reason": f"authorize_failed: {exc}"}


# ── 对外主流程 ──────────────────────────────────────────────────────────
class SideEffectWatcher:
    """在一次实验前后拍快照、diff、按策略决定回滚。

    用法：
        watcher = SideEffectWatcher(quantum_base, queue_base, task_id)
        before = watcher.snapshot(qubit)
        ... 跑实验 ...
        report = watcher.after(qubit, before, node_name)
    """

    def __init__(self, quantum_base: str = "", queue_base: str = "", task_id: str = "",
                 policy: Optional[Dict[str, Any]] = None,
                 bounds: Optional[Dict[str, float]] = None,
                 logger: Optional[Any] = None):
        self.quantum_base = quantum_base or ""
        self.queue_base = queue_base or ""
        self.task_id = task_id or ""
        self.policy = policy if policy is not None else load_policy()
        self.bounds = bounds if bounds is not None else load_bounds()
        self._log = logger or (lambda _m: None)
        self.reports: List[Dict[str, Any]] = []

    @property
    def mode(self) -> str:
        return str(self.policy.get("mode") or "shadow")

    def snapshot(self, qubit: str) -> Optional[Dict[str, Any]]:
        return fetch_registry(self.quantum_base, qubit)

    def after(self, qubit: str, before: Optional[Dict[str, Any]],
              node_name: str = "") -> Dict[str, Any]:
        """实验跑完之后调用：拍后照、diff、决定回滚。

        返回一份可直接进 run 记录的副作用报告。
        """
        report: Dict[str, Any] = {
            "node": node_name, "qubit": qubit, "mode": self.mode,
            "changes": [], "restored": [], "skipped": [], "notes": [],
        }
        if before is None:
            report["notes"].append("没有前快照（:3003 /qubit/registry 不可用），跳过副作用检查")
            self.reports.append(report)
            return report
        after = self.snapshot(qubit)
        if after is None:
            report["notes"].append("没有后快照（:3003 /qubit/registry 不可用），跳过副作用检查")
            self.reports.append(report)
            return report

        changes = diff_registry(before, after)
        report["changes"] = changes
        if not changes:
            report["notes"].append("没有寄存器被改动")
            self.reports.append(report)
            return report

        plan = plan_rollback(changes, self.policy, self.bounds)
        report["skipped"] = plan["skipped"]
        restore = plan["restore"]
        if not restore:
            report["notes"].append("所有改动都在策略表之外，只报告不回滚")
            self.reports.append(report)
            return report

        if self.mode != "auto":
            report["notes"].append(
                f"影子模式：{len(restore)} 个键可回滚但未回滚（QMCLAW_AUTO_REVERT=1 才真回滚）")
            self.reports.append(report)
            return report

        keys = [str(c["key"]) for c in restore]
        if self.policy.get("require_ticket", True):
            auth = authorize(self.queue_base, self.task_id, keys, qubit)
            if not auth.get("ok"):
                report["notes"].append(
                    f"回滚需要票据但没拿到（{auth.get('reason')}），只报告不回滚")
                report["skipped"].extend({**c, "reason": "no_ticket"} for c in restore)
                self.reports.append(report)
                return report

        params = {str(c["key"]): c["before"] for c in restore}
        res = apply_params(self.quantum_base, qubit, params)
        if res.get("success"):
            report["restored"] = [{"key": k, "value": v} for k, v in params.items()]
        else:
            report["notes"].append(f"回滚写入被拒：{res.get('error') or res.get('status')}")
            report["skipped"].extend({**c, "reason": "write_denied"} for c in restore)
        self.reports.append(report)
        return report
