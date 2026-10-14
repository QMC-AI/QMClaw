# 导师上游融合 —— 交付报告（对比改动前）

> **报告日期**：2026-10-08
> **分支**：`feat/c-real-machine`
> **当前 HEAD**：`68340d3`（工作树干净）
> **结论**：上游 2 个提交 / 41 个文件已合入；我方 36 个提交**零改写**保留；4 个文件 / 6 处冲突按预案解掉；
> 自动验证 **12 PASS / 0 FAIL**；11 个服务**全部已启动**并通过健康检查。
> 过程中**发现并修复了 1 处我引入的回归**（默认模型翻转），已提交 `eb65542`，并补上能抓住它的校验（`68340d3`）。

---

## 一、一句话结论

导师那两个提交是一整块**与我不重叠的 Hermes 扩展**（memory / skills / cron / 审批 / 前端渲染组件）+ SSE 基础设施。
真正需要人工判断的只有 **4 个文件里的 6 处冲突**，其中 3 处是"顺序/语义敏感"的陷阱：

| 冲突 | 若按直觉解 | 实测后果 |
|------|-----------|----------|
| `base_service.py` | 简单并集 | `@_timed_request("DELETE")` 粘到导师的 SSE 方法上，`do_DELETE` 裸奔 → 我的 DELETE 计时丢失、每个 SSE 请求被记成 `SLOW DELETE` |
| `CLAUDE.md` | "取我的" | 导师在同一冲突块里新增的整段 `Compaction 压缩规则` 被**整段删掉** |
| `model_configs.json` | 按 `id` 并集 | 导师把 8 个模型 id **整套重编号** → 18 条里 16 条重复，默认模型从 `deepseek-flash` 翻成 MiniMax M2.7 |

**而语法校验（`ast.parse`、字符串存在性）对这 3 处全部报绿** —— 这是整个任务里最值得记录的一条教训。

---

## 二、改动前是什么样（基线）

### 2.1 三个锚点

| 锚点 | 值 | 含义 |
|------|-----|------|
| fork 点 | `f2d83b5`（2026-09-21 14:41） | 与上游分叉处；**它不是上游提交**，是我方仓库里 PR #2 的合并提交 |
| 上游当时 HEAD | `03d838d`（2026-09-30 11:15） | 导师 main |
| 我方合并前 HEAD | `3d646c9`（2026-10-08 20:29） | 36 个改造提交的顶端 |

> **关键坑**：`git merge origin/main` / `git rebase upstream/main` 都是错姿势 —— `origin` 是**我自己的 fork**，那份 `main` 上没有导师一行代码。
> fork 点的真实依据是**内容相同**：上游 `c3fd6e6` 的 tree 与我方 `f2d83b5` 的 tree **哈希完全相同**（已核实）。

### 2.2 改动前的规模

| | 提交数 | 文件数 | 行数 |
|---|---|---|---|
| 我方（相对基线） | **36** | 62 | +7726 / −347 |
| 导师上游（相对基线） | **2**（`ea773be`、`03d838d`） | 41 | — |
| 重叠 | — | **11**（7 自动合并 + 4 冲突） | — |

### 2.3 改动前源代码的 5 类缺陷（我此前的改造针对它们）

来自 `docs/CHANGES_VS_SOURCE.md`，是本次融合需要保住的东西：

1. **agent 工具从不执行** —— ReAct 循环解析文本 `Action:` 协议，但模型输出的是原生 `｜DSML｜` 工具调用标记 → 解析不到就当最终答案返回（`iterations=1`）
2. **`RunExperimentTool` 注册错误** —— executor 把结果 dict 当代码 POST，服务端 500（`unhashable type: 'slice'`）
3. **真实离线数据从未被解析** —— 复合 dtype 未拆列 + 扫描轴单位差 1e9，一律走合成
4. **前端契约字段一个都没返回** —— `steps` / `results` / `charts` 全缺，步骤区和结果卡片永不渲染
5. **memory 功能从未跑通** —— agent_service 没有任何 `/memory/*` 路由，实测 502

---

## 三、本次改了什么（对比表）

### 3.1 从上游合入的内容（41 个文件 = 19 改 + 18 增 + 4 重命名）

**新增 18 个**（导师的 Hermes 扩展，与我零冲突）：

