from datetime import date, time
from decimal import Decimal

from app.extensions import db
from app.models import Role, Shift, ShiftStatus, ShiftType, StaffProfile, User, WorkLocation


def test_model_basic_crud(app):
    with app.app_context():
        user = User(username="crud-user", role=Role.STUDENT)
        user.set_password("CrudPassword!2026")
        db.session.add(user)
        db.session.flush()

        profile = StaffProfile(
            user_id=user.id,
            name="CRUD 測試",
            student_number="CRUD001",
            nationality="台灣",
        )
        location = WorkLocation(
            code="CRUD_MC", name="CRUD 管理中心", name_en="CRUD Management Center", color="#1556a3", display_order=99
        )
        db.session.add(location)
        db.session.flush()
        shift_type = ShiftType(
            code="CRUD_PM",
            name="CRUD 下午班",
            name_en="CRUD Afternoon Shift",
            location_id=location.id,
            start_time=time(13),
            end_time=time(17),
            default_hours=Decimal("4"),
            display_order=99,
        )
        db.session.add_all([profile, shift_type])
        db.session.flush()

        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        shift = Shift(
            shift_date=date(2026, 8, 20),
            shift_type_id=shift_type.id,
            staff_id=profile.id,
            status=ShiftStatus.SCHEDULED,
            created_by=admin.id,
        )
        db.session.add(shift)
        db.session.commit()

        saved = db.session.scalar(db.select(Shift).where(Shift.id == shift.id))
        assert saved.staff.name == "CRUD 測試"
        assert saved.shift_type.location == "CRUD_MC"

        profile.phone = "0900-000-000"
        db.session.commit()
        assert db.session.get(StaffProfile, profile.id).phone == "0900-000-000"

        db.session.delete(shift)
        db.session.commit()
        assert db.session.get(Shift, shift.id) is None
