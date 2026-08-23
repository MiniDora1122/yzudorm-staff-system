from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import base64
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from flask import current_app, request
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    AttendanceAdjustment,
    AttendanceReconciliation,
    AuditLog,
    AttendanceDevice,
    AttendanceDirection,
    AttendanceEvent,
    AttendanceMethod,
    AttendancePolicy,
    AttendanceReviewHistory,
    AttendanceStatus,
    CardStatus,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    ShiftType,
    StaffCard,
    StaffProfile,
    User,
    utc_now,
)
from .audit import add_audit
from .notifications import complete_notification, notify_admins, notify_user


class AttendanceError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class EncryptedRequestContext:
    device: AttendanceDevice
    request_id: str
    path: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _transport_key(secret: bytes, direction: str) -> bytes:
    return hmac.new(secret, f"dorm-attendance/{direction}/v1".encode(), hashlib.sha256).digest()


def _request_aad(method: str, path: str, device_code: str, request_id: str, timestamp: str) -> bytes:
    return "\n".join([method.upper(), path, device_code, request_id, timestamp, "1"]).encode()


def decrypt_device_payload(body: bytes, *, path: str) -> tuple[EncryptedRequestContext, dict]:
    """Authenticate and decrypt one ENCRYPTED_HTTP request.

    Device code, time and request id are routing metadata. All punch data,
    including account passwords and card UIDs, remains inside AES-256-GCM.
    """
    try:
        envelope = json.loads(body)
        code = str(envelope["device_id"])
        request_id = str(envelope["request_id"])
        timestamp = str(envelope["timestamp"])
        nonce = _unb64(str(envelope["nonce"]))
        ciphertext = _unb64(str(envelope["ciphertext"]))
        if int(envelope.get("key_version", 0)) != 1:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise AttendanceError("INVALID_ENVELOPE", "加密打卡封包格式錯誤。", 400)
    device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == code))
    if not device or not device.is_active:
        raise AttendanceError("DEVICE_NOT_ALLOWED", "此裝置未註冊、已封存或已刪除，請由管理員重新註冊。 / Device unavailable.", 401)
    if path != "/attendance-api/activate" and not device.enrolled_at:
        raise AttendanceError("DEVICE_NOT_ACTIVATED", "此裝置尚未完成一次性啟用。", 401)
    source = request.remote_addr or ""
    if not _source_allowed(device, source):
        raise AttendanceError("DEVICE_NETWORK_DENIED", "此裝置來源網路不在允許範圍。", 403)
    try:
        request_time = datetime.fromtimestamp(int(timestamp), timezone.utc)
    except (ValueError, OSError):
        raise AttendanceError("INVALID_DEVICE_TIME", "裝置時間格式錯誤。", 401)
    if abs((utc_now() - request_time).total_seconds()) > 300:
        raise AttendanceError("DEVICE_TIME_DRIFT", "裝置時間偏差過大，請校正系統時間。", 401)
    if len(request_id) < 16 or len(request_id) > 64 or len(nonce) != 12:
        raise AttendanceError("INVALID_NONCE", "裝置請求識別碼無效。", 401)
    from ..models import AttendanceDeviceNonce
    if db.session.scalar(db.select(AttendanceDeviceNonce.id).where(
        AttendanceDeviceNonce.device_id == device.id,
        AttendanceDeviceNonce.nonce == request_id,
    )):
        raise AttendanceError("REPLAYED_REQUEST", "重複的裝置請求已被拒絕。", 409)
    try:
        use_pending = path == "/attendance-api/activate" and bool(device.pending_secret_encrypted)
        plaintext = AESGCM(_transport_key(decrypt_device_secret(device, pending=use_pending), "request")).decrypt(
            nonce, ciphertext, _request_aad(request.method, path, code, request_id, timestamp)
        )
        payload = json.loads(plaintext)
        if not isinstance(payload, dict):
            raise ValueError
    except Exception as exc:
        raise AttendanceError("DECRYPTION_FAILED", "加密打卡封包驗證失敗。", 401) from exc
    db.session.execute(db.delete(AttendanceDeviceNonce).where(
        AttendanceDeviceNonce.created_at < utc_now() - timedelta(days=1)
    ))
    db.session.add(AttendanceDeviceNonce(device_id=device.id, nonce=request_id))
    device.last_seen_at = utc_now()
    device.last_ip = source[:45] or None
    if path != "/attendance-api/activate":
        observe_device_identity(device, payload.get("_device"))
    return EncryptedRequestContext(device, request_id, path), payload


def encrypt_device_response(context: EncryptedRequestContext, payload: dict, status: int = 200) -> dict:
    nonce = secrets.token_bytes(12)
    aad = "\n".join([
        str(status), context.path, context.device.device_code, context.request_id, "1"
    ]).encode()
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ciphertext = AESGCM(_transport_key(decrypt_device_secret(context.device), "response")).encrypt(
        nonce, plaintext, aad
    )
    return {
        "device_id": context.device.device_code,
        "request_id": context.request_id,
        "key_version": 1,
        "status": status,
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
    }


