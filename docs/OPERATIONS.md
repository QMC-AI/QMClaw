# OPERATIONS — QMClaw 日常运维手册

> 面向"要跑一次实验 / 要重启服务 / 出故障了怎么办"的人。
> 设计说明见代码注释与 `ADAPTER_GUIDE.md`，术语表见该文档附录。

---

## 0. 端口表

| 端口 | 服务 | 作用 | 备注 |
|---|---|---|---|
| 3002 | Express (qmclaw-server) | `/sessions/plot` 等 | **不透传 `dataset_index`**，只能画"最新"数据集；要画指定数据集直连 3004 |
| 3003 | quantum_service | `/execute` `/health` `/qubit/params` `/qubit/registry` `/qubit/set_params` `/qubit/apply_dataset` `/qubit/capabilities` `/qubit/probe_transition` `/qubit/runs` `/qubit/assess` `/qubit/writer` `/qubit/profile` `/qubit/replay` | 真机执行；实验**必须** `do_plot=False` |
| 3004 | analysis_service | `/connect` `/plot` `/plot/online/v2` | 绘图与拟合都归它 |
| 3008 | workflow_service | `/workflows/parse_qwf` `/workflows/validate` `/workflows/lint` `/workflows/manifest` | DSL 执行主体 |
| 3009 | task_queue | `/task*`（额度、清单、票据、审计报表） | P3 治理层 |

---

## 1. 启停（**由人操作**，AGENTS.md 第 3 条）

```powershell
# 1) 停旧进程（逐个查 PID 再杀）
Get-NetTCPConnection -LocalPort 3003,3004,3008,3009 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique
taskkill /PID <PID> /F

# 2) 起全套
python "Agentic Workflow\qmclaw-server\scripts\start_all_services.py"
```

启动脚本已设 `MPLBACKEND=Agg`（`scripts/start_all_services.py`），避免 matplotlib 拉起 Qt GUI 把服务卡死。

> **改了 `services/workflow_service/dsl/` 或 `services/task_queue/` 必须重启 :3008/:3009**，否则跑的还是旧代码。

---

## 2. 真机写闸门

默认**关闭**。要跑真机实验/改寄存器，人工开：

```jsonc
// Agentic Workflow/qmclaw-server/config/fallback_config.json
"quantum_service": { "actuation": { "allow_execute": true, "allow_set_params": true } }
```

或设环境变量 `QMCLAW_ALLOW_EXECUTE=1` / `QMCLAW_ALLOW_SET_PARAMS=1`，然后重启服务。
闸门只在服务端强制，agent 侧只是提前解释。

---

## 3. 跑一次实验

### 3.1 传统方式（单个实验）

```powershell
# 1) 体检命令本身（可选）
python D:\qmclaw\docs\deploy_diff_tools\verify_experiment_commands.py q2ld3

# 2) 通过微服务下发（不要直连 LabRAD）
curl.exe -X POST http://127.0.0.1:3003/execute -H "Content-Type: application/json" `
  -d '{"command":"sq.iqraw(q2ld3, do_plot=False)","timeout":120}'
```

### 3.2 DSL 方式（推荐，带额度与票据）

```powershell
cd D:\qmclaw

# 干跑（不碰硬件）
python qwf_lint.py llm_multi_exp.qwf          # 离线体检，有 error 就先改
python qwf_run.py llm_multi_exp.qwf --dry --if yes

# 真机：建 task + 签票 + 跑
python qwf_run.py llm_multi_exp.qwf --create-task --experiments 5 `
       --sign-scope auto --if yes
```

`--sign-scope` 语法：`auto`（用写清单自带幅度上界）或 `f10:0.05,PiGate.amp:0.1`。
只有派生键（`discriminator.*`）时不需要票据，会自动跳过签票。

### 3.3 把实验数据集里的寄存器快照写回比特（`/qubit/apply_dataset`）

厂商实验（`s21_zpa2d` / `iqraw` / `pidf` …）会**静默改寄存器**（s21 的 auto-update 回写
`fread`、宽频 pidf 把 `f10` 写到错误频点），而实验数据集的 `parameters` 里留有当时的完整
registry 快照（键形如 `q7ld3.f10`）。本端点读回快照、与当前 registry 比对后**只写回有差异的键**。

```powershell
# 只看差异，不写（dry_run）
curl -s -X POST http://127.0.0.1:3003/qubit/apply_dataset `
     -H "Content-Type: application/json" `
     -d '{"name":"q7ld3","dataset":1032,"dry_run":true}'

# 真写回（受写闸门 allow_set_params 约束）
curl -s -X POST http://127.0.0.1:3003/qubit/apply_dataset `
     -H "Content-Type: application/json" `
     -d '{"name":"q7ld3","dataset":1032}'

# 也要写回 discriminator.*（默认跳过，它是测量拟合产物，iqraw 会自动重算）
curl -s -X POST http://127.0.0.1:3003/qubit/apply_dataset `
     -H "Content-Type: application/json" `
     -d '{"name":"q7ld3","dataset":1032,"include":["discriminator."]}'
```

请求体：`name`（比特名）、`dataset`（数据集编号）、`skip`（默认 `["discriminator."]`）、
`include`（强制写回的前缀，覆盖 skip 且即使值相同也重写）、`dry_run`。
返回：`compared` / `to_apply` / `unchanged` / `skipped` / `changes{键: {from,to}}` /
`updated` / `verified` / `mismatch`。

### 3.4 能力探测、跃迁探测与运行记录（阶段 2 新增）

| 端点 | 方法 | 作用 |
|---|---|---|
| `/qubit/capabilities` | GET | 静态 AST 探测 lqms 能力（不 import、不连设备）：哪些 `.pyd` 遮蔽了更新的 `.py`、源码里有而运行时没有的函数（如 `spectroscopy_iq` / `time_rabi` / `iqcenter`） |
| `/qubit/probe_transition` | POST | 扫频找比特跃迁（默认 `method="pidf"`），强制 `do_plot=False`、**`update=False`**（防厂商静默回写 `f10`/`fread`） |
| `/qubit/runs` | GET | 读 RunRecord 执行台账（每次 `:3003/execute` 落一条 jsonl，含 `run_id` / `governed` / `dataset_ids` / `program_hash` / `metrics` / `verdict`；`replay_auto_record=full` 时还含 `snapshot_before` / `snapshot_after` / `diff`） |
| `/qubit/assess` | POST | **纯计算**：由证据（contrast / t1_us / t2_star_us / xeb_p…）判定比特状态与指标达标情况，**不下发任何实验** |
| `/qubit/writer` | GET | 单写者状态：当前模式（`off`/`shadow`/`enforce`）、队列深度、影子观测统计（哪些操作"本应经由单写者"） |
| `/qubit/profile` | GET | 设备画像（Capability Profile）：读 `config/device_profile.json`，给出各比特的 DAC/ADC 通道、`f10`/`f21`/`fread`、`bias_z`、π 脉冲参数与缺失项；**只读文件，不连设备** |
| `/qubit/replay` | POST | 回放层（厂商语义仿真 + fixture 录制/回放）：`action=simulate` 离线推演一段实验代码会改哪些寄存器、会不会报错；`action=record` 录制 fixture；`action=list` 列出 fixture；`action=replay` 回放并检出与仿真的漂移；**不连设备、不受写闸门影响** |

```powershell
# 能力探测（离线、无副作用）
curl -s http://127.0.0.1:3003/qubit/capabilities

# 扫频找跃迁（会占用真机通道，执行前请先与用户确认）
curl -s -X POST http://127.0.0.1:3003/qubit/probe_transition `
     -H "Content-Type: application/json" `
     -d '{"name":"q7ld3","df":0.3,"method":"pidf"}'

# 读最近 20 条运行记录
curl -s "http://127.0.0.1:3003/qubit/runs?limit=20"

# 纯计算：由已有证据判定比特状态（不碰硬件，可随意调用）
curl -s -X POST http://127.0.0.1:3003/qubit/assess `
     -H "Content-Type: application/json" `
     -d '{"name":"q2ld3","evidence":{"freq_locked":true,"contrast":0.765,"t1_us":72.2,"t2_star_us":6.58,"xeb_p":0.9965,"xeb_gate_fidelity":0.9973}}'

# 单写者状态（离线、无副作用）
curl -s http://127.0.0.1:3003/qubit/writer

# 设备画像（只读文件，不碰硬件）
curl -s http://127.0.0.1:3003/qubit/profile
curl -s "http://127.0.0.1:3003/qubit/profile?name=q7ld3"

# 回放层：离线推演一段实验代码会改哪些寄存器、会不会报错（不碰硬件）
curl -s -X POST http://127.0.0.1:3003/qubit/replay `
     -H "Content-Type: application/json" `
     -d '{"action":"simulate","code":"sq.pidf(q7ld3, df=0.05, do_plot=False, update=False)","registry":{"f10":3.65,"f21":3.45}}'

# 列出已录制的 fixture / 回放并检出漂移
curl -s -X POST http://127.0.0.1:3003/qubit/replay -H "Content-Type: application/json" -d '{"action":"list"}'
curl -s -X POST http://127.0.0.1:3003/qubit/replay -H "Content-Type: application/json" -d '{"action":"replay","fixture_id":"fx-xxxxxxxxxxxx"}'

# 设备画像：按当前设备状态刷新（只读 registry，不下发实验；不改 config 目录）
curl -s "http://127.0.0.1:3003/qubit/profile?refresh=1"
```

说明：
- **单写者（`/qubit/writer`）**：`quantum_service.actuation.writer_mode` 三档
  `off` / `shadow`（默认，只观测不改变执行路径）/ `enforce`（实验真正交给唯一 worker 线程串行执行）；
  每请求读取 → **热生效**；环境变量 `QMCLAW_WRITER_MODE` 优先于配置文件。
  影子期用 `/qubit/runs` 的 `governance` 字段看治理覆盖率（`ungoverned>0` 即存在绕过 task_queue 的直发执行）。
