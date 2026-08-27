# -*- coding: utf-8 -*-
"""S21 vs Flux 实验模板"""


class s21vsflux_template:
    """S21 随 Flux 变化实验模板（全可调芯片）"""
    name = "S21vsFlux"
    signal = "iq_avg"
    description = "S21 vs Flux 测量"
    default_params = {
        "qubits_scan": ["Q0", "Q1"],
        "qubits_read": ["Q0", "Q1"],
        "freq": [-3e6, 3e6],
        "read_bias": [-3, 3],
    }
