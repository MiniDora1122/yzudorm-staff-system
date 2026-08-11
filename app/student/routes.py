from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from flask import flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload

from . import bp
from ..decorators import role_required
from ..extensions import db
from ..models import (
    LeaveRequest,
    LeaveStatus,
    Role,
    DocumentStatus,
    DocumentPageKind,
    DocumentType,
    Shift,
    ShiftStatus,
    ShiftType,
    StaffProfile,
    StaffDocument,
    SwapAdminStatus,
    SwapRequest,
    WorkLocation,
)
from ..services.payroll import calculate_staff_cost, get_payroll_setting
from ..services.notifications import notifications_for_user
from ..services.documents import (
    PRIVACY_NOTICE_VERSION,
    PAGE_LABELS,
    confirm_document_set,
    delete_document_set,
    expiry_state,
    group_document_sets,
    mask_identifier,
    read_document,
    upload_document_set,
)
from ..services.requests import (
    WorkflowError,
    cancel_leave_request,
    cancel_swap_request,
    create_leave_request,
    create_swap_request,
    respond_swap_request,
)
from ..services.scheduling import month_bounds, shift_to_event
from ..services.workflow_calendar import (
    add_annotations,
    direct_swap_invitations,
    swap_annotation,
    workflow_annotations,
)
from ..time_utils import local_today


@bp.get("/")
@role_required(Role.STUDENT)
def dashboard():
    profile = current_user.staff_profile
    today = local_today()
    month_start = today.replace(day=1)
    next_month = date(today.year + (today.month == 12), 1 if today.month == 12 else today.month + 1, 1)
    shifts = []
    month_hours = Decimal("0")
    month_wage = 0
    hourly_wage = 0
    pending_leave = 0
    pending_swap = 0
    if profile:
        shifts = db.session.scalars(
            db.select(Shift)
            .where(
                Shift.staff_id == profile.id,
                Shift.status == ShiftStatus.SCHEDULED,
                Shift.shift_date >= today,
            )
            .order_by(Shift.shift_date)
            .limit(5)
        ).all()
        month_hours = db.session.scalar(
            db.select(db.func.coalesce(db.func.sum(ShiftType.default_hours), 0))
            .select_from(Shift)
            .join(ShiftType, Shift.shift_type_id == ShiftType.id)
            .where(
                Shift.staff_id == profile.id,
                Shift.status == ShiftStatus.SCHEDULED,
                Shift.shift_date >= month_start,
                Shift.shift_date < next_month,
            )
        )
        setting = get_payroll_setting(month_start)
        if setting is not None:
            cost = calculate_staff_cost(
                profile=profile, hours=Decimal(str(month_hours or 0)), setting=setting
            )
            month_wage = cost["gross_wage"]
            hourly_wage = cost["hourly_wage"]
        pending_leave = db.session.scalar(
            db.select(db.func.count())
            .select_from(LeaveRequest)
            .where(LeaveRequest.staff_id == profile.id, LeaveRequest.status == LeaveStatus.PENDING)
        )
        pending_swap = db.session.scalar(
            db.select(db.func.count())
            .select_from(SwapRequest)
            .where(
                db.or_(
                    db.and_(
                        SwapRequest.target_staff_id == profile.id,
                        SwapRequest.admin_status == SwapAdminStatus.NOT_READY,
                    ),
                    db.and_(
                        SwapRequest.requester_id == profile.id,
                        SwapRequest.admin_status.in_([SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING]),
                    ),
                )
            )
        )
    locations = db.session.scalars(
        db.select(WorkLocation)
        .order_by(WorkLocation.display_order)
    ).all()
    return render_template(
        "student/dashboard.html",
        profile=profile,
        shifts=shifts,
        month_hours=month_hours,
        month_wage=month_wage,
        hourly_wage=hourly_wage,
        pending_leave=pending_leave,
        pending_swap=pending_swap,
        open_notifications=notifications_for_user(current_user)[0],
        residence_state=expiry_state(profile.residence_expiry) if profile else None,
        permit_state=expiry_state(profile.work_permit_expiry) if profile else None,
        locations=locations,
        locations_data=[
            {"id": item.id, "code": item.code, "name": item.name, "nameEn": item.name_en, "color": item.color}
            for item in locations
        ],
    )


