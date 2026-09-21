# QMClaw 本机运行 TODO List

> 目标：让 `D:\semester4\QMClaw` 的代码在**本机**跑起来。
> 所有"缺失"结论都在本机实测得出，不是推断。每条都带**证据**和**验收标准**。
> 关联：`docs/RUNTIME_DEPS.md`（依赖清单）、`docs/DEPLOY_DIFF.md`（与 deploy 包的差异）。

---

## 阶段 A 执行结果（P0 已全部完成）

| 步骤 | 状态 | 实测结果 |
|---|---|---|
| A1 拷贝 vendor | 完成 | 三个子模块落位；修掉了多套一层的结构问题（见"坑 1"） |
| A2 修 qca 硬编码路径 | 完成 | `server.py:40` 用 `parents[3]`、`lab_tool.py:19` 用 `parents[5]`，均指向仓库根，找不到时 `raise` |
| A3 拷离线数据 | 完成 | `data\offline_data\` 3 个 `.dir`，5,011 文件 |
| A4 生成索引 | 完成 | 修掉生成器/读取器目录不一致的 bug（见"坑 2"）；`load()` 确认从索引文件读到 5008 datasets / 111 qubits |
| A5 创建 `.env` | 完成 | `PYTHON_BIN` / `PORT=3002` / `PLOTS_DIR` / `QMCLAW_USE_SERVICES=true` 已填；LLM Key 仍为空占位，待填 |
| A6 npm install | 完成 | web +90 包、server +106 包 |
| A7 editable 重装 | 完成 | 4 个 editable 全部指向 `D:\semester4\QMClaw\...`；15/15 模块在裸解释器下可解析；`qubitclient` 0.6.21 → **0.6.22** |
| A8 验收 | 完成 | 微服务 **10/10 可导入**；hermes `AIAgent` 已绑定；qca vendor 路径正确 |

**P0 验收结论：接线完成。** 但**尚未启动任何服务**（按约定由人操作）。

---

## 阶段 B 执行结果（启动就绪审核 + 准备工作）

对"能否启动"做了超出 import 层面的静态审核（服务 `before_start`、Express 路由、TS 类型检查、环境变量清单），
并完成了所有不依赖用户决策的准备工作。

### 已完成

| 项 | 内容 |
|---|---|
| B-1（原 P2-7） | **删掉 `llm_service/server.py:55-57` 的 API Key 明文日志**，改为只报长度（`_mask()`）。全仓库已无其它明文打印 Key 的位置 |
| B-2（原 P4-14） | `labrad_client.py:19` 与 `analysis_service/server.py:138` 的 `return Path("D:/QMClaw")` 兜底改为 `raise RuntimeError`。实测两处向上搜索仍成功，未触发 raise |
| B-3（原 P4-15） | 两个 `run-dev.cmd` 改为**优先 AutoClaw、否则用 PATH 上的 node**，找不到 node 时报错退出（本机 node 在 `C:\Program Files\nodejs`） |
| B-4（原 P4-16） | `test_offline.py:65` 改为相对 `__file__` 解析 `data/offline_data` 并带缺失提示；`test_load_qubits.py:39,45` 输出改到脚本同目录。实测 `test_offline.py` 现在能直接跑通 |
| B-5（原 P4-17） | `qubitclient_service/requirements.txt` 的 `-e` 路径由 3 级改为 **4 级**（`../../../../vendor/QubitClient`），实测解析到 `D:\semester4\QMClaw\vendor\QubitClient` 且含 `pyproject.toml`；同时修了 `server.py:152` 的误导性报错提示 |
| B-6（原 P1-4） | 创建 `config/qubitclient.json`（按 `qubitclient.json.example` 结构）。实测服务能加载，`initialized=True`。**`api_key` 仍是 `REPLACE_ME`，待填**。该文件已在 `.gitignore` 中 |

### 审核新发现

**N1 — 微服务模式下 Express 不会启动 Python 服务。**
`src/index.ts:546-550`：`if (USE_MICROSERVICES) return Promise.reject("Subprocess not available in microservices mode")`。
而 `services.json` 是 `use_microservices: true` → **10 个 Python 服务必须手工启动**。
另：`/server/start` 按钮走 `scripts/start_services.py`，那是 **LabRAD/Ray/DeviceManager/Uwave/DataStore**
（`[1/5]`…`[5/5]`）这套测控基础设施，**不是**微服务，两者易混淆。

**N2 — 单独启动服务不会加载 `.env`。**
全仓库只有 3 处 `load_dotenv`：`start_all_services.py:30`（`qmclaw-server/.env` ✅）、
`hermes_service/server.py:78`（自己算路径 ✅）、`qca_service/server.py:65`（`VENDOR_QCA_DIR/.env` ❌ 该文件不存在）。
`llm` / `agent` / `qubitclient` 都靠 `services/common/config.py:33-37` 读 `os.environ`，**自己不加载 .env**。
→ 必须走 `start_all_services.py` 启动，否则 API Key 传不进去。

**N3 — 生产构建不可用，只有 dev 能跑。**
`npx tsc --noEmit` 实测：qmclaw-server **24 个类型错误**、qmclaw-web **6 个类型错误**。
`npm run dev`（tsx / next dev）不做类型检查 → 能跑；`npm run build` + `npm start` → 失败。
属仓库既有问题，非本次引入。

**N4 — 好消息：所有 `before_start` 都是优雅降级，没有硬失败。**
`quantum`/`analysis`（LabRAD 失败→降级）、`llm`（缺 Key→WARNING）、`qubitclient`（缺配置→WARNING）、
`hermes`（缺 AIAgent→WARNING）。`base_service.py:262-270` 只有 `before_start` 抛异常才 `sys.exit(1)`，没人抛。
另核对：**9 个 config JSON 全部合法**，**9 个服务脚本路径全部存在**。

**N5 — 环境变量清单（代码实际读取的全部）**
`ANTHROPIC_API_KEY` / `BACKEND_DIR` / `DEEPSEEK_API_KEY` / `FEISHU_WEBHOOK_URL` / `HOST` /
`IMAGE_CLASSIFIER_DIR` / `LABRAD_BAT` / `MEASURE_SCRIPTS` / `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` /
`MINIMAX_GROUP_ID` / `NEXT_PUBLIC_API_URL` / `OPENAI_API_KEY` / `PLOTS_DIR` / `PORT` / `PYTHON_BIN` /
`QCA_MODEL` / `QMCLAW_CONFIG_DIR` / `QMCLAW_PLOTS_DIR` / `QMCLAW_USE_SERVICES` / `SDL_VIDEODRIVER` / `TEMP`。
前端只需 `NEXT_PUBLIC_API_URL`（默认 `http://localhost:3002`，**不配也能跑**）。
**必填的只有 4 个**：`PYTHON_BIN`（已填）、`PORT`（已填）、`QMCLAW_USE_SERVICES`（已填）、**至少一个 LLM Key（未填）**。

