# -*- coding: utf-8 -*-
"""S21 实验模板"""


class S21_template:
    """S21 频率扫描实验模板"""
    name = "S21"
    signal = "S"  # 网分信号
    description = "S21 腔频/比特频率测量"
    # 实验线路结构，供 call_interface 使用
    circuit = [["GET", "S", "NA.CH1"]]
    # 默认参数
    default_params = {
        "frequency_start": -40e6,
        "frequency_end": 40e6,
        "frequency_sample_num": 101,
        "state": [0],
    }
