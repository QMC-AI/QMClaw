# 把导师上游融合进本仓库 —— 合并方案

> 日期：2026-10-08
> 分支：`feat/c-real-machine`（HEAD `af90c1c`）
> 方向：**以我为主** —— 把导师上游的更新合进来，保留本地 36 个改造提交
> 状态：**方案已实测完成冲突预判与解法验证，等用户确认后执行**

---

## 一、结论先行

**这次融合在 git 层面成本很低（4 个文件 / 6 处冲突），但冲突是"语义敏感"的，不是"随便解解就行" —— 详见第十三节的修正版结论。**

| 指标 | 数值 |
|------|------|
| 你相对 fork 点的提交 | **36 个**（62 个文件） |
| 导师相对 fork 点的提交 | **2 个**（41 个文件 = 新增 18 / 修改 19 / 重命名 4） |
| 双方都改过的文件 | **11 个** |
| 其中能自动合并 | **7 个** |
| 真冲突 | **4 个文件 / 6 处冲突** |
| 6 处冲突的解法 | 已逐条定规则并**实测校验通过**（见第五节） |

关键判断：导师的 41 个文件里，**11 个与你的改动重叠**（7 个自动合并 + 4 个冲突），其余新增文件都不与你的改动同名。他做的是 Hermes 扩展（memory / skills / cron / 审批 / 前端渲染组件），你做的是离线链路、memory 存储层、服务稳定性、真机只读联调。**两边整体是并行的两条线。**

> ⚠️ 但"并行"不等于"无交集"。复核另外查出 **8 个既不重叠、也没被本方案初稿列出的上游改动**，它们**干净合并却语义相关**（详见第三节 3.3），其中 `offline_data_lab.py` 是**你离线链路核心依赖被整份重写**。
>
> 还有一条是真正需要你拍板的：导师新增的 `hermes_service/memory.py` 与你的 `common/memory_store.py` 互不引用，合完会变成"一个项目多套长期记忆"。**而且不止两处 —— 实测是「三处」路径**（导师自己的 python 侧与网关侧指向的还不是同一个文件）。这不是 git 冲突（文件不同名，git 合得干干净净），但需要产品决策。**详见第十节。**

---

## 二、仓库拓扑（先说清楚"融合"是在哪两个东西之间发生）

```
github.com/Secsix/QMClaw          ← 导师上游，main = 03d838d（2026-09-30）
        │
        │  fork（tree 等同上游 c3fd6e6，2026-09-21）
        ▼
github.com/hzz-wdb/QMClaw         ← 你的 fork，origin
        │
        ├── main ................. 仅文档提交（25 个，停在 16fd00b / 09-24）
        └── feat/c-real-machine .. 你的主战场（36 个提交，HEAD af90c1c / 09-30）
                                    │
                                    └── 改动集中在 `Agentic Workflow/`（201 个跟踪文件）

vendor/  ← 同一导师的另外 3 个仓库，以 submodule 形式 pin 住
        ├── Quantum-Calibration-Agent-Blueprint   d8d37bc
        ├── hermes-agent                          c4a5dee
        └── QubitClient                           3da70fe
```

### 三条必须知道的事实

**1）`f2d83b5` 不是上游的提交。** 它是你自己仓库里 PR #2 的合并提交，标题写着 "Merge pull request #2 from Secsix/main"。上游 `Secsix/QMClaw` 的 `main` **从来没有**包含过它 —— 上游只有一条线性历史（`18d7fa1 → … → c3fd6e6 → ea773be → 03d838d`）。所以：

- **不能**用 `git merge origin/main` 或 `git rebase upstream/main` 的常规姿势 —— 你的 `origin` 是**自己的 fork**，它的 `main` 上没有导师的一行代码；
- fork 点的真实依据是**内容相同**：上游 `c3fd6e6` 的 tree 与你的 `f2d83b5` 的 tree **哈希完全相同**（已核实），两边从这一版分叉。

**2）导师的"新代码"只有 2 个提交。**

| 提交 | 日期 | 内容 |
|------|------|------|
| `ea773be` | 2026-09-24 | 从 hermes-hudui 移植 memory 管理 / 动态 Skills 管理 / Cron 定时任务；新增 `sq.iqraw` 离线绘图接口 |
| `03d838d` | 2026-09-30 | Complete Hermes agent integration with management UI |

也就是说，**"融合"的实际体积是 2 个提交、41 个文件**，而不是"一整个仓库"。

**3）`vendor/` 与 `D:\semester4\deploy` 都不在融合范围内**（已核实，详见下一节「二·补」）。

---

## 二·补、融合范围之外的两个东西（别把它们卷进来）

### `vendor/` 三个子模块 —— 本次融合是真正的 no-op

| 检查 | 结果 |
|------|------|
| 导师两个提交是否动了 `vendor` 的 gitlink | **没有**：`c3fd6e6:vendor` 与 `main:vendor` 的 tree 哈希都是 `a5a903f229ab32c1e92c98479330f8d2eb2fa69e`（相同） |
| 你的分支是否动过 `vendor` / `.gitmodules` | **没有**（`git diff f2d83b5 HEAD -- vendor .gitmodules` 为空） |
| 三个目录里的内容是否还是 pin 的那三个 commit | **是**：与上游 `Secsix/*` 逐文件 SHA256 比对，QubitClient 779/779、hermes-agent 11948/11948 全同；Blueprint 300 个文件里 299 个相同，唯一差异是本地多的一行 `.gitignore` 加一个未跟踪的 `data\index.db`（本地跑服务生成） |

**结论**：`vendor/` 里**没有任何自研/分叉代码**，只是上游仓库在 pin commit 上的未改动拷贝。所以本次合并**不要**去改 `.gitmodules` 或 gitlink。

> ⚠️ **但有一条环境前提必须记住**：新克隆的仓库里 `vendor/` 是**空的**（索引里只有 gitlink），而 `services/qca_service/server.py` 在 vendor 缺失时**启动即 `raise RuntimeError`**（已核实，第 41-45 行）。当前你本机的 `vendor/` 是齐的，所以不影响本次合并；但如果以后重装/换机，要先恢复 vendor：
> - 有网：`git submodule update --init --recursive`
> - 无网：把 `D:\semester4\deploy\vendor.zip` 解到 `vendor\`（已核实该 zip 是同一版本的冗余副本，不含 git 历史）

### `D:\semester4\deploy` —— 是环境 + 数据，不是代码

| 项 | 事实 |
|----|------|
| `deploy\qmclaw\qmclaw` | 就是 CLAUDE.md 约定 10 指的 CPython 3.11.16 环境（249 包）。**不含任何 QMClaw 源码** |
| 它与仓库的关系 | 环境里 `hermes-agent` / `qubitclient` / `qca` / `qmclaw` 四个包都是 **editable 安装、指向 `D:\semester4\QMClaw\vendor\*`** —— 也就是说它消费的正是仓库里的 vendor，`deploy\vendor` 只是冗余副本 |
| `deploy\offline_data` | 是仓库离线数据的**子集**：5011 个测量文件 SHA256 全同，仓库侧仅多 3 个可运行时重生成的索引 json |
| 是否要合并 | **不要**。它没有独立代码可融合 |

> 📌 **由此得出一条运维前提**：仓库必须留在 `D:\semester4\QMClaw`，否则那 4 个 editable 安装失效（得重跑 `pip install -e`）。

---

## 三、这次融合要动什么

### 3.1 导师新增（18 个文件，与你零重叠，直接拿来）

```
Agentic Workflow/qmclaw-server/
  config/skills_config.json                     ← 新增
  services/hermes_service/approval_ws.py        ← 新增（WebSocket 审批）
  services/hermes_service/cron.py               ← 新增（定时任务）
  services/hermes_service/mcp_toolsets.py       ← 新增
  services/hermes_service/memory.py             ← 新增
  services/hermes_service/skills.py             ← 新增
  services/common/quantum_tools/analysis_tools.py
  services/common/quantum_tools/workflow_tools.py
Agentic Workflow/qmclaw-web/src/
  components/HermesExtensionsPanel.tsx          ← 新增
  components/HermesSessionsSidebar.tsx          ← 新增
  components/HermesToolbar.tsx                  ← 新增
  lib/markdown.tsx / reasoning-block.tsx / tool-call-card.tsx
