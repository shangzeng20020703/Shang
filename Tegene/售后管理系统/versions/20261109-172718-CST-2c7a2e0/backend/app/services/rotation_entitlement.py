"""Rolling onsite rest entitlement, independent of a particular project/week.

One accepted full workday is two units and one half workday is one unit. Twelve
work units earn two rest units (one day). Facts are keyed by employee/date so
repeated punches, approval events and retries cannot double count a day.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, or_, select

from app.models.attendance import AttendanceEffect, AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.rotation import RotationAccount, RotationClaim, RotationDay, RotationDraftDay, RotationLedger
from app.models.rotation_entitlement import (RotationAutoRest, RotationEntitlementAccount,
    RotationEntitlementLedger, RotationEntitlementPolicy, RotationWorkFact)
from app.services import field_roles
from app.services.field_membership import lock_person, snapshot


WORK_UNITS_PER_REST_DAY = 12
REST_UNITS_PER_DAY = 2
REST_POLICIES = {"comp_time", "rest", "rest_time", "time_off", "lieu", "adjust_rest"}
_last_due_rest_scan = 0.0


def today():
    from app.services.rotation import today as rotation_today
    return rotation_today()


async def policy(db):
    return await db.get(RotationEntitlementPolicy, 1)


async def policy_view(db, user):
    if "admin" not in await field_roles.role_names(db, user):
        raise HTTPException(403, "仅系统管理员可查看休息额度生效设置")
    row = await policy(db)
    return {"enabled": bool(row), "effective_date": row.effective_date if row else None}


async def set_policy(db, user, effective_date: date):
    if "admin" not in await field_roles.role_names(db, user):
        raise HTTPException(403, "仅系统管理员可设置休息额度生效日期")
    if effective_date < today():
        raise HTTPException(422, "生效日期不能早于今天；历史记录不自动重算")
    row = await policy(db)
    if row is None or row.effective_date != effective_date:
        already_punched = await db.scalar(select(AttendanceRecord.id).join(RotationDay,
            (RotationDay.employee_id == AttendanceRecord.employee_id) &
            (RotationDay.work_date == AttendanceRecord.date)).where(
            AttendanceRecord.date >= effective_date,
            or_(AttendanceRecord.clock_in_time.is_not(None),
                AttendanceRecord.clock_out_time.is_not(None),
                AttendanceRecord.overtime_hours > 0)).limit(1))
        if already_punched:
            raise HTTPException(409, "生效日起已有现场轮休打卡；请选择晚于现有打卡的日期，保留原有调休余额")
    before = snapshot(row) if row else None
    if row and row.effective_date != effective_date and (
        await db.scalar(select(RotationWorkFact.id).limit(1)) or
        await db.scalar(select(RotationAutoRest.id).limit(1)) or
        await db.scalar(select(RotationEntitlementLedger.id).limit(1))):
        raise HTTPException(409, "已有滚动额度记录，不能修改生效日期")
    if row is None:
        row = RotationEntitlementPolicy(id=1, effective_date=effective_date, updated_by=user.id)
        db.add(row)
    else:
        row.effective_date, row.updated_by = effective_date, user.id
    db.add(AuditLog(operator_id=user.id, operator_name=user.name, module="attendance",
        action="rotation_entitlement_policy", resource_type="rotation",
        before_data=json.dumps(jsonable_encoder(before), ensure_ascii=False),
        after_data=json.dumps({"effective_date": effective_date.isoformat()}, ensure_ascii=False)))
    await db.flush()
    return {"enabled": True, "effective_date": effective_date}


async def _rolling_account(db, eid):
    row = await db.get(RotationEntitlementAccount, eid)
    if row is None:
        row = RotationEntitlementAccount(employee_id=eid, work_units=0, awarded_units=0)
        db.add(row)
        await db.flush()
    return row


async def _rest_account(db, eid):
    row = await db.get(RotationAccount, eid)
    if row is None:
        row = RotationAccount(employee_id=eid, units=0)
        db.add(row)
        await db.flush()
    return row


def _record_ledger(db, *, source_key, eid, work_date, action, delta, rest_account, rolling_account, actor_id, reason):
    db.add(RotationEntitlementLedger(source_key=source_key, employee_id=eid, work_date=work_date, action=action,
        delta_units=delta, balance_units=rest_account.units, work_units=rolling_account.work_units,
        actor_id=actor_id, reason=reason))


async def _accept_work(db, fact, units, actor_id, reason):
    """Apply a correction atomically; do not silently remove already spent rest."""
    if units not in (0, 1, 2):
        raise HTTPException(422, "有效工作日只能按 0、0.5 或 1 天确认")
    rolling = await _rolling_account(db, fact.employee_id)
    rest = await _rest_account(db, fact.employee_id)
    old_units = fact.units
    total = rolling.work_units + units - old_units
    expected_awards = (total // WORK_UNITS_PER_REST_DAY) * REST_UNITS_PER_DAY
    award_delta = expected_awards - rolling.awarded_units
    if rest.units + award_delta < 0:
        raise HTTPException(409, "额度已使用，不能直接撤销来源；请先核对并处理已使用记录")
    fact.units = units
    fact.revision += 1
    fact.status = "accepted" if units else "rejected"
    fact.confirmed_by = actor_id
    fact.reason = reason
    rolling.work_units = total
    rolling.awarded_units = expected_awards
    rest.units += award_delta
    if units != old_units:
        _record_ledger(db, source_key=f"work:{fact.id}:{fact.revision}",
            eid=fact.employee_id, work_date=fact.work_date,
            action="work_credit" if units > old_units else "work_correction", delta=award_delta,
            rest_account=rest, rolling_account=rolling, actor_id=actor_id,
            reason=f"有效出勤 {units / 2:g} 天；{reason}")
    await db.flush()


async def _facts(db, row):
    from app.services.rotation import credit_facts
    punch = await credit_facts(db, row)
    trip = await db.scalar(select(AttendanceEffect.id).where(AttendanceEffect.employee_id == row.employee_id,
        AttendanceEffect.work_date == row.work_date, AttendanceEffect.effect_type == "business_trip",
        AttendanceEffect.effect_status == "active").limit(1))
    overtime = await db.scalar(select(AttendanceEffect.id).where(AttendanceEffect.employee_id == row.employee_id,
        AttendanceEffect.work_date == row.work_date, AttendanceEffect.effect_type == "overtime",
        AttendanceEffect.effect_status == "active", AttendanceEffect.pay_policy.in_(REST_POLICIES)).limit(1))
    if not overtime:
        from app.models.overtime import OvertimeRequest
        from app.services.attendance import AttendanceRecordService
        requests = (await db.scalars(select(OvertimeRequest).where(
            OvertimeRequest.employee_id == row.employee_id,
            OvertimeRequest.overtime_date == row.work_date,
            OvertimeRequest.status == "approved"))).all()
        for request in requests:
            if await AttendanceRecordService.overtime_request_converts_to_comp_time(
                db, request, (row.rule_snapshot or {}).get("extra_config") or {}):
                overtime = f"request:{request.id}"
                break
    full_clean = punch["complete"] and punch["suggested_days"] == 1 and not punch["issues"]
    suggested = 2 if full_clean or trip else int(punch["suggested_days"] * 2)
    source = "punch" if punch["complete"] and punch["suggested_days"] else "travel" if trip else "punch"
    if overtime:
        suggested = 0
    fingerprint = hashlib.sha256(json.dumps({"punch": punch["fingerprint"], "trip": trip,
        "overtime": overtime, "row_revision": row.revision}, sort_keys=True).encode()).hexdigest()
    return {"suggested_units": suggested, "auto": bool(full_clean and not overtime),
        "source": source, "fingerprint": fingerprint, "trip": bool(trip),
        "overtime": bool(overtime), "punch": punch}


async def _settle_work_day(db, row):
    if row.kind == "leave":
        existing = await db.scalar(select(RotationWorkFact).where(RotationWorkFact.employee_id == row.employee_id,
            RotationWorkFact.work_date == row.work_date))
        if existing and existing.units:
            existing.status = "needs_review"
            existing.reason = "排班已改为全天调休，原出勤额度须复核"
        return
    facts = await _facts(db, row)
    fact = await db.scalar(select(RotationWorkFact).where(RotationWorkFact.employee_id == row.employee_id,
        RotationWorkFact.work_date == row.work_date))
    if fact is None and not facts["suggested_units"]:
        return
    if fact is None:
        fact = RotationWorkFact(employee_id=row.employee_id, project_id=row.project_id,
            work_date=row.work_date, units=0, suggested_units=facts["suggested_units"],
            status="pending", source=facts["source"], fingerprint=facts["fingerprint"])
        db.add(fact)
        await db.flush()
    elif fact.fingerprint == facts["fingerprint"] and fact.project_id == row.project_id:
        return
    fact.project_id = row.project_id
    fact.suggested_units = facts["suggested_units"]
    fact.source = facts["source"]
    fact.fingerprint = facts["fingerprint"]
    if fact.units and (not facts["auto"] or fact.units > facts["suggested_units"]):
        fact.status = "needs_review"
        fact.reason = "打卡、出差或项目规则已变化，请售后经理复核已入账工时"
    elif facts["auto"]:
        await _accept_work(db, fact, 2, None, "完整打卡达到项目规定工时，自动入账")
    elif facts["suggested_units"]:
        fact.status = "pending"
        fact.reason = "半天工时、异常打卡或批准的出差，需要售后经理确认"
    elif fact.units:
        fact.status = "needs_review"
        fact.reason = "原有效出勤证据已失效，请售后经理复核"
    else:
        fact.status = "rejected"
    await db.flush()


def ordinary_rest(row):
    marker = (row.rule_snapshot or {}).get("_rotation_rest_type")
    if marker:
        return marker == "ordinary"
    return row.original_kind == "rest" and (row.work_date - row.cycle_start).days % 7 == 6


async def _settle_ordinary_rest(db, row):
    if not ordinary_rest(row) or row.work_date > today():
        return
    current = await db.scalar(select(RotationAutoRest).where(RotationAutoRest.employee_id == row.employee_id,
        RotationAutoRest.work_date == row.work_date))
    if row.kind != "rest" and current is None:
        return
    work_fact = await db.scalar(select(RotationWorkFact).where(RotationWorkFact.employee_id == row.employee_id,
        RotationWorkFact.work_date == row.work_date))
    has_work = bool(work_fact and work_fact.units > 0)
    balance = await _rest_account(db, row.employee_id)
    rolling = await _rolling_account(db, row.employee_id)
    if current:
        if current.units and has_work:
            balance.units += current.units
            returned = current.units
            current.status, current.units = "worked", 0
            _record_ledger(db, source_key=f"ordinary-reversal:{current.id}", eid=row.employee_id,
                work_date=row.work_date, action="ordinary_reversal", delta=returned,
                rest_account=balance, rolling_account=rolling, actor_id=None,
                reason="轮休日补录有效出勤，退回原自动扣除额度")
        elif current.status == "applied" and row.kind != "rest":
            current.status, current.reason = "needs_review", "原轮休已改为上班，待实际出勤确认后退还扣除额度"
        elif current.status == "needs_review" and row.kind == "rest" and current.units:
            current.status, current.reason = "applied", "轮休安排恢复，保留原已扣额度"
        elif current.status not in {"applied", "needs_review"} and has_work:
            current.status, current.reason = "worked", "轮休日实际出勤，不扣休息额度"
        elif current.status == "worked" and not has_work:
            current.status, current.reason = "needs_action", "原轮休日出勤证据已变化，待处理"
        return  # Insufficient days stay pending rather than consume later earnings.
    if has_work:
        status, units, reason = "worked", 0, "轮休日实际出勤，不自动扣休息额度"
    elif work_fact and work_fact.status in {"pending", "needs_review"}:
        status, units, reason = "needs_action", 0, "轮休日出勤待确认，不自动扣除额度"
    elif balance.units >= REST_UNITS_PER_DAY:
        status, units, reason = "applied", REST_UNITS_PER_DAY, "六上休一正常轮休自动扣除"
        balance.units -= units
    else:
        status, units, reason = "needs_action", 0, "到期时休息额度不足，待处理"
    item = RotationAutoRest(employee_id=row.employee_id, project_id=row.project_id,
        work_date=row.work_date, status=status, units=units, reason=reason)
    db.add(item)
    await db.flush()
    if status == "applied":
        _record_ledger(db, source_key=f"ordinary:{item.id}", eid=row.employee_id,
            work_date=row.work_date, action="ordinary_rest", delta=-units,
            rest_account=balance, rolling_account=rolling, actor_id=None, reason=reason)


async def sync_employee_through(db, eid: int, through: date):
    """Settle one person's dated facts in order; reads of calendar never settle."""
    configured = await policy(db)
    if not configured or through < configured.effective_date:
        return
    await lock_person(db, eid)
    rows = (await db.scalars(select(RotationDay).where(RotationDay.employee_id == eid,
        RotationDay.work_date >= configured.effective_date, RotationDay.work_date <= through)
        .order_by(RotationDay.work_date))).all()
    for row in rows:
        await _settle_work_day(db, row)
        await _settle_ordinary_rest(db, row)