- `/qubit/assess` 的判据全部来自 `config/lab_criteria.json`（指标唯一真源），阈值改动只改该文件；
  状态机（`unknown → freq_locked → amp_calibrated → state_discriminated → coherence_measured → characterized`）
  在 `services/common/qubit_state.py`，返回体含 `state` / `reasons` / `missing` / `next_action` / `metrics`。
- `probe_transition` 的 `method` 限 `pidf` / `spectroscopy` / `iqcenter`；能力缺失时返回
  `not_found`，提示"重启服务无效：需实验室重新编译 lqms 扩展"。
- `/qubit/profile` 的数据源是 `config/device_profile.json`，由 `D:\qmclaw\_gen_device_profile.py`
  生成（只读 `POST /qubit/registry`，**不下发任何实验**）。用 `--dry-run` 先看差异；
  若目标目录不可写，脚本会改写到 `D:\qmclaw\_device_profile.json` 并提示手动拷贝。
- **fixture 自动录制（`quantum_service.actuation.replay_auto_record`）**：三档
  `off`（默认，不录）/ `code_only`（只记代码、比特、数据集号、trace_id，不读寄存器）/ `full`（额外采 registry 前后快照）。
  每请求读取 → **热生效**；环境变量 `QMCLAW_REPLAY_AUTO_RECORD` 优先于配置文件。
  `full` 会多两次 LabRAD 读，**正在跑实验时建议保持 `off` 或 `code_only`**；录制失败一律吞掉，不影响实验结果。
- **画像自动刷新（`GET /qubit/profile?refresh=1`）**：按画像里的比特逐个只读 `POST /qubit/registry`，
  用设备当前值覆盖旧值写回内存中的画像，并落一份快照到 `<server>/data/device_profile_snapshot.json`
  （data 目录不可写时降级到 `%TEMP%\qmclaw-device-profile\`）；**不改 `config/device_profile.json`**。
  不带 `refresh=1` 时仍是纯读文件，不连设备。
- **`/qubit/registry` 的成功判据**：成功时返回 `{"name","session_path","registry","source"}`，**没有 `success` 字段**
  （失败时才有 `error`）。凡是判断"读到了没有"，一律用 `payload.get("error")` / `payload["registry"]`，
  不要用 `payload.get("success")`——2026-11-09 修掉了两处同源 bug：
  `_safe_registry_snapshot()`（导致 fixture 的 `registry_before/after` 恒为 `{}`、值级漂移检测从未发生）与
  `_refresh_device_profile()`（导致真机上画像刷新全部被当成"registry 读取失败"，profile 永不更新）。
  两处修复都需要**重启 `:3003`** 才生效。
- **GET 查询参数会并入请求体**：`handle_request` 会把 query（`?refresh=1&name=q7ld3`）合并进 data，
  body 里的同名字段优先。所以 `GET /qubit/profile?refresh=1` 里的 `refresh` 确实生效，
  `GET /qubit/profile?refresh=1&name=q7ld3` 还能只回填并返回该比特条目。
  2026-11-09 修掉了这处 bug：此前 `query` 整个被丢弃，`refresh` 恒为 False，文档写的用法实际无效。
- `:3003/execute` 的响应现在带 `trace_id`（调用方可自带 `trace_id` / `traceId` 或请求头 `X-Trace-Id` 透传；
  未透传时由服务端生成并在 `trace_hint` 里说明），并落入 RunRecord 台账，可与 `/qubit/runs` 对账。
- `program`（执行 IR）**不在**端口层直接提供，避免绕过治理层；IR 请走 `:3008/workflows/run_ir`。
- 端口层的写操作仍受 `quantum_service.actuation` 的 `allow_execute` / `allow_set_params` 闸门约束。

### 3.5 traceId 全链贯通（阶段 3 第二批）

一条 `trace_id` 从 Express 贯穿到 RunRecord 台账，排查任何一次实验都能按它串起来。

| 环节 | 位置 | 行为 |
|---|---|---|
| Express 入口 | `src/services/serviceProxy.ts` | 最前置中间件生成或透传 `X-Trace-Id`，回写响应头；`AsyncLocalStorage` 保存当前 trace |
| 基础层 | `services/base/base_service.py` | `_inbound_trace()` 读取入站 `X-Trace-Id`（白名单 `A-Za-z0-9-_.:`，截断 64）；GET 注入 `query["trace_id"]`、POST 注入 `data["trace_id"]` |
| workflow | `services/workflow_service/server.py` | `_handle_run_ir` 用 `ensure_trace` 取 trace 并写入 `context["trace_id"]`；`_start_executor` 同步 `self._lqcs.trace_id`；响应回显 `trace_id` |
| DSL 后端 | `services/workflow_service/dsl/backends/lqcs.py` | 队列 `/submit` 的 metadata 带 `traceId`；直连 `:3003/execute` 的 body 带 `trace_id` 且带 `X-Trace-Id` 头 |
| task_queue | `services/task_queue/server.py` | 提交时存入 `Task.metadata["traceId"]`；`/task/{id}/status` 回显；`/tasks?trace_id=` 过滤 |
| quantum | `services/quantum_service/server.py` | `:3003/execute` 响应带 `trace_id` 并写入 RunRecord；`/qubit/runs?trace_id=` 过滤 |

```powershell
# 手动指定一条 trace（Express 会透传，服务端不再生成）
curl -s -X POST http://127.0.0.1:3003/execute `
     -H "Content-Type: application/json" -H "X-Trace-Id: t-mystudy-001" `
     -d '{"code":"sq.iqraw(q2ld3, do_plot=False)","timeout":300}'

# 按 trace 反查执行台账 / 任务
curl -s "http://127.0.0.1:3003/qubit/runs?trace_id=t-mystudy-001"
curl -s "http://127.0.0.1:3009/tasks?trace_id=t-mystudy-001"
```

透传优先级 = 请求头 `X-Trace-Id` > body `trace_id` > body `traceId` > 服务端生成（生成时形如 `t-<12hex>`）。

### 3.6 goal 的阈值取自 Metric Registry（阶段 4）

`goal.success` 里除了 `value`，还可以写 **`criteria` + `level`**，阈值由
`config/lab_criteria.json` 提供（唯一真源，改阈值只改那一个文件，不必改 qwf）：

```qwf
goal {
  objective "把态区分对比度拉到 0.7 以上"
  success {
    metric   r1.contrast
    operator >=
    criteria contrast      # registry 指标名
    level    good          # 档位：good / weak_but_usable / unusable / min
  }
  max_rounds 3
}
```

- `criteria` 省略 `level` 时默认 `min`（= `contrast` 的 `unusable` 档 0.2）。
- 显式写了 `value` 就以 `value` 为准，但仍会记下 `criteriaRef`（形如 `contrast.good`）
  供核对；outcome 的 `detail.criteriaRef` 里能看到。
- `criteria` 指向未知指标、或 `level` 不是合法档位 → 校验报 `E_GOAL_CRITERIA`（不碰真机）。
- 可用档位：`contrast`（unusable / weak_but_usable / good）、`dmu_sig`（ambiguous / alive）、
  `t1`·`t2_star`（min_us）、`xeb`（min_p / min_gate_fidelity）、`s21_dip`（min_depth_db / offset）。
- outcome 里新增 `qubit_state` 字段：把本次运行的指标折成比特标定阶段
  （`unknown → freq_locked → amp_calibrated → state_discriminated → coherence_measured → characterized`），
  带 `reasons` / `missing` / `next_action`，便于判断"下一步该做什么"。

### 3.7 回放回归：CI 覆盖真机链路（阶段 5）

真机 fixture 里同时记着"厂商实验实际改了什么"和"我们以为它会改什么"，
两者之差就是**厂商语义漂移**。阶段 5 把这个比对做成可批量跑的回归：

```powershell
# 读默认 fixture 目录（services/common/replay.py 的 ReplayStore 目录）
D:\anaconda3\envs\lqcs\python.exe D:\qmclaw\_replay_regression.py

# 指定目录 / 限制条数 / 输出 JSON / 允许 N 条漂移
D:\anaconda3\envs\lqcs\python.exe D:\qmclaw\_replay_regression.py --dir D:\tmp\fx --limit 20 --json
D:\anaconda3\envs\lqcs\python.exe D:\qmclaw\_replay_regression.py --max-drift 2
```

- 结论 `verdict`：`green`（仿真与真机一致）/ `drift`（检出漂移）/ `broken`（fixture 损坏）/
  `empty`（**还没有真机 fixture，回归未覆盖真机链路，不等于通过**）。
- 退出码：`0` 通过；`1` 检出漂移或 fixture 损坏；`2` 运行错误。
- 漂移分三类：`recorded_only`（真机改了、仿真没预测到）、`simulated_only`（仿真以为会改、真机没改）、
  `value_mismatch`（两边都改了但值不同）。
- fixture 来源：`:3003/execute` 自动录制（`quantum_service.actuation.replay_auto_record`
  = `off` / `code_only` / `full`，见 §3.4），或 `POST :3003/qubit/replay {"action":"record"}` 手工录。
- **现在已经有真机 fixture 了**：2026-11-09 之前几轮真机实验已自动录到
  `Agentic Workflow/qmclaw-server/data/replay-fixtures/`（例如 `fx-*.json`，带真实 `dataset_ids`），
  跑 `_replay_regression.py` 得到 `verdict=green`、drift=0 —— 说明"厂商实验实际改了什么"
  与"回放层以为它会改什么"目前一致。
