"""Project-scoped rotation planning, approvals and rest accounting.

Days are published explicitly. Merely reading a calendar never creates attendance
or credits. Account/employee locks serialize approval, reversal and spending.
"""
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, or_

from app.models.attendance import AttendanceRule, AttendanceRecord, AttendanceMonthSummary, MonthlySummaryStatus, AttendanceEffect
from app.models.employee import Employee
from app.models.audit_log import AuditLog
from app.models.field_service import FieldAssignment, FieldMembership, FieldSite, FieldAttendanceSegment
from app.models.rotation import RotationDay, RotationClaim, RotationAccount, RotationLedger
from app.services import field_service as field
from app.services.field_membership import lock_person, snapshot, restore, utc, CST

LABELS = {"work": "上班", "rest": "正常轮休", "leave": "调休", "half_leave": "半天调休", "pending": "待确认"}
REST_POLICIES = {"comp_time", "rest", "rest_time", "time_off", "lieu", "adjust_rest"}


def today():
    return datetime.now(CST).date()


def is_rotation(rule):
    return bool(rule and (rule.extra_config or {}).get("rule_type") == "rotation")


def audit(db, user, action, before, after):
    db.add(AuditLog(operator_id=user.id, operator_name=user.name, module="attendance", action=action,
        resource_type="rotation", before_data=json.dumps(jsonable_encoder(before), ensure_ascii=False),
        after_data=json.dumps(jsonable_encoder(after), ensure_ascii=False)))


async def unlocked(db, employee_id, day):
    if await db.scalar(select(AttendanceMonthSummary.id).where(AttendanceMonthSummary.employee_id == employee_id,
            AttendanceMonthSummary.year == day.year, AttendanceMonthSummary.month == day.month,
            AttendanceMonthSummary.status == MonthlySummaryStatus.locked)):
        raise HTTPException(409, "考勤月份已锁定，请先解锁")


async def member_on(db, employee_id, project_id, day):
    start = datetime.combine(day, time.min, CST).astimezone(timezone.utc)
    return await db.scalar(select(FieldMembership.id).join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
        .join(FieldSite, FieldSite.id == FieldAssignment.site_id).where(FieldMembership.employee_id == employee_id,
            FieldSite.project_id == project_id, FieldMembership.started_at < start + timedelta(days=1),
            or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > start),
            or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > FieldMembership.started_at)).limit(1))


async def day_row(db, employee_id, day, project_id=None):
    row = await db.scalar(select(RotationDay).where(RotationDay.employee_id == employee_id, RotationDay.work_date == day))
    return row if row and (project_id is None or row.project_id == project_id) else None


def day_view(row):
    return {"id": row.id, "employee_id": row.employee_id, "project_id": row.project_id, "date": row.work_date,
        "cycle_start": row.cycle_start, "original_kind": row.original_kind, "kind": row.kind,
        "label": LABELS[row.kind], "revision": row.revision, "reason": row.reason, "updated_by": row.updated_by,
        "updated_at": row.updated_at, "rule_name": row.rule_snapshot.get("name"),
        "required_hours": required_hours(row) / (2 if row.kind == "half_leave" else 1) if row.kind in {"work", "half_leave"} else 0}


def required_hours(row):
    from app.services.attendance import AttendanceRecordService as attendance
    rule = restore(AttendanceRule, row.rule_snapshot)
    return attendance._free_required_hours(rule) or Decimal(str(rule.work_hours_per_day or 8))


