from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import AuditLog, BackupPolicy, BackupRun, BackupScheduleMode, User
from app.services import backups

from .conftest import login


def test_admin_can_choose_interval_or_daily_backup_schedule(client, app):
    login(client)
    response = client.post(
        "/admin/operations/backup-settings",
        data={
            "enabled": "1",
            "mode": "INTERVAL",
            "interval_hours": "6",
            "daily_time": "03:30",
            "month": "2026-08",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        policy = db.session.get(BackupPolicy, 1)
        assert policy.enabled is True
        assert policy.mode == BackupScheduleMode.INTERVAL
        assert policy.interval_hours == 6
        assert db.session.scalar(
            db.select(AuditLog).where(AuditLog.action == "BACKUP_POLICY_UPDATED")
        )

    page = client.get("/admin/operations?month=2026-08")
    assert b'name="mode"' in page.data
    assert b'name="interval_hours"' in page.data
    assert b'name="daily_time"' in page.data
    assert "每隔 6 小時".encode("utf-8") in page.data


def test_backup_failure_waits_until_next_configured_interval(app, monkeypatch):
    taipei = ZoneInfo("Asia/Taipei")
    now = datetime(2026, 8, 23, 12, 0, tzinfo=taipei)
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        db.session.add(
            BackupPolicy(
                id=1,
                enabled=True,
                mode=BackupScheduleMode.INTERVAL,
                interval_hours=6,
                daily_hour=2,
                daily_minute=0,
                updated_by=admin.id,
            )
        )
        db.session.add(
            BackupRun(
                status="FAILED",
                started_at=(now - timedelta(hours=1)).astimezone(timezone.utc),
                finished_at=(now - timedelta(hours=1)).astimezone(timezone.utc),
            )
        )
        db.session.commit()
        monkeypatch.setattr(backups, "local_now", lambda: now)
        assert backups.run_backup_if_due() is None

        expected = object()
        monkeypatch.setattr(backups, "local_now", lambda: now + timedelta(hours=5, minutes=1))
        monkeypatch.setattr(backups, "run_backup", lambda: expected)
        assert backups.run_backup_if_due() is expected


def test_failed_daily_backup_is_not_retried_every_five_minutes(app, monkeypatch):
    taipei = ZoneInfo("Asia/Taipei")
    now = datetime(2026, 8, 23, 2, 5, tzinfo=taipei)
    with app.app_context():
        admin = db.session.scalar(db.select(User).where(User.username == "admin-test"))
        db.session.add(
            BackupPolicy(
                id=1,
                enabled=True,
                mode=BackupScheduleMode.DAILY,
                interval_hours=24,
                daily_hour=2,
                daily_minute=0,
                updated_by=admin.id,
            )
        )
        db.session.add(
            BackupRun(
                status="FAILED",
                started_at=(now - timedelta(minutes=5)).astimezone(timezone.utc),
                finished_at=(now - timedelta(minutes=5)).astimezone(timezone.utc),
            )
        )
        db.session.commit()
        monkeypatch.setattr(backups, "local_now", lambda: now)
        assert backups.run_backup_if_due() is None
