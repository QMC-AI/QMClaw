# -*- coding: utf-8 -*-
"""
S21 腔频/比特频率测量
对应原版 quark_mcp/tools/s21.py
"""

from new_ctrl.task import call_interface
from new_templates import S21_template


def s21(
        qubits: list[str],
        frequency_start: float = -40e6,
        frequency_end: float = 40e6,
        frequency_sample_num: int = 101,
        state: int | list[int] | None = [0],
        plot: bool = True,
        *args, **kwargs
        ):
    """
    S21 频率扫描测量。

    Args:
        qubits: 量子比特列表，如 ['Q0', 'Q1']
        frequency_start: 起始频率 (Hz)
        frequency_end: 终止频率 (Hz)
        frequency_sample_num: 频率采样点数
        state: 量子比特初始态，如 [0]
        plot: 是否绘图
    """
    tid = call_interface(
        workflow=S21_template,
        qubits=qubits,
        frequency_start=frequency_start,
        frequency_end=frequency_end,
        frequency_sample_num=frequency_sample_num,
        state=state,
        plot=plot,
        *args, **kwargs
    )
    return tid
