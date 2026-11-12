# start-qmclaw.ps1 -- one-click launcher for the QMClaw stack (D:\qmclaw)
#
# Components, each in its own console window (so one can be Ctrl+C'd alone):
#   1. Express gateway       :3002         Agentic Workflow\qmclaw-server        (npm run dev)
#   2. Python microservices  :3003-3012    Agentic Workflow\qmclaw-server\scripts (start_all_services.py --all)
#   3. Next.js web UI        :8081         Agentic Workflow\qmclaw-web           (npm run dev)
#
# The script starts the components, polls the health endpoints, prints a table
# and opens the UI. It never kills anything unless you pass -Kill / -Stop.
#
# ASCII-only on purpose: Windows PowerShell 5.1 decodes a BOM-less .ps1 with the
# ANSI code page, so non-ASCII characters here would break parsing.
#
# Examples:
#   start-qmclaw.cmd                    start everything, wait for health, open the UI
#   start-qmclaw.cmd -NoWeb             gateway + python services only
#   start-qmclaw.cmd -Kill              kill leftovers holding the ports first
#   start-qmclaw.cmd -Kill -NoBrowser   restart the stack quietly
#   start-qmclaw.cmd -Status            health table only, start nothing
#   start-qmclaw.cmd -Stop              stop the whole stack
#   start-qmclaw.cmd -Wait 150          longer health budget (LabRAD connect is slow)

[CmdletBinding()]
param(
    [string]$Root = 'D:\qmclaw',
    [string]$PythonBin = '',
    [switch]$NoGateway,
    [switch]$NoServices,
    [switch]$NoWeb,
    [switch]$NoBrowser,
    [switch]$Kill,
    [switch]$Stop,
    [switch]$Status,
    [int]$Wait = 90
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Server      = Join-Path $Root 'Agentic Workflow\qmclaw-server'
$Web         = Join-Path $Root 'Agentic Workflow\qmclaw-web'
$SvcDir      = Join-Path $Server 'scripts'
$LogDir      = Join-Path $Root 'logs'
$Stamp       = Get-Date -Format 'yyyyMMdd-HHmmss'
$LogFile     = Join-Path $LogDir "start-qmclaw-$Stamp.log"

$GatewayPort = 3002
$WebPort     = 8081
$SvcPorts    = 3003..3012
$AllPorts    = @($GatewayPort) + $SvcPorts + @($WebPort)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Say([string]$msg, [string]$color = '') {
    $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
    if ($color) { Write-Host $line -ForegroundColor $color } else { Write-Host $line }
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch { }
}

function Section([string]$title) {
    Say ''
    Say ('=' * 70)
    Say "== $title"
    Say ('=' * 70)
}

# --------------------------------------------------------------------- probes
function Test-PortOpen([int]$port, [int]$timeoutMs = 400) {
    # Cheap TCP probe first: an HTTP request against a closed port can sit in
    # the connect timeout for seconds, and the health loop probes 12 ports.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne($timeoutMs, $false)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-Url([string]$url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Test-ServicePort([int]$port) {
    if (-not (Test-PortOpen $port)) { return $false }
    return (Test-Url "http://127.0.0.1:$port/health")
}

function Test-WebPort([int]$port) {
    if (-not (Test-PortOpen $port)) { return $false }
    return (Test-Url "http://127.0.0.1:$port/")
}

function Get-PortOwner([int]$port) {
    try {
        $conn = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop |
                Select-Object -First 1
        if ($conn) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            $name = '?'
            if ($proc) { $name = $proc.ProcessName }
            return [pscustomobject]@{ Port = $port; Pid = $conn.OwningProcess; Name = $name }
        }
    } catch { }
    return $null
}

function Get-PythonServicesProcess {
    # microservice children: started either by start_all_services.py or as
    # "python -m services.<name>_service.server" -- note the dot, not a slash
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'start_all_services|services[\\/.]\w+_service' }
}

