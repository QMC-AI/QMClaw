"""_phase17_check.py - 阶段 17 自检：服务进程组（端口税，路线图 §3 #6）

验证目标：
1. `services/common/service_groups.py` 的端口表与 `scripts/start_all_services.py`
   （以及 `config/services.json`，若存在）完全一致——不允许两处各抄一份。
2. 组定义自洽：端口不冲突、成员都已登记、可合并的组里没有"有阻塞理由"的服务。
3. 每个不能合并的服务都必须写明理由（防止后人静默塞进 core 组）。
4. `scripts/start_group.py` 只从定义模块取端口/成员，脚本里没有硬编码端口。
5. 启动器行为：`--dry-run` 不启动进程、未知组退出码 2、`lab` 组拒绝同进程启动。
6. 文档在位：`docs/OPERATIONS.md` §3.20、路线图 §3 #6 注记。

全程离线：不启动任何服务、不连设备。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "Agentic Workflow" / "qmclaw-server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append(f"{name} {detail}".strip())
        print(f"[FAIL] {name} {detail}".rstrip())


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


from services.common import service_groups as sg  # noqa: E402


def services_from_starter() -> dict:
    """从 start_all_services.py 的 SERVICES 字面量里取 {名字: 端口}。"""
    tree = ast.parse(read(SERVER / "scripts" / "start_all_services.py"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "SERVICES" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        out = {}
        for key, value in zip(node.value.keys, node.value.values):
            if not isinstance(key, ast.Constant) or not isinstance(value, ast.Dict):
                continue
            port = None
            for k, v in zip(value.keys, value.values):
                if isinstance(k, ast.Constant) and k.value == "port" and isinstance(v, ast.Constant):
                    port = v.value
            if port is not None:
                out[key.value] = port
        return out
    return {}


# ── 1 端口表一致 ─────────────────────────────────────────────────────────────
starter_ports = services_from_starter()
check("能解析出 start_all_services.py 的服务表", bool(starter_ports), f"got={len(starter_ports)}")
check("定义模块端口表与启动脚本一致", starter_ports == sg.SERVICE_PORTS,
      f"starter={starter_ports} module={sg.SERVICE_PORTS}")

config_path = SERVER / "config" / "services.json"
if config_path.exists():
    try:
        cfg_services = json.loads(read(config_path)).get("services", {})
    except Exception:
        cfg_services = {}
    cfg_ports = {k: v.get("port") for k, v in cfg_services.items() if isinstance(v, dict)}
    # config/services.json 里没有登记的服务（如 qubitclient）不算漂移：
    # 它是脚本启动清单的子集，只校验两边都有的部分。
    shared = {k: cfg_ports[k] for k in cfg_ports if k in sg.SERVICE_PORTS}
    drift = {k: (shared[k], sg.SERVICE_PORTS[k]) for k in shared
             if shared[k] != sg.SERVICE_PORTS[k]}
    check("config/services.json 端口与定义一致", not drift, str(drift)[:200])
    check("config/services.json 里没有未登记的多余服务",
          not (set(cfg_ports) - set(sg.SERVICE_PORTS)), str(set(cfg_ports) - set(sg.SERVICE_PORTS)))
else:
    check("config/services.json 不存在时跳过（不算失败）", True)

# ── 2 端口唯一 ───────────────────────────────────────────────────────────────
check("端口无重复", len(set(sg.SERVICE_PORTS.values())) == len(sg.SERVICE_PORTS))
check("known_services 按端口升序", sg.known_services() ==
      sorted(sg.SERVICE_PORTS, key=lambda n: sg.SERVICE_PORTS[n]))
check("port_of 未登记返回 None", sg.port_of("nope") is None)
check("port_of 已登记返回值", sg.port_of("workflow") == 3008)

# ── 3 组定义自洽 ─────────────────────────────────────────────────────────────
check("核心组存在且为同进程", sg.GROUPS.get("core", {}).get("merged") is True)
check("核心组成员是 workflow + task_queue",
      sg.group_members("core") == ["workflow", "task_queue"])
check("核心组端口无冲突", sg.plan_group("core")["conflicts"] == [])
core_plan = sg.plan_group("core")
check("核心组计划标 merged=True", core_plan["merged"] is True)
check("核心组计划端口正确",
      core_plan["ports"] == {"workflow": 3008, "task_queue": 3009})
check("核心组成员都有入口点",
      all(m in sg.SERVICE_ENTRYPOINTS for m in core_plan["members"]))
check("核心组成员都不是阻塞服务",
      all(sg.is_mergeable(m) for m in core_plan["members"]))

lab_plan = sg.plan_group("lab")
check("lab 组为独立进程", lab_plan["merged"] is False)
check("lab 组给出阻塞理由", set(lab_plan["blockers"]) == {"quantum", "analysis"})
check("lab 组端口正确", lab_plan["ports"] == {"quantum": 3003, "analysis": 3004})

check("未知组返回空成员", sg.group_members("nope") == [])
check("未知组计划 known=False", sg.plan_group("nope")["known"] is False)
check("组名大小写/空白容错", sg.group_members("  CORE ") == ["workflow", "task_queue"])

# ── 4 每个服务要么可合并、要么写明理由 ───────────────────────────────────────
silent = [name for name in sg.SERVICE_PORTS
          if not sg.is_mergeable(name) and name not in sg.MERGE_BLOCKERS]
check("不可合并的服务都写了理由", not silent, str(silent))
check("阻塞理由不为空串", all(str(v).strip() for v in sg.MERGE_BLOCKERS.values()))
check("未登记的组不会引用阻塞服务", all(
    all(m in sg.SERVICE_PORTS for m in (g.get("members") or []))
    for g in sg.GROUPS.values()))
check("describe_groups 覆盖所有组", {i["group"] for i in sg.describe_groups()} == set(sg.GROUPS))

# ── 5 启动器只从定义取事实 ───────────────────────────────────────────────────
group_script = read(SERVER / "scripts" / "start_group.py")


def _hardcoded_ports(text: str) -> set:
    """代码里（不算文档字符串/注释）直接写死的端口字面量。"""
    ports = set(sg.SERVICE_PORTS.values())
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ports
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool) and id(node) not in docstrings:
            if node.value in ports:
                found.add(node.value)
    return found

check("启动器 import 了定义模块", "services.common.service_groups" in group_script)
check("启动器用 plan_group 决策", "plan_group(" in group_script)
check("启动器用登记入口点构造服务", "SERVICE_ENTRYPOINTS" in group_script)
check("启动器没有硬编码端口", not _hardcoded_ports(group_script),
      "代码里出现了端口字面量（文档字符串/注释里说明端口不算）")

# 行为：直接加载启动器模块（不执行 main），调用 run_group 的干跑路径
spec = importlib.util.spec_from_file_location("_p17_start_group", SERVER / "scripts" / "start_group.py")
start_group = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(start_group)
    loaded = True
except Exception as exc:  # pragma: no cover
    loaded = False
    print("载入 start_group.py 失败：", exc)
check("start_group.py 可作为模块载入", loaded)

if loaded:
    check("dry-run core 成功", start_group.run_group("core", dry_run=True) == 0)
    check("dry-run lab 成功（仅打印计划）", start_group.run_group("lab", dry_run=True) == 0)
    check("未知组退出码 2", start_group.run_group("nope", dry_run=True) == 2)
    check("lab 组拒绝同进程启动（退出码 2）", start_group.run_group("lab", dry_run=False) == 2)

# ── 6 文档在位 ───────────────────────────────────────────────────────────────
ops = read(ROOT / "docs" / "OPERATIONS.md")
check("OPERATIONS 有 §3.20", "### 3.20" in ops)
check("OPERATIONS 提到 start_group.py", "start_group.py" in ops)
check("OPERATIONS §4 表含 _phase17_check.py", "_phase17_check.py" in ops)
roadmap = read(ROOT / "docs" / "ARCHITECTURE_ROADMAP.md")
check("路线图 §3 #6 有阶段 17 注记", "阶段 17" in roadmap and "service_groups" in roadmap)

print(f"\n_phase17_check: PASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for item in FAILED:
        print("  -", item)
sys.exit(0 if FAIL == 0 else 1)
