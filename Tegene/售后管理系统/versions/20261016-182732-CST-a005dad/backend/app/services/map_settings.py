"""Resolve the browser map key without exposing the server-side WebService key."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.system_setting import SystemSetting

BROWSER_KEY_NAME = "tencent_map_browser_key"


async def browser_map_key(db: AsyncSession) -> tuple[str, str]:
    saved = await db.get(SystemSetting, BROWSER_KEY_NAME)
    if saved:
        return saved.value, "platform"
    key = (settings.TENCENT_MAP_BROWSER_KEY or "").strip()
    return (key, "environment") if key else ("", "none")


async def browser_map_status(db: AsyncSession) -> dict[str, str | bool]:
    key, source = await browser_map_key(db)
    return {
        "configured": bool(key),
        "source": source,
        "masked_key": ("••••••••" + key[-4:]) if key else "",
        "has_override": source == "platform",
    }
