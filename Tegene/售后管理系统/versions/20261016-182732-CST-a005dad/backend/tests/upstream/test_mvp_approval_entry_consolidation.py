from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.deps import get_current_user
from app.main import app


@pytest.fixture
def mvp_entry_overrides():
    async def override_get_db():
        yield None

    async def override_get_current_user():
        return SimpleNamespace(id=1, employee_no="E001", name="测试员工", role="employee", is_active=True)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "payload", "business_code"),
    [
        (
            "/api/v1/attendance/outside-approval",
            {
                "address": "测试地址",
                "latitude": 31.2,
                "longitude": 121.5,
                "photo_url": "data:image/png;base64,AA==",
                "occurred_at": "2026-05-05T09:00:00+08:00",
            },
            "outside",
        ),
        (
            "/api/v1/attendance/corrections",
            {"record_id": 1, "correction_note": "测试补卡"},
            "punch_correction",
        ),
        (
            "/api/v1/attendance/punch-corrections",
            {
                "employee_id": 1,
                "correction_date": "2026-05-05",
                "punch_time": "2026-05-05T09:00:00+08:00",
                "punch_type": "check_in",
                "reason": "测试补卡",
            },
            "punch_correction",
        ),
        (
            "/api/v1/overtime",
            {
                "overtime_date": "2026-05-05",
                "start_time": "18:00",
                "end_time": "20:00",
                "hours": 2,
                "overtime_type": "weekday",
                "reason": "测试加班",
            },
            "legal_overtime",
        ),
    ],
)
async def test_mvp_legacy_attendance_write_entries_are_blocked(
    mvp_entry_overrides,
    path: str,
    payload: dict,
    business_code: str,
):
    headers = {
        "X-Allow-Legacy-Attendance-Write": "true",
        "X-Allow-Legacy-Overtime-Write": "true",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(path, json=payload, headers=headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "/api/v1/approval/applications" in detail
    assert f"business_code={business_code}" in detail
