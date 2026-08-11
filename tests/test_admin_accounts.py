from app.extensions import db
from app.models import AuditLog, Role, StaffProfile, User

from .conftest import login


CREATE_PAYLOAD = {
    "username": "new.student",
    "student_number": "NEW003",
    "name": "新進工讀生",
    "nationality": "台灣",
    "email": "new.student@example.edu.tw",
    "phone": "0912-345-678",
    "temporary_password": "Temporary!2026",
    "confirm_temporary_password": "Temporary!2026",
}


def logout(client):
    client.post("/auth/logout")


def test_admin_creates_student_account_with_hashed_temporary_password(client, app):
    login(client)
    response = client.post("/admin/staff", data=CREATE_PAYLOAD, follow_redirects=True)
    assert response.status_code == 200
    assert "已建立 新進工讀生 的工讀生帳號".encode() in response.data

    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "new.student"))
        assert user is not None
        assert user.role == Role.STUDENT
        assert user.must_change_password is True
        assert user.password_hash != CREATE_PAYLOAD["temporary_password"]
        assert user.password_hash.startswith("$argon2")
        assert user.check_password(CREATE_PAYLOAD["temporary_password"])
        assert user.staff_profile.student_number == "NEW003"
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "STUDENT_ACCOUNT_CREATED")
        )
        assert audit is not None
        assert CREATE_PAYLOAD["temporary_password"] not in audit.safe_summary


def test_new_student_must_change_temporary_password_before_using_system(client, app):
    login(client)
    client.post("/admin/staff", data=CREATE_PAYLOAD)
    logout(client)

    response = login(client, "new.student", "Temporary!2026")
    assert response.status_code == 302
    blocked = client.get("/student/", follow_redirects=False)
    assert blocked.status_code == 302
    assert blocked.headers["Location"].endswith("/auth/change-password")

    changed = client.post(
        "/auth/change-password",
        data={
            "current_password": "Temporary!2026",
            "new_password": "MyPermanent!2026",
            "confirm_password": "MyPermanent!2026",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 302
    assert changed.headers["Location"].endswith("/student/")
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "new.student"))
        assert user.must_change_password is False
        assert user.check_password("MyPermanent!2026")


def test_duplicate_username_or_student_number_does_not_leave_partial_account(client, app):
    login(client)
    duplicate_username = dict(CREATE_PAYLOAD, username="student-test", student_number="NEW004")
    response = client.post("/admin/staff", data=duplicate_username, follow_redirects=True)
    assert "此登入帳號已存在".encode() in response.data

    duplicate_number = dict(CREATE_PAYLOAD, username="another-student", student_number="TEST001")
    response = client.post("/admin/staff", data=duplicate_number, follow_redirects=True)
    assert "此學號已由其他工讀生使用".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(User).where(User.username == "another-student")) is None
        assert db.session.scalar(db.select(User).where(User.username == "new.student")) is None


def test_admin_resets_student_password_and_old_password_immediately_fails(client, app):
    login(client)
    with app.app_context():
        profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == "TEST001")
        )
        profile_id = profile.id
    response = client.post(
        f"/admin/staff/{profile_id}/reset-password",
        data={
            "temporary_password": "ResetPass!2026",
            "confirm_temporary_password": "ResetPass!2026",
        },
        follow_redirects=True,
    )
    assert "已重設 測試學生 的臨時密碼".encode() in response.data
    with app.app_context():
        user = db.session.scalar(db.select(User).where(User.username == "student-test"))
        assert user.must_change_password is True
        assert not user.check_password("StudentTest!2026")
        assert user.check_password("ResetPass!2026")
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "STUDENT_PASSWORD_RESET")
        )
        assert audit is not None
        assert "ResetPass!2026" not in audit.safe_summary

    logout(client)
    assert login(client, "student-test", "StudentTest!2026").status_code == 401
    assert login(client, "student-test", "ResetPass!2026").status_code == 302


def test_students_cannot_create_accounts_or_reset_passwords(client, app):
    login(client, "student-test", "StudentTest!2026")
    assert client.post("/admin/staff", data=CREATE_PAYLOAD).status_code == 403
    with app.app_context():
        other_profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == "TEST002")
        )
        other_id = other_profile.id
    response = client.post(
        f"/admin/staff/{other_id}/reset-password",
        data={
            "temporary_password": "HackedPass!2026",
            "confirm_temporary_password": "HackedPass!2026",
        },
    )
    assert response.status_code == 403


def test_password_confirmation_must_match(client, app):
    login(client)
    payload = dict(CREATE_PAYLOAD, confirm_temporary_password="Different!2026")
    response = client.post("/admin/staff", data=payload, follow_redirects=True)
    assert "兩次輸入的臨時密碼不一致".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(User).where(User.username == "new.student")) is None
