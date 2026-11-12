#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""_phase13_check.py — 阶段 13：设备画像进运行期（`$profile.*`）。

对应 docs/ARCHITECTURE_ROADMAP.md §2.7 的最后一项：
> 画像（config/device_profile.json）只在 **lint 期**被读过；运行期流程仍然写死
> f10 / fread / pi 幅度，换片就得重写一遍 qwf。

本阶段把画像变成**运行期可引用**的一等公民：

  - `services/common/profile.py` 新增 `profile_env(qubit)` / `profile_env_fields()`，
    把某个比特的画像条目折成扁平字典（键名与画像字段同名）；
  - `IrExecutor.__init__` 在 env 就绪后构建 `self.profile`（`context.profile` 优先，
    否则读当前 `env.qubit` 的画像），于是 `.qwf` 里可以写 `$profile.f10`；
  - `validate` 放行 `$profile` 头并对字段名给 `W_PROFILE_UNKNOWN_FIELD`；
  - lint 增 `W_PROFILE_UNKNOWN_FIELD` / `W_PROFILE_MISSING_FIELD` / `W_PROFILE_UNKNOWN_QUBIT`
    与 `I_PROFILE_REFS`；
  - 画像缺失只 warning，不拦执行（离线/换机器仍能跑既有 qwf）。

离线自检（纯本地）：
  1. `profile_env` 的折平语义（只有真有的字段、缺比特返回空、不抛异常）
  2. 执行器解析 `$profile.f10` / `$profile.pi_gate_amp` / 未知字段报错 / context 覆盖
  3. validate：`$profile` 头不再报 E_UNDEFINED_REF；未知字段 → W_PROFILE_UNKNOWN_FIELD
  4. lint：文本 qwf 用 `$profile.f10` 不报错；错字段/错比特有对应诊断
  5. 契约：键名三处一致（profile 字段表 / validate 回退表 / lint 提示）；文档写明用法

