# deploy 依赖包 与 QMClaw 项目 差异报告

> 比对对象：`D:\semester4\deploy\`（父目录下的部署依赖包）
> 比对目标：`D:\semester4\QMClaw\`（本项目当前工作副本）
> 生成时间：基于当前工作副本，git HEAD = `f2d83b5`（工作区干净）
> 方法：解包 `qmclaw.zip` / `vendor.zip` 的中央目录清单与关键元数据，逐条核对需求声明与实际安装集合；`deploy` 目录只读，未做任何修改。

---

## 0. 结论摘要

`deploy/` 是"一台已经跑通过的机器"的**环境快照 + 依赖离线包**，不是本项目的源代码包。它与当前项目之间存在的差异分四类：

| 级别 | 差异 | 影响 |
|------|------|------|
| 🔴 阻断 | `vendor/` 三个子模块目录**完全为空**（0 文件），而 `vendor.zip` 内含 13,584 个文件 | `import hermes_agent` / `import qca` / `import qubitclient` 全部失败 |
| 🔴 阻断 | 环境中两个 editable 安装硬编码指向**旧项目路径** `D:\Documents\QMClaw\vendor\...` | 即使解包了 `vendor.zip` 到新位置，`hermes` / `qca` 仍然导入失败 |
| 🔴 阻断 | `offline_data/` 缺失（deploy 有 5,011 个文件 / 413 MiB） | 离线模式（`default_mode: "offline"`）无数据可用 |
| 🟠 高 | `lqms` 版本不一致：deploy 轮子是 **0.3.3**，环境里装的是 **0.3.1** | 轮子与已装环境脱节，需确认以哪个为准 |
| 🟠 高 | `qubitclient` 版本不一致：`vendor.zip` 是 **0.6.22**，环境里是 **0.6.21** | 同上 |
| 🟠 高 | `cryptography==36.0.0` 与 `pyopenssl>=24.0.0` 互相冲突 | `lqcs_deploy/requirements.txt` 实际无法按字面安装 |
| 🟡 中 | `envelopes-qc` 声明 `>=0.1.0`，deploy 提供的是 **0.0.6** | 需求不满足；且该包在代码中从未被引用 |
| 🟡 中 | `quanputing` / `quanputing_server` 两个轮子**未安装**且无代码引用；若安装会引入 **15 处版本冲突 + 7 处缺失依赖** | 属于随包附带但当前用不上的产物 |
| 🟡 中 | 环境自身有 2 处强制依赖冲突（`langchain-openai`↔`openai`、`qca`↔`rich`） | 运行时可能出现行为偏差 |
| 🟡 中 | `conda-meta` 记录的 `packaging` / `prompt-toolkit` 版本与 `site-packages` 实际版本不符 | conda 认为环境已损坏；`conda install` 可能误降级 |
| 🟢 低 | 项目代码引用了 5 个 env 未安装的第三方模块（`docx` / `onnxruntime` / `torchvision` / `redis` / `swiftmcp`） | 前两个影响 docx 报告导出、图像分类服务；后三个对应可选功能 |
| 🟢 低 | Node 版本：deploy 带 `node-v24.20.0`，本机是 `v24.14.0` | 当前均满足 vendor 的 engines 约束，无实际影响 |

---

## 1. deploy 产物清单与目标位置映射

| deploy 产物 | 大小 | 内容 | 应落到项目的位置 | 项目当前状态 |
|---|---|---|---|---|
| `qmclaw.zip` | 754.1 MiB / 91,340 条目 | conda 环境 `qmclaw`（Python 3.11.16，site-packages 解压后 ≈2,027 MB） | conda env `qmclaw` | ❌ 本机无该环境 |
| `vendor.zip` | 99.9 MiB / 14,996 条目 | 三个 git 子模块的工作树 | `vendor/hermes-agent`、`vendor/QubitClient`、`vendor/Quantum-Calibration-Agent-Blueprint` | ❌ **三个目录均为空** |
| `lqcs-0.1.3-cp311-cp311-win_amd64.whl` | 33.5 MiB | `lqcs` 0.1.3 | env site-packages | ✅ env 内为 0.1.3，一致 |
| `lqms-0.3.3-cp311-cp311-win_amd64.whl` | 9.3 MiB | `lqms` 0.3.3 | env site-packages | ⚠️ env 内为 **0.3.1** |
| `pylabrad-0.98.7-py2.py3-none-any.whl` | 160 KiB | `pylabrad` 0.98.7（提供顶层 `labrad` 包） | env site-packages | ✅ 0.98.7，一致 |
| `envelopes_qc-0.0.6-cp311-cp311-win_amd64.whl` | 190 KiB | `envelopes-qc` 0.0.6（顶层模块 `envelopes`） | env site-packages | ✅ 装了 0.0.6，但与需求声明冲突 |
| `quanputing-0.1.0-py3-none-any.whl` | 49 KiB | `quanputing` 0.1.0 | env site-packages | ❌ **未安装** |
| `quanputing_server-0.1.0-py3-none-any.whl` | 11 KiB | `quanputing_server` 0.1.0 | env site-packages | ❌ **未安装** |
| `node-v24.20.0-x64.msi` | 31.6 MiB | Node.js 24.20.0 安装包 | 系统运行时 | ⚠️ 本机为 v24.14.0 |
| `offline_data/` | 413.5 MiB / 5,011 文件 | 历史测控 HDF5 数据 | `Agentic Workflow/qmclaw-server/data/offline_data/` | ❌ **`data/` 目录整体不存在** |
| `qmclaw_requirement/` | **空目录（0 项）** | 无内容 | 未知 | ⚠️ 空壳，疑为预留的依赖清单目录，待确认 |

> 说明：`deploy/qmclaw_requirement/` 是一个空目录（创建时间 2026-09-21 07:45:10，与 `offline_data/` 同批）。本次比对时为空，无法判断其预期内容；若它本应放一份 requirements 清单，请补充后重新比对。

环境构造痕迹（来自 `qmclaw/conda-meta/history`）：

```
==> 2026-09-16 14:46:55 <==
# cmd: D:\ProgramData\Anaconda3\Scripts\conda-script.py create --name qmclaw python=3.11 -y
# conda version: 22.9.0
  源：mirrors.tuna.tsinghua.edu.cn/.../conda-forge（21 个 conda 包）
