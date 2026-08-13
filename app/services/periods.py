from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import (
    LeaveRequest,
    LeaveStatus,
    MonthlySettlement,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    ShiftType,
    StaffProfile,
    SwapAdminStatus,
    SwapRequest,
    utc_now,
)
from .payroll import get_payroll_setting
from .audit import add_audit
from .scheduling import month_bounds


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
            f"{settlement.month_start:%Y-%m} 已完成月份結算並鎖定；請先由管理員解鎖。 / This month is closed and locked."
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
    settlement = settlement_for(start)
    return {
        "month_start": start,
        "draft_count": counts.get(ShiftPublicationStatus.DRAFT.value, 0),
        "published_count": counts.get(ShiftPublicationStatus.PUBLISHED.value, 0),
        "settlement": settlement,
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


def _pending_workflow_count(start: date, end: date) -> int:
    leave_count = db.session.scalar(
        db.select(db.func.count())
        .select_from(LeaveRequest)
        .join(LeaveRequest.shift)
        .where(
            LeaveRequest.status == LeaveStatus.PENDING,
            Shift.shift_date >= start,
            Shift.shift_date < end,
        )
    ) or 0
    swap_count = db.session.scalar(
        db.select(db.func.count())
        .select_from(SwapRequest)
        .join(SwapRequest.requester_shift)
        .where(
            SwapRequest.admin_status.in_([SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING]),
            Shift.shift_date >= start,
            Shift.shift_date < end,
        )
    ) or 0
    return leave_count + swap_count


def _snapshot(start: date, end: date) -> str:
    shifts = db.session.scalars(
        db.select(Shift)
        .join(Shift.shift_type)
        .options(joinedload(Shift.staff), joinedload(Shift.shift_type).joinedload(ShiftType.work_location))
        .where(
            Shift.shift_date >= start,
            Shift.shift_date < end,
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
        )
        .order_by(Shift.staff_id, Shift.shift_date, ShiftType.start_time)
    ).all()
    by_staff: dict[int, dict] = defaultdict(lambda: {"hours": Decimal("0"), "locations": defaultdict(Decimal)})
    profiles: dict[int, StaffProfile] = {}
    for shift in shifts:
        profiles[shift.staff_id] = shift.staff
        hours = Decimal(str(shift.shift_type.default_hours))
        by_staff[shift.staff_id]["hours"] += hours
        by_staff[shift.staff_id]["locations"][shift.shift_type.work_location.code] += hours
    setting = get_payroll_setting(start)
    if setting is None:
        raise PeriodError(
            "此月份尚未設定薪資與保險費率，無法建立結算快照。 / Payroll settings are required before closing the month."
        )
    lines = []
    for staff_id, totals in sorted(by_staff.items(), key=lambda item: profiles[item[0]].student_number):
        profile = profiles[staff_id]
        wage = Decimal(str(profile.hourly_wage or setting.default_hourly_wage))
        lines.append(
            {
                "staff_id": staff_id,
                "student_number": profile.student_number,
                "name": profile.name,
                "hours": str(totals["hours"]),
                "hourly_wage": str(wage),
                "estimated_gross": str((totals["hours"] * wage).quantize(Decimal("1"))),
                "locations": {key: str(value) for key, value in sorted(totals["locations"].items())},
            }
        )
    return json.dumps({"month": start.strftime("%Y-%m"), "lines": lines}, ensure_ascii=False)


def close_month(value: date, *, actor_user_id: int) -> MonthlySettlement:
    start = month_start_for(value)
    summary = period_summary(start)
    if summary["settlement"] and summary["settlement"].is_locked:
        raise PeriodError("此月份已經結算。 / This month is already closed.")
    if summary["draft_count"]:
        raise PeriodError("仍有草稿排班；請先發布或刪除草稿後再結算。 / Draft shifts must be resolved first.")
    _, end = month_bounds(start.strftime("%Y-%m"))
    if _pending_workflow_count(start, end):
        raise PeriodError("仍有進行中的請假或換班，請處理完成後再結算。 / Pending workflows must be resolved first.")
    settlement = summary["settlement"] or MonthlySettlement(month_start=start)
    settlement.is_locked = True
    settlement.snapshot_json = _snapshot(start, end)
    settlement.closed_by = actor_user_id
    settlement.closed_at = utc_now()
    settlement.unlocked_by = None
    settlement.unlocked_at = None
    settlement.unlock_reason = None
    db.session.add(settlement)
    db.session.flush()
    add_audit(actor_user_id, "MONTH_CLOSED", "MonthlySettlement", settlement.id, f"結算並鎖定 {start:%Y-%m}")
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
