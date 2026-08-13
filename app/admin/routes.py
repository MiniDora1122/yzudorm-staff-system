from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
import csv
import re
from zipfile import ZIP_DEFLATED, ZipFile

from flask import current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from . import bp
from ..decorators import role_required
from ..extensions import db
from ..models import (
    Country,
    PayrollSetting,
    AuditLog,
    DocumentStatus,
    LeaveRequest,
    LeaveStatus,
    Role,
    SchedulingExceptionPeriod,
    SchedulingPolicy,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    ShiftType,
    StaffProfile,
    StaffDocument,
    SwapAdminStatus,
    SwapRequest,
    User,
    WorkLocation,
    utc_now,
)
from ..services.payroll import calculate_staff_cost, get_payroll_setting, money
from ..services.notifications import notification_page_for_user, notifications_for_user
from ..services.compliance import active_countries, canonical_country_name, get_scheduling_policy
from ..services.accounts import (
    AccountError,
    create_admin_account,
    create_student_account,
    reset_admin_password,
    reset_student_password,
)
from ..services.documents import (
    PAGE_LABELS,
    DocumentError,
    expiry_state,
    group_document_sets,
    mask_identifier,
    read_document,
    review_document_set,
)
from ..services.reports import (
    build_daily_hours_matrix_workbook,
    build_monthly_hours_workbook,
    document_expiry_csv,
    payroll_cost_csv,
    shift_detail_csv,
    workflow_history_csv,
)
from ..services.audit import add_audit
from ..services.requests import WorkflowError, review_leave_request, review_swap_request
from ..services.retention import cleanup_expired_documents, get_retention_policy, save_retention_policy
from ..services.scheduling import (
    SchedulingConflict,
    create_shift,
    create_weekly_shift_series,
    month_bounds,
    shift_to_event,
    update_shift,
    validate_shift_assignment,
)
from ..services.workflow_calendar import add_annotations, workflow_annotations
from ..time_utils import local_today


@bp.get("/")
@role_required(Role.ADMIN)
def dashboard():
    today = local_today()
    month_start, next_month = month_bounds(today.strftime("%Y-%m"))
    counts = {
        "staff": db.session.scalar(
            db.select(db.func.count()).select_from(StaffProfile).join(StaffProfile.user).where(User.is_active.is_(True))
        ),
        "shift_types": db.session.scalar(db.select(db.func.count()).select_from(ShiftType)),
        "shifts": db.session.scalar(
            db.select(db.func.count())
            .select_from(Shift)
            .where(
                Shift.status == ShiftStatus.SCHEDULED,
                Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
                Shift.shift_date >= month_start,
                Shift.shift_date < next_month,
            )
        ),
        "today": db.session.scalar(
            db.select(db.func.count())
            .select_from(Shift)
            .where(
                Shift.shift_date == today,
                Shift.status == ShiftStatus.SCHEDULED,
                Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
            )
        ),
        "pending_leave": db.session.scalar(
            db.select(db.func.count()).select_from(LeaveRequest).where(LeaveRequest.status == LeaveStatus.PENDING)
        ),
        "pending_swap": db.session.scalar(
            db.select(db.func.count()).select_from(SwapRequest).where(SwapRequest.admin_status == SwapAdminStatus.PENDING)
        ),
    }
    shift_types = db.session.scalars(
        db.select(ShiftType).where(ShiftType.is_active.is_(True)).order_by(ShiftType.display_order)
    ).all()
    tomorrow = today + timedelta(days=1)
    upcoming_shifts = db.session.scalars(
        db.select(Shift)
        .join(Shift.shift_type)
        .join(Shift.staff)
        .options(
            joinedload(Shift.staff),
            joinedload(Shift.shift_type).joinedload(ShiftType.work_location),
        )
        .where(
            Shift.shift_date.in_([today, tomorrow]),
            Shift.status.in_([ShiftStatus.SCHEDULED, ShiftStatus.ON_LEAVE]),
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
        )
        .order_by(Shift.shift_date, ShiftType.start_time, ShiftType.display_order, StaffProfile.name)
    ).all()
    schedule_by_day = {
        today: [shift for shift in upcoming_shifts if shift.shift_date == today],
        tomorrow: [shift for shift in upcoming_shifts if shift.shift_date == tomorrow],
    }
    return render_template(
        "admin/dashboard.html",
        counts=counts,
        shift_types=shift_types,
        schedule_by_day=schedule_by_day,
        today=today,
        tomorrow=tomorrow,
        open_notifications=notifications_for_user(current_user)[0],
    )


@bp.get("/notifications")
@role_required(Role.ADMIN)
def notifications_page():
    open_notifications, completed_notifications, pagination = notification_page_for_user(
        current_user, page=request.args.get("page", 1, type=int)
    )
    return render_template(
        "notifications.html",
        open_notifications=open_notifications,
        completed_notifications=completed_notifications,
        pagination=pagination,
        dashboard_url=url_for("admin.dashboard"),
    )


