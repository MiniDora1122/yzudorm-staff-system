# Copyright (c) 2026 Tay Yang Long. All Rights Reserved. Designed & Developed by Tay Yang Long.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
import ipaddress
import re
from zoneinfo import ZoneInfo

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload

from . import bp
from ..decorators import role_required
from ..extensions import db
from ..models import (
    AttendanceDevice,
    AttendanceDirection,
    AttendanceEvent,
    AttendancePolicy,
    AttendanceStatus,
    CardStatus,
    Role,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    ShiftType,
    StaffCard,
    StaffProfile,
    User,
    WorkLocation,
    utc_now,
)
from ..services.attendance import (
    AttendanceError,
    card_uid_hash,
    create_provisioning_package,
    calculated_hours_for_shifts,
    new_enrollment,
    normalize_uid,
    policy,
    review_event,
    reconcile_shift_hours,
)
from ..services.audit import add_audit
from ..services.scheduling import SchedulingConflict, create_shift, schedule_serialized
from ..time_utils import local_date_utc_bounds, local_today


@bp.get("/attendance")
@role_required(Role.ADMIN)
def attendance_page():
    query_mode = request.args.get("mode", "day")
    selected_date = request.args.get("date", local_today().isoformat())
    try:
        day = datetime.fromisoformat(selected_date).date()
    except ValueError:
        day = local_today()
    start, end = local_date_utc_bounds(day)
    range_start_value = request.args.get("start", f"{day.isoformat()}T00:00")
    range_end_value = request.args.get("end", f"{day.isoformat()}T23:59")
    if query_mode == "range":
        try:
            local_tz = ZoneInfo(current_app.config["APP_TIMEZONE"])
            local_start = datetime.fromisoformat(range_start_value).replace(tzinfo=local_tz)
            local_end = datetime.fromisoformat(range_end_value).replace(tzinfo=local_tz)
            if local_end < local_start or local_end - local_start > timedelta(days=366):
                raise ValueError
            start, end = local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)
        except ValueError:
            flash("查詢區間格式錯誤、結束時間早於開始時間，或超過一年。", "danger")
            query_mode = "day"
            range_start_value, range_end_value = f"{day.isoformat()}T00:00", f"{day.isoformat()}T23:59"
    end_filter = AttendanceEvent.occurred_at <= end if query_mode == "range" else AttendanceEvent.occurred_at < end
    event_statement = (
        db.select(AttendanceEvent)
        .options(
            joinedload(AttendanceEvent.staff),
            joinedload(AttendanceEvent.device).joinedload(AttendanceDevice.location),
            joinedload(AttendanceEvent.shift).joinedload(Shift.shift_type),
            joinedload(AttendanceEvent.reviewer),
        )
        .where(AttendanceEvent.occurred_at >= start, end_filter)
        .order_by(AttendanceEvent.occurred_at.desc())
    )
    event_pagination = db.paginate(
        event_statement,
        page=max(1, request.args.get("page", 1, type=int)),
        per_page=100,
        error_out=False,
    )
    events = event_pagination.items
    day_shifts = db.session.scalars(
        db.select(Shift)
        .options(
            joinedload(Shift.staff),
            joinedload(Shift.shift_type).joinedload(ShiftType.work_location),
            joinedload(Shift.reconciliation),
        )
        .where(
            Shift.shift_date == day,
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
        )
        .join(Shift.shift_type)
        .order_by(ShiftType.start_time, Shift.staff_id)
    ).all()
    calculated_hours = calculated_hours_for_shifts(day_shifts)
    review_statuses = {
        AttendanceStatus.LATE_PENDING_REVIEW,
        AttendanceStatus.MISSING_CLOCK_IN,
        AttendanceStatus.UNMATCHED,
    }
    pending_statement = (
        db.select(AttendanceEvent)
        .options(
            joinedload(AttendanceEvent.staff),
            joinedload(AttendanceEvent.device).joinedload(AttendanceDevice.location),
            joinedload(AttendanceEvent.shift).joinedload(Shift.shift_type).joinedload(ShiftType.work_location),
        )
        .where(AttendanceEvent.status.in_(review_statuses))
        .order_by(AttendanceEvent.occurred_at)
    )
    pending_pagination = db.paginate(
        pending_statement,
        page=max(1, request.args.get("pending_page", 1, type=int)),
        per_page=50,
        error_out=False,
    )
    pending_events = pending_pagination.items
    tz = ZoneInfo(current_app.config["APP_TIMEZONE"])
    event_dates = {
        event.id: (event.occurred_at.replace(tzinfo=timezone.utc) if event.occurred_at.tzinfo is None else event.occurred_at).astimezone(tz).date()
        for event in pending_events
    }
    staff_ids = {event.staff_id for event in pending_events if event.staff_id}
    candidate_shifts: dict[int, list[Shift]] = {event.id: [] for event in pending_events}
    if pending_events and staff_ids:
        first_day = min(event_dates.values()) - timedelta(days=1)
        last_day = max(event_dates.values()) + timedelta(days=1)
        possible = db.session.scalars(
            db.select(Shift)
            .options(joinedload(Shift.shift_type).joinedload(ShiftType.work_location), joinedload(Shift.staff))
            .join(Shift.shift_type)
            .where(
                Shift.staff_id.in_(staff_ids),
                Shift.shift_date.between(first_day, last_day),
                Shift.status == ShiftStatus.SCHEDULED,
                Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
            )
            .order_by(Shift.shift_date, ShiftType.start_time)
        ).all()
        for event in pending_events:
            candidate_shifts[event.id] = [
                shift for shift in possible
                if shift.staff_id == event.staff_id
                and shift.shift_type.location_id == event.device.location_id
                and abs((shift.shift_date - event_dates[event.id]).days) <= 1
            ]
    return render_template(
        "admin/attendance.html",
        events=events,
        event_pagination=event_pagination,
        pending_events=pending_events,
        pending_pagination=pending_pagination,
        candidate_shifts=candidate_shifts,
        event_dates=event_dates,
        active_shift_types=db.session.scalars(
            db.select(ShiftType)
            .options(joinedload(ShiftType.work_location))
            .where(ShiftType.is_active.is_(True))
            .order_by(ShiftType.display_order)
        ).all(),
        selected_date=day.isoformat(),
        query_mode=query_mode,
        range_start=range_start_value,
        range_end=range_end_value,
        day_shifts=day_shifts,
        calculated_hours=calculated_hours,
        review_statuses=review_statuses,
    )


