from app.extensions import db
from app.models import AuditLog, Role, User

from .conftest import login


ADMIN_PAYLOAD = {
    "display_name": "第二位管理員",
    "username": "admin.two",
    "temporary_password": "Temporary!2026",
    "confirm_temporary_password": "Temporary!2026",
}


def logout(client):
    client.post("/auth/logout")


def test_admin_can_create_another_admin_with_hashed_temporary_password(client, app):
    login(client)
    response = client.post("/admin/admin-accounts", data=ADMIN_PAYLOAD, follow_redirects=True)
    assert response.status_code == 200
    assert "已建立管理員帳號 admin.two".encode() in response.data

    with app.app_context():
        administrator = db.session.scalar(db.select(User).where(User.username == "admin.two"))
        assert administrator is not None
        assert administrator.display_name == "第二位管理員"
        assert administrator.role == Role.ADMIN
        assert administrator.must_change_password is True
        assert administrator.password_hash != ADMIN_PAYLOAD["temporary_password"]
        assert administrator.password_hash.startswith("$argon2")
        assert administrator.check_password(ADMIN_PAYLOAD["temporary_password"])
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "ADMIN_ACCOUNT_CREATED")
        )
        assert audit is not None
        assert ADMIN_PAYLOAD["temporary_password"] not in audit.safe_summary


def test_new_admin_must_change_password_then_can_manage_admin_accounts(client, app):
    login(client)
    client.post("/admin/admin-accounts", data=ADMIN_PAYLOAD)
    logout(client)

    assert login(client, "admin.two", "Temporary!2026").status_code == 302
    blocked = client.get("/admin/admin-accounts", follow_redirects=False)
    assert blocked.status_code == 302
    assert blocked.headers["Location"].endswith("/auth/change-password")

    changed = client.post(
        "/auth/change-password",
        data={
            "current_password": "Temporary!2026",
            "new_password": "PermanentAdmin!2026",
            "confirm_password": "PermanentAdmin!2026",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 302
    assert changed.headers["Location"].endswith("/admin/")
    page = client.get("/admin/admin-accounts")
    assert page.status_code == 200
    assert "管理員帳號".encode() in page.data
    assert b"Admin accounts" in page.data


def test_student_cannot_view_or_create_admin_accounts(client, app):
    login(client, "student-test", "StudentTest!2026")
    assert client.get("/admin/admin-accounts").status_code == 403
    assert client.post("/admin/admin-accounts", data=ADMIN_PAYLOAD).status_code == 403
    with app.app_context():
        assert db.session.scalar(db.select(User).where(User.username == "admin.two")) is None


def test_duplicate_admin_username_does_not_create_partial_account(client, app):
    login(client)
    response = client.post(
        "/admin/admin-accounts",
        data=dict(ADMIN_PAYLOAD, username="student-test"),
        follow_redirects=True,
    )
    assert "此登入帳號已存在".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(User).where(User.display_name == "第二位管理員")) is None


def test_admin_can_reset_another_admin_but_not_self(client, app):
    login(client)
    client.post("/admin/admin-accounts", data=ADMIN_PAYLOAD)
    with app.app_context():
        second = db.session.scalar(db.select(User).where(User.username == "admin.two"))
        current = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        second_id = second.id
        current_id = current.id

    response = client.post(
        f"/admin/admin-accounts/{second_id}/reset-password",
        data={
            "temporary_password": "ResetAdmin!2026",
            "confirm_temporary_password": "ResetAdmin!2026",
        },
        follow_redirects=True,
    )
    assert "已重設管理員 admin.two".encode() in response.data
    with app.app_context():
        second = db.session.get(User, second_id)
        assert second.must_change_password is True
        assert second.check_password("ResetAdmin!2026")
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "ADMIN_PASSWORD_RESET")
        )
        assert audit is not None
        assert "ResetAdmin!2026" not in audit.safe_summary

    response = client.post(
        f"/admin/admin-accounts/{current_id}/reset-password",
        data={
            "temporary_password": "SelfReset!2026",
            "confirm_temporary_password": "SelfReset!2026",
        },
        follow_redirects=True,
    )
    assert "不可在此重設自己的密碼".encode() in response.data


def test_primary_pages_include_smaller_english_labels(client):
    login(client)
    admin_page = client.get("/admin/admin-accounts")
    dashboard = client.get("/admin/")
    assert b'data-en="Admin accounts"' in admin_page.data
    assert b'data-en="Admin dashboard"' in dashboard.data
    assert b"admin-navbar" in dashboard.data
    assert b"white-space: nowrap" in client.get("/static/css/app.css").data
    assert b"nav-link bg-transparent border-0" in dashboard.data
    assert dashboard.data.count(b'data-bilingual-processed="true"') >= 5
    assert dashboard.data.count(b"nav-dropdown-indicator") == 4
    assert b"navbar-notification-count { margin-left: .25rem;" in client.get("/static/css/app.css").data
    assert b'<span class="nav-primary-label"><i class="bi bi-bell-fill' in dashboard.data
    assert b'element.classList.contains("nav-primary-label")' in client.get("/static/js/bilingual.js").data
    assert b"font-size: .7em" in client.get("/static/css/app.css").data
    assert b"js/bilingual.js" in dashboard.data
    bilingual_script = client.get("/static/js/bilingual.js")
    assert bilingual_script.status_code == 200
    assert "薪資與法定費率設定".encode() in bilingual_script.data

    logout(client)
    login(client, "student-test", "StudentTest!2026")
    student_page = client.get("/student/")
    assert b'data-en="MY SCHEDULE"' in student_page.data
    assert b'data-en="My upcoming shifts"' in student_page.data
