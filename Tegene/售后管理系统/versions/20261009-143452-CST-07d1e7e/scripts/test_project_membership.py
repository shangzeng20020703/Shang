"""Isolated acceptance: roster -> A -> B -> A, frozen history and account lockout."""
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
from app.models.attendance import AttendanceRecord, AttendanceRule, AttendancePunchTimeRecord, AttendanceMonthSummary, MonthlySummaryStatus
from app.models.field_service import FieldMembership, FieldAttendanceSegment, FieldAssignment
from app.services import field_membership as service


async def main():
    await initialize()
    await seed(True)
    await initialize()
    app.state.limiter.enabled = False
    tokens = {}
    day = datetime.now(service.CST).date()
    moment = datetime.combine(day, time(8), service.CST).astimezone(timezone.utc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def req(method, path, who="admin", status=200, **kw):
            response = await client.request(method, "/api/v1" + path,
                headers={"Authorization": "Bearer " + tokens[who]} if who in tokens else {}, **kw)
            assert response.status_code == status, (path, response.status_code, response.text[:1500])
            return response.json()

        for i, who in [(1, "admin"), (2, "manager")]:
            tokens[who] = (await req("POST", "/auth/login", who, json={"phone": f"1990000000{i}", "password": "Demo-AfterSales-2026!"}))["access_token"]
        roster = dict(name="流转工程师", phone="13800000111", employee_no="FLOW-1", role="employee", status="在职", is_active=True)
        employee_id = (await req("POST", "/field/people", json=roster))["id"]
        tokens["engineer"] = (await req("POST", "/auth/login", "engineer", json={"phone":roster["phone"], "password":"585858"}))["access_token"]
        await req("POST", "/auth/management-login", "engineer", status=403, json={"phone":roster["phone"], "password":"585858"})
        await req("GET", "/auth/management-session", "engineer", status=403)
        assert not (await req("GET", "/attendance/my/runtime", "engineer"))["has_rule"]
        await req("POST", "/attendance/clock-in", "engineer", status=409, json={})
        from app.services.attendance import AttendanceRecordService, MonthSummaryService
        async with async_session() as db:
            assert not await MonthSummaryService._expected_work_day(db, employee_id, day, None)
        directory = await req("GET", "/field/directory")
        base = directory["projects"][0]
        projects = []
        for code, clock_in in [("FLOW-A", "08:00:00"), ("FLOW-B", "10:00:00")]:
            rule = await req("POST", "/attendance/rules", status=201, json={"name":code, "work_hour_type":"standard", "clock_in_time":clock_in, "clock_out_time":"18:00:00", "effective_date":str(day), "require_gps":False, "require_wifi":False, "require_photo":False, "extra_config":{"scope_mode":"project"}})
            project = await req("POST", "/field/projects", json={"code":code,"name":code,"customer_id":base["customer_id"],"manager_id":base["manager_id"]})
            await req("PUT", f"/field/projects/{project['id']}/configuration", json={"rule_id":rule["id"]})
            projects.append((project["id"], rule["id"]))
        a, b = projects[0][0], projects[1][0]
        with patch.object(service, "clock_now", side_effect=lambda: moment):
            added = await req("POST", f"/field/projects/{a}/members", "manager", json={"employee_ids":[employee_id]})
            assignment_a = added["members"][0]["id"]
            async with async_session() as db:
                future_rule = await AttendanceRecordService._get_rule_runtime(db, employee_id, day + timedelta(days=1))
                assert future_rule["rule"].id == projects[0][1]
            runtime_a = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime_a["project"]["id"] == a and runtime_a["current_record"] is None
            await req("POST", "/attendance/clock-in", "engineer", json={"project_context":runtime_a["project_context"]})
            # Import does not reset passwords or allocate duplicate accounts.
            await req("PUT", f"/field/people/{employee_id}", json={**roster,"password":"Personal-Password-1"})
            file = {"file":("people.csv",f"姓名,手机号\n{roster['name']},{roster['phone']}\n".encode())}
            await req("POST", "/field/roster/import?preview=false", files=file)
            await req("POST", "/auth/login", "engineer", status=401, json={"phone":roster["phone"],"password":"585858"})
            await req("POST", "/auth/login", "engineer", json={"phone":roster["phone"],"password":"Personal-Password-1"})
            moment += timedelta(hours=2)
            await req("POST", f"/field/assignments/{assignment_a}/transfer", "engineer", status=403, json={"project_id":b})
            # Failed destination validation must roll the entire transfer back.
            await req("POST", f"/field/assignments/{assignment_a}/transfer", status=404, json={"project_id":999999})
            assert (await req("GET", "/attendance/my/runtime", "engineer"))["project"]["id"] == a
            await req("POST", f"/field/assignments/{assignment_a}/transfer", "manager", json={"project_id":b})
            await req("POST", "/attendance/clock-out", "engineer", status=409, json={"project_context":runtime_a["project_context"]})
            runtime_b = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime_b["project"]["id"] == b and runtime_b["current_record"] is None
            assert runtime_b["project_segments"][0]["needs_review"]
            await req("POST", "/attendance/clock-in", "engineer", json={"project_context":runtime_b["project_context"]})
            # Editing the rule later cannot rewrite the active segment or its history.
            await req("PUT", f"/attendance/rules/{projects[1][1]}", json={"name":"B 改名","clock_in_time":"12:00:00"})
            runtime_b = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime_b["rule"]["name"] == "FLOW-B" and runtime_b["expected"]["clock_in"] == "10:00"
            moment += timedelta(hours=2)
            await req("POST", "/attendance/clock-out", "engineer", json={"project_context":runtime_b["project_context"]})
            directory = await req("GET", "/field/directory")
            assignment_b = next(r["id"] for r in directory["assignments"] if r["employee_id"] == employee_id and r["is_current"])
            await req("POST", f"/field/assignments/{assignment_b}/transfer", "manager", json={"project_id":a})
            runtime_a2 = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime_a2["current_record"] is None and runtime_a2["project_context"] != runtime_a["project_context"]
            await req("POST", "/attendance/clock-in", "engineer", json={"project_context":runtime_a2["project_context"]})
            moment += timedelta(hours=1)
            runtime_a2 = await req("GET", "/attendance/my/runtime", "engineer")
            await req("POST", "/attendance/clock-out", "engineer", json={"project_context":runtime_a2["project_context"]})
            records = await req("GET", f"/attendance/records?employee_id={employee_id}", "engineer")
            record = records["items"][0]
            assert len(record["project_segments"]) == 3, record
            assert float(record["work_hours"]) == 3, record  # Never the five-hour A-in -> A2-out span.
            assert record["project_segments"][0]["clock_out_time"] is None
            assert record["display_status"] == "待核对"
            dashboard = await req("GET", "/field/dashboard", "manager", params={"day":str(day)})
            parts = [r for r in dashboard["rows"] if r["employee_id"] == employee_id]
            assert len(parts) == 3 and parts[0]["status"] == "待核对", parts
            # A locked month remains readable; new punches fail, termination still succeeds.
            async with async_session() as db:
                summary = await db.scalar(select(AttendanceMonthSummary).where(AttendanceMonthSummary.employee_id == employee_id, AttendanceMonthSummary.year == day.year, AttendanceMonthSummary.month == day.month))
                summary.status = MonthlySummaryStatus.locked
                await db.commit()
            await req("POST", "/attendance/clock-out", "engineer", status=400, json={"project_context":runtime_a2["project_context"]})
            assert len((await req("GET", f"/attendance/records?employee_id={employee_id}", "engineer"))["items"][0]["project_segments"]) == 3
            # Termination invalidates already issued tokens and retains all evidence.
            await req("PUT", f"/field/people/{employee_id}", json={**roster,"status":"离职","is_active":True})
            await req("GET", "/auth/me", "engineer", status=403)
            await req("POST", "/attendance/clock-in", "engineer", status=403, json={"project_context":runtime_a2["project_context"]})
            await req("POST", "/auth/login", "engineer", status=401, json={"phone":roster["phone"],"password":"Personal-Password-1"})
            # Bulk import matches the roster only and rolls back every row on conflict.
            night_person = await req("POST", "/field/people", json={**roster, "name":"夜班工程师", "phone":"13800000112", "employee_no":"FLOW-2"})
            night_id = night_person["id"]
            files = {"file":("members.csv", "姓名,手机号\n夜班工程师,13800000112\n未知人员,13800000999\n".encode())}
            report = await req("POST", f"/field/projects/{b}/members/import", "manager", files=files)
            assert report["success"] == 1 and report["failed"] == 1
            await req("POST", f"/field/projects/{b}/members/import?preview=false", "manager", status=422, files=files)
            async with async_session() as db:
                assert not await service.current(db, night_id, moment)
            files = {"file":("members.csv", "姓名,手机号\n夜班工程师,13800000112\n".encode())}
            await req("POST", f"/field/projects/{b}/members/import?preview=false", "manager", files=files)
            await req("POST", f"/field/projects/{a}/members", "manager", status=409, json={"employee_ids":[night_id]})
            # Night checkout belongs to the previous workday; transfer does not inherit it.
            tokens["night"] = (await req("POST", "/auth/login", "night", json={"phone":"13800000112", "password":"585858"}))["access_token"]
            night_rule = await req("POST", "/attendance/rules", status=201, json={"name":"夜班", "work_hour_type":"standard", "clock_in_time":"20:00:00", "clock_out_time":"08:00:00", "effective_date":str(day), "require_gps":False, "extra_config":{"scope_mode":"project"}})
            await req("PUT", f"/field/projects/{b}/configuration", json={"rule_id":night_rule["id"]})
            moment = datetime.combine(day, time(20), service.CST).astimezone(timezone.utc)
            night = await req("GET", "/attendance/my/runtime", "night")
            await req("POST", "/attendance/clock-in", "night", json={"project_context":night["project_context"]})
            moment += timedelta(hours=11)
            night = await req("GET", "/attendance/my/runtime", "night")
            assert night["date"] == str(day) and night["current_record"]["clock_in_time"]
            await req("POST", "/attendance/clock-out", "night", json={"project_context":night["project_context"]})
            directory = await req("GET", "/field/directory")
            night_assignment = next(row["id"] for row in directory["assignments"] if row["employee_id"] == night_id)
            await req("POST", f"/field/assignments/{night_assignment}/transfer", "manager", json={"project_id":a})
            moved = await req("GET", "/attendance/my/runtime", "night")
            assert moved["date"] == str(day + timedelta(days=1)) and moved["current_record"] is None
        async with async_session() as db:
            person = await db.get(Employee, employee_id)
            assert person.status == "离职" and not person.is_active
            assert await db.scalar(select(func.count(FieldMembership.id)).where(FieldMembership.employee_id == employee_id)) == 3
            assert await db.scalar(select(func.count(FieldAttendanceSegment.id)).where(FieldAttendanceSegment.employee_id == employee_id)) == 3
            assert await db.scalar(select(func.count(AttendancePunchTimeRecord.id)).where(AttendancePunchTimeRecord.employee_id == employee_id)) == 5
            assert await db.scalar(select(FieldMembership.id).where(FieldMembership.active_employee_id == employee_id)) is None
    await engine.dispose()
    print("PASS: phone/password, unassigned guard, backend denial, password preservation, atomic transfer/import, stale page, A/B/A segments, frozen rules, no cross-project pairing, locked-month reads, termination token lockout, overnight transfer, retained evidence")


if __name__ == "__main__":
    asyncio.run(main())
