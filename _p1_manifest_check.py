"""P1 收尾②自检：写清单模块 manifest.py + bounds 块（文本 DSL）+ registry_bounds.json 默认表。

纯离线，不连服务、不连硬件。
    ast.parse → manifest 枚举（写集合/次数/幅度）→ bounds 优先级 → 文本 DSL bounds 往返
    → validate 接入（E_MANIFEST_UNBOUNDED 为 warning）→ 既有 .qwf 回归
"""
import ast
import glob
import os
import sys

ROOT = r"D:/qmclaw/Agentic Workflow/qmclaw-server/services/workflow_service"
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

from dsl import normalize, validate  # noqa: E402
from dsl import parse_qwf  # noqa: E402
from dsl.manifest import collect_manifest, load_config_bounds, manifest_errors  # noqa: E402
from dsl.textdsl.printer import ir_to_qwf  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    mark = "OK " if cond else "FAIL"
    if not cond:
        FAIL.append(name)
    print(f"[{mark}] {name} {extra}")


def ir_of(pipeline, **extra):
    ir = {"ir_version": 1, "id": "mtest", "pipeline": pipeline}
    ir.update(extra)
    return ir


def measure(fn, qubit="q4ld3", **params):
    st = {"op": "measure", "fn": fn, "qubit": qubit}
    if params:
        st["params"] = params
    return st


# ── 1. 语法检查 ─────────────────────────────────────────────────
for rel in ["manifest.py", "validate.py", "textdsl/parser.py", "textdsl/printer.py"]:
    p = os.path.join(ROOT, "dsl", rel)
    try:
        ast.parse(open(p, encoding="utf-8").read(), filename=p)
        check(f"ast.parse {rel}", True)
    except SyntaxError as e:
        check(f"ast.parse {rel}", False, str(e))

# ── 2. 默认上界表 ───────────────────────────────────────────────
cfg = load_config_bounds()
check("config/registry_bounds.json 读到", len(cfg) > 0, f"{len(cfg)} 键")
check("默认表含 f10", "f10" in cfg, repr(cfg.get("f10")))
check("默认表不含 timing.*", not any(k.startswith("timing.") for k in cfg))

# ── 3. 写集合枚举：iqraw（派生键）───────────────────────────────
man = collect_manifest(ir_of([measure("sq.iqraw")]))
keys = {w["key"] for w in man["writes"]}
check("iqraw 写集合 = discriminator.*",
      keys == {"discriminator.center0", "discriminator.center1",
               "discriminator.radius0", "discriminator.method"}, str(sorted(keys)))
check("iqraw 全部标记为 derived", all(w["derived"] for w in man["writes"]))
check("iqraw 派生键可代理（无需人批）",
      all(w["delegable"] for w in man["writes"]))
check("iqraw 无无界键", man["unbounded"] == [], str(man["unbounded"]))

# ── 4. 写集合枚举：pidf（f10/f21，默认表给上界）────────────────
man = collect_manifest(ir_of([measure("sq.pidf")]))
by_key = {w["key"]: w for w in man["writes"]}
check("pidf 写集合 = f10+f21", set(by_key) == {"f10", "f21"}, str(sorted(by_key)))
check("pidf f10 幅度来自 config", by_key["f10"]["bound_source"] == "config",
      repr(by_key["f10"]["bound_source"]))
check("pidf f10 可代理", by_key["f10"]["delegable"] is True)
check("pidf 无无界键", man["unbounded"] == [], str(man["unbounded"]))

# ── 5. 写集合枚举：set_pi（always 模式）────────────────────────
man = collect_manifest(ir_of([measure("sq.set_pi")]))
check("set_pi 写集合 = PiGate.amp/alpha",
      {w["key"] for w in man["writes"]} == {"PiGate.amp", "PiGate.alpha"},
      str([w["key"] for w in man["writes"]]))
man_half = collect_manifest(ir_of([measure("sq.set_pi", gate="X/2")]))
check("set_pi gate=X/2 写 PiHalf",
      {w["key"] for w in man_half["writes"]} == {"PiHalf.amp", "PiHalf.alpha"},
      str([w["key"] for w in man_half["writes"]]))

# ── 6. 不写的实验（mode=none）──────────────────────────────────
man = collect_manifest(ir_of([measure("sq.t1"), measure("sq.xeb")]))
check("t1/xeb 不写任何键", man["writes"] == [], str(man["writes"]))

# ── 7. update=False 不算写 ─────────────────────────────────────
man = collect_manifest(ir_of([measure("sq.pidf", update=False)]))
check("pidf update=False 不写", man["writes"] == [], str(man["writes"]))

# ── 8. repeat 次数放大 ─────────────────────────────────────────
man = collect_manifest(ir_of([
    {"op": "repeat", "maxRounds": 3, "body": [measure("sq.pidf")]},
]))
f10 = {w["key"]: w for w in man["writes"]}["f10"]
check("repeat×3 次数放大", f10["max_count"] == 3, repr(f10["max_count"]))
check("repeat 轮数记录", man["rounds"] == 3, repr(man["rounds"]))

