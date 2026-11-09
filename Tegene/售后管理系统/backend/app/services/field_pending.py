"""Pre-rule project roster and atomic activation into attendance memberships."""

import json
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, or_, select

from app.models.attendance import AttendanceRule
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.rotation import RotationDraftDay
from app.models.field_service import (
    FieldAssignment, FieldMembership, FieldProject, FieldProjectPendingMember,
    FieldRosterProfile, FieldSite,
)
from app.services.field_membership import CST, clock_now, lock_person


def describe(row, name):
    return {"id": row.id, "project_id": row.project_id,
            "employee_id": row.employee_id, "employee_name": name,
            "status": "pending", "is_leader_auto": bool(row.is_leader_auto)}


async def _rows(db, project_id):
    return list((await db.scalars(select(FieldProjectPendingMember).where(
        FieldProjectPendingMember.project_id == project_id,
    ).order_by(FieldProjectPendingMember.id))).all())


async def confirm_pending_roster(db, user, project_id, data):
    """Replace one pre-rule roster, including its leader, in the request transaction."""
    from app.services.field_service import field_staff_category, require_project_manager
    from app.services.field_roles import record_project_leader_change

    project = await require_project_manager(db, user, project_id)
    # Serialize two managers confirming the same project. The unique pending
    # person constraint remains a second line of defence in MySQL.
    project = await db.scalar(select(FieldProject).where(
        FieldProject.id == project.id).with_for_update().execution_options(populate_existing=True))
    if not project.is_active:
        raise HTTPException(422, "项目已停用")
    if await db.scalar(select(FieldSite.id).where(FieldSite.project_id == project_id).limit(1)):
        raise HTTPException(409, "项目已建立考勤规则，请使用现有人员维护和调动流程")

    requested_ids = [data.manager_id, *data.member_ids]
    people = {person.id: person for person in (await db.scalars(select(Employee).where(
        Employee.id.in_(requested_ids)))).all()}
    profiles = {row.employee_id: row.details or {} for row in (await db.scalars(
        select(FieldRosterProfile).where(FieldRosterProfile.employee_id.in_(requested_ids))
    )).all()}
    role_rows = (await db.execute(select(EmployeeRole.employee_id, EmployeeRole.role_name).where(
        EmployeeRole.employee_id.in_(requested_ids), EmployeeRole.is_active.is_(True)
    ))).all()
    active_roles = {}
    for employee_id, role_name in role_rows:
        active_roles.setdefault(employee_id, set()).add(role_name)
    incumbent_ids = set((await db.scalars(select(FieldProject.manager_id).where(
        FieldProject.manager_id.is_not(None)))).all())
    for employee_id in requested_ids:
        person = people.get(employee_id)
        if not person or not person.is_active or person.status not in {"在职", "试用"}:
            raise HTTPException(422, f"人员 {employee_id} 不在有效花名册中")
        category = field_staff_category(person, active_roles.get(employee_id, set()),
                                        profiles.get(employee_id), incumbent_ids)
        if employee_id == data.manager_id:
            if category != "leader_candidate" and employee_id != project.manager_id:
                raise HTTPException(422, f"{person.name} 不是项目组长候选")
        elif category != "member":
            raise HTTPException(422, f"{person.name} 不是组员候选")

    rows = await _rows(db, project_id)
    before = {"manager_id": project.manager_id,
              "member_ids": sorted(row.employee_id for row in rows if not row.is_leader_auto)}
    desired = set(requested_ids)
    existing = {row.employee_id: row for row in rows}
    removed = {row.employee_id for row in rows if row.employee_id not in desired}
    if removed and await db.scalar(select(RotationDraftDay.id).where(
            RotationDraftDay.project_id == project_id,
            RotationDraftDay.employee_id.in_(removed)).limit(1)):
        raise HTTPException(409, "拟移出的人员已有确认的个人排班；请先撤销其待生效排班再确认名单")
    if project.manager_id != data.manager_id:
        await record_project_leader_change(db, user, project, data.manager_id)
    for row in rows:
        if row.employee_id not in desired:
            await db.delete(row)
        else:
            row.is_leader_auto = row.employee_id == data.manager_id
    for employee_id in requested_ids:
        if employee_id not in existing:
            db.add(FieldProjectPendingMember(project_id=project_id, employee_id=employee_id,
                is_leader_auto=employee_id == data.manager_id, created_by=user.id))
    await db.flush()
    after = {"manager_id": data.manager_id, "member_ids": sorted(data.member_ids)}
    if before != after:
        db.add(AuditLog(operator_id=user.id, operator_name=user.name, module="field",
            action="confirm_pending_roster", resource_type="FieldProject", resource_id=project_id,
            before_data=json.dumps(before, ensure_ascii=False),
            after_data=json.dumps(after, ensure_ascii=False)))
    current = await _rows(db, project_id)
    return {"id": project_id, "manager_id": project.manager_id,
            "member_ids": after["member_ids"], "pending_member_count": len(current),
            "pending_members": [describe(row, people[row.employee_id].name) for row in current]}


