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

### B4 ⬜ 执行超时后守护线程仍在真机上继续跑（打开写权限前必须修）

**证据**：`quantum_service._handle_execute` 把 `exec` 放在守护线程里，`join(timeout)` 超时后**上报 timeout 并释放 busy 锁，但线程仍在继续执行**。此时第二条指令可以并发下发。

**影响**：离线无所谓；**真机上等于两条实验指令同时打到硬件**。

**日后可能的改法**：超时后标记服务为「卡死」并拒绝后续执行，而不是静默放行。

**关联**：C3（写闸门）、C 组。

---

## C. 真机接通（P1，硬件恢复后）

> **完整设计见 `docs/REAL_MACHINE_PLAN.md`**，此处只登记条目与依赖顺序。
> 已确认的决策：本机是**客户端**连**远程测控机**；硬件**当前不可用**；**先只读联调，禁止写**；`default_mode` 用 `auto`。

| 编号 | 条目 | 对应 | 前置 |
|---|---|---|---|
| C1 | 连接参数外置到 `system.json`（`labrad.connect(host=,port=)`） | `REAL_MACHINE_PLAN` §四.A | 需测控机 host/IP |
| C2 | 修复离线↔在线切换不生效与静默假成功 | §四.C | — |
| C3 | 只读写闸门（`actuation.allow_execute` / `allow_set_params` 默认 false） | §四.B | C2 |
| C4 | 无硬件验证（单元 / 协议 / 影子执行） | §四.E | C1 |
| C5 | 只读自检脚本 `check_labrad_link.py` + `docs/REAL_MACHINE.md` | §四.F | C1 |
| C6 | **在线时 agent 图源必须切到在线绘图端点** | §四.F 第 5 条 | C1 |
| C7 | `generate_qubit` 首参待验证（实验室脚本传 `globals()`，`labrad_client` 传 `{"s": s}`） | §七.6 | 可实测 |
| C8 | `~/.lqcs` 缺失且 schema 编译在 `.pyd` 内不可静态读取 | §二.4 | 需从测控机拷贝 |
| C9 | 外部条件：host/IP、TLS、远程 DataVault 会话存在性、lqms/lqcs 版本对齐、防火墙放行 | §七.1/3/4/5 | 需实验室 |

**特别提醒 C6**：agent 现在的图源是**离线**接口 `/plot/offline/v2`。接通真机后若不同时切到在线绘图端点，会出现「已经连上真机、agent 却仍在展示离线/合成图」—— 即该文档 §五「数据来源混淆」的具体形态。

---

## D. 工程质量（P2）

### D1 ⏸ 既有 TypeScript 类型错误阻塞生产构建

**证据**（已实测）：`tsc --noEmit` —— **web 6 个**（`page.tsx` ×4、`AgentToolsPanel` ×1、`VariantGenerator` ×1）、**server 24 个**。

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
D1 / D2（有空再说）
```

**优先级判断依据**：A 组决定「数能不能信」，B 组决定「会不会挂/丢数据」，C 组决定「能不能接真机」。
在 A1 确认之前，**不要基于当前 T1 数值做任何判断**。
