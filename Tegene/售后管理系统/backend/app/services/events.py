"""
业务事件 outbox 服务。

业务模块只负责把事件可靠写入本地数据库；分发、重试和消费者幂等由
event_dispatcher 处理。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_event import BusinessEventOutbox


class BusinessEventService:
    """事务性 outbox 写入与扫描服务。"""

    @staticmethod
    async def enqueue(
        db: AsyncSession,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: Optional[int],
        payload: dict[str, Any],
        idempotency_key: str,
        source_module: str,
        schema_version: int = 1,
        headers: Optional[dict[str, Any]] = None,
        occurred_at: Optional[datetime] = None,
    ) -> BusinessEventOutbox:
        """幂等写入业务事件。"""
        existing_result = await db.execute(
            select(BusinessEventOutbox).where(
                BusinessEventOutbox.idempotency_key == idempotency_key
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            return existing

        event = BusinessEventOutbox(
            event_id=str(uuid4()),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            source_module=source_module,
            schema_version=schema_version,
            idempotency_key=idempotency_key,
            payload_json=payload,
            headers_json=headers or {},
            status="pending",
            attempt_count=0,
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def list_dispatchable(
        db: AsyncSession,
        *,
        limit: int = 50,
        now: Optional[datetime] = None,
    ) -> list[BusinessEventOutbox]:
        """扫描可分发的 pending/failed 事件。"""
        current = now or datetime.now(timezone.utc)
        result = await db.execute(
            select(BusinessEventOutbox)
            .where(
                BusinessEventOutbox.status.in_(["pending", "failed"]),
                or_(
                    BusinessEventOutbox.next_retry_at.is_(None),
                    BusinessEventOutbox.next_retry_at <= current,
                ),
            )
            .order_by(BusinessEventOutbox.created_at.asc(), BusinessEventOutbox.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    def mark_failed(
        event: BusinessEventOutbox,
        error: str,
        *,
        max_attempts: int = 5,
        now: Optional[datetime] = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)
        event.last_error = error[:4000]
        if int(event.attempt_count or 0) >= max_attempts:
            event.status = "dead"
            event.next_retry_at = None
            return
        event.status = "failed"
        delay_seconds = min(3600, 2 ** max(0, int(event.attempt_count or 1) - 1) * 60)
        event.next_retry_at = current + timedelta(seconds=delay_seconds)
