# 对抗式审查：`docs/UPSTREAM_MERGE_PLAN.md` 是否真的可执行

> 审查者：merge-plan-reviewer（独立复核，未参与方案撰写）
> 审查对象：`docs/UPSTREAM_MERGE_PLAN.md`（HEAD `af90c1c`，分支 `feat/c-real-machine`）
> 方法：不采信文档结论，用真实 `git merge` 重放 + 逐字节/逐 AST 复核。**没有**修改方案本身，**没有**碰 `.analysis\`。

## 0. 复现环境（本报告所有【已核实】的依据）

沙箱禁止写 `D:\semester4\QMClaw\.git`，且本机 `git fetch/clone <本地路径>` 会因为 msys `sh.exe` 无法创建 signal pipe 而失败（`*** fatal error - couldn't create signal pipe, Win32 error 5`）。因此我**没有**动真仓库的 `.git`，而是在 `.merge-scratch\probe\mrepo` 里重建了一个"物镜相同"的探针仓库，用它跑**真正的 `git merge`**：

```powershell
git init .merge-scratch\probe\mrepo
Copy-Item 'D:\semester4\QMClaw\.git\objects\*'                       .merge-scratch\probe\mrepo\.git\objects -Recurse -Force
Copy-Item 'D:\semester4\QMClaw\.upstream-mirror\objects\*'           .merge-scratch\probe\mrepo\.git\objects -Recurse -Force
git -C .merge-scratch\probe\mrepo update-ref refs/remotes/user/feat af90c1cabfcd45005b44bb4a389004102e494a3e
git -C .merge-scratch\probe\mrepo update-ref refs/remotes/up/main   03d838d23fbc95f6a8c727a3fece164976085820
git -C .merge-scratch\probe\mrepo checkout -b sync af90c1c
git -C .merge-scratch\probe\mrepo -c user.name=p -c user.email=p@l merge --no-ff refs/remotes/up/main
```

真实 merge 的结果与文档预判在**冲突文件集合上完全一致**（4 文件 / 6 块）。我还逐 blob 比对了"7 个自动合并文件"的预判产物与 git 真实 merge 的暂存内容（`.merge-scratch\review-clean-compare.py`）：

```
.claude/progress.md 51297/51297 一致 · .gitignore 616/616 一致
hermes_runner.py 36496/36496 一致 · start_all_services.py 15838/15838 一致
analysis_service/server.py 68945/68945 一致 · hermes_service/server.py 52747/52747 一致
src/index.ts 156862/156862 一致            → tw3 预判与 git 实际 merge 一致: 是
```

即**预判工具本身是可信的**（文档 §3.2 里 52747 字节这个数字也复现了）；问题出在**冲突解法规则**和**验证清单的判据**上。审查用到的脚本（均新建于 `.merge-scratch\`，可随时删除）：
`review-extract.py`（抽真实 merge 的冲突块）、`review-clean-compare.py`、`review-json-union.py`、`review-model-dupes.py`、`review-index-ts.py`、`review-upstream-audit.py`。方案自带的 `tw3.py` / `resolve.py` / `show-conflicts.ps1` / `verify-doc-claims.py` 我都跑过，结论见下。

**先行结论**：文档的 32 条事实主张基本成立（我独立重放了冲突集合），但 **"6 处冲突的解法已逐条实测校验通过" 不成立** —— 按文档规则执行会得到 2 处语义错误 + 1 处配置退化，而方案自带的校验器对这 3 处**全部报 PASS**。

---

## 一、第四节命令序列逐条可执行性

| # | 命令 | 判定 | 依据 |
|---|------|------|------|
| 0 | `git add "docs/CHANGES_VS_SOURCE.md" "docs/对齐-进度与待确认.md"` + `git commit` | **漏步骤，会导致第 4 步必然失败** | 见 1.1 |
| 1 | `git branch backup/pre-upstream-merge-20261008` | 语法可行，但前提不成立 | 见 1.2 |
| 2 | `git config http.sslBackend openssl` / `http.sslVerify false` / `remote add` / `fetch upstream main` | **网络姿势已核实可行**，但有 2 个副作用/矛盾 | 见 1.3 |
| 3 | `git checkout -b feat/upstream-sync` | 可行 | — |
| 4 | `git merge upstream/main --no-ff -m ...` | 可行（`upstream/main` 引用确实会被创建），冲突集合与预期一致 | 见 1.3 |
| 5 | `git add <解完的文件>` | **不是可执行命令**（占位符，且 4 个路径都含空格/中文，必须加引号） | 见 1.4 |
| 6 | `git checkout feat/c-real-machine` + `git merge feat/upstream-sync --ff-only` | 数学上成立，但**代码块里缺少人机闸门** | 见 1.5 |
| — | 回退 `git reset --hard backup/...` | 可行，但会连带丢弃未提交改动（第 0 步没提交干净时更危险） | 见 1.2 |

### 1.1 【已核实】【P0】第 0 步不会让工作树变干净，第 4 步的 merge 会直接中止

当前真实状态（`git -c core.quotepath=false status --short`，只读）：

```
 M .gitignore
 M docs/CHANGES_VS_SOURCE.md
