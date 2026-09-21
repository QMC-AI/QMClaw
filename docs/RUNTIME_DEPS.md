# QMClaw 运行依赖清单与接线指南

> 前提：`qmclaw.zip`、`vendor.zip` 已解压完成（2026-09 最新一次比对后）。
> 本文件所有结论都在真实解压出来的环境上**实测得出**，不是从压缩包元数据推断。
> 关联文档：`docs/DEPLOY_DIFF.md`（差异报告）。

---

## 0. 一句话结论

**Python 环境本身是完整的**（249 个包，10 个微服务里 9 个能直接 import）。
现在缺的不是"包"，而是**接线**：vendor 内容没落位、2 处硬编码路径指向上一个项目位置、离线数据没拷、`.env` 没建、Node 依赖没装。

---

## 1. 解压后的实际布局（已实测）

| 路径 | 内容 | 状态 |
|---|---|---|
| `D:\semester4\deploy\qmclaw\qmclaw\` | conda 环境根（注意**双层同名目录**） | ✅ 可用 |
| `D:\semester4\deploy\qmclaw\qmclaw\python.exe` | Python **3.11.16**，conda-forge 构建 | ✅ 实测可运行 |
| `D:\semester4\deploy\vendor\` | hermes-agent / QubitClient / Quantum-Calibration-Agent-Blueprint | ✅ 内容完整 |
| `D:\semester4\deploy\offline_data\` | 3 个日期目录，5,011 个 HDF5 | ✅ 就位 |
| `D:\semester4\deploy\*.whl` | lqcs / lqms / pylabrad / envelopes_qc / quanputing×2 | ✅ 就位 |
| `D:\semester4\QMClaw\vendor\` | 三个**空目录** | ❌ 待填充 |

### 1.1 本机没有 Python 二进制被注册为 `python`

只有 `C:\Users\39045\AppData\Local\Programs\Python\Python314\python.exe`（无关）。
**所有 Python 命令都必须显式用环境里的解释器**：

```powershell
$PY = "D:\semester4\deploy\qmclaw\qmclaw\python.exe"
& $PY -V      # Python 3.11.16
```

### 1.2 环境健康度实测

```
$ & $PY -m pip list --format=freeze | Measure-Object -Line
  249

$ & $PY -m pip check
  langchain-openai 1.2.0 has requirement openai<3.0.0,>=2.26.0, but you have openai 2.24.0.
  qca 0.1.0 has requirement rich==14.3.4, but you have rich 14.3.3.
```

两处冲突不阻塞启动（都是宽松运行期依赖），但建议修掉，见 §6。

### 1.3 微服务模块导入实测（`check_services.py`）

```
  OK    llm          :3006   OK    quantum      :3003   OK    analysis     :3004
  OK    agent        :3005   OK    image        :3007   OK    workflow     :3008
  OK    task_queue   :3009   FAIL  qca          :3011   OK    hermes       :3012
  OK    qubitclient  :—                    →  9 / 10
