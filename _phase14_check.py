#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase14_check.py — 阶段 14：厂商语义风险进 lint（roadmap §3 #4）。

背景：厂商实验的"默认为真机做点什么"从来没被静态拦过——
  - `sq.iqraw` / `sq.piamp` / `sq.pidf` / `sq.spectroscopy` 等 `mode="default_on"`
    的实验**不写 update 就会回写寄存器**，一个"只看看"的 workflow 也会改参数；
  - `sq.pidf` 默认 `df=0.03`（±30 MHz），真实偏差 35.7 MHz 时整段扫空；
  - 必填参数（`sq.t1` 的 zpa、`sq.spectroscopy` 的 freq）已在 whitelist 里，
    但新增实验只能靠人肉记。

本阶段把这些规则做成**表驱动**（复用 `WRITE_EFFECTS` 与 `VENDOR_SEMANTICS`，
lint 里不再抄第二份厂商语义）：

  - `W_UPDATE_DEFAULT_ON`：default_on 类实验没关 update → 警告（带 keys）
  - `I_UPDATE_ON`：显式写了 update=True → info（明确知道会写）
  - `W_PIDF_DF_SMALL`：sq.pidf 缺 df 或 |df| < 0.05 → 警告
  - `E_ARG_MISSING`：VENDOR_SEMANTICS 里的 requires 未满足（专用规则已覆盖的跳过）

