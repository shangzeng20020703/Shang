"""Published personal calendars and an append-only, half-day rest ledger."""
from datetime import date, datetime, timezone
from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def now():
    return datetime.now(timezone.utc)


class RotationDay(Base):
    __tablename__ = "field_rotation_days"
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_rotation_person_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date)
    cycle_start: Mapped[date] = mapped_column(Date)
    original_kind: Mapped[str] = mapped_column(String(12))
    kind: Mapped[str] = mapped_column(String(12))
    rule_snapshot: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    reason: Mapped[str] = mapped_column(String(500), default="周期排班")
    updated_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class RotationClaim(Base):
    __tablename__ = "field_rotation_claims"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # schedule / credit / leave
    days: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    units: Mapped[int] = mapped_column(Integer, default=0)  # one unit = half a day
    reason: Mapped[str] = mapped_column(String(500))
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    review_note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RotationAccount(Base):
    __tablename__ = "field_rotation_accounts"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    units: Mapped[int] = mapped_column(Integer, default=0)


class RotationLedger(Base):
    __tablename__ = "field_rotation_ledger"
    __table_args__ = (UniqueConstraint("claim_id", "action", name="uq_rotation_ledger_event"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("field_rotation_claims.id"))
    action: Mapped[str] = mapped_column(String(16))
    delta_units: Mapped[int] = mapped_column(Integer)
    balance_units: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
