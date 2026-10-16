"""An in-flight old-rule shift can finish after a project switches to rotation."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register isolated SQLite tables
from app.core.database import Base
from app.models.attendance import AttendanceRecord, AttendanceRule, WorkHourType
from app.models.employee import Employee
from app.models.field_service import (FieldAssignment, FieldAttendanceSegment,
                                      FieldCustomer, FieldMembership, FieldProject, FieldSite)
from app.models.organization import Location
from app.schemas.attendance import ClockInRequest, ClockOutRequest
from app.services import field_membership
from app.services.attendance import AttendanceRecordService


@pytest.mark.asyncio
async def test_old_shift_can_clock_out_after_rotation_rule_change_without_opening_new_shift(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        day = date(2026, 10, 16)
        now = datetime(2026, 10, 16, 10, tzinfo=timezone(timedelta(hours=8)))
        started = now - timedelta(hours=2)
        monkeypatch.setattr(field_membership, "clock_now", lambda: now.astimezone(timezone.utc))

        async with sessions() as db:
            db.add(Employee(id=1, employee_no="E1", name="组员", phone="19900000501",
                            status="在职", is_field_staff=True))
            db.add(FieldCustomer(id=1, name="虚构客户"))
            db.add(Location(id=1, name="虚构现场"))
            old_rule = AttendanceRule(id=1, name="旧自由上下班", work_hour_type=WorkHourType.flexible,
                                      effective_date=date(2020, 1, 1),
                                      extra_config={"rule_type": "free", "free_work_hours_mode": "limit:8"})
            db.add(old_rule)
            db.add(AttendanceRule(id=2, name="新现场轮休", work_hour_type=WorkHourType.flexible,
                                  effective_date=day,
                                  extra_config={"rule_type": "rotation", "scope_mode": "project",
                                                "project_id": 1, "free_work_hours_mode": "limit:8"}))
            db.add(FieldProject(id=1, code="P1", name="项目一", customer_id=1, manager_id=1))
            await db.flush()
            # The project was switched to the rotation rule after its old-rule
            # clock-in, before any RotationDay was published.
            db.add(FieldSite(id=1, project_id=1, name="虚构现场", location_id=1, rule_id=2))
            db.add(FieldAssignment(id=1, site_id=1, employee_id=1, start_date=day,
                                   end_date=date(9999, 12, 30), created_by=1, status="active"))
            await db.flush()
            db.add(FieldMembership(id=1, assignment_id=1, employee_id=1,
                                   started_at=(started - timedelta(days=1)).astimezone(timezone.utc),
                                   active_employee_id=1))
            record = AttendanceRecord(employee_id=1, date=day, clock_in_time=started.astimezone(timezone.utc))
            db.add(record)
            await db.flush()
            db.add(FieldAttendanceSegment(membership_id=1, employee_id=1, work_date=day,
                                          attendance_record_id=record.id, project_id=1,
                                          project_name="项目一", rule_snapshot=field_membership.snapshot(old_rule),
                                          record_data=field_membership.snapshot(record)))
            await db.flush()

            context = await field_membership.punch_context(db, 1)
            assert context is not None and context["legacy_close_only"]
            assert context["runtime"]["rule"].id == old_rule.id

            with pytest.raises(HTTPException) as blocked:
                await AttendanceRecordService.clock_in(db, 1,
                    ClockInRequest(project_context=context["revision"]))
            assert blocked.value.status_code == 409

            result, _ = await AttendanceRecordService.clock_out(db, 1,
                ClockOutRequest(project_context=context["revision"]))
            assert result.clock_out_time is not None
            assert result.clock_in_time is not None
            assert result.clock_out_time > result.clock_in_time
            # Once the old shift is closed, the unpublished rotation rule still
            # blocks all further punching on that day.
            assert await field_membership.punch_context(db, 1) is None
            with pytest.raises(HTTPException) as blocked_again:
                await AttendanceRecordService.clock_out(db, 1, ClockOutRequest())
            assert blocked_again.value.status_code == 409
    finally:
        await engine.dispose()
