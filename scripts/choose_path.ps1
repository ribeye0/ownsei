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
        # Windows PowerShell uses .NET Framework, whose FolderBrowserDialog
        # shows a tree. Use the Shell's common Open dialog in folder mode.
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

namespace Ownsei
{
    public static class ExplorerFolderPicker
    {
        private const uint PickFolders = 0x20;
        private const uint ForceFileSystem = 0x40;
        private const uint FileSystemPath = 0x80058000;
        private const int Cancelled = unchecked((int)0x800704C7);

        [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
        private class FileOpenDialog { }

        // Keep the native vtable order, including IModalWindow.Show.
        [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IFileDialog
        {
            [PreserveSig] int Show(IntPtr owner);
            void SetFileTypes(uint count, IntPtr filters);
            void SetFileTypeIndex(uint index);
            void GetFileTypeIndex(out uint index);
            void Advise(IntPtr events, out uint cookie);
            void Unadvise(uint cookie);
            void SetOptions(uint options);
            void GetOptions(out uint options);
            void SetDefaultFolder(IShellItem folder);
            void SetFolder(IShellItem folder);
            void GetFolder(out IShellItem folder);
            void GetCurrentSelection(out IShellItem item);
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
            void GetFileName(out IntPtr name);
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
            void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
            void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
            void GetResult(out IShellItem item);
        }

        [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellItem
        {
            void BindToHandler(IntPtr bindContext, ref Guid handler, ref Guid iid, out IntPtr result);
            void GetParent(out IShellItem parent);
            void GetDisplayName(uint format, out IntPtr name);
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
        private static extern int SHCreateItemFromParsingName(
            string path, IntPtr bindContext, ref Guid iid, out IShellItem item);

        public static string Select(IntPtr owner, string initialDirectory)
        {
            IFileDialog dialog = (IFileDialog)new FileOpenDialog();
            IShellItem initial = null;
            IShellItem selected = null;
            IntPtr path = IntPtr.Zero;

            try
            {
                uint options;
                dialog.GetOptions(out options);
                dialog.SetOptions(options | PickFolders | ForceFileSystem);
                dialog.SetTitle("Ownsei - Select audio folder");

                Guid iid = typeof(IShellItem).GUID;
                Marshal.ThrowExceptionForHR(SHCreateItemFromParsingName(
                    initialDirectory, IntPtr.Zero, ref iid, out initial));
                dialog.SetFolder(initial);

                int result = dialog.Show(owner);
                if (result == Cancelled)
                    return null;
                Marshal.ThrowExceptionForHR(result);

                dialog.GetResult(out selected);
                selected.GetDisplayName(FileSystemPath, out path);
                return Marshal.PtrToStringUni(path);
            }
            finally
            {
                if (path != IntPtr.Zero) Marshal.FreeCoTaskMem(path);
                if (selected != null) Marshal.ReleaseComObject(selected);
                if (initial != null) Marshal.ReleaseComObject(initial);
                Marshal.ReleaseComObject(dialog);
            }
        }
    }
}
"@
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
    if ($Kind -eq 'directory') {
        $selected = [Ownsei.ExplorerFolderPicker]::Select($owner.Handle, $initialDirectory)
        if ($null -ne $selected) { [Console]::WriteLine($selected) }
    } elseif ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
        [Console]::WriteLine($dialog.FileName)
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $dialog) { $dialog.Dispose() }
    if ($null -ne $owner) { $owner.Dispose() }
}
