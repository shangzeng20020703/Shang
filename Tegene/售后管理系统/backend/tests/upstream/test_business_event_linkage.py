"""
审批与考勤事件联动测试。
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.attendance import (
    AttendanceEffect,
    AttendancePunchTimeRecord,
    AttendanceRecord,
    AttendanceRecalcTask,
    AttendanceRule,
    AttendanceStatus,
    ClockSource,
    CorrectionStatus,
    DayType,
    WorkHourType,
)
from app.api.v1.event_monitor import _approval_context_from_event
from app.services.approval import _enqueue_approval_terminal_event
from app.services.attendance import AttendanceRecordService, AttendanceReportService
from app.services.attendance_effects import AttendanceApprovalEffectConsumer
from app.services.leave_events import LeaveApprovalBalanceConsumer
from app.models.leave import ApprovalStatus


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        if self.value is None:
            return []
        if isinstance(self.value, list):
            return self.value
        return [self.value]


class _FakeSession:
    def __init__(self):
        self.effects = []
        self.tasks = []
        self.attendance_records = []
        self.punch_records = []
        self.other = []

    async def execute(self, stmt):
        entity = (getattr(stmt, "column_descriptions", None) or [{}])[0].get("entity")
        if entity is AttendanceEffect:
            return _Result(self.effects[0] if self.effects else None)
        if entity is AttendanceRecalcTask:
            return _Result(self.tasks[0] if self.tasks else None)
        if entity is AttendancePunchTimeRecord:
            return _Result(self.punch_records[0] if self.punch_records else None)
        if entity is AttendanceRecord:
            return _Result(self.attendance_records[0] if self.attendance_records else None)
        return _Result(None)

    def add(self, item):
        if isinstance(item, AttendanceEffect):
            self.effects.append(item)
        elif isinstance(item, AttendanceRecalcTask):
            self.tasks.append(item)
        elif isinstance(item, AttendancePunchTimeRecord):
            self.punch_records.append(item)
        elif isinstance(item, AttendanceRecord):
            self.attendance_records.append(item)
        else:
            self.other.append(item)

    async def flush(self):
        return None


def _attendance_rule_for_punch_correction() -> AttendanceRule:
    return AttendanceRule(
        id=1,
        name="标准上下班",
        work_hour_type=WorkHourType.standard,
        clock_in_time=time(9, 0),
        clock_out_time=time(18, 0),
        flexible_minutes=5,
        work_hours_per_day=Decimal("8.0"),
        overtime_weekday_rate=Decimal("1.5"),
        overtime_weekend_rate=Decimal("2.0"),
        overtime_holiday_rate=Decimal("3.0"),
        require_gps=False,
        require_wifi=False,
        require_photo=False,
        is_active=True,
        priority=100,
        effective_date=date(2026, 1, 1),
        extra_config={"late_threshold_minutes": 5},
    )


def _runtime_for_punch_correction(rule: AttendanceRule) -> dict:
    return {
        "rule": rule,
        "expected_in": time(9, 0),
        "expected_out": time(18, 0),
        "check_in_required": True,
        "check_out_required": True,
        "flex_mode": "none",
        "early_threshold_minutes": 0,
        "rest_periods": [],
        "extra": {},
    }


def test_event_monitor_approval_context_exposes_approval_subject():
    event = SimpleNamespace(
        aggregate_type="approval_instance",
        aggregate_id=9,
        payload_json={
            "approval_instance_id": 9,
            "module": "attendance",
            "business_type": "attendance_outside",
            "business_id": 1714291200,
            "applicant_id": 6,
            "applicant_name": "王五",
            "summary": "外出打卡审批 - 王五",
        },
    )

    context = _approval_context_from_event(event)

    assert context["approval_instance_id"] == 9
    assert context["business_type_label"] == "外出"
    assert context["applicant_name"] == "王五"
    assert context["summary"] == "外出打卡审批 - 王五"


@pytest.mark.asyncio
async def test_enqueue_approval_terminal_event_builds_outbox_payload():
    db = AsyncMock()
    event = SimpleNamespace(status="pending")
    instance = SimpleNamespace(
        id=12,
        module="business_trip",
        business_type="business_trip",
        business_id=12,
        template_version_id=None,
        applicant_id=7,
        summary="出差申请",
        form_data={"trip_location": "上海", "start_date": "2026-05-01", "end_date": "2026-05-02"},
        flow_snapshot=None,
        template_version=None,
    )

    with patch(
        "app.services.approval.BusinessEventService.enqueue",
        new=AsyncMock(return_value=event),
    ) as enqueue, patch(
        "app.services.approval.dispatch_event",
        new=AsyncMock(),
    ) as dispatch, patch(
        "app.services.approval._employee_identity_by_id",
        new=AsyncMock(return_value=("赵六", "TG000008")),
    ):
        await _enqueue_approval_terminal_event(
            db,
            instance,
            "approved",
            actor_id=99,
            occurred_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
        )

    enqueue.assert_awaited_once()
    kwargs = enqueue.await_args.kwargs
    assert kwargs["event_type"] == "approval.instance.approved.v1"
    assert kwargs["idempotency_key"] == "approval_instance:12:approved"
    assert kwargs["payload"]["approval_instance_id"] == 12
    assert kwargs["payload"]["applicant_name"] == "赵六"
    assert kwargs["payload"]["applicant_no"] == "TG000008"
    assert kwargs["payload"]["form_data"]["trip_location"] == "上海"
    dispatch.assert_awaited_once_with(db, event)


@pytest.mark.asyncio
async def test_enqueue_approval_terminal_event_does_not_lazy_load_template_version():
    db = AsyncMock()
    event = SimpleNamespace(status="processed")

    class LazyInstance:
        id = 13
        module = "attendance"
        business_type = "punch_correction"
        business_id = 13
        template_version_id = None
        applicant_id = 6
        summary = "补卡申请"
        form_data = {"date_1": "2026-04-24"}
        flow_snapshot = None

        @property
        def template_version(self):
            raise AssertionError("不应触发 template_version 懒加载")

    with patch(
        "app.services.approval.BusinessEventService.enqueue",
        new=AsyncMock(return_value=event),
    ) as enqueue, patch(
        "app.services.approval.dispatch_event",
        new=AsyncMock(),
    ):
        await _enqueue_approval_terminal_event(
            db,
            LazyInstance(),
            "approved",
            actor_id=36,
            occurred_at=datetime(2026, 4, 28, tzinfo=timezone.utc),
        )

    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["payload"]["effect_bindings"] == []


@pytest.mark.asyncio
async def test_enqueue_approval_terminal_event_does_not_block_when_outbox_fails(caplog):
    db = AsyncMock()
    instance = SimpleNamespace(
        id=14,
        module="punch_correction",
        business_type="punch_correction",
        business_id=14,
        template_version_id=None,
        applicant_id=6,
        summary="补卡申请",
        form_data={"punch_time": "2026-04-24T18:30:00+08:00"},
        flow_snapshot=None,
        template_version=None,
    )

    with patch(
        "app.services.approval.BusinessEventService.enqueue",
        new=AsyncMock(side_effect=RuntimeError("outbox unavailable")),
    ):
        await _enqueue_approval_terminal_event(
            db,
            instance,
            "approved",
            actor_id=36,
            occurred_at=datetime(2026, 4, 28, tzinfo=timezone.utc),
        )

    assert "审批终态事件写入或分发失败" in caplog.text


@pytest.mark.asyncio
async def test_attendance_consumer_creates_business_trip_effects_idempotently():
    db = _FakeSession()
    event = SimpleNamespace(
        event_id="evt-1",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 22,
            "module": "business_trip",
            "business_type": "business_trip",
            "business_id": 22,
            "applicant_id": 5,
            "form_data": {
                "trip_location": "广州",
                "start_date": "2026-05-01",
                "end_date": "2026-05-01",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    handled = await consumer.handle(db, event)
    handled_again = await consumer.handle(db, event)

    assert handled is True
    assert handled_again is True
    assert len(db.effects) == 1
    assert db.effects[0].effect_type == "business_trip"
    assert db.effects[0].employee_id == 5
    assert db.effects[0].work_date.isoformat() == "2026-05-01"
    assert len(db.tasks) == 1


@pytest.mark.asyncio
async def test_attendance_consumer_creates_business_trip_effect_from_dynamic_duration_field():
    db = _FakeSession()
    event = SimpleNamespace(
        event_id="evt-trip-duration",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 26,
            "module": "business_trip",
            "business_type": "business_trip",
            "business_id": 26,
            "applicant_id": 6,
            "form_data": {
                "trip_location": "东莞",
                "trip_duration": 1,
                "trip_duration_start_time": "2026-05-05",
                "trip_duration_end_time": "2026-05-05",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    handled = await consumer.handle(db, event)

    assert handled is True
    assert len(db.effects) == 1
    effect = db.effects[0]
    assert effect.effect_type == "business_trip"
    assert effect.work_date.isoformat() == "2026-05-05"
    assert effect.minutes == 480
    assert effect.attendance_status == AttendanceStatus.business_trip.value
    assert len(db.tasks) == 1


@pytest.mark.asyncio
async def test_attendance_consumer_creates_overtime_effect_from_dynamic_duration_field():
    db = _FakeSession()
    event = SimpleNamespace(
        event_id="evt-overtime-duration",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 25,
            "module": "legal_overtime",
            "business_type": "legal_overtime",
            "business_id": 25,
            "applicant_id": 6,
            "form_data": {
                "reason": "木马",
                "duration_7": 10.02,
                "duration_7_start_time": "2026-05-05T10:19",
                "duration_7_end_time": "2026-05-05T20:20",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    handled = await consumer.handle(db, event)

    assert handled is True
    assert len(db.effects) == 1
    effect = db.effects[0]
    assert effect.effect_type == "overtime"
    assert effect.work_date.isoformat() == "2026-05-05"
    assert effect.start_at.isoformat() == "2026-05-05T10:19:00+08:00"
    assert effect.end_at.isoformat() == "2026-05-05T20:20:00+08:00"
    assert effect.minutes == 601
    assert len(db.tasks) == 1


@pytest.mark.asyncio
async def test_attendance_consumer_creates_outside_effect_from_dynamic_duration_field():
    db = _FakeSession()
    event = SimpleNamespace(
        event_id="evt-outside-duration",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 27,
            "module": "outside",
            "business_type": "outside",
            "business_id": 27,
            "applicant_id": 6,
            "form_data": {
                "text_5": "华强北",
                "duration_5": 1,
                "duration_5_start_time": "2026-05-05",
                "duration_5_end_time": "2026-05-05",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    handled = await consumer.handle(db, event)

    assert handled is True
    assert len(db.effects) == 1
    effect = db.effects[0]
    assert effect.effect_type == "outside"
    assert effect.work_date.isoformat() == "2026-05-05"
    assert effect.start_at.isoformat() == "2026-05-05T00:00:00+08:00"
    assert effect.end_at.isoformat() == "2026-05-05T23:59:59+08:00"
    assert effect.minutes == 480
    assert effect.attendance_status == AttendanceStatus.normal.value
    assert len(db.tasks) == 1


@pytest.mark.asyncio
async def test_attendance_consumer_creates_dynamic_punch_correction_effect():
    db = _FakeSession()
    event = SimpleNamespace(
        event_id="evt-punch-1",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 9,
            "module": "punch_correction",
            "business_type": "punch_correction",
            "business_id": 9,
            "applicant_id": 6,
            "form_data": {
                "employee_id": 6,
                "correction_date": "2026-04-24",
                "punch_time": "2026-04-24T18:30:00+08:00",
                "punch_type": "check_out",
                "patch_reason": "下班早退",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    with patch(
        "app.services.attendance.AttendanceRecordService._get_rule_runtime",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.attendance.AttendanceRecordService._recalculate_record_after_punch",
        new=AsyncMock(),
    ), patch(
        "app.services.attendance.AttendanceRecordService._refresh_month_summary_for_date",
        new=AsyncMock(),
    ):
        handled = await consumer.handle(db, event)

    assert handled is True
    assert len(db.effects) == 1
    effect = db.effects[0]
    assert effect.effect_type == "punch_correction"
    assert effect.employee_id == 6
    assert effect.work_date.isoformat() == "2026-04-24"
    assert effect.start_at.isoformat() == "2026-04-24T10:30:00"
    assert effect.payload_json["effect_detail"]["punch_time"] == "2026-04-24T18:30:00+08:00"
    assert effect.payload_json["effect_detail"]["punch_type"] == "check_out"
    assert len(db.punch_records) == 1
    assert db.punch_records[0].punch_type == "clock_out"
    assert len(db.attendance_records) == 1
    assert db.attendance_records[0].clock_out_time.isoformat() == "2026-04-24T10:30:00"
    assert db.attendance_records[0].correction_status == CorrectionStatus.approved
    assert len(db.tasks) == 1


@pytest.mark.asyncio
async def test_approved_clock_in_correction_recalculates_daily_record_to_normal():
    cst = ZoneInfo("Asia/Shanghai")
    db = _FakeSession()
    record = AttendanceRecord(
        id=31,
        employee_id=6,
        date=date(2026, 5, 2),
        clock_out_time=datetime(2026, 5, 2, 18, 30, tzinfo=cst),
        status=AttendanceStatus.late,
        anomaly_type="迟到596分钟",
        source=ClockSource.app,
        correction_status=CorrectionStatus.none,
    )
    db.attendance_records.append(record)
    rule = _attendance_rule_for_punch_correction()
    runtime = _runtime_for_punch_correction(rule)
    event = SimpleNamespace(
        event_id="evt-punch-clock-in-normal",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 10,
            "module": "punch_correction",
            "business_type": "punch_correction",
            "business_id": 10,
            "applicant_id": 6,
            "form_data": {
                "employee_id": 6,
                "correction_date": "2026-05-02",
                "punch_time": "2026-05-02T09:00:00+08:00",
                "punch_correction_slot": "上班卡",
                "reason": "早上忘记打卡",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    with patch(
        "app.services.attendance.AttendanceRecordService._get_rule_runtime",
        new=AsyncMock(return_value=runtime),
    ), patch(
        "app.services.attendance.WorkCalendarService.get_day_type",
        new=AsyncMock(return_value=DayType.workday),
    ), patch(
        "app.services.attendance.AttendanceRecordService._load_approved_overtime_entries",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.services.attendance.AttendanceRecordService._sync_comp_time_balance_delta",
        new=AsyncMock(),
    ), patch(
        "app.services.attendance.AttendanceRecordService._calculate_overtime",
        return_value=Decimal("0.00"),
    ), patch(
        "app.services.attendance.AttendanceRecordService._refresh_month_summary_for_date",
        new=AsyncMock(),
    ):
        handled = await consumer.handle(db, event)

    assert handled is True
    assert db.punch_records[0].punch_type == "clock_in"
    assert db.punch_records[0].source == "approval"
    assert record.clock_in_time.isoformat() == "2026-05-02T01:00:00"
    assert record.clock_out_time.isoformat() == "2026-05-02T18:30:00+08:00"
    assert record.status == AttendanceStatus.normal
    assert record.anomaly_type is None
    assert record.correction_status == CorrectionStatus.approved

    AttendanceRecordService._attach_display_status(record, runtime)
    assert AttendanceReportService._status_text(record) == "正常"
    assert AttendanceReportService._attendance_clock_count(record) == 2
    assert AttendanceReportService._extract_minutes(record.anomaly_type, "迟到") == 0


@pytest.mark.asyncio
async def test_second_approval_punch_uses_same_storage_timezone_for_work_hours():
    db = _FakeSession()
    record = AttendanceRecord(
        id=33,
        employee_id=6,
        date=date(2026, 4, 30),
        # 模拟第一张补卡已落 MySQL 后读回的 UTC naive 下班卡：北京时间 18:30。
        clock_out_time=datetime(2026, 4, 30, 10, 30),
        status=AttendanceStatus.missed_clock,
        source=ClockSource.manual,
        correction_status=CorrectionStatus.approved,
    )
    db.attendance_records.append(record)
    rule = AttendanceRule(
        id=1,
        name="标准上下班",
        work_hour_type=WorkHourType.standard,
        clock_in_time=time(9, 30),
        clock_out_time=time(18, 30),
        flexible_minutes=0,
        work_hours_per_day=Decimal("8.0"),
        overtime_weekday_rate=Decimal("1.5"),
        overtime_weekend_rate=Decimal("2.0"),
        overtime_holiday_rate=Decimal("3.0"),
        require_gps=False,
        require_wifi=False,
        require_photo=False,
        is_active=True,
        priority=100,
        effective_date=date(2026, 1, 1),
        extra_config={
            "enable_overtime": True,
            "overtime_policy": {
                "workday": {
                    "enabled": True,
                    "period_mode": "all",
                    "calc_method": "by_approval",
                    "max_minutes": 240,
                    "min_minutes": 30,
                    "allow_rest_deduction": True,
                    "rest_periods": [{"start": "12:00", "end": "13:00"}],
                }
            },
        },
    )
    runtime = {
        "rule": rule,
        "expected_in": time(9, 30),
        "expected_out": time(18, 30),
        "check_in_required": True,
        "check_out_required": True,
        "flex_mode": "none",
        "early_threshold_minutes": 0,
        "half_day_pm_start": None,
        "half_day_absence_cutoff": None,
        "rest_periods": [(time(12, 0), time(13, 0))],
        "extra": rule.extra_config,
        "employee": SimpleNamespace(location_id=1),
    }
    event = SimpleNamespace(
        event_id="evt-punch-clock-in-after-clock-out",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 16,
            "module": "punch_correction",
            "business_type": "punch_correction",
            "business_id": 16,
            "applicant_id": 6,
            "form_data": {
                "employee_id": 6,
                "correction_date": "2026-04-30",
                "punch_time": "2026-04-30T09:30:00+08:00",
                "punch_correction_slot": "上班卡",
                "reason": "早上忘记打卡",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    with patch(
        "app.services.attendance.AttendanceRecordService._get_rule_runtime",
        new=AsyncMock(return_value=runtime),
    ), patch(
        "app.services.attendance.WorkCalendarService.get_day_type",
        new=AsyncMock(return_value=DayType.workday),
    ), patch(
        "app.services.attendance.AttendanceRecordService._load_approved_overtime_entries",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.services.attendance.AttendanceRecordService._sync_comp_time_balance_delta",
        new=AsyncMock(),
    ), patch(
        "app.services.attendance.AttendanceRecordService._refresh_month_summary_for_date",
        new=AsyncMock(),
    ):
        handled = await consumer.handle(db, event)

    assert handled is True
    assert record.clock_in_time.isoformat() == "2026-04-30T01:30:00"
    assert record.clock_out_time.isoformat() == "2026-04-30T10:30:00"
    assert record.work_hours == Decimal("8.00")
    assert record.overtime_hours == Decimal("0.00")
    assert record.status == AttendanceStatus.normal
    assert db.punch_records[0].punch_time.isoformat() == "2026-04-30T01:30:00"


@pytest.mark.asyncio
async def test_late_clock_in_correction_keeps_late_but_recalculates_minutes():
    cst = ZoneInfo("Asia/Shanghai")
    db = _FakeSession()
    record = AttendanceRecord(
        id=32,
        employee_id=6,
        date=date(2026, 5, 2),
        clock_out_time=datetime(2026, 5, 2, 18, 30, tzinfo=cst),
        status=AttendanceStatus.late,
        anomaly_type="迟到596分钟",
        source=ClockSource.app,
        correction_status=CorrectionStatus.none,
    )
    db.attendance_records.append(record)
    rule = _attendance_rule_for_punch_correction()
    runtime = _runtime_for_punch_correction(rule)
    event = SimpleNamespace(
        event_id="evt-punch-clock-in-late",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 11,
            "module": "punch_correction",
            "business_type": "punch_correction",
            "business_id": 11,
            "applicant_id": 6,
            "form_data": {
                "employee_id": 6,
                "correction_date": "2026-05-02",
                "punch_time": "2026-05-02T10:00:00+08:00",
                "punch_correction_slot": "上班卡",
                "reason": "补卡时间晚于宽限",
            },
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    with patch(
        "app.services.attendance.AttendanceRecordService._get_rule_runtime",
        new=AsyncMock(return_value=runtime),
    ), patch(
        "app.services.attendance.WorkCalendarService.get_day_type",
        new=AsyncMock(return_value=DayType.workday),
    ), patch(
        "app.services.attendance.AttendanceRecordService._load_approved_overtime_entries",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.services.attendance.AttendanceRecordService._sync_comp_time_balance_delta",
        new=AsyncMock(),
    ), patch(
        "app.services.attendance.AttendanceRecordService._calculate_overtime",
        return_value=Decimal("0.00"),
    ), patch(
        "app.services.attendance.AttendanceRecordService._refresh_month_summary_for_date",
        new=AsyncMock(),
    ):
        handled = await consumer.handle(db, event)

    assert handled is True
    assert db.punch_records[0].punch_type == "clock_in"
    assert record.clock_in_time.isoformat() == "2026-05-02T02:00:00"
    assert record.status == AttendanceStatus.late
    assert record.anomaly_type == "迟到55分钟"

    AttendanceRecordService._attach_display_status(record, runtime)
    assert AttendanceReportService._status_text(record) == "迟到"
    assert AttendanceReportService._attendance_clock_count(record) == 2
    assert AttendanceReportService._extract_minutes(record.anomaly_type, "迟到") == 55


@pytest.mark.asyncio
async def test_attendance_consumer_skips_unrelated_approval_event():
    db = _FakeSession()
    event = SimpleNamespace(
        event_id="evt-2",
        event_type="approval.instance.approved.v1",
        payload_json={
            "approval_instance_id": 23,
            "module": "recruitment",
            "business_type": "recruitment_demand",
            "business_id": 1,
            "applicant_id": 5,
            "form_data": {},
        },
    )
    consumer = AttendanceApprovalEffectConsumer()

    handled = await consumer.handle(db, event)

    assert handled is False
    assert db.effects == []
    assert db.tasks == []


@pytest.mark.asyncio
async def test_leave_balance_consumer_deducts_approved_leave_idempotently():
    leave_type = SimpleNamespace(
        id=3,
        name="年假",
        quota_limited=True,
        max_days_per_year=Decimal("5.0"),
        deduct_from="annual_leave",
    )
    leave = SimpleNamespace(
        id=41,
        employee_id=7,
        leave_type_id=3,
        leave_type=leave_type,
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        days=Decimal("1.0"),
        approval_status=ApprovalStatus.pending,
        approver_id=None,
        approved_at=None,
    )
    balance = SimpleNamespace(
        id=55,
        employee_id=7,
        leave_type_id=3,
        year=2026,
        total_days=Decimal("5.0"),
        used_days=Decimal("0.0"),
        remaining_days=Decimal("5.0"),
        expired_days=Decimal("0.0"),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result(leave), _Result([]), _Result(balance), _Result(None)])
    db.scalar = AsyncMock(return_value=Decimal("1.0"))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    event = SimpleNamespace(
        event_id="evt-leave-balance-1",
        event_type="approval.instance.approved.v1",
        occurred_at=datetime(2026, 5, 6, 9, 0, tzinfo=timezone.utc),
        payload_json={
            "approval_instance_id": 90,
            "module": "leave",
            "business_type": "leave_request",
            "business_id": 41,
            "applicant_id": 7,
            "approver_id": 8,
            "summary": "年假 1 天",
        },
    )

    handled = await LeaveApprovalBalanceConsumer().handle(db, event)

    assert handled is True
    assert leave.approval_status == ApprovalStatus.approved
    assert leave.approver_id == 8
    assert balance.used_days == Decimal("1.0")
    assert balance.remaining_days == Decimal("4.0")
    audit_log = db.add.call_args.args[0]
    assert audit_log.module == "leave"
    assert audit_log.action == "balance_adjust"
    assert audit_log.operator_name == "系统"
    assert "evt-leave-balance-1" in audit_log.after_data
    assert '"adjustment": -1.0' in audit_log.after_data


@pytest.mark.asyncio
async def test_leave_balance_consumer_skips_leave_type_without_balance_rule():
    leave_type = SimpleNamespace(
        id=4,
        name="婚假",
        quota_limited=False,
        max_days_per_year=None,
        deduct_from="none",
    )
    leave = SimpleNamespace(
        id=42,
        employee_id=7,
        leave_type_id=4,
        leave_type=leave_type,
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        days=Decimal("1.0"),
        approval_status=ApprovalStatus.pending,
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result(leave), _Result(None)])
    event = SimpleNamespace(
        event_id="evt-leave-balance-skip",
        event_type="approval.instance.approved.v1",
        payload_json={
            "module": "leave",
            "business_type": "leave_request",
            "business_id": 42,
        },
    )

    handled = await LeaveApprovalBalanceConsumer().handle(db, event)

    assert handled is False
    db.scalar.assert_not_called()
