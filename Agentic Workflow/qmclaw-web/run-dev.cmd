@echo off
REM Wrapper: ensure a Node runtime is on PATH, then run `npm run dev` in the web directory.
REM
REM The lab machine bundled Node inside AutoClaw; this machine has a normal Node install.
REM So: prefer the bundled one when it exists, otherwise use whatever `node` is on PATH.
setlocal

if exist "C:\Program Files\AutoClaw\resources\node\node.exe" (
  set "PATH=C:\Program Files\AutoClaw\resources\node;%PATH%"
  echo [run-dev] using bundled AutoClaw node
)

where node >nul 2>nul
if errorlevel 1 (
  echo [run-dev] ERROR: node not found on PATH.
  echo [run-dev] Install Node.js 18+ ^(https://nodejs.org/^) or add it to PATH, then retry.
  exit /b 1
)

cd /d "%~dp0"
call npm run dev
