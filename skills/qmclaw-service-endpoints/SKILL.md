---
name: qmclaw-service-endpoints
description: >-
  QMClaw 全部微服务的端点清单与功能说明（端口、路径、请求体、响应、用途）。
  覆盖 Express 网关 :3002、quantum_service :3003（真机执行）、analysis_service :3004（绘图/拟合）、
  workflow_service :3008（DSL 执行）、task_queue :3009（额度/票据）、agent :3005、image :3007、
  llm :3006、qubitclient :3010、qca :3011、hermes :3012。
  触发词：微服务、端点、endpoint、API、端口、3003、3004、3008、3009、调实验接口、
  读写寄存器、真机接口、怎么调用服务、量子服务接口、qwf、跑实验、LabRAD 连接、
  register、寄存器、任务队列、入队、票据、额度、绘图、拟合、run_ir、execute、
  离线模式、offline、写闸门、门控、goal 块、require_task、治理闸门、在线升级、热升级。
version: 1.4.0
author: QMClaw Contributors
---

# QMClaw 微服务端点总表

> **铁律：凡涉及真机（LabRAD / 硬件 / 数据集 / 寄存器）的功能，一律通过下面这些微服务端点实现，
> 不要在沙箱进程里 `import lqms` 直连 LabRAD，也不要自己起第二个客户端连 Ray。**
> 直连会绕过写闸门、绕过任务账本、绕过 LabRAD 会话锁，还会和正式服务抢 Ray 集群
> （第二个 Ray 客户端会卡在 `Connecting to existing Ray cluster at 192.0.2.11:6479`）。

## 触发时机：什么情况下**必须**先调用本 skill

只要任务落在下面任意一条，就**先调用 `qmclaw-service-endpoints`**，再动手；禁止凭记忆直接敲端点或裸代码：

| 场景 | 典型说法 | 该走的端点 |
|---|---|---|
| **用 qwf 执行测控实验** | "跑个 Ramsey / T1 / XEB / iqraw"、"标定一下 q2ld3"、"扫个频" | `:3008/workflows/run_ir`（DSL 编排）→ 内部 `:3003/execute` |
| **直接下发一个 sq.\* 实验** | "调一次 spectroscopy"、"做一次 pidf" | `:3003/execute`（必须 `do_plot=False`） |
| **连接 / 切换 LabRAD 会话** | "连上实验室"、"换 session"、"LabRAD 掉线了" | `:3003/connect`、`:3003/switch_session`、`:3004/connect` |
| **读写 register（寄存器）** | "把 f10 改掉"、"看 PiGate.amp"、"把数据集参数写回去" | `:3003/qubit/registry`（读）、`:3003/qubit/set_params`（写）、`:3003/qubit/apply_dataset`（按数据集快照回写） |
| **扫频找跃迁 / 查设备能力 / 查运行记录** | "这比特还有没有共振"、"现在能用哪些实验"、"刚才跑了什么" | `:3003/qubit/capabilities`（离线）、`:3003/qubit/probe_transition`（**真机，需先向用户提问**）、`:3003/qubit/runs`（只读） |
| **把任务放进队列 / 要票据 / 要额度** | "排个队"、"申请授权"、"跑 workflow 要多少实验" | `:3009/task*`（建 task / ticket / manifest / authorize / lease / lane） |
| **画图 / 拟合 / 出报告** | "把这组数据画出来"、"拟合 T2"、"出个图" | `:3004/plot`（要带 `dataset_index`） |
| **查看有哪些端点 / 端口对不上 / 服务挂了** | "3003 有哪些接口"、"服务怎么调" | 本 skill 即答案 |

**判定口诀**：只要碰到"真机 / 硬件 / 寄存器 / 数据集 / qwf / 队列 / 绘图"中的任何一个词，
就是触发时机 —— 先调 skill 拿端点，再发请求。

## 如何调用本 skill

1. **读取**：`skills/qmclaw-service-endpoints/SKILL.md`（仓库 `skills/` 目录是 Hermes 的技能扫描根，
   `GET :3002/api/hermes/skills/debug` 可确认是否已被索引）。
2. **按场景取端点**：从上表定位服务与端点 → 在对应章节（§1–§6）拿到完整请求体/响应示例。
3. **按 §7「真机功能实现规约」执行**：一律 `do_plot=False`、写操作走 `:3003/qubit/*`、
   画图走 `:3004/plot`、画指定数据集必须带 `dataset_index`、改 `dsl/` 后要重启 :3008。
