# -*- coding: utf-8 -*-
"""功率偏移实验模板"""


class powershift_template:
    """功率偏移曲线实验模板"""
    name = "PowerShift"
    signal = "iq_avg"
    description = "功率偏移曲线测量"
    default_params = {
        "power": [-40, 0],
        "freq": [-100e6, 100e6],
    }