用法：D:/anaconda3/envs/lqcs/python.exe _phase14_check.py
"""
from __future__ import annotations

import ast
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
FAILED: list[str] = []

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


from services.workflow_service.dsl import lint as L  # noqa: E402
from services.workflow_service.dsl.backends.lqcs import WRITE_EFFECTS  # noqa: E402
from services.common.replay import VENDOR_SEMANTICS  # noqa: E402

LINT_SRC = open(os.path.join(SERVER, "services", "workflow_service", "dsl", "lint.py"),
                encoding="utf-8").read()


def make_ir(fn: str, args: dict | None = None, **extra) -> dict:
    """构造只含一个 measure 节点的 IR（args 用 JSON 里的字符串/数字形式）。"""
    stmt = {"op": "measure", "as": "c1", "fn": fn, "qubit": "$env.qubit",
            "timeout": extra.pop("timeout", 60), "args": dict(args or {})}
    stmt.update(extra)
    return {
        "workflow": "p14",
        "env": {"qubit": "q2ld3"},
        "budget": {"maxWall": 600, "maxWrites": 4, "maxDatasets": 2},
        "pipeline": [stmt],
    }


def codes(rep, bucket: str) -> list:
    if isinstance(rep, dict):
        return [d.get("code") for d in (rep.get(bucket) or [])]
    return [d.get("code") for d in getattr(rep, bucket)]


def lint(fn: str, args: dict | None = None, **extra):
    return L.lint_ir(make_ir(fn, args, **extra))


print("=" * 72)
print("阶段 14：厂商语义风险进 lint")
print("=" * 72)

# ── 1. 常量与辅助函数 ────────────────────────────────────────────────────────
print("\n[1] 常量与辅助函数")
check("UPDATE_FLAG 与厂商默认 flag 一致", L.UPDATE_FLAG == "update", L.UPDATE_FLAG)
check("MIN_PIDF_DF = 0.05", L.MIN_PIDF_DF == 0.05, str(L.MIN_PIDF_DF))
check("专用规则覆盖表含 (sq.t1, zpa)", ("sq.t1", "zpa") in L._REQUIREMENT_COVERED)
check("专用规则覆盖表含 (sq.spectroscopy, freq)",
      ("sq.spectroscopy", "freq") in L._REQUIREMENT_COVERED)

for text, want in [("true", True), ("False", False), ("1", True), ("0", False),
                   ("yes", True), ("OFF", False), (True, True), (False, False),
                   ("q2ld3.regs.f10", None), ("", None), (None, None)]:
    check(f"_as_flag({text!r}) == {want}", L._as_flag(text) == want, str(L._as_flag(text)))

for text, want in [(0.03, 0.03), ("0.05", 0.05), ("-0.1", -0.1), (3, 3.0),
                   (True, None), ("np.arange(0, 1)", None), (None, None)]:
    got = L._as_float(text)
    ok = (got is None and want is None) or (got is not None and want is not None
                                            and abs(got - want) < 1e-12)
    check(f"_as_float({text!r}) == {want}", ok, str(got))

check("_vendor_requires(sq.t1) == ['zpa']", L._vendor_requires("sq.t1") == ["zpa"],
      str(L._vendor_requires("sq.t1")))
check("_vendor_requires(sq.spectroscopy) == ['freq']",
      L._vendor_requires("sq.spectroscopy") == ["freq"],
      str(L._vendor_requires("sq.spectroscopy")))
check("_vendor_requires(sq.iqraw) == []", L._vendor_requires("sq.iqraw") == [])
check("_vendor_requires 未知函数不抛", L._vendor_requires("sq.nope_xyz") == [])

# ── 2. 默认回写：W_UPDATE_DEFAULT_ON / I_UPDATE_ON ──────────────────────────
print("\n[2] 默认回写（default_on）")
default_on_fns = [fn for fn, eff in WRITE_EFFECTS.items()
                  if isinstance(eff, dict) and eff.get("mode") == "default_on"]
check("WRITE_EFFECTS 里确有 default_on 类实验（≥6 个）", len(default_on_fns) >= 6,
      str(sorted(default_on_fns)[:8]))

for fn in ("sq.iqraw", "sq.piamp", "sq.pidf", "sq.spectroscopy"):
    rep = lint(fn)
    check(f"{fn} 不写 update → W_UPDATE_DEFAULT_ON",
          "W_UPDATE_DEFAULT_ON" in codes(rep, "warnings"), str(codes(rep, "warnings")))

rep = lint("sq.iqraw")
warn_docs = [d for d in rep["warnings"] if d.get("code") == "W_UPDATE_DEFAULT_ON"]
check("W_UPDATE_DEFAULT_ON 带 keys（i.e. 会写哪些键）",
      bool(warn_docs) and "discriminator.center0" in (warn_docs[0].get("keys") or []),
      str(warn_docs[:1]))
check("W_UPDATE_DEFAULT_ON 带 hint（怎么关）",
      bool(warn_docs) and "update" in str(warn_docs[0].get("hint") or ""))

rep = lint("sq.piamp", {"update": "False"})
check("sq.piamp update=False → 不报 W_UPDATE_DEFAULT_ON",
      "W_UPDATE_DEFAULT_ON" not in codes(rep, "warnings"), str(codes(rep, "warnings")))
rep = lint("sq.piamp", {"update": False})
check("sq.piamp update=false(bool) → 不报 W_UPDATE_DEFAULT_ON",
      "W_UPDATE_DEFAULT_ON" not in codes(rep, "warnings"), str(codes(rep, "warnings")))

rep = lint("sq.piamp", {"update": "True"})
check("sq.piamp update=True → 不报 warning 但要出 I_UPDATE_ON",
      "W_UPDATE_DEFAULT_ON" not in codes(rep, "warnings")
      and "I_UPDATE_ON" in codes(rep, "infos"),
      f"warn={codes(rep, 'warnings')} info={codes(rep, 'infos')}")

rep = lint("sq.piamp", {"update": "some_expr"})
check("update 写表达式（认不出）→ 不误报 W_UPDATE_DEFAULT_ON",
      "W_UPDATE_DEFAULT_ON" not in codes(rep, "warnings"), str(codes(rep, "warnings")))

rep = lint("sq.t1", {"zpa": 0.0})
check("mode=none 的实验（sq.t1）不报默认回写",
      "W_UPDATE_DEFAULT_ON" not in codes(rep, "warnings"), str(codes(rep, "warnings")))

# ── 3. sq.pidf 的 df 太小 ───────────────────────────────────────────────────
print("\n[3] sq.pidf 的 df 覆盖范围")
rep = lint("sq.pidf")
check("sq.pidf 不写 df → W_PIDF_DF_SMALL（默认只覆盖 ±30 MHz）",
      "W_PIDF_DF_SMALL" in codes(rep, "warnings"), str(codes(rep, "warnings")))

rep = lint("sq.pidf", {"df": "0.03"})
check("sq.pidf df=0.03 → W_PIDF_DF_SMALL", "W_PIDF_DF_SMALL" in codes(rep, "warnings"),
      str(codes(rep, "warnings")))
rep = lint("sq.pidf", {"df": "-0.04"})
check("sq.pidf df=-0.04（负向扫，绝对值小）→ W_PIDF_DF_SMALL",
      "W_PIDF_DF_SMALL" in codes(rep, "warnings"), str(codes(rep, "warnings")))
rep = lint("sq.pidf", {"df": "0.05"})
check("sq.pidf df=0.05 → 不报（边界值放行）",
      "W_PIDF_DF_SMALL" not in codes(rep, "warnings"), str(codes(rep, "warnings")))
rep = lint("sq.pidf", {"df": "0.6"})
check("sq.pidf df=0.6 → 不报", "W_PIDF_DF_SMALL" not in codes(rep, "warnings"),
      str(codes(rep, "warnings")))
rep = lint("sq.pidf", {"df": "$env.qubit"})
check("sq.pidf df 是引用（认不出）→ 不误报",
      "W_PIDF_DF_SMALL" not in codes(rep, "warnings"), str(codes(rep, "warnings")))
rep = lint("sq.ramsey_df", {"delay": "100"})
check("别的函数（sq.ramsey_df）不报 W_PIDF_DF_SMALL",
      "W_PIDF_DF_SMALL" not in codes(rep, "warnings"), str(codes(rep, "warnings")))

# ── 4. 通用必填参数 E_ARG_MISSING ───────────────────────────────────────────
print("\n[4] 通用必填参数（表驱动）")
rep = lint("sq.t1", {})
check("sq.t1 缺 zpa → 仍只报专用的 E_T1_NO_ZPA（不重复报 E_ARG_MISSING）",
      "E_T1_NO_ZPA" in codes(rep, "errors")
      and "E_ARG_MISSING" not in codes(rep, "errors"), str(codes(rep, "errors")))
rep = lint("sq.spectroscopy", {})
check("sq.spectroscopy 缺 freq → 仍只报 E_SPEC_NO_FREQ",
      "E_SPEC_NO_FREQ" in codes(rep, "errors")
      and "E_ARG_MISSING" not in codes(rep, "errors"), str(codes(rep, "errors")))

_orig_requires = L._vendor_requires
try:
    L._vendor_requires = lambda fn: ["my_flag"] if fn == "sq.piamp" else _orig_requires(fn)
    rep = lint("sq.piamp", {})
    check("打桩：requires 未满足 → E_ARG_MISSING",
          "E_ARG_MISSING" in codes(rep, "errors"), str(codes(rep, "errors")))
    rep = lint("sq.piamp", {"my_flag": "1"})
    check("打桩：requires 已满足 → 不报 E_ARG_MISSING",
          "E_ARG_MISSING" not in codes(rep, "errors"), str(codes(rep, "errors")))
finally:
    L._vendor_requires = _orig_requires

check("恢复真实 _vendor_requires 后 sq.piamp 无 E_ARG_MISSING",
      "E_ARG_MISSING" not in codes(lint("sq.piamp"), "errors"))

# ── 5. 既有规则回归（新规则不能顶掉老的诊断）───────────────────────────────
print("\n[5] 既有规则回归")
rep = lint("sq.allxy", {})
check("不存在的函数仍报 E_UNKNOWN_FN", "E_UNKNOWN_FN" in codes(rep, "errors"),
      str(codes(rep, "errors")))
rep = L.lint_ir(make_ir("sq.iqraw", {}, do_plot=True))
check("measure 写 do_plot 仍报 E_DO_PLOT", "E_DO_PLOT" in codes(rep, "errors"),
      str(codes(rep, "errors")))
rep = L.lint_ir(make_ir("sq.iqraw", {}, timeout=None))
check("没写 timeout 仍报 W_NO_TIMEOUT", "W_NO_TIMEOUT" in codes(rep, "warnings"),
      str(codes(rep, "warnings")))
rep = lint("sq.iqraw", {"update": "False"})
check("update=False 的 iqraw 仍报 W_WRITE_FN（写入语义提示保留）",
      "W_WRITE_FN" in codes(rep, "warnings") or "I_DERIVED_KEYS" in codes(rep, "infos"),
      f"warn={codes(rep, 'warnings')} info={codes(rep, 'infos')}")

# ── 6. 表驱动（lint 里不许再抄一份厂商语义）────────────────────────────────
print("\n[6] 契约：表驱动 + 文档 + runner")
check("lint.py 引用 WRITE_EFFECTS（_write_effects）", "from .backends.lqcs import WRITE_EFFECTS" in LINT_SRC)
check("lint.py 通过 _vendor_requires 读 VENDOR_SEMANTICS",
      "from ...common.replay import VENDOR_SEMANTICS" in LINT_SRC and "_vendor_requires" in LINT_SRC)
for token in ("W_UPDATE_DEFAULT_ON", "I_UPDATE_ON", "W_PIDF_DF_SMALL", "E_ARG_MISSING",
              "_REQUIREMENT_COVERED", "MIN_PIDF_DF"):
    check(f"lint.py 含 {token}", token in LINT_SRC)
check("lint.py 没有硬编码 discriminator 键清单（不许抄 second 份）",
      "'discriminator.center0', 'discriminator.center1'" not in LINT_SRC)

try:
    ast.parse(LINT_SRC)
    check("lint.py 语法 OK", True)
except SyntaxError as exc:  # pragma: no cover
    check("lint.py 语法 OK", False, str(exc))

OPS = os.path.join(ROOT, "docs", "OPERATIONS.md")
ops = open(OPS, encoding="utf-8").read() if os.path.exists(OPS) else ""
check("OPERATIONS 写了 W_UPDATE_DEFAULT_ON", "W_UPDATE_DEFAULT_ON" in ops)
check("OPERATIONS 写了 W_PIDF_DF_SMALL", "W_PIDF_DF_SMALL" in ops)
check("OPERATIONS §4 自检表登记 _phase14_check.py", "_phase14_check.py" in ops)

RUNNER = os.path.join(ROOT, "run_all_checks.py")
runner = open(RUNNER, encoding="utf-8").read() if os.path.exists(RUNNER) else ""
check("run_all_checks.py OFFLINE 含 _phase14_check.py", "_phase14_check.py" in runner)

ROADMAP = os.path.join(ROOT, "docs", "ARCHITECTURE_ROADMAP.md")
roadmap = open(ROADMAP, encoding="utf-8").read() if os.path.exists(ROADMAP) else ""
check("ROADMAP 记录了阶段 14（§3 #4）",
      "阶段 14" in roadmap or "W_UPDATE_DEFAULT_ON" in roadmap)

print("\n" + "=" * 72)
print(f"PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  -", name)
print("=" * 72)
sys.exit(1 if FAIL else 0)