@bp.get("/audit-logs")
@role_required(Role.ADMIN)
def audit_logs_page():
    statement = db.select(AuditLog).outerjoin(AuditLog.actor)
    action = request.args.get("action", "").strip()
    username = request.args.get("username", "").strip()
    ip_address = request.args.get("ip_address", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    if action:
        statement = statement.where(AuditLog.action == action)
    if username:
        statement = statement.where(User.username.ilike(f"%{username}%"))
    if ip_address:
        statement = statement.where(AuditLog.ip_address == ip_address)
    try:
        if date_from:
            statement = statement.where(AuditLog.created_at >= datetime.combine(date.fromisoformat(date_from), time.min))
        if date_to:
            statement = statement.where(
                AuditLog.created_at < datetime.combine(date.fromisoformat(date_to) + timedelta(days=1), time.min)
            )
    except ValueError:
        flash("日期格式錯誤，已忽略日期篩選。 / Invalid date filter ignored.", "warning")

    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(statement.order_by(AuditLog.created_at.desc()), page=max(page, 1), per_page=50)
    actions = db.session.scalars(db.select(AuditLog.action).distinct().order_by(AuditLog.action)).all()
    return render_template(
        "admin/audit_logs.html",
        pagination=pagination,
        audit_logs=pagination.items,
        actions=actions,
        filters={
            "action": action,
            "username": username,
            "ip_address": ip_address,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@bp.get("/settings/nationalities")
@role_required(Role.ADMIN)
def nationalities_settings():
    countries = db.session.scalars(
        db.select(Country).order_by(Country.display_order, Country.name)
    ).all()
    usage = dict(
        db.session.execute(
            db.select(StaffProfile.nationality, db.func.count(StaffProfile.id))
            .group_by(StaffProfile.nationality)
        ).all()
    )
    return render_template("admin/nationalities.html", countries=countries, usage=usage)


@bp.post("/settings/nationalities")
@role_required(Role.ADMIN)
def create_country():
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()
    name_en = request.form.get("name_en", "").strip()
    try:
        display_order = int(request.form.get("display_order", "0"))
    except ValueError:
        display_order = 0
    if not re.fullmatch(r"[A-Z0-9_-]{2,12}", code) or not name or len(name) > 80 or not name_en or len(name_en) > 100:
        flash("國籍代碼或中英文名稱格式錯誤。 / Invalid country settings.", "danger")
        return redirect(url_for("admin.nationalities_settings"))
    country = Country(
        code=code,
        name=name,
        name_en=name_en,
        weekly_limit_exempt=request.form.get("weekly_limit_exempt") == "yes",
        display_order=display_order,
    )
    db.session.add(country)
    try:
        db.session.flush()
        add_audit(current_user.id, "COUNTRY_CREATED", "Country", country.id, f"新增國籍 {country.code} {country.name}")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("國籍代碼或名稱已存在。 / Country code or name already exists.", "danger")
        return redirect(url_for("admin.nationalities_settings"))
    flash("國籍已新增。 / Nationality added.", "success")
    return redirect(url_for("admin.nationalities_settings"))


@bp.post("/settings/nationalities/<int:country_id>")
@role_required(Role.ADMIN)
def update_country(country_id: int):
    country = db.session.get(Country, country_id)
    if country is None:
        flash("找不到國籍設定。", "danger")
        return redirect(url_for("admin.nationalities_settings"))
    old_name = country.name
    name = request.form.get("name", "").strip()
    name_en = request.form.get("name_en", "").strip()
    try:
        display_order = int(request.form.get("display_order", "0"))
    except ValueError:
        display_order = 0
    if not name or len(name) > 80 or not name_en or len(name_en) > 100:
        flash("中英文名稱格式錯誤。 / Invalid country names.", "danger")
        return redirect(url_for("admin.nationalities_settings"))
    country.name = name
    country.name_en = name_en
    country.display_order = display_order
    country.is_active = True if country.is_taiwan else request.form.get("is_active") == "yes"
    country.weekly_limit_exempt = request.form.get("weekly_limit_exempt") == "yes"
    if old_name != name:
        db.session.execute(
            db.update(StaffProfile).where(StaffProfile.nationality == old_name).values(nationality=name)
        )
    try:
        add_audit(current_user.id, "COUNTRY_UPDATED", "Country", country.id, f"更新國籍 {country.code} {country.name}")
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("國籍名稱已存在。 / Country name already exists.", "danger")
        return redirect(url_for("admin.nationalities_settings"))
    flash("國籍設定已更新。 / Nationality updated.", "success")
    return redirect(url_for("admin.nationalities_settings"))


@bp.post("/settings/nationalities/<int:country_id>/delete")
@role_required(Role.ADMIN)
def delete_country(country_id: int):
    country = db.session.get(Country, country_id)
    if country is None or country.is_taiwan:
        flash("台灣國籍設定不可刪除。 / Taiwan cannot be deleted.", "danger")
        return redirect(url_for("admin.nationalities_settings"))
    used = db.session.scalar(
        db.select(db.func.count()).select_from(StaffProfile).where(StaffProfile.nationality == country.name)
    )
    if used:
        flash("此國籍仍有工讀生使用，請先修改人員資料。 / Nationality is still in use.", "danger")
        return redirect(url_for("admin.nationalities_settings"))
    country.is_active = False
    add_audit(current_user.id, "COUNTRY_ARCHIVED", "Country", country.id, f"停用國籍 {country.code} {country.name}")
    db.session.commit()
    flash("國籍已停用。 / Nationality disabled.", "success")
    return redirect(url_for("admin.nationalities_settings"))


@bp.get("/settings/scheduling")
@role_required(Role.ADMIN)
def scheduling_settings():
    policy = get_scheduling_policy()
    periods = db.session.scalars(
        db.select(SchedulingExceptionPeriod).order_by(
            SchedulingExceptionPeriod.starts_on.desc(), SchedulingExceptionPeriod.id.desc()
        )
    ).all()
    countries = db.session.scalars(
        db.select(Country).where(Country.is_active.is_(True)).order_by(Country.display_order, Country.name)
    ).all()
    weekdays = [
        (0, "星期一", "Monday"),
        (1, "星期二", "Tuesday"),
        (2, "星期三", "Wednesday"),
        (3, "星期四", "Thursday"),
        (4, "星期五", "Friday"),
        (5, "星期六", "Saturday"),
        (6, "星期日", "Sunday"),
    ]
    return render_template(
        "admin/scheduling_settings.html",
        policy=policy,
        periods=periods,
        countries=countries,
        weekdays=weekdays,
    )


@bp.post("/settings/scheduling")
@role_required(Role.ADMIN)
def update_scheduling_policy():
    try:
        limit = Decimal(request.form.get("weekly_hour_limit", "20"))
    except InvalidOperation:
        limit = Decimal("0")
    if limit < Decimal("0.5") or limit > Decimal("168"):
        flash("每週時數上限必須介於 0.5–168 小時。 / Invalid weekly limit.", "danger")
        return redirect(url_for("admin.scheduling_settings"))
    try:
        week_starts_on = int(request.form.get("week_starts_on", "0"))
    except ValueError:
        week_starts_on = -1
    if week_starts_on not in range(7):
        flash("每週起始日設定無效。 / Invalid week start day.", "danger")
        return redirect(url_for("admin.scheduling_settings"))
    policy = get_scheduling_policy()
    if policy is None:
        policy = SchedulingPolicy(id=1)
        db.session.add(policy)
    policy.foreign_weekly_limit_enabled = request.form.get("foreign_weekly_limit_enabled") == "yes"
    policy.weekly_hour_limit = limit
    policy.week_starts_on = week_starts_on
    policy.updated_by = current_user.id
    add_audit(
        current_user.id,
        "SCHEDULING_POLICY_UPDATED",
        "SchedulingPolicy",
        1,
        f"每週限制={'啟用' if policy.foreign_weekly_limit_enabled else '停用'}；上限 {limit} 小時；起始星期={week_starts_on}",
    )
    db.session.commit()
    flash("排班限制已更新。 / Scheduling policy updated.", "success")
    return redirect(url_for("admin.scheduling_settings"))


def _period_form_values():
    name = request.form.get("name", "").strip()
    name_en = request.form.get("name_en", "").strip()
    try:
        starts_on = date.fromisoformat(request.form.get("starts_on", ""))
        ends_on = date.fromisoformat(request.form.get("ends_on", ""))
    except ValueError as exc:
        raise ValueError("例外期間日期格式錯誤。 / Invalid exception dates.") from exc
    if not name or len(name) > 100 or not name_en or len(name_en) > 120 or ends_on < starts_on:
        raise ValueError("例外期間名稱錯誤，且截止日不可早於開始日。 / Invalid exception period.")
    return name, name_en, starts_on, ends_on


@bp.post("/settings/scheduling/exception-periods")
@role_required(Role.ADMIN)
def create_scheduling_exception():
    try:
        name, name_en, starts_on, ends_on = _period_form_values()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.scheduling_settings"))
    period = SchedulingExceptionPeriod(name=name, name_en=name_en, starts_on=starts_on, ends_on=ends_on)
    db.session.add(period)
    db.session.flush()
    add_audit(current_user.id, "SCHEDULING_EXCEPTION_CREATED", "SchedulingExceptionPeriod", period.id, f"新增排班限制例外期間 {name} {starts_on}–{ends_on}")
    db.session.commit()
    flash("例外期間已新增。 / Exception period added.", "success")
    return redirect(url_for("admin.scheduling_settings"))


@bp.post("/settings/scheduling/exception-periods/<int:period_id>")
@role_required(Role.ADMIN)
def update_scheduling_exception(period_id: int):
    period = db.session.get(SchedulingExceptionPeriod, period_id)
    if period is None:
        flash("找不到例外期間。", "danger")
        return redirect(url_for("admin.scheduling_settings"))
    try:
        period.name, period.name_en, period.starts_on, period.ends_on = _period_form_values()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.scheduling_settings"))
    period.is_active = request.form.get("is_active") == "yes"
    add_audit(current_user.id, "SCHEDULING_EXCEPTION_UPDATED", "SchedulingExceptionPeriod", period.id, f"更新排班限制例外期間 {period.name}")
    db.session.commit()
    flash("例外期間已更新。 / Exception period updated.", "success")
    return redirect(url_for("admin.scheduling_settings"))


@bp.post("/settings/scheduling/exception-periods/<int:period_id>/delete")
@role_required(Role.ADMIN)
def delete_scheduling_exception(period_id: int):
    period = db.session.get(SchedulingExceptionPeriod, period_id)
    if period is None:
        flash("找不到例外期間。", "danger")
        return redirect(url_for("admin.scheduling_settings"))
    add_audit(current_user.id, "SCHEDULING_EXCEPTION_DELETED", "SchedulingExceptionPeriod", period.id, f"刪除排班限制例外期間 {period.name}")
    db.session.delete(period)
    db.session.commit()
    flash("例外期間已刪除。 / Exception period deleted.", "success")
    return redirect(url_for("admin.scheduling_settings"))


@bp.get("/schedule")
@role_required(Role.ADMIN)
def schedule():
    profiles = db.session.scalars(
        db.select(StaffProfile)
        .join(StaffProfile.user)
        .where(StaffProfile.user.has(is_active=True))
        .order_by(StaffProfile.name)
    ).all()
    shift_types = db.session.scalars(
        db.select(ShiftType)
        .where(ShiftType.is_active.is_(True))
        .order_by(ShiftType.display_order)
    ).all()
    active_locations = db.session.scalars(
        db.select(WorkLocation)
        .where(WorkLocation.is_active.is_(True))
        .order_by(WorkLocation.display_order, WorkLocation.name)
    ).all()
    locations = db.session.scalars(
        db.select(WorkLocation).order_by(WorkLocation.display_order, WorkLocation.name)
    ).all()
    return render_template(
        "admin/schedule.html",
        profiles=profiles,
        shift_types=shift_types,
        locations=locations,
        active_locations=active_locations,
        locations_data=[
            {
                "id": location.id,
                "code": location.code,
                "name": location.name,
                "nameEn": location.name_en,
                "color": location.color,
                "displayOrder": location.display_order,
            }
            for location in locations
        ],
    )


@bp.get("/shifts/import-template.csv")
@role_required(Role.ADMIN)
def shift_import_template():
    content = "\ufeff日期,學號,班別代碼\n2026-08-12,DEMO001,OFFICE_AM\n"
    return send_file(
        BytesIO(content.encode("utf-8")),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name="shift-import-template.csv",
    )


@bp.post("/shifts/import")
@role_required(Role.ADMIN)
def import_shifts():
    uploaded = request.files.get("shift_file")
    allow_location_overlap = request.form.get("allow_location_overlap") == "yes"
    try:
        publication_status = ShiftPublicationStatus(
            request.form.get("publication_status", ShiftPublicationStatus.DRAFT.value)
        )
    except ValueError:
        publication_status = ShiftPublicationStatus.DRAFT
    if uploaded is None or not uploaded.filename:
        flash("請選擇 CSV 排班檔案。 / Please select a CSV schedule file.", "danger")
        return redirect(url_for("admin.schedule"))
    raw = uploaded.read(1_000_001)
    if len(raw) > 1_000_000:
        flash("CSV 檔案不可超過 1MB。 / CSV files must not exceed 1MB.", "danger")
        return redirect(url_for("admin.schedule"))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("CSV 必須使用 UTF-8 編碼。 / CSV must use UTF-8 encoding.", "danger")
        return redirect(url_for("admin.schedule"))

    reader = csv.DictReader(StringIO(text))
    profiles = {
        profile.student_number.upper(): profile
        for profile in db.session.scalars(
            db.select(StaffProfile).join(StaffProfile.user).where(User.is_active.is_(True))
        ).all()
    }
    shift_types = {
        item.code.upper(): item
        for item in db.session.scalars(
            db.select(ShiftType).where(ShiftType.is_active.is_(True))
        ).all()
    }
    created = []
    try:
        for line_number, row in enumerate(reader, start=2):
            if line_number > 501:
                raise ValueError("一次最多匯入 500 筆排班。 / Maximum 500 shifts per import.")
            normalized = {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
            date_text = normalized.get("日期") or normalized.get("date") or normalized.get("shift_date")
            student_number = (
                normalized.get("學號") or normalized.get("student_number") or ""
            ).upper()
            shift_code = (
                normalized.get("班別代碼") or normalized.get("shift_type_code") or ""
            ).upper()
            if not date_text and not student_number and not shift_code:
                continue
            try:
                shift_date = date.fromisoformat(date_text)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"第 {line_number} 列日期格式錯誤，請使用 YYYY-MM-DD。") from exc
            staff = profiles.get(student_number)
            shift_type = shift_types.get(shift_code)
            if staff is None:
                raise ValueError(f"第 {line_number} 列找不到學號 {student_number}。")
            if shift_type is None:
                raise ValueError(f"第 {line_number} 列找不到班別代碼 {shift_code}。")
            created.append(
                create_shift(
                    shift_date=shift_date,
                    shift_type=shift_type,
                    staff=staff,
                    actor_id=current_user.id,
                    allow_location_overlap=allow_location_overlap,
                    publication_status=publication_status,
                    commit=False,
                )
            )
        if not created:
            raise ValueError("CSV 沒有可匯入的排班資料。 / No schedule rows found in CSV.")
        add_audit(
            current_user.id,
            "SHIFTS_BULK_IMPORTED",
            "Shift",
            created[0].id,
            f"批量匯入 {len(created)} 筆排班；同地點重疊確認={allow_location_overlap}",
        )
        db.session.commit()
    except (ValueError, SchedulingConflict, IntegrityError) as exc:
        db.session.rollback()
        message = exc.message if isinstance(exc, SchedulingConflict) else str(exc)
        flash(f"批量匯入失敗，未寫入任何資料：{message}", "danger")
        return redirect(url_for("admin.schedule"))
    flash(f"已成功匯入 {len(created)} 筆排班。 / Imported {len(created)} shifts.", "success")
    return redirect(url_for("admin.schedule"))


@bp.get("/staff")
@role_required(Role.ADMIN)
def staff():
    sort_key = request.args.get("sort", "name")
    direction = request.args.get("direction", "asc")
    sort_columns = {
        "name": db.func.lower(StaffProfile.name),
        "student_number": db.func.lower(StaffProfile.student_number),
        "contact": db.func.lower(db.func.coalesce(StaffProfile.email, StaffProfile.phone, "")),
        "nationality": db.func.lower(StaffProfile.nationality),
    }
    if sort_key not in sort_columns:
        sort_key = "name"
    if direction not in {"asc", "desc"}:
        direction = "asc"
    order_expression = sort_columns[sort_key]
    order_expression = order_expression.desc() if direction == "desc" else order_expression.asc()
    account_status = request.args.get("status", "active")
    if account_status not in {"active", "archived"}:
        account_status = "active"
    search = request.args.get("q", "").strip()[:100]
    statement = (
        db.select(StaffProfile)
        .join(StaffProfile.user)
        .order_by(order_expression, StaffProfile.id)
    )
    statement = statement.where(User.is_active.is_(account_status == "active"))
    if search:
        pattern = f"%{search}%"
        statement = statement.where(
            db.or_(
                StaffProfile.name.ilike(pattern),
                StaffProfile.student_number.ilike(pattern),
                User.username.ilike(pattern),
                StaffProfile.email.ilike(pattern),
                StaffProfile.nationality.ilike(pattern),
            )
        )
    pagination = db.paginate(
        statement,
        page=max(1, request.args.get("page", 1, type=int)),
        per_page=25,
        error_out=False,
    )
    profiles = pagination.items
    profile_ids = [profile.id for profile in profiles]
    current_documents = db.session.scalars(
        db.select(StaffDocument)
        .where(StaffDocument.status.in_([
            DocumentStatus.NEEDS_REVIEW,
            DocumentStatus.PENDING_ADMIN,
            DocumentStatus.REJECTED,
            DocumentStatus.CONFIRMED,
        ]), StaffDocument.staff_id.in_(profile_ids or [-1]))
        .order_by(StaffDocument.uploaded_at.desc())
    ).all()
    documents_by_staff = {}
    for group in group_document_sets(current_documents):
        documents_by_staff.setdefault(group["primary"].staff_id, []).append(group)
    return render_template(
        "admin/staff.html",
        profiles=profiles,
        documents_by_staff=documents_by_staff,
        mask_identifier=mask_identifier,
        expiry_state=expiry_state,
        today=local_today(),
        page_labels=PAGE_LABELS,
        countries=active_countries(),
        sort_key=sort_key,
        sort_direction=direction,
        pagination=pagination,
        account_status=account_status,
        search=search,
    )


@bp.get("/admin-accounts")
@role_required(Role.ADMIN)
def admin_accounts():
    account_status = request.args.get("status", "active")
    if account_status not in {"active", "archived"}:
        account_status = "active"
    administrators = db.session.scalars(
        db.select(User)
        .where(User.role == Role.ADMIN, User.is_active.is_(account_status == "active"))
        .order_by(User.created_at, User.username)
    ).all()
    return render_template(
        "admin/admin_accounts.html", administrators=administrators, account_status=account_status
    )


@bp.post("/admin-accounts")
@role_required(Role.ADMIN)
def create_administrator():
    try:
        administrator = create_admin_account(
            username=request.form.get("username", ""),
            display_name=request.form.get("display_name", ""),
            temporary_password=request.form.get("temporary_password", ""),
            password_confirmation=request.form.get("confirm_temporary_password", ""),
            actor_user_id=current_user.id,
        )
        flash(
            f"已建立管理員帳號 {administrator.username}；請安全交付臨時密碼，首次登入後會強制修改。",
            "success",
        )
    except AccountError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.admin_accounts"))


@bp.post("/admin-accounts/<int:user_id>/reset-password")
@role_required(Role.ADMIN)
def reset_administrator_password(user_id: int):
    administrator = db.session.get(User, user_id)
    if administrator is None or administrator.role != Role.ADMIN:
        flash("找不到指定管理員。", "danger")
        return redirect(url_for("admin.admin_accounts"))
    try:
        reset_admin_password(
            user=administrator,
            temporary_password=request.form.get("temporary_password", ""),
            password_confirmation=request.form.get("confirm_temporary_password", ""),
            actor_user_id=current_user.id,
        )
        flash(
            f"已重設管理員 {administrator.username} 的臨時密碼；下次操作時會要求先修改密碼。",
            "success",
        )
    except AccountError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.admin_accounts"))


@bp.post("/admin-accounts/<int:user_id>/delete")
@role_required(Role.ADMIN)
def delete_administrator(user_id: int):
    administrator = db.session.get(User, user_id)
    if administrator is None or administrator.role != Role.ADMIN or not administrator.is_active:
        flash("找不到指定管理員。 / Administrator not found.", "danger")
        return redirect(url_for("admin.admin_accounts"))
    if administrator.id == current_user.id:
        flash("不可刪除目前登入中的管理員帳號。 / You cannot delete your current account.", "danger")
        return redirect(url_for("admin.admin_accounts"))
    active_admins = db.session.scalar(
        db.select(db.func.count()).select_from(User).where(User.role == Role.ADMIN, User.is_active.is_(True))
    )
    if active_admins <= 1:
        flash("系統至少必須保留一個有效管理員帳號。 / At least one active administrator is required.", "danger")
        return redirect(url_for("admin.admin_accounts"))
    administrator.is_active = False
    administrator.archived_at = utc_now()
    administrator.archived_by = current_user.id
    add_audit(current_user.id, "ADMIN_ARCHIVED", "User", administrator.id, f"停用管理員 {administrator.username}")
    db.session.commit()
    flash("管理員帳號已刪除；歷史稽核紀錄仍保留。 / Administrator account removed; audit history was retained.", "success")
    return redirect(url_for("admin.admin_accounts"))


@bp.post("/admin-accounts/<int:user_id>/restore")
@role_required(Role.ADMIN)
def restore_administrator(user_id: int):
    administrator = db.session.get(User, user_id)
    if administrator is None or administrator.role != Role.ADMIN or administrator.is_active:
        flash("找不到已封存管理員。 / Archived administrator not found.", "danger")
    else:
        administrator.is_active = True
        administrator.archived_at = None
        administrator.archived_by = None
        add_audit(current_user.id, "ADMIN_RESTORED", "User", administrator.id, f"復原管理員 {administrator.username}")
        db.session.commit()
        flash("管理員帳號已復原。 / Administrator restored.", "success")
    return redirect(url_for("admin.admin_accounts", status="archived"))


@bp.post("/staff")
@role_required(Role.ADMIN)
def create_staff():
    try:
        nationality = canonical_country_name(request.form.get("nationality", ""))
        profile = create_student_account(
            username=request.form.get("username", ""),
            temporary_password=request.form.get("temporary_password", ""),
            password_confirmation=request.form.get("confirm_temporary_password", ""),
            name=request.form.get("name", ""),
            student_number=request.form.get("student_number", ""),
            email=request.form.get("email", ""),
            phone=request.form.get("phone", ""),
            nationality=nationality,
            actor_user_id=current_user.id,
        )
        flash(
            f"已建立 {profile.name} 的工讀生帳號；請安全交付臨時密碼，首次登入後會強制修改。",
            "success",
        )
    except (AccountError, ValueError) as exc:
        db.session.rollback()
        flash(exc.message if isinstance(exc, AccountError) else str(exc), "danger")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:staff_id>/reset-password")
@role_required(Role.ADMIN)
def reset_staff_password(staff_id: int):
    profile = db.session.get(StaffProfile, staff_id)
    if profile is None:
        flash("找不到指定工讀生。", "danger")
        return redirect(url_for("admin.staff"))
    try:
        reset_student_password(
            profile=profile,
            temporary_password=request.form.get("temporary_password", ""),
            password_confirmation=request.form.get("confirm_temporary_password", ""),
            actor_user_id=current_user.id,
        )
        flash(
            f"已重設 {profile.name} 的臨時密碼；下次操作時會要求先修改密碼。",
            "success",
        )
    except AccountError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:staff_id>/delete")
@role_required(Role.ADMIN)
def delete_staff(staff_id: int):
    profile = db.session.get(StaffProfile, staff_id)
    if profile is None or not profile.user.is_active:
        flash("找不到指定工讀生。 / Student not found.", "danger")
        return redirect(url_for("admin.staff"))
    future_shift = db.session.scalar(
        db.select(Shift.id)
        .where(
            Shift.staff_id == profile.id,
            Shift.shift_date >= local_today(),
            Shift.status.in_([ShiftStatus.SCHEDULED, ShiftStatus.ON_LEAVE]),
        )
        .limit(1)
    )
    active_leave = db.session.scalar(
        db.select(LeaveRequest.id)
        .where(LeaveRequest.staff_id == profile.id, LeaveRequest.status == LeaveStatus.PENDING)
        .limit(1)
    )
    active_swap = db.session.scalar(
        db.select(SwapRequest.id)
        .where(
            db.or_(SwapRequest.requester_id == profile.id, SwapRequest.target_staff_id == profile.id),
            SwapRequest.admin_status.in_([SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING]),
        )
        .limit(1)
    )
    if future_shift or active_leave or active_swap:
        flash(
            "此工讀生仍有未來排班或進行中的請假／換班，請先完成或刪除相關資料。 / Resolve future shifts and active requests first.",
            "danger",
        )
        return redirect(url_for("admin.staff"))
    profile.user.is_active = False
    profile.user.archived_at = utc_now()
    profile.user.archived_by = current_user.id
    add_audit(current_user.id, "STAFF_ARCHIVED", "StaffProfile", profile.id, f"停用工讀生 {profile.student_number}")
    db.session.commit()
    flash("工讀生帳號已刪除；歷史排班、報表及文件稽核仍保留。 / Student account removed; history was retained.", "success")
    return redirect(url_for("admin.staff"))


@bp.post("/staff/<int:staff_id>/restore")
@role_required(Role.ADMIN)
def restore_staff(staff_id: int):
    profile = db.session.get(StaffProfile, staff_id)
    if profile is None or profile.user.is_active:
        flash("找不到已封存工讀生。 / Archived student not found.", "danger")
    else:
        profile.user.is_active = True
        profile.user.archived_at = None
        profile.user.archived_by = None
        add_audit(current_user.id, "STAFF_RESTORED", "StaffProfile", profile.id, f"復原工讀生 {profile.student_number}")
        db.session.commit()
        flash("工讀生帳號已復原，原排班與歷史資料仍完整保留。 / Student restored with history.", "success")
    return redirect(url_for("admin.staff", status="archived"))


@bp.get("/documents")
@role_required(Role.ADMIN)
def documents_page():
    group_statement = (
        db.select(
            StaffDocument.document_set_id,
            db.func.max(StaffDocument.uploaded_at).label("latest_upload"),
        )
        .where(StaffDocument.status != DocumentStatus.DELETED)
        .group_by(StaffDocument.document_set_id)
        .order_by(db.desc("latest_upload"))
    )
    pagination = db.paginate(
        group_statement,
        page=max(1, request.args.get("page", 1, type=int)),
        per_page=25,
        error_out=False,
    )
    set_ids = list(pagination.items)
    documents = db.session.scalars(
        db.select(StaffDocument)
        .options(joinedload(StaffDocument.staff), joinedload(StaffDocument.draft))
        .where(StaffDocument.document_set_id.in_(set_ids or ["-"]))
        .order_by(StaffDocument.uploaded_at.desc())
    ).all()
    policy = get_retention_policy()
    cleanup_logs = db.session.scalars(
        db.select(AuditLog)
        .where(AuditLog.action == "DOCUMENT_PURGED_BY_RETENTION")
        .order_by(AuditLog.created_at.desc())
        .limit(20)
    ).all()
    return render_template(
        "admin/documents.html",
        document_groups=group_document_sets(documents),
        policy=policy,
        default_retention_days=current_app.config["DOCUMENT_DEFAULT_RETENTION_DAYS"],
        cleanup_logs=cleanup_logs,
        key_primary_path=current_app.config["DOCUMENT_KEY_PRIMARY_PATH"],
        key_backup_path=current_app.config["DOCUMENT_KEY_BACKUP_PATH"],
        page_labels=PAGE_LABELS,
        pagination=pagination,
    )


@bp.post("/documents/<int:document_id>/review")
@role_required(Role.ADMIN)
def review_staff_document(document_id: int):
    document = db.session.get(StaffDocument, document_id)
    if document is None:
        flash("找不到指定證件。 / Document not found.", "danger")
        return redirect(url_for("admin.documents_page"))
    try:
        review_document_set(
            document=document,
            decision=request.form.get("decision", ""),
            reason=request.form.get("review_reason", ""),
            fields_confirmed=request.form.get("fields_confirmed") == "yes",
            actor_user_id=current_user.id,
        )
        if request.form.get("decision") == "APPROVE":
            flash("證件已核准，正式個人資料已更新。 / Document approved and profile updated.", "success")
        else:
            flash("證件已退回學生修正。 / Document returned to the student.", "warning")
    except DocumentError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.documents_page"))


@bp.post("/documents/retention")
@role_required(Role.ADMIN)
def update_document_retention():
    try:
        retention_days = int(request.form.get("retention_days", ""))
        cleanup_time = time.fromisoformat(request.form.get("cleanup_time", ""))
    except (TypeError, ValueError):
        flash("保存天數或清理時間格式錯誤。", "danger")
        return redirect(url_for("admin.documents_page"))
    if retention_days < 0 or retention_days > 3650:
        flash("保存天數必須介於 0–3650 天；0 代表不自動清理。", "danger")
        return redirect(url_for("admin.documents_page"))
    save_retention_policy(
        retention_days=retention_days,
        cleanup_hour=cleanup_time.hour,
        cleanup_minute=cleanup_time.minute,
        actor_user_id=current_user.id,
    )
    flash("文件保存期限與每日清理時間已更新。", "success")
    return redirect(url_for("admin.documents_page"))


@bp.post("/documents/cleanup")
@role_required(Role.ADMIN)
def run_document_cleanup():
    deleted_ids = cleanup_expired_documents(actor_user_id=current_user.id)
    flash(f"清理完成，共刪除 {len(deleted_ids)} 份到期影像；稽核資料已保留。", "success")
    return redirect(url_for("admin.documents_page"))


@bp.get("/documents/<int:document_id>/file")
@role_required(Role.ADMIN)
def view_staff_document(document_id: int):
    document = db.session.get(StaffDocument, document_id)
    if document is None:
        return "", 404
    if document.status == DocumentStatus.DELETED:
        return "", 404
    try:
        response = send_file(BytesIO(read_document(document)), mimetype=document.mime_type, max_age=0)
    except DocumentError:
        return "", 404
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@bp.get("/documents/<int:document_id>/download")
@role_required(Role.ADMIN)
def download_staff_document(document_id: int):
    document = db.session.get(StaffDocument, document_id)
    if document is None or document.status == DocumentStatus.DELETED:
        return "", 404
    label = "residence-permit" if document.document_type.value == "RESIDENCE_PERMIT" else "work-permit"
    page_label = document.page_kind.value.lower().replace("_", "-")
    try:
        response = send_file(
            BytesIO(read_document(document)),
            mimetype=document.mime_type,
            as_attachment=True,
            download_name=f"{document.staff.student_number}-{label}-{page_label}-{document.uploaded_at:%Y%m%d}.jpg",
            max_age=0,
        )
    except DocumentError:
        return "", 404
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@bp.get("/document-sets/<string:set_id>/download")
@role_required(Role.ADMIN)
def download_staff_document_set(set_id: str):
    documents = db.session.scalars(
        db.select(StaffDocument)
        .where(
            StaffDocument.document_set_id == set_id,
            StaffDocument.status != DocumentStatus.DELETED,
        )
        .order_by(StaffDocument.id)
    ).all()
    if not documents:
        return "", 404
    output = BytesIO()
    try:
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for index, document in enumerate(documents, start=1):
                archive.writestr(
                    f"{index:02d}-{document.page_kind.value.lower()}.jpg",
                    read_document(document),
                )
    except DocumentError:
        return "", 404
    add_audit(
        current_user.id,
        "DOCUMENT_SET_DOWNLOADED",
        "StaffDocument",
        documents[0].id,
        f"下載{documents[0].document_type.value}整組文件，共 {len(documents)} 頁",
    )
    db.session.commit()
    output.seek(0)
    label = "residence-permit" if documents[0].document_type.value == "RESIDENCE_PERMIT" else "work-permit"
    response = send_file(
        output,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{documents[0].staff.student_number}-{label}-{documents[0].uploaded_at:%Y%m%d}.zip",
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@bp.get("/payroll")
@role_required(Role.ADMIN)
def payroll():
    profiles = db.session.scalars(db.select(StaffProfile).order_by(StaffProfile.name)).all()
    setting = get_payroll_setting(local_today())
    return render_template("admin/payroll.html", profiles=profiles, setting=setting)


@bp.post("/staff/<int:staff_id>")
@role_required(Role.ADMIN)
def update_staff(staff_id: int):
    profile = db.session.get(StaffProfile, staff_id)
    if profile is None:
        flash("找不到指定工讀生。", "danger")
        return redirect(url_for("admin.staff"))
    name = request.form.get("name", "").strip()
    student_number = request.form.get("student_number", "").strip().upper()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    try:
        nationality = canonical_country_name(request.form.get("nationality", ""))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.staff"))
    if not name or len(name) > 100:
        flash("姓名需為 1–100 字。", "danger")
        return redirect(url_for("admin.staff"))
    if not re.fullmatch(r"[A-Z0-9_-]{3,30}", student_number):
        flash("學號需為 3–30 位英數字、底線或連字號。", "danger")
        return redirect(url_for("admin.staff"))
    duplicate = db.session.scalar(
        db.select(StaffProfile).where(
            StaffProfile.student_number == student_number,
            StaffProfile.id != profile.id,
        )
    )
    if duplicate:
        flash("此學號已由其他工讀生使用。", "danger")
        return redirect(url_for("admin.staff"))
    if len(email) > 255 or (email and ("@" not in email or "." not in email.rsplit("@", 1)[-1])):
        flash("Email 格式錯誤。", "danger")
        return redirect(url_for("admin.staff"))
    if len(phone) > 30:
        flash("聯絡電話或國籍格式錯誤。", "danger")
        return redirect(url_for("admin.staff"))
    profile.name = name
    profile.student_number = student_number
    profile.email = email or None
    profile.phone = phone or None
    profile.nationality = nationality
    add_audit(
        current_user.id,
        "STAFF_PROFILE_UPDATED",
        "StaffProfile",
        profile.id,
        f"更新工讀生基本資料與學號 {student_number}",
    )
    db.session.commit()
    flash("工讀生基本資料與學號已更新。", "success")
    return redirect(url_for("admin.staff"))


@bp.get("/reports")
@role_required(Role.ADMIN)
def reports_page():
    return render_template("admin/reports.html", default_month=local_today().strftime("%Y-%m"))


def _report_month():
    return month_bounds(request.args.get("month", local_today().strftime("%Y-%m")))


def _download_report(data: bytes, filename: str, mimetype: str, report_code: str, start: date):
    add_audit(
        current_user.id,
        "REPORT_DOWNLOADED",
        "MonthlyReport",
        start.year * 100 + start.month,
        f"下載 {report_code} 報表，月份 {start:%Y-%m}",
    )
    db.session.commit()
    return send_file(BytesIO(data), mimetype=mimetype, as_attachment=True, download_name=filename, max_age=0)


@bp.get("/reports/monthly-hours.xlsx")
@role_required(Role.ADMIN)
def monthly_hours_xlsx():
    try:
        start, end = _report_month()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.reports_page"))
    filename = f"{start.year - 1911}年{start.month}月工讀生約用時數表.xlsx"
    return _download_report(
        build_monthly_hours_workbook(start, end),
        filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "MONTHLY_HOURS_CALENDAR",
        start,
    )


@bp.get("/reports/daily-hours-matrix.xlsx")
@role_required(Role.ADMIN)
def daily_hours_matrix_xlsx():
    try:
        start, end = _report_month()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.reports_page"))
    filename = f"{start.year - 1911}年{start.month}月全體工讀生每日時數表.xlsx"
    return _download_report(
        build_daily_hours_matrix_workbook(start, end),
        filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "DAILY_HOURS_MATRIX",
        start,
    )


@bp.get("/reports/shifts.csv")
@role_required(Role.ADMIN)
def shift_detail_report():
    try:
        start, end = _report_month()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.reports_page"))
    return _download_report(shift_detail_csv(start, end), f"{start:%Y-%m}-排班明細.csv", "text/csv; charset=utf-8", "SHIFT_DETAIL", start)


@bp.get("/reports/payroll.csv")
@role_required(Role.ADMIN)
def payroll_cost_report():
    try:
        start, end = _report_month()
        data = payroll_cost_csv(start, end)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.reports_page"))
    return _download_report(data, f"{start:%Y-%m}-薪資與雇主成本.csv", "text/csv; charset=utf-8", "PAYROLL_COST", start)


@bp.get("/reports/workflows.csv")
@role_required(Role.ADMIN)
def workflow_history_report():
    try:
        start, end = _report_month()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.reports_page"))
    return _download_report(workflow_history_csv(start, end), f"{start:%Y-%m}-請假換班紀錄.csv", "text/csv; charset=utf-8", "WORKFLOW_HISTORY", start)


@bp.get("/reports/document-expiry.csv")
@role_required(Role.ADMIN)
def document_expiry_report():
    today = local_today()
    return _download_report(document_expiry_csv(), f"{today:%Y-%m-%d}-證件效期清單.csv", "text/csv; charset=utf-8", "DOCUMENT_EXPIRY", today.replace(day=1))


@bp.get("/requests")
@role_required(Role.ADMIN)
def requests_page():
    filter_scope = request.args.get("scope", "ALL").upper()
    filter_month = request.args.get("month", local_today().strftime("%Y-%m"))
    if filter_scope not in {"ALL", "MONTH"}:
        filter_scope = "ALL"
    shift_details = joinedload(Shift.shift_type).joinedload(ShiftType.work_location)
    leave_statement = (
        db.select(LeaveRequest)
        .options(joinedload(LeaveRequest.staff), joinedload(LeaveRequest.shift).options(shift_details))
        .order_by(LeaveRequest.created_at.desc())
    )
    swap_statement = (
        db.select(SwapRequest)
        .options(
            joinedload(SwapRequest.requester),
            joinedload(SwapRequest.target_staff),
            joinedload(SwapRequest.requester_shift).options(shift_details),
            joinedload(SwapRequest.target_shift).options(shift_details),
        )
        .order_by(SwapRequest.created_at.desc())
    )
    if filter_scope == "MONTH":
        try:
            month_start, month_end = month_bounds(filter_month)
        except ValueError:
            filter_month = local_today().strftime("%Y-%m")
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
    page = max(1, request.args.get("page", 1, type=int))
    leave_pagination = db.paginate(leave_statement, page=page, per_page=50, error_out=False)
    swap_pagination = db.paginate(swap_statement, page=page, per_page=50, error_out=False)
    leave_requests = leave_pagination.items
    swap_requests = swap_pagination.items
    return render_template(
        "admin/requests.html",
        leave_requests=leave_requests,
        swap_requests=swap_requests,
        filter_scope=filter_scope,
        filter_month=filter_month,
        history_page=page,
        history_pages=max(leave_pagination.pages, swap_pagination.pages),
    )


@bp.post("/leave-requests/<int:request_id>/review")
@role_required(Role.ADMIN)
def review_leave(request_id: int):
    request_item = db.session.get(LeaveRequest, request_id)
    if request_item is None:
        flash("找不到指定請假申請。", "danger")
        return redirect(url_for("admin.requests_page"))
    decision = request.form.get("decision", "")
    review_note = request.form.get("review_note", "").strip()
    if len(review_note) > 1000:
        flash("審核備註不可超過 1000 字。", "danger")
        return redirect(url_for("admin.requests_page"))
    try:
        review_leave_request(
            request_item=request_item,
            decision=decision,
            review_note=review_note,
            actor_user_id=current_user.id,
        )
        flash("請假申請已核准，原班已標示為缺員。" if decision == "APPROVE" else "請假申請已拒絕。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.requests_page"))


@bp.post("/swap-requests/<int:request_id>/review")
@role_required(Role.ADMIN)
def review_swap(request_id: int):
    request_item = db.session.get(SwapRequest, request_id)
    if request_item is None:
        flash("找不到指定換班申請。", "danger")
        return redirect(url_for("admin.requests_page"))
    decision = request.form.get("decision", "")
    review_note = request.form.get("review_note", "").strip()
    if len(review_note) > 1000:
        flash("審核備註不可超過 1000 字。", "danger")
        return redirect(url_for("admin.requests_page"))
    try:
        review_swap_request(
            request_item=request_item,
            decision=decision,
            review_note=review_note,
            actor_user_id=current_user.id,
        )
        flash("換班已核准並更新正式排班。" if decision == "APPROVE" else "換班申請已拒絕。", "success")
    except WorkflowError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("admin.requests_page"))


def parse_calendar_date(value: str | None, field_name: str) -> date:
    if not value:
        raise ValueError(f"缺少{field_name}。")
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"{field_name}格式錯誤。") from exc


def get_assignment(payload: dict) -> tuple[date, ShiftType, StaffProfile]:
    try:
        shift_date = date.fromisoformat(str(payload.get("shift_date", "")))
        shift_type_id = int(payload.get("shift_type_id"))
        staff_id = int(payload.get("staff_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("日期、班別與工讀生皆為必填。") from exc

    shift_type = db.session.get(ShiftType, shift_type_id)
    staff = db.session.get(StaffProfile, staff_id)
    if shift_type is None or not shift_type.is_active:
        raise ValueError("指定的班別不存在或已停用。")
    if staff is None or not staff.user.is_active:
        raise ValueError("指定的工讀生不存在或已停用。")
    return shift_date, shift_type, staff


def api_error(message: str, status: int = 400, code: str = "VALIDATION_ERROR"):
    return jsonify({"error": {"code": code, "message": message}}), status


@bp.post("/api/locations")
@role_required(Role.ADMIN)
def create_location_api():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).strip().upper()
    name = str(payload.get("name", "")).strip()
    name_en = str(payload.get("name_en", "")).strip()
    color = str(payload.get("color", "#1556a3")).strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", code):
        return api_error("地點代碼需為 2–40 位大寫英數字或底線，且以英文字母開頭。")
    if not name or len(name) > 100:
        return api_error("請輸入 1–100 字的地點名稱。")
    if not name_en or len(name_en) > 100:
        return api_error("請輸入 1–100 字的地點英文名稱。")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        return api_error("地點顏色格式錯誤。")
    duplicate = db.session.scalar(
        db.select(WorkLocation).where(
            db.or_(WorkLocation.code == code, WorkLocation.name == name)
        )
    )
    if duplicate:
        return api_error("地點代碼或名稱已存在。", 409, "DUPLICATE_LOCATION")
    max_order = db.session.scalar(db.select(db.func.max(WorkLocation.display_order))) or 0
    location = WorkLocation(
        code=code,
        name=name,
        name_en=name_en,
        color=color.lower(),
        display_order=max_order + 10,
    )
    db.session.add(location)
    db.session.commit()
    return jsonify(
        {
            "id": location.id,
            "code": location.code,
            "name": location.name,
            "nameEn": location.name_en,
            "color": location.color,
            "displayOrder": location.display_order,
        }
    ), 201


@bp.put("/api/locations/<int:location_id>")
@role_required(Role.ADMIN)
def update_location_api(location_id: int):
    location = db.session.get(WorkLocation, location_id)
    if location is None or not location.is_active:
        return api_error("找不到指定工作地點。", 404, "NOT_FOUND")
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).strip().upper()
    name = str(payload.get("name", "")).strip()
    name_en = str(payload.get("name_en", "")).strip()
    color = str(payload.get("color", "#1556a3")).strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", code):
        return api_error("地點代碼需為 2–40 位大寫英數字或底線，且以英文字母開頭。")
    if not name or len(name) > 100:
        return api_error("請輸入 1–100 字的地點名稱。")
    if not name_en or len(name_en) > 100:
        return api_error("請輸入 1–100 字的地點英文名稱。")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        return api_error("地點顏色格式錯誤。")
    duplicate = db.session.scalar(
        db.select(WorkLocation).where(
            WorkLocation.id != location.id,
            db.or_(WorkLocation.code == code, WorkLocation.name == name),
        )
    )
    if duplicate:
        return api_error("地點代碼或名稱已存在。", 409, "DUPLICATE_LOCATION")
    location.code = code
    location.name = name
    location.name_en = name_en
    location.color = color.lower()
    db.session.commit()
    return jsonify({"id": location.id, "code": location.code, "name": location.name, "nameEn": location.name_en, "color": location.color})


@bp.delete("/api/locations/<int:location_id>")
@role_required(Role.ADMIN)
def delete_location_api(location_id: int):
    location = db.session.get(WorkLocation, location_id)
    if location is None or not location.is_active:
        return api_error("找不到指定工作地點。 / Location not found.", 404, "NOT_FOUND")
    location.is_active = False
    disabled_types = 0
    for shift_type in location.shift_types:
        if shift_type.is_active:
            shift_type.is_active = False
            disabled_types += 1
    add_audit(
        current_user.id,
        "LOCATION_ARCHIVED",
        "WorkLocation",
        location.id,
        f"停用地點 {location.code} 及 {disabled_types} 個班別",
    )
    db.session.commit()
    return jsonify({"archived": True, "disabledShiftTypes": disabled_types})


@bp.post("/api/shift-types")
@role_required(Role.ADMIN)
def create_shift_type_api():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).strip().upper()
    name = str(payload.get("name", "")).strip()
    name_en = str(payload.get("name_en", "")).strip()
    try:
        location_id = int(payload.get("location_id"))
        start_time = time.fromisoformat(str(payload.get("start_time", "")))
        end_time = time.fromisoformat(str(payload.get("end_time", "")))
        default_hours = Decimal(str(payload.get("default_hours", "")))
    except (TypeError, ValueError, InvalidOperation) as exc:
        return api_error("地點、開始時間、結束時間與時數皆為必填。")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", code):
        return api_error("班別代碼需為 2–40 位大寫英數字或底線。")
    if not name or len(name) > 100:
        return api_error("請輸入 1–100 字的班別名稱。")
    if not name_en or len(name_en) > 100:
        return api_error("請輸入 1–100 字的班別英文名稱。")
    actual_hours = Decimal(str((datetime.combine(date.min, end_time) - datetime.combine(date.min, start_time)).total_seconds() / 3600))
    if start_time >= end_time or default_hours <= 0 or default_hours > Decimal("8") or actual_hours > Decimal("8"):
        return api_error("班別與實際排班時間皆不得超過 8 小時。 / Shift hours cannot exceed 8 hours.")
    location = db.session.get(WorkLocation, location_id)
    if location is None or not location.is_active:
        return api_error("指定地點不存在或已停用。")
    if db.session.scalar(db.select(ShiftType).where(ShiftType.code == code)):
        return api_error("班別代碼已存在。", 409, "DUPLICATE_SHIFT_TYPE")
    max_order = db.session.scalar(
        db.select(db.func.max(ShiftType.display_order)).where(
            ShiftType.location_id == location.id
        )
    ) or location.display_order * 10
    shift_type = ShiftType(
        code=code,
        name=name,
        name_en=name_en,
        location_id=location.id,
        start_time=start_time,
        end_time=end_time,
        default_hours=default_hours,
        display_order=max_order + 10,
    )
    db.session.add(shift_type)
    db.session.commit()
    return jsonify({"id": shift_type.id, "code": shift_type.code, "name": shift_type.name, "nameEn": shift_type.name_en}), 201


