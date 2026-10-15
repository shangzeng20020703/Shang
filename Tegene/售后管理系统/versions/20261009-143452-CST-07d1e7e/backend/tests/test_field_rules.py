from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.models.attendance import ShiftSchedule, AttendanceRule, WorkHourType
from app.services.attendance import AttendanceRecordService
from app.schemas.field_service import AssignmentInput, PersonInput


@pytest.mark.asyncio
async def test_night_shift_checkout_stays_on_previous_workday():
    previous = date(2026, 9, 28)
    now = datetime(2026, 9, 29, 7, tzinfo=timezone(timedelta(hours=8)))
    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    db.scalar.return_value = ShiftSchedule(
        date=previous, start_time=time(20), end_time=time(8)
    )
    current = {"rule_type": "fixed"}
    old = {"rule_type": "fixed", "night": True}
    with patch.object(
        AttendanceRecordService,
        "_get_rule_runtime",
        AsyncMock(side_effect=[current, old]),
    ):
        day, runtime = await AttendanceRecordService._resolve_punch_target_date(
            db, 3, now
        )
    assert day == previous and runtime is old


@pytest.mark.asyncio
async def test_previous_night_does_not_capture_next_evening():
    now = datetime(2026, 9, 29, 19, tzinfo=timezone(timedelta(hours=8)))
    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    db.scalar.return_value = ShiftSchedule(
        date=now.date() - timedelta(days=1), start_time=time(20), end_time=time(8)
    )
    current = {"rule_type": "fixed"}
    with patch.object(
        AttendanceRecordService, "_get_rule_runtime", AsyncMock(return_value=current)
    ):
        day, runtime = await AttendanceRecordService._resolve_punch_target_date(
            db, 3, now
        )
    assert day == now.date() and runtime is current


def test_assignment_inverted_or_unbounded_range_rejected():
    for end in ["2026-09-28", "2028-09-29"]:
        with pytest.raises(ValueError):
            AssignmentInput(
                site_id=1, employee_id=2, start_date="2026-09-29", end_date=end
            )


def test_password_and_role_validation():
    for values in [{"password": "123456"}, {"role": "finance"}]:
        with pytest.raises(ValueError):
            PersonInput(name="测试", employee_no="T", phone="10000000001", **values)


def test_photo_rule_is_independent_of_gps_rule():
    rule = SimpleNamespace(
        require_gps=False,
        require_wifi=False,
        require_photo=True,
        extra_config={},
        gps_latitude=None,
        gps_longitude=None,
        gps_radius_meters=None,
        wifi_ssid=None,
    )
    assert "缺少打卡照片" in AttendanceRecordService._validate_clock(
        rule, None, None, None, None, False
    )
    assert (
        AttendanceRecordService._validate_clock(
            rule, None, None, None, "/uploads/attendance/real.jpg", False
        )
        == []
    )


def test_normal_punch_does_not_require_outside_only_photo():
    rule = SimpleNamespace(
        require_gps=True,
        require_wifi=False,
        require_photo=True,  # Old saved value combined both photo settings.
        extra_config={
            "photo_each_punch": False,
            "watermark_photo_required": True,
            "locations": [{"latitude": 39.9, "longitude": 116.4, "radius": 300}],
        },
        gps_latitude=Decimal("39.9"),
        gps_longitude=Decimal("116.4"),
        gps_radius_meters=300,
        wifi_ssid=None,
    )
    assert AttendanceRecordService._validate_clock(
        rule, Decimal("39.9"), Decimal("116.4"), None, None, False
    ) == []
    rule.extra_config["photo_each_punch"] = True
    assert AttendanceRecordService._validate_clock(
        rule, Decimal("39.9"), Decimal("116.4"), None, None, False
    ) == ["缺少打卡照片"]


@pytest.mark.asyncio
async def test_map_service_secret_is_not_returned_as_browser_key(monkeypatch):
    from app.core.config import settings
    from app.api.v1.attendance import get_tencent_web_sdk_config

    monkeypatch.setattr(settings, "TENCENT_MAP_WEB_SERVICE_KEY", "private-service-key")
    monkeypatch.setattr(settings, "TENCENT_MAP_BROWSER_KEY", None)
    db = AsyncMock()
    db.get.return_value = None
    result = await get_tencent_web_sdk_config(db, SimpleNamespace(id=1))
    assert not result["enabled"] and result["key"] == ""


@pytest.mark.asyncio
async def test_field_audit_records_changed_fields_without_password():
    from app.services.field_service import audited
    from app.models.field_service import FieldRosterProfile
    row = SimpleNamespace(id=8, name="原姓名", employee_no="T8", phone="13800000000",
                          position="", department_id=None, direct_manager_id=None,
                          is_active=True, status="在职")
    db = AsyncMock()
    db.add = MagicMock()
    db.get.side_effect = lambda model, item_id: None if model is FieldRosterProfile else row
    db.scalar.return_value = "member8"
    async def save_person(db, user, data, item_id=None):
        row.name = data.name
        return {"id": row.id, "name": row.name}
    data = PersonInput(name="新姓名", employee_no="T8", phone=row.phone, password="private-password")
    with patch('app.services.field_service.roles', AsyncMock(return_value={"employee"})):
        await audited("save", save_person)(db, SimpleNamespace(id=1, name="管理员"), data, 8)
    import json
    log = db.add.call_args.args[0]
    assert json.loads(log.before_data)["name"] == "原姓名"
    assert json.loads(log.after_data)["name"] == "新姓名"
    assert json.loads(log.after_data)["password_changed"] is True
    assert "private-password" not in log.after_data + log.before_data


@pytest.mark.asyncio
async def test_import_preview_does_not_create_audit_change():
    from app.services.field_service import audited
    db = AsyncMock()
    db.add = MagicMock()
    async def import_members(db, user):
        return {"preview": True, "success": 3}
    await audited("import-members", import_members)(db, SimpleNamespace(id=1, name="管理员"))
    db.add.assert_not_called()
