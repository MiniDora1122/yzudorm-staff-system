from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload

from . import bp
from ..decorators import role_required
from ..extensions import db
from ..models import AttendanceDevice, AttendanceEvent, AttendanceStatus, CardStatus, Role, Shift, StaffCard
from ..services.attendance import AttendanceError, submit_reason


@bp.get("/attendance")
@role_required(Role.STUDENT)
def attendance_page():
    profile = current_user.staff_profile
    events = []
    cards = []
    if profile:
        events = db.session.scalars(
            db.select(AttendanceEvent)
            .options(
                joinedload(AttendanceEvent.device).joinedload(AttendanceDevice.location),
                joinedload(AttendanceEvent.shift).joinedload(Shift.shift_type),
                joinedload(AttendanceEvent.reviewer),
            )
            .where(AttendanceEvent.staff_id == profile.id)
            .order_by(AttendanceEvent.occurred_at.desc())
            .limit(100)
        ).all()
        cards = db.session.scalars(
            db.select(StaffCard).where(StaffCard.staff_id == profile.id).order_by(StaffCard.registered_at.desc())
        ).all()
    return render_template(
        "student/attendance.html", events=events, cards=cards,
        reason_required={AttendanceStatus.LATE_REASON_REQUIRED, AttendanceStatus.MISSING_CLOCK_IN},
        card_status=CardStatus,
    )


@bp.post("/attendance/<int:event_id>/reason")
@role_required(Role.STUDENT)
def submit_attendance_reason(event_id: int):
    event = db.session.get(AttendanceEvent, event_id)
    if not event or not current_user.staff_profile or event.staff_id != current_user.staff_profile.id:
        flash("找不到可填寫的打卡紀錄。", "danger")
        return redirect(url_for("student.attendance_page"))
    try:
        submit_reason(
            event,
            category=request.form.get("reason_category", "其他"),
            reason=request.form.get("reason_text", ""),
            claimed_arrival=request.form.get("claimed_arrival_at") or None,
        )
        flash("出勤事由已送交管理員確認。 / Explanation submitted.", "success")
    except (AttendanceError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("student.attendance_page"))
