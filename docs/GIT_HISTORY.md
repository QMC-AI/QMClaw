# 提交历史与回滚手册

> **目的**：能查到「某个改动在哪个提交里」，并能安全地回滚它。
> **配套文档**：`docs/BACKLOG.md`（待办总表）、`docs/CHANGES_VS_SOURCE.md`（改动与能力说明）。

---

## 一、当前状态

| 项 | 值 |
|---|---|
| 分支 | `main` |
| HEAD | `67da04d` |
| 提交总数 | 36 |
| 远程 | `origin` = `https://gh-proxy.org/https://github.com/hzz-wdb/QMClaw.git` |
| **`origin/main`** | **`f2d83b5`** —— 即**本地领先远程 23 个提交，尚未推送**<br>（该数字随每次提交增长，查当前值：`git rev-list --count origin/main..HEAD`） |
| 本机改动规模 | `f2d83b5..HEAD` = **46 个文件，+5668 / −291** |
| 工作树 | 干净 |

---

## 二、回滚前必读的四件事

### 1. 还没推送 → `reset` 仍然安全

`origin/main` 停在 `f2d83b5`，本地多出的 23 个提交**只有本机有**。所以现在可以用 `git reset --hard` 重写历史，不会影响任何人。

**一旦执行了 `git push`，就不要再 `reset`** —— 那会造成远程/本地分叉，只能改用 `git revert`。

### 2. 有四类东西不在 git 里，回滚**不会**恢复它们

已用 `git check-ignore` 逐个核实：

| 路径 | 内容 | 影响 |
|---|---|---|
| `Agentic Workflow/qmclaw-server/data/offline_data/` | **5008 个离线数据集**（112 个 T1、111 个 qubit） | 丢了就没有任何实验数据可跑 |
| `Agentic Workflow/qmclaw-server/config/memory/` | 长期记忆（episodes / skills / index.json） | 丢了对话历史与技能 |
| `Agentic Workflow/qmclaw-server/.env` | `DEEPSEEK_API_KEY` 等 | 丢了所有服务起不来 |
| `Agentic Workflow/qmclaw-web/public/plots/` | 生成的图 | 只是缓存，可再生成 |
| `Agentic Workflow/qmclaw-server/config/qubitclient.json` | qubitclient 配置 | 该服务降级 |

**回滚代码不会碰这些**；反过来，**误删这些也无法用 git 找回**。迁移机器时必须手工拷贝。

### 3. `vendor/` 是「悬空子模块」，回滚也恢复不了内容

```
$ git ls-files -s vendor
160000 d8d37bc ... vendor/Quantum-Calibration-Agent-Blueprint
160000 3da70fe ... vendor/QubitClient
160000 c4a5dee ... vendor/hermes-agent
```

`.gitmodules` 声明了三个子模块（都指向 `github.com/Secsix/*`），但磁盘上子模块的 `.git` 已被移除，**现在的 vendor 内容是本机未跟踪的普通文件**：

| 子模块 | 磁盘文件数 |
|---|---|
| `vendor/hermes-agent` | 12549 |
| `vendor/QubitClient` | 785 |
| `vendor/Quantum-Calibration-Agent-Blueprint` | 322 |

**换句话说**：`git clone` 或 `reset` 都拿不回 vendor 内容。要恢复只能 `git submodule update --init`（需要能访问 GitHub，本机走的是 gh-proxy）或从 `D:\semester4\deploy\vendor` 再拷一份。

### 4. 代码回滚后必须重启受影响的服务

Python 服务不热重载，**改了文件不重启等于没改**（详见第七节）。

---

## 三、本机 23 个提交（新 → 旧）

`⚠️` = 不建议单独回滚（会破坏后续提交或退回已知坏状态）

### 阶段 6 —— 待办总表

| hash | 日期 | 说明 | 改动文件 |
|---|---|---|---|
| `67da04d` | 09-23 | docs: 新增待办总表 BACKLOG | `docs/BACKLOG.md`(新) + 3 个 docs 加入口 |

### 阶段 5 —— agent 图表与 experiments 同源

