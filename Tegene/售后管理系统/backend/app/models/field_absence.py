"""Project-at-date audit snapshot for field leave and business trips."""

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FieldAbsenceDay(Base):
    __tablename__ = "field_absence_days"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "work_date", name="uq_field_absence_source_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(24), index=True)  # leave_request / approval_instance
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("field_projects.id"), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("field_sites.id"), nullable=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("attendance_rules.id"), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
