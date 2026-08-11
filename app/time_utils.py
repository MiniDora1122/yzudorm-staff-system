from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import current_app


def local_today() -> date:
    timezone = ZoneInfo(current_app.config["APP_TIMEZONE"])
    return datetime.now(timezone).date()


def local_now() -> datetime:
    timezone = ZoneInfo(current_app.config["APP_TIMEZONE"])
    return datetime.now(timezone)