用法：D:/anaconda3/envs/lqcs/python.exe _phase13_check.py
"""
from __future__ import annotations

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


from services.common import profile as P  # noqa: E402
from services.workflow_service.dsl import executor as EX  # noqa: E402
from services.workflow_service.dsl import lint as L  # noqa: E402
from services.workflow_service.dsl import parse_qwf as TP  # noqa: E402

import importlib  # noqa: E402

# `dsl/__init__` 把 validate() 函数导出成同名符号，`from dsl import validate` 拿到的是函数，
# 这里要的是模块本身（要读 _PROFILE_FIELDS_FALLBACK）。
V = importlib.import_module("services.workflow_service.dsl.validate")

print("=" * 72)
print("_phase13_check — 阶段 13：设备画像进运行期（$profile.*）")
print("=" * 72)

# ── 1. profile_env 折平语义 ────────────────────────────────────────────────
print("\n[1] profile_env")
FIXED = {
    "version": "9.9.9",
    "updated_at": "2026-11-10T00:00:00Z",
    "qubits": {"qx1": {"f10": 3.5, "fread": 6.7, "pi_gate_amp": 1.25, "xy_dac": "DAC_LQ 1"}},
}
env = P.profile_env("qx1", FIXED)
check("已知比特：折出 f10/fread/pi_gate_amp", env.get("f10") == 3.5 and env.get("fread") == 6.7
      and env.get("pi_gate_amp") == 1.25)
check("已知比特：带上 qubit 与 source", env.get("qubit") == "qx1" and env.get("source") == "device_profile")
check("已知比特：带 version/updated_at", env.get("version") == "9.9.9"
      and env.get("updated_at") == "2026-11-10T00:00:00Z")
check("已知比特：不补默认值（没 f21 就没有该键）", "f21" not in env)
check("字符串字段保留原样", env.get("xy_dac") == "DAC_LQ 1")
check("未知比特 → 空 dict", P.profile_env("q_nope", FIXED) == {})
check("空画像 → 空 dict", P.profile_env("qx1", {"qubits": {}}) == {})
check("None / 非字符串比特 → 空 dict（不抛）",
      P.profile_env(None, FIXED) == {} and P.profile_env(123, FIXED) == {})
check("布尔值不当数值（跳过）", "f10" not in P.profile_env("qx1", {"qubits": {"qx1": {"f10": True}}}))
check("字段表含元信息与关键字段",
      {"qubit", "source", "f10", "pi_gate_amp", "fread"} <= set(P.profile_env_fields()))
check("字段表里没有重复项", len(P.profile_env_fields()) == len(set(P.profile_env_fields())))

# 真实画像：q4ld3 应该在（不存在就跳过这组，不当失败）
real = P.load_profile()
real_has = "q4ld3" in (real.get("qubits") or {})
if real_has:
    renv = P.profile_env("q4ld3")
    check("真实画像：q4ld3 能折出 f10（数值）", isinstance(renv.get("f10"), (int, float)))
    check("真实画像：q4ld3 能折出 pi_gate_amp", isinstance(renv.get("pi_gate_amp"), (int, float)))
else:
    print("  [SKIP] 真实画像里没有 q4ld3（离线环境差异）")

# ── 2. 执行器解析 ─────────────────────────────────────────────────────────
print("\n[2] executor._resolve")
IR_EMPTY = {"ir_version": 1, "id": "t13", "env": {}, "pipeline": []}
ex = EX.IrExecutor(None, dict(IR_EMPTY), {"qubit": "qx1", "profile": dict(env)})
check("context 里的 profile 优先生效", ex._resolve("$profile.f10") == 3.5)
check("裸 $profile 返回整份 dict", isinstance(ex._resolve("$profile"), dict)
      and ex._resolve("$profile").get("qubit") == "qx1")

if real_has:
    ex_real = EX.IrExecutor(None, dict(IR_EMPTY), {"qubit": "q4ld3"})
    check("执行器自己读画像（没给 context.profile）",
          ex_real._resolve("$profile.f10") == P.qubit_entry(real, "q4ld3")["f10"])
    check("$profile.pi_gate_amp 可解析",
          ex_real._resolve("$profile.pi_gate_amp") == P.qubit_entry(real, "q4ld3")["pi_gate_amp"])

try:
    ex._resolve("$profile.no_such_field")
    check("未知字段抛 UndefinedRef", False, "没有抛异常")
except Exception as exc:
    check("未知字段抛 UndefinedRef 且提示可用字段",
          "不在设备画像里" in str(exc) and "f10" in str(exc), repr(exc))

ex_none = EX.IrExecutor(None, dict(IR_EMPTY), {"qubit": "q_missing"})
check("画像里没有该比特时给 warning，不抛",
      any("设备画像里没有 q_missing" in w for w in ex_none.warnings), str(ex_none.warnings))
try:
    ex_none._resolve("$profile.f10")
    check("没有画像时 $profile.* 报清晰错误", False, "没有抛异常")
except Exception as exc:
    check("没有画像时 $profile.* 报清晰错误", "不可用" in str(exc) and "q_missing" in str(exc), repr(exc))

ex_env_profile = EX.IrExecutor(
    None, {"ir_version": 1, "id": "t13b", "env": {"profile": {"f10": 9.9}}, "pipeline": []}, {})
check("IR 的 env.profile 也能被引用", ex_env_profile._resolve("$profile.f10") == 9.9)

# ── 3. validate ───────────────────────────────────────────────────────────
print("\n[3] validate")


def make_ir(args, qubit="q4ld3"):
    return {
        "ir_version": 1,
        "id": "t13v",
        "env": {"qubit": qubit},
        "budget": {"maxWallSeconds": 60, "maxWrites": 4, "maxDatasets": 1},
        "pipeline": [{"op": "measure", "fn": "sq.iqraw", "qubit": "$env.qubit",
                      "args": args, "as": "c1", "source": {"line": 1}}],
    }


def codes(diags):
    return [d.get("code") for d in diags]


_norm, diags = V.validate(make_ir({"freq": "$profile.f10"}), require_writes=False)
check("$profile.f10 不再误报 E_UNDEFINED_REF", "E_UNDEFINED_REF" not in codes(diags), str(codes(diags)))
check("$profile.f10 没有任何 error 级诊断",
      not [d for d in diags if d.get("level") == "error"], str(diags))

_norm, diags = V.validate(make_ir({"freq": "$profile.bogus_field"}), require_writes=False)
check("未知画像字段 → W_PROFILE_UNKNOWN_FIELD（warning 不拦）",
      "W_PROFILE_UNKNOWN_FIELD" in codes(diags)
      and not [d for d in diags if d.get("level") == "error"], str(codes(diags)))

_norm, diags = V.validate(make_ir({"freq": "$env.qubit"}), require_writes=False)
check("$env.qubit 行为不变", "E_UNDEFINED_REF" not in codes(diags))

_norm, diags = V.validate(make_ir({"freq": "$nope.x"}), require_writes=False)
check("其它未绑定引用仍报 E_UNDEFINED_REF", "E_UNDEFINED_REF" in codes(diags))
check("validate 的画像字段回退表与 profile 表一致",
      set(V._PROFILE_FIELDS_FALLBACK) >= set(P.profile_env_fields()))

# ── 4. lint ───────────────────────────────────────────────────────────────
print("\n[4] lint")

TEXT_OK = """
workflow profile_ref {
  env { qubit "q4ld3" }
  budget { max_wall 15m  max_writes 4  max_datasets 1 }
  measure as c1 {
    fn    sq.iqraw
    qubit $env.qubit
    args  { freq $profile.f10 }
  }
  say "f10 from profile"
}
"""
rep = L.lint_text(TEXT_OK)
check("用 $profile.f10 的 qwf：无 error", (rep.get("counts") or {}).get("error") == 0,
      str(rep.get("errors"))[:200])
check("合法字段不报 W_PROFILE_UNKNOWN_FIELD",
      "W_PROFILE_UNKNOWN_FIELD" not in [w.get("code") for w in (rep.get("warnings") or [])])

TEXT_BAD_FIELD = TEXT_OK.replace("$profile.f10", "$profile.bogus_field")
rep_bad = L.lint_text(TEXT_BAD_FIELD)
warn_codes = [w.get("code") for w in (rep_bad.get("warnings") or [])]
check("错字段 → W_PROFILE_UNKNOWN_FIELD", "W_PROFILE_UNKNOWN_FIELD" in warn_codes, str(warn_codes))

TEXT_BAD_QUBIT = TEXT_OK.replace('"q4ld3"', '"q_not_in_profile"')
rep_q = L.lint_text(TEXT_BAD_QUBIT)
q_codes = [w.get("code") for w in (rep_q.get("warnings") or [])]
check("画像里没有的比特 → W_PROFILE_UNKNOWN_QUBIT", "W_PROFILE_UNKNOWN_QUBIT" in q_codes, str(q_codes))

IR_OK = TP(TEXT_OK)
check("文本 DSL 能解析 $profile 引用", isinstance(IR_OK, dict)
      and "$profile.f10" in str(IR_OK.get("pipeline")))

# 产品范例回归：5 个受版本控制的 qwf 仍然 0 error
import glob  # noqa: E402
sample_dir = os.path.join(SERVER, "data", "workflows", "examples")
bad = []
for path in sorted(glob.glob(os.path.join(sample_dir, "*.qwf"))):
    with open(path, "r", encoding="utf-8") as fh:
        r = L.lint_text(fh.read())
    if r.get("errors"):
        bad.append((os.path.basename(path), [e.get("code") for e in r["errors"]]))
check("既有产品 qwf 仍 0 error", not bad, str(bad))

# ── 5. 契约与文档 ─────────────────────────────────────────────────────────
print("\n[5] 契约/文档")
check("executor 有 _walk_profile", hasattr(EX.IrExecutor, "_walk_profile"))
check("registry 桥接导出 profile_env", "profile_env" in open(
    os.path.join(SERVER, "services", "workflow_service", "dsl", "registry.py"),
    encoding="utf-8").read())
check("lint 调用了 _profile_ref_checks", "_profile_ref_checks(rep" in open(
    os.path.join(SERVER, "services", "workflow_service", "dsl", "lint.py"),
    encoding="utf-8").read())

doc_ops = os.path.join(ROOT, "docs", "OPERATIONS.md")
with open(doc_ops, "r", encoding="utf-8") as fh:
    ops = fh.read()
check("OPERATIONS 写了 $profile 用法", "$profile.f10" in ops and "profile_env" in ops)
doc_road = os.path.join(ROOT, "docs", "ARCHITECTURE_ROADMAP.md")
with open(doc_road, "r", encoding="utf-8") as fh:
    road = fh.read()
check("ROADMAP §2.7 标了阶段 13 落地", "阶段 13" in road and "$profile" in road)

print("=" * 72)
print(f"PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for name in FAILED:
        print("  - " + name)
print("=" * 72)
sys.exit(0 if FAIL == 0 else 1)
