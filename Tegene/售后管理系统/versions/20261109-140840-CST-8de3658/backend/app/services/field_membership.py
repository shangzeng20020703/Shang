"""Project membership intervals and punch-time snapshots, with daily attendance rollups."""
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text, or_

from app.models.employee import Employee
from app.models.field_service import FieldAssignment, FieldAssignmentDay, FieldMembership, FieldAttendanceSegment, FieldSite, FieldProject, FieldTransferRequest, FieldProjectPendingMember
from app.models.attendance import AttendanceRecord, AttendanceRule, AttendanceStatus, CorrectionStatus, ClockSource, ShiftSchedule

CST = timezone(timedelta(hours=8))


def clock_now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def snapshot(row):
    return jsonable_encoder({c.name: getattr(row, c.name) for c in row.__table__.columns})


def restore(model, data):
    values = {}
    for column in model.__table__.columns:
        value = data.get(column.name)
        kind = column.type.python_type
        if value is not None:
            if kind in (datetime, date, time):
                value = kind.fromisoformat(value)
                if kind is datetime:
                    value = utc(value)
            elif kind is Decimal:
                value = Decimal(str(value))
            elif isinstance(kind, type) and hasattr(kind, '__members__'):
                value = kind(value)
        if column.name in data:
            values[column.name] = value
    return model(**values)


def segment_runtime(frozen, day, punch_at=None):
    from app.services.attendance import AttendanceRecordService as attendance
    rule = restore(AttendanceRule, frozen)
    runtime = attendance._build_runtime(rule, None, day, punch_at_cst=punch_at)
    saved = frozen.get("_runtime")
    if saved:
        runtime.update(saved)
        for key in ("expected_in", "expected_out", "half_day_pm_start", "half_day_absence_cutoff"):
            runtime[key] = time.fromisoformat(saved[key]) if saved.get(key) else None
        runtime["rest_periods"] = [(time.fromisoformat(a), time.fromisoformat(b)) for a, b in saved.get("rest_periods", [])]
    runtime["project_assigned"] = True
    return runtime


async def lock_person(db, employee_id):
    # A no-op write also serializes SQLite transactions (SELECT FOR UPDATE does not).
    await db.execute(text("UPDATE employees SET id = id WHERE id = :id"), {"id": employee_id})
    return await db.scalar(select(Employee).where(Employee.id == employee_id).execution_options(populate_existing=True))


async def migrate_assignments(db):
    """Idempotent additive migration; no punch, shift or historical result is rewritten."""
    rows = (await db.scalars(select(FieldAssignment).where(~FieldAssignment.id.in_(select(FieldMembership.assignment_id))))).all()
    for assignment in rows:
        days = list((await db.scalars(select(FieldAssignmentDay.work_date).where(FieldAssignmentDay.assignment_id == assignment.id))).all())
        start = min(days) if days else assignment.start_date
        end = max(days) if days else assignment.end_date
        started = datetime.combine(start, time.min, CST).astimezone(timezone.utc)
        ended = datetime.combine(end + timedelta(days=1), time.min, CST).astimezone(timezone.utc)
        if assignment.status != "active" and not days:
            ended = started
        db.add(FieldMembership(assignment_id=assignment.id, employee_id=assignment.employee_id,
                               started_at=started, ended_at=ended))
    await db.flush()


async def current(db, employee_id, now=None):
    now = now or clock_now()
    rows = (await db.execute(select(FieldMembership, FieldAssignment, FieldSite, FieldProject)
        .join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
        .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
        .join(FieldProject, FieldProject.id == FieldSite.project_id)
        .where(FieldMembership.employee_id == employee_id, FieldMembership.started_at <= now,
               or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > now)))).all()
    if len(rows) > 1:
        raise HTTPException(409, "人员存在重叠项目，请先核对参与记录")
    return rows[0] if rows else None


async def project_on(db, employee_id, day):
    """Return the single main project for a China-local calendar day.

    New dated assignments must not put two project intervals in the same day.
    Legacy same-day transfer rows remain stored for historical audit and are
    surfaced as a conflict instead of choosing one project arbitrarily.
    """
    start = datetime.combine(day, time.min, CST).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    rows = (await db.execute(select(FieldMembership, FieldAssignment, FieldSite, FieldProject)
        .join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
        .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
        .join(FieldProject, FieldProject.id == FieldSite.project_id)
        .where(FieldMembership.employee_id == employee_id,
               FieldMembership.started_at < end,
               or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > start)))).all()
    if len(rows) > 1:
        raise HTTPException(409, "该员工当天存在多个项目，请先确认唯一主项目")
    return rows[0] if rows else None


