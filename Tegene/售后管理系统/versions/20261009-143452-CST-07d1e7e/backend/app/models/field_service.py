"""Basic aftersales context; deliberately no continuous-presence or recheck models."""

from datetime import date, datetime, timezone
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class FieldRosterProfile(Base):
    __tablename__ = "field_roster_profiles"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class FieldCustomer(Base):
    __tablename__ = "field_customers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    contact: Mapped[str] = mapped_column(String(120), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FieldProject(Base):
    __tablename__ = "field_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    customer_id: Mapped[int] = mapped_column(ForeignKey("field_customers.id"))
    manager_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FieldTegeneProject(Base):
    __tablename__ = "field_tegene_projects"
    __table_args__ = (UniqueConstraint("source_url", "external_id", name="uq_field_tegene_project"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"), unique=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FieldSite(Base):
    __tablename__ = "field_sites"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"))
    name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str] = mapped_column(String(500), default="")
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), unique=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("attendance_rules.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class FieldAssignment(Base):
    __tablename__ = "field_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("field_sites.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FieldAssignmentDay(Base):
    __tablename__ = "field_assignment_days"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_field_person_day"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("field_assignments.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    work_date: Mapped[date] = mapped_column(Date, index=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift_schedules.id"), unique=True)


class FieldMembership(Base):
    """Effective participation interval; assignment rows retain the original history."""
    __tablename__ = "field_memberships"
    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("field_assignments.id"), unique=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULL for ended / legacy bounded intervals; guarantees one open membership.
    active_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), unique=True, nullable=True)


class FieldAttendanceSegment(Base):
    __tablename__ = "field_attendance_segments"
    __table_args__ = (UniqueConstraint("membership_id", "work_date", name="uq_field_segment_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    membership_id: Mapped[int] = mapped_column(ForeignKey("field_memberships.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    attendance_record_id: Mapped[int] = mapped_column(ForeignKey("attendance_records.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("field_projects.id"))
    project_name: Mapped[str] = mapped_column(String(150))
    rule_snapshot: Mapped[dict] = mapped_column(JSON)
    record_data: Mapped[dict] = mapped_column(JSON)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)


class FieldFile(Base):
    __tablename__ = "field_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(500), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FieldSchemaVersion(Base):
    __tablename__ = "field_schema_versions"
    version: Mapped[int] = mapped_column(primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class FieldTemplateDraft(Base):
    __tablename__ = "field_template_drafts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "template_id", name="uq_field_template_draft_owner"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    # Zero is the current user's unfinished new template; published templates use their ID.
    template_id: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class FieldWecomIdentity(Base):
    __tablename__ = "field_wecom_identities"
    __table_args__ = (UniqueConstraint("corp_id", "userid", name="uq_field_wecom_member"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), unique=True)
    corp_id: Mapped[str] = mapped_column(String(128))
    userid: Mapped[str] = mapped_column(String(128))


class FieldLoginState(Base):
    __tablename__ = "field_login_states"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    challenge: Mapped[str] = mapped_column(String(64))
    client: Mapped[str] = mapped_column(String(10))
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FieldPosition(Base):
    """Department-local position names; employee assignments use department_id + position."""
    __tablename__ = "field_positions"
    __table_args__ = (UniqueConstraint("department_id", "name", name="uq_field_department_position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    name: Mapped[str] = mapped_column(String(100))