根目录：AGENTS.md · SOUL.md · auth.lock · memory/MEMORY.md
```

### 3.2 双方都改过（11 个文件 —— 融合的全部难点都在这）

| 文件 | 判定 | 说明 |
|------|------|------|
| `.claude/progress.md` | 可自动合并 | 双方各自追加日志 |
| `.gitignore` | **看第 0 步怎么提交** | 导师在第 8 行加 `cache/` `*.db*`；你在文件中部改了 `config/memory/` 并删了 `CLAUDE.md` 行 —— 这两处相距很远，**只提交 HEAD 版时自动合并**（已用真实 merge 实测：冲突仍是 4 个文件）。但如果把追加在**文件末尾**的临时目录规则一起提交，差异区域就与导师的新增挨上 → **多出 1 处冲突**。处置见 6.0 |
| `scripts/hermes_runner.py` | 可自动合并 | |
| `scripts/start_all_services.py` | 可自动合并 | |
| `services/analysis_service/server.py` | 可自动合并 | 你加 `PLOT_LOCK` 串行化，导师加 iqraw 离线绘图 |
| `services/hermes_service/server.py` | 可自动合并 | **已实测**：fork 点 19607 字节 → 你 19999（+392）→ 导师 52355（+32748）。两者互不撞，合并后 1291 行、52747 字节，4 个新模块 import 与你的模型接口改动同时都在 |
| `src/index.ts` | 可自动合并 | |
| `config/model_configs.json` | **冲突 ×1** | 双方都在数组头部插模型条目 |
| `services/base/base_service.py` | **冲突 ×1** | 你加 `@_timed_request("DELETE")`；导师加 `handle_streaming_request()` |
| `qmclaw-web/src/components/HermesChatPanel.tsx` | **冲突 ×3** | 见第五节 |
| `CLAUDE.md` | **冲突 ×1** | 同一行"约定 10 python 路径"被双方各改一次；**导师还在同一冲突块里追加了整段 Compaction 压缩规则**（初稿漏看，见 5.4） |

### 3.3 既不重叠、也容易被漏掉的 8 个上游改动（复核补出）

导师 41 个文件 = 新增 18 + 修改 19 + 重命名 4。3.1 只列了新增、3.2 只列了重叠 —— **19 个修改里有 8 个既不在重叠表、也不属于"新增"**：

```
.env.example                                  · services/common/offline_data_lab.py   ← 整份重写
qmclaw-web/package.json                       · qmclaw-web/package-lock.json
qmclaw-web/src/app/page.tsx                   · qmclaw-web/src/components/VariantGenerator.tsx
qmclaw-web/src/lib/api.ts                     · qmclaw-web/tsconfig.tsbuildinfo
```

它们能**干净合并**（你不曾改过），但语义相关，其中两条最要紧：

| 文件 | 是什么 | 为什么危险 |
|------|--------|-----------|
| `services/common/offline_data_lab.py` | **+359 / −39 整份重写**：`fitData()` 签名从 `(exp_num, collect, do_plot)` 变成 `(datasets, fit_func, qname, update, collect, config_name, des, **kwargs)`，新增 `_fit_iqraw` / `_auto_detect_fit_func` / `_default_plot` / `_plot_fit_result` 整条流水线 | 你的 Tier1/Tier2 出图最终走的就是这个类。**API 兼容已核实**（位置参数与 `do_plot` 仍生效，仓库里没有 `exp_num=` 关键字调用点），但**出图口径/样式会变** —— 而验证清单第 5 项只查子串、第 9 项只看 `charts=1`，两项都会全绿 |
| `qmclaw-web/package.json` + `package-lock.json` | 新增 **4 个前端依赖**：`highlight.js` / `react-markdown` / `rehype-highlight` / `remark-gfm` | 本机 `require.resolve` 这 4 个**全部失败**，不装则前端 build（第 7 项）必然红 |

> 复现：`git -C .upstream-mirror diff --name-status --find-renames c3fd6e6 main`，与 3.1 / 3.2 的清单求差。

---

## 四、分支与合并策略

### 4.1 用 merge，不要 rebase

| | merge（推荐） | rebase |
|---|---|---|
| 你的 36 个提交 hash | **保持不变** | 全部重写 |
| 已 push 到 fork 的分支 | 无影响 | 需要 force push |
| `docs/GIT_HISTORY.md` 里记的 hash | **继续有效** | 全部失效，文档要重写 |
| 冲突解决次数 | 一次 | 可能每个提交各来一次 |
| 回退 | `git reset --hard backup` 一步到位 | 同样要 backup |

你的 `docs/` 下有回滚手册（`GIT_HISTORY.md`）且大量提交 hash 已被文档引用，rebase 会让这些记录全部作废。**选 merge。**

### 4.2 分支安排

```
af90c1c (feat/c-real-machine)  ──┐
                                 ├──► merge commit  (新分支 feat/upstream-sync)
03d838d (upstream/main)        ──┘
```

不直接在 `feat/c-real-machine` 上合，而是开 `feat/upstream-sync`：**主战场分支保持可回退**，合并验证全部通过后再把 `feat/c-real-machine` 快进或合并过去。同时在合并**前**打一个备份分支作为硬回退点。

### 4.3 网络：本机必须用这个姿势访问 GitHub

**已实测**：直连 `https://github.com/...` 报 `schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS (0x8009030e)`；**加 `openssl` 后端 + 关证书校验 + `gh-proxy.org` 代理** 即可正常访问（已验证能 clone 上游、能 ls-remote，且从活远端确认 `main = 03d838d`，不是只信本地镜像）。

**但要按"逐命令生效"写，不要落盘成配置**（复核指出的副作用）：

```powershell
# 推荐：只对这条命令生效，不写进 .git/config
git -c http.sslBackend=openssl -c http.sslVerify=false fetch upstream main
```

> ⚠️ 两条提醒：
> 1. `git config http.sslVerify false` 写的是**仓库级持久配置**，会同时作用于 `origin` 的 fetch/push —— 不是"仅本机临时"。别落盘。
> 2. 你的 `origin` **本来就是** `https://gh-proxy.org/https://github.com/hzz-wdb/QMClaw.git`（`git remote -v` 已核实），fetch 和 push 都走这个代理。所以"push 走直连"这个说法与现状矛盾 —— 真要直连得先改 `origin` 的 URL，那是另一件事，本方案不含。

---

## 五、6 处冲突的解法（已逐条实测）

> ⚠️ **本节已按独立复核结果重写。** 初稿的三条规则经复核发现会产出**语义错误**（装饰器挂错函数、丢掉导师新增的整段压缩规则、模型配置 18 条里 16 条重复并翻转默认模型），而初稿自带的校验器**对这 3 处全部报 PASS** —— 因为它只断言"子串存在"，不断言语义。
>
> 下面的规则是**修正版**，已用 `.merge-scratch\resolve-fixed.py` 实测全部通过（校验器已升级为语义断言：装饰器的 AST 绑定目标、`(provider,modelId)` 去重、默认模型首条、关键标识符必须不存在/存在）。

**校验器的教训**（值得记下来）：`ast.parse` 能过 ≠ 代码对。`@_timed_request("DELETE")` 挂到 `handle_streaming_request` 上完全合法、能解析、能运行，只是你的 DELETE 路由从此失去计时、且每个 SSE 请求被记成 `SLOW DELETE`。**语法校验抓不到这类错误，必须断言语义目标。**

### 5.1 `config/model_configs.json` —— 按逻辑模型去重（不是按 id）

**冲突原因（初稿写错了）**：不是"导师动了 `model_default_006` 的字段"，而是导师**整份重建**了模型列表：把 8 个模型的 `id` **全部重编号**（`model_default_00N` / `model_1787551340750_N` → `model_1790146722274_0..7`），顺带重置 `createdAt/updatedAt`，并把 MiniMax M2.7 提到数组首位。因为新旧 id 字符串长度相近，整个文件只涨了 29 字节 —— 这正是初稿误判成"改动很小"的原因。

**实测对照**：

| | 条数 | id 形态 |
|---|---|---|
| fork 点 | 9 | `model_default_001..006` + `model_1787551340750_0..2` |
| 你 | 10 | 上述 + `deepseek-flash`（**在首位**） |
| 导师 | 9 | `model_default_006` + `model_1790146722274_0..7` |

id 交集**只有 `model_default_006` 一个**，其余 8 个逻辑模型两边 id 完全不同 → 按 id 并集会得到 **18 条、其中 16 条是重复模型**。

**❌ 错误解法**：按 `id` 做并集（初稿的做法）。后果有两层：

1. 8 个逻辑模型各出现两次，只因 id 不同而躲过"无重复 id"这条判据；
2. **更严重**：导师条目在前 → 第一条 `enabled + text` 变成 MiniMax M2.7，而 `AgentChatPanel` 与 `HermesChatPanel` 都是 `models.filter(...)[0].id` 取默认模型 → **两个面板的默认模型从 `deepseek-flash` 翻成 MiniMax M2.7**，直接推翻你在 `docs/TODO_MEMORY_HERMES.md` 里记的 H3 验收。

**✅ 正确解法**：按**逻辑模型** `(provider, modelId)` 去重 —— 导师条目采纳其字段改动、**保留你的 id**（前端与 DB 引用的是旧 id）、你独有的条目追加，最后把 `deepseek-flash` 放回首位。不要用 `dict(theirs)` + `json.dumps` 整文件重排（会产生大量 diff 噪音）。

**实测结果（修正版）**：`models=10`，无 `(provider,modelId)` 重复，第一条 `enabled+text` = **`deepseek-flash`** ✓

### 5.2 `services/base/base_service.py` —— 顺序敏感：导师方法必须插在装饰器**之上**

**冲突原因**：两个改动落在**同一个锚点**上（不是"互不相干的两处"）。

- 你的：`@_timed_request("DELETE")` 这一行（B 组稳定性改造：DELETE 路由的慢请求计时）
- 导师的：整个 `handle_streaming_request()` 方法（SSE 流式响应，34 行）

文本并集的顺序是 `ours` 在前、`theirs` 在后，于是变成"装饰器 + 34 行方法定义" —— 装饰器就粘到了 SSE 方法上。

**❌ 错误解法**：简单并集（初稿的做法）。后果（已用 AST 实测确认）：

```
UNION 结果:  handle_streaming_request ← @_timed_request('DELETE')   ← 挂错对象
             do_DELETE                 ← 无装饰器                  ← 你的改造丢失
```

1. DELETE 路由的慢请求统计彻底消失；
2. 每个 SSE 流式请求被计入 `request_count` 并打成 `SLOW DELETE <path>`，指标与日志双双被污染。

**✅ 正确解法**：**theirs 在前、ours 在后** —— 导师的新方法插到 `@_timed_request("DELETE")` 上方，装饰器重新贴回 `do_DELETE`。或者并集后手工把装饰器补回 `do_DELETE` 上方。

**实测结果（修正版）**：`ast.parse` 通过，装饰器绑定为
`{_timed_request('GET'): [do_GET], _timed_request('POST'): [do_POST], _timed_request('DELETE'): [do_DELETE]}` ✓ 且导师的 `handle_streaming_request` 在位 ✓

> 附带发现：README 第 44–45 行（TODO 标题 + 状态行）写着「**SSE 流式处理支持**」与「状态：当前使用轮询模式，需要解决 Python 后台线程 stdout 通信问题」。**导师的这两个提交正好把 SSE 基础设施带过来了**，融合后这条 TODO 可以直接推进。

### 5.3 `HermesChatPanel.tsx` —— 3 处，规则不同

| # | 冲突内容 | 解法 |
|---|----------|------|
| 1 | 你加 `import { useModelStore }`；导师加 `MarkdownContent` / `ReasoningBlock` / `ToolCallsList` 三个 import + `extractImagesFromContent` / `normalizeToolCalls` 两个工具函数 | **并集**（两侧都是同一锚点的纯插入） |
| 2 | 你删掉了硬编码 `DEFAULT_HERMES_MODEL = "minimax/MiniMax-M2.7"`（该模型在配置里根本不存在）；导师在这行旁边加了 `interface ApprovalRequest` | **保留导师的接口，丢弃导师那行 `DEFAULT_HERMES_MODEL`** |
| 3 | 同一处模型加载逻辑：你的 `useModelStore` 版本 ↔ 导师的 `api.hermesGetModels()` 版本 | **取你的** |

**第 2 处为什么不能简单并集**：导师那侧包含 `const DEFAULT_HERMES_MODEL = "minimax/MiniMax-M2.7";` 一行。你的第 3 处取了 OURS，所以这个常量**会变成零引用的死代码** —— 运行期不致命，但它把你特意删掉的错误模型字面量又请了回来。而且 `tsconfig.json` 没有 `noUnusedLocals`、仓库里也没有任何 eslint 配置，**没有任何现有检查会拦住它**。规则必须是"留接口、丢常量"，并在校验器里断言 `DEFAULT_HERMES_MODEL` **不存在**。

