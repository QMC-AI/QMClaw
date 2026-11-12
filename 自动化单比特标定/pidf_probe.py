"""pidf 单点复测：sq.pidf(qobj, df=0.1) -> sq.pidf(qobj)

只采不写（update=False），跑完把两条 P1 曲线打印出来，用于判断
2787/2788 那种"全平"到底是 pidf 参数问题还是读出区分度问题。

用法：
    D:/anaconda3/envs/lqcs/python.exe pidf_probe.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qwf_auto_calib import (  # noqa: E402
    DEFAULT_BIT,
    DEFAULT_DATAVAULT,
    build_qwf,
    collect_datasets,
    load_dv,
    parse_validate,
    read_registry,
    run_ir,
)

SESSION = "20251103_LD_SQ"


def main():
    upd = "--update" in sys.argv
    bit = os.environ.get("BIT", DEFAULT_BIT)
    reg, err = read_registry(bit)
    if err:
        print("读寄存器失败：", err)
        return 2
    print("f10   =", reg.get("f10"))
    print("f21   =", reg.get("f21"))
    print("fread =", reg.get("fread"), " power =", reg.get("ReadIn.power"))
    print("bias_z=", reg.get("bias_z"))
    print("PiGate.amp =", reg.get("PiGate.amp"), " length =", reg.get("PiGate.length"))

    steps = [
        ("d1", "sq.pidf", 600, "pidf df=0.1（只采不写）", {"df": 0.1, "update": upd}),
        ("d2", "sq.pidf", 600, "pidf 默认", {"update": upd}),
    ]
    budget = {"max_wall": "15m", "max_writes": 2, "max_datasets": 2, "max_scan_points": 50}
    text = build_qwf(f"{bit}_pidf_d01", f"{bit} pidf 复测 df=0.1", bit, SESSION, budget, steps)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "q7ld3_pidf_d01.qwf"), "w", encoding="utf-8") as f:
        f.write(text)

    ir, err = parse_validate(text)
    if err:
        print("parse/validate 失败：", err)
        return 2

    st = run_ir(ir, {"qubit": bit, "session": SESSION}, timeout=1200)
    print("status:", st.get("status"), " outcome:", st.get("outcome"))
    ds = collect_datasets(st, DEFAULT_DATAVAULT)
    print("datasets:", {k: v["dataset"] for k, v in ds.items()})

    for alias, kind in (("d1", "pidf df=0.1"), ("d2", "pidf 默认")):
        if alias not in ds:
            print(f"\n[{kind}] 没有数据集")
            continue
        rec = load_dv(ds[alias]["path"])["data"]
        x = np.asarray(rec["f0"], float)
        y = np.asarray(rec["f6"], float)
        amp = np.asarray(rec["f1"], float)
        print(f"\n[{kind}] ds={ds[alias]['dataset']}  {os.path.basename(ds[alias]['path'])}")
        print("  df轴(GHz):", np.array2string(x, precision=4, separator=", "))
        print("  P1      :", np.array2string(y, precision=4, separator=", "))
        print("  Amp(e6) :", np.array2string(amp / 1e6, precision=2, separator=", "))
        k = int(np.argmin(amp))
        print(f"  Amp min={amp[k]/1e6:.2f}e6 @ df={x[k]:+.4f}GHz  depth="
              f"{-20*np.log10(amp[k]/np.median(amp)):.2f}dB  p2p={20*np.log10(amp.max()/amp.min()):.2f}dB")
        print(f"  min={y.min():.4f} @ df={x[int(np.argmin(y))]:+.4f}GHz   max={y.max():.4f}"
              f"   p2p={y.max()-y.min():.4f}   std={y.std():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
