"""Confirmed personal rotation dates before a project's first rule exists.

Draft dates are visible but never authorize a punch or earn rest credit. The
first rotation rule atomically converts them to published, frozen-rule days.
"""

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.models.attendance import AttendanceRecord
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.field_service import FieldProject, FieldProjectPendingMember
from app.models.rotation import RotationDay, RotationDraftDay
from app.schemas.notification import NotificationCreate
from app.services import field_roles
from app.services.field_membership import lock_person, project_on
from app.services.notification import NotificationService


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def sunday(day: date) -> date:
    return monday(day) + timedelta(days=6)


def dates(start: date, end: date):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def draft_view(row):
    return {"id": row.id, "employee_id": row.employee_id, "project_id": row.project_id,
            "date": row.work_date, "cycle_start": row.cycle_start,
            "original_kind": row.original_kind, "kind": row.kind,
            "shift_label": row.shift_label if row.kind == "work" else None,
            "revision": row.revision, "reason": row.reason,
            "updated_by": row.updated_by, "updated_at": row.updated_at,
            "rule_name": None, "required_hours": 0, "status": "pending_rule"}


async def pending_ids(db, project_id):
    return set((await db.scalars(select(FieldProjectPendingMember.employee_id).where(
        FieldProjectPendingMember.project_id == project_id))).all())


def status_for_rows(member_ids, rows, current_day):
    """A common future week is ready only with every person's every open date."""
    members = set(member_ids)
    future = [row for row in rows if row.work_date >= current_day]
    weeks = sorted({monday(row.work_date) for row in future})
    evaluated = []
    for week in weeks:
        start, end = max(current_day, week), week + timedelta(days=6)
        if start > end:
            continue
        available = {(row.employee_id, row.work_date) for row in future
                     if start <= row.work_date <= end and row.employee_id in members}
        complete = {eid for eid in members if all((eid, day) in available for day in dates(start, end))}
        evaluated.append((week, start, end, complete))
    # Point the UI at the first incomplete week. A later complete week must
    # never mask an earlier partial plan that would otherwise be converted.
    chosen = next((item for item in evaluated if item[3] != members), None)
    if chosen is None and evaluated:
        chosen = evaluated[0]
    if chosen is None:
        week = monday(current_day)
        chosen = (week, current_day, week + timedelta(days=6), set())
    week, start, end, complete = chosen
    return {
        "pre_rule_schedule_count": sum(row.employee_id in members for row in future),
        "pre_rule_schedule_member_count": len(complete),
        "pre_rule_schedule_employee_ids": sorted({row.employee_id for row in future if row.employee_id in members}),
        "pre_rule_schedule_ready": bool(members and evaluated and
                                        all(item[3] == members for item in evaluated)),
        "pre_rule_schedule_week_start": week,
        "pre_rule_schedule_publish_start": start,
        "pre_rule_schedule_week_end": end,
        "pre_rule_schedule_missing_member_ids": sorted(members - complete),
    }


async def project_status(db, project_id, current_day=None):
    from app.services.rotation import today
    members = await pending_ids(db, project_id)
    rows = (await db.scalars(select(RotationDraftDay).where(
        RotationDraftDay.project_id == project_id))).all()
    return status_for_rows(members, rows, current_day or today())


async def project_statuses(db, project_ids, pending_rows, current_day=None):
    from app.services.rotation import today
    projects = set(project_ids)
    if not projects:
        return {}
    rows = (await db.scalars(select(RotationDraftDay).where(
        RotationDraftDay.project_id.in_(projects)))).all()
    by_project = defaultdict(list)
    members = defaultdict(set)
    for row in rows:
        by_project[row.project_id].append(row)
    for row in pending_rows:
        members[row.project_id].add(row.employee_id)
    return {pid: status_for_rows(members[pid], by_project[pid], current_day or today())
            for pid in projects}


async def require_ready(db, project_id):
    from app.services.rotation import today
    members = await pending_ids(db, project_id)
    if not members:
        raise HTTPException(422, "请先确认项目组长和全部组员，再确认个人排班")
    status = await project_status(db, project_id)
    if status["pre_rule_schedule_ready"]:
        return status
    names = dict((await db.execute(select(Employee.id, Employee.name).where(
        Employee.id.in_(status["pre_rule_schedule_missing_member_ids"])))).all())
    missing = "、".join(names.get(eid, str(eid)) for eid in status["pre_rule_schedule_missing_member_ids"])
    week = status["pre_rule_schedule_week_start"]
    raise HTTPException(422, f"请先确认 {week} 所在周全部项目人员的个人排班；未完整确认：{missing}")


