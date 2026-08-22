param(
    [ValidateSet("Enable", "Disable")][string]$Mode,
    [ValidateRange(1, 1440)][int]$IntervalMinutes = 5,
    [string]$TaskName = "DormStaffSystem-PortableWatchdog",
    [string]$LauncherTaskName = "DormStaffSystem-PortableLauncher",
    [string]$InteractiveUser
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

$launcher = Join-Path $PSScriptRoot "DormStaffLauncher.exe"
if (-not (Test-Path -LiteralPath $launcher)) { throw "DormStaffLauncher.exe not found." }
if ([string]::IsNullOrWhiteSpace($InteractiveUser)) { throw "Original interactive user SID is required." }
$action = New-ScheduledTaskAction -Execute $launcher -Argument "--auto-start" -WorkingDirectory $PSScriptRoot
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $InteractiveUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($startupTrigger, $repeatTrigger) -Settings $settings -Principal $taskPrincipal -Force | Out-Null

try {
    $launcherAction = New-ScheduledTaskAction -Execute $launcher -Argument "--auto-start" -WorkingDirectory $PSScriptRoot
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $InteractiveUser
    $launcherPrincipal = New-ScheduledTaskPrincipal -UserId $InteractiveUser -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $LauncherTaskName -Action $launcherAction -Trigger $logonTrigger `
        -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0)) `
        -Principal $launcherPrincipal -Force | Out-Null
    Start-ScheduledTask -TaskName $LauncherTaskName
} catch {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $LauncherTaskName -Confirm:$false -ErrorAction SilentlyContinue
    throw
}
