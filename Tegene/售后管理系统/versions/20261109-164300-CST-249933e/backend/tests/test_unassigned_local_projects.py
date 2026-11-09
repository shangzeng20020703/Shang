"""Fresh local projects may wait for a leader without granting candidate access."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all foreign-key targets
from app.core.config import settings
from app.core.database import Base
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import FieldCustomer, FieldProjectLeaderHistory
from app.schemas.field_service import ProjectConfigurationInput, ProjectInput
from app.services.field_roles import role_in_project
from app.services.field_service import configure_project, directory, save_project


@pytest.mark.asyncio
async def test_new_local_project_stays_unassigned_until_manager_appoints_leader(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "DATABASE_URL_OVERRIDE", "sqlite+aiosqlite:///:memory:")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            admin = Employee(id=1, employee_no="ADMIN", name="管理员", phone="19900000101",
                             status="在职", is_superuser=True)
            manager = Employee(id=2, employee_no="MGR", name="售后经理", phone="19900000102", status="在职")
            regional = Employee(id=3, employee_no="REG", name="区域组长", phone="19900000103", status="在职")
            candidate = Employee(id=4, employee_no="LEAD", name="待任命组长", phone="19900000104", status="在职")
            db.add_all([admin, manager, regional, candidate])
            await db.flush()
            db.add_all([
                EmployeeRole(employee_id=2, role_name="aftersales_manager", granted_by=1),
                EmployeeRole(employee_id=3, role_name="regional_lead", granted_by=1),
                FieldCustomer(id=1, name="模拟客户"),
            ])
            await db.flush()

            project = await save_project(db, manager, ProjectInput(
                code="UNASSIGNED-1", name="待分配项目", customer_id=1,
            ))
            assert project["manager_id"] is None
            pid = project["id"]
            region_view = await directory(db, regional)
            assert next(p for p in region_view["projects"] if p["id"] == pid)["can_manage"] is False
            assert await role_in_project(db, candidate, pid) == "none"

            saved = await configure_project(db, manager, pid, ProjectConfigurationInput(manager_id=4))
            assert saved["manager_id"] == 4
            assert await role_in_project(db, candidate, pid) == "project_leader"
            history = await db.scalar(select(FieldProjectLeaderHistory).where(
                FieldProjectLeaderHistory.project_id == pid))
            assert history.previous_employee_id is None and history.employee_id == 4

            with pytest.raises(HTTPException) as rejected:
                await save_project(db, manager, ProjectInput(
                    code="UNASSIGNED-1", name="待分配项目", customer_id=1,
                ), pid)
            assert rejected.value.status_code == 422

            monkeypatch.setattr(settings, "ENVIRONMENT", "production")
            with pytest.raises(HTTPException) as rejected:
                await save_project(db, manager, ProjectInput(
                    code="PROD-UNASSIGNED", name="不能无组长创建", customer_id=1,
                ))
            assert rejected.value.status_code == 422
    finally:
        await engine.dispose()
