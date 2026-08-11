from app.extensions import db
from app.models import User

from .conftest import login


def test_login_success_redirects_admin(client):
    response = login(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")

    dashboard = client.get("/admin/")
    assert dashboard.status_code == 200
    assert "管理員儀表板".encode() in dashboard.data


def test_login_failure_returns_401(client):
    response = login(client, password="wrong-password")
    assert response.status_code == 401
    assert "帳號或密碼錯誤".encode() in response.data


def test_logout_clears_session(client):
    login(client)
    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")

    blocked = client.get("/admin/", follow_redirects=False)
    assert blocked.status_code == 302
    assert "/auth/login" in blocked.headers["Location"]


def test_protected_page_requires_login(client):
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_student_cannot_access_admin_route(client):
    login(client, "student-test", "StudentTest!2026")
    dashboard = client.get("/student/")
    assert dashboard.status_code == 200
    assert "測試學生".encode() in dashboard.data
    assert b"studentCalendar" in dashboard.data

    response = client.get("/admin/")
    assert response.status_code == 403


def test_password_is_not_stored_as_plaintext(app):
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "student-test"))
        assert user.password_hash != "StudentTest!2026"
        assert user.password_hash.startswith("$argon2")
        assert user.check_password("StudentTest!2026")


def test_change_password(client, app):
    login(client, "student-test", "StudentTest!2026")
    response = client.post(
        "/auth/change-password",
        data={
            "current_password": "StudentTest!2026",
            "new_password": "ChangedPass!2026",
            "confirm_password": "ChangedPass!2026",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "student-test"))
        assert user.check_password("ChangedPass!2026")


def test_eight_character_password_is_allowed_but_seven_is_rejected(client, app):
    login(client, "student-test", "StudentTest!2026")
    rejected = client.post(
        "/auth/change-password",
        data={
            "current_password": "StudentTest!2026",
            "new_password": "Abcd123",
            "confirm_password": "Abcd123",
        },
    )
    assert rejected.status_code == 200
    assert "密碼長度需為 8 至 128 個字元".encode() in rejected.data

    accepted = client.post(
        "/auth/change-password",
        data={
            "current_password": "StudentTest!2026",
            "new_password": "Abcd123!",
            "confirm_password": "Abcd123!",
        },
        follow_redirects=False,
    )
    assert accepted.status_code == 302
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "student-test"))
        assert user.check_password("Abcd123!")
