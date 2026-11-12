"""qwf_auto_calib.py — 把「自动化单比特标定」流水线改写成 .qwf 驱动（2026-11-10）。

原流水线（zpa_calibration_pipeline.py）在 Python 里直接 import lqms / 直连 LabRAD 逐点
`qobj.regs.bias_z = bias` 再调 sq.*。按仓库规约（AGENTS.md 约定 11/12/13）真机一律：
    读/写寄存器 -> POST :3003/qubit/registry / qubit/set_params
    跑测控实验 -> POST :3008/workflows/run_ir（.qwf 文本 -> parse_qwf -> validate -> run_ir）
    指标       -> 只读 DataVault 里的 hdf5（:3004 plot 留给人工）

动机（见 docs / skill §10）：`.qwf` 里**没有**写寄存器的节点（`apply` 只记建议值，
server.py:473 明确「暂不下发硬件」），所以 bias_z 这一段只能留在驱动里，用
:3003/qubit/set_params 写；每个 bias 点的实验序列则整体写成一个 .qwf，一次 run_ir 跑完。

流程（2026-11-11 版：S21ZPA 地图先行 + 两段式 probe）：
    1. 服务预检 + 寄存器快照 + 驱动自检（PiGate.length 必须 == PiHalf.length，piamp/pidf 用的是 PiHalf）
    2. 第一个实验就是 s21_zpa2d（qf 里 zpa range -3 3 0.2，只采不写）-> 数据集
       -> 从中找 1dB 以上凹陷的可靠区间 -> 从区间最小值起、0.2 步长升序铺 bias 候选
    3. 逐 bias 两段式（每个 bias 写一次 bias_z，再各跑一个 qwf）：
       3a 全窗 spectroscopy：`np.arange(2, 4, 0.005)`（400 点，不看寄存器 f10）
          -> 挑离基线最远的点当候选 -> 候选点 ±0.1 GHz / 0.5 MHz 细扫
          -> Lorentzian 拟合 + 判据 1–6，判「是不是比特频率」
       3b 只有 3a 通过才跑 iqraw + piamp -> visibility>=0.05 且 snr>=0.3 且 有 Pi 激发
       3c 通过 -> 选中该 bias（并把谱线给出的 f0 写进 f10/f21），停止遍历
    4. 链前定频确认（2 次复扫 + 功率展宽）：measure_f10 过判据才写 f10
    5. 标定链（一个 chain.qwf）：pidf(df=.1) -> pidf -> ramsey_df -> s21_dis -> iqraw
       -> set_pi(X) -> set_pi(X/2) -> t1(zpa=b*) -> iqraw（全部 update false）
    6. 结果写 out/<bit>_qwf_result.json + events.jsonl + summary.csv

用法：
    python qwf_auto_calib.py --plan                 # 只做离线规划（完全不碰硬件）
    python qwf_auto_calib.py --dry                  # 生成 .qwf + parse/validate + dryRun
    python qwf_auto_calib.py --execute              # 真跑（需人在场授权，见 skill §13.3）
    python qwf_auto_calib.py --execute --max-bias-points 4 --no-chain
    python qwf_auto_calib.py --execute --skip-zpa2d --s21zpa <已有 hdf5>   # 复用旧地图

安全：--execute 之外任何模式都不会写寄存器、不会下发实验。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

import h5py
import numpy as np
from scipy.signal import find_peaks

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = "http://127.0.0.1:3008"
Q_BASE = "http://127.0.0.1:3003"
HERE = Path(__file__).resolve().parent
DEFAULT_DATAVAULT = Path(r"D:\DataVault\LQHL.dir\test.dir")
DEFAULT_BIT = "q7ld3"

# 与原脚本 Settings 对齐的默认值
SPEC_AMP = 0.1
SPEC_HALF_SPAN = 0.1      # 细扫只看候选频率 ±0.1 GHz（原脚本 5.5->4.0 是另一台设备的口径）
SPEC_STEP = 0.002
# 宽扫：每个 bias 先跑一遍 2–4 GHz / 5 MHz 的全窗，先把谱线找出来，再局部细扫定 FWHM
WIDE_SCAN_LO = 2.0
WIDE_SCAN_HI = 4.0
WIDE_SCAN_STEP = 0.005
WIDE_SCAN_AMP = 0.3
WIDE_SCAN_MIN_CONTRAST_DB = 1.0
PIAMP_AMP = 2.0
BIAS_STEP = 0.2
VISIBILITY_THRESHOLD = 0.05
T1_DELAY_STOP_NS = 84000.0
T1_DELAY_STEP_NS = 6000.0

# 名字合法的比特标识：q7ld3 / q4ld3 / q5_1 ...（原 plan_zpa_bias.py 的 r'q\d+_\d+' 是个 bug）
_BIT_RE = re.compile(r"(q[0-9A-Za-z]+)")


# ───────────────────────────── HTTP ─────────────────────────────
def post(base, path, payload, timeout=120):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def get(base, path, timeout=30):
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, (list, tuple)):
        out[prefix[:-1]] = list(obj)
    else:
        out[prefix[:-1] if prefix else None] = obj
    return out


def read_registry(bit):
    """只读：摊平 registry。返回 (dict, error)。"""
    data = post(Q_BASE, "/qubit/registry", {"name": bit}, timeout=30)
    if data.get("error"):
        return {}, data["error"]
    return flatten(data.get("registry") or {}), None


def write_params(bit, params):
    """写寄存器（受 :3003 allow_set_params 闸门约束）。

    坑（2026-11-12）：`discriminator.center0/center1` 必须写成**数值数组**，不能写读回来的字符串。
    写字符串进去之后，厂商 ReadoutProcessor.iq2prob 会在 `_process_signals` 里把字符串当序列
    乘复数，报 `TypeError: can't multiply sequence by non-int of type 'complex'`，
    结果是 spectroscopy / piamp 全部 failed（iqraw 不受影响，所以很容易误判成"硬件坏了"）。
    """
    fixed = {}
    for k, v in params.items():
        if k.startswith("discriminator.center") and isinstance(v, str):
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", v)
            if len(nums) >= 2:
                v = [float(nums[0]), float(nums[1])]
        fixed[k] = v
    return post(Q_BASE, "/qubit/set_params", {"name": bit, "params": fixed}, timeout=60)


def _centers(reg):
    """registry 里 discriminator 的 center0/center1 字符串 -> np.array。"""
    out = []
    for k in ("discriminator.center0", "discriminator.center1"):
        v = reg.get(k)
        if isinstance(v, str):
            nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", v)
            out.append(np.array([float(t) for t in nums[:2]]) if len(nums) >= 2 else None)
        elif isinstance(v, (list, tuple)) and len(v) >= 2:
            out.append(np.array([float(v[0]), float(v[1])]))
        else:
            out.append(None)
    return out


def preflight_params(bit, reg, datavault, log, allow_length_mismatch=False):
    """§三-①：驱动链路参数自检。返回 {ok, issues, length, discriminator_ok, ...}。"""
    issues, warns = [], []
    pg = float(reg.get("PiGate.length") or 0)
    ph = float(reg.get("PiHalf.length") or 0)
    # 两条硬规则（2026-11-12 用户确认）：
    #   ① PiGate.length 必须等于 PiHalf.length —— piamp/pidf 实际用的是 PiHalf
    #      （q8ld3 的坑：PiGate 改成 30 而 PiHalf 仍是 210，结果怎么都激发不起来）；
    #   ② 两者都必须在 20–40 ns —— 本机所有比特的经验值都是 30 ns；
    #      300 ns 这类超长脉冲属于配置事故（q11ld3 就是 300 ns，π 面积严重不足）。
    # 任一不满足都中止，除非显式给 --allow-length-mismatch。
    length_ok = abs(pg - ph) < 1e-6
    if not length_ok:
        issues.append(f"PiGate.length={pg:g} 与 PiHalf.length={ph:g} 不一致"
                      "（piamp/pidf 用的是 PiHalf）")
    elif not 20 <= pg <= 40:
        issues.append(f"PiGate/PiHalf.length={pg:g}ns 不在 20–40ns 经验区间"
                      "（本机常规值 30 ns；超长脉冲 π 面积严重不足，会激发不起来）")
    for name in ("PiGate.alpha", "PiHalf.alpha"):
        a = reg.get(name)
        try:
            if a is not None and abs(float(a)) > 3:
                warns.append(f"{name}={a} 明显偏大（DRAG 与脉宽不匹配？）")
        except (TypeError, ValueError):
            warns.append(f"{name}={a} 不是数值")

    c0s, c1s = _centers(reg)
    disc_ok, disc_note = None, "无 discriminator 中心"
    try:
        hits = [p for p in Path(datavault).rglob(f"* - {bit}%c IQraw*.hdf5")
                if p.stat().st_size > 0]
        if hits:
            res = load_dv(max(hits, key=lambda p: p.stat().st_mtime))
            a = res["data"]
            m0 = np.array([np.median(a["f1"]), np.median(a["f2"])], float)
            m1 = np.array([np.median(a["f3"]), np.median(a["f4"])], float)
            s0 = float(np.median(np.hypot(a["f1"] - m0[0], a["f2"] - m0[1])))
            s1 = float(np.median(np.hypot(a["f3"] - m1[0], a["f4"] - m1[1])))
            sig = max(0.5 * (s0 + s1), 1e-9)
            d0 = float(np.linalg.norm(c0s - m0)) if c0s is not None else None
            d1 = float(np.linalg.norm(c1s - m1)) if c1s is not None else None
            worst = max([d for d in (d0, d1) if d is not None] or [0.0])
            disc_ok = worst <= 3 * sig
            disc_note = (f"最近 IQraw {Path(res['path']).name}：d0={None if d0 is None else round(d0)} "
                         f"d1={None if d1 is None else round(d1)} σ≈{round(sig)} → "
                         f"{'ok' if disc_ok else '失配（P1 列不可信，改用 amplitude 判据）'}")
    except Exception as exc:  # noqa: BLE001
        disc_note = f"discriminator 自检跳过：{exc}"
    if disc_ok is False:
        warns.append("discriminator 与当前 IQ 云失配：" + disc_note)

    for w in warns:
        log(f"  ! 预警：{w}")
    log(f"  驱动自检：PiGate.length={pg:g} PiHalf.length={ph:g} → {'ok' if length_ok else 'FAIL'}")
    log(f"  {disc_note}")
    return {"ok": length_ok or allow_length_mismatch, "issues": issues, "warns": warns,
            "pi_gate_length_ns": pg, "pi_half_length_ns": ph,
            "length_ok": length_ok, "discriminator_ok": disc_ok, "discriminator_note": disc_note}


# ─────────────────────── DataVault / 离线规划 ───────────────────────
def latest_s21zpa(root, bit):
    root = Path(root)
    hits = [p for p in root.rglob(f"* - {bit}%c S21*.hdf5")
            if "zpa" in p.name.lower() and p.stat().st_size > 0]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def dataset_file(root, dsid):
    root = Path(root)
    hits = list(root.rglob(f"{int(dsid):05d} - *"))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def load_dv(path):
    with h5py.File(path, "r") as h:
        ds = h["DataVault"]
        return {"path": str(path), "kind": "", "data": ds[()], "attrs": dict(ds.attrs)}


def load_dv_retry(path, tries=15, delay=12.0, log=None):
    """DataVault 刚写完时写入方仍持锁（Win32 GetLastError()=33），重试到能打开为止。"""
    last = None
    for i in range(tries):
        try:
            return load_dv(path)
        except OSError as exc:          # noqa: PERF203
            last = exc
            if log and i == 0:
                log(f"    DataVault 暂时被锁，重试读取：{Path(path).name}")
            time.sleep(delay)
    raise last


def plan_intervals(path, min_prominence_db=1.0, min_points=3):
    """S21ZPA 2D 图上找「凹陷深且连续」的 bias 区间（离线，无硬件）。

    与原 plan_zpa_bias.py 同思路，但修掉两个 bug：
      - `re.search(r'q\\d+_\\d+')` 对 q7ld3 直接 AttributeError；
      - 该正则原本还被写成了匹配字面反斜杠。
    """
    name = Path(path).name
    m = _BIT_RE.search(name.split(" - ")[-1].replace("%c", " "))
    bit = m.group(1) if m else Path(path).stem

    with h5py.File(path, "r") as h:
        ds = h["DataVault"]
        a = ds[()]
        at = dict(ds.attrs)

    f = np.unique(a["f0"])
    z = np.unique(a["f1"])
    grid = np.full((len(f), len(z)), np.nan)
    grid[np.searchsorted(f, a["f0"]), np.searchsorted(z, a["f1"])] = a["f2"]
    db = 20 * np.log10(np.where(grid > 0, grid, np.nan))

    points = []
    for j, bias in enumerate(z):
        valid = np.isfinite(db[:, j])
        k = int(np.argmin(db[:, j])) if valid.all() and len(f) >= 3 else None
        prom = 0.0
        if k is not None:
            ps, props = find_peaks(-db[:, j], prominence=0)
            if k in ps:
                prom = float(props["prominences"][np.where(ps == k)[0][0]])
        supported = (k is not None and 0 < k < len(f) - 1 and prom >= min_prominence_db)
        points.append({"bias": float(bias), "supported": supported, "f_index": k,
                       "prominence_db": prom,
                       "freq_native": float(f[k]) if k is not None else None})

    intervals = []
    start = None
    flags = [p["supported"] for p in points] + [False]
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        if not v and start is not None:
            end = i
            if end - start >= min_points:
                seg = points[start:end]
                freqs = np.array([p["freq_native"] for p in seg])
                zs = z[start:end]
                slopes = np.abs(np.gradient(freqs, zs)) / max(np.ptp(f), 1e-12)
                scores = []
                for k in range(1, len(seg) - 1):
                    edge = min(seg[k]["f_index"], len(f) - 1 - seg[k]["f_index"]) / max(len(f) - 1, 1)
                    scores.append(seg[k]["prominence_db"] / (1 + seg[k]["prominence_db"])
                                  + edge - 2 * slopes[k])
                best = int(np.argmax(scores)) + 1 if scores else len(seg) // 2
                intervals.append({
                    "interval": [float(z[start]), float(z[end - 1])],
                    "point_count": end - start,
                    "candidate_bias": seg[best]["bias"],
                    "candidate_freq_native": seg[best]["freq_native"],
                    "median_prominence_db": float(np.median([p["prominence_db"] for p in seg])),
                })
            start = None

    return {
        "file": Path(path).name,
        "bit": bit,
        "bias_axis": [float(z[0]), float(z[-1]), float(np.median(np.diff(z)))],
        "readout_axis": [float(f[0]), float(f[-1]), float(np.median(np.diff(f)))],
        "independent0_unit": str(at.get("Independent0.unit", "")) or "unconfirmed",
        "independent1_unit": str(at.get("Independent1.unit", "")) or "unconfirmed",
        "intervals": sorted(intervals, key=lambda q: (-q["point_count"], -q["median_prominence_db"])),
        "points": points,
    }


def bias_grid(intervals, step):
    """按区间起点铺 Decimal 网格（避免 0.1+0.2 的浮点尾巴）。"""
    out = []
    st = Decimal(str(step))
    for iv in intervals:
        lo, hi = (Decimal(str(x)) for x in iv["interval"])
        v = lo
        while v <= hi + Decimal("1e-9"):
            val = float(v)
            if all(abs(val - o) > 1e-9 for o in out):
                out.append(val)
            v += st
    return out


# ───────────────────────── 指标（只读 hdf5） ─────────────────────────
def iqraw_visibility(res):
    a = res["data"]
    names = a.dtype.names
    if not all(n in names for n in ("f1", "f2", "f3", "f4")):
        raise ValueError("IQraw 需要 f1..f4（Is|I,Qs|I,Is|X,Qs|X）")
    c0 = np.column_stack([a["f1"], a["f2"]]).astype(float)
    c1 = np.column_stack([a["f3"], a["f4"]]).astype(float)
    c0, c1 = c0[np.isfinite(c0).all(1)], c1[np.isfinite(c1).all(1)]
    if len(c0) < 10 or len(c1) < 10:
        raise ValueError("IQraw 有效样本不足")
    m0, m1 = c0.mean(0), c1.mean(0)
    d = float(np.linalg.norm(m1 - m0))
    s0 = float(np.sqrt(np.mean(np.sum((c0 - m0) ** 2, axis=1))))
    s1 = float(np.sqrt(np.mean(np.sum((c1 - m1) ** 2, axis=1))))
    # 读出保真度：沿两团云连线做理想线性判别
    dn = (m1 - m0) / max(d, 1e-30)
    p0, p1 = c0 @ dn, c1 @ dn
    thr = 0.5 * (float(p0.mean()) + float(p1.mean()))
    e0 = float(np.mean(p0 > thr))          # |0> 误判成 1
    e1 = float(np.mean(p1 < thr))          # |1> 误判成 0
    return {"visibility": d / max(d + 2 * (s0 + s1), 1e-30),
            "distance01": d, "rms0": s0, "rms1": s1, "shots": int(len(c0)),
            "F0": round(1 - e0, 4), "F1": round(1 - e1, 4),
            "F_avg": round(1 - 0.5 * (e0 + e1), 4),
            "snr": round(d / max(0.5 * (s0 + s1), 1e-30), 3)}



def pipulse_excitation(res, min_contrast=0.05, min_peak=0.5):
    a = res["data"]
    p = np.asarray(a["f6"], float)
    x = np.asarray(a["f0"], float)
    m = np.isfinite(p) & np.isfinite(x)
    if m.sum() < 4:
        raise ValueError("PiPulse 有效点不足")
    p, x = p[m], x[m]
    order = np.argsort(x)
    p, x = p[order], x[order]
    edge = max(1, len(p) // 8)
    base = float(np.median(np.r_[p[:edge], p[-edge:]]))
    peak, trough = float(p.max()), float(p.min())
    contrast = peak - base
    # 判别器质量有限时 P1 峰值普遍压到 0.4 以下，单看绝对峰值会漏判；
    # 这里加一条「相对基线翻倍」的判据，只要求幅度依赖显著、且发生在非零幅度处。
    relative_ok = bool(base > 0 and peak >= 2 * base and x[np.argmax(p)] > 0)
    return {"excited": bool(contrast >= min_contrast and (peak >= min_peak or relative_ok)),
            "P1_peak": peak, "P1_trough": trough, "P1_baseline": base,
            "P1_contrast": contrast, "amp_at_max": float(x[np.argmax(p)]),
            "peak_abs_ok": bool(peak >= min_peak), "peak_relative_ok": relative_ok}


def fit_exp(x, y):
    from scipy.optimize import curve_fit

    def fun(t, c, a, tau):
        return c + a * np.exp(-t / max(tau, 1e-12))

    p, _ = curve_fit(fun, x, y, p0=[float(y[-1]), float(y[0] - y[-1]), max(np.ptp(x) / 2, 1)],
                     maxfev=30000)
    pred = fun(x, *p)
    return float(abs(p[2])), float(np.sqrt(np.mean((pred - y) ** 2)))


def _dep_field(a, i):
    """Dependent{i} 对应的数据列：DataVault 里 f0 是自变量，之后按顺序排。"""
    names = list(a.dtype.names or ())
    if i is None or i < 0 or i + 1 >= len(names):
        return names[1] if len(names) > 1 else None
    return names[i + 1]


def _p1_index(labels):
    """P1 列选择：t1/ramsey 要看「π 激发后」的 P1（label 里带 X），别用 P1|I（恒 0）。"""
    idx = [i for i, v in enumerate(labels) if "P1" in v]
    if not idx:
        return None
    for i in idx:
        if "x" in labels[i].lower():
            return i
    return idx[-1]


def calib_metrics(kind, res):
    """按数据集类型抽指标（原生单位，单位换算交给报告读者，和原脚本一致）。"""
    a = res["data"]
    labels = [str(res["attrs"].get(f"Dependent{i}.label", "")) for i in range(10)]
    pi = _p1_index(labels)
    if kind == "t1":
        x = np.asarray(a["f0"], float)
        y = np.asarray(a[_dep_field(a, pi)], float)
        if np.all(np.abs(y[np.isfinite(y)]) <= 1e-6):
            return {"T1_status": "invalid", "reason": "P1 列全 0（判别器失配），不拟合"}
        m = np.isfinite(x) & np.isfinite(y) & (x >= 0)
        if m.sum() < 4:
            return {}
        tau, rmse = fit_exp(x[m], y[m])
        if rmse > 0.1 * max(float(np.ptp(y[m])), 1e-9) or tau > 1e6:
            return {"T1_status": "invalid", "T1_native": tau, "T1_fit_rmse": rmse,
                    "reason": "拟合质量太差，不采信"}
        return {"T1_native": tau, "T1_fit_rmse": rmse, "T1_status": "ok"}
    if kind == "ramsey":
        y = np.asarray(a[_dep_field(a, pi)], float)
        if np.all(np.abs(y[np.isfinite(y)]) <= 1e-6):
            return {"T2_status": "invalid", "reason": "P1 列全 0（判别器失配），不拟合"}
        return {"T2_status": "requires_decay_fit_from_Ramsey", "T2_type": "Ramsey T2*"}
    if kind == "pidf":
        # 共振时读出振幅(f1)出现凹陷；P1 列(f6)依赖判别器，q7ld3 上失真，不能用
        x = np.asarray(a["f0"], float)
        y = np.asarray(a["f1"], float)
        if len(x) < 3:
            return {}
        i = int(np.argmin(y))
        base = float(np.median(y))
        depth = float(-20 * np.log10(y[i] / base)) if base > 0 and y[i] > 0 else 0.0  # 正值=凹陷深度
        if 0 < i < len(x) - 1:
            c = np.polyfit(x[i - 1:i + 2], y[i - 1:i + 2], 2)
            df_min = float(-c[1] / (2 * c[0])) if abs(c[0]) > 1e-30 else float(x[i])
        else:
            df_min = float(x[i])
        return {"pidf_dip_df": round(df_min, 6),
                "pidf_dip_depth_db": round(depth, 3),
                "pidf_edge": bool(i in (0, len(x) - 1)),
                "pidf_points": int(len(x))}
    if kind == "s21_dis":
        j = next((i for i, v in enumerate(labels) if "distance" in v.lower() and "01" in v), None)
        return {"distance01_median": float(np.median(a[_dep_field(a, j)])) if j is not None else None}
    return {}


# ───────────────────────── .qwf 生成 ─────────────────────────
def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(v)
    return f'"{v}"'   # 字符串值必须带引号，否则 gate X/2 里的 '/' 会被词法拒绝


def _args_block(params):
    lines = []
    for k, v in params.items():
        if isinstance(v, dict) and "$arange" in v:
            lo, hi, st = v["$arange"]
            lines.append(f"          {k} range {_fmt(lo)} {_fmt(hi)} {_fmt(st)}")
        else:
            lines.append(f"          {k} {_fmt(v)}")
    return "\n".join(lines)


def build_qwf(name, description, bit, session, budget, steps):
    """steps: [(alias, fn, timeout, note, params)] -> 一个 workflow 文本。"""
    out = [f"workflow {name} {{",
           "  meta {",
           f'    name        "{name}"',
           f'    description "{description}"',
           '    tags        ["calib", "zpa", "single-qubit", "auto"]',
           "  }",
           "",
           "  env {",
           f'    qubit   "{bit}"',
           f'    session "{session}"',
           "  }",
           "",
           "  budget {",
           f"    max_wall     {budget['max_wall']}",
           f"    max_writes   {budget['max_writes']}",
           f"    max_datasets {budget['max_datasets']}",
           f"    max_scan_points {budget['max_scan_points']}",
           "  }",
           ""]
    first = steps[0][0]
    out += ["  goal {",
            f'    objective   "{description}"',
            f"    success {{ metric ${first}.dataset  operator >=  value 0 }}",
            "    max_rounds  1",
            '    on_achieved "continue"',
            "  }",
            ""]
    for alias, fn, timeout, note, params in steps:
        out += [f"  measure as {alias} {{",
                f"    fn      {fn}",
                "    qubit   $env.qubit",
                f"    timeout {timeout}",
                f'    note    "{note}"']
        if params:
            out += ["    args {", _args_block(params), "    }"]
        out += ["  }", ""]
    out.append('  say "' + " ".join(f"{a}=${{{a}.dataset}}" for a, *_ in steps) + '"')
    out.append("}")
    return "\n".join(out) + "\n"


def _tag(bias):
    """bias 数字 -> 合法标识符片段（-2.6 -> m2p6）。"""
    return "m" + f"{bias:g}".replace("-", "").replace(".", "p")


def build_probe_qwf(bit, session, f10, bias, tag, piamp_amp=PIAMP_AMP):
    params = {
        "freq": {"$arange": [round(f10 - SPEC_HALF_SPAN, 6),
                             round(f10 + SPEC_HALF_SPAN, 6), SPEC_STEP]},
        "spec_amp": SPEC_AMP,
        "update": False,
    }
    steps = [
        ("s1", "sq.spectroscopy", 400, f"{bit} bias={bias} 局部谱（只采不写）", params),
        ("q1", "sq.iqraw", 300, f"{bit} bias={bias} IQraw（只采不写）", {"update": False}),
        ("p1", "sq.piamp", 300, f"{bit} bias={bias} PiPulse（只采不写）", {"amp": piamp_amp, "update": False}),
    ]
    budget = {"max_wall": "20m", "max_writes": 3, "max_datasets": 3, "max_scan_points": 300}
    return build_qwf(f"{bit}_probe_b{tag}", f"{bit} ZPA 探测：bias_z={bias}", bit, session, budget, steps)


def build_zpa2d_qwf(bit, session, zpa_lo=-3.0, zpa_hi=3.0, zpa_step=0.2, freq_span=0.002):
    """第 1 个实验：读出腔的 ZPA 二维地图（fread × zpa），只采不写。

    厂商默认 zpa=np.arange(-3,3.1,0.4)（16 层）、freq=fread±freq_span/2（0.2 MHz 步长），
    这里把 zpa 轴显式写死，避免"默认轴"在不同比特上点数不可控。
    """
    params = {"zpa": {"$arange": [zpa_lo, zpa_hi, zpa_step]},
              "freq_span": freq_span, "update": False}
    steps = [("z1", "sq.s21_zpa2d", 1200, f"{bit} S21ZPA 地图（只采不写）", params)]
    budget = {"max_wall": "25m", "max_writes": 1, "max_datasets": 1, "max_scan_points": 900}
    return build_qwf(f"{bit}_s21zpa2d", f"{bit} 读出腔 ZPA 地图（bias 区间来源）",
                     bit, session, budget, steps)


def build_spec_scan_qwf(bit, session, f10, bias, tag,
                        half_span=SPEC_HALF_SPAN, step=SPEC_STEP, amp=SPEC_AMP):
    """第 2 步：单个 bias 点上的局部 spectroscopy（只采不写），用来判"是不是比特频率"。"""
    lo, hi = round(f10 - half_span, 6), round(f10 + half_span, 6)
    params = {"freq": {"$arange": [lo, hi, step]}, "spec_amp": amp, "update": False}
    n = len(np.arange(lo, hi, step))
    steps = [("s1", "sq.spectroscopy", 420,
              f"{bit} bias={bias} 谱线 判频（只采不写）", params)]
    # maxScanPoints 是**单条实验**的硬闸，必须 ≥ 实际扫点数（n+1 保险）
    budget = {"max_wall": "15m", "max_writes": 1, "max_datasets": 1,
              "max_scan_points": int(n) + 1}
    return build_qwf(f"{bit}_spec_b{tag}", f"{bit} bias_z={bias} 判频", bit, session, budget, steps)


def build_wide_spec_qwf(bit, session, bias, tag, lo=WIDE_SCAN_LO, hi=WIDE_SCAN_HI,
                        step=WIDE_SCAN_STEP, amp=WIDE_SCAN_AMP, update=True):
    """第 2 步：整个 2–4 GHz 全窗 spectroscopy（`np.arange(2, 4, 0.005)`，400 点）。

    频窗固定不看寄存器里的 f10 —— q9ld3 的教训就是 f10 可能偏上百 MHz。
    `update=True`：由厂商库自己找峰并回写 f10/f21，驱动不再做洛伦兹拟合判频。
    """
    params = {"freq": {"$arange": [lo, hi, step]}, "spec_amp": amp, "update": bool(update)}
    n = len(np.arange(lo, hi, step))
    what = "写 f10" if update else "只采不写"
    steps = [("w1", "sq.spectroscopy", 900,
              f"{bit} bias={bias} 全窗谱线 {lo}–{hi}GHz（{what}）", params)]
    budget = {"max_wall": "20m", "max_writes": 1, "max_datasets": 1,
              "max_scan_points": int(n) + 1}
    return build_qwf(f"{bit}_widespec_b{tag}", f"{bit} bias_z={bias} 全窗找谱线",
                     bit, session, budget, steps)


def fit_lorentzian_generic(x, y):
    """任意基线的洛伦兹拟合（相位/IQ 列也能用），返回 f0/fwhm/r2/depth/sigma 与深度信噪比。"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    base = float(np.median(y))
    i_ext = int(np.argmax(np.abs(y - base)))
    half_win = max(0.012, 20 * float(np.median(np.diff(x))))
    sel = np.abs(x - x[i_ext]) <= half_win
    if sel.sum() >= 8:
        base = float(np.median(np.r_[y[sel][:3], y[sel][-3:]]))
        x, y = x[sel], y[sel]
        i_ext = int(np.argmax(np.abs(y - base)))
    span = float(np.ptp(x)) or 1e-9
    best = None
    for sign in (+1.0, -1.0):
        def fun(f, c, a, f0, w, _s=sign):
            return c + _s * a / (1 + ((f - f0) / (w / 2)) ** 2)

        p0 = [base, max(abs(y[i_ext] - base), 1e-9), float(x[i_ext]), max(span / 8.0, 1e-6)]
        bounds = ([-np.inf, 0.0, float(x.min()) - 1e-6, 1e-7],
                  [np.inf, np.inf, float(x.max()) + 1e-6, span * 2])
        try:
            p, _ = curve_fit(fun, x, y, p0=p0, bounds=bounds, maxfev=60000)
        except Exception:      # noqa: BLE001
            continue
        pred = fun(x, *p)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - float(np.sum((y - pred) ** 2)) / max(ss_tot, 1e-30)
        if best is None or r2 > best[0]:
            best = (r2, p, fun)
    if best is None:
        raise ValueError("洛伦兹拟合失败")
    r2, p, fun = best
    resid = y - fun(x, *p)
    sigma = float(np.std(resid)) or 1e-12
    return {"f0": round(float(p[2]), 6), "fwhm": round(abs(float(p[3])), 6),
            "r2": round(r2, 4), "depth": round(abs(float(p[1])), 6),
            "sigma": round(sigma, 6),
            "snr_depth": round(abs(float(p[1])) / sigma, 2),
            "n_points": int(len(x))}


