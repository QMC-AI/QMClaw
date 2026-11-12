"""从 lqms 源码里抽取「哪些 sq 实验会自动改参数」的实证表。

用途：`services/workflow_service/dsl/backends/lqcs.py` 里的 `WRITE_EFFECTS` 是**人工
整理的知识表**，这个脚本负责把「整理依据」变成可复跑的证据——它只读源码，不猜：

1. 每个 `sq.*` 函数的签名里有没有 `update` 参数、默认值是什么；
2. 函数体里调了哪些 `runner.update_xxx(...)`，调用处有没有把 `update` 传下去；
3. 顺带列出 `base_experiment.py` 里每个 `update_xxx` 自己是否有 `if update:` 保护、
   默认值是什么、写到哪些注册表键。

判档规则（写进报告）：
  always      → 调用处没传 update（或被调方根本没有 update 参数）→ 无论调用者传什么都会写
  default_on  → 签名 update=True 且调用处把 update 传下去了 → 默认写，可关
  opt_in      → 签名 update=False → 默认不写，显式开
  none        → 函数体里没有任何 update_* 调用

运行：python docs/deploy_diff_tools/lqms_write_audit.py [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

SQ_NODES = os.environ.get(
    "QMCLAW_LQMS_SQ_NODES",
    r"D:/anaconda3/envs/lqcs/Lib/site-packages/lqms/measure/tuners/sq_nodes.py",
)
BASE_EXP = os.environ.get(
    "QMCLAW_LQMS_BASE_EXP",
    r"D:/anaconda3/envs/lqcs/Lib/site-packages/lqms/measure/tuners/base_experiment.py",
)

# update_xxx → 注册表键（人工对照，行号证据见 base_experiment.py）
UPDATE_KEYS = {
    "update_amp": ["PiGate.amp"],
    "update_alpha": ["PiGate.alpha"],
    "update_f10": ["f10"],
    "update_fread": ["fread"],
    "update_power": ["power"],
    "update_zeff": ["zeff"],
    "update_iq": ["discriminator.center0", "discriminator.center1"],
    "update_iq_zero": ["discriminator.center0", "discriminator.radius0", "discriminator.method"],
    "update_xy": ["timing.xy"],
    "update_z": ["timing.z"],
    "update_optimal_params": ["(多键，见 update_optimal_params)"],
}


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def audit_sq_nodes(path: str = SQ_NODES) -> List[Dict[str, Any]]:
    src = _read(path)
    tree = ast.parse(src)
    out: List[Dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        defaults = [ast.unparse(d) for d in node.args.defaults] + [
            ast.unparse(d) for d in node.args.kw_defaults if d is not None
        ]
        upd_default: Optional[str] = None
        has_update = "update" in args
        if has_update:
            # 把 defaults 对齐到参数尾部（简单对齐：Python 规则下 defaults 对齐 args 末尾）
            try:
                idx = len(node.args.args) - len(node.args.defaults)
                # 只处理 update 在普通参数里的情况
                pos = node.args.args.index(
                    next(a for a in node.args.args if a.arg == "update")
                )
                if pos >= idx:
                    upd_default = ast.unparse(node.args.defaults[pos - idx])
            except (StopIteration, ValueError):
                upd_default = "?"
        calls: List[Dict[str, Any]] = []
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if not name.startswith("update_"):
                continue
            passed = {kw.arg for kw in sub.keywords if kw.arg}
            calls.append(
                {
                    "callee": name,
                    "line": sub.lineno,
                    "passes_update": "update" in passed,
                    "keys": UPDATE_KEYS.get(name, ["?"]),
                }
            )
        if not calls:
            mode = "none"
        elif any(not c["passes_update"] for c in calls):
            mode = "always"
        elif has_update and upd_default == "True":
            mode = "default_on"
        elif has_update and upd_default == "False":
            mode = "opt_in"
        else:
            mode = "unknown"
        keys: List[str] = []
        for c in calls:
            for k in c["keys"]:
                if k not in keys:
                    keys.append(k)
        out.append(
            {
                "fn": f"sq.{node.name}",
                "line": node.lineno,
                "has_update_param": has_update,
                "update_default": upd_default,
                "mode": mode,
                "writes": keys,
                "calls": calls,
            }
        )
    return out


def audit_base_classes(path: str = BASE_EXP) -> List[Dict[str, Any]]:
    """`update_xxx` 自己有没有 `if update:` 保护 —— 决定「传 update 下去有没有用」。"""
    src = _read(path)
    tree = ast.parse(src)
    rows: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef,)):
            continue
        if not node.name.startswith("update_"):
            continue
        args = [a.arg for a in node.args.args]
        has_update = "update" in args
        default: Optional[str] = None
        if has_update:
            try:
                pos = args.index("update")
                base = len(node.args.args) - len(node.args.defaults)
                if pos >= base:
                    default = ast.unparse(node.args.defaults[pos - base])
            except ValueError:
                pass
        guarded = False
        for sub in ast.walk(node):
            if isinstance(sub, ast.If) and "update" in ast.unparse(sub.test):
                guarded = True
                break
        klass = ""
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and node in parent.body:
                klass = parent.name
                break
        rows.append(
            {
                "class": klass,
                "fn": node.name,
                "line": node.lineno,
                "has_update_param": has_update,
                "update_default": default,
                "guarded_by_if_update": guarded,
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(SQ_NODES):
        print(f"找不到 {SQ_NODES}（可用 QMCLAW_LQMS_SQ_NODES 覆盖）", file=sys.stderr)
        return 2
    rows = audit_sq_nodes()
    base = audit_base_classes() if os.path.exists(BASE_EXP) else []
    if args.json:
        print(json.dumps({"sq": rows, "base": base}, ensure_ascii=False, indent=2))
        return 0
    print(f"# sq_nodes.py 写入语义（来源 {SQ_NODES}）\n")
    print(f"{'函数':<24}{'档':<12}{'update 默认':<12}{'写入键':<34}证据行")
    for r in rows:
        ev = ",".join(str(c["line"]) for c in r["calls"]) or "-"
        print(
            f"{r['fn']:<24}{r['mode']:<12}{str(r['update_default']):<12}"
            f"{','.join(r['writes']) or '-':<34}{ev}"
        )
    print(f"\n# base_experiment.py 的 update_* 是否受 if update 保护（{len(base)} 个）\n")
    print(f"{'类':<22}{'方法':<22}{'默认':<8}{'if update 保护':<10}行")
    for r in base:
        print(
            f"{r['class']:<22}{r['fn']:<22}{str(r['update_default']):<8}"
            f"{str(r['guarded_by_if_update']):<10}{r['line']}"
        )
    tally: Dict[str, int] = {}
    for r in rows:
        tally[r["mode"]] = tally.get(r["mode"], 0) + 1
    print(f"\n分档合计：{tally}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