- 录 fixture 的正确姿势（2026-11-09 实测）：
  1. `fallback_config.json` 的 `quantum_service.actuation.replay_auto_record` 临时改成 `full`
     （**只在你自己的实验间隙改**，常开会给每次实验多读两次 LabRAD registry）；
  2. 跑一次实验 → 服务端自动落 `fx-*.json`；
  3. 立刻把该项改回 `off`（默认值）；
  4. `D:\anaconda3\envs\lqcs\python.exe D:\qmclaw\_replay_regression.py` 看 verdict。
- 设备层报错时**照样会落 fixture，但 `dataset_ids` 为空、status=error** —— 这种 fixture
  不含真机数据，回归只能验证代码路径，不能当"真机覆盖"。
- **fixture 目录用环境变量钉住**：`QMCLAW_REPLAY_FIXTURE_DIR`（读写两侧都认；`--dir` 优先于它）。
  `ReplayStore` 在目标目录不可写时会**静默降级**到系统临时目录（`%TEMP%\qmclaw-replay-fixtures`），
  于是"服务端写的"和"CLI 读的"可能根本不是同一批 fixture。回归报告因此会同时给出
  `requested_directory`（请求的）、`directory`（实际生效的）与 `degraded_store`（是否降级），
  降级时报告会打出 `!! 目标目录不可写…` 的警告。排查时先看这三项。
- **"无漂移"可能是弱结论**：fixture 没有 `registry_after` 快照时，**值级比对根本没发生**，
  `drift=0` 只说明"没比过"。这类用例会被打上 `has_registry=false` 并附
  「未做值级比对（无漂移 ≠ 一致）」，报告顶部也会汇总提示"弱结论"。
  2026-11-09 已修 `quantum_service/server.py` 的 `_safe_registry_snapshot()`（原来错判
  `payload["success"]`，而 `/qubit/registry` 根本不返回该字段 → 快照恒为空），
  修完需要**重启 :3003** 且重新录一条 fixture 才能拿到带 registry 的样本。
- **读侧不再跟着写侧降级**（2026-11-09 修）：以前 `load()` / `list_fixtures()` 也走写探针，
  于是"目标目录不可写"会把**读取**也带到临时目录，而报告里显示的还是请求目录 ——
  结论指向另一批 fixture。现在读侧用 `ReplayStore.read_directory`：
  目录**可读**就直接读它（写不动只影响写）；只有本实例**已经写过并降级**时才读降级目录
  （否则会出现"存进去读不到"）。服务端整进程共用一个 `ReplayStore` 实例
  （`_get_replay_store()`），保证"刚录的 fixture 马上能回放"。
  报告的 `directory` 字段现在是**实际读取目录**，与 `requested_directory` 不一致时会打出
  `!! 实际读取目录 ≠ 请求目录` 警告。

### 3.8 统一状态语义：canonical status + reasonCode（阶段 6）

以前同一个"被拒/卡住/超时"在不同服务里有十几套 status 写法（`read_only`、`stuck`、
`degraded_permanent`、`quota_exceeded`、`needs_human`、`not_authorized`…），调用方只能
按字符串猜。阶段 6 起分两套词表，**谁都别混用**：

**① 规范状态（失败/终态语义）**：`ok` / `degraded` / `queued` / `blocked` / `failed`

| 规范状态 | 含义 | 典型 reasonCode |
|---|---|---|
| `ok` | 成功 | — |
| `degraded` | 服务在但能力降级（LabRAD 掉了、设备卡死） | `degraded` / `stuck` / `not_connected` |
| `queued` | 已受理、还没结果 | — |
| `blocked` | 需要人/需要授权，重试无用 | `task_required` / `not_authorized` / `read_only` / `needs_human` / `quota_exceeded` |
| `failed` | 真失败，可重试 | `timeout` / `validation_error` / `not_found` / `internal_error` |

**② 执行生命周期（进度语义，原样保留）**：`success` / `busy` / `offline` /
`offline_simulated` / `healthy` / `idle` / `running` / `completed` / `cancelled` /
`rejected` / `pending`。前端的 `qmclaw-web/src/app/page.tsx` 只消费
`success` / `busy` / `offline` / `offline_simulated`，所以**这些值一个都不能改**。

**错误信封（`services/common/errors.py: error_envelope()`）**

```json
{
  "status": "blocked",              // 规范状态，五种之一
  "reasonCode": "not_authorized",   // 机器可判的原因码（= error_code）
  "error_code": "not_authorized",   // 兼容旧字段
  "error": "缺少授权票据…",          // 给人看的
  "hint": "请先在 :3009 为该写清单申请 ticket",
  "retryable": false,
  "http_status": 403,
  "legacyStatus": "not_authorized"  // 旧 status 原值（≠ status 时才出现）
}
```

- 未知错误码不会抛异常：退化成 `internal_error`，并附 `requested_code` 便于排查。
- `normalize_error(payload)` 幂等：已是信封就原样返回；生命周期状态（`success` 等）
  一律不改写；不会覆盖领域自带的原因码。
- `describe_codes()` 现在是 **1.1.0**，额外返回 `canonical_statuses`。

**领域原因码挪到 `detailReasonCode`**：任务账本原本用自己的 `reasonCode`
（`no_ticket` / `partial_scope` / `out_of_scope` / `unknown_task` / `derived_keys`），
和信封的 `reasonCode` 语义冲突。现在统一：`reasonCode` = 错误码，
领域原因在 `detailReasonCode`。仓库内没有别的消费者，旧断言读不到时会退化成
"要么 reasonCode 要么 detailReasonCode"。

**workflow / goal outcome**：`_compute_outcome()` 与 `_report()` 在保留旧 `status`
（`completed` / `achieved` / `not_achieved` / `needs_human` …）的同时，新增
`canonicalStatus` 与 `reasonCode`。映射：`completed`/`achieved` → `ok`；
`failed`/`error`/`not_achieved`/`cancelled` → `failed`；
`needs_human`/`quota_exceeded`/`budget_exhausted`/`budget_exceeded` → `blocked`。

**升级到新代码后要重启哪些服务**：`:3003`（quantum）、`:3004`（analysis）、
`:3008`（workflow）、`:3009`（task_queue）都改了错误路径，**全部需要重启**才生效。

---

### 3.9 LLM 决策的 key / 模型名 / 缺失时怎么退化

`decide` 节点的两家供应商都从 **`Agentic Workflow/qmclaw-server/.env`** 读 key
（不进 git，换机器要手工拷）：

| 配置名（`config/model_configs.json`） | key 环境变量 | 协议 |
|---|---|---|
| `llm_decision` | `OPENAI_API_KEY` | openai 兼容 |
| `llm_decision_minimax` | `MINIMAX_API_KEY` | anthropic 兼容 |

- **模型名是可配置的**，别写死在代码/自检里：供应商下线模型（例如 `MiniMax-Text-01`
  返回 2069）时只改 `model_configs.json`，代码不动。
- **缺 key 的退化路径**：`decide` 节点拿不到内容 → 绑定里 `choice` 为空 →
  outcome 变 `needs_human`（规范状态 `blocked`），工作流停在那儿等人，
  **不会崩、也不会瞎跑实验**。
- **自检里的 SKIP 是什么意思**：`_p2_llm_check.py` 第 3 节真的打外网。没有 key、
  供应商抽风、模型下线时它记 `SKIP` 而不是 `FAIL` —— 离线套件必须能断网跑，
  外部依赖变红不代表我们的代码坏了。看到 SKIP 先查 `.env` 和供应商状态。
- 离线静态检查在 CI（`.github/workflows/checks.yml`）：语法编译 + 文档漂移 +
  产品 `.qwf` lint，**不连设备、不起服务**。

### 3.10 数据集归属与直发侧程序清单（阶段 7）

**要解决的问题**：`sq.iqraw` / `sq.pidf` / `sq.t1` 这类实验只把数据写进 DataVault，
stdout 里**不打印**数据集号，于是 `RunRecord.dataset_ids` 一直是空的——事后没法回答
"这个结论出自哪个数据集"。

**做法**（`services/common/dataset_ids.py` + `services/common/program_manifest.py`）：

1. **两条互补来源**：仍然解析 stdout 里的 `dataset: N`；同时在 `:3003/execute`
   真正下发实验**前后各列一次 DataVault 目录**（`cxn.data_vault.dir(session)`，
   单次只读、毫秒级），差集即本次新增的数据集。
2. **宁可少记，绝不乱记**：任何一侧探测失败（连接断、session 不对、异常）都返回
   `None` → 本次不记编号，而不是把上次遗留的数据集算成这次的产出。
3. **来源可追溯**：`RunRecord.dataset_source` 取值 `stdout` / `datavault` /
   `stdout+datavault` / `unknown`，响应里同样回带 `dataset_ids` 与 `dataset_source`。
4. **程序清单**：直发也有 manifest —— `RunRecord.manifest` 里是
   `{version, program_hash, qubits, functions, calls[], auto_writes[], warnings[]}`，
   每条 call 用 `services/common/replay.py` 的厂商语义表补齐
   "会不会静默回写寄存器""缺哪个必填参数会崩"。清单是**确定性**的（不含时间戳），
   同码同输出，便于比对与回放。

**关掉探测**：`set QMCLAW_DATASET_PROBE=0` 后重启 `:3003`（默认开启）。
探测只在 LabRAD 已连接时生效；离线模拟模式不受影响。

**真机订正（2026-11-10）**：LabRAD 的 `data_vault.dir()` **不接受 session 参数**。
首版实现传 `dir(session)`，LabRAD 静默忽略、列出的是连接根目录 `['']`，
于是差集永远为空、`dataset_ids` 一直缺号（真机 iqraw 实测 `dataset_source=unknown`）。
现在改由 `services/common/dataset_ids.py` 的 `read_session_dataset_numbers(cd, dir, session)`
处理：先 `cd("")` 回根、再 `cd(session 去掉前导空段)`、最后无参 `dir()` ——
与 `labrad_client.py` 连接时的做法一致，全程只读。真机连接上实测返回 2668 个数据集。
**该修复需重启 `:3003` 才生效。**

