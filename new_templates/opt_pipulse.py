# -*- coding: utf-8 -*-
"""最优 π 脉冲实验模板"""


class opt_pipulse_template:
    """最优 π 脉冲实验模板"""
    name = "OptPiPulse"
    signal = "population"
    description = "最优 π 脉冲测量"
    default_params = {
        "stage": 1,
        "N_list": [1, 3, 5],
        "amp_list": [0.5, 1.5],
        "delay": 20e-9,
    }
