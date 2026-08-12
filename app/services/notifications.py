from __future__ import annotations

from dataclasses import dataclass

from flask import url_for
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import (
    DocumentStatus,
    DocumentType,
    LeaveRequest,
    LeaveStatus,
    Notification,
    NotificationStatus,
    Role,
    StaffDocument,
    StaffProfile,
    SwapAdminStatus,
    SwapPeerStatus,
    SwapRequest,
    User,
    utc_now,
)
from .documents import expiry_state, group_document_sets
from .compliance import missing_required_document_types


MANAGED_CATEGORIES = {
    "DOCUMENT_REVIEW",
    "DOCUMENT_REJECTED",
    "DOCUMENT_REQUIRED",
    "DOCUMENT_EXPIRY",
    "LEAVE_REVIEW",
    "SWAP_REVIEW",
    "SWAP_RESPONSE",
}
SEVERITY_ORDER = {"DANGER": 0, "WARNING": 1, "INFO": 2, "SUCCESS": 3}


@dataclass(frozen=True)
class NotificationSpec:
    key: str
    category: str
    severity: str
    title_zh: str
    title_en: str
    message_zh: str
    message_en: str
    target_url: str


def _document_label(document_type: DocumentType) -> tuple[str, str]:
    if document_type == DocumentType.RESIDENCE_PERMIT:
        return "居留證", "residence permit"
    return "工作證", "work permit"


def _scope_statement(*, role: Role | None = None, user_id: int | None = None):
    statement = db.select(Notification).where(Notification.category.in_(MANAGED_CATEGORIES))
    if role is not None:
        return statement.where(
            Notification.recipient_role == role,
            Notification.recipient_user_id.is_(None),
        )
    return statement.where(
        Notification.recipient_user_id == user_id,
        Notification.recipient_role.is_(None),
    )


def _sync_specs(
    specs: list[NotificationSpec], *, role: Role | None = None, user_id: int | None = None
) -> None:
    existing = {
        item.notification_key: item
        for item in db.session.scalars(_scope_statement(role=role, user_id=user_id)).all()
    }
    active_keys = {spec.key for spec in specs}
    now = utc_now()
    for spec in specs:
        notification = existing.get(spec.key)
        if notification is None:
            notification = Notification(
                notification_key=spec.key,
                recipient_role=role,
                recipient_user_id=user_id,
                category=spec.category,
                severity=spec.severity,
                title_zh=spec.title_zh,
                title_en=spec.title_en,
                message_zh=spec.message_zh,
                message_en=spec.message_en,
                target_url=spec.target_url,
            )
            db.session.add(notification)
            continue
        notification.category = spec.category
        notification.severity = spec.severity
        notification.title_zh = spec.title_zh
        notification.title_en = spec.title_en
        notification.message_zh = spec.message_zh
        notification.message_en = spec.message_en
        notification.target_url = spec.target_url
        notification.status = NotificationStatus.OPEN
        notification.completed_at = None

    for key, notification in existing.items():
        if key not in active_keys and notification.status == NotificationStatus.OPEN:
            notification.status = NotificationStatus.COMPLETED
            notification.completed_at = now
    if db.session.new or any(
        db.session.is_modified(item, include_collections=False) for item in existing.values()
    ):
        db.session.commit()


