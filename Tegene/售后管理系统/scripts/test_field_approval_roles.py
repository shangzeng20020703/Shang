"""Check field leave/travel approval routing in an isolated SQLite database."""

import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

test_db = tempfile.TemporaryDirectory(prefix="aftersales-field-approval-")
os.environ.update(
    DATABASE_URL_OVERRIDE=f"sqlite+aiosqlite:///{test_db.name}/test.db",
    BACKGROUND_JOBS="false",
    ENVIRONMENT="development",
    LOGIN_MODE="password",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bootstrap import initialize, seed
from app.core.database import async_session, engine
from app.main import app
from app.models.approval import ApprovalFlow, ApprovalInstance, ApprovalNode
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.leave import ApprovalStatus, LeaveRequest, LeaveType
from app.models.attendance import AttendanceRule, WorkHourType
from app.models.field_absence import FieldAbsenceDay
from app.models.field_service import FieldAssignment, FieldCustomer, FieldMembership, FieldProject, FieldSite
from app.models.organization import Location


async def main() -> None:
    await initialize()
    await seed(True)
    app.state.limiter.enabled = False
    async with async_session() as db:
        admin = await db.scalar(select(Employee).where(Employee.employee_no == "ADMIN"))
        old_manager = await db.scalar(select(Employee).where(Employee.employee_no == "DEMO-MANAGER"))
        staff = await db.scalar(select(Employee).where(Employee.employee_no == "DEMO-STAFF"))
        staff.hire_date = date.today() - timedelta(days=1000)
        manager = Employee(employee_no="AFTERSALES-MGR-QA", name="售后经理验收",
                           phone="19911113331", hashed_password=old_manager.hashed_password,
                           is_active=True, status="在职")
        regional = Employee(employee_no="REGIONAL-QA", name="区域组长验收",
                            phone="19911113332", hashed_password=old_manager.hashed_password,
                            is_active=True, status="在职")
        hr = Employee(employee_no="HR-QA", name="HR验收",
                      phone="19911113333", hashed_password=old_manager.hashed_password,
                      is_active=True, status="在职")
        company_person = Employee(employee_no="COMPANY-QA", name="非售后员工验收",
                                  phone="19911113334", hashed_password=old_manager.hashed_password,
                                  is_active=True, status="在职")
        db.add_all([manager, regional, hr, company_person])
        await db.flush()
        for user, role in ((manager, "aftersales_manager"), (regional, "regional_lead"), (hr, "hr")):
            db.add(EmployeeRole(employee_id=user.id, role_name=role, granted_by=admin.id))
        # Deliberately configure old manager/HR nodes: field absence must override these.
        for module in ("leave", "business_trip"):
            flow = ApprovalFlow(name=f"旧{module}流程", module=module, is_active=True)
            db.add(flow)
            await db.flush()
            db.add_all([
                ApprovalNode(flow_id=flow.id, node_order=1, node_type="approval", approver_type="manager"),
                ApprovalNode(flow_id=flow.id, node_order=2, node_type="approval", approver_type="hr"),
            ])
        leave_type = await db.scalar(select(LeaveType).where(LeaveType.code == "personal"))
        future = date.today() + timedelta(days=30)
        future += timedelta(days=(7 - future.weekday()) % 7)
        customer = FieldCustomer(name="审批项目快照客户")
        rule = AttendanceRule(name="审批项目快照规则", work_hour_type=WorkHourType.flexible)
        db.add_all([customer, rule])
        await db.flush()
        projects = []
        for number in (1, 2):
            project = FieldProject(code=f"ABSENCE-QA-{number}", name=f"审批项目{number}",
                                   customer_id=customer.id, manager_id=old_manager.id)
            location = Location(name=f"审批项目地点{number}")
            db.add_all([project, location])
            await db.flush()
            site = FieldSite(project_id=project.id, name=project.name,
                             location_id=location.id, rule_id=rule.id)
            db.add(site)
            await db.flush()
            first = future if number == 1 else future + timedelta(days=1)
            last = first
            assignment = FieldAssignment(site_id=site.id, employee_id=staff.id,
                                         start_date=first, end_date=last, created_by=admin.id)
            db.add(assignment)
            await db.flush()
            start = datetime.combine(first, time.min, timezone(timedelta(hours=8))).astimezone(timezone.utc)
            end = start + timedelta(days=1)
            db.add(FieldMembership(assignment_id=assignment.id, employee_id=staff.id,
                                   started_at=start, ended_at=end))
            projects.append(project)
        first_leave = LeaveRequest(employee_id=staff.id, leave_type_id=leave_type.id,
                                   start_date=future, end_date=future, days=Decimal("1"),
                                   reason="独立库角色验收", approval_status=ApprovalStatus.pending)
        direct_leave = LeaveRequest(employee_id=staff.id, leave_type_id=leave_type.id,
                                    start_date=future + timedelta(days=1),
                                    end_date=future + timedelta(days=1), days=Decimal("1"),
                                    reason="独立请假接口验收", approval_status=ApprovalStatus.pending)
        db.add_all([first_leave, direct_leave])
        company_leave = LeaveRequest(employee_id=company_person.id, leave_type_id=leave_type.id,
                                     start_date=future, end_date=future, days=Decimal("1"),
                                     reason="非售后申请隔离", approval_status=ApprovalStatus.pending)
        db.add(company_leave)
        await db.commit()
        ids = {"admin": admin.id, "legacy": old_manager.id, "staff": staff.id,
               "manager": manager.id, "regional": regional.id, "hr": hr.id,
               "first_leave": first_leave.id, "direct_leave": direct_leave.id,
               "company_leave": company_leave.id, "company_person": company_person.id,
               "project1": projects[0].id, "project2": projects[1].id,
               "leave_type": leave_type.id, "future": future}

    tokens = {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def request(method, path, who="admin", expected=200, **kwargs):
            headers = {"Authorization": f"Bearer {tokens[who]}"} if who in tokens else {}
            response = await client.request(method, "/api/v1" + path, headers=headers, **kwargs)
            assert response.status_code == expected, (method, path, who, response.status_code, response.text[:1000])
            return response.json() if response.content else None

        for role, phone in (("admin", "19900000001"), ("legacy", "19900000002"),
                            ("staff", "19900000003"), ("manager", "19911113331"),
                            ("regional", "19911113332"), ("hr", "19911113333")):
            result = await request("POST", "/auth/login", role,
                                   json={"phone": phone, "password": "Demo-AfterSales-2026!"})
            tokens[role] = result["access_token"]

        item = await request("POST", "/approval/instances", "staff", expected=201,
                             json={"module": "leave", "business_type": "leave_request",
                                   "business_id": ids["first_leave"], "summary": "现场请假"})
        leave_instance_id = item["id"]
        async with async_session() as db:
            instance = await db.get(ApprovalInstance, leave_instance_id)
            assert "aftersales_single_approval" in instance.flow_snapshot
        for role in ("legacy", "regional", "hr", "staff"):
            await request("POST", f"/approval/instances/{leave_instance_id}/process", role,
                          expected=403, json={"action": "approve", "comment": "越权"})
        await request("POST", f"/approval/instances/{leave_instance_id}/process", "admin",
                      expected=422, json={"action": "approve"})
        decided = await request("POST", f"/approval/instances/{leave_instance_id}/process", "manager",
                                json={"action": "approve", "comment": "同意"})
        assert decided["status"] == "approved"
        async with async_session() as db:
            leave = await db.get(LeaveRequest, ids["first_leave"])
            assert leave.approval_status == ApprovalStatus.approved and leave.approver_id == ids["manager"]

        trip = await request("POST", "/approval/instances", "staff", expected=201,
                             json={"module": "business_trip", "business_type": "business_trip",
                                   "business_id": 0, "summary": "跨项目出差",
                                   "form_data": {"start_time": ids["future"].isoformat(),
                                                 "end_time": (ids["future"] + timedelta(days=1)).isoformat()}})
        async with async_session() as db:
            days = (await db.scalars(select(FieldAbsenceDay).where(
                FieldAbsenceDay.source_type == "approval_instance",
                FieldAbsenceDay.source_id == trip["id"],
            ).order_by(FieldAbsenceDay.work_date))).all()
            assert [row.project_id for row in days] == [ids["project1"], ids["project2"]]
        await request("GET", f"/approval/instances/{trip['id']}", "legacy", expected=403)
        legacy_pending = await request("GET", "/approval/my-pending", "legacy")
        assert all(item["id"] != trip["id"] for item in legacy_pending["items"])
        manager_pending = await request("GET", "/approval/my-pending", "manager")
        assert any(item["id"] == trip["id"] for item in manager_pending["items"])
        await request("POST", f"/approval/instances/{trip['id']}/process", "regional",
                      expected=403, json={"action": "reject", "comment": "越权"})
        decision = await request("POST", f"/approval/instances/{trip['id']}/process", "manager",
                                 json={"action": "reject", "comment": "信息待补"})
        assert decision["status"] == "rejected"

        own = await request("POST", "/approval/instances", "manager", expected=201,
                            json={"module": "leave", "business_type": "leave_request",
                                  "business_id": 0, "summary": "经理本人申请"})
        await request("POST", f"/approval/instances/{own['id']}/process", "manager",
                      expected=403, json={"action": "approve", "comment": "自行同意"})
        await request("POST", f"/approval/instances/{own['id']}/process", "admin",
                      expected=422, json={"action": "approve"})
        decided = await request("POST", f"/approval/instances/{own['id']}/process", "admin",
                                json={"action": "approve", "comment": "经理缺席，管理员代审"})
        assert decided["status"] == "approved"

        for role in ("legacy", "regional", "hr"):
            await request("POST", f"/leave/requests/{ids['direct_leave']}/approve", role,
                          expected=403, json={"approved": False, "note": "越权"})
        await request("POST", f"/leave/requests/{ids['company_leave']}/approve", "manager",
                      expected=403, json={"approved": True, "note": "越界审批"})
        await request("GET", f"/leave/requests/{ids['company_leave']}", "manager", expected=403)
        manager_leaves = await request("GET", "/leave/requests", "manager")
        assert all(item["employee_id"] != ids["company_person"] for item in manager_leaves["items"])
        manager_pending_leaves = await request("GET", "/leave/requests/pending", "manager")
        assert all(item["employee_id"] != ids["company_person"] for item in manager_pending_leaves["items"])
        await request("GET", "/leave/calendar", "staff", expected=403,
                      params={"year": ids["future"].year, "month": ids["future"].month})
        await request("GET", "/leave/stats", "staff", expected=403)
        await request("GET", "/leave/balances", "legacy", expected=403,
                      params={"year": ids["future"].year})
        await request("GET", f"/leave/integrations/approval-context/{ids['company_leave']}",
                      "legacy", expected=403)
        await request("POST", f"/leave/requests/{ids['direct_leave']}/approve", "admin",
                      expected=422, json={"approved": False})
        direct = await request("POST", f"/leave/requests/{ids['direct_leave']}/approve", "manager",
                               json={"approved": False, "note": "资料不完整"})
        assert direct["approval_status"] == "rejected"
        submitted = await request("POST", "/leave/requests", "staff", expected=201,
                                  json={"leave_type_id": ids["leave_type"],
                                        "start_date": ids["future"].isoformat(),
                                        "end_date": (ids["future"] + timedelta(days=1)).isoformat(),
                                        "reason": "跨项目独立请假"})
        async with async_session() as db:
            days = (await db.scalars(select(FieldAbsenceDay).where(
                FieldAbsenceDay.source_type == "leave_request",
                FieldAbsenceDay.source_id == submitted["id"],
            ).order_by(FieldAbsenceDay.work_date))).all()
            assert [row.project_id for row in days] == [ids["project1"], ids["project2"]]
        decided = await request("POST", f"/leave/requests/{submitted['id']}/approve", "manager",
                                json={"approved": True, "note": "统一审批整单"})
        assert decided["approval_status"] == "approved"
        await request("GET", f"/leave/requests/{ids['direct_leave']}", "regional", expected=403)
        own_list = await request("GET", "/leave/requests", "regional")
        assert all(row["employee_id"] == ids["regional"] for row in own_list["items"])
        manager_list = await request("GET", "/approval/instances?group=approved", "manager")
        assert manager_list["total"] >= 2

    await engine.dispose()
    test_db.cleanup()
    print("field approvals: manager-only leave/travel, admin reason, legacy-role denial and leave visibility passed")


if __name__ == "__main__":
    asyncio.run(main())
