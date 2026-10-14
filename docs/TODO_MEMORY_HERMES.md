# TODO — Agent Memory 功能 + Hermes 模型接口对齐

> 目标（用户原话）：
> 1. 完成 agent 接口下的 memory 功能，要求记录每一轮的对话
> 2. 配置 hermes 的模型接口，要求和 agent 接口的模型接口一致
>
> 本文档基于**实测**（非推断）形成。关联：`docs/TODO_RUNTIME.md`、`docs/RUNTIME_DEPS.md`。

---

## 执行结果（两个任务均已完成并端到端验证）

| 项 | 状态 | 证据 |
|---|---|---|
| M1 memory 存储层移植 | ✅ | `services/common/memory_store.py` + `reflection_engine.py`，路径解析实测指向 `<server>/config/memory` |
| M2 `/memory/*` 端点 | ✅ | 从 `502 {"error":"Unknown path: /memory/episodes"}` 变为 `200`，8 个端点全通 |
| M3 **记录每一轮对话** | ✅ | 对话返回 `episode_id`；`episodeCount 0 → 1`；落盘含 `sessionId / roundIndex / model / status / steps / metrics` |
| M4 recall 接线 | ✅ | `/memory/recall` 返回 `hasMemory/relevantSkills/recentEpisodes/qubitHistory/context` |
| M5 memory 验收 | ✅ | `stats.episodeCount == list.count`；`stats.skillCount == skills.count`（修复前后者不一致） |
| H1 统一模型列表来源 | ✅ | `/api/models` 与 `/api/hermes/models` 均返回 **10 个**，**id 序列一致**、**字段结构一致** |
| H2 统一标识符语义 | ✅ | `deepseek-flash`(id)、`model_default_001`(id)、`gpt-4o`(modelId)、`GPT-4o`(name) 全部正确解析 |
| H3 修死默认值 | ✅ | 删除 `"minimax/MiniMax-M2.7"`；两个面板默认都选 `deepseek-flash` |
| H4 共用解析函数 | ✅ | `hermes_service._resolve_model_config()`，两处调用点统一 |
| H5 hermes 验收 | ✅ | hermes 对话 `completed=True / '收到'`；两面板下拉框内容完全一致 |
| V1 挂死定位 | ✅ | **确认是 `HERMES_HOME` 不可写所致**（详见下） |
| V2 hermes 可观测性 | ✅ | 加了 `Model ... resolved via ...` 日志，能直接看到命中哪个字段 |

### 关键结论：hermes `/chat` 挂死的根因

实测：`HERMES_HOME` 默认是 `%LOCALAPPDATA%\hermes`，在受限环境下**不可写**，
AIAgent 会在 `run_conversation` 里卡死（>300s，且一行日志都没有）。

把 `HERMES_HOME` 指到项目内后：

```
HERMES_HOME = D:\semester4\QMClaw\...\qmclaw-server\data\hermes
AIAgent 构造完成 7.2s
run_conversation 完成 4.0s
completed = True   final_response = '收到'
```

已在 `.env` 里显式设置 `HERMES_HOME`，hermes 对话经网关端到端 **8.3s** 返回。

---

## 一、现状调查（实测证据）

### 1.1 Memory 链路：前端和网关都做完了，只差后端

| 层 | 状态 | 证据 |
|---|---|---|
| 前端 UI | ✅ 已实现 | `qmclaw-web/src/components/MemoryPanel.tsx`（23.5 KB），调用 7 个 api 方法 |
| 前端 API | ✅ 已实现 | `qmclaw-web/src/lib/api.ts:1124-1180`，8 个 `memory*` 方法 |
| Express 网关 | ✅ 已接好 | `src/index.ts:2360-2445`，微服务模式下 `proxyToService('agent', '/memory/...')` |
| **agent_service** | ❌ **完全没有** | 路由只有 `/health` `/chat` `/chat/stream` `/tasks` `/tasks/status` `/tools`、`/api/v1/*` |
| 旧实现（legacy） | ✅ 存在但只服务 job_runner | `scripts/memory_store.py`(12 KB)、`scripts/reflection_engine.py`(17.8 KB) |

