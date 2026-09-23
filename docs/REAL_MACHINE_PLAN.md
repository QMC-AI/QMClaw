# 接通真机：从离线切到远程测控机的设计方案

> **文档性质**：设计参考基线（尚未实施）。实施按第八节的提交顺序推进，每完成一项回来勾选。
> **编写依据**：对 `services/quantum_service/labrad_client.py`、`services/quantum_service/server.py`、
> `services/analysis_service/server.py`、`config/system.json`、`config/fallback_config.json`
> 的静态审阅，加上对已装 `lqcs`/`lqms`/`labrad` 包的只读探查。
> **相关文档**：`docs/CHANGES_VS_SOURCE.md`（已完成的能力）、`docs/RUNTIME_DEPS.md`（依赖与接线）、
> `docs/DEPLOY_DIFF.md`（deploy 包差异）、`docs/ADAPTER_GUIDE.md`（测控系统适配）。

## 实施状态

| 步骤 | 内容 | 状态 |
|---|---|---|
| 0 | 本方案落盘 | ✅ 已完成 |
| A | 连接参数外置（远程拓扑） | ⬜ 待办 |
| C | 修复离线↔在线切换语义 | ⬜ 待办 |
| B | 只读写闸门 | ⬜ 待办 |
| E | 无硬件验证（单元 / 协议 / 影子） | ⬜ 待办 |
| F | 自检脚本与文档 | ⬜ 待办 |

---

## 一、目标与成功标准

**目标**：让 QMClaw 在**不接硬件**的前提下具备"连上远程测控机并只读调控"的能力，且离线路径完全不受影响。
硬件可用后再单独打开写权限。

**本期成功标准（全部可在无硬件条件下验收）**

1. LabRAD 连接参数从配置读取（host/port/timeout 可指向远程测控机），不再是写死的 localhost。
2. 开启 `auto` 且真机可达时模式正确切到在线；真机不可达时干净回落到离线，离线行为与现在**逐字节一致**。
3. 写闸门关闭时，**即使已连上真机**，执行实验与改 qubit 参数都被服务端拒绝，并给出明确只读提示。
4. `/health` 与 `/status` 能明确回答："数据来自真机还是离线"、"是否允许写"。
5. 提供只读联调自检脚本，一条命令输出全部前置条件与失败原因。

**明确不在本期**：不打开写权限、不跑真机实验、不改离线模拟器、不升级 `lqms`。

**已确认的决策**（本轮与用户确认）

| 项 | 决策 |
|---|---|
| 拓扑 | 本机是**客户端**，通过局域网连**远程测控机**（不是本机托管 LabRAD） |
| 硬件现状 | **当前不可用**（检修/不在手边）→ 只能做无硬件验证 |
| 安全策略 | **先只读联调，禁止任何写操作** |
| 默认模式 | `default_mode: "auto"` |

---

## 二、现状盘点

### 2.1 已经具备的（比预期完整）

整套测控栈**已装好**，无需额外安装：

| 组件 | 版本 | 说明 |
|---|---|---|
| `pylabrad` | 0.98.7 | 提供顶层 `labrad` 包 |
| `lqms` | 0.3.1 | Cython 编译版（`.pyd`） |
| `lqcs` | 0.1.3 | 编译版，**自带 scalabrad `LabRADmanager` jar 与 `registry.sqlite`** |
| `ray` | 2.41.0 | 分布式执行 |
| Java | 1.8.0_471 | 仅当本机自托管 LabRAD manager 时才需要 |

关键模块**全部存在**（已核对 `lqms-0.3.1.dist-info/RECORD`）：

- `lqms/measure/tuners/sq_nodes.cp311-win_amd64.pyd` ← 实验模块（`sq.t1` 等）
- `lqms/pyle/workflow.cp311-win_amd64.pyd` ← `switchSession`
- `lqms/utils/save_path.cp311-win_amd64.pyd` ← `get_info_path`
- `lqms/measure/basic/basic_tuner`、`.../util` ← `BasicTuner` / `setWiringInfo`
- `lqms/data_process/dataAnalysisCore` ← `DataLab` / `InfoBase` / `QubitUpdater`
- `lqcs/system_config.cp311-win_amd64.pyd`、`lqcs/servers_control/start_server/*`、`lqcs/servers_control/serverControlQT`

代码侧：

