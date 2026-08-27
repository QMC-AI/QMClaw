@echo off
REM Wrapper: adds node to PATH then runs npm dev in the server directory
set "PATH=C:\Program Files\AutoClaw\resources\node;%PATH%"
cd /d "%~dp0"
npm run dev