"""LabRAD 只读联调自检 — 一条命令输出全部前置条件与失败原因

只读、不改状态、不下发实验。Run with the env's python:

    D:\\semester4\\deploy\\qmclaw\\qmclaw\\python.exe docs\\deploy_diff_tools\\check_labrad_link.py

输出：
  1. 解析出的 host/port/timeout 及其来源（env / system.json / default）
  2. TCP 可达性
  3. ~/.lqcs 是否存在
  4. TEMP 可写性与 lqms 导入
  5. labrad.connect 实际结果与失败阶段
  6. sq_nodes 可用实验 vs experiment_configs 覆盖差集
  7. 写闸门状态
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

SERVER_ROOT = Path(r"D:\semester4\QMClaw\Agentic Workflow\qmclaw-server")
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

FAILURES = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("== 1. LabRAD 连接参数 ==")
    from services.common.config import get_labrad_params, get_labrad_paths, load_system_config

    env_host = os.environ.get("LABRADHOST")
    env_port = os.environ.get("LABRADPORT")
    sys_conn = (load_system_config().get("lqcs") or {}).get("connection") or {}
    params = get_labrad_params()
    host_src = "env:LABRADHOST" if env_host else (
        "system.json:lqcs.connection" if sys_conn.get("host") else "default"
    )
    port_src = "env:LABRADPORT" if env_port else (
        "system.json:lqcs.connection" if sys_conn.get("port") else "default"
    )
    print(f"       host={params['host']} (from {host_src})")
    print(f"       port={params['port']} (from {port_src})")
    print(f"       timeout={params['timeout']}")
    paths = get_labrad_paths()
    print(f"       paths.measure_scripts={paths.get('measure_scripts')}")
    print(f"       paths.backend_dir={paths.get('backend_dir')}")
    check("host is set", bool(params["host"]))
    check("port is set", bool(params["port"]))

    print("== 2. TCP 可达性 ==")
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        sock.connect((params["host"], int(params["port"])))
        check(f"TCP {params['host']}:{params['port']} reachable", True)
    except Exception as e:
        check(f"TCP {params['host']}:{params['port']} reachable", False, str(e))
    finally:
        sock.close()

    print("== 3. ~/.lqcs ==")
    lqcs_dir = Path.home() / ".lqcs"
    check("~/.lqcs exists", lqcs_dir.exists(), str(lqcs_dir))
    if lqcs_dir.exists():
        try:
            from lqcs import system_config

            ip = system_config.get_config().get("ip")
            ray_head = system_config.get_ray_head()
            ray_port = system_config.get_ray_port()
            print(f"       ip={ip} ray_head={ray_head} ray_port={ray_port}")
            check("lqcs.system_config readable", True)
        except Exception as e:
            check("lqcs.system_config readable", False, str(e))

    print("== 4. TEMP 可写与 lqms 导入 ==")
    try:
        with tempfile.NamedTemporaryFile(delete=True) as f:
            f.write(b"ok")
        check("TEMP writable", True)
    except Exception as e:
        check("TEMP writable", False, str(e))

    try:
        import lqms  # noqa: F401
        from lqms.measure.tuners import sq_nodes  # noqa: F401

        check("lqms + sq_nodes import", True)
    except Exception as e:
        check("lqms + sq_nodes import", False, str(e))
        sq_nodes = None
    else:
        from lqms.measure.tuners import sq_nodes

    print("== 5. labrad.connect 实际结果 ==")
    try:
        from services.common.config import labrad_connect

        cxn = labrad_connect()
        check("labrad.connect succeeded", True, f"ID={getattr(cxn, 'ID', '?')}")
        try:
            cxn.disconnect()
        except Exception:
            pass
    except Exception as e:
        check("labrad.connect succeeded", False, str(e))

    print("== 6. sq_nodes vs experiment_configs ==")
    if sq_nodes is not None:
        import json

        exp_fns = sorted(
            n for n in dir(sq_nodes)
            if not n.startswith("_") and not n.startswith("qq") and callable(getattr(sq_nodes, n, None))
        )
        print(f"       sq_nodes 实验函数 {len(exp_fns)} 个: {exp_fns[:12]}{'...' if len(exp_fns) > 12 else ''}")
        try:
            with open(SERVER_ROOT / "config" / "experiment_configs.json", encoding="utf-8") as f:
                configured = sorted((json.load(f).get("experiments") or {}).keys())
            missing = [c for c in configured if c not in exp_fns and c.replace("_", " ") not in exp_fns]
            print(f"       experiment_configs 条目 {len(configured)} 个")
            if missing:
                print(f"       配置里有但 sq_nodes 未直接匹配: {missing}")
            check("experiment_configs loadable", True)
        except Exception as e:
            check("experiment_configs loadable", False, str(e))

    print("== 7. 写闸门状态 ==")
    from services.common.config import get_actuation_flags

    for svc in ("quantum_service", "analysis_service"):
        flags = get_actuation_flags(svc)
        print(f"       {svc}: {flags}")
        check(
            f"{svc} write gate closed by default",
            not flags["allow_execute"] and not flags["allow_set_params"],
            "REVIEW if True — 仅在人工提权后才应打开",
        )

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
