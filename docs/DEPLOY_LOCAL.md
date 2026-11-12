# QMClaw 本机部署记录（工作区 `D:\qmclaw`）

> 本文档记录的是**本机**（Windows + `D:\anaconda3\envs\lqcs`）的实际部署结果。
> `docs/RUNTIME_DEPS.md` 与 `docs/DEPLOY_DIFF.md` 描述的是**上一台机器**
> （`D:\semester4\QMClaw` + `deploy\qmclaw` conda 环境 + `deploy\vendor`），
> 路径、解释器、数据来源都与本机不同，**不能再照着它们操作**。
>
> 一键复现：`deploy-local.cmd`（即 `deploy-local.ps1`，见 §7）

---

## 0. 结论

| 项 | 状态 |
|---|---|
| Python 依赖（微服务侧） | ✅ 已补齐（192 → 214 个发行版，新增 22 / 升级 7） |
| Node 依赖（Express + 前端） | ✅ 已安装（server 106 / web 195 个包） |
| 运行期目录 / `.env` | ✅ 已创建 |
| 微服务 import 自检 | ✅ **10 / 10** 可导入，0 失败 |
| `vendor/hermes-agent` | ✅ 已就位（提交 `c4a5dee`，**浅克隆**） |
| `vendor/QubitClient` | ✅ 已就位（提交 `3da70fe`，`qubitclient 0.6.22`） |
| `vendor/Quantum-Calibration-Agent-Blueprint` | ✅ 已就位（提交 `d8d37bc`） |
| LabRAD 真机 | ✅ **已对接**（本机 `localhost:7682`），见 §9、§10 |
| 当前会话 | `/LQHL/test/20261016`（从 `20260324` 复制而来），见 §10 |
| 写闸门 | 🔓 **已打开**（`quantum_service` 两项 = `true`，**全局生效**），见 §10.3 |
| LLM API Key | ⚠️ 未填，见 §6.3 |
| 离线数据 `data/offline_data` | ⚠️ 空目录；真机已可用，暂时不需要它，见 §6.2 |
| `qubitclient.json` | ⚠️ 未创建，见 §6.5 |
| 服务是否已启动 | 否 —— 按项目约定 3，启停由人操作，见 §5 |

---

## 1. 本机环境事实

| 项 | 值 | 备注 |
|---|---|---|
| 工作区 | `D:\qmclaw` | **不是 git 仓库**（无 `.git`，是从别处拷贝来的工作副本） |
| Python | `D:\anaconda3\envs\lqcs\python.exe` | Python **3.11.9**，部署后 192 → 214 个发行版 |
| Node / npm | `v26.3.0` / `11.16.0` | 满足 `qmclaw-server` 的 `node >= 18` |
| Express 网关 | `:3002` | 由 `.env` 的 `PORT=3002` 指定 |
| Next.js 前端 | `:8081` | 由 `qmclaw-web/package.json` 指定（不是文档里写的 3001） |
| 各微服务 | 3003–3009 / 3010 / 3011 / 3012 | 见 `config/services.json` |

文档里出现过的其它解释器路径**在本机均不存在**（`D:\semester4\deploy\qmclaw\qmclaw\python.exe`、
`C:\Users\HUAWEI\.conda\envs\qmclaw\python.exe`），已统一改为上面的 `lqcs` 环境。

---

## 2. 已完成的动作

全部由 `deploy-local.ps1` 串起来，分五步：

### 2.1 `vendor/` 三个子模块

| 子模块 | 来源 | 结果 |
|---|---|---|
| `hermes-agent` | `git clone` → `https://github.com/Secsix/hermes-agent` | ✅ 提交 `c4a5dee`，11,977 个文件，**浅克隆** |
| `QubitClient` | `git clone` → `https://github.com/Secsix/QubitClient` | ✅ 提交 `3da70fe`，`__version__ = "0.6.22"` |
| `Quantum-Calibration-Agent-Blueprint` | `git clone` → `https://github.com/Secsix/...` | ✅ 提交 `d8d37bc`，328 个文件 |

三个提交号都与 `.gitmodules` 在上一台机器记录的 gitlink **完全一致**。

本机 GitHub 连通性**极不稳定**：TCP 能连、TLS 能握手，但大仓库传输中途会被
`Connection was reset`（`curl 56 Recv failure`）打断。三个仓库都失败过多次，
最终靠**重试 + `-c http.version=HTTP/1.1`** 成功：

