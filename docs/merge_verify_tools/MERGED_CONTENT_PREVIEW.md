# 合并后 4 个冲突文件的实际内容（预览）

> **用途**：跑真实合并**之前**，先看清那 6 处冲突会被解成什么。
> **来源**：探针仓库 `.merge-scratch\run-merge-probe\repo` 的合并提交 `a6596bb`（第一父 = 我方 HEAD `af90c1c`，第二父 = 上游 `03d838d`）。
> **复现**：`.\pipeline-test.ps1 -RepoRoot D:\semester4\QMClaw`，或 `.\run-merge.ps1 -DryRun`。
> 逐行 diff（732 KB，含全部 41 个文件）生成方式：
> `git -C <probe> diff <第一父SHA> <合并SHA> --output=effective-conflicts.diff`

## 1. `config/model_configs.json` —— 按逻辑模型去重（10 条）

| # | id | provider | modelId | name | 来源 |
|---|----|----------|---------|------|------|
| 1 | `deepseek-flash` | deepseek | `deepseek-flash` | DeepSeek Flash | 我方独有 |
| 2 | `model_default_006` | minimax | `MiniMax-M2.7` | MiniMax M2.7 | 我方 id 保留 / 导师字段 |
| 3 | `model_default_001` | openai | `gpt-4o` | GPT-4o | 我方 id 保留 |
| 4 | `model_default_002` | openai | `gpt-4o-mini` | GPT-4o Mini | 我方 id 保留 |
| 5 | `model_default_003` | openai | `gpt-4-turbo` | GPT-4 Turbo | 我方 id 保留 |
| 6 | `model_default_004` | anthropic | `claude-3-5-sonnet-20241022` | Claude 3.5 Sonnet | 我方 id 保留 |
| 7 | `model_default_005` | deepseek | `deepseek-chat` | DeepSeek Chat | 我方 id 保留 |
| 8 | `model_1787551340750_0` | minimax | `MiniMax-Text-01` | MiniMax Text-01 | 我方 id 保留 |
| 9 | `model_1787551340750_1` | minimax | `abab6.5s-chat` | MiniMax ABAB6.5S | 我方 id 保留 |
| 10 | `model_1787551340750_2` | minimax | `abab6-chat` | MiniMax ABAB6 | 我方 id 保留 |

**默认模型（两个面板取第一条 enabled+text）= `deepseek-flash`** —— 用户 H3 验收守住。

> 导师把 8 个模型 id 整套重编号了。这里**按 `(provider, modelId)` 语义去重**，
> 所以你的 id 全部原样保留、只采纳导师的字段改动，没有变成 18 条重复。

## 2. `services/base/base_service.py` —— 装饰器回到正确函数上

| 装饰器 | 绑定到 |
|--------|--------|
| `_timed_request('DELETE')` | `do_DELETE` |
| `_timed_request('GET')` | `do_GET` |
| `_timed_request('POST')` | `do_POST` |

- 导师的 `handle_streaming_request`（SSE）在树中：**是**
- 我方 `ThreadingHTTPServer` 在树中：**是**
- 行数：357

> 这是最容易解错的一处：简单并集会把 `@_timed_request("DELETE")` 粘到 SSE 方法上、让 `do_DELETE` 裸奔。
> 上表的绑定关系由 **AST 断言**得出，不是看字符串在不在。

## 3. `qmclaw-web/src/components/HermesChatPanel.tsx` —— 并集 + 选边

- `useModelStore`（我方改造）：**在**
- `MarkdownContent`（导师）：**在**
- `ReasoningBlock`（导师）：**在**
- `ToolCallsList`（导师）：**在**
- `interface ApprovalRequest`（导师）：**在**
- `DEFAULT_HERMES_MODEL`（我方 H3 删掉的错误字面量）：**已不存在 ✓**
- 花括号平衡：`{`=423 `}`=423
- 行数：1371

## 4. `CLAUDE.md` —— 并集

- 导师新增的 `# Compaction 压缩规则` 段：**在 ✓**
- 我方约定 10（本机 python 路径）：**在 ✓**
- 我方约定 11（按节奏提交）：**在 ✓**
- 是否残留他人机器路径 `C:\Users\HUAWEI` 作为规则正文：**否 ✓**（只在你那句"原文档写的是他人机器路径"的解释里出现）
- 行数：96

## 合并整体改动面

```
41 files changed, 12400 insertions(+), 357 deletions(-)
```

对照方案预测：导师 41 个文件（新增 18 / 修改 19 / 重命名 4），我方 36 个提交原有内容不动 —— 数字吻合。

## ⚠️ 这份预览的有效期

它对应**上游 `03d838d` 这一版**。若导师之后又推了新提交，
`run-merge.ps1` 会在 fetch 后发现 SHA 变化并警告；此时这份预览**不再代表实际结果**，
应按 `UPSTREAM_MERGE_PLAN.md` 第十四节重新走一遍并重生成。
