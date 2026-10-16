"""A manager can assign a primary regional lead and project members without extra form fields."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all isolated SQLite tables
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import (
    FieldAssignment,
    FieldCustomer,
    FieldMembership,
    FieldProject,
    FieldRegionalAssignment,
)


@pytest.mark.asyncio
async def test_manager_assigns_primary_only_then_adds_members_after_rule():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as db:
        db.add_all([
            Employee(id=1, employee_no="ADMIN", name="管理员", phone="19900000201",
                     status="在职", is_superuser=True),
            Employee(id=2, employee_no="MGR", name="售后经理", phone="19900000202", status="在职"),
            Employee(id=3, employee_no="REG", name="区域组长", phone="19900000203", status="在职"),
            Employee(id=4, employee_no="MEM", name="组员", phone="19900000204", status="在职",
                     is_field_staff=True),
        ])
        await db.flush()
        db.add_all([
            EmployeeRole(employee_id=2, role_name="aftersales_manager", granted_by=1),
            EmployeeRole(employee_id=3, role_name="regional_lead", granted_by=1),
            FieldCustomer(id=1, name="虚构客户"),
        ])
        await db.flush()
        db.add(FieldProject(id=1, code="MANAGER-P1", name="经理配置项目", customer_id=1))
        await db.commit()

    async def override_db():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def override_user():
        async with sessions() as db:
            return await db.get(Employee, 2)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            # The optional assistant list and reason are intentionally omitted.
            response = await client.put("/api/v1/field/projects/1/regional-leads",
                                        json={"primary_lead_id": 3})
            assert response.status_code == 200, response.text
            assert response.json() == {"project_id": 1, "primary_lead_id": 3,
                                       "assistant_lead_ids": []}

            # Staff are visible as a pending roster before a project rule exists.
            response = await client.post("/api/v1/field/projects/1/members",
                                         json={"employee_ids": [4]})
            assert response.status_code == 200, response.text
            assert response.json()["members"] == []
            assert [item["employee_id"] for item in response.json()["pending_members"]] == [4]

            pending_view = await client.get("/api/v1/field/directory")
            assert [item["employee_id"] for item in pending_view.json()["pending_members"]] == [4]

            response = await client.post("/api/v1/attendance/projects/1/rule", json={
                "name": "项目自由打卡", "work_hour_type": "flexible", "work_hours_per_day": 8,
                "extra_config": {"rule_type": "free", "scope_mode": "project", "project_id": 1},
            })
            assert response.status_code == 201, response.text

            response = await client.post("/api/v1/field/projects/1/members/activate")
            assert response.status_code == 200, response.text
            # Rule save already attempted activation, so retry is idempotent.
            assert response.json()["pending_members"] == []

        async with sessions() as db:
            regional = (await db.scalars(select(FieldRegionalAssignment).where(
                FieldRegionalAssignment.project_id == 1,
                FieldRegionalAssignment.active_employee_id.is_not(None),
            ))).all()
            assert len(regional) == 1
            assert regional[0].employee_id == 3 and regional[0].kind == "primary"
            assert regional[0].reason == ""
            assignment = await db.scalar(select(FieldAssignment).where(
                FieldAssignment.employee_id == 4))
            assert assignment is not None and assignment.created_by == 2
            membership = await db.scalar(select(FieldMembership).where(
                FieldMembership.assignment_id == assignment.id))
            assert membership is not None and membership.ended_at is None
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()
