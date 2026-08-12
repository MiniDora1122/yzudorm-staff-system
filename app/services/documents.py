from __future__ import annotations

import hashlib
import io
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import (
    DocumentPageKind,
    DocumentStatus,
    DocumentType,
    DocumentDraft,
    StaffDocument,
    StaffProfile,
    utc_now,
)
from ..time_utils import local_today
from .requests import add_audit
from .requests import WorkflowError
from .retention import retention_deadline


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
EXTENSION_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
MIME_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
PRIVACY_NOTICE_VERSION = "2026-08-v2"

PAGE_KINDS = {
    DocumentType.RESIDENCE_PERMIT: (
        DocumentPageKind.RESIDENCE_FRONT,
        DocumentPageKind.RESIDENCE_BACK,
    ),
    DocumentType.WORK_PERMIT: (
        DocumentPageKind.WORK_PERMIT_PAGE_1,
        DocumentPageKind.WORK_PERMIT_PAGE_2,
    ),
}
REQUIRED_PAGE_KINDS = {
    DocumentType.RESIDENCE_PERMIT: frozenset(PAGE_KINDS[DocumentType.RESIDENCE_PERMIT]),
    DocumentType.WORK_PERMIT: frozenset({DocumentPageKind.WORK_PERMIT_PAGE_1}),
}
PAGE_LABELS = {
    DocumentPageKind.RESIDENCE_FRONT: "正面",
    DocumentPageKind.RESIDENCE_BACK: "反面",
    DocumentPageKind.WORK_PERMIT_PAGE_1: "第 1 頁",
    DocumentPageKind.WORK_PERMIT_PAGE_2: "第 2 頁",
}


class DocumentError(WorkflowError):
    pass


def _fernet() -> Fernet:
    configured = current_app.config.get("DOCUMENT_ENCRYPTION_KEY")
    if not configured:
        raise RuntimeError("文件加密金鑰尚未初始化。")
    key = configured.encode("ascii") if isinstance(configured, str) else configured
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("DOCUMENT_ENCRYPTION_KEY 不是有效的 Fernet 金鑰。") from exc


def _storage_root() -> Path:
    root = Path(current_app.config["DOCUMENT_STORAGE_DIR"])
    if not root.is_absolute():
        root = Path(current_app.root_path).parent / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _document_path(stored_path: str) -> Path:
    root = _storage_root()
    path = (root / stored_path).resolve()
    if root != path and root not in path.parents:
        raise DocumentError("INVALID_STORAGE_PATH", "文件儲存路徑錯誤。")
    return path


def _normalize_image(upload: FileStorage) -> tuple[bytes, int, int, str]:
    original_name = Path(upload.filename or "").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentError("INVALID_EXTENSION", "只接受 JPG、PNG 或 WEBP 圖片。")
    declared_type = (upload.mimetype or "").lower()
    if declared_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise DocumentError("INVALID_MIME", "檔案類型不是允許的圖片格式。")

    maximum = int(current_app.config["MAX_DOCUMENT_FILE_BYTES"])
    raw = upload.stream.read(maximum + 1)
    if not raw:
        raise DocumentError("EMPTY_FILE", "上傳檔案是空的。")
    if len(raw) > maximum:
        raise DocumentError("FILE_TOO_LARGE", "圖片超過允許的檔案大小。")

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            detected_format = probe.format
            probe.verify()
        if detected_format not in ALLOWED_FORMATS:
            raise DocumentError("INVALID_IMAGE", "無法辨識為允許的圖片格式。")
        if detected_format != EXTENSION_FORMATS[extension] or detected_format != MIME_FORMATS[declared_type]:
            raise DocumentError("FORMAT_MISMATCH", "圖片內容與副檔名或檔案類型不一致。")
        with Image.open(io.BytesIO(raw)) as opened:
            width, height = opened.size
            if width < 320 or height < 200:
                raise DocumentError("IMAGE_TOO_SMALL", "圖片解析度過低，請重新拍攝清楚的證件照片。")
            if width * height > int(current_app.config["DOCUMENT_MAX_PIXELS"]):
                raise DocumentError("IMAGE_TOO_LARGE", "圖片像素過大，請縮小後再上傳。")
            image = ImageOps.exif_transpose(opened)
            width, height = image.size
            normalized = image.convert("RGB")
            output = io.BytesIO()
            # 統一重新編碼為 JPEG，可移除 EXIF／定位資訊與附加內容。
            normalized.save(output, format="JPEG", quality=92, optimize=True)
    except DocumentError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise DocumentError("INVALID_IMAGE", "圖片內容損毀或格式不合法。") from exc
    return output.getvalue(), width, height, original_name


