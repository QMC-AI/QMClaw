# 待办总表（BACKLOG）

> **本文档是唯一的待办入口。** 所有「日后待做 / 已知局限 / 已接受的现状」都登记在这里，
> 不再散落在多份文档里。详细设计放在关联文档中，这里只登记条目、证据、影响与前置条件。
>
> 关联文档：
> - `docs/REAL_MACHINE_PLAN.md` —— 接通远程真机的完整设计（C 组的详细方案）
> - `docs/CHANGES_VS_SOURCE.md` —— 已完成的能力与改动清单
> - `docs/GIT_HISTORY.md` —— 提交历史与回滚手册（想退回某个改动时看这份）
> - `docs/RUNTIME_DEPS.md` / `docs/DEPLOY_DIFF.md` —— 依赖与部署差异
> - `CLAUDE.md` —— 开发约定

## 记号

| 记号 | 含义 |
|---|---|
| P0 | 影响**结论可信度** —— 数值/图可能是错的，不修就不能拿去做判断 |
| P1 | 影响**稳定性或可用性** —— 会卡死、会丢数据、或阻塞真机接通 |
| P2 | 工程质量与体验 —— 不影响正确性 |
| ⬜ / 🚧 / ⏸ | 待办 / 进行中 / 暂缓（已接受现状） |
| **前置** | 需要实验室/导师确认，或需要外部条件（硬件、网络）才能动手 |

---

## A. 数据正确性（P0）

> 这一组决定「agent 报出来的数值能不能信」。在修完之前，**离线跑出的实验结论只能用于验证链路，不能当作测量结果**。

### A1 ⬜ 信号列语义错误：一律取 `f1`，但 T1 的信号在 `f2`

**现象**：T1 的图是一条没有衰减的乱跳线；T1 数值不是从曲线算出来的。

**证据**（对 q10lu8 的 `20251103.dir/00398 - q10lu8%c T1.hdf5` 实测）：

| 列 | 走向 | 指数拟合 |
|---|---|---|
| `f1` | 0.267 ↔ 0.290 上下乱跳 | T1=8.47 µs，**R² = 0.0085**（几乎零解释力）|
| `f2` | **0.620 → 0.283 单调衰减** | **T1 = 4.94 µs，R² = 0.9537** ✅ |

抽样 12 个 T1 文件复核，是稳定规律：`f1` 的 R² 全部很差（0.004–0.507），`f2` 的 R² 全部不错（0.498–0.986）。

**范围不止 T1** —— 大多数实验类型的列数远多于 2：

```
IQraw 4 列   PiPulse 7 列   S21 7 列   Spectroscopy 7 列
T1 2 列      Ramsey df 7 列  S21_dis 9 列  IQcenter spectroscopy 13 列
```

而代码**一律只取 `f1`**。凡是「有效信号不在 f1」的实验，图和数值都不对。

**这是原有设计，不是本次引入的**（已用 git 核实）：

- `f2d83b5`（动手前的基线）的 `load_hdf5_data` 已有 `data['y'] = arr[:, 1]`
- `offline_data_lab.py`（experiments 页绘图源，只有一次上游提交，未被修改）两处都是 `y = data[:, 1]`
- `07ceb0d` 只**新增了复合 dtype 分支**，映射 `names[0]→x`、`names[1]→y`，**沿用原约定**（改之前真实数据路径从未生效，走不到选列这一步）

即：原设计假设 2D 数组且信号在第 1 列；实际交付的是复合 dtype，且 T1 的有效信号在第 2 列 —— **假设与数据格式不符**。

**实验室旁证**：实验室自己的脚本按实验类型选列 —— PiPulse `yIdx=5`、S21power2d/Spectroscopy `yIdx=1`、T1 走 `dp.T1(data)`（lqms 内部决定）。说明列索引是**随实验类型变化**的。

**当前决定**：**先沿用导师的取法，不改代码。**

**日后可能的改法**：

1. 向实验室/导师确认每类实验的信号列语义（问法见文末「待确认问题」）
2. 在 `experiment_configs.json` 增加 `signalColumn` 字段，把列语义做成配置而非硬编码
3. `offline_data_provider.load_hdf5_data` 按实验类型返回正确的 y；`offline_data_lab.py` 同步
4. 已知 T1 → `f2`（本次实测确认），可直接用于验证改动是否正确

**前置**：需要导师确认（第 1 步）。**在确认前不要猜列** —— 猜错比现状更糟。

**关联**：A2（同一条链路上）、A5。

---

### A2 ⬜ T1 数值不是拟合结果，是扫描轴上的位置值

**现象**：T1 报 8 µs，但真实拟合是 4.94 µs。两者接近纯属巧合。

