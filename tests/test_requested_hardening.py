from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import urllib.error

from app.extensions import db
from app.models import (
    AttendanceAdjustment, AttendanceDevice, AttendanceDirection, AttendanceEvent,
    AttendanceMethod, AttendanceReviewHistory, AttendanceStatus, Notification, Shift,
    ShiftImportBatch, ShiftPublicationStatus, ShiftStatus, ShiftType, StaffProfile,
    User, WorkLocation,
)
from app.services.attendance import (
    attendance_annotations, calculated_hours_for_shift, create_provisioning_package,
    decrypt_device_secret, encrypt_device_secret, review_event, submit_reason,
)
from .conftest import login
from .test_attendance import FIXED_NOW, setup_attendance, signed_post


def _attendance_event(app, status=AttendanceStatus.LATE_PENDING_REVIEW, direction=AttendanceDirection.IN):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        staff = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST001"))
        location = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        shift_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == "TEST_AM"))
        shift = Shift(shift_date=date(2026, 8, 19), shift_type=shift_type, staff=staff,
                      created_by=admin.id, publication_status=ShiftPublicationStatus.PUBLISHED)
        device = AttendanceDevice(device_code="HARDENING-CLOCK", name="Clock", location=location,
                                  secret_encrypted=encrypt_device_secret("active-secret"), enrolled_at=datetime.now(timezone.utc),
                                  created_by=admin.id)
        db.session.add_all([shift, device]); db.session.flush()
        event = AttendanceEvent(event_uuid="11111111-1111-1111-1111-111111111111", device=device,
                                staff=staff, shift=shift, method=AttendanceMethod.ACCOUNT,
                                direction=direction, status=status,
                                occurred_at=datetime(2026, 8, 19, 1, 12, tzinfo=timezone.utc), device_sequence=1)
        db.session.add(event); db.session.commit()
        return event.id, shift.id, admin.id


def test_returned_attendance_can_be_resubmitted_with_history(app):
    event_id, _shift_id, admin_id = _attendance_event(app)
    with app.app_context():
        event = db.session.get(AttendanceEvent, event_id)
        event.reason_text = "原事由"
        review_event(event, decision="REJECT", note="請補充細節", actor_user_id=admin_id)
        assert event.status == AttendanceStatus.RETURNED
        submit_reason(event, category="交通延誤", reason="已補上公車班次")
        assert event.status == AttendanceStatus.LATE_PENDING_REVIEW
        assert [row.decision for row in event.review_history] == ["REJECT", "RESUBMITTED"]


def test_replacement_package_keeps_active_secret_until_activation(app):
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        location = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        device = AttendanceDevice(device_code="ROTATE-CLOCK", name="Rotate", location=location,
                                  secret_encrypted=encrypt_device_secret("old-secret"),
                                  enrolled_at=datetime.now(timezone.utc), created_by=admin.id)
        create_provisioning_package(device, server_url="http://10.0.0.1", passphrase="safe package password",
                                    transport_mode="ENCRYPTED_HTTP", activation_minutes=60)
        assert decrypt_device_secret(device) == b"old-secret"
        assert decrypt_device_secret(device, pending=True) != b"old-secret"


def test_adjusted_clock_in_is_used_for_hours_and_calendar(app):
    event_id, shift_id, admin_id = _attendance_event(app, AttendanceStatus.REVIEWED, AttendanceDirection.OUT)
    with app.app_context():
        event = db.session.get(AttendanceEvent, event_id)
        event.occurred_at = datetime(2026, 8, 19, 5, 0, tzinfo=timezone.utc)
        db.session.add(AttendanceAdjustment(event=event, direction=AttendanceDirection.IN,
                                            adjusted_at=datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc),
                                            reason="核准補登", created_by=admin_id))
        db.session.commit()
        shift = db.session.get(Shift, shift_id)
        assert calculated_hours_for_shift(shift) == 4
        assert any("補登上班" in item["label"] for item in attendance_annotations([shift])[shift.id])


def test_csv_preview_confirm_and_undo(client, app):
    login(client)
    content = "日期,學號,班別代碼\n2026-10-06,TEST001,TEST_AM\n"
    from io import BytesIO
    preview = client.post("/admin/shifts/import", data={
        "mode": "preview", "publication_status": "DRAFT",
        "shift_file": (BytesIO(content.encode()), "test.csv"),
    }, content_type="multipart/form-data")
    assert preview.status_code == 200
    assert "確認匯入".encode() in preview.data
    confirmed = client.post("/admin/shifts/import", data={
        "mode": "confirm", "publication_status": "DRAFT", "csv_text": content,
        "original_filename": "test.csv",
    })
    assert confirmed.status_code == 302
    with app.app_context():
        batch = db.session.scalar(db.select(ShiftImportBatch))
        batch_id = batch.id
        assert batch.row_count == 1
    undone = client.post(f"/admin/shifts/import-batches/{batch_id}/undo")
    assert undone.status_code == 302
    with app.app_context():
        shift = db.session.scalar(db.select(Shift).where(Shift.import_batch_id == batch_id))
        assert shift.status == ShiftStatus.CANCELLED


def test_admin_can_query_attendance_by_exact_time_range(client, app):
    _attendance_event(app)
    login(client)
    page = client.get(
        "/admin/attendance?mode=range&start=2026-08-19T09:00&end=2026-08-19T10:00"
    )
    assert page.status_code == 200
    assert "區間打卡".encode() in page.data
    assert b"2026-08-19 09:12:00" in page.data


