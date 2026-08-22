import hashlib
import hmac
import json
import importlib.util
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.extensions import db
from app.models import (
    AttendanceAdjustment,
    AttendanceDevice,
    AttendanceDirection,
    AttendanceEvent,
    AttendanceMethod,
    AttendanceStatus,
    CardStatus,
    Shift,
    ShiftPublicationStatus,
    StaffCard,
    StaffProfile,
    User,
    WorkLocation,
)
from app.services.attendance import (
    _b64,
    _request_aad,
    _transport_key,
    _unb64,
    card_uid_hash,
    create_provisioning_package,
    decrypt_device_secret,
    encrypt_device_secret,
)

from .conftest import login


FIXED_NOW = datetime(2026, 8, 19, 6, 0, tzinfo=timezone.utc)
SECRET = "device-test-secret"


def setup_attendance(app):
    with app.app_context():
        office = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        profiles = db.session.scalars(db.select(StaffProfile).order_by(StaffProfile.id)).all()
        shift_type_id = db.session.scalar(db.select(Shift.shift_type_id).limit(1))
        if not shift_type_id:
            from app.models import ShiftType
            shift_type_id = db.session.scalar(db.select(ShiftType.id).where(ShiftType.code == "TEST_AM"))
        device = AttendanceDevice(
            device_code="CLOCK-OFFICE-01", name="辦公室打卡機", location_id=office.id,
            allowed_cidr="127.0.0.0/8", secret_encrypted=encrypt_device_secret(SECRET),
            is_active=True, enrolled_at=FIXED_NOW, created_by=admin.id,
        )
        db.session.add(device)
        for index, profile in enumerate(profiles):
            uid = f"CARD000{index + 1}"
            db.session.add(StaffCard(
                staff_id=profile.id, uid_hash=card_uid_hash(uid), uid_last4=uid[-4:],
                status=CardStatus.ACTIVE, registered_by=admin.id,
            ))
            db.session.add(Shift(
                shift_date=date(2026, 8, 19), shift_type_id=shift_type_id, staff_id=profile.id,
                publication_status=ShiftPublicationStatus.PUBLISHED, created_by=admin.id,
            ))
        db.session.commit()


def signed_post(client, path, payload, *, signature_secret=SECRET):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(FIXED_NOW.timestamp()))
    nonce = uuid4().hex
    canonical = "\n".join(["POST", path, timestamp, nonce, hashlib.sha256(body).hexdigest()]).encode()
    signature = hmac.new(signature_secret.encode(), canonical, hashlib.sha256).hexdigest()
    return client.post(path, data=body, content_type="application/json", headers={
        "X-Attendance-Device": "CLOCK-OFFICE-01",
        "X-Attendance-Timestamp": timestamp,
        "X-Attendance-Nonce": nonce,
        "X-Attendance-Signature": signature,
    })


def encrypted_post(client, path, payload, *, secret=SECRET):
    request_id = uuid4().hex
    timestamp = str(int(FIXED_NOW.timestamp()))
    nonce = b"0123456789ab"
    plain = json.dumps(payload, separators=(",", ":")).encode()
    envelope = {
        "device_id": "CLOCK-OFFICE-01", "request_id": request_id,
        "timestamp": timestamp, "key_version": 1, "nonce": _b64(nonce),
        "ciphertext": _b64(AESGCM(_transport_key(secret.encode(), "request")).encrypt(
            nonce, plain, _request_aad("POST", path, "CLOCK-OFFICE-01", request_id, timestamp)
        )),
    }
    response = client.post(path, json=envelope)
    response_envelope = response.json
    response_aad = "\n".join([
        str(response.status_code), path, "CLOCK-OFFICE-01", request_id, "1"
    ]).encode()
    result = json.loads(AESGCM(_transport_key(secret.encode(), "response")).decrypt(
        _unb64(response_envelope["nonce"]), _unb64(response_envelope["ciphertext"]), response_aad
    ))
    return response, result, json.dumps(envelope).encode()


def test_only_signed_registered_device_can_punch_and_duplicate_is_idempotent(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    setup_attendance(app)
    payload = {
        "event_id": str(uuid4()), "sequence": 1, "occurred_at": "2026-08-19T08:55:00+08:00",
        "method": "CARD", "card_uid": "CARD0001", "offline": False,
    }
    denied = signed_post(client, "/attendance-api/punch", payload, signature_secret="wrong")
    assert denied.status_code == 401
    accepted = signed_post(client, "/attendance-api/punch", payload)
    assert accepted.status_code == 200
    assert accepted.json["direction"] == "IN"
    assert accepted.json["status"] == "NORMAL"
    replayed_event = signed_post(client, "/attendance-api/punch", payload)
    assert replayed_event.status_code == 200
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(AttendanceEvent)) == 1


