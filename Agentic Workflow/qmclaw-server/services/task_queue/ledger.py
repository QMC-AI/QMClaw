"""
services/task_queue/ledger.py - 任务账本（task registry）+ 授权票据（capability ticket）

对应 `docs/DSL_DESIGN.md` §13.3 / §13.4。

**为什么要有它**：2026-11-05 的事故里，用户只要求"再测试一次 iqraw"，
智能体实际跑了 18 个独立 qwf —— 每个 qwf 都是一份全新的额度，
所以"复测默认跑一次"这条规则根本没有落点。账本把额度的作用域从
**qwf** 改成 **task（一次用户请求周期）**，跨 qwf 累计。

三个"session"不要混用：
- LabRAD session：物理数据目录，与额度无关
- 对话 session：一次聊天窗口，太粗（一次对话里可能有多个各自合法的任务）
- **task**：一次用户请求触发的目标 ✅ 正确归属

设计要点：
1. **未声明 task 的 qwf，默认额度 = 1 次实验**（复测默认跑一次）。
2. **重复也计数** —— 14 次 iqraw 正是漏了这一项。
3. 超额**硬停在排队层**：返回 `quota_exceeded`，不下发硬件。
4. 票据的 scope 必须 ⊆ 写清单（manifest），人批准的是**写清单**不是程序。
5. `neverDelegable`（当前 `timing.*`）命中时**连代理降级都不允许**。
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..common.errors import error_envelope

# 未声明 task 时的默认额度：**一次实验就停**（"复测默认跑一次"的落点）。
# `datasets` 是二级上限，给得宽松一些：goal 循环一轮里可能产出多个数据集，
# 若也卡在 1，会把正常的 max_rounds=3 目标流程误判成超额。
DEFAULT_BUDGETS = {
    "experiments": 1,
    "datasets": 50,
    "writes": 0,
    "wallSeconds": 300,
}

# 票据永远不可代理的键前缀（用户 2026-11-05 决定）
NEVER_DELEGABLE_PREFIXES = ("timing.",)

# 派生键：厂商按刚采到的数据重算出来的量（读出判别中心），不是标定决策。
# 它们不需要票据授权（与 P3.5 回滚策略"discriminator.* 永跳过"保持一致）。
DERIVED_KEY_PREFIXES = ("discriminator.",)


def _now() -> float:
    return time.time()


def _iso(ts: Optional[float] = None) -> str:
    ts = _now() if ts is None else ts
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _data_dir() -> Path:
    """账本落盘目录：默认在 task_queue 包下的 data/tasks/。"""
    env = os.environ.get("QMCLAW_TASK_DATA_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "data" / "tasks"


def _norm_key(key: str) -> str:
    """键名规范化：大小写不敏感的前缀匹配用（PiGate.amp / pigate.amp 视为同一个）。"""
    return str(key or "").strip()


def _matches_prefix(key: str, prefix: str) -> bool:
    """`timing.` 前缀匹配 `timing.xy`；`*` 匹配一切。"""
    key = _norm_key(key)
    prefix = _norm_key(prefix)
    if prefix == "*":
        return True
    if prefix.endswith("."):
        return key.lower().startswith(prefix.lower())
    return key.lower() == prefix.lower()


def is_never_delegable(key: str, prefixes: Optional[List[str]] = None) -> bool:
    """是否命中不可代理地板（默认 `timing.*`）。"""
    for prefix in (prefixes if prefixes is not None else NEVER_DELEGABLE_PREFIXES):
        if _matches_prefix(key, prefix):
            return True
    return False


def is_derived_key(key: str, prefixes: Optional[List[str]] = None) -> bool:
    """是否是派生键（默认 `discriminator.*`）：厂商重算出来的量，不需要票据。"""
    for prefix in (prefixes if prefixes is not None else DERIVED_KEY_PREFIXES):
        if _matches_prefix(key, prefix):
            return True
    return False


def manifest_hash(manifest: Dict[str, Any]) -> str:
    """写清单的稳定哈希（`sha256:…`）。

    只对**授权语义**取哈希：writes 列表的（键/次数/幅度/可否代理）。
    id / taskId / 时间戳不参与 —— 否则同一份清单换个 id 就对不上了。
    """
    writes = []
    for item in (manifest or {}).get("writes") or []:
        writes.append({
            "key": _norm_key(item.get("key")),
            "qubit": item.get("qubit"),
            "count": int(item.get("count") or 0),
            "maxDelta": item.get("maxDelta"),
            "delegable": bool(item.get("delegable", True)),
        })
    writes.sort(key=lambda w: (w["key"], str(w["qubit"])))
    payload = json.dumps({"writes": writes}, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TaskLedger:
    """task 账本 + 票据存储。线程安全，磁盘持久化（一个 task 一个 json）。"""

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = Path(data_dir) if data_dir else _data_dir()
        self._lock = threading.RLock()
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # ── 存储 ────────────────────────────────────────────────────────────
    def _path(self, task_id: str) -> Path:
        safe = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "-_")
        return self._dir / f"{safe or 'unknown'}.json"

    def _write(self, task: Dict[str, Any]) -> None:
        path = self._path(task.get("id", ""))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(path))
        except Exception:
            pass

    def _read(self, task_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(task_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── task 生命周期 ───────────────────────────────────────────────────
    def create(self, objective: str = "",
               stop_condition: Optional[Dict[str, Any]] = None,
               budgets: Optional[Dict[str, Any]] = None,
               qubits: Optional[List[str]] = None,
               task_id: Optional[str] = None) -> Dict[str, Any]:
        """新建 task。

        额度 = 默认值，被 `budgets` 里**显式给出且非 None** 的项逐项覆盖
        （没给的项落回默认；默认的 `experiments=1` 就是"未声明也最多跑一次"）。
        """
        merged = dict(DEFAULT_BUDGETS)
        for key, value in (budgets or {}).items():
            if value is not None:
                merged[key] = value
        tid = task_id or time.strftime("t-%Y%m%d-%H%M%S")
        with self._lock:
            existing = self._read(tid)
            if existing:
                return existing
            task = {
                "id": tid,
                "objective": objective or "",
                "stopCondition": stop_condition or {},
                "budgets": merged,
                "qubits": list(qubits or []),
                "manifests": [],
                "tickets": [],
                "usage": {"experiments": 0, "datasets": [], "writes": 0, "wallSeconds": 0.0},
                "events": [],
                "state": "active",
                "createdAt": _iso(),
            }
            self._write(task)
            return task

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._read(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            for path in sorted(self._dir.glob("*.json")):
                try:
                    out.append(json.loads(path.read_text(encoding="utf-8")))
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def release(self, task_id: str) -> Dict[str, Any]:
        """结束 task，冻结账本（不再接受额度消耗）。"""
        with self._lock:
            task = self._read(task_id)
            if not task:
                return error_envelope("not_found", f"unknown task {task_id}")
            task["state"] = "released"
            task["releasedAt"] = _iso()
            self._write(task)
            return {"ok": True, "task": task}

    # ── 额度 ────────────────────────────────────────────────────────────
    @staticmethod
    def remaining(task: Dict[str, Any]) -> Dict[str, Any]:
        budgets = task.get("budgets") or {}
        usage = task.get("usage") or {}
        datasets_used = len(usage.get("datasets") or [])
        return {
            "experiments": max(0, int(budgets.get("experiments", 0)) - int(usage.get("experiments", 0))),
            "datasets": max(0, int(budgets.get("datasets", 0)) - datasets_used),
            "writes": max(0, int(budgets.get("writes", 0)) - int(usage.get("writes", 0))),
            "wallSeconds": max(0.0, float(budgets.get("wallSeconds", 0)) - float(usage.get("wallSeconds", 0))),
        }

    def charge(self, task_id: str, kind: str = "experiments", count: int = 1,
               dataset: Optional[Any] = None, detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """消耗额度。超额返回 `{"ok": False, "reason": ...}`，**不修改账本**。"""
        with self._lock:
            task = self._read(task_id)
            if not task:
                return {
                    **error_envelope("not_found", f"unknown task {task_id}"),
                    "ok": False,
                    "reason": "unknown_task",
                }
            if task.get("state") != "active":
                return {"ok": False, "reason": "task_released", "remaining": self.remaining(task)}
            usage = task.setdefault("usage", {"experiments": 0, "datasets": [], "writes": 0, "wallSeconds": 0.0})
            budgets = task.get("budgets") or {}
            raw_count = count          # wallSeconds 要保留小数
            count = max(1, int(count))
            if kind == "experiments":
                if int(usage.get("experiments", 0)) + count > int(budgets.get("experiments", 0)):
                    return self._blocked(
                        task, "experiments", count,
                        f"experiments 额度 {budgets.get('experiments')} 已用 {usage.get('experiments')}")
                usage["experiments"] = int(usage.get("experiments", 0)) + count
            elif kind == "datasets":
                datasets = usage.setdefault("datasets", [])
                if len(datasets) + count > int(budgets.get("datasets", 0)):
                    return self._blocked(
                        task, "datasets", count,
                        f"datasets 额度 {budgets.get('datasets')} 已用 {len(datasets)}")
                for _ in range(count):
                    if dataset is not None:
                        datasets.append(dataset)
                    else:
                        datasets.append(None)
            elif kind == "writes":
                if int(usage.get("writes", 0)) + count > int(budgets.get("writes", 0)):
                    return self._blocked(
                        task, "writes", count,
                        f"writes 额度 {budgets.get('writes')} 已用 {usage.get('writes')}")
                usage["writes"] = int(usage.get("writes", 0)) + count
            elif kind == "wallSeconds":
                usage["wallSeconds"] = float(usage.get("wallSeconds", 0)) + float(raw_count)
            else:
                return {"ok": False, "reason": f"unknown kind {kind}"}
            self._record(task, "charge", {"chargeKind": kind, "count": count,
                                         "dataset": dataset, **(detail or {})})
            self._write(task)
            return {"ok": True, "remaining": self.remaining(task), "usage": usage}

    # ── 写清单与票据 ────────────────────────────────────────────────────
    def _blocked(self, task: Dict[str, Any], kind: str, count: int, detail: str) -> Dict[str, Any]:
        """额度超额：**不改 usage**，但记一条 `quota_blocked` 事件，让报表看得见"被挡过几次"。"""
        self._record(task, "quota_blocked", {"quotaKind": kind, "count": count, "detail": detail})
        self._write(task)
        return {**error_envelope("quota_exceeded", detail),
                "ok": False, "reason": "quota_exceeded", "detail": detail,
                "remaining": self.remaining(task)}

    def record_dataset(self, task_id: str, dataset: Any,
                       detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """实验跑完后回填真实数据集号。

        额度是**下发前**扣的，那时还不知道数据集号，只能先占一个 `None`；
        节点执行完拿到真实编号，再把最后一个 `None` 占位替换掉，
        这样 `usage.datasets` 里不会留下一串 null（审计要用）。
        """
        with self._lock:
            task = self._read(task_id)
            if not task:
                return {"ok": False, "reason": "unknown_task"}
            datasets = ((task.get("usage") or {}).get("datasets")) or []
            for idx in range(len(datasets) - 1, -1, -1):
                if datasets[idx] is None:
                    datasets[idx] = dataset
                    self._record(task, "dataset", {"dataset": dataset, **(detail or {})})
                    self._write(task)
                    return {"ok": True, "dataset": dataset}
            return {"ok": True, "dataset": dataset, "note": "no placeholder"}

    def refund(self, task_id: str, kind: str = "experiments", count: int = 1,
               detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """退还额度（D1-②：闸门拦下时把预扣的额度还回去）。

        - `kind="experiments"`：扣回实验次数。
        - `kind="datasets"`：回收**还没回填编号的占位**（只回收 `None`，
          已经拿到真实数据集号的条目不回收 —— 那次实验真的跑过）。

        返回 `{"ok": True, "refunded": n, ...}`；`refunded=0` 表示没东西可退。
        """
        with self._lock:
            task = self._read(task_id)
            if not task:
                return {"ok": False, "reason": "unknown_task"}
            if task.get("state") != "active":
                return {"ok": False, "reason": "task_released"}
            usage = task.setdefault("usage", {"experiments": 0, "datasets": [], "writes": 0, "wallSeconds": 0.0})
            count = max(1, int(count or 1))
            refunded = 0
            if kind == "experiments":
                used = int(usage.get("experiments", 0))
                refunded = min(used, count)
                usage["experiments"] = used - refunded
            elif kind == "datasets":
                datasets = usage.setdefault("datasets", [])
                for _ in range(count):
                    for idx in range(len(datasets) - 1, -1, -1):
                        if datasets[idx] is None:
                            del datasets[idx]
                            refunded += 1
                            break
                    else:
                        break
            else:
                return {"ok": False, "reason": f"unknown kind {kind}"}
            if refunded:
                self._record(task, "refund", {"refundKind": kind, "count": refunded, **(detail or {})})
                self._write(task)
            return {"ok": True, "refunded": refunded, "remaining": self.remaining(task), "usage": usage}

    def save_manifest(self, task_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        """保存写清单。返回 `{"manifestHash": ...}`。"""
        with self._lock:
            task = self._read(task_id)
            if not task:
                return error_envelope("not_found", f"unknown task {task_id}")
            mh = manifest.get("hash") or manifest_hash(manifest)
            stored = dict(manifest)
            stored["hash"] = mh
            stored.setdefault("taskId", task_id)
            manifests = task.setdefault("manifests", [])
            manifests[:] = [m for m in manifests if m.get("hash") != mh]
            manifests.append(stored)
            self._record(task, "manifest", {"hash": mh, "writes": len(stored.get("writes") or [])})
            self._write(task)
            return {"ok": True, "manifestHash": mh, "manifest": stored}

    def get_manifest(self, task_id: str, mhash: str) -> Optional[Dict[str, Any]]:
        task = self._read(task_id)
        if not task:
            return None
        for m in task.get("manifests") or []:
            if m.get("hash") == mhash:
                return m
        return None

    def issue_ticket(self, task_id: str, manifest_hash_value: str,
                     scope: Optional[List[Dict[str, Any]]] = None,
                     granted_by: str = "human",
                     expires_in_seconds: int = 24 * 3600,
                     never_delegable: Optional[List[str]] = None,
                     ticket_id: Optional[str] = None) -> Dict[str, Any]:
        """签发票据。**必须绑定 manifestHash**，scope 必须是 manifest 的子集。"""
        with self._lock:
            task = self._read(task_id)
            if not task:
                return error_envelope("not_found", f"unknown task {task_id}")
            manifest = self.get_manifest(task_id, manifest_hash_value)
            if not manifest:
                return {"error": f"unknown manifestHash {manifest_hash_value}"}
            manifest_writes = {(_norm_key(w.get("key"))): w for w in (manifest.get("writes") or [])}

            # scope ⊆ manifest：人批准了 A，票里不能写 B
            normalized_scope: List[Dict[str, Any]] = []
            for item in scope or []:
                keys = [_norm_key(k) for k in (item.get("keys") or [])]
                unknown = [k for k in keys if k not in manifest_writes]
                if unknown:
                    return {"error": f"scope 超出写清单：{unknown}"}
                max_delta = dict(item.get("maxDelta") or {})
                for key, value in list(max_delta.items()):
                    m_delta = manifest_writes.get(_norm_key(key), {}).get("maxDelta")
                    if m_delta is None:
                        return {"error": f"scope 里 {key} 在写清单中没有幅度上界，不可代理"}
                    if value is not None and float(value) > float(m_delta):
                        return {"error": f"scope 里 {key} 的 maxDelta {value} 大于写清单 {m_delta}"}
                normalized_scope.append({
                    "cls": item.get("cls") or "write",
                    "qubit": item.get("qubit"),
                    "keys": keys,
                    "limit": int(item.get("limit") or len(keys) or 1),
                    "used": 0,
                    "maxDelta": max_delta,
                })

            tid = ticket_id or ("tk-" + hashlib.sha256(
                f"{task_id}:{manifest_hash_value}:{_now()}".encode()).hexdigest()[:8])
            ticket = {
                "id": tid,
                "taskId": task_id,
                "grantedBy": granted_by,
                "issuedAt": _iso(),
                "expiresAt": _iso(_now() + float(expires_in_seconds)),
                "manifestHash": manifest_hash_value,
                "scope": normalized_scope,
                "neverDelegable": list(never_delegable if never_delegable is not None else NEVER_DELEGABLE_PREFIXES),
                "revoked": False,
            }
            task.setdefault("tickets", []).append(ticket)
            self._record(task, "ticket_issued", {"ticket": tid, "manifestHash": manifest_hash_value})
            self._write(task)
            return {"ok": True, "ticket": ticket}

    def revoke_ticket(self, task_id: str, ticket_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self._read(task_id)
            if not task:
                return error_envelope("not_found", f"unknown task {task_id}")
            for ticket in task.get("tickets") or []:
                if ticket.get("id") == ticket_id:
                    ticket["revoked"] = True
                    ticket["revokedAt"] = _iso()
                    self._record(task, "ticket_revoked", {"ticket": ticket_id})
                    self._write(task)
                    return {"ok": True, "ticket": ticket}
            return {"error": f"unknown ticket {ticket_id}"}

    # ── 授权判定（纯函数 + 账本状态） ──────────────────────────────────
    @staticmethod
    def _ticket_expired(ticket: Dict[str, Any]) -> bool:
        expires = ticket.get("expiresAt")
        if not expires:
            return False
        try:
            return time.mktime(time.strptime(expires, "%Y-%m-%dT%H:%M:%S")) < _now()
        except Exception:
            return False

    # 授权失败原因的优先级：越"硬"的原因越优先报给人看
    # （hard 失败立刻终止；partial/ticket_expired 允许换一张票再试）
    _REASON_PRIORITY = ("never_delegable", "limit_exhausted", "exceeded_max_delta",
                        "no_max_delta", "partial_scope", "ticket_expired", "out_of_scope")

    def authorize(self, task_id: str, action: Dict[str, Any]) -> Dict[str, Any]:
        """判定一次写动作是否在票据内。

        `action` 形如 `{"keys": ["PiGate.amp"], "qubit": "q2ld3", "deltas": {"PiGate.amp": 0.01}}`。

        返回 `{"ok": bool, "reason": str, "authorizedBy": str, "needsHuman": bool}`。
        `reason` 是人话；机器可读的失败码放在 `reasonCode`（同时写进事件）。

        **一次调用只记一条 authorize 事件**（D1-①）。旧实现会在"票只覆盖部分键"时
        先记 partial_scope 再记 out_of_scope，一次写动作被算成两次拦截，
        报表的 actions/越界率因此虚高。现在：每张票的判定收进 `attempts`，
        事件只记一条，`reason` 取优先级最高的那个失败码。
        """
        with self._lock:
            task = self._read(task_id)
            if not task:
                return {**error_envelope("not_found", f"unknown task {task_id}"),
                        "ok": False, "reason": "unknown_task", "detailReasonCode": "unknown_task",
                        "authorizedBy": "none", "needsHuman": True}
            keys = [_norm_key(k) for k in (action.get("keys") or [])]
            deltas = action.get("deltas") or {}
            qubit = action.get("qubit")

            # T0：没有写键 → 自动放行
            if not keys:
                self._record(task, "authorize", {"keys": [], "ok": True, "by": "auto", "reason": "no_writes"})
                self._write(task)
                return {"ok": True, "reason": "no writes", "authorizedBy": "auto", "needsHuman": False}

            # 派生键豁免：discriminator.* 是厂商按刚采到的数据重算出来的派生量，
            # 不是标定决策（config/registry_bounds.json 明确说它"不进本表"），
            # 因此不需要票据。规则与 P3.5 回滚策略一致（discriminator.* 永跳过）。
            # 少了这一条：一旦为 f10 签了票，同一条流程里的 sq.iqraw 会被
            # "out_of_scope" 拦下 → 整条流程 needs_human 卡死。
            derived = [k for k in keys if is_derived_key(k)]
            real_keys = [k for k in keys if k not in derived]
            if not real_keys:
                self._record(task, "authorize", {"keys": keys, "ok": True, "by": "derived",
                                                "reason": "derived_keys"})
                self._write(task)
                return {"ok": True, "reason": "只有派生键（不需要票据）",
                        "reasonCode": "derived_keys", "authorizedBy": "derived",
                        "needsHuman": False}
            if derived:
                keys = real_keys
                deltas = {k: v for k, v in (deltas or {}).items() if _norm_key(k) in keys}

            tickets = [t for t in (task.get("tickets") or []) if not t.get("revoked")]
            if not tickets:
                self._record(task, "authorize", {"keys": keys, "ok": False, "by": "none", "reason": "no_ticket"})
                self._write(task)
                return {**error_envelope("not_authorized", "没有可用票据"),
                        "ok": False, "reason": "没有可用票据", "detailReasonCode": "no_ticket",
                        "authorizedBy": "none", "needsHuman": True}

            attempts: List[Dict[str, Any]] = []
            decision: Optional[Dict[str, Any]] = None
            for ticket in tickets:
                res = self._evaluate_ticket(ticket, keys, deltas, qubit)
                attempts.append({"ticket": ticket.get("id"), "ok": bool(res.get("ok")),
                                 "reason": res.get("code"), "message": res.get("message")})
                if res.get("ok") or res.get("hard"):
                    decision = res
                    break

            if decision and decision.get("ok"):
                ticket = decision["ticket"]
                # 全部键命中票据 → 放行并记账
                for key in keys:
                    for scope in ticket.get("scope") or []:
                        if _norm_key(key) in [_norm_key(k) for k in (scope.get("keys") or [])]:
                            scope["used"] = int(scope.get("used") or 0) + 1
                ticket["usedAt"] = _iso()
                self._record(task, "authorize", {"keys": keys, "ok": True,
                                                "by": f"agent-delegated:{ticket.get('id')}",
                                                "reason": "in_scope", "ticket": ticket.get("id"),
                                                "attempts": attempts})
                self._write(task)
                return {"ok": True, "reason": "在票据内", "reasonCode": "in_scope",
                        "authorizedBy": f"agent-delegated:{ticket.get('id')}",
                        "needsHuman": False, "ticket": ticket.get("id")}

            # 所有票据都没兜住 → 取优先级最高的失败码作为对外原因
            failed = [a for a in attempts if not a.get("ok")]
            if failed:
                failed.sort(key=lambda a: self._reason_rank(a.get("reason")))
                primary = failed[0]
            else:
                primary = {"reason": "out_of_scope", "message": "写操作不在任何票据范围内"}
            code = primary.get("reason") or "out_of_scope"
            message = primary.get("message") or "写操作不在任何票据范围内"
            event = {"keys": keys, "ok": False, "by": "none", "reason": code, "attempts": attempts}
            if decision and decision.get("blocked"):
                event["blocked"] = decision["blocked"]
            if decision and decision.get("matched"):
                event["matched"] = decision["matched"]
            self._record(task, "authorize", event)
            self._write(task)
            out = {**error_envelope("not_authorized", message),
                   "ok": False, "reason": message, "detailReasonCode": code,
                   "authorizedBy": "none", "needsHuman": True}
            if decision and decision.get("blocked"):
                out["neverDelegable"] = decision["blocked"]
            return out

    @classmethod
    def _reason_rank(cls, code: Optional[str]) -> int:
        try:
            return cls._REASON_PRIORITY.index(code)
        except ValueError:
            return len(cls._REASON_PRIORITY)

    def _evaluate_ticket(self, ticket: Dict[str, Any], keys: List[str],
                         deltas: Dict[str, Any], qubit: Optional[str]) -> Dict[str, Any]:
        """拿一张票去判一次写动作。

        返回 `{"ok", "code", "message", "hard", ...}`；`hard=True` 表示换别的票也没用
        （不可代理地板 / 票据额度用尽 / 超出幅度上界 / 没有幅度上界），必须人直批。
        """
        if self._ticket_expired(ticket):
            return {"ok": False, "code": "ticket_expired", "hard": False,
                    "message": f"票据 {ticket.get('id')} 已过期"}
        never = list(ticket.get("neverDelegable") or NEVER_DELEGABLE_PREFIXES)
        blocked = [k for k in keys if is_never_delegable(k, never)]
        if blocked:
            return {"ok": False, "code": "never_delegable", "hard": True, "blocked": blocked,
                    "message": f"命中不可代理地板：{blocked}"}

        matched = set()
        for key in keys:
            hit = self._match_scope(ticket, key, qubit)
            if hit is None:
                continue
            matched.add(_norm_key(key))
            entry, scope = hit
            limit = int(scope.get("limit") or 0)
            used = int(scope.get("used") or 0)
            if limit and used >= limit:
                return {"ok": False, "code": "limit_exhausted", "hard": True,
                        "message": f"票据 {ticket.get('id')} 对 {key} 的额度已用尽"}
            delta = deltas.get(key)
            max_delta = (scope.get("maxDelta") or {}).get(key)
            if delta is not None and max_delta is not None and abs(float(delta)) > float(max_delta):
                return {"ok": False, "code": "exceeded_max_delta", "hard": True,
                        "message": f"{key} 实际改动 {delta} 超过票据上限 {max_delta}"}
            if delta is not None and max_delta is None:
                return {"ok": False, "code": "no_max_delta", "hard": True,
                        "message": f"{key} 没有幅度上界，不可代理"}

        # 必须**每个**写键都被这张票据覆盖：只覆盖一部分不能放行
        # （否则"票里只批了 amp"却连 alpha 一起改，等于票形同虚设）。
        if len(matched) < len(set(keys)):
            missing = sorted(set(keys) - matched)
            if matched:
                return {"ok": False, "code": "partial_scope", "hard": False, "matched": sorted(matched),
                        "message": f"票据 {ticket.get('id')} 只覆盖 {sorted(matched)}，缺少 {missing}"}
            return {"ok": False, "code": "out_of_scope", "hard": False, "matched": [],
                    "message": "写操作不在任何票据范围内"}
        return {"ok": True, "code": "in_scope", "ticket": ticket}

    @staticmethod
    def _match_scope(ticket: Dict[str, Any], key: str, qubit: Optional[str]) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """在票据 scope 里找能覆盖该键的条目。"""
        key = _norm_key(key)
        for scope in ticket.get("scope") or []:
            if scope.get("cls") not in (None, "write"):
                continue
            scope_qubit = scope.get("qubit")
            if scope_qubit and qubit and scope_qubit != qubit:
                continue
            for k in scope.get("keys") or []:
                if _norm_key(k) == key:
                    return scope, scope
        return None

    # ── 事件与报表 ──────────────────────────────────────────────────────
    @staticmethod
    def _record(task: Dict[str, Any], kind: str, detail: Dict[str, Any]) -> None:
        events = task.setdefault("events", [])
        events.append({"at": _iso(), "kind": kind, **detail})
        if len(events) > 2000:
            del events[:-2000]

    def report(self, task_id: str) -> Dict[str, Any]:
        """审计报表（§13.6 三个指标）。

        - **越界率** = 被拦下的动作 / 总动作 —— 机制是"挡住了越界"还是"把越界变成了失败"
        - **授权缺失率** = 需要人直批的次数 / 总放行请求 —— 票据范围是否给得太窄
        - **代理放行率** = agent-delegated / 总放行 —— 委托是否真的在起作用
        """
        task = self._read(task_id)
        if not task:
            return error_envelope("not_found", f"unknown task {task_id}")
        events = task.get("events") or []
        auth = [e for e in events if e.get("kind") == "authorize"]
        allowed = [e for e in auth if e.get("ok")]
        blocked = [e for e in auth if not e.get("ok")]
        delegated = [e for e in allowed if str(e.get("by", "")).startswith("agent-delegated")]
        needs_human = [e for e in blocked if e.get("reason") in ("no_ticket", "out_of_scope", "never_delegable",
                                                              "limit_exhausted", "exceeded_max_delta", "no_max_delta",
                                                              "partial_scope", "ticket_expired")]
        quota_blocked = [e for e in events if e.get("kind") == "quota_blocked"]
        total = len(auth)
        return {
            "taskId": task_id,
            "state": task.get("state"),
            "objective": task.get("objective"),
            "usage": task.get("usage"),
            "budgets": task.get("budgets"),
            "remaining": self.remaining(task),
            "manifests": [{"hash": m.get("hash"), "writes": len(m.get("writes") or [])} for m in task.get("manifests") or []],
            "tickets": [{"id": t.get("id"), "expiresAt": t.get("expiresAt"), "revoked": bool(t.get("revoked")),
                         "used": sum(int(s.get("used") or 0) for s in t.get("scope") or [])} for t in task.get("tickets") or []],
            "metrics": {
                "actions": total,
                "allowed": len(allowed),
                "blocked": len(blocked),
                "越界率": round(len(blocked) / total, 4) if total else 0.0,
                "授权缺失率": round(len(needs_human) / total, 4) if total else 0.0,
                "代理放行率": round(len(delegated) / len(allowed), 4) if allowed else 0.0,
                # 额度闸门是另一条轴（不是"越界"，是"用完了"），单独计数
                "quotaBlocked": len(quota_blocked),
                "总拦截": len(blocked) + len(quota_blocked),
            },
            "events": events[-100:],
        }
