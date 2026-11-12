@echo off
REM ============================================================
REM  QMClaw 本机部署包装器
REM
REM  deploy-local.ps1 未做数字签名，本机执行策略是 RemoteSigned，
REM  直接双击/调用 .ps1 会被拒。这里用 -ExecutionPolicy Bypass
REM  只对这一次进程绕过执行策略，不改动机器的全局设置。
REM
REM  注意：受限沙箱下子进程是 Low 完整性令牌，写不进工作区既有子目录，
REM        本脚本需要在普通（非受限）shell 里运行。
REM ============================================================
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy-local.ps1" %*
exit /b %ERRORLEVEL%
