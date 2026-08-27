# -*- coding: utf-8 -*-
"""T1 实验模板"""


class t1_template:
    """T1 弛豫时间实验模板"""
    name = "T1"
    signal = "population"
    description = "T1 弛豫时间测量"
    default_params = {
        "delay": [0, 80e-6],
    }
