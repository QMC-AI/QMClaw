# -*- coding: utf-8 -*-
"""
功率偏移曲线测量
对应原版 quark_mcp/tools/powershift.py
"""

from new_ctrl.task import call_interface
from new_templates import powershift_template


def powershift(
        qubits: list[str],
        power,
        freq,
        plot: bool = True,
        *args, **kwargs
        ):
    """
    功率偏移实验。

    Args:
        qubits: 量子比特列表
        power: 功率扫描（list, dBm）
        freq: 频率扫描（list, Hz）
    """
    tid = call_interface(
        workflow=powershift_template,
        qubits=qubits,
        power=power,
        freq=freq,
        plot=plot,
        *args, **kwargs
    )
    return tid