| 领域 | 文件 |
|------|------|
| Hermes 后端（5） | `services/hermes_service/{memory,skills,cron,approval_ws,mcp_toolsets}.py` |
| 量子工具（2） | `services/common/quantum_tools/{analysis_tools,workflow_tools}.py` |
| 前端组件（3） | `components/{HermesExtensionsPanel,HermesSessionsSidebar,HermesToolbar}.tsx` |
| 前端渲染库（3） | `lib/{markdown,reasoning-block,tool-call-card}.tsx` |
| 配置/根文件（5） | `config/skills_config.json`、`AGENTS.md`、`SOUL.md`、`memory/MEMORY.md`、`auth.lock` |

**修改 19 个**，其中 4 个是冲突文件（见第四节），其余 15 个自动合并。

**重命名 4 个**：`sse_verify.py`、`sse_verify_standalone.py`、`test_labrad.py`、`test_load_qubits.py` → 全部 `R100` 移入 `tests/`。

### 3.2 我方改造的保留情况（逐项实测）

| 我方改造 | 融合后 | 证据 |
|---|---|---|
| **B 组稳定性**：多线程服务 / `SLOW` 慢请求日志 / 绘图锁 / 记忆索引锁 | ✅ 全在 | AST 断言：`_timed_request('DELETE')→do_DELETE`、`GET→do_GET`、`POST→do_POST`；`PLOT_LOCK`、`_INDEX_LOCK`、`ThreadingHTTPServer` 均在 |
| **离线链路**：单位换算 / 复合 dtype 拆列 / Tier1-Tier2 出图 | ✅ 标志位全在 | `X_UNIT_SCALE` 等 |
| **memory 8 个端点** | ✅ 可用 | `GET /api/agent/memory/stats` → `{"episodeCount":14,"orphanCount":0,...}` |
| **agent 侧技能接口** | ✅ 可用 | `GET /api/skills` → 返回 `t1_full_characterization` 等 |
| **hermes `/chat` 挂死修复** | ✅ 生效 | `POST /api/hermes/chat` → `completed:true`，回复 `OK`（融合前曾 >300s 挂死） |
| **模型配置默认值（H3 验收）** | ⚠️→✅ **曾被我改坏，已修** | 见第五节 |
| **前端 `useModelStore` 改造** | ✅ 保留 | `DEFAULT_HERMES_MODEL` 全 `src` 零命中 |

### 3.3 新增的工具与文档（本次会话产出，56 个新增条目）

| 类别 | 内容 |
|------|------|
| **方案** | `docs/UPSTREAM_MERGE_PLAN.md`（14 节） |
| **独立复核** | `docs/UPSTREAM_MERGE_PLAN_REVIEW.md`（394 行） |
| **逐字校对** | `docs/UPSTREAM_MERGE_PLAN_PROOFREAD.md`（16 处问题） |
| **验证工具** | `docs/merge_verify_tools/`：13 个脚本 + 3 份说明（`README.md` / `ARCHIVE_CHECK.md` / `MERGED_CONTENT_PREVIEW.md`） |
| **一键执行** | `docs/merge_verify_tools/run-merge.ps1`（真实仓库第 0~9 步） |
| **待办登记** | `docs/BACKLOG.md` 新增 **G 组 10 条** |
| **约定更新** | `CLAUDE.md` 入口 + `.claude/progress.md` 两条记录 |

---

## 四、4 个冲突文件 / 6 处冲突的最终解法

### 4.1 `services/base/base_service.py` —— 顺序敏感（1 处）

**险点**：两边改动落在**同一锚点**。文本并集（ours 在前）会把装饰器粘到 SSE 方法上。

| | 内容 |
|---|---|
| 我方 | `@_timed_request("DELETE")`（DELETE 路由的慢请求计时） |
| 导师 | `handle_streaming_request()` 34 行（SSE） |

**解法**：导师方法插在装饰器**之上**。**最终 AST 实测**：
```
_timed_request('DELETE') → ['do_DELETE']   ✅ 装饰器回到正确函数
导师 handle_streaming_request 在位          ✅
```

### 4.2 `CLAUDE.md` —— 并集（1 处）

**险点**：导师在同一冲突块里加了**整段** `# Compaction 压缩规则`。"取我的"会把它删光。

**解法**：保留我方约定 10/11 + 追加导师整段，只丢导师那侧重复的 python 路径行。
**最终**：`Compaction` 段在位 ✅，我方约定 10（本机路径）在位 ✅。

### 4.3 `config/model_configs.json` —— 按逻辑模型去重（1 处）

**险点**：导师把 8 个模型 id **整套重编号**（`model_default_00N`/`model_1787551340750_N` → `model_1790146722274_0..7`），
id 交集只有 `model_default_006`。按 `id` 并集会得到 **18 条里 16 条重复**。