def create_provisioning_package(
    device: AttendanceDevice, *, server_url: str, passphrase: str, transport_mode: str,
    activation_minutes: int,
) -> bytes:
    if len(passphrase) < 10:
        raise AttendanceError("WEAK_PACKAGE_PASSWORD", "註冊包密碼至少需要 10 個字元。")
    parsed_url = urlsplit(server_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise AttendanceError("INVALID_SERVER_URL", "中央系統網址必須以 http:// 或 https:// 開頭。")
    if transport_mode not in {"HTTPS", "ENCRYPTED_HTTP"}:
        raise AttendanceError("INVALID_TRANSPORT", "打卡傳輸模式設定錯誤。")
    if not 5 <= activation_minutes <= 10_080:
        raise AttendanceError("INVALID_ACTIVATION_WINDOW", "啟用期限需介於 5 分鐘至 7 天。")
    secret = secrets.token_urlsafe(48)
    activation_token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(minutes=activation_minutes)
    # Keep the current terminal working until the replacement package proves
    # possession of its new secret by completing activation.
    encrypted_secret = encrypt_device_secret(secret)
    if device.secret_encrypted and device.enrolled_at:
        device.pending_secret_encrypted = encrypted_secret
    else:
        device.secret_encrypted = encrypted_secret
        device.pending_secret_encrypted = None
    device.enrollment_token_hash = hashlib.sha256(activation_token.encode()).hexdigest()
    device.enrollment_expires_at = expires_at
    payload = json.dumps({
        "format": "dorm-attendance-device-v1",
        "server": server_url.rstrip("/"),
        "device_id": device.device_code,
        "device_name": device.name,
        "location": device.location.name,
        "secret": secret,
        "activation_token": activation_token,
        "activation_expires_at": expires_at.isoformat(),
        "transport_mode": transport_mode,
        "key_version": 1,
    }, ensure_ascii=False, separators=(",", ":")).encode()
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=300_000).derive(passphrase.encode())
    ciphertext = AESGCM(key).encrypt(nonce, payload, b"dorm-attendance-provision-v1")
    return json.dumps({
        "format": "dorm-attendance-provision-v1", "iterations": 300000,
        "salt": _b64(salt), "nonce": _b64(nonce), "ciphertext": _b64(ciphertext),
    }, separators=(",", ":")).encode()


def _device_identity(value: dict | None) -> tuple[str, str, list[str]]:
    try:
        installation_id = str(UUID(str((value or {})["installation_id"])))
        computer_name = str((value or {})["computer_name"]).strip()[:255]
        raw_macs = (value or {})["mac_addresses"]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AttendanceError("INVALID_DEVICE_IDENTITY", "無法辨識終端電腦或網路卡資料。")
    macs = []
    for value in raw_macs if isinstance(raw_macs, list) else []:
        compact = "".join(character for character in str(value).upper() if character in "0123456789ABCDEF")
        if len(compact) == 12:
            mac = ":".join(compact[index:index + 2] for index in range(0, 12, 2))
            if mac not in macs:
                macs.append(mac)
    if not computer_name or not macs:
        raise AttendanceError("INVALID_DEVICE_IDENTITY", "終端必須回報電腦名稱與至少一個 MAC 位址。")
    return installation_id, computer_name, sorted(macs)[:16]


def activate_device(device: AttendanceDevice, payload: dict) -> None:
    token = str(payload.get("activation_token", ""))
    expected = device.enrollment_token_hash or ""
    expires_at = _aware(device.enrollment_expires_at) if device.enrollment_expires_at else None
    installation_id, computer_name, macs = _device_identity(payload.get("_device"))
    encoded_macs = json.dumps(macs, separators=(",", ":"))
    if (not expected and device.enrolled_at and device.installation_id == installation_id
            and device.computer_name == computer_name and device.mac_addresses_json == encoded_macs):
        return
    if not expected or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected):
        raise AttendanceError("ACTIVATION_USED", "此註冊包已使用或無效，請由管理員重新產生。", 401)
    if not expires_at or expires_at < utc_now():
        raise AttendanceError("ACTIVATION_EXPIRED", "此註冊包已逾期，請由管理員重新產生。", 401)
    duplicate = db.session.scalar(db.select(AttendanceDevice.id).where(
        AttendanceDevice.installation_id == installation_id,
        AttendanceDevice.id != device.id,
    ))
    if duplicate:
        raise AttendanceError("INSTALLATION_ALREADY_BOUND", "此電腦安裝識別碼已綁定其他裝置。", 409)
    device.installation_id = installation_id
    device.computer_name = computer_name
    device.mac_addresses_json = encoded_macs
    device.pending_computer_name = None
    device.pending_mac_addresses_json = None
    device.identity_changed_at = None
    device.enrolled_at = utc_now()
    if device.pending_secret_encrypted:
        device.secret_encrypted = device.pending_secret_encrypted
        device.pending_secret_encrypted = None
    device.enrollment_token_hash = None
    device.enrollment_expires_at = None
    add_audit(None, "ATTENDANCE_DEVICE_ACTIVATED", "AttendanceDevice", device.id, f"打卡裝置 {device.device_code} 完成一次性啟用")


