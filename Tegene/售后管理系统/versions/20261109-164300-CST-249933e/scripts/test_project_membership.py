"""Isolated acceptance: future A→B→A handoff, one daily project, frozen punch history."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sandbox = tempfile.TemporaryDirectory(prefix="aftersales-membership-")
os.environ.update(DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///" + sandbox.name + "/test.db",
                  BACKGROUND_JOBS="false", LOGIN_MODE="password", ENVIRONMENT="development")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func
from app.bootstrap import initialize, seed
from app.core.database import async_session, engine
from app.main import app
from app.models.employee import Employee
from app.models.attendance import AttendanceRecord, AttendanceRule, AttendancePunchTimeRecord
from app.models.field_service import FieldMembership, FieldAttendanceSegment
from app.services import field_membership as service
from app.services import field_pending


async def main():
    await initialize()
    await seed(True)
    await initialize()
    app.state.limiter.enabled = False
    tokens = {}
    day = datetime.now(service.CST).date()
    moment = datetime.combine(day, time(8), service.CST).astimezone(timezone.utc)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def req(method, path, who="admin", expected=200, **kw):
            response = await client.request(method, "/api/v1" + path,
                headers={"Authorization": "Bearer " + tokens[who]} if who in tokens else {}, **kw)
            assert response.status_code == expected, (path, response.status_code, response.text[:1200])
            return response.json() if response.content else None

        for who, phone, password in [
            ("admin", "19900000001", "Demo-AfterSales-2026!"),
            ("legacy_manager", "19900000002", "Demo-AfterSales-2026!"),
        ]:
            tokens[who] = (await req("POST", "/auth/login", who,
                json={"phone": phone, "password": password}))["access_token"]
        person = await req("POST", "/field/people", json={
            "name": "流转工程师", "phone": "13800000111", "employee_no": "FLOW-1",
            "role": "employee", "status": "在职", "is_active": True})
        eid = person["id"]
        tokens["engineer"] = (await req("POST", "/auth/login", "engineer",
            json={"phone": "13800000111", "password": "585858"}))["access_token"]
        await req("POST", "/auth/management-login", "engineer", expected=403,
                  json={"phone": "13800000111", "password": "585858"})
        directory = await req("GET", "/field/directory")
        base = directory["projects"][0]
        # Project leaders now join their appointed projects automatically.
        # Give A and B distinct leaders so the fixture itself does not create
        # a conflicting cross-project membership for the seeded manager.
        leaders = {}
        for code, phone in [("FLOW-A", "13800000114"), ("FLOW-B", "13800000115")]:
            leader = await req("POST", "/field/people", json={
                "name": f"{code} 组长", "phone": phone,
                "employee_no": f"{code}-LEAD", "role": "employee",
                "status": "在职", "is_active": True})
            leaders[code] = leader["id"]
        projects = []
        for code, clock_in in [("FLOW-A", "08:00:00"), ("FLOW-B", "10:00:00")]:
            rule = await req("POST", "/attendance/rules", expected=201,
                json={"name": code, "work_hour_type": "standard", "clock_in_time": clock_in,
                      "clock_out_time": "18:00:00", "effective_date": str(day),
                      "require_gps": False, "require_wifi": False, "require_photo": False,
                      "extra_config": {"scope_mode": "project"}})
            project = await req("POST", "/field/projects", json={
                "code": code, "name": code, "customer_id": base["customer_id"],
                "manager_id": leaders[code]})
            await req("PUT", f"/field/projects/{project['id']}/configuration",
                      json={"rule_id": rule["id"]})
            projects.append((project["id"], rule["id"]))
        a, b = projects[0][0], projects[1][0]
        for label, phone in [("区域A", "13800000112"), ("区域B", "13800000113")]:
            person = await req("POST", "/field/people", json={
                "name": label, "phone": phone, "role": "regional_lead"})
            tokens[label] = (await req("POST", "/auth/login", label,
                json={"phone": phone, "password": "585858"}))["access_token"]
            if label == "区域A":
                lead_a = person["id"]
            else:
                lead_b = person["id"]
        await req("PUT", f"/field/projects/{a}/regional-leads", json={
            "primary_lead_id": lead_a, "reason": "测试原项目主责"})
        await req("PUT", f"/field/projects/{b}/regional-leads", json={
            "primary_lead_id": lead_b, "reason": "测试接收项目主责"})

        with patch.object(service, "clock_now", side_effect=lambda: moment), \
                patch.object(field_pending, "clock_now", side_effect=lambda: moment):
            added = await req("POST", f"/field/projects/{a}/members", json={"employee_ids": [eid]})
            assignment_a = added["members"][0]["id"]
            await req("POST", f"/field/assignments/{assignment_a}/transfer", expected=409,
                      json={"project_id": b})
            first_transfer = await req("POST", f"/field/assignments/{assignment_a}/transfer-requests", "区域B",
                json={"project_id": b, "effective_date": str(day + timedelta(days=1)),
                      "reason": "次日转项目 B"})
            await req("POST", f"/field/transfer-requests/{first_transfer['id']}/review", "区域A",
                      json={"approve": True, "note": "原项目交接完成"})
            async with async_session() as db:
                assert (await service.project_on(db, eid, day))[3].id == a
                assert (await service.project_on(db, eid, day + timedelta(days=1)))[3].id == b
            runtime_a = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime_a["project"]["id"] == a
            await req("POST", "/attendance/clock-in", "engineer",
                      json={"project_context": runtime_a["project_context"]})
            moment += timedelta(hours=2)
            runtime_a = await req("GET", "/attendance/my/runtime", "engineer")
            await req("POST", "/attendance/clock-out", "engineer",
                      json={"project_context": runtime_a["project_context"]})
            moment = datetime.combine(day + timedelta(days=1), time(10), service.CST).astimezone(timezone.utc)
            runtime_b = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime_b["project"]["id"] == b
            await req("POST", "/attendance/clock-in", "engineer",
                      json={"project_context": runtime_b["project_context"]})
            moment += timedelta(hours=2)
            runtime_b = await req("GET", "/attendance/my/runtime", "engineer")
            await req("POST", "/attendance/clock-out", "engineer",
                      json={"project_context": runtime_b["project_context"]})
            # Later rule edits must not rewrite already punched source snapshots.
            await req("PUT", f"/attendance/rules/{projects[1][1]}",
                      json={"name": "B 新规则", "clock_in_time": "12:00:00"})
            historic = await req("GET", f"/attendance/records?employee_id={eid}", "engineer")
            punched = {r["date"]: r for r in historic["items"]}
            assert len(punched[str(day + timedelta(days=1))]["project_segments"]) == 1
            assert punched[str(day + timedelta(days=1))]["project_segments"][0]["project_id"] == b
            listing = await req("GET", "/field/directory")
            assignment_b = next(row["id"] for row in listing["assignments"]
                                if row["employee_id"] == eid and row["is_current"])
            second = await req("POST", f"/field/assignments/{assignment_b}/transfer-requests", "区域A",
                json={"project_id": a, "effective_date": str(day + timedelta(days=2)),
                      "reason": "次日回项目 A"})
            await req("POST", f"/field/transfer-requests/{second['id']}/review", "区域B",
                      json={"approve": True, "note": "B 项目交接完成"})
            moment = datetime.combine(day + timedelta(days=2), time(8), service.CST).astimezone(timezone.utc)
            runtime_a2 = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime_a2["project"]["id"] == a
            await req("POST", "/attendance/clock-in", "engineer",
                      json={"project_context": runtime_a2["project_context"]})
            moment += timedelta(hours=1)
            runtime_a2 = await req("GET", "/attendance/my/runtime", "engineer")
            await req("POST", "/attendance/clock-out", "engineer",
                      json={"project_context": runtime_a2["project_context"]})
            async with async_session() as db:
                assert (await service.project_on(db, eid, day + timedelta(days=2)))[3].id == a
                assert await db.scalar(select(func.count(FieldMembership.id)).where(
                    FieldMembership.employee_id == eid)) == 3
                assert await db.scalar(select(func.count(FieldAttendanceSegment.id)).where(
                    FieldAttendanceSegment.employee_id == eid)) == 3
                assert await db.scalar(select(func.count(AttendancePunchTimeRecord.id)).where(
                    AttendancePunchTimeRecord.employee_id == eid)) == 6

        await req("PUT", f"/field/people/{eid}", json={
            "name": "流转工程师", "phone": "13800000111", "employee_no": "FLOW-1",
            "role": "employee", "status": "离职", "is_active": False})
        await req("GET", "/auth/me", "engineer", expected=403)
        async with async_session() as db:
            person = await db.get(Employee, eid)
            assert person.status == "离职" and not person.is_active
    await engine.dispose()
    print("PASS: future A/B/A handoff, one main project per day, independent punch history, rule snapshot and account lockout")


if __name__ == "__main__":
    asyncio.run(main())
