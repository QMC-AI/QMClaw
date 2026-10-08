# -*- coding: utf-8 -*-
"""
T1 离线拟合脚本 (QMClaw) — v2
复用 quantum_service 的 ExperimentSimulator（加载真实离线数据），
对 T1 弛豫数据做指数衰减拟合，输出 T1 时间常数。
用法: python t1_fit.py <qubit>
示例: python t1_fit.py q5lu8
"""
import sys
import json
import numpy as np
from scipy.optimize import curve_fit


def get_t1_data(qubit: str):
    """通过 ExperimentSimulator 生成 T1 模拟数据，返回 (t, y, source)"""
    sys.path.insert(0, r"D:\bishe\QMClaw\Agentic Workflow\qmclaw-server")
    try:
        from services.common.offline_data_provider import OfflineDataProvider
        from services.quantum_service.offline_simulator import ExperimentSimulator

        provider = OfflineDataProvider(
            data_path=r"D:\bishe\deploy\offline_data"
        )
        sim = ExperimentSimulator(provider)
        result = sim.simulate(f"sq.t1({qubit}, do_plot=True)")
        if result.get("status") != "offline_simulated":
            raise RuntimeError(f"模拟失败: {result.get('status')}")

        data = result.get("data") or {}
        x = np.asarray(data.get("x", []), dtype=float)
        y = np.asarray(data.get("y", []), dtype=float)
        if len(x) == 0 or len(y) == 0:
            raise RuntimeError("无数据点")

        # 找出是否有真实数据集来源
        source = result.get("source_data") or result.get("dataset_id") or "synthetic"
        return x, y, source, result
    except Exception as e:
        raise RuntimeError(f"模拟器调用失败: {e}")


def fit_t1(t, y):
    """指数衰减拟合: y = A*exp(-t/tau) + offset"""
    def decay(x, A, tau, offset):
        return A * np.exp(-x / tau) + offset

    A0 = max(y) - min(y)
    off0 = min(y)
    tau0 = (max(t) - min(t)) / 5
    if tau0 <= 0:
        tau0 = 1.0
    popt, pcov = curve_fit(decay, t, y, p0=[A0, tau0, off0], maxfev=10000)
    perr = np.sqrt(np.diag(pcov))
    return popt, perr, decay


def main():
    qubit = sys.argv[1] if len(sys.argv) > 1 else "q5lu8"
    print(f"=== T1 拟合: {qubit} ===")

    try:
        t, y, source, result = get_t1_data(qubit)
        print(f"数据来源: {source}")
        # 若模拟器自带 metrics，展示其估算
        if result.get("metrics", {}).get("t1"):
            print(f"模拟器估算 T1: {result['metrics']['t1'] * 1e6:.2f} us")
    except RuntimeError as e:
        print(f"!! {e}")
        print("回退: 用合成指数衰减数据演示拟合流程")
        rng = np.random.default_rng(42)
        t = np.linspace(0, 120, 60)
        tau_true = 35.0
        y = np.exp(-t / tau_true) + rng.normal(0, 0.03, 60)
        source = "synthetic_demo"

    print(f"数据点: {len(t)}, 时间范围: {t.min():.3e} ~ {t.max():.3e} s")

    popt, perr, decay = fit_t1(t, y)
    A, tau, offset = popt
    dA, dtau, doff = perr

    print("\n--- 拟合结果 ---")
    print(f"T1 时间常数 τ = {tau:.4e} s  =  {tau * 1e6:.2f} us ± {dtau * 1e6:.2f} us")
    print(f"初始幅度 A   = {A:.3f} ± {dA:.3f}")
    print(f"本底 offset  = {offset:.3f} ± {doff:.3f}")

    ss_res = np.sum((y - decay(t, *popt)) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot
    print(f"拟合优度 R²  = {r2:.4f}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.scatter(t * 1e6, y, s=12, label="data")
        xs = np.linspace(t.min(), t.max(), 300)
        ax.plot(xs * 1e6, decay(xs, *popt), "r-", lw=2,
                label=f"fit: τ={tau*1e6:.1f}us, R²={r2:.3f}")
        ax.set_xlabel("Time (us)")
        ax.set_ylabel("Amplitude")
        ax.set_title(f"T1 relaxation fit — {qubit} (offline)")
        ax.legend()
        out = f"t1_fit_{qubit}.png"
        fig.savefig(out, dpi=120, bbox_inches="tight")
        print(f"\n拟合图已保存: {out}")
    except Exception as e:
        print(f"\n(绘图失败，不影响结果: {e})")


if __name__ == "__main__":
    main()
