# -*- coding: utf-8 -*-
"""
S21 vs Flux 测量（全可调芯片）
对应原版 quark_mcp/tools/s21vsflux.py
"""

from new_ctrl.task import call_interface
from new_templates import s21vsflux_template


def s21vsflux(
        qubits_scan: list[str],
        qubits_read: list[str],
        freq,
        read_bias,
        plot: bool = True,
        *args, **kwargs
        ):
    """
    S21 随 Flux 变化实验。

    Args:
        qubits_scan: 被调谐的量子比特列表
        qubits_read: 被读取的量子比特列表
        freq: 频率扫描（list, Hz）
        read_bias: 读取偏置扫描（list）
    """
    tid = call_interface(
        workflow=s21vsflux_template,
        qubits_scan=qubits_scan,
        qubits_read=qubits_read,
        freq=freq,
        read_bias=read_bias,
        plot=plot,
        *args, **kwargs
    )
    return tid