# ------------------------------------------------------------------- stopping
function Stop-Stack {
    Section 'stopping QMClaw'
    $killed = @()

    foreach ($port in $AllPorts) {
        $owner = Get-PortOwner $port
        if ($owner) {
            Stop-Process -Id $owner.Pid -Force -ErrorAction SilentlyContinue
            $killed += ("{0} (pid {1}) on :{2}" -f $owner.Name, $owner.Pid, $port)
        }
    }

    foreach ($p in (Get-PythonServicesProcess)) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $killed += ("python (pid {0})" -f $p.ProcessId)
    }

    if ($killed.Count -eq 0) {
        Say '  nothing was listening, nothing to stop'
    } else {
        foreach ($k in $killed) { Say "  stopped $k" }
    }
    Say 'done'
}

# -------------------------------------------------------------------- health
function Show-Health {
    $gwUp  = Test-ServicePort $GatewayPort
    $webUp = Test-WebPort $WebPort

    $up = @()
    $down = @()
    foreach ($p in $SvcPorts) {
        if (Test-ServicePort $p) { $up += $p } else { $down += $p }
    }

    $svcState = '{0}/10 up' -f $up.Count
    if ($down.Count -gt 0) { $svcState += ' (down: ' + ($down -join ',') + ')' }

    $rows = @(
        [pscustomobject]@{ Component = 'gateway  (express)'; Port = '3002';      State = $(if ($gwUp) { 'OK' } else { 'DOWN' }) }
        [pscustomobject]@{ Component = 'services (python)';  Port = '3003-3012'; State = $svcState }
        [pscustomobject]@{ Component = 'web      (next)';    Port = '8081';      State = $(if ($webUp) { 'OK' } else { 'DOWN' }) }
    )
    Say ''
    $rows | Format-Table -AutoSize | Out-String -Width 140 | Write-Host
    Say ("  gateway : http://127.0.0.1:{0}/health" -f $GatewayPort)
    Say ("  services: http://127.0.0.1:3008/health  (workflow)")
    Say ("  web     : http://localhost:{0}/" -f $WebPort)
    return [pscustomobject]@{ Gateway = $gwUp; Web = $webUp; ServicesUp = $up.Count; ServicesDown = $down }
}

# ------------------------------------------------------------------ launching
function New-Launcher([string]$name, [string]$dir, [string[]]$commands) {
    # A tiny per-component script: Start-Process + -File keeps quoting sane for
    # paths containing spaces (the whole repo path has one).
    $file = Join-Path $env:TEMP ("qmclaw-launch-{0}-{1}.ps1" -f $name, $Stamp)
    $body = @(
        '$ErrorActionPreference = ''Continue'''
        'try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }'
        ("try {{ `$Host.UI.RawUI.WindowTitle = 'QMClaw - {0}' }} catch {{ }}" -f $name)
        ("Set-Location -LiteralPath '{0}'" -f $dir)
    )
    $body += $commands
    $body += @(
        ''
        ("Write-Host '[start-qmclaw] {0} exited. Close this window or press Ctrl+C.' -ForegroundColor Yellow" -f $name)
    )
    Set-Content -Path $file -Value $body -Encoding ASCII
    return $file
}

