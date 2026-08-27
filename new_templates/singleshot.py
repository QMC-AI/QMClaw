# -*- coding: utf-8 -*-
"""单发读取实验模板"""


class singleshot_template:
    """单发读取实验模板"""
    name = "SingleShot"
    signal = "iq"  # 单发，不平均
    description = "单发读取测量"
    default_params = {
        "stage": 1,
    }
