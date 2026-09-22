# 相对源代码的改动与已实现功能

> **基线**：`f2d83b5`（Merge pull request #2 from Secsix/main）—— 本次工作开始前的仓库状态
> **范围**：`f2d83b5..HEAD`，14 个提交，41 个文件，+4469 / −285
> **本文档回答两个问题**：改了源代码的什么、现在能实现什么

---

## 一、现在能实现什么

一条主链路被打通了：**在 agent 页面用自然语言下指令 → 自动执行实验 → 返回可读结果与图表**。

| 能力 | 状态 | 说明 |
|------|------|------|
| 自然语言驱动实验 | ✅ | 「对 q10lu12 运行 t1 实验」→ 自动选工具、执行、回填结果 |
| 真实离线历史数据 | ✅ | 有历史数据的 qubit 走真实数据；没有的走合成并**明确标注** |
| 图表生成与访问 | ✅ | 生成 PNG，前端经 `/plots/` 访问（HTTP 200） |
| 长期记忆 | ✅ | 每轮对话落 episode，`/memory/*` 8 个端点可用 |
| 多轮会话串联 | ✅ | 同一 `sessionId` 下 `roundIndex` 递增 |
| 模型切换 | ✅ | 10 个模型配置；agent 与 hermes 两侧接口一致 |
| 运行模式 | ✅ | 离线优先，无需 LabRAD 即可跑通全链路 |

### 实测两个场景

**场景 1 —— `对 q10lu12 运行 t1 实验`**
该 qubit 在离线库中只有 `s21` + `s21power2d`，**没有 T1 数据**：

```
success=True  iterations=2  steps=2  charts=1
```

正确降级到合成数据，并在响应里带免责声明。

**场景 2 —— `对 q10lu8 运行 t1 实验`**
该 qubit 有真实 T1 数据（`data/offline_data/20251103.dir/00398 - q10lu8%c T1.hdf5`）：

```
success=True  iterations=2  steps=1  results={"T1 (µs)": 8}  charts=1
```

无合成警告，走的是真实数据路径。

---

## 二、改动前的问题（源代码的实际状态）

五个各自独立、但都指向"链路跑不通"的缺陷：

1. **工具从不执行**
   agent 的 ReAct 循环解析文本 `Action: tool(args)` 协议，但 system prompt 从未教过这个格式。模型输出的是它原生的工具调用标记（`｜DSML｜`），解析不到就被当成最终答案直接返回（实测 `iterations=1`）。工具注册表存在但形同虚设。

2. **`RunExperimentTool` 注册错误**
   它的契约是 `code_builder(name, params) -> 代码字符串`，而旧实现传入的 `run_experiment` 返回结果 dict，于是 executor 把 dict 当代码 POST 出去，服务端 `code[:100]` 抛 `unhashable type: 'slice'`（HTTP 500）。

3. **真实离线数据从未被解析**
   即使 qubit 有历史数据也一律走合成路径（stdout 里永远是 `No matching historical data found`）。根因是两处独立缺陷：加载器没拆复合 dtype、扫描轴单位差 1e9。

4. **前端契约字段一个都没返回**
   `AgentChatPanel` 期望 `steps` / `results` / `charts`，后端一个都不给 —— 步骤区和结果卡片永不渲染。

5. **memory 功能在两个模式下都从未跑通**
   前端 `MemoryPanel` 和 Express 的 `/api/agent/memory/*` 代理都在，但 agent_service 没有任何 `/memory/*` 路由（实测返回 502 Unknown path）。

---

## 三、改动清单

### 1. agent 工具循环：改用原生 function calling

**提交**：`edf528c` `b016df1`
**文件**：`services/llm_service/server.py`（+95/−13）、`services/agent_service/server.py`（+540）、`services/agent_service/adapter.py`、`services/common/quantum_tools/execution.py`

**llm_service —— 打通 tools 透传**