**证据**：`services/quantum_service/offline_simulator.py:348`

```python
t1_idx = len(y) // 3          # 取数组 1/3 位置
t1_estimate = x[t1_idx]       # 直接拿该位置的 x 值当 T1
```

与曲线形状、衰减、噪声全都无关，所以对随机噪声免疫、每次跑都一样。

**影响**：`results` 里的 `T1 (µs)` 不可用。同理 `piamp` 用的是 `len(y)//4`（`:535`）。

**日后可能的改法**：用 `curve_fit` 拟合 `a*exp(-t/T1)+c`，并把拟合曲线画到图上，与 experiments 页的 `qter.fitData` 真正对齐。

**前置**：无（但应等 A1 定了信号列再改，否则对错的列做拟合没有意义）。

---

### A3 ⬜ `X_UNIT_SCALE` 单位换算表需实验室确认，三类实验未覆盖

**证据**：`offline_simulator.py` 的 `X_UNIT_SCALE` 是从 17 类实验的**实测抽样**推断的：

- `t1` / `ramsey` / `ramsey_df` / `spinechocpmg` → `1e-9`（历史数据是 ns）
- `s21*` / `spectroscopy*` → `1e9`（历史数据是 GHz）
- `iqraw` / `piamp*` / `xeb` 等序号或无量纲量 → 不换算

**未覆盖**：`pipulse_df`、`pulseshape`、`timingxyz`（离线库里存在这些类型）。

**影响**：换算错会让数值差 1e9 量级，且 stdout 与 metrics 会自相矛盾。

**前置**：需要实验室确认各实验扫描轴的原生单位。

---

### A4 ⬜ Ramsey 永远走合成路径：索引里的类型名是 `ramsey df`（带空格）

**证据**（已核实 `data/offline_data/datasets.json`，5008 条）：

```
ramsey df   82 条      ← 索引里的实际类型名（带空格）
ramsey       1 条
```

而模拟器查的是 `ramsey_df`（下划线，见 `offline_simulator.py:45,62,213`）→ 匹配不上 → 即使有 82 个真实 Ramsey 数据集也一律走合成。

**影响**：Ramsey 实验拿不到真实数据。`t1` 不受影响。

**日后可能的改法**：在 `find_matching_datasets` 里做类型名归一化（空格/下划线/大小写等价），或改成按 `experiment_configs.json` 的 key 映射。

**前置**：无（改名安全，但要确认实验室的权威拼写）。

---

### A5 ⬜ 图源与数值源不是同一套处理

**现象**（本次改动后的状态）：

- **图** 走 `analysis_service /plot/offline/v2` → **原始历史数据，零变换**（只做单位换算）
- **数值** 走模拟器的 metrics，其内部对真实数据做了随机扰动：

```python
noise_scale = random.uniform(0.9, 1.1)              # 幅度 ±10%
noise = np.random.normal(0, np.std(y) * 0.02, len(y)) # 2% 高斯噪声
simulated_y = y * noise_scale + noise
```

该扰动现在只影响传给 agent 的 `arrays` 曲线数组，不再影响显示的那张图。

**影响**：图和数值来自同一次实验但经过不同处理，目前没有可见矛盾，但语义上不一致。

**日后可能的改法**：明确"离线回放是否要加噪"这一个设计问题 —— 要么去掉加噪（回放就是回放），要么让图和数值都走同一条带噪路径。

**前置**：这是个设计取舍，建议与导师确认意图。

---

## B. 服务稳定性（P1）

### B1 ⬜ `base_service` 单线程 `HTTPServer` 卡死（根因未修，只做过重启止血）

**现象**：服务进程活着、端口 `LISTENING`、但连不上；连接僵在 `CLOSE_WAIT`，新请求进不来。**已发生两次**（另一次表现为 agent 对话 300s 超时）。

**证据**：`services/base/base_service.py:217`

```python
self._server = HTTPServer((self.config.host, self.config.port), handler)
```

`HTTPServer` 即 `socketserver.TCPServer`，**单线程不并发**。`run()` 里 `while self._running: self._server.handle_request()` 一次只处理一个连接，**任何一个卡住的请求都会堵死整个服务**。

另外 `ServiceConfig` 的 `max_workers = 4` 与 `request_timeout = 60` **全项目只有声明、没有任何引用点**。

**日后可能的改法**：

1. `HTTPServer` → `ThreadingHTTPServer`（import + 构造各一行）
2. 加**慢请求日志**（记录 path 与耗时，超阈值打印）—— 下次再堵能直接定位
3. 把 `request_timeout` 真正接进 LLM 调用链
4. **必须先完成 B2、B3**，否则换多线程会把「卡死」变成「静默的数据错乱」