```

`qca` 的失败原因不是缺包，是**源码里的绝对路径写死**，见 §2.2。

### 1.4 全项目导入实测（`check_runtime_imports.py`）

| 场景 | 可解析 | 结论 |
|---|---|---|
| 现状（什么都不改） | 30 / 91 | vendor 相关的 9 个模块全部解析失败 |
| 把 `deploy\vendor\{hermes-agent,Quantum-Calibration-Agent-Blueprint}` 加进 `sys.path` | **39 / 91** | 全部 vendor 模块恢复正常 |

剩下的 13 个中：7 个是项目自身模块（`backends`、`hermes_runner`、`job_runner`、`memory_store`、`image_classifier`、`integration_api`、`services`，运行时由 `sys.path` 提供）、1 个是实验室本地包 `sq`、**5 个是真正缺失的可选依赖**（见 §5）。

---

## 2. 必须做的 5 件事

### 2.1 把 vendor 内容放到项目里

`hermes_service` 和 `qca_service` 都按 `QMCLAW_ROOT/vendor/<name>` 找代码。当前 `D:\semester4\QMClaw\vendor\` 三个目录是空的 —— 注意**空目录也算"存在"**，所以 `_find_qmclaw_root()` 探测成功，但往里找不到模块。

```powershell
$DST = "D:\semester4\QMClaw\vendor"
Copy-Item -Recurse -Force "D:\semester4\deploy\vendor\hermes-agent"                          "$DST\hermes-agent"
Copy-Item -Recurse -Force "D:\semester4\deploy\vendor\QubitClient"                           "$DST\QubitClient"
Copy-Item -Recurse -Force "D:\semester4\deploy\vendor\Quantum-Calibration-Agent-Blueprint"   "$DST\Quantum-Calibration-Agent-Blueprint"
```

> 实测确认：`deploy\vendor` 的内容**足以**满足 `run_agent`、`hermes_cli`、`agent`、`toolsets`、`model_tools`、`acp_adapter`、`core.*`、`prompt`、`cli`、`server`、`tools`、`vlm` 的全部导入。

> 也可用 `git submodule update --init --recursive` 走网络拉取，好处是能校验提交号
> （`c4a5deef` hermes-agent / `3da70fe0` QubitClient / `d8d37bc4` QCA）。离线场景用上面的拷贝。

拷贝完成后确认 `vendor\QubitClient\qubitclient\__init__.py` 里的 `__version__` 是 `"0.6.22"`。

### 2.2 修 2 处写死的绝对路径（**源码问题，必须改**）

这不是环境的问题，是仓库代码里就写死了上一个项目位置 `D:\Documents\QMClaw`：

| 文件 | 行 | 内容 |
|---|---|---|
| `Agentic Workflow/qmclaw-server/services/qca_service/server.py` | 38 | `VENDOR_QCA_DIR = Path(r"D:\Documents\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint")` |
| `Agentic Workflow/qmclaw-server/services/qca_service/tools/lab_tool.py` | 17 | `VENDOR_DIR = Path(r"D:\Documents\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint")` |

实测日志（`check_services.py` 输出）已经印证：

```
[QCA] Vendor dir: D:\Documents\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint
[QCA] sys.path[0]: D:\Documents\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint
  FAIL  qca :3011  ModuleNotFoundError: No module named 'core.workflow_validation'
```

`server.py` 里还用它去 `load_dotenv(VENDOR_QCA_DIR / ".env")`，所以 QCA 的 `.env` 也在找错地方。

改法（两处同样处理，不再写绝对路径）：

```python
# qca_service/server.py  —— 相对 __file__ 推导，_SERVICE_DIR 已在第 26 行定义
VENDOR_QCA_DIR = _SERVICE_DIR.parent.parent.parent / "vendor" / "Quantum-Calibration-Agent-Blueprint"
if not VENDOR_QCA_DIR.exists():
    raise RuntimeError(f"vendor QCA not found: {VENDOR_QCA_DIR} (did you copy deploy\\vendor?)")
```

```python
# qca_service/tools/lab_tool.py  —— 该文件在 services/qca_service/tools/ 下，多退一级
VENDOR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "vendor" / "Quantum-Calibration-Agent-Blueprint"
```

### 2.3 离线数据落位 + 建索引

配置 `fallback_config.json` 的 `data_path: "data/offline_data"` 是**相对 qmclaw-server 根**解析的：

```python
# services/quantum_service/server.py:227
offline_path = Path(__file__).parent.parent.parent / self._offline_data_path
```

而 `Agentic Workflow/qmclaw-server/data/` 整个目录还不存在（已被 `.gitignore` 忽略，属正常）。

```powershell
$SRV = "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"
New-Item -ItemType Directory -Force -Path "$SRV\data" | Out-Null
Copy-Item -Recurse -Force "D:\semester4\deploy\offline_data" "$SRV\data\offline_data"

