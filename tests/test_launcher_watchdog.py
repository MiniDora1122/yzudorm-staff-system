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
    assert "/auth/login" in watchdog
    assert "Dormitory Student Worker System" in watchdog
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