**影响面**：全部 10 个 Python 服务共享基类，改完必须重启全部服务。

---

### B2 ⬜ matplotlib `pyplot` 线程不安全的串行化（B1 的前置）

**证据**：本仓库大量使用 `plt.close('all')`、`plt.get_fignums()`、`plt.figure(fig_nums[-1])` 这类**进程级全局**操作：

- `services/quantum_service/server.py`（`_generate_simulation_plot`）
- `services/analysis_service/server.py`（`_handle_plot_offline` / `_plot_offline_v2` / `_plot_variant` / `_plot` / `_plot_historical` / `_plot_experiments` 共 6 处）

**不修就换多线程的后果**：并发请求会互相 `close('all')` 掉对方正在渲染的 figure，或取到对方的 figure 号 —— 把「卡死」变成**静默的图表错乱**。

**日后可能的改法**：新增 `services/common/plotting.py` 提供进程内 `PLOT_LOCK` 与 `@serialized_plot` 装饰器，**只串行化 pyplot 区段**（不必锁整个请求，长耗时的 LLM/实验请求仍可并发）。

---

### B3 ⬜ `memory_store` 索引读-改-写竞态（B1 的前置）

**证据**：`create_episode` / `create_skill` / `archive_episode` / `delete_skill` 都是 `load → modify → save`，无锁。并发下会丢更新。

**影响**：丢的是**索引条目**，实体文件还在 → 产生新一批「有文件无索引」的孤儿（`orphanCount` 只统计反向的孤儿，这类丢索引的反而看不见）。

**日后可能的改法**：加模块级 `RLock` + `_update_index(mutator)` 辅助函数，把 4 处调用点收敛进去。

---

### B4 ✅ 执行超时后守护线程仍在真机上继续跑（打开写权限前必须修）

**证据**：`quantum_service._handle_execute` 把 `exec` 放在守护线程里，`join(timeout)` 超时后**上报 timeout 并释放 busy 锁，但线程仍在继续执行**。此时第二条指令可以并发下发。

**影响**：离线无所谓；**真机上等于两条实验指令同时打到硬件**。

**已修**（随 C 组落地）：超时后标记 `_exec_stuck` 并拒绝后续 `/execute`、`/qubit/set_params`；`/connect` 带 `reset_stuck=true` 可由人工确认后清除。

**关联**：C3（写闸门）、C 组。

---

## C. 真机接通（P1，硬件恢复后）

> **完整设计见 `docs/REAL_MACHINE_PLAN.md`**，运维手册见 **`docs/REAL_MACHINE.md`**。
> 已确认的决策：本机是**客户端**连**远程测控机**；硬件**当前不可用**；**先只读联调，禁止写**；`default_mode` 用 `auto`。

| 编号 | 条目 | 对应 | 前置 | 状态 |
|---|---|---|---|---|
| C1 | 连接参数外置到 `system.json`（`labrad.connect(host=,port=)`） | `REAL_MACHINE_PLAN` §四.A | 需测控机 host/IP | ✅ 代码完成（地址待填） |
| C2 | 修复离线↔在线切换不生效与静默假成功 | §四.C | — | ✅ |
| C3 | 只读写闸门（`actuation.allow_execute` / `allow_set_params` 默认 false） | §四.B | C2 | ✅ |
| C4 | 无硬件验证（单元 / 协议 / 影子执行） | §四.E | C1 | ✅ 单元 14 例；协议/影子待后续 |
| C5 | 只读自检脚本 `check_labrad_link.py` + `docs/REAL_MACHINE.md` | §四.F | C1 | ✅ |
| C6 | **在线时 agent 图源必须切到在线绘图端点** | §四.F 第 5 条 | C1 | ✅ |
| C7 | `generate_qubit` 首参待验证（实验室脚本传 `globals()`，`labrad_client` 传 `{"s": s}`） | §七.6 | 可实测 | ⬜ |
| C8 | `~/.lqcs` 缺失且 schema 编译在 `.pyd` 内不可静态读取 | §二.4 | 需从测控机拷贝 | ⬜ |
| C9 | 外部条件：host/IP、TLS、远程 DataVault 会话存在性、lqms/lqcs 版本对齐、防火墙放行 | §七.1/3/4/5 | 需实验室 | ⬜ |

**特别提醒 C6**：agent 现在的图源是**离线**接口 `/plot/offline/v2`。接通真机后若不同时切到在线绘图端点，会出现「已经连上真机、agent 却仍在展示离线/合成图」—— 即该文档 §五「数据来源混淆」的具体形态。

---

## D. 工程质量（P2）

### D1 ⏸ 既有 TypeScript 类型错误阻塞生产构建（2026-10-08 复核：web 侧已消失）

