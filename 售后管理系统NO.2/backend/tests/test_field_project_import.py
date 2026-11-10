"""Workbook import is reviewable, idempotent and independent of attendance rules."""

import io
from datetime import timedelta
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
from app.models.employee_role import EmployeeRole
from app.models.attendance import AttendanceRule, WorkHourType
from app.models.field_service import (
    FieldAssignment, FieldCustomer, FieldProject, FieldProjectCatalog, FieldProjectPendingMember,
    FieldRegionalAssignment, FieldSite,
)
from app.models.organization import Location
from app.services import rotation


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
            Employee(id=2, employee_no="STAFF", name="组员", phone="19900003002", status="在职",
                     is_field_staff=True),
            Employee(id=3, employee_no="LEADER", name="组长", phone="19900003003", status="在职",
                     is_field_staff=True, position="项目组长"),
            Employee(id=4, employee_no="REGIONAL", name="区域组长", phone="19900003004",
                     status="在职"),
            FieldCustomer(id=1, name="测试客户"),
        ])
        await db.flush()
        db.add(EmployeeRole(employee_id=4, role_name="regional_lead", granted_by=1))
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
            assert response.json()["projects_created"] == 2
            catalog = (await client.get("/api/v1/field/projects/catalog")).json()
            assert catalog["total"] == 3
            imported = next(item for item in catalog["items"] if item["code"] == "TG002")
            assert imported["status"] == "pending_personnel"
            assert imported["local_project_id"] is not None
            assert next(item for item in catalog["items"] if item["code"] == "TG001")["status"] == "pending_personnel"
            async with sessions() as db:
                assert (await db.get(FieldProject, 1)).name == "表格项目名"
                new_project = await db.get(FieldProject, imported["local_project_id"])
                assert new_project.customer_id is None and new_project.manager_id is None
                assert not (await db.scalars(select(FieldSite).where(
                    FieldSite.project_id == new_project.id))).all()
            directory = (await client.get("/api/v1/field/directory")).json()
            assert next(row for row in directory["projects"] if row["code"] == "TG002")["customer_name"] == ""

            repeated = (await preview()).json()
            assert repeated["summary"]["unchanged"] == 3
            response = await client.post("/api/v1/field/projects/import/commit",
                files={"file": ("locations.xlsx", data)},
                data={"preview_token": repeated["preview_token"]})
            assert response.status_code == 200 and response.json()["unchanged"] == 3
            assert response.json()["projects_created"] == 0
            assert (await client.get("/api/v1/field/projects/catalog")).json()["total"] == 3

            # The project has an ID before personnel/rules, so a manager can
            # appoint a regional lead and delegate the pending roster.
            assigned = await client.put(f"/api/v1/field/projects/{imported['local_project_id']}/regional-leads",
                                        json={"primary_lead_id": 4})
            assert assigned.status_code == 200, assigned.text
            user_id = 4
            roster = await client.put(f"/api/v1/field/projects/{imported['local_project_id']}/pending-roster",
                                      json={"manager_id": 3, "member_ids": [2]})
            assert roster.status_code == 200, roster.text
            assert roster.json()["pending_member_count"] == 2
            imported_after_roster = next(item for item in (await client.get(
                "/api/v1/field/projects/catalog")).json()["items"] if item["code"] == "TG002")
            assert imported_after_roster["status"] == "pending_rule"
            async with sessions() as db:
                assert len((await db.scalars(select(FieldRegionalAssignment).where(
                    FieldRegionalAssignment.project_id == imported["local_project_id"]))).all()) == 1
                assert len((await db.scalars(select(FieldProjectPendingMember).where(
                    FieldProjectPendingMember.project_id == imported["local_project_id"]))).all()) == 2
            assert (await client.post("/api/v1/field/projects/catalog/TG002/promote",
                                      json={"customer_id": 1, "manager_id": 3})).status_code == 403

            user_id = 1
            promoted = await client.post("/api/v1/field/projects/catalog/TG002/promote",
                                         json={"customer_id": 1, "manager_id": 3})
            assert promoted.status_code == 200, promoted.text
            assert promoted.json()["project_id"] == imported["local_project_id"]
            conflict = await client.post("/api/v1/field/projects/catalog/TG002/promote",
                                         json={"customer_id": 1, "manager_id": 4})
            assert conflict.status_code == 409
            cleared = await client.patch(f"/api/v1/field/projects/{imported['local_project_id']}/customer",
                                         json={"customer_id": None})
            assert cleared.status_code == 200 and cleared.json()["customer_name"] == ""
            refilled = await client.patch(f"/api/v1/field/projects/{imported['local_project_id']}/customer",
                                          json={"customer_id": 1})
            assert refilled.status_code == 200 and refilled.json()["customer_name"] == "测试客户"
            async with sessions() as db:
                assert (await db.get(FieldProject, imported["local_project_id"])).manager_id == 3
                assert len((await db.scalars(select(FieldProjectPendingMember).where(
                    FieldProjectPendingMember.project_id == imported["local_project_id"]))).all()) == 2
                assert not (await db.scalars(select(FieldSite).where(
                    FieldSite.project_id == imported["local_project_id"]))).all()
            start = rotation.today() + timedelta(days=1)
            plan = {"start_date": str(start), "end_date": str(start + timedelta(days=6)),
                    "cycle_start": str(start), "template": "six_one", "days": [],
                    "reason": "周期排班"}
            for employee_id in (2, 3):
                base = (f"/api/v1/rotation/projects/{imported['local_project_id']}"
                        f"/person-plans/{employee_id}")
                person_preview = await client.post(base + "/preview", json=plan)
                assert person_preview.status_code == 200, person_preview.text
                confirmed = await client.put(base + "/confirm", params={
                    "revision": person_preview.json()["revision"]}, json=plan)
                assert confirmed.status_code == 200, confirmed.text
                assert confirmed.json()["status"] == "pending_rule"
            directory = (await client.get("/api/v1/field/directory")).json()
            scheduled = next(row for row in directory["projects"] if row["code"] == "TG002")
            assert scheduled["pre_rule_schedule_ready"] is True
            assert scheduled["member_count"] == 0
            rule = {"name": "导入项目轮休规则", "work_hour_type": "flexible",
                    "work_hours_per_day": 12, "effective_date": str(start),
                    "extra_config": {"rule_type": "rotation", "free_work_hours_mode": "limit:12"}}
            missing_location = await client.post(
                f"/api/v1/attendance/projects/{imported['local_project_id']}/rule", json=rule)
            assert missing_location.status_code == 400
            assert "有效的打卡位置" in missing_location.text
            with_location = await client.post(
                f"/api/v1/attendance/projects/{imported['local_project_id']}/rule", json={
                    **rule, "extra_config": {**rule["extra_config"], "check_methods": ["GPS"],
                    "locations": [{"name": "手动配置现场", "latitude": 31.2,
                                   "longitude": 121.4, "radius": 300}]},
                })
            assert with_location.status_code == 201, with_location.text
            directory = (await client.get("/api/v1/field/directory")).json()
            activated = next(row for row in directory["projects"] if row["code"] == "TG002")
            assert activated["rule_id"] == with_location.json()["id"]
            assert activated["pending_member_count"] == 0
            async with sessions() as db:
                site = await db.scalar(select(FieldSite).where(
                    FieldSite.project_id == imported["local_project_id"]))
                assert site is not None
                assert len((await db.scalars(select(FieldAssignment).where(
                    FieldAssignment.site_id == site.id))).all()) == 2
            assert (await client.get("/api/v1/field/projects/catalog")).json()["total"] == 3

            user_id = 2
            visible = (await client.get("/api/v1/field/projects/catalog")).json()
            assert [row["code"] for row in visible["items"]] == ["TG002"]
            assert (await preview()).status_code == 403
            assert (await client.post("/api/v1/field/projects/catalog/TG002/promote",
                                      json={"customer_id": 1, "manager_id": 3})).status_code == 403
            assert (await client.patch(f"/api/v1/field/projects/{imported['local_project_id']}/customer",
                                       json={"customer_id": None})).status_code == 403
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
            db.add_all([admin, leader,
                        Employee(id=3, employee_no="OTHER", name="其他组长",
                                 phone="19900004003", status="在职")])
            await db.flush()
            db.add(FieldProject(id=1, code="TG042", name="临时本地名称",
                                customer_id=None, manager_id=None))
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
            assert linked["manager_id"] == 2 and linked["manager_id_matches_request"] is True
            assert (await db.get(FieldProject, 1)).name == "表格权威名称"
            assert (await db.get(FieldProject, 1)).manager_id == 2
            assert (await db.get(FieldProject, 1)).customer_id is not None
            mismatched = await tegene_projects.link_project(db, admin, external_id,
                                                             TegeneProjectInput(manager_id=3))
            assert mismatched["manager_id"] == 2 and mismatched["manager_id_matches_request"] is False
            assert (await db.get(FieldProject, 1)).manager_id == 2
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
