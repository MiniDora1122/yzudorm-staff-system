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
