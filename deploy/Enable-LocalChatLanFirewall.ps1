#Requires -Version 5.1
<#
.SYNOPSIS
  Allow Local Chat's own TCP port from the local subnet on Private networks only.

.DESCRIPTION
  Adds one inbound Windows Firewall rule named "Local Chat iPhone LAN".
  The rule is Private profile, TCP, the Local Chat port, remote address LocalSubnet,
  and the Local Chat executable (or a Python you pass explicitly).

  This script does not:
    - change the network category (Public / Private / Domain)
    - open the Ollama port
    - open every port, or every program
    - add a Public-profile or Any-remote rule
    - touch existing rules other than one previous "Local Chat iPhone LAN" rule,
      which it replaces so the port and program stay in sync

  Run it from an Administrator PowerShell on the MSI after Local Chat is installed.
  Close this script's window when it finishes. It does not start Local Chat.

.PARAMETER Port
  The port shown in Local Chat's iPhone panel. Default 8080. 11434 is refused.

.PARAMETER Program
  Full path to Local Chat.exe, or to the python.exe that runs desktop.py.
  If omitted, the script looks in a few known folders and Start-menu shortcuts.
  If it finds more than one, it stops and asks you to pass -Program.

.PARAMETER DryRun
  Print the rule that would be added and exit. Does not need Administrator.

.PARAMETER IConfirmPrivateNetwork
  Only if Windows returns no connected network profile. You are stating that you
  already set this PC's network to Private yourself. The script still does not
  change the category.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Enable-LocalChatLanFirewall.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Enable-LocalChatLanFirewall.ps1 -DryRun

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\deploy\Enable-LocalChatLanFirewall.ps1 -Port 8080 -Program "$env:LOCALAPPDATA\Programs\LocalChat\Local Chat.exe"
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [string]$Program = "",
    [switch]$DryRun,
    [switch]$IConfirmPrivateNetwork
)

$ErrorActionPreference = "Stop"
$RuleName = "Local Chat iPhone LAN"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Add-Candidate {
    param($List, [string]$Path)
    if (-not $Path) { return }
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $full = (Resolve-Path -LiteralPath $Path).Path
    if ($List -notcontains $full) { [void]$List.Add($full) }
}

function Get-ShortcutTarget {
    param([string]$LinkPath)
    $shell = New-Object -ComObject WScript.Shell
    return $shell.CreateShortcut($LinkPath).TargetPath
}

function Find-LocalChatPrograms {
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($dir in @(
        (Join-Path $env:LOCALAPPDATA "Programs\LocalChat"),
        (Join-Path $env:LOCALAPPDATA "Programs\Local Chat"),
        (Join-Path $env:LOCALAPPDATA "Local Chat"),
        (Join-Path $env:USERPROFILE "local-chat-app")
    )) {
        Add-Candidate $found (Join-Path $dir "Local Chat.exe")
    }
    $roots = @(
        [Environment]::GetFolderPath("Desktop"),
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
        (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs")
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        Get-ChildItem -LiteralPath $root -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $target = Get-ShortcutTarget $_.FullName
            } catch {
                return
            }
            if ($target -and ([IO.Path]::GetFileName($target) -eq "Local Chat.exe")) {
                Add-Candidate $found $target
            }
        }
    }
    return @($found)
}

if ($Port -eq 11434) {
    Write-Error "Refusing to open the Ollama port (11434). Local Chat's iPhone port is the one shown in the app, usually 8080."
    exit 1
}
if ($Port -lt 1 -or $Port -gt 65535) {
    Write-Error "Port must be the Local Chat port shown in the iPhone panel."
    exit 1
}

$programPath = ""
if ($Program) {
    if (-not (Test-Path -LiteralPath $Program)) {
        Write-Error "Program not found: $Program"
        exit 1
    }
    $programPath = (Resolve-Path -LiteralPath $Program).Path
    if ((Get-Item -LiteralPath $programPath).PSIsContainer) {
        Write-Error "Program must be Local Chat.exe or python.exe, not a folder: $programPath"
        exit 1
    }
} else {
    $candidates = @(Find-LocalChatPrograms)
    if ($candidates.Count -eq 1) {
        $programPath = $candidates[0]
    } elseif ($candidates.Count -eq 0) {
        Write-Host "No Local Chat.exe was found in the usual folders or shortcuts."
        Write-Host "Pass -Program with the full path of Local Chat.exe, or of the python.exe that runs desktop.py."
        Write-Host "This script will not add a port-only rule."
        exit 3
    } else {
        Write-Host "More than one Local Chat.exe was found. Pass -Program with the one you actually start:"
        $candidates | ForEach-Object { Write-Host "  $_" }
        exit 3
    }
}

$leaf = [IO.Path]::GetFileName($programPath)
if ($leaf -match '^(?i)ollama') {
    Write-Error "Refusing to add a firewall rule for Ollama. Pass Local Chat.exe or python.exe."
    exit 1
}

Write-Host "Program: $programPath"
Write-Host "TCP port: $Port"
Write-Host "Profile: Private only. Remote address: LocalSubnet. Direction: Inbound."
Write-Host "This does not change Public/Private and does not open Ollama."

$profiles = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue)
$connected = @($profiles | Where-Object {
    $_.IPv4Connectivity -and $_.IPv4Connectivity -notin @("Disconnected", "NoTraffic")
})
$private = @($connected | Where-Object { $_.NetworkCategory -eq "Private" })
if ($connected.Count -gt 0 -and $private.Count -eq 0) {
    Write-Host ""
    Write-Host "A connected network on this PC is not set to Private:"
    $connected | ForEach-Object { Write-Host ("  {0}: {1}" -f $_.Name, $_.NetworkCategory) }
    Write-Host "This script will not change that."
    Write-Host "Set it yourself: Settings, Network and internet, Wi-Fi, your network, Network profile, Private."
    Write-Host "Then run this script again. No firewall rule was added."
    exit 2
}
if ($connected.Count -eq 0 -and -not $IConfirmPrivateNetwork) {
    Write-Host "Windows did not report a connected network profile. No rule was added."
    Write-Host "Confirm this PC's Wi-Fi is Private, then re-run with -IConfirmPrivateNetwork."
    Write-Host "This script still will not change the category for you."
    exit 2
}

if ($DryRun) {
    Write-Host "Dry run only. No firewall rule was added or removed."
    exit 0
}

if (-not (Test-Administrator)) {
    Write-Error "Run this script in an Administrator PowerShell. No rule was added."
    exit 1
}

$existing = @(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -eq $RuleName })
if ($existing.Count -gt 0) {
    Write-Host "Replacing the existing '$RuleName' rule."
    $existing | Remove-NetFirewallRule
}

New-NetFirewallRule `
    -DisplayName $RuleName `
    -Description "Inbound TCP for LocalVault AI on a Private network, local subnet only. Does not include Ollama." `
    -Direction Inbound `
    -Action Allow `
    -Profile Private `
    -Protocol TCP `
    -LocalPort $Port `
    -RemoteAddress LocalSubnet `
    -Program $programPath | Out-Null

Write-Host "Added '$RuleName' for $leaf on TCP $Port (Private, LocalSubnet)."
Write-Host "Remove it later with Disable-LocalChatLanFirewall.ps1."
exit 0