**证据**（已实测）：`tsc --noEmit` —— **web 6 个**（`page.tsx` ×4、`AgentToolsPanel` ×1、`VariantGenerator` ×1）、**server 24 个**。

**2026-10-08 复核**：web `tsc --noEmit --incremental false` **EXIT=0**（零错误，原 6 个存量疑随上游合并消失，`npm run build` 仍被沙箱限制未完整验证）；server 侧 `index.ts` 存量 29 行错误仍在（T3 实测改动前后 32 持平）。

**影响**：dev 模式可用，生产构建被阻塞。

**状态**：暂缓（既有问题，与本次工作无关）。修之前不要指望 `npm run build` 能过。

---

### D2 ⬜ `measure_scripts` 不完整

**证据**：仓库里 `measure_scripts/measure_scripts/` 只有一个 `sq_workflow/backend.py`，其余实验室本地代码未交付。

**影响**：对 `quantum_service` **无影响**（它走 `lqms.measure.tuners.sq_nodes`）；但走 `job_runner.py` / `hermes_runner.py` 的 `import sq` 路径则可能缺件。

---

## E. 已接受现状（登记在案，避免反复讨论）

| 编号 | 项 | 决定与理由 |
|---|---|---|
| E1 | `lqms` 保持 **0.3.1**，不升级到 deploy 里的 0.3.3 | 项目里 30+ 处 `from lqms...` 已在本机用 0.3.1 逐条验证可导入；`deploy/lqms-0.3.3-...whl` 保留在原地备用 |
| E2 | `pip check` 2 处冲突 | `langchain-openai` 需 `openai>=2.26.0`、`qca` 需 `rich==14.3.4`，与 `hermes-agent` 的精确 pin（`openai==2.24.0`、`rich==14.3.3`）三方互斥，无法同时满足。维持环境快照组合 |
| E3 | `get_qubit_params` 查不到离线 qubit | 它走 LabRAD 路径而非离线 provider（原 L4）。不影响实验执行，只让 agent 多绕一步。**接通在线后自然消解**（C1） |

---

## F. 新功能：从历史记录点进去继续对话

> **需求**（用户提出）：agent 面板 → Memory → 任务记录里能看到过去的对话，但**只能看结果，不能接着聊**。希望能点进去继续这段对话。
>
> 这一组是**新功能**，不是修 bug；但下面 F0 里列的是「为什么现在做不到」的四个根因，其中两个本身就是缺陷。

### F0 ⬜ 现状与根因（已核实）

| # | 根因 | 证据 |
|---|---|---|
| 1 | **agent 面板完全没有会话概念** | `AgentChatPanel.tsx` 只有 `const [messages, setMessages] = useState<ChatMessage[]>([])`；全文没有 `sessionId`，不读不写任何持久化 —— **刷新页面即丢** |
| 2 | **前端从不发送 `session_id`** | 后端 `_handle_chat` 支持 `session_id`（`server.py:588`），`_record_episode` 用 `task.context.get("session_id") or task.task_id`。前端不传 → 每个 episode 的 `sessionId` 就是它自己的 `task_id`（实测：`task_1790158958819` / `task_1790158979904`…**各不相同**）→ **无法把多轮归成一段对话** |
| 3 | **`roundIndex` 一直是 `null`** | 实测磁盘上 11 个 episode 全是 `"roundIndex": null` |
| 4 | **MemoryPanel 只有「反思 / 归档」，没有「继续」入口** | `MemoryPanel.tsx` 列表项只有 🤔 / 📦 两个按钮，`DetailView` 只读 |

**顺带发现的另一个死壳**（不在用户描述里，但同一类问题）：

```tsx
// page.tsx:970-973  —— 聊天页的会话侧栏
<ChatHistorySidebar
  currentSessionId={""}                                              // 硬编码空
  onSelectSession={(id) => { console.log("Select session:", id); }}  // 只打日志
  onNewSession={() => { console.log("New session"); }}               // 只打日志
/>
```

后端 `/api/chat/sessions`（增删查）与 `chatService.ts`（消息数组落盘到 `data/chat_history/`）**都已就绪**，但这个侧栏点击什么都不做。实测 `data/chat_history/` 里 **0 个会话**。

### F1 ✅ 后端：会话（session）列表与回放接口（2026-10-08 分级记忆 P1 落地）

`memory_store` + `agent_service`：