- `services/quantum_service/labrad_client.py` 已完整实现在线握手：
  `connect → setWiringInfo → Ray → switchSession → DataLab → QubitUpdater → generate_qubit/coupler → BasicTuner → sq_nodes`
- `quantum_service` 在线执行路径已完整：构造 `exec` 上下文（`cxn/s/sq/data/qter/BasicTuner` + 全部 qubit 对象）后执行代码。
- `config/session.json` = `["", "LQHL", "test", "20260324"]`，与实验室自己的脚本**完全一致**。
- `measure_scripts/measure_scripts/sq_workflow/backend.py` 是**实验室在用的真实脚本**，其引导流程与
  `labrad_client` 一一对应 —— 这是"客户端代码写法正确"的最强证据。

### 2.2 关键的四个缺口

**(1) 连接参数是死配置**

`config/system.json` 里已有：

```json
"lqcs": { "connection": { "host": "localhost", "port": 7682, "timeout": 30 } }
```

但 `labrad_client.py:238`、`analysis_service/server.py:198,260,933` 全都是裸的 `labrad.connect()`。
按 pylabrad 的 docstring，`host` 默认取 `LABRADHOST` 环境变量，否则 localhost。
**远程拓扑下这是硬阻断** —— 配置写了也没人读。

**(2) `lqcs.paths` 是 `null` 且无人读取**

`config/system.json` 的 `lqcs.paths.measure_scripts` / `backend_dir` 都是 `null`。
`labrad_client._get_qmclaw_root()` 改为向上搜索含 `measure_scripts` 的祖先目录，找不到就 `raise`，不可配置。

**(3) 离线→在线运行时切换是坏的（静默假成功）**

离线模式下 `before_start()` 提前 return → `self._labrad` 始终为 `None`，且 fallback 被置为 `DEGRADED_PERMANENT`。
随后 `_ensure_connected()` 在 `self._labrad is None` 时**直接 `return False`，不会懒创建客户端**。
所以：

- `POST /mode online` 单独调用**永远连不上**；
- 更糟的是它返回 `{"success": true}` —— 调用方无法察觉模式切了但没连上。

目前唯一能连上的路径是 `POST /connect`（它会懒创建客户端）或改 `default_mode` 后重启。

**(4) 真机上没有任何写保护**

`run_code` 会 `exec` 任意代码，作用域里有全部 qubit 对象，可直接写 `f10`/`bias_z` 等 setpoint；
`/qubit/set_params` 也是直通。执行前无确认、无白名单、无审计。

### 2.3 已确认的远程连接 API

`labrad.connect(host=..., port=...)` 可用。docstring：

> `host` (str): The hostname of the manager. By default, we will use the hostname from the
> `LABRADHOST` environment variable, or localhost if the environment variable is not set.

### 2.4 已确认的 lqcs 配置面

`lqcs.system_config` 首次导入时访问 **`~/.lqcs`**。实测（只读沙箱内）：

```
IMPORT FAILED: PermissionError [WinError 5] 拒绝访问: 'C:\Users\39045\.lqcs'
```

即它需要读写该目录（沙箱内无法创建故报错）。该目录当前**不存在**，且其 schema 编译在 `.pyd` 内，
**静态读不到**。它提供 `get_config()['ip']`、`get_ray_head()`、`get_ray_port()` —— 实验室网络拓扑的真相来源。

旁证：`vendor/QubitClient/script/lqcs/measure_scripts/debug.py:9` 也用了 `pathlib.Path.home() / '.lqcs' / 'debug'`，
确认 `~/.lqcs` 就是 lqcs 的按用户配置目录。

### 2.5 已确认的隐藏前置条件

`lqms` 的 `.pyd` 在导入时需要**可写的临时目录**，否则：

```
FileNotFoundError [Errno 2] No usable temporary directory found in [...]
```

离线模式从不导入 `lqms`（`before_start()` 提前 return），所以这个问题**只在切到在线时才暴露**。

---

## 三、核心架构决策：两个正交开关

「默认 `auto`」与「先只读」必须用**两个独立开关**调和，否则 `auto` 会在硬件上线时自动把指令发到真机：

| 开关 | 位置 | 默认 | 含义 |
|---|---|---|---|
| `mode` | `fallback_config.json` → `default_mode: "auto"` | `auto` | **数据来源**：online / offline / auto |
| `actuation` | `fallback_config.json` → `quantum_service.actuation.*` | **全部 `false`** | **是否允许写**：执行实验 / 改 qubit 参数 |

设计要点：

