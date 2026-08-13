from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.orm import joinedload, selectinload

from . import bp
from ..decorators import role_required
from ..extensions import db
from ..models import (
    RequirementStatus,
    Role,
    ShiftType,
    StaffingRequirement,
    VacancyApplication,
)
from ..services.periods import PeriodError
from ..services.workforce import (
    WorkforceError,
    cancel_application,
    eligible_staff_ids,
    submit_application,
    vacancies,
)
from ..time_utils import local_today


@bp.get("/vacancies")
@role_required(Role.STUDENT)
def vacancies_page():
    profile = current_user.staff_profile
    requirements = db.session.scalars(
        db.select(StaffingRequirement)
        .options(
            joinedload(StaffingRequirement.shift_type).joinedload(ShiftType.work_location),
            selectinload(StaffingRequirement.audience_groups),
            selectinload(StaffingRequirement.audience_staff),
        )
        .where(
            StaffingRequirement.status == RequirementStatus.OPEN,
            StaffingRequirement.shift_date >= local_today(),
        )
        .order_by(StaffingRequirement.shift_date, StaffingRequirement.id)
        .limit(100)
    ).all()
    eligible = [
        item for item in requirements if profile.id in eligible_staff_ids(item) and vacancies(item) > 0
    ]
    applications = db.session.scalars(
        db.select(VacancyApplication)
        .options(
            joinedload(VacancyApplication.requirement)
            .joinedload(StaffingRequirement.shift_type)
            .joinedload(ShiftType.work_location)
        )
        .where(VacancyApplication.staff_id == profile.id)
        .order_by(VacancyApplication.created_at.desc())
        .limit(100)
    ).all()
    application_by_requirement = {item.requirement_id: item for item in applications}
    return render_template(
        "student/vacancies.html",
        requirements=eligible,
        applications=applications,
        application_by_requirement=application_by_requirement,
        vacancies=vacancies,
    )


@bp.post("/vacancies/<int:requirement_id>/apply")
@role_required(Role.STUDENT)
def apply_for_vacancy(requirement_id: int):
    requirement = db.session.get(StaffingRequirement, requirement_id)
    if requirement is None:
        flash("找不到缺員班次。 / Vacancy not found.", "danger")
        return redirect(url_for("student.vacancies_page"))
    try:
        submit_application(
            requirement,
            current_user.staff_profile,
            note=request.form.get("note", ""),
            actor_user_id=current_user.id,
            today=local_today(),
        )
        flash("缺員承接申請已送出，等待管理員審核。 / Application submitted.", "success")
    except (WorkforceError, PeriodError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("student.vacancies_page"))


@bp.post("/vacancy-applications/<int:application_id>/cancel")
@role_required(Role.STUDENT)
def cancel_vacancy_application(application_id: int):
    application = db.session.get(VacancyApplication, application_id)
    if application is None:
        flash("找不到缺員申請。", "danger")
    else:
        try:
            cancel_application(
                application,
                profile=current_user.staff_profile,
                actor_user_id=current_user.id,
            )
            flash("缺員申請已取消。 / Application cancelled.", "success")
        except WorkforceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return redirect(url_for("student.vacancies_page"))
