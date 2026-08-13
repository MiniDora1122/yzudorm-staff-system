from __future__ import annotations

from datetime import date

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from . import bp
from ..decorators import role_required
from ..extensions import db
from ..models import (
    RequirementAudienceGroup,
    RequirementAudienceStaff,
    RequirementStatus,
    Role,
    ShiftType,
    StaffGroup,
    StaffGroupMember,
    StaffingRequirement,
    StaffProfile,
    User,
    VacancyApplication,
    VacancyApplicationStatus,
)
from ..services.audit import add_audit
from ..services.periods import PeriodError
from ..services.workforce import (
    WorkforceError,
    assigned_count,
    complete_requirement_notifications,
    publish_requirement_notifications,
    review_application,
    vacancies,
)
from ..time_utils import local_today


def _selected_ids(field: str) -> list[int]:
    values = []
    for value in request.form.getlist(field):
        if value.isdigit():
            values.append(int(value))
    return sorted(set(values))


@bp.get("/workforce")
@role_required(Role.ADMIN)
def workforce_page():
    profiles = db.session.scalars(
        db.select(StaffProfile)
        .join(StaffProfile.user)
        .where(User.is_active.is_(True))
        .order_by(StaffProfile.name)
    ).all()
    groups = db.session.scalars(
        db.select(StaffGroup)
        .options(selectinload(StaffGroup.memberships).joinedload(StaffGroupMember.staff))
        .where(StaffGroup.is_active.is_(True))
        .order_by(StaffGroup.name)
    ).all()
    requirements = db.session.scalars(
        db.select(StaffingRequirement)
        .options(
            joinedload(StaffingRequirement.shift_type).joinedload(ShiftType.work_location),
            selectinload(StaffingRequirement.audience_groups).joinedload(RequirementAudienceGroup.group),
            selectinload(StaffingRequirement.audience_staff).joinedload(RequirementAudienceStaff.staff),
            selectinload(StaffingRequirement.applications).joinedload(VacancyApplication.staff),
        )
        .order_by(StaffingRequirement.shift_date.desc(), StaffingRequirement.id.desc())
        .limit(150)
    ).all()
    applications = db.session.scalars(
        db.select(VacancyApplication)
        .options(
            joinedload(VacancyApplication.staff),
            joinedload(VacancyApplication.requirement)
            .joinedload(StaffingRequirement.shift_type)
            .joinedload(ShiftType.work_location),
        )
        .order_by(
            db.case((VacancyApplication.status == VacancyApplicationStatus.PENDING, 0), else_=1),
            VacancyApplication.created_at.desc(),
        )
        .limit(150)
    ).all()
    return render_template(
        "admin/workforce.html",
        profiles=profiles,
        groups=groups,
        requirements=requirements,
        applications=applications,
        shift_types=db.session.scalars(
            db.select(ShiftType)
            .options(joinedload(ShiftType.work_location))
            .where(ShiftType.is_active.is_(True))
            .order_by(ShiftType.display_order)
        ).all(),
        assigned_count=assigned_count,
        vacancies=vacancies,
        today=local_today(),
    )


