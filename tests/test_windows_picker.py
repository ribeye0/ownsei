"""Execute the actual picker script without opening interactive dialogs.

Windows CI runs this with built-in Windows PowerShell 5.1. PowerShell 7 can
also exercise argument binding and initial-path handling on other platforms.
"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from onsei.util import ROOT

POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="PowerShell not installed")

# Only the dialog is replaced: the production script still binds arguments,
# resolves paths, handles cancellation and disposes its dialog instance.
PROBE = r"""
param([string]$Picker, [string]$Kind, [string]$Initial = '')
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition 'namespace System.Windows.Forms { public enum DialogResult { OK = 1, Cancel = 2 } }'
function Add-Type { param([string]$AssemblyName) }
function New-Object {
    param([string]$TypeName, [object[]]$ArgumentList)
    if ($TypeName -in @('System.Windows.Forms.OpenFileDialog', 'System.Windows.Forms.FolderBrowserDialog')) {
        $dialog = [pscustomobject]@{
            Description = ''; ShowNewFolderButton = $true; Title = ''; Filter = '';
            CheckFileExists = $false; SelectedPath = ''; InitialDirectory = ''; FileName = ''
        }
        $dialog | Add-Member ScriptMethod ShowDialog {
            $actual = if ($env:OWNSEI_PICKER_KIND -eq 'file') { $this.InitialDirectory } else { $this.SelectedPath }
            if ($actual -ne $env:OWNSEI_PICKER_EXPECTED) { throw "Unexpected initial directory: $actual" }
            $global:OwnseiPickerShown = $true
            return [System.Windows.Forms.DialogResult]::Cancel
        }
        $dialog | Add-Member ScriptMethod Dispose { $global:OwnseiPickerDisposed = $true }
        return $dialog
    }
    Microsoft.PowerShell.Utility\New-Object @PSBoundParameters
}
& $Picker -Kind $Kind -Initial $Initial
if (-not $global:OwnseiPickerShown -or -not $global:OwnseiPickerDisposed) { throw 'Dialog lifecycle incomplete' }
"""


@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("start", ["empty", "whitespace", "missing", "directory", "file"])
def test_powershell_picker_initial_path_and_cancel(tmp_path, kind, start):
    folder = tmp_path / "音聲 [test] & $(literal)"
    folder.mkdir()
    audio = folder / "01 日本語.wav"
    audio.touch()
    initial = {
        "empty": "",
        "whitespace": "  ",
        "missing": str(tmp_path / "missing"),
        "directory": str(folder),
        "file": str(audio),
    }[start]
    expected = folder if start in {"directory", "file"} else Path.home()
    probe = tmp_path / "probe.ps1"
    probe.write_text(PROBE, encoding="utf-8")
    args = [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA"]
    if os.name != "nt":  # STA is Windows-only in PowerShell 7.
        args.remove("-STA")
    args += ["-File", str(probe), "-Picker", str(ROOT / "scripts/choose_path.ps1"), "-Kind", kind]
    if initial:
        args += ["-Initial", initial]
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env={**os.environ, "OWNSEI_PICKER_EXPECTED": str(expected), "OWNSEI_PICKER_KIND": kind},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
