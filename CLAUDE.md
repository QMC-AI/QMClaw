# CLAUDE.md — QmClaw 开发约定

## 导师上游融合（✅ **已合并完成** 2026-10-08）

导师上游 `Secsix/QMClaw`（main `03d838d`）已合入 `feat/c-real-machine`。

| 项 | 值 |
|----|-----|
| 合并提交 | `bf725e9`（父1 = 我方 `3d646c9`，父2 = 上游 `03d838d`） |
| 当前 HEAD | `e8aaa85`（合并 + 收尾 + 文档提交） |
| 冲突 | 4 个文件 / 6 处，全部按方案第五节解掉（`model_configs` / `base_service` / `HermesChatPanel` / `CLAUDE.md`） |
| 我方提交 | **36 个全部保留**，历史未被改写 |
| 回退点 | `git reset --hard backup/pre-upstream-merge-20261008-202936` |
| 自动验证 | `probe-verify` **12 PASS / 0 FAIL**（4 项需起服务、3 项 WARN 已处置） |

**收尾后必看**：

- `docs/UPSTREAM_MERGE_PLAN.md` — 方案全文（14 节：冲突解法、执行步骤、23 项验证清单、下次同步流程）
- **`docs/BACKLOG.md` G 组** — 融合后 10 条待办（记忆三处收敛 / skills / 依赖未声明 / …），**下一步从这儿挑**
- `docs/merge_verify_tools/` — 13 个验证脚本 + `run-merge.ps1`（再同步上游时复用，见方案第十四节）

**仍待人工（约定 3：服务启停由人控制）**：验证清单第 7 项（`npm install` 装 4 个新前端依赖 + 构建）、
第 8/9/10 项（起服务跑接口与端到端 t1、并肉眼比图 —— `offline_data_lab.py` 被上游整份重写）、
第 11/23 项（`GET /api/hermes/skills` 应非空；目录联接已建好，实测可扫到 7 个技能）。


## 配置文件

所有配置统一保存在 `qmclaw-server/config/` 目录：

| 文件 | 说明 |
|------|------|
| `session.json` | DataVault session 配置 |
| `experiment_configs.json` | 测控实验配置（函数、绘图命令） |
| `model_configs.json` | LLM 模型配置 |
| `fallback_config.json` | 离线模式配置（offline_mode 配置） |

**API Key 说明**：从环境变量读取，不保存在配置文件中
- `OPENAI_API_KEY`、`MINIMAX_API_KEY`、`ANTHROPIC_API_KEY`、`DEEPSEEK_API_KEY`

## 记忆数据（不纳入版本控制）

agent 的长期记忆存放在 `qmclaw-server/config/memory/`，**整个目录都在 `.gitignore` 中**：

| 路径 | 内容 |
|------|------|
| `episodes/*.json` | 每轮对话的完整记录（task / steps / status / metrics / response） |
| `skills/*.json` | 从对话里沉淀出的可复用技能 |
| `index.json` | 上面两者的摘要索引，每轮对话都会被重写 |

- **只存在于本地**：迁移机器或交付时必须**手工拷贝整个 `config/memory/` 目录**。
  漏拷会产生"索引里有记录、实体文件已丢失"的孤儿状态，`GET /memory/stats` 的 `orphanCount` 会暴露它
- **不要放进 git**：`index.json` 每轮对话都变，且单独存在没有意义 —— 它引用的是未跟踪的实体文件，
  clone 下来只会得到一份悬空目录

## 真机写闸门（安全约定）

**写闸门默认关闭**：`fallback_config.json` 的 `quantum_service.actuation` / `analysis_service.actuation`
里 `allow_execute` 与 `allow_set_params` 默认均为 `false`。

- **任何打开写权限的操作必须由人显式执行并记录**（改配置或设 `QMCLAW_ALLOW_EXECUTE` / `QMCLAW_ALLOW_SET_PARAMS`，然后重启服务）
- 闸门只在**服务端**强制；agent 侧只是提前解释
- 执行超时后服务标记 `stuck` 并拒绝后续下发，须人工确认现场安全后再 `reset_stuck` 或重启
- 提权步骤与故障排查见 `docs/REAL_MACHINE.md`

## 离线模式

