from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_ROOT = PROJECT_ROOT / "portable-windows-launcher"


def test_launcher_watchdog_uses_native_task_scheduler_and_health_check():
    configure = (LAUNCHER_ROOT / "configure-watchdog-task.ps1").read_text(encoding="utf-8")
    watchdog = (LAUNCHER_ROOT / "watchdog.ps1").read_text(encoding="utf-8")
    stop_server = (LAUNCHER_ROOT / "stop-server.ps1").read_text(encoding="utf-8")
    launcher = (LAUNCHER_ROOT / "DormStaffLauncher.cs").read_text(encoding="utf-8")

    assert "Register-ScheduledTask" in configure
    assert "Unregister-ScheduledTask" in configure
    assert "New-ScheduledTaskTrigger -AtStartup" in configure
    assert "New-ScheduledTaskTrigger -AtLogOn" in configure
    assert '"DormStaffSystem-PortableLauncher"' in configure
    assert "DormStaffLauncher.exe" in configure
    assert "RepetitionInterval" in configure
    assert "/healthz" in watchdog
    assert '"service"\\s*:\\s*"dorm-staff-system"' in watchdog
    assert '"--trusted-proxy=127.0.0.1"' in watchdog
    assert '"--trusted-proxy-count=1"' in watchdog
    assert "--trusted-proxy=127.0.0.1" in launcher
    assert "--trusted-proxy-count=1" in launcher
    assert "x-forwarded-for x-forwarded-proto x-forwarded-host x-forwarded-port" in launcher
    assert "Port $port is occupied" in watchdog
    assert '".venv\\server.pid"' in watchdog
    assert "WatchdogIntervalMinutes" in launcher
    assert "AutoStartEnabled" in launcher
    assert "Auto-start: On" in launcher
    assert "ConfigureWatchdog(true)" in launcher
    assert "ConfigureWatchdog(false)" in launcher
    assert "GetRunningServerProcess()" in launcher
    assert "ImportWatchdogLog()" in launcher
    assert "statusTimer.Interval = 2000" in launcher
    assert "AppIsHealthy()" in launcher
    assert "啟動中／無回應" in launcher
    assert "bool healthy = AppIsHealthy();" in launcher
    assert "else if (AppIsHealthy() || ConfiguredPortIsOccupied())" in launcher
    assert "ConfiguredPortIsOccupied()" in launcher
    assert "Port 已占用／系統無回應" in launcher
    assert "Get-NetTCPConnection" in stop_server
    assert "portable Python; refusing to stop it" in stop_server
    assert "Stop-Process" in stop_server
    assert "maintenance.lock" in watchdog
    assert "Test-ActiveMarker" in watchdog
    assert "InteractiveUser" in configure
    assert "WindowsIdentity.GetCurrent().User.Value" in launcher
    assert "watchdog-status.ps1" in launcher
    assert '${Prefix}_PATH_OK=' in (LAUNCHER_ROOT / "watchdog-status.ps1").read_text(encoding="utf-8")
    assert "InstanceMutexName" in launcher
    assert "AcquireMaintenance" in launcher
    assert "RunMaintenanceBackground" in launcher
    assert "ExportSupportBundle" in launcher
    assert '"/healthz"' in launcher


def test_git_update_closes_launcher_before_replacing_it():
    updater = (LAUNCHER_ROOT / "self-update.ps1").read_text(encoding="utf-8")
    watchdog = (LAUNCHER_ROOT / "watchdog.ps1").read_text(encoding="utf-8")
    launcher = (LAUNCHER_ROOT / "DormStaffLauncher.cs").read_text(encoding="utf-8")

    assert "PrepareGitUpdate()" in launcher
    assert "StartExternalUpdaterAndClose" in launcher
    assert "DormStaffSelfUpdate-" in launcher
    assert "--update-result=" in launcher
    assert "Wait-Process -Id $ParentProcessId" in updater
    assert 'Invoke-Checked $git @("merge", "--ff-only"' in updater
    assert 'Invoke-Checked $python @("-m", "pip", "install"' in updater
    assert '"flask", "--app", "wsgi.py", "db", "upgrade"' in updater
    assert "Start-Process -FilePath $launcher" in updater
    assert "launcher-before-update.exe" in updater
    assert "update-in-progress" in watchdog
    assert "Restore-PreviousVersion" in updater
    assert "OldCommit" in updater
    assert "rolledback" in updater
    assert "update-state.ini" in updater
    assert "RecoveryOnly" in updater
    assert "PromptInterruptedUpdateRecovery" in launcher
    assert "update-state.ini" in watchdog
    assert "LauncherDirectoryBase64" in launcher
    assert "LauncherDirectoryBase64" in updater
    assert "Already up to date." in launcher
    assert "oldCommit.Equals(targetCommit" in launcher
    assert 'maintenanceOperation != "UPDATE_RECOVERY_REQUIRED"' in launcher
    assert '$previousErrorActionPreference = $ErrorActionPreference' in updater
    assert '$ErrorActionPreference = "Continue"' in updater
    assert '$exitCode = $LASTEXITCODE' in updater
    assert 'if ($exitCode -ne 0)' in updater
