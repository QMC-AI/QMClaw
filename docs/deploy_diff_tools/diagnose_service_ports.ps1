# Diagnose which process owns each QMClaw service port and find orphans.
# ASCII-only on purpose: Windows PowerShell 5.1 decodes BOM-less UTF-8 as ANSI,
# so non-ASCII characters inside a .ps1 break parsing.
$ErrorActionPreference = 'SilentlyContinue'

function Show-Owner([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($c) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        "  {0,-5} PID={1,-7} {2,-14} started {3}" -f $port, $c.OwningProcess, $p.ProcessName, $p.StartTime
    } else {
        "  {0,-5} (nobody listening)" -f $port
    }
}

Write-Host "=== listeners on 3002-3012 ==="
foreach ($port in @(3002) + (3003..3012)) { Show-Owner $port }

Write-Host ""
Write-Host "=== all connections on 3011 (any state) ==="
Get-NetTCPConnection -LocalPort 3011 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, State, OwningProcess |
    Format-Table -AutoSize | Out-String -Width 200 | Write-Host

Write-Host "=== python processes: qca_service or start_all_services ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'qca_service|start_all_services' } |
    ForEach-Object {
        $pr = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
        $cmd = ($_.CommandLine -replace '\s+', ' ')
        if ($cmd.Length -gt 160) { $cmd = $cmd.Substring(0, 160) }
        "  PID={0,-7} PPID={1,-7} started {2}" -f $_.ProcessId, $_.ParentProcessId, $pr.StartTime
        "      {0}" -f $cmd
    }

Write-Host ""
Write-Host "=== count of python processes by service module ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    ForEach-Object {
        if ($_.CommandLine -match 'services\.([a-z_]+)\.server') { $matches[1] }
        elseif ($_.CommandLine -match 'start_all_services') { 'start_all' }
        else { 'other' }
    } | Group-Object | Sort-Object Count -Descending |
    ForEach-Object { "  {0,-18} {1}" -f $_.Name, $_.Count }