**实测失败**：

```
POST /api/agent/memory/episodes  -> HTTP 502  {"error":"Unknown path: /memory/episodes"}
GET  /api/agent/memory/stats     -> HTTP 502  {"error":"Unknown path: /memory/stats"}
GET  /api/agent/memory/skills    -> HTTP 502  {"error":"Unknown path: /memory/skills"}
```

**数据目录也不存在**：`config/memory/episodes` 和 `config/memory/skills` 均无（`config/memory` 本身在）。
`memory_store.py:28-29` 的 `init_memory()` 才会创建。

**结论**：这不是"从零做 memory"，而是**把已经写好、已经在 legacy 模式跑通的 memory 移植进 agent_service**，
并在对话路径上挂上"记录每一轮"的钩子。前端一行都不用改。

### 1.2 Hermes 模型接口：与 agent **在三处不一致**

| 维度 | Agent（`AgentChatPanel`） | Hermes（`HermesChatPanel`） |
|---|---|---|
| 模型列表来源 | `GET /api/models` → `modelService.listModels()` 读 `model_configs.json` | `GET /api/hermes/models` → **Express 内联实现**（`index.ts:2623`）自己读同一个文件 |
| 返回字段 | 完整条目（`id`,`name`,`provider`,`modelId`,`enabled`,`capabilities`,`config`,…） | 裁剪成 `{id, name, provider, base_url}` |
| `id` 的含义 | 条目的 **`id`** 字段 | 条目的 **`modelId`**（`m.modelId \|\| m.name`） |
| 排序 | 按 `updatedAt` **倒序** | **文件原序** |
| 前端发送 | `context.model_id` = 下拉框 value = `id` | `model` = 下拉框 value = `id` |
| 后端匹配 | `llm_service` 按 **字符串前缀** 路由（`model.startswith("deepseek")`） | `hermes_service` 按 **`modelId` 或 `name`** 匹配（`server.py:328` / `:484`） |
| 默认值 | 第一个 enabled 的 text 模型 | **硬编码 `"minimax/MiniMax-M2.7"`**（`HermesChatPanel.tsx:34`）—— 这个字符串在 `model_configs.json` 里**根本不存在** |

**实测**：
```
GET /api/hermes/models  第一条字段 = ['id','name','provider']            id = deepseek-flash
GET /api/models         第一条字段 = ['id','name','provider','modelId','enabled','capabilities',...]
```

**当前为什么"看起来能跑"**：我给 `model_configs.json` 加的 `deepseek-flash` 条目里
`id == modelId == "deepseek-flash"`，两边正好撞对了。**换任何别的模型就会错位** ——
例如 agent 侧发 `model_default_001`（内部 id），llm_service 认不出前缀 → 走兜底；
hermes 侧 Express 把 `id` 映射成 `modelId`（`gpt-4o`），后端按 `modelId` 匹配 → 能中。
**两套语义，靠巧合工作。**

### 1.3 顺带发现的两个问题（不在本次范围，但会影响验收）

**F1 — hermes `/chat` 会挂死（>300s 无响应）**

```
POST http://localhost:3012/chat      -> 耗时 302.0s  TimeoutError
POST http://localhost:3002/api/hermes/chat -> HTTP 502 "Failed to connect to hermes service: The operation was aborted due to timeout"
```
`/health` 正常、`Successfully imported AIAgent`、`Loaded 10 model configurations` 都正常，
卡在 `server.py:510` 的 `agent.run_conversation(message)`。
**初步怀疑是沙箱所致**：服务跑在我的后台任务里，AIAgent 想写 `%LOCALAPPDATA%\hermes` 被拒
（早前实测见过 `WinError 5 拒绝访问。: 'C:\Users\39045\AppData\Local\hermes'`）。
**需在不受限的终端里复测**才能判定是环境问题还是代码问题。

**F2 — QCA 用了没有 key 的 provider**
```
[qca] Using model: minimax:MiniMax-Text-01
[qca] API Key configured: No (empty)
```
`qca_service` 硬编码走 MiniMax，而环境只有 DeepSeek key。与本次任务无关，但要单独修。

