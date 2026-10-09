"""Standalone aftersales API. Run from backend with uvicorn app.main:app."""

import asyncio, contextlib, logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy import select, text, cast, String
from sqlalchemy.exc import IntegrityError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.config import settings, BACKEND_DIR
from app.core.database import engine, get_db, async_session
from app.core.limiter import limiter
from app.core.deps import get_current_user
from app.api.v1.router import router
from app.models.field_service import (
    FieldFile,
    FieldSchemaVersion,
    FieldAssignment,
    FieldSite,
    FieldProject,
)
from app.models.approval import ApprovalInstance
import app.core.period_guard
from app.services.field_service import is_admin

logger = logging.getLogger(__name__)


async def dispatch_loop():
    from app.services.event_dispatcher import dispatch_pending
    from app.services.field_jobs import process_recalculation

    while True:
        try:
            async with async_session() as db:
                await dispatch_pending(db, limit=50)
                await db.commit()
            await process_recalculation()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Approval event dispatch failed; retry in next cycle")
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app):
    # Schema changes are explicit via bootstrap; startup verifies the baseline exists.
    async with async_session() as db:
        if not await db.get(FieldSchemaVersion, 8):
            raise RuntimeError("Run python -m app.bootstrap to apply schema version 8 first")
    worker = asyncio.create_task(dispatch_loop()) if settings.BACKGROUND_JOBS else None
    yield
    if worker:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    logger.warning("Database constraint rejected a write: %s", type(exc.orig).__name__)
    return JSONResponse(
        status_code=409,
        content={"detail": "数据冲突，请检查重复编号、重叠排班或被使用的记录"},
    )


app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health():
    async with async_session() as db:
        await db.execute(text("SELECT 1"))
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


@app.get("/uploads/{file_path:path}")
async def download(file_path: str, request: Request, db=Depends(get_db)):
    auth = request.headers.get("authorization", "")
    token = request.query_params.get("token") or (
        auth[7:] if auth.lower().startswith("bearer ") else ""
    )
    if not token:
        raise HTTPException(401, "请登录后访问文件")
    user = await get_current_user(db, token)
    upload_root = (BACKEND_DIR / "uploads").resolve()
    target = (upload_root / file_path).resolve()
    if upload_root not in target.parents or not target.is_file():
        raise HTTPException(404, "文件不存在")
    row = await db.scalar(
        select(FieldFile).where(FieldFile.path == "/uploads/" + file_path)
    )
    if not row:
        raise HTTPException(404, "文件不存在")
    allowed = user.id == row.owner_id or await is_admin(db, user)
    if not allowed and file_path.startswith("attendance/"):
        allowed = bool(
            await db.scalar(
                select(FieldAssignment.id)
                .join(FieldSite, FieldSite.id == FieldAssignment.site_id)
                .join(FieldProject, FieldProject.id == FieldSite.project_id)
                .where(
                    FieldAssignment.employee_id == row.owner_id,
                    FieldProject.manager_id == user.id,
                )
                .limit(1)
            )
        )
    if not allowed:
        from app.services.approval import user_can_view_instance

        candidates = (
            await db.scalars(
                select(ApprovalInstance).where(
                    cast(ApprovalInstance.form_data, String).contains(row.path)
                )
            )
        ).all()
        for instance in candidates:
            if await user_can_view_instance(db, instance, user.id):
                allowed = True
                break
    if not allowed:
        raise HTTPException(403, "无权访问该文件")
    return FileResponse(
        target,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )
