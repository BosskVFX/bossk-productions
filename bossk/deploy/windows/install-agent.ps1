# Windows PC: idle + session reporter as a scheduled task that starts at logon, plus the Claude Code hooks.
# Run in an elevated PowerShell from the bossk\ folder:
#   .\deploy\windows\install-agent.ps1 -Core http://truenas:8750 -Token <BOSSK_TOKEN>
param(
  [Parameter(Mandatory=$true)][string]$Core,
  [Parameter(Mandatory=$true)][string]$Token,
  [string]$Machine = "pc"
)
$root = Resolve-Path "$PSScriptRoot\..\.."
$py = (Get-Command python).Source
& $py "$root\agents\hooks\bossk_hook.py" install --core $Core --token $Token --machine $Machine
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$root\agents\idle_reporter.py`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "BOSSK Agent" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "BOSSK Agent"
Write-Host "BOSSK agent installed and started on $Machine -> $Core"
