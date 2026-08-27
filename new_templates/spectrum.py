# -*- coding: utf-8 -*-
"""一维能谱实验模板"""


class spectrum_template:
    """一维能谱实验模板"""
    name = "Spectrum"
    signal = "population"
    description = "一维能谱测量"
    default_params = {
        "freq": [-100e6, 100e6],
        "drive_amp": 0.0,
        "duration": 100e-9,
        "from_idle": True,
        "absolute": True,
        "build_dependencies": False,
    }