async def apply_runtime(db, runtime, employee_id, day, project_id=None):
    """Use the published rule snapshot; frozen punch data remains immutable."""
    row = await day_row(db, employee_id, day, project_id)
    if not row and not is_rotation(runtime.get("rule")):
        return runtime
    if row:
        from app.services.attendance import AttendanceRecordService as attendance
        rule = restore(AttendanceRule, row.rule_snapshot)
        # Half-day leave reduces only that day's obligation, never the source rule.
        extra = dict(rule.extra_config or {})
        extra["_rotation_original_rest"] = row.original_kind == "rest"
        extra["rest_day_shift_mode"] = "multi"
        if row.kind == "half_leave":
            extra["free_work_hours_mode"] = f"limit:{required_hours(row) / 2}"
        rule.extra_config = extra
        runtime.update(attendance._build_runtime(rule, None, day))
        state = day_view(row)
    else:
        state = {"kind": "pending", "label": LABELS["pending"], "date": day, "required_hours": 0}
    state = jsonable_encoder(state)
    runtime["extra"] = {**(runtime.get("extra") or {}), "_rotation_pending": state["kind"] == "pending"}
    needs_punch = state["kind"] in {"work", "half_leave"}
    runtime.update(rotation=state, no_punch=not needs_punch,
        check_in_required=needs_punch, check_out_required=needs_punch)
    return runtime


async def project_rule(db, user, project_id):
    project = await field.require_project_manager(db, user, project_id)
    site = await field.project_site(db, project_id)
    rule = await db.get(AttendanceRule, site.rule_id) if site else None
    if not project.is_active or not rule or not rule.is_active or not is_rotation(rule):
        raise HTTPException(422, "请先为项目配置已启用的现场轮休规则")
    return rule


async def preview(db, user, project_id, data):
    rule = await project_rule(db, user, project_id)
    if data.start_date < max(today(), rule.effective_date):
        raise HTTPException(422, "周期排班须从今天或规则生效日之后开始，历史调整请使用调班申请")
    edits = {e.date: e.kind for e in data.edits}
    result = []
    for eid in sorted(set(data.employee_ids)):
        for offset in range((data.end_date - data.start_date).days + 1):
            day = data.start_date + timedelta(days=offset)
            if not await member_on(db, eid, project_id, day):
                raise HTTPException(422, "所选日期内人员不属于该项目，请核对参与区间")
            row = await day_row(db, eid, day)
            original = "rest" if (day - data.cycle_start).days % 7 == 6 else "work"
            result.append({"employee_id": eid, "date": day, "cycle_start": data.cycle_start,
                "original_kind": row.original_kind if row and row.project_id == project_id and row.cycle_start == data.cycle_start else original, "kind": edits.get(day, original),
                "expected_revision": row.revision if row else 0})
    digest = hashlib.sha256(json.dumps(jsonable_encoder([snapshot(rule), result]), sort_keys=True).encode()).hexdigest()
    return {"rows": result, "revision": digest, "rule_id": rule.id}


async def publish(db, user, project_id, data, revision):
    for eid in sorted(set(data.employee_ids)):
        await lock_person(db, eid)
    plan = await preview(db, user, project_id, data)
    if revision != plan["revision"]:
        raise HTTPException(409, "排班或规则已变化，请重新预览")
    rule = await project_rule(db, user, project_id)
    for item in plan["rows"]:
        eid, day = item["employee_id"], item["date"]
        await unlocked(db, eid, day)
        row = await day_row(db, eid, day)
        if await db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.employee_id == eid, AttendanceRecord.date == day)):
            raise HTTPException(409, "当天已有打卡，请从未来日期发布；已有排班通过带原因的日期调整修改")
        if row:
            if row.project_id != project_id and await member_on(db, eid, row.project_id, day):
                raise HTTPException(409, "人员当天仍参与其他项目，请从调动后的未来日期安排")
            if row.kind in {"leave", "half_leave"}:
                raise HTTPException(409, "已有批准调休，不能用周期排班覆盖")
            if await db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.employee_id == eid, AttendanceRecord.date == day)):
                raise HTTPException(409, "当天已有打卡，请通过带原因的日期调整修改")
            before = day_view(row)
            row.project_id = project_id
            row.kind = item["kind"]
            row.original_kind = item["original_kind"]
            row.cycle_start = data.cycle_start
            row.rule_snapshot = snapshot(rule)
            row.revision += 1
            row.reason, row.updated_by = data.reason, user.id
        else:
            before = None
            row = RotationDay(employee_id=eid, project_id=project_id, work_date=day, cycle_start=data.cycle_start,
                original_kind=item["original_kind"], kind=item["kind"], rule_snapshot=snapshot(rule),
                revision=1, reason=data.reason, updated_by=user.id)
            db.add(row)
        await db.flush()
        audit(db, user, "rotation_publish", before, day_view(row))
    return {"count": len(plan["rows"])}


