# Python plans the removal, then exits so its executable and DLLs can be removed.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$python = Join-Path $root '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python)) {
    $uv = Join-Path $root '.tools/uv.exe'
    if (Test-Path -LiteralPath $uv) {
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $root '.tools/python'
        $python = & $uv python find --managed-python --no-python-downloads 3.12
    }
    if (!$python -or !(Test-Path -LiteralPath $python)) {
        $python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    }
}
$temp = [IO.Path]::GetTempFileName()
try {
    & $python -B (Join-Path $PSScriptRoot 'uninstall.py') --windows-plan $temp @args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $json = [IO.File]::ReadAllText($temp)
    if (!$json) { exit 0 }
    $plan = ConvertFrom-Json -InputObject $json
    Set-Location -LiteralPath ([IO.Path]::GetTempPath())
    [Environment]::CurrentDirectory = [IO.Path]::GetTempPath()
    foreach ($group in 'files', 'last', 'directories') {
        foreach ($path in $plan.$group) {
            $parent = [IO.Directory]::GetParent($path)
            while ($null -ne $parent) {
                if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                    throw "Path changed, stopped at: $path"
                }
                $parent = $parent.Parent
            }
            $attributes = [IO.File]::GetAttributes($path)
            $directory = $attributes -band [IO.FileAttributes]::Directory
            if ($group -eq 'directories') {
                if ($attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Path changed: $path" }
                if (![IO.Directory]::GetFileSystemEntries($path).Length) { [IO.Directory]::Delete($path) }
            } elseif ($directory -and ($attributes -band [IO.FileAttributes]::ReparsePoint)) {
                [IO.Directory]::Delete($path)
            } else {
                if (($attributes -band [IO.FileAttributes]::ReadOnly) -and
                    !($attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    [IO.File]::SetAttributes($path, ($attributes -band (-bnot [IO.FileAttributes]::ReadOnly)))
                }
                [IO.File]::Delete($path)
            }
        }
    }
    Write-Host 'Ownsei uninstalled.'
} finally {
    [IO.File]::Delete($temp)
}