quantum_service 和 analysis_service 支持离线模式，可在 LabRAD 不可用时使用历史数据：

- **配置**：`fallback_config.json` 中的 `offline_mode` 字段
- **模式切换**：POST `/mode` 接口，参数 `{"mode": "online"|"offline"|"auto"}`
  - `online`: 强制使用 LabRAD
  - `offline`: 强制使用离线数据
  - `auto`: 自动切换（LabRAD 断开时自动离线）
- **离线数据**：存放在 `qmclaw-server/data/offline_data/` 目录
- **索引生成**：运行 `python -m services.common.offline_data_provider --generate-index`

## 约定

1. **开发任务用 sub-session 隔离**：不污染主 session 的日常对话
2. **模块化和结构化设计**：分层架构（前端/后端/测控），单一职责，API 优先
3. **前后端服务启停由人控制**：不自行启动或杀死前后端服务，这些操作要由人完成（Express :3002、Next.js :8081）
4. **复杂功能开发前先确认需求**：向用户提问明确需求、优先级、边界条件
5. **CLAUDE.md 保持精简**：开发进展写入 `.claude/progress.md`，CLAUDE.md 只保留经用户确认的约定
    - **每次修改都要记录进 `.claude/progress.md`**（改动内容、文件、验证结果、提交 hash）
6. 当一个同样的问题反复出现两次以上，就不要采用直接的思路去解决了，而是增加一些debug信息来辅助解决。
7. 当需求涉及较多修改时，先制定计划，由用户确认后才能执行。
8. 计划模式下，或者用户明确让制定计划的时候，需要多向用户提问，详细了解需求。
9. 当前系统有后端打印日志的过滤器，只有满足特定声明逻辑的日志才能被打印到后端的终端窗口里。所以当你要增加后端日志的时候，需要检查一下后端日志过滤器(Agentic Workflow\qmclaw-server\src\index.ts)。
10. python用这个D:\semester4\deploy\qmclaw\qmclaw\python.exe（本机实际使用的解释器，Python 3.11.16，已装 249 个包）
    - 原文档写的 `C:\Users\HUAWEI\.conda\envs\qmclaw\python.exe` 是他人机器的路径，本机不存在
11. **按节奏提交，不是每改一个文件就提交**：累计到一定量或碰上重要节点时 `git commit`。
    - **攒够 3~5 处改动**提交一次；一次任务 / 会话收尾前必须提交干净，不留半成品在树上
    - **重要操作前后必须各自提交一次**（提交作为回退点）：
      - 动约定、配置、依赖（`CLAUDE.md`、`model_configs.json`、`.env`、`requirements.txt`）之后
      - 重构 / 批量替换之前先提交一版干净基线，之后提交一版结果
      - 删除文件、清理数据、改数据库或 memory 之前
      - 推送（`git push`）之前
    - 提交信息带 scope，例如 `fix(memory): 修正离线索引生成目录`、`feat(hermes): 模型接口与 agent 对齐`
    - **提交前必须确认没有把密钥加进去**：`.env` 已在 `.gitignore` 中，不要用 `git add -f` 强加；
      提交前和**推送前**各扫一遍（过滤掉 `.env.example.demo` 里的 `sk-xxxx...` 占位符）：
      `git grep --cached -E "sk-[a-zA-Z0-9]{20,}" | Select-String -NotMatch "sk-x{20,}"`
      一旦误提交并已推送，改历史无用，必须去平台吊销并轮换密钥
    - 只提交本次改动涉及的文件，不要 `git add -A` 把无关的构建产物（如 `tsconfig.tsbuildinfo`）一起带上

# Compaction 压缩规则（自动压缩时生效）
## 摘要必须保留内容
1. 项目架构：QMC Pro / QMC Claw微服务分层定义、各个微服务调用关系（Agent、Workflow、Quantum、Analysis、Image、QubitClient）
2. 关键技术决策：自动驾驶类比的规则引擎+大模型混合方案
3. 未完成任务清单、当前开发阶段
4. 核心代码接口定义、重要实验测控逻辑


## 需要简化/丢弃
1. 反复调试的中间失败日志、重复报错
2. 临时探索性的非核心讨论
3. 无关闲聊内容
4. 确认已修复的bug
