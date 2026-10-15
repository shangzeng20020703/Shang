"""
本地业务事件分发器。

第一阶段保持单体内同步分发：审批等服务写入 outbox 后，可立即调用
dispatch_event()；后台补偿任务也可以调用 dispatch_pending() 重试失败事件。
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_event import BusinessEventConsumption, BusinessEventOutbox
from app.services.events import BusinessEventService


class BusinessEventConsumer(Protocol):
    name: str
    event_types: set[str]

    async def handle(self, db: AsyncSession, event: BusinessEventOutbox) -> bool | None:
        ...


_CONSUMERS: dict[str, BusinessEventConsumer] = {}
_DEFAULTS_REGISTERED = False


def register_consumer(consumer: BusinessEventConsumer) -> None:
    """注册一个本地消费者。重复注册同名消费者时以后一次为准。"""
    _CONSUMERS[consumer.name] = consumer


def _ensure_default_consumers() -> None:
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED:
        return
    from app.services.attendance_effects import AttendanceApprovalEffectConsumer
    from app.services.leave_events import LeaveApprovalBalanceConsumer

    register_consumer(AttendanceApprovalEffectConsumer())
    register_consumer(LeaveApprovalBalanceConsumer())
    _DEFAULTS_REGISTERED = True


def _consumers_for(event_type: str) -> list[BusinessEventConsumer]:
    _ensure_default_consumers()
    return [
        consumer
        for consumer in _CONSUMERS.values()
        if event_type in getattr(consumer, "event_types", set())
    ]


async def _load_or_start_consumption(
    db: AsyncSession,
    *,
    event_id: str,
    consumer_name: str,
) -> BusinessEventConsumption:
    result = await db.execute(
        select(BusinessEventConsumption).where(
            BusinessEventConsumption.event_id == event_id,
            BusinessEventConsumption.consumer_name == consumer_name,
        )
    )
    consumption = result.scalar_one_or_none()
    if consumption is None:
        consumption = BusinessEventConsumption(
            event_id=event_id,
            consumer_name=consumer_name,
            status="processing",
            attempt_count=1,
        )
        db.add(consumption)
        await db.flush()
        return consumption
    if consumption.status in {"succeeded", "skipped"}:
        return consumption
    consumption.status = "processing"
    consumption.attempt_count = int(consumption.attempt_count or 0) + 1
    consumption.last_error = None
    return consumption


async def dispatch_event(db: AsyncSession, event: BusinessEventOutbox) -> BusinessEventOutbox:
    """分发单个 outbox 事件，消费者失败不会抛出到业务服务。"""
    event.status = "dispatching"
    event.attempt_count = int(event.attempt_count or 0) + 1
    event.next_retry_at = None
    await db.flush()

    consumers = _consumers_for(event.event_type)
    if not consumers:
        event.status = "dispatched"
        event.last_error = None
        await db.flush()
        return event

    all_ok = True
    errors: list[str] = []
    now = datetime.now(timezone.utc)
    for consumer in consumers:
        consumption = await _load_or_start_consumption(
            db,
            event_id=event.event_id,
            consumer_name=consumer.name,
        )
        if consumption.status in {"succeeded", "skipped"}:
            continue
        try:
            handled = consumer.handle(db, event)
            if inspect.isawaitable(handled):
                handled = await handled
            consumption.status = "succeeded" if handled is not False else "skipped"
            consumption.processed_at = now
            consumption.last_error = None
        except Exception as exc:  # pragma: no cover - 具体失败由消费测试覆盖
            all_ok = False
            message = f"{consumer.name}: {exc}"
            errors.append(message)
            consumption.status = "failed"
            consumption.last_error = message[:4000]

    if all_ok:
        event.status = "dispatched"
        event.last_error = None
        event.next_retry_at = None
    else:
        BusinessEventService.mark_failed(event, "; ".join(errors))
    await db.flush()
    return event


async def dispatch_pending(db: AsyncSession, *, limit: int = 50) -> list[BusinessEventOutbox]:
    """扫描并分发可重试事件，供后台任务或运维脚本调用。"""
    events = await BusinessEventService.list_dispatchable(db, limit=limit)
    for event in events:
        await dispatch_event(db, event)
    return events
