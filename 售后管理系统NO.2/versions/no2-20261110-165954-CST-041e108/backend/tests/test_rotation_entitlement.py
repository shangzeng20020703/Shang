"""Isolated acceptance for cross-project six-workday rest accounting."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register isolated tables
from app.core.database import Base
from app.core.database import get_db
from app.core.deps import get_current_user
from app.main import app
from app.models.attendance import AttendanceEffect, AttendanceRecord, AttendanceRule, DayType, WorkHourType
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import (FieldAssignment, FieldCustomer, FieldMembership,
                                      FieldProject, FieldRegionalAssignment, FieldSite)
from app.models.leave import LeaveBalance, LeaveType
from app.models.organization import Location
from app.models.overtime import OvertimeRequest
from app.models.rotation import RotationAccount, RotationClaim, RotationDay
from app.models.rotation_entitlement import RotationAutoRest, RotationEntitlementLedger, RotationWorkFact
from app.schemas.rotation import ClaimInput, ClaimReview
from app.services import rotation, rotation_entitlement as entitlement
from app.services.attendance import AttendanceRecordService
from app.services.approval import _ensure_rotation_overtime_credit_conflict
from app.api.v1.overtime import OvertimeApproveRequest, approve_overtime


START = date(2026, 11, 9)


def punch(day, hours=12):
    started = datetime.combine(day, time(8), rotation.CST).astimezone(timezone.utc)
    return AttendanceRecord(employee_id=2, date=day, clock_in_time=started,
                            clock_out_time=started + timedelta(hours=hours))


async def isolated_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add_all([
            Employee(id=1, employee_no="ADMIN", name="管理员", phone="19900002001",
                     is_superuser=True, status="在职"),
            Employee(id=2, employee_no="WORKER", name="组员", phone="19900002002",
                     is_field_staff=True, status="在职"),
            Employee(id=3, employee_no="MGR", name="售后经理", phone="19900002003", status="在职"),
            Employee(id=4, employee_no="LEAD", name="项目组长", phone="19900002004",
                     is_field_staff=True, status="在职"),
            Employee(id=5, employee_no="REG", name="区域组长", phone="19900002005", status="在职"),
            FieldCustomer(id=1, name="虚构客户"),
            Location(id=1, name="虚构打卡点"),
            FieldProject(id=1, code="P1", name="虚构项目一", customer_id=1, manager_id=4),
            FieldProject(id=2, code="P2", name="虚构项目二", customer_id=1),
        ])
        await db.flush()
        db.add(EmployeeRole(employee_id=3, role_name="aftersales_manager", granted_by=1))
        db.add(EmployeeRole(employee_id=5, role_name="regional_lead", granted_by=1))
        db.add(FieldRegionalAssignment(project_id=1, employee_id=5, kind="primary",
            active_employee_id=5, active_primary_project_id=1, appointed_by=1))
        rule = AttendanceRule(name="十二小时现场轮休", work_hour_type=WorkHourType.flexible,
                              work_hours_per_day=12, effective_date=START,
                              extra_config={"rule_type": "rotation", "scope_mode": "project",
                                            "free_work_hours_mode": "limit:12"})
        db.add(rule)
        await db.flush()
        db.add(FieldSite(id=1, project_id=1, name="项目一现场", location_id=1, rule_id=rule.id))
        await db.flush()
        db.add(FieldAssignment(id=1, site_id=1, employee_id=2, start_date=START,
            end_date=START + timedelta(days=8), created_by=1))
        await db.flush()
        db.add(FieldMembership(assignment_id=1, employee_id=2,
            started_at=datetime.combine(START, time.min, rotation.CST).astimezone(timezone.utc),
            ended_at=datetime.combine(START + timedelta(days=9), time.min, rotation.CST).astimezone(timezone.utc)))
        await db.commit()
        manager = await db.get(Employee, 3)
        worker = await db.get(Employee, 2)
        admin = await db.get(Employee, 1)
        yield db, rule, admin, manager, worker
        await db.commit()
    await engine.dispose()


async def add_day(db, rule, offset, *, kind="work", rest_type=None, project_id=None, hours=None):
    day = START + timedelta(days=offset)
    snapshot = rotation.rule_with_shift(rule, None)
    if rest_type:
        snapshot["_rotation_rest_type"] = rest_type
    db.add(RotationDay(employee_id=2, project_id=project_id or (1 if offset < 9 else 2),
        work_date=day, cycle_start=START,
        original_kind="rest" if kind == "rest" else "work", kind=kind,
        rule_snapshot=snapshot, revision=1, reason="周期排班", updated_by=1))
    if hours is not None:
        db.add(punch(day, hours))
    await db.flush()


@pytest.mark.asyncio
async def test_eight_days_rest_then_four_carries_progress_across_projects(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        assert (await entitlement.policy_view(db, admin))["enabled"] is False
        with pytest.raises(HTTPException) as denied:
            await entitlement.set_policy(db, worker, START)
        assert denied.value.status_code == 403
        await entitlement.set_policy(db, admin, START)
        for offset in range(8):
            await add_day(db, rule, offset, hours=12)
        await add_day(db, rule, 8, kind="rest", rest_type="concentrated")
        for offset in range(9, 13):
            await add_day(db, rule, offset, hours=12)
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=8))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=7))
        first = await entitlement.my_entitlement(db, worker)
        assert first["available_days"] == 1 and first["progress_days"] == 2
        assert first["active"] and first["policy_enabled"]
        application = await rotation.create_claim(db, worker, ClaimInput(project_id=1, kind="leave",
            days=[{"date": START + timedelta(days=8), "kind": "leave"}], reason="连续上八天后集中休息"))
        assert application["status"] == "pending"
        approved = await rotation.review(db, manager, application["id"],
            ClaimReview(action="approve", note="批准集中休息"))
        assert approved["status"] == "approved"
        after_rest = await entitlement.my_entitlement(db, worker)
        assert after_rest["available_days"] == 0 and after_rest["progress_days"] == 2
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=14))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=12))
        second = await entitlement.my_entitlement(db, worker)
        assert second["available_days"] == 1 and second["progress_days"] == 0
        assert any(item["action"] == "work_credit" and item["progress_days"] == 2
                   for item in second["ledger"])
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=12))
        assert (await db.scalar(select(RotationAccount.units).where(RotationAccount.employee_id == 2))) == 2
        with pytest.raises(HTTPException) as immutable:
            await entitlement.set_policy(db, admin, START + timedelta(days=15))
        assert immutable.value.status_code == 409
        assert (await db.scalar(select(RotationEntitlementLedger.id).where(
            RotationEntitlementLedger.action == "work_credit").order_by(RotationEntitlementLedger.id.desc()).limit(1)))


@pytest.mark.asyncio
async def test_six_one_auto_debits_and_concentrated_patterns_do_not(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(6):
            await add_day(db, rule, offset, hours=12)
        await add_day(db, rule, 6, kind="rest", rest_type="ordinary")
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=8))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=6))
        assert (await db.get(RotationAccount, worker.id)).units == 0
        auto = await db.scalar(select(RotationAutoRest).where(RotationAutoRest.employee_id == worker.id))
        assert auto.status == "applied" and auto.units == 2
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=6))
        assert len((await db.scalars(select(RotationAutoRest))).all()) == 1

    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(12):
            await add_day(db, rule, offset, hours=12)
        for offset in (12, 13):
            await add_day(db, rule, offset, kind="rest", rest_type="concentrated")
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=15))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=13))
        assert (await db.get(RotationAccount, worker.id)).units == 4
        assert not (await db.scalars(select(RotationAutoRest))).all()

    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(18):
            await add_day(db, rule, offset, hours=12)
        for offset in (18, 19, 20):
            await add_day(db, rule, offset, kind="rest", rest_type="concentrated")
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=22))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=20))
        assert (await db.get(RotationAccount, worker.id)).units == 6
        assert (await entitlement.my_entitlement(db, worker))["progress_days"] == 0
        assert not (await db.scalars(select(RotationAutoRest))).all()


@pytest.mark.asyncio
async def test_half_day_and_travel_require_manager_review_and_no_duplicate(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        await add_day(db, rule, 0, hours=6)
        await add_day(db, rule, 1)
        await add_day(db, rule, 2)
        db.add(AttendanceEffect(source_event_id="TRIP-1", employee_id=2,
            effect_type="business_trip", effect_status="active", work_date=START + timedelta(days=1)))
        db.add(AttendanceEffect(source_event_id="TRIP-2", employee_id=2,
            effect_type="business_trip", effect_status="active", work_date=START + timedelta(days=2)))
        await db.flush()
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=3))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=1))
        pending = await entitlement.pending_work_days(db, manager)
        assert len(pending) == 3 and {p["suggested_days"] for p in pending} == {0.5, 1}
        with pytest.raises(HTTPException) as forbidden:
            await entitlement.review_work_day(db, worker, worker.id, START, "approve", 0.5, "本人确认")
        assert forbidden.value.status_code == 403
        await entitlement.review_work_day(db, manager, worker.id, START, "approve", 0.5, "核实六小时")
        await entitlement.review_work_day(db, manager, worker.id, START + timedelta(days=1),
            "approve", 1, "核实出差实际工作")
        await entitlement.review_work_day(db, manager, worker.id, START + timedelta(days=2),
            "approve", 1, "核实次日出差实际工作")
        view = await entitlement.my_entitlement(db, worker)
        assert view["progress_days"] == 2.5 and view["available_days"] == 0
        with pytest.raises(HTTPException) as duplicate:
            await entitlement.review_work_day(db, manager, worker.id, START, "approve", 0.5, "重复审批")
        assert duplicate.value.status_code == 409
        assert len((await db.scalars(select(RotationWorkFact))).all()) == 3


@pytest.mark.asyncio
async def test_overtime_conversion_cannot_duplicate_rolling_source(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        await add_day(db, rule, 0, hours=12)
        overtime = AttendanceEffect(source_event_id="OT-1", employee_id=2,
            effect_type="overtime", effect_status="active", pay_policy="comp_time", work_date=START)
        db.add(overtime)
        await db.flush()
        await entitlement.sync_employee_through(db, worker.id, START)
        assert await db.scalar(select(RotationWorkFact.id)) is None
        overtime.effect_status = "revoked"
        await db.flush()
        await entitlement.sync_employee_through(db, worker.id, START)
        assert (await db.scalar(select(RotationWorkFact.units))) == 2
        with pytest.raises(HTTPException) as duplicate:
            await rotation.ensure_overtime_not_credited(db, worker.id, START, "comp_time")
        assert duplicate.value.status_code == 409


@pytest.mark.asyncio
async def test_attendance_overtime_balance_excludes_new_rotation_days_but_keeps_prior_credit(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START + timedelta(days=1))
        extra = {**rule.extra_config, "overtime_policy": {
            "workday": {"enabled": True, "convert_mode": "comp_time",
                        "sync_auto_leave_type": True}}}
        rule.extra_config = extra
        db.add(LeaveType(name="调休", code="comp_time", leave_unit="hour", hours_per_day=8))
        await db.flush()
        for offset in (0, 1):
            await add_day(db, rule, offset, hours=14)
            record = await db.scalar(select(AttendanceRecord).where(
                AttendanceRecord.employee_id == worker.id,
                AttendanceRecord.date == START + timedelta(days=offset)))
            record.overtime_hours = Decimal("2")
            await AttendanceRecordService._sync_comp_time_balance_delta(
                db, worker.id, record.date, extra, DayType.workday,
                Decimal("0"), Decimal("2"))
        balance = await db.scalar(select(LeaveBalance).where(LeaveBalance.employee_id == worker.id))
        assert balance.total_days == Decimal("0.250")  # Only the pre-effective-date two hours.
        await AttendanceRecordService.sync_comp_time_balances_from_attendance(
            db, START.year, [worker.id], allow_decrease=True)
        await db.refresh(balance)
        assert balance.total_days == Decimal("0.250")
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=2))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=1))
        fact = await db.scalar(select(RotationWorkFact).where(
            RotationWorkFact.employee_id == worker.id,
            RotationWorkFact.work_date == START + timedelta(days=1)))
        assert fact and fact.units == 2 and fact.status == "accepted"
        assert balance.total_days == Decimal("0.250")


@pytest.mark.asyncio
async def test_policy_activation_preserves_already_issued_same_day_comp_time(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        extra = {**rule.extra_config, "overtime_policy": {
            "workday": {"enabled": True, "convert_mode": "comp_time",
                        "sync_auto_leave_type": True}}}
        rule.extra_config = extra
        db.add(LeaveType(name="调休", code="comp_time", leave_unit="hour", hours_per_day=8))
        await add_day(db, rule, 0, hours=14)
        record = await db.scalar(select(AttendanceRecord).where(
            AttendanceRecord.employee_id == worker.id, AttendanceRecord.date == START))
        record.overtime_hours = Decimal("2")
        await AttendanceRecordService._sync_comp_time_balance_delta(
            db, worker.id, START, extra, DayType.workday, Decimal("0"), Decimal("2"))
        balance = await db.scalar(select(LeaveBalance).where(LeaveBalance.employee_id == worker.id))
        assert balance and balance.total_days == Decimal("0.250")
        with pytest.raises(HTTPException) as overlap:
            await entitlement.set_policy(db, admin, START)
        assert overlap.value.status_code == 409
        assert (await entitlement.policy_view(db, admin))["enabled"] is False
        await entitlement.set_policy(db, admin, START + timedelta(days=1))
        await AttendanceRecordService.sync_comp_time_balances_from_attendance(
            db, START.year, [worker.id], allow_decrease=True)
        await db.refresh(balance)
        assert balance.total_days == Decimal("0.250")


@pytest.mark.asyncio
async def test_approved_overtime_request_and_rolling_work_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    extra_policy = {"workday": {"enabled": True, "convert_mode": "comp_time",
                                "sync_auto_leave_type": True}}
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        rule.extra_config = {**rule.extra_config, "overtime_policy": extra_policy}
        db.add(LeaveType(name="调休", code="comp_time", leave_unit="hour", hours_per_day=8))
        await add_day(db, rule, 0, hours=12)
        db.add(OvertimeRequest(employee_id=worker.id, overtime_date=START,
            start_time="18:00", end_time="20:00", hours=Decimal("2"),
            overtime_type="weekday", status="approved"))
        await db.flush()
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=1))
        await entitlement.sync_employee_through(db, worker.id, START)
        assert await db.scalar(select(RotationWorkFact.id)) is None
        await AttendanceRecordService.sync_comp_time_balances_from_attendance(db, START.year, [worker.id])
        balance = await db.scalar(select(LeaveBalance).where(LeaveBalance.employee_id == worker.id))
        assert balance and balance.total_days == Decimal("0.250")

    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        rule.extra_config = {**rule.extra_config, "overtime_policy": extra_policy}
        await add_day(db, rule, 0, hours=12)
        request = OvertimeRequest(employee_id=worker.id, overtime_date=START,
            start_time="18:00", end_time="20:00", hours=Decimal("2"),
            overtime_type="weekday", status="pending")
        db.add(request)
        await db.flush()
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=1))
        await entitlement.sync_employee_through(db, worker.id, START)
        assert (await db.scalar(select(RotationWorkFact.units))) == 2
        with pytest.raises(HTTPException) as duplicate:
            await approve_overtime(request.id, OvertimeApproveRequest(action="approve"), db, admin)
        assert duplicate.value.status_code == 409
        assert request.status == "pending"


@pytest.mark.asyncio
async def test_employee_api_is_self_only_and_manager_queue_is_role_guarded(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(6):
            await add_day(db, rule, offset, hours=12)
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=7))
        actor = {"id": worker.id}

        async def override_db():
            yield db

        async def override_user():
            return await db.get(Employee, actor["id"])

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                own = await client.get("/api/v1/rotation/me/entitlement")
                assert own.status_code == 200 and own.json()["available_days"] == 1
                denied = await client.get("/api/v1/rotation/entitlement/pending")
                assert denied.status_code == 403
                actor["id"] = manager.id
                other = await client.get("/api/v1/rotation/me/entitlement?employee_id=2")
                assert other.status_code == 200 and other.json()["available_days"] == 0
                assert other.json()["active"] is False
                actor["id"] = worker.id
                policy_denied = await client.get("/api/v1/rotation/entitlement/policy")
                assert policy_denied.status_code == 403
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_leave_review_rejects_full_punch_and_earned_work_on_same_day(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(6):
            await add_day(db, rule, offset, hours=12)
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=7))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=5))
        assert (await db.get(RotationAccount, worker.id)).units == 2
        full = await rotation.create_claim(db, worker, ClaimInput(project_id=1, kind="leave",
            days=[{"date": START, "kind": "leave"}], reason="申请当天休息"))
        with pytest.raises(HTTPException) as conflict:
            await rotation.review(db, manager, full["id"], ClaimReview(action="approve", note="同意"))
        assert conflict.value.status_code == 409
        await rotation.review(db, manager, full["id"], ClaimReview(action="reject", note="已有出勤"))
        half = await rotation.create_claim(db, worker, ClaimInput(project_id=1, kind="leave",
            days=[{"date": START, "kind": "half_leave"}], reason="改为半天休息"))
        with pytest.raises(HTTPException) as half_conflict:
            await rotation.review(db, manager, half["id"], ClaimReview(action="approve", note="同意"))
        assert half_conflict.value.status_code == 409
        assert (await db.get(RotationAccount, worker.id)).units == 2


@pytest.mark.asyncio
async def test_insufficient_ordinary_rest_reconciles_later_work_evidence(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        await add_day(db, rule, 0, kind="rest", rest_type="ordinary")
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=1))
        await entitlement.sync_employee_through(db, worker.id, START)
        auto = await db.scalar(select(RotationAutoRest).where(RotationAutoRest.employee_id == worker.id))
        assert auto.status == "needs_action" and auto.units == 0
        db.add(punch(START, 12))
        await db.flush()
        await entitlement.sync_employee_through(db, worker.id, START)
        assert auto.status == "worked" and auto.units == 0
        record = await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id == 2,
            AttendanceRecord.date == START))
        record.clock_out_time = None
        await db.flush()
        await entitlement.sync_employee_through(db, worker.id, START)
        await entitlement.review_work_day(db, manager, worker.id, START, "reject", 0, "补卡未完成")
        await entitlement.sync_employee_through(db, worker.id, START)
        assert auto.status == "needs_action" and auto.units == 0


@pytest.mark.asyncio
async def test_today_ordinary_rest_reserves_credit_before_future_leave_approval(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(6):
            await add_day(db, rule, offset, hours=12)
        await add_day(db, rule, 6, kind="rest", rest_type="ordinary")
        await add_day(db, rule, 7, kind="work")
        await db.commit()
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=6))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=5))
        assert (await db.get(RotationAccount, worker.id)).units == 2
        await db.commit()
        application = await rotation.create_claim(db, worker, ClaimInput(project_id=1, kind="leave",
            days=[{"date": START + timedelta(days=7), "kind": "leave"}], reason="申请明天调休"))
        await db.commit()
        with pytest.raises(HTTPException) as denied:
            await rotation.review(db, manager, application["id"],
                ClaimReview(action="approve", note="核对额度"))
        assert denied.value.status_code == 409
        await db.rollback()  # API transaction rolls back failed approval.
        worker = await db.get(Employee, 2)
        rule = await db.get(AttendanceRule, 1)
        view = await entitlement.my_entitlement(db, worker)
        assert view["available_days"] == 0
        auto = await db.scalar(select(RotationAutoRest).where(RotationAutoRest.employee_id == worker.id))
        assert auto.status == "applied" and auto.work_date == START + timedelta(days=6)
        runtime = await rotation.apply_runtime(db, {"rule": rule}, worker.id,
            START + timedelta(days=6), 1)
        assert runtime["rotation"]["kind"] == "rest" and runtime["no_punch"] is True


@pytest.mark.asyncio
async def test_approved_rest_to_work_waits_for_punch_then_refunds_once(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(6):
            await add_day(db, rule, offset, hours=12)
        await add_day(db, rule, 6, kind="rest", rest_type="ordinary")
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=6))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=6))
        assert (await db.get(RotationAccount, worker.id)).units == 0
        adjustment = await rotation.create_claim(db, worker, ClaimInput(project_id=1,
            kind="schedule", days=[{"date": START + timedelta(days=6), "kind": "work"}],
            reason="现场继续工作"))
        lead = await db.get(Employee, 4)
        regional = await db.get(Employee, 5)
        first = await rotation.review(db, lead, adjustment["id"],
            ClaimReview(action="approve", expected_step=0, note="项目组长核对"))
        assert first["status"] == "pending"
        second = await rotation.review(db, regional, adjustment["id"],
            ClaimReview(action="approve", expected_step=1, note="区域组长核对"))
        assert second["status"] == "pending"
        final = await rotation.review(db, manager, adjustment["id"],
            ClaimReview(action="approve", expected_step=2, note="售后经理终审"))
        assert final["status"] == "approved"
        auto = await db.scalar(select(RotationAutoRest).where(RotationAutoRest.employee_id == worker.id))
        assert auto.status == "needs_review" and auto.units == 2
        assert (await db.get(RotationAccount, worker.id)).units == 0
        db.add(punch(START + timedelta(days=6), 12))
        await db.flush()
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=6))
        assert auto.status == "worked" and auto.units == 0
        assert (await db.get(RotationAccount, worker.id)).units == 2
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=6))
        reversal = (await db.scalars(select(RotationEntitlementLedger).where(
            RotationEntitlementLedger.action == "ordinary_reversal"))).all()
        assert len(reversal) == 1 and reversal[0].delta_units == 2


@pytest.mark.asyncio
async def test_punched_pending_day_cannot_be_revoked_to_no_schedule(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        claim = await rotation.create_claim(db, worker, ClaimInput(project_id=1,
            kind="schedule", days=[{"date": START, "kind": "work"}], reason="现场事后确认"))
        await rotation.review(db, await db.get(Employee, 4), claim["id"],
            ClaimReview(action="approve", expected_step=0, note="项目组长核对"))
        await rotation.review(db, await db.get(Employee, 5), claim["id"],
            ClaimReview(action="approve", expected_step=1, note="区域组长核对"))
        approved = await rotation.review(db, manager, claim["id"],
            ClaimReview(action="approve", expected_step=2, note="售后经理终审"))
        assert approved["status"] == "approved"
        db.add(punch(START, 12))
        await db.flush()
        await entitlement.sync_employee_through(db, worker.id, START)
        assert (await db.scalar(select(RotationWorkFact.units))) == 2
        with pytest.raises(HTTPException) as protected:
            await rotation.review(db, manager, claim["id"],
                ClaimReview(action="revoke", note="尝试删除已打卡排班"))
        assert protected.value.status_code == 409
        assert await rotation.day_row(db, worker.id, START, 1)


@pytest.mark.asyncio
async def test_overtime_rest_conversion_is_blocked_before_approval_terminal_state(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        await add_day(db, rule, 0, hours=12)
        await entitlement.sync_employee_through(db, worker.id, START)
        claim = SimpleNamespace(module="overtime", business_id=None, applicant_id=worker.id,
            form_data={"pay_policy": "comp_time", "overtime_date": START.isoformat()})
        with pytest.raises(HTTPException) as duplicated:
            await _ensure_rotation_overtime_credit_conflict(db, claim)
        assert duplicated.value.status_code == 409
        claim.form_data["pay_policy"] = "overtime_pay"
        await _ensure_rotation_overtime_credit_conflict(db, claim)


@pytest.mark.asyncio
async def test_correction_requires_review_and_preserves_spent_credit(monkeypatch):
    monkeypatch.setattr(rotation, "today", lambda: START)
    async for db, rule, admin, manager, worker in isolated_db():
        await entitlement.set_policy(db, admin, START)
        for offset in range(6):
            await add_day(db, rule, offset, hours=12)
        monkeypatch.setattr(rotation, "today", lambda: START + timedelta(days=7))
        await entitlement.sync_employee_through(db, worker.id, START + timedelta(days=5))
        assert (await db.get(RotationAccount, worker.id)).units == 2
        claim = RotationClaim(employee_id=worker.id, project_id=1, kind="leave",
            days=[{"date": str(START + timedelta(days=6)), "kind": "leave"}],
            status="approved", units=2, reason="使用一天额度", submitted_by=worker.id,
            reviewed_by=manager.id)
        db.add(claim)
        await db.flush()
        await rotation.post_ledger(db, manager, claim, "leave", -2, "已批准使用")
        assert (await db.get(RotationAccount, worker.id)).units == 0
        first = await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id == 2,
            AttendanceRecord.date == START))
        first.clock_out_time = None
        await db.flush()
        await entitlement.sync_employee_through(db, worker.id, START)
        fact = await db.scalar(select(RotationWorkFact).where(RotationWorkFact.employee_id == 2,
            RotationWorkFact.work_date == START))
        assert fact.status == "needs_review" and fact.units == 2
        with pytest.raises(HTTPException) as spent:
            await entitlement.review_work_day(db, manager, worker.id, START, "reject", 0, "证据失效")
        assert spent.value.status_code == 409
        assert (await db.get(RotationAccount, worker.id)).units == 0
        assert fact.units == 2
        await rotation.post_ledger(db, manager, claim, "reversal", 2, "先退回已使用额度")
        await entitlement.review_work_day(db, manager, worker.id, START, "reject", 0, "补卡仍未完成")
        assert (await db.get(RotationAccount, worker.id)).units == 0
        assert (await entitlement.my_entitlement(db, worker))["progress_days"] == 5
