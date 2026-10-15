"""Administrator-only runtime configuration for the browser map SDK."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.system_setting import SystemSetting
from app.services.map_settings import BROWSER_KEY_NAME, browser_map_status

router = APIRouter()


class BrowserMapKeyUpdate(BaseModel):
    browser_key: str


def audit_change(db: AsyncSession, user: Employee, before: dict, after: dict, action: str) -> None:
    # Audit only the source and presence: neither the new nor the old key belongs in logs.
    def snapshot(status: dict) -> dict:
        return {
            "configured": status["configured"],
            "configuration_source": status["source"],
            "has_override": status["has_override"],
        }

    db.add(AuditLog(
        operator_id=user.id,
        operator_name=user.name,
        action=action,
        module="system",
        resource_type="TencentBrowserMapKey",
        before_data=json.dumps(snapshot(before)),
        after_data=json.dumps(snapshot(after)),
    ))


@router.get("/tencent-map")
async def get_tencent_map_setting(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin")),
):
    return await browser_map_status(db)


@router.put("/tencent-map")
async def update_tencent_map_setting(
    data: BrowserMapKeyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin")),
):
    key = data.browser_key.strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{10,128}", key):
        raise HTTPException(422, "请输入有效的腾讯地图浏览器 Key")
    before = await browser_map_status(db)
    saved = await db.get(SystemSetting, BROWSER_KEY_NAME)
    if saved:
        saved.value = key
    else:
        db.add(SystemSetting(name=BROWSER_KEY_NAME, value=key))
    await db.flush()
    after = await browser_map_status(db)
    audit_change(db, current_user, before, after, "update")
    return after


@router.delete("/tencent-map")
async def clear_tencent_map_setting(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin")),
):
    before = await browser_map_status(db)
    saved = await db.get(SystemSetting, BROWSER_KEY_NAME)
    if saved:
        await db.delete(saved)
        await db.flush()
        after = await browser_map_status(db)
        audit_change(db, current_user, before, after, "delete")
        return after
    return before
