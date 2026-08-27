# -*- coding: utf-8 -*-
"""
Delta 实验（频率偏移校准）
对应原版 quark_mcp/tools/delta.py
"""

from new_ctrl.task import call_interface
from new_templates import delta_template


def delta(
        qubits: list[str],
        delta_list,
        N_list: list[int] = [1, 5, 13],
        stage: int = 1,
        delay: float = 20e-9,
        plot: bool = True,
        *args, **kwargs
        ):
    """
    Delta 实验。

    Args:
        qubits: 量子比特列表
        N_list: 脉冲个数列表
        delta_list: 频率偏移扫描（list, Hz）
        stage: 测量阶段
        delay: 脉冲间隔
    """
    tid = call_interface(
        workflow=delta_template,
        qubits=qubits,
        N_list=N_list,
        delta_list=delta_list,
        stage=stage,
        delay=delay,
        plot=plot,
        *args, **kwargs
    )
    return tid
