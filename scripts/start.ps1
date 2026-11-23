$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.tools\python'
$python = Join-Path $root '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { & (Join-Path $PSScriptRoot 'install.ps1') }
& $python (Join-Path $PSScriptRoot 'launch.py') @args
exit $LASTEXITCODE
