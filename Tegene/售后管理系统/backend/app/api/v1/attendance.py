"""
考勤管理模块 API 路由

本模块提供售后管理系统的考勤相关 RESTful 接口，涵盖以下功能域：

1. 考勤规则 (Attendance Rules)
   - 管理不同工作地点的打卡时间、迟到/早退宽限、加班规则等配置

2. 排班管理 (Shift Schedule)
   - 为员工或整个部门分配班次（支持单条和批量）

3. 打卡 (Clock In / Clock Out)
   - 员工上下班打卡，返回打卡记录，异常情况通过返回值标注

4. 考勤记录查询 (Attendance Records)
   - 按员工、日期范围、出勤状态分页查询

5. 考勤异常与申诉 (Anomalies & Appeals)
   - 迟到/早退/缺卡/旷工等异常记录查询

6. 工作日历 (Work Calendar)
   - 管理法定节假日、调休工作日等日历条目

7. 月度汇总 (Monthly Summary)
   - 生成/查询/确认/锁定/导出员工月度考勤汇总
   - 锁定后数据作为薪资计算的考勤输入

8. 考勤月报导出 (Monthly Report Export)
   - 输出 Excel 格式的月度考勤报告

架构说明：
- 路由层（本文件）只负责参数接收、权限校验和 Schema 转换，不包含业务逻辑
- 业务逻辑全部封装在 app/services/attendance.py 的各 Service 类中
- 权限控制通过 Depends(get_current_user) 和 Depends(require_roles(...)) 实现

相关 Service 类：
- AttendanceRuleService    — 考勤规则 CRUD
- ShiftScheduleService     — 排班 CRUD（含批量）
- AttendanceRecordService  — 打卡、记录查询、异常申诉
- WorkCalendarService      — 工作日历 CRUD（含批量）
- MonthSummaryService      — 月度汇总生成/查询/确认/锁定
- AttendanceReportService  — Excel 月报导出
"""

import base64
import csv
import io
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, inspect, or_, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.services.map_settings import browser_map_key
from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.approval import ApprovalInstance
from app.models.attendance import (
    AttendanceEffect,
    AttendanceMonthSummary,
    AttendanceRecord,
    AttendanceStatus,
    DayType,
    PunchCorrectionRequest,
)
from app.models.employee import Employee
from app.models.leave import LeaveBalance, LeaveRequest, LeaveType
from app.models.overtime import OvertimeRequest
from app.schemas.attendance import (
    AttendanceAppealCreate,
    AttendanceAppealReview,
    AttendanceOutsideApprovalCreate,
    AttendanceOutsideApprovalOut,
    AttendanceRecordOut,
    AttendanceRecordQuery,
    AttendanceRuleConflictOut,
    AttendanceRuleCopyIn,
    AttendanceRuleCreate,
    AttendanceRuleLogOut,
    AttendanceRuleOut,
    AttendanceRuleToggle,
    AttendanceRuleUpdate,
    ClockInRequest,
    ClockOutRequest,
    ClockValidateRequest,
    CorrectionApproval,
    CorrectionRequest,
    MonthSummaryConfirm,
    MonthSummaryGenerateRequest,
    MonthSummaryLock,
    MonthSummaryOut,
    MobileDashboardOut,
    MobileDashboardOvertimeDetailOut,
    OvertimeApplicationCreate,
    PaginatedResponse,
    PunchCorrectionCreate,
    PunchCorrectionEligibilityRequest,
    PunchCorrectionEligibilityResponse,
    PunchCorrectionOut,
    PunchCorrectionReview,
    ShiftScheduleBatchCreate,
    ShiftScheduleCreate,
    ShiftScheduleOut,
    ShiftScheduleUpdate,
    MonthlyReportTaskCreate,
    WorkCalendarBatchCreate,
    WorkCalendarCreate,
    WorkCalendarOut,
    WorkCalendarUpdate,
)
from app.services.attendance import (
    AttendancePunchTimeRecordService,
    AttendanceRecordService,
    AttendanceMonthlyReportTaskService,
    AttendanceReportService,
    AttendanceRuleService,
    MonthSummaryService,
    PunchCorrectionEligibilityService,
    PunchCorrectionService,
    ShiftScheduleService,
    WorkCalendarService,
    create_outside_approval as create_outside_approval_service,
)

router = APIRouter(
    tags=["考勤管理"],
    dependencies=[Depends(get_current_user)],
)
logger = logging.getLogger(__name__)


def _parse_attendance_status_value(raw: str) -> AttendanceStatus:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("empty attendance status")

    legacy_code_map = {
        "missing_punch": "missed_clock",
    }
    normalized = legacy_code_map.get(text.lower(), text.lower())
    if normalized in AttendanceStatus.__members__:
        return AttendanceStatus[normalized]
    return AttendanceStatus(text)


def _parse_attendance_status_filters(
    status_value: Optional[str],
    status_values: Optional[list[str]],
) -> list[AttendanceStatus]:
    raw_values = status_values if status_values else ([status_value] if status_value else [])
    parsed: list[AttendanceStatus] = []
    seen: set[AttendanceStatus] = set()
    for raw in raw_values:
        for item in str(raw or "").split(","):
            text = item.strip()
            if not text:
                continue
            status_item = _parse_attendance_status_value(text)
            if status_item not in seen:
                parsed.append(status_item)
                seen.add(status_item)
    return parsed


def _block_legacy_mvp_write(request: Optional[Request], business_label: str, business_code: str) -> None:
    """
    MVP 阶段假勤申请只允许从通用审批中心提交，旧 HTTP 写入口不再面向
    前端产生新的分叉单据；历史数据处理应走服务层或脚本。
    """
    if request is None:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"{business_label}已收口到审批中心，请使用 "
            f"/api/v1/approval/applications 提交 business_code={business_code} 的申请"
        ),
    )


def _is_tencent_webservice_disabled(message: str) -> bool:
    normalized = str(message or "").lower()
    return (
        "webserviceapi" in normalized
        or "webservice api" in normalized
        or "未开启webservice" in normalized
        or "未开启 webservice" in normalized
    )


def _comp_time_balance_hours_from_balance(
    leave_type: LeaveType,
    balance: Optional[LeaveBalance],
    amount_field: str = "total_days",
) -> Decimal:
    """把调休余额账本中的额度换算成小时，用于移动端累计加班调休展示。"""
    if balance is None:
        return Decimal("0.0")
    try:
        amount = Decimal(str(getattr(balance, amount_field, 0) or 0))
    except Exception:
        return Decimal("0.0")
    if amount <= 0:
        return Decimal("0.0")

    unit = str(getattr(leave_type, "leave_unit", "day") or "day")
    try:
        hours_per_day = Decimal(str(getattr(leave_type, "hours_per_day", None) or 8))
    except Exception:
        hours_per_day = Decimal("8")
    if hours_per_day <= 0:
        hours_per_day = Decimal("8")

    if unit == "hour":
        hours = amount * hours_per_day
    else:
        # 兼容历史调休类型未配置成“小时”时，自动同步逻辑写入的原始小时数。
        hours = amount
    return hours.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _mobile_day_type_key(day_type: DayType | str | None) -> str:
    value = str(getattr(day_type, "value", day_type) or "")
    if value == DayType.holiday.value:
        return "holiday"
    if value == DayType.weekend.value:
        return "restday"
    return "workday"


def _mobile_day_type_label(day_type: DayType | str | None) -> str:
    return {
        "workday": "工作日",
        "restday": "休息日",
        "holiday": "节假日",
    }.get(_mobile_day_type_key(day_type), "工作日")


def _mobile_hm(value: Optional[datetime]) -> Optional[str]:
    local_dt = AttendanceRecordService._to_cst_datetime(value)
    return local_dt.strftime("%H:%M") if local_dt else None