**N6 — 原 P2-8「修 pip 依赖冲突」是个死结，已放弃并回退。**
实测这不是 2 处宽松冲突，而是**三方互斥**：

| 包 | 要求 |
|---|---|
| `hermes-agent 0.21.0` | `openai==2.24.0`、`rich==14.3.3`（**34 条全部精确 pin**，源码注释明确说这是刻意设计） |
| `langchain-openai 1.2.0` | `openai>=2.26.0,<3` |
| `qca 0.1.0` | `rich==14.3.4` |

试过升到 `openai 2.54.0` + `rich 14.3.4`：`langchain-openai`/`qca` 满足了，
但 `hermes-agent` 的两条精确 pin 被打破。**三者无法同时满足**。
已**回退到 deploy 快照的原始组合**（`openai 2.24.0` + `rich 14.3.3`）——
这是上一台机器实际跑通过的已知可用状态，擅自改组合的风险大于收益。
`pip check` 恢复为原来的 2 条宽松提示。**此项标记为"接受现状"，不再尝试修复**（除非上游放宽 pin）。

---

## 启动 Runbook

```powershell
$PY  = "D:\semester4\deploy\qmclaw\qmclaw\python.exe"
$SRV = "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"

# ①（仅在线模式）LabRAD —— 人工操作 GUI
& $PY -c "from lqcs.servers_control import run_server_control; run_server_control()"

# ② 10 个 Python 微服务（前台，看日志）—— 必须走这个脚本，否则 .env 不会加载（见 N2）
cd "$SRV\scripts"; & $PY start_all_services.py --all

# ③ Express 网关 :3002   （或直接双击 run-dev.cmd）
cd $SRV; npm run dev

# ④ 前端 :8081           （或直接双击 run-dev.cmd）
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-web"; npm run dev

# ⑤ 浏览器 http://localhost:8081
```

**端口总表**：3003 quantum｜3004 analysis｜3005 agent｜3006 llm｜3007 image｜3008 workflow｜
3009 task_queue｜**3010 qubitclient**｜3011 qca｜3012 hermes｜**3002 Express**｜**8081 前端**｜7682 LabRAD

