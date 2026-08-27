# -*- coding: utf-8 -*-
"""
随机基准测试
对应原版 quark_mcp/tools/rb_1q.py
"""

from new_ctrl.task import call_interface
from new_templates import rb_template


def rb(
        qubits: list[str],
        couplers: tuple = tuple([]),
        stage: int = 3,
        gate=None,
        cycle=None,
        size: int = 11,
        plot: bool = True,
        *args, **kwargs
        ):
    """
    随机基准测试实验。

    Args:
        qubits: 量子比特列表
        couplers: 耦合器元组
        stage: 测量阶段
        gate: 门列表
        cycle: 循环周期列表
        size: 循环采样大小
    """
    if gate is None:
        gate = [
            'ref',
            [('Y/2', 0)],
            [('I', 0)],
            [('X', 0)],
            [('X/2', 0)],
            [('Y', 0)],
        ][:1]
    if cycle is None:
        import numpy as np
        cycle = np.unique(np.logspace(0, np.log10(1000), 21, dtype=int)).tolist()

    tid = call_interface(
        workflow=rb_template,
        qubits=qubits,
        couplers=couplers,
        stage=stage,
        gate=gate,
        cycle=cycle,
        size=size,
        plot=plot,
        *args, **kwargs
    )
    return tid