function Start-Component([string]$name, [string]$dir, [string[]]$commands) {
    $launcher = New-Launcher $name $dir $commands
    Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $launcher) `
        -WorkingDirectory $dir | Out-Null
}

function Test-ComponentPortsBusy([int[]]$ports) {
    $busy = @()
    foreach ($p in $ports) {
        $owner = Get-PortOwner $p
        if ($owner) { $busy += $owner }
    }
    return $busy
}

# ---------------------------------------------------------------------- main
Say ("start-qmclaw   root={0}   python={1}" -f $Root, $PythonBin)
Say ("log: {0}" -f $LogFile)

Section '0. preflight'
if (-not (Test-Path $Root))   { Say "  FAIL root not found: $Root" 'Red';   exit 1 }
if (-not (Test-Path $Server)) { Say "  FAIL server not found: $Server" 'Red'; exit 1 }
if (-not (Test-Path $SvcDir)) { Say "  FAIL scripts dir not found: $SvcDir" 'Red'; exit 1 }
Say "  root   : $Root"
Say "  server : $Server"
Say "  web    : $Web"

if (-not $PythonBin) {
    $candidates = @(
        'D:\anaconda3\envs\lqcs\python.exe',
        'C:\Users\HUAWEI\.conda\envs\qmclaw\python.exe'
    )
    $PythonBin = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $PythonBin) {
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($cmd) { $PythonBin = $cmd.Source }
    }
}
if ($PythonBin -and (Test-Path $PythonBin)) {
    $pyVer = ''
    try { $pyVer = ((& $PythonBin -V 2>&1) -join ' ').Trim() } catch { $pyVer = '' }
    if ($pyVer) { Say ("  python : {0} ({1})" -f $PythonBin, $pyVer) }
    else        { Say ("  python : {0}" -f $PythonBin) }
} else {
    Say '  python : NOT FOUND (pass -PythonBin <path>)' 'Yellow'
}

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
if ($npm) {
    $nodeVer = ''
    $npmVer  = ''
    try { $nodeVer = ((& node -v 2>&1) -join ' ').Trim() } catch { }
    try { $npmVer  = ((& $npm.Source -v 2>&1) -join ' ').Trim() } catch { }
    $verText = @()
    if ($nodeVer) { $verText += "node $nodeVer" }
    if ($npmVer)  { $verText += "npm $npmVer" }
    if ($verText.Count -eq 0) { $verText = @('version not reported') }
    Say ("  node   : " + ($verText -join '  |  '))
} else {
    Say '  node   : NOT FOUND (install Node 18+ or add it to PATH)' 'Yellow'
}

foreach ($pair in @(@{ n = 'qmclaw-server'; d = $Server }, @{ n = 'qmclaw-web'; d = $Web })) {
    if (-not (Test-Path (Join-Path $pair.d 'node_modules'))) {
        Say ("  WARN {0}\node_modules missing -- run deploy-local.cmd once" -f $pair.d) 'Yellow'
    }
}

if ($Stop) { Stop-Stack; exit 0 }
if ($Status) {
    Section 'health'
    Show-Health | Out-Null
    exit 0
}

if ($Kill) {
    Section '1. killing leftovers'
    $killed = 0
    foreach ($port in $AllPorts) {
        $owner = Get-PortOwner $port
        if ($owner) {
            Stop-Process -Id $owner.Pid -Force -ErrorAction SilentlyContinue
            Say ("  killed {0} (pid {1}) on :{2}" -f $owner.Name, $owner.Pid, $port)
            $killed++
        }
    }
    foreach ($p in (Get-PythonServicesProcess)) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Say ("  killed python (pid {0})" -f $p.ProcessId)
        $killed++
    }
    if ($killed -eq 0) { Say '  nothing was listening' }
    Start-Sleep -Seconds 1
}

Section '2. launching components'
$started = @{ Gateway = $false; Services = $false; Web = $false }

if (-not $NoServices) {
    $busy = Test-ComponentPortsBusy $SvcPorts
    if ($busy.Count -gt 0) {
        Say ("  SKIP services: port(s) already in use: {0} (use -Kill)" -f (($busy | ForEach-Object { $_.Port }) -join ',')) 'Yellow'
    } elseif (-not $PythonBin -or -not (Test-Path $PythonBin)) {
        Say '  SKIP services: python interpreter not found' 'Yellow'
    } else {
        Start-Component 'services' $SvcDir @("& '$PythonBin' start_all_services.py --all")
        Say '  started python microservices :3003-3012 (own window)'
        $started.Services = $true
    }
}

$gatewayBusy = Get-PortOwner $GatewayPort
if (-not $NoGateway) {
    if ($gatewayBusy) {
        Say ("  SKIP gateway: :{0} already in use by {1} (pid {2})" -f $GatewayPort, $gatewayBusy.Name, $gatewayBusy.Pid) 'Yellow'
    } elseif (-not $npm) {
        Say '  SKIP gateway: npm not found' 'Yellow'
    } elseif (-not (Test-Path (Join-Path $Server 'node_modules'))) {
        Say '  SKIP gateway: node_modules missing' 'Yellow'
    } else {
        Start-Component 'gateway' $Server @('npm run dev')
        Say "  started express gateway :$GatewayPort (own window)"
        $started.Gateway = $true
    }
}

if (-not $NoWeb) {
    if (-not (Test-Path $Web)) {
        Say "  SKIP web: $Web not found" 'Yellow'
    } elseif (Get-PortOwner $WebPort) {
        Say ("  SKIP web: :{0} already in use" -f $WebPort) 'Yellow'
    } elseif (-not $npm) {
        Say '  SKIP web: npm not found' 'Yellow'
    } elseif (-not (Test-Path (Join-Path $Web 'node_modules'))) {
        Say '  SKIP web: node_modules missing' 'Yellow'
    } else {
        Start-Component 'web' $Web @('npm run dev')
        Say "  started next.js web :$WebPort (own window)"
        $started.Web = $true
    }
}

if (-not ($started.Gateway -or $started.Services -or $started.Web)) {
    Say ''
    Say 'nothing was started' 'Yellow'
    exit 1
}

# ---------------------------------------------------------------- wait ready
Section '3. waiting for health'
$deadline = (Get-Date).AddSeconds($Wait)
$lastLine = ''
while ($true) {
    $gwUp  = (Test-ServicePort $GatewayPort)
    $webUp = (Test-WebPort $WebPort)
    $svcUp = 0
    foreach ($p in $SvcPorts) { if (Test-ServicePort $p) { $svcUp++ } }

    $pending = @()
    if ($started.Gateway  -and -not $gwUp)                  { $pending += 'gateway' }
    if ($started.Services -and $svcUp -lt $SvcPorts.Count)  { $pending += ('services {0}/10' -f $svcUp) }
    if ($started.Web      -and -not $webUp)                 { $pending += 'web' }

    $line = ('  gateway:{0}  services:{1}/10  web:{2}' -f `
             $(if ($gwUp) { 'up' } else { '..' }),
             $svcUp,
             $(if ($webUp) { 'up' } else { '..' }))
    if ($line -ne $lastLine) {
        Write-Host $line
        $lastLine = $line
    }

    if ($pending.Count -eq 0) { break }
    if ((Get-Date) -ge $deadline) {
        Say ("  timed out after {0}s waiting for: {1}" -f $Wait, ($pending -join ', ')) 'Yellow'
        break
    }
    Start-Sleep -Seconds 2
}

Section '4. summary'
$health = Show-Health

$allGood = $true
if ($started.Gateway  -and -not $health.Gateway)        { $allGood = $false }
if ($started.Web      -and -not $health.Web)            { $allGood = $false }
if ($started.Services -and $health.ServicesDown.Count -gt 0) { $allGood = $false }

Say ''
if ($started.Web -and -not $NoBrowser) {
    Say ("  opening http://localhost:{0}" -f $WebPort) 'Cyan'
    Start-Process ("http://localhost:{0}" -f $WebPort) | Out-Null
} elseif ($started.Web) {
    Say ("  UI    : http://localhost:{0}" -f $WebPort)
}
if ($started.Gateway) { Say ("  api   : http://127.0.0.1:{0}/health" -f $GatewayPort) }

Say ''
Say '  each component runs in its own window (Ctrl+C there to stop just that one)'
Say '  stop everything : start-qmclaw.cmd -Stop'
Say '  check health    : start-qmclaw.cmd -Status'
Say ("  log             : {0}" -f $LogFile)
Say ''
if ($allGood) { Say 'all requested components are up' 'Green' } else { Say 'some components did not come up -- see the tables above' 'Yellow' }

exit $(if ($allGood) { 0 } else { 1 })
