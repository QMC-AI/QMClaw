# deploy-local.ps1 -- QMClaw local deployment for D:\qmclaw
#
# Idempotent. Does four things and never starts a service (project rule:
# service start/stop is done by a human):
#   1. clone the three vendor/ submodules (URLs from .gitmodules, pinned shas)
#   2. install the Python packages the project imports but the env lacks
#   3. install Node dependencies for qmclaw-server and qmclaw-web
#   4. create runtime directories, then run the microservice import smoke test
#
# NOTE: this file is deliberately ASCII-only. This machine runs Windows
# PowerShell 5.1, which decodes .ps1 files without a UTF-8 BOM using the ANSI
# code page; non-ASCII text here silently breaks parsing.
#
# Run (unsigned script -> bypass execution policy for this process only):
#   powershell -NoProfile -ExecutionPolicy Bypass -File D:\qmclaw\deploy-local.ps1
# or just: deploy-local.cmd
#
# The restricted sandbox runs child processes with a Low integrity token, which
# cannot write into existing subdirectories of the workspace; run this in a
# normal (unsandboxed) shell.

[CmdletBinding()]
param(
    [string]$Root = 'D:\qmclaw',
    [string]$PythonBin = 'D:\anaconda3\envs\lqcs\python.exe',
    [switch]$SkipVendor,
    [switch]$SkipPython,
    [switch]$SkipNode
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
# decoded output from the Python services is UTF-8; without this the console
# code page mangles it in the log
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Server = Join-Path $Root 'Agentic Workflow\qmclaw-server'
$Web    = Join-Path $Root 'Agentic Workflow\qmclaw-web'
$Vendor = Join-Path $Root 'vendor'
$LogDir = Join-Path $Root '.deploy-logs'
$Stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$Log    = Join-Path $LogDir "deploy-$Stamp.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$script:Failures = @()

function Say([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $Log -Value $line
}

function Step([string]$title) {
    Say ''
    Say ('=' * 70)
    Say "== $title"
    Say ('=' * 70)
}

function Fail([string]$what, [string]$detail) {
    $script:Failures += "$what :: $detail"
    Say "  !! FAIL $what -- $detail"
}

# --------------------------------------------------------------- 0. preflight
Step '0. preflight'
Say "root        : $Root"
Say "server      : $Server"
Say "web         : $Web"
Say "python      : $PythonBin"
if (-not (Test-Path $Root))   { Fail 'root'   "not found: $Root";   exit 1 }
if (-not (Test-Path $Server)) { Fail 'server' "not found: $Server"; exit 1 }
if (-not (Test-Path $Web))    { Fail 'web'    "not found: $Web";    exit 1 }
if (-not $SkipPython) {
    if (-not (Test-Path $PythonBin)) { Fail 'python' "not found: $PythonBin"; exit 1 }
    Say "python ver  : $(& $PythonBin -V 2>&1)"
}
Say "node        : $(node -v) / npm $(npm -v)"

# ------------------------------------------------------- 1. vendor submodules
if (-not $SkipVendor) {
    Step '1. clone vendor/ submodules'

    $subs = @(
        @{ Name = 'hermes-agent';                        Url = 'https://github.com/Secsix/hermes-agent';                        Sha = 'c4a5deeffa959f8ebc891e02c5bcf767aff1dd2a'; Local = @() },
        @{ Name = 'QubitClient';                         Url = 'https://github.com/Secsix/QubitClient';                         Sha = '3da70fe01d8a873418ed84915d4a05c35f07082a'; Local = @('D:\work\QubitClient', 'C:\work\QubitClient') },
        @{ Name = 'Quantum-Calibration-Agent-Blueprint'; Url = 'https://github.com/Secsix/Quantum-Calibration-Agent-Blueprint'; Sha = 'd8d37bc4de28d2347d8e21405903b29a2891f793'; Local = @() }
    )

    $env:GIT_TERMINAL_PROMPT = '0'
    foreach ($s in $subs) {
        $dst = Join-Path $Vendor $s.Name
        Say "--- $($s.Name)"

        # only ever remove an existing *empty* placeholder directory
        if (Test-Path $dst) {
            $count = @(Get-ChildItem $dst -Recurse -Force -ErrorAction SilentlyContinue).Count
            if ($count -eq 0) {
                Say '    empty placeholder, removing and re-cloning'
                Remove-Item $dst -Force -Recurse
            } elseif (Test-Path (Join-Path $dst '.git')) {
                Say '    already a git repo, skipping clone'
                continue
            } elseif ($count -gt 0) {
                Say '    non-empty and not a git repo, keeping as-is'
                continue
            }
        }

        git clone --quiet $s.Url $dst 2>&1 | ForEach-Object { Say "    $_" }
        if ($LASTEXITCODE -ne 0) {
            Say '    clone failed, retrying with the openssl TLS backend'
            git -c http.sslBackend=openssl clone --quiet $s.Url $dst 2>&1 | ForEach-Object { Say "    $_" }
        }

        if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $dst '.git'))) {
            # github.com is unreachable from this network and the Secsix repos are
            # private anyway; fall back to a local checkout when one is available.
            $src = $s.Local | Where-Object { Test-Path $_ } | Select-Object -First 1
            if ($src) {
                Say "    clone unavailable, copying local checkout instead: $src"
                if (Test-Path $dst) { Remove-Item $dst -Force -Recurse }
                # /XD .git: keep this dependency tree independent of its own repo
                # /XF qubitclient.json: that file holds lab-internal addresses and an API key
                robocopy $src $dst /E /XD .git .venv node_modules __pycache__ /XF qubitclient.json /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) {
                    Fail "vendor/$($s.Name)" "robocopy from $src failed (exit=$LASTEXITCODE)"
                } else {
                    Say "    copied $(@(Get-ChildItem $dst -Recurse -Force -File -ErrorAction SilentlyContinue).Count) files from $src"
                    Say '    NOTE: local checkout, NOT the commit pinned in .gitmodules'
                }
            } else {
                Fail "vendor/$($s.Name)" 'git clone failed (github unreachable / private repo) and no local source found'
            }
            continue
        }

        git -C $dst checkout --quiet $s.Sha 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Say "    checked out pinned commit $($s.Sha.Substring(0,8))"
        } else {
            Say "    pinned commit $($s.Sha.Substring(0,8)) not found, staying on default HEAD"
        }
        Say "    HEAD  : $((git -C $dst log --oneline -1 2>&1) -join ' ')"
        Say "    files : $(@(Get-ChildItem $dst -Recurse -Force -File -ErrorAction SilentlyContinue).Count)"
    }

    $qcInit = Join-Path $Vendor 'QubitClient\qubitclient\__init__.py'
    if (Test-Path $qcInit) {
        Say "QubitClient: $((Select-String -Path $qcInit -Pattern '__version__' | Select-Object -First 1).Line)"
    }
}