- `_handle_chat` 接受并透传 `tools` / `tool_choice`；**不带 tools 时行为完全不变**（向后兼容）
- `_call_deepseek` / `_call_openai` 在 tools 非空时写入 payload（两者同为 OpenAI 兼容形状）
- `_call_minimax` 不支持工具调用，带 tools 时记 warning 后继续，不报错
- 新增 `_normalize_tool_calls`：把 API 返回的 `tool_calls` 归一化成 `[{id, name, arguments(dict), arguments_raw}]`。`arguments` 是 JSON 字符串需解析，解析失败保留原始串并标 `parse_error`
- 响应新增 `tool_calls` / `finish_reason` / `assistant_message` —— 后者供上层原样回灌，因为原生协议要求 assistant 的 `tool_calls` 消息与其后的 `role="tool"` 消息**成对出现**

**agent_service —— 重写推理循环**

- `_react_reason` 重写为原生循环：`registry.get_definitions()` 作为 tools 传给模型 → 读 `tool_calls` → `registry.execute` → 结果以 `role="tool"` 回灌 → 直到模型不再调用工具
- 删除文本 `Action:` 解析分支（含 eval 参数解析）
- 新增 `_compact_for_llm`：曲线数组只留点数，避免把 100 个数据点塞进 LLM 上下文
- 新增 `_summarize_tool_result`：按类型分派 —— `get_qubits` 返回 **list**、实验类返回 **dict**，不判类型会直接抛异常
- system prompt 重写：工具参数由 schema 携带，prompt 只补行为约束

**前端契约补齐**（原先一个字段都没返回）

- `steps`: `[{tool, input, observation}]`
- `results`: `Record<string, number>`，**做单位换算** —— 前端用 `v.toFixed(4)` 渲染，T1 = 5.56e-5 秒会被显示成 `0.0001`，故换算为 µs / GHz
- `charts`: 绘图路径，由前端 `public/` 静态提供
- 合成数据时**强制**在 response 前加免责提示，不依赖模型自觉

**adapter**

- 修复 `RunExperimentTool` 注册：改传 `build_experiment_code`
- 新增 `build_experiment_code`：按 `config/experiment_configs.json` 的 `defaultCommand` 模板生成代码。旧实现拼成 `sq.t1(target='q10lu12')`，而权威约定是 `sq.t1(q10lu12, do_plot=True)` —— **qubit 必须是位置参数**，否则离线模拟器的解析正则会把整串当 qubit 名；同时修正函数名映射（ramsey 实际是 `sq.ramsey_df`）
- `_http_call` 的 HTTPError 分支带上响应体，否则 500 的真实原因全被丢掉
- 删除已成死代码的 `run_experiment`

**quantum_tools/execution.py**

- 成功判定接受 `offline_simulated`（离线跑通却被报成 failed）
- 结果字段映射：离线返回的是 `data` / `metrics` / `plotPath`，而不是 `result`

---

### 2. 离线数据链路：真实历史数据首次可用

**提交**：`07ceb0d`
**文件**：`services/common/offline_data_provider.py`、`services/quantum_service/offline_simulator.py`、`services/quantum_service/server.py`

**(1) 加载器拆复合列**

本仓库所有离线 HDF5 的根下只有 `DataVault`，是一个**复合 dtype 的 1 维数组**：

```
f0  = 扫描轴（t1/ramsey 是 ns，s21/spectroscopy 是 GHz）
f1.. = 测量列
```

旧代码先命中 `ndim == 1` 分支，把整块复合数组塞进 `data['x']`，模拟器找不到 y。改为优先识别 `arr.dtype.names` 并拆列：`x = f0`、`y = f1`、其余进 `series`。

**(2) 扫描轴单位归一化**

历史数据用 ns / GHz，而模拟器的合成路径用 SI，两者相差 1e9 量级。新增 `X_UNIT_SCALE` 换算表与 `_load_data`，在**计算 metrics / 生成 stdout 之前**完成换算 —— 否则 stdout 会留下未换算的原始值（T1 报成 8000）而 metrics 已是换算值，两者自相矛盾。