async def managed_field_person(db, employee_id, person=None):
    if person is None:
        from app.services.attendance import AttendanceRecordService
        person = await AttendanceRecordService._get_employee(db, employee_id)
    if not isinstance(person, Employee):
        return False
    if person.is_field_staff:
        return True
    if await db.scalar(select(FieldProjectPendingMember.id).where(
            FieldProjectPendingMember.employee_id == employee_id).limit(1)):
        return True
    return (await db.execute(select(FieldMembership.id).where(FieldMembership.employee_id == employee_id).limit(1))).scalar_one_or_none() is not None


async def day_runtime(db, person, day):
    """Unpunched days still use project rules when generating attendance summaries."""
    from app.services.attendance import AttendanceRecordService as attendance
    owner = await project_on(db, person.id, day)
    if owner:
        membership, _, site, _ = owner
        shift = await db.scalar(select(ShiftSchedule).join(FieldAssignmentDay, FieldAssignmentDay.shift_id == ShiftSchedule.id)
            .where(FieldAssignmentDay.assignment_id == membership.assignment_id, FieldAssignmentDay.work_date == day))
        rule_id = shift.rule_id if shift else site.rule_id
    else:
        # Retain manual historical schedules; ended project schedules cannot reappear.
        shift = await db.scalar(select(ShiftSchedule).outerjoin(FieldAssignmentDay, FieldAssignmentDay.shift_id == ShiftSchedule.id)
            .where(ShiftSchedule.employee_id == person.id, ShiftSchedule.date == day, FieldAssignmentDay.id.is_(None)))
        if not shift:
            return None
        rule_id = shift.rule_id
    rule = await db.get(AttendanceRule, rule_id)
    if not rule or rule.effective_date > day:
        return None
    if owner and (rule.extra_config or {}).get("rule_type") == "rotation":
        from app.services.field_pending import rotation_punch_ready
        if not await rotation_punch_ready(db, person.id, site.project_id, day):
            return None
    runtime = attendance._build_runtime(rule, shift, day)
    attendance._apply_rule_runtime_people(runtime, person.id)
    runtime.update(employee=person, shift=shift, project_assigned=True)
    from app.services.rotation import apply_runtime
    project_id = site.project_id if owner else None
    await apply_runtime(db, runtime, person.id, day, project_id)
    return runtime


async def add_members(db, user, project_id, employee_ids):
    from app.services import field_service as service
    from app.services import field_pending
    project = await service.require_project_manager(db, user, project_id)
    site = await service.project_site(db, project_id)
    rule = await db.get(AttendanceRule, site.rule_id) if site else None
    if not project.is_active:
        raise HTTPException(422, "项目已停用")
    staged = await field_pending.stage_members(db, user, project_id, employee_ids)
    if not site or not rule or not rule.is_active:
        return staged
    return await field_pending.activate_members(db, user, project_id)


async def preserve_legacy_day(db, employee_id, day):
    record = await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id == employee_id, AttendanceRecord.date == day))
    if not record or await db.scalar(select(FieldAttendanceSegment.id).where(FieldAttendanceSegment.attendance_record_id == record.id).limit(1)):
        return
    from app.models.attendance import AttendanceMonthSummary, MonthlySummaryStatus
    if await db.scalar(select(AttendanceMonthSummary.id).where(AttendanceMonthSummary.employee_id == employee_id, AttendanceMonthSummary.year == day.year, AttendanceMonthSummary.month == day.month, AttendanceMonthSummary.status == MonthlySummaryStatus.locked)):
        return
    row = (await db.execute(select(FieldMembership, FieldSite, FieldProject, AttendanceRule)
        .join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
        .join(FieldAssignmentDay, FieldAssignmentDay.assignment_id == FieldAssignment.id)
        .join(ShiftSchedule, ShiftSchedule.id == FieldAssignmentDay.shift_id)
        .join(AttendanceRule, AttendanceRule.id == ShiftSchedule.rule_id)
        .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
        .join(FieldProject, FieldProject.id == FieldSite.project_id)
        .where(FieldAssignmentDay.employee_id == employee_id, FieldAssignmentDay.work_date == day))).first()
    if not row:
        raise HTTPException(409, "当天已有未关联项目的历史考勤，请先核对后再打卡")
    membership, site, project, rule = row
    db.add(FieldAttendanceSegment(membership_id=membership.id, employee_id=employee_id, work_date=day,
        attendance_record_id=record.id, project_id=project.id, project_name=project.name,
        rule_snapshot=snapshot(rule), record_data=snapshot(record), needs_review=False))
    await db.flush()


