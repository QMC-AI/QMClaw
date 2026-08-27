# -*- coding: utf-8 -*-
"""
new_templates — 13 个实验模板
对应原版 quark_templates 的角色

每个模板对象包含实验的元信息（类型、线路结构等），
被 call_interface 使用来区分实验类型并驱动测控系统。
"""

from .s21 import S21_template
from .rabi import rabi_template
from .ramsey import ramsey_template
from .t1 import t1_template
from .spectrum import spectrum_template
from .spectrum_2d import spectrum_2d_template
from .s21vsflux import s21vsflux_template
from .singleshot import singleshot_template
from .drag import drag_template
from .opt_pipulse import opt_pipulse_template
from .powershift import powershift_template
from .delta import delta_template
from .rb import rb_template

__all__ = [
    "S21_template",
    "rabi_template",
    "ramsey_template",
    "t1_template",
    "spectrum_template",
    "spectrum_2d_template",
    "s21vsflux_template",
    "singleshot_template",
    "drag_template",
    "opt_pipulse_template",
    "powershift_template",
    "delta_template",
    "rb_template",
]
