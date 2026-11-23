$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$env:PYTHONUTF8 = '1'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.tools\python'
$python = Join-Path $root '.venv\Scripts\python.exe'
$ready = $false
if (Test-Path -LiteralPath $python) {
    $ready = Test-Path -LiteralPath '.runtime-envs/update/backup/plan.json'
    if (!$ready) {
        & $python (Join-Path $PSScriptRoot 'install_state.py')
        $ready = $LASTEXITCODE -eq 0
    }
}
if (!$ready) { & (Join-Path $PSScriptRoot 'install.ps1') }
& $python (Join-Path $PSScriptRoot 'launch.py') @args
exit $LASTEXITCODE