@bp.post("/attendance/shifts/<int:shift_id>/reconcile")
@role_required(Role.ADMIN)
def reconcile_attendance_shift(shift_id: int):
    shift = db.get_or_404(Shift, shift_id)
    try:
        reconcile_shift_hours(
            shift,
            payable_hours=Decimal(request.form.get("payable_hours", "")),
            note=request.form.get("note", ""),
            actor_user_id=current_user.id,
        )
        flash("計薪時數已完成核對，薪資報表會立即採用此數值。 / Payable hours reconciled.", "success")
    except (AttendanceError, InvalidOperation) as exc:
        db.session.rollback()
        flash(getattr(exc, "message", "計薪時數格式錯誤。 / Invalid payable hours."), "danger")
    return redirect(url_for("admin.attendance_page", date=request.form.get("date", shift.shift_date.isoformat())))


@bp.get("/settings/attendance")
@role_required(Role.ADMIN)
def attendance_settings():
    return render_template(
        "admin/attendance_settings.html",
        devices=db.session.scalars(
            db.select(AttendanceDevice)
            .where(AttendanceDevice.deleted_at.is_(None))
            .order_by(AttendanceDevice.is_active.desc(), AttendanceDevice.name)
        ).all(),
        locations=db.session.scalars(
            db.select(WorkLocation).where(WorkLocation.is_active.is_(True)).order_by(WorkLocation.display_order)
        ).all(),
        attendance_policy=policy(),
        attendance_enabled=current_app.config.get("ATTENDANCE_ENABLED", True),
        attendance_transport_mode=current_app.config.get("ATTENDANCE_TRANSPORT_MODE", "HTTPS"),
        default_attendance_server_url=request.url_root.rstrip("/"),
    )


def _settings_url(section: str) -> str:
    return url_for("admin.attendance_settings") + f"#{section}"


def _device_or_404(device_id: int) -> AttendanceDevice:
    device = db.session.get(AttendanceDevice, device_id)
    if device is None or device.deleted_at is not None:
        abort(404)
    return device


