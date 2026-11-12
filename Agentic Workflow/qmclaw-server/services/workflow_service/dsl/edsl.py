"""dsl.edsl - Python 内嵌 DSL（路线 A）。

```python
from dsl import edsl

wf = edsl.workflow("contrast_tuning", name="态区分迭代调优")
wf.env(qubit="q2ld3", shots=5000)
wf.budget(maxWrites=30, maxWallSeconds=7200)

wf.let("shots", 5000)
wf.measure("sq.iqraw", as_="e1", qubit="$env.qubit")
wf.read("$e1.dataset", as_="c1", threshold=0.2)

with wf.repeat(max_rounds=5, export=["c2"],
               stop_when={"metric": "$c2.contrast", "op": ">=", "value": 0.7}):
    wf.measure("sq.piamp", as_="e2", qubit="$env.qubit")
    wf.read("$e2.dataset", as_="c2")

wf.assert_("$c2.contrast", ">=", 0.7, as_="g1")
with wf.if_("$g1.passed"):
    wf.say("contrast ok: ${$c2.contrast}")
with wf.else_():
    wf.say("still low, manual check")

ir = wf.to_ir()
```

它**只构造 IR 字典**：不执行、不联网、不需要服务在线。之所以用上下文管理器
表达 `repeat`/`if`，是因为缩进即作用域，和 `.qwf` 的语法一一对应——
两个前端读起来是同一段程序，这是"三前端一 IR"能被验证的前提。
"""

from typing import Any, Dict, List, Optional

from .const import IR_VERSION

__all__ = ["workflow", "WorkflowBuilder", "arange"]


def arange(start: float, stop: float, step: Optional[float] = None) -> Dict[str, Any]:
    """扫描区间：`arange(6.5, 7.1, 0.001)` → `{"$arange": [6.5, 7.1, 0.001]}`。

    对应 `.qwf` 的 `args { freq range 6.5 7.1 0.001 }`，最终渲染成
    `np.arange(6.5, 7.1, 0.001)`。官方标定流程几乎每步都要传扫描区间，
    而 IR 是 JSON、没有数组构造器，所以用这一个约定把 arange 表达出来。
    """
    values: List[float] = [start, stop] if step is None else [start, stop, step]
    return {"$arange": values}


def workflow(workflow_id: str, name: Optional[str] = None, **meta: Any) -> "WorkflowBuilder":
    """建一个工作流。`env=` / `budget=` / `policy=` / `context=` / `defaults=` 直接进 IR 顶层，
    其余关键字进 `meta`（与文本前端的 `meta {}` 段落一致）。"""
    builder = WorkflowBuilder(workflow_id, name, meta)
    for key in ("env", "budget", "policy", "context", "defaults"):
        if key in builder.ir["meta"]:
            section = builder.ir["meta"].pop(key)
            if isinstance(section, dict):
                builder.ir.setdefault(key, {}).update(section)
    return builder