def test_dashboard_health_is_after_primary_content(client):
    login(client)
    page = client.get("/admin/")
    assert page.status_code == 200
    assert page.data.index("系統健康".encode()) > page.data.index("功能入口".encode())


def test_device_offline_alert_has_its_own_schedule(client, app, monkeypatch):
    import app.services.health as health

    setup_attendance(app)
    login(client)
    with app.app_context():
        device_id = db.session.scalar(db.select(AttendanceDevice.id))
    response = client.post(f"/admin/attendance/devices/{device_id}/offline-alert", data={
        "offline_alert_enabled": "1",
        "offline_threshold_minutes": "10",
        "offline_weekdays": ["0", "1", "2", "3", "4"],
        "offline_monitor_start": "08:00",
        "offline_monitor_end": "18:00",
    })
    assert response.status_code == 302

    monday_0900 = datetime(2026, 8, 24, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(health, "utc_now", lambda: monday_0900)
    with app.app_context():
        device = db.session.get(AttendanceDevice, device_id)
        device.last_seen_at = monday_0900 - timedelta(minutes=11)
        db.session.commit()
        assert health.offline_attendance_devices() == [device]
        device.offline_monitor_weekdays = "1"
        db.session.commit()
        assert health.offline_attendance_devices() == []

        device.offline_monitor_weekdays = "0"
        device.offline_monitor_start = datetime.strptime("22:00", "%H:%M").time()
        device.offline_monitor_end = datetime.strptime("06:00", "%H:%M").time()
        db.session.commit()
        tuesday_0100 = datetime(2026, 8, 24, 17, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(health, "utc_now", lambda: tuesday_0100)
        assert health.offline_attendance_devices() == [device]


def test_offline_notification_uses_taipei_time_on_every_page(client, app, monkeypatch):
    import app.services.health as health

    setup_attendance(app)
    last_seen = datetime(2026, 8, 23, 15, 59, 58, tzinfo=timezone.utc)
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice))
        device.last_seen_at = last_seen
        device.offline_threshold_minutes = 5
        db.session.commit()
    monkeypatch.setattr(health, "utc_now", lambda: datetime(2026, 8, 23, 16, 20, tzinfo=timezone.utc))
    login(client)
    assert client.get("/admin/schedule").status_code == 200
    with app.app_context():
        notification = db.session.scalar(db.select(Notification).where(Notification.category == "ATTENDANCE_DEVICE"))
        assert "2026-08-23 23:59:58" in notification.message_zh


def test_archived_device_is_rejected_and_can_be_restored(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    setup_attendance(app)
    login(client)
    with app.app_context():
        device_id = db.session.scalar(db.select(AttendanceDevice.id))
    assert client.post(f"/admin/attendance/devices/{device_id}/archive").status_code == 302
    rejected = signed_post(client, "/attendance-api/health", {})
    assert rejected.status_code == 401
    assert rejected.json["error"]["code"] == "DEVICE_NOT_ALLOWED"
    assert client.post(f"/admin/attendance/devices/{device_id}/restore").status_code == 302
    with app.app_context():
        device = db.session.get(AttendanceDevice, device_id)
        assert device.is_active is True
        assert device.secret_encrypted is None
        assert device.enrolled_at is None


def test_deleted_device_disappears_but_attendance_history_remains(client, app):
    setup_attendance(app)
    login(client)
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice))
        shift = db.session.scalar(db.select(Shift).limit(1))
        db.session.add(AttendanceEvent(
            event_uuid="22222222-2222-2222-2222-222222222222", device=device,
            staff_id=shift.staff_id, shift=shift, method=AttendanceMethod.CARD,
            direction=AttendanceDirection.IN, status=AttendanceStatus.NORMAL,
            occurred_at=datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc), device_sequence=99,
        ))
        db.session.commit()
        device_id, old_code = device.id, device.device_code
        event_count = db.session.scalar(db.select(db.func.count()).select_from(AttendanceEvent))
    assert client.post(f"/admin/attendance/devices/{device_id}/delete").status_code == 302
    with app.app_context():
        device = db.session.get(AttendanceDevice, device_id)
        assert device.deleted_at is not None
        assert device.is_active is False
        assert device.device_code != old_code
        assert db.session.scalar(db.select(db.func.count()).select_from(AttendanceEvent)) == event_count
    settings = client.get("/admin/settings/attendance")
    assert f'<div class="small font-monospace mt-1">{old_code}</div>'.encode() not in settings.data


def test_encrypted_terminal_recognizes_plain_device_revocation(monkeypatch):
    terminal_path = Path(__file__).parents[1] / "attendance-terminal" / "attendance_terminal.py"
    spec = importlib.util.spec_from_file_location("attendance_terminal_revocation_test", terminal_path)
    terminal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terminal)
    response = json.dumps({
        "error": {"code": "DEVICE_NOT_ALLOWED", "message": "裝置已封存或刪除"}
    }).encode()

    def denied(_request, timeout=10):
        raise urllib.error.HTTPError("http://server", 401, "Unauthorized", {}, BytesIO(response))

    monkeypatch.setattr(terminal.urllib.request, "urlopen", denied)
    try:
        terminal.encrypted_json(
            {"server": "http://server", "device_id": "OLD", "secret": "old-secret"},
            "/attendance-api/health", {},
        )
    except terminal.ApiError as exc:
        assert exc.status == 401
        assert exc.code == "DEVICE_NOT_ALLOWED"
    else:
        raise AssertionError("Revocation response was treated as an offline connection")