1. `memory_store.list_sessions()` —— 扫 `index.json` 的 episodes，按 `sessionId` 分组，返回：`{sessionId, rounds, firstAt, lastAt, taskPreview, qubit, model, status}`
2. `memory_store.get_session_episodes(session_id)` —— 取该会话全部 episode，按 `roundIndex`（为 null 时退回 `timestamp`）排序
3. agent_service 新增两个端点（沿用现有 `/memory/*` 前缀路由即可，无需新机制）：
   - `POST /memory/sessions` —— 会话列表
   - `GET /memory/sessions/{sessionId}` —— 回放：把 episodes 映射成消息序列

回放的消息形状（与前端 `ChatMessage` 对齐）：

```json
{ "messages": [
    {"role": "user",      "content": "<episode.task>",     "timestamp": "..."},
    {"role": "assistant", "content": "<episode.response>", "steps": [...], "results": {...}}
] }
```

**前置**：F0-2 必须先解决（前端开始传 `session_id`），否则新产生的 episode 仍然无法分组。

### F2 ✅ 前端：agent 面板支持持久会话（2026-10-08 分级记忆 P1 落地）

1. `AgentChatPanel` 增加 `sessionId` 状态（新建时生成 / 选中时载入），每次 `POST /chat` 带上 `session_id`
   —— **后端已支持，不用改后端**（`_handle_chat` 会把它透传进 `task.context`，`_record_episode` 用它填 `sessionId`）
2. 续聊时 `roundIndex` 递增（后端字段已有，只需前端带上当前轮次）
3. 会话列表 UI：可复用 `ChatHistorySidebar` 的交互模式，但**数据源用 memory 的 `/memory/sessions`**（那才是 agent 的对话），不要与聊天页的 `chat_history` 混用
4. 刷新页面后能恢复：从 `/memory/sessions/{id}` 拉回 messages

### F3 ⬜ 前端：从历史记录点进去继续（用户要的那个功能）

1. `MemoryPanel` 的 `DetailView` 增加「↩ 在此继续对话」按钮
2. 点击后：
   - 取该 episode 的 `sessionId`
   - 拉 `/memory/sessions/{sessionId}` 得到整段对话
   - 把 messages 填回 `AgentChatPanel`（需要 `MemoryPanel` → `AgentChatPanel` 的回调，或提到共同父组件）
   - 切到 Chat 视图并聚焦输入框
3. **退化分支**（重要）：老数据每个 episode 的 `sessionId` 各不相同（F0-2），所以它们都是**单轮会话**。这种情形按钮语义改为「以该任务为起点新建会话」，把该轮的 task/response 作为上下文起点，而不是假装能接上历史

### F4 ⬜ 修复或移除 `page.tsx` 的死壳侧栏

`onSelectSession` / `onNewSession` / `currentSessionId` 三个 prop 目前是空实现。二选一：

- **接上**：`onSelectSession(id)` → `api.getChatSession(id)` → 载入 messages；`onNewSession` → `api.createChatSession(...)`
- **或移除**：如果这个侧栏对聊天页本来就没用，就删掉，别留一个点了没反应的 UI

**决定点**：这是一个「要不要保留聊天页的会话功能」的产品问题，建议先确认再做。

### F5 ⬜ 数据与兼容性（回放会丢的东西）

| 项 | 现状 | 影响 |
|---|---|---|
| **图表** | episode **不存 `charts`**（已核实 schema 无此字段），只有 `response` 文本里提到 `/plots/xxx.png` 路径 | 回放时图会丢。要么补存 charts，要么按 `dataset_id` 重新出图（见 `CHANGES_VS_SOURCE` §三.7） |
| `roundIndex` | 一直是 `null` | 排序回退到 `timestamp`；新会话开始后应正确填充 |
| 空 episode | 磁盘上有一条 `ep_20260923_220058_39f37c7f`，`task` 为空 | 列表里应过滤掉 |
| 老数据的会话分组 | 每个 episode 一个 sessionId | 只能显示为单轮，见 F3 的退化分支 |

### F 验收

1. 新开一段对话跑 3 轮 → 会话列表里显示为**1 个会话、3 轮**（而不是 3 个单轮）
2. 刷新页面 → 能从历史点进去，看到完整 3 轮，并能接着发第 4 轮，`roundIndex = 4`
3. 打开一个**老的单轮 episode** → 能「以该任务为起点新建会话」并继续
4. 回放的 assistant 消息能显示 `steps` 与 `results`（与实时对话一致）
5. `page.tsx` 的侧栏：要么点击真能载入会话，要么已移除（不能留空壳）

### F 建议实现顺序

```
F1（后端会话接口）
  → F2（前端持久会话 + 传 session_id）   ← 这一步做完，老数据问题就不再累积
    → F3（详情页「继续对话」入口）
F4 独立，可与 F2/F3 并行
F5 在 F1 之后随手处理（尤其是 charts 要不要补存）
```

