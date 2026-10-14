# run-merge.ps1 - execute the upstream merge in the REAL repo (run OUTSIDE the DSH sandbox)
#
# Why outside the sandbox: DSH denies every write to .git (git config / remote add /
# fetch / branch / merge / add / commit). Run this in a normal PowerShell window, or
# switch the session to full access first.
#
# It automates sections 4 + 6 of docs/UPSTREAM_MERGE_PLAN.md, in the tested order:
#   0     restore .gitignore to HEAD, commit ALL modified tracked files, assert clean
#   1     backup branch (hard rollback point)
#   2     add upstream remote + fetch (per-command openssl flags; nothing written to config)
#   3     create feat/upstream-sync
#   4     merge upstream/main            -> expect exactly 4 conflicted files / 6 blocks
#   5     resolve-conflicts.py --write   -> expect all PASS, 0 unresolved
#   6     post-merge chores: drop stale tsbuildinfo, untrack auth.lock
#   7     run probe-verify.py, then STOP for the human gate (service-dependent checks)
#   8     re-add scratch ignore rules as its own commit
#   9     fast-forward feat/c-real-machine (only with -Continue), optionally clean scratch
#
# Safety: refuses to run unless the tree is in the expected state; aborts on any
# unexpected conflict set; never uses `git add -A`.
#
# ASCII-only on purpose: Windows PowerShell reads .ps1 as ANSI, so non-ASCII text
# breaks parsing (verified - the first draft of this file failed that way).

param(
    [string]$RepoRoot = 'D:\semester4\QMClaw',
    [switch]$DryRun,        # rehearse on a throwaway probe repo (no real-repo writes)
    [switch]$Continue,      # step 9: fast-forward feat/c-real-machine
    [switch]$SkipPost,      # skip the step 6 chores
    [switch]$KeepScratch    # keep .merge-scratch / .analysis at the end
)

$ErrorActionPreference = 'Stop'

$UPSTREAM_URL  = 'https://gh-proxy.org/https://github.com/Secsix/QMClaw.git'
$UP_SHA_EXPECT = '03d838d23fbc95f6a8c727a3fece164976085820'
$PY            = 'D:\semester4\deploy\qmclaw\qmclaw\python.exe'
$TOOLS         = Join-Path $RepoRoot 'docs\merge_verify_tools'
$BRANCH_MAIN   = 'feat/c-real-machine'
$BRANCH_SYNC   = 'feat/upstream-sync'
$EXPECT_CONFLICTS = @(
    'Agentic Workflow/qmclaw-server/config/model_configs.json',
    'Agentic Workflow/qmclaw-server/services/base/base_service.py',
    'Agentic Workflow/qmclaw-web/src/components/HermesChatPanel.tsx',
    'CLAUDE.md'
)

