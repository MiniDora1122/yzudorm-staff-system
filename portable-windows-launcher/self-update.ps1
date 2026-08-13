param(
    [string]$LauncherDirectory,
    [string]$LauncherDirectoryBase64,
    [Parameter(Mandatory = $true)][int]$ParentProcessId,
    [switch]$RecoveryOnly
)

$ErrorActionPreference = "Stop"
$LauncherDirectory = if (-not [string]::IsNullOrWhiteSpace($LauncherDirectoryBase64)) {
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($LauncherDirectoryBase64))
} elseif (-not [string]::IsNullOrWhiteSpace($LauncherDirectory)) { $LauncherDirectory } else {
    throw "Launcher directory was not provided."
}
$launcherRoot = [IO.Path]::GetFullPath($LauncherDirectory)
$configPath = Join-Path $launcherRoot "launcher.ini"
$runtime = Join-Path $launcherRoot ".venv"
$logDirectory = Join-Path $runtime "logs"
$maintenanceMarker = Join-Path $runtime "update-in-progress"
$statePath = Join-Path $runtime "update-state.ini"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
$logPath = Join-Path $logDirectory "self-update.log"
if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 2MB) {
    Move-Item -LiteralPath $logPath -Destination (Join-Path $logDirectory "self-update.previous.log") -Force
}
$launcher = Join-Path $launcherRoot "DormStaffLauncher.exe"
$launcherBackup = Join-Path $runtime "launcher-before-update.exe"

function Write-UpdateLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) -Encoding UTF8
}

function Read-KeyValues([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match '^\s*([^#;][^=]*)=(.*)$') { $values[$matches[1].Trim()] = $matches[2].Trim() }
    }
    return $values
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory) {
    Push-Location $WorkingDirectory
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        # Native tools such as Alembic write normal INFO messages to stderr.
        # Treat the process exit code, not the output stream, as authoritative.
        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
        foreach ($line in $output) { Write-UpdateLog ([string]$line) }
        if ($exitCode -ne 0) { throw "$FilePath failed with exit code $exitCode" }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }
}

function Restore-PreviousVersion(
    [string]$Git,
    [string]$Python,
    [string]$ProjectRoot,
    [hashtable]$State
) {
    Write-UpdateLog "Update failed; restoring the previous code and portable data."
    Invoke-Checked $Git @("reset", "--hard", $State.OldCommit) $ProjectRoot
    Invoke-Checked $Python @("-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--timeout", "60", "--retries", "3", "-r", (Join-Path $ProjectRoot "requirements.txt")) $ProjectRoot
    $backupPath = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($State.BackupPathBase64))
    $restoreHelper = Join-Path $launcherRoot "migrate_portable_data.py"
    if (-not (Test-Path -LiteralPath $backupPath) -or -not (Test-Path -LiteralPath $restoreHelper)) {
        throw "The pre-update backup or restore helper is missing."
    }
    $env:PYTHONPATH = $ProjectRoot
    Invoke-Checked $Python @($restoreHelper, "restore", "--project-root", $ProjectRoot, "--source", $backupPath) $ProjectRoot
    Invoke-Checked $Python @("-c", "from app import create_app; create_app(); print('Rollback import OK')") $ProjectRoot
    if (Test-Path -LiteralPath $launcherBackup) {
        Copy-Item -LiteralPath $launcherBackup -Destination $launcher -Force
    }
    Write-UpdateLog "Previous version and portable data restored successfully."
}

