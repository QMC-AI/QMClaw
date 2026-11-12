# QMClaw 架构路线图（顶层设计 + 在线升级协议）

> 状态：v1（2026-11-09 记录）
> 背景：用户 m14819 要求解释"各微服务的设计逻辑与问题"，m14821 要求给顶层设计建议，
> m14826 批准记录并要求**"在线升级——用户继续做测控实验不受干扰，后台逐步升级"**。
> 本文件是这些建议的落盘版本，也是后续改造的唯一依据。

---

## 0. 一条总原则

**把纪律变成结构。**

现在系统里大部分隐患来自同一件事：*规范靠文档、执行靠自觉*。

- "实验必须 `do_plot=False`"
- "真机实验必须走 qwf / 必须先调 skill"
- "fread 是逐比特的，别被 auto-update 改掉"

凡是靠自觉的规则，迟早会被一次赶时间的操作绕过。
**凡是能被绕过的规则，就该改造成绕不过去的结构；凡是能被静默破坏的状态，就该变成显式可查、可回滚的一等对象。**

---

## 1. 现状设计逻辑（保留，不推翻）

1. **按"副作用边界"切服务，而不是按功能名词切。**
   `:3003` 唯一动硬件，`:3004` 唯一出图。这条线是被一次真实故障逼出来的：
   厂商实验带 `do_plot=True` → matplotlib 在工作线程拉起 Qt GUI → `:3003` 端口仍 LISTENING 但不再 accept。
   于是"执行"与"出图"必须分家。
2. **数据流单向，数据集是唯一事实源。**
   `qwf/代码 → LabRAD → DataVault 数据集 → analysis 拟合 → image/qubitclient 分类 → 决策回流`。
   寄存器快照天然躺在数据集 `parameters` 里 → 才有 `/qubit/apply_dataset`。
3. **自治驾驶三层护栏。**
   物理层（HardwareLane 并发恒 1）／授权层（manifest→ticket→authorize+审计）／决策层（LLM 只在允许选项里选，ask→needs_human）。
4. **DSL 把厂商隐式语义显式化**（goal lowering 成 repeat/if-else/assert）。
5. **规范作为可演进资产**（AGENTS/CLAUDE + skill + 长期记忆 memory L3）。

---

## 2. 顶层设计建议（本次批准的部分）

### 2.1 单写者原则 ★最高优先
整个系统对硬件**只能有一个写入者**。

现状：`task_queue` 已实现"硬件通道并发恒 1"，但 `:3003/execute` 是**旁路**——直接进 LabRAD，
不经队列、不经票据、不经 lint。治理层因此是"可选装饰"。
（实证：本轮 q7ld3 排查全部走直发，绕过了额度与审计，`s21` 的 auto-update 静默改掉 `fread` 无人拦截。）

**目标形态**：`:3003/execute` 降级为 *producer*（只提交），真正的仪器调用由唯一 worker 消费。
于是"绕过账本"在拓扑上不可能，而不是靠约定。

**收益**：治理层从"配置项"变成"拓扑性质"。

**落地状态（2026-11-09，阶段 2 第三批）**
- `services/quantum_service/writer.py`：单写者内核 `SingleWriter`（唯一 worker 线程 + FIFO 队列，
  串行执行、异常隔离、超时不影响 worker）。
- 三档模式 `quantum_service.actuation.writer_mode`：`off` / `shadow`（**默认**，只观测不改变执行路径）
  / `enforce`（实验真正交给唯一 worker 串行执行）；每请求读取 → 热生效；环境变量 `QMCLAW_WRITER_MODE` 优先。
- `GET :3003/qubit/writer`：模式、队列深度、影子观测统计。
- `GET :3003/qubit/runs` 新增 `governance` 摘要（total / governed / ungoverned / coverage），
  `ungoverned>0` 即存在绕过 task_queue 的直发执行 → 打开 `require_task` 可归零。
- 自检 `_phase2c_check.py` PASS=50 FAIL=0。
- **最后一步的进度（2026-11-10，阶段 15）**：异步通道与审计已经打通 ——
  异步执行现在落**两条同 run_id 的记录**（提交 `queued` + 完成态含 dataset_ids/metrics/
  verdict），读侧 `/qubit/runs` 与 `/qubit/state` 按 run_id 去重；基线（DataVault 差集与
  registry 前快照）改在 worker 真正执行前取。见 `docs/OPERATIONS.md` §3.18。
  **阶段 16a（2026-11-10）已落地**：`/execute` 默认改成异步（producer 语义），
  三态契约 `wait → async → 默认`，默认值来自 `quantum_service.actuation.execute_default`
  （环境变量 `QMCLAW_EXECUTE_DEFAULT` 优先），随时可改回 `sync` 热回退；所有 Python 消费方
  显式表态（agent/qca/hermes/client_proxy 走 `wait:true`，workflow/task_queue 走异步 job）。
  见 `docs/OPERATIONS.md` §3.19。**16b 已落地（阶段 19，2026-11-10）**：Express 新增
  `GET /api/quantum/jobs/:id`、`POST /api/quantum/jobs/:id/abandon` 两条代理；前端
  `runExperiment / executeExperiment` 命中 `queued` 自动轮询到终态并摊平成老结果形状，
  页面只加一行相位提示。见 `docs/OPERATIONS.md` §3.22。**§2.1 至此收口。**
- **审计查询（2026-11-10，阶段 18）**：`GET :3003/qubit/runs` 支持
  `qubit / status / source / dataset_id / trace_id / since / until` 过滤，且**过滤先于截断**
  （老 trace 不再被 limit 截掉）；规范状态由 `runrecord.record_canonical_status()` 唯一决定，
  纯函数 `filter_records()` 可离线测；回包带 `filters` 回显。见 `docs/OPERATIONS.md` §3.21。

### 2.2 设备抽象层 Qubit Port
全系统的语义目前直接来自厂商 `sq.*`：`zpa` 二维扫描默认展开几千点、`freq=None` 会崩、
auto-update 静默改 `fread`、T1 必须给 `zpa`、`tbuffer=0` 的 `set_pi` 不能用、
`.pyd` 遮蔽导致部分函数根本不存在。这些知识散落在会话记忆里。

**端口接口**：`read_state / apply_pulse / measure(program) / probe_transition`，内置三件事：

| 内置能力 | 解决什么 |
|---|---|
| 参数校验与钳制（扫描点数上限、维度合法性、强制 `do_plot=False`） | 厂商语义风险上移拦截 |
| 能力探测（该 lqms 版本真正支持哪些函数） | `.pyd` 遮蔽类问题 |
| 事务语义：前快照 → 数据集 → 后快照 → diff，可回滚 | `autorevert` 从"事后插件"变成"端口自带语义" |

### 2.3 RunRecord 一等公民
统一结构：

```
{runId, program/IR, inputs, snapshotBefore, datasetIds, snapshotAfter, diff, metrics, verdict}
```

由端口层自动产出并落盘。可复现性、事后回滚、"这个结论出自哪个数据集"、状态机的证据链，
全部有了同一个载体。当前缺口：`:3003/execute` 的 stdout 不打印数据集号，直发侧没有 manifest。

**落地状态（2026-11-09 22:55，阶段 3 第 2 条补齐）**

- `services/common/runrecord.py` 新增两个纯函数：
  `extract_metrics(result)`（只从结果里挑 `contrast/p1_i/p1_x/separation/snr/dmu_sig/
  T1_us/T2_us/R_squared/xeb_p/gate_fidelity` 这些**真的写了**的数值，不猜不补）、
  `derive_verdict(metrics, status=None)`（用 Metric Registry 判 `ok`/`warn`/`fail`，
  没有可判定指标就返回 `None`，不编造结论）。
