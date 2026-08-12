from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import (
    LeaveRequest,
    LeaveStatus,
    Shift,
    ShiftType,
    SwapAdminStatus,
    SwapPeerStatus,
    SwapRequest,
)


def swap_annotation(item: SwapRequest, profile_id: int | None = None) -> dict | None:
    if item.admin_status == SwapAdminStatus.APPROVED:
        return {"kind": "SWAP", "status": "APPROVED", "label": "換班已核准", "class": "success"}
    if item.admin_status in {SwapAdminStatus.REJECTED, SwapAdminStatus.CANCELLED} or item.peer_status == SwapPeerStatus.REJECTED:
        return None
    if item.peer_status == SwapPeerStatus.ACCEPTED:
        return {"kind": "SWAP", "status": "PENDING_ADMIN", "label": "換班待管理員審核", "class": "info"}
    label = "換班等待對方回覆"
    if profile_id is not None and item.target_staff_id == profile_id:
        label = "換班邀請待你回覆"
    return {"kind": "SWAP", "status": "PENDING_PEER", "label": label, "class": "warning"}


def workflow_annotations(shifts: Iterable[Shift], *, profile_id: int | None = None) -> dict[int, list[dict]]:
    """Return visible workflow labels keyed by shift id, without exposing private notes."""
    shift_ids = {shift.id for shift in shifts}
    annotations: dict[int, list[dict]] = defaultdict(list)
    if not shift_ids:
        return annotations

    leave_statement = db.select(LeaveRequest).where(
        LeaveRequest.shift_id.in_(shift_ids),
        LeaveRequest.status.in_([LeaveStatus.PENDING, LeaveStatus.APPROVED]),
    )
    if profile_id is not None:
        leave_statement = leave_statement.where(LeaveRequest.staff_id == profile_id)
    for item in db.session.scalars(leave_statement):
        if item.status == LeaveStatus.APPROVED:
            annotation = {"kind": "LEAVE", "status": "APPROVED", "label": "請假已核准／缺員", "class": "danger"}
        else:
            annotation = {"kind": "LEAVE", "status": "PENDING", "label": "請假待審核", "class": "warning"}
        annotations[item.shift_id].append(annotation)

    swap_statement = db.select(SwapRequest).where(
        db.or_(
            SwapRequest.requester_shift_id.in_(shift_ids),
            SwapRequest.target_shift_id.in_(shift_ids),
        )
    )
    if profile_id is not None:
        swap_statement = swap_statement.where(
            db.or_(SwapRequest.requester_id == profile_id, SwapRequest.target_staff_id == profile_id)
        )
    for item in db.session.scalars(swap_statement):
        annotation = swap_annotation(item, profile_id)
        if annotation is None:
            continue
        annotations[item.requester_shift_id].append(annotation)
        if item.target_shift_id is not None:
            annotations[item.target_shift_id].append(annotation)
    return annotations


def add_annotations(event: dict, annotations: list[dict]) -> dict:
    event["extendedProps"]["workflowAnnotations"] = annotations
    return event


def direct_swap_invitations(*, profile_id: int, start, end) -> list[SwapRequest]:
    """Find direct-takeover invitations that do not yet belong to the target's calendar."""
    return db.session.scalars(
        db.select(SwapRequest)
        .options(
            joinedload(SwapRequest.requester_shift).joinedload(Shift.staff),
            joinedload(SwapRequest.requester_shift).joinedload(Shift.series),
            joinedload(SwapRequest.requester_shift)
            .joinedload(Shift.shift_type)
            .joinedload(ShiftType.work_location),
        )
        .join(Shift, SwapRequest.requester_shift_id == Shift.id)
        .where(
            SwapRequest.target_staff_id == profile_id,
            SwapRequest.target_shift_id.is_(None),
            SwapRequest.peer_status != SwapPeerStatus.REJECTED,
            SwapRequest.admin_status.in_([SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING]),
            Shift.shift_date >= start,
            Shift.shift_date < end,
        )
    ).all()
