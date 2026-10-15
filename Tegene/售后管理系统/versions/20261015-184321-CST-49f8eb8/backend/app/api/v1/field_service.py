from datetime import date
from uuid import UUID
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi import APIRouter, Depends, Query, UploadFile, File, Response
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.schemas.field_service import (
    CustomerInput,
    ProjectInput,
    SiteInput,
    AssignmentInput,
    TegeneProjectInput,
    ProjectConfigurationInput,
    ProjectMembersInput,
)
from app.services import field_service as service
from app.services import tegene_projects
from app.services.field_roster import import_roster
from app.services import field_membership
from app.schemas.field_service import TransferInput
from app.schemas.field_roster import ROSTER_COLUMNS

router = APIRouter()


@router.get("/tegene/projects")
async def tegene_project_list(db=Depends(get_db), user=Depends(get_current_user)):
    return await tegene_projects.list_projects(db, user)


@router.post("/tegene/projects/{external_id}/link")
async def tegene_project_link(external_id: UUID, data: TegeneProjectInput,
                              db=Depends(get_db), user=Depends(get_current_user)):
    return await tegene_projects.link_project(db, user, external_id, data)


@router.post("/tegene/projects/{external_id}/configure")
async def configure_tegene_project(external_id: UUID, data: ProjectConfigurationInput,
                                  db=Depends(get_db), user=Depends(get_current_user)):
    await service.require_admin(db, user)
    if data.manager_id is None:
        raise HTTPException(422, "请先指定项目负责人")
    linked = await tegene_projects.link_project(db, user, external_id, data)
    return await service.configure_project(db, user, linked["project_id"], data)


@router.put("/projects/{project_id}/configuration")
async def configure_project(project_id: int, data: ProjectConfigurationInput,
                            db=Depends(get_db), user=Depends(get_current_user)):
    return await service.configure_project(db, user, project_id, data)


@router.post("/projects/{project_id}/members")
async def add_project_members(project_id: int, data: ProjectMembersInput,
                              db=Depends(get_db), user=Depends(get_current_user)):
    return await service.add_project_members(db, user, project_id, data)


@router.post("/projects/{project_id}/members/import")
async def import_project_members(project_id: int, file: UploadFile = File(...), preview: bool = True,
                                 db=Depends(get_db), user=Depends(get_current_user)):
    content = await file.read(5 * 1024 * 1024 + 1)
    operation = service.audited("import-members", field_membership.import_members)
    return await operation(db, user, project_id, content, file.filename, preview)


