# -*- coding: utf-8 -*-
"""
测控系统适配模板 — 基于此文件实现你的 _execute_workflow()

使用方法:
  1. 复制本文件为 task.py（或直接修改 task.py）
  2. 在 _execute_workflow() 中按 workflow.name 分发到你的测控系统函数
  3. 确保返回值格式符合输出契约（见函数体内注释）
  4. 运行 python tests/test_smoke.py 验证

只需改 _execute_workflow()，其他函数（call_interface/get_data/query_param/update_param）
无需改动。
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── 参数存储 ──────────────────────────────────────────────────
_PARAM_STORE: dict = {}

# ── 数据存储（tid → result）─────────────────────────────────
_DATA_STORE: dict = {}


# ═══════════════════════════════════════════════════════════════
#  通用接口（无需改动）
# ═══════════════════════════════════════════════════════════════

def call_interface(workflow, **kwargs) -> str:
    """调用测控接口执行实验，返回 tid。"""
    tid = f"tid_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    try:
        result = _execute_workflow(workflow, tid, **kwargs)
    except Exception as e:
        logger.error("执行实验失败: %s", e)
        result = {"error": str(e)}
    _DATA_STORE[tid] = result
    return tid


def get_data(rid) -> dict:
    """根据 tid 获取测量数据。"""
    return _DATA_STORE.get(str(rid), {})


def query_param(key: str):
    """查询参数值。"""
    return _PARAM_STORE.get(key)


def update_param(key: str, value):
    """更新参数值。"""
    _PARAM_STORE[key] = value
    return True


# ═══════════════════════════════════════════════════════════════
#  【你要改的唯一函数】_execute_workflow
# ═══════════════════════════════════════════════════════════════

def _execute_workflow(workflow, tid, **kwargs):
    """
    驱动你的测控系统执行实验。

    Args:
        workflow: 实验模板对象
            - workflow.name: 实验名，用于区分实验类型
        tid: 实验记录 ID（字符串）
        **kwargs: 实验参数

    Returns:
        dict: {
            "data": { ... },   # 必选，见下方"输出契约"
            "meta": { ... },   # 可选
        }

    输出契约（data 字段）:
        实验类型        data 键名       数据类型
        ─────────  ──────────  ──────────
        S21         s21         list[complex]
        Rabi        population  list[float]
        T1          population  list[float]
        Ramsey      population  list[float]
        SingleShot  iq          list[list[complex]]
        Spectrum    population  list[float]
        Spectrum2D  population  list[list[float]]
        PowerShift  iq_avg      list[list[complex]]
        S21vsFlux   iq_avg      list[list[complex]]
        DRAG        population  list[float]
        OptPiPulse  population  list[float]
        Delta       population  list[float]
        RB          population  list[float]
    """
    wf_name = getattr(workflow, 'name', str(workflow))
    logger.debug("执行实验: %s, tid=%s, kwargs=%s", wf_name, tid, kwargs)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  在这里实现你的测控系统调用
    #  以下是示例，请替换成你自己的实现
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # TODO: 导入你的测控系统模块
    # from my_lab.hardware import run_s21, run_rabi, run_t1, ...

    if wf_name == 'S21':
        # TODO: 调用你的 S21 实验函数
        # result = run_s21(
        #     qubits=kwargs['qubits'],
        #     freq_start=kwargs['frequency_start'],
        #     freq_end=kwargs['frequency_end'],
        #     n_points=kwargs['frequency_sample_num'],
        # )
        # return {
        #     "data": {"s21": result['s21']},        # list[complex]
        #     "meta": {"freq": {"def": result['freq']}},
        # }
        pass

    elif wf_name == 'Rabi':
        # TODO: 调用你的 Rabi 实验函数
        # result = run_rabi(qubits=..., amp=..., width=...)
        # return {
        #     "data": {"population": result['population']},  # list[float]
        #     "meta": {"drive_amp": {"def": result['amp']}},
        # }
        pass

    elif wf_name == 'T1':
        # TODO: 调用你的 T1 实验函数
        # result = run_t1(qubits=..., delay=...)
        # return {
        #     "data": {"population": result['population']},  # list[float]
        #     "meta": {"delay": {"def": result['delay']}},
        # }
        pass

    elif wf_name == 'Ramsey':
        # TODO: 调用你的 Ramsey 实验函数
        pass

    elif wf_name == 'SingleShot':
        # TODO: 调用你的单发读取实验函数
        pass

    elif wf_name == 'Spectrum':
        # TODO: 调用你的能谱实验函数
        pass

    elif wf_name == 'Spectrum2D':
        # TODO: 调用你的 2D 能谱实验函数
        pass

    elif wf_name == 'S21vsFlux':
        # TODO: 调用你的 S21 vs Flux 实验函数
        pass

    elif wf_name == 'DRAG':
        # TODO: 调用你的 DRAG 实验函数
        pass

    elif wf_name == 'OptPiPulse':
        # TODO: 调用你的最优 π 脉冲实验函数
        pass

    elif wf_name == 'PowerShift':
        # TODO: 调用你的功率偏移实验函数
        pass

    elif wf_name == 'Delta':
        # TODO: 调用你的 Delta 实验函数
        pass

    elif wf_name == 'RB':
        # TODO: 调用你的随机基准测试实验函数
        pass

    else:
        raise ValueError(f"未知实验类型: {wf_name}")

    # 如果上面的 TODO 都没实现，返回空数据
    return {"data": {}, "meta": {}}