```powershell
git -c http.version=HTTP/1.1 clone https://github.com/Secsix/hermes-agent <目标目录>
```

`hermes-agent` 是浅克隆（`--depth 1`），所以它只含一个提交、没有历史；日常使用
没问题，但如果之后要 `git pull` 或切别的提交，先 `git fetch --unshallow`。

### 2.2 Python 依赖（装进 `D:\anaconda3\envs\lqcs`）

只装"项目里真的 `import`、环境里确实没有"的包，清单与来源：

| 包 | 服务的引用位置 |
|---|---|
| `portalocker` | `services/hermes_service/memory.py` |
| `langgraph` | `services/qca_service/server.py` |
| `langchain` | `services/qca_service/server.py` |
| `deepagents` | `services/qca_service/server.py` |
| `redis` | `services/task_queue/server.py`（可选后端，默认内存后端可用） |
| `onnxruntime` | `services/image_service/server.py`（可选后端） |
| `pyopenssl>=24.0.0` | 需求声明 `>=24`，环境里原为 22.0.0 |

实际落地 **新增 22 个、升级 7 个**（含被连带拉进来的 `langchain-anthropic`、
`langchain-google-genai`、`google-genai`、`google-auth`、`httpx2`、`langsmith` 等）。
改动前后的完整清单在 `.deploy-logs/pip-freeze-{before,after}-*.txt`。

**升级的 7 个（这 7 个是本机既有环境里已有的包）**：

| 包 | 原 | 新 |
|---|---|---|
| `cryptography` | 36.0.0 | **50.0.2** |
| `pyOpenSSL` | 22.0.0 | **26.4.0** |
| `websockets` | 14.1 | **16.1.1** |
| `langchain-core` | 1.3.2 | 1.6.11 |
| `langchain-protocol` | 0.0.12 | 0.0.19 |
| `langsmith` | 0.7.37 | 0.14.9 |
| `idna` | 3.11 | 3.20 |

`pip check` 的净变化（诚实记账）：

- ✅ 顺带**修好**两条既有冲突：`fastmcp requires websockets>=15.0.1`、
  `joserfc requires cryptography>=45.0.1`
- ⚠️ **新增**两条冲突：`quanputing` / `quanputing-server` 都精确要求
  `websockets==14.1`，现在被升到 16.1.1
- 其余 `quanputing` 的 7 条缺失依赖、2 条版本冲突都是**改动前就存在**的

> 若你仍在用 `quanputing` 那套 Qt 工具，把 `websockets` 退回 14.1 即可
> （`langgraph-sdk` 要求的是 `>=14,<17`，退回不影响本项目）：
> `& D:\anaconda3\envs\lqcs\python.exe -m pip install websockets==14.1`

### 2.3 Node 依赖

| 目录 | 命令 | 结果 |
|---|---|---|
| `Agentic Workflow/qmclaw-server` | `npm ci` | 106 个包 |
| `Agentic Workflow/qmclaw-web` | `npm ci` | 195 个包 |

实测 `tsx v4.22.4` + `esbuild 0.28.0` 可用。npm 会提示
`esbuild@0.28.0 (postinstall: node install.js)` 未执行 —— **这是虚惊**：
esbuild 的平台二进制来自可选依赖 `@esbuild/win32-x64`，不依赖 postinstall。

### 2.4 运行期目录

`qmclaw-server/{data,data/workflows,data/templates,data/offline_data,logs,config/memory}`
与 `qmclaw-web/public/plots` 已创建（都已在 `.gitignore` 中）。

### 2.5 `.env`

`Agentic Workflow/qmclaw-server/.env` 已创建，内容：

```env
PYTHON_BIN=D:\anaconda3\envs\lqcs\python.exe
PLOTS_DIR=D:\qmclaw\Agentic Workflow\qmclaw-web\public\plots
PORT=3002
QMCLAW_USE_SERVICES=true
# 四家 LLM Key 全部留空 —— 见 §6.3
```

---

## 3. 与 `docs/RUNTIME_DEPS.md` 的关键差异

