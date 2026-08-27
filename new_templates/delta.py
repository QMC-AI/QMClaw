# -*- coding: utf-8 -*-
"""Delta 实验模板"""


class delta_template:
    """Delta 实验（频率偏移校准）模板"""
    name = "Delta"
    signal = "population"
    description = "频率偏移校准"
    default_params = {
        "N_list": [1, 5, 13],
        "delta_list": [-20e6, 20e6],
        "stage": 1,
        "delay": 20e-9,
    }