class WorkflowBuilder:
    def __init__(self, workflow_id: str, name: Optional[str] = None, meta: Optional[dict] = None):
        self.ir: Dict[str, Any] = {
            "ir_version": IR_VERSION,
            "id": workflow_id,
            "meta": dict(meta or {}),
            "env": {},
            "budget": {},
            "pipeline": [],
        }
        if name:
            self.ir["meta"]["name"] = name
        self._stack: List[List[Dict[str, Any]]] = [self.ir["pipeline"]]

    # ── 顶层配置 ──
    def env(self, **kwargs: Any) -> "WorkflowBuilder":
        self.ir["env"].update(kwargs)
        return self

    def budget(self, **kwargs: Any) -> "WorkflowBuilder":
        self.ir["budget"].update({k: v for k, v in kwargs.items() if v is not None})
        return self

    def policy(self, **kwargs: Any) -> "WorkflowBuilder":
        """流程策略。目前只有 `readOnly=True`：声明这份流程不该改机器参数，
        编译器会在下发前把会自动写 registry 的实验拦下来（见 docs/DSL_DESIGN.md）。"""
        self.ir.setdefault("policy", {}).update(kwargs)
        return self

    def defaults(self, **kwargs: Any) -> "WorkflowBuilder":
        self.ir.setdefault("defaults", {}).update(kwargs)
        return self

    def context(self, **kwargs: Any) -> "WorkflowBuilder":
        self.ir.setdefault("context", {}).update(kwargs)
        return self

    # ── 语句 ──
    def let(self, name: str, value: Any) -> "WorkflowBuilder":
        return self._add({"op": "let", "as": name, "value": value})

    def measure(self, fn: str, as_: Optional[str] = None, qubit: str = "$env.qubit",
                params: Optional[Dict[str, Any]] = None, **extra: Any) -> "WorkflowBuilder":
        stmt: Dict[str, Any] = {"op": "measure", "fn": fn, "qubit": qubit}
        if params:
            stmt["params"] = dict(params)
        return self._add(_named(stmt, as_, extra))

    def read(self, dataset: Any, as_: Optional[str] = None, kind: str = "IQraw",
             qubit: Optional[str] = None, threshold: Optional[float] = None,
             **extra: Any) -> "WorkflowBuilder":
        stmt: Dict[str, Any] = {"op": "read", "kind": kind, "dataset": dataset}
        if qubit:
            stmt["qubit"] = qubit
        if threshold is not None:
            stmt["threshold"] = threshold
        return self._add(_named(stmt, as_, extra))

    def analyze(self, dataset: Any, as_: Optional[str] = None,
                command: str = "qter.fitData(do_plot=False)", **extra: Any) -> "WorkflowBuilder":
        stmt = {"op": "analyze", "dataset": dataset, "command": command}
        return self._add(_named(stmt, as_, extra))

    def decide(self, intent: str, as_: Optional[str] = None, mode: str = "analysis",
               system: Optional[str] = None, temperature: Optional[float] = None,
               max_tokens: Optional[int] = None, **extra: Any) -> "WorkflowBuilder":
        stmt: Dict[str, Any] = {"op": "decide", "mode": mode, "intent": intent}
        if system:
            stmt["systemPrompt"] = system
        if temperature is not None:
            stmt["temperature"] = temperature
        if max_tokens is not None:
            stmt["maxTokens"] = max_tokens
        return self._add(_named(stmt, as_, extra))

    def ask(self, prompt: Any, as_: Optional[str] = None, model: Optional[str] = None,
            temperature: Optional[float] = None, **extra: Any) -> "WorkflowBuilder":
        messages = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
        stmt: Dict[str, Any] = {"op": "ask", "messages": messages}
        if model:
            stmt["model"] = model
        if temperature is not None:
            stmt["temperature"] = temperature
        return self._add(_named(stmt, as_, extra))

    def classify(self, image: Any, as_: Optional[str] = None,
                 threshold: Optional[float] = None, **extra: Any) -> "WorkflowBuilder":
        stmt: Dict[str, Any] = {"op": "classify", "image": image}
        if threshold is not None:
            stmt["threshold"] = threshold
        return self._add(_named(stmt, as_, extra))

    def vlm(self, image: Any, as_: Optional[str] = None, family: Optional[str] = None,
            **extra: Any) -> "WorkflowBuilder":
        stmt: Dict[str, Any] = {"op": "vlm", "image": image}
        if family:
            stmt["family"] = family
        return self._add(_named(stmt, as_, extra))

    def assert_(self, metric: Any, operator: str = ">=", threshold: Any = None,
                as_: Optional[str] = None, on_fail: str = "halt", **extra: Any) -> "WorkflowBuilder":
        stmt: Dict[str, Any] = {"op": "assert", "metric": metric, "operator": operator}
        if threshold is not None:
            stmt["threshold"] = threshold
        if on_fail != "halt":
            stmt["onFail"] = on_fail
        return self._add(_named(stmt, as_, extra))

    def say(self, template: Any, as_: Optional[str] = None, **extra: Any) -> "WorkflowBuilder":
        return self._add(_named({"op": "say", "template": template}, as_, extra))

    def apply(self, params: Dict[str, Any], as_: Optional[str] = None,
              **extra: Any) -> "WorkflowBuilder":
        return self._add(_named({"op": "apply", "params": dict(params)}, as_, extra))

    # ── 控制流 ──
    def repeat(self, max_rounds: int = 1, as_: Optional[str] = None,
               stop_when: Any = None, export: Optional[List[str]] = None,
               **extra: Any) -> "_Block":
        stmt: Dict[str, Any] = {"op": "repeat", "maxRounds": int(max_rounds), "body": []}
        if stop_when is not None:
            stmt["stopWhen"] = stop_when
        if export:
            stmt["export"] = list(export)
        self._add(_named(stmt, as_, extra))
        return _Block(self, stmt["body"])

    def if_(self, cond: Any, as_: Optional[str] = None, **extra: Any) -> "_Block":
        stmt: Dict[str, Any] = {"op": "if", "cond": cond, "then": [], "else": []}
        self._add(_named(stmt, as_, extra))
        self._last_if = stmt
        return _Block(self, stmt["then"])

    def else_(self) -> "_Block":
        stmt = getattr(self, "_last_if", None)
        if stmt is None:
            raise RuntimeError("else_() 必须紧跟在 if_() 的 with 块之后")
        self._last_if = None
        return _Block(self, stmt["else"])

    # ── 输出 ──
    def to_ir(self) -> Dict[str, Any]:
        return self.ir

    # ── 内部 ──
    def _add(self, stmt: Dict[str, Any]) -> "WorkflowBuilder":
        self._stack[-1].append(stmt)
        return self


class _Block:
    """with 块：进入时把新列表压栈，退出时弹栈。"""

    def __init__(self, builder: WorkflowBuilder, target: List[Dict[str, Any]]):
        self.builder = builder
        self.target = target

    def __enter__(self) -> WorkflowBuilder:
        self.builder._stack.append(self.target)
        return self.builder

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.builder._stack.pop()
        return False


def _named(stmt: Dict[str, Any], as_: Optional[str], extra: Dict[str, Any]) -> Dict[str, Any]:
    if as_:
        stmt["as"] = as_
    for key, value in extra.items():
        if value is not None:
            stmt[key] = value
    return stmt
