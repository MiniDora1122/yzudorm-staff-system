from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Shift, ShiftSeries, ShiftStatus, ShiftType, StaffProfile


class SchedulingConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def times_overlap(start_a: time, end_a: time, start_b: time, end_b: time) -> bool:
    return start_a < end_b and start_b < end_a


def shift_hours(shift_type: ShiftType) -> float:
    """Return the actual clock hours represented by a same-day shift type."""
    start = datetime.combine(date.min, shift_type.start_time)
    end = datetime.combine(date.min, shift_type.end_time)
    return (end - start).total_seconds() / 3600


def validate_shift_assignment(
    *,
    shift_date: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    exclude_shift_id: int | None = None,
    exclude_shift_ids: set[int] | None = None,
    allow_location_overlap: bool = False,
) -> None:
    proposed_hours = shift_hours(shift_type)
    if proposed_hours <= 0 or proposed_hours > 8:
        raise SchedulingConflict(
            "SHIFT_EXCEEDS_DAILY_LIMIT",
            "單一排班不得超過 8 小時。 / A single shift cannot exceed 8 hours.",
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


def create_shift(
    *,
    shift_date: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    actor_id: int,
    allow_location_overlap: bool = False,
    series_id: int | None = None,
    commit: bool = True,
) -> Shift:
    validate_shift_assignment(
        shift_date=shift_date,
        shift_type=shift_type,
        staff=staff,
        allow_location_overlap=allow_location_overlap,
    )
    shift = Shift(
        shift_date=shift_date,
        shift_type_id=shift_type.id,
        staff_id=staff.id,
        status=ShiftStatus.SCHEDULED,
        created_by=actor_id,
        series_id=series_id,
    )
    db.session.add(shift)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return shift


def create_weekly_shift_series(
    *,
    starts_on: date,
    ends_on: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    actor_id: int,
    allow_location_overlap: bool = False,
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
            series_id=series.id,
            commit=False,
        )
        for shift_date in dates
    ]
    db.session.flush()
    return created


def update_shift(
    shift: Shift,
    *,
    shift_date: date,
    shift_type: ShiftType,
    staff: StaffProfile,
    allow_location_overlap: bool = False,
) -> Shift:
    validate_shift_assignment(
        shift_date=shift_date,
        shift_type=shift_type,
        staff=staff,
        exclude_shift_id=shift.id,
        allow_location_overlap=allow_location_overlap,
    )
    shift.shift_date = shift_date
    shift.shift_type = shift_type
    shift.staff = staff
    shift.status = ShiftStatus.SCHEDULED
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
    return {
        "id": str(shift.id),
        "title": title,
        "start": f"{shift.shift_date.isoformat()}T{shift_type.start_time:%H:%M:%S}",
        "end": f"{shift.shift_date.isoformat()}T{shift_type.end_time:%H:%M:%S}",
        "backgroundColor": "#dc3545" if is_vacancy else location.color,
        "borderColor": "#dc3545" if is_vacancy else location.color,
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
            "isVacancy": is_vacancy,
            "seriesId": shift.series_id,
            "seriesStartsOn": shift.series.starts_on.isoformat() if shift.series else None,
            "seriesEndsOn": shift.series.ends_on.isoformat() if shift.series else None,
        },
    }


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