> `qubitclient:3010` 在 `services.json` 里没有，但 `start_all_services.py` 会起它。
> 更省事的替代路径：`.env` 里设 `QMCLAW_USE_SERVICES=false` → Express 自己 spawn `scripts/job_runner.py`，
> 只需启 ③④ 两步（legacy 单体模式）。

---

### 阶段 A 踩到并修掉的 3 个坑

**坑 1 — `Copy-Item -Recurse` 在目标目录已存在时会多套一层。**
`Copy-Item -Recurse src dst` 若 `dst` 已存在（哪怕为空），会生成 `dst\src名\`。
本次三个 vendor 目录都变成了 `vendor\hermes-agent\hermes-agent\` 这种双层结构，
这正是 A7 `pip install -e` 报"找不到 setup.py / pyproject.toml"的真正原因——文件都在深一层。
现已上提一级，三个目录与 `deploy\vendor` 基准逐项对齐。

> 上提过程中误删了 `vendor\QubitClient\qubitclient\`（313 项），已从 `deploy\vendor` 完整恢复并核对通过。
> 教训：`Move-Item` 之后不要无条件 `Remove-Item` 内层目录，要先确认移动是否全部成功。

**坑 2 — 离线索引生成器写的目录，读取器根本不读。**
`offline_data_provider.py:424` 原本是 `output_path = Path(data_path).parent / "offline"`，
即 `--data-path data/offline_data` 会把索引写到 `data\offline\`；
而读取端 `OfflineDataProvider.load()`（第 84-86 行）读的是 `<data_path>/qubits.json`，
也就是 `data\offline_data\`。两边对不上，生成的索引永远不会被加载。
已把第 424 行改成 `output_path = Path(data_path)`，并在同处加了注释说明原因。

**坑 3 — vendor 里的悬空 `.git` 指针会让整个仓库的 git 命令失败。**
`deploy\vendor\*\` 各有一个 `.git` 文本文件写着 `gitdir: ../../.git/modules/vendor/<name>`，
但 `.git/modules` 在本仓库不存在。vendor 为空时 git 静默忽略；
一旦填入文件，`git status` 直接 `fatal: not a git repository`（exit 128）。
已删除这三个悬空指针文件，git 恢复（`git log` 始终正常，只有 `status` 受影响）。

---

## 已决策事项（2026-09-21）

### D1 — LLM Key：用 DeepSeek

`.env` 已写入 `DEEPSEEK_API_KEY`（实测 `services/common/config.py` 读到 `SET(len=35)`）。
其余三家留空。`model_configs.json` 里 DeepSeek 那条 `deepseek-chat` 即可用。

### D2 — 运行模式：先跑离线

`fallback_config.json` 两个服务的 `default_mode` 本来就是 `"offline"`，**无需改动**。
实测 `before_start()`（真实启动路径，非仅 import）：

```
[quantum_service]  Offline data loaded: 5008 datasets, 111 qubits
[quantum_service]  Mode: offline
[quantum_service]  Offline mode: skipping LabRAD connection
[analysis_service] Offline data loaded: 5008 datasets
[analysis_service] Mode: offline
[analysis_service] Offline mode: skipping LabRAD connection
```

→ 不需要 LabRAD、不需要硬件，10 个服务可直接起。要切回在线时改 `default_mode` 为 `"auto"` 或
调 `POST /mode {"mode":"online"}`。

### D3 — session 路径：统一为 `20260324`

`config/session.json` 的 `path` 已由 `["test","20260822_device1"]` 改为 **`["test","20260324"]`**。

理由：
- `20260324` 同时存在于 `system.json` 的 `lqcs.session.default_path` 和 `data/offline_data/`，三处归一
- `20260822_device1` 在离线数据里不存在。虽然它在 `test_load_qubits.py:10` 里也出现（说明它曾是
  上一台机器上的真实 session），但**离线运行必须指向有数据的 session**
- 若日后接入在线 LabRAD 且该 session 确实存在，改回来即可（单行 JSON）

### D4 — `lqms` 版本：**保持 0.3.1，不升级到 0.3.3**

理由（与 D5 同一原则：不擅自偏离已验证状态）：
- 环境快照 `qmclaw.zip` 里装的就是 **0.3.1**，那是上一台机器实际跑通过的状态
- 项目里 30+ 处 `from lqms...` 已在本机用 0.3.1 逐条验证可导入，`before_start()` 实测通过
- 0.3.3 是新版本，API 差异未知，升级属于**无法验证的变更**，收益不明而风险实在
- `deploy\lqms-0.3.3-cp311-cp311-win_amd64.whl` 保留在原地，将来确有需要再升

> `qmclaw-server\python\requirements.txt:16` 原本写的是不可安装的占位 URL
> （`lqms @ git+https://github.com/...@main#egg=lqms`），已改为说明性注释，避免误导。