async def stage_members(db, user, project_id, employee_ids, *, is_leader_auto=False):
    """An additive roster save; no assignment or permission is granted yet."""
    from app.services.field_service import require_project_manager

    project = await require_project_manager(db, user, project_id)
    if not project.is_active:
        raise HTTPException(422, "项目已停用")
    requested = sorted(set(employee_ids))
    for employee_id in requested:
        person = await lock_person(db, employee_id)
        if not person or not person.is_active or person.status not in {"在职", "试用"}:
            raise HTTPException(422, f"员工 {employee_id} 不在有效花名册中")
    existing = {row.employee_id: row for row in await _rows(db, project_id)}
    for employee_id in requested:
        if employee_id in existing:
            continue
        row = FieldProjectPendingMember(
            project_id=project_id, employee_id=employee_id,
            is_leader_auto=is_leader_auto, created_by=user.id,
        )
        db.add(row)
    await db.flush()
    names = dict((await db.execute(select(Employee.id, Employee.name).where(
        Employee.id.in_(requested)))).all())
    return {"members": [], "pending_members": [describe(row, names.get(row.employee_id, ""))
                                             for row in await _rows(db, project_id)]}


async def activate_members(db, user, project_id, *, fail_on_conflict=True):
    """Validate the complete roster before writing any participation interval."""
    from app.services.field_service import require_project_manager, project_site

    project = await require_project_manager(db, user, project_id)
    site = await project_site(db, project_id)
    rule = await db.get(AttendanceRule, site.rule_id) if site else None
    if not project.is_active or not site or not rule or not rule.is_active:
        raise HTTPException(422, "请先为项目配置有效的考勤规则")
    if project.manager_id is not None:
        # Older projects may predate the pending-roster table. Their appointed
        # leader still needs participation to appear on personal scheduling.
        leader = await db.get(Employee, project.manager_id)
        already = await db.scalar(select(FieldAssignment.id).join(
            FieldSite, FieldSite.id == FieldAssignment.site_id,
        ).where(FieldSite.project_id == project_id,
                FieldAssignment.employee_id == project.manager_id,
                FieldAssignment.status == "active").limit(1))
        if leader and not already and not await db.scalar(select(FieldProjectPendingMember.id).where(
                FieldProjectPendingMember.project_id == project_id,
                FieldProjectPendingMember.employee_id == leader.id)):
            db.add(FieldProjectPendingMember(project_id=project_id,
                employee_id=leader.id, is_leader_auto=True, created_by=user.id))
            await db.flush()
    rows = await _rows(db, project_id)
    if not rows:
        return {"members": [], "pending_members": []}

    now = clock_now()
    local_today = now.astimezone(CST).date()
    start_date = max(local_today, rule.effective_date)
    started_at = (now if start_date == local_today else
                  datetime.combine(start_date, time.min, CST).astimezone(timezone.utc))
    day_start = datetime.combine(start_date, time.min, CST).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)
    # SQLite has no SELECT FOR UPDATE, so serialise by employee row; transactions
    # using MySQL/Postgres also cannot activate the same person concurrently.
    people = {row.employee_id: await lock_person(db, row.employee_id) for row in rows}
    conflicts = []
    already_assigned = set()
    for row in rows:
        person = people[row.employee_id]
        if not person or not person.is_active or person.status not in {"在职", "试用"}:
            conflicts.append({"employee_id": row.employee_id,
                              "employee_name": person.name if person else str(row.employee_id),
                              "project_id": None, "project_name": "", "reason": "人员已停用或离职"})
            continue
        intervals = (await db.execute(select(FieldMembership, FieldSite, FieldProject)
            .join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
            .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
            .join(FieldProject, FieldProject.id == FieldSite.project_id)
            .where(FieldMembership.employee_id == row.employee_id,
                   or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > day_start)))).all()
        own = False
        for membership, other_site, other_project in intervals:
            # Any future or current participation outside this project conflicts;
            # the current calendar day also cannot have two main projects.
            if other_project.id == project_id and membership.ended_at is None:
                own = True
            elif other_project.id != project_id:
                conflicts.append({"employee_id": row.employee_id,
                    "employee_name": person.name, "project_id": other_project.id,
                    "project_name": other_project.name,
                    "reason": "已在其他项目或存在未来项目安排"})
                break
        if not own and not any(c["employee_id"] == row.employee_id for c in conflicts):
            earlier = (await db.execute(select(FieldMembership, FieldProject)
                .join(FieldAssignment, FieldAssignment.id == FieldMembership.assignment_id)
                .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
                .join(FieldProject, FieldProject.id == FieldSite.project_id)
                .where(FieldMembership.employee_id == row.employee_id,
                       FieldMembership.started_at < day_end,
                       or_(FieldMembership.ended_at.is_(None), FieldMembership.ended_at > day_start)))).first()
            if earlier and earlier[1].id != project_id:
                conflicts.append({"employee_id": row.employee_id,
                    "employee_name": person.name, "project_id": earlier[1].id,
                    "project_name": earlier[1].name,
                    "reason": "生效当天已归属其他项目"})
        if own:
            already_assigned.add(row.employee_id)
    if conflicts:
        detail = {"message": "项目人员暂未生效，请先处理以下冲突后重试", "conflicts": conflicts}
        if fail_on_conflict:
            raise HTTPException(409, detail)
        names = {eid: person.name for eid, person in people.items() if person}
        return {"members": [], "pending_members": [describe(row, names.get(row.employee_id, "")) for row in rows],
                "conflicts": conflicts}

    members = []
    for row in rows:
        if row.employee_id not in already_assigned:
            assignment = FieldAssignment(site_id=site.id, employee_id=row.employee_id,
                start_date=start_date, end_date=date(9999, 12, 30), created_by=user.id,
                status="active")
            db.add(assignment)
            await db.flush()
            db.add(FieldMembership(assignment_id=assignment.id,
                employee_id=row.employee_id, started_at=started_at,
                active_employee_id=row.employee_id))
            await db.flush()
            members.append({"id": assignment.id, "employee_id": row.employee_id,
                            "project_id": project_id, "started_at": started_at})
        await db.delete(row)
    await db.flush()
    return {"members": members, "pending_members": []}


