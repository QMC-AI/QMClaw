# pipeline-test.ps1 - reproduce the full upstream-merge pipeline in a probe repo
#
# Why a probe repo: the DSH sandbox refuses to write the real .git, so we copy both
# object databases into a throwaway repo and run real git operations there.
#
# Usage:
#   .\pipeline-test.ps1 [-ProbeDir <path>] [-SkipPost]
#
# What it does:
#   1. build probe repo from <repo>/.git/objects + <repo>/.upstream-mirror/objects
#   2. real `git merge` of upstream main into user HEAD   -> expect 4 conflicts / 6 blocks
#   3. resolve with resolve-conflicts.py --write          -> expect all PASS, 0 unresolved
#   4. commit the merge
#   5. post-merge steps (8/19/21): re-add scratch ignores, untrack tsbuildinfo, untrack auth.lock
#   6. run probe-verify.py                                -> expect 13 PASS / 0 FAIL
#
# ASCII-only on purpose: Windows PowerShell reads .ps1 as ANSI, so non-ASCII
# output text breaks parsing.

param(
    [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$ProbeDir = "",
    [switch]$SkipPost
)

$ErrorActionPreference = 'Continue'

$USER_SHA = 'af90c1cabfcd45005b44bb4a389004102e494a3e'   # user feat/c-real-machine HEAD
$UP_SHA   = '03d838d23fbc95f6a8c727a3fece164976085820'   # upstream Secsix/QMClaw main
$PY       = 'D:\semester4\deploy\qmclaw\qmclaw\python.exe'
$tools    = $PSScriptRoot

if (-not $ProbeDir) { $ProbeDir = Join-Path $RepoRoot '.merge-scratch\pipeline-probe' }
$repo = Join-Path $ProbeDir 'repo'

Write-Output "root      = $RepoRoot"
Write-Output "probe     = $repo"
Write-Output "tools     = $tools"
Write-Output ""

if (-not (Test-Path (Join-Path $RepoRoot '.upstream-mirror\objects'))) {
    Write-Output "!! missing $RepoRoot\.upstream-mirror\objects"
    Write-Output "   create it first, e.g.:"
    Write-Output "   git -c http.sslBackend=openssl -c http.sslVerify=false clone --mirror https://gh-proxy.org/https://github.com/Secsix/QMClaw.git `"$RepoRoot\.upstream-mirror`""
    exit 2
}

# ---- 1) build probe repo -------------------------------------------------
if (Test-Path $ProbeDir) { Remove-Item -Recurse -Force $ProbeDir }
New-Item -ItemType Directory -Force -Path $repo | Out-Null
cmd /c "git init -q ""$repo"" 2>nul"
cmd /c "git -C ""$repo"" config core.autocrlf false"
cmd /c "git -C ""$repo"" config user.name pipeline"
cmd /c "git -C ""$repo"" config user.email pipeline@local"
Copy-Item (Join-Path $RepoRoot '.git\objects\*')             (Join-Path $repo '.git\objects') -Recurse -Force
Copy-Item (Join-Path $RepoRoot '.upstream-mirror\objects\*') (Join-Path $repo '.git\objects') -Recurse -Force
cmd /c "git -C ""$repo"" update-ref refs/heads/ours $USER_SHA"
cmd /c "git -C ""$repo"" update-ref refs/heads/theirs $UP_SHA"
cmd /c "git -C ""$repo"" checkout -q ours"
Write-Output "### 1) probe repo ready (checkout exit=$LASTEXITCODE)"

# ---- 2) real merge ------------------------------------------------------
Write-Output ""
Write-Output "### 2) real git merge"
cmd /c "git -C ""$repo"" merge --no-ff theirs -m ""merge(upstream): pipeline-test"" 2>&1" > $null
$mergeRc = $LASTEXITCODE
$conf = cmd /c "git -C ""$repo"" ls-files -u 2>nul" | ForEach-Object { ($_ -split "`t")[1] } | Sort-Object -Unique
# NOTE: never pipe a native command and then read $LASTEXITCODE - the pipeline
# overwrites it with the last cmdlet's status. Redirect instead.
Write-Output "   merge exit=$mergeRc (1 = conflicts, expected)"
Write-Output "   conflicted files: $($conf.Count)"
$conf | ForEach-Object { Write-Output "      - $_" }
Write-Output "   conflict blocks:"
cmd /c "git -C ""$repo"" grep -c ""^<<<<<<<"" 2>nul" | ForEach-Object { Write-Output "      $_" }
if ($conf.Count -ne 4) {
    Write-Output "   !! expected 4 conflicted files."
    Write-Output "   !! If you see a 5th (.gitignore), step 0 was not done cleanly - see UPSTREAM_MERGE_PLAN.md section 6.0"
}

# ---- 3) resolve ---------------------------------------------------------
Write-Output ""
Write-Output "### 3) resolve-conflicts.py --write"
& $PY (Join-Path $tools 'resolve-conflicts.py') $repo --write 2>&1 | Select-Object -Last 8
$resolveRc = $LASTEXITCODE
Write-Output "   resolve exit=$resolveRc"
$left = cmd /c "git -C ""$repo"" ls-files -u 2>nul" | Where-Object { $_ }
if ($left) { Write-Output "   !! STILL UNRESOLVED:"; $left | ForEach-Object { Write-Output "      $_" } }
else { Write-Output "   0 unresolved OK" }

# ---- 4) commit ----------------------------------------------------------
Write-Output ""
Write-Output "### 4) commit the merge"
cmd /c "git -C ""$repo"" commit -q --no-edit 2>&1"
Write-Output "   commit exit=$LASTEXITCODE"
cmd /c "git -C ""$repo"" log --oneline -1 2>&1" | ForEach-Object { Write-Output "   $_" }

# ---- 5) post-merge steps ------------------------------------------------
if (-not $SkipPost) {
    Write-Output ""
    Write-Output "### 5) post-merge steps (8 / 19 / 21)"

    # step 8: ignore rules back
    # NOTE: .upstream-mirror/ is the upstream bare mirror - a LONG-LIVED local cache that
    # docs/merge_verify_tools/* scripts depend on. It must stay ignored but must NOT be
    # deleted; .merge-scratch/ and .analysis/ are the disposable analysis dirs.
    Add-Content -Path (Join-Path $repo '.gitignore') -Encoding utf8 `
        -Value "`n# local-only upstream merge artifacts (mirror is a reusable cache - keep it)`n.upstream-mirror/`n.merge-scratch/`n.analysis/`n"
    cmd /c "git -C ""$repo"" add .gitignore"
    cmd /c "git -C ""$repo"" commit -q -m ""chore: ignore upstream merge scratch dirs"""
    Write-Output "   step 8  (ignore scratch dirs)          exit=$LASTEXITCODE"

    # step 19: untrack build artifact
    cmd /c "git -C ""$repo"" rm --cached -q ""Agentic Workflow/qmclaw-web/tsconfig.tsbuildinfo"" 2>&1" > $null
    cmd /c "git -C ""$repo"" commit -q -m ""chore: untrack build artifact tsconfig.tsbuildinfo"""
    Write-Output "   step 19 (untrack tsconfig.tsbuildinfo) exit=$LASTEXITCODE"

    # step 21: untrack runtime artifact
    cmd /c "git -C ""$repo"" rm --cached -q auth.lock 2>&1" > $null
    cmd /c "git -C ""$repo"" commit -q -m ""chore: untrack runtime artifact auth.lock"""
    Write-Output "   step 21 (untrack auth.lock)            exit=$LASTEXITCODE"
}

# ---- 6) verify ----------------------------------------------------------
Write-Output ""
Write-Output "### 6) probe-verify.py"
& $PY (Join-Path $tools 'probe-verify.py') $repo 2>&1 | Select-Object -Last 30
$verifyRc = $LASTEXITCODE
Write-Output "   verify exit=$verifyRc"
Write-Output ""
Write-Output "probe repo: $repo"
