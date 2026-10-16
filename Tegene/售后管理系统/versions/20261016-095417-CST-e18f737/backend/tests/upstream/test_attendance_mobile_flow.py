"""
Red-phase tests for the mobile attendance flow story.

These tests encode the story contract for the new mobile attendance and
outside-approval flow. The current baseline is expected to fail them because
the required endpoints, schemas, and service entry points are not yet present.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app


def _route_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


class _AsyncLikeSession:
    def __init__(self, sync_session):
        self._sync = sync_session

    def add(self, obj):
        self._sync.add(obj)

    def add_all(self, objects):
        self._sync.add_all(objects)

    async def execute(self, *args, **kwargs):
        return self._sync.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self._sync.scalar(*args, **kwargs)

    async def flush(self):
        self._sync.flush()

    async def refresh(self, obj):
        self._sync.refresh(obj)

    async def delete(self, obj):
        self._sync.delete(obj)

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._sync, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._sync, name)


class TestAttendanceMobileContract:
    def test_mobile_attendance_registers_the_new_contract_endpoints(self) -> None:
        paths = _route_paths()

        assert "/api/v1/attendance/outside-approval" in paths, (
            "AC5 expects a POST /api/v1/attendance/outside-approval endpoint "
            "that creates a durable outside-approval instance."
        )
        assert "/api/v1/attendance/my/runtime" in paths, (
            "AC6 expects a mobile runtime endpoint for the attendance page."
        )
        assert "/api/v1/attendance/mobile/dashboard" in paths, (
            "AC6 expects a mobile dashboard endpoint for today/statistics data."
        )
        assert "/api/v1/attendance/mobile/comp-time-details" in paths, (
            "home comp-time balance should open employee-scoped overtime-to-rest detail rows."
        )
        assert "/api/v1/attendance/mobile/export" in paths, (
            "mobile statistics export should be a real employee-scoped backend endpoint, "
            "not a frontend-only CSV assembled from stale UI state."
        )

    def test_mobile_attendance_exposes_outside_approval_schemas(self) -> None:
        import app.schemas.attendance as attendance_schemas

        assert getattr(attendance_schemas, "AttendanceOutsideApprovalCreate", None) is not None, (
            "AC3/AC5 require AttendanceOutsideApprovalCreate with address, "
            "coordinates, client, remark, photo_url, and occurred_at."
        )
        assert getattr(attendance_schemas, "AttendanceOutsideApprovalOut", None) is not None, (
            "AC5 requires AttendanceOutsideApprovalOut so the API can return "
            "the approval instance id and durable payload summary."
        )
        assert getattr(attendance_schemas, "MobileDashboardOut", None) is not None, (
            "AC6 requires MobileDashboardOut for the mobile attendance summary."
        )

    def test_mobile_dashboard_route_uses_the_real_response_contract(self) -> None:
        import app.schemas.attendance as attendance_schemas

        route = next(
            item
            for item in app.routes
            if getattr(item, "path", "") == "/api/v1/attendance/mobile/dashboard"
        )
        assert getattr(route, "response_model", None) is attendance_schemas.MobileDashboardOut

        payload = {
            "range": {
                "type": "month",
                "target_date": "2026-04-30",
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "year": 2026,
                "month": 4,
            },
            "applications": {
                "items": [
                    {"key": "leave", "label": "请假", "module": "leave", "pending": 0},
                    {"key": "outside", "label": "外出", "module": "attendance_outside", "pending": 1},
                ],
                "record_count": 1,
            },
            "stats": {
                "normal_days": 2,
                "abnormal_days": 2,
                "late_count": 0,
                "early_count": 0,
                "missed_count": 1,
                "absent_count": 1,
                "leave_count": 0,
                "correction_count": 0,
                "outside_count": 1,
                "business_trip_count": 0,
                "field_work_count": 0,
                "overtime_minutes": 0,
                "overtime_hours": 0,
            },
            "monthly_abnormal": {
                "pending_days": 2,
                "has_today_abnormal": True,
                "today_status": "缺卡",
                "today_anomaly": "缺少下班打卡",
                "latest_date": "2026-04-30",
                "latest_status": "缺卡",
                "latest_anomaly": "缺少下班打卡",
            },
            "daily": {
                "clock_in_time": "2026-04-30T09:30:00+08:00",
                "clock_out_time": None,
                "status": "缺卡",
                "is_abnormal": True,
                "anomaly_type": "缺少下班打卡",
                "clock_in_status": "正常",
                "clock_out_status": "未打卡",
                "location_status": "范围内",
                "location_abnormal": False,
                "work_minutes": 0,
                "expected_minutes": 480,
            },
            "details": {
                "attendance_records": [
                    {
                        "id": 1,
                        "date": "2026-04-30",
                        "status": "missed_clock",
                        "display_status": "缺卡",
                        "anomaly_type": "缺少下班打卡",
                        "is_abnormal": True,
                        "is_rest_day": False,
                        "day_type": "workday",
                        "day_type_label": "工作日",
                        "clock_in_time": "2026-04-30T09:30:00+08:00",
                        "clock_out_time": None,
                        "clock_in_status": "正常",
                        "clock_out_status": "未打卡",
                        "work_minutes": 0,
                        "overtime_minutes": 0,
                        "source": "app",
                    }
                ],
                "leave_items": [
                    {
                        "id": "correction:1",
                        "date": "2026-04-30",
                        "type": "punch_correction",
                        "label": "补卡",
                        "status": "pending",
                    }
                ],
                "overtime_items": [
                    {
                        "id": "overtime:1",
                        "date": "2026-04-10",
                        "date_type": "workday",
                        "date_type_label": "工作日",
                        "minutes": 126,
                        "hours": 2.1,
                        "settlement": "comp_time",
                        "settlement_label": "调休(1:1)",
                    }
                ],
                "overtime_month_items": [
                    {
                        "id": "overtime-month:2026-04",
                        "year": 2026,
                        "month": 4,
                        "label": "2026年4月",
                        "minutes": 126,
                        "hours": 2.1,
                        "source_count": 1,
                    }
                ],
                "counts": {
                    "attendance": {"all": 1, "missed_clock": 1, "abnormal": 1},
                    "leave": {"all": 1, "punch_correction": 1},
                    "overtime_date_type": {"all": 1, "workday": 1},
                    "overtime_settlement": {"all": 1, "comp_time": 1},
                },
            },
        }
        dashboard = attendance_schemas.MobileDashboardOut.model_validate(payload)
        assert dashboard.stats.normal_days == 2
        assert dashboard.stats.outside_count == 1
        assert dashboard.monthly_abnormal.pending_days == 2
        assert dashboard.daily.clock_out_status == "未打卡"
        assert dashboard.details.attendance_records[0].display_status == "缺卡"
        assert dashboard.details.leave_items[0].type == "punch_correction"
        assert dashboard.details.overtime_items[0].settlement == "comp_time"
        assert dashboard.details.overtime_month_items[0].label == "2026年4月"

    def test_mobile_dashboard_overtime_hours_follow_comp_time_balance_total(self) -> None:
        from app.api.v1.attendance import _comp_time_balance_hours_from_balance

        leave_type = SimpleNamespace(
            code="comp_time",
            leave_unit="hour",
            hours_per_day=Decimal("8.0"),
        )
        balance = SimpleNamespace(
            total_days=Decimal("0.625"),
            remaining_days=Decimal("0.375"),
        )

        hours = _comp_time_balance_hours_from_balance(leave_type, balance)

        assert hours == Decimal("5.0")

    @pytest.mark.asyncio
    async def test_mobile_dashboard_counts_approval_effect_punch_corrections_by_target_date(self, tmp_path) -> None:
        import app.models  # noqa: F401
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session, sessionmaker

        from app.api.v1.attendance import get_mobile_attendance_dashboard
        from app.core.database import Base
        from app.models.attendance import AttendanceEffect
        from app.models.employee import Employee, EmployeeStatus

        db_path = tmp_path / "mobile-dashboard-punch-correction.sqlite3"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

        try:
            with session_factory() as sync_session:
                employee = Employee(
                    employee_no="TG-MOBILE-001",
                    name="移动端补卡员工",
                    phone="13900001001",
                    status=EmployeeStatus.ACTIVE.value,
                    is_active=True,
                )
                sync_session.add(employee)
                sync_session.flush()
                target_day = date(2026, 4, 30)
                sync_session.add_all(
                    [
                        AttendanceEffect(
                            source_module="approval",
                            source_event_id="evt-mobile-correction-in",
                            source_instance_id=501,
                            source_business_type="punch_correction",
                            source_business_id=501,
                            employee_id=employee.id,
                            effect_type="punch_correction",
                            effect_status="active",
                            work_date=target_day,
                            start_at=datetime(2026, 4, 30, 1, 28, tzinfo=timezone.utc),
                            end_at=datetime(2026, 4, 30, 1, 28, tzinfo=timezone.utc),
                            minutes=0,
                            payload_json={
                                "effect_detail": {
                                    "punch_type": "check_in",
                                    "punch_time": "2026-04-30T09:28:00+08:00",
                                    "reason": "上班卡补卡",
                                }
                            },
                        ),
                        AttendanceEffect(
                            source_module="approval",
                            source_event_id="evt-mobile-correction-out",
                            source_instance_id=502,
                            source_business_type="punch_correction",
                            source_business_id=502,
                            employee_id=employee.id,
                            effect_type="punch_correction",
                            effect_status="active",
                            work_date=target_day,
                            start_at=datetime(2026, 4, 30, 10, 35, tzinfo=timezone.utc),
                            end_at=datetime(2026, 4, 30, 10, 35, tzinfo=timezone.utc),
                            minutes=0,
                            payload_json={
                                "effect_detail": {
                                    "punch_type": "check_out",
                                    "punch_time": "2026-04-30T18:35:00+08:00",
                                    "reason": "下班卡补卡",
                                }
                            },
                        ),
                    ]
                )
                sync_session.flush()
                db = _AsyncLikeSession(sync_session)

                payload = await get_mobile_attendance_dashboard(
                    range_type="week",
                    target_date="2026-05-03",
                    db=db,
                    current_user=employee,
                )

                correction_rows = [
                    item for item in payload["details"]["leave_items"]
                    if item["type"] == "punch_correction"
                ]
                assert payload["stats"]["correction_count"] == 2
                assert payload["details"]["counts"]["leave"]["punch_correction"] == 2
                assert [item["date"] for item in correction_rows] == ["2026-04-30", "2026-04-30"]
                assert {item["punch_type"] for item in correction_rows} == {"check_in", "check_out"}
        finally:
            engine.dispose()

    @pytest.mark.asyncio
    async def test_mobile_comp_time_details_returns_dated_overtime_to_rest_rows(self, tmp_path) -> None:
        import app.models  # noqa: F401
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session, sessionmaker

        from app.api.v1.attendance import get_mobile_comp_time_details
        from app.core.database import Base
        from app.models.audit_log import AuditLog
        from app.models.attendance import AttendanceEffect
        from app.models.employee import Employee, EmployeeStatus
        from app.models.leave import LeaveBalance, LeaveType

        db_path = tmp_path / "mobile-comp-time-details.sqlite3"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

        try:
            with session_factory() as sync_session:
                employee = Employee(
                    employee_no="TG-COMP-001",
                    name="调休明细员工",
                    phone="13900002001",
                    status=EmployeeStatus.ACTIVE.value,
                    is_active=True,
                )
                sync_session.add(employee)
                sync_session.flush()
                leave_type = LeaveType(
                    name="调休",
                    code="comp_time",
                    leave_unit="hour",
                    hours_per_day=Decimal("8.0"),
                    is_active=True,
                )
                sync_session.add(leave_type)
                sync_session.flush()
                balance = LeaveBalance(
                    employee_id=employee.id,
                    leave_type_id=leave_type.id,
                    year=2026,
                    total_days=Decimal("0.075"),
                    used_days=Decimal("0"),
                    remaining_days=Decimal("0.075"),
                    expired_days=Decimal("0"),
                )
                sync_session.add(balance)
                sync_session.flush()
                sync_session.add(
                    AuditLog(
                        operator_id=employee.id,
                        operator_name=employee.name,
                        action="balance_adjust",
                        module="leave",
                        resource_id=balance.id,
                        resource_type="LeaveBalance",
                    )
                )
                sync_session.add(
                    AttendanceEffect(
                        source_module="approval",
                        source_event_id="evt-comp-time-001",
                        source_instance_id=7001,
                        source_business_type="overtime",
                        source_business_id=7001,
                        employee_id=employee.id,
                        effect_type="overtime",
                        effect_status="active",
                        work_date=date(2026, 5, 9),
                        start_at=datetime(2026, 5, 9, 10, 30, tzinfo=timezone.utc),
                        end_at=datetime(2026, 5, 9, 10, 48, tzinfo=timezone.utc),
                        minutes=18,
                        pay_policy="comp_time",
                        payload_json={"reason": "下班后处理报表"},
                    )
                )
                sync_session.flush()
                db = _AsyncLikeSession(sync_session)

                rows = await get_mobile_comp_time_details(
                    year=2026,
                    db=db,
                    current_user=employee,
                )

                assert len(rows) == 2
                effect_row = next(item for item in rows if item["source"] == "attendance_effect")
                delta_row = next(item for item in rows if item["source"] == "leave_balance_adjustment")
                assert effect_row["date"] == "2026-05-09"
                assert effect_row["hours"] == 0.3
                assert effect_row["minutes"] == 18
                assert effect_row["settlement"] == "comp_time"
                assert effect_row["start_time"] == "18:30"
                assert effect_row["end_time"] == "18:48"
                assert effect_row["reason"] == "下班后处理报表"
                assert delta_row["display_date_label"] == "未关联日期"
                assert delta_row["date_type_label"] == "余额差额"
                assert delta_row["hours"] == 0.3
                assert delta_row["minutes"] == 18
                assert delta_row["source_note"] == "这部分余额没有匹配到具体加班来源，可能来自历史同步或人工调整"
        finally:
            engine.dispose()

    @pytest.mark.asyncio
    async def test_mobile_comp_time_details_clears_stale_auto_balance_without_sources(self, tmp_path) -> None:
        import app.models  # noqa: F401
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import Session, sessionmaker

        from app.api.v1.attendance import get_mobile_comp_time_details
        from app.core.database import Base
        from app.models.employee import Employee, EmployeeStatus
        from app.models.leave import LeaveBalance, LeaveType

        db_path = tmp_path / "mobile-comp-time-stale-balance.sqlite3"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

        try:
            with session_factory() as sync_session:
                employee = Employee(
                    employee_no="TG-COMP-STALE",
                    name="调休旧余额员工",
                    phone="13900002002",
                    status=EmployeeStatus.ACTIVE.value,
                    is_active=True,
                )
                sync_session.add(employee)
                sync_session.flush()
                leave_type = LeaveType(
                    name="调休",
                    code="comp_time",
                    leave_unit="hour",
                    hours_per_day=Decimal("8.0"),
                    is_active=True,
                )
                sync_session.add(leave_type)
                sync_session.flush()
                balance = LeaveBalance(
                    employee_id=employee.id,
                    leave_type_id=leave_type.id,
                    year=2026,
                    total_days=Decimal("0.301"),
                    used_days=Decimal("0"),
                    remaining_days=Decimal("0.301"),
                    expired_days=Decimal("0"),
                )
                sync_session.add(balance)
                sync_session.flush()
                db = _AsyncLikeSession(sync_session)

                rows = await get_mobile_comp_time_details(
                    year=2026,
                    db=db,
                    current_user=employee,
                )
                refreshed = sync_session.execute(
                    select(LeaveBalance).where(LeaveBalance.id == balance.id)
                ).scalar_one()

                assert rows == []
                assert refreshed.total_days == Decimal("0.000")
                assert refreshed.remaining_days == Decimal("0.000")
        finally:
            engine.dispose()

    def test_outside_approval_service_contract_requires_durable_fresh_session_visibility(self) -> None:
        import app.services.attendance as attendance_service

        create_outside_approval = getattr(attendance_service, "create_outside_approval", None)
        assert callable(create_outside_approval), (
            "AC5 requires an attendance.create_outside_approval service that "
            "creates the approval row and can be verified from a fresh session "
            "or new connection after the request finishes."
        )

        params = list(signature(create_outside_approval).parameters)
        assert params[:3] == ["db", "employee", "data"], (
            "create_outside_approval should accept (db, employee, data) so the "
            "request-owned transaction can persist the approval instance."
        )

    @pytest.mark.asyncio
    async def test_tencent_web_sdk_config_degrades_without_service_key(self, monkeypatch) -> None:
        from app.api.v1.attendance import get_tencent_web_sdk_config
        from app.core.config import settings

        monkeypatch.setattr(settings, "TENCENT_MAP_WEB_SERVICE_KEY", None)
        monkeypatch.setattr(settings, "TENCENT_MAP_BROWSER_KEY", None)
        db = AsyncMock()
        db.get.return_value = None

        payload = await get_tencent_web_sdk_config(db, SimpleNamespace(id=1))

        assert payload["enabled"] is False
        assert payload["key"] == ""
        assert payload["unavailable_reason"] == "腾讯地图服务未配置"

    @pytest.mark.asyncio
    async def test_tencent_web_sdk_config_returns_enabled_config_when_key_exists(self, monkeypatch) -> None:
        from app.api.v1.attendance import get_tencent_web_sdk_config
        from app.core.config import settings

        monkeypatch.setattr(settings, "TENCENT_MAP_BROWSER_KEY", "test-map-key")
        db = AsyncMock()
        db.get.return_value = None

        payload = await get_tencent_web_sdk_config(db, SimpleNamespace(id=1))

        assert payload["enabled"] is True
        assert payload["key"] == "test-map-key"
        assert payload["script_url"] == "https://map.qq.com/api/gljs"
