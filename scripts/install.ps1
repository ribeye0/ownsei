$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = '1'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.tools\python'
$uv = Join-Path $root '.tools\uv.exe'
function Run-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Program" }
}
New-Item -ItemType Directory -Force '.tools' | Out-Null
if (!(Test-Path -LiteralPath $uv)) {
    $archive = Join-Path $root '.tools\uv.zip'
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
    Invoke-WebRequest -UseBasicParsing 'https://github.com/astral-sh/uv/releases/download/0.13.0/uv-x86_64-pc-windows-msvc.zip' -OutFile $archive
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant() -ne '088962f9e7b7bd9ea740c04c650b2a21c8928c345bd99ac24350dc924dba656c') { throw 'uv SHA256 mismatch' }
    Expand-Archive -LiteralPath $archive -DestinationPath '.tools' -Force
    Remove-Item -LiteralPath $archive
}
Run-Checked $uv @('python','install','3.12')
Run-Checked $uv @('sync','--locked','--no-dev','--python','3.12')
$python = Join-Path $root '.venv\Scripts\python.exe'
Run-Checked $python @('scripts/setup_engines.py')
Run-Checked $python @('scripts/setup_ffmpeg.py')
if (!(Test-Path 'frontend/dist/index.html')) {
    Push-Location frontend
    try {
        Run-Checked 'npm.cmd' @('ci')
        Run-Checked 'npm.cmd' @('run','build')
    } finally { Pop-Location }
}
Run-Checked $python @('-m','onsei.cli','doctor','--strict')
Write-Host 'Ownsei ready. Open Ownsei.cmd; download models in Model management.'
