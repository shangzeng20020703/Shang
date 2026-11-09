"""Confirmed personal rotation plans, including dates awaiting a project.

These plans are scheduling intentions. Only ``RotationDay`` with a frozen rule
snapshot can authorize attendance or participate in rest accounting.
"""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


class RotationPersonalPlan(Base):
    __tablename__ = "field_rotation_person_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    context_project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    cycle_start: Mapped[date] = mapped_column(Date)
    template: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(String(500), default="周期排班")
    updated_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RotationPersonalPlanDay(Base):
    __tablename__ = "field_rotation_person_plan_days"
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_rotation_person_plan_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("field_rotation_person_plans.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("field_projects.id"), nullable=True, index=True)
    work_date: Mapped[date] = mapped_column(Date)
    original_kind: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(12))
    rest_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    shift_label: Mapped[str | None] = mapped_column(String(12), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
