"""Isolated API acceptance for aftersales roles, rule ownership, and dated handoff."""
import asyncio
import io
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl
scratch = tempfile.TemporaryDirectory(prefix="aftersales-field-roles-")
os.environ.update(DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///" + scratch.name + "/test.db",
                  BACKGROUND_JOBS="false", LOGIN_MODE="password", ENVIRONMENT="development")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from httpx import AsyncClient, ASGITransport
from app.bootstrap import initialize, seed
from app.core.database import async_session, engine
from app.main import app
from app.models.attendance import (
    AttendanceMonthSummary, AttendancePunchTimeRecord, AttendanceRecord,
    AttendanceStatus, ShiftSchedule, ShiftType,
)
from app.models.employee import Employee
from app.models.field_service import FieldAssignmentDay
from sqlalchemy import select
from app.services.field_membership import CST, project_on


async def main():
    await initialize()
    await seed(True)
    app.state.limiter.enabled = False
    tokens = {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def req(method, path, who="admin", expected=200, **kw):
            response = await client.request(method, "/api/v1" + path,
                headers={"Authorization": "Bearer " + tokens[who]} if who in tokens else {}, **kw)
            assert response.status_code == expected, (method, path, response.status_code, response.text[:1200])
            return response.json() if response.content else None

        async def login(who, phone, password="585858"):
            tokens[who] = (await req("POST", "/auth/login", who, json={"phone": phone, "password": password}))["access_token"]

        await login("admin", "19900000001", "Demo-AfterSales-2026!")
        await login("legacy", "19900000002", "Demo-AfterSales-2026!")
        async def person(name, phone, role="employee", category=None):
            row = await req("POST", "/field/people", json={"name": name, "phone": phone, "role": role,
                **({"field_staff_category": category} if category else {})})
            await login(name, phone)
            return row["id"]

        lead_a = await person("区域甲", "13800000901", "regional_lead")
        lead_b = await person("区域乙", "13800000902", "regional_lead")
        manager = await person("售后经理", "13800000903", "aftersales_manager")
        member = await person("跨项目组员", "13800000904")
        ordinary = await person("普通现场组员", "13800000906")
        b_leader = await person("乙项目组长", "13800000907", category="leader_candidate")
        unowned_leader = await person("待分配项目组长", "13800000908", category="leader_candidate")
        day = datetime.now(CST).date() - timedelta(days=1)
        async with async_session() as db:
            company_person = Employee(employee_no="COMPANY-ROLE-QA", name="非售后员工",
                                      phone="13800000905", hashed_password="unused",
                                      is_active=True, status="在职", is_field_staff=False)
            db.add(company_person)
            await db.flush()
            company_record = AttendanceRecord(employee_id=company_person.id, date=day,
                status=AttendanceStatus.absent, anomaly_type="非售后考勤不得外泄")
            db.add(company_record)
            db.add(AttendancePunchTimeRecord(employee_id=company_person.id, work_date=day,
                punch_time=datetime.now(CST), punch_type="clock_in"))
            db.add(AttendanceMonthSummary(employee_id=company_person.id, year=day.year,
                month=day.month, absent_count=1))
            await db.flush()
            company_record_id = company_record.id
            await db.commit()
            company_person_id = company_person.id
        manager_people = await req("GET", "/field/people", "售后经理")
        assert all(p["id"] != company_person_id for p in manager_people)
        await req("PUT", f"/field/people/{company_person_id}", "售后经理", expected=403,
                  json={"name": "被越权修改", "phone": "13800000905", "role": "employee"})
        old = await req("GET", "/field/directory")
        first = old["projects"][0]
        project_a = first["id"]
        customer = first["customer_id"]
        rule = first["rule_id"]
        assert rule
        project_b = (await req("POST", "/field/projects", json={
            "code": "ROLE-B", "name": "权限项目乙", "customer_id": customer,
            "manager_id": b_leader})) ["id"]
        await req("PUT", f"/field/projects/{project_b}/configuration", json={"rule_id": rule})
        await req("PUT", f"/field/projects/{project_a}/regional-leads", "legacy", expected=403,
                  json={"primary_lead_id": lead_a})
        await req("PUT", f"/field/projects/{project_a}/regional-leads", "售后经理",
                  json={"primary_lead_id": lead_a, "assistant_lead_ids": [], "reason": "测试主责"})
        await req("PUT", f"/field/projects/{project_b}/regional-leads", "售后经理",
                  json={"primary_lead_id": lead_b, "assistant_lead_ids": [], "reason": "测试主责"})
        await req("PUT", f"/field/projects/{project_a}", "区域甲", expected=403,
            json={"code": first["code"] + "-FORBIDDEN", "name": first["name"],
                  "customer_id": customer, "manager_id": lead_a,
                  "description": first.get("description") or "", "is_active": first["is_active"]})
        view = await req("GET", "/field/directory", "区域甲")
        assert {p["id"] for p in view["projects"]} >= {project_a, project_b}
        assert company_person_id not in {p["id"] for p in view["people"]}
        assert company_person_id in {p["id"] for p in old["people"]}
        overview = await req("GET", "/field/dashboard", "区域甲")
        assert {p["id"] for p in overview["projects"]} >= {project_a, project_b}
        assert next(p for p in view["projects"] if p["id"] == project_a)["can_manage"]
        assert not next(p for p in view["projects"] if p["id"] == project_b)["can_manage"]
        await req("PUT", f"/field/projects/{project_b}/configuration", "区域甲", expected=403,
                  json={"manager_id": lead_a})
        await req("PUT", f"/field/projects/{project_b}/regional-leads", "区域甲", expected=403,
                  json={"primary_lead_id": lead_a})
        await req("GET", "/auth/management-session", "区域甲")
        await req("GET", "/auth/management-session", "售后经理")
        await req("GET", "/auth/management-session", "乙项目组长")
        await req("PUT", f"/attendance/projects/{project_b}/rule", "区域甲", expected=403,
                  json={"name": "禁止改乙"})
        before_b = (await req("GET", f"/attendance/projects/{project_b}/rule", "区域甲"))["id"]
        changed = await req("PUT", f"/attendance/projects/{project_a}/rule", "区域甲",
                            json={"name": "甲项目专用规则"})
        assert changed["id"] != before_b
        assert (await req("GET", f"/attendance/projects/{project_b}/rule", "区域甲"))["id"] == before_b
        await req("PUT", f"/attendance/rules/{before_b}", "区域甲", expected=403,
                  json={"name": "非法全局修改"})
        await req("PUT", f"/field/projects/{project_a}/configuration", "区域甲",
                  json={"manager_id": lead_a})
        await req("POST", f"/field/projects/{project_a}/members", "区域甲",
                  json={"employee_ids": [member]})
        async with async_session() as db:
            db.add(AttendanceRecord(employee_id=member, date=day,
                status=AttendanceStatus.absent, anomaly_type="权限验收用异常"))
            db.add(ShiftSchedule(employee_id=company_person_id, date=day,
                shift_type=ShiftType.day_shift, rule_id=rule))
            await db.commit()
        scope_dates = {"start_date": day.isoformat(), "end_date": day.isoformat()}
        company_keyword = {**scope_dates, "keyword": "非售后员工"}
        for who in ("区域甲", "售后经理"):
            await req("GET", "/attendance/records", who, expected=403,
                params={"employee_id": company_person_id})
            await req("GET", f"/attendance/records/{company_record_id}", who, expected=403)
            assert (await req("GET", "/attendance/records", who,
                params=scope_dates))["total"] == 1
            assert (await req("GET", "/attendance/punch-time-records", who,
                params=company_keyword))["total"] == 0
            assert (await req("GET", "/attendance/daily-report", who,
                params=company_keyword))["total"] == 0
            assert (await req("GET", "/attendance/monthly-report", who,
                params=company_keyword))["total"] == 0
            assert (await req("GET", "/attendance/anomalies", who,
                params=scope_dates))["total"] == 1
            assert (await req("GET", "/attendance/summaries", who,
                params={"year": day.year, "month": day.month}))["total"] == 0
            await req("GET", f"/attendance/summaries/{company_person_id}", who,
                expected=403, params={"year": day.year, "month": day.month})
            export = await client.get("/api/v1/attendance/export-monthly",
                headers={"Authorization": "Bearer " + tokens[who]},
                params={"year": day.year, "month": day.month})
            assert export.status_code == 200, export.text[:500]
            book = openpyxl.load_workbook(io.BytesIO(export.content), read_only=True)
            assert not any(cell == "非售后员工" for row in book.active.values for cell in row)
            book.close()
        assert (await req("GET", "/attendance/records", "admin",
            params={"employee_id": company_person_id}))["total"] == 1
        assert (await req("GET", "/attendance/punch-time-records", "admin",
            params=company_keyword))["total"] == 1
        assert (await req("GET", "/attendance/summaries", "admin",
            params={"year": day.year, "month": day.month}))["total"] == 1
        await req("GET", "/attendance/shifts", "普通现场组员", expected=403,
            params={"employee_id": company_person_id})
        for who in ("区域甲", "售后经理"):
            assert (await req("GET", "/attendance/shifts", who,
                params={"employee_id": company_person_id})) == []
        assert len(await req("GET", "/attendance/shifts", "admin",
            params={"employee_id": company_person_id})) == 1
        admin_task = await req("POST", "/attendance/monthly-report/tasks", "admin",
            json=company_keyword)
        field_task = await req("POST", "/attendance/monthly-report/tasks", "区域甲",
            json=company_keyword)
        assert admin_task["query"]["scope"] == "all"
        assert field_task["query"]["scope"] == "aftersales"
        await req("GET", f"/attendance/monthly-report/tasks/{admin_task['task_id']}",
            "区域甲", expected=403)
        await req("GET", f"/attendance/monthly-report/export?task_id={admin_task['task_id']}",
            "售后经理", expected=403)
        for _ in range(80):
            field_state = await req("GET", f"/attendance/monthly-report/tasks/{field_task['task_id']}",
                "区域甲")
            if field_state["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.05)
        assert field_state["status"] == "succeeded", field_state
        assert (await req("GET", f"/attendance/monthly-report/tasks/{field_task['task_id']}/result",
            "区域甲"))["total"] == 0
        region_records = await req("GET", "/attendance/records", "区域甲",
            params={"employee_id": member, "start_date": day.isoformat()})
        assert region_records["total"] >= 1
        await req("GET", "/attendance/records", "售后经理",
            params={"employee_id": member})
        await req("GET", "/attendance/records", "legacy", expected=403,
            params={"employee_id": member})
        await req("GET", "/attendance/today-stats", "区域甲")
        await req("GET", "/attendance/today-stats", "legacy", expected=403)
        assert (await req("GET", "/attendance/anomalies", "区域甲"))["total"] >= 1
        assert (await req("GET", "/attendance/anomalies", "legacy"))["total"] == 0
        await req("GET", "/attendance/daily-report", "区域甲",
            params={"start_date": day.isoformat(), "end_date": day.isoformat()})
        await req("GET", "/attendance/daily-report", "legacy", expected=403)
        current = await req("GET", "/field/directory", "区域甲")
        assignment = next(a["id"] for a in current["assignments"] if a["employee_id"] == member and a["is_current"])
        unowned = (await req("POST", "/field/projects", json={
            "code": "ROLE-NO-REGION", "name": "缺主责项目", "customer_id": customer,
            "manager_id": unowned_leader}))["id"]
        await req("PUT", f"/field/projects/{unowned}/configuration", json={"rule_id": rule})
        await req("POST", f"/field/assignments/{assignment}/transfer", "售后经理", expected=409,
                  json={"project_id": project_b})
        future = datetime.now(CST).date() + timedelta(days=3)
        await req("POST", f"/field/assignments/{assignment}/transfer-requests", "售后经理", expected=409,
            json={"project_id": unowned, "effective_date": future.isoformat(), "reason": "缺主责不得交接"})
        async with async_session() as db:
            source_shift = ShiftSchedule(employee_id=member, date=future,
                shift_type=ShiftType.day_shift, rule_id=changed["id"])
            db.add(source_shift)
            await db.flush()
            db.add(FieldAssignmentDay(assignment_id=assignment, employee_id=member,
                work_date=future, shift_id=source_shift.id))
            await db.commit()
        handoff = await req("POST", f"/field/assignments/{assignment}/transfer-requests", "区域乙",
                            json={"project_id": project_b, "effective_date": future.isoformat(), "reason": "虚构交接"})
        assert handoff["status"] == "pending" and handoff["incoming_approved_by"] == lead_b
        pending = await req("GET", "/field/transfer-requests", "区域甲")
        assert next(x for x in pending if x["id"] == handoff["id"])["can_review"]
        await req("POST", f"/field/transfer-requests/{handoff['id']}/review", "区域乙", expected=403,
                  json={"approve": True, "note": "不可自己审核"})
        await req("PUT", f"/field/projects/{project_a}/regional-leads", "售后经理",
                  json={"primary_lead_id": lead_b, "assistant_lead_ids": [], "reason": "审核期间调整主责"})
        assert not next(x for x in (await req("GET", "/field/transfer-requests", "区域乙"))
            if x["id"] == handoff["id"])["can_review"]
        assert next(x for x in (await req("GET", "/field/transfer-requests", "区域甲"))
            if x["id"] == handoff["id"])["can_review"]
        resolved = await req("POST", f"/field/transfer-requests/{handoff['id']}/review", "区域甲",
                             json={"approve": True, "note": "原项目交接完成"})
        assert resolved["status"] == "approved"
        await req("PUT", f"/field/projects/{project_a}/regional-leads", "售后经理",
                  json={"primary_lead_id": lead_a, "assistant_lead_ids": [], "reason": "审核后恢复主责"})
        await req("PUT", f"/field/projects/{unowned}/regional-leads", "售后经理",
                  json={"primary_lead_id": lead_a, "assistant_lead_ids": [], "reason": "同区域项目主责"})
        same_member = await req("POST", f"/field/projects/{project_a}/members", "区域甲",
            json={"employee_ids": [ordinary]})
        same_assignment = same_member["members"][0]["id"]
        same_handoff = await req("POST", f"/field/assignments/{same_assignment}/transfer-requests",
            "售后经理", json={"project_id": unowned,
                "effective_date": (future + timedelta(days=1)).isoformat(), "reason": "同区域内部调动"})
        assert same_handoff["review_stage"] == "incoming" and same_handoff["incoming_approved_by"] is None
        first_review = await req("POST", f"/field/transfer-requests/{same_handoff['id']}/review",
            "区域甲", json={"approve": True, "note": "接收项目确认"})
        assert first_review["status"] == "pending" and first_review["review_stage"] == "outgoing"
        final_review = await req("POST", f"/field/transfer-requests/{same_handoff['id']}/review",
            "区域甲", json={"approve": True, "note": "原项目确认"})
        assert final_review["status"] == "approved"
        async with async_session() as db:
            before = await project_on(db, member, future - timedelta(days=1))
            after = await project_on(db, member, future)
            assert before[3].id == project_a and after[3].id == project_b
            assert await db.scalar(select(FieldAssignmentDay.id).where(
                FieldAssignmentDay.assignment_id == assignment,
                FieldAssignmentDay.work_date == future)) is None
            assert await db.scalar(select(ShiftSchedule.id).where(
                ShiftSchedule.employee_id == member, ShiftSchedule.date == future)) is None
        await req("POST", f"/field/projects/{project_b}/members", "售后经理", expected=409,
                  json={"employee_ids": [member]})
    await engine.dispose()
    print("PASS: four field roles, management entry, global read/project write, project-owned rules, leader scope, future two-sided handoff and one-main-project day")


if __name__ == "__main__":
    asyncio.run(main())
