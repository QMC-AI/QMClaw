# -*- coding: utf-8 -*-
"""在读出频率上做 IQraw 可见度扫点：固定 bias，扫 fread 偏移，找最佳读点。

只写 fread（便于逐点切换），iqraw 本身 update false。
"""
import glob
import json
import os
import sys
import time
import urllib.request

import h5py
import numpy as np

BASE = "http://127.0.0.1:3008"
Q = "http://127.0.0.1:3003"
BIT = os.environ.get("BIT", "q7ld3")   # 换比特：set BIT=q8ld3
SESSION = "20251103_LD_SQ"
ROOT = r"D:\DataVault\LQHL.dir\test.dir\20251103_LD_SQ.dir"


def post(base, path, body, timeout=1800):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(url, timeout=60):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def qwf_iqraw():
    return f"""workflow readout_iqraw {{
  meta {{ name "readout_iqraw"  description "读点可见度检查"  tags [readout, iqraw] }}
  env {{ qubit "{BIT}"  session "{SESSION}" }}
  budget {{ max_wall 600  max_writes 1  max_datasets 1  max_scan_points 20 }}
  goal {{ objective "iqraw 数据"  success {{ metric $c1.dataset  operator >=  value 0 }}  max_rounds 1  on_achieved "continue" }}
  measure as c1 {{ fn sq.iqraw  qubit $env.qubit  timeout 400  note "iqraw"  args {{ update false }} }}
  say "dataset=${{c1.dataset}}"
}}
"""


def run(wid, ir, dry=False):
    launch = post(BASE, "/workflows/run_ir", {"ir": ir, "context": {"qubit": BIT, "session": SESSION},
                                              "workflowId": wid, "dryRun": dry})
    if not launch.get("ok"):
        raise RuntimeError(launch)
    t0 = time.time()
    while time.time() - t0 < 900:
        time.sleep(3)
        st = post(BASE, "/workflows/status", {"workflowId": wid})
        if st.get("status") in ("completed", "failed", "cancelled"):
            nodes = st.get("nodes") or {}
            if isinstance(nodes, list):
                nodes = {str(n.get("id")): n for n in nodes}
            ds = None
            for n in nodes.values():
                ds = n.get("dataset_id") or (n.get("result") or {}).get("dataset_id") or ds
            return st, ds
    return {"status": "timeout"}, None


def visibility(ds):
    p = glob.glob(os.path.join(ROOT, "%05d - *IQraw*" % ds))
    if not p:
        return None
    a = None
    for _ in range(10):          # DataVault 写入方常持锁，重试
        try:
            with h5py.File(p[0], "r") as h:
                a = h["DataVault"][:]
            break
        except OSError:
            time.sleep(10)
    if a is None:
        return {"ds": ds, "error": "locked"}
    c0 = np.column_stack([a["f1"], a["f2"]]).astype(float)
    c1 = np.column_stack([a["f3"], a["f4"]]).astype(float)
    m0, m1 = c0.mean(0), c1.mean(0)
    d = float(np.linalg.norm(m1 - m0))
    s0 = float(np.sqrt(np.mean(((c0 - m0) ** 2).sum(1))))
    s1 = float(np.sqrt(np.mean(((c1 - m1) ** 2).sum(1))))
    return {"ds": ds, "sep": round(d, 1), "rms0": round(s0, 1), "rms1": round(s1, 1),
            "vis": round(d / (d + 2 * (s0 + s1)), 4) if d + 2 * (s0 + s1) else 0.0}


def main():
    center = float(sys.argv[1]) if len(sys.argv) > 1 else 6.63340
    powers = [float(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["-32"])]
    offsets = [float(x) for x in (sys.argv[3:] or ["-0.00006", "0", "0.00006"])]
    text = qwf_iqraw()
    ir = post(BASE, "/workflows/parse_qwf", {"text": text})
    assert ir.get("ok"), ir
    val = post(BASE, "/workflows/validate", {"ir": ir["ir"]})
    assert val.get("ok"), val
    ir = ir["ir"]
    print("center", center, "powers", powers, "offsets", offsets, flush=True)
    for pwr in powers:
        post(Q, "/qubit/set_params", {"name": BIT, "params": {"ReadIn.power": pwr}})
        for off in offsets:
            fread = round(center + off, 6)
            post(Q, "/qubit/set_params", {"name": BIT, "params": {"fread": fread}})
            wid = f"wf_iqscan_{int(time.time() * 1000) % 10 ** 9}"
            st, ds = run(wid, ir)
            info = visibility(ds) if ds else None
            print("power %+6.1f dB  off %+8.0f kHz  fread %.6f  status %s  %s"
                  % (pwr, off * 1000, fread, st.get("status"), info), flush=True)


if __name__ == "__main__":
    main()