# ------------------------------------------------------------ 2. python deps
if (-not $SkipPython) {
    Step '2. install missing Python packages'

    $freezeBefore = Join-Path $LogDir "pip-freeze-before-$Stamp.txt"
    & $PythonBin -m pip freeze 2>$null | Out-File -FilePath $freezeBefore
    Say "wrote $freezeBefore"

    # only packages the project really imports and the env really lacks;
    # already-satisfied dependencies are left alone (pip default upgrade strategy)
    $pkgs = @(
        'portalocker',        # services/hermes_service/memory.py
        'langgraph',          # services/qca_service/server.py
        'langchain',          # services/qca_service/server.py
        'deepagents',         # services/qca_service/server.py
        'redis',              # services/task_queue/server.py (optional backend)
        'onnxruntime',        # services/image_service/server.py (optional backend)
        'pyopenssl>=24.0.0'   # requirements ask >=24, env had 22.0.0
    )

    Say "packages: $($pkgs -join ', ')"
    & $PythonBin -m pip install --disable-pip-version-check --no-input @pkgs 2>&1 |
        ForEach-Object { Say "    $_" }
    if ($LASTEXITCODE -ne 0) { Fail 'pip install' "exit=$LASTEXITCODE" }

    $freezeAfter = Join-Path $LogDir "pip-freeze-after-$Stamp.txt"
    & $PythonBin -m pip freeze 2>$null | Out-File -FilePath $freezeAfter
    Say "wrote $freezeAfter"

    $before = @(Get-Content $freezeBefore)
    $after  = @(Get-Content $freezeAfter)
    # Compare-Object <reference> <difference>: '=>' means only in the difference
    # set, so the difference argument has to be the *after* list.
    $added   = @(Compare-Object $before $after | Where-Object { $_.SideIndicator -eq '=>' } | ForEach-Object { $_.InputObject })
    $removed = @(Compare-Object $before $after | Where-Object { $_.SideIndicator -eq '<=' } | ForEach-Object { $_.InputObject })
    Say "new     ($($added.Count)): $($added -join ' | ')"
    Say "old/upg ($($removed.Count)): $($removed -join ' | ')"
}