**重启后实测（2026-11-10 09:24）**：`sq.iqraw(q4ld3, do_plot=False, update=False)` 3.2 s 成功，
响应与 RunRecord 都是 `dataset_ids: [2669]`、`dataset_source: "datavault"`，
`manifest.functions == ["iqraw"]`、`auto_writes == ["discriminator.center0",
"discriminator.center1", "fread"]`。`/qubit/params` 同步验证 `params_source == "registry"`（28 键）。
即：**三条来源里的 DataVault 差集在真机上确实补上了 stdout 不打印的数据集号**。

**仍然存在的边界**：目录差集只能归属"本次执行新增"的数据集。如果实验内部另起
DataVault 子会话或写别处，编号仍会缺失 —— 那时以 `dataset_source` 判断可信度，
不要假定 `dataset_ids` 一定完整。

### 3.11 会话路径的唯一实现（阶段 8）

**要解决的问题**（roadmap §3 #2/#3）：同一个"会话路径"能力散在四处——

- `quantum_service/server.py` 的 `/connect` 与 `/switch_session`、`analysis_service/server.py`
  的 `/switch_session` 各自 `json.loads(session_path)`：传一个坏字符串就抛
  `JSONDecodeError`，接口回 500 而不是一条可读的校验错误；
- analysis 的 `/connect` 干脆**忽略**请求里的 `session_path`，只能读配置；
- "读 `config/session.json` → `['', user] + path`，缺文件回
  `['', 'LQHL', 'test', '20260324']`" 在 `labrad_client.py` 与
  `analysis_service/server.py` 各有一份逐字重复，改一处就会漂。

**现在**：唯一实现在 `services/common/session.py`（纯标准库，不连 LabRAD）。

| 函数 | 作用 |
|---|---|
| `parse_session_path(value)` | 接受列表 / 元组 / JSON 字符串 / `LQHL/test/2025`；返回 `(path, error)`，**永不抛异常**；空输入回 `(None, "")` 表示"未指定" |
| `normalize_session_path(path)` | 折成标准列表；坏输入回 `DEFAULT_SESSION` |
| `session_key(path)` / `same_session(a, b)` | 稳定的会话标识 / 同一会话判定（坏输入一律判不同，不猜） |
| `load_session_config(config_path=None)` | 返回 `(path, source)`，`source` 为 `config` / `default`；文件缺失、损坏、`path` 为空数组都回默认 |

**接线**：四个会话接口全部走 `parse_session_path`，非法入参回
`validation_error` 信封（`status=failed` + `reasonCode=validation_error`）；
analysis 的 `/connect` 现在会**优先用请求里指定的会话**，没指定才读配置；
两处 `_load_session_config` 都变成一行委托。

**校验规则**：每段必须是字符串；除首段外不能有空段；空数组非法；首段为空表示
"从 LabRAD 根开始"。`/LQHL/test/x` 与 `["", "LQHL", "test", "x"]` 是同一个会话。

### 3.12 registry 读取收敛：`/qubit/params` 是 registry 的投影（阶段 9）

**要解决的问题**（roadmap §3 #2 剩余项）：`/qubit/registry` 用 `registry_wrapper2`
摊平全树，而 `/qubit/params` 另写一套 `getattr` + `float()` 投影 —— 同一份参数两种读法，
迟早漂；而厂商实验改的是 registry 那份。

**现在**：`_read_registry_tree(qname)` 是**唯一**读路径（不加锁，调用方持
`self._labrad_lock`），两个端点都走它：

- `/qubit/registry`：直接回全树（含 `timing.*`、`channels.*`、`correction.*`）。
- `/qubit/params`：按 `QuantumService.PARAM_KEYS`（**28 个历史键，钉死**）投影，
  值经 `_legacy_param_value` 折成历史形状 —— 数值 → `float`，字符串（`ReadOut.window_type`、
  `discriminator.method`）与数组（`discriminator.center0/1`）→ `None`。
  registry 打不开时退回 qubit 对象投影（`_params_from_qubit_object`），响应里用
  `params_source` 标注 `registry` / `qubit_object`（降级模式仍是 `fallback`）。

**为什么不直接把全树吐给 `/qubit/params`**：前端 `QubitParamsPanel` 会把收到的
`params` 原样 `parseFloat` 后回写 `set_params` —— 多出来的对象/数组键会变成 `NaN`
写进 registry。键集必须钉死；要看全树请用 `/qubit/registry`。

**写入侧**没有第二套：`/qubit/set_params` 与 `/qubit/apply_dataset` 共用
`_write_qubit_params`（先写 qubit 对象，拿不到的键退回 registry 写）。

### 3.13 真机可用性语义：一份判据（阶段 10）

**要解决的问题**（roadmap §3 #3）：`:3003` 与 `:3004` 各自手写同一段
"连上就 healthy / 离线模式算 healthy / 在线模式没连上才算 degraded"；
而且两个服务各有一份**永远走不到**的 `_handle_health`（`/health` 在
`base_service` 的 HTTP 层就被拦下、走 `get_health()`），死代码的判据还与活的那份
不一致（analysis 的死版本不认 `stuck`、也不认 `offline_provider`）。

**现在**：`services/common/availability.py` 是唯一判据，`get_health()` 只负责喂字段。

```json
"availability": {
  "state": "ok | degraded | unavailable",
  "reasonCode": null | "stuck" | "offline_mode" | "offline_fallback"
                     | "not_connected" | "initializing",
  "message": "人能看懂的一句话",
  "device": "labrad | datalab", "deviceConnected": true,
  "mode": "online | offline | auto", "fallbackState": "connected",
  "sessionPath": ["", "LQHL", "test", "20251103_LD_SQ"],
  "busy": false, "ready": true, "offlineAvailable": true, "stuck": false
}
```

判据顺序（先命中先返回）：

1. `stuck`（超时闸门）→ `degraded` / `stuck` —— 比"设备其实连着"更该被看到
2. 设备已连 → `ok`
3. `mode=offline` → `ok` / `offline_mode`（离线是配置，不是故障）
4. `mode=auto` 且离线数据可用 → `ok` / `offline_fallback`
5. `mode=auto` + 离线不可用 + 永久降级 → `unavailable` / `not_connected`
6. 正在初始化 → `degraded` / `initializing`
7. 其余 → `unavailable` / `not_connected`

**历史字段不变**：`status` 由 `derive_legacy_status(availability)` 推出（只有 `ok` 是
`healthy`，`degraded` 与 `unavailable` 都是 `degraded`），因此 `:3003`/`:3004` 的
HTTP 码与老消费者行为完全一致；`labrad_connected` / `datalab_connected` /
`fallback_state` / `stuck` / `lane` 等字段一个没少。analysis 的健康检查补上了
`offline_available` / `stuck` / `stuck_reason`（以前只在 quantum 侧有）。

**HTTP 码统一**：`base_service.send_health_response()` 用
`availability.health_status_code()` —— 有 `availability` 看它，没有就退回历史 `status`，
老服务/离线包缺模块时也不会把健康检查打挂。

**注意**：`availability.state` 与 `errors.py` 的规范状态（`ok/degraded/queued/blocked/failed`，
见 §3.8）**不是同一套词表** —— 前者描述"这台服务的真机能力"，后者描述"这次请求的结局"。

---

### 3.14 比特状态可自查：证据从台账来（阶段 11）

**要解决的问题**（roadmap §2.4）：阶段 2 的 `qubit_state.derive_state(evidence)` 只会
"给证据 → 判在哪一态"，证据得由调用方填 —— 等于标定记录仍在人脑里，系统答不了
"q7ld3 卡在哪一态、缺什么证据、下一步跑哪个实验"。

**现在**：证据由执行台账推导，人来补的只是少数无法从数据判读的断言。

```python
# services/common/state_evidence.py
evidence_from_records(records, qubit="q4ld3")
# → {"evidence": {...}, "sources": {...}, "conflicts": [...],
#    "considered": 3, "ignored": 1, "ignored_runs": ["run-x"]}
```

| 证据键 | 从哪来 | 判定方式 |
|---|---|---|
| `freq_locked` | `sq.spectroscopy` / `spectroscopy_iq` / `pidf` / `ramsey_df` | 成功且有数据集号即视为频率已确认 |
| `amp_calibrated` | `sq.piamp` / `pidf` / `time_rabi` / `rabi` / `set_pi` | 同上；或 `contrast` 达到"可用"以上（隐含说明幅度可复现） |
| `contrast` | 台账 `metrics.contrast` | 原值，交给 `lab_criteria.json` 判 |
| `dmu_sig` | `metrics.dmu_sig` | 原值 |
| `t1_us` / `t2_star_us` | `metrics.T1_us` / `T2_us` | 原值（注意 `qter.fitData` 的 T2 在 `data[1][0]`，不在 `para` 里） |
| `xeb_p` / `xeb_gate_fidelity` | `metrics.xeb_p` / `gate_fidelity` | 原值 |

**只认"真跑过"的记录**：`status` ∈ `success/completed/ok` 且 `dataset_ids` 非空；失败记录
只计入 `records.ignored` 并保留 `run_id`，不产生证据（`ignored_runs` 供人回看）。
按 `finished_at`（缺失退回 `started_at`）倒序，**新证据覆盖旧证据**；同一指标两次读数
相对差 > `20%` 时记进 `conflicts`（`{key, kept_run, dropped_run, kept, dropped, relative_delta}`）
—— 读出链慢漂（例如 q4ld3 的 contrast 从 0.74 掉到 0.58）就该在这里冒头，而不是先污染状态。

**端点**：

