"""Project-scoped rotation planning, approvals and rest accounting.

Days are published explicitly. Merely reading a calendar never creates attendance
or credits. Account/employee locks serialize approval, reversal and spending.
"""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, or_

from app.models.attendance import AttendanceRule, AttendanceRecord, AttendanceMonthSummary, MonthlySummaryStatus, AttendanceEffect
from app.models.employee import Employee
from app.models.audit_log import AuditLog
from app.models.field_service import FieldAttendanceSegment, FieldProject
from app.models.employee_role import EmployeeRole
from app.models.rotation import RotationDay, RotationClaim, RotationAccount, RotationLedger
from app.schemas.notification import NotificationCreate
from app.services import field_service as field, field_roles
from app.services.notification import NotificationService
from app.services.field_membership import lock_person, snapshot, restore, utc, CST

LABELS = {"work": "上班", "rest": "正常轮休", "leave": "调休", "half_leave": "半天调休", "pending": "待确认"}
REST_POLICIES = {"comp_time", "rest", "rest_time", "time_off", "lieu", "adjust_rest"}
SHIFT_LABEL_KEY = "_rotation_display_shift_label"


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
    from app.services.field_membership import project_on
    found = await project_on(db, employee_id, day)
    return found[0].id if found and found[3].id == project_id else None


async def personal_cycle_start(db, employee_id, proposed):
    """A move to a different project never restarts an established personal cycle."""
    anchor = await db.scalar(select(RotationDay.cycle_start).where(RotationDay.employee_id == employee_id)
        .order_by(RotationDay.work_date, RotationDay.id).limit(1))
    return anchor or proposed


async def day_row(db, employee_id, day, project_id=None):
    row = await db.scalar(select(RotationDay).where(RotationDay.employee_id == employee_id, RotationDay.work_date == day))
    return row if row and (project_id is None or row.project_id == project_id) else None


def day_view(row):
    return {"id": row.id, "employee_id": row.employee_id, "project_id": row.project_id, "date": row.work_date,
        "cycle_start": row.cycle_start, "original_kind": row.original_kind, "kind": row.kind,
        "shift_label": day_shift_label(row),
        "label": LABELS[row.kind], "revision": row.revision, "reason": row.reason, "updated_by": row.updated_by,
        "updated_at": row.updated_at, "rule_name": row.rule_snapshot.get("name"),
        "required_hours": required_hours(row) / (2 if row.kind == "half_leave" else 1) if row.kind in {"work", "half_leave"} else 0}


def day_shift_label(row):
    """A display-only label kept beside the immutable rule snapshot.

    Old published rows have no label. `restore(AttendanceRule, ...)` ignores this
    extra JSON key, so it cannot alter punch windows or required hours.
    """
    return (row.rule_snapshot or {}).get(SHIFT_LABEL_KEY) if row.kind in {"work", "half_leave"} else None


def rule_with_shift(rule, label):
    return {**snapshot(rule), SHIFT_LABEL_KEY: label}


def update_shift_label(row, label):
    row.rule_snapshot = {**row.rule_snapshot, SHIFT_LABEL_KEY: label}


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
    project = await field_roles.require_project_schedule_manager(db, user, project_id)
    site = await field.project_site(db, project_id)
    rule = await db.get(AttendanceRule, site.rule_id) if site else None
    if not project.is_active or not rule or not rule.is_active or not is_rotation(rule):
        raise HTTPException(422, "请先为项目配置已启用的现场轮休规则")
    return rule