- `:3003/execute` 落 RunRecord 时填 `metrics` 与 `verdict`；`replay_auto_record=full`
  时额外落 `snapshot_before` / `snapshot_after` / `diff`，并把 after 快照**复用**给回放
  fixture（避免为审计多读一次设备）。日常模式（`off`/`code_only`）不额外读 registry。
- ~~**仍未解**：`dataset_ids` 对 `sq.iqraw` 这类"stdout 不打印数据集号"的实验仍为空；
  直发侧依旧没有 manifest。~~ → **已解（阶段 7，2026-11-09）**：

**落地状态（2026-11-09 23:35，阶段 7：数据集归属 + 直发侧 manifest）**

- `services/common/dataset_ids.py`（纯标准库）：`parse_dataset_ids`（stdout 里
  `dataset: N`）、`iter_dataset_numbers`（`data_vault.dir()` 的 `(dirs, files)`、
  扁平列表、怪形状都不炸）、`new_dataset_numbers`（**任一侧未知就返回空**，
  宁可少记也不把上次遗留的数据集算成本次产出）、`merge_dataset_ids`、
  `probe_dataset_numbers`（异常/None 返回诊断字符串，绝不外抛）、
  `dataset_source_label`（`stdout` / `datavault` / `stdout+datavault` / `unknown`）。
- `:3003/execute` 包装层在执行前后各列一次 DataVault 目录取差集，与 stdout 解析结果合并；
  `RunRecord.dataset_source` 标注来源，响应回带 `dataset_ids` + `dataset_source`。
  单次 `dir` 是毫秒级只读调用，失败即放弃（不打扰实验）；`QMCLAW_DATASET_PROBE=0` 可关闭。
- `services/common/program_manifest.py`：直发侧也有 manifest ——
  `{version, program_hash, qubits, functions, calls[], auto_writes[], warnings[]}`，
  每条 call 用 `replay.VENDOR_SEMANTICS` 补 `auto_writes` / `missing_required`，
  **确定性输出**（无时间戳），随 `RunRecord.manifest` 落盘。
- 自检 `_phase7_check.py`：**PASS=56 FAIL=0**（解析 / 差集 / 合并 / 探测失败路径 /
  清单结构与确定性 / RunRecord 形状 / 服务接线）。
- **边界**：目录差集只归属"本次执行新增"的数据集；实验若自起子会话或写别处仍会缺号，
  以 `dataset_source` 判断可信度。真机验证需 `:3003` 起来后跑一次带 iqraw 的 goal。

**落地状态（2026-11-10 08:50，阶段 8：会话路径唯一实现 → §3 #2 的会话部分已收口）**

- `services/common/session.py`（纯标准库，不连 LabRAD）：
  `parse_session_path`（列表 / 元组 / JSON 字符串 / `LQHL/test/2025` 都认，
  返回 `(path, error)`，**永不抛异常**）、`normalize_session_path`、`session_key`、
  `same_session`（坏输入一律判不同，不猜）、`load_session_config`（回
  `(path, source)`，文件缺失/损坏/`path` 为空数组都回默认）。
- 四个会话接口全部改走它：`:3003` 的 `/connect`、`/switch_session`，`:3004` 的
  `/connect`、`/switch_session`；坏输入回 `validation_error` 信封而不是
  `JSONDecodeError` 500。`:3004/connect` 现在**优先用请求里指定的会话**。
- 两处 `_load_session_config`（`quantum_service/labrad_client.py`、
  `analysis_service/server.py`）从"逐字重复两份"改为一行的委托。
- 自检 `_phase8_check.py`：**PASS=39 FAIL=0**（各输入形态 / 非法输入 / key 与
  same / 配置与降级 / 四个接口与两份加载函数的接线 / 防回退字面量扫描）。
- **§3 #2 的另两项后来也收口了**：`:3004/plot` vs Express `/sessions/plot` 的
  `dataset_index` 透传在阶段 12 补上（OPERATIONS §3.15）；registry 与
  `/qubits/:name/params` 的读取路径在阶段 9 收敛为 `_read_registry_tree` + `PARAM_KEYS`
  投影（OPERATIONS §3.12）。本行保留为当时的收口记录。

**落地状态（2026-11-10 09:05，阶段 9：registry 读取收敛）**

- `_read_registry_tree(qname)` 成为唯一读路径（不加锁，调用方持 `self._labrad_lock`）：
  `/qubit/registry` 回全树，`/qubit/params` 只做 `PARAM_KEYS`（28 个历史键）投影，
  值经 `_legacy_param_value` 折成历史形状（字符串/数组 → `None`）。
- registry 打不开时退回 qubit 对象投影 `_params_from_qubit_object`，响应 `params_source`
  标注 `registry` / `qubit_object`。
- **不把全树吐给 `/qubit/params`** 的原因：前端 `QubitParamsPanel` 会把 `params`
  原样 `parseFloat` 回写，对象/数组键会变成 `NaN` 进 registry。键集必须钉死。
- 写入侧原本就已共用 `_write_qubit_params`（set_params 与 apply_dataset），未新增第二套。
- 自检 `_phase9_check.py`：**PASS=35 FAIL=0**。
- 同批修掉阶段 7 的真机缺陷：LabRAD `data_vault.dir()` 不吃 session 参数（静默列根目录），
  首版探测恒为空 → `dataset_ids` 一直缺号；改为 `read_session_dataset_numbers`
  （cd 回根 → cd session → 无参 dir），真机连接实测 2668 个数据集。**需重启 `:3003` 生效。**
- `:3004/plot` vs Express `/sessions/plot` 的 `dataset_index` 透传仍需改 Express 契约，
  留待用户确认。

**落地状态（2026-11-10 09:17，阶段 12：`dataset_index` 透传 → §3 #2 收口）**

- Express `POST /sessions/plot` 接受 `dataset_index`（驼峰 `datasetIndex` 也认）并原样转发给
  analysis `/plot`；不传 = 画最新（原行为不变）。非法值在 Express 层回 400
  （`status=failed` / `reasonCode=validation_error`），不再让下游猜。
- analysis `/plot` 回包补 `dataset_index` / `dataset_name`，"画的是哪个数据集"可对账。
- 于是"画指定数据集"不再需要绕过 Express 直连 `:3004` —— 同一能力只剩一个入口。
- 自检 `_phase12_check.py`：**PASS=21 FAIL=0**；文档 `docs/OPERATIONS.md` §3.15。

**落地状态（2026-11-10 09:15，阶段 10：真机可用性语义收敛）**

- `services/common/availability.py` 成为唯一判据：`build_availability()` 出
  `{state, reasonCode, message, device, deviceConnected, mode, fallbackState,
  sessionPath, busy, ready, offlineAvailable, stuck}`；判据顺序 stuck → 已连 →
  离线模式 → 自动降级 → 永久降级 → 初始化 → 未连接。
- `:3003`/`:3004` 的 `get_health()` 只喂字段；两份**走不到**的 `_handle_health`
  改成一行委托（死代码判据与活的那份本就不一致）。
- `base_service.send_health_response()` 走 `health_status_code()`：`ok` → 200，
  `degraded`/`unavailable` → 503；历史 `status` 由 `derive_legacy_status()` 推出，
  老消费者与 HTTP 码行为不变。
- analysis 的健康检查补上 `offline_available` / `stuck` / `stuck_reason`。
- 自检 `_phase10_check.py`：**PASS=62 FAIL=0**。
- 词表提醒：`availability.state`（真机能力）≠ `errors.py` 的规范状态（请求结局）。