@bp.get("/notifications")
@role_required(Role.STUDENT)
def notifications_page():
    open_notifications, completed_notifications = notifications_for_user(current_user)
    return render_template(
        "notifications.html",
        open_notifications=open_notifications,
        completed_notifications=completed_notifications,
        dashboard_url=url_for("student.dashboard"),
    )


@bp.get("/profile")
@role_required(Role.STUDENT)
def profile():
    profile = current_user.staff_profile
    documents = []
    if profile:
        documents = db.session.scalars(
            db.select(StaffDocument)
            .where(
                StaffDocument.staff_id == profile.id,
                StaffDocument.status.not_in([DocumentStatus.REPLACED, DocumentStatus.DELETED]),
            )
            .order_by(StaffDocument.uploaded_at.desc())
        ).all()
    return render_template(
        "student/profile.html",
        profile=profile,
        document_groups=group_document_sets(documents),
        privacy_notice_version=PRIVACY_NOTICE_VERSION,
        mask_identifier=mask_identifier,
        residence_state=expiry_state(profile.residence_expiry) if profile else None,
        permit_state=expiry_state(profile.work_permit_expiry) if profile else None,
        page_labels=PAGE_LABELS,
    )


@bp.post("/profile")
@role_required(Role.STUDENT)
def update_profile():
    profile = current_user.staff_profile
    if profile is None:
        flash("找不到工讀生資料。", "danger")
        return redirect(url_for("student.profile"))
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    nationality = request.form.get("nationality", "").strip()
    if len(email) > 255 or (email and ("@" not in email or "." not in email.rsplit("@", 1)[-1])):
        flash("Email 格式錯誤。", "danger")
        return redirect(url_for("student.profile"))
    if len(phone) > 30 or not nationality or len(nationality) > 80:
        flash("聯絡電話或國籍格式錯誤。", "danger")
        return redirect(url_for("student.profile"))
    profile.email = email or None
    profile.phone = phone or None
    profile.nationality = nationality
    db.session.commit()
    flash("基本資料已更新。", "success")
    return redirect(url_for("student.profile"))