async def preview(db, user, project_id, data):
    rule = await project_rule(db, user, project_id)
    if data.start_date < max(today(), rule.effective_date):
        raise HTTPException(422, "周期排班须从今天或规则生效日之后开始，历史调整请使用调班申请")
    edits = {e.date: e for e in data.edits}
    person_edits = {(e.employee_id, e.date): e for e in data.person_edits}
    result = []
    for eid in sorted(set(data.employee_ids)):
        await field_roles.require_project_schedule_manager(db, user, project_id, eid)
        anchor = await personal_cycle_start(db, eid, data.cycle_start)
        own_days = 0
        for offset in range((data.end_date - data.start_date).days + 1):
            day = data.start_date + timedelta(days=offset)
            if not await member_on(db, eid, project_id, day):
                if not data.member_days_only or (eid, day) in person_edits:
                    raise HTTPException(422, "所选日期内人员不属于该项目，请核对参与区间")
                continue
            own_days += 1
            row = await day_row(db, eid, day)
            original = "rest" if (day - anchor).days % 7 == 6 else "work"
            edit = person_edits.get((eid, day)) or edits.get(day)
            kind = edit.kind if edit else original
            if edit and "shift_label" in edit.model_fields_set:
                shift_label = edit.shift_label
            else:
                shift_label = day_shift_label(row) if row else None
            result.append({"employee_id": eid, "date": day, "cycle_start": anchor,
                "original_kind": row.original_kind if row else original, "kind": kind,
                "shift_label": shift_label if kind == "work" else None,
                "expected_revision": row.revision if row else 0})
        if not own_days:
            raise HTTPException(422, "所选人员在这段日期内没有该项目的参与日期")
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
        if row:
            raise HTTPException(409, "日期已有已发布排班，请提交调班申请并完成逐级审核")
        if await db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.employee_id == eid, AttendanceRecord.date == day)):
            raise HTTPException(409, "当天已有打卡，请从未来日期发布；已有排班通过带原因的日期调整修改")
        before = None
        row = RotationDay(employee_id=eid, project_id=project_id, work_date=day, cycle_start=item["cycle_start"],
            original_kind=item["original_kind"], kind=item["kind"], rule_snapshot=rule_with_shift(rule, item["shift_label"]),
            revision=1, reason=data.reason, updated_by=user.id)
        db.add(row)
        await db.flush()
        audit(db, user, "rotation_publish", before, day_view(row))
    project = await db.get(FieldProject, project_id)
    for eid in sorted({item["employee_id"] for item in plan["rows"]}):
        dates = [item["date"] for item in plan["rows"] if item["employee_id"] == eid]
        await NotificationService.create(db, NotificationCreate(
            recipient_id=eid, sender_id=user.id, notif_type="system",
            title="个人排班已发布",
            content=f"{project.name}：{min(dates)} 至 {max(dates)} 已发布 {len(dates)} 天排班，请查看个人排班日历。",
            ref_type="rotation_schedule", ref_id=project_id))
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


async def manual_day(db, user, project_id, employee_id, day, kind, reason, shift_label=None):
    """Explicit confirmation of an unpublished date, never a read-time fallback."""
    if await day_row(db, employee_id, day):
        raise HTTPException(409, "当天已有其他项目安排或排班已变化，请刷新")
    rule = await project_rule(db, user, project_id)
    if rule.effective_date > day or not await member_on(db, employee_id, project_id, day):
        raise HTTPException(422, "日期不在规则生效或项目参与区间内")
    anchor = await personal_cycle_start(db, employee_id, day-timedelta(days=6) if kind == "rest" else day)
    original = "rest" if (day - anchor).days % 7 == 6 else "work"
    row = RotationDay(employee_id=employee_id, project_id=project_id, work_date=day,
        cycle_start=anchor, original_kind=original, kind=kind,
        rule_snapshot=rule_with_shift(rule, shift_label if kind == "work" else None),
        revision=0, reason=reason, updated_by=user.id)
    db.add(row)
    await db.flush()
    return row


async def adjust(db, user, project_id, data):
    from app.schemas.rotation import ClaimDay, ClaimInput
    edits = [(d.employee_id, d) for d in data.changes] if data.changes else [
        (eid, d) for eid in sorted(set(data.employee_ids)) for d in data.days]
    claims = []
    for eid in sorted({eid for eid, _ in edits}):
        await field_roles.require_project_schedule_manager(db, user, project_id, eid)
        mine = [d for person, d in edits if person == eid]
        for edit in mine:
            row = await day_row(db, eid, edit.date)
            if edit.expected_revision is not None and edit.expected_revision != (row.revision if row else 0):
                raise HTTPException(409, "排班已变化，请刷新后重新调整")
        claims.append(await create_claim(db, user, ClaimInput(project_id=project_id, employee_id=eid,
            kind="schedule", days=[ClaimDay(date=d.date, kind=d.kind, project_id=project_id,
                **({"shift_label": d.shift_label} if "shift_label" in d.model_fields_set else {})) for d in mine],
            reason=data.reason)))
    return {"claims": claims, "count": len(claims), "claim": claims[0] if len(claims) == 1 else None}


