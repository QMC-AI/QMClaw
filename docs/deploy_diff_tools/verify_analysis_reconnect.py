"""验证 analysis_service 修复后的重连路径（不启动 HTTP 服务）。

直接实例化 AnalysisService，调用修好的 _do_reconnect()，
检查 DataLab 是否就绪、InfoBase 缺失时是否被安全跳过。

用法：
    D:/anaconda3/envs/lqcs/python.exe docs/deploy_diff_tools/verify_analysis_reconnect.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, "..", "..", "Agentic Workflow", "qmclaw-server"))
sys.path.insert(0, SERVER)
os.chdir(SERVER)

from services.analysis_service.server import AnalysisService  # noqa: E402


def main():
    print(f"python: {sys.executable}", flush=True)
    svc = AnalysisService(port=3097)
    print(f"mode  : {svc._get_mode()}", flush=True)

    ok = svc._do_reconnect()
    print(f"\n_do_reconnect() -> {ok}", flush=True)
    print(f"  _data  : {type(svc._data).__name__ if svc._data is not None else None}", flush=True)
    print(f"  _info  : {type(svc._info).__name__ if svc._info is not None else None}", flush=True)
    print(f"  _qter  : {type(svc._qter).__name__ if svc._qter is not None else None}", flush=True)
    print(f"  session: {svc._session_path}", flush=True)
    print(f"  fallback state = {svc._fallback.state}, last_error = {svc._fallback.last_error}", flush=True)

    if svc._data is not None:
        try:
            import inspect
            print(f"  DataLab datasets attr: {hasattr(svc._data, 'datasets')}", flush=True)
        except Exception as e:
            print(f"  inspect failed: {e}", flush=True)

    if svc._cxn is not None:
        try:
            svc._cxn.disconnect()
            print("  disconnected", flush=True)
        except Exception as e:
            print(f"  disconnect failed: {e}", flush=True)

    return 0 if ok and svc._data is not None else 1


if __name__ == "__main__":
    sys.exit(main())