async def mark_changed(db, row):
    """Explicit day changes refresh that day only; never rebuild unrelated history."""
    from app.services.attendance_recalc import AttendanceRecalcService
    # Existing project snapshots carry punch facts; update their schedule metadata only.
    segments = (await db.scalars(select(FieldAttendanceSegment).where(FieldAttendanceSegment.employee_id == row.employee_id,
        FieldAttendanceSegment.project_id == row.project_id, FieldAttendanceSegment.work_date == row.work_date))).all()
    for segment in segments:
        frozen = dict(segment.rule_snapshot)
        runtime = dict(frozen.get("_runtime") or {})
        runtime["rotation"] = jsonable_encoder(day_view(row))
        runtime["no_punch"] = row.kind not in {"work", "half_leave"}
        runtime["check_in_required"] = runtime["check_out_required"] = not runtime["no_punch"]
        extra = dict(runtime.get("extra") or frozen.get("extra_config") or {})
        extra["free_work_hours_mode"] = f"limit:{required_hours(row) / (2 if row.kind == 'half_leave' else 1)}"
        extra["_rotation_original_rest"] = row.original_kind == "rest"
        extra["rest_day_shift_mode"] = "multi"
        runtime["extra"] = extra
        frozen["_runtime"] = runtime
        segment.rule_snapshot = frozen
    await AttendanceRecalcService.mark_dirty(db, employee_id=row.employee_id, work_date=row.work_date)


async def manual_day(db, user, project_id, employee_id, day, kind, reason):
    """Explicit confirmation of an unpublished date, never a read-time fallback."""
    if await day_row(db, employee_id, day):
        raise HTTPException(409, "当天已有其他项目安排或排班已变化，请刷新")
    rule = await project_rule(db, user, project_id)
    if rule.effective_date > day or not await member_on(db, employee_id, project_id, day):
        raise HTTPException(422, "日期不在规则生效或项目参与区间内")
    row = RotationDay(employee_id=employee_id, project_id=project_id, work_date=day,
        cycle_start=day-timedelta(days=6) if kind == "rest" else day, original_kind=kind, kind=kind,
        rule_snapshot=snapshot(rule), revision=0, reason=reason, updated_by=user.id)
    db.add(row)
    await db.flush()
    return row


async def adjust(db, user, project_id, data):
    await field.require_project_manager(db, user, project_id)
    if len({e.date for e in data.days}) != len(data.days):
        raise HTTPException(422, "日期不能重复")
    result = []
    edits = [(d.employee_id, d) for d in data.changes] if data.changes else [(eid, d) for eid in sorted(set(data.employee_ids)) for d in data.days]
    for eid in sorted({eid for eid, _ in edits}):
        await lock_person(db, eid)
        for edit in [d for person, d in edits if person == eid]:
            await unlocked(db, eid, edit.date)
            row = await day_row(db, eid, edit.date, project_id)
            if not row and edit.expected_revision in (None, 0):
                row = await manual_day(db, user, project_id, eid, edit.date, edit.kind, data.reason)
            if not row or not await member_on(db, eid, project_id, edit.date):
                raise HTTPException(409, "排班已变化，或人员不属于该项目")
            if edit.expected_revision is not None and edit.expected_revision != row.revision:
                raise HTTPException(409, "排班已变化，请刷新后重新调整")
            if row.kind in {"leave", "half_leave"}:
                raise HTTPException(409, "请先撤销已批准的调休申请")
            before = day_view(row)
            row.kind, row.reason, row.updated_by = edit.kind, data.reason, user.id
            row.revision += 1
            await db.flush()
            audit(db, user, "rotation_adjust", before, day_view(row))
            await mark_changed(db, row)
            result.append(day_view(row))
    return {"rows": result}


