# 上游融合验证工具（只读 / 可复现）

> 来源：2026-10-08 那次「把导师上游 `Secsix/QMClaw` 融合进 `feat/c-real-machine`」的分析。
> 方案文档：`docs/UPSTREAM_MERGE_PLAN.md`；独立复核：`docs/UPSTREAM_MERGE_PLAN_REVIEW.md`。
> 分析过程中这些脚本原本放在 `.merge-scratch/`（未跟踪、合并完成后会被清理），这里留一份归档，便于日后复现。
>
> **全部脚本默认只读**：除 `resolve-conflicts.py --write` 外，都不修改任何文件。

## 本机环境前提

| 项 | 值 |
|----|----|
| Python | `D:\semester4\deploy\qmclaw\qmclaw\python.exe`（约定 10，3.11.16） |
| 控制台编码 | 脚本里已 `sys.stdout.reconfigure(encoding='utf-8')`；PowerShell 里建议先 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8` |
| 运行 ps1 | 先 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force` |
| **沙箱限制** | 本机 DSH 沙箱**禁止写 `.git`**（`git fetch/add/commit/branch` 全部被拒）。因此需要一个「探针仓库」来做真实 merge —— 见下 |
| 本地 `git fetch <本地路径>` | 会因 msys `sh.exe` 无法建 signal pipe 而失败；所以用**复制 object DB** 的方式建探针，不用 fetch |

## 目录里有什么

| 文件 | 说明 |
|------|------|
| `README.md` | 本文件：环境前提、探针仓库建法、8 个实测过的坑 |
| `ARCHIVE_CHECK.md` | 搬运/新增脚本的哈希校验与运行结果 |
| **`MERGED_CONTENT_PREVIEW.md`** | **合并会得到的实际内容预览**：4 个冲突文件被解成什么样（模型 10 条、装饰器绑定、并集结果）+ 整体改动面。**跑真合并前先看这份** |
| 13 个脚本 | 见下两张表 |

## 脚本清单（日常用这 7 个）

| 脚本 | 作用 | 用法 |
|------|------|------|
| **`run-merge.ps1`** | **在真实仓库上执行合并**（第 0~9 步一条龙：还原+提交 → 备份分支 → fetch → 分同步分支 → merge → 解冲突 → 收尾 → 自动验证 → 人工闸门 → 快进合回）。**必须在沙箱外运行**。支持 `-DryRun` 在探针仓库上演练 | `.\run-merge.ps1`，人验通过后 `.\run-merge.ps1 -Continue` |
| **`pipeline-test.ps1`** | **一条命令跑完整流程**（探针仓库版）：建探针 → merge → 解冲突 → 提交 → 后处理 → 验证 | `.\pipeline-test.ps1 -RepoRoot D:\semester4\QMClaw` |
| `resolve-conflicts.py` | **解 6 处冲突**（方案第五节修正版规则的实现，带语义校验）。默认试算，`--write` 才写回 | `python resolve-conflicts.py <仓库路径> [--write]` |
| `probe-verify.py` | 在合并后的工作树上跑验证清单里可自动化的项（第 1-6、12、14-21 项） | `python probe-verify.py [仓库路径]` |
| `verify-doc-claims.py` | 把方案文档里的 32 条**事实主张**逐条对着 git 数据复核 | `python verify-doc-claims.py` |
| `tw3.py` | 逐 blob 三方合并**预判**（每个 blob 校验与 `git cat-file -s` 报的字节数一致，避免编码/截断造成假冲突） | `python tw3.py` |
| `gi3way.py` | 定论 `.gitignore` 三方合并是 CLEAN 还是 CONFLICT（按 `.gitignore` 的特殊性单列） | `python gi3way.py` |

### 过程脚本（复现方案里"已实测"的结论用，日常不需要跑）

| 脚本 | 作用 |
|------|------|
| `probe-sync.ps1` | 建探针仓库（复制两侧 object DB）并跑真实 `git merge`（功能已并入 `pipeline-test.ps1`） |
| `resolve.py` | **初稿版**解冲突 —— 保留它是为了复现"初稿校验器给出 3 个假 PASS"这个教训 |
| `resolve-fixed.py` | **修正版**解冲突 —— 第五节"修正版规则全部 PASS"这条结论的证据就是它（`resolve-conflicts.py` 是它的可复用版） |
| `verify-clean-merges.py` | 内容级完整性：7 个"自动合并"文件里双方里程碑字符串是否都还在 |
| `show-conflicts.ps1` | 打印 6 处冲突块的双方内容（定规则时用） |
| `probe-resolve.py` | 在真实 merge 的冲突标记上应用修正版规则（`resolve-conflicts.py` 的前身） |

> `pipeline-test.ps1` 的实测输出（2026-10-08）：`merge exit=1`（4 个冲突文件 / 6 块）→ `resolve exit=0`（全 PASS、0 未解决）→ 3 个后处理提交 `exit=0` → `verify exit=0`（**13 PASS / 0 FAIL / 4 SKIP / 2 WARN**）。
>
> ⚠️ 脚本里的两条坑（写 PowerShell 时容易犯，已修）：
> ① **不要对原生命令用管道后再读 `$LASTEXITCODE`** —— 管道会用最后一个 cmdlet 的状态把它覆盖掉，导致打印出来的 exit 码是假的（本脚本第一版就踩了，用 `> $null` 重定向才对）；
> ② **`.ps1` 里不要写中文输出** —— Windows PowerShell 按 ANSI 读脚本，非 ASCII 会破坏解析（本脚本刻意全 ASCII）。

