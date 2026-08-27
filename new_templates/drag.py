# -*- coding: utf-8 -*-
"""DRAG 实验模板"""


class drag_template:
    """DRAG 脉冲优化实验模板"""
    name = "DRAG"
    signal = "population"
    description = "DRAG 脉冲优化测量"
    default_params = {
        "lamb": [-0.5, 0.5],
        "stage": 1,
        "N_repeat": 1,
        "pulsePair": [0, 1],
    }
