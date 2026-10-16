"""A partial project week publishes only owned dates and sends one inbox item per person."""

from datetime import date, datetime, time, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all models for isolated SQLite tables
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.attendance import AttendanceRule, WorkHourType
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import (FieldAssignment, FieldCustomer, FieldMembership,
                                      FieldProject, FieldRegionalAssignment, FieldSite)
from app.models.notification import Notification
from app.models.organization import Location
from app.models.rotation import RotationDay
from app.services import rotation


@pytest.mark.asyncio
async def test_weekly_publish_person_edits_partial_membership_and_inbox(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    start = date(2026, 10, 19)  # Monday, a completely fictional test week
    end = start + timedelta(days=6)
    transfer = start + timedelta(days=3)
    monkeypatch.setattr(rotation, "today", lambda: start)

    async with sessions() as db:
        db.add_all([
            Employee(id=1, employee_no="TEST-MANAGER", name="售后经理", phone="19900008001", status="在职"),
            Employee(id=2, employee_no="TEST-A", name="组员甲", phone="19900008002", status="在职", is_field_staff=True),
            Employee(id=3, employee_no="TEST-B", name="组员乙", phone="19900008003", status="在职", is_field_staff=True),
            Employee(id=4, employee_no="TEST-LEADER", name="项目组长", phone="19900008004", status="在职", is_field_staff=True),
            Employee(id=5, employee_no="TEST-REGION", name="区域组长", phone="19900008005", status="在职"),
        ])
        await db.flush()
        db.add(EmployeeRole(employee_id=1, role_name="aftersales_manager", granted_by=1))
        db.add(EmployeeRole(employee_id=5, role_name="regional_lead", granted_by=1))
        db.add(FieldCustomer(id=1, name="虚构客户"))
        db.add_all([Location(id=1, name="虚构地点甲"), Location(id=2, name="虚构地点乙")])
        rule = AttendanceRule(id=1, name="12 小时现场轮休", work_hour_type=WorkHourType.flexible,
                              work_hours_per_day=12, effective_date=start,
                              extra_config={"scope_mode": "project", "rule_type": "rotation"})
        db.add(rule)
        db.add_all([
            FieldProject(id=1, code="TEST-P1", name="虚构项目甲", customer_id=1, manager_id=4),
            FieldProject(id=2, code="TEST-P2", name="虚构项目乙", customer_id=1, manager_id=4),
        ])
        await db.flush()
        db.add(FieldRegionalAssignment(project_id=1, employee_id=5, kind="primary",
                                       active_employee_id=5, active_primary_project_id=1, appointed_by=1))
        db.add_all([
            FieldSite(id=1, project_id=1, name="甲项目现场", location_id=1, rule_id=1),
            FieldSite(id=2, project_id=2, name="乙项目现场", location_id=2, rule_id=1),
        ])
        await db.flush()
        membership_dates = [(2, 1, start, end + timedelta(days=1)),
                            (3, 1, start, transfer), (3, 2, transfer, end + timedelta(days=1)),
                            (4, 1, start, end + timedelta(days=1))]
        for assignment_id, (employee_id, project_id, first, after_last) in enumerate(membership_dates, start=1):
            db.add(FieldAssignment(id=assignment_id, site_id=project_id, employee_id=employee_id,
                                   start_date=first, end_date=after_last - timedelta(days=1), created_by=1))
            await db.flush()
            db.add(FieldMembership(assignment_id=assignment_id, employee_id=employee_id,
                                   started_at=datetime.combine(first, time.min, rotation.CST).astimezone(timezone.utc),
                                   ended_at=datetime.combine(after_last, time.min, rotation.CST).astimezone(timezone.utc)))
        await db.commit()

    async def override_db():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    active_user = 1

    async def override_user():
        async with sessions() as db:
            return await db.get(Employee, active_user)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            body = {
                "employee_ids": [2, 3, 4], "start_date": str(start), "end_date": str(end),
                "cycle_start": str(start), "member_days_only": True,
                "edits": [{"date": str(start + timedelta(days=1)), "kind": "rest"}],
                "person_edits": [{"employee_id": 2, "date": str(start + timedelta(days=1)), "kind": "work"}],
            }
            path = "/api/v1/rotation/projects/1"
            legacy = await client.post(path + "/preview", json={**body, "member_days_only": False})
            assert legacy.status_code == 422
            duplicate = await client.post(path + "/preview", json={**body, "person_edits": body["person_edits"] * 2})
            assert duplicate.status_code == 422
            wrong_project_edit = await client.post(path + "/preview", json={**body, "person_edits": [
                {"employee_id": 3, "date": str(transfer), "kind": "rest"}]})
            assert wrong_project_edit.status_code == 422

            preview = await client.post(path + "/preview", json=body)
            assert preview.status_code == 200, preview.text
            rows = preview.json()["rows"]
            assert len(rows) == 17  # 7 + 3 + 7 project-owned dates
            assert next(r for r in rows if r["employee_id"] == 2 and r["date"] == str(start + timedelta(days=1)))["kind"] == "work"
            assert next(r for r in rows if r["employee_id"] == 3 and r["date"] == str(start + timedelta(days=1)))["kind"] == "rest"
            assert {r["date"] for r in rows if r["employee_id"] == 3} == {
                str(start + timedelta(days=offset)) for offset in range(3)}
            assert next(r for r in rows if r["employee_id"] == 2 and r["date"] == str(end))["original_kind"] == "rest"
            async with sessions() as db:
                assert await db.scalar(select(func.count(RotationDay.id))) == 0
                assert await db.scalar(select(func.count(Notification.id))) == 0

            # A late employee conflict must roll back earlier employees in the
            # same publish request and must not emit any inbox messages.
            async with sessions() as db:
                rule = await db.get(AttendanceRule, 1)
                db.add(RotationDay(employee_id=4, project_id=1, work_date=start,
                                   cycle_start=start, original_kind="work", kind="work",
                                   rule_snapshot=rotation.rule_with_shift(rule, None),
                                   revision=1, reason="已有排班", updated_by=1))
                await db.commit()
            conflict_preview = await client.post(path + "/preview", json=body)
            assert conflict_preview.status_code == 200, conflict_preview.text
            conflicted = await client.post(path + f"/publish?revision={conflict_preview.json()['revision']}", json=body)
            assert conflicted.status_code == 409
            async with sessions() as db:
                assert await db.scalar(select(func.count(RotationDay.id))) == 1
                assert await db.scalar(select(func.count(Notification.id))) == 0
                await db.delete(await db.scalar(select(RotationDay).where(RotationDay.employee_id == 4)))
                await db.commit()
            preview = await client.post(path + "/preview", json=body)
            assert preview.status_code == 200, preview.text

            stale = await client.post(path + "/publish?revision=stale", json=body)
            assert stale.status_code == 409
            published = await client.post(path + f"/publish?revision={preview.json()['revision']}", json=body)
            assert published.status_code == 200, published.text
            assert published.json()["count"] == 17
            repeated = await client.post(path + f"/publish?revision={preview.json()['revision']}", json=body)
            assert repeated.status_code == 409

            # Published changes use the existing three-step approval chain.
            # An employee receives a new message only when final approval takes effect.
            active_user = 2
            claim = await client.post("/api/v1/rotation/claims", json={
                "project_id": 1, "kind": "schedule", "reason": "现场临时调班",
                "days": [{"date": str(start + timedelta(days=2)), "kind": "rest"}],
            })
            assert claim.status_code == 200, claim.text
            claim_id = claim.json()["id"]
            active_user = 4
            first_review = await client.post(f"/api/v1/rotation/claims/{claim_id}/review",
                                             json={"action": "approve", "note": "组长核对"})
            assert first_review.status_code == 200, first_review.text
            active_user = 5
            second_review = await client.post(f"/api/v1/rotation/claims/{claim_id}/review",
                                              json={"action": "approve", "note": "区域组长核对"})
            assert second_review.status_code == 200, second_review.text
            async with sessions() as db:
                assert await db.scalar(select(func.count(Notification.id))) == 3
            active_user = 1
            final_review = await client.post(f"/api/v1/rotation/claims/{claim_id}/review",
                                             json={"action": "approve", "note": "经理终审"})
            assert final_review.status_code == 200, final_review.text
            assert final_review.json()["status"] == "approved"
            repeated_review = await client.post(f"/api/v1/rotation/claims/{claim_id}/review",
                                                json={"action": "approve", "note": "重复终审"})
            assert repeated_review.status_code == 409

            target_body = {"employee_ids": [3], "start_date": str(transfer), "end_date": str(end),
                           "cycle_start": str(transfer), "member_days_only": True}
            target_path = "/api/v1/rotation/projects/2"
            target_preview = await client.post(target_path + "/preview", json=target_body)
            assert target_preview.status_code == 200, target_preview.text
            target_rows = target_preview.json()["rows"]
            assert len(target_rows) == 4
            assert target_rows[-1]["original_kind"] == "rest"
            published = await client.post(target_path + f"/publish?revision={target_preview.json()['revision']}",
                                          json=target_body)
            assert published.status_code == 200, published.text

        async with sessions() as db:
            assert await db.scalar(select(func.count(RotationDay.id))) == 21
            notices = (await db.scalars(select(Notification).order_by(Notification.recipient_id, Notification.id))).all()
            assert [(n.recipient_id, n.ref_type, n.ref_id) for n in notices] == [
                (2, "rotation_schedule", 1), (2, "rotation_schedule", 1),
                (3, "rotation_schedule", 1),
                (3, "rotation_schedule", 2), (4, "rotation_schedule", 1)]
            assert notices[1].title == "个人排班调整已生效"
            assert all(not notice.is_read for notice in notices)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()
