"""
考勤重算任务服务。

当前版本先落地员工+日期级 dirty 标记，避免审批消费者直接承担完整日结果重算。
后续后台任务可以读取 attendance_recalc_tasks 执行物化和月汇总刷新。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance import AttendanceRecalcTask


class AttendanceRecalcService:
    """维护日考勤重算脏标记。"""

    @staticmethod
    async def mark_dirty(
        db: AsyncSession,
        *,
        employee_id: int,
        work_date: date,
        reason: str = "approval_effect",
        source_event_id: str | None = None,
    ) -> AttendanceRecalcTask:
        result = await db.execute(
            select(AttendanceRecalcTask).where(
                AttendanceRecalcTask.employee_id == employee_id,
                AttendanceRecalcTask.work_date == work_date,
                AttendanceRecalcTask.reason == reason,
                AttendanceRecalcTask.source_event_id == source_event_id,
                AttendanceRecalcTask.status.in_(["pending", "processing"]),
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        task = AttendanceRecalcTask(
            employee_id=employee_id,
            work_date=work_date,
            reason=reason,
            status="pending",
            source_event_id=source_event_id,
        )
        db.add(task)
        await db.flush()
        return task
