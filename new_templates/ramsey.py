# -*- coding: utf-8 -*-
"""Ramsey 实验模板"""


class ramsey_template:
    """Ramsey 干涉实验模板"""
    name = "Ramsey"
    signal = "population"
    description = "Ramsey 干涉测量 → T2* / 频率精校"
    default_params = {
        "delta": 20e6,
        "delay": [1e-9],
        "stage": 1,
        "scale": 15,
    }
