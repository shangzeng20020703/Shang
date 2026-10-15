"""Isolated dashboard acceptance: unique people, historical segments, scope and empty data."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sandbox = tempfile.TemporaryDirectory(prefix="aftersales-dashboard-")
os.environ.update(DATABASE_URL_OVERRIDE="sqlite+aiosqlite:///" + sandbox.name + "/test.db",
                  BACKGROUND_JOBS="false", LOGIN_MODE="password", ENVIRONMENT="development")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.bootstrap import initialize, seed
from app.core.database import async_session, engine
from app.main import app
from app.models.employee import Employee
from app.models.organization import Location
from app.models.attendance import AttendanceRecord, AttendanceStatus
from app.models.field_service import FieldProject, FieldSite, FieldAssignment, FieldMembership, FieldAttendanceSegment
from app.services.field_service import CST, today
from app.services.field_dashboard import anomaly_labels


async def main():
    await initialize()
    await seed(True)
    await initialize()
    app.state.limiter.enabled = False
    day = today()
    start = datetime.combine(day, time.min, CST).astimezone(timezone.utc)
    async with async_session() as db:
        admin = await db.scalar(select(Employee).where(Employee.employee_no == "ADMIN"))
        manager = await db.scalar(select(Employee).where(Employee.employee_no == "DEMO-MANAGER"))
        staff = await db.scalar(select(Employee).where(Employee.employee_no == "DEMO-STAFF"))
        site = await db.scalar(select(FieldSite))
        project = await db.get(FieldProject, site.project_id)
        assignment = await db.scalar(select(FieldAssignment).where(FieldAssignment.employee_id == staff.id))
        interval = await db.scalar(select(FieldMembership).where(FieldMembership.assignment_id == assignment.id))
        interval.started_at, interval.ended_at = start, start + timedelta(hours=10)
        other = FieldProject(code="DASH-PRIVATE", name="其他负责人的项目", customer_id=project.customer_id, manager_id=admin.id)
        db.add(other)
        await db.flush()
        location = Location(name="B")
        db.add(location)
        await db.flush()
        site_b = FieldSite(project_id=other.id, name="B", rule_id=site.rule_id, location_id=location.id)
        db.add(site_b)
        await db.flush()
        unpunched = Employee(name="未打卡人员", employee_no="DASH-EMPTY", phone="13800000301", status="试用", is_active=True)
        departed = Employee(name="已离职人员", employee_no="DASH-LEFT", phone="13800000302", status="离职", is_active=False)
        db.add_all([unpunched, departed])
        await db.flush()
        # The same person worked in A and B; one ended incomplete, one completed.
        record = AttendanceRecord(employee_id=staff.id, date=day, status=AttendanceStatus.late, anomaly_type="迟到 5 分钟")
        db.add(record)
        await db.flush()
        db.add(FieldAttendanceSegment(membership_id=interval.id, employee_id=staff.id, work_date=day,
            attendance_record_id=record.id, project_id=project.id, project_name=project.name,
            rule_snapshot={"name":"A规则"}, record_data={"clock_in_time":(start+timedelta(hours=9)).isoformat(), "status":"迟到", "anomaly_type":"迟到 5 分钟"}, needs_review=True))
        a_b = FieldAssignment(site_id=site_b.id, employee_id=staff.id, start_date=day, end_date=day, created_by=admin.id)
        a_empty = FieldAssignment(site_id=site.id, employee_id=unpunched.id, start_date=day, end_date=day, created_by=admin.id)
        a_left = FieldAssignment(site_id=site.id, employee_id=departed.id, start_date=day, end_date=day, created_by=admin.id)
        a_void = FieldAssignment(site_id=site.id, employee_id=manager.id, start_date=day, end_date=day, created_by=admin.id)
        db.add_all([a_b, a_empty, a_left, a_void])
        await db.flush()
        second = FieldMembership(assignment_id=a_b.id, employee_id=staff.id, started_at=start+timedelta(hours=10), ended_at=start+timedelta(days=1))
        db.add_all([second,
            FieldMembership(assignment_id=a_empty.id, employee_id=unpunched.id, started_at=start, ended_at=start+timedelta(days=1)),
            FieldMembership(assignment_id=a_left.id, employee_id=departed.id, started_at=start, ended_at=start+timedelta(hours=12)),
            FieldMembership(assignment_id=a_void.id, employee_id=manager.id, started_at=start+timedelta(hours=8), ended_at=start+timedelta(hours=8)),
        ])
        await db.flush()
        db.add(FieldAttendanceSegment(membership_id=second.id, employee_id=staff.id, work_date=day,
            attendance_record_id=record.id, project_id=other.id, project_name=other.name,
            rule_snapshot={"name":"B规则"}, record_data={"clock_in_time":(start+timedelta(hours=10)).isoformat(), "clock_out_time":(start+timedelta(hours=18)).isoformat(), "status":"正常"}))
        project_id, other_id = project.id, other.id
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tokens = {}
        for i, name in enumerate(("admin", "manager", "staff"), 1):
            response = await client.post('/api/v1/auth/login', json={"phone":f"1990000000{i}", "password":"Demo-AfterSales-2026!"})
            assert response.status_code == 200, response.text
            tokens[name] = response.json()["access_token"]
        async def get(who="admin", status=200, **params):
            response = await client.get('/api/v1/field/dashboard', params={"day":str(day), **params}, headers={"Authorization":"Bearer " + tokens[who]})
            assert response.status_code == status, response.text
            return response.json()
        result = await get()
        assert result['people_total'] == 2  # Ordinary staff and probation only; management and departed excluded.
        assert result['summary']['people'] == 3 and len(result['rows']) == 4
        assert result['summary']['punched'] == 1 and result['summary']['partial'] == 1
        assert result['summary']['complete'] == 0 and result['summary']['unpunched'] == 2
        assert result['summary']['abnormal'] == 1
        assert {a['name']:a['value'] for a in result['summary']['anomalies']}['迟到'] == 1
        assert {a['name']:a['value'] for a in result['summary']['anomalies']}['待核对'] == 1
        assert len(result['trend']) == 7 and result['trend'][-1]['people'] == 3
        assert all(point['people'] == 0 for point in result['trend'][:-1])
        assert sum(p['people'] for p in result['project_stats']) == 4  # cross-project counts differ intentionally.
        async with async_session() as db:
            manager_interval = await db.scalar(select(FieldMembership).where(FieldMembership.assignment_id == a_void.id))
            manager_interval.started_at = start+timedelta(hours=9)
            manager_interval.ended_at = start+timedelta(days=1)
            await db.commit()
        with patch('app.services.field_dashboard.clock_now', return_value=start+timedelta(hours=14)):
            current = await get()
            staffing = {p['id']:p for p in current['staffing']}
            assert current['assigned_people'] == 2
            assert [m['name'] for m in staffing[project_id]['members']] == ['未打卡人员']
            assert [m['name'] for m in staffing[other_id]['members']] == [staff.name]
            assert all(p['people'] == len(p['members']) for p in current['staffing'])
            manager_current = await get('manager')
            assert {p['id'] for p in manager_current['staffing']} == {project_id}
        filtered = await get(project_id=other_id)
        assert filtered['summary']['people'] == 1 and filtered['summary']['complete'] == 1
        assert filtered['summary']['abnormal'] == 0  # No leaked A anomaly or daily rollup into B.
        scoped = await get('manager')
        assert {p['id'] for p in scoped['projects']} == {project_id}
        assert all(row['project_id'] == project_id for row in scoped['rows'])
        assert scoped['summary']['abnormal'] == 1
        await get('manager', status=404, project_id=other_id)
        await get('staff', status=403)
        empty = await get(day=str(day-timedelta(days=30)))
        assert empty['summary']['people'] == 0 and empty['summary']['abnormal'] == 0 and empty['people_total'] == 2
        assert empty['assigned_people'] == 0 and all(p['people'] == 0 for p in empty['staffing'])
        assert empty['rows'] == [] and all(p['punched'] == 0 for p in empty['trend'])
    assert anomaly_labels({'status':AttendanceStatus.late}) == ['迟到']
    assert anomaly_labels({'status':'未打卡'}) == []
    assert anomaly_labels({'anomaly_type':'早退 5 分钟; 缺少上班打卡; GPS 范围外; 照片缺失'}) == ['早退','缺卡','定位 / WiFi','其他异常']
    await engine.dispose()
    print('PASS: deduplication, mutually exclusive progress, multi-anomaly deduplication, 7-day CST window, zero intervals, historical departed staff, project filter, scope and empty dates')

if __name__ == '__main__':
    asyncio.run(main())