def _card_return_url(staff_id: int | None = None) -> str:
    if request.form.get("return_to") == "staff":
        return url_for("admin.staff") + (f"#staff-{staff_id}" if staff_id else "")
    return url_for("admin.staff")


def _activation_minutes() -> int:
    return int(request.form.get("activation_hours", "")) * 60


@bp.post("/attendance/devices")
@role_required(Role.ADMIN)
def create_attendance_device():
    code = request.form.get("device_code", "").strip().upper()
    name = request.form.get("name", "").strip()
    try:
        location_id = int(request.form.get("location_id", ""))
        location = db.session.get(WorkLocation, location_id)
        allowed_cidr = request.form.get("allowed_cidr", "").strip()
        if not re.fullmatch(r"[A-Z0-9._-]{1,60}", code) or not name or location is None or not location.is_active:
            raise ValueError
        if allowed_cidr:
            ipaddress.ip_network(allowed_cidr, strict=False)
        device = AttendanceDevice(
            device_code=code[:60], name=name[:120], location_id=location_id,
            allowed_cidr=allowed_cidr[:80] or None,
            offline_threshold_minutes=policy().offline_threshold_minutes,
            created_by=current_user.id,
        )
        db.session.add(device)
        db.session.flush()
        device.location = location
        mode = current_app.config.get("ATTENDANCE_TRANSPORT_MODE", "HTTPS")
        if mode == "ENCRYPTED_HTTP":
            package = create_provisioning_package(
                device,
                server_url=request.form.get("server_url", "").strip(),
                passphrase=request.form.get("package_password", ""),
                transport_mode=mode,
                activation_minutes=_activation_minutes(),
            )
        else:
            token = new_enrollment(device)
        add_audit(current_user.id, "ATTENDANCE_DEVICE_CREATED", "AttendanceDevice", device.id, f"建立打卡裝置 {code}")
        db.session.commit()
        if mode == "ENCRYPTED_HTTP":
            return send_file(
                BytesIO(package), mimetype="application/octet-stream", as_attachment=True,
                download_name=f"{device.device_code}.dormclock",
            )
        flash(f"裝置已建立。一次性註冊碼（10 分鐘有效，只顯示一次）：{token}", "success")
    except (ValueError, AttendanceError):
        db.session.rollback()
        flash("裝置資料、中央網址或註冊包密碼格式錯誤。 / Invalid device registration data.", "danger")
    except Exception:
        db.session.rollback()
        flash("裝置代碼已存在或無法建立。 / Duplicate or unavailable device code.", "danger")
    return redirect(_settings_url("devices"))


@bp.post("/attendance/devices/<int:device_id>/enrollment")
@role_required(Role.ADMIN)
def renew_attendance_enrollment(device_id: int):
    device = _device_or_404(device_id)
    device.is_active = True
    token = new_enrollment(device)
    db.session.commit()
    flash(f"新的一次性註冊碼（10 分鐘有效，只顯示一次）：{token}", "warning")
    return redirect(_settings_url("devices"))


@bp.post("/attendance/devices/<int:device_id>/package")
@role_required(Role.ADMIN)
def download_attendance_package(device_id: int):
    device = _device_or_404(device_id)
    try:
        device.is_active = True
        device.revoked_at = None
        device.revoked_by = None
        package = create_provisioning_package(
            device,
            server_url=request.form.get("server_url", "").strip(),
            passphrase=request.form.get("package_password", ""),
            transport_mode=current_app.config.get("ATTENDANCE_TRANSPORT_MODE", "HTTPS"),
            activation_minutes=_activation_minutes(),
        )
        add_audit(current_user.id, "ATTENDANCE_DEVICE_REKEYED", "AttendanceDevice", device.id, f"重新產生打卡裝置 {device.device_code} 註冊包")
        db.session.commit()
        return send_file(
            BytesIO(package), mimetype="application/octet-stream", as_attachment=True,
            download_name=f"{device.device_code}.dormclock",
        )
    except (AttendanceError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, AttendanceError) else "請填寫有效的啟用期限。", "danger")
        return redirect(_settings_url("devices"))