?? .dsh-orders/
?? docs/UPSTREAM_MERGE_PLAN.md
?? docs/对齐-进度与待确认.md
```

`.gitignore` 的未提交改动是在追加 `.upstream-mirror/`、`.merge-scratch/`、`.analysis/`（`git diff -- .gitignore`）。第 0 步只 `git add` 了两个 docs 文件，**没有把 `.gitignore` 收进去**，而导师这两个提交恰好也改了 `.gitignore`（`cache/`、`*.db*`）。

复现（探针仓库里注入一处未提交的 `.gitignore` 改动，再跑第 4 步）：

```
git -C .merge-scratch\probe\mrepo status --short -- .gitignore   #  M .gitignore
git -C .merge-scratch\probe\mrepo merge --no-ff refs/remotes/up/main -m probe
# error: Your local changes to the following files would be overwritten by merge:
#         .gitignore
# Please commit your changes or stash them before you merge.
# Aborting
# Merge with strategy ort failed.
# exit=2
```

后果：**文档第 4 步根本执行不到冲突**，第 1/5/6 步全部落空。第 0 步必须补上 `.gitignore`（或 `git stash`），并加一句 `git status --short` 断言为空。

### 1.2 【已核实】第 1 步的"前提：工作树干净"没有任何命令保证

- `git branch <name>` 只是建引用、不切分支，本身没问题；但它备份的是**当前 HEAD**。文档没有断言当前在 `feat/c-real-machine` 且 HEAD == `af90c1c`（我查了，现在确实是这二者，但这是**巧合式安全**）。
- `git reset --hard backup/...` 会丢弃工作树未提交改动；在第 0 步没提交干净的情况下，回退会把 `.gitignore` 的改动一起吃掉。

建议在备份分支之前插两条断言：`git rev-parse --abbrev-ref HEAD`（应为 `feat/c-real-machine`）与 `git rev-parse HEAD`（应为 `af90c1c`）。

### 1.3 【已核实】网络姿势可行 —— 但两个副作用/矛盾文档没写

我在探针仓库里逐字复现了第 2 步（`git version 2.53.0.windows.2`）：

```
git ls-remote https://gh-proxy.org/https://github.com/Secsix/QMClaw.git main
# 03d838d23fbc95f6a8c727a3fece164976085820  refs/heads/main      ← 与 .upstream-mirror 的 main 完全一致
git fetch upstream main
#  * [new branch]      main       -> upstream/main                ← 关键：引用确实被创建
git rev-parse refs/remotes/upstream/main  → 03d838d...
```

- 直连 schannel 失败、加 `openssl + sslVerify=false + gh-proxy` 后可用：**文档这条是对的**（`ls-remote` 还顺带从**活的远端**确认了 `main = 03d838d`，不是只信本地镜像）。
- **副作用 1（P1）**：`git config http.sslVerify false` 写的是**仓库级持久配置**，不是"仅本机临时"。它会同时作用于 `origin` 的 fetch/push。
- **副作用 2（P1，文档自相矛盾）**：`git remote -v` 显示 `origin` **本来就是** `https://gh-proxy.org/https://github.com/hzz-wdb/QMClaw.git`。所以文档 §4.3 那句"不要用它 push 私人代码 / push 走直连"与仓库现状冲突 —— 照做需要先改 `origin` 的 URL，而第四节没有这一步。建议改用逐命令 `-c http.sslBackend=openssl -c http.sslVerify=false`（本任务书自己就是这么写的），不要落盘。
- **副作用 3（P0，环境约束）**：`git config` / `remote add` / `fetch` / `branch` / `add` / `commit` 全都要写 `.git`，本会话沙箱一律拒绝，且**本会话的审批提示已禁用、无法提权**。所以第四节整段在当前 agent 会话里**一条都跑不通**，必须由人在沙箱外执行。文档 §8 末行"把会话切到完全权限"这个处置在当前环境不可用，应改成"由人在普通终端执行"。

### 1.4 【已核实】第 5 步不是命令

`git add <解完的文件>` 是占位符。4 个路径全部含空格（`Agentic Workflow/...`），必须写成带引号的 4 条，例如：
`git add "Agentic Workflow/qmclaw-server/config/model_configs.json" "Agentic Workflow/qmclaw-server/services/base/base_service.py" "Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx" CLAUDE.md`

### 1.5 【已核实】第 6 步缺少"人起服务"的闸门，照抄会跳过验证

`git merge feat/upstream-sync --ff-only` 本身成立（`af90c1c` 是合并提交的第一父，可以快进）。但代码块把"验证（见第七节），全绿后主分支合并"写成了**连续三行命令**，注释不是闸门。而第七节第 8/10 项必须由人启停服务（CLAUDE.md 约定 3，文档 §7 末行也承认了这点）。照抄执行 = **没验证就合回主分支**。文档给的这一点（第 8/10 项由人做）是对的，只是缺一条显式的 `# ← 在此停下，人工起服务并跑第 8/10 项`。

### 1.6 第四节缺失的步骤（不修就必然卡住）

1. **`npm install`**（4 个新前端依赖未安装，见 3.1）；
2. `.env` / `HERMES_HOME` 的决策（见 4.4）；
3. `tsconfig.tsbuildinfo` 的处理时机（见 3.3）；
4. 4 个重命名测试文件的引用检查（见 3.8）；
5. `memory/MEMORY.md`、`auth.lock` 是否入库的决定（见 4.6）。

---

## 二、第五节 6 处冲突解法逐条判定

### 2.1 【已核实】【P0】`5.2 base_service.py` —— "两边都留"把装饰器绑到了错误的函数上

文档原话："这两个改动互不相干 —— 一个在 `do_DELETE` 上方加装饰器，一个是新增方法"。**它们落在同一个锚点上**，文本并集会改语义。真实 merge 的冲突块（`review-extract.py`，探针仓库）：

