param(
    [string]$TaskName = "DormStaffSystem-PortableWatchdog",
    [string]$LauncherTaskName = "DormStaffSystem-PortableLauncher"
)

$ErrorActionPreference = "SilentlyContinue"

function Write-TaskStatus([string]$Prefix, [string]$Name, [string]$ExpectedPath) {
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Write-Output "${Prefix}_EXISTS=0"
        Write-Output "${Prefix}_ENABLED=0"
        Write-Output "${Prefix}_PATH_OK=0"
        return
    }
    Write-Output "${Prefix}_EXISTS=1"
    Write-Output ("${Prefix}_ENABLED=" + $(if ($task.State -eq "Disabled") { "0" } else { "1" }))
    $pathMatches = $false
    foreach ($action in $task.Actions) {
        $arguments = [string]$action.Arguments
        if ($action.Execute.Equals($ExpectedPath, [StringComparison]::OrdinalIgnoreCase) -or
            $arguments.IndexOf($ExpectedPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $pathMatches = $true
        }
    }
    Write-Output ("${Prefix}_PATH_OK=" + $(if ($pathMatches) { "1" } else { "0" }))
    $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -ne $info) {
        Write-Output "${Prefix}_LAST_RESULT=$($info.LastTaskResult)"
        Write-Output "${Prefix}_LAST_RUN=$($info.LastRunTime.ToString('O'))"
        Write-Output "${Prefix}_NEXT_RUN=$($info.NextRunTime.ToString('O'))"
    }
}

Write-TaskStatus "WATCHDOG" $TaskName (Join-Path $PSScriptRoot "watchdog.ps1")
Write-TaskStatus "LAUNCHER" $LauncherTaskName (Join-Path $PSScriptRoot "DormStaffLauncher.exe")
