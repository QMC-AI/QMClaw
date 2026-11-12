"""services/common/replay.py — 回放层（Replay，纯标准库，可离线单测）

对应 docs/ARCHITECTURE_ROADMAP.md §2.6：

    录制 LabRAD 调用序列做 fixture + 设备仿真（auto-update、默认扫描展开、错误注入），
    让 CI 能覆盖真机链路。

现状痛点（本次升级的动机）：
- 现有 18 套自检全部离线，**一个都不碰设备**；
- 厂商函数会在你没要求的时候静默改寄存器（`spectroscopy` 回写 f10/f21、
  `s21` 回写 fread、`iqraw` 回写 discriminator 中心），这正是"比特假死"的根因之一；
- 厂商函数的**默认扫描展开**也会咬人：`sq.t1` 不传 zpa → zpa×时间二维扫描（极慢）；
  `sq.spectroscopy` 不传 freq → 退化成标量 → `IndexError: list index out of range`。

本模块提供三件事：
1. `VENDOR_SEMANTICS`：厂商语义表（每个 sq 函数的自动回写目标、必填参数、默认展开、
   典型错误）。这是"把厂商语义写进代码"的落点。
2. `simulate(code, registry)`：对一段实验代码做**离线仿真**——不连设备，按语义表
   推出"这段代码会把哪些寄存器改掉、会展开成什么扫描、会报什么错"。
3. `ReplayStore` / `record_fixture` / `replay_fixture`：录制 fixture、离线回放，
   并把"仿真结果"与"真机当时的结果"做对比，**厂商语义漂移**在 CI 里就能被发现。

设计约束（与 runrecord.py 一致）：
- 纯标准库，不 import 任何服务模块、不碰 LabRAD；
- 写盘失败不抛异常（回放不能打断真机执行）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 厂商语义表：sq.<fn> 的自动回写 / 默认展开 / 典型错误
#
# auto_writes : 厂商在 update=True（默认）时**静默**回写的寄存器键（扁平化后的小写键）
# requires    : 必须显式传入的参数；缺失 → 典型错误（复现真机上的失败）
# defaults    : 参数的默认展开语义（用于在仿真里给出"这段代码实际会扫多少点"）
# note        : 人读的说明
# ---------------------------------------------------------------------------
VENDOR_SEMANTICS: Dict[str, Dict[str, Any]] = {
    "spectroscopy": {
        "summary": "扫频找共振，auto-update 回写 f10/f21",
        "auto_writes": ["f10", "f21"],
        "requires": ["freq"],
        "defaults": {"freq": "缺省 → 退化为标量 → IndexError: list index out of range"},
        "note": "平坦数据上会回写一个错误的 f10（实测上午错 +35.7 MHz，pidf 后纠正 −35.55 MHz）",
    },
    "spectroscopy_auto": {
        "summary": "z 脉冲 AC-Stark 自动谱学",
        "auto_writes": ["f10", "f21"],
        "requires": [],
        "defaults": {"zpa_scan": "默认 -3..3 step 0.4", "freq_scan": "默认跟随 f10"},
        "note": "唯一可能看到'腔频随 zpa 弯曲'的实验",
    },
    "spectroscopy_iq": {
        "summary": "IQ 域谱学（源码有、运行时被 .pyd 遮蔽 → 不可用）",
        "auto_writes": ["f10", "f21", "fc"],
        "requires": [],
        "defaults": {},
        "note": "当前环境不可用：.pyd 遮蔽 .py，需实验室重新编译 lqms 扩展",
    },
    "s21": {
        "summary": "腔反射谱，auto-update 回写 fread",
        "auto_writes": ["fread"],
        "requires": [],
        "defaults": {"freq": "缺省 → fread 邻域自动扫描"},
        "note": "会把该比特自己的 fread 改成扫到的凹陷频率（曾把 q1/q3 的 fread 写成 6.7255 → 假死）",
    },
    "iqraw": {
        "summary": "裸 IQ 采集，auto-update 回写 discriminator 中心（有时连带 fread）",
        "auto_writes": ["discriminator.center0", "discriminator.center1", "fread"],
        "requires": [],
        "defaults": {"states": "默认四个分支 (Is|Qs|Is|Qs)"},
        "note": "弱对比度数据上会写出'看起来分得很开'的假中心 → 判读要看 contrast 而非中心间距",
    },
    "pidf": {
        "summary": "扫 df 找比特跃迁，auto-update 回写 f10",
        "auto_writes": ["f10"],
        "requires": [],
        "defaults": {"df": "默认 0.03（±30 MHz）"},
        "note": "df 太小会扫空（实测偏差 35.7 MHz 时 0.03 覆盖不到，需 ≥0.05）",
    },
    "piamp": {
        "summary": "标定 π 脉冲幅度，回写 PiGate.amp（不动 alpha）",
        "auto_writes": ["pigate.amp"],
        "requires": [],
        "defaults": {},
        "note": "只改幅度；若频率不对，幅度扫描不会改善对比度",
    },
    "set_pi": {
        "summary": "细调 π 脉冲，回写 PiGate.amp/alpha",
        "auto_writes": ["pigate.amp", "pigate.alpha"],
        "requires": [],
        "defaults": {"gate": "默认 'X'"},
        "note": "tbuffer=0 时不可用于标定",
    },
    "ramsey_df": {
        "summary": "Ramsey 测频偏，auto-update 回写 f10/f21",
        "auto_writes": ["f10", "f21"],
        "requires": [],
        "defaults": {"delay": "默认 Ramsey 延迟序列"},
        "note": "实测一次修正 +0.672 MHz",
    },
    "ramsey_t2": {
        "summary": "Ramsey 测 T2*（不写寄存器）",
        "auto_writes": [],
        "requires": [],
        "defaults": {},
        "note": "",
    },
    "spinecho_t2": {
        "summary": "CPMG 测 T2（不写寄存器）",
        "auto_writes": [],
        "requires": [],
        "defaults": {},
        "note": "",
    },
    "t1": {
        "summary": "测 T1（不写寄存器）",
        "auto_writes": [],
        "requires": ["zpa"],
        "defaults": {"zpa": "缺省 → zpa×时间二维扫描（极慢）；只测单个 zpa 必须显式传 zpa=<固定值>"},
        "note": "T1 拟合需要 zpa 维度，1D 数据 qter.fitData 会报 KeyError 'z pulse amplitude'",
    },
    "xeb": {
        "summary": "XEB 随机基准保真度（不写寄存器）",
        "auto_writes": [],
        "requires": [],
        "defaults": {"k": "默认 30", "stats": "默认 300"},
        "note": "单次约 110 s",
    },
}

# 会被仿真的函数别名（写错名字也要能发现）
KNOWN_SQ_FUNCTIONS = sorted(VENDOR_SEMANTICS.keys())

# 从代码里抠出 sq.<fn>(<qubit>, ...) 调用
_CALL_RE = re.compile(
    r"\bsq\s*\.\s*([A-Za-z_][A-Za-z_0-9]*)\s*\(\s*([A-Za-z_][A-Za-z_0-9]*)?\s*"
    r"(?:,\s*((?:[^()]|\([^()]*\))*?))?\s*\)",
    re.DOTALL,
)

_KWARG_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\s*=\s*([^,]+)")


def parse_calls(code: str) -> List[Dict[str, Any]]:
    """从实验代码里解析出 sq.<fn>(<qubit>, kwargs...) 调用序列（保持出现顺序）。"""
    calls: List[Dict[str, Any]] = []
    if not code:
        return calls
    for match in _CALL_RE.finditer(code):
        fn = match.group(1)
        qubit = match.group(2)
        argblob = match.group(3) or ""
        kwargs: Dict[str, str] = {}
        for kw in _KWARG_RE.finditer(argblob):
            kwargs[kw.group(1)] = kw.group(2).strip()
        calls.append({"fn": fn, "qubit": qubit, "kwargs": kwargs, "raw": match.group(0)})
    return calls


def _flatten(registry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把嵌套 registry 摊平成小写点号键（与 :3003/qubit/registry 的摊平口径一致）。"""
    flat: Dict[str, Any] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                walk(f"{prefix}.{key}" if prefix else str(key), val)
        elif isinstance(node, (list, tuple)):
            for idx, val in enumerate(node):
                walk(f"{prefix}.{idx}" if prefix else str(idx), val)
        else:
            flat[prefix.lower()] = node

    walk("", registry or {})
    return flat


