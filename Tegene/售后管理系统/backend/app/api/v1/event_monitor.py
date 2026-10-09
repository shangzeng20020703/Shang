"""
业务事件与考勤 effect 监控 API。

本模块只暴露运维排查所需的只读查询和 outbox 重试入口，供管理端确认
审批终态事件、消费者幂等记录、考勤影响记录和重算 dirty 标记是否连通。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import require_roles
from app.models.approval import ApprovalInstance, ApprovalTemplateVersion, ApprovalType
from app.models.attendance import AttendanceEffect, AttendanceRecalcTask
from app.models.business_event import BusinessEventConsumption, BusinessEventOutbox
from app.models.employee import Employee
from app.services.event_dispatcher import dispatch_event, dispatch_pending

router = APIRouter()


def _page_offset(page: int, page_size: int) -> int:
    return (max(1, page) - 1) * page_size


async def _total(db: AsyncSession, stmt) -> int:
    result = await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    return int(result.scalar() or 0)


def _page(items: list[dict[str, Any]], total: int, page: int, page_size: int) -> dict[str, Any]:
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _status_counts(rows: list[tuple[Any, int]]) -> dict[str, int]:
    return {str(status or "unknown"): int(count or 0) for status, count in rows}


APPROVAL_BUSINESS_TYPE_LABELS = {
    "attendance_outside": "外出",
    "outside": "外出",
    "punch_correction": "补卡",
    "attendance_punch_correction": "补卡",
    "patch_apply": "补卡",
    "leave": "请假",
    "leave_request": "请假",
    "on_leave": "请假",
    "business_trip": "出差",
    "overtime": "加班",
}


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _safe_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _payload_dict(event: BusinessEventOutbox) -> dict[str, Any]:
    payload = getattr(event, "payload_json", None)
    return payload if isinstance(payload, dict) else {}


def _approval_business_label(*codes: Any) -> str | None:
    for raw_code in codes:
        code = str(raw_code or "").strip().lower()
        if not code:
            continue
        if code in APPROVAL_BUSINESS_TYPE_LABELS:
            return APPROVAL_BUSINESS_TYPE_LABELS[code]
        if code.startswith("attendance_rule_"):
            if code.endswith("_patch_apply"):
                return "补卡"
            if code.endswith("_leave_punch"):
                return "请假打卡"
            if code.endswith("_overtime"):
                return "加班"
            if code.endswith("_approve_punch"):
                return "审批打卡"
        if "outside" in code:
            return "外出"
        if "punch" in code or "patch" in code:
            return "补卡"
        if "leave" in code:
            return "请假"
        if "business_trip" in code or "trip" in code:
            return "出差"
        if "overtime" in code:
            return "加班"
    return None


def _approval_instance_id(event: BusinessEventOutbox) -> int | None:
    if getattr(event, "aggregate_type", None) == "approval_instance":
        aggregate_id = _safe_int(getattr(event, "aggregate_id", None))
        if aggregate_id:
            return aggregate_id
    return _safe_int(_payload_dict(event).get("approval_instance_id"))


def _approval_context_from_event(event: BusinessEventOutbox) -> dict[str, Any] | None:
    instance_id = _approval_instance_id(event)
    payload = _payload_dict(event)
    event_type = str(getattr(event, "event_type", "") or "")
    is_approval_event = (
        event_type.startswith("approval.instance.")
        or getattr(event, "aggregate_type", None) == "approval_instance"
        or bool(payload.get("approval_instance_id"))
    )
    if not is_approval_event:
        return None

    module = _clean_text(payload.get("module"))
    business_type = _clean_text(payload.get("business_type"))
    approval_type_name = _clean_text(payload.get("approval_type_name"))
    business_type_label = approval_type_name or _approval_business_label(business_type, module)
    return {
        "approval_instance_id": instance_id,
        "module": module,
        "business_type": business_type,
        "business_type_label": business_type_label,
        "business_id": _safe_int(payload.get("business_id")),
        "summary": _clean_text(payload.get("summary")),
        "applicant_id": _safe_int(payload.get("applicant_id")),
        "applicant_name": _clean_text(payload.get("applicant_name")),
        "applicant_no": _clean_text(payload.get("applicant_no")),
        "approval_type_name": approval_type_name,
    }


def _filled(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None and value != ""}


async def _approval_contexts_for_events(
    db: AsyncSession,
    events: list[BusinessEventOutbox],
) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    instance_ids: set[int] = set()

    for event in events:
        context = _approval_context_from_event(event)
        if not context:
            continue
        contexts[event.event_id] = context
        instance_id = _safe_int(context.get("approval_instance_id"))
        if instance_id:
            instance_ids.add(instance_id)

    if not instance_ids:
        return contexts

    rows = (
        await db.execute(
            select(
                ApprovalInstance,
                Employee.name.label("applicant_name"),
                Employee.employee_no.label("applicant_no"),
                ApprovalType.name.label("approval_type_name"),
            )
            .outerjoin(Employee, Employee.id == ApprovalInstance.applicant_id)
            .outerjoin(ApprovalTemplateVersion, ApprovalTemplateVersion.id == ApprovalInstance.template_version_id)
            .outerjoin(ApprovalType, ApprovalType.id == ApprovalTemplateVersion.approval_type_id)
            .where(ApprovalInstance.id.in_(instance_ids))
        )
    ).all()

    instance_contexts: dict[int, dict[str, Any]] = {}
    fallback_codes: set[str] = set()
    for instance, applicant_name, applicant_no, approval_type_name in rows:
        module = _clean_text(getattr(instance, "module", None))
        business_type = _clean_text(getattr(instance, "business_type", None))
        for code in (module, business_type):
            if code:
                fallback_codes.add(code)
        instance_contexts[int(instance.id)] = _filled(
            {
                "approval_instance_id": instance.id,
                "module": module,
                "business_type": business_type,
                "business_id": _safe_int(getattr(instance, "business_id", None)),
                "summary": _clean_text(getattr(instance, "summary", None)),
                "applicant_id": _safe_int(getattr(instance, "applicant_id", None)),
                "applicant_name": _clean_text(applicant_name),
                "applicant_no": _clean_text(applicant_no),
                "approval_type_name": _clean_text(approval_type_name),
            }
        )

    type_name_by_code: dict[str, str] = {}
    if fallback_codes:
        type_rows = (
            await db.execute(
                select(ApprovalType.business_code, ApprovalType.name)
                .where(ApprovalType.business_code.in_(fallback_codes))
                .order_by(ApprovalType.id.desc())
            )
        ).all()
        for business_code, type_name in type_rows:
            code = _clean_text(business_code)
            name = _clean_text(type_name)
            if code and name and code not in type_name_by_code:
                type_name_by_code[code] = name

    for event in events:
        context = dict(contexts.get(event.event_id) or {})
        instance_id = _safe_int(context.get("approval_instance_id"))
        if instance_id and instance_id in instance_contexts:
            context.update(instance_contexts[instance_id])
        if not context.get("approval_type_name"):
            context["approval_type_name"] = (
                type_name_by_code.get(str(context.get("business_type") or ""))
                or type_name_by_code.get(str(context.get("module") or ""))
            )
        if not context.get("business_type_label"):
            context["business_type_label"] = context.get("approval_type_name") or _approval_business_label(
                context.get("business_type"),
                context.get("module"),
            )
        if context:
            contexts[event.event_id] = context

    return contexts


def _event_dict(event: BusinessEventOutbox, approval_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "source_module": event.source_module,
        "schema_version": event.schema_version,
        "idempotency_key": event.idempotency_key,
        "payload_json": event.payload_json,
        "headers_json": event.headers_json,
        "status": event.status,
        "attempt_count": event.attempt_count,
        "next_retry_at": event.next_retry_at,
        "last_error": event.last_error,
        "occurred_at": event.occurred_at,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
        "approval_context": approval_context,
    }


def _consumption_dict(item: BusinessEventConsumption) -> dict[str, Any]:
    return {
        "id": item.id,
        "event_id": item.event_id,
        "consumer_name": item.consumer_name,
        "status": item.status,
        "attempt_count": item.attempt_count,
        "processed_at": item.processed_at,
        "last_error": item.last_error,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _effect_dict(effect: AttendanceEffect, employee_name: str | None = None, employee_no: str | None = None) -> dict[str, Any]:
    return {
        "id": effect.id,
        "source_module": effect.source_module,
        "source_event_id": effect.source_event_id,
        "source_instance_id": effect.source_instance_id,
        "source_business_type": effect.source_business_type,
        "source_business_id": effect.source_business_id,
        "employee_id": effect.employee_id,
        "employee_name": employee_name,
        "employee_no": employee_no,
        "effect_type": effect.effect_type,
        "effect_status": effect.effect_status,
        "start_at": effect.start_at,
        "end_at": effect.end_at,
        "work_date": effect.work_date,
        "minutes": effect.minutes,
        "priority": effect.priority,
        "pay_policy": effect.pay_policy,
        "attendance_status": effect.attendance_status,
        "payload_json": effect.payload_json,
        "applied_at": effect.applied_at,
        "created_at": effect.created_at,
        "updated_at": effect.updated_at,
    }


def _recalc_dict(task: AttendanceRecalcTask, employee_name: str | None = None, employee_no: str | None = None) -> dict[str, Any]:
    return {
        "id": task.id,
        "employee_id": task.employee_id,
        "employee_name": employee_name,
        "employee_no": employee_no,
        "work_date": task.work_date,
        "reason": task.reason,
        "status": task.status,
        "source_event_id": task.source_event_id,
        "last_error": task.last_error,
        "created_at": task.created_at,
        "processed_at": task.processed_at,
        "updated_at": task.updated_at,
    }


@router.get("/summary", summary="事件与 effect 概览")
async def event_monitor_summary(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    outbox_rows = (
        await db.execute(
            select(BusinessEventOutbox.status, func.count(BusinessEventOutbox.id))
            .group_by(BusinessEventOutbox.status)
        )
    ).all()
    consumption_rows = (
        await db.execute(
            select(BusinessEventConsumption.status, func.count(BusinessEventConsumption.id))
            .group_by(BusinessEventConsumption.status)
        )
    ).all()
    effect_rows = (
        await db.execute(
            select(AttendanceEffect.effect_status, func.count(AttendanceEffect.id))
            .group_by(AttendanceEffect.effect_status)
        )
    ).all()
    recalc_rows = (
        await db.execute(
            select(AttendanceRecalcTask.status, func.count(AttendanceRecalcTask.id))
            .group_by(AttendanceRecalcTask.status)
        )
    ).all()
    recent_failed_result = await db.execute(
        select(BusinessEventOutbox)
        .where(BusinessEventOutbox.status.in_(["failed", "dead"]))
        .order_by(BusinessEventOutbox.updated_at.desc(), BusinessEventOutbox.id.desc())
        .limit(5)
    )
    return {
        "outbox_by_status": _status_counts(outbox_rows),
        "consumption_by_status": _status_counts(consumption_rows),
        "effect_by_status": _status_counts(effect_rows),
        "recalc_by_status": _status_counts(recalc_rows),
        "recent_failed_events": [_event_dict(item) for item in recent_failed_result.scalars().all()],
    }


@router.get("/outbox", summary="Outbox 事件列表")
async def list_outbox_events(
    status: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    source_module: Optional[str] = Query(None),
    aggregate_type: Optional[str] = Query(None),
    aggregate_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None, description="事件ID、幂等键或错误信息"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    conditions = []
    if status:
        conditions.append(BusinessEventOutbox.status == status)
    if event_type:
        conditions.append(BusinessEventOutbox.event_type.ilike(f"%{event_type.strip()}%"))
    if source_module:
        conditions.append(BusinessEventOutbox.source_module == source_module)
    if aggregate_type:
        conditions.append(BusinessEventOutbox.aggregate_type == aggregate_type)
    if aggregate_id is not None:
        conditions.append(BusinessEventOutbox.aggregate_id == aggregate_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                BusinessEventOutbox.event_id.ilike(like),
                BusinessEventOutbox.idempotency_key.ilike(like),
                BusinessEventOutbox.last_error.ilike(like),
            )
        )

    stmt = select(BusinessEventOutbox)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    total = await _total(db, stmt)
    result = await db.execute(
        stmt.order_by(BusinessEventOutbox.created_at.desc(), BusinessEventOutbox.id.desc())
        .offset(_page_offset(page, page_size))
        .limit(page_size)
    )
    events = result.scalars().all()
    approval_contexts = await _approval_contexts_for_events(db, list(events))
    return _page(
        [_event_dict(item, approval_contexts.get(item.event_id)) for item in events],
        total,
        page,
        page_size,
    )


@router.get("/consumptions", summary="消费者幂等记录列表")
async def list_event_consumptions(
    status: Optional[str] = Query(None),
    consumer_name: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    stmt = (
        select(
            BusinessEventConsumption,
            BusinessEventOutbox.event_type,
            BusinessEventOutbox.status.label("event_status"),
        )
        .outerjoin(BusinessEventOutbox, BusinessEventOutbox.event_id == BusinessEventConsumption.event_id)
    )
    conditions = []
    if status:
        conditions.append(BusinessEventConsumption.status == status)
    if consumer_name:
        conditions.append(BusinessEventConsumption.consumer_name.ilike(f"%{consumer_name.strip()}%"))
    if event_id:
        conditions.append(BusinessEventConsumption.event_id.ilike(f"%{event_id.strip()}%"))
    if conditions:
        stmt = stmt.where(and_(*conditions))

    total = await _total(db, stmt)
    result = await db.execute(
        stmt.order_by(BusinessEventConsumption.created_at.desc(), BusinessEventConsumption.id.desc())
        .offset(_page_offset(page, page_size))
        .limit(page_size)
    )
    items: list[dict[str, Any]] = []
    for consumption, event_type_value, event_status in result.all():
        row = _consumption_dict(consumption)
        row["event_type"] = event_type_value
        row["event_status"] = event_status
        items.append(row)
    return _page(items, total, page, page_size)


@router.get("/effects", summary="考勤 effect 列表")
async def list_attendance_effects(
    employee_id: Optional[int] = Query(None),
    effect_type: Optional[str] = Query(None),
    effect_status: Optional[str] = Query(None),
    source_business_type: Optional[str] = Query(None),
    source_business_id: Optional[int] = Query(None),
    source_instance_id: Optional[int] = Query(None),
    source_event_id: Optional[str] = Query(None),
    work_date_from: Optional[date] = Query(None),
    work_date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    stmt = (
        select(AttendanceEffect, Employee.name, Employee.employee_no)
        .outerjoin(Employee, Employee.id == AttendanceEffect.employee_id)
    )
    conditions = []
    if employee_id is not None:
        conditions.append(AttendanceEffect.employee_id == employee_id)
    if effect_type:
        conditions.append(AttendanceEffect.effect_type == effect_type)
    if effect_status:
        conditions.append(AttendanceEffect.effect_status == effect_status)
    if source_business_type:
        conditions.append(AttendanceEffect.source_business_type == source_business_type)
    if source_business_id is not None:
        conditions.append(AttendanceEffect.source_business_id == source_business_id)
    if source_instance_id is not None:
        conditions.append(AttendanceEffect.source_instance_id == source_instance_id)
    if source_event_id:
        conditions.append(AttendanceEffect.source_event_id.ilike(f"%{source_event_id.strip()}%"))
    if work_date_from is not None:
        conditions.append(AttendanceEffect.work_date >= work_date_from)
    if work_date_to is not None:
        conditions.append(AttendanceEffect.work_date <= work_date_to)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    total = await _total(db, stmt)
    result = await db.execute(
        stmt.order_by(AttendanceEffect.work_date.desc(), AttendanceEffect.id.desc())
        .offset(_page_offset(page, page_size))
        .limit(page_size)
    )
    items = [_effect_dict(effect, employee_name, employee_no) for effect, employee_name, employee_no in result.all()]
    return _page(items, total, page, page_size)


@router.get("/recalc-tasks", summary="考勤重算 dirty 任务列表")
async def list_attendance_recalc_tasks(
    employee_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    reason: Optional[str] = Query(None),
    source_event_id: Optional[str] = Query(None),
    work_date_from: Optional[date] = Query(None),
    work_date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    stmt = (
        select(AttendanceRecalcTask, Employee.name, Employee.employee_no)
        .outerjoin(Employee, Employee.id == AttendanceRecalcTask.employee_id)
    )
    conditions = []
    if employee_id is not None:
        conditions.append(AttendanceRecalcTask.employee_id == employee_id)
    if status:
        conditions.append(AttendanceRecalcTask.status == status)
    if reason:
        conditions.append(AttendanceRecalcTask.reason == reason)
    if source_event_id:
        conditions.append(AttendanceRecalcTask.source_event_id.ilike(f"%{source_event_id.strip()}%"))
    if work_date_from is not None:
        conditions.append(AttendanceRecalcTask.work_date >= work_date_from)
    if work_date_to is not None:
        conditions.append(AttendanceRecalcTask.work_date <= work_date_to)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    total = await _total(db, stmt)
    result = await db.execute(
        stmt.order_by(AttendanceRecalcTask.work_date.desc(), AttendanceRecalcTask.id.desc())
        .offset(_page_offset(page, page_size))
        .limit(page_size)
    )
    items = [_recalc_dict(task, employee_name, employee_no) for task, employee_name, employee_no in result.all()]
    return _page(items, total, page, page_size)


@router.post("/outbox/dispatch-pending", summary="批量分发待处理 outbox")
async def dispatch_pending_outbox(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    events = await dispatch_pending(db, limit=limit)
    return {
        "count": len(events),
        "items": [_event_dict(event) for event in events],
    }


@router.post("/outbox/{event_pk}/dispatch", summary="重试单个 outbox 事件")
async def dispatch_single_outbox(
    event_pk: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    result = await db.execute(select(BusinessEventOutbox).where(BusinessEventOutbox.id == event_pk))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    if event.status == "dispatching":
        raise HTTPException(status_code=409, detail="事件正在分发中")
    if event.status == "dead":
        event.status = "pending"
        event.next_retry_at = None
        await db.flush()
    await dispatch_event(db, event)
    return _event_dict(event)


@router.post("/consumptions/{consumption_pk}/reprocess", summary="重新消费单条事件")
async def reprocess_event_consumption(
    consumption_pk: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    result = await db.execute(
        select(BusinessEventConsumption).where(BusinessEventConsumption.id == consumption_pk)
    )
    consumption = result.scalar_one_or_none()
    if consumption is None:
        raise HTTPException(status_code=404, detail="消费记录不存在")
    if consumption.status == "processing":
        raise HTTPException(status_code=409, detail="消费记录正在处理中")
    if consumption.status == "succeeded":
        raise HTTPException(status_code=409, detail="消费记录已成功，避免重复执行业务副作用")

    event_result = await db.execute(
        select(BusinessEventOutbox).where(BusinessEventOutbox.event_id == consumption.event_id)
    )
    event = event_result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail="关联事件不存在")
    if event.status == "dispatching":
        raise HTTPException(status_code=409, detail="事件正在分发中")

    consumption.status = "failed"
    consumption.last_error = None
    event.status = "pending"
    event.next_retry_at = None
    await db.flush()
    await dispatch_event(db, event)

    row = _consumption_dict(consumption)
    row["event_type"] = event.event_type
    row["event_status"] = event.status
    return row
