# -*- coding: utf-8 -*-
"""
Rabi 振荡测量 → π 脉冲幅度
对应原版 quark_mcp/tools/rabi.py
"""

from new_ctrl.task import call_interface
from new_templates import rabi_template


def rabi(
        qubits: list[str],
        drive_amp,
        width: float = 30e-9,
        signal: str = "iq_avg",
        plot: bool = True,
        *args, **kwargs
        ):
    """
    Rabi 振荡实验。

    Args:
        qubits: 量子比特列表
        drive_amp: 驱动幅度扫描（list）
        width: 脉冲宽度
        signal: 信号类型 ('iq_avg' / 'population')
    """
    tid = call_interface(
        workflow=rabi_template,
        qubits=qubits,
        drive_amp=drive_amp,
        width=width,
        signal=signal,
        plot=plot,
        *args, **kwargs
    )
    return tid
