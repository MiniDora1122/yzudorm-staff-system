from io import BytesIO

from PIL import Image

from app.extensions import db
from app.models import DocumentPageKind, DocumentStatus, StaffDocument, StaffProfile

from .conftest import login


def logout(client):
    client.post("/auth/logout")


def image_upload(color="white", filename="permit.png"):
    stream = BytesIO()
    Image.new("RGB", (900, 560), color).save(stream, format="PNG")
    stream.seek(0)
    return stream, filename


def upload_residence(client, color="white", filename="permit.png"):
    back_color = "lightgreen" if color == "lightblue" else "lightgray"
    return client.post(
        "/student/documents",
        data={
            "document_type": "RESIDENCE_PERMIT",
            "privacy_consent": "yes",
            "residence_front": image_upload(color, filename),
            "residence_back": image_upload(back_color, f"back-{filename}"),
        },
        content_type="multipart/form-data",
    )


def admin_review(client, document_id, decision="APPROVE", reason="", fields_confirmed=True):
    logout(client)
    login(client)
    return client.post(
        f"/admin/documents/{document_id}/review",
        data={
            "decision": decision,
            "review_reason": reason,
            "fields_confirmed": "yes" if fields_confirmed else "",
        },
    )


def test_document_is_encrypted_and_unconfirmed_data_does_not_overwrite_profile(client, app):
    login(client, "student-test", "StudentTest!2026")
    assert upload_residence(client).status_code == 302

    with app.app_context():
        document = db.session.scalar(db.select(StaffDocument).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))
        profile = db.session.scalar(db.select(StaffProfile).where(StaffProfile.student_number == "TEST001"))
        assert document.status == DocumentStatus.NEEDS_REVIEW
        assert profile.residence_id is None
        stored = (app.config["DOCUMENT_STORAGE_DIR"] + "/" + document.storage_key)
        with open(stored, "rb") as encrypted_file:
            encrypted = encrypted_file.read()
        assert not encrypted.startswith(b"\xff\xd8\xff")
        document_id = document.id

    preview = client.get(f"/student/documents/{document_id}/file")
    assert preview.status_code == 200
    assert preview.data.startswith(b"\xff\xd8\xff")
    assert "no-store" in preview.headers["Cache-Control"]


def test_owner_submits_then_admin_approves_and_confirmed_image_is_retained(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        document_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))

    response = client.post(
        f"/student/documents/{document_id}/confirm",
        data={"residence_id": "A123456789", "residence_expiry": "2026-12-31"},
    )
    assert response.status_code == 302
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        profile = document.staff
        assert document.status == DocumentStatus.PENDING_ADMIN
        assert profile.residence_id is None
        storage_key = document.storage_key

    assert admin_review(client, document_id).status_code == 302
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        assert document.status == DocumentStatus.CONFIRMED
        assert document.staff.residence_id == "A123456789"

    logout(client)
    login(client, "student-test", "StudentTest!2026")
    client.post(f"/student/documents/{document_id}/delete")
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        assert document.status == DocumentStatus.CONFIRMED
        assert document.storage_key == storage_key


def test_student_cannot_read_another_students_document_but_admin_can_download(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        document_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))

    logout(client)
    login(client, "student-two", "StudentTwo!2026")
    assert client.get(f"/student/documents/{document_id}/file").status_code == 404
    assert client.get(f"/student/documents/{document_id}/download").status_code == 404

    logout(client)
    login(client)
    download = client.get(f"/admin/documents/{document_id}/download")
    assert download.status_code == 200
    assert download.data.startswith(b"\xff\xd8\xff")
    assert "attachment" in download.headers["Content-Disposition"]
    assert "no-store" in download.headers["Cache-Control"]


