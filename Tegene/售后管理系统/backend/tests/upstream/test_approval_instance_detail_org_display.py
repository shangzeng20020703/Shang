from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import approval as approval_api


class _RowsResult:
    def __init__(self, rows: list[tuple]):
        self._rows = rows

    def all(self):
        return self._rows


class _FirstResult:
    def __init__(self, row: tuple | None):
        self._row = row

    def first(self):
        return self._row


@pytest.mark.asyncio
async def test_form_field_display_values_resolves_department_array_values():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_RowsResult([(58, "机电组")]))

    display_values = await approval_api._form_field_display_values(
        db,
        {"department": [58]},
        {"department": "department"},
    )

    assert display_values["department"] == "机电组"


@pytest.mark.asyncio
async def test_form_field_display_values_formats_duration_with_template_unit():
    db = AsyncMock()

    day_display_values = await approval_api._form_field_display_values(
        db,
        {"trip_duration": 6},
        {"trip_duration": "duration"},
        {"trip_duration": {"duration_mode": "natural_day", "unit_hours": 24}},
    )
    hour_display_values = await approval_api._form_field_display_values(
        db,
        {"overtime_duration": 3},
        {"overtime_duration": "duration"},
        {"overtime_duration": {"time_scale": "hour"}},
    )

    assert day_display_values["trip_duration"] == "6 天"
    assert hour_display_values["overtime_duration"] == "3 小时"


@pytest.mark.asyncio
async def test_approval_instance_out_includes_applicant_department_and_company_text():
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_FirstResult(("孙明月", 5, "人事部", None, "示例公司", None, None))
    )
    instance = SimpleNamespace(
        id=23,
        template_version_id=None,
        flow_id=12,
        applicant_id=6,
        module="",
        business_id=23,
        business_type="business_trip",
        summary="大加",
        form_data=None,
        flow_snapshot=None,
        current_node_order=1,
        status="pending",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    result = await approval_api._approval_instance_out(db, instance)

    assert result.applicant_name == "孙明月"
    assert result.department_id == 5
    assert result.department_name == "人事部"
    assert result.company_id is None
    assert result.company_name == "示例公司"
