# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import current_app

from ..extensions import db
from ..models import AttendanceDevice, BackupRun, utc_now


def _monitor_window_start(device: AttendanceDevice, now: datetime) -> datetime | None:
    current_minute = now.time().replace(second=0, microsecond=0)
    start, end = device.offline_monitor_start, device.offline_monitor_end
    if start <= end:
        return datetime.combine(now.date(), start, now.tzinfo) if now.weekday() in device.offline_weekdays and start <= current_minute <= end else None
    if now.weekday() in device.offline_weekdays and current_minute >= start:
        return datetime.combine(now.date(), start, now.tzinfo)
    previous = now.date() - timedelta(days=1)
    return datetime.combine(previous, start, now.tzinfo) if previous.weekday() in device.offline_weekdays and current_minute <= end else None


def offline_attendance_devices() -> list[AttendanceDevice]:
    now_utc = utc_now()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_local = now_utc.astimezone(ZoneInfo(current_app.config["APP_TIMEZONE"]))
    devices = db.session.scalars(
        db.select(AttendanceDevice).where(
            AttendanceDevice.is_active.is_(True),
            AttendanceDevice.enrolled_at.is_not(None),
            AttendanceDevice.offline_alert_enabled.is_(True),
        ).order_by(AttendanceDevice.name)
    ).all()
    result = []
    for device in devices:
        window_start = _monitor_window_start(device, now_local)
        if window_start is None:
            continue
        last = device.last_seen_at or device.enrolled_at
        if last and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        reference = max(last, window_start.astimezone(timezone.utc)) if last else window_start.astimezone(timezone.utc)
        if reference < now_utc - timedelta(minutes=device.offline_threshold_minutes):
            result.append(device)
    return result


def system_health_summary() -> dict:
    success = db.session.scalar(
        db.select(BackupRun).where(BackupRun.status == "SUCCESS").order_by(BackupRun.started_at.desc()).limit(1)
    )
    failure = db.session.scalar(
        db.select(BackupRun).where(BackupRun.status == "FAILED").order_by(BackupRun.started_at.desc()).limit(1)
    )
    database_size = None
    database = db.engine.url.database
    if db.engine.url.get_backend_name() == "sqlite" and database:
        path = Path(database)
        if path.is_file():
            database_size = path.stat().st_size
    return {
        "last_backup_success": success,
        "last_backup_failure": failure,
        "offline_devices": offline_attendance_devices(),
        "mac_change_count": db.session.scalar(
            db.select(db.func.count()).select_from(AttendanceDevice).where(
                AttendanceDevice.identity_changed_at.is_not(None)
            )
        ) or 0,
        "database_size": database_size,
        "web_transport_mode": current_app.config.get("WEB_TRANSPORT_MODE", "TRUSTED_HTTP"),
    }
