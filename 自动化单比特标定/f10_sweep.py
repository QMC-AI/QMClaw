"""q11ld3：用「激发」直接扫 f10（P0 建议的落地版）。

固定 bias，逐点设 f10，跑 sq.piamp(amp=A)，只看**读出幅度列 f1**的 Rabi 幅度（不依赖判别器）。
真跃迁处会看到 f1 随 amp 单调下降/回升（π 翻转），噪声点则只有 ±2–3% 抖动。
"""
import io
import json
import sys
import time

import numpy as np

sys.path.insert(0, r"D:\qmclaw\自动化单比特标定")
from qwf_auto_calib import (BASE, build_piamp_qwf, collect_datasets, load_dv_retry,  # noqa: E402
                            parse_validate, run_ir, write_params, read_registry)

BIT = __import__("os").environ.get("BIT", "q11ld3")
SESSION = "20251103_LD_SQ"
DV = r"D:\DataVault\LQHL.dir\test.dir"
OUT = r"D:\qmclaw\自动化单比特标定\out"

bias = float(sys.argv[1]) if len(sys.argv) > 1 else -2.0
amp = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
f0 = float(sys.argv[3]) if len(sys.argv) > 3 else 3.15
f1 = float(sys.argv[4]) if len(sys.argv) > 4 else 3.40
step = float(sys.argv[5]) if len(sys.argv) > 5 else 0.02

reg, _ = read_registry(BIT)
write_params(BIT, {"bias_z": bias})
print("bias_z=%.2f  PiGate.length=%s PiHalf.length=%s  amp=%.2f" % (
    bias, reg.get("PiGate.length"), reg.get("PiHalf.length"), amp), flush=True)

rows = []
for f in np.arange(f0, f1 + 1e-9, step):
    write_params(BIT, {"f10": round(float(f), 6), "f21": round(float(f) - 0.2, 6)})
    text = build_piamp_qwf(BIT, SESSION, bias, amp)
    ir, err = parse_validate(text, require_writes=False)
    if err:
        print("  %.4f 校验失败 %s" % (f, err), flush=True)
        continue
    wid = "wf_f10sweep_%d_%d" % (int(time.time() * 1000) % 10 ** 9, int(round(f * 1000)))
    st = run_ir(ir, {"qubit": BIT, "session": SESSION}, timeout=600)
    ds = collect_datasets(st, DV)
    if "p1" not in ds:
        print("  %.4f no dataset (%s)" % (f, st.get("status")), flush=True)
        continue
    res = load_dv_retry(ds["p1"]["path"], log=print)
    a = res["data"]
    y = np.asarray(a["f1"], float) / 1e6
    swing = (y.max() - y.min()) / max(np.median(y), 1e-9) * 100
    p1c = np.asarray(a["f6"], float)
    xa = np.asarray(a["f0"], float)
    corr = float(np.corrcoef(xa, y)[0, 1]) if y.std() > 0 else 0.0
    rows.append({"f10": float(f), "dataset": ds["p1"]["dataset"], "swing_pct": round(float(swing), 2),
                 "corr": round(corr, 3), "score": round(abs(corr) * swing, 2),
                 "f1_min": round(float(y.min()), 2), "f1_max": round(float(y.max()), 2),
                 "p1_ptp": round(float(p1c.max() - p1c.min()), 3)})
    print("  f10=%.4f ds=%d  幅度摆动 %.1f%%  corr(r) %+.2f  (%0.1f–%0.1f M)" % (
        f, ds["p1"]["dataset"], swing, corr, y.min(), y.max()), flush=True)

rows.sort(key=lambda r: -r["score"])
print("\n按幅度摆动排序：")
for r in rows[:5]:
    print("  %.4f  score %.1f  swing %.1f%%  corr %+.2f  ds %d" % (
        r["f10"], r["score"], r["swing_pct"], r["corr"], r["dataset"]))
io.open(OUT + "\\" + BIT + "_f10sweep.json", "w", encoding="utf-8").write(
    json.dumps(rows, ensure_ascii=False, indent=2))
