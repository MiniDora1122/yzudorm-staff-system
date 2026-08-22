from __future__ import annotations

import hashlib
from datetime import timedelta, timezone
from pathlib import Path

from flask import current_app

from deployment.create_portable_backup import create_backup

from ..extensions import db
from ..models import BackupPolicy, BackupRun, BackupScheduleMode, utc_now
from ..time_utils import local_now
from .audit import add_audit


def backup_directory() -> Path:
    path = Path(current_app.config["AUTOMATIC_BACKUP_DIR"])
    if not path.is_absolute():
        path = Path(current_app.root_path).parent / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def latest_backup_run() -> BackupRun | None:
    return db.session.scalar(db.select(BackupRun).order_by(BackupRun.started_at.desc()).limit(1))


def backup_policy() -> dict:
    policy = db.session.get(BackupPolicy, 1)
    if policy:
        return {
            "enabled": policy.enabled,
            "mode": policy.mode,
            "interval_hours": policy.interval_hours,
            "daily_hour": policy.daily_hour,
            "daily_minute": policy.daily_minute,
        }
    return {
        "enabled": bool(current_app.config.get("AUTOMATIC_BACKUP_ENABLED")),
        "mode": BackupScheduleMode.DAILY,
        "interval_hours": 24,
        "daily_hour": int(current_app.config["AUTOMATIC_BACKUP_HOUR"]),
        "daily_minute": int(current_app.config["AUTOMATIC_BACKUP_MINUTE"]),
    }


def save_backup_policy(
    *,
    enabled: bool,
    mode: BackupScheduleMode,
    interval_hours: int,
    daily_hour: int,
    daily_minute: int,
    actor_user_id: int,
) -> BackupPolicy:
    policy = db.session.get(BackupPolicy, 1)
    if policy is None:
        policy = BackupPolicy(id=1, updated_by=actor_user_id)
        db.session.add(policy)
    policy.enabled = enabled
    policy.mode = mode
    policy.interval_hours = interval_hours
    policy.daily_hour = daily_hour
    policy.daily_minute = daily_minute
    policy.updated_by = actor_user_id
    schedule = (
        f"每隔 {interval_hours} 小時" if mode == BackupScheduleMode.INTERVAL
        else f"每日 {daily_hour:02d}:{daily_minute:02d}"
    )
    db.session.flush()
    add_audit(
        actor_user_id,
        "BACKUP_POLICY_UPDATED",
        "BackupPolicy",
        policy.id,
        f"自動備份{'啟用' if enabled else '停用'}，排程：{schedule}",
    )
    db.session.commit()
    return policy


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prune_old_backups() -> int:
    days = max(1, int(current_app.config["AUTOMATIC_BACKUP_RETENTION_DAYS"]))
    cutoff = local_now() - timedelta(days=days)
    removed = 0
    for path in backup_directory().glob("automatic-*.zip"):
        modified = path.stat().st_mtime
        if modified < cutoff.timestamp():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def run_backup(*, actor_user_id: int | None = None) -> BackupRun:
    started = utc_now()
    run = BackupRun(status="RUNNING", started_at=started)
    db.session.add(run)
    db.session.commit()
    destination = backup_directory() / f"automatic-{local_now():%Y%m%d-%H%M%S}.zip"
    try:
        manifest = create_backup(destination, allow_running=True)
        run.status = "SUCCESS"
        run.filename = destination.name
        run.size_bytes = destination.stat().st_size
        run.sha256 = _file_sha256(destination)
        run.validation_message = (
            f"Validated {manifest['file_count']} files and SQLite integrity_check returned ok."
        )
        prune_old_backups()
        add_audit(
            actor_user_id,
            "SYSTEM_BACKUP_SUCCEEDED",
            "BackupRun",
            run.id,
            f"完整備份建立並驗證成功：{destination.name}",
        )
    except Exception as exc:
        destination.unlink(missing_ok=True)
        run.status = "FAILED"
        run.validation_message = str(exc)[:500]
        add_audit(
            actor_user_id,
            "SYSTEM_BACKUP_FAILED",
            "BackupRun",
            run.id,
            "完整備份建立或驗證失敗",
        )
    run.finished_at = utc_now()
    db.session.commit()
    return run


def run_backup_if_due() -> BackupRun | None:
    policy = backup_policy()
    if not policy["enabled"]:
        return None
    now = local_now()
    latest = latest_backup_run()
    last_attempt = latest.started_at if latest else None
    if last_attempt and last_attempt.tzinfo is None:
        last_attempt = last_attempt.replace(tzinfo=timezone.utc)
    if last_attempt:
        last_attempt = last_attempt.astimezone(now.tzinfo)

    if policy["mode"] == BackupScheduleMode.INTERVAL:
        if last_attempt and now - last_attempt < timedelta(hours=policy["interval_hours"]):
            return None
    else:
        due_time = (policy["daily_hour"], policy["daily_minute"])
        if (now.hour, now.minute) < due_time:
            return None
        if last_attempt and last_attempt.date() == now.date():
            return None
    return run_backup()


def register_backup_commands(app) -> None:
    import click

    @app.cli.command("backup-run")
    @click.option("--actor-user-id", type=int)
    def backup_run_command(actor_user_id: int | None):
        run = run_backup(actor_user_id=actor_user_id)
        click.echo(f"Backup {run.status}: {run.filename or run.validation_message}")