| hash | 日期 | 说明 | 改动文件 |
|---|---|---|---|
| `f0eb134` | 09-23 | docs: 记录图表与图源一致性 | `docs/CHANGES_VS_SOURCE.md`、`docs/REAL_MACHINE_PLAN.md` |
| `69608a0` | 09-23 | fix(offline): 合成图版式对齐 + CJK 字体 | `quantum_service/offline_simulator.py`、`quantum_service/server.py` |
| `30a156c` | 09-23 | feat(web): 面板渲染实验图表 | `web/src/components/AgentChatPanel.tsx` |
| `5acc1fb` | 09-23 | feat(agent): 实验后取同款图 | `agent_service/adapter.py`、`agent_service/server.py` |
| `ff252cd` ⚠️ | 09-23 | feat(tools): 透传 `dataset_id`/`source_data`/`synthetic` | `common/quantum_tools/base.py`、`execution.py` |

> `ff252cd` 是 `5acc1fb` 的前提：单独回滚它，`agent_service` 读不到 `dataset_id`，**功能会静默失效**（不报错，只是退回模拟器自绘图）。

### 阶段 4 —— 设计文档

| hash | 日期 | 说明 | 改动文件 |
|---|---|---|---|
| `1e2b3fa` | 09-23 | docs: 真机接通设计方案 | `docs/REAL_MACHINE_PLAN.md`(新)、`CHANGES_VS_SOURCE.md` |
| `542f131` | 09-23 | docs: 改动与已实现功能说明 | `docs/CHANGES_VS_SOURCE.md`(新) |

### 阶段 3 —— 约定与仓库清理

| hash | 日期 | 说明 | 改动文件 |
|---|---|---|---|
| `221abf3` | 09-22 | docs(CLAUDE): 记忆数据备份约定 | `CLAUDE.md` |
| `e288bd5` | 09-22 | chore(memory): 移除 memory 索引跟踪 | `.gitignore`、`srv:config/memory/index.json`(删除) |
| `470d847` | 09-22 | docs(CLAUDE): 约定 11 改为按节奏提交 | `CLAUDE.md` |
| `447e363` | 09-22 | docs(CLAUDE): 约定 10/3 改本机环境 | `CLAUDE.md` |
| `59cbc6a` | 09-22 | docs: 记录 4 条已知局限 | `docs/TODO_RUNTIME.md` |
| `3eb5d4b` | 09-22 | chore(memory): 移除误入库的测试 episode | `srv:config/memory/index.json` |
| `b4bbdf1` | 09-22 | docs(CLAUDE): 增加"每次修改后提交"约定 | `CLAUDE.md` |

> `e288bd5` 回滚的后果：`index.json` **重新被跟踪**，之后每轮对话都会污染工作树（这正是当初要修的问题）。

### 阶段 2 —— agent 工具循环修复（核心）

| hash | 日期 | 说明 | 改动文件 |
|---|---|---|---|
| `07ceb0d` ⚠️ | 09-22 | fix(offline): 修复真实离线数据从未被解析 | `common/offline_data_provider.py`、`quantum_service/offline_simulator.py`、`quantum_service/server.py` |
| `b016df1` ⚠️ | 09-22 | feat(agent): 原生 tool calling + 补齐前端契约 | `agent_service/adapter.py`、`agent_service/server.py`、`common/quantum_tools/execution.py` |
| `edf528c` ⚠️ | 09-22 | feat(llm): tools 透传与 tool_calls 解析 | `llm_service/server.py` |

> **这三个强烈不建议回滚**：
> - 回滚 `edf528c` + `b016df1` → 退回「工具永不执行、只回一段文字」的状态
> - 回滚 `07ceb0d` → 退回「真实离线数据从未生效、一律合成」的状态
>
> 它们是「agent 能真正跑实验」的全部基础。

### 阶段 1 —— memory / hermes / 本机接线