```
#1  合并文件行 136..173   OURS=1行/29B   BASE=0行/0B   THEIRS=34行/1236B
OURS 侧:   @_timed_request("DELETE")
THEIRS 侧: def handle_streaming_request(self, path: str):  ...（34 行）
```

`resolve.py` 的 union 结果（`%TEMP%\qmclaw-3way\resolved\...base_service.py`）：

```
136:    @_timed_request("DELETE")            ← 我方（原本紧贴 do_DELETE）
137:    def handle_streaming_request(self, path: str):   ← 导师
...
171:    def do_DELETE(self):                 ← 裸奔，装饰器没了
```

对照我方 HEAD 原文（`git -C . grep -n '@_timed_request\|def do_' HEAD -- .../base_service.py`）：`L131 @_timed_request("DELETE")` → `L132 def do_DELETE`。

**解错会怎样（已核实）**：
1. 我方 B 组改造里 **DELETE 路由的慢请求统计/计数彻底丢失**（`do_DELETE` 不再被包装）；
2. 每个 SSE 流式请求被计入 `request_count` 并被打成 `SLOW DELETE <path>`（`_timed_request` 的日志串是 `f"[{name}] SLOW {method} {self.path}"`），指标与日志都被污染；
3. **方案自带的校验器抓不到**：`resolve.py` 只断言 `ast.parse` 通过 + `ThreadingHTTPServer` / `handle_streaming_request` / `_timed_request` 三个字符串存在 → PASS；第七节第 4 项同样只是查 `SLOW ` 子串存在 → 也是绿。这是"双重盲区"。

**修正**：该冲突块按 **theirs 在前、ours 在后**（导师方法插到 `@_timed_request("DELETE")` 上方），或 union 之后把装饰器补回 `do_DELETE` 上方；校验器改为断言装饰器的 AST 目标（`ast.FunctionDef.decorator_list`）而不是子串存在。

### 2.2 【已核实】【P0】`5.1 model_configs.json` —— 冲突原因写错，"按 id 并集"产生 16/18 条重复模型并翻转默认模型

**文档把冲突原因说成"导师在数组头部动了 `model_default_006` 的字段"。** 真实 diff（`git -C .upstream-mirror diff c3fd6e6 main -- .../model_configs.json`）显示导师做的是**整份重建**：把 8 个模型的 `id` 全部重编号（`model_default_00N` / `model_1787551340750_N` → `model_1790146722274_0..7`），顺带重置 `createdAt/updatedAt`，并把 MiniMax M2.7 提到数组首位。因为 id 字符串长度相近，文件只涨了 29 字节 —— 这正是文档"改动很小"印象的来源。

按"解析 JSON、按 `id` 做并集、导师条目在前"执行（`resolve.py` 的 `json_union`，实测输出）：

```
原始字节: base=3631 ours=4016 theirs=3660 | 并集产物=7201
models = 18
按 modelId 重复: gpt-4o x2, gpt-4o-mini x2, gpt-4-turbo x2, claude-3-5-sonnet x2,
                 deepseek-chat x2, MiniMax-Text-01 x2, abab6.5s-chat x2, abab6-chat x2
重复条目占比: 16/18
```

即 **8 个逻辑模型各出现两次**，只是因为 id 不同才躲过了"无重复 id"这条判据。

**更严重的是它推翻了我方已验收的行为**：`resolve.py` 输出里第一条 enabled+text 是 `model_default_006`（MiniMax M2.7）；而 `AgentChatPanel.tsx:67-71` 与 `HermesChatPanel.tsx`（5.3 第 3 处取 OURS 的那段）都是 `models.filter(...)[0].id`，`modelStore.fetchModels()` 又按服务端返回顺序原样存（`src/store/modelStore.ts:28-36`，无排序）。于是**两个面板的默认模型从 `deepseek-flash` 翻成 MiniMax M2.7**。而我方 `docs/TODO_MEMORY_HERMES.md` 明确记着：

```
L22: | H3 修死默认值 | ✅ | 删除 "minimax/MiniMax-M2.7"；两个面板默认都选 deepseek-flash |
L86: GET /api/hermes/models  第一条字段 = ['id','name','provider']   id = deepseek-flash
```

第七节第 8 项的判据是"两个接口返回一致且**包含** `deepseek-flash`" —— 18 条里当然包含，**照样全绿**。

另外两个次要问题（已核实）：`json_union` 用 `dict(theirs)` 重建整个对象再 `json.dumps(indent=2)`，产物 7201 字节 vs 原文 4016 字节（整文件重排、diff 噪音、末尾换行语义变化）；且该规则对"两边都改过的同 id 条目"一律取 theirs（本例 `model_default_006.baseUrl` 取 theirs 是对的，但规则本身会静默丢弃我方同 id 字段改动）。

**修正**：按 `(provider, modelId)` 去重而不是按 `id`；保留我方 id（前端/DB 引用的是旧 id）、采纳导师的字段改动；把 `deepseek-flash` 放回首位；判据升级为"无 `(provider,modelId)` 重复"+"`/api/models` 第一条 id == `deepseek-flash`"。

### 2.3 【已核实】`5.3 第 1 处` 并集 —— 正确

真实块规模 `OURS=1行/52B（import useModelStore）`、`THEIRS=98行/3541B`（3 个 lib import + `extractImagesFromContent` / `normalizeToolCalls` 两个工具函数）。两边都是**同一锚点的纯插入**，并集后 import 在上、函数定义在下，`resolve.py` 报"花括号平衡 {=423"、5 个关键标识符全部存在。无需改。

