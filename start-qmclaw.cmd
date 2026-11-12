@echo off
REM ============================================================
REM  QMClaw one-click launcher (wrapper)
REM
REM  start-qmclaw.ps1 is not digitally signed and the default
REM  execution policy rejects it, so bypass the policy for this
REM  process only (does not change the machine-wide setting).
REM
REM  Usage:
REM    start-qmclaw.cmd                  start everything
REM    start-qmclaw.cmd -NoWeb           gateway + python services
REM    start-qmclaw.cmd -Kill            kill leftovers first
REM    start-qmclaw.cmd -Status          health table only
REM    start-qmclaw.cmd -Stop            stop the whole stack
REM ============================================================
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-qmclaw.ps1" %*
exit /b %ERRORLEVEL%
