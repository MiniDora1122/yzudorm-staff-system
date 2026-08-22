param(
    [Parameter(Mandatory=$true)][ValidateSet("Enable", "Disable")][string]$Mode,
    [Parameter(Mandatory=$true)][string]$InteractiveUser,
    [string]$TaskName = "DormAttendanceTerminal-AutoStart"
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Administrator permission is required."
}

if ($null -ne (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
if ($Mode -eq "Disable") { exit 0 }

$terminal = Join-Path $PSScriptRoot "DormAttendanceTerminal.exe"
if (-not (Test-Path -LiteralPath $terminal)) { throw "DormAttendanceTerminal.exe not found." }
if ([string]::IsNullOrWhiteSpace($InteractiveUser)) { throw "Interactive user SID is required." }

$action = New-ScheduledTaskAction -Execute $terminal -Argument "--auto-start" -WorkingDirectory $PSScriptRoot
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $InteractiveUser
$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $InteractiveUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logonTrigger, $repeatTrigger) `
    -Settings $settings -Principal $taskPrincipal -Force | Out-Null
