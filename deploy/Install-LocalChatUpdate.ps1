#Requires -Version 5.1
<#
.SYNOPSIS
  Back up Local Chat chats/settings, then copy an updated source tree or a built app folder.

.DESCRIPTION
  The live install path on this PC is not assumed. The script looks in a few known
  folders and in Desktop / Start-menu shortcuts. If it finds zero or several, it
  stops unless you pass -InstallDir.

  Before it changes program files it:
    - refuses to continue while "Local Chat" is running (it does not kill the process)
    - copies %LOCALAPPDATA%\LocalChat\data to
      %LOCALAPPDATA%\LocalChat\backups\<timestamp>\data
    - copies a data folder that lives inside the install directory, if that is a
      different folder
    - copies the program files it is about to replace into the same backup folder

  It does not delete chats or settings. It does not open firewall ports.
  It does not change the network category. It does not build Local Chat.exe
  (build_exe.bat does that on the MSI, and this environment has no Windows build).

.PARAMETER InstallDir
  Folder that contains Local Chat.exe or server.py. Required when discovery is
  ambiguous. If the folder does not exist yet, it is created and treated as a
  new source install.

.PARAMETER SourceDir
  Updated source tree. Defaults to the parent of this deploy folder.

.PARAMETER DistDir
  A built "dist\Local Chat" folder (the one that contains Local Chat.exe).
  Required to replace an existing exe install. Without it, an exe install is
  backed up and left in place.

.PARAMETER DryRun
  Print the plan and exit without copying.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Install-LocalChatUpdate.ps1 -DryRun

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Install-LocalChatUpdate.ps1 -DryRun

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Install-LocalChatUpdate.ps1 -InstallDir "$env:LOCALAPPDATA\Programs\Local Chat" -DistDir ".\dist\Local Chat"
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "",
    [string]$SourceDir = "",
    [string]$DistDir = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $SourceDir) {
    $SourceDir = Split-Path -Parent $PSScriptRoot
}
$SourceDir = (Resolve-Path -LiteralPath $SourceDir).Path

function Add-Dir {
    param($List, [string]$Dir)
    if (-not $Dir) { return }
    $exe = Join-Path $Dir "Local Chat.exe"
    $server = Join-Path $Dir "server.py"
    if (-not (Test-Path -LiteralPath $exe) -and -not (Test-Path -LiteralPath $server)) { return }
    if (-not (Test-Path -LiteralPath $Dir)) { return }
    $full = (Resolve-Path -LiteralPath $Dir).Path
    if ($List -notcontains $full) { [void]$List.Add($full) }
}

function Get-ShortcutTarget {
    param([string]$LinkPath)
    $shell = New-Object -ComObject WScript.Shell
    return $shell.CreateShortcut($LinkPath).TargetPath
}

function Find-InstallDirs {
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($dir in @(
        (Join-Path $env:LOCALAPPDATA "Programs\LocalChat"),
        (Join-Path $env:LOCALAPPDATA "Programs\Local Chat"),
        (Join-Path $env:LOCALAPPDATA "Local Chat"),
        (Join-Path $env:USERPROFILE "local-chat-app")
    )) {
        Add-Dir $found $dir
    }
    $roots = @(
        [Environment]::GetFolderPath("Desktop"),
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
        (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs")
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        Get-ChildItem -LiteralPath $root -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            try { $target = Get-ShortcutTarget $_.FullName } catch { return }
            if ($target) { Add-Dir $found (Split-Path -Parent $target) }
        }
    }
    return @($found)
}

function Copy-Tree {
    param([string]$From, [string]$To, [string[]]$SkipNames)
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Get-ChildItem -LiteralPath $From -Force | ForEach-Object {
        if ($SkipNames -contains $_.Name) { return }
        if ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
        $dest = Join-Path $To $_.Name
        if ($_.PSIsContainer) {
            Copy-Tree -From $_.FullName -To $dest -SkipNames $SkipNames
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
        }
    }
}