def sync_admin_notifications() -> None:
    specs: list[NotificationSpec] = []
    pending_documents = db.session.scalars(
        db.select(StaffDocument)
        .options(joinedload(StaffDocument.staff))
        .where(StaffDocument.status == DocumentStatus.PENDING_ADMIN)
        .order_by(StaffDocument.uploaded_at.desc())
    ).all()
    for group in group_document_sets(pending_documents):
        label_zh, label_en = _document_label(group["document_type"])
        profile = group["primary"].staff
        specs.append(
            NotificationSpec(
                key=f"ADMIN:DOCUMENT_REVIEW:{group['set_id']}",
                category="DOCUMENT_REVIEW",
                severity="INFO",
                title_zh=f"{profile.name}的{label_zh}等待審核",
                title_en=f"{profile.name}'s {label_en} awaits review",
                message_zh="請核對所有影像、證號與有效日期後再核准。",
                message_en="Verify all images, identification fields and validity dates before approval.",
                target_url=url_for("admin.documents_page"),
            )
        )

    active_profiles = db.session.scalars(
        db.select(StaffProfile)
        .join(StaffProfile.user)
        .where(User.is_active.is_(True))
        .order_by(StaffProfile.name)
    ).all()
    for profile in active_profiles:
        for kind, label_zh, label_en, expiry in (
            ("RESIDENCE", "居留證", "Residence permit", profile.residence_expiry),
            ("WORK", "工作證", "Work permit", profile.work_permit_expiry),
        ):
            state = expiry_state(expiry)
            if state["code"] not in {"EXPIRED", "CRITICAL", "WARNING"}:
                continue
            severity = "DANGER" if state["code"] in {"EXPIRED", "CRITICAL"} else "WARNING"
            specs.append(
                NotificationSpec(
                    key=f"ADMIN:DOCUMENT_EXPIRY:{profile.id}:{kind}:{expiry.isoformat()}",
                    category="DOCUMENT_EXPIRY",
                    severity=severity,
                    title_zh=f"{profile.name}的{label_zh}{state['label']}",
                    title_en=f"{profile.name}'s {label_en.lower()} requires attention",
                    message_zh=f"截止日：{expiry.isoformat()}。更新並核准新證件後才會完成此待辦。",
                    message_en=f"Expiry: {expiry.isoformat()}. This completes after an updated document is approved.",
                    target_url=url_for("admin.staff"),
                )
            )

    pending_leaves = db.session.scalars(
        db.select(LeaveRequest)
        .options(joinedload(LeaveRequest.staff), joinedload(LeaveRequest.shift))
        .where(LeaveRequest.status == LeaveStatus.PENDING)
        .order_by(LeaveRequest.created_at)
    ).all()
    for item in pending_leaves:
        specs.append(
            NotificationSpec(
                key=f"ADMIN:LEAVE_REVIEW:{item.id}",
                category="LEAVE_REVIEW",
                severity="WARNING",
                title_zh=f"{item.staff.name}的請假申請等待審核",
                title_en=f"Leave request from {item.staff.name} awaits review",
                message_zh=f"排班日期：{item.shift.shift_date.isoformat()}。",
                message_en=f"Shift date: {item.shift.shift_date.isoformat()}.",
                target_url=f"{url_for('admin.requests_page')}#leaveReview",
            )
        )

    pending_swaps = db.session.scalars(
        db.select(SwapRequest)
        .options(joinedload(SwapRequest.requester), joinedload(SwapRequest.requester_shift))
        .where(SwapRequest.admin_status == SwapAdminStatus.PENDING)
        .order_by(SwapRequest.created_at)
    ).all()
    for item in pending_swaps:
        specs.append(
            NotificationSpec(
                key=f"ADMIN:SWAP_REVIEW:{item.id}",
                category="SWAP_REVIEW",
                severity="INFO",
                title_zh=f"{item.requester.name}的換班申請等待審核",
                title_en=f"Swap request from {item.requester.name} awaits review",
                message_zh=f"原排班日期：{item.requester_shift.shift_date.isoformat()}。",
                message_en=f"Original shift date: {item.requester_shift.shift_date.isoformat()}.",
                target_url=f"{url_for('admin.requests_page')}#swapReview",
            )
        )
    _sync_specs(specs, role=Role.ADMIN)