def _unflatten(flat: Dict[str, Any]) -> Dict[str, Any]:
    """把摊平的键还原成嵌套结构（回放 fixture 用）。"""
    out: Dict[str, Any] = {}
    for key, val in flat.items():
        parts = key.split(".")
        node = out
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = val
    return out


def _lookup(flat: Dict[str, Any], key: str) -> Any:
    """按大小写不敏感的方式取寄存器值，支持 'pigate.amp' / 'PiGate.amp' 两种写法。

    registry 可能是**按比特嵌套**的（{"q7ld3": {"f10": ...}}）也可能是**单比特摊平**的
    （{"f10": ...}）；后者优先，找不到再按 ".<key>" 后缀匹配。
    """
    target = key.lower()
    for k, v in flat.items():
        if k.lower() == target:
            return v
    suffix = "." + target
    for k, v in flat.items():
        if k.lower().endswith(suffix):
            return v
    return None


def _lookup_key(flat: Dict[str, Any], key: str) -> Optional[str]:
    """找到寄存器在摊平字典里的真实键名（写回时要用）。"""
    target = key.lower()
    for k in flat:
        if k.lower() == target:
            return k
    suffix = "." + target
    for k in flat:
        if k.lower().endswith(suffix):
            return k
    return None


def _resolve_key(flat: Dict[str, Any], key: str, qubit: Optional[str] = None) -> Optional[str]:
    """定位寄存器在摊平字典里的真实键名。

    优先 "<qubit>.<key>"（registry 按比特嵌套时的形态），其次裸 "<key>"，
    最后按 ".<key>" 后缀兜底。
    """
    if qubit:
        exact = f"{qubit}.{key}".lower()
        for k in flat:
            if k.lower() == exact:
                return k
    return _lookup_key(flat, key)


