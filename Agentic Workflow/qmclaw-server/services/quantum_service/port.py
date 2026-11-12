"""services/quantum_service/port.py — Qubit Port 设备抽象层（纯委派，可离线单测）

背景（docs/ARCHITECTURE_ROADMAP.md §2.2）：
厂商实验函数（sq.*）语义隐式、副作用静默（auto-update 改 f10/fread）、能力随安装
版本漂移（.pyd 遮蔽）。本层把"对一个比特做什么"收敛成 5 个语义操作，并内建：

1. 参数校验与钳制（非法参数在触达硬件前就被拒）
2. 能力探测（不可用的实验函数直接拒绝，而不是跑到一半报 AttributeError）
3. 事务语义：前快照 → 执行 → 后快照 → diff（可回滚、可审计）

设计约束：
- **纯委派**：本模块不 import lqms、不直接碰 LabRAD，只调用 QuantumService 已有能力
  → 可以用假 service 做离线单测。
- 端口不绕过写闸门：所有写操作仍然经过 service 的 allow_execute / allow_set_params。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from services.common.capabilities import capability_report
from services.common.errors import error_envelope
from services.common.runrecord import diff_snapshots


DEFAULT_DF = 0.3          # pidf 扫频半宽（GHz）
DEFAULT_TIMEOUT = 300


class QubitPort:
    """单个比特的操作端口。

    Args:
        service: QuantumService 实例（或任何提供同名内部方法的替身）。
        program_runner: 可选回调 `fn(ir, **opts) -> dict`，用于把"编排"交给
            :3008/workflows/run_ir（单写者原则）。不注入时 program() 只做本地校验。
    """

    def __init__(
        self,
        service: Any,
        program_runner: Optional[Callable[..., Dict[str, Any]]] = None,
    ):
        self._svc = service
        self._program_runner = program_runner

    # ------------------------------------------------------------------ 读

    def read_state(self, qubit: str) -> Dict[str, Any]:
        """读取比特寄存器快照（只读）。"""
        if not qubit:
            return error_envelope("validation_error", "qubit 名称必填")
        return self._svc._handle_qubit_registry({"name": qubit})

    def capabilities(self) -> Dict[str, Any]:
        """能力探测：当前安装里哪些实验函数实际可用。"""
        return capability_report()

    # ------------------------------------------------------------------ 写

    def apply_pulse(
        self,
        qubit: str,
        params: Dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """写比特参数（受 allow_set_params 闸门约束）。

        params 形如 {"f10": 3.62, "PiGate.amp": 1.2}；键名必须是 registry 里的叶子键。
        """
        if not qubit:
            return error_envelope("validation_error", "qubit 名称必填")
        if not isinstance(params, dict) or not params:
            return error_envelope("validation_error", "params 不能为空")
        bad = [k for k in params if not isinstance(k, str) or not k.strip()]
        if bad:
            return error_envelope("validation_error", f"非法参数键: {bad}")
        if dry_run:
            return {"success": True, "dry_run": True, "name": qubit, "params": params}
        return self._svc._handle_qubit_set_params({"name": qubit, "params": params})

    # ------------------------------------------------------------------ 测

    def measure(
        self,
        code: str,
        *,
        task_id: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        qubit: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行一段实验代码（受 allow_execute 闸门与硬件通道约束）。

        事务语义：执行前后各取一次 registry 快照，返回 diff。
        """
        if not code or not str(code).strip():
            return error_envelope("validation_error", "code 不能为空")
        payload: Dict[str, Any] = {"code": code, "timeout": timeout}
        if task_id:
            payload["task_id"] = task_id

        before = None
        if qubit:
            snap = self.read_state(qubit)
            before = snap.get("registry") if isinstance(snap, dict) else None

        result = self._svc._handle_execute(payload)

        after = None
        if qubit:
            snap = self.read_state(qubit)
            after = snap.get("registry") if isinstance(snap, dict) else None

        if isinstance(result, dict):
            result = dict(result)
            if before is not None and after is not None:
                result["registry_diff"] = diff_snapshots(before, after)
        return result

    def probe_transition(
        self,
        qubit: str,
        *,
        df: float = DEFAULT_DF,
        task_id: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        method: str = "pidf",
    ) -> Dict[str, Any]:
        """探测比特跃迁频率（扫频）。

        默认用 `sq.pidf`（扫频找 P1 峰）。update=False → 不让厂商函数回写 f10，
        避免污染寄存器（fread/f10 auto-update 是本项目反复踩的坑）。

        返回：{code, method, df, result, registry_diff}
        """
        if not qubit:
            return error_envelope("validation_error", "qubit 名称必填")
        if method not in ("pidf", "spectroscopy", "iqcenter"):
            return error_envelope(
                "validation_error",
                f"method 仅支持 pidf / spectroscopy / iqcenter，收到 {method}",
            )

        unavailable = self._unavailable()
        if method in unavailable:
            return error_envelope(
                "not_found",
                f"{method} 在当前安装不可用（能力缺失: {method}）",
                capability_hint="重启服务无效：需实验室重新编译 lqms 扩展",
            )

        if method == "pidf":
            code = f"sq.pidf({qubit}, df={df}, do_plot=False, update=False)"
        elif method == "spectroscopy":
            code = (
                f"sq.spectroscopy({qubit}, freq=np.arange({qubit}.regs.f10-{df}, "
                f"{qubit}.regs.f10+{df}, 0.005), do_plot=False, update=False)"
            )
        else:
            code = f"sq.iqcenter({qubit}, do_plot=False)"

        result = self.measure(code, task_id=task_id, timeout=timeout, qubit=qubit)
        return {
            "success": not bool(isinstance(result, dict) and result.get("error")),
            "qubit": qubit,
            "method": method,
            "df": df,
            "code": code,
            "result": result,
        }

    # -------------------------------------------------------------- 编排

    def program(self, ir: Any, **opts: Any) -> Dict[str, Any]:
        """编排一段实验 IR（单写者入口）。

        需要注入 program_runner（指向 :3008/workflows/run_ir）；未注入时只做本地
        校验并返回明确错误，不自己偷偷执行。
        """
        if ir in (None, "", {}, []):
            return error_envelope("validation_error", "IR 不能为空")
        if self._program_runner is None:
            return error_envelope(
                "not_found",
                "未注入 program_runner：请通过 :3008/workflows/run_ir 走单写者入口",
                hint="端口层不直接执行 IR，避免绕过治理层",
            )
        return self._program_runner(ir, **opts)

    # -------------------------------------------------------------- 内部

    def _unavailable(self) -> List[str]:
        """当前安装里不可用的实验函数（来自能力探测）。"""
        try:
            report = capability_report()
        except Exception:
            return []
        missing: List[str] = []
        for info in (report.get("capabilities") or {}).values():
            missing.extend(info.get("missing_in_runtime", []))
        return sorted(set(missing))
