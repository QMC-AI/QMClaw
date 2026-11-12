"""从 IQraw 数据集算读出保真度（理想线性判别），并汇总 q7ld3 参数。

用法： python readout_metrics.py            # 扫 2765..2815 所有 IQraw
"""
import glob
import os
import sys

import h5py
import numpy as np

ROOT = r"D:\DataVault\LQHL.dir\test.dir\20251103_LD_SQ.dir"


def load_iqraw(path):
    with h5py.File(path, "r") as h:
        a = h["DataVault"][:]
    r0 = np.stack([a["f1"], a["f2"]], axis=1).astype(float)
    r1 = np.stack([a["f3"], a["f4"]], axis=1).astype(float)
    return r0, r1


def fidelity(r0, r1):
    mu0, mu1 = r0.mean(axis=0), r1.mean(axis=0)
    d = mu1 - mu0
    dn = d / (np.linalg.norm(d) + 1e-12)
    p0, p1 = r0 @ dn, r1 @ dn
    thr = 0.5 * (p0.mean() + p1.mean())
    e0 = float(np.mean(p0 > thr))      # |0> 被判成 1
    e1 = float(np.mean(p1 < thr))      # |1> 被判成 0
    f_avg = 1.0 - 0.5 * (e0 + e1)
    s0 = float(np.sqrt(np.mean((p0 - p0.mean()) ** 2)))
    s1 = float(np.sqrt(np.mean((p1 - p1.mean()) ** 2)))
    sep = float(np.linalg.norm(d))
    vis = sep / (sep + 2 * (s0 + s1))
    snr = sep / (0.5 * (s0 + s1))
    return dict(sep=sep, s0=s0, s1=s1, vis=vis, snr=snr,
                e0=e0, e1=e1, f_avg=f_avg, f0=1 - e0, f1c=1 - e1,
                thr=float(thr))


def main():
    rows = []
    for p in sorted(glob.glob(os.path.join(ROOT, "* - q7ld3%c IQraw.hdf5"))):
        n = int(os.path.basename(p)[:5])
        if n < 2740:
            continue
        try:
            r0, r1 = load_iqraw(p)
        except Exception as exc:
            print(n, "read fail", exc)
            continue
        try:
            m = fidelity(r0, r1)
        except Exception as exc:
            print(n, "fit fail", exc)
            continue
        rows.append((n, m))
        print("%05d  sep=%7.0f  rms=%6.0f/%6.0f  vis=%.4f  snr=%.3f  F0=%.4f F1=%.4f Favg=%.4f"
              % (n, m["sep"], m["s0"], m["s1"], m["vis"], m["snr"],
                 m["f0"], m["f1c"], m["f_avg"]))
    if rows:
        best = max(rows, key=lambda r: r[1]["f_avg"])
        print("\nbest fidelity: %05d  Favg=%.4f vis=%.4f" % (best[0], best[1]["f_avg"], best[1]["vis"]))
        bestv = max(rows, key=lambda r: r[1]["vis"])
        print("best visibility: %05d vis=%.4f Favg=%.4f" % (bestv[0], bestv[1]["vis"], bestv[1]["f_avg"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