### D5 — vendor 与 git 子模块：**方案 A，保持现状**

即：vendor 只作为**本地运行依赖**，不入库，靠 `deploy\vendor` 分发。

理由：
- 本机 hosts 屏蔽了 GitHub（`127.0.0.1 github.com #S302`），方案 B/C 解决不了"换机器"的问题，
  而 `deploy\vendor` 已经能完整提供内容（实测与基准逐项对齐）
- 方案 B（去子模块化入库）会让仓库体积 +200 MB，且**改变仓库结构属于维护者决策**，
  不该由我单方面做了
- 方案 C（本地 submodule 源）需要为三个 vendor 各建 git 仓库并提交历史，工作量与收益不成比例
- 对"跑起来"这个目标，方案 A 零改动即已成立

**代价与缓解**：新机器 clone 后 vendor 仍为空，必须手工填充。填充步骤见下方"vendor 填充"。

#### vendor 填充（可复现）

```powershell
$DST = "D:\semester4\QMClaw\vendor"
# 注意：目标目录已存在时必须先删，否则 Copy-Item -Recurse 会多套一层（见"坑 1"）
foreach ($n in @("hermes-agent","QubitClient","Quantum-Calibration-Agent-Blueprint")) {
  Remove-Item "$DST\$n" -Recurse -Force -ErrorAction SilentlyContinue
  Copy-Item -Recurse -Force "D:\semester4\deploy\vendor\$n" "$DST\$n"
}
# 删掉悬空 .git 指针，否则 git status 会 fatal（见"坑 3"）
foreach ($n in @("hermes-agent","QubitClient","Quantum-Calibration-Agent-Blueprint")) {
  Remove-Item "$DST\$n\.git" -Force -ErrorAction SilentlyContinue
}
# 重建 editable 安装（指向新路径）
$PY = "D:\semester4\deploy\qmclaw\qmclaw\python.exe"
foreach ($n in @("QubitClient","Quantum-Calibration-Agent-Blueprint","hermes-agent")) {
  & $PY -m pip install -e "$DST\$n" --no-deps
}
```

**校验**：三个目录的递归项数应为 `13710 / 921 / 415`（比基准各多 7 个 `*.egg-info` 是正常的）。

---

## 结构性问题：vendor 与 git 子模块（已按 D5 决定：保持现状）

这是本次暴露出的、比单个 TODO 更重要的一个问题：

1. `.gitmodules` 声明的三个子模块从未初始化，`.git/modules/` 不存在，没有任何子模块的 git 对象。
2. 本机 hosts 文件屏蔽了 GitHub（`127.0.0.1 github.com ... #S302`，共 120+ 条，含 github / huggingface / docker）。
   因此 **`git submodule update --init --recursive` 在本机永远不可能成功**——`deploy\vendor` 是唯一来源。
3. `vendor/` 里的内容对 git 不可见：这三个路径在 index 里是 gitlink（mode 160000），
   往里面放文件后 `git status -uall` 完全不报告。也就是说：
   - 这些文件不会被提交，换台机器或重新 clone 后 vendor 依然是空的
   - `git status` 显示"干净"是假象，容易误判

   实测：
   ```
   $ git status --porcelain -uall
    M "Agentic Workflow/qmclaw-server/services/common/offline_data_provider.py"
    M "Agentic Workflow/qmclaw-server/services/qca_service/server.py"
    M "Agentic Workflow/qmclaw-server/services/qca_service/tools/lab_tool.py"
   ?? docs/...                       <- vendor\ 下 15,000+ 个文件一个都没列出
   ```

**三个可选方向**（需要定，没有擅自改仓库结构）：

| 方案 | 做法 | 代价 |
|---|---|---|
| 保持现状 | vendor 只作为本地运行依赖，不入库；依赖 `deploy\vendor` 分发 | 每次换机器都要手工拷；clone 出来跑不了 |
| 去子模块化，直接入库 | `git rm --cached vendor/*` + 删 `.gitmodules` + 正常 `git add vendor/` | 仓库体积 +200 MB（hermes-agent 占 172 MB）；但 clone 即可用 |
| 改用本地 submodule 源 | 把 `deploy\vendor\*` 作为本地 git 仓库，`.gitmodules` 的 url 指向本地路径 | 需要为三个 vendor 各建一个 git 仓库并提交历史 |

