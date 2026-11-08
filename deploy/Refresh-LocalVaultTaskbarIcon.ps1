#Requires -Version 5.1
<#
.SYNOPSIS
  Point existing Local Chat.exe shortcuts at the LocalVault AI icon.

.DESCRIPTION
  Finds shortcuts on the Desktop, in the Start menu, and in the taskbar pin
  folder whose target file is already named Local Chat.exe. For each one it
  writes the same target, arguments, working directory, window style, hotkey,
  and description back, then sets IconLocation to the icon file next to that
  program and sets System.AppUserModel.ID to LocalChat.Desktop.

  That is the same id desktop.py sets before it opens a window. The running
  window and the pin then share one taskbar button and the branded icon.

  It does not create a shortcut you do not already have.
  It does not retarget a shortcut that points at python.exe or anything else.
  It does not rename or delete shortcuts.
  It does not change %LOCALAPPDATA%\LocalChat.
  It does not touch the firewall.
  It does not restart Explorer.

  Icon file, first match next to that shortcut's program:
    icon.ico
    desktop_assets\icon.ico
    _internal\desktop_assets\icon.ico
  If none of those files is there, IconLocation is the program file itself, index 0.

  Windows can keep a cached picture for a pin. If the pin still looks wrong
  after this script, unpin it and pin Local Chat.exe again.

.PARAMETER DryRun
  Print what would change and do not write.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Refresh-LocalVaultTaskbarIcon.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$AppUserModelId = "LocalChat.Desktop"

$ShortcutCode = @'
using System;
using System.Runtime.InteropServices;

namespace LocalVault {
    public static class ShortcutIcon {
        public static void SetAppUserModelId(string shortcutPath, string appId) {
            object link = new CShellLink();
            IPersistFile file = (IPersistFile)link;
            file.Load(shortcutPath, 0);
            IPropertyStore store = (IPropertyStore)link;
            PROPERTYKEY key = new PROPERTYKEY();
            key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
            key.pid = 5;
            PROPVARIANT pv = new PROPVARIANT();
            pv.vt = 31;
            pv.pszVal = Marshal.StringToCoTaskMemUni(appId);
            try {
                store.SetValue(ref key, ref pv);
                store.Commit();
            } finally {
                PropVariantClear(ref pv);
            }
            file.Save(shortcutPath, true);
            SHChangeNotify(0x00002000, 0x00001005, shortcutPath, IntPtr.Zero);
        }

        [DllImport("ole32.dll")]
        static extern int PropVariantClear(ref PROPVARIANT pvar);

        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        static extern void SHChangeNotify(int eventId, uint flags, string item1, IntPtr item2);

