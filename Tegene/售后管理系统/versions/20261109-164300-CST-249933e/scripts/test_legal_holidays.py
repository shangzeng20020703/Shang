"""Exercise holiday editing and attendance classification against an isolated database."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

sandbox = tempfile.TemporaryDirectory(prefix="aftersales-holidays-")
os.environ.update(DATABASE_URL_OVERRIDE=f"sqlite+aiosqlite:///{sandbox.name}/test.db", BACKGROUND_JOBS="false", ENVIRONMENT="development")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.bootstrap import initialize, seed
from app.core.database import async_session, engine
from app.main import app
from app.models.attendance import AttendanceEffect, AttendanceMonthSummary, AttendanceRecord, AttendanceRule, AttendanceStatus, DayType, WorkCalendar, WorkHourType
from app.models.audit_log import AuditLog
from app.models.approval import ApprovalInstance
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.organization import Location
from app.services.attendance import AttendanceRecordService, AttendanceReportService, MonthSummaryService, WorkCalendarService
from app.services.legal_holidays import LegalHolidayService
from sqlalchemy import select

EXPECTED = ["2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-05-01", "2026-05-02", "2026-06-19", "2026-09-25", "2026-10-01", "2026-10-02", "2026-10-03"]

async def history_snapshot():
    async with async_session() as db:
        return [repr((await db.execute(select(model.__table__).order_by(model.id))).mappings().all()) for model in (AttendanceRecord, AttendanceMonthSummary, ApprovalInstance)]

async def main():
    await initialize()
    await seed(True)
    app.state.limiter.enabled = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def req(method, path, token=None, expected=200, **kw):
            response = await client.request(method, "/api/v1" + path, headers={"Authorization": f"Bearer {token}"} if token else {}, **kw)
            assert response.status_code == expected, (method, path, response.status_code, response.text[:500])
            return response.json() if response.content else None
        tokens = {}
        for i, role in enumerate(("admin", "manager", "employee"), 1):
            tokens[role] = (await req("POST", "/auth/management-login" if role == "admin" else "/auth/login", json={"phone": f"1990000000{i}", "password": "Demo-AfterSales-2026!"}))["access_token"]
        path = "/attendance/legal-holidays/2026"
        await req("GET", path, expected=401)
        initial = await req("GET", path, tokens["employee"])
        assert [row["date"] for row in initial["entries"]] == EXPECTED
        assert initial["revision"] == 0 and initial["source"] == "default"
        assert (await req("GET", "/attendance/legal-holidays/2027", tokens["admin"]))["entries"] == []
        await req("GET", "/attendance/legal-holidays/2200", tokens["admin"], expected=422)
        body = {"entries": initial["entries"], "expected_revision": 0}
        legacy = {"date": "2026-05-04", "day_type": "holiday", "holiday_name": "旧假期"}
        for role in ("manager", "employee"):
            await req("PUT", path, tokens[role], expected=403, json=body)
            for method, endpoint, payload in (("POST", "/calendar", legacy), ("POST", "/calendar/batch", {"entries": [legacy]}), ("PUT", "/calendar/999", {"day_type": "holiday"}), ("DELETE", "/calendar/999", None)):
                await req(method, "/attendance" + endpoint, tokens[role], expected=403, **({"json": payload} if payload else {}))
        async with async_session() as db:
            manager = await db.scalar(select(Employee).where(Employee.employee_no == "DEMO-MANAGER"))
            staff = await db.scalar(select(Employee).where(Employee.employee_no == "DEMO-STAFF"))
            db.add(EmployeeRole(employee_id=manager.id, role_name="hr", granted_by=manager.id))
            location = Location(name="隔离节假日测试地点")
            db.add(location); await db.flush(); location_id = location.id
            db.add_all([
                WorkCalendar(date=date(2026, 5, 4), day_type=DayType.holiday, holiday_name="旧全局假期"),
                WorkCalendar(date=date(2026, 5, 9), day_type=DayType.special_workday),
                WorkCalendar(date=date(2026, 5, 4), location_id=location_id, day_type=DayType.holiday, holiday_name="地点例外"),
                AttendanceRecord(employee_id=staff.id, date=date(2026, 5, 1), status=AttendanceStatus.normal, work_hours=Decimal("7"), overtime_hours=Decimal("1")),
                AttendanceMonthSummary(employee_id=staff.id, year=2026, month=5, work_days=20, actual_days=1),
            ])
            await db.commit()
        await req("GET", path, tokens["manager"])
        await req("PUT", path, tokens["manager"], expected=403, json=body)  # HR cannot write either.
        await req("POST", "/attendance/calendar", tokens["admin"], expected=409, json=legacy)
        for entries in ([initial["entries"][0]] * 2, [{"date": "2027-01-01", "holiday_name": "元旦"}], [{"date": "2026-01-01", "holiday_name": "  "}]):
            await req("PUT", path, tokens["admin"], expected=422, json={"entries": entries, "expected_revision": 0})
        async with async_session() as db:
            assert await WorkCalendarService.get_day_type(db, date(2026, 5, 2)) == DayType.holiday  # Explicit holiday wins over Saturday.
            assert await WorkCalendarService.get_day_type(db, date(2026, 5, 9)) == DayType.weekend  # No official makeup day.
            assert await WorkCalendarService.get_day_type(db, date(2026, 5, 4)) == DayType.workday  # Legacy global holiday is inactive.
            assert await WorkCalendarService.get_day_type(db, date(2026, 5, 4), location_id) == DayType.holiday
            assert await WorkCalendarService.get_day_type(db, date(2026, 2, 16)) == DayType.workday
            rule = AttendanceRule(name="节假日免打卡测试", work_hour_type=WorkHourType.standard, extra_config={"legal_holiday_no_punch": True})
            with patch.object(AttendanceRecordService, "_get_rule_runtime", AsyncMock(return_value={"rule": rule})):
                assert not await MonthSummaryService._expected_work_day(db, staff.id, date(2026, 5, 1), None)
                assert await MonthSummaryService._expected_work_day(db, staff.id, date(2026, 5, 4), None)
            effect = AttendanceEffect(effect_type="overtime", minutes=180)
            day_type = await WorkCalendarService.get_day_type(db, date(2026, 5, 2))
            assert AttendanceReportService._daily_overtime_effect_metrics([effect], day_type=day_type)["holiday_ot_to_pay"] == 3.0

        history = await history_snapshot()
        changed = [{"date": "2026-05-04", "holiday_name": "自定义假日"}]
        saved = await req("PUT", path, tokens["admin"], json={"entries": changed, "expected_revision": 0})
        assert saved["entries"] == changed and saved["revision"] == 1 and saved["source"] == "configured"
        await req("PUT", path, tokens["admin"], expected=409, json=body)
        async with async_session() as db:
            assert await WorkCalendarService.get_day_type(db, date(2026, 5, 4)) == DayType.holiday
            assert await WorkCalendarService.get_day_type(db, date(2026, 5, 1)) == DayType.workday
        await engine.dispose()
        code = "import asyncio; from app.core.database import async_session; from app.services.legal_holidays import LegalHolidayService\nasync def run():\n async with async_session() as db:\n  value=await LegalHolidayService.get_year(db,2026); assert value.revision==1 and value.entries[0].holiday_name=='自定义假日'; print('fresh process persistence passed')\nasyncio.run(run())"
        child = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "backend")}, capture_output=True, text=True)
        assert child.returncode == 0, child.stderr
        print(child.stdout.strip())
        empty = await req("PUT", path, tokens["admin"], json={"entries": [], "expected_revision": 1})
        assert empty["entries"] == [] and empty["source"] == "configured"
        assert (await req("GET", path, tokens["employee"]))["entries"] == []
        assert await history_snapshot() == history, "Saving holidays must not rewrite historical records"
        async with async_session() as db:
            logs = (await db.scalars(select(AuditLog).where(AuditLog.resource_type == "LegalHolidayYear"))).all()
            assert len(logs) == 2 and json.loads(logs[-1].after_data)["entries"] == []
    await engine.dispose(); sandbox.cleanup()
    print("legal holidays: 11 dates, role permissions, validation, persistence, conflict handling, day types and historical preservation passed")

if __name__ == "__main__":
    asyncio.run(main())
