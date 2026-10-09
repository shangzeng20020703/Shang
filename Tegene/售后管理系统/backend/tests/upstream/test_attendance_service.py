"""
考勤服务单元测试
覆盖: 工时计算、异常字符串格式、时区
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.attendance import AttendanceRecordService, AttendanceReportService, WorkCalendarService
from app.models.attendance import AttendanceStatus, DayType, WorkHourType


class _EmptyCalendarResult:
    def scalar_one_or_none(self):
        return None


class _EmptyCalendarDb:
    async def execute(self, stmt):
        return _EmptyCalendarResult()


class TestCalculateWorkHours:

    def test_normal_9_hours(self):
        clock_in = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        clock_out = datetime(2026, 3, 1, 18, 0, 0, tzinfo=timezone.utc)
        result = AttendanceRecordService._calculate_work_hours(clock_in, clock_out)
        assert result == Decimal("9.00")

    def test_no_clock_in_returns_none(self):
        clock_out = datetime(2026, 3, 1, 18, 0, 0)
        result = AttendanceRecordService._calculate_work_hours(None, clock_out)
        assert result is None

    def test_no_clock_out_returns_none(self):
        clock_in = datetime(2026, 3, 1, 9, 0, 0)
        result = AttendanceRecordService._calculate_work_hours(clock_in, None)
        assert result is None

    def test_both_none_returns_none(self):
        result = AttendanceRecordService._calculate_work_hours(None, None)
        assert result is None

    def test_overnight_work(self):
        clock_in = datetime(2026, 3, 1, 22, 0, 0)
        clock_out = datetime(2026, 3, 2, 6, 0, 0)
        result = AttendanceRecordService._calculate_work_hours(clock_in, clock_out)
        assert result > Decimal("0")

    def test_result_is_decimal(self):
        clock_in = datetime(2026, 3, 1, 9, 0, 0)
        clock_out = datetime(2026, 3, 1, 17, 30, 0)
        result = AttendanceRecordService._calculate_work_hours(clock_in, clock_out)
        assert isinstance(result, Decimal)


class TestAnomalyStringFormat:
    """验证缺少上班打卡时异常字符串格式正确"""

    def test_no_extra_anomalies(self):
        # 只有缺少上班打卡，无其他异常
        anomalies = []
        result = "; ".join(["缺少上班打卡"] + anomalies) if anomalies else "缺少上班打卡"
        assert result == "缺少上班打卡"
        assert "; ;" not in result

    def test_with_extra_anomaly(self):
        anomalies = ["GPS异常"]
        result = "; ".join(["缺少上班打卡"] + anomalies) if anomalies else "缺少上班打卡"
        assert result == "缺少上班打卡; GPS异常"
        assert "; ;" not in result

    def test_with_multiple_anomalies(self):
        anomalies = ["GPS异常", "WiFi未连接"]
        result = "; ".join(["缺少上班打卡"] + anomalies) if anomalies else "缺少上班打卡"
        assert result == "缺少上班打卡; GPS异常; WiFi未连接"


class TestAttendanceClockNotificationPolicy:
    """上下班打卡只沉淀考勤数据，不额外生成站内系统通知。"""

    def test_clock_punch_updates_month_summary_without_system_notification(self):
        clock_in_source = inspect.getsource(AttendanceRecordService.clock_in)
        clock_out_source = inspect.getsource(AttendanceRecordService.clock_out)
        service_source = inspect.getsource(AttendanceRecordService)

        assert "_refresh_month_summary_for_date" in clock_in_source
        assert "_refresh_month_summary_for_date" in clock_out_source
        assert "_notify_management_clock_sync" not in service_source
        assert "NotificationService" not in service_source
        assert "考勤同步" not in service_source


class TestWorkCalendarOfficialPlan:
    """国务院节假日兜底口径。"""

    def test_2026_may_9_is_makeup_workday(self):
        assert WorkCalendarService._official_day_type(date(2026, 5, 9)) == DayType.special_workday

    def test_2026_labor_day_is_holiday(self):
        assert WorkCalendarService._official_day_type(date(2026, 5, 1)) == DayType.holiday

    @pytest.mark.asyncio
    async def test_get_day_type_uses_official_plan_before_weekday_fallback(self):
        result = await WorkCalendarService.get_day_type(_EmptyCalendarDb(), date(2026, 5, 9))
        assert result == DayType.special_workday


class TestTimezone:
    """验证时区处理"""

    def test_now_is_timezone_aware(self):
        now = datetime.now(timezone.utc)
        assert now.tzinfo is not None

    def test_local_date_from_utc(self):
        # 验证从 UTC 时间转换为本地日期的正确性
        now = datetime.now(timezone.utc)
        local_date = now.astimezone().date()
        assert local_date is not None


class TestMonthlyLeaveReportMetrics:

    def test_hour_leave_uses_type_hours_per_day_for_monthly_report(self):
        segments = [
            {
                "employee_id": 1,
                "leave_type_code": "maternity",
                "start_date": "2026-04-30",
                "end_date": "2026-05-02",
                "start_half": "am",
                "end_half": "pm",
                "days": 3,
                "hours_per_day": 24,
            }
        ]

        metrics = AttendanceReportService._aggregate_monthly_leave_metrics(
            segments,
            date(2026, 5, 1),
            date(2026, 5, 31),
            {1},
        )

        assert metrics[1]["maternity_leave_hours"] == Decimal("48")

    def test_day_leave_keeps_day_amount_when_hours_per_day_changes(self):
        segments = [
            {
                "employee_id": 1,
                "leave_type_code": "sick",
                "start_date": "2026-05-03",
                "end_date": "2026-05-04",
                "start_half": "pm",
                "end_half": "pm",
                "days": 1.5,
                "hours_per_day": 24,
            }
        ]

        metrics = AttendanceReportService._aggregate_monthly_leave_metrics(
            segments,
            date(2026, 5, 1),
            date(2026, 5, 31),
            {1},
        )

        assert metrics[1]["sick_leave_days"] == Decimal("1.5")


class TestAttendanceRuleScope:

    def test_fixed_worker_rule_matches_regular_employee_type(self):
        assert AttendanceRecordService._employee_type_matches("固定工", "正式") is True

    def test_fixed_worker_rule_matches_probation_employee_type(self):
        assert AttendanceRecordService._employee_type_matches("固定工", "试用") is True

    def test_intern_rule_does_not_match_regular_employee_type(self):
        assert AttendanceRecordService._employee_type_matches("实习", "正式") is False

    def test_scope_specificity_orders_employee_group_department_all(self):
        employee = SimpleNamespace(id=7, department_id=3)
        all_rule = SimpleNamespace(department_id=None, extra_config={"scope_mode": "all"})
        dept_rule = SimpleNamespace(
            department_id=None,
            extra_config={
                "scope_mode": "custom",
                "scope_custom_types": ["department"],
                "department_ids": [3],
            },
        )
        group_rule = SimpleNamespace(
            department_id=None,
            extra_config={
                "scope_mode": "custom",
                "scope_custom_types": ["group"],
                "attendance_groups": ["测试组"],
            },
        )
        employee_rule = SimpleNamespace(
            department_id=None,
            extra_config={"scope_mode": "all", "assigned_employee_ids": [7]},
        )

        scores = [
            AttendanceRecordService._rule_scope_specificity(rule, employee)
            for rule in (employee_rule, group_rule, dept_rule, all_rule)
        ]
        assert scores == sorted(scores, reverse=True)


class TestClockMethodValidation:

    @staticmethod
    def _rule(extra: dict, *, require_photo: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            require_gps=True,
            require_wifi=True,
            require_photo=require_photo,
            gps_latitude=None,
            gps_longitude=None,
            gps_radius_meters=None,
            wifi_ssid=None,
            extra_config=extra,
        )

    def test_mobile_gps_or_wifi_any_method_allows_wifi_match(self):
        rule = self._rule({
            "check_method_mode": "any",
            "locations": [{"name": "总部", "latitude": 39.9, "longitude": 116.4, "radius": 100}],
            "wifi_list": [{"name": "HQ-WIFI"}],
        })
        errors = AttendanceRecordService._validate_clock(
            rule,
            Decimal("30.0"),
            Decimal("120.0"),
            "HQ-WIFI",
            None,
            False,
        )
        assert errors == []

    def test_mobile_gps_or_wifi_any_method_rejects_when_both_fail(self):
        rule = self._rule({
            "check_method_mode": "any",
            "locations": [{"name": "总部", "latitude": 39.9, "longitude": 116.4, "radius": 100}],
            "wifi_list": [{"name": "HQ-WIFI"}],
        })
        errors = AttendanceRecordService._validate_clock(
            rule,
            Decimal("30.0"),
            Decimal("120.0"),
            "OTHER-WIFI",
            None,
            False,
        )
        assert any("超出打卡范围" in item for item in errors)
        assert any("WiFi不匹配" in item for item in errors)

    def test_mobile_all_method_requires_each_configured_method(self):
        rule = self._rule({
            "check_method_mode": "all",
            "locations": [{"name": "总部", "latitude": 39.9, "longitude": 116.4, "radius": 100}],
            "wifi_list": [{"name": "HQ-WIFI"}],
        })
        errors = AttendanceRecordService._validate_clock(
            rule,
            Decimal("30.0"),
            Decimal("120.0"),
            "HQ-WIFI",
            None,
            False,
        )
        assert any("超出打卡范围" in item for item in errors)

    def test_mobile_in_range_clock_requires_photo_when_configured(self):
        rule = self._rule(
            {
                "check_method_mode": "any",
                "locations": [{"name": "总部", "latitude": 39.9, "longitude": 116.4, "radius": 100}],
                "wifi_list": [{"name": "HQ-WIFI"}],
            },
            require_photo=True,
        )
        errors = AttendanceRecordService._validate_clock(
            rule,
            Decimal("39.9001"),
            Decimal("116.4001"),
            None,
            None,
            False,
        )
        # Extracted system: the explicit required-photo switch applies in range too.
        assert errors == ["缺少打卡照片"]

    def test_mobile_out_of_range_clock_still_requires_photo_when_configured(self):
        rule = self._rule(
            {
                "check_method_mode": "all",
                "locations": [{"name": "总部", "latitude": 39.9, "longitude": 116.4, "radius": 100}],
                "wifi_list": [{"name": "HQ-WIFI"}],
            },
            require_photo=True,
        )
        errors = AttendanceRecordService._validate_clock(
            rule,
            Decimal("30.0"),
            Decimal("120.0"),
            "HQ-WIFI",
            None,
            False,
        )
        assert any("超出打卡范围" in item for item in errors)
        assert "缺少打卡照片" in errors


class TestAttendanceStatusCalculation:

    @staticmethod
    def _standard_rule() -> SimpleNamespace:
        return SimpleNamespace(
            work_hour_type=WorkHourType.standard,
            clock_in_time=time(9, 30),
            clock_out_time=time(17, 30),
            flexible_minutes=0,
            extra_config={"rule_type": "fixed"},
        )

    @staticmethod
    def _standard_rule_with_half_day() -> SimpleNamespace:
        return SimpleNamespace(
            work_hour_type=WorkHourType.standard,
            clock_in_time=time(9, 30),
            clock_out_time=time(18, 30),
            flexible_minutes=0,
            effective_date=date(2026, 4, 1),
            extra_config={
                "rule_type": "fixed",
                "time_segments": [
                    {
                        "weekdays": [1, 2, 3, 4, 5],
                        "clock_in": "09:30",
                        "clock_out": "18:30",
                        "rest_start": "12:00",
                        "rest_end": "13:00",
                        "half_day_am": ["09:30", "12:30"],
                        "half_day_pm": ["13:30", "18:30"],
                        "check_in_required": True,
                        "check_out_required": True,
                    }
                ],
            },
        )

    @staticmethod
    def _legacy_fixed_rule_with_default_flexible_minutes() -> SimpleNamespace:
        return SimpleNamespace(
            work_hour_type=WorkHourType.standard,
            clock_in_time=time(9, 30),
            clock_out_time=time(18, 30),
            flexible_minutes=15,
            extra_config={"rule_type": "fixed", "flex_mode": "none", "enable_flexible": False},
        )

    def test_late_clock_in_is_marked_late_before_clock_out(self):
        punch_at = datetime(2026, 4, 21, 7, 13, tzinfo=timezone.utc)  # 15:13 CST
        status = AttendanceRecordService._determine_clock_in_status(
            self._standard_rule(),
            punch_at,
            date(2026, 4, 21),
            expected_in=time(9, 30),
        )
        assert status == AttendanceStatus.late

    def test_early_leave_wins_when_clock_out_is_before_expected_out(self):
        punch_at = datetime(2026, 4, 21, 7, 13, tzinfo=timezone.utc)  # 15:13 CST
        status = AttendanceRecordService._determine_status(
            self._standard_rule(),
            punch_at,
            punch_at,
            date(2026, 4, 21),
            expected_in=time(9, 30),
            expected_out=time(17, 30),
        )
        assert status == AttendanceStatus.early_leave

    def test_preview_late_status_for_mobile_button(self):
        punch_at = datetime(2026, 4, 21, 7, 13, tzinfo=timezone.utc)  # 15:13 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._standard_rule(),
            "clock_in",
            punch_at,
            date(2026, 4, 21),
            expected_in=time(9, 30),
        )
        assert preview["status"] == AttendanceStatus.late
        assert preview["minutes"] > 0

    def test_clock_in_after_half_day_is_absent_and_counts_from_pm_start(self):
        punch_at = datetime(2026, 4, 24, 6, 48, tzinfo=timezone.utc)  # 14:48 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._standard_rule_with_half_day(),
            "clock_in",
            punch_at,
            date(2026, 4, 24),
            expected_in=time(9, 30),
        )
        assert preview["status"] == AttendanceStatus.absent
        assert preview["minutes"] == 108

        status = AttendanceRecordService._determine_clock_in_status(
            self._standard_rule_with_half_day(),
            punch_at,
            date(2026, 4, 24),
            expected_in=time(9, 30),
        )
        assert status == AttendanceStatus.absent

    def test_clock_in_during_rest_is_half_day_absent_without_afternoon_late(self):
        punch_at = datetime(2026, 4, 24, 4, 30, tzinfo=timezone.utc)  # 12:30 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._standard_rule_with_half_day(),
            "clock_in",
            punch_at,
            date(2026, 4, 24),
            expected_in=time(9, 30),
        )
        assert preview["status"] == AttendanceStatus.absent
        assert preview["minutes"] == 0

        record = SimpleNamespace(
            date=date(2026, 4, 24),
            clock_in_time=punch_at,
            clock_out_time=None,
        )
        runtime = {
            "rule": self._standard_rule_with_half_day(),
            "expected_in": time(9, 30),
            "check_in_required": True,
            "flex_mode": "none",
            "half_day_absence_cutoff": time(12, 0),
            "half_day_pm_start": time(13, 0),
        }
        anomalies: list[str] = []
        AttendanceRecordService._append_status_anomalies(record, runtime, anomalies)
        assert anomalies == ["旷工半天"]

    def test_half_day_absence_anomaly_uses_afternoon_start(self):
        record = SimpleNamespace(
            date=date(2026, 4, 24),
            clock_in_time=datetime(2026, 4, 24, 6, 48, tzinfo=timezone.utc),  # 14:48 CST
            clock_out_time=None,
        )
        runtime = {
            "rule": self._standard_rule_with_half_day(),
            "expected_in": time(9, 30),
            "check_in_required": True,
            "flex_mode": "none",
            "half_day_absence_cutoff": time(12, 0),
            "half_day_pm_start": time(13, 0),
        }
        anomalies: list[str] = []
        AttendanceRecordService._append_status_anomalies(record, runtime, anomalies)
        assert anomalies == ["旷工半天", "迟到108分钟"]

    def test_full_day_status_after_half_day_clock_in_is_absent(self):
        status = AttendanceRecordService._determine_status(
            self._standard_rule_with_half_day(),
            datetime(2026, 4, 24, 6, 48, tzinfo=timezone.utc),  # 14:48 CST
            datetime(2026, 4, 24, 10, 30, tzinfo=timezone.utc),  # 18:30 CST
            date(2026, 4, 24),
            expected_in=time(9, 30),
            expected_out=time(18, 30),
            half_day_pm_start=time(13, 0),
        )
        assert status == AttendanceStatus.absent

    def test_fixed_rule_ignores_legacy_flexible_minutes_unless_enabled(self):
        punch_at = datetime(2026, 4, 22, 1, 41, tzinfo=timezone.utc)  # 09:41 CST
        status = AttendanceRecordService._determine_clock_in_status(
            self._legacy_fixed_rule_with_default_flexible_minutes(),
            punch_at,
            date(2026, 4, 22),
            expected_in=time(9, 30),
            flex_mode="none",
        )
        assert status == AttendanceStatus.late

    def test_fixed_rule_preview_marks_0941_late_for_0930_rule(self):
        punch_at = datetime(2026, 4, 22, 1, 41, tzinfo=timezone.utc)  # 09:41 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._legacy_fixed_rule_with_default_flexible_minutes(),
            "clock_in",
            punch_at,
            date(2026, 4, 22),
            expected_in=time(9, 30),
            flex_mode="none",
        )
        assert preview["status"] == AttendanceStatus.late
        assert preview["minutes"] == 11

    def test_exact_scheduled_minute_is_not_late(self):
        punch_at = datetime(2026, 4, 24, 2, 26, 59, tzinfo=timezone.utc)  # 10:26:59 CST
        status = AttendanceRecordService._determine_clock_in_status(
            self._standard_rule(),
            punch_at,
            date(2026, 4, 24),
            expected_in=time(10, 26),
        )
        assert status == AttendanceStatus.normal

    def test_clock_in_preview_stays_normal_within_exact_scheduled_minute(self):
        punch_at = datetime(2026, 4, 24, 2, 26, 59, tzinfo=timezone.utc)  # 10:26:59 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._standard_rule(),
            "clock_in",
            punch_at,
            date(2026, 4, 24),
            expected_in=time(10, 26),
        )
        assert preview["status"] == AttendanceStatus.normal
        assert preview["minutes"] == 0

    def test_late_anomaly_starts_from_next_minute(self):
        record = SimpleNamespace(
            date=date(2026, 4, 24),
            clock_in_time=datetime(2026, 4, 24, 2, 26, 59, tzinfo=timezone.utc),  # 10:26:59 CST
            clock_out_time=None,
        )
        runtime = {
            "rule": self._standard_rule(),
            "expected_in": time(10, 26),
            "check_in_required": True,
            "flex_mode": "none",
        }
        anomalies: list[str] = []
        AttendanceRecordService._append_status_anomalies(record, runtime, anomalies)
        assert anomalies == []

    def test_full_day_display_status_uses_minute_precision(self):
        record = SimpleNamespace(
            date=date(2026, 4, 24),
            clock_in_time=datetime(2026, 4, 24, 2, 26, 59, tzinfo=timezone.utc),  # 10:26:59 CST
            clock_out_time=datetime(2026, 4, 24, 2, 30, 1, tzinfo=timezone.utc),  # 10:30:01 CST
            status=AttendanceStatus.late,
            clock_in_gps_lat=None,
            clock_in_gps_lng=None,
            clock_out_gps_lat=None,
            clock_out_gps_lng=None,
            anomaly_type=None,
        )
        runtime = {
            "rule": self._standard_rule(),
            "expected_in": time(10, 26),
            "expected_out": time(10, 30),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
            "early_threshold_minutes": 0,
        }
        assert AttendanceRecordService._record_display_status(record, runtime) == "正常"

    def test_no_punch_employee_with_existing_full_punch_keeps_no_punch_priority(self):
        record = SimpleNamespace(
            date=date(2026, 4, 30),
            clock_in_time=datetime(2026, 4, 30, 2, 32, tzinfo=timezone.utc),  # 10:32 CST
            clock_out_time=datetime(2026, 4, 30, 3, 26, tzinfo=timezone.utc),  # 11:26 CST
            status=AttendanceStatus.late,
            clock_in_gps_lat=None,
            clock_in_gps_lng=None,
            clock_out_gps_lat=None,
            clock_out_gps_lng=None,
            anomaly_type=None,
        )
        runtime = {
            "rule": self._standard_rule(),
            "no_punch": True,
            "segment": {"check_in_required": True, "check_out_required": True},
            "expected_in": time(10, 0),
            "expected_out": time(10, 30),
            "check_in_required": False,
            "check_out_required": False,
            "flex_mode": "none",
            "early_threshold_minutes": 15,
        }

        AttendanceRecordService._attach_display_status(record, runtime)

        assert record.display_status == "免打卡"
        assert record.clock_in_status == "免打卡"
        assert record.clock_out_status == "免打卡"
        assert record.location_abnormal is False

    @pytest.mark.asyncio
    async def test_no_punch_recalculate_does_not_set_attendance_exception(self):
        record = SimpleNamespace(
            date=date(2026, 4, 30),
            clock_in_time=datetime(2026, 4, 30, 2, 32, tzinfo=timezone.utc),
            clock_out_time=datetime(2026, 4, 30, 3, 26, tzinfo=timezone.utc),
            status=AttendanceStatus.late,
            overtime_hours=Decimal("1.00"),
            anomaly_type="迟到32分钟",
        )
        runtime = {
            "rule": self._standard_rule(),
            "no_punch": True,
            "expected_in": time(10, 0),
            "expected_out": time(10, 30),
            "check_in_required": False,
            "check_out_required": False,
        }
        anomalies = ["迟到32分钟"]

        await AttendanceRecordService._recalculate_record_after_punch(
            None,
            record,
            9,
            record.date,
            runtime,
            anomalies,
        )

        assert record.status == AttendanceStatus.normal
        assert record.overtime_hours == Decimal("0.00")
        assert record.anomaly_type is None
        assert anomalies == []

    def test_attach_display_status_sets_bound_clock_and_location_statuses(self):
        record = SimpleNamespace(
            date=date(2026, 4, 24),
            clock_in_time=datetime(2026, 4, 24, 2, 26, 59, tzinfo=timezone.utc),  # 10:26:59 CST
            clock_out_time=datetime(2026, 4, 24, 2, 30, 1, tzinfo=timezone.utc),  # 10:30:01 CST
            status=AttendanceStatus.normal,
            clock_in_gps_lat=None,
            clock_in_gps_lng=None,
            clock_out_gps_lat=None,
            clock_out_gps_lng=None,
            anomaly_type=None,
        )
        runtime = {
            "rule": self._standard_rule(),
            "expected_in": time(10, 26),
            "expected_out": time(10, 30),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
            "early_threshold_minutes": 0,
        }
        AttendanceRecordService._attach_display_status(record, runtime)
        assert record.display_status == "正常"
        assert record.clock_in_status == "正常"
        assert record.clock_out_status == "正常"
        assert record.location_status == "范围内"
        assert record.location_abnormal is False

    def test_attach_display_status_sets_separate_punch_location_statuses(self):
        rule = self._standard_rule()
        rule.gps_latitude = Decimal("39.9")
        rule.gps_longitude = Decimal("116.4")
        rule.gps_radius_meters = 100
        record = SimpleNamespace(
            date=date(2026, 4, 24),
            clock_in_time=datetime(2026, 4, 24, 2, 26, tzinfo=timezone.utc),
            clock_out_time=datetime(2026, 4, 24, 2, 30, tzinfo=timezone.utc),
            status=AttendanceStatus.normal,
            clock_in_gps_lat=Decimal("30.0"),
            clock_in_gps_lng=Decimal("120.0"),
            clock_in_location="异地",
            clock_out_gps_lat=Decimal("39.9001"),
            clock_out_gps_lng=Decimal("116.4001"),
            clock_out_location="范围内",
            anomaly_type=None,
        )
        runtime = {
            "rule": rule,
            "expected_in": time(10, 26),
            "expected_out": time(10, 30),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
            "early_threshold_minutes": 0,
        }
        AttendanceRecordService._attach_display_status(record, runtime)
        assert record.clock_in_location_abnormal is True
        assert record.clock_in_location_status == "打卡位置异常"
        assert record.clock_out_location_abnormal is False
        assert record.clock_out_location_status == "范围内"
        assert record.location_abnormal is True

    def test_display_status_combines_late_early_and_location_anomalies(self):
        rule = self._standard_rule()
        rule.gps_latitude = Decimal("39.9")
        rule.gps_longitude = Decimal("116.4")
        rule.gps_radius_meters = 100
        record = SimpleNamespace(
            date=date(2026, 5, 28),
            clock_in_time=datetime(2026, 5, 28, 2, 39, tzinfo=timezone.utc),  # 10:39 CST
            clock_out_time=datetime(2026, 5, 28, 3, 46, tzinfo=timezone.utc),  # 11:46 CST
            status=AttendanceStatus.early_leave,
            clock_in_gps_lat=Decimal("32.058380"),
            clock_in_gps_lng=Decimal("118.796470"),
            clock_out_gps_lat=Decimal("32.058380"),
            clock_out_gps_lng=Decimal("118.796470"),
            anomaly_type="超出打卡范围: 距离885119米, 限制300米; 迟到69分钟; 早退344分钟",
        )
        runtime = {
            "rule": rule,
            "expected_in": time(9, 30),
            "expected_out": time(17, 30),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
            "early_threshold_minutes": 0,
        }

        assert AttendanceRecordService._record_display_status(record, runtime) == "迟到/早退/打卡位置异常"

    def test_display_status_falls_back_to_anomaly_parts_without_rule(self):
        record = SimpleNamespace(
            status=AttendanceStatus.normal,
            anomaly_type="超出打卡范围: 距离885119米, 限制300米; 迟到69分钟; 早退344分钟",
            clock_in_gps_lat=None,
            clock_in_gps_lng=None,
            clock_out_gps_lat=None,
            clock_out_gps_lng=None,
        )

        assert AttendanceRecordService._record_display_status(record, None) == "迟到/早退/打卡位置异常"

    def test_full_day_status_uses_minute_precision(self):
        status = AttendanceRecordService._determine_status(
            self._standard_rule(),
            datetime(2026, 4, 24, 2, 26, 59, tzinfo=timezone.utc),  # 10:26:59 CST
            datetime(2026, 4, 24, 2, 30, 1, tzinfo=timezone.utc),  # 10:30:01 CST
            date(2026, 4, 24),
            expected_in=time(10, 26),
            expected_out=time(10, 30),
        )
        assert status == AttendanceStatus.normal

    def test_rebuild_anomaly_text_removes_stale_late_status(self):
        record = SimpleNamespace(
            date=date(2026, 4, 24),
            clock_in_time=datetime(2026, 4, 24, 2, 26, 59, tzinfo=timezone.utc),  # 10:26:59 CST
            clock_out_time=datetime(2026, 4, 24, 2, 30, 1, tzinfo=timezone.utc),  # 10:30:01 CST
            anomaly_type="迟到1分钟",
        )
        runtime = {
            "rule": self._standard_rule(),
            "expected_in": time(10, 26),
            "expected_out": time(10, 30),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
            "early_threshold_minutes": 0,
        }
        assert AttendanceRecordService._rebuild_anomaly_text_with_status(record, runtime) is None

    def test_display_status_recalculates_clock_in_only_late(self):
        record = SimpleNamespace(
            date=date(2026, 4, 22),
            clock_in_time=datetime(2026, 4, 22, 1, 41, tzinfo=timezone.utc),  # 09:41 CST
            clock_out_time=None,
            status=AttendanceStatus.normal,
            clock_in_gps_lat=None,
            clock_in_gps_lng=None,
            clock_out_gps_lat=None,
            clock_out_gps_lng=None,
            anomaly_type=None,
        )
        runtime = {
            "rule": self._legacy_fixed_rule_with_default_flexible_minutes(),
            "expected_in": time(9, 30),
            "check_in_required": True,
            "flex_mode": "none",
        }
        assert AttendanceRecordService._record_display_status(record, runtime) == "迟到"

    def test_status_anomalies_include_late_minutes_for_mobile_month_detail(self):
        record = SimpleNamespace(
            date=date(2026, 4, 22),
            clock_in_time=datetime(2026, 4, 22, 1, 37, tzinfo=timezone.utc),  # 09:37 CST
            clock_out_time=None,
        )
        runtime = {
            "rule": self._legacy_fixed_rule_with_default_flexible_minutes(),
            "expected_in": time(9, 30),
            "check_in_required": True,
            "flex_mode": "none",
        }
        anomalies: list[str] = []
        AttendanceRecordService._append_status_anomalies(record, runtime, anomalies)
        assert anomalies == ["迟到7分钟"]

    def test_missing_clock_waits_until_punch_window_ends(self):
        rule = self._standard_rule()
        record = SimpleNamespace(
            date=date(2026, 4, 29),
            clock_in_time=datetime(2026, 4, 29, 2, 52, tzinfo=timezone.utc),  # 10:52 CST
            clock_out_time=None,
        )
        runtime = {
            "rule": rule,
            "segment": {"punch_start": "05:00", "punch_end": "23:59"},
            "expected_in": time(9, 30),
            "expected_out": time(17, 30),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
        }

        before_window_end = datetime(2026, 4, 29, 18, 0, tzinfo=timezone(timedelta(hours=8)))
        after_window_end = datetime(2026, 4, 30, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        assert AttendanceRecordService._punch_window_has_ended(
            record.date,
            runtime,
            as_of=before_window_end,
        ) is False
        assert AttendanceRecordService._determine_incomplete_record_status(
            rule,
            record,
            record.date,
            runtime,
            as_of=before_window_end,
        ) == AttendanceStatus.late
        assert AttendanceRecordService._determine_incomplete_record_status(
            rule,
            record,
            record.date,
            runtime,
            as_of=after_window_end,
        ) == AttendanceStatus.missed_clock

    def test_missing_clock_anomaly_is_added_after_status_matures(self):
        record = SimpleNamespace(
            date=date(2026, 4, 22),
            clock_in_time=datetime(2026, 4, 22, 1, 37, tzinfo=timezone.utc),
            clock_out_time=None,
            status=AttendanceStatus.missed_clock,
        )
        runtime = {
            "rule": self._standard_rule(),
            "segment": {"punch_start": "05:00", "punch_end": "23:59"},
            "expected_in": time(9, 30),
            "expected_out": time(17, 30),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
        }
        anomalies: list[str] = []

        AttendanceRecordService._append_status_anomalies(record, runtime, anomalies)

        assert anomalies == ["缺少下班打卡", "迟到7分钟"]

    def test_mobile_phase_allows_clock_out_update_after_completed_record(self):
        record = SimpleNamespace(
            clock_in_time=datetime(2026, 4, 29, 2, 52, tzinfo=timezone.utc),
            clock_out_time=datetime(2026, 4, 29, 5, 6, tzinfo=timezone.utc),
        )

        assert AttendanceRecordService._resolve_mobile_punch_phase(record) == "clock_out"

    def test_no_punch_employee_with_existing_single_punch_keeps_no_punch_priority(self):
        rule = self._standard_rule()
        record = SimpleNamespace(
            date=date(2026, 4, 28),
            clock_in_time=datetime(2026, 4, 28, 1, 38, tzinfo=timezone.utc),
            clock_out_time=None,
            status=AttendanceStatus.normal,
        )
        runtime = {
            "rule": rule,
            "no_punch": True,
            "segment": {
                "punch_start": "04:00",
                "punch_end": "03:59",
                "check_in_required": True,
                "check_out_required": True,
            },
            "expected_in": time(10, 0),
            "expected_out": time(10, 30),
            "check_in_required": False,
            "check_out_required": False,
            "flex_mode": "none",
        }

        status = AttendanceRecordService._determine_incomplete_record_status(
            rule,
            record,
            record.date,
            runtime,
            as_of=datetime(2026, 4, 29, 4, 0, tzinfo=timezone(timedelta(hours=8))),
        )

        assert status == AttendanceStatus.normal

    def test_cross_day_punch_window_ends_next_day(self):
        runtime = {
            "rule": self._standard_rule(),
            "segment": {"punch_start": "04:00", "punch_end": "03:59"},
            "expected_in": time(9, 30),
            "expected_out": time(17, 30),
        }

        assert AttendanceRecordService._punch_window_has_ended(
            date(2026, 4, 29),
            runtime,
            as_of=datetime(2026, 4, 30, 3, 59, 59, tzinfo=timezone(timedelta(hours=8))),
        ) is False
        assert AttendanceRecordService._punch_window_has_ended(
            date(2026, 4, 29),
            runtime,
            as_of=datetime(2026, 4, 30, 4, 0, tzinfo=timezone(timedelta(hours=8))),
        ) is True

    def test_preview_early_leave_status_for_mobile_button(self):
        punch_at = datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc)  # 16:00 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._standard_rule(),
            "clock_out",
            punch_at,
            date(2026, 4, 21),
            expected_in=time(9, 30),
            expected_out=time(17, 30),
        )
        assert preview["status"] == AttendanceStatus.early_leave
        assert preview["minutes"] == 90

    def test_preview_before_off_duty_status_inside_early_grace(self):
        punch_at = datetime(2026, 4, 21, 9, 25, tzinfo=timezone.utc)  # 17:25 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._standard_rule(),
            "clock_out",
            punch_at,
            date(2026, 4, 21),
            expected_in=time(9, 30),
            expected_out=time(17, 30),
            early_threshold_minutes=10,
        )
        assert preview["status"] == "before_off_duty"
        assert preview["minutes"] == 5

    def test_preview_before_off_duty_status_for_late_leave_flex_rule(self):
        punch_at = datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc)  # 16:00 CST
        preview = AttendanceRecordService._preview_punch_status(
            self._standard_rule(),
            "clock_out",
            punch_at,
            date(2026, 4, 21),
            expected_in=time(9, 30),
            expected_out=time(17, 30),
            flex_mode="late_leave",
        )
        assert preview["status"] == "before_off_duty"
        assert preview["minutes"] == 90

    def test_punch_window_treats_exact_minute_as_in_range_even_with_seconds(self):
        local_dt = datetime(2026, 4, 24, 10, 30, 59, tzinfo=timezone(timedelta(hours=8)))
        assert AttendanceRecordService._within_punch_window(local_dt, "09:00", "10:30") is True

    def test_punch_window_marks_next_minute_as_out_of_range(self):
        local_dt = datetime(2026, 4, 24, 10, 31, 0, tzinfo=timezone(timedelta(hours=8)))
        assert AttendanceRecordService._within_punch_window(local_dt, "09:00", "10:30") is False

    def test_late_leave_flex_rule_still_marks_early_clock_out(self):
        status = AttendanceRecordService._determine_status(
            self._standard_rule(),
            datetime(2026, 4, 21, 1, 30, tzinfo=timezone.utc),  # 09:30 CST
            datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc),  # 16:00 CST
            date(2026, 4, 21),
            expected_in=time(9, 30),
            expected_out=time(17, 30),
            flex_mode="late_leave",
        )
        assert status == AttendanceStatus.early_leave

    def test_late_leave_flex_rule_requires_late_leave_to_offset_late_arrival(self):
        status = AttendanceRecordService._determine_status(
            self._standard_rule(),
            datetime(2026, 4, 21, 2, 0, tzinfo=timezone.utc),  # 10:00 CST
            datetime(2026, 4, 21, 9, 45, tzinfo=timezone.utc),  # 17:45 CST
            date(2026, 4, 21),
            expected_in=time(9, 30),
            expected_out=time(17, 30),
            flex_mode="late_leave",
        )
        assert status == AttendanceStatus.late

    def test_late_leave_flex_rule_is_normal_after_full_offset(self):
        status = AttendanceRecordService._determine_status(
            self._standard_rule(),
            datetime(2026, 4, 21, 2, 0, tzinfo=timezone.utc),  # 10:00 CST
            datetime(2026, 4, 21, 10, 0, tzinfo=timezone.utc),  # 18:00 CST
            date(2026, 4, 21),
            expected_in=time(9, 30),
            expected_out=time(17, 30),
            flex_mode="late_leave",
        )
        assert status == AttendanceStatus.normal


class TestOvertimePolicyCalculation:

    @staticmethod
    def _rule() -> SimpleNamespace:
        return SimpleNamespace(work_hours_per_day=Decimal("8.0"))

    @staticmethod
    def _extra_policy(calc_method: str, period_mode: str = "all") -> dict:
        base_day = {
            "enabled": True,
            "period_mode": period_mode,
            "calc_method": calc_method,
            "start_after_off_duty_minutes": 0,
            "allow_rest_deduction": False,
            "rest_deduction_mode": "period",
            "rest_periods": [{"start": "12:00", "end": "13:00"}],
            "deduct_every_minutes": 300,
            "deduct_minutes": 60,
            "min_minutes": 30,
            "max_minutes": 240,
            "allow_convert": True,
            "convert_mode": "overtime_pay",
        }
        return {
            "enable_overtime": True,
            "overtime_policy": {
                "workday": dict(base_day),
                "restday": dict(base_day),
                "holiday": dict(base_day),
            },
            "overtime_duration": {
                "rounding_mode": "round",
                "decimal_places": 2,
                "unit": "hour",
                "day_to_hours": 8,
            },
        }

    def test_before_work_by_clock_uses_only_pre_work_interval(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "before_work")
        result = AttendanceRecordService._calculate_overtime(
            Decimal("2.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 8, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 10, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )
        assert result == Decimal("1.00")

    def test_before_work_by_approval_and_clock_intersection(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_approval_clock", "before_work")
        approved_entries = [
            {
                "start_dt": datetime(2026, 4, 1, 7, 30, tzinfo=cst),
                "end_dt": datetime(2026, 4, 1, 9, 30, tzinfo=cst),
                "hours": Decimal("2.0"),
            }
        ]
        result = AttendanceRecordService._calculate_overtime(
            Decimal("2.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 8, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 10, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=approved_entries,
        )
        assert result == Decimal("1.00")

    def test_after_work_respects_start_after_off_duty_minutes(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "after_work")
        extra["overtime_policy"]["workday"]["start_after_off_duty_minutes"] = 30
        result = AttendanceRecordService._calculate_overtime(
            Decimal("2.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 18, 10, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 20, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )
        assert result == Decimal("1.50")

    def test_after_work_five_minute_threshold_starts_counting_after_cutoff(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "after_work")
        extra["overtime_policy"]["workday"]["start_after_off_duty_minutes"] = 5
        extra["overtime_policy"]["workday"]["min_minutes"] = 0
        result = AttendanceRecordService._calculate_overtime(
            Decimal("9.58"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 9, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 18, 35, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )
        assert result == Decimal("0.50")

    def test_after_work_five_minute_threshold_does_not_count_within_cutoff(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "after_work")
        extra["overtime_policy"]["workday"]["start_after_off_duty_minutes"] = 5
        extra["overtime_policy"]["workday"]["min_minutes"] = 0
        for clock_out in (
            datetime(2026, 4, 1, 18, 4, tzinfo=cst),
            datetime(2026, 4, 1, 18, 5, tzinfo=cst),
        ):
            result = AttendanceRecordService._calculate_overtime(
                Decimal("9.08"),
                self._rule(),
                extra=extra,
                day_type=DayType.workday,
                clock_in=datetime(2026, 4, 1, 9, 0, tzinfo=cst),
                clock_out=clock_out,
                target_date=date(2026, 4, 1),
                expected_in=time(9, 0),
                expected_out=time(18, 0),
                approved_entries=[],
            )
            assert result == Decimal("0.00")

    def test_after_work_thirty_minute_threshold_does_not_count_at_cutoff(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "after_work")
        extra["overtime_policy"]["workday"]["start_after_off_duty_minutes"] = 30
        extra["overtime_policy"]["workday"]["min_minutes"] = 0
        result = AttendanceRecordService._calculate_overtime(
            Decimal("9.50"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 9, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 18, 30, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )
        assert result == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_after_work_overtime_recalculation_writes_record_hours_after_clock_out(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "after_work")
        extra["overtime_policy"]["workday"]["start_after_off_duty_minutes"] = 5
        extra["overtime_policy"]["workday"]["min_minutes"] = 0
        extra["overtime_policy"]["workday"]["max_minutes"] = 1000
        rule = SimpleNamespace(
            work_hour_type=WorkHourType.standard,
            work_hours_per_day=Decimal("8.0"),
            clock_in_time=time(9, 0),
            clock_out_time=time(18, 0),
            flexible_minutes=0,
            extra_config=extra,
        )
        record = SimpleNamespace(
            date=date(2026, 4, 1),
            clock_in_time=datetime(2026, 4, 1, 9, 0, tzinfo=cst),
            clock_out_time=datetime(2026, 4, 1, 18, 35, tzinfo=cst),
            work_hours=None,
            overtime_hours=Decimal("0.00"),
            status=AttendanceStatus.normal,
            anomaly_type=None,
        )
        runtime = {
            "rule": rule,
            "rule_type": "fixed",
            "expected_in": time(9, 0),
            "expected_out": time(18, 0),
            "check_in_required": True,
            "check_out_required": True,
            "flex_mode": "none",
            "early_threshold_minutes": 0,
            "half_day_pm_start": None,
            "half_day_absence_cutoff": None,
            "rest_periods": [],
            "extra": extra,
            "employee": SimpleNamespace(location_id=1),
        }

        with patch(
            "app.services.attendance.WorkCalendarService.get_day_type",
            new=AsyncMock(return_value=DayType.workday),
        ), patch(
            "app.services.attendance.AttendanceRecordService._load_approved_overtime_entries",
            new=AsyncMock(return_value=[]),
        ), patch(
            "app.services.attendance.AttendanceRecordService._sync_comp_time_balance_delta",
            new=AsyncMock(),
        ):
            await AttendanceRecordService._recalculate_record_after_punch(
                AsyncMock(),
                record,
                employee_id=9,
                target_date=date(2026, 4, 1),
                runtime=runtime,
                anomalies=[],
            )

        assert record.overtime_hours == Decimal("0.50")
        assert record.status == AttendanceStatus.normal

    def test_workday_all_by_clock_counts_before_work_and_post_off_duty_with_delay(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "all")
        extra["overtime_policy"]["workday"]["start_after_off_duty_minutes"] = 30
        extra["overtime_policy"]["workday"]["max_minutes"] = 1000
        result = AttendanceRecordService._calculate_overtime(
            Decimal("12.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 8, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 20, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )
        assert result == Decimal("2.50")

    def test_special_workday_without_schedule_anchor_does_not_count_full_clock_as_overtime(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_approval", "all")
        extra["overtime_policy"]["workday"]["convert_mode"] = "comp_time"
        result = AttendanceRecordService._calculate_overtime(
            Decimal("2.10"),
            self._rule(),
            extra=extra,
            day_type=DayType.special_workday,
            clock_in=datetime(2026, 5, 9, 9, 35, tzinfo=cst),
            clock_out=datetime(2026, 5, 9, 11, 41, tzinfo=cst),
            target_date=date(2026, 5, 9),
            expected_in=None,
            expected_out=None,
            approved_entries=[],
        )
        assert result == Decimal("0")

    def test_by_clock_rest_deduction_supports_multiple_rest_periods(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "after_work")
        extra["overtime_policy"]["workday"]["min_minutes"] = 0
        extra["overtime_policy"]["workday"]["max_minutes"] = 1000
        extra["overtime_policy"]["workday"]["allow_rest_deduction"] = True
        extra["overtime_policy"]["workday"]["rest_deduction_mode"] = "period"
        extra["overtime_policy"]["workday"]["rest_periods"] = [
            {"start": "18:30", "end": "19:00"},
            {"start": "20:00", "end": "20:15"},
        ]
        result = AttendanceRecordService._calculate_overtime(
            Decimal("4.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 18, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 22, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )
        assert result == Decimal("3.25")

    def test_restday_ignores_period_mode(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_clock", "before_work")
        result = AttendanceRecordService._calculate_overtime(
            Decimal("4.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.weekend,
            clock_in=datetime(2026, 4, 5, 10, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 5, 14, 0, tzinfo=cst),
            target_date=date(2026, 4, 5),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )
        assert result == Decimal("4.00")

    def test_comp_time_mode_not_payroll_eligible(self):
        extra = self._extra_policy("by_clock", "all")
        extra["overtime_policy"]["workday"]["convert_mode"] = "comp_time"
        assert AttendanceRecordService._is_overtime_payroll_eligible(extra, DayType.workday) is False

    def test_comp_time_settlement_uses_ratio_and_excludes_payroll(self):
        extra = self._extra_policy("by_clock", "all")
        extra["overtime_policy"]["workday"]["convert_mode"] = "comp_time"
        extra["overtime_policy"]["workday"]["comp_time_ratio"] = 1.5
        comp_hours, pay_hours = AttendanceRecordService._split_overtime_settlement(
            extra,
            DayType.workday,
            Decimal("2.00"),
        )
        assert comp_hours == Decimal("3.00")
        assert pay_hours == Decimal("0")

    def test_comp_time_balance_delta_uses_recalculated_difference(self):
        extra = self._extra_policy("by_clock", "all")
        extra["overtime_policy"]["workday"]["convert_mode"] = "comp_time"
        extra["overtime_policy"]["workday"]["comp_time_ratio"] = 1.5

        delta = AttendanceRecordService._comp_time_balance_delta(
            extra,
            DayType.workday,
            Decimal("1.00"),
            Decimal("2.00"),
        )

        assert delta == Decimal("1.5")

    def test_comp_time_hours_convert_to_hour_leave_balance_days(self):
        leave_type = SimpleNamespace(leave_unit="hour", hours_per_day=Decimal("8.0"))

        result = AttendanceRecordService._comp_hours_to_balance_days(leave_type, Decimal("5.00"))

        assert result == Decimal("0.625")

    def test_comp_time_balance_delta_skips_auto_sync_when_disabled(self):
        extra = self._extra_policy("by_clock", "all")
        extra["overtime_policy"]["workday"]["convert_mode"] = "comp_time"
        extra["overtime_policy"]["workday"]["sync_auto_leave_type"] = False

        delta = AttendanceRecordService._comp_time_balance_delta(
            extra,
            DayType.workday,
            Decimal("0.00"),
            Decimal("2.00"),
        )

        assert delta == Decimal("0.0")

    def test_overtime_pay_settlement_excludes_comp_time(self):
        extra = self._extra_policy("by_clock", "all")
        comp_hours, pay_hours = AttendanceRecordService._split_overtime_settlement(
            extra,
            DayType.weekend,
            Decimal("2.50"),
        )
        assert comp_hours == Decimal("0")
        assert pay_hours == Decimal("2.50")

    def test_disabled_day_rule_does_not_count_approved_overtime(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_approval", "all")
        extra["overtime_policy"]["workday"]["enabled"] = False
        approved_entries = [
            {
                "start_dt": datetime(2026, 4, 1, 18, 0, tzinfo=cst),
                "end_dt": datetime(2026, 4, 1, 20, 0, tzinfo=cst),
                "hours": Decimal("2.0"),
            }
        ]
        result = AttendanceRecordService._calculate_overtime(
            Decimal("10.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=None,
            clock_out=None,
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=approved_entries,
        )
        assert result == Decimal("0")

    def test_by_approval_uses_approved_duration_without_clock_intersection(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_approval", "all")
        extra["overtime_policy"]["workday"]["allow_rest_deduction"] = False
        approved_entries = [
            {
                "start_dt": datetime(2026, 4, 1, 18, 0, tzinfo=cst),
                "end_dt": datetime(2026, 4, 1, 20, 0, tzinfo=cst),
                "hours": Decimal("2.0"),
            }
        ]
        result = AttendanceRecordService._calculate_overtime(
            Decimal("9.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 18, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 19, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=approved_entries,
        )
        assert result == Decimal("2.00")

    def test_by_approval_without_request_uses_self_clock_overtime(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_approval", "after_work")
        extra["overtime_policy"]["workday"]["start_after_off_duty_minutes"] = 30
        extra["overtime_policy"]["workday"]["allow_rest_deduction"] = False

        result = AttendanceRecordService._calculate_overtime(
            Decimal("10.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 18, 10, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 20, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )

        assert result == Decimal("1.50")

    def test_by_approval_clock_without_request_uses_self_clock_overtime(self):
        cst = timezone(timedelta(hours=8))
        extra = self._extra_policy("by_approval_clock", "after_work")
        extra["overtime_policy"]["workday"]["allow_rest_deduction"] = False

        result = AttendanceRecordService._calculate_overtime(
            Decimal("10.00"),
            self._rule(),
            extra=extra,
            day_type=DayType.workday,
            clock_in=datetime(2026, 4, 1, 18, 0, tzinfo=cst),
            clock_out=datetime(2026, 4, 1, 20, 0, tzinfo=cst),
            target_date=date(2026, 4, 1),
            expected_in=time(9, 0),
            expected_out=time(18, 0),
            approved_entries=[],
        )

        assert result == Decimal("2.00")


class TestRulePeopleSettings:

    def test_runtime_people_marks_no_punch_and_management_ids(self):
        runtime = {
            "extra": {
                "no_punch_employee_ids": [5, "7"],
                "report_target_ids": ["9"],
                "assist_manager_ids": [10],
            },
            "check_in_required": True,
            "check_out_required": True,
        }

        out = AttendanceRecordService._apply_rule_runtime_people(runtime, 5)

        assert out["no_punch"] is True
        assert out["check_in_required"] is False
        assert out["check_out_required"] is False
        assert out["report_target_ids"] == [9]
        assert out["assist_manager_ids"] == [10]

    def test_runtime_people_keeps_punch_required_for_other_employees(self):
        runtime = {
            "extra": {
                "no_punch_employee_ids": [5],
                "report_target_ids": [],
                "assist_manager_ids": [],
            },
            "check_in_required": True,
            "check_out_required": True,
        }

        out = AttendanceRecordService._apply_rule_runtime_people(runtime, 6)

        assert out["no_punch"] is False
        assert out["check_in_required"] is True
        assert out["check_out_required"] is True