async def _mobile_comp_time_detail_rows(
    db: AsyncSession,
    current_user: Employee,
    year: int,
) -> list[dict[str, Any]]:
    """重建员工本年度加班转调休来源明细，给移动端余额弹层展示具体日期。"""
    start_day = date(year, 1, 1)
    end_day = date(year, 12, 31)
    employee_id = int(current_user.id)
    runtime_cache: dict[date, dict] = {}
    day_type_cache: dict[date, DayType] = {}

    await AttendanceRecordService.sync_comp_time_balances_from_attendance(
        db,
        year=year,
        employee_ids=[employee_id],
        allow_decrease=True,
        recalculate_records=True,
    )

    async def _has_attendance_effects_table() -> bool:
        def _has_table(sync_session) -> bool:
            connection = sync_session.connection()
            return inspect(connection).has_table("attendance_effects")

        try:
            return bool(await db.run_sync(_has_table))
        except Exception:
            return False

    async def _day_type_for(work_day: date) -> DayType:
        cached = day_type_cache.get(work_day)
        if cached is not None:
            return cached
        resolved = await WorkCalendarService.get_day_type(
            db,
            work_day,
            getattr(current_user, "location_id", None),
        )
        day_type_cache[work_day] = resolved
        return resolved

    async def _comp_hours_for_overtime(
        work_day: date,
        hours: Decimal | float | int,
        pay_policy: Optional[str] = None,
    ) -> Decimal:
        policy = str(pay_policy or "").strip()
        if policy in {"comp_time", "rest", "rest_time", "time_off", "lieu", "adjust_rest"}:
            try:
                parsed = Decimal(str(hours or 0))
            except Exception:
                return Decimal("0.0")
            return max(parsed, Decimal("0.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if policy in {"overtime_pay", "payroll", "paid_overtime"}:
            return Decimal("0.0")

        runtime = runtime_cache.get(work_day)
        if runtime is None:
            runtime = await AttendanceRecordService._get_rule_runtime(db, employee_id, work_day)
            runtime_cache[work_day] = runtime
        rule = runtime.get("rule") if isinstance(runtime, dict) else None
        extra_config = getattr(rule, "extra_config", None)
        if not isinstance(extra_config, dict):
            extra_config = {}
        day_type = await _day_type_for(work_day)
        comp_hours, _ = AttendanceRecordService._split_overtime_settlement(
            extra_config,
            day_type,
            hours,
        )
        return max(comp_hours, Decimal("0.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _append_detail(
        items: list[dict[str, Any]],
        seen: set[str],
        *,
        item_id: str,
        work_day: date,
        day_type: DayType | str | None,
        comp_hours: Decimal,
        source: str,
        status_text: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        if comp_hours <= 0 or item_id in seen:
            return
        seen.add(item_id)
        hours = comp_hours.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        minutes = int((comp_hours * Decimal("60")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        items.append(
            {
                "id": item_id,
                "date": work_day.isoformat(),
                "date_type": _mobile_day_type_key(day_type),
                "date_type_label": _mobile_day_type_label(day_type),
                "minutes": minutes,
                "hours": float(hours),
                "settlement": "comp_time",
                "settlement_label": "调休",
                "start_time": start_time,
                "end_time": end_time,
                "status": status_text,
                "reason": reason,
                "source": source,
            }
        )

    def _parse_form_datetime(value: Any) -> Optional[datetime]:
        if value in (None, ""):
            return None
        if isinstance(value, dict):
            for key in ("start", "start_time", "start_date", "end", "end_time", "end_date", "date", "value"):
                parsed = _parse_form_datetime(value.get(key))
                if parsed is not None:
                    return parsed
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        raw = str(value).strip().replace("Z", "+00:00")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=None)
        except ValueError:
            try:
                return datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time())
            except ValueError:
                return None

    def _form_value_has_time(value: Any) -> bool:
        if isinstance(value, dict):
            return any(_form_value_has_time(value.get(key)) for key in ("start", "start_time", "end", "end_time", "value"))
        if isinstance(value, datetime):
            return True
        return bool(re.search(r"(T|\s)\d{1,2}:\d{2}", str(value or "").strip()))

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
        return Decimal(match.group(0)) if match else None

    def _duration_minutes_from_approval_form(
        form_data: dict[str, Any],
        start_at: Optional[datetime],
        end_at: Optional[datetime],
        start_raw: Any,
        end_raw: Any,
    ) -> int:
        for key in ("minutes", "overtime_minutes", "duration_minutes"):
            parsed = _decimal_from_form_value(form_data.get(key))
            if parsed is not None:
                return max(0, int(parsed))
        for key in ("hours", "overtime_hours", "duration_hours"):
            parsed = _decimal_from_form_value(form_data.get(key))
            if parsed is not None:
                return max(0, int(parsed * Decimal("60")))
        parsed_duration = None
        raw_duration = ""
        for key in ("overtime_duration", "duration", "work_duration", "time_length"):
            value = form_data.get(key)
            if value not in (None, ""):
                parsed_duration = _decimal_from_form_value(value)
                raw_duration = str(value or "")
                break
        if parsed_duration is not None:
            if "分钟" in raw_duration or "minute" in raw_duration.lower():
                return max(0, int(parsed_duration))
            if "天" in raw_duration or "day" in raw_duration.lower():
                return max(0, int(parsed_duration * Decimal("480")))
            if "小时" in raw_duration or "hour" in raw_duration.lower():
                return max(0, int(parsed_duration * Decimal("60")))
            has_time = _form_value_has_time(start_raw) or _form_value_has_time(end_raw)
            return max(0, int(parsed_duration * (Decimal("60") if has_time else Decimal("480"))))
        if start_at and end_at and end_at > start_at:
            return max(0, int((end_at - start_at).total_seconds() // 60))
        return 0

    def _first_form_value(form_data: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            value = form_data.get(key)
            if value not in (None, ""):
                return value
        return None

    def _approved_overtime_instance_items(instance: ApprovalInstance) -> list[dict[str, Any]]:
        form_data = instance.form_data if isinstance(instance.form_data, dict) else {}
        duration_value = form_data.get("overtime_duration")
        start_raw = _first_form_value(
            form_data,
            ("overtime_duration_start_time", "overtime_start_time", "duration_start_time", "start_at", "start_time", "start_date", "overtime_date", "date"),
        )
        end_raw = _first_form_value(
            form_data,
            ("overtime_duration_end_time", "overtime_end_time", "duration_end_time", "end_at", "end_time", "end_date", "overtime_date", "date"),
        )
        if isinstance(duration_value, dict):
            start_raw = start_raw or duration_value.get("start") or duration_value.get("start_time")
            end_raw = end_raw or duration_value.get("end") or duration_value.get("end_time")

        start_at = _parse_form_datetime(start_raw)
        end_at = _parse_form_datetime(end_raw) or start_at
        if start_at and end_at and end_at < start_at:
            end_at += timedelta(days=1)
        minutes = _duration_minutes_from_approval_form(form_data, start_at, end_at, start_raw, end_raw)
        if not (start_at and end_at and minutes > 0):
            return []

        period_days: list[date] = []
        current_day = start_at.date()
        final_day = end_at.date()
        while current_day <= final_day:
            period_days.append(current_day)
            current_day += timedelta(days=1)
        visible_days = [work_day for work_day in period_days if start_day <= work_day <= end_day]
        if not visible_days:
            return []
        minutes_per_day = max(1, int(round(minutes / len(period_days))))
        reason = str(form_data.get("overtime_reason") or form_data.get("reason") or instance.summary or "").strip() or None
        return [
            {
                "instance": instance,
                "work_date": work_day,
                "minutes": minutes_per_day,
                "start_at": start_at if work_day == start_at.date() else datetime.combine(work_day, datetime.min.time()),
                "end_at": end_at if work_day == end_at.date() else datetime.combine(work_day, datetime.max.time()),
                "has_start_time": _form_value_has_time(start_raw),
                "has_end_time": _form_value_has_time(end_raw),
                "reason": reason,
            }
            for work_day in visible_days
        ]

    items: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
    seen_business_ids: set[str] = set()
    seen_instance_ids: set[str] = set()
    seen_record_dates: set[date] = set()

    request_result = await db.execute(
        select(OvertimeRequest)
        .where(
            and_(
                OvertimeRequest.employee_id == employee_id,
                OvertimeRequest.overtime_date >= start_day,
                OvertimeRequest.overtime_date <= end_day,
                OvertimeRequest.status == "approved",
            )
        )
        .order_by(OvertimeRequest.overtime_date.desc(), OvertimeRequest.id.desc())
    )
    for request in request_result.scalars().all():
        work_day = request.overtime_date
        raw_hours = Decimal(str(request.hours or 0))
        comp_hours = await _comp_hours_for_overtime(work_day, raw_hours)
        day_type = await _day_type_for(work_day)
        seen_business_ids.add(str(request.id))
        _append_detail(
            items,
            seen_item_ids,
            item_id=f"overtime:{request.id}",
            work_day=work_day,
            day_type=day_type,
            comp_hours=comp_hours,
            source="overtime_request",
            status_text=str(request.status or "approved"),
            start_time=request.start_time,
            end_time=request.end_time,
            reason=request.reason,
        )
        if comp_hours > 0:
            seen_record_dates.add(work_day)

    if await _has_attendance_effects_table():
        effect_result = await db.execute(
            select(AttendanceEffect)
            .where(
                and_(
                    AttendanceEffect.employee_id == employee_id,
                    AttendanceEffect.effect_status == "active",
                    AttendanceEffect.effect_type == "overtime",
                    AttendanceEffect.work_date >= start_day,
                    AttendanceEffect.work_date <= end_day,
                )
            )
            .order_by(AttendanceEffect.work_date.desc(), AttendanceEffect.id.desc())
        )
        for effect in effect_result.scalars().all():
            if effect.work_date is None:
                continue
            payload = effect.payload_json if isinstance(effect.payload_json, dict) else {}
            business_id = payload.get("overtime_request_id") or effect.source_business_id or effect.source_instance_id or effect.id
            if str(business_id) in seen_business_ids:
                continue
            if effect.source_instance_id:
                seen_instance_ids.add(str(effect.source_instance_id))
            raw_hours = Decimal(str(effect.minutes or 0)) / Decimal("60")
            comp_hours = await _comp_hours_for_overtime(effect.work_date, raw_hours, effect.pay_policy)
            day_type = await _day_type_for(effect.work_date)
            _append_detail(
                items,
                seen_item_ids,
                item_id=f"overtime-effect:{effect.id}",
                work_day=effect.work_date,
                day_type=day_type,
                comp_hours=comp_hours,
                source="attendance_effect",
                status_text=str(effect.effect_status or "active"),
                start_time=_mobile_hm(effect.start_at),
                end_time=_mobile_hm(effect.end_at),
                reason=str(payload.get("reason") or "") or None,
            )
            if comp_hours > 0:
                seen_record_dates.add(effect.work_date)

    overtime_approval_codes = ("overtime", "overtime_request", "legal_overtime", "holiday_overtime", "overtime_holiday")
    instance_result = await db.execute(
        select(ApprovalInstance)
        .where(
            and_(
                ApprovalInstance.applicant_id == employee_id,
                ApprovalInstance.status == "approved",
                or_(
                    ApprovalInstance.module.in_(overtime_approval_codes),
                    ApprovalInstance.business_type.in_(overtime_approval_codes),
                ),
            )
        )
        .order_by(ApprovalInstance.created_at.desc(), ApprovalInstance.id.desc())
    )
    for detail in [item for instance in instance_result.scalars().all() for item in _approved_overtime_instance_items(instance)]:
        instance = detail["instance"]
        instance_id = str(instance.id)
        business_id = str(getattr(instance, "business_id", "") or "")
        business_code = str(getattr(instance, "business_type", None) or getattr(instance, "module", None) or "")
        if instance_id in seen_instance_ids:
            continue
        if business_code in {"overtime", "overtime_request"} and business_id and business_id in seen_business_ids:
            continue
        work_day = detail["work_date"]
        raw_hours = Decimal(int(detail["minutes"] or 0)) / Decimal("60")
        comp_hours = await _comp_hours_for_overtime(work_day, raw_hours)
        day_type = await _day_type_for(work_day)
        _append_detail(
            items,
            seen_item_ids,
            item_id=f"overtime-approval:{instance.id}:{work_day.isoformat()}",
            work_day=work_day,
            day_type=day_type,
            comp_hours=comp_hours,
            source="approval_instance",
            status_text=str(instance.status or "approved"),
            start_time=detail["start_at"].strftime("%H:%M") if detail.get("has_start_time") else None,
            end_time=detail["end_at"].strftime("%H:%M") if detail.get("has_end_time") else None,
            reason=detail.get("reason"),
        )
        if comp_hours > 0:
            seen_record_dates.add(work_day)

    record_result = await db.execute(
        select(AttendanceRecord)
        .where(
            and_(
                AttendanceRecord.employee_id == employee_id,
                AttendanceRecord.date >= start_day,
                AttendanceRecord.date <= end_day,
                AttendanceRecord.overtime_hours > 0,
            )
        )
        .order_by(AttendanceRecord.date.desc(), AttendanceRecord.id.desc())
    )
    for record in record_result.scalars().all():
        if record.date in seen_record_dates:
            continue
        raw_hours = Decimal(str(record.overtime_hours or 0))
        comp_hours = await _comp_hours_for_overtime(record.date, raw_hours)
        day_type = await _day_type_for(record.date)
        end_at = AttendanceRecordService._to_cst_datetime(record.clock_out_time)
        start_at = end_at - timedelta(hours=float(raw_hours)) if end_at and raw_hours > 0 else None
        _append_detail(
            items,
            seen_item_ids,
            item_id=f"attendance-record:{record.id}",
            work_day=record.date,
            day_type=day_type,
            comp_hours=comp_hours,
            source="attendance_record",
            status_text="recorded",
            start_time=start_at.strftime("%H:%M") if start_at else None,
            end_time=end_at.strftime("%H:%M") if end_at else None,
            reason=record.anomaly_type,
        )

    balance_hours = Decimal("0.0")
    balance_updated_date: Optional[date] = None
    for condition in (
        LeaveType.code == "comp_time",
        LeaveType.code == "comp",
        LeaveType.name == "调休",
    ):
        balance_result = await db.execute(
            select(LeaveType, LeaveBalance)
            .outerjoin(
                LeaveBalance,
                and_(
                    LeaveBalance.leave_type_id == LeaveType.id,
                    LeaveBalance.employee_id == employee_id,
                    LeaveBalance.year == year,
                ),
            )
            .where(and_(condition, LeaveType.is_active.is_(True)))
            .order_by(LeaveType.id.asc())
            .limit(1)
        )
        row = balance_result.first()
        if row is None:
            continue
        leave_type, balance = row
        balance_hours = _comp_time_balance_hours_from_balance(leave_type, balance, "remaining_days")
        if balance is not None and getattr(balance, "updated_at", None):
            local_updated = AttendanceRecordService._to_cst_datetime(balance.updated_at)
            balance_updated_date = local_updated.date() if local_updated else None
        break

    detail_hours = sum(
        (Decimal(str(item.get("minutes") or 0)) / Decimal("60"))
        for item in items
    )
    detail_hours = Decimal(str(detail_hours)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    balance_delta = (balance_hours - detail_hours).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if balance_delta >= Decimal("0.1"):
        delta_minutes = int((balance_delta * Decimal("60")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        items.append(
            {
                "id": f"comp-time-balance-delta:{year}",
                "date": (balance_updated_date or end_day).isoformat(),
                "display_date_label": "未关联日期",
                "date_type": "balance_delta",
                "date_type_label": "余额差额",
                "minutes": delta_minutes,
                "hours": float(balance_delta),
                "settlement": "comp_time",
                "settlement_label": "调休",
                "start_time": None,
                "end_time": None,
                "status": "balance_delta",
                "reason": "余额账本差额",
                "source": "leave_balance_adjustment",
                "source_note": "这部分余额没有匹配到具体加班来源，可能来自历史同步或人工调整",
            }
        )

    return sorted(items, key=lambda item: (str(item["date"]), str(item.get("end_time") or item.get("start_time") or ""), str(item["id"])), reverse=True)


# ===================================================================
# 考勤规则
# ===================================================================

@router.get("/maps/tencent/place-suggestions")
async def get_tencent_place_suggestions(
    keyword: str = Query(..., min_length=1, max_length=100, description="地点关键词"),
    region: str = Query("全国", max_length=50, description="搜索区域"),
    limit: int = Query(10, ge=1, le=20, description="返回条数"),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/maps/tencent/place-suggestions — 腾讯地图地点联想（后端代理）

    说明：
    - 前端不直接携带腾讯地图 Key，统一由后端代理转发。
    - 该接口仅返回前端定位选择所需的最小字段。
    """
    key = (settings.TENCENT_MAP_WEB_SERVICE_KEY or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="腾讯地图服务未配置")

    url = f"{settings.TENCENT_MAP_BASE_URL.rstrip('/')}/ws/place/v1/suggestion"
    params = {
        "key": key,
        "keyword": keyword.strip(),
        "region": region.strip() or "全国",
    }
    try:
        async with httpx.AsyncClient(timeout=settings.TENCENT_MAP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Tencent map suggestion request failed: %s", exc)
        raise HTTPException(status_code=502, detail="腾讯地图服务暂不可用") from exc

    if payload.get("status") != 0:
        message = str(payload.get("message") or "腾讯地图返回异常")
        logger.warning(
            "Tencent map suggestion error: status=%s message=%s",
            payload.get("status"),
            message,
        )
        if _is_tencent_webservice_disabled(message):
            return {
                "enabled": False,
                "items": [],
                "unavailable_reason": message,
            }
        raise HTTPException(status_code=502, detail=message)

    items = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        location = item.get("location")
        lat = location.get("lat") if isinstance(location, dict) else None
        lng = location.get("lng") if isinstance(location, dict) else None
        try:
            lat_num = float(lat)
            lng_num = float(lng)
        except (TypeError, ValueError):
            continue
        items.append(
            {
                "value": item.get("title") or item.get("address") or keyword,
                "title": item.get("title") or "",
                "address": item.get("address") or "",
                "latitude": lat_num,
                "longitude": lng_num,
            }
        )
        if len(items) >= limit:
            break

    return {"items": items}


@router.get("/maps/tencent/reverse-geocode")
async def get_tencent_reverse_geocode(
    latitude: float = Query(..., ge=-90, le=90, description="纬度"),
    longitude: float = Query(..., ge=-180, le=180, description="经度"),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/maps/tencent/reverse-geocode — 腾讯地图逆地址解析（后端代理）
    """
    key = (settings.TENCENT_MAP_WEB_SERVICE_KEY or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="腾讯地图服务未配置")

    url = f"{settings.TENCENT_MAP_BASE_URL.rstrip('/')}/ws/geocoder/v1"
    params = {
        "key": key,
        "location": f"{latitude},{longitude}",
        "get_poi": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.TENCENT_MAP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Tencent map reverse-geocode request failed: %s", exc)
        raise HTTPException(status_code=502, detail="腾讯地图服务暂不可用") from exc

    if payload.get("status") != 0:
        message = str(payload.get("message") or "腾讯地图返回异常")
        if _is_tencent_webservice_disabled(message):
            return {
                "enabled": False,
                "address": "",
                "place_name": "",
                "formatted_addresses": {},
                "poi": None,
                "adcode": None,
                "city": "",
                "district": "",
                "province": "",
                "unavailable_reason": message,
            }
        raise HTTPException(status_code=502, detail=message)

    result = payload.get("result") or {}
    ad_info = result.get("ad_info") or {}
    address_component = result.get("address_component") or {}
    pois = result.get("pois") if isinstance(result.get("pois"), list) else []
    first_poi = pois[0] if pois and isinstance(pois[0], dict) else {}
    formatted_addresses = result.get("formatted_addresses") or {}
    place_name = (
        first_poi.get("title")
        or formatted_addresses.get("recommend")
        or formatted_addresses.get("rough")
        or result.get("address")
        or ""
    )
    return {
        "address": result.get("address") or "",
        "place_name": place_name,
        "formatted_addresses": formatted_addresses,
        "poi": {
            "title": first_poi.get("title") or "",
            "address": first_poi.get("address") or "",
            "category": first_poi.get("category") or "",
            "distance": first_poi.get("_distance"),
        } if first_poi else None,
        "adcode": ad_info.get("adcode"),
        "city": ad_info.get("city") or address_component.get("city") or "",
        "district": ad_info.get("district") or address_component.get("district") or "",
        "province": ad_info.get("province") or address_component.get("province") or "",
    }


@router.get("/maps/tencent/coord-translate")
async def get_tencent_coord_translate(
    latitude: float = Query(..., ge=-90, le=90, description="待转换纬度"),
    longitude: float = Query(..., ge=-180, le=180, description="待转换经度"),
    coord_type: int = Query(1, ge=1, le=6, description="输入坐标类型，1=GPS"),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/maps/tencent/coord-translate — 腾讯地图坐标转换代理

    手机 GPS 通常是原始 GPS 坐标；腾讯地图地点、逆地址和静态图使用腾讯坐标系。
    移动端打卡前先统一转换，避免坐标系偏移造成 300-600 米级误判。
    """
    key = (settings.TENCENT_MAP_WEB_SERVICE_KEY or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="腾讯地图服务未配置")

    url = f"{settings.TENCENT_MAP_BASE_URL.rstrip('/')}/ws/coord/v1/translate"
    params = {
        "key": key,
        "locations": f"{latitude},{longitude}",
        "type": coord_type,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.TENCENT_MAP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Tencent map coord-translate request failed: %s", exc)
        raise HTTPException(status_code=502, detail="腾讯地图服务暂不可用") from exc

    if payload.get("status") != 0:
        message = str(payload.get("message") or "腾讯地图返回异常")
        if _is_tencent_webservice_disabled(message):
            return {
                "enabled": False,
                "latitude": None,
                "longitude": None,
                "coord_type": coord_type,
                "source": "tencent_coord_translate",
                "unavailable_reason": message,
            }
        raise HTTPException(status_code=502, detail=message)

    locations = payload.get("locations") or []
    first = locations[0] if locations and isinstance(locations[0], dict) else {}
    try:
        converted_lat = float(first.get("lat"))
        converted_lng = float(first.get("lng"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="腾讯地图坐标转换结果异常") from exc

    return {
        "latitude": converted_lat,
        "longitude": converted_lng,
        "coord_type": coord_type,
        "source": "tencent_coord_translate",
    }


@router.get("/maps/tencent/gps-location")
async def get_tencent_gps_location(
    latitude: float = Query(..., ge=-90, le=90, description="GPS 纬度"),
    longitude: float = Query(..., ge=-180, le=180, description="GPS 经度"),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/maps/tencent/gps-location — 腾讯 GPS 定位解析

    移动端传入设备 GPS 坐标，后端统一调用腾讯坐标转换和逆地址解析，
    返回可直接用于腾讯地图展示、打卡范围校验和地点名展示的坐标与地址。
    """
    key = (settings.TENCENT_MAP_WEB_SERVICE_KEY or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="腾讯地图服务未配置")

    base_url = settings.TENCENT_MAP_BASE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=settings.TENCENT_MAP_TIMEOUT_SECONDS) as client:
            translate_response = await client.get(
                f"{base_url}/ws/coord/v1/translate",
                params={
                    "key": key,
                    "locations": f"{latitude},{longitude}",
                    "type": 1,
                },
            )
            translate_response.raise_for_status()
            translate_payload = translate_response.json()
            if translate_payload.get("status") != 0:
                message = str(translate_payload.get("message") or "腾讯地图返回异常")
                if _is_tencent_webservice_disabled(message):
                    return {
                        "enabled": False,
                        "latitude": None,
                        "longitude": None,
                        "raw_latitude": latitude,
                        "raw_longitude": longitude,
                        "coordinate_system": "wgs84",
                        "place_name": "",
                        "address": "",
                        "unavailable_reason": message,
                        "source": "tencent_gps",
                    }
                raise HTTPException(status_code=502, detail=message)

            locations = translate_payload.get("locations") or []
            first = locations[0] if locations and isinstance(locations[0], dict) else {}
            converted_lat = float(first.get("lat"))
            converted_lng = float(first.get("lng"))

            geocode_response = await client.get(
                f"{base_url}/ws/geocoder/v1",
                params={
                    "key": key,
                    "location": f"{converted_lat},{converted_lng}",
                    "get_poi": 1,
                },
            )
            geocode_response.raise_for_status()
            geocode_payload = geocode_response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.warning("Tencent map gps-location request failed: %s", exc)
        raise HTTPException(status_code=502, detail="腾讯地图服务暂不可用") from exc

    if geocode_payload.get("status") != 0:
        message = str(geocode_payload.get("message") or "腾讯地图返回异常")
        if _is_tencent_webservice_disabled(message):
            return {
                "enabled": False,
                "latitude": converted_lat,
                "longitude": converted_lng,
                "raw_latitude": latitude,
                "raw_longitude": longitude,
                "coordinate_system": "tencent",
                "place_name": "",
                "address": "",
                "unavailable_reason": message,
                "source": "tencent_gps",
            }
        raise HTTPException(status_code=502, detail=message)

    result = geocode_payload.get("result") or {}
    ad_info = result.get("ad_info") or {}
    address_component = result.get("address_component") or {}
    pois = result.get("pois") if isinstance(result.get("pois"), list) else []
    first_poi = pois[0] if pois and isinstance(pois[0], dict) else {}
    formatted_addresses = result.get("formatted_addresses") or {}
    place_name = (
        first_poi.get("title")
        or formatted_addresses.get("recommend")
        or formatted_addresses.get("rough")
        or result.get("address")
        or ""
    )

    return {
        "latitude": converted_lat,
        "longitude": converted_lng,
        "raw_latitude": latitude,
        "raw_longitude": longitude,
        "coordinate_system": "tencent",
        "place_name": place_name,
        "address": result.get("address") or "",
        "formatted_addresses": formatted_addresses,
        "poi": {
            "title": first_poi.get("title") or "",
            "address": first_poi.get("address") or "",
            "category": first_poi.get("category") or "",
            "distance": first_poi.get("_distance"),
        } if first_poi else None,
        "adcode": ad_info.get("adcode"),
        "city": ad_info.get("city") or address_component.get("city") or "",
        "district": ad_info.get("district") or address_component.get("district") or "",
        "province": ad_info.get("province") or address_component.get("province") or "",
        "source": "tencent_gps",
    }


@router.get("/maps/tencent/ip-location")
async def get_tencent_ip_location(
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/maps/tencent/ip-location — 腾讯地图 IP 定位兜底

    浏览器在非 HTTPS 内网地址下可能禁止高精度 GPS。该接口只作为地图展示兜底，
    真正打卡仍会把前端拿到的经纬度提交给后端校验。
    """
    key = (settings.TENCENT_MAP_WEB_SERVICE_KEY or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="腾讯地图服务未配置")

    url = f"{settings.TENCENT_MAP_BASE_URL.rstrip('/')}/ws/location/v1/ip"
    try:
        async with httpx.AsyncClient(timeout=settings.TENCENT_MAP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params={"key": key})
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Tencent map IP location request failed: %s", exc)
        raise HTTPException(status_code=502, detail="腾讯地图服务暂不可用") from exc

    if payload.get("status") != 0:
        message = str(payload.get("message") or "腾讯地图返回异常")
        if _is_tencent_webservice_disabled(message):
            return {
                "enabled": False,
                "latitude": None,
                "longitude": None,
                "address": "",
                "source": "tencent_ip",
                "unavailable_reason": message,
            }
        raise HTTPException(status_code=502, detail=message)

    result = payload.get("result") or {}
    location = result.get("location") or {}
    return {
        "latitude": location.get("lat"),
        "longitude": location.get("lng"),
        "address": result.get("ad_info", {}).get("city") or "",
        "source": "tencent_ip",
    }


@router.get("/maps/tencent/static-map-preview")
async def get_tencent_static_map_preview(
    latitude: float = Query(..., ge=-90, le=90, description="纬度"),
    longitude: float = Query(..., ge=-180, le=180, description="经度"),
    zoom: int = Query(16, ge=3, le=18, description="缩放等级"),
    width: int = Query(640, ge=240, le=1280, description="宽度"),
    height: int = Query(200, ge=120, le=800, description="高度"),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/maps/tencent/static-map-preview — 腾讯静态地图（后端代理，返回 base64）
    """
    key = (settings.TENCENT_MAP_WEB_SERVICE_KEY or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="腾讯地图服务未配置")

    url = f"{settings.TENCENT_MAP_BASE_URL.rstrip('/')}/ws/staticmap/v2"
    params = {
        "key": key,
        "center": f"{latitude},{longitude}",
        "zoom": zoom,
        "size": f"{width}*{height}",
        "markers": f"size:large|color:0x2F7CF6|label:A|{latitude},{longitude}",
        "scale": 2,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.TENCENT_MAP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            if "image" not in content_type:
                # 某些异常情况下返回 json
                try:
                    payload = response.json()
                    message = str(payload.get("message") or "腾讯地图返回异常")
                except Exception:
                    message = "腾讯地图静态图获取失败"
                if _is_tencent_webservice_disabled(message):
                    return {
                        "enabled": False,
                        "image_data": "",
                        "center": {"latitude": latitude, "longitude": longitude},
                        "zoom": zoom,
                        "size": {"width": width, "height": height},
                        "unavailable_reason": message,
                    }
                raise HTTPException(status_code=502, detail=message)
            image_bytes = response.content
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.warning("Tencent map static-map request failed: %s", exc)
        raise HTTPException(status_code=502, detail="腾讯地图服务暂不可用") from exc

    image_base64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "image_data": f"data:image/png;base64,{image_base64}",
        "center": {"latitude": latitude, "longitude": longitude},
        "zoom": zoom,
        "size": {"width": width, "height": height},
    }


@router.get("/maps/tencent/web-sdk-config")
async def get_tencent_web_sdk_config(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/maps/tencent/web-sdk-config — 腾讯地图 JS SDK 配置

    前端实时地图使用独立的浏览器 Key。平台配置优先，环境变量兜底。
    正式域名应在腾讯控制台加入该 Key 的 Web 端域名限制。
    """
    key, _source = await browser_map_key(db)
    if not key:
        return {
            "enabled": False,
            "key": "",
            "script_url": "https://map.qq.com/api/gljs",
            "version": "1.exp",
            "unavailable_reason": "腾讯地图服务未配置",
        }
    return {
        "enabled": True,
        "key": key,
        "script_url": "https://map.qq.com/api/gljs",
        "version": "1.exp",
    }


@router.get("/my/runtime")
async def get_my_attendance_runtime(
    target_date: Optional[str] = Query(None, description="目标日期 YYYY-MM-DD，默认今天"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/my/runtime — 查询当前员工当日考勤规则运行态

    用途：
    - 给移动端打卡页提供“应上班/应下班/GPS范围/规则类型”等运行参数。
    """
    if target_date:
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD") from exc
    else:
        from app.services.field_service import CST
        parsed_date, _ = await AttendanceRecordService._resolve_punch_target_date(
            db, current_user.id, datetime.now(CST))

    result = await AttendanceRecordService.get_mobile_runtime(db, current_user.id, parsed_date, inspect_date=bool(target_date))
    from app.models.field_service import FieldProject, FieldSite, FieldAssignment, FieldAssignmentDay
    project = await db.scalar(select(FieldProject)
        .join(FieldSite, FieldSite.project_id == FieldProject.id)
        .join(FieldAssignment, FieldAssignment.site_id == FieldSite.id)
        .join(FieldAssignmentDay, FieldAssignmentDay.assignment_id == FieldAssignment.id)
        .where(FieldAssignmentDay.employee_id == current_user.id, FieldAssignmentDay.work_date == parsed_date))
    if not result.get("field_managed"):
        result["project"] = {"id": project.id, "code": project.code, "name": project.name} if project else None
    result["map_service_enabled"] = bool(settings.TENCENT_MAP_WEB_SERVICE_KEY)
    browser_key, _source = await browser_map_key(db)
    result["map_browser_enabled"] = bool(browser_key)
    return result


@router.get("/mobile/dashboard", response_model=MobileDashboardOut)
async def get_mobile_attendance_dashboard(
    range_type: str = Query("day", pattern="^(day|week|month)$", description="统计周期"),
    target_date: Optional[str] = Query(None, description="目标日期 YYYY-MM-DD，默认今天"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/mobile/dashboard — 移动端考勤页聚合数据

    这个接口给移动端“申请/统计”页供数，确保页面上的文案和数字来自真实业务数据：
    - 申请入口：请假、外出、加班等入口及待处理数量
    - 统计页：日/周/月上下班打卡、异常、请假、外出、出差、加班分钟
    """
    if target_date:
        try:
            base_day = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD") from exc
    else:
        base_day = date.today()

    if range_type == "day":
        start_day = end_day = base_day
    elif range_type == "week":
        start_day = base_day - timedelta(days=base_day.weekday())
        end_day = start_day + timedelta(days=6)
    else:
        start_day = base_day.replace(day=1)
        if base_day.month == 12:
            next_month = date(base_day.year + 1, 1, 1)
        else:
            next_month = date(base_day.year, base_day.month + 1, 1)
        end_day = next_month - timedelta(days=1)

    records_result = await db.execute(
        select(AttendanceRecord)
        .where(
            and_(
                AttendanceRecord.employee_id == current_user.id,
                AttendanceRecord.date >= start_day,
                AttendanceRecord.date <= end_day,
            )
        )
        .order_by(AttendanceRecord.date.asc())
    )
    records = list(records_result.scalars().all())

    month_start = base_day.replace(day=1)
    if base_day.month == 12:
        month_end = date(base_day.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(base_day.year, base_day.month + 1, 1) - timedelta(days=1)
    if start_day == month_start and end_day == month_end:
        month_records = records
    else:
        month_records_result = await db.execute(
            select(AttendanceRecord)
            .where(
                and_(
                    AttendanceRecord.employee_id == current_user.id,
                    AttendanceRecord.date >= month_start,
                    AttendanceRecord.date <= month_end,
                )
            )
            .order_by(AttendanceRecord.date.asc())
        )
        month_records = list(month_records_result.scalars().all())

    runtime_cache: dict[date, dict] = {}
    for record in {record.id: record for record in [*records, *month_records] if record.id is not None}.values():
        runtime = runtime_cache.get(record.date)
        if runtime is None:
            runtime = await AttendanceRecordService._get_rule_runtime(db, record.employee_id, record.date)
            runtime_cache[record.date] = runtime
        AttendanceRecordService._attach_display_status(record, runtime)

    corrections_count = await db.scalar(
        select(func.count(PunchCorrectionRequest.id)).where(
            and_(
                PunchCorrectionRequest.employee_id == current_user.id,
                PunchCorrectionRequest.correction_date >= start_day,
                PunchCorrectionRequest.correction_date <= end_day,
            )
        )
    )
    pending_corrections = await db.scalar(
        select(func.count(PunchCorrectionRequest.id)).where(
            and_(
                PunchCorrectionRequest.employee_id == current_user.id,
                PunchCorrectionRequest.status == "pending",
            )
        )
    )
    punch_correction_approval_codes = ("punch_correction", "attendance_punch_correction")
    pending_correction_approvals = await db.scalar(
        select(func.count(ApprovalInstance.id)).where(
            and_(
                ApprovalInstance.applicant_id == current_user.id,
                ApprovalInstance.status == "pending",
                or_(
                    ApprovalInstance.module.in_(punch_correction_approval_codes),
                    ApprovalInstance.business_type.in_(punch_correction_approval_codes),
                ),
            )
        )
    )
    pending_corrections_total = max(int(pending_corrections or 0), int(pending_correction_approvals or 0))
    pending_leave = await db.scalar(
        select(func.count(LeaveRequest.id)).where(
            and_(
                LeaveRequest.employee_id == current_user.id,
                LeaveRequest.approval_status == "pending",
            )
        )
    )
    approved_overtime_hours = await db.scalar(
        select(func.coalesce(func.sum(OvertimeRequest.hours), 0)).where(
            and_(
                OvertimeRequest.employee_id == current_user.id,
                OvertimeRequest.overtime_date >= start_day,
                OvertimeRequest.overtime_date <= end_day,
                OvertimeRequest.status == "approved",
            )
        )
    )
    overtime_approval_codes = ("overtime", "overtime_request", "legal_overtime", "holiday_overtime", "overtime_holiday")
    pending_overtime = await db.scalar(
        select(func.count(OvertimeRequest.id)).where(
            and_(
                OvertimeRequest.employee_id == current_user.id,
                OvertimeRequest.status == "pending",
            )
        )
    )
    pending_overtime_approvals = await db.scalar(
        select(func.count(ApprovalInstance.id)).where(
            and_(
                ApprovalInstance.applicant_id == current_user.id,
                ApprovalInstance.status == "pending",
                or_(
                    ApprovalInstance.module.in_(overtime_approval_codes),
                    ApprovalInstance.business_type.in_(overtime_approval_codes),
                ),
            )
        )
    )
    pending_overtime_total = max(int(pending_overtime or 0), int(pending_overtime_approvals or 0))
    pending_business_trip = await db.scalar(
        select(func.count(ApprovalInstance.id)).where(
            and_(
                ApprovalInstance.applicant_id == current_user.id,
                ApprovalInstance.status == "pending",
                or_(
                    ApprovalInstance.module.in_(["business_trip", "travel"]),
                    ApprovalInstance.business_type.in_(["business_trip", "travel"]),
                ),
            )
        )
    )
    pending_outside = await db.scalar(
        select(func.count(ApprovalInstance.id)).where(
            and_(
                ApprovalInstance.applicant_id == current_user.id,
                ApprovalInstance.status == "pending",
                or_(
                    ApprovalInstance.module.in_(["attendance_outside", "outside"]),
                    ApprovalInstance.business_type.in_(["attendance_outside", "outside"]),
                ),
            )
        )
    )

    async def _active_effect_day_count(effect_types: list[str]) -> int:
        if not attendance_effects_available:
            return 0
        result = await db.scalar(
            select(func.count(func.distinct(AttendanceEffect.work_date))).where(
                and_(
                    AttendanceEffect.employee_id == current_user.id,
                    AttendanceEffect.effect_status == "active",
                    AttendanceEffect.effect_type.in_(effect_types),
                    AttendanceEffect.work_date >= start_day,
                    AttendanceEffect.work_date <= end_day,
                )
            )
        )
        return int(result or 0)

    async def _active_effect_minutes(effect_type: str) -> int:
        if not attendance_effects_available:
            return 0
        result = await db.scalar(
            select(func.coalesce(func.sum(AttendanceEffect.minutes), 0)).where(
                and_(
                    AttendanceEffect.employee_id == current_user.id,
                    AttendanceEffect.effect_status == "active",
                    AttendanceEffect.effect_type == effect_type,
                    AttendanceEffect.work_date >= start_day,
                    AttendanceEffect.work_date <= end_day,
                )
            )
        )
        return int(result or 0)

    async def _has_attendance_effects_table() -> bool:
        def _has_table(sync_session) -> bool:
            connection = sync_session.connection()
            return inspect(connection).has_table("attendance_effects")

        try:
            return bool(await db.run_sync(_has_table))
        except Exception:
            return False

    attendance_effects_available = await _has_attendance_effects_table()

    async def _comp_time_balance_hours() -> Decimal:
        await AttendanceRecordService.sync_comp_time_balances_from_attendance(
            db,
            year=base_day.year,
            employee_ids=[current_user.id],
            allow_decrease=True,
            recalculate_records=True,
        )
        for condition in (
            LeaveType.code == "comp_time",
            LeaveType.code == "comp",
            LeaveType.name == "调休",
        ):
            result = await db.execute(
                select(LeaveType, LeaveBalance)
                .outerjoin(
                    LeaveBalance,
                    and_(
                        LeaveBalance.leave_type_id == LeaveType.id,
                        LeaveBalance.employee_id == current_user.id,
                        LeaveBalance.year == base_day.year,
                    ),
                )
                .where(and_(condition, LeaveType.is_active.is_(True)))
                .order_by(LeaveType.id.asc())
                .limit(1)
            )
            row = result.first()
            if row is None:
                continue
            leave_type, balance = row
            return _comp_time_balance_hours_from_balance(leave_type, balance, "total_days")
        return Decimal("0.0")

    def _status_text(record: AttendanceRecord) -> str:
        display_status = getattr(record, "display_status", None)
        if display_status:
            return str(display_status)
        value = getattr(record.status, "value", record.status)
        status_map = {
            "normal": "正常",
            "late": "迟到",
            "early_leave": "早退",
            "missed_clock": "缺卡",
            "absent": "旷工",
            "leave": "请假",
            "on_leave": "请假",
            "business_trip": "出差",
        }
        return status_map.get(str(value), str(value or ""))

    def _is_abnormal_record(record: Optional[AttendanceRecord]) -> bool:
        if record is None:
            return False
        status_text = _status_text(record)
        return any(item in status_text for item in ("迟到", "早退", "缺卡", "旷工")) or bool(record.anomaly_type)

    def _enum_value(value) -> str:
        return str(getattr(value, "value", value) or "")

    day_type_cache: dict[date, DayType] = {}

    async def _day_type_for(work_day: date) -> DayType:
        cached = day_type_cache.get(work_day)
        if cached is not None:
            return cached
        resolved = await WorkCalendarService.get_day_type(
            db,
            work_day,
            getattr(current_user, "location_id", None),
        )
        day_type_cache[work_day] = resolved
        return resolved

    def _day_type_key(day_type: DayType | str | None) -> str:
        value = _enum_value(day_type)
        if value == DayType.holiday.value:
            return "holiday"
        if value == DayType.weekend.value:
            return "restday"
        return "workday"

    def _day_type_label(day_type: DayType | str | None) -> str:
        key = _day_type_key(day_type)
        return {
            "workday": "工作日",
            "restday": "休息日",
            "holiday": "节假日",
        }.get(key, "工作日")

    def _status_key(record: AttendanceRecord) -> str:
        text = f"{_status_text(record)} {record.anomaly_type or ''}"
        if "迟到" in text:
            return "late"
        if "早退" in text:
            return "early_leave"
        if "缺卡" in text:
            return "missed_clock"
        if "旷工" in text:
            return "absent"
        if _is_abnormal_record(record):
            return "abnormal"
        return "normal"

    def _iter_days(left: date, right: date):
        current = max(left, start_day)
        final = min(right, end_day)
        while current <= final:
            yield current
            current += timedelta(days=1)

    def _leave_label(effect_type: str, payload: Optional[dict] = None) -> str:
        payload = payload or {}
        detail = payload.get("effect_detail") if isinstance(payload.get("effect_detail"), dict) else {}
        form_data = (
            payload.get("approval_event", {}).get("form_data")
            if isinstance(payload.get("approval_event"), dict)
            else {}
        )
        form_data = form_data if isinstance(form_data, dict) else {}
        if effect_type == "leave":
            code = str(detail.get("leave_type_code") or form_data.get("leave_type_code") or "")
            if code == "comp_time":
                return "调休"
            return str(
                detail.get("leave_type_name")
                or form_data.get("leave_type_name")
                or form_data.get("leave_type")
                or "请假"
            )
        return {
            "punch_correction": "补卡",
            "outside": "外出",
            "business_trip": "出差",
            "field_work": "外勤",
        }.get(effect_type, effect_type)

    def _dashboard_punch_type(value: Any) -> str:
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

    def _dashboard_punch_type_label(value: Any) -> str:
        punch_type = _dashboard_punch_type(value)
        if punch_type == "check_in":
            return "上班卡"
        if punch_type == "check_out":
            return "下班卡"
        return str(value or "").strip() or "补卡"

    async def _settlement_for_overtime(
        work_day: date,
        hours: Decimal | float | int,
        pay_policy: Optional[str] = None,
    ) -> tuple[str, str]:
        policy = str(pay_policy or "").strip()
        if policy in {"comp_time", "rest", "rest_time"}:
            return "comp_time", "调休(1:1)"
        if policy in {"overtime_pay", "payroll", "paid_overtime"}:
            return "overtime_pay", "加班费"
        runtime = runtime_cache.get(work_day)
        if runtime is None:
            runtime = await AttendanceRecordService._get_rule_runtime(db, current_user.id, work_day)
            runtime_cache[work_day] = runtime
        rule = runtime.get("rule") if isinstance(runtime, dict) else None
        extra_config = getattr(rule, "extra_config", None)
        if not isinstance(extra_config, dict):
            extra_config = {}
        day_type = await _day_type_for(work_day)
        comp_hours, pay_hours = AttendanceRecordService._split_overtime_settlement(
            extra_config,
            day_type,
            hours,
        )
        if comp_hours > 0 and pay_hours <= 0:
            return "comp_time", "调休(1:1)"
        if pay_hours > 0:
            return "overtime_pay", "加班费"
        return "none", "无核算方式"

    async def _comp_hours_for_overtime(
        work_day: date,
        hours: Decimal | float | int,
        pay_policy: Optional[str] = None,
    ) -> Decimal:
        policy = str(pay_policy or "").strip()
        if policy in {"comp_time", "rest", "rest_time", "time_off", "lieu", "adjust_rest"}:
            try:
                parsed = Decimal(str(hours or 0))
            except Exception:
                return Decimal("0.0")
            return max(parsed, Decimal("0.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if policy in {"overtime_pay", "payroll", "paid_overtime"}:
            return Decimal("0.0")

        runtime = runtime_cache.get(work_day)
        if runtime is None:
            runtime = await AttendanceRecordService._get_rule_runtime(db, current_user.id, work_day)
            runtime_cache[work_day] = runtime
        rule = runtime.get("rule") if isinstance(runtime, dict) else None
        extra_config = getattr(rule, "extra_config", None)
        if not isinstance(extra_config, dict):
            extra_config = {}
        day_type = await _day_type_for(work_day)
        comp_hours, _ = AttendanceRecordService._split_overtime_settlement(
            extra_config,
            day_type,
            hours,
        )
        return max(comp_hours, Decimal("0.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    active_effects = []
    if attendance_effects_available:
        active_effects_result = await db.execute(
            select(AttendanceEffect)
            .where(
                and_(
                    AttendanceEffect.employee_id == current_user.id,
                    AttendanceEffect.effect_status == "active",
                    AttendanceEffect.work_date >= start_day,
                    AttendanceEffect.work_date <= end_day,
                )
            )
            .order_by(AttendanceEffect.work_date.desc(), AttendanceEffect.id.desc())
        )
        active_effects = list(active_effects_result.scalars().all())

    leave_requests_result = await db.execute(
        select(LeaveRequest)
        .options(selectinload(LeaveRequest.leave_type))
        .where(
            and_(
                LeaveRequest.employee_id == current_user.id,
                LeaveRequest.approval_status == "approved",
                LeaveRequest.start_date <= end_day,
                LeaveRequest.end_date >= start_day,
            )
        )
    )
    approved_leave_requests = list(leave_requests_result.scalars().all())

    correction_requests_result = await db.execute(
        select(PunchCorrectionRequest)
        .where(
            and_(
                PunchCorrectionRequest.employee_id == current_user.id,
                PunchCorrectionRequest.correction_date >= start_day,
                PunchCorrectionRequest.correction_date <= end_day,
            )
        )
        .order_by(PunchCorrectionRequest.correction_date.desc(), PunchCorrectionRequest.id.desc())
    )
    correction_requests = list(correction_requests_result.scalars().all())

    overtime_requests_result = await db.execute(
        select(OvertimeRequest)
        .where(
            and_(
                OvertimeRequest.employee_id == current_user.id,
                OvertimeRequest.overtime_date >= start_day,
                OvertimeRequest.overtime_date <= end_day,
                OvertimeRequest.status == "approved",
            )
        )
        .order_by(OvertimeRequest.overtime_date.desc(), OvertimeRequest.id.desc())
    )
    approved_overtime_requests = list(overtime_requests_result.scalars().all())

    overtime_instances_result = await db.execute(
        select(ApprovalInstance)
        .where(
            and_(
                ApprovalInstance.applicant_id == current_user.id,
                ApprovalInstance.status == "approved",
                or_(
                    ApprovalInstance.module.in_(overtime_approval_codes),
                    ApprovalInstance.business_type.in_(overtime_approval_codes),
                ),
            )
        )
        .order_by(ApprovalInstance.created_at.desc(), ApprovalInstance.id.desc())
    )
    approved_overtime_instances = list(overtime_instances_result.scalars().all())

    month_summary_overtime_minutes = 0
    if range_type == "month":
        month_summary_result = await db.execute(
            select(AttendanceMonthSummary)
            .where(
                and_(
                    AttendanceMonthSummary.employee_id == current_user.id,
                    AttendanceMonthSummary.year == base_day.year,
                    AttendanceMonthSummary.month == base_day.month,
                )
            )
            .limit(1)
        )
        month_summary = month_summary_result.scalar_one_or_none()
        if month_summary is not None:
            month_summary_overtime_minutes = int(
                (
                    Decimal(str(month_summary.overtime_weekday_hours or 0))
                    + Decimal(str(month_summary.overtime_weekend_hours or 0))
                    + Decimal(str(month_summary.overtime_holiday_hours or 0))
                )
                * Decimal("60")
            )

    outside_records = [
        r for r in records
        if "outside:" in str(r.clock_in_photo_url or "") or "outside:" in str(r.clock_out_photo_url or "")
    ]
    leave_record_days = len({r.date for r in records if _status_text(r) == "请假"})
    business_trip_record_days = len({r.date for r in records if _status_text(r) == "出差"})
    outside_record_days = len({r.date for r in outside_records})
    approved_leave_days = {
        current_day
        for request in approved_leave_requests
        for current_day in _iter_days(request.start_date, request.end_date)
    }
    leave_count_value = max(
        leave_record_days,
        len(approved_leave_days),
        await _active_effect_day_count(["leave"]),
    )
    outside_count_value = max(outside_record_days, await _active_effect_day_count(["outside"]))
    business_trip_count_value = max(
        business_trip_record_days,
        await _active_effect_day_count(["business_trip"]),
    )
    field_work_count_value = await _active_effect_day_count(["field_work"])
    overtime_minutes_value = max(
        int(float(approved_overtime_hours or 0) * 60),
        await _active_effect_minutes("overtime"),
        int(sum(float(r.overtime_hours or 0) * 60 for r in records)),
        month_summary_overtime_minutes,
    )

    normal_days = sum(1 for r in records if _status_text(r) == "正常" and not r.anomaly_type)
    abnormal_days = sum(1 for r in records if _is_abnormal_record(r))
    late_count = sum(1 for r in records if "迟到" in _status_text(r) or "迟到" in str(r.anomaly_type or ""))
    early_count = sum(1 for r in records if "早退" in _status_text(r) or "早退" in str(r.anomaly_type or ""))
    missed_count = sum(1 for r in records if "缺卡" in _status_text(r) or "缺卡" in str(r.anomaly_type or ""))
    absent_count = sum(1 for r in records if "旷工" in _status_text(r) or "旷工" in str(r.anomaly_type or ""))

    day_record = next((r for r in records if r.date == base_day), None)
    clock_in = day_record.clock_in_time if day_record else None
    clock_out = day_record.clock_out_time if day_record else None
    day_status = _status_text(day_record) if day_record else "未打卡"
    day_is_abnormal = _is_abnormal_record(day_record)
    monthly_abnormal_records = [r for r in month_records if _is_abnormal_record(r)]
    latest_month_abnormal = max(monthly_abnormal_records, key=lambda r: r.date, default=None)
    day_runtime = await AttendanceRecordService._get_rule_runtime(
        db,
        current_user.id,
        base_day,
    )
    day_rule = day_runtime.get("rule") if isinstance(day_runtime, dict) else None
    expected_hours = AttendanceReportService._standard_work_hours_from_runtime(day_rule, day_runtime, base_day)
    expected_minutes = int(float(expected_hours or 0) * 60)

    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    def _first_form_value(form_data: dict[str, Any], keys: tuple[str, ...]) -> Any:
        for key in keys:
            value = form_data.get(key)
            if value not in (None, ""):
                return value
        return None

    def _parse_form_datetime(value: Any) -> Optional[datetime]:
        if value in (None, ""):
            return None
        if isinstance(value, dict):
            return _parse_form_datetime(
                value.get("start")
                or value.get("start_time")
                or value.get("start_date")
                or value.get("end")
                or value.get("end_time")
                or value.get("end_date")
                or value.get("date")
                or value.get("value")
            )
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        raw = str(value).strip().replace("Z", "+00:00")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=None)
        except ValueError:
            try:
                return datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time())
            except ValueError:
                return None

    def _form_value_has_time(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                _form_value_has_time(value.get(key))
                for key in ("start", "start_time", "end", "end_time", "value")
            )
        if isinstance(value, datetime):
            return True
        raw = str(value or "").strip()
        return bool(re.search(r"(T|\s)\d{1,2}:\d{2}", raw))

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

    def _duration_minutes_from_approval_form(
        form_data: dict[str, Any],
        start_at: Optional[datetime],
        end_at: Optional[datetime],
        start_raw: Any,
        end_raw: Any,
    ) -> int:
        for key in ("minutes", "overtime_minutes", "duration_minutes"):
            value = form_data.get(key)
            parsed = _decimal_from_form_value(value)
            if parsed is not None:
                return max(0, int(parsed))

        for key in ("hours", "overtime_hours", "duration_hours"):
            value = form_data.get(key)
            parsed = _decimal_from_form_value(value)
            if parsed is not None:
                return max(0, int(parsed * Decimal("60")))

        duration_key = ""
        duration_value = None
        for key in ("overtime_duration", "duration", "work_duration", "time_length"):
            value = form_data.get(key)
            if value not in (None, ""):
                duration_key = key
                duration_value = value
                break

        parsed_duration = _decimal_from_form_value(duration_value)
        raw_duration = str(duration_value or "")
        if parsed_duration is not None:
            if "分钟" in raw_duration or "minute" in raw_duration.lower():
                return max(0, int(parsed_duration))
            if "天" in raw_duration or "day" in raw_duration.lower():
                return max(0, int(parsed_duration * Decimal("480")))
            if "小时" in raw_duration or "hour" in raw_duration.lower():
                return max(0, int(parsed_duration * Decimal("60")))
            if duration_key == "overtime_duration":
                has_time = _form_value_has_time(start_raw) or _form_value_has_time(end_raw)
                return max(0, int(parsed_duration * (Decimal("60") if has_time else Decimal("480"))))
            return max(0, int(parsed_duration * Decimal("60")))

        if start_at and end_at and end_at > start_at:
            return max(0, int((end_at - start_at).total_seconds() // 60))
        return 0

    def _approved_overtime_instance_items(
        instance: ApprovalInstance,
        visible_start: Optional[date] = None,
        visible_end: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        form_data = instance.form_data if isinstance(instance.form_data, dict) else {}
        start_raw = _first_form_value(
            form_data,
            (
                "overtime_duration_start_time",
                "overtime_start_time",
                "duration_start_time",
                "start_at",
                "start_time",
                "start_date",
                "overtime_date",
                "date",
            ),
        )
        end_raw = _first_form_value(
            form_data,
            (
                "overtime_duration_end_time",
                "overtime_end_time",
                "duration_end_time",
                "end_at",
                "end_time",
                "end_date",
                "overtime_date",
                "date",
            ),
        )
        duration_value = form_data.get("overtime_duration")
        if isinstance(duration_value, dict):
            start_raw = start_raw or duration_value.get("start") or duration_value.get("start_time")
            end_raw = end_raw or duration_value.get("end") or duration_value.get("end_time")

        start_at = _parse_form_datetime(start_raw)
        end_at = _parse_form_datetime(end_raw) or start_at
        if start_at and end_at and end_at < start_at:
            end_at += timedelta(days=1)
        minutes = _duration_minutes_from_approval_form(form_data, start_at, end_at, start_raw, end_raw)
        if not (start_at and end_at and minutes > 0):
            return []

        period_days: list[date] = []
        current_day = start_at.date()
        final_day = end_at.date()
        if final_day < current_day:
            current_day, final_day = final_day, current_day
        while current_day <= final_day:
            period_days.append(current_day)
            current_day += timedelta(days=1)
        range_start = visible_start or start_day
        range_end = visible_end or end_day
        visible_days = [work_day for work_day in period_days if range_start <= work_day <= range_end]
        if not visible_days:
            return []
        minutes_per_day = max(1, int(round(minutes / len(period_days))))
        reason = str(
            form_data.get("overtime_reason")
            or form_data.get("reason")
            or instance.summary
            or ""
        ).strip() or None

        return [
            {
                "instance": instance,
                "work_date": work_day,
                "minutes": minutes_per_day,
                "start_at": start_at if work_day == start_at.date() else datetime.combine(work_day, datetime.min.time()),
                "end_at": end_at if work_day == end_at.date() else datetime.combine(work_day, datetime.max.time()),
                "has_start_time": _form_value_has_time(start_raw),
                "has_end_time": _form_value_has_time(end_raw),
                "reason": reason,
            }
            for work_day in visible_days
        ]

    approved_overtime_instance_items = [
        item
        for instance in approved_overtime_instances
        for item in _approved_overtime_instance_items(instance)
    ]

    attendance_details = []
    for record in sorted(records, key=lambda item: item.date, reverse=True):
        day_type = await _day_type_for(record.date)
        status_text = _status_text(record)
        status_key = _status_key(record)
        source = _enum_value(record.source)
        attendance_details.append(
            {
                "id": record.id,
                "date": record.date.isoformat(),
                "status": status_key,
                "display_status": status_text,
                "anomaly_type": record.anomaly_type,
                "is_abnormal": _is_abnormal_record(record),
                "is_rest_day": _day_type_key(day_type) == "restday",
                "day_type": _day_type_key(day_type),
                "day_type_label": _day_type_label(day_type),
                "clock_in_time": _iso(record.clock_in_time),
                "clock_out_time": _iso(record.clock_out_time),
                "clock_in_status": getattr(record, "clock_in_status", None),
                "clock_out_status": getattr(record, "clock_out_status", None),
                "clock_in_location": record.clock_in_location,
                "clock_out_location": record.clock_out_location,
                "clock_in_gps_lat": record.clock_in_gps_lat,
                "clock_in_gps_lng": record.clock_in_gps_lng,
                "clock_out_gps_lat": record.clock_out_gps_lat,
                "clock_out_gps_lng": record.clock_out_gps_lng,
                "clock_in_device": record.clock_in_device,
                "clock_out_device": record.clock_out_device,
                "correction_status": _enum_value(record.correction_status),
                "correction_note": record.correction_note,
                "work_minutes": int(float(record.work_hours or 0) * 60),
                "overtime_minutes": int(float(record.overtime_hours or 0) * 60),
                "source": source,
            }
        )

    leave_details = []
    seen_leave_keys: set[tuple[str, date, str]] = set()
    for effect in active_effects:
        effect_type = str(effect.effect_type or "")
        if effect_type not in {"leave", "outside", "business_trip", "field_work", "punch_correction"}:
            continue
        if effect.work_date is None:
            continue
        payload = effect.payload_json if isinstance(effect.payload_json, dict) else {}
        detail = payload.get("effect_detail") if isinstance(payload.get("effect_detail"), dict) else {}
        punch_type = _dashboard_punch_type(detail.get("punch_type")) if effect_type == "punch_correction" else None
        punch_time = detail.get("punch_time") if effect_type == "punch_correction" else None
        business_id = (
            detail.get("correction_id")
            or detail.get("leave_request_id")
            or effect.source_business_id
            or effect.source_instance_id
            or effect.id
        )
        business_key = str(business_id)
        if effect_type == "punch_correction":
            business_key = f"{business_key}:{punch_type or ''}:{punch_time or _iso(effect.start_at) or ''}"
        key = (effect_type, effect.work_date, business_key)
        if key in seen_leave_keys:
            continue
        seen_leave_keys.add(key)
        minutes = int(effect.minutes or 0)
        leave_details.append(
            {
                "id": f"effect:{effect.id}",
                "date": effect.work_date.isoformat(),
                "type": effect_type,
                "label": _leave_label(effect_type, payload),
                "status": effect.effect_status,
                "minutes": minutes,
                "days": round(minutes / 480, 2) if minutes else 0,
                "source_module": effect.source_module,
                "source_business_id": effect.source_business_id,
                "source_instance_id": effect.source_instance_id,
                "start_at": _iso(effect.start_at),
                "end_at": _iso(effect.end_at),
                "reason": str(
                    detail.get("reason")
                    or detail.get("client_name")
                    or detail.get("trip_location")
                    or payload.get("reason")
                    or payload.get("client_name")
                    or payload.get("trip_location")
                    or ""
                ) or None,
                "punch_type": punch_type,
                "punch_type_label": _dashboard_punch_type_label(punch_type) if effect_type == "punch_correction" else None,
                "punch_time": (punch_time or _iso(effect.start_at)) if effect_type == "punch_correction" else None,
            }
        )

    for request in approved_leave_requests:
        label = getattr(getattr(request, "leave_type", None), "name", None) or "请假"
        if getattr(getattr(request, "leave_type", None), "code", None) == "comp_time":
            label = "调休"
        for current_day in _iter_days(request.start_date, request.end_date):
            key = ("leave", current_day, str(request.id))
            if key in seen_leave_keys:
                continue
            seen_leave_keys.add(key)
            leave_details.append(
                {
                    "id": f"leave:{request.id}:{current_day.isoformat()}",
                    "date": current_day.isoformat(),
                    "type": "leave",
                    "label": label,
                    "status": _enum_value(request.approval_status),
                    "minutes": 480,
                    "days": 1,
                    "source_module": "leave",
                    "source_business_id": request.id,
                    "start_at": _iso(datetime.combine(current_day, datetime.min.time())),
                    "end_at": _iso(datetime.combine(current_day, datetime.max.time())),
                    "reason": request.reason,
                }
            )

    for request in correction_requests:
        punch_type = _dashboard_punch_type(request.punch_type)
        key = ("punch_correction", request.correction_date, str(request.id))
        if key in seen_leave_keys:
            continue
        seen_leave_keys.add(key)
        leave_details.append(
            {
                "id": f"correction:{request.id}",
                "date": request.correction_date.isoformat(),
                "type": "punch_correction",
                "label": "补卡",
                "status": str(request.status or "pending"),
                "minutes": 0,
                "days": 0,
                "source_module": "attendance_punch_correction",
                "source_business_id": request.id,
                "start_at": _iso(request.punch_time),
                "end_at": _iso(request.punch_time),
                "reason": request.reason,
                "punch_type": punch_type,
                "punch_type_label": _dashboard_punch_type_label(punch_type),
                "punch_time": _iso(request.punch_time),
            }
        )

    for record in outside_records:
        key = ("outside", record.date, f"record:{record.id}")
        if key in seen_leave_keys:
            continue
        seen_leave_keys.add(key)
        leave_details.append(
            {
                "id": f"outside-record:{record.id}",
                "date": record.date.isoformat(),
                "type": "outside",
                "label": "外出打卡",
                "status": "recorded",
                "minutes": 0,
                "days": 0,
                "source_module": "attendance_record",
                "source_business_id": record.id,
                "start_at": _iso(record.clock_in_time or record.clock_out_time),
                "end_at": _iso(record.clock_out_time or record.clock_in_time),
                "reason": record.clock_in_location or record.clock_out_location,
            }
        )

    overtime_details = []
    seen_overtime_keys: set[tuple[date, str]] = set()
    seen_overtime_business_ids: set[str] = set()
    seen_overtime_instance_ids: set[str] = set()
    for request in approved_overtime_requests:
        hours = Decimal(str(request.hours or 0))
        minutes = int(hours * Decimal("60"))
        overtime_type = str(request.overtime_type or "weekday")
        date_type_key = {
            "weekday": "workday",
            "weekend": "restday",
            "holiday": "holiday",
        }.get(overtime_type, _day_type_key(await _day_type_for(request.overtime_date)))
        settlement_key, settlement_label = await _settlement_for_overtime(request.overtime_date, hours)
        key = (request.overtime_date, f"request:{request.id}")
        seen_overtime_keys.add(key)
        seen_overtime_business_ids.add(str(request.id))
        overtime_details.append(
            {
                "id": f"overtime:{request.id}",
                "date": request.overtime_date.isoformat(),
                "date_type": date_type_key,
                "date_type_label": {
                    "workday": "工作日",
                    "restday": "休息日",
                    "holiday": "节假日",
                }.get(date_type_key, "工作日"),
                "minutes": minutes,
                "hours": float(hours),
                "settlement": settlement_key,
                "settlement_label": settlement_label,
                "start_time": request.start_time,
                "end_time": request.end_time,
                "status": request.status,
                "reason": request.reason,
                "source": "overtime_request",
            }
        )

    for effect in active_effects:
        if str(effect.effect_type or "") != "overtime" or effect.work_date is None:
            continue
        payload = effect.payload_json if isinstance(effect.payload_json, dict) else {}
        business_id = payload.get("overtime_request_id") or effect.source_business_id or effect.source_instance_id or effect.id
        if str(business_id) in seen_overtime_business_ids:
            continue
        key = (effect.work_date, f"effect:{business_id}")
        if key in seen_overtime_keys:
            continue
        seen_overtime_keys.add(key)
        if effect.source_instance_id:
            seen_overtime_instance_ids.add(str(effect.source_instance_id))
        day_type = await _day_type_for(effect.work_date)
        hours = Decimal(str(effect.minutes or 0)) / Decimal("60")
        settlement_key, settlement_label = await _settlement_for_overtime(
            effect.work_date,
            hours,
            effect.pay_policy,
        )
        overtime_details.append(
            {
                "id": f"overtime-effect:{effect.id}",
                "date": effect.work_date.isoformat(),
                "date_type": _day_type_key(day_type),
                "date_type_label": _day_type_label(day_type),
                "minutes": int(effect.minutes or 0),
                "hours": float(hours),
                "settlement": settlement_key,
                "settlement_label": settlement_label,
                "start_time": effect.start_at.strftime("%H:%M") if effect.start_at else None,
                "end_time": effect.end_at.strftime("%H:%M") if effect.end_at else None,
                "status": effect.effect_status,
                "reason": str(payload.get("reason") or "") or None,
                "source": "attendance_effect",
            }
        )

    for item in approved_overtime_instance_items:
        instance = item["instance"]
        instance_id = str(instance.id)
        business_id = str(getattr(instance, "business_id", "") or "")
        business_code = str(getattr(instance, "business_type", None) or getattr(instance, "module", None) or "")
        if instance_id in seen_overtime_instance_ids:
            continue
        if business_code in {"overtime", "overtime_request"} and business_id and business_id in seen_overtime_business_ids:
            continue
        work_day = item["work_date"]
        key = (work_day, f"approval:{instance.id}:{getattr(instance, 'business_id', '')}")
        if key in seen_overtime_keys:
            continue
        seen_overtime_keys.add(key)
        day_type = await _day_type_for(work_day)
        minutes = int(item["minutes"] or 0)
        hours = Decimal(minutes) / Decimal("60")
        settlement_key, settlement_label = await _settlement_for_overtime(work_day, hours)
        overtime_details.append(
            {
                "id": f"overtime-approval:{instance.id}:{work_day.isoformat()}",
                "date": work_day.isoformat(),
                "date_type": _day_type_key(day_type),
                "date_type_label": _day_type_label(day_type),
                "minutes": minutes,
                "hours": float(hours),
                "settlement": settlement_key,
                "settlement_label": settlement_label,
                "start_time": item["start_at"].strftime("%H:%M") if item.get("has_start_time") else None,
                "end_time": item["end_at"].strftime("%H:%M") if item.get("has_end_time") else None,
                "status": instance.status,
                "reason": item.get("reason"),
                "source": "approval_instance",
            }
        )

    existing_overtime_dates = {date.fromisoformat(str(item["date"])) for item in overtime_details}
    for record in records:
        hours = Decimal(str(record.overtime_hours or 0))
        if hours <= 0 or record.date in existing_overtime_dates:
            continue
        day_type = await _day_type_for(record.date)
        settlement_key, settlement_label = await _settlement_for_overtime(record.date, hours)
        overtime_details.append(
            {
                "id": f"attendance-record:{record.id}",
                "date": record.date.isoformat(),
                "date_type": _day_type_key(day_type),
                "date_type_label": _day_type_label(day_type),
                "minutes": int(hours * Decimal("60")),
                "hours": float(hours),
                "settlement": settlement_key,
                "settlement_label": settlement_label,
                "start_time": None,
                "end_time": None,
                "status": "recorded",
                "reason": record.anomaly_type,
                "source": "attendance_record",
            }
        )

    leave_details.sort(key=lambda item: str(item["date"]), reverse=True)
    overtime_details.sort(key=lambda item: str(item["date"]), reverse=True)
    overtime_minutes_value = max(
        overtime_minutes_value,
        sum(int(item.get("minutes") or 0) for item in overtime_details),
        month_summary_overtime_minutes,
    )
    comp_time_balance_hours = await _comp_time_balance_hours()
    comp_time_balance_minutes = int(
        (comp_time_balance_hours * Decimal("60")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    async def _build_overtime_month_items() -> list[dict[str, Any]]:
        year_start = date(base_day.year, 1, 1)
        year_end = date(base_day.year, 12, 31)
        totals: dict[int, Decimal] = {}
        source_counts: dict[int, int] = {}
        seen_keys: set[tuple[date, str]] = set()
        seen_business_ids: set[str] = set()
        seen_instance_ids: set[str] = set()

        def add_hours(work_day: date, source_key: str, hours: Decimal | float | int) -> None:
            if work_day.year != base_day.year:
                return
            key = (work_day, source_key)
            if key in seen_keys:
                return
            try:
                parsed = Decimal(str(hours or 0))
            except Exception:
                parsed = Decimal("0.0")
            if parsed <= 0:
                return
            seen_keys.add(key)
            month = work_day.month
            totals[month] = totals.get(month, Decimal("0.0")) + parsed
            source_counts[month] = source_counts.get(month, 0) + 1

        request_result = await db.execute(
            select(OvertimeRequest)
            .where(
                and_(
                    OvertimeRequest.employee_id == current_user.id,
                    OvertimeRequest.overtime_date >= year_start,
                    OvertimeRequest.overtime_date <= year_end,
                    OvertimeRequest.status == "approved",
                )
            )
            .order_by(OvertimeRequest.overtime_date.desc(), OvertimeRequest.id.desc())
        )
        for request in request_result.scalars().all():
            request_id = str(request.id)
            seen_business_ids.add(request_id)
            comp_hours = await _comp_hours_for_overtime(
                request.overtime_date,
                Decimal(str(request.hours or 0)),
            )
            add_hours(request.overtime_date, f"request:{request_id}", comp_hours)

        if attendance_effects_available:
            effect_result = await db.execute(
                select(AttendanceEffect)
                .where(
                    and_(
                        AttendanceEffect.employee_id == current_user.id,
                        AttendanceEffect.effect_status == "active",
                        AttendanceEffect.effect_type == "overtime",
                        AttendanceEffect.work_date >= year_start,
                        AttendanceEffect.work_date <= year_end,
                    )
                )
                .order_by(AttendanceEffect.work_date.desc(), AttendanceEffect.id.desc())
            )
            for effect in effect_result.scalars().all():
                if effect.work_date is None:
                    continue
                payload = effect.payload_json if isinstance(effect.payload_json, dict) else {}
                business_id = payload.get("overtime_request_id") or effect.source_business_id or effect.source_instance_id or effect.id
                if str(business_id) in seen_business_ids:
                    continue
                if effect.source_instance_id:
                    seen_instance_ids.add(str(effect.source_instance_id))
                comp_hours = await _comp_hours_for_overtime(
                    effect.work_date,
                    Decimal(str(effect.minutes or 0)) / Decimal("60"),
                    effect.pay_policy,
                )
                add_hours(effect.work_date, f"effect:{business_id}", comp_hours)

        for item in [
            detail
            for instance in approved_overtime_instances
            for detail in _approved_overtime_instance_items(instance, year_start, year_end)
        ]:
            instance = item["instance"]
            instance_id = str(instance.id)
            business_id = str(getattr(instance, "business_id", "") or "")
            business_code = str(getattr(instance, "business_type", None) or getattr(instance, "module", None) or "")
            if instance_id in seen_instance_ids:
                continue
            if business_code in {"overtime", "overtime_request"} and business_id and business_id in seen_business_ids:
                continue
            work_day = item["work_date"]
            comp_hours = await _comp_hours_for_overtime(
                work_day,
                Decimal(int(item["minutes"] or 0)) / Decimal("60"),
            )
            add_hours(work_day, f"approval:{instance_id}:{business_id}", comp_hours)

        existing_dates = {work_day for work_day, _ in seen_keys}
        record_result = await db.execute(
            select(AttendanceRecord)
            .where(
                and_(
                    AttendanceRecord.employee_id == current_user.id,
                    AttendanceRecord.date >= year_start,
                    AttendanceRecord.date <= year_end,
                    AttendanceRecord.overtime_hours > 0,
                )
            )
            .order_by(AttendanceRecord.date.desc(), AttendanceRecord.id.desc())
        )
        for record in record_result.scalars().all():
            if record.date in existing_dates:
                continue
            comp_hours = await _comp_hours_for_overtime(record.date, record.overtime_hours)
            add_hours(record.date, f"record:{record.id}", comp_hours)

        items: list[dict[str, Any]] = []
        for month in sorted(totals.keys(), reverse=True):
            hours = totals[month].quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            if hours <= 0:
                continue
            items.append(
                {
                    "id": f"overtime-month:{base_day.year}-{month:02d}",
                    "year": base_day.year,
                    "month": month,
                    "label": f"{base_day.year}年{month}月",
                    "hours": float(hours),
                    "minutes": int((hours * Decimal("60")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
                    "source_count": int(source_counts.get(month, 0)),
                }
            )
        return items

    overtime_month_items = await _build_overtime_month_items()

    attendance_counts = {
        "all": len(attendance_details),
        "normal": sum(1 for item in attendance_details if item["status"] == "normal" and not item["is_abnormal"]),
        "rest": sum(1 for item in attendance_details if item["is_rest_day"]),
        "abnormal": sum(1 for item in attendance_details if item["is_abnormal"]),
        "late": sum(1 for item in attendance_details if item["status"] == "late"),
        "early_leave": sum(1 for item in attendance_details if item["status"] == "early_leave"),
        "missed_clock": sum(1 for item in attendance_details if item["status"] == "missed_clock"),
        "absent": sum(1 for item in attendance_details if item["status"] == "absent"),
    }
    leave_counts = {
        "all": len(leave_details),
        "leave": sum(1 for item in leave_details if item["type"] == "leave"),
        "punch_correction": sum(1 for item in leave_details if item["type"] == "punch_correction"),
        "outside": sum(1 for item in leave_details if item["type"] == "outside"),
        "business_trip": sum(1 for item in leave_details if item["type"] == "business_trip"),
        "field_work": sum(1 for item in leave_details if item["type"] == "field_work"),
    }
    overtime_date_counts = {
        "all": len(overtime_details),
        "workday": sum(1 for item in overtime_details if item["date_type"] == "workday"),
        "restday": sum(1 for item in overtime_details if item["date_type"] == "restday"),
        "holiday": sum(1 for item in overtime_details if item["date_type"] == "holiday"),
    }
    overtime_settlement_counts = {
        "all": len(overtime_details),
        "comp_time": sum(1 for item in overtime_details if item["settlement"] == "comp_time"),
        "overtime_pay": sum(1 for item in overtime_details if item["settlement"] == "overtime_pay"),
        "none": sum(1 for item in overtime_details if item["settlement"] == "none"),
    }

    return {
        "range": {
            "type": range_type,
            "target_date": base_day.isoformat(),
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "year": base_day.year,
            "month": base_day.month,
        },
        "applications": {
            "items": [
                {"key": "legal_overtime", "label": "法定节假日加班申请", "module": "overtime", "pending": pending_overtime_total},
                {"key": "leave", "label": "请假", "module": "leave", "pending": int(pending_leave or 0)},
                {"key": "business_trip", "label": "出差", "module": "approval", "pending": int(pending_business_trip or 0)},
                {"key": "outside", "label": "外出", "module": "attendance_outside", "pending": int(pending_outside or 0)},
                {"key": "punch_correction", "label": "打卡补卡", "module": "attendance_punch_correction", "pending": pending_corrections_total},
            ],
            "record_count": int(
                (pending_leave or 0)
                + pending_overtime_total
                + pending_corrections_total
                + (pending_business_trip or 0)
                + (pending_outside or 0)
            ),
        },
        "stats": {
            "normal_days": normal_days,
            "abnormal_days": abnormal_days,
            "late_count": late_count,
            "early_count": early_count,
            "missed_count": missed_count,
            "absent_count": absent_count,
            "leave_count": leave_count_value,
            "correction_count": max(int(corrections_count or 0), leave_counts["punch_correction"]),
            "outside_count": outside_count_value,
            "business_trip_count": business_trip_count_value,
            "field_work_count": field_work_count_value,
            "overtime_minutes": comp_time_balance_minutes,
            "overtime_hours": float(comp_time_balance_hours),
        },
        "monthly_abnormal": {
            "pending_days": len({r.date for r in monthly_abnormal_records}),
            "has_today_abnormal": day_is_abnormal,
            "today_status": day_status,
            "today_anomaly": day_record.anomaly_type if day_record else None,
            "latest_date": latest_month_abnormal.date.isoformat() if latest_month_abnormal else None,
            "latest_status": _status_text(latest_month_abnormal) if latest_month_abnormal else None,
            "latest_anomaly": latest_month_abnormal.anomaly_type if latest_month_abnormal else None,
        },
        "daily": {
            "clock_in_time": _iso(clock_in),
            "clock_out_time": _iso(clock_out),
            "status": day_status,
            "is_abnormal": day_is_abnormal,
            "anomaly_type": day_record.anomaly_type if day_record else None,
            "clock_in_status": getattr(day_record, "clock_in_status", None) if day_record else "未打卡",
            "clock_out_status": getattr(day_record, "clock_out_status", None) if day_record else "未打卡",
            "location_status": getattr(day_record, "location_status", None) if day_record else None,
            "location_abnormal": bool(getattr(day_record, "location_abnormal", False)) if day_record else False,
            "work_minutes": int(float(getattr(day_record, "work_hours", 0) or 0) * 60) if day_record else 0,
            "expected_minutes": expected_minutes,
        },
        "details": {
            "attendance_records": attendance_details,
            "leave_items": leave_details,
            "overtime_items": overtime_details,
            "overtime_month_items": overtime_month_items,
            "counts": {
                "attendance": attendance_counts,
                "leave": leave_counts,
                "overtime_date_type": overtime_date_counts,
                "overtime_settlement": overtime_settlement_counts,
            },
        },
    }


@router.get("/mobile/comp-time-details", response_model=list[MobileDashboardOvertimeDetailOut])
async def get_mobile_comp_time_details(
    year: Optional[int] = Query(None, ge=2000, le=2100, description="查询年度，默认当前年份"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/mobile/comp-time-details — 当前员工加班转调休来源明细。

    用于移动端首页“调休余额”弹层展示余额背后的具体加班日期、时段和来源。
    """
    target_year = year or date.today().year
    return await _mobile_comp_time_detail_rows(db, current_user, target_year)


@router.get("/mobile/export")
async def export_mobile_attendance_stats(
    range_type: str = Query("month", pattern="^(day|week|month)$", description="统计周期"),
    target_date: Optional[str] = Query(None, description="目标日期 YYYY-MM-DD，默认今天"),
    status_filter: str = Query("all", pattern="^(all|normal|abnormal)$", description="导出打卡状态过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/mobile/export — 导出当前员工自己的考勤统计明细。

    员工端导出只能导出本人数据，避免复用管理端报表接口造成权限扩大。
    """
    if target_date:
        try:
            base_day = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD") from exc
    else:
        base_day = date.today()

    if range_type == "day":
        start_day = end_day = base_day
    elif range_type == "week":
        start_day = base_day - timedelta(days=base_day.weekday())
        end_day = start_day + timedelta(days=6)
    else:
        start_day = base_day.replace(day=1)
        if base_day.month == 12:
            end_day = date(base_day.year + 1, 1, 1) - timedelta(days=1)
        else:
            end_day = date(base_day.year, base_day.month + 1, 1) - timedelta(days=1)

    result = await db.execute(
        select(AttendanceRecord)
        .where(
            and_(
                AttendanceRecord.employee_id == current_user.id,
                AttendanceRecord.date >= start_day,
                AttendanceRecord.date <= end_day,
            )
        )
        .order_by(AttendanceRecord.date.asc())
    )
    records = list(result.scalars().all())

    runtime_cache: dict[date, dict] = {}
    for record in records:
        runtime = runtime_cache.get(record.date)
        if runtime is None:
            runtime = await AttendanceRecordService._get_rule_runtime(db, record.employee_id, record.date)
            runtime_cache[record.date] = runtime
        AttendanceRecordService._attach_display_status(record, runtime)

    def _record_status_text(record: AttendanceRecord) -> str:
        value = getattr(record, "display_status", None) or getattr(record.status, "value", record.status) or ""
        return str(value)

    def _record_is_abnormal(record: AttendanceRecord) -> bool:
        text = f"{_record_status_text(record)} {record.anomaly_type or ''}"
        return any(item in text for item in ("迟到", "早退", "缺卡", "旷工", "异常")) or bool(record.anomaly_type)

    if status_filter == "normal":
        records = [record for record in records if _record_status_text(record) == "正常" and not _record_is_abnormal(record)]
    elif status_filter == "abnormal":
        records = [record for record in records if _record_is_abnormal(record)]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["日期", "状态", "上班打卡", "下班打卡", "工时", "加班小时", "异常", "上班地点", "下班地点", "打卡设备"])
    for record in records:
        status = getattr(record, "display_status", None) or getattr(record.status, "value", record.status) or ""
        writer.writerow(
            [
                record.date.isoformat(),
                status,
                record.clock_in_time.isoformat() if record.clock_in_time else "",
                record.clock_out_time.isoformat() if record.clock_out_time else "",
                f"{float(record.work_hours or 0):.2f}",
                f"{float(record.overtime_hours or 0):.2f}",
                record.anomaly_type or "",
                record.clock_in_location or "",
                record.clock_out_location or "",
                record.clock_out_device or record.clock_in_device or "",
            ]
        )

    content = ("\ufeff" + output.getvalue()).encode("utf-8")
    filename = f"attendance_mobile_{range_type}_{start_day.strftime('%Y%m%d')}_{end_day.strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post(
    "/outside-approval",
    response_model=AttendanceOutsideApprovalOut,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
async def create_attendance_outside_approval(
    data: AttendanceOutsideApprovalCreate,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/outside-approval — 旧版外出打卡审批写入口

    MVP 阶段外出申请统一从审批中心提交，本端点仅保留历史兼容的路由形态。
    """
    _block_legacy_mvp_write(request, "外出审批", "outside")
    return await create_outside_approval_service(db, current_user, data)


@router.post("/rules", response_model=AttendanceRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    data: AttendanceRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /attendance/rules — 创建考勤规则

    用途：
        为指定工作地点创建一套考勤规则，包含上班时间、下班时间、
        迟到/早退宽限分钟数、弹性打卡窗口等配置。
        通常由 HR 或管理员在新建工作地点或调整规则时调用。

    权限：
        仅管理员和 HR 可维护规则。

    请求体 (Body)：
        AttendanceRuleCreate — 考勤规则创建 Schema，包含以下字段：
            - name: str                  考勤规则名称
            - location_id: int | None    关联工作地点 ID（可为空表示全局规则）
            - work_start_time: time      标准上班时间（如 09:00）
            - work_end_time: time        标准下班时间（如 18:00）
            - late_threshold_minutes: int 迟到宽限分钟数（如 5 分钟内不算迟到）
            - early_leave_threshold_minutes: int 早退宽限分钟数
            - is_active: bool            是否启用，默认 True

    响应：
        201 Created — AttendanceRuleOut，返回新建的考勤规则对象（含 id）

    对应 Service：
        AttendanceRuleService.create(db, data)
    """
    rule = await AttendanceRuleService.create(db, data, current_user=current_user)
    return rule


@router.get("/rules", response_model=list[AttendanceRuleOut])
async def list_rules(
    location_id: Optional[int] = Query(None, description="工作地点ID"),
    is_active: Optional[bool] = Query(None, description="是否启用"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/rules — 获取考勤规则列表

    用途：
        查询所有考勤规则，支持按工作地点和启用状态过滤。
        前端排班配置页面和考勤管理页面调用此接口获取可选规则列表。

    权限：
        需要管理员、HR 或项目负责人角色。

    查询参数 (Query)：
        - location_id: int | None   按工作地点 ID 过滤（不传则返回所有地点的规则）
        - is_active: bool | None    按启用状态过滤（True=仅启用，False=仅禁用，不传=全部）

    响应：
        200 OK — list[AttendanceRuleOut]，考勤规则列表（无分页）

    对应 Service：
        AttendanceRuleService.list_rules(db, location_id, is_active)
    """
    return await AttendanceRuleService.list_rules(db, location_id, is_active)


@router.get("/rules/{rule_id}", response_model=AttendanceRuleOut)
async def get_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/rules/{rule_id} — 获取考勤规则详情

    用途：
        按主键 ID 查询单条考勤规则的完整详情。

    权限：
        需要管理员、HR 或项目负责人角色。

    路径参数 (Path)：
        - rule_id: int   考勤规则主键 ID

    响应：
        200 OK  — AttendanceRuleOut，考勤规则详情
        404 Not Found — 规则不存在时返回 {"detail": "考勤规则不存在"}

    对应 Service：
        AttendanceRuleService.get(db, rule_id)
    """
    rule = await AttendanceRuleService.get(db, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="考勤规则不存在")
    return rule


@router.put("/rules/{rule_id}", response_model=AttendanceRuleOut)
async def update_rule(
    rule_id: int,
    data: AttendanceRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /attendance/rules/{rule_id} — 更新考勤规则

    用途：
        修改指定考勤规则的字段，使用 Pydantic v2 partial update（只传需要修改的字段）。
        常用场景：调整上下班时间、修改宽限分钟数、启用/禁用规则。

    权限：
        仅管理员和 HR 可维护规则。

    路径参数 (Path)：
        - rule_id: int   考勤规则主键 ID

    请求体 (Body)：
        AttendanceRuleUpdate — 考勤规则更新 Schema（所有字段可选）

    响应：
        200 OK  — AttendanceRuleOut，更新后的考勤规则对象
        404 Not Found — 规则不存在时返回 {"detail": "考勤规则不存在"}

    对应 Service：
        AttendanceRuleService.update(db, rule_id, data)
    """
    rule = await AttendanceRuleService.update(
        db, rule_id, data, current_user=current_user
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="考勤规则不存在")
    return rule


@router.post("/rules/conflicts/check", response_model=list[AttendanceRuleConflictOut])
async def check_rule_conflicts(
    data: AttendanceRuleUpdate,
    exclude_rule_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    payload = data.model_dump(exclude_unset=True)
    conflicts = await AttendanceRuleService.detect_conflicts(
        db, payload, exclude_rule_id=exclude_rule_id
    )
    return conflicts


@router.patch("/rules/{rule_id}/toggle", response_model=AttendanceRuleOut)
async def toggle_rule(
    rule_id: int,
    data: AttendanceRuleToggle,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    rule = await AttendanceRuleService.toggle_active(
        db, rule_id, data.is_active, current_user=current_user
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="考勤规则不存在")
    return rule


@router.post("/rules/{rule_id}/copy", response_model=AttendanceRuleOut)
async def copy_rule(
    rule_id: int,
    data: AttendanceRuleCopyIn,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    rule = await AttendanceRuleService.copy_rule(
        db, rule_id, data.name, current_user=current_user
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="考勤规则不存在")
    return rule


@router.get("/rules/{rule_id}/logs", response_model=list[AttendanceRuleLogOut])
async def list_rule_logs(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    return await AttendanceRuleService.list_logs(db, rule_id)


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /attendance/rules/{rule_id} — 删除考勤规则

    用途：
        永久删除指定考勤规则。删除前应确认该规则没有关联的有效排班记录。

    权限：
        仅管理员和 HR 可维护规则。

    路径参数 (Path)：
        - rule_id: int   考勤规则主键 ID

    响应：
        204 No Content — 删除成功，无响应体
        404 Not Found  — 规则不存在时返回 {"detail": "考勤规则不存在"}

    对应 Service：
        AttendanceRuleService.delete(db, rule_id)
    """
    success = await AttendanceRuleService.delete(db, rule_id, current_user=current_user)
    if not success:
        raise HTTPException(status_code=404, detail="考勤规则不存在")


# ===================================================================
# 排班
# ===================================================================

@router.post("/shifts", response_model=ShiftScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_shift(
    data: ShiftScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /attendance/shifts — 创建单条排班

    用途：
        为某位员工在某个日期分配具体班次（绑定考勤规则）。
        单条创建适合临时调班场景；批量排班请使用 POST /shifts/batch。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    请求体 (Body)：
        ShiftScheduleCreate，主要字段：
            - employee_id: int      目标员工 ID
            - rule_id: int          关联的考勤规则 ID
            - work_date: date       排班日期
            - shift_type: str | None  班次类型（如白班/夜班等，可为空）

    响应：
        201 Created — ShiftScheduleOut，新建的排班记录

    对应 Service：
        ShiftScheduleService.create(db, data)
    """
    return await ShiftScheduleService.create(db, data)


@router.post("/shifts/batch", response_model=list[ShiftScheduleOut], status_code=status.HTTP_201_CREATED)
async def batch_create_shifts(
    data: ShiftScheduleBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /attendance/shifts/batch — 批量创建排班

    用途：
        一次为多名员工或某个日期范围批量创建排班记录，常用于月初排班操作。
        内部调用 ShiftScheduleService.batch_create 批量写入，避免 N+1 插入。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    请求体 (Body)：
        ShiftScheduleBatchCreate，主要字段：
            - employee_ids: list[int]   目标员工 ID 列表
            - rule_id: int              关联的考勤规则 ID
            - start_date: date          排班开始日期
            - end_date: date            排班结束日期
            - exclude_weekends: bool    是否跳过周六日（默认 True）

    响应：
        201 Created — list[ShiftScheduleOut]，批量创建的排班记录列表

    对应 Service：
        ShiftScheduleService.batch_create(db, data)
    """
    return await ShiftScheduleService.batch_create(db, data)


@router.get("/shifts", response_model=list[ShiftScheduleOut])
async def list_shifts(
    employee_id: Optional[int] = Query(None, description="员工ID"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    start_date: Optional[date] = Query(None, description="开始日期"),
    end_date: Optional[date] = Query(None, description="结束日期"),
    month: Optional[str] = Query(None, description="月份 (YYYY-MM格式)"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/shifts — 查询排班记录

    用途：
        查询排班记录，支持多种过滤方式。前端排班日历视图的主要数据来源。
        如果传入 month 参数（YYYY-MM），会自动解析为对应月份的 start_date 和 end_date，
        无需手动计算月份边界。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    查询参数 (Query)：
        - employee_id: int | None    按员工 ID 过滤
        - department_id: int | None  按部门 ID 过滤（查询该部门全部员工的排班）
        - start_date: date | None    查询开始日期（与 end_date 配合使用）
        - end_date: date | None      查询结束日期
        - month: str | None          月份字符串，格式 "YYYY-MM"（如 "2025-03"）
                                     传入后自动优先于 start_date/end_date

    注意：
        - month 参数与 start_date/end_date 不能同时有效：若 month 有值且
          start_date/end_date 未传，则以 month 解析结果为准
        - month 格式无效（非 YYYY-MM 或月份超范围）时返回 400

    响应：
        200 OK  — list[ShiftScheduleOut]，排班记录列表（无分页）
        400 Bad Request — month 参数格式无效

    对应 Service：
        ShiftScheduleService.list_shifts(db, employee_id, department_id, start_date, end_date)
    """
    # 如果传了 month 参数 (YYYY-MM), 自动解析为 start_date 和 end_date
    if month and not start_date and not end_date:
        try:
            parts = month.split("-")
            if len(parts) != 2:
                raise ValueError
            y, m = int(parts[0]), int(parts[1])
            if not (1 <= m <= 12):
                raise ValueError
            start_date = date(y, m, 1)
            if m == 12:
                end_date = date(y + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(y, m + 1, 1) - timedelta(days=1)
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="month 参数格式无效, 应为 YYYY-MM (月份范围 01-12)")

    return await ShiftScheduleService.list_shifts(
        db,
        employee_id=employee_id,
        department_id=department_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.put("/shifts/{shift_id}", response_model=ShiftScheduleOut)
async def update_shift(
    shift_id: int,
    data: ShiftScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    PUT /attendance/shifts/{shift_id} — 更新排班记录

    用途：
        修改某条排班的班次配置，如更换考勤规则、调整日期等。
        常用于临时调班（如某天改为弹性班）。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    路径参数 (Path)：
        - shift_id: int   排班记录主键 ID

    请求体 (Body)：
        ShiftScheduleUpdate — 排班更新 Schema（所有字段可选）

    响应：
        200 OK  — ShiftScheduleOut，更新后的排班记录
        404 Not Found — 排班记录不存在时返回 {"detail": "排班记录不存在"}

    对应 Service：
        ShiftScheduleService.update(db, shift_id, data)
    """
    schedule = await ShiftScheduleService.update(db, shift_id, data)
    if schedule is None:
        raise HTTPException(status_code=404, detail="排班记录不存在")
    return schedule


@router.delete("/shifts/{shift_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift(
    shift_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    DELETE /attendance/shifts/{shift_id} — 删除排班记录

    用途：
        删除指定排班记录，通常在录入错误时使用。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    路径参数 (Path)：
        - shift_id: int   排班记录主键 ID

    响应：
        204 No Content — 删除成功，无响应体
        404 Not Found  — 记录不存在时返回 {"detail": "排班记录不存在"}

    对应 Service：
        ShiftScheduleService.delete(db, shift_id)
    """
    success = await ShiftScheduleService.delete(db, shift_id)
    if not success:
        raise HTTPException(status_code=404, detail="排班记录不存在")


# ===================================================================
# 打卡
# ===================================================================

@router.get("/today-stats")
async def get_today_stats(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/today-stats — 今日考勤统计

    用途：
        获取今日全体员工的考勤概览统计数据，供管理驾驶舱或 HR 首页展示。
        典型返回内容：已打卡人数、应出勤人数、迟到人数、未打卡人数等聚合数字。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    响应：
        200 OK — dict，今日考勤统计，字段由 AttendanceRecordService.get_today_stats 决定，
                 通常包含：
                   - total_scheduled: int   今日排班总人数
                   - clocked_in: int        已上班打卡人数
                   - late_count: int        迟到人数
                   - absent_count: int      未打卡（旷工）人数

    对应 Service：
        AttendanceRecordService.get_today_stats(db)
    """
    stats = await AttendanceRecordService.get_today_stats(db)
    return stats


@router.post("/clock/validate")
async def validate_clock(
    data: ClockValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/clock/validate — 打卡前规则校验与状态预判

    对齐产品文档：输入员工、时间、位置/Wi-Fi/设备信息，返回是否符合规则、
    命中规则和预判结果。员工本人可校验自己；HR/管理员/主管可校验其他员工。
    """
    employee_id = data.employee_id or current_user.id
    if employee_id != current_user.id:
        can_validate = await AttendanceRecordService._has_any_role(
            db,
            current_user,
            ("admin", "hr", "manager"),
        )
        if not can_validate:
            raise HTTPException(status_code=403, detail="无权校验其他员工考勤")
    return await AttendanceRecordService.validate_clock(db, employee_id, data)


@router.post("/clock-in", response_model=AttendanceRecordOut)
async def clock_in(
    data: ClockInRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/clock-in — 员工上班打卡

    用途：
        记录当前登录员工的上班打卡时间。Service 内部会根据员工排班规则判断
        是否迟到，并自动生成相应的考勤异常记录（如有）。
        若打卡成功但同时检测到异常（如迟到），异常信息通过 anomalies 返回值
        标注，响应仍为 200 且包含完整的考勤记录。

    权限：
        需要登录（Depends(get_current_user)），员工本人调用，使用 current_user.id。

    请求体 (Body)：
        ClockInRequest，主要字段：
            - clock_in_time: datetime | None   打卡时间（为空则取服务器当前时间）
            - location: str | None             打卡位置（GPS 或地址描述，可选）
            - device_id: str | None            打卡设备标识（可选）

    响应：
        200 OK — AttendanceRecordOut，包含本次打卡的完整考勤记录：
                   - id: int                考勤记录 ID
                   - employee_id: int       员工 ID
                   - work_date: date        考勤日期
                   - clock_in_time: datetime 实际打卡时间
                   - status: str            考勤状态（normal/late/...）

    注意：
        - 若 anomalies 列表不为空，表示本次打卡检测到异常，但打卡仍然成功
        - 同一员工同一天不能重复打上班卡（Service 层校验）

    对应 Service：
        AttendanceRecordService.clock_in(db, current_user.id, data)
        返回值：(record, anomalies)
    """
    user_agent = request.headers.get("user-agent") if request else None
    record, anomalies = await AttendanceRecordService.clock_in(db, current_user.id, data, request_user_agent=user_agent)
    if anomalies:
        # 打卡成功但有异常提示, 通过 header 返回
        return AttendanceRecordOut.model_validate(record)
    return record


@router.post("/clock-out", response_model=AttendanceRecordOut)
async def clock_out(
    data: ClockOutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/clock-out — 员工下班打卡

    用途：
        记录当前登录员工的下班打卡时间。Service 内部会根据考勤规则判断
        是否早退，并计算实际工作时长（work_hours）写入考勤记录。
        若检测到早退，anomalies 中会包含相应标注。

    权限：
        需要登录（Depends(get_current_user)），员工本人调用，使用 current_user.id。

    请求体 (Body)：
        ClockOutRequest，主要字段：
            - clock_out_time: datetime | None  打卡时间（为空则取服务器当前时间）
            - location: str | None             打卡位置（可选）
            - device_id: str | None            打卡设备标识（可选）

    响应：
        200 OK — AttendanceRecordOut，更新后的完整考勤记录：
                   - clock_out_time: datetime  实际下班打卡时间
                   - work_hours: float         本日实际工作小时数
                   - status: str               考勤状态（含早退判断结果）

    注意：
        - 必须先有对应日期的上班打卡记录，否则 Service 返回错误
        - anomalies 返回值在本端点未额外处理，异常信息已记录在 record 中

    对应 Service：
        AttendanceRecordService.clock_out(db, current_user.id, data)
        返回值：(record, anomalies)
    """
    user_agent = request.headers.get("user-agent") if request else None
    record, anomalies = await AttendanceRecordService.clock_out(db, current_user.id, data, request_user_agent=user_agent)
    return record


@router.get("/punch-time-records")
async def get_punch_time_records(
    start_date: Optional[date] = Query(None, description="开始日期，默认当月1日"),
    end_date: Optional[date] = Query(None, description="结束日期，默认今天"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    keyword: Optional[str] = Query(None, description="姓名/工号关键词"),
    rule_id: Optional[int] = Query(None, description="规则ID"),
    include_recent_left: bool = Query(True, description="是否包含离职90天内成员"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/punch-time-records — 查询原始打卡时间记录。

    该接口读取不可覆盖的每次打卡流水，按日期列展开；日报/月报仍读取覆盖后的
    AttendanceRecord 最终记录，两者数据层职责不同。
    """
    today = (datetime.utcnow() + timedelta(hours=8)).date()
    target_start = start_date or today.replace(day=1)
    target_end = end_date or today
    try:
        return await AttendancePunchTimeRecordService.query_calendar(
            db,
            start_date=target_start,
            end_date=target_end,
            department_id=department_id,
            keyword=keyword,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/punch-time-records/export")
async def export_punch_time_records(
    start_date: Optional[date] = Query(None, description="开始日期，默认当月1日"),
    end_date: Optional[date] = Query(None, description="结束日期，默认今天"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    keyword: Optional[str] = Query(None, description="姓名/工号关键词"),
    rule_id: Optional[int] = Query(None, description="规则ID"),
    include_recent_left: bool = Query(True, description="是否包含离职90天内成员"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/punch-time-records/export — 导出原始打卡时间记录 Excel。
    """
    today = (datetime.utcnow() + timedelta(hours=8)).date()
    target_start = start_date or today.replace(day=1)
    target_end = end_date or today
    try:
        content = await AttendancePunchTimeRecordService.export_calendar(
            db,
            start_date=target_start,
            end_date=target_end,
            department_id=department_id,
            keyword=keyword,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    filename = f"attendance_punch_time_{target_start.strftime('%Y%m%d')}_{target_end.strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ===================================================================
# 考勤记录查询
# ===================================================================

@router.get("/daily-report")
async def get_daily_report(
    start_date: Optional[date] = Query(None, description="开始日期，默认当天"),
    end_date: Optional[date] = Query(None, description="结束日期，默认当天"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    keyword: Optional[str] = Query(None, description="姓名/工号关键词"),
    status: Optional[str] = Query(None, description="考勤状态（正常/迟到/早退/缺卡/旷工/请假/出差/异常）"),
    rule_id: Optional[int] = Query(None, description="规则ID"),
    include_recent_left: bool = Query(True, description="是否包含离职90天内成员"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/daily-report — 日报汇总在线查看

    返回两份在线数据：
    - overview_rows: 对齐企业微信“日报概况统计”结构
    - detail_rows: 对齐企业微信“日报打卡明细”结构
    """
    target_start = start_date or date.today()
    target_end = end_date or target_start
    if target_start > target_end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return await AttendanceReportService.get_daily_report_data(
        db,
        start_date=target_start,
        end_date=target_end,
        department_id=department_id,
        keyword=keyword,
        status=status,
        rule_id=rule_id,
        include_recent_left=include_recent_left,
        page=page,
        page_size=page_size,
    )


@router.get("/daily-report/export")
async def export_daily_report(
    start_date: Optional[date] = Query(None, description="开始日期，默认当天"),
    end_date: Optional[date] = Query(None, description="结束日期，默认当天"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    keyword: Optional[str] = Query(None, description="姓名/工号关键词"),
    status: Optional[str] = Query(None, description="考勤状态（正常/迟到/早退/缺卡/旷工/请假/出差/异常）"),
    rule_id: Optional[int] = Query(None, description="规则ID"),
    include_recent_left: bool = Query(True, description="是否包含离职90天内成员"),
    overview_columns: Optional[str] = Query(None, description="概况表导出列key，逗号分隔"),
    detail_columns: Optional[str] = Query(None, description="明细表导出列key，逗号分隔"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/daily-report/export — 导出日报汇总 Excel

    导出 Excel 含两张 sheet：
    - 概况统计与打卡明细
    - 打卡详情
    """
    target_start = start_date or date.today()
    target_end = end_date or target_start
    if target_start > target_end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")

    data = await AttendanceReportService.export_daily_report(
        db,
        start_date=target_start,
        end_date=target_end,
        department_id=department_id,
        keyword=keyword,
        status=status,
        rule_id=rule_id,
        include_recent_left=include_recent_left,
        overview_columns=[part.strip() for part in str(overview_columns or "").split(",") if part.strip()] or None,
        detail_columns=[part.strip() for part in str(detail_columns or "").split(",") if part.strip()] or None,
    )
    filename = f"attendance_daily_{target_start.strftime('%Y%m%d')}_{target_end.strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/monthly-report")
async def get_monthly_report(
    start_date: Optional[date] = Query(None, description="开始日期，默认当月1日"),
    end_date: Optional[date] = Query(None, description="结束日期，默认今天"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    keyword: Optional[str] = Query(None, description="姓名/工号关键词"),
    status: Optional[str] = Query(None, description="打卡状态（正常/迟到/早退/缺卡/旷工/请假/出差/异常）"),
    rule_id: Optional[int] = Query(None, description="规则ID"),
    include_recent_left: bool = Query(True, description="是否包含离职90天内成员"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/monthly-report — 月报汇总在线查看

    返回两份在线数据：
    - overview_rows: 对齐企业微信“月报概况统计”结构
    - detail_rows: 对齐企业微信“月报打卡明细”结构，包含日期动态列
    """
    today = date.today()
    target_start = start_date or today.replace(day=1)
    target_end = end_date or today
    if target_start > target_end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return await AttendanceReportService.get_monthly_report_data(
        db,
        start_date=target_start,
        end_date=target_end,
        department_id=department_id,
        keyword=keyword,
        status=status,
        rule_id=rule_id,
        include_recent_left=include_recent_left,
        page=page,
        page_size=page_size,
    )


@router.post("/monthly-report/tasks")
async def create_monthly_report_task(
    data: MonthlyReportTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /attendance/monthly-report/tasks — 创建月报异步生成任务。

    该接口只归一化查询条件并登记任务，立即返回 task_id；实际月报生成在
    后台任务中完成，前端通过 task_id 轮询状态并读取结果。
    """
    today = date.today()
    target_start = data.start_date or today.replace(day=1)
    target_end = data.end_date or today
    if target_start > target_end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return await AttendanceMonthlyReportTaskService.create_task(
        db,
        start_date=target_start,
        end_date=target_end,
        department_id=data.department_id,
        keyword=data.keyword,
        status=data.status,
        rule_id=data.rule_id,
        include_recent_left=data.include_recent_left,
        requested_by_id=getattr(current_user, "id", None),
        force_regenerate=data.force_regenerate,
    )


@router.get("/monthly-report/tasks/{task_id}")
async def get_monthly_report_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    task = await AttendanceMonthlyReportTaskService.get_task(task_id, db)
    if task is None:
        raise HTTPException(status_code=404, detail="月报任务不存在或已过期")
    return task


@router.get("/monthly-report/tasks/{task_id}/result")
async def get_monthly_report_task_result(
    task_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    task = await AttendanceMonthlyReportTaskService.get_task(task_id, db)
    if task is None:
        raise HTTPException(status_code=404, detail="月报任务不存在或已过期")
    if task["status"] == "failed":
        raise HTTPException(status_code=409, detail=task.get("error") or "月报任务生成失败")
    if task["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="月报任务仍在生成中")
    result = await AttendanceMonthlyReportTaskService.get_result(
        task_id,
        page=page,
        page_size=page_size,
        db=db,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="月报任务结果不存在或已过期")
    return result


@router.get("/monthly-report/tasks/{task_id}/export")
async def export_monthly_report_task_result(
    task_id: str,
    overview_columns: Optional[str] = Query(None, description="概况表导出列key，逗号分隔"),
    detail_columns: Optional[str] = Query(None, description="明细表基础列key，逗号分隔"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/monthly-report/tasks/{task_id}/export — 基于已生成任务导出月报。
    """
    task = await AttendanceMonthlyReportTaskService.get_task(task_id, db)
    if task is None:
        raise HTTPException(status_code=404, detail="月报任务不存在或已过期")
    if task["status"] == "failed":
        raise HTTPException(status_code=409, detail=task.get("error") or "月报任务生成失败")
    if task["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="月报任务仍在生成中")
    data = await AttendanceMonthlyReportTaskService.export_result(
        task_id,
        overview_columns=[part.strip() for part in str(overview_columns or "").split(",") if part.strip()] or None,
        detail_columns=[part.strip() for part in str(detail_columns or "").split(",") if part.strip()] or None,
        db=db,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="月报任务结果不存在或已过期")
    filename = f"attendance_monthly_task_{task_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/monthly-report/export")
async def export_wecom_monthly_report(
    start_date: Optional[date] = Query(None, description="开始日期，默认当月1日"),
    end_date: Optional[date] = Query(None, description="结束日期，默认今天"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    keyword: Optional[str] = Query(None, description="姓名/工号关键词"),
    status: Optional[str] = Query(None, description="打卡状态（正常/迟到/早退/缺卡/旷工/请假/出差/异常）"),
    rule_id: Optional[int] = Query(None, description="规则ID"),
    include_recent_left: bool = Query(True, description="是否包含离职90天内成员"),
    overview_columns: Optional[str] = Query(None, description="概况表导出列key，逗号分隔"),
    detail_columns: Optional[str] = Query(None, description="明细表基础列key，逗号分隔"),
    task_id: Optional[str] = Query(None, description="已生成月报任务ID，传入后复用任务结果"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/monthly-report/export — 导出月报汇总 Excel

    导出 Excel 含两张 sheet：
    - 月报概况统计
    - 月报打卡明细
    """
    today = date.today()
    target_start = start_date or today.replace(day=1)
    target_end = end_date or today
    if target_start > target_end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")

    export_overview_columns = [part.strip() for part in str(overview_columns or "").split(",") if part.strip()] or None
    export_detail_columns = [part.strip() for part in str(detail_columns or "").split(",") if part.strip()] or None
    if task_id:
        data = await AttendanceMonthlyReportTaskService.export_result(
            task_id,
            overview_columns=export_overview_columns,
            detail_columns=export_detail_columns,
            db=db,
        )
        if data is None:
            raise HTTPException(status_code=404, detail="月报任务结果不存在或已过期")
    else:
        data = await AttendanceReportService.export_wecom_monthly_report(
            db,
            start_date=target_start,
            end_date=target_end,
            department_id=department_id,
            keyword=keyword,
            status=status,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
            overview_columns=export_overview_columns,
            detail_columns=export_detail_columns,
        )
    filename = f"attendance_monthly_{target_start.strftime('%Y%m%d')}_{target_end.strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/records", response_model=PaginatedResponse)
async def query_records(
    employee_id: Optional[int] = Query(None),
    department_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None, description="姓名/工号关键词"),
    work_hour_type: Optional[str] = Query(None, description="工时组: flexible/standard/comprehensive"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    attendance_status: Optional[str] = Query(None, alias="status"),
    attendance_statuses: Optional[list[str]] = Query(None, alias="statuses"),
    anomalies_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/records — 分页查询考勤记录

    用途：
        支持多条件组合查询考勤记录，是 HR 考勤管理页面和员工自查的主要接口。
        查询参数均为可选，不传则返回全部记录（按日期倒序分页）。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。
        员工通常只查自己的记录（传 employee_id=自己），HR 可查全员。

    查询参数 (Query)：
        - employee_id: int | None    按员工 ID 过滤（不传则返回所有员工记录）
        - start_date: date | None    开始日期（格式 YYYY-MM-DD）
        - end_date: date | None      结束日期（格式 YYYY-MM-DD）
        - status: str | None         单个考勤状态过滤，兼容旧调用
        - statuses: str[] | str | None
                                     多个考勤状态过滤，支持重复 query 或逗号分隔；
                                     状态值一对一对应正常/迟到/早退/缺卡/旷工/请假/出差
        - page: int                  页码，从 1 开始，默认 1
        - page_size: int             每页条数，默认 20，最大 1000

    响应：
        200 OK — PaginatedResponse，包含：
                   - total: int               总记录数
                   - page: int                当前页码
                   - page_size: int           每页条数
                   - items: list[AttendanceRecordOut]  当页考勤记录列表
        400 Bad Request — status 参数值不在有效枚举范围内

    对应 Service：
        AttendanceRecordService.query_records(db, params)
        params 为 AttendanceRecordQuery 对象
    """
    try:
        parsed_statuses = _parse_attendance_status_filters(attendance_status, attendance_statuses)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"无效的状态: {exc}") from exc
    parsed_status = parsed_statuses[0] if len(parsed_statuses) == 1 else None

    parsed_wh = None
    if work_hour_type:
        from app.models.attendance import WorkHourType as WH
        try:
            parsed_wh = WH(work_hour_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的工时组: {work_hour_type}")

    can_manage_all = await AttendanceRecordService._has_any_role(
        db,
        current_user,
        ("admin", "hr", "manager"),
    )
    if not can_manage_all:
        if employee_id is not None and employee_id != current_user.id:
            raise HTTPException(status_code=403, detail="仅可查看自己的考勤记录")
        employee_id = current_user.id
        department_id = None
        keyword = None
        parsed_wh = None

    params = AttendanceRecordQuery(
        employee_id=employee_id,
        department_id=department_id,
        keyword=keyword,
        work_hour_type=parsed_wh,
        start_date=start_date,
        end_date=end_date,
        status=parsed_status,
        statuses=parsed_statuses or None,
        anomalies_only=anomalies_only,
        page=page,
        page_size=page_size,
    )
    records, total = await AttendanceRecordService.query_records(db, params)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[AttendanceRecordOut.model_validate(r) for r in records],
    )


@router.get("/records/{record_id}", response_model=AttendanceRecordOut)
async def get_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/records/{record_id} — 获取考勤记录详情

    用途：
        按主键 ID 查询单条考勤记录的完整信息，包含上下班打卡时间、
        工作时长、考勤状态、异常标注等详情。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    路径参数 (Path)：
        - record_id: int   考勤记录主键 ID

    响应：
        200 OK  — AttendanceRecordOut，完整的考勤记录详情
        404 Not Found — 记录不存在时返回 {"detail": "考勤记录不存在"}

    对应 Service：
        AttendanceRecordService.get_record(db, record_id)
    """
    record = await AttendanceRecordService.get_record(db, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="考勤记录不存在")
    can_manage_all = await AttendanceRecordService._has_any_role(
        db,
        current_user,
        ("admin", "hr", "manager"),
    )
    if not can_manage_all and record.employee_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可查看自己的考勤记录")
    return record


@router.post("/appeals", response_model=AttendanceRecordOut)
async def submit_appeal(
    data: AttendanceAppealCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    record = await AttendanceRecordService.submit_appeal(db, data, current_user)
    if record is None:
        raise HTTPException(status_code=404, detail="考勤记录不存在或无权操作")
    return record


@router.post("/appeals/{record_id}/review", response_model=AttendanceRecordOut)
async def review_appeal(
    record_id: int,
    data: AttendanceAppealReview,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    record = await AttendanceRecordService.review_appeal(db, record_id, data)
    if record is None:
        raise HTTPException(status_code=404, detail="考勤记录不存在")
    return record


# ===================================================================
# 异常申诉
# ===================================================================

@router.get("/anomalies", response_model=PaginatedResponse)
async def list_anomalies(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/anomalies — 查询考勤异常列表

    用途：
        查询存在异常的考勤记录（迟到、早退、缺卡、旷工等），
        是 HR 处理考勤异常的入口接口。支持按日期范围分页查询。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。
        HR/管理员通常用此接口查看全员异常，员工可查看自己的异常。

    查询参数 (Query)：
        - start_date: date | None    异常考勤记录的开始日期（可选）
        - end_date: date | None      异常考勤记录的结束日期（可选）
        - page: int                  页码，从 1 开始，默认 1
        - page_size: int             每页条数，默认 20，最大 1000

    响应：
        200 OK — PaginatedResponse，包含：
                   - total: int               总异常记录数
                   - page: int                当前页码
                   - page_size: int           每页条数
                   - items: list[AttendanceRecordOut]  异常考勤记录列表
                     （status 字段为 late/early_leave/absent/missing_punch 之一）

    对应 Service：
        AttendanceRecordService.list_anomalies(db, start_date, end_date, page, page_size)
    """
    records, total = await AttendanceRecordService.list_anomalies(
        db, start_date, end_date, page, page_size
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[AttendanceRecordOut.model_validate(r) for r in records],
    )

@router.post("/corrections", response_model=AttendanceRecordOut, deprecated=True)
async def request_correction(
    data: CorrectionRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/corrections — 申请补卡/考勤异常修正

    用途：
        员工对某条考勤记录申请修正，如补录漏打的上班卡或下班卡。
        Service 会校验申请员工是否有权操作该记录，并将记录置为「待审批」状态。

    权限：
        需要登录（Depends(get_current_user)），员工本人申请，使用 current_user.id。

    请求体 (Body)：
        CorrectionRequest，主要字段：
            - record_id: int              需要修正的考勤记录 ID
            - correct_clock_in: datetime | None   修正后的上班时间（可选）
            - correct_clock_out: datetime | None  修正后的下班时间（可选）
            - reason: str                         申请原因说明

    响应：
        200 OK  — AttendanceRecordOut，修正申请已提交的考勤记录（状态变为 pending_correction）
        404 Not Found — 记录不存在或员工无权操作时返回 {"detail": "考勤记录不存在或无权操作"}

    注意：
        此端点为旧版补卡流程，MVP 新提交请使用
        POST /api/v1/approval/applications 且 business_code=punch_correction。

    对应 Service：
        AttendanceRecordService.request_correction(db, current_user.id, data)
    """
    _block_legacy_mvp_write(request, "补卡申请", "punch_correction")
    record = await AttendanceRecordService.request_correction(db, current_user.id, data)
    if record is None:
        raise HTTPException(status_code=404, detail="考勤记录不存在或无权操作")
    return record


@router.post("/corrections/approve", response_model=AttendanceRecordOut)
async def approve_correction(
    data: CorrectionApproval,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /attendance/corrections/approve — 审批补卡申请（旧版流程）

    用途：
        管理员/HR/经理审批员工提交的考勤修正申请。
        审批通过后，考勤记录将以修正时间覆盖原打卡时间并重新计算考勤状态；
        审批拒绝则恢复原状态。

    权限：
        需要 admin、hr 或 manager 角色之一（Depends(require_roles("admin", "hr", "manager"))）。
        普通员工无权调用此接口。

    请求体 (Body)：
        CorrectionApproval，主要字段：
            - record_id: int    需要审批的考勤记录 ID
            - approved: bool    True 表示通过，False 表示拒绝
            - note: str | None  审批意见（可选）

    响应：
        200 OK  — AttendanceRecordOut，审批后的考勤记录
        404 Not Found — 记录不存在时返回 {"detail": "考勤记录不存在"}
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    注意：
        此端点为旧版补卡审批流程，新版请使用 POST /punch-corrections/{id}/review。

    对应 Service：
        AttendanceRecordService.approve_correction(db, data)
    """
    record = await AttendanceRecordService.approve_correction(db, data)
    if record is None:
        raise HTTPException(status_code=404, detail="考勤记录不存在")
    return record


@router.post(
    "/internal/punch-correction/eligibility",
    response_model=PunchCorrectionEligibilityResponse,
)
async def get_punch_correction_eligibility(
    data: PunchCorrectionEligibilityRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/internal/punch-correction/eligibility — 补卡可用性内部查询

    审批模块发起补卡审批前调用本接口，考勤模块只读计算指定员工和日期
    是否可补卡、可补哪些卡点以及可选补卡时间范围。
    """
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid4().hex[:12]}"
    try:
        target_date = PunchCorrectionEligibilityService.parse_target_date(data.target_date)
        PunchCorrectionEligibilityService.resolve_timezone(data.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    employee = await AttendanceRecordService._get_employee(db, data.employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="员工不存在")

    can_manage_all = await AttendanceRecordService._has_any_role(
        db,
        current_user,
        ("admin", "hr", "manager"),
    )
    if data.employee_id != current_user.id and not can_manage_all:
        raise HTTPException(status_code=403, detail="无权查询该员工补卡信息")

    try:
        result = await PunchCorrectionEligibilityService.calculate(
            db,
            data,
            target_date=target_date,
            target_employee=employee,
        )
    except (OperationalError, ProgrammingError) as exc:
        logger.warning("Punch correction eligibility dependency failed: %s", exc)
        raise HTTPException(status_code=503, detail="考勤服务暂不可用") from exc

    return PunchCorrectionEligibilityResponse(request_id=request_id, **result)


# ===================================================================
# 补卡申请流程 (PunchCorrectionRequest)
# ===================================================================

@router.get("/punch-corrections", response_model=PaginatedResponse)
async def list_punch_corrections(
    employee_id: Optional[int] = Query(None, description="员工ID过滤"),
    correction_status: Optional[str] = Query(None, alias="status", description="状态过滤: pending/approved/rejected"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/punch-corrections — 查询补卡申请列表（新版独立工单流程）

    用途：
        查询补卡申请工单列表，支持按员工 ID 和审批状态过滤。
        此接口对应独立的 PunchCorrectionRequest 模型（区别于旧版 corrections 接口），
        具备完整的工单状态流转（pending → approved/rejected）。
        响应中包含申请员工的姓名（employee_name），方便审批页展示。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。
        HR/管理员通常查询所有员工，员工自查时传自己的 employee_id。

    查询参数 (Query)：
        - employee_id: int | None    按员工 ID 过滤（不传则返回全员申请）
        - status: str | None         工单状态过滤：
                                       pending（待审批）/ approved（已通过）/ rejected（已拒绝）
        - skip: int                  跳过条数（offset 分页），默认 0
        - limit: int                 返回条数上限，默认 50，最大 1000

    响应：
        200 OK — PaginatedResponse，包含：
                   - total: int                    总工单数
                   - page: int                     固定为 1（offset 分页不计页码）
                   - page_size: int                等于 limit 参数值
                   - items: list[PunchCorrectionOut]  补卡申请工单列表
                     （每条包含 employee_name 字段）

    对应 Service：
        PunchCorrectionService.list_corrections(db, employee_id, status, skip, limit)
    """
    can_manage_all = await AttendanceRecordService._has_any_role(
        db,
        current_user,
        ("admin", "hr", "manager"),
    )
    if not can_manage_all:
        if employee_id is not None and employee_id != current_user.id:
            raise HTTPException(status_code=403, detail="仅可查看自己的补卡申请")
        employee_id = current_user.id

    items, total = await PunchCorrectionService.list_corrections(
        db, employee_id=employee_id, status=correction_status, skip=skip, limit=limit
    )
    # 填充 employee_name
    out_items = []
    for item in items:
        out = PunchCorrectionOut.model_validate(item)
        emp = item.employee
        if emp:
            out.employee_name = emp.name
        out_items.append(out)
    return PaginatedResponse(total=total, page=1, page_size=limit, items=out_items)


@router.post(
    "/punch-corrections",
    response_model=PunchCorrectionOut,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
async def create_punch_correction(
    data: PunchCorrectionCreate,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/punch-corrections — 旧版独立补卡工单写入口

    用途：
        MVP 阶段补卡申请统一从审批中心提交，本端点仅保留历史兼容的路由形态。

    权限：
        需要登录（Depends(get_current_user)），员工本人提交。

    请求体 (Body)：
        PunchCorrectionCreate，主要字段：
            - employee_id: int            申请员工 ID（通常为当前用户）
            - correction_date: date       需要补卡的日期
            - correction_type: str        补卡类型（clock_in / clock_out）
            - correct_time: datetime      补录的正确打卡时间
            - reason: str                 补卡原因说明

    响应：
        201 Created — PunchCorrectionOut，新建的补卡申请工单（含 employee_name）：
                       - id: int                    工单 ID
                       - status: str                初始状态为 "pending"
                       - employee_name: str | None  申请员工姓名

    对应 Service：
        PunchCorrectionService.create_correction(db, data)
    """
    _block_legacy_mvp_write(request, "补卡申请", "punch_correction")
    can_manage_all = await AttendanceRecordService._has_any_role(
        db,
        current_user,
        ("admin", "hr", "manager"),
    )
    if data.employee_id != current_user.id and not can_manage_all:
        raise HTTPException(status_code=403, detail="仅可为自己提交补卡申请")

    item = await PunchCorrectionService.create_correction(db, data)
    out = PunchCorrectionOut.model_validate(item)
    if item.employee:
        out.employee_name = item.employee.name
    return out


@router.post("/punch-corrections/{correction_id}/review", response_model=PunchCorrectionOut)
async def review_punch_correction(
    correction_id: int,
    data: PunchCorrectionReview,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /attendance/punch-corrections/{correction_id}/review — 审批补卡申请工单

    用途：
        对指定补卡申请工单进行审批操作（通过或拒绝）。
        审批通过后，Service 会自动更新对应日期的考勤记录中的打卡时间，
        并重新计算考勤状态（如迟到/缺卡等判断）。
        响应中包含申请员工的姓名（employee_name）。

    权限：
        需要登录（Depends(get_current_user)），无额外角色限制（由业务层判断是否有权审批）。
        建议前端限制为 HR/admin/manager 才能看到审批按钮。

    路径参数 (Path)：
        - correction_id: int   补卡申请工单主键 ID

    请求体 (Body)：
        PunchCorrectionReview，主要字段：
            - status: PunchCorrectionStatus  审批结果枚举：approved（通过）/ rejected（拒绝）
            - review_comment: str | None     审批意见（可选）

    响应：
        200 OK  — PunchCorrectionOut，审批后的工单（含 employee_name）：
                   - status: str   已更新为 "approved" 或 "rejected"
                   - reviewer_id: int  审批人 ID（当前用户）
        404 Not Found — 工单不存在时返回 {"detail": "补卡申请不存在"}

    对应 Service：
        PunchCorrectionService.review_correction(
            db, correction_id, reviewer_id, status, comment
        )
    """
    item = await PunchCorrectionService.review_correction(
        db,
        correction_id=correction_id,
        reviewer_id=current_user.id,
        status=data.status.value,
        comment=data.review_comment,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="补卡申请不存在")
    out = PunchCorrectionOut.model_validate(item)
    if item.employee:
        out.employee_name = item.employee.name
    return out


# ===================================================================
# 工作日历
# ===================================================================

@router.post("/calendar", response_model=WorkCalendarOut, status_code=status.HTTP_201_CREATED)
async def create_calendar_entry(
    data: WorkCalendarCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /attendance/calendar — 创建工作日历条目

    用途：
        新建一条工作日历条目，用于标记某日为法定节假日、调休工作日或特殊班次。
        考勤计算时会参考日历条目判断某天是否为工作日。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。
        建议前端限制为 HR/admin 才能编辑日历。

    请求体 (Body)：
        WorkCalendarCreate，主要字段：
            - calendar_date: date       日历日期
            - day_type: str             日期类型：workday（工作日）/ holiday（节假日）/
                                                  rest_day（休息日）/ makeup_day（调休工作日）
            - location_id: int | None   关联工作地点（为空表示全局生效）
            - remark: str | None        备注说明（如"元旦假期"）

    响应：
        201 Created — WorkCalendarOut，新建的工作日历条目（含 id）

    对应 Service：
        WorkCalendarService.create(db, data)
    """
    return await WorkCalendarService.create(db, data)


@router.post("/calendar/batch", response_model=list[WorkCalendarOut], status_code=status.HTTP_201_CREATED)
async def batch_create_calendar(
    data: WorkCalendarBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /attendance/calendar/batch — 批量创建工作日历条目

    用途：
        一次批量创建多条工作日历条目，常用于年初配置全年节假日安排。
        内部调用 WorkCalendarService.batch_create 批量写入数据库。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    请求体 (Body)：
        WorkCalendarBatchCreate，主要字段：
            - entries: list[WorkCalendarCreate]   日历条目列表（每条结构同单条创建）

    响应：
        201 Created — list[WorkCalendarOut]，批量创建的日历条目列表

    对应 Service：
        WorkCalendarService.batch_create(db, data.entries)
    """
    return await WorkCalendarService.batch_create(db, data.entries)


@router.get("/calendar", response_model=list[WorkCalendarOut])
async def list_calendar(
    start_date: date = Query(..., description="开始日期"),
    end_date: date = Query(..., description="结束日期"),
    location_id: Optional[int] = Query(None, description="工作地点ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/calendar — 查询工作日历

    用途：
        按日期范围查询工作日历条目，支持按工作地点过滤。
        前端日历视图展示节假日/调休标注时调用此接口，
        考勤 Service 内部也会调用此接口判断某天是否为工作日。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    查询参数 (Query)：
        - start_date: date          查询开始日期（必填）
        - end_date: date            查询结束日期（必填）
        - location_id: int | None   按工作地点过滤（不传则返回全局日历条目）

    响应：
        200 OK — list[WorkCalendarOut]，指定日期范围内的工作日历条目列表（无分页）

    对应 Service：
        WorkCalendarService.list_calendar(db, start_date, end_date, location_id)
    """
    return await WorkCalendarService.list_calendar(db, start_date, end_date, location_id)


@router.put("/calendar/{calendar_id}", response_model=WorkCalendarOut)
async def update_calendar_entry(
    calendar_id: int,
    data: WorkCalendarUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    PUT /attendance/calendar/{calendar_id} — 更新工作日历条目

    用途：
        修改指定工作日历条目，如更改日期类型、更新备注等。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    路径参数 (Path)：
        - calendar_id: int   工作日历条目主键 ID

    请求体 (Body)：
        WorkCalendarUpdate — 工作日历更新 Schema（所有字段可选）

    响应：
        200 OK  — WorkCalendarOut，更新后的日历条目
        404 Not Found — 条目不存在时返回 {"detail": "日历条目不存在"}

    对应 Service：
        WorkCalendarService.update(db, calendar_id, data)
    """
    entry = await WorkCalendarService.update(db, calendar_id, data)
    if entry is None:
        raise HTTPException(status_code=404, detail="日历条目不存在")
    return entry


@router.delete("/calendar/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar_entry(
    calendar_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    DELETE /attendance/calendar/{calendar_id} — 删除工作日历条目

    用途：
        删除指定工作日历条目，通常用于撤销错误录入的节假日/调休配置。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    路径参数 (Path)：
        - calendar_id: int   工作日历条目主键 ID

    响应：
        204 No Content — 删除成功，无响应体
        404 Not Found  — 条目不存在时返回 {"detail": "日历条目不存在"}

    对应 Service：
        WorkCalendarService.delete(db, calendar_id)
    """
    success = await WorkCalendarService.delete(db, calendar_id)
    if not success:
        raise HTTPException(status_code=404, detail="日历条目不存在")


# ===================================================================
# 月度汇总
# ===================================================================

@router.post("/summaries/generate", response_model=list[MonthSummaryOut])
async def generate_summaries(
    data: MonthSummaryGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /attendance/summaries/generate — 生成月度考勤汇总（HR/管理员触发）

    用途：
        触发月度考勤汇总的计算和写入。Service 会遍历指定月份内所有员工的
        考勤记录，统计出勤天数、迟到次数、早退次数、旷工天数、
        加班小时数等指标，生成或覆盖对应的 MonthSummary 记录。
        薪资模块在计算当月薪资时会读取此汇总数据。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。
        普通员工和经理无权触发此操作。

    请求体 (Body)：
        MonthSummaryGenerateRequest，主要字段：
            - year: int                    汇总年份
            - month: int                   汇总月份（1-12）
            - employee_ids: list[int] | None  指定员工 ID 列表（不传则处理全员）

    响应：
        200 OK — list[MonthSummaryOut]，生成/更新的月度汇总记录列表，
                  每条记录包含：
                   - employee_id: int           员工 ID
                   - year/month: int            汇总年月
                   - actual_work_days: float    实际出勤天数
                   - late_count: int            迟到次数
                   - early_leave_count: int     早退次数
                   - absent_days: float         旷工天数
                   - overtime_hours: float      加班小时数
                   - status: str                汇总状态（draft/confirmed/locked）
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        MonthSummaryService.generate(db, data)
    """
    return await MonthSummaryService.generate(db, data)


@router.get("/summaries", response_model=PaginatedResponse)
async def list_summaries(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /attendance/summaries — 分页查询月度汇总列表

    用途：
        查询指定年月的全员考勤月度汇总记录，按分页返回。
        HR 在月底关账前用此接口核查全员考勤汇总，也可供薪资模块批量读取。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    查询参数 (Query)：
        - year: int       汇总年份（必填，2020-2100）
        - month: int      汇总月份（必填，1-12）
        - page: int       页码，从 1 开始，默认 1
        - page_size: int  每页条数，默认 20，最大 1000

    响应：
        200 OK — PaginatedResponse，包含：
                   - total: int                 该月汇总总记录数
                   - page: int                  当前页码
                   - page_size: int             每页条数
                   - items: list[MonthSummaryOut]  当页月度汇总列表

    对应 Service：
        MonthSummaryService.list_summaries(db, year, month, page, page_size)
    """
    summaries, total = await MonthSummaryService.list_summaries(
        db, year, month, page, page_size
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[MonthSummaryOut.model_validate(s) for s in summaries],
    )


@router.get("/summaries/export")
async def export_summaries(
    year: int = Query(..., ge=2020, le=2100, description="年份"),
    month: int = Query(..., ge=1, le=12, description="月份"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /attendance/summaries/export — 导出月度汇总数据

    用途：
        导出指定年月的全员月度考勤汇总数据，当前实现暂时返回 JSON 格式，
        后续规划实现 Excel/CSV 格式下载。
        与 GET /summaries 的区别：此接口不分页，返回该月所有汇总记录（最多 9999 条）。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    查询参数 (Query)：
        - year: int    汇总年份（必填，2020-2100）
        - month: int   汇总月份（必填，1-12）

    响应（当前为 JSON）：
        200 OK — dict，包含：
                   - year: int                     汇总年份
                   - month: int                    汇总月份
                   - total: int                    总记录数
                   - items: list[dict]             所有月度汇总记录（MonthSummaryOut 序列化结果）

    注意：
        此路由必须在 GET /summaries/{employee_id} 之前注册，避免路径冲突
        （"export" 会被识别为 employee_id 参数）。FastAPI 路由顺序敏感。

    对应 Service：
        MonthSummaryService.list_summaries(db, year, month, page=1, page_size=9999)
    """
    # 获取该月所有汇总数据 (不分页)
    summaries, total = await MonthSummaryService.list_summaries(
        db, year, month, page=1, page_size=9999
    )
    return {
        "year": year,
        "month": month,
        "total": total,
        "items": [MonthSummaryOut.model_validate(s).model_dump() for s in summaries],
    }


@router.get("/summaries/{employee_id}", response_model=MonthSummaryOut)
async def get_summary(
    employee_id: int,
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /attendance/summaries/{employee_id} — 获取员工月度考勤汇总

    用途：
        查询指定员工在指定年月的考勤月度汇总记录（单条）。
        员工自查或 HR 查看某员工的月度汇总时调用。
        薪资模块计算个人薪资时也通过此接口（或直接调用 Service）获取考勤数据。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    路径参数 (Path)：
        - employee_id: int   员工主键 ID

    查询参数 (Query)：
        - year: int    汇总年份（必填，2020-2100）
        - month: int   汇总月份（必填，1-12）

    响应：
        200 OK  — MonthSummaryOut，该员工指定月份的考勤汇总详情
        404 Not Found — 汇总记录不存在（未生成）时返回 {"detail": "汇总数据不存在"}

    对应 Service：
        MonthSummaryService.get_summary(db, employee_id, year, month)
    """
    can_manage_all = await AttendanceRecordService._has_any_role(
        db,
        current_user,
        ("admin", "hr", "manager"),
    )
    if not can_manage_all and employee_id != current_user.id:
        raise HTTPException(status_code=403, detail="仅可查看自己的汇总数据")

    summary = await MonthSummaryService.get_summary(db, employee_id, year, month)
    if summary is None:
        await MonthSummaryService.generate(
            db,
            MonthSummaryGenerateRequest(
                year=year,
                month=month,
                employee_ids=[employee_id],
            ),
        )
        summary = await MonthSummaryService.get_summary(db, employee_id, year, month)
    if summary is None:
        raise HTTPException(status_code=404, detail="汇总数据不存在")
    return summary


@router.post("/summaries/confirm", response_model=MonthSummaryOut)
async def confirm_summary(
    data: MonthSummaryConfirm,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /attendance/summaries/confirm — 员工确认月度考勤汇总

    用途：
        员工对自己的月度考勤汇总进行确认，确认后 status 从 "draft" 变为 "confirmed"。
        HR 在锁定月度汇总前通常要求所有员工完成确认操作。
        Service 内部会校验 summary_id 对应的员工是否为当前登录用户，防止越权操作。

    权限：
        需要登录（Depends(get_current_user)），员工本人确认自己的汇总。

    请求体 (Body)：
        MonthSummaryConfirm，主要字段：
            - summary_id: int   需要确认的月度汇总记录 ID

    响应：
        200 OK  — MonthSummaryOut，确认后的月度汇总（status 已变为 "confirmed"）
        404 Not Found — 汇总记录不存在或当前用户无权操作（非本人汇总）时返回
                        {"detail": "汇总数据不存在或无权操作"}

    对应 Service：
        MonthSummaryService.employee_confirm(db, data.summary_id, current_user.id)
    """
    summary = await MonthSummaryService.employee_confirm(db, data.summary_id, current_user.id)
    if summary is None:
        raise HTTPException(status_code=404, detail="汇总数据不存在或无权操作")
    return summary


@router.post("/summaries/lock")
async def lock_summaries(
    data: MonthSummaryLock,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /attendance/summaries/lock — 锁定月度考勤汇总（HR/管理员操作）

    用途：
        将指定年月的所有月度汇总记录状态从 "confirmed"（或 "draft"）批量变更为 "locked"。
        锁定后数据不可再修改，作为薪资计算模块的最终输入来源。
        通常在月末关账时由 HR 执行此操作。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。
        普通员工和经理无权执行锁定操作。

    请求体 (Body)：
        MonthSummaryLock，主要字段：
            - year: int    锁定年份
            - month: int   锁定月份（1-12）

    响应：
        200 OK — dict，包含：
                   - message: str       操作结果描述（如"已锁定 50 条汇总记录"）
                   - locked_count: int  实际锁定的记录条数
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        MonthSummaryService.lock_month(db, data.year, data.month)
    """
    count = await MonthSummaryService.lock_month(db, data.year, data.month)
    return {"message": f"已锁定 {count} 条汇总记录", "locked_count": count}


# ===================================================================
# 考勤月报导出
# ===================================================================

@router.get("/export-monthly")
async def export_monthly_report(
    year: int = Query(..., ge=2020, le=2100, description="年份"),
    month: int = Query(..., ge=1, le=12, description="月份"),
    department_id: Optional[int] = Query(None, description="部门ID（为空则导出全员）"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    GET /attendance/export-monthly — 导出考勤月报 Excel 文件

    用途：
        生成并下载指定年月的考勤月报，以 Excel（.xlsx）文件格式返回。
        支持按部门过滤，不传 department_id 则导出全员报表。
        响应使用 StreamingResponse 流式输出，避免大文件内存溢出。
        文件名格式为 attendance_YYYYMM.xlsx（如 attendance_202503.xlsx）。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。
        建议前端限制为 HR/admin 才显示导出按钮。

    查询参数 (Query)：
        - year: int                 报表年份（必填，2020-2100）
        - month: int                报表月份（必填，1-12）
        - department_id: int | None 部门 ID（不传则导出全员）

    响应：
        200 OK — Excel 文件流（application/vnd.openxmlformats-officedocument.spreadsheetml.sheet）
                  HTTP Header 含：
                    Content-Disposition: attachment; filename=attendance_YYYYMM.xlsx

    注意：
        Excel 数据由 AttendanceReportService.export_monthly_report 生成，
        返回 bytes 格式，通过 io.BytesIO 包装后以 StreamingResponse 输出。

    对应 Service：
        AttendanceReportService.export_monthly_report(db, year, month, department_id)
        返回值：bytes（Excel 文件内容）
    """
    import io

    data = await AttendanceReportService.export_monthly_report(db, year, month, department_id)
    filename = f"attendance_{year}{month:02d}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
