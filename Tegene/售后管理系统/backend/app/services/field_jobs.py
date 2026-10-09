"""Finish original attendance dirty tasks using the original day/month calculators."""

import logging
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.database import async_session
from app.models.attendance import (
    AttendanceRecalcTask,
    AttendanceRecord,
    AttendanceMonthSummary,
    MonthlySummaryStatus,
)
from app.services.attendance import AttendanceRecordService

logger = logging.getLogger(__name__)


async def process_recalculation(limit=50):
    async with async_session() as db:
        ids = list(
            (
                await db.scalars(
                    select(AttendanceRecalcTask.id)
                    .where(AttendanceRecalcTask.status.in_(["pending", "failed"]))
                    .order_by(AttendanceRecalcTask.updated_at)
                    .limit(limit)
                )
            ).all()
        )
    for task_id in ids:
        async with async_session() as db:
            task = await db.get(AttendanceRecalcTask, task_id)
            if not task or task.status == "completed":
                continue
            locked = await db.scalar(
                select(AttendanceMonthSummary.id).where(
                    AttendanceMonthSummary.employee_id == task.employee_id,
                    AttendanceMonthSummary.year == task.work_date.year,
                    AttendanceMonthSummary.month == task.work_date.month,
                    AttendanceMonthSummary.status == MonthlySummaryStatus.locked,
                )
            )
            if locked:
                task.last_error = "月份已锁定，解锁后重试"
                task.updated_at = datetime.now(timezone.utc)
                await db.commit()
                continue
            try:
                async with db.begin_nested():
                    task.status = "processing"
                    record = await db.scalar(
                        select(AttendanceRecord).where(
                            AttendanceRecord.employee_id == task.employee_id,
                            AttendanceRecord.date == task.work_date,
                        )
                    )
                    if record:
                        runtime = await AttendanceRecordService._get_rule_runtime(
                            db, task.employee_id, task.work_date
                        )
                        await AttendanceRecordService._recalculate_record_after_punch(
                            db, record, task.employee_id, task.work_date, runtime
                        )
                    await AttendanceRecordService._refresh_month_summary_for_date(
                        db, task.employee_id, task.work_date
                    )
                    task.status = "completed"
                    task.processed_at = datetime.now(timezone.utc)
                    task.last_error = None
            except Exception as exc:
                task = await db.get(AttendanceRecalcTask, task_id)
                task.status = "failed"
                task.last_error = str(exc)[:1000]
                logger.exception("Attendance recalculation failed: task %s", task_id)
            await db.commit()