**第 3 处为什么必须选边**：这是同一个 `useEffect` 的两种互斥实现，并集会变成 `}, [textModels, model]);` + `}, []);` 双收尾 → 语法坏（实测花括号失衡 `{=427 }=428`）。导师那侧用的是 `api.hermesGetModels()` + 硬编码兜底列表，其中 id 写的是 `"minimax/MiniMax-M2.7"` —— 这个标识符在 `model_configs.json` 里根本不存在；你走 `useModelStore`，与 `AgentChatPanel` 同一套语义。**取你的，复核未找到反例。**

**已核实：取你的不会丢导师的东西。** 四条证据：

1. 导师那段 `useEffect` 里**只有**模型列表拉取，没有别的副作用（逐行看过 `main:HermesChatPanel.tsx` 第 336-350 行）；
2. `DEFAULT_HERMES_MODEL` 这个常量**只在本文件内被使用**（`main` 上仅两处：第 158 行定义、第 340 行 `useState` 初值），删掉不影响任何其它文件；
3. 导师本版新增的 `HermesToolbar` / `HermesSessionsSidebar` / `HermesExtensionsPanel` **完全不碰模型加载**（对 `hermesGetModels` / `useModelStore` / `setModels` / `getModels` 全部 0 命中）；
4. 你改后的自洽性也确认过：`models` 来自 `useModelStore`，下拉框用 `models.map()` 渲染，没有悬空的 `setModels` 调用。

> ⚠️ 验收时别误判：合并后 `HermesChatPanel` 走 zustand store，而导师新增的三个组件**各自** `import { api }` 直接拉取 —— **同一屏存在两条模型加载路径**，"面板里换模型、工具条没跟着变"属于这个结构问题，不是合并 bug。

**实测结果（修正版）**：`DEFAULT_HERMES_MODEL` 不存在 ✓，花括号平衡 `{=423` ✓，5 个关键标识符均在 ✓

### 5.4 `CLAUDE.md` —— 并集（**不是**"取你的"）

**冲突原因（初稿只说了一半）**：冲突块里双方的差异**不止**"约定 10 那一行" —— 导师那侧是 15 行，包含重复的约定 10 一行 **+ 一整段新增的 `# Compaction 压缩规则（自动压缩时生效）`**（摘要必须保留 4 条 / 需要简化丢弃 4 条）。

**❌ 错误解法**：取你的。实测结果 `CLAUDE.md` = 5465 字节 = **你 HEAD 的原文件**，`Compaction` 命中 **0** —— 导师新增的整段压缩规则被静默删除。这不是注释，是 agent 直接读取的行为约定，属于配置语义丢失。

**✅ 正确解法**：**并集** —— 保留你的约定 10/11，追加导师的 `# Compaction 压缩规则` 段，**只丢弃导师那侧重复的 python 路径行**。

**实测结果（修正版）**：`Compaction` 段在位 ✓，你的约定 10/11 在位 ✓，无残留冲突标记 ✓

---

## 六、执行步骤

> 以下命令**已按本机实测校正**（含网络姿势与沙箱限制）。

> 🚀 **也可以一条命令跑完**：`docs/merge_verify_tools/run-merge.ps1` 把下面第 0~9 步全部自动化了
> （含第 0 步的"提交全部已修改文件 + 断言干净"、备份分支、`fetch`、merge、调 `resolve-conflicts.py`、
> 收尾、自动验证、**人工闸门**、以及 `-Continue` 时的快进合回）。
> **它必须在沙箱外运行**（沙箱禁写 `.git`）；想先演练可加 `-DryRun`（在探针仓库上真跑一遍，不碰真实 `.git`）。
> 已在探针仓库上实测：`4 冲突 → 全解 → 提交 → 收尾 → 13 PASS / 0 FAIL`，`-Continue` 的第 8/9 步也验证到全绿。
> **下面的手工步骤仍然保留** —— 想逐步确认、或脚本因环境报错时，按它走。

> 👀 **执行前先看结果预览**：`docs/merge_verify_tools/MERGED_CONTENT_PREVIEW.md`
> —— 它把"4 个冲突文件会被解成什么样"直接列出来了（模型 10 条与默认模型、装饰器绑定、并集结果、
> 整体改动面 `41 files / +12400 −357`）。你可以在**什么都不动**的情况下先审一遍结果。

**执行前提（已核实）**：

1. **已跟踪文件里有若干处于修改状态** —— **不要照抄任何固定清单，执行前先跑这一条**：

   ```powershell
   git status --short --untracked-files=no      # 这就是第 0 步必须全部提交的集合
   ```

   > 这个数字**会随文档工作变化**，写死它必然会过期 —— 本方案已因此栽过两次
   > （先是漏了 `docs/BACKLOG.md`，后是漏了 `.claude/progress.md`）。
   > **正确做法**：用 `git add -u` 提交全部已跟踪改动（`run-merge.ps1` 就是这么做的），
   > 它从 `git status` 推导，永远不会过期，也**不会**碰到 `.gitignore` 的忽略规则
   > （注意 `.gitignore` 里有 `.claude/`，而 `git add <路径>` 对尚未入索引的路径会被判 "ignored" ——
   > 详见归档 README 的「坑」第 9 条）。

   写方案时（2026-10-08）的集合是这 5 个，仅供对照：

   | 文件 | 为什么被改 |
   |------|-----------|
   | `.gitignore` | 本轮为隔离临时目录而加（第 0 步会先还原成 HEAD 版） |
   | `CLAUDE.md` | 加入口指引（指向本方案与 `run-merge.ps1`） |
   | `docs/BACKLOG.md` | 本轮新增 G 组 10 条 |
   | `docs/CHANGES_VS_SOURCE.md` | 上一轮补记稳定性改造 |
   | `.claude/progress.md` | 按 CLAUDE.md 约定 5 补记开发日志（**合并时还会再改一次**，见第九节） |

   **第 0 步必须把它们都提交**，否则第 0.1 步的"工作树必须干净"断言会非空、直接卡住。
2. **分析材料很大且在变**：`.upstream-mirror` 约 32 MB、`.merge-scratch` 约 40 MB、`.analysis` 约 180 MB —— 合计 **200+ MB / 1.5 万个文件**（实测值随探针仓库增删而变，别当固定值）。一条 `git add -A` 就会把它们全吞进提交。
3. **未跟踪文件不影响 merge**（只有"已修改的已跟踪文件"会挡住 merge）。
4. `docs/对齐-进度与待确认.md` 目前是**未跟踪**状态（不是"被修改"）—— 它从没被提交过，第 0 步会把它一并纳入版本控制。

> ⚠️ **绝对不要用 `git add -A`**。会把 200+ MB 分析材料提交进去，且**一旦 push 就很难收回**。

### 6.0 `.gitignore` 这一步有个坑（已实测，务必按此做法）

`.gitignore` 现在的未提交改动是**追加在文件末尾**的 3 条临时目录规则。如果把它直接提交，第 4 步的冲突会从 4 个变成 5 个 —— 因为这 3 条落在文件末尾，而导师的 `cache/` / `*.db*` 加在第 8 行附近，git 的差异区域会重叠。

| 第 0 步怎么提交 | 真实 merge 实测结果 |
|---|---|
| 只提交 HEAD 版 `.gitignore`（603 字节，**不含**那 3 条） | **4 个冲突文件**，`.gitignore` 自动合并 ✓ |
| 把这 3 条一起提交 | **5 个冲突文件**（多一个 `.gitignore`；`CLAUDE.md` 仍是 1 处） |

**两种做法，任选其一**：

- **推荐（本节命令采用）**：第 0 步把 `.gitignore` 还原成 HEAD 版再提交（`603` 字节）→ 冲突保持 4 个。
- 或者：把这 3 条一起提交，接受多 1 处 `.gitignore` 冲突，解冲突时把两段都留下。

> ⚠️ **还原之后、合并完成之前，`.gitignore` 必须保持"与 HEAD 一致"**，不能再加回那 3 条规则 ——
> 否则第 4 步 merge 会因为 `.gitignore` 有未提交改动而 **Aborting**（正是 0.1 要断言的那条）。
> 代价是合并期间那 200+ MB 分析材料会显示为**未跟踪**，所以：
> **第 2~6 步一律不要用 `git add -A` / `git add .`**，只按路径精确 `add`（这几步的命令都是精确路径，照抄即可）。
> 3 条规则放到**第 8 步**（合并成功之后）作为独立提交入库。