def parse_optional_date(field: str) -> date | None:
    value = request.form.get(field, "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WorkflowError("INVALID_DATE", "日期格式錯誤。") from exc


@bp.post("/documents")
@role_required(Role.STUDENT)
def upload_staff_document():
    profile = current_user.staff_profile
    if profile is None:
        flash("請選擇要上傳的文件。", "danger")
        return redirect(url_for("student.profile"))
    if request.form.get("privacy_consent") != "yes":
        flash("請先閱讀並同意個資蒐集告知。", "danger")
        return redirect(url_for("student.profile"))
    try:
        document_type = DocumentType(request.form.get("document_type", ""))
        uploads = (
            {
                DocumentPageKind.RESIDENCE_FRONT: request.files.get("residence_front"),
                DocumentPageKind.RESIDENCE_BACK: request.files.get("residence_back"),
            }
            if document_type == DocumentType.RESIDENCE_PERMIT
            else {
                DocumentPageKind.WORK_PERMIT_PAGE_1: request.files.get("work_permit_page_1"),
                DocumentPageKind.WORK_PERMIT_PAGE_2: request.files.get("work_permit_page_2"),
            }
        )
        upload_document_set(
            profile=profile,
            document_type=document_type,
            uploads=uploads,
            actor_user_id=current_user.id,
        )
        flash("整份文件已安全上傳，請逐頁核對後再確認更新。", "success")
    except (ValueError, WorkflowError) as exc:
        db.session.rollback()
        flash(exc.message if isinstance(exc, WorkflowError) else "文件類型錯誤。", "danger")
    return redirect(url_for("student.profile"))


@bp.post("/documents/<int:document_id>/confirm")
@role_required(Role.STUDENT)
def confirm_staff_document(document_id: int):
    profile = current_user.staff_profile
    document = db.session.get(StaffDocument, document_id)
    if profile is None or document is None:
        flash("找不到指定文件。", "danger")
        return redirect(url_for("student.profile"))
    try:
        fields = {
            "residence_id": request.form.get("residence_id", "").strip(),
            "residence_expiry": parse_optional_date("residence_expiry"),
            "work_permit_start": parse_optional_date("work_permit_start"),
            "work_permit_expiry": parse_optional_date("work_permit_expiry"),
        }
        confirm_document_set(
            document=document,
            profile=profile,
            fields=fields,
            actor_user_id=current_user.id,
        )
        flash("證件資料已送交管理員審核；核准前不會更新正式資料。 / Submitted for administrator review.", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("student.profile"))


@bp.get("/documents/<int:document_id>/file")
@role_required(Role.STUDENT)
def view_staff_document(document_id: int):
    profile = current_user.staff_profile
    document = db.session.get(StaffDocument, document_id)
    if profile is None or document is None or document.staff_id != profile.id:
        return "", 404
    if document.status == DocumentStatus.DELETED:
        return "", 404
    try:
        response = send_file(BytesIO(read_document(document)), mimetype=document.mime_type, max_age=0)
    except WorkflowError:
        return "", 404
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.get("/documents/<int:document_id>/download")
@role_required(Role.STUDENT)
def download_staff_document(document_id: int):
    profile = current_user.staff_profile
    document = db.session.get(StaffDocument, document_id)
    if profile is None or document is None or document.staff_id != profile.id or document.status == DocumentStatus.DELETED:
        return "", 404
    label = "residence-permit" if document.document_type == DocumentType.RESIDENCE_PERMIT else "work-permit"
    page_label = document.page_kind.value.lower().replace("_", "-")
    try:
        response = send_file(
            BytesIO(read_document(document)),
            mimetype=document.mime_type,
            as_attachment=True,
            download_name=f"{label}-{page_label}-{document.uploaded_at:%Y%m%d}.jpg",
            max_age=0,
        )
    except WorkflowError:
        return "", 404
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@bp.get("/document-sets/<string:set_id>/download")
@role_required(Role.STUDENT)
def download_staff_document_set(set_id: str):
    profile = current_user.staff_profile
    documents = db.session.scalars(
        db.select(StaffDocument)
        .where(
            StaffDocument.document_set_id == set_id,
            StaffDocument.status != DocumentStatus.DELETED,
        )
        .order_by(StaffDocument.id)
    ).all()
    if profile is None or not documents or any(item.staff_id != profile.id for item in documents):
        return "", 404
    output = BytesIO()
    try:
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for index, document in enumerate(documents, start=1):
                archive.writestr(f"{index:02d}-{document.page_kind.value.lower()}.jpg", read_document(document))
    except WorkflowError:
        return "", 404
    output.seek(0)
    label = "residence-permit" if documents[0].document_type == DocumentType.RESIDENCE_PERMIT else "work-permit"
    response = send_file(
        output,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{label}-{documents[0].uploaded_at:%Y%m%d}.zip",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@bp.post("/documents/<int:document_id>/delete")
@role_required(Role.STUDENT)
def delete_staff_document(document_id: int):
    profile = current_user.staff_profile
    document = db.session.get(StaffDocument, document_id)
    if profile is None or document is None:
        flash("找不到指定文件。", "danger")
        return redirect(url_for("student.profile"))
    try:
        delete_document_set(document=document, profile=profile, actor_user_id=current_user.id)
        flash("文件影像已刪除；已確認的正式欄位不受影響。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("student.profile"))


@bp.get("/requests")
@role_required(Role.STUDENT)
def requests_page():
    profile = current_user.staff_profile
    if profile is None:
        return render_template(
            "student/requests.html", own_shifts=[], target_shifts=[], target_profiles=[],
            leave_requests=[], swap_requests=[], filter_scope="ALL",
            filter_month=local_today().strftime("%Y-%m"), profile=None,
        )
    today = local_today()
    own_shifts = db.session.scalars(
        db.select(Shift)
        .where(
            Shift.staff_id == profile.id,
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.shift_date >= today,
        )
        .order_by(Shift.shift_date, ShiftType.display_order)
        .join(ShiftType)
    ).all()
    target_shifts = db.session.scalars(
        db.select(Shift)
        .join(ShiftType)
        .where(
            Shift.staff_id != profile.id,
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.shift_date >= today,
        )
        .order_by(Shift.shift_date, ShiftType.display_order)
    ).all()
    target_profiles = db.session.scalars(
        db.select(StaffProfile)
        .join(StaffProfile.user)
        .where(StaffProfile.id != profile.id, StaffProfile.user.has(is_active=True))
        .order_by(StaffProfile.name)
    ).all()
    filter_scope = request.args.get("scope", "ALL").upper()
    filter_month = request.args.get("month", today.strftime("%Y-%m"))
    if filter_scope not in {"ALL", "MONTH"}:
        filter_scope = "ALL"
    leave_statement = (
        db.select(LeaveRequest)
        .where(LeaveRequest.staff_id == profile.id)
        .order_by(LeaveRequest.created_at.desc())
    )
    swap_statement = (
        db.select(SwapRequest)
        .where(db.or_(SwapRequest.requester_id == profile.id, SwapRequest.target_staff_id == profile.id))
        .order_by(SwapRequest.created_at.desc())
    )
    if filter_scope == "MONTH":
        try:
            month_start, month_end = month_bounds(filter_month)
        except ValueError:
            filter_month = today.strftime("%Y-%m")
            month_start, month_end = month_bounds(filter_month)
            flash("月份格式錯誤，已改為本月。", "warning")
        leave_statement = leave_statement.where(
            LeaveRequest.shift.has(db.and_(Shift.shift_date >= month_start, Shift.shift_date < month_end))
        )
        swap_statement = swap_statement.where(
            db.or_(
                SwapRequest.requester_shift.has(db.and_(Shift.shift_date >= month_start, Shift.shift_date < month_end)),
                SwapRequest.target_shift.has(db.and_(Shift.shift_date >= month_start, Shift.shift_date < month_end)),
            )
        )
    leave_requests = db.session.scalars(leave_statement).all()
    swap_requests = db.session.scalars(swap_statement).all()
    return render_template(
        "student/requests.html",
        own_shifts=own_shifts,
        target_shifts=target_shifts,
        target_profiles=target_profiles,
        leave_requests=leave_requests,
        swap_requests=swap_requests,
        profile=profile,
        filter_scope=filter_scope,
        filter_month=filter_month,
    )


@bp.post("/leave-requests")
@role_required(Role.STUDENT)
def create_leave():
    profile = current_user.staff_profile
    try:
        shift_id = int(request.form.get("shift_id", ""))
    except ValueError:
        flash("請選擇要請假的排班。", "danger")
        return redirect(url_for("student.requests_page"))
    reason = request.form.get("reason", "").strip()
    note = request.form.get("note", "").strip()
    if not reason or len(reason) > 255 or len(note) > 1000:
        flash("請輸入 1–255 字原因，備註不可超過 1000 字。", "danger")
        return redirect(url_for("student.requests_page"))
    shift = db.session.get(Shift, shift_id)
    if profile is None or shift is None:
        flash("找不到指定排班。", "danger")
        return redirect(url_for("student.requests_page"))
    try:
        create_leave_request(
            profile=profile,
            shift=shift,
            reason=reason,
            note=note,
            actor_user_id=current_user.id,
            today=local_today(),
        )
        flash("請假申請已送出。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("student.requests_page"))


@bp.post("/leave-requests/<int:request_id>/cancel")
@role_required(Role.STUDENT)
def cancel_leave(request_id: int):
    request_item = db.session.get(LeaveRequest, request_id)
    profile = current_user.staff_profile
    if request_item is None or profile is None:
        flash("找不到指定請假申請。", "danger")
        return redirect(url_for("student.requests_page"))
    try:
        cancel_leave_request(request_item=request_item, profile=profile, actor_user_id=current_user.id)
        flash("請假申請已取消。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("student.requests_page"))


@bp.post("/swap-requests")
@role_required(Role.STUDENT)
def create_swap():
    profile = current_user.staff_profile
    try:
        requester_shift_id = int(request.form.get("requester_shift_id", ""))
        target_staff_id = int(request.form.get("target_staff_id", ""))
        target_shift_text = request.form.get("target_shift_id", "").strip()
        target_shift_id = int(target_shift_text) if target_shift_text else None
    except ValueError:
        flash("換班資料格式錯誤。", "danger")
        return redirect(url_for("student.requests_page"))
    requester_shift = db.session.get(Shift, requester_shift_id)
    target_staff = db.session.get(StaffProfile, target_staff_id)
    target_shift = db.session.get(Shift, target_shift_id) if target_shift_id else None
    note = request.form.get("note", "").strip()
    if profile is None or requester_shift is None or target_staff is None or len(note) > 1000:
        flash("換班資料不完整或備註過長。", "danger")
        return redirect(url_for("student.requests_page"))
    try:
        create_swap_request(
            requester=profile,
            requester_shift=requester_shift,
            target_staff=target_staff,
            target_shift=target_shift,
            note=note,
            actor_user_id=current_user.id,
            today=local_today(),
        )
        flash("換班邀請已送出，等待對方回覆。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("student.requests_page"))


@bp.post("/swap-requests/<int:request_id>/respond")
@role_required(Role.STUDENT)
def respond_swap(request_id: int):
    request_item = db.session.get(SwapRequest, request_id)
    profile = current_user.staff_profile
    if request_item is None or profile is None:
        flash("找不到指定換班邀請。", "danger")
        return redirect(url_for("student.requests_page"))
    try:
        respond_swap_request(
            request_item=request_item,
            profile=profile,
            decision=request.form.get("decision", ""),
            actor_user_id=current_user.id,
        )
        flash("換班邀請已回覆。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("student.requests_page"))


@bp.post("/swap-requests/<int:request_id>/cancel")
@role_required(Role.STUDENT)
def cancel_swap(request_id: int):
    request_item = db.session.get(SwapRequest, request_id)
    profile = current_user.staff_profile
    if request_item is None or profile is None:
        flash("找不到指定換班申請。", "danger")
        return redirect(url_for("student.requests_page"))
    try:
        cancel_swap_request(request_item=request_item, profile=profile, actor_user_id=current_user.id)
        flash("換班申請已取消。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("student.requests_page"))


def parse_calendar_date(value: str | None) -> date:
    if not value:
        raise ValueError("缺少日期範圍。")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError("日期格式錯誤。") from exc


@bp.get("/api/shifts")
@role_required(Role.STUDENT)
def shift_events():
    profile = current_user.staff_profile
    if profile is None:
        return jsonify([])
    try:
        start = parse_calendar_date(request.args.get("start"))
        end = parse_calendar_date(request.args.get("end"))
    except ValueError as exc:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}), 400

    statement = (
        db.select(Shift)
        .options(
            joinedload(Shift.shift_type).joinedload(ShiftType.work_location),
            joinedload(Shift.staff),
            joinedload(Shift.series),
        )
        .join(ShiftType)
        .where(
            Shift.staff_id == profile.id,
            Shift.status.in_([ShiftStatus.SCHEDULED, ShiftStatus.ON_LEAVE]),
            Shift.shift_date >= start,
            Shift.shift_date < end,
        )
        .order_by(Shift.shift_date, ShiftType.display_order)
    )
    shifts = db.session.scalars(statement).all()
    annotations = workflow_annotations(shifts, profile_id=profile.id)
    events = [
        add_annotations(shift_to_event(shift, student_view=True), annotations.get(shift.id, []))
        for shift in shifts
    ]
    own_ids = {shift.id for shift in shifts}
    for invitation in direct_swap_invitations(profile_id=profile.id, start=start, end=end):
        if invitation.requester_shift_id in own_ids:
            continue
        event = shift_to_event(invitation.requester_shift, student_view=True)
        event["id"] = f"swap-invitation-{invitation.id}"
        event["title"] = f"換班邀請｜{event['title']}"
        event["backgroundColor"] = "#d97706"
        event["borderColor"] = "#d97706"
        event["extendedProps"]["isSwapInvitation"] = True
        events.append(add_annotations(event, [swap_annotation(invitation, profile.id)]))
    return jsonify(events)


@bp.get("/api/monthly-hours")
@role_required(Role.STUDENT)
def monthly_hours_api():
    profile = current_user.staff_profile
    try:
        start, end = month_bounds(request.args.get("month", ""))
    except ValueError as exc:
        return jsonify({"error": {"code": "VALIDATION_ERROR", "message": str(exc)}}), 400

    total = 0
    gross_wage = 0
    hourly_wage = 0
    if profile is not None:
        total = db.session.scalar(
            db.select(db.func.coalesce(db.func.sum(ShiftType.default_hours), 0))
            .select_from(Shift)
            .join(ShiftType)
            .where(
                Shift.staff_id == profile.id,
                Shift.status == ShiftStatus.SCHEDULED,
                Shift.shift_date >= start,
                Shift.shift_date < end,
            )
        )
        setting = get_payroll_setting(start)
        if setting is not None:
            cost = calculate_staff_cost(
                profile=profile, hours=Decimal(str(total or 0)), setting=setting
            )
            gross_wage = cost["gross_wage"]
            hourly_wage = cost["hourly_wage"]
    return jsonify(
        {
            "month": start.strftime("%Y-%m"),
            "total_hours": float(total or 0),
            "gross_wage": gross_wage,
            "hourly_wage": hourly_wage,
        }
    )