def upload_document_set(
    *,
    profile: StaffProfile,
    document_type: DocumentType,
    uploads: dict[DocumentPageKind, FileStorage | None],
    actor_user_id: int,
) -> list[StaffDocument]:
    provided = {
        kind: upload
        for kind, upload in uploads.items()
        if upload is not None and bool(Path(upload.filename or "").name)
    }
    allowed = set(PAGE_KINDS[document_type])
    required = REQUIRED_PAGE_KINDS[document_type]
    if not required.issubset(provided) or not set(provided).issubset(allowed):
        if document_type == DocumentType.RESIDENCE_PERMIT:
            message = "居留證必須同時上傳正面與反面。"
        else:
            message = "工作證必須上傳第 1 頁；如有第 2 頁請一併上傳。"
        raise DocumentError("INCOMPLETE_DOCUMENT_SET", message)

    prepared = []
    for page_kind in PAGE_KINDS[document_type]:
        upload = provided.get(page_kind)
        if upload is None:
            continue
        normalized, width, height, original_name = _normalize_image(upload)
        checksum = hashlib.sha256(normalized).hexdigest()
        duplicate = db.session.scalar(
            db.select(StaffDocument).where(
                StaffDocument.staff_id == profile.id,
                StaffDocument.document_type == document_type,
                StaffDocument.sha256 == checksum,
                StaffDocument.status != DocumentStatus.DELETED,
            )
        )
        if duplicate or any(item[4] == checksum for item in prepared):
            raise DocumentError("DUPLICATE_DOCUMENT", "這張證件影像已上傳過，請確認每一頁都不同。")
        prepared.append((page_kind, normalized, width, height, checksum, original_name))

    document_set_id = uuid4().hex
    uploaded_at = utc_now()
    stored_paths: list[Path] = []
    documents: list[StaffDocument] = []
    try:
        for page_kind, normalized, width, height, checksum, original_name in prepared:
            storage_key = f"{profile.id}/{uuid4().hex}.bin"
            final_path = _document_path(storage_key)
            final_path.parent.mkdir(parents=True, exist_ok=True)
            with final_path.open("xb") as output:
                output.write(_fernet().encrypt(normalized))
            stored_paths.append(final_path)
            document = StaffDocument(
                staff_id=profile.id,
                document_type=document_type,
                document_set_id=document_set_id,
                page_kind=page_kind,
                storage_key=storage_key,
                original_filename=(secure_filename(original_name) or "document-image")[:255],
                mime_type="image/jpeg",
                file_size=len(normalized),
                sha256=checksum,
                image_width=width,
                image_height=height,
                status=DocumentStatus.NEEDS_REVIEW,
                privacy_notice_version=PRIVACY_NOTICE_VERSION,
                uploaded_at=uploaded_at,
                retention_until=retention_deadline(uploaded_at),
            )
            db.session.add(document)
            documents.append(document)
        db.session.flush()
        db.session.add(DocumentDraft(document_id=documents[0].id))
        add_audit(
            actor_user_id,
            "DOCUMENT_SET_UPLOADED",
            "StaffDocument",
            documents[0].id,
            f"上傳{document_type.value}整組文件，共 {len(documents)} 頁，等待人工確認",
        )
        db.session.commit()
        return documents
    except Exception:
        db.session.rollback()
        for path in stored_paths:
            path.unlink(missing_ok=True)
        raise


def document_set_documents(document: StaffDocument) -> list[StaffDocument]:
    return db.session.scalars(
        db.select(StaffDocument)
        .where(StaffDocument.document_set_id == document.document_set_id)
        .order_by(StaffDocument.id)
    ).all()


def group_document_sets(documents: list[StaffDocument]) -> list[dict]:
    grouped: dict[str, list[StaffDocument]] = {}
    for document in documents:
        grouped.setdefault(document.document_set_id, []).append(document)
    result = []
    for pages in grouped.values():
        pages.sort(key=lambda item: PAGE_KINDS[item.document_type].index(item.page_kind))
        primary = pages[0]
        result.append(
            {
                "set_id": primary.document_set_id,
                "document_type": primary.document_type,
                "status": primary.status,
                "uploaded_at": primary.uploaded_at,
                "retention_until": primary.retention_until,
                "pages": pages,
                "primary": primary,
            }
        )
    result.sort(key=lambda item: item["uploaded_at"], reverse=True)
    return result