async def remove_pending(db, user, project_id, pending_id):
    from app.services.field_service import require_project_manager
    project = await require_project_manager(db, user, project_id)
    row = await db.get(FieldProjectPendingMember, pending_id)
    if not row or row.project_id != project_id:
        raise HTTPException(404, "待配置人员不存在")
    if row.employee_id == project.manager_id:
        raise HTTPException(409, "项目组长已被任命，请先更换项目组长")
    if await db.scalar(select(RotationDraftDay.id).where(
            RotationDraftDay.project_id == project_id,
            RotationDraftDay.employee_id == row.employee_id).limit(1)):
        raise HTTPException(409, "该人员已有确认的个人排班，请先撤销待生效排班")
    await db.delete(row)
    await db.flush()
    return {"deleted": True, "id": pending_id}


async def sync_project_leader(db, user, project, previous_employee_id):
    """Keep automatic pending rows aligned with the current appointment."""
    if previous_employee_id and previous_employee_id != project.manager_id:
        if await db.scalar(select(RotationDraftDay.id).where(
                RotationDraftDay.project_id == project.id,
                RotationDraftDay.employee_id == previous_employee_id).limit(1)):
            raise HTTPException(409, "原项目组长已有确认的个人排班，请先撤销待生效排班")
        await db.execute(delete(FieldProjectPendingMember).where(
            FieldProjectPendingMember.project_id == project.id,
            FieldProjectPendingMember.employee_id == previous_employee_id,
            FieldProjectPendingMember.is_leader_auto.is_(True)))
    if project.manager_id is None:
        return
    existing = await db.scalar(select(FieldAssignment.id).join(FieldSite,
        FieldSite.id == FieldAssignment.site_id).where(
        FieldSite.project_id == project.id,
        FieldAssignment.employee_id == project.manager_id,
        FieldAssignment.status == "active").limit(1))
    if existing:
        return
    await stage_members(db, user, project.id, [project.manager_id], is_leader_auto=True)
    from app.services.field_service import project_site
    if await project_site(db, project.id):
        await activate_members(db, user, project.id, fail_on_conflict=False)


async def first_published_rotation_date(db, employee_id, project_id):
    from app.models.rotation import RotationDay
    return await db.scalar(select(RotationDay.work_date).where(
        RotationDay.employee_id == employee_id,
        RotationDay.project_id == project_id,
    ).order_by(RotationDay.work_date).limit(1))


async def rotation_punch_ready(db, employee_id, project_id, day):
    first = await first_published_rotation_date(db, employee_id, project_id)
    return first is not None and first <= day