async def _check_other_project(db, employee_id, project_id, day):
    published = await db.scalar(select(RotationDay).where(
        RotationDay.employee_id == employee_id, RotationDay.work_date == day))
    if published:
        raise HTTPException(409, f"人员 {employee_id} 在 {day} 已有正式排班，请核对项目调动")
    draft = await db.scalar(select(RotationDraftDay).where(
        RotationDraftDay.employee_id == employee_id, RotationDraftDay.work_date == day))
    if draft and draft.project_id != project_id:
        raise HTTPException(409, f"人员 {employee_id} 在 {day} 已于其他项目确认待生效排班")
    existing = await project_on(db, employee_id, day)
    if existing and existing[3].id != project_id:
        raise HTTPException(409, f"人员 {employee_id} 在 {day} 已属于其他项目，请先办理项目调动")
    return draft


async def preview(db, user, project_id, data):
    from app.services import field_service as field
    from app.services.rotation import personal_cycle_start, today

    project = await field_roles.require_project_schedule_manager(db, user, project_id)
    if not project.is_active:
        raise HTTPException(422, "项目已停用")
    if await field.project_site(db, project_id):
        raise HTTPException(409, "项目已有规则，请使用正式排班流程")
    members = await pending_ids(db, project_id)
    if not members or set(data.employee_ids) != members or len(data.employee_ids) != len(members):
        raise HTTPException(422, "规则前须同时为已确认的项目组长和全部组员排班")
    current_day = today()
    if data.start_date < current_day or data.start_date != max(current_day, monday(data.start_date)) \
            or data.end_date != sunday(data.start_date):
        raise HTTPException(422, "规则前按北京时间整周确认：本周从今天到周日，未来周从周一到周日")
    edits = {edit.date: edit for edit in data.edits}
    person_edits = {(edit.employee_id, edit.date): edit for edit in data.person_edits}
    result = []
    for eid in sorted(members):
        await field_roles.require_project_schedule_manager(db, user, project_id, eid)
        anchor = await personal_cycle_start(db, eid, data.cycle_start)
        for day in dates(data.start_date, data.end_date):
            old = await _check_other_project(db, eid, project_id, day)
            original = "rest" if (day - anchor).days % 7 == 6 else "work"
            edit = person_edits.get((eid, day)) or edits.get(day)
            kind = edit.kind if edit else original
            label = (edit.shift_label if edit and "shift_label" in edit.model_fields_set
                     else old.shift_label if old else None)
            result.append({"employee_id": eid, "date": day, "cycle_start": anchor,
                "original_kind": old.original_kind if old else original, "kind": kind,
                "shift_label": label if kind == "work" else None,
                "expected_revision": old.revision if old else 0})
    digest = hashlib.sha256(json.dumps(jsonable_encoder([
        project_id, sorted(members), result]), sort_keys=True).encode()).hexdigest()
    return {"rows": result, "revision": digest, "rule_id": None, "phase": "pre_rule"}


async def publish(db, user, project_id, data, revision):
    from app.services.rotation import audit

    await field_roles.require_project_schedule_manager(db, user, project_id)
    await db.scalar(select(FieldProject).where(
        FieldProject.id == project_id).with_for_update())
    for eid in sorted(set(data.employee_ids)):
        await lock_person(db, eid)
    plan = await preview(db, user, project_id, data)
    if revision != plan["revision"]:
        raise HTTPException(409, "待生效排班或人员名单已变化，请重新预览")
    before, after = [], []
    for item in plan["rows"]:
        eid, day = item["employee_id"], item["date"]
        row = await db.scalar(select(RotationDraftDay).where(
            RotationDraftDay.employee_id == eid, RotationDraftDay.work_date == day))
        if row is None:
            row = RotationDraftDay(employee_id=eid, project_id=project_id, work_date=day,
                cycle_start=item["cycle_start"], original_kind=item["original_kind"],
                kind=item["kind"], shift_label=item["shift_label"], revision=1,
                reason=data.reason, updated_by=user.id)
            db.add(row)
        else:
            old = draft_view(row)
            before.append(old)
            changed = any(getattr(row, key) != value for key, value in (
                ("cycle_start", item["cycle_start"]), ("kind", item["kind"]),
                ("shift_label", item["shift_label"]), ("reason", data.reason)))
            if changed:
                row.cycle_start, row.kind = item["cycle_start"], item["kind"]
                row.shift_label, row.reason = item["shift_label"], data.reason
                row.revision += 1
                row.updated_by = user.id
        await db.flush()
        after.append(draft_view(row))
    audit(db, user, "rotation_pre_rule_confirm", before, after)
    return {"count": len(plan["rows"]), "phase": "pre_rule", "status": "pending_rule"}