**解法**：按 `(provider, modelId)` 语义去重 —— 同名模型**保留我方 id**，只采纳导师字段改动。

**最终 10 条**（我方 id 全部原样保留）：

| # | id | modelId | 来源 |
|---|---|---|---|
| 1 | `deepseek-flash` | `deepseek-flash` | 我方独有 |
| 2 | `model_default_006` | `MiniMax-M2.7` | 我方 id / 导师 `baseUrl` |
| 3–7 | `model_default_001..005` | gpt-4o … deepseek-chat | 我方 id |
| 8–10 | `model_1787551340750_0..2` | MiniMax Text-01 / ABAB | 我方 id |

### 4.4 `HermesChatPanel.tsx` —— 3 处，规则不同

| # | 内容 | 解法 | 最终 |
|---|------|------|------|
| 1 | 我方 `useModelStore` import ↔ 导师 3 个 lib import + 2 个工具函数 | 并集 | 两者都在 ✅ |
| 2 | 我方删掉硬编码 `DEFAULT_HERMES_MODEL` ↔ 导师加 `interface ApprovalRequest` | 留接口、**丢**那行常量 | `DEFAULT_HERMES_MODEL` 零命中 ✅ |
| 3 | 同一 `useEffect` 的两种实现 | **取我方** | 花括号平衡 423/423 ✅ |

> 第 3 处必须选边：并集会产生 `}, [textModels, model]);` + `}, []);` **双收尾 → 语法坏**（实测失衡 427/428）。

---

## 五、★ 我引入的 1 处回归（已发现并修复）

这是本次唯一一次**实际造成功能退化**的改动，值得单独记录。

### 现象

服务起好后实测 `/api/models`，**首条不是 `deepseek-flash`，而是 `model_default_002`（GPT-4o Mini）** —— 推翻 `docs/TODO_MEMORY_HERMES.md` 的 H3 验收（"两个面板默认都选 deepseek-flash"）。

### 根因（两层）

1. **排序不是我控制的**：`src/services/modelService.ts:204` 的 `listModels()` **按 `updatedAt` 倒序排序**：
   ```ts
   return [...config.models].sort((a,b) => new Date(b.updatedAt) - new Date(a.updatedAt));
   ```
   所以"把 `deepseek-flash` 放在 JSON 首位"**根本不起作用**。
2. **我在 4.3 的解法里把它踩了**：对同名逻辑模型"采纳导师字段"时，**连 `createdAt`/`updatedAt` 也一起采纳了** —— 而导师那批是 `2026-09-23`，比我方的 `2024-01-01`/`2026-09-21` 新 → 排序被整体翻转，`deepseek-flash` 从第 1 掉到**第 9**。

实测差异（同名模型）：

```
gpt-4o / gpt-4o-mini / gpt-4-turbo / claude-3-5-sonnet / deepseek-chat /
MiniMax-Text-01 / abab6.5s-chat / abab6-chat  → 差异字段只有 createdAt, updatedAt
MiniMax-M2.7                                  → 差异字段只有 baseUrl（实质改动，要留）
```

**导师对同名模型只改了时间戳**；唯一实质改动是 `MiniMax-M2.7` 的 `baseUrl → .../anthropic`。

### 修法（提交 `eb65542`）

对「两边都有的同一逻辑模型」**保留我方 `createdAt`/`updatedAt`**（纯元数据），只采纳导师的实质字段。
diff 复核确认**只动了时间戳**（16 行），`baseUrl` 的导师改动保留。

**修复实测**：

```
/api/models        首条 = deepseek-flash ✅（共 10 条）
/api/hermes/models 首条 = deepseek-flash ✅（共 10 条）
```

### 为什么我的校验器没抓到（提交 `68340d3` 补上）

原第 2 项判据只看**文件里**第一条 `enabled+text`，而 handler 取的是**排序后**的第一条 —— 盲区。
已改为两项都打印、以排序后为准。并用**反向测试**证明新判据有效：

```
A) 修复后（时间戳=我方值）              → PASS  文件首条=deepseek-flash | 排序后首条=deepseek-flash
B) 回归态（时间戳=导师那批 2026-09-23） → FAIL  文件首条=deepseek-flash | 排序后首条=model_default_006
```

注意 B 行：**回归态下"文件首条"仍是 `deepseek-flash`** —— 这正是旧判据放它过去的原因。

---

## 六、服务验收（已全部启动）

