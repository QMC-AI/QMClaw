"""阶段 2 第二批离线自检：Metric Registry 接入 + 比特状态机 + /qubit/assess 端点接线。

不连真机、不起服务；纯函数与 AST 静态检查。
运行：python _phase2b_check.py   （退出码 1 表示有 FAIL）
"""
from __future__ import annotations

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

PASS = 0
FAIL = 0
WARN = 0
FAILS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s" % name)
    else:
        FAIL += 1
        FAILS.append(name)
        print("  FAIL  %s  %s" % (name, detail))


def warn(name, cond, detail=""):
    global WARN
    if not cond:
        WARN += 1
        print("  WARN  %s  %s" % (name, detail))
    else:
        print("  PASS  %s" % name)


def section(title):
    print("\n== %s ==" % title)


# ---------------------------------------------------------------- metrics
section("metrics（Metric Registry 接入）")
from services.common import metrics as M  # noqa: E402

crit = M.load_criteria()
check("lab_criteria 可读且有 version", bool(crit.get("version")), crit.get("version"))
check("metrics 定义含 contrast/t1/t2_star/xeb/dmu_sig/s21_dip",
      all(k in crit.get("metrics", {}) for k in
          ("contrast", "t1", "t2_star", "xeb", "dmu_sig", "s21_dip")))

r = M.evaluate_contrast(0.765)
check("contrast 0.765 → good", r["verdict"] == "good" and r["ok"], r)
r = M.evaluate_contrast(0.28)
check("contrast 0.28 → weak_but_usable", r["verdict"] == "weak_but_usable" and r["ok"], r)
r = M.evaluate_contrast(0.05)
check("contrast 0.05 → unusable", r["verdict"] == "unusable" and not r["ok"], r)
check("contrast 判定带 next_step", bool(M.evaluate_contrast(0.05)["next_step"]))
check("contrast_from_p1 = |P1X-P1I|", abs(M.contrast_from_p1(0.1, 0.9) - 0.8) < 1e-9)
check("evaluate 分发 contrast", M.evaluate("contrast", 0.5)["metric"] == "contrast")

check("t1 72us 达标", M.evaluate_t1(72.2)["ok"])
check("t1 5us 不达标", not M.evaluate_t1(5.0)["ok"])
check("t2_star 6.58us 达标", M.evaluate_t2_star(6.58)["ok"])
check("t2_star 0.2us 不达标", not M.evaluate_t2_star(0.2)["ok"])
xe = M.evaluate_xeb(p=0.9965, gate_fidelity=0.9973)
check("xeb p=0.9965/gf=0.9973 → good", xe["ok"], xe)
xe = M.evaluate_xeb(p=0.95, gate_fidelity=0.99)
check("xeb p=0.95 → poor 且提示", not xe["ok"] and bool(xe["next_step"]), xe)
check("dmu_sig 2.05 → alive", M.evaluate_dmu_sig(2.05)["verdict"] == "alive")
check("dmu_sig 0.08 → no_response", M.evaluate_dmu_sig(0.08)["verdict"] == "no_response")

try:
    M.evaluate("not_a_metric", 1)
    check("未知指标抛 KeyError", False)
except KeyError:
    check("未知指标抛 KeyError", True)

allr = M.evaluate_all({"contrast": 0.765, "t1_us": 72.2, "unknown_x": 1})
check("evaluate_all 跳过未知指标", "unknown_x" not in allr and "contrast" in allr)
check("阈值来自真源文件而非硬编码",
      M.evaluate_contrast(0.2)["thresholds"]["unusable"]
      == crit["metrics"]["contrast"]["thresholds"]["unusable"])

# ---------------------------------------------------------------- 状态机
section("qubit_state（比特状态机）")
from services.common import qubit_state as QS  # noqa: E402

check("状态序列正确",
      QS.STATES == ["unknown", "freq_locked", "amp_calibrated",
                    "state_discriminated", "coherence_measured", "characterized"])
check("state_table 含全部状态描述",
      all(s in QS.state_table()["descriptions"] for s in QS.STATES))

