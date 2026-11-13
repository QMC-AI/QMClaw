# qubit-contrast-tuning

量子比特态区分度（readout contrast）迭代调优 Skill。

## 内容

- `SKILL.md`：contrast 口径、迭代序列、实测基线、踩坑记录
- `README.md`：本文件

## 适用场景

- IQ 云分不开、`P(1|prep X)` 与 `P(1|prep I)` 差距很小
- 怀疑 `f10` 被写偏（例如 spectroscopy 在平坦数据上给出了离谱的更新）
- 想从"勉强能读"推到实验室合格的 0.4 以上

## 方法一句话

`iqraw -> piamp -> iqraw -> pidf -> iqraw -> piamp`，用 `sq.pidf` 找态区分度最高的频点，`sq.piamp` 只负责幅度。

## 脚本

```bash
python docs/deploy_diff_tools/iqraw_contrast_tune.py --qubit q2ld3 --df 0.05 --rounds 3
```

## 实测

q2ld3（2026-11-03）：contrast 0.216 → 0.765，f10 修正 −35.55 MHz，SNR 0.65 → 2.51。

## 相关资源

- 标定全流程：`skills/qubit-calibration/SKILL.md`
- 实验室判据：`D:/lqcs/qubit_sensing/tools/calibrate.py`（`stage_check`）
- 数据集判读：`D:/lqcs/qubit_sensing/tools/inspect_dataset.py`