@bp.post("/attendance/devices/<int:device_id>/update")
@role_required(Role.ADMIN)
def update_attendance_device(device_id: int):
    device = _device_or_404(device_id)
    name = request.form.get("name", "").strip()
    if not name or len(name) > 120:
        flash("裝置名稱不可空白且不得超過 120 個字元。", "danger")
        return redirect(_settings_url("devices"))
    device.name = name
    add_audit(current_user.id, "ATTENDANCE_DEVICE_UPDATED", "AttendanceDevice", device.id, f"更新打卡裝置 {device.device_code} 名稱")
    db.session.commit()
    flash("裝置名稱已更新。 / Device name updated.", "success")
    return redirect(_settings_url("devices"))


@bp.post("/attendance/devices/<int:device_id>/offline-alert")
@role_required(Role.ADMIN)
def update_attendance_device_offline_alert(device_id: int):
    device = _device_or_404(device_id)
    try:
        threshold = int(request.form.get("offline_threshold_minutes", ""))
        weekdays = sorted({int(value) for value in request.form.getlist("offline_weekdays")})
        start = datetime.strptime(request.form.get("offline_monitor_start", ""), "%H:%M").time()
        end = datetime.strptime(request.form.get("offline_monitor_end", ""), "%H:%M").time()
        enabled = request.form.get("offline_alert_enabled") == "1"
        if not 5 <= threshold <= 1440 or any(day not in range(7) for day in weekdays) or enabled and not weekdays:
            raise ValueError
        before = {
            "enabled": device.offline_alert_enabled,
            "threshold": device.offline_threshold_minutes,
            "weekdays": device.offline_monitor_weekdays,
            "start": device.offline_monitor_start.isoformat(),
            "end": device.offline_monitor_end.isoformat(),
        }
        device.offline_alert_enabled = enabled
        device.offline_threshold_minutes = threshold
        device.offline_monitor_weekdays = ",".join(map(str, weekdays))
        device.offline_monitor_start = start
        device.offline_monitor_end = end
        add_audit(
            current_user.id, "ATTENDANCE_DEVICE_ALERT_UPDATED", "AttendanceDevice", device.id,
            f"更新打卡裝置 {device.device_code} 離線提醒",
            changes={"before": before, "after": {
                "enabled": enabled, "threshold": threshold,
                "weekdays": device.offline_monitor_weekdays,
                "start": start.isoformat(), "end": end.isoformat(),
            }},
        )
        db.session.commit()
        flash("裝置離線提醒設定已更新。 / Offline alert settings updated.", "success")
    except (TypeError, ValueError):
        db.session.rollback()
        flash("請選擇有效的監控星期、時段與 5～1440 分鐘。 / Invalid offline alert settings.", "danger")
    return redirect(_settings_url("devices"))


@bp.post("/attendance/devices/<int:device_id>/confirm-identity")
@role_required(Role.ADMIN)
def confirm_attendance_device_identity(device_id: int):
    device = _device_or_404(device_id)
    if not device.pending_mac_addresses_json:
        flash("此裝置沒有待確認的電腦或 MAC 異動。", "warning")
        return redirect(_settings_url("devices"))
    device.computer_name = device.pending_computer_name
    device.mac_addresses_json = device.pending_mac_addresses_json
    device.pending_computer_name = None
    device.pending_mac_addresses_json = None
    device.identity_changed_at = None
    add_audit(current_user.id, "ATTENDANCE_DEVICE_IDENTITY_CONFIRMED", "AttendanceDevice", device.id, f"確認打卡裝置 {device.device_code} 電腦與 MAC 異動")
    db.session.commit()
    flash("裝置電腦名稱與 MAC 異動已確認。", "success")
    return redirect(_settings_url("devices"))


def _disable_device(device: AttendanceDevice) -> None:
    device.is_active = False
    device.secret_encrypted = None
    device.pending_secret_encrypted = None
    device.enrollment_token_hash = None
    device.enrollment_expires_at = None
    device.enrolled_at = None
    device.revoked_by = current_user.id
    device.revoked_at = utc_now()


@bp.post("/attendance/devices/<int:device_id>/archive")
@role_required(Role.ADMIN)
def archive_attendance_device(device_id: int):
    device = _device_or_404(device_id)
    _disable_device(device)
    add_audit(current_user.id, "ATTENDANCE_DEVICE_ARCHIVED", "AttendanceDevice", device.id, f"封存打卡裝置 {device.device_code}")
    db.session.commit()
    flash("裝置已封存；既有打卡紀錄保留。恢復後必須重新註冊。 / Device archived.", "warning")
    return redirect(_settings_url("devices"))


