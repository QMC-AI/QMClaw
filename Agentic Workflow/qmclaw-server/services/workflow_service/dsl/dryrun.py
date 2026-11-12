"""dsl.dryrun - 干跑：零机时验证控制流。

两种模式：
- `synthetic`：所有 `measure` 返回合成结果（`FN_METRICS` 表），数据集编号为负。
  用来验证"语法/绑定/分支是否走得通"。
- `replay`：从历史 run 的 context 里回放真实的 dataset / metrics。
  **只有这种模式能验证 `stopWhen` 的命中点**，因为需要真实的对比度序列。

一条原则：干跑产出的 `dataset` 一律为负，审计里标 `dryRun: true`，
避免任何"干跑结果被当成真数据"的误读。
"""

import itertools
from typing import Any, Callable, Dict, Optional

from .const import (
    FN_METRICS,
    OP_ANALYZE,
    OP_APPLY,
    OP_ASSERT,
    OP_ASK,
    OP_DECIDE,
    OP_MEASURE,
    OP_READ,
    OP_SAY,
    OPS,
)

# 干跑时的典型耗时估计（秒），仅用于"预计墙钟"
ESTIMATED_SECONDS = {
    OP_MEASURE: 30.0,
    OP_READ: 5.0,
    OP_ANALYZE: 10.0,
    OP_SAY: 0.0,
    OP_APPLY: 0.0,
    OP_ASSERT: 0.0,
    OP_DECIDE: 3.0,
    OP_ASK: 0.0,
}


class DryRunProvider:
    def __init__(self, mode: str = "synthetic", resolve: Callable = None,
                 replay: Optional[Dict[str, Any]] = None,
                 contrast_start: float = 0.56, contrast_step: float = 0.07):
        self.mode = mode
        self.resolve = resolve or (lambda v: v)
        self.replay = replay or {}
        self._datasets = itertools.count(-1, -1)      # -1, -2, ...
        self._contrast = _Ramp(contrast_start, contrast_step)
        self.estimated_seconds = 0.0
        self.notes = []
        self._decide_calls = 0

    # ── 主入口 ──
    def stub(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        op = stmt.get("op")
        self.estimated_seconds += ESTIMATED_SECONDS.get(op, 0.0)
        if name in self.replay and isinstance(self.replay[name], dict):
            return dict(self.replay[name])
        handler = {
            OP_MEASURE: self._measure,
            OP_READ: self._read,
            OP_ANALYZE: self._analyze,
            OP_ASSERT: self._assert,
            OP_SAY: self._say,
            OP_APPLY: self._apply,
            OP_DECIDE: self._decide,
            OP_ASK: self._ask,
        }.get(op)
        if handler is None:
            return {"status": "completed", "dryRun": True}
        return handler(stmt, name)

    # ── decide / ask：干跑也要走通"选择"与"问人"两条语义 ───────────────
    def _decide(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        """干跑的 decide：按 choices 列表轮转产出 choice，让下游 if 分支可测。"""
        choices = stmt.get("choices") or []
        if isinstance(choices, str):
            choices = [choices]
        choices = [str(c) for c in choices]
        if not choices:
            return {"status": "completed", "dryRun": True}
        idx = self._decide_calls % len(choices)
        self._decide_calls += 1
        choice = choices[idx]
        return {
            "status": "completed",
            "choice": choice,
            "choices": choices,
            "stdout": f"[DRY-RUN] decide -> choice={choice}",
            "dryRun": True,
        }

    def _ask(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        """干跑的 ask：问人这一步在干跑里不真的等人，但语义标记保持一致。"""
        question = str(stmt.get("question") or stmt.get("prompt") or "")
        options = stmt.get("options") or []
        if isinstance(options, str):
            options = [options]
        return {
            "status": "completed",
            "question": question,
            "options": [str(o) for o in options],
            "stdout": f"[DRY-RUN] ask: {question}",
            "dryRun": True,
        }

    # ── 各 op ──
    def _measure(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        dataset = next(self._datasets)
        fn = str(stmt.get("fn") or "")
        metrics = dict(FN_METRICS.get(fn, {}))
        return {
            "status": "completed",
            "dataset_id": dataset,
            "metrics": metrics,
            "stdout": f"[DRY-RUN] {fn} -> dataset {dataset}",
            "dryRun": True,
        }

    def _read(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        dataset = self._safe_resolve(stmt.get("dataset"))
        contrast = self._contrast.next()
        return {
            "status": "completed",
            "dataset_id": dataset if isinstance(dataset, int) else -1,
            "contrast": contrast,
            "metrics": {"contrast": contrast, "snr": 3.0, "shots": 5000},
            "stdout": f"[DRY-RUN] contrast(dataset={dataset}) = {contrast:.4f}",
            "dryRun": True,
        }

    def _analyze(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        dataset = self._safe_resolve(stmt.get("dataset"))
        # 干跑的拟合结果是编的：给一组"典型成功值"，让下游 assert / say 能继续，
        # 也让 15 步这类长流程能整链跑通（真机值当然以 analysis_service 为准）。
        metrics = {
            "T1_us": 62.0, "T2_us": 31.0, "T2star_us": 18.0,
            "R_squared": 0.97, "f10_ghz": 4.85, "f01_ghz": 4.85,
            "fidelity": 0.995, "pi_amp": 0.42, "alpha": -0.31,
        }
        return {
            "status": "completed",
            "dataset_id": dataset if isinstance(dataset, int) else -1,
            "metrics": metrics,
            "stdout": "[DRY-RUN] qter.fitData -> T1 = 62.00 us",
            "dryRun": True,
        }

    def _assert(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        metric = stmt.get("metric")
        left = self._num(self._safe_resolve(metric if _is_ref(metric) else stmt.get("value")))
        right = self._num(self._safe_resolve(stmt.get("threshold", stmt.get("value"))))
        operator = str(stmt.get("operator", ">="))
        if left is None or right is None:
            return {"status": "failed",
                    "error": f"dry-run 比较失败：{metric!r} 与 {right!r} 不是数值",
                    "dryRun": True}
        passed = bool(OPS.get(operator, OPS[">="])(left, right))
        out = {
            "status": "completed",
            "passed": passed,
            "value": left,
            "threshold": right,
            "operator": operator,
            "metric": metric,
            "metrics": {"value": left, "threshold": right, "passed": passed},
            "stdout": f"[DRY-RUN] gate {left} {operator} {right} -> {'PASS' if passed else 'FAIL'}",
            "dryRun": True,
        }
        if not passed and stmt.get("onFail", "halt") != "continue":
            out["status"] = "failed"
            out["error"] = f"quality gate failed: {left} {operator} {right} (dry-run)"
        return out

    def _say(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        text = self._safe_resolve(stmt.get("template", stmt.get("message", "")))
        return {"status": "completed", "stdout": str(text), "message": str(text), "dryRun": True}

    def _apply(self, stmt: Dict[str, Any], name: str) -> Dict[str, Any]:
        params = self._safe_resolve(stmt.get("params") or {})
        return {"status": "completed", "params": params, "dryRun": True}

    # ── 工具 ──
    def _safe_resolve(self, value: Any) -> Any:
        try:
            return self.resolve(value)
        except Exception:
            return value

    @staticmethod
    def _num(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None


class _Ramp:
    """对比度给一个递增序列，让 `stopWhen` 在干跑里也能命中（而不是一轮就停）。"""

    def __init__(self, start: float, step: float):
        self.value = start
        self.step = step

    def next(self) -> float:
        self.value = min(0.95, self.value + self.step)
        return round(self.value, 4)


def _is_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("$")