---

## 本机现状快照（阶段 A 完成后，实测）

| 项 | 状态 |
|---|---|
| Python 解释器 | `D:\semester4\deploy\qmclaw\qmclaw\python.exe` — 3.11.16，249 包 |
| 环境自检 | `pip check` 仍有 2 处宽松冲突（P1-10 未做）；微服务 **10/10 可导入** |
| `QMClaw\vendor\` | 三个目录齐全，与 `deploy\vendor` 逐项对齐（各多 7 个 pip 生成的 `*.egg-info`） |
| `qmclaw-server\data\offline_data` | 5,011 文件 + 3 个索引 JSON |
| `qmclaw-server\.env` | 已建，**LLM Key 待填** |
| `node_modules`×2 | 均已安装 |
| `config\qubitclient.json` | 仍不存在（P1-8） |
| 端口监听 | 7682 / 3002–3012 / 8081 **全部空闲** |
| git 工作区 | 可用；3 个已跟踪文件被修改 + `docs/` 新增 |

**仍未闭环的**：P1（8/9/10/11）、P2（12–16）、P3（17–23）。

---

## P0 — 阻断项（已全部完成，保留原始记录备查）

### [完成] 1. 把 vendor 三个子模块拷进项目

**证据**：`QMClaw\vendor\{hermes-agent,QubitClient,Quantum-Calibration-Agent-Blueprint}` 递归计数均为 **0**。
实测把 `deploy\vendor` 的两个目录加进 `sys.path` 后，项目导入从 **30/91 → 39/91**，所有 vendor 模块恢复。
另：`hermes_service` 探测 `vendor/hermes-agent` 时**空目录也算存在**，所以它会打印 `Added hermes-agent to sys.path` 却仍然 `ModuleNotFoundError: No module named 'run_agent'`。

> 注意：不要直接 `Copy-Item -Recurse src dst` 到已存在的空目录（会踩坑 1）。
> 正确做法先删空目录，或用 `Copy-Item -Path "$src\*" -Destination $dst -Recurse -Force`。

**验收**：`vendor\QubitClient\qubitclient\__init__.py` 里 `__version__ == "0.6.22"`；
`import run_agent`、`import core.workflow_validation` 均成功。

---

### [完成] 2. 改 qca_service 里 2 处写死的绝对路径

**证据**：`qca` 服务是 10 个里唯一 import 失败的，实测日志直接暴露原因：

```
[QCA] Vendor dir: D:\Documents\QMClaw\vendor\Quantum-Calibration-Agent-Blueprint
  FAIL  qca :3011  ModuleNotFoundError: No module named 'core.workflow_validation'
```

| 文件 | 行 | 现值 |
|---|---|---|
| `services\qca_service\server.py` | 38 → **40** | `VENDOR_QCA_DIR = _SERVICE_DIR.parents[3] / "vendor" / "Quantum-Calibration-Agent-Blueprint"` |
| `services\qca_service\tools\lab_tool.py` | 17 → **19** | `VENDOR_DIR = Path(__file__).resolve().parents[5] / "vendor" / "..."` |

两处都加了 `if not VENDOR_QCA_DIR.exists(): raise RuntimeError(...)`，路径找不到时立刻暴露。

**验收**：`& $PY -B docs\deploy_diff_tools\check_services.py` → **10 / 10 可导入**。

---

### [完成] 3. 拷离线数据到 qmclaw-server\data\offline_data

**证据**：`config\fallback_config.json` 的 `data_path: "data/offline_data"`，代码按
`Path(__file__).parent.parent.parent / data_path` 解析（`quantum_service\server.py:227`、`analysis_service\server.py:283`）
→ 期望 `qmclaw-server\data\offline_data`。

**验收**：`$SRV\data\offline_data` 下有 3 个 `.dir` 目录、共 5,011 个文件。

---

### [完成] 4. 生成离线数据索引

**证据**：`deploy\offline_data` 不含任何 `.json` 索引文件。`OfflineDataProvider.load()` 在找不到
`qubits.json` + `datasets.json` 时会退化为扫描 5,011 个文件。

> 这一步暴露了坑 2：生成器默认写 `data_path/../offline`，读取器读 `data_path/`。
> 已修 `offline_data_provider.py:424`。

```powershell
$PY  = "D:\semester4\deploy\qmclaw\qmclaw\python.exe"
$SRV = "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"
cd $SRV
& $PY -m services.common.offline_data_provider --generate-index
```

**验收**：`data\offline_data\` 下有 `qubits.json` / `datasets.json` / `experiments.json`，
且 `OfflineDataProvider('data/offline_data').load()` 打印 `Loaded 5008 datasets, 111 qubits from index files`。

> 5,011 − 5,008 = 3，差的 3 个是各日期目录里的 `session.ini`，不是 HDF5，**属正常**。

---

### [完成] 5. 创建 qmclaw-server\.env

**证据**：Express 端 `src\index.ts:41` 读 `path.join(__dirname, "..", ".env")`＝`qmclaw-server\.env`；
Hermes 端 `hermes_service\server.py:76` 读同一位置。

当前内容（`PYTHON_BIN` / `PORT` / `PLOTS_DIR` / `QMCLAW_USE_SERVICES` 已填，**Key 待填**）：

```ini
MINIMAX_API_KEY=
MINIMAX_GROUP_ID=
DEEPSEEK_API_KEY=
PYTHON_BIN=D:\semester4\deploy\qmclaw\qmclaw\python.exe
PLOTS_DIR=D:\semester4\QMClaw\Agentic Workflow\qmclaw-web\public\plots
PORT=3002
QMCLAW_USE_SERVICES=true
```

> `PORT` 必须显式设成 3002：`src\index.ts:181` 默认值是 `8080`，但前端
> `src\services\apiClients.ts:7` 默认连 `http://localhost:3002`。不设会连不上。

