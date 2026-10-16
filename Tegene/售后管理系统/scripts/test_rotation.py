"""Isolated API acceptance for personal rotations and the half-day ledger."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sandbox = tempfile.TemporaryDirectory(prefix="aftersales-rotation-")
os.environ.update(DATABASE_URL_OVERRIDE=f"sqlite+aiosqlite:///{sandbox.name}/test.db",
                  BACKGROUND_JOBS="false", ENVIRONMENT="development", LOGIN_MODE="password")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func
from app.bootstrap import initialize, seed
from app.core.database import async_session, engine
from app.main import app
from app.models.employee import Employee
from app.models.attendance import AttendanceRecord, AttendanceRule, AttendanceEffect, AttendanceMonthSummary, MonthlySummaryStatus
from app.models.field_service import FieldAttendanceSegment, FieldProject
from app.models.rotation import RotationDay, RotationClaim, RotationLedger, RotationAccount
from app.models.audit_log import AuditLog
from app.services import rotation, field_membership
from app.services.attendance import AttendanceRecordService as attendance, MonthSummaryService


async def main():
    await initialize()
    await seed(True)
    await initialize()  # Additive bootstrap is repeatable.
    app.state.limiter.enabled = False
    tokens = {}
    day = datetime.now(rotation.CST).date()
    moment = datetime.combine(day, time(8), rotation.CST).astimezone(timezone.utc)
    simulated_today = day
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def req(method, path, who="admin", status=200, **kw):
            response = await client.request(method, "/api/v1" + path,
                headers={"Authorization": "Bearer " + tokens[who]} if who in tokens else {}, **kw)
            assert response.status_code == status, (method, path, response.status_code, response.text[:1800])
            return response.json()

        for i, who in [(1, "admin"), (2, "manager"), (3, "staff")]:
            tokens[who] = (await req("POST", "/auth/login", who, json={"phone": f"1990000000{i}", "password": "Demo-AfterSales-2026!"}))["access_token"]
        directory = await req("GET", "/field/directory")
        base = directory["projects"][0]
        person = await req("POST", "/field/people", json={"name":"轮休验收工程师", "phone":"19911112222", "employee_no":"ROT-QA", "role":"employee"})
        eid = person["id"]
        tokens["engineer"] = (await req("POST", "/auth/login", "engineer", json={"phone":"19911112222", "password":"585858"}))["access_token"]
        rule = await req("POST", "/attendance/rules", status=201, json={"name":"轮休验收12小时", "work_hour_type":"flexible", "work_hours_per_day":12,
            "effective_date":str(day), "require_gps":False, "require_photo":False, "extra_config":{"scope_mode":"project", "rule_type":"rotation", "free_work_hours_mode":"limit:12", "free_day_start_time":"05:00"}})
        project = await req("POST", "/field/projects", json={"code":"ROT-QA", "name":"轮休验收项目", "customer_id":base["customer_id"], "manager_id":base["manager_id"]})
        pid = project["id"]
        await req("PUT", f"/field/projects/{pid}/configuration", json={"rule_id":rule["id"]})
        with patch.object(field_membership, "clock_now", side_effect=lambda: moment), patch.object(rotation, "today", side_effect=lambda: simulated_today):
            await req("POST", f"/field/projects/{pid}/members", "manager", json={"employee_ids":[eid]})
            runtime = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime["rotation"]["kind"] == "pending"
            async with async_session() as db:
                assert not await MonthSummaryService._expected_work_day(db, eid, day, None)
            body = {"employee_ids":[eid], "start_date":str(day), "end_date":str(day+timedelta(days=27)), "cycle_start":str(day)}
            await req("POST", f"/rotation/projects/{pid}/preview", "staff", status=403, json=body)
            await req("POST", f"/rotation/projects/{base['id']}/preview", "engineer", status=403, json=body)
            preview = await req("POST", f"/rotation/projects/{pid}/preview", "manager", json=body)
            assert [i for i, row in enumerate(preview["rows"][:14]) if row["original_kind"] == "rest"] == [6,13]
            async with async_session() as db:
                assert await db.scalar(select(func.count(RotationDay.id))) == 0
            await req("POST", f"/rotation/projects/{pid}/publish?revision=stale", "manager", status=409, json=body)
            await req("POST", f"/rotation/projects/{pid}/publish?revision={preview['revision']}", "manager", json=body)
            await req("POST", f"/rotation/projects/{pid}/publish?revision={preview['revision']}", "manager", status=409, json=body)
            runtime = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime["rotation"]["kind"] == "work" and float(runtime["rotation"]["required_hours"]) == 12
            assert runtime["check"]["check_out_required"]
            async with async_session() as db:
                assert await MonthSummaryService._expected_work_day(db, eid, day, None)
                assert not await MonthSummaryService._expected_work_day(db, eid, day+timedelta(days=6), None)
            # Actual clock endpoints: six hours is abnormal on an ordinary 12-hour work day.
            await req("POST", "/attendance/clock-in", "engineer", json={"project_context":runtime["project_context"]})
            moment += timedelta(hours=6)
            runtime = await req("GET", "/attendance/my/runtime", "engineer")
            await req("POST", "/attendance/clock-out", "engineer", json={"project_context":runtime["project_context"]})
            async with async_session() as db:
                rec = await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id==eid, AttendanceRecord.date==day))
                assert rec.work_hours == 6 and "工时不足" in (rec.anomaly_type or "")
            # Two original rest dates, complete real check-in/out, then approve credits.
            credits = []
            for offset, hours, expected in [(6,12,1), (13,12,1), (20,6,0.5)]:
                work_day = day+timedelta(days=offset)
                moment = datetime.combine(work_day, time(8), rotation.CST).astimezone(timezone.utc)
                simulated_today = work_day
                runtime = await req("GET", "/attendance/my/runtime", "engineer")
                assert runtime["rotation"]["kind"] == "rest"
                await req("POST", "/attendance/clock-in", "engineer", json={"project_context":runtime["project_context"]})
                moment += timedelta(hours=hours)
                runtime = await req("GET", "/attendance/my/runtime", "engineer")
                await req("POST", "/attendance/clock-out", "engineer", json={"project_context":runtime["project_context"]})
                request = {"project_id":pid,"kind":"credit","days":[{"date":str(work_day),"kind":"work"}],"reason":"现场陪产占用轮休"}
                c = await req("POST", "/rotation/claims", "engineer", json=request)
                assert c["current_facts"]["suggested_days"] == expected and c["current_facts"]["complete"]
                await req("POST", "/rotation/claims", "engineer", status=409, json=request)
                await req("POST", f"/rotation/claims/{c['id']}/review", "engineer", status=403, json={"action":"approve","note":"越权"})
                review = {"action":"approve","days":expected,"note":"负责人核对工时后确认"}
                approved = await req("POST", f"/rotation/claims/{c['id']}/review", "manager", json=review)
                assert approved["days_amount"] == expected
                await req("POST", f"/rotation/claims/{c['id']}/review", "manager", json=review)
                credits.append(c["id"])
            query = f"/rotation/calendar?start={day}&end={day+timedelta(days=40)}&project_id={pid}"
            data = await req("GET", query, "manager")
            assert data["balances"][0]["days"] == 2.5 and len(data["ledger"]) == 3
            await req("GET", query+f"&employee_id={eid}", "staff", status=403)
            await req("GET", f"/rotation/calendar?start={day}&end={day}&employee_id={eid}", "staff", status=403)
            # Credits are frozen; changed punch facts expose a review warning.
            async with async_session() as db:
                s = await db.scalar(select(FieldAttendanceSegment).where(FieldAttendanceSegment.employee_id==eid,FieldAttendanceSegment.work_date==day+timedelta(days=20)))
                original = dict(s.record_data)
                s.record_data = {**s.record_data, "clock_out_time":(moment-timedelta(hours=1)).isoformat()}
                await db.commit()
            changed = await req("GET", query, "manager")
            assert next(c for c in changed["claims"] if c["id"]==credits[-1])["needs_review"]
            async with async_session() as db:
                s = await db.scalar(select(FieldAttendanceSegment).where(FieldAttendanceSegment.employee_id==eid,FieldAttendanceSegment.work_date==day+timedelta(days=20)))
                s.record_data=original;await db.commit()
            # Three days exceeds 2.5; transaction must leave every day unchanged.
            leave_days = [{"date":str(day+timedelta(days=n)),"kind":"leave"} for n in (21,22,23)]
            leave = await req("POST", "/rotation/claims", "engineer", json={"project_id":pid,"kind":"leave","days":leave_days,"reason":"申请集中调休"})
            await req("POST", f"/rotation/claims/{leave['id']}/review", "manager", status=409, json={"action":"approve","note":"额度不足"})
            data=await req("GET",query,"manager")
            assert all(d["kind"]=="work" for d in data["days"] if d["date"] in {v["date"] for v in leave_days})
            await req("POST", f"/rotation/claims/{leave['id']}/review", "manager", json={"action":"reject","note":"余额不足，重新申请"})
            used = await req("POST", "/rotation/claims", "engineer", json={"project_id":pid,"kind":"leave","days":[*leave_days[:2],{"date":leave_days[2]["date"],"kind":"half_leave"}],"reason":"两天半调休"})
            await req("POST", f"/rotation/claims/{used['id']}/review", "manager", json={"action":"approve","note":"同意调休"})
            data = await req("GET", query, "manager")
            assert data["balances"][0]["days"] == 0
            async with async_session() as db:
                person=await db.get(Employee,eid)
                rt=await attendance._get_rule_runtime(db,eid,day+timedelta(days=23),person)
                assert rt["rotation"]["kind"]=="half_leave" and attendance._free_required_hours(rt["rule"],rt["extra"])==6
                assert not await MonthSummaryService._expected_work_day(db,eid,day+timedelta(days=21),None)
            await req("POST", f"/rotation/claims/{credits[0]}/review", "manager", status=409, json={"action":"revoke","note":"不能删除已用额度"})
            await req("POST", f"/rotation/claims/{used['id']}/review", "manager", json={"action":"revoke","note":"现场取消调休，退回额度"})
            # Normal rest never consumes balance. Adjusting a rest day preserves origin.
            rest_day=day+timedelta(days=27)
            await req("POST", f"/rotation/projects/{pid}/adjust", "manager", json={"employee_ids":[eid],"days":[{"date":str(rest_day),"kind":"work"}],"reason":"临时继续陪产"})
            data = await req("GET", query, "manager")
            r=next(d for d in data["days"] if d["date"]==str(rest_day))
            assert r["original_kind"]=="rest" and r["kind"]=="work" and data["balances"][0]["days"]==2.5
            await req("POST", f"/rotation/projects/{pid}/adjust", "manager", status=409, json={"employee_ids":[eid],"days":[{"date":str(rest_day),"kind":"rest","expected_revision":1}],"reason":"过期页面"})
            schedule=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"schedule","days":[{"date":str(rest_day),"kind":"rest"}],"reason":"申请恢复轮休"})
            await req("POST",f"/rotation/claims/{schedule['id']}/review","manager",json={"action":"approve","note":"同意恢复"})
            moment=datetime.combine(rest_day,time(8),rotation.CST).astimezone(timezone.utc)
            simulated_today=rest_day
            rt=await req("GET","/attendance/my/runtime","engineer")
            await req("POST","/attendance/clock-in","engineer",json={"project_context":rt["project_context"]})
            short=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"credit","days":[{"date":str(rest_day),"kind":"work"}],"reason":"临时陪产两小时"})
            await req("POST",f"/rotation/claims/{short['id']}/review","manager",status=409,json={"action":"approve","days":0.5,"note":"缺卡不能确认"})
            moment+=timedelta(hours=2)
            rt=await req("GET","/attendance/my/runtime","engineer")
            await req("POST","/attendance/clock-out","engineer",json={"project_context":rt["project_context"]})
            await req("POST",f"/rotation/claims/{short['id']}/review","manager",status=422,json={"action":"approve","days":1,"note":"不足工时不能记全天"})
            await req("POST",f"/rotation/claims/{short['id']}/review","manager",json={"action":"approve","days":0.5,"note":"负责人确认临时出勤，按半天安排补休"})
            # Cross-midnight clocks use the preceding 05:00-boundary work date.
            night_day=day+timedelta(days=24)
            moment=datetime.combine(night_day,time(20),rotation.CST).astimezone(timezone.utc)
            rt=await req("GET","/attendance/my/runtime","engineer")
            await req("POST","/attendance/clock-in","engineer",json={"project_context":rt["project_context"]})
            moment+=timedelta(hours=7)
            night=await req("GET","/attendance/my/runtime","engineer")
            assert night["date"]==str(night_day)
            await req("POST","/attendance/clock-out","engineer",json={"project_context":night["project_context"]})
            # Unpublished days keep the punch facts and a pending display, never a normal verdict.
            moment=datetime.combine(day+timedelta(days=29),time(8),rotation.CST).astimezone(timezone.utc)
            rt=await req("GET","/attendance/my/runtime","engineer")
            assert rt["rotation"]["kind"]=="pending"
            await req("POST","/attendance/clock-in","engineer",json={"project_context":rt["project_context"]})
            moment+=timedelta(hours=2)
            rt=await req("GET","/attendance/my/runtime","engineer")
            await req("POST","/attendance/clock-out","engineer",json={"project_context":rt["project_context"]})
            records=await req("GET",f"/attendance/records?employee_id={eid}","engineer")
            assert next(r for r in records["items"] if r["date"]==str(day+timedelta(days=29)))["display_status"]=="待确认"
            confirm=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"schedule","days":[{"date":str(day+timedelta(days=29)),"kind":"work"}],"reason":"申请事后确认未排班日期的出勤"})
            await req("POST",f"/rotation/claims/{confirm['id']}/review","manager",json={"action":"approve","note":"核对现场后确认当天上班"})
            half=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"leave","days":[{"date":str(day),"kind":"half_leave"}],"reason":"当天六小时出勤，另半天申请调休"})
            await req("POST",f"/rotation/claims/{half['id']}/review","manager",json={"action":"approve","note":"确认半天调休"})
            from app.services.field_jobs import process_recalculation
            await process_recalculation()
            async with async_session() as db:
                rec=await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id==eid,AttendanceRecord.date==day))
                assert not rec.anomaly_type, rec.anomaly_type
            await req("POST",f"/rotation/claims/{half['id']}/review","manager",json={"action":"revoke","note":"验收后退回额度"})
            await process_recalculation()
            # Legacy conversion cannot double count the same credited date.
            async with async_session() as db:
                try:
                    await rotation.ensure_overtime_not_credited(db,eid,day+timedelta(days=6),"comp_time")
                    raise AssertionError("duplicate overtime accepted")
                except Exception as exc:
                    assert getattr(exc,"status_code",None)==409
                row=await rotation.day_row(db,eid,day+timedelta(days=6),pid)
                runtime=await rotation.apply_runtime(db,{},eid,row.work_date,pid)
                assert attendance._split_overtime_settlement({**runtime["extra"],"overtime_policy":{"workday":{"convert_mode":"comp_time"}}},None,12)[0]==0
            # Owner scope is checked on write and view, even for another manager.
            other=await req("POST","/field/projects",json={"code":"ROT-OTHER","name":"其他项目","customer_id":base["customer_id"],"manager_id":1})
            await req("POST",f"/rotation/projects/{other['id']}/preview","manager",status=403,json=body)
            # Two simultaneous spending approvals cannot overdraw one account.
            spend=[]
            for offsets in [(1,2),(3,4)]:
                c=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"leave","days":[{"date":str(day+timedelta(days=n)),"kind":"leave"} for n in offsets],"reason":"并发额度验收"})
                spend.append(c["id"])
            async def approve_raw(cid):
                return await client.post(f"/api/v1/rotation/claims/{cid}/review",headers={"Authorization":"Bearer "+tokens["manager"]},json={"action":"approve","note":"并发审批验收"})
            responses=await asyncio.gather(*(approve_raw(cid) for cid in spend))
            assert sorted(r.status_code for r in responses)==[200,409], [(r.status_code,r.text) for r in responses]
            winning=next(r.json()["id"] for r in responses if r.status_code==200)
            await req("POST",f"/rotation/claims/{winning}/review","manager",json={"action":"revoke","note":"验收完成退回额度"})
            # A transfer must not strand the employee behind the old project's future calendar.
            future={"employee_ids":[eid],"start_date":str(day+timedelta(days=30)),"end_date":str(day+timedelta(days=33)),"cycle_start":str(day)}
            future_preview=await req("POST",f"/rotation/projects/{pid}/preview","manager",json=future)
            await req("POST",f"/rotation/projects/{pid}/publish?revision={future_preview['revision']}","manager",json=future)
            target=await req("POST","/field/projects",json={"code":"ROT-NEXT","name":"调动后轮休项目","customer_id":base["customer_id"],"manager_id":base["manager_id"]})
            await req("PUT",f"/field/projects/{target['id']}/configuration",json={"rule_id":rule["id"]})
            current_directory=await req("GET","/field/directory")
            assignment=next(a for a in current_directory["assignments"] if a["employee_id"]==eid and a["is_current"])
            await req("POST",f"/field/assignments/{assignment['id']}/transfer","manager",json={"project_id":target["id"]})
            next_preview=await req("POST",f"/rotation/projects/{target['id']}/preview","manager",json=future)
            await req("POST",f"/rotation/projects/{target['id']}/publish?revision={next_preview['revision']}","manager",json=future)
            async with async_session() as db:
                transferred=await rotation.day_row(db,eid,day+timedelta(days=30))
                assert transferred.project_id==target['id']
                historical=await rotation.day_row(db,eid,day)
                assert historical.project_id==pid
            async with async_session() as db:
                summary=await db.scalar(select(AttendanceMonthSummary).where(AttendanceMonthSummary.employee_id==eid,AttendanceMonthSummary.year==rest_day.year,AttendanceMonthSummary.month==rest_day.month))
                if summary:
                    summary.status=MonthlySummaryStatus.locked
                else:
                    db.add(AttendanceMonthSummary(employee_id=eid,year=rest_day.year,month=rest_day.month,status=MonthlySummaryStatus.locked))
                await db.commit()
            await req("POST",f"/rotation/projects/{pid}/adjust","manager",status=409,json={"employee_ids":[eid],"days":[{"date":str(rest_day),"kind":"work"}],"reason":"锁定月不能更改"})
            # Process-restart persistence and audit evidence.
            await engine.dispose()
            async with async_session() as db:
                assert (await db.get(RotationAccount,eid)).units==6
                assert await db.scalar(select(func.count(RotationLedger.id)))==10
                assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.resource_type=="rotation"))>20
    print("PASS: rotation preview/publish, real clocks, 14-day credits, half-days, pending dates, scoped permissions, atomic spending, reversals, adjustments, overnight dates, duplicate conversion, persistence and audit")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