async def end_membership(db, membership, now):
    membership.ended_at = max(utc(membership.started_at), utc(now))
    membership.active_employee_id = None
    assignment = await db.get(FieldAssignment, membership.assignment_id)
    assignment.status = "ended"
    assignment.end_date = now.astimezone(CST).date()
    # Snapshot only today's legacy attendance if this interval actually covered today.
    if utc(membership.started_at) <= now:
        await preserve_legacy_day(db, membership.employee_id, now.astimezone(CST).date())
    segments = (await db.scalars(select(FieldAttendanceSegment).where(FieldAttendanceSegment.membership_id == membership.id))).all()
    changed_months = {}
    for segment in segments:
        data = segment.record_data
        from app.services.attendance import AttendanceRecordService as attendance
        runtime = segment_runtime(segment.rule_snapshot, segment.work_date)
        missing = (runtime.get("check_in_required", True) and not data.get("clock_in_time")) or (runtime.get("check_out_required", True) and not data.get("clock_out_time"))
        if missing and not segment.needs_review:
            # Locked months keep their immutable result; review can be derived from the interval.
            from app.models.attendance import AttendanceMonthSummary, MonthlySummaryStatus
            locked = await db.scalar(select(AttendanceMonthSummary.id).where(AttendanceMonthSummary.employee_id == segment.employee_id, AttendanceMonthSummary.year == segment.work_date.year, AttendanceMonthSummary.month == segment.work_date.month, AttendanceMonthSummary.status == MonthlySummaryStatus.locked))
            if not locked:
                segment.needs_review = True
                await rollup(db, await db.get(AttendanceRecord, segment.attendance_record_id))
                changed_months[(segment.work_date.year, segment.work_date.month)] = segment.work_date
    await db.flush()
    for day in changed_months.values():
        await attendance._refresh_month_summary_for_date(db, membership.employee_id, day)


async def remove_or_transfer(db, user, assignment_id, project_id=None):
    from app.services import field_service as service
    assignment = await db.get(FieldAssignment, assignment_id)
    if not assignment:
        raise HTTPException(404, "项目人员记录不存在")
    site = await db.get(FieldSite, assignment.site_id)
    await service.require_project_manager(db, user, site.project_id)
    if project_id == site.project_id:
        raise HTTPException(422, "请选择其他项目")
    if project_id is not None:
        await service.require_project_manager(db, user, project_id)
        from app.services.field_roles import primary_regional_lead_id
        outgoing = await primary_regional_lead_id(db, site.project_id)
        incoming = await primary_regional_lead_id(db, project_id)
        if outgoing and incoming and outgoing != incoming:
            raise HTTPException(409, "跨区域项目调动须使用未来交接申请，经双方确认")
        raise HTTPException(409, "项目调动须填写未来生效日期并走交接申请")
    await lock_person(db, assignment.employee_id)
    membership = await db.scalar(select(FieldMembership).where(FieldMembership.assignment_id == assignment_id).execution_options(populate_existing=True))
    now = clock_now()
    if not membership or (membership.ended_at and utc(membership.ended_at) <= now):
        raise HTTPException(409, "该参与记录已经结束，请刷新项目")
    await end_membership(db, membership, now)
    if project_id is not None:
        await add_members(db, user, project_id, [assignment.employee_id])
    return {"id": assignment_id, "employee_id": assignment.employee_id, "project_id": project_id, "ended_at": now}


