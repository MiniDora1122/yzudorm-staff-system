from app.extensions import db
from app.models import (
    DocumentPageKind,
    Notification,
    NotificationStatus,
    StaffDocument,
)

from .conftest import login
from .test_documents import logout, upload_residence


def _submit_residence_document(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        document_id = db.session.scalar(
            db.select(StaffDocument.id).where(
                StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT
            )
        )
    client.post(
        f"/student/documents/{document_id}/confirm",
        data={"residence_id": "A123456789", "residence_expiry": "2027-12-31"},
    )
    logout(client)
    login(client)
    return document_id


def test_notification_pages_are_role_protected(client):
    login(client, "student-test", "StudentTest!2026")
    assert client.get("/student/notifications").status_code == 200
    assert client.get("/admin/notifications").status_code == 403

    logout(client)
    login(client)
    assert client.get("/admin/notifications").status_code == 200
    assert client.get("/student/notifications").status_code == 403


def test_opening_notification_does_not_complete_it_but_finishing_review_does(client, app):
    document_id = _submit_residence_document(client, app)

    first_dashboard = client.get("/admin/")
    assert "測試學生的居留證等待審核".encode("utf-8") in first_dashboard.data
    assert "Notifications".encode("utf-8") in first_dashboard.data

    notification_page = client.get("/admin/notifications")
    assert "未完成事項".encode("utf-8") in notification_page.data
    assert "查看通知不等於完成".encode("utf-8") in notification_page.data
    client.get("/admin/documents")
    client.get("/admin/notifications")

    with app.app_context():
        notification = db.session.scalar(
            db.select(Notification).where(Notification.category == "DOCUMENT_REVIEW")
        )
        assert notification.status == NotificationStatus.OPEN
        assert notification.completed_at is None

    client.post(
        f"/admin/documents/{document_id}/review",
        data={"decision": "APPROVE", "fields_confirmed": "yes"},
        follow_redirects=True,
    )
    history_page = client.get("/admin/notifications")
    assert "已完成紀錄".encode("utf-8") in history_page.data
    assert "測試學生的居留證等待審核".encode("utf-8") in history_page.data

    with app.app_context():
        notification = db.session.scalar(
            db.select(Notification).where(Notification.category == "DOCUMENT_REVIEW")
        )
        assert notification.status == NotificationStatus.COMPLETED
        assert notification.completed_at is not None


def test_student_rejected_document_stays_open_until_resubmitted(client, app):
    document_id = _submit_residence_document(client, app)
    client.post(
        f"/admin/documents/{document_id}/review",
        data={"decision": "REJECT", "review_reason": "資料不清楚，請重新上傳"},
    )
    logout(client)
    login(client, "student-test", "StudentTest!2026")

    for _ in range(2):
        page = client.get("/student/", follow_redirects=True)
        assert page.status_code == 200
        assert "必須上傳外籍生證件".encode("utf-8") in page.data
        notifications = client.get("/student/notifications")
        assert "居留證已被退回，請修正".encode("utf-8") in notifications.data

    with app.app_context():
        notification = db.session.scalar(
            db.select(Notification).where(Notification.category == "DOCUMENT_REJECTED")
        )
        assert notification.status == NotificationStatus.OPEN

    client.post(
        f"/student/documents/{document_id}/confirm",
        data={"residence_id": "A123456789", "residence_expiry": "2027-12-31"},
        follow_redirects=True,
    )
    client.get("/student/notifications")
    with app.app_context():
        notification = db.session.scalar(
            db.select(Notification).where(Notification.category == "DOCUMENT_REJECTED")
        )
        assert notification.status == NotificationStatus.COMPLETED


def test_notification_reconciliation_is_throttled_between_read_only_pages(client, monkeypatch):
    import app.services.notifications as notifications

    calls = 0
    original = notifications.sync_admin_notifications

    def counted_sync():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(notifications, "sync_admin_notifications", counted_sync)
    login(client)
    client.get("/admin/")
    client.get("/admin/schedule")
    client.get("/admin/notifications")
    assert calls == 1