| 实验族 | 系数 |
|--------|------|
| `t1` / `ramsey` / `ramsey_df` / `spinechocpmg` | `1e-9` |
| `s21` / `s21_dis` / `s21power2d` / `s21zpa2d` / `spectroscopy` / `spectroscopyauto` | `1e9` |
| `iqraw` / `piamp*` / `xeb` 等序号或无量纲量 | 不换算 |

**(3) 顺带修 JSON 序列化崩溃**

`_generate_plot_data` 把 numpy 数组直接放进 `x`/`y`（合成路径用了 `tolist`，真实路径没有），导致 `json.dumps` 抛 `Object of type ndarray is not JSON serializable`，响应退化成 500。改为源头 `tolist`，另加 `quantum_service._json_safe` 做兜底递归转换。

---

### 3. memory 长期记忆：从 502 到完整可用

**提交**：`73b257b`
**文件**：`services/common/memory_store.py`（新增，425 行）、`services/common/reflection_engine.py`（新增，556 行）、`services/agent_service/server.py`

**存储层**

- `memory_store.py` 由 `scripts/memory_store.py` 移植，**磁盘格式完全不变**（两种模式的数据可互读）
- 路径改为按服务根解析 —— 原实现写的是 `dirname(__file__)/../config`，那是站在 `scripts/` 下的正确写法，搬到 `services/common/` 后会解析成 `services/config`（错的）
- `reflection_engine.py` 同步移植，导入改为包内相对导入并保留脚本回退
- `get_memory_stats` / `list_skills` 统一为"文件真实存在"口径，修复"stats 说有 N 条、list 却一条都列不出来"的不一致；索引里文件缺失的历史残留用 `orphanCount` 透明暴露

**接口**

- agent_service 新增 `/memory/{episodes,skills,stats,recall,reflect}` 共 8 个端点
- `handle_request` 增加前缀路由，以支持 `/memory/episodes/{id}` 这类路径参数

**记录每一轮对话**

- `AgentTask` 增加 `steps` 字段；`_react_reason` 每轮记录 `{iteration, assistant, action, observation}`
- `_handle_chat` 完成后落一条 episode，并回填 `sessionId` / `roundIndex` / `model` / `response`（原 schema 没有这几个字段，无法把多轮串成一次会话）
- 写盘失败不影响对话返回；失败的一轮同样记入，以便前端看到失败案例

---

### 4. hermes 模型接口对齐 + `/chat` 挂死修复

**提交**：`9985eba`
**文件**：`services/hermes_service/server.py`、`services/llm_service/server.py`、`src/index.ts`、`qmclaw-web/src/components/HermesChatPanel.tsx`、`config/model_configs.json`

**模型接口不一致的三处**（此前靠"id 恰好等于 modelId"侥幸工作，换任何别的模型就错位）

| 问题 | 修法 |
|------|------|
| `/api/hermes/models` 是 Express 内联读 `model_configs.json` 的**另一套实现**，且把 id 映射成 `m.modelId` | 改为复用与 `/api/models` 相同的 `listModels()` |
| hermes_service 只匹配 `modelId`/`name`，而前端传的是条目的 `id` | 抽出 `_resolve_model_config()`，优先级改为 `id -> modelId -> name`，两处调用点统一 |
| `HermesChatPanel` 硬编码默认值 `minimax/MiniMax-M2.7` 在配置里根本不存在 | 改为复用与 `AgentChatPanel` 同一个 `useModelStore`，取第一个 enabled 的 text 模型；删掉会把不存在模型塞进下拉框的硬编码兜底列表 |

**修复 hermes `/chat` 挂死**（>300s 无响应且无日志）

根因是 `HERMES_HOME` 默认指向 `%LOCALAPPDATA%\hermes`，在受限环境下不可写，`AIAgent` 卡死在 `run_conversation`。已在 `.env` 显式指向项目内 `data/hermes`，实测 **8.3s** 正常返回。

**其他**

- `_call_deepseek` 现在真正使用传入的 `model`（原来硬编码 `deepseek-chat`，上层选什么都无效）
- 未知 model 回退到"已配置 key 的 provider"，而不是硬走 MiniMax
- 去掉三行会**完整打印 API Key** 的调试日志，改为只报长度
- `config/model_configs.json` 新增 `deepseek-flash` 条目

