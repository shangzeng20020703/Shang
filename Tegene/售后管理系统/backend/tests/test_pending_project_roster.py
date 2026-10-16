"""Project roster can precede rules without granting attendance until published."""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register isolated SQLite tables
from app.core.database import Base
from app.models.attendance import AttendanceRule, WorkHourType
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import (
    FieldAssignment, FieldCustomer, FieldMembership, FieldProject,
    FieldProjectPendingMember, FieldSite,
)
from app.models.organization import Location
from app.models.rotation import RotationDay
from app.schemas.attendance import ClockInRequest, ClockValidateRequest
from app.schemas.field_service import ProjectConfigurationInput, SiteInput
from app.services import field_pending, field_service
from app.services.attendance import AttendanceRecordService
from app.services.field_membership import punch_context


@pytest.mark.asyncio
async def test_pre_rule_roster_leader_atomic_activation_and_rotation_punch_gate():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            admin = Employee(id=1, employee_no="A", name="管理员", phone="19900000301",
                             status="在职", is_superuser=True)
            manager = Employee(id=2, employee_no="M", name="售后经理", phone="19900000302",
                               status="在职")
            leader = Employee(id=3, employee_no="L", name="组长", phone="19900000303",
                              status="在职", is_field_staff=True)
            member = Employee(id=4, employee_no="P", name="组员", phone="19900000304",
                              status="在职", is_field_staff=True)
            conflict = Employee(id=5, employee_no="C", name="冲突组员", phone="19900000305",
                                status="在职", is_field_staff=True)
            alternate = Employee(id=6, employee_no="L2", name="候选组长", phone="19900000306",
                                 status="在职", is_field_staff=True)
            db.add_all([admin, manager, leader, member, conflict, alternate])
            await db.flush()
            db.add(EmployeeRole(employee_id=2, role_name="aftersales_manager", granted_by=1))
            db.add(FieldCustomer(id=1, name="虚构客户"))
            db.add_all([
                FieldProject(id=1, code="P1", name="待配置项目", customer_id=1),
                FieldProject(id=2, code="P2", name="已有成员项目", customer_id=1),
            ])
            old_rule = AttendanceRule(id=1, name="旧项目规则",
                work_hour_type=WorkHourType.flexible, effective_date=date(2020, 1, 1))
            db.add(old_rule)
            db.add(Location(id=1, name="旧现场"))
            await db.flush()
            db.add(FieldSite(id=2, project_id=2, name="旧现场", location_id=1, rule_id=1))
            await db.flush()
            now = datetime.now(timezone.utc)
            db.add(FieldAssignment(id=2, site_id=2, employee_id=5,
                start_date=date(2020, 1, 1), end_date=date(9999, 12, 30),
                created_by=1, status="active"))
            db.add(FieldMembership(assignment_id=2, employee_id=5,
                started_at=now - timedelta(days=1), active_employee_id=5))
            await db.flush()

            staged = await field_pending.stage_members(db, manager, 1, [4, 5])
            assert {item["employee_id"] for item in staged["pending_members"]} == {4, 5}
            assert not (await db.scalars(select(FieldAssignment).where(
                FieldAssignment.site_id != 2))).all()
            own_view = await field_service.directory(db, member)
            assert [p["id"] for p in own_view["projects"]] == [1]
            assert [row["employee_id"] for row in own_view["pending_members"]] == [4]
            assert await punch_context(db, 4) is None
            preflight = await AttendanceRecordService.validate_clock(db, 4, ClockValidateRequest())
            assert preflight["is_valid"] is False and preflight["status"] == "pending"
            with pytest.raises(HTTPException) as no_rule_punch:
                await AttendanceRecordService.clock_in(db, 4, ClockInRequest())
            assert no_rule_punch.value.status_code == 409

            await field_service.configure_project(db, manager, 1,
                ProjectConfigurationInput(manager_id=3))
            pending = (await db.scalars(select(FieldProjectPendingMember).where(
                FieldProjectPendingMember.project_id == 1))).all()
            assert {row.employee_id for row in pending} == {3, 4, 5}
            leader_row = next(row for row in pending if row.employee_id == 3)
            assert leader_row.is_leader_auto
            with pytest.raises(HTTPException) as cannot_remove:
                await field_pending.remove_pending(db, manager, 1, leader_row.id)
            assert cannot_remove.value.status_code == 409
            await field_service.configure_project(db, manager, 1,
                ProjectConfigurationInput(manager_id=6))
            assert not await db.scalar(select(FieldProjectPendingMember.id).where(
                FieldProjectPendingMember.project_id == 1,
                FieldProjectPendingMember.employee_id == 3))
            await field_service.configure_project(db, manager, 1,
                ProjectConfigurationInput(manager_id=3))
            assert not await db.scalar(select(FieldProjectPendingMember.id).where(
                FieldProjectPendingMember.project_id == 1,
                FieldProjectPendingMember.employee_id == 6))

            rotation_rule = AttendanceRule(id=3, name="现场轮休",
                work_hour_type=WorkHourType.flexible,
                effective_date=field_service.today(),
                extra_config={"rule_type": "rotation", "scope_mode": "project",
                              "project_id": 1, "free_work_hours_mode": "limit:12"})
            db.add(rotation_rule)
            await db.flush()
            await field_service.save_site(db, manager,
                SiteInput(project_id=1, name="待配置项目", rule_id=3))
            # Automatic activation found one conflict and activated nobody.
            assert not (await db.scalars(select(FieldAssignment).where(
                FieldAssignment.site_id != 2))).all()
            with pytest.raises(HTTPException) as blocked:
                await field_pending.activate_members(db, manager, 1)
            assert blocked.value.status_code == 409
            assert blocked.value.detail["conflicts"][0]["employee_name"] == "冲突组员"

            rejected = next(row for row in pending if row.employee_id == 5)
            await field_pending.remove_pending(db, manager, 1, rejected.id)
            activated = await field_pending.activate_members(db, manager, 1)
            assert {item["employee_id"] for item in activated["members"]} == {3, 4}
            assert activated["pending_members"] == []
            assert (await field_pending.activate_members(db, manager, 1))["members"] == []
            view = await field_service.directory(db, manager)
            assignment = next(a for a in view["assignments"] if a["employee_id"] == 4)
            assert assignment["can_punch"] is False
            assert await punch_context(db, 4) is None
            preflight = await AttendanceRecordService.validate_clock(db, 4, ClockValidateRequest())
            assert preflight["is_valid"] is False
            with pytest.raises(HTTPException) as unpublished_punch:
                await AttendanceRecordService.clock_in(db, 4, ClockInRequest())
            assert unpublished_punch.value.status_code == 409

            db.add(RotationDay(employee_id=4, project_id=1,
                work_date=field_service.today() - timedelta(days=1),
                cycle_start=field_service.today() - timedelta(days=1),
                original_kind="work", kind="work", rule_snapshot={}, updated_by=2))
            await db.flush()
            view = await field_service.directory(db, manager)
            assignment = next(a for a in view["assignments"] if a["employee_id"] == 4)
            assert assignment["can_punch"] is True
            context = await punch_context(db, 4)
            assert context is not None and context["runtime"]["rotation"]["kind"] == "pending"
            record, _ = await AttendanceRecordService.clock_in(db, 4,
                ClockInRequest(project_context=context["revision"]))
            assert record.display_status == "待确认"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_future_rule_effective_date_keeps_participation_inactive_today():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            manager = Employee(id=1, employee_no="ADM", name="管理员",
                phone="19900000401", status="在职", is_superuser=True)
            member = Employee(id=2, employee_no="MEM", name="组员",
                phone="19900000402", status="在职", is_field_staff=True)
            db.add_all([manager, member])
            db.add(FieldCustomer(id=1, name="未来生效客户"))
            db.add(FieldProject(id=1, code="FUTURE", name="未来生效项目", customer_id=1))
            await db.flush()
            await field_pending.stage_members(db, manager, 1, [2])
            tomorrow = field_service.today() + timedelta(days=1)
            db.add(AttendanceRule(id=1, name="明日规则",
                work_hour_type=WorkHourType.flexible, effective_date=tomorrow,
                extra_config={"rule_type": "rotation", "scope_mode": "project",
                              "project_id": 1, "free_work_hours_mode": "limit:8"}))
            await db.flush()
            await field_service.save_site(db, manager,
                SiteInput(project_id=1, name="未来生效项目", rule_id=1))
            view = await field_service.directory(db, member)
            assert view["pending_members"] == []
            assert len(view["assignments"]) == 1
            assert view["assignments"][0]["start_date"] == tomorrow
            assert view["assignments"][0]["is_current"] is False
            assert view["assignments"][0]["can_punch"] is False
            with pytest.raises(HTTPException) as blocked:
                await AttendanceRecordService.clock_in(db, 2, ClockInRequest())
            assert blocked.value.status_code == 409
    finally:
        await engine.dispose()
