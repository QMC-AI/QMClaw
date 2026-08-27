# -*- coding: utf-8 -*-
"""
DRAG 脉冲优化测量
对应原版 quark_mcp/tools/drag.py
"""

from new_ctrl.task import call_interface
from new_templates import drag_template


def drag(
        qubits: list[str],
        lamb,
        pulsePair,
        stage: int = 1,
        N_repeat: int = 1,
        signal: str = "population",
        plot: bool = True,
        *args, **kwargs
        ):
    """
    DRAG (Derivative Removal by Adiabatic Gate) 脉冲优化实验。

    Args:
        qubits: 量子比特列表
        lamb: DRAG 系数扫描（list）
        stage: 测量阶段
        N_repeat: 重复次数
        pulsePair: 脉冲对
        signal: 信号类型
    """
    tid = call_interface(
        workflow=drag_template,
        qubits=qubits,
        lamb=lamb,
        stage=stage,
        N_repeat=N_repeat,
        pulsePair=pulsePair,
        signal=signal,
        plot=plot,
        *args, **kwargs
    )
    return tid