---

### 5. 本机运行接线

**提交**：`0635fa0`
**文件**：`config/session.json`、`requirements.txt` ×3、`run-dev.cmd` ×2、`scripts/start_all_services.py`、`analysis_service/server.py`、`qca_service/server.py`、`qca_service/tools/lab_tool.py`、`quantum_service/labrad_client.py`、`qubitclient_service/server.py`、`offline_data_provider.py`、`test_offline.py`、`test_load_qubits.py`

- `session.json` 指向本机实测可用的 `test/20260324`
- **`start_all_services.py` 显式把 stdout/stderr 切到 UTF-8** —— 脚本会打印 ✅/⏳/⚠️，而 Windows 控制台/管道默认是 GBK，会抛 `UnicodeEncodeError` 并让整个启动流程异常退出，**连带杀掉已经起来的子服务**（表现为"服务其实起得来、却被收尾逻辑干掉"）
- 各处路径改为按本机实际目录结构解析，不再依赖 cwd
- 补齐依赖声明

---

### 6. 文档、校验工具与约定

**提交**：`0d77a0b` `59cbc6a` `447e363` `470d847` `e288bd5` `221abf3`

| 文件 | 内容 |
|------|------|
| `docs/DEPLOY_DIFF.md` | deploy 包与项目的逐项差异（vendor / lqms / qubitclient / offline_data 等） |
| `docs/RUNTIME_DEPS.md` | 运行所需依赖与接线说明 |
| `docs/TODO_RUNTIME.md` | 本机运行待办清单 + 阶段执行记录 + 4 条已知局限 |
| `docs/TODO_MEMORY_HERMES.md` | memory 与 hermes 的实施记录 |
| `docs/deploy_diff_tools/` | 7 个**只读**校验脚本（导入 / 依赖 / vendor / 服务 / 配置一致性） |

**`CLAUDE.md` 约定变更**

- 约定 10：python 路径改为本机实际的 `D:\semester4\deploy\qmclaw\qmclaw\python.exe`（原 `C:\Users\HUAWEI\...` 是他人机器路径，本机不存在）
- 约定 3：Next.js 端口 `3001 -> 8081`（`qmclaw-web/package.json` 的 dev 脚本写死 `-p 8081`，实测 3001 未监听）
- 约定 11：提交时机从"每改一处就提交"改为**按节奏提交**（攒够 3~5 处，或重要节点前后各一次）
- 新增「记忆数据（不纳入版本控制）」一节

**`.gitignore`**

- `config/memory/` **整目录**忽略。原规则只忽略了 `episodes/` 与 `skills/` 两个子目录，**漏了 `index.json`** —— 而它每轮对话都会被重写，导致工作树长期带一个 modified 文件；且它单独存在没有意义（引用的是未跟踪的实体文件，clone 下来只是一份悬空目录）
- 删除 `CLAUDE.md` 一行：该文件已被跟踪，此规则当前无效，但一旦被 untrack 就会**静默**变成不可提交，属于埋雷

---

## 四、验收证据

| 检查项 | 结果 |
|--------|------|
| 10 个 Python 服务端口 | `3003`–`3012` 全部 `LISTENING` 且 `/health` 返回 `healthy` |
| Express 网关 `:3002` | health OK，microservices 模式，agent 指向 `:3005` |
| Next.js `:8081` | HTTP 200 |
| 场景 1：`对 q10lu12 运行 t1 实验` | `success=True iterations=2 steps=2 charts=1`，带合成数据免责声明 |
| 场景 2：`对 q10lu8 运行 t1 实验` | `success=True iterations=2 steps=1 results={"T1 (µs)": 8} charts=1`，无合成警告 |
| 图表可访问 | `:8081/plots/...` 返回 HTTP 200 |
| memory 接口 | `/memory/stats` 返回 `episodeCount` / `skillCount` / `orphanCount`，与 `/memory/episodes` 列表计数一致 |
| 模型接口一致性 | `/api/models` 与 `/api/hermes/models` 均返回 10 个，id 序列与字段结构完全一致 |
| hermes 对话 | 经网关 `completed=True`，耗时 8.3s（原先 >300s 挂死） |

