"""Project catalog geofences must be valid and versioned without stale sites."""

from datetime import date
from decimal import Decimal
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register all tables for isolated integration tests
from app.api.v1.attendance import _apply_catalog_to_project_rule_payload
from app.core.database import Base, get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.attendance import AttendanceRecord, AttendanceRule
from app.models.employee import Employee
from app.models.field_service import FieldCustomer, FieldProject, FieldProjectCatalog, FieldSite
from app.services.attendance import (
    AttendanceRecordService,
    AttendanceRuleService,
    _preserved_manual_locations,
    apply_catalog_location_to_project_rule,
    catalog_geofence,
)


def _rule(**kwargs):
    base = dict(
        require_gps=True, require_wifi=False, require_photo=False,
        gps_latitude=None, gps_longitude=None, gps_radius_meters=None,
        wifi_ssid=None, extra_config={},
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _workbook_with_moved_site():
    book = Workbook()
    sheet = book.active
    for column, label in ((3, "项目编号"), (4, "项目名称"),
                          (5, "地理位置"), (10, "是否需要定位")):
        sheet.cell(1, column, label)
    for column, value in ((3, "TG001"), (4, "虚构项目1"),
                          (5, "新虚构现场"), (10, "是")):
        sheet.cell(2, column, value)
    sheet.cell(2, 5).hyperlink = (
        "https://docs.qq.com/scenario/map.html?source=tencentmap"
        "&latitude=40.1&longitude=116.8"
    )
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def test_gps_rule_rejects_missing_or_invalid_geofence():
    for location in (None, {"latitude": "NaN", "longitude": 116.4, "radius": 300},
                     {"latitude": 39.9, "longitude": 116.4, "radius": 0}):
        payload = {"require_gps": True, "extra_config": {
            "locations": [location] if location else [], "check_methods": ["GPS"]}}
        with pytest.raises(ValueError, match="有效的打卡位置"):
            AttendanceRuleService._validate_gps_geofence(payload)
    AttendanceRuleService._validate_gps_geofence({
        "require_gps": True,
        "extra_config": {"locations": [{"latitude": 39.9, "longitude": 116.4, "radius": 300}]},
    })


def test_legacy_gps_rule_without_fence_cannot_pass_a_punch():
    errors = AttendanceRecordService._validate_clock(
        _rule(), Decimal("39.9"), Decimal("116.4"), None, None, False)
    assert "打卡规则缺少有效打卡范围" in errors
    # A configured Wi-Fi alternative cannot mask a broken GPS rule.
    errors = AttendanceRecordService._validate_clock(
        _rule(require_wifi=True, extra_config={"wifi_list": [{"name": "HQ"}]}),
        Decimal("39.9"), Decimal("116.4"), "HQ", None, False)
    assert "打卡规则缺少有效打卡范围" in errors


def test_no_gps_requirement_does_not_enforce_a_saved_location():
    rule = _rule(require_gps=False, extra_config={"locations": [
        {"latitude": 39.9, "longitude": 116.4, "radius": 300}]})
    assert AttendanceRecordService._validate_clock(
        rule, Decimal("30"), Decimal("120"), None, None, False) == []


def test_catalog_default_requires_gps_and_prefills_tencent_location():
    catalog = {"code": "TG001", "name": "项目一", "address": "北京现场",
               "latitude": 39.9, "longitude": 116.4, "require_gps": True, "radius_m": 300}
    location = catalog_geofence(catalog)
    assert location["source"] == "project_catalog"
    payload = {"require_gps": False, "gps_latitude": None, "gps_longitude": None,
               "extra_config": {"check_methods": []}}
    _apply_catalog_to_project_rule_payload(payload, catalog)
    assert payload["require_gps"] is True
    assert payload["extra_config"]["locations"] == [location]
    AttendanceRuleService._validate_gps_geofence(payload)


def test_missing_catalog_location_must_be_filled_before_rule_save():
    payload = {"require_gps": False, "extra_config": {"check_methods": []}}
    _apply_catalog_to_project_rule_payload(payload, {
        "code": "TG002", "name": "项目二", "address": "", "latitude": None,
        "longitude": None, "require_gps": True, "radius_m": 300,
    })
    with pytest.raises(ValueError, match="有效的打卡位置"):
        AttendanceRuleService._validate_gps_geofence(payload)


def test_legacy_primary_replacement_keeps_only_additional_manual_sites():
    rule = _rule(
        gps_latitude=Decimal("39.900000"), gps_longitude=Decimal("116.400000"),
        extra_config={"locations": [
            {"name": "旧主地点", "latitude": 39.9, "longitude": 116.4, "radius": 300},
            {"name": "手工第二地点", "latitude": 40.0, "longitude": 116.5, "radius": 200},
        ]},
    )
    assert [row["name"] for row in _preserved_manual_locations(rule)] == ["手工第二地点"]
    marked = _rule(extra_config={"locations": [
        {"name": "手工第二地点", "latitude": 40.0, "longitude": 116.5, "radius": 200},
        {"name": "表格地点", "latitude": 39.9, "longitude": 116.4,
         "radius": 450, "source": "project_catalog"},
    ]})
    assert [row["name"] for row in _preserved_manual_locations(marked)] == ["手工第二地点"]


@pytest.mark.asyncio
async def test_confirmed_import_versions_rule_and_removes_old_primary():
    source = _rule(
        id=10, name="旧规则", gps_latitude=Decimal("39.9"),
        gps_longitude=Decimal("116.4"), gps_radius_meters=450,
        extra_config={"check_methods": ["GPS"], "locations": [
            {"name": "旧主地点", "latitude": 39.9, "longitude": 116.4, "radius": 450},
            {"name": "手工第二地点", "latitude": 40.0, "longitude": 116.5, "radius": 200},
        ]},
    )
    site = SimpleNamespace(id=3, name="现场", address="旧址", location_id=9, rule_id=10)
    catalog = {"code": "TG001", "name": "项目一", "address": "新址",
               "latitude": 31.2, "longitude": 121.4, "require_gps": True, "radius_m": 300}
    user = SimpleNamespace(id=1)
    with (
        patch("app.services.field_roles.require_project_manage", AsyncMock(
            return_value=SimpleNamespace(code="TG001"))),
        patch("app.services.field_service.project_site", AsyncMock(return_value=site)),
        patch("app.services.field_service.save_site", AsyncMock()) as save_site,
        patch.object(AttendanceRuleService, "get", AsyncMock(return_value=source)),
        patch.object(AttendanceRuleService, "copy_rule", AsyncMock(return_value=SimpleNamespace(id=11))),
        patch.object(AttendanceRuleService, "update", AsyncMock(return_value=SimpleNamespace(id=11))) as update,
    ):
        await apply_catalog_location_to_project_rule(AsyncMock(), 4, catalog, user)
    saved = update.await_args.args[2]
    assert saved.require_gps is True
    assert saved.gps_latitude == Decimal("31.2")
    assert saved.gps_radius_meters == 450
    assert [loc["name"] for loc in saved.extra_config["locations"]] == ["新址", "手工第二地点"]
    assert saved.extra_config["locations"][0]["source"] == "project_catalog"
    assert save_site.await_args.args[-1] == 3


@pytest.mark.asyncio
async def test_project_rule_uses_catalog_and_requires_missing_location_to_be_filled():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(Employee(id=1, employee_no="ADMIN", name="管理员",
                        phone="19900001001", status="在职", is_superuser=True))
        db.add(Employee(id=2, employee_no="MEMBER", name="普通组员",
                        phone="19900001002", status="在职"))
        db.add(FieldCustomer(id=1, name="虚构客户"))
        await db.flush()
        for number, location, gps in ((1, (39.9, 116.4), True),
                                      (2, (None, None), True),
                                      (3, (None, None), False)):
            code = f"TG{number:03d}"
            db.add(FieldProject(id=number, code=code, name=f"虚构项目{number}",
                                customer_id=1, manager_id=1))
            db.add(FieldProjectCatalog(code=code, name=f"虚构项目{number}",
                                       address="虚构现场" if location[0] else "",
                                       latitude=location[0], longitude=location[1],
                                       require_gps=gps))
        await db.commit()

    async def override_db():
        async with sessions() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    user_id = 1

    async def override_user():
        async with sessions() as db:
            return await db.get(Employee, user_id)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            defaults = await client.get("/api/v1/attendance/projects/1/rule-defaults")
            assert defaults.status_code == 200, defaults.text
            assert defaults.json()["location"]["source"] == "project_catalog"
            assert defaults.json()["location"]["radius"] == 300
            missing = await client.get("/api/v1/attendance/projects/2/rule-defaults")
            assert missing.json()["needs_manual_location"] is True
            payload = {"name": "虚构固定规则", "work_hour_type": "standard",
                       "require_gps": False, "extra_config": {"check_methods": []}}
            created = await client.post("/api/v1/attendance/projects/1/rule", json=payload)
            assert created.status_code == 201, created.text
            assert created.json()["require_gps"] is True
            assert created.json()["extra_config"]["locations"][0]["latitude"] == 39.9
            blocked = await client.post("/api/v1/attendance/projects/2/rule", json=payload)
            assert blocked.status_code == 400, blocked.text
            assert "有效的打卡位置" in blocked.json()["detail"]
            manual = await client.post("/api/v1/attendance/projects/2/rule", json={
                **payload, "extra_config": {"check_methods": ["GPS"], "locations": [
                    {"name": "手动配置现场", "latitude": 31.2,
                     "longitude": 121.4, "radius": 300},
                ]},
            })
            assert manual.status_code == 201, manual.text
            async with sessions() as db:
                from sqlalchemy import select
                site = await db.scalar(select(FieldSite).where(FieldSite.project_id == 2))
                assert site.address == "手动配置现场"
            listed = await client.get("/api/v1/field/projects/catalog")
            manual_row = next(item for item in listed.json()["items"] if item["code"] == "TG002")
            assert manual_row["active_rule_requires_gps"] is True
            assert manual_row["active_rule_has_valid_geofence"] is True
            no_gps = await client.post("/api/v1/attendance/projects/3/rule", json={
                **payload, "require_gps": True,
                "gps_latitude": 39.9, "gps_longitude": 116.4,
                "extra_config": {"check_methods": ["GPS", "WiFi"]},
            })
            assert no_gps.status_code == 201, no_gps.text
            assert no_gps.json()["require_gps"] is False
            assert no_gps.json()["extra_config"]["check_methods"] == ["WiFi"]
            async with sessions() as db:
                db.add(AttendanceRecord(id=91, employee_id=1,
                                        date=date(2026, 1, 2),
                                        clock_in_location="历史实际地址"))
                await db.commit()
            changed_file = _workbook_with_moved_site()
            async def preview():
                return await client.post("/api/v1/field/projects/import/preview",
                                         files={"file": ("project.xlsx", changed_file)})
            pending = await preview()
            assert pending.status_code == 200, pending.text
            assert pending.json()["rows"][0]["active_rule_change"] is True
            no_approval = await client.post("/api/v1/field/projects/import/commit",
                files={"file": ("project.xlsx", changed_file)},
                data={"preview_token": pending.json()["preview_token"]})
            assert no_approval.status_code == 200, no_approval.text
            assert no_approval.json()["active_rules_updated"] == 0
            routine_edit = await client.put("/api/v1/attendance/projects/1/rule",
                                            json={"name": "改名不应用新位置"})
            assert routine_edit.status_code == 200, routine_edit.text
            assert routine_edit.json()["id"] != created.json()["id"]
            assert Decimal(str(routine_edit.json()["gps_latitude"])) == Decimal("39.9")
            assert routine_edit.json()["extra_config"]["locations"][0]["latitude"] == 39.9
            invalid_toggle = await client.put("/api/v1/attendance/projects/1/rule",
                                              json={"require_gps": False,
                                                    "extra_config": {"check_methods": []}})
            assert invalid_toggle.status_code == 409, invalid_toggle.text
            approved_preview = await preview()
            assert approved_preview.json()["rows"][0]["active_rule_change"] is True
            approved = await client.post("/api/v1/field/projects/import/commit",
                files={"file": ("project.xlsx", changed_file)},
                data={"preview_token": approved_preview.json()["preview_token"],
                      "confirm_active_rule_project_ids": "[1]"})
            assert approved.status_code == 200, approved.text
            assert approved.json()["active_rules_updated"] == 1
            current = await client.get("/api/v1/attendance/projects/1/rule")
            assert current.status_code == 200, current.text
            assert current.json()["id"] != routine_edit.json()["id"]
            assert Decimal(str(current.json()["gps_latitude"])) == Decimal("40.1")
            assert current.json()["extra_config"]["locations"][0]["source"] == "project_catalog"
            async with sessions() as db:
                assert (await db.get(AttendanceRule, created.json()["id"])).gps_latitude == Decimal("39.9")
                assert (await db.get(AttendanceRecord, 91)).clock_in_location == "历史实际地址"
            user_id = 2
            denied = await client.get("/api/v1/attendance/projects/1/rule-defaults")
            assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