async def cancel_person(db, user, project_id, employee_id):
    from app.services import field_service as field
    from app.services.rotation import audit

    await field_roles.require_project_schedule_manager(db, user, project_id, employee_id)
    await db.scalar(select(FieldProject).where(
        FieldProject.id == project_id).with_for_update())
    if await field.project_site(db, project_id):
        raise HTTPException(409, "规则已建立；正式排班修改须走调班审批")
    await lock_person(db, employee_id)
    rows = (await db.scalars(select(RotationDraftDay).where(
        RotationDraftDay.project_id == project_id,
        RotationDraftDay.employee_id == employee_id))).all()
    before = [draft_view(row) for row in rows]
    for row in rows:
        await db.delete(row)
    if rows:
        audit(db, user, "rotation_pre_rule_cancel", before, [])
    return {"count": len(rows), "phase": "pre_rule"}


async def finalize(db, user, project_id, rule, expected_member_ids):
    """Atomically bind all confirmed dates after roster activation."""
    from app.services.rotation import audit, day_view, rule_with_shift, today, unlocked, member_on

    query = select(RotationDraftDay).where(
        RotationDraftDay.project_id == project_id).order_by(
        RotationDraftDay.work_date, RotationDraftDay.employee_id).with_for_update()
    all_rows = list((await db.scalars(query)).all())
    expired = [row for row in all_rows if row.work_date < today()]
    if expired:
        audit(db, user, "rotation_pre_rule_expired", [draft_view(row) for row in expired],
              {"expired_unpublished_count": len(expired), "note": "过期计划未转为出勤"})
        for row in expired:
            await db.delete(row)
    rows = [row for row in all_rows if row.work_date >= today()]
    if not rows:
        raise HTTPException(422, "请先确认全部项目人员的未来一周排班")
    if rule.effective_date > rows[0].work_date:
        raise HTTPException(422, f"规则生效日不能晚于已确认排班首日 {rows[0].work_date}；请调整生效日或重新确认日期")
    if {row.employee_id for row in rows} != set(expected_member_ids):
        raise HTTPException(409, "待生效排班含已移出人员，请先核对名单与计划")
    dates_by_person = defaultdict(list)
    for row in rows:
        await unlocked(db, row.employee_id, row.work_date)
        if not await member_on(db, row.employee_id, project_id, row.work_date):
            raise HTTPException(409, f"人员 {row.employee_id} 在 {row.work_date} 尚未加入本项目，请核对项目调动")
        if await db.scalar(select(RotationDay.id).where(
                RotationDay.employee_id == row.employee_id, RotationDay.work_date == row.work_date)):
            raise HTTPException(409, f"人员 {row.employee_id} 在 {row.work_date} 已有正式排班")
        if await db.scalar(select(AttendanceRecord.id).where(
                AttendanceRecord.employee_id == row.employee_id, AttendanceRecord.date == row.work_date)):
            raise HTTPException(409, f"人员 {row.employee_id} 在 {row.work_date} 已有打卡记录")
        published = RotationDay(employee_id=row.employee_id, project_id=project_id,
            work_date=row.work_date, cycle_start=row.cycle_start,
            original_kind=row.original_kind, kind=row.kind,
            rule_snapshot=rule_with_shift(rule, row.shift_label), revision=1,
            reason=row.reason, updated_by=user.id)
        db.add(published)
        await db.flush()
        audit(db, user, "rotation_publish", None, day_view(published))
        dates_by_person[row.employee_id].append(row.work_date)
        await db.delete(row)
    project = await db.get(FieldProject, project_id)
    for eid, planned in dates_by_person.items():
        await NotificationService.create(db, NotificationCreate(
            recipient_id=eid, sender_id=user.id, notif_type="system",
            title="个人排班已发布",
            content=f"{project.name}：{min(planned)} 至 {max(planned)} 已发布 {len(planned)} 天排班，请查看个人排班日历。",
            ref_type="rotation_schedule", ref_id=project_id))
    return len(rows)