| hash | 日期 | 说明 | 改动文件 |
|---|---|---|---|
| `9985eba` | 09-22 | feat(hermes): 模型接口对齐 + 修 `/chat` 挂死 | `hermes_service/server.py`、`llm_service/server.py`、`src/index.ts`、`HermesChatPanel.tsx`、`config/model_configs.json` |
| `73b257b` | 09-22 | feat(memory): 实现 memory 并记录每轮对话 | `agent_service/server.py`、`common/memory_store.py`(新)、`common/reflection_engine.py`(新)、`config/memory/index.json`、`docs/TODO_MEMORY_HERMES.md` |
| `0635fa0` | 09-22 | fix: 本机运行接线（路径/离线索引/依赖） | 15 个文件（见下） |
| `0d77a0b` | 09-22 | docs: deploy 差异/运行依赖/本机 TODO | `docs/DEPLOY_DIFF.md`、`RUNTIME_DEPS.md`、`TODO_RUNTIME.md` + 7 个校验脚本 |

`0635fa0` 的 15 个文件：
`config/session.json`、`python/requirements.txt`、`run-dev.cmd`、`scripts/start_all_services.py`、
`analysis_service/server.py`、`common/offline_data_provider.py`、`qca_service/server.py`、
`qca_service/tools/lab_tool.py`、`quantum_service/labrad_client.py`、
`qubitclient_service/requirements.txt`、`qubitclient_service/server.py`、
`test_offline.py`、`web/run-dev.cmd`、`lqcs_deploy/requirements.txt`、`test_load_qubits.py`

> `0635fa0` 里有一条**关键修复**：`start_all_services.py` 显式把 stdout/stderr 切到 UTF-8。回滚它会让启动器在打印 ✅/⚠️ 时抛 `UnicodeEncodeError`，**连带杀掉已起来的子服务**。
>
> `73b257b` 回滚的后果：agent 的 `/memory/*` 路由消失，memory 面板退回 502。

---

## 四、上游历史（`f2d83b5` 及以前，14 个）

这些是交付包自带的历史，**不是我做的改动**，回滚通常不需要碰。

| hash | 日期 | 说明 |
|---|---|---|
| `f2d83b5` | 09-21 | **Merge PR #2 —— 本机工作的基线** |
| `c3fd6e6` | 09-21 | 离线模拟 / 图像分析 / QCA / vendor 子模块（大提交） |
| `b0fbe5b` | 09-21 | 同上（重复提交信息） |
| `52001ff` | 09-17 | Merge PR #1 |
| `4524ac0` | 09-16 | 重构为微服务架构 |
| `68a09b9` | 09-11 | 新增 Hermes tab 页面 |
| `765d42f` | 09-07 | 非阻塞模式实现 agent 对话 |
| `ee4b69d` | 09-03 | 量子测控智能体（可独立完成 `sq.iqraw`） |
| `a69b664` | 09-02 | 新增 agent / images 两个 tab |
| `18d7fa1` | 08-27 | first commit |
| `7ba10ea` `dc668e7` `7978797` `c77a86e` | 08-27 | README / zip 的增删 |

---

## 五、按主题回滚（推荐用法）

> 优先用 `revert`（生成反向提交，历史可追溯）。只有在「确定不要这些提交了」时才用 `reset`。

### 场景 A：整体回到某个时间点（丢弃之后所有本地提交）

```bash
# 先留个后路
git branch backup-before-rollback

# 硬回到基线（会丢弃 23 个提交）
git reset --hard f2d83b5
```

⚠️ 之后 **`config/memory/` 会重新被跟踪**（因为 `.gitignore` 的改动也被回退了），记得重新处理。

### 场景 B：回到「agent 图表」这一整组之前

```bash
git branch backup-before-rollback
git reset --hard 1e2b3fa
```

结果：agent 退回「模拟器自绘图」，前端不再渲染图，合成图版式回到旧样式。

### 场景 C：只撤销某一个改动（保留历史）

```bash
git revert <hash>          # 生成一条反向提交
```

例如只撤销前端渲染：

```bash
git revert 30a156c
```

### 场景 D：撤销一组相互依赖的提交

必须在**一条命令里**按反向时间顺序给出，避免中间态：