**最小可用切片**：F1 + F2 只做「会话分组 + 刷新后能恢复」，就已经能回答「这段对话我聊到哪儿了」；F3 的「点进去继续」是完整体验。

---

## G. 融合导师上游之后的收尾（登记日期：2026-10-08）

> 背景：把上游 `Secsix/QMClaw` 的 2 个提交（`ea773be` / `03d838d`，Hermes 扩展 + SSE 基础设施）合进 `feat/c-real-machine`。
> 完整方案见 `docs/UPSTREAM_MERGE_PLAN.md`，独立复核见 `docs/UPSTREAM_MERGE_PLAN_REVIEW.md`。
> **本组条目都不是合并的阻塞项**，是合并完成后要接着处理的。

### G1 ⬜ 记忆存储收敛：现在是**三处**路径，不是两套

融合后同时存在：

| 位置 | 谁读写 |
|---|---|
| `<qmclaw-server>/config/memory/`（episodes / skills / index.json） | 你的 `common/memory_store.py` + `/api/agent/memory/*` |
| `<qmclaw-server>/data/memories/{MEMORY,USER}.md` | 导师 python 侧 `hermes_service/memory.py` |
| `<repo>/memory/MEMORY.md` | 导师**网关**侧（`src/index.ts` 的 `getHermesMemoryDir()`，4 个 handler 直接读写文件） |

**注意导师自己两侧也不互通**（python 写 `data/memories`，UI 读 `<repo>/memory`），所以收敛比"统一两套"更麻烦。
**收敛方向待定**：先决定"记忆的权威位置是哪个"，再谈迁移。
**顺带**：`memory/MEMORY.md` 入库后会被网关运行时改写 → 每次同步上游必冲突，处置见 G5。

### G2 ⬜ 融合后 `/api/hermes/skills` 会返回空 —— 修法与判据已实测

**现象**：导师新增的 Hermes 技能管理界面看不到任何技能。

**根因（已逐层查证）**：`services/hermes_service/skills.py:63` 的 `_get_data_dir()` 读 `<qmclaw-server>/data/skills`，然后 `skills.py:137` 用 `skills_dir.rglob("SKILL.md")` 收集 —— 而该目录**在当前仓库里不存在**，上游自己也没带技能文件。项目真正的技能在 `<repo>/skills/<name>/SKILL.md`（**7 个**）。

> ⚠️ **`skills.py` 不读 `HERMES_HOME`**（全上游对它的引用只有 hermes_runner / server.py 的取值逻辑 + 一行日志；`server.py:186` 那行也说明它服务的是 Hermes agent 自己的 `get_skills_dir()`）。
> 所以"把 `.env` 的 `HERMES_HOME` 改成项目根"**修不了技能列表** —— 这是最容易踩的误区。

**修法（三选一，推荐 A）**：

| | 做法 | 代价 |
|---|---|---|
| **A（推荐）** | `mklink /J "<qmclaw-server>\data\skills" "<repo>\skills"` | **已实测可行**：junction 建得成，Python `rglob('SKILL.md')` 能穿过它扫到文件。`data/` 已在 `.gitignore`，联接不会进版本库，**换机需重建** |
| B | 改 `skills.py` 的 `_get_data_dir`：优先 `HERMES_HOME/skills`，否则回落 `<repo>/skills` | 改上游文件，下次同步必冲突；但语义最正确 |
| C | 手工拷贝 `<repo>/skills/*` 到 `data/skills/` | 变成两份技能，改一份另一份不同步 —— 不推荐 |

**验收判据**：起服务后 `GET /api/hermes/skills` **非空**且能看到 7 个技能名。

**另**：你自己的 agent 侧技能功能**不受影响**（走 `config/skills.json` + `memory_store` 的 `/api/agent/memory/skills`），本项只影响导师新加的界面。
`HERMES_HOME` 语义（本机 `.env` 指向 `data\hermes`，导师假设项目根）本身仍待你决策 —— 详见 `UPSTREAM_MERGE_PLAN.md` 第十·补.1。

### G3 ✅ `scripts/memory_store.py` 与 `services/common/memory_store.py` 双份（2026-10-08 P4 已收敛）

后者是从前者移植的（磁盘格式不变），移植后脚本版仍在库里。当前无引用冲突，但改存储格式时容易只改一份。
**已处理**：脚本版收敛为**转发层**（`sys.path` 挂载 + `from services.common.memory_store import *` + 显式重导出公开/私有名字），单实现，schema 不再可能分叉；测试用 `is` 身份断言验证两条导入路径指向同一实现。

### G4 ⬜ 新模块的第三方依赖未声明（**上游原来的漏项**）

导师新增模块用到 `apscheduler`（cron）、`tzlocal`（cron）、`portalocker`（memory 文件锁）、`websockets`（approval_ws），但：

