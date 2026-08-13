from __future__ import annotations

from datetime import timedelta

from ..extensions import db
from ..models import AuditLog, DocumentRetentionPolicy, DocumentStatus, StaffDocument, utc_now
from ..time_utils import local_now


def get_retention_policy() -> DocumentRetentionPolicy | None:
    return db.session.get(DocumentRetentionPolicy, 1)


def configured_retention_days() -> int:
    from flask import current_app

    policy = get_retention_policy()
    return policy.retention_days if policy else int(current_app.config["DOCUMENT_DEFAULT_RETENTION_DAYS"])


def retention_deadline(base_time, days: int | None = None):
    days = configured_retention_days() if days is None else days
    return base_time + timedelta(days=days) if days > 0 else None


def recalculate_retention_deadlines(policy: DocumentRetentionPolicy) -> None:
    documents = db.session.scalars(
        db.select(StaffDocument).where(
            StaffDocument.status.in_(
                [DocumentStatus.NEEDS_REVIEW, DocumentStatus.REJECTED, DocumentStatus.REPLACED, DocumentStatus.FAILED]
            )
        )
    ).all()
    for document in documents:
        document.retention_until = retention_deadline(
            document.replaced_at or document.uploaded_at, policy.retention_days
        )


def save_retention_policy(*, retention_days: int, cleanup_hour: int, cleanup_minute: int, actor_user_id: int):
    policy = get_retention_policy()
    if policy is None:
        policy = DocumentRetentionPolicy(id=1, updated_by=actor_user_id)
        db.session.add(policy)
    policy.retention_days = retention_days
    policy.cleanup_hour = cleanup_hour
    policy.cleanup_minute = cleanup_minute
    policy.updated_by = actor_user_id
    recalculate_retention_deadlines(policy)
    db.session.flush()
    db.session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action="DOCUMENT_RETENTION_POLICY_UPDATED",
            entity_type="DocumentRetentionPolicy",
            entity_id=policy.id,
            safe_summary=f"文件保存政策更新為 {retention_days} 天，每日 {cleanup_hour:02d}:{cleanup_minute:02d} 清理",
        )
    )
    db.session.commit()
    return policy


def cleanup_expired_documents(*, actor_user_id: int) -> list[int]:
    """Delete encrypted bytes while retaining safe metadata and an audit trail."""
    from .documents import document_path

    now = utc_now()
    documents = db.session.scalars(
        db.select(StaffDocument).where(
            StaffDocument.status.in_(
                [DocumentStatus.NEEDS_REVIEW, DocumentStatus.REJECTED, DocumentStatus.REPLACED, DocumentStatus.FAILED]
            ),
            StaffDocument.retention_until.is_not(None),
            StaffDocument.retention_until <= now,
        )
    ).all()
    deleted_ids = []
    for document in documents:
        path = document_path(document)
        if path is not None:
            path.unlink(missing_ok=True)
        document.storage_key = None
        document.status = DocumentStatus.DELETED
        document.deleted_at = now
        deleted_ids.append(document.id)
        db.session.add(
            AuditLog(
                actor_user_id=actor_user_id,
                action="DOCUMENT_PURGED_BY_RETENTION",
                entity_type="StaffDocument",
                entity_id=document.id,
                safe_summary=f"依保存政策清除 {document.document_type.value} 文件影像",
            )
        )
    policy = get_retention_policy()
    if policy is not None:
        policy.last_cleanup_at = now
    db.session.commit()
    return deleted_ids


def run_cleanup_if_due() -> int:
    policy = get_retention_policy()
    if policy is None:
        return 0
    now = local_now()
    if (now.hour, now.minute) < (policy.cleanup_hour, policy.cleanup_minute):
        return 0
    if policy.last_cleanup_at is not None:
        last = policy.last_cleanup_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=now.tzinfo)
        if last.astimezone(now.tzinfo).date() == now.date():
            return 0
    return len(cleanup_expired_documents(actor_user_id=policy.updated_by))


def _scheduled_tick(app) -> None:
    """Backward-compatible entry point; the unified maintenance scheduler owns execution."""
    with app.app_context():
        run_cleanup_if_due()


def register_retention_commands(app) -> None:
    import click

    @app.cli.command("documents-cleanup")
    @click.option("--actor-user-id", type=int, required=True, help="執行稽核所記錄的管理員 User ID。")
    def documents_cleanup(actor_user_id: int):
        deleted = cleanup_expired_documents(actor_user_id=actor_user_id)
        click.echo(f"Purged {len(deleted)} expired document(s).")