### 2.4 【已核实】【P0】`5.3 第 2 处` —— 文档的散文和它"实测通过"的规则不是同一件事

散文说："**并集**（保留导师的接口，保持你的删除）"。但 `resolve.py` 的 `union` 实现是 `out.extend(ours); out.extend(theirs)`，会**把导师那侧的 `const DEFAULT_HERMES_MODEL = "minimax/MiniMax-M2.7";` 一起塞回来**：

```
o__HermesChatPanel.tsx : DEFAULT_HERMES_MODEL 命中 0
t__HermesChatPanel.tsx : 命中 2（声明 + 使用）
resolved              : 命中 1  → L161 只有声明，全文件零引用（死代码）
```

后果分两层：① 运行期没有把硬编码兜底列表塞回下拉框（因为第 3 处取了 OURS），所以不致命；② 但**被"实测校验通过"的那个产物 ≠ 文档教你手搓出来的那个产物**，而且它把用户特意删掉的错误模型字面量又请了回来。`qmclaw-web/tsconfig.json` 没有 `noUnusedLocals`、仓库里也**没有任何 eslint 配置**（`.eslintrc*` / `eslint.config*` 均不存在），所以不会 fail build —— 这更说明它不会被任何现有检查抓住。

**修正**：规则改成"保留 `interface ApprovalRequest`、丢弃 `const DEFAULT_HERMES_MODEL` 那一行"，并在校验器里断言 `DEFAULT_HERMES_MODEL` **不存在**（现在没有任何检查断言这一点）。

### 2.5 【已核实】`5.3 第 3 处` 取 OURS —— 正确，反例检查不成立

- 并集确实不可行：两侧都是同一个 `useEffect` 的互斥实现，并集会变成 `... }, [textModels, model]);` + `... }, []);` 双收尾 → 语法坏。文档这个判断成立（我复算了 `resolve.py` 在 `union,union,union` 下会失衡）。
- "应该取对方"的**反例不存在**：导师那侧以 `api.hermesGetModels()` + 硬编码兜底列表为准，其中 `id` 用的是 `"minimax/MiniMax-M2.7"` 这种在 `model_configs.json` 里不存在的标识符（配置文件里 MiniMax M2.7 的 id 是 `model_default_006`）；而我方走 `useModelStore`，与 `AgentChatPanel` 同一套语义。
- 保留一条提示（文档 §5.3 注里已提到，验收时别漏）：合并后 `HermesChatPanel` 走 zustand store，导师新增的 `HermesToolbar` / `HermesSessionsSidebar` / `HermesExtensionsPanel` 直接 `import { api } from "../lib/api"` 自己拉取 —— **同一屏存在两条模型加载路径**，面板与工具条的模型列表可能不同步，别把它误判成合并 bug。

### 2.6 【已核实】【P0】`5.4 CLAUDE.md` —— "取你的"会整段丢掉导师新增的 Compaction 规则

文档说冲突原因是"同一行约定 10 python 路径被双方各改一次"。真实冲突块（`CLAUDE.md:67..99`）里 **theirs 侧是 15 行/641B**：重复的约定 10 一行 + **一整段新增的 `# Compaction 压缩规则（自动压缩时生效）`**（摘要必须保留 4 条 / 需要简化丢弃 4 条）。

按"取你的"解出来的结果：

```
resolved CLAUDE.md 字节数 = 5465  = 我方 HEAD 的 CLAUDE.md 字节数（git cat-file -s HEAD:CLAUDE.md）
theirs = 2956
resolved 中 "Compaction" 命中 = 0
```

即**导师新增的整段压缩规则被静默删除**。这不是"解释性文字"，是会被 agent 直接读取的行为约定（CLAUDE.md 就是本仓库的工作指令），属于配置语义丢失。

**修正**：该块应做 **union** —— 保留我方约定 10/11，追加导师的 Compaction 段，只丢掉重复的那一行 python 路径。

---

## 三、第七节验证清单的漏项

先说一条**已核实**的硬伤：**第 1 项的命令本身是坏的**。

文档写的 `git grep -nE "^(<{7}\|={7}\|>{7})" -- .` 用的是 `-E`（ERE），在 ERE 里 `\|` 是**字面竖线**而不是"或"。实测（探针仓库里 6 个冲突块都还在）：

```
pat1 = '^(<{7}\|={7}\|>{7})'      ← 文档原样
  → 输出 0 行，exit=1        ← 判据"无输出"= PASS，而 18 条冲突标记全在树上
pat2 = '^(<{7}|={7}|>{7})'        ← 去掉反斜杠
  → 输出 19 行（18 条真标记 + 1 条误报 demo_hermes_verify.py:3:=============================）
pat3 = '^(<{7}|={7}$|>{7}|\|{7})' ← 建议替换（已实测：恰好 18 行，零误报）
```

也就是说第 1 项会给出**假绿**；去掉转义又会因为 `demo_hermes_verify.py` 的 `=====` 分隔行产生误报。建议直接用 `pat3`（或 `git grep -nF -e "<<<<<<< " -e ">>>>>>> "` 再加 `git grep -nE '^={7}$'`）。

其余漏项（每条给出后果与复现）：