### 2.4 比特状态机
`unknown → freq_locked → amp_calibrated → state_discriminated → coherence_measured → characterized`

每态定义**进入条件 + 证据数据集 + 判据**；推进由规则决定，LLM 只在**分叉点**
（多候选动作、判据临界、异常）给出选择且必须给理由。
收益：标定从"知识在人脑里"变成"状态在系统里"，能回答"q7ld3 卡在哪一态、缺什么证据"。

**落地状态（2026-11-09，阶段 4）**
- 状态机已接进 outcome：`dsl/executor.py` 的 `_compute_outcome()` 把本次运行的指标
  （contrast / dmu_sig / T1_us / T2_us / xeb_p …）折成证据，调用
  `services/common/qubit_state.derive_state()`，outcome 里多出 `qubit_state`
  （`state` / `reasons` / `missing` / `next_action`）。
- 于是"目标达没达成"与"这个比特现在到哪一态、缺什么证据"是同一份结论，
  不再需要人对着散落的实验记录自己推断。

**落地状态（2026-11-10，阶段 11）：证据从台账来**
- 阶段 4 只做了"给证据 → 判态"，证据仍由调用方（executor / 人或 agent）自己拼。
  阶段 11 补上另一半：`services/common/state_evidence.py` 的
  `evidence_from_records(records, qubit)` 直接读 `RunRecord` 台账（`data/workflow-runs/*.json`），
  把真跑过的实验翻成证据，并保留 `run_id` / `dataset_ids` / `functions` / `finished_at`，
  评审能点回原始数据。
- 门槛明确：只认 `status ∈ success/completed/ok` **且 `dataset_ids` 非空**的记录；失败、无数据集的
  记录只计入 `records.ignored` 并留 `ignored_runs`。按时间倒序新覆盖旧；同一指标两次读数
  相对差 >20% 记 `conflicts` —— 读出链慢漂在污染状态之前就被点名。
- `qubit_state` 补两张表：`EVIDENCE_SOURCES`（7 种证据 ↔ 产出实验，附厂商坑提示）与
  `MISSING_FOR_STATE`（6 态各缺什么）；`derive_state()` 输出新增 `missing_detail`
  （带 `produced_by` 的缺失清单），`evidence_plan(state)` 给出"缺什么 + 下一步"。
- 端点 `POST/GET :3003/qubit/state`：台账 + 可选显式 `evidence`（覆盖台账、来源标
  `{"source": "request"}`）→ 状态 / 证据来源 / 冲突 / 计划。**只读**，不执行实验也不写寄存器。
- 自检 `_phase11_check.py` PASS=76；文档 `docs/OPERATIONS.md` §3.14。

### 2.5 Metric Registry（指标与判据唯一真源）
建 `config/lab_criteria.json`：每个指标定义**标准算法、参数、阈值、置信度、数据列口径**；
实验产出统一 metric snapshot；`goal` 判定只引用指标名与阈值。

现状痛点：contrast 门槛在会话里出现三个版本（实验室 0.4 / 用户 0.2 / 实测活比特 0.25–0.40）；
T1 拟合有"自由 offset"和"固定 c=ground floor"两套；`dc.DataLab` 与 `dv.variables()` 列序不一致。

**落地状态（2026-11-09，阶段 4）**
- `services/common/metrics.py` 新增阈值解析接口：`resolve_threshold(metric, level)` /
  `available_levels(metric)` / `criteria_metric_name(ref)` + 别名表
  （`min`/`usable`/`weak`/`target`/`gate`/`offset` …），**不抛异常**，未知指标只给诊断文本。
- `goal.success` 支持 `criteria` + `level`：阈值由 registry 提供，不再在 qwf 里写死数字；
  显式 `value` 优先但仍记 `criteriaRef`（形如 `contrast.good`）。
  新增错误码 `E_GOAL_CRITERIA`（未知指标 / 未知档位 / `level` 单独出现）。
- `goal.py` 的 lowering 把 registry 阈值写进 `goal_assert.threshold`，
  `meta.goal_criteria_ref` 记录来源；`executor._compute_outcome()` 的 outcome 新增
  `qubit_state`（比特状态机）+ `detail.criteriaRef`。
- 新增 `services/workflow_service/dsl/registry.py`：**Metric Registry 接入层**。
  `dsl` 包既可能被当 `services.workflow_service.dsl` 导入，也可能被离线自检当顶层 `dsl` 导入
  （此时 `...common` 相对导入会报 "attempted relative import beyond top-level package"），
  这一层统一用绝对导入 `services.common.*` 并补 `sys.path`，两种加载方式判据一致。
- 自检 `_phase4_check.py` PASS=37 FAIL=0；全量 24 套自检 exit=0（断言 773 通过 / 0 失败）。

**落地状态（2026-11-09，阶段 2 第二批）**
- `config/lab_criteria.json` 已成为判据唯一真源（contrast 0.2/0.4/0.7、t1 固定偏置口径、t2_star、xeb p/gate_fidelity）。
- `services/common/metrics.py`：按指标名统一判定（`evaluate_contrast` / `evaluate_t1` / `evaluate_t2_star` /
  `evaluate_xeb` / `evaluate_dmu_sig` / `evaluate` / `evaluate_all`），返回体统一
  `{metric, value, verdict, ok, thresholds, next_step, criteria_version}`；未知指标抛 `KeyError`。
- `services/common/qubit_state.py`：状态机由证据推导状态，返回
  `{state, state_index, description, reasons, missing, next_action, metrics, criteria_version}`。
- `POST :3003/qubit/assess`：**纯计算**端点（不下发实验），给证据即得状态与指标判定；
  非法入参返回统一错误信封 `validation_error`。
- 自检 `_phase2b_check.py` PASS=47 FAIL=0。

**落地状态（2026-11-09，阶段 2 第一批）**
- `services/common/runrecord.py`：RunRecord 一等公民（`run_id` / `program_hash` / `governed` /
  `dataset_ids` / `snapshot_before|after` / `diff`），`:3003/execute` 每次落一条 jsonl 到
  `data/run-records/`（不可写时降级到系统临时目录）。
- `services/common/errors.py`：统一错误码表（12 项）+ `error_envelope` + `normalize_error`（历史状态别名归一）。
- `services/common/capabilities.py`：静态 AST 探测 `.pyd` 遮蔽 `.py`，给出"源码有、运行时没有"的函数清单。
- `services/quantum_service/port.py` + `POST :3003/qubit/capabilities` `/qubit/probe_transition` `/qubit/runs`。

### 2.6 回放层（Replay）
录制 LabRAD 调用序列做 fixture + 设备仿真（auto-update、默认扫描展开、错误注入），
让 CI 能覆盖真机链路。现状：14 套自检 288 断言全绿，但**一个都不碰设备**。

### 2.7 附带
- **设备画像 Capability Profile**：session path、比特名 `q2ld3`、xy DAC `DC 761`、读出频率现在全硬编码。
  每条芯片/每台设备一份画像（可用函数、通道映射、已标定集合、腔频表），qwf 引用画像而非写死名字。
- **一条 traceId 贯穿全链**：Express → workflow → task_queue → quantum → LabRAD。

**落地状态（2026-11-09，阶段 3 第一批）**
- `services/common/trace.py`：`new_trace_id` / `ensure_trace` / `normalize_trace_id` / `trace_headers` / `attach_trace`；
  透传优先级 = 请求头 `X-Trace-Id` > body `trace_id` > body `traceId` > 服务端生成（生成时响应带 `trace_hint`）。