---

### [完成] 6. 安装前端 Node 依赖

```powershell
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-web"; npm install
```

依赖：`next 14.2.35`、`react/react-dom 18.2.0`、`@xyflow/react ^12.11.3`、`recharts ^3.0.0`、
`socket.io-client ^4.6.0`、`zustand ^4.5.7`、`typescript ^5.3.0`。实测 +90 包。

**验收**：`npm run dev` 起在 **8081**（`package.json:8` 是 `next dev -p 8081`，不是 CLAUDE.md 写的 3001）。

---

### [完成] 7. 安装后端 Node 依赖

```powershell
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"; npm install
```

依赖：`express ^4.18.2`、`cors ^2.8.6`、`dotenv ^16.6.1`、`socket.io ^4.6.0`、
`tsx ^4.7.0`、`typescript ^5.3.0`。实测 +106 包。

---

## P1 — 功能完整性问题（未开始）

### [完成] 8. 创建 config\qubitclient.json

**证据**：`qubitclient_service\server.py` 按 4 个位置找配置，**实测 4 个全不存在**：

| 候选位置 | 存在？ |
|---|---|
| `qmclaw-server\services\qubitclient_service\config\qubitclient.json` | 否 |
| `qmclaw-server\config\qubitclient.json` | 否 |
| `vendor\QubitClient\qubitclient.json` | 否 |
| `.\qubitclient.json`（cwd） | 否 |

只有模板存在：`D:\semester4\QMClaw\vendor\QubitClient\qubitclient.json.example`。

**做**：拷模板到 `qmclaw-server\config\qubitclient.json` 并填入 LLM 配置。
**验收**：`qubitclient_service` 启动不再报 `No LLM configuration found`。

---

### [完成] 9. 统一 session 路径配置（当前三处不一致）

**证据**：

| 来源 | 值 |
|---|---|
| `config\session.json` → `session.path` | `test / 20260822_device1` |
| `config\system.json` → `lqcs.session.default_path` | `test / 20260324` |
| `deploy\offline_data\` 实际目录 | `20251005.dir` / `20251103.dir` / `20260324.dir` |

→ **`20260822_device1` 在离线数据里根本不存在**，切会话会失败。

**做**：确认 `20260822_device1` 是笔误还是确实有个没交付的 session。若离线运行，统一改成 `20260324`。

---

### [接受现状] 10. 修掉环境的 2 处强制依赖冲突

**证据**：`& $PY -m pip check` 实测输出：

```
langchain-openai 1.2.0 has requirement openai<3.0.0,>=2.26.0, but you have openai 2.24.0.
qca 0.1.0 has requirement rich==14.3.4, but you have rich 14.3.3.
```

不阻塞启动，但 `qca` 用的是 vendor 里 `pyproject.toml` 的精确 pin，建议对齐。

```powershell
& $PY -m pip install "openai>=2.26.0,<3" "rich==14.3.4"
& $PY -m pip check      # 期望：No broken requirements found.
```

---

### [已决策] 11. 确认 lqms 版本（0.3.3 还是 0.3.1）

**证据**：deploy 里提供的是 `lqms-0.3.3-cp311-cp311-win_amd64.whl`，但环境里装的是 **0.3.1**
（`direct_url.json` 显示来源是 `D:/Documents/xwechat_files/.../2025-11/lqms-0.3.1-...whl` 这个临时目录）。
两者不自洽。`lqms` 是核心运行时依赖（项目里 30+ 处 `from lqms...` 导入）。

```powershell
& $PY -m pip install --force-reinstall "D:\semester4\deploy\lqms-0.3.3-cp311-cp311-win_amd64.whl"
```

> 另注：`qmclaw-server\python\requirements.txt:16` 写的是 `lqms @ git+https://github.com/...@main#egg=lqms`，
> 是占位 URL（省略号），不可安装。

