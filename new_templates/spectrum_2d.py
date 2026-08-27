# -*- coding: utf-8 -*-
"""二维能谱实验模板"""


class spectrum_2d_template:
    """二维能谱实验模板（频率 vs 偏置）"""
    name = "Spectrum2D"
    signal = "population"
    description = "二维能谱测量（频率 vs 偏置）"
    default_params = {
        "freq": [-100e6, 100e6],
        "bias": [-0.1, 0.1],
        "drive_amp": 0.0,
        "duration": 100e-9,
        "from_idle": False,
        "absolute": True,
    }
