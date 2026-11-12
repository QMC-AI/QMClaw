"""复现浏览器里 Spectroscopy 的失败点，但**不跑实验、不下发脉冲**。

原理：
    BaseExperiment.__call__(qobj, smt_run=True) 只做 init_run + prepare_dataset
    然后立即 return（smt_run=True 时不会进入 run()）。浏览器那次失败就发生在
    prepare_dataset -> sweeps.prepDataset -> Dataset.__init__ 这一段，
    所以单独调到这里就能复现，且不会触发任何测量。

用法：
    D:\\anaconda3\\envs\\lqcs\\python.exe docs\\deploy_diff_tools\\probe_spectroscopy_prepare.py [qname]

默认 qname = q2ld3（用户报错时选中的比特）。
"""
import os
import sys
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SERVER = _REPO / "Agentic Workflow" / "qmclaw-server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))


def _dump_regs(qobj):
    try:
        regs = qobj.regs
        keys = list(regs.keys())
        print(f"regs keys ({len(keys)}): {keys[:40]}")
        for k in ("fread", "f10", "fc", "PiGate", "Readout"):
            try:
                print(f"  {k} = {regs[k]!r}"[:400])
            except Exception as exc:  # noqa: BLE001
                print(f"  {k} -> ERR {type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"regs unavailable: {type(exc).__name__}: {exc}")


def main() -> int:
    qname = sys.argv[1] if len(sys.argv) > 1 else "q2ld3"
    os.chdir(_SERVER)

    from services.common.config import load_system_config
    from backends.lqcs import LQCSBackend

    backend = LQCSBackend(load_system_config().get("lqcs", {}))
    if not backend.initialize():
        print("FAILED: backend.initialize() returned False")
        return 1

    all_qobjs = backend._all_qubits
    print()
    print(f"qubits      : {len(all_qobjs)}  ({qname} present: {qname in all_qobjs})")
    qobj = all_qobjs.get(qname)
    if qobj is None:
        print(f"available   : {list(all_qobjs)[:24]}")
        backend.shutdown()
        return 1

    print(f"qobj type   : {type(qobj).__name__}")
    _dump_regs(qobj)

    # 拦截 prepDataset，打印真正传进去的参数，再交给原函数（保留原始异常）
    from lqms.pyle.dataking import sweeps

    orig = sweeps.prepDataset

    def spy(*args, **kwargs):
        print("---- sweeps.prepDataset(...) ----")
        for i, val in enumerate(args):
            try:
                rep = repr(val)
            except Exception:  # noqa: BLE001
                rep = "<unrepresentable>"
            print(f"  arg{i}: {type(val).__name__} = {rep[:900]}")
        for k, val in kwargs.items():
            try:
                rep = repr(val)
            except Exception:  # noqa: BLE001
                rep = "<unrepresentable>"
            print(f"  kw[{k}]: {type(val).__name__} = {rep[:400]}")
        return orig(*args, **kwargs)

    sweeps.prepDataset = spy

    from lqms.measure.tuners import base_experiment as basex

    mode = sys.argv[2] if len(sys.argv) > 2 else "default"
    call_kwargs = {"smt_run": True}
    if mode == "sweep":
        import numpy as np

        f10 = float(qobj.regs["f10"])
        call_kwargs["freq"] = np.arange(f10 - 0.05, f10 + 0.05, 0.002)
        print(f"extra: freq = arange(f10-0.05, f10+0.05, 0.002) -> {len(call_kwargs['freq'])} pts")

    print()
    print(f"==== Spectroscopy().__call__(qobj, smt_run=True) [mode={mode}] ====")
    try:
        runner = basex.Spectroscopy()
        runner(qobj, **call_kwargs)
        print("RESULT: prepare_dataset 正常返回（未复现异常，也未运行实验）")
    except Exception:  # noqa: BLE001
        print("RESULT: 复现异常 ↓")
        traceback.print_exc()
    finally:
        try:
            backend.shutdown()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
