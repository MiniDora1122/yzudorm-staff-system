from __future__ import annotations

from flask import current_app, jsonify, request

from . import bp
from ..extensions import db
from ..models import AttendanceEvent, AttendanceMethod
from ..services.attendance import (
    AttendanceError,
    activate_device,
    create_event,
    decrypt_device_payload,
    encrypt_device_response,
    enroll_device,
    event_json,
    identify_account,
    identify_card,
    submit_reason,
    verify_device_request,
)


def error(exc: AttendanceError, context=None):
    db.session.rollback()
    if context is not None:
        return jsonify(encrypt_device_response(
            context, {"error": {"code": exc.code, "message": str(exc)}}, exc.status
        )), exc.status
    return jsonify({"error": {"code": exc.code, "message": str(exc)}}), exc.status


def device_request():
    body = request.get_data(cache=True)
    if current_app.config.get("ATTENDANCE_TRANSPORT_MODE") == "ENCRYPTED_HTTP":
        return decrypt_device_payload(body, path=request.path)
    return None, request.get_json(force=True) if body else {}


def success(payload: dict, context=None, status=200):
    if context is not None:
        return jsonify(encrypt_device_response(context, payload, status)), status
    return jsonify(payload), status


@bp.post("/enroll")
def enroll():
    try:
        if current_app.config.get("ATTENDANCE_TRANSPORT_MODE") == "ENCRYPTED_HTTP":
            raise AttendanceError(
                "PACKAGE_REQUIRED", "加密 HTTP 模式請匯入管理員下載的註冊包。", 410
            )
        device, secret = enroll_device((request.get_json(silent=True) or {}).get("token", ""))
        return jsonify({
            "device_id": device.device_code,
            "device_name": device.name,
            "location": device.location.name,
            "secret": secret,
        })
    except AttendanceError as exc:
        return error(exc)


@bp.post("/activate")
def activate():
    context = None
    try:
        if current_app.config.get("ATTENDANCE_TRANSPORT_MODE") != "ENCRYPTED_HTTP":
            raise AttendanceError("ACTIVATION_NOT_AVAILABLE", "此傳輸模式不使用加密註冊包。", 400)
        context, payload = decrypt_device_payload(request.get_data(cache=True), path=request.path)
        activate_device(context.device, payload)
        db.session.commit()
        return success({
            "device_name": context.device.name,
            "location": context.device.location.name,
            "activated": True,
        }, context)
    except AttendanceError as exc:
        return error(exc, context)


@bp.post("/punch")
def punch():
    context = None
    try:
        context, payload = device_request()
        device = context.device if context else verify_device_request(request.get_data(cache=True), path=request.path)
        method = AttendanceMethod(payload.get("method"))
        payload["method"] = method.value
        if method == AttendanceMethod.CARD:
            profile, card = identify_card(payload.get("card_uid", ""))
        elif method == AttendanceMethod.ACCOUNT:
            profile = identify_account(payload.get("username", ""), payload.get("password", ""), device)
            card = None
        else:
            raise AttendanceError("INVALID_METHOD", "打卡驗證方式不正確。")
        event = create_event(device=device, profile=profile, card=card, payload=payload)
        return success(event_json(event), context)
    except (AttendanceError, ValueError) as exc:
        if not isinstance(exc, AttendanceError):
            exc = AttendanceError("INVALID_REQUEST", "打卡資料格式錯誤。")
        return error(exc, context)


@bp.post("/events/<event_uuid>/reason")
def reason(event_uuid: str):
    context = None
    try:
        context, payload = device_request()
        device = context.device if context else verify_device_request(request.get_data(cache=True), path=request.path)
        event = db.session.scalar(
            db.select(AttendanceEvent).where(
                AttendanceEvent.event_uuid == event_uuid,
                AttendanceEvent.device_id == device.id,
            )
        )
        if event is None:
            raise AttendanceError("EVENT_NOT_FOUND", "找不到打卡紀錄。", 404)
        submit_reason(
            event,
            category=payload.get("category", "其他"),
            reason=payload.get("reason", ""),
            claimed_arrival=payload.get("claimed_arrival_at"),
        )
        return success({"message": "事由已送交管理員確認。", "status": event.status.value}, context)
    except AttendanceError as exc:
        return error(exc, context)


@bp.route("/health", methods=["GET", "POST"])
def health():
    context = None
    try:
        context, _payload = device_request()
        device = context.device if context else verify_device_request(request.get_data(cache=True), path=request.path)
        db.session.commit()
        return success({"status": "ok", "device": device.device_code, "device_name": device.name, "location": device.location.name}, context)
    except AttendanceError as exc:
        return error(exc, context)