- `mode` 只管"从哪取数、连不连 LabRAD"，**不管能不能写**。
- `actuation.allow_execute`（默认 `false`）管 `POST /execute` 与 agent 的实验执行工具。
- `actuation.allow_set_params`（默认 `false`）管 `POST /qubit/set_params`。
- 环境变量可覆盖（`QMCLAW_ALLOW_EXECUTE` / `QMCLAW_ALLOW_SET_PARAMS`），便于临时提权而不改文件。
- **闸门只在服务端强制**，绝不依赖 LLM 自觉。agent 侧只做"提前解释为什么不能做"，真实拦截在 `quantum_service`。

这样 `default_mode: auto` 可以放心用：连上真机后能读 qubit 参数、列实验、看会话，
但**任何写操作都会被拒**，直到显式翻开闸门。

---

## 四、实施分组

### A. 连接参数外置（远程拓扑的关键改动）

1. 新增读取器（放进已有 `services/common/config.py`，不新造机制）：

   ```python
   get_labrad_params() -> {"host": str, "port": int, "timeout": int}
   ```

   优先级：**环境变量 `LABRADHOST`/`LABRADPORT` → `config/system.json` 的 `lqcs.connection` → 现有默认（localhost:7682）**。
   `system.json` 不改时行为完全不变。

2. 把裸 `labrad.connect()` 替换为 `labrad.connect(**params)`：

   | 文件 | 行 |
   |---|---|
   | `services/quantum_service/labrad_client.py` | 238（主路径） |
   | `services/analysis_service/server.py` | 198, 260, 933 |
   | `backends/lqcs/__init__.py` | 170（脚本层，避免误连 localhost） |
   | `scripts/check_services.py` | 54 |
   | `python/workflow/entry.py` | 102 |

3. 把 `system.json` 的 `lqcs.paths.{measure_scripts,backend_dir}` 接进
   `labrad_client._setup_paths()` 与 `_get_qmclaw_root()`：显式配置优先，缺省仍走现有向上搜索；
   两者都拿不到才 `raise` —— 保留"不静默回退到写死路径"的现有设计意图。

### B. 只读写闸门（本期最重要的安全项）

1. `fallback_config.json` 的 `quantum_service` 下新增：

   ```json
   "actuation": { "allow_execute": false, "allow_set_params": false }
   ```

2. 在两个写入口最前面加统一检查：

   - `_handle_execute`：闸门关闭 → 返回 `{"status": "read_only", "error": ..., "hint": ...}`，
     **不进入离线模拟分支、不占用 busy 锁**。
   - `_handle_qubit_set_params`：同样拒绝。

   **判定顺序**：先判离线（离线模拟不碰硬件，不受闸门限制），再判闸门。
   这样保证现有离线验收用例全部回归通过。

3. 只读路由保持可用：`/health`、`/status`、`/qubits`、`/experiments`、`/qubit/params`、
   `/datasets`、`/sessions`、`/session_tree`、`/mode`。

4. `/health` 与 `/status` 增补字段：

   | 字段 | 含义 |
   |---|---|
   | `mode` | 已有 |
   | `data_source` | 新：`"labrad"` / `"offline"` / `"fallback"` |
   | `labrad_connected` | 已有 |
   | `actuation_allowed` | 新 |
   | `labrad_host` / `labrad_port` | 新，便于确认到底连了谁 |

5. agent 工具层（`services/common/quantum_tools/execution.py` 的 `RunCodeTool` / `RunExperimentTool`）
   读同一开关，失败时返回带 `hint` 的结构化错误，让 agent 能解释"当前是只读联调"，而不是抛看不懂的 500。

### C. 修复离线↔在线切换语义

1. `_ensure_connected()`：`self._labrad is None` 且 `self._mode != OFFLINE` 时**懒创建** `LabRADClient()`
   （与 `_do_reconnect` 已有逻辑对齐）。
2. `_handle_mode` 切到 `online`/`auto` 时清掉 `DEGRADED_PERMANENT`，否则 `_ensure_connected()` 第一行就 `return False`。
3. `_handle_mode` 不再返回静默假成功：切到 online/auto 时**实际尝试连接**，响应里带回 `connected` 与失败原因。
4. 保持 `/connect` 为显式连接动作（它已能懒创建客户端，是当前唯一可用路径）。
5. `_setup_ray()` 的返回值目前被忽略。改为：**Ray 失败不阻断只读能力**
   （qubit 参数生成不依赖 Ray，只有 `sq.*` 执行依赖 DeviceManager）。
   Ray 状态写进 `/status`；`allow_execute` 打开时把"Ray 未就绪"作为执行前置校验。