async def reconcile_transfer_schedule(db, user, employee_id, source_project_id, target_project_id,
                                      effective_date, reason):
    """Queue approval for published future dates after a two-party project transfer.

    The transfer itself never rewrites published calendar history. Until the
    project approval chain finishes, target-project punch runtime is pending.
    """
    from app.schemas.rotation import ClaimDay, ClaimInput
    rows = (await db.scalars(select(RotationDay).where(RotationDay.employee_id == employee_id,
        RotationDay.project_id == source_project_id, RotationDay.work_date >= effective_date)
        .order_by(RotationDay.work_date))).all()
    claims = []
    for start in range(0, len(rows), 62):
        batch = rows[start:start + 62]
        for row in batch:
            if row.kind in {"leave", "half_leave"}:
                raise HTTPException(409, "未来日期已有批准调休，须先撤销再办理跨项目交接")
            if await db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.date == row.work_date)):
                raise HTTPException(409, "未来日期已有打卡，不能自动变更项目归属")
        if batch:
            data = ClaimInput(project_id=target_project_id, employee_id=employee_id,
                kind="schedule", days=[ClaimDay(date=row.work_date, kind=row.kind,
                    project_id=target_project_id) for row in batch], reason=reason)
            claims.append(await create_claim(db, user, data, require_actor_scope=False))
    return claims


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


async def schedule_steps(db, employee_id, snapshots, submitted_by=None):
    """Freeze the project approval chain at request time, including transfer source."""
    self_review_ids = {employee_id, submitted_by}
    project_ids = sorted({pid for item in snapshots
        for pid in (item["project_id"], item.get("before_project_id")) if pid is not None})
    steps = []
    for pid in project_ids:
        project = await db.get(FieldProject, pid)
        if not project or not project.manager_id:
            raise HTTPException(422, "涉及项目尚未任命项目组长")
        assigned_regionals = await field_roles.regional_assignments(db, project_id=pid)
        if not assigned_regionals:
            raise HTTPException(422, "涉及项目尚未分配区域组长")
        own_lead = project.manager_id in self_review_ids
        steps.append({"role": "project_leader", "project_id": pid,
            "status": "skipped" if own_lead else "pending",
            **({"skip_reason": "排班员工或代提交人不得审批本人提交的排班"} if own_lead else {})})
        eligible_regionals = [assignment.employee_id for assignment in assigned_regionals
            if "regional_lead" in await field_roles.role_names(db, assignment.employee_id)]
        if not eligible_regionals:
            raise HTTPException(422, "涉及项目没有在职的区域组长")
        if not set(eligible_regionals) - self_review_ids:
            raise HTTPException(422, "区域组长不能审批本人或代提交的排班，请售后经理先为项目分配独立的协管区域组长")
        steps.append({"role": "regional_lead", "project_id": pid, "status": "pending"})
    manager_claimant = False
    for eid in self_review_ids:
        if eid is not None and "aftersales_manager" in await field_roles.role_names(db, eid):
            manager_claimant = True
            break
    other_managers = set(await field_roles.aftersales_manager_ids(db)) - self_review_ids
    final_role = "aftersales_manager"
    if manager_claimant or not other_managers:
        admins = set((await db.scalars(select(Employee.id).outerjoin(EmployeeRole,
            (EmployeeRole.employee_id == Employee.id) & (EmployeeRole.role_name == "admin") &
            (EmployeeRole.is_active.is_(True))).where(Employee.is_active.is_(True),
            or_(Employee.is_superuser.is_(True), EmployeeRole.id.is_not(None))))).all())
        if not admins - self_review_ids:
            raise HTTPException(422, "没有可独立终审的售后经理或管理员，请先配置审核人")
        final_role = "admin"
    steps.append({"role": final_role, "project_id": None, "status": "pending"})
    return steps


def next_review_index(steps, start=0):
    return next((i for i in range(start, len(steps)) if steps[i]["status"] == "pending"), len(steps))


