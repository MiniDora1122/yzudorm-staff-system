# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import wraps
from threading import RLock

from sqlalchemy import text
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import (
    Shift, ShiftPublicationStatus, ShiftSeries, ShiftStatus, ShiftType,
    StaffAvailability, StaffProfile, utc_now,
)
from .compliance import get_scheduling_policy, is_weekly_limit_exception_day, weekly_limit_applies


class SchedulingConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_schedule_write_lock = RLock()


def _acquire_database_schedule_lock() -> None:
    """Serialize schedule validation plus writes across application processes."""
    dialect = db.session.get_bind().dialect.name
    if dialect == "sqlite":
        db.session.execute(text(
            "INSERT OR IGNORE INTO schedule_write_locks (id, touched_at) VALUES (1, CURRENT_TIMESTAMP)"
        ))
        db.session.execute(text("UPDATE schedule_write_locks SET touched_at = CURRENT_TIMESTAMP WHERE id = 1"))
    else:
        db.session.execute(text("SELECT id FROM schedule_write_locks WHERE id = 1 FOR UPDATE"))


def schedule_serialized(func):
    """Serialize validation and writes in both threads and database processes."""
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _schedule_write_lock:
            _acquire_database_schedule_lock()
            db.session.expire_all()
            try:
                return func(*args, **kwargs)
            except Exception:
                db.session.rollback()
                raise

    return wrapped


def times_overlap(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    return start_a < end_b and start_b < end_a


def shift_hours(shift_type: ShiftType) -> float:
    """Return configured hours used consistently by weekly limits and payroll."""
    return float(shift_type.default_hours)


def validate_shift_assignment(
    *,
    shift_date: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    exclude_shift_id: int | None = None,
    exclude_shift_ids: set[int] | None = None,
    allow_location_overlap: bool = False,
    allow_availability_conflict: bool = False,
) -> None:
    from .periods import ensure_month_open

    ensure_month_open(shift_date)
    proposed_hours = shift_hours(shift_type)
    if proposed_hours <= 0 or proposed_hours > 8:
        raise SchedulingConflict(
            "SHIFT_EXCEEDS_DAILY_LIMIT",
            "單一排班不得超過 8 小時。 / A single shift cannot exceed 8 hours.",
        )

    availability = db.session.scalars(
        db.select(StaffAvailability).where(
            StaffAvailability.staff_id == staff.id,
            StaffAvailability.availability_date == shift_date,
        )
    ).all()
    overlapping_unavailable = any(
        not item.is_available
        and times_overlap(shift_type.start_time, shift_type.end_time, item.start_time, item.end_time)
        for item in availability
    )
    available_blocks = [item for item in availability if item.is_available]
    outside_available_blocks = bool(available_blocks) and not any(
        item.start_time <= shift_type.start_time and item.end_time >= shift_type.end_time
        for item in available_blocks
    )
    if not allow_availability_conflict and (overlapping_unavailable or outside_available_blocks):
        raise SchedulingConflict(
            "AVAILABILITY_CONFIRM_REQUIRED",
            f"{staff.name} 回報此時段不可排班或不在可排班時段內；管理員確認後仍可排班。 / Availability conflict; an administrator may override it.",
        )

    statement = (
        db.select(Shift)
        .options(joinedload(Shift.shift_type), joinedload(Shift.staff))
        .where(
            Shift.shift_date == shift_date,
            Shift.status == ShiftStatus.SCHEDULED,
        )
    )
    excluded = set(exclude_shift_ids or set())
    if exclude_shift_id is not None:
        excluded.add(exclude_shift_id)
    if excluded:
        statement = statement.where(Shift.id.not_in(excluded))

    existing_shifts = list(db.session.scalars(statement))
    staff_day_hours = proposed_hours + sum(
        shift_hours(existing.shift_type)
        for existing in existing_shifts
        if existing.staff_id == staff.id
    )
    if staff_day_hours > 8:
        raise SchedulingConflict(
            "DAILY_HOURS_LIMIT",
            f"{staff.name} 在 {shift_date} 的排班合計將超過 8 小時。 / Daily scheduled hours would exceed 8.",
        )

    range_start = shift_date - timedelta(days=5)
    range_end = shift_date + timedelta(days=5)
    workday_statement = db.select(Shift.shift_date).where(
        Shift.staff_id == staff.id,
        Shift.status == ShiftStatus.SCHEDULED,
        Shift.shift_date.between(range_start, range_end),
    )
    if excluded:
        workday_statement = workday_statement.where(Shift.id.not_in(excluded))
    workdays = set(db.session.scalars(workday_statement))
    workdays.add(shift_date)
    ordered_days = sorted(workdays)
    streak = 0
    previous_day = None
    for workday in ordered_days:
        streak = streak + 1 if previous_day and workday == previous_day + timedelta(days=1) else 1
        if streak >= 6:
            raise SchedulingConflict(
                "CONSECUTIVE_DAYS_LIMIT",
                f"{staff.name} 不得連續工作超過 5 天。 / More than 5 consecutive workdays is not allowed.",
            )
        previous_day = workday

    if weekly_limit_applies(staff, shift_date):
        policy = get_scheduling_policy()
        days_since_week_start = (shift_date.weekday() - policy.week_starts_on) % 7
        week_start = shift_date - timedelta(days=days_since_week_start)
        week_end = week_start + timedelta(days=6)
        week_statement = (
            db.select(Shift)
            .options(joinedload(Shift.shift_type))
            .where(
                Shift.staff_id == staff.id,
                Shift.status == ShiftStatus.SCHEDULED,
                Shift.shift_date.between(week_start, week_end),
            )
        )
        if excluded:
            week_statement = week_statement.where(Shift.id.not_in(excluded))
        counted_shifts = [
            existing
            for existing in db.session.scalars(week_statement)
            if not is_weekly_limit_exception_day(existing.shift_date)
        ]
        weekly_hours = proposed_hours + sum(shift_hours(existing.shift_type) for existing in counted_shifts)
        limit = float(policy.weekly_hour_limit)
        if weekly_hours > limit:
            raise SchedulingConflict(
                "FOREIGN_WEEKLY_HOURS_LIMIT",
                f"{staff.name} 在 {week_start}–{week_end} 的受限制排班將達 {weekly_hours:g} 小時，"
                f"超過每週 {limit:g} 小時上限。 / The weekly limit would be exceeded.",
            )

    for existing in existing_shifts:
        existing_type = existing.shift_type
        if existing.staff_id == staff.id and times_overlap(
            shift_type.start_time,
            shift_type.end_time,
            existing_type.start_time,
            existing_type.end_time,
        ):
            location = existing_type.work_location.name
            raise SchedulingConflict(
                "STAFF_TIME_OVERLAP",
                f"{staff.name} 在 {shift_date} {existing_type.start_time:%H:%M}–{existing_type.end_time:%H:%M} 已於{location}排班。",
            )
        if (
            not allow_location_overlap
            and existing_type.location_id == shift_type.location_id
            and times_overlap(
                shift_type.start_time,
                shift_type.end_time,
                existing_type.start_time,
                existing_type.end_time,
            )
        ):
            location = shift_type.work_location.name
            raise SchedulingConflict(
                "LOCATION_CONFIRM_REQUIRED",
                f"{shift_date} {location} {shift_type.start_time:%H:%M}–{shift_type.end_time:%H:%M} 與 {existing.staff.name} 的排班時段重疊。此地點同時段可安排多人，但需要管理員再次確認。",
            )


@schedule_serialized
def create_shift(
    *,
    shift_date: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    actor_id: int,
    allow_location_overlap: bool = False,
    allow_availability_conflict: bool = False,
    series_id: int | None = None,
    publication_status: ShiftPublicationStatus = ShiftPublicationStatus.PUBLISHED,
    commit: bool = True,
) -> Shift:
    validate_shift_assignment(
        shift_date=shift_date,
        shift_type=shift_type,
        staff=staff,
        allow_location_overlap=allow_location_overlap,
        allow_availability_conflict=allow_availability_conflict,
    )
    shift = Shift(
        shift_date=shift_date,
        shift_type_id=shift_type.id,
        staff_id=staff.id,
        status=ShiftStatus.SCHEDULED,
        created_by=actor_id,
        series_id=series_id,
        publication_status=publication_status,
        published_at=utc_now() if publication_status == ShiftPublicationStatus.PUBLISHED else None,
        published_by=actor_id if publication_status == ShiftPublicationStatus.PUBLISHED else None,
    )
    db.session.add(shift)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return shift


@schedule_serialized
def create_weekly_shift_series(
    *,
    starts_on: date,
    ends_on: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    actor_id: int,
    allow_location_overlap: bool = False,
    allow_availability_conflict: bool = False,
    publication_status: ShiftPublicationStatus = ShiftPublicationStatus.PUBLISHED,
) -> list[Shift]:
    if ends_on < starts_on:
        raise ValueError("截止日期不可早於開始日期。 / The end date cannot be before the start date.")
    if ends_on > starts_on + timedelta(days=730):
        raise ValueError("每週重複排班期間不可超過兩年。 / A recurring series cannot exceed two years.")
    dates = []
    current = starts_on
    while current <= ends_on:
        dates.append(current)
        current += timedelta(days=7)
    if len(dates) > 105:
        raise ValueError("每個重複系列最多 105 筆排班。 / Maximum 105 shifts per series.")

    series = ShiftSeries(
        staff_id=staff.id,
        shift_type_id=shift_type.id,
        starts_on=starts_on,
        ends_on=ends_on,
        weekday=starts_on.weekday(),
        created_by=actor_id,
    )
    db.session.add(series)
    db.session.flush()
    created = [
        create_shift(
            shift_date=shift_date,
            shift_type=shift_type,
            staff=staff,
            actor_id=actor_id,
            allow_location_overlap=allow_location_overlap,
            allow_availability_conflict=allow_availability_conflict,
            series_id=series.id,
            publication_status=publication_status,
            commit=False,
        )
        for shift_date in dates
    ]
    db.session.flush()
    return created


@schedule_serialized
def update_shift(
    shift: Shift,
    *,
    shift_date: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    allow_location_overlap: bool = False,
    allow_availability_conflict: bool = False,
    publication_status: ShiftPublicationStatus | None = None,
    actor_id: int | None = None,
) -> Shift:
    validate_shift_assignment(
        shift_date=shift_date,
        shift_type=shift_type,
        staff=staff,
        exclude_shift_id=shift.id,
        allow_location_overlap=allow_location_overlap,
        allow_availability_conflict=allow_availability_conflict,
    )
    shift.shift_date = shift_date
    shift.shift_type = shift_type
    shift.staff = staff
    shift.status = ShiftStatus.SCHEDULED
    if publication_status is not None:
        shift.publication_status = publication_status
        shift.published_at = utc_now() if publication_status == ShiftPublicationStatus.PUBLISHED else None
        shift.published_by = (actor_id or shift.created_by) if publication_status == ShiftPublicationStatus.PUBLISHED else None
    db.session.commit()
    return shift


def shift_to_event(shift: Shift, *, student_view: bool = False) -> dict:
    shift_type = shift.shift_type
    location = shift_type.work_location
    location_label = location.name
    time_label = f"{shift_type.start_time:%H:%M}–{shift_type.end_time:%H:%M}"
    if student_view:
        title = f"{shift_type.start_time:%H:%M} {location_label}｜{shift_type.name}"
    else:
        title = f"{shift.staff.name}｜{location_label} {shift_type.start_time:%H:%M}"

    is_vacancy = shift.status == ShiftStatus.ON_LEAVE
    background = "#dc3545" if is_vacancy else location.color
    return {
        "id": str(shift.id),
        "title": title,
        "start": f"{shift.shift_date.isoformat()}T{shift_type.start_time:%H:%M:%S}",
        "end": f"{shift.shift_date.isoformat()}T{shift_type.end_time:%H:%M:%S}",
        "backgroundColor": background,
        "borderColor": background,
        "textColor": contrast_text_color(background),
        "extendedProps": {
            "shiftDate": shift.shift_date.isoformat(),
            "shiftTypeId": shift_type.id,
            "shiftTypeName": shift_type.name,
            "shiftTypeNameEn": shift_type.name_en,
            "staffId": shift.staff_id,
            "staffName": shift.staff.name,
            "location": location.code,
            "locationId": location.id,
            "locationLabel": location_label,
            "locationLabelEn": location.name_en,
            "locationColor": location.color,
            "locationOrder": location.display_order,
            "startTime": shift_type.start_time.strftime("%H:%M"),
            "endTime": shift_type.end_time.strftime("%H:%M"),
            "timeLabel": time_label,
            "hours": float(shift_type.default_hours),
            "status": shift.status.value,
            "publicationStatus": shift.publication_status.value,
            "isDraft": shift.publication_status == ShiftPublicationStatus.DRAFT,
            "isVacancy": is_vacancy,
            "seriesId": shift.series_id,
            "seriesStartsOn": shift.series.starts_on.isoformat() if shift.series else None,
            "seriesEndsOn": shift.series.ends_on.isoformat() if shift.series else None,
        },
    }


def contrast_text_color(color: str) -> str:
    """Choose readable black or white text for a six-digit calendar color."""
    value = color.lstrip("#")
    if len(value) != 6:
        return "#ffffff"
    try:
        red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return "#ffffff"
    luminance = (red * 299 + green * 587 + blue * 114) / 1000
    return "#111827" if luminance >= 160 else "#ffffff"


def month_bounds(month_value: str) -> tuple[date, date]:
    try:
        year_text, month_text = month_value.split("-", maxsplit=1)
        start = date(int(year_text), int(month_text), 1)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("月份格式必須是 YYYY-MM。") from exc

    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    return start, end
