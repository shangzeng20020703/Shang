"""NO.2 attendance facts. Times are stored as Beijing local wall time."""

from datetime import date, datetime
from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class MonthlyPolicy(Base):
    __tablename__ = "field_monthly_policies"
    effective: Mapped[date] = mapped_column(Date, primary_key=True)
    cutoff: Mapped[int]
    minutes: Mapped[int]
    photo: Mapped[bool]


class ProjectRadius(Base):
    __tablename__ = "field_monthly_radii"
    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    meters: Mapped[int]


class MonthlyPunch(Base):
    __tablename__ = "field_monthly_punches"
    __table_args__ = (
        UniqueConstraint("employee_id", "request_key"),
        UniqueConstraint("employee_id", "day", "sequence"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    at: Mapped[datetime] = mapped_column(DateTime)
    sequence: Mapped[int]
    request_key: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(4))
    latitude: Mapped[float]
    longitude: Mapped[float]
    matches: Mapped[list] = mapped_column(JSON)
    photo: Mapped[str] = mapped_column(String(500), default="")


class ProjectConfirmation(Base):
    __tablename__ = "field_monthly_confirmations"
    id: Mapped[int] = mapped_column(primary_key=True)
    punch_id: Mapped[int] = mapped_column(
        ForeignKey("field_monthly_punches.id"), unique=True
    )
    code: Mapped[str] = mapped_column(String(50))
    manager_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    at: Mapped[datetime] = mapped_column(DateTime)


class MonthlyRest(Base):
    __tablename__ = "field_monthly_rest"
    __table_args__ = (UniqueConstraint("employee_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(12), default="pending")
    created: Mapped[datetime] = mapped_column(DateTime)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    reviewed: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(String(500), default="")


class MonthlyAbsence(Base):
    __tablename__ = "field_monthly_absences"
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(default=True)


class MonthlyEligibility(Base):
    __tablename__ = "field_monthly_eligibility"
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    eligible: Mapped[bool]