async def schedule_workflow(db, claim):
    workflow = (claim.facts or {}).get("workflow")
    if workflow or claim.kind != "schedule" or claim.status != "pending":
        return workflow
    # Pending claims written by the previous version had one direct approver.
    # Derive the new chain on read and save it on the first review.
    legacy_days = [{**item, "project_id": item.get("project_id", claim.project_id),
        "before_project_id": item.get("before_project_id", claim.project_id)} for item in claim.days]
    steps = await schedule_steps(db, claim.employee_id, legacy_days, claim.submitted_by)
    return {"steps": steps, "next": next_review_index(steps)}


async def can_review_step(db, user, claim, step):
    if user.id in {claim.employee_id, claim.submitted_by}:
        return False
    role, project_id = step["role"], step["project_id"]
    if role == "project_leader":
        return await field_roles.is_project_leader(db, user, project_id)
    if role == "regional_lead":
        return "regional_lead" in await field_roles.role_names(db, user) and bool(
            await field_roles.regional_assignments(db, project_id=project_id, employee_id=user.id))
    if role == "admin":
        return "admin" in await field_roles.role_names(db, user)
    # An employee may be appointed after submitting a request. The frozen
    # workflow must not let a second aftersales manager approve that employee's
    # now-manager application; an administrator handles it with a reason.
    if await field_roles.is_aftersales_manager(db, claim.employee_id):
        return "admin" in await field_roles.role_names(db, user)
    return bool(await field_roles.role_names(db, user) & {"aftersales_manager", "admin"})


async def require_review_step(db, user, claim, step, note):
    if not await can_review_step(db, user, claim, step):
        raise HTTPException(403, "当前审批节点无权处理，且不能审批本人申请")
    if step["role"] in {"aftersales_manager", "admin"}:
        await field_roles.require_aftersales_approver(db, user, note)


async def require_claim_approver(db, user, claim, note):
    role = await field_roles.require_aftersales_approver(db, user, note)
    if await field_roles.is_aftersales_manager(db, claim.employee_id) and role != "admin":
        raise HTTPException(403, "售后经理本人申请须由系统管理员填写原因代审")
    return role


async def create_claim(db, user, data, *, require_actor_scope=True):
    eid = data.employee_id or user.id
    if data.kind in {"credit", "leave"} and eid != user.id:
        raise HTTPException(403, "攒休和调休须由员工本人提交")
    day_projects = {item.project_id or data.project_id for item in data.days}
    if require_actor_scope and eid != user.id:
        for pid in day_projects:
            await field_roles.require_project_schedule_manager(db, user, pid, eid)
    await lock_person(db, eid)
    snapshots = []
    for item in data.days:
        pid = item.project_id or data.project_id
        row = await day_row(db, eid, item.date)
        previous_shift = day_shift_label(row) if row else None
        requested_shift = (item.shift_label if "shift_label" in item.model_fields_set else previous_shift)
        if item.kind != "work" and data.kind == "schedule":
            requested_shift = None
        if not await member_on(db, eid, pid, item.date):
            raise HTTPException(422, "当天主项目与申请项目不一致，请核对派驻日期")
        if row and row.project_id != pid and data.kind != "schedule":
            raise HTTPException(422, "申请日期对应的已发布项目与主项目不一致")
        if not row and data.kind == "schedule":
            site = await field.project_site(db, pid)
            rule = await db.get(AttendanceRule, site.rule_id) if site else None
            if is_rotation(rule) and rule.effective_date <= item.date:
                await unlocked(db, eid, item.date)
                snapshots.append({**item.model_dump(mode="json"), "project_id": pid,
                    "shift_label": requested_shift, "before_shift_label": None,
                    "revision": 0, "before_kind": "pending", "before_project_id": None})
                continue
        if not row:
            raise HTTPException(422, "请先由负责人发布本人在该项目的排班")
        await unlocked(db, eid, item.date)
        if data.kind == "credit" and (row.original_kind != "rest" or row.kind == "pending" or item.date > today()):
            raise HTTPException(422, "仅能确认已发生的原定轮休日出勤")
        if data.kind == "leave" and row.kind != "work":
            raise HTTPException(422, "调休须安排在已发布的上班日，正常轮休无需扣余额")
        if data.kind == "schedule" and row.kind in {"leave", "half_leave"}:
            raise HTTPException(409, "已有批准调休，请先撤销调休申请")
        if data.kind == "schedule" and row.project_id != pid and await db.scalar(
                select(AttendanceRecord.id).where(AttendanceRecord.employee_id == eid,
                    AttendanceRecord.date == item.date)):
            raise HTTPException(409, "当天已有打卡，不能变更考勤归属项目")
        if data.kind == "schedule" and row.kind == item.kind and row.project_id == pid and previous_shift == requested_shift:
            raise HTTPException(422, "该日期安排没有变化")
        snapshots.append({**item.model_dump(mode="json"), "project_id": pid,
            "shift_label": requested_shift, "before_shift_label": previous_shift,
            "revision": row.revision, "before_kind": row.kind, "before_project_id": row.project_id})
    existing = (await db.scalars(select(RotationClaim).where(RotationClaim.employee_id == eid,
        RotationClaim.kind == data.kind,
        RotationClaim.status.in_(["pending"] if data.kind == "schedule" else ["pending", "approved"])))).all()
    requested = {d["date"] for d in snapshots}
    if any(requested & {d["date"] for d in c.days} for c in existing):
        raise HTTPException(409, "这些日期已有同类待审或已批准申请")
    requested_units = sum(1 if d["kind"] == "half_leave" else 2 for d in snapshots) if data.kind == "leave" else 0
    steps = await schedule_steps(db, eid, snapshots, user.id) if data.kind == "schedule" else []
    workflow = {"steps": steps, "next": next_review_index(steps)} if steps else None
    claim = RotationClaim(employee_id=eid, project_id=snapshots[0]["project_id"], kind=data.kind, days=snapshots,
        status="pending", units=requested_units, reason=data.reason.strip(), submitted_by=user.id,
        facts={"workflow": workflow} if workflow else {})
    db.add(claim)
    await db.flush()
    audit(db, user, "rotation_request", None, snapshot(claim))
    return await claim_view(db, claim, user)