| # | 漏掉的检查 | 后果 | 复现 |
|---|---|---|---|
| 3.1 | **`npm install`**（4 个新依赖） | 第 7 项必然红，且无法靠"依赖可能变过"解决 | 见 4.2 |
| 3.2 | 第 3 项的 `python` **未指定解释器** | `Get-Command python` → `...\Python314\python.exe`，不是约定 10 的 3.11.16；用错解释器的"语法通过"不能代表运行时 | `(Get-Command python).Source` |
| 3.3 | 第 4 项用子串判断 | 抓不到 2.1 的装饰器错挂（`SLOW ` 仍在、`_timed_request` 仍在） | 见 2.1 |
| 3.4 | 第 2/8 项判据太弱 | 18 条（含 16 条重复）也能通过"无重复 id"；默认模型翻成 MiniMax M2.7 也能通过"包含 deepseek-flash" | 见 2.2 |
| 3.5 | 没有断言 `DEFAULT_HERMES_MODEL` 必须不存在 | 用户 H3 的要求无人守护，解析器反而会把它加回来 | 见 2.4 |
| 3.6 | 没有 `.env` / `HERMES_HOME` 一致性检查 | 导师新增的 skills/memory 功能会读错目录（见 4.4） | 见 4.4 |
| 3.7 | 没有新模块 **import 冒烟测试** | `compileall` 只查语法，不查缺失模块/缺失 vendor 符号 | `python -c "import services.hermes_service.memory, ...skills, ...cron, ...approval_ws, ...mcp_toolsets"` |
| 3.8 | 4 个重命名测试文件**没有引用检查** | 我方对 `test_load_qubits.py` 的 48 行改动被静默搬到 `tests/`（内容已核实一致），仓库里若有文档/脚本仍指向根路径就会指向空文件 | `git grep -n "test_load_qubits.py\|sse_verify.py\|test_labrad.py\|test_load_qubits" HEAD -- docs scripts README.md` |
| 3.9 | `tsconfig.tsbuildinfo` 的处理时机 | 它在导师改动清单里且 `tsconfig.json` 有 `"incremental": true` → 合并后工作树里是**导师那边的陈旧构建产物**；文档说"合并后从索引摘掉"（独立提交），但第 7 项要在那之前 build | `git ls-files --stage -- 'Agentic Workflow/qmclaw-web/tsconfig.tsbuildinfo'`；`Get-Content .../tsconfig.json` |
| 3.10 | 运行时状态文件是否该入库 | `memory/MEMORY.md` 被**网关直接读写**（`index.ts:2852 getHermesMemoryDir()` = `<repo>/memory`，2698/2743/2767/2811 四个 handler 拼 `MEMORY.md`）；`auth.lock` 合并进仓库但**全仓库无任何代码引用**（`git grep -n 'auth.lock' main` 为空）。这两个文件入库后会被运行时改写/成为垃圾，且下次同步上游必冲突 | 见 4.6 |
| 3.11 | 新落点检查 | `getHermesDir()`（`index.ts:2857`）= `%LOCALAPPDATA%\hermes`（存在则优先），否则 `~/.hermes`；`hermes_runner.py` 另有 `~/.qmclaw_hermes` → cron/config 状态落在**仓库外**，备份/交付容易漏 | 上面两处代码位置 |
| 3.12 | 没有"改动面"核对 | 无法证明合并只影响了预期的 `41 ∪ 62` 文件 | 合并后 `git diff --name-only HEAD^1` 与两侧清单求并集比对 |

---

## 四、第七、八节都没提到的第三类风险

### 4.1 【已核实】方案 §3 漏掉了 8 个"干净合并、但语义相关"的上游改动

导师 41 个文件 = 新增 18 + 修改 19 + 重命名 4。文档 §3.1 只列了 18 个新增，§3.2 只列了 11 个重叠 —— **19 个修改里有 8 个既不在重叠表、也没被任何一节逐一列出**：

```
.env.example · services/common/offline_data_lab.py · qmclaw-web/package.json
qmclaw-web/package-lock.json · qmclaw-web/src/app/page.tsx
qmclaw-web/src/components/VariantGenerator.tsx · qmclaw-web/src/lib/api.ts
qmclaw-web/tsconfig.tsbuildinfo
```

复现：`git -C .upstream-mirror diff --name-status --find-renames c3fd6e6 main`，与 §3.1/§3.2 的清单求差。其中 4 个直接对应下面的风险，1 个（`offline_data_lab.py`）是**我方离线链路的核心依赖被整体重写**。文档第 23 行"导师的 41 个文件里，没有任何一个是你改过的同一路径"这句本身也自相矛盾（紧接着就说 11 个重叠）。

### 4.2 【已核实】【P0】前端新增 4 个 npm 依赖，但合并流程里没有安装步骤

`package.json` 新增（`git -C .upstream-mirror diff c3fd6e6 main -- .../package.json`）：`highlight.js`、`react-markdown`、`rehype-highlight`、`remark-gfm`。新文件 `src/lib/markdown.tsx` 直接引用它们（L16-19）。本机现状：

```
node -e "require.resolve('react-markdown')"  → NOT_RESOLVABLE（4 个全部）
```

后果：`npm install`/`npm ci` 之前，前端 build 与 dev server 都会 `Module not found`（第 7 项、以及第 6 项"前端 3 个新组件能编译"都过不去）。这里 npm 解析失败是【已核实】，"构建必然失败"是【推断·高置信】（Node 模块解析是确定性的）。注意 `package-lock.json` 也被导师那一版覆盖，`npm ci` 会严格按导师 lock 安装 —— 本机 registry/缓存不通时需要先确认能装。

### 4.3 【已核实·API 兼容 / 推断·行为变化】`offline_data_lab.py` 被整文件重写，且没有任何检查能发现行为变化

