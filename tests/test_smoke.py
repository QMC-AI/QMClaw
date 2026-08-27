# -*- coding: utf-8 -*-
"""
冒烟测试 — 验证 import 链通畅，模块可实例化
运行: python tests/test_smoke.py
"""

import sys
import os

# 把 QMClaw 项目根目录加入 sys.path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_import_new_ctrl():
    """new_ctrl 包导入"""
    import new_ctrl
    assert new_ctrl.__file__.endswith("__init__.py")


def test_import_new_ctrl_tools():
    """new_ctrl.tools 13 个实验导入"""
    from new_ctrl.tools import (
        s21, rabi, ramsey, t1, spectrum, spectrum_2d,
        s21vsflux, singleshot, drag, opt_pipulse,
        powershift, delta, rb,
    )
    assert all(callable(f) for f in [
        s21, rabi, ramsey, t1, spectrum, spectrum_2d,
        s21vsflux, singleshot, drag, opt_pipulse,
        powershift, delta, rb,
    ])


def test_import_new_ctrl_task():
    """new_ctrl.task 4 个接口导入"""
    from new_ctrl.task import call_interface, get_data, query_param, update_param
    assert all(callable(f) for f in [
        call_interface, get_data, query_param, update_param,
    ])


def test_import_new_templates():
    """new_templates 13 个模板导入"""
    from new_templates import (
        S21_template, rabi_template, ramsey_template, t1_template,
        spectrum_template, spectrum_2d_template, s21vsflux_template,
        singleshot_template, drag_template, opt_pipulse_template,
        powershift_template, delta_template, rb_template,
    )
    assert all(hasattr(t, 'name') for t in [
        S21_template, rabi_template, ramsey_template, t1_template,
        spectrum_template, spectrum_2d_template, s21vsflux_template,
        singleshot_template, drag_template, opt_pipulse_template,
        powershift_template, delta_template, rb_template,
    ])


def test_call_interface_s21():
    """call_interface 能跑 S21 并返回 tid"""
    from new_ctrl.task import call_interface, get_data
    from new_templates import S21_template
    tid = call_interface(
        workflow=S21_template,
        qubits=["Q0"],
        frequency_start=-40e6,
        frequency_end=40e6,
        frequency_sample_num=101,
    )
    assert isinstance(tid, str) and tid.startswith("tid_")
    data = get_data(tid)
    assert "s21" in data["data"]


def test_call_interface_rabi():
    """call_interface 能跑 Rabi 并返回 tid"""
    from new_ctrl.task import call_interface, get_data
    from new_templates import rabi_template
    tid = call_interface(
        workflow=rabi_template,
        qubits=["Q0"],
        drive_amp=[0.01, 0.05, 0.1],
    )
    assert isinstance(tid, str) and tid.startswith("tid_")
    data = get_data(tid)
    assert "population" in data["data"]


def test_query_update_param():
    """query_param / update_param 读写"""
    from new_ctrl.task import query_param, update_param
    update_param("test_key", 42)
    assert query_param("test_key") == 42


if __name__ == "__main__":
    # 逐个运行测试
    tests = [
        test_import_new_ctrl,
        test_import_new_ctrl_tools,
        test_import_new_ctrl_task,
        test_import_new_templates,
        test_call_interface_s21,
        test_call_interface_rabi,
        test_query_update_param,
    ]
    passed = 0
    failed = 0
    for test in tests:
        name = test.__name__
        print(f"  RUN  {name}")
        try:
            test()
            print(f"  ✅ PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  ❌ FAIL  {name}: {e}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"  结果: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{'='*50}")
    if failed > 0:
        sys.exit(1)
