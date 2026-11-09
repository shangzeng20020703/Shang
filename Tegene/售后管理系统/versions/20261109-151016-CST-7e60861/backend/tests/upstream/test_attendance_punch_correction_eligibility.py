"""
补卡可用性内部 API 契约测试。

覆盖审批模块发起补卡审批前需要调用的只读资格计算能力。
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app
from app.models.attendance import AttendanceStatus, CorrectionStatus, ShiftType, WorkHourType
from app.schemas.attendance import PunchCorrectionEligibilityRequest
from app.services.attendance import AttendanceRuleService, PunchCorrectionEligibilityService


def _route_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def _rule(extra: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=5,
        name="东莞工厂标准工时",
        work_hour_type=WorkHourType.standard,
        clock_in_time=time(8, 0),
        clock_out_time=time(17, 30),
        flexible_minutes=5,
        extra_config=extra,
    )


def _runtime(rule: SimpleNamespace) -> dict:
    return {
        "rule": rule,
        "extra": rule.extra_config,
        "rule_type": "fixed",
        "expected_in": time(8, 0),
        "expected_out": time(17, 30),
        "check_in_required": True,
        "check_out_required": True,
        "flex_mode": "none",
        "early_threshold_minutes": 0,
        "segment": {
            "name": "白班",
            "key": "weekday-day-shift",
            "punch_start": "06:00",
            "punch_end": "22:00",
        },
        "shift": SimpleNamespace(id=456, shift_type=ShiftType.day_shift),
    }


def _extra(**overrides) -> dict:
    data = {
        "enable_patch_apply": True,
        "patch_types": ["缺卡/旷工", "迟到", "早退"],
        "patch_time_limit": "过去28天内",
        "patch_month_limit": "3次",
        "patch_deadline": "下月5日",
    }
    data.update(overrides)
    return data


class TestPunchCorrectionEligibilityContract:
    def test_internal_eligibility_endpoint_and_schema_are_registered(self) -> None:
        assert "/api/v1/attendance/internal/punch-correction/eligibility" in _route_paths()

        import app.schemas.attendance as attendance_schemas

        assert getattr(attendance_schemas, "PunchCorrectionEligibilityRequest", None) is not None
        assert getattr(attendance_schemas, "PunchCorrectionEligibilityResponse", None) is not None

    def test_target_date_parser_accepts_compact_date(self) -> None:
        assert PunchCorrectionEligibilityService.parse_target_date("20260428") == date(2026, 4, 28)

    def test_rule_patch_apply_binding_uses_patch_switch(self) -> None:
        rule = SimpleNamespace(is_active=True)
        extra = _extra(enable_patch_apply=True)

        assert AttendanceRuleService._approval_item_enabled(rule, "patch_apply", extra) is True
        assert AttendanceRuleService._approval_item_enabled(rule, "approve_punch", extra) is False
        assert "补卡申请" in AttendanceRuleService._approval_item_description("patch_apply", extra)


class TestPunchCorrectionEligibilityService:
    @pytest.mark.asyncio
    async def test_calculate_denies_when_rule_patch_apply_disabled(self) -> None:
        db = AsyncMock()
        employee = SimpleNamespace(id=123, location_id=None)
        target_date = date(2026, 4, 28)
        rule = _rule(_extra(enable_patch_apply=False))
        runtime = _runtime(rule)
        record = SimpleNamespace(
            id=987,
            employee_id=123,
            date=target_date,
            clock_in_time=None,
            clock_out_time=datetime(2026, 4, 28, 9, 30, tzinfo=timezone.utc),
            status=AttendanceStatus.missed_clock,
            correction_status=CorrectionStatus.none,
            anomaly_type="缺少上班打卡",
        )
        payload = PunchCorrectionEligibilityRequest(
            employee_id=123,
            target_date="2026-04-28",
            punch_types=["check_in"],
            include_window_days=False,
            timezone="Asia/Shanghai",
            request_at=datetime(2026, 4, 28, 10, 30, tzinfo=timezone(timedelta(hours=8))),
        )

        with patch(
            "app.services.attendance.AttendanceRecordService._get_rule_runtime",
            new=AsyncMock(return_value=runtime),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._count_month_corrections",
            new=AsyncMock(return_value=0),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._is_month_locked",
            new=AsyncMock(return_value=False),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._load_records_by_date",
            new=AsyncMock(return_value={target_date: record}),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._load_pending_corrections_by_slot",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._expected_work_day",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.services.attendance.WorkCalendarService.get_day_type",
            new=AsyncMock(return_value="workday"),
        ):
            result = await PunchCorrectionEligibilityService.calculate(
                db,
                payload,
                target_date=target_date,
                target_employee=employee,
            )

        assert result["biz_success"] is True
        assert result["biz_code"] == "PATCH_APPLY_DISABLED"
        assert result["data"]["can_apply"] is False
        assert result["data"]["policy"]["enable_patch_apply"] is False
        assert result["data"]["deny_reasons"][0]["code"] == "PATCH_APPLY_DISABLED"

    @pytest.mark.asyncio
    async def test_calculate_allows_missing_check_in_without_side_effects(self) -> None:
        db = AsyncMock()
        db.add = MagicMock()
        employee = SimpleNamespace(id=123, location_id=None)
        target_date = date(2026, 4, 28)
        rule = _rule(_extra())
        runtime = _runtime(rule)
        record = SimpleNamespace(
            id=987,
            employee_id=123,
            date=target_date,
            clock_in_time=None,
            clock_out_time=datetime(2026, 4, 28, 9, 30, tzinfo=timezone.utc),
            status=AttendanceStatus.missed_clock,
            correction_status=CorrectionStatus.none,
            anomaly_type="缺少上班打卡",
        )
        payload = PunchCorrectionEligibilityRequest(
            employee_id=123,
            target_date="2026-04-28",
            punch_types=["check_in"],
            include_window_days=False,
            timezone="Asia/Shanghai",
            request_at=datetime(2026, 4, 28, 10, 30, tzinfo=timezone(timedelta(hours=8))),
        )

        with patch(
            "app.services.attendance.AttendanceRecordService._get_rule_runtime",
            new=AsyncMock(return_value=runtime),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._count_month_corrections",
            new=AsyncMock(return_value=1),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._is_month_locked",
            new=AsyncMock(return_value=False),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._load_records_by_date",
            new=AsyncMock(return_value={target_date: record}),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._load_pending_corrections_by_slot",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._expected_work_day",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.services.attendance.WorkCalendarService.get_day_type",
            new=AsyncMock(return_value="workday"),
        ):
            result = await PunchCorrectionEligibilityService.calculate(
                db,
                payload,
                target_date=target_date,
                target_employee=employee,
            )

        assert result["biz_success"] is True
        assert result["biz_code"] == "OK"
        assert result["data"]["can_apply"] is True
        assert result["data"]["policy"]["used_count_in_month"] == 1
        assert result["data"]["policy"]["remaining_count_in_month"] == 2
        assert result["data"]["summary"]["eligible_punch_count"] == 1
        punch = result["data"]["days"][-1]["shifts"][0]["patchable_punches"][0]
        assert punch["punch_type"] == "check_in"
        assert punch["reason_code"] == "MISSING_CHECK_IN"
        assert punch["allowed_punch_time"]["start"] == "2026-04-28T06:00:00+08:00"
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_calculate_returns_business_denial_when_target_date_out_of_window(self) -> None:
        db = AsyncMock()
        employee = SimpleNamespace(id=123, location_id=None)
        target_date = date(2026, 3, 15)
        runtime = _runtime(_rule(_extra()))
        payload = PunchCorrectionEligibilityRequest(
            employee_id=123,
            target_date="2026-03-15",
            timezone="Asia/Shanghai",
            request_at=datetime(2026, 4, 28, 10, 30, tzinfo=timezone(timedelta(hours=8))),
        )

        with patch(
            "app.services.attendance.AttendanceRecordService._get_rule_runtime",
            new=AsyncMock(return_value=runtime),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._count_month_corrections",
            new=AsyncMock(return_value=0),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._is_month_locked",
            new=AsyncMock(return_value=False),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._load_records_by_date",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.services.attendance.PunchCorrectionEligibilityService._load_pending_corrections_by_slot",
            new=AsyncMock(return_value={}),
        ):
            result = await PunchCorrectionEligibilityService.calculate(
                db,
                payload,
                target_date=target_date,
                target_employee=employee,
            )

        assert result["biz_success"] is True
        assert result["biz_code"] == "DATE_OUT_OF_PATCH_WINDOW"
        assert result["data"]["can_apply"] is False
        assert result["data"]["summary"]["window_day_count"] == 28
        assert result["data"]["days"] == []
        assert result["data"]["deny_reasons"][0]["code"] == "DATE_OUT_OF_PATCH_WINDOW"
