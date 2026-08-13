from __future__ import annotations

from datetime import date

from ..extensions import db
from ..models import (
    MonthlySettlement,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    utc_now,
)
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
