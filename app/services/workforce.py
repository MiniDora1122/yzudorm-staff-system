from __future__ import annotations

from datetime import date

from flask import url_for

from ..extensions import db
from ..models import (
    RequirementStatus,
    Role,
    Shift,
    ShiftPublicationStatus,
    ShiftStatus,
    StaffGroupMember,
    StaffingRequirement,
    StaffProfile,
    User,
    VacancyApplication,
    VacancyApplicationStatus,
    utc_now,
)
from .audit import add_audit
from .notifications import complete_notification, notify_admins, notify_user
from .periods import ensure_month_open
from .scheduling import SchedulingConflict, create_shift, validate_shift_assignment


class WorkforceError(ValueError):
    pass


def assigned_count(requirement: StaffingRequirement) -> int:
    return db.session.scalar(
        db.select(db.func.count())
        .select_from(Shift)
        .where(
            Shift.shift_date == requirement.shift_date,
            Shift.shift_type_id == requirement.shift_type_id,
            Shift.status == ShiftStatus.SCHEDULED,
            Shift.publication_status == ShiftPublicationStatus.PUBLISHED,
        )
    ) or 0


def vacancies(requirement: StaffingRequirement) -> int:
    return max(0, requirement.required_count - assigned_count(requirement))


def eligible_staff_ids(requirement: StaffingRequirement) -> set[int]:
    direct = {item.staff_id for item in requirement.audience_staff}
    group_ids = [item.group_id for item in requirement.audience_groups]
    if group_ids:
        direct.update(
            db.session.scalars(
                db.select(StaffGroupMember.staff_id).where(StaffGroupMember.group_id.in_(group_ids))
            ).all()
        )
    if not direct:
        direct.update(
            db.session.scalars(
                db.select(StaffProfile.id)
                .join(StaffProfile.user)
                .where(User.is_active.is_(True))
            ).all()
        )
    return direct


def publish_requirement_notifications(requirement: StaffingRequirement) -> None:
    for profile in db.session.scalars(
        db.select(StaffProfile).where(StaffProfile.id.in_(eligible_staff_ids(requirement)))
    ):
        if not profile.user.is_active:
            continue
        notify_user(
            profile.user_id,
            key=f"VACANCY:{requirement.id}:USER:{profile.user_id}",
            category="VACANCY_AVAILABLE",
            severity="INFO",
            title_zh=f"{requirement.shift_date} 有開放缺員班次",
            title_en="An eligible vacancy is available",
            message_zh=f"{requirement.shift_type.work_location.name}｜{requirement.shift_type.name}，可至缺員專區申請。",
            message_en="Open the vacancy page to review and apply.",
            target_url=url_for("student.vacancies_page"),
        )


def complete_requirement_notifications(requirement: StaffingRequirement) -> None:
    for user_id in db.session.scalars(
        db.select(User.id).where(User.role == Role.STUDENT)
    ):
        complete_notification(f"VACANCY:{requirement.id}:USER:{user_id}")


def submit_application(
    requirement: StaffingRequirement,
    profile: StaffProfile,
    *,
    note: str,
    actor_user_id: int,
    today: date,
) -> VacancyApplication:
    ensure_month_open(requirement.shift_date)
    if requirement.status != RequirementStatus.OPEN or requirement.shift_date < today:
        raise WorkforceError("此缺員班次已關閉或已過期。 / This vacancy is closed or expired.")
    if profile.id not in eligible_staff_ids(requirement):
        raise WorkforceError("此缺員未對你的帳號或群組開放。 / You are not eligible for this vacancy.")
    if vacancies(requirement) <= 0:
        raise WorkforceError("此班所需人數已補足。 / This staffing requirement is already filled.")
    try:
        validate_shift_assignment(
            shift_date=requirement.shift_date,
            shift_type=requirement.shift_type,
            staff=profile,
            allow_location_overlap=True,
        )
    except SchedulingConflict as exc:
        raise WorkforceError(f"此班與你的班表或時數限制衝突：{exc.message}") from exc
    application = db.session.scalar(
        db.select(VacancyApplication).where(
            VacancyApplication.requirement_id == requirement.id,
            VacancyApplication.staff_id == profile.id,
        )
    )
    if application and application.status in {
        VacancyApplicationStatus.PENDING,
        VacancyApplicationStatus.APPROVED,
    }:
        raise WorkforceError("你已經申請過此缺員班次。 / You already applied for this vacancy.")
    if application is None:
        application = VacancyApplication(requirement_id=requirement.id, staff_id=profile.id)
        db.session.add(application)
    application.status = VacancyApplicationStatus.PENDING
    application.note = note.strip()[:500] or None
    application.reviewed_by = None
    application.reviewed_at = None
    application.review_note = None
    db.session.flush()
    notify_admins(
        key=f"VACANCY_APPLICATION:{application.id}",
        category="VACANCY_APPLICATION",
        severity="INFO",
        title_zh=f"{profile.name}申請承接缺員班次",
        title_en=f"Vacancy application from {profile.name}",
        message_zh=f"日期：{requirement.shift_date}，請審核是否加入正式排班。",
        message_en=f"Date: {requirement.shift_date}. Review the application before scheduling.",
        target_url=url_for("admin.workforce_page") + "#applications",
    )
    complete_notification(f"VACANCY:{requirement.id}:USER:{profile.user_id}")
    add_audit(actor_user_id, "VACANCY_APPLIED", "VacancyApplication", application.id, f"申請缺員需求 #{requirement.id}")
    db.session.commit()
    return application


