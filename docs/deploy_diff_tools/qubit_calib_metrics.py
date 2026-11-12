"""One-shot single-qubit calibration + metrics for a qubit (default ``q2ld3``).

Pipeline
--------
1. **frequency**  ``sq.ramsey_df``  - Ramsey fringe with fixed detuning, re-centres ``f10``
2. **pi pulse**   ``sq.set_pi``     - fine-tunes ``PiGate.amp`` / ``PiGate.alpha``
3. **metrics**    four core numbers, each measured by its own experiment:

   ============  ==========================================  ==================
   metric        experiment                                  how it is scored
   ============  ==========================================  ==================
   态区分度       ``sq.iqraw``                                discriminator axis
   T1            ``sq.t1(..., zpa=<fixed>)``                 offset exponential
   T2*           ``sq.ramsey_t2``                            ``QubitUpdater``
   XEB 保真度     ``sq.xeb``                                 ``process_xeb``
   ============  ==========================================  ==================

4. **plots**       every dataset above is rendered through the *analysis* service
                   (``POST :3004/plot``), never in the quantum service, and by
                   lqms' own drawing code (``QubitUpdater.fitData`` ->
                   ``fitFuncList`` for iqraw / T1 / Ramsey, ``px.XEB`` for XEB).

Why the T1 plot needs a parameter patch
---------------------------------------
``data_process.T1`` reads ``data.parameters['z pulse amplitude']`` -- the x-axis of
a 2-D zpa x delay scan.  A 1-D T1 taken with a fixed ``zpa=`` has no such key, so
the native fit dies with ``KeyError: 'z pulse amplitude'``.  The plot command below
injects that key from ``--t1-zpa`` before calling ``qter.fitData``; the native fit
then agrees with our own fixed-offset fit (71.1 us on dataset 599).

Why ``zpa=0`` for T1
--------------------
``sq.t1`` accepts ``zpa`` as a float, a list, or a list of lists.  With the default
(``None``) it runs a **2-D zpa x delay scan**, which takes forever on this setup.
Passing ``zpa=<fixed>`` collapses it to a single 1-D relaxation trace.

Why ``do_plot=False`` everywhere
--------------------------------
The quantum service runs head-less: any experiment that opens a matplotlib GUI from
its worker thread wedges the process (see ``.claude/progress.md``, 2026-11-03).  All
plotting is delegated to the analysis service, which uses the ``Agg`` backend.

Usage
-----
    python docs/deploy_diff_tools/qubit_calib_metrics.py                 # full run
    python docs/deploy_diff_tools/qubit_calib_metrics.py --skip-freq --skip-pi
    python docs/deploy_diff_tools/qubit_calib_metrics.py --only iqraw,t1
    python docs/deploy_diff_tools/qubit_calib_metrics.py --dry-run

Options
-------
--qubit     qubit name (default q2ld3)
--t1-zpa    fixed zpa for the T1 scan (default 0.0)
--xeb-k      maximum Clifford length for XEB (default 30)
--xeb-stats shots per XEB sequence (default 300)
--timeout   per-step HTTP timeout in seconds (default 1800)
--skip-freq / --skip-pi / --only / --dry-run
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

os.environ.setdefault("MPLBACKEND", "Agg")

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Agentic Workflow", "qmclaw-server"))
sys.path.insert(0, SERVER_DIR)

QUANTUM_URL = "http://127.0.0.1:3003"
ANALYSIS_URL = "http://127.0.0.1:3004"
SESSION = ["", "LQHL", "test", "20251103_LD_SQ"]


# --------------------------------------------------------------------------- HTTP
def _post(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode(errors="replace"))


def execute(code: str, timeout: int) -> dict:
    """Run experiment code inside the quantum service (respects its LabRAD lock)."""
    return _post(QUANTUM_URL + "/execute", {"code": code, "timeout": timeout}, timeout + 60)


def analysis_health() -> dict:
    try:
        with urllib.request.urlopen(ANALYSIS_URL + "/health", timeout=15) as resp:
            return json.loads(resp.read().decode(errors="replace"))
    except Exception as exc:  # pragma: no cover - only on a dead service
        return {"error": f"{type(exc).__name__}: {exc}"}


def ensure_analysis_connected() -> bool:
    """The analysis service drops to ``degraded_permanent`` after a failed start-up."""
    health = analysis_health()
    if health.get("datalab_connected"):
        return True
    print("    analysis not connected (%s) -> POST /connect" % health.get("fallback_state"))
    try:
        res = _post(ANALYSIS_URL + "/connect", {}, 180)
    except Exception as exc:
        print(f"    !! connect failed: {type(exc).__name__}: {exc}")
        return False
    print("    connect ->", res.get("message") or res.get("error"))
    return bool(res.get("success"))


def plot_dataset(num: int, command: str) -> str | None:
    """Render one dataset through the analysis service; returns the plot URL."""
    try:
        res = _post(ANALYSIS_URL + "/plot", {"command": command, "dataset_index": num}, 600)
    except Exception as exc:
        print(f"    !! plot request failed: {type(exc).__name__}: {exc}")
        return None
    if res.get("error"):
        print("    !! plot error:", res["error"])
        return None
    return res.get("plotUrl")


# ------------------------------------------------------------------- LabRAD reads
def _connect():
    from services.common.config import labrad_connect

    return labrad_connect()


def read_regs(cxn, qubit: str) -> dict:
    r = cxn.registry
    base = SESSION + [qubit]
    out: dict = {}

    def _get(path, key):
        try:
            r.cd(path)
            return r.get(key)
        except Exception:
            return None

    out["f10"] = _get(base, "f10")
    out["f21"] = _get(base, "f21")
    out["pi_amp"] = _get(base + ["PiGate"], "amp")
    out["pi_alpha"] = _get(base + ["PiGate"], "alpha")
    out["pi_len"] = _get(base + ["PiGate"], "length")
    c0 = _get(base + ["discriminator"], "center0")
    c1 = _get(base + ["discriminator"], "center1")
    if c0 is not None and c1 is not None:
        out["center0"] = np.asarray(c0, dtype=float)
        out["center1"] = np.asarray(c1, dtype=float)
    return out


def latest_dataset(cxn, kind: str):
    """Highest-numbered dataset whose name contains ``kind``.

    Only the spectroscopy-style experiments print ``dataset: N``; everything else has
    to be recovered from the DataVault listing, which returns ``(dirs, files)`` where
    the connection must already sit inside the session folder.
    """
    best = None
    dv = cxn.data_vault
    dv.cd(SESSION)
    _dirs, files = dv.dir()
    for entry in files:
        m = re.match(r"(\d+)\s*-\s*(.+)$", str(entry))
        if not m:
            continue
        num, name = int(m.group(1)), m.group(2)
        if kind.lower() in name.lower() and (best is None or num > best[0]):
            best = (num, name)
    return best


def dataset_name(cxn, num: int) -> str:
    dv = cxn.data_vault
    dv.cd(SESSION)
    _dirs, files = dv.dir()
    for entry in files:
        m = re.match(r"(\d+)\s*-\s*(.+)$", str(entry))
        if m and int(m.group(1)) == num:
            return m.group(2)
    return f"dataset {num}"


def load_dataset(cxn, num: int):
    from lqms.data_process import dataAnalysisCore as dc

    data = dc.DataLab(SESSION, cxn.data_vault, dv_type="data_vault")
    data.loadDataset(num)
    return data


def raw_arrays(cxn, num: int):
    """Raw columns plus a *column-aligned* name list.

    ``dv.get()`` stacks the independent variables first and the dependent ones
    after, so ``dep.index(name)`` alone is off by ``len(indep)`` -- indexing the
    delay column instead of ``P1 | I`` was silently fitting the ground branch.
    The returned names are therefore ``indep + dep`` in the same order as the
    columns of ``a``.
    """
    dv = cxn.data_vault
    dv.cd(SESSION)
    dv.open(num)
    indep, dep = dv.variables()
    cols = [v[0] for v in indep] + [v[0] for v in dep]
    return cols, np.asarray(dv.get())


# ----------------------------------------------------------------------- metrics
def score_iqraw(a: np.ndarray, c0: np.ndarray, c1: np.ndarray) -> dict:
    """Contrast on the current discriminator axis (same rule as calibrate.py)."""
    br_i = a[:, [1, 2]]   # Is|I, Qs|I
    br_x = a[:, [3, 4]]   # Is|X, Qs|X
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
        "snr": sep / sig if sig else 0.0,
        "shots": int(len(a)),
    }


def fit_t1(t_ns: np.ndarray, p_excited: np.ndarray, p_ground: np.ndarray | None = None) -> dict:
    """``a*exp(-t/T1) + c`` with ``c`` pinned to the measured ground floor.

    Letting ``c`` float on 16 points lets the fit soak up the slow drift of the
    readout and collapse the amplitude (a 417 us / amp~0 fit was reported once).
    The ground branch ``P1|I`` is the same readout with no pi pulse, so its mean
    is the right ``c``; that turns into a two-parameter fit that is well posed.
    Also returns the free-offset and log-linear estimates as a cross-check.
    """
    from scipy.optimize import curve_fit

    t = t_ns / 1000.0
    c = float(np.mean(p_ground)) if p_ground is not None and len(p_ground) else float(p_excited.min())
    out = {"ground_floor": c}
    try:
        popt, _ = curve_fit(
            lambda x, a, T: a * np.exp(-x / T),
            t, p_excited - c,
            p0=[max(float(p_excited[0] - c), 0.05), max(float(t.max()) / 2, 5.0)],
            maxfev=20000,
            bounds=([0, 1e-3], [2, 1e6]),
        )
        out.update(t1_us=float(popt[1]), amp=float(popt[0]), method="fixed-offset")
    except Exception as exc:  # pragma: no cover - degraded fit
        out.update(t1_us=float("nan"), error=f"{type(exc).__name__}: {exc}")

    # cross-checks (best effort, do not overwrite the primary value)
    try:
        popt_s, _ = curve_fit(
            lambda x, a, T, b: a * np.exp(-((x / T) ** b)),
            t, p_excited - c,
            p0=[max(float(p_excited[0] - c), 0.05), 40.0, 1.0],
            maxfev=40000,
            bounds=([0, 1e-3, 0.2], [2, 1e6, 2.0]),
        )
        out["t1_stretched_us"] = float(popt_s[1])
        out["stretch_beta"] = float(popt_s[2])
    except Exception:
        pass
    try:
        popt3, _ = curve_fit(
            lambda x, a, T, cc: a * np.exp(-x / T) + cc,
            t, p_excited,
            p0=[max(float(p_excited.max() - p_excited.min()), 0.05), 40.0, c],
            maxfev=20000,
            bounds=([0, 1e-3, -1], [2, 1e6, 2]),
        )
        out["t1_free_us"] = float(popt3[1])
        out["offset_free"] = float(popt3[2])
    except Exception:
        pass
    try:
        m = (p_excited - c) > 0.1
        if m.sum() >= 3:
            out["t1_loglin_us"] = float(-1.0 / np.polyfit(t[m], np.log(p_excited[m] - c), 1)[0])
    except Exception:
        pass
    return out


def fit_t2(cxn, num: int) -> dict:
    """T2* from the Ramsey trace, via ``QubitUpdater.fitData`` (falls back to a fit).

    ``fitData`` returns ``(para, model)`` where ``para`` only carries
    ``{'variables': [...]}`` -- the fitted T2 lives in ``model[1][0]``
    (``model = [freq, T2, envelop, envelop_fit]``).  Checking ``"T2" in para``
    is always False, which used to force the manual fallback every time.
    """
    import contextlib
    import io

    from lqms.data_process import QubitUpdater

    data = load_dataset(cxn, num)
    qter = QubitUpdater(data, None)
    note = ""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = qter.fitData(num, collect=True)
        para, model = res if isinstance(res, tuple) and len(res) == 2 else (res, None)
        t2 = None
        if isinstance(para, dict):
            v = para.get("T2")
            if v is not None:
                t2 = float(np.asarray(v).ravel()[0])
        if t2 is None and isinstance(model, (list, tuple)) and len(model) > 1:
            arr = np.asarray(model[1]).ravel()
            if arr.size:
                t2 = float(arr[0])
        if t2 is not None and np.isfinite(t2) and t2 > 0:
            return {"t2_us": t2, "source": "qter", "note": note}
        note = "qter returned no usable T2"
    except Exception as exc:
        note = f"{type(exc).__name__}: {exc}"

    # fallback: damped cosine on P1
    from scipy.optimize import curve_fit

    cols, a = raw_arrays(cxn, num)
    t = a[:, 0] / 1000.0
    y = a[:, cols.index("P1")] if "P1" in cols else a[:, 2]
    y = (y - y.min()) / max(y.max() - y.min(), 1e-9)
    guess_f = 1.0 / max(2 * (t[1] - t[0]), 1e-6)
    try:
        popt, _ = curve_fit(
            lambda x, A, T, f, phi, c: A * np.exp(-x / T) * np.cos(2 * np.pi * f * x + phi) + c,
            t, y,
            p0=[0.5, float(t.max()) / 3, guess_f, 0.0, 0.5],
            maxfev=40000,
            bounds=([0, 1e-3, 0, -np.pi, 0], [2, 1e5, 5 * guess_f + 1, np.pi, 1]),
        )
        return {"t2_us": float(popt[1]), "source": "manual", "note": note}
    except Exception as exc:
        return {"t2_us": float("nan"), "error": f"{type(exc).__name__}: {exc}", "note": note}


def xeb_fidelity(cxn, num: int) -> dict:
    """Cross-entropy benchmarking: per-cycle polarisation ``p`` and Pauli error."""
    import contextlib
    import io

    from lqms.data_process.xeb import process_xeb as px

    data = load_dataset(cxn, num)
    with contextlib.redirect_stdout(io.StringIO()):
        out = px.XEB(data, [num], collect=True, do_plot=False)
    entry = out.get(num) if isinstance(out, dict) else None
    if not entry:
        return {"error": "no XEB result"}
    p = float(np.asarray(entry.get("p")).ravel()[0])
    err = float(np.asarray(entry.get("error_Pauli_per_cycle")).ravel()[0])
    fids = np.asarray(entry.get("XEB_fids"), dtype=float)
    return {
        "p": p,
        "error_pauli_per_cycle": err,
        # single-qubit gate fidelity per cycle (p is the depolarising parameter)
        "gate_fidelity": 1.0 - err,
        "fid_first": float(fids[0]) if fids.size else float("nan"),
        "fid_last": float(fids[-1]) if fids.size else float("nan"),
    }


# ------------------------------------------------------------------------ plots
# Every command below is executed by analysis_service inside
#   exec(plot_command, {"plt", "np", "x", "y", "fig", "qter", "data"})
# after `data.loadDataset(dataset_index)`, so `qter` / `data` / `plt` are in scope.
# Prefer lqms' own drawers (QubitUpdater.fitData -> fitFuncList, px.XEB) over
# hand-rolled matplotlib: they carry the fits, the axis conventions and the
# per-experiment annotations that the group actually reads.
PLOT_CMDS = {
    # 1D T1 (zpa pinned) has no 'z pulse amplitude' key, which data_process.T1
    # insists on (it is the x-axis of the 2D zpa x delay scan). Injecting it is
    # enough to get the native fit + plot, and the number it prints (T1 = 71.1 us
    # on dataset 599) matches our own fixed-offset fit exactly.
    "t1": (
        "data.parameters['z pulse amplitude'] = {t1_zpa}\n"
        "qter.fitData(do_plot=True)\n"
    ),
    "iqraw": (
        "qter.fitData(do_plot=True)\n"
        # The native IQraw panel puts its crimson dataset title in a fig.text at
        # y=0.96, i.e. *inside* the axes box, and analysis_service calls
        # plt.tight_layout() afterwards -- which does not reserve room for it, so the
        # title collides with the subplot titles.  Pushing it above the figure box
        # survives tight_layout and bbox_inches='tight' still captures it.
        "for t in plt.gcf().texts:\n"
        "    if t.get_position()[1] > 0.90:\n"
        "        t.set_position((0.5, 1.02)); t.set_va('bottom'); t.set_ha('center')\n"
    ),
    "t2": "qter.fitData(do_plot=True)\n",
    "xeb": (
        "from lqms.data_process.xeb import process_xeb as px\n"
        "px.XEB(data, [{exp_num}], collect=True, do_plot=True)\n"
    ),
}


def plot_step(kind: str, num: int, command: str | None = None) -> str | None:
    cmd = command if command is not None else PLOT_CMDS.get(kind, "")
    url = plot_dataset(num, cmd)
    if url:
        print(f"    plot -> {url}")
    return url


# ----------------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qubit", default="q2ld3")
    ap.add_argument("--t1-zpa", type=float, default=0.0)
    ap.add_argument("--xeb-k", type=int, default=30)
    ap.add_argument("--xeb-stats", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--skip-freq", action="store_true")
    ap.add_argument("--skip-pi", action="store_true")
    ap.add_argument("--only", default="", help="comma list: iqraw,t1,t2,xeb")
    ap.add_argument("--out", default="", help="output JSON path (default: workspace root)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    q = args.qubit
    only = {s.strip().lower() for s in args.only.split(",") if s.strip()}

    plan: list[tuple[str, str]] = []
    if not args.skip_freq and not only:
        plan.append(("freq", f"sq.ramsey_df({q}, do_plot=False)"))
    if not args.skip_pi and not only:
        plan.append(("pi", f"sq.set_pi({q}, do_plot=False)"))

    metrics = [
        ("iqraw", f"sq.iqraw({q}, do_plot=False)"),
        ("t1", f"sq.t1({q}, zpa={args.t1_zpa}, do_plot=False)"),
        ("t2", f"sq.ramsey_t2({q}, do_plot=False)"),
        ("xeb", f"sq.xeb({q}, k={args.xeb_k}, stats={args.xeb_stats})"),
    ]
    if only:
        metrics = [m for m in metrics if m[0] in only]
    plan.extend(metrics)

    if args.dry_run:
        for i, (name, code) in enumerate(plan, 1):
            print(f"{i}. {name:6s} {code}")
        return 0

    print(f"== {q}: {len(plan)} step(s) ==")
    cxn = _connect()
    results: dict = {"qubit": q, "metrics": {}, "datasets": {}, "plots": {}}
    try:
        regs = read_regs(cxn, q)
        print("   start: f10=%.9f  pi_amp=%.6f  pi_alpha=%.6f"
              % (regs["f10"], regs["pi_amp"], regs["pi_alpha"]))

        for idx, (name, code) in enumerate(plan, 1):
            print(f"\n[{idx}/{len(plan)}] {name}: {code}", flush=True)
            t0 = time.time()
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
                print("    stderr tail:", (resp.get("stderr") or "")[-600:])
                return 2
            results.setdefault("timing", {})[name] = round(dt, 1)

            if name in ("freq", "pi"):
                r2 = read_regs(cxn, q)
                print("    now: f10=%.9f  pi_amp=%.6f  pi_alpha=%.6f"
                      % (r2["f10"], r2["pi_amp"], r2["pi_alpha"]))
                results.setdefault("steps", {})[name] = {
                    "f10": r2["f10"], "pi_amp": r2["pi_amp"], "pi_alpha": r2["pi_alpha"],
                }
                continue

            # ---- metric experiments -------------------------------------------------
            if name == "iqraw":
                found = latest_dataset(cxn, "IQraw")
            elif name == "t1":
                found = latest_dataset(cxn, "T1")
            elif name == "t2":
                found = latest_dataset(cxn, "Ramsey")
            else:
                found = latest_dataset(cxn, "XEB")
            if not found:
                print(f"    !! no dataset found for {name}")
                continue
            num, ds_name = found
            print(f"    dataset {num}: {ds_name}")
            results["datasets"][name] = num

            regs = read_regs(cxn, q)
            if name == "iqraw":
                m = score_iqraw(load_dataset(cxn, num).data, regs["center0"], regs["center1"])
                print("    contrast=%.3f  P1(I)=%.3f  P1(X)=%.3f  SNR=%.2f"
                      % (m["contrast"], m["p1_i"], m["p1_x"], m["snr"]))
            elif name == "t1":
                cols, a = raw_arrays(cxn, num)
                ix = cols.index("P1 | X") if "P1 | X" in cols else a.shape[1] - 1
                ig = cols.index("P1 | I") if "P1 | I" in cols else a.shape[1] - 2
                m = fit_t1(a[:, 0], a[:, ix], a[:, ig])
                print("    T1 = %.1f us  (amp=%.3f  ground floor=%.3f  "
                      "[free-offset %.1f, log-lin %.1f, stretched %.1f @ beta %.2f])"
                      % (m.get("t1_us", float("nan")), m.get("amp", float("nan")),
                         m.get("ground_floor", float("nan")),
                         m.get("t1_free_us", float("nan")), m.get("t1_loglin_us", float("nan")),
                         m.get("t1_stretched_us", float("nan")), m.get("stretch_beta", float("nan"))))
            elif name == "t2":
                m = fit_t2(cxn, num)
                print("    T2* = %.2f us  (%s)" % (m.get("t2_us", float("nan")), m.get("source", "?")))
            else:
                m = xeb_fidelity(cxn, num)
                print("    XEB p=%.4f  Pauli error/cycle=%.5f  gate fidelity=%.4f"
                      % (m.get("p", float("nan")), m.get("error_pauli_per_cycle", float("nan")),
                         m.get("gate_fidelity", float("nan"))))
            m["f10"] = regs["f10"]
            m["pi_amp"] = regs["pi_amp"]
            results["metrics"][name] = m

            # ---- plotting -----------------------------------------------------------
            # All four figures are drawn by lqms itself (QubitUpdater.fitData ->
            # fitFuncList for t1/iqraw/ramsey, px.XEB for xeb) so the panels match
            # what the group reads in lab notebooks.
            if ensure_analysis_connected():
                cmd = PLOT_CMDS.get(name, "qter.fitData({exp_num})").format(
                    exp_num=num, t1_zpa=args.t1_zpa)
                url = plot_step(name, num, cmd)
                if url:
                    results["plots"][name] = url
    finally:
        try:
            cxn.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------ summary
    regs = {}
    try:
        cxn2 = _connect()
        regs = read_regs(cxn2, q)
        cxn2.disconnect()
    except Exception:
        pass

    print("\n===== summary (%s) =====" % q)
    if regs:
        print("  f10      = %.9f GHz" % regs["f10"])
        print("  PiGate   = amp %.6f / alpha %.6f / length %s"
              % (regs["pi_amp"], regs["pi_alpha"], regs.get("pi_len")))
    mm = results["metrics"]
    if "iqraw" in mm:
        # ASCII label: this interpreter's stdout is gbk+surrogateescape, so a CJK
        # label here turns into mojibake as soon as the output is piped.
        print("  contrast = %.3f  (P1(I)=%.3f  P1(X)=%.3f)" % (
            mm["iqraw"]["contrast"], mm["iqraw"]["p1_i"], mm["iqraw"]["p1_x"]))
    if "t1" in mm:
        print("  T1       = %.1f us" % mm["t1"].get("t1_us", float("nan")))
    if "t2" in mm:
        print("  T2*      = %.2f us" % mm["t2"].get("t2_us", float("nan")))
    if "xeb" in mm:
        print("  XEB      = p %.4f, Pauli error/cycle %.5f, gate fidelity %.4f" % (
            mm["xeb"].get("p", float("nan")),
            mm["xeb"].get("error_pauli_per_cycle", float("nan")),
            mm["xeb"].get("gate_fidelity", float("nan"))))
    if results["plots"]:
        print("  plots:")
        for k, v in results["plots"].items():
            print(f"    {k:6s} {v}")

    # ``docs/`` is read-only in this workspace (PermissionError on create), so the
    # JSON goes to the workspace root unless --out says otherwise.
    out = args.out or os.path.join(
        os.path.abspath(os.path.join(HERE, "..", "..")),
        f"calib_metrics_{q}_{time.strftime('%Y%m%d_%H%M%S')}.json",
    )
    try:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
        print(f"\nwrote {out}")
    except Exception as exc:
        print(f"\n!! could not write {out}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
