# 归档校验：`.merge-scratch/` → `docs/merge_verify_tools/`

> 搬运方式：read 工具读源文件全文 → write 工具写目标文件（逐字符原样，未改注释/代码/空行/换行符）。
> 校验方式：`Get-FileHash -Algorithm SHA256`，源与目标各自计算后比对**完整 64 位**哈希；下表只列前 16 位。
> 源文件属性：LF 换行、无 BOM、末尾有换行（6 个文件一致），目标文件属性相同。

| 文件名 | 源 SHA256(前16位) | 目标 SHA256(前16位) | 是否一致 | 源字节数 | 目标字节数 |
|--------|------------------|--------------------|---------|---------|-----------|
| resolve-conflicts.py | 17730752F0108A92 | 17730752F0108A92 | ✅ 一致 | 12018 | 12018 |
| probe-verify.py | 6AD833A1A043451C | 6AD833A1A043451C | ✅ 一致 | 12219 | 12219 |
| verify-doc-claims.py | 3E82E06543583C07 | 3E82E06543583C07 | ✅ 一致 | 7295 | 7295 |
| tw3.py | BAD5EC26C1E6C973 | BAD5EC26C1E6C973 | ✅ 一致 | 4836 | 4836 |
| gi3way.py | 70BC20E808AC038E | 70BC20E808AC038E | ✅ 一致 | 3288 | 3288 |
| probe-sync.ps1 | E3959765F8C7DDCC | E3959765F8C7DDCC | ✅ 一致 | 1624 | 1624 |

**总结：6/6 一致**（SHA256 全等、字节数全等）—— 6 个脚本已逐字节原样归档到 `docs/merge_verify_tools/`；`.merge-scratch/` 源文件未改动，`docs/merge_verify_tools/README.md` 未改动。

---

## 追加：`pipeline-test.ps1`（非搬运，是**新写的参数化脚本**）

它把原来只在 `.merge-scratch/` 里的 `e2e-run.ps1` + `e2e-post.ps1` 合并、参数化，目标是"一条命令复现完整流程"。
因此**没有源文件可比对哈希**，改为记录它自身的属性与**运行结果**：

| 项 | 值 |
|----|----|
| 路径 | `docs/merge_verify_tools/pipeline-test.ps1` |
| SHA256(前16位) | **A2DC92DB30D4819B** |
| 字节数 | **6527** |
| 编码/换行 | 无 BOM、LF、末尾有换行、**纯 ASCII（0 个非 ASCII 字节）** |
| 为何纯 ASCII | Windows PowerShell 按 ANSI 读 `.ps1`，中文字符串会破坏解析（实测踩过） |

> 📌 **哈希已更新（2026-10-08 晚）**：本表原记 `C81AA59428EEFD89` / 6247 字节，是**修 bug 之前**的版本。
> 该脚本第一版对原生命令用了管道再读 `$LASTEXITCODE`，管道会把它覆盖成最后一个 cmdlet 的状态 ——
> 打印出来的 exit 码是假的（流程本身跑对，但诊断信息误导）。改成 `> $null` 重定向后文件变为
> `A2DC92DB30D4819B` / 6527 字节，**并重跑确认**下面的运行结果仍然成立。
> 这个不一致是脚本搬运/校验的 teammate 在核对时发现的（它按"只追加不改既有内容"的约束只写了观察行）。

**运行结果（2026-10-08 实测）**：

```
merge exit=1        (4 个冲突文件 / 6 块，符合预期)
resolve exit=0      (4 个文件全 PASS、0 未解决冲突)
step 8/19/21 exit=0 (三个后处理提交)
verify exit=0       (13 PASS / 0 FAIL / 4 SKIP / 2 WARN)
```

用法：`.\pipeline-test.ps1 -RepoRoot D:\semester4\QMClaw`（需要先有 `.upstream-mirror/`，脚本会在缺失时提示创建命令）

---

## 追加 3：`run-merge.ps1`（新写的"真实执行"脚本）

`pipeline-test.ps1` 跑的是**探针仓库**（用来验证流程本身）；`run-merge.ps1` 跑的是**真实仓库**（第 0~9 步一条龙），
供用户在沙箱外执行真正的合并。同样**没有源文件可比对哈希**，记录自身属性与运行结果：

| 项 | 值 |
|----|----|
| 路径 | `docs/merge_verify_tools/run-merge.ps1` |
| 字节数 | 15386 |
| 编码/换行 | 无 BOM、LF、末尾有换行、**纯 ASCII（0 个非 ASCII 字节）** |
| 语法 | PowerShell 解析器 0 错误 |

**演练结果（`-DryRun`，2026-10-08 实测）**：

