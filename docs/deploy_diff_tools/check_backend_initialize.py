"""LQCS 后端 initialize() 只读联调自检

只做 quantum_service 在 online 模式下启动时会做的事：连 LabRAD → setWiringInfo →
switchSession → 生成 qubit/coupler 对象 → 构造 BasicTuner。
**不下发任何实验、不修改任何参数**（写闸门与实验执行路径都不经过这里）。

    D:\\anaconda3\\envs\\lqcs\\python.exe docs\\deploy_diff_tools\\check_backend_initialize.py

输出：后端状态、session 路径、可用 qubit 列表、可用实验函数数量。
"""
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SERVER = _REPO / "Agentic Workflow" / "qmclaw-server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))


def main() -> int:
    import os

    os.chdir(_SERVER)

    from services.common.config import get_labrad_params, load_system_config
    from backends.lqcs import LQCSBackend
    from backends.backend_types import BackendStatus

    params = get_labrad_params()
    print(f"LabRAD target : {params['host']}:{params['port']} (timeout {params['timeout']}s)")
    system = load_system_config()
    print(f"session.json  : ", end="")
    import json

    cfg = json.loads((_SERVER / "config" / "session.json").read_text(encoding="utf-8"))
    print(cfg.get("session"))

    lqcs_cfg = system.get("lqcs", {})
    backend = LQCSBackend(lqcs_cfg)

    t0 = time.time()
    ok = backend.initialize()
    dt = time.time() - t0

    print()
    print(f"initialize() -> {ok}   ({dt:.1f}s)")
    print(f"status        : {backend.status}")

    if not ok:
        print("FAILED: 后端初始化失败，上面的 [LQCS Backend] 日志里有失败阶段")
        return 1

    print(f"session path  : {backend.current_session_path}")
    qubits = backend.get_qubits()
    print(f"qubits        : {len(qubits)} 个 -> {[q['name'] for q in qubits][:12]}")
    if qubits and len(qubits[0]) > 1:
        q0 = qubits[0]
        print(f"  {q0['name']}: f10={q0.get('f10')} fread={q0.get('fread')} bias_z={q0.get('bias_z')}")
    experiments = backend.list_experiments()
    print(f"experiments   : {len(experiments)} 个 -> {[e['name'] for e in experiments][:10]}")

    # 验证 DataVault 当前目录已经切到配置的 session，并核对数据集可见性
    try:
        dv = backend.cxn.data_vault
        raw = dv.dir()
        subdirs = [str(x) for x in (list(raw[0]) if len(raw) >= 1 else [])]
        datasets = [str(x) for x in (list(raw[1]) if len(raw) >= 2 else [])]
        print(f"DataVault now : dirs={subdirs} datasets={len(datasets)}")
        if datasets:
            print(f"  first/last  : {datasets[0]!r} ... {datasets[-1]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"DataVault dir() failed: {exc}")

    backend.shutdown()
    print()
    print("OK: 后端可初始化（只读，未下发任何实验）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        sys.exit(2)