| 项 | 那份文档（上一台机器） | 本机实际 |
|---|---|---|
| Python 环境 | conda env `qmclaw`（249 包，带 `lqms 0.3.1` / `lqcs 0.1.3`） | 复用 `D:\anaconda3\envs\lqcs`（`lqms 0.2.1` / `lqcs 0.1.2`） |
| vendor 来源 | `deploy\vendor`（离线包） | GitHub（不稳定），只有前两个成功 |
| 离线数据 | `deploy\offline_data`（5,011 文件 / 413 MB） | **无**，本机不存在该数据 |
| `lqms` 版本 | 待定（0.3.1 vs 0.3.3） | 沿用环境的 **0.2.1**，未改动 |
| `qubitclient` | 环境 0.6.21 / vendor 0.6.22 | 环境 **0.6.21**，vendor **0.6.22**（与文档一致） |

`docs/RUNTIME_DEPS.md` §2.2 提到的两处写死绝对路径（`D:\Documents\QMClaw\...`）
**在本工作区已经修好**，现按 `__file__` 相对推导：

- `services/qca_service/server.py:40` → `_SERVICE_DIR.parents[3] / "vendor" / ...`
- `services/qca_service/tools/lab_tool.py:19` → `Path(__file__).resolve().parents[5] / "vendor" / ...`

`config/session.json` 也已是 `["test","20260324"]`（不再是那个不存在的 `20260822_device1`）。

---

## 4. 验证结果

```
== 5. verify ==
--- node toolchain
  qmclaw-server: tsx v4.22.4 (esbuild win32-x64)
  qmclaw-web: next 14.2.35
--- microservice import smoke test
  [hermes_service] Added hermes-agent to sys.path
  [hermes_service] Successfully imported AIAgent from hermes-agent     <-- 关键
  [QCA] Vendor dir: D:\qmclaw\vendor\Quantum-Calibration-Agent-Blueprint
  [QCA] Deep Agents framework loaded successfully
  OK    llm :3006      OK    quantum :3003    OK    analysis :3004
  OK    agent :3005    OK    image :3007      OK    workflow :3008
  OK    task_queue :3009   OK  qca :3011      OK    hermes :3012
  OK    qubitclient
  可导入 10 / 10，失败 0
```

脚本最终报告 `all steps completed, no failures`。

---

## 5. 启动方式（由人操作）

> 依据项目约定 3：**前后端服务启停由人完成**，部署脚本不启动任何服务。
> 另外 `config/services.json` 是 `use_microservices: true`，此时 Express **不会**
> 代起 Python 服务，10 个微服务必须单独启动。

```powershell
$PY  = "D:\anaconda3\envs\lqcs\python.exe"
$SRV = "D:\qmclaw\Agentic Workflow\qmclaw-server"
$WEB = "D:\qmclaw\Agentic Workflow\qmclaw-web"

# ① 10 个 Python 微服务（必须走这个脚本，否则 .env 不会被加载）
cd "$SRV\scripts"; & $PY start_all_services.py --all

# ② Express 网关 :3002
cd $SRV; npm run dev

# ③ 前端 :8081
cd $WEB; npm run dev

# ④ 浏览器 http://localhost:8081
```

省事模式：`.env` 里把 `QMCLAW_USE_SERVICES` 改成 `false`，Express 会自己
spawn `scripts/job_runner.py`（legacy 单体模式），只需启 ②③ 两步。

真机在线模式还需要先起 LabRAD（人工点 GUI）：

```powershell
& $PY -c "from lqcs.servers_control import run_server_control; run_server_control()"
```

---

## 6. 已知局限与待办

### 6.1 写闸门（2026-10-16 已按用户指示打开，见 §10.3）

- 2026-10-16 11:50 之前：`allow_execute` / `allow_set_params` 均为 `false`（只读联调）。
- **现在：`quantum_service` 两项均为 `true`**，可下发实验、可改参数。
- **注意这是全局开关**：代码里没有按 session / 目录的隔离，任何 session 都变成可写。
- `analysis_service` 的两项仍为 `false`（它守的是分析侧 `/execute`）。
- 用完请改回 `false` 并重启服务；提权流程原始说明见 `docs/REAL_MACHINE.md` 第四节。

### 6.2 离线数据缺失（真机可用后已不阻塞）

- `data/offline_data/` 是空目录。`fallback_config.json` 现在是
  `default_mode: "auto"`，也就是说 quantum / analysis 会**先尝试连 LabRAD**，
  连不上再退回离线 —— 但离线侧没有数据，等于两边都取不到数。
- 本机全盘没有 `offline_data`（上一台机器的 413 MB 数据没有交付到本机）。
- 要跑离线演示，需要先把历史 HDF5 数据拷到
  `Agentic Workflow/qmclaw-server/data/offline_data/`，再生成索引：
  `& $PY -m services.common.offline_data_provider --generate-index`