---

## 二、Todolist

### 任务 1：Agent Memory（记录每一轮对话）

- [ ] **M1. 把 memory 存储层移植进 `agent_service`**
  - 新建 `services/common/memory_store.py`（或 `services/agent_service/memory_store.py`），
    以 `scripts/memory_store.py` 为蓝本
  - 保持**磁盘格式完全不变**（`config/memory/index.json` + `episodes/*.json` + `skills/*.json`），
    这样 legacy 模式写的数据 microservice 模式读得到，反之亦然
  - Episode schema（照抄 `memory_store.py:79-93`）：
    `id / timestamp / task / qubit / mode / steps / status / metrics / tags / usedSkills / reflection / skillCreated / archived`
  - 路径必须相对**服务根**解析（`_SERVICE_DIR.parents[N]`），不要用 `os.path.dirname(__file__)/..`
    那种依赖 cwd 的写法

- [ ] **M2. 在 `agent_service` 暴露 `/memory/*` 端点**（对齐 Express 已有的代理路径）
  | 端点 | 方法 | 对应 Express 代理 |
  |---|---|---|
  | `/memory/episodes` | POST | `/api/agent/memory/episodes` |
  | `/memory/episodes/{id}` | GET | `/api/agent/memory/episodes/:id` |
  | `/memory/episodes/{id}` | DELETE | 归档 |
  | `/memory/skills` | GET | 列表 |
  | `/memory/skills/{id}` | DELETE | 删除 |
  | `/memory/stats` | GET | 统计 |
  | `/memory/recall` | POST | 召回 |
  | `/memory/reflect` | POST | 反思 |
  - 路由要处理**路径参数**（`/memory/episodes/{id}`），而 `agent_service.handle_request` 现在是纯 `==` 比较，需加前缀匹配

- [ ] **M3. 记录每一轮对话（核心需求）**
  - 在 `_handle_chat` 完成后落一条 episode：
    - `task` = 用户消息
    - `steps` = ReAct 每一轮的 `{iteration, assistant, action, observation}`
    - `status` = `success` / `failed`
    - `metrics` = `{iterations, duration_ms, tokens?}`
    - `mode` = `react`
  - **新增 `session_id` 与 `round_index` 字段**（原 schema 没有），用于把多轮对话串成一次会话
    —— 「记录每一轮」需要能区分"同一会话的第 N 轮"和"不同会话"
  - `_react_reason` 目前只返回最终结果、丢掉了中间轮次，需要把 `task.messages`（已有）转成 `steps`
  - 落盘失败不能影响对话返回（try/except 包住，只记日志）

- [ ] **M4. `/memory/recall` 接上 `reflection_engine`**
  - `scripts/reflection_engine.py:468` 的 `recall_for_task(task, qubit)` 已实现，
    但它的 `_get_api_key_for_provider` 老逻辑可能只认 miniMax/openai，需确认能走 deepseek
  - 召回结果注入 system prompt（legacy `job_runner.py` 有现成做法可参考）

- [ ] **M5. 验收**
  - `POST /api/agent/chat` 一轮后，`POST /api/agent/memory/episodes` 能列出该轮
  - `GET /api/agent/memory/stats` 计数 +1
  - 前端 Memory 面板能看到记录、能点开详情、能归档
  - 连续 3 轮对话 → 3 条 episode，且 `session_id` 相同、`round_index` 递增

### 任务 2：Hermes 模型接口与 Agent 对齐

- [ ] **H1. 统一模型列表来源**
  - 删掉 `index.ts:2623-2645` 的内联实现，`/api/hermes/models` 改为复用
    `modelService.listModels()`（和 `/api/models` 同一个函数、同一份数据）
  - 返回结构与 `/api/models` 完全一致（不再裁剪字段、不再改 `id` 语义、同样按 `updatedAt` 排序）
  - 兼容性：`HermesChatPanel` 只用 `id` 和 `name`，字段变多不影响它