```powershell
cd D:\semester4\QMClaw

# 0) 把 .gitignore 还原成 HEAD 版（去掉末尾 3 条临时目录规则），再提交**全部已修改的已跟踪文件**，
#    清干净工作树。漏掉任何一个，第 0.1 步的断言就会非空。
#    ⚠️ 文件数会随本轮工作变化 —— 先跑 `git status --short --untracked-files=no` 看实际清单，
#       下面这 4 个是写方案时的状态（含 .claude/progress.md，约定 5 要求的那条日志）。
#    ⚠️ 必须用 cmd 重定向做字节级还原。用 PowerShell 的
#       `git show HEAD:.gitignore | Set-Content -Encoding utf8 -NoNewline`
#       会加 BOM 又去掉末尾换行 → 实测只剩 561 字节、且 git 仍认为文件被改（白折腾）。
cmd /c "git cat-file blob HEAD:.gitignore > .gitignore"
(Get-Item .gitignore).Length                  # 应为 603
cmd /c "git diff --quiet -- .gitignore"       # exit 0 = 与索引一致
git status --short
git add .gitignore "docs/BACKLOG.md" "docs/CHANGES_VS_SOURCE.md" ".claude/progress.md" "docs/对齐-进度与待确认.md"
git commit -m "docs: 补记稳定性改造与导师对齐摘要；BACKLOG 新增 G 组；progress 记录融合方案"

# 0.1) 【P0】断言工作树已干净 —— 否则第 4 步 merge 会直接 Aborting(exit 2)
#      已实测：.gitignore 只要还是 modified，merge 就报
#      "Your local changes to the following files would be overwritten by merge: .gitignore"
git status --short --untracked-files=no      # 必须无输出

# 0.2) 【重要】合并期间不要碰 .gitignore
#      此时 .upstream-mirror/ .merge-scratch/ .analysis/ 会显示为**未跟踪**（200+ MB / 1.5 万文件）。
#      它们不会被 merge 影响，也不会被提交 —— 前提是：
#      ⚠️ 第 2~6 步一律不要用 `git add -A` / `git add .`，只按路径精确 add。
#      3 条忽略规则留到第 8 步（合并成功之后）再入库。

# 1) 备份分支 —— 硬回退点
git rev-parse --abbrev-ref HEAD              # 必须是 feat/c-real-machine
git rev-parse HEAD                           # 必须是 af90c1c
git branch backup/pre-upstream-merge-20261008

# 2) 配置 upstream remote（本机必须带 openssl，否则 schannel 报 SEC_E_NO_CREDENTIALS）
#    推荐逐命令传参，不要把 sslVerify=false 落盘进 .git/config（它会作用于 origin）
git remote add upstream https://gh-proxy.org/https://github.com/Secsix/QMClaw.git
git -c http.sslBackend=openssl -c http.sslVerify=false fetch upstream main
git rev-parse refs/remotes/upstream/main     # 必须是 03d838d23fbc95f6a8c727a3fece164976085820

# 3) 开同步分支
git checkout -b feat/upstream-sync

# 4) 合并（预期恰好 4 个文件冲突）
git merge upstream/main --no-ff -m "merge(upstream): 融合导师上游 03d838d（Hermes 扩展 + SSE 基础设施）"
git status --short                           # 确认冲突文件恰为下面 4 个

# 5) 按第五节【修正版】规则解冲突 —— 已做成脚本，不必手搓
#    脚本永久归档在 docs\merge_verify_tools\resolve-conflicts.py（.merge-scratch 会被第 9 步清理）
#    试算（不改任何文件，先看结论）：
python "docs\merge_verify_tools\resolve-conflicts.py" .
#    写回 + git add：
python "docs\merge_verify_tools\resolve-conflicts.py" . --write
#    脚本只处理下面 4 个文件，规则即第五节：
#      model_configs.json   -> 按 (provider, modelId) 去重；保留我方 id；deepseek-flash 置首
#      base_service.py      -> 导师方法插到 @_timed_request("DELETE") 之上（顺序敏感！）
#      HermesChatPanel.tsx  -> 冲突1 并集；冲突2 留 interface ApprovalRequest、丢 DEFAULT_HERMES_MODEL；冲突3 取本地
#      CLAUDE.md            -> 并集：保留我方约定 10/11 + 追加导师 Compaction 段
#    若出现第 5 个冲突文件（.gitignore），脚本会明确报"预期之外的冲突文件"，
#    说明第 0 步没把 .gitignore 还原干净 —— 见 6.0，回去重做第 0 步。
git status --short
git commit --no-edit

# 5.1) 【P0】前端新依赖必须先装，否则第 7 项（前端构建）必然红
#      导师 package.json 新增：highlight.js / react-markdown / rehype-highlight / remark-gfm
cd "Agentic Workflow/qmclaw-web"; npm install; cd D:\semester4\QMClaw

# 5.2) 【P1】删掉被导师覆盖的陈旧增量构建产物，再跑构建
Remove-Item "Agentic Workflow\qmclaw-web\tsconfig.tsbuildinfo" -ErrorAction SilentlyContinue

# 6) 验证（见第七节）
#    ╔══════════════════════════════════════════════════════════════════════╗
#    ║  在此停下。第 8/9/10/11/17/23 项需要人工启停服务（CLAUDE.md 约定 3）。 ║
#    ║  人工起服务 → 跑完这些项 → 全部通过后，才执行下面的第 7 步。          ║
#    ╚══════════════════════════════════════════════════════════════════════╝

# 7) 全部验证通过后，才把同步分支快进合回主战场分支
git checkout feat/c-real-machine
git merge feat/upstream-sync --ff-only

# 8) 合并完成后，再把临时目录规则加回来（此时不会再与任何东西冲突）
Add-Content .gitignore "`n# 上游融合分析的临时目录（本地用，勿提交）`n.upstream-mirror/`n.merge-scratch/`n.analysis/"
git add .gitignore
git commit -m "chore: 忽略上游融合分析临时目录"

