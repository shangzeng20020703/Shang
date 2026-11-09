"""Workbook import is reviewable, idempotent and independent of attendance rules."""

import io
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all foreign-key targets
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.employee import Employee
from app.models.attendance import AttendanceRule, WorkHourType
from app.models.field_service import FieldCustomer, FieldProject, FieldProjectCatalog, FieldSite
from app.models.organization import Location


def workbook(rows):
    book = Workbook()
    sheet = book.active
    for column, label in ((3, "项目编号"), (4, "项目名称"), (5, "地理位置"), (10, "是否需要定位")):
        sheet.cell(1, column, label)
    for index, (code, name, address, needs_gps, link) in enumerate(rows, 2):
        sheet.cell(index, 3, code)
        sheet.cell(index, 4, name)
        sheet.cell(index, 5, address)
        sheet.cell(index, 10, needs_gps)
        if link:
            sheet.cell(index, 5).hyperlink = link
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


LINK = "https://docs.qq.com/scenario/map.html?source=tencentmap&latitude=39.5&longitude=116.4"


@pytest.mark.asyncio
async def test_import_preview_commit_catalog_promotion_and_idempotence():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all([
            Employee(id=1, employee_no="ADMIN", name="管理员", phone="19900003001",
                     status="在职", is_superuser=True),
            Employee(id=2, employee_no="STAFF", name="组员", phone="19900003002", status="在职"),
            Employee(id=3, employee_no="LEADER", name="组长", phone="19900003003", status="在职"),
            FieldCustomer(id=1, name="测试客户"),
        ])
        await db.flush()
        db.add(FieldProject(id=1, code="TG001", name="旧项目名", customer_id=1, manager_id=3))
        await db.commit()

    user_id = 1

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
            return await db.get(Employee, user_id)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    data = workbook([
        ("TG001", "表格项目名", "测试现场", "是", LINK),
        ("TG002", "待配置项目", "", "是", None),
        ("MK003", "免定位项目", "", "否", None),
    ])
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def preview(blob=data):
                return await client.post("/api/v1/field/projects/import/preview",
                                         files={"file": ("locations.xlsx", blob)})

            response = await preview()
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["summary"]["total"] == 3
            assert payload["summary"]["requires_gps"] == 2
            assert payload["summary"]["missing_location"] == 1
            assert payload["summary"]["error"] == 0
            assert payload["rows"][0]["local_project_id"] == 1
            assert payload["rows"][0]["status"] == "create"
            assert payload["rows"][1]["warning"]
            async with sessions() as db:
                assert (await db.scalars(select(FieldProjectCatalog))).all() == []

            response = await client.post("/api/v1/field/projects/import/commit",
                files={"file": ("locations.xlsx", data)}, data={"preview_token": "wrong"})
            assert response.status_code == 409
            response = await client.post("/api/v1/field/projects/import/commit",
                files={"file": ("locations.xlsx", data)},
                data={"preview_token": payload["preview_token"]})
            assert response.status_code == 200, response.text
            assert response.json()["created"] == 3
            catalog = (await client.get("/api/v1/field/projects/catalog")).json()
            assert catalog["total"] == 3
            assert next(item for item in catalog["items"] if item["code"] == "TG002")["status"] == "staged"
            assert next(item for item in catalog["items"] if item["code"] == "TG001")["status"] == "pending_rule"
            async with sessions() as db:
                assert (await db.get(FieldProject, 1)).name == "表格项目名"

            repeated = (await preview()).json()
            assert repeated["summary"]["unchanged"] == 3
            response = await client.post("/api/v1/field/projects/import/commit",
                files={"file": ("locations.xlsx", data)},
                data={"preview_token": repeated["preview_token"]})
            assert response.status_code == 200 and response.json()["unchanged"] == 3
            assert (await client.get("/api/v1/field/projects/catalog")).json()["total"] == 3

            promoted = await client.post("/api/v1/field/projects/catalog/TG002/promote",
                                         json={"customer_id": 1, "manager_id": 3})
            assert promoted.status_code == 200, promoted.text
            assert promoted.json()["project_id"] != 1
            assert (await client.get("/api/v1/field/projects/catalog")).json()["total"] == 3

            user_id = 2
            assert (await client.get("/api/v1/field/projects/catalog")).json()["total"] == 0
            assert (await preview()).status_code == 403
            assert (await client.post("/api/v1/field/projects/catalog/TG002/promote",
                                      json={"customer_id": 1, "manager_id": 3})).status_code == 403
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_code_rejects_entire_batch():
    from app.services.field_project_import import parse_workbook
    data = workbook([
        ("TG001", "项目一", "", "否", None),
        ("TG001", "项目二", "", "否", None),
    ])
    rows = parse_workbook(data, "locations.xlsx")
    assert len(rows) == 2
    assert all("项目编号重复" in row["error"] for row in rows)


