"""复现 analysis_service 的初始化链路，逐步定位失败点（只读，不写数据）。

用法：
    D:/anaconda3/envs/lqcs/python.exe docs/deploy_diff_tools/probe_analysis_init.py

每一步单独 try/except，打印 traceback，能直接看出是 LabRAD 连接、注册表
还是 DataLab 初始化挂掉。analysis_service 的报错被压成了 "未知错误"（空
字符串），所以这里必须拿到完整栈。
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, "..", "..", "Agentic Workflow", "qmclaw-server"))
sys.path.insert(0, SERVER)

SESSION = os.environ.get("PROBE_SESSION", "").strip()
if SESSION:
    SESSION_PATH = ["", "LQHL"] + SESSION.split("/")
else:
    SESSION_PATH = ["", "LQHL", "test", "20251103_LD_SQ"]


def step(name, fn):
    print(f"\n=== {name} ===", flush=True)
    try:
        out = fn()
        print(f"OK: {name}", flush=True)
        return out
    except Exception as e:
        print(f"FAIL: {name} -> {type(e).__name__}: {e!r}", flush=True)
        traceback.print_exc()
        return None


def main():
    print(f"python   : {sys.executable}")
    print(f"version  : {sys.version.split()[0]}")
    print(f"session  : {SESSION_PATH}", flush=True)

    from services.common.config import labrad_connect, get_labrad_params
    print(f"labrad   : {get_labrad_params()}", flush=True)

    cxn = step("labrad_connect()", labrad_connect)
    if cxn is None:
        return 1

    dv = step("cxn.data_vault", lambda: cxn.data_vault)
    if dv is not None:
        step("dv.cwd()", lambda: print("cwd =", dv.cwd()) or dv.cwd())

    step("import lqms.data_process", lambda: __import__("lqms.data_process", fromlist=["x"]))
    from lqms.data_process import dataAnalysisCore as dc, QubitUpdater
    from lqms.utils.save_path import get_info_path
    from lqms.pyle import registry_wrapper2

    s = step("RegistryWrapper(cxn, session)", lambda: registry_wrapper2.RegistryWrapper(cxn, SESSION_PATH))

    data = step(
        "DataLab(session, dv)",
        lambda: dc.DataLab(SESSION_PATH, dv, dv_type="data_vault"),
    )
    if data is not None:
        step("DataLab.datasets_count", lambda: print("datasets =", len(data.datasets)) or len(data.datasets))

    info = None
    if s is not None:
        info_path = step("get_info_path(registry)", lambda: get_info_path(s))
        if info_path:
            print(f"info_path = {info_path}", flush=True)
            info = step("InfoBase(info_path)", lambda: dc.InfoBase(info_path))

    if data is not None:
        step("QubitUpdater(data, info)", lambda: QubitUpdater(data, info))

    step("cxn.disconnect()", lambda: cxn.disconnect())
    print("\nDONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