# 9) 清理分析材料（都不进版本库）
#    ⚠️ 保留 .upstream-mirror/ —— 它是上游裸镜像，docs\merge_verify_tools\ 里
#       5 个脚本都要用它（pipeline-test / probe-sync / tw3 / gi3way / verify-doc-claims）。
#       它已被 .gitignore 忽略，31.6 MB，留着下次同步上游还能直接用。
Remove-Item -Recurse -Force .merge-scratch, .analysis
```

> 📌 **`.upstream-mirror/` 是长期本地缓存，不要删**。它让"再同步一次上游"变成一条 `git fetch`，
> 而不必重新 clone。<br>
> 📌 **`.merge-scratch/` 删掉不影响复现**：12.1 里点名的 **11 个脚本已全部归档**到 `docs/merge_verify_tools/`
> （另有新写的 `pipeline-test.ps1`，共 12 个脚本 + `README.md` + `ARCHIVE_CHECK.md`），
> 搬运均逐字节校验过哈希（11 个搬运脚本分两批，第一批 6 个、第二批 5 个，全部一致）。

> 🔴 **这段命令在本 agent 会话里一条都跑不通。** `git config` / `remote add` / `fetch` / `branch` / `add` / `commit` 全都要写 `.git`，而沙箱一律拒绝。**必须由人（或把会话切到完全权限）在普通终端里执行。**

**回退**：`git reset --hard backup/pre-upstream-merge-20261008`（同步分支上出问题也可以随时 `git merge --abort`）。

---

## 七、验证清单

合并不是"解完冲突就完了"。以下每条都要跑，**任何一条不过就不要合回主分支**。

> ⚠️ **清单本身也已被复核修正过**。初稿第 1 项的命令是**坏的**（`-E` 下 `\|` 是字面竖线，实测在冲突标记全在时返回 0 行 = 假绿）；第 2/3/4/6/8 项的判据太弱，抓不到第五节那三处语义错误。

| # | 检查项 | 判据 | 状态 |
|---|--------|------|------|
| 1 | 无残留冲突标记（**两阶段判据**） | **解冲突前**：`git grep -nE '^(<{7}\|={7}$\|>{7}\|\|{7})' -- .` 应命中 **18 行**（6 处冲突块 × 3 个标记；这是用来证明正则不假绿的）；**解完并 `git add` 后**：应命中 **0 行**。**不要**用 `^(<{7}\|={7}\|>{7})` —— 那是假绿；去掉 `\|` 转义又会被 `demo_hermes_verify.py` 的 `=====` 分隔行误报 | 待跑 |
| 2 | 模型配置**语义**正确 | `model_configs.json` 应为 **10 条**；无 `(provider, modelId)` 重复（**不是**只查 id 重复 —— 初稿的 18 条方案"无重复 id"也能过）；JSON 合法 | 待跑 |
| 3 | Python 语法 | 用**约定 10 的解释器**：`D:\semester4\deploy\qmclaw\qmclaw\python.exe -m compileall -q "Agentic Workflow\qmclaw-server\services" "Agentic Workflow\qmclaw-server\scripts"`。注意 PATH 上的 `python` 是 3.14，不是这套环境 | 待跑 |
| 4 | 你的稳定性改造**按语义**还在 | **断言装饰器的 AST 绑定目标**：`_timed_request('DELETE')` → `do_DELETE`（不能只查 `SLOW ` 子串 —— 初稿的错挂方案子串全在）；另查 `ThreadingHTTPServer`、`PLOT_LOCK` / `serialized_plot`、`_INDEX_LOCK` | 待跑 |
| 5 | 你的离线链路还在 | 单位换算表 `X_UNIT_SCALE`、复合 dtype 拆列逻辑、Tier1/Tier2 出图分支均未被覆盖 | 待跑 |
| 6 | 新模块能 import（冒烟） | **不是**只 grep `server.py`：用约定 10 的解释器跑 `python -c "import services.hermes_service.memory, services.hermes_service.skills, services.hermes_service.cron, services.hermes_service.approval_ws, services.hermes_service.mcp_toolsets"`（`compileall` 只查语法，查不出缺模块/缺符号） | 待跑 |
| 7 | 前端构建 | 先 `npm install` 装 `highlight.js` / `react-markdown` / `rehype-highlight` / `remark-gfm`（4 个都缺，本机 `require.resolve` 全部失败），**并先删掉**被导师覆盖的 `tsconfig.tsbuildinfo`，再 build | 待跑 |
| 8 | 接口与默认模型 | `/api/models` 与 `/api/hermes/models` 返回一致且 **第一条 id == `deepseek-flash`**（只查"包含 deepseek-flash"抓不到默认模型翻转 —— 初稿的 18 条方案也能过），对应用户 H3 验收 | 待跑 |
| 9 | 端到端场景 + **人工看图** | `对 q10lu8 运行 t1`（真实数据）与 `对 q10lu12 运行 t1`（合成降级）返回 `success=True` 且 `charts=1`；**并与合并前的图肉眼比对** —— `offline_data_lab.py` 被导师整份重写，`charts=1` 抓不到出图口径变化 | 待跑 |
| 10 | hermes 对话 + 新功能 | 经网关对话正常（融合前 8.3s）；另走一次 **approval / cron** 接口（新功能会 import `vendor/hermes-agent`，版本兼容性静态 grep 看不出来） | 待跑 |
| 11 | **`HERMES_HOME` 语义 + 技能列表非空** | 本机 `.env` 现指向 `data\hermes`，导师假设它指向**项目根**。**但注意：`skills.py` 不读 `HERMES_HOME`** —— 技能列表走 `<qmclaw-server>/data/skills`（现不存在）。修法与判据见第十·补.1：起服务后 `GET /api/hermes/skills` 必须**非空**且能看到 7 个技能名 | 待跑 |
| 12 | **`DEFAULT_HERMES_MODEL` 必须不存在** | `git grep -n DEFAULT_HERMES_MODEL -- "Agentic Workflow/qmclaw-web/src"` 无输出（用户 H3 要求；仓库无 eslint / `noUnusedLocals`，没有别的检查守护它） | 待跑 |
| 13 | 记忆路径盘点 | 实际有**三处**（见第十节）；记录各自读写路径与数据量，登记 BACKLOG（不要求本轮统一） | 待跑 |
| 14 | `.gitignore` 误伤复查 | 用合并后的 `.gitignore` 对 `config/memory/`、`docs/`、`vendor/` 等关键路径跑忽略判定 | 待跑 |
| 15 | 新模块依赖可导入 | 用约定 10 解释器 `import apscheduler, tzlocal, portalocker, websockets` 通过；并把这 4 个包补进 `Agentic Workflow/qmclaw-server/python/requirements.txt` | 待跑 |
| 16 | `vendor/` 保持不动 | `git diff HEAD~1 HEAD --stat -- vendor .gitmodules` 应为空（导师两提交未动 vendor，见第二·补节） | 待跑 |
| 17 | `qca_service` 能起来 | 该服务在 vendor 缺失时**启动即 `raise RuntimeError`**（`services/qca_service/server.py:41-45`）；确认 `vendor/Quantum-Calibration-Agent-Blueprint` 在位后再起 | 待跑 |
| 18 | 重命名测试文件的旧引用扫描 | 4 个文件被 `R100` 移进 `tests/`；扫 `docs` / `scripts` / `README.md` 里是否还有指向根路径的引用（否则会指向空文件） | 待跑 |
| 19 | `tsconfig.tsbuildinfo` 出库 | 确认它已从索引摘除并加入 `.gitignore`（`incremental: true` + 被导师覆盖，留着会让工作树反复变脏，与约定 11 打架） | 待跑 |
| 20 | 改动面核对 | `git diff --name-only HEAD^1` 应等于"我方 62 ∪ 导师 41"两侧清单的并集 —— 证明合并没碰到预期之外的文件 | 待跑 |
| 21 | `auth.lock` 不入库 | `git rm --cached auth.lock` 并从索引摘除（**1 字节空格**的运行时残留、全上游零引用，证据见第十·补.2） | 待跑 |
| 22 | **`memory/MEMORY.md` 去留已决定** | 见第十·补.3：内容有用（`/plots/` URL 规范）但被网关运行时改写 → 决定"保留入库（每次同步冲突）"或"移出索引 + `.gitignore`" | 待跑 |
| 23 | **技能目录已修好** | 按第十·补.1 的修法 A/B 之一处理后，`GET /api/hermes/skills` **非空**且能看到 7 个技能名（`mklink /J` 已实测可行） | 待跑 |

> 第 8、9、10、11、17、23 项涉及**启停服务**，按 CLAUDE.md 约定 3，**必须由人来起停**，不要自行拉起或杀掉进程。

---

## 八、风险与注意点

| 风险 | 说明 | 处置 |
|------|------|------|
| **多套 memory 并存（最需要决策的一条）** | 实测是**三处**路径（不是两套，见第十节 10.1）：你的 `config/memory/`、导师 python 侧的 `data/memories/`、导师**网关**侧的 `<repo>/memory/`。三者互不引用，融合后 Agent 侧记的账 Hermes 看不见，反之亦然；**导师自己两侧也不互通** | **不是 git 冲突，是产品决策**。建议先合、后单独立项统一；至少在 BACKLOG 登记"记忆多路径待收敛"。见第十节 |
| **新模块的第三方依赖没有被声明（上游就缺）** | 导师新增的 `hermes_service/cron.py` 用 `apscheduler` + `tzlocal`，`hermes_service/memory.py` 用 `portalocker`；而 `hermes_service/server.py` 是**顶层** `from . import cron/memory` —— 缺包会让**整个 hermes 服务起不来**。实测：`tzlocal`、`portalocker`、`websockets` 在**你的分支和上游的 requirements 里都没声明**（`apscheduler` 只在 `lqcs_deploy/requirements.txt` 第 9 行）。本机解释器 4 个包都恰好已装，所以**不会立刻炸** | 不是本次合并引入的，是上游原来的漏项。**建议本轮补一个 commit** 把 `apscheduler` / `tzlocal` / `portalocker` / `websockets` 加进 `Agentic Workflow/qmclaw-server/python/requirements.txt`，并在第七节第 **15** 项做导入自检 |
| **`websockets` 同样未声明** | `approval_ws.py` 依赖它做 WebSocket 审批通道；本机已装，声明缺失 | 同上 |
| **前端新增 4 个依赖，本机全缺** | 导师 `package.json` 加了 `highlight.js` / `react-markdown` / `rehype-highlight` / `remark-gfm`，新的 `src/lib/markdown.tsx` 直接用它们。本机 `require.resolve` 四个全部失败 | 合并后**先 `npm install`** 再跑第 7 项（前端构建），否则必然红。注意 `package-lock.json` 也被导师那版覆盖，`npm ci` 会严格按他的 lock 装 |
| **`offline_data_lab.py` 被整份重写** | `+359/−39`。API 兼容已核实（无 `exp_num=` 关键字调用点），但**出图口径与样式会变**，而现有检查全绿 | 第 9 项加"与合并前的图**肉眼比对**"，不要只判 `charts=1`。另有【推断】风险：旧用法 `fitData(-1, collect=True, do_plot=False)[1][-2][0]`（`python/workflow/model.py:189`）依赖旧的返回结构，别把离线 updater 接到那条调用上 |
| **`HERMES_HOME` 语义冲突** | 本机 `.env` 里 `HERMES_HOME=…\qmclaw-server\data\hermes`；导师新增逻辑与 `.env.example` 都假设它指向**项目根**（"to use project's skills directory"）。已设置 → 导师的回落逻辑不生效 | 合并前定下语义。**但注意：改它修不了技能列表** —— `skills.py` 根本不读这个变量，见第十·补.1 |
| **融合后 `/api/hermes/skills` 会返回空** | `skills.py` 读 `<qmclaw-server>/data/skills`（该目录不存在），而项目技能在 `<repo>/skills/<name>/SKILL.md`（7 个）。上游自己也没带技能文件 | **修法与判据见第十·补.1**：`mklink /J` 建目录联接（已实测可行，`rglob` 能穿过 junction）；验收 `GET /api/hermes/skills` 非空且能看到 7 个技能名。你自己的 agent 侧技能不受影响 |
| **`memory/MEMORY.md` 与 `auth.lock` 是运行时/无引用文件** | `memory/MEMORY.md`（内容有用：要求图片用 `/plots/` URL、禁 base64）被**网关直接读写**（`getHermesMemoryDir()` = `<repo>/memory`，4 个 handler 拼 `MEMORY.md`）→ 合并后工作树会被常驻改写，违反约定 11，且下次同步上游必冲突。`auth.lock` 是 **1 字节空格**的运行时残留，**全上游零代码引用** | 处置与证据见**十·补.2 / 十·补.3**；第 21 项验收 |
| **`tsconfig.tsbuildinfo` + `incremental: true`** | 它是**被跟踪的构建产物**且被导师改过；`qmclaw-web/tsconfig.json` 开着 `incremental`。合并后工作树里躺着的是**导师机器上的增量构建状态** | 跑第 7 项**之前**先删掉它，再单独提交"移出索引 + 加 `.gitignore`"（第 19 项） |
| **同一屏两条模型加载路径** | 合并后 `HermesChatPanel` 走 zustand `useModelStore`，而导师新增的 `HermesToolbar` / `HermesSessionsSidebar` / `HermesExtensionsPanel` 各自 `import { api }` 直接拉取 | "面板里换模型、工具条没跟着变"是结构问题，**不是合并 bug**，验收时别记成回归 |
| **新工具模块的注册入口是 Hermes 侧** | 导师新增的 `quantum_tools/analysis_tools.py`（528 行）与 `workflow_tools.py`（456 行）由 `hermes_service/server.py` 与 `mcp_toolsets.py` 在运行时注册，**不走你的 `agent_service/adapter.py`** | 已核实：这两处注册代码都在自动合并的文件里，无需手工接线。若希望 Agent 侧也能用这些工具，需在 `adapter.py` 另行登记（本轮不做） |
| **`.gitignore`：基本无害，但有一处埋雷** | 导师只加了 `cache/` 与 `*.db*` 两行。实测被跟踪文件里**没有** `cache/` 路径（0 命中），但有 **1 个**：`Agentic Workflow/qmclaw-server/services/qca_service/data/index.db`。已被跟踪的文件不受 `.gitignore` 影响，所以**当前不会误伤**；但一旦它将来被 untrack，`*.db*` 会让它**静默**变成不可提交 | 合并后 `git check-ignore -v` 复查（第七节第 14 项）；这个 `index.db` 是否该进版本库，另开一个小议题 |
| **`vendor/` 的"已修改"假象** | **已核实：导师这两个提交并没有动 `.gitmodules`，也没动 `vendor` 的 gitlink**（`c3fd6e6:vendor` 与 `main:vendor` 的 tree 哈希相同），你的分支同样没动过。所以合并本身不会碰 `vendor/`。但你的 `vendor/` 是**无 `.git` 的普通目录**而索引里是 submodule gitlink，`git status` 可能显示异常 | 合并前先看一眼 `git status` 基线，把这个既有状态和合并引入的改动区分开；**不要**在合并提交里顺带修 submodule。详见第二·补节 |
| **`qca_service/core/` 是死代码（被 vendor 遮蔽）** | `qca_service` 自带 `core/{discovery,models,runner,storage,__init__}.py`，与 vendor `core/` 同名；但 `server.py` 先把 vendor 插进 `sys.path` 再 import，同名 `core` 解析到 vendor 那份。服务侧那几个文件体积明显更小（如 `runner.py` 4264 vs vendor 17796 字节），且 `server.py:86` import 了服务侧**不存在**的 `core.workflow_validation` —— 反证运行时用的是 vendor 的 core | **不是本合并引入的**。属清理候选（连同 `qca_service/scripts/` 的旧版本），但**删之前必须确认无其它调用方** —— 复核报告未启动服务验证实际导入结果。建议本轮**只登记不动手** |
| **`scripts/` 与根目录测试文件被移动** | 导师把 `sse_verify.py` / `test_labrad.py` 等 4 个文件 `R100` 重命名进 `tests/`。你的工作树里这些文件还在根目录 | 合并会按重命名处理；确认根目录旧文件已删除、`tests/` 下新文件存在（第 18 项扫描旧引用） |
| **代理是第三方** | `gh-proxy.org` 转发了你的 fetch/push 流量 | 与 4.3 一致：你的 `origin` **本来就是** gh-proxy 的 URL，fetch/push 都走它；确需直连得先改 `origin` 的 URL（本方案不含）。**不要**为此临时改仓库配置 |
| **沙箱会拒绝写 `.git`** | 本机 DSH 沙箱下 `git config` / `remote add` / `fetch` / `branch` / `add` / `commit` 等所有写 `.git` 的操作一律被拒（`.git` 的 ACL 里有一条 `Everyone: Deny DeleteSubdirectoriesAndFiles`，且沙箱用的 SID 与本人账号都不在允许项里） | **第六节整段必须由人在普通终端执行**（或把会话切到完全权限）。在本 agent 会话里一条都跑不通 |

---

## 九、做完之后要更新什么

按 CLAUDE.md 约定 5 与 11：

1. `.claude/progress.md` 追加本轮记录：改动内容、文件、验证结果、合并提交 hash
2. `docs/CHANGES_VS_SOURCE.md` 的基线描述需要更新（现在写的是 `f2d83b5..HEAD`，融合后上游那份也进来了，应说明"基线之上叠加了上游 `03d838d`"）
3. `docs/GIT_HISTORY.md` 追加合并提交，保持回滚手册可用
4. `docs/BACKLOG.md`：SSE 那条 TODO 现在有基础设施了，可以更新状态；**并且我已预先登记好「G 组」共 10 条**（G1 记忆三处收敛 / G2 skills 两边各做一套 / G3 memory_store 双份 / G4 新模块依赖未声明 / G5 状态文件入库决定 / G6 前端 4 个依赖 / G7 `offline_data_lab.py` 出图口径回归 / G8 qca_service 死代码 / G9 tsbuildinfo / G10 SSE 就位）—— 合并完成后无需再补登记，逐条推进即可
5. `README.md` 的 TODO 里 "SSE 流式处理支持" 可以勾掉或降级

---

## 十、融合之后会浮现的重复实现（不是 git 冲突，但必须知道）

### 10.1 记忆变成**三处**路径并存（不是两套）

初稿说"两套记忆"，复核查证后是**三处**，而且导师自己的 python 服务与他自己的 UI 指向的**还不是同一个文件**：

| 位置 | 谁读写的 | 证据 |
|------|----------|------|
| `<qmclaw-server>/config/memory/`（`episodes/*.json`、`skills/*.json`、`index.json`） | **你的** `common/memory_store.py` + `/api/agent/memory/*` | `memory_store.py:32` `MEMORY_BASE = _SERVER_ROOT/config/memory` |
| `<qmclaw-server>/data/memories/{MEMORY,USER}.md` | **导师 python 侧** | `hermes_service/memory.py:106-122` `_get_data_dir()` |
| `<repo>/memory/MEMORY.md` | **导师网关侧**（新 UI 的 memory 接口直接读文件） | `src/index.ts:2852` `getHermesMemoryDir()` = `<repo>/memory`，4 个 handler 拼 `MEMORY.md` |

**这意味着融合后 Hermes 记住的事、Agent 侧查不到，而导师自己两侧也不互通。** 收敛工作量比初稿估计的大 —— 初稿说"先合、后单独立项"这个方向仍然对，但要**在 BACKLOG 里写明是三处而不是两套**。

### 10.2 skills 也是两边各做一套（初稿漏了）

| | 你的 | 导师的 |
|---|---|---|
| 存储 | `qmclaw-server/config/skills.json` + `memory_store` 的 skills | `HERMES_HOME/skills/**/SKILL.md` + 新增 `config/skills_config.json` |
| 接口 | `/api/skills` CRUD（`src/index.ts:2685-2724`）、`/api/agent/memory/skills` | `/api/hermes/skills` |

所以初稿那句"记忆是**唯一**一个两边各做了一套、且都还能跑的地方"**不成立** —— skills 是第二处，而且你这边自己还是两处。

### 10.3 `scripts/memory_store.py` 与 `services/common/memory_store.py` 双份（第三处重复）

按你的 `CHANGES_VS_SOURCE.md` 记载，`services/common/memory_store.py` 是从 `scripts/memory_store.py` **移植**过去的，磁盘格式保持不变。移植后脚本版仍在库里，两份实现会随融合一起保留。

**影响**：低（当前无引用冲突），但属于"同一功能两份实现"的隐患 —— 以后改存储格式容易只改一份。建议在 BACKLOG 里记为"清理项"。

### 10.4 建议：先合，后单独立项

不要在这次合并里顺手统一存储层，原因有三：① 三处都有正在工作的读写路径，改动会牵连前端两个面板、两侧 skills 接口与 8 个 memory 端点；② 存储格式完全不同（`§` 分隔的 markdown vs JSON 索引），统一需要设计迁移；③ 合并不是重构的时机。**本轮只在 BACKLOG 登记，不动手。**

> 顺带一提：你的 `docs/TODO_MEMORY_HERMES.md` 里已有 memory 与 hermes 的实施记录，收敛方案应该从那份文档续写。

---

## 十·补、三处必须处置的地方（已实测，含具体修法与判据）

### 十·补.1 技能列表会空 —— 而且**改 `HERMES_HOME` 修不了它**

**现象（已实测）**：合并后导师新增的 `/api/hermes/skills` 会返回**空列表**。

**根因（逐层查证）**：

| 环节 | 事实 |
|------|------|
| 谁收集技能 | `services/hermes_service/skills.py` 的 `collect_skills()` |
| 从哪读 | `skills.py:119` `skills_dir = _get_data_dir()` → `skills.py:63` `data_dir = <qmclaw-server>/data/skills`，然后 `skills.py:137` `skills_dir.rglob("SKILL.md")` |
| 该目录现状 | **不存在** → rglob 返回空 → 列表空 |
| 上游自己有没有带 | **没有**（`git ls-tree main -- .../hermes_service/data` 为空） |
| 项目真正的技能在哪 | `<repo>/skills/<name>/SKILL.md` —— **7 个**（`qmclaw-decision-architecture`、`qmclaw-framework-overview`、`qmclaw-tool-scheduling`、`qmclaw-tuneup-workflow`、`quantum-experiment-plotter`、`qubit-calibration`、`qubit-data-analysis`；`_shared/` 无 SKILL.md，无害） |
| **`HERMES_HOME` 在这里起什么作用** | **不起作用**。全上游对 `HERMES_HOME` 的引用只有两类：① `scripts/hermes_runner.py:147-152` 与 `services/hermes_service/server.py:175-181` 的"取值或回落"逻辑；② `server.py:186` 的一行日志。**`skills.py` 完全不读它** |

> ⚠️ 所以网上最容易想到的"把 `HERMES_HOME` 改成项目根"**修不了技能列表** —— 它只影响 Hermes agent 自己的 `get_skills_dir()`（`<HERMES_HOME>/skills`）。

**三种修法**（按侵入性排序，任选其一）：

| 方案 | 做法 | 代价 |
|------|------|------|
| **A. 建目录联接（推荐，零改码）** | `mklink /J "<qmclaw-server>\data\skills" "<repo>\skills"` | **已实测可行**：`mklink /J` 成功，Python `rglob('SKILL.md')` **能穿过 junction** 扫到 `skillA\SKILL.md`。<br>注意：`data/` 已在 `.gitignore`（`Agentic Workflow/qmclaw-server/data/`），所以这个联接**不会进版本库**，换机要重建 |
| **B. 改 `skills.py` 的 `_get_data_dir`** | 让它优先读 `HERMES_HOME/skills`，否则回落项目 `<repo>/skills` | 改上游文件 → 下次同步上游会冲突；但语义最正确 |
| **C. 手工复制** | 把 `<repo>/skills/*` 拷进 `<qmclaw-server>/data/skills/` | 会变成**两份技能**，改一份另一份不跟着变 —— 不推荐 |

**验收判据**：起服务后 `GET /api/hermes/skills` 返回**非空**且能看到上面 7 个技能名。

> 好消息：**你自己的 agent 侧技能功能不受影响**（走 `config/skills.json` + `memory_store` 的 `/api/agent/memory/skills`），这一处只影响导师新加的 Hermes 技能管理界面。

### 十·补.2 `auth.lock` 是运行时残留，别入库

已核实（证据齐全）：

- 上游提交的 `auth.lock` 内容 = **1 字节，一个空格**（`b' '`）；
- 你本机 `qmclaw-server\data\hermes\auth.lock` 内容 = **同样 1 字节空格**；
- **全上游零代码引用**（`git grep 'auth\.lock' main` 为空）。

→ 基本可以确定是导师从 hermes 运行目录误提交的运行时文件。

**建议**：合并后 `git rm --cached auth.lock`，并加进 `.gitignore`。

### 十·补.3 `memory/MEMORY.md`：内容有用，但它会被网关运行时改写

**内容本身与你的方案一致**（已核实）：上游那份 4 行，写的是"图片用 `/plots/filename.png` 的 URL 格式，**不要试图 base64 内联或直接用文件路径**" —— 正是你图表方案（前端渲染 `/plots/` 路径）的做法，值得保留。

**但**：合并后它就是**网关运行时读写的那个文件**（`src/index.ts` 的 `getHermesMemoryDir()` → `<repo>/memory`，4 个 handler 直接拼 `MEMORY.md`）。

**后果**：工作树被常驻改写 → 违反 CLAUDE.md 约定 11"收尾前必须提交干净"，且**下次同步上游必冲突**。

**建议**（二选一，取决于你要不要那份约定留在版本库里）：

- **保留入库**：接受"每次同步上游都冲突"，好处是那份图像规范跟着仓库走；
- **移出索引 + 加 `.gitignore`**：改在部署时手工放置，同步上游不会再冲突。

> 同一族的第三个落点（别漏）：`%LOCALAPPDATA%\hermes`（存在则优先）或 `~/.hermes`，以及 `hermes_runner.py` 里的 `~/.qmclaw_hermes` —— **cron / config 状态会落在仓库外**，备份与交付时容易漏。已登记 BACKLOG G5。

### 十·补.4 顺带核实：`offline_data_lab.py` 被整份重写（非路径问题，但同批）

`+359 / −39`。API 兼容已核实（位置参数与 `do_plot` 仍生效、仓库里没有 `exp_num=` 关键字调用点），**但你的 Tier1/Tier2 出图走的就是这个类** → 出图口径/样式会变，而 `charts=1`、子串检查**全都抓不到**。合并后需**肉眼比对一次图**。已登记 BACKLOG G7。

---

## 十一、独立复核（对抗式审查）—— **已完成，结论已并入本文**

复核由独立复核员完成，**不是复述而是找错**。完整报告：`docs/UPSTREAM_MERGE_PLAN_REVIEW.md`（394 行，23 条【已核实】/ 3 条【推断】）。

**方法（值得记一下）**：沙箱禁写 `.git`、本地 `fetch` 又被 msys `sh.exe` 的 signal-pipe 限制挡住，于是复核员在 `.merge-scratch\probe\mrepo` 里**复制两侧 object DB 重建探针仓库**，跑**真实 `git merge`** 重放 —— 而不是只读文档推断。真实 merge 的冲突集合与本文预判**完全一致**（4 文件 / 6 块），7 个自动合并文件的字节数与预判逐个相同（如 `hermes_service/server.py` 52747 字节），**说明预判工具本身可信，问题出在冲突解法的规则和校验判据上**。

### 复核推翻了初稿的 3 处（都已改正）

| # | 初稿规则 | 实测后果 | 现在的规则 |
|---|----------|----------|-----------|
| 5.2 | `base_service.py` 简单并集 | `@_timed_request("DELETE")` 被粘到 `handle_streaming_request` 上；`do_DELETE` 裸奔。**AST 实测确认** | 顺序敏感：导师方法插到装饰器**之上** |
| 5.4 | `CLAUDE.md` 取 OURS | 结果 = 你 HEAD 原文件，导师新增的整段 `Compaction` 压缩规则**静默消失**（命中 0） | 并集：保留你的约定 10/11 + 追加导师 Compaction 段 |
| 5.1 | 按 `id` 并集 JSON | 导师把 8 个模型 id **整套重编号** → 18 条里 **16 条重复**；且"导师条目在前"使两个面板默认模型从 `deepseek-flash` **翻成 MiniMax M2.7**，推翻 H3 验收 | 按 `(provider, modelId)` 去重；保留我方 id；`deepseek-flash` 置首 |

**这三处初稿自带的校验器全部报了 PASS** —— 因为它只断言"子串存在"（`ast.parse` 通过 + 3 个字符串在），不断言语义目标。**这是本次最有价值的教训：语法校验抓不到"装饰器挂错函数"这类错误。**

### 其余已并入的修正

- **第六节（执行步骤）**：第 0 步必须**一并提交全部已修改的已跟踪文件**（写方案时是 4 个；漏任何一个第 0.1 步断言都会自锁）；网络姿势改为逐命令 `-c` 传参，不落盘 `sslVerify=false`；`<解完的文件>` 改成 4 条带引号的真实路径；补 `npm install`、删 `tsbuildinfo`；插入**显式人机闸门**
- 第七节：第 1 项判据换成 `'^(<{7}|={7}$|>{7}|\|{7})'`（原命令在 `-E` 下 `\|` 是字面竖线，**实测 0 行 = 假绿**），并写成**两阶段**：解冲突前应命中 18 行、解完 `git add` 后应 0 行；第 2/3/4/6/8 项升级为语义断言；清单从 15 项扩到 **23 项**（补 `npm install`、解释器写死为约定 10、import 冒烟、`HERMES_HOME`、`DEFAULT_HERMES_MODEL` 必须不存在、重命名引用扫描、`tsbuildinfo` 出库、改动面核对、状态文件入库、技能目录修好）
- 第三节：补上 3.3 —— **8 个既不重叠也容易被漏掉的上游改动**（含 `offline_data_lab.py` 整份重写）
- 第八节：补前端 4 个新依赖、`offline_data_lab.py` 行为变化、`HERMES_HOME` 语义冲突、`memory/MEMORY.md` 被运行时改写、`tsbuildinfo`、同屏两条模型加载路径
- 第十节：记忆不是"两套"而是**三处**，skills 也是两边各一套

### 复核的负面结论（同样重要）

- 5.3 第 1、3 处判断**正确**，取 OURS 无反例；
- `src/index.ts` **没有**撞出重复路由或重复顶层声明（上游新增 20 个路由、改我方 0 个）；
- 新工具模块硬编码的 `localhost:3004` / `3008` 与本机 `config/services.json` 一致；
- `offline_data_lab.py` **没有**会炸的调用点（位置参数与 `do_plot` 仍生效，无 `exp_num=` 关键字调用）。

> 复核末尾列的 16 条"必须在执行前修正"（P0 八条 / P1 八条）**已全部并入本文第四、五、七、八节**。逐条对照见复核报告第 370-392 行。

---

## 十二、本方案自身的验证记录

方案里的每条事实主张都不是"看着像"，而是跑出来的。**可复现材料都留在工作区里**（已被 `.gitignore` 忽略，不会误提交）：

> 📌 **脚本另有一份正式归档**：`docs/merge_verify_tools/`（含说明 `README.md` 与校验报告 `ARCHIVE_CHECK.md`）。
> 因为 `.merge-scratch/` 会在第六节第 9 步被清理，归档那份才是长期可复现的入口。
> **已核实**：6 个脚本逐字节搬运（SHA256 全 64 位相等、字节数相等），且**归档版跑起来与源版结果一致**
> —— 用归档版 `resolve-conflicts.py` 对仍处于未解冲突状态的探针仓库试算，4 个文件全 PASS 并正确报出
> `.gitignore` 预期外冲突，说明脚本**不依赖所在目录**。
> 下面表格里的路径是分析当时的原始位置。

### 12.1 用到的脚本与结果

| 脚本 | 作用 | 结果 |
|------|------|------|
| `.merge-scratch\tw3.py` | 逐 blob 三方合并预判（每个 blob 都校验与 `git cat-file -s` 报的字节数一致，杜绝编码/截断造成的假冲突） | 11 个重叠文件：7 CLEAN / 4 CONFLICT / 6 处冲突块，0 输入缺失 |
| `.merge-scratch\resolve.py` | **初稿版本**：按逐块规则解 6 处冲突，校验 JSON 解析 / `ast.parse` / 花括号平衡 / 关键符号**存在** | 4 个文件"全部 PASS" —— **但这个 PASS 是假的**（见下） |
| `.merge-scratch\resolve-fixed.py` | **修正版**：落实复核 P0 的 6 条；校验升级为**语义断言**（装饰器 AST 绑定目标、`(provider,modelId)` 去重、默认模型首条、`DEFAULT_HERMES_MODEL` 必须不存在、`Compaction` 必须存在） | 4 个文件全部 PASS（真） |
| `.merge-scratch\verify-clean-merges.py` | 内容级完整性：7 个"自动合并"文件里，双方各自特有的里程碑字符串是否都还在 | PASS —— 没有任何一方功能被静默丢掉 |
| `.merge-scratch\verify-doc-claims.py` | 把本文档里的事实主张逐条对着 git 数据复核 | 32 条全部 PASS |
| `.merge-scratch\show-conflicts.ps1` | 打印 6 处冲突块的双方内容 | 用于第五节逐条定规则 |
| `.merge-scratch\probe-sync.ps1` | **端到端实测**：复制两侧 object DB 建探针仓库，跑**真实 `git merge`** | 真实冲突集合 = **4 文件 / 6 块**，与预判完全一致（1/1/3/1），零误报 |
| `.merge-scratch\probe-resolve.py` | 在**真实 merge 的冲突标记上**应用第五节修正版规则并暂存 | 4 个文件解完，残留标记 0 |
| `.merge-scratch\probe-verify.py` | 在合并后的真实工作树上跑第七节可自动化项 | **13 PASS / 0 FAIL / 4 SKIP / 2 WARN / 2 INFO** |
| `.merge-scratch\gi3way.py` | 定论 `.gitignore` 三方合并是 CLEAN 还是 CONFLICT | HEAD 版 → CLEAN；带末尾 3 条 → CONFLICT 1 处（见 6.0） |
| **`.merge-scratch\resolve-conflicts.py`** | **可直接使用的解冲突脚本**（第五节 4 条规则的实现，带语义校验；默认试算，`--write` 才写回） | 在真实冲突仓库上实测：4 个文件全 PASS、0 未解决 |
| **`docs\merge_verify_tools\pipeline-test.ps1`** | **一条命令跑完整流程**（建探针 → 真实 merge → 解冲突 → 提交 → 后处理 8/19/21 → 验证），是原 `e2e-run.ps1` + `e2e-post.ps1` 的参数化归档版 | **已实测**：`merge exit=1`（4 冲突 / 6 块）→ `resolve exit=0`（全 PASS、0 未解决）→ 3 个后处理提交 → `verify exit=0`（**13 PASS / 0 FAIL**） |

### 12.2 端到端实测结果（第七节自动化项）

在**真实 merge + 修正版解冲突**后的工作树上，逐项结果：

| # | 项 | 结果 |
|---|----|------|
| 1 | 无残留冲突标记 | 解冲突**后** PASS（0 行）；解冲突**前**该正则应命中 18 行（两阶段判据见第七节第 1 项） |
| 2 | 模型配置语义 | PASS（10 条 / 无 `(provider,modelId)` 重复 / 首条 `deepseek-flash`） |
| 3 | Python 语法（3.11.16） | PASS（`compileall` services + scripts） |
| 4 | 装饰器语义绑定 | PASS（`_timed_request('DELETE') → do_DELETE`，SSE 方法在位） |
| 5 | 稳定性/离线改造标志 | PASS（`X_UNIT_SCALE` / `PLOT_LOCK` / `_INDEX_LOCK` / `ThreadingHTTPServer`） |
| 6 | 新模块存在+被引用 | PASS（`server.py` 引用 5/5，5 个新文件都在） |
| 12 | `DEFAULT_HERMES_MODEL` 不存在 | PASS（全 `src` 零命中） |
| 14 | `.gitignore` 规则齐备 | PASS（双方规则都在） |
| 15 | 新模块依赖可导入 | PASS（4 个包本机都已装） |
| 16 | `vendor`/`.gitmodules` 无差异 | PASS |
| 18 | 重命名旧引用扫描 | PASS（未发现指向根路径的旧引用） |
| 19 | `tsbuildinfo` 出库 | PASS（`git rm --cached` 后不再跟踪） |
| 20 | 改动面核对 | PASS（暂存文件全部落在"导师 41 ∪ 我方 62"内） |
| 13 | 记忆路径盘点 | **INFO**（脚本只记录不判失败：三处路径 + 网关侧 `memory/MEMORY.md` 已进树） |
| 17 | `qca_service` 前提 | **INFO**（vendor 目录在、`server.py` 缺 vendor 会 `raise RuntimeError`；实际起服务由人做） |
| 7/8/9/10 | 前端构建 / 接口 / 端到端 / hermes 对话 | **SKIP** —— 需 `npm install`（联网）与人工启停服务（约定 3） |
| 11/21 | `HERMES_HOME` 语义 / 状态文件入库 | **WARN** —— 需你决策，见第八节 |
| 22/23 | `MEMORY.md` 去留 / 技能目录修好 | **未跑** —— 本轮新增的检查项，归档脚本尚未覆盖 |

**合计 21 项**（13 PASS + 4 SKIP + 2 WARN + 2 INFO）；清单共 23 项，第 22/23 项是本轮新增、归档脚本还没覆盖，需人工按第十·补 判据检查。

> **结论：静态可自动化项全绿。** 剩余必须由人做的是：第 7 项（联网 `npm install` + 前端构建）、
> 第 8/9/10/11/17/23 项（起服务验证）、第 22 项（决定 `memory/MEMORY.md` 去留）。

### 12.3 完整流水线贯通测试（最有说服力的一条）

前面 12.2 是"对某次合并结果做验证"；这一条是**把第六节整段流程跑通**：

```
建探针仓库（真实两侧 object DB）
  → 真实 git merge            → 冲突 4 个文件 / 6 块（1/1/3/1）
  → resolve-conflicts.py --write → 4 个文件全 PASS、0 未解决冲突
  → git commit                 → 877a641，工作树干净
  → 后处理三步（加忽略规则 / rm --cached tsbuildinfo / rm --cached auth.lock）
  → 最终独立验证                → 13 PASS / 0 FAIL / 4 SKIP / 2 WARN
```

**关键结论**：这条路径**已经端到端验证过**，所以第六节不再是"看起来该这样"的命令序列，而是**跑通过的流程**。脚本位置与用途见 12.1。

> 两个"原本会 FAIL、补上后处理就转 PASS"的项，正好证明验证清单确实在起作用：
> `#14 .gitignore 规则齐备`（第 8 步补规则后转 PASS）、
> `#19 tsbuildinfo 出库`（`git rm --cached` 后转 PASS）。
> 也就是说：**如果你跳过第 8/19 步，验证清单会当场报出来**。

### 12.4 三处处置建议的验证

第十·补 给的三条处置不是凭空写的，每条都实测过：

| 结论 | 怎么验的 |
|------|----------|
| 技能列表会空 | 逐层追到 `skills.py:119 → _get_data_dir() → skills.py:63 data/skills → :137 rglob("SKILL.md")`；实测 `<qmclaw-server>/data/skills` **不存在**；`git ls-tree main -- .../hermes_service/data` **为空**（上游自己也没带技能） |
| **改 `HERMES_HOME` 修不了它** | `git grep HERMES_HOME main` 全部命中只有：`hermes_runner.py:136-152`、`hermes_service/server.py:173-186`（取值+一行日志）、`.env.example:28`、`start_all_services.py:157` —— **`skills.py` 零命中** |
| `mklink /J` 可行且 `rglob` 能穿过 | 在临时目录实测：`mklink /J` 成功；Python `pathlib.Path(...).rglob('SKILL.md')` 通过 junction 扫到 `skillA\SKILL.md` |
| `auth.lock` 是运行时残留 | 上游 blob = `b' '`（1 字节空格）；你本机 `data\hermes\auth.lock` = **同为 1 字节空格**；`git grep 'auth\.lock' main` **为空**（零引用） |
| `MEMORY.md` 内容与你的方案一致 | 上游那份 4 行：含 `/plots/`、含 `URL format`、明确写"不要 base64 内联或直接文件路径" |

### 12.5 过程中被纠正的错误（含我自己犯的）

**机器复核抓出来的两处**：

1. 初稿写"导师这两个提交动过 `.gitmodules`" —— 实测**没动**，已改成"gitlink 不变，合并不会碰 `vendor/`"；
2. 初稿写"`.gitignore` 新增项完全无害" —— 实测仓库里有 1 个被跟踪的 `qca_service/data/index.db` 会被 `*.db*` 命中（当前不影响，但是条埋雷），已改。

**独立复核推翻的三处冲突解法**：见第十一节（装饰器错挂 / 丢 Compaction 段 / 模型配置 16 条重复）。

**我自己在本轮实测里又抓出四处**：

1. 内容级验证的探针曾误报 `plot_lock`、`cron` "丢失" —— 实际是探针写错（真符号是 `serialized_plot`；`cron` 在该文件里从来不存在）；
2. 初稿说"`.gitignore` 加规则后重测仍自动合并" —— **测错了输入**（测的是 HEAD 版而不是工作树版）。实测：带末尾 3 条会多出 1 处冲突，已写成 6.0；
3. 初稿的第 0 步还原命令 `git show HEAD:.gitignore | Set-Content -Encoding utf8 -NoNewline` **跑不通** —— 实测产 561 字节、带 BOM、`git diff` 仍报差异。已改成字节级的 `cmd /c "git cat-file blob HEAD:.gitignore > .gitignore"` 并验证 `git diff --quiet` 返回 0；
4. **第 9 步会删掉 `.upstream-mirror/`，而 7 个归档脚本里有 5 个依赖它** —— 那会把刚归档的验证工具变成废物。已改为只删 `.merge-scratch` + `.analysis`，镜像长期保留（它是可复用的本地缓存，31.6 MB）。

**还有一次是"修正本身引入新问题"，值得单独记**：

我为了消除"200+ MB 分析材料在合并期间暴露为未跟踪"的风险，曾把第 0 步改成
「还原 → 提交 → **加回 3 条规则** → 再合并」。
**但那是错的** —— `.gitignore` 一旦有未提交改动，第 4 步 merge 就会 `Aborting`，
而这条恰恰是我自己在 0.1 断言里写下要防的事。**修正反而打破了原本成立的约束。**

正确做法（现已写入 6.0）：还原后**不再碰 `.gitignore`**，让那 3 条规则缺席到第 8 步；
代价是合并期间分析材料显示为未跟踪，因此 **第 2~6 步禁止 `git add -A` / `git add .`**，只按路径精确 `add`。

> 教训：改一个步骤前，先检查它是否依赖其他步骤已经建立的**约束**（"工作树干净"就是一个跨步骤约束）。

### 12.6 最该记住的一条教训

初稿的 `resolve.py` 对 4 个文件全报 PASS，其中**有 3 处实质错误**：

| 校验器查了什么 | 为什么不够 |
|----------------|-----------|
| `ast.parse` 通过 | `@_timed_request("DELETE")` 挂在 `handle_streaming_request` 上**完全合法**，能解析、能运行，只是语义错了 |
| 关键字符串"存在" | `SLOW `、`_timed_request`、`handle_streaming_request` 三个子串在错挂方案里**一个不少** |
| JSON 能解析 + id 不重复 | 18 条里 16 条是同一模型的重复条目，只因 id 不同而躲过判据 |
| `Compaction` 没查 | 初稿"取 OURS"把导师整段规则删掉，而校验器里根本没有这条断言 |

**结论：验证必须是"断言语义目标"，不是"检查字符串在场"。** 这也是为什么第七节的第 2/3/4/6/8/12 项现在写的是 AST 绑定、`(provider,modelId)` 去重、默认模型首条、标识符必须**不存在**这类判据。

---

## 十三、一句话（修正版）

导师这 2 个提交在 git 层面确实只是一个 merge 的量级 —— **4 个文件 / 6 处冲突，7 个重叠文件自动合并，你的 36 个提交 hash 与回滚手册全部不受影响**。用 merge 而不是 rebase 这个判断成立。

**但"成本很低"要加限定**：6 处冲突里有 3 处是**顺序/语义敏感**的，按直觉解（简单并集、"取你的"、按 id 合 JSON）会分别导致 ①你的 DELETE 路由失去计时、②导师的压缩规则被整段删掉、③模型配置翻倍且默认模型被翻转 —— **而这三处语法校验全都查不出来**。所以正确说法是：

> **冲突体量小，但不是"随便解解就行"：按第五节修正版规则解那 6 处，再补 `npm install`、`HERMES_HOME` 决策、运行时状态文件入库决定，然后才是一个合并提交能收尾的量级。**

合完之后真正值得跟导师坐下来谈的不是代码怎么合，而是**记忆与技能到底放哪儿**（第十节）—— 记忆有**三处**路径（连导师自己的 python 侧与网关侧都不互通）、skills **两边各做一套**，那是真正需要定"权威位置"的地方。

---

## 十四、下次上游再更新时怎么做（这套流程是**可重复**的）

本次融合的具体数字（`03d838d`、2 个提交、41 个文件）会过期，但**方法不会**。下次导师推了新提交，照下面走。

### 14.1 刷新上游镜像

```powershell
# .upstream-mirror 是裸镜像，长期保留在本地（已被 .gitignore 忽略）
# 它的 origin 已经指向 gh-proxy，所以**必须**带 openssl 参数，否则报 schannel SEC_E_NO_CREDENTIALS
git -C .upstream-mirror -c http.sslBackend=openssl -c http.sslVerify=false `
    fetch --prune origin "+refs/heads/*:refs/heads/*"
git -C .upstream-mirror log --oneline -5 main
```

> 已实测：`fetch exit=0`，`main` 仍是 `03d838d`（刷新不会改坏本地镜像）。
> **不要删 `.upstream-mirror/`** —— `docs/merge_verify_tools/` 里 5 个脚本依赖它。

### 14.2 先看变化面，再决定要不要合

```powershell
$m = '.upstream-mirror'
# 上次融合到哪儿了？（本次是 03d838d）
git -C $m log --oneline 03d838d..main
# 新提交改了哪些文件
git -C $m diff --name-only 03d838d main
# 你这边改过哪些文件（从 fork 点算起）
git diff --name-only f2d83b5 HEAD
```

把两个清单求交集 —— 这就是**新的重叠面**，也就是下一轮要处理的冲突范围。

### 14.3 三种情况

| 新提交情况 | 怎么做 |
|------------|--------|
| **没有新提交**（`03d838d..main` 为空） | 什么都不用做 |
| **新提交与你改过的文件零重叠** | 直接 `git merge`，应零冲突；跑一遍 14.4 的验证 |
| **有新重叠** | 先跑 `tw3.py` 预判重叠文件，再走第六节流程；若出现新的冲突文件，`resolve-conflicts.py` 会明确报"预期之外的冲突文件"，那时按第五节的方法**逐块定规则**（并把这个新规则的依据补进第五节） |

### 14.4 每次同步后都要跑的验证

```powershell
# 一条命令：建探针 → 真实 merge → 试算解冲突 → 提交 → 后处理 → 验证
.\docs\merge_verify_tools\pipeline-test.ps1 -RepoRoot D:\semester4\QMClaw
```

> ⚠️ `pipeline-test.ps1` 顶部 pin 了两个 SHA（`$USER_SHA` / `$UP_SHA`）。
> **换了新上游提交后要同步更新 `$UP_SHA`**，否则测的还是上一次的合并。
> 文档类主张用 `python docs\merge_verify_tools\verify-doc-claims.py` 复核（它会随文档变化而需要更新断言）。

### 14.5 本次的"基线锚点"，供下次比对

| 项 | 值 |
|----|----|
| 你的 fork 点 | `f2d83b5`（tree 等同上游 `c3fd6e6`） |
| 你当时的 HEAD | `af90c1c`（36 个提交 / 62 个文件） |
| 上游当时 main | `03d838d`（相对 fork 点 2 个提交 / 41 个文件） |
| 合并后的重叠面 | 11 个文件（7 自动合并 + 4 冲突 / 6 块） |

**下次把这些值替换成新的即可**，方法完全一样。

