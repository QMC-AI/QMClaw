"""D2 检查：quantum_service 的只读 registry 端点 + reg_snapshot.py 已改成 HTTP 客户端。

两部分：
  [1] 静态检查（不依赖服务在跑）
      - :3003 源码里有 `/qubit/registry` 路由与 handler
      - reg_snapshot.py 不再 import labrad_connect（服务边界收口）
      - reg_snapshot.py 走 /qubit/registry + /qubit/set_params
  [2] HTTP 检查（服务在跑时）
      - POST :3003 /qubit/registry {name: q2ld3} 返回 registry，且含 timing.* 键
      - 服务未重启时给出明确提示（而不是笼统 FAIL）

跑法：
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
        D:/anaconda3/envs/lqcs/python.exe D:/qmclaw/_registry_ep_check.py
"""
import ast
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server", "services",
                      "quantum_service", "server.py")
SNAP = os.path.join(ROOT, "reg_snapshot.py")
BASE = os.environ.get("QMCLAW_QUANTUM_URL", "http://127.0.0.1:3003")

PASS, FAIL, WARN = 0, 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


def warn(name, extra=""):
    global WARN
    WARN += 1
    print(f"  WARN  {name}  {extra}")


def post(path, payload, timeout=60):
    url = BASE.rstrip("/") + path
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


print("[1] 静态检查")
src = io.open(SERVER, encoding="utf-8").read()
ast.parse(src)
check("quantum_service/server.py 语法正确", True)
check("有 /qubit/registry 路由", '"/qubit/registry"' in src)
check("有 _handle_qubit_registry handler", "def _handle_qubit_registry" in src)
check("registry 读取在 LabRAD 锁内",
      re.search(r"def _handle_qubit_registry.*?with self\._labrad_lock", src, re.S) is not None)
check("set_params 有 registry 回落写入", "registry write failed" in src)

snap_src = io.open(SNAP, encoding="utf-8").read()
ast.parse(snap_src)
check("reg_snapshot.py 语法正确", True)
check("reg_snapshot.py 不再直连 LabRAD",
      "labrad_connect" not in snap_src and "registry_wrapper2" not in snap_src)
check("reg_snapshot.py 走 /qubit/registry", '"/qubit/registry"' in snap_src)
check("reg_snapshot.py 写走 /qubit/set_params", '"/qubit/set_params"' in snap_src)

print("[2] HTTP 检查")
try:
    res = post("/qubit/registry", {"name": "q2ld3"})
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", "replace")
    if "Unknown path" in body:
        warn("服务未重启（:3003 还是旧代码）",
             "Unknown path: /qubit/registry —— 请重启 :3003 后重跑本脚本")
    else:
        check("POST /qubit/registry 可用", False, f"HTTP {e.code} {body[:200]}")
except Exception as e:
    warn("连不上 :3003", f"{type(e).__name__}: {e}")
else:
    if res.get("error"):
        check("POST /qubit/registry 可用", False, res.get("error"))
    else:
        reg = res.get("registry") or {}
        check("POST /qubit/registry 可用", True)
        flat = {}
        for k, v in reg.items():
            if isinstance(v, dict):
                for sk, sv in v.items():
                    flat[f"{k}.{sk}"] = sv
            else:
                flat[k] = v
        check("registry 非空", len(flat) > 0, len(flat))
        check("含 timing.* 键", any(k.startswith("timing.") for k in flat),
              sorted(k for k in flat if k.startswith("timing.")))
        check("含 f10 / f21 / fread",
              all(k in flat for k in ("f10", "f21", "fread")),
              [k for k in ("f10", "f21", "fread") if k not in flat])
        check("含 PiGate.* 键", any(k.startswith("PiGate.") for k in flat))

print(f"\nPASS={PASS}  FAIL={FAIL}  WARN={WARN}")
sys.exit(0 if FAIL == 0 else 1)
