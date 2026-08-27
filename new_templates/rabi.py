# -*- coding: utf-8 -*-
"""Rabi 实验模板"""


class rabi_template:
    """Rabi 振荡实验模板"""
    name = "Rabi"
    signal = "iq_avg"
    description = "Rabi 振荡测量 → π 脉冲幅度"
    default_params = {
        "drive_amp": [0.01, 0.1],
        "width": 30e-9,
    }