- `services/quantum_service/server.py`：`:3003/execute` 响应带 `trace_id`，并写入 RunRecord（新增 `trace_id` 字段）。
- `services/common/profile.py` + `config/device_profile.json`：12 比特画像（xy/z DAC、readout ADC、`f10`/`f21`/`fread`、
  `fc_read`、`bias_z`、`fc`、π 脉冲 amp/alpha、读入功率、读出幅度、`timing.xy`），带 `validate_profile` 缺失项检查。
- `GET :3003/qubit/profile`：只读画像文件（不连设备、不下发实验）；带 `name` 返回单比特条目。
- 生成脚本 `D:\qmclaw\_gen_device_profile.py`：只读 `POST /qubit/registry`，支持 `--dry-run`；目标目录不可写时改写工作区并提示拷贝。
- 自检 `_phase3_check.py`：**PASS=49 FAIL=0 WARN=0**（trace 12 项 / profile 16 项 / 真实画像 8 项 / server 接线 8 项 + 其余）。
- 未完成：traceId 尚未贯通 Express → workflow → task_queue（当前只到 `:3003/execute` 与台账）；画像尚未被 qwf 引用。

**落地状态（2026-11-09，阶段 3 第二批 ①traceId 全链贯通）**
- Express：`src/services/serviceProxy.ts` 用 `AsyncLocalStorage` 保存当前 trace，最前置中间件生成/透传 `X-Trace-Id` 并回写响应头；`proxyToService` 统一加 `X-Trace-Id` 请求头。
- 基础层：`services/base/base_service.py` 的 `_inbound_trace()` 读入站 `X-Trace-Id`（字符白名单 `A-Za-z0-9-_.:`、截断 64），GET 注入 `query["trace_id"]`、POST 注入 `data["trace_id"]`，响应回写 `X-Trace-Id`。
- workflow：`services/workflow_service/server.py` 的 `_handle_run_ir` 用 `ensure_trace` 取 trace 并写入 `context["trace_id"]`，`_start_executor` 内同步 `self._lqcs.trace_id`；4 条返回路径回显 `trace_id`。
- DSL 后端：`services/workflow_service/dsl/backends/lqcs.py` 队列 `/submit` 的 metadata 带 `traceId`，直连 `:3003/execute` 的 body 带 `trace_id` 且带 `X-Trace-Id` 头。
- task_queue：`_handle_submit` 把 trace 存进 `Task.metadata["traceId"]`，`_handle_status` 回显，`_handle_list` 支持 `?trace_id=` 过滤。
- quantum：`:3003/execute` 响应带 `trace_id` 并写入 RunRecord（新增 `trace_id` 字段）；`GET :3003/qubit/runs?trace_id=` 支持按 trace 过滤。
- 自检 `_phase3b_check.py`：**PASS=51 FAIL=0 WARN=0**（base 静态 / workflow 静态 / lqcs 运行期 / task_queue 运行期 / quantum 运行期 / serviceProxy.ts 静态）。
- 后续（均已落地）：画像进运行期在阶段 13（`$profile.*`，OPERATIONS §3.16）；
  回放层在 §2.6 与阶段 3 第二批③/阶段 5（`services/common/replay.py` +
  `services/common/regression.py`，OPERATIONS §3.7）。

**落地状态（2026-11-09，阶段 3 第二批 ②设备画像被 qwf/DSL 引用）**
- `services/workflow_service/dsl/lint.py` 新增三条画像规则 + 一条提示（读 `config/device_profile.json`，读不到就降级为提示，不报错）：
  - `I_PROFILE_QUBIT`（info）：`env.qubit` 在画像里 → 打印该比特的 `f10`/`f21`/`fread`/`xy_dac`/`z_dac`/πamp，便于人工核对。
  - `W_PROFILE_UNKNOWN_QUBIT`（warn）：`env.qubit` 不在画像里 → 大概率是名字写错或换了芯片/会话。
  - `W_PROFILE_HARDCODED_QUBIT`（warn）：measure/read 节点把比特名写死（应写 `$env.qubit`）→ 换片就打错比特。
  - `W_PROFILE_FOREIGN_QUBIT`（warn）：节点参数里出现画像中存在的**另一个**比特名 → 复制 qwf 忘改名的典型痕迹。
  - 画像不可用时给 `I_PROFILE_UNAVAILABLE`（info），**不**误报 warning。
- 自检 `_phase3c_check.py`：**PASS=27 FAIL=0 WARN=0**（规则存在性 / 画像文件自洽 / 正例 / 三类反例 / 画像缺失降级 / 既有规则回归）。
- 未完成：回放层（§2.6）尚未实现；画像仍是"读文件"，尚未由 `:3003/qubit/registry` 自动刷新。

**落地状态（2026-11-09，阶段 3 第二批 ③回放层）**
- `services/common/replay.py`（纯标准库、可离线单测）：
  - `VENDOR_SEMANTICS`：厂商语义表（12 个 `sq.*` 函数），每个条目含 `summary` / `auto_writes`（`update=True` 时厂商会静默回写的寄存器键）/ `requires`（必填参数，缺失即复现真机错误）/ `defaults` / `note`。
    例：`spectroscopy` 回写 `f10`/`f21` 且 `freq` 必填（缺省会退化成标量 → `IndexError: list index out of range`）；`s21` 回写该比特自己的 `fread`（曾把 q1/q3 写成 6.7255 → 假死）；`iqraw` 回写 `discriminator.center0/center1`（弱对比度下会写出假中心）；`pidf` 回写 `f10`（`df` 太小会扫空，需 ≥0.05）；`t1` 的 `zpa` 必填（否则走 zpa×时间二维扫描，极慢）；`spectroscopy_iq` 标注"当前环境不可用：`.pyd` 遮蔽 `.py`，需实验室重新编译 lqms 扩展"。
  - `parse_calls(code)`：从实验代码里抽出 `sq.<fn>(<qubit>, **kwargs)` 调用序列（保持出现顺序）。
  - `simulate(code, registry)`：离线推演——未知函数给 warning、缺必填参数复现真机错误码（`validation_error` + 原文 hint）、缺省参数记默认展开、逐项推演 `auto_writes` 的新值，产出 `registry_before/after`、`diff`、`warnings[]`、`error`。
  - `ReplayStore` / `record_fixture()` / `replay_fixture()`：fixture 落盘（默认 `data/replay-fixtures/`，不可写降级临时目录）、按 `fx-<12hex>` 取回、回放时比对**仿真 diff** 与**真机 registry 快照实算 diff**，输出 `simulated_only` / `recorded_only` / `value_mismatch` 三类漂移。
- `POST :3003/qubit/replay`：四个动作 `simulate`（默认）/ `record` / `list` / `replay`；**不连设备、不受写闸门影响**，可在 CI 里随便跑。
- 自检 `_phase3d_check.py`：**PASS=58 FAIL=0 WARN=0**（语义表 / parse_calls / simulate 四类典型行为 / store 往返 / 漂移检出 / 端点四动作）。
- 未完成：fixture 目前靠手工 `action=record` 录制，尚未由 `:3003/execute` 的 RunRecord 自动录制；仿真只覆盖"会改哪些键、会不会报错"，数值需以真机记录为准；真机侧仍需实验室重编译 lqms 才能覆盖 `spectroscopy_iq` 等新函数。

