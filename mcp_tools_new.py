# -*- coding: utf-8 -*-
"""
新的 MCP 服务代码 — 参照 mcp_tools.py
包含的实验不变（13 个 @mcp.tool），底层对接新的测控系统。

区别于原版:
  - 原版: from qubitctrl import mcp  → from quark_mcp.tools import s21 → call_interface(...)
  - 本版: from swiftmcp import mcp   → from new_ctrl.tools import s21  → call_interface(...)
           ↑ 或直接调你自己的测控函数

使用:
  python mcp_tools_new.py                    # stdio 模式
  # 或在 .mcp.json / 配置里用 streamable_http 启动
"""

import json
import numpy as np

from swiftmcp import FastMCP
mcp = FastMCP('quantum-service')
from new_ctrl.tools import (                # ← 你的新测控系统 (见下方 new_ctrl 目录)
    s21 as new_s21,
    rabi as new_rabi,
    ramsey as new_ramsey,
    t1 as new_t1,
    spectrum as new_spectrum,
    spectrum_2d as new_spectrum_2d,
    s21vsflux as new_s21vsflux,
    singleshot as new_singleshot,
    drag as new_drag,
    opt_pipulse as new_opt_pipulse,
    powershift as new_powershift,
    delta as new_delta,
    rb as new_rb,
)
from new_ctrl.task import (                  # ← 通用接口
    get_data as get_data_by_rid,
    query_param as query_new_param,
    update_param as update_new_param,
)


# ═══════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════
def convert_ndarray(obj):
    """把 numpy 数组转成可 JSON 序列化的 list。"""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_ndarray(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_ndarray(i) for i in obj]
    else:
        return obj


# ═══════════════════════════════════════════════════════════════
#  通用接口 (不依赖具体实验)
# ═══════════════════════════════════════════════════════════════

@mcp.tool
def get_data(rid: int | str):
    """根据实验记录 ID (rid) 获取测量数据。"""
    data = get_data_by_rid(rid)
    return convert_ndarray(data)


@mcp.tool
def query_param(key: str):
    """查询指定 key 的参数值。"""
    value = query_new_param(key)
    return value


@mcp.tool
def update_param(key: str, value):
    """更新指定 key 的参数值。"""
    update_new_param(key, value)
    return "success"


# ═══════════════════════════════════════════════════════════════
#  13 个实验任务 (@mcp.tool)
#  接口与原版 mcp_tools.py 完全一致，底层对接 new_ctrl
# ═══════════════════════════════════════════════════════════════

@mcp.tool
def s21(
        qubits: list[str] = ['Q0', 'Q1'],
        frequency_center: float = 6.5,
        frequency_half_bandwidth: float = 0.0005,
        frequency_sample_num: int = 101,
        state: int | list[int] | None = [0],
        plot: bool = False,
        ):
    """
    S21 腔频/比特频率测量。
    参数用频率中心(GHz) + 半带宽(GHz)，内部转换成 start/end(Hz)。
    """
    frequency_start = (frequency_center - frequency_half_bandwidth) * 1e6
    frequency_end   = (frequency_center + frequency_half_bandwidth) * 1e6
    if isinstance(state, int):
        state = [state]
    tid = new_s21(
        qubits=qubits,
        frequency_start=frequency_start,
        frequency_end=frequency_end,
        frequency_sample_num=frequency_sample_num,
        state=state,
        plot=plot,
    )
    return str(tid)  # 返回实验记录 ID（字符串）