async def request_transfer(db, user, assignment_id, data):
    from app.services.field_roles import primary_regional_lead_id, require_project_manage
    assignment = await db.get(FieldAssignment, assignment_id)
    if not assignment:
        raise HTTPException(404, "原项目人员记录不存在")
    source_site = await db.get(FieldSite, assignment.site_id)
    if source_site.project_id == data.project_id:
        raise HTTPException(422, "请选择其他项目")
    await require_project_manage(db, user, data.project_id)
    outgoing_reviewer = await primary_regional_lead_id(db, source_site.project_id)
    incoming_reviewer = await primary_regional_lead_id(db, data.project_id)
    if not outgoing_reviewer or not incoming_reviewer:
        raise HTTPException(409, "原项目和接收项目均须先指定主责区域组长")
    destination = await db.scalar(select(FieldSite).where(
        FieldSite.project_id == data.project_id, FieldSite.is_active.is_(True)).limit(1))
    if not destination or not (await db.get(FieldProject, data.project_id)).is_active:
        raise HTTPException(422, "接收项目尚未配置有效的打卡现场")
    target_rule = await db.get(AttendanceRule, destination.rule_id)
    if not target_rule or not target_rule.is_active or target_rule.effective_date > data.effective_date:
        raise HTTPException(422, "接收项目考勤规则在交接日期尚未生效")
    if data.effective_date <= clock_now().astimezone(CST).date():
        raise HTTPException(422, "交接生效日期须晚于今天")
    await lock_person(db, assignment.employee_id)
    membership = await db.scalar(select(FieldMembership).where(FieldMembership.assignment_id == assignment_id))
    handoff_at = datetime.combine(data.effective_date, time.min, CST).astimezone(timezone.utc)
    if not membership or utc(membership.started_at) >= handoff_at or (membership.ended_at and utc(membership.ended_at) <= handoff_at):
        raise HTTPException(409, "原项目参与记录未覆盖交接日期")
    if await db.scalar(select(FieldTransferRequest.id).where(
            FieldTransferRequest.employee_id == assignment.employee_id,
            FieldTransferRequest.status == "pending").limit(1)):
        raise HTTPException(409, "该员工已有待确认的项目交接")
    # A published/recorded future project must be reconciled explicitly.
    overlapping = await db.scalar(select(FieldMembership.id).where(
        FieldMembership.employee_id == assignment.employee_id,
        FieldMembership.assignment_id != assignment_id,
        FieldMembership.started_at < handoff_at + timedelta(days=1),
        or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > handoff_at)).limit(1))
    if overlapping:
        raise HTTPException(409, "交接日期已有其他项目安排")
    now = clock_now()
    # Reserve both project owners at submission time. The *_approved_at fields
    # distinguish a reserved reviewer from an actual approval, so changing
    # regional assignments later cannot silently reroute a pending handoff.
    incoming_confirmed = user.id == incoming_reviewer
    request = FieldTransferRequest(employee_id=assignment.employee_id,
        pending_employee_id=assignment.employee_id,
        from_assignment_id=assignment_id, from_project_id=source_site.project_id,
        to_project_id=data.project_id, effective_date=data.effective_date,
        requested_by=user.id, requested_at=now,
        incoming_approved_by=incoming_reviewer,
        incoming_approved_at=now if incoming_confirmed else None,
        outgoing_approved_by=outgoing_reviewer,
        reason=data.reason.strip(), status="pending")
    db.add(request)
    await db.flush()
    from app.models.audit_log import AuditLog
    db.add(AuditLog(operator_id=user.id, operator_name=user.name, action="request_transfer",
        module="field", resource_type="FieldTransferRequest", resource_id=request.id,
        after_data=json.dumps({"employee_id": request.employee_id,
            "from_project_id": request.from_project_id, "to_project_id": request.to_project_id,
            "effective_date": request.effective_date.isoformat(), "reason": request.reason,
            "incoming_reviewer_id": incoming_reviewer,
            "outgoing_reviewer_id": outgoing_reviewer,
            "incoming_confirmed": incoming_confirmed}, ensure_ascii=False)))
    return transfer_view(request)


def transfer_view(request, names=None, can_review=False):
    names = names or {}
    review_stage = ("incoming" if request.incoming_approved_at is None else "outgoing") if request.status == "pending" else None
    return {"id": request.id, "employee_id": request.employee_id,
            "employee_name": names.get(request.employee_id, ""),
            "from_project_id": request.from_project_id,
            "from_project_name": names.get(("project", request.from_project_id), ""),
            "to_project_id": request.to_project_id,
            "to_project_name": names.get(("project", request.to_project_id), ""),
            "effective_date": request.effective_date, "status": request.status,
            "reason": request.reason, "review_note": request.review_note,
            "requested_by": request.requested_by,
            "incoming_approved_by": request.incoming_approved_by if request.incoming_approved_at else None,
            "outgoing_approved_by": request.outgoing_approved_by if request.outgoing_approved_at else None,
            "review_stage": review_stage,
            "can_review": can_review}


