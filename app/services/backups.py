from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from flask import current_app

from deployment.create_portable_backup import create_backup

from ..extensions import db
from ..models import BackupRun, utc_now
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
    if not current_app.config.get("AUTOMATIC_BACKUP_ENABLED"):
        return None
    now = local_now()
    due_time = (
        int(current_app.config["AUTOMATIC_BACKUP_HOUR"]),
        int(current_app.config["AUTOMATIC_BACKUP_MINUTE"]),
    )
    if (now.hour, now.minute) < due_time:
        return None
    last_success = db.session.scalar(
        db.select(BackupRun)
        .where(BackupRun.status == "SUCCESS")
        .order_by(BackupRun.finished_at.desc())
        .limit(1)
    )
    if last_success and last_success.finished_at:
        finished = last_success.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=now.tzinfo)
        if finished.astimezone(now.tzinfo).date() == now.date():
            return None
    return run_backup()


def register_backup_commands(app) -> None:
    import click

    @app.cli.command("backup-run")
    @click.option("--actor-user-id", type=int)
    def backup_run_command(actor_user_id: int | None):
        run = run_backup(actor_user_id=actor_user_id)
        click.echo(f"Backup {run.status}: {run.filename or run.validation_message}")
