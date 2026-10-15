"""Small department position directory, including positions already used in the roster."""
import json
from fastapi import HTTPException
from sqlalchemy import select
from app.models.field_service import FieldPosition
from app.models.organization import Department
from app.models.audit_log import AuditLog
from app.services.field_service import require_admin, list_people


async def list_positions(db, user):
    await require_admin(db, user)
    departments = {d.id: d for d in (await db.scalars(select(Department).where(Department.is_active.is_(True)))).all()}
    rows = {}
    for position in (await db.scalars(select(FieldPosition))).all():
        if position.department_id in departments:
            rows[(position.department_id, position.name)] = []
    for person in await list_people(db, user):
        name = (person.get("position") or "").strip()
        if not name:
            continue
        department_id = person.get("department_id")
        if department_id not in departments:
            matches = [d.id for d in departments.values() if d.name == person.get("department_name")]
            department_id = matches[0] if len(matches) == 1 else None
        members = rows.setdefault((department_id, name), [])
        if person.get("is_active") and person.get("status") in {"在职", "试用"}:
            members.append({"id": person["id"], "name": person["name"]})
    return [{"department_id": dept, "department_name": departments[dept].name if dept else "未关联部门",
             "name": name, "member_count": len(members), "members": members}
            for (dept, name), members in sorted(rows.items(), key=lambda pair: (pair[0][0] or 0, pair[0][1]))]


async def create_position(db, user, data):
    await require_admin(db, user)
    department = await db.get(Department, data.department_id)
    if not department or not department.is_active:
        raise HTTPException(422, "请选择有效部门")
    existing = await db.scalar(select(FieldPosition).where(FieldPosition.department_id == data.department_id, FieldPosition.name == data.name))
    if not existing:
        existing = FieldPosition(department_id=data.department_id, name=data.name)
        db.add(existing)
        await db.flush()
        db.add(AuditLog(operator_id=user.id, operator_name=user.name, module="organization", action="create",
                        resource_type="FieldPosition", resource_id=existing.id,
                        after_data=json.dumps({"department_name": department.name, "name": data.name}, ensure_ascii=False)))
        await db.flush()
    return {"id": existing.id, "department_id": existing.department_id, "name": existing.name}
