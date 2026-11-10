"""A complete personal week is confirmed before the first rotation rule."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register the isolated schema
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.attendance import AttendanceRule
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import FieldAssignment, FieldCustomer, FieldProject, FieldSite
from app.models.notification import Notification
from app.models.rotation import RotationDay, RotationDraftDay
from app.services import rotation, rotation_draft


def week_payload(start, people):
    return {"employee_ids": people, "start_date": str(start),
            "end_date": str(start + timedelta(days=6 - start.weekday())),
            "cycle_start": str(start), "member_days_only": True, "reason": "周期排班"}


def rule_payload(day, kind="rotation"):
    return {"name": f"现场规则-{kind}", "work_hour_type": "flexible",
            "work_hours_per_day": 12, "effective_date": str(day),
            "extra_config": {"rule_type": kind, "free_work_hours_mode": "limit:12"}}


def test_partial_old_week_cannot_be_masked_by_complete_later_week():
    start = rotation_draft.monday(rotation.today()) + timedelta(days=7)
    rows = [SimpleNamespace(employee_id=1, work_date=start + timedelta(days=i)) for i in range(7)]
    later = start + timedelta(days=7)
    rows += [SimpleNamespace(employee_id=eid, work_date=later + timedelta(days=i))
             for eid in (1, 2) for i in range(7)]
    status = rotation_draft.status_for_rows({1, 2}, rows, start)
    assert not status["pre_rule_schedule_ready"]
    assert status["pre_rule_schedule_week_start"] == start
    assert status["pre_rule_schedule_missing_member_ids"] == [2]


@pytest.mark.asyncio
async def test_pre_rule_full_week_gate_atomic_rule_binding_and_notifications():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add_all([
                Employee(id=1, employee_no="M", name="售后经理", phone="19900009101", status="在职"),
                Employee(id=2, employee_no="L", name="项目组长", phone="19900009102", status="在职",
                         is_field_staff=True, position="项目组长候选"),
                *(Employee(id=i, employee_no=f"P{i}", name=f"组员{i}",
                           phone=f"1990000910{i}", status="在职", is_field_staff=True)
                  for i in (3, 4, 5)),
                FieldCustomer(id=1, name="虚构客户"),
                FieldProject(id=1, code="P1", name="虚构项目", customer_id=1),
            ])
            await db.flush()
            db.add(EmployeeRole(employee_id=1, role_name="aftersales_manager", granted_by=1))
            await db.commit()

        active_user = {"id": 1}

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
                return await db.get(Employee, active_user["id"])

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            saved = await client.put("/api/v1/field/projects/1/pending-roster",
                                     json={"manager_id": 2, "member_ids": [3, 4, 5]})
            assert saved.status_code == 200, saved.text
            start = rotation.today()
            plan = week_payload(start, [2, 3, 4, 5])
            no_plan = await client.post("/api/v1/attendance/projects/1/rule",
                                        json=rule_payload(start))
            assert no_plan.status_code == 422 and "个人排班" in no_plan.text
            async with sessions() as db:
                direct_rule = AttendanceRule(id=99, name="直绑轮休规则", work_hour_type="flexible",
                    work_hours_per_day=12, effective_date=start,
                    extra_config={"rule_type": "rotation", "scope_mode": "project",
                                  "project_id": 1, "free_work_hours_mode": "limit:12"})
                db.add(direct_rule)
                await db.commit()
            direct_without_plan = await client.post("/api/v1/field/sites",
                json={"project_id": 1, "name": "虚构现场", "rule_id": 99})
            assert direct_without_plan.status_code == 422 and "个人排班" in direct_without_plan.text
            async with sessions() as db:
                await db.delete(await db.get(AttendanceRule, 99))
                await db.commit()

            active_user["id"] = 2
            leader_preview = await client.post("/api/v1/rotation/projects/1/preview", json=plan)
            assert leader_preview.status_code == 200, leader_preview.text
            leader_rule = await client.post("/api/v1/attendance/projects/1/rule",
                                            json=rule_payload(start))
            assert leader_rule.status_code == 403
            active_user["id"] = 3
            member_preview = await client.post("/api/v1/rotation/projects/1/preview", json=plan)
            assert member_preview.status_code == 403
            active_user["id"] = 1

            preview = await client.post("/api/v1/rotation/projects/1/preview", json=plan)
            assert preview.status_code == 200 and preview.json()["phase"] == "pre_rule"
            assert len(preview.json()["rows"]) == 4 * (7 - start.weekday())
            confirmed = await client.post(
                f"/api/v1/rotation/projects/1/publish?revision={preview.json()['revision']}", json=plan)
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "pending_rule"
            directory = (await client.get("/api/v1/field/directory")).json()
            project = next(p for p in directory["projects"] if p["id"] == 1)
            assert project["pre_rule_schedule_ready"]
            assert project["pre_rule_schedule_member_count"] == 4
            assert project["pre_rule_schedule_missing_member_ids"] == []
            assert project["member_count"] == 0
            calendar = (await client.get("/api/v1/rotation/calendar", params={
                "project_id": 1, "start": str(start), "end": plan["end_date"]})).json()
            assert calendar["days"] == []
            assert len(calendar["draft_days"]) == len(preview.json()["rows"])
            assert all(d["status"] == "pending_rule" and d["required_hours"] == 0
                       for d in calendar["draft_days"])
            active_user["id"] = 3
            member_directory = (await client.get("/api/v1/field/directory")).json()
            member_project = next(p for p in member_directory["projects"] if p["id"] == 1)
            assert member_project["pre_rule_schedule_ready"]
            assert member_project["pre_rule_schedule_member_count"] == 4
            assert member_project["pre_rule_schedule_employee_ids"] == []
            assert member_project["pre_rule_schedule_missing_member_ids"] == []
            active_user["id"] = 1
            async with sessions() as db:
                assert not (await db.scalars(select(FieldAssignment))).all()
                assert not (await db.scalars(select(Notification))).all()

            stale = await client.post(
                f"/api/v1/rotation/projects/1/publish?revision={preview.json()['revision']}", json=plan)
            assert stale.status_code == 409  # the draft revisions changed
            wrong_type = await client.post("/api/v1/attendance/projects/1/rule",
                                           json=rule_payload(start, "free"))
            assert wrong_type.status_code == 422 and "现场轮休" in wrong_type.text
            late = await client.post("/api/v1/attendance/projects/1/rule",
                                     json=rule_payload(start + timedelta(days=8)))
            assert late.status_code == 422 and "不能晚于" in late.text
            async with sessions() as db:
                assert not (await db.scalars(select(AttendanceRule))).all()
                assert not (await db.scalars(select(FieldSite))).all()
                assert not (await db.scalars(select(FieldAssignment))).all()
                assert len((await db.scalars(select(RotationDraftDay))).all()) == len(preview.json()["rows"])

            async with sessions() as db:
                db.add(AttendanceRule(id=99, name="直绑轮休规则", work_hour_type="flexible",
                    work_hours_per_day=12, effective_date=start,
                    extra_config={"rule_type": "rotation", "scope_mode": "project",
                                  "project_id": 1, "free_work_hours_mode": "limit:12"}))
                await db.commit()
            created = await client.post("/api/v1/field/sites",
                json={"project_id": 1, "name": "虚构现场", "rule_id": 99})
            assert created.status_code == 200, created.text
            async with sessions() as db:
                assert len((await db.scalars(select(FieldAssignment))).all()) == 4
                assert len((await db.scalars(select(RotationDay))).all()) == len(preview.json()["rows"])
                assert not (await db.scalars(select(RotationDraftDay))).all()
                assert len((await db.scalars(select(Notification))).all()) == 4
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_pre_rule_cross_project_conflict_cancel_and_expired_days(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add_all([
                Employee(id=1, employee_no="M", name="售后经理", phone="19900009201", status="在职"),
                Employee(id=2, employee_no="L1", name="组长一", phone="19900009202", status="在职",
                         is_field_staff=True, position="项目组长候选"),
                Employee(id=3, employee_no="L2", name="组长二", phone="19900009203", status="在职",
                         is_field_staff=True, position="项目组长候选"),
                Employee(id=4, employee_no="P", name="共享组员", phone="19900009204", status="在职",
                         is_field_staff=True),
                Employee(id=5, employee_no="P5", name="替补组员", phone="19900009205", status="在职",
                         is_field_staff=True),
                FieldCustomer(id=1, name="虚构客户"),
                FieldProject(id=1, code="P1", name="项目一", customer_id=1),
                FieldProject(id=2, code="P2", name="项目二", customer_id=1),
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
        start = rotation.today() + timedelta(days=(7 - rotation.today().weekday()) % 7)
        monkeypatch.setattr(rotation, "today", lambda: start)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            for pid, leader in ((1, 2), (2, 3)):
                saved = await client.put(f"/api/v1/field/projects/{pid}/pending-roster",
                                         json={"manager_id": leader, "member_ids": [4]})
                assert saved.status_code == 200, saved.text
            plan1 = week_payload(start, [2, 4])
            first = (await client.post("/api/v1/rotation/projects/1/preview", json=plan1)).json()
            confirmed = await client.post(
                f"/api/v1/rotation/projects/1/publish?revision={first['revision']}", json=plan1)
            assert confirmed.status_code == 200, confirmed.text
            conflict = await client.post("/api/v1/rotation/projects/2/preview",
                                         json=week_payload(start, [3, 4]))
            assert conflict.status_code == 409 and "其他项目" in conflict.text
            cannot_remove = await client.put("/api/v1/field/projects/1/pending-roster",
                                             json={"manager_id": 2, "member_ids": [5]})
            assert cannot_remove.status_code == 409 and "撤销" in cannot_remove.text
            canceled = await client.delete("/api/v1/rotation/projects/1/draft-days",
                                           params={"employee_id": 4})
            assert canceled.status_code == 200 and canceled.json()["count"] == 7
            repeated = await client.delete("/api/v1/rotation/projects/1/draft-days",
                                           params={"employee_id": 4})
            assert repeated.status_code == 200 and repeated.json()["count"] == 0
            replaced = await client.put("/api/v1/field/projects/1/pending-roster",
                                        json={"manager_id": 2, "member_ids": [5]})
            assert replaced.status_code == 200, replaced.text
            preview = await client.post("/api/v1/rotation/projects/1/preview",
                                        json=week_payload(start, [2, 5]))
            assert preview.status_code == 200, preview.text
            confirmed = await client.post(
                f"/api/v1/rotation/projects/1/publish?revision={preview.json()['revision']}",
                json=week_payload(start, [2, 5]))
            assert confirmed.status_code == 200, confirmed.text

            # On the next day, yesterday's unactivated draft cannot become attendance.
            monkeypatch.setattr(rotation, "today", lambda: start + timedelta(days=1))
            created = await client.post("/api/v1/attendance/projects/1/rule",
                                        json=rule_payload(start))
            assert created.status_code == 201, created.text
            async with sessions() as db:
                formal = (await db.scalars(select(RotationDay))).all()
                assert len(formal) == 2 * 6
                assert all(row.work_date > start for row in formal)
                assert not (await db.scalars(select(RotationDraftDay))).all()
                expired = (await db.scalars(select(AuditLog).where(
                    AuditLog.action == "rotation_pre_rule_expired"))).all()
                assert len(expired) == 1 and "过期计划未转为出勤" in expired[0].after_data
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()