def test_late_account_punch_requires_reason_and_admin_can_review(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    setup_attendance(app)
    event_id = str(uuid4())
    response = signed_post(client, "/attendance-api/punch", {
        "event_id": event_id, "sequence": 1, "occurred_at": "2026-08-19T09:12:00+08:00",
        "method": "ACCOUNT", "username": "student-test", "password": "StudentTest!2026", "offline": False,
    })
    assert response.status_code == 200
    assert response.json["status"] == "LATE_REASON_REQUIRED"
    assert response.json["late_minutes"] == 12
    reason = signed_post(client, f"/attendance-api/events/{event_id}/reason", {
        "category": "交通延誤", "reason": "公車臨時改道", "claimed_arrival_at": None,
    })
    assert reason.status_code == 200
    with app.app_context():
        event = db.session.scalar(db.select(AttendanceEvent).where(AttendanceEvent.event_uuid == event_id))
        assert event.status == AttendanceStatus.LATE_PENDING_REVIEW
        event_db_id = event.id
    login(client)
    assert client.get("/admin/attendance").status_code == 200
    reviewed = client.post(f"/admin/attendance/events/{event_db_id}/review", data={"decision": "APPROVE", "review_note": "已確認"})
    assert reviewed.status_code == 302
    with app.app_context():
        assert db.session.get(AttendanceEvent, event_db_id).status == AttendanceStatus.REVIEWED


def test_first_punch_near_end_is_missing_clock_in_and_creates_adjustment_after_review(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    setup_attendance(app)
    event_id = str(uuid4())
    response = signed_post(client, "/attendance-api/punch", {
        "event_id": event_id, "sequence": 1, "occurred_at": "2026-08-19T13:02:00+08:00",
        "method": "ACCOUNT", "username": "student-two", "password": "StudentTwo!2026", "offline": False,
    })
    assert response.status_code == 200
    assert response.json["direction"] == "OUT"
    assert response.json["status"] == "MISSING_CLOCK_IN"
    reason = signed_post(client, f"/attendance-api/events/{event_id}/reason", {
        "category": "忘記刷卡", "reason": "到班時忘記刷學生證", "claimed_arrival_at": "2026-08-19T09:03:00+08:00",
    })
    assert reason.status_code == 200
    with app.app_context():
        event_db_id = db.session.scalar(db.select(AttendanceEvent.id).where(AttendanceEvent.event_uuid == event_id))
    login(client)
    client.post(f"/admin/attendance/events/{event_db_id}/review", data={"decision": "APPROVE", "review_note": "監視器確認"})
    with app.app_context():
        event = db.session.get(AttendanceEvent, event_db_id)
        adjustment = db.session.scalar(db.select(AttendanceAdjustment).where(AttendanceAdjustment.event_id == event_db_id))
        assert event.status == AttendanceStatus.REVIEWED
        assert adjustment.direction == AttendanceDirection.IN


def test_admin_can_register_card_and_student_can_view_attendance_page(client, app):
    login(client)
    with app.app_context():
        staff_id = db.session.scalar(db.select(StaffProfile.id).where(StaffProfile.student_number == "TEST001"))
    response = client.post("/admin/attendance/cards", data={"staff_id": staff_id, "card_uid": "ABCD123456", "return_to": "staff"})
    assert response.status_code == 302
    assert response.location.endswith(f"/admin/staff#staff-{staff_id}")
    roster = client.get("/admin/staff")
    assert b"3456" in roster.data
    client.post("/auth/logout")
    login(client, "student-test", "StudentTest!2026")
    page = client.get("/student/attendance")
    assert page.status_code == 200
    assert b"3456" in page.data


def test_encrypted_http_hides_payload_and_encrypts_response(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    app.config["ATTENDANCE_TRANSPORT_MODE"] = "ENCRYPTED_HTTP"
    setup_attendance(app)
    payload = {
        "event_id": str(uuid4()), "sequence": 1, "occurred_at": "2026-08-19T08:55:00+08:00",
        "method": "CARD", "card_uid": "CARD0001", "offline": False,
    }
    response, result, sent_body = encrypted_post(client, "/attendance-api/punch", payload)
    assert response.status_code == 200
    assert result["direction"] == "IN"
    assert b"CARD0001" not in sent_body
    assert b"student" not in response.data.lower()


def test_encrypted_http_rejects_tampering(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    app.config["ATTENDANCE_TRANSPORT_MODE"] = "ENCRYPTED_HTTP"
    setup_attendance(app)
    envelope = {
        "device_id": "CLOCK-OFFICE-01", "request_id": uuid4().hex,
        "timestamp": str(int(FIXED_NOW.timestamp())), "key_version": 1,
        "nonce": _b64(b"0123456789ab"), "ciphertext": _b64(b"tampered"),
    }
    response = client.post("/attendance-api/punch", json=envelope)
    assert response.status_code == 401
    assert response.json["error"]["code"] == "DECRYPTION_FAILED"


def test_attendance_can_be_disabled(client, app):
    app.config["ATTENDANCE_ENABLED"] = False
    response = client.post("/attendance-api/punch", json={})
    assert response.status_code == 503
    assert response.json["error"]["code"] == "ATTENDANCE_DISABLED"


def test_provisioning_package_sets_unique_device_secret(app):
    with app.app_context():
        office = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        device = AttendanceDevice(device_code="CLOCK-PACKAGE-01", name="Package", location=office, created_by=admin.id)
        db.session.add(device)
        package = create_provisioning_package(
            device, server_url="http://192.168.1.10:5000", passphrase="correct horse battery",
            transport_mode="ENCRYPTED_HTTP", activation_minutes=120,
        )
        outer = json.loads(package)
        assert outer["format"] == "dorm-attendance-provision-v1"
        assert b"192.168.1.10" not in package
        assert b"CLOCK-PACKAGE-01" not in package
        assert device.secret_encrypted and not device.enrolled_at
        assert device.enrollment_token_hash and device.enrollment_expires_at


def test_admin_creates_dynamic_device_and_downloads_encrypted_package(client, app):
    app.config["ATTENDANCE_TRANSPORT_MODE"] = "ENCRYPTED_HTTP"
    login(client)
    with app.app_context():
        location_id = db.session.scalar(db.select(WorkLocation.id).where(WorkLocation.code == "OFFICE"))
    response = client.post("/admin/attendance/devices", data={
        "device_code": "CLOCK-FUTURE-27", "name": "Future terminal",
        "location_id": location_id, "allowed_cidr": "10.20.0.0/16",
        "server_url": "http://10.20.0.5:5000", "package_password": "package password 2026",
        "activation_hours": "12",
    })
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].endswith("CLOCK-FUTURE-27.dormclock")
    assert b"CLOCK-FUTURE-27" not in response.data
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice).where(
            AttendanceDevice.device_code == "CLOCK-FUTURE-27"
        ))
        assert device.location_id == location_id
        assert device.allowed_cidr == "10.20.0.0/16"
        assert device.secret_encrypted


def test_server_package_is_compatible_with_terminal_importer(app, monkeypatch):
    terminal_path = Path(__file__).parents[1] / "attendance-terminal" / "attendance_terminal.py"
    spec = importlib.util.spec_from_file_location("attendance_terminal_test", terminal_path)
    terminal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terminal)
    monkeypatch.setattr(terminal, "save_config", lambda _data: None)
    activation_calls = []
    monkeypatch.setattr(terminal, "device_identity", lambda: {
        "installation_id": "d0441544-a797-4144-9a58-2941b5549d09",
        "computer_name": "CLOCK-PC", "mac_addresses": ["AA:BB:CC:DD:EE:FF"],
    })
    monkeypatch.setattr(terminal, "signed_json", lambda config, path, payload: activation_calls.append((path, payload)) or {"activated": True})
    with app.app_context():
        office = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        device = AttendanceDevice(device_code="CLOCK-INTEROP", name="Interop", location=office, created_by=admin.id)
        db.session.add(device)
        package = create_provisioning_package(
            device, server_url="http://10.0.0.5:5000", passphrase="interop password 2026",
            transport_mode="ENCRYPTED_HTTP", activation_minutes=60,
        )
        result = terminal.import_package_data(package.decode(), "interop password 2026")
        assert result["device_id"] == "CLOCK-INTEROP"
        assert result["transport_mode"] == "ENCRYPTED_HTTP"
        assert result["secret"].encode() == decrypt_device_secret(device)
        assert "activation_token" not in result
        assert activation_calls[0][0] == "/attendance-api/activate"


