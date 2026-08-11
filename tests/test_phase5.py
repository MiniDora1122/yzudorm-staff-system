from datetime import timedelta
from pathlib import Path
from app.extensions import db
from app.models import AuditLog, DocumentPageKind, DocumentStatus, StaffDocument, StaffProfile, utc_now
from app.services.document_keys import ensure_document_encryption_key
from app.services.retention import _scheduled_tick

from .conftest import login
from .test_documents import admin_review, image_upload, upload_residence


def logout(client):
    client.post("/auth/logout")


def test_key_primary_and_backup_are_created_and_primary_can_be_restored(app):
    primary = Path(app.config["DOCUMENT_KEY_PRIMARY_PATH"])
    backup = Path(app.config["DOCUMENT_KEY_BACKUP_PATH"])
    assert primary.read_bytes() == backup.read_bytes()
    primary.unlink()
    result = ensure_document_encryption_key(app)
    assert result["primary"].read_bytes() == result["backup"].read_bytes()
    original = backup.read_bytes()
    primary.write_text("corrupted-key", encoding="utf-8")
    ensure_document_encryption_key(app)
    assert primary.read_bytes() == original


def test_work_permit_confirmation_only_requires_start_and_end(client, app):
    login(client, "student-test", "StudentTest!2026")
    client.post(
        "/student/documents",
        data={
            "document_type": "WORK_PERMIT",
            "privacy_consent": "yes",
            "work_permit_page_1": image_upload("white", "work-permit-page-1.png"),
            "work_permit_page_2": image_upload("lightblue", "work-permit-page-2.png"),
        },
        content_type="multipart/form-data",
    )
    with app.app_context():
        document_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.WORK_PERMIT_PAGE_1))
    client.post(
        f"/student/documents/{document_id}/confirm",
        data={"work_permit_start": "2026-09-01", "work_permit_expiry": "2027-08-31"},
    )
    with app.app_context():
        profile = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST001"))
        assert profile.work_permit_start is None
        assert db.session.get(StaffDocument, document_id).status == DocumentStatus.PENDING_ADMIN
    logout(client)
    login(client)
    review_page = client.get("/admin/documents")
    assert b"2026-09-01" in review_page.data
    assert b"2027-08-31" in review_page.data
    admin_review(client, document_id)
    with app.app_context():
        profile = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST001"))
        assert profile.work_permit_start.isoformat() == "2026-09-01"
        assert profile.work_permit_expiry.isoformat() == "2027-08-31"
        set_id = db.session.get(StaffDocument, document_id).document_set_id
        assert all(item.status == DocumentStatus.CONFIRMED for item in db.session.scalars(db.select(StaffDocument).where(StaffDocument.document_set_id == set_id)))


def test_admin_retention_policy_cleanup_deletes_bytes_and_keeps_audit(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        document_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))
        stored_path = Path(app.config["DOCUMENT_STORAGE_DIR"]) / db.session.get(StaffDocument, document_id).storage_key

    logout(client)
    login(client)
    response = client.post(
        "/admin/documents/retention",
        data={"retention_days": "30", "cleanup_time": "02:30"},
    )
    assert response.status_code == 302
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        document.retention_until = utc_now() - timedelta(days=1)
        db.session.commit()

    client.post("/admin/documents/cleanup")
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        assert document.status == DocumentStatus.DELETED
        assert document.storage_key is None
        assert not stored_path.exists()
        assert db.session.scalar(
            db.select(db.func.count()).select_from(AuditLog).where(
                AuditLog.action == "DOCUMENT_PURGED_BY_RETENTION",
                AuditLog.entity_id == document_id,
            )
        ) == 1


def test_scheduled_tick_runs_once_policy_time_is_due(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        document_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))
    logout(client)
    login(client)
    client.post(
        "/admin/documents/retention",
        data={"retention_days": "1", "cleanup_time": "00:00"},
    )
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        document.retention_until = utc_now() - timedelta(minutes=1)
        db.session.commit()
    _scheduled_tick(app)
    with app.app_context():
        assert db.session.get(StaffDocument, document_id).status == DocumentStatus.DELETED
