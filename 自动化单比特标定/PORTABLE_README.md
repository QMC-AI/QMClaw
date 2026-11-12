# 可移植真机测试包

## 文件

至少复制以下文件到另一台电脑同一目录：

```text
portable_zpa_pipeline.py
zpa_calibration_pipeline.py
zpa_features.py
classify_zpa_datavault.py
plan_zpa_bias.py
zpa_v2/model.json
zpa_lab_adapter_template.py
datavault_live.py
requirements-portable.txt
zpa_pipeline_manifest.example.json
```

复制时保持 `zpa_v2/model.json` 的相对路径；或者在manifest/命令中指定绝对模型路径。

## 安装

在另一台电脑使用与真机实验环境相同的Python，不要覆盖实验室已有环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-portable.txt
```

如果真机的`sq/qobj`来自实验室专用包，不要从公共pip安装替代品；在已有实验环境中运行，并保证这个目录可导入你们的实验模块。

## 真实代码修改点

从`zpa_lab_adapter_template.py`复制一个本机适配器，例如`my_lab_adapter.py`，实现：

- `get_context(bit)`：返回正在使用的(qobj,sq)；
- `preflight`：确认bias上下限、频率单位GHz、`bias_z`映射和`t1(zpa=selected_bias)`含义；
- `snapshot/restore`：保存并恢复完整实验状态；
- `wait_for_bias`：实际应用`qobj.regs.bias_z`、等待稳定并读回；
- `latest_datavault_path`：记录本次实验生成的准确DataVault结果路径；
- `maximum_visibility`：调用`datavault_live.iqraw_visibility`或实验室正式Visibility算法；
- `piamp_excited`：调用`datavault_live.pipulse_excitation`或实验室确认的P1规则；
- `final_metrics`：从当前文件提取读取保真度、T1、T2和比特频率；
- `safe_shutdown/restore`：异常和中断时安全关闭并恢复。

默认真机DataVault根目录为：`D:\DataVault\LQHL.dir\test.dir`。该目录包含按日期/设备划分的子目录，实时结果发现会递归搜索子目录；适配器必须确认当前会话确实写入此目录。

真实DataVault文件会以以下字段产生：

```text
Spectroscopy: f0=freq, f6=P1
IQraw: f0=stats, f1=Is|I, f2=Qs|I, f3=Is|X, f4=Qs|X
PiPulse: f0=piAmp, f6=P1
PiPulse df: f0=df, f6=P1
Ramsey df: f0=delay
S21_dis: distance | 01
T1: 根据Dependent label寻找P1
```

## π与π/2命令

流程现在使用你确认的命令：

```python
sq.set_pi(qobj, gate='X')
sq.set_pi(qobj, gate='X/2')
```

不是`set_pi_half`，也不是`set_pi/2`。适配器中的兼容方法保留，但主流程不调用它。

## 先离线计划

把manifest中的`out`改成新目录，并填写每个比特的当前S21ZPA文件：

```json
{"bit":"q23_7", "s21zpa_file":"00731 - q23_7%c S21zpa2d.hdf5"}
```

运行：

```powershell
python portable_zpa_pipeline.py zpa_pipeline_manifest.example.json
```

离线模式只识别区间和生成bias序列，不操作硬件。

## 真机执行

必须在实验室已有真机环境中，先让适配器获得当前`qobj`和`sq`，再显式执行：

```powershell
python portable_zpa_pipeline.py actual_manifest.json --execute --adapter my_lab_adapter
```

执行前检查：

1. 先使用一个已确认的单比特和一个全新输出目录；
2. 确认DataVault根目录与当前实验会话一致；
3. 确认脚本能区分本次新文件与历史文件；
4. 确认`Visibility`与`P1`判据经过历史数据核验；
5. 确认设备bias、RF功率、频率范围和恢复流程；
6. 第一次实验建议把真实适配器的执行参数打印/记录，不要同时跑多比特。

流程为：

```text
S21ZPA正常图像识别
→ 可靠ZPA区间
→ 0.2步长 bias_z
→ spectroscopy
→ IQraw DataVault
→ PiPulse DataVault
→ Visibility > 0.05 且P1激发
→ 停止遍历
→ pidf(df=0.08)
→ pidf()
→ ramsey_df()
→ s21_dis()
→ iqraw()
→ set_pi(gate='X')
→ set_pi(gate='X/2')
→ t1(zpa=selected_bias)
→ iqraw()
→ 输出结果
```

实验异常、超时或中断会尝试关闭驱动并恢复快照；清理失败会停止后续批次。不要强制杀Python进程替代适配器的安全停机。

## 测试

在没有真机的电脑上：

```powershell
python -m py_compile *.py
python test_zpa_pipeline.py
python test_datavault_live.py
```

测试全部使用模拟数据，不会连接真机。