该文件**不在 11 个重叠文件里**（我方从未改过它），所以合并会**无声地整份替换**：+359/-39，`fitData()` 从 `(exp_num, collect, do_plot)` 变成 `(datasets, fit_func, qname, update, collect, config_name, des, **kwargs)`，并新增 `_fit_iqraw` / `_auto_detect_fit_func` / `_default_plot` / `_plot_fit_result` 等一整条拟合-出图流水线。

我做了兼容性反例检查，**没有找到会炸的调用点**（这点必须讲清楚，避免误报）：
- 位置参数仍生效（`offline_data_lab.py:318-319` `if datasets is not None: self._data.loadDataset(datasets)`）；
- `do_plot` 仍生效（L332 `do_plot = kwargs.get("do_plot", True)`）；
- 仓库里**没有** `exp_num=` 关键字调用点：`config/experiment_configs.json` 用的是模板 `qter.fitData({exp_num})`，`agent_service/adapter.py:132-136` 把它改写成 `qter.fitData(do_plot=True)`，`analysis_service/server.py:800-826` 只解析 `k=v` 形式再 `qter.fitData(**params)`。

但**风险真实存在**：我方"离线链路"的 Tier1/Tier2 出图最终就是走到这个类里的，引擎换了以后出图口径/样式会变；而第七节第 5 项查的是 `X_UNIT_SCALE` 等**子串是否还在**，第 9 项只看 `success=True 且 charts=1` —— 两项都可能全绿而图已经不是原来那张。这类"检查通过但行为变了"的盲区，正好落在文档反复强调的"你的离线链路还在"这句话上。

另有【推断】风险：老实现的 `collect=True` 返回语义与新实现不同（L353 会返回 `{"success": True, "data_shape": ...}`），仓库里 `python/workflow/model.py:189` 存在 `self._qter.fitData(-1, collect=True, do_plot=False)[1][-2][0]` 这种按下标取值的旧式用法；在线路径不受影响，但若将来把离线 updater 接到那条调用上就会炸。建议在合并后对这个文件单独跑一次真实离线出图并与合并前**肉眼比对图片**（而不是只判 `charts=1`）。

### 4.4 【已核实·路径冲突 / 推断·功能失效】`HERMES_HOME` 语义冲突：`.env` 不在合并范围内，导师的新功能会读错目录

- 本机真实 `.env`（gitignored，不在合并范围）里：`HERMES_HOME=D:\semester4\QMClaw\Agentic Workflow\qmclaw-server\data\hermes`；
- 导师新增的 `hermes_runner.py` 那段（合并后会进树，位于 `m__hermes_runner.py:137-153`）与 `.env.example` 注释都写着：`HERMES_HOME` 应指向**项目根**，"Must be set BEFORE importing AIAgent **to use project's skills directory**"，未设置时才回落到 `_QMCLAW_ROOT`；
- 而本机**已经设置**了它 → 导师的默认逻辑不会生效 → skills 目录会解析到 `<server>/data/hermes/skills`，而项目真正的 19 个技能文件在 `<repo>/skills/**/SKILL.md`（`git ls-files skills` 共 19 个，含 `SKILL.md`）。

后果【推断·高置信】：导师新增的 `/api/hermes/skills` 动态技能管理界面在**本机**看到的技能列表为空或不对；第七节第 6 项只检查"四个新模块能被 `server.py` 引用到"（静态 grep），第 8/10 项也不碰 skills，所以整套清单不会报错。**修法**：合并前决定 `.env` 里 `HERMES_HOME` 到底指向项目根还是 `data/hermes`，并把"`/api/hermes/skills` 返回非空且能看到项目 `skills/`"写进验收。

### 4.5 【已核实】记忆不是"两套"而是**三处路径**；skills 同样是"各做一套"—— §10/§11 的判断不完整

文档 §10.1 把导师那套描述成"自己的数据目录 / `MEMORY.md`、`USER.md` / Hermes 侧 UI 读取"。代码不是这样：

| 位置 | 谁写的/读的 | 证据 |
|---|---|---|
| `<server>/config/memory/`（episodes/skills/index.json） | 我方 `common/memory_store.py` + `/api/agent/memory/*` | §10.1 已述 |
| `<server>/data/memories/{MEMORY,USER}.md` | 导师 python 侧 | `hermes_service/memory.py:106-122` `_get_data_dir()` = `<qmclaw-server>/data/memories` |
| `<repo>/memory/MEMORY.md` | 导师**网关**侧（新 UI 的 memory 接口直接读文件） | `src/index.ts:2852` `getHermesMemoryDir()` = `path.resolve(__dirname,'..','..','..','memory')`，2698/2743/2767/2811 拼 `MEMORY.md` |

也就是说导师自己的 python 服务与自己的 UI 指向的**不是同一个文件**（`data/memories` vs 仓库根 `memory/`）。§10.1 建议"先合、后单独立项统一"，但连导师内部都没统一，收敛工作量被低估了。

skills 同理：我方已有 `/api/skills` CRUD（存 `qmclaw-server/config/skills.json`，`index.ts:2685-2724`）+ agent 侧 `/api/agent/memory/skills`（走 `memory_store`），导师又加 `/api/hermes/skills`（读 `HERMES_HOME/skills/**/SKILL.md`）+ 新配置 `config/skills_config.json`（`{"disabled_skills":[],"enabled_skills":[]}`，只有 `skills.py` 引用）。所以 §11 那句"记忆……是**唯一**一个两边各做了一套、且都还能跑的地方"**不成立**，skills 是第二处（我方内部还是两处）。

