param([ValidateSet('file','directory')][string]$Kind, [string]$Initial = '')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$dialog = $null
$owner = $null
try {
    Add-Type -AssemblyName System.Windows.Forms
    $initialDirectory = [Environment]::GetFolderPath('UserProfile')
    # Test-Path with an empty string throws in Windows PowerShell 5.1.
    # Invalid or removed paths should also fall back to the user's home folder.
    if (-not [string]::IsNullOrWhiteSpace($Initial)) {
        $item = Get-Item -LiteralPath $Initial -ErrorAction SilentlyContinue
        if ($null -ne $item) {
            if ($item.PSIsContainer) { $initialDirectory = $item.FullName }
            elseif ($item -is [System.IO.FileInfo]) { $initialDirectory = $item.DirectoryName }
        }
    }
    if ($Kind -eq 'directory') {
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Ownsei - Select audio folder'
        $dialog.ShowNewFolderButton = $false
        $dialog.SelectedPath = $initialDirectory
    } else {
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = 'Ownsei - Select audio'
        $dialog.Filter = 'Audio|*.wav;*.flac;*.mp3;*.m4a;*.ogg;*.opus|All files|*.*'
        $dialog.CheckFileExists = $true
        $dialog.InitialDirectory = $initialDirectory
    }
    # This helper has no main window. Keep its dialog above the browser
    # even when launched from an HTTP request or a hidden console.
    $owner = New-Object System.Windows.Forms.Form
    $owner.ShowInTaskbar = $false
    $owner.TopMost = $true
    $owner.Opacity = 0
    $owner.StartPosition = 'CenterScreen'
    $owner.Show()
    $owner.Activate()
    if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        if ($Kind -eq 'directory') { [Console]::WriteLine($dialog.SelectedPath) }
        else { [Console]::WriteLine($dialog.FileName) }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $dialog) { $dialog.Dispose() }
    if ($null -ne $owner) { $owner.Dispose() }
}
