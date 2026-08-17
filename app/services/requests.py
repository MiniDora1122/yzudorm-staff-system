from __future__ import annotations

from datetime import date

from ..extensions import db
from ..models import (
    LeaveRequest,
    LeaveStatus,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    StaffProfile,
    SwapAdminStatus,
    SwapPeerStatus,
    SwapRequest,
    utc_now,
)
from .audit import add_audit
from .scheduling import SchedulingConflict, validate_shift_assignment


class WorkflowError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_swap_assignments(
    *,
    requester: StaffProfile,
    requester_shift: Shift,
    target_staff: StaffProfile,
    target_shift: Shift | None,
) -> None:
    involved = {requester_shift.id}
    if target_shift is not None:
        involved.add(target_shift.id)
    try:
        validate_shift_assignment(
            shift_date=requester_shift.shift_date,
            shift_type=requester_shift.shift_type,
            staff=target_staff,
            exclude_shift_ids=involved,
            allow_location_overlap=True,
        )
        if target_shift is not None:
            validate_shift_assignment(
                shift_date=target_shift.shift_date,
                shift_type=target_shift.shift_type,
                staff=requester,
                exclude_shift_ids=involved,
                allow_location_overlap=True,
            )
    except SchedulingConflict as exc:
        raise WorkflowError(
            exc.code,
            f"換班後會造成排班衝突，無法送出：{exc.message} / The proposed swap has a scheduling conflict.",
        ) from exc


def create_leave_request(
    *, profile: StaffProfile, shift: Shift, reason: str, note: str | None, actor_user_id: int, today: date
) -> LeaveRequest:
    reason = reason.strip()
    if not reason or len(reason) > 255:
        raise WorkflowError("REASON_REQUIRED", "請假原因為必填，且不可超過 255 字。 / Leave reason is required.")
    if shift.staff_id != profile.id:
        raise WorkflowError("NOT_OWNER", "只能替自己的排班提出請假。")
    if shift.status != ShiftStatus.SCHEDULED:
        raise WorkflowError("SHIFT_UNAVAILABLE", "此排班目前不可提出請假。")
    if shift.publication_status != ShiftPublicationStatus.PUBLISHED:
        raise WorkflowError(
            "SHIFT_NOT_PUBLISHED",
            "草稿排班尚未正式發布，不能提出請假。 / Draft shifts cannot be used for leave requests.",
        )
    if shift.shift_date < today:
        raise WorkflowError("PAST_SHIFT", "已過期排班不可提出新請假。")
    duplicate = db.session.scalar(
        db.select(LeaveRequest).where(
            LeaveRequest.shift_id == shift.id,
            LeaveRequest.status == LeaveStatus.PENDING,
        )
    )
    if duplicate:
        raise WorkflowError("DUPLICATE_LEAVE", "此排班已有待處理的請假申請。")
    request_item = LeaveRequest(
        staff_id=profile.id,
        shift_id=shift.id,
        reason=reason,
        note=note or None,
        status=LeaveStatus.PENDING,
    )
    db.session.add(request_item)
    db.session.flush()
    add_audit(actor_user_id, "LEAVE_CREATED", "LeaveRequest", request_item.id, f"建立請假申請，排班 #{shift.id}")
    db.session.commit()
    return request_item


def cancel_leave_request(*, request_item: LeaveRequest, profile: StaffProfile, actor_user_id: int) -> None:
    if request_item.staff_id != profile.id:
        raise WorkflowError("NOT_OWNER", "只能取消自己的請假申請。")
    if request_item.status != LeaveStatus.PENDING:
        raise WorkflowError("INVALID_STATUS", "只有待處理的請假申請可以取消。")
    request_item.status = LeaveStatus.CANCELLED
    add_audit(actor_user_id, "LEAVE_CANCELLED", "LeaveRequest", request_item.id, f"取消請假申請 #{request_item.id}")
    db.session.commit()