@bp.put("/api/shift-types/<int:shift_type_id>")
@role_required(Role.ADMIN)
def update_shift_type_api(shift_type_id: int):
    shift_type = db.session.get(ShiftType, shift_type_id)
    if shift_type is None or not shift_type.is_active:
        return api_error("找不到指定班別。", 404, "NOT_FOUND")
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).strip().upper()
    name = str(payload.get("name", "")).strip()
    name_en = str(payload.get("name_en", "")).strip()
    try:
        location_id = int(payload.get("location_id"))
        start_value = time.fromisoformat(str(payload.get("start_time", "")))
        end_value = time.fromisoformat(str(payload.get("end_time", "")))
        default_hours = Decimal(str(payload.get("default_hours", "")))
    except (TypeError, ValueError, InvalidOperation):
        return api_error("地點、開始時間、結束時間與時數皆為必填。")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,39}", code):
        return api_error("班別代碼需為 2–40 位大寫英數字或底線。")
    if not name or len(name) > 100:
        return api_error("請輸入 1–100 字的班別名稱。")
    if not name_en or len(name_en) > 100:
        return api_error("請輸入 1–100 字的班別英文名稱。")
    actual_hours = Decimal(str((datetime.combine(date.min, end_value) - datetime.combine(date.min, start_value)).total_seconds() / 3600))
    if start_value >= end_value or default_hours <= 0 or default_hours > Decimal("8") or actual_hours > Decimal("8"):
        return api_error("班別與實際排班時間皆不得超過 8 小時。 / Shift hours cannot exceed 8 hours.")
    location = db.session.get(WorkLocation, location_id)
    if location is None or not location.is_active:
        return api_error("指定地點不存在或已停用。")
    duplicate = db.session.scalar(
        db.select(ShiftType).where(ShiftType.id != shift_type.id, ShiftType.code == code)
    )
    if duplicate:
        return api_error("班別代碼已存在。", 409, "DUPLICATE_SHIFT_TYPE")

    shift_type.code = code
    shift_type.name = name
    shift_type.name_en = name_en
    shift_type.work_location = location
    shift_type.start_time = start_value
    shift_type.end_time = end_value
    shift_type.default_hours = default_hours
    try:
        db.session.flush()
        affected = db.session.scalars(
            db.select(Shift).where(
                Shift.shift_type_id == shift_type.id,
                Shift.status == ShiftStatus.SCHEDULED,
            )
        ).all()
        for shift in affected:
            validate_shift_assignment(
                shift_date=shift.shift_date,
                shift_type=shift_type,
                staff=shift.staff,
                exclude_shift_id=shift.id,
                allow_location_overlap=True,
            )
        db.session.commit()
    except SchedulingConflict as exc:
        db.session.rollback()
        return api_error(
            f"修改後會造成既有排班衝突：{exc.message}",
            409,
            exc.code,
        )
    return jsonify({"id": shift_type.id, "code": shift_type.code, "name": shift_type.name, "nameEn": shift_type.name_en})


