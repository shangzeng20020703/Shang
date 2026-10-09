"""Isolated real-SQLite API acceptance test. Never touches the local demonstration DB."""

import os, sys, tempfile, asyncio, json
from pathlib import Path
from datetime import timedelta, datetime, timezone

sandbox = tempfile.TemporaryDirectory(prefix="aftersales-test-")
os.environ["DATABASE_URL_OVERRIDE"] = "sqlite+aiosqlite:///" + sandbox.name + "/test.db"
os.environ["BACKGROUND_JOBS"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.bootstrap import initialize, seed
from app.main import app
from app.core.database import async_session, engine
from app.models.approval import ApprovalType, ApprovalTask, ApprovalInstance
from app.models.attendance import (
    AttendanceMonthSummary,
    MonthlySummaryStatus,
    AttendanceRecord,
    AttendanceEffect,
)
from app.services.field_service import today
from sqlalchemy import select
from httpx import AsyncClient, ASGITransport

passed = []


async def main():
    await initialize()
    await seed(True)
    await initialize()
    await seed(True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:

        async def req(method, path, who=None, status=200, **kw):
            if who:
                kw["headers"] = {"Authorization": "Bearer " + tokens[who]}
            if path in {"/attendance/clock-in", "/attendance/clock-out"}:
                runtime = await c.get("/api/v1/attendance/my/runtime", headers=kw["headers"])
                kw.setdefault("json", {})["project_context"] = runtime.json().get("project_context")
            r = await c.request(method, "/api/v1" + path, **kw)
            assert r.status_code == status, (method, path, r.status_code, r.text[:1600])
            return r.json() if r.content else None

        tokens = {}
        for i, name in enumerate(["admin", "manager", "staff"], 1):
            token = await req(
                "POST",
                "/auth/management-login" if i == 1 else "/auth/login",
                json={"phone": f"1990000000{i}", "password": "Demo-AfterSales-2026!"},
            )
            tokens[name] = token["access_token"]
        passed.append("three role login and idempotent seed")
        await req("GET", "/field/people", "staff", status=403)
        await req(
            "POST",
            "/field/customers",
            "manager",
            status=403,
            json={"name": "Forbidden"},
        )
        await req("GET", "/field/directory", status=401)
        passed.append("unauthenticated and role restrictions")
        directory = await req("GET", "/field/directory", "admin")
        assignment = directory["assignments"][0]
        site = directory["sites"][0]
        staff_id = assignment["employee_id"]
        profile = await req("GET", "/ess/profile", "staff")
        assert profile["id"] == staff_id
        await req("PUT", "/ess/profile", "staff", json={"email": "demo@example.test"})
        assert (await req("GET", "/ess/profile", "staff"))["email"] == "demo@example.test"
        await req("GET", "/ess/leave/balance", "staff", params={"year": today().year})
        await req("GET", "/ess/leave/requests", "staff")
        await req("GET", "/ess/payslips", "staff", status=404)
        await req("GET", "/ess/profile", status=401)
        passed.append("original mobile self-service profile and leave reads")
        team = await req("GET", "/field/team-attendance", "manager",
                         params={"year": today().year, "month": today().month})
        assert staff_id in {p["id"] for p in team["people"]}
        admin_profile = await req("GET", "/auth/me", "admin")
        assert admin_profile["id"] not in {p["id"] for p in team["people"]}
        assert all(p["summary"] for p in team["people"])
        await req("GET", "/field/team-attendance", "staff", status=403,
                  params={"year": today().year, "month": today().month})
        passed.append("original team cards use scoped summaries and reject employee access")
        await req(
            "POST",
            "/field/assignments",
            "admin",
            status=409,
            json={
                "site_id": site["id"],
                "employee_id": staff_id,
                "start_date": str(today()),
                "end_date": str(today()),
            },
        )
        passed.append("overlapping assignments rejected atomically")
        day = today() + timedelta(days=12)
        new = await req(
            "POST",
            "/field/assignments",
            "manager",
            json={
                "site_id": site["id"],
                "employee_id": staff_id,
                "start_date": str(day),
                "end_date": str(day + timedelta(days=2)),
            },
        )
        await req("POST", f"/field/assignments/{new['id']}/cancel", "manager")
        assert (
            await req("GET", "/field/dashboard", "manager", params={"day": str(day)})
        )["total_scheduled"] == 0
        passed.append("manager creates and cancels future shifts with FK integrity")
        for endpoint in ["clock-in", "clock-out"]:
            await req(
                "POST", "/attendance/" + endpoint, "staff", json={"source": "app"}
            )
        stats = await req("GET", "/field/dashboard", "manager")
        assert (
            stats["clocked_in"] == 1
            and stats["clocked_out"] == 1
            and stats["continuous_presence_enabled"] is False
        ), stats
        passed.append("clock in/out reflected in project-scoped overview")
        # Multiple anomalies must stay visible in the restored team cards.
        from app.models.attendance import AttendanceStatus
        from app.services.attendance import MonthSummaryService
        from app.schemas.attendance import MonthSummaryGenerateRequest
        from app.services.field_service import dashboard
        from app.models.employee import Employee
        async with async_session() as db:
            savepoint = await db.begin_nested()
            rec = await db.scalar(select(AttendanceRecord).where(
                AttendanceRecord.employee_id == staff_id, AttendanceRecord.date == today()
            ))
            rec.status = AttendanceStatus.missed_clock
            rec.anomaly_type = "缺少下班打卡；迟到5分钟"
            await db.flush()
            await MonthSummaryService.generate(db, MonthSummaryGenerateRequest(
                year=today().year, month=today().month, employee_ids=[staff_id]
            ))
            summary = await MonthSummaryService.get_summary(db, staff_id, today().year, today().month)
            assert summary.late_count == 1 and summary.missed_clock_count == 1
            manager_user = await db.scalar(select(Employee).where(Employee.phone == "19900000002"))
            assert (await dashboard(db, manager_user, today()))["late_count"] == 1
            await savepoint.rollback()
        passed.append("combined missed-punch and lateness remain visible in team statistics")
        async with async_session() as db:
            template = await db.scalar(
                select(ApprovalType).where(
                    ApprovalType.business_code == "field_daily_report"
                )
            )
            template_id = template.id
        await req(
            "PUT",
            f"/field/template-drafts/{template_id}",
            "admin",
            json={"template": {"id": template_id}, "fields": []},
        )
        assert (await req("GET", f"/field/template-drafts/{template_id}", "admin"))[
            "data"
        ]["template"]["id"] == template_id
        await req("GET", f"/field/template-drafts/{template_id}", "staff", status=403)
        await req("DELETE", f"/field/template-drafts/{template_id}", "admin")
        passed.append("template draft persistence and role protection")
        payload = {
            "approval_type_id": template_id,
            "form_data": {
                "work_date": str(today()),
                "project_name": "测试项目",
                "work_summary": "完成陪产检查",
            },
        }
        application = await req(
            "POST", "/approval/applications", "staff", status=201, json=payload
        )
        instance_id = application["id"]
        await req(
            "POST",
            f"/approval/instances/{instance_id}/process",
            "staff",
            status=403,
            json={"action": "approve", "comment": "不得自审"},
        )
        await req(
            "POST",
            f"/approval/instances/{instance_id}/process",
            "manager",
            json={"action": "approve", "comment": "确认"},
        )
        detail = await req("GET", f"/approval/instances/{instance_id}", "staff")
        assert detail["status"] == "approved", detail
        await req(
            "POST",
            f"/approval/instances/{instance_id}/process",
            "manager",
            status=400,
            json={"action": "approve"},
        )
        passed.append(
            "dynamic form, manager approval, self-approval rejection, terminal replay rejection"
        )
        withdrawn = await req(
            "POST", "/approval/applications", "staff", status=201, json=payload
        )
        await req("POST", f"/approval/instances/{withdrawn['id']}/withdraw", "staff")
        async with async_session() as db:
            tasks = (
                await db.scalars(
                    select(ApprovalTask).where(
                        ApprovalTask.instance_id == withdrawn["id"]
                    )
                )
            ).all()
            assert tasks and all(t.status != "pending" for t in tasks)
        passed.append("withdrawal removes pending tasks")
        # Leave approval must produce an attendance effect, not just an approved label.
        async with async_session() as db:
            leave_id = await db.scalar(
                select(ApprovalType.id).where(ApprovalType.business_code == "leave")
            )
        leave_day = today() + timedelta(days=1)
        leave_payload = {
            "approval_type_id": leave_id,
            "form_data": {
                "leave_type": "事假",
                "start_time": f"{leave_day}T08:00:00+08:00",
                "end_time": f"{leave_day}T17:00:00+08:00",
                "duration": 8,
                "reason": "验收请假",
            },
        }
        leave = await req(
            "POST", "/approval/applications", "staff", status=201, json=leave_payload
        )
        await req(
            "POST",
            f"/approval/instances/{leave['id']}/process",
            "manager",
            json={"action": "approve"},
        )
        async with async_session() as db:
            effects = (
                await db.scalars(
                    select(AttendanceEffect).where(
                        AttendanceEffect.source_instance_id == leave["id"]
                    )
                )
            ).all()
            assert effects and all(
                e.employee_id == staff_id and e.effect_type == "leave" for e in effects
            )
        passed.append("approved leave writes an attendance effect for the applicant")
        malicious = {
            **leave_payload,
            "form_data": {**leave_payload["form_data"], "employee_id": 999},
        }
        await req("POST", "/approval/applications", "staff", status=403, json=malicious)
        passed.append("attendance form cannot impersonate another employee")
        # A missing punch is eligible only under configured correction policy.
        from app.models.attendance import AttendanceRule, ShiftSchedule, ShiftType, DayType
        from app.services.attendance import WorkCalendarService
        from datetime import time

        # The previous calendar day can be a weekend or public holiday.
        async with async_session() as db:
            target = None
            for offset in range(1, 31):
                candidate = today() - timedelta(days=offset)
                if await WorkCalendarService.get_day_type(db, candidate) in (DayType.workday, DayType.special_workday):
                    target = candidate
                    break
        assert target is not None, "No eligible past workday in the correction window"
        async with async_session() as db:
            rule = await db.get(AttendanceRule, site["rule_id"])
            rule.extra_config = {
                **rule.extra_config,
                "enable_patch_apply": True,
                "patch_types": ["缺卡/旷工", "迟到", "早退"],
                "patch_month_limit": "3次",
                "patch_time_limit": "过去30天内",
                "patch_deadline": "不设置",
            }
            db.add(
                ShiftSchedule(
                    employee_id=staff_id,
                    date=target,
                    rule_id=rule.id,
                    shift_type=ShiftType.day_shift,
                    start_time=time(8),
                    end_time=time(17),
                )
            )
            correction_id = await db.scalar(
                select(ApprovalType.id).where(
                    ApprovalType.business_code == "punch_correction"
                )
            )
            await db.commit()
        correction_payload = {
            "approval_type_id": correction_id,
            "form_data": {
                "correction_date": str(target),
                "punch_type": "clock_in",
                "punch_time": f"{target}T08:00:00+08:00",
                "reason": "漏打卡",
            },
        }
        correction = await req(
            "POST",
            "/approval/applications",
            "staff",
            status=201,
            json=correction_payload,
        )
        await req(
            "POST",
            "/approval/applications",
            "staff",
            status=409,
            json=correction_payload,
        )
        await req(
            "POST",
            f"/approval/instances/{correction['id']}/process",
            "manager",
            json={"action": "approve"},
        )
        async with async_session() as db:
            record = await db.scalar(
                select(AttendanceRecord).where(
                    AttendanceRecord.employee_id == staff_id,
                    AttendanceRecord.date == target,
                )
            )
            assert record and record.clock_in_time is not None
        passed.append(
            "correction eligibility, duplicate prevention and approved punch writeback"
        )
        from app.services.field_jobs import process_recalculation
        from app.models.attendance import AttendanceRecalcTask

        await process_recalculation()
        async with async_session() as db:
            tasks = (await db.scalars(select(AttendanceRecalcTask))).all()
            assert tasks and all(t.status == "completed" for t in tasks), [
                (t.status, t.last_error) for t in tasks
            ]
        passed.append("event-generated recalculation queue reaches completed state")
        # Files are private even with a guessed path.
        file = await req(
            "POST",
            "/upload",
            "staff",
            files={"file": ("proof.txt", b"test proof", "text/plain")},
            params={"category": "approval"},
        )
        r = await c.get(file["url"])
        assert r.status_code == 401
        r = await c.get(
            file["url"], headers={"Authorization": "Bearer " + tokens["staff"]}
        )
        assert r.status_code == 200
        r = await c.get(
            file["url"], headers={"Authorization": "Bearer " + tokens["manager"]}
        )
        assert r.status_code == 403
        passed.append("file owner authorization, no anonymous or unrelated reader")
        # Monthly lock protects underlying records even for an administrative write.
        async with async_session() as db:
            summary = await db.scalar(
                select(AttendanceMonthSummary).where(
                    AttendanceMonthSummary.employee_id == staff_id,
                    AttendanceMonthSummary.year == today().year,
                    AttendanceMonthSummary.month == today().month,
                )
            )
            if summary is None:
                summary = AttendanceMonthSummary(
                    employee_id=staff_id, year=today().year, month=today().month
                )
                db.add(summary)
            summary.status = MonthlySummaryStatus.locked
            await db.commit()
        await req(
            "POST", "/attendance/clock-out", "staff", status=400, json={"source": "app"}
        )
        await req("GET", "/attendance/records", "admin")
        passed.append("locked month blocks mutation while allowing history reads")
        assert (await req("GET", "/notifications", "staff"))["total"] > 0
        passed.append("notification persistence")
        logs = await req("GET", "/audit-logs?module=field&resource_types=save_site", "admin")
        assert logs and all(row["resource_type"] == "save_site" for row in logs)
        await req("GET", "/audit-logs", "staff", status=403)
        passed.append("audit business-object filter and administrator-only access")
        chain = []
        for level in range(1, 5):
            chain.append(await req("POST", "/departments", "admin", status=201,
                                   json={"name": f"组织验收{level}", "parent_id": chain[-1]["id"] if chain else None}))
        await req("POST", "/departments", "admin", status=400,
                  json={"name": "超出四级", "parent_id": chain[-1]["id"]})
        branch = await req("POST", "/departments", "admin", status=201, json={"name": "调动测试部门"})
        await req("POST", "/departments", "admin", status=201, json={"name": "随调子部门", "parent_id": branch["id"]})
        await req("PUT", f"/departments/{branch['id']}", "admin", status=400, json={"parent_id": chain[2]["id"]})
        assert (await req("GET", f"/departments/{branch['id']}", "admin"))["parent_id"] is None
        await req("PUT", f"/departments/{chain[0]['id']}", "admin", status=400, json={"parent_id": chain[-1]["id"]})
        position = {"department_id": chain[-1]["id"], "name": "现场工程师"}
        first = await req("POST", "/field/positions", "admin", status=201, json=position)
        assert (await req("POST", "/field/positions", "admin", status=201, json=position))["id"] == first["id"]
        await req("POST", "/field/positions", "staff", status=403, json=position)
        await req("GET", "/field/positions", "staff", status=403)
        await req("POST", "/field/positions", "admin", status=422, json={**position, "name": "   "})
        await req("POST", "/field/positions", "admin", status=201, json={**position, "department_id": chain[0]["id"]})
        person = {"name": "岗位联动测试", "phone": "13888889991", "position": position["name"], "department_id": position["department_id"]}
        saved = await req("POST", "/field/people", "admin", json=person)
        catalog = await req("GET", "/field/positions", "admin")
        selected = next(p for p in catalog if p["department_id"] == position["department_id"] and p["name"] == position["name"])
        assert selected["member_count"] == 1 and selected["members"][0]["id"] == saved["id"]
        roster_person = next(p for p in await req("GET", "/field/people", "admin") if p["id"] == saved["id"])
        assert roster_person["department_name"] == chain[-1]["name"]
        await req("PUT", f"/field/people/{saved['id']}", "admin", json={**person, "status": "离职"})
        catalog = await req("GET", "/field/positions", "admin")
        assert next(p for p in catalog if p["department_id"] == position["department_id"] and p["name"] == position["name"])["member_count"] == 0
        passed.append("four-level department create/move guards and department-local positions linked to roster")
    await engine.dispose()
    result = {
        "passed": len(passed),
        "cases": passed,
        "database": "temporary isolated SQLite",
        "continuous_presence": "deferred",
    }
    Path("evidence/integration-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


asyncio.run(main())