def sync_student_notifications(user: User) -> None:
    profile = user.staff_profile
    specs: list[NotificationSpec] = []
    if profile is not None:
        for document_type in sorted(missing_required_document_types(profile), key=lambda item: item.value):
            label_zh, label_en = _document_label(document_type)
            specs.append(
                NotificationSpec(
                    key=f"STUDENT:{user.id}:DOCUMENT_REQUIRED:{document_type.value}",
                    category="DOCUMENT_REQUIRED",
                    severity="DANGER",
                    title_zh=f"必須完成{label_zh}",
                    title_en=f"Your {label_en} is required",
                    message_zh="請上傳完整文件、核對資料並等待管理員核准；完成前其他功能將暫停使用。",
                    message_en="Upload the complete document and obtain administrator approval before using other features.",
                    target_url=f"{url_for('student.profile')}#documentUploadSection",
                )
            )
        rejected = db.session.scalars(
            db.select(StaffDocument)
            .where(
                StaffDocument.staff_id == profile.id,
                StaffDocument.status == DocumentStatus.REJECTED,
            )
            .order_by(StaffDocument.reviewed_at.desc(), StaffDocument.uploaded_at.desc())
        ).all()
        for group in group_document_sets(rejected):
            label_zh, label_en = _document_label(group["document_type"])
            specs.append(
                NotificationSpec(
                    key=f"STUDENT:{user.id}:DOCUMENT_REJECTED:{group['set_id']}",
                    category="DOCUMENT_REJECTED",
                    severity="DANGER",
                    title_zh=f"{label_zh}已被退回，請修正",
                    title_en=f"Your {label_en} was returned for correction",
                    message_zh=group["primary"].rejection_reason or "請依管理員說明修正後重新送審。",
                    message_en="Correct the submitted document according to the administrator's reason and resubmit it.",
                    target_url=f"{url_for('student.profile')}#documentReviewSection",
                )
            )

        for kind, label_zh, label_en, expiry in (
            ("RESIDENCE", "居留證", "Residence permit", profile.residence_expiry),
            ("WORK", "工作證", "Work permit", profile.work_permit_expiry),
        ):
            state = expiry_state(expiry)
            if state["code"] not in {"EXPIRED", "CRITICAL", "WARNING"}:
                continue
            severity = "DANGER" if state["code"] in {"EXPIRED", "CRITICAL"} else "WARNING"
            specs.append(
                NotificationSpec(
                    key=f"STUDENT:{user.id}:DOCUMENT_EXPIRY:{kind}:{expiry.isoformat()}",
                    category="DOCUMENT_EXPIRY",
                    severity=severity,
                    title_zh=f"你的{label_zh}{state['label']}",
                    title_en=f"Your {label_en.lower()} requires attention",
                    message_zh=f"截止日：{expiry.isoformat()}。請上傳更新文件並完成管理員審核。",
                    message_en=f"Expiry: {expiry.isoformat()}. Upload an update and complete administrator review.",
                    target_url=f"{url_for('student.profile')}#documentReviewSection",
                )
            )

        invitations = db.session.scalars(
            db.select(SwapRequest)
            .options(joinedload(SwapRequest.requester), joinedload(SwapRequest.requester_shift))
            .where(
                SwapRequest.target_staff_id == profile.id,
                SwapRequest.peer_status == SwapPeerStatus.PENDING,
                SwapRequest.admin_status == SwapAdminStatus.NOT_READY,
            )
            .order_by(SwapRequest.created_at)
        ).all()
        for item in invitations:
            specs.append(
                NotificationSpec(
                    key=f"STUDENT:{user.id}:SWAP_RESPONSE:{item.id}",
                    category="SWAP_RESPONSE",
                    severity="INFO",
                    title_zh=f"{item.requester.name}邀請你換班",
                    title_en=f"Swap invitation from {item.requester.name}",
                    message_zh=f"原排班日期：{item.requester_shift.shift_date.isoformat()}，請接受或拒絕。",
                    message_en=f"Original shift date: {item.requester_shift.shift_date.isoformat()}. Accept or reject the invitation.",
                    target_url=url_for("student.requests_page"),
                )
            )
    _sync_specs(specs, user_id=user.id)


def notifications_for_user(user: User) -> tuple[list[Notification], list[Notification]]:
    statement = db.select(Notification)
    if user.role == Role.ADMIN:
        statement = statement.where(Notification.recipient_role == Role.ADMIN)
    else:
        statement = statement.where(Notification.recipient_user_id == user.id)
    items = db.session.scalars(statement.order_by(Notification.updated_at.desc())).all()
    open_items = sorted(
        (item for item in items if item.status == NotificationStatus.OPEN),
        key=lambda item: (SEVERITY_ORDER.get(item.severity, 9), -item.id),
    )
    completed_items = [item for item in items if item.status == NotificationStatus.COMPLETED]
    return open_items, completed_items


def open_notification_count(user: User) -> int:
    statement = db.select(db.func.count()).select_from(Notification).where(
        Notification.status == NotificationStatus.OPEN
    )
    if user.role == Role.ADMIN:
        statement = statement.where(Notification.recipient_role == Role.ADMIN)
    else:
        statement = statement.where(Notification.recipient_user_id == user.id)
    return db.session.scalar(statement) or 0