### D. 远程连通性前置条件（需在人工终端操作，只读沙箱做不了）

1. **`~/.lqcs` 必须存在且内容正确**。它是 `get_config()['ip']` / `get_ray_head()` / `get_ray_port()` 的来源，当前缺失。
   - 做法：在**你自己的终端**（非沙箱）跑一次 `python -c "from lqcs import system_config"` 让它落地再查看；
   - **更可靠的是直接从测控机拷贝整个 `~/.lqcs`** —— 它承载实验室真实拓扑，手写易错。
2. **保证 TEMP 可写**，否则在线分支一启动就 `FileNotFoundError`。
3. **网络可达**：测控机 TCP 7682 可连；确认是否启用 TLS（`labrad.connect` 的默认端口随 TLS 模式变化）。
4. **版本对齐**：以**测控机上的 `lqms`/`lqcs` 版本为准**。本机 `lqms` 0.3.1 而 deploy 给的是 0.3.3 轮子
   （此前决策保持 0.3.1）；`sq_nodes` 签名必须与真机一致。
5. **DataVault 会话存在性**：`['', 'LQHL', 'test', '20260324']` 必须在**远程** DataVault 上存在，
   否则 `switchSession`/`DataLab` 失败。
6. **Java 8**：本机已具备，但**只有本机自己托管 LabRAD manager 时才需要**；纯远程客户端不需要。

### E. 无硬件条件下的验证手段（"影子"验证）

硬件不可用，本期靠三层验证，全部不碰真机：

1. **单元层**：mock `labrad.connect`，断言
   (a) 传入的 `host`/`port` 确实来自 `system.json`；
   (b) `initialize()` 的调用顺序；
   (c) 闸门关闭时 `/execute` 与 `/qubit/set_params` 被拒且**不进入模拟分支**。
2. **协议层**：在**本机**用 `lqcs` 自带的 `registry.sqlite` + `LabRADmanager` jar 起一个
   **无硬件的本地 LabRAD manager**，跑通 `pylabrad` 真实握手与 `system_config` 读取。
   可验证注册表、握手、超时、`switchSession` 之前的全部环节，不需要冰箱。
3. **影子执行**：在线分支下把 `exec` 换成记录器，返回**将要下发的代码**与解析出的 `sq.*` 调用，
   而不真正执行。让 agent 完整工具循环端到端跑通，同时**从机制上保证不写硬件**；
   还能核对 `adapter.build_experiment_code` 的模板名是否真存在于真机 `sq_nodes`。

**明确承认的验证边界**：没有硬件就无法验证真实比特参数、实验物理正确性与数据质量，
这三项必须等硬件恢复后单独验收。

### F. 只读自检脚本与文档

1. 新增 `docs/deploy_diff_tools/check_labrad_link.py`（沿用现有 7 个只读校验脚本风格），一条命令输出：
   - 解析出的 `host`/`port`/`timeout` 及其来源（env / system.json / default）
   - TCP 7682 可达性
   - `~/.lqcs` 是否存在，以及 `get_config()['ip']` / ray head / ray port
   - TEMP 可写性与 `lqms` 导入是否通过
   - `labrad.connect` 实际结果与失败阶段
   - `sq_nodes` 里可用的实验函数清单（与 `config/experiment_configs.json` 的模板做差集）
   - 闸门状态（`allow_execute` / `allow_set_params`）
2. 新增 `docs/REAL_MACHINE.md`：拓扑图、每个配置键怎么填、启动顺序、
   从只读到可写的提权步骤、故障排查表。
3. `CLAUDE.md` 增补约定：**写闸门默认关闭；任何打开写权限的操作必须由人显式执行并记录**。
4. 更新 `docs/CHANGES_VS_SOURCE.md` 中 L4（`get_qubit_params` 查不到离线 qubit）—— 在线模式下自然消解。

---

## 五、边界情况与失败模式

