from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
import json

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask_login import UserMixin
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Enum as SqlEnum, ForeignKey, Index, Integer, Numeric, String, Text, Time, UniqueConstraint
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


class ShiftPublicationStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class BackupScheduleMode(str, Enum):
    INTERVAL = "INTERVAL"
    DAILY = "DAILY"


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


class RequirementStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class VacancyApplicationStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AttendanceMethod(str, Enum):
    CARD = "CARD"
    ACCOUNT = "ACCOUNT"
    ADMIN = "ADMIN"


class AttendanceDirection(str, Enum):
    IN = "IN"
    OUT = "OUT"
    UNKNOWN = "UNKNOWN"


class AttendanceStatus(str, Enum):
    NORMAL = "NORMAL"
    LATE_REASON_REQUIRED = "LATE_REASON_REQUIRED"
    LATE_PENDING_REVIEW = "LATE_PENDING_REVIEW"
    MISSING_CLOCK_IN = "MISSING_CLOCK_IN"
    UNMATCHED = "UNMATCHED"
    REVIEWED = "REVIEWED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"


class CardStatus(str, Enum):
    ACTIVE = "ACTIVE"
    LOST = "LOST"
    REPLACED = "REPLACED"
    REVOKED = "REVOKED"


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
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[int | None] = mapped_column(Integer)

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


