# -*- coding: utf-8 -*-
"""QMClaw 上手验证：install.md 4.2 示例（Mock 模式，无需硬件）"""
from new_ctrl.task import call_interface, get_data
from new_templates import S21_template, rabi_template, t1_template

# ① S21 实验
print("=== ① S21 ===")
tid = call_interface(
    workflow=S21_template,
    qubits=["Q0"],
    frequency_start=-40e6,
    frequency_end=40e6,
    frequency_sample_num=101,
)
print("tid =", tid)
data = get_data(tid)
s21 = data["data"]["s21"]
print("S21 数据点数:", len(s21), "| 前3个值:", s21[:3])

# ② Rabi 实验
print("=== ② Rabi ===")
tid_rabi = call_interface(
    workflow=rabi_template,
    qubits=["Q0"],
    drive_amp=[0.01, 0.05, 0.1],
)
print("tid =", tid_rabi)
data_rabi = get_data(tid_rabi)
pop = data_rabi["data"]["population"]
print("Rabi 数据点数:", len(pop), "| 前3个值:", pop[:3])

# ③ T1 实验
print("=== ③ T1 ===")
tid_t1 = call_interface(
    workflow=t1_template,
    qubits=["Q0"],
    delay=[0, 1e-6, 5e-6, 10e-6, 20e-6],
)
print("tid =", tid_t1)
data_t1 = get_data(tid_t1)
pop1 = data_t1["data"]["population"]
print("T1 数据点数:", len(pop1), "| 前3个值:", pop1[:3])

# ④ 参数查询/更新
print("=== ④ 参数操作 ===")
from new_ctrl.task import update_param, query_param
update_param("Q0.frequency", 6.5e9)
value = query_param("Q0.frequency")
print("Q0.frequency =", value)

print("\n全部示例运行成功 ✅")