### 6.3 LLM API Key 未填

`.env` 里四家 Key 全空。`model_configs.json` 里的模型分属 4 个 provider，
**至少填一个**（DeepSeek 最省事）才能用 agent / decision 类功能，否则：
`llm_service` 起得来但每次调用报缺 Key，`agent` / `workflow` 的 LLM 节点不可用。

### 6.4 `npm run build` 仍不可用（仓库既有问题）

`tsc` 有既存类型错误（上一台机器实测 server 24 个、web 6 个），而 `npm run dev`
用的 `tsx` / `next dev` 不做类型检查。**本机部署只支持 dev 模式**，生产构建需要
先修类型错误。

### 6.5 `qubitclient.json` 未创建

`qubitclient_service` 会依次找
`services/qubitclient_service/config/` → `qmclaw-server/config/` →
`vendor/QubitClient/` → `./`。现在都没有，服务会以 `initialized=False` 启动
（只影响 QubitClient/NNScope 相关能力）。
模板是 `vendor/QubitClient/qubitclient.json.example`。
注意该文件含实验室内部地址与 Key，已被 `.gitignore` 全局忽略，**不要提交**。

### 6.6 `experiment_configs.json` 里有 6 个名字对不上 `sq_nodes`

自检发现的既有问题（非本次引入）。配置里声明了 13 个实验，其中这 6 个在
`lqms.measure.tuners.sq_nodes` 里**没有同名函数**：

```
allxy, drag_calibrate, pulsed_spec, ramsey, single_shot, swap
```

（`sq_nodes` 实际导出 20 个可调用对象，包含 `ramsey_df` / `ramsey_t2` 等。）
前端会把这些名字列出来，但真正下发时按名字取函数会失败。写权限打开之前值得
先对一遍映射。

---

## 7. 关于沙箱与权限（本机特有）

部署过程中撞到两个环境层面的坑，都已处理，重跑时可直接照做：

1. **`D:\qmclaw` 缺少必要权限** —— 最初所有命令都报
   `grantWrite(D:\qmclaw)` / `SetNamedSecurityInfoW failed (Win32 5)`，
   即连列目录都失败。已用 `diagnose-windows-sandbox-acl.ps1` 检查并修复
   （只给当前用户在 `D:\qmclaw` 上补了一条完全控制项，文件内容与所有者未动；
   备份与回滚命令在 `D:\qmclaw-acl-recovery\`）。
2. **受限 shell 里子进程是低完整性令牌** —— 这类进程能读、能连网，但**写不进
   工作区里已存在的子目录**（`git` 报 `Permission denied`、`npm` 报
   `EPERM mkdir`、`python` 报 `WinError 5`）。所以 `deploy-local.ps1`
   **必须在普通（非受限）shell 里运行**。
3. **执行策略**：本机 `LocalMachine` 为 `RemoteSigned`，而 `deploy-local.ps1`
   没有数字签名，直接 `.\deploy-local.ps1` 会被拒。用 `deploy-local.cmd`
   （内部是 `powershell -ExecutionPolicy Bypass -File ...`，只对这一次进程生效，
   不改机器设置）。

> `deploy-local.ps1` 内容**刻意全部使用 ASCII**：本机是 Windows PowerShell 5.1，
> 对没有 UTF-8 BOM 的 `.ps1` 会按 ANSI 代码页解码，脚本里写中文会直接导致语法错误。

---

## 8. 复现命令

```powershell
# 完整部署（已装好的步骤会自动跳过）
D:\qmclaw\deploy-local.cmd

# 只补 vendor、只做校验
D:\qmclaw\deploy-local.cmd -SkipNode -SkipPython
D:\qmclaw\deploy-local.cmd -SkipVendor -SkipNode -SkipPython