def _diff_flat(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """两个摊平字典的差异（{key: {from, to}}）。"""
    out: Dict[str, Any] = {}
    for key in set(before) | set(after):
        if before.get(key) != after.get(key):
            out[key] = {"from": before.get(key), "to": after.get(key)}
    return out


def _numeric(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def simulate(
    code: str,
    registry: Optional[Dict[str, Any]] = None,
    *,
    session: Optional[str] = None,
) -> Dict[str, Any]:
    """离线仿真一段实验代码的厂商语义。

    返回：
        {ok, calls[], registry_before, registry_after, diff, warnings[], error}
    """
    flat_before = _flatten(registry)
    flat_after: Dict[str, Any] = dict(flat_before)
    steps: List[Dict[str, Any]] = []
    warnings: List[str] = []
    error: Optional[Dict[str, Any]] = None

    for call in parse_calls(code):
        fn = call["fn"]
        kwargs = call["kwargs"]
        sem = VENDOR_SEMANTICS.get(fn)
        step: Dict[str, Any] = {
            "fn": fn,
            "qubit": call["qubit"],
            "kwargs": kwargs,
            "known": sem is not None,
            "auto_writes": [],
            "notes": [],
        }
        if sem is None:
            step["notes"].append(
                f"未知函数 sq.{fn}：不在厂商语义表内，仿真不覆盖（真机上可能直接 AttributeError）"
            )
            warnings.append(f"sq.{fn} 不在厂商语义表内")
            steps.append(step)
            continue

        # 必填参数 → 典型错误
        missing = [p for p in sem.get("requires", []) if p not in kwargs]
        if missing:
            detail = sem.get("defaults", {})
            hint = ""
            if fn == "spectroscopy" and "freq" in missing:
                hint = (
                    "sq.spectroscopy 必须显式传 freq=np.arange(...)；缺省会退化成标量并报 "
                    "IndexError: list index out of range"
                )
            if fn == "t1" and "zpa" in missing:
                hint = (
                    "sq.t1 必须显式传 zpa=<固定值>；缺省会做 zpa×时间二维扫描（极慢）"
                )
            step["error"] = {
                "code": "validation_error",
                "message": f"sq.{fn} 缺少必填参数 {missing}",
                "hint": hint,
            }
            warnings.append(f"sq.{fn} 缺少必填参数 {missing}")
            steps.append(step)
            error = step["error"]
            break

        # 默认展开提示
        for param, text in (sem.get("defaults") or {}).items():
            if param not in kwargs:
                step["notes"].append(f"参数 {param} 使用默认展开：{text}")

        # 自动回写（auto-update）：厂商在 update=True 时静默改寄存器
        for key in sem.get("auto_writes", []):
            old = _lookup(flat_after, key)
            new = old
            if fn in ("spectroscopy", "spectroscopy_auto", "spectroscopy_iq"):
                new = old  # 真实值取决于扫出来的共振；仿真只标出"会被改"
            elif fn == "s21":
                new = old
            elif fn == "iqraw":
                new = old
            elif fn == "pidf":
                df = _numeric(kwargs.get("df", 0.03))
                base = _numeric(old)
                if df is not None and base is not None:
                    new = round(base + df, 9)
                step["notes"].append(f"仿真把 f10 平移了 df={df}（真实值由扫出的跃迁决定）")
            elif fn == "ramsey_df":
                base = _numeric(old)
                if base is not None:
                    new = round(base + 0.000672, 9)
                step["notes"].append("仿真按实测值平移 +0.672 MHz（真实值由 Ramsey 相位决定）")
            elif fn == "piamp":
                amp = _numeric(kwargs.get("amp"))
                if amp is not None:
                    new = amp
            elif fn == "set_pi":
                if "amp" in kwargs:
                    new = _numeric(kwargs["amp"])
                elif "alpha" in kwargs:
                    new = _numeric(kwargs["alpha"])
            resolved = _resolve_key(flat_after, key, call.get("qubit"))
            if new is not None and new != old and resolved:
                # 写回（保留 registry 原始的键形态：单比特摊平 / 按比特嵌套都能命中）
                flat_after[resolved] = new
            step["auto_writes"].append(
                {"key": key, "resolved_key": resolved, "from": old, "to": new}
            )

        steps.append(step)

    diff = _diff_flat(flat_before, flat_after)
    return {
        "ok": error is None,
        "session": session,
        "calls": steps,
        "registry_before": flat_before,
        "registry_after": flat_after,
        "registry_after_nested": _unflatten(flat_after),
        "diff": diff,
        "warnings": warnings,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Fixture 录制 / 回放
# ---------------------------------------------------------------------------
class ReplayStore:
    """fixture 的落盘器（每个 fixture 一个 JSON 文件）。

    - 目录不可写时自动降级到系统临时目录（回放不打断真机执行）。
    - 线程安全。
    """

    def __init__(self, directory: Optional[str] = None):
        self._lock = threading.Lock()
        # env 覆盖优先：沙箱内 server 目录常不可写，降级到 TEMP 后 fixture 会"人间蒸发"，
        # 而读侧（CLI / 自检）在另一个可写目录 → 读到的和写的不是同一批 fixture。
        env_dir = os.environ.get("QMCLAW_REPLAY_FIXTURE_DIR")
        self._dir = (
            directory
            or env_dir
            or os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data",
                "replay-fixtures",
            )
        )
        self._active_dir = self._dir
        self._degraded = False

    @property
    def directory(self) -> str:
        return self._active_dir

    @property
    def degraded(self) -> bool:
        """True 表示目标目录不可写，已降级到临时目录（读写的可能不是同一批 fixture）。"""
        return self._degraded

    @property
    def read_directory(self) -> str:
        """读侧实际使用的目录。

        读和写分开判定：目标目录**可读**就直接读它（写探针失败只影响写），
        否则退回 `_ensure_dir()`（不可读时才降级到临时目录）。
        以前读侧也走写探针，于是沙箱里"目录不可写"就把读取也带到了临时目录 ——
        报告里显示的还是请求目录，结论指向另一批 fixture。

        注意：如果本实例**已经写过**（写侧已降级到临时目录），读侧必须跟着读那个目录，
        否则会出现"存进去读不到"（save 走临时目录、load 走请求目录）。
        """
        if self._degraded:
            return self._active_dir
        if os.path.isdir(self._dir) and os.access(self._dir, os.R_OK):
            return self._dir
        return self._ensure_dir()

    def _ensure_dir(self) -> str:
        try:
            os.makedirs(self._active_dir, exist_ok=True)
            probe = os.path.join(self._active_dir, ".probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return self._active_dir
        except Exception:
            if not self._degraded:
                self._degraded = True
                self._active_dir = os.path.join(tempfile.gettempdir(), "qmclaw-replay-fixtures")
                try:
                    os.makedirs(self._active_dir, exist_ok=True)
                except Exception:
                    pass
            return self._active_dir

    def save(self, fixture: Dict[str, Any]) -> Optional[str]:
        """写入一个 fixture；返回路径，失败返回 None。"""
        try:
            path = self._ensure_dir()
            if not path:
                return None
            fid = str(fixture.get("fixture_id") or f"fx-{uuid.uuid4().hex[:12]}")
            fixture["fixture_id"] = fid
            fname = os.path.join(path, f"{fid}.json")
            with self._lock:
                with open(fname, "w", encoding="utf-8") as fh:
                    json.dump(fixture, fh, ensure_ascii=False, indent=2)
            return fname
        except Exception:
            return None

    def load(self, fixture_id: str) -> Optional[Dict[str, Any]]:
        try:
            path = self.read_directory
            if not path or not os.path.isdir(path):
                return None
            fname = os.path.join(path, f"{fixture_id}.json")
            if not os.path.isfile(fname):
                return None
            with open(fname, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def list_fixtures(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出最近的 fixture（倒序）。"""
        try:
            path = self.read_directory
            if not path or not os.path.isdir(path):
                return []
            names = [n for n in os.listdir(path) if n.endswith(".json")]
            names.sort(reverse=True)
            out: List[Dict[str, Any]] = []
            for name in names[: max(0, int(limit))]:
                try:
                    with open(os.path.join(path, name), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    out.append(
                        {
                            "fixture_id": data.get("fixture_id"),
                            "created_at": data.get("created_at"),
                            "source": data.get("source"),
                            "qubits": data.get("qubits", []),
                            "dataset_ids": data.get("dataset_ids", []),
                            "call_count": len(data.get("calls", []) or []),
                        }
                    )
                except Exception:
                    continue
            return out
        except Exception:
            return []


def record_fixture(
    *,
    code: str,
    registry_before: Optional[Dict[str, Any]] = None,
    registry_after: Optional[Dict[str, Any]] = None,
    qubits: Optional[List[str]] = None,
    dataset_ids: Optional[List[int]] = None,
    run_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    source: str = "execute",
    status: str = "unknown",
    stdout: str = "",
) -> Dict[str, Any]:
    """从一次执行构造 fixture（尚未落盘）。"""
    sim = simulate(code, registry_before)
    return {
        "fixture_id": f"fx-{uuid.uuid4().hex[:12]}",
        "created_at": time.time(),
        "source": source,
        "run_id": run_id,
        "trace_id": trace_id,
        "task_id": task_id,
        "qubits": list(qubits or []),
        "dataset_ids": list(dataset_ids or []),
        "status": status,
        "code": code,
        "stdout": stdout[:4000],
        "registry_before": registry_before or {},
        "registry_after": registry_after or {},
        "simulated": sim,
    }


def replay_fixture(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """回放一个 fixture：拿仿真结果与真机当时的结果对比，找出厂商语义漂移。

    返回 {ok, drift[], simulated_diff, recorded_diff, notes[]}
    """
    code = str(fixture.get("code") or "")
    before = fixture.get("registry_before") or {}
    after = fixture.get("registry_after") or {}
    sim = simulate(code, before)

    # 真机记录到的真实差异：由 registry_before 与 registry_after 实算（而不是取仿真结果）
    recorded_diff = _diff_flat(_flatten(before), _flatten(after))
    simulated_diff = sim.get("diff") or {}

    drift: List[Dict[str, Any]] = []
    for key in sorted(set(recorded_diff) | set(simulated_diff)):
        rec = recorded_diff.get(key)
        simd = simulated_diff.get(key)
        if rec is None:
            drift.append({"key": key, "kind": "simulated_only", "simulated": simd})
        elif simd is None:
            drift.append({"key": key, "kind": "recorded_only", "recorded": rec})
        elif rec.get("to") != simd.get("to"):
            drift.append({"key": key, "kind": "value_mismatch", "recorded": rec, "simulated": simd})
    return {
        "ok": not drift,
        "fixture_id": fixture.get("fixture_id"),
        "drift": drift,
        "simulated_diff": simulated_diff,
        "recorded_diff": recorded_diff,
        "notes": [
            "仿真只覆盖'哪些键会被改'与'会不会报错'，具体数值以真机记录为准",
            "真实执行后应把 registry_after 写回 fixture，供后续回放比对",
        ],
    }
