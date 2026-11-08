#Requires -Version 5.1
<#
.SYNOPSIS
  Remove only the "Local Chat iPhone LAN" firewall rule.

.DESCRIPTION
  Does not change the network category, does not remove other firewall rules,
  and does not stop Local Chat. Closing the app is what stops iPhone access.
  Removing this rule is the extra step that closes the Windows port allowance.

.PARAMETER DryRun
  Show the matching rule, if any, and do not remove it.
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RuleName = "Local Chat iPhone LAN"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$rules = @(Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -eq "Local Chat iPhone LAN" })

if ($rules.Count -eq 0) {
    Write-Host "No '$RuleName' firewall rule was present. Nothing was changed."
    exit 0
}

Write-Host "Found $($rules.Count) '$RuleName' rule(s)."
if ($DryRun) {
    $rules | ForEach-Object { Write-Host ("  {0} enabled={1} profile={2}" -f $_.DisplayName, $_.Enabled, $_.Profile) }
    Write-Host "Dry run only. No rule was removed."
    exit 0
}

if (-not (Test-Administrator)) {
    Write-Error "Run this script in an Administrator PowerShell. No rule was removed."
    exit 1
}

$rules | Remove-NetFirewallRule
Write-Host "Removed the '$RuleName' rule. Other firewall rules were left as they were."
exit 0