- `tzlocal` / `portalocker` / `websockets` 在**你与上游的 requirements 里都没有**；
- `apscheduler` 只在 `lqcs_deploy/requirements.txt` 第 9 行有。

而 `hermes_service/server.py` 是**顶层** import 这些模块 → 缺包会让**整个 hermes 服务起不来**。
本机解释器 4 个包恰好都已装，所以现在不会炸；**换机 / 重建环境必炸**。
→ 补进 `Agentic Workflow/qmclaw-server/python/requirements.txt`。

### G5 ⬜ 决定两个运行时/无引用文件是否入库（证据已补齐）

**`auth.lock` —— 建议不入库（运行时残留，证据齐全）**

- 上游提交的内容 = **1 字节，一个空格**（`b' '`）；
- 你本机 `qmclaw-server\data\hermes\auth.lock` = **同样 1 字节空格**；
- **全上游零代码引用**（`git grep 'auth\.lock' main` 为空）。

→ 基本可确定是导师从 hermes 运行目录误提交的。处置：`git rm --cached auth.lock` + 加 `.gitignore`。

**`memory/MEMORY.md` —— 内容有用，但会被运行时改写**

合并后它就是**网关读写的那个文件**（`src/index.ts` 的 `getHermesMemoryDir()` → `<repo>/memory`，4 个 handler 直接拼 `MEMORY.md`）→ 工作树被常驻改写，违反约定 11，且**下次同步上游必冲突**。

内容本身与你的图表方案一致（4 行，要求图片用 `/plots/xxx.png` URL 格式、**不要 base64 内联或直接文件路径**），所以值得保留 → **二选一**：
- 保留入库，接受"每次同步都冲突"；
- 移出索引 + 加 `.gitignore`，改在部署时手工放置。

**同族第三处（别漏）**：`%LOCALAPPDATA%\hermes`（存在则优先）或 `~/.hermes`，以及 `hermes_runner.py` 的 `~/.qmclaw_hermes` → **cron / config 状态落在仓库外**，备份与交付容易漏。

### G6 ⬜ 前端新增 4 个依赖需要安装

导师 `package.json` 新增 `highlight.js` / `react-markdown` / `rehype-highlight` / `remark-gfm`；本机 `require.resolve` **四个全部失败**。
合并后跑前端前先 `npm install`。注意 `package-lock.json` 也被上游那版覆盖。

### G7 ⬜ `offline_data_lab.py` 被上游整份重写，出图口径需回归

`+359/−39`：`fitData()` 签名从 `(exp_num, collect, do_plot)` 变成
`(datasets, fit_func, qname, update, collect, config_name, des, **kwargs)`，新增 `_fit_iqraw` / `_auto_detect_fit_func` / `_default_plot` / `_plot_fit_result` 整条流水线。

**API 兼容已核实**（位置参数与 `do_plot` 仍生效，仓库里没有 `exp_num=` 关键字调用点），**但你的 Tier1/Tier2 出图走的就是这个类** → 出图口径/样式会变，而现有检查（`charts=1`、子串存在）**全都抓不到**。
→ 需要与合并前的图**肉眼比对**做一次回归。
另有【推断】风险：旧用法 `fitData(-1, collect=True, do_plot=False)[1][-2][0]`（`python/workflow/model.py:189`）依赖旧的返回结构，别把离线 updater 接到那条调用上。

### G8 ⬜ `qca_service/core/` 与 `qca_service/scripts/` 是死代码（清理候选）

`qca_service` 自带 `core/{discovery,models,runner,storage,__init__}.py`，与 vendor `core/` 同名；但 `server.py` 先把 vendor 插进 `sys.path` 再 import，同名模块解析到 vendor 那份。服务侧版本明显更旧（如 `runner.py` 4264 vs vendor 17796 字节），且 `server.py:86` import 了服务侧**不存在**的 `core.workflow_validation` → 反证运行时用的是 vendor 的 core。

**删之前必须确认无其它调用方**（复核未启动服务验证实际导入结果）。详见 `.analysis/deploy-vendor-report.md` §3.2。

### G9 ⬜ `tsconfig.tsbuildinfo` 不该进版本库

`qmclaw-web/tsconfig.json` 开着 `incremental: true`，而这个**构建产物被跟踪**且被上游改过 → 合并后工作树里躺着的是**导师机器上的增量构建状态**，可能"跳检"或让工作树反复变脏（与约定 11 打架）。
→ 跑前端构建前先删掉，再统一"移出索引 + 加 `.gitignore`"（独立提交，别混进合并提交）。

### G10 ⬜ SSE 基础设施已经就位（原 README TODO 可推进）