# ── 9. if/else 并集 ───────────────────────────────────────────
man = collect_manifest(ir_of([
    {"op": "if", "cond": "$x",
     "then": [measure("sq.pidf")],
     "else": [measure("sq.set_pi")]},
]))
check("if/else 写集合取并集",
      {w["key"] for w in man["writes"]} == {"f10", "f21", "PiGate.amp", "PiGate.alpha"},
      str(sorted(w["key"] for w in man["writes"])))

# ── 10. IR bounds 优先于 config ────────────────────────────────
ir_b = ir_of([measure("sq.pidf")], bounds={"f10": 0.001})
man = collect_manifest(ir_b)
by_key = {w["key"]: w for w in man["writes"]}
check("IR bounds 覆盖 config", by_key["f10"]["bound"] == 0.001 and
      by_key["f10"]["bound_source"] == "ir",
      repr((by_key["f10"]["bound"], by_key["f10"]["bound_source"])))
check("IR 未声明的键仍走 config", by_key["f21"]["bound_source"] == "config",
      repr(by_key["f21"]["bound_source"]))

# ── 11. 无上界 ⇒ 不可代理 + E_MANIFEST_UNBOUNDED ──────────────
ir_u = ir_of([measure("sq.s21")], bounds={})
cfg_backup = load_config_bounds
import dsl.manifest as _mf  # noqa: E402
_mf.load_config_bounds = lambda root=None: {}
try:
    man = collect_manifest(ir_u)
    check("无上界的键进 unbounded", man["unbounded"] == ["fread"], str(man["unbounded"]))
    check("无上界的键不可代理",
          all(not w["delegable"] for w in man["writes"] if w["key"] == "fread"))
    errs = manifest_errors(ir_u)
    check("E_MANIFEST_UNBOUNDED 被报出",
          any(c == "E_MANIFEST_UNBOUNDED" for c, _m, _p in errs), str(errs))
finally:
    _mf.load_config_bounds = cfg_backup

# ── 12. timing.* 永不可代理 ────────────────────────────────────
ir_t = ir_of([measure("sq.test_timing_xyz")], bounds={"timing.xy": 1.0})
man = collect_manifest(ir_t)
by_key = {w["key"]: w for w in man["writes"]}
check("timing.xy 标为 never_delegable", by_key["timing.xy"]["never_delegable"] is True)
check("timing.xy 即使有上界也不可代理",
      by_key["timing.xy"]["delegable"] is False)
check("timing.* 进 never_delegable 列表",
      "timing.xy" in man["never_delegable"], str(man["never_delegable"]))

# ── 13. 未知函数 ⇒ 写集合不可枚举 ──────────────────────────────
man = collect_manifest(ir_of([measure("sq.some_unknown_exp")]))
check("未知函数进 unknown_fns", man["unknown_fns"] == ["sq.some_unknown_exp"],
      str(man["unknown_fns"]))
check("未知函数 ⇒ dynamic", man["dynamic"] is True)

# ── 14. 文本 DSL bounds 块往返 ─────────────────────────────────
BOUNDS_QWF = """workflow bounds_test {
  meta { name "bounds_test" }
  env { qubit "q4ld3" }
  budget { max_wall 300  max_writes 3 }
  bounds {
    f10 0.01
    PiGate.amp 0.2
  }
  measure as c1 {
    fn      sq.pidf
    qubit   $env.qubit
    timeout 60
  }
}
"""
try:
    ir = parse_qwf(BOUNDS_QWF)
    b = ir.get("bounds") or {}
    check("bounds 块解析", b == {"f10": 0.01, "PiGate.amp": 0.2}, repr(b))
    text = ir_to_qwf(ir)
    check("printer 输出 bounds 块", "bounds {" in text,
          repr([l for l in text.splitlines() if "bounds" in l]))
    check("printer 保留带点键名", "PiGate.amp 0.2" in text, repr(text))
    ir2 = parse_qwf(text)
    check("bounds 往返一致", (ir2.get("bounds") or {}) == b, repr(ir2.get("bounds")))
except Exception as e:  # noqa: BLE001
    check("bounds 文本 DSL", False, repr(e))

# ── 15. validate 接入（warning 不拦）──────────────────────────
try:
    ir = parse_qwf(BOUNDS_QWF)
    out, diags = validate(ir)
    errs = [d for d in diags if d.get("level") != "warning"]
    check("validate 无 error（manifest 只报 warning）", not errs,
          str([(d.get("code"), d.get("message")) for d in errs]))
except Exception as e:  # noqa: BLE001
    check("validate 接入", False, repr(e))

# ── 16. 既有 .qwf 回归 ─────────────────────────────────────────
for f in sorted(glob.glob(r"D:/qmclaw/*.qwf")):
    try:
        r = parse_qwf(open(f, encoding="utf-8").read())
        out, diags = validate(r)
        errs = [d for d in diags if d.get("level") != "warning"]
        check(f"回归 {os.path.basename(f)}", not errs,
              str([(d.get("code"), d.get("message")) for d in errs]))
    except Exception as e:  # noqa: BLE001
        check(f"回归 {os.path.basename(f)}", False, repr(e))

print("\n=== 汇总 ===")
print(f"FAIL {len(FAIL)}" + (f": {FAIL}" if FAIL else "（全部通过）"))
sys.exit(1 if FAIL else 0)
