# 在沙箱内构造探针仓库并跑真实 git merge（不写真实 .git）
$ErrorActionPreference = 'Continue'
$root    = 'D:\semester4\QMClaw'
$probe   = Join-Path $root '.merge-scratch\syncprobe'
$repo    = Join-Path $probe 'repo'
$USER_SHA = 'af90c1cabfcd45005b44bb4a389004102e494a3e'
$UP_SHA   = '03d838d23fbc95f6a8c727a3fece164976085820'

if (Test-Path $probe) { Remove-Item -Recurse -Force $probe }
New-Item -ItemType Directory -Force -Path $repo | Out-Null

cmd /c "git init -q ""$repo"" 2>nul"
cmd /c "git -C ""$repo"" config core.autocrlf false"
cmd /c "git -C ""$repo"" config user.name probe"
cmd /c "git -C ""$repo"" config user.email probe@local"

# 复制两侧 object DB
Copy-Item (Join-Path $root '.git\objects\*') (Join-Path $repo '.git\objects') -Recurse -Force
Copy-Item (Join-Path $root '.upstream-mirror\objects\*') (Join-Path $repo '.git\objects') -Recurse -Force

# 建立引用
cmd /c "git -C ""$repo"" update-ref refs/heads/ours $USER_SHA"
cmd /c "git -C ""$repo"" update-ref refs/heads/theirs $UP_SHA"

# 检出 ours
cmd /c "git -C ""$repo"" checkout -q ours 2>&1"
Write-Output "checkout ours exit=$LASTEXITCODE"

# 真实 merge
cmd /c "git -C ""$repo"" merge --no-ff theirs -m ""merge(upstream): probe"" 2>&1"
Write-Output "merge exit=$LASTEXITCODE (1=有冲突, 2=中止)"
Write-Output ""
Write-Output "=== 冲突文件清单（真实 merge 产出） ==="
cmd /c "git -C ""$repo"" diff --name-only --diff-filter=U"
Write-Output ""
Write-Output "=== 冲突块计数 ==="
cmd /c "git -C ""$repo"" grep -c ""^<<<<<<<"" -- . 2>nul"
Write-Output ""
Write-Output "=== 探针仓库位置: $repo ==="