@pytest.mark.asyncio
async def test_imported_local_name_survives_tegene_link_and_refresh(monkeypatch):
    from app.schemas.field_service import TegeneProjectInput
    from app.services import tegene_projects

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    external_id = UUID(int=42)
    remote = {"id": str(external_id), "projectNo": "TG042", "name": "源系统名称",
              "customer": "源客户", "status": "active"}

    async def fake_projects():
        return "https://source.example/api", [dict(remote)]

    monkeypatch.setattr(tegene_projects, "configured", lambda: True)
    monkeypatch.setattr(tegene_projects, "fetch_projects", fake_projects)
    try:
        async with sessions() as db:
            admin = Employee(id=1, employee_no="ADMIN", name="管理员", phone="19900004001",
                             status="在职", is_superuser=True)
            leader = Employee(id=2, employee_no="LEADER", name="组长", phone="19900004002",
                              status="在职")
            db.add_all([admin, leader, FieldCustomer(id=1, name="本地客户")])
            await db.flush()
            db.add(FieldProject(id=1, code="TG042", name="临时本地名称",
                                customer_id=1, manager_id=2))
            db.add(FieldProjectCatalog(code="TG042", name="表格权威名称",
                                       address="测试地点", latitude=39.5,
                                       longitude=116.4, require_gps=True))
            await db.commit()

        async with sessions() as db:
            admin = await db.get(Employee, 1)
            listed = await tegene_projects.list_projects(db, admin)
            assert listed["projects"][0]["name"] == "表格权威名称"
            assert listed["projects"][0]["source_name"] == "源系统名称"
            linked = await tegene_projects.link_project(db, admin, external_id,
                                                         TegeneProjectInput(manager_id=2))
            assert linked["project_id"] == 1
            assert (await db.get(FieldProject, 1)).name == "表格权威名称"
            remote["name"] = "源系统再次更名"
            await tegene_projects.link_project(db, admin, external_id,
                                                TegeneProjectInput(manager_id=2))
            assert (await db.get(FieldProject, 1)).name == "表格权威名称"
            assert (await tegene_projects.list_projects(db, admin))["projects"][0]["source_name"] == "源系统再次更名"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reimport_keeps_offering_unapproved_active_rule_change():
    from fastapi import HTTPException
    from app.services.field_project_import import commit_import, list_catalog, preview_import

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    data = workbook([("TG007", "测试项目", "新现场", "是", LINK)])
    no_coords = workbook([("TG007", "测试项目", "", "是", None)])
    try:
        async with sessions() as db:
            admin = Employee(id=1, employee_no="ADMIN", name="管理员", phone="19900005001",
                             status="在职", is_superuser=True)
            db.add_all([admin, Employee(id=2, employee_no="LEAD", name="组长",
                                        phone="19900005002", status="在职"),
                        FieldCustomer(id=1, name="客户"), Location(id=1, name="旧现场")])
            await db.flush()
            db.add(FieldProject(id=1, code="TG007", name="旧名", customer_id=1, manager_id=2))
            db.add(AttendanceRule(id=1, name="旧规则", work_hour_type=WorkHourType.standard,
                                  require_gps=False, extra_config={}))
            await db.flush()
            db.add(FieldSite(id=1, project_id=1, name="旧名", address="旧地点",
                             location_id=1, rule_id=1, is_active=True))
            await db.flush()

            first = await preview_import(db, admin, data, "locations.xlsx")
            assert first["rows"][0]["active_rule_change"] is True
            await commit_import(db, admin, data, "locations.xlsx", first["preview_token"], [])
            assert (await db.get(AttendanceRule, 1)).require_gps is False
            assert (await db.get(FieldSite, 1)).address == "旧地点"

            again = await preview_import(db, admin, data, "locations.xlsx")
            assert again["rows"][0]["status"] == "unchanged"
            assert again["rows"][0]["active_rule_change"] is True
            listed = await list_catalog(db, admin)
            assert listed["items"][0]["rule_updates_pending"] is True

            missing = await preview_import(db, admin, no_coords, "locations.xlsx")
            with pytest.raises(HTTPException) as rejected:
                await commit_import(db, admin, no_coords, "locations.xlsx",
                                    missing["preview_token"], [1])
            assert rejected.value.status_code == 422
            assert (await db.get(AttendanceRule, 1)).require_gps is False
    finally:
        await engine.dispose()
