from datetime import date
from fastapi import APIRouter, Depends, Query
from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.rotation import RotationPublish, RotationAdjust, ClaimInput, ClaimReview
from app.services import rotation as service

router = APIRouter()


@router.get("/calendar")
async def calendar(start: date, end: date, project_id: int | None = None, employee_id: int | None = None,
                   db=Depends(get_db), user=Depends(get_current_user)):
    return await service.calendar(db, user, start, end, project_id, employee_id)


@router.post("/projects/{project_id}/preview")
async def preview(project_id: int, data: RotationPublish, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.preview(db, user, project_id, data)


@router.post("/projects/{project_id}/publish")
async def publish(project_id: int, data: RotationPublish, revision: str = Query(...),
                  db=Depends(get_db), user=Depends(get_current_user)):
    return await service.publish(db, user, project_id, data, revision)


@router.post("/projects/{project_id}/adjust")
async def adjust(project_id: int, data: RotationAdjust, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.adjust(db, user, project_id, data)


@router.post("/claims")
async def request(data: ClaimInput, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.create_claim(db, user, data)


@router.post("/claims/{claim_id}/review")
async def review(claim_id: int, data: ClaimReview, db=Depends(get_db), user=Depends(get_current_user)):
    return await service.review(db, user, claim_id, data)
