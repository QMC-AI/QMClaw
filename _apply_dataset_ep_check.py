"""apply_dataset 端点检查：quantum_service 的 /qubit/apply_dataset。

背景：厂商实验（s21_zpa2d / iqraw / pidf …）会静默改寄存器（fread、f10、bias_z …），
而实验数据集的 `parameters` 里留有当时的完整 registry 快照。D2 的 /qubit/registry
是只读的，本端点负责把快照写回（实验后对齐现场 / 回滚）。

两部分：
  [1] 静态检查（不依赖服务在跑）
      - server.py 里有 `/qubit/apply_dataset` 路由与 `_handle_qubit_apply_dataset`
      - 写回逻辑复用了 /qubit/set_params 的 `_write_qubit_params`
      - Express 代理 serviceProxy.ts 有 `/quantum/qubit/apply_dataset`
  [2] HTTP 检查（服务在跑时）
      - POST :3003 /qubit/apply_dataset {name, dataset, dry_run: true}
        返回 success=True、compared>0、skipped 含 discriminator.*
      - 服务未重启时给出明确提示（而不是笼统 FAIL）

跑法：
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
        D:/anaconda3/envs/lqcs/python.exe D:/qmclaw/_apply_dataset_ep_check.py
"""
import io
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server", "services",
                      "quantum_service", "server.py")
PROXY = os.path.join(ROOT, "Agentic Workflow", "qmclaw-server", "src",
                     "services", "serviceProxy.ts")
BASE = os.environ.get("QMCLAW_QUANTUM_URL", "http://127.0.0.1:3003")
QUBIT = os.environ.get("APPLY_DATASET_QUBIT", "q7ld3")
DATASET = int(os.environ.get("APPLY_DATASET_ID", "1032"))

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


def post(path, payload, timeout=120):
    url = BASE.rstrip("/") + path
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return {"error": raw}


print("[1] 静态检查")
src = io.open(SERVER, encoding="utf-8").read()
check("server.py 有 /qubit/apply_dataset 路由", '"/qubit/apply_dataset"' in src)
check("server.py 有 _handle_qubit_apply_dataset", "def _handle_qubit_apply_dataset" in src)
check("写回复用 _write_qubit_params", "_write_qubit_params(qname, todo)" in src)
check("写回逻辑有写闸门", "allow_set_params" in src.split("def _handle_qubit_apply_dataset")[1][:4000])
try:
    proxy_src = io.open(PROXY, encoding="utf-8").read()
    check("serviceProxy.ts 有 /quantum/qubit/apply_dataset",
          "/quantum/qubit/apply_dataset" in proxy_src)
except Exception as e:  # noqa: BLE001
    warn("读 serviceProxy.ts 失败", str(e))

print("[2] HTTP 检查")
try:
    res = post("/qubit/apply_dataset",
               {"name": QUBIT, "dataset": DATASET, "dry_run": True})
except Exception as e:  # noqa: BLE001
    res = {"error": str(e)}

err = str(res.get("error") or "")
if "Unknown path" in err:
    warn("服务未重启，/qubit/apply_dataset 还不存在",
         "重启 :3003（start_all_services.py）后再跑本检查")
elif res.get("success") is not True:
    check("dry-run 返回 success", False, json.dumps(res, ensure_ascii=False)[:300])
else:
    check("dry-run 返回 success", True)
    check("compared > 0", (res.get("compared") or 0) > 0, str(res.get("compared")))
    check("to_apply 是整数", isinstance(res.get("to_apply"), int))
    skipped = res.get("skipped") or []
    check("默认跳过 discriminator.*",
          any(str(k).startswith("discriminator.") for k in skipped), str(skipped[:5]))
    check("changes 为 dict", isinstance(res.get("changes"), dict))
    check("dry_run 标记为 True", res.get("dry_run") is True)

    # 缺参数的错误分支
    r2 = post("/qubit/apply_dataset", {"dataset": DATASET})
    check("缺 name 报错", r2.get("success") is False, json.dumps(r2, ensure_ascii=False)[:200])
    r3 = post("/qubit/apply_dataset", {"name": QUBIT})
    check("缺 dataset 报错", r3.get("success") is False, json.dumps(r3, ensure_ascii=False)[:200])

print(f"\nPASS={PASS} FAIL={FAIL} WARN={WARN}")
sys.exit(1 if FAIL else 0)
