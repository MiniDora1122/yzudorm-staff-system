from __future__ import annotations

import atexit
import os
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from ..extensions import db
from .backups import run_backup_if_due
from .retention import run_cleanup_if_due


_scheduler: BackgroundScheduler | None = None
_lock_handle = None


def _try_process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _maintenance_tick(app) -> None:
    with app.app_context():
        try:
            if app.config.get("DOCUMENT_CLEANUP_SCHEDULER_ENABLED"):
                run_cleanup_if_due()
            run_backup_if_due()
        except Exception:
            # Individual backup failures are recorded by run_backup; this guard keeps
            # an unexpected maintenance error from terminating the scheduler thread.
            db.session.rollback()
            app.logger.exception("Scheduled maintenance tick failed")


def init_maintenance_scheduler(app) -> None:
    global _lock_handle, _scheduler
    if app.config.get("TESTING") or not app.config.get("MAINTENANCE_SCHEDULER_ENABLED"):
        return
    if _scheduler is not None and _scheduler.running:
        return
    lock_path = Path(app.instance_path) / "maintenance-scheduler.lock"
    _lock_handle = _try_process_lock(lock_path)
    if _lock_handle is None:
        app.logger.info("Maintenance scheduler is already owned by another process")
        return
    _scheduler = BackgroundScheduler(timezone=app.config["APP_TIMEZONE"], daemon=True)
    _scheduler.add_job(
        _maintenance_tick,
        "interval",
        seconds=max(60, int(app.config["MAINTENANCE_CHECK_SECONDS"])),
        args=[app],
        id="application-maintenance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False) if _scheduler and _scheduler.running else None)