@bp.delete("/api/shift-types/<int:shift_type_id>")
@role_required(Role.ADMIN)
def delete_shift_type_api(shift_type_id: int):
    shift_type = db.session.get(ShiftType, shift_type_id)
    if shift_type is None or not shift_type.is_active:
        return api_error("找不到指定班別。 / Shift type not found.", 404, "NOT_FOUND")
    shift_type.is_active = False
    add_audit(
        current_user.id,
        "SHIFT_TYPE_ARCHIVED",
        "ShiftType",
        shift_type.id,
        f"停用班別 {shift_type.code}",
    )
    db.session.commit()
    return jsonify({"archived": True})


def decimal_field(payload: dict, name: str, *, minimum: Decimal, maximum: Decimal) -> Decimal:
    try:
        value = Decimal(str(payload.get(name, "")))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{name} 格式錯誤。") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} 超出允許範圍。")
    return value


@bp.post("/api/payroll-settings")
@role_required(Role.ADMIN)
def save_payroll_setting_api():
    payload = request.get_json(silent=True) or {}
    try:
        effective_date = date.fromisoformat(str(payload.get("effective_date", "")))
        hourly_wage = decimal_field(
            payload, "default_hourly_wage", minimum=Decimal("1"), maximum=Decimal("10000")
        )
        if effective_date >= date(2026, 1, 1) and hourly_wage < Decimal("196"):
            raise ValueError("2026 年適用時薪不可低於法定最低時薪 196 元。")
        percentage_fields = {
            "labor_insurance_rate": (Decimal("0"), Decimal("30")),
            "employment_insurance_rate": (Decimal("0"), Decimal("10")),
            "employer_labor_share": (Decimal("0"), Decimal("100")),
            "occupational_accident_rate": (Decimal("0"), Decimal("10")),
            "health_insurance_rate": (Decimal("0"), Decimal("20")),
            "employer_health_share": (Decimal("0"), Decimal("100")),
            "supplementary_health_rate": (Decimal("0"), Decimal("20")),
            "employer_pension_rate": (Decimal("6"), Decimal("20")),
        }
        rates = {
            name: decimal_field(payload, name, minimum=bounds[0], maximum=bounds[1])
            / Decimal("100")
            for name, bounds in percentage_fields.items()
        }
        average_dependents = decimal_field(
            payload, "average_dependents", minimum=Decimal("0"), maximum=Decimal("10")
        )
    except ValueError as exc:
        return api_error(str(exc))

    setting = db.session.scalar(
        db.select(PayrollSetting).where(PayrollSetting.effective_date == effective_date)
    )
    if setting is None:
        setting = PayrollSetting(effective_date=effective_date)
        db.session.add(setting)
    setting.default_hourly_wage = hourly_wage
    setting.average_dependents = average_dependents
    for field, value in rates.items():
        setattr(setting, field, value)
    db.session.commit()
    return jsonify({"message": "薪資與保險費率設定已儲存。", "id": setting.id})


