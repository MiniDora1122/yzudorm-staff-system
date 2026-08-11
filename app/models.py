from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask_login import UserMixin
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum as SqlEnum, ForeignKey, Integer, Numeric, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db


password_hasher = PasswordHasher()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, Enum):
    ADMIN = "ADMIN"
    STUDENT = "STUDENT"


class ShiftStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    ON_LEAVE = "ON_LEAVE"
    CANCELLED = "CANCELLED"


class LeaveStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class SwapPeerStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class SwapAdminStatus(str, Enum):
    NOT_READY = "NOT_READY"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class DocumentType(str, Enum):
    RESIDENCE_PERMIT = "RESIDENCE_PERMIT"
    WORK_PERMIT = "WORK_PERMIT"


class DocumentPageKind(str, Enum):
    RESIDENCE_FRONT = "RESIDENCE_FRONT"
    RESIDENCE_BACK = "RESIDENCE_BACK"
    WORK_PERMIT_PAGE_1 = "WORK_PERMIT_PAGE_1"
    WORK_PERMIT_PAGE_2 = "WORK_PERMIT_PAGE_2"


class DocumentStatus(str, Enum):
    UPLOADED = "UPLOADED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PENDING_ADMIN = "PENDING_ADMIN"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    REPLACED = "REPLACED"
    FAILED = "FAILED"
    DELETED = "DELETED"


class NotificationStatus(str, Enum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(SqlEnum(Role, native_enum=False), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    staff_profile: Mapped[StaffProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    created_shifts: Mapped[list[Shift]] = relationship(
        back_populates="creator", foreign_keys="Shift.created_by"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = password_hasher.hash(password)

    def check_password(self, password: str) -> bool:
        try:
            valid = password_hasher.verify(self.password_hash, password)
        except (VerificationError, InvalidHashError):
            return False
        if valid and password_hasher.check_needs_rehash(self.password_hash):
            self.set_password(password)
        return valid

    def has_role(self, role: Role) -> bool:
        return self.role == role


class StaffProfile(db.Model):
    __tablename__ = "staff_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    student_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(255))
    nationality: Mapped[str] = mapped_column(String(80), default="台灣", nullable=False)
    residence_id: Mapped[str | None] = mapped_column(String(100))
    residence_expiry: Mapped[date | None] = mapped_column(Date)
    work_permit_start: Mapped[date | None] = mapped_column(Date)
    work_permit_expiry: Mapped[date | None] = mapped_column(Date)
    hourly_wage: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    labor_insured_salary: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    health_insured_salary: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    pension_salary: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    labor_insurance_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    employment_insurance_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    health_insurance_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    labor_pension_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="staff_profile")
    shifts: Mapped[list[Shift]] = relationship(back_populates="staff")
    documents: Mapped[list[StaffDocument]] = relationship(
        back_populates="staff", cascade="all, delete-orphan"
    )


class WorkLocation(db.Model):
    __tablename__ = "work_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(7), default="#1556a3", nullable=False)
    display_order: Mapped[int] = mapped_column(default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    shift_types: Mapped[list[ShiftType]] = relationship(back_populates="work_location")


class ShiftType(db.Model):
    __tablename__ = "shift_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    location_id: Mapped[int] = mapped_column(ForeignKey("work_locations.id"), nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    default_hours: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    display_order: Mapped[int] = mapped_column(default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    work_location: Mapped[WorkLocation] = relationship(back_populates="shift_types")
    shifts: Mapped[list[Shift]] = relationship(back_populates="shift_type")

    @property
    def location(self) -> str:
        """Compatibility alias used by calendar serializers."""
        return self.work_location.code


class PayrollSetting(db.Model):
    __tablename__ = "payroll_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    effective_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    default_hourly_wage: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    labor_insurance_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    employment_insurance_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    employer_labor_share: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    occupational_accident_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    health_insurance_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    employer_health_share: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    average_dependents: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    supplementary_health_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    employer_pension_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )



class Shift(db.Model):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    shift_type_id: Mapped[int] = mapped_column(ForeignKey("shift_types.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("shift_series.id"), index=True)
    status: Mapped[ShiftStatus] = mapped_column(
        SqlEnum(ShiftStatus, native_enum=False), default=ShiftStatus.SCHEDULED, nullable=False
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    shift_type: Mapped[ShiftType] = relationship(back_populates="shifts")
    staff: Mapped[StaffProfile] = relationship(back_populates="shifts")
    creator: Mapped[User] = relationship(back_populates="created_shifts", foreign_keys=[created_by])
    series: Mapped[ShiftSeries | None] = relationship(back_populates="shifts")


class ShiftSeries(db.Model):
    __tablename__ = "shift_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False, index=True)
    shift_type_id: Mapped[int] = mapped_column(ForeignKey("shift_types.id"), nullable=False, index=True)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    staff: Mapped[StaffProfile] = relationship(foreign_keys=[staff_id])
    shift_type: Mapped[ShiftType] = relationship(foreign_keys=[shift_type_id])
    creator: Mapped[User] = relationship(foreign_keys=[created_by])
    shifts: Mapped[list[Shift]] = relationship(back_populates="series")


class LeaveRequest(db.Model):
    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False, index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[LeaveStatus] = mapped_column(
        SqlEnum(LeaveStatus, native_enum=False), default=LeaveStatus.PENDING, nullable=False
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    staff: Mapped[StaffProfile] = relationship(foreign_keys=[staff_id])
    shift: Mapped[Shift] = relationship(foreign_keys=[shift_id])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])


class SwapRequest(db.Model):
    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False, index=True)
    requester_shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), nullable=False, index=True)
    target_staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False, index=True)
    target_shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"), index=True)
    peer_status: Mapped[SwapPeerStatus] = mapped_column(
        SqlEnum(SwapPeerStatus, native_enum=False), default=SwapPeerStatus.PENDING, nullable=False
    )
    admin_status: Mapped[SwapAdminStatus] = mapped_column(
        SqlEnum(SwapAdminStatus, native_enum=False), default=SwapAdminStatus.NOT_READY, nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    peer_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    requester: Mapped[StaffProfile] = relationship(foreign_keys=[requester_id])
    requester_shift: Mapped[Shift] = relationship(foreign_keys=[requester_shift_id])
    target_staff: Mapped[StaffProfile] = relationship(foreign_keys=[target_staff_id])
    target_shift: Mapped[Shift | None] = relationship(foreign_keys=[target_shift_id])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])

    @property
    def display_status(self) -> str:
        if self.admin_status == SwapAdminStatus.APPROVED:
            return "APPROVED"
        if self.admin_status == SwapAdminStatus.REJECTED:
            return "REJECTED"
        if self.admin_status == SwapAdminStatus.CANCELLED:
            return "CANCELLED"
        if self.peer_status == SwapPeerStatus.REJECTED:
            return "PEER_REJECTED"
        if self.peer_status == SwapPeerStatus.ACCEPTED:
            return "PENDING_ADMIN"
        return "PENDING_PEER"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False, index=True)
    safe_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    actor: Mapped[User] = relationship(foreign_keys=[actor_user_id])


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "(recipient_role IS NOT NULL AND recipient_user_id IS NULL) OR "
            "(recipient_role IS NULL AND recipient_user_id IS NOT NULL)",
            name="ck_notification_single_recipient",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_key: Mapped[str] = mapped_column(String(180), unique=True, nullable=False, index=True)
    recipient_role: Mapped[Role | None] = mapped_column(SqlEnum(Role, native_enum=False), index=True)
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    title_zh: Mapped[str] = mapped_column(String(200), nullable=False)
    title_en: Mapped[str] = mapped_column(String(200), nullable=False)
    message_zh: Mapped[str] = mapped_column(String(500), nullable=False)
    message_en: Mapped[str] = mapped_column(String(500), nullable=False)
    target_url: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        SqlEnum(NotificationStatus, native_enum=False),
        default=NotificationStatus.OPEN,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    recipient_user: Mapped[User | None] = relationship(foreign_keys=[recipient_user_id])