导师这两个提交把 `handle_streaming_request()`（`services/base/base_service.py`）+ `approval_ws.py` 带进来了，README 里那条「SSE 流式处理支持 —— 当前使用轮询模式」现在有基础设施了。
**注意**：`base_service.py` 的这处合并在顺序上敏感 —— 导师的新方法必须插在 `@_timed_request("DELETE")` **之上**，否则装饰器会挂到 SSE 方法上（详见 `UPSTREAM_MERGE_PLAN.md` 5.2）。

---

## 待确认问题（给实验室/导师）

按优先级：

1. **（A1，最关键）** 离线 HDF5 是复合 dtype，`f0` 是扫描轴。加载器和 `offline_data_lab` 都默认**第 1 列（`f1`）**是信号列。但实测 T1 文件里 `f1` 没有衰减（R²=0.008），`f2` 才是衰减（T1≈4.94 µs，R²=0.95）。**每类实验的信号列是固定的第 1 列，还是随实验类型不同？** 实验室脚本里 PiPulse 用 `yIdx=5`、S21power2d/Spectroscopy 用 `yIdx=1`，看起来是随类型变的。
2. **（A3）** 各实验扫描轴的原生单位是什么（ns？GHz？序号？）—— 现有换算表只覆盖 17 类，`pipulse_df` / `pulseshape` / `timingxyz` 未覆盖。
3. **（A5）** 离线回放的图与数值，**是否应该加随机噪声**？（模拟器现在对真实数据加了 ±10% 幅度缩放 + 2% 噪声）
4. **（A4）** Ramsey 的权威类型拼写是 `ramsey df`（带空格）还是 `ramsey_df`？
5. **（C9）** 测控机的 host/IP、是否启用 TLS、远程 DataVault 上是否存在会话 `['', 'LQHL', 'test', '20260324']`、测控机上的 `lqms`/`lqcs` 版本。

---

## 建议执行顺序

```
A1（等导师确认列语义）→ A2（真拟合）→ A3 / A4（单位与命名）
B2 → B3 → B1（先做线程安全前置，再换多线程）
B4 + C1 → C2 → C3（真机接通；B4 必须在开写权限前完成）
C4 / C5 / C6（验证与文档）
F1 → F2 → F3（历史对话可续聊；F2 越早做越好，否则老数据一直无法分组）
D1 / D2（有空再说）
```

**注**：F 组与 A/B/C 相互独立，可以随时插队。若要尽快见效，F1+F2 是最小可用切片。

**优先级判断依据**：A 组决定「数能不能信」，B 组决定「会不会挂/丢数据」，C 组决定「能不能接真机」。
在 A1 确认之前，**不要基于当前 T1 数值做任何判断**。

---

## H. 分级记忆落地后的 nits 与收尾（登记日期：2026-10-08）

> 分级记忆 T1–T7 已落地（详见 `.claude/progress.md` 同日条目）。以下为审查中记录、暂不阻塞的小项。

| 编号 | 项 | 说明 | 状态 |
|---|---|---|---|
| H1 | `summary_threshold` 语义空挂 | `config/system.json` 的 memory 块有此键且被校验读取，但折叠触发只用 `window_size` 规则——需定义它到底约束什么（或删除该键） | ⬜ 待定义 |
| H2 | `update_episode_fields` 不回写 index 摘要 | 只合并实体文件；若日后用它改 status/qubit，index 条目会滞后（`list_sessions`/`get_session_episodes` 读实体不受影响） | ⬜ 已知 |
| H3 | 单轮退化的种子消息仅前端内存 | 「以该任务为起点新建会话」的首轮内容不入后端 session，刷新后消失；此后 `round_index` 从 2 起而 rounds 计 1（语义固有，可接受） | ⏸ 接受现状 |
| H4 | 42 个 `pytest-cache-files-*` 空目录删不掉 | 目录授权表只剩 SYSTEM/Administrators/特殊身份，当前用户 `MY_RIGHTS=[]`；ACL 诊断技能判定 NOT_THIS_CLASS（管辖外），git 不可见（空目录）。需要时请人工删除或切完全权限后清理 | ⬜ 待人工 |
| H5 | skill 文档与实际不符 | `brain-worker-team` skill 写的「subagent 有 model 字段」在本构建不成立（实测 spawn_teammate/subagent 均继承主模型）；唯一便宜路由是 `workflow.agent()` 的 model 覆盖。skill 文档待回写 | ⬜ 待改 |
| H6 | L4 用户画像与 G1 收敛 | 分级记忆 L4 本期未接（导师 hermes 侧已有 MEMORY.md/USER.md 实现）；G1 三处收敛时一并决策 | ⬜ 见 G1 |
