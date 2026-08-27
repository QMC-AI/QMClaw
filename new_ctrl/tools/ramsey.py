# -*- coding: utf-8 -*-
"""
Ramsey 干涉测量 → T2* / 频率精校
对应原版 quark_mcp/tools/ramsey.py
"""

from new_ctrl.task import call_interface
from new_templates import ramsey_template


def ramsey(
        qubits: list[str],
        delta,
        delay,
        stage: int = 1,
        scale: int = 15,
        signal: str = "population",
        plot: bool = True,
        *args, **kwargs
        ):
    """
    Ramsey 干涉实验。

    Args:
        qubits: 量子比特列表
        delta: 失谐频率 (Hz)
        delay: 延时扫描（list）
        stage: 测量阶段
        scale: 缩放因子
    """
    tid = call_interface(
        workflow=ramsey_template,
        qubits=qubits,
        delta=delta,
        delay=delay,
        stage=stage,
        scale=scale,
        signal=signal,
        plot=plot,
        *args, **kwargs
    )
    return tid
