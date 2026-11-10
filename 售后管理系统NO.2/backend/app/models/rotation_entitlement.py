"""Forward-only, employee-wide onsite rotation entitlement facts.

The old claim ledger remains untouched.  These tables hold the new rolling
six-workday policy after an administrator selects its effective date.
"""
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now():
    return datetime.now(timezone.utc)


class RotationEntitlementPolicy(Base):
    __tablename__ = "field_rotation_entitlement_policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    effective_date: Mapped[date] = mapped_column(Date)
    updated_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RotationEntitlementAccount(Base):
    __tablename__ = "field_rotation_entitlement_accounts"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    work_units: Mapped[int] = mapped_column(Integer, default=0)  # half-workdays after policy start
    awarded_units: Mapped[int] = mapped_column(Integer, default=0)  # half-rest-days already awarded


class RotationWorkFact(Base):
    __tablename__ = "field_rotation_work_facts"
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_rotation_work_fact_person_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    units: Mapped[int] = mapped_column(Integer, default=0)  # accepted half-workdays, at most 2
    suggested_units: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    source: Mapped[str] = mapped_column(String(20), default="punch")
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(500), default="")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RotationAutoRest(Base):
    __tablename__ = "field_rotation_auto_rest"
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_rotation_auto_rest_person_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="pending_credit")
    units: Mapped[int] = mapped_column(Integer, default=0)  # half-rest-days consumed
    reason: Mapped[str] = mapped_column(String(500), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RotationEntitlementLedger(Base):
    __tablename__ = "field_rotation_entitlement_ledger"
    __table_args__ = (UniqueConstraint("source_key", name="uq_rotation_entitlement_event"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_key: Mapped[str] = mapped_column(String(100))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    work_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    action: Mapped[str] = mapped_column(String(24))
    delta_units: Mapped[int] = mapped_column(Integer)  # half-rest-days in account
    balance_units: Mapped[int] = mapped_column(Integer)
    work_units: Mapped[int] = mapped_column(Integer)  # total half-workdays after policy start
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
