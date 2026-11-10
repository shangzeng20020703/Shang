"""Independent personal rotation plans and their pre-rule / published dates."""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import or_, select

from app.models.attendance import AttendanceRecord, AttendanceRule
from app.models.employee import Employee
from app.models.field_service import FieldAssignment, FieldProject, FieldProjectPendingMember, FieldSite
from app.models.rotation import RotationDay, RotationDraftDay
from app.models.rotation_person_plan import RotationPersonalPlan, RotationPersonalPlanDay
from app.schemas.notification import NotificationCreate
from app.services import field_roles
from app.services.field_membership import lock_person, project_on
from app.services.notification import NotificationService


PATTERNS = {"six_one": (6, 1), "twelve_two": (12, 2), "eighteen_three": (18, 3)}


def days_between(start, end):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def day_view(row, status=None, cycle_start=None):
    return {"id": row.id, "employee_id": row.employee_id, "project_id": row.project_id,
            "date": row.work_date, "cycle_start": cycle_start,
            "original_kind": row.original_kind, "kind": row.kind, "rest_type": row.rest_type,
            "shift_label": row.shift_label if row.kind == "work" else None,
            "revision": row.revision, "status": status or ("pending_project" if row.project_id is None else "pending_rule")}


async def _project_rule(db, project_id):
    from app.services.field_service import project_site
    site = await project_site(db, project_id)
    if not site:
        return None
    rule = await db.get(AttendanceRule, site.rule_id)
    if not rule or not rule.is_active or (rule.extra_config or {}).get("rule_type") != "rotation":
        raise HTTPException(409, "本项目已建立其他类型规则，不能使用个人轮休排班")
    return rule


async def _authorized(db, user, project_id, employee_id, *, write=True):
    if write:
        project = await field_roles.require_project_schedule_manager(db, user, project_id, employee_id)
    else:
        project = await field_roles.require_project_read(db, user, project_id)
    person = await db.get(Employee, employee_id)
    if not person or not person.is_active or person.status not in {"在职", "试用"}:
        raise HTTPException(422, "人员不在有效花名册中")
    if not project.is_active:
        raise HTTPException(422, "项目已停用")
    if user.id != employee_id:
        pending = await db.scalar(select(FieldProjectPendingMember.id).where(
            FieldProjectPendingMember.project_id == project_id,
            FieldProjectPendingMember.employee_id == employee_id).limit(1))
        assigned = await db.scalar(select(FieldAssignment.id).join(
            FieldSite, FieldSite.id == FieldAssignment.site_id).where(
            FieldSite.project_id == project_id,
            FieldAssignment.employee_id == employee_id).limit(1))
        planned = await db.scalar(select(RotationPersonalPlanDay.id).where(
            RotationPersonalPlanDay.project_id == project_id,
            RotationPersonalPlanDay.employee_id == employee_id).limit(1))
        if not any((pending, assigned, planned)):
            raise HTTPException(403, "只能查看或安排本项目参与人员")
    return project, person


async def get_plans(db, user, project_id, employee_id):
    await _authorized(db, user, project_id, employee_id, write=False)
    from app.services.rotation import today
    established_cycle_start = await _established_cycle(db, employee_id)
    bindable = list((await db.execute(select(
        RotationPersonalPlanDay.plan_id, RotationPersonalPlanDay.work_date,
        RotationPersonalPlanDay.kind, RotationPersonalPlanDay.rest_type,
    ).where(RotationPersonalPlanDay.employee_id == employee_id,
            RotationPersonalPlanDay.project_id.is_(None),
            RotationPersonalPlanDay.work_date >= today(),
    ).order_by(RotationPersonalPlanDay.work_date))).all())
    bindable_days = [{"plan_id": plan_id, "date": day, "kind": kind,
                      "rest_type": rest_type, "status": "pending_project"}
                     for plan_id, day, kind, rest_type in bindable]
    plans = list((await db.scalars(select(RotationPersonalPlan).where(
        RotationPersonalPlan.employee_id == employee_id,
        or_(RotationPersonalPlan.context_project_id == project_id,
            RotationPersonalPlan.id.in_(select(RotationPersonalPlanDay.plan_id).where(
                RotationPersonalPlanDay.employee_id == employee_id,
                RotationPersonalPlanDay.project_id == project_id))),
    ).order_by(RotationPersonalPlan.start_date, RotationPersonalPlan.id))).all())
    if not plans:
        return {"employee_id": employee_id, "project_id": project_id,
                "established_cycle_start": established_cycle_start,
                "plans": [], "bindable_days": bindable_days}
    rows = list((await db.scalars(select(RotationPersonalPlanDay).where(
        RotationPersonalPlanDay.plan_id.in_([plan.id for plan in plans]),
    ).order_by(RotationPersonalPlanDay.work_date))).all())
    published = set((await db.execute(select(RotationDay.employee_id, RotationDay.work_date).where(
        RotationDay.employee_id == employee_id,
        RotationDay.work_date >= min(plan.start_date for plan in plans),
        RotationDay.work_date <= max(plan.end_date for plan in plans),
    ))).all())
    by_plan = {plan.id: [] for plan in plans}
    for row in rows:
        owner_plan = next(plan for plan in plans if plan.id == row.plan_id)
        if owner_plan.context_project_id != project_id and row.project_id not in {None, project_id}:
            continue
        status = "published" if (row.employee_id, row.work_date) in published else None
        by_plan[row.plan_id].append(day_view(row, status,
            owner_plan.cycle_start))
    return {"employee_id": employee_id, "project_id": project_id,
            "established_cycle_start": established_cycle_start,
            "bindable_days": bindable_days, "plans": [
        {"id": plan.id, "start_date": plan.start_date, "end_date": plan.end_date,
         "cycle_start": plan.cycle_start, "template": plan.template,
         "revision": plan.revision, "reason": plan.reason,
         "editable": plan.context_project_id == project_id, "rows": by_plan[plan.id]}
        for plan in plans]}


