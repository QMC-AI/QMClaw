"""services/common/capabilities.py — 能力探测（静态 AST，不 import 硬件栈）

为什么需要它（docs/ARCHITECTURE_ROADMAP.md §2.2 / §3 问题⑤）：
本机 lqms 安装里 **.pyd 编译扩展遮蔽了更新的 .py 源码**：

    lqms/measure/tuners/sq_nodes.cp311-win_amd64.pyd   (2026-04-21)  ← 实际被导入
    lqms/measure/tuners/sq_nodes.py                     (2026-09-08)  ← 含新功能

结果：`sq.spectroscopy_iq` / `sq.time_rabi` / `sq.iqcenter` / `sq.impa_gain` / `sq.pidf21`
在运行时不存在，且**重启服务也无用**（装的就是这套被遮蔽的包）。

本模块的做法：
- 用 `ast` **静态解析 .py 源码**拿到"源码里有什么功能"；
- 与"运行时实际导出了什么"（KNOWN_RUNTIME_EXPORTS 基线 / 可选运行时探测）比对；
- 输出 `missing_in_runtime`，让上层（lint / 端口层 / 目标求解）能提前知道"这个实验跑不了"。

约束：纯标准库；不 import lqms（避免 torch/MKL DLL 冲突与启动开销）；
不碰 LabRAD → 可在 CI / 影子实例离线跑。
"""

from __future__ import annotations

import ast
import os
import sys
from typing import Any, Dict, List, Optional

# 运行时实际导出（2026-11-09 在 D:\anaconda3\envs\lqcs 观测到的 pyd 导出集合）
KNOWN_RUNTIME_EXPORTS: Dict[str, List[str]] = {
    "lqms.measure.tuners.sq_nodes": [
        "ERROR_COLOR", "INFO_COLOR", "QuantumObject", "WARN_COLOR", "basex",
        "colorama", "convertUnits", "iqraw", "np", "ph", "piamp", "pidf", "plt",
        "pulse_shape", "pulse_shape_ramsey", "ramsey_df", "ramsey_t2", "s21",
        "s21_dis", "s21_power2d", "s21_zpa2d", "set_pi", "smt_experiment",
        "spectroscopy", "spectroscopy_auto", "spinecho_t2", "st", "t1",
        "test_timing_xyz", "util", "xeb",
    ],
    "lqms.measure.tuners.base_experiment": [
        "AlphaFine", "AutoScanExperiment", "BaseExperiment", "BasicTuner",
        "Dataset", "IQraw", "MultiTypeExperiment", "PiAmpFine", "PiPulse",
        "PulseShape", "PulseShapeFreq", "PulseShapeRamsey", "Qubit",
        "Ramsey", "ResonatorPhotonDecay", "S21", "Spectroscopy",
        "SpectroscopyAuto", "SpinEchoCPMG", "T1", "TimingXYZ", "XEB",
    ],
}

# 源码里有、运行时没有（.pyd 遮蔽导致的功能缺失）
KNOWN_MISSING: List[str] = [
    "spectroscopy_iq", "time_rabi", "iqcenter", "impa_gain", "pidf21",
]


def find_lqms_root() -> Optional[str]:
    """在 sys.path 中找到 lqms 包目录（不 import 它）。"""
    for entry in sys.path:
        if not entry:
            continue
        cand = os.path.join(entry, "lqms")
        if os.path.isdir(cand):
            return cand
    return None


def scan_defs(path: str) -> List[str]:
    """静态解析一个 .py 文件，返回其中定义的顶层函数与类名。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except Exception:
        return []
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return sorted(names)


def discover_shadowed(tuners_dir: str) -> List[Dict[str, Any]]:
    """找出"同目录下 .pyd 与 .py 并存"的模块（.pyd 优先被导入 → 遮蔽 .py）。"""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(tuners_dir):
        return out
    py_files = [f for f in os.listdir(tuners_dir) if f.endswith(".py")]
    for py_name in sorted(py_files):
        stem = py_name[:-3]
        pyd_candidates = [
            f for f in os.listdir(tuners_dir)
            if f.startswith(stem + ".") and f.endswith(".pyd")
        ]
        if not pyd_candidates:
            continue
        py_path = os.path.join(tuners_dir, py_name)
        pyd_path = os.path.join(tuners_dir, pyd_candidates[0])
        try:
            py_mtime = os.path.getmtime(py_path)
            pyd_mtime = os.path.getmtime(pyd_path)
        except OSError:
            py_mtime = pyd_mtime = 0.0
        out.append({
            "module": "lqms.measure.tuners." + stem,
            "source": py_path,
            "compiled": pyd_path,
            "source_mtime": py_mtime,
            "compiled_mtime": pyd_mtime,
            "source_is_newer": bool(py_mtime > pyd_mtime),
        })
    return out


def capability_report(lqms_root: Optional[str] = None) -> Dict[str, Any]:
    """生成能力报告。

    Returns:
        {
          "lqms_root": str|None,
          "shadowed_modules": [...],
          "capabilities": {module: {"source_functions":[...], "runtime_exports":[...],
                                     "missing_in_runtime":[...]}},
          "known_missing": [...],
          "verdict": "degraded" | "ok",
          "note": "...",
        }
    """
    root = lqms_root or find_lqms_root()
    report: Dict[str, Any] = {
        "lqms_root": root,
        "shadowed_modules": [],
        "capabilities": {},
        "known_missing": list(KNOWN_MISSING),
        "verdict": "unknown",
        "note": "",
    }
    if not root:
        report["verdict"] = "unknown"
        report["note"] = "未在 sys.path 中找到 lqms 包目录"
        return report

    tuners = os.path.join(root, "measure", "tuners")
    shadowed = discover_shadowed(tuners)
    report["shadowed_modules"] = shadowed

    for item in shadowed:
        module = item["module"]
        src_funcs = scan_defs(item["source"])
        runtime = list(KNOWN_RUNTIME_EXPORTS.get(module, []))
        missing = sorted([f for f in src_funcs if f not in runtime]) if runtime else []
        report["capabilities"][module] = {
            "source_functions": src_funcs,
            "runtime_exports": runtime,
            "missing_in_runtime": missing,
            "source_is_newer": item["source_is_newer"],
        }

    sq = report["capabilities"].get("lqms.measure.tuners.sq_nodes", {})
    missing_sq = sq.get("missing_in_runtime", [])
    if missing_sq:
        report["verdict"] = "degraded"
        report["note"] = (
            "lqms 被 .pyd 遮蔽：" + ", ".join(missing_sq[:12])
            + " 在源码中存在但运行时不可用；重启服务无效，需实验室重新编译扩展。"
        )
    else:
        report["verdict"] = "ok"
        report["note"] = "未发现 .pyd 遮蔽导致的明显功能缺失"
    return report


def unavailable_experiments(report: Optional[Dict[str, Any]] = None) -> List[str]:
    """返回因能力缺失而不可用的实验函数名清单（供 lint / 前端置灰）。"""
    rep = report or capability_report()
    out: List[str] = []
    for module, info in (rep.get("capabilities") or {}).items():
        for fn in info.get("missing_in_runtime", []):
            out.append(fn)
    return sorted(set(out))