---

## 五、已知局限（已记录、未修）

记录于 `docs/TODO_RUNTIME.md`：

| 编号 | 局限 | 影响 |
|------|------|------|
| **L1** | 离线模拟器的 T1 数值**不是真拟合** —— 取曲线 1/3 处的 x 值当 T1（代码注释自称"简化估算"） | 数值仅供参考，建议后续改用 `curve_fit` 拟合 `a*exp(-t/T1)+c` |
| **L2** | `X_UNIT_SCALE` 换算表是从 17 类实验的实测抽样推断的 | 需与实验室确认；`pipulse_df` / `pulseshape` / `timingxyz` 暂未覆盖 |
| **L3** | Ramsey 的索引类型名是 `ramsey df`（**带空格**）而模拟器查 `ramsey_df` | Ramsey 即使有真实数据也走合成路径；`t1` 不受影响 |
| **L4** | `get_qubit_params` 查不到离线数据里的 qubit（走的是 LabRAD 路径而非离线 provider） | 不影响实验执行，但会让 agent 多绕一步 |

---

## 六、待办（本轮识别、未执行）

### 服务卡死的根因未修

**现象**：agent 服务 `:3005` 出现"进程活着、端口 `LISTENING`、但连不上"，连接僵在 `CLOSE_WAIT`，后续请求全部进不来。已发生**两次**（另一次表现为 agent 对话 300s 超时），本轮只是重启止血。

**根因**：`services/base/base_service.py:217`

```python
self._server = HTTPServer((self.config.host, self.config.port), handler)
```

`HTTPServer` 就是 `socketserver.TCPServer` —— **单线程、不并发**。`run()` 里是：

```python
while self._running:
    self._server.handle_request()   # 一次只处理一个连接
```

因此**任何一个卡住的请求都会堵死整个服务**：唯一的处理线程陷在里面，后续连接只能在 accept 队列排队，队列满（`request_queue_size` 默认 5）之后内核直接拒绝。

另外，`ServiceConfig` 里的 `max_workers = 4` 与 `request_timeout = 60` 全项目**只有声明、没有任何引用点**，即这两个字段从未生效。

**拟修方案**（改动小，但影响全部 10 个服务，需重启才生效）：

1. `HTTPServer` → `ThreadingHTTPServer`（import + 构造各一行）
2. 加**慢请求日志**：记录每个请求的 path 与耗时，超阈值打印 —— 下次再堵能直接定位（按 CLAUDE.md 约定 6，同一问题出现两次以上应加 debug 信息而非直接重试）
3. 把 `request_timeout` 真正接进 LLM 调用链

**注意 —— 直接换多线程会引入新问题，必须先处理线程安全**：

`matplotlib.pyplot` 使用**进程级全局状态**，不是线程安全的。而本仓库大量使用 `plt.close('all')`、`plt.get_fignums()`、`plt.figure(fig_nums[-1])` 这类全局操作：

- `services/quantum_service/server.py`（`_generate_simulation_plot`）
- `services/analysis_service/server.py`（`_handle_plot_offline`、`_handle_plot_offline_v2`、`_handle_plot_variant`、`_handle_plot`、`_handle_plot_historical`、`_handle_plot_experiments` 共 6 处）

并发时会互相 `close('all')` 掉对方正在渲染的 figure，或取到对方的 figure 号 —— 会把"卡死"变成**静默的图表错乱**。因此需要：

- 新增 `services/common/plotting.py` 提供进程内 `PLOT_LOCK` 与 `@serialized_plot` 装饰器，只串行化 pyplot 区段（**不必锁整个请求**，长耗时的 LLM / 实验请求仍可并发）
- `memory_store` 的索引读-改-写加锁：`create_episode` / `create_skill` / `archive_episode` / `delete_skill` 目前是 `load → modify → save`，并发下会丢更新（丢的是索引条目，实体文件会变成新的孤儿）