st = QS.derive_state({})
check("无证据 → unknown", st["state"] == "unknown", st)
check("unknown 给出下一步建议", bool(st["next_action"]))
st = QS.derive_state({"freq_locked": True})
check("freq_locked → freq_locked", st["state"] == "freq_locked", st)
st = QS.derive_state({"freq_locked": True, "amp_calibrated": True})
check("amp_calibrated → amp_calibrated", st["state"] == "amp_calibrated", st)
st = QS.derive_state({"freq_locked": True, "contrast": 0.28})
check("弱对比度 → state_discriminated（≥0.2 门槛）",
      st["state"] == "state_discriminated", st)
st = QS.derive_state({"contrast": 0.05})
check("对比度 0.05（不可用）→ unknown（既没锁频也没标幅度）",
      st["state"] == "unknown", st)
st = QS.derive_state({"contrast": 0.05, "freq_locked": True})
check("对比度 0.05 → freq_locked", st["state"] == "freq_locked", st)
st = QS.derive_state({"contrast": 0.765, "t1_us": 72.2, "t2_star_us": 6.58})
check("T1+T2* 达标 → coherence_measured", st["state"] == "coherence_measured", st)
st = QS.derive_state({"contrast": 0.765, "t1_us": 72.2, "t2_star_us": 6.58,
                       "xeb_p": 0.9965, "xeb_gate_fidelity": 0.9973})
check("全证据 → characterized", st["state"] == "characterized", st)
check("状态机带 reasons 说明", bool(st["reasons"]))
check("状态机带 missing 缺口", isinstance(st["missing"], list))
check("状态机内嵌指标判定", "contrast" in st["metrics"])
st = QS.derive_state({"freq_locked": "true"})
check("字符串布尔可识别", st["state"] == "freq_locked", st)

# ---------------------------------------------------------------- server 接线
section("server.py 接线（/qubit/assess）")
srv_path = os.path.join(SERVER, "services", "quantum_service", "server.py")
src = open(srv_path, "r", encoding="utf-8").read()
tree = ast.parse(src)
funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
check("_handle_qubit_assess 存在", "_handle_qubit_assess" in funcs)
check("路由 /qubit/assess 存在", '"/qubit/assess"' in src)
check("路由注册在 /qubit/runs 之后", src.index('"/qubit/assess"') > src.index('"/qubit/runs"'))
check("assess 使用 metrics.evaluate_all", "evaluate_all" in src)
check("assess 使用 qubit_state.derive_state", "derive_state" in src)
check("assess 不含实验下发（无 sq. 调用）", "sq." not in src.split("def _handle_qubit_assess")[1].split("def _handle_qubit_capabilities")[0])
check("assess 出错走 error_envelope", "error_envelope" in src.split("def _handle_qubit_assess")[1].split("def _handle_qubit_capabilities")[0])

# ---------------------------------------------------------------- 端点离线行为
section("/qubit/assess 处理器级行为（不连 LabRAD）")
from services.quantum_service.server import QuantumService  # noqa: E402

svc = QuantumService(port=3999)
svc._ensure_connected = lambda: False  # 测试"未连接"分支，避免真连 LabRAD
try:
    out = svc._handle_qubit_assess({"name": "q2ld3", "contrast": 0.765,
                                    "t1_us": 72.2, "t2_star_us": 6.58})
    check("assess 返回 success", out.get("success") is True, out)
    check("assess 给出 state", out.get("state", {}).get("state") == "coherence_measured", out)
    check("assess 给出 metrics", "contrast" in out.get("metrics", {}))
    out = svc._handle_qubit_assess({"name": "q7ld3", "evidence": {"contrast": 0.0283}})
    check("assess 支持 evidence 包裹", out.get("success") is True
          and out["state"]["state"] in ("unknown", "freq_locked"), out)
    out = svc._handle_qubit_assess({"name": "q1ld3"})
    check("assess 空证据 → unknown", out.get("success") is True
          and out["state"]["state"] == "unknown", out)
    out = svc._handle_qubit_assess({"name": "q1ld3", "evidence": "oops"})
    check("assess 非法 evidence → 错误信封",
          out.get("success") is False and out.get("error_code") == "validation_error", out)
finally:
    try:
        svc._server_close()
    except Exception:
        pass

# ---------------------------------------------------------------- 汇总
print("\n_phase2b_check: PASS=%d FAIL=%d WARN=%d" % (PASS, FAIL, WARN))
if FAILS:
    print("失败项：%s" % ", ".join(FAILS))
sys.exit(1 if FAIL else 0)