### 4.6 【已核实】合并会把两个"运行时状态文件"带进版本库

- `memory/MEMORY.md`：导师提交的内容是项目约定（如"图像显示规范"），但合并后它就是**网关运行时读写的那个文件**（见 4.5 第 3 行）。工作树会被运行时常驻改写 → 违反 CLAUDE.md 约定 11 的"收尾前必须提交干净"，且下次同步上游必冲突。
- `auth.lock`：导师新增的空文件，**整个上游仓库没有任何代码引用它**（`git -C .upstream-mirror grep -n 'auth.lock' main` 为空），是运行时残留/误提交。
- 相关的第三个落点：`%LOCALAPPDATA%\hermes`（或 `~/.hermes`），见 3.11。

文档 §8 只问了"`auth.lock` / `SOUL.md` / `memory/MEMORY.md` 是不是该进版本库"（一句待办），但没有把它变成**执行前**的决定，也没有把它和"运行时会改写 tracked 文件"这个机制联系起来。

### 4.7 【已核实】`tsconfig.tsbuildinfo` + `incremental: true` 的组合

`qmclaw-web/tsconfig.json` 有 `"incremental": true`，`tsconfig.tsbuildinfo` 是**被跟踪**的文件且**被导师修改过**。合并后工作树里躺着的是导师机器上的增量构建状态。后果【推断】：`next build` / `tsc` 首次运行的增量基线来自另一台机器/另一棵源码树，可能出现"跳检"或反复重写该文件（重写又让工作树变脏，和约定 11 打架）。建议在跑第 7 项**之前**删除工作树里的这个文件，再统一"从索引摘除 + 加 .gitignore"。

### 4.8 【推断】vendor 子模块：gitlink 没变 ≠ 兼容

文档 §8 的结论是"导师这两个提交没动 `.gitmodules`，所以合并不会碰 `vendor/`"——这部分**已核实**（`verify-doc-claims.py` 第 9 条 PASS）。但合并后的 `hermes_service/server.py` 会在运行时把 `vendor/hermes-agent` 加进 `sys.path` 并 `import`（`m__...server.py` 的 `_HERMES_AGENT_PATH = os.path.join(_QMCLAW_ROOT, "vendor", "hermes-agent")`），而本机 `vendor/hermes-agent` 是**没有 `.git` 的普通目录**（版本是否等于 pin 的 `c4a5dee` 由 task-1 负责核实）。导师新增的 approval / cron / mcp 功能会不会调用仅在更新版 hermes-agent 里存在的接口，静态 grep 看不出来。**建议**：把"合并后 `python -c 'import ...hermes_service.server'` 冒烟 + hermes 服务启动 + 走一次审批/定时任务接口"写成显式检查项（第 10 项只覆盖普通对话）。

### 4.9 【已核实】前端 store 状态：两条模型加载路径并存

`useModelStore` 是全局 zustand store（`src/store/modelStore.ts`），我方 `AgentChatPanel` 与 `HermesChatPanel` 都用它；导师新增的三个组件不经 store、各自 `import { api }`（`HermesToolbar.tsx` / `HermesSessionsSidebar.tsx` / `HermesExtensionsPanel.tsx` 均只 `import { api } from "../lib/api"`）。后果是"面板里换模型，工具条不一定跟着变"这类**看起来像 bug 的合并现象**；验收时不要把它记成回归。

### 4.10 【已核实】路由与顶层声明：**没有**发现冲突（负面结论同样重要）

我担心"自动合并"的 `src/index.ts` 会撞出重复路由或重复声明，实测三份文件（ours 135906 / theirs 156949 / merge 156862 字节）：

- 上游新增 20 个 `(method,path)`（`/api/hermes/{cron,memory,sessions,skills,...}`），删改我方路由 0 个；
- 合并结果的 `(method,path)` 计数与 ours ∪ theirs 逐条一致，**没有重复注册**；
- 顶层声明重复：merged 为 0。

复现：`.merge-scratch\review-index-ts.py`（用 tw3 的 `m__...index.ts` 作为 merged 输入，因为探针仓库会被 `merge --abort` 还原）。另外新工具模块硬编码的 `http://localhost:3004`（analysis）与 `http://localhost:3008`（workflow）与本机 `config/services.json` 的 3004/3008 **一致**，这一条也没问题。

---

## 五、"一句话结论"是否站得住

原句：「导师的两个提交是一整块**互不重叠**的 Hermes 扩展，你只需在 4 个文件里手工做 6 个决定；其中 3 个决定是"两边都留"，2 个是"留你的"，1 个是"JSON 按 id 合"。用 merge 而不是 rebase，因为它会让你的 36 个提交和回滚手册里的所有 hash 继续有效。」

逐断言判定：

| 断言 | 判定 | 依据 |
|---|---|---|
| "4 个文件 / 6 处冲突" | **成立**【已核实】 | 真实 merge 与 tw3 预判一致：model_configs 1、base_service 1、HermesChatPanel 3、CLAUDE.md 1；7 个重叠文件全部自动合并 |
| "用 merge 不用 rebase，36 个提交 hash 继续有效" | **成立**【已核实】 | merge 不改写历史；`git merge feat/upstream-sync --ff-only` 路径成立 |
| "互不重叠" | **不成立**（且自相矛盾） | 11 个文件重叠；第 23 行"没有任何一个是同一路径"与紧接着的"11 个重叠"直接打架；另有 8 个非重叠但语义相关的上游改动（4.1） |
| "6 个决定……已逐条实测校验通过" | **不成立** | 5.2 解错（装饰器挂错函数）、5.4 解错（丢 Compaction 段）、5.1 产生 16/18 重复模型并翻转默认模型；而校验器/清单对这 3 处全部报绿 |
| "融合成本很低，可以在一个合并提交里做完" | **需加限定**【已核实】 | 冲突体量确实小，但在修好上述 3 条规则之前不算"定案"；此外还有 `npm install`、`.env`/`HERMES_HOME`、运行时状态文件入库三件必办事项 |

