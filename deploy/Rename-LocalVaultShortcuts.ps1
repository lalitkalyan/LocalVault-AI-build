#Requires -Version 5.1
<#
.SYNOPSIS
  Rename existing Desktop, Start menu, and taskbar shortcuts to "LocalVault AI".

.DESCRIPTION
  The program file stays Local Chat.exe. This script only renames shortcut files
  that already point at a file named Local Chat.exe. It writes the same target,
  arguments, working directory, icon, window style, and hotkey back before saving,
  and sets the shortcut description to LocalVault AI.

  It does not create a shortcut you do not already have.
  It does not move or rename Local Chat.exe.
  It does not change %LOCALAPPDATA%\LocalChat.
  It does not touch the firewall.

  The program is usually:
    %LOCALAPPDATA%\Programs\LocalChat\Local Chat.exe

  If LocalVault AI.lnk already exists in the same folder and points at that same exe,
  the old Local Chat.lnk is removed so you do not get two icons. If LocalVault AI.lnk
  points somewhere else, both shortcuts are left alone.

.PARAMETER DryRun
  Print what would change and do not write.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Rename-LocalVaultShortcuts.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$OldLeaf = "Local Chat.lnk"
$NewLeaf = "LocalVault AI.lnk"

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

function Save-SameTarget {
    param($Link, [string]$Path)
    $target = $Link.TargetPath
    $arguments = $Link.Arguments
    $work = $Link.WorkingDirectory
    $icon = $Link.IconLocation
    $style = $Link.WindowStyle
    $hotkey = $Link.Hotkey
    $Link.Description = "LocalVault AI"
    $Link.TargetPath = $target
    $Link.Arguments = $arguments
    $Link.WorkingDirectory = $work
    $Link.IconLocation = $icon
    $Link.WindowStyle = $style
    $Link.Hotkey = $hotkey
    $Link.Save()
    $check = Get-ShellLink $Path
    if (-not (Same-Target $check.TargetPath $target)) {
        throw "Shortcut target changed unexpectedly: $Path"
    }
    if ($check.Arguments -ne $arguments) {
        throw "Shortcut arguments changed unexpectedly: $Path"
    }
}

$roots = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
    (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"),
    (Join-Path $env:APPDATA "Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar")
)

$renamed = 0
$removedDupes = 0
$skipped = 0
foreach ($root in $roots) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
    $links = @(Get-ChildItem -LiteralPath $root -Filter $OldLeaf -Recurse -ErrorAction SilentlyContinue)
    foreach ($link in $links) {
        try {
            $sc = Get-ShellLink $link.FullName
        } catch {
            Write-Host "Skipped (could not read): $($link.FullName)"
            $skipped++
            continue
        }
        $target = $sc.TargetPath
        if ([IO.Path]::GetFileName($target) -ne "Local Chat.exe") {
            Write-Host "Skipped (not Local Chat.exe): $($link.FullName) -> $target"
            $skipped++
            continue
        }
        $dest = Join-Path $link.DirectoryName $NewLeaf
        if (Test-Path -LiteralPath $dest) {
            $existing = Get-ShellLink $dest
            if (([IO.Path]::GetFileName($existing.TargetPath) -eq "Local Chat.exe") -and (Same-Target $existing.TargetPath $target)) {
                Write-Host "LocalVault AI.lnk already points at the same program. Removing the old name only: $($link.FullName)"
                if (-not $DryRun) {
                    Remove-Item -LiteralPath $link.FullName -Force
                }
                $removedDupes++
            } else {
                Write-Host "Left both shortcuts. $dest points somewhere else."
                $skipped++
            }
            continue
        }
        Write-Host "Rename $($link.FullName) -> $dest"
        Write-Host "  target stays: $target"
        if (-not $DryRun) {
            Save-SameTarget -Link $sc -Path $link.FullName
            Rename-Item -LiteralPath $link.FullName -NewName $NewLeaf
        }
        $renamed++
    }
}

if ($DryRun) {
    Write-Host "Dry run only. Shortcuts renamed: $renamed. Old duplicates that would be removed: $removedDupes. Skipped: $skipped."
} else {
    Write-Host "Renamed: $renamed. Old duplicates removed: $removedDupes. Skipped: $skipped."
}
if ($renamed -eq 0 -and $removedDupes -eq 0) {
    Write-Host "No Local Chat.lnk pointing at Local Chat.exe was found. Nothing was created."
}
exit 0