```http
POST/GET :3003/qubit/state   {"name": "q4ld3", "limit": 200,
                              "evidence": {"freq_locked": true}}   # evidence 可省
→ {"success": true, "name": "q4ld3",
   "state": {"state": "state_discriminated", "state_index": 2, "reasons": [...],
             "missing": ["t1_us/t2_star_us"],
             "missing_detail": [{"key": "t1_us", "produced_by": ["sq.t1"], "note": "..."}],
             "next_action": "...", "metrics": {...}, "criteria_version": "1.0.0"},
   "evidence": {...}, "evidence_sources": {"contrast": {"run_id": "...", "dataset_ids": [2669],
                                    "functions": ["iqraw"], "finished_at": 1.7e9}},
   "conflicts": [], "records": {"considered": 3, "ignored": 0, "ignored_runs": [], "limit": 200},
   "plan": {"state": "...", "missing": [...], "next_action": "..."}}
```

- **只读**：不执行实验、不写寄存器；查询本身不会改变任何比特状态。
- `evidence` 显式传入时覆盖台账值，来源标记为 `{"source": "request"}`（人是最终权威）。
- `evidence_sources` 让每条证据都能点回 **run_id / 数据集号 / 函数名 / 时间**。
- `EVIDENCE_SOURCES` 里带厂商坑提示：`sq.spectroscopy` 必须显式给 `freq`（否则标量扫描
  会在 `prepare_dataset` 抛 `IndexError`）、`sq.t1` 必须给固定 `zpa`（否则是 zpa×时间
  二维扫描）、`sq.iqraw` 的 `auto=True` 会静默回写 `discriminator.*` 与 `fread`。
- 与 `/qubit/assess` 的分工：`assess` 只吃调用方给的证据做纯判定（不读盘），`state` 负责
  "证据从哪来"。两者共用 `derive_state` 与 `config/lab_criteria.json`，判据不会分叉。

---

### 3.15 画指定数据集：`dataset_index` 透传（阶段 12）

**要解决的问题**（roadmap §3 #2 末项）：Express `/sessions/plot` 只转发 `{command}`，
不透传 `dataset_index` —— 想"画第 2668 号数据集"只能绕过 Express 直连 `:3004/plot`，
于是同一能力两套入口、两套行为（且直连会绕开 Express 的鉴权与日志）。

**现在**：

```http
POST :3002/sessions/plot
{"command": "qter.fitData({exp_num}, collect=True, do_plot=True)",
 "dataset_index": 2668}          # 也可写 datasetIndex；不传 = 画会话里最新的
```

- 传了必须是非负整数（数字或数字字符串都认）；`-1`/`0`/不传都表示"最新"，
  与原行为一致。非法值 **在 Express 层就回 400**：
  `{"error": "...", "status": "failed", "reasonCode": "validation_error"}`，不再让下游猜。
- 键名原样透传给 analysis `/plot`（`data.get("dataset_index", -1)` → `data.loadDataset(idx)`）。
- analysis 回包补 `dataset_index` 与 `dataset_name`，让"画的是哪个数据集"可对账：

```json
{"success": true, "plotPath": "...", "plotUrl": "/plots/xxx.png",
 "dataset_index": 2668, "dataset_name": "02668 - q4ld3: IQraw"}
```

- 前端仍只发 `command`（UI 的语义是"画最新"），行为不变；需要指定数据集的调用方
  （agent / 脚本 / 复现流程）多传一个字段即可。
- 自检 `_phase12_check.py`（Express 两个别名与非法值、两分支都转发、analysis 回包与结构完好、
  键名契约一致、文档在位）。

---



### 3.16 设备画像进运行期：`$profile.*`（阶段 13）

**要解决的问题**（roadmap §2.7 末项）：画像 `config/device_profile.json` 以前只在
**lint 期**被读过（判断"这个 qwf 的比特名/参数像不像画像里的"），运行期流程仍然
把 `f10` / `fread` / π 幅度写死在 `args` 里 —— 换一片芯片就得逐个改 qwf。

**现在**：执行器启动时把当前比特的画像折进 env，`.qwf` 里直接引用：

```text
env { qubit "q4ld3" }

measure as c1 {
  fn    sq.spectroscopy
  qubit $env.qubit
  args  { freq range 4.0 3.0 -0.002 }
}
measure as c2 {
  fn    sq.pidf
  qubit $env.qubit
  args  { df $profile.f10 }
}
```

可引用的键（键名与画像字段**同名**，唯一真源 `services/common/profile.py` 的
`PROFILE_ENV_FIELDS`）：

| 分类 | 键 |
|---|---|
| 元信息 | `qubit`、`version`、`updated_at`、`source`（恒为 `device_profile`） |
| 频率 | `f10`、`f21`、`fread`、`fc`、`fc_read` |
| 偏置/脉冲 | `bias_z`、`pi_gate_amp`、`pi_gate_alpha`、`read_out_amp`、`read_in_power`、`timing_xy` |
| 通道 | `xy_dac`、`z_dac`、`readout_adc`、`readin_dac` |

语义与边界：

- 解析顺序：`context.profile`（调用方显式给，最高优先）→ IR `env.profile` →
  `config/device_profile.json` 里 `env.qubit` 那一条。`profile_env(qubit)` 只放**真有的字段**，
  不补默认值 —— 用例写 `$profile.f10` 而画像没有 `f10` 时宁可报错，也不拿 `0.0` 去扫频。
- 画像缺失 / 比特不在画像里：执行器给一条 warning（`设备画像里没有 qX 的条目`），
  流程本身继续跑；真引用到 `$profile.*` 才抛 `UndefinedRef`，错误里列出可用字段。
- 静态检查：`validate` 放行 `$profile` 头，字段名不在表里 → `W_PROFILE_UNKNOWN_FIELD`；
  lint 另有 `W_PROFILE_MISSING_FIELD`（画像缺该字段）、`W_PROFILE_UNKNOWN_QUBIT`、
  `I_PROFILE_REFS`（引用了画像字段但离线读不到画像）。
- 刷新画像：`GET :3003/qubit/profile?refresh=1`（阶段 3 已有）。
- 自检 `_phase13_check.py`（折平语义 / 执行器解析与报错 / validate 与 lint 诊断 /
  键名三处一致 / 产品 qwf 回归 / 文档在位）。

---

### 3.17 厂商语义风险进 lint：默认回写 / 扫描窗口 / 必填参数（阶段 14）

**要解决的问题**（roadmap §3 #4）：厂商实验的"默认顺手做点真机操作"从来没被静态拦过。

- `WRITE_EFFECTS` 里 `mode="default_on"` 的实验（`sq.iqraw` / `sq.piamp` / `sq.pidf` /
  `sq.spectroscopy` / `sq.ramsey_df` / `sq.s21` …）**不写 `update` 就回写寄存器**，
  一个"只看看"的 workflow 也会悄悄改掉 `f10` / `discriminator.*`；
- `sq.pidf` 默认 `df=0.03` 只覆盖 ±30 MHz，实测真实偏差 35.7 MHz 时整段扫空、报"没找到跃迁"；
- 必填参数（`sq.t1` 的 `zpa`、`sq.spectroscopy` 的 `freq`）有专用规则，但换实验就得再补一条。

**现在**：三条规则都**表驱动**（复用 `services/workflow_service/dsl/backends/lqcs.py` 的
`WRITE_EFFECTS` 与 `services/common/replay.py` 的 `VENDOR_SEMANTICS`，lint 里不再抄第二份）：

| 代码 | 级别 | 触发 | 怎么改 |
|---|---|---|---|
| `W_UPDATE_DEFAULT_ON` | warning | `default_on` 类实验没写 `update`（或写成表达式认不出） | `args { update False }` 关掉；诊断里带 `keys`（会回写哪些键） |
| `I_UPDATE_ON` | info | 显式写了 `update=True` | 不用改；这条 info 是"我知道它会写"的确认 |
| `W_PIDF_DF_SMALL` | warning | `sq.pidf` 缺 `df`，或 `0 < abs(df) < 0.05` | `args { df 0.05 }`（±50 MHz、21 点、约 8 s） |
| `E_ARG_MISSING` | error | `VENDOR_SEMANTICS[fn]["requires"]` 里的参数没给 | 显式补参数；已被专用规则覆盖的（`sq.t1`/`zpa`、`sq.spectroscopy`/`freq`）不重复报 |

语义与边界：

- 只判定**字面量**：`update` / `df` 写成 `$env.*`、`$profile.*`、`np.arange(...)` 等表达式时
  认不出 → **不报**（宁可漏报也不误报，避免逼用户为过 lint 拆表达式）。
- 关掉默认回写**不代表不会写**：`mode="always"` 的实验（`sq.set_pi`、`sq.s21_dis`、`sq.iqcenter`）
  `update=False` 也照写，仍由 `W_WRITE_FN` + 写闸门管，别把 warning 消失当成只读。
- `sq.iqraw` 的 `discriminator.*` 是派生键（不需要票据），但中心点会被重算 ——
  `W_UPDATE_DEFAULT_ON` 对它是"你这一眼会改判读基准"的提醒。
- 自检 `_phase14_check.py`（常量与字面量解释、四条默认回写分支、df 五个边界、
  `E_ARG_MISSING` 打桩路径与去重、既有规则回归、表驱动契约与文档在位）。

---

### 3.18 异步执行的 RunRecord：提交与完成拆两条（阶段 15，producer 化前置）

**要解决的问题**（roadmap §2.1 的最后一步）：`/execute` 早就能带 `async=true`
立即回 `job_id`，但审计记录是在**提交那一刻**写的 —— 拿到的只是 `{status:"queued"}`，
于是异步执行永远缺 `dataset_ids` / `metrics` / `verdict`。谁改用异步，审计链就断在提交处。

**现在**：一次异步执行落**两条同 `run_id`** 的记录，读侧按 `run_id` 去重取最后一条。

```http
POST :3003/execute
{"code": "sq.iqraw(q4ld3, do_plot=False, update=False)", "async": true}
→ 200 {"status": "queued", "job_id": "job_...", "run_id": "run-...", "trace_id": "...",
        "queue_position": 0, "poll": "/jobs/job_...", "async": true}
```