# 日志与依赖清单
D:\qmclaw\.deploy-logs\
```

---

## 9. LabRAD 真机对接（本机，只读）

### 9.1 结果：连接层零改动即可用

本机 LabRAD manager 已在运行（PID 3188，监听 `0.0.0.0:7682`），
`config/system.json` 里的 `lqcs.connection` 本来就是 `localhost:7682`，
`~/.lqcs`（`C:\Users\yaqia\.lqcs`）也已在位，因此**不需要改任何连接配置**。

实测（`docs/deploy_diff_tools/check_labrad_link.py`，全部 PASS）：

| 检查项 | 结果 |
|---|---|
| 连接参数 | `localhost:7682`，来自 `system.json:lqcs.connection` |
| TCP 可达 | PASS |
| `~/.lqcs` | PASS，`ip=192.0.2.11` `ray_head=192.0.2.11:6479` |
| TEMP 可写 + `lqms`/`sq_nodes` 导入 | PASS |
| `labrad.connect` | PASS，`ID=1000000564` |
| 写闸门 | 两道都是 `False`（预期） |

再跑一次量子服务启动时真正会走的全流程
（`docs/deploy_diff_tools/check_backend_initialize.py`）：

```
[LQCS Backend] LabRAD connected! Connection ID: 1000000569
[LQCS Backend] setWiringInfo done
[LQCS Backend] switchSession done
initialize() -> True   (8.1s)      status: READY
qubits        : 128 个
experiments   : 20 个
session path  : ['', 'LQHL', 'test', '20251103_LD_SQ']
```

### 9.2 唯一改动的配置：session 路径

本机 DataVault 的真实层级是 `/LQHL/test/<样本名>/`（根下只有
`GHzDAC Calibration` 与 `LQHL` 两项）。原先 `session.json` 填的是
`test/20260324`（当初为离线数据设的），而**测控机注册表里当前挂载的样本是
`test/20251103_LD_SQ`** —— lqms 自己打印的 `Sample Path` 就是后者，两者错位。

已按"以机器实际挂载为准"统一（两处都改了）：

| 文件 | 原值 | 现值 |
|---|---|---|
| `config/session.json` → `session.path` | `["test","20260324"]` | `["test","20251103_LD_SQ"]` |
| `config/system.json` → `lqcs.session.default_path` | `["test","20260324"]` | `["test","20251103_LD_SQ"]` |

改完复验，三处已一致：

```
session.json           : ['test', '20251103_LD_SQ']
Registry Sample Path   : ['test', '20251103_LD_SQ']
Data Vault directory   : ['', 'LQHL', 'test', '20251103_LD_SQ']
```

> `/LQHL/test/` 下另有 18 个会话（`20251005`、`20251103`、`20251126_ld23`、
> `20251203`、`20260324`、`CZ_q2ld2_q3ld2` 等）。要切到别的样本，改这两处即可，
> 或运行时 `POST /sessions/switch`。
>
> ⚠️ **此处的取值后来又被改成 `20261016`**，见 §10。

### 9.3 新增/修正的诊断脚本

| 脚本 | 作用 |
|---|---|
| `docs/deploy_diff_tools/check_labrad_link.py` | **已修**：原先写死 `D:\semester4\QMClaw\...`，现按 `__file__` 推导（可用 `QMCLAW_SERVER_ROOT` 或第一个参数覆盖） |
| `docs/deploy_diff_tools/list_datavault.py` | **新增**：只读列出 DataVault 目录树，用来挑 session 路径 |
| `docs/deploy_diff_tools/check_backend_initialize.py` | **新增**：只读跑一遍 `LQCSBackend.initialize()`，report qubit/experiment 数量，不下载实验、不改参数 |

跑法（LabRAD 相关的都需要**非受限 shell**，原因见 §7 第 2 条）：

```powershell
$PY = "D:\anaconda3\envs\lqcs\python.exe"
& $PY docs\deploy_diff_tools\check_labrad_link.py
& $PY docs\deploy_diff_tools\list_datavault.py 3
& $PY docs\deploy_diff_tools\check_backend_initialize.py
```

### 9.4 服务起来后的只读验收

```powershell
& $PY "Agentic Workflow\qmclaw-server\scripts\check_readonly_regression.py"
```

期望：`/health` 的 `data_source` 为 `labrad`、`actuation_allowed: false`、
`/execute` 返回 `read_only`。（该脚本需要 `quantum:3003` 与 `analysis:3004`
已在运行，按约定 3 由人启动。）

> ⚠️ 写闸门已在 §10.3 打开，因此 `actuation_allowed` 现在是 `true`、
> `/execute` 不再返回 `read_only` —— 上面这条期望已过期，只适用于只读阶段。

---

## 10. 新建可写会话 20261016（2026-10-16）

### 10.1 目的

在 `test` 下新建一个当天日期的会话目录，把 `20260324` 的数据整体复制过去，
作为**可写**的工作会话，避免在原始数据目录上直接下实验。

### 10.2 数据复制

| 项 | 值 |
|---|---|
| 源 | `D:\DataVault\LQHL.dir\test.dir\20260324.dir` |
| 目标 | `D:\DataVault\LQHL.dir\test.dir\20261016.dir`（LabRAD 路径 `/LQHL/test/20261016`） |
| 内容 | 2,635 项 / 121.1 MB / 2,634 个 hdf5 |
| 一致性 | 条目数与总字节**逐项一致**；`session.ini` 的 `counter = 2690` 与 tags 一并保留 |
| 命令 | `robocopy <src> <dst> /E /COPY:DAT /R:1 /W:1`（执行前确认目标不存在，存在则中止不覆盖） |
| 回退 | 删除 `20261016.dir` 即可，不影响源目录 |

**数据集 ID 不会冲突**：ID 是**目录内唯一**的，`20251103_LD_SQ.dir`（`counter=19`）
与 `20260324.dir`（`counter=2690`）本就各自从 `00001` 开始编号，计数器存在各自目录的
`session.ini` 里，所以整目录复制不会与原件撞号，新目录里新建数据集会从 `02691` 起。

### 10.3 写闸门（可审计事件）

| 项 | 值 |
|---|---|
| 时间 | **2026-10-16 11:50** |
| 操作人 | `yaqia @ CASIA`（用户明确指示，agent 执行） |
| 打开范围 | `config/fallback_config.json` → `quantum_service.actuation.allow_execute` = `true`、`allow_set_params` = `true` |
| 未打开 | `analysis_service.actuation` 两项仍为 `false` |
| 生效方式 | **需重启 quantum_service**（不热重载） |
| **生效范围** | **全局** —— 检查点只有 `quantum_service/server.py:879`（`/execute`）与 `:1365`（`/qubit/set_params`），**不带 session / 目录维度**；任何 session 都变成可写。"只对新目录开权限"在现有实现里做不到 |
| 关闭方式 | 改回 `false`，或设 `QMCLAW_ALLOW_EXECUTE=0` / `QMCLAW_ALLOW_SET_PARAMS=0`，重启服务 |
| 预计关闭 | **未定，用完请立即改回并重启** |

### 10.4 必须改代码：`session.json` 不足以决定数据落点

实测 `initialize()` **全程没有把 DataVault 切到 `session_path`**，只把它传给
`dc.DataLab`；而 lqcs 的 `switchSession` 会把 DataVault 当前目录设成
**注册表里挂载的样本**。因此只改 `session.json`，数据仍会落到注册表样本目录。

改动点（两处，逻辑相同，都在 `switchSession` **之后**、构造 `DataLab` **之前**）：

| 文件 | 位置 |
|---|---|
| `services/quantum_service/labrad_client.py` | 微服务模式（`quantum_service` 用的就是它） |
| `backends/lqcs/__init__.py` | legacy 模式（`scripts/job_runner.py` 用的） |

```python
_dv = self._cxn.data_vault
_dv.cd("")            # DataVault 的 cd 是相对当前目录的，先回根
_clean = session_path[1:] if session_path and session_path[0] == "" else session_path
if _clean:
    _dv.cd(_clean)    # 覆盖 lqcs 注册表设的样本目录
```

失败只告警不中断（`WARNING: DataVault cd to ... failed`）。

### 10.5 验证结果

```
session.json  : {'user': 'LQHL', 'path': ['test', '20261016']}
Sample Path is ['test', '20251103_LD_SQ']                     <- lqcs 设的（旧）
[LQCS Backend] DataVault cd -> ['LQHL', 'test', '20261016']   <- 补的，覆盖上面
DataVault now : dirs=[] datasets=2634
  first/last  : '00001 - q1ld4: S21' ... '02689 - q2ld4: SpectroscopyAuto'
initialize() -> True   (7.4s)   status: READY   128 qubits / 20 experiments
```

即：LabRAD 能看到新目录，2634 个数据集可读，写入目标已切到 `20261016`。

### 10.6 注意：样本（sample）≠ DataVault 目录

qubit 对象仍来自 lqcs 注册表挂载的样本 `test/20251103_LD_SQ`，本次只改了
**数据落点**。若要让"测什么"也换成新样本，那是 lqcs 侧的 sample 注册表操作，
不是 `session.json` 能决定的 —— 需要时另行确认。