        [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
        class CShellLink { }

        [ComImport, Guid("0000010b-0000-0000-C000-000000000046"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IPersistFile {
            void GetClassID(out Guid pClassID);
            [PreserveSig] int IsDirty();
            void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
            void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
            void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
            void GetCurFile(out IntPtr ppszFileName);
        }

        [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
         InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IPropertyStore {
            void GetCount(out uint cProps);
            void GetAt(uint iProp, out PROPERTYKEY pkey);
            void GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
            void SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
            void Commit();
        }

        [StructLayout(LayoutKind.Sequential, Pack = 4)]
        struct PROPERTYKEY {
            public Guid fmtid;
            public uint pid;
        }

        [StructLayout(LayoutKind.Explicit)]
        struct PROPVARIANT {
            [FieldOffset(0)] public ushort vt;
            [FieldOffset(8)] public IntPtr pszVal;
        }
    }
}
'@

function Get-ShellLink {
    param([string]$LinkPath)
    $shell = New-Object -ComObject WScript.Shell
    return $shell.CreateShortcut($LinkPath)
}

function Same-Target {
    param([string]$A, [string]$B)
    if (-not $A -or -not $B) { return $false }
    $na = [IO.Path]::GetFullPath($A).TrimEnd("\")
    $nb = [IO.Path]::GetFullPath($B).TrimEnd("\")
    return $na.Equals($nb, [StringComparison]::OrdinalIgnoreCase)
}

function Get-IconLocation {
    param([string]$ExePath)
    $dir = Split-Path -Parent $ExePath
    foreach ($rel in @(
        "icon.ico",
        "desktop_assets\icon.ico",
        "_internal\desktop_assets\icon.ico"
    )) {
        $cand = Join-Path $dir $rel
        if (Test-Path -LiteralPath $cand) {
            return "$cand,0"
        }
    }
    return "$ExePath,0"
}

function Update-LocalChatShortcut {
    param($Link, [string]$Path, [string]$IconLocation)
    $target = $Link.TargetPath
    $arguments = $Link.Arguments
    $work = $Link.WorkingDirectory
    $style = $Link.WindowStyle
    $hotkey = $Link.Hotkey
    $description = $Link.Description
    Write-Host "Update $Path"
    Write-Host "  target stays: $target"
    Write-Host "  icon: $IconLocation"
    Write-Host "  AppUserModelID: $AppUserModelId"
    if ($DryRun) { return }
    $Link.TargetPath = $target
    $Link.Arguments = $arguments
    $Link.WorkingDirectory = $work
    $Link.WindowStyle = $style
    $Link.Hotkey = $hotkey
    $Link.Description = $description
    $Link.IconLocation = $IconLocation
    $Link.Save()
    # Release the shell link before the property-store save opens the same file.
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($Link)
    [LocalVault.ShortcutIcon]::SetAppUserModelId($Path, $AppUserModelId)
    $check = Get-ShellLink $Path
    if (-not (Same-Target $check.TargetPath $target)) {
        throw "Shortcut target changed unexpectedly: $Path"
    }
    if ($check.Arguments -ne $arguments) {
        throw "Shortcut arguments changed unexpectedly: $Path"
    }
    $iconFile = [IO.Path]::GetFileName(($IconLocation -replace ',0$', ''))
    if ($check.IconLocation -notlike "*$iconFile*") {
        throw "Shortcut icon was not saved: $Path"
    }
}

$roots = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
    (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"),
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar")
)

$updated = 0
$skipped = 0
$failed = 0
$compilerReady = $false
foreach ($root in $roots) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    $links = @(Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -ErrorAction SilentlyContinue)
    foreach ($link in $links) {
        $leaf = $link.Name
        $familiar = ($leaf -eq "Local Chat.lnk") -or ($leaf -eq "LocalVault AI.lnk")
        try {
            $sc = Get-ShellLink $link.FullName
        } catch {
            if ($familiar) { Write-Host "Skipped (could not read): $($link.FullName)" }
            $skipped++
            continue
        }
        $target = $sc.TargetPath
        if ([IO.Path]::GetFileName($target) -ne "Local Chat.exe") {
            if ($familiar) {
                Write-Host "Left alone (target is not Local Chat.exe): $($link.FullName) -> $target"
            }
            $skipped++
            continue
        }
        if (-not (Test-Path -LiteralPath $target)) {
            Write-Host "Skipped (program file is missing): $($link.FullName) -> $target"
            $skipped++
            continue
        }
        $iconLocation = Get-IconLocation $target
        try {
            if (-not $DryRun -and -not $compilerReady) {
                if (-not ("LocalVault.ShortcutIcon" -as [type])) {
                    Add-Type -TypeDefinition $ShortcutCode -Language CSharp
                }
                $compilerReady = $true
            }
            Update-LocalChatShortcut -Link $sc -Path $link.FullName -IconLocation $iconLocation
            $updated++
        } catch {
            Write-Host "Could not update $($link.FullName): $($_.Exception.Message)"
            $failed++
        }
    }
}

if ($DryRun) {
    Write-Host "Dry run only. Shortcuts that would be updated: $updated. Left alone: $skipped."
} else {
    Write-Host "Updated: $updated. Left alone: $skipped. Failed: $failed."
}
if ($updated -eq 0) {
    Write-Host "No shortcut pointing at Local Chat.exe was found. Nothing was created."
    Write-Host "A pin created while python.exe was running is left as it is. Unpin it and pin Local Chat.exe."
}
Write-Host "Explorer was not restarted. If a pin still shows the old picture, unpin it and pin Local Chat.exe again."
if ($failed -gt 0) { exit 1 }
exit 0
