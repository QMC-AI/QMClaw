# start-services-quiet.ps1 - launch all QMClaw services as detached background processes
#
# Difference from scripts/start_all_services.py: that one is a FOREGROUND supervisor
# (stops on Ctrl+C) and pipes every child's output into its own stdout. This script
# starts each process detached with its output going to a log file, so it never
# occupies a terminal and never streams into the caller.
#
# Logs follow the repo's existing convention:  <repo>\.runtime-logs\<name>.out.log / .err.log
# State (PID/port/log) is recorded in        <repo>\.runtime-logs\services-status.json
#
# Usage:
#   .\start-services-quiet.ps1            # start; ports already listening are skipped
#   .\start-services-quiet.ps1 -Restart   # stop processes this script started, then start
#   .\start-services-quiet.ps1 -Stop      # stop processes this script started
#   .\start-services-quiet.ps1 -Status    # show status only
#
# NOTE: ASCII-only on purpose. Windows PowerShell reads .ps1 as ANSI, so non-ASCII
# text (e.g. Chinese) breaks parsing - this file failed that way in its first draft.

param(
    [switch]$Restart,
    [switch]$Stop,
    [switch]$Status,
    [switch]$NoWait,
    [string]$RepoRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
)

$ErrorActionPreference = 'Continue'
$LOG_DIR = Join-Path $RepoRoot '.runtime-logs'
# NOTE: do NOT name the state file variable $STATUS - that collides with a PowerShell
# automatic variable, and assignment fails with "cannot convert String to SwitchParameter".
$STATUS_FILE = Join-Path $LOG_DIR 'services-status.json'
$PY      = 'D:\semester4\deploy\qmclaw\qmclaw\python.exe'
$SERVER  = Join-Path $RepoRoot 'Agentic Workflow\qmclaw-server'
$WEB     = Join-Path $RepoRoot 'Agentic Workflow\qmclaw-web'

New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

# key = directory name under services/ (= python -m services.<key>.server)
# value = port, aligned with config/services.json
$PY_SERVICES = [ordered]@{
    'llm_service'         = 3006
    'quantum_service'     = 3003
    'analysis_service'    = 3004
    'agent_service'       = 3005
    'image_service'       = 3007
    'workflow_service'    = 3008
    'task_queue'          = 3009
    'qubitclient_service' = 3010
    'qca_service'         = 3011
    'hermes_service'      = 3012
}

function Test-Port($p) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue)
}

# Start-Process -ArgumentList does NOT quote arguments for you. Any path containing a
# space ("...\Agentic Workflow\...") gets split at the space and node reports
# "Cannot find module D:\...\Agentic". Wrap such arguments in double quotes explicitly.
function Q($a) {
    if ($a -match '\s') { return '"' + $a + '"' }
    return $a
}

function Get-AllTargets {
    $t = foreach ($k in $PY_SERVICES.Keys) {
        [pscustomobject]@{ Name = $k; Port = $PY_SERVICES[$k]; Kind = 'py' }
    }
    $t += [pscustomobject]@{ Name = 'express'; Port = 3002; Kind = 'node' }
    $t += [pscustomobject]@{ Name = 'web';     Port = 8081; Kind = 'web' }
    return $t
}

function Show-Status {
    Write-Host "==== QMClaw service status ====" -ForegroundColor Cyan
    $rows = foreach ($s in Get-AllTargets) {
        $listen = Get-NetTCPConnection -State Listen -LocalPort $s.Port -ErrorAction SilentlyContinue |
                  Select-Object -First 1
        $ownerId = if ($listen) { $listen.OwningProcess } else { $null }
        $http = '-'
        if ($listen) {
            $url = if ($s.Kind -eq 'web') { "http://127.0.0.1:$($s.Port)/" }
                   else { "http://127.0.0.1:$($s.Port)/health" }
            try {
                $r = Invoke-WebRequest $url -TimeoutSec 15 -UseBasicParsing -ErrorAction Stop
                $http = $r.StatusCode
            } catch { $http = 'ERR' }
        }
        [pscustomobject]@{
            Service = $s.Name
            Port    = $s.Port
            State   = if ($listen) { 'UP' } else { 'DOWN' }
            PID     = $ownerId
            HTTP    = $http
            Log     = "$($s.Name).out.log"
        }
    }
    $rows | Format-Table -AutoSize
    $up = ($rows | Where-Object { $_.State -eq 'UP' }).Count
    Write-Host ("  UP {0} / {1}" -f $up, $rows.Count) -ForegroundColor Green
}

function Stop-Managed {
    if (-not (Test-Path $STATUS_FILE)) { Write-Host "  no processes recorded by this script"; return }
    $rec = Get-Content $STATUS_FILE -Raw | ConvertFrom-Json
    foreach ($e in @($rec)) {
        $proc = Get-Process -Id $e.Pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host ("  stopping {0} (PID {1})" -f $e.Name, $e.Pid)
            try { Stop-Process -Id $e.Pid -Force -ErrorAction Stop } catch {}
        }
    }
    Remove-Item $STATUS_FILE -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