def alt_column_line(a, x, step, min_r2=0.6, min_snr=4.0,
                    fwhm_range=(0.5, 3.0), min_half_points=3):
    """幅度判据不过时，用相位/IQ 列再判一次（弱驱动下色散谱线主要显现在这些列）。"""
    names = [n for n in a.dtype.names if n != "f0"]
    best = None
    for name in names:
        if name == "f1":
            continue
        y = np.asarray(a[name], float)
        if y.size != x.size or not np.all(np.isfinite(y)) or np.std(y) == 0:
            continue
        try:
            m = fit_lorentzian_generic(x, y)
        except Exception:      # noqa: BLE001
            continue
        f0, w = m["f0"], m["fwhm"]
        lo, hi = float(np.min(x)), float(np.max(x))
        n_half = int(np.sum(np.abs(x - f0) <= w / 2))
        chk = [m["r2"] >= min_r2, m["snr_depth"] >= min_snr,
               fwhm_range[0] <= w * 1000 <= fwhm_range[1],
               n_half >= min_half_points,
               min(f0 - lo, hi - f0) >= 2 * w,
               step * 1000 <= w * 500]
        if not all(chk):
            continue
        if best is None or m["snr_depth"] > best[0]["snr_depth"]:
            best = ({**m, "column": name}, chk)
    return best