==> 2026-09-16 21:59:27 <==
# cmd: ... conda-script.py install ipython
```

即：**conda 只负责 Python 3.11 + ipython**，其余 249 个发行版（`dist-info`/`egg-info` 目录数，其中 248 个带 `METADATA`，`setuptools` 走 `egg-info/PKG-INFO`）全部由 pip 装入。

---

## 2. 🔴 阻断级差异

### 2.1 `vendor/` 三个子模块目录为空

`.gitmodules` 声明了三个子模块，git HEAD 中也记录了各自的 gitlink 提交：

```toml
[submodule "vendor/Quantum-Calibration-Agent-Blueprint"]
	path = vendor/Quantum-Calibration-Agent-Blueprint
	url = https://github.com/Secsix/Quantum-Calibration-Agent-Blueprint
[submodule "vendor/hermes-agent"]
	path = vendor/hermes-agent
	url = https://github.com/Secsix/hermes-agent
[submodule "vendor/QubitClient"]
	path = vendor/QubitClient
	url = https://github.com/Secsix/QubitClient
```

```
$ git ls-tree HEAD vendor/
160000 commit d8d37bc4de28d2347d8e21405903b29a2891f793  vendor/Quantum-Calibration-Agent-Blueprint
160000 commit 3da70fe01d8a873418ed84915d4a05c35f07082a  vendor/QubitClient
160000 commit c4a5deeffa959f8ebc891e02c5bcf767aff1dd2a  vendor/hermes-agent
```

本机三个目录 `Get-ChildItem -Recurse -Force` 结果均为 **0 个文件 / 0 个目录**（子模块未初始化）。

`vendor.zip` 正是这三个工作树的内容（条目里带 `gitdir: ../../.git/modules/vendor/<name>` 指针文件，说明是从一次 `git submodule update` 后的检出打包的）：

| 子模块 | 条目数 | 解压后大小 | 项目声明的版本 | `qmclaw.zip` 环境内安装版本 |
|---|---|---|---|---|
| `hermes-agent` | 13,665 | 172.0 MiB | 0.21.0 | `hermes_agent` 0.21.0（editable） |
| `QubitClient` | 919 | 20.1 MiB | 动态版本 → **0.6.22** | `qubitclient` **0.6.21** |
| `Quantum-Calibration-Agent-Blueprint` | 412 | 4.5 MiB | 0.1.0 | `qca` 0.1.0（editable） |

**注意**：`vendor.zip` **不包含** `.git/modules/`（即子模块的 git 对象库），只包含工作树 + `.git` 指针文件。因此解压后 `git submodule status` 无法报告提交号，需要重新 `git submodule update --init` 或手工比对。

**影响**（源码里已确认的依赖点）：

- `services/qubitclient_service/server.py:144` —— `from qubitclient.llm import QubitLLM`，报错提示为 `Install with: pip install -e vendor/QubitClient`
- `services/qca_service/*` —— QCA 服务本体
- `services/hermes_service/server.py:110` —— `Successfully imported AIAgent from hermes-agent`
- `scripts/hermes_runner.py:34-59` —— 向上查找"含 `vendor/hermes-agent` 的目录"作为 `QMCLAW_ROOT`，找不到会打印
  `[Hermes] WARNING: Could not find QMCLAW_ROOT, using file-based: ...`

同时三个目录均**不含 `node_modules`**（`hermes-agent` 的 JS 工作区需另行 `npm install`）。

### 2.2 conda 环境中的 editable 安装指向旧项目路径

这是最隐蔽、也最容易在"换机器部署"时踩到的问题。环境里有两个 PEP 660 editable 安装：

`qmclaw/Lib/site-packages/__editable__.hermes_agent-0.21.0.pth`
→ `__editable___hermes_agent_0_21_0_finder.py`，其 `MAPPING` 字典共 **48 个条目**，全部指向：

```
D:\Documents\QMClaw\vendor\hermes-agent\<模块名>
```

`qmclaw/Lib/site-packages/__editable__.qca-0.1.0.pth`
→ `__editable___qca_0_1_0_finder.py`，`MAPPING` 指向：

```
D:\Documents\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint\{cli,core,prompt,scripts,server,tools,vlm}
```

而项目现在的实际位置是 `D:\semester4\QMClaw\`。对应 `dist-info/direct_url.json` 也如实记录了旧路径：

```json
{"dir_info": {"editable": true}, "url": "file:///D:/Documents/QMClaw/vendor/hermes-agent"}
{"dir_info": {"editable": true}, "url": "file:///D:/Documents/QMClaw/vendor/Quantum-Calibration-Agent-Blueprint"}
```

**后果**：`D:\Documents\QMClaw\` 不存在 ⇒
- `import hermes_agent`、`import hermes_cli`、`import run_agent`、`import toolsets` 等 48 个顶层模块全部 `ModuleNotFoundError`
- `qca`、`qca.exe` 同样失效
- 即使把 `vendor.zip` 解压到 `D:\semester4\QMClaw\vendor\`，.pth 里的路径仍然是旧的，**必须重装**

修复（在新位置重建两处 editable 安装）：

```powershell
& $PY -m pip install -e "D:\semester4\QMClaw\vendor\hermes-agent"
& $PY -m pip install -e "D:\semester4\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint"
& $PY -m pip install -e "D:\semester4\QMClaw\vendor\QubitClient"
```

### 2.3 `offline_data` 缺失

配置声明的路径（`Agentic Workflow/qmclaw-server/config/fallback_config.json`）：

```json
{
  "quantum_service": { "offline_mode": { "enabled": true, "data_path": "data/offline_data",
                                         "auto_switch_on_disconnect": true, "default_mode": "offline" } },
  "analysis_service": { "offline_mode": { "enabled": true, "data_path": "data/offline_data",
                                          "auto_switch_on_disconnect": true, "default_mode": "offline" } }
}
```

`data_path` 在代码中按 **相对 qmclaw-server 根目录** 解析：

```python
# services/quantum_service/server.py:227 与 services/analysis_service/server.py:283
offline_path = Path(__file__).parent.parent.parent / self._offline_data_path
```

即期望位置为 `Agentic Workflow/qmclaw-server/data/offline_data/`。
**项目里 `Agentic Workflow/qmclaw-server/data/` 整个目录不存在**（已被 `.gitignore` 忽略：`Agentic Workflow/qmclaw-server/data/`、`offline_data/`），必须从 `deploy/offline_data` 拷贝。

`deploy/offline_data` 内容：

| 日期目录 | 文件数 | 大小 |
|---|---|---|
| `20251005.dir` | 3,787 | 337.7 MiB |
| `20251103.dir` | 485 | 34.2 MiB |
| `20260324.dir` | 739 | 41.6 MiB |
| **合计** | **5,011** | **413.5 MiB** |

---

## 3. 🟠 高优先级版本差异

### 3.1 `lqms`：轮子 0.3.3 vs 环境 0.3.1

```
deploy\lqms-0.3.3-cp311-cp311-win_amd64.whl   → METADATA: Name: lqms  Version: 0.3.3
qmclaw.zip 内                                  → lqms-0.3.1.dist-info
```

环境里 0.3.1 的来源记录（`direct_url.json`）显示它来自一个微信临时文件目录：

```json
{"url": "file:///D:/Documents/xwechat_files/wxid_8kt448vkmo6f22_4011/msg/file/2025-11/lqms-0.3.1-cp311-cp311-win_amd64.whl"}
```

也就是说：**deploy 里的 0.3.3 轮子是后来补的，但环境快照（qmclaw.zip）还是在 0.3.1 状态下打的**。两者不自洽，需要确认以哪个为准再重装。

`lqms` 是本项目最核心的运行时依赖（`backends/lqcs/`、`services/quantum_service/labrad_client.py`、`scripts/job_runner.py` 中共 30+ 处 `from lqms...` 导入），升级前应确认新版本的 API 兼容性。

> 另注：项目 `Agentic Workflow/qmclaw-server/python/requirements.txt:16` 写的是 `lqms @ git+https://github.com/...@main#egg=lqms` —— 这是一个**占位 URL**（省略号），不可安装，实际只能靠本地轮子。

### 3.2 `qubitclient`：vendor.zip 0.6.22 vs 环境 0.6.21

```python
# vendor.zip → QubitClient/qubitclient/__init__.py
__version__ = "0.6.22"
```

环境内：`qubitclient-0.6.21.dist-info`，且**没有 `direct_url.json`**（安装来源未被记录，无法从环境快照反推）。

`QubitClient` 采用 `dynamic = ["version"]`（`version = {attr = "qubitclient.__version__"}`），因此 git 提交 `3da70fe` 对应的是 0.6.22。

`QubitClient` 与本项目是 **editable 耦合**：
`services/qubitclient_service/server.py:106` 会去 `config_base / "vendor" / "QubitClient" / "qubitclient.json"` 找配置，`services/qubitclient_service/requirements.txt` 里写的是 `-e ../../../vendor/QubitClient`。

### 3.3 `cryptography==36.0.0` 与项目自身需求冲突

| 来源 | 声明 |
|---|---|
| `lqcs_deploy/requirements.txt:12` | `cryptography==36.0.0` |
| `Agentic Workflow/qmclaw-server/python/requirements.txt:12` | `pyopenssl>=24.0.0  # Required: older versions incompatible with cryptography>=50.0.0` |
| 环境实际安装 | `cryptography 50.0.0` + `pyopenssl 26.4.0` |

即：`lqcs_deploy/requirements.txt` 的 `cryptography==36.0.0` 与 `pyopenssl>=24.0.0` **不可能同时满足**；deploy 环境事实上选择了 `cryptography 50.0.0`，忽略了这个 pin。该 pin 应视为过期项。

（附带说明：`lqcs_deploy/requirements.txt` 全 34 条中，除这条以外**其余全部满足**，详见 §5.2。）

### 3.4 `envelopes-qc` 版本不满足声明

| 项 | 值 |
|---|---|
| 声明（`qmclaw-server/python/requirements.txt:22`） | `envelopes-qc>=0.1.0` |
| deploy 提供 | `envelopes_qc-0.0.6-cp311-cp311-win_amd64.whl` |
| 环境实际安装 | `envelopes_qc 0.0.6`（顶层模块 `envelopes`） |
| 代码引用 | **全仓库 grep 无任何 `import envelopes`** |

三处不一致：声明版本高于实际、实际两者一致、且该包在代码里根本没被用到。属于需求文件与部署包之间的历史遗留。

---

## 4. 🟡 中优先级差异

### 4.1 `quanputing` / `quanputing_server`：随包附带但未安装、未使用

两个轮子都**不在** `qmclaw.zip` 的环境里（无 dist-info），且全仓库 grep `quanputing` **零命中**。

如果真要安装，它们会与现有环境产生大量冲突：

**`quanputing-0.1.0`**（顶层模块 `d3view, main, measurement, mixin, parameter_widget, plot, plot_functions, qubit_grid, selfdefinedclass`）

| 声明 | 环境实际 |
|---|---|
| `deepdiff==8.1.1` | ❌ 缺失 |
| `h5py==3.13.0` | ⚠️ 3.16.0 |
| `matplotlib==3.10.1` | ⚠️ 3.11.2 |
| `nest_asyncio==1.6.0` | ❌ 缺失 |
| `numpy==2.2.5` | ⚠️ 2.4.6 |
| `PyQt6==6.9.0` | ⚠️ 6.11.0 |
| `PyQt6_sip==13.9.1` | ⚠️ 13.12.0 |
| `pyqtgraph==0.13.7` | ❌ 缺失 |
| `PySide6==6.9.0` / `PySide6_Addons==6.8.1.1` / `PySide6_Essentials==6.8.1.1` | ❌ 缺失（会引入第二个 Qt 绑定，与 PyQt6 并存） |
| `python-dotenv==1.1.0` | ⚠️ 1.2.2 |
| `qtconsole==5.6.1` | ❌ 缺失 |
| `scikit_learn==1.6.1` | ⚠️ 1.9.1 |
| `websockets==14.1` | ⚠️ 15.0.1 |

**`quanputing_server-0.1.0`**（顶层模块 `qfuncs, server_runsequence`）

| 声明 | 环境实际 |
|---|---|
| `h5py==3.13.0` | ⚠️ 3.16.0 |
| `matplotlib==3.10.1` | ⚠️ 3.11.2 |
| `numpy==2.2.5` | ⚠️ 2.4.6 |
| `PyQt6==6.9.0` | ⚠️ 6.11.0 |
| `PyQt6_sip==13.9.1` | ⚠️ 13.12.0 |
| `ray==2.40.0` | ⚠️ 2.41.0（`lqcs_deploy` 要求 `ray<=2.41.0`，恰好卡在上界） |
| `websockets==14.1` | ⚠️ 15.0.1 |

**建议**：除非明确要用，否则不要把这两个轮子装进 `qmclaw` 环境。

### 4.2 环境自身的强制依赖冲突（2 处）

对 `qmclaw.zip` 内 249 个发行版的全部 `Requires-Dist`（排除 `extra` 门控的可选项，共跳过 1,735 条）逐条按 PEP 440 求值，得到 **2 处真实冲突**：

| 依赖方 | 要求 | 环境实际 |
|---|---|---|
| `langchain-openai 1.2.0` | `openai<3.0.0,>=2.26.0` | `openai 2.24.0` |
| `qca 0.1.0`（vendor 的 `pyproject.toml`） | `rich==14.3.4` | `rich 14.3.3` |

其余无冲突（`MISSING` 项为 0）。

三个 vendor 项目的强制依赖对环境的满足情况：

| 项目 | 强制依赖数 | 问题 |
|---|---|---|
| `hermes-agent 0.21.0` | 34 | ✅ 0 处 |
| `qubitclient`（0.6.22） | 7 | ✅ 0 处 |
| `qca 0.1.0` | 14 | ⚠️ 1 处（`rich==14.3.4` vs 14.3.3） |

### 4.3 `conda-meta` 与 `site-packages` 版本不符

pip 在 conda 环境里覆盖了 conda 装的两个包，但没更新 `conda-meta`：

| 包 | `conda-meta` 记录 | `site-packages` 实际 |
|---|---|---|
| `packaging` | 26.3 | **26.0** |
| `prompt-toolkit` | 3.0.53 | **3.0.52** |

其余 18 个 conda 包的版本与 pip 侧一致，另 1 个（`python_abi 3.11`）为 conda 独有、无 pip counterpart。这两个包被 pip 降级后，conda 会把环境判定为不一致，后续 `conda install` 有可能把它们"修"回去从而破坏依赖解析。

### 4.4 conda 环境内的其他异常

- `Scripts/deepagents-cli.exe` 用的是 site-packages 里的 `deepagents_cli 0.0.40`；但环境根目录下还额外存在一个 `qmclaw/deepagents_cli/default_agent_prompt.md`（仅此一个文件，非包）。若工作目录恰为环境根目录，可能造成资源查找歧义。
- `qmclaw` 本身（本仓库的 `new_ctrl` / `new_templates` 包）**没有安装进环境**——`site-packages` 下没有 `qmclaw` / `new_ctrl` / `new_templates` 任何痕迹。根 `pyproject.toml` 定义的 `qmclaw 0.1.0` 需要在环境中补装或通过 `PYTHONPATH` 暴露。

---

## 5. 需求文件逐条核对

### 5.1 根 `requirements.txt`

| 声明 | 环境实际 | 结果 |
|---|---|---|
| `numpy>=1.26` | 2.4.6 | ✅ |
| `scipy>=1.10` | 1.17.1 | ✅ |
| `matplotlib>=3.9` | 3.11.2 | ✅ |
| `plotly>=6.4.0` | 6.7.0 | ✅ |
| `opencv-python-headless>=4.12` | 5.0.0.93 | ✅ |

### 5.2 `lqcs_deploy/requirements.txt`（34 条）

| 声明 | 环境实际 | 结果 |
|---|---|---|
| `twisted` | 26.4.0 | ✅ |
| `PyQt6` | 6.11.0 | ✅ |
| `pylabrad>=0.98.7` | 0.98.7 | ✅ |
| `pywin32` | 311 | ✅ |
| `stim` | 1.16.0 | ✅ |
| `scipy` | 1.17.1 | ✅ |
| `func_timeout` | 4.3.5 | ✅ |
| `pyvisa` | 1.16.2 | ✅ |
| `apscheduler` | 3.11.3 | ✅ |
| `h5py` | 3.16.0 | ✅ |
| `adjustText` | 1.4.0 | ✅ |
| **`cryptography==36.0.0`** | **50.0.0** | ❌ 见 §3.3 |
| `numpy-quaternion` | 2024.0.13 | ✅ |
| `physics-tenpy` | 1.1.1 | ✅ |
| `cssselect` | 1.5.0 | ✅ |
| `qutip` | 5.3.1 | ✅ |
| `protobuf` | 6.33.6 | ✅ |
| `cvxpy` | 1.9.2 | ✅ |
| `networkx==3.2.1` | 3.2.1 | ✅ |
| `numpy` | 2.4.6 | ✅ |
| `matplotlib` | 3.11.2 | ✅ |
| `pandas` | 3.0.5 | ✅ |
| `seaborn` | 0.13.2 | ✅ |
| `tqdm` | 4.70.1 | ✅ |
| `colorama` | 0.4.6 | ✅ |
| `dill` | 0.4.1 | ✅ |
| `ray<=2.41.0` | 2.41.0 | ✅（正好在上界） |
| `torch` | 2.14.0 | ✅ |
| `configparser` | 7.2.0 | ✅ |
| `future` | 1.0.0 | ✅ |
| `ipython` | 9.17.1 | ✅ |
| `setuptools` | 84.0.0（conda） | ✅ |
| `scikit-learn` | 1.9.1 | ✅ |
| `psutil` | 7.2.2 | ✅ |

### 5.3 `Agentic Workflow/qmclaw-server/python/requirements.txt`

| 声明 | 环境实际 | 结果 |
|---|---|---|
| `numpy>=1.20.0` | 2.4.6 | ✅ |
| `scipy>=1.7.0` | 1.17.1 | ✅ |
| `matplotlib>=3.5.0` | 3.11.2 | ✅ |
| `sympy>=1.10.0` | 1.14.0 | ✅ |
| `h5py>=3.10.0` | 3.16.0 | ✅ |
| `labrad>=0.9.0` | 无 `labrad` 发行版；由 `pylabrad 0.98.7` 提供顶层 `labrad` 包 | ⚠️ 导入可用，但 `pip check` 会报缺失 |
| `pyopenssl>=24.0.0` | 26.4.0 | ✅ |
| `pywin32>=300` | 311 | ✅ |
| `lqms @ git+https://github.com/...@main#egg=lqms` | 0.3.1 | ⚠️ 占位 URL，不可安装 |
| `python-dotenv>=1.0.0` | 1.2.2 | ✅ |
| **`envelopes-qc>=0.1.0`** | **0.0.6** | ❌ 见 §3.4 |
| `langchain-core>=0.3.0` | 1.3.0 | ✅ |
| `langgraph>=0.2.0` | 1.1.9 | ✅ |
| `fastapi>=0.136.0` | 0.136.0 | ✅ |
| `uvicorn>=0.30.0` | 0.44.0 | ✅ |

### 5.4 `services/qubitclient_service/requirements.txt`

```
-e ../../../vendor/QubitClient
openai>=1.0.0
```

- `openai>=1.0.0` → 环境 2.24.0 ✅
- `-e ../../../vendor/QubitClient` → 按需求文件所在目录解析会得到
  `Agentic Workflow/vendor/QubitClient`，而真实位置是**仓库根**的 `QMClaw/vendor/QubitClient`，**多退了一级**。
  同一目录下 `server.py:152` 的报错提示 `pip install -e vendor/QubitClient` 也是相对 server 目录的写法，同样指不到。建议改为仓库根相对路径或绝对路径。

---

## 6. 项目代码引用但 deploy 环境未提供的模块

对全仓库 129 个 Python 文件（不含 `docs/deploy_diff_tools/` 自身）做 AST 级导入扫描，剔除标准库与项目自身模块后，与 deploy 环境比对，确认以下**真实缺口**（`win32con`、`pylab` 等经核实由 `pywin32` 的 `.pth` 与 site-packages 顶层的 `pylab.py` 提供，属假阳性；`hermes_runner`、`job_runner`、`memory_store`、`image_classifier`、`integration_api` 是 `scripts/` 内的同目录模块，也属假阳性）：

| 模块 | 引用位置 | 提供方 | 影响面 |
|---|---|---|---|
| `docx` | `measure_scripts/measure_scripts/sq_workflow/backend.py`（`from docx import Document` 等 3 处，用于生成 `.docx` 图表报告） | `python-docx`，环境内**无** | docx 报告导出功能不可用 |
| `onnxruntime` | `services/image_service/server.py` | 环境内**无** | 图像服务的 ONNX 后端不可用 |
| `torchvision` | `services/image_service/server.py` | 环境内**无**（`torch 2.14.0` 有装） | 图像服务的 Torch 后端不可用 |
| `redis` | `services/task_queue/server.py` | 环境内**无** | 任务队列的 Redis 后端不可用（可选） |
| `swiftmcp` | 根 `mcp_tools_new.py` | 环境内**无** | MCP 服务（`pyproject.toml` 中已注释为可选） |
| `sq` | `scripts/hermes_runner.py:362` | 实验室本地 `sq_workflow` 包；仓库内 `measure_scripts/measure_scripts/sq_workflow/` **只有 `backend.py` 一个文件** | 依赖 `BACKEND_DIR` 环境变量指向真实位置 |

前 5 项中，`onnxruntime` / `torchvision` / `redis` / `swiftmcp` 都对应**可选功能**（服务内部有降级或 `try/except`），`docx` 则是 `backend.py` 的硬依赖。

---

## 7. `offline_data` 数据侧差异

### 7.1 索引文件缺失

`services/common/offline_data_provider.py` 的加载逻辑是：

1. 优先读 `offline_data/qubits.json` + `offline_data/datasets.json`（`load()`，第 84-116 行）
2. 两者都不存在时，回退为**扫描 `*.dir` 目录**，用正则 `(\d+)\s*-\s*(\S+)\s+(.+)\.hdf5$` 解析文件名（第 140-197 行）
3. `experiments.json` 只影响实验类型列表，缺失时回退到 `config/experiment_configs.json`（第 232-249 行）

`deploy/offline_data` **不含任何 `.json` 索引文件**（顶层只有 3 个 `.dir` 目录），因此会走扫描兜底路径。项目自带生成命令：

```bash
python -m services.common.offline_data_provider --generate-index
```

拷贝数据后建议跑一次，避免每次启动都扫描 5,011 个文件。

### 7.2 实验类型覆盖

`OfflineDataProvider.EXPERIMENT_TYPE_MAP` 只做 8 种标准化映射（`s21`、`iqraw`、`pipulse`、`pipulse df`、`spectroscopy`、`xeb reference`、`s21power2d`、`s21zpa2d`）；未命中的按小写原名入库。实际数据里的类型分布：

| 日期目录 | 主要实验类型（文件数） |
|---|---|
| `20251005.dir` | IQraw 2006、PiPulse 1024、S21 160、Spectroscopy 153、S21zpa2d 53、PiPulse df 52、PiAmpFine 51、AlphaFine 51、IQcenter spectroscopy 43、Ramsey df 42、S21power2d 35、PulseShape 29、S21_dis 17、T1 15、XEB reference 13、各类 CZ/Swap 双比特实验 ~50 |
| `20251103.dir` | IQraw 182、PiPulse 83、T1 69、S21 57、S21power2d 47、S21zpa2d 18、S21_dis 14、Spectroscopy 14 |
| `20260324.dir` | IQraw 185、S21 111、Spectroscopy 78、PiAmpFine 72、AlphaFine 72、S21zpa2d 42、Ramsey df 40、PiPulse 36、T1 28、S21_dis 27、S21power2d 17、PiPulse df 15、XEB reference 10、SpectroscopyAuto 3、Ramsey 1、SpinEchoCPMG 1 |

即 `PiAmpFine`、`AlphaFine`、`T1`、`Ramsey df`、`S21_dis`、`PulseShape`、`SpectroscopyAuto`、`SpinEchoCPMG` 等类型**不在映射表内**，会以原始小写字符串出现在索引里，前端筛选需要能接受这些值。

### 7.3 路径不一致

- 配置期望：`Agentic Workflow/qmclaw-server/data/offline_data`
- `Agentic Workflow/qmclaw-server/test_offline.py:65` 硬编码：`D:/Documents/QMClaw/Agentic Workflow/offline_data`
  —— 既是**旧盘符路径**，又**少了 `qmclaw-server` 一级**，与运行时解析规则不符。

---

## 8. Node.js 运行时

| 项 | deploy | 本机 |
|---|---|---|
| Node | `node-v24.20.0-x64.msi` | **v24.14.0** |
| npm | — | 11.9.0 |

约束核对：

| 来源 | 约束 | 本机是否满足 |
|---|---|---|
| `qmclaw-server/package.json` engines | `node >= 18.0.0` | ✅ |
| `qmclaw-web/package.json` | 无 `engines` 字段 | — |
| `vendor/hermes-agent/package.json` engines | `node ^22.22.0 \|\| ^24.11.0 \|\| >=26.0.0`；`npm <11.10.0 \|\| >=11.17.0` | ✅（24.14.0 落在 `^24.11.0`；npm 11.9.0 落在 `<11.10.0`） |

结论：本机无需安装 deploy 里的 msi；但若日后升级 npm 到 `11.10.x`–`11.16.x` 区间，会落入 hermes-agent 明确排除的窗口。

另：项目内**没有任何 `node_modules`**，`qmclaw-server` / `qmclaw-web` / 三个 vendor JS 工作区都需要各自 `npm install`。

---

## 9. 文档与脚本中的过期路径（参考信息）

这些不影响 deploy 本身，但会让"照着文档部署"的人走偏：

| 位置 | 内容 | 问题 |
|---|---|---|
| `Agentic Workflow/README.md:104,183,197,203` | `D:\qmclaw\qmclaw-web`、`d:\Documents\QMClaw\...` | 旧路径 |
| `Agentic Workflow/README.md:139-140` | Node `C:\Program Files\AutoClaw\resources\node\node.exe`；Python `C:\Users\lqcs\Programs\Python\Python311\python.exe` | 上一台机器的路径 |
| `skills/qubit-calibration/SKILL.md:241` | 同上 Python 路径 | 同上 |
| `skills/qubit-data-analysis/SKILL.md:228` | 同上 Python 路径 | 同上 |
| `Agentic Workflow/qmclaw-server/test_offline.py:65` | `D:/Documents/QMClaw/Agentic Workflow/offline_data` | 旧路径 + 缺一级 |
| `CLAUDE.md:10` | Python 用 `C:\Users\HUAWEI\.conda\envs\qmclaw\python.exe` | 第 4 个不同的 Python 路径 |
| `services/qubitclient_service/requirements.txt:2` | `-e ../../../vendor/QubitClient` | 相对层级错误（见 §5.4） |

---

## 10. 复现本次比对的方法

```powershell
# 0) 前置：需要可读的 deploy 目录
$DEPLOY = "D:\semester4\deploy"
$PROJ   = "D:\semester4\QMClaw"

# 1) 导出两个 zip 的中央目录清单（路径 / 大小 / CRC32）
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($z in "vendor.zip","qmclaw.zip") {
  $zip = [System.IO.Compression.ZipFile]::OpenRead("$DEPLOY\$z")
  $out = New-Object System.IO.StreamWriter("$PROJ\.tmp-diff\$z.tsv")
  $out.WriteLine("path`tsize`tcrc32")
  $zip.Entries | % { $out.WriteLine("$($_.FullName)`t$($_.Length)`t$($_.Crc32)") }
  $out.Close(); $zip.Dispose()
}

# 2) 列出环境内已装发行版
Select-String -Path "$PROJ\.tmp-diff\qmclaw_zip_manifest.tsv" `
  -Pattern '^qmclaw/Lib/site-packages/([^/]+)\.dist-info/' |
  % { ($_.Matches[0].Groups[1].Value) } | Sort-Object -Unique

# 3) 确认 vendor 状态
git -C $PROJ ls-tree HEAD vendor/   # 应输出 3 行 160000
Get-ChildItem "$PROJ\vendor" -Recurse -Force | Measure-Object   # 当前为 0
```

完整的依赖约束求解脚本（基于 `packaging` 按 PEP 440 求值）放在 `docs/deploy_diff_tools/`：

| 脚本 | 作用 |
|---|---|
| `_envmeta.py` | 公共引导：从 env zip 中抽取全部 `METADATA`/`PKG-INFO`，缓存到系统临时目录（可用 `QMCLAW_METADATA_CACHE` 覆盖） |
| `check_deps_installed.py` | 检查 env 内部所有强制依赖约束是否自洽 |
| `check_vendor_and_wheels.py` | 用 `vendor.zip` 里三个 `pyproject.toml` 与 `deploy/*.whl` 的依赖去核对 env |
| `check_project_imports.py` | AST 扫描本项目导入，找出 env 未提供的模块 |

需要 `packaging` 可导入（本机 Python 3.14 自带）。可覆盖的环境变量：`QMCLAW_ENV_ZIP`、`QMCLAW_VENDOR_ZIP`、`QMCLAW_DEPLOY_DIR`、`QMCLAW_ROOT`。

---

## 11. 建议的处置顺序

**第 0 步 — 先确认事实（需要人确认）**

1. `lqms` 到底以 **0.3.3** 还是 **0.3.1** 为准？deploy 里同时存在"0.3.3 轮子"和"装了 0.3.1 的环境快照"，二者矛盾。
2. `envelopes-qc` 的 `>=0.1.0` 是笔误还是真有一个未交付的 0.1.0？
3. `quanputing` / `quanputing_server` 这两个轮子还要不要用？不用的话建议从 deploy 里移除以免误装。

**第 1 步 — 恢复 vendor（解开 🔴 阻断）**

```bash
git submodule update --init --recursive      # 推荐：拿到真实 git 历史，可校验提交号
# 或者离线解压
Expand-Archive D:\semester4\deploy\vendor.zip -DestinationPath D:\semester4\QMClaw\
```

解压后核对提交号是否为 `c4a5deef`(hermes-agent) / `3da70fe0`(QubitClient) / `d8d37bc4`(QCA)，并确认 `QubitClient/qubitclient/__init__.py` 是 `0.6.22`。

**第 2 步 — 用新路径重建 editable 安装**

新环境（把 `qmclaw.zip` 解压到目标路径后）必须先修正两个 `.pth` 指向的旧路径：

```powershell
$PY = "<env>\python.exe"
& $PY -m pip install -e "D:\semester4\QMClaw\vendor\hermes-agent"
& $PY -m pip install -e "D:\semester4\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint"
& $PY -m pip install -e "D:\semester4\QMClaw\vendor\QubitClient"   # 顺带把 0.6.21 → 0.6.22
& $PY -m pip install -e "D:\semester4\QMClaw"                      # 让 new_ctrl / new_templates 可用
```

**第 3 步 — 拷贝离线数据并建索引**

```powershell
Copy-Item -Recurse -Force "D:\semester4\deploy\offline_data" `
  "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server\data\offline_data"
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"
& $PY -m services.common.offline_data_provider --generate-index
```

**第 4 步 — 收敛版本差异**

- 依据第 0 步的结论统一 `lqms` 版本
- 删掉 `lqcs_deploy/requirements.txt` 里的 `cryptography==36.0.0`
- 修正或删除 `envelopes-qc>=0.1.0`
- 补齐 `rich==14.3.4`、`openai>=2.26.0`（或反之调整 `langchain-openai` / `qca` 的 pin）
- 视需要补装 `python-docx`（docx 报告）、`onnxruntime`+`torchvision`（图像服务）

**第 5 步 — 修文档与脚本里的旧路径**（见 §9），并把 `QMCLAW_ROOT` 的探测逻辑从"硬找 `vendor/hermes-agent`"改为可配置项，避免再次出现"环境跟着盘符走"的问题。

---

## 附录 A：`qmclaw.zip` 环境概况

| 项 | 值 |
|---|---|
| 环境名 | `qmclaw` |
| Python | 3.11.16（conda-forge `python-3.11.16-hb12b558_1_cpython`） |
| conda 包 | 21 个（全部来自清华 conda-forge 镜像） |
| pip 发行版 | 249 个 `dist-info`/`egg-info`（248 个含 `METADATA`） |
| 条目总数 | 91,340 |
| `Lib/site-packages` 解压体积 | ≈2,027 MB |
| 关键包 | `torch 2.14.0`、`ray 2.41.0`、`numpy 2.4.6`、`pandas 3.0.5`、`langchain 1.2.15`、`langgraph 1.1.9`、`deepagents 0.5.3`、`qutip 5.3.1`、`pyqt6 6.11.0`、`lqcs 0.1.3`、`lqms 0.3.1`、`pylabrad 0.98.7`、`envelopes_qc 0.0.6`、`qca 0.1.0`、`hermes_agent 0.21.0`、`qubitclient 0.6.21` |

## 附录 B：`vendor.zip` 目录概况

| 路径 | 条目 | 解压后大小 | 顶层标识 |
|---|---|---|---|
| `hermes-agent/` | 13,665 | 172.0 MiB | `pyproject.toml` → `hermes-agent 0.21.0`；`package.json` → `hermes-agent 1.0.0`（npm workspaces） |
| `QubitClient/` | 919 | 20.1 MiB | `pyproject.toml` → `qubitclient`（dynamic）；`qubitclient/__init__.py` → `__version__ = "0.6.22"` |
| `Quantum-Calibration-Agent-Blueprint/` | 412 | 4.5 MiB | `pyproject.toml` → `qca 0.1.0`；`py-modules = ["cli","prompt","server"]` |
| **合计** | **14,996**（其中 13,584 个非空文件） | 196.6 MiB | 三个目录各有一个 `.git` 指针文件指向 `../../.git/modules/vendor/<name>`，**不含 git 对象库** |
