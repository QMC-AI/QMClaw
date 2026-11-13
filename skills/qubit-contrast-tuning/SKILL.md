---
name: qubit-contrast-tuning
description: >
  量子比特态区分度（readout contrast）迭代调优。用 iqraw -> piamp -> iqraw -> pidf -> iqraw -> piamp
  的序列，借 pi 脉冲频率失谐扫描把 f10 拉回正确位置、把 contrast 从"勉强可用"推到实验室合格线。
  含 contrast 的计算口径、判定门限、实测基线（q2ld3：0.22 -> 0.765）以及踩过的坑。
  Triggers: '态区分', '对比度', 'contrast', 'visibility', 'iqraw 调优', 'pidf', 'f10 偏了',
  'IQ 云分不开', 'readout optimization', 'state discrimination'
---

# 态区分度（contrast）迭代调优

当一个比特"能读出但分不清 |0> 和 |1>"时，不要直接重跑 spectroscopy，也不要盲目调读出功率。
按下面的顺序做，先确认 contrast 的量级，再用 `sq.pidf` 找频率。

## 一、先算清楚 contrast，别只看 discriminator 中心

contrast 定义（与实验室 `qubit_sensing/tools/calibrate.py` 的 `stage_check` 同口径）：

```
contrast = | P(1 | prep X) - P(1 | prep I) |
```

其中投影轴和阈值取 discriminator 的 `center0` / `center1`：

```python
u   = (center1 - center0) / norm(center1 - center0)
thr = 0.5 * (center0 + center1) @ u
P1  = mean(shots @ u > thr)      # shots 取 Is|I/Qs|I 或 Is|X/Qs|X 两列
```

判定门限：

| contrast | 含义 | 动作 |
|---|---|---|
| < 0.2 | 不可用 | 先查 f10 是否偏了、π 是否在共振 |
| 0.2 ~ 0.4 | 可用但弱，**从这里可以开始优化** | 跑迭代序列 |
| > 0.4 | 实验室合格 | 继续微调或进入下一步标定 |
| > 0.7 | 良好 | 停止，转去调 T1/T2/XEB |

辅助指标：两云间距 `sep`、云半径 `sigma`、`SNR = sep / sigma`。经验上 `SNR > 2` 对应 contrast 0.7 左右。

## 二、迭代序列

```
sq.iqraw  ->  sq.piamp  ->  sq.iqraw  ->  sq.pidf  ->  sq.iqraw  ->  sq.piamp
  测基线        调π幅度        复测          调f10       复测         再调π幅度
```

**核心思想**：`sq.pidf` 扫的是 π 脉冲相对当前 `f10` 的失谐 `df`，在 `±df` 窗口里找态区分度最高的频点，
它写回的是比特频率。`sq.piamp` 只动幅度，**不改频率**。所以：

- 如果 contrast 卡在低位，**先怀疑频率**（pidf），不要指望 piamp；
- 频率对了之后，piamp 只在 1~2% 的范围内微调，converge 很快。

每轮约 30 秒（iqraw 3 s / piamp 6 s / pidf 8 s），跑 2~3 轮即可收敛。

## 三、脚本

`docs/deploy_diff_tools/iqraw_contrast_tune.py`

```bash
# 单个比特，扫 ±50 MHz，跑一轮
python docs/deploy_diff_tools/iqraw_contrast_tune.py --qubit q2ld3 --df 0.05

# 连跑三轮观察是否收敛
python docs/deploy_diff_tools/iqraw_contrast_tune.py --qubit q2ld3 --df 0.05 --rounds 3

# 只看会下发什么，不碰硬件
python docs/deploy_diff_tools/iqraw_contrast_tune.py --qubit q2ld3 --dry-run
```

脚本行为：

1. 通过 `POST :3003/execute` 逐步下发（尊重 quantum_service 的 LabRAD 锁），每步 `do_plot=False`；
2. 每跑完一个 `sq.iqraw` 就读回数据集算 contrast / P1 / SNR；
3. 结束后打印 contrast 历史表并指出最佳一轮。

> ⚠️ **`--df` 必须覆盖实际频偏**。默认 `df=0.03` 只有 ±30 MHz；若 f10 已被写偏 35 MHz 以上，
> 这个窗口会扫不到正确频点。怀疑频率写坏时直接给 `--df 0.05` 或更大。

## 四、实测基线（q2ld3，2026-11-03）

起点是 f10 被一次平坦数据上的噪声拟合推高了 35.7 MHz：

| 轮次 | 步骤 | dataset | contrast | P1(I) | P1(X) | SNR | f10 (GHz) | PiGate.amp |
|---|---|---|---|---|---|---|---|---|
| 0 | 调优前 | 536 | 0.216 | 0.062 | 0.278 | 0.65 | 3.656875155 | 1.218983 |
| 1 | iqraw | 537 | 0.261 | 0.143 | 0.404 | 0.74 | 3.656875155 | 1.313216 |
| 1 | piamp | 539 | 0.233 | 0.109 | 0.342 | 0.65 | 3.656875155 | 1.313216 |
| 1 | **pidf** | 541 | **0.633** | 0.247 | 0.880 | 1.93 | **3.621327046** | 1.157553 |
| 2 | iqraw | 544 | 0.715 | 0.165 | 0.880 | 2.25 | 3.621327046 | 1.157553 |
| 2 | piamp | 546 | 0.727 | 0.166 | 0.893 | 2.34 | 3.621327046 | 1.205319 |
| 2 | pidf | 548 | 0.719 | 0.167 | 0.886 | 2.27 | 3.621954083 | 1.205319 |
| 3 | iqraw | 550 | 0.711 | 0.178 | 0.889 | 2.22 | 3.621954083 | 1.161461 |
| 3 | piamp | 552 | 0.734 | 0.161 | 0.894 | 2.36 | 3.621954083 | 1.173172 |
| 3 | **pidf** | 554 | **0.765** | 0.133 | 0.898 | 2.51 | **3.621227332** | 1.173172 |

