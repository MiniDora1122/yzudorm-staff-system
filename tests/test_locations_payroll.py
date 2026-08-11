from app.extensions import db
from app.models import ShiftType, StaffProfile, WorkLocation

from .conftest import login
from .test_scheduling import create_api_shift, ids


def test_admin_can_add_location_and_shift_type(client, app):
    login(client)
    location_response = client.post(
        "/admin/api/locations",
        json={"code": "LIBRARY", "name": "閱覽室", "name_en": "Reading Room", "color": "#7c3aed"},
    )
    assert location_response.status_code == 201
    location_id = location_response.json["id"]

    shift_type_response = client.post(
        "/admin/api/shift-types",
        json={
            "code": "LIBRARY_PM",
            "name": "閱覽室晚班",
            "name_en": "Reading Room Evening Shift",
            "location_id": location_id,
            "start_time": "18:00",
            "end_time": "21:00",
            "default_hours": "3",
        },
    )
    assert shift_type_response.status_code == 201

    with app.app_context():
        profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == "TEST001")
        )
        shift_type = db.session.scalar(
            db.select(ShiftType).where(ShiftType.code == "LIBRARY_PM")
        )
        assert shift_type.work_location.name == "閱覽室"
        profile_id = profile.id
        shift_type_id = shift_type.id

    created = create_api_shift(
        client,
        shift_date="2026-08-22",
        staff_id=profile_id,
        shift_type_id=shift_type_id,
    )
    assert created.status_code == 201
    assert created.json["extendedProps"]["location"] == "LIBRARY"
    assert created.json["extendedProps"]["locationLabel"] == "閱覽室"

    filtered = client.get(
        "/admin/api/shifts?start=2026-08-01&end=2026-09-01&location=LIBRARY"
    )
    assert filtered.status_code == 200
    assert len(filtered.json) == 1


def test_duplicate_location_is_rejected(client):
    login(client)
    response = client.post(
        "/admin/api/locations",
        json={"code": "OFFICE", "name": "另一個辦公室", "name_en": "Another Office", "color": "#123456"},
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "DUPLICATE_LOCATION"


def test_payroll_report_calculates_employer_costs(client, app):
    values = ids(app)
    login(client)
    created = create_api_shift(
        client,
        shift_date="2026-08-10",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    )
    assert created.status_code == 201

    response = client.get("/admin/api/payroll?month=2026-08")
    assert response.status_code == 200
    student = next(row for row in response.json["rows"] if row["student_number"] == "TEST001")
    assert student["hours"] == 4.0
    assert student["hourly_wage"] == 196.0
    assert student["gross_wage"] == 784
    assert student["labor_insurance"] == 2375
    assert student["employment_insurance"] == 207
    assert student["occupational_accident"] == 44
    assert student["health_insurance"] == 1428
    assert student["labor_pension"] == 270
    assert student["employer_benefits"] == 4324
    assert student["employer_total"] == 5108


def test_student_payroll_response_hides_insurance_costs(client, app):
    values = ids(app)
    login(client)
    assert create_api_shift(
        client,
        shift_date="2026-08-10",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    ).status_code == 201

    client.post("/auth/logout")
    login(client, "student-test", "StudentTest!2026")
    response = client.get("/student/api/monthly-hours?month=2026-08")
    assert response.status_code == 200
    assert response.json == {
        "month": "2026-08",
        "total_hours": 4.0,
        "gross_wage": 784,
        "hourly_wage": 196.0,
    }
    for hidden_field in (
        "labor_insurance",
        "employment_insurance",
        "occupational_accident",
        "health_insurance",
        "labor_pension",
        "employer_total",
    ):
        assert hidden_field not in response.json

    assert client.get("/admin/payroll").status_code == 403
    assert client.get("/admin/api/payroll?month=2026-08").status_code == 403


def test_student_cannot_create_location(client):
    login(client, "student-test", "StudentTest!2026")
    response = client.post(
        "/admin/api/locations",
        json={"code": "SECRET", "name": "不可新增", "name_en": "Forbidden", "color": "#123456"},
    )
    assert response.status_code == 403
    with client.application.app_context():
        assert db.session.scalar(
            db.select(WorkLocation).where(WorkLocation.code == "SECRET")
        ) is None