def test_invalid_file_is_rejected(client, app):
    login(client, "student-test", "StudentTest!2026")
    response = client.post(
        "/student/documents",
        data={
            "document_type": "WORK_PERMIT",
            "privacy_consent": "yes",
            "work_permit_page_1": (BytesIO(b"not an image"), "permit.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "圖片內容損毀或格式不合法".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(StaffDocument)) == 0


def test_image_signature_must_match_extension_and_mime(client, app):
    login(client, "student-test", "StudentTest!2026")
    response = client.post(
        "/student/documents",
        data={
            "document_type": "WORK_PERMIT",
            "privacy_consent": "yes",
            "work_permit_page_1": image_upload("white", "pretends-to-be-jpeg.jpg"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "圖片內容與副檔名或檔案類型不一致".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(StaffDocument)) == 0


def test_replacement_keeps_document_history_for_admin_download(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client, "white", "first.png")
    with app.app_context():
        first_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))
    client.post(
        f"/student/documents/{first_id}/confirm",
        data={"residence_id": "A111111111", "residence_expiry": "2026-10-31"},
    )
    admin_review(client, first_id)
    logout(client)
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client, "lightblue", "second.png")
    with app.app_context():
        second_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT, StaffDocument.id != first_id))
    client.post(
        f"/student/documents/{second_id}/confirm",
        data={"residence_id": "A222222222", "residence_expiry": "2027-10-31"},
    )
    admin_review(client, second_id)
    with app.app_context():
        first_set = db.session.get(StaffDocument, first_id).document_set_id
        second_set = db.session.get(StaffDocument, second_id).document_set_id
        assert all(item.status == DocumentStatus.REPLACED for item in db.session.scalars(db.select(StaffDocument).where(StaffDocument.document_set_id == first_set)))
        assert all(item.status == DocumentStatus.CONFIRMED for item in db.session.scalars(db.select(StaffDocument).where(StaffDocument.document_set_id == second_set)))

    logout(client)
    login(client)
    assert client.get(f"/admin/documents/{first_id}/download").status_code == 200
    assert client.get(f"/admin/documents/{second_id}/download").status_code == 200


def test_admin_rejection_requires_reason_and_student_can_correct(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        document_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))
    client.post(
        f"/student/documents/{document_id}/confirm",
        data={"residence_id": "A123456789", "residence_expiry": "2026-12-31"},
    )

    admin_review(client, document_id, "REJECT")
    with app.app_context():
        assert db.session.get(StaffDocument, document_id).status == DocumentStatus.PENDING_ADMIN

    client.post(
        f"/admin/documents/{document_id}/review",
        data={"decision": "REJECT", "review_reason": "影像反光，請重新核對資料"},
    )
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        assert document.status == DocumentStatus.REJECTED
        assert "反光" in document.rejection_reason
        assert document.staff.residence_id is None

    logout(client)
    login(client, "student-test", "StudentTest!2026")
    page = client.get("/student/profile")
    assert "影像反光".encode("utf-8") in page.data
    client.post(
        f"/student/documents/{document_id}/confirm",
        data={"residence_id": "A987654321", "residence_expiry": "2027-12-31"},
    )
    with app.app_context():
        assert db.session.get(StaffDocument, document_id).status == DocumentStatus.PENDING_ADMIN


def test_admin_must_verify_residence_fields_before_approval(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        document_id = db.session.scalar(db.select(StaffDocument.id).where(StaffDocument.page_kind == DocumentPageKind.RESIDENCE_FRONT))
    client.post(
        f"/student/documents/{document_id}/confirm",
        data={"residence_id": "A135792468", "residence_expiry": "2027-06-30"},
    )
    logout(client)
    login(client)
    review_page = client.get("/admin/documents")
    assert b"A135792468" in review_page.data
    assert b"2027-06-30" in review_page.data
    assert b' name="fields_confirmed"' in review_page.data

    response = client.post(
        f"/admin/documents/{document_id}/review",
        data={"decision": "APPROVE"},
        follow_redirects=True,
    )
    assert "核准前必須確認證件影像與送審欄位一致。".encode("utf-8") in response.data
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        assert document.status == DocumentStatus.PENDING_ADMIN
        assert document.staff.residence_id is None

    client.post(
        f"/admin/documents/{document_id}/review",
        data={"decision": "APPROVE", "fields_confirmed": "yes"},
    )
    with app.app_context():
        document = db.session.get(StaffDocument, document_id)
        assert document.status == DocumentStatus.CONFIRMED
        assert document.staff.residence_id == "A135792468"


def test_residence_requires_both_sides(client, app):
    login(client, "student-test", "StudentTest!2026")
    response = client.post(
        "/student/documents",
        data={
            "document_type": "RESIDENCE_PERMIT",
            "privacy_consent": "yes",
            "residence_front": image_upload("white", "front.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "居留證必須同時上傳正面與反面".encode() in response.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(StaffDocument)) == 0


def test_admin_can_download_complete_document_set_as_zip(client, app):
    login(client, "student-test", "StudentTest!2026")
    upload_residence(client)
    with app.app_context():
        set_id = db.session.scalar(db.select(StaffDocument.document_set_id))
    logout(client)
    login(client)
    response = client.get(f"/admin/document-sets/{set_id}/download")
    assert response.status_code == 200
    assert response.data.startswith(b"PK")
    assert "attachment" in response.headers["Content-Disposition"]
