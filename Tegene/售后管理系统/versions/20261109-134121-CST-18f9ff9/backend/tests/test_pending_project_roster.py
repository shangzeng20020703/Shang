"""Project roster can precede rules without granting attendance until published."""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register isolated SQLite tables
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.attendance import AttendanceRule, WorkHourType
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import (
    FieldAssignment, FieldCustomer, FieldMembership, FieldProject,
    FieldProjectLeaderHistory, FieldProjectPendingMember, FieldSite,
    FieldTegeneProject,
)
from app.models.audit_log import AuditLog
from app.models.organization import Location
from app.models.rotation import RotationDay
from app.schemas.attendance import ClockInRequest, ClockValidateRequest
from app.schemas.field_service import ProjectConfigurationInput, SiteInput
from app.services import field_pending, field_service, tegene_projects
from app.services.attendance import AttendanceRecordService
from app.services.field_membership import punch_context


@pytest.mark.asyncio
async def test_confirm_pending_roster_http_replaces_atomically_and_activates_after_rule():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add_all([
                Employee(id=1, employee_no="A", name="管理员", phone="19900000601",
                         status="在职", is_superuser=True),
                Employee(id=2, employee_no="M", name="售后经理", phone="19900000602",
                         status="在职"),
                *(Employee(id=i, employee_no=f"L{i}", name=f"组长{i}",
                           phone=f"1990000060{i}", position="项目组长候选",
                           status="在职", is_field_staff=True) for i in (3, 7)),
                *(Employee(id=i, employee_no=f"P{i}", name=f"组员{i}",
                           phone=f"1990000060{i}", status="在职", is_field_staff=True)
                  for i in (4, 5, 6)),
                Employee(id=8, employee_no="X", name="普通账号", phone="19900000608",
                         status="在职"),
                Employee(id=9, employee_no="R", name="区域组长", phone="19900000609",
                         status="在职", is_field_staff=True),
                FieldCustomer(id=1, name="测试客户"),
                FieldProject(id=1, code="ROSTER", name="待配置项目", customer_id=1),
                FieldProject(id=2, code="OTHER", name="已有项目", customer_id=1),
            ])
            await db.flush()
            db.add_all([
                EmployeeRole(employee_id=2, role_name="aftersales_manager", granted_by=1),
                EmployeeRole(employee_id=9, role_name="regional_lead", granted_by=1),
                AttendanceRule(id=1, name="旧规则", work_hour_type=WorkHourType.flexible,
                               effective_date=date(2020, 1, 1)),
                Location(id=1, name="已有现场"),
            ])
            await db.flush()
            db.add(FieldSite(id=2, project_id=2, name="已有现场", location_id=1, rule_id=1))
            await db.flush()
            db.add(FieldAssignment(id=2, site_id=2, employee_id=5,
                start_date=date(2020, 1, 1), end_date=date(9999, 12, 30),
                created_by=1, status="active"))
            db.add(FieldMembership(assignment_id=2, employee_id=5,
                started_at=datetime.now(timezone.utc) - timedelta(days=1), active_employee_id=5))
            await db.commit()

        current_user = {"id": 2}

        async def override_db():
            async with sessions() as db:
                try:
                    yield db
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

        async def override_user():
            async with sessions() as db:
                return await db.get(Employee, current_user["id"])

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        url = "/api/v1/field/projects/1/pending-roster"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            initial = await client.put(url, json={"manager_id": 3, "member_ids": [4, 5, 6]})
            assert initial.status_code == 200, initial.text
            assert initial.json()["id"] == 1
            assert initial.json()["pending_member_count"] == 4
            assert {row["employee_id"] for row in initial.json()["pending_members"]} == {3, 4, 5, 6}
            refreshed = (await client.get("/api/v1/field/directory")).json()
            project = next(row for row in refreshed["projects"] if row["id"] == 1)
            assert (project["manager_id"], project["pending_member_count"], project["member_count"]) == (3, 4, 0)

            # A previously appointed leader remains selectable if a legacy
            # personnel import omitted the field-staff classification.
            async with sessions() as db:
                incumbent = await db.get(Employee, 3)
                incumbent.is_field_staff = False
                incumbent.position = ""
                await db.commit()
            repeated = await client.put(url, json={"manager_id": 3, "member_ids": [4, 5, 6]})
            assert repeated.status_code == 200, repeated.text
            async with sessions() as db:
                assert len((await db.scalars(select(FieldProjectPendingMember))).all()) == 4
                assert len((await db.scalars(select(FieldProjectLeaderHistory))).all()) == 1
                assert len((await db.scalars(select(AuditLog).where(
                    AuditLog.action == "confirm_pending_roster"))).all()) == 1

            invalid = await client.put(url, json={"manager_id": 7, "member_ids": [4, 9]})
            assert invalid.status_code == 422, invalid.text
            duplicate = await client.put(url, json={"manager_id": 7, "member_ids": [4, 4]})
            assert duplicate.status_code == 422, duplicate.text
            overlap = await client.put(url, json={"manager_id": 7, "member_ids": [4, 7]})
            assert overlap.status_code == 422, overlap.text
            async with sessions() as db:
                assert (await db.get(FieldProject, 1)).manager_id == 3
                assert {row.employee_id for row in (await db.scalars(select(
                    FieldProjectPendingMember))).all()} == {3, 4, 5, 6}

            current_user["id"] = 8
            denied = await client.put(url, json={"manager_id": 7, "member_ids": [4, 6]})
            assert denied.status_code == 403, denied.text
            current_user["id"] = 9
            unassigned_regional = await client.put(url, json={"manager_id": 7, "member_ids": [4, 6]})
            assert unassigned_regional.status_code == 403, unassigned_regional.text
            current_user["id"] = 3
            project_leader = await client.put(url, json={"manager_id": 7, "member_ids": [4, 6]})
            assert project_leader.status_code == 403, project_leader.text
            current_user["id"] = 2
            conflict = await client.post("/api/v1/attendance/projects/1/rule", json={
                "name": "轮休规则", "work_hour_type": "flexible", "work_hours_per_day": 12,
                "effective_date": str(field_service.today()),
                "extra_config": {"rule_type": "rotation", "scope_mode": "project",
                                 "project_id": 1, "free_work_hours_mode": "limit:12"},
            })
            assert conflict.status_code == 409, conflict.text
            conflict_detail = conflict.json()["detail"]["conflicts"]
            assert conflict_detail[0]["employee_name"] == "组员5"
            assert conflict_detail[0]["project_name"] == "已有项目"
            assert "已在其他项目" in conflict_detail[0]["reason"]
            async with sessions() as db:
                assert not await db.scalar(select(FieldSite.id).where(FieldSite.project_id == 1))
                assert not await db.scalar(select(AttendanceRule.id).where(AttendanceRule.name == "轮休规则"))
                assert not await db.scalar(select(FieldAssignment.id).where(FieldAssignment.site_id != 2))
                assert {row.employee_id for row in (await db.scalars(select(
                    FieldProjectPendingMember))).all()} == {3, 4, 5, 6}

            replaced = await client.put(url, json={"manager_id": 7, "member_ids": [4, 6]})
            assert replaced.status_code == 200, replaced.text
            assert {row["employee_id"] for row in replaced.json()["pending_members"]} == {4, 6, 7}
            assert next(row for row in replaced.json()["pending_members"]
                        if row["employee_id"] == 7)["is_leader_auto"]

            created = await client.post("/api/v1/attendance/projects/1/rule", json={
                "name": "轮休规则", "work_hour_type": "flexible", "work_hours_per_day": 12,
                "effective_date": str(field_service.today()),
                "extra_config": {"rule_type": "rotation", "scope_mode": "project",
                                 "project_id": 1, "free_work_hours_mode": "limit:12"},
            })
            assert created.status_code == 201, created.text
            after_rule = (await client.get("/api/v1/field/directory")).json()
            assert {row["employee_id"] for row in after_rule["assignments"]
                    if row["project_id"] == 1 and row["is_current"]} == {4, 6, 7}
            blocked = await client.put(url, json={"manager_id": 3, "member_ids": [4, 5]})
            assert blocked.status_code == 409, blocked.text
            async with sessions() as db:
                assert (await db.get(FieldProject, 1)).manager_id == 7
                assert len((await db.scalars(select(FieldAssignment))).all()) == 4
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_confirm_tegene_pending_roster_links_and_rolls_back_on_invalid_member(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add_all([
                Employee(id=1, employee_no="A", name="管理员", phone="19900000701",
                         status="在职", is_superuser=True),
                Employee(id=2, employee_no="L", name="组长", phone="19900000702",
                         position="组长候选", status="在职", is_field_staff=True),
                Employee(id=3, employee_no="P", name="组员", phone="19900000703",
                         status="在职", is_field_staff=True),
            ])
            await db.commit()

        first_id, second_id = uuid4(), uuid4()

        async def fake_projects():
            return "https://example.invalid", [
                {"id": str(first_id), "projectNo": "TG-A", "name": "Tegene A", "customer": "测试客户"},
                {"id": str(second_id), "projectNo": "TG-B", "name": "Tegene B", "customer": "测试客户"},
            ]

        monkeypatch.setattr(tegene_projects, "fetch_projects", fake_projects)

        async def override_db():
            async with sessions() as db:
                try:
                    yield db
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

        async def override_user():
            async with sessions() as db:
                return await db.get(Employee, 1)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            invalid = await client.post(f"/api/v1/field/tegene/projects/{second_id}/pending-roster",
                                        json={"manager_id": 2, "member_ids": [999]})
            assert invalid.status_code == 422, invalid.text
            async with sessions() as db:
                assert not (await db.scalars(select(FieldTegeneProject))).all()
                assert not (await db.scalars(select(FieldProject))).all()

            saved = await client.post(f"/api/v1/field/tegene/projects/{first_id}/pending-roster",
                                      json={"manager_id": 2, "member_ids": [3]})
            assert saved.status_code == 200, saved.text
            assert saved.json()["pending_member_count"] == 2
            async with sessions() as db:
                binding = (await db.scalars(select(FieldTegeneProject))).one()
                assert binding.project_id == saved.json()["id"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_saved_four_person_roster_survives_reload_and_activates_for_rotation():
    """Follow the project UI order: leader, members, refresh, rule, scheduling."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add_all([
                Employee(id=1, employee_no="MGR", name="售后经理", phone="19900000501", status="在职"),
                Employee(id=2, employee_no="LEADER", name="测试组长", phone="19900000502",
                         status="在职", is_field_staff=True),
                *(Employee(id=employee_id, employee_no=f"MEM-{employee_id}",
                           name=f"测试组员{employee_id - 2}", phone=f"1990000050{employee_id}",
                           status="在职", is_field_staff=True) for employee_id in (3, 4, 5)),
                FieldCustomer(id=1, name="虚构客户"),
                FieldProject(id=1, code="DEMO-04", name="模拟项目04", customer_id=1),
            ])
            await db.flush()
            db.add(EmployeeRole(employee_id=1, role_name="aftersales_manager", granted_by=1))
            await db.commit()

        async def override_db():
            async with sessions() as db:
                try:
                    yield db
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

        async def override_user():
            async with sessions() as db:
                return await db.get(Employee, 1)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            configured = await client.put("/api/v1/field/projects/1/configuration",
                                          json={"manager_id": 2})
            assert configured.status_code == 200, configured.text
            saved = await client.post("/api/v1/field/projects/1/members",
                                      json={"employee_ids": [3, 4, 5]})
            assert saved.status_code == 200, saved.text
            assert {row["employee_id"] for row in saved.json()["pending_members"]} == {2, 3, 4, 5}
            assert saved.json()["members"] == []

            # Every request uses a fresh DB session, like refreshing the page.
            before_rule = (await client.get("/api/v1/field/directory")).json()
            project = next(row for row in before_rule["projects"] if row["id"] == 1)
            assert project["pending_member_count"] == 4
            assert project["member_count"] == 0
            assert project["rule_id"] is None
            assert {row["employee_name"] for row in before_rule["pending_members"]} == {
                "测试组长", "测试组员1", "测试组员2", "测试组员3"}

            created = await client.post("/api/v1/attendance/projects/1/rule", json={
                "name": "现场轮休", "work_hour_type": "flexible", "work_hours_per_day": 12,
                "effective_date": str(field_service.today()),
                "extra_config": {"rule_type": "rotation", "scope_mode": "project",
                                 "project_id": 1, "free_work_hours_mode": "limit:12"},
            })
            assert created.status_code == 201, created.text
            after_rule = (await client.get("/api/v1/field/directory")).json()
            project = next(row for row in after_rule["projects"] if row["id"] == 1)
            assert project["pending_member_count"] == 0
            assert project["member_count"] == 4
            assert project["rule_id"] == created.json()["id"]
            assert after_rule["pending_members"] == []
            assert {row["employee_id"] for row in after_rule["assignments"]
                    if row["project_id"] == 1 and row["is_current"]} == {2, 3, 4, 5}

            tomorrow = field_service.today() + timedelta(days=1)
            preview = await client.post("/api/v1/rotation/projects/1/preview", json={
                "employee_ids": [2, 3, 4, 5], "start_date": str(tomorrow),
                "end_date": str(tomorrow), "cycle_start": str(tomorrow),
                "member_days_only": True, "reason": "周期排班",
            })
            assert preview.status_code == 200, preview.text
            assert {row["employee_id"] for row in preview.json()["rows"]} == {2, 3, 4, 5}
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


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