async def claim_view(db, claim, user=None, scoped_project_id=None):
    result = snapshot(claim)
    result["employee_name"] = (await db.get(Employee, claim.employee_id)).name
    result["days_amount"] = claim.units / 2
    workflow = None
    approval_setup_required = False
    if claim.kind == "schedule":
        try:
            workflow = await schedule_workflow(db, claim)
        except HTTPException as exc:
            if exc.status_code != 422:
                raise
            approval_setup_required = True
    steps = workflow["steps"] if workflow else []
    next_index = workflow["next"] if workflow else 0
    next_step = steps[next_index] if claim.status == "pending" and next_index < len(steps) else None
    result["approval_steps"] = steps
    result["next_approval"] = {"role": next_step["role"], "project_id": next_step["project_id"],
        "step_index": next_index} if next_step else None
    result["approval_setup_required"] = approval_setup_required
    if user is not None:
        roles = await field_roles.role_names(db, user)
        manager = user.id not in {claim.employee_id, claim.submitted_by} and bool(
            roles & ({"admin"} if await field_roles.is_aftersales_manager(db, claim.employee_id)
                     else {"aftersales_manager", "admin"}))
        result["can_revoke"] = bool(claim.status == "approved" and manager)
        if claim.kind == "schedule":
            result["can_review"] = bool((next_step and await can_review_step(db, user, claim, next_step))
                or result["can_revoke"])
        else:
            result["can_review"] = bool((claim.status == "pending" and manager) or result["can_revoke"])
    if claim.kind == "credit":
        row = await day_row(db, claim.employee_id, date.fromisoformat(claim.days[0]["date"]),
            claim.days[0].get("project_id", claim.project_id))
        facts = await credit_facts(db, row) if row else {}
        result["current_facts"] = facts
        result["needs_review"] = claim.status == "approved" and facts.get("fingerprint") != claim.facts.get("fingerprint")
    if user is not None and user.id != claim.employee_id:
        roles = await field_roles.role_names(db, user)
        if not roles & {"admin", "hr", "aftersales_manager", "regional_lead"}:
            involved = {pid for item in claim.days for pid in
                (item.get("project_id", claim.project_id), item.get("before_project_id")) if pid is not None}
            visible = {pid for pid in involved if await field_roles.is_project_leader(db, user, pid)}
            if scoped_project_id is not None:
                visible &= {scoped_project_id}
            if not visible:
                raise HTTPException(403, "无权查看该申请涉及的项目")
            result["days"] = [item for item in result["days"] if visible & {
                item.get("project_id", claim.project_id), item.get("before_project_id")}]
            result["approval_steps"] = [item for item in result["approval_steps"] if item.get("project_id") in visible]
            result["facts"] = {key: value for key, value in (result.get("facts") or {}).items() if key != "workflow"}
            if result["next_approval"] and result["next_approval"]["project_id"] not in visible:
                result["next_approval"] = None
            if result["project_id"] not in visible:
                result["project_id"] = min(visible)
            if len(result["days"]) < len(claim.days):
                result["review_note"] = ""
                result["reviewed_by"] = None
                result["reviewed_at"] = None
    return result