@mcp.tool
def rabi(
        qubits: list[str] = ['Q0', 'Q1'],
        amp_start: float = 0,
        amp_end: float = 2,
        amp_sample_num: int = 16,
        width: float = 30e-9,
        signal: str = 'iq_avg',
        plot: bool = True,
        ):
    """Rabi 振荡测量 → π 脉冲幅度。"""
    drive_amp = np.linspace(amp_start, amp_end, amp_sample_num).tolist()
    tid = new_rabi(
        qubits=qubits,
        drive_amp=drive_amp,
        width=width,
        signal=signal,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def ramsey(
        qubits: list[str] = ['Q0', 'Q2'],
        delta: float = 20e6,
        delay_start: float = 0,
        delay_end: float = 100,
        delay_sample_num: int = 100,
        stage: int = 1,
        scale: int = 15,
        signal: str = 'population',
        plot: bool = True,
        ):
    """Ramsey 干涉测量 → T2* / 频率精校。"""
    delay = np.linspace(delay_start, delay_end, delay_sample_num).tolist()
    delay = (np.array(delay) * 1e-9).tolist()
    tid = new_ramsey(
        qubits=qubits,
        delta=delta,
        delay=delay,
        stage=stage,
        scale=scale,
        signal=signal,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def t1(
        qubits: list[str] = ['Q0', 'Q1'],
        delay_start: float = 0,
        delay_end: float = 80000,
        delay_sample_num: int = 17,
        signal: str = 'population',
        plot: bool = True,
        ):
    """T1 弛豫时间测量。"""
    delay = np.linspace(delay_start, delay_end, delay_sample_num).tolist()
    delay = (np.array(delay) * 1e-9).tolist()
    tid = new_t1(
        qubits=qubits,
        delay=delay,
        signal=signal,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def spectrum(
        qubits: list[str] = ['Q0', 'Q1'],
        freq_start: float = -3,
        freq_end: float = 3,
        freq_sample_num: int = 200,
        drive_amp: float = 0.0,
        duration: float = 100e-9,
        from_idle: bool = True,
        absolute: bool = True,
        signal: str = 'population',
        build_dependencies: bool = False,
        plot: bool = True,
        ):
    """一维能谱测量。"""
    freq = np.linspace(freq_start, freq_end, freq_sample_num).tolist()
    freq = (np.array(freq) * 1e6).tolist()
    tid = new_spectrum(
        qubits=qubits,
        freq=freq,
        drive_amp=drive_amp,
        duration=duration,
        from_idle=from_idle,
        absolute=absolute,
        signal=signal,
        build_dependencies=build_dependencies,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def spectrum_2d(
        qubits: list[str] = ['Q0', 'Q1'],
        freq_start: float = -3,
        freq_end: float = 3,
        freq_sample_num: int = 200,
        bias_start: float = -1,
        bias_end: float = 1,
        bias_sample_num: int = 100,
        drive_amp: float = 0.0,
        duration: float = 100e-9,
        from_idle: bool = False,
        absolute: bool = True,
        plot: bool = True,
        ):
    """二维能谱测量（频率 vs 偏置）。"""
    freq = np.linspace(freq_start, freq_end, freq_sample_num).tolist()
    freq = (np.array(freq) * 1e6).tolist()
    bias = np.linspace(bias_start, bias_end, bias_sample_num).tolist()
    tid = new_spectrum_2d(
        qubits=qubits,
        freq=freq,
        bias=bias,
        drive_amp=drive_amp,
        duration=duration,
        from_idle=from_idle,
        absolute=absolute,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def s21vsflux(
        qubits_scan: list[str] = ['Q0', 'Q1'],
        qubits_read: list[str] = None,
        freq_center: float = 6.5,
        freq_half_bandwidth: float = 0.03,
        freq_sample_num: int = 11,
        read_bias_start: float = -3,
        read_bias_end: float = 3,
        read_bias_sample_num: int = 16,
        plot: bool = True,
        ):
    """S21 vs Flux 测量（全可调芯片）。"""
    freq_start = freq_center - freq_half_bandwidth
    freq_end   = freq_center + freq_half_bandwidth
    freq = np.linspace(freq_start, freq_end, freq_sample_num).tolist()
    freq = (np.array(freq) * 1e6).tolist()
    read_bias = np.linspace(read_bias_start, read_bias_end, read_bias_sample_num).tolist()
    if qubits_read is None:
        qubits_read = qubits_scan
    tid = new_s21vsflux(
        qubits_scan=qubits_scan,
        qubits_read=qubits_read,
        freq=freq,
        read_bias=read_bias,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def singleshot(
        qubits: list[str] = ['Q0', 'Q1'],
        stage: int = 1,
        plot: bool = True,
        ):
    """单发读取测量。"""
    tid = new_singleshot(
        qubits=qubits,
        stage=stage,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def drag(
        qubits: list[str] = ['Q0', 'Q1'],
        lamb: list[float] = [-0.5, 0.5],
        stage: int = 1,
        N_repeat: int = 1,
        pulsePair: list[int] = [0, 1],
        signal: str = 'population',
        plot: bool = True,
        ):
    """DRAG 脉冲优化测量。"""
    tid = new_drag(
        qubits=qubits,
        lamb=lamb,
        stage=stage,
        N_repeat=N_repeat,
        pulsePair=pulsePair,
        signal=signal,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def opt_pipulse(
        qubits: list[str] = ['Q0', 'Q1'],
        stage: int = 1,
        N_list: list[int] = [1, 3, 5],
        amp_list: list[float] = None,
        delay: float = 20e-9,
        signal: str = 'population',
        plot: bool = True,
        ):
    """最优 π 脉冲测量。"""
    if amp_list is None:
        amp_list = np.linspace(0.5, 1.5, 51).tolist()
    tid = new_opt_pipulse(
        qubits=qubits,
        stage=stage,
        N_list=N_list,
        amp_list=amp_list,
        delay=delay,
        signal=signal,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def powershift(
        qubits: list[str] = ['Q0', 'Q1'],
        power_start: float = -40,
        power_end: float = 0,
        power_sample_num: int = 27,
        freq_start: float = -100e6,
        freq_end: float = 100e6,
        freq_sample_num: int = 200,
        plot: bool = True,
        ):
    """功率偏移曲线测量。"""
    power = np.linspace(power_start, power_end, power_sample_num).tolist()
    freq = np.linspace(freq_start, freq_end, freq_sample_num).tolist()
    tid = new_powershift(
        qubits=qubits,
        power=power,
        freq=freq,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def delta(
        qubits: list[str] = ['Q0', 'Q1'],
        N_list: list[int] = [1, 5, 13],
        delta_start: float = -20,
        delta_end: float = 20,
        delta_sample_num: int = 101,
        stage: int = 1,
        delay: float = 20e-9,
        plot: bool = True,
        ):
    """Delta 实验（频率偏移校准）。"""
    delta_list = np.linspace(delta_start, delta_end, delta_sample_num).tolist()
    delta_list = (np.array(delta_list) * 1e6).tolist()
    tid = new_delta(
        qubits=qubits,
        N_list=N_list,
        delta_list=delta_list,
        stage=stage,
        delay=delay,
        plot=plot,
    )
    return str(tid)


@mcp.tool
def rb(
        qubits: list[str],
        couplers: tuple = tuple([]),
        stage: int = 3,
        gate: list = ['ref'],
        cycle: list = None,
        size: int = 11,
        plot: bool = True,
        ):
    """随机基准测试。"""
    if gate is None:
        gate = [
            'ref',
            [('Y/2', 0)],
            [('I', 0)],
            [('X', 0)],
            [('X/2', 0)],
            [('Y', 0)],
        ][:1]
    if cycle is None:
        cycle = np.unique(np.logspace(0, np.log10(1000), 21, dtype=int)).tolist()
    tid = new_rb(
        qubits=qubits,
        couplers=couplers,
        stage=stage,
        gate=gate,
        cycle=cycle,
        size=size,
        plot=plot,
    )
    return str(tid)


# ═══════════════════════════════════════════════════════════════
#  启动
# ═══════════════════════════════════════════════════════════════
import os

if __name__ == "__main__":
    # 传输方式通过环境变量控制，默认 streamable-http
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")

    if transport == "stdio":
        print("启动新的 MCP 服务 [stdio] ...")
        mcp.run(transport="stdio")
    else:
        port = int(os.environ.get("MCP_PORT", "8008"))
        print(f"启动新的 MCP 服务 [streamable-http:{port}] ...")
        mcp.run(transport=transport, port=port)