async def settle_due_rest(db):
    """Background catch-up for elapsed normal rest days without a punch event."""
    global _last_due_rest_scan
    if time.monotonic() - _last_due_rest_scan < 300:
        return 0
    configured = await policy(db)
    if not configured or configured.effective_date > today():
        return 0
    rows = (await db.scalars(select(RotationDay.employee_id).outerjoin(RotationAutoRest,
        (RotationAutoRest.employee_id == RotationDay.employee_id) &
        (RotationAutoRest.work_date == RotationDay.work_date))
        .where(RotationDay.work_date >= configured.effective_date, RotationDay.work_date <= today(),
            RotationDay.kind == "rest", RotationAutoRest.id.is_(None))
        .order_by(RotationDay.work_date).limit(200))).all()
    for eid in dict.fromkeys(rows):
        await sync_employee_through(db, eid, today())
    if len(rows) < 200:
        _last_due_rest_scan = time.monotonic()
    return len(rows)


async def review_work_day(db, user, eid: int, work_date: date, action: str, work_days: float, note: str):
    await field_roles.require_aftersales_approver(db, user, note)
    if user.id == eid:
        raise HTTPException(403, "不能确认本人出勤")
    if action not in {"approve", "reject"} or work_days not in {0, 0.5, 1} or not note.strip():
        raise HTTPException(422, "请填写有效确认结果和原因")
    configured = await policy(db)
    if not configured or work_date < configured.effective_date or work_date > today():
        raise HTTPException(422, "日期不在滚动额度生效范围内")
    await sync_employee_through(db, eid, work_date)
    fact = await db.scalar(select(RotationWorkFact).where(RotationWorkFact.employee_id == eid,
        RotationWorkFact.work_date == work_date))
    if not fact or fact.status not in {"pending", "needs_review"}:
        raise HTTPException(409, "该日期没有待确认的有效出勤，请刷新")
    from app.services.rotation import day_row
    row = await day_row(db, eid, work_date)
    if not row or row.project_id != fact.project_id:
        raise HTTPException(409, "项目排班已变化，请刷新")
    evidence = await _facts(db, row)
    requested = int(work_days * 2) if action == "approve" else 0
    if requested > evidence["suggested_units"]:
        raise HTTPException(422, "确认天数不能超过可核实的有效出勤")
    if evidence["overtime"] and requested:
        raise HTTPException(409, "当日已有加班转调休，不能重复累计")
    before = snapshot(fact)
    await _accept_work(db, fact, requested, user.id, note.strip())
    fact.suggested_units = evidence["suggested_units"]
    fact.fingerprint = evidence["fingerprint"]
    db.add(AuditLog(operator_id=user.id, operator_name=user.name, module="attendance",
        action="rotation_work_review", resource_type="rotation", resource_id=fact.id,
        before_data=json.dumps(jsonable_encoder(before), ensure_ascii=False),
        after_data=json.dumps(jsonable_encoder(snapshot(fact)), ensure_ascii=False)))
    await db.flush()
    return {"employee_id": eid, "date": work_date, "status": fact.status, "work_days": fact.units / 2}


