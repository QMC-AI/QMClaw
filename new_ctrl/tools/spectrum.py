# -*- coding: utf-8 -*-
"""
一维能谱测量
对应原版 quark_mcp/tools/spectrum.py
"""

from new_ctrl.task import call_interface
from new_templates import spectrum_template


def spectrum(
        qubits: list[str],
        freq,
        drive_amp: float = 0.0,
        duration: float = 100e-9,
        from_idle: bool = True,
        absolute: bool = True,
        signal: str = "population",
        build_dependencies: bool = False,
        plot: bool = True,
        *args, **kwargs
        ):
    """
    一维能谱实验。

    Args:
        qubits: 量子比特列表
        freq: 频率扫描（list, Hz）
        drive_amp: 驱动幅度
        duration: 脉冲持续时间
        from_idle: 是否从 idle 态开始
        absolute: 频率是否绝对
        signal: 信号类型
        build_dependencies: 是否构建依赖
    """
    tid = call_interface(
        workflow=spectrum_template,
        qubits=qubits,
        freq=freq,
        drive_amp=drive_amp,
        duration=duration,
        from_idle=from_idle,
        absolute=absolute,
        signal=signal,
        build_dependencies=build_dependencies,
        plot=plot,
        *args, **kwargs
    )
    return tid