## 「探针仓库」怎么来的（沙箱内做真实 merge 的办法）

沙箱写不了真实 `.git`，但可以**复制对象库**到工作区内的一个新仓库，在那里跑真实 git 操作：

```powershell
$root = 'D:\semester4\QMClaw'
$repo = "$root\.merge-scratch\probe\repo"
New-Item -ItemType Directory -Force -Path $repo | Out-Null
git init -q $repo
git -C $repo config core.autocrlf false
git -C $repo config user.name probe
git -C $repo config user.email probe@local
Copy-Item "$root\.git\objects\*"                 "$repo\.git\objects" -Recurse -Force
Copy-Item "$root\.upstream-mirror\objects\*"     "$repo\.git\objects" -Recurse -Force
git -C $repo update-ref refs/heads/ours   af90c1cabfcd45005b44bb4a389004102e494a3e
git -C $repo update-ref refs/heads/theirs 03d838d23fbc95f6a8c727a3fece164976085820
git -C $repo checkout -q ours
git -C $repo merge --no-ff theirs -m "merge(upstream): probe"   # 预期 4 文件冲突 / 6 块
python resolve-conflicts.py $repo --write
git -C $repo commit --no-edit
python probe-verify.py $repo                                    # 预期 13 PASS / 0 FAIL
```

其中 `af90c1c` = 你的 `feat/c-real-machine` HEAD，`03d838d` = 上游 `Secsix/QMClaw` 的 `main`。
`.upstream-mirror/` 是上游的裸镜像（`git clone --mirror`，本机需带
`-c http.sslBackend=openssl -c http.sslVerify=false` 并经 `gh-proxy.org`，直连会报
`schannel: SEC_E_NO_CREDENTIALS`）。

## 已知的「坑」（都实测过，别再踩）

1. **`.gitignore` 的三方合并对输入敏感**：只提交 HEAD 版 → CLEAN；把追加在**文件末尾**的忽略规则一起提交 → CONFLICT（差异区与导师第 8 行的新增挨上）。
2. **`git grep -E` 里 `\|` 是字面竖线**：`'^(<{7}\|={7}\|>{7})'` 会返回 0 行（假绿）。用 `'^(<{7}|={7}$|>{7}|\|{7})'`，或去掉转义后另行排除 `=====` 分隔行。
3. **还原文件要用字节级重定向**：`git show HEAD:x | Set-Content -Encoding utf8 -NoNewline` 会加 BOM 又去掉末尾换行（实测 603 → 561 字节且 git 仍认为被改）。用 `cmd /c "git cat-file blob HEAD:x > x"`。
4. **`ast.parse` 通过 ≠ 代码对**：`@_timed_request("DELETE")` 挂到别的函数上完全合法。必须断言装饰器的 AST 绑定目标。
5. **按 `id` 合并模型配置是错的**：上游把 id 整套重编号了，要按 `(provider, modelId)` 语义去重。
6. **不要在 `.ps1` 里写中文输出**：Windows PowerShell 按 ANSI 读脚本文件，非 ASCII 字符串会破坏解析（实测报"字符串缺少终止符"）。脚本一律纯 ASCII，中文放到 Markdown 里。
7. **不要对原生命令用管道后再读 `$LASTEXITCODE`**：管道会用最后一个 cmdlet 的状态覆盖它，打印出来的 exit 码是假的（`pipeline-test.ps1` 第一版踩过）。用 `> $null` 重定向。
8. **写工作区文件要走 read/write 工具，别用 `Copy-Item`/`cp`**：沙箱会以 `file access denied` 拒绝，且工作区写需要经过版本守卫。搬运文件时用 read 读全文 → write 写目标，然后 `Get-FileHash` 比对确认逐字节一致（本次 6 个脚本就是这么搬的，见 `ARCHIVE_CHECK.md`）。
9. **`.gitignore` 里有 `.claude/`，而 `git add .claude/progress.md` 会被判为 "ignored"** —— 对**尚未进入索引**的路径，`git add <path>` 会撞忽略规则并失败；已跟踪文件不受影响（真实仓库里 `progress.md` 本就已跟踪，`git check-ignore` 对它退出码 1，所以"恰好"能过）。**别依赖这个巧合：第 0 步一律用 `git add -u`**。这是 `run-merge.ps1` 第 0 步的实测教训（详见 `ARCHIVE_CHECK.md`）。
10. **`^` 和 `--` 在 `cmd /c` 里会被吃掉**：`git diff A^1 B` 通过 `cmd /c` 执行会变成 `A1`；`git diff ... -- <path>` 配 `cmd` 重定向也会解析失败。取父提交用 `git log -1 --pretty=%P <sha>`（不用 `^`），跑 diff 直接用 PowerShell 调 git 并用 `--output=<file>`。
