"""Field-specific authority. The legacy global ``manager`` role grants no field scope."""

import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.field_service import (
    FieldAssignment,
    FieldMembership,
    FieldProject,
    FieldProjectLeaderHistory,
    FieldRegionalAssignment,
)


async def role_names(db, user):
    if isinstance(user, int):
        user = await db.get(Employee, user)
    if user is None or not user.is_active or user.status not in {"在职", "试用"}:
        return set()
    names = set((await db.scalars(select(EmployeeRole.role_name).where(
        EmployeeRole.employee_id == user.id, EmployeeRole.is_active.is_(True)
    ))).all())
    if user.is_superuser:
        names.add("admin")
    return names


async def is_aftersales_manager(db, user):
    return "aftersales_manager" in await role_names(db, user)


async def aftersales_manager_ids(db):
    return list((await db.scalars(select(EmployeeRole.employee_id)
        .join(Employee, Employee.id == EmployeeRole.employee_id)
        .where(EmployeeRole.role_name == "aftersales_manager", EmployeeRole.is_active.is_(True),
               Employee.is_active.is_(True), Employee.status.in_(("在职", "试用"))))).all())


async def is_aftersales_person(db, employee_id):
    return await db.scalar(aftersales_person_ids().where(Employee.id == employee_id).limit(1)) is not None


def aftersales_person_ids():
    """SQL scope for managers who may read only aftersales personnel."""
    return select(Employee.id).where(or_(
        Employee.is_field_staff.is_(True),
        Employee.id.in_(select(EmployeeRole.employee_id).where(
            EmployeeRole.role_name.in_(("aftersales_manager", "regional_lead")),
            EmployeeRole.is_active.is_(True))),
        Employee.id.in_(select(FieldProject.manager_id).where(FieldProject.manager_id.is_not(None))),
        Employee.id.in_(select(FieldProjectLeaderHistory.employee_id)),
        Employee.id.in_(select(FieldMembership.employee_id)),
    ))


async def require_aftersales_approver(db, user, note=None):
    if isinstance(user, int):
        user = await db.get(Employee, user)
    names = await role_names(db, user)
    if "admin" in names:
        if not (note or "").strip():
            raise HTTPException(422, "管理员代审须填写原因")
        return "admin"
    if "aftersales_manager" in names:
        return "aftersales_manager"
    raise HTTPException(403, "仅售后经理可审批售后申请")


async def is_project_leader(db, user, project_id):
    project = await db.get(FieldProject, project_id)
    return bool(project and project.manager_id == getattr(user, "id", user))


async def regional_assignments(db, project_id=None, employee_id=None):
    stmt = select(FieldRegionalAssignment).where(FieldRegionalAssignment.revoked_at.is_(None))
    if project_id is not None:
        stmt = stmt.where(FieldRegionalAssignment.project_id == project_id)
    if employee_id is not None:
        stmt = stmt.where(FieldRegionalAssignment.employee_id == employee_id)
    return list((await db.scalars(stmt.order_by(FieldRegionalAssignment.id))).all())


async def primary_regional_lead_id(db, project_id):
    row = await db.scalar(select(FieldRegionalAssignment.employee_id).where(
        FieldRegionalAssignment.project_id == project_id,
        FieldRegionalAssignment.kind == "primary",
        FieldRegionalAssignment.revoked_at.is_(None)).limit(1))
    return row


async def role_in_project(db, user, project_id):
    names = await role_names(db, user)
    if "admin" in names:
        return "admin"
    if "aftersales_manager" in names:
        return "aftersales_manager"
    if "regional_lead" in names:
        assigned = await regional_assignments(db, project_id, user.id)
        if assigned:
            return "regional_lead"
    if await is_project_leader(db, user, project_id):
        return "project_leader"
    from app.models.field_service import FieldSite
    own = await db.scalar(select(FieldAssignment.id).join(FieldSite, FieldSite.id == FieldAssignment.site_id)
        .where(FieldAssignment.employee_id == user.id, FieldSite.project_id == project_id).limit(1))
    return "member" if own else "none"