def test_encrypted_package_activation_is_one_time_and_binds_identity(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    app.config["ATTENDANCE_TRANSPORT_MODE"] = "ENCRYPTED_HTTP"
    setup_attendance(app)
    token = "one-time-activation-token"
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == "CLOCK-OFFICE-01"))
        device.enrolled_at = None
        device.enrollment_token_hash = hashlib.sha256(token.encode()).hexdigest()
        device.enrollment_expires_at = FIXED_NOW + timedelta(hours=2)
        db.session.commit()
    identity = {
        "installation_id": "22d2061b-4639-45c0-8f59-cbb3c165fa81",
        "computer_name": "OFFICE-PC-01", "mac_addresses": ["AABBCCDDEEFF"],
    }
    response, result, _ = encrypted_post(client, "/attendance-api/activate", {
        "activation_token": token, "_device": identity,
    })
    assert response.status_code == 200 and result["activated"] is True
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == "CLOCK-OFFICE-01"))
        assert device.enrolled_at is not None
        assert device.enrollment_token_hash is None
        assert device.computer_name == "OFFICE-PC-01"
        assert device.mac_addresses == ["AA:BB:CC:DD:EE:FF"]
    response, result, _ = encrypted_post(client, "/attendance-api/activate", {
        "activation_token": token,
        "_device": {**identity, "installation_id": "219e8e0c-9644-4e0c-b732-caad13388137"},
    })
    assert response.status_code == 401
    assert result["error"]["code"] == "ACTIVATION_USED"


