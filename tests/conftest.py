from datetime import date, time
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app import create_app
from app.extensions import db
from app.models import PayrollSetting, Role, ShiftType, StaffProfile, User, WorkLocation


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "tests-only-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
            "DOCUMENT_STORAGE_DIR": str(tmp_path / "private_documents"),
            "DOCUMENT_KEY_DIR": str(tmp_path / "private_keys"),
            "DOCUMENT_KEY_BACKUP_DIR": str(tmp_path / "private_keys" / "backup"),
            "DOCUMENT_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
            "DOCUMENT_CLEANUP_SCHEDULER_ENABLED": False,
        }
    )
    with app.app_context():
        db.create_all()

        admin = User(username="admin-test", display_name="測試管理員", role=Role.ADMIN)
        admin.set_password("AdminTest!2026")
        student = User(username="student-test", role=Role.STUDENT)
        student.set_password("StudentTest!2026")
        student_two = User(username="student-two", role=Role.STUDENT)
        student_two.set_password("StudentTwo!2026")
        db.session.add_all([admin, student, student_two])
        db.session.flush()
        db.session.add_all(
            [
            StaffProfile(
                user_id=student.id,
                name="測試學生",
                student_number="TEST001",
                nationality="台灣",
                labor_insured_salary=Decimal("29500"),
                health_insured_salary=Decimal("29500"),
                pension_salary=Decimal("4500"),
            ),
            StaffProfile(
                user_id=student_two.id,
                name="第二位學生",
                student_number="TEST002",
                nationality="台灣",
            ),
            ]
        )
        office = WorkLocation(code="OFFICE", name="辦公室", name_en="Office", color="#198754", display_order=1)
        management = WorkLocation(code="MC", name="管理中心", name_en="Management Center", color="#1556a3", display_order=2)
        db.session.add_all([office, management])
        db.session.flush()
        db.session.add_all(
            [
            ShiftType(
                code="TEST_AM",
                name="測試上午班",
                name_en="Test Morning Shift",
                location_id=office.id,
                start_time=time(9),
                end_time=time(13),
                default_hours=Decimal("4"),
                display_order=1,
            ),
            ShiftType(
                code="TEST_AM_ALT",
                name="相同地點同時段班",
                name_en="Alternate Morning Shift",
                location_id=office.id,
                start_time=time(9),
                end_time=time(13),
                default_hours=Decimal("4"),
                display_order=2,
            ),
            ShiftType(
                code="TEST_OVERLAP",
                name="跨地點重疊班",
                name_en="Overlapping Shift",
                location_id=management.id,
                start_time=time(11),
                end_time=time(15),
                default_hours=Decimal("4"),
                display_order=3,
            ),
            ShiftType(
                code="TEST_PM",
                name="測試下午班",
                name_en="Test Afternoon Shift",
                location_id=management.id,
                start_time=time(15),
                end_time=time(19),
                default_hours=Decimal("4"),
                display_order=4,
            ),
            ]
        )
        db.session.add(
            PayrollSetting(
                effective_date=date(2026, 1, 1),
                default_hourly_wage=Decimal("196"),
                labor_insurance_rate=Decimal("0.115"),
                employment_insurance_rate=Decimal("0.01"),
                employer_labor_share=Decimal("0.70"),
                occupational_accident_rate=Decimal("0.0015"),
                health_insurance_rate=Decimal("0.0517"),
                employer_health_share=Decimal("0.60"),
                average_dependents=Decimal("0.56"),
                supplementary_health_rate=Decimal("0.0211"),
                employer_pension_rate=Decimal("0.06"),
            )
        )
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, username="admin-test", password="AdminTest!2026"):
    return client.post(
        "/auth/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
