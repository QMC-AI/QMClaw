# -*- coding: utf-8 -*-
"""
单发读取测量
对应原版 quark_mcp/tools/singleshot.py
"""

from new_ctrl.task import call_interface
from new_templates import singleshot_template


def singleshot(
        qubits: list[str],
        stage: int = 1,
        plot: bool = True,
        *args, **kwargs
        ):
    """
    单发读取实验。

    Args:
        qubits: 量子比特列表
        stage: 测量阶段
    """
    tid = call_interface(
        workflow=singleshot_template,
        qubits=qubits,
        stage=stage,
        plot=plot,
        *args, **kwargs
    )
    return tid