@bp.put("/api/staff/<int:staff_id>/payroll")
@role_required(Role.ADMIN)
def update_staff_payroll_api(staff_id: int):
    profile = db.session.get(StaffProfile, staff_id)
    if profile is None:
        return api_error("找不到指定工讀生。", 404, "NOT_FOUND")
    payload = request.get_json(silent=True) or {}
    try:
        hourly_text = str(payload.get("hourly_wage", "")).strip()
        profile.hourly_wage = (
            None
            if not hourly_text
            else decimal_field(payload, "hourly_wage", minimum=Decimal("196"), maximum=Decimal("10000"))
        )
        for field in ("labor_insured_salary", "health_insured_salary", "pension_salary"):
            text = str(payload.get(field, "")).strip()
            setattr(
                profile,
                field,
                None
                if not text
                else decimal_field(payload, field, minimum=Decimal("0"), maximum=Decimal("1000000")),
            )
        for field in (
            "labor_insurance_enabled",
            "employment_insurance_enabled",
            "health_insurance_enabled",
            "labor_pension_enabled",
        ):
            setattr(profile, field, bool(payload.get(field)))
    except ValueError as exc:
        return api_error(str(exc))
    db.session.commit()
    return jsonify({"message": f"{profile.name} 的薪資與投保設定已更新。"})


