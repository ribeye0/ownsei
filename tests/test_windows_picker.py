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

# The UI objects are replaced; the production script still binds arguments,
# resolves paths, handles cancellation and disposes its UI objects.
PROBE = r"""
param([string]$Picker, [string]$Kind, [string]$Initial = '')
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition 'namespace System.Windows.Forms { public enum DialogResult { OK = 1, Cancel = 2 } }'
function Add-Type {
    param([string]$AssemblyName, [string]$TypeDefinition)
    if ($TypeDefinition) {
        # Compile the shipped interop declarations, then substitute only the UI call.
        Microsoft.PowerShell.Utility\Add-Type -TypeDefinition ($TypeDefinition.Replace('namespace Ownsei', 'namespace CompiledOwnsei'))
        Microsoft.PowerShell.Utility\Add-Type -TypeDefinition @'
using System;
namespace Ownsei {
    public static class ExplorerFolderPicker {
        public static string Select(IntPtr owner, string initialDirectory) {
            if (owner != new IntPtr(7) || initialDirectory != Environment.GetEnvironmentVariable("OWNSEI_PICKER_EXPECTED"))
                throw new InvalidOperationException("Incorrect owner or initial folder");
            Environment.SetEnvironmentVariable("OWNSEI_FOLDER_SHOWN", "1");
            return Environment.GetEnvironmentVariable("OWNSEI_PICKER_SELECTED");
        }
    }
}
'@
    }
}
function New-Object {
    param([string]$TypeName, [object[]]$ArgumentList)
    if ($TypeName -eq 'System.Windows.Forms.Form') {
        $owner = [pscustomobject]@{ ShowInTaskbar = $true; TopMost = $false; Opacity = 1; StartPosition = '' }
        $owner | Add-Member NoteProperty Handle ([IntPtr]::Zero)
        $owner | Add-Member ScriptMethod Show {
            if (-not $this.TopMost -or $this.ShowInTaskbar) { throw 'Incorrect owner setup' }
            $this.Handle = [IntPtr]7
            $global:OwnseiOwnerShown = $true
        }
        $owner | Add-Member ScriptMethod Activate {}
        $owner | Add-Member ScriptMethod Dispose { $global:OwnseiOwnerDisposed = $true }
        return $owner
    }
    if ($TypeName -eq 'System.Windows.Forms.OpenFileDialog') {
        $dialog = [pscustomobject]@{
            Title = ''; Filter = '';
            CheckFileExists = $false; InitialDirectory = ''; FileName = ''
        }
        $dialog | Add-Member ScriptMethod ShowDialog {
            $owner = $args[0]
            if (-not $global:OwnseiOwnerShown -or -not $owner.TopMost -or $owner.ShowInTaskbar) {
                throw 'Picker requires a visible-in-front owner without a taskbar button'
            }
            $actual = $this.InitialDirectory
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
if (-not $global:OwnseiOwnerDisposed) { throw 'Owner was not disposed' }
if ($Kind -eq 'directory' -and $env:OWNSEI_FOLDER_SHOWN -ne '1') { throw 'Folder picker was not shown' }
if ($Kind -eq 'file' -and (-not $global:OwnseiPickerShown -or -not $global:OwnseiPickerDisposed)) {
    throw 'Dialog lifecycle incomplete'
}
"""


@pytest.mark.parametrize("kind", ["file", "directory"])
@pytest.mark.parametrize("start", ["empty", "whitespace", "missing", "directory", "file"])
def test_powershell_picker_initial_path_and_cancel(tmp_path, kind, start):
    exercise_picker(tmp_path, kind, start)


def test_explorer_returns_selected_folder_verbatim(tmp_path):
    exercise_picker(tmp_path, "directory", "empty", select=True)


def exercise_picker(tmp_path, kind, start, select=False):
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
        env={
            **os.environ,
            "OWNSEI_PICKER_EXPECTED": str(expected),
            **({"OWNSEI_PICKER_SELECTED": str(folder)} if select else {}),
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (f"{folder}\n" if select else "")
