from app.extensions import db
from app.models import AuditLog

from .conftest import login


def test_failed_login_records_masked_account_and_request_source(client, app):
    response = client.post(
        "/auth/login",
        data={"username": "unknown-account", "password": "NeverLogThisPassword"},
        headers={"User-Agent": "Audit-Test-Browser/1.0"},
        environ_base={"REMOTE_ADDR": "198.51.100.24"},
    )
    assert response.status_code == 401
    with app.app_context():
        item = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "LOGIN_FAILED")
        )
        assert item.actor_user_id is None
        assert item.ip_address == "198.51.100.24"
        assert item.user_agent == "Audit-Test-Browser/1.0"
        assert item.http_method == "POST"
        assert item.route == "auth.login"
        assert "u******t" in item.safe_summary
        assert "NeverLogThisPassword" not in item.safe_summary


def test_login_logout_and_password_change_are_audited(client, app):
    login(client, "student-test", "StudentTest!2026")
    client.post(
        "/auth/change-password",
        data={
            "current_password": "StudentTest!2026",
            "new_password": "ChangedPass!2026",
            "confirm_password": "ChangedPass!2026",
        },
    )
    client.post("/auth/logout")

    with app.app_context():
        events = {
            item.action: item
            for item in db.session.scalars(
                db.select(AuditLog).where(
                    AuditLog.action.in_({"LOGIN_SUCCEEDED", "PASSWORD_CHANGED", "LOGOUT"})
                )
            )
        }
        assert set(events) == {"LOGIN_SUCCEEDED", "PASSWORD_CHANGED", "LOGOUT"}
        assert all(item.actor_user_id is not None for item in events.values())
        assert all(item.ip_address == "127.0.0.1" for item in events.values())
        assert events["PASSWORD_CHANGED"].route == "auth.change_password"


def test_audit_page_is_admin_only_and_supports_filters(client):
    client.post(
        "/auth/login",
        data={"username": "admin-test", "password": "wrong-password"},
        environ_base={"REMOTE_ADDR": "203.0.113.9"},
    )
    login(client)
    page = client.get("/admin/audit-logs?action=LOGIN_FAILED&ip_address=203.0.113.9")
    assert page.status_code == 200
    assert b"LOGIN_FAILED" in page.data
    assert b"203.0.113.9" in page.data
    assert "安全事件與操作稽核".encode("utf-8") in page.data

    client.post("/auth/logout")
    login(client, "student-test", "StudentTest!2026")
    assert client.get("/admin/audit-logs").status_code == 403