async def list_transfer_requests(db, user):
    from app.services.field_roles import role_names, role_in_project
    names = await role_names(db, user)
    requests = (await db.scalars(select(FieldTransferRequest).order_by(
        FieldTransferRequest.requested_at.desc(), FieldTransferRequest.id.desc()))).all()
    visible = []
    for row in requests:
        if not names & {"admin", "hr", "aftersales_manager", "regional_lead"} and user.id not in {
                row.employee_id, row.requested_by} and not await role_in_project(db, user, row.from_project_id) == "project_leader" and not await role_in_project(db, user, row.to_project_id) == "project_leader":
            continue
        visible.append(row)
    employee_ids = {x.employee_id for x in visible}
    project_ids = {x.from_project_id for x in visible} | {x.to_project_id for x in visible}
    labels = dict((await db.execute(select(Employee.id, Employee.name).where(Employee.id.in_(employee_ids)))).all())
    labels.update({("project", pid): name for pid, name in (await db.execute(
        select(FieldProject.id, FieldProject.name).where(FieldProject.id.in_(project_ids)))).all()})
    output = []
    for row in visible:
        reviewer_id = row.incoming_approved_by if row.incoming_approved_at is None else row.outgoing_approved_by
        can_review = row.status == "pending" and "regional_lead" in names and user.id == reviewer_id
        output.append(transfer_view(row, labels, can_review))
    return output


