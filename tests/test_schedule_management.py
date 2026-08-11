from io import BytesIO

from app.extensions import db
from app.models import AuditLog, Shift, ShiftType, WorkLocation

from .conftest import login


def test_admin_can_edit_location_and_shift_type(client, app):
    login(client)
    with app.app_context():
        location = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        shift_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == "TEST_AM"))
        location_id = location.id
        shift_type_id = shift_type.id

    response = client.put(
        f"/admin/api/locations/{location_id}",
        json={"code": "OFFICE_NEW", "name": "新辦公室", "name_en": "New Office", "color": "#123456"},
    )
    assert response.status_code == 200
    assert response.json["code"] == "OFFICE_NEW"

    response = client.put(
        f"/admin/api/shift-types/{shift_type_id}",
        json={
            "location_id": location_id,
            "code": "TEST_AM_NEW",
            "name": "更新上午班",
            "name_en": "Updated Morning Shift",
            "start_time": "08:30",
            "end_time": "12:30",
            "default_hours": "4",
        },
    )
    assert response.status_code == 200
    with app.app_context():
        location = db.session.get(WorkLocation, location_id)
        shift_type = db.session.get(ShiftType, shift_type_id)
        assert location.name == "新辦公室"
        assert location.color == "#123456"
        assert shift_type.name == "更新上午班"
        assert shift_type.start_time.strftime("%H:%M") == "08:30"


def test_student_cannot_edit_locations_or_shift_types(client, app):
    login(client, "student-test", "StudentTest!2026")
    assert client.put("/admin/api/locations/1", json={}).status_code == 403
    assert client.put("/admin/api/shift-types/1", json={}).status_code == 403


def test_bulk_import_is_atomic_and_records_audit(client, app):
    login(client)
    valid_csv = "日期,學號,班別代碼\n2026-08-20,TEST001,TEST_AM\n2026-08-21,TEST002,TEST_PM\n"
    response = client.post(
        "/admin/shifts/import",
        data={"shift_file": (BytesIO(valid_csv.encode("utf-8")), "shifts.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "已成功匯入 2 筆排班".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Shift)) == 2
        audit = db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "SHIFTS_BULK_IMPORTED")
        )
        assert audit is not None

    invalid_csv = "日期,學號,班別代碼\n2026-08-22,TEST001,TEST_AM\n2026-08-23,UNKNOWN,TEST_PM\n"
    response = client.post(
        "/admin/shifts/import",
        data={"shift_file": (BytesIO(invalid_csv.encode("utf-8")), "invalid.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "批量匯入失敗，未寫入任何資料".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Shift)) == 2


def test_bulk_import_location_overlap_needs_explicit_checkbox(client, app):
    login(client)
    csv_data = "日期,學號,班別代碼\n2026-08-24,TEST001,TEST_AM\n2026-08-24,TEST002,TEST_AM_ALT\n"
    response = client.post(
        "/admin/shifts/import",
        data={"shift_file": (BytesIO(csv_data.encode("utf-8")), "overlap.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "需要管理員再次確認".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Shift)) == 0

    response = client.post(
        "/admin/shifts/import",
        data={
            "allow_location_overlap": "yes",
            "shift_file": (BytesIO(csv_data.encode("utf-8")), "overlap.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "已成功匯入 2 筆排班".encode() in response.data


def test_dashboard_contains_today_and_tomorrow_sections(client):
    login(client)
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "今天排班".encode() in response.data
    assert "明天排班".encode() in response.data
    assert b"Today shifts" in response.data
    assert b"/admin/requests#leaveReview" in response.data
    assert b"/admin/requests#swapReview" in response.data


def test_shift_type_cannot_exceed_eight_hours(client, app):
    login(client)
    with app.app_context():
        location_id = db.session.scalar(db.select(WorkLocation.id).where(WorkLocation.code == "OFFICE"))
    response = client.post(
        "/admin/api/shift-types",
        json={
            "location_id": location_id,
            "code": "TOO_LONG",
            "name": "超長班",
            "name_en": "Too long",
            "start_time": "08:00",
            "end_time": "17:00",
            "default_hours": "9",
        },
    )
    assert response.status_code == 400
    assert "8" in response.json["error"]["message"]


def test_student_request_chooser_and_wage_disclaimer_are_visible(client):
    login(client, "student-test", "StudentTest!2026")
    requests_page = client.get("/student/requests")
    assert b'id="requestTypeChooser"' in requests_page.data
    assert b'id="leaveRequestPanel"' in requests_page.data
    assert b'id="swapRequestPanel"' in requests_page.data
    assert requests_page.data.count(b"request-form-panel") == 2

    dashboard = client.get("/student/")
    assert "尚未扣除勞保、健保等費用，僅供試算參考。".encode("utf-8") in dashboard.data
    assert b'href="#studentScheduleApp"' in dashboard.data
    assert b'href="#upcomingShifts"' in dashboard.data
