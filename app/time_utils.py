from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app


def local_today() -> date:
    timezone = ZoneInfo(current_app.config["APP_TIMEZONE"])
    return datetime.now(timezone).date()


def local_now() -> datetime:
    timezone = ZoneInfo(current_app.config["APP_TIMEZONE"])
    return datetime.now(timezone)


def format_local_datetime(value: datetime | None, pattern: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(current_app.config["APP_TIMEZONE"])).strftime(pattern)


def local_date_utc_bounds(value: date) -> tuple[datetime, datetime]:
    local_zone = ZoneInfo(current_app.config["APP_TIMEZONE"])
    start = datetime.combine(value, time.min, local_zone).astimezone(timezone.utc)
    return start, start + timedelta(days=1)