def review_leave_request(
    *, request_item: LeaveRequest, decision: str, review_note: str | None, actor_user_id: int
) -> None:
    if request_item.status != LeaveStatus.PENDING:
        raise WorkflowError("INVALID_STATUS", "此請假申請已處理，不能重複審核。")
    if decision not in {"APPROVE", "REJECT"}:
        raise WorkflowError("INVALID_DECISION", "審核決定格式錯誤。")
    now = utc_now()
    request_item.reviewed_by = actor_user_id
    request_item.reviewed_at = now
    request_item.review_note = review_note or None
    if decision == "APPROVE":
        from .periods import ensure_month_open

        ensure_month_open(request_item.shift.shift_date)
        if request_item.shift.status != ShiftStatus.SCHEDULED:
            raise WorkflowError("SHIFT_CHANGED", "原排班狀態已變更，無法核准。")
        request_item.status = LeaveStatus.APPROVED
        request_item.shift.status = ShiftStatus.ON_LEAVE
        action = "LEAVE_APPROVED"
        summary = f"核准請假 #{request_item.id}；排班 #{request_item.shift_id} 標記缺員"
    else:
        request_item.status = LeaveStatus.REJECTED
        action = "LEAVE_REJECTED"
        summary = f"拒絕請假 #{request_item.id}"
    add_audit(actor_user_id, action, "LeaveRequest", request_item.id, summary)
    db.session.commit()


def create_swap_request(
    *,
    requester: StaffProfile,
    requester_shift: Shift,
    target_staff: StaffProfile,
    target_shift: Shift | None,
    note: str | None,
    actor_user_id: int,
    today: date,
) -> SwapRequest:
    note = (note or "").strip()
    if not note or len(note) > 1000:
        raise WorkflowError("REASON_REQUIRED", "換班原因為必填，且不可超過 1000 字。 / Swap reason is required.")
    if requester_shift.staff_id != requester.id:
        raise WorkflowError("NOT_OWNER", "不能交換不屬於自己的排班。")
    if requester_shift.status != ShiftStatus.SCHEDULED or requester_shift.shift_date < today:
        raise WorkflowError("SHIFT_UNAVAILABLE", "自己的原排班已過期或目前不可交換。")
    if requester_shift.publication_status != ShiftPublicationStatus.PUBLISHED:
        raise WorkflowError(
            "SHIFT_NOT_PUBLISHED",
            "草稿排班尚未正式發布，不能提出換班。 / Draft shifts cannot be swapped.",
        )
    if target_staff.id == requester.id:
        raise WorkflowError("SAME_STAFF", "換班對象不可選擇自己。")
    if not target_staff.user.is_active:
        raise WorkflowError("TARGET_INACTIVE", "換班對象帳號已停用。")
    if target_shift is not None:
        if target_shift.staff_id != target_staff.id:
            raise WorkflowError("TARGET_SHIFT_NOT_OWNER", "指定班表不屬於換班對象。")
        if target_shift.status != ShiftStatus.SCHEDULED or target_shift.shift_date < today:
            raise WorkflowError("TARGET_SHIFT_UNAVAILABLE", "對方班表已過期或目前不可交換。")
        if target_shift.publication_status != ShiftPublicationStatus.PUBLISHED:
            raise WorkflowError(
                "TARGET_SHIFT_NOT_PUBLISHED",
                "對方的草稿排班尚未正式發布。 / The target shift is still a draft.",
            )
        if target_shift.id == requester_shift.id:
            raise WorkflowError("SAME_SHIFT", "不能用同一筆班表交換。")
    validate_swap_assignments(
        requester=requester,
        requester_shift=requester_shift,
        target_staff=target_staff,
        target_shift=target_shift,
    )
    duplicate = db.session.scalar(
        db.select(SwapRequest).where(
            SwapRequest.requester_shift_id == requester_shift.id,
            SwapRequest.peer_status != SwapPeerStatus.REJECTED,
            SwapRequest.admin_status.in_([SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING]),
        )
    )
    if duplicate:
        raise WorkflowError("DUPLICATE_SWAP", "此排班已有進行中的換班申請。")
    request_item = SwapRequest(
        requester_id=requester.id,
        requester_shift_id=requester_shift.id,
        target_staff_id=target_staff.id,
        target_shift_id=target_shift.id if target_shift else None,
        note=note,
    )
    db.session.add(request_item)
    db.session.flush()
    add_audit(actor_user_id, "SWAP_CREATED", "SwapRequest", request_item.id, f"建立換班申請 #{request_item.id}")
    db.session.commit()
    return request_item


def cancel_swap_request(*, request_item: SwapRequest, profile: StaffProfile, actor_user_id: int) -> None:
    if request_item.requester_id != profile.id:
        raise WorkflowError("NOT_OWNER", "只能取消自己提出的換班申請。")
    if request_item.admin_status not in {SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING}:
        raise WorkflowError("INVALID_STATUS", "此換班申請已完成審核，不能取消。")
    request_item.admin_status = SwapAdminStatus.CANCELLED
    add_audit(actor_user_id, "SWAP_CANCELLED", "SwapRequest", request_item.id, f"取消換班申請 #{request_item.id}")
    db.session.commit()


