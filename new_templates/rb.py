# -*- coding: utf-8 -*-
"""随机基准测试实验模板"""


class rb_template:
    """随机基准测试实验模板"""
    name = "RB"
    signal = "population"
    description = "随机基准测试"
    default_params = {
        "couplers": tuple([]),
        "stage": 3,
        "gate": ['ref'],
        "cycle": [1, 1000],
        "size": 11,
    }
