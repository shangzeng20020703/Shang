"""Independent plans are confirmed before a rule and continue across projects."""

from datetime import datetime, time, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.attendance import AttendanceRule
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import (FieldAssignment, FieldCustomer, FieldMembership,
                                      FieldProject, FieldProjectPendingMember, FieldSite)
from app.models.organization import Location
from app.models.rotation import RotationDay
from app.services import rotation
from app.services.field_membership import CST, day_runtime, punch_context
from app.services.field_pending import rotation_punch_ready


def body(start, template, length, *, tbd_after=None, custom=False):
    days = []
    for offset in range(length):
        day = start + timedelta(days=offset)
        kind = "rest" if (template == "six_one" and offset % 7 == 6 or
                          template == "twelve_two" and offset % 14 >= 12 or
                          template == "eighteen_three" and offset % 21 >= 18) else "work"
        if custom or tbd_after is not None and offset >= tbd_after:
            item = {"date": str(day), "kind": kind}
            if tbd_after is not None and offset >= tbd_after:
                item["project_id"] = None
            days.append(item)
    return {"start_date": str(start), "end_date": str(start + timedelta(days=length - 1)),
            "cycle_start": str(start), "template": template,
            "days": days, "reason": "周期排班"}


@pytest.mark.asyncio
async def test_independent_patterns_rule_gate_tbd_transfer_and_self_read(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    start = rotation.today() + timedelta(days=1)
    second = start + timedelta(days=2)
    transfer = start + timedelta(days=3)
    async with sessions() as db:
        db.add_all([
            Employee(id=1, employee_no="M", name="售后经理", phone="19900009601", status="在职"),
            Employee(id=2, employee_no="P2", name="成员甲", phone="19900009602", status="在职", is_field_staff=True),
            Employee(id=3, employee_no="P3", name="成员乙", phone="19900009603", status="在职", is_field_staff=True),
            Employee(id=4, employee_no="R4", name="未分配区域组长", phone="19900009604", status="在职"),
            FieldCustomer(id=1, name="虚构客户"),
            FieldProject(id=1, code="P1", name="项目一", customer_id=1),
            FieldProject(id=2, code="P2", name="项目二", customer_id=1),
            FieldProjectPendingMember(project_id=1, employee_id=2, created_by=1),
            FieldProjectPendingMember(project_id=1, employee_id=3, created_by=1),
        ])
        await db.flush()
        db.add(EmployeeRole(employee_id=1, role_name="aftersales_manager", granted_by=1))
        db.add(EmployeeRole(employee_id=4, role_name="regional_lead", granted_by=1))
        db.add_all([
            AttendanceRule(id=1, name="项目一轮休", work_hour_type="flexible",
                work_hours_per_day=12, effective_date=start,
                extra_config={"rule_type": "rotation", "scope_mode": "project", "project_id": 1}),
            AttendanceRule(id=2, name="项目二轮休", work_hour_type="flexible",
                work_hours_per_day=12, effective_date=transfer,
                extra_config={"rule_type": "rotation", "scope_mode": "project", "project_id": 2}),
        ])
        await db.commit()

    async def override_db():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    active_user = {"id": 1}

    async def override_user():
        async with sessions() as db:
            return await db.get(Employee, active_user["id"])

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            p1 = body(start, "twelve_two", 14, tbd_after=3)
            base = "/api/v1/rotation/projects/1/person-plans"
            long_preview = await client.post(f"{base}/2/preview", json=body(start, "eighteen_three", 42))
            assert long_preview.status_code == 200, long_preview.text
            long_rows = long_preview.json()["rows"]
            assert [i for i, row in enumerate(long_rows) if row["kind"] == "rest"] == [18, 19, 20, 39, 40, 41]
            assert {long_rows[i]["rest_type"] for i in (18, 19, 20, 39, 40, 41)} == {"concentrated"}
            preview = await client.post(f"{base}/2/preview", json=p1)
            assert preview.status_code == 200, preview.text
            assert len(preview.json()["rows"]) == 14
            assert preview.json()["rows"][-1]["rest_type"] == "concentrated"
            confirmed = await client.put(f"{base}/2/confirm", params={
                "revision": preview.json()["revision"]}, json=p1)
            assert confirmed.status_code == 200, confirmed.text
            assert confirmed.json()["status"] == "pending_rule"
            active_user["id"] = 4
            regional_read = await client.get(f"{base}/2")
            assert regional_read.status_code == 200, regional_read.text
            assert regional_read.json()["plans"][0]["id"] == confirmed.json()["id"]
            regional_write = await client.post(f"{base}/2/preview", json=p1)
            assert regional_write.status_code == 403
            regional_confirm = await client.put(f"{base}/2/confirm", params={
                "revision": preview.json()["revision"]}, json=p1)
            assert regional_confirm.status_code == 403
            active_user["id"] = 1
            stale = await client.put(f"{base}/2/confirm", params={
                "revision": preview.json()["revision"]}, json=p1)
            assert stale.status_code == 409
            pending = await client.get("/api/v1/field/directory")
            project = next(p for p in pending.json()["projects"] if p["id"] == 1)
            assert not project["pre_rule_schedule_ready"]
            assert project["pre_rule_schedule_missing_member_ids"] == [3]

            p2 = body(second, "six_one", 7)
            early = {**p2, "days": [{"date": str(second + timedelta(days=2)), "kind": "rest"}]}
            early_preview = await client.post(f"{base}/3/preview", json=early)
            assert early_preview.status_code == 200, early_preview.text
            assert early_preview.json()["rows"][2]["rest_type"] == "concentrated"
            assert early_preview.json()["rows"][6]["rest_type"] == "concentrated"
            preview2 = await client.post(f"{base}/3/preview", json=p2)
            assert preview2.status_code == 200, preview2.text
            assert preview2.json()["rows"][-1]["rest_type"] == "ordinary"
            confirmed2 = await client.put(f"{base}/3/confirm", params={
                "revision": preview2.json()["revision"]}, json=p2)
            assert confirmed2.status_code == 200, confirmed2.text
            ready = await client.get("/api/v1/field/directory")
            project = next(p for p in ready.json()["projects"] if p["id"] == 1)
            assert project["pre_rule_schedule_ready"]
            assert project["pre_rule_schedule_member_count"] == 2

            rule = await client.post("/api/v1/field/sites", json={
                "project_id": 1, "name": "项目一现场", "rule_id": 1})
            assert rule.status_code == 200, rule.text
            follow_start = second + timedelta(days=7)
            follow = body(follow_start, "six_one", 7)
            follow["cycle_start"] = str(second)
            follow_preview = await client.post(f"{base}/3/preview", json=follow)
            assert follow_preview.status_code == 200, follow_preview.text
            follow_confirmed = await client.put(f"{base}/3/confirm", params={
                "revision": follow_preview.json()["revision"]}, json=follow)
            assert follow_confirmed.status_code == 200, follow_confirmed.text
            assert follow_confirmed.json()["published_count"] == 7
            async with sessions() as db:
                assignments = (await db.scalars(select(FieldAssignment).where(
                    FieldAssignment.site_id == rule.json()["id"]))).all()
                assert {a.employee_id: a.start_date for a in assignments} == {2: start, 3: second}
                published = (await db.scalars(select(RotationDay).where(
                    RotationDay.project_id == 1))).all()
                assert len(published) == 17  # 3 project-one days + 14 independent dates
                ordinary = next(row for row in published if row.employee_id == 3 and
                                row.work_date == second + timedelta(days=6))
                assert ordinary.rule_snapshot["_rotation_rest_type"] == "ordinary"
                source_assignment = next(a for a in assignments if a.employee_id == 2)
                source_membership = await db.scalar(select(FieldMembership).where(
                    FieldMembership.assignment_id == source_assignment.id))
                # The old open-ended membership spans the TBD date, but it must
                # not turn that explicitly unassigned date into a punch day.
                assert not await rotation_punch_ready(db, 2, 1, transfer)
                assert await day_runtime(db, await db.get(Employee, 2), transfer) is None
                tbd_noon = datetime.combine(transfer, time(12), CST).astimezone(timezone.utc)
                assert await punch_context(db, 2, tbd_noon) is None
                # An ordinary unpublished date that still belongs to project 1
                # retains the existing "待确认" punch path.
                unplanned = start + timedelta(days=14)
                assert await rotation_punch_ready(db, 2, 1, unplanned)
                normal_noon = datetime.combine(unplanned, time(12), CST).astimezone(timezone.utc)
                assert (await punch_context(db, 2, normal_noon))["runtime"]["rotation"]["kind"] == "pending"

            active_user["id"] = 2
            self_view = await client.get(f"{base}/2")
            assert self_view.status_code == 200
            assert self_view.json()["established_cycle_start"] == str(start)
            assert len(self_view.json()["bindable_days"]) == 11
            own_calendar = await client.get("/api/v1/rotation/calendar", params={
                "start": str(start), "end": str(start + timedelta(days=13))})
            assert own_calendar.status_code == 200, own_calendar.text
            pending_project = [row for row in own_calendar.json()["draft_days"]
                               if row["status"] == "pending_project"]
            assert len(pending_project) == 11
            assert all(row["employee_id"] == 2 and row["project_id"] is None and
                       row["required_hours"] == 0 for row in pending_project)
            attempt = await client.post("/api/v1/rotation/projects/2/person-plans/2/bind",
                json={"plan_id": confirmed.json()["id"], "dates": [str(transfer)], "reason": "项目交接"})
            assert attempt.status_code == 403
            active_user["id"] = 1
            scoped_calendar = await client.get("/api/v1/rotation/calendar", params={
                "project_id": 1, "start": str(start), "end": str(start + timedelta(days=13))})
            assert scoped_calendar.status_code == 200, scoped_calendar.text
            assert all(row["status"] != "pending_project" for row in scoped_calendar.json()["draft_days"])

            # Simulate an already approved transfer. The bind path itself never
            # changes participation or bypasses the transfer approval chain.
            async with sessions() as db:
                assignment = await db.get(FieldAssignment, source_assignment.id)
                assignment.end_date = transfer - timedelta(days=1)
                membership = await db.get(FieldMembership, source_membership.id)
                membership.ended_at = datetime.combine(transfer, time.min, CST).astimezone(timezone.utc)
                membership.active_employee_id = None
                db.add(Location(id=2, name="项目二位置"))
                db.add(FieldSite(id=2, project_id=2, name="项目二现场", location_id=2, rule_id=2))
                await db.flush()
                target = FieldAssignment(site_id=2, employee_id=2,
                    start_date=transfer, end_date=transfer + timedelta(days=10),
                    status="active", created_by=1)
                db.add(target)
                await db.flush()
                db.add(FieldMembership(assignment_id=target.id, employee_id=2,
                    started_at=datetime.combine(transfer, time.min, CST).astimezone(timezone.utc),
                    active_employee_id=2))
                await db.commit()
            target_view = await client.get("/api/v1/rotation/projects/2/person-plans/2")
            assert target_view.status_code == 200, target_view.text
            assert target_view.json()["plans"] == []
            assert target_view.json()["established_cycle_start"] == str(start)
            bound = await client.post("/api/v1/rotation/projects/2/person-plans/2/bind",
                json={"plan_id": confirmed.json()["id"], "dates": [str(transfer)], "reason": "项目交接"})
            assert bound.status_code == 200, bound.text
            assert bound.json()["status"] == "published"
            async with sessions() as db:
                target_day = await db.scalar(select(RotationDay).where(
                    RotationDay.employee_id == 2, RotationDay.work_date == transfer))
                assert target_day.project_id == 2
                assert target_day.cycle_start == start
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()


def test_custom_delayed_rest_is_concentrated():
    from app.services.rotation_person_plan import _rest_types
    rows = [{"kind": "work", "original_kind": "work"} for _ in range(8)] + [
        {"kind": "rest", "original_kind": "rest"}]
    _rest_types(rows, "custom")
    assert rows[-1]["rest_type"] == "concentrated"
    rows = [{"kind": "work", "original_kind": "work"} for _ in range(6)] + [
        {"kind": "rest", "original_kind": "rest"}]
    _rest_types(rows, "custom")
    assert rows[-1]["rest_type"] == "ordinary"
