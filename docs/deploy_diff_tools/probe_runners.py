"""逐个实验 Runner 走 prepare_dataset（smt_run=True），看默认参数下谁会失败。

只做 init_run + prepare_dataset，**不运行实验、不下发任何脉冲**。

    D:\\anaconda3\\envs\\lqcs\\python.exe docs\\deploy_diff_tools\\probe_runners.py [qname]
"""
import os
import sys
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SERVER = _REPO / "Agentic Workflow" / "qmclaw-server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

# QMClaw experiment name -> lqms runner class
RUNNERS = ["Spectroscopy", "S21", "T1", "IQraw", "Ramsey", "PiPulse", "XEB", "DispersiveShift"]

_last_axes = []


def main() -> int:
    qname = sys.argv[1] if len(sys.argv) > 1 else "q2ld3"
    os.chdir(_SERVER)

    from services.common.config import load_system_config
    from backends.lqcs import LQCSBackend

    backend = LQCSBackend(load_system_config().get("lqcs", {}))
    if not backend.initialize():
        print("FAILED: backend.initialize()")
        return 1
    qobj = backend._all_qubits.get(qname)
    if qobj is None:
        print(f"no such qubit {qname}")
        return 1

    from lqms.pyle.dataking import sweeps
    import numpy as np

    orig = sweeps.prepDataset

    def spy(*args, **kwargs):
        axes = args[2] if len(args) > 2 else []
        shape = []
        for item in axes:
            val = item[0] if isinstance(item, (list, tuple)) else item
            if np.iterable(val):
                try:
                    shape.append(len(val))
                except Exception:  # noqa: BLE001
                    shape.append("?")
        _last_axes.clear()
        _last_axes.append((len(axes), shape))
        return orig(*args, **kwargs)

    sweeps.prepDataset = spy

    from lqms.measure.tuners import base_experiment as basex

    print(f"\nqubit = {qname}   f10={qobj.regs['f10']}  fread={qobj.regs['fread']}\n")
    print(f"{'runner':16} {'axes':5} {'swept':12} result")
    print("-" * 62)
    for cls_name in RUNNERS:
        cls = getattr(basex, cls_name, None)
        if cls is None:
            print(f"{cls_name:16} {'--':5} {'--':12} missing")
            continue
        _last_axes.clear()
        try:
            runner = cls()
            runner(qobj, smt_run=True)
            axes = _last_axes[0][0] if _last_axes else -1
            shape = _last_axes[0][1] if _last_axes else "-"
            print(f"{cls_name:16} {axes:<5} {str(shape):12} OK")
        except Exception as exc:  # noqa: BLE001
            axes = _last_axes[0][0] if _last_axes else -1
            shape = _last_axes[0][1] if _last_axes else "-"
            print(f"{cls_name:16} {axes:<5} {str(shape):12} {type(exc).__name__}: {exc}")
            if os.environ.get("PROBE_TRACE"):
                traceback.print_exc()

    backend.shutdown()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
