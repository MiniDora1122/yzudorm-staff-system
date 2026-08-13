from __future__ import annotations

import re

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Role, StaffProfile, User
from .audit import add_audit


USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{3,80}")
STUDENT_NUMBER_PATTERN = re.compile(r"[A-Za-z0-9_-]{3,30}")


class AccountError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_username(value: str) -> str:
    username = value.strip().lower()
    if not USERNAME_PATTERN.fullmatch(username):
        raise AccountError("INVALID_USERNAME", "帳號需為 3–80 位英數字、句點、底線或連字號。")
    return username


def normalize_student_number(value: str) -> str:
    student_number = value.strip().upper()
    if not STUDENT_NUMBER_PATTERN.fullmatch(student_number):
        raise AccountError("INVALID_STUDENT_NUMBER", "學號需為 3–30 位英數字、底線或連字號。")
    return student_number


def validate_temporary_password(password: str, confirmation: str) -> None:
    if password != confirmation:
        raise AccountError("PASSWORD_MISMATCH", "兩次輸入的臨時密碼不一致。")
    if len(password) < 8 or len(password) > 128:
        raise AccountError("INVALID_PASSWORD", "臨時密碼長度需為 8–128 個字元。")


def validate_profile_fields(*, name: str, email: str, phone: str, nationality: str) -> None:
    if not name or len(name) > 100:
        raise AccountError("INVALID_NAME", "姓名需為 1–100 字。")
    if len(email) > 255 or (email and ("@" not in email or "." not in email.rsplit("@", 1)[-1])):
        raise AccountError("INVALID_EMAIL", "Email 格式錯誤。")
    if len(phone) > 30 or not nationality or len(nationality) > 80:
        raise AccountError("INVALID_PROFILE", "聯絡電話或國籍格式錯誤。")


def validate_display_name(display_name: str) -> str:
    display_name = display_name.strip()
    if not display_name or len(display_name) > 100:
        raise AccountError("INVALID_DISPLAY_NAME", "顯示名稱需為 1–100 字。")
    return display_name


def ensure_unique_username(username: str) -> None:
    if (
        db.session.scalar(
            db.select(User.id).where(db.func.lower(User.username) == username)
        )
        is not None
    ):
        raise AccountError("DUPLICATE_USERNAME", "此登入帳號已存在。")


def create_admin_account(
    *,
    username: str,
    display_name: str,
    temporary_password: str,
    password_confirmation: str,
    actor_user_id: int,
) -> User:
    username = normalize_username(username)
    display_name = validate_display_name(display_name)
    validate_temporary_password(temporary_password, password_confirmation)
    ensure_unique_username(username)

    user = User(
        username=username,
        display_name=display_name,
        role=Role.ADMIN,
        is_active=True,
        must_change_password=True,
    )
    user.set_password(temporary_password)
    db.session.add(user)
    try:
        db.session.flush()
        add_audit(
            actor_user_id,
            "ADMIN_ACCOUNT_CREATED",
            "User",
            user.id,
            f"建立管理員帳號 {username}，登入後須修改臨時密碼",
        )
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise AccountError("DUPLICATE_ACCOUNT", "帳號已存在，未建立任何資料。") from exc
    return user


def reset_admin_password(
    *,
    user: User,
    temporary_password: str,
    password_confirmation: str,
    actor_user_id: int,
) -> None:
    validate_temporary_password(temporary_password, password_confirmation)
    if user.role != Role.ADMIN:
        raise AccountError("INVALID_ROLE", "只能重設管理員帳號的密碼。")
    if user.id == actor_user_id:
        raise AccountError("SELF_RESET_NOT_ALLOWED", "不可在此重設自己的密碼，請使用導覽列的修改密碼功能。")
    user.set_password(temporary_password)
    user.must_change_password = True
    add_audit(
        actor_user_id,
        "ADMIN_PASSWORD_RESET",
        "User",
        user.id,
        f"重設管理員帳號 {user.username} 的臨時密碼，登入後須修改",
    )
    db.session.commit()


def create_student_account(
    *,
    username: str,
    temporary_password: str,
    password_confirmation: str,
    name: str,
    student_number: str,
    email: str,
    phone: str,
    nationality: str,
    actor_user_id: int,
) -> StaffProfile:
    username = normalize_username(username)
    student_number = normalize_student_number(student_number)
    name = name.strip()
    email = email.strip()
    phone = phone.strip()
    nationality = nationality.strip()
    validate_temporary_password(temporary_password, password_confirmation)
    validate_profile_fields(name=name, email=email, phone=phone, nationality=nationality)

    ensure_unique_username(username)
    if (
        db.session.scalar(
            db.select(StaffProfile.id).where(StaffProfile.student_number == student_number)
        )
        is not None
    ):
        raise AccountError("DUPLICATE_STUDENT_NUMBER", "此學號已由其他工讀生使用。")

    user = User(
        username=username,
        role=Role.STUDENT,
        is_active=True,
        must_change_password=True,
    )
    user.set_password(temporary_password)
    profile = StaffProfile(
        user=user,
        name=name,
        student_number=student_number,
        email=email or None,
        phone=phone or None,
        nationality=nationality,
    )
    db.session.add(profile)
    try:
        db.session.flush()
        add_audit(
            actor_user_id,
            "STUDENT_ACCOUNT_CREATED",
            "User",
            user.id,
            f"建立工讀生帳號 {username}，學號 {student_number}，登入後須修改臨時密碼",
        )
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise AccountError("DUPLICATE_ACCOUNT", "帳號或學號已存在，未建立任何資料。") from exc
    return profile


def reset_student_password(
    *,
    profile: StaffProfile,
    temporary_password: str,
    password_confirmation: str,
    actor_user_id: int,
) -> None:
    validate_temporary_password(temporary_password, password_confirmation)
    if profile.user.role != Role.STUDENT:
        raise AccountError("INVALID_ROLE", "只能重設工讀生帳號的密碼。")
    profile.user.set_password(temporary_password)
    profile.user.must_change_password = True
    add_audit(
        actor_user_id,
        "STUDENT_PASSWORD_RESET",
        "User",
        profile.user.id,
        f"重設工讀生帳號 {profile.user.username} 的臨時密碼，登入後須修改",
    )
    db.session.commit()