@bp.post("/attendance/devices/<int:device_id>/restore")
@role_required(Role.ADMIN)
def restore_attendance_device(device_id: int):
    device = _device_or_404(device_id)
    if device.deleted_at:
        flash("已刪除的裝置不可恢復。", "danger")
        return redirect(_settings_url("devices"))
    device.is_active = True
    device.revoked_by = None
    device.revoked_at = None
    add_audit(current_user.id, "ATTENDANCE_DEVICE_RESTORED", "AttendanceDevice", device.id, f"恢復打卡裝置 {device.device_code}")
    db.session.commit()
    flash("裝置已恢復，請重新下載註冊包或產生註冊碼。 / Device restored; registration required.", "success")
    return redirect(_settings_url("devices"))


@bp.post("/attendance/devices/<int:device_id>/delete")
@role_required(Role.ADMIN)
def delete_attendance_device(device_id: int):
    device = _device_or_404(device_id)
    original_code = device.device_code
    has_history = db.session.scalar(
        db.select(AttendanceEvent.id).where(AttendanceEvent.device_id == device.id).limit(1)
    ) is not None
    from ..models import AttendanceDeviceNonce
    db.session.execute(db.delete(AttendanceDeviceNonce).where(AttendanceDeviceNonce.device_id == device.id))
    if has_history:
        _disable_device(device)
        device.deleted_at = utc_now()
        device.installation_id = None
        device.device_code = f"DELETED-{device.id}-{original_code}"[:60]
        add_audit(current_user.id, "ATTENDANCE_DEVICE_DELETED", "AttendanceDevice", device.id, f"刪除打卡裝置 {original_code}；歷史紀錄保留")
        db.session.commit()
    else:
        add_audit(current_user.id, "ATTENDANCE_DEVICE_DELETED", "AttendanceDevice", device.id, f"刪除未使用打卡裝置 {original_code}")
        db.session.delete(device)
        db.session.commit()
    flash("裝置已刪除且無法恢復；既有打卡紀錄不受影響。 / Device deleted.", "success")
    return redirect(_settings_url("devices"))


@bp.post("/attendance/cards")
@role_required(Role.ADMIN)
def register_staff_card():
    profile = None
    try:
        staff_id = int(request.form.get("staff_id", ""))
        profile = db.session.get(StaffProfile, staff_id)
        uid = normalize_uid(request.form.get("card_uid", ""))
        digest = card_uid_hash(uid)
        if profile is None:
            raise AttendanceError("STAFF_NOT_FOUND", "找不到工讀生。")
        if db.session.scalar(db.select(StaffCard.id).where(StaffCard.uid_hash == digest)):
            raise AttendanceError("CARD_ALREADY_REGISTERED", "這張卡片已經登錄。")
        for old in db.session.scalars(
            db.select(StaffCard).where(StaffCard.staff_id == profile.id, StaffCard.status == CardStatus.ACTIVE)
        ):
            old.status = CardStatus.REPLACED
            old.disabled_by = current_user.id
            old.disabled_at = utc_now()
            old.disable_reason = "登錄新卡片"
        card = StaffCard(
            staff_id=profile.id, uid_hash=digest, uid_last4=uid[-4:], registered_by=current_user.id
        )
        db.session.add(card)
        db.session.flush()
        add_audit(current_user.id, "STAFF_CARD_REGISTERED", "StaffCard", card.id, f"為 {profile.name} 登錄卡片末四碼 {card.uid_last4}")
        db.session.commit()
        flash("學生證已登錄；原有效卡片已自動停用。 / Card registered.", "success")
    except (ValueError, AttendanceError) as exc:
        db.session.rollback()
        flash(str(exc) or "卡片資料格式錯誤。", "danger")
    return redirect(_card_return_url(profile.id if profile else None))


@bp.post("/attendance/cards/<int:card_id>/disable")
@role_required(Role.ADMIN)
def disable_staff_card(card_id: int):
    card = db.get_or_404(StaffCard, card_id)
    card.status = CardStatus.LOST if request.form.get("status") == "LOST" else CardStatus.REVOKED
    card.disabled_by = current_user.id
    card.disabled_at = utc_now()
    card.disable_reason = request.form.get("reason", "").strip()[:500] or "管理員停用"
    add_audit(current_user.id, "STAFF_CARD_DISABLED", "StaffCard", card.id, f"停用卡片末四碼 {card.uid_last4}")
    db.session.commit()
    flash("卡片已停用。 / Card disabled.", "warning")
    return redirect(_card_return_url(card.staff_id))


