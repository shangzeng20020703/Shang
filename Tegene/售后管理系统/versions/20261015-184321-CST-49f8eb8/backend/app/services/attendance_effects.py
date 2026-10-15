"""
审批事件到考勤 effect 的消费者。

消费者只订阅审批终态事件，把审批单据转换为 attendance_effects，并创建日重算
dirty 标记。请假余额扣减仍由请假域服务负责，审批服务现有同步逻辑也暂时保留。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.attendance import (
    AttendanceEffect,
    AttendancePunchTimeRecord,
    AttendanceRecord,
    AttendanceStatus,
    ClockSource,
    CorrectionStatus,
)
from app.models.business_event import BusinessEventOutbox
from app.services.attendance_recalc import AttendanceRecalcService


CST = ZoneInfo("Asia/Shanghai")

EFFECT_PRIORITIES = {
    "punch_correction": 100,
    "business_trip": 90,
    "leave": 80,
    "outside": 70,
    "overtime": 60,
}

LEAVE_TYPE_CODE_BY_NAME = {
    "年假": "annual",
    "婚假": "marriage",
    "产休假": "maternity",
    "产假": "maternity",
    "陪产假": "paternity",
    "病假": "sick",
    "调休": "comp_time",
    "事假": "personal",
    "病假（长期）": "sick_long",
    "长期病假": "sick_long",
    "产检假": "prenatal_check",
    "丧假": "bereavement",
    "病假（通用）": "sick_general",
}


class AttendanceApprovalEffectConsumer:
    """将审批终态事件转换为考勤影响记录。"""

    name = "attendance.approval_effect_consumer"
    event_types = {
        "approval.instance.approved.v1",
        "approval.instance.rejected.v1",
        "approval.instance.revoked.v1",
        "approval.instance.cancelled.v1",
    }

    async def handle(self, db: AsyncSession, event: BusinessEventOutbox) -> bool | None:
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        if event.event_type != "approval.instance.approved.v1":
            return await self._revoke_effects(db, event, payload)

        binding = _attendance_binding(payload)
        effect_type = _binding_effect_type(binding) or _infer_effect_type(payload)
        if not effect_type:
            return False

        rows = await _build_effect_rows(db, event, payload, effect_type, binding)
        if not rows:
            return False

        touched_dates: set[tuple[int, date]] = set()
        for row in rows:
            effect = await _upsert_effect(db, row)
            work_date = getattr(effect, "work_date", None)
            employee_id = getattr(effect, "employee_id", None)
            if work_date and employee_id:
                touched_dates.add((int(employee_id), work_date))
                await AttendanceRecalcService.mark_dirty(
                    db,
                    employee_id=int(employee_id),
                    work_date=work_date,
                    source_event_id=event.event_id,
                )

        if effect_type == "punch_correction":
            await _ensure_approval_punch_record(db, event, payload, binding)
        else:
            await _refresh_touched_months(db, touched_dates)

        return True

    async def _revoke_effects(
        self,
        db: AsyncSession,
        event: BusinessEventOutbox,
        payload: dict[str, Any],
    ) -> bool:
        instance_id = _safe_int(payload.get("approval_instance_id"))
        business_id = _safe_int(payload.get("business_id"))
        business_type = str(payload.get("business_type") or payload.get("module") or "").strip()

        conditions = [AttendanceEffect.source_module == "approval", AttendanceEffect.effect_status == "active"]
        if instance_id:
            conditions.append(AttendanceEffect.source_instance_id == instance_id)
        elif business_id and business_type:
            conditions.extend(
                [
                    AttendanceEffect.source_business_id == business_id,
                    AttendanceEffect.source_business_type == business_type,
                ]
            )
        else:
            return False

        result = await db.execute(select(AttendanceEffect).where(and_(*conditions)))
        effects = list(result.scalars().all())
        if not effects:
            return False

        now = datetime.now(timezone.utc)
        touched_dates: set[tuple[int, date]] = set()
        for effect in effects:
            effect.effect_status = "revoked"
            effect.updated_at = now
            payload_json = dict(effect.payload_json or {})
            payload_json["revoked_by_event_id"] = event.event_id
            payload_json["revoked_by_event_type"] = event.event_type
            effect.payload_json = _json_safe(payload_json)
            if effect.work_date and effect.employee_id:
                touched_dates.add((int(effect.employee_id), effect.work_date))
                await AttendanceRecalcService.mark_dirty(
                    db,
                    employee_id=int(effect.employee_id),
                    work_date=effect.work_date,
                    source_event_id=event.event_id,
                )
        await _refresh_touched_months(db, touched_dates)
        return True


def _attendance_binding(payload: dict[str, Any]) -> dict[str, Any]:
    bindings = payload.get("effect_bindings")
    if not isinstance(bindings, list):
        return {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        if binding.get("target_module") == "attendance" and binding.get("effect_type"):
            return binding
    return {}


def _binding_effect_type(binding: dict[str, Any]) -> str:
    return str(binding.get("effect_type") or "").strip()


def _infer_effect_type(payload: dict[str, Any]) -> str:
    module = str(payload.get("module") or "").strip()
    business_type = str(payload.get("business_type") or "").strip()
    candidates = {module, business_type}
    if candidates & {"leave", "leave_request"}:
        return "leave"
    if candidates & {"overtime", "overtime_request", "legal_overtime", "holiday_overtime", "overtime_holiday"}:
        return "overtime"
    if candidates & {"business_trip", "travel"}:
        return "business_trip"
    if candidates & {"outside", "attendance_outside"}:
        return "outside"
    if candidates & {"punch_correction", "attendance_punch_correction"}:
        return "punch_correction"
    if module.startswith("attendance_rule_") and module.endswith("_patch_apply"):
        return "punch_correction"
    return ""


async def _build_effect_rows(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    effect_type: str,
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    if effect_type == "leave":
        return await _build_leave_effects(db, event, payload, binding)
    if effect_type == "overtime":
        return await _build_overtime_effects(db, event, payload, binding)
    if effect_type == "business_trip":
        return _build_business_trip_effects(event, payload, binding)
    if effect_type == "outside":
        return _build_outside_effects(event, payload, binding)
    if effect_type == "punch_correction":
        return await _build_punch_correction_effects(db, event, payload, binding)
    return []


async def _build_leave_effects(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    from app.models.leave import LeaveRequest

    business_id = _safe_int(payload.get("business_id"))
    leave = None
    if business_id:
        result = await db.execute(
            select(LeaveRequest)
            .options(selectinload(LeaveRequest.leave_type))
            .where(LeaveRequest.id == business_id)
        )
        leave = result.scalar_one_or_none()

    if leave is not None:
        employee_id = int(leave.employee_id)
        start_day = leave.start_date
        end_day = leave.end_date
        pay_policy = "paid" if bool(getattr(getattr(leave, "leave_type", None), "is_paid", True)) else "unpaid"
        extra_payload = {
            "leave_request_id": leave.id,
            "leave_type_id": leave.leave_type_id,
            "leave_type_code": getattr(getattr(leave, "leave_type", None), "code", None),
            "leave_days": str(leave.days),
            "start_half": _enum_value(leave.start_half),
            "end_half": _enum_value(leave.end_half),
        }
        return [
            _effect_row(
                event,
                payload,
                employee_id=employee_id,
                effect_type="leave",
                work_date=current,
                start_at=_day_start(current),
                end_at=_day_end(current),
                minutes=_leave_minutes_for_day(leave, current),
                pay_policy=pay_policy,
                attendance_status=AttendanceStatus.on_leave.value,
                payload_extra=extra_payload,
            )
            for current in _date_range(start_day, end_day)
        ]

    form_data = _form_data(payload)
    start_at = _mapped_datetime(
        form_data,
        binding,
        "start_at",
        ("start_time", "start_date", "duration", "leave_duration"),
    )
    end_at = _mapped_datetime(
        form_data,
        binding,
        "end_at",
        ("end_time", "end_date", "duration", "leave_duration"),
    )
    employee_id = _safe_int(payload.get("applicant_id"))
    if not (employee_id and start_at and end_at):
        return []
    total_minutes = _mapped_leave_minutes(form_data, binding) or 0
    leave_type_value = _mapped_value(
        form_data,
        binding,
        "leave_type",
        ("leave_type", "leave_type_name", "leave_type_code"),
    )
    leave_type_code = _leave_type_code(leave_type_value)
    leave_type_name = _leave_type_name(leave_type_value)
    work_dates = list(_date_range(start_at.date(), end_at.date()))
    minutes_by_day = _split_total_minutes_by_dates(total_minutes, work_dates, default_per_day=480)
    return [
        _effect_row(
            event,
            payload,
            employee_id=employee_id,
            effect_type="leave",
            work_date=current,
            start_at=_day_start(current),
            end_at=_day_end(current),
            minutes=minutes_by_day.get(current, 480),
            pay_policy=str(form_data.get("pay_policy") or "paid"),
            attendance_status=AttendanceStatus.on_leave.value,
            payload_extra={
                "leave_type_code": leave_type_code,
                "leave_type_name": leave_type_name,
                "duration": form_data.get("duration") or form_data.get("leave_duration"),
                "reason": form_data.get("reason"),
            },
        )
        for current in _date_range(start_at.date(), end_at.date())
    ]


async def _build_overtime_effects(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    from app.models.overtime import OvertimeRequest

    business_id = _safe_int(payload.get("business_id"))
    overtime = None
    if business_id:
        result = await db.execute(select(OvertimeRequest).where(OvertimeRequest.id == business_id))
        overtime = result.scalar_one_or_none()

    if overtime is not None:
        target_date = overtime.overtime_date
        start_at = _local_time_on_date(target_date, overtime.start_time)
        end_at = _local_time_on_date(target_date, overtime.end_time)
        if end_at <= start_at:
            end_at += timedelta(days=1)
        minutes = int(Decimal(str(overtime.hours or 0)) * Decimal("60"))
        return [
            _effect_row(
                event,
                payload,
                employee_id=int(overtime.employee_id),
                effect_type="overtime",
                work_date=target_date,
                start_at=start_at,
                end_at=end_at,
                minutes=minutes,
                pay_policy="overtime_pay",
                payload_extra={
                    "overtime_request_id": overtime.id,
                    "overtime_type": overtime.overtime_type,
                    "hours": str(overtime.hours),
                },
            )
        ]

    return _build_generic_period_effects(event, payload, binding, "overtime", "overtime_pay")


def _build_business_trip_effects(
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    form_data = _form_data(payload)
    start_at = _mapped_datetime(
        form_data,
        binding,
        "start_at",
        ("start_time", "start_date", "trip_start_time", "trip_start_date", "trip_duration"),
    )
    end_at = _mapped_datetime(
        form_data,
        binding,
        "end_at",
        ("end_time", "end_date", "trip_end_time", "trip_end_date", "trip_duration"),
    )
    if not (start_at and end_at):
        duration_value = form_data.get("trip_duration")
        if isinstance(duration_value, dict):
            start_at = _parse_datetime_like(
                duration_value.get("start") or duration_value.get("start_time") or duration_value.get("start_date")
            )
            end_at = _parse_datetime_like(
                duration_value.get("end") or duration_value.get("end_time") or duration_value.get("end_date")
            )
    employee_id = _safe_int(payload.get("applicant_id"))
    if not (employee_id and start_at and end_at):
        return []
    return [
        _effect_row(
            event,
            payload,
            employee_id=employee_id,
            effect_type="business_trip",
            work_date=current,
            start_at=_day_start(current),
            end_at=_day_end(current),
            minutes=480,
            pay_policy="paid",
            attendance_status=AttendanceStatus.business_trip.value,
            payload_extra={"trip_location": form_data.get("trip_location")},
        )
        for current in _date_range(start_at.date(), end_at.date())
    ]


def _build_outside_effects(
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = _build_generic_period_effects(event, payload, binding, "outside", "paid")
    if rows:
        return rows
    form_data = _form_data(payload)
    occurred_at = _parse_datetime_like(form_data.get("occurred_at"))
    employee_id = _safe_int(form_data.get("employee_id")) or _safe_int(payload.get("applicant_id"))
    if not (employee_id and occurred_at):
        return []
    full_day = bool(form_data.get("full_day_exempt"))
    return [
        _effect_row(
            event,
            payload,
            employee_id=employee_id,
            effect_type="outside",
            work_date=occurred_at.astimezone(CST).date(),
            start_at=_day_start(occurred_at.astimezone(CST).date()) if full_day else occurred_at,
            end_at=_day_end(occurred_at.astimezone(CST).date()) if full_day else occurred_at,
            minutes=480 if full_day else 0,
            pay_policy="paid",
            attendance_status=AttendanceStatus.normal.value if full_day else None,
            payload_extra={
                "address": form_data.get("address"),
                "place_title": form_data.get("place_title"),
                "client_name": form_data.get("client_name"),
            },
        )
    ]


async def _build_punch_correction_effects(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    from app.models.attendance import PunchCorrectionRequest

    business_id = _safe_int(payload.get("business_id"))
    correction = None
    if business_id:
        result = await db.execute(
            select(PunchCorrectionRequest).where(PunchCorrectionRequest.id == business_id)
        )
        correction = result.scalar_one_or_none()

    if correction is not None:
        punch_time = _stored_or_aware_punch_time(correction.punch_time)
        return [
            _effect_row(
                event,
                payload,
                employee_id=int(correction.employee_id),
                effect_type="punch_correction",
                work_date=correction.correction_date,
                start_at=punch_time,
                end_at=punch_time,
                minutes=0,
                pay_policy=None,
                payload_extra={
                    "correction_id": correction.id,
                    "punch_type": correction.punch_type,
                    "punch_time": _safe_iso(correction.punch_time),
                    "reason": correction.reason,
                },
            )
        ]

    dynamic_punch = _dynamic_punch_correction_data(payload, binding)
    if dynamic_punch:
        raw_punch_time = dynamic_punch["punch_time"]
        punch_time = _approval_form_punch_storage_time(raw_punch_time)
        return [
            _effect_row(
                event,
                payload,
                employee_id=dynamic_punch["employee_id"],
                effect_type="punch_correction",
                work_date=dynamic_punch["work_date"],
                start_at=punch_time,
                end_at=punch_time,
                minutes=0,
                pay_policy=None,
                payload_extra={
                    "punch_type": dynamic_punch.get("punch_type"),
                    "punch_time": raw_punch_time.isoformat(),
                    "reason": dynamic_punch.get("reason"),
                    "source": "dynamic_approval_form",
                },
            )
        ]

    return _build_generic_period_effects(event, payload, binding, "punch_correction", None)


def _build_generic_period_effects(
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    binding: dict[str, Any],
    effect_type: str,
    pay_policy: Optional[str],
) -> list[dict[str, Any]]:
    form_data = _form_data(payload)
    if effect_type == "overtime":
        start_aliases = (
            "start_at",
            "start_time",
            "start_date",
            "overtime_start_time",
            "overtime_duration_start_time",
            "duration_start_time",
            "overtime_date",
            "date",
        )
        end_aliases = (
            "end_at",
            "end_time",
            "end_date",
            "overtime_end_time",
            "overtime_duration_end_time",
            "duration_end_time",
            "overtime_date",
            "date",
        )
    else:
        start_aliases = ("start_at", "start_time", "date", "duration", "outside_duration")
        end_aliases = ("end_at", "end_time", "date", "duration", "outside_duration")
    start_raw = _mapped_raw_value(form_data, binding, "start_at", start_aliases)
    end_raw = _mapped_raw_value(form_data, binding, "end_at", end_aliases)
    start_at = _parse_datetime_like(start_raw)
    end_at = _parse_datetime_like(end_raw) or start_at
    if (
        effect_type == "outside"
        and end_raw not in (None, "")
        and start_at
        and end_at
        and end_at >= start_at
        and not _form_value_has_time(end_raw)
    ):
        end_at = _day_end(end_at.astimezone(CST).date() if end_at.tzinfo else end_at.date())
    if start_at and end_at and end_at < start_at:
        end_at += timedelta(days=1)
    employee_id = _safe_int(form_data.get("employee_id")) or _safe_int(payload.get("applicant_id"))
    if not (employee_id and start_at and end_at):
        return []
    minutes = (
        _mapped_overtime_minutes(form_data, binding, start_at, end_at)
        if effect_type == "overtime"
        else _mapped_period_minutes(form_data, binding, start_raw, end_raw)
    ) or max(
        0,
        int((end_at - start_at).total_seconds() // 60),
    )
    return [
        _effect_row(
            event,
            payload,
            employee_id=employee_id,
            effect_type=effect_type,
            work_date=current,
            start_at=segment_start,
            end_at=segment_end,
            minutes=segment_minutes,
            pay_policy=pay_policy,
            attendance_status=AttendanceStatus.normal.value if effect_type == "outside" and segment_minutes >= 480 else None,
        )
        for current, segment_start, segment_end, segment_minutes in _split_period_by_day(
            start_at,
            end_at,
            override_total_minutes=minutes,
        )
    ]


async def _upsert_effect(db: AsyncSession, row: dict[str, Any]) -> AttendanceEffect:
    source_instance_id = row.get("source_instance_id")
    conditions = [
        AttendanceEffect.effect_type == row["effect_type"],
        AttendanceEffect.employee_id == row["employee_id"],
        AttendanceEffect.work_date == row.get("work_date"),
        AttendanceEffect.start_at == row.get("start_at"),
        AttendanceEffect.end_at == row.get("end_at"),
    ]
    if source_instance_id:
        conditions.append(AttendanceEffect.source_instance_id == source_instance_id)
    else:
        conditions.append(AttendanceEffect.source_event_id == row["source_event_id"])

    result = await db.execute(select(AttendanceEffect).where(and_(*conditions)).limit(1))
    effect = result.scalar_one_or_none()
    if effect is None:
        effect = AttendanceEffect(**row)
        db.add(effect)
    else:
        for key, value in row.items():
            setattr(effect, key, value)
    await db.flush()
    return effect


async def _ensure_approval_punch_record(
    db: AsyncSession,
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    binding: dict[str, Any],
) -> None:
    from app.models.attendance import PunchCorrectionRequest

    business_id = _safe_int(payload.get("business_id"))
    correction = None
    if business_id:
        result = await db.execute(
            select(PunchCorrectionRequest).where(PunchCorrectionRequest.id == business_id)
        )
        correction = result.scalar_one_or_none()
    if correction is not None:
        employee_id = int(correction.employee_id)
        work_date = correction.correction_date
        punch_time = _stored_or_aware_punch_time(correction.punch_time)
        raw_punch_type = correction.punch_type
        reason = correction.reason
    else:
        dynamic_punch = _dynamic_punch_correction_data(payload, binding)
        if dynamic_punch is None:
            return
        employee_id = int(dynamic_punch["employee_id"])
        work_date = dynamic_punch["work_date"]
        punch_time = _approval_form_punch_storage_time(dynamic_punch["punch_time"])
        raw_punch_type = dynamic_punch.get("punch_type")
        reason = dynamic_punch.get("reason")

    normalized_punch_type = _normalize_punch_type(raw_punch_type)
    if normalized_punch_type not in {"check_in", "check_out"}:
        return
    punch_type = "clock_in" if normalized_punch_type == "check_in" else "clock_out"
    existing_result = await db.execute(
        select(AttendancePunchTimeRecord)
        .where(
            AttendancePunchTimeRecord.employee_id == employee_id,
            AttendancePunchTimeRecord.work_date == work_date,
            AttendancePunchTimeRecord.punch_time == punch_time,
            AttendancePunchTimeRecord.punch_type == punch_type,
            AttendancePunchTimeRecord.source == "approval",
        )
        .limit(1)
    )
    if existing_result.scalar_one_or_none() is not None:
        return
    record_result = await db.execute(
        select(AttendanceRecord)
        .where(
            AttendanceRecord.employee_id == employee_id,
            AttendanceRecord.date == work_date,
        )
        .order_by(AttendanceRecord.id.desc())
        .limit(1)
    )
    attendance_record = record_result.scalar_one_or_none()
    if attendance_record is None:
        attendance_record = AttendanceRecord(
            employee_id=employee_id,
            date=work_date,
            source=ClockSource.manual,
        )
        db.add(attendance_record)
        await db.flush()
    db.add(
        AttendancePunchTimeRecord(
            employee_id=employee_id,
            attendance_record_id=getattr(attendance_record, "id", None),
            work_date=work_date,
            punch_time=punch_time,
            punch_type=punch_type,
            source="approval",
            remark=f"审批补卡事件 {event.event_id}",
        )
    )
    await _apply_approval_punch_to_attendance_record(
        db,
        attendance_record,
        employee_id=employee_id,
        work_date=work_date,
        punch_time=punch_time,
        punch_type=punch_type,
        reason=reason,
    )


async def _apply_approval_punch_to_attendance_record(
    db: AsyncSession,
    record: AttendanceRecord,
    *,
    employee_id: int,
    work_date: date,
    punch_time: datetime,
    punch_type: str,
    reason: Any,
) -> None:
    from app.services.attendance import AttendanceRecordService

    def _punch_compare_value(value: datetime) -> datetime:
        local_value = AttendanceRecordService._to_cst_datetime(value)
        if local_value is not None:
            return local_value
        return value if value.tzinfo else value.replace(tzinfo=CST)

    if punch_type == "clock_in":
        if record.clock_in_time is None or _punch_compare_value(punch_time) < _punch_compare_value(record.clock_in_time):
            record.clock_in_time = punch_time
    else:
        if record.clock_out_time is None or _punch_compare_value(punch_time) > _punch_compare_value(record.clock_out_time):
            record.clock_out_time = punch_time
    record.source = ClockSource.manual
    record.correction_status = CorrectionStatus.approved
    record.correction_note = str(reason or "审批补卡通过")

    runtime = await AttendanceRecordService._get_rule_runtime(db, employee_id, work_date)
    anomalies: list[str] = []
    await AttendanceRecordService._recalculate_record_after_punch(
        db,
        record,
        employee_id,
        work_date,
        runtime,
        anomalies,
    )
    rebuilt_text = AttendanceRecordService._rebuild_anomaly_text_with_status(record, runtime)
    parts = [part.strip() for part in str(rebuilt_text or "").split(";") if part.strip()]
    seen = set(parts)
    for item in anomalies:
        item_text = str(item or "").strip()
        if item_text and item_text not in seen:
            parts.append(item_text)
            seen.add(item_text)
    record.anomaly_type = "; ".join(parts) if parts else None
    await AttendanceRecordService._refresh_month_summary_for_date(db, employee_id, work_date)
    await db.flush()


def _effect_row(
    event: BusinessEventOutbox,
    payload: dict[str, Any],
    *,
    employee_id: int,
    effect_type: str,
    work_date: date,
    start_at: Optional[datetime],
    end_at: Optional[datetime],
    minutes: int,
    pay_policy: Optional[str],
    attendance_status: Optional[str] = None,
    payload_extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload_json = {
        "approval_event": payload,
        "effect_detail": payload_extra or {},
    }
    return {
        "source_module": "approval",
        "source_event_id": event.event_id,
        "source_instance_id": _safe_int(payload.get("approval_instance_id")),
        "source_business_type": str(payload.get("business_type") or ""),
        "source_business_id": _safe_int(payload.get("business_id")),
        "employee_id": int(employee_id),
        "effect_type": effect_type,
        "effect_status": "active",
        "start_at": start_at,
        "end_at": end_at,
        "work_date": work_date,
        "minutes": int(minutes or 0),
        "priority": EFFECT_PRIORITIES.get(effect_type, 0),
        "pay_policy": pay_policy,
        "attendance_status": attendance_status,
        "payload_json": _json_safe(payload_json),
        "applied_at": None,
    }


def _form_data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("form_data")
    return value if isinstance(value, dict) else {}


def _mapped_value(form_data: dict[str, Any], binding: dict[str, Any], target_key: str, aliases: tuple[str, ...]) -> Any:
    field_map = binding.get("field_map") if isinstance(binding.get("field_map"), dict) else {}
    mapped_key = field_map.get(target_key)
    if mapped_key and form_data.get(mapped_key) not in (None, ""):
        return form_data.get(mapped_key)
    for key in aliases:
        if form_data.get(key) not in (None, ""):
            return form_data.get(key)
    return None


def _mapped_raw_value(
    form_data: dict[str, Any],
    binding: dict[str, Any],
    target_key: str,
    aliases: tuple[str, ...],
) -> Any:
    value = _mapped_value(form_data, binding, target_key, aliases)
    if target_key in {"start_at", "end_at"} and value not in (None, ""):
        if _parse_datetime_like(value) is not None:
            return value
        dynamic_value = _dynamic_period_boundary_value(form_data, binding, target_key, aliases)
        if dynamic_value not in (None, ""):
            return dynamic_value
    if value not in (None, ""):
        return value
    if target_key in {"start_at", "end_at"}:
        return _dynamic_period_boundary_value(form_data, binding, target_key, aliases)
    return None


def _mapped_datetime(
    form_data: dict[str, Any],
    binding: dict[str, Any],
    target_key: str,
    aliases: tuple[str, ...],
) -> Optional[datetime]:
    return _parse_datetime_like(_mapped_raw_value(form_data, binding, target_key, aliases))


def _safe_iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _approval_form_punch_storage_time(value: datetime) -> datetime:
    from app.services.attendance import AttendanceRecordService

    normalized = AttendanceRecordService._local_business_datetime_to_storage(value)
    return normalized or value


def _stored_or_aware_punch_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return _approval_form_punch_storage_time(value)


def _mapped_int(form_data: dict[str, Any], binding: dict[str, Any], target_key: str) -> int:
    value = _mapped_value(form_data, binding, target_key, (target_key,))
    try:
        return int(Decimal(str(value)))
    except Exception:
        return 0


def _mapped_period_minutes(
    form_data: dict[str, Any],
    binding: dict[str, Any],
    start_raw: Any,
    end_raw: Any,
) -> int:
    minutes = _mapped_int(form_data, binding, "minutes")
    if minutes > 0:
        return minutes

    duration_value = _mapped_duration_value(
        form_data,
        binding,
        "duration",
        ("duration", "outside_duration", "duration_days", "days", "time_length"),
    )
    parsed = _decimal_from_form_value(duration_value)
    if parsed is None:
        return 0

    raw_duration = str(duration_value or "")
    if "分钟" in raw_duration or "minute" in raw_duration.lower():
        return max(0, int(parsed))
    if "小时" in raw_duration or "hour" in raw_duration.lower():
        return max(0, int(parsed * Decimal("60")))
    if "天" in raw_duration or "day" in raw_duration.lower():
        return max(0, int(parsed * Decimal("480")))
    if _form_value_has_time(start_raw) or _form_value_has_time(end_raw):
        return max(0, int(parsed * Decimal("60")))
    return max(0, int(parsed * Decimal("480")))


def _mapped_leave_minutes(form_data: dict[str, Any], binding: dict[str, Any]) -> int:
    minutes = _mapped_int(form_data, binding, "minutes")
    if minutes > 0:
        return minutes
    duration_value = _mapped_duration_value(
        form_data,
        binding,
        "duration",
        ("duration", "leave_duration", "duration_days", "days"),
    )
    parsed = _decimal_from_form_value(duration_value)
    if parsed is None:
        return 0
    raw = str(duration_value or "")
    if "分钟" in raw or "minute" in raw.lower():
        return max(0, int(parsed))
    if "小时" in raw or "hour" in raw.lower():
        return max(0, int(parsed * Decimal("60")))
    return max(0, int(parsed * Decimal("480")))


def _leave_type_code(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    known_codes = set(LEAVE_TYPE_CODE_BY_NAME.values())
    if lowered in known_codes:
        return lowered
    return LEAVE_TYPE_CODE_BY_NAME.get(raw)


def _leave_type_name(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw in LEAVE_TYPE_CODE_BY_NAME:
        return raw
    lowered = raw.lower()
    for name, code in LEAVE_TYPE_CODE_BY_NAME.items():
        if lowered == code:
            return name
    return raw


def _decimal_from_form_value(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("minutes", "minute", "hours", "hour", "days", "day", "value", "duration"):
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


def _form_value_has_time(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_form_value_has_time(value.get(key)) for key in ("start", "start_time", "end", "end_time", "value"))
    if isinstance(value, datetime):
        return True
    raw = str(value or "").strip()
    return bool(re.search(r"(T|\s)\d{1,2}:\d{2}", raw))


def _mapped_overtime_minutes(
    form_data: dict[str, Any],
    binding: dict[str, Any],
    start_at: datetime,
    end_at: datetime,
) -> int:
    minute_value = _mapped_value(
        form_data,
        binding,
        "minutes",
        ("minutes", "overtime_minutes", "duration_minutes"),
    )
    parsed_minutes = _decimal_from_form_value(minute_value)
    if parsed_minutes is not None:
        return max(0, int(parsed_minutes))

    hour_value = _mapped_value(
        form_data,
        binding,
        "hours",
        ("hours", "overtime_hours", "duration_hours"),
    )
    parsed_hours = _decimal_from_form_value(hour_value)
    if parsed_hours is not None:
        return max(0, int(parsed_hours * Decimal("60")))

    duration_value = _mapped_duration_value(
        form_data,
        binding,
        "duration",
        ("overtime_duration", "duration", "work_duration", "time_length"),
    )
    parsed_duration = _decimal_from_form_value(duration_value)
    if parsed_duration is None:
        return 0

    raw_duration = str(duration_value or "")
    if "分钟" in raw_duration or "minute" in raw_duration.lower():
        return max(0, int(parsed_duration))
    if "天" in raw_duration or "day" in raw_duration.lower():
        return max(0, int(parsed_duration * Decimal("480")))
    if "小时" in raw_duration or "hour" in raw_duration.lower():
        return max(0, int(parsed_duration * Decimal("60")))

    start_raw = _mapped_raw_value(
        form_data,
        binding,
        "start_at",
        ("overtime_duration_start_time", "overtime_start_time", "duration_start_time", "start_at", "start_time", "start_date"),
    )
    end_raw = _mapped_raw_value(
        form_data,
        binding,
        "end_at",
        ("overtime_duration_end_time", "overtime_end_time", "duration_end_time", "end_at", "end_time", "end_date"),
    )
    has_time = _form_value_has_time(start_raw) or _form_value_has_time(end_raw)
    if has_time:
        return max(0, int(parsed_duration * Decimal("60")))
    if start_at.date() == end_at.date() and end_at > start_at:
        return max(0, int(parsed_duration * Decimal("60")))
    return max(0, int(parsed_duration * Decimal("480")))


def _mapped_duration_value(
    form_data: dict[str, Any],
    binding: dict[str, Any],
    target_key: str,
    aliases: tuple[str, ...],
) -> Any:
    value = _mapped_value(form_data, binding, target_key, aliases)
    if value not in (None, ""):
        return value

    field_map = binding.get("field_map") if isinstance(binding.get("field_map"), dict) else {}
    mapped_key = field_map.get(target_key)
    if mapped_key and form_data.get(mapped_key) not in (None, ""):
        return form_data.get(mapped_key)

    base_candidates = _period_base_candidates(aliases)
    for key, candidate in form_data.items():
        if candidate in (None, "") or key.endswith(("_start_time", "_end_time", "_start_date", "_end_date")):
            continue
        has_boundary = any(
            boundary_key in form_data
            for boundary_key in (
                f"{key}_start_time",
                f"{key}_end_time",
                f"{key}_start_date",
                f"{key}_end_date",
            )
        )
        if has_boundary and _period_base_matches(key, base_candidates):
            return candidate
    return None


def _dynamic_period_boundary_value(
    form_data: dict[str, Any],
    binding: dict[str, Any],
    target_key: str,
    aliases: tuple[str, ...],
) -> Any:
    suffix = "_start_time" if target_key == "start_at" else "_end_time"
    date_suffix = "_start_date" if target_key == "start_at" else "_end_date"
    field_map = binding.get("field_map") if isinstance(binding.get("field_map"), dict) else {}
    mapped_key = field_map.get(target_key)
    exact_candidates: list[str] = []
    if mapped_key:
        exact_candidates.extend([mapped_key, f"{mapped_key}{suffix}", f"{mapped_key}{date_suffix}"])

    base_candidates = _period_base_candidates(aliases)
    for base in base_candidates:
        exact_candidates.extend([f"{base}{suffix}", f"{base}{date_suffix}"])

    for key in exact_candidates:
        if form_data.get(key) not in (None, ""):
            return form_data.get(key)

    matches: list[tuple[int, int, Any]] = []
    for index, (key, value) in enumerate(form_data.items()):
        if value in (None, ""):
            continue
        if key.endswith(suffix):
            base = key[: -len(suffix)]
        elif key.endswith(date_suffix):
            base = key[: -len(date_suffix)]
        else:
            continue
        if not _period_base_matches(base, base_candidates):
            continue
        score = 2 if base in base_candidates else 1
        matches.append((score, -index, value))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][2]


def _period_base_candidates(aliases: tuple[str, ...]) -> set[str]:
    generic = {"start_at", "end_at", "start_time", "end_time", "start_date", "end_date", "date"}
    result: set[str] = set()
    for alias in aliases:
        base = alias
        for suffix in ("_start_time", "_end_time", "_start_date", "_end_date"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if base and base not in generic:
            result.add(base)
    return result


def _period_base_matches(base: str, candidates: set[str]) -> bool:
    if base in candidates:
        return True
    if any(base.startswith(f"{candidate}_") for candidate in candidates):
        return True
    if "duration" in base and (not candidates or any("duration" in candidate for candidate in candidates)):
        return True
    return False


def _parse_date_like(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(CST).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    parsed_at = _parse_datetime_like(value)
    return parsed_at.astimezone(CST).date() if parsed_at else None


def _normalize_punch_type(value: Any) -> str:
    raw = str(value or "").strip()
    normalized = raw.lower()
    if normalized in {"check_in", "clock_in", "in", "on"}:
        return "check_in"
    if normalized in {"check_out", "clock_out", "out", "off"}:
        return "check_out"
    if "上班" in raw or "签到" in raw:
        return "check_in"
    if "下班" in raw or "签退" in raw:
        return "check_out"
    return normalized or raw


def _dynamic_punch_correction_data(
    payload: dict[str, Any],
    binding: dict[str, Any],
) -> Optional[dict[str, Any]]:
    form_data = _form_data(payload)
    employee_id = (
        _safe_int(_mapped_value(form_data, binding, "employee_id", ("employee_id",)))
        or _safe_int(payload.get("applicant_id"))
    )
    punch_time = _mapped_datetime(
        form_data,
        binding,
        "punch_time",
        ("punch_time", "datetime_3", "datetime", "date_time"),
    )
    work_date = _parse_date_like(
        _mapped_value(
            form_data,
            binding,
            "work_date",
            ("correction_date", "target_date", "date_1", "date"),
        )
    )
    if work_date is None and punch_time is not None:
        work_date = punch_time.astimezone(CST).date()
    punch_type = _normalize_punch_type(
        _mapped_value(
            form_data,
            binding,
            "punch_type",
            ("punch_type", "punch_correction_slot", "correction_slot", "punch_slot", "slot", "select_2"),
        )
    )
    if punch_type not in {"check_in", "check_out"}:
        return None
    if not (employee_id and punch_time and work_date):
        return None
    return {
        "employee_id": employee_id,
        "work_date": work_date,
        "punch_time": punch_time,
        "punch_type": punch_type,
        "reason": (
            form_data.get("patch_reason")
            or form_data.get("textarea_4")
            or form_data.get("reason")
        ),
    }


async def _refresh_touched_months(db: AsyncSession, touched_dates: set[tuple[int, date]]) -> None:
    """按员工+月份刷新未锁定月报；测试替身缺少 AsyncSession.scalar 时跳过。"""
    if not touched_dates or not callable(getattr(db, "scalar", None)):
        return
    from app.services.attendance import AttendanceRecordService

    refreshed: set[tuple[int, int, int]] = set()
    for employee_id, work_date in sorted(touched_dates, key=lambda item: (item[0], item[1])):
        key = (employee_id, work_date.year, work_date.month)
        if key in refreshed:
            continue
        refreshed.add(key)
        await AttendanceRecordService._refresh_month_summary_for_date(db, employee_id, work_date)


def _split_total_minutes_by_dates(
    total_minutes: int,
    work_dates: list[date],
    *,
    default_per_day: int,
) -> dict[date, int]:
    if not work_dates:
        return {}
    if total_minutes <= 0:
        return {item: default_per_day for item in work_dates}
    if len(work_dates) == 1:
        return {work_dates[0]: total_minutes}

    default_total = default_per_day * len(work_dates)
    if total_minutes >= default_total:
        return {item: default_per_day for item in work_dates}

    base = total_minutes // len(work_dates)
    remainder = total_minutes % len(work_dates)
    result: dict[date, int] = {}
    for index, item in enumerate(work_dates):
        result[item] = base + (1 if index < remainder else 0)
    return result


def _split_period_by_day(
    start_at: datetime,
    end_at: datetime,
    *,
    override_total_minutes: int = 0,
) -> list[tuple[date, datetime, datetime, int]]:
    if end_at < start_at:
        end_at += timedelta(days=1)
    if end_at == start_at:
        return [(start_at.astimezone(CST).date(), start_at, end_at, max(0, int(override_total_minutes or 0)))]

    start_local = start_at.astimezone(CST) if start_at.tzinfo else start_at.replace(tzinfo=CST)
    end_local = end_at.astimezone(CST) if end_at.tzinfo else end_at.replace(tzinfo=CST)
    raw_segments: list[tuple[date, datetime, datetime, int]] = []
    for current in _date_range(start_local.date(), end_local.date()):
        segment_start = max(start_local, _day_start(current))
        segment_end = min(end_local, _day_end(current))
        if segment_end <= segment_start:
            continue
        raw_minutes = max(0, int((segment_end - segment_start).total_seconds() // 60))
        raw_segments.append((current, segment_start, segment_end, raw_minutes))

    if not raw_segments:
        return [(start_local.date(), start_local, end_local, max(0, int(override_total_minutes or 0)))]

    total_minutes = max(0, int(override_total_minutes or 0))
    raw_total = sum(item[3] for item in raw_segments)
    if total_minutes <= 0 or raw_total == total_minutes:
        return raw_segments
    if raw_total <= 0:
        only = raw_segments[0]
        return [(only[0], only[1], only[2], total_minutes)]

    allocated: list[tuple[date, datetime, datetime, int]] = []
    remaining = total_minutes
    for index, (work_date, segment_start, segment_end, raw_minutes) in enumerate(raw_segments):
        if index == len(raw_segments) - 1:
            minutes = remaining
        else:
            minutes = int((Decimal(raw_minutes) * Decimal(total_minutes) / Decimal(raw_total)).to_integral_value())
            minutes = max(0, min(minutes, remaining))
        allocated.append((work_date, segment_start, segment_end, minutes))
        remaining -= minutes
    return allocated


def _parse_datetime_like(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=CST)
    if isinstance(value, date):
        return _day_start(value)
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(raw[:10])
        except ValueError:
            return None
        return _day_start(parsed_date)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=CST)


def _local_time_on_date(target_date: date, hm: Any) -> datetime:
    try:
        parsed_time = time.fromisoformat(str(hm or "00:00"))
    except ValueError:
        parsed_time = time(0, 0)
    return datetime.combine(target_date, parsed_time, tzinfo=CST)


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=CST)


def _day_end(value: date) -> datetime:
    return datetime.combine(value, time.max.replace(microsecond=0), tzinfo=CST)


def _date_range(start_day: date, end_day: date) -> list[date]:
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    result: list[date] = []
    current = start_day
    while current <= end_day:
        result.append(current)
        current += timedelta(days=1)
    return result


def _leave_minutes_for_day(leave: Any, current: date) -> int:
    start_half = _enum_value(getattr(leave, "start_half", "am"))
    end_half = _enum_value(getattr(leave, "end_half", "pm"))
    if leave.start_date == leave.end_date:
        return 240 if start_half == end_half else 480
    if current == leave.start_date and start_half == "pm":
        return 240
    if current == leave.end_date and end_half == "am":
        return 240
    return 480


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _safe_int(value: Any) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
