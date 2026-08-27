# -*- coding: utf-8 -*-
"""
二维能谱测量（频率 vs 偏置）
对应原版 quark_mcp/tools/spectrum_2d.py
"""

from new_ctrl.task import call_interface
from new_templates import spectrum_2d_template


def spectrum_2d(
        qubits: list[str],
        freq,
        bias,
        drive_amp: float = 0.0,
        duration: float = 100e-9,
        from_idle: bool = False,
        absolute: bool = True,
        plot: bool = True,
        *args, **kwargs
        ):
    """
    二维能谱实验。

    Args:
        qubits: 量子比特列表
        freq: 频率扫描（list, Hz）
        bias: 偏置扫描（list）
        drive_amp: 驱动幅度
        duration: 脉冲持续时间
        from_idle: 是否从 idle 态开始
        absolute: 频率是否绝对
    """
    tid = call_interface(
        workflow=spectrum_2d_template,
        qubits=qubits,
        freq=freq,
        bias=bias,
        drive_amp=drive_amp,
        duration=duration,
        from_idle=from_idle,
        absolute=absolute,
        plot=plot,
        *args, **kwargs
    )
    return tid