---

## P2 — 可选功能（未开始）

### [ ] 12. 装 python-docx（docx 报告导出）

**证据**：`measure_scripts\...\sq_workflow\backend.py:18,19,23` 有 `from docx import Document`；
实测 `find_spec("docx")` 为 `None`。vendor 里也有多处引用。

```powershell
& $PY -m pip install python-docx
```

### [ ] 13. 装 onnxruntime / torchvision（image_service 后端）

**证据**：`image_service\server.py` 引用；实测两者均 `find_spec` 为 `None`。
注意环境里的 torch 是 **CPU 版**（`2.14.0+cpu`），`torchvision` 必须匹配这个版本。

```powershell
& $PY -m pip install onnxruntime
# torchvision 需匹配 torch 2.14.0+cpu，装前先确认可用版本
```

### [ ] 14. 装 redis（task_queue 可选后端）

**证据**：`task_queue\server.py` 引用；实测缺失。**默认内存后端可用，非必需**。

```powershell
& $PY -m pip install redis
```

### [ ] 15. 装 swiftmcp（根 MCP 工具）

**证据**：`mcp_tools_new.py` 引用；实测缺失。根 `pyproject.toml` 里也已注释为可选。

```powershell
& $PY -m pip install swiftmcp
```

### [ ] 16.（可选）补 data\fallback\{quantum,analysis}\ 降级数据

**证据**：`config\fallback_config.json` 声明 `fallback_data_path: data/fallback/quantum` 与 `.../analysis`，
**两个目录都不存在**。`.claude\progress.md` 声称这些文件已完成，实际未随仓库交付。
`FallbackManager.get_fallback_data()` 找不到时只打印 `Fallback data not found`，**会优雅降级**，所以非阻断。

---

## P3 — 隐患与清理（未开始）

### [完成] 17. 修 2 处 `Path("D:/QMClaw")` 兜底路径

**证据**：两处的向上搜索**目前都能成功**（实测 `_get_qmclaw_root()` 返回 `D:\semester4\QMClaw`），
兜底分支**未被触发**——属潜在 bug，不是当前故障。

| 文件 | 行 |
|---|---|
| `services\quantum_service\labrad_client.py` | 30 |
| `services\analysis_service\server.py` | 148 |

**做**：兜底改成 `raise`（而不是返回一个不存在的旧路径），这样路径找不到时能立刻暴露。

### [完成] 18. 修 run-dev.cmd 里的 AutoClaw node 路径（2 个文件）

**证据**：两个 `run-dev.cmd` 第 3 行都是 `set "PATH=C:\Program Files\AutoClaw\resources\node;%PATH%"`，
本机 Node 实际在 `C:\Program Files\nodejs`。

| 文件 | 行 |
|---|---|
| `Agentic Workflow\qmclaw-server\run-dev.cmd` | 3 |
| `Agentic Workflow\qmclaw-web\run-dev.cmd` | 3 |

### [完成] 19. 修测试脚本里的旧路径

- `Agentic Workflow\qmclaw-server\test_offline.py:65` → `D:/Documents/QMClaw/Agentic Workflow/offline_data`
  （旧盘符 + 少了 `qmclaw-server` 一级，与运行时解析规则不符）
- `test_load_qubits.py:39,45` → `D:/QMClaw/test_qubit_info.json`

### [完成] 20. 修 qubitclient_service 的依赖声明

**证据**：`services\qubitclient_service\requirements.txt:2` 是 `-e ../../../vendor/QubitClient`，
按该文件所在目录解析会得到 `Agentic Workflow\vendor\QubitClient`，而真实位置是**仓库根**的
`QMClaw\vendor\QubitClient` —— 多退了一级。同目录 `server.py:152` 的报错提示
`pip install -e vendor/QubitClient` 同样指不到。

