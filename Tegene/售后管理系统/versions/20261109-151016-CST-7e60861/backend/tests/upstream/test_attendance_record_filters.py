from unittest.mock import AsyncMock

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.v1.attendance import _parse_attendance_status_filters
from app.models.attendance import AttendanceStatus
from app.schemas.attendance import AttendanceRecordQuery
from app.services.attendance import AttendanceRecordService


class _CountResult:
    def scalar(self):
        return 0


class _EmptyScalarRows:
    def all(self):
        return []


class _EmptyRowsResult:
    def scalars(self):
        return _EmptyScalarRows()


def test_parse_attendance_status_filters_supports_multiple_clear_values():
    statuses = _parse_attendance_status_filters(
        None,
        ["正常,迟到", "早退", "missing_punch"],
    )

    assert statuses == [
        AttendanceStatus.normal,
        AttendanceStatus.late,
        AttendanceStatus.early_leave,
        AttendanceStatus.missed_clock,
    ]


@pytest.mark.asyncio
async def test_query_records_filters_by_multiple_main_statuses():
    executed = []

    async def execute(stmt):
        executed.append(stmt)
        return _CountResult() if len(executed) == 1 else _EmptyRowsResult()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)

    records, total = await AttendanceRecordService.query_records(
        db,
        AttendanceRecordQuery(
            statuses=[AttendanceStatus.late, AttendanceStatus.early_leave],
            page=1,
            page_size=20,
        ),
    )

    assert records == []
    assert total == 0
    params = executed[0].compile().params
    status_params = [
        value
        for value in params.values()
        if isinstance(value, (list, tuple))
    ]
    assert [AttendanceStatus.late, AttendanceStatus.early_leave] in status_params


@pytest.mark.asyncio
async def test_normal_filter_excludes_records_with_anomaly_reason():
    executed = []

    async def execute(stmt):
        executed.append(stmt)
        return _CountResult() if len(executed) == 1 else _EmptyRowsResult()

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    await AttendanceRecordService.query_records(
        db, AttendanceRecordQuery(status=AttendanceStatus.normal),
    )

    assert "anomaly_type IS NULL" in str(executed[0])