cd $SRV
& $PY -m services.common.offline_data_provider --generate-index
```

`deploy\offline_data` 里**没有** `qubits.json` / `datasets.json` / `experiments.json` 索引文件，不建索引的话每次启动都要扫描 5,011 个文件。

> ⚠️ 顺带发现配置冲突：`config/session.json` 写的是 `["test", "20260822_device1"]`，而 `config/system.json` 的
> `lqcs.session.default_path` 写的是 `["test", "20260324"]`，离线数据里只有 `20251005 / 20251103 / 20260324`。
> **`20260822_device1` 这个 session 在哪都不存在**，切会话会失败。建议先统一成 `20260324`。

### 2.4 建 `.env`（API Key + 解释器路径）

Express 端从 `qmclaw-server/.env` 读（`src/index.ts:41`，`path.join(__dirname, "..", ".env")`），
Hermes 端也从同一位置读（`hermes_service/server.py:76`）。

```powershell
Copy-Item "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server\.env.example" `
          "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server\.env"
```

然后填写（**至少一个 Key**，因为 `model_configs.json` 里 9 个模型分属 4 个 provider）：

| 变量 | 用途 | 备注 |
|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek Chat / Hermes 默认 | `.env.example.demo` 推荐先用这个 |
| `MINIMAX_API_KEY` + `MINIMAX_GROUP_ID` | MiniMax M2.7 / Text-01 / ABAB | `model_configs.json` 里模型最多的一家 |
| `OPENAI_API_KEY` | GPT-4o / 4o-mini / 4-Turbo | |
| `ANTHROPIC_API_KEY` | Claude 3.5 Sonnet | |
| `PYTHON_BIN` | **指向 §1.1 的解释器** | 不设会回落到 `"python"` 而失败 |
| `PORT` | Express 端口 | 代码默认 `8080`，但前端 `apiClients.ts` 默认 `3002`、README/CLAUDE.md 也写 3002 → **务必显式设 `PORT=3002`** |
| `PLOTS_DIR` | 绘图输出目录 | 可选，默认指向 `qmclaw-web/public/plots` |
| `QMCLAW_USE_SERVICES` | `true` 启用微服务 | |

```
PYTHON_BIN=D:\semester4\deploy\qmclaw\qmclaw\python.exe
PORT=3002
```

> `D:\semester4\QMClaw\.env.example.demo` 是 Hermes 的演示模板，但它放在**仓库根**，而代码读的是
> `qmclaw-server\.env` —— 内容可以借鉴，位置要放对。

### 2.5 Node 依赖

Node 运行时本机是 **v24.14.0 / npm 11.9.0**，满足需求（`qmclaw-server` 要 `>=18`；vendor 的
`hermes-agent` 要 `^22.22.0 || ^24.11.0 || >=26.0.0`，npm `<11.10.0 || >=11.17.0`）。
`deploy\node-v24.20.0-x64.msi` **可以不装**。

项目内目前**没有任何 `node_modules`**，三处都要装：

```powershell
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-web";    npm install   # next 14.2.35 / react 18.2.0 / @xyflow/react / recharts / socket.io-client / zustand
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"; npm install   # express / cors / dotenv / socket.io / tsx / typescript
cd "D:\semester4\QMClaw\vendor\hermes-agent";            npm install   # 可选：只在要用 Hermes 的 TUI/桌面端时需要
```

> `Agentic Workflow\qmclaw-server\run-dev.cmd` 里写死了 `C:\Program Files\AutoClaw\resources\node`，
> 本机是 `C:\Program Files\nodejs`。直接 `npm run dev` 即可，或改掉这行。

---

## 3. 各组件依赖矩阵（实测）