async def pending_work_days(db, user):
    roles = await field_roles.role_names(db, user)
    if not roles & {"admin", "aftersales_manager"}:
        raise HTTPException(403, "仅售后经理或管理员可查看待确认出勤")
    configured = await policy(db)
    if configured:
        trips = (await db.scalars(select(AttendanceEffect).join(RotationDay,
            (RotationDay.employee_id == AttendanceEffect.employee_id) &
            (RotationDay.work_date == AttendanceEffect.work_date))
            .where(AttendanceEffect.effect_type == "business_trip",
                AttendanceEffect.effect_status == "active",
                AttendanceEffect.work_date >= configured.effective_date,
                AttendanceEffect.work_date <= today())
            .order_by(AttendanceEffect.work_date.desc()).limit(500))).all()
        latest_trip_by_employee = {}
        for row in trips:
            latest_trip_by_employee[row.employee_id] = max(
                latest_trip_by_employee.get(row.employee_id, row.work_date), row.work_date)
        for eid, end_day in latest_trip_by_employee.items():
            await sync_employee_through(db, eid, end_day)
    rows = (await db.scalars(select(RotationWorkFact).where(
        RotationWorkFact.status.in_(["pending", "needs_review"]))
        .order_by(RotationWorkFact.work_date, RotationWorkFact.id).limit(500))).all()
    names = {p.id: p.name for p in (await db.scalars(select(Employee).where(Employee.id.in_(
        {r.employee_id for r in rows} or {-1})))).all()}
    return [{"employee_id": r.employee_id, "employee_name": names.get(r.employee_id),
        "date": r.work_date, "project_id": r.project_id,
        "suggested_days": r.suggested_units / 2, "status": r.status, "reason": r.reason} for r in rows]


