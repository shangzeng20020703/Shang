"""Field selectors distinguish staff categories from project permissions."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register models for the isolated database
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import FieldCustomer, FieldProject, FieldRosterProfile


@pytest.mark.asyncio
async def test_demo_staff_categories_and_explicit_roster_override():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Employee(id=100, employee_no="ADMIN", name="管理员",
                        phone="19900009000", status="在职", is_superuser=True))
        for index in range(1, 20):
            role = "employee" if index <= 16 else ("regional_lead" if index <= 18 else "aftersales_manager")
            name = (f"测试组员{index:02d}" if index <= 12 else
                    f"测试组长{index - 12:02d}" if index <= 16 else
                    f"测试区域组长{index - 16:02d}" if index <= 18 else "测试售后经理")
            position = "项目组长候选" if 13 <= index <= 15 else "组员"
            db.add(Employee(id=index, employee_no=f"{index:03d}", name=name,
                            phone=f"19900009{index:03d}", status="在职",
                            is_field_staff=index <= 15, position=position))
            db.add(EmployeeRole(employee_id=index, role_name=role, granted_by=100))
        db.add(Employee(id=20, employee_no="020", name="原公司员工",
                        phone="19900009020", status="在职", is_field_staff=False))
        db.add(EmployeeRole(employee_id=20, role_name="employee", granted_by=100))
        await db.flush()
        db.add(FieldCustomer(id=1, name="虚构客户"))
        db.add(FieldProject(id=1, code="DEMO", name="虚构项目", customer_id=1,
                            manager_id=16))  # 016 is an incumbent without the old field-staff flag
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
            return await db.get(Employee, 100)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            directory = await client.get("/api/v1/field/directory")
            assert directory.status_code == 200, directory.text
            options = {row["employee_no"]: row for row in directory.json()["people"]}
            assert {key for key, row in options.items() if row["field_staff_category"] == "member"} == {
                f"{index:03d}" for index in range(1, 13)
            }
            assert {key for key, row in options.items() if row["field_staff_category"] == "leader_candidate"} == {
                f"{index:03d}" for index in range(13, 17)
            }
            assert all(options[f"{index:03d}"]["field_staff_category"] is None
                       for index in range(17, 20))
            assert options["017"]["role"] == "regional_lead"
            assert options["019"]["role"] == "aftersales_manager"
            assert options["020"]["field_staff_category"] is None

            roster = await client.get("/api/v1/field/people")
            assert roster.status_code == 200, roster.text
            roster_items = {row["employee_no"]: row for row in roster.json()}
            assert roster_items["013"]["field_staff_category"] == "leader_candidate"
            assert roster_items["016"]["field_staff_category"] == "leader_candidate"

            # An explicit category overrides a legacy position for an
            # unappointed candidate.
            edited = await client.put("/api/v1/field/people/13", json={
                "employee_no": "013", "name": "测试组长01", "phone": "19900009013",
                "position": "项目组长候选", "status": "在职", "role": "employee",
                "field_staff_category": "member",
            })
            assert edited.status_code == 200, edited.text
            rejected = await client.put("/api/v1/field/people/16", json={
                "employee_no": "016", "name": "测试组长04", "phone": "19900009016",
                "position": "组员", "status": "在职", "role": "employee",
                "field_staff_category": "member",
            })
            assert rejected.status_code == 409, rejected.text
            classified = await client.put("/api/v1/field/people/20", json={
                "employee_no": "020", "name": "原公司员工", "phone": "19900009020",
                "status": "在职", "role": "employee", "field_staff_category": "member",
            })
            assert classified.status_code == 200, classified.text
            roster = await client.get("/api/v1/field/people")
            roster_items = {row["employee_no"]: row for row in roster.json()}
            assert roster_items["013"]["field_staff_category"] == "member"
            assert roster_items["016"]["field_staff_category"] == "leader_candidate"
            assert roster_items["016"]["field_role"] == "project_leader"
            directory = await client.get("/api/v1/field/directory")
            options = {row["employee_no"]: row for row in directory.json()["people"]}
            assert options["013"]["field_staff_category"] == "member"
            assert options["016"]["field_staff_category"] == "leader_candidate"
            assert options["020"]["field_staff_category"] == "member"

        async with sessions() as db:
            profile = await db.get(FieldRosterProfile, 13)
            assert profile.details["field_staff_category"] == "member"
            assert (await db.get(Employee, 20)).is_field_staff is True
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()
