"""Protect locked attendance periods across API, approval callbacks and event consumers."""

from sqlalchemy import event, select, inspect
from sqlalchemy.orm import Session
from app.models.field_service import FieldAttendanceSegment
from app.models.attendance import (
    AttendanceRecord,
    AttendancePunchTimeRecord,
    AttendanceEffect,
    ShiftSchedule,
    AttendanceMonthSummary,
    MonthlySummaryStatus,
)


@event.listens_for(Session, "before_flush")
def prevent_locked_period_writes(session, flush_context, instances):
    keys = set()
    for item in set(session.new) | set(session.dirty) | set(session.deleted):
        if not isinstance(
            item,
            (
                FieldAttendanceSegment,
                AttendanceRecord,
                AttendancePunchTimeRecord,
                AttendanceEffect,
                ShiftSchedule,
            ),
        ):
            continue
        if item in session.dirty and not session.is_modified(
            item, include_collections=False
        ):
            continue
        day = getattr(item, "date", None) or getattr(item, "work_date", None)
        person = getattr(item, "employee_id", None)
        if day and person:
            keys.add((person, day.year, day.month))
        # Moving a record cannot escape a lock on its original date/person.
        for name in ("date", "work_date"):
            if name in inspect(item).attrs:
                for old in inspect(item).attrs[name].history.deleted:
                    if old and person:
                        keys.add((person, old.year, old.month))
    for employee_id, year, month in keys:
        locked = (
            session.connection()
            .execute(
                select(AttendanceMonthSummary.id).where(
                    AttendanceMonthSummary.employee_id == employee_id,
                    AttendanceMonthSummary.year == year,
                    AttendanceMonthSummary.month == month,
                    AttendanceMonthSummary.status == MonthlySummaryStatus.locked,
                )
            )
            .first()
        )
        if locked:
            raise ValueError(f"{year}-{month:02d} 考勤已锁定，请先解锁后再修改")
