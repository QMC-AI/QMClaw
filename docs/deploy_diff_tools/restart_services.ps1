# Fully restart QMClaw python microservices (run by a human; this script only
# cleans up leftovers and prints the next step).
#
# NOTE: keep this file ASCII-only -- Windows PowerShell 5.1 decodes BOM-less
# scripts as ANSI, so any Chinese text here breaks string terminators.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "D:\qmclaw\docs\deploy_diff_tools\restart_services.ps1"
#
# Why: closing the start_all_services.py window leaves orphaned child services
# holding a broken log pipe -- they keep listening but answer every request with
# a 0-byte close. Leftovers must be killed before restarting.

$ErrorActionPreference = 'SilentlyContinue'

$targets = @(3003, 3004, 3005, 3006, 3007, 3008, 3009, 3010, 3011, 3012)

Write-Host "=== 1/3 Killing leftover QMClaw python services ===" -ForegroundColor Cyan
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match '-m services\.|start_all_services' }

if (-not $procs) {
    Write-Host "  no leftover process found"
} else {
    foreach ($p in $procs) {
        Write-Host ("  KILL {0}" -f $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force
    }
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "=== 2/3 Checking whether ports are free ===" -ForegroundColor Cyan
$busy = @()
foreach ($port in $targets) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($conn) {
        $busy += $port
        Write-Host ("  port {0} still held by PID {1}" -f $port, $conn.OwningProcess) -ForegroundColor Yellow
    }
}
if ($busy.Count -eq 0) {
    Write-Host "  3003-3012 are all free" -ForegroundColor Green
} else {
    Write-Host ("  still busy, check them with: netstat -ano | findstr :{0}" -f $busy[0]) -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== 3/3 Next step (run in a NEW window and keep it open) ===" -ForegroundColor Cyan
Write-Host '  cd "D:\qmclaw\Agentic Workflow\qmclaw-server\scripts"'
Write-Host '  & "D:\anaconda3\envs\lqcs\python.exe" start_all_services.py --all'
Write-Host ""
Write-Host "To stop: press Ctrl+C in that window (graceful stop_all), never just close it." -ForegroundColor Yellow