async def can_view_project(db, user, project_id):
    names = await role_names(db, user)
    if names & {"admin", "hr", "aftersales_manager", "regional_lead"}:
        return True
    return await role_in_project(db, user, project_id) in {"project_leader", "member"}


async def require_project_read(db, user, project_id):
    project = await db.get(FieldProject, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    if not await can_view_project(db, user, project_id):
        raise HTTPException(403, "无权查看该项目")
    return project


async def require_project_manage(db, user, project_id):
    project = await db.get(FieldProject, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    if await role_in_project(db, user, project_id) not in {"admin", "aftersales_manager", "regional_lead"}:
        raise HTTPException(403, "无权管理该项目")
    return project


async def require_project_schedule_manager(db, user, project_id, employee_id=None):
    project = await db.get(FieldProject, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    role = await role_in_project(db, user, project_id)
    if role not in {"admin", "aftersales_manager", "regional_lead", "project_leader"}:
        raise HTTPException(403, "无权为该项目排班")
    if employee_id is not None and role == "project_leader":
        # A project leader may schedule themselves or active/future participants.
        from app.models.field_service import FieldSite
        if employee_id != user.id and not await db.scalar(select(FieldAssignment.id)
                .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
                .where(FieldAssignment.employee_id == employee_id,
                       FieldSite.project_id == project_id).limit(1)):
            raise HTTPException(403, "只能为本项目组员排班")
    return project


async def set_project_regional_leads(db, user, project_id, primary_lead_id, assistant_lead_ids, reason=""):
    project = await db.get(FieldProject, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    if not await role_names(db, user) & {"admin", "aftersales_manager"}:
        raise HTTPException(403, "仅售后经理可分配区域组长")
    desired = {int(eid): "assistant" for eid in assistant_lead_ids}
    if primary_lead_id is not None:
        desired[int(primary_lead_id)] = "primary"
    if any(eid <= 0 for eid in desired) or len(desired) != len(assistant_lead_ids) + int(primary_lead_id is not None):
        raise HTTPException(422, "主责与协管区域组长不可重复")
    for eid in desired:
        person = await db.get(Employee, eid)
        if (not person or not person.is_active or person.status not in {"在职", "试用"}
                or "regional_lead" not in await role_names(db, person)):
            raise HTTPException(422, "请选择在职且具有区域组长角色的人员")
    existing = await regional_assignments(db, project_id)
    before = {row.employee_id: row.kind for row in existing}
    if before == desired:
        return {"project_id": project_id, "primary_lead_id": primary_lead_id,
                "assistant_lead_ids": sorted(assistant_lead_ids)}
    now = datetime.now(timezone.utc)
    for row in existing:
        if desired.get(row.employee_id) != row.kind:
            row.revoked_at = now
            row.revoked_by = user.id
            row.active_employee_id = None
            row.active_primary_project_id = None
    await db.flush()
    for eid, kind in desired.items():
        if before.get(eid) != kind:
            db.add(FieldRegionalAssignment(project_id=project_id, employee_id=eid,
                kind=kind, active_employee_id=eid,
                active_primary_project_id=project_id if kind == "primary" else None,
                appointed_by=user.id, appointed_at=now, reason=reason.strip()))
    await db.flush()
    from app.models.audit_log import AuditLog
    db.add(AuditLog(operator_id=user.id, operator_name=user.name,
                    action="set_project_regional_leads", module="field",
                    resource_type="FieldProject", resource_id=project_id,
                    before_data=json.dumps(before, ensure_ascii=False),
                    after_data=json.dumps({"assignments": desired, "reason": reason.strip()}, ensure_ascii=False)))
    return {"project_id": project_id, "primary_lead_id": primary_lead_id,
            "assistant_lead_ids": sorted(assistant_lead_ids)}


async def record_project_leader_change(db, user, project, new_employee_id):
    if project.manager_id != new_employee_id:
        db.add(FieldProjectLeaderHistory(project_id=project.id,
            previous_employee_id=project.manager_id, employee_id=new_employee_id,
            changed_by=user.id))
        project.manager_id = new_employee_id
        await db.flush()