def test_expired_activation_package_is_rejected(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    app.config["ATTENDANCE_TRANSPORT_MODE"] = "ENCRYPTED_HTTP"
    setup_attendance(app)
    token = "expired-activation-token"
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == "CLOCK-OFFICE-01"))
        device.enrolled_at = None
        device.enrollment_token_hash = hashlib.sha256(token.encode()).hexdigest()
        device.enrollment_expires_at = FIXED_NOW - timedelta(minutes=1)
        db.session.commit()
    response, result, _ = encrypted_post(client, "/attendance-api/activate", {
        "activation_token": token,
        "_device": {"installation_id": "343f3a2c-8b8c-490e-b41f-8fb80f216481", "computer_name": "LATE-PC", "mac_addresses": ["11:22:33:44:55:66"]},
    })
    assert response.status_code == 401
    assert result["error"]["code"] == "ACTIVATION_EXPIRED"


def test_mac_change_waits_for_admin_confirmation(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    app.config["ATTENDANCE_TRANSPORT_MODE"] = "ENCRYPTED_HTTP"
    setup_attendance(app)
    installation_id = "98805f27-e1d8-4e3a-92c0-91012022bb14"
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == "CLOCK-OFFICE-01"))
        device.installation_id = installation_id
        device.computer_name = "OFFICE-PC"
        device.mac_addresses_json = '["AA:BB:CC:DD:EE:FF"]'
        db.session.commit()
    response, _result, _ = encrypted_post(client, "/attendance-api/punch", {
        "event_id": str(uuid4()), "sequence": 1, "occurred_at": "2026-08-19T08:55:00+08:00",
        "method": "CARD", "card_uid": "CARD0001", "offline": False,
        "_device": {"installation_id": installation_id, "computer_name": "OFFICE-PC-RENAMED", "mac_addresses": ["00:11:22:33:44:55"]},
    })
    assert response.status_code == 200
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == "CLOCK-OFFICE-01"))
        device_id = device.id
        assert device.computer_name == "OFFICE-PC"
        assert device.pending_computer_name == "OFFICE-PC-RENAMED"
        assert device.pending_mac_addresses == ["00:11:22:33:44:55"]
    login(client)
    renamed = client.post(f"/admin/attendance/devices/{device_id}/update", data={"name": "辦公室入口打卡機"})
    assert renamed.status_code == 302
    settings = client.get("/admin/settings/attendance")
    assert "OFFICE-PC-RENAMED".encode() in settings.data
    confirmed = client.post(f"/admin/attendance/devices/{device_id}/confirm-identity")
    assert confirmed.status_code == 302
    with app.app_context():
        device = db.session.get(AttendanceDevice, device_id)
        assert device.name == "辦公室入口打卡機"
        assert device.computer_name == "OFFICE-PC-RENAMED"
        assert device.pending_mac_addresses_json is None