@bp.get("/api/payroll")
@role_required(Role.ADMIN)
def payroll_report_api():
    try:
        start, end = month_bounds(request.args.get("month", ""))
    except ValueError as exc:
        return api_error(str(exc))
    setting = get_payroll_setting(start)
    if setting is None:
        return api_error("此月份尚未設定薪資與保險費率。", 409, "PAYROLL_NOT_CONFIGURED")

    hours_rows = db.session.execute(
        db.select(Shift.staff_id, db.func.sum(ShiftType.default_hours))
        .select_from(Shift)
        .join(ShiftType)
        .where(
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
            Shift.shift_date >= start,
            Shift.shift_date < end,
        )
        .group_by(Shift.staff_id)
    ).all()
    hours_by_staff = {staff_id: Decimal(str(hours or 0)) for staff_id, hours in hours_rows}
    profiles = db.session.scalars(db.select(StaffProfile).order_by(StaffProfile.name)).all()
    rows = [
        calculate_staff_cost(
            profile=profile,
            hours=hours_by_staff.get(profile.id, Decimal("0")),
            setting=setting,
        )
        for profile in profiles
    ]
    gross_total = sum((Decimal(str(row["gross_wage"])) for row in rows), Decimal("0"))
    health_salary_total = sum(
        (
            profile.health_insured_salary or Decimal("0")
            for profile in profiles
            if profile.health_insurance_enabled
        ),
        Decimal("0"),
    )
    supplementary_health = money(
        max(gross_total - health_salary_total, Decimal("0"))
        * setting.supplementary_health_rate
    )
    benefits_total = sum(row["employer_benefits"] for row in rows)
    return jsonify(
        {
            "month": start.strftime("%Y-%m"),
            "effective_date": setting.effective_date.isoformat(),
            "rows": rows,
            "totals": {
                "hours": sum(row["hours"] for row in rows),
                "gross_wage": sum(row["gross_wage"] for row in rows),
                "employer_benefits": benefits_total,
                "supplementary_health": int(supplementary_health),
                "employer_total": sum(row["employer_total"] for row in rows)
                + int(supplementary_health),
            },
        }
    )


