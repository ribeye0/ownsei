param([string]$ReportDirectory = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Run this check on native Windows.' }
if (!$ReportDirectory) { $ReportDirectory = Join-Path $root ('artifacts\validation\windows-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$output = [IO.Path]::GetFullPath($ReportDirectory)
if (Test-Path -LiteralPath $output) { throw "Use a new report directory: $output" }
New-Item -ItemType Directory -Path $output | Out-Null
$env:PYTHONUTF8 = '1'
$python = Join-Path $root '.venv\Scripts\python.exe'
$report = [ordered]@{
    status = 'running'; root = $root; started = (Get-Date).ToUniversalTime().ToString('o');
    os = [Environment]::OSVersion.VersionString; powershell = $PSVersionTable.PSVersion.ToString();
    scope = 'Source regression only; release, native UI and GPU acceptance are separate.';
    steps = @()
}
function Save-Report {
    [IO.File]::WriteAllText((Join-Path $output 'report.json'), ($report | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))
}
function Run-Step([string]$Name, [string]$Program, [string[]]$Arguments) {
    $log = Join-Path $output ($Name + '.log')
    $row = [ordered]@{ name = $Name; status = 'running'; command = @($Program) + $Arguments; log = $log }
    $report.steps += $row
    Save-Report
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        if (!(Get-Command $Program -ErrorAction SilentlyContinue)) { throw "Program not found: $Program" }
        # Windows PowerShell treats native stderr as ErrorRecord objects. Keep
        # the output, and judge native tools by their exit code instead.
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $Program @Arguments 2>&1 | Tee-Object -LiteralPath $log | Out-Host
            $code = $LASTEXITCODE
        } finally { $ErrorActionPreference = $previousPreference }
        $row.exit_code = $code
        if ($code -ne 0) { throw "Exit code $code" }
        $row.status = 'passed'
    } catch {
        $row.status = 'failed'
        $row.error = $_.Exception.Message
        Add-Content -LiteralPath $log -Value $row.error
        Write-Warning "$Name failed: $($row.error)"
    } finally {
        $row.seconds = [math]::Round($timer.Elapsed.TotalSeconds, 3)
        Save-Report
    }
}
Push-Location -LiteralPath $root
try {
    Save-Report
    Run-Step 'python-version' $python @('--version')
    Run-Step 'ruff' $python @('-m', 'ruff', 'check', 'src', 'scripts', 'tests')
    $tests = @(
        'tests/test_platforms.py', 'tests/test_windows_picker.py', 'tests/test_database_lifecycle.py',
        'tests/test_api.py', 'tests/test_project_management.py', 'tests/test_delivery_files.py',
        'tests/test_versions.py', 'tests/test_translation_contract.py', 'tests/test_translation_retry.py',
        'tests/test_translation_names.py', 'tests/test_translation_document.py', 'tests/test_webvtt.py',
        'tests/test_audio.py', 'tests/test_launcher.py', 'tests/test_user_paths.py', 'tests/test_release_packages.py',
        'tests/test_model_management.py::test_running_worker_cannot_overwrite_cancelled_or_replaced_job',
        'tests/test_model_management.py::test_progress_writer_excludes_catalog_reader_during_atomic_replacement',
        'tests/test_model_management.py::test_catalog_does_not_report_ready_before_preparation_finishes'
    )
    $junitReport = Join-Path $output 'pytest.xml'
    Run-Step 'pytest' $python (@('-m', 'pytest', '-ra', '-q', '--junitxml', $junitReport) + $tests)
    Push-Location -LiteralPath (Join-Path $root 'frontend')
    try {
        Run-Step 'frontend-tests' 'npm.cmd' @('test')
        Run-Step 'frontend-build' 'npm.cmd' @('run', 'build')
    } finally { Pop-Location }
    $row = [ordered]@{ name = 'powershell-parser'; status = 'running' }
    $report.steps += $row
    try {
        Get-ChildItem -LiteralPath (Join-Path $root 'scripts') -Filter '*.ps1' | ForEach-Object {
            $errors = $null
            [void][Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$null, [ref]$errors)
            if ($errors.Count -gt 0) { throw ($errors | Out-String) }
        }
        $row.status = 'passed'
    } catch { $row.status = 'failed'; $row.error = $_.Exception.Message }
    Save-Report
    Run-Step 'source-audit' $python @('scripts/package_source.py', '--check')
    $report.status = if (@($report.steps | Where-Object { $_.status -ne 'passed' }).Count) { 'failed' } else { 'passed' }
} catch { $report.status = 'failed'; $report.error = $_.Exception.Message }
finally {
    $report.finished = (Get-Date).ToUniversalTime().ToString('o')
    Save-Report
    Pop-Location
}
Write-Host "Validation $($report.status). Report: $output"
if ($report.status -ne 'passed') { exit 1 }
