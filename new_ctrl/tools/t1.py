# -*- coding: utf-8 -*-
"""
T1 弛豫时间测量
对应原版 quark_mcp/tools/t1.py
"""

from new_ctrl.task import call_interface
from new_templates import t1_template


def t1(
        qubits: list[str],
        delay,
        signal: str = "population",
        plot: bool = True,
        *args, **kwargs
        ):
    """
    T1 弛豫时间测量实验。

    Args:
        qubits: 量子比特列表
        delay: 弛豫延时扫描（list, 秒）
        signal: 信号类型 ('population' / 'iq_avg')
    """
    tid = call_interface(
        workflow=t1_template,
        qubits=qubits,
        delay=delay,
        signal=signal,
        plot=plot,
        *args, **kwargs
    )
    return tid
