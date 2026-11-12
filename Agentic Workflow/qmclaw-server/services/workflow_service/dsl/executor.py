"""dsl.executor - IR 执行器。

核心设计：**不重写执行体**。每条 IR 语句被编译成一个 `WorkflowNode`，交给现有的
`WorkflowService._execute_node`（重试、超时、async job、abandon、取消、判定全在里面）。
IrExecutor 只负责四件现有解释器没有的事：控制流、`$ref` 绑定、预算、审计。

为什么能这么接：
- `_execute_node_once` 在末尾有 `context[node_id] = result`（server.py:620），
  所以"绑定名 = 节点 id"天然把结果留在上下文里；
- `:599` 会设置 `node._workflow = workflow`，取消事件与 `_node_timeout` 因此可用；
- 注册一个真 `Workflow` 进 `service._workflows`，`/workflows/status`、前端轮询、
  `_persist_workflow_run` 全部照旧工作，不需要新接口。
"""

import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 阶段 6：workflow / goal outcome 的状态 → 规范状态（见 services/common/errors.py
# 的 CANONICAL_STATUSES：ok / degraded / queued / blocked / failed）。
# 这里**不改变**已有的 status 取值（前端与 Express 已经在消费它们），
# 只额外给出 canonicalStatus + reasonCode，方便调用方用一套逻辑判断。
_STATUS_CANONICAL = {
    "completed": "ok",
    "achieved": "ok",
    "failed": "failed",
    "error": "failed",
    "not_achieved": "failed",
    "cancelled": "failed",
    "needs_human": "blocked",
    "quota_exceeded": "blocked",
    "budget_exhausted": "blocked",
    "budget_exceeded": "blocked",
}

from . import ir as ir_mod
from .autorevert import SideEffectWatcher
from .budget import Budget, BudgetExceeded
from .compile import build_node
from .const import (
    OP_ANALYZE,
    OP_APPLY,
    OP_ASK,
    OP_ASSERT,
    OP_BODY_KEY,
    OP_CALL,
    OP_CLASSIFY,
    OP_DECIDE,
    OP_EFFECT,
    OP_IF,
    OP_LET,
    OP_MEASURE,
    OP_READ,
    OP_REPEAT,
    OP_SAY,
    OP_VLM,
    OPS,
    IR_VERSION,
)

# qobj 的 registry 写入在真机 stdout 里长这样：
#   __setitem__ <qobj.f10> to 6.726144 from 6.725331 in path ['fread']
# 只认 "in path [...]" 这一截，前面的值格式化怎么变都不影响解析。
#
# 真机（2026-11-04 实测）还有新格式，参数名和容器分开：
#   __setitem__ amp to 0.27 from 0.0 in path ['', 'LQHL', 'test', '20251103_LD_SQ', 'q2ld3', 'PiGate']
#   __setitem__ f10 to 4.08 from 3.20 in path ['', 'LQHL', 'test', '20251103_LD_SQ', 'q2ld3']
# 名字只有末一段（'amp'），能定位的是路径末端的容器（'PiGate'）→ 键是 'PiGate.amp'；
# 路径停在比特这一层的（'f10'）→ 键就是 'f10'。容器名是厂商知识，问后端适配器要。
_SETITEM_RE = re.compile(
    r"__setitem__\s+(?:<(?P<name>[^>]+)>|(?P<bare>[A-Za-z_][\w.]*))\s+to\b"
    r"[^\n]*?in path\s+(?P<path>\[[^\]]*\])")
# 带值的写法：真机 stdout 里 `to <新值> from <旧值>` 都有，能算出这次写入挪动了多少。
_SETITEM_VAL_RE = re.compile(
    r"__setitem__\s+(?:<(?P<name>[^>]+)>|(?P<bare>[A-Za-z_][\w.]*))\s+"
    r"to\s+(?P<new>[-+0-9.eE]+)\s+from\s+(?P<old>[-+0-9.eE]+)\s+"
    r"in path\s+(?P<path>\[[^\]]*\])")
_SETITEM_PATH_RE = re.compile(r"in path\s+(\[[^\]]*\])")
from .validate import INTERP_RE, parse_ref

EXTERNAL_OPS = (OP_DECIDE, OP_ASK, OP_VLM)
WRITE_OPS = (OP_MEASURE,)


class Halt(Exception):
    """语义上的失败中止（不是 Python 异常，是流程状态）。"""


class Cancelled(Exception):
    pass


class UndefinedRef(Exception):
    pass


class NeedsHuman(Exception):
    """流程停下来等人拍板（`ask` 节点）。

    不是失败：outcome 契约把它映射成 `needs_human`，上层据此向用户提问。
    """

    def __init__(self, message: str, question: str = "", options=None):
        super().__init__(message)
        self.question = question or message
        self.options = list(options or [])


class QuotaExceeded(Exception):
    """task 额度用尽（P3，`docs/DSL_DESIGN.md` §13.4）。

    也是"不是失败"：硬件一次都没下发，outcome 映射成 `quota_exceeded`，
    由上层决定是新建 task 还是收工 —— 这正是 2026-11-05 事故里缺的那道闸。
    """


