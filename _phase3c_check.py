#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase3c_check.py — 阶段 3 第二批②：设备画像被 qwf/DSL 引用（lint 画像规则）。

离线自检，不连 LabRAD、不执行真机实验：
  1. lint.py 里画像规则函数存在且被 lint_ir 调用
  2. 正例：env.qubit 在画像里 → I_PROFILE_QUBIT
  3. 反例：env.qubit 不在画像里 → W_PROFILE_UNKNOWN_QUBIT
  4. 反例：节点写死别的比特 → W_PROFILE_HARDCODED_QUBIT
  5. 反例：参数里出现别的比特 → W_PROFILE_FOREIGN_QUBIT
  6. 画像不可用 → I_PROFILE_UNAVAILABLE（不报错，只提示）
  7. 画像文件本身自洽（12 比特、含 f10/f21/fread/xy_dac）
  8. 回归：既有 lint 规则没被破坏（E_T1_NO_ZPA 仍能触发）

用法：D:/anaconda3/envs/lqcs/python.exe _phase3c_check.py
"""
from __future__ import annotations

import io
import json
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
WARN = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def warn(name: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  [WARN] {name} {detail}")


def lint_text_safe(text: str):
    from services.workflow_service.dsl import lint_text
    buf = io.StringIO()
    with redirect_stdout(buf):
        rep = lint_text(text)
    return rep


def codes(rep, kind: str):
    return [w.get("code") for w in rep.get(kind, [])]


QWF_TEMPLATE = """workflow {wid} {{
  meta {{
    name "{wid}"
    description "phase3c lint fixture"
    tags ["test"]
  }}
  env {{
    qubit   "{qubit}"
    session "20251103_LD_SQ"
  }}
  budget {{
    max_wall   600
    max_writes 2
    max_datasets 2
    max_scan_points 200
  }}
  measure as c1 {{
    fn      sq.iqraw
    qubit   {qexpr}
    timeout 60s
  }}
  say "done"
}}
"""


def make_qwf(wid: str, qubit: str, qexpr: str = "$env.qubit",
             args_block: str = "") -> str:
    body = QWF_TEMPLATE.format(wid=wid, qubit=qubit, qexpr=qexpr)
    if args_block:
        body = body.replace(
            "    timeout 60s\n",
            "    timeout 60s\n    args {\n" + args_block + "    }\n")
    return body


print("=" * 72)
print("_phase3c_check — 阶段 3 第二批②：设备画像 lint 规则")
print("=" * 72)

# ── 1. lint.py 里画像规则函数存在 ──────────────────────────────────────
try:
    from services.workflow_service.dsl import lint as lint_mod
except Exception as exc:  # pragma: no cover
    print(f"  [FAIL] 导入 services.workflow_service.dsl.lint 失败: {exc}")
    print(f"\n_phase3c_check: PASS={PASS} FAIL={FAIL + 1} WARN={WARN}")
    sys.exit(1)

check("lint 模块暴露 _load_profile_qubits",
      hasattr(lint_mod, "_load_profile_qubits"))
check("lint 模块暴露 _profile_checks",
      hasattr(lint_mod, "_profile_checks"))
check("lint 模块暴露 _profile_foreign_qubit_checks",
      hasattr(lint_mod, "_profile_foreign_qubit_checks"))

src = open(os.path.join(SERVER, "services", "workflow_service", "dsl", "lint.py"),
           encoding="utf-8").read()
check("lint_ir 调用了 _profile_checks", "_profile_checks(rep, env, pipeline)" in src)
check("lint_ir 调用了 _profile_foreign_qubit_checks",
      "_profile_foreign_qubit_checks(rep, env, pipeline)" in src)
check("lint 引入了画像加载（common.profile）", "common.profile" in src)
for code in ("I_PROFILE_QUBIT", "I_PROFILE_UNAVAILABLE", "W_PROFILE_UNKNOWN_QUBIT",
             "W_PROFILE_HARDCODED_QUBIT", "W_PROFILE_FOREIGN_QUBIT"):
    check(f"规则码 {code} 定义在 lint.py", code in src)

# ── 2. 画像文件自洽 ───────────────────────────────────────────────────
profile_path = os.path.join(SERVER, "config", "device_profile.json")
check("画像文件存在", os.path.exists(profile_path), profile_path)
profile = {}
if os.path.exists(profile_path):
    try:
        profile = json.load(open(profile_path, encoding="utf-8"))
    except Exception as exc:
        check("画像文件可解析", False, str(exc))
    else:
        check("画像文件可解析", True)
qubits = profile.get("qubits") or {}
check("画像含 qubits 映射", isinstance(qubits, dict) and len(qubits) > 0,
      f"len={len(qubits) if isinstance(qubits, dict) else 'n/a'}")
if isinstance(qubits, dict):
    sample_q = next(iter(qubits))
    entry = qubits[sample_q] or {}
    for key in ("f10", "f21", "fread", "xy_dac"):
        check(f"画像条目 {sample_q}.{key} 存在", key in entry)

# ── 3. 正例：env.qubit 在画像里 → I_PROFILE_QUBIT ─────────────────────
if qubits:
    known_q = next(iter(qubits))
    rep = lint_text_safe(make_qwf("phase3c_ok", known_q))
    check("正例：env.qubit 在画像里 → I_PROFILE_QUBIT",
          "I_PROFILE_QUBIT" in codes(rep, "infos"),
          str(codes(rep, "infos")))
    check("正例：没有 W_PROFILE_UNKNOWN_QUBIT",
          "W_PROFILE_UNKNOWN_QUBIT" not in codes(rep, "warnings"),
          str(codes(rep, "warnings")))
    check("正例：$env.qubit 不触发 W_PROFILE_HARDCODED_QUBIT",
          "W_PROFILE_HARDCODED_QUBIT" not in codes(rep, "warnings"),
          str(codes(rep, "warnings")))

    # ── 4. 反例：env.qubit 不在画像里 ───────────────────────────────
    rep = lint_text_safe(make_qwf("phase3c_unknown", "qZZld3"))
    check("反例：env.qubit 不在画像 → W_PROFILE_UNKNOWN_QUBIT",
          "W_PROFILE_UNKNOWN_QUBIT" in codes(rep, "warnings"),
          str(codes(rep, "warnings")))

    # ── 5. 反例：节点写死别的比特 ───────────────────────────────────
    other = next((q for q in qubits if q != known_q), None)
    if other:
        rep = lint_text_safe(make_qwf("phase3c_hardcoded", known_q,
                                      qexpr=other))
        check("反例：节点写死别的比特 → W_PROFILE_HARDCODED_QUBIT",
              "W_PROFILE_HARDCODED_QUBIT" in codes(rep, "warnings"),
              str(codes(rep, "warnings")))

    # ── 6. 反例：参数里出现别的比特 ─────────────────────────────────
    rep = lint_text_safe(make_qwf("phase3c_foreign", known_q,
                                  args_block=f'note "target {other}"\n'))
    check("反例：参数里出现别的比特 → W_PROFILE_FOREIGN_QUBIT",
          "W_PROFILE_FOREIGN_QUBIT" in codes(rep, "warnings"),
          str(codes(rep, "warnings")))

# ── 7. 画像不可用 → I_PROFILE_UNAVAILABLE（不报错） ──────────────────
orig_loader = lint_mod._load_profile_qubits
try:
    lint_mod._load_profile_qubits = lambda: {}
    rep = lint_text_safe(make_qwf("phase3c_noprofile", "q7ld3"))
    check("画像不可用 → I_PROFILE_UNAVAILABLE",
          "I_PROFILE_UNAVAILABLE" in codes(rep, "infos"),
          str(codes(rep, "infos")))
    check("画像不可用 → 不产生 W_PROFILE_UNKNOWN_QUBIT",
          "W_PROFILE_UNKNOWN_QUBIT" not in codes(rep, "warnings"),
          str(codes(rep, "warnings")))
finally:
    lint_mod._load_profile_qubits = orig_loader

# ── 8. 既有 lint 规则未被破坏 ────────────────────────────────────────
bad_t1 = """workflow phase3c_t1 {
  env { qubit "q7ld3" }
  budget { max_wall 600 max_writes 2 max_datasets 2 }
  measure as c1 { fn sq.t1 qubit $env.qubit timeout 60s }
  say "done"
}
"""
rep = lint_text_safe(bad_t1)
check("回归：sq.t1 未传 zpa 仍触发 E_T1_NO_ZPA",
      "E_T1_NO_ZPA" in codes(rep, "errors"), str(codes(rep, "errors")))

# ── 9. 厂商坑静态预判（freq=None / 默认 zpa 展开 / 扫描点数上限）─────────────
def make_fn_qwf(wid: str, fn: str, args_block: str = "") -> str:
    return """workflow {wid} {{
  meta {{ name "{wid}" }}
  env {{
    qubit   "q7ld3"
    session "20251103_LD_SQ"
  }}
  budget {{
    max_wall 600
    max_writes 2
    max_datasets 2
    max_scan_points 200
  }}
  measure as c1 {{
    fn      {fn}
    qubit   $env.qubit
    timeout 60s
{args}
  }}
  say "done"
}}
""".format(wid=wid, fn=fn,
           args=("    args {\n" + args_block + "    }\n") if args_block else "")


rep = lint_text_safe(make_fn_qwf("p3c_spec_nofreq", "sq.spectroscopy"))
check("sq.spectroscopy 不传 freq → E_SPEC_NO_FREQ",
      "E_SPEC_NO_FREQ" in codes(rep, "errors"), str(codes(rep, "errors")))

rep = lint_text_safe(make_fn_qwf("p3c_spec_freq", "sq.spectroscopy", "      freq 4.0\n"))
check("sq.spectroscopy 传了 freq → 不报 E_SPEC_NO_FREQ",
      "E_SPEC_NO_FREQ" not in codes(rep, "errors"), str(codes(rep, "errors")))

rep = lint_text_safe(make_fn_qwf("p3c_zpa2d_default", "sq.s21_zpa2d", "      freq 1.0\n"))
check("sq.s21_zpa2d 不传 zpa → W_ZPA2D_DEFAULT_ZPA",
      "W_ZPA2D_DEFAULT_ZPA" in codes(rep, "warnings"), str(codes(rep, "warnings")))

rep = lint_text_safe(make_fn_qwf("p3c_zpa2d_explicit", "sq.s21_zpa2d",
                                 "      freq 1.0\n      zpa 0.0\n"))
check("sq.s21_zpa2d 显式 zpa → 不报 W_ZPA2D_DEFAULT_ZPA",
      "W_ZPA2D_DEFAULT_ZPA" not in codes(rep, "warnings"), str(codes(rep, "warnings")))

# 扫描点数用 lint_ir 直接测：文本 DSL 的 lexer 不认 `np.arange(...)`（括号非法），
# 真实 IR 里 args 是 JSON 里的字符串，所以这里模拟 qwf_gen 生成的 IR。
def make_ir(shot_expr: str) -> dict:
    return {
        "workflow": "p3c_points",
        "env": {"qubit": "q7ld3"},
        "budget": {"maxWall": 600, "maxWrites": 2, "maxDatasets": 2, "maxScanPoints": 200},
        "pipeline": [
            {"op": "measure", "as": "c1", "fn": "sq.iqraw",
             "qubit": "$env.qubit", "timeout": 60, "args": {"shots": shot_expr}},
        ],
    }


rep = lint_mod.lint_ir(make_ir("np.arange(0, 4000, 1)"))
check("超大扫描轴 → W_SWEEP_LARGE",
      "W_SWEEP_LARGE" in codes(rep, "warnings"), str(codes(rep, "warnings")))

rep = lint_mod.lint_ir(make_ir("np.arange(0, 100, 1)"))
check("预算内的扫描轴 → 不报 W_SWEEP_LARGE",
      "W_SWEEP_LARGE" not in codes(rep, "warnings"), str(codes(rep, "warnings")))
rep = lint_text_safe(make_fn_qwf("p3c_few_points", "sq.iqraw",
                                 "      shots np.arange(0, 10, 1)\n"))
check("小扫描轴 → 不报 W_SWEEP_LARGE",
      "W_SWEEP_LARGE" not in codes(rep, "warnings"), str(codes(rep, "warnings")))

check("_sweep_points/arange 估算", lint_mod._sweep_points("np.arange(0, 1, 0.1)") == 11,
      str(lint_mod._sweep_points("np.arange(0, 1, 0.1)")))
check("_sweep_points/linspace 估算", lint_mod._sweep_points("np.linspace(0, 1, 51)") == 51,
      str(lint_mod._sweep_points("np.linspace(0, 1, 51)")))
check("_sweep_points/list 长度", lint_mod._sweep_points([1, 2, 3]) == 3)
check("_sweep_points/认不出就 0", lint_mod._sweep_points("q2ld3.regs.f10") == 0,
      str(lint_mod._sweep_points("q2ld3.regs.f10")))

# ── 10. analyze 绑定（回归：曾误报 E_UNKNOWN_BINDING）────────────────────────
# 「analyze c1 as a1 → say ${a1.T1_us}」是最常见写法。早期 lint 的 binds 只登记
# measure/read/decide，导致所有「分析后汇报」的工作流都报 E_UNKNOWN_BINDING。
ANALYZE_QWF = """workflow p3c_analyze_bind {
  meta { name "p3c_analyze_bind" }
  env { qubit "q2ld3" }
  budget { max_wall 600 max_writes 2 max_datasets 2 }
  measure as c1 { fn sq.t1 qubit $env.qubit args { zpa 0.0 } timeout 60s }
  analyze c1 as a1 { command "qter.fitData(do_plot=False)" }
  assert a1.T1_us >= 20 else halt as g1
  say "T1 = ${a1.T1_us} us"
}
"""
rep = lint_text_safe(ANALYZE_QWF)
check("analyze 绑定：say 引用 ${a1.T1_us} 不再报 E_UNKNOWN_BINDING",
      "E_UNKNOWN_BINDING" not in codes(rep, "errors"), str(codes(rep, "errors")))
check("analyze 绑定：${a1.T1_us} 是已知字段（无 W_UNKNOWN_ATTR）",
      "W_UNKNOWN_ATTR" not in codes(rep, "warnings"), str(codes(rep, "warnings")))
rep = lint_text_safe(ANALYZE_QWF.replace("${a1.T1_us}", "${zz.T1_us}"))
check("未声明绑定仍报 E_UNKNOWN_BINDING",
      "E_UNKNOWN_BINDING" in codes(rep, "errors"), str(codes(rep, "errors")))
rep = lint_text_safe(ANALYZE_QWF.replace("${a1.T1_us}", "${a1.nonsense_field}"))
check("analyze 未知字段只 WARN 不 ERROR",
      "W_UNKNOWN_ATTR" in codes(rep, "warnings")
      and "E_UNKNOWN_BINDING" not in codes(rep, "errors"),
      str(codes(rep, "warnings")))

print("=" * 72)
print(f"_phase3c_check: PASS={PASS} FAIL={FAIL} WARN={WARN}")
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
print("=" * 72)
sys.exit(1 if FAIL else 0)