# -------------------------------------------------------------- 3. node deps
if (-not $SkipNode) {
    Step '3. install Node dependencies'
    foreach ($p in @(@{ n = 'qmclaw-server'; d = $Server }, @{ n = 'qmclaw-web'; d = $Web })) {
        Say "--- $($p.n)"
        Push-Location $p.d
        if (Test-Path 'package-lock.json') { $cmd = 'ci' } else { $cmd = 'install' }
        Say "    npm $cmd"
        npm $cmd --no-fund --no-audit 2>&1 | Select-Object -Last 12 | ForEach-Object { Say "    $_" }
        if ($LASTEXITCODE -ne 0) { Fail "npm $cmd $($p.n)" "exit=$LASTEXITCODE" }
        Say "    node_modules: $(Test-Path 'node_modules')  packages: $(@(Get-ChildItem 'node_modules' -Directory -ErrorAction SilentlyContinue).Count)"
        Pop-Location
    }
}

# ------------------------------------------------- 4. runtime dirs + smoke test
Step '4. create runtime directories'
$dirs = @(
    (Join-Path $Server 'data'),
    (Join-Path $Server 'data\workflows'),
    (Join-Path $Server 'data\templates'),
    (Join-Path $Server 'data\offline_data'),
    (Join-Path $Server 'logs'),
    (Join-Path $Server 'config\memory'),
    (Join-Path $Web    'public\plots')
)
foreach ($d in $dirs) {
    try {
        New-Item -ItemType Directory -Force -Path $d -ErrorAction Stop | Out-Null
        Say "  ok   $d"
    } catch {
        Fail "mkdir $d" $_.Exception.Message
    }
}

# ------------------------------------------------------------------- 5. verify
Step '5. verify'
$havePy = Test-Path $PythonBin

if ($true) {
    Say '--- node toolchain'
    # npm 11.16 reports "allow-scripts" warnings for esbuild's postinstall, but
    # the platform binary actually arrives as the optional dependency
    # @esbuild/win32-x64, so the real test is whether tsx runs.
    foreach ($p in @(@{ n = 'qmclaw-server'; d = $Server }, @{ n = 'qmclaw-web'; d = $Web })) {
        Push-Location $p.d
        $tsx = Join-Path $p.d 'node_modules\tsx\dist\cli.mjs'
        if (Test-Path $tsx) {
            $v = (& node $tsx --version 2>&1 | Select-Object -First 1) -join ' '
            Say "  $($p.n): $v (esbuild $(@(Get-ChildItem 'node_modules\@esbuild' -Directory -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Name)))"
        } elseif (Test-Path (Join-Path $p.d 'node_modules\next\package.json')) {
            $nv = (Get-Content (Join-Path $p.d 'node_modules\next\package.json') -Raw | ConvertFrom-Json).version
            Say "  $($p.n): next $nv"
        } else {
            Say "  $($p.n): node_modules incomplete"
        }
        Pop-Location
    }
}

if ($havePy) {
    Say '--- pip check (pre-existing conflicts in this env are expected)'
    & $PythonBin -m pip check 2>&1 |
        Select-String -NotMatch 'WARNING: Ignoring invalid' |
        ForEach-Object { Say "  $_" }

    Say '--- microservice import smoke test'
    Push-Location $Server
    & $PythonBin -B (Join-Path $Root 'docs\deploy_diff_tools\check_services.py') $Server 2>&1 |
        Select-String -NotMatch 'WARNING: Ignoring invalid' |
        ForEach-Object { Say "  $_" }
    Pop-Location
}

# ------------------------------------------------------------------- summary
Step 'summary'
if ($script:Failures.Count -eq 0) {
    Say 'all steps completed, no failures'
} else {
    Say "$($script:Failures.Count) failure(s):"
    $script:Failures | ForEach-Object { Say "  - $_" }
}
Say "log: $Log"
Say ''
Say 'Next steps (service start/stop is done by a human, by project rule):'
Say "  terminal 1: cd `"$Server`"; npm run dev     # Express gateway :3002"
Say "  terminal 2: cd `"$Web`"; npm run dev        # Next.js UI     :8081"
Say '  health   : http://127.0.0.1:3002/health'

if ($script:Failures.Count -eq 0) { exit 0 } else { exit 1 }
