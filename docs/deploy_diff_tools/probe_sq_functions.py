"""逐个调用 sq.<实验>(qobj, do_plot=False)，只验证 prepare_dataset 能过。

安全措施：把 SMTExperiment.run / MultiTypeSMT.run 换成直接抛 _ProbeStop，
所以 prepare_dataset 成功后**立刻中断**，不会真的下发脉冲或跑测量。

    D:\\anaconda3\\envs\\lqcs\\python.exe docs\\deploy_diff_tools\\probe_sq_functions.py [qname]
"""
import json
import os
import sys
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SERVER = _REPO / "Agentic Workflow" / "qmclaw-server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))


class _ProbeStop(Exception):
    pass


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

    # 阻断真正的测量
    from lqms.measure.tuners import smt_experiment

    def _stop(*_a, **_k):
        raise _ProbeStop("prepare_dataset OK, run() blocked by probe")

    blocked = []
    for cls_name in ("SMTExperiment", "MultiTypeSMT"):
        cls = getattr(smt_experiment, cls_name, None)
        if cls is not None:
            cls.run = _stop
            blocked.append(cls_name)
    print(f"blocked run(): {blocked}")

    from lqms.pyle.dataking import sweeps
    import numpy as np

    orig = sweeps.prepDataset
    stats = {}

    def spy(*args, **kwargs):
        axes = args[2] if len(args) > 2 else []
        swept = []
        for item in axes:
            val = item[0] if isinstance(item, (list, tuple)) else item
            if np.iterable(val):
                try:
                    swept.append(len(val))
                except Exception:  # noqa: BLE001
                    swept.append("?")
        stats["axes"] = len(axes)
        stats["swept"] = swept
        return orig(*args, **kwargs)

    sweeps.prepDataset = spy

    cfg = json.loads((_SERVER / "config" / "experiment_configs.json").read_text(encoding="utf-8"))
    experiments = cfg.get("experiments", {})

    sq = backend._sq

    if len(sys.argv) > 2:
        targets = [(n, n) for n in sys.argv[2].split(",") if n]
    else:
        targets = [
            (k, v.get("function", "").replace("sq.", "")) for k, v in experiments.items()
        ]

    print(f"\nqubit = {qname}\n")
    print(f"{'experiment':16} {'func':14} {'axes':5} {'swept':14} result")
    print("-" * 78)
    for key, func in targets:
        fn = getattr(sq, func, None)
        if fn is None:
            print(f"{key:16} {func:14} {'--':5} {'--':14} MISSING")
            continue
        stats.clear()
        call_kwargs = {} if func == "xeb" else {"do_plot": False}
        try:
            fn(qobj, **call_kwargs)
            print(f"{key:16} {func:14} {stats.get('axes',-1):<5} {str(stats.get('swept')):14} OK (run blocked)")
        except _ProbeStop:
            print(f"{key:16} {func:14} {stats.get('axes',-1):<5} {str(stats.get('swept')):14} OK (run blocked)")
        except Exception as exc:  # noqa: BLE001
            print(
                f"{key:16} {func:14} {stats.get('axes',-1):<5} {str(stats.get('swept')):14} "
                f"{type(exc).__name__}: {exc}"
            )
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
