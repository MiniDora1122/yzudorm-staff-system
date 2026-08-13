from datetime import date

from app.extensions import db
from app.models import AuditLog, Role, Shift, ShiftSeries, ShiftStatus, ShiftType, StaffProfile, User, WorkLocation

from .conftest import login


def assignment_ids(app):
    with app.app_context():
        return {
            "student_one": db.session.scalar(db.select(StaffProfile.id).where(StaffProfile.student_number == "TEST001")),
            "student_two": db.session.scalar(db.select(StaffProfile.id).where(StaffProfile.student_number == "TEST002")),
            "am": db.session.scalar(db.select(ShiftType.id).where(ShiftType.code == "TEST_AM")),
            "pm": db.session.scalar(db.select(ShiftType.id).where(ShiftType.code == "TEST_PM")),
        }


def create_shift(client, *, shift_date, staff_id, shift_type_id, **extra):
    return client.post(
        "/admin/api/shifts",
        json={"shift_date": shift_date, "staff_id": staff_id, "shift_type_id": shift_type_id, **extra},
    )


def test_weekly_series_creates_real_shifts_through_end_date(client, app):
    values = assignment_ids(app)
    login(client)
    response = create_shift(
        client,
        shift_date="2026-09-01",
        staff_id=values["student_one"],
        shift_type_id=values["am"],
        repeat_weekly=True,
        recurrence_end="2027-01-30",
    )
    assert response.status_code == 201
    assert response.json["count"] == 22
    with app.app_context():
        series = db.session.get(ShiftSeries, response.json["seriesId"])
        shifts = db.session.scalars(db.select(Shift).where(Shift.series_id == series.id).order_by(Shift.shift_date)).all()
        assert series.starts_on == date(2026, 9, 1)
        assert series.ends_on == date(2027, 1, 30)
        assert series.weekday == 1
        assert len(shifts) == 22
        assert all((right.shift_date - left.shift_date).days == 7 for left, right in zip(shifts, shifts[1:]))
        assert shifts[-1].shift_date <= series.ends_on
        assert db.session.scalar(db.select(AuditLog).where(AuditLog.action == "SHIFT_SERIES_CREATED"))


def test_weekly_series_can_keep_staff_and_shift_for_next_entry(client):
    login(client)
    script = client.get("/static/js/admin_schedule.js")
    assert script.status_code == 200
    assert b'const keepAdding = !shiftId && document.getElementById("continueAdding").checked;' in script.data
    assert b'document.getElementById("repeatWeekly").checked = false;' in script.data
    assert b'if (!keepAdding) modal.hide();' in script.data


def test_weekly_series_rolls_back_every_occurrence_when_one_conflicts(client, app):
    values = assignment_ids(app)
    login(client)
    assert create_shift(
        client, shift_date="2026-09-15", staff_id=values["student_one"], shift_type_id=values["am"]
    ).status_code == 201
    response = create_shift(
        client,
        shift_date="2026-09-01",
        staff_id=values["student_one"],
        shift_type_id=values["am"],
        repeat_weekly=True,
        recurrence_end="2026-09-29",
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "STAFF_TIME_OVERLAP"
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Shift)) == 1
        assert db.session.scalar(db.select(db.func.count()).select_from(ShiftSeries)) == 0


def test_recurring_shift_can_delete_this_and_following_occurrences(client, app):
    values = assignment_ids(app)
    login(client)
    created = create_shift(
        client,
        shift_date="2026-09-01",
        staff_id=values["student_one"],
        shift_type_id=values["am"],
        repeat_weekly=True,
        recurrence_end="2026-09-29",
    )
    with app.app_context():
        shifts = db.session.scalars(
            db.select(Shift).where(Shift.series_id == created.json["seriesId"]).order_by(Shift.shift_date)
        ).all()
        third_id = shifts[2].id
    deleted = client.delete(f"/admin/api/shifts/{third_id}?scope=future")
    assert deleted.status_code == 200
    assert deleted.json["cancelled"] == 3
    with app.app_context():
        states = db.session.scalars(
            db.select(Shift.status).where(Shift.series_id == created.json["seriesId"]).order_by(Shift.shift_date)
        ).all()
        assert states == [ShiftStatus.SCHEDULED, ShiftStatus.SCHEDULED, ShiftStatus.CANCELLED, ShiftStatus.CANCELLED, ShiftStatus.CANCELLED]


def test_bulk_delete_cancels_selected_different_shifts(client, app):
    values = assignment_ids(app)
    login(client)
    first = create_shift(client, shift_date="2026-10-01", staff_id=values["student_one"], shift_type_id=values["am"])
    second = create_shift(client, shift_date="2026-10-02", staff_id=values["student_two"], shift_type_id=values["pm"])
    response = client.post("/admin/api/shifts/bulk-delete", json={"shift_ids": [first.json["id"], second.json["id"]]})
    assert response.status_code == 200
    assert response.json["cancelled"] == 2
    with app.app_context():
        assert set(db.session.scalars(db.select(Shift.status)).all()) == {ShiftStatus.CANCELLED}


def test_location_and_shift_type_delete_are_safe_archives(client, app):
    login(client)
    with app.app_context():
        office = db.session.scalar(db.select(WorkLocation).where(WorkLocation.code == "OFFICE"))
        office_id = office.id
        type_ids = [item.id for item in office.shift_types]
        mc_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == "TEST_PM"))
        mc_type_id = mc_type.id
    assert client.delete(f"/admin/api/shift-types/{mc_type_id}").status_code == 200
    response = client.delete(f"/admin/api/locations/{office_id}")
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(WorkLocation, office_id).is_active is False
        assert all(db.session.get(ShiftType, item_id).is_active is False for item_id in type_ids)
        assert db.session.get(ShiftType, mc_type_id).is_active is False


def test_admin_and_unused_student_accounts_can_be_deleted_but_not_current_admin(client, app):
    login(client)
    with app.app_context():
        current_admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        student_two = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST002"))
        current_admin_id = current_admin.id
        student_two_id = student_two.id
        second_admin = User(username="admin-delete", display_name="Delete Me", role=Role.ADMIN)
        second_admin.set_password("DeleteAdmin!2026")
        db.session.add(second_admin)
        db.session.commit()
        second_admin_id = second_admin.id
    client.post(f"/admin/admin-accounts/{current_admin_id}/delete")
    client.post(f"/admin/admin-accounts/{second_admin_id}/delete")
    client.post(f"/admin/staff/{student_two_id}/delete")
    with app.app_context():
        assert db.session.get(User, current_admin_id).is_active is True
        assert db.session.get(User, second_admin_id).is_active is False
        assert db.session.get(StaffProfile, student_two_id).user.is_active is False


def test_student_with_future_shift_must_clear_schedule_before_deletion(client, app):
    values = assignment_ids(app)
    login(client)
    shift = create_shift(client, shift_date="2026-10-06", staff_id=values["student_two"], shift_type_id=values["am"])
    client.post(f"/admin/staff/{values['student_two']}/delete")
    with app.app_context():
        assert db.session.get(StaffProfile, values["student_two"]).user.is_active is True
    client.delete(f"/admin/api/shifts/{shift.json['id']}")
    client.post(f"/admin/staff/{values['student_two']}/delete")
    with app.app_context():
        assert db.session.get(StaffProfile, values["student_two"]).user.is_active is False
