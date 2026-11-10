from datetime import date
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Literal
from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.rotation import RotationPublish, RotationAdjust, ClaimInput, ClaimReview, PersonPlanInput, PersonPlanBind
from app.services import rotation as service

router = APIRouter()


class EntitlementPolicyInput(BaseModel):
    effective_date: date


class WorkDayReviewInput(BaseModel):
    employee_id: int = Field(gt=0)
    action: Literal["approve", "reject"]
    work_days: Literal[0, 0.5, 1] = 0
    note: str = Field(min_length=1, max_length=500)


@router.get("/entitlement/policy")
async def entitlement_policy(db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_entitlement import policy_view
    return await policy_view(db, user)


@router.put("/entitlement/policy")
async def set_entitlement_policy(data: EntitlementPolicyInput, db=Depends(get_db),
                                 user=Depends(get_current_user)):
    from app.services.rotation_entitlement import set_policy
    return await set_policy(db, user, data.effective_date)


@router.get("/me/entitlement")
async def my_entitlement(db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_entitlement import my_entitlement as read_entitlement
    return await read_entitlement(db, user)


@router.get("/entitlement/pending")
async def entitlement_pending(db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_entitlement import pending_work_days
    return await pending_work_days(db, user)


@router.post("/entitlement/days/{work_date}/review")
async def review_entitlement_day(work_date: date, data: WorkDayReviewInput,
                                 db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_entitlement import review_work_day
    return await review_work_day(db, user, data.employee_id, work_date,
                                 data.action, data.work_days, data.note)


@router.get("/calendar")
async def calendar(start: date, end: date, project_id: int | None = None, employee_id: int | None = None,
                   db=Depends(get_db), user=Depends(get_current_user)):
    return await service.calendar(db, user, start, end, project_id, employee_id)


@router.get("/projects/{project_id}/person-plans/{employee_id}")
async def person_plans(project_id: int, employee_id: int, db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_person_plan import get_plans
    return await get_plans(db, user, project_id, employee_id)


@router.post("/projects/{project_id}/person-plans/{employee_id}/preview")
async def preview_person_plan(project_id: int, employee_id: int, data: PersonPlanInput,
                              db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_person_plan import preview
    return await preview(db, user, project_id, employee_id, data)


@router.put("/projects/{project_id}/person-plans/{employee_id}/confirm")
async def confirm_person_plan(project_id: int, employee_id: int, data: PersonPlanInput,
                              revision: str = Query(...), db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_person_plan import confirm
    return await confirm(db, user, project_id, employee_id, data, revision)


@router.post("/projects/{project_id}/person-plans/{employee_id}/bind")
async def bind_person_plan(project_id: int, employee_id: int, data: PersonPlanBind,
                           db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_person_plan import bind_pending_project
    return await bind_pending_project(db, user, project_id, employee_id, data)


@router.post("/projects/{project_id}/preview")
async def preview(project_id: int, data: RotationPublish, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.preview(db, user, project_id, data)


@router.post("/projects/{project_id}/publish")
async def publish(project_id: int, data: RotationPublish, revision: str = Query(...),
                  db=Depends(get_db), user=Depends(get_current_user)):
    return await service.publish(db, user, project_id, data, revision)


@router.delete("/projects/{project_id}/draft-days")
async def cancel_draft_days(project_id: int, employee_id: int = Query(..., gt=0),
                            db=Depends(get_db), user=Depends(get_current_user)):
    from app.services.rotation_draft import cancel_person
    return await cancel_person(db, user, project_id, employee_id)


@router.post("/projects/{project_id}/adjust")
async def adjust(project_id: int, data: RotationAdjust, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.adjust(db, user, project_id, data)


@router.post("/claims")
async def request(data: ClaimInput, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.create_claim(db, user, data)


@router.post("/claims/{claim_id}/review")
async def review(claim_id: int, data: ClaimReview, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.review(db, user, claim_id, data)
