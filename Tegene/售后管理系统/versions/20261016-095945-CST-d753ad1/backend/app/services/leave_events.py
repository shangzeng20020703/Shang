"""
请假审批事件消费者。

审批服务只负责发布终态事件；本消费者负责把请假审批通过落实到员工假期
余额，并写入假期余额页可读取的审计记录。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit_log import AuditLog
from app.models.business_event import BusinessEventOutbox
from app.models.leave import ApprovalStatus, DeductFrom, LeaveBalance, LeaveRequest, LeaveType
from app.services.leave import LeaveBalanceService, LeaveTypeService


LEAVE_TYPE_CODE_BY_NAME = {
    "年假": "annual",
    "婚假": "marriage",
    "产休假": "maternity",
    "产假": "maternity",
    "陪产假": "paternity",
    "病假": "sick",
    "调休": "comp_time",
    "事假": "personal",
    "丧假": "bereavement",
    "产检假": "prenatal_check",
}


class LeaveApprovalBalanceConsumer:
    """消费请假审批通过事件，扣减员工对应假期余额。"""

    name = "leave.approval_balance_consumer"
    event_types = {"approval.instance.approved.v1"}

    async def handle(self, db: AsyncSession, event: BusinessEventOutbox) -> bool | None:
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        if event.event_type != "approval.instance.approved.v1" or not _is_leave_payload(payload):
            return False

        leave = None
        if _payload_may_reference_leave_request(payload):
            leave = await _load_leave_request(db, _safe_int(payload.get("business_id")))
        if leave is None:
            return await _handle_dynamic_leave_approval(db, event, payload)

        leave_type = getattr(leave, "leave_type", None)
        if leave_type is None:
            return False
        if not await _should_update_balance(db, int(leave.employee_id), leave_type, int(leave.start_date.year)):
            return False

        if leave.approval_status != ApprovalStatus.approved:
            leave.approval_status = ApprovalStatus.approved
        approver_id = _safe_int(payload.get("approver_id"))
        if approver_id and getattr(leave, "approver_id", None) is None:
            leave.approver_id = approver_id
        if getattr(leave, "approved_at", None) is None:
            leave.approved_at = _parse_datetime(payload.get("approved_at") or payload.get("finished_at")) or getattr(
                event,
                "occurred_at",
                None,
            ) or datetime.now(timezone.utc)

        balance = await LeaveBalanceService.sync_approved_usage_for_leave_type(
            db,
            employee_id=int(leave.employee_id),
            leave_type_id=int(leave.leave_type_id),
            year=int(leave.start_date.year),
        )
        await _append_balance_audit_log(db, event, payload, leave, leave_type, balance)
        return True


def _is_leave_payload(payload: dict[str, Any]) -> bool:
    module = str(payload.get("module") or "").strip()
    business_type = str(payload.get("business_type") or "").strip()
    return module == "leave" or business_type == "leave_request"


def _payload_may_reference_leave_request(payload: dict[str, Any]) -> bool:
    business_type = str(payload.get("business_type") or "").strip()
    if business_type == "leave_request":
        return True
    business_id = _safe_int(payload.get("business_id"))
    approval_instance_id = _safe_int(payload.get("approval_instance_id"))
    if not business_id:
        return False
    if approval_instance_id and business_id == approval_instance_id:
        return False
    return True


async def _load_leave_request(db: AsyncSession, request_id: Optional[int]) -> Optional[LeaveRequest]:
    if not request_id:
        return None
    result = await db.execute(
        select(LeaveRequest)
        .options(selectinload(LeaveRequest.leave_type))
        .where(LeaveRequest.id == request_id)
    )
    return result.scalar_one_or_none()


def _leave_type_requires_balance_deduction(leave_type: LeaveType) -> bool:
    quota_limited = bool(
        getattr(leave_type, "quota_limited", getattr(leave_type, "max_days_per_year", None) is not None)
    )
    deduct_from = _enum_value(getattr(leave_type, "deduct_from", DeductFrom.none))
    return quota_limited or deduct_from not in {"", "none", None}


async def _should_update_balance(
    db: AsyncSession,
    employee_id: int,
    leave_type: LeaveType,
    year: int,
) -> bool:
    if _leave_type_requires_balance_deduction(leave_type):
        return True
    existing = await LeaveBalanceService.get_balance(db, employee_id, int(leave_type.id), year)
    return existing is not None


async def _audit_log_exists(db: AsyncSession, event_id: str) -> bool:
    result = await db.execute(
        select(AuditLog.id)
        .where(
            and_(
                AuditLog.module == "leave",
                AuditLog.action == "balance_adjust",
                AuditLog.after_data.contains(event_id),
            )
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _append_balance_audit_log(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    leave: LeaveRequest,
    leave_type: LeaveType,
    balance: LeaveBalance,
) -> None:
    if await _audit_log_exists(db, event.event_id):
        return

    days = Decimal(str(getattr(leave, "days", 0) or 0))
    db.add(
        AuditLog(
            operator_id=None,
            operator_name="系统",
            module="leave",
            action="balance_adjust",
            resource_id=getattr(balance, "id", None),
            resource_type="LeaveBalance",
            before_data=None,
            after_data=json.dumps(
                {
                    "employee_id": int(leave.employee_id),
                    "leave_type_id": int(leave.leave_type_id),
                    "leave_type_name": getattr(leave_type, "name", None),
                    "year": int(leave.start_date.year),
                    "adjustment": -float(days),
                    "reason": _audit_reason(payload, leave, leave_type),
                    "remaining_days": float(balance.remaining_days or 0),
                    "total_days": float(balance.total_days or 0),
                    "used_days": float(balance.used_days or 0),
                    "source_event_id": event.event_id,
                    "approval_instance_id": _safe_int(payload.get("approval_instance_id")),
                    "leave_request_id": int(leave.id),
                    "usage_source": "leave_request",
                },
                ensure_ascii=False,
            ),
        )
    )
    await db.flush()


def _audit_reason(payload: dict[str, Any], leave: LeaveRequest, leave_type: LeaveType) -> str:
    summary = str(payload.get("summary") or "").strip()
    date_range = f"{leave.start_date.isoformat()} 至 {leave.end_date.isoformat()}"
    if summary:
        return f"审批通过自动扣减：{summary}"
    return f"审批通过自动扣减：{getattr(leave_type, 'name', '请假')} {date_range}"


async def _handle_dynamic_leave_approval(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
) -> bool:
    form_data = _form_data(payload)
    binding = _event_binding(payload)
    leave_type_value = _mapped_value(
        form_data,
        binding,
        "leave_type",
        ("leave_type", "leave_type_name", "leave_type_code", "select_1"),
    )
    leave_type = await _resolve_leave_type(db, leave_type_value)
    if leave_type is None:
        return False

    employee_id = _safe_int(
        _mapped_value(form_data, binding, "employee_id", ("employee_id", "applicant_id"))
    ) or _safe_int(payload.get("applicant_id"))
    start_day = _mapped_date(form_data, binding, "start_at")
    end_day = _mapped_date(form_data, binding, "end_at") or start_day
    if not (employee_id and start_day and end_day):
        return False
    if end_day < start_day:
        start_day, end_day = end_day, start_day

    days = _dynamic_leave_days(form_data, binding, leave_type, start_day, end_day)
    if days <= 0:
        return False
    year = int(start_day.year)
    if not await _should_update_balance(db, employee_id, leave_type, year):
        return False
    if await _audit_log_exists(db, event.event_id):
        return True

    balance = await LeaveBalanceService.get_or_create(db, employee_id, int(leave_type.id), year)
    balance.used_days = Decimal(str(balance.used_days or 0)) + days
    remaining_days = Decimal(str(balance.total_days or 0)) - balance.used_days - Decimal(str(balance.expired_days or 0))
    balance.remaining_days = remaining_days if remaining_days > 0 else Decimal("0")
    await db.flush()
    await db.refresh(balance)
    await _append_dynamic_balance_audit_log(
        db,
        event,
        payload,
        leave_type,
        balance,
        employee_id=employee_id,
        start_day=start_day,
        end_day=end_day,
        days=days,
    )
    return True


async def _resolve_leave_type(db: AsyncSession, value: Any) -> Optional[LeaveType]:
    code = _leave_type_code(value)
    if code:
        leave_type = await LeaveTypeService.get_by_code(db, code)
        if leave_type is not None:
            return leave_type
    raw = str(value or "").strip()
    if not raw:
        return None
    result = await db.execute(
        select(LeaveType)
        .where(or_(LeaveType.name == raw, func.lower(LeaveType.code) == raw.lower()))
        .order_by(LeaveType.is_active.desc(), LeaveType.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _leave_type_code(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    known_codes = set(LEAVE_TYPE_CODE_BY_NAME.values())
    if lowered in known_codes:
        return lowered
    return LEAVE_TYPE_CODE_BY_NAME.get(raw)


def _form_data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("form_data")
    return value if isinstance(value, dict) else {}


def _event_binding(payload: dict[str, Any]) -> dict[str, Any]:
    bindings = payload.get("effect_bindings")
    if not isinstance(bindings, list):
        return {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        if binding.get("target_module") in {"attendance", "leave"}:
            return binding
    return {}


def _mapped_value(form_data: dict[str, Any], binding: dict[str, Any], target_key: str, aliases: tuple[str, ...]) -> Any:
    field_map = binding.get("field_map") if isinstance(binding.get("field_map"), dict) else {}
    mapped_key = field_map.get(target_key)
    if mapped_key and form_data.get(mapped_key) not in (None, ""):
        return form_data.get(mapped_key)
    for key in aliases:
        if form_data.get(key) not in (None, ""):
            return form_data.get(key)
    return None


def _mapped_boundary_value(form_data: dict[str, Any], binding: dict[str, Any], target_key: str) -> Any:
    if target_key == "start_at":
        aliases = (
            "start_at",
            "start_time",
            "start_date",
            "leave_start_time",
            "leave_start_date",
            "duration_start_time",
            "duration_start_date",
            "leave_duration_start_time",
            "leave_duration_start_date",
        )
        boundary_keys = ("start", "start_time", "start_date")
        suffixes = ("_start_time", "_start_date")
    else:
        aliases = (
            "end_at",
            "end_time",
            "end_date",
            "leave_end_time",
            "leave_end_date",
            "duration_end_time",
            "duration_end_date",
            "leave_duration_end_time",
            "leave_duration_end_date",
        )
        boundary_keys = ("end", "end_time", "end_date")
        suffixes = ("_end_time", "_end_date")

    value = _mapped_value(form_data, binding, target_key, aliases)
    if value not in (None, ""):
        return value

    for base in ("duration", "leave_duration"):
        period_value = form_data.get(base)
        if isinstance(period_value, dict):
            for key in boundary_keys:
                if period_value.get(key) not in (None, ""):
                    return period_value.get(key)
        for suffix in suffixes:
            if form_data.get(f"{base}{suffix}") not in (None, ""):
                return form_data.get(f"{base}{suffix}")
    return None


def _mapped_date(form_data: dict[str, Any], binding: dict[str, Any], target_key: str) -> Optional[date]:
    return _parse_date_like(_mapped_boundary_value(form_data, binding, target_key))


def _dynamic_leave_days(
    form_data: dict[str, Any],
    binding: dict[str, Any],
    leave_type: LeaveType,
    start_day: date,
    end_day: date,
) -> Decimal:
    duration_value = _mapped_value(
        form_data,
        binding,
        "duration",
        ("duration", "leave_duration", "duration_days", "days", "leave_days", "time_length"),
    )
    parsed = _decimal_from_form_value(duration_value)
    if parsed is not None:
        raw = str(duration_value or "")
        hours_per_day = Decimal(str(getattr(leave_type, "hours_per_day", 8) or 8))
        if hours_per_day <= 0:
            hours_per_day = Decimal("8")
        if "分钟" in raw or "minute" in raw.lower():
            return parsed / (hours_per_day * Decimal("60"))
        if "小时" in raw or "hour" in raw.lower():
            return parsed / hours_per_day
        return parsed
    return Decimal(str((end_day - start_day).days + 1))


def _decimal_from_form_value(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("days", "day", "hours", "hour", "minutes", "minute", "value", "duration"):
            parsed = _decimal_from_form_value(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    return Decimal(match.group(0))


def _parse_date_like(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


async def _append_dynamic_balance_audit_log(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    leave_type: LeaveType,
    balance: LeaveBalance,
    *,
    employee_id: int,
    start_day: date,
    end_day: date,
    days: Decimal,
) -> None:
    db.add(
        AuditLog(
            operator_id=None,
            operator_name="系统",
            module="leave",
            action="balance_adjust",
            resource_id=getattr(balance, "id", None),
            resource_type="LeaveBalance",
            before_data=None,
            after_data=json.dumps(
                {
                    "employee_id": int(employee_id),
                    "leave_type_id": int(leave_type.id),
                    "leave_type_name": getattr(leave_type, "name", None),
                    "year": int(start_day.year),
                    "adjustment": -float(days),
                    "reason": _dynamic_audit_reason(payload, leave_type, start_day, end_day),
                    "remaining_days": float(balance.remaining_days or 0),
                    "total_days": float(balance.total_days or 0),
                    "used_days": float(balance.used_days or 0),
                    "source_event_id": event.event_id,
                    "approval_instance_id": _safe_int(payload.get("approval_instance_id")),
                    "leave_request_id": None,
                    "usage_source": "dynamic_approval",
                    "start_date": start_day.isoformat(),
                    "end_date": end_day.isoformat(),
                },
                ensure_ascii=False,
            ),
        )
    )
    await db.flush()


def _dynamic_audit_reason(
    payload: dict[str, Any],
    leave_type: LeaveType,
    start_day: date,
    end_day: date,
) -> str:
    summary = str(payload.get("summary") or "").strip()
    if summary:
        return f"审批通过自动扣减：{summary}"
    return f"审批通过自动扣减：{getattr(leave_type, 'name', '请假')} {start_day.isoformat()} 至 {end_day.isoformat()}"


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()