if ($Status)  { Show-Status; return }
if ($Stop)    { Stop-Managed; Show-Status; return }
if ($Restart) { Write-Host "== stopping previously started processes =="; Stop-Managed }

$started = @()

# ---------- 1) Python microservices ----------
Write-Host "== Python microservices (detached, logs -> .runtime-logs) ==" -ForegroundColor Cyan
foreach ($k in $PY_SERVICES.Keys) {
    $port = $PY_SERVICES[$k]
    if (Test-Port $port) { Write-Host ("  {0,-20} :{1}  already listening, skip" -f $k, $port); continue }

    $mod    = 'services.' + $k + '.server'
    $outLog = Join-Path $LOG_DIR ($k + '.out.log')
    $errLog = Join-Path $LOG_DIR ($k + '.err.log')

    $env:PORT = "$port"
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'

    $p = Start-Process -FilePath $PY -ArgumentList @('-m', $mod) `
            -WorkingDirectory $SERVER `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
            -WindowStyle Hidden -PassThru
    Write-Host ("  {0,-20} :{1}  PID {2}" -f $k, $port, $p.Id)
    $started += [pscustomobject]@{ Name = $k; Port = $port; Pid = $p.Id; Log = $outLog }
}

# ---------- 2) Express gateway ----------
Write-Host "== Express gateway :3002 ==" -ForegroundColor Cyan
if (Test-Port 3002) { Write-Host "  already listening, skip" }
else {
    $outLog = Join-Path $LOG_DIR 'express.out.log'
    $errLog = Join-Path $LOG_DIR 'express.err.log'
    $env:PYTHON_BIN = $PY
    $node = (Get-Command node).Source
    $tsx  = Join-Path $SERVER 'node_modules\tsx\dist\cli.mjs'
    $p = Start-Process -FilePath $node -ArgumentList @((Q $tsx), 'src/index.ts') `
            -WorkingDirectory $SERVER `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
            -WindowStyle Hidden -PassThru
    Write-Host ("  {0,-20} :{1}  PID {2}" -f 'express', 3002, $p.Id)
    $started += [pscustomobject]@{ Name = 'express'; Port = 3002; Pid = $p.Id; Log = $outLog }
}

# ---------- 3) Next.js frontend ----------
Write-Host "== Next.js frontend :8081 ==" -ForegroundColor Cyan
if (Test-Port 8081) { Write-Host "  already listening, skip" }
else {
    $outLog = Join-Path $LOG_DIR 'web.out.log'
    $errLog = Join-Path $LOG_DIR 'web.err.log'
    $node = (Get-Command node).Source
    $next = Join-Path $WEB 'node_modules\next\dist\bin\next'
    $p = Start-Process -FilePath $node -ArgumentList @((Q $next), 'dev', '-p', '8081') `
            -WorkingDirectory $WEB `
            -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
            -WindowStyle Hidden -PassThru
    Write-Host ("  {0,-20} :{1}  PID {2}" -f 'web', 8081, $p.Id)
    $started += [pscustomobject]@{ Name = 'web'; Port = 8081; Pid = $p.Id; Log = $outLog }
}

# ---------- 4) persist state ----------
# MERGE with what is already recorded - a second run that only fills in the missing
# services must not drop the entries from the first run.
if ($started.Count -gt 0) {
    $existing = @()
    if (Test-Path $STATUS_FILE) {
        try { $existing = @(Get-Content $STATUS_FILE -Raw | ConvertFrom-Json) } catch { $existing = @() }
    }
    $merged = @($existing | Where-Object { $started.Name -notcontains $_.Name }) + $started
    $merged | ConvertTo-Json -Depth 3 | Set-Content $STATUS_FILE -Encoding utf8
    Write-Host ("`n  recorded {0} new process(es), {1} total -> {2}" -f $started.Count, $merged.Count, $STATUS_FILE)
}

# ---------- 5) wait until ready ----------
# -NoWait returns right after spawning, so a long readiness loop can never hold up the
# caller (which matters when this is invoked from an agent session that may abort a call).
if (-not $NoWait) {
    Write-Host "`n== waiting for services to become ready ==" -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $pending = @()
        foreach ($s in Get-AllTargets) {
            if (-not (Test-Port $s.Port)) { $pending += $s.Name; continue }
            if ($s.Kind -eq 'web') { continue }   # first compile is slow; Show-Status judges it
            try {
                Invoke-WebRequest "http://127.0.0.1:$($s.Port)/health" -TimeoutSec 4 -UseBasicParsing -ErrorAction Stop | Out-Null
            } catch { $pending += $s.Name }
        }
        if ($pending.Count -eq 0) { break }
        Start-Sleep -Seconds 2
    }
    Write-Host ""
    Show-Status
}
Write-Host "`nLogs:    .runtime-logs\<name>.out.log / <name>.err.log" -ForegroundColor DarkGray
Write-Host "         (logs are UTF-8; read them with:  Get-Content <file> -Encoding UTF8 -Tail 50)" -ForegroundColor DarkGray
Write-Host "Stop:    .\start-services-quiet.ps1 -Stop" -ForegroundColor DarkGray
Write-Host "Status:  .\start-services-quiet.ps1 -Status" -ForegroundColor DarkGray