$result = "failed"
$newLauncherProcess = $null
$enteredApply = $false
try {
    Set-Content -LiteralPath $maintenanceMarker -Value @(
        "Operation=GIT_UPDATE",
        "Pid=$PID",
        "StartedUtc=$([DateTime]::UtcNow.ToString('O'))"
    ) -Encoding UTF8
    if (-not $RecoveryOnly) {
        Copy-Item -LiteralPath $launcher -Destination $launcherBackup -Force
    } elseif (-not (Test-Path -LiteralPath $launcherBackup)) {
        throw "The previous Launcher backup is missing; automatic recovery cannot continue."
    }
    Wait-Process -Id $ParentProcessId -Timeout 30 -ErrorAction SilentlyContinue
    if ($null -ne (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) {
        throw "Launcher did not close within 30 seconds."
    }

    if (-not (Test-Path -LiteralPath $configPath) -or -not (Test-Path -LiteralPath $statePath)) {
        throw "Launcher configuration or update state is missing."
    }
    $config = Read-KeyValues $configPath
    $state = Read-KeyValues $statePath
    if ([string]::IsNullOrWhiteSpace($state.OldCommit) -or [string]::IsNullOrWhiteSpace($state.TargetCommit) -or [string]::IsNullOrWhiteSpace($state.BackupPathBase64)) {
        throw "Update rollback state is incomplete."
    }
    $projectSetting = if ([string]::IsNullOrWhiteSpace($config.ProjectPath)) { ".." } else { $config.ProjectPath }
    $projectRoot = if ([IO.Path]::IsPathRooted($projectSetting)) {
        [IO.Path]::GetFullPath($projectSetting)
    } else { [IO.Path]::GetFullPath((Join-Path $launcherRoot $projectSetting)) }
    $git = Join-Path $runtime "git\cmd\git.exe"
    $python = Join-Path $runtime "python\python.exe"
    if (-not (Test-Path -LiteralPath $git) -or -not (Test-Path -LiteralPath $python)) { throw "Portable Git or Python is missing." }

    $enteredApply = $true
    if ($RecoveryOnly) {
        Restore-PreviousVersion $git $python $projectRoot $state
        $result = "rolledback"
    } else { try {
        Write-UpdateLog "Launcher closed; applying fetched Git update $($state.TargetCommit)."
        Invoke-Checked $git @("merge", "--ff-only", $state.TargetCommit) $projectRoot
        Invoke-Checked $python @("-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--timeout", "60", "--retries", "3", "-r", (Join-Path $projectRoot "requirements.txt")) $projectRoot
        $env:PYTHONPATH = $projectRoot
        Invoke-Checked $python @("-m", "flask", "--app", "wsgi.py", "db", "upgrade") $projectRoot
        Invoke-Checked $python @("-c", "from app import create_app; create_app(); print('Application import OK')") $projectRoot
        if (-not (Test-Path -LiteralPath $launcher)) { throw "Updated Launcher executable is missing." }
        $newLauncherProcess = Start-Process -FilePath $launcher -ArgumentList "--update-result=success" -WorkingDirectory $launcherRoot -PassThru
        Start-Sleep -Seconds 5
        if ($newLauncherProcess.HasExited) { throw "Updated Launcher exited during startup validation." }
        $result = "success"
        Write-UpdateLog "Git safe update completed and the updated Launcher stayed running."
    } catch {
        Write-UpdateLog ("UPDATE ERROR: " + $_.Exception.Message)
        if ($null -ne $newLauncherProcess -and -not $newLauncherProcess.HasExited) {
            Stop-Process -Id $newLauncherProcess.Id -Force -ErrorAction SilentlyContinue
        }
        try {
            Restore-PreviousVersion $git $python $projectRoot $state
            $result = "rolledback"
        } catch {
            Write-UpdateLog ("ROLLBACK ERROR: " + $_.Exception.Message)
            if (Test-Path -LiteralPath $launcherBackup) {
                Copy-Item -LiteralPath $launcherBackup -Destination $launcher -Force -ErrorAction SilentlyContinue
            }
            $result = "failed"
        }
    } }
} catch {
    Write-UpdateLog ("ERROR: " + $_.Exception.Message)
} finally {
    if ($result -ne "failed" -or (-not $enteredApply -and -not $RecoveryOnly)) {
        Remove-Item -LiteralPath $maintenanceMarker -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $launcherBackup -Force -ErrorAction SilentlyContinue
    }
    if ($result -ne "success" -and (Test-Path -LiteralPath $launcher)) {
        Start-Process -FilePath $launcher -ArgumentList "--update-result=$result" -WorkingDirectory $launcherRoot
    }
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