def _to_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and "value" in value:
        return _to_number(value["value"])
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _as_text(value: Any) -> str:
    """把任意结果压成可比较的文本（decide 的 choice / recommendation 之类）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("choice", "text", "content", "value", "message"):
            if key in value:
                return _as_text(value[key])
        return ""
    if isinstance(value, (list, tuple, set)):
        return ",".join(_as_text(v) for v in value)
    return str(value)


def wrap_result(raw: Any, started: float, finished: float) -> Dict[str, Any]:
    """把现有节点的返回包装成 IR 结果契约（§3.4）。判定只信 `_classify_result` 的结论。"""
    raw = raw if isinstance(raw, dict) else {"value": raw}
    status = str(raw.get("status") or "completed")
    ok = (
        not raw.get("error")
        and status not in ("failed", "error", "timeout", "cancelled")
        and raw.get("success", True) is not False
    )
    metrics = dict(raw.get("metrics") or {})
    for key in ("contrast", "snr", "T1_us", "R_squared", "pi_amp", "f0", "f1"):
        if raw.get(key) is not None:
            metrics.setdefault(key, raw[key])

    dataset = raw.get("dataset_id")
    if dataset is None:
        dataset = raw.get("dataset")
    if isinstance(dataset, str):
        try:
            dataset = int(dataset.strip())
        except ValueError:
            dataset = None

    value = raw.get("value")
    if value is None:
        value = raw.get("contrast")

    artifacts = dict(raw.get("artifacts") or {})
    if raw.get("plotPath") and "plot" not in artifacts:
        artifacts["plot"] = raw["plotPath"]

    out = {
        "ok": bool(ok),
        "status": status,
        "value": value,
        "metrics": metrics,
        "dataset": dataset,
        "artifacts": artifacts,
        "stdout": raw.get("stdout") or "",
        "error": raw.get("error"),
        "startedAt": round(started, 3),
        "finishedAt": round(finished, 3),
        "cost": {"wallSeconds": round(finished - started, 2), "llmCalls": 0},
    }
    # 让 `$x.passed` / `$x.conversation` 这类字段也能被引用到
    for key in ("passed", "threshold", "operator", "metric", "conversation",
                "symptom", "recommendations", "message", "params", "variables",
                "qubit", "kind", "job_id", "choice", "choices",
                "needs_human", "question", "options"):
        if key in raw:
            out[key] = raw[key]
        elif isinstance(raw.get("metrics"), dict) and key in raw["metrics"]:
            # quality_gate 会把 passed/threshold 复制进 metrics，这里补到顶层，
            # 让 `$g1.passed` 这类引用在两种来源下都成立。
            out[key] = raw["metrics"][key]
    return out


class IrExecutor:
    def __init__(self, service, ir: Dict[str, Any], context: Optional[Dict[str, Any]] = None,
                 dry_run: bool = False, dry_provider: Callable = None,
                 workflow_id: str = None, cancel_event: threading.Event = None,
                 budget_mode: str = "real"):
        self.svc = service
        self.ir = ir
        self.ctx = dict(context or {})
        self.env = dict(ir.get("env") or {})
        self.env.update(self.ctx)          # 运行时 context 覆盖同名 env 键
        self.bindings: Dict[str, Any] = {}
        self.legacy: Dict[str, Any] = {}
        self.audit: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        # 阶段 13（§2.7）：画像进运行期。以前只有 lint 期读 config/device_profile.json，
        # 流程里写死 f10/fread；现在 `.qwf` 可以写 `$profile.f10`，值在执行开始时定下。
        # 优先级：调用方 context 里显式给的 profile > 当前比特的画像条目。
        self.profile: Dict[str, Any] = {}
        try:
            given = self.env.get("profile")
            if isinstance(given, dict) and given:
                self.profile = dict(given)
            else:
                from .registry import load_profile, profile_env
                self.profile = profile_env(self.env.get("qubit"), load_profile())
        except Exception as exc:  # noqa: BLE001 — 画像读不出来不该拦住流程
            self.warnings.append(f"设备画像加载失败（$profile.* 不可用）：{exc!r}")
        if self.profile:
            self.env.setdefault("profile", self.profile)
        else:
            qubit = self.env.get("qubit")
            if isinstance(qubit, str) and qubit:
                self.warnings.append(
                    f"设备画像里没有 {qubit} 的条目（$profile.* 不可用）："
                    "跑一次 GET /qubit/profile?refresh=1 刷新画像，或在 context 里直接给 profile")
        self.dry_run = dry_run
        self.dry = dry_provider
        if self.dry_run and self.dry is None:
            # 只给 dry_run=True 却没给 provider 时，用默认的合成数据源，
            # 否则会退化成 {"value": 0} 的空结果（下游 $ref 全部解析失败）。
            from .dryrun import DryRunProvider
            self.dry = DryRunProvider("synthetic")
        if self.dry is not None:
            # 干跑也要能解析 `$ref`（assert/stopWhen 要比对真实绑定），
            # 所以把执行器自己的解析函数交给 provider。
            try:
                self.dry.resolve = self._resolve
            except Exception:
                pass
        self.budget = Budget(ir.get("budget"), mode="dry" if dry_run else budget_mode)
        self.cancel = cancel_event or threading.Event()
        self.workflow_id = workflow_id or f"{ir.get('id', 'ir')}_{int(time.time() * 1000)}"
        self.node_names: Dict[int, str] = {}
        self._seq = 0
        self.workflow = None
        self._NS = None
        self.status = "completed"
        self.error = None
        self.started = time.time()
        self.qualified = 0
        # 编译期成本估算（点数/预计耗时/写入语义），下发前算一次，报告里带出去
        self.plan: Optional[Dict[str, Any]] = None
        self.effects: Optional[Dict[str, Any]] = None
        # 结果契约（§13.6）：流程结束后给调用方一个明确的"结局"，
        # 而不是让调用方从 status/error 里猜。
        self.outcome: Dict[str, Any] = {"status": "achieved", "reason": "", "detail": {}}
        self._outcome_computed = False
        # `ask` 节点停下来等人拍板时，把问题带出来（outcome.detail 会引用它）
        self.needs_human: Optional[Dict[str, Any]] = None
        # P3 闸门：本次 run 归属的 task（None = 不限额，保持既有 .qwf 行为不变）
        self.gate_task: Optional[str] = None
        self.gate_notes: List[Dict[str, Any]] = []
        # D1-③：run 级落档是否失败（失败会兜底写 run-persist-failed-*.json）
        self.persist_failed = False
        # P3.5 副作用自愈：实验前后拍 registry 快照，diff 出被改动的键。
        # 默认影子模式（只记录不回滚），策略见 config/registry_rollback_policy.json。
        self.side_effect_reports: List[Dict[str, Any]] = []
        self._side_effect_watcher: Optional[SideEffectWatcher] = None

    # ── 生命周期 ──────────────────────────────────────────────────────────────
    def _classes(self):
        # 用**服务类所在的模块**取 NodeStatus/Workflow，而不是 `from ..server import`。
        # 服务以 `-m` 启动时包名可能与这里推断的不同，直接 import 会拿到另一份模块对象，
        # 于是枚举成员身份不相等（status 打印是 "completed"，但 `in (NodeStatus.COMPLETED,)`
        # 判 False），取消/回收这些按枚举比较的逻辑就会失效。
        module = None
        for klass in type(self.svc).__mro__:          # 子类（如自测里的 FakeSvc）继续往上找
            candidate = sys.modules.get(klass.__module__)
            if candidate is not None and hasattr(candidate, "NodeStatus") and hasattr(candidate, "Workflow"):
                module = candidate
                break
        if module is None:                                  # 兜底
            from .. import server as module  # type: ignore
        return module.Workflow, module.WorkflowNode, module.NodeStatus

    def prepare(self) -> None:
        Workflow, WorkflowNode, NodeStatus = self._classes()
        self._NS = NodeStatus
        self._assign_names(self.ir.get("pipeline") or [])
        nodes = {}
        for st in ir_mod.iter_all_statements(self.ir.get("pipeline") or []):
            name = self.node_names.get(id(st))
            if not name or st.get("op") == OP_LET:
                continue
            node_type = self._node_type(st)
            if node_type is None:
                continue
            nodes[name] = WorkflowNode(id=name, type=node_type, config={}, depends=[])
        settings = {
            "description": (self.ir.get("meta") or {}).get("description", ""),
            "source": "ir",
            "irHash": ir_mod.ir_hash(self.ir),
            "nodeTimeout": (self.ir.get("defaults") or {}).get("nodeTimeout", 300),
        }
        self.workflow = Workflow(
            id=self.workflow_id,
            name=(self.ir.get("meta") or {}).get("name") or self.ir.get("id"),
            nodes=nodes,
            status=NodeStatus.RUNNING,
            context=dict(self.ctx),
            settings=settings,
        )
        self.svc._workflows[self.workflow_id] = self.workflow
        if self.cancel is None:
            self.cancel = threading.Event()
        self.svc._cancel_events[self.workflow_id] = self.cancel
        # 内存回收：IR 运行同样要受 `QMCLAW_MAX_WORKFLOWS` 约束，
        # 否则长时间跑 DSL 会把 `_workflows` / `_cancel_events` 撑大。
        evict = getattr(self.svc, "_evict_old_workflows", None)
        if evict is not None:
            lock = getattr(self.svc, "_workflows_lock", None)
            try:
                if lock is not None:
                    with lock:
                        evict()
                else:
                    evict()
            except Exception:
                pass

    def run(self) -> Dict[str, Any]:
        if self.workflow is None:
            self.prepare()
        try:
            self._preflight()
            self._gate_bind_task()
            self._block(self.ir.get("pipeline") or [])
        except Halt as exc:
            self.status, self.error = "failed", str(exc)
        except NeedsHuman as exc:
            self.status = "needs_human"
            self.error = str(exc)
            self.needs_human = {
                "question": getattr(exc, "question", "") or str(exc),
                "options": list(getattr(exc, "options", []) or []),
            }
        except QuotaExceeded as exc:
            # 额度闸门：一次硬件都没下发（§13.4）
            self.status, self.error = "quota_exceeded", str(exc)
        except Cancelled:
            self.status, self.error = "cancelled", None
        except BudgetExceeded as exc:
            self.status, self.error = "budget_exceeded", exc.message
        except UndefinedRef as exc:
            self.status, self.error = "failed", f"undefined ref: {exc}"
        except Exception as exc:  # 执行器自身的 bug 不能吞
            self.status, self.error = "failed", f"executor crashed: {exc}"
            traceback.print_exc()
        finally:
            self._finalize()
            self._gate_charge_wall()
            self._persist()
        return self._report()

    def _finalize(self) -> None:
        """把没跑到的节点收成 skipped，正在跑的收成 cancelled。"""
        if self.workflow is None:
            return
        for node in self.workflow.nodes.values():
            if node.status == self._NS.RUNNING:
                if self.status in ("cancelled", "needs_human"):
                    node.status = self._NS.CANCELLED
                else:
                    node.status = self._NS.FAILED
            elif node.status == self._NS.PENDING:
                node.status = self._NS.SKIPPED
        self.workflow.status = {
            "completed": self._NS.COMPLETED,
            "cancelled": self._NS.CANCELLED,
            "budget_exceeded": getattr(self._NS, "BUDGET_EXCEEDED", self._NS.FAILED),
            "quota_exceeded": getattr(self._NS, "QUOTA_EXCEEDED", self._NS.FAILED),
            "needs_human": getattr(self._NS, "NEEDS_HUMAN", self._NS.FAILED),
            "failed": self._NS.FAILED,
        }.get(self.status, self._NS.FAILED)
        self.workflow.error = self.error
        self.workflow.completed_at = time.time()
        if self.effects is not None:
            declared = {w for item in self.effects.get("planned") or []
                        for w in item.get("writes") or []}
            observed = self._collect_observed_writes(declared)
            drift = self._collect_drift(declared)
            self.effects = dict(
                self.effects,
                observed=observed,
                undeclared=[k for k in observed if k not in declared],
                drift=drift,
            )
            if self.effects["undeclared"]:
                self.warnings.append(
                    "真机实际改了未声明的参数："
                    + ", ".join(self.effects["undeclared"])
                    + "（适配器的写入语义表可能过期了）"
                )
            for ev in drift:
                if ev.get("level") != "warn":
                    continue
                rel = ev.get("rel")
                rel_txt = f"，相对变化 {rel * 100:.1f}%" if isinstance(rel, (int, float)) else ""
                self.warnings.append(
                    f"真机把 {ev['key']} 从 {ev['from']:g} 挪到 {ev['to']:g}"
                    f"（Δ{ev['delta']:g}{rel_txt}，节点 {ev.get('node')}）。"
                    "频率类参数被挪动会改变后续实验的基准——"
                    "后面的门禁结果未必是机器本身变差"
                )
        # 运行结束后取消事件没有意义了；留着会让 `_cancel_events` 随运行次数无限增长
        try:
            self.svc._cancel_events.pop(self.workflow_id, None)
        except (AttributeError, KeyError):
            pass

        # 结局判定放在最后：此时 bindings 齐了，才知道门禁过没过
        self.outcome = self._compute_outcome()
        self._outcome_computed = True
        try:
            if self.workflow is not None and hasattr(self.workflow, "settings"):
                self.workflow.settings = dict(self.workflow.settings or {})
                self.workflow.settings["outcome"] = self.outcome
                # 等人拍板时把问题也挂到 workflow 上：调用方（前端 / qwf_run.py）
                # 只看 /workflows/status 一个接口就能拿到 question/options
                if self.needs_human:
                    self.workflow.settings["needs_human"] = dict(self.needs_human)
        except Exception:
            pass

    def _compute_outcome(self) -> Dict[str, Any]:
        """把运行结果翻译成 outcome 契约。

        取值：achieved / not_achieved / budget_exhausted / quota_exceeded /
              needs_human / error / cancelled
        判定顺序：needs_human > 预算/取消/异常 > 目标达成与否。
        """
        meta = self.ir.get("meta") or {}
        has_goal = bool(self.ir.get("goal")) or bool(meta.get("goal_lowered"))
        detail: Dict[str, Any] = {}
        status = "achieved"
        reason = ""

        # ① 任何节点要求人工介入 → needs_human（流程不算"跑完就达成"）
        for name, res in (self.bindings or {}).items():
            res = res if isinstance(res, dict) else {}
            if res.get("status") == "needs_human" or res.get("needs_human") is True:
                status = "needs_human"
                reason = res.get("question") or res.get("message") or f"{name} 需要人工介入"
                detail["node"] = name
                detail["question"] = res.get("question")
                detail["options"] = res.get("options") or []
                break
        if status != "needs_human" and self.status == "needs_human":
            status = "needs_human"
            reason = self.error or "需要人工介入"
            detail.update(self.needs_human or {})

        if status != "needs_human":
            if self.status == "budget_exceeded":
                status = "budget_exhausted"
                reason = self.error or "预算耗尽"
            elif self.status == "quota_exceeded":
                # 账本额度（跨 qwf），与 IR 自身的 budget_exceeded 区分开
                status = "quota_exceeded"
                reason = self.error or "task 额度耗尽"
                detail["gate"] = list(self.gate_notes[-5:])
            elif self.status == "cancelled":
                status = "cancelled"
                reason = self.error or "已取消"
            elif self.status != "completed":
                status = "error"
                reason = self.error or "执行失败"
            elif has_goal:
                gate = (self.bindings or {}).get("goal_assert")
                gate = gate if isinstance(gate, dict) else {}
                detail = {
                    "metric": gate.get("metric"),
                    "value": gate.get("value"),
                    "threshold": gate.get("threshold"),
                    "operator": gate.get("operator"),
                }
                criteria_ref = gate.get("criteriaRef")
                if not criteria_ref:
                    ref_meta = meta.get("goal_criteria_ref") or {}
                    criteria_ref = ref_meta.get("ref") if isinstance(ref_meta, dict) else None
                if criteria_ref:
                    detail["criteriaRef"] = criteria_ref
                passed = gate.get("passed")
                if passed is True:
                    status = "achieved"
                    reason = "目标达成"
                elif passed is False:
                    status = "not_achieved"
                    reason = "目标未达成"
                else:
                    status = "not_achieved"
                    reason = "目标未判定（门禁节点没有产出 passed）"

        # D5-④：outcome 契约扩展 —— 副作用违规与常用指标一起带出去，
        # 上层不必再去翻 run 记录的 sideEffects / bindings。
        restored_keys = set()
        for rep in (getattr(self, "side_effect_reports", None) or []):
            for item in (rep.get("restored") or []):
                if isinstance(item, dict) and item.get("key"):
                    restored_keys.add(str(item.get("key")))
        violations: List[Dict[str, Any]] = []
        auto_reverted = False
        for rep in (getattr(self, "side_effect_reports", None) or []):
            if rep.get("restored"):
                auto_reverted = True
            for ch in (rep.get("changes") or []):
                key = str(ch.get("key") or "")
                if key in restored_keys:
                    continue
                violations.append({
                    "node": rep.get("node"), "key": key,
                    "before": ch.get("before"), "after": ch.get("after"),
                    "reason": next((s.get("reason") for s in (rep.get("skipped") or [])
                                    if isinstance(s, dict) and s.get("key") == key), "not_restored"),
                })
        metrics: Dict[str, Any] = {}
        keep = ("contrast", "p1_i", "p1_x", "separation", "snr", "T1_us", "T2_us",
                "R_squared", "dataset", "dataset_id")
        for name, res in (self.bindings or {}).items():
            res = res if isinstance(res, dict) else {}
            src = res.get("metrics") if isinstance(res.get("metrics"), dict) else res
            if not isinstance(src, dict):
                continue
            picked = {k: src[k] for k in keep if isinstance(src.get(k), (int, float))}
            if picked:
                metrics[name] = picked
        out = {"status": status, "reason": reason, "detail": detail}
        # 阶段 6：goal outcome 同步给出规范状态（旧的 status 值保持不变）
        out["canonicalStatus"] = _STATUS_CANONICAL.get(status, "failed")
        out["reasonCode"] = status
        if violations:
            out["violations"] = violations
        if auto_reverted:
            out["autoReverted"] = True
        if metrics:
            out["metrics"] = metrics

        # 阶段 4：把常用指标折成比特状态机证据，输出当前标定阶段
        # （unknown → freq_locked → amp_calibrated → state_discriminated
        #   → coherence_measured → characterized）
        evidence: Dict[str, Any] = {}
        for _name, res in (self.bindings or {}).items():
            res = res if isinstance(res, dict) else {}
            src = res.get("metrics") if isinstance(res.get("metrics"), dict) else res
            if not isinstance(src, dict):
                continue
            for key, target in (("contrast", "contrast"), ("dmu_sig", "dmu_sig"),
                                ("T1_us", "t1_us"), ("T2_us", "t2_star_us"),
                                ("xeb_p", "xeb_p"),
                                ("xeb_gate_fidelity", "xeb_gate_fidelity")):
                val = src.get(key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    evidence[target] = val
        if evidence:
            try:
                from .registry import derive_state  # 绝对导入层，兼容顶层 `import dsl`
                state = derive_state(evidence)
            except Exception:
                state = None
            if isinstance(state, dict):
                out["qubit_state"] = state
        return out

    def _ensure_outcome(self) -> Dict[str, Any]:
        """outcome 只算一次；`_finalize` 算过就别重复。"""
        if not self._outcome_computed:
            self.outcome = self._compute_outcome()
            self._outcome_computed = True
        return self.outcome

    def _gate_task_snapshot(self) -> Dict[str, Any]:
        """读一次 task 现状（拿写清单哈希 / 票据 id / 额度阻塞次数）。"""
        if not self.gate_task:
            return {}
        res = self._gate_get(f"/task/{self.gate_task}")
        task = res.get("task") if isinstance(res, dict) else None
        return task if isinstance(task, dict) else {}

    def _persist_fallback_dirs(self) -> list:
        """run 落档失败时的兜底目录候选（按优先级）。

        优先 workflow-runs 目录，其次临时目录。注意：目录可能存在但**不可写**
        （例如受限部署环境），所以兜底写入要逐个试，而不是只试第一个。
        """
        dirs = []
        try:
            from .. import server as _srv
            dirs.append(Path(_srv.DATA_DIR))
        except Exception:
            pass
        dirs.append(Path(tempfile.gettempdir()) / "qmclaw-run-failures")
        out = []
        for d in dirs:
            if d not in out:
                out.append(d)
        return out

    def _persist_fallback_dir(self) -> Path:
        """run 落档失败时的兜底目录（第一个候选，保留兼容）。"""
        return self._persist_fallback_dirs()[0]

    def _persist(self) -> None:
        """把这次 run 落档。

        D1-③：落档失败不能只留一句 warning —— 那样 run 级 JSON 会静默消失，
        事后没人知道发生了什么。现在失败会：
          1. 置 `persistFailed`（report 里能看见）；
          2. 写一份 `run-persist-failed-<workflow_id>.json` 到 workflow-runs 目录；
          3. 把告警写进 warnings。
        """
        extra: Dict[str, Any] = {
            "irVersion": IR_VERSION,
            "irHash": ir_mod.ir_hash(self.ir),
            "ir": self.ir,
            "usage": self.budget.usage(),
            "statements": self.audit,
            "dryRun": self.dry_run,
            "outcome": self.outcome,
            "source": (self.ir.get("meta") or {}).get("source"),
        }
        # D1-④：审计链补齐 —— task / 写清单 / 票据 / 额度拦截 / 闸门备注
        snap = self._gate_task_snapshot()
        manifests = [m for m in (snap.get("manifests") or []) if isinstance(m, dict)]
        tickets = [t for t in (snap.get("tickets") or []) if isinstance(t, dict)]
        events = [e for e in (snap.get("events") or []) if isinstance(e, dict)]
        extra.update({
            "taskId": self.gate_task,
            "manifestHash": manifests[-1].get("hash") if manifests else None,
            "manifestHashes": [m.get("hash") for m in manifests],
            "ticketIds": [t.get("id") for t in tickets],
            "quotaBlocked": len([e for e in events if e.get("kind") == "quota_blocked"]),
            "gateNotes": list(self.gate_notes),
            # P3.5：实验前后 registry 快照的 diff 与回滚结果
            "sideEffects": list(self.side_effect_reports),
        })
        try:
            try:
                res = self.svc._persist_workflow_run(self.workflow_id, self.legacy, extra=extra)
            except TypeError:
                # 老签名：退化成只落上下文
                res = self.svc._persist_workflow_run(self.workflow_id, self.legacy)
            if isinstance(res, dict) and res.get("ok") is False:
                raise RuntimeError(res.get("error") or "persist failed")
            self.persist_failed = False
        except Exception as exc:
            self.persist_failed = True
            self.warnings.append(f"persist failed: {exc}")
            payload = json.dumps({
                "workflowId": self.workflow_id,
                "failedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "error": str(exc),
                "extra": extra,
                "context": _slim(self.legacy),
            }, ensure_ascii=False, indent=2, default=str)
            last_err = None
            for base in self._persist_fallback_dirs():
                try:
                    base.mkdir(parents=True, exist_ok=True)
                    path = base / f"run-persist-failed-{self.workflow_id}.json"
                    path.write_text(payload, encoding="utf-8")
                    self.warnings.append(f"persist fallback written: {path}")
                    break
                except Exception as exc2:
                    last_err = exc2
            else:
                self.warnings.append(f"persist fallback failed: {last_err}")

    def _report(self) -> Dict[str, Any]:
        self._ensure_outcome()
        return {
            "ok": self.status == "completed",
            "status": self.status,
            # 阶段 6：规范状态（旧的 status 值保留，前端不受影响）
            "canonicalStatus": _STATUS_CANONICAL.get(self.status, "failed"),
            "reasonCode": self.status,
            "error": self.error,
            "workflowId": self.workflow_id,
            "irHash": ir_mod.ir_hash(self.ir),
            "usage": self.budget.usage(),
            "plan": self.plan,
            "effects": self.effects,
            "statements": self.audit,
            "bindings": {k: _slim(v) for k, v in self.bindings.items()},
            "warnings": self.warnings,
            "outcome": self.outcome,
            "dryRun": self.dry_run,
            "taskId": self.gate_task,
            "persistFailed": self.persist_failed,
            "gateNotes": list(self.gate_notes),
            "sideEffects": list(self.side_effect_reports),
        }

    # ── 名字分配 ──────────────────────────────────────────────────────────────
    def _assign_names(self, stmts: List[Dict[str, Any]]) -> None:
        for st in stmts:
            if not isinstance(st, dict):
                continue
            self._seq += 1
            name = st.get("as") or f"s{self._seq}"
            self.node_names[id(st)] = str(name)
            for key in OP_BODY_KEY.get(st.get("op"), ()):
                self._assign_names(st.get(key) or [])

    def _node_type(self, st: Dict[str, Any]) -> Optional[str]:
        op = st.get("op")
        if op == OP_SAY:
            return "notify" if st.get("channel") == "notify" else "print"
        from .const import OP_TO_NODE
        return OP_TO_NODE.get(op)

    # ── 语句执行 ──────────────────────────────────────────────────────────────
    def _block(self, stmts: List[Dict[str, Any]]) -> None:
        for st in stmts:
            if self.cancel.is_set():
                raise Cancelled()
            self.budget.check_wall()
            self._stmt(st)

    def _stmt(self, st: Dict[str, Any]) -> None:
        name = self.node_names.get(id(st)) or "?"
        op = st.get("op")
        if not self._when_ok(st.get("when")):
            self._mark(name, "skipped", note=f"when=false: {st.get('when')!r}")
            return
        if op == OP_LET:
            self._exec_let(st)
        elif op == OP_REPEAT:
            self._exec_repeat(st)
        elif op == OP_IF:
            self._exec_if(st)
        elif op == OP_CALL:
            raise Halt(f"{name}: call 子工作流在 v1 未实现（P4）")
        else:
            self._dispatch(st, name)

    def _exec_let(self, st: Dict[str, Any]) -> None:
        name = st.get("name") or self.node_names.get(id(st))
        value = self._resolve(st.get("value"))
        if name in self.bindings and not st.get("overwrite", True):
            self.warnings.append(f"let {name} 已存在，按 overwrite=false 保留原值")
            return
        self.bindings[name] = {
            "ok": True, "status": "completed", "value": value, "metrics": {},
            "dataset": None, "artifacts": {}, "stdout": "", "error": None,
            "cost": {"wallSeconds": 0, "llmCalls": 0},
        }
        self.audit.append({"op": OP_LET, "as": name, "status": "completed"})

    def _exec_repeat(self, st: Dict[str, Any]) -> None:
        body = st.get("body") or []
        max_rounds = int(st.get("maxRounds") or 1)
        export = [n for n in (st.get("export") or []) if isinstance(n, str)]
        body_names = self._collect_names(body)
        rounds = 0
        try:
            for round_idx in range(1, max_rounds + 1):
                if self.cancel.is_set():
                    raise Cancelled()
                self.budget.check_wall()
                rounds = round_idx
                # S2：每轮重新绑定，不继承上一轮的值（否则 stopWhen 会误判）
                for nm in body_names:
                    self.bindings.pop(nm, None)
                self._block(body)
                if st.get("stopWhen") and self._compare(st["stopWhen"]):
                    self.audit.append({"op": OP_REPEAT, "round": round_idx,
                                       "status": "completed", "stoppedBy": "stopWhen"})
                    break
            else:
                self.audit.append({"op": OP_REPEAT, "round": rounds,
                                   "status": "completed", "stoppedBy": "maxRounds"})
        finally:
            for nm in body_names:
                if nm not in export:
                    self.bindings.pop(nm, None)
        if st.get("export"):
            self.audit.append({"op": OP_REPEAT, "export": export, "rounds": rounds})

    def _exec_if(self, st: Dict[str, Any]) -> None:
        branch = "then" if self._when_ok(st.get("cond")) else "else"
        body = st.get(branch) or []
        names = self._collect_names(body)
        self._block(body)
        # 分支内绑定在体外不可静态引用（validate 已拦），这里只保留运行期值供审计
        for nm in names:
            if nm in self.bindings:
                self.audit.append({"op": OP_IF, "branch": branch, "as": nm,
                                   "status": "completed"})

    # ── 下发前闸门：点数 / 预计耗时 / 写入语义 ────────────────────────────────
    def _preflight(self) -> None:
        """碰硬件之前先把成本估算算清楚。

        lqcs 的三类坑都在这儿拦（表在适配器里，这里是纯消费方）：

        1. `policy.readOnly=true` 却有条目会自动改参数 → 直接失败，一次都不下发；
        2. 计划写入条数 > `budget.maxWrites` → 预算错；
        3. 某步预计耗时 > 它自己的 `timeout` → 直接失败（真机上必然超时，
           白占时间还会把服务拖成 stuck，`sq.pulse_shape` 300 s 那次就是这么来的）；
        4. 合计预计耗时 > `budget.maxWallSeconds` → 预算错。

        干跑不碰硬件，所以只记录不拦截——否则一条 `sq.pulse_shape` 会被自己的
        估计值挡住，看不到后面实验的干跑结果。
        """
        hook = getattr(self.svc, "_plan_ir", None)
        if not callable(hook):
            return
        try:
            plan = hook(self.ir)
        except Exception:
            return
        if not plan:
            return
        from .plan import plan_checks

        self.plan = plan
        self.effects = {
            "planned": [
                {"as": s["as"], "fn": s["fn"], "mode": s["writeMode"], "writes": s["writes"]}
                for s in plan.get("steps") or [] if s["writesEffective"]
            ],
        }
        checks = plan_checks(plan, self.ir)
        for w in checks["warnings"]:
            if w not in self.warnings:
                self.warnings.append(w)
        tot = plan.get("totals") or {}
        self.warnings.insert(0, (
            f"计划：{tot.get('points', 0)} 点 / 约 {tot.get('seconds', 0):g}s / "
            f"{tot.get('writeSteps', 0)} 条实验会自动改参数"
        ))
        if self.dry_run:
            return

        if checks["errors"]:
            raise Halt("；".join(checks["errors"]))

        for s in plan.get("steps") or []:
            timeout = s.get("timeout")
            if timeout and s.get("seconds") and float(s["seconds"]) > float(timeout):
                why = "；该实验走自适应扫描，名义点数只是下界" if s.get("adaptive") else ""
                raise Halt(
                    f"{s['as']}({s['fn']}): 预计运行 {float(s['seconds']):g}s，"
                    f"超过语句 timeout={timeout:g}s{why}。"
                    f"把 timeout 提到 ≥{float(s['seconds']) * 1.5:.0f}s，或换实验/收窄扫描"
                )

        wall = (self.ir.get("budget") or {}).get("maxWallSeconds")
        est = float(tot.get("seconds") or 0)
        if est and isinstance(wall, (int, float)) and est > float(wall):
            raise BudgetExceeded(
                "maxWallSeconds",
                f"计划预计 {est:g}s，超过预算 maxWallSeconds={float(wall):g}s"
                "（预计值来自适配器的耗时表，可能偏保守）",
            )

    def _collect_drift(self, declared: Optional[set] = None) -> List[Dict[str, Any]]:
        """真机每次写入挪动了多少（`to <新值> from <旧值>`）。

        "声明会写"不等于"写多少"：本轮真机里 `sq.spectroscopy` 把 `f10` 从 3.2037
        改成 4.0884（**0.885 GHz**），把后面所有实验的基准都挪走了——门禁因此全红，
        但节点状态一直是 completed。这类"静默改频率"必须自己算出来报上去。

        哪些键算"危险漂移"、挪多少算多，是厂商知识 → 问后端适配器
        （`svc._drift_threshold`）；问不到就只记录不报警。
        """
        import ast

        events: List[Dict[str, Any]] = []
        nodes = list((self.workflow.nodes or {}).values()) if self.workflow else []
        for node in nodes:
            res = node.result if isinstance(node.result, dict) else {}
            text = res.get("stdout") or ""
            if not text:
                continue
            for m in _SETITEM_VAL_RE.finditer(text):
                try:
                    path = ast.literal_eval(m.group("path"))
                except (ValueError, SyntaxError):
                    continue
                if not isinstance(path, (list, tuple)):
                    continue
                name = m.group("name") or m.group("bare") or ""
                key = self._write_key(name, list(path), set(declared or ()))
                try:
                    old = float(m.group("old"))
                    new = float(m.group("new"))
                except (TypeError, ValueError):
                    continue
                delta = new - old
                rel = (abs(delta) / abs(old)) if old else None
                events.append({
                    "node": getattr(node, "id", None) or getattr(node, "name", None),
                    "key": key,
                    "from": old,
                    "to": new,
                    "delta": delta,
                    "rel": rel,
                    "level": self._drift_level(key, delta, rel),
                })
        return events

    def _drift_level(self, key: str, delta: float, rel: Optional[float]) -> str:
        """这次挪动算不算"危险漂移"。阈值问后端（厂商知识），问不到 → 只记录。"""
        hook = getattr(self.svc, "_drift_threshold", None)
        if not callable(hook):
            return "info"
        try:
            th = hook(key)
        except Exception:
            return "info"
        if not th:
            return "info"
        abs_tol = th.get("abs")
        rel_tol = th.get("rel")
        if abs_tol is not None and abs(delta) > float(abs_tol):
            return "warn"
        if rel_tol is not None and rel is not None and rel > float(rel_tol):
            return "warn"
        return "info"

    def _collect_observed_writes(self, declared: Optional[set] = None) -> List[str]:
        """从各节点 stdout 里把真机上实际发生的参数写入抠出来（`__setitem__ ... in path`）。

        这是"声明 vs 实际"的闭环：表说这一步会写 `fread`，真机却写了 `PiGate.amp`，
        说明表过期了或者厂商改了实现——这种漂移必须能在报告里看到。

        键名要跟写入语义表对得上，所以参数名 + 注册表路径要翻译一次
        （`PiGate` + `amp` → `PiGate.amp`），翻译规则问后端适配器。
        """
        import ast

        declared = set(declared or set())
        keys: set = set()
        nodes = list((self.workflow.nodes or {}).values()) if self.workflow else []
        for node in nodes:
            res = node.result if isinstance(node.result, dict) else {}
            text = res.get("stdout") or ""
            if not text:
                continue
            covered: List[tuple] = []
            for m in _SETITEM_RE.finditer(text):
                try:
                    path = ast.literal_eval(m.group("path"))
                except (ValueError, SyntaxError):
                    continue
                if not isinstance(path, (list, tuple)):
                    continue
                covered.append(m.span())
                name = m.group("name") or m.group("bare") or ""
                keys.add(self._write_key(name, list(path), declared))
            for m in _SETITEM_PATH_RE.finditer(text):
                if any(s <= m.start() < e for s, e in covered):
                    continue          # 已经按新格式解析过了
                try:
                    path = ast.literal_eval(m.group(1))
                except (ValueError, SyntaxError):
                    continue
                if isinstance(path, (list, tuple)) and path:
                    keys.add(".".join(str(p) for p in path))
        return sorted(keys)

    def _write_key(self, name: str, path: List[str], declared: set) -> str:
        """真机 stdout 的一次写入 → 写入语义表里用的键名。

        优先问后端适配器（容器名是厂商知识）；问不到就退回"声明表里出现过的容器"。
        """
        name = str(name or "")
        if name.startswith("qobj."):
            name = name[5:]
        if not path:
            return name
        tail = str(path[-1])
        if len(path) == 1:
            return tail               # 老格式：path 本身就是键名
        hook = getattr(self.svc, "_write_key", None)
        if callable(hook):
            try:
                got = hook(name, path)
                if got:
                    return str(got)
            except Exception:
                pass
        containers = {k.split(".")[0] for k in declared if "." in k}
        if tail in containers:
            return f"{tail}.{name}"
        return name

    def _estimate_points(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """让后端适配器估算这条实验的扫描点数。

        "哪个参数不给值会展开成多少点"是厂商知识，按 S7 的边界放在适配器里；
        执行器只负责拿结果去扣额度，不做任何厂商假设。没有钩子就返回 None（不拦）。
        """
        hook = getattr(self.svc, "_estimate_scan_points", None)
        if not callable(hook):
            return None
        try:
            return hook(config.get("fn"), config.get("params") or {})
        except Exception:
            return None

    # ── P3：task 额度 + 授权票据闸门（§13.4） ────────────────────────────────
    def _gate_task_id(self) -> Optional[str]:
        """本次 run 归属哪个 task。

        优先级：`ctx.taskId` > `ir.context.taskId` > `ir.meta.taskId` > `auto-<ctx.requestId>`。
        都没有 → None（不限额：既有的近 100 条 `.qwf` 行为完全不变）。
        """
        for src in (self.ctx, self.ir.get("context") or {}, self.ir.get("meta") or {}):
            if not isinstance(src, dict):
                continue
            tid = src.get("taskId") or src.get("task_id")
            if tid:
                return str(tid)
        rid = self.ctx.get("requestId") or self.ctx.get("request_id")
        if rid:
            return "auto-" + str(rid)
        return None

    def _gate_enforced(self) -> bool:
        """是否强制要求票据（没有票据也要拦）。

        默认不强制：只有当 task 里已经存在票据时，闸门才真的启用 ——
        这样"人还没开始授权"的日常调试不会被一张空票据表挡住。
        """
        if str(os.environ.get("QMCLAW_ENFORCE_TICKETS") or "").strip() == "1":
            return True
        for key in ("enforceTickets", "enforce_tickets"):
            if self.ctx.get(key):
                return True
            ctx = self.ir.get("context") or {}
            if isinstance(ctx, dict) and ctx.get(key):
                return True
        return False

    def _gate_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """调 task_queue（:3009）。连不上就**放行**并记一条 notes。

        失败必须是开放的：账本服务挂了不能把真机实验室整个停摆，
        但每次降级都要留痕，报表里能看出"这一跑其实没受管辖"。
        """
        post = getattr(self.svc, "_queue_post", None)
        if not callable(post):
            note = {"gate": path, "ok": True, "skipped": "service has no _queue_post"}
            self.gate_notes.append(note)
            return note
        try:
            res = post(path, payload, timeout=10.0)
        except Exception as exc:
            note = {"gate": path, "ok": True, "skipped": f"task_queue 不可达: {exc}"}
            self.gate_notes.append(note)
            return note
        if not isinstance(res, dict):
            note = {"gate": path, "ok": True, "skipped": "task_queue 返回非对象"}
            self.gate_notes.append(note)
            return note
        self.gate_notes.append({"gate": path, **{k: res.get(k) for k in
                                                ("ok", "reason", "authorizedBy", "needsHuman", "remaining")
                                                if k in res}})
        return res

    def _gate_charge_wall(self) -> None:
        """把本次 run 实际占用的机时回填到 task 账本（让 wallSeconds 不再恒为 0）。

        放在 `_finalize()` 之后：哪怕 run 被额度拦下，已经花掉的机时也要记账。
        """
        if not self.gate_task:
            return
        try:
            seconds = round(float(self.budget.wall()), 2)
        except Exception:
            return
        if seconds <= 0:
            return
        self._gate_post("/task/charge", {"taskId": self.gate_task, "kind": "wallSeconds",
                                         "count": seconds})

    def _gate_get(self, path: str) -> Dict[str, Any]:
        """读 task_queue（:3009）。连不上返回 `{"error": ...}`（调用方自行降级）。"""
        get = getattr(self.svc, "_queue_get", None)
        if not callable(get):
            return {"error": "service has no _queue_get"}
        try:
            res = get(path, timeout=10.0)
        except Exception as exc:
            return {"error": str(exc)}
        return res if isinstance(res, dict) else {"error": "task_queue 返回非对象"}

    def _gate_bind_task(self) -> None:
        """run 开始时确定 task（**不扣额度**）。

        额度按**实验节点**扣，不按 run 扣：一次 goal 循环可能下发 3 次实验，
        只在 run 头上扣 1 次就等于放开了 3 倍——2026-11-05 那次事故就是这么失控的。
        """
        tid = self._gate_task_id()
        if not tid:
            return
        if self.dry_run:
            return      # 干跑不碰硬件，也不该吃掉 task 的实验额度
        self.gate_task = tid
        probe = self._gate_get(f"/task/{tid}")
        if probe.get("error") or not probe.get("task"):
            goal = self.ir.get("goal") or {}
            self._gate_post("/task", {
                "taskId": tid,
                "objective": goal.get("objective") or "",
                "stopCondition": goal.get("success") or {},
                "qubits": self._gate_qubits(),
            })

    def _gate_charge_experiment(self, node_name: str) -> None:
        """下发前扣 1 次实验额度（超额 → QuotaExceeded，一次命令都不发出去）。"""
        if not self.gate_task:
            return
        res = self._gate_post("/task/charge", {"taskId": self.gate_task, "kind": "experiments",
                                               "count": 1, "detail": {"node": node_name}})
        if res.get("ok") is False:
            reason = res.get("reason") or ""
            if reason == "quota_exceeded":
                raise QuotaExceeded(
                    f"task {self.gate_task} 的实验额度已用尽：{res.get('detail') or '超额度'}；"
                    f"剩余 {res.get('remaining')}。要再跑请新建 task 或调高 budgets.experiments")
            if reason == "task_released":
                raise QuotaExceeded(f"task {self.gate_task} 已释放，不能再下发实验")
            if reason == "unknown_task":
                raise QuotaExceeded(f"task {self.gate_task} 不存在，不能下发实验")
            self.gate_notes.append(f"额度检查失败：{reason or res.get('error')}")

    def _gate_qubits(self) -> List[str]:
        """扫出流程里出现的比特（task 的 `qubits` 字段只是索引，不做判据）。"""
        out: List[str] = []

        def walk(stmts: Any) -> None:
            if isinstance(stmts, dict):
                stmts = [stmts]
            for st in stmts or []:
                if not isinstance(st, dict):
                    continue
                if st.get("op") == OP_MEASURE:
                    q = st.get("qubit")
                    if isinstance(q, str) and q and not q.startswith("$") and q not in out:
                        out.append(q)
                for key in (OP_BODY_KEY, "body", "else", "then", "statements"):
                    walk(st.get(key))

        walk(self.ir.get("pipeline") or [])
        return out

    def _gate_check_dataset_quota(self, node_name: str) -> None:
        """下发前扣 1 个数据集额度（此时还不知道编号，先占位）。

        真实编号在实验跑完后由 `_gate_record_dataset` 回填。
        """
        if not self.gate_task:
            return
        res = self._gate_post("/task/charge", {"taskId": self.gate_task, "kind": "datasets",
                                               "count": 1, "detail": {"node": node_name}})
        if res.get("ok") is False and res.get("reason") == "quota_exceeded":
            raise QuotaExceeded(f"task {self.gate_task} 的数据集额度已用尽（节点 {node_name}）："
                                f"剩余 {res.get('remaining')}")

    def _gate_refund(self, node_name: str, kind: str, count: int = 1, why: str = "") -> None:
        """把预扣的额度还回去（D1-②）。

        闸门把节点拦下时，一次硬件都没下发，额度就不该被扣 ——
        否则"没跑的实验"会吃掉 task 的额度，批准后重跑会撞 quota_exceeded。
        """
        if not self.gate_task:
            return
        self._gate_post(f"/task/{self.gate_task}/refund",
                        {"kind": kind, "count": count, "node": node_name, "why": why})

    def _gate_record_dataset(self, node_name: str, dataset: Any) -> None:
        """实验跑完把真实数据集号回填到账本（额度是下发前占位的）。"""
        if not self.gate_task or dataset is None:
            return
        self._gate_post(f"/task/{self.gate_task}/dataset",
                        {"dataset": dataset, "node": node_name})

    def _gate_write_keys(self, config: Dict[str, Any]) -> List[str]:
        """这条实验会静默改哪些寄存器（厂商知识在适配器里）。"""
        adapter = getattr(self.svc, "_lqcs", None)
        hook = getattr(adapter, "write_effects", None)
        fn, params = config.get("fn"), config.get("params") or {}
        eff = None
        if callable(hook):
            try:
                eff = hook(fn, params)
            except Exception:
                eff = None
        if eff is None:
            try:
                from .backends.lqcs import write_effects as _we
                eff = _we(fn, params)
            except Exception:
                return []
        if not isinstance(eff, dict) or not eff.get("effective"):
            return []
        return [str(k) for k in (eff.get("writes") or [])]

    def _gate_authorize(self, config: Dict[str, Any], node_name: str) -> None:
        """下发前的授权检查：不在票据内 → NeedsHuman（硬件不下发）。"""
        if not self.gate_task:
            return
        keys = self._gate_write_keys(config)
        if not keys:
            return
        res = self._gate_post("/task/authorize", {
            "taskId": self.gate_task,
            "keys": keys,
            "qubit": config.get("qubit"),
            "deltas": {},
            "enforce": self._gate_enforced(),
            "node": node_name,
        })
        if res.get("ok") or res.get("skipped"):
            return
        reason = res.get("reason") or "写操作未获授权"
        self.audit.append({"op": "gate", "as": node_name, "status": "blocked",
                           "ok": False, "error": reason, "keys": keys})
        raise NeedsHuman(
            f"{node_name} 要写 {keys}，但{reason}",
            question=(f"节点 {node_name} 会修改寄存器 {keys}（{config.get('fn')}），"
                      f"当前 task {self.gate_task} 没有覆盖这些键的票据。怎么处理？"),
            options=["批准写清单并签发票据后重跑", "跳过该节点", "中止这次流程"],
        )

    def _dispatch(self, st: Dict[str, Any], name: str) -> None:
        op = st.get("op")
        effect = st.get("effect") or OP_EFFECT.get(op, "compute")
        t0 = time.time()
        try:
            compiled = build_node(st, name, resolve=self._resolve, env=self.env)
        except UndefinedRef:
            raise
        except Exception as exc:
            raise Halt(f"{name}: 编译节点失败: {exc}")

        if compiled is None:      # let
            self._exec_let(st)
            return

        node = self._ensure_node(name, compiled["type"], compiled["config"], compiled["depends"])
        if st.get("timeout"):
            node.config["timeout"] = st["timeout"]
        if st.get("retries") is not None:
            node.config["retries"] = st["retries"]
        if st.get("retryDelay") is not None:
            node.config["retryDelay"] = st["retryDelay"]

        if effect == "write":
            self.budget.charge_write()
        if op in EXTERNAL_OPS:
            # `ask` 带 question 时是向人提问（不调 LLM），不占 LLM 额度。
            if not (op == OP_ASK and compiled["type"] == "ask"):
                self.budget.charge_llm()
        if op == OP_MEASURE and not self.dry_run:
            # 点数闸门：lqcs 里 `xxx=None` 都有默认展开量（`sq.t1` 的 zpa=None → 201 点），
            # 必须在**下发前**拦住，否则一次"测个 T1"就能把真机拖到超时卡死。
            est = self._estimate_points(compiled["config"])
            points = int((est or {}).get("points") or 0)
            if points:
                self.budget.charge_scan_points(points, name, (est or {}).get("note") or "")
            # P3 闸门（§13.4）：额度与授权都必须在**硬件下发前**判定，
            # 超额 → QuotaExceeded、越界 → NeedsHuman，两种情况一次命令都不会发出去。
            # 额度按**实验节点**扣（不是按 run 扣）：一次 goal 循环可能下发 3 次实验。
            #
            # D1-②：额度是"预扣 → 判定 → 不通过就退"的三段式。
            # 顺序故意是「扣额度 → 判授权 → 查数据集额度」：
            #   · 授权不通过 → 预扣的额度与数据集占位**全额退回**，run 一次硬件都没下发；
            #   · 数据集额度不够 → 退掉刚扣的实验额度。
            # 反过来（先判授权）会让"授权通过但额度不够"把票据额度白白烧掉。
            self._gate_charge_experiment(name)
            dataset_reserved = False
            try:
                self._gate_check_dataset_quota(name)
                dataset_reserved = True
                self._gate_authorize(compiled["config"], name)
            except QuotaExceeded:
                self._gate_refund(name, "experiments", 1, "quota_exceeded")
                if dataset_reserved:
                    self._gate_refund(name, "datasets", 1, "quota_exceeded")
                raise
            except NeedsHuman:
                self._gate_refund(name, "experiments", 1, "needs_human")
                if dataset_reserved:
                    self._gate_refund(name, "datasets", 1, "needs_human")
                raise

        # P3.5 副作用自愈：下发前拍 registry 快照，跑完再拍一张做 diff。
        # 只在真机路径（非干跑）且策略开启时做；快照失败不影响实验本身。
        side_effect_before = None
        side_effect_qubit = ""
        if op == OP_MEASURE and not self.dry_run:
            watcher = self._side_effect_watcher_get()
            if watcher is not None and watcher.policy.get("enabled", True):
                side_effect_qubit = str(compiled["config"].get("qubit") or "")
                if side_effect_qubit:
                    side_effect_before = watcher.snapshot(side_effect_qubit)

        if self.dry_run:
            raw = self.dry.stub(st, name) if self.dry else {"status": "completed", "value": 0}
            node.started_at = t0
            node.result = raw
        else:
            raw = self.svc._execute_node(self.workflow, name, self.legacy)

        if op == OP_MEASURE and not self.dry_run and side_effect_before is not None:
            watcher = self._side_effect_watcher_get()
            try:
                report = watcher.after(side_effect_qubit, side_effect_before, name)
            except Exception as exc:  # 副作用检查永远不该让流程失败
                report = {"node": name, "qubit": side_effect_qubit,
                          "changes": [], "restored": [], "skipped": [],
                          "notes": [f"副作用检查异常: {exc}"]}
            self.side_effect_reports.append(report)
            if report.get("changes"):
                self.gate_notes.append({
                    "gate": "autorevert", "node": name,
                    "mode": report.get("mode"),
                    "changes": len(report.get("changes") or []),
                    "restored": len(report.get("restored") or []),
                    "skipped": len(report.get("skipped") or []),
                })


        wrapped = wrap_result(raw, t0, time.time())
        if self.dry_run:
            # 干跑不走 svc._execute_node，节点状态得自己按判定结果给
            node.status = self._NS.COMPLETED if wrapped["ok"] else self._NS.FAILED
            node.completed_at = time.time()
            node.error = wrapped.get("error")
        self.legacy[name] = raw if isinstance(raw, dict) else {"value": raw}
        self.bindings[name] = wrapped
        self.budget.charge_dataset(wrapped.get("dataset"))
        # 数据集号回填到 task 账本（下发前只占了位，真实编号在这里补上）
        if op == OP_MEASURE and not self.dry_run:
            self._gate_record_dataset(name, wrapped.get("dataset"))
        self.audit.append({
            "op": op, "as": name, "status": wrapped["status"],
            "ok": wrapped["ok"], "dataset": wrapped.get("dataset"),
            "durationMs": int((wrapped["finishedAt"] - wrapped["startedAt"]) * 1000),
            "error": wrapped.get("error"),
            "dryRun": self.dry_run,
        })

        if not wrapped["ok"]:
            policy = st.get("onError") or (self.ir.get("defaults") or {}).get("onError") or "halt"
            if policy == "halt":
                raise Halt(f"{name}: {wrapped.get('error') or wrapped['status']}")
            self.warnings.append(f"{name} 失败但 onError={policy}，继续执行")

        # `ask` 节点返回 needs_human：流程停在这里等人拍板（不是失败）。
        if wrapped.get("needs_human"):
            raise NeedsHuman(
                wrapped.get("question") or wrapped.get("message") or f"{name} 需要人工介入",
                question=wrapped.get("question") or "",
                options=wrapped.get("options") or [],
            )

    def _side_effect_watcher_get(self) -> Optional[SideEffectWatcher]:
        """P3.5：按需构造副作用观察者（量子服务地址 + 队列地址 + 本次 task）。"""
        if self._side_effect_watcher is not None:
            return self._side_effect_watcher
        try:
            self._side_effect_watcher = SideEffectWatcher(
                quantum_base=str(getattr(self.svc, "_quantum_service_url", "") or ""),
                queue_base=str(getattr(self.svc, "_queue_service_url", "") or ""),
                task_id=self.gate_task or "",
                logger=lambda m: self.gate_notes.append({"gate": "autorevert", "note": m}),
            )
        except Exception:
            return None
        return self._side_effect_watcher

    def _ensure_node(self, name: str, node_type: str, config: Dict[str, Any],
                     depends: List[str]):
        Workflow, WorkflowNode, NodeStatus = self._classes()
        node = self.workflow.nodes.get(name)
        if node is None:
            node = WorkflowNode(id=name, type=node_type, config=config, depends=list(depends))
            self.workflow.nodes[name] = node
        else:
            node.type = node_type
            node.config = config
            node.depends = list(depends)
            node.status = NodeStatus.PENDING
            node.error = None
        node._workflow = self.workflow
        return node

    def _mark(self, name: str, status: str, note: str = "") -> None:
        node = self.workflow.nodes.get(name) if self.workflow else None
        if node is not None:
            node.status = self._NS.SKIPPED if status == "skipped" else self._NS.FAILED
            node.error = note or None
        self.audit.append({"op": "?", "as": name, "status": status, "note": note})

    # ── 引用解析 ──────────────────────────────────────────────────────────────
    def _resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            direct = parse_ref(value)
            if direct is not None:
                return self._lookup_ref(*direct)
            if "${" in value:
                def _repl(match):
                    inner = match.group(1).strip()
                    expr = inner if inner.startswith("$") else "$" + inner
                    return _to_text(self._resolve(expr))

                return INTERP_RE.sub(_repl, value)
            if "{{" in value:
                self.warnings.append(f"legacy interpolation: {value[:60]!r}")
            return value
        if isinstance(value, dict):
            if set(value) <= {"value", "unit"} and "value" in value:
                return self._resolve(value["value"])
            return {k: self._resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def _lookup_ref(self, name: str, path: List[str]) -> Any:
        if name == "env":
            if not path:
                return dict(self.env)
            return self._walk_env(path)
        if name == "profile":
            if not path:
                return dict(self.profile)
            return self._walk_profile(path)
        source = self.bindings.get(name)
        if source is None:
            source = self.legacy.get(name)
        if source is None:
            if name in self.env:
                source = {"value": self.env[name]}
                self.bindings.setdefault(name, {
                    "ok": True, "status": "completed", "value": self.env[name],
                    "metrics": {}, "dataset": None, "artifacts": {}, "stdout": "",
                    "error": None,
                })
            else:
                raise UndefinedRef(f"${name} 未绑定")
        if not path:
            # 裸引用 `$x`：如果 x 是执行结果包装（let 的 value 或某条语句的契约），
            # 取它的 value —— 否则 `"hello ${n}"` 会把整份契约字典插进字符串。
            # 同理，env 绑定的 `{"value": v}` 也要拆开，不然会打印成 `{'value': 'q2ld3'}`。
            if isinstance(source, dict) and "value" in source:
                if "ok" in source or set(source) <= {"value", "unit"}:
                    return source.get("value")
            return source
        found, val = _walk(source, path)
        if not found:
            # 允许省略 metrics/：`$c1.contrast` 等价 `$c1.metrics.contrast`
            for prefix in ("metrics", "artifacts", "variables"):
                found, val = _walk(source, [prefix] + path)
                if found:
                    break
        if not found:
            raise UndefinedRef(f"${name}.{'.'.join(path)} 不存在")
        return val

    def _walk_env(self, path: List[str]) -> Any:
        cur: Any = self.env
        for part in path:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise UndefinedRef(f"$env.{'.'.join(path)} 未声明")
        return cur

    def _walk_profile(self, path: List[str]) -> Any:
        if not self.profile:
            qubit = self.env.get("qubit")
            raise UndefinedRef(
                f"$profile.{'.'.join(path)} 不可用：设备画像里没有 {qubit!r} 的条目"
                if qubit else "$profile 不可用：流程没有 env.qubit，无法确定用哪颗比特的画像")
        cur: Any = self.profile
        for part in path:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                avail = ", ".join(sorted(self.profile))
                raise UndefinedRef(
                    f"$profile.{'.'.join(path)} 不在设备画像里（可用：{avail}）")
        return cur

    # ── 条件 ──────────────────────────────────────────────────────────────────
    def _when_ok(self, when: Any) -> bool:
        if when is None:
            return True
        if isinstance(when, bool):
            return when
        if isinstance(when, str):
            text = when.strip()
            if text.startswith("!"):
                return not self._truthy(self._resolve(text[1:].strip()))
            return self._truthy(self._resolve(text))
        if isinstance(when, dict):
            if "allOf" in when:
                return all(self._when_ok(w) for w in when["allOf"])
            if "anyOf" in when:
                return any(self._when_ok(w) for w in when["anyOf"])
            return self._compare(when)
        return bool(when)

    def _truthy(self, value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("ok") is False:
                return False
            if "value" in value:
                return bool(value["value"])
            if "passed" in value:
                return bool(value["passed"])
            if "status" in value:
                return value["status"] == "completed"
            return bool(value)
        return bool(value)

    def _compare(self, spec: Dict[str, Any]) -> bool:
        """比较判据。数值走 OPS；非数值（decide 的选择结果等）走字符串比较。

        支持三种写法：
          {"metric": "$r1.contrast", "operator": ">=", "threshold": 0.6}
          {"metric": "$d1.choice", "operator": "==", "value": "increase_power"}
          {"left": "$d1.choice", "operator": "in", "value": ["a", "b"]}
        """
        left_raw = self._resolve(spec.get("metric", spec.get("left")))
        right_raw = self._resolve(spec.get("threshold", spec.get("value", spec.get("right"))))
        operator = str(spec.get("operator") or spec.get("op") or ">=")
        left = _to_number(left_raw)
        right = _to_number(right_raw)
        if left is not None and right is not None:
            fn = OPS.get(operator, OPS[">="])
            return bool(fn(left, right))

        # ── 非数值：字符串/集合比较（P2：让 decide 的选择能驱动分支） ──────────
        ltext = _as_text(left_raw)
        rtext = _as_text(right_raw)
        if operator in ("==", "eq", "equals"):
            return ltext == rtext
        if operator in ("!=", "ne", "not_equals"):
            return ltext != rtext
        if operator in ("in", "one_of"):
            opts = right_raw if isinstance(right_raw, (list, tuple, set)) else [right_raw]
            return ltext in {_as_text(o) for o in opts}
        if operator in ("not in", "not_in"):
            opts = right_raw if isinstance(right_raw, (list, tuple, set)) else [right_raw]
            return ltext not in {_as_text(o) for o in opts}
        if operator == "contains":
            return rtext in ltext
        if operator == "startswith":
            return ltext.startswith(rtext)
        raise Halt(
            f"比较失败：{spec.get('metric')!r} 或 {spec.get('value')!r} 不是数值"
            f"（left={left_raw!r}, right={right_raw!r}）"
        )

    # ── 工具 ──────────────────────────────────────────────────────────────────
    def _collect_names(self, stmts: List[Dict[str, Any]]) -> List[str]:
        out: List[str] = []
        for st in stmts:
            name = self.node_names.get(id(st))
            if name and st.get("op") != OP_LET:
                out.append(name)
            for key in OP_BODY_KEY.get(st.get("op"), ()):
                out.extend(self._collect_names(st.get(key) or []))
        return out


def _walk(obj: Any, path: List[str]):
    cur = obj
    for part in path:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _to_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _slim(result: Dict[str, Any]) -> Dict[str, Any]:
    """报告里的绑定视图：够用即可，但 §3.4 约定的门控/文本字段必须保留。"""
    if not isinstance(result, dict):
        return {"value": result}
    out = {
        "ok": result.get("ok"),
        "status": result.get("status"),
        "value": result.get("value"),
        "metrics": result.get("metrics"),
        "dataset": result.get("dataset"),
        "error": result.get("error"),
    }
    for key in ("passed", "threshold", "operator", "metric",
                "message", "artifacts", "stdout", "qubit", "job_id"):
        if result.get(key) is not None:
            out[key] = result[key]
    return out


def run_ir(service, ir: Dict[str, Any], context: Optional[Dict[str, Any]] = None,
           dry_run: bool = False, dry_provider: Callable = None,
           workflow_id: str = None) -> Dict[str, Any]:
    """执行一份**已校验**的 IR。返回 §3.4 风格的报告。"""
    executor = IrExecutor(service, ir, context=context, dry_run=dry_run,
                          dry_provider=dry_provider, workflow_id=workflow_id)
    return executor.run()