| 时刻 | 记录内容 |
|---|---|
| 提交 | `status="queued"`、`job_id`、`trace_id`、`manifest`、`governed`（无 `finished_at`） |
| 完成（worker 落） | 真实 `status`、`dataset_ids` + `dataset_source`、`metrics`、`verdict`、`finished_at`、`diff`（replay full 时） |

- **基线在执行前取**：数据集差集的基线（DataVault 目录）与 registry 前快照都在 worker
  真正开始执行的那一刻才取 —— 排队期间别的 job 可能已经产出数据集/改过寄存器，
  提交时取会算错。
- **读侧去重**：`GET /qubit/runs` 先翻成时间顺序 → `collapse_records()` 按 `run_id`
  取最后一条 → 再翻回"新到旧"；`/qubit/state` 的 `evidence_from_records` 同样按
  `run_id` 去重（优先保留"有数据集且有完成时间"的那条，否则 considered 会虚高）。
- **失败/放弃也有终态**：worker 抛异常 → `internal_error`；job 被 abandon → `abandoned`；
  `_finalize_async_job` 任何异常只记日志，绝不改写 job 结果。
- **默认仍是同步**：不传 `async` 的调用方行为完全不变（同一个 `_complete_record`
  在请求线程里跑）。默认异步化见 §3.19（阶段 16a）。
- 自检 `_phase15_check.py`（`collapse_records` 语义 / `job_id` 落盘 / `_complete_record`
  状态与数据集归属 / `_handle_async_submission` 回包契约与 job 挂载 / `_finalize_async_job`
  与异常隔离 / 读侧去重 / worker 取基线与补记录的顺序契约）。

---

### 3.19 `/execute` 默认 producer 化：三态契约（阶段 16a）

**默认不再是"等结果"**。`POST :3003/execute` 不带表态字段时按配置决定，出厂默认是**异步**（提交即返回 `job_id`）：

| 请求 | 行为 | 回包要点 |
|---|---|---|
| `{"code": ...}` | 按 `execute_default`（默认 `async`） | `status:"queued"`, `job_id`, `run_id`, `trace_id`, `poll`, `execute_mode` |
| `{"code": ..., "wait": true}` | 旧同步契约（等 lane 跑完） | 同以前：`status:"success"` + 结果 + `run_id`/`dataset_ids` |
| `{"code": ..., "async": true}` | 异步（显式） | 同默认 |
| `{"code": ..., "async": false}` | 同步（旧写法） | 同 `wait:true` |
| `{"code": ..., "wait": true, "async": true}` | **`wait` 优先 → 同步** | 同 `wait:true` |

**判定顺序**：`wait` → `async` → 默认。字符串 `"false"` 会被解释成 False（不要用裸 `bool()` —— 老写法踩过 `async:"false"` 反而走异步的坑）。实现在 `services/common/exec_policy.py`（纯函数，`describe_execute_policy()` 可自描述）。

**默认值来源**：环境变量 `QMCLAW_EXECUTE_DEFAULT`（`sync`/`async`）优先，否则 `config/fallback_config.json` 的 `quantum_service.actuation.execute_default`，再否则 `async`。环境变量写错值会被忽略（继续看配置），不会把服务切成意外模式。**回退方式**：把 `execute_default` 改成 `"sync"`（热生效，不用回滚代码）。

**提交期就能判定的失败仍然同步返回**：写闸门（`read_only`）、卡死（`stuck`）、降级（`degraded`）、未连接（`not_connected`）、`task_required` —— 不会先回 `queued` 再失败，调用方不必区分"排队后被拒"和"运行时失败"。

**轮询**：`GET /jobs/{id}` 回包现在带 `run_id` 与 `trace_id`（内部 `record_*` 字段不外泄），可直接对上 `/qubit/runs` 的审计记录；终态失败时 `error` 即失败原因。

**完成态结果就在轮询回包里**（`result` 字段）：worker 跑完后 `_finalize_async_job` 会把 RunRecord 的结论回写到 `job["result"]`，因此终态回包含 `status`、`run_id`、`trace_id`、`dataset_ids`、`dataset_source`、`execute_mode:"async"`，以及 worker 原始输出（`stdout`/`output` 等）。**调用方不必再查 `/qubit/runs`**。这条是 2026-11-10 真机验收时补的——首版 `trace_id` 从 job 上被 `pop` 掉、`result` 也还是原始返回值，异步契约等于"结果要自己另找"。

真机验收口径（同一台 q4ld3，`:3003` 重启后）：

| 用例 | 实测 |
|---|---|
| `{"code":"1+1"}`（默认异步） | 0.05 s 回 `queued` + `job_id`/`run_id`/`trace_id`/`poll`/`execute_mode:"async"` |
| `{"code":"sq.iqraw(q4ld3, do_plot=False, update=False)","wait":true}` | 3.33 s 回 `success`、`execute_mode:"sync"`、`dataset_ids:[2670]` |
| 同一 iqraw 走默认异步 | 提交 0.04 s；轮询到 `success`；`/qubit/runs` 该 run 有 `dataset_ids:[2671]`、`dataset_source:"datavault"`、`trace_id`、`job_id`、`manifest.functions:["iqraw"]` |

**消费方（本阶段全部显式表态，行为不变）**：

| 位置 | 做法 |
|---|---|
| `services/workflow_service/dsl/backends/lqcs.py` | 原本就是 producer 路径：`{async:true}` + 轮询 `/jobs/{id}` + 超时 `abandon` |
| `services/task_queue/server.py` | 同上（任务账本下发实验走异步 job） |
| `services/agent_service/adapter.py` | `{wait:true}`；万一仍收到 `queued`，用 `_poll_job` 轮询到底，绝不把 `job_id` 当结果 |
| `services/client_proxy.py` | `{wait:true}` |
| `services/qca_service/adapter.py`、`core/runner.py` | `{wait:true}` |
| `services/hermes_service/quantum_toolset.py`、`cron.py` | `{wait:true}` |
| `Agentic Workflow/qmclaw-server/src/index.ts` | 不直连量子 `/execute`（前端直发走 agent），无需改；`/sessions/plot` 与 analysis 的 `/execute` 与量子无关 |

**前端**："直接执行"按钮的排队/轮询 UI 属阶段 16b（需另授权），本阶段前端行为不变（它经 agent 走 `wait:true`）。

- 自检 `_phase16_check.py`（判定矩阵与字符串陷阱 / 默认值来源与优先级 / 服务端接线（解析结果钉进 `call_data`、三处 `_handle_execute_impl(call_data)`）/ `/jobs` 回包字段与内部字段不泄漏 / `_poll_job` 桩服务器端到端（queued→completed、失败终态、超时）/ 全仓 `/execute` 调用点扫描必须显式表态 / 配置落盘一致性 / 文档在位）。

---

### 3.20 服务进程组：一次重启起一组（阶段 17，端口税 §3 #6）

**问题**：10 个端口 = 10 个进程，改 `dsl/` 要单独重启 `:3008`、改账本要再重启 `:3009`，运维面太大。

**做法**：把"哪些服务可以合进同一个进程"写成唯一事实来源 `services/common/service_groups.py`
（端口表 + 入口点 + 每个不可合并服务的**理由**），启动器 `scripts/start_group.py` 只读它：

```powershell
python scripts/start_group.py --list
python scripts/start_group.py --group core --dry-run   # 只打印计划
python scripts/start_group.py --group core             # workflow :3008 + task_queue :3009 同进程
```

- `core` 组（`workflow` + `task_queue`）在**同一个 Python 进程**里各起一个 HTTP server（每服务一线程），
  改 `dsl/` 或账本后只需重启这一次；任一服务线程挂掉会停整组并以退出码 1 结束（不静默半死）。
- **端口号一个都不改**：调用方 URL 不变。真正"减端口"需要网关式路径前缀，属于契约变更，另行授权。
- `lab` 组（`quantum`/`analysis`）**不允许**同进程：LabRAD 会话与 Ray 连接独占，一边崩会拖垮另一边；
  `--group lab` 会打印阻塞理由并退出码 2，请仍用 `scripts/start_all_services.py` 分开起。
- 新增服务时先登记 `SERVICE_PORTS`；若它不能合并，必须在 `MERGE_BLOCKERS` 写理由——
  `_phase17_check.py` 会对"没有理由的不可合并服务"直接判 FAIL。
- 自检 `_phase17_check.py`（PASS=38）：两处端口表一致（`start_all_services.py` / `config/services.json`）、
  端口无重复、组定义自洽（`plan_group`）、阻塞理由齐备、启动器不硬编码端口、`--dry-run` 不启动进程、
  未知组退出码 2、`lab` 拒绝同进程、文档在位。

---

### 3.21 审计查询：`/qubit/runs` 过滤（阶段 18）

`GET :3003/qubit/runs` 原来只能"取最近 N 条"，查"q4ld3 上周那批失败的"得自己拉全量再筛。
现在服务端过滤，且**过滤发生在截断之前**：

| 参数 | 说明 |
|---|---|
| `limit` | 返回条数（1..2000，默认 50） |
| `qubit` | 单值 / 列表 / 逗号分隔；记录的 `qubits` 任一提及即命中。**全局实验（qubits 为空）不会被 qubit 条件放行** |
| `status` | 规范状态 `ok`/`blocked`/`degraded`/`failed`，或原始生命周期值 `success`/`error`/`timeout`… |
| `source` | `execute` / `workflow` … 精确匹配 |
| `dataset_id` | 数据集号出现在 `dataset_ids` 里；给了非数字则必然空结果（不静默忽略条件） |
| `trace_id` | 精确匹配（阶段 3 的老能力，现在不再被 `limit` 截掉） |
| `since` / `until` | epoch 秒或 ISO 串；按 `finished_at → started_at → created_at` 取时间，**都没有时间的记录不进入时间窗**（宁缺毋错） |