用 `scripts/start_all_services.py --all`（后台作业）+ `npm run dev` 启动。

### 6.1 服务状态（实时）

| 端口 | 服务 | 状态 |
|------|------|------|
| 3002 | Express 网关 | UP（`{"express":"ok","mode":"microservices"}`） |
| 3003 | quantum 测控 | UP |
| 3004 | analysis 分析 | UP |
| 3005 | agent | UP |
| 3006 | llm | UP |
| 3007 | image | UP |
| 3008 | workflow | UP |
| 3009 | task_queue | UP |
| 3010 | qubitclient | UP |
| 3011 | qca | UP |
| 3012 | hermes | UP |
| 8081 | Next.js 前端 | HTTP 200（19182 bytes，标题 `qmclaw - Quantum Measurement & Calibration Workflow`） |

> 说明：`start_all_services.py` 配置里启用了 9 个微服务（`qubitclient` 未在 `config/services.json` 中），
> 但实际 3010 也在监听并健康 —— 10 个微服务全部 200。

### 6.2 关键接口验收

| 接口 | 结果 |
|------|------|
| `GET /api/models` | 200，10 条，**首条 `deepseek-flash`** ✅ |
| `GET /api/hermes/models` | 200，与上面一致 ✅ |
| `GET /api/hermes/skills` | 200，**非空**，返回真实技能（`path` 指向仓库 `skills\...\SKILL.md`）✅ |
| `GET /api/agent/memory/stats` | 200，`episodeCount:14`、`orphanCount:0` ✅（我方 memory 功能可用） |
| `POST /api/agent/memory/episodes` | 200，返回 3 条 episode ✅ |
| `GET /api/skills` | 200，返回我方技能 ✅ |
| `POST /api/hermes/chat` | 200，`completed:true`，回复 `OK` ✅（挂死修复仍生效） |
| `GET /api/services/health` | 200，微服务 `reachable:true` |

### 6.3 前置修复：前端 4 个新依赖

合并带进来 4 个前端依赖但没装，**不装则前端必然起不来**：

```
highlight.js / react-markdown / rehype-highlight / remark-gfm   → 装前全部 MISSING
npm install → added 105 packages → 装后全部 OK（package-lock.json 未被改动）
```

---

## 七、验收证据汇总

| 检查 | 结果 |
|------|------|
| 真实 `git merge` 冲突集合 | **4 文件 / 6 块（1/1/3/1）**，与预案**逐项一致** |
| 合并改动面 | `41 files changed, +12400 −357`，与执行前预览**逐字吻合** |
| 我方 36 个提交 | **零改写**（`git merge-base --is-ancestor 3d646c9 HEAD` 为真） |
| 自动验证（合并后真实仓库） | **12 PASS / 0 FAIL**（4 SKIP 需人起服务、3 WARN 为待决/误报） |
| **前端构建** | **`✓ Compiled successfully`**，类型检查通过，4 个静态页生成（验证清单第 7 项） |
| 方案 32 条事实主张机器复核 | **0 失败** |
| 交付报告自身 27 条事实 | **0 失败**（见 `.merge-scratch/verify-delivery-report.py`） |
| 独立对抗式复核 | 找出初稿 **3 处实质错误**，已全部纠正 |
| 逐字校对 | **13 处必改 + 3 处建议改**，复验 FAIL=0 |
| 归档脚本哈希 | 13 个脚本**逐字节一致** |
| 密钥扫描（推送前） | 索引中**无密钥** |
| 服务健康 | 11 个服务全部 UP |

### 已核销的两条误报

- 校验清单 `#18` 报的 8 处"旧引用"（`test_load_qubits.py` 等）**全在** `docs/CHANGES_VS_SOURCE.md`、`GIT_HISTORY.md`、`TODO_RUNTIME.md` 的**历史日志**里，不是活引用。
- `/api/agent/memory/episodes` 首次 GET 返回 404 —— 该端点**设计为 POST**，用 POST 返回 200。

---

## 八、提交清单与回退

### 8.1 本次会话的提交（在 `3d646c9` 之后，共 9 个）

