# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from ..extensions import db
from ..models import (
    AttendanceEvent,
    AttendanceReconciliation,
    AttendanceStatus,
    LeaveRequest,
    LeaveStatus,
    MonthlySettlement,
    RequirementStatus,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    StaffProfile,
    StaffingRequirement,
    SwapAdminStatus,
    SwapRequest,
    utc_now,
)
from .audit import add_audit
from .scheduling import month_bounds
from .compliance import missing_required_document_types


class PeriodError(ValueError):
    pass


def month_start_for(value: date) -> date:
    return value.replace(day=1)


def settlement_for(value: date) -> MonthlySettlement | None:
    return db.session.scalar(
        db.select(MonthlySettlement).where(MonthlySettlement.month_start == month_start_for(value))
    )


def ensure_month_open(value: date) -> None:
    settlement = settlement_for(value)
    if settlement and settlement.is_locked:
        raise PeriodError(
            f"{settlement.month_start:%Y-%m} 的排班已鎖定；請先由管理員解鎖。 / Scheduling is locked for this month."
        )


def period_summary(value: date) -> dict:
    start = month_start_for(value)
    _, end = month_bounds(start.strftime("%Y-%m"))
    rows = db.session.execute(
        db.select(Shift.publication_status, db.func.count(Shift.id))
        .where(
            Shift.shift_date >= start,
            Shift.shift_date < end,
            Shift.status != ShiftStatus.CANCELLED,
        )
        .group_by(Shift.publication_status)
    ).all()
    counts = {status.value: count for status, count in rows}
    pending_leave_count = db.session.scalar(
        db.select(db.func.count(LeaveRequest.id))
        .join(Shift, LeaveRequest.shift_id == Shift.id)
        .where(Shift.shift_date >= start, Shift.shift_date < end, LeaveRequest.status == LeaveStatus.PENDING)
    ) or 0
    pending_swap_count = db.session.scalar(
        db.select(db.func.count(SwapRequest.id))
        .join(Shift, SwapRequest.requester_shift_id == Shift.id)
        .where(
            Shift.shift_date >= start,
            Shift.shift_date < end,
            SwapRequest.admin_status.in_({SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING}),
        )
    ) or 0
    pending_attendance_count = db.session.scalar(
        db.select(db.func.count(db.distinct(AttendanceEvent.id)))
        .join(Shift, AttendanceEvent.shift_id == Shift.id)
        .where(
            Shift.shift_date >= start,
            Shift.shift_date < end,
            AttendanceEvent.status.in_({
                AttendanceStatus.LATE_REASON_REQUIRED,
                AttendanceStatus.LATE_PENDING_REVIEW,
                AttendanceStatus.MISSING_CLOCK_IN,
                AttendanceStatus.UNMATCHED,
            }),
        )
    ) or 0
    unreconciled_count = db.session.scalar(
        db.select(db.func.count(db.distinct(Shift.id)))
        .join(AttendanceEvent, AttendanceEvent.shift_id == Shift.id)
        .outerjoin(AttendanceReconciliation, AttendanceReconciliation.shift_id == Shift.id)
        .where(
            Shift.shift_date >= start,
            Shift.shift_date < end,
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
            AttendanceReconciliation.id.is_(None),
        )
    ) or 0
    open_requirement_count = db.session.scalar(
        db.select(db.func.count(StaffingRequirement.id)).where(
            StaffingRequirement.shift_date >= start,
            StaffingRequirement.shift_date < end,
            StaffingRequirement.status == RequirementStatus.OPEN,
        )
    ) or 0
    month_shifts = db.session.scalars(
        db.select(Shift).where(
            Shift.shift_date >= start, Shift.shift_date < end,
            Shift.status == ShiftStatus.SCHEDULED,
        )
    ).all()
    daily_hours = defaultdict(float)
    work_dates = defaultdict(set)
    scheduled_staff_ids = set()
    for shift in month_shifts:
        daily_hours[(shift.staff_id, shift.shift_date)] += float(shift.shift_type.default_hours)
        work_dates[shift.staff_id].add(shift.shift_date)
        scheduled_staff_ids.add(shift.staff_id)
    overtime_count = sum(hours > 8 for hours in daily_hours.values())
    consecutive_count = 0
    for dates in work_dates.values():
        run = 0
        previous = None
        violated = False
        for current in sorted(dates):
            run = run + 1 if previous and current == previous + timedelta(days=1) else 1
            previous = current
            if run > 5:
                violated = True
        consecutive_count += int(violated)
    profiles = db.session.scalars(
        db.select(StaffProfile).where(StaffProfile.id.in_(scheduled_staff_ids))
    ).all() if scheduled_staff_ids else []
    missing_document_count = sum(bool(missing_required_document_types(profile)) for profile in profiles)
    checklist = {
        "draft_shifts": counts.get(ShiftPublicationStatus.DRAFT.value, 0),
        "pending_leave": pending_leave_count,
        "pending_swap": pending_swap_count,
        "pending_attendance": pending_attendance_count,
        "unreconciled_attendance": unreconciled_count,
        "open_requirements": open_requirement_count,
        "overtime_shifts": overtime_count,
        "missing_documents": missing_document_count,
        "consecutive_work": consecutive_count,
    }
    settlement = settlement_for(start)
    return {
        "month_start": start,
        "draft_count": counts.get(ShiftPublicationStatus.DRAFT.value, 0),
        "published_count": counts.get(ShiftPublicationStatus.PUBLISHED.value, 0),
        "settlement": settlement,
        "checklist": checklist,
        "checklist_ready": not any(checklist.values()),
    }