4. **出错先查端点表**：不要改用直连 LabRAD 的老办法兜底。

## 0. 端口与服务对照

| 端口 | 服务 | 源码 | 职责 |
|---|---|---|---|
| 3002 | Express 网关 | `qmclaw-server/src/index.ts` + `src/services/serviceProxy.ts` | 前端唯一入口；`/api/*` 代理到下面各服务 |
| 3003 | **quantum_service** | `services/quantum_service/server.py` | **真机执行**：跑实验、读写寄存器、切会话 |
| 3004 | **analysis_service** | `services/analysis_service/server.py` | **绘图与拟合**（`:3003` 不画图） |
| 3005 | agent_service | `services/agent_service/server.py` | 对话、任务、工具、记忆 |
| 3006 | llm_service | `services/llm_service/server.py` | LLM 推理代理 |
| 3007 | image_service | `services/image_service/server.py` | 实验图分类（ResNet 等） |
| 3008 | **workflow_service** | `services/workflow_service/server.py` | DSL（.qwf）解析、lint、清单、执行 |
| 3009 | **task_queue** | `services/task_queue/server.py` | 任务、额度、授权票据、审计 |
| 3010 | qubitclient_service | `services/qubitclient_service/server.py` | VLM 读谱分析 |
| 3011 | qca_service | `services/qca_service/server.py` | QCA 校准智能体（无独立 HTTP 路由） |
| 3012 | hermes_service | `services/hermes_service/server.py` | Hermes Agent、技能、定时任务 |

服务启停**由人操作**（AGENTS.md 第 3 条）：
`taskkill /PID <旧PID> /F` → `python "D:\qmclaw\Agentic Workflow\qmclaw-server\scripts\start_all_services.py"`

---

## 1. quantum_service :3003 —— 真机执行（最重要）

| 方法 | 路径 | 功能 | 请求体 / 说明 |
|---|---|---|---|
| GET | `/health` | 健康检查 | 返回 `status`/`degraded`/LabRAD 连接态 |
| POST | `/connect` | 手动连/重连 LabRAD | `{session_path?, timeout=60, reset_stuck?}`；服务掉线或 stale 时用它恢复 |
| GET | `/status` | 当前状态 | LabRAD 连接、会话、忙闲 |
| GET | `/qubits` | 列出比特 | 可用 `qubit`/`path` 过滤 |
| GET | `/experiments` | 列出可用实验 | `{path?, qubit?, experiment_type?, date?}`；内容来自 `config/experiment_configs.json` |
| POST | `/execute` | **执行一段 Python 实验代码** | `{code, task_id?, timeout=300, async?}`。注入 `cxn/s/sq/data/qter/np/BasicTuner` 与**按名字注入比特对象**（代码里直接写 `q2ld3`） |
| GET | `/jobs` | 异步作业列表 | `async=true` 时配合 |
| GET | `/jobs/{id}` | 作业状态 | |
| POST | `/switch_session` | 切换 LabRAD 会话 | `{session_path}` |
| GET | `/sessions` | 列出可用会话 | |
| GET | `/session_tree` | 会话目录树 | `{max_depth=5}` |
| GET | `/qubit/params` | 读比特寄存器 | `{name}` |
| GET | `/qubit/registry` | **只读摊平 registry**（含 `timing.*`） | `{name}`；响应体是 `{"registry": {...}}` |
| POST | `/qubit/set_params` | **写寄存器** | `{name, params:{键:值}}`；受 `allow_set_params` 闸门约束 |
| POST | `/qubit/apply_dataset` | **把数据集里的寄存器快照写回比特** | `{name, dataset, skip?=["discriminator."], include?, dry_run?}`；只写有差异的键，返回 `compared/to_apply/unchanged/skipped/changes/updated/verified/mismatch` |
| GET | `/qubit/capabilities` | **能力探测**（静态 AST，不 import lqms、不连设备） | 返回 `shadowed_modules` / `capabilities{module:{source_functions,runtime_exports,missing_in_runtime}}` / `verdict`；用来判断 `spectroscopy_iq` / `time_rabi` / `iqcenter` 是否可用（`.pyd` 遮蔽 `.py`） |
| POST | `/qubit/probe_transition` | **扫频找比特跃迁** | `{name, df?=0.3, method?="pidf", task_id?, timeout?}`；强制 `do_plot=False` + **`update=False`**（防厂商静默回写 `f10`/`fread`）；method 限 `pidf`/`spectroscopy`/`iqcenter`；能力缺失返回 `not_found` 并提示"需实验室重新编译 lqms 扩展" |
| GET | `/qubit/runs` | **读运行台账**（RunRecord） | `{limit?=50}`；每次 `:3003/execute` 落一条 jsonl，含 `run_id`/`governed`/`dataset_ids`/`program_hash`/`warnings` |
| POST | `/mode` | 在线/离线模式切换 | `{mode:"online"\|"offline"\|"auto"}` |
| POST | `/datasets` | 列出数据集 | `{path}` |

