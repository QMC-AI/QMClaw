"""按 quantum_service /execute 的方式，逐条执行 experiment_configs.json 的 defaultCommand。

只验证「命令串能不能被 exec 到 prepare_dataset 成功」，不跑测量：
SMTExperiment.run / MultiTypeSMT.run 被替换成抛 _ProbeStop。

    D:\\anaconda3\\envs\\lqcs\\python.exe docs\\deploy_diff_tools\\verify_experiment_commands.py [qname]
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

    import numpy as np
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

    from lqms.measure.tuners import smt_experiment

    def _stop(*_a, **_k):
        raise _ProbeStop("prepare_dataset OK; run() blocked")

    for cls_name in ("SMTExperiment", "MultiTypeSMT"):
        cls = getattr(smt_experiment, cls_name, None)
        if cls is not None:
            cls.run = _stop

    # 与 services/quantum_service/server.py 的 _execute() 保持一致
    exec_globals = {
        "__name__": "__agent__",
        "cxn": backend.cxn,
        "s": backend._s,
        "sq": backend._sq,
        "data": backend._data,
        "qter": backend._qter,
        "BasicTuner": backend._BasicTuner,
        "generate_qubit": backend._generate_qubit,
        "generate_coupler": backend._generate_coupler,
        "np": np,
        qname: qobj,
        "__builtins__": __builtins__,
    }

    cfg = json.loads(
        (_SERVER / "config" / "experiment_configs.json").read_text(encoding="utf-8")
    )
    experiments = cfg.get("experiments", {})

    print(f"\nqubit = {qname}\n")
    ok = bad = skip = 0
    for key, spec in experiments.items():
        cmd = (spec.get("defaultCommand") or "").replace("{qubit}", qname)
        fn_name = (spec.get("function") or "").replace("sq.", "")
        if spec.get("available") is False:
            alt = spec.get("alternative") or {}
            note = spec.get("note") or "该实验在当前 sq_nodes 中不可用"
            alt_txt = f"；可考虑 {alt.get('function')}" if alt.get("function") else ""
            print(f"{key:16} SKIP  不可用：{note}{alt_txt}")
            skip += 1
            continue
        if not cmd:
            print(f"{key:16} (no defaultCommand)")
            skip += 1
            continue
        if getattr(backend._sq, fn_name, None) is None:
            print(f"{key:16} SKIP  sq.{fn_name} 不存在")
            skip += 1
            continue
        try:
            exec(cmd, exec_globals, {})
            print(f"{key:16} OK    {cmd}")
            ok += 1
        except _ProbeStop:
            print(f"{key:16} OK    {cmd}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"{key:16} FAIL  {cmd}\n                 -> {type(exc).__name__}: {exc}")
            bad += 1
            if os.environ.get("PROBE_TRACE"):
                traceback.print_exc()

    backend.shutdown()
    print(f"\nsummary: OK={ok}  FAIL={bad}  SKIP={skip}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
