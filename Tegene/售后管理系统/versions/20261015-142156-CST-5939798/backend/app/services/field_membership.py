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
from app.models.field_service import FieldAssignment, FieldAssignmentDay, FieldMembership, FieldAttendanceSegment, FieldSite, FieldProject
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


async def managed_field_person(db, employee_id, person=None):
    if person is None:
        from app.services.attendance import AttendanceRecordService
        person = await AttendanceRecordService._get_employee(db, employee_id)
    if not isinstance(person, Employee):
        return False
    if person.is_field_staff:
        return True
    return (await db.execute(select(FieldMembership.id).where(FieldMembership.employee_id == employee_id).limit(1))).scalar_one_or_none() is not None


async def day_runtime(db, person, day):
    """Unpunched days still use project rules when generating attendance summaries."""
    from app.services.attendance import AttendanceRecordService as attendance
    start = datetime.combine(day, time.min, CST).astimezone(timezone.utc)
    row = (await db.execute(select(FieldMembership, FieldSite)
        .join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
        .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
        .where(FieldMembership.employee_id == person.id, FieldMembership.started_at < start + timedelta(days=1),
               or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > start),
               or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > FieldMembership.started_at))
        .order_by(FieldMembership.started_at.desc(), FieldMembership.id.desc()).limit(1))).first()
    if row:
        membership, site = row
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
    runtime = attendance._build_runtime(rule, shift, day)
    attendance._apply_rule_runtime_people(runtime, person.id)
    runtime.update(employee=person, shift=shift, project_assigned=True)
    return runtime


async def add_members(db, user, project_id, employee_ids):
    from app.services import field_service as service
    project = await service.require_project_manager(db, user, project_id)
    site = await service.project_site(db, project_id)
    rule = await db.get(AttendanceRule, site.rule_id) if site else None
    if not project.is_active or not site or not rule or not rule.is_active or rule.effective_date > clock_now().astimezone(CST).date():
        raise HTTPException(422, "请先为项目配置已生效的考勤规则")
    result = []
    for employee_id in sorted(set(employee_ids)):
        person = await lock_person(db, employee_id)
        now = clock_now()
        if not person or not person.is_active or person.status not in {"在职", "试用"}:
            raise HTTPException(422, "只能添加花名册中在职且启用的人员")
        existing = await current(db, employee_id, now)
        if existing:
            raise HTTPException(409, f"{person.name}已在{existing[3].name}，请从原项目使用调动功能")
        planned = await db.scalar(select(FieldMembership.id).where(FieldMembership.employee_id == employee_id, FieldMembership.started_at > now, or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > FieldMembership.started_at)).limit(1))
        if planned:
            raise HTTPException(409, f"{person.name}还有未来项目安排，请先移出该安排")
        assignment = FieldAssignment(site_id=site.id, employee_id=employee_id, start_date=now.astimezone(CST).date(),
                                     end_date=date(9999, 12, 30), created_by=user.id, status="active")
        db.add(assignment)
        await db.flush()
        membership = FieldMembership(assignment_id=assignment.id, employee_id=employee_id,
                                      started_at=now, active_employee_id=employee_id)
        db.add(membership)
        await db.flush()
        result.append({"id": assignment.id, "employee_id": employee_id, "project_id": project_id, "started_at": now})
    return {"members": result}


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
    await lock_person(db, assignment.employee_id)
    membership = await db.scalar(select(FieldMembership).where(FieldMembership.assignment_id == assignment_id).execution_options(populate_existing=True))
    now = clock_now()
    if not membership or (membership.ended_at and utc(membership.ended_at) <= now):
        raise HTTPException(409, "该参与记录已经结束，请刷新项目")
    await end_membership(db, membership, now)
    if project_id is not None:
        await add_members(db, user, project_id, [assignment.employee_id])
    return {"id": assignment_id, "employee_id": assignment.employee_id, "project_id": project_id, "ended_at": now}


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
    # Continue an overnight segment only within the same participation interval.
    previous = await db.scalar(select(FieldAttendanceSegment).where(FieldAttendanceSegment.membership_id == membership.id, FieldAttendanceSegment.work_date == day - timedelta(days=1)))
    if previous:
        runtime = segment_runtime(previous.rule_snapshot, previous.work_date)
        start, end = runtime.get("expected_in"), runtime.get("expected_out")
        cutoff = attendance._parse_hm((runtime.get("extra") or {}).get("free_day_start_time")) if runtime.get("rule_type") == "free" else None
        if (start and end and end <= start and now.astimezone(CST) <= datetime.combine(day, end, CST) + timedelta(hours=4)) or (cutoff and now.astimezone(CST).time() < cutoff):
            day = previous.work_date
    segment = await db.scalar(select(FieldAttendanceSegment).where(FieldAttendanceSegment.membership_id == membership.id, FieldAttendanceSegment.work_date == day))
    if not segment:
        legacy_rule_id = await db.scalar(select(ShiftSchedule.rule_id).join(FieldAssignmentDay, FieldAssignmentDay.shift_id == ShiftSchedule.id)
            .where(FieldAssignmentDay.assignment_id == assignment.id, FieldAssignmentDay.work_date == day))
        if legacy_rule_id:
            rule = await db.get(AttendanceRule, legacy_rule_id)
    frozen = segment.rule_snapshot if segment else snapshot(rule)
    runtime = segment_runtime(frozen, day, now.astimezone(CST))
    attendance._apply_rule_runtime_people(runtime, employee_id)
    runtime.update(employee=person, field_segment=True)
    revision = hashlib.sha256(json.dumps([membership.id, day.isoformat(), frozen], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    record = restore(AttendanceRecord, segment.record_data) if segment else AttendanceRecord(employee_id=employee_id, date=day,
        status=AttendanceStatus.normal, source=ClockSource.app, correction_status=CorrectionStatus.none,
        created_at=now, is_mock_location=False)
    return dict(membership=membership, project=project, day=day, segment=segment, rule_snapshot=frozen,
                runtime=runtime, record=record, revision=revision, now=now)


async def prepare_punch(db, employee_id, data):
    person = await lock_person(db, employee_id)
    if not person or not person.is_active or person.status not in {"在职", "试用"}:
        raise HTTPException(403, "账号已锁定")
    context = await punch_context(db, employee_id)
    if not context:
        raise HTTPException(409, "尚未分配有效项目或考勤规则，请联系项目负责人")
    if data.project_context != context["revision"]:
        raise HTTPException(409, "项目或考勤规则已更新，请刷新后重新打卡")
    await preserve_legacy_day(db, employee_id, context["day"])
    # Legacy preservation may have created this segment.
    context = await punch_context(db, employee_id, context["now"])
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
    record.display_status = "待核对" if any(segment.needs_review for segment in segments) else ("异常" if notes else "正常")
    return True


def segment_view(segment):
    from app.services.attendance import AttendanceRecordService
    rule = restore(AttendanceRule, segment.rule_snapshot)
    required = AttendanceRecordService._free_required_hours(rule)
    return {**segment.record_data, "segment_id": segment.id, "project_id": segment.project_id,
            "project_name": segment.project_name, "rule_name": segment.rule_snapshot.get("name"),
            "needs_review": segment.needs_review, "required_work_hours": str(required) if required is not None else None,
            "display_status": "待核对" if segment.needs_review else segment.record_data.get("status")}