async def review_transfer(db, user, request_id, data):
    from app.services.field_roles import role_names
    request = await db.get(FieldTransferRequest, request_id)
    if not request:
        raise HTTPException(404, "项目交接申请不存在")
    if request.status != "pending":
        raise HTTPException(409, "交接申请已处理")
    stage = "incoming" if request.incoming_approved_at is None else "outgoing"
    reviewer_id = request.incoming_approved_by if stage == "incoming" else request.outgoing_approved_by
    if not reviewer_id or user.id != reviewer_id or "regional_lead" not in await role_names(db, user):
        raise HTTPException(403, "须由该阶段指定的主责区域组长确认交接")
    if (stage == "outgoing" and request.incoming_approved_by != request.outgoing_approved_by
            and user.id == request.incoming_approved_by):
        raise HTTPException(403, "跨区域交接须由原项目方的另一位负责人确认")
    await lock_person(db, request.employee_id)
    now = clock_now()
    if stage == "incoming":
        if data.approve:
            request.incoming_approved_at = now
        else:
            request.status = "rejected"
            request.pending_employee_id = None
        request.review_note = data.note.strip()
        from app.models.audit_log import AuditLog
        db.add(AuditLog(operator_id=user.id, operator_name=user.name,
            action="approve_incoming_transfer" if data.approve else "reject_incoming_transfer",
            module="field", resource_type="FieldTransferRequest", resource_id=request.id,
            after_data=json.dumps({"status": request.status, "stage": "incoming",
                "reviewer_id": user.id, "note": request.review_note}, ensure_ascii=False)))
        await db.flush()
        return transfer_view(request)
    if data.approve:
        handoff_at = datetime.combine(request.effective_date, time.min, CST).astimezone(timezone.utc)
        if handoff_at <= now:
            raise HTTPException(409, "交接日期已过，请重新申请")
        # A future punch, including a manually added record, cannot be moved
        # to another project without a separate attendance correction.
        recorded = await db.scalar(select(AttendanceRecord.id).where(
            AttendanceRecord.employee_id == request.employee_id,
            AttendanceRecord.date >= request.effective_date).limit(1))
        if recorded:
            raise HTTPException(409, "交接日期后已有考勤记录，请先核对后再确认交接")
        source = await db.scalar(select(FieldMembership).where(
            FieldMembership.assignment_id == request.from_assignment_id))
        if not source or utc(source.started_at) >= handoff_at or (source.ended_at and utc(source.ended_at) <= handoff_at):
            raise HTTPException(409, "原项目参与记录已变化，请重新申请")
        other = await db.scalar(select(FieldMembership.id).where(
            FieldMembership.employee_id == request.employee_id,
            FieldMembership.assignment_id != request.from_assignment_id,
            or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > handoff_at)).limit(1))
        if other:
            raise HTTPException(409, "交接日期已有其他项目安排")
        site = await db.scalar(select(FieldSite).where(FieldSite.project_id == request.to_project_id,
            FieldSite.is_active.is_(True)).limit(1))
        if not site:
            raise HTTPException(409, "接收项目打卡现场已失效")
        rule = await db.get(AttendanceRule, site.rule_id)
        if not rule or not rule.is_active or rule.effective_date > request.effective_date:
            raise HTTPException(409, "接收项目考勤规则已失效，请重新申请交接")
        # Legacy field assignments materialize their dates as global shifts.
        # Clear only future unworked rows from the outgoing assignment. The
        # confirmed handoff and the audit entry below explain the change.
        future_days = (await db.scalars(select(FieldAssignmentDay).where(
            FieldAssignmentDay.assignment_id == request.from_assignment_id,
            FieldAssignmentDay.work_date >= request.effective_date))).all()
        shift_ids = [row.shift_id for row in future_days]
        for row in future_days:
            await db.delete(row)
        await db.flush()
        for shift_id in shift_ids:
            shift = await db.get(ShiftSchedule, shift_id)
            if shift:
                await db.delete(shift)
        await db.flush()
        source.ended_at = handoff_at
        source.active_employee_id = None
        old_assignment = await db.get(FieldAssignment, request.from_assignment_id)
        old_assignment.end_date = request.effective_date - timedelta(days=1)
        target = FieldAssignment(site_id=site.id, employee_id=request.employee_id,
            start_date=request.effective_date, end_date=date(9999, 12, 30),
            created_by=request.requested_by, status="active",
            description=f"跨项目交接申请 #{request.id}")
        db.add(target)
        await db.flush()
        db.add(FieldMembership(assignment_id=target.id, employee_id=request.employee_id,
            started_at=handoff_at, ended_at=None, active_employee_id=request.employee_id))
        await db.flush()
        request.status = "approved"
        from app.services.rotation import reconcile_transfer_schedule
        await reconcile_transfer_schedule(db, user, request.employee_id,
            request.from_project_id, request.to_project_id, request.effective_date,
            request.reason)
    else:
        request.status = "rejected"
    request.pending_employee_id = None
    if data.approve:
        request.outgoing_approved_at = now
    request.review_note = data.note.strip()
    from app.models.audit_log import AuditLog
    db.add(AuditLog(operator_id=user.id, operator_name=user.name,
        action="approve_transfer" if data.approve else "reject_transfer",
        module="field", resource_type="FieldTransferRequest", resource_id=request.id,
        after_data=json.dumps({"status": request.status,
            "outgoing_approved_by": user.id, "note": request.review_note,
            "future_source_shifts_cleared": len(future_days) if data.approve else 0}, ensure_ascii=False)))
    await db.flush()
    return transfer_view(request)


async def stop_person(db, employee_id):
    await lock_person(db, employee_id)
    now = clock_now()
    memberships = (await db.scalars(select(FieldMembership).where(FieldMembership.employee_id == employee_id, or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > now)))).all()
    for membership in memberships:
        await end_membership(db, membership, now)


async def import_members(db, user, project_id, content, filename, preview=True):
    from app.services.field_roster import parse_file
    from app.services.field_service import require_project_manager
    await require_project_manager(db, user, project_id)
    rows, ids = [], set()
    for line, item in parse_file(content, filename):
        phone = re.sub(r"[\s-]", "", item.get("phone", "")).removeprefix("+86")
        person = await db.scalar(select(Employee).where(Employee.phone == phone))
        error = ""
        if not person or person.name != item.get("name"):
            error = "姓名、手机号与花名册不匹配，请先维护花名册"
        elif not person.is_active or person.status not in {"在职", "试用"}:
            error = "人员已离职或账号停用"
        elif person.id in ids:
            error = "文件内人员重复"
        else:
            active = await current(db, person.id)
            if active:
                error = f"已在{active[3].name}，请使用调动功能"
            ids.add(person.id)
        rows.append({"row": line, "name": item.get("name"), "phone": phone, "error": error})
    failed = sum(bool(row["error"]) for row in rows)
    if not preview:
        if failed or not ids:
            raise HTTPException(422, "请修正预览中的全部问题后再导入")
        await add_members(db, user, project_id, list(ids))
    return {"preview": preview, "rows": rows, "success": len(rows) - failed, "failed": failed}