@bp.post("/workforce/groups")
@role_required(Role.ADMIN)
def create_staff_group():
    name = request.form.get("name", "").strip()
    name_en = request.form.get("name_en", "").strip()
    if not name or not name_en:
        flash("群組中英文名稱皆為必填。 / Both group names are required.", "danger")
        return redirect(url_for("admin.workforce_page"))
    group = StaffGroup(name=name[:100], name_en=name_en[:120], created_by=current_user.id)
    group.memberships = [StaffGroupMember(staff_id=staff_id) for staff_id in _selected_ids("staff_ids")]
    db.session.add(group)
    try:
        db.session.flush()
        add_audit(current_user.id, "STAFF_GROUP_CREATED", "StaffGroup", group.id, f"建立學生群組 {group.name}")
        db.session.commit()
        flash("學生群組已建立。 / Student group created.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("群組名稱已存在。 / Group name already exists.", "danger")
    return redirect(url_for("admin.workforce_page"))


@bp.post("/workforce/groups/<int:group_id>")
@role_required(Role.ADMIN)
def update_staff_group(group_id: int):
    group = db.session.get(StaffGroup, group_id)
    if group is None or not group.is_active:
        flash("找不到學生群組。 / Group not found.", "danger")
        return redirect(url_for("admin.workforce_page"))
    name = request.form.get("name", "").strip()
    name_en = request.form.get("name_en", "").strip()
    if not name or not name_en:
        flash("群組中英文名稱皆為必填。", "danger")
        return redirect(url_for("admin.workforce_page"))
    group.name = name[:100]
    group.name_en = name_en[:120]
    group.memberships = [StaffGroupMember(staff_id=staff_id) for staff_id in _selected_ids("staff_ids")]
    try:
        add_audit(current_user.id, "STAFF_GROUP_UPDATED", "StaffGroup", group.id, f"更新學生群組 {group.name}")
        db.session.commit()
        flash("學生群組已更新。 / Student group updated.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("群組名稱已被使用。 / Group name already exists.", "danger")
    return redirect(url_for("admin.workforce_page"))


@bp.post("/workforce/groups/<int:group_id>/archive")
@role_required(Role.ADMIN)
def archive_staff_group(group_id: int):
    group = db.session.get(StaffGroup, group_id)
    if group is None or not group.is_active:
        flash("找不到學生群組。", "danger")
    else:
        group.is_active = False
        add_audit(current_user.id, "STAFF_GROUP_ARCHIVED", "StaffGroup", group.id, f"封存學生群組 {group.name}")
        db.session.commit()
        flash("學生群組已封存；既有缺員發布紀錄仍保留。 / Group archived.", "success")
    return redirect(url_for("admin.workforce_page"))


@bp.post("/workforce/requirements")
@role_required(Role.ADMIN)
def create_staffing_requirement():
    try:
        shift_date = date.fromisoformat(request.form.get("shift_date", ""))
        shift_type_id = int(request.form.get("shift_type_id", ""))
        required_count = int(request.form.get("required_count", ""))
    except (TypeError, ValueError) as exc:
        flash(str(exc) if str(exc) else "缺員需求資料格式錯誤。", "danger")
        return redirect(url_for("admin.workforce_page"))
    shift_type = db.session.get(ShiftType, shift_type_id)
    if shift_type is None or not shift_type.is_active or not 1 <= required_count <= 50:
        flash("班別或需求人數無效。 / Invalid shift type or required count.", "danger")
        return redirect(url_for("admin.workforce_page"))
    requirement = StaffingRequirement(
        shift_date=shift_date,
        shift_type_id=shift_type.id,
        required_count=required_count,
        note=request.form.get("note", "").strip()[:500] or None,
        created_by=current_user.id,
    )
    requirement.audience_groups = [
        RequirementAudienceGroup(group_id=group_id) for group_id in _selected_ids("group_ids")
    ]
    requirement.audience_staff = [
        RequirementAudienceStaff(staff_id=staff_id) for staff_id in _selected_ids("staff_ids")
    ]
    db.session.add(requirement)
    try:
        db.session.flush()
        publish_requirement_notifications(requirement)
        add_audit(
            current_user.id,
            "STAFFING_REQUIREMENT_PUBLISHED",
            "StaffingRequirement",
            requirement.id,
            f"發布 {shift_date} 缺員需求，人數 {required_count}",
        )
        db.session.commit()
        flash("缺員需求已發布給指定學生；未指定對象時會對全部有效學生開放。 / Vacancy published.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("同一天同一班別已有一筆人力需求，請先關閉原需求。 / A requirement already exists for this slot.", "danger")
    return redirect(url_for("admin.workforce_page"))


@bp.post("/workforce/requirements/<int:requirement_id>/cancel")
@role_required(Role.ADMIN)
def cancel_staffing_requirement(requirement_id: int):
    requirement = db.session.get(StaffingRequirement, requirement_id)
    if requirement is None or requirement.status == RequirementStatus.CANCELLED:
        flash("找不到缺員需求。", "danger")
    else:
        requirement.status = RequirementStatus.CANCELLED
        complete_requirement_notifications(requirement)
        add_audit(current_user.id, "STAFFING_REQUIREMENT_CANCELLED", "StaffingRequirement", requirement.id, f"取消缺員需求 #{requirement.id}")
        db.session.commit()
        flash("缺員需求已取消。 / Vacancy cancelled.", "success")
    return redirect(url_for("admin.workforce_page"))


@bp.post("/workforce/applications/<int:application_id>/review")
@role_required(Role.ADMIN)
def review_vacancy_application(application_id: int):
    application = db.session.get(VacancyApplication, application_id)
    if application is None:
        flash("找不到缺員申請。", "danger")
        return redirect(url_for("admin.workforce_page"))
    try:
        review_application(
            application,
            decision=request.form.get("decision", ""),
            review_note=request.form.get("review_note", ""),
            actor_user_id=current_user.id,
        )
        flash("缺員申請已完成審核；核准時已直接加入正式排班。 / Application reviewed.", "success")
    except (WorkforceError, PeriodError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("admin.workforce_page") + "#applications")