**`/execute` 使用要点**
- **实验必须 `do_plot=False`**（否则 matplotlib 在工作线程拉起 Qt GUI，`:3003` 会停止 accept，表现为端口还在 LISTENING 但所有请求 HTTP 000）。绘图一律走 `:3004 /plot`。
- 显式给频率：`sq.spectroscopy(q, freq=np.arange(-0.1,0.1,0.002)+q.regs.f10, do_plot=False)`；`freq=None` 会退化成标量并报 `IndexError`。
- 会话锁是服务端的，多个请求会排队；写操作记得开写闸门。
- 厂商实验会**静默回写寄存器**（`s21` 的 auto-update 改 `fread`，宽频 `pidf` 改 `f10`）。跑完用 `/qubit/apply_dataset` 或 `/qubit/set_params` 复原。

---

## 2. analysis_service :3004 —— 绘图与拟合

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/health` | 健康检查；`degraded_permanent` 表示 LabRAD 断了，用 `POST /connect` 恢复 |
| POST | `/connect` | 连接 LabRAD → `{"success":true,"session_path":[...]}` |
| POST | `/switch_session` | 切会话 |
| POST | `/execute` | 执行分析代码（注入 `data/qter/sq/np` 等） |
| POST | `/plot` | **按命令绘图**：`{command, dataset_index?}` —— **唯一能指定数据集编号的绘图入口** |
| POST | `/plot/historical` | 画历史数据集 |
| POST | `/plot/experiments` | 画某实验最近一次的数据图 |
| GET | `/stats` | 数据集统计 |
| POST | `/analyze` | 数据分析并返回结构化结果 |
| POST | `/datasets` | 列出数据集 |
| POST | `/load` | 载入数据集 |
| POST | `/mode` | 在线/离线模式 |
| GET | `/datasets/offline` | 离线数据集索引 |
| POST | `/plot/offline` / `/plot/offline/v2` | 离线绘图 |
| POST | `/plot/online/v2` | 在线绘图（v2 参数化） |
| GET | `/variants/types` | 支持的变体类型 |
| POST | `/variants/generate` | 生成变体数据集 |
| GET | `/variants/list` | 列出变体 |
| POST | `/variants/plot` | 画变体图 |

常用绘图命令：`qter.fitData(N, collect=True)`（拟合参数）、`from lqms.data_process.xeb import process_xeb as px; px.XEB(data,[N],collect=True,do_plot=True)`（XEB）。
注意：`dc.DataLab` 没有 `fitData`；T1 的 `qter.fitData` 需要 zpa 维度，1D T1 走不通，须自己拟合。

---

## 3. workflow_service :3008 —— DSL（.qwf）执行

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/workflows` | 列出工作流 |
| POST | `/workflows/create` | 新建工作流 |
| POST | `/workflows/run` | 运行工作流 |
| POST | `/workflows/status` | 运行状态 |
| POST | `/workflows/cancel` | 取消运行 |
| POST | `/workflows/validate` | `{ir, backend?, vendorCheck?}` 校验 IR（纯校验，不碰硬件） |
| POST | `/workflows/run_ir` | `{ir, context, dryRun}` 执行 IR（唯一带额度/票据的执行入口） |
| POST | `/workflows/manifest` | `{ir\|text}` 生成写清单 + 票据需求 |
| POST | `/workflows/parse_qwf` | `{text}` 文本 DSL → IR（带 lint 报告） |
| POST | `/workflows/lint` | `{text}` 体检（错误/警告/提示，不连硬件） |
| POST | `/workflows/qwf_examples` | 列出 `data/workflows` 下的 `.qwf` 范例 |
| POST | `/workflows/qwf_to_canvas` | `.qwf` → 画布节点 |
| POST | `/workflows/canvas_to_qwf` | 画布 → `.qwf` |
| POST | `/workflows/stmt_to_qwf` | 原子语句 IR → `.qwf` 片段 |
| POST | `/workflows/ir_from_canvas` | 画布 → IR |
| GET/POST | `/workflows/saved` | 列出/保存 `.qwf` |
| GET/DELETE | `/workflows/saved/{id}` | 取回/删除 |
| GET | `/runs`, `/runs/{id}`, `/runs/stats/{workflowId}` | 运行记录 |
| DELETE | `/runs/delete`, `/runs/workflow/{workflowId}` | 删运行记录 |