function Say  ($m) { Write-Host $m }
function Hdr  ($m) { Write-Host ""; Write-Host "### $m" -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "   OK   $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "   WARN $m" -ForegroundColor Yellow }
function Die  ($m) { Write-Host "   FAIL $m" -ForegroundColor Red; exit 1 }

function GitEx {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    # Native commands write warnings to stderr (e.g. "LF will be replaced by CRLF").
    # With $ErrorActionPreference='Stop' those become TERMINATING errors and kill the
    # script even though git exited 0. Local Continue + explicit exit-code check below.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & git -C $script:Target @GitArgs 2>&1
        $script:LastGitExit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return $out
}

function GitMust {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $out = GitEx @GitArgs
    if ($script:LastGitExit -ne 0) { Die ("git " + ($GitArgs -join ' ') + " failed:`n" + $out) }
    return $out
}

function LastLine ($v) { return (($v | Out-String).Trim()) }

# ---------------------------------------------------------------- setup
if (-not (Test-Path (Join-Path $RepoRoot '.git'))) { Die "not a git repo: $RepoRoot" }
if (-not (Test-Path $PY)) { Die "python not found: $PY" }

if ($DryRun) {
    Say "==== DRY RUN: rehearse on a throwaway probe repo (real .git untouched) ===="
    $probe = Join-Path $RepoRoot '.merge-scratch\run-merge-probe'
    $probeRepo = Join-Path $probe 'repo'
    if (Test-Path $probe) { Remove-Item -Recurse -Force $probe }
    New-Item -ItemType Directory -Force -Path $probeRepo | Out-Null
    if (-not (Test-Path (Join-Path $RepoRoot '.upstream-mirror\objects'))) {
        Die "missing .upstream-mirror\objects - build the mirror first (see README)"
    }
    & git init -q $probeRepo
    & git -C $probeRepo config core.autocrlf false
    & git -C $probeRepo config user.name dryrun
    & git -C $probeRepo config user.email dryrun@local
    Copy-Item (Join-Path $RepoRoot '.git\objects\*')             (Join-Path $probeRepo '.git\objects') -Recurse -Force
    Copy-Item (Join-Path $RepoRoot '.upstream-mirror\objects\*') (Join-Path $probeRepo '.git\objects') -Recurse -Force
    $realHead = LastLine (& git -C $RepoRoot rev-parse HEAD)
    & git -C $probeRepo update-ref "refs/heads/$BRANCH_MAIN" $realHead
    & git -C $probeRepo update-ref 'refs/remotes/upstream/main' $UP_SHA_EXPECT
    & git -C $probeRepo checkout -q $BRANCH_MAIN
    $script:Target = $probeRepo
    $script:Dry = $true
    Say "   probe = $probeRepo"
} else {
    $script:Target = (Resolve-Path $RepoRoot).Path
    $script:Dry = $false
}

Say "repo = $script:Target"

# ---------------------------------------------------------------- step 0
Hdr "STEP 0: restore .gitignore, commit every modified tracked file, assert clean"

$dirty = @(GitEx status --short --untracked-files=no | Where-Object { $_ -match '\S' })
if ($dirty.Count -gt 0) {
    Say ("   modified tracked files (" + $dirty.Count + "):")
    $dirty | ForEach-Object { Say ("     " + $_) }

    # .gitignore MUST match HEAD, otherwise step 4 aborts with "would be overwritten"
    if ($dirty -match '^\s*M\s+\.gitignore$') {
        Say "   -> restore .gitignore from HEAD (byte-exact redirection: no BOM, keep trailing newline)"
        & cmd /c "git -C `"$script:Target`" cat-file blob HEAD:.gitignore > `"$script:Target\.gitignore`""
        $len = (Get-Item (Join-Path $script:Target '.gitignore')).Length
        if ($len -ne 603) { Warn ".gitignore is $len bytes after restore (HEAD version should be 603) - check manually" }
        else { Ok ".gitignore restored to 603 bytes" }
    }

    $paths = @($dirty | ForEach-Object { ($_ -replace '^\s*\S+\s+', '').Trim('"') })

    # Stage modified tracked files with `git add -u` rather than `git add <path>`.
    # Why: .gitignore contains `.claude/`, and `git add <path>` is rejected as "ignored"
    # for a path that is not in the index. Already-tracked files are unaffected by
    # .gitignore, so `-u` is the robust form. (Verified in the real repo:
    # `git check-ignore .claude/progress.md` exits 1, i.e. not ignored, because it is tracked.)
    GitMust add -u | Out-Null

    # Anything reported as modified but still untracked (e.g. the alignment doc) needs an
    # explicit add; --force in case a rule matches it.
    foreach ($p in $paths) {
        $tracked = LastLine (GitEx ls-files -- $p)
        if (-not $tracked) {
            GitEx add --force -- $p | Out-Null
            Ok ("added untracked file: " + $p)
        }
    }

    GitMust commit -q -m "docs: merge plan wrap-up (BACKLOG group G, progress log, alignment summary)" | Out-Null
    Ok ("committed " + $paths.Count + " file(s)")
}
$stillDirty = @(GitEx status --short --untracked-files=no | Where-Object { $_ -match '\S' })
if ($stillDirty.Count -gt 0) { Die ("tree still dirty, merge would abort:`n" + ($stillDirty -join "`n")) }
Ok "tree clean (no modified tracked files)"

# ---------------------------------------------------------------- step 1
Hdr "STEP 1: backup branch (hard rollback point)"
$branch  = LastLine (GitMust rev-parse --abbrev-ref HEAD)
$headSha = LastLine (GitMust rev-parse HEAD)
Say ("   branch = " + $branch)
Say ("   HEAD   = " + $headSha)
if ($branch -ne $BRANCH_MAIN) { Warn ("not on " + $BRANCH_MAIN + " - confirm this is intended") }
$bk = "backup/pre-upstream-merge-" + (Get-Date -Format 'yyyyMMdd-HHmmss')
GitMust branch $bk | Out-Null
Ok ("created backup branch " + $bk)
Say ("   rollback: git -C `"$script:Target`" reset --hard " + $bk)

# ---------------------------------------------------------------- step 2
Hdr "STEP 2: add upstream remote and fetch (per-command openssl; nothing written to config)"
if ($script:Dry) {
    Ok "dry run: using the prepared refs/remotes/upstream/main"
} else {
    $remotes = LastLine (GitMust remote)
    if (($remotes -split "`n") -notcontains 'upstream') {
        GitMust remote add upstream $UPSTREAM_URL | Out-Null
        Ok "added remote upstream"
    } else { Ok "remote upstream already present" }

    # --no-recurse-submodules matters here: vendor/{hermes-agent,QubitClient,Blueprint} are
    # plain directories with no .git (the submodules were never initialized), so a recursive
    # fetch prints "Could not access submodule ..." and exits 1 EVEN THOUGH the superproject
    # ref was fetched fine. Judge success by the ref, not by the exit code alone.
    $fetchOut = GitEx -c http.sslBackend=openssl -c http.sslVerify=false fetch --no-recurse-submodules upstream main
    $fetchRc = $script:LastGitExit
    $refOk = LastLine (GitEx rev-parse --verify --quiet refs/remotes/upstream/main)
    if (-not $refOk) {
        Die ("fetch failed and refs/remotes/upstream/main is absent (network/proxy?):`n" + $fetchOut)
    }
    if ($fetchRc -ne 0) {
        Warn "fetch exited $fetchRc but the upstream ref is present - continuing (submodule noise?)"
    }
    Ok "fetch ok"
}
$upSha = LastLine (GitMust rev-parse refs/remotes/upstream/main)
Say ("   upstream/main = " + $upSha)
if ($upSha -ne $UP_SHA_EXPECT) {
    Warn ("upstream is no longer " + $UP_SHA_EXPECT + " - the mentor pushed new commits")
    Warn "the conflict set may differ; read section 14 of UPSTREAM_MERGE_PLAN.md first"
    if (-not $script:Dry) {
        $ans = Read-Host "   continue with the merge anyway? (y/N)"
        if ($ans -ne 'y') { Die "aborted by your choice (no merge performed)" }
    }
}