| 组件 | 端口 | Python 依赖 | 外部依赖 | 状态 |
|---|---|---|---|---|
| **llm_service** | 3006 | openai, anthropic, httpx, dotenv | 至少一个 LLM API Key | ✅ |
| **quantum_service** | 3003 | `lqms`, `labrad`, `pylab`, h5py, numpy | **LabRAD 服务 :7682**；离线模式需要 `data/offline_data` | ✅ 代码就绪，等数据 |
| **analysis_service** | 3004 | `lqms`, matplotlib, numpy, scipy | 同上 | ✅ |
| **agent_service** | 3005 | langchain, langgraph, httpx | 经 HTTP 调 quantum_service | ✅ |
| **image_service** | 3007 | torch（**CPU 版**）、PIL、numpy | `onnxruntime` / `torchvision` **未装** | ⚠️ Torch/ONNX 后端不可用 |
| **workflow_service** | 3008 | 标准库为主 + httpx | 依赖其他服务 | ✅ |
| **task_queue** | 3009 | 标准库 | `redis` **未装**（默认内存后端可用） | ✅ 降级可用 |
| **qca_service** | 3011 | deepagents, langchain-core, langgraph, fastapi | **vendor QCA 路径**（§2.2 要改） | ❌ 需先修路径 |
| **hermes_service** | 3012 | `run_agent`（vendor hermes-agent） | **vendor 路径**（§2.1 要拷） | ⚠️ 现在能启但 AIAgent 不可用 |
| **qubitclient_service** | — | `qubitclient` 0.6.21 ✅ | `config/qubitclient.json`（**不存在**） | ⚠️ 缺配置文件 |
| Express 网关 | 3002 | — | Node + `PYTHON_BIN` | ✅ |
| Next.js 前端 | 8081* | — | Node | ✅ |
| LabRAD 服务 | 7682 | `lqcs` ✅ | 需要**手动启动** | ✅ 包就绪 |

\* `qmclaw-web/package.json` 里是 `next dev -p 8081`，而 CLAUDE.md 写 3001 —— 以 package.json 为准。

`system.json` 声明 LQCS 后端的 `required_packages: ["lqms", "labrad", "pylab"]`，实测三者全部 ✅。

### 3.1 LabRAD / 测控侧依赖（`lqcs_deploy/requirements.txt`，34 条）

实测结果：**33 条满足**，唯一不满足的是 `cryptography==36.0.0`（环境里是 50.0.0，且与本项目
`pyopenssl>=24.0.0` 的要求互斥）。其余如 `twisted 26.4.0`、`PyQt6 6.11.0`、`stim 1.16.0`、
`qutip 5.3.1`、`cvxpy 1.9.2`、`pyvisa 1.16.2`、`apscheduler 3.11.3`、`ray 2.41.0`、`torch 2.14.0`
全部就位。

启动 LabRAD GUI：

```powershell
& $PY -c "from lqcs.servers_control import run_server_control; run_server_control()"
```

然后在 GUI 里依次启：labrad / datas / grapher / registry editor / ray / device manager / uwave manager。

---

## 4. 启动顺序

```powershell
$PY  = "D:\semester4\deploy\qmclaw\qmclaw\python.exe"
$SRV = "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"

# ① LabRAD（人工操作 GUI）
& $PY -c "from lqcs.servers_control import run_server_control; run_server_control()"

# ② 微服务（前台，看日志）
cd $SRV
& $PY -m services.quantum_service.server      # :3003
& $PY -m services.analysis_service.server     # :3004
# …其余按需

# ③ Express 网关 :3002（另开终端）
cd $SRV; npm run dev

# ④ 前端 :8081（另开终端）
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-web"; npm run dev
```

> 约定：**前后端服务的启停由人操作**，不要交给自动化脚本。

---

## 5. 真正缺失的可选依赖

实测 `find_spec` 全部返回 `None`。缺了只影响对应功能，不影响主流程启动：

| 模块 | pip 包名 | 影响 | 安装命令 |
|---|---|---|---|
| `docx` | `python-docx` | `measure_scripts/.../sq_workflow/backend.py` 的 docx 图表报告导出 | `& $PY -m pip install python-docx` |
| `onnxruntime` | `onnxruntime` | image_service 的 ONNX 推理后端 | `& $PY -m pip install onnxruntime` |
| `torchvision` | `torchvision` | image_service 的 Torch 后端 | 需匹配 `torch 2.14.0+cpu` |
| `redis` | `redis` | task_queue 的 Redis 后端（默认内存后端可用） | `& $PY -m pip install redis` |
| `swiftmcp` | `swiftmcp` | 根目录 `mcp_tools_new.py` 的 MCP 服务 | `& $PY -m pip install swiftmcp` |

另有两个"包在但没配好"的：