@bp.post("/attendance/policy")
@role_required(Role.ADMIN)
def update_attendance_policy():
    try:
        item = policy()
        before = {field: getattr(item, field) for field in ("early_checkin_minutes", "late_grace_minutes", "checkout_after_minutes", "duplicate_seconds", "offline_threshold_minutes")}
        for field, minimum, maximum in (
            ("early_checkin_minutes", 0, 240), ("late_grace_minutes", 0, 60),
            ("checkout_after_minutes", 0, 360), ("duplicate_seconds", 5, 600),
            ("offline_threshold_minutes", 5, 1440),
        ):
            value = int(request.form.get(field, ""))
            if not minimum <= value <= maximum:
                raise ValueError
            setattr(item, field, value)
        item.updated_by = current_user.id
        add_audit(
            current_user.id, "ATTENDANCE_POLICY_UPDATED", "AttendancePolicy", item.id,
            "更新打卡判定設定",
            changes={"before": before, "after": {field: getattr(item, field) for field in before}},
        )
        db.session.commit()
        flash("打卡判定設定已更新。 / Attendance policy updated.", "success")
    except ValueError:
        db.session.rollback()
        flash("打卡設定數值超出允許範圍。", "danger")
    return redirect(_settings_url("policy"))


@bp.post("/attendance/events/<int:event_id>/review")
@role_required(Role.ADMIN)
@schedule_serialized
def review_attendance_event(event_id: int):
    event = db.get_or_404(AttendanceEvent, event_id)
    try:
        decision = request.form.get("decision", "")
        if decision == "APPROVE":
            direction = AttendanceDirection(request.form.get("direction") or event.direction.value)
            if direction == AttendanceDirection.UNKNOWN:
                raise AttendanceError("DIRECTION_REQUIRED", "核准前請選擇上班卡或下班卡。")
            event.direction = direction
            shift_id = request.form.get("shift_id", type=int)
            create_missing_shift = request.form.get("create_missing_shift") == "yes"
            if shift_id:
                shift = db.session.get(Shift, shift_id)
                if (
                    shift is None
                    or shift.staff_id != event.staff_id
                    or shift.status != ShiftStatus.SCHEDULED
                    or shift.publication_status != ShiftPublicationStatus.PUBLISHED
                    or shift.shift_type.location_id != event.device.location_id
                ):
                    raise AttendanceError("INVALID_SHIFT", "選擇的排班不屬於此學生或打卡地點。")
                event.shift = shift
            elif create_missing_shift:
                shift_type = db.session.get(ShiftType, request.form.get("new_shift_type_id", type=int))
                if (
                    event.staff is None
                    or shift_type is None
                    or not shift_type.is_active
                    or shift_type.location_id != event.device.location_id
                ):
                    raise AttendanceError("INVALID_NEW_SHIFT", "請選擇與打卡地點相符的有效班別。")
                shift = create_shift(
                    shift_date=datetime.fromisoformat(request.form.get("new_shift_date", "")).date(),
                    shift_type=shift_type,
                    staff=event.staff,
                    actor_id=current_user.id,
                    allow_location_overlap=request.form.get("allow_location_overlap") == "yes",
                    allow_availability_conflict=True,
                    publication_status=ShiftPublicationStatus.PUBLISHED,
                    commit=False,
                )
                event.shift = shift
            elif event.shift_id:
                pass
            elif request.form.get("confirm_no_shift") != "yes":
                raise AttendanceError(
                    "SHIFT_CONFIRMATION_REQUIRED",
                    "請選擇既有排班、建立補登排班，或明確確認此打卡不對應排班。",
                )
        review_event(
            event, decision=decision,
            note=request.form.get("review_note", ""), actor_user_id=current_user.id,
        )
        flash("出勤異常已完成審核。 / Attendance exception reviewed.", "success")
    except (AttendanceError, SchedulingConflict, ValueError) as exc:
        db.session.rollback()
        flash(getattr(exc, "message", str(exc)), "danger")
    return redirect(url_for("admin.attendance_page", date=request.form.get("date", "")))