def read_document(document: StaffDocument) -> bytes:
    if not document.storage_key:
        raise DocumentError("DOCUMENT_UNAVAILABLE", "文件影像已不存在。")
    path = _document_path(document.storage_key)
    try:
        return _fernet().decrypt(path.read_bytes())
    except (FileNotFoundError, InvalidToken) as exc:
        raise DocumentError("DOCUMENT_UNAVAILABLE", "文件不存在或無法解密。") from exc


def confirm_document_set(
    *, document: StaffDocument, profile: StaffProfile, fields: dict, actor_user_id: int
) -> None:
    if document.staff_id != profile.id:
        raise DocumentError("NOT_OWNER", "只能確認自己的證件資料。")
    documents = document_set_documents(document)
    if not documents or any(item.staff_id != profile.id for item in documents):
        raise DocumentError("NOT_OWNER", "只能確認自己的證件資料。")
    if any(item.status not in {DocumentStatus.NEEDS_REVIEW, DocumentStatus.REJECTED} for item in documents):
        raise DocumentError("INVALID_STATUS", "這份文件已送審、確認或被取代。")
    required = REQUIRED_PAGE_KINDS[document.document_type]
    if not required.issubset({item.page_kind for item in documents}):
        raise DocumentError("INCOMPLETE_DOCUMENT_SET", "文件頁面不完整，請重新上傳整份證件。")

    draft = document.draft
    if draft is None:
        draft = DocumentDraft(document_id=document.id)
        db.session.add(draft)
    if document.document_type == DocumentType.RESIDENCE_PERMIT:
        number = str(fields.get("residence_id", "")).strip().upper()
        expiry = fields.get("residence_expiry")
        if len(number) < 4 or len(number) > 100 or expiry is None:
            raise DocumentError("INVALID_FIELDS", "請輸入居留證號與有效期限。")
        draft.residence_id = number
        draft.residence_expiry = expiry
        draft.work_permit_start = None
        draft.work_permit_expiry = None
        submitted_fields = ["residence_id", "residence_expiry"]
    else:
        start = fields.get("work_permit_start")
        expiry = fields.get("work_permit_expiry")
        if start is None or expiry is None:
            raise DocumentError("INVALID_FIELDS", "請輸入工作證開始日與截止日。")
        if start > expiry:
            raise DocumentError("INVALID_DATE_RANGE", "工作證開始日不可晚於截止日。")
        draft.residence_id = None
        draft.residence_expiry = None
        draft.work_permit_start = start
        draft.work_permit_expiry = expiry
        submitted_fields = ["work_permit_start", "work_permit_expiry"]

    for item in documents:
        item.status = DocumentStatus.PENDING_ADMIN
        item.rejection_reason = None
        item.reviewed_at = None
        item.reviewed_by = None
    add_audit(
        actor_user_id,
        "DOCUMENT_SUBMITTED_FOR_REVIEW",
        "StaffDocument",
        document.id,
        f"送出{document.document_type.value}整組文件供管理員審核，共 {len(documents)} 頁：{','.join(submitted_fields)}",
    )
    db.session.commit()


