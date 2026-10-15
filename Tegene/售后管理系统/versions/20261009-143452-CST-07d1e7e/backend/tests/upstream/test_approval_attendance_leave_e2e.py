from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.database import Base
from app.models.approval import ApprovalTask
from app.models.audit_log import AuditLog
from app.models.attendance import (
    AttendanceEffect,
    AttendanceMonthSummary,
    AttendanceRecalcTask,
    DayType,
    WorkCalendar,
)
from app.models.business_event import BusinessEventConsumption, BusinessEventOutbox
from app.models.employee import Employee, EmployeeStatus
from app.models.leave import ApprovalStatus, HalfDay, LeaveBalance, LeaveRequest, LeaveType
from app.schemas.approval import ApprovalActionRequest, ApprovalApplicationSubmit
from app.schemas.attendance import MonthSummaryGenerateRequest
from app.services import approval as approval_service
from app.services.attendance import AttendanceReportService, MonthSummaryService
from app.services.leave import LeaveBalanceService
from app.services.leave_events import LeaveApprovalBalanceConsumer


class AsyncLikeSession:
    def __init__(self, sync_session: Session):
        self._sync = sync_session

    class _AsyncTransaction:
        def __init__(self, transaction):
            self._transaction = transaction

        async def __aenter__(self):
            self._transaction.__enter__()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return self._transaction.__exit__(exc_type, exc, tb)

    def begin_nested(self):
        return self._AsyncTransaction(self._sync.begin_nested())

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

    async def commit(self):
        self._sync.commit()

    async def rollback(self):
        self._sync.rollback()

    async def close(self):
        self._sync.close()

    def __getattr__(self, name):
        return getattr(self._sync, name)