| 提交 | 内容 |
|------|------|
| `3d646c9` | 第 0 步：提交 5 个已修改文件（`.gitignore` 先还原成 HEAD 版） |
| `bf725e9` | **合并提交**（父1 = 我方 `3d646c9`，父2 = 上游 `03d838d`） |
| `547a0d0` | 停止跟踪构建产物 `tsconfig.tsbuildinfo` |
| `7e09c8e` | 停止跟踪运行时残留 `auth.lock` |
| `72cf997` | 忽略上游融合临时目录 |
| `bdafd81` | 融入方案 / 复核 / 校对报告 + 13 个验证脚本 |
| `e8aaa85` | progress 记录融合执行完成 |
| `ee5262c` | CLAUDE.md 入口改为"已合并完成" |
| `eb65542` | **修复默认模型回归**（还原时间戳） |
| `68340d3` | **升级第 2 项判据**（按 `updatedAt` 倒序判定） |

### 8.2 回退点

```powershell
# 回到合并前（我方改造完整、未含上游）
git reset --hard backup/pre-upstream-merge-20261008-202936    # = 3d646c9
```

另有两个更早的备份分支（`...-202918`、`...-203008`），为执行过程中脚本自动创建。

---

## 九、3 项决策的处置

| # | 决策 | 处置 | 依据 |
|---|------|------|------|
| 1 | 技能目录修法 | **已建目录联接** `qmclaw-server\data\skills` ⇄ `<repo>\skills` | 实测 `rglob('SKILL.md')` 扫到**全部 7 个技能**；`/api/hermes/skills` 已返回非空。**注意**：`skills.py` **不读** `HERMES_HOME`，所以"改 `HERMES_HOME`"修不了它；且 `data/` 在 `.gitignore` 内，联接不入库、换机需重建 |
| 2 | `auth.lock` / `memory/MEMORY.md` 去留 | `auth.lock` **已出库 + 精确忽略**（用 `auth.lock` 而非 `*.lock`，后者会误伤 `package-lock.json`）；`MEMORY.md` **保留入库** | `auth.lock` 是 1 字节空格的运行时残留、全上游零引用；`MEMORY.md` 内容是与图表方案一致的 `/plots/` URL 规范 |
| 3 | `.env` 的 `HERMES_HOME` | **保持现状** | 该变量只影响 Hermes agent 自身；与技能列表无关 |

---

## 十、仍待人工完成（约定 3：服务启停由人控制）

服务已由你授权启动，以下验收项**需要你在浏览器/终端确认**：

| 项 | 判据 |
|---|------|
| 前端页面 | 打开 http://127.0.0.1:8081 ，确认 Tab 切换、Agent/Hermes 面板正常 |
| 第 8/10 项 | 面板里模型下拉首个应为 `deepseek-flash`；hermes 对话正常；走一次 approval / cron |
| 第 9 项 | 端到端 `对 q10lu8 运行 t1`（真实数据）与 `对 q10lu12 运行 t1`（合成降级），**并与合并前的图肉眼比对** —— `offline_data_lab.py` 被上游**整份重写**（+359/−39），`charts=1` 抓不到出图口径变化 |
| 第 11/23 项 | 页面里 Hermes 技能管理应能看到那 **7 个技能**（接口已验非空） |
| 推送 | 尚未 `git push`（按约定 11，推送前需你确认） |

### 后续 10 条收尾待办

已登记在 `docs/BACKLOG.md` **G 组**：G1 记忆三处收敛 · G2 skills 修法 · G3 `memory_store` 双份 · G4 新模块依赖未声明
（`apscheduler`/`tzlocal`/`portalocker`/`websockets` 上游与我都未声明，换机必炸）· G5 状态文件入库 · G6 前端 4 依赖 ·
G7 `offline_data_lab` 出图回归 · G8 `qca_service/core` 死代码 · G9 `tsbuildinfo` · G10 SSE 已就位。

---

## 十一、三条值得记住的教训

1. **`ast.parse` 通过 ≠ 代码对。** `@_timed_request("DELETE")` 挂到 SSE 方法上完全合法、能解析、能运行，只是语义错了 —— 语法校验查不出来。验证必须**断言语义目标**（装饰器的 AST 绑定对象、`(provider,modelId)` 去重、标识符必须**不存在**）。
2. **没跑过的分支等于没测过。** `run-merge.ps1` 的 `-DryRun` 每次从干净状态起步，所以第 0 步"工作树脏"那条分支一直没被走到；专门造脏仓库去测，才发现 `git add <路径>` 会被 `.gitignore` 里的 `.claude/` 拒绝。
3. **别写死"当前状态"。** 我三次把"已修改文件清单"写进文档，三次因为它随工作内容变化而过期（漏 `BACKLOG.md`、漏 `progress.md`、漏 `CLAUDE.md`）。最终改成从 `git status` 动态推导（`git add -u`）+ 文档里明确"不要照抄任何固定清单"。
