from datetime import date, time
from decimal import Decimal

import click

from .extensions import db
from .models import (
    Country,
    PayrollSetting,
    Role,
    SchedulingPolicy,
    Shift,
    ShiftStatus,
    ShiftType,
    StaffProfile,
    User,
    WorkLocation,
)


DEMO_USERS = (
    {
        "username": "admin",
        "display_name": "系統管理員",
        "password": "AdminDemo!2026",
        "role": Role.ADMIN,
    },
    {
        "username": "student1",
        "password": "StudentDemo!2026",
        "role": Role.STUDENT,
        "profile": {
            "name": "陳小安",
            "student_number": "DEMO001",
            "phone": "0912-000-001",
            "email": "student1@example.test",
            "nationality": "台灣",
            "labor_insured_salary": Decimal("29500"),
            "health_insured_salary": Decimal("29500"),
            "pension_salary": Decimal("4500"),
        },
    },
    {
        "username": "student2",
        "password": "StudentDemo!2026",
        "role": Role.STUDENT,
        "profile": {
            "name": "Alex Chen",
            "student_number": "DEMO002",
            "phone": "0912-000-002",
            "email": "student2@example.test",
            "nationality": "外國籍",
            "residence_expiry": date(2027, 12, 31),
            "work_permit_start": date(2026, 5, 1),
            "work_permit_expiry": date(2027, 5, 1),
            "labor_insured_salary": Decimal("29500"),
            "health_insured_salary": Decimal("29500"),
            "pension_salary": Decimal("4500"),
        },
    },
)


LOCATIONS = (
    ("OFFICE", "辦公室", "Office", "#198754", 10),
    ("MC", "管理中心", "Management Center", "#1556a3", 20),
)


SHIFT_TYPES = (
    ("OFFICE_AM", "辦公室上午班", "Office Morning Shift", "OFFICE", time(9), time(13), Decimal("4"), 10),
    ("OFFICE_PM", "辦公室下午班", "Office Afternoon Shift", "OFFICE", time(13), time(17), Decimal("4"), 20),
    ("MC_AM", "管理中心上午班", "Management Center Morning Shift", "MC", time(9), time(13), Decimal("4"), 30),
    ("MC_PM", "管理中心下午班", "Management Center Afternoon Shift", "MC", time(13), time(17), Decimal("4"), 40),
    ("MC_EVENING", "管理中心晚班", "Management Center Evening Shift", "MC", time(18), time(21), Decimal("3"), 50),
    ("MC_SDA", "管理中心 SDA 班", "Management Center SDA Shift", "MC", time(17, 30), time(21, 30), Decimal("4"), 60),
)


DEMO_SHIFTS = (
    (date(2026, 8, 11), "MC_PM", "student2"),
    (date(2026, 8, 12), "OFFICE_AM", "student1"),
    (date(2026, 8, 15), "MC_EVENING", "student1"),
    (date(2026, 8, 20), "MC_SDA", "student2"),
    (date(2026, 8, 21), "OFFICE_PM", "student1"),
    (date(2026, 8, 25), "MC_AM", "student1"),
)


def seed_database() -> None:
    for code, name, name_en, is_taiwan, order in (
        ("TW", "台灣", "Taiwan", True, 10),
        ("FOREIGN", "外國籍", "Foreign nationality", False, 100),
    ):
        country = db.session.scalar(
            db.select(Country).where(db.or_(Country.code == code, Country.name == name))
        )
        if country is None:
            db.session.add(
                Country(code=code, name=name, name_en=name_en, is_taiwan=is_taiwan, display_order=order)
            )
    db.session.flush()
    used_codes = set(db.session.scalars(db.select(Country.code)).all())
    existing_nationalities = db.session.scalars(
        db.select(StaffProfile.nationality).distinct().order_by(StaffProfile.nationality)
    ).all()
    legacy_index = 1
    for nationality in existing_nationalities:
        if not nationality or db.session.scalar(db.select(Country.id).where(Country.name == nationality)):
            continue
        while f"NAT{legacy_index:03d}" in used_codes:
            legacy_index += 1
        code = f"NAT{legacy_index:03d}"
        used_codes.add(code)
        db.session.add(
            Country(
                code=code,
                name=nationality,
                name_en=nationality,
                display_order=100 + legacy_index,
            )
        )
        legacy_index += 1
    if db.session.get(SchedulingPolicy, 1) is None:
        db.session.add(
            SchedulingPolicy(
                id=1,
                foreign_weekly_limit_enabled=True,
                weekly_hour_limit=Decimal("20"),
                week_starts_on=0,
            )
        )
    db.session.flush()

    users: dict[str, User] = {}
    for data in DEMO_USERS:
        user = db.session.scalar(db.select(User).where(User.username == data["username"]))
        if user is None:
            user = User(
                username=data["username"],
                display_name=data.get("display_name"),
                role=data["role"],
            )
            user.set_password(data["password"])
            db.session.add(user)
            db.session.flush()
        elif data.get("display_name") and not user.display_name:
            user.display_name = data["display_name"]
        users[user.username] = user
        profile_data = data.get("profile")
        if profile_data and user.staff_profile is None:
            db.session.add(StaffProfile(user_id=user.id, **profile_data))
        elif profile_data and user.staff_profile is not None:
            for field in ("labor_insured_salary", "health_insured_salary", "pension_salary"):
                if getattr(user.staff_profile, field) is None:
                    setattr(user.staff_profile, field, profile_data.get(field))

    locations: dict[str, WorkLocation] = {}
    for code, name, name_en, color, order in LOCATIONS:
        location = db.session.scalar(
            db.select(WorkLocation).where(WorkLocation.code == code)
        )
        if location is None:
            location = WorkLocation(
                code=code,
                name=name,
                name_en=name_en,
                color=color,
                display_order=order,
            )
            db.session.add(location)
            db.session.flush()
        locations[code] = location

    shift_types: dict[str, ShiftType] = {}
    for code, name, name_en, location, start, end, hours, order in SHIFT_TYPES:
        shift_type = db.session.scalar(db.select(ShiftType).where(ShiftType.code == code))
        if shift_type is None:
            shift_type = ShiftType(
                code=code,
                name=name,
                name_en=name_en,
                location_id=locations[location].id,
                start_time=start,
                end_time=end,
                default_hours=hours,
                display_order=order,
            )
            db.session.add(shift_type)
            db.session.flush()
        shift_types[code] = shift_type

    payroll_setting = db.session.scalar(
        db.select(PayrollSetting).where(PayrollSetting.effective_date == date(2026, 1, 1))
    )
    if payroll_setting is None:
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

    db.session.flush()
    for shift_date, shift_code, username in DEMO_SHIFTS:
        shift_type = shift_types[shift_code]
        staff = users[username].staff_profile
        existing = db.session.scalar(
            db.select(Shift).where(
                Shift.shift_date == shift_date,
                Shift.shift_type_id == shift_type.id,
            )
        )
        if existing is None and staff is not None:
            db.session.add(
                Shift(
                    shift_date=shift_date,
                    shift_type_id=shift_type.id,
                    staff_id=staff.id,
                    status=ShiftStatus.SCHEDULED,
                    created_by=users["admin"].id,
                )
            )

    db.session.commit()


def register_commands(app) -> None:
    from .services.retention import register_retention_commands

    register_retention_commands(app)

    @app.cli.command("seed")
    def seed_command():
        """建立可重複執行的開發用示範資料。"""
        seed_database()
        click.echo("Seed data created or already present.")
