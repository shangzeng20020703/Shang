"""Pre-rule project roster and atomic activation into attendance memberships."""

from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, or_, select

from app.models.attendance import AttendanceRule
from app.models.employee import Employee
from app.models.field_service import (
    FieldAssignment, FieldMembership, FieldProject, FieldProjectPendingMember, FieldSite,
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
    await db.delete(row)
    await db.flush()
    return {"deleted": True, "id": pending_id}


async def sync_project_leader(db, user, project, previous_employee_id):
    """Keep automatic pending rows aligned with the current appointment."""
    if previous_employee_id and previous_employee_id != project.manager_id:
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