def review_document_set(
    *,
    document: StaffDocument,
    decision: str,
    reason: str | None,
    fields_confirmed: bool,
    actor_user_id: int,
) -> None:
    documents = document_set_documents(document)
    if not documents or any(item.status != DocumentStatus.PENDING_ADMIN for item in documents):
        raise DocumentError("INVALID_STATUS", "此文件目前不在待管理員審核狀態。")
    if decision not in {"APPROVE", "REJECT"}:
        raise DocumentError("INVALID_DECISION", "審核決定格式錯誤。")
    reason = (reason or "").strip()
    if len(reason) > 1000:
        raise DocumentError("INVALID_REASON", "審核原因不可超過 1000 字。")
    if decision == "REJECT" and not reason:
        raise DocumentError("REJECTION_REASON_REQUIRED", "退回時必須填寫不通過原因。")
    if decision == "APPROVE" and not fields_confirmed:
        raise DocumentError(
            "DOCUMENT_FIELDS_CONFIRMATION_REQUIRED",
            "核准前必須確認證件影像與送審欄位一致。 / Verify the document image and submitted fields before approval.",
        )

    now = utc_now()
    if decision == "REJECT":
        for item in documents:
            item.status = DocumentStatus.REJECTED
            item.reviewed_at = now
            item.reviewed_by = actor_user_id
            item.rejection_reason = reason
        add_audit(
            actor_user_id,
            "DOCUMENT_REJECTED",
            "StaffDocument",
            document.id,
            f"退回{document.document_type.value}整組文件，共 {len(documents)} 頁",
        )
        db.session.commit()
        return

    draft = document.draft
    if draft is None:
        raise DocumentError("MISSING_DRAFT", "找不到學生送審的證件欄位資料。")
    profile = document.staff
    if document.document_type == DocumentType.RESIDENCE_PERMIT:
        if not draft.residence_id or draft.residence_expiry is None:
            raise DocumentError("INVALID_FIELDS", "送審的居留證資料不完整。")
        profile.residence_id = draft.residence_id
        profile.residence_expiry = draft.residence_expiry
        confirmed_fields = ["residence_id", "residence_expiry"]
    else:
        if draft.work_permit_start is None or draft.work_permit_expiry is None:
            raise DocumentError("INVALID_FIELDS", "送審的工作證資料不完整。")
        profile.work_permit_start = draft.work_permit_start
        profile.work_permit_expiry = draft.work_permit_expiry
        confirmed_fields = ["work_permit_start", "work_permit_expiry"]

    previous = db.session.scalars(
        db.select(StaffDocument).where(
            StaffDocument.staff_id == profile.id,
            StaffDocument.document_type == document.document_type,
            StaffDocument.status == DocumentStatus.CONFIRMED,
            StaffDocument.document_set_id != document.document_set_id,
        )
    ).all()
    for item in previous:
        item.status = DocumentStatus.REPLACED
        item.replaced_at = now
        item.retention_until = retention_deadline(now)
    for item in documents:
        item.status = DocumentStatus.CONFIRMED
        item.confirmed_at = now
        item.confirmed_by = actor_user_id
        item.reviewed_at = now
        item.reviewed_by = actor_user_id
        item.rejection_reason = None
    draft.confirmed_at = now
    add_audit(
        actor_user_id,
        "DOCUMENT_APPROVED",
        "StaffDocument",
        document.id,
        f"管理員核准{document.document_type.value}整組文件，共 {len(documents)} 頁：{','.join(confirmed_fields)}",
    )
    db.session.commit()


def mask_identifier(value: str | None) -> str:
    if not value:
        return "—"
    return f"••••{value[-4:]}" if len(value) > 4 else "••••"


def expiry_state(value):
    """Return a centralized, template-friendly document expiry state."""
    if value is None:
        return {"code": "MISSING", "label": "尚未填寫", "class": "secondary", "days": None}

    days = (value - local_today()).days
    warning_days, critical_days = current_app.config.get("EXPIRY_WARNING_DAYS", (60, 30))
    if days < 0:
        return {"code": "EXPIRED", "label": f"已到期 {abs(days)} 天", "class": "danger", "days": days}
    if days <= critical_days:
        return {"code": "CRITICAL", "label": f"{days} 天內到期", "class": "danger", "days": days}
    if days <= warning_days:
        return {"code": "WARNING", "label": f"{days} 天內到期", "class": "warning", "days": days}
    return {"code": "VALID", "label": "有效", "class": "success", "days": days}


def document_path(document: StaffDocument) -> Path | None:
    if not document.storage_key:
        return None
    return _document_path(document.storage_key)


def delete_document_set(*, document: StaffDocument, profile: StaffProfile, actor_user_id: int) -> None:
    if document.staff_id != profile.id:
        raise DocumentError("NOT_OWNER", "只能移除自己的待確認文件。")
    documents = document_set_documents(document)
    if any(item.status not in {DocumentStatus.NEEDS_REVIEW, DocumentStatus.REJECTED} for item in documents):
        raise DocumentError("CONFIRMED_DOCUMENT_RETAINED", "已確認的證件需依校方保存政策保留，不能由學生自行刪除。")
    now = utc_now()
    for item in documents:
        path = document_path(item)
        if path is not None:
            path.unlink(missing_ok=True)
        item.storage_key = None
        item.status = DocumentStatus.DELETED
        item.deleted_at = now
    add_audit(
        actor_user_id,
        "DOCUMENT_DELETED",
        "StaffDocument",
        document.id,
        f"刪除未確認的{document.document_type.value}整組文件，共 {len(documents)} 頁",
    )
    db.session.commit()


masked = mask_identifier