语义细节：

- 规范状态由 `record_canonical_status()` 唯一决定：有 `error_code` 就看错误码的规范状态（`quota_exceeded` → `blocked`，
  未登记码 → `failed`），否则按生命周期值（`success/completed` → `ok`，`error/timeout/cancelled/rejected` → `failed`）。
- 不带时区的 ISO 串按 **UTC** 解释（审计要可复现，不能随机器本地时区漂移）。
- 过滤前后都保留"新→旧"响应顺序；同一 `run_id` 的 queued/完成两条先按阶段 15 去重再过滤。
- 回包新增 `filters`（回显生效的条件，便于确认"是我筛空了还是本来没有"），`count` 是过滤后的条数。
- 实现：纯函数 `filter_records()` 在 `services/common/runrecord.py`（可离线测）；端点见 `_handle_qubit_runs`。
- 自检 `_phase18_check.py`（PASS=68）：规范状态映射 / 时间解析 / 各维度过滤 / 组合与边界 /
  桩 writer 端到端（过滤在任何截断之前、queued 去重）/ 源码契约 / 文档在位。

### 3.22 前端直接执行：排队与轮询（阶段 19 / 16b）

阶段 16a 把 `POST /execute` 默认改成 producer 语义（立刻回 `{status:"queued", job_id, run_id, trace_id, poll}`），
但前端仍按"同步返回结果"读 `stdout`，实际拿到的是空壳。阶段 19 补上最后一环。

**Express 新增两条代理**（`src/services/serviceProxy.ts`，挂在 `/api` 下）：

| 路由 | 转发 | 说明 |
|---|---|---|
| `GET /api/quantum/jobs/:id` | `GET :3003/jobs/{id}` | 轮询作业；完成态的 `result` 已带 `dataset_ids / dataset_source / trace_id / execute_mode` |
| `POST /api/quantum/jobs/:id/abandon` | `POST :3003/jobs/{id}/abandon` | 放弃排队中的作业（尽力而为） |

**前端契约**（`Agentic Workflow/qmclaw-web/src/lib/api.ts`）：

- `isQueuedJob(payload)`：`status === "queued"` 且 `job_id` 非空才算排队；
- `waitForQuantumJob(jobId, {timeoutMs, intervalMs, onProgress})`：默认 600 s / 800 ms；
  终态集合 `success||completed||error||failed||timeout||cancelled||rejected`；
  **只有没有 `status` 的传输层错误才继续等**（Express 重启时常见），带 `status` 的失败立刻返回；
  超时抛错，不返回半成品；
- `flattenQuantumJob(submit, job)`：把"提交回包 + 终态 job"摊平成老的
  `{task_id, status, stdout, stderr, error, result}`，另加 `dataset_ids / run_id / trace_id / execute_mode`
  —— 老页面代码不用改；
- `runExperiment` / `executeExperiment` 命中 queued 自动轮询；`onProgress` 可选。

**页面**（`qmclaw-web/src/app/page.tsx`）：主执行按钮与自定义命令都传 `onProgress`，
只在相位变化时打一行 `⏳ queued #3 / running / success`，不刷屏。

**边界**：

- 提交期就能判定的错误（`read_only / stuck / degraded / not_connected / task_required`）仍**同步**返回，
  前端不会进轮询分支；
- 想回退同步：`config/fallback_config.json` 的 `quantum_service.actuation.execute_default = "sync"`
  （或环境变量 `QMCLAW_EXECUTE_DEFAULT=sync`），前端两条路径都兼容；
- 老接口 `waitForJob()`（`GET /job/{id}`）保留给 legacy 作业，微服务作业一律走 `waitForQuantumJob`。

- 自检 `_phase19_check.py`（PASS=62）：助手齐全 / 终态集合 / 超时与传输层重试 / 两个入口都轮询 /
  摊平字段与后端契约对齐 / Express 两条代理（含 `encodeURIComponent`） / 页面进度提示 /
  防回退扫描（不再声称同步） / 桩作业序列终止条件 / 文档在位。

---

## 4. 自检脚本清单（都在 `D:\qmclaw\`）

| 脚本 | 覆盖 | 期望 |
|---|---|---|
| `_p1_check.py` | P1 目标契约 lowering | 全部通过 |
| `_p1_goal_dsl_check.py` | goal 块 + 全目录 `.qwf` lint 回归 | FAIL 0 |
| `_p1_manifest_check.py` | 写清单哈希一致性 | FAIL 0 |
| `_qwf_gen_goal_check.py` | `qwf_gen.py` 的 goal 块生成：native / expanded 两种写法往返一致、lowering 出同样三件套 | 24 OK / 0 FAIL |
| `_p2_ask_choice_check.py` | ask/choice 契约 | PASS=58 FAIL=0 |
| `_p2_flow_check.py` | 流程分支 | PASS=14 FAIL=0 |
| `_p2_llm_check.py` | decide 契约 | PASS=19 FAIL=0 |
| `_p2_outcome_check.py` | outcome 契约 | PASS=13 FAIL=0 |
| `_p3_check.py` | 账本/授权/额度/退款/落档（离线） | PASS=60 FAIL=0 |
| `_p3_e2e.py` | 同上（HTTP，默认打正式 :3008/:3009） | PASS=36 FAIL=0 |
| `_derived_key_check.py` | 派生键豁免 | PASS=14 FAIL=0 |
| `_autorevert_check.py` | 副作用自愈策略 | PASS=25 FAIL=0 |
| `_autorevert_exec_check.py` | 自愈接入执行器 | PASS=7 FAIL=0 |
| `_registry_ep_check.py` | `:3003 /qubit/registry` 端点 + reg_snapshot 走 HTTP | PASS=14 FAIL=0（服务已重启时 WARN=0） |
| `_apply_dataset_ep_check.py` | `:3003 /qubit/apply_dataset` 端点（数据集参数快照写回） | 静态 PASS=5；HTTP 段需服务重启后才有意义 |
| `_d2_registry_offline_check.py` | D2 边界收口（处理器级 + 桩 HTTP，**不连真机**） | PASS=23 FAIL=0 |
| `_phase2_check.py` | 阶段 2：RunRecord / 错误码 / 能力探测 / Qubit Port / server 接线（含 RunRecord 的 metrics/verdict 提取、快照与 diff 接线） | PASS=112 FAIL=0 |
| `_phase2b_check.py` | 阶段 2b：Metric Registry 接入 / 比特状态机 / `/qubit/assess` 端点 | PASS=47 FAIL=0 |
| `_phase2c_check.py` | 阶段 2c：单写者内核（串行/FIFO/异常隔离/超时）/ 治理覆盖率 / writer_mode 配置 | PASS=50 FAIL=0 |
| `_phase3_check.py` | 阶段 3：traceId 贯穿 / 设备画像（加载·校验·合并·报告）/ `/qubit/profile` 接线 | PASS=49 FAIL=0 |
| `_phase3b_check.py` | 阶段 3 第二批：traceId 全链贯通（Express→workflow→task_queue→quantum→RunRecord） | PASS=51 FAIL=0 |
| `_phase3c_check.py` | 阶段 3 第二批②：设备画像 lint（I_PROFILE_QUBIT / W_PROFILE_*）+ 厂商坑静态预判（E_SPEC_NO_FREQ / W_ZPA2D_DEFAULT_ZPA / W_SWEEP_LARGE / `_sweep_points`）+ analyze 绑定 | PASS=42 FAIL=0 |
| `_phase3d_check.py` | 阶段 3 第二批③：回放层（厂商语义仿真 + fixture 录制/回放 + 漂移检出） | PASS=58 FAIL=0 |
| `_phase3e_check.py` | 阶段 3 后续：fixture 自动录制（replay_auto_record 开关）/ 设备画像自动刷新（`/qubit/profile?refresh=1`） | PASS=40 FAIL=0 |
| `_phase4_check.py` | 阶段 4：Metric Registry 接入 goal 判定（criteria/level）+ 比特状态机进 outcome | PASS=37 FAIL=0 |
| `_phase5_check.py` | 阶段 5：回放回归（fixture → 仿真 vs 真机，漂移检出；空目录 = 未覆盖；弱结论与目录降级保护；读侧目录不随写侧降级） | PASS=42 FAIL=0 |
| `_phase6_check.py` | 阶段 6：统一状态语义（canonical status / reasonCode / legacyStatus / 未知码退化 / normalize 幂等 / 各服务接入与裸字面量扫描 / outcome 映射） | PASS=54 FAIL=0 |
| `_phase7_check.py` | 阶段 7：数据集归属（stdout 解析 / DataVault 目录差集 / cd+dir 真实语义 / 合并 / 探测失败不外抛）与直发侧程序清单（厂商语义补全 / 确定性 / RunRecord 落盘 / `:3003` 接线） | PASS=64 FAIL=0 |
| `_phase8_check.py` | 阶段 8：会话路径唯一实现（解析各形态 / 坏输入只回原因 / key 与同会话判定 / 配置读取与降级 / 四个接口与两份 `_load_session_config` 的接线） | PASS=39 FAIL=0 |
| `_phase9_check.py` | 阶段 9：registry 读取收敛（`PARAM_KEYS` 键集 / `_legacy_param_value` 历史形状 / qubit 对象退路 / 两端点共用唯一读路径） | PASS=35 FAIL=0 |
| `_phase10_check.py` | 阶段 10：真机可用性语义收敛（判据矩阵 / 历史 `status` 与 HTTP 码 / 模式归一化 / 两服务共用唯一判据且死处理器只剩委托） | PASS=62 FAIL=0 |
| `_phase11_check.py` | 阶段 11：比特状态可自查（台账→证据推导 / 成功与数据集门槛 / 新证据覆盖与 20% 冲突 / 证据来源表 7 项与缺失表 6 态 / `derive_state.missing_detail` / `/qubit/state` 接线且只读） | PASS=76 FAIL=0 |
| `_phase12_check.py` | 阶段 12：`dataset_index` 透传（Express 两个别名与非法值 400、两分支都转发、analysis 回包带实际编号与名字、键名契约、文档在位） | PASS=21 FAIL=0 |
| `_phase13_check.py` | 阶段 13：设备画像进运行期（`profile_env` 折平语义 / 执行器 `$profile.*` 解析与报错 / validate 与 lint 诊断 / 键名三处一致 / 产品 qwf 回归） | PASS=38 FAIL=0 |
| `_phase14_check.py` | 阶段 14：厂商语义风险进 lint（默认回写 `W_UPDATE_DEFAULT_ON`/`I_UPDATE_ON` / `sq.pidf` 的 `df` 窗口 / 表驱动必填 `E_ARG_MISSING` 与去重 / 字面量判定边界 / 既有规则回归） | PASS=69 FAIL=0 |
| `_phase15_check.py` | 阶段 15：异步执行的 RunRecord（`collapse_records` 去重语义 / `job_id` / `_complete_record` 状态与数据集归属 / 异步回包契约与 job 挂载 / 补齐异常隔离 / 读侧按 run_id 去重 / worker 顺序契约） | PASS=54 FAIL=0 |
| `_phase16_check.py` | 阶段 16a：`/execute` 默认 producer 化（三态判定矩阵与字符串陷阱 / 默认值来源与环境变量优先级 / 服务端接线与 `call_data` / `/jobs` 回包 run_id+trace_id 且不泄漏内部字段 / 完成态结果回写 `job["result"]` 与 trace 不 pop / `_poll_job` 桩服务器端到端与超时 / 全仓 `/execute` 调用点必须显式表态 / 配置落盘一致性） | PASS=58 FAIL=0 |
| `_phase17_check.py` | 阶段 17：服务进程组（端口表与启动脚本/config 一致 / 端口唯一 / 组定义与 `plan_group` / 不可合并服务必须写理由 / 启动器不硬编码端口 / `--dry-run` 不启动进程 / 未知组与 `lab` 组退出码 2） | PASS=38 FAIL=0 |
| `_phase18_check.py` | 阶段 18：审计查询过滤（规范状态映射 / epoch 与 ISO 时间解析 / qubit·status·source·dataset·trace·时间窗过滤 / 组合与边界 / 过滤先于截断 / queued 去重 / 回包 `filters`） | PASS=68 FAIL=0 |
| `_phase19_check.py` | 阶段 19：前端排队/轮询 + Express 作业代理（终态集合 / 超时与传输层重试 / 两个执行入口都轮询 / 摊平字段对齐后端 / `GET·POST /api/quantum/jobs/:id` / 页面进度提示 / 防"同步"回退扫描） | PASS=62 FAIL=0 |
| `_docs_paths_check.py` | 文档漂移：`docs/*.md` 与根 `*.md` 里的仓库路径必须存在；§4 本表必须与 `run_all_checks.py` 的 OFFLINE/ONLINE 列表一致（两个方向） | PASS=6 FAIL=0 |
| `_syntax_check.py` | 全仓 Python 语法（`ast.parse` 全仓 `.py`，跳过 `vendor/` 与构建目录）—— 抓"从来没能 import"的角落文件 | 动态（当前 527）FAIL=0 |