def test_attendance_review_note_and_shift_link_are_visible_to_admin_and_student(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    setup_attendance(app)
    event_id = str(uuid4())
    response = signed_post(client, "/attendance-api/punch", {
        "event_id": event_id, "sequence": 1, "occurred_at": "2026-08-19T09:12:00+08:00",
        "method": "ACCOUNT", "username": "student-test", "password": "StudentTest!2026", "offline": False,
    })
    assert response.status_code == 200
    signed_post(client, f"/attendance-api/events/{event_id}/reason", {
        "category": "交通延誤", "reason": "公車臨時改道", "claimed_arrival_at": None,
    })
    with app.app_context():
        event = db.session.scalar(db.select(AttendanceEvent).where(AttendanceEvent.event_uuid == event_id))
        event_db_id, shift_id = event.id, event.shift_id
    login(client)
    reviewed = client.post(f"/admin/attendance/events/{event_db_id}/review", data={
        "decision": "APPROVE", "direction": "IN", "shift_id": shift_id,
        "review_note": "已查核值班紀錄，核准。", "date": "2026-08-19",
    })
    assert reviewed.status_code == 302
    admin_page = client.get("/admin/attendance?date=2026-08-19")
    assert "已查核值班紀錄，核准。".encode() in admin_page.data
    assert b"/admin/schedule?date=2026-08-19" in admin_page.data
    client.post("/auth/logout")
    login(client, "student-test", "StudentTest!2026")
    student_page = client.get("/student/attendance")
    assert "已查核值班紀錄，核准。".encode() in student_page.data
    assert b"2026-08-19" in student_page.data
    assert b"date=2026-08-19#studentScheduleApp" in student_page.data


def test_admin_can_link_unmatched_punch_to_new_shift(client, app):
    setup_attendance(app)
    with app.app_context():
        device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == "CLOCK-OFFICE-01"))
        profile = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST001"))
        from app.models import ShiftType
        shift_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == "TEST_AM"))
        event = AttendanceEvent(
            event_uuid=str(uuid4()), device_id=device.id, staff_id=profile.id,
            method=AttendanceMethod.CARD, direction=AttendanceDirection.UNKNOWN,
            status=AttendanceStatus.UNMATCHED, occurred_at=FIXED_NOW,
            device_sequence=99,
        )
        db.session.add(event)
        db.session.commit()
        event_id, shift_type_id = event.id, shift_type.id
    login(client)
    response = client.post(f"/admin/attendance/events/{event_id}/review", data={
        "decision": "APPROVE", "direction": "IN", "create_missing_shift": "yes",
        "new_shift_date": "2026-08-20", "new_shift_type_id": shift_type_id,
        "review_note": "確認臨時值班並補建排班。", "date": "2026-08-19",
    })
    assert response.status_code == 302
    with app.app_context():
        event = db.session.get(AttendanceEvent, event_id)
        assert event.status == AttendanceStatus.REVIEWED
        assert event.direction == AttendanceDirection.IN
        assert event.shift is not None
        assert event.shift.shift_date == date(2026, 8, 20)


def test_attendance_status_appears_on_admin_and_student_calendars(client, app, monkeypatch):
    import app.services.attendance as attendance
    monkeypatch.setattr(attendance, "utc_now", lambda: FIXED_NOW)
    setup_attendance(app)
    response = signed_post(client, "/attendance-api/punch", {
        "event_id": str(uuid4()), "sequence": 1, "occurred_at": "2026-08-19T08:55:00+08:00",
        "method": "CARD", "card_uid": "CARD0001", "offline": False,
    })
    assert response.status_code == 200
    login(client)
    admin_events = client.get("/admin/api/shifts?start=2026-08-01&end=2026-09-01").get_json()
    assert any(
        annotation["kind"] == "ATTENDANCE" and "上班已打卡" in annotation["label"]
        for event in admin_events for annotation in event["extendedProps"]["workflowAnnotations"]
    )
    client.post("/auth/logout")
    login(client, "student-test", "StudentTest!2026")
    student_events = client.get("/student/api/shifts?start=2026-08-01&end=2026-09-01").get_json()
    assert any(
        annotation["kind"] == "ATTENDANCE" and annotation["url"] == "/student/attendance"
        for event in student_events for annotation in event["extendedProps"]["workflowAnnotations"]
    )


def test_attendance_settings_are_separate_and_cards_also_appear_on_staff_page(client):
    login(client)
    attendance_page = client.get("/admin/attendance")
    settings_page = client.get("/admin/settings/attendance")
    staff_page = client.get("/admin/staff")
    assert attendance_page.status_code == settings_page.status_code == staff_page.status_code == 200
    assert "授權打卡裝置".encode() not in attendance_page.data
    assert "授權打卡裝置".encode() in settings_page.data
    assert "學生證登錄".encode() not in settings_page.data
    assert "刷學生證或輸入卡片 UID".encode() in staff_page.data
    assert "申請與人力".encode() in staff_page.data
    assert b"Requests &amp; Staffing" in staff_page.data
