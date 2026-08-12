param(
    [ValidateSet("Enable", "Disable")][string]$Mode,
    [ValidateRange(1, 1440)][int]$IntervalMinutes = 5,
    [string]$TaskName = "DormStaffSystem-PortableWatchdog",
    [string]$LauncherTaskName = "DormStaffSystem-PortableLauncher"
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Administrator permission is required."
}

if ($Mode -eq "Disable") {
    foreach ($name in @($TaskName, $LauncherTaskName)) {
        if ($null -ne (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
            Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
        }
    }
    exit 0
}

$watchdog = Join-Path $PSScriptRoot "watchdog.ps1"
if (-not (Test-Path -LiteralPath $watchdog)) { throw "watchdog.ps1 not found." }
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $watchdog
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $PSScriptRoot
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($startupTrigger, $repeatTrigger) -Settings $settings -Principal $taskPrincipal -Force | Out-Null

$launcher = Join-Path $PSScriptRoot "DormStaffLauncher.exe"
if (-not (Test-Path -LiteralPath $launcher)) { throw "DormStaffLauncher.exe not found." }
$interactiveUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$launcherAction = New-ScheduledTaskAction -Execute $launcher -WorkingDirectory $PSScriptRoot
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $interactiveUser
$launcherPrincipal = New-ScheduledTaskPrincipal -UserId $interactiveUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $LauncherTaskName -Action $launcherAction -Trigger $logonTrigger `
    -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew) `
    -Principal $launcherPrincipal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
