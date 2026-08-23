from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from ..extensions import db
from ..models import AttendanceReconciliation, MinimumWageRate, PayrollSetting, Shift, ShiftType, StaffProfile


ZERO = Decimal("0")
ONE_DOLLAR = Decimal("1")


def money(value: Decimal) -> Decimal:
    return value.quantize(ONE_DOLLAR, rounding=ROUND_HALF_UP)


def get_payroll_setting(on_date: date) -> PayrollSetting | None:
    return db.session.scalar(
        db.select(PayrollSetting)
        .where(PayrollSetting.effective_date <= on_date)
        .order_by(PayrollSetting.effective_date.desc())
        .limit(1)
    )


def minimum_wage_on(on_date: date) -> Decimal:
    rate = db.session.scalar(
        db.select(MinimumWageRate)
        .where(MinimumWageRate.effective_date <= on_date)
        .order_by(MinimumWageRate.effective_date.desc())
        .limit(1)
    )
    return rate.hourly_wage if rate else ZERO


def payable_hours_expression():
    return db.func.coalesce(AttendanceReconciliation.payable_hours, ShiftType.default_hours)


def payable_hours_for_shift(shift: Shift) -> Decimal:
    return shift.reconciliation.payable_hours if shift.reconciliation else shift.shift_type.default_hours


def calculate_staff_cost(
    *, profile: StaffProfile, hours: Decimal, setting: PayrollSetting, on_date: date
) -> dict:
    hourly_wage = max(profile.hourly_wage or setting.default_hourly_wage, minimum_wage_on(on_date))
    gross_wage = money(hours * hourly_wage)

    labor_salary = profile.labor_insured_salary or ZERO
    health_salary = profile.health_insured_salary or ZERO
    pension_salary = profile.pension_salary or ZERO

    labor_insurance = ZERO
    employment_insurance = ZERO
    occupational_accident = ZERO
    health_insurance = ZERO
    labor_pension = ZERO

    if profile.labor_insurance_enabled and labor_salary > 0:
        labor_insurance = money(
            labor_salary * setting.labor_insurance_rate * setting.employer_labor_share
        )
        occupational_accident = money(
            labor_salary * setting.occupational_accident_rate
        )
        if profile.employment_insurance_enabled:
            employment_insurance = money(
                labor_salary
                * setting.employment_insurance_rate
                * setting.employer_labor_share
            )

    if profile.health_insurance_enabled and health_salary > 0:
        health_insurance = money(
            health_salary
            * setting.health_insurance_rate
            * setting.employer_health_share
            * (Decimal("1") + setting.average_dependents)
        )

    if profile.labor_pension_enabled and pension_salary > 0:
        labor_pension = money(pension_salary * setting.employer_pension_rate)

    employer_benefits = (
        labor_insurance
        + employment_insurance
        + occupational_accident
        + health_insurance
        + labor_pension
    )
    return {
        "staff_id": profile.id,
        "name": profile.name,
        "student_number": profile.student_number,
        "hours": float(hours),
        "hourly_wage": float(hourly_wage),
        "hourly_wage_override": (
            float(profile.hourly_wage) if profile.hourly_wage is not None else None
        ),
        "gross_wage": int(gross_wage),
        "labor_insured_salary": float(labor_salary),
        "health_insured_salary": float(health_salary),
        "pension_salary": float(pension_salary),
        "labor_insurance": int(labor_insurance),
        "employment_insurance": int(employment_insurance),
        "occupational_accident": int(occupational_accident),
        "health_insurance": int(health_insurance),
        "labor_pension": int(labor_pension),
        "employer_benefits": int(employer_benefits),
        "employer_total": int(gross_wage + employer_benefits),
        "insurance_configured": all(
            [
                not profile.labor_insurance_enabled or labor_salary > 0,
                not profile.health_insurance_enabled or health_salary > 0,
                not profile.labor_pension_enabled or pension_salary > 0,
            ]
        ),
        "flags": {
            "labor": profile.labor_insurance_enabled,
            "employment": profile.employment_insurance_enabled,
            "health": profile.health_insurance_enabled,
            "pension": profile.labor_pension_enabled,
        },
    }