运行方式（**必须带环境变量**，否则中文输出会炸）：

```powershell
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONDONTWRITEBYTECODE="1"
D:/anaconda3/envs/lqcs/python.exe D:\qmclaw\_p3_check.py
```

### 一次跑完并落盘（推荐）

```powershell
D:/anaconda3/envs/lqcs/python.exe D:\qmclaw\run_all_checks.py          # 12 套离线自检
D:/anaconda3/envs/lqcs/python.exe D:\qmclaw\run_all_checks.py --e2e   # 再加 e2e / registry 两套
D:/anaconda3/envs/lqcs/python.exe D:\qmclaw\run_all_checks.py --only _p3_check
```

输出统一落在 `D:\qmclaw\_checks\<时间戳>\`，**所有 DSL 测试的原始数据都汇总在这里**：

```
_checks/<ts>/
  summary.json              每个脚本的 exit_code / pass / fail / 耗时
  manifest.json             总索引：自检结果 + 归档产物清单(sha256) + 外部引用目录
  checks/<name>.log         每个自检脚本的原始 stdout+stderr
  artifacts/
    workflow-runs/*.json    每次 workflow run 落档（outcome / gateNotes / sideEffects）
    ledger/*.json           task_queue 账本（预算/额度/票据/事件）
    e2e-tasks/*.json        e2e 自检的 task 数据
    lane-tasks/*.json       实验 lane 任务（含下发给量子端的 payload）
```

DataVault 原始 hdf5、绘图 png、寄存器快照体积大且是活目录，**只记路径不复制**（在 manifest 的 `external` 段）。
沙箱禁止 subprocess，runner 用 `runpy` 在当前进程内跑脚本并重定向 stdout。
另可用 `--archive-only --window-min 600` 只归档不跑检查。

---

## 5. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `:3003` 端口在监听但 `/health` 全部 HTTP 000 | 有人带了 `do_plot=True` 跑实验，matplotlib 拉起 Qt GUI 卡住工作线程 | 重启 :3003；实验一律 `do_plot=False` |
| 画图画的是"最新"数据集而不是我要的 | `:3002 /sessions/plot` 不透传 `dataset_index` | 直连 `POST :3004/plot`，body 带 `{"command":"qter.fitData(N)","dataset_index":N}` |
| `qter.fitData` 报 `AttributeError: 'NoneType' object has no attribute '__name__'` | fitData 对该数据集没有对应拟合函数 | 换 `px.XEB(...)` 或自定义绘图命令 |
| `sq.t1` 跑几十分钟不回来 | 走了 zpa-时间二维扫描 | 必须传 `zpa=<固定值>` |
| `sq.spectroscopy` 报 `IndexError: list index out of range` | 没传 `freq`，退化成标量 | 显式传 `freq=np.arange(-0.1,0.1,0.002)+{qubit}.regs.f10` |
| flow 被 needs_human 卡住 | 写操作不在任何票据范围内 | 看 `GET :3009/task/{id}/report` 的 events，或用 `--sign-scope` 重签 |
| 流程被 quota_exceeded 卡住 | task 的实验额度用尽 | 新建 task，或调高 budgets.experiments |
| `.qwf` 干跑报 `undefined ref: $d1.symptom` | 干跑时 decide 桩没有 symptom 字段 | 别在 say 里引用 `symptom`（`reasoning` 同理不可引用） |
| `:3009` 起来但 `POST /task` 返回 taskId，随后所有接口报 `unknown task` | 账本落盘目录写不进去（task 存不住） | 用 `QMCLAW_TASK_DATA_DIR=D:/qmclaw/_task_data` 启动 :3009（该目录在沙箱内可写） |
| `:3003` 启动卡在 `ray.init(address='192.0.2.11:6479')` 不返回 | Ray 集群握手在受限沙箱内会挂起（TCP 通但不回包） | 用不受限的运行环境启动 :3003（DSH 里需 `danger-full-access`） |
| 实验报 `lqcs/servers/device_manager.py prepare_dac_meas: ufunc 'divide' not supported` | 设备层（编译版 lqcs）算 DAC 参数时分母类型不对，与参数无关 | 实验室侧排查/复位设备；同一命令重试无效 |
| PowerShell `curl` 参数不认识 | PS 5.1 的 `curl` 是 `Invoke-WebRequest` 别名 | 用 `Invoke-RestMethod` 或 `curl.exe`；多行 JSON 写成单行 |
| `PermissionError` 写 `docs/`、`.claude/`、`data/workflow-runs` | 沙箱目录权限不一致 | 用 `edit`/`write` 工具改文件；临时产物落工作区根目录 |

---

## 6. 目录约定

| 路径 | 放什么 | 可写性 |
|---|---|---|
| `D:\qmclaw\` | 自检脚本、临时 qwf、快照 | 可写 |
| `D:\qmclaw\reg_snapshots\` | 寄存器快照（`reg_snapshot.py` 产出） | 可写 |
| `D:\qmclaw\_p3_e2e_tasks\` | E2E 自检的 task 数据目录 | 可写 |
| `D:\\qmclaw\\_task_data\\` | :3009 任务账本落盘目录（env `QMCLAW_TASK_DATA_DIR`） | 可写（沙箱内起 :3009 必须指到这里，否则 task 存不住 → unknown task） |
| `docs\`、`\.claude\` | 文档 | 只能用 `edit`/`write` 工具改，bash 不能删 |
| `Agentic Workflow\...\data\workflow-runs\` | run 记录 | 目录能建，**文件写不进**（沙箱）→ 落档兜底会落到 tempdir |

---

## 7. 回归节奏

改完 `dsl/` 或 `services/` 后：

1. `ast.parse` 语法检查（`docs/`、`\.claude/` 下不能用 `py_compile`，会写 `__pycache__` 失败）
2. 跑第四节的自检脚本（离线那几套不需要起服务）
3. HTTP 那几套（`_p3_e2e.py`）需要 `:3009` 在跑（默认就打正式 :3008/:3009；
   沙箱里要在临时端口验证就设 `QMCLAW_WF_URL` / `QMCLAW_TQ_URL`）：
   `D:/qmclaw/_tq_test_server.py`（:3019）、`D:/qmclaw/_wf_test_server.py`（:3018，`QMCLAW_QUEUE_URL=http://127.0.0.1:3019`）
4. 同步 `.claude/progress.md`（内容写在**文件顶部**）与 `docs/DSL_DESIGN.md`
5. 请人重启 :3003/:3008/:3009 后再真机复验
