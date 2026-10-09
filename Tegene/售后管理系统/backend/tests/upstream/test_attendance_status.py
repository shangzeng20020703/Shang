"""
考勤状态判定单元测试
覆盖: _determine_status() 时区比较、迟到/早退/正常/旷工/缺卡 逻辑
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from types import SimpleNamespace
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.attendance import AttendanceRecordService
from app.models.attendance import AttendanceStatus, WorkHourType


CST = timezone(timedelta(hours=8))


def make_rule(
    work_hour_type=WorkHourType.standard,
    clock_in_time=time(9, 0),
    clock_out_time=time(18, 0),
    flexible_minutes=10,
    work_hours_per_day=Decimal("8"),
):
    """创建考勤规则 mock"""
    rule = MagicMock()
    rule.work_hour_type = work_hour_type
    rule.clock_in_time = clock_in_time
    rule.clock_out_time = clock_out_time
    rule.flexible_minutes = flexible_minutes
    rule.work_hours_per_day = work_hours_per_day
    rule.extra_config = {"late_threshold_minutes": flexible_minutes}
    return rule


TARGET_DATE = date(2026, 3, 3)  # 周一


def local_dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, 3, hour, minute, 0, tzinfo=CST)


class TestDetermineStatusStandard:
    """标准工时制状态判定（时区感知版本）"""

    def test_normal_on_time(self):
        """准时到、准时走 → 正常"""
        rule = make_rule()
        clock_in = local_dt(9, 0)
        clock_out = local_dt(18, 0)
        status = AttendanceRecordService._determine_status(rule, clock_in, clock_out, TARGET_DATE)
        assert status == AttendanceStatus.normal

    def test_late_arrival(self):
        """9:11 到 → 超过10分钟弹性 → 迟到"""
        rule = make_rule()
        clock_in = local_dt(9, 11)
        clock_out = local_dt(18, 0)
        status = AttendanceRecordService._determine_status(rule, clock_in, clock_out, TARGET_DATE)
        assert status == AttendanceStatus.late

    def test_on_time_within_flex(self):
        """9:05 到（弹性10分钟内）→ 正常"""
        rule = make_rule()
        clock_in = local_dt(9, 5)
        clock_out = local_dt(18, 0)
        status = AttendanceRecordService._determine_status(rule, clock_in, clock_out, TARGET_DATE)
        assert status == AttendanceStatus.normal

    def test_early_leave(self):
        """17:50 下班（早退）"""
        rule = make_rule()
        clock_in = local_dt(9, 0)
        clock_out = local_dt(17, 50)
        status = AttendanceRecordService._determine_status(rule, clock_in, clock_out, TARGET_DATE)
        assert status == AttendanceStatus.early_leave

    def test_absent_no_clocks(self):
        """没有任何打卡 → 旷工"""
        rule = make_rule()
        status = AttendanceRecordService._determine_status(rule, None, None, TARGET_DATE)
        assert status == AttendanceStatus.absent

    def test_missed_clock_no_in(self):
        """只有下班打卡，没有上班 → 缺卡"""
        rule = make_rule()
        clock_out = local_dt(18, 0)
        status = AttendanceRecordService._determine_status(rule, None, clock_out, TARGET_DATE)
        assert status == AttendanceStatus.missed_clock

    def test_missed_clock_no_out(self):
        """只有上班打卡，没有下班 → 缺卡"""
        rule = make_rule()
        clock_in = local_dt(9, 0)
        status = AttendanceRecordService._determine_status(rule, clock_in, None, TARGET_DATE)
        assert status == AttendanceStatus.missed_clock

    def test_no_type_error_with_timezone_aware_clocks(self):
        """时区感知 datetime 与规则时间比较不抛 TypeError（P0 修复验证）"""
        rule = make_rule()
        clock_in = local_dt(9, 0)
        clock_out = local_dt(18, 0)
        # 不应抛异常
        try:
            AttendanceRecordService._determine_status(rule, clock_in, clock_out, TARGET_DATE)
            no_exception = True
        except TypeError:
            no_exception = False
        assert no_exception, "_determine_status 不应抛 TypeError（时区比较修复后）"

    def test_utc_clocks_convert_to_cst_business_time(self):
        """UTC 存储时间应转换为中国时区后再按本地班次判定。"""
        rule = make_rule()
        clock_in = datetime(2026, 3, 3, 1, 0, 0, tzinfo=timezone.utc)
        clock_out = datetime(2026, 3, 3, 10, 0, 0, tzinfo=timezone.utc)
        status = AttendanceRecordService._determine_status(rule, clock_in, clock_out, TARGET_DATE)
        assert status == AttendanceStatus.normal


class TestDetermineStatusFlexible:
    """弹性工时制状态判定"""

    def test_flexible_both_clocks_normal(self):
        """弹性制有两次打卡 → 正常"""
        rule = make_rule(work_hour_type=WorkHourType.flexible)
        clock_in = local_dt(10, 0)
        clock_out = local_dt(19, 0)
        status = AttendanceRecordService._determine_status(rule, clock_in, clock_out, TARGET_DATE)
        assert status == AttendanceStatus.normal

    def test_flexible_missing_one_clock(self):
        """弹性制缺一次打卡 → 缺卡"""
        rule = make_rule(work_hour_type=WorkHourType.flexible)
        clock_in = local_dt(10, 0)
        status = AttendanceRecordService._determine_status(rule, clock_in, None, TARGET_DATE)
        assert status == AttendanceStatus.missed_clock

    def test_flexible_no_clocks(self):
        """弹性制无打卡 → 旷工"""
        rule = make_rule(work_hour_type=WorkHourType.flexible)
        status = AttendanceRecordService._determine_status(rule, None, None, TARGET_DATE)
        assert status == AttendanceStatus.absent


class TestFreeWorkHoursThreshold:
    @pytest.mark.asyncio
    async def test_existing_project_record_rechecks_shortage_and_clears_it_after_full_day(self):
        segment = {
            "project_name": "测试项目3", "clock_in_time": "2026-10-09T08:00:00",
            "clock_out_time": "2026-10-09T08:00:10", "work_hours": "0.00",
            "required_work_hours": "12", "needs_review": False,
        }
        runtime = {"project_segments": [segment]}
        record = SimpleNamespace(anomaly_type=None, status=AttendanceStatus.normal)
        await AttendanceRecordService._recalculate_record_after_punch(None, record, 1, TARGET_DATE, runtime)
        AttendanceRecordService._attach_display_status(record, runtime)
        assert record.display_status == "异常"
        assert "工时不足" in record.anomaly_type

        segment["work_hours"] = "12.00"
        await AttendanceRecordService._recalculate_record_after_punch(None, record, 1, TARGET_DATE, runtime)
        AttendanceRecordService._attach_display_status(record, runtime)
        assert record.display_status == "正常"
        assert record.anomaly_type is None

    def test_short_day_is_anomaly_until_full_limit_is_reached(self):
        rule = SimpleNamespace(work_hour_type=WorkHourType.flexible, extra_config={
            "rule_type": "free", "free_work_hours_mode": "limit:12",
        })
        start = local_dt(8)
        runtime = {"rule": rule, "extra": rule.extra_config, "rest_periods": []}
        short = SimpleNamespace(date=TARGET_DATE, clock_in_time=start,
                                clock_out_time=start + timedelta(hours=11, minutes=59, seconds=59),
                                work_hours=Decimal("12.00"))
        assert "工时不足" in AttendanceRecordService._free_hours_shortage_issue(short, runtime)
        short.clock_out_time = start + timedelta(hours=12)
        assert AttendanceRecordService._free_hours_shortage_issue(short, runtime) is None

    def test_unlimited_and_single_punch_have_no_hours_shortage(self):
        rule = SimpleNamespace(work_hour_type=WorkHourType.flexible, extra_config={
            "rule_type": "free", "free_work_hours_mode": "unlimited",
        })
        record = SimpleNamespace(date=TARGET_DATE, clock_in_time=local_dt(8),
                                 clock_out_time=local_dt(9), work_hours=Decimal("1"))
        runtime = {"rule": rule, "extra": rule.extra_config, "rest_periods": []}
        assert AttendanceRecordService._free_hours_shortage_issue(record, runtime) is None
        rule.extra_config["free_work_hours_mode"] = "limit:12"
        record.clock_out_time = None
        assert AttendanceRecordService._free_hours_shortage_issue(record, runtime) is None
