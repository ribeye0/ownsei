@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%
