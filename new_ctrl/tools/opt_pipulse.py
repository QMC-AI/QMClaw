# -*- coding: utf-8 -*-
"""
最优 π 脉冲测量
对应原版 quark_mcp/tools/opt_pipulse.py
"""

from new_ctrl.task import call_interface
from new_templates import opt_pipulse_template


def opt_pipulse(
        qubits: list[str],
        amp_list,
        stage: int = 1,
        N_list: list[int] = [1, 3, 5],
        delay: float = 20e-9,
        signal: str = "population",
        plot: bool = True,
        *args, **kwargs
        ):
    """
    最优 π 脉冲实验。

    Args:
        qubits: 量子比特列表
        stage: 测量阶段
        N_list: 脉冲个数列表
        amp_list: 幅度扫描（list）
        delay: 脉冲间隔
        signal: 信号类型
    """
    tid = call_interface(
        workflow=opt_pipulse_template,
        qubits=qubits,
        stage=stage,
        N_list=N_list,
        amp_list=amp_list,
        delay=delay,
        signal=signal,
        plot=plot,
        *args, **kwargs
    )
    return tid
