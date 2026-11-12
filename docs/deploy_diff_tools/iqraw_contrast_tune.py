"""Iterative readout-contrast tuning: iqraw -> piamp -> iqraw -> pidf -> iqraw -> piamp.

Why
---
`q2ld3` currently shows only ~0.22 contrast (P(1|prep X) - P(1|prep I)), which is above
the 0.2 "good enough to start from" line but far below the lab's 0.4 threshold.  The
recipe is to alternate:

* `sq.piamp`  - re-optimise the pi-pulse amplitude against the current readout
* `sq.iqraw`  - re-measure the IQ clouds and let it refresh the discriminator
* `sq.pidf`   - small frequency-offset scan (df), pick the drive frequency with the
                largest contrast -> this is what actually re-centres `f10`

Every `iqraw` run is scored here the same way the lab's `qubit_sensing/tools/calibrate.py
stage_check` does it: project the shots onto the discriminator axis, threshold at the
mid-point of center0/center1, then report P(1|prep I), P(1|prep X) and their difference.

Everything is executed *through the quantum microservice* (`POST :3003/execute`) so the
service's own LabRAD lock is respected; the datasets are read back over a separate
LabRAD connection.  `do_plot=False` everywhere - the service runs without a display and
a GUI backend would wedge it.

Usage
-----
    python docs/deploy_diff_tools/iqraw_contrast_tune.py --qubit q2ld3
    python docs/deploy_diff_tools/iqraw_contrast_tune.py --qubit q2ld3 --rounds 2
    python docs/deploy_diff_tools/iqraw_contrast_tune.py --qubit q2ld3 --dry-run

Options
-------
--df      half-width of the `pidf` frequency scan, in GHz (default 0.03 -> +/-30 MHz)
--timeout per-step HTTP timeout in seconds (default 900)
--rounds  how many times to replay the whole 6-step sequence (default 1)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Agentic Workflow", "qmclaw-server"))
sys.path.insert(0, SERVER_DIR)

QUANTUM_URL = "http://127.0.0.1:3003"
SESSION = ["", "LQHL", "test", "20251103_LD_SQ"]


# --------------------------------------------------------------------------- HTTP
def execute(code: str, timeout: int) -> dict:
    """POST /execute on the quantum service and return the decoded JSON."""
    payload = json.dumps({"code": code, "timeout": timeout}).encode()
    req = urllib.request.Request(
        QUANTUM_URL + "/execute", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
        return json.loads(resp.read().decode(errors="replace"))


def dataset_from(stdout: str):
    m = re.search(r"dataset:\s*(\d+)", stdout or "")
    return int(m.group(1)) if m else None


def latest_dataset(cxn, kind: str = "IQraw"):
    """Highest-numbered dataset whose name contains `kind` (e.g. 'IQraw').

    `sq.iqraw` does not print "dataset: N" (only the tuning experiments such as
    spectroscopy do), so the number has to be read back from the DataVault listing.
    """
    best = None
    cxn.data_vault.cd(SESSION)            # the raw connection starts at ['', 'LQHL']
    _dirs, files = cxn.data_vault.dir()   # (subdirectories, dataset files)
    for entry in files:
        entry = str(entry)
        m = re.match(r"(\d+)\s*-\s*(.+)$", entry)
        if not m:
            continue
        num, name = int(m.group(1)), m.group(2)
        if kind.lower() in name.lower() and (best is None or num > best[0]):
            best = (num, name)
    return best


# ------------------------------------------------------------------- LabRAD reads
def _connect():
    from services.common.config import labrad_connect

    return labrad_connect()


def read_regs(cxn, qubit: str) -> dict:
    r = cxn.registry
    base = SESSION + [qubit]
    out = {}

    def _get(path, key):
        try:
            r.cd(path)
            return r.get(key)
        except Exception:
            return None

    out["f10"] = _get(base, "f10")
    out["f21"] = _get(base, "f21")
    out["pi_amp"] = _get(base + ["PiGate"], "amp")
    out["pi_len"] = _get(base + ["PiGate"], "length")
    c0 = _get(base + ["discriminator"], "center0")
    c1 = _get(base + ["discriminator"], "center1")
    if c0 is not None and c1 is not None:
        out["center0"] = np.asarray(c0, dtype=float)
        out["center1"] = np.asarray(c1, dtype=float)
    return out


def load_shots(cxn, num: int) -> np.ndarray:
    from lqms.data_process import dataAnalysisCore as dc

    data = dc.DataLab(SESSION, cxn.data_vault, dv_type="data_vault")
    data.loadDataset(num)
    return np.asarray(data.data)


def score(a: np.ndarray, c0: np.ndarray, c1: np.ndarray) -> dict:
    """Contrast on the discriminator axis + the raw cloud separation."""
    br_i = a[:, [1, 2]]   # Is|I, Qs|I  (prepare ground state)
    br_x = a[:, [3, 4]]   # Is|X, Qs|X  (prepare excited state)

    u = (c1 - c0) / np.linalg.norm(c1 - c0)
    thr = 0.5 * ((c0 + c1) @ u)
    p1_i = float(np.mean(br_i @ u > thr))
    p1_x = float(np.mean(br_x @ u > thr))

    sep = float(np.linalg.norm(br_x.mean(0) - br_i.mean(0)))
    sig = float(np.sqrt((br_i.std(0) ** 2 + br_x.std(0) ** 2) / 2).mean())
    return {
        "p1_i": p1_i,
        "p1_x": p1_x,
        "contrast": abs(p1_x - p1_i),
        "sep": sep,
        "sigma": sig,
        "snr": sep / sig if sig else 0.0,
        "shots": int(len(a)),
    }


def measure(cxn, qubit: str, num: int) -> dict:
    regs = read_regs(cxn, qubit)
    a = load_shots(cxn, num)
    if "center0" not in regs:
        raise RuntimeError("discriminator centers missing from registry")
    s = score(a, regs["center0"], regs["center1"])
    s.update({"f10": regs.get("f10"), "pi_amp": regs.get("pi_amp"), "dataset": num})
    return s


# ------------------------------------------------------------------------ driver
def steps_for(qubit: str, df: float) -> list[tuple[str, str]]:
    q = qubit
    return [
        ("iqraw", f"sq.iqraw({q}, do_plot=False)"),
        ("piamp", f"sq.piamp({q}, do_plot=False)"),
        ("iqraw", f"sq.iqraw({q}, do_plot=False)"),
        ("pidf", f"sq.pidf({q}, df={df}, do_plot=False)"),
        ("iqraw", f"sq.iqraw({q}, do_plot=False)"),
        ("piamp", f"sq.piamp({q}, do_plot=False)"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qubit", default="q2ld3")
    ap.add_argument("--df", type=float, default=0.03, help="pidf half-width, GHz")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    steps = steps_for(args.qubit, args.df)
    if args.dry_run:
        for i, (name, code) in enumerate(steps, 1):
            print(f"{i}. {name:6s} {code}")
        return 0

    cxn = _connect()
    history = []
    try:
        print(f"== baseline registers ({args.qubit}) ==")
        regs = read_regs(cxn, args.qubit)
        print("   f10=%.9f  pi_amp=%.6f  pi_len=%s" % (regs["f10"], regs["pi_amp"], regs.get("pi_len")))

        for rnd in range(1, args.rounds + 1):
            print(f"\n===== round {rnd}/{args.rounds} =====")
            for idx, (name, code) in enumerate(steps, 1):
                t0 = time.time()
                print(f"[{idx}/{len(steps)}] {name}: {code}", flush=True)
                try:
                    resp = execute(code, args.timeout)
                except Exception as exc:
                    print(f"    !! request failed: {type(exc).__name__}: {exc}")
                    return 2
                dt = time.time() - t0
                status = resp.get("status")
                err = (resp.get("error") or "").strip()
                print(f"    -> {status} in {dt:.1f}s" + (f" | error: {err[:300]}" if err else ""))
                if status != "success":
                    tail = (resp.get("stderr") or "")[-600:]
                    print(f"    stderr tail: {tail}")
                    return 2

                if name == "iqraw":
                    found = latest_dataset(cxn, "IQraw")
                    num = dataset_from(resp.get("stdout", "")) or (found[0] if found else None)
                    if num is None:
                        print("    !! could not determine dataset number")
                        continue
                    m = measure(cxn, args.qubit, num)
                    history.append(m)
                    print(
                        "    %s  contrast=%.3f  P1(I)=%.3f  P1(X)=%.3f  SNR=%.2f  f10=%.9f  pi_amp=%.6f  ds=%d"
                        % ((found[1] if found and found[0] == num else ""),
                           m["contrast"], m["p1_i"], m["p1_x"], m["snr"],
                           m["f10"], m["pi_amp"], num)
                    )
                else:
                    regs = read_regs(cxn, args.qubit)
                    print("    now: f10=%.9f  pi_amp=%.6f" % (regs["f10"], regs["pi_amp"]))
    finally:
        try:
            cxn.disconnect()
        except Exception:
            pass

    print("\n===== contrast history =====")
    print(f"{'#':>2} {'dataset':>7} {'contrast':>8} {'p1_I':>6} {'p1_X':>6} {'snr':>5} {'f10':>14} {'pi_amp':>8}")
    for i, m in enumerate(history, 1):
        print(f"{i:>2} {m['dataset']:>7} {m['contrast']:>8.3f} {m['p1_i']:>6.3f} {m['p1_x']:>6.3f} "
              f"{m['snr']:>5.2f} {m['f10']:>14.9f} {m['pi_amp']:>8.5f}")
    if history:
        best = max(history, key=lambda m: m["contrast"])
        print(f"\nbest contrast {best['contrast']:.3f} at dataset {best['dataset']} "
              f"(f10={best['f10']:.9f}, pi_amp={best['pi_amp']:.6f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