def cancel_application(
    application: VacancyApplication,
    *,
    profile: StaffProfile,
    actor_user_id: int,
) -> None:
    if application.staff_id != profile.id or application.status != VacancyApplicationStatus.PENDING:
        raise WorkforceError("只有自己的待審申請可以取消。 / Only your pending application can be cancelled.")
    application.status = VacancyApplicationStatus.CANCELLED
    complete_notification(f"VACANCY_APPLICATION:{application.id}")
    if application.requirement.status == RequirementStatus.OPEN:
        notify_user(
            profile.user_id,
            key=f"VACANCY:{application.requirement_id}:USER:{profile.user_id}",
            category="VACANCY_AVAILABLE",
            severity="INFO",
            title_zh=f"{application.requirement.shift_date} 有開放缺員班次",
            title_en="An eligible vacancy is available",
            message_zh="你已取消原申請，如仍有名額可重新申請。",
            message_en="You cancelled your application and may reapply while the vacancy remains open.",
            target_url=url_for("student.vacancies_page"),
        )
    add_audit(actor_user_id, "VACANCY_APPLICATION_CANCELLED", "VacancyApplication", application.id, f"取消缺員申請 #{application.id}")
    db.session.commit()


def review_application(
    application: VacancyApplication,
    *,
    decision: str,
    review_note: str,
    actor_user_id: int,
) -> None:
    if application.status != VacancyApplicationStatus.PENDING:
        raise WorkforceError("此申請已處理。 / This application has already been reviewed.")
    requirement = application.requirement
    ensure_month_open(requirement.shift_date)
    if decision == "APPROVE":
        if requirement.status != RequirementStatus.OPEN or vacancies(requirement) <= 0:
            raise WorkforceError("缺員已補足或需求已關閉。 / The requirement is already filled or closed.")
        create_shift(
            shift_date=requirement.shift_date,
            shift_type=requirement.shift_type,
            staff=application.staff,
            actor_id=actor_user_id,
            allow_location_overlap=True,
            publication_status=ShiftPublicationStatus.PUBLISHED,
            commit=False,
        )
        application.status = VacancyApplicationStatus.APPROVED
        action = "VACANCY_APPLICATION_APPROVED"
    elif decision == "REJECT":
        application.status = VacancyApplicationStatus.REJECTED
        action = "VACANCY_APPLICATION_REJECTED"
    else:
        raise WorkforceError("審核決定格式錯誤。 / Invalid review decision.")
    application.reviewed_by = actor_user_id
    application.reviewed_at = utc_now()
    application.review_note = review_note.strip()[:500] or None
    complete_notification(f"VACANCY_APPLICATION:{application.id}")
    if decision == "APPROVE" and vacancies(requirement) <= 0:
        requirement.status = RequirementStatus.CLOSED
        complete_requirement_notifications(requirement)
        for other in requirement.applications:
            if other.status == VacancyApplicationStatus.PENDING:
                other.status = VacancyApplicationStatus.REJECTED
                other.reviewed_by = actor_user_id
                other.reviewed_at = utc_now()
                other.review_note = "需求人數已補足。 / Requirement filled."
                complete_notification(f"VACANCY_APPLICATION:{other.id}")
    add_audit(actor_user_id, action, "VacancyApplication", application.id, f"審核缺員申請 #{application.id}")
    db.session.commit()
