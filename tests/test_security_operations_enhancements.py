from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
import sqlite3
import zipfile

from cryptography.fernet import Fernet
import pytest

from app import create_app

from deployment.create_portable_backup import verify_backup
from app.extensions import db
from app.models import (
    AttendanceReconciliation,
    AuditLog,
    MinimumWageRate,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    ShiftType,
    StaffAvailability,
    StaffProfile,
    User,
)
from app.services.payroll import minimum_wage_on
from app.services.scheduling import SchedulingConflict, create_shift, shift_hours


def login(client, username="admin-test", password="AdminTest!2026"):
    return client.post(
        "/auth/login", data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_login_regenerates_server_side_session_and_rate_limits(client, app):
    with client.session_transaction() as session:
        session["pre_login_marker"] = "present"
    before = client.get_cookie("session").value
    response = login(client)
    assert response.status_code == 302
    after = client.get_cookie("session").value
    assert after != before

    client.post("/auth/logout")
    app.config.update(LOGIN_RATE_ACCOUNT_LIMIT=2, LOGIN_RATE_IP_LIMIT=20)
    for _ in range(2):
        response = client.post(
            "/auth/login", data={"username": "admin-test", "password": "wrong-password"}
        )
        assert response.status_code == 401
    blocked = login(client)
    assert blocked.status_code == 429


def test_security_headers_are_present_without_incorrect_http_hsts(client):
    response = client.get("/auth/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "no-store" in response.headers["Cache-Control"]
    assert "Strict-Transport-Security" not in response.headers


def test_non_test_app_rejects_missing_or_fixed_secret():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"TESTING": False, "SECRET_KEY": "dev-only-change-me"})


def test_configured_hours_drive_weekly_and_payroll_aggregation(client, app):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        staff = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST001"))
        shift_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == "TEST_AM"))
        shift_type.default_hours = Decimal("3.50")
        shift = Shift(
            shift_date=date(2026, 8, 4), shift_type=shift_type, staff=staff,
            created_by=admin.id, status=ShiftStatus.SCHEDULED,
            publication_status=ShiftPublicationStatus.PUBLISHED,
        )
        db.session.add(shift)
        db.session.flush()
        db.session.add(AttendanceReconciliation(
            shift=shift, calculated_hours=Decimal("3.75"), payable_hours=Decimal("3.25"),
            note="核對打卡", reviewed_by=admin.id,
        ))
        db.session.commit()
        assert shift_hours(shift_type) == 3.5

    login(client)
    report = client.get("/admin/api/payroll?month=2026-08").get_json()
    row = next(item for item in report["rows"] if item["student_number"] == "TEST001")
    assert row["hours"] == 3.25


def test_student_availability_warns_but_admin_can_override(app):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        staff = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST001"))
        shift_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == "TEST_AM"))
        db.session.add(StaffAvailability(
            staff_id=staff.id, availability_date=date(2026, 9, 8),
            start_time=time(9), end_time=time(13), is_available=False, note="上課",
        ))
        db.session.commit()
        try:
            create_shift(
                shift_date=date(2026, 9, 8), shift_type=shift_type, staff=staff,
                actor_id=admin.id,
            )
        except SchedulingConflict as exc:
            assert exc.code == "AVAILABILITY_CONFIRM_REQUIRED"
        else:
            raise AssertionError("Availability conflict was not raised")
        shift = create_shift(
            shift_date=date(2026, 9, 8), shift_type=shift_type, staff=staff,
            actor_id=admin.id, allow_availability_conflict=True,
        )
        assert shift.id is not None


def test_effective_dated_minimum_wage_and_audit_timezone_filter(client, app):
    with app.app_context():
        db.session.add_all([
            MinimumWageRate(effective_date=date(2026, 1, 1), hourly_wage=Decimal("196")),
            MinimumWageRate(effective_date=date(2027, 1, 1), hourly_wage=Decimal("205")),
        ])
        db.session.add(AuditLog(
            action="TZ_FILTER_TEST", entity_type="Test", entity_id=1,
            safe_summary="台北 8 月 23 日凌晨",
            created_at=datetime(2026, 8, 22, 16, 30, tzinfo=timezone.utc),
        ))
        db.session.commit()
        assert minimum_wage_on(date(2026, 12, 31)) == Decimal("196")
        assert minimum_wage_on(date(2027, 1, 1)) == Decimal("205")

    login(client)
    included = client.get("/admin/audit-logs?date_from=2026-08-23&date_to=2026-08-23")
    excluded = client.get("/admin/audit-logs?date_from=2026-08-22&date_to=2026-08-22")
    assert "台北 8 月 23 日凌晨".encode() in included.data
    assert "台北 8 月 23 日凌晨".encode() not in excluded.data


def test_backup_verification_requires_every_document_and_decrypts_it(tmp_path):
    database = tmp_path / "snapshot.db"
    plaintext = b"private-document-test"
    key = Fernet.generate_key()
    encrypted = Fernet(key).encrypt(plaintext)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE staff_documents (storage_key TEXT, sha256 TEXT)")
        connection.execute(
            "INSERT INTO staff_documents VALUES (?, ?)",
            ("staff/one.enc", hashlib.sha256(plaintext).hexdigest()),
        )
    db_data = database.read_bytes()

    def make_archive(path, include_document):
        files = {
            "instance/dorm_staff.db": db_data,
            "instance/private_keys/document-fernet.key": key,
        }
        if include_document:
            files["instance/private_documents/staff/one.enc"] = encrypted
        manifest = {
            "format": "dorm-staff-portable-backup-v1",
            "file_count": len(files),
            "files": {
                name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in files.items()
            },
        }
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            archive.writestr("PORTABLE_BACKUP_MANIFEST.json", json.dumps(manifest))

    valid = tmp_path / "valid.zip"
    make_archive(valid, True)
    assert verify_backup(valid)["verified_document_count"] == 1

    missing = tmp_path / "missing.zip"
    make_archive(missing, False)
    with pytest.raises(RuntimeError, match="missing or cannot be decrypted"):
        verify_backup(missing)
