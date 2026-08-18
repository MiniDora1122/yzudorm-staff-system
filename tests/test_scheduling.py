from datetime import date, time
from decimal import Decimal

from app.extensions import db
from app.models import (
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    ShiftType,
    StaffProfile,
    User,
    WorkLocation,
)

from .conftest import login


def ids(app):
    with app.app_context():
        return {
            "student_one": db.session.scalar(
                db.select(StaffProfile).where(StaffProfile.student_number == "TEST001")
            ).id,
            "student_two": db.session.scalar(
                db.select(StaffProfile).where(StaffProfile.student_number == "TEST002")
            ).id,
            **{
                item.code: item.id
                for item in db.session.scalars(db.select(ShiftType)).all()
            },
        }


def create_api_shift(client, *, shift_date, staff_id, shift_type_id, allow_location_overlap=False):
    return client.post(
        "/admin/api/shifts",
        json={
            "shift_date": shift_date,
            "staff_id": staff_id,
            "shift_type_id": shift_type_id,
            "allow_location_overlap": allow_location_overlap,
        },
    )


def test_legal_shift_can_be_created(client, app):
    values = ids(app)
    login(client)
    response = create_api_shift(
        client,
        shift_date="2026-08-10",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    )
    assert response.status_code == 201
    assert response.json["extendedProps"]["staffName"] == "測試學生"

    hours = client.get(
        f"/admin/api/monthly-hours?month=2026-08&staff_id={values['student_one']}"
    )
    assert hours.status_code == 200
    assert hours.json["rows"][0]["office_hours"] == 4.0
    assert hours.json["total_hours"] == 4.0

    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Shift)) == 1


def test_same_location_and_time_requires_confirmation_then_is_allowed(client, app):
    values = ids(app)
    login(client)
    assert create_api_shift(
        client,
        shift_date="2026-08-11",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    ).status_code == 201

    duplicate = create_api_shift(
        client,
        shift_date="2026-08-11",
        staff_id=values["student_two"],
        shift_type_id=values["TEST_AM_ALT"],
    )
    assert duplicate.status_code == 409
    assert duplicate.json["error"]["code"] == "LOCATION_CONFIRM_REQUIRED"

    confirmed = create_api_shift(
        client,
        shift_date="2026-08-11",
        staff_id=values["student_two"],
        shift_type_id=values["TEST_AM_ALT"],
        allow_location_overlap=True,
    )
    assert confirmed.status_code == 201
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Shift)) == 2


def test_same_staff_time_overlap_is_rejected(client, app):
    values = ids(app)
    login(client)
    assert create_api_shift(
        client,
        shift_date="2026-08-12",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    ).status_code == 201

    overlap = create_api_shift(
        client,
        shift_date="2026-08-12",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_OVERLAP"],
    )
    assert overlap.status_code == 409
    assert overlap.json["error"]["code"] == "STAFF_TIME_OVERLAP"


def test_admin_can_update_list_and_delete_shift(client, app):
    values = ids(app)
    login(client)
    page = client.get("/admin/schedule")
    assert page.status_code == 200
    assert "FullCalendar 排班".encode() in page.data

    created = create_api_shift(
        client,
        shift_date="2026-08-13",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    )
    shift_id = created.json["id"]

    updated = client.put(
        f"/admin/api/shifts/{shift_id}",
        json={
            "shift_date": "2026-08-14",
            "staff_id": values["student_two"],
            "shift_type_id": values["TEST_PM"],
        },
    )
    assert updated.status_code == 200
    assert updated.json["extendedProps"]["staffName"] == "第二位學生"

    events = client.get("/admin/api/shifts?start=2026-08-01&end=2026-09-01")
    assert events.status_code == 200
    assert [event["id"] for event in events.json] == [shift_id]

    deleted = client.delete(f"/admin/api/shifts/{shift_id}")
    assert deleted.status_code == 200
    assert deleted.json["cancelled"] == 1
    with app.app_context():
        assert db.session.get(Shift, int(shift_id)).status == ShiftStatus.CANCELLED


