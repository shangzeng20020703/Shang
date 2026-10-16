from datetime import date

import pytest

from app.services.attendance import AttendanceMonthlyReportTaskService, AttendanceReportService


class DummyDB:
    pass


@pytest.mark.asyncio
async def test_monthly_report_task_reuses_running_query(monkeypatch):
    AttendanceMonthlyReportTaskService._reset_memory_store_for_tests()
    scheduled: list[str] = []

    async def no_snapshot(_db, _cache_key):
        return None

    monkeypatch.setattr(AttendanceMonthlyReportTaskService, "_load_recent_snapshot", no_snapshot)
    monkeypatch.setattr(AttendanceMonthlyReportTaskService, "_schedule_generation", scheduled.append)

    first = await AttendanceMonthlyReportTaskService.create_task(
        DummyDB(),
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 31),
        department_id=3,
        keyword=" 张三 ",
        status="异常",
        rule_id=8,
        include_recent_left=True,
        requested_by_id=1,
    )
    second = await AttendanceMonthlyReportTaskService.create_task(
        DummyDB(),
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 31),
        department_id=3,
        keyword="张三",
        status="异常",
        rule_id=8,
        include_recent_left=True,
        requested_by_id=1,
    )

    assert first["task_id"] == second["task_id"]
    assert second["deduplicated"] is True
    assert scheduled == [first["task_id"]]


def test_monthly_report_export_from_task_result_uses_snapshot_rows():
    result = AttendanceReportService._monthly_report_result_payload(
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 2),
        page=1,
        page_size=1,
        total=1,
        day_columns=[
            {"key": "day_20260501", "title": "1\n星期五", "date": "2026-05-01"},
            {"key": "day_20260502", "title": "2\n星期六", "date": "2026-05-02"},
        ],
        overview_rows=[
            {
                "employee_name": "张三",
                "account": "E001",
                "rule_name": "标准工时",
                "department_name": "研发部",
                "position": "工程师",
            }
        ],
        detail_rows=[
            {
                "employee_name": "张三",
                "account": "E001",
                "rule_name": "标准工时",
                "department_name": "研发部",
                "position": "工程师",
                "day_20260501": "正常 09:00 18:00 ;",
                "day_20260502": "正常（休息） ;",
            }
        ],
    )

    content = AttendanceReportService.export_wecom_monthly_report_from_result(
        result,
        overview_columns=["employee_name", "account", "rule_name"],
        detail_columns=["employee_name", "account"],
    )

    assert content.startswith(b"PK")
    assert len(content) > 1000