@pytest.mark.asyncio
async def test_mobile_leave_approval_to_attendance_effect_and_summary(tmp_path: Path):
    db_path = tmp_path / "approval-attendance-leave-e2e.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    leave_day = date(2026, 5, 4)
    month_start = date(2026, 5, 1)

    try:
        with session_factory() as sync_session:
            db = AsyncLikeSession(sync_session)
            applicant = Employee(
                employee_no="TG-E2E-001",
                name="移动端请假员工",
                phone="13900000001",
                status=EmployeeStatus.ACTIVE.value,
                is_active=True,
            )
            approver = Employee(
                employee_no="TG-E2E-002",
                name="审批主管",
                phone="13900000002",
                status=EmployeeStatus.ACTIVE.value,
                is_active=True,
            )
            sync_session.add_all([applicant, approver])
            sync_session.flush()

            current = month_start
            while current.month == 5:
                sync_session.add(
                    WorkCalendar(
                        date=current,
                        location_id=None,
                        day_type=DayType.workday if current == leave_day else DayType.weekend,
                    )
                )
                current += timedelta(days=1)
            sync_session.flush()

            template = await approval_service.save_template_config(
                db,
                {
                    "name": "请假",
                    "business_code": "leave",
                    "category": "假勤管理",
                    "scope": "mobile",
                    "is_active": True,
                    "fields": [
                        {"label": "所在公司", "code": "company", "field_type": "company", "is_required": True},
                        {
                            "label": "请假类型",
                            "code": "leave_type",
                            "field_type": "select",
                            "is_required": True,
                            "options_json": {"options": ["年假", "病假", "事假"], "attendance_component": "leave"},
                        },
                        {"label": "开始时间", "code": "start_time", "field_type": "date", "is_required": True},
                        {"label": "结束时间", "code": "end_time", "field_type": "date", "is_required": True},
                        {
                            "label": "请假时长",
                            "code": "duration",
                            "field_type": "duration",
                            "is_required": True,
                            "is_business_calculation": True,
                            "options_json": {"attendance_component": "leave", "duration_mode": "natural_day"},
                        },
                        {"label": "请假事由", "code": "reason", "field_type": "textarea", "is_required": False},
                    ],
                    "flow_nodes": [
                        {
                            "node_order": 1,
                            "node_type": "approval",
                            "approver_type": "specific_user",
                            "approver_id": approver.id,
                        }
                    ],
                },
            )

            instance = await approval_service.submit_dynamic_application(
                db,
                ApprovalApplicationSubmit(
                    approval_type_id=template.id,
                    summary="移动端年假 1 天",
                    form_data={
                        "company": 1,
                        "leave_type": "年假",
                        "start_time": leave_day.isoformat(),
                        "end_time": leave_day.isoformat(),
                        "duration": 1,
                        "reason": "全链路验收",
                    },
                ),
                applicant.id,
            )
            task = sync_session.execute(
                select(ApprovalTask).where(ApprovalTask.instance_id == instance.id)
            ).scalar_one()
            assert task.approver_id == approver.id

            approved = await approval_service.process_approval(
                db,
                instance_id=instance.id,
                approver_id=approver.id,
                data=ApprovalActionRequest(action="approve", comment="同意"),
            )
            assert approved.status == "approved"

            await MonthSummaryService.generate(
                db,
                MonthSummaryGenerateRequest(year=2026, month=5, employee_ids=[applicant.id]),
            )
            report = await AttendanceReportService.get_monthly_report_data(
                db,
                start_date=leave_day,
                end_date=leave_day,
                keyword="移动端请假员工",
                include_recent_left=False,
                page=1,
                page_size=20,
            )
            sync_session.commit()

            event = sync_session.execute(select(BusinessEventOutbox)).scalar_one()
            assert event.event_type == "approval.instance.approved.v1"
            assert event.status == "dispatched"
            assert event.payload_json["module"] == "leave"
            assert event.payload_json["form_data"]["leave_type"] == "年假"

            consumptions = sync_session.execute(select(BusinessEventConsumption)).scalars().all()
            consumption_by_name = {item.consumer_name: item for item in consumptions}
            assert consumption_by_name["attendance.approval_effect_consumer"].status == "succeeded"
            assert consumption_by_name["leave.approval_balance_consumer"].status == "succeeded"

            effect = sync_session.execute(select(AttendanceEffect)).scalar_one()
            assert effect.source_instance_id == instance.id
            assert effect.effect_type == "leave"
            assert effect.effect_status == "active"
            assert effect.work_date == leave_day
            assert effect.minutes == 480
            assert effect.payload_json["effect_detail"]["leave_type_code"] == "annual"

            recalc_task = sync_session.execute(select(AttendanceRecalcTask)).scalar_one()
            assert recalc_task.employee_id == applicant.id
            assert recalc_task.work_date == leave_day
            assert recalc_task.status == "pending"

            summary = sync_session.execute(
                select(AttendanceMonthSummary).where(
                    AttendanceMonthSummary.employee_id == applicant.id,
                    AttendanceMonthSummary.year == 2026,
                    AttendanceMonthSummary.month == 5,
                )
            ).scalar_one()
            assert str(summary.leave_days) == "1.0"
            assert summary.absent_count == 0

            overview = report["overview_rows"][0]
            detail = report["detail_rows"][0]
            day_key = report["detail_day_columns"][0]["key"]
            assert "1" in overview["annual_leave_days"]
            assert "请假" in detail[day_key]
            assert "年假" in detail[day_key]
            balance = sync_session.execute(
                select(LeaveBalance)
                .join(LeaveType, LeaveType.id == LeaveBalance.leave_type_id)
                .where(
                    LeaveBalance.employee_id == applicant.id,
                    LeaveType.code == "annual",
                    LeaveBalance.year == 2026,
                )
            ).scalar_one()
            assert balance.used_days == Decimal("1.000")
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_leave_approval_balance_consumer_updates_balance_and_audit_log(tmp_path: Path):
    db_path = tmp_path / "approval-leave-balance-consumer.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    try:
        with session_factory() as sync_session:
            db = AsyncLikeSession(sync_session)
            employee = Employee(
                employee_no="TG-LEAVE-BAL-001",
                name="请假余额员工",
                phone="13900000005",
                status=EmployeeStatus.ACTIVE.value,
                is_active=True,
            )
            leave_type = LeaveType(
                name="年假",
                code="annual",
                is_paid=True,
                max_days_per_year=Decimal("5.0"),
                requires_proof=False,
                deduct_from="annual_leave",
                policy_region="all",
                is_active=True,
                quota_limited=True,
                leave_unit="day",
                time_calc="workday",
                hours_per_day=Decimal("8.0"),
            )
            sync_session.add_all([employee, leave_type])
            sync_session.flush()
            balance = LeaveBalance(
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                year=2026,
                total_days=Decimal("5.0"),
                used_days=Decimal("0.0"),
                remaining_days=Decimal("5.0"),
                expired_days=Decimal("0.0"),
            )
            leave_request = LeaveRequest(
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                start_date=date(2026, 5, 18),
                end_date=date(2026, 5, 18),
                start_half=HalfDay.am,
                end_half=HalfDay.pm,
                days=Decimal("1.0"),
                reason="审批通过扣减测试",
                approval_status=ApprovalStatus.pending,
            )
            sync_session.add_all([balance, leave_request])
            sync_session.flush()

            event = BusinessEventOutbox(
                event_id="evt-leave-balance-sqlite",
                event_type="approval.instance.approved.v1",
                aggregate_type="approval_instance",
                aggregate_id=701,
                source_module="approval",
                idempotency_key="approval_instance:701:approved",
                payload_json={
                    "approval_instance_id": 701,
                    "module": "leave",
                    "business_type": "leave_request",
                    "business_id": leave_request.id,
                    "applicant_id": employee.id,
                    "approver_id": employee.id,
                    "summary": "年假 1 天",
                },
                headers_json={},
                status="pending",
            )

            handled = await LeaveApprovalBalanceConsumer().handle(db, event)
            sync_session.flush()

            assert handled is True
            sync_session.refresh(balance)
            sync_session.refresh(leave_request)
            assert leave_request.approval_status == ApprovalStatus.approved
            assert balance.used_days == Decimal("1.000")
            assert balance.remaining_days == Decimal("4.000")

            audit_log = sync_session.execute(
                select(AuditLog).where(
                    AuditLog.module == "leave",
                    AuditLog.action == "balance_adjust",
                )
            ).scalar_one()
            audit_payload = json.loads(audit_log.after_data)
            assert audit_log.operator_name == "系统"
            assert audit_payload["source_event_id"] == "evt-leave-balance-sqlite"
            assert audit_payload["approval_instance_id"] == 701
            assert audit_payload["leave_request_id"] == leave_request.id
            assert audit_payload["adjustment"] == -1.0
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_dynamic_leave_approval_balance_consumer_updates_annual_balance_and_survives_sync(tmp_path: Path):
    db_path = tmp_path / "dynamic-approval-leave-balance.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    try:
        with session_factory() as sync_session:
            db = AsyncLikeSession(sync_session)
            employee = Employee(
                employee_no="TG-DYNAMIC-LEAVE-001",
                name="动态请假员工",
                phone="13900000006",
                status=EmployeeStatus.ACTIVE.value,
                is_active=True,
                hire_date=date(2020, 1, 1),
                first_work_date=date(2020, 1, 1),
            )
            leave_type = LeaveType(
                name="年假",
                code="annual",
                is_paid=True,
                max_days_per_year=Decimal("5.0"),
                requires_proof=False,
                deduct_from="annual_leave",
                policy_region="all",
                is_active=True,
                quota_limited=True,
                quota_rule_type="fixed",
                quota_rule={"fixed_days": 5},
                issuance_rule="year_start",
                leave_unit="day",
                time_calc="workday",
                hours_per_day=Decimal("8.0"),
            )
            sync_session.add_all([employee, leave_type])
            sync_session.flush()
            balance = LeaveBalance(
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                year=2026,
                total_days=Decimal("5.0"),
                used_days=Decimal("0.0"),
                remaining_days=Decimal("5.0"),
                expired_days=Decimal("0.0"),
            )
            sync_session.add(balance)
            sync_session.flush()

            event = BusinessEventOutbox(
                event_id="evt-dynamic-leave-annual",
                event_type="approval.instance.approved.v1",
                aggregate_type="approval_instance",
                aggregate_id=801,
                source_module="approval",
                idempotency_key="approval_instance:801:approved",
                payload_json={
                    "approval_instance_id": 801,
                    "module": "leave",
                    "business_type": "leave",
                    "business_id": 801,
                    "applicant_id": employee.id,
                    "summary": "动态年假 1 天",
                    "form_data": {
                        "leave_type": "年假",
                        "start_time": "2026-05-22",
                        "end_time": "2026-05-22",
                        "duration": 1,
                    },
                },
                headers_json={},
                status="pending",
            )

            handled = await LeaveApprovalBalanceConsumer().handle(db, event)
            sync_session.flush()

            assert handled is True
            sync_session.refresh(balance)
            assert balance.used_days == Decimal("1.000")
            assert balance.remaining_days == Decimal("4.000")

            await LeaveBalanceService.sync_rule_based_balances(
                db,
                year=2026,
                employee_ids=[employee.id],
                leave_type_ids=[leave_type.id],
            )
            sync_session.flush()
            sync_session.refresh(balance)
            assert balance.used_days == Decimal("1.000")
            assert balance.remaining_days == Decimal("4.000")

            audit_payload = json.loads(
                sync_session.execute(
                    select(AuditLog.after_data).where(
                        AuditLog.module == "leave",
                        AuditLog.action == "balance_adjust",
                    )
                ).scalar_one()
            )
            assert audit_payload["usage_source"] == "dynamic_approval"
            assert audit_payload["source_event_id"] == "evt-dynamic-leave-annual"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_dynamic_leave_approval_balance_consumer_updates_existing_sick_balance(tmp_path: Path):
    db_path = tmp_path / "dynamic-approval-sick-balance.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    try:
        with session_factory() as sync_session:
            db = AsyncLikeSession(sync_session)
            employee = Employee(
                employee_no="TG-DYNAMIC-SICK-001",
                name="动态病假员工",
                phone="13900000007",
                status=EmployeeStatus.ACTIVE.value,
                is_active=True,
            )
            leave_type = LeaveType(
                name="病假",
                code="sick",
                is_paid=True,
                max_days_per_year=None,
                requires_proof=True,
                deduct_from="none",
                policy_region="all",
                is_active=True,
                quota_limited=False,
                leave_unit="day",
                time_calc="workday",
                hours_per_day=Decimal("8.0"),
            )
            sync_session.add_all([employee, leave_type])
            sync_session.flush()
            balance = LeaveBalance(
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                year=2026,
                total_days=Decimal("5.0"),
                used_days=Decimal("0.0"),
                remaining_days=Decimal("5.0"),
                expired_days=Decimal("0.0"),
            )
            sync_session.add(balance)
            sync_session.flush()

            event = BusinessEventOutbox(
                event_id="evt-dynamic-leave-sick",
                event_type="approval.instance.approved.v1",
                aggregate_type="approval_instance",
                aggregate_id=802,
                source_module="approval",
                idempotency_key="approval_instance:802:approved",
                payload_json={
                    "approval_instance_id": 802,
                    "module": "leave",
                    "business_type": "leave",
                    "business_id": 802,
                    "applicant_id": employee.id,
                    "summary": "动态病假 1 天",
                    "form_data": {
                        "leave_type": "病假",
                        "start_time": "2026-05-23",
                        "end_time": "2026-05-23",
                        "duration": 1,
                    },
                },
                headers_json={},
                status="pending",
            )

            handled = await LeaveApprovalBalanceConsumer().handle(db, event)
            sync_session.flush()

            assert handled is True
            sync_session.refresh(balance)
            assert balance.used_days == Decimal("1.000")
            assert balance.remaining_days == Decimal("4.000")
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_attendance_effects_without_clock_records_drive_daily_and_month_summary(tmp_path: Path):
    db_path = tmp_path / "approval-attendance-effect-only.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    leave_day = date(2026, 5, 6)
    outside_day = date(2026, 5, 7)
    overtime_day = date(2026, 5, 8)

    try:
        with session_factory() as sync_session:
            db = AsyncLikeSession(sync_session)
            employee = Employee(
                employee_no="TG-EFFECT-001",
                name="审批考勤员工",
                phone="13900000003",
                status=EmployeeStatus.ACTIVE.value,
                is_active=True,
            )
            sync_session.add(employee)
            sync_session.flush()

            current = date(2026, 5, 1)
            while current.month == 5:
                day_type = DayType.weekend
                if current in {leave_day, outside_day}:
                    day_type = DayType.workday
                elif current == overtime_day:
                    day_type = DayType.holiday
                sync_session.add(WorkCalendar(date=current, location_id=None, day_type=day_type))
                current += timedelta(days=1)
            sync_session.flush()

            sync_session.add_all(
                [
                    AttendanceEffect(
                        source_module="approval",
                        source_event_id="evt-leave-only",
                        source_instance_id=9001,
                        source_business_type="leave",
                        employee_id=employee.id,
                        effect_type="leave",
                        effect_status="active",
                        start_at=datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2026, 5, 6, 23, 59, tzinfo=timezone.utc),
                        work_date=leave_day,
                        minutes=480,
                        pay_policy="paid",
                        attendance_status="请假",
                        payload_json={"effect_detail": {"leave_type_code": "annual", "leave_type_name": "年假"}},
                    ),
                    AttendanceEffect(
                        source_module="approval",
                        source_event_id="evt-outside-only",
                        source_instance_id=9002,
                        source_business_type="attendance_outside",
                        employee_id=employee.id,
                        effect_type="outside",
                        effect_status="active",
                        start_at=datetime(2026, 5, 7, 0, 0, tzinfo=timezone.utc),
                        end_at=datetime(2026, 5, 7, 23, 59, tzinfo=timezone.utc),
                        work_date=outside_day,
                        minutes=480,
                        pay_policy="paid",
                        attendance_status="正常",
                        payload_json={"effect_detail": {"place_title": "客户现场"}},
                    ),
                    AttendanceEffect(
                        source_module="approval",
                        source_event_id="evt-overtime-only",
                        source_instance_id=9003,
                        source_business_type="overtime",
                        employee_id=employee.id,
                        effect_type="overtime",
                        effect_status="active",
                        start_at=datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc),
                        end_at=datetime(2026, 5, 8, 13, 0, tzinfo=timezone.utc),
                        work_date=overtime_day,
                        minutes=180,
                        pay_policy="overtime_pay",
                        payload_json={"effect_detail": {"settlement": "overtime_pay"}},
                    ),
                ]
            )
            sync_session.flush()

            await MonthSummaryService.generate(
                db,
                MonthSummaryGenerateRequest(year=2026, month=5, employee_ids=[employee.id]),
            )
            report = await AttendanceReportService.get_daily_report_data(
                db,
                start_date=leave_day,
                end_date=overtime_day,
                keyword="审批考勤员工",
                include_recent_left=False,
                page=1,
                page_size=20,
            )
            sync_session.commit()

            summary = sync_session.execute(
                select(AttendanceMonthSummary).where(
                    AttendanceMonthSummary.employee_id == employee.id,
                    AttendanceMonthSummary.year == 2026,
                    AttendanceMonthSummary.month == 5,
                )
            ).scalar_one()
            assert str(summary.leave_days) == "1.0"
            assert summary.absent_count == 0
            assert str(summary.overtime_holiday_hours) == "3.00"

            rows_by_date = {row["date_label"][:10].replace("/", "-"): row for row in report["overview_rows"]}
            assert rows_by_date["2026-05-06"]["attendance_result"] == "请假"
            assert rows_by_date["2026-05-07"]["attendance_result"] == "正常（外出）"
            assert rows_by_date["2026-05-08"]["overtime_hours"] == 3.0
            assert report["total"] == 3
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_monthly_report_dedupes_leave_request_and_matching_effect(tmp_path: Path):
    db_path = tmp_path / "approval-attendance-leave-report-dedupe.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    leave_day = date(2026, 5, 14)

    try:
        with session_factory() as sync_session:
            db = AsyncLikeSession(sync_session)
            employee = Employee(
                employee_no="TG-LEAVE-DEDUPE-001",
                name="请假去重员工",
                phone="13900000004",
                status=EmployeeStatus.ACTIVE.value,
                is_active=True,
            )
            leave_type = LeaveType(
                name="年假",
                code="annual",
                is_paid=True,
                is_active=True,
                quota_limited=False,
                leave_unit="day",
                hours_per_day=Decimal("8.0"),
            )
            sync_session.add_all([employee, leave_type])
            sync_session.flush()

            leave_request = LeaveRequest(
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                start_date=leave_day,
                end_date=leave_day,
                start_half=HalfDay.am,
                end_half=HalfDay.pm,
                days=Decimal("1.0"),
                reason="已批准请假",
                approval_status=ApprovalStatus.approved,
                approved_at=datetime(2026, 5, 5, 8, 0, tzinfo=timezone.utc),
            )
            sync_session.add(WorkCalendar(date=leave_day, location_id=None, day_type=DayType.workday))
            sync_session.add(leave_request)
            sync_session.flush()
            sync_session.add(
                AttendanceEffect(
                    source_module="approval",
                    source_event_id="evt-leave-dedupe",
                    source_instance_id=9901,
                    source_business_type="leave_request",
                    source_business_id=leave_request.id,
                    employee_id=employee.id,
                    effect_type="leave",
                    effect_status="active",
                    start_at=datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc),
                    end_at=datetime(2026, 5, 14, 23, 59, tzinfo=timezone.utc),
                    work_date=leave_day,
                    minutes=480,
                    pay_policy="paid",
                    attendance_status="请假",
                    payload_json={
                        "effect_detail": {
                            "leave_request_id": leave_request.id,
                            "leave_type_code": "annual",
                            "leave_type_name": "年假",
                        }
                    },
                )
            )
            sync_session.flush()

            report = await AttendanceReportService.get_monthly_report_data(
                db,
                start_date=leave_day,
                end_date=leave_day,
                keyword="请假去重员工",
                include_recent_left=False,
                page=1,
                page_size=20,
            )
            sync_session.commit()

            overview = report["overview_rows"][0]
            detail = report["detail_rows"][0]
            day_key = report["detail_day_columns"][0]["key"]
            assert overview["annual_leave_days"] == "1天"
            assert detail[day_key].count("年假1天") == 1
    finally:
        engine.dispose()


def test_monthly_effect_period_keeps_local_approval_effect_time():
    effect = AttendanceEffect(
        effect_type="overtime",
        work_date=date(2026, 5, 15),
        start_at=datetime(2026, 5, 15, 18, 30),
        end_at=datetime(2026, 5, 15, 20, 30),
        minutes=120,
    )

    assert AttendanceReportService._format_effect_period(effect) == "5/15 18:30 - 5/15 20:30"
