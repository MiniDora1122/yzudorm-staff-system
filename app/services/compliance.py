from __future__ import annotations

from datetime import date

from ..extensions import db
from ..models import (
    Country,
    DocumentStatus,
    DocumentType,
    SchedulingExceptionPeriod,
    SchedulingPolicy,
    StaffDocument,
    StaffProfile,
)


def active_countries() -> list[Country]:
    return db.session.scalars(
        db.select(Country)
        .where(Country.is_active.is_(True))
        .order_by(Country.display_order, Country.name)
    ).all()


def country_by_name(name: str, *, active_only: bool = True) -> Country | None:
    statement = db.select(Country).where(Country.name == name.strip())
    if active_only:
        statement = statement.where(Country.is_active.is_(True))
    return db.session.scalar(statement)


def canonical_country_name(name: str) -> str:
    country = country_by_name(name)
    if country is None:
        raise ValueError("請從管理員建立的國籍清單中選擇。 / Select a configured nationality.")
    return country.name


def is_taiwan_nationality(profile_or_name: StaffProfile | str) -> bool:
    name = profile_or_name.nationality if isinstance(profile_or_name, StaffProfile) else profile_or_name
    country = country_by_name(name, active_only=False)
    if country is not None:
        return country.is_taiwan
    return name.strip().casefold() in {"台灣", "臺灣", "taiwan", "roc", "中華民國"}


def requires_work_documents(profile: StaffProfile) -> bool:
    return not is_taiwan_nationality(profile)


def confirmed_document_types(profile: StaffProfile) -> set[DocumentType]:
    return set(
        db.session.scalars(
            db.select(StaffDocument.document_type)
            .where(
                StaffDocument.staff_id == profile.id,
                StaffDocument.status == DocumentStatus.CONFIRMED,
            )
            .distinct()
        ).all()
    )


def missing_required_document_types(profile: StaffProfile) -> set[DocumentType]:
    if not requires_work_documents(profile):
        return set()
    return {DocumentType.RESIDENCE_PERMIT, DocumentType.WORK_PERMIT} - confirmed_document_types(profile)


def get_scheduling_policy() -> SchedulingPolicy | None:
    return db.session.get(SchedulingPolicy, 1)


def is_weekly_limit_exception_day(value: date) -> bool:
    return (
        db.session.scalar(
            db.select(SchedulingExceptionPeriod.id)
            .where(
                SchedulingExceptionPeriod.is_active.is_(True),
                SchedulingExceptionPeriod.starts_on <= value,
                SchedulingExceptionPeriod.ends_on >= value,
            )
            .limit(1)
        )
        is not None
    )


def weekly_limit_applies(profile: StaffProfile, shift_date: date) -> bool:
    policy = get_scheduling_policy()
    if policy is None or not policy.foreign_weekly_limit_enabled:
        return False
    if is_weekly_limit_exception_day(shift_date):
        return False
    country = country_by_name(profile.nationality, active_only=False)
    return not (country and country.weekly_limit_exempt)