### [完成] 21. 清理失效的需求声明

| 文件 | 行 | 问题 |
|---|---|---|
| `lqcs_deploy\requirements.txt` | 12 | `cryptography==36.0.0` 与 `qmclaw-server\python\requirements.txt:12` 的 `pyopenssl>=24.0.0`（注释明说需 cryptography>=50.0.0）**互斥**，环境实际装的 50.0.0 |
| `Agentic Workflow\qmclaw-server\python\requirements.txt` | 22 | `envelopes-qc>=0.1.0`，但只交付 0.0.6，且全仓库**零引用** |
| `Agentic Workflow\qmclaw-server\python\requirements.txt` | 16 | `lqms @ git+...` 占位 URL，见第 11 项 |

### [ ] 22. 修文档里的旧路径（会让照着做的人走偏）

`Agentic Workflow\README.md:104,139,140,183,197,203`、`skills\qubit-calibration\SKILL.md:241`、
`skills\qubit-data-analysis\SKILL.md:228`、`CLAUDE.md:10` —— 分别指向
`D:\qmclaw`、`D:\Documents\QMClaw`、`C:\Program Files\AutoClaw\resources\node`、
`C:\Users\lqcs\...\Python311\python.exe`、`C:\Users\HUAWEI\.conda\envs\qmclaw\python.exe`
（**本机一个都不存在**）。

### [ ] 23. 明确不装 quanputing / quanputing_server

**证据**：两个轮子在 `deploy\` 里，但**未安装进环境、项目代码零引用**。
若安装会与现有环境产生 **15 处版本冲突 + 7 处缺失依赖**，还会引入第二套 Qt 绑定 PySide6（与 PyQt6 并存）。

**做**：确认不用后，建议从 `deploy\` 移除以免误装。

---

## 附：不构成缺失的项（已核实，避免误判）

| 看起来像缺失 | 实际情况 |
|---|---|
| `import protobuf` 失败 | 假阳性 —— `protobuf 6.33.6` 已装，正确导入是 `google.protobuf` |
| `import win32con` / `pylab` 失败 | 假阳性 —— 由 pywin32 的 `.pth` 和 site-packages 顶层 `pylab.py` 提供 |
| `import qtpy` / `socketio` | 全项目零引用，本就不需要 |
| `import sq` 失败 | `hermes_runner.py:362` 用 `try/except` 包着，失败只 `logger.warning` 并返回 False；主路径走 `lqms.measure.tuners.sq_nodes`，正常。`measure_scripts\...\sq_workflow\` 只有 `backend.py`，是实验室本地代码，仓库未交付 |
| `data\variants\` 空目录 | 跑 `check_services.py` 时被 `offline_variant_generator.py:103` 自动创建的副作用，已 gitignore，无害 |
| vendor 里各多 7 个 `*.egg-info` | `pip install -e` 生成的构建产物；PEP 660 安装不依赖它们，删留均可（`.gitignore` 已含 `*.egg-info/`） |
| `session.ini` 未被索引 | 不是 HDF5，正则本就不该匹配 |

---

## 建议执行顺序

```
P0: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7      已完成
P1: 8 -> 9 -> 10 -> 11                   （功能可用 + 消除矛盾）
P2: 12 -> 13 -> 14 -> 15 -> 16           （按需）
P3: 17 ... 23                            （清理，可延后）
```

**复验命令**：

```powershell
$PY  = "D:\semester4\deploy\qmclaw\qmclaw\python.exe"
$T   = "D:\semester4\QMClaw\docs\deploy_diff_tools"

& $PY -B "$T\check_services.py"        # 期望 10 / 10 可导入
& $PY -m pip check                     # 期望 No broken requirements found.（P1-10 做完后）
& $PY -B "$T\check_runtime_imports.py" "D:\semester4\QMClaw"
                                       # 不可解析项应只剩项目自身模块 + sq + 5 个可选包
```

---

## 需要决策的事项

1. **lqms 用 0.3.3 还是 0.3.1？**（第 11 项）
2. **`session.json` 的 `20260822_device1`** 是笔误还是漏交付的 session？（第 9 项）
3. **`envelopes-qc>=0.1.0`** 是笔误还是漏交付的 0.1.0？（第 21 项）
4. **vendor 与 git 子模块怎么处理？**（见上方"结构性问题"三选一）
5. **`.env` 里的 LLM API Key** 需要你填（第 5 项）。