function Same-Path {
    param([string]$A, [string]$B)
    if (-not $A -or -not $B) { return $false }
    $na = [IO.Path]::GetFullPath($A).TrimEnd("\")
    $nb = [IO.Path]::GetFullPath($B).TrimEnd("\")
    return ($na.Equals($nb, [StringComparison]::OrdinalIgnoreCase))
}

$explicit = [bool]$InstallDir
if ($explicit) {
    if ((Test-Path -LiteralPath $InstallDir) -and (Get-Item -LiteralPath $InstallDir).PSIsContainer) {
        $target = (Resolve-Path -LiteralPath $InstallDir).Path
    } else {
        $target = [IO.Path]::GetFullPath($InstallDir)
    }
} else {
    $candidates = @(Find-InstallDirs)
    if ($candidates.Count -eq 0) {
        Write-Host "No Local Chat install was found. Looked in:"
        Write-Host "  $env:LOCALAPPDATA\Programs\LocalChat"
        Write-Host "  $env:LOCALAPPDATA\Programs\Local Chat"
        Write-Host "  $env:LOCALAPPDATA\Local Chat"
        Write-Host "  $env:USERPROFILE\local-chat-app"
        Write-Host "  Desktop and Start-menu shortcuts to Local Chat.exe"
        Write-Host "Pass -InstallDir with the real folder. Nothing was changed."
        exit 4
    }
    if ($candidates.Count -gt 1) {
        Write-Host "More than one Local Chat folder was found. Pass -InstallDir. Nothing was changed."
        $candidates | ForEach-Object { Write-Host "  $_" }
        exit 3
    }
    $target = $candidates[0]
}

$running = @(Get-Process -Name "Local Chat" -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Write-Host "LocalVault AI is running. Close its window so it can stop its server and any Ollama it started."
    Write-Host "The process name is still Local Chat. This script will not force it to quit. Nothing was changed."
    exit 1
}

$dataDir = if ($env:LOCALCHAT_DATA) { $env:LOCALCHAT_DATA } else { Join-Path $env:LOCALAPPDATA "LocalChat\data" }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = Join-Path $env:LOCALAPPDATA "LocalChat\backups\$stamp"
$isExe = (Test-Path -LiteralPath (Join-Path $target "Local Chat.exe"))
$isSource = (Test-Path -LiteralPath (Join-Path $target "server.py"))

Write-Host "Source:  $SourceDir"
Write-Host "Install: $target"
Write-Host "Backup:  $backupRoot"
Write-Host "Chats:   $dataDir"
if ($isExe) { Write-Host "Install type: exe" }
elseif ($isSource -or $explicit) { Write-Host "Install type: source" }
else { Write-Host "Install type: new folder" }

if ($DryRun) {
    Write-Host "Dry run only. No files were copied."
    exit 0
}

New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
if (Test-Path -LiteralPath $dataDir) {
    Copy-Tree -From $dataDir -To (Join-Path $backupRoot "data") -SkipNames @()
    Write-Host "Backed up chats and settings."
} else {
    Write-Host "No chat folder at $dataDir. There was nothing there to back up."
}

$installData = Join-Path $target "data"
if ((Test-Path -LiteralPath $installData) -and -not (Same-Path $installData $dataDir)) {
    Copy-Tree -From $installData -To (Join-Path $backupRoot "install-data") -SkipNames @()
    Write-Host "Backed up the data folder inside the install directory. It was not deleted."
}

if (Test-Path -LiteralPath $target) {
    Copy-Tree -From $target -To (Join-Path $backupRoot "program") -SkipNames @(
        "data", ".venv-build", "build", "__pycache__", ".git", "backups")
    Write-Host "Backed up program files (chats were stored in the data backup, not mixed in)."
}

if (-not (Test-Path -LiteralPath $target)) {
    New-Item -ItemType Directory -Force -Path $target | Out-Null
}

$sourceFiles = @(
    "server.py", "desktop.py", "lan.py", "app.js", "reliability.js", "style.css", "index.html", "favicon.svg",
    "apple-touch-icon.png", "start.bat", "start.sh", "Start-iPhone-Access.bat", "build_exe.bat",
    "LocalChat.spec", "requirements-desktop.txt", "make_icon.py", "README.md", ".gitignore", ".gitattributes",
    "manifest.webmanifest"
)
$sourceDirs = @("docs", "deploy", "tests", "vendor", "desktop_assets")
if (-not ($isExe -and -not $isSource)) {
    foreach ($name in ($sourceFiles + $sourceDirs)) {
        if (-not (Test-Path -LiteralPath (Join-Path $SourceDir $name))) {
            Write-Error "Updated source is missing $name. The install folder was not changed."
            exit 1
        }
    }
}

if ($isExe -and -not $isSource) {
    if (-not $DistDir) {
        Write-Host ""
        Write-Host "This install is Local Chat.exe. The exe was not replaced."
        Write-Host "On this PC, build the updated source with build_exe.bat, then re-run:"
        Write-Host "  -InstallDir `"$target`" -DistDir `"<source>\dist\Local Chat`""
        Write-Host "Your chats are still in $dataDir and a backup is in $backupRoot."
        exit 0
    }
    $builtExe = Join-Path $DistDir "Local Chat.exe"
    if (-not (Test-Path -LiteralPath $builtExe)) {
        Write-Error "DistDir does not contain Local Chat.exe: $DistDir"
        exit 1
    }
    Copy-Tree -From (Resolve-Path -LiteralPath $DistDir).Path -To $target -SkipNames @("data", ".venv-build", "build", "__pycache__", ".git")
    Write-Host "Replaced program files from the built folder. Chats and settings were not overwritten."
    exit 0
}

foreach ($name in $sourceFiles) {
    Copy-Item -LiteralPath (Join-Path $SourceDir $name) -Destination (Join-Path $target $name) -Force
}
foreach ($dirName in $sourceDirs) {
    Copy-Tree -From (Join-Path $SourceDir $dirName) -To (Join-Path $target $dirName) -SkipNames @("__pycache__", ".git")
}
Write-Host "Updated the source install. The data folder was not replaced."
Write-Host "Backup: $backupRoot"
exit 0