| 场景 | 期望行为 |
|---|---|
| LabRAD 不可达 + `auto` | 干净回落离线；`data_source` 明确写 `offline`，不得让用户误判来源 |
| 连上但 Ray 未就绪 | 只读查询照常；`allow_execute` 若已打开则拒绝执行并说明原因 |
| 闸门关闭 + 在线 | `/execute`、`/qubit/set_params` 返回 `read_only`，**不进入模拟分支**、不占 busy 锁 |
| 闸门关闭 + 离线 | 离线模拟照常执行（不碰硬件）—— 保证现有离线验收用例回归通过 |
| `switchSession` 挂起 | 已有 30s 线程超时；把失败阶段写进 `/status`，不要只留一句 `initialize failed` |
| 数据来源混淆 | 在线结果带 `source: "labrad"` + `host`；离线结果已带 `synthetic`/`simulated`，两者互斥且都显式 |
| **执行超时（打开写权限前必须修）** | 现状：`exec` 跑在守护线程，`join(timeout)` 超时后**上报 timeout 并释放 busy 锁，但线程仍在真机上继续跑**，第二条指令可能并发下发。写权限打开前必须改为"标记服务卡死并拒绝后续执行"，而非静默放行 |
| 全局 stdout/stderr 替换 | 仅在执行串行化时安全；与上一项同源，一并处理 |

---

## 六、验收清单

### 只读阶段（现在就能做，无需硬件）

1. `check_labrad_link.py` 全绿，或给出可操作的失败原因。
2. 本地无硬件 LabRAD manager 场景下，`LabRADClient.initialize()` 能推进到 `switchSession`
   （或精确报出卡在哪一步）。
3. 闸门关闭 + `mode=online` 时，`/execute` 与 `/qubit/set_params` 均被拒。
4. `/health`、`/status` 暴露 `mode` / `data_source` / `labrad_connected` / `actuation_allowed` / `labrad_host`。
5. **回归**：闸门关闭 + LabRAD 不可达时，离线端到端
   （`对 q10lu12 运行 t1 实验`、`对 q10lu8 运行 t1 实验`）结果与当前完全一致。
6. 影子执行能让 agent 跑完整个工具循环并返回"将下发的代码"，且真机上零副作用。

### 可写阶段（需硬件，本期不做，仅登记）

需先完成：超时/并发修复 + 显式翻开闸门 + 每次真实下发前人工确认 + 白名单实验范围。

---

## 七、假设与待解未知

需要补充信息或到真机侧确认：

1. **测控机的 host/IP 与端口**未提供；是否启用 TLS 未知。
2. **`~/.lqcs` 的 schema 静态不可读**（编译在 `.pyd` 内）。需在人工终端落地后查看，
   或从测控机整目录拷贝。
3. **远程 DataVault 是否存在 `['', 'LQHL', 'test', '20260324']`**，以及是否应换成别的会话。
4. **测控机上的 `lqms`/`lqcs` 版本**未知，`sq_nodes` 签名兼容性需比对。
5. **防火墙**是否放行 7682 与 Ray 端口；远程节点能否作为 Ray client 接入
   （实验室可能不支持，需允许 Ray 失败而不影响只读）。
6. **`generate_qubit` 首个参数**：实验室脚本传 `globals()`，而 `labrad_client` 传 `{"s": self._s}`。
   需以 `lqms` 0.3.1 实测确认哪种正确 —— **这是潜在的静默失败点**。
7. `measure_scripts/measure_scripts/` 只有 `sq_workflow/backend.py`，仓库未交付其余实验室本地代码。
   对 `quantum_service` 无影响（它走 `lqms.measure.tuners.sq_nodes`），
   但走 `job_runner.py` / `hermes_runner.py` 的 `import sq` 路径则可能缺件。

---

## 八、提交切分

按 CLAUDE.md 约定 11：

| 序 | 提交信息 | 对应 |
|---|---|---|
| 0 | `docs: 新增真机接通设计方案（远程测控机 + 只读联调）` | 本文档 ✅ |
| 1 | `feat(labrad): 连接参数外置到 system.json，支持远程测控机` | A |
| 2 | `fix(quantum): 修复离线→在线切换不生效与静默假成功` | C |
| 3 | `feat(safety): 增加写闸门，只读联调期间禁止执行与改参` | B |
| 4 | `test(labrad): 无硬件 LabRAD 联调与影子执行验证` | E |
| 5 | `docs: 新增真机接通说明与只读自检脚本` | F |

**依赖顺序**：`0 → A → C → B → E → F`
（B 依赖 C 的 `data_source` 判定；E 依赖 A 的参数注入；每步可独立回退。）

**执行节奏**：每一步按其自身提交粒度推进，不合并成一次性大改；
服务端 Python 改动需重启受影响微服务才生效。
