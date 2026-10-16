"""Isolated API acceptance for personal rotations and the half-day ledger."""
import asyncio
import json
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
from app.models.employee_role import EmployeeRole
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

        async def approve_schedule(claim_id, *, submitter_is_leader=False):
            path = f"/rotation/claims/{claim_id}/review"
            if not submitter_is_leader:
                first = await req("POST", path, "manager", json={"action":"approve","note":"项目组长核对"})
                assert first["status"] == "pending" and first["next_approval"]["role"] == "regional_lead"
            second = await req("POST", path, "staff", json={"action":"approve","note":"区域组长核对"})
            assert second["status"] == "pending" and second["next_approval"]["role"] == "aftersales_manager"
            final = await req("POST", path, "aftersales", json={"action":"approve","note":"售后经理终审"})
            assert final["status"] == "approved"
            return final

        for i, who in [(1, "admin"), (2, "manager"), (3, "staff")]:
            tokens[who] = (await req("POST", "/auth/login", who, json={"phone": f"1990000000{i}", "password": "Demo-AfterSales-2026!"}))["access_token"]
        directory = await req("GET", "/field/directory")
        base = directory["projects"][0]
        person = await req("POST", "/field/people", json={"name":"轮休验收工程师", "phone":"19911112222", "employee_no":"ROT-QA", "role":"employee"})
        eid = person["id"]
        tokens["engineer"] = (await req("POST", "/auth/login", "engineer", json={"phone":"19911112222", "password":"585858"}))["access_token"]
        rule = await req("POST", "/attendance/rules", status=201, json={"name":"轮休验收12小时", "work_hour_type":"flexible", "work_hours_per_day":12,
            "effective_date":str(day), "require_gps":False, "require_photo":False, "extra_config":{"scope_mode":"project", "rule_type":"rotation", "free_work_hours_mode":"limit:12", "free_day_start_time":"05:00"}})
        project = await req("POST", "/field/projects", json={"code":"ROT-QA", "name":"轮休验收项目", "customer_id":base["customer_id"], "manager_id":2})
        pid = project["id"]
        async with async_session() as db:
            db.add(EmployeeRole(employee_id=3, role_name="regional_lead", granted_by=1))
            await db.commit()
        await req("PUT", f"/field/projects/{pid}/regional-leads", json={"primary_lead_id":3,"assistant_lead_ids":[],"reason":"独立测试库角色分配"})
        sales=await req("POST", "/field/people", json={"name":"售后经理验收","phone":"19911114444","employee_no":"ROT-SALES","role":"aftersales_manager"})
        tokens["aftersales"]=(await req("POST", "/auth/login", "aftersales", json={"phone":"19911114444","password":"585858"}))["access_token"]
        sales2=await req("POST", "/field/people", json={"name":"第二售后经理","phone":"19911116666","employee_no":"ROT-SALES-2","role":"aftersales_manager"})
        tokens["aftersales2"]=(await req("POST", "/auth/login", "aftersales2", json={"phone":"19911116666","password":"585858"}))["access_token"]
        backup=await req("POST", "/field/people", json={"name":"协管区域组长","phone":"19911117777","employee_no":"ROT-REGION-BACKUP","role":"regional_lead"})
        tokens["backup"]=(await req("POST", "/auth/login", "backup", json={"phone":"19911117777","password":"585858"}))["access_token"]
        await req("PUT", f"/field/projects/{pid}/configuration", json={"rule_id":rule["id"]})
        with patch.object(field_membership, "clock_now", side_effect=lambda: moment), patch.object(rotation, "today", side_effect=lambda: simulated_today):
            await req("POST", f"/field/projects/{pid}/members", "staff", json={"employee_ids":[eid]})
            runtime = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime["rotation"]["kind"] == "pending"
            async with async_session() as db:
                assert not await MonthSummaryService._expected_work_day(db, eid, day, None)
            body = {"employee_ids":[eid], "start_date":str(day), "end_date":str(day+timedelta(days=27)), "cycle_start":str(day),
                "edits":[{"date":str(day),"kind":"work","shift_label":"night"}]}
            await req("POST", f"/rotation/projects/{pid}/preview", "engineer", status=403, json=body)
            await req("POST", f"/rotation/projects/{base['id']}/preview", "engineer", status=403, json=body)
            preview = await req("POST", f"/rotation/projects/{pid}/preview", "manager", json=body)
            assert [i for i, row in enumerate(preview["rows"][:14]) if row["original_kind"] == "rest"] == [6,13]
            assert preview["rows"][0]["shift_label"] == "night"
            async with async_session() as db:
                assert await db.scalar(select(func.count(RotationDay.id))) == 0
            await req("POST", f"/rotation/projects/{pid}/publish?revision=stale", "manager", status=409, json=body)
            await req("POST", f"/rotation/projects/{pid}/publish?revision={preview['revision']}", "manager", json=body)
            await req("POST", f"/rotation/projects/{pid}/publish?revision={preview['revision']}", "manager", status=409, json=body)
            await req("POST", "/rotation/claims", "staff", status=422, json={"project_id":pid,
                "employee_id":eid,"kind":"schedule",
                "days":[{"date":str(day+timedelta(days=2)),"kind":"rest"}],
                "reason":"唯一的区域组长不能代提交后自审"})
            await req("PUT", f"/field/projects/{pid}/regional-leads", json={
                "primary_lead_id":3,"assistant_lead_ids":[backup["id"]],
                "reason":"为代提交排班配置独立区域审核人"})
            runtime = await req("GET", "/attendance/my/runtime", "engineer")
            assert runtime["rotation"]["kind"] == "work" and float(runtime["rotation"]["required_hours"]) == 12
            assert runtime["rotation"]["shift_label"] == "night"
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
                await req("POST", f"/rotation/claims/{c['id']}/review", "manager", status=403, json={"action":"approve","note":"组长不得审批攒休"})
                await req("POST", f"/rotation/claims/{c['id']}/review", "staff", status=403, json={"action":"approve","note":"区域组长不得审批攒休"})
                review = {"action":"approve","days":expected,"note":"负责人核对工时后确认"}
                approver = "admin" if offset == 6 else "aftersales"
                approved = await req("POST", f"/rotation/claims/{c['id']}/review", approver, json=review)
                assert approved["days_amount"] == expected
                await req("POST", f"/rotation/claims/{c['id']}/review", approver, json=review)
                await req("POST", f"/rotation/claims/{c['id']}/review", "engineer", status=403, json=review)
                credits.append(c["id"])
            query = f"/rotation/calendar?start={day}&end={day+timedelta(days=40)}&project_id={pid}"
            data = await req("GET", query, "manager")
            assert data["balances"][0]["days"] == 2.5 and len(data["ledger"]) == 3
            await req("GET", query+f"&employee_id={eid}", "staff")
            await req("GET", f"/rotation/calendar?start={day}&end={day}&employee_id={eid}", "staff")
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
            await req("POST", f"/rotation/claims/{leave['id']}/review", "staff", status=403, json={"action":"approve","note":"区域组长不能审批调休"})
            await req("POST", f"/rotation/claims/{leave['id']}/review", "aftersales", status=409, json={"action":"approve","note":"额度不足"})
            data=await req("GET",query,"manager")
            assert all(d["kind"]=="work" for d in data["days"] if d["date"] in {v["date"] for v in leave_days})
            await req("POST", f"/rotation/claims/{leave['id']}/review", "aftersales", json={"action":"reject","note":"余额不足，重新申请"})
            used = await req("POST", "/rotation/claims", "engineer", json={"project_id":pid,"kind":"leave","days":[*leave_days[:2],{"date":leave_days[2]["date"],"kind":"half_leave"}],"reason":"两天半调休"})
            await req("POST", f"/rotation/claims/{used['id']}/review", "aftersales", json={"action":"approve","note":"同意调休"})
            data = await req("GET", query, "manager")
            assert data["balances"][0]["days"] == 0
            async with async_session() as db:
                person=await db.get(Employee,eid)
                rt=await attendance._get_rule_runtime(db,eid,day+timedelta(days=23),person)
                assert rt["rotation"]["kind"]=="half_leave" and attendance._free_required_hours(rt["rule"],rt["extra"])==6
                assert not await MonthSummaryService._expected_work_day(db,eid,day+timedelta(days=21),None)
            await req("POST", f"/rotation/claims/{credits[0]}/review", "admin", status=409, json={"action":"revoke","note":"不能删除已用额度"})
            await req("POST", f"/rotation/claims/{used['id']}/review", "admin", json={"action":"revoke","note":"现场取消调休，退回额度"})
            # Normal rest never consumes balance. Adjusting a rest day preserves origin.
            rest_day=day+timedelta(days=27)
            pending_adjust = await req("POST", f"/rotation/projects/{pid}/adjust", "manager", json={"employee_ids":[eid],"days":[{"date":str(rest_day),"kind":"work","shift_label":"day"}],"reason":"临时继续陪产"})
            assert pending_adjust["claim"]["status"] == "pending"
            # A pending request left by the previous version must acquire the
            # new chain instead of falling through to one-step approval.
            async with async_session() as db:
                old=await db.get(RotationClaim,pending_adjust["claim"]["id"])
                old.facts={}
                old.days=[{k:v for k,v in item.items() if k not in {"project_id","before_project_id"}} for item in old.days]
                await db.commit()
            before_adjust = await req("GET", query, "manager")
            assert next(d for d in before_adjust["days"] if d["date"]==str(rest_day))["kind"] == "rest"
            pending_view=next(c for c in before_adjust["claims"] if c["id"]==pending_adjust["claim"]["id"])
            assert pending_view["next_approval"]["role"] == "regional_lead"
            assert pending_view["approval_steps"][0]["status"] == "skipped"
            await req("POST", f"/rotation/claims/{pending_adjust['claim']['id']}/review", "manager", status=403,
                json={"action":"approve","note":"代提交人不能自审"})
            await approve_schedule(pending_adjust["claim"]["id"], submitter_is_leader=True)
            data = await req("GET", query, "manager")
            r=next(d for d in data["days"] if d["date"]==str(rest_day))
            assert r["original_kind"]=="rest" and r["kind"]=="work" and r["shift_label"]=="day" and data["balances"][0]["days"]==2.5
            await req("POST", f"/rotation/projects/{pid}/adjust", "manager", status=409, json={"employee_ids":[eid],"days":[{"date":str(rest_day),"kind":"rest","expected_revision":1}],"reason":"过期页面"})
            schedule=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"schedule","days":[{"date":str(rest_day),"kind":"rest"}],"reason":"申请恢复轮休"})
            await approve_schedule(schedule["id"])
            rested = await req("GET", query, "manager")
            assert next(d for d in rested["days"] if d["date"] == str(rest_day))["shift_label"] is None
            moment=datetime.combine(rest_day,time(8),rotation.CST).astimezone(timezone.utc)
            simulated_today=rest_day
            rt=await req("GET","/attendance/my/runtime","engineer")
            await req("POST","/attendance/clock-in","engineer",json={"project_context":rt["project_context"]})
            short=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"credit","days":[{"date":str(rest_day),"kind":"work"}],"reason":"临时陪产两小时"})
            await req("POST",f"/rotation/claims/{short['id']}/review","admin",status=409,json={"action":"approve","days":0.5,"note":"缺卡不能确认"})
            moment+=timedelta(hours=2)
            rt=await req("GET","/attendance/my/runtime","engineer")
            await req("POST","/attendance/clock-out","engineer",json={"project_context":rt["project_context"]})
            await req("POST",f"/rotation/claims/{short['id']}/review","admin",status=422,json={"action":"approve","days":1,"note":"不足工时不能记全天"})
            await req("POST",f"/rotation/claims/{short['id']}/review","admin",json={"action":"approve","days":0.5,"note":"负责人确认临时出勤，按半天安排补休"})
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
            await approve_schedule(confirm["id"])
            half=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"leave","days":[{"date":str(day),"kind":"half_leave"}],"reason":"当天六小时出勤，另半天申请调休"})
            await req("POST",f"/rotation/claims/{half['id']}/review","admin",json={"action":"approve","note":"确认半天调休"})
            from app.services.field_jobs import process_recalculation
            await process_recalculation()
            async with async_session() as db:
                rec=await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id==eid,AttendanceRecord.date==day))
                assert not rec.anomaly_type, rec.anomaly_type
            await req("POST",f"/rotation/claims/{half['id']}/review","admin",json={"action":"revoke","note":"验收后退回额度"})
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
            regional_read = await req("GET", f"/rotation/calendar?start={day}&end={day}&project_id={other['id']}", "staff")
            assert not regional_read["can_manage"]
            await req("POST",f"/rotation/projects/{other['id']}/preview","staff",status=403,json=body)
            # A project leader's own schedule skips that leader's approval node.
            await req("POST", f"/field/projects/{pid}/members", "staff", json={"employee_ids":[2]})
            own_date=day+timedelta(days=35)
            own=await req("POST","/rotation/claims","manager",json={"project_id":pid,"kind":"schedule",
                "days":[{"date":str(own_date),"kind":"work"}],"reason":"项目组长本人未来排班"})
            assert [(step["role"],step["status"]) for step in own["approval_steps"]]==[
                ("project_leader","skipped"),("regional_lead","pending"),("aftersales_manager","pending")]
            assert own["next_approval"]["step_index"] == 1
            await req("POST",f"/rotation/claims/{own['id']}/review","staff",json={"action":"approve","note":"区域组长审核组长本人排班"})
            await req("POST",f"/rotation/claims/{own['id']}/review","aftersales",json={"action":"approve","note":"售后经理终审组长本人排班"})
            # Two simultaneous spending approvals cannot overdraw one account.
            spend=[]
            for offsets in [(1,2),(3,4)]:
                c=await req("POST","/rotation/claims","engineer",json={"project_id":pid,"kind":"leave","days":[{"date":str(day+timedelta(days=n)),"kind":"leave"} for n in offsets],"reason":"并发额度验收"})
                spend.append(c["id"])
            async def approve_raw(cid):
                return await client.post(f"/api/v1/rotation/claims/{cid}/review",headers={"Authorization":"Bearer "+tokens["admin"]},json={"action":"approve","note":"并发审批验收"})
            responses=await asyncio.gather(*(approve_raw(cid) for cid in spend))
            assert sorted(r.status_code for r in responses)==[200,409], [(r.status_code,r.text) for r in responses]
            winning=next(r.json()["id"] for r in responses if r.status_code==200)
            await req("POST",f"/rotation/claims/{winning}/review","admin",json={"action":"revoke","note":"验收完成退回额度"})
            # A transfer must not strand the employee behind the old project's future calendar.
            future={"employee_ids":[eid],"start_date":str(day+timedelta(days=30)),"end_date":str(day+timedelta(days=33)),"cycle_start":str(day)}
            future_preview=await req("POST",f"/rotation/projects/{pid}/preview","manager",json=future)
            await req("POST",f"/rotation/projects/{pid}/publish?revision={future_preview['revision']}","manager",json=future)
            target_lead=await req("POST","/field/people",json={"name":"接收项目组长","phone":"19911115555","employee_no":"ROT-TARGET-LEAD","role":"employee"})
            tokens["target_lead"]=(await req("POST","/auth/login","target_lead",json={"phone":"19911115555","password":"585858"}))["access_token"]
            target=await req("POST","/field/projects",json={"code":"ROT-NEXT","name":"调动后轮休项目","customer_id":base["customer_id"],"manager_id":target_lead["id"]})
            await req("PUT",f"/field/projects/{target['id']}/configuration",json={"rule_id":rule["id"]})
            incoming=await req("POST","/field/people",json={"name":"接收区域组长","phone":"19911113333","employee_no":"ROT-REGION","role":"regional_lead"})
            tokens["incoming"]=(await req("POST","/auth/login","incoming",json={"phone":"19911113333","password":"585858"}))["access_token"]
            await req("PUT",f"/field/projects/{target['id']}/regional-leads",json={"primary_lead_id":incoming["id"],"assistant_lead_ids":[],"reason":"接收区域"})
            current_directory=await req("GET","/field/directory")
            assignment=next(a for a in current_directory["assignments"] if a["employee_id"]==eid and a["is_current"])
            transfer=await req("POST",f"/field/assignments/{assignment['id']}/transfer-requests","incoming",json={"project_id":target["id"],"effective_date":str(day+timedelta(days=30)),"reason":"轮休周期不重置的项目交接"})
            await req("POST",f"/field/transfer-requests/{transfer['id']}/review","staff",json={"approve":True,"note":"原项目确认交接"})
            next_calendar=await req("GET",f"/rotation/calendar?start={day+timedelta(days=30)}&end={day+timedelta(days=33)}&project_id={target['id']}")
            queued=next(c for c in next_calendar["claims"] if c["kind"]=="schedule" and c["status"]=="pending")
            assert len(queued["approval_steps"])==5 and queued["next_approval"]["role"]=="project_leader"
            assert all(step["status"]=="pending" for step in queued["approval_steps"])
            source_calendar=await req("GET",f"/rotation/calendar?start={day+timedelta(days=30)}&end={day+timedelta(days=33)}&project_id={pid}","manager")
            assert any(c["id"]==queued["id"] for c in source_calendar["claims"])
            for who in ("manager","backup","target_lead","incoming","admin"):
                await req("POST",f"/rotation/claims/{queued['id']}/review",who,json={"action":"approve","note":f"{who}确认交接排班"})
            async with async_session() as db:
                transferred=await rotation.day_row(db,eid,day+timedelta(days=30))
                assert transferred.project_id==target['id']
                assert transferred.cycle_start==day
                historical=await rotation.day_row(db,eid,day)
                assert historical.project_id==pid
                assert historical.rule_snapshot.get(rotation.SHIFT_LABEL_KEY) == "night"
            # Each project leader sees only the dates of their own project in
            # both calendar responses and direct review responses.
            source_only = day + timedelta(days=26)
            target_only = day + timedelta(days=31)
            multi = await req("POST", "/rotation/claims", "engineer", json={"kind":"schedule",
                "days":[{"date":str(source_only),"project_id":pid,"kind":"rest"},
                        {"date":str(target_only),"project_id":target["id"],"kind":"rest"}],
                "reason":"跨项目各调整一日"})
            source_review = await req("POST", f"/rotation/claims/{multi['id']}/review", "manager",
                json={"action":"approve","note":"原项目组长核对"})
            assert [d["date"] for d in source_review["days"]] == [str(source_only)]
            assert all(s["project_id"] == pid for s in source_review["approval_steps"])
            assert "workflow" not in source_review["facts"]
            assert source_review["next_approval"] == {"role":"regional_lead","project_id":pid,"step_index":1}
            source_visible = await req("GET", f"/rotation/calendar?start={source_only}&end={target_only}&project_id={pid}", "manager")
            assert [d["date"] for d in next(c for c in source_visible["claims"] if c["id"] == multi["id"])["days"]] == [str(source_only)]
            await req("POST", f"/rotation/claims/{multi['id']}/review", "staff",
                json={"action":"approve","note":"原区域组长核对"})
            source_after_region = await req("GET", f"/rotation/calendar?start={source_only}&end={target_only}&project_id={pid}", "manager")
            assert next(c for c in source_after_region["claims"] if c["id"] == multi["id"])["next_approval"] is None
            target_review = await req("POST", f"/rotation/claims/{multi['id']}/review", "target_lead",
                json={"action":"approve","note":"目标项目组长核对"})
            assert [d["date"] for d in target_review["days"]] == [str(target_only)]
            assert all(s["project_id"] == target["id"] for s in target_review["approval_steps"])
            await req("POST", f"/rotation/claims/{multi['id']}/review", "incoming",
                json={"action":"approve","note":"目标区域组长核对"})
            await req("POST", f"/rotation/claims/{multi['id']}/review", "aftersales",
                json={"action":"approve","note":"售后经理最终核对"})
            cross=await req("POST","/rotation/claims","engineer",json={"kind":"leave",
                "days":[{"date":str(day+timedelta(days=25)),"kind":"half_leave","project_id":pid},
                        {"date":str(day+timedelta(days=30)),"kind":"half_leave","project_id":target["id"]}],
                "reason":"跨项目交接前后各半天调休"})
            await req("POST",f"/rotation/claims/{cross['id']}/review","staff",status=403,
                json={"action":"approve","note":"区域组长不能审批跨项目调休"})
            approved_cross=await req("POST",f"/rotation/claims/{cross['id']}/review","aftersales",
                json={"action":"approve","note":"售后经理整单审批"})
            assert approved_cross["status"]=="approved" and approved_cross["days_amount"]==1
            await req("POST",f"/rotation/claims/{cross['id']}/review","aftersales",
                json={"action":"revoke","note":"验收后恢复跨项目日期余额"})
            # One account may sign two distinct roles, but a retried node cannot
            # silently advance the next stage. A self-review stage is skipped.
            async with async_session() as db:
                db.add(EmployeeRole(employee_id=target_lead["id"],role_name="regional_lead",granted_by=1))
                await db.commit()
            await req("PUT",f"/field/projects/{target['id']}/regional-leads",json={
                "primary_lead_id":target_lead["id"],"assistant_lead_ids":[],"reason":"双身份审批验收"})
            dual_day=day+timedelta(days=33)
            dual=await req("POST","/rotation/claims","engineer",json={"project_id":target["id"],
                "kind":"schedule","days":[{"date":str(dual_day),"kind":"rest"}],"reason":"双身份两级审批"})
            dual_path=f"/rotation/claims/{dual['id']}/review"
            assert dual["next_approval"]["step_index"] == 0
            leader_signed=await req("POST",dual_path,"target_lead",json={
                "action":"approve","expected_step":0,"note":"项目组长签署"})
            assert leader_signed["next_approval"]["step_index"] == 1
            await req("POST",dual_path,"target_lead",status=409,json={
                "action":"approve","expected_step":0,"note":"重复项目组长签署"})
            await req("POST",dual_path,"target_lead",status=409,json={
                "action":"approve","note":"缺少明确节点"})
            regional_signed=await req("POST",dual_path,"target_lead",json={
                "action":"approve","expected_step":1,"note":"区域组长签署"})
            assert regional_signed["next_approval"]["step_index"] == 2
            async with async_session() as db:
                events=(await db.scalars(select(AuditLog).where(
                    AuditLog.action=="rotation_approval_step"))).all()
                signed=[json.loads(event.after_data) for event in events
                    if json.loads(event.after_data).get("id")==dual["id"]]
                assert len(signed)==2
                assert [entry["facts"]["workflow"]["next"] for entry in signed]==[1,2]
            await req("POST",dual_path,"aftersales",json={
                "action":"approve","expected_step":2,"note":"售后经理终审"})
            await req("POST",dual_path,"aftersales",status=409,json={
                "action":"approve","expected_step":2,"note":"重复终审"})
            await req("POST",f"/field/projects/{target['id']}/members","target_lead",
                json={"employee_ids":[target_lead["id"]]})
            own_regional_day=day+timedelta(days=35)
            await req("POST","/rotation/claims","target_lead",status=422,json={
                "project_id":target["id"],"kind":"schedule",
                "days":[{"date":str(own_regional_day),"kind":"work"}],
                "reason":"本人兼任组长和唯一区域组长，须先分配协管"})
            await req("PUT",f"/field/projects/{target['id']}/regional-leads",json={
                "primary_lead_id":target_lead["id"],"assistant_lead_ids":[incoming["id"]],
                "reason":"为本人排班配置独立协管审核人"})
            self_claim=await req("POST","/rotation/claims","target_lead",json={
                "project_id":target["id"],"kind":"schedule",
                "days":[{"date":str(own_regional_day),"kind":"work"}],"reason":"本人兼任组长和区域组长"})
            assert [(step["role"],step["status"]) for step in self_claim["approval_steps"]]==[
                ("project_leader","skipped"),("regional_lead","pending"),("aftersales_manager","pending")]
            assert self_claim["approval_steps"][0].get("skip_reason")
            assert self_claim["next_approval"]["step_index"] == 1
            await req("POST",f"/rotation/claims/{self_claim['id']}/review","target_lead",status=403,
                json={"action":"approve","expected_step":1,"note":"本人不得自审"})
            await req("POST",f"/rotation/claims/{self_claim['id']}/review","incoming",
                json={"action":"approve","expected_step":1,"note":"协管区域组长独立审核"})
            await req("POST",f"/rotation/claims/{self_claim['id']}/review","admin",
                json={"action":"approve","expected_step":2,"note":"管理员代审本人双角色排班"})
            regional_submitted=await req("POST","/rotation/claims","staff",json={"project_id":pid,
                "employee_id":eid,"kind":"schedule",
                "days":[{"date":str(day+timedelta(days=4)),"kind":"rest"}],
                "reason":"区域组长代组员提出调班"})
            assert [(s["role"],s["status"]) for s in regional_submitted["approval_steps"]]==[
                ("project_leader","pending"),("regional_lead","pending"),("aftersales_manager","pending")]
            await req("POST",f"/rotation/claims/{regional_submitted['id']}/review","manager",
                json={"action":"approve","expected_step":0,"note":"项目组长核对"})
            await req("POST",f"/rotation/claims/{regional_submitted['id']}/review","staff",status=403,
                json={"action":"approve","expected_step":1,"note":"代提交区域组长不得自审"})
            await req("POST",f"/rotation/claims/{regional_submitted['id']}/review","backup",
                json={"action":"approve","expected_step":1,"note":"协管区域组长独立审核"})
            await req("POST",f"/rotation/claims/{regional_submitted['id']}/review","aftersales",
                json={"action":"approve","expected_step":2,"note":"售后经理终审"})
            manager_submitted=await req("POST","/rotation/claims","aftersales",json={"project_id":pid,
                "employee_id":eid,"kind":"schedule",
                "days":[{"date":str(day+timedelta(days=5)),"kind":"rest"}],
                "reason":"售后经理代组员提出调班"})
            assert [s["role"] for s in manager_submitted["approval_steps"]]==[
                "project_leader","regional_lead","admin"]
            assert not manager_submitted["can_review"]
            await req("POST",f"/rotation/claims/{manager_submitted['id']}/review","manager",
                json={"action":"approve","expected_step":0,"note":"项目组长核对"})
            await req("POST",f"/rotation/claims/{manager_submitted['id']}/review","staff",
                json={"action":"approve","expected_step":1,"note":"区域组长核对"})
            await req("POST",f"/rotation/claims/{manager_submitted['id']}/review","aftersales",status=403,
                json={"action":"approve","expected_step":2,"note":"代提交售后经理不得自审"})
            await req("POST",f"/rotation/claims/{manager_submitted['id']}/review","admin",
                json={"action":"approve","expected_step":2,"note":"管理员代审售后经理提交的调班"})
            # Admin authority never overrides the ban on approving a claim for
            # the same employee or one the admin submitted on their behalf.
            await req("POST",f"/field/projects/{pid}/members","staff",json={"employee_ids":[1]})
            admin_start=day+timedelta(days=29)
            admin_plan={"employee_ids":[1],"start_date":str(admin_start),
                "end_date":str(admin_start+timedelta(days=1)),"cycle_start":str(admin_start-timedelta(days=6))}
            admin_preview=await req("POST",f"/rotation/projects/{pid}/preview","admin",json=admin_plan)
            assert [row["original_kind"] for row in admin_preview["rows"]]==["rest","work"]
            await req("POST",f"/rotation/projects/{pid}/publish?revision={admin_preview['revision']}","admin",json=admin_plan)
            simulated_today=admin_start
            own_credit=await req("POST","/rotation/claims","admin",json={"project_id":pid,
                "kind":"credit","days":[{"date":str(admin_start),"kind":"work"}],"reason":"管理员本人攒休"})
            own_leave=await req("POST","/rotation/claims","admin",json={"project_id":pid,
                "kind":"leave","days":[{"date":str(admin_start+timedelta(days=1)),"kind":"leave"}],"reason":"管理员本人调休"})
            assert not own_credit["can_review"] and not own_leave["can_review"]
            for claim_id in (own_credit["id"],own_leave["id"]):
                await req("POST",f"/rotation/claims/{claim_id}/review","admin",status=403,
                    json={"action":"approve","note":"管理员不得自审"})
            await req("POST",f"/field/projects/{pid}/members","staff",json={"employee_ids":[sales["id"]]})
            manager_plan={**admin_plan,"employee_ids":[sales["id"]]}
            manager_preview=await req("POST",f"/rotation/projects/{pid}/preview","admin",json=manager_plan)
            await req("POST",f"/rotation/projects/{pid}/publish?revision={manager_preview['revision']}","admin",json=manager_plan)
            manager_credit=await req("POST","/rotation/claims","aftersales",json={"project_id":pid,
                "kind":"credit","days":[{"date":str(admin_start),"kind":"work"}],"reason":"经理本人攒休"})
            manager_leave=await req("POST","/rotation/claims","aftersales",json={"project_id":pid,
                "kind":"leave","days":[{"date":str(admin_start+timedelta(days=1)),"kind":"leave"}],
                "reason":"经理本人调休"})
            for claim_id in (manager_credit["id"],manager_leave["id"]):
                await req("POST",f"/rotation/claims/{claim_id}/review","aftersales2",status=403,
                    json={"action":"reject","note":"第二位经理不得代审经理本人"})
                await req("POST",f"/rotation/claims/{claim_id}/review","admin",
                    json={"action":"reject","note":"管理员核对经理本人申请"})
            manager_own_schedule=await req("POST","/rotation/claims","aftersales",json={
                "project_id":pid,"kind":"schedule",
                "days":[{"date":str(admin_start+timedelta(days=1)),"kind":"rest"}],
                "reason":"经理本人调整现场排班"})
            assert manager_own_schedule["approval_steps"][-1]["role"] == "admin"
            manager_own_path=f"/rotation/claims/{manager_own_schedule['id']}/review"
            await req("POST",manager_own_path,"manager",json={
                "action":"approve","expected_step":0,"note":"项目组长核对"})
            await req("POST",manager_own_path,"staff",json={
                "action":"approve","expected_step":1,"note":"区域组长核对"})
            await req("POST",manager_own_path,"aftersales2",status=403,json={
                "action":"approve","expected_step":2,"note":"另一经理不能代审"})
            await req("POST",manager_own_path,"admin",json={
                "action":"approve","expected_step":2,"note":"管理员代审经理本人调班"})
            await req("POST","/rotation/claims","admin",status=403,json={"project_id":pid,
                "employee_id":eid,"kind":"leave",
                "days":[{"date":str(day+timedelta(days=24)),"kind":"half_leave"}],
                "reason":"管理员代员工提交"})
            await req("POST","/rotation/claims","aftersales",status=403,json={"project_id":pid,
                "employee_id":eid,"kind":"credit",
                "days":[{"date":str(rest_day),"kind":"work"}],"reason":"售后经理不能代提攒休"})
            async with async_session() as db:
                assert await db.get(RotationAccount,1) is None
                assert not await db.scalar(select(RotationLedger.id).where(
                    RotationLedger.claim_id.in_([own_credit["id"],own_leave["id"]])))
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
                assert await db.scalar(select(func.count(RotationLedger.id)))==12
                assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.resource_type=="rotation"))>20
    print("PASS: rotation preview/publish, real clocks, 14-day credits, half-days, pending dates, scoped permissions, atomic spending, reversals, adjustments, overnight dates, duplicate conversion, persistence and audit")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