**落地状态（2026-11-09，阶段 3 后续：fixture 自动录制 + 画像自动刷新）**
- **fixture 自动录制**：`services/common/config.py` 新增 `_replay_auto_record()`（环境变量 `QMCLAW_REPLAY_AUTO_RECORD` → config `quantum_service.actuation.replay_auto_record` → 默认 `off`；非法值回落 `off`），三档语义：
  - `off`（默认）——完全不录，零额外开销；
  - `code_only`——记 `code` / `qubits` / `dataset_ids` / `trace_id` / `task_id`，**不读寄存器**；
  - `full`——额外采 registry 前后快照（会多两次 LabRAD 读）。
  `:3003/execute` 在 RunRecord 落盘后调用 `_maybe_record_replay_fixture(...)`，成功时响应带 `replay_fixture`；**所有异常吞掉**，绝不影响真机实验结果。
- **画像自动刷新**：`GET :3003/qubit/profile?refresh=1` 按画像里的比特逐个只读 `POST /qubit/registry`，
  `merge_registry(..., overwrite=True)` 用设备当前值覆盖旧值（否则画像里的旧 `f10`/`fread` 会永远盖住设备现状），
  快照落到 `<server>/data/device_profile_snapshot.json`（data 不可写时降级 `%TEMP%\qmclaw-device-profile\`）；**不改 `config/device_profile.json`**。
- 自检 `_phase3e_check.py`：**PASS=37 FAIL=0 WARN=0**（开关三档与非法值 / server 静态接线 / 自动录制行为与异常隔离 / 画像刷新与"不带 refresh 不连设备"）。
- 全量自检：`run_all_checks.py --e2e` 20 套全 exit=0（含 `_phase3e_check.py`）。
- 未完成：回放仿真仍只覆盖键级语义；`full` 模式的快照依赖真机 registry 读取，尚无真机 fixture 样本（需用户在重启窗口后授权一次）。

**落地状态（2026-11-10，阶段 13：画像进运行期 → §2.7 的"画像被 qwf 引用"收口）**
- 阶段 3 第二批② 只把画像用在 **lint 期**（判断比特名/参数像不像画像）。阶段 13 把画像
  变成**运行期可引用**：`services/common/profile.py` 新增
  `profile_env(qubit, profile=None)`（把画像条目折成扁平字典，只放真有的字段）、
  `profile_env_fields()`（`$profile.<key>` 的合法键表）、`PROFILE_ENV_FIELDS` / `PROFILE_ENV_META`。
- `IrExecutor.__init__` 在 env 就绪后构建 `self.profile`：`context.profile` 优先 →
  IR `env.profile` → `config/device_profile.json` 里 `env.qubit` 那一条；
  `_lookup_ref` 新增 `profile` 分支走 `_walk_profile`，于是 `.qwf` 里可以写 `$profile.f10`
  而不再把频率写死。画像缺失只 warning（`设备画像里没有 qX 的条目`），不拦执行。
- 静态检查同步：`validate` 放行 `$profile` 头、未知字段给 `W_PROFILE_UNKNOWN_FIELD`
  （回退表 `_PROFILE_FIELDS_FALLBACK` 与 profile 表一致）；`lint` 新增 `_profile_ref_checks`
  → `W_PROFILE_UNKNOWN_FIELD` / `W_PROFILE_MISSING_FIELD` / `W_PROFILE_UNKNOWN_QUBIT` /
  `I_PROFILE_REFS`，5 个产品 qwf 仍 0 error。
- 自检 `_phase13_check.py` PASS=38；文档 `docs/OPERATIONS.md` §3.16。
- 未完成：`config/device_profile.json` 仍是"读文件"（刷新靠 `GET /qubit/profile?refresh=1`
  写快照），尚未在 qwf 运行时按需自动刷新；画像里的通道名（`xy_dac` 等）仍由人工维护。

**落地状态（2026-11-09，阶段 5：fixture 驱动的离线回归）**
- 新增 `services/common/regression.py`（纯标准库、离线）：
  - `classify_fixture(fixture)`：回放单条 fixture，产出 `ok` / `drift_count` / `drift_kinds` / `simulated_diff` / `recorded_diff`；
  - `run_regression(fixtures, *, max_drift=0)`：批量回放并给结论 `verdict` = `green` / `drift` / `broken` / `empty`，单条坏 fixture 被隔离、不拖垮整轮；
  - `load_fixtures(directory, limit)` / `regression_from_directory(...)`：从 fixture 目录读回并端到端出报告；
  - `format_report(report)`：人读报告（`DRIFT_LABELS` 把三类漂移翻成中文）。
- CLI `D:\qmclaw\_replay_regression.py`：`--dir` / `--limit` / `--max-drift` / `--json`；退出码 `0` 通过、`1` 检出漂移或 fixture 损坏、`2` 运行错误。**`empty` 明确提示"还没有真机 fixture，回归未覆盖真机链路"，不算通过**。
- 自检 `_phase5_check.py`：**PASS=28 FAIL=0 WARN=0**（API 齐备 / 一致例无漂移 / 漂移例被检出且可读 / 坏 fixture 隔离 / 空目录 → empty / 落盘读回往返 / 报告文本 / CLI 以 runpy 进程内验证）。
- 全量自检：`run_all_checks.py` OFFLINE 21 套全 exit=0（含 `_phase5_check.py`）。
- 未完成：**仍无真机 fixture**——回归目前只验证代码路径；真正覆盖真机链路需要用户授权跑一次真机小实验（并把 `replay_auto_record` 临时设为 `full` 或 `code_only`）。

**落地状态（2026-11-09 晚，阶段 5 收尾：真机 fixture 回归跑通）**
- **已有真机 fixture**：`data/replay-fixtures/` 里存在 8 条早前真机实验自动录下的 fixture（带真实 `dataset_ids`）；
  `D:\qmclaw\_replay_regression.py --dir "Agentic Workflow/qmclaw-server/data/replay-fixtures"` →
  **`verdict=green`，8 条通过、8 条漂移 0**，退出码 0。→ §2.6 的核心目标（CI 能覆盖真机链路）**已达成**，
  厂商语义表与真机 registry 快照目前一致。
- **自动录制链路验证通过**：`replay_auto_record=full` 时 `:3003/execute` 自动落 `fx-*.json`
  （含 `fixture_id`/`created_at`/`source`/`run_id`/`trace_id`/`qubits`/`dataset_ids`/`status`/`simulated`/`registry_before|after`）。
  本轮 q2ld3 iqraw 因**设备层**报错（`lqcs/servers/device_manager.py:1384 prepare_dac_meas: ufunc 'divide' not supported`，
  3 次重试一致，与参数无关）→ 落下的 fixture `dataset_ids=[]`、`status=error`，**不算真机覆盖**；
  `registry_before/after` 为空是因为 `_safe_registry_snapshot` 在 execute 上下文里取不到 registry。
- **`_phase3e_check.py` 断言改为测"代码级默认"而非当前配置值**（原断言读 `fallback_config` 现值，
  我们临时把 `replay_auto_record` 改成 `full` 后自检就红）：改用打桩 loader 去掉该键再断言默认 `off`，
  另加一条"配置值 ∈ {off, code_only, full}"；重跑 **PASS=38 FAIL=0 WARN=0**。
- **`_p3_e2e.py` exit=99 根因**：任务账本是文件落盘（`services/task_queue/ledger.py:59 _data_dir()`，
  env `QMCLAW_TASK_DATA_DIR` 可覆盖），沙箱内 `services/task_queue/data/tasks/` **不可写** →
  task 建完就丢，后续全 `unknown task`（`GET /task/{id}/report` 返回 `{"actions":[]}` → runner `KeyError: 'actions'`）。
  用 `QMCLAW_TASK_DATA_DIR=D:/qmclaw/_task_data` 重启 :3009 后 **PASS=36 FAIL=0**。
- 全量自检：`run_all_checks.py --e2e` **24 套全 exit=0**（`_phase3e` 38 / `_phase4` 37 / `_phase5` 28 / `_p3_e2e` 36 …）。
- 未完成：`full` 模式下 registry 快照仍取不到（空 dict），因此**值级**漂移检测暂未真正生效；
  需在 execute 上下文里拿到 registry 后再补一次真机 fixture 录制。

**落地状态（2026-11-09 晚，阶段 5 收尾修正：回归结论不再是"弱结论"）**
- **根因**：`quantum_service/server.py` 的 `_safe_registry_snapshot()` 判 `payload.get("success")`，
  而 `_handle_qubit_registry()` 返回的是 `{"name","session_path","registry","source"}` —— **没有 `success` 字段**
  → 快照恒为 `None` → fixture 的 `registry_before/after` 全 `{}` → **值级比对从未发生**，
  之前报的 `verdict=green / drift=0` 是"没比过"而非"一致"。
  **已修**：改为 `if payload.get("error"): return None` + 取 `payload["registry"]`，非空才返回。
- **fixture 目录会被静默降级**：`ReplayStore._ensure_dir()` 写 `.probe` 探针失败就落到
  `tempfile.gettempdir()/qmclaw-replay-fixtures`，而报告里仍显示"请求目录" → 读写两侧可能不是同一批 fixture。
  **已改**：`ReplayStore.__init__` 支持 env `QMCLAW_REPLAY_FIXTURE_DIR`（`--dir` > env > 默认 `data/replay-fixtures`），
  新增 `degraded` 属性；`regression_from_directory()` 报告新增 `requested_directory` / `directory`（实际生效）/
  `degraded_store`，降级时附 note；`format_report()` 打印请求 vs 实际目录、降级警告与"弱结论"汇总。
- **弱结论显式化**：`classify_fixture()` 新增 `has_registry` 字段；无 `registry_after` 快照的用例附
  「fixture 没有 registry_after 快照：未做值级比对（无漂移 ≠ 一致）」，报告顶部汇总提示。
- 自检 `_phase5_check.py` 扩到 **PASS=37 FAIL=0 WARN=0**（新增：弱结论说明 / 报告三字段 / env 覆盖生效 / 报告含弱结论提示）。
- 实测：现有 9 条真机 fixture 全部 `has_registry=false` → 报告打出「9/9 条 fixture 没有 registry 快照（无漂移 ≠ 一致，属弱结论）」。
- 未完成：修好的 `_safe_registry_snapshot` 需要**重启 :3003** 才生效；重启后需再录一条 fixture 才能拿到带 registry 的样本
  （本轮 q2ld3 iqraw 仍被设备层 `prepare_dac_meas` divide TypeError 卡住）。

**落地状态（2026-11-09 21:23，阶段 5 收尾修正②：同源 bug 全面清账）**
- `_safe_registry_snapshot()` 的修复**暴露出第二个同源 bug**：`_refresh_device_profile()`（`quantum_service/server.py:787`）
  第 810 行同样用 `not payload.get("success")` 判失败 → 真机上每个比特都被记成"registry 读取失败"，
  画像刷新实际是坏的（此前自检的 stub 带了 `success` 键，所以假绿）。
  **已修**：改为 `payload.get("error")` 判失败 + 校验 `payload["registry"]` 是 dict，并把 `error` 文本带进错误信息。
- 自检 `_phase3e_check.py` 的 stub 改成**真实返回形状**（去掉 `success` 键，补 name/session_path/source），
  断言从「返回 success」改为「返回 registry 内容（`f10 == 3.6501`）」→ **PASS=38 FAIL=0 WARN=0**。
- 全量 `run_all_checks.py --e2e` → **24 套全 exit=0**（产物 `_checks/20261109_212325/`）：
  _phase3e 38 / _phase4 37 / _phase5 37 / _p3_e2e 36 / _phase2 97 …。
- 仍需**重启 :3003** 让两处修复在真机生效；重启后建议先 `GET /qubit/profile?refresh=1` 验证 `refreshed` 非空，
  再录一条 `replay_auto_record=full` 的成功实验以拿到带 registry 快照的 fixture。

**落地状态（2026-11-09 21:29，阶段 5 收尾修正③：GET 查询参数被丢弃）**
- 重启 :3003 后验证 `GET /qubit/profile?refresh=1`，响应里**没有 `refreshed` 字段** → 定位到第三处 bug：
  `quantum_service/server.py:526 handle_request(method, path, data, query)` 把 `query` **整个丢掉**，
  于是文档里写的 `?refresh=1` / `?name=q7ld3` 实际都不生效（`refresh` 恒为 False）。
  **已修**：路由前把 query 并入 data（body 同名字段优先，query 取 list 首元素）。
- 自检 `_phase3e_check.py` 新增第 5 组断言（`handle_request("GET","/qubit/profile",{},{"refresh":["1"],"name":["q7ld3"]})`
  → `refreshed` 含 q7ld3；只给 `name` → 返回 entry）→ **PASS=40 FAIL=0 WARN=0**。
- 重启 :3003（沙箱外 `danger-full-access`，Ray 握手在沙箱内会挂起）→ `GET /qubit/profile?refresh=1`
  返回 `refreshed` 12 个比特、`errors: []`、`snapshot_path` 落 `data/device_profile_snapshot.json`；
  `GET /qubit/profile?refresh=1&name=q7ld3` 返回该比特条目（f10 3.650111089742225 / f21 3.450111089742225 /
  fread 6.63282 / fc_read 6.843 / bias_z -1 / fc 4.08 / pi_gate_amp 0.9652409545368593 /
  pi_gate_alpha -0.5074020276102298 / xy_dac "DAC_LQ 761 AB" / z_dac "DAC_LQ 762 A" /
  readout_adc "ADC_LQ 760" / readin_dac "DAC_LQ 759 AB" / read_in_power -38.0 / read_out_amp 1.0 / timing_xy 1.85）。
- 全量 `run_all_checks.py --e2e` → **24 套全 exit=0**（产物 `_checks/20261109_212848/`）：
  _phase3e 40 / _phase4 37 / _phase5 37 / _p3_e2e 36 / _phase2 97 …。

**落地状态（2026-11-09 21:36，阶段 5 收尾修正④：读侧目录不再随写侧降级）**
- 现象：CLI 明明 `--dir` 指向工作区 `data/replay-fixtures/`，报告却说"12/12 条没有 registry 快照"，
  而那个目录里只有 6 条 —— **实际读的是 `%TEMP%\qmclaw-replay-fixtures`**。
- 根因：`ReplayStore._ensure_dir()` 写 `.probe` 探针失败即静默降级到临时目录，而这个降级
  **同时作用在读路径**（`load()` / `list_fixtures()`）；沙箱内 `Agentic Workflow/` 子目录只写不了 →
  读取也被带偏。同时 `regression_from_directory()` 另建 store 实例、从未触发降级 →
  `degraded=False`、报告里的目录仍是请求目录 → **既没告警又指错目录**。
- **已修**：
  - `services/common/replay.py` 新增 `read_directory` 属性：目录可读就直接读它；
    仅当**本实例已写过并降级**时才读降级目录（否则"存进去读不到"）；
    `load()` / `list_fixtures()` 改用它。
  - `services/common/regression.py`：`load_fixtures(..., store=None)` 支持外部 store，
    `regression_from_directory()` 复用**同一个** store 实例，`report["directory"]` 现在是
    **实际读取目录**，与 `requested_directory` 不一致时报告打 `!! 实际读取目录 ≠ 请求目录`。
  - `services/quantum_service/server.py`：新增 `_get_replay_store()`，整个服务共用一个
    `ReplayStore` 实例（`_handle_qubit_replay` 与 `_maybe_record_replay_fixture` 都用它），
    保证"刚录的 fixture 马上能回放"。
- 自检 `_phase5_check.py` 新增第 9 节（读目录与写目录解耦、同一 store 列目录与读内容一致、
  报告目录字段正确、不一致才告警）→ **PASS=42 FAIL=0**；`_phase3d_check.py` PASS=58；
  全量 `run_all_checks.py --e2e` → **24 套全 exit=0**（产物 `_checks/20261109_213633/`：
  _phase3d 58 / _phase3e 40 / _phase4 37 / _phase5 42 / _p3_e2e 36 / _phase2 97 …）。
- 真机验证：CLI 读请求目录正确读到 6 条（含 2 条带 registry 的新 fixture），
  verdict=green、drift=0、4/6 弱结论提示（4 条旧 fixture 无 registry 快照），exit=0。

---

**落地状态（2026-11-10，阶段 17：服务进程组）**
- 新增 `services/common/service_groups.py`：`SERVICE_PORTS`（10 个服务端口）、`SERVICE_ENTRYPOINTS`（可同进程启动的
  服务 → 模块/类）、`MERGE_BLOCKERS`（每个**不可**合并服务的理由：LabRAD/Ray 独占、import 副作用、线程池争用…）、
  `GROUPS`（`core` = workflow+task_queue 可合并；`lab` = quantum+analysis 不可合并）、
  `plan_group()` / `describe_groups()` / `conflicts()`（纯函数，无副作用）。
- 新增 `scripts/start_group.py`：`--list` / `--group core [--dry-run]`。`core` 在同一 Python 进程里用线程各起一个
  `ThreadingHTTPServer`，任一线程退出即停整组（退出码 1）；`lab` 组拒绝同进程并打印阻塞理由（退出码 2）。
  **端口号不变**，故调用方契约不变。
- 自检 `_phase17_check.py`（PASS=38）：端口表与 `scripts/start_all_services.py`、`config/services.json` 三方一致；
  端口唯一；`plan_group` 结果与定义一致；不可合并服务必须有理由；启动器不硬编码端口；`--dry-run` 不启动进程；
  未知组/`lab` 组退出码 2；文档在位。
- 诚实边界：这一步省的是"重启次数"，**没有减端口**。真正的端口合并要网关式路径前缀
  （`/workflows/*` 与 `/task/*` 同端口），会改前端与 Express 契约，需要单独排期与授权。

---

## 3. 已知问题清单（供后续排期，与本文件同源）

| # | 问题 | 证据 | 处置归属 |
|---|---|---|---|
| 1 | `:3003/execute` 的 `task_id` 可选 → 治理层可绕过 | 本轮 q7ld3 全程直发 | §2.1 + 阶段 0 止血 |
| 2 | 同一能力多处实现、语义不一致 | `:3004/plot` vs Express `/sessions/plot`（不透传 dataset_index）；registry vs `/qubits/:name/params`；切会话 3 处 | ✅ **已解**：切会话（阶段 8）、registry 读取（阶段 9）、`dataset_index` 透传（阶段 12，2026-11-10），见 OPERATIONS §3.11/§3.12/§3.15 |
| 3 | 真机可用性状态分散 | `:3003` stale/stuck vs `:3004` degraded_permanent | ✅ **已解（阶段 10，2026-11-10）**：`services/common/availability.py` 唯一判据，`get_health` 只喂字段、`_handle_health` 只剩委托，HTTP 码走 `health_status_code()`，见 `docs/OPERATIONS.md` §3.13 |
| 4 | 厂商语义风险 lint 拦不住 | 静默改寄存器、zpa 默认展开、`freq=None` 崩 | ✅ **已解（阶段 14，2026-11-10）**：`W_UPDATE_DEFAULT_ON`/`I_UPDATE_ON`（默认回写）、`W_PIDF_DF_SMALL`（`df` 窗口）、表驱动 `E_ARG_MISSING`；与 2026-11-09 的 `E_SPEC_NO_FREQ`/`W_ZPA2D_DEFAULT_ZPA`/`W_SWEEP_LARGE` 合起来覆盖已知厂商坑，见 OPERATIONS §3.17 |
| 5 | 领域判据无可版本化载体 | contrast 三版本；fread 逐比特；.pyd 遮蔽 | §2.5 → `config/lab_criteria.json` |
| 6 | 端口税 | 11 服务，改 `dsl/` 就要重启 :3008 | 🟡 **阶段 17（2026-11-10）已解一半**：`services/common/service_groups.py` + `scripts/start_group.py` 让 `workflow`+`task_queue` 同进程（一次重启起一组），端口不变；真正减端口需网关式路径前缀（契约变更，另议），见 OPERATIONS §3.20 |
| 7 | 错误语义多层并存 | degraded/stuck/quota_exceeded/needs_human/not_achieved/budget_exhausted | ✅ **已解（阶段 6，2026-11-09）**：规范状态 `{ok,degraded,queued,blocked,failed}` + `reasonCode` + `legacyStatus`，见 `docs/OPERATIONS.md` §3.8 |
| 8 | 可复现性缺口 | stdout 不打印数据集号，直发无 manifest | ✅ **已解（阶段 7，2026-11-09）**：DataVault 目录差集补数据集号 + `RunRecord.manifest` 程序清单，见 `docs/OPERATIONS.md` §3.10 |
| 9 | 自检覆盖不到真机/前端 | 288 断言全绿但不碰设备；`.env` key 空；`npm run build` 不可用 | §2.6 Replay |
| 10 | 约定文档漂移 | 解释器路径长期写错（今天才修） | 加 CI 断言：文档里的路径必须存在 |

### 落地状态（2026-11-09 22:25，阶段 6：统一状态语义 → §3 #7 #3 已收口）

- `services/common/errors.py` 重写：`CANONICAL_STATUSES = ("ok","degraded","queued","blocked","failed")`；
  每个错误码带 `legacy_status`（旧 status 原值，随信封以 `legacyStatus` 返回）；
  `error_envelope()` 输出 `{status, reasonCode, error_code, error, hint, retryable, http_status}`，
  未知码退化为 `internal_error` + `requested_code`；`normalize_error()` 幂等且**不改写执行生命周期状态**；
  `describe_codes()` 升 **1.1.0** 并附 `canonical_statuses`。
- **两套词表刻意分工**：失败语义用规范状态；进度语义（`success`/`busy`/`offline`/
  `offline_simulated`/`healthy`/`idle`/`running`/`completed`/`cancelled`/`rejected`/`pending`）
  保持不变 —— 前端 `qmclaw-web/src/app/page.tsx` 只认前四个，改了就白屏。
- 接入面：`quantum_service/server.py`（含 `/jobs` 的 unknown job、enforced writer、stuck、
  read_only、degraded、timeout、`/qubit/set_params` 与 `/qubit/apply_dataset`）、
  `quantum_service/writer.py`、`analysis_service/server.py`、`task_queue/ledger.py`、
  `task_queue/server.py`、`common/governance.py`；`handle_request` 统一走 `normalize_error`。
- 领域原因码与错误码分家：账本原 `reasonCode`（`no_ticket`/`partial_scope`/`out_of_scope`/
  `unknown_task`/`derived_keys`）改名 **`detailReasonCode`**。
- 消费方兼容：`agent_service/adapter.py`、`common/quantum_tools/execution.py` 的
  `read_only`/`stuck` 判断改为"规范状态或旧状态任一命中"；`qca_service/core/runner.py`
  同时认 `error` 与 `failed`（否则只读/卡死拒会被静默当成成功）。
- outcome 层：`dsl/executor.py` 的 `_compute_outcome()` / `_report()` 新增
  `canonicalStatus` + `reasonCode`，旧 `status` 全部保留。
- 自检：`_phase6_check.py` **PASS=54 FAIL=0**（含 5 个服务的裸字面量扫描）；
  离线 22 套全 exit=0（`_checks/20261109_222215`）。
- **未做（需用户授权窗口）**：`:3003/execute` 彻底 producer 化（只返 job_id，前端与
  Express 契约要一起改）；`:3003` 当前未监听，`_apply_dataset_ep_check.py` 属在线用例，
  待服务由用户启动后复跑。

---

### 落地状态（2026-11-10，阶段 14：厂商语义风险进 lint → §3 #4 收口）

- **表驱动，不抄第二份**：`services/workflow_service/dsl/lint.py` 新增
  `_vendor_requires(fn)`（读 `services/common/replay.py` 的 `VENDOR_SEMANTICS`）、
  `_as_flag` / `_as_float`（只解释字面量，认不出返回 None = 不判定）；
  默认回写判断直接复用 `services/workflow_service/dsl/backends/lqcs.py` 的 `WRITE_EFFECTS`。
- 三条规则（另加一条 info）：
  | 代码 | 级别 | 触发 |
  |---|---|---|
  | `W_UPDATE_DEFAULT_ON` | warning | `mode="default_on"` 的实验没写 `update` / 写成表达式 |
  | `I_UPDATE_ON` | info | 显式 `update=True`（确认"知道会写"） |
  | `W_PIDF_DF_SMALL` | warning | `sq.pidf` 缺 `df`，或 `0 < abs(df) < 0.05`（默认 0.03 只覆盖 ±30 MHz） |
  | `E_ARG_MISSING` | error | `VENDOR_SEMANTICS[fn]["requires"]` 未满足（`_REQUIREMENT_COVERED` 跳过专用规则已覆盖的 `sq.t1`/`zpa`、`sq.spectroscopy`/`freq`） |
- 边界：`mode="always"`（`sq.set_pi`/`sq.s21_dis`/`sq.iqcenter`）`update=False` 也照写 ——
  warning 消失不等于只读，仍由 `W_WRITE_FN` 与写闸门管；表达式参数一律不判定（宁可漏报不误报）。
- 自检 `_phase14_check.py` **PASS=64 FAIL=0**；已进 `run_all_checks.py` OFFLINE。
- 文档：`docs/OPERATIONS.md` §3.17 + §4 表行。

---

## 4. 在线升级协议（用户 m14826 硬要求）

> 目标：**用户继续做测控实验不受干扰；后台逐步升级。**

### 4.1 三类改动

| 类别 | 内容 | 是否需重启 | 生效时机 |
|---|---|---|---|
| **A. 热配置** | `config/*.json`（fallback_config / registry_bounds / lab_criteria / experiment_configs / registry_rollback_policy） | 否 | 每请求读取，改完即生效（`get_actuation_flags` 即此模式） |
| **B. 后台可验证的代码改动** | 服务源码、dsl 模块 | 是（一次窗口） | 编辑源码不影响运行中的进程；**验证在离线/影子路径完成**，等用户方便时统一重启 |
| **C. 进程内热插** | 通过 `:3003/execute` 注入的模块/新实验封装 | 否 | 需先有 B 类的一次性重启把"注入机制"做进去 |

### 4.2 铁律

1. **绝不 kill 正在跑的 `:3003` / `:3008` / `:3009`**，除非用户在明确窗口授权。
2. **绝不从后台脚本 `import lqms` 直连 LabRAD**；LabRAD/Ray 客户端全机只能有一个（实测第二个会卡在
   `Connecting to existing Ray cluster at 192.0.2.11:6479`）。
3. 后台验证一律走**离线/影子路径**：影子实例只做 HTTP 契约与纯函数自检，**不连 LabRAD**。
4. 每一次改动都要有"可回退点"：改动前 `git commit`，改动后 `git commit`。
5. 用户的真机实验优先级高于后台升级；后台动作不得触发任何硬件下发。

### 4.2.1 真机实验前置授权协议（用户 m14993 硬要求）

> 用户原话："接下来的升级我希望不要打断我这边的实验，你可以先做代码上的升级，需要做真机实验的时候向我提问，
> 我回复提问之后你就可以做测控实验了，这样就不影响。"

| 步骤 | 约定 |
|---|---|
| 1. 代码级优先 | 后台默认只做**离线/影子**改动与自检（`_phase2_check.py` 这类不连设备的套件），不触发任何硬件下发 |
| 2. 提问前置 | 任何**需要真机测控实验**的步骤（跑 `sq.*`、写寄存器、扫频、占用 hardware lane）必须**先向用户提问**，等用户回复授权后才执行 |
| 3. 提问内容 | 说明：做什么实验、目标比特与参数、预计耗时、会不会改寄存器（写哪些键）、如何回滚；用户可直接回复"可以/否/改参数" |
| 4. 未授权即不执行 | 没等到回复就一直等，不擅自下发；用户正在跑实验时（hardware lane 忙）同样先问 |
| 5. 改写寄存器需单独确认 | 写 `f10` / `fread` / `PiGate.*` / `bias_z` 等会影响后续实验的键，属于"会改现场状态"，必须在提问中显式说明并单独获得许可 |
| 6. 影子先行 | 能用影子/离线验证的（如能力探测、错误码归一、端口委派），一律不占真机通道 |

已发生的授权请求示例：①`/qubit/apply_dataset` 把 q7ld3 的 `bias_z: -1 → -2.8` 写回（dry-run 已确认唯一差异）；
②阶段 2 / 3 收官各需一次真机小实验（由用户定时）。

### 4.3 需要用户一次性重启才生效的清单（滚动累积，只重启一次）

| 项 | 所在服务 | 状态 |
|---|---|---|
| `POST /qubit/apply_dataset` | :3003 | 代码已就绪，待重启 |
| `:3003/execute` 的 `require_task` 闸门 | :3003 | 代码已就绪，配置默认关，重启后可热开 |
| lint 增规则（写前快照检测、扫描点数上限、`freq=None`、强制 `do_plot=False`） | :3008 | 离线 CLI 可验，服务器待重启 |
| `POST /workflows/manifest` 等 P3 端点 | :3008/:3009 | 已就绪，待重启 |

### 4.4 阶段划分

- **阶段 0（无需重启，后台可做）**：判据固化（`config/lab_criteria.json`）、skill 更新、require_task 代码准备、
  lint 规则离线验证、RunRecord 规格定义、Roadmap 记录。← 当前阶段
- **阶段 1（需一次重启窗口）**：:3003 的 require_task / apply_dataset 生效、:3008 lint 生效、Express 代理补齐。
- **阶段 2**：单写者落地（`:3003/execute` 降级为 producer）、错误语义统一、重复能力收拢到单一入口。
- **阶段 3**：Qubit Port + RunRecord 落地，Capability Profile 落地。
- **阶段 4**：比特状态机 + Metric Registry 接入 goal 判定。
- **阶段 5**：Replay 层 + CI 覆盖真机链路。

---

## 5. 与既有文档的关系

- 微服务端点全表与调用规范：`skills/qmclaw-service-endpoints/SKILL.md`
- 指标判据唯一真源：`config/lab_criteria.json`
- 运维与端口：`docs/OPERATIONS.md`
- DSL 设计细节：`docs/DSL_DESIGN.md`
- 进度流水：`.claude/progress.md`