# ---------------------------------------------------------------- step 3
Hdr "STEP 3: create sync branch $BRANCH_SYNC"
$exists = LastLine (GitMust branch --list $BRANCH_SYNC)
if ($exists) { Die "$BRANCH_SYNC already exists - resolve that first; I will not delete branches" }
GitMust checkout -q -b $BRANCH_SYNC | Out-Null
Ok ("switched to " + $BRANCH_SYNC)

# ---------------------------------------------------------------- step 4
Hdr "STEP 4: real merge (expect exactly 4 conflicted files / 6 blocks)"
$mergeOut = GitEx merge upstream/main --no-ff -m "merge(upstream): integrate mentor upstream (Hermes extensions); see docs/UPSTREAM_MERGE_PLAN.md"
$mergeRc = $script:LastGitExit
if ($mergeRc -eq 0) {
    Warn "merge finished with ZERO conflicts - not what the plan expects (4 files)"
    Warn "either upstream changed, or these conflicts were already resolved. Verify manually."
    exit 0
}

$unresolved = @(GitMust ls-files -u | ForEach-Object { ($_ -split "`t")[1] } | Sort-Object -Unique)
Say ("   conflicted files: " + $unresolved.Count)
$unresolved | ForEach-Object { Say ("     - " + $_) }

$unexpected = @($unresolved | Where-Object { $EXPECT_CONFLICTS -notcontains $_ })
if ($unexpected.Count -gt 0) {
    Die ("unexpected conflicted files: " + ($unexpected -join ', ') + "`n" +
         "most likely step 0 did not restore .gitignore cleanly (see plan section 6.0).`n" +
         "fix: git merge --abort, then rerun this script; or resolve those by hand.")
}
Ok "conflict set matches the plan (4 files)"

# ---------------------------------------------------------------- step 5
Hdr "STEP 5: resolve conflicts with the archived script (dry pass, then --write)"
$resolver = Join-Path $TOOLS 'resolve-conflicts.py'
if (-not (Test-Path $resolver)) { Die "missing $resolver" }

& $PY $resolver $script:Target 2>&1 | Select-Object -Last 6
if ($LASTEXITCODE -ne 0) { Die "dry pass not all PASS - nothing was written; resolve by hand" }
Ok "dry pass all PASS"

& $PY $resolver $script:Target --write 2>&1 | Select-Object -Last 4
if ($LASTEXITCODE -ne 0) { Die "write pass failed validation - inspect by hand" }
$left = @(GitMust ls-files -u | Where-Object { $_ -match '\S' })
if ($left.Count -gt 0) { Die ("still unresolved:`n" + ($left -join "`n")) }
Ok "0 unresolved, staged"

GitMust commit -q --no-edit | Out-Null
$mSha = LastLine (GitMust rev-parse --short HEAD)
Ok ("merge committed: " + $mSha)