@bp.get("/api/shifts")
@role_required(Role.ADMIN)
def shift_events():
    try:
        start = parse_calendar_date(request.args.get("start"), "開始日期")
        end = parse_calendar_date(request.args.get("end"), "結束日期")
    except ValueError as exc:
        return api_error(str(exc))

    statement = (
        db.select(Shift)
        .options(
            joinedload(Shift.shift_type).joinedload(ShiftType.work_location),
            joinedload(Shift.staff),
            joinedload(Shift.series),
        )
        .join(ShiftType)
        .join(WorkLocation)
        .where(
            Shift.status.in_([ShiftStatus.SCHEDULED, ShiftStatus.ON_LEAVE]),
            Shift.shift_date >= start,
            Shift.shift_date < end,
        )
        .order_by(Shift.shift_date, ShiftType.display_order)
    )
    location = request.args.get("location", "ALL")
    if location != "ALL":
        statement = statement.where(WorkLocation.code == location)
    staff_id = request.args.get("staff_id", type=int)
    if staff_id:
        statement = statement.where(Shift.staff_id == staff_id)

    shifts = db.session.scalars(statement).all()
    annotations = workflow_annotations(shifts)
    return jsonify([add_annotations(shift_to_event(shift), annotations.get(shift.id, [])) for shift in shifts])


