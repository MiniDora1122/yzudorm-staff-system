import hashlib
import json
import sqlite3
import zipfile
from datetime import date

from app.extensions import db
from app.models import (
    RequirementStatus,
    Shift,
    ShiftPublicationStatus,
    StaffGroup,
    StaffingRequirement,
    StaffProfile,
    VacancyApplication,
    VacancyApplicationStatus,
)
from deployment.create_portable_backup import verify_backup

from .conftest import login


def _ids(app):
    with app.app_context():
        profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == "TEST001")
        )
        shift_type_id = db.session.scalar(db.select(Shift.shift_type_id).limit(1))
        if shift_type_id is None:
            from app.models import ShiftType

            shift_type_id = db.session.scalar(
                db.select(ShiftType.id).where(ShiftType.code == "TEST_AM")
            )
        return profile.id, shift_type_id


def test_draft_publish_close_unlock_workflow(client, app):
    staff_id, shift_type_id = _ids(app)
    login(client)
    assert client.get("/admin/operations?month=2026-11").status_code == 200
    created = client.post(
        "/admin/api/shifts",
        json={
            "shift_date": "2026-11-03",
            "staff_id": staff_id,
            "shift_type_id": shift_type_id,
            "publication_status": "DRAFT",
        },
    )
    assert created.status_code == 201
    assert created.json["extendedProps"]["publicationStatus"] == "DRAFT"

    client.post("/auth/logout")
    login(client, "student-test", "StudentTest!2026")
    hidden = client.get("/student/api/shifts?start=2026-11-01&end=2026-12-01")
    assert hidden.json == []

    client.post("/auth/logout")
    login(client)
    published = client.post("/admin/operations/publish", data={"month": "2026-11"})
    assert published.status_code == 302
    closed = client.post("/admin/operations/close", data={"month": "2026-11"})
    assert closed.status_code == 302
    blocked = client.post(
        "/admin/api/shifts",
        json={
            "shift_date": "2026-11-10",
            "staff_id": staff_id,
            "shift_type_id": shift_type_id,
            "publication_status": "DRAFT",
        },
    )
    assert blocked.status_code == 400
    assert "鎖定" in blocked.json["error"]["message"]

    client.post(
        "/admin/operations/unlock",
        data={"month": "2026-11", "reason": "主管核准修正漏列班次"},
    )
    unblocked = client.post(
        "/admin/api/shifts",
        json={
            "shift_date": "2026-11-10",
            "staff_id": staff_id,
            "shift_type_id": shift_type_id,
            "publication_status": "DRAFT",
        },
    )
    assert unblocked.status_code == 201


def test_group_targeted_vacancy_application_becomes_published_shift(client, app):
    staff_id, shift_type_id = _ids(app)
    login(client)
    response = client.post(
        "/admin/workforce/groups",
        data={"name": "晚班組", "name_en": "Evening Team", "staff_ids": str(staff_id)},
    )
    assert response.status_code == 302
    assert client.get("/admin/workforce").status_code == 200
    with app.app_context():
        group_id = db.session.scalar(db.select(StaffGroup.id).where(StaffGroup.name == "晚班組"))

    response = client.post(
        "/admin/workforce/requirements",
        data={
            "shift_date": "2026-12-08",
            "shift_type_id": str(shift_type_id),
            "required_count": "1",
            "group_ids": str(group_id),
            "note": "可獨立值班",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        requirement_id = db.session.scalar(db.select(StaffingRequirement.id))

    client.post("/auth/logout")
    login(client, "student-test", "StudentTest!2026")
    page = client.get("/student/vacancies")
    assert "可獨立值班".encode("utf-8") in page.data
    applied = client.post(
        f"/student/vacancies/{requirement_id}/apply", data={"note": "可以配合"}
    )
    assert applied.status_code == 302
    with app.app_context():
        application_id = db.session.scalar(db.select(VacancyApplication.id))

    client.post("/auth/logout")
    login(client)
    reviewed = client.post(
        f"/admin/workforce/applications/{application_id}/review",
        data={"decision": "APPROVE", "review_note": "核准"},
    )
    assert reviewed.status_code == 302
    with app.app_context():
        application = db.session.get(VacancyApplication, application_id)
        requirement = db.session.get(StaffingRequirement, requirement_id)
        shift = db.session.scalar(
            db.select(Shift).where(
                Shift.shift_date == date(2026, 12, 8), Shift.staff_id == staff_id
            )
        )
        assert application.status == VacancyApplicationStatus.APPROVED
        assert requirement.status == RequirementStatus.CLOSED
        assert shift.publication_status == ShiftPublicationStatus.PUBLISHED


def test_archived_staff_can_be_restored(client, app):
    with app.app_context():
        profile = db.session.scalar(
            db.select(StaffProfile).where(StaffProfile.student_number == "TEST002")
        )
        profile_id = profile.id
    login(client)
    client.post(f"/admin/staff/{profile_id}/delete")
    archived_page = client.get("/admin/staff?status=archived")
    assert b"TEST002" in archived_page.data
    client.post(f"/admin/staff/{profile_id}/restore")
    with app.app_context():
        assert db.session.get(StaffProfile, profile_id).user.is_active is True


def test_backup_verifier_checks_manifest_and_sqlite_integrity(tmp_path):
    database = tmp_path / "snapshot.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO sample (value) VALUES ('ok')")
    data = database.read_bytes()
    manifest = {
        "format": "dorm-staff-portable-backup-v1",
        "file_count": 1,
        "files": {
            "instance/dorm_staff.db": {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        },
    }
    archive = tmp_path / "verified.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("instance/dorm_staff.db", data)
        output.writestr("PORTABLE_BACKUP_MANIFEST.json", json.dumps(manifest))
    assert verify_backup(archive)["file_count"] == 1
