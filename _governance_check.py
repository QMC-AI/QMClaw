"""_governance_check.py — 阶段 0 离线自检（不连 LabRAD、不碰真机）

覆盖：
1. services.common.governance.evaluate_require_task 的闸门矩阵（纯函数）
2. services.common.config.get_actuation_flags 对 require_task 的读取（配置项 + 环境变量）
3. config/lab_criteria.json 结构与关键阈值（指标唯一真源）
4. docs/ARCHITECTURE_ROADMAP.md 存在且关键章节齐全
5. 修改过的源码 AST 可解析、:3003 已接上闸门

用法：python _governance_check.py
"""
import ast
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
sys.path.insert(0, str(SERVER))

PASS, FAIL, WARN = 0, 0, 0
LOG = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        LOG.append(f"PASS  {name}")
    else:
        FAIL += 1
        LOG.append(f"FAIL  {name}  {detail}")


def warn(name, detail=""):
    global WARN
    WARN += 1
    LOG.append(f"WARN  {name}  {detail}")


# ---------- 1. 闸门矩阵 ----------
from services.common.governance import evaluate_require_task  # noqa: E402

flags_off = {"require_task": False}
flags_on = {"require_task": True}

check("gate/off/无task_id 放行", evaluate_require_task({"code": "x"}, flags_off) is None)
check("gate/off/有task_id 放行", evaluate_require_task({"code": "x", "task_id": "t-1"}, flags_off) is None)
r = evaluate_require_task({"code": "x"}, flags_on)
check("gate/on/无task_id 拦截", r is not None and r.get("status") == "blocked", str(r))
check("gate/on/reason_code", bool(r) and r.get("reason_code") == "task_required")
check("gate/on/有task_id 放行", evaluate_require_task({"code": "x", "task_id": "t-1"}, flags_on) is None)
check("gate/on/空白task_id 拦截", evaluate_require_task({"code": "x", "task_id": "  "}, flags_on) is not None)
check("gate/缺flags 放行", evaluate_require_task({"code": "x"}, None) is None)
check("gate/缺flags键 放行", evaluate_require_task({"code": "x"}, {}) is None)
check("gate/None data 拦截", evaluate_require_task(None, flags_on) is not None)

# ---------- 2. 闸门开关读取 ----------
from services.common.config import get_actuation_flags  # noqa: E402

f = get_actuation_flags("quantum_service")
check("flags/含 require_task 键", "require_task" in f, str(f))
check("flags/默认不阻断", f.get("require_task") is False, str(f))

os.environ["QMCLAW_REQUIRE_TASK"] = "1"
f2 = get_actuation_flags("quantum_service")
check("flags/环境变量可开", f2.get("require_task") is True, str(f2))
os.environ["QMCLAW_REQUIRE_TASK"] = "0"
f3 = get_actuation_flags("quantum_service")
check("flags/环境变量可关", f3.get("require_task") is False, str(f3))
del os.environ["QMCLAW_REQUIRE_TASK"]

cfg_path = SERVER / "config" / "fallback_config.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
check("cfg/quantum_service.require_task 存在",
      "require_task" in cfg["quantum_service"]["actuation"], str(cfg["quantum_service"]["actuation"]))
check("cfg/默认 require_task=false", cfg["quantum_service"]["actuation"].get("require_task") is False)

# ---------- 3. 指标唯一真源 ----------
crit_path = SERVER / "config" / "lab_criteria.json"
check("lab_criteria/存在", crit_path.exists())
if crit_path.exists():
    crit = json.loads(crit_path.read_text(encoding="utf-8"))
    metrics = crit.get("metrics") or {}
    for key in ("contrast", "t1", "t2_star", "xeb", "s21_dip", "dmu_sig"):
        check(f"lab_criteria/含指标 {key}", key in metrics)
    thr = (metrics.get("contrast") or {}).get("thresholds") or {}
    check("lab_criteria/contrast 阈值三档",
          thr.get("unusable") == 0.2 and thr.get("weak_but_usable") == 0.4 and thr.get("good") == 0.7, str(thr))
    facts = crit.get("registry_facts") or {}
    check("lab_criteria/fread 逐比特事实已记录", facts.get("fread_is_per_qubit", {}).get("value") is True)
    check("lab_criteria/厂商坑已记录", len((crit.get("capabilities") or {}).get("vendor_pitfalls") or []) >= 5)

# ---------- 4. 路线图文档 ----------
road = ROOT / "docs" / "ARCHITECTURE_ROADMAP.md"
check("roadmap/存在", road.exists())
if road.exists():
    text = road.read_text(encoding="utf-8")
    for section in ("单写者", "Qubit Port", "RunRecord", "Metric Registry", "在线升级协议",
                    "铁律", "阶段划分"):
        check(f"roadmap/含「{section}」", section in text)
    check("roadmap/含 require_task 清单", "require_task" in text)

# ---------- 5. 源码可解析 + 已接线 ----------
for rel in ("services/quantum_service/server.py", "services/common/config.py",
            "services/common/governance.py"):
    p = SERVER / rel
    ok = True
    err = ""
    try:
        ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:
        ok = False
        err = str(e)
    check(f"ast/{rel}", ok, err)

srv = (SERVER / "services" / "quantum_service" / "server.py").read_text(encoding="utf-8")
check("server/已接 evaluate_require_task", "evaluate_require_task" in srv)
check("server/status 暴露 require_task", '"require_task"' in srv)

# ---------- 输出 ----------
summary = "\n".join(LOG)
print(summary)
print(f"\nPASS={PASS} FAIL={FAIL} WARN={WARN}")
raise SystemExit(1 if FAIL else 0)
