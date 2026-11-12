"""离线验证 _handle_plot：连 LabRAD -> 载入最新数据集 -> 执行 defaultPlotCommand。

用法：
    D:/anaconda3/envs/lqcs/python.exe docs/deploy_diff_tools/verify_plot_handler.py [command]
默认 command 为 `qter.fitData({exp_num})`。
"""
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.abspath(os.path.join(HERE, "..", "..", "Agentic Workflow", "qmclaw-server"))
sys.path.insert(0, SERVER)
os.chdir(SERVER)

import services.analysis_service.server as analysis_srv  # noqa: E402
from services.analysis_service.server import AnalysisService  # noqa: E402

# 沙箱不允许写 qmclaw-web/public/plots，验证时把输出目录改到系统临时目录
TMP_PLOTS = Path(tempfile.mkdtemp(prefix="qmclaw_plot_"))
analysis_srv.PLOTS_DIR = TMP_PLOTS


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "qter.fitData({exp_num})"
    svc = AnalysisService(port=3097)
    if not svc._do_reconnect():
        print("FAIL: reconnect failed")
        return 1
    print(f"connected, dataset_num = {getattr(svc._data, 'dataset_num', '?')}")

    result = svc._handle_plot({"command": command, "dataset_index": -1})
    print(f"result: {result}")
    path = result.get("plotPath")
    if path and os.path.exists(path):
        print(f"plot file size = {os.path.getsize(path)} bytes")
    else:
        print("no plot file produced")
    try:
        svc._cxn.disconnect()
    except Exception:
        pass
    return 0 if path and os.path.exists(path) else 1


if __name__ == "__main__":
    sys.exit(main())