async def _established_cycle(db, employee_id, excluding_plan_id=None):
    records = []
    for model in (RotationDay, RotationDraftDay):
        result = (await db.execute(select(model.work_date, model.cycle_start).where(
            model.employee_id == employee_id).order_by(model.work_date, model.id).limit(1))).first()
        if result:
            records.append(result)
    query = select(RotationPersonalPlan.start_date, RotationPersonalPlan.cycle_start).where(
        RotationPersonalPlan.employee_id == employee_id)
    if excluding_plan_id:
        query = query.where(RotationPersonalPlan.id != excluding_plan_id)
    result = (await db.execute(query.order_by(RotationPersonalPlan.start_date,
        RotationPersonalPlan.id).limit(1))).first()
    if result:
        records.append(result)
    return min(records, key=lambda pair: pair[0])[1] if records else None


def _rest_types(rows, template, previous_rows=()):
    timeline = [*previous_rows, *rows]
    offset = len(previous_rows)
    for index, row in enumerate(rows):
        if row["kind"] != "rest":
            row["rest_type"] = None
        elif template in {"twelve_two", "eighteen_three"}:
            row["rest_type"] = "concentrated"
        else:
            streak = 0
            for previous in reversed(timeline[:offset + index]):
                if previous["kind"] != "work":
                    break
                streak += 1
            next_is_rest = index + 1 < len(rows) and rows[index + 1]["kind"] == "rest"
            row["rest_type"] = ("ordinary" if streak == 6 and not next_is_rest and
                (template == "custom" or row["original_kind"] == "rest") else "concentrated")