async def credit_facts(db, row):
    from app.services.attendance import AttendanceRecordService as attendance
    segments = (await db.scalars(select(FieldAttendanceSegment).where(FieldAttendanceSegment.employee_id == row.employee_id,
        FieldAttendanceSegment.work_date == row.work_date, FieldAttendanceSegment.project_id == row.project_id))).all()
    if segments:
        pieces = [restore(AttendanceRecord, s.record_data) for s in segments]
    else:
        record = await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id == row.employee_id, AttendanceRecord.date == row.work_date))
        other_segment = await db.scalar(select(FieldAttendanceSegment.id).where(FieldAttendanceSegment.employee_id == row.employee_id,
            FieldAttendanceSegment.work_date == row.work_date).limit(1))
        pieces = [record] if record and not other_segment else []
    hours = Decimal(0)
    complete = bool(pieces) and all(p.clock_in_time and p.clock_out_time for p in pieces)
    for piece in pieces:
        if piece.clock_in_time and piece.clock_out_time:
            hours += attendance._calculate_work_hours(piece.clock_in_time, piece.clock_out_time,
                target_date=row.work_date, rest_periods=[], round_result=False) or Decimal(0)
    required = required_hours(row)
    facts = {"hours": str(hours), "required_hours": str(required), "complete": complete,
        "issues": list(dict.fromkeys(p.anomaly_type for p in pieces if p.anomaly_type)),
        "punches": [{"in": p.clock_in_time, "out": p.clock_out_time, "anomaly": p.anomaly_type} for p in pieces]}
    facts = jsonable_encoder(facts)
    facts["fingerprint"] = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    facts["suggested_days"] = 1 if complete and hours >= required else 0.5 if complete and hours >= required / 2 else 0
    return facts


async def account(db, eid):
    row = await db.get(RotationAccount, eid)
    if row is None:
        row = RotationAccount(employee_id=eid, units=0)
        db.add(row)
        await db.flush()
    return row


async def post_ledger(db, user, claim, action, units, note):
    balance = await account(db, claim.employee_id)
    if balance.units + units < 0:
        raise HTTPException(409, "待休余额不足。已使用额度不能直接撤销，请先核对使用记录")
    balance.units += units
    db.add(RotationLedger(employee_id=claim.employee_id, claim_id=claim.id, action=action, delta_units=units,
        balance_units=balance.units, actor_id=user.id, reason=note))


async def create_claim(db, user, data):
    eid = data.employee_id or user.id
    if eid != user.id:
        await field.require_project_manager(db, user, data.project_id)
    await lock_person(db, eid)
    snapshots = []
    for item in data.days:
        row = await day_row(db, eid, item.date, data.project_id)
        if not row and data.kind == "schedule" and await member_on(db, eid, data.project_id, item.date):
            site = await field.project_site(db, data.project_id)
            rule = await db.get(AttendanceRule, site.rule_id) if site else None
            if is_rotation(rule) and rule.effective_date <= item.date and not await day_row(db, eid, item.date):
                await unlocked(db, eid, item.date)
                snapshots.append({**item.model_dump(mode="json"), "revision": 0, "before_kind": "pending"})
                continue
        if not row or not await member_on(db, eid, data.project_id, item.date):
            raise HTTPException(422, "请先由负责人发布本人在该项目的排班")
        await unlocked(db, eid, item.date)
        if data.kind == "credit" and (row.original_kind != "rest" or row.kind == "pending" or item.date > today()):
            raise HTTPException(422, "仅能确认已发生的原定轮休日出勤")
        if data.kind == "leave" and row.kind != "work":
            raise HTTPException(422, "调休须安排在已发布的上班日，正常轮休无需扣余额")
        snapshots.append({**item.model_dump(mode="json"), "revision": row.revision, "before_kind": row.kind})
    existing = (await db.scalars(select(RotationClaim).where(RotationClaim.employee_id == eid,
        RotationClaim.kind == data.kind, RotationClaim.status.in_(["pending", "approved"])))).all()
    requested = {d["date"] for d in snapshots}
    if any(requested & {d["date"] for d in c.days} for c in existing):
        raise HTTPException(409, "这些日期已有同类待审或已批准申请")
    requested_units = sum(1 if d["kind"] == "half_leave" else 2 for d in snapshots) if data.kind == "leave" else 0
    claim = RotationClaim(employee_id=eid, project_id=data.project_id, kind=data.kind, days=snapshots,
        status="pending", units=requested_units, reason=data.reason.strip(), submitted_by=user.id, facts={})
    db.add(claim)
    await db.flush()
    audit(db, user, "rotation_request", None, snapshot(claim))
    return await claim_view(db, claim)