def observe_device_identity(device: AttendanceDevice, value: dict | None) -> None:
    if not value:
        return
    installation_id, computer_name, macs = _device_identity(value)
    if device.installation_id and installation_id != device.installation_id:
        raise AttendanceError("DEVICE_IDENTITY_MISMATCH", "裝置安裝識別碼不符，請重新註冊。", 401)
    encoded_macs = json.dumps(macs, separators=(",", ":"))
    if not device.installation_id:
        device.installation_id = installation_id
        device.computer_name = computer_name
        device.mac_addresses_json = encoded_macs
        return
    if computer_name == device.computer_name and encoded_macs == device.mac_addresses_json:
        return
    if computer_name != device.pending_computer_name or encoded_macs != device.pending_mac_addresses_json:
        device.pending_computer_name = computer_name
        device.pending_mac_addresses_json = encoded_macs
        device.identity_changed_at = utc_now()
        add_audit(None, "ATTENDANCE_DEVICE_IDENTITY_CHANGED", "AttendanceDevice", device.id, f"打卡裝置 {device.device_code} 回報電腦或 MAC 異動")


def policy() -> AttendancePolicy:
    item = db.session.get(AttendancePolicy, 1)
    if item is None:
        item = AttendancePolicy(id=1)
        db.session.add(item)
        db.session.flush()
    return item


def normalize_uid(value: str) -> str:
    uid = "".join(ch for ch in value.strip().upper() if ch.isalnum())
    if not 4 <= len(uid) <= 64:
        raise AttendanceError("INVALID_CARD_UID", "卡片 UID 格式不正確。 / Invalid card UID.")
    return uid


def card_uid_hash(value: str) -> str:
    uid = normalize_uid(value)
    # Reuse the backed-up stable encryption key so rotating Flask sessions never invalidates cards.
    key = current_app.config["DOCUMENT_ENCRYPTION_KEY"].encode("ascii")
    return hmac.new(key, b"attendance-card:" + uid.encode("ascii"), hashlib.sha256).hexdigest()


def encrypt_device_secret(secret: str) -> str:
    return Fernet(current_app.config["DOCUMENT_ENCRYPTION_KEY"].encode("ascii")).encrypt(secret.encode()).decode()


def decrypt_device_secret(device: AttendanceDevice, *, pending: bool = False) -> bytes:
    encrypted = device.pending_secret_encrypted if pending else device.secret_encrypted
    if not encrypted:
        raise AttendanceError("DEVICE_NOT_ENROLLED", "打卡裝置尚未完成註冊。", 401)
    return Fernet(current_app.config["DOCUMENT_ENCRYPTION_KEY"].encode("ascii")).decrypt(
        encrypted.encode()
    )


def new_enrollment(device: AttendanceDevice) -> str:
    token = secrets.token_urlsafe(32)
    device.enrollment_token_hash = hashlib.sha256(token.encode()).hexdigest()
    device.enrollment_expires_at = utc_now() + timedelta(minutes=10)
    return token


def enroll_device(token: str) -> tuple[AttendanceDevice, str]:
    digest = hashlib.sha256(token.strip().encode()).hexdigest()
    device = db.session.scalar(
        db.select(AttendanceDevice).where(AttendanceDevice.enrollment_token_hash == digest)
    )
    now = utc_now()
    if not device or not device.is_active or not device.enrollment_expires_at or _aware(device.enrollment_expires_at) < now:
        raise AttendanceError("INVALID_ENROLLMENT", "裝置註冊碼無效或已過期。", 401)
    secret = secrets.token_urlsafe(48)
    device.secret_encrypted = encrypt_device_secret(secret)
    device.enrolled_at = now
    device.enrollment_token_hash = None
    device.enrollment_expires_at = None
    device.last_ip = (request.remote_addr or "")[:45] or None
    add_audit(None, "ATTENDANCE_DEVICE_ENROLLED", "AttendanceDevice", device.id, f"打卡裝置 {device.device_code} 完成註冊")
    db.session.commit()
    return device, secret


def _source_allowed(device: AttendanceDevice, source: str) -> bool:
    if not device.allowed_cidr:
        return True
    try:
        return ipaddress.ip_address(source) in ipaddress.ip_network(device.allowed_cidr, strict=False)
    except ValueError:
        return False


