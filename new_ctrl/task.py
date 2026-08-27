# -*- coding: utf-8 -*-
"""
new_ctrl.task — 通用接口
对应原版 qubitctrl.task.quark，提供 call_interface / get_data / query_param / update_param

call_interface 是核心：它接收 workflow 模板 + 实验参数，
驱动你的新测控系统执行实验，返回实验记录 ID (tid)。

你需要根据你的实际测控系统实现 _execute_workflow()。
"""

import json
import logging
import numpy as np
from datetime import datetime

logger = logging.getLogger(__name__)

# ── 参数存储（内存中的简易存储，实际应持久化）──────────────────
_PARAM_STORE: dict = {}

# ── 数据存储（tid → result），实际应存数据库/文件 ─────────────
_DATA_STORE: dict = {}


def call_interface(workflow, **kwargs) -> str:
    """
    调用测控接口执行实验。

    Args:
        workflow: 实验模板对象（如 S21_template）
        **kwargs: 实验参数 (qubits, frequency_start, ...)

    Returns:
        tid: 实验记录 ID（字符串）
    """
    logger.debug("call_interface: workflow=%s kwargs=%s",
                 getattr(workflow, '__name__', workflow), kwargs)

    # ① 生成唯一 tid
    tid = f"tid_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    # ② 调用你的测控系统执行实验
    #    workflow 对象知道实验类型，kwargs 是实验参数
    #    你需要把它翻译成你测控系统的调用
    try:
        result = _execute_workflow(workflow, tid, **kwargs)
    except Exception as e:
        logger.error("执行实验失败: %s", e)
        result = {"error": str(e)}

    # ③ 存储结果，供 get_data 取回
    _DATA_STORE[tid] = result

    return tid


def _execute_workflow(workflow, tid, **kwargs):
    """
    驱动你的新测控系统执行实验。

    这里是你要对接实际硬件的地方。
    workflow 对象包含实验类型/线路，kwargs 是实验参数。

    返回实验数据（dict）。
    """
    # TODO: 对接你的新测控系统
    #   if workflow == S21_template:
    #       return your_hardware.run_s21(**kwargs)
    #   elif workflow == Rabi_template:
    #       return your_hardware.run_rabi(**kwargs)
    #   ...

    logger.debug("执行实验: workflow=%s, tid=%s, kwargs=%s",
                 getattr(workflow, '__name__', workflow), tid, kwargs)

    # ── Mock 实现 ────────────────────────────────────────────
    # 返回模拟实验数据，方便在没有实际硬件时 demo 和测试
    wf_name = getattr(workflow, 'name', str(workflow))
    qubits = kwargs.get('qubits', ['Q0'])
    n_qubits = len(qubits) if isinstance(qubits, (list, tuple)) else 1

    if wf_name == 'S21':
        freq = kwargs.get('frequency_start', 0) + \
               (kwargs.get('frequency_end', 0) - kwargs.get('frequency_start', 0)) * \
               np.linspace(0, 1, kwargs.get('frequency_sample_num', 101))
        s21 = np.exp(-0.5 * ((freq - freq.mean()) / (freq.std() or 1))**2) \
            * (1 + 0.1j)
        data = {"s21": s21.tolist()}
        meta = {"freq": {"def": freq.tolist()}, "qubits": qubits}

    elif wf_name == 'Rabi':
        drive_amp = np.asarray(kwargs.get('drive_amp', [0.01]))
        population = 0.5 - 0.5 * np.cos(2 * np.pi * drive_amp)
        data = {"population": population.tolist()}
        meta = {"drive_amp": {"def": drive_amp.tolist()}}

    elif wf_name == 'T1':
        delay = np.asarray(kwargs.get('delay', [0]))
        population = np.exp(-delay / 20e-6)
        data = {"population": population.tolist()}
        meta = {"delay": {"def": delay.tolist()}}

    elif wf_name == 'Ramsey':
        delay = np.asarray(kwargs.get('delay', [0]))
        delta = kwargs.get('delta', 20e6)
        population = 0.5 + 0.5 * np.cos(2 * np.pi * delta * delay) \
            * np.exp(-delay / 10e-6)
        data = {"population": population.tolist()}
        meta = {"delay": {"def": delay.tolist()}, "delta": delta}

    elif wf_name == 'SingleShot':
        rng = np.random.default_rng(42)
        data = {"iq": [rng.complex(size=1000).tolist() for _ in range(n_qubits)]}
        meta = {"shots": 1000, "qubits": qubits}

    else:
        # 通用 mock：返回空数据
        data = {}
        meta = {}

    return {
        "tid": tid,
        "workflow": wf_name,
        "kwargs": kwargs,
        "data": data,
        "meta": meta,
    }


def get_data(rid) -> dict:
    """
    根据实验记录 ID (rid) 获取测量数据。

    对应原版 qubitctrl.task.quark.get_data
    """
    rid_str = str(rid)
    if rid_str in _DATA_STORE:
        return _DATA_STORE[rid_str]

    # 如果 tid 不在内存里，你可能需要从数据库/文件加载
    logger.warning("get_data: tid=%s 不存在于存储", rid_str)
    return {}


def query_param(key: str):
    """
    查询指定 key 的参数值。
    """
    value = _PARAM_STORE.get(key)
    logger.debug("query_param: key=%s value=%s", key, value)
    return value


def update_param(key: str, value):
    """
    更新指定 key 的参数值。
    """
    _PARAM_STORE[key] = value
    logger.debug("update_param: key=%s value=%s", key, value)
    return True
