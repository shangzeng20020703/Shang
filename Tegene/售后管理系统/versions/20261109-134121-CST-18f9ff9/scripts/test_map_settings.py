"""Isolated browser map setting acceptance against a temporary SQLite database."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from httpx import ASGITransport, AsyncClient

temp_db = tempfile.TemporaryDirectory(prefix="aftersales-map-settings-")
os.environ.update(
    DATABASE_URL_OVERRIDE=f"sqlite+aiosqlite:///{temp_db.name}/test.db",
    BACKGROUND_JOBS="false",
    ENVIRONMENT="development",
    TENCENT_MAP_BROWSER_KEY="ENVVV-ENVVV-ENVVV-ENVVV-ENVVV-ENVVV",
    TENCENT_MAP_WEB_SERVICE_KEY="SERVER-ONLY-PRIVATE-KEY",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bootstrap import initialize, seed
from app.core.config import settings
from app.core.database import async_session, engine
from app.main import app
from app.models.audit_log import AuditLog
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.services.map_settings import browser_map_key
from sqlalchemy import select

OVERRIDE = "PLATF-PLATF-PLATF-PLATF-PLATF-PLATF"
REPLACEMENT = "NEWER-NEWER-NEWER-NEWER-NEWER-NEWER"


async def main():
    await initialize()
    await seed(True)
    app.state.limiter.enabled = False
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def request(method, path, token=None, expected=200, **kwargs):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await client.request(method, "/api/v1" + path, headers=headers, **kwargs)
            assert response.status_code == expected, (method, path, response.status_code, response.text[:300])
            return response.json() if response.content else None

        admin = (await request("POST", "/auth/management-login", json={"phone": "19900000001", "password": "Demo-AfterSales-2026!"}))["access_token"]
        staff = (await request("POST", "/auth/login", json={"phone": "19900000003", "password": "Demo-AfterSales-2026!"}))["access_token"]
        async with async_session() as db:
            manager = await db.scalar(select(Employee).where(Employee.employee_no == "DEMO-MANAGER"))
            db.add(EmployeeRole(employee_id=manager.id, role_name="hr", granted_by=manager.id))
            await db.commit()
        hr = (await request("POST", "/auth/management-login", json={"phone": "19900000002", "password": "Demo-AfterSales-2026!"}))["access_token"]

        path = "/system-settings/tencent-map"
        for token in (staff, hr):
            await request("GET", path, token, expected=403)
            await request("PUT", path, token, expected=403, json={"browser_key": OVERRIDE})
            await request("DELETE", path, token, expected=403)
        await request("GET", path, expected=401)
        initial = await request("GET", path, admin)
        assert initial["source"] == "environment" and initial["configured"]
        assert initial["masked_key"].endswith("NVVV")
        assert settings.TENCENT_MAP_BROWSER_KEY not in str(initial)

        await request("PUT", path, admin, expected=422, json={"browser_key": "bad key"})
        saved = await request("PUT", path, admin, json={"browser_key": OVERRIDE})
        assert saved["source"] == "platform" and saved["has_override"]
        assert OVERRIDE not in str(saved)
        current = await request("GET", "/attendance/maps/tencent/web-sdk-config", staff)
        assert current["enabled"] and current["key"] == OVERRIDE
        runtime = await request("GET", "/attendance/my/runtime", staff)
        assert runtime["map_browser_enabled"] and runtime["map_service_enabled"]

        await request("PUT", path, admin, json={"browser_key": REPLACEMENT})
        async with async_session() as fresh_db:
            key, source = await browser_map_key(fresh_db)
            assert (key, source) == (REPLACEMENT, "platform")
        assert (await request("GET", "/attendance/maps/tencent/web-sdk-config", staff))["key"] == REPLACEMENT

        async with async_session() as db:
            logs = (await db.scalars(select(AuditLog).where(AuditLog.module == "system"))).all()
            assert len(logs) == 2
            for log in logs:
                assert OVERRIDE not in str(log.before_data) + str(log.after_data)
                assert REPLACEMENT not in str(log.before_data) + str(log.after_data)

        cleared = await request("DELETE", path, admin)
        assert cleared["source"] == "environment" and not cleared["has_override"]
        assert (await request("GET", "/attendance/maps/tencent/web-sdk-config", staff))["key"] == settings.TENCENT_MAP_BROWSER_KEY
        async with async_session() as db:
            logs = (await db.scalars(select(AuditLog).where(AuditLog.module == "system"))).all()
            assert len(logs) == 3 and all(OVERRIDE not in str(log.before_data) + str(log.after_data) for log in logs)

        settings.TENCENT_MAP_BROWSER_KEY = None
        empty = await request("GET", path, admin)
        assert empty["source"] == "none" and not empty["configured"]
        sdk = await request("GET", "/attendance/maps/tencent/web-sdk-config", staff)
        assert not sdk["enabled"] and not sdk["key"]

    await engine.dispose()
    temp_db.cleanup()
    print("map settings: permissions, override, persistence, audit redaction and fallback passed")


if __name__ == "__main__":
    asyncio.run(main())
