from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from app.core.database import get_db
from app.core.limiter import limiter
from app.services import wecom_auth as service

router = APIRouter()


class StartInput(BaseModel):
    client: Literal["web", "mobile"]
    environment: Literal["web", "in_app"]
    challenge: str = Field(pattern=r"^[a-f0-9]{64}$")


class CompleteInput(BaseModel):
    client: Literal["web", "mobile"]
    state: str = Field(min_length=32, max_length=128)
    verifier: str = Field(min_length=43, max_length=128)
    code: str = Field(min_length=1, max_length=512)


@router.get("/policy")
async def policy():
    return service.policy()


@router.post("/wecom/start")
@limiter.limit("20/minute")
async def start(request: Request, data: StartInput, db=Depends(get_db)):
    return await service.begin(db, data.client, data.environment, data.challenge)


@router.post("/wecom/complete")
@limiter.limit("20/minute")
async def complete(request: Request, data: CompleteInput, db=Depends(get_db)):
    return await service.complete(db, data.client, data.state, data.verifier, data.code)