async def claim_view(db, claim):
    result = snapshot(claim)
    result["employee_name"] = (await db.get(Employee, claim.employee_id)).name
    result["days_amount"] = claim.units / 2
    if claim.kind == "credit":
        row = await day_row(db, claim.employee_id, date.fromisoformat(claim.days[0]["date"]), claim.project_id)
        facts = await credit_facts(db, row) if row else {}
        result["current_facts"] = facts
        result["needs_review"] = claim.status == "approved" and facts.get("fingerprint") != claim.facts.get("fingerprint")
    return result


async def review(db, user, claim_id, data):
    claim = await db.get(RotationClaim, claim_id)
    if not claim:
        raise HTTPException(404, "申请不存在")
    await field.require_project_manager(db, user, claim.project_id)
    await lock_person(db, claim.employee_id)
    await db.refresh(claim)
    desired = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[data.action]
    if claim.status == desired:
        return await claim_view(db, claim)
    if claim.status != ("approved" if data.action == "revoke" else "pending"):
        raise HTTPException(409, "申请状态已变化，请刷新")
    before = snapshot(claim)
    if data.action == "reject":
        pass
    elif data.action == "revoke":
        if claim.kind in {"credit", "leave"}:
            await post_ledger(db, user, claim, "reversal", -claim.units if claim.kind == "credit" else claim.units, data.note)
        if claim.kind != "credit":
            for item in claim.days:
                day = date.fromisoformat(item["date"])
                await unlocked(db, claim.employee_id, day)
                row = await day_row(db, claim.employee_id, day, claim.project_id)
                if not row or row.revision != item.get("applied_revision"):
                    raise HTTPException(409, "日期已再次调整，请先核对后续调整，不能直接撤销")
                row.kind, row.reason, row.updated_by = item["before_kind"], data.note, user.id
                row.revision += 1
                await mark_changed(db, row)
    else:
        if claim.kind == "credit":
            item = claim.days[0]
            row = await day_row(db, claim.employee_id, date.fromisoformat(item["date"]), claim.project_id)
            if not row or row.original_kind != "rest" or row.kind == "pending":
                raise HTTPException(409, "原定轮休安排已变化，请重新核对出勤来源")
            await unlocked(db, claim.employee_id, row.work_date)
            facts = await credit_facts(db, row)
            if not facts["complete"]:
                raise HTTPException(409, "缺少完整上下班卡，请先补卡后再确认")
            days = facts["suggested_days"] if data.days is None else data.days
            if days > facts["suggested_days"] and not (facts["suggested_days"] == 0 and days == 0.5):
                raise HTTPException(422, "不能超过按有效工时折算的额度")
            effects = (await db.scalars(select(AttendanceEffect).where(AttendanceEffect.employee_id == claim.employee_id,
                AttendanceEffect.work_date == row.work_date, AttendanceEffect.effect_type == "overtime", AttendanceEffect.effect_status == "active"))).all()
            if any(e.pay_policy in REST_POLICIES for e in effects):
                raise HTTPException(409, "当天已有加班转调休，请先核对并撤销重复来源")
            claim.units, claim.facts = int(days * 2), facts
            await post_ledger(db, user, claim, "credit", claim.units, data.note)
        else:
            applied = []
            for item in claim.days:
                day = date.fromisoformat(item["date"])
                await unlocked(db, claim.employee_id, day)
                row = await day_row(db, claim.employee_id, day, claim.project_id)
                if row is None and claim.kind == "schedule" and item["revision"] == 0:
                    row = await manual_day(db, user, claim.project_id, claim.employee_id, day, item["kind"], data.note)
                if not row or row.revision != item["revision"] or not await member_on(db, claim.employee_id, claim.project_id, day):
                    raise HTTPException(409, "排班或项目参与关系已变化，请重新申请")
                if claim.kind == "leave" and row.kind != "work":
                    raise HTTPException(409, "日期已不是上班日")
                before_day = day_view(row)
                row.kind, row.reason, row.updated_by = item["kind"], data.note, user.id
                row.revision += 1
                applied.append({**item, "applied_revision": row.revision})
                audit(db, user, "rotation_approval_day", before_day, day_view(row))
                await mark_changed(db, row)
            claim.days = applied
            if claim.kind == "leave":
                claim.units = sum(1 if d["kind"] == "half_leave" else 2 for d in claim.days)
                await post_ledger(db, user, claim, "leave", -claim.units, data.note)
    claim.status, claim.reviewed_by, claim.review_note = desired, user.id, data.note.strip()
    claim.reviewed_at = datetime.now(timezone.utc)
    audit(db, user, "rotation_" + data.action, before, snapshot(claim))
    await db.flush()
    return await claim_view(db, claim)