async def rest_projection(db, eid: int, start: date, end: date):
    """Read-only projected status, shared by self and management calendars."""
    from app.models.rotation_person_plan import RotationPersonalPlanDay
    configured = await policy(db)
    rest = await db.get(RotationAccount, eid)
    query_start = min(start, today())  # reserve earlier future dates before the viewed month
    planned = (await db.scalars(select(RotationDay).where(RotationDay.employee_id == eid,
        RotationDay.work_date >= query_start, RotationDay.work_date <= end,
        RotationDay.kind.in_(["rest", "leave", "half_leave"]))
        .order_by(RotationDay.work_date))).all()
    pre_rule = (await db.scalars(select(RotationPersonalPlanDay).where(
        RotationPersonalPlanDay.employee_id == eid,
        RotationPersonalPlanDay.work_date >= start, RotationPersonalPlanDay.work_date <= end,
        RotationPersonalPlanDay.kind == "rest").order_by(RotationPersonalPlanDay.work_date))).all()
    published_dates = set((await db.scalars(select(RotationDay.work_date).where(
        RotationDay.employee_id == eid, RotationDay.work_date >= start,
        RotationDay.work_date <= end))).all())
    auto_rows = (await db.scalars(select(RotationAutoRest).where(RotationAutoRest.employee_id == eid,
        RotationAutoRest.work_date >= query_start, RotationAutoRest.work_date <= end))).all()
    auto_by_date = {r.work_date: r for r in auto_rows}
    claims = (await db.scalars(select(RotationClaim).where(RotationClaim.employee_id == eid,
        RotationClaim.kind == "leave", RotationClaim.status.in_(["pending", "approved"])))).all()
    claim_by_date = {date.fromisoformat(d["date"]): c for c in claims for d in c.days}
    results = []
    projected_units = rest.units if rest else 0
    for row in planned:
        claim = claim_by_date.get(row.work_date)
        auto = auto_by_date.get(row.work_date)
        ordinary = ordinary_rest(row)
        units = 1 if row.kind == "half_leave" else 2
        if not configured or row.work_date < configured.effective_date:
            status = "approved"  # Legacy dates keep their existing treatment.
        elif (auto and auto.status == "applied") or (claim and claim.status == "approved"):
            status = "approved"
        elif row.work_date < today():
            status = "needs_action"
        elif projected_units < units:
            status = "pending_credit"
        elif ordinary:
            status = "ready"
            projected_units -= units
        elif claim and claim.status == "pending":
            status = "pending_review"
            projected_units -= units
        else:
            status = "pending_application"
            projected_units -= units
        if row.work_date >= start:
            results.append({"date": row.work_date, "project_id": row.project_id,
                "kind": "ordinary" if ordinary else "concentrated", "status": status,
                "days": units / 2})
    for row in pre_rule:
        if row.work_date not in published_dates:
            results.append({"date": row.work_date, "project_id": row.project_id,
                "kind": row.rest_type or "concentrated",
                "status": "pending_rule" if row.project_id else "pending_project", "days": 1})
    results.sort(key=lambda item: item["date"])
    return results, auto_rows