def publish_month(value: date, *, actor_user_id: int) -> int:
    start = month_start_for(value)
    ensure_month_open(start)
    _, end = month_bounds(start.strftime("%Y-%m"))
    shifts = db.session.scalars(
        db.select(Shift).where(
            Shift.shift_date >= start,
            Shift.shift_date < end,
            Shift.status != ShiftStatus.CANCELLED,
            Shift.publication_status == ShiftPublicationStatus.DRAFT,
        )
    ).all()
    now = utc_now()
    for shift in shifts:
        shift.publication_status = ShiftPublicationStatus.PUBLISHED
        shift.published_at = now
        shift.published_by = actor_user_id
    if shifts:
        add_audit(
            actor_user_id,
            "SHIFT_MONTH_PUBLISHED",
            "Shift",
            0,
            f"發布 {start:%Y-%m} 共 {len(shifts)} 筆草稿排班",
        )
        db.session.commit()
    return len(shifts)


def close_month(value: date, *, actor_user_id: int) -> MonthlySettlement:
    start = month_start_for(value)
    summary = period_summary(start)
    if summary["settlement"] and summary["settlement"].is_locked:
        raise PeriodError("此月份排班已經鎖定。 / Scheduling is already locked for this month.")
    if summary["draft_count"]:
        raise PeriodError("仍有草稿排班；請先發布或刪除草稿後再鎖定。 / Draft shifts must be resolved before locking.")
    settlement = summary["settlement"] or MonthlySettlement(month_start=start)
    settlement.is_locked = True
    settlement.snapshot_json = None
    settlement.closed_by = actor_user_id
    settlement.closed_at = utc_now()
    settlement.unlocked_by = None
    settlement.unlocked_at = None
    settlement.unlock_reason = None
    db.session.add(settlement)
    db.session.flush()
    add_audit(actor_user_id, "SCHEDULE_MONTH_LOCKED", "MonthlySettlement", settlement.id, f"鎖定 {start:%Y-%m} 排班")
    db.session.commit()
    return settlement


def unlock_month(value: date, *, reason: str, actor_user_id: int) -> MonthlySettlement:
    start = month_start_for(value)
    settlement = settlement_for(start)
    if settlement is None or not settlement.is_locked:
        raise PeriodError("此月份目前沒有鎖定。 / This month is not locked.")
    reason = reason.strip()
    if len(reason) < 5:
        raise PeriodError("解鎖原因至少需要 5 個字元。 / Please provide an unlock reason.")
    settlement.is_locked = False
    settlement.unlocked_by = actor_user_id
    settlement.unlocked_at = utc_now()
    settlement.unlock_reason = reason[:500]
    add_audit(actor_user_id, "MONTH_UNLOCKED", "MonthlySettlement", settlement.id, f"解鎖 {start:%Y-%m}：{reason[:300]}")
    db.session.commit()
    return settlement