def respond_swap_request(
    *, request_item: SwapRequest, profile: StaffProfile, decision: str, actor_user_id: int
) -> None:
    if request_item.target_staff_id != profile.id:
        raise WorkflowError("NOT_TARGET", "只有被邀請的工讀生可以回覆。")
    if request_item.peer_status != SwapPeerStatus.PENDING or request_item.admin_status != SwapAdminStatus.NOT_READY:
        raise WorkflowError("INVALID_STATUS", "此換班邀請已回覆或已取消。")
    if decision == "ACCEPT":
        validate_swap_assignments(
            requester=request_item.requester,
            requester_shift=request_item.requester_shift,
            target_staff=request_item.target_staff,
            target_shift=request_item.target_shift,
        )
        request_item.peer_status = SwapPeerStatus.ACCEPTED
        request_item.admin_status = SwapAdminStatus.PENDING
        action = "SWAP_PEER_ACCEPTED"
    elif decision == "REJECT":
        request_item.peer_status = SwapPeerStatus.REJECTED
        action = "SWAP_PEER_REJECTED"
    else:
        raise WorkflowError("INVALID_DECISION", "回覆格式錯誤。")
    request_item.peer_responded_at = utc_now()
    add_audit(actor_user_id, action, "SwapRequest", request_item.id, f"回覆換班申請 #{request_item.id}")
    db.session.commit()


def review_swap_request(
    *, request_item: SwapRequest, decision: str, review_note: str | None, actor_user_id: int
) -> None:
    if request_item.peer_status != SwapPeerStatus.ACCEPTED or request_item.admin_status != SwapAdminStatus.PENDING:
        raise WorkflowError("PEER_NOT_ACCEPTED", "對方尚未接受，或此換班申請已處理。")
    if decision not in {"APPROVE", "REJECT"}:
        raise WorkflowError("INVALID_DECISION", "審核決定格式錯誤。")
    request_item.reviewed_by = actor_user_id
    request_item.reviewed_at = utc_now()
    request_item.review_note = review_note or None
    if decision == "REJECT":
        request_item.admin_status = SwapAdminStatus.REJECTED
        add_audit(actor_user_id, "SWAP_REJECTED", "SwapRequest", request_item.id, f"拒絕換班 #{request_item.id}")
        db.session.commit()
        return

    requester_shift = request_item.requester_shift
    target_shift = request_item.target_shift
    from .periods import ensure_month_open

    ensure_month_open(requester_shift.shift_date)
    if target_shift is not None:
        ensure_month_open(target_shift.shift_date)
    if requester_shift.status != ShiftStatus.SCHEDULED or requester_shift.staff_id != request_item.requester_id:
        raise WorkflowError("REQUESTER_SHIFT_CHANGED", "申請人的原排班已變更，無法核准。")
    if target_shift is not None and (
        target_shift.status != ShiftStatus.SCHEDULED or target_shift.staff_id != request_item.target_staff_id
    ):
        raise WorkflowError("TARGET_SHIFT_CHANGED", "換班對象的原排班已變更，無法核准。")

    involved = {requester_shift.id}
    if target_shift is not None:
        involved.add(target_shift.id)
    try:
        validate_shift_assignment(
            shift_date=requester_shift.shift_date,
            shift_type=requester_shift.shift_type,
            staff=request_item.target_staff,
            exclude_shift_ids=involved,
            allow_location_overlap=True,
        )
        if target_shift is not None:
            validate_shift_assignment(
                shift_date=target_shift.shift_date,
                shift_type=target_shift.shift_type,
                staff=request_item.requester,
                exclude_shift_ids=involved,
                allow_location_overlap=True,
            )
    except SchedulingConflict as exc:
        raise WorkflowError(exc.code, f"重新檢查排班時發現衝突：{exc.message}") from exc

    requester_shift.staff_id = request_item.target_staff_id
    if target_shift is not None:
        target_shift.staff_id = request_item.requester_id
    request_item.admin_status = SwapAdminStatus.APPROVED
    add_audit(
        actor_user_id,
        "SWAP_APPROVED",
        "SwapRequest",
        request_item.id,
        f"核准換班 #{request_item.id}；排班 #{requester_shift.id}"
        + (f" 與 #{target_shift.id} 交換" if target_shift else " 改由受邀者承接"),
    )
    db.session.commit()