async def my_entitlement(db, user):
    """Self-only global snapshot, independent of month and project filters."""
    eid = user.id
    # Local previews can disable background jobs.  A balance refresh settles
    # this employee's elapsed days under the same person lock.
    await sync_employee_through(db, eid, today())
    configured = await policy(db)
    rest = await db.get(RotationAccount, eid)
    rolling = await db.get(RotationEntitlementAccount, eid)
    facts = (await db.scalars(select(RotationWorkFact).where(RotationWorkFact.employee_id == eid,
        RotationWorkFact.status.in_(["pending", "needs_review"]))
        .order_by(RotationWorkFact.work_date.desc()).limit(100))).all()
    from app.models.rotation_person_plan import RotationPersonalPlanDay
    from app.models.field_service import FieldProjectPendingMember
    active = bool(rest or rolling or await db.scalar(select(RotationDay.id).where(RotationDay.employee_id == eid).limit(1))
        or await db.scalar(select(RotationDraftDay.id).where(RotationDraftDay.employee_id == eid).limit(1))
        or await db.scalar(select(RotationPersonalPlanDay.id).where(RotationPersonalPlanDay.employee_id == eid).limit(1))
        or await db.scalar(select(FieldProjectPendingMember.id).where(FieldProjectPendingMember.employee_id == eid).limit(1)))
    start, end = today() - timedelta(days=31), today() + timedelta(days=365)
    planned_rest, auto_rows = await rest_projection(db, eid, start, end)
    old_ledger = (await db.scalars(select(RotationLedger).where(RotationLedger.employee_id == eid)
        .order_by(RotationLedger.id.desc()).limit(100))).all()
    new_ledger = (await db.scalars(select(RotationEntitlementLedger).where(
        RotationEntitlementLedger.employee_id == eid).order_by(RotationEntitlementLedger.id.desc()).limit(100))).all()
    ledger = ([{"id": f"old-{r.id}", "action": r.action, "delta_days": r.delta_units / 2,
        "balance_days": r.balance_units / 2, "progress_days": None,
        "date": r.created_at, "reason": r.reason} for r in old_ledger] +
        [{"id": f"rolling-{r.id}", "action": r.action, "delta_days": r.delta_units / 2,
        "balance_days": r.balance_units / 2,
        "progress_days": (r.work_units % WORK_UNITS_PER_REST_DAY) / 2,
        "date": r.created_at, "reason": r.reason} for r in new_ledger])
    ledger.sort(key=lambda item: item["date"], reverse=True)
    updated = max([r.updated_at for r in facts] + [r.updated_at for r in auto_rows] +
        [r.created_at for r in old_ledger] + [r.created_at for r in new_ledger], default=None)
    return {"active": active, "enabled": bool(configured), "policy_enabled": bool(configured),
        "effective_date": configured.effective_date if configured else None,
        "available_days": (rest.units if rest else 0) / 2,
        "progress_units": (rolling.work_units if rolling else 0) % WORK_UNITS_PER_REST_DAY,
        "progress_days": ((rolling.work_units if rolling else 0) % WORK_UNITS_PER_REST_DAY) / 2,
        "threshold_days": 6,
        "pending_confirmation": [{"date": r.work_date, "project_id": r.project_id,
            "suggested_days": r.suggested_units / 2, "reason": r.reason, "status": r.status} for r in facts],
        "planned_rest": planned_rest, "ledger": ledger[:100], "updated_at": updated}
