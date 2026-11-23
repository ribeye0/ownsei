@echo off
setlocal
cd /d "%TEMP%"
rem Keep this final command on one line; the uninstall can delete this launcher.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall.ps1" %* & if errorlevel 1 (pause & exit /b 1) else (exit /b 0)