**修正版结论**：

> 导师这 2 个提交在 git 层面确实只是一个 merge 的量级（4 个文件 / 6 处冲突，36 个提交 hash 与回滚手册不受影响），**但"6 处解法已实测通过"不成立**：按现规则执行会得到 ①`@_timed_request("DELETE")` 挂到 SSE 函数、`do_DELETE` 失去计时，②CLAUDE.md 整段丢掉导师新增的 Compaction 规则，③模型配置 18 条里 16 条重复、两个面板默认模型从 `deepseek-flash` 翻成 MiniMax M2.7。这三条必须先改规则（并把校验器从"子串存在"升级为语义断言），再补 `npm install`、`HERMES_HOME` 决策、`memory/MEMORY.md`+`auth.lock` 入库决定、`.gitignore` 提交干净，然后才是一个合并提交能收尾的量级。

---

## 必须在执行前修正（清单）

**P0 —— 不改就会解错/跑不通**

1. **第 0 步补提交 `.gitignore`**（或 `git stash`），并在第 1 步前断言 `git status --short` 为空。否则第 4 步 `git merge` 直接 `Aborting`（已实测 exit 2）。
2. **改 5.2 规则**：导师的 `handle_streaming_request` 必须插到我方 `@_timed_request("DELETE")` **上方**（而不是 union 成"装饰器 + 导师方法"）；校验器改为断言装饰器的 AST 目标包含 `do_DELETE`。
3. **改 5.1 规则**：按 `(provider, modelId)` 去重（不是 `id`），保留我方 id、采纳导师字段改动、`deepseek-flash` 置首；不要用 `dict(theirs)` + `json.dumps` 整文件重排。判据加两条：无 `(provider,modelId)` 重复；`/api/models` 第一条 == `deepseek-flash`（对应用户 H3 验收）。
4. **改 5.4 规则为 union**：保留我方约定 10/11 + 追加导师的 `# Compaction 压缩规则` 段，只丢弃重复的 python 路径行。
5. **对齐 5.3 第 2 处**：规则与文字二选一 —— 正确做法是"保留 `interface ApprovalRequest`、丢掉 `const DEFAULT_HERMES_MODEL`"，并在校验器里断言该标识符**不存在**。
6. **第 1 项判据的命令换成可用的**：`git grep -nE '^(<{7}|={7}$|>{7}|\|{7})' -- .`（已实测恰好 18 行、零误报），或 `git grep -nF -e "<<<<<<< " -e ">>>>>>> "` + `git grep -nE '^={7}$'`。文档原命令在 `-E` 下永远返回 0 行（假绿）。
7. **补 `npm install`**（`highlight.js` / `react-markdown` / `rehype-highlight` / `remark-gfm` 四个都缺），并在第 6/7 项之前执行。
8. **第 4 项升级为语义检查**（装饰器绑定），**第 2 项**加"无 `(provider,modelId)` 重复"，**第 8 项**加"第一条 == deepseek-flash"。

**P1 —— 不改会留下隐患或误判**

9. 第 3 项把解释器写死为 `D:\semester4\deploy\qmclaw\qmclaw\python.exe`（PATH 上的是 Python 3.14，不是约定 10 的 3.11.16）。
10. 跑第 7 项**之前**删掉工作树里的 `Agentic Workflow/qmclaw-web/tsconfig.tsbuildinfo`（`incremental: true` + 被导师覆盖），再单独提交"移出索引 + 加 .gitignore"。
11. 合并前决定 `.env` 的 `HERMES_HOME` 语义（当前 `data\hermes` vs 导师假设的项目根），并把"`/api/hermes/skills` 非空且能看到 `skills/` 下 19 个技能"写进验收。
12. 合并前决定 `memory/MEMORY.md`（网关运行时会改写）与 `auth.lock`（无任何代码引用）是否入库；若入库，接受"每次同步上游都冲突"的代价。
13. 网络姿势改用逐命令 `-c http.sslBackend=openssl -c http.sslVerify=false`，不要把 `sslVerify=false` 留在 `.git/config`（`origin` 本身也走 gh-proxy，文档里"push 走直连"与现状矛盾，需要时另行改 `origin` URL）。
14. 第 6 步前插入显式人机闸门（人工起服务 → 跑第 8/10 项 → 再 `--ff-only` 合回 `feat/c-real-machine`）。
15. 补两项轻量检查：4 个重命名测试文件的旧路径引用扫描（`docs` / `scripts` / `README.md`）；合并后对新 hermes 模块与 `hermes_service.server` 做一次 import 冒烟（覆盖 vendor/hermes-agent 版本问题）。
16. 第 9 项的离线出图建议加"与合并前图片肉眼比对"（§4.3：引擎被整份替换，`charts=1` 抓不到口径变化）。

> 附：本次审查新建的辅助脚本都在 `.merge-scratch\`（`review-*.py`）与探针仓库 `.merge-scratch\probe\mrepo`，均为本地可弃材料；未修改 `docs/UPSTREAM_MERGE_PLAN.md`，未触碰 `.analysis\`。