@bp.post("/api/shifts")
@role_required(Role.ADMIN)
def create_shift_api():
    payload = request.get_json(silent=True) or {}
    try:
        shift_date, shift_type, staff = get_assignment(payload)
        publication_status = ShiftPublicationStatus(
            str(payload.get("publication_status", ShiftPublicationStatus.PUBLISHED.value)).upper()
        )
        if payload.get("repeat_weekly") is True:
            recurrence_end = date.fromisoformat(str(payload.get("recurrence_end", "")))
            shifts = create_weekly_shift_series(
                starts_on=shift_date,
                ends_on=recurrence_end,
                shift_type=shift_type,
                staff=staff,
                actor_id=current_user.id,
                allow_location_overlap=payload.get("allow_location_overlap") is True,
                publication_status=publication_status,
            )
            add_audit(
                current_user.id,
                "SHIFT_SERIES_CREATED",
                "ShiftSeries",
                shifts[0].series_id,
                f"建立每週重複排班，共 {len(shifts)} 筆",
            )
            db.session.commit()
            return jsonify({"count": len(shifts), "seriesId": shifts[0].series_id}), 201
        shift = create_shift(
            shift_date=shift_date,
            shift_type=shift_type,
            staff=staff,
            actor_id=current_user.id,
            allow_location_overlap=payload.get("allow_location_overlap") is True,
            publication_status=publication_status,
        )
    except SchedulingConflict as exc:
        db.session.rollback()
        return api_error(exc.message, 409, exc.code)
    except ValueError as exc:
        db.session.rollback()
        return api_error(str(exc))
    except IntegrityError:
        db.session.rollback()
        return api_error("排班資料發生重複或一致性錯誤。", 409, "SHIFT_INTEGRITY_ERROR")
    return jsonify(shift_to_event(shift)), 201


@bp.put("/api/shifts/<int:shift_id>")
@role_required(Role.ADMIN)
def update_shift_api(shift_id: int):
    shift = db.session.get(Shift, shift_id)
    if shift is None or shift.status != ShiftStatus.SCHEDULED:
        return api_error("找不到指定排班。", 404, "NOT_FOUND")
    payload = request.get_json(silent=True) or {}
    try:
        shift_date, shift_type, staff = get_assignment(payload)
        publication_status = ShiftPublicationStatus(
            str(payload.get("publication_status", shift.publication_status.value)).upper()
        )
        shift = update_shift(
            shift,
            shift_date=shift_date,
            shift_type=shift_type,
            staff=staff,
            allow_location_overlap=payload.get("allow_location_overlap") is True,
            publication_status=publication_status,
            actor_id=current_user.id,
        )
    except SchedulingConflict as exc:
        db.session.rollback()
        return api_error(exc.message, 409, exc.code)
    except ValueError as exc:
        db.session.rollback()
        return api_error(str(exc))
    except IntegrityError:
        db.session.rollback()
        return api_error("排班資料發生重複或一致性錯誤。", 409, "SHIFT_INTEGRITY_ERROR")
    return jsonify(shift_to_event(shift))


@bp.delete("/api/shifts/<int:shift_id>")
@role_required(Role.ADMIN)
def delete_shift_api(shift_id: int):
    shift = db.session.get(Shift, shift_id)
    if shift is None or shift.status == ShiftStatus.CANCELLED:
        return api_error("找不到指定排班。", 404, "NOT_FOUND")
    scope = request.args.get("scope", "single")
    if scope not in {"single", "future", "series"}:
        return api_error("刪除範圍格式錯誤。 / Invalid deletion scope.")
    if scope != "single" and shift.series_id is None:
        return api_error("此排班不屬於重複系列。 / This shift is not part of a recurring series.")
    statement = db.select(Shift).where(Shift.id == shift.id)
    if scope == "future":
        statement = db.select(Shift).where(
            Shift.series_id == shift.series_id,
            Shift.shift_date >= shift.shift_date,
            Shift.status != ShiftStatus.CANCELLED,
        )
    elif scope == "series":
        statement = db.select(Shift).where(
            Shift.series_id == shift.series_id,
            Shift.status != ShiftStatus.CANCELLED,
        )
    shifts = db.session.scalars(statement).all()
    error = cancel_shift_records(
        shifts,
        actor_user_id=current_user.id,
        action="SHIFT_SERIES_CANCELLED" if scope != "single" else "SHIFT_CANCELLED",
    )
    if error:
        db.session.rollback()
        return api_error(error, 409, "SHIFT_HAS_ACTIVE_WORKFLOW")
    db.session.commit()
    return jsonify({"cancelled": len(shifts), "scope": scope})


def cancel_shift_records(shifts: list[Shift], *, actor_user_id: int, action: str) -> str | None:
    if not shifts:
        return "找不到可刪除的排班。 / No shifts can be deleted."
    ids = [item.id for item in shifts]
    if any(item.status != ShiftStatus.SCHEDULED for item in shifts):
        return "請假缺員或已取消的排班不可直接刪除，請先完成相關流程。 / Resolve leave or cancelled shifts first."
    from ..services.periods import PeriodError, ensure_month_open

    try:
        for item in shifts:
            ensure_month_open(item.shift_date)
    except PeriodError as exc:
        return str(exc)
    pending_leave = db.session.scalar(
        db.select(LeaveRequest.id)
        .where(LeaveRequest.shift_id.in_(ids), LeaveRequest.status == LeaveStatus.PENDING)
        .limit(1)
    )
    pending_swap = db.session.scalar(
        db.select(SwapRequest.id)
        .where(
            db.or_(SwapRequest.requester_shift_id.in_(ids), SwapRequest.target_shift_id.in_(ids)),
            SwapRequest.admin_status.in_([SwapAdminStatus.NOT_READY, SwapAdminStatus.PENDING]),
        )
        .limit(1)
    )
    if pending_leave or pending_swap:
        return "選取的排班仍有進行中的請假或換班申請，請先處理申請。 / Selected shifts have active requests."
    for item in shifts:
        item.status = ShiftStatus.CANCELLED
    add_audit(actor_user_id, action, "Shift", shifts[0].id, f"取消 {len(shifts)} 筆排班")
    return None


@bp.post("/api/shifts/bulk-delete")
@role_required(Role.ADMIN)
def bulk_delete_shifts_api():
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("shift_ids")
    if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 500:
        return api_error("請選擇 1–500 筆排班。 / Select between 1 and 500 shifts.")
    try:
        ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError):
        return api_error("排班編號格式錯誤。 / Invalid shift ID.")
    shifts = db.session.scalars(db.select(Shift).where(Shift.id.in_(ids))).all()
    if len(shifts) != len(ids):
        return api_error("部分排班不存在或已被刪除。 / Some shifts no longer exist.", 404, "NOT_FOUND")
    error = cancel_shift_records(shifts, actor_user_id=current_user.id, action="SHIFTS_BULK_CANCELLED")
    if error:
        db.session.rollback()
        return api_error(error, 409, "SHIFT_HAS_ACTIVE_WORKFLOW")
    db.session.commit()
    return jsonify({"cancelled": len(shifts)})


@bp.get("/api/monthly-hours")
@role_required(Role.ADMIN)
def monthly_hours_api():
    try:
        start, end = month_bounds(request.args.get("month", ""))
    except ValueError as exc:
        return api_error(str(exc))

    location = request.args.get("location", "ALL")
    staff_filter = request.args.get("staff_id", type=int)
    profile_statement = db.select(StaffProfile).order_by(StaffProfile.name)
    if staff_filter:
        profile_statement = profile_statement.where(StaffProfile.id == staff_filter)
    profiles = db.session.scalars(profile_statement).all()
    report_locations = db.session.scalars(
        db.select(WorkLocation)
        .order_by(WorkLocation.display_order)
    ).all()
    totals = {
        profile.id: {
            "staff_id": profile.id,
            "name": profile.name,
            "student_number": profile.student_number,
            "office_hours": 0.0,
            "mc_hours": 0.0,
            "location_hours": {location.code: 0.0 for location in report_locations},
            "total_hours": 0.0,
        }
        for profile in profiles
    }

    hours_statement = (
        db.select(
            Shift.staff_id,
            WorkLocation.code,
            db.func.sum(ShiftType.default_hours),
        )
        .select_from(Shift)
        .join(ShiftType)
        .join(WorkLocation)
        .where(
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
            Shift.shift_date >= start,
            Shift.shift_date < end,
        )
        .group_by(Shift.staff_id, WorkLocation.code)
    )
    if location != "ALL":
        hours_statement = hours_statement.where(WorkLocation.code == location)
    if staff_filter:
        hours_statement = hours_statement.where(Shift.staff_id == staff_filter)

    for staff_id, shift_location, hours in db.session.execute(hours_statement):
        if staff_id not in totals:
            continue
        value = float(hours or 0)
        totals[staff_id]["location_hours"][shift_location] = value
        if shift_location == "OFFICE":
            totals[staff_id]["office_hours"] = value
        elif shift_location == "MC":
            totals[staff_id]["mc_hours"] = value
        totals[staff_id]["total_hours"] += value

    rows = list(totals.values())
    return jsonify(
        {
            "month": start.strftime("%Y-%m"),
            "rows": rows,
            "total_hours": sum(row["total_hours"] for row in rows),
        }
    )