**改 `services/workflow_service/dsl/` 下的代码必须重启 :3008 才生效。**

---

## 4. task_queue :3009 —— 额度、票据、审计

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/health` | 健康 / 通道状态 |
| GET | `/lane` | 通道状态（前两层并发保险：执行层单实验 + qwf 层租约） |
| POST | `/submit` | 提交任务 |
| POST | `/status` | 任务状态 |
| POST | `/list` | 任务列表 |
| POST | `/cancel` | 取消任务 |
| POST | `/retry` | 重试任务 |
| POST | `/stats` | 统计信息 |
| POST | `/poll` | 轮询取任务（外部 worker 用） |
| POST | `/complete` | 标记任务完成并上报结果 |
| POST | `/lease` `/lease/status` `/lease/release` | 通道租约（获取/查询/释放） |
| POST | `/task` | 建 task → `{objective, stopCondition, budgets, qubits}` |
| GET | `/task` | 列出 task |
| GET | `/task/{id}` | task 现状 + 剩余额度 |
| POST | `/task/{id}/release` | 结束 task，冻结账本 |
| POST | `/task/{id}/refund` | 退还预扣额度（`kind=experiments\|datasets`） |
| POST | `/task/{id}/dataset` | 回填真实数据集号 |
| GET | `/task/{id}/report` | 审计报表（越界率/授权缺失率/代理放行率） |
| POST | `/task/{id}/manifest` | 保存写清单 → `{manifestHash}` |
| GET | `/task/{id}/manifest`, `/task/{id}/manifest/{hash}` | 读写清单 |
| POST | `/task/{id}/ticket` | 签发票据（绑定 manifestHash） |
| POST | `/task/{id}/ticket/{tid}/revoke` | 吊销票据 |
| POST | `/task/charge` | 消耗额度（超额返回 `quota_exceeded`） |
| POST | `/task/authorize` | 判定一次写动作是否在票据内 |

---

## 5. Express 网关 :3002 —— 前端入口 + 代理

代理前缀（`src/services/serviceProxy.ts`，统一挂在 `/api` 下）：
`/api/quantum/*`、`/api/analysis/*`、`/api/workflow/*`、`/api/workflows*`、`/api/workflow-runs*`、`/api/tasks/*`、`/api/llm/*`、`/api/agent/*`、`/api/image/*`。

自有用路由（`src/index.ts`）：
- 会话/比特：`GET /sessions`、`POST /sessions/switch`、`GET /sessions/tree`、`GET|POST /sessions/config`、`GET /sessions/status`、`GET /sessions/diagnostic`、`GET /sessions/test-load`、`GET /qubits`、`GET /qubits/:name/params`、`PUT /qubits/:name/params`、`GET /datasets`、`GET /datasets/plot`
- 绘图：`POST /sessions/plot`（**只透传 `{command}`，不透传 `dataset_index` → 只能画“最新”数据集；要画指定编号直连 `:3004/plot`**）、`POST /sessions/plot-historical`、`POST /api/analysis/plot/experiments`、`POST /api/analysis/plot/offline`
- 模式：`GET|POST /api/analysis/mode`、`GET|POST /api/quantum/mode`
- 作业：`POST /job`、`GET /job/:jobId`、`DELETE /job/:jobId`、`GET /jobs`、`GET /result/:jobId`、`GET /plot/:jobId`
- 工作流：`POST /workflow`、`GET|DELETE /workflow/:workflowId`、`GET /workflows`、`POST /api/run-node`、`GET|POST /api/workflows*`
- 配置：`GET|POST /api/models*`、`GET|POST /api/templates*`、`GET|PUT /api/experiments/configs*`、`GET|PUT /api/rules`、`GET /api/debug/routes`
- 其它：`/api/chat/*`、`/api/agent/*`、`/api/hermes/*`（含 skills/cron/memory）、`/api/skills*`、`/api/mcp-tools*`、`/api/classify/*`、`/api/experiments/run-analysis`

---

## 6. 其余服务

- **agent_service :3005** — `POST /chat`、`POST /chat/stream`、`GET /tasks`、`POST /tasks/status`、`GET /tools`、`GET|POST /memory/*`（episodes/sessions/skills/stats/recall/reflect/maintenance:forget）、`GET /api/v1/tools|qubits|experiments|health`。
- **llm_service :3006** — `POST /chat`、`GET /models`、`GET /stats`。
- **image_service :3007** — `POST /classify/single`、`POST /classify/folder`、`POST /train`、`GET /model/info`、`GET /training/status`。
- **qubitclient_service :3010** — `GET /families`、`POST /describe`、`POST /classify`、`POST /reasoning`、`POST /assess_fit`、`POST /extract_params`、`POST /evaluate`、`POST /analyze_full`。
- **hermes_service :3012** — `POST /chat`、`POST /chat/stream`、`WS /ws`、`GET /models`、`GET /sessions`、`/memory/*`、`/skills*`、`/cron*`、`GET /quantum-tools`、`POST /quantum-tools/execute`。

---

## 7. 真机功能实现规约（照着做）

1. **所有真机功能都走端点**：跑实验 → `POST :3003/execute`；读/写寄存器 → `GET|POST :3003/qubit/*`；
   绘图/拟合 → `POST :3004/plot`；批量标定/编排 → `POST :3008/workflows/run_ir`；
   额度与票据 → `:3009/task*`。**不要**在本地脚本里 `import lqms` 直连 LabRAD。
2. **实验一律 `do_plot=False`**；出图交给 `:3004 /plot`。
3. **写操作受闸门**：`quantum_service.actuation.allow_execute` / `allow_set_params`（默认 false），开关由人改配置或设 `QMCLAW_ALLOW_EXECUTE` / `QMCLAW_ALLOW_SET_PARAMS` 后重启。细则见 **§13**。
4. **改服务端代码要重启对应服务**才生效（`:3003` / `:3004` / `:3008` / `:3009` 各自独立进程）。
5. **画指定数据集走 `:3004 /plot`**（带 `dataset_index`），不要用 Express 的 `/sessions/plot`。
6. **服务掉线先自愈**：`:3003` stale → `POST /connect`（`reset_stuck:true`）；`:3004` `degraded_permanent` → `POST /connect`。
7. **跑完实验检查寄存器**：厂商实验会静默改 `fread`/`f10`/`bias_z`；用 `GET /qubit/registry` 对比，或用 `POST /qubit/apply_dataset` 按数据集快照复原。

## 8. 调用模板

```powershell
# 跑一个实验（do_plot=False！）
curl -s -X POST http://127.0.0.1:3003/execute -H "Content-Type: application/json" `
     -d '{"code":"import numpy as np\nsq.iqraw(q2ld3, do_plot=False)","timeout":120}'

# 读 / 写寄存器
curl -s -X POST http://127.0.0.1:3003/qubit/registry -H "Content-Type: application/json" -d '{"name":"q7ld3"}'
curl -s -X POST http://127.0.0.1:3003/qubit/set_params -H "Content-Type: application/json" `
     -d '{"name":"q7ld3","params":{"f10":3.650111}}'

# 按数据集快照复原寄存器
curl -s -X POST http://127.0.0.1:3003/qubit/apply_dataset -H "Content-Type: application/json" `
     -d '{"name":"q7ld3","dataset":1032,"dry_run":true}'

# 画指定数据集
curl -s -X POST http://127.0.0.1:3004/plot -H "Content-Type: application/json" `
     -d '{"command":"qter.fitData(1032, collect=True)","dataset_index":1032}'
```

## 9. 相关文档

- `docs/OPERATIONS.md` —— 端口表、启停、写闸门、两种跑实验方式、自检脚本清单、常见故障
- `docs/DSL_FACTS.md` / `docs/DSL_DESIGN.md` —— DSL 与治理层细节
- `docs/REAL_MACHINE.md` —— 真机写闸门提权与故障排查

---

## 10. 测控实验一律走 `.qwf` 路径（2026-11-05 用户确认）

所有 `sq.*` 实验都写成 `.qwf`，经 workflow_service(:3008) 的 `parse_qwf → validate → run_ir` 执行，
**不再直接 `POST :3003/execute` 下发裸代码**（`:3003/execute` 只用于一次性单实验与调试）。

- **生成**：`qwf_gen.py`；**执行**：`qwf_run.py`（先 `--dry` 干跑，再 `--if yes` 真跑）。
- **参数**：`args` 块传实验参数；`zpa`/`delay` 这类扫描轴**必须显式写**，否则厂商默认会展开成几千点。
- **必须带 `goal` 块，一次 `run_ir` 跑完**（机制见 `docs/DSL_DESIGN.md` §13）：

  ```
  goal { objective "…" success { metric r1.contrast operator >= value 0.6 } max_rounds 3 }
  ```

  由服务端 lowering 成 `repeat + if/else + assert`，**不要手写展开版**。
- **返回 `not_achieved` / `budget_exhausted` / `quota_exceeded` / `needs_human` 时**：
  先把 `outcome` 汇报给用户，得到确认后才允许发起下一个 qwf；
  `needs_human` 表示流程正等人拍板 —— 改完 `ask` 的问题后**重跑同一个 qwf** 即可续。
- 改 `dsl/` 源码后**必须重启 workflow_service**（模块被 `sys.modules` 缓存），否则新代码不生效。

## 11. 所有测控实验都经 task_queue 排队（2026-11-05 用户确认）

硬件通道只有一条，并发下发会互相污染寄存器。两层粒度：

- **执行层按单实验**：task_queue :3009 的 `HardwareLane`，全局并发度恒 1。
- **调度层按 qwf 租约**：整段工作流独占通道，默认 `yield_between_nodes=false`（中途不接受插单）。
- `POST :3008/workflows/run_ir` 在通道被占时**不报 `hardware busy`**，而是立刻返回
  `status="queued"` + `queuePosition`，由服务端等待线程接手续跑；调用方只需轮询 `/workflows/status`。
- **服务重启不重放**任何未完成的真机任务（`_abort_orphan_tasks` 直接标 failed）。
- 调试准入：`POST :3009/lease`、`/lease/status`、`/lease/release`、`/lane`。

## 12. 配置文件、离线模式与运行环境

### 12.1 配置文件（统一在 `qmclaw-server/config/`）

| 文件 | 说明 |
|---|---|
| `session.json` | DataVault session 配置 |
| `experiment_configs.json` | 测控实验配置（函数、绘图命令） |
| `model_configs.json` | LLM 模型配置 |
| `fallback_config.json` | 离线模式配置（`offline_mode`）与真机写闸门（`actuation`） |

**API Key 从环境变量读取，不写进配置文件**：
`OPENAI_API_KEY`、`MINIMAX_API_KEY`、`ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`。

### 12.2 离线模式（LabRAD 不可用时用历史数据）

- **配置**：`fallback_config.json` 的 `offline_mode` 字段。
- **切换**：`POST :3003/mode`、`:3004/mode`（Express 代理：`/api/quantum/mode`、`/api/analysis/mode`），
  参数 `{"mode": "online"|"offline"|"auto"}`；`online` 强制 LabRAD、`offline` 强制离线、`auto` 自动切换。
- **离线数据目录**：`qmclaw-server/data/offline_data/`。
- **索引生成**：`python -m services.common.offline_data_provider --generate-index`。

### 12.3 运行环境

- **Python 解释器**：`D:\anaconda3\envs\lqcs\python.exe`（含 lqms + labrad）；
  调 `services.common.config` 需要 `PYTHONPATH="D:/qmclaw/Agentic Workflow/qmclaw-server"`。
- **服务启停由人控制**（AGENTS.md 第 3 条）：`taskkill /PID <旧PID> /F` →
  `python "D:\qmclaw\Agentic Workflow\qmclaw-server\scripts\start_all_services.py"`。
- **沙箱限制**：不能写 `config/`、`docs/`、`.claude/`（用 write/edit 工具写）；
  不能在沙箱里 `import lqms` 直连 LabRAD；不能起第二个 Ray 客户端。

---

## 13. 真机写闸门（安全约定）

**写闸门默认关闭**：`fallback_config.json` 的 `quantum_service.actuation` / `analysis_service.actuation`
里 `allow_execute` 与 `allow_set_params` 默认均为 `false`。

- **任何打开写权限的操作必须由人显式执行并记录**（改配置或设 `QMCLAW_ALLOW_EXECUTE` /
  `QMCLAW_ALLOW_SET_PARAMS`，然后重启服务）。
- 闸门只在**服务端**强制；agent 侧只是提前解释。
- 执行超时后服务标记 `stuck` 并拒绝后续下发，须人工确认现场安全后再 `reset_stuck`（`:3003 /connect`
  带 `reset_stuck:true`）或重启。
- 提权步骤与故障排查见 `docs/REAL_MACHINE.md`。

### 13.1 治理闸门 `require_task`（2026-11-09 起）
`fallback_config.json` 的 `quantum_service.actuation.require_task`（默认 `false`）：

| 值 | 行为 |
|---|---|
| `false`（默认） | `/execute` 直发可用（调试习惯不受影响） |
| `true` | `/execute` **必须带 `task_id`**，否则返回 `status=blocked` + `reason_code=task_required` |

- 判定逻辑在纯函数 `services/common/governance.py::evaluate_require_task`，**每请求读取配置 → 改完即热生效，不用重启**；
  环境变量 `QMCLAW_REQUIRE_TASK=1` 可临时开/关。
- 带着 task_id 跑实验的正确姿势：先 `POST :3009/task` 建任务（拿 `task.id`）→ 再 `POST :3003/execute` 带 `task_id`；
  或直接走 `POST :3008/workflows/run_ir`（自动建 task + manifest + 票据）。
- 打开前建议先看 `docs/ARCHITECTURE_ROADMAP.md` §2.1（单写者原则）与 §4（在线升级协议）。

### 13.2 在线升级协议（后台改代码不打断用户实验）
1. **A 热配置**：`fallback_config.json` / 环境变量，每请求读取 → 改完即生效，不需重启。
2. **B 后台代码改动**：编辑源码不影响运行中的进程；验证一律走**离线自检**或**影子实例**（独立端口、不连 LabRAD）。
3. **C 纯函数热插**：新增的纯函数模块（如 `services/common/governance.py`）可在运行中直接生效。
4. **D 需重启**：改到 `server.py` 请求处理路径或 Express 路由 → 需要用户一次性重启窗口。
5. **铁律**：不 kill `:3003/:3008/:3009`；沙箱里不 `import lqms` 直连 LabRAD（第二个 Ray 客户端会卡在
   `Connecting to existing Ray cluster at 192.0.2.11:6479`）；后台验证不连 LabRAD；每批改动前后各 commit 一次。

### 13.3 真机实验前置授权协议（2026-11-09 用户确认，必读）

> 用户原话："接下来的升级我希望不要打断我这边的实验，你可以先做代码上的升级，需要做真机实验的时候向我提问，
> 我回复提问之后你就可以做测控实验了，这样就不影响。"

| # | 约定 |
|---|---|
| 1 | **代码级优先**：后台默认只做离线/影子改动与自检（如 `_phase2_check.py`），不触发任何硬件下发 |
| 2 | **提问前置**：任何需要真机测控实验的动作（跑 `sq.*`、写寄存器、扫频、占用 hardware lane）**先向用户提问**，等回复授权后再执行 |
| 3 | **提问内容**：做什么实验 / 目标比特与参数 / 预计耗时 / 会不会改寄存器（写哪些键）/ 如何回滚；用户可回复"可以 / 否 / 改参数" |
| 4 | **未授权即不执行**：没等到回复就一直等；用户正在跑实验时（lane 忙）同样先问 |
| 5 | **改寄存器单独确认**：写 `f10`/`fread`/`PiGate.*`/`bias_z` 属于"改现场状态"，必须在提问里显式说明并单独获得许可 |
| 6 | **影子先行**：能离线验证的一律不占真机通道；`/qubit/capabilities` 就是为"先看清能力再决定"准备的 |

典型场景 → 对应端点：
- "这个比特还有没有跃迁" → 先 `GET /qubit/capabilities`（离线）→ 再 `POST /qubit/probe_transition`（**真机，需授权**）。
- "刚才那批实验跑了什么" → `GET /qubit/runs`（只读台账，无需授权）。

---

## 14. 判据与指标真源
- **`config/lab_criteria.json` 是指标唯一真源**（contrast / t1 / t2_star / xeb / s21_dip / dmu_sig 的算法、参数、阈值、列口径）。
  写脚本、goal、报告时不要另立一套数字；改阈值要同时改该文件与 `.claude/progress.md`。
- 快速入口：`skills/qubit-contrast-tuning/SKILL.md` 第七节（判据速查 + 三条易翻车现场事实）。

---

## 15. 真机实验的前置检查与证据闸门（2026-11-09 补充）

> 来源：q7ld3 spectroscopy 实战复盘。教训一句话：**先确认"仪器实际做了什么"，再解释"数据说明了什么"。**
> 驱动幅度为 0 时，"无共振"是废话；驱动频率没跟着扫描走时，"扫频无峰"是伪结论；看不清图时，
> "图上没峰"是无效结论。四项前置检查不全，**不允许下结论**。

### 15.1 前置检查（四项，缺一不可）

| # | 检查项 | 怎么做 | 不做会怎样 |
|---|---|---|---|
| 1 | **厂商 API 默认值** | 跑之前先 `POST :3003/execute` 做只读 introspection，打印 `inspect.signature(...)` 与默认值 | `sq.spectroscopy` 的 `spec_amp` 默认 **0.0** → 零驱动空谱，白跑 400 s |
| 2 | **驱动参数非零且语义正确** | 显式写 `spec_amp`（参考 `0.8*PiGate.amp*PiGate.length/spec_len`）；`zpa` 写进寄存器 `bias_z`，不要在实验参数里再叠一层 | 有效偏置变成两者的和（-1 + -1 = -2），测的是另一个工作点 |
| 3 | **驱动频率 = 扫描点** | 核对厂商映射：`fc = 扫描频率 - sb_freq`，驱动频率 = `fc + sb_freq` | 扫频是假的，"无峰"结论无效 |
| 4 | **数据通道可读且判据明确** | 经 `:3003/execute` 只读 `data.loadDataset(ds)` 导出数值数组（CSV/JSON），不要凭服务生成的 PNG 下结论 | 模型可能读不到图；`:3004` 的 `allow_execute` 常为 false，只能 `/plot` 不能 `/execute` |

运行时模块是**编译版 .pyd**（`sq_nodes.cp311-win_amd64.pyd` / `base_experiment.cp311-win_amd64.pyd`），
`inspect.getsource` 不可用 → 只能读 `.py` 源码 + 用 `inspect.signature` 反射。

### 15.2 timeout 预估（别用默认值撞校验）

workflow_service 的 lint 会按**最坏单点耗时**估算：`预计运行 1406s，超过语句 timeout=900s`。
预估公式：`timeout ≥ 扫描点数 × 1 s`（1400 点 → ≥1400 s，建议再乘 1.5 留余量），
同时 `budget.max_wall` 要大于该 timeout，否则 lease/额度侧先失败。

### 15.3 goal 判据模板（非 IQraw 数据集）

goal 模式默认要求 `read kind IQraw`，而 lint D5 只允许 **IQraw 形状**的数据集吃 `read`；
spectroscopy / s21 / pidf 等频谱数据集不是 IQraw → 会被拒。改用数据集存在性作判据：

```
goal {
  objective "在 q7ld3 的 zpa(bias_z)=-1、spec_amp=0.05 状态下扫 spectroscopy，采集 2.0-5.5 GHz 频谱数据集，用于定位 0-1 跃迁频率"
  success { metric $c1.dataset >= 0 }
  max_rounds 1
  on_achieved "continue"
}
```

### 15.4 跑完的后置核对

- 回读 `POST :3003/qubit/registry`：确认 `f10`/`f21` 没被静默回写
  （`Spectroscopy._init_axes` 会把 `param["f10"]` 赋成扫描轴；`update=false` 可避免回写）。
- 写闸门只写必要的键（例：只写 `bias_z`），并把**原值记录在案**以便复原。
- 汇报四件套：`outcome` / 数据集号 / 扫描范围与步长 / **是否找到频率 + 证据**，拿不准就问用户。