def _dev_sigma(x, y, smooth=5, edge=8):
    """滑动平均后相对中位数的偏离（以自身 σ 为单位）；两端各裁 edge 点，避开端点伪影。"""
    y = np.asarray(y, float)
    if len(y) < 4 * smooth:
        smooth = max(1, len(y) // 8)
    ys = np.convolve(y, np.ones(smooth) / smooth, mode="same")
    sl = slice(edge, len(y) - edge) if len(y) > 2 * edge + 2 * smooth else slice(None)
    dev = ys - float(np.median(ys[sl]))
    s = float(np.std(dev[sl])) or 1e-30
    return dev / s, sl


def wide_scan_candidates(res, min_contrast_db=WIDE_SCAN_MIN_CONTRAST_DB,
                         min_sigma=4.0, min_cols=2, top=3, sep=0.006):
    """全窗谱线找候选：幅度列看 dB 对比度，幅度不够时看相位/IQ 列的偏离（σ）。

    弱驱动下色散谱线在幅度上只有零点几 dB，但在相位/I/Q 上是几个 σ 的相干结构；
    只盯幅度列会把这种真实谱线判成"无候选"。
    """
    a = res["data"]
    x = np.asarray(a["f0"], float)
    names = [n for n in a.dtype.names if n != "f0"]
    cols = {}
    for n in names:
        y = np.asarray(a[n], float)
        if y.size == x.size and np.all(np.isfinite(y)) and np.std(y) > 0:
            cols[n] = y
    if "f1" not in cols:
        return None

    y = cols["f1"]
    base = float(np.median(y))
    out = {"n_points": int(len(x))}
    amp_hit = None
    if base > 0:
        db = 20 * np.log10(np.maximum(y, 1e-12) / base)
        sl = slice(8, len(x) - 8) if len(x) > 24 else slice(None)
        i_max, i_min = int(np.argmax(db[sl])) + (sl.start or 0), int(np.argmin(db[sl])) + (sl.start or 0)
        ia = i_max if abs(db[i_max]) >= abs(db[i_min]) else i_min
        contrast = float(db[ia])
        amp_hit = (ia, contrast, db)

    # 多列 σ 投票（弱驱动下谱线主要显现在相位/I/Q 列）
    n = len(x)
    sig = {}
    for name, v in cols.items():
        d, _sl = _dev_sigma(x, v)
        sig[name] = np.abs(d)
    idx = np.arange(n)
    if n > 24:
        idx = idx[8:n - 8]
    score = np.zeros(n)
    votes = np.zeros(n, int)
    for name, d in sig.items():
        score = np.maximum(score, d)
        votes += (d >= 3.0).astype(int)
    cand = [(float(score[i]), int(votes[i]), int(i)) for i in idx if votes[i] >= min_cols]
    # 优先多列一致（票数），票数相同再看偏离幅度：单列 9σ 往往是噪声
    cand.sort(key=lambda t: (t[1], t[0]), reverse=True)
    def _mk(ia, src, sig_v=None, nv=None):
        return {"f0": round(float(x[ia]), 6),
                "contrast_db": round(float(amp_hit[1]), 3) if amp_hit else None,
                "shape": "multi" if sig_v is not None else
                         ("peak" if (amp_hit and amp_hit[1] > 0) else "dip"),
                "base": round(base, 1), "index": int(ia), "source": src,
                "sigma": round(float(sig_v), 2) if sig_v is not None else None,
                "votes": int(nv) if nv is not None else None}

    out = []
    # 强幅度线（>=2 dB）优先；弱驱动下幅度只有零点几 dB 时看多列相干结构
    if amp_hit and abs(amp_hit[1]) >= max(2.0, min_contrast_db):
        out.append(_mk(amp_hit[0], "f1"))
    for s0, nv, i0 in cand:
        if s0 < min_sigma:
            continue
        if any(abs(float(x[i0]) - o["f0"]) < sep for o in out):
            continue
        cols = [nm for nm in sig if sig[nm][i0] >= 3.0]
        out.append(_mk(i0, "+".join(sorted(cols)), s0, nv))
        if len(out) >= top:
            break
    if not out and amp_hit and abs(amp_hit[1]) >= min_contrast_db:
        out.append(_mk(amp_hit[0], "f1"))
    if out:
        # 多列一致的候选优先，单列幅度峰（votes=0）垫底：每个候选要花 3 分钟验证
        out.sort(key=lambda o: (o.get("votes") or 0, o.get("sigma") or 0.0), reverse=True)
        out[0]["n_points"] = int(len(x))
    return out or None


def wide_scan_peak(res, min_contrast_db=WIDE_SCAN_MIN_CONTRAST_DB,
                   min_sigma=4.0, min_cols=2):
    """兼容旧调用：只取最优先的候选。"""
    got = wide_scan_candidates(res, min_contrast_db, min_sigma, min_cols, top=1)
    return got[0] if got else None


def build_iq_pi_qwf(bit, session, bias, tag, piamp_amp=PIAMP_AMP):
    """第 3 步：谱线判频通过后，同一 bias 点上补 IQraw + PiPulse（只采不写）。"""
    steps = [
        ("q1", "sq.iqraw", 300, f"{bit} bias={bias} IQraw（只采不写）", {"update": False}),
        ("p1", "sq.piamp", 300, f"{bit} bias={bias} PiPulse（只采不写）",
         {"amp": piamp_amp, "update": False}),
    ]
    budget = {"max_wall": "20m", "max_writes": 2, "max_datasets": 2, "max_scan_points": 120}
    return build_qwf(f"{bit}_iqpi_b{tag}", f"{bit} bias_z={bias} 读出+激发判据",
                     bit, session, budget, steps)


def build_f10_qwf(bit, session):
    """单独跑一次 pidf(df=0.1)，只采不写；f10 由驱动按振幅凹陷算。"""
    steps = [("d1", "sq.pidf", 600, f"{bit} pidf df=0.1（只采不写，定 f10）",
              {"df": 0.1, "update": False})]
    budget = {"max_wall": "15m", "max_writes": 1, "max_datasets": 1, "max_scan_points": 30}
    return build_qwf(f"{bit}_f10_pidf", f"{bit} 用振幅凹陷定 f10", bit, session, budget, steps)


def build_spec_qwf(bit, session, f0, half_span, step, amp, repeats=2, broaden=False):
    """局部 spectroscopy：同窗复扫 repeats 次（判复现），可选一次 amp×10 看功率展宽。"""
    params = {"freq": {"$arange": [round(f0 - half_span, 6), round(f0 + half_span, 6), step]},
              "spec_amp": amp, "update": False}
    steps = []
    for i in range(max(1, repeats)):
        steps.append((f"s{i + 1}", "sq.spectroscopy", 420,
                      f"{bit} {f0:.4f}±{half_span:.3f}GHz 谱线（只采不写，第{i + 1}次）", dict(params)))
    if broaden:
        bow = dict(params)
        bow["spec_amp"] = round(amp * 10, 6)
        steps.append(("sb", "sq.spectroscopy", 420, f"{bit} 功率展宽校验（spec_amp×10）", bow))
    budget = {"max_wall": "30m", "max_writes": len(steps),
              "max_datasets": len(steps), "max_scan_points": 260 * len(steps)}
    return build_qwf(f"{bit}_spec_line", f"{bit} 定 f10（Lorentzian 拟合）",
                     bit, session, budget, steps)


def fit_lorentzian(x, y):
    """y(f) = c ± A / (1 + ((f-f0)/(w/2))^2)：峰、谷两个模型都试，取 R² 更好的。

    返回 f0/FWHM/A/R²/对比度；contrast_db 为正表示「相对基线偏高」，负表示凹陷。
    """
    from scipy.optimize import curve_fit

    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    if len(x) < 8:
        raise ValueError("谱线点数不足")
    order = np.argsort(x)
    x, y = x[order], y[order]
    edge = max(1, len(y) // 8)
    base = float(np.median(np.r_[y[:edge], y[-edge:]]))
    # 先在极值附近取一个窄窗再拟合：整窗拟合会把尖峰摊成又宽又浅的洛伦兹
    i_ext = int(np.argmax(np.abs(y - base)))
    half_win = max(0.012, 20 * float(np.median(np.diff(x))))
    sel = np.abs(x - x[i_ext]) <= half_win
    if sel.sum() >= 8:
        base = float(np.median(np.r_[y[sel][:3], y[sel][-3:]]))
        x, y = x[sel], y[sel]
    span = float(np.ptp(x))

    def make(sign):
        def fun(f, c, a, f0, w):
            return c + sign * a / (1 + ((f - f0) / (w / 2)) ** 2)
        return fun

    best = None
    for sign, guess in ((+1.0, float(x[np.argmax(y)])), (-1.0, float(x[np.argmin(y)]))):
        fun = make(sign)
        r0 = float(y[np.argmax(y)] if sign > 0 else y[np.argmin(y)])
        p0 = [base, max(abs(r0 - base), 1e-9), guess, max(span / 8.0, 1e-6)]
        bounds = ([0.0, 0.0, float(x.min()) - 1e-6, 1e-7],
                  [np.inf, np.inf, float(x.max()) + 1e-6, span * 2])
        try:
            p, _ = curve_fit(fun, x, y, p0=p0, bounds=bounds, maxfev=60000)
        except Exception:  # noqa: BLE001
            continue
        pred = fun(x, *p)
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - float(np.sum((y - pred) ** 2)) / max(ss_tot, 1e-30)
        if best is None or r2 > best[0]:
            best = (r2, p, fun, sign)
    if best is None:
        raise ValueError("Lorentzian 拟合失败")
    r2, p, fun, sign = best
    f0, fwhm = float(p[2]), abs(float(p[3]))
    peak = float(fun(f0, *p))
    n_half = int(np.sum(np.abs(x - f0) <= fwhm / 2))
    return {"f0": round(f0, 6), "fwhm": round(fwhm, 6), "dip": round(float(p[1]), 1),
            "base": round(base, 1), "r2": round(r2, 4),
            "shape": "peak" if sign > 0 else "dip",
            "contrast_db": round(float(20 * np.log10(max(peak, 1e-9) / base)), 3) if base > 0 else None,
            "n_half": n_half, "n_points": int(len(x))}


def check_spec_line(m, x, step, min_r2=0.6, min_contrast_db=1.5,
                    fwhm_range=(0.5, 3.0), min_half_points=3, recheck=None):
    """§二 判据 1–6（复现判据传入 recheck=第二次拟合 f0）。"""
    lo, hi = float(np.min(x)), float(np.max(x))
    f0, w = m["f0"], m["fwhm"]
    chk = [
        ("R²≥0.6", m["r2"] >= min_r2, m["r2"]),
        ("对比度≥1.5dB", abs(m["contrast_db"] or 0) >= min_contrast_db, m["contrast_db"]),
        ("0.5≤FWHM≤3MHz", fwhm_range[0] <= w * 1000 <= fwhm_range[1], round(w * 1000, 3)),
        ("半高内≥3点", m["n_half"] >= min_half_points, m["n_half"]),
        ("中心距窗边≥2×FWHM", min(f0 - lo, hi - f0) >= 2 * w, round(min(f0 - lo, hi - f0), 5)),
        ("两次复扫一致<0.5MHz", recheck is None or abs(recheck - f0) * 1000 < 0.5,
         None if recheck is None else round(abs(recheck - f0) * 1000, 3)),
        ("步长≤FWHM/2", step * 1000 <= w * 500, round(step * 1000, 3)),
    ]
    ok = all(c[1] for c in chk)
    return {"ok": ok, "checks": [{"name": n, "ok": bool(v), "value": val} for n, v, val in chk],
            "failed": [n for n, v, _ in chk if not v]}


def measure_f10(args, session, log):
    """局部 spectroscopy -> Lorentzian 拟合 -> §二 判据 -> 写 f10/f21。"""
    bit = args.bit
    reg, err = read_registry(bit)
    if err:
        return {"error": err}
    f10, f21 = float(reg.get("f10")), float(reg.get("f21"))
    half, step = args.f10_half_span, args.f10_step
    log(f"  谱线定 f10：当前 f10={f10:.6f}，扫 {f10 - half:.4f}–{f10 + half:.4f} / {step * 1000:.1f} MHz")
    text = build_spec_qwf(bit, session, f10, half, step, args.spec_amp,
                          repeats=2, broaden=args.f10_broaden_check)
    (args.out / f"{bit}_spec_line.qwf").write_text(text, encoding="utf-8")
    ir, verr = parse_validate(text, require_writes=False)
    if verr:
        return {"error": verr}
    st = run_ir(ir, {"qubit": bit, "session": session}, timeout=args.timeout, log=log)
    ds = collect_datasets(st, args.datavault)
    if "s1" not in ds:
        return {"error": "谱线扫描没有产出数据集", "outcome": st.get("status")}
    fits, xs = [], []
    for alias in ("s1", "s2", "sb"):
        if alias not in ds:
            continue
        try:
            a = load_dv(ds[alias]["path"])["data"]
            x = np.asarray(a["f0"], float)
            y = np.asarray(a["f1"], float)
            fits.append((alias, ds[alias]["dataset"], fit_lorentzian(x, y), x))
            xs.append(x)
        except Exception as exc:  # noqa: BLE001
            log(f"    ! {alias} 拟合失败：{exc}")
    if not fits:
        return {"error": "谱线拟合全部失败"}
    alias, dsid, m, x = fits[0]
    rchk = fits[1][2]["f0"] if len(fits) > 1 and fits[1][0] == "s2" else None
    # 两次复扫取 R² 更高的一次当主拟合，另一次做复现校验（噪声大时单次容易卡在阈值上）
    repeats = [f for f in fits if f[0] in ("s1", "s2")]
    if len(repeats) == 2 and repeats[1][2]["r2"] > repeats[0][2]["r2"]:
        alias, dsid, m, x = repeats[1][0], repeats[1][1], repeats[1][2], repeats[1][3]
        rchk = repeats[0][2]["f0"]
    verdict = check_spec_line(m, x, step, recheck=rchk)
    info = {"dataset": dsid, "f10_before": f10, **{k: v for k, v in m.items() if not k.startswith("_")},
            "verdict": verdict}
    if len(fits) > 2 and fits[2][0] == "sb":
        w0, wb = m["fwhm"], fits[2][2]["fwhm"]
        info["broaden"] = {"fwhm_amp": round(w0 * 1000, 3), "fwhm_amp10": round(wb * 1000, 3),
                           "ratio": round(wb / max(w0, 1e-9), 3),
                           "power_broadened": wb / max(w0, 1e-9) >= 1.5}
        verdict["checks"].append({"name": "功率展宽≥1.5×", "ok": bool(info["broaden"]["power_broadened"]),
                                  "value": info["broaden"]["ratio"]})
    log(f"    fit f0={m['f0']:.6f} FWHM={m['fwhm'] * 1000:.3f}MHz 深度={m['contrast_db']}dB "
        f"R²={m['r2']} 复扫={rchk}")
    if not verdict["ok"]:
        info.update({"applied": False, "f10_confidence": "low",
                     "reason": "判据不过：" + ", ".join(verdict["failed"])})
        log(f"    ✗ f10 未确认（{info['reason']}），不写寄存器")
        return info
    delta = round(m["f0"] - f10, 9)
    new_f10, new_f21 = round(m["f0"], 9), round(f21 + delta, 9)
    info.update({"f10_after": new_f10, "f21_after": new_f21, "delta": delta,
                 "f10_confidence": "high" if m["r2"] >= 0.8 and (m["contrast_db"] or 0) >= 2.5 else "ok"})
    if args.no_write_f10:
        info.update({"applied": False, "reason": "--no-write-f10"})
        return info
    wr = write_params(bit, {"f10": new_f10, "f21": new_f21})
    if wr.get("error"):
        info.update({"applied": False, "reason": f"写入失败：{wr['error']}"})
        return info
    back, _ = read_registry(bit)
    ok = abs(float(back.get("f10", 1e9)) - new_f10) < 1e-6
    info["applied"] = bool(ok)
    info["readback_f10"] = back.get("f10")
    log(f"    {'✓' if ok else '✗'} f10 {f10:.6f} → {new_f10:.6f}（Δ={delta * 1000:+.3f} MHz）回读={back.get('f10')}")
    return info


def measure_f10_pidf(args, session, log):
    """pidf -> 振幅凹陷 -> 阈值把关 -> 写 f10/f21。返回可写入 result 的 dict。"""
    bit = args.bit
    reg, err = read_registry(bit)
    if err:
        return {"error": err}
    f10 = float(reg.get("f10"))
    f21 = float(reg.get("f21"))
    log(f"  pidf 定 f10：当前 f10={f10} f21={f21}")
    text = build_f10_qwf(bit, session)
    (args.out / f"{bit}_f10_pidf.qwf").write_text(text, encoding="utf-8")
    ir, verr = parse_validate(text, require_writes=False)
    if verr:
        return {"error": verr}
    st = run_ir(ir, {"qubit": bit, "session": session}, timeout=args.timeout, log=log)
    ds = collect_datasets(st, args.datavault)
    if "d1" not in ds:
        return {"error": "pidf 没有产出数据集", "outcome": st.get("status")}
    res = load_dv(ds["d1"]["path"])
    m = calib_metrics("pidf", res)
    info = {"dataset": ds["d1"]["dataset"], "f10_before": f10, **m}
    log(f"    凹陷 df={m.get('pidf_dip_df')} GHz 深度={m.get('pidf_dip_depth_db')} dB"
        f" edge={m.get('pidf_edge')} → f10≈{round(f10 + (m.get('pidf_dip_df') or 0), 6)}")
    depth = float(m.get("pidf_dip_depth_db") or 0.0)
    if m.get("pidf_edge") or depth < args.f10_min_depth:
        info["applied"] = False
        info["reason"] = (f"凹陷 {depth:.2f} dB < 阈值 {args.f10_min_depth} dB"
                          if depth < args.f10_min_depth else "凹陷落在扫描边缘")
        log(f"    ✗ 拒绝写 f10：{info['reason']}")
        return info
    delta = float(m["pidf_dip_df"])
    new_f10, new_f21 = round(f10 + delta, 9), round(f21 + delta, 9)
    info.update({"f10_after": new_f10, "f21_after": new_f21, "delta": round(delta, 6)})
    if args.no_write_f10:
        info["applied"] = False
        info["reason"] = "--no-write-f10"
        log("    （--no-write-f10：只算不写）")
        return info
    wr = write_params(bit, {"f10": new_f10, "f21": new_f21})
    if wr.get("error"):
        info.update({"applied": False, "reason": f"写入失败：{wr['error']}"})
        return info
    back, _ = read_registry(bit)
    ok = abs(float(back.get("f10", 1e9)) - new_f10) < 1e-6
    info["applied"] = bool(ok)
    info["readback_f10"] = back.get("f10")
    log(f"    {'✓' if ok else '✗'} 写入 f10={new_f10} f21={new_f21} 回读={back.get('f10')}")
    return info


def build_piamp_qwf(bit, session, bias, amp):
    """扩幅重扫 PiPulse（最大幅度处仍单调上升时用）。"""
    steps = [("p1", "sq.piamp", 420, f"{bit} bias={bias} PiPulse 扩幅到 {amp}（只采不写）",
              {"amp": amp, "update": False})]
    budget = {"max_wall": "20m", "max_writes": 1, "max_datasets": 1, "max_scan_points": 60}
    return build_qwf(f"{bit}_piamp_x5_b{_tag(bias)}", f"{bit} π 幅度扩幅（bias={bias}）",
                     bit, session, budget, steps)


def p1_column_all_zero(res, tol=1e-6):
    a = res["data"]
    if "f6" not in a.dtype.names:
        return False
    v = np.asarray(a["f6"], float)
    return bool(np.all(np.abs(v[np.isfinite(v)]) <= tol))


def probe_verdict(vis, p1, args, disc_ok=None):
    """probe 判据：visibility 与 snr 同时过，且有 Pi 激发；P1 列全 0 时只认 amplitude。"""
    reasons = []
    if not vis:
        reasons.append("IQraw 指标缺失")
    else:
        if vis["visibility"] < args.visibility_threshold:
            reasons.append(f"visibility {vis['visibility']:.4f} < {args.visibility_threshold}")
        if vis.get("snr", 0) < args.snr_threshold:
            reasons.append(f"snr {vis.get('snr')} < {args.snr_threshold}")
    if not p1:
        reasons.append("PiPulse 指标缺失")
    elif not p1["excited"]:
        reasons.append(f"无激发（P1_contrast {p1['P1_contrast']:.3f}）")
    return {"ok": not reasons, "reasons": reasons}


def build_chain_qwf(bit, session, bias, tag):
    """激发确认后的标定链，节点顺序与参数按用户 2026-11-11 指定（全部用库默认 update）。"""
    steps = [
        ("d1", "sq.pidf", 600, "pidf 默认 df=0.03 定 f10", {}),
        ("r1", "sq.ramsey_df", 900, "ramsey_df(df=0.1)", {"df": 0.1}),
        ("r2", "sq.ramsey_df", 900, "ramsey_df 默认", {}),
        ("m1", "sq.s21_dis", 400, "s21_dis 读出区分度", {}),
        ("q1", "sq.iqraw", 300, "IQraw", {}),
        ("x1", "sq.set_pi", 300, "set_pi gate=X", {"gate": "X"}),
        ("x2", "sq.set_pi", 300, "set_pi gate=X/2", {"gate": "X/2"}),
        ("q2", "sq.iqraw", 300, "set_pi 后 IQraw", {}),
        ("t1", "sq.t1", 1200, f"T1（zpa={bias}）", {"zpa": bias}),
        ("e1", "sq.ramsey_t2", 1800, "ramsey_t2 回波", {}),
        ("xb", "sq.xeb", 3600, "xeb 随机基准", {}),
    ]
    budget = {"max_wall": "120m", "max_writes": 11, "max_datasets": 11, "max_scan_points": 300}
    return build_qwf(f"{bit}_chain_b{tag}", f"{bit} 标定链：bias_z={bias}", bit, session, budget, steps)


# ───────────────────────── 执行 ─────────────────────────
def parse_validate(text, require_writes=True):
    parsed = post(BASE, "/workflows/parse_qwf", {"text": text})
    if parsed.get("error") or parsed.get("ok") is False:
        return None, f"parse: {parsed.get('error')}"
    ir = parsed.get("ir") or {}
    val = post(BASE, "/workflows/validate", {"ir": ir, "requireWrites": require_writes})
    if not val.get("ok"):
        errs = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in (val.get("errors") or [])[:4])
        return None, f"validate: {errs or val.get('summaryText')}"
    return ir, None


def run_ir(ir, ctx, dry=False, timeout=1800, poll=5.0, log=print):
    wid = f"wf_calib_{int(time.time())}"
    launch = post(BASE, "/workflows/run_ir", {"ir": ir, "context": ctx,
                                              "workflowId": wid, "dryRun": dry})
    if not launch.get("ok"):
        return {"status": "launch_failed", "error": launch.get("error"), "workflowId": wid}
    if launch.get("status") == "queued":
        log(f"    排队中，前面还有 {launch.get('queuePosition')} 个流程")
    t0 = time.time()
    while True:
        time.sleep(poll)
        st = post(BASE, "/workflows/status", {"workflowId": wid})
        state = st.get("status")
        if state in ("completed", "failed", "cancelled", "budget_exceeded",
                     "needs_human", "quota_exceeded"):
            st["elapsed"] = round(time.time() - t0, 1)
            # 顺手把每个节点压成 {status, dataset}
            nodes = st.get("nodes") or {}
            if isinstance(nodes, list):
                nodes = {str(n.get("id")): n for n in nodes}
            slim = {}
            for nid, n in nodes.items():
                res = n.get("result") or {}
                slim[nid] = {"status": n.get("status"),
                             "dataset": n.get("dataset_id") or res.get("dataset_id"),
                             "error": n.get("error")}
            st["node_map"] = slim
            return st
        if time.time() - t0 > timeout:
            post(BASE, "/workflows/cancel", {"workflowId": wid})
            return {"status": "timeout", "workflowId": wid}


def collect_datasets(st, root):
    out = {}
    for alias, info in (st.get("node_map") or {}).items():
        ds = info.get("dataset")
        if ds in (None, ""):
            continue
        p = dataset_file(root, ds)
        if p:
            out[alias] = {"dataset": int(ds), "path": str(p)}
    return out


def bias_grid_ascending(intervals, step):
    """从全区最小 bias 起按 step 升序铺点，只保留落在任一 supported 区间内的点。"""
    if not intervals:
        return []
    st = Decimal(str(step))
    lo = min(Decimal(str(iv["interval"][0])) for iv in intervals)
    hi = max(Decimal(str(iv["interval"][1])) for iv in intervals)
    out, v = [], lo
    while v <= hi + Decimal("1e-9"):
        if any(Decimal(str(iv["interval"][0])) - Decimal("1e-9") <= v
               <= Decimal(str(iv["interval"][1])) + Decimal("1e-9") for iv in intervals):
            out.append(float(v))
        v += st
    return out


def pick_biases(plan, args):
    """bias 候选：默认从区间最小值升序、步长 --bias-step；--biases 直接覆盖。"""
    if args.biases.strip():
        return [round(float(x), 6) for x in args.biases.split(",") if x.strip()]
    grid = [round(b + args.bias_offset, 6)
            for b in bias_grid_ascending(plan.get("intervals") or [], args.bias_step)]
    return grid[: max(1, args.max_bias_points)]


def run_zpa2d(args, session, log, dry=False):
    """第 1 个实验：s21_zpa2d 真机跑（或干跑），返回 (hdf5 路径, 规划结果, 错误)。"""
    bit = args.bit
    text = build_zpa2d_qwf(bit, session, args.zpa_lo, args.zpa_hi, args.zpa_step,
                           args.zpa_freq_span)
    (args.out / f"{bit}_s21zpa2d.qwf").write_text(text, encoding="utf-8")
    ir, verr = parse_validate(text, require_writes=False)
    if verr:
        return None, None, f"s21_zpa2d 校验失败：{verr}"
    st = run_ir(ir, {"qubit": bit, "session": session}, dry=dry,
                timeout=args.timeout, log=log)
    log(f"    outcome={st.get('status')} 用时={st.get('elapsed')}s")
    if dry:
        return None, None, None
    if st.get("status") != "completed":
        return None, None, f"s21_zpa2d 未完成（{st.get('status')}）"
    ds = collect_datasets(st, args.datavault)
    for _ in range(6):                    # DataVault 落盘有延迟
        if "z1" in ds:
            break
        time.sleep(5)
        ds = collect_datasets(st, args.datavault)
    if "z1" not in ds:
        return None, None, "s21_zpa2d 没有产出数据集"
    path = Path(ds["z1"]["path"])
    plan = plan_intervals(path)
    plan["dataset"] = ds["z1"]["dataset"]
    return path, plan, None


def _restore_f10(args, row, log=print):
    """探测点不成立时把 f10/f21 复原成进该点之前的值（窄窗 update=True 已改过寄存器）。"""
    before = row.get("f10_before")
    after = row.get("f10_after_spec")
    if before is None or after is None or abs(float(after) - float(before)) < 5e-7:
        return
    try:
        write_params(args.bit, {"f10": float(before),
                                "f21": float(row.get("f21_before") or 0.0)})
        log(f"    f10 复原：{float(after):.6f} → {float(before):.6f}")
    except Exception as exc:              # noqa: BLE001
        log(f"    ! f10 复原失败：{exc}")


def _wide_candidates(args, session, bias, log, tag, row):
    """2a：2–4 GHz 宽扫只找线索（update=False），返回候选列表。"""
    bit = args.bit
    text = build_wide_spec_qwf(bit, session, bias, tag, args.wide_lo, args.wide_hi,
                               args.wide_step, args.spec_amp, update=False)
    (args.out / f"{bit}_widespec_b{tag}.qwf").write_text(text, encoding="utf-8")
    ir, verr = parse_validate(text, require_writes=False)
    if verr:
        row["error"] = f"全窗谱线校验失败：{verr}"
        return []
    st = run_ir(ir, {"qubit": bit, "session": session}, timeout=args.timeout, log=log)
    ds = collect_datasets(st, args.datavault)
    for _ in range(10):
        if "w1" in ds:
            break
        time.sleep(6)
        ds = collect_datasets(st, args.datavault)
    row["wide_outcome"] = st.get("status")
    if "w1" not in ds:
        row["error"] = f"全窗谱线没有数据集（{st.get('status')}）"
        return []
    row["wide_dataset"] = ds["w1"]["dataset"]
    try:
        res = load_dv_retry(ds["w1"]["path"], log=log)
    except Exception as exc:              # noqa: BLE001
        row["error"] = f"全窗谱线读不了：{exc}"
        return []
    return wide_scan_candidates(res, args.wide_min_contrast) or []


def _verify_candidate(args, session, bias, log, tag, cand, pi_scan_amp, disc_ok, row):
    """2b+2c：候选点窄窗 spectroscopy(update=True) → IQraw+PiPulse 判据 → 结论写回 row。"""
    bit = args.bit
    f_drive = float(cand["f0"])
    row["stage"] = "spec"
    row["f10_candidate"] = f_drive
    text_s = build_wide_spec_qwf(bit, session, bias, f"nb{tag}",
                                 round(f_drive - args.narrow_half_span, 6),
                                 round(f_drive + args.narrow_half_span, 6),
                                 args.narrow_step, args.spec_amp, update=True)
    (args.out / f"{bit}_narrowspec_b{tag}.qwf").write_text(text_s, encoding="utf-8")
    ir, verr = parse_validate(text_s, require_writes=True)
    if verr:
        row["error"] = f"窄窗谱线校验失败：{verr}"
        return row
    st = run_ir(ir, {"qubit": bit, "session": session}, timeout=args.timeout, log=log)
    ds = collect_datasets(st, args.datavault)
    for _ in range(10):                   # DataVault 落盘有延迟
        if "w1" in ds:
            break
        time.sleep(6)
        ds = collect_datasets(st, args.datavault)
    row["spec_outcome"] = st.get("status")
    if "w1" not in ds:
        row["error"] = f"窄窗谱线没有数据集（{st.get('status')}）"
        return row
    row["spec_dataset"] = ds["w1"]["dataset"]
    try:
        reg2, _ = read_registry(bit)
        row["f10_after_spec"] = float(reg2.get("f10", 0.0) or 0.0)
        log(f"    窄窗谱线 ds {row['spec_dataset']}（{f_drive - args.narrow_half_span:.4f}–"
            f"{f_drive + args.narrow_half_span:.4f} GHz）→ 库回写 f10="
            f"{row['f10_after_spec']:.6f}（进入该点时 {row['f10_before']:.6f}）→ 验证激发")
    except Exception as exc:              # noqa: BLE001
        log(f"    ! 回读 f10 失败：{exc}")

    text2 = build_iq_pi_qwf(bit, session, bias, tag, piamp_amp=pi_scan_amp)
    (args.out / f"{bit}_iqpi_b{tag}.qwf").write_text(text2, encoding="utf-8")
    ir2, verr2 = parse_validate(text2, require_writes=False)
    if verr2:
        row.update({"stage": "iqpi", "error": f"校验失败：{verr2}"})
        return row
    st2 = run_ir(ir2, {"qubit": bit, "session": session}, timeout=args.timeout, log=log)
    ds2 = collect_datasets(st2, args.datavault)
    row["iqpi_outcome"] = st2.get("status")
    vis = p1 = None
    try:
        if "q1" in ds2:
            vis = iqraw_visibility(load_dv_retry(ds2["q1"]["path"], log=log))
        if "p1" in ds2:
            p1 = pipulse_excitation(load_dv_retry(ds2["p1"]["path"], log=log))
    except Exception as exc:              # noqa: BLE001
        log(f"    ! 指标计算失败：{exc}")
    # P1 全饱和（≈1 或 ≈0）不是"没激发"，而是判别器/驱动过载 → 缩幅重扫一次
    if (p1 and "p1" in ds2 and not p1["excited"]
            and (p1["P1_trough"] > 0.9 or p1["P1_peak"] < 0.1)):
        small = round(pi_scan_amp / 3.0, 3)
        log(f"    P1 全饱和（{p1['P1_trough']:.3f}–{p1['P1_peak']:.3f}）→ 判别器/驱动过载，"
            f"缩幅到 {small:g} 重扫")
        ts = build_piamp_qwf(bit, session, bias, small)
        (args.out / f"{bit}_piamp_div3_b{tag}.qwf").write_text(ts, encoding="utf-8")
        irs, verrs = parse_validate(ts, require_writes=False)
        if not verrs:
            sts = run_ir(irs, {"qubit": bit, "session": session}, timeout=args.timeout, log=log)
            dss = collect_datasets(sts, args.datavault)
            if "p1" in dss:
                try:
                    p1 = pipulse_excitation(load_dv_retry(dss["p1"]["path"], log=log))
                    p1["amp_range"] = small
                    ds2["p1_div3"] = dss["p1"]
                    log(f"    缩幅后 P1 {p1['P1_trough']:.3f}–{p1['P1_peak']:.3f}"
                        f" contrast={p1['P1_contrast']:.3f} excited={p1['excited']}")
                except Exception as exc:  # noqa: BLE001
                    log(f"    ! 缩幅指标失败：{exc}")
    # 最大幅度处 P1 仍单调上升且仍有响应 → 扩幅 5× 重扫一次（§三-③）
    if (p1 and not p1["excited"] and "p1" in ds2
            and p1["amp_at_max"] >= pi_scan_amp * 0.88 and p1["P1_contrast"] > 0.02):
        big = round(pi_scan_amp * 5, 3)
        log(f"    P1 在 {pi_scan_amp:g} 处仍上升（contrast {p1['P1_contrast']:.3f}）→ 扩幅到 {big:g} 重扫")
        t2 = build_piamp_qwf(bit, session, bias, big)
        (args.out / f"{bit}_piamp_x5_b{tag}.qwf").write_text(t2, encoding="utf-8")
        irx, verrx = parse_validate(t2, require_writes=False)
        if verrx:
            log(f"    ✗ 扩幅 qwf 校验失败：{verrx}")
        else:
            stx = run_ir(irx, {"qubit": bit, "session": session}, timeout=args.timeout, log=log)
            dsx = collect_datasets(stx, args.datavault)
            if "p1" in dsx:
                try:
                    p1 = pipulse_excitation(load_dv(dsx["p1"]["path"]))
                    p1["amp_range"] = big
                    ds2["p1_x5"] = dsx["p1"]
                    log(f"    扩幅后 P1_peak={p1['P1_peak']:.3f} contrast={p1['P1_contrast']:.3f}")
                except Exception as exc:  # noqa: BLE001
                    log(f"    ! 扩幅指标失败：{exc}")
    verdict = probe_verdict(vis, p1, args, disc_ok)
    row.update({"stage": "iqpi",
                "datasets": {k: v["dataset"] for k, v in ds2.items()},
                "visibility": vis and round(vis["visibility"], 4),
                "snr": vis and vis.get("snr"),
                "p1_contrast": p1 and round(p1["P1_contrast"], 4),
                "excited": p1 and p1["excited"],
                "passed": bool(verdict["ok"]), "reasons": verdict["reasons"]})
    log(f"    visibility={row['visibility']} snr={row['snr']} P1_contrast={row['p1_contrast']} "
        f"excited={row['excited']} → {'通过' if verdict['ok'] else '不通过'}")


    verdict = probe_verdict(vis, p1, args, disc_ok)
    row.update({"stage": "iqpi",
                "datasets": {k: v["dataset"] for k, v in ds2.items()},
                "visibility": vis and round(vis["visibility"], 4),
                "snr": vis and vis.get("snr"),
                "p1_contrast": p1 and round(p1["P1_contrast"], 4),
                "excited": p1 and p1["excited"],
                "passed": bool(verdict["ok"]), "reasons": verdict["reasons"],
                "line_ok": bool(verdict["ok"])})
    log(f"    visibility={row['visibility']} snr={row['snr']} P1_contrast={row['p1_contrast']} "
        f"excited={row['excited']} → {'通过' if verdict['ok'] else '不通过'}")
    return row


def probe_at_bias(args, session, bias, log, pi_scan_amp, disc_ok=None):
    """单个 bias 点：宽扫找候选 → 逐个候选窄窗写 f10 并验证激发（激发是最终仲裁）。"""
    bit = args.bit
    tag = _tag(bias)
    row = {"bias": bias}
    reg, err = read_registry(bit)
    if err:
        row["error"] = err
        return row
    f10 = float(reg.get("f10", 0.0))
    row.update({"f10_before": f10, "f21_before": float(reg.get("f21", 0.0) or 0.0)})

    row["stage"] = "wide"
    cands = _wide_candidates(args, session, bias, log, tag, row)
    if row.get("error"):
        return row
    if not cands:
        log(f"    {row.get('wide_dataset')} 全窗无候选谱线（对比度 < {args.wide_min_contrast} dB）")
        return row
    row["candidates"] = [c["f0"] for c in cands]
    log(f"    宽扫 ds {row.get('wide_dataset')} 候选 {len(cands)} 个："
        + "、".join(f"{c['f0']:.4f}（{c.get('votes')} 票 {c.get('sigma')}σ {c.get('source')}）"
                    for c in cands))

    for k, cand in enumerate(cands, 1):
        if k > 1:
            log(f"    候选 {k}/{len(cands)}：{cand['f0']:.4f} GHz 再试")
        _verify_candidate(args, session, bias, log, f"{tag}_{k}" if k > 1 else tag,
                          cand, pi_scan_amp, disc_ok, row)
        if row.get("passed"):
            return row
        _restore_f10(args, row, log)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bit", default=DEFAULT_BIT)
    ap.add_argument("--datavault", type=Path, default=DEFAULT_DATAVAULT)
    ap.add_argument("--s21zpa", type=Path, default=None, help="指定 S21ZPA hdf5（默认为最新）")
    ap.add_argument("--skip-zpa2d", action="store_true",
                    help="不跑 s21_zpa2d，直接用已有 hdf5 规划 bias 区间")
    ap.add_argument("--zpa-lo", type=float, default=-3.0, help="s21_zpa2d 的 zpa 起点")
    ap.add_argument("--zpa-hi", type=float, default=3.0, help="s21_zpa2d 的 zpa 终点")
    ap.add_argument("--zpa-step", type=float, default=0.2, help="s21_zpa2d 的 zpa 步长")
    ap.add_argument("--zpa-freq-span", type=float, default=0.002,
                    help="s21_zpa2d 的读出频率半窗（GHz，默认 ±1 MHz）")
    ap.add_argument("--session", default="", help="默认取 :3003/health 的 session_path[-1]")
    ap.add_argument("--out", type=Path, default=HERE / "out")
    ap.add_argument("--bias-step", type=float, default=BIAS_STEP)
    ap.add_argument("--visibility-threshold", type=float, default=VISIBILITY_THRESHOLD)
    ap.add_argument("--max-bias-points", type=int, default=6, help="最多探测几个 bias 点")
    ap.add_argument("--bias-offset", type=float, default=0.0, help="网格整体平移（native 单位）")
    ap.add_argument("--biases", default="",
                    help="逗号分隔的 bias 清单，直接覆盖离线规划的顺序（如 0.4,-0.2）")
    ap.add_argument("--set-f10", type=float, default=None,
                    help="探测前把 f10/f21 写成该值（人工确认过的比特频率，GHz）")
    ap.add_argument("--no-chain", action="store_true", help="选中 bias 后不跑标定链")
    ap.add_argument("--no-pidf", action="store_true", help="跳过 pidf 定 f10 这一步")
    ap.add_argument("--no-write-f10", action="store_true",
                    help="pidf 只算 f10 不写寄存器（默认通过阈值就写）")
    ap.add_argument("--f10-min-depth", type=float, default=0.3,
                    help="pidf 振幅凹陷的最小深度（dB），不足则拒绝写 f10（默认 0.3）")
    ap.add_argument("--spec-amp", type=float, default=SPEC_AMP, help="细扫谱线的驱动幅度")
    ap.add_argument("--wide-lo", type=float, default=WIDE_SCAN_LO, help="全窗谱线起点（GHz）")
    ap.add_argument("--wide-hi", type=float, default=WIDE_SCAN_HI, help="全窗谱线终点（GHz）")
    ap.add_argument("--wide-step", type=float, default=WIDE_SCAN_STEP, help="全窗谱线步长（GHz）")
    ap.add_argument("--wide-amp", type=float, default=WIDE_SCAN_AMP, help="全窗谱线的驱动幅度")
    ap.add_argument("--wide-min-contrast", type=float, default=WIDE_SCAN_MIN_CONTRAST_DB,
                    help="全窗里算「找到谱线」的最小对比度（dB），默认 1.0")
    ap.add_argument("--narrow-half-span", type=float, default=0.02,
                    help="candidate narrow window half span (GHz)")
    ap.add_argument("--narrow-step", type=float, default=0.0005,
                    help="candidate narrow window step (GHz)")
    ap.add_argument("--f10-half-span", type=float, default=0.1, help="候选点细扫半窗（GHz）")
    ap.add_argument("--f10-step", type=float, default=0.0005, help="候选点细扫步长（GHz）")
    ap.add_argument("--f10-broaden-check", action="store_true",
                    help="额外跑一次 spec_amp×10 做功率展宽校验（判据 ⑦）")
    ap.add_argument("--snr-threshold", type=float, default=0.3,
                    help="IQraw 信噪比阈值（sep/σ），与 visibility 同时满足才算通过")
    ap.add_argument("--pi-amp", type=float, default=None,
                    help="PiPulse 扫描上限（默认 1.6×PiHalf.amp；过驱动会让判别器饱和成 P1≡1）")
    ap.add_argument("--allow-length-mismatch", action="store_true",
                    help="允许 PiGate.length / PiHalf.length 不一致或不在 20–40 ns（默认直接中止）")
    ap.add_argument("--pi-length", type=float, default=None,
                    help="执行前把 PiGate.length 与 PiHalf.length 同时设为该值（本机常规 30 ns）")
    ap.add_argument("--force-bias", type=float, default=None,
                    help="跳过 probe，直接用这个 bias_z 跑标定链（人工接受某点后用）")
    ap.add_argument("--execute", action="store_true", help="真跑（会写寄存器 + 驱动真机）")
    ap.add_argument("--dry", action="store_true", help="生成并干跑，不碰硬件")
    ap.add_argument("--plan", action="store_true", help="只做离线规划")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--settle", type=float, default=1.5, help="bias 写入后的稳定等待（秒）")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    events = (args.out / "events.jsonl").open("a", encoding="utf-8")

    def log(msg):
        print(msg, flush=True)
        events.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "msg": msg},
                                ensure_ascii=False) + "\n")
        events.flush()

    # 1) 离线规划（--plan：只读 hdf5，不碰硬件）
    if args.plan:
        zpa_file = args.s21zpa or latest_s21zpa(args.datavault, args.bit)
        if not zpa_file:
            log(f"✗ {args.datavault} 下找不到 {args.bit} 的 S21ZPA 数据")
            return 2
        log(f"[plan] 离线规划：{zpa_file.name}")
        plan = plan_intervals(zpa_file)
        ordered = pick_biases(plan, args)
        log(f"    区间 {[iv['interval'] for iv in plan['intervals']]}（单位 {plan['independent1_unit']}）")
        log(f"    bias 候选（{len(ordered)} 点，步长 {args.bias_step}，从最小值升序）：{ordered}")
        (args.out / f"{args.bit}_plan.json").write_text(
            json.dumps({**plan, "probe_order": ordered}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        log("完成（--plan 模式，未生成/未执行 qwf）")
        return 0

    # 2) 服务预检 + 寄存器快照 + 驱动自检
    health = get(Q_BASE, "/health")
    log(f"[1/5] 服务：connected={health.get('connected')} busy={health.get('busy')} "
        f"stuck={health.get('stuck')} allow_execute={health.get('allow_execute')} "
        f"allow_set_params={health.get('allow_set_params')}")
    if health.get("stuck"):
        log("✗ :3003 stuck，先处理写闸门/卡死（skill §13）")
        return 2
    session = args.session or (health.get("session_path") or ["", ""])[-1]
    log(f"    session={session}")

    reg, err = read_registry(args.bit)
    if err:
        log(f"✗ 读 registry 失败：{err}")
        return 2
    orig_bias = reg.get("bias_z")
    snapshot = {k: reg.get(k) for k in
                ("f10", "f21", "bias_z", "PiGate.amp", "PiGate.alpha",
                 "PiGate.length", "PiGate.zpa", "PiHalf.length", "PiHalf.amp",
                 "PiHalf.alpha", "discriminator.method")}
    log(f"    当前 f10={reg.get('f10')} bias_z={orig_bias} PiGate.amp={reg.get('PiGate.amp')}"
        f" PiGate.length={reg.get('PiGate.length')} PiHalf.length={reg.get('PiHalf.length')}")

    # 驱动脉宽修正（2026-11-12 用户要求）：300 ns 这类超长脉冲会让 π 面积严重不足，
    # 本机常规值是 30 ns；--pi-length 30 会在自检前把两个 length 一起改掉。
    if args.pi_length is not None and args.execute:
        want = {"PiGate.length": args.pi_length, "PiHalf.length": args.pi_length}
        cur = {k: reg.get(k) for k in want}
        if any(abs(float(cur[k] or 0) - args.pi_length) > 1e-9 for k in want):
            log(f"    [fix] 驱动脉宽 {cur} → {want}")
            write_params(args.bit, want)
            reg, err = read_registry(args.bit)
            if err:
                log(f"✗ 回读 registry 失败：{err}")
                return 2
            snapshot.update({k: reg.get(k) for k in want})
            got = {k: reg.get(k) for k in want}
            log(f"    回读：{got}"
                + ("" if all(abs(float(v) - args.pi_length) < 1e-9 for v in got.values())
                   else "  ✗ 回读不一致"))
    pre = preflight_params(args.bit, reg, args.datavault, log,
                           allow_length_mismatch=args.allow_length_mismatch)
    if not pre["ok"]:
        log("✗ 驱动参数自检不过：" + "；".join(pre["issues"])
            + "（修：--pi-length 30 自动改；确知要跳过再加 --allow-length-mismatch）")
        return 2
    # piamp 扫的是绝对幅度：1.6×PiHalf.amp，下限 1.0（保证盖住 π，q9ld3 实测 π≈0.73），
    # 上限 2.0。一上来扫到 2.0 会把 IQ 云推出判别区，P1 饱和成 1.0，看起来像"没有激发"。
    pi_half_amp = float(reg.get("PiHalf.amp") or 0.0)
    pi_scan_amp = args.pi_amp or round(min(max(1.6 * pi_half_amp, 1.0), 2.0), 3)
    log(f"  PiPulse 扫描上限：amp={pi_scan_amp}（PiHalf.amp={pi_half_amp}，"
        f"规则：1.6×PiHalf.amp 夹在 1.0–2.0；过驱动会让判别器饱和）")

    if args.execute and args.set_f10 is not None:
        wr = write_params(args.bit, {"f10": args.set_f10,
                                     "f21": round(args.set_f10 - 0.2, 9)})
        if wr.get("error"):
            log(f"✗ 写 f10 失败：{wr.get('error')}")
            return 2
        reg, err = read_registry(args.bit)
        if err:
            log(f"✗ 读 registry 失败：{err}")
            return 2
        snapshot["f10"] = reg.get("f10")
        snapshot["f21"] = reg.get("f21")
        log(f"    ✓ f10={reg.get('f10')} f21={reg.get('f21')} 已写入并回读")

    # 3) 第一个实验：S21ZPA 地图 → 用它的结果定 bias 区间
    if args.skip_zpa2d:
        zpa_file = args.s21zpa or latest_s21zpa(args.datavault, args.bit)
        if not zpa_file:
            log(f"✗ {args.datavault} 下找不到 {args.bit} 的 S21ZPA 数据")
            return 2
        log(f"[2/5] 跳过 zpa2d，复用 {zpa_file.name}")
        plan = plan_intervals(zpa_file)
    elif args.execute:
        log(f"[2/5] 跑 s21_zpa2d：zpa {args.zpa_lo}…{args.zpa_hi} 步长 {args.zpa_step}（只采不写）")
        zpa_file, plan, zerr = run_zpa2d(args, session, log)
        if zerr:
            log(f"  ✗ {zerr}")
            return 2
        log(f"    数据集：{zpa_file.name}")
    else:
        log("[2/5] 生成 s21_zpa2d 并干跑（不碰硬件）")
        _, _, zerr = run_zpa2d(args, session, log, dry=True)
        if zerr:
            log(f"  ✗ {zerr}")
            return 2
        zpa_file = args.s21zpa or latest_s21zpa(args.datavault, args.bit)
        plan = plan_intervals(zpa_file) if zpa_file else {"intervals": [],
                                                          "independent1_unit": "?"}

    ordered = pick_biases(plan, args)
    log(f"    区间 {[iv['interval'] for iv in plan.get('intervals') or []]}"
        f"（单位 {plan.get('independent1_unit')}）")
    log(f"    bias 候选（{len(ordered)} 点，步长 {args.bias_step}，从最小值升序）：{ordered}")
    (args.out / f"{args.bit}_plan.json").write_text(
        json.dumps({**plan, "probe_order": ordered}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if not ordered:
        log("✗ 区间为空，没有可探测的 bias 点")
        return 2

    if not args.execute:
        # 生成 + 干跑（不碰硬件）
        log("[3/5] 生成 .qwf 并干跑（不碰硬件）")
        b0 = ordered[0]
        f10_cur = float(reg.get("f10") or 0.0)
        samples = [
            ("widespec", build_wide_spec_qwf(args.bit, session, b0, _tag(b0),
                                             args.wide_lo, args.wide_hi,
                                             args.wide_step, args.spec_amp), True),
            ("iqpi", build_iq_pi_qwf(args.bit, session, b0, _tag(b0),
                                     piamp_amp=pi_scan_amp), False),
        ]
        if args.dry:
            samples.append(("chain", build_chain_qwf(args.bit, session, b0, _tag(b0)), True))
        for name, text, need_writes in samples:
            (args.out / f"{args.bit}_{name}_example.qwf").write_text(text, encoding="utf-8")
            ir, verr = parse_validate(text, require_writes=need_writes)
            log(f"    {name} 校验 -> {verr or 'ok'}")
            if ir:
                st = run_ir(ir, {"qubit": args.bit, "session": session}, dry=True, timeout=180)
                log(f"    {name} dryRun -> {st.get('status')}")
        log("完成（未真跑；真跑加 --execute，需先获得人工授权）")
        return 0

    # 4) 逐 bias 探测：先 spectroscopy 判频，通过后再 IQraw + PiPulse
    accepted = args.force_bias
    probe_rows = []
    if args.force_bias is not None:
        log(f"[3/5] 跳过探测：人工接受 bias_z={args.force_bias}")
        wr = write_params(args.bit, {"bias_z": args.force_bias})
        if wr.get("error"):
            log(f"    ✗ 写 bias_z 失败：{wr.get('error')}")
            accepted = None
        else:
            time.sleep(args.settle)
            reg_fb, _ = read_registry(args.bit)
            if abs(float(reg_fb.get("bias_z", 1e9)) - args.force_bias) > 1e-6:
                log(f"    ✗ 写入未生效：回读 bias_z={reg_fb.get('bias_z')}")
                accepted = None
            else:
                log(f"    ✓ bias_z={args.force_bias} 已写入并回读确认")
    else:
        log(f"[3/5] 逐 bias 探测（最多 {len(ordered)} 点；谱线判频 → IQraw+PiPulse；"
            f"visibility≥{args.visibility_threshold}、snr≥{args.snr_threshold}、需有激发）")
        for i, bias in enumerate(ordered, 1):
            log(f"  --- [{i}/{len(ordered)}] bias_z={bias}")
            wr = write_params(args.bit, {"bias_z": bias})
            if wr.get("error"):
                log(f"    ✗ 写 bias_z 失败：{wr.get('error')}")
                break
            time.sleep(args.settle)
            reg2, _ = read_registry(args.bit)
            if abs(float(reg2.get("bias_z", 1e9)) - bias) > 1e-6:
                log(f"    ✗ 写入未生效：回读 bias_z={reg2.get('bias_z')}")
                break
            row = probe_at_bias(args, session, bias, log, pi_scan_amp,
                                pre.get("discriminator_ok"))
            probe_rows.append(row)
            if row.get("error"):
                log(f"    ! {row['error']}")
                _restore_f10(args, row, log)
                continue
            if not row.get("passed"):
                log(f"    原因：{'; '.join(row.get('reasons') or ['未通过'])}")
                _restore_f10(args, row)        # 这点不成立 → f10 复原，避免漂移累积
                continue
            # 优先用窄窗 update=True 由库写进去的 f10（比宽扫候选粗粒度值更准）
            cand = float(row.get("f10_after_spec") or row.get("f10_candidate") or 0.0)
            cur_f10 = float(row["f10_before"])
            if cand and abs(cand - cur_f10) > 5e-4:
                new_f21 = round(float(row["f21_before"]) + (cand - cur_f10), 9)
                wr2 = write_params(args.bit, {"f10": round(cand, 9), "f21": new_f21})
                log(f"    写 f10 {cur_f10:.6f} → {cand:.6f}："
                    f"{'ok' if not wr2.get('error') else wr2['error']}")
            accepted = bias
            log(f"  ✓ 接受 bias_z={bias}（visibility {row['visibility']:.4f}，"
                f"snr {row['snr']}，有 Pi 激发）")
            break

    result = {"bit": args.bit, "s21zpa": str(zpa_file), "plan": plan,
              "probe_order": ordered, "probes": probe_rows, "accepted_bias": accepted,
              "snapshot_before": snapshot, "preflight": pre, "chain": None}

    # 4) 标定链（f10 已由 spectroscopy 的 update=True 写入，不再做洛伦兹拟合计判）
    if accepted is not None and not args.no_chain:
        log(f"[4/4] 标定链：bias_z={accepted}")
        text = build_chain_qwf(args.bit, session, accepted, _tag(accepted))
        (args.out / f"{args.bit}_chain_b{accepted}.qwf").write_text(text, encoding="utf-8")
        ir, verr = parse_validate(text, require_writes=True)
        if verr:
            log(f"  ✗ chain 校验失败：{verr}")
            result["chain"] = {"error": verr}
        else:
            st = run_ir(ir, {"qubit": args.bit, "session": session}, timeout=args.timeout * 2, log=log)
            ds = collect_datasets(st, args.datavault)
            metrics = {}
            kinds = {"d1": "pidf", "d2": "pidf", "r1": "ramsey", "m1": "s21_dis",
                     "q1": "iqraw", "t1": "t1", "q2": "iqraw"}
            for alias, info in ds.items():
                kind = kinds.get(alias, "")
                try:
                    res = load_dv(info["path"])
                    if kind == "iqraw":
                        metrics[alias] = iqraw_visibility(res)
                    elif kind == "t1":
                        metrics[alias] = calib_metrics("t1", res)
                    elif kind in ("ramsey", "s21_dis", "pidf"):
                        metrics[alias] = calib_metrics(kind, res)
                except Exception as exc:
                    metrics[alias] = {"error": str(exc)}
            result["chain"] = {"outcome": st.get("status"),
                               "wf_status": st.get("status"),
                               "nodes": st.get("node_map"),
                               "datasets": {k: v["dataset"] for k, v in ds.items()},
                               "metrics": metrics}
            log(f"  chain outcome={st.get('status')}")

    # 5) 收尾：回读 registry，确认没有静默跑飞
    reg3, _ = read_registry(args.bit)
    result["snapshot_after"] = {k: reg3.get(k) for k in snapshot}
    # 比特参数汇总（报告直接引用这一块）
    try:
        f10r, f21r = float(reg3.get("f10")), float(reg3.get("f21"))
    except (TypeError, ValueError):
        f10r = f21r = None
    qp = {"f10_GHz": reg3.get("f10"), "f21_GHz": reg3.get("f21"),
          "nonlin_GHz": round(f21r - f10r, 6) if f10r and f21r else None,
          "fread_GHz": reg3.get("fread"),
          "readout_power_dB": reg3.get("ReadIn.power"),
          "readout_length_ns": reg3.get("ReadIn.length"),
          "readout_window_type": reg3.get("ReadOut.window_type"),
          "pi_amp": reg3.get("PiGate.amp"), "pi_alpha": reg3.get("PiGate.alpha"),
          "pi_length_ns": reg3.get("PiGate.length"),
          "pi_half_length_ns": reg3.get("PiHalf.length"),
          "bias_z": reg3.get("bias_z"),
          "adc_start_ns": reg3.get("timing.adc_start")}
    f10info = result.get("f10") or {}
    qp["f10_confidence"] = f10info.get("f10_confidence") or (
        "pidf" if f10info.get("applied") else "unconfirmed")
    qp["f10_source"] = ("spectroscopy-fit" if "r2" in f10info
                        else ("pidf-dip" if "pidf_dip_db" in f10info or "pidf_dip_df" in f10info else None))
    qp["discriminator_ok"] = pre.get("discriminator_ok")
    qp["pi_amp_source"] = "PiHalf.amp（piamp/pidf 实际使用的幅度）"
    piamp_rows = [p for p in probe_rows if p.get("p1_contrast") is not None]
    if piamp_rows:
        qp["pi_amp_scan_max"] = max((p.get("datasets") or {}).get("p1_x5", 0) or 0 for p in piamp_rows) or None
        qp["p1_contrast_max"] = max(p["p1_contrast"] for p in piamp_rows)
        qp["pi_amp_converged"] = bool(any(p.get("excited") for p in piamp_rows))
    if result.get("f10"):
        qp["f10_pidf_GHz"] = result["f10"].get("f10_after") or (
            None if result["f10"].get("f10_before") is None
            else round(result["f10"]["f10_before"] + (result["f10"].get("pidf_dip_df") or 0), 6))
    chain_m = (result.get("chain") or {}).get("metrics") or {}
    if "q1" in chain_m and isinstance(chain_m["q1"], dict):
        qp.update({k: chain_m["q1"].get(k) for k in ("visibility", "F0", "F1", "F_avg", "snr")})
    result["qubit_params"] = qp
    result["elapsed"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (args.out / f"{args.bit}_qwf_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with (args.out / f"{args.bit}_summary.csv").open("w", encoding="utf-8-sig", newline="") as fo:
        import csv
        w = csv.writer(fo)
        w.writerow(["bias", "spec_dataset", "line_ok", "iqpi_outcome",
                    "visibility", "snr", "p1_contrast", "excited", "datasets"])
        for r in probe_rows:
            w.writerow([r.get("bias"), r.get("spec_dataset"), r.get("line_ok"),
                        r.get("iqpi_outcome"), r.get("visibility"), r.get("snr"),
                        r.get("p1_contrast"), r.get("excited"),
                        json.dumps(r.get("datasets") or {})])
        w.writerow([])
        w.writerow(["accepted_bias", accepted])
        w.writerow(["bias_z_before", orig_bias, "bias_z_after", reg3.get("bias_z")])
        w.writerow(["f10_before", snapshot.get("f10"),
                    "f10_after", reg3.get("f10")])
    log(f"报告：{args.out / f'{args.bit}_qwf_result.json'}")
    if accepted is None:
        log("✗ 没有 bias 点通过判据；bias_z 未回滚（如需回滚："
            f"set_params {args.bit} bias_z={orig_bias}）")
        return 3
    log(f"✓ 完成：bias_z={accepted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