async def ensure_overtime_not_credited(db, employee_id, day, pay_policy):
    if pay_policy not in REST_POLICIES:
        return
    await lock_person(db, employee_id)
    claims = (await db.scalars(select(RotationClaim).where(RotationClaim.employee_id == employee_id,
        RotationClaim.kind == "credit", RotationClaim.status == "approved", RotationClaim.units > 0))).all()
    if any(c.days[0]["date"] == day.isoformat() for c in claims):
        raise HTTPException(409, "当天已确认轮休攒休，不能重复加班转调休，请先核对来源")


async def calendar(db, user, start, end, project_id=None, employee_id=None):
    if not 0 <= (end - start).days <= 365:
        raise HTTPException(422, "日期范围须为1至366天")
    admin = await field.is_admin(db, user)
    managed = None
    if project_id:
        try:
            await field.require_project_manager(db, user, project_id)
            managed = True
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
            managed = False
        if not managed and employee_id not in (None, user.id):
            raise HTTPException(403, "只能查看本人排班")
    elif not admin:
        if employee_id is not None and employee_id != user.id:
            raise HTTPException(403, "只能查看本人排班")
        employee_id = user.id
    eid = employee_id or (user.id if not managed and not admin else None)
    stmt = select(RotationDay).where(RotationDay.work_date >= start, RotationDay.work_date <= end)
    claims_stmt = select(RotationClaim)
    if project_id:
        stmt, claims_stmt = stmt.where(RotationDay.project_id == project_id), claims_stmt.where(RotationClaim.project_id == project_id)
    if eid:
        stmt, claims_stmt = stmt.where(RotationDay.employee_id == eid), claims_stmt.where(RotationClaim.employee_id == eid)
    days = (await db.scalars(stmt.order_by(RotationDay.work_date, RotationDay.employee_id))).all()
    claims = [c for c in (await db.scalars(claims_stmt.order_by(RotationClaim.id.desc()))).all()
        if any(start.isoformat() <= d["date"] <= end.isoformat() for d in c.days)]
    names = {p.id: p.name for p in (await db.scalars(select(Employee).where(Employee.id.in_({d.employee_id for d in days} | {c.employee_id for c in claims})))).all()}
    ledger_stmt = select(RotationLedger).join(RotationClaim, RotationLedger.claim_id == RotationClaim.id)
    if project_id:
        ledger_stmt = ledger_stmt.where(RotationClaim.project_id == project_id)
    if eid:
        ledger_stmt = ledger_stmt.where(RotationLedger.employee_id == eid)
    ledger = (await db.scalars(ledger_stmt.order_by(RotationLedger.id.desc()).limit(500))).all()
    accounts = (await db.scalars(select(RotationAccount).where(RotationAccount.employee_id.in_(set(names) | ({eid} if eid else set()))))).all()
    return {"days": [{**day_view(d), "employee_name": names.get(d.employee_id)} for d in days],
        "claims": [await claim_view(db, c) for c in claims], "ledger": [snapshot(l) for l in ledger],
        "balances": [{"employee_id": a.employee_id, "employee_name": names.get(a.employee_id), "days": a.units / 2} for a in accounts],
        "can_manage": bool(admin or managed), "labels": LABELS}
