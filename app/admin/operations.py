from __future__ import annotations

from datetime import date, time

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from . import bp
from ..decorators import role_required
from ..extensions import db
from ..models import BackupRun, BackupScheduleMode, Role
from ..services.backups import (
    backup_directory,
    backup_policy,
    latest_backup_run,
    run_backup,
    save_backup_policy,
)
from ..services.periods import PeriodError, close_month, period_summary, publish_month, unlock_month
from ..services.scheduling import month_bounds
from ..time_utils import local_today


def _selected_month() -> date:
    value = request.values.get("month", local_today().strftime("%Y-%m"))
    return month_bounds(value)[0]


@bp.get("/operations")
@role_required(Role.ADMIN)
def operations_page():
    month = _selected_month()
    summary = period_summary(month)
    runs = db.session.scalars(
        db.select(BackupRun).order_by(BackupRun.started_at.desc()).limit(30)
    ).all()
    policy = backup_policy()
    return render_template(
        "admin/operations.html",
        selected_month=month.strftime("%Y-%m"),
        period=summary,
        backup_runs=runs,
        latest_backup=latest_backup_run(),
        backup_dir=backup_directory(),
        backup_policy=policy,
        automatic_backup_enabled=policy["enabled"],
        backup_retention_days=current_app.config["AUTOMATIC_BACKUP_RETENTION_DAYS"],
    )


@bp.post("/operations/publish")
@role_required(Role.ADMIN)
def publish_period():
    month = _selected_month()
    try:
        count = publish_month(month, actor_user_id=current_user.id)
        flash(f"已發布 {month:%Y-%m} 的 {count} 筆草稿排班。 / Published {count} draft shifts.", "success")
    except PeriodError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("admin.operations_page", month=month.strftime("%Y-%m")))


@bp.post("/operations/close")
@role_required(Role.ADMIN)
def close_period():
    month = _selected_month()
    try:
        close_month(month, actor_user_id=current_user.id)
        flash(f"{month:%Y-%m} 的排班已鎖定；薪資試算仍會獨立顯示。 / Scheduling locked.", "success")
    except PeriodError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("admin.operations_page", month=month.strftime("%Y-%m")))


@bp.post("/operations/unlock")
@role_required(Role.ADMIN)
def unlock_period():
    month = _selected_month()
    try:
        unlock_month(
            month,
            reason=request.form.get("reason", ""),
            actor_user_id=current_user.id,
        )
        flash(f"{month:%Y-%m} 已解鎖；操作原因已寫入稽核紀錄。 / Month unlocked.", "warning")
    except PeriodError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("admin.operations_page", month=month.strftime("%Y-%m")))


@bp.post("/operations/backup")
@role_required(Role.ADMIN)
def run_backup_now():
    run = run_backup(actor_user_id=current_user.id)
    if run.status == "SUCCESS":
        flash("完整備份已建立，manifest 雜湊與 SQLite 完整性檢查均通過。 / Backup verified.", "success")
    else:
        flash(f"備份失敗：{run.validation_message}", "danger")
    return redirect(url_for("admin.operations_page", month=request.form.get("month")))


@bp.post("/operations/backup-settings")
@role_required(Role.ADMIN)
def update_backup_settings():
    try:
        mode = BackupScheduleMode(request.form.get("mode", ""))
        interval_hours = int(request.form.get("interval_hours", ""))
        daily_time = time.fromisoformat(request.form.get("daily_time", ""))
        if not 1 <= interval_hours <= 168:
            raise ValueError
    except (TypeError, ValueError):
        flash("備份排程格式錯誤；間隔須為 1–168 小時。 / Invalid backup schedule.", "danger")
        return redirect(url_for("admin.operations_page", month=request.form.get("month")))
    save_backup_policy(
        enabled=request.form.get("enabled") == "1",
        mode=mode,
        interval_hours=interval_hours,
        daily_hour=daily_time.hour,
        daily_minute=daily_time.minute,
        actor_user_id=current_user.id,
    )
    flash("自動備份排程已更新。 / Automatic backup schedule updated.", "success")
    return redirect(url_for("admin.operations_page", month=request.form.get("month")))
