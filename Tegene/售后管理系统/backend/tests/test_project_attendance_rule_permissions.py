"""Project rule writes stay within field project authority."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register the full metadata for the isolated database
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.attendance import AttendanceRule
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import FieldCustomer, FieldProject, FieldRegionalAssignment, FieldSite


@pytest.mark.asyncio
async def test_project_rule_create_and_update_respect_assigned_project_scope():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add_all([
            Employee(id=1, employee_no="ADMIN", name="管理员", phone="19900000001", status="在职", is_superuser=True),
            Employee(id=2, employee_no="MANAGER", name="售后经理", phone="19900000002", status="在职"),
            Employee(id=3, employee_no="REGION1", name="区域组长甲", phone="19900000003", status="在职"),
            Employee(id=4, employee_no="REGION2", name="区域组长乙", phone="19900000004", status="在职"),
            Employee(id=5, employee_no="LEADER", name="项目组长", phone="19900000005", status="在职"),
        ])
        await db.flush()
        db.add_all([
            EmployeeRole(employee_id=2, role_name="aftersales_manager", granted_by=1),
            EmployeeRole(employee_id=3, role_name="regional_lead", granted_by=1),
            EmployeeRole(employee_id=4, role_name="regional_lead", granted_by=1),
        ])
        db.add(FieldCustomer(id=1, name="虚构客户"))
        await db.flush()
        db.add_all([
            FieldProject(id=1, code="P1", name="项目一", customer_id=1, manager_id=5),
            FieldProject(id=2, code="P2", name="项目二", customer_id=1, manager_id=5),
        ])
        await db.flush()
        db.add_all([
            FieldRegionalAssignment(project_id=1, employee_id=3, kind="primary",
                                    active_employee_id=3, active_primary_project_id=1, appointed_by=2),
            FieldRegionalAssignment(project_id=2, employee_id=4, kind="primary",
                                    active_employee_id=4, active_primary_project_id=2, appointed_by=2),
        ])
        await db.commit()

    active_user = 2

    async def override_db():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def override_user():
        async with session_factory() as db:
            return await db.get(Employee, active_user)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    payload = {
        "name": "项目轮休规则", "work_hour_type": "flexible", "work_hours_per_day": 8,
        "extra_config": {"rule_type": "free", "scope_mode": "all", "project_id": 999},
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            active_user = 3
            denied = await client.post("/api/v1/attendance/projects/2/rule", json=payload)
            assert denied.status_code == 403
            denied = await client.post("/api/v1/attendance/rules", json=payload)
            assert denied.status_code == 403

            active_user = 5
            denied = await client.post("/api/v1/attendance/projects/1/rule", json=payload)
            assert denied.status_code == 403

            active_user = 3
            created = await client.post("/api/v1/attendance/projects/1/rule", json=payload)
            assert created.status_code == 201, created.text
            first_rule = created.json()
            assert first_rule["extra_config"]["scope_mode"] == "project"
            assert first_rule["extra_config"]["project_id"] == 1
            assert first_rule["department_id"] is None
            assert first_rule["location_id"] is not None

            duplicate = await client.post("/api/v1/attendance/projects/1/rule", json=payload)
            assert duplicate.status_code == 409
            denied = await client.put("/api/v1/attendance/projects/2/rule", json={"name": "越权"})
            assert denied.status_code == 403
            denied = await client.put(f"/api/v1/attendance/rules/{first_rule['id']}", json={"name": "越权"})
            assert denied.status_code == 403

            revised = await client.put("/api/v1/attendance/projects/1/rule", json={
                "name": "甲项目新版规则", "department_id": 888,
                "location_id": None, "extra_config": {"scope_mode": "all", "project_id": 2},
            })
            assert revised.status_code == 200, revised.text
            assert revised.json()["id"] != first_rule["id"]
            assert revised.json()["department_id"] is None
            assert revised.json()["location_id"] == first_rule["location_id"]
            assert revised.json()["extra_config"]["scope_mode"] == "project"
            assert revised.json()["extra_config"]["project_id"] == 1

            active_user = 2
            manager_created = await client.post("/api/v1/attendance/projects/2/rule", json=payload)
            assert manager_created.status_code == 201, manager_created.text
            manager_revised = await client.put("/api/v1/attendance/projects/2/rule", json={"name": "经理设置"})
            assert manager_revised.status_code == 200, manager_revised.text
            denied = await client.post("/api/v1/attendance/rules", json=payload)
            assert denied.status_code == 403

        async with session_factory() as db:
            original = await db.get(AttendanceRule, first_rule["id"])
            project_one = await db.get(FieldSite, 1)
            project_two = await db.get(FieldSite, 2)
            assert original.name == "项目轮休规则"
            assert project_one.rule_id == revised.json()["id"]
            assert project_two.rule_id == manager_revised.json()["id"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()
