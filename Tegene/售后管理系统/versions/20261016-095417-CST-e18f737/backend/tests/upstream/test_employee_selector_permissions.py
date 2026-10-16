from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import employees as employees_api
from app.core.database import get_db
from app.core.deps import get_current_user
from app.main import app


@pytest.fixture
def employee_selector_overrides(monkeypatch):
    db = AsyncMock()
    role_result = MagicMock()
    role_result.scalars.return_value.first.return_value = None
    role_result.all.return_value = []
    db.execute.return_value = role_result

    async def override_get_db():
        yield db

    async def override_get_current_user():
        return SimpleNamespace(
            id=999,
            is_active=True,
            is_superuser=False,
            department_id=None,
        )

    async def fake_resolve_employee_scope(_db, _current_user):
        return {"employee"}, False

    async def fake_get_list(_db, **_kwargs):
        employee = SimpleNamespace(
            id=1,
            employee_no="E001",
            name="张三",
            phone="13800138000",
            position="工程师",
            department_id=58,
            department=SimpleNamespace(name="机电组"),
            status="在职",
        )
        return 1, [employee]

    monkeypatch.setattr(employees_api, "_resolve_employee_scope", fake_resolve_employee_scope)
    monkeypatch.setattr(employees_api.EmployeeService, "get_list", fake_get_list)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        yield db
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_employee_selector_allows_authenticated_employee_without_role_row(
    employee_selector_overrides,
):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/api/v1/employees/selector?limit=1000")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "employee_no": "E001",
            "name": "张三",
            "phone": "138****8000",
            "position": "工程师",
            "department_id": 58,
            "department_name": "机电组",
            "status": "在职",
            "role_names": [],
        }
    ]
    employee_selector_overrides.execute.assert_awaited()