def test_student_calendar_only_returns_own_shifts(client, app):
    values = ids(app)
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        db.session.add_all(
            [
                Shift(
                    shift_date=date(2026, 8, 16),
                    shift_type_id=values["TEST_AM"],
                    staff_id=values["student_one"],
                    status=ShiftStatus.SCHEDULED,
                    created_by=admin.id,
                ),
                Shift(
                    shift_date=date(2026, 8, 17),
                    shift_type_id=values["TEST_PM"],
                    staff_id=values["student_two"],
                    status=ShiftStatus.SCHEDULED,
                    created_by=admin.id,
                ),
                Shift(
                    shift_date=date(2026, 8, 18),
                    shift_type_id=values["TEST_PM"],
                    staff_id=values["student_two"],
                    status=ShiftStatus.SCHEDULED,
                    publication_status=ShiftPublicationStatus.DRAFT,
                    created_by=admin.id,
                ),
            ]
        )
        db.session.commit()

    login(client, "student-test", "StudentTest!2026")
    response = client.get("/student/api/shifts?start=2026-08-01&end=2026-09-01")
    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]["extendedProps"]["staffName"] == "測試學生"
    assert response.json[0]["extendedProps"]["locationLabel"] == "辦公室"

    all_schedules = client.get(
        "/student/api/shifts?start=2026-08-01&end=2026-09-01&scope=all"
    )
    assert all_schedules.status_code == 200
    assert len(all_schedules.json) == 2
    assert {event["extendedProps"]["staffName"] for event in all_schedules.json} == {
        "測試學生",
        "第二位學生",
    }
    assert all(
        event["extendedProps"]["publicationStatus"] == "PUBLISHED"
        for event in all_schedules.json
    )

    invalid_scope = client.get(
        "/student/api/shifts?start=2026-08-01&end=2026-09-01&scope=unknown"
    )
    assert invalid_scope.status_code == 400

    hours = client.get("/student/api/monthly-hours?month=2026-08")
    assert hours.status_code == 200
    assert hours.json["total_hours"] == 4.0


def test_student_cannot_mutate_shifts(client, app):
    values = ids(app)
    login(client, "student-test", "StudentTest!2026")
    response = create_api_shift(
        client,
        shift_date="2026-08-18",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    )
    assert response.status_code == 403


def test_single_shift_over_eight_hours_is_rejected(client, app):
    values = ids(app)
    with app.app_context():
        location = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        long_type = ShiftType(
            code="TEST_LONG",
            name="超長班",
            name_en="Overlong shift",
            location_id=location.id,
            start_time=time(8, 0),
            end_time=time(17, 0),
            default_hours=Decimal("9"),
            display_order=999,
        )
        db.session.add(long_type)
        db.session.commit()
        long_type_id = long_type.id
    login(client)
    response = create_api_shift(
        client,
        shift_date="2026-09-01",
        staff_id=values["student_one"],
        shift_type_id=long_type_id,
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "SHIFT_EXCEEDS_DAILY_LIMIT"


def test_daily_total_over_eight_hours_is_rejected(client, app):
    values = ids(app)
    with app.app_context():
        location = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        types = [
            ShiftType(code="TEST_SIX", name="六小時班", name_en="Six hours", location_id=location.id, start_time=time(6), end_time=time(12), default_hours=Decimal("6"), display_order=991),
            ShiftType(code="TEST_THREE", name="三小時班", name_en="Three hours", location_id=location.id, start_time=time(12), end_time=time(15), default_hours=Decimal("3"), display_order=992),
        ]
        db.session.add_all(types)
        db.session.commit()
        first_id, second_id = (item.id for item in types)
    login(client)
    assert create_api_shift(client, shift_date="2026-09-02", staff_id=values["student_one"], shift_type_id=first_id).status_code == 201
    response = create_api_shift(client, shift_date="2026-09-02", staff_id=values["student_one"], shift_type_id=second_id)
    assert response.status_code == 409
    assert response.json["error"]["code"] == "DAILY_HOURS_LIMIT"


def test_sixth_consecutive_workday_is_rejected(client, app):
    values = ids(app)
    login(client)
    for day in range(1, 6):
        assert create_api_shift(client, shift_date=f"2026-09-{day:02d}", staff_id=values["student_one"], shift_type_id=values["TEST_AM"]).status_code == 201
    response = create_api_shift(client, shift_date="2026-09-06", staff_id=values["student_one"], shift_type_id=values["TEST_AM"])
    assert response.status_code == 409
    assert response.json["error"]["code"] == "CONSECUTIVE_DAYS_LIMIT"