def verify_device_request(body: bytes, *, path: str) -> AttendanceDevice:
    code = request.headers.get("X-Attendance-Device", "")
    timestamp = request.headers.get("X-Attendance-Timestamp", "")
    nonce = request.headers.get("X-Attendance-Nonce", "")
    signature = request.headers.get("X-Attendance-Signature", "")
    device = db.session.scalar(db.select(AttendanceDevice).where(AttendanceDevice.device_code == code))
    if not device or not device.is_active:
        raise AttendanceError("DEVICE_NOT_ALLOWED", "此裝置未註冊、已封存或已刪除，請由管理員重新註冊。 / Device unavailable.", 401)
    if not device.enrolled_at:
        raise AttendanceError("DEVICE_NOT_ACTIVATED", "此裝置尚未完成一次性啟用。", 401)
    source = request.remote_addr or ""
    if not _source_allowed(device, source):
        raise AttendanceError("DEVICE_NETWORK_DENIED", "此裝置來源網路不在允許範圍。", 403)
    try:
        request_time = datetime.fromtimestamp(int(timestamp), timezone.utc)
    except (ValueError, OSError):
        raise AttendanceError("INVALID_DEVICE_TIME", "裝置時間格式錯誤。", 401)
    if abs((utc_now() - request_time).total_seconds()) > 300:
        raise AttendanceError("DEVICE_TIME_DRIFT", "裝置時間偏差過大，請校正系統時間。", 401)
    if not (16 <= len(nonce) <= 64):
        raise AttendanceError("INVALID_NONCE", "裝置請求識別碼無效。", 401)
    from ..models import AttendanceDeviceNonce

    if db.session.scalar(
        db.select(AttendanceDeviceNonce.id).where(
            AttendanceDeviceNonce.device_id == device.id,
            AttendanceDeviceNonce.nonce == nonce,
        )
    ):
        raise AttendanceError("REPLAYED_REQUEST", "重複的裝置請求已被拒絕。", 409)
    canonical = "\n".join(
        [request.method.upper(), path, timestamp, nonce, hashlib.sha256(body).hexdigest()]
    ).encode()
    expected = hmac.new(decrypt_device_secret(device), canonical, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AttendanceError("INVALID_DEVICE_SIGNATURE", "裝置簽章驗證失敗。", 401)
    db.session.execute(
        db.delete(AttendanceDeviceNonce).where(AttendanceDeviceNonce.created_at < utc_now() - timedelta(days=1))
    )
    db.session.add(AttendanceDeviceNonce(device_id=device.id, nonce=nonce))
    device.last_seen_at = utc_now()
    device.last_ip = source[:45] or None
    try:
        observe_device_identity(device, json.loads(body or b"{}").get("_device"))
    except (AttributeError, json.JSONDecodeError):
        raise AttendanceError("INVALID_DEVICE_IDENTITY", "裝置識別資料格式錯誤。")
    return device


def identify_card(uid: str) -> tuple[StaffProfile, StaffCard]:
    card = db.session.scalar(
        db.select(StaffCard).where(
            StaffCard.uid_hash == card_uid_hash(uid), StaffCard.status == CardStatus.ACTIVE
        )
    )
    if not card or not card.staff.user.is_active:
        raise AttendanceError("CARD_NOT_REGISTERED", "卡片尚未登錄或已停用，請洽管理員。", 404)
    card.last_used_at = utc_now()
    return card.staff, card


def identify_account(username: str, password: str, device: AttendanceDevice) -> StaffProfile:
    recent_failures = db.session.scalar(
        db.select(db.func.count()).select_from(AuditLog).where(
            AuditLog.action == "ATTENDANCE_LOGIN_FAILED",
            AuditLog.entity_id == device.id,
            AuditLog.created_at >= utc_now() - timedelta(minutes=5),
        )
    ) or 0
    if recent_failures >= 10:
        raise AttendanceError("LOGIN_RATE_LIMITED", "登入失敗次數過多，請五分鐘後再試。", 429)
    user = db.session.scalar(db.select(User).where(User.username == username.strip()))
    if not user or not user.is_active or not user.staff_profile or not user.check_password(password):
        add_audit(None, "ATTENDANCE_LOGIN_FAILED", "AttendanceDevice", device.id, f"裝置 {device.device_code} 帳號打卡驗證失敗")
        db.session.commit()
        raise AttendanceError("INVALID_ACCOUNT", "帳號或密碼錯誤。", 401)
    if user.must_change_password:
        raise AttendanceError("PASSWORD_CHANGE_REQUIRED", "請先在學生系統完成密碼變更。", 403)
    return user.staff_profile


def _shift_window(shift: Shift, tz: ZoneInfo, p: AttendancePolicy) -> tuple[datetime, datetime, datetime, datetime]:
    start_local = datetime.combine(shift.shift_date, shift.shift_type.start_time, tz)
    end_date = shift.shift_date + timedelta(days=1) if shift.shift_type.end_time <= shift.shift_type.start_time else shift.shift_date
    end_local = datetime.combine(end_date, shift.shift_type.end_time, tz)
    return (
        (start_local - timedelta(minutes=p.early_checkin_minutes)).astimezone(timezone.utc),
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        (end_local + timedelta(minutes=p.checkout_after_minutes)).astimezone(timezone.utc),
    )


def classify_punch(profile: StaffProfile, device: AttendanceDevice, occurred_at: datetime) -> dict:
    p = policy()
    tz = ZoneInfo(current_app.config["APP_TIMEZONE"])
    local_day = occurred_at.astimezone(tz).date()
    shifts = db.session.scalars(
        db.select(Shift)
        .join(Shift.shift_type)
        .where(
            Shift.staff_id == profile.id,
            Shift.shift_date.in_([local_day, local_day - timedelta(days=1)]),
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
            ShiftType.location_id == device.location_id,
        )
        .order_by(Shift.shift_date, ShiftType.start_time)
    ).all()
    candidates = []
    for shift in shifts:
        window_start, shift_start, shift_end, window_end = _shift_window(shift, tz, p)
        if window_start <= occurred_at <= window_end:
            distance = min(abs((occurred_at - shift_start).total_seconds()), abs((occurred_at - shift_end).total_seconds()))
            candidates.append((distance, shift, shift_start, shift_end))
    if not candidates:
        return {"shift": None, "direction": AttendanceDirection.UNKNOWN, "status": AttendanceStatus.UNMATCHED, "late": 0}
    chosen = None
    for _, shift, shift_start, shift_end in sorted(candidates, key=lambda item: item[0]):
        existing = db.session.scalars(
            db.select(AttendanceEvent).where(
                AttendanceEvent.shift_id == shift.id,
                AttendanceEvent.staff_id == profile.id,
                AttendanceEvent.status != AttendanceStatus.DUPLICATE,
            ).order_by(AttendanceEvent.occurred_at)
        ).all()
        if not (
            any(item.direction == AttendanceDirection.IN for item in existing)
            and any(item.direction == AttendanceDirection.OUT for item in existing)
        ):
            chosen = (shift, shift_start, shift_end, existing)
            break
    if chosen is None:
        return {"shift": None, "direction": AttendanceDirection.UNKNOWN, "status": AttendanceStatus.UNMATCHED, "late": 0}
    shift, shift_start, shift_end, existing = chosen
    last = existing[-1] if existing else None
    if last and abs((occurred_at - _aware(last.occurred_at)).total_seconds()) <= p.duplicate_seconds:
        return {"shift": shift, "direction": last.direction, "status": AttendanceStatus.DUPLICATE, "late": 0}
    has_in = any(item.direction == AttendanceDirection.IN for item in existing)
    has_out = any(item.direction == AttendanceDirection.OUT for item in existing)
    if has_in and not has_out:
        return {"shift": shift, "direction": AttendanceDirection.OUT, "status": AttendanceStatus.NORMAL, "late": 0}
    midpoint = shift_start + (shift_end - shift_start) / 2
    if not has_in and occurred_at >= midpoint:
        return {"shift": shift, "direction": AttendanceDirection.OUT, "status": AttendanceStatus.MISSING_CLOCK_IN, "late": 0}
    late = max(0, int((occurred_at - shift_start).total_seconds() // 60))
    status = AttendanceStatus.LATE_REASON_REQUIRED if late > p.late_grace_minutes else AttendanceStatus.NORMAL
    return {"shift": shift, "direction": AttendanceDirection.IN, "status": status, "late": late}


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_occurred_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        raise AttendanceError("INVALID_TIME", "打卡時間格式錯誤。")
    if parsed.tzinfo is None:
        raise AttendanceError("INVALID_TIMEZONE", "打卡時間必須包含時區。")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > utc_now() + timedelta(minutes=5) or parsed < utc_now() - timedelta(days=30):
        raise AttendanceError("TIME_OUT_OF_RANGE", "打卡時間超出可接受範圍。")
    return parsed


def create_event(*, device: AttendanceDevice, profile: StaffProfile, card: StaffCard | None, payload: dict) -> AttendanceEvent:
    event_uuid = str(payload.get("event_id", ""))
    try:
        from uuid import UUID
        UUID(event_uuid)
    except (ValueError, TypeError):
        raise AttendanceError("INVALID_EVENT_ID", "事件識別碼格式錯誤。")
    occurred_at = parse_occurred_at(payload.get("occurred_at", ""))
    try:
        sequence = int(payload.get("sequence"))
    except (TypeError, ValueError):
        raise AttendanceError("INVALID_SEQUENCE", "裝置事件序號格式錯誤。")
    if sequence <= 0:
        raise AttendanceError("INVALID_SEQUENCE", "裝置事件序號必須大於零。")
    try:
        method = AttendanceMethod(payload.get("method"))
    except ValueError as exc:
        raise AttendanceError("INVALID_METHOD", "打卡驗證方式不正確。") from exc
    existing = db.session.scalar(db.select(AttendanceEvent).where(AttendanceEvent.event_uuid == event_uuid))
    if existing:
        if (
            existing.device_id != device.id
            or existing.staff_id != profile.id
            or existing.card_id != (card.id if card else None)
            or existing.device_sequence != sequence
            or existing.method != method
            or _aware(existing.occurred_at) != occurred_at
        ):
            raise AttendanceError("EVENT_ID_REUSED", "事件識別碼已被不同的打卡資料使用。", 409)
        return existing
    sequence_owner = db.session.scalar(
        db.select(AttendanceEvent).where(
            AttendanceEvent.device_id == device.id,
            AttendanceEvent.device_sequence == sequence,
        )
    )
    if sequence_owner:
        raise AttendanceError("SEQUENCE_REUSED", "此裝置事件序號已被其他打卡資料使用。", 409)
    result = classify_punch(profile, device, occurred_at)
    event = AttendanceEvent(
        event_uuid=event_uuid,
        device_id=device.id,
        staff_id=profile.id,
        card_id=card.id if card else None,
        shift_id=result["shift"].id if result["shift"] else None,
        method=method,
        direction=result["direction"],
        status=result["status"],
        occurred_at=occurred_at,
        source_ip=(request.remote_addr or "")[:45] or None,
        offline_synced=bool(payload.get("offline")),
        device_sequence=sequence,
        late_minutes=result["late"],
    )
    db.session.add(event)
    device.last_sequence = max(device.last_sequence, sequence)
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise AttendanceError("DUPLICATE_EVENT", "打卡事件或裝置序號已存在，請勿重複送出。", 409) from exc
    if event.status == AttendanceStatus.LATE_REASON_REQUIRED:
        notify_user(
            profile.user_id, key=f"ATTENDANCE_REASON:{event.id}", category="ATTENDANCE_REASON", severity="WARNING",
            title_zh="遲到事由尚未填寫", title_en="Late-arrival reason required",
            message_zh=f"{event.occurred_at.astimezone(ZoneInfo(current_app.config['APP_TIMEZONE'])):%Y-%m-%d %H:%M} 遲到 {event.late_minutes} 分鐘，請填寫事由。",
            message_en=f"Late by {event.late_minutes} minutes. Submit a reason.", target_url="/student/attendance",
        )
    elif event.status in {AttendanceStatus.MISSING_CLOCK_IN, AttendanceStatus.UNMATCHED}:
        if event.status == AttendanceStatus.MISSING_CLOCK_IN:
            notify_user(
                profile.user_id, key=f"ATTENDANCE_REASON:{event.id}", category="ATTENDANCE_REASON", severity="WARNING",
                title_zh="漏刷上班資料尚未補齊", title_en="Missing clock-in details required",
                message_zh="系統只收到下班打卡，請填寫實際到班時間與漏刷原因。",
                message_en="Only a clock-out was found. Submit your arrival time and reason.", target_url="/student/attendance",
            )
        notify_admins(
            key=f"ATTENDANCE_REVIEW:{event.id}", category="ATTENDANCE_REVIEW", severity="WARNING",
            title_zh=f"{profile.name}有待確認的打卡異常", title_en=f"Attendance exception for {profile.name}",
            message_zh="可能漏刷上班或無法對應正式排班，請等待學生說明後審核。",
            message_en="Possible missing clock-in or unmatched shift.", target_url="/admin/attendance",
        )
    add_audit(profile.user_id, "ATTENDANCE_PUNCHED", "AttendanceEvent", event.id, f"{method.value} {event.direction.value}，狀態 {event.status.value}")
    db.session.commit()
    return event


def submit_reason(event: AttendanceEvent, *, category: str, reason: str, claimed_arrival: str | None = None) -> None:
    if event.status not in {
        AttendanceStatus.LATE_REASON_REQUIRED, AttendanceStatus.MISSING_CLOCK_IN, AttendanceStatus.RETURNED
    }:
        raise AttendanceError("REASON_NOT_ALLOWED", "此打卡紀錄目前不需要或不能再修改事由。", 409)
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise AttendanceError("REASON_REQUIRED", "請填寫 1 至 1000 字的事由。")
    event.reason_category = category.strip()[:80] or "其他"
    event.reason_text = reason
    was_returned = event.status == AttendanceStatus.RETURNED
    if event.status == AttendanceStatus.LATE_REASON_REQUIRED or (was_returned and event.direction != AttendanceDirection.OUT):
        event.status = AttendanceStatus.LATE_PENDING_REVIEW
    else:
        if not claimed_arrival:
            raise AttendanceError("ARRIVAL_TIME_REQUIRED", "漏刷上班必須填寫實際到班時間。")
        local = datetime.fromisoformat(claimed_arrival)
        if local.tzinfo is None:
            local = local.replace(tzinfo=ZoneInfo(current_app.config["APP_TIMEZONE"]))
        event.claimed_arrival_at = local.astimezone(timezone.utc)
        event.status = AttendanceStatus.MISSING_CLOCK_IN
    if was_returned:
        db.session.add(AttendanceReviewHistory(
            event=event, previous_status=AttendanceStatus.RETURNED.value,
            new_status=event.status.value, decision="RESUBMITTED", note=reason,
            reviewed_by=event.staff.user_id if event.staff else None,
        ))
        event.reviewed_by = None
        event.reviewed_at = None
        event.review_note = None
        complete_notification(f"ATTENDANCE_RESULT:{event.id}")
    complete_notification(f"ATTENDANCE_REASON:{event.id}")
    notify_admins(
        key=f"ATTENDANCE_REVIEW:{event.id}", category="ATTENDANCE_REVIEW", severity="WARNING",
        title_zh=f"{event.staff.name}的出勤事由等待確認", title_en=f"Attendance reason from {event.staff.name}",
        message_zh=reason[:300], message_en="Review the submitted attendance explanation.", target_url="/admin/attendance",
    )
    db.session.commit()


def review_event(event: AttendanceEvent, *, decision: str, note: str, actor_user_id: int) -> None:
    if decision not in {"APPROVE", "REJECT"}:
        raise AttendanceError("INVALID_DECISION", "審核決定格式錯誤。")
    if event.status not in {
        AttendanceStatus.LATE_PENDING_REVIEW,
        AttendanceStatus.MISSING_CLOCK_IN,
        AttendanceStatus.UNMATCHED,
    }:
        raise AttendanceError("REVIEW_NOT_ALLOWED", "這筆打卡目前不在待審核狀態。", 409)
    note = note.strip()[:1000]
    if decision == "REJECT" and not note:
        raise AttendanceError("REVIEW_NOTE_REQUIRED", "退回時必須填寫管理員備註。")
    previous_status = event.status
    if previous_status == AttendanceStatus.MISSING_CLOCK_IN and decision == "APPROVE":
        if not event.claimed_arrival_at or not event.reason_text:
            raise AttendanceError("INCOMPLETE_EXPLANATION", "學生尚未填寫到班時間與漏刷原因。")
        db.session.add(AttendanceAdjustment(
            event_id=event.id, direction=AttendanceDirection.IN, adjusted_at=event.claimed_arrival_at,
            reason=f"管理員核准漏刷補登：{event.reason_text}", created_by=actor_user_id,
        ))
    if decision == "APPROVE":
        event.status = AttendanceStatus.REVIEWED
    elif previous_status in {AttendanceStatus.LATE_PENDING_REVIEW, AttendanceStatus.MISSING_CLOCK_IN}:
        event.status = AttendanceStatus.RETURNED
    else:
        event.status = AttendanceStatus.REJECTED
    event.reviewed_by = actor_user_id
    event.reviewed_at = utc_now()
    event.review_note = note or None
    db.session.add(AttendanceReviewHistory(
        event=event, previous_status=previous_status.value, new_status=event.status.value,
        decision=decision, note=event.review_note, reviewed_by=actor_user_id,
    ))
    complete_notification(f"ATTENDANCE_REVIEW:{event.id}")
    complete_notification(f"ATTENDANCE_REASON:{event.id}")
    if event.staff:
        result_key = f"ATTENDANCE_RESULT:{event.id}"
        notify_user(
            event.staff.user_id,
            key=result_key,
            category="ATTENDANCE_RESULT",
            severity="SUCCESS" if decision == "APPROVE" else "DANGER",
            title_zh="出勤異常已核准" if decision == "APPROVE" else "出勤異常已退回",
            title_en="Attendance exception approved" if decision == "APPROVE" else "Attendance exception returned",
            message_zh=event.review_note or "管理員已完成確認。",
            message_en="The administrator completed the attendance review.",
            target_url="/student/attendance",
        )
        if decision == "APPROVE":
            complete_notification(result_key)
    add_audit(actor_user_id, f"ATTENDANCE_{decision}D", "AttendanceEvent", event.id, f"審核出勤事件 #{event.id}")
    db.session.commit()


def calculated_hours_for_shifts(shifts: list[Shift]) -> dict[int, Decimal | None]:
    """Calculate many shifts with two bulk queries instead of two queries per shift."""
    shift_ids = [shift.id for shift in shifts]
    if not shift_ids:
        return {}
    events = db.session.scalars(
        db.select(AttendanceEvent)
        .where(
            AttendanceEvent.shift_id.in_(shift_ids),
            AttendanceEvent.status.notin_({AttendanceStatus.DUPLICATE, AttendanceStatus.REJECTED}),
            AttendanceEvent.direction.in_({AttendanceDirection.IN, AttendanceDirection.OUT}),
        )
        .order_by(AttendanceEvent.occurred_at)
    ).all()
    event_ids = [event.id for event in events]
    adjustments = db.session.scalars(
        db.select(AttendanceAdjustment)
        .where(AttendanceAdjustment.event_id.in_(event_ids or [-1]))
        .order_by(AttendanceAdjustment.created_at, AttendanceAdjustment.id)
    ).all()
    events_by_shift: dict[int, list[AttendanceEvent]] = {shift_id: [] for shift_id in shift_ids}
    adjustments_by_event: dict[int, list[AttendanceAdjustment]] = {}
    for event in events:
        if event.shift_id in events_by_shift:
            events_by_shift[event.shift_id].append(event)
    for item in adjustments:
        adjustments_by_event.setdefault(item.event_id, []).append(item)
    return {
        shift_id: _calculated_hours(events_by_shift[shift_id], adjustments_by_event)
        for shift_id in shift_ids
    }


def _calculated_hours(
    events: list[AttendanceEvent], adjustments_by_event: dict[int, list[AttendanceAdjustment]]
) -> Decimal | None:
    in_times = [event.occurred_at for event in events if event.direction == AttendanceDirection.IN]
    out_times = [event.occurred_at for event in events if event.direction == AttendanceDirection.OUT]
    for event in events:
        for item in adjustments_by_event.get(event.id, []):
            (in_times if item.direction == AttendanceDirection.IN else out_times).append(item.adjusted_at)
    if not in_times or not out_times:
        return None
    started_at = min(_aware(value) for value in in_times)
    ended_at = max(_aware(value) for value in out_times)
    if ended_at <= started_at:
        return None
    return (Decimal(str((ended_at - started_at).total_seconds())) / Decimal("3600")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def calculated_hours_for_shift(shift: Shift) -> Decimal | None:
    return calculated_hours_for_shifts([shift]).get(shift.id)


def reconcile_shift_hours(
    shift: Shift, *, payable_hours: Decimal, note: str, actor_user_id: int
) -> AttendanceReconciliation:
    if payable_hours < 0 or payable_hours > 8:
        raise AttendanceError("INVALID_PAYABLE_HOURS", "單筆計薪時數必須介於 0 到 8 小時。")
    note = note.strip()
    if not note or len(note) > 1000:
        raise AttendanceError("RECONCILIATION_NOTE_REQUIRED", "請填寫 1 至 1000 字的核對依據。")
    record = shift.reconciliation or AttendanceReconciliation(shift=shift)
    before = None if record.id is None else {
        "calculated_hours": str(record.calculated_hours) if record.calculated_hours is not None else None,
        "payable_hours": str(record.payable_hours),
        "note": record.note,
    }
    record.calculated_hours = calculated_hours_for_shift(shift)
    record.payable_hours = payable_hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    record.note = note
    record.reviewed_by = actor_user_id
    record.reviewed_at = utc_now()
    db.session.add(record)
    db.session.flush()
    add_audit(
        actor_user_id,
        "ATTENDANCE_HOURS_RECONCILED",
        "AttendanceReconciliation",
        record.id,
        f"核對排班 #{shift.id} 計薪時數 {record.payable_hours}",
        changes={
            "before": before,
            "after": {
                "calculated_hours": str(record.calculated_hours) if record.calculated_hours is not None else None,
                "payable_hours": str(record.payable_hours),
                "note": record.note,
            },
        },
    )
    db.session.commit()
    return record


def attendance_annotations(shifts: Iterable[Shift], *, profile_id: int | None = None) -> dict[int, list[dict]]:
    """Return compact clock-in/out labels for calendar events."""
    shifts = list(shifts)
    shift_ids = {shift.id for shift in shifts if profile_id is None or shift.staff_id == profile_id}
    annotations: dict[int, list[dict]] = defaultdict(list)
    if not shift_ids:
        return annotations
    tz = ZoneInfo(current_app.config["APP_TIMEZONE"])
    events = db.session.scalars(
        db.select(AttendanceEvent)
        .where(
            AttendanceEvent.shift_id.in_(shift_ids),
            AttendanceEvent.status != AttendanceStatus.DUPLICATE,
        )
        .order_by(AttendanceEvent.occurred_at)
    ).all()
    latest: dict[tuple[int, AttendanceDirection], AttendanceEvent] = {}
    for event in events:
        latest[(event.shift_id, event.direction)] = event
    for (shift_id, direction), event in latest.items():
        if direction == AttendanceDirection.UNKNOWN:
            label = "打卡待確認"
        else:
            label = f"{'上班' if direction == AttendanceDirection.IN else '下班'}已打卡 {_aware(event.occurred_at).astimezone(tz):%H:%M}"
        style = (
            "danger" if event.status == AttendanceStatus.REJECTED
            else "warning" if event.status in {
                AttendanceStatus.LATE_REASON_REQUIRED,
                AttendanceStatus.LATE_PENDING_REVIEW,
                AttendanceStatus.MISSING_CLOCK_IN,
                AttendanceStatus.UNMATCHED,
            }
            else "success"
        )
        annotations[shift_id].append({
            "kind": "ATTENDANCE",
            "status": event.status.value,
            "label": label,
            "class": style,
            "eventId": event.id,
            "url": "/student/attendance" if profile_id is not None else f"/admin/attendance?date={event.shift.shift_date}",
        })
    event_ids = [event.id for event in events]
    adjustments = db.session.scalars(
        db.select(AttendanceAdjustment)
        .where(AttendanceAdjustment.event_id.in_(event_ids))
        .order_by(AttendanceAdjustment.created_at)
    ).all() if event_ids else []
    for item in adjustments:
        event = item.event
        if not event.shift_id:
            continue
        direction_label = "上班" if item.direction == AttendanceDirection.IN else "下班"
        annotations[event.shift_id].append({
            "kind": "ATTENDANCE_ADJUSTMENT", "status": "REVIEWED",
            "label": f"補登{direction_label} {_aware(item.adjusted_at).astimezone(tz):%H:%M}",
            "class": "info", "eventId": event.id,
            "url": "/student/attendance" if profile_id is not None else f"/admin/attendance?date={event.shift.shift_date}",
        })
    return annotations


def event_json(event: AttendanceEvent) -> dict:
    return {
        "event_id": event.event_uuid,
        "student": event.staff.name if event.staff else None,
        "direction": event.direction.value,
        "status": event.status.value,
        "occurred_at": _aware(event.occurred_at).isoformat(),
        "late_minutes": event.late_minutes,
        "requires_reason": event.status in {AttendanceStatus.LATE_REASON_REQUIRED, AttendanceStatus.MISSING_CLOCK_IN, AttendanceStatus.RETURNED},
        "requires_arrival_time": event.status == AttendanceStatus.MISSING_CLOCK_IN or (event.status == AttendanceStatus.RETURNED and event.direction == AttendanceDirection.OUT),
        "location": event.device.location.name,
        "shift": (
            f"{event.shift.shift_type.start_time:%H:%M}–{event.shift.shift_type.end_time:%H:%M}"
            if event.shift else None
        ),
    }
