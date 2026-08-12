from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models import (
    Country,
    Notification,
    SchedulingExceptionPeriod,
    SchedulingPolicy,
    Shift,
    ShiftStatus,
    ShiftType,
    StaffProfile,
    User,
)

from .conftest import login
from .test_scheduling import create_api_shift, ids


def _make_foreign_student(app, student_number="TEST001", *, exempt=False):
    with app.app_context():
        country = Country(
            code="JP",
            name="日本",
            name_en="Japan",
            weekly_limit_exempt=exempt,
            display_order=20,
        )
        db.session.add(country)
        profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == student_number)
        )
        profile.nationality = country.name
        db.session.commit()


def test_staff_roster_headers_sort_ascending_and_descending(client):
    login(client)
    descending = client.get("/admin/staff?sort=student_number&direction=desc")
    assert descending.status_code == 200
    assert descending.data.index(b"TEST002") < descending.data.index(b"TEST001")
    assert b"bi-sort-down" in descending.data

    ascending = client.get("/admin/staff?sort=student_number&direction=asc")
    assert ascending.data.index(b"TEST001") < ascending.data.index(b"TEST002")


def test_admin_manages_nationalities_and_student_forms_use_select(client, app):
    login(client)
    response = client.post(
        "/admin/settings/nationalities",
        data={
            "code": "VN",
            "name": "越南",
            "name_en": "Vietnam",
            "display_order": "20",
            "weekly_limit_exempt": "yes",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "越南".encode("utf-8") in response.data
    with app.app_context():
        country = db.session.scalar(db.select(Country).where(Country.code == "VN"))
        assert country.weekly_limit_exempt is True

    roster = client.get("/admin/staff")
    assert b'<select class="form-select" id="createNationality"' in roster.data
    assert "Vietnam".encode("utf-8") in roster.data


def test_foreign_student_is_redirected_until_both_documents_are_approved(client, app):
    _make_foreign_student(app)
    login(client, "student-test", "StudentTest!2026")
    blocked = client.get("/student/", follow_redirects=False)
    assert blocked.status_code == 302
    assert "/student/profile#documentUploadSection" in blocked.headers["Location"]

    profile_page = client.get("/student/profile")
    assert "必須完成外籍生證件".encode("utf-8") in profile_page.data
    assert "完成前其他功能暫停使用".encode("utf-8") in profile_page.data
    notification_page = client.get("/student/notifications")
    assert "必須完成居留證".encode("utf-8") in notification_page.data
    assert "必須完成工作證".encode("utf-8") in notification_page.data
    with app.app_context():
        assert db.session.scalar(
            db.select(db.func.count()).select_from(Notification).where(Notification.category == "DOCUMENT_REQUIRED")
        ) == 2


def test_foreign_student_cannot_self_declare_taiwanese_nationality(client, app):
    _make_foreign_student(app)
    login(client, "student-test", "StudentTest!2026")
    response = client.post(
        "/student/profile",
        data={"email": "student@example.test", "phone": "0912000000", "nationality": "台灣"},
        follow_redirects=True,
    )
    assert "必須由管理員核對後修改".encode("utf-8") in response.data
    with app.app_context():
        profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == "TEST001")
        )
        assert profile.nationality == "日本"


def test_foreign_weekly_limit_can_be_enabled_disabled_and_bypassed_by_period(client, app):
    _make_foreign_student(app)
    values = ids(app)
    login(client)
    for day in range(7, 12):
        assert create_api_shift(
            client,
            shift_date=f"2026-09-{day:02d}",
            staff_id=values["student_one"],
            shift_type_id=values["TEST_AM"],
        ).status_code == 201

    over_limit = create_api_shift(
        client,
        shift_date="2026-09-13",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    )
    assert over_limit.status_code == 409
    assert over_limit.json["error"]["code"] == "FOREIGN_WEEKLY_HOURS_LIMIT"

    with app.app_context():
        db.session.add(
            SchedulingExceptionPeriod(
                name="暑假",
                name_en="Summer break",
                starts_on=date(2026, 9, 13),
                ends_on=date(2026, 9, 13),
            )
        )
        db.session.commit()
    assert create_api_shift(
        client,
        shift_date="2026-09-13",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    ).status_code == 201

    with app.app_context():
        policy = db.session.get(SchedulingPolicy, 1)
        policy.foreign_weekly_limit_enabled = False
        policy.weekly_hour_limit = Decimal("20")
        db.session.commit()
    assert create_api_shift(
        client,
        shift_date="2026-09-14",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    ).status_code == 201