class StaffDocument(db.Model):
    __tablename__ = "staff_documents"
    __table_args__ = (
        UniqueConstraint("document_set_id", "page_kind", name="uq_document_set_page"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False, index=True)
    document_type: Mapped[DocumentType] = mapped_column(
        SqlEnum(DocumentType, native_enum=False), nullable=False, index=True
    )
    document_set_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    page_kind: Mapped[DocumentPageKind] = mapped_column(
        SqlEnum(DocumentPageKind, native_enum=False), nullable=False
    )
    storage_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    image_width: Mapped[int] = mapped_column(nullable=False)
    image_height: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[DocumentStatus] = mapped_column(
        SqlEnum(DocumentStatus, native_enum=False), default=DocumentStatus.UPLOADED, nullable=False, index=True
    )
    privacy_notice_version: Mapped[str] = mapped_column(String(30), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staff: Mapped[StaffProfile] = relationship(back_populates="documents", foreign_keys=[staff_id])
    confirmer: Mapped[User | None] = relationship(foreign_keys=[confirmed_by])
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])
    draft: Mapped[DocumentDraft | None] = relationship(
        back_populates="document", uselist=False, cascade="all, delete-orphan"
    )


class DocumentRetentionPolicy(db.Model):
    __tablename__ = "document_retention_policies"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    retention_days: Mapped[int] = mapped_column(default=365, nullable=False)
    cleanup_hour: Mapped[int] = mapped_column(default=3, nullable=False)
    cleanup_minute: Mapped[int] = mapped_column(default=0, nullable=False)
    last_cleanup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    updater: Mapped[User] = relationship(foreign_keys=[updated_by])


class DocumentDraft(db.Model):
    __tablename__ = "document_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("staff_documents.id"), unique=True, nullable=False
    )
    residence_id: Mapped[str | None] = mapped_column(String(100))
    residence_expiry: Mapped[date | None] = mapped_column(Date)
    work_permit_start: Mapped[date | None] = mapped_column(Date)
    work_permit_expiry: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[StaffDocument] = relationship(back_populates="draft")