async def review(db, user, claim_id, data):
    claim = await db.get(RotationClaim, claim_id)
    if not claim:
        raise HTTPException(404, "申请不存在")
    if user.id in {claim.employee_id, claim.submitted_by}:
        raise HTTPException(403, "排班员工及代提交人均不能审批或撤销本人提交的申请")
    await lock_person(db, claim.employee_id)
    await db.refresh(claim)
    desired = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[data.action]
    if claim.status == desired:
        if claim.kind == "schedule":
            raise HTTPException(409, "该排班申请的审批节点已经处理，请刷新")
        prior_actors = {part.get("actor_id") for part in ((claim.facts or {}).get("workflow") or {}).get("steps", [])}
        if user.id not in prior_actors and user.id != claim.reviewed_by:
            await require_claim_approver(db, user, claim, data.note)
        return await claim_view(db, claim, user)
    if claim.status != ("approved" if data.action == "revoke" else "pending"):
        raise HTTPException(409, "申请状态已变化，请刷新")
    workflow = await schedule_workflow(db, claim) if claim.kind == "schedule" else None
    if workflow and data.action != "revoke" and data.expected_step is not None and data.expected_step != workflow["next"]:
        raise HTTPException(409, "审批节点已变化，请刷新后按当前节点处理")
    if workflow and not (claim.facts or {}).get("workflow"):
        claim.days = [{**item, "project_id": item.get("project_id", claim.project_id),
            "before_project_id": item.get("before_project_id", claim.project_id)} for item in claim.days]
        claim.facts = {**(claim.facts or {}), "workflow": workflow}
    step = workflow["steps"][workflow["next"]] if workflow and claim.status == "pending" else None
    if step and data.action != "revoke":
        await require_review_step(db, user, claim, step, data.note)
        # The same account may hold adjacent roles. Require an explicit current
        # step index so a retried request cannot sign the next role by accident.
        previous_approved = next((part for part in reversed(workflow["steps"][:workflow["next"]])
            if part["status"] == "approved"), None)
        if previous_approved and previous_approved.get("actor_id") == user.id and data.expected_step is None:
            raise HTTPException(409, "同一账号兼任连续审批角色，请刷新并按当前节点分别签署")
    else:
        await require_claim_approver(db, user, claim, data.note)
    before = snapshot(claim)
    if step and data.action == "approve" and workflow["next"] < len(workflow["steps"]) - 1:
        updated = json.loads(json.dumps(workflow))
        updated["steps"][updated["next"]].update(status="approved", actor_id=user.id,
            note=data.note.strip(), reviewed_at=datetime.now(timezone.utc).isoformat())
        updated["next"] = next_review_index(updated["steps"], updated["next"] + 1)
        claim.facts = {**(claim.facts or {}), "workflow": updated}
        claim.reviewed_by, claim.review_note = user.id, data.note.strip()
        claim.reviewed_at = datetime.now(timezone.utc)
        audit(db, user, "rotation_approval_step", before, snapshot(claim))
        await db.flush()
        return await claim_view(db, claim, user)
    if data.action == "reject":
        pass
    elif data.action == "revoke":
        if claim.kind in {"credit", "leave"}:
            await post_ledger(db, user, claim, "reversal", -claim.units if claim.kind == "credit" else claim.units, data.note)
        if claim.kind != "credit":
            for item in claim.days:
                day = date.fromisoformat(item["date"])
                await unlocked(db, claim.employee_id, day)
                row = await day_row(db, claim.employee_id, day, item.get("project_id", claim.project_id))
                if not row or row.revision != item.get("applied_revision"):
                    raise HTTPException(409, "日期已再次调整，请先核对后续调整，不能直接撤销")
                if item["before_kind"] == "pending":
                    await db.delete(row)
                    from app.services.attendance_recalc import AttendanceRecalcService
                    await AttendanceRecalcService.mark_dirty(db, employee_id=claim.employee_id, work_date=day)
                else:
                    if claim.kind == "schedule" and item.get("before_project_id", claim.project_id) != row.project_id:
                        source = item.get("before_project_id", claim.project_id)
                        if not await member_on(db, claim.employee_id, source, day):
                            raise HTTPException(409, "原项目派驻已结束，不能直接撤销调班；请重新申请")
                        site = await field.project_site(db, source)
                        rule = await db.get(AttendanceRule, site.rule_id) if site else None
                        if not is_rotation(rule):
                            raise HTTPException(409, "原项目轮休规则已失效，不能直接撤销")
                        row.project_id, row.rule_snapshot = source, rule_with_shift(rule, item.get("before_shift_label"))
                    row.kind, row.reason, row.updated_by = item["before_kind"], data.note, user.id
                    if claim.kind == "schedule":
                        update_shift_label(row, item.get("before_shift_label"))
                    row.revision += 1
                    await mark_changed(db, row)
    else:
        if claim.kind == "credit":
            item = claim.days[0]
            row = await day_row(db, claim.employee_id, date.fromisoformat(item["date"]),
                item.get("project_id", claim.project_id))
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
                target = item.get("project_id", claim.project_id)
                row = await day_row(db, claim.employee_id, day)
                if row is None and claim.kind == "schedule" and item["revision"] == 0:
                    row = await manual_day(db, user, target, claim.employee_id, day, item["kind"], data.note,
                        item.get("shift_label"))
                if not row or row.revision != item["revision"] or not await member_on(db, claim.employee_id, target, day):
                    raise HTTPException(409, "排班或项目参与关系已变化，请重新申请")
                if row.project_id != (item.get("before_project_id") or target):
                    raise HTTPException(409, "项目归属已变化，请重新申请")
                if claim.kind == "leave" and row.kind != "work":
                    raise HTTPException(409, "日期已不是上班日")
                before_day = day_view(row)
                if claim.kind == "schedule" and row.project_id != target:
                    if await db.scalar(select(AttendanceRecord.id).where(AttendanceRecord.employee_id == claim.employee_id,
                            AttendanceRecord.date == day)):
                        raise HTTPException(409, "当天已有打卡，不能变更考勤归属项目")
                    site = await field.project_site(db, target)
                    rule = await db.get(AttendanceRule, site.rule_id) if site else None
                    if not is_rotation(rule) or rule.effective_date > day:
                        raise HTTPException(409, "目标项目轮休规则已变化，请重新申请")
                    row.project_id, row.rule_snapshot = target, rule_with_shift(rule, item.get("shift_label", day_shift_label(row)))
                row.kind, row.reason, row.updated_by = item["kind"], data.note, user.id
                if claim.kind == "schedule":
                    update_shift_label(row, item.get("shift_label", day_shift_label(row)))
                row.revision += 1
                applied.append({**item, "applied_revision": row.revision})
                audit(db, user, "rotation_approval_day", before_day, day_view(row))
                await mark_changed(db, row)
            claim.days = applied
            if claim.kind == "leave":
                claim.units = sum(1 if d["kind"] == "half_leave" else 2 for d in claim.days)
                await post_ledger(db, user, claim, "leave", -claim.units, data.note)
    if step and data.action == "approve":
        updated = json.loads(json.dumps(workflow))
        updated["steps"][updated["next"]].update(status="approved", actor_id=user.id,
            note=data.note.strip(), reviewed_at=datetime.now(timezone.utc).isoformat())
        updated["next"] = next_review_index(updated["steps"], updated["next"] + 1)
        claim.facts = {**(claim.facts or {}), "workflow": updated}
        if updated["next"] < len(updated["steps"]):
            desired = "pending"
    claim.status, claim.reviewed_by, claim.review_note = desired, user.id, data.note.strip()
    claim.reviewed_at = datetime.now(timezone.utc)
    audit(db, user, "rotation_" + data.action, before, snapshot(claim))
    if claim.kind == "schedule" and data.action in {"approve", "revoke"} and desired in {"approved", "revoked"}:
        project_days = {}
        for item in claim.days:
            if data.action == "revoke":
                pid = item.get("before_project_id") or item.get("project_id") or claim.project_id
            else:
                pid = item.get("project_id") or claim.project_id
            project_days.setdefault(pid, []).append(date.fromisoformat(item["date"]))
        for pid, dates in project_days.items():
            project = await db.get(FieldProject, pid)
            await NotificationService.create(db, NotificationCreate(
                recipient_id=claim.employee_id, sender_id=user.id, notif_type="system",
                title="个人排班调整已生效" if data.action == "approve" else "个人排班调整已撤销",
                content=f"{project.name}：{min(dates)} 至 {max(dates)} 的排班已更新，请查看个人排班日历。",
                ref_type="rotation_schedule", ref_id=pid))
    await db.flush()
    return await claim_view(db, claim, user)


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
    roles = await field_roles.role_names(db, user)
    all_projects = bool(roles & {"admin", "hr", "aftersales_manager", "regional_lead"})
    managed = False
    project_role = None
    if project_id:
        await field_roles.require_project_read(db, user, project_id)
        project_role = await field_roles.role_in_project(db, user, project_id)
        managed = project_role in {"admin", "aftersales_manager", "regional_lead", "project_leader"}
        if not all_projects and project_role != "project_leader" and employee_id not in (None, user.id):
            raise HTTPException(403, "只能查看本人排班")
    elif not all_projects:
        if employee_id is not None and employee_id != user.id:
            raise HTTPException(403, "只能查看本人排班")
        employee_id = user.id
    eid = employee_id or (user.id if not managed and not all_projects else None)
    stmt = select(RotationDay).where(RotationDay.work_date >= start, RotationDay.work_date <= end)
    claims_stmt = select(RotationClaim)
    if project_id:
        stmt = stmt.where(RotationDay.project_id == project_id)
    if eid:
        stmt, claims_stmt = stmt.where(RotationDay.employee_id == eid), claims_stmt.where(RotationClaim.employee_id == eid)
    days = (await db.scalars(stmt.order_by(RotationDay.work_date, RotationDay.employee_id))).all()
    claims = [c for c in (await db.scalars(claims_stmt.order_by(RotationClaim.id.desc()))).all()
        if any(start.isoformat() <= d["date"] <= end.isoformat() and
            (project_id is None or project_id in {d.get("project_id", c.project_id), d.get("before_project_id")})
            for d in c.days)]
    names = {p.id: p.name for p in (await db.scalars(select(Employee).where(Employee.id.in_({d.employee_id for d in days} | {c.employee_id for c in claims})))).all()}
    ledger_stmt = select(RotationLedger).join(RotationClaim, RotationLedger.claim_id == RotationClaim.id)
    if project_id:
        ledger_stmt = ledger_stmt.where(RotationLedger.claim_id.in_({c.id for c in claims} or {-1}))
    if eid:
        ledger_stmt = ledger_stmt.where(RotationLedger.employee_id == eid)
    ledger = (await db.scalars(ledger_stmt.order_by(RotationLedger.id.desc()).limit(500))).all()
    accounts = (await db.scalars(select(RotationAccount).where(RotationAccount.employee_id.in_(set(names) | ({eid} if eid else set()))))).all()
    claim_views = []
    for claim in claims:
        item = await claim_view(db, claim, user, project_id if project_role == "project_leader" else None)
        claim_views.append(item)
    return {"days": [{**day_view(d), "employee_name": names.get(d.employee_id)} for d in days],
        "claims": claim_views, "ledger": [snapshot(l) for l in ledger],
        "balances": [{"employee_id": a.employee_id, "employee_name": names.get(a.employee_id), "days": a.units / 2} for a in accounts],
        "can_manage": managed or bool(roles & {"admin", "aftersales_manager"}), "labels": LABELS}