def test_week_start_day_is_configurable(client, app):
    _make_foreign_student(app)
    values = ids(app)
    with app.app_context():
        policy = db.session.get(SchedulingPolicy, 1)
        policy.week_starts_on = 6  # Sunday through Saturday
        db.session.commit()
    login(client)
    for day in (6, 7, 8, 9, 10):
        assert create_api_shift(
            client,
            shift_date=f"2026-09-{day:02d}",
            staff_id=values["student_one"],
            shift_type_id=values["TEST_AM"],
        ).status_code == 201

    over_limit = create_api_shift(
        client,
        shift_date="2026-09-12",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    )
    assert over_limit.status_code == 409
    assert over_limit.json["error"]["code"] == "FOREIGN_WEEKLY_HOURS_LIMIT"


def test_taiwan_is_limited_unless_admin_explicitly_marks_it_exempt(client, app):
    values = ids(app)
    login(client)
    for day in (14, 15, 16, 17, 19):
        assert create_api_shift(
            client,
            shift_date=f"2026-09-{day:02d}",
            staff_id=values["student_one"],
            shift_type_id=values["TEST_AM"],
        ).status_code == 201

    rejected = create_api_shift(
        client,
        shift_date="2026-09-20",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    )
    assert rejected.status_code == 409
    assert rejected.json["error"]["code"] == "FOREIGN_WEEKLY_HOURS_LIMIT"

    with app.app_context():
        taiwan = db.session.scalar(db.select(Country).where(Country.is_taiwan.is_(True)))
        taiwan.weekly_limit_exempt = True
        db.session.commit()
    assert create_api_shift(
        client,
        shift_date="2026-09-20",
        staff_id=values["student_one"],
        shift_type_id=values["TEST_AM"],
    ).status_code == 201


def test_admin_updates_week_start_and_taiwan_exemption(client, app):
    login(client)
    response = client.post(
        "/admin/settings/scheduling",
        data={
            "foreign_weekly_limit_enabled": "yes",
            "weekly_hour_limit": "24",
            "week_starts_on": "6",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b'<option value="6" selected>' in response.data

    with app.app_context():
        policy = db.session.get(SchedulingPolicy, 1)
        taiwan = db.session.scalar(db.select(Country).where(Country.is_taiwan.is_(True)))
        taiwan_id = taiwan.id
        assert policy.week_starts_on == 6
        assert policy.weekly_hour_limit == Decimal("24")

    client.post(
        f"/admin/settings/nationalities/{taiwan_id}",
        data={
            "name": "台灣",
            "name_en": "Taiwan",
            "display_order": "10",
            "weekly_limit_exempt": "yes",
            "is_active": "yes",
        },
    )
    with app.app_context():
        assert db.session.get(Country, taiwan_id).weekly_limit_exempt is True

    invalid = client.post(
        "/admin/settings/scheduling",
        data={"weekly_hour_limit": "20", "week_starts_on": "7"},
        follow_redirects=True,
    )
    assert "每週起始日設定無效".encode("utf-8") in invalid.data
    with app.app_context():
        assert db.session.get(SchedulingPolicy, 1).week_starts_on == 6


def test_deleting_student_archives_account_but_retains_historical_shift(client, app):
    with app.app_context():
        profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == "TEST001")
        )
        shift_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == "TEST_AM"))
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        shift = Shift(
            shift_date=date(2020, 1, 1),
            shift_type_id=shift_type.id,
            staff_id=profile.id,
            status=ShiftStatus.SCHEDULED,
            created_by=admin.id,
        )
        db.session.add(shift)
        db.session.commit()
        profile_id = profile.id
        shift_id = shift.id

    login(client)
    response = client.post(f"/admin/staff/{profile_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert "歷史排班、報表及文件稽核仍保留".encode("utf-8") in response.data
    with app.app_context():
        assert db.session.get(StaffProfile, profile_id).user.is_active is False
        assert db.session.get(Shift, shift_id).staff_id == profile_id


def test_settings_pages_are_admin_only(client):
    login(client, "student-test", "StudentTest!2026")
    assert client.get("/admin/settings/nationalities").status_code == 403
    assert client.get("/admin/settings/scheduling").status_code == 403


def test_taiwanese_student_is_not_asked_to_upload_sensitive_documents(client):
    login(client, "student-test", "StudentTest!2026")
    page = client.get("/student/profile")
    assert "無需上傳證件".encode("utf-8") in page.data
    assert b'id="documentUploadSection"' not in page.data
    rejected = client.post("/student/documents", data={}, follow_redirects=True)
    assert "不需要上傳居留證或工作證".encode("utf-8") in rejected.data