async def punch_context(db, employee_id, now=None):
    from app.services.attendance import AttendanceRecordService as attendance
    now = now or clock_now()
    person = await db.get(Employee, employee_id)
    row = await current(db, employee_id, now)
    if not person or not person.is_active or person.status not in {"在职", "试用"} or not row:
        return None
    membership, assignment, site, project = row
    rule = await db.get(AttendanceRule, site.rule_id)
    if not site.is_active or not project.is_active or not rule or not rule.is_active or rule.effective_date > now.astimezone(CST).date():
        return None
    day = now.astimezone(CST).date()
    if (rule.extra_config or {}).get("rule_type") == "rotation":
        cutoff = attendance._parse_hm((rule.extra_config or {}).get("free_day_start_time")) or time(5)
        if now.astimezone(CST).time() < cutoff:
            day -= timedelta(days=1)
    # Continue an overnight segment only within the same participation interval.
    previous = await db.scalar(select(FieldAttendanceSegment).where(FieldAttendanceSegment.membership_id == membership.id, FieldAttendanceSegment.work_date == day - timedelta(days=1)))
    if previous:
        runtime = segment_runtime(previous.rule_snapshot, previous.work_date)
        start, end = runtime.get("expected_in"), runtime.get("expected_out")
        cutoff = attendance._parse_hm((runtime.get("extra") or {}).get("free_day_start_time")) if runtime.get("rule_type") == "free" else None
        if (start and end and end <= start and now.astimezone(CST) <= datetime.combine(day, end, CST) + timedelta(hours=4)) or (cutoff and now.astimezone(CST).time() < cutoff):
            day = previous.work_date
    segment = await db.scalar(select(FieldAttendanceSegment).where(FieldAttendanceSegment.membership_id == membership.id, FieldAttendanceSegment.work_date == day))
    legacy_close_only = False
    if (rule.extra_config or {}).get("rule_type") == "rotation":
        from app.services.field_pending import rotation_punch_ready
        if not await rotation_punch_ready(db, employee_id, project.id, day):
            # A rule change cannot strand an already started shift. The saved
            # segment keeps its original rule, but only its outstanding clock-out
            # may proceed until the first rotation schedule is published.
            punches = segment.record_data if segment else {}
            if not punches.get("clock_in_time") or punches.get("clock_out_time"):
                return None
            legacy_close_only = True
    if not segment:
        legacy_rule_id = await db.scalar(select(ShiftSchedule.rule_id).join(FieldAssignmentDay, FieldAssignmentDay.shift_id == ShiftSchedule.id)
            .where(FieldAssignmentDay.assignment_id == assignment.id, FieldAssignmentDay.work_date == day))
        if legacy_rule_id:
            rule = await db.get(AttendanceRule, legacy_rule_id)
    frozen = segment.rule_snapshot if segment else snapshot(rule)
    runtime = segment_runtime(frozen, day, now.astimezone(CST))
    attendance._apply_rule_runtime_people(runtime, employee_id)
    from app.services.rotation import apply_runtime
    await apply_runtime(db, runtime, employee_id, day, project.id)
    runtime.update(employee=person, field_segment=True)
    revision = hashlib.sha256(json.dumps([membership.id, day.isoformat(), frozen, runtime.get("rotation")], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    record = restore(AttendanceRecord, segment.record_data) if segment else AttendanceRecord(employee_id=employee_id, date=day,
        status=AttendanceStatus.normal, source=ClockSource.app, correction_status=CorrectionStatus.none,
        created_at=now, is_mock_location=False)
    return dict(membership=membership, project=project, day=day, segment=segment, rule_snapshot=frozen,
                runtime=runtime, record=record, revision=revision, now=now,
                legacy_close_only=legacy_close_only)


async def prepare_punch(db, employee_id, data, *, phase):
    person = await lock_person(db, employee_id)
    if not person or not person.is_active or person.status not in {"在职", "试用"}:
        raise HTTPException(403, "账号已锁定")
    context = await punch_context(db, employee_id)
    if not context:
        raise HTTPException(409, "尚未分配有效项目、考勤规则或首次发布的个人排班，请联系项目负责人")
    if context["legacy_close_only"] and phase != "clock_out":
        raise HTTPException(409, "请先发布个人排班；当前只允许完成已有班次的下班打卡")
    if data.project_context != context["revision"]:
        raise HTTPException(409, "项目或考勤规则已更新，请刷新后重新打卡")
    await preserve_legacy_day(db, employee_id, context["day"])
    # Legacy preservation may have created this segment.
    context = await punch_context(db, employee_id, context["now"])
    if not context or (context["legacy_close_only"] and phase != "clock_out"):
        raise HTTPException(409, "项目或排班已变化，请刷新后重新打卡")
    daily = await db.scalar(select(AttendanceRecord).where(AttendanceRecord.employee_id == employee_id, AttendanceRecord.date == context["day"]))
    if not daily:
        daily = AttendanceRecord(employee_id=employee_id, date=context["day"])
        db.add(daily)
        await db.flush()
    context["daily"] = daily
    context["record"].id = daily.id
    if not context["segment"]:
        project = context["project"]
        frozen = {**context["rule_snapshot"], "_runtime": jsonable_encoder({k: v for k, v in context["runtime"].items() if k not in {"rule", "employee", "shift", "field_segment"}})}
        segment = FieldAttendanceSegment(membership_id=context["membership"].id, employee_id=employee_id,
            work_date=context["day"], attendance_record_id=daily.id, project_id=project.id, project_name=project.name,
            rule_snapshot=frozen, record_data=snapshot(context["record"]), needs_review=False)
        db.add(segment)
        context["segment"] = segment
        await db.flush()
    return context


async def finish_punch(db, context, punch_id):
    ids = context["segment"].record_data.get("punch_record_ids", [])
    context["segment"].record_data = {**snapshot(context["record"]), "punch_record_ids": [*ids, punch_id]}
    await db.flush()
    await rollup(db, context["daily"])
    await db.flush()
    return context["daily"]


async def rollup(db, record):
    segments = list((await db.scalars(select(FieldAttendanceSegment).where(FieldAttendanceSegment.attendance_record_id == record.id).order_by(FieldAttendanceSegment.id))).all())
    if not segments:
        return False
    pieces = [restore(AttendanceRecord, segment.record_data) for segment in segments]
    for phase, choose in (("clock_in", min), ("clock_out", max)):
        available = [piece for piece in pieces if getattr(piece, phase + "_time")]
        chosen = choose(available, key=lambda piece: getattr(piece, phase + "_time")) if available else None
        for column in AttendanceRecord.__table__.columns:
            if column.name.startswith(phase + "_"):
                setattr(record, column.name, getattr(chosen, column.name) if chosen else None)
    record.work_hours = sum((piece.work_hours or Decimal(0) for piece in pieces), Decimal(0))
    record.overtime_hours = sum((piece.overtime_hours or Decimal(0) for piece in pieces), Decimal(0))
    record.is_mock_location = any(piece.is_mock_location for piece in pieces)
    record.status = next((piece.status for piece in pieces if piece.status != AttendanceStatus.normal), AttendanceStatus.normal)
    notes = []
    for segment, piece in zip(segments, pieces):
        if segment.needs_review:
            record.status = AttendanceStatus.missed_clock
        issue = "待核对：项目结束时缺卡" if segment.needs_review else piece.anomaly_type
        if issue:
            notes.append(f"{segment.project_name}：{issue}")
    record.anomaly_type = "; ".join(notes)[:200] or None
    record.project_segments = [segment_view(segment) for segment in segments]
    pending_rotation = any(((s.rule_snapshot.get("_runtime") or {}).get("rotation") or {}).get("kind") == "pending" for s in segments)
    record.display_status = "待确认" if pending_rotation else ("待核对" if any(segment.needs_review for segment in segments) else ("异常" if notes else "正常"))
    return True


def segment_view(segment):
    from app.services.attendance import AttendanceRecordService
    rule = restore(AttendanceRule, segment.rule_snapshot)
    required = AttendanceRecordService._free_required_hours(rule)
    rotation = (segment.rule_snapshot.get("_runtime") or {}).get("rotation")
    if rotation:
        required = Decimal(str(rotation.get("required_hours") or 0))
    return {**segment.record_data, "rotation": rotation, "segment_id": segment.id, "project_id": segment.project_id,
            "project_name": segment.project_name, "rule_name": segment.rule_snapshot.get("name"),
            "needs_review": segment.needs_review, "required_work_hours": str(required) if required is not None else None,
            "display_status": "待核对" if segment.needs_review else segment.record_data.get("status")}
