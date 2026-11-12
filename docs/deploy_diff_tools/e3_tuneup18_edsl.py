"""实验三对照件 B：18 步单比特标定流程的 **eDSL（Python 内嵌）** 写法。

本文件只构造 IR 字典，不执行、不连硬件。它和
`data/workflows/examples/single_qubit_tuneup_18.qwf` 是同一段程序的两种写法，
用于回答"同一个人写同一件事，哪种前端更省"。

用法：
    from e3_tuneup18_edsl import build_ir
    ir = build_ir()

对应关系：每条 `wf.measure(...)` 等价于 `.qwf` 里的一行 `measure as x { ... }`。
"""

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[2] / "Agentic Workflow" / "qmclaw-server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from services.workflow_service.dsl import edsl                   # noqa: E402


def build_ir():
    """18 步官方流程 → IR dict（与 .qwf 版本语义一致）。"""
    wf = edsl.workflow(
        "single_qubit_tuneup",
        name="Single-qubit tune-up (18 steps, eDSL)",
        description="官方 18 步单比特标定：找腔 → 读出 → 激发 → 频率 → 相干 → 脉冲形状 → XEB",
        tags=["single-qubit", "tune-up", "edsl"],
    )
    wf.env(qubit="q2ld3")
    wf.budget(maxWallSeconds=14400, maxWrites=60)

    # 1 腔扫频（update=False，由人挑谷）
    wf.measure("sq.s21", as_="s21", params={"update": False}, timeout=180)
    # 2 读取功率扫描
    wf.measure("sq.s21_power2d", as_="power2d", timeout=300)
    # 3 ZPA 二维
    wf.measure("sq.s21_zpa2d", as_="zpa2d", timeout=300)
    # 4 找 f10（区间按器件改；spectroscopy(freq=None) 本机会 IndexError）
    wf.measure("sq.spectroscopy", as_="spec",
               params={"freq": edsl.arange(4.0, 3.0, -0.002)}, timeout=300)

    # 5 IQ 点云 + 判定
    wf.measure("sq.iqraw", as_="iq", timeout=180)
    wf.read("$iq.dataset", as_="r_iq", kind="IQraw", threshold=0.2)
    wf.assert_("$r_iq.contrast", ">=", 0.3, as_="g_iq", on_fail="continue")

    # 6 pi 振幅
    wf.measure("sq.piamp", as_="piamp", params={"amp": 2}, timeout=300)
    # 7 PiPulse 微调 f10
    wf.measure("sq.pidf", as_="pidf", timeout=300)
    # 8 Ramsey 微调 f10
    wf.measure("sq.ramsey_df", as_="ramsey_df", timeout=300)

    # 9 色散位移微调 fread，然后重做 IQ
    wf.measure("sq.s21_dis", as_="s21_dis", timeout=300)
    wf.measure("sq.iqraw", as_="iq2", timeout=180)
    wf.read("$iq2.dataset", as_="r_iq2", kind="IQraw", threshold=0.2)
    wf.assert_("$r_iq2.contrast", ">=", 0.3, as_="g_iq2", on_fail="continue")

    # 10 X 与 X/2 各一次
    wf.measure("sq.set_pi", as_="pi_x", params={"gate": "X"}, timeout=300)
    wf.measure("sq.set_pi", as_="pi_x2", params={"gate": "X/2"}, timeout=300)

    # 11 XY/Z 同步
    wf.measure("sq.test_timing_xyz", as_="timing", params={"update": "xy"}, timeout=300)
    # 12 脉冲形状（结果不自动更新）
    wf.measure("sq.pulse_shape", as_="shape", params={"step_height": 0.2}, timeout=300)
    # 13 f10-zpa 关系
    wf.measure("sq.spectroscopy_auto", as_="spec_auto",
               params={"zpa_scan": edsl.arange(-0.2, 0.2, 0.002)}, timeout=600)

    # 14 T1 + 拟合 + 判定
    wf.measure("sq.t1", as_="t1", params={"zpa": edsl.arange(-1.0, 0.4, 0.02)}, timeout=300)
    wf.analyze("$t1.dataset", as_="a_t1", command="qter.fitData(do_plot=False)")
    wf.assert_("$a_t1.T1_us", ">=", 20, as_="g_t1", on_fail="continue")

    # 15 T2*
    wf.measure("sq.ramsey_t2", as_="t2star", timeout=300)
    # 16 自旋回波 / CPMG
    wf.measure("sq.spinecho_t2", as_="echo", timeout=300)

    # 17 base slope：sq_nodes 里没有，只能在工作流外做，这里只记一笔
    wf.say("step 17 base slope 需在工作流外执行：set_piLen(1000,1000) → "
           "bassex.PulseShapeFreq() → ps.base_slope() → set_piLen(30,30)")

    # 18 XEB
    wf.measure("sq.xeb", as_="xeb", params={"gate": "reference"}, timeout=900)

    wf.say("tune-up 结束：contrast=${r_iq2.contrast}，T1=${a_t1.T1_us}us"
           "（阈值：T1>50us、visibility>0.75、T2>1.5us）")
    return wf.to_ir()


if __name__ == "__main__":
    import json

    print(json.dumps(build_ir(), ensure_ascii=False, indent=2))