```
STEP 0  tree clean                        OK
STEP 1  backup branch created             OK
STEP 2  upstream/main = 03d838d           OK (dry run 用预置 ref)
STEP 3  switched to feat/upstream-sync    OK
STEP 4  conflicted files: 4               OK (与预期一致)
STEP 5  dry pass all PASS → 0 unresolved → merge committed a6596bb   OK
STEP 6  tsbuildinfo / auth.lock untracked OK
STEP 7  probe-verify 跑完 + 人工闸门打印   OK
（-Continue 路径另测：STEP 8 提交忽略规则、STEP 9 快进合回 feat/c-real-machine，ff exit=0）
最终 probe-verify: 13 PASS / 0 FAIL / 4 SKIP / 2 WARN
```

### 第 0 步的「脏工作树」路径另测（本轮补，并抓到一个真 bug）

`-DryRun` 每次都从干净状态起步，所以第 0 步真正要处理的分支（工作树脏）一直没被测到。于是单独建了一个
**与真实仓库同形**的脏探针（3 个已修改的已跟踪文件：`.claude/progress.md`、`.gitignore`、`docs/BACKLOG.md`；
`.gitignore` 已领先 HEAD 若干行），实测：

**❌ 第一版写法 `git add <路径>` 会失败**：

```
The following paths are ignored by one of your .gitignore files:
.claude
hint: Use -f if you really want to add them.
```

原因：`.gitignore` 里有 `.claude/`，而对**尚未进入索引**的路径，`git add <path>` 会被判为"ignored"。
真实仓库里之所以能过，只因为 `progress.md` 本来就是**已跟踪**文件（已核实：`git check-ignore .claude/progress.md`
退出码 1，即未被忽略；`git status` 也把它报成 `M`）。**依赖这个巧合太脆**。

**✅ 改成 `git add -u`（已跟踪文件的更新）后实测通过**：

```
.gitignore 还原 = 603 字节（与索引逐字节一致）
add -u exit=0  →  commit exit=0  →  提交后脏文件数 = 0   PASS
随后真实 merge：CONFLICT ×4，冲突文件数 = 4（与方案预期一致，未 Aborting）
```

> 这条改动把第 0 步从"恰好能过"变成"结构上不会踩忽略规则"。
> 教训同第十二节那条：**没走过的分支等于没测过**。

**⚠️ 它必须由人在沙箱外运行**：脚本的 `git config/remote/fetch/branch/merge/add/commit` 全部要写 `.git`，
DSH 沙箱一律拒绝。演练（`-DryRun`）不受此限，因为它只在工作区内的探针仓库上操作。

> 脚本第一版**因为写了中文而解析失败**（Windows PowerShell 按 ANSI 读 `.ps1`，实测 2950 个非 ASCII 字节 → 3 处语法错误）。
> 改成纯 ASCII 后解析 0 错误。这条已记进 README 的"坑"清单。

---

## 追加 2：补齐 5 个脚本

> 搬运方式同第 1 节：read 工具读源文件全文 → write 工具写目标文件（逐字符原样，未改注释/代码/空行/换行符）。
> 校验方式：`Get-FileHash -Algorithm SHA256`，源与目标各自计算后比对**完整 64 位**哈希；下表只列前 16 位。
> 源文件属性：无 BOM、LF 换行、无行尾空白、末尾有 LF（5 个文件一致），目标文件因哈希全等而属性相同。

| 文件名 | 源 SHA256(前16位) | 目标 SHA256(前16位) | 是否一致 | 源字节数 | 目标字节数 |
|--------|------------------|--------------------|---------|---------|-----------|
| resolve.py | 086C16C9F19A2812 | 086C16C9F19A2812 | ✅ 一致 | 7924 | 7924 |
| resolve-fixed.py | 3D674E307C17DFE7 | 3D674E307C17DFE7 | ✅ 一致 | 10332 | 10332 |
| verify-clean-merges.py | 936861C208AD4C44 | 936861C208AD4C44 | ✅ 一致 | 3756 | 3756 |
| show-conflicts.ps1 | 70092C23F4232986 | 70092C23F4232986 | ✅ 一致 | 1412 | 1412 |
| probe-resolve.py | BC2FCD0FBB7DEBFE | BC2FCD0FBB7DEBFE | ✅ 一致 | 6191 | 6191 |

**总结：5/5 一致**（SHA256 全等、字节数全等）—— 5 个脚本已逐字节原样归档到 `docs/merge_verify_tools/`；`.merge-scratch/` 源文件未改动，目录内既有的 7 个脚本 + README.md + 本节以上内容均未改动（既有 6 个脚本哈希复核仍与第 1 节表格一致）。

> **附带观察（与本次搬运无关，未修改既有内容）**：本节核对时发现 `pipeline-test.ps1` 当前 SHA256(前16位)=`A2DC92DB30D4819B`、6527 字节，与上方第 2 节记录的 `C81AA59428EEFD89`、6247 字节**不一致**；该文件 mtime 为 20:07:20，早于本次搬运（20:16 起），即其被改动发生在第 2 节写入之后、且不是本次操作所为。该行既有记录按约定保持原样，是否更新由归档负责人决定。
