# Read-only: list python.exe processes with their command lines (QMClaw services).
$ErrorActionPreference = 'SilentlyContinue'

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Select-Object ProcessId, ParentProcessId, @{n = 'CmdLine'; e = { $_.CommandLine } } |
    Format-Table -AutoSize -Wrap
