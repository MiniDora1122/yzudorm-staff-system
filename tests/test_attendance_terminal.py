import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace


def load_terminal_module():
    path = Path(__file__).parents[1] / "attendance-terminal" / "attendance_terminal.py"
    spec = importlib.util.spec_from_file_location("attendance_terminal_control_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_kiosk_page_exposes_card_scanner_and_authenticated_shutdown():
    terminal = load_terminal_module()
    kiosk = SimpleNamespace(token="browser-token", control_token="manager-token")
    server = terminal.ThreadingHTTPServer(("127.0.0.1", 0), terminal.handler_for(kiosk))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    try:
        assert b"Student card scanner" in urllib.request.urlopen(root + "/", timeout=2).read()
        assert b'"running":true' in urllib.request.urlopen(root + "/health", timeout=2).read()
        denied = urllib.request.Request(root + "/__shutdown", method="POST")
        try:
            urllib.request.urlopen(denied, timeout=2)
            assert False, "shutdown without control token should fail"
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        allowed = urllib.request.Request(
            root + "/__shutdown", method="POST", headers={"X-Control-Token": "manager-token"}
        )
        assert b'"stopping":true' in urllib.request.urlopen(allowed, timeout=2).read()
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        server.server_close()


def test_terminal_autostart_is_visible_single_instance_and_checked_every_five_minutes():
    root = Path(__file__).parents[1] / "attendance-terminal"
    launcher = (root / "DormAttendanceTerminal.cs").read_text(encoding="utf-8")
    task = (root / "configure-autostart.ps1").read_text(encoding="utf-8")

    assert '"--auto-start"' in launcher
    assert "AutoStartScript=True" in launcher
    assert '"Local\\\\DormAttendanceTerminal"' in launcher
    assert "Shown += delegate" in launcher
    assert "StartTerminal();" in launcher
    assert "啟用自啟動" in launcher
    assert "停用自啟動" in launcher
    assert "-AtLogOn" in task
    assert "-RepetitionInterval (New-TimeSpan -Minutes 5)" in task
    assert "-LogonType Interactive" in task
    assert "-MultipleInstances IgnoreNew" in task
    assert 'Unregister-ScheduledTask -TaskName $TaskName' in task
    assert "-AtStartup" not in task


def test_terminal_git_update_uses_saved_https_repository_url():
    root = Path(__file__).parents[1] / "attendance-terminal"
    launcher = (root / "DormAttendanceTerminal.cs").read_text(encoding="utf-8")
    updater = (root / "terminal-self-update.ps1").read_text(encoding="utf-8")

    assert "Git 更新來源 / Update source" in launcher
    assert "RepositoryUrl=" in launcher
    assert "Uri.UriSchemeHttps" in launcher
    assert 'uri.Host.Equals("github.com"' in launcher
    assert "UserInfo" in launcher
    assert 'Read-Setting "RepositoryUrl"' in updater
    assert 'https://codeload.github.com/' in updater
    assert "Invoke-WebRequest" in updater
    assert "Expand-Archive" in updater
    assert 'Downloaded update is missing $name' in updater
    assert "Previous terminal files restored" in updater
    assert 'Join-Path $deviceData "update-in-progress"' in updater


def test_terminal_runtime_has_no_parent_project_or_portable_runtime_dependency():
    root = Path(__file__).parents[1] / "attendance-terminal"
    launcher = (root / "DormAttendanceTerminal.cs").read_text(encoding="utf-8")
    updater = (root / "terminal-self-update.ps1").read_text(encoding="utf-8")
    defaults = (root / "terminal-defaults.ini").read_text(encoding="utf-8")

    assert "portable-windows-launcher" not in launcher
    assert "DormStaffLauncher" not in launcher
    assert "ProjectRoot" not in launcher
    assert "portable-windows-launcher" not in updater
    assert "git.exe" not in updater
    assert "TerminalRoot" in updater
    assert "RepositoryUrl=https://github.com/" in defaults
    assert "GitBranch=main" in defaults