# ---------------------------------------------------------------- step 6 (chores)
if (-not $SkipPost) {
    Hdr "STEP 6: post-merge chores"
    $tb = 'Agentic Workflow/qmclaw-web/tsconfig.tsbuildinfo'
    $wtTb = Join-Path $script:Target $tb
    if (Test-Path $wtTb) { Remove-Item -Force $wtTb; Ok ("removed stale working-tree " + $tb) }
    if ((LastLine (GitMust ls-files -- $tb))) {
        GitEx rm --cached -q $tb | Out-Null
        GitMust commit -q -m "chore: stop tracking build artifact tsconfig.tsbuildinfo" | Out-Null
        Ok "tsbuildinfo untracked and committed"
    } else { Ok "tsbuildinfo not tracked" }

    if ((LastLine (GitMust ls-files -- auth.lock))) {
        GitEx rm --cached -q auth.lock | Out-Null
        GitMust commit -q -m "chore: stop tracking runtime leftover auth.lock" | Out-Null
        Ok "auth.lock untracked and committed"
    } else { Ok "auth.lock not tracked" }
}

# ---------------------------------------------------------------- step 7
Hdr "STEP 7: automated verification"
& $PY (Join-Path $TOOLS 'probe-verify.py') $script:Target 2>&1 | Select-Object -Last 30
Say ""
Write-Host "   ==================== HUMAN GATE ====================" -ForegroundColor Yellow
Write-Host "   These need a person (CLAUDE.md rule 3: humans start/stop services):" -ForegroundColor Yellow
Write-Host "     item 7    frontend build: npm install (4 new deps), then build" -ForegroundColor Yellow
Write-Host "     item 8/10 start services, verify endpoints and hermes chat" -ForegroundColor Yellow
Write-Host "     item 9    end-to-end t1 scenario + eyeball-compare the plots" -ForegroundColor Yellow
Write-Host "     item 11   GET /api/hermes/skills must be non-empty (fix skills dir first)" -ForegroundColor Yellow
Write-Host "     item 17   start qca_service (needs vendor present)" -ForegroundColor Yellow
Write-Host "     item 22   decide the fate of memory/MEMORY.md" -ForegroundColor Yellow
Write-Host "     item 23   re-check skills after the fix" -ForegroundColor Yellow
Write-Host "   ====================================================" -ForegroundColor Yellow

if (-not $Continue) {
    Say ""
    Say ("   merge is parked on " + $BRANCH_SYNC + ". After the human checks pass, finish with:")
    Say "     .\run-merge.ps1 -Continue"
    Say ("   rollback: git -C `"$script:Target`" reset --hard " + $bk)
    exit 0
}

# ---------------------------------------------------------------- step 8
Hdr "STEP 8: commit the scratch ignore rules on their own"
$giWt = Join-Path $script:Target '.gitignore'
if ((Get-Content $giWt -Raw) -notmatch 'upstream-mirror') {
    Add-Content -Path $giWt -Encoding utf8 -Value "`n# local-only upstream merge artifacts (mirror is a reusable cache - keep it)`n.upstream-mirror/`n.merge-scratch/`n.analysis/`n"
    GitMust add .gitignore | Out-Null
    GitMust commit -q -m "chore: ignore local upstream-merge artifacts" | Out-Null
    Ok "ignore rules committed"
} else { Ok "ignore rules already present" }

# ---------------------------------------------------------------- step 9
Hdr "STEP 9: fast-forward the main working branch"
GitMust checkout -q $BRANCH_MAIN | Out-Null
$ff = GitEx merge $BRANCH_SYNC --ff-only
if ($script:LastGitExit -ne 0) { Die ("fast-forward failed (branch moved?):`n" + $ff) }
Ok ("fast-forwarded " + $BRANCH_MAIN)
Say ("   rollback: git -C `"$script:Target`" reset --hard " + $bk)

if (-not $KeepScratch) {
    Hdr "cleanup (keeping .upstream-mirror - 5 of the 12 archived scripts need it)"
    foreach ($d in @('.merge-scratch', '.analysis')) {
        $p = Join-Path $script:Target $d
        if (Test-Path $p) { Remove-Item -Recurse -Force $p; Ok ("removed " + $d) }
    }
    Ok ".upstream-mirror kept"
}

Hdr "DONE"
Say ("   merge commit : " + $mSha)
Say ("   backup branch: " + $bk)
Say "   next: update docs/CHANGES_VS_SOURCE.md, docs/GIT_HISTORY.md, docs/BACKLOG.md, and .claude/progress.md (plan section 9)"