class Country(db.Model):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    name_en: Mapped[str] = mapped_column(String(100), nullable=False)
    is_taiwan: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weekly_limit_exempt: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SchedulingPolicy(db.Model):
    __tablename__ = "scheduling_policies"
    __table_args__ = (
        CheckConstraint("week_starts_on BETWEEN 0 AND 6", name="ck_scheduling_policy_week_starts_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    foreign_weekly_limit_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weekly_hour_limit: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("20"), nullable=False)
    # Python weekday convention: Monday=0, Sunday=6.
    week_starts_on: Mapped[int] = mapped_column(default=0, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SchedulingExceptionPeriod(db.Model):
    __tablename__ = "scheduling_exception_periods"
    __table_args__ = (
        CheckConstraint("ends_on >= starts_on", name="ck_scheduling_exception_date_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


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
    __table_args__ = (
        Index("ix_shifts_staff_status_date", "staff_id", "status", "shift_date"),
        Index("ix_shifts_status_publication_date", "status", "publication_status", "shift_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    shift_type_id: Mapped[int] = mapped_column(ForeignKey("shift_types.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("shift_series.id"), index=True)
    status: Mapped[ShiftStatus] = mapped_column(
        SqlEnum(ShiftStatus, native_enum=False), default=ShiftStatus.SCHEDULED, nullable=False
    )
    publication_status: Mapped[ShiftPublicationStatus] = mapped_column(
        SqlEnum(ShiftPublicationStatus, native_enum=False),
        default=ShiftPublicationStatus.PUBLISHED,
        nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[int | None] = mapped_column(Integer)
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
    __table_args__ = (Index("ix_leave_requests_status_created", "status", "created_at"),)

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
    __table_args__ = (Index("ix_swap_requests_admin_status_created", "admin_status", "created_at"),)

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
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False, index=True)
    safe_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), index=True)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    http_method: Mapped[str | None] = mapped_column(String(10))
    route: Mapped[str | None] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    actor: Mapped[User] = relationship(foreign_keys=[actor_user_id])


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "(recipient_role IS NOT NULL AND recipient_user_id IS NULL) OR "
            "(recipient_role IS NULL AND recipient_user_id IS NOT NULL)",
            name="ck_notification_single_recipient",
        ),
        Index("ix_notifications_role_status", "recipient_role", "status"),
        Index("ix_notifications_user_status", "recipient_user_id", "status"),
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


class MonthlySettlement(db.Model):
    __tablename__ = "monthly_settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    month_start: Mapped[date] = mapped_column(Date, unique=True, nullable=False, index=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    snapshot_json: Mapped[str | None] = mapped_column(Text)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlocked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlock_reason: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    closer: Mapped[User | None] = relationship(foreign_keys=[closed_by])
    unlocker: Mapped[User | None] = relationship(foreign_keys=[unlocked_by])


class BackupRun(db.Model):
    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column()
    sha256: Mapped[str | None] = mapped_column(String(64))
    validation_message: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackupPolicy(db.Model):
    __tablename__ = "backup_policies"
    __table_args__ = (
        CheckConstraint("interval_hours >= 1 AND interval_hours <= 168", name="ck_backup_policy_interval_hours"),
        CheckConstraint("daily_hour >= 0 AND daily_hour <= 23", name="ck_backup_policy_daily_hour"),
        CheckConstraint("daily_minute >= 0 AND daily_minute <= 59", name="ck_backup_policy_daily_minute"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    mode: Mapped[BackupScheduleMode] = mapped_column(
        SqlEnum(BackupScheduleMode, native_enum=False), default=BackupScheduleMode.DAILY, nullable=False
    )
    interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    daily_hour: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    daily_minute: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    updater: Mapped[User] = relationship(foreign_keys=[updated_by])


class StaffGroup(db.Model):
    __tablename__ = "staff_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name_en: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    memberships: Mapped[list[StaffGroupMember]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class StaffGroupMember(db.Model):
    __tablename__ = "staff_group_members"

    group_id: Mapped[int] = mapped_column(ForeignKey("staff_groups.id"), primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    group: Mapped[StaffGroup] = relationship(back_populates="memberships")
    staff: Mapped[StaffProfile] = relationship()


class StaffingRequirement(db.Model):
    __tablename__ = "staffing_requirements"
    __table_args__ = (
        UniqueConstraint("shift_date", "shift_type_id", name="uq_staffing_requirement_slot"),
        CheckConstraint("required_count > 0", name="ck_staffing_requirement_positive_count"),
        Index("ix_staffing_requirements_status_date", "status", "shift_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_date: Mapped[date] = mapped_column(Date, nullable=False)
    shift_type_id: Mapped[int] = mapped_column(ForeignKey("shift_types.id"), nullable=False)
    required_count: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[RequirementStatus] = mapped_column(
        SqlEnum(RequirementStatus, native_enum=False), default=RequirementStatus.OPEN, nullable=False
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    shift_type: Mapped[ShiftType] = relationship()
    audience_groups: Mapped[list[RequirementAudienceGroup]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )
    audience_staff: Mapped[list[RequirementAudienceStaff]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )
    applications: Mapped[list[VacancyApplication]] = relationship(
        back_populates="requirement", cascade="all, delete-orphan"
    )


class RequirementAudienceGroup(db.Model):
    __tablename__ = "requirement_audience_groups"

    requirement_id: Mapped[int] = mapped_column(ForeignKey("staffing_requirements.id"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("staff_groups.id"), primary_key=True)

    requirement: Mapped[StaffingRequirement] = relationship(back_populates="audience_groups")
    group: Mapped[StaffGroup] = relationship()


class RequirementAudienceStaff(db.Model):
    __tablename__ = "requirement_audience_staff"

    requirement_id: Mapped[int] = mapped_column(ForeignKey("staffing_requirements.id"), primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), primary_key=True)

    requirement: Mapped[StaffingRequirement] = relationship(back_populates="audience_staff")
    staff: Mapped[StaffProfile] = relationship()


class VacancyApplication(db.Model):
    __tablename__ = "vacancy_applications"
    __table_args__ = (
        UniqueConstraint("requirement_id", "staff_id", name="uq_vacancy_application_staff"),
        Index("ix_vacancy_applications_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("staffing_requirements.id"), nullable=False)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False)
    status: Mapped[VacancyApplicationStatus] = mapped_column(
        SqlEnum(VacancyApplicationStatus, native_enum=False),
        default=VacancyApplicationStatus.PENDING,
        nullable=False,
    )
    note: Mapped[str | None] = mapped_column(String(500))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    requirement: Mapped[StaffingRequirement] = relationship(back_populates="applications")
    staff: Mapped[StaffProfile] = relationship()
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])


class AttendancePolicy(db.Model):
    __tablename__ = "attendance_policies"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    early_checkin_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    late_grace_minutes: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    checkout_after_minutes: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    duplicate_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class AttendanceDevice(db.Model):
    __tablename__ = "attendance_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location_id: Mapped[int] = mapped_column(ForeignKey("work_locations.id"), nullable=False)
    allowed_cidr: Mapped[str | None] = mapped_column(String(80))
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    enrollment_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    installation_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    computer_name: Mapped[str | None] = mapped_column(String(255))
    mac_addresses_json: Mapped[str | None] = mapped_column(Text)
    pending_computer_name: Mapped[str | None] = mapped_column(String(255))
    pending_mac_addresses_json: Mapped[str | None] = mapped_column(Text)
    identity_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(45))
    last_sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    location: Mapped[WorkLocation] = relationship()

    @property
    def mac_addresses(self) -> list[str]:
        try:
            return json.loads(self.mac_addresses_json or "[]")
        except (TypeError, json.JSONDecodeError):
            return []

    @property
    def pending_mac_addresses(self) -> list[str]:
        try:
            return json.loads(self.pending_mac_addresses_json or "[]")
        except (TypeError, json.JSONDecodeError):
            return []


class AttendanceDeviceNonce(db.Model):
    __tablename__ = "attendance_device_nonces"
    __table_args__ = (UniqueConstraint("device_id", "nonce", name="uq_attendance_device_nonce"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("attendance_devices.id"), nullable=False, index=True)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class StaffCard(db.Model):
    __tablename__ = "staff_cards"
    __table_args__ = (
        Index("ix_staff_cards_staff_status", "staff_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff_profiles.id"), nullable=False)
    uid_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    uid_last4: Mapped[str] = mapped_column(String(4), nullable=False)
    status: Mapped[CardStatus] = mapped_column(SqlEnum(CardStatus, native_enum=False), default=CardStatus.ACTIVE, nullable=False)
    registered_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    disabled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disable_reason: Mapped[str | None] = mapped_column(String(500))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    staff: Mapped[StaffProfile] = relationship()


class AttendanceEvent(db.Model):
    __tablename__ = "attendance_events"
    __table_args__ = (
        Index("ix_attendance_events_staff_time", "staff_id", "occurred_at"),
        Index("ix_attendance_events_status_time", "status", "occurred_at"),
        Index("ix_attendance_events_shift_direction", "shift_id", "direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("attendance_devices.id"), nullable=False)
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff_profiles.id"), index=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("staff_cards.id"))
    shift_id: Mapped[int | None] = mapped_column(ForeignKey("shifts.id"), index=True)
    method: Mapped[AttendanceMethod] = mapped_column(SqlEnum(AttendanceMethod, native_enum=False), nullable=False)
    direction: Mapped[AttendanceDirection] = mapped_column(SqlEnum(AttendanceDirection, native_enum=False), nullable=False)
    status: Mapped[AttendanceStatus] = mapped_column(SqlEnum(AttendanceStatus, native_enum=False), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(45))
    offline_synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    device_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    late_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reason_category: Mapped[str | None] = mapped_column(String(80))
    reason_text: Mapped[str | None] = mapped_column(String(1000))
    claimed_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(1000))

    device: Mapped[AttendanceDevice] = relationship()
    staff: Mapped[StaffProfile | None] = relationship()
    card: Mapped[StaffCard | None] = relationship()
    shift: Mapped[Shift | None] = relationship()
    reviewer: Mapped[User | None] = relationship(foreign_keys=[reviewed_by])


class AttendanceAdjustment(db.Model):
    __tablename__ = "attendance_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("attendance_events.id"), nullable=False, index=True)
    direction: Mapped[AttendanceDirection] = mapped_column(SqlEnum(AttendanceDirection, native_enum=False), nullable=False)
    adjusted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    event: Mapped[AttendanceEvent] = relationship()