- [ ] **H2. 统一模型标识符语义**
  - 前端两边都发条目的 **`id`**
  - `hermes_service` 的匹配逻辑（`server.py:328` 与 `:484`）改为与 agent 一致：
    **优先精确匹配 `id`**，再退回 `modelId` / `name`（保持向后兼容）
  - 抽成一个共用函数，避免两处 `_handle_chat` / `_handle_chat_stream` 重复

- [ ] **H3. 统一默认模型**
  - `HermesChatPanel.tsx:34` 的 `DEFAULT_HERMES_MODEL = "minimax/MiniMax-M2.7"` 是**死字符串**，删掉
  - 改为与 `AgentChatPanel.tsx:66-68` 相同策略：**模型列表加载完后取第一个 enabled 的 text 模型**
  - 顺手修 `HermesChatPanel.tsx:59-60` 的"硬编码兜底模型列表"，它会把不存在的 `minimax/...` 塞进下拉框

- [ ] **H4. 后端 provider 路由对齐**
  - 现状：agent 走 `llm_service`（按前缀路由），hermes 走 `hermes_service` → 直接 `AIAgent(base_url, api_key, model)`
  - 目标不是把 hermes 改成走 llm_service（AIAgent 需要真 key + base_url），
    而是**保证同一个模型 id 在两边解析到同一个 provider/model/key**
  - 抽出 `resolve_model_config(model_id) -> (provider, modelId, baseUrl, apiKey)` 共用

- [ ] **H5. 验收**
  - `GET /api/models` 与 `GET /api/hermes/models` 返回**同样的条目、同样的顺序**
  - 两个面板的下拉框默认选中**同一个模型**
  - 选定某个模型后，两侧后端日志显示解析到同一个 `provider` + `modelId`

### 任务 3（前置依赖 / 风险）

- [ ] **V1. 判定 hermes `/chat` 挂死是沙箱问题还是代码问题**（见 F1）
  - 在**用户自己的终端**（不受沙箱限制）跑一次 `POST :3012/chat`，看是否仍然 >300s
  - 若只是沙箱：本次不动它，但要在文档里标注"服务必须在非受限终端运行"
  - 若确实挂：需要单独排查 AIAgent 初始化（可能与 `skip_memory`、state DB、toolsets 有关）
- [ ] **V2. 为 hermes chat 加超时与可观测性**
  - 现在挂死时**一行日志都没有**（`_log` 在 `_handle_chat` 里，但请求未走到就阻塞了）
  - 在 `run_conversation` 外层加超时保护 + 进入/退出日志，便于下次定位

---

## 三、设计决策（待确认）

| # | 问题 | 我的建议 |
|---|---|---|
| D1 | 「每一轮对话」的粒度：一次 `/chat` 请求算一轮，还是 ReAct 的每次迭代算一轮？ | **一次 `/chat` = 一条 episode（一轮）**，ReAct 迭代存进该 episode 的 `steps`。这样前端列表不会爆炸，且信息不丢 |
| D2 | 是否给 Episode 加 `session_id` / `round_index`？ | **加**。原 schema 无法把多轮串起来，"记录每一轮"必须能按会话查看 |
| D3 | memory 存储放 `services/common/` 还是 `services/agent_service/`？ | `services/common/`，与 `offline_data_provider.py` 等公共模块一致，未来 hermes 也能复用 |
| D4 | 前端要不要改？ | **任务 1 一行不改**（接口已对齐）；**任务 2 只改 `HermesChatPanel.tsx` 的默认值与兜底列表** |
| D5 | `memory_store.py` 留在 `scripts/` 还是移走？ | **留在原地**。legacy 模式还在用（`job_runner.py` 通过 `sendbackendRequest("memory_*")` 调），移走会破坏它 |

---

## 四、执行顺序

```
M1 → M2 → M3        （memory 主链路，M3 是核心需求）
H1 → H3 → H2/H4     （先统一数据源，再统一语义）
M4 → M5 → H5        （召回 + 双向验收）
V1 → V2             （hermes 挂死定位，可与上面并行）
```

**每步完成后**：`py_compile` 语法检查 → 重启对应微服务 → 打接口实测 → 记录证据。
（注意：改 Python 代码后**必须重启对应微服务**，Express 每次请求现读配置文件不用重启。）
