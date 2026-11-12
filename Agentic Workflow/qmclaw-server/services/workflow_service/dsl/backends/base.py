"""后端适配器契约（IR v2 预留，见 `docs/BACKEND_ADAPTERS.md`）。

这是"把测控软件挡在 IR 外面"的那道墙。当前**只有 lqcs 一个实现**，其余后端只留接口；
真正的执行路径仍在 `server.py` 的 experiment/contrast/analyze 分支里，等 S7 再整体搬进来。

契约的四条硬约定：

1. IR 里不出现厂商符号；厂商名、原生 id 只准出现在 `RawResult.meta`。
2. 能力必须**声明**（`capabilities()`）而不是靠调用失败来发现。
3. 结果用"数量 + 单位"（`quantities`），键名取自 `dsl.quantities.QUANTITIES`。
4. 取消通过 `CancelToken` 协作，不在 `run()` 里自己杀线程。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class AxisSpec:
    """一根扫描轴。两种写法互斥：

    * 绝对：`start` / `stop` / `points`（可选 `step`）
    * 相对：`center` / `span` / `points`（center 可以是参数引用，如 `$p.f10`）
    """

    start: Optional[float] = None
    stop: Optional[float] = None
    center: Optional[float] = None
    span: Optional[float] = None
    points: int = 0
    step: Optional[float] = None
    unit: str = ""

    def is_relative(self) -> bool:
        return self.center is not None or self.span is not None

    def to_dict(self) -> Dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v not in (None, "") and v != 0}
        out["points"] = self.points
        return out


@dataclass
class RunRequest:
    """一次实验请求（语义层，不含任何厂商关键字）。"""

    experiment: str                      # 受控词表，如 "t1" / "iqraw"
    device: str = ""                     # 设备 id，如 "LQHL"
    qubit: str = ""                      # 设备内的比特，如 "q2ld3"
    sweep: Dict[str, AxisSpec] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)   # 规范参数名 → 数量/字面量
    shots: Optional[int] = None
    options: Dict[str, Any] = field(default_factory=dict)  # 适配器私有开关
    effect: str = "write"                # read | write
    timeout: Optional[float] = None


@dataclass
class RawResult:
    """适配器返回值。核心层会把它归一到 IR §3.4 的结果契约。"""

    ok: bool = True
    status: str = "completed"
    data_ref: Any = None                 # 数据集引用（lqcs 的 dataset id / qiskit 的 result id）
    quantities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)     # {"vendor": ..., "native_id": ...}

    def to_contract(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "status": self.status,
            "dataset": self.data_ref, "quantities": self.quantities,
            "artifacts": self.artifacts, "stdout": self.stdout,
            "error": self.error, "meta": self.meta,
        }


class CancelToken(Protocol):
    def is_set(self) -> bool: ...


class CapabilityError(RuntimeError):
    """目标后端缺少 IR 要求的能力。"""


class BackendAdapter(ABC):
    """所有测控软件的接入点。**只有 run/fit/参数映射** 可以写厂商代码。"""

    name: str = "abstract"
    #: `run()` 是否真的可用。预留期一律 False，S7 接入 lqcs 后置 True。
    IMPLEMENTED = False

    # ── 必须实现 ──────────────────────────────────────────────────────────
    @abstractmethod
    def capabilities(self) -> Dict[str, Any]:
        """返回能力声明，形状见 `dsl.capabilities`。"""

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        """后端是否可达/可用；用于 `backend: auto` 时的探测。"""

    # ── 执行（S7 接入） ────────────────────────────────────────────────────
    def run(self, req: RunRequest, token: Optional[CancelToken] = None) -> RawResult:
        raise NotImplementedError(f"{self.name}.run() 尚未接入（S7）")

    def fit(self, kind: str, data_ref: Any) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError(f"{self.name}.fit() 尚未接入（S8）")

    # ── 设备与参数 ─────────────────────────────────────────────────────────
    def devices(self) -> List[Dict[str, Any]]:
        return []

    def read_param(self, path: str) -> Dict[str, Any]:
        raise NotImplementedError(f"{self.name}.read_param() 尚未接入（S8）")

    def write_param(self, path: str, quantity: Dict[str, Any]) -> None:
        raise NotImplementedError(f"{self.name}.write_param() 尚未接入（S8）")

    # ── 副作用与耗时预估（给编译期统计用）──────────────────────────────────
    #
    # 这两件事都是**厂商知识**，只能由适配器回答；执行器/校验器只消费结果。
    # 返回 None 表示"这个后端不知道"，上层按"未知、不拦"处理。
    def write_effects(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """这条实验会不会自己改参数。形状见 `LqcsAdapter.write_effects`。"""
        return None

    def estimate_runtime(self, fn: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """这条实验预计跑多久（秒）。形状见 `LqcsAdapter.estimate_runtime`。"""
        return None

    def write_key(self, name: str, path: Optional[List[str]] = None) -> Optional[str]:
        """真机 stdout 的一次写入 → 写入语义表里用的键名。

        后端知道自己的参数命名空间（哪个是容器名、路径停在哪一层），
        执行器只负责把 `__setitem__ <name> ... in path [...]` 交给它翻译。
        """
        return None

    def drift_threshold(self, key: str) -> Optional[Dict[str, Any]]:
        """这个键被挪动多少算"危险漂移"。

        返回 `{"abs": 阈值}`（或 `{"rel": 相对阈值}`）；None = 只记录不报警。
        阈值是厂商知识：哪个键挪一下就会把后面实验的基准带偏，只有后端知道。
        """
        return None

    # ── 辅助 ──────────────────────────────────────────────────────────────
    def describe(self) -> Dict[str, Any]:
        caps = self.capabilities() or {}
        return {
            "name": self.name,
            "capabilities": caps,
            "devices": self.devices(),
            "implemented": self.is_implemented(),
        }

    def is_implemented(self) -> bool:
        """`run()` 是否真的可用（预留期用 False 让上层能区分"声明了"和"能跑"）。"""
        return bool(getattr(type(self), "IMPLEMENTED", False))