- **`qubitclient_service`** 需要 `config/qubitclient.json`，现在不存在。它会依次找
  `services/qubitclient_service/config/qubitclient.json` → `qmclaw-server/config/qubitclient.json`
  → `vendor/QubitClient/qubitclient.json` → `./qubitclient.json`。
  `deploy\vendor\QubitClient\qubitclient.json.example` 是模板。
- **`torch` 是 CPU 版**（`2.14.0+cpu`），图像推理没有 GPU 加速。

**不要装** `quanputing` / `quanputing_server` 这两个轮子：项目代码零引用，且会与现有环境产生
15 处版本冲突 + 7 处缺失依赖（含引入第二套 Qt 绑定 PySide6）。

---

## 6. 建议顺手修掉的环境问题

```powershell
# ① 两处强制依赖冲突
& $PY -m pip install "openai>=2.26.0,<3"      # 满足 langchain-openai 1.2.0
& $PY -m pip install "rich==14.3.4"           # 满足 qca 的 pin
& $PY -m pip check                            # 期望：No broken requirements found.

# ② conda-meta 与 site-packages 不一致（packaging 26.3→26.0、prompt-toolkit 3.0.53→3.0.52）
#    是 pip 覆盖 conda 装的包造成的。只要不跑 conda install 就无影响；
#    若必须修，用 pip 重装而不是 conda：
& $PY -m pip install --force-reinstall "packaging==26.3" "prompt-toolkit==3.0.53"
```

`lqms` 版本待定（deploy 轮子是 **0.3.3**，环境里装的是 **0.3.1**），确认后再决定是否升级：

```powershell
& $PY -m pip install --force-reinstall "D:\semester4\deploy\lqms-0.3.3-cp311-cp311-win_amd64.whl"
```

> 环境里 `hermes_agent` 和 `qca` 是 **editable** 安装，`.pth` 里写死指向 `D:\Documents\QMClaw\vendor\...`。
> 只要按 §2.1 把 vendor 拷到项目里，`hermes_service` / `qca_service` 走的是**自己的 sys.path 逻辑**，
> 不依赖这两个 editable 安装，所以**不改 .pth 也能跑**。
> 但如果你想在命令行直接用 `hermes` / `qca` 命令，或者 `import hermes_cli`，就需要重装：
>
> ```powershell
> & $PY -m pip install -e "D:\semester4\QMClaw\vendor\hermes-agent"
> & $PY -m pip install -e "D:\semester4\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint"
> & $PY -m pip install -e "D:\semester4\QMClaw\vendor\QubitClient"   # 顺带 0.6.21 → 0.6.22
> ```
>
> 另外环境里 `qubitclient 0.6.21` 是**普通安装**（在 site-packages 里），可直接 import；
> 但与 vendor 源码的 `0.6.22` 差一个补丁版本。

---

## 7. 附：本次用到的校验脚本

都在 `docs/deploy_diff_tools/`，用环境里的解释器跑（会自动缓存元数据到系统临时目录，不污染仓库）：

```powershell
$PY = "D:\semester4\deploy\qmclaw\qmclaw\python.exe"
$T  = "D:\semester4\QMClaw\docs\deploy_diff_tools"

& $PY -B "$T\check_services.py"           # 10 个微服务能否 import
& $PY -B "$T\check_env_imports.py"        # 按功能分组的依赖冒烟测试
& $PY -B "$T\check_runtime_imports.py" "D:\semester4\QMClaw" `
        "D:\semester4\QMClaw\vendor\hermes-agent" `
        "D:\semester4\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint"
& $PY -B "$T\check_deps_installed.py"     # env 内部依赖自洽性
& $PY -B "$T\check_vendor_and_wheels.py"  # vendor/轮子 声明 vs env
```

---

## 8. 待你确认的三件事

1. **`lqms` 用 0.3.3 还是 0.3.1？** deploy 里的轮子是 0.3.3，环境里装的是 0.3.1，两者矛盾。
2. **`session.json` 的 `20260822_device1`** 是笔误还是有个没交付的 session？离线数据里只有 20260324 那批。
3. **`envelopes-qc`** 声明 `>=0.1.0` 但只交付了 0.0.6，且代码零引用 —— 是笔误还是漏交付？