结论：**一次 pidf 就把 contrast 从 0.22 拉到 0.63**（f10 修正 −35.55 MHz，几乎正好是错误回写的镜像），
后续两轮只做微调（0.72 → 0.765）。piamp 在第一轮把幅度从 1.219 调到 1.313，contrast 反而略降。

## 五、坑（都踩过）

1. **spectroscopy 的自动拟合会把 f10 写坏。** `sq_nodes` 的 `spectroscopy.update_f10` 会把 `distance`
   曲线的峰和 `deviation` 曲线的峰做平均，任一拟合发散就会写出离谱的值（实验室见过 35.18 / 1.19 / 2.62 GHz
   被写到平坦数据上）。所以：**光谱图看起来是平的，就不要信它写回的 f10**，用 pidf 或 `inspect_dataset.py` 复核。
2. **不要用 discriminator 中心间距判断态区分。** `sq.iqraw(update=True)` 每次都拿当前数据重写
   `discriminator.center0/center1`；在一片单峰宽分布上做二分类也会得到"看起来分得很开"的假中心。
   要看 contrast（P1 对比度）。
3. **`sq.iqraw` 的 stdout 里没有 `dataset: N`**（只有 spectroscopy 这类更新型实验才打印）。
   数据集号必须从 `cxn.data_vault.dir()` 解析，注意它返回的是元组 `(子目录列表, 数据集文件列表)`，
   且独立连接默认停在 `['', 'LQHL']`，要先 `cd` 到 session 叶子目录。
4. **服务端执行一律 `do_plot=False`。** 微服务跑在没有显示环境的后台，本机 matplotlib 默认后端是 `qtagg`，
   `do_plot=True` 会在工作线程拉起 Qt GUI，把整个服务卡死（进程仍 `LISTENING`，但 accept 不再响应）。
   画图统一交给 analysis_service 的 `/plot`。
5. **跨轮比较要用同一组 discriminator 中心。** 用"当时的中心"评分不可复现；脚本统一用当前中心。

## 六、调完还不够怎么办

残余 `P1(I)` 偏高（如 0.13）通常是**读出泄露**，而不是激发不足（`P1(X)` 已 0.89）：

1. `sq.set_pi(qobjs, gate='X')` —— 同时细调 `amp` 与 `alpha`（piamp 不碰 alpha）；
2. 读出端扫 `ReadOut.amp` / `ReadOut.length` / `ReadIn.power`；
3. 再跑 `sq.iqraw` 确认 contrast 不降。

## 七、判据真源与治理（2026-11-09 起）

**判据唯一真源**：`Agentic Workflow/qmclaw-server/config/lab_criteria.json`。
本节写的门槛只是速查；脚本、goal、报告一律引用该文件里的 `metrics.contrast.thresholds`。
改了阈值要同时改那个文件和 `.claude/progress.md`，不要在别处另写一套数字。

| 项 | 值 | 备注 |
|---|---|---|
| 不可用 | `< 0.2` | 用户 m13944/m14121 定 |
| 可用但弱 | `0.2 ~ 0.4` | 从这里开始迭代 |
| 实验室合格 | `> 0.4` | 历史实验室判据 |
| 良好 | `> 0.7` | 转 T1/T2/XEB |

同文件里还有：`t1`（固定 c=ground floor 的两参拟合）、`t2_star`（`fitData` 的 T2 在 `data[1][0]`，
不在 `para['T2']`）、`xeb`、`s21_dip`、`dmu_sig`、`registry_facts`、`capabilities`（含 lqms 可用函数清单与厂商坑）。

### 治理闸门
真机执行走 `:3003/execute` 时，若服务端 `quantum_service.actuation.require_task=true`（热切），
**必须带 task_id**（先 `POST :3009/task`，或直接走 `:3008/workflows/run_ir`），否则返回
`status=blocked, reason_code=task_required`。这是"单写者原则"的第一步，见
`docs/ARCHITECTURE_ROADMAP.md` §2.1。

### 三条容易翻车的现场事实
1. **fread 是逐比特独立的**（`q7ld3.fread`），实验的 auto-update 会静默回写**该比特自己**的 fread
   （q1/q3 被 s21 写成 6.7255 → 假死；q7 被 iqraw 写成 6.802）。扫描时用
   `sq.iqraw(auto=False, update=False, do_plot=False)`，每点显式钉 fread，
   事后用数据集 `parameters` 快照反查实际值。
2. **lqms 的 `.pyd` 遮蔽了更新的 `.py`**：`spectroscopy_iq` / `time_rabi` / `iqcenter` 当前**不可用**
   （`D:/anaconda3/envs/lqcs/Lib/site-packages/lqms/measure/tuners/` 下 `.pyd`(2026-04-21) 优先于
   `.py`(2026-09-08)），重启服务无用，需实验室重编译。可用函数清单见 `lab_criteria.json` 的
   `capabilities.lqms_available_functions`。
3. **厂商 `sq.*` 的静默副作用**：spectroscopy 在平坦数据上会把 f10 写坏；`freq=None` 会崩；
   `zpa` 不传会展开成几千点的二维扫描。

## 相关

- 标定全流程：`skills/qubit-calibration/SKILL.md`
- 数据集判读工具：`D:/lqcs/qubit_sensing/tools/inspect_dataset.py`
- 实验室判据实现：`D:/lqcs/qubit_sensing/tools/calibrate.py` 的 `stage_check`