```bash
git revert --no-commit f0eb134 69608a0 30a156c 5acc1fb ff252cd
git commit -m "revert: 撤销 agent 图表与 experiments 同源的一组改动"
```

### 场景 E：只回滚个别文件，不动其他

```bash
git checkout f2d83b5 -- "Agentic Workflow/qmclaw-server/services/agent_service/adapter.py"
```

适合「只把某个文件退回旧版」的局部试验。

### 场景 F：撤销一批还没提交的改动

```bash
git checkout -- <file...>        # 撤销工作区改动
git restore --staged <file...>   # 取消暂存
```

---

## 六、命令速查

| 目的 | 命令 |
|---|---|
| 看某提交改了什么 | `git show <hash>` |
| 只看文件名 | `git show --stat <hash>` |
| 看某文件的演变 | `git log --oneline -- <path>` |
| 查某行是谁引入的 | `git log -S "<代码片段>" -- <path>` |
| 查某文件在旧版的样子 | `git show <hash>:<path>` |
| 对比两个点 | `git diff <hash1> <hash2>` |
| 本次工作总差异 | `git diff --shortstat f2d83b5 HEAD` |
| 列出未推送提交 | `git log --oneline origin/main..HEAD` |
| 当前状态 | `git status --short` |

---

## 七、回滚后必须重启什么

Python 服务**不热重载**，改完文件必须重启对应服务才生效。

| 回滚涉及的目录/文件 | 需要重启 |
|---|---|
| `services/agent_service/*` | `agent_service` → **:3005** |
| `services/quantum_service/*` | `quantum_service` → **:3003** |
| `services/analysis_service/*` | `analysis_service` → **:3004** |
| `services/llm_service/*` | `llm_service` → **:3006** |
| `services/hermes_service/*` | `hermes_service` → **:3012** |
| `services/common/quantum_tools/*` | **:3005** + **:3003**（两边都导入） |
| `services/common/offline_data_provider.py` | **:3003** + **:3004** |
| `services/base/base_service.py` | **全部 10 个服务** |
| `src/index.ts`（Express） | Express → **:3002** |
| `qmclaw-web/src/**` | Next.js → **:8081**（dev 模式自动热更新） |
| `config/*.json` | Express 每次请求重读，**无需重启**；Python 服务需重启 |

**最省事的做法**：整组重启

```powershell
# 停掉 Python 服务组，再重新拉起（10 个服务一起）
cd "D:\semester4\QMClaw\Agentic Workflow\qmclaw-server"
& "D:\semester4\deploy\qmclaw\qmclaw\python.exe" scripts\start_all_services.py --all
```

> 注意脚本在 `scripts\` 下，不在服务根目录。

---

## 八、坑

1. **`post-checkout` 会改工作树**：回滚后 `git status` 可能出现新的 modified 文件（例如 `.gitignore` 规则变了导致某些文件重新变为已跟踪），属正常，逐一看清再决定。
2. **`tsconfig.tsbuildinfo` 是已跟踪的构建产物**：跑 `npx tsc` 会改它。按 CLAUDE.md 约定不要提交，用 `git checkout -- "Agentic Workflow/qmclaw-web/tsconfig.tsbuildinfo"` 还原。
3. **部分回滚会造成静默失效**：例如只回滚 `ff252cd` 而保留 `5acc1fb`，图表功能不会报错，只是悄悄退回旧行为。回滚一组功能时**整组一起回**。
4. **回滚 `e288bd5` 会让 memory 索引重新被跟踪**，之后每轮对话都脏工作树。
5. **回滚不会恢复数据**：`data/`、`config/memory/`、`.env`、`vendor/` 内容都不在版本控制里（见第二节 2、3）。
6. **提交信息是中文**：本机 git 已能正常显示；若在别的终端出现乱码，设 `git config core.quotepath false` 与 `chcp 65001`。
7. **推送前先扫密钥**（CLAUDE.md 约定 11）：

   ```bash
   git grep --cached -E "sk-[a-zA-Z0-9]{20,}" | Select-String -NotMatch "sk-x{20,}"
   ```
