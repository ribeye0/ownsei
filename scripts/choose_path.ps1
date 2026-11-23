param([ValidateSet('file','directory')][string]$Kind, [string]$Initial = '')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Windows.Forms
$dialog = $null
try {
    if ($Kind -eq 'directory') {
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Ownsei - Select audio folder'
        $dialog.ShowNewFolderButton = $false
        if (Test-Path -LiteralPath $Initial -PathType Container) { $dialog.SelectedPath = $Initial }
    } else {
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = 'Ownsei - Select audio'
        $dialog.Filter = 'Audio|*.wav;*.flac;*.mp3;*.m4a;*.ogg;*.opus|All files|*.*'
        $dialog.CheckFileExists = $true
        if (Test-Path -LiteralPath $Initial -PathType Container) { $dialog.InitialDirectory = $Initial }
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        if ($Kind -eq 'directory') { [Console]::WriteLine($dialog.SelectedPath) }
        else { [Console]::WriteLine($dialog.FileName) }
    }
} finally { if ($null -ne $dialog) { $dialog.Dispose() } }