async def preview(db, user, project_id, employee_id, data):
    from app.services.rotation import today

    project, _ = await _authorized(db, user, project_id, employee_id)
    rule = await _project_rule(db, project_id)
    if data.start_date < today():
        raise HTTPException(422, "个人排班只能从今天或未来日期开始；历史排班请查看记录")
    if not rule and not await db.scalar(select(FieldProjectPendingMember.id).where(
        FieldProjectPendingMember.project_id == project_id,
        FieldProjectPendingMember.employee_id == employee_id).limit(1)):
        raise HTTPException(422, "请先确认该项目组长或组员名单")
    prior = await db.get(RotationPersonalPlan, data.plan_id) if data.plan_id else None
    if data.plan_id and (not prior or prior.employee_id != employee_id or
                         prior.context_project_id != project_id):
        raise HTTPException(404, "个人排班计划不存在")
    established = await _established_cycle(db, employee_id, data.plan_id)
    if established and established != data.cycle_start:
        raise HTTPException(422, f"个人周期已从 {established} 开始，跨项目排班不能重置周期起点")
    if prior and data.start_date != prior.start_date:
        raise HTTPException(422, "已有计划的开始日期不可修改；请撤销未生效计划后另建")
    edits = {item.date: item for item in data.days}
    rows = []
    for day in days_between(data.start_date, data.end_date):
        edit = edits.get(day)
        if data.template == "custom":
            kind = edit.kind
        else:
            work, rest = PATTERNS[data.template]
            original = "work" if (day - data.cycle_start).days % (work + rest) < work else "rest"
            kind = edit.kind if edit else original
        original_kind = (kind if data.template == "custom" else
            ("work" if (day - data.cycle_start).days % sum(PATTERNS[data.template]) < PATTERNS[data.template][0] else "rest"))
        project_for_day = (edit.project_id if edit and "project_id" in edit.model_fields_set else project_id)
        if project_for_day not in {None, project_id}:
            raise HTTPException(403, "只能安排本项目日期或标记为待分配项目")
        if project_for_day == project_id:
            owner = await project_on(db, employee_id, day)
            if owner and owner[3].id != project_id:
                raise HTTPException(409, f"{day} 已归属其他项目，请先办理项目调动")
            if rule and not owner:
                raise HTTPException(409, f"{day} 尚未加入本项目，请先完成项目人员分配")
        else:
            owner = await project_on(db, employee_id, day)
            # The current project's open-ended participation may still span a
            # future TBD date. A missing published day prevents punching there;
            # the later approved transfer will close that participation first.
            if owner and owner[3].id != project_id:
                raise HTTPException(409, f"{day} 已归属其他项目，请填写对应项目安排")
        published = await db.scalar(select(RotationDay.id).where(
            RotationDay.employee_id == employee_id, RotationDay.work_date == day))
        if published:
            raise HTTPException(409, f"{day} 已正式发布，后续修改须提交调班申请")
        existing = await db.scalar(select(RotationPersonalPlanDay).where(
            RotationPersonalPlanDay.employee_id == employee_id,
            RotationPersonalPlanDay.work_date == day))
        if existing and (not prior or existing.plan_id != prior.id):
            raise HTTPException(409, f"{day} 已在另一段个人排班中，请先核对日期")
        draft = await db.scalar(select(RotationDraftDay).where(
            RotationDraftDay.employee_id == employee_id, RotationDraftDay.work_date == day))
        if draft and (not prior or not existing or existing.plan_id != prior.id):
            raise HTTPException(409, f"{day} 已有旧版待生效排班，请先核对或撤销")
        rows.append({"employee_id": employee_id, "date": day,
            "project_id": project_for_day, "cycle_start": data.cycle_start,
            "original_kind": original_kind, "kind": kind,
            "shift_label": edit.shift_label if edit and kind == "work" else None,
            "expected_revision": existing.revision if existing else 0,
            "status": "pending_project" if project_for_day is None else
                      "published_on_confirm" if rule else "pending_rule"})
    recent_start = data.start_date - timedelta(days=7)
    recent = {row.work_date: row.kind for row in (await db.scalars(select(
        RotationPersonalPlanDay).where(
        RotationPersonalPlanDay.employee_id == employee_id,
        RotationPersonalPlanDay.work_date >= recent_start,
        RotationPersonalPlanDay.work_date < data.start_date))).all()}
    for older in (await db.scalars(select(RotationDay).where(
        RotationDay.employee_id == employee_id,
        RotationDay.work_date >= recent_start,
        RotationDay.work_date < data.start_date))).all():
        recent.setdefault(older.work_date, older.kind)
    previous = []
    cursor = data.start_date - timedelta(days=1)
    while cursor in recent:
        previous.append({"kind": recent[cursor]})
        cursor -= timedelta(days=1)
    _rest_types(rows, data.template, list(reversed(previous)))
    owned_dates = [item["date"] for item in rows if item["project_id"] == project_id]
    if not owned_dates:
        raise HTTPException(422, "个人排班至少有一天属于当前项目")
    if rule and rule.effective_date > min(owned_dates):
        raise HTTPException(422, "本项目安排日期不能早于项目规则生效日期")
    revision = hashlib.sha256(json.dumps(jsonable_encoder([
        project.id, employee_id, prior.id if prior else None, prior.revision if prior else 0,
        rule.id if rule else None, data.model_dump(), rows,
    ]), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"rows": rows, "revision": revision,
            "plan_id": prior.id if prior else None,
            "phase": "published" if rule else "pre_rule"}


async def confirm(db, user, project_id, employee_id, data, revision):
    from app.services.rotation import audit, rule_with_shift, today, unlocked

    await db.scalar(select(FieldProject).where(FieldProject.id == project_id).with_for_update())
    await lock_person(db, employee_id)
    result = await preview(db, user, project_id, employee_id, data)
    if result["revision"] != revision:
        raise HTTPException(409, "个人排班、人员或规则已变化，请重新预览")
    rule = await _project_rule(db, project_id)
    plan = await db.get(RotationPersonalPlan, data.plan_id) if data.plan_id else None
    before = None
    if plan:
        before = {"id": plan.id, "start_date": plan.start_date,
                  "end_date": plan.end_date, "template": plan.template,
                  "revision": plan.revision}
        old = list((await db.scalars(select(RotationPersonalPlanDay).where(
            RotationPersonalPlanDay.plan_id == plan.id).with_for_update())).all())
        retained = {item["date"] for item in result["rows"]}
        for row in old:
            if row.work_date in retained:
                continue
            if await db.scalar(select(RotationDay.id).where(
                RotationDay.employee_id == employee_id, RotationDay.work_date == row.work_date)):
                raise HTTPException(409, "已有正式排班日期不可从个人计划中删除")
            draft = await db.scalar(select(RotationDraftDay).where(
                RotationDraftDay.employee_id == employee_id, RotationDraftDay.work_date == row.work_date))
            if draft:
                await db.delete(draft)
            await db.delete(row)
        plan.end_date, plan.cycle_start, plan.template = data.end_date, data.cycle_start, data.template
        plan.reason, plan.updated_by, plan.revision = data.reason, user.id, plan.revision + 1
    else:
        plan = RotationPersonalPlan(employee_id=employee_id, context_project_id=project_id,
            start_date=data.start_date, end_date=data.end_date, cycle_start=data.cycle_start,
            template=data.template, reason=data.reason, updated_by=user.id, revision=1)
        db.add(plan)
        await db.flush()
    published_dates = []
    for item in result["rows"]:
        day = item["date"]
        row = await db.scalar(select(RotationPersonalPlanDay).where(
            RotationPersonalPlanDay.employee_id == employee_id,
            RotationPersonalPlanDay.work_date == day))
        if row:
            row.project_id, row.original_kind, row.kind = item["project_id"], item["original_kind"], item["kind"]
            row.rest_type, row.shift_label = item["rest_type"], item["shift_label"]
            row.revision, row.updated_by = row.revision + 1, user.id
        else:
            row = RotationPersonalPlanDay(plan_id=plan.id, employee_id=employee_id,
                project_id=item["project_id"], work_date=day,
                original_kind=item["original_kind"], kind=item["kind"],
                rest_type=item["rest_type"], shift_label=item["shift_label"],
                revision=1, updated_by=user.id)
            db.add(row)
        if item["project_id"] is None:
            old_draft = await db.scalar(select(RotationDraftDay).where(
                RotationDraftDay.employee_id == employee_id, RotationDraftDay.work_date == day))
            if old_draft:
                await db.delete(old_draft)
            continue
        if rule:
            await unlocked(db, employee_id, day)
            if await db.scalar(select(AttendanceRecord.id).where(
                AttendanceRecord.employee_id == employee_id, AttendanceRecord.date == day)):
                raise HTTPException(409, f"{day} 已有打卡记录，不能重新发布")
            frozen = {**rule_with_shift(rule, item["shift_label"]),
                      "_rotation_rest_type": item["rest_type"]}
            db.add(RotationDay(employee_id=employee_id, project_id=project_id,
                work_date=day, cycle_start=data.cycle_start,
                original_kind=item["original_kind"], kind=item["kind"],
                rule_snapshot=frozen, revision=1, reason=data.reason, updated_by=user.id))
            published_dates.append(day)
        else:
            draft = await db.scalar(select(RotationDraftDay).where(
                RotationDraftDay.employee_id == employee_id, RotationDraftDay.work_date == day))
            if draft:
                draft.project_id, draft.cycle_start = project_id, data.cycle_start
                draft.original_kind, draft.kind = item["original_kind"], item["kind"]
                draft.shift_label, draft.reason = item["shift_label"], data.reason
                draft.updated_by, draft.revision = user.id, draft.revision + 1
            else:
                db.add(RotationDraftDay(employee_id=employee_id, project_id=project_id,
                    work_date=day, cycle_start=data.cycle_start,
                    original_kind=item["original_kind"], kind=item["kind"],
                    shift_label=item["shift_label"], revision=1,
                    reason=data.reason, updated_by=user.id))
    await db.flush()
    audit(db, user, "rotation_person_plan_confirm", before, {
        "id": plan.id, "employee_id": employee_id, "project_id": project_id,
        "start_date": data.start_date, "end_date": data.end_date,
        "template": data.template, "revision": plan.revision,
        "project_days": sum(item["project_id"] == project_id for item in result["rows"]),
        "pending_project_days": sum(item["project_id"] is None for item in result["rows"]),
    })
    if published_dates:
        project = await db.get(FieldProject, project_id)
        await NotificationService.create(db, NotificationCreate(recipient_id=employee_id,
            sender_id=user.id, notif_type="system", title="个人排班已发布",
            content=f"{project.name}：{min(published_dates)} 至 {max(published_dates)} 已发布 {len(published_dates)} 天排班。",
            ref_type="rotation_schedule", ref_id=project_id))
    return {"id": plan.id, "revision": plan.revision,
            "count": len(result["rows"]), "published_count": len(published_dates),
            "status": "published" if rule else "pending_rule",
            "phase": "published" if rule else "pre_rule"}


async def bind_pending_project(db, user, project_id, employee_id, data):
    """Attach existing TBD dates after this project's staffing/transfer is authorized."""
    from app.services.rotation import audit, rule_with_shift, today, unlocked

    project, _ = await _authorized(db, user, project_id, employee_id)
    await db.scalar(select(FieldProject).where(FieldProject.id == project_id).with_for_update())
    await lock_person(db, employee_id)
    plan = await db.get(RotationPersonalPlan, data.plan_id)
    if not plan or plan.employee_id != employee_id:
        raise HTTPException(404, "个人排班计划不存在")
    rule = await _project_rule(db, project_id)
    if not rule and not await db.scalar(select(FieldProjectPendingMember.id).where(
        FieldProjectPendingMember.employee_id == employee_id,
        FieldProjectPendingMember.project_id == project_id).limit(1)):
        raise HTTPException(422, "请先确认目标项目的人员名单")
    rows = list((await db.scalars(select(RotationPersonalPlanDay).where(
        RotationPersonalPlanDay.plan_id == plan.id,
        RotationPersonalPlanDay.work_date.in_(data.dates)).with_for_update())).all())
    if len(rows) != len(data.dates):
        raise HTTPException(422, "部分日期不属于这份个人排班计划")
    for row in rows:
        if row.project_id is not None:
            raise HTTPException(409, f"{row.work_date} 已分配项目，不能重复分配")
        if row.work_date < today():
            raise HTTPException(422, "过去日期不能作为未来项目安排")
        owner = await project_on(db, employee_id, row.work_date)
        if owner and owner[3].id != project_id:
            raise HTTPException(409, f"{row.work_date} 仍归属其他项目，请先完成跨项目调动审批")
        if rule and not owner:
            raise HTTPException(409, f"{row.work_date} 尚未加入目标项目，请先完成项目分配或调动审批")
        if rule and rule.effective_date > row.work_date:
            raise HTTPException(422, f"{row.work_date} 早于目标项目规则生效日")
        if await db.scalar(select(RotationDay.id).where(
            RotationDay.employee_id == employee_id, RotationDay.work_date == row.work_date)):
            raise HTTPException(409, f"{row.work_date} 已有正式排班")
        if await db.scalar(select(RotationDraftDay.id).where(
            RotationDraftDay.employee_id == employee_id, RotationDraftDay.work_date == row.work_date)):
            raise HTTPException(409, f"{row.work_date} 已有待生效排班")
        if await db.scalar(select(AttendanceRecord.id).where(
            AttendanceRecord.employee_id == employee_id, AttendanceRecord.date == row.work_date)):
            raise HTTPException(409, f"{row.work_date} 已有打卡记录")
    for row in rows:
        row.project_id, row.revision, row.updated_by = project_id, row.revision + 1, user.id
        if rule:
            await unlocked(db, employee_id, row.work_date)
            db.add(RotationDay(employee_id=employee_id, project_id=project_id,
                work_date=row.work_date, cycle_start=plan.cycle_start,
                original_kind=row.original_kind, kind=row.kind,
                rule_snapshot={**rule_with_shift(rule, row.shift_label),
                               "_rotation_rest_type": row.rest_type},
                revision=1, reason=data.reason, updated_by=user.id))
        else:
            db.add(RotationDraftDay(employee_id=employee_id, project_id=project_id,
                work_date=row.work_date, cycle_start=plan.cycle_start,
                original_kind=row.original_kind, kind=row.kind,
                shift_label=row.shift_label, revision=1,
                reason=data.reason, updated_by=user.id))
    await db.flush()
    audit(db, user, "rotation_person_plan_bind_project",
          {"plan_id": plan.id, "dates": data.dates, "project_id": None},
          {"plan_id": plan.id, "dates": data.dates, "project_id": project_id,
           "rule_id": rule.id if rule else None, "reason": data.reason})
    if rule:
        await NotificationService.create(db, NotificationCreate(recipient_id=employee_id,
            sender_id=user.id, notif_type="system", title="个人排班已分配项目",
            content=f"{project.name}：{min(data.dates)} 至 {max(data.dates)} 的个人排班已生效。",
            ref_type="rotation_schedule", ref_id=project_id))
    return {"plan_id": plan.id, "bound_count": len(rows), "project_id": project_id,
            "status": "published" if rule else "pending_rule"}