@router.post("/assignments/{item_id}/remove")
async def remove_member(item_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.audited("remove-member", field_membership.remove_or_transfer)(db, user, item_id)


@router.post("/assignments/{item_id}/transfer")
async def transfer_member(item_id: int, data: TransferInput, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.audited("transfer-member", field_membership.remove_or_transfer)(db, user, item_id, data.project_id)


@router.get("/directory")
async def directory(db=Depends(get_db), user=Depends(get_current_user)):
    return await service.directory(db, user)


@router.get("/dashboard")
async def dashboard(
    day: date | None = None, project_id: int | None = Query(None, gt=0),
    db=Depends(get_db), user=Depends(require_roles("admin", "hr", "manager"))
):
    return await service.dashboard(db, user, day or service.today(), project_id, include_trend=True)


@router.get('/team-attendance')
async def team_attendance(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    db=Depends(get_db), user=Depends(get_current_user),
):
    return await service.team_attendance(db, user, year, month)


@router.post("/customers")
async def create_customer(
    data: CustomerInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.save_customer(db, user, data)


@router.put("/customers/{item_id}")
async def update_customer(
    item_id: int,
    data: CustomerInput,
    db=Depends(get_db),
    user=Depends(get_current_user),
):
    return await service.save_customer(db, user, data, item_id)


@router.post("/projects")
async def create_project(
    data: ProjectInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.save_project(db, user, data)


@router.put("/projects/{item_id}")
async def update_project(
    item_id: int, data: ProjectInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.save_project(db, user, data, item_id)


@router.post("/sites")
async def create_site(
    data: SiteInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.save_site(db, user, data)


@router.put("/sites/{item_id}")
async def update_site(
    item_id: int, data: SiteInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.save_site(db, user, data, item_id)


@router.post("/assignments")
async def create_assignment(
    data: AssignmentInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.create_assignment(db, user, data)


@router.post("/assignments/{item_id}/cancel")
async def cancel_assignment(
    item_id: int, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.cancel_assignment(db, user, item_id)


from app.schemas.field_service import PersonInput


@router.get("/people")
async def people(db=Depends(get_db), user=Depends(get_current_user)):
    return await service.list_people(db, user)


@router.get("/roster/template")
async def roster_template(db=Depends(get_db), user=Depends(get_current_user)):
    await service.require_admin(db, user)
    return FileResponse(Path(__file__).resolve().parents[2] / "templates" / "aftersales-roster.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename="售后名册模板.xlsx")


@router.get("/roster/columns")
async def roster_columns(db=Depends(get_db), user=Depends(get_current_user)):
    await service.require_admin(db, user)
    return [{"key": key, "label": label, "kind": kind} for key, label, kind in ROSTER_COLUMNS]


@router.post("/roster/import")
async def roster_import(file: UploadFile = File(...), preview: bool = True,
                        db=Depends(get_db), user=Depends(get_current_user)):
    await service.require_admin(db, user)
    content = await file.read(5 * 1024 * 1024 + 1)
    return await import_roster(db, user, content, file.filename, preview)


@router.post("/people")
async def create_person(
    data: PersonInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.save_person(db, user, data)


@router.put("/people/{item_id}")
async def update_person(
    item_id: int, data: PersonInput, db=Depends(get_db), user=Depends(get_current_user)
):
    return await service.save_person(db, user, data, item_id)


from sqlalchemy import select
from fastapi import HTTPException
from app.models.field_service import FieldTemplateDraft


async def check_draft_access(db, user, template_id):
    await service.require_admin(db, user)
    if template_id:
        from app.services.approval import (
            get_approval_type,
            _ensure_template_manage_allowed,
        )

        template = await get_approval_type(db, template_id)
        await _ensure_template_manage_allowed(db, template, user)


@router.get("/template-drafts/{template_id}")
async def read_template_draft(
    template_id: int, db=Depends(get_db), user=Depends(get_current_user)
):
    await check_draft_access(db, user, template_id)
    row = await db.scalar(
        select(FieldTemplateDraft).where(
            FieldTemplateDraft.owner_id == user.id,
            FieldTemplateDraft.template_id == template_id,
        )
    )
    return {
        "data": row.data if row else None,
        "updated_at": row.updated_at if row else None,
    }


@router.put("/template-drafts/{template_id}")
async def save_template_draft(
    template_id: int, data: dict, db=Depends(get_db), user=Depends(get_current_user)
):
    await check_draft_access(db, user, template_id)
    import json

    if len(json.dumps(data)) > 2_000_000:
        raise HTTPException(413, "模板草稿过大")
    row = await db.scalar(
        select(FieldTemplateDraft).where(
            FieldTemplateDraft.owner_id == user.id,
            FieldTemplateDraft.template_id == template_id,
        )
    )
    if row is None:
        row = FieldTemplateDraft(owner_id=user.id, template_id=template_id)
        db.add(row)
    row.data = data
    await db.flush()
    return {"saved": True, "updated_at": row.updated_at}


@router.delete("/template-drafts/{template_id}")
async def delete_template_draft(
    template_id: int, db=Depends(get_db), user=Depends(get_current_user)
):
    await check_draft_access(db, user, template_id)
    row = await db.scalar(
        select(FieldTemplateDraft).where(
            FieldTemplateDraft.owner_id == user.id,
            FieldTemplateDraft.template_id == template_id,
        )
    )
    if row:
        await db.delete(row)
    return {"deleted": True}


from app.schemas.field_service import PositionInput
from app.services import field_positions


@router.get("/positions")
async def positions(db=Depends(get_db), user=Depends(get_current_user)):
    return await field_positions.list_positions(db, user)


@router.post("/positions", status_code=201)
async def create_position(data: PositionInput, db=Depends(get_db), user=Depends(get_current_user)):
    return await field_positions.create_position(db, user, data)
