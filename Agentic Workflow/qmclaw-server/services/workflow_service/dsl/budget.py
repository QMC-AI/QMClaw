"""dsl.budget - 四类硬闸（S7）。

预算不是"事后统计"，而是**执行前的闸门**：每条 write 之前先扣一次额度，扣不动
就抛 BudgetExceeded，流程优雅中止并保留已跑语句的结果。这样"跑到一半才发现要
下发 200 次实验"不会发生。
"""

import os
import time
from typing import Any, Dict, Optional


class BudgetExceeded(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


class Budget:
    def __init__(self, spec: Optional[Dict[str, Any]] = None, mode: str = "real"):
        spec = spec or {}
        self.mode = mode
        self.max_wall = _num(spec.get("maxWallSeconds"))
        self.max_writes = _num(spec.get("maxWrites"))
        self.max_datasets = _num(spec.get("maxDatasets")) or self.max_writes
        self.max_llm = _num(spec.get("maxLlmCalls"))
        # 单条实验允许的最大扫描点数。
        # IR 里声明的 maxScanPoints 与机器级环境变量 QMCLAW_MAX_SCAN_POINTS 取更严的那个；
        # 两者都没给 = 不拦（但执行器仍会估算并在大点数时告警）。
        ir_cap = _num(spec.get("maxScanPoints"))
        env_cap = _num(os.environ.get("QMCLAW_MAX_SCAN_POINTS"))
        if ir_cap and env_cap:
            self.max_scan_points = min(ir_cap, env_cap)
        else:
            self.max_scan_points = ir_cap or env_cap
        self.scan_points = 0
        self.max_scan_points_seen = 0
        self.writes = 0
        self.datasets = []
        self.llm_calls = 0
        self.started = time.time()

    # ── 计时 ──
    def wall(self) -> float:
        return time.time() - self.started

    def check_wall(self) -> None:
        if self.max_wall and self.wall() > self.max_wall:
            raise BudgetExceeded(
                "maxWallSeconds",
                f"超过预算 maxWallSeconds={self.max_wall}（已用 {self.wall():.0f}s）",
            )

    # ── 扣额度 ──
    def charge_write(self) -> None:
        if self.max_writes and self.writes >= self.max_writes:
            raise BudgetExceeded(
                "maxWrites",
                f"超过预算 maxWrites={self.max_writes}（已下发 {self.writes} 次写操作）",
            )
        self.writes += 1

    def charge_dataset(self, dataset: Any) -> None:
        if not isinstance(dataset, int) or dataset <= 0:
            return
        if dataset not in self.datasets:
            self.datasets.append(dataset)
        if self.max_datasets and len(self.datasets) > self.max_datasets:
            raise BudgetExceeded(
                "maxDatasets",
                f"超过预算 maxDatasets={self.max_datasets}（已写 {len(self.datasets)} 个数据集）",
            )

    def charge_llm(self, n: int = 1) -> None:
        if self.max_llm and self.llm_calls + n > self.max_llm:
            raise BudgetExceeded(
                "maxLlmCalls",
                f"超过预算 maxLlmCalls={self.max_llm}（已调用 {self.llm_calls} 次）",
            )
        self.llm_calls += n

    def charge_scan_points(self, points: int, label: str = "", detail: str = "") -> None:
        """单条实验的扫描点数闸门。

        为什么要单独一道：lqcs 里凡是 `xxx=None` 都有默认展开量（`sq.t1` 的
        `zpa=None` → 201 点），一不小心就从"几十点"变成"几千点"，真机直接被拖到
        超时卡死。点数在**下发前**估算，超限就不下发。
        """
        try:
            n = int(points)
        except (TypeError, ValueError):
            return
        if n <= 0:
            return
        if self.max_scan_points and n > self.max_scan_points:
            extra = f"（{detail}）" if detail else ""
            where = f"，语句 {label}" if label else ""
            raise BudgetExceeded(
                "maxScanPoints",
                f"超过预算 maxScanPoints={self.max_scan_points:g}{where}："
                f"预计扫描 {n} 点{extra}",
            )
        self.scan_points += n
        self.max_scan_points_seen = max(self.max_scan_points_seen, n)

    # ── 报告 ──
    def usage(self) -> Dict[str, Any]:
        return {
            "wallSeconds": round(self.wall(), 2),
            "writes": self.writes,
            "datasets": list(self.datasets),
            "llmCalls": self.llm_calls,
            "scanPoints": self.scan_points,
            "maxScanPointsSeen": self.max_scan_points_seen,
            "limits": {
                "maxWallSeconds": self.max_wall,
                "maxWrites": self.max_writes,
                "maxDatasets": self.max_datasets,
                "maxLlmCalls": self.max_llm,
                "maxScanPoints": self.max_scan_points,
            },
        }


def _num(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
