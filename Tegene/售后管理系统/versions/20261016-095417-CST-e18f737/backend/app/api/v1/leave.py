"""
假期管理模块 API 路由

本模块提供售后管理系统的假期相关 RESTful 接口，涵盖以下功能域：

1. 假期类型管理 (Leave Types)
   - 定义假期分类（年假/病假/事假/调休/婚假/产假等）
   - 配置每种假期的规则：计量单位（天/小时）、是否需审批、
     最大可用天数、是否可跨年结转等

2. 假期余额管理 (Leave Balances)
   - 查询员工各类假期的剩余余额（含已使用/总量）
   - 批量初始化年度假期余额（年初批量操作）
   - 按工龄自动计算并初始化年假
   - HR 手动调整余额（增加/减少）
   - 处理调休假期过期逻辑
   - HR 聚合视图（所有员工所有假期类型汇总一览）

3. 请假日历 (Leave Calendar)
   - 按年月展示请假分布，支持按部门过滤

4. 请假申请流程 (Leave Requests)
   - 员工提交请假申请，自动校验余额和日期合法性
   - 分页查询（员工自查 / HR 管理视角）
   - 查询待审批列表（经理/HR 专用）
   - 审批（通过/拒绝）、撤销请假申请
   - 审批通过时自动扣减假期余额；撤销时自动归还余额

架构说明：
- 路由层（本文件）只负责参数接收、权限校验和 Schema 转换，不包含业务逻辑
- 业务逻辑全部封装在 app/services/leave.py 的各 Service 类中
- 权限控制通过 Depends(get_current_user) 和 Depends(require_roles(...)) 实现

相关 Service 类：
- LeaveTypeService      — 假期类型 CRUD
- LeaveBalanceService   — 余额查询、初始化、调整、年假计算、调休过期
- LeaveCalendarService  — 请假日历数据
- LeaveRequestService   — 请假申请的创建、查询、审批、撤销

假期类型 code 枚举（系统内置）：
- annual       年假（按工龄阶梯配置天数）
- sick         病假
- personal     事假
- comp_time    调休（加班补休）
- marriage     婚假
- maternity    产假
"""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.models.leave import ApprovalStatus, LeaveBalance, LeaveRequest, LeaveType
from app.models.organization import Department
from app.schemas.leave import (
    AnnualLeaveInitRequest,
    BalanceAdjustRequest,
    BulkInitBalanceRequest,
    BulkInitResult,
    LeaveBatchApprovalRequest,
    LeaveApprovalRequest,
    LeaveBalanceAdjust,
    LeaveBalanceOptionOut,
    LeaveBalanceOut,
    LeaveBalanceSummary,
    LeaveEmployeeDateUpdate,
    LeaveRequestCreate,
    LeaveRequestOut,
    LeaveRequestQuery,
    LeaveStatsOut,
    LeaveTypeCreate,
    LeaveTypeOut,
    LeaveTypeUpdate,
    PaginatedResponse,
)
from app.schemas.audit_log import AuditLogCreate
from app.services.leave import (
    LeaveBalanceService,
    LeaveCalendarService,
    LeaveIntegrationService,
    LeaveRequestService,
    LeaveTypeService,
)
from app.services.audit_log import AuditLogService

router = APIRouter(tags=["假期管理"])


def _to_cn_status(status_value: str) -> str:
    status_value = getattr(status_value, "value", status_value)
    mapping = {
        "pending": "待审批",
        "approved": "已通过",
        "rejected": "已驳回",
        "cancelled": "已撤销",
    }
    return mapping.get(str(status_value), str(status_value))


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))


def _decimal_to_float(value) -> float:
    return float(value or 0)


def _balance_amount_text(value: float, leave_type: LeaveType) -> str:
    unit = getattr(leave_type, "leave_unit", "day") or "day"
    hours_per_day = float(getattr(leave_type, "hours_per_day", 8) or 8)
    number = float(value or 0)
    if unit == "hour":
        amount = number * hours_per_day
        return f"{amount:g}小时"
    if unit == "half_day":
        return f"{number:g}天（半天粒度）"
    return f"{number:g}天"


def _balance_ratio_text(remaining_days: float, total_days: float, leave_type: LeaveType) -> str:
    unit = getattr(leave_type, "leave_unit", "day") or "day"
    hours_per_day = float(getattr(leave_type, "hours_per_day", 8) or 8)
    remaining = float(remaining_days or 0)
    total = float(total_days or 0)
    if total <= 0:
        return "无"
    if unit == "hour":
        return f"{remaining * hours_per_day:g}/{total * hours_per_day:g}小时"
    return f"{remaining:g}/{total:g}"


def _balance_display_text(
    remaining_days: float,
    total_days: float,
    leave_type: LeaveType,
    quota_limited: bool,
) -> str:
    if getattr(leave_type, "code", None) == "comp_time":
        return _balance_amount_text(remaining_days, leave_type) if total_days > 0 else "无"
    if not quota_limited:
        return "不限额"
    if getattr(leave_type, "code", None) == "annual":
        return _balance_ratio_text(remaining_days, total_days, leave_type)
    return _balance_amount_text(remaining_days, leave_type) if total_days > 0 else "无"


def _datetime_sort_key(value: datetime) -> float:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.timestamp()
    return value.replace(tzinfo=timezone.utc).timestamp()


def _latest_datetime(values: list[Optional[datetime]]) -> Optional[datetime]:
    actual_values = [value for value in values if value is not None]
    if not actual_values:
        return None
    return max(actual_values, key=_datetime_sort_key)


def _leave_balance_item(
    leave_type: LeaveType,
    employee: Employee,
    balance: Optional[LeaveBalance],
    year: int,
) -> dict:
    if not LeaveRequestService._employee_matches_scope(leave_type, employee):
        return {
            "leave_type_id": leave_type.id,
            "leave_type_name": leave_type.name,
            "leave_type_code": leave_type.code,
            "leave_unit": getattr(leave_type, "leave_unit", "day") or "day",
            "time_calc": getattr(leave_type, "time_calc", "natural_day") or "natural_day",
            "hours_per_day": _decimal_to_float(getattr(leave_type, "hours_per_day", 8)),
            "rounding_direction": getattr(leave_type, "rounding_direction", "none") or "none",
            "rounding_unit": getattr(leave_type, "rounding_unit", "none") or "none",
            "quota_limited": bool(getattr(leave_type, "quota_limited", True)),
            "quota_rule_type": getattr(leave_type, "quota_rule_type", None) or "fixed",
            "quota_rule": getattr(leave_type, "quota_rule", None) or {},
            "issuance_rule": getattr(leave_type, "issuance_rule", None) or "manual",
            "requires_proof": bool(getattr(leave_type, "requires_proof", False)),
            "sort_order": int(getattr(leave_type, "sort_order", 99) or 99),
            "validity_type": getattr(leave_type, "validity_type", None) or "natural_year",
            "expire_month_day": getattr(leave_type, "expire_month_day", None),
            "expiration_reminder_enabled": bool(getattr(leave_type, "expiration_reminder_enabled", False)),
            "reminder_before_value": int(getattr(leave_type, "reminder_before_value", 1) or 1),
            "reminder_before_unit": getattr(leave_type, "reminder_before_unit", "month") or "month",
            "reminder_notify_employee": bool(getattr(leave_type, "reminder_notify_employee", True)),
            "reminder_notify_manager": bool(getattr(leave_type, "reminder_notify_manager", False)),
            "expected_days": 0.0,
            "actual_days": 0.0,
            "used_days": 0.0,
            "remaining_days": 0.0,
            "expected_text": "不适用",
            "actual_text": "不适用",
            "used_text": "不适用",
            "remaining_text": "不适用",
            "balance_text": "不适用",
            "record_rule_text": f"{leave_type.name} | 规则：不适用当前员工",
            "is_effective": False,
            "effective_date": None,
            "not_effective_reason": "不适用当前员工",
            "updated_at": getattr(balance, "updated_at", None),
        }
    quota_limited = bool(getattr(leave_type, "quota_limited", True))
    reference_date = LeaveBalanceService._reference_date_for_year(year)
    effective_date = LeaveBalanceService._new_employee_effective_date(leave_type, employee)
    is_effective = LeaveBalanceService._is_new_employee_rule_effective(leave_type, employee, reference_date)
    if not is_effective:
        used_days = _decimal_to_float(getattr(balance, "used_days", 0)) if balance else 0.0
        return {
            "leave_type_id": leave_type.id,
            "leave_type_name": leave_type.name,
            "leave_type_code": leave_type.code,
            "leave_unit": getattr(leave_type, "leave_unit", "day") or "day",
            "time_calc": getattr(leave_type, "time_calc", "natural_day") or "natural_day",
            "hours_per_day": _decimal_to_float(getattr(leave_type, "hours_per_day", 8)),
            "rounding_direction": getattr(leave_type, "rounding_direction", "none") or "none",
            "rounding_unit": getattr(leave_type, "rounding_unit", "none") or "none",
            "quota_limited": quota_limited,
            "quota_rule_type": getattr(leave_type, "quota_rule_type", None) or "fixed",
            "quota_rule": getattr(leave_type, "quota_rule", None) or {},
            "issuance_rule": getattr(leave_type, "issuance_rule", None) or "manual",
            "requires_proof": bool(getattr(leave_type, "requires_proof", False)),
            "sort_order": int(getattr(leave_type, "sort_order", 99) or 99),
            "validity_type": getattr(leave_type, "validity_type", None) or "natural_year",
            "expire_month_day": getattr(leave_type, "expire_month_day", None),
            "expiration_reminder_enabled": bool(getattr(leave_type, "expiration_reminder_enabled", False)),
            "reminder_before_value": int(getattr(leave_type, "reminder_before_value", 1) or 1),
            "reminder_before_unit": getattr(leave_type, "reminder_before_unit", "month") or "month",
            "reminder_notify_employee": bool(getattr(leave_type, "reminder_notify_employee", True)),
            "reminder_notify_manager": bool(getattr(leave_type, "reminder_notify_manager", False)),
            "expected_days": 0.0,
            "actual_days": 0.0,
            "used_days": used_days,
            "remaining_days": 0.0,
            "expected_text": "无",
            "actual_text": "无",
            "used_text": _balance_amount_text(used_days, leave_type),
            "remaining_text": "无",
            "balance_text": "无",
            "record_rule_text": f"{leave_type.name} | 规则：未到生效日期",
            "is_effective": False,
            "effective_date": effective_date.isoformat() if effective_date else None,
            "not_effective_reason": "未到生效日期" if effective_date else "请先维护员工关键日期",
            "updated_at": getattr(balance, "updated_at", None),
        }
    expected_days = _decimal_to_float(
        LeaveBalanceService.calculate_quota_for_employee(leave_type, employee, year)
    )
    total_days = _decimal_to_float(getattr(balance, "total_days", 0)) if balance else 0.0
    used_days = _decimal_to_float(getattr(balance, "used_days", 0)) if balance else 0.0
    remaining_days = _decimal_to_float(getattr(balance, "remaining_days", 0)) if balance else 0.0

    if quota_limited:
        expected_text = _balance_amount_text(expected_days, leave_type) if expected_days else "无"
        actual_text = _balance_amount_text(total_days, leave_type) if balance else "无"
        remaining_text = _balance_amount_text(remaining_days, leave_type) if balance else "无"
    else:
        expected_text = "不限额"
        actual_text = "不限额"
        remaining_text = "不限额"
    balance_text = _balance_display_text(remaining_days, total_days, leave_type, quota_limited)

    used_text = _balance_amount_text(used_days, leave_type)
    rule = getattr(leave_type, "quota_rule", None) or {}
    validity_type = getattr(leave_type, "validity_type", None) or "natural_year"
    expire_month_day = getattr(leave_type, "expire_month_day", None)
    if validity_type == "from_issue_date":
        validity_text = f"自发放日起{int(rule.get('validity_years') or 1)}年后失效"
    elif validity_type == "fixed_date" and expire_month_day:
        validity_text = f"每年{expire_month_day.replace('-', '月')}日失效"
    elif validity_type == "forever":
        validity_text = "永久有效"
    elif validity_type == "quarter_end":
        validity_text = "每季度末失效"
    elif validity_type == "after_overtime_days":
        validity_text = f"加班{int(rule.get('overtime_valid_days') or rule.get('validity_days') or 30)}天后失效"
    else:
        validity_text = "每年12月31日失效"

    return {
        "leave_type_id": leave_type.id,
        "leave_type_name": leave_type.name,
        "leave_type_code": leave_type.code,
        "leave_unit": getattr(leave_type, "leave_unit", "day") or "day",
        "time_calc": getattr(leave_type, "time_calc", "natural_day") or "natural_day",
        "hours_per_day": _decimal_to_float(getattr(leave_type, "hours_per_day", 8)),
        "rounding_direction": getattr(leave_type, "rounding_direction", "none") or "none",
        "rounding_unit": getattr(leave_type, "rounding_unit", "none") or "none",
        "quota_limited": quota_limited,
        "quota_rule_type": getattr(leave_type, "quota_rule_type", None) or "fixed",
        "quota_rule": rule,
        "issuance_rule": getattr(leave_type, "issuance_rule", None) or "manual",
        "requires_proof": bool(getattr(leave_type, "requires_proof", False)),
        "sort_order": int(getattr(leave_type, "sort_order", 99) or 99),
        "validity_type": validity_type,
        "expire_month_day": expire_month_day,
        "expiration_reminder_enabled": bool(getattr(leave_type, "expiration_reminder_enabled", False)),
        "reminder_before_value": int(getattr(leave_type, "reminder_before_value", 1) or 1),
        "reminder_before_unit": getattr(leave_type, "reminder_before_unit", "month") or "month",
        "reminder_notify_employee": bool(getattr(leave_type, "reminder_notify_employee", True)),
        "reminder_notify_manager": bool(getattr(leave_type, "reminder_notify_manager", False)),
        "expected_days": expected_days,
        "actual_days": total_days,
        "used_days": used_days,
        "remaining_days": remaining_days,
        "expected_text": expected_text,
        "actual_text": actual_text,
        "used_text": used_text,
        "remaining_text": remaining_text,
        "balance_text": balance_text,
        "record_rule_text": f"{leave_type.name} | 规则：{remaining_text if quota_limited else '不限额'}，{validity_text}",
        "is_effective": True,
        "effective_date": effective_date.isoformat() if effective_date else None,
        "not_effective_reason": None,
        "updated_at": getattr(balance, "updated_at", None),
    }


def _balance_option_amount_text(value: float, item: dict) -> str:
    unit = item.get("leave_unit") or "day"
    hours_per_day = float(item.get("hours_per_day") or 8)
    number = float(value or 0)
    if unit == "hour":
        return f"{number * hours_per_day:g}小时"
    return f"{number:g}天"


def _leave_balance_option_item(item: dict) -> dict:
    name = str(item.get("leave_type_name") or "").strip()
    code = str(item.get("leave_type_code") or "").strip()
    quota_limited = bool(item.get("quota_limited", True))
    balance_text = str(item.get("balance_text") or "").strip()
    remaining_days = float(item.get("remaining_days") or 0)
    remaining_text = str(item.get("remaining_text") or "").strip()
    if remaining_text in {"", "无", "不适用"}:
        remaining_text = _balance_option_amount_text(remaining_days, item)

    is_effective = bool(item.get("is_effective", True))
    is_available = balance_text != "不适用" and is_effective
    option_label = name
    if code == "comp_time" or quota_limited:
        option_label = f"{name}(剩余{remaining_text})"

    return {
        "leave_type_id": int(item.get("leave_type_id") or 0),
        "leave_type_code": code or None,
        "leave_type_name": name,
        "leave_unit": item.get("leave_unit") or "day",
        "time_calc": item.get("time_calc") or "natural_day",
        "hours_per_day": float(item.get("hours_per_day") or 8),
        "rounding_direction": item.get("rounding_direction") or "none",
        "rounding_unit": item.get("rounding_unit") or "none",
        "quota_limited": quota_limited,
        "requires_proof": bool(item.get("requires_proof", False)),
        "total_days": float(item.get("actual_days") or 0),
        "used_days": float(item.get("used_days") or 0),
        "remaining_days": remaining_days,
        "remaining_text": remaining_text,
        "balance_text": balance_text or remaining_text,
        "option_label": option_label,
        "is_available": is_available,
        "sort_order": int(item.get("sort_order") or 99),
        "reason": None if is_available else str(item.get("not_effective_reason") or "不适用当前员工"),
    }


async def _sync_comp_time_balances_for_leave_views(
    db: AsyncSession,
    year: int,
    employee_ids: Optional[list[int]] = None,
) -> None:
    """让假期管理余额页与移动端首页使用同一套调休余额同步口径。"""
    from app.services.attendance import AttendanceRecordService

    await AttendanceRecordService.sync_comp_time_balances_from_attendance(
        db,
        year=year,
        employee_ids=employee_ids,
        allow_decrease=True,
        recalculate_records=True,
    )


async def _build_leave_balance_summaries(
    db: AsyncSession,
    year: int,
    department_id: Optional[int] = None,
    employment_type: Optional[str] = None,
    keyword: Optional[str] = None,
    employee_id: Optional[int] = None,
) -> list[LeaveBalanceSummary]:
    """从员工花名册实时聚合假期余额，确保员工管理变更能在假期余额页同步出现。"""
    await LeaveTypeService.ensure_default_wecom_types(db)
    await LeaveBalanceService.sync_rule_based_balances(
        db,
        year=year,
        employee_ids=[employee_id] if employee_id is not None else None,
    )
    await _sync_comp_time_balances_for_leave_views(
        db,
        year=year,
        employee_ids=[employee_id] if employee_id is not None else None,
    )

    type_result = await db.execute(
        select(LeaveType)
        .where(LeaveType.is_active == True)
        .order_by(LeaveType.sort_order.asc(), LeaveType.id.asc())
    )
    type_rows = list(type_result.scalars().all())
    type_map: dict[str, LeaveType] = {row.code: row for row in type_rows}
    comp_type = (
        type_map.get("comp_time")
        or type_map.get("comp")
        or next((row for row in type_rows if str(row.name or "").strip() == "调休"), None)
    )

    emp_query = select(Employee).where(Employee.is_active == True)
    if employee_id is not None:
        emp_query = emp_query.where(Employee.id == employee_id)
    if department_id is not None:
        emp_query = emp_query.where(Employee.department_id == department_id)
    if employment_type:
        emp_query = emp_query.where(Employee.employment_type == employment_type)
    if keyword:
        emp_query = emp_query.where(
            or_(
                Employee.name.ilike(f"%{keyword}%"),
                Employee.employee_no.ilike(f"%{keyword}%"),
            )
        )
    emp_query = emp_query.order_by(Employee.employee_no.asc(), Employee.id.asc())
    emp_result = await db.execute(emp_query)
    employees = list(emp_result.scalars().all())
    if not employees:
        return []

    emp_ids = [e.id for e in employees]
    dept_ids = [e.department_id for e in employees if e.department_id]
    dept_map: dict[int, str] = {}
    if dept_ids:
        dept_result = await db.execute(
            select(Department.id, Department.name).where(Department.id.in_(dept_ids))
        )
        dept_map = {d.id: d.name for d in dept_result.all()}

    bal_result = await db.execute(
        select(LeaveBalance).where(
            LeaveBalance.employee_id.in_(emp_ids),
            LeaveBalance.year == year,
        )
    )
    balance_rows = list(bal_result.scalars().all())
    from collections import defaultdict

    by_emp: dict[int, dict[int, LeaveBalance]] = defaultdict(dict)
    for balance in balance_rows:
        by_emp[balance.employee_id][balance.leave_type_id] = balance

    def _item_days(items: list[dict], code: str, field: str) -> float:
        item = next((row for row in items if row.get("leave_type_code") == code), None)
        if not item:
            return 0.0
        return float(item.get(field) or 0)

    summaries: list[LeaveBalanceSummary] = []
    for emp in employees:
        balance_map = by_emp.get(emp.id, {})
        leave_balance_items = [
            _leave_balance_item(leave_type, emp, balance_map.get(leave_type.id), year)
            for leave_type in type_rows
        ]
        latest_updated = _latest_datetime([item.get("updated_at") for item in leave_balance_items])

        comp_code = str(getattr(comp_type, "code", "comp_time") if comp_type else "comp_time")
        has_marriage = type_map.get("marriage") is not None
        has_maternity = type_map.get("maternity") is not None

        summaries.append(
            LeaveBalanceSummary(
                employee_id=emp.id,
                employee_name=emp.name,
                employee_no=emp.employee_no,
                gender=emp.gender,
                department_id=emp.department_id,
                department_name=dept_map.get(emp.department_id, "-"),
                hire_date=emp.hire_date,
                first_work_date=emp.first_work_date,
                probation_end_date=emp.probation_end_date,
                photo_url=emp.photo_url,
                status=emp.status,
                employment_type=emp.employment_type,
                company=emp.company,
                position=emp.position,
                job_level=emp.job_level,
                annual_total=_item_days(leave_balance_items, "annual", "actual_days"),
                annual_used=_item_days(leave_balance_items, "annual", "used_days"),
                annual_remaining=_item_days(leave_balance_items, "annual", "remaining_days"),
                sick_total=_item_days(leave_balance_items, "sick", "actual_days"),
                sick_used=_item_days(leave_balance_items, "sick", "used_days"),
                sick_remaining=_item_days(leave_balance_items, "sick", "remaining_days"),
                personal_total=_item_days(leave_balance_items, "personal", "actual_days"),
                personal_used=_item_days(leave_balance_items, "personal", "used_days"),
                personal_remaining=_item_days(leave_balance_items, "personal", "remaining_days"),
                comp_total=_item_days(leave_balance_items, comp_code, "actual_days"),
                comp_used=_item_days(leave_balance_items, comp_code, "used_days"),
                comp_remaining=_item_days(leave_balance_items, comp_code, "remaining_days"),
                marriage_total=_item_days(leave_balance_items, "marriage", "actual_days") if has_marriage else None,
                marriage_remaining=_item_days(leave_balance_items, "marriage", "remaining_days") if has_marriage else None,
                maternity_total=_item_days(leave_balance_items, "maternity", "actual_days") if has_maternity else None,
                maternity_remaining=_item_days(leave_balance_items, "maternity", "remaining_days") if has_maternity else None,
                updated_at=latest_updated,
                leave_balances=leave_balance_items,
            )
        )
    return summaries


def _reason_category(leave_type_name: str, reason: Optional[str]) -> str:
    text = (reason or "").strip()
    if "病假" in leave_type_name:
        if any(k in text for k in ["手术", "住院"]):
            return "手术"
        if "陪护" in text:
            return "陪护"
        return "感冒"
    return "其他"


async def _serialize_leave_requests(db: AsyncSession, rows: list[LeaveRequest]) -> list[dict]:
    if not rows:
        return []
    employee_ids = {r.employee_id for r in rows}
    approver_ids = {r.approver_id for r in rows if r.approver_id}
    type_ids = {r.leave_type_id for r in rows}

    emp_res = await db.execute(
        select(Employee.id, Employee.name, Employee.employee_no, Employee.department_id).where(
            Employee.id.in_(employee_ids.union(approver_ids))
        )
    )
    emp_rows = emp_res.all()
    emp_map = {e.id: e for e in emp_rows}

    dept_ids = {e.department_id for e in emp_rows if getattr(e, "department_id", None)}
    dept_map: dict[int, str] = {}
    if dept_ids:
        dept_res = await db.execute(
            select(Department.id, Department.name).where(Department.id.in_(dept_ids))
        )
        dept_map = {d.id: d.name for d in dept_res.all()}

    type_res = await db.execute(
        select(LeaveType.id, LeaveType.name, LeaveType.code, LeaveType.requires_proof).where(
            LeaveType.id.in_(type_ids)
        )
    )
    type_map = {t.id: t for t in type_res.all()}

    balance_keys = {(r.employee_id, r.leave_type_id, r.start_date.year) for r in rows}
    balance_map: dict[tuple[int, int, int], LeaveBalance] = {}
    if balance_keys:
        conditions = [
            and_(
                LeaveBalance.employee_id == employee_id,
                LeaveBalance.leave_type_id == leave_type_id,
                LeaveBalance.year == year,
            )
            for (employee_id, leave_type_id, year) in balance_keys
        ]
        bal_res = await db.execute(select(LeaveBalance).where(or_(*conditions)))
        for b in bal_res.scalars().all():
            balance_map[(b.employee_id, b.leave_type_id, b.year)] = b

    now = datetime.now(timezone.utc)
    items: list[dict] = []
    for r in rows:
        e = emp_map.get(r.employee_id)
        approver = emp_map.get(r.approver_id) if r.approver_id else None
        t = type_map.get(r.leave_type_id)
        dept_name = dept_map.get(getattr(e, "department_id", None), "-")
        balance = balance_map.get((r.employee_id, r.leave_type_id, r.start_date.year))
        total_days = float(balance.total_days) if balance and balance.total_days is not None else 0.0
        remaining_days = float(balance.remaining_days) if balance and balance.remaining_days is not None else 0.0
        requested_days = float(r.days or 0)
        status_value = _enum_value(r.approval_status)
        created_at = r.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        projected_remaining = remaining_days - requested_days if status_value == "pending" else remaining_days
        is_timeout = status_value == "pending" and (now - created_at) > timedelta(hours=24)
        leave_type_name = getattr(t, "name", "未知假期")
        items.append(
            {
                "id": r.id,
                "employee_id": r.employee_id,
                "applicant_name": getattr(e, "name", f"员工#{r.employee_id}"),
                "applicant_no": getattr(e, "employee_no", "-"),
                "department_name": dept_name,
                "leave_type": leave_type_name,
                "leave_type_code": getattr(t, "code", None),
                "request_time": created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                "start_date": str(r.start_date),
                "end_date": str(r.end_date),
                "days": requested_days,
                "reason": r.reason or "",
                "reason_category": _reason_category(leave_type_name, r.reason),
                "remaining_quota": max(round(projected_remaining, 1), 0.0),
                "total_quota": round(total_days, 1),
                "quota_text": f"{max(round(projected_remaining, 1), 0.0)}/{round(total_days, 1)}",
                "status": status_value,
                "status_text": _to_cn_status(status_value),
                "attachment": bool(r.proof_url),
                "attachment_url": r.proof_url,
                "requires_proof": bool(getattr(t, "requires_proof", False)),
                "approver_name": getattr(approver, "name", ""),
                "approval_node": "部门主管审批" if status_value == "pending" else "已完成",
                "is_timeout": is_timeout,
                "approved_at": r.approved_at.astimezone().strftime("%Y-%m-%d %H:%M") if r.approved_at else None,
                "created_at": created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                "start_half": _enum_value(r.start_half),
                "end_half": _enum_value(r.end_half),
            }
        )
    return items


# ===================================================================
# 假期类型
# ===================================================================

@router.post("/types", response_model=LeaveTypeOut, status_code=status.HTTP_201_CREATED)
async def create_leave_type(
    data: LeaveTypeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /leave/types — 创建假期类型

    用途：
        新增一种假期类型，如创建"丧假""陪产假"等企业自定义假期。
        假期类型定义了该假期的计量单位、最大可用天数、是否需要审批等规则，
        是假期余额和请假申请的基础配置。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。

    请求体 (Body)：
        LeaveTypeCreate，主要字段：
            - name: str                假期类型名称（如"年假"）
            - code: str                假期类型编码，唯一标识（如"annual"）
            - unit: str                计量单位：day（天）/ hour（小时）
            - max_days: float | None   年度最大可用天数（null 表示不限制）
            - requires_approval: bool  是否需要审批，默认 True
            - allow_carry_over: bool   是否允许跨年结转，默认 False
            - is_active: bool          是否启用，默认 True
            - description: str | None  假期说明（可选）

    响应：
        201 Created — LeaveTypeOut，新建的假期类型对象（含 id）

    对应 Service：
        LeaveTypeService.create(db, data)
    """
    leave_type = await LeaveTypeService.create(db, data)
    await LeaveBalanceService.sync_rule_based_balances(
        db, year=date.today().year, leave_type_ids=[leave_type.id]
    )
    return leave_type


@router.get("/types", response_model=list[LeaveTypeOut])
async def list_leave_types(
    is_active: Optional[bool] = Query(None, description="是否启用"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /leave/types — 获取假期类型列表

    用途：
        查询所有假期类型，支持按启用状态过滤。
        请假申请页面需要调用此接口来填充假期类型下拉选项。
        HR 管理假期配置时也通过此接口获取类型列表。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    查询参数 (Query)：
        - is_active: bool | None   按启用状态过滤：
                                     True = 仅返回启用的假期类型（员工申请时使用）
                                     False = 仅返回禁用的
                                     不传 = 返回所有类型

    响应：
        200 OK — list[LeaveTypeOut]，假期类型列表（无分页）

    对应 Service：
        LeaveTypeService.list_types(db, is_active)
    """
    return await LeaveTypeService.list_types(db, is_active)


@router.get("/types/{type_id}", response_model=LeaveTypeOut)
async def get_leave_type(
    type_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /leave/types/{type_id} — 获取假期类型详情

    用途：
        按主键 ID 查询单个假期类型的完整配置信息。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    路径参数 (Path)：
        - type_id: int   假期类型主键 ID

    响应：
        200 OK  — LeaveTypeOut，假期类型详情
        404 Not Found — 类型不存在时返回 {"detail": "假期类型不存在"}

    对应 Service：
        LeaveTypeService.get(db, type_id)
    """
    leave_type = await LeaveTypeService.get(db, type_id)
    if leave_type is None:
        raise HTTPException(status_code=404, detail="假期类型不存在")
    return leave_type


@router.put("/types/{type_id}", response_model=LeaveTypeOut)
async def update_leave_type(
    type_id: int,
    data: LeaveTypeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /leave/types/{type_id} — 更新假期类型

    用途：
        修改指定假期类型的配置，如调整最大天数、更改是否需要审批等。
        使用 Pydantic v2 partial update（只传需要修改的字段即可）。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。

    路径参数 (Path)：
        - type_id: int   假期类型主键 ID

    请求体 (Body)：
        LeaveTypeUpdate — 假期类型更新 Schema（所有字段可选）

    响应：
        200 OK  — LeaveTypeOut，更新后的假期类型对象
        404 Not Found — 类型不存在时返回 {"detail": "假期类型不存在"}

    对应 Service：
        LeaveTypeService.update(db, type_id, data)
    """
    leave_type = await LeaveTypeService.update(db, type_id, data)
    if leave_type is None:
        raise HTTPException(status_code=404, detail="假期类型不存在")
    await LeaveBalanceService.sync_rule_based_balances(
        db, year=date.today().year, leave_type_ids=[leave_type.id]
    )
    return leave_type


@router.delete("/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_leave_type(
    type_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /leave/types/{type_id} — 删除假期类型

    用途：
        永久删除指定假期类型。删除前应确认该类型没有关联的有效余额记录或请假申请。
        建议使用禁用（is_active=False）代替删除，以保留历史数据完整性。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。

    路径参数 (Path)：
        - type_id: int   假期类型主键 ID

    响应：
        204 No Content — 删除成功，无响应体
        404 Not Found  — 类型不存在时返回 {"detail": "假期类型不存在"}

    对应 Service：
        LeaveTypeService.delete(db, type_id)
    """
    success = await LeaveTypeService.delete(db, type_id)
    if not success:
        raise HTTPException(status_code=404, detail="假期类型不存在")


# ===================================================================
# 假期余额
# ===================================================================

@router.post("/balances/bulk-init", response_model=BulkInitResult)
async def bulk_init_balances(
    data: BulkInitBalanceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /leave/balances/bulk-init — 批量初始化员工假期余额（HR/管理员专用）

    用途：
        年初批量为员工创建指定年度的各类假期余额记录。
        Service 会遍历所有激活的假期类型，按类型配置（max_days等）
        为每位员工初始化对应的 LeaveBalance 行。
        支持 reset_existing 参数控制是否覆盖已有余额（用于纠错场景）。
        通常在每年1月初由 HR 执行一次。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。
        普通员工无权调用此接口。

    请求体 (Body)：
        BulkInitBalanceRequest，主要字段：
            - year: int                      初始化的年度（如 2025）
            - employee_ids: list[int] | None  指定员工 ID 列表（不传则处理全员在职员工）
            - reset_existing: bool            是否重置已有余额记录，默认 False
                                              （True 时会清空已用天数重新初始化）

    响应：
        200 OK — BulkInitResult，包含：
                   - created_count: int    新建的余额记录数
                   - skipped_count: int    因已存在而跳过的记录数（reset_existing=False 时）
                   - updated_count: int    重置更新的记录数（reset_existing=True 时）
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        LeaveBalanceService.bulk_init_balances(db, year, employee_ids, reset_existing)
    """
    result = await LeaveBalanceService.bulk_init_balances(
        db,
        year=data.year,
        employee_ids=data.employee_ids,
        reset_existing=data.reset_existing,
    )
    return BulkInitResult(**result)


@router.post("/balances/adjust-v2", response_model=LeaveBalanceOut)
async def adjust_balance_v2(
    data: BalanceAdjustRequest,
    year: int = Query(..., ge=2020, le=2100, description="年度"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /leave/balances/adjust-v2 — 手动调整员工假期余额（新版，HR/管理员专用）

    用途：
        HR 手动增加或减少某员工某类假期的年度余额，适用于特殊补偿、
        行政调整、数据纠错等场景。
        正数表示增加余额，负数表示减少余额。
        与 POST /balances/adjust（旧版）的区别：此接口通过 query 参数传递年份，
        Body 中包含调整原因，便于审计追踪。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。

    查询参数 (Query)：
        - year: int   调整的年度（必填，2020-2100）

    请求体 (Body)：
        BalanceAdjustRequest，主要字段：
            - employee_id: int      目标员工 ID
            - leave_type_id: int    假期类型 ID
            - adjustment: float     调整天数（正数增加，负数减少）
            - reason: str           调整原因（用于审计日志）

    响应：
        200 OK — LeaveBalanceOut，调整后的假期余额记录：
                   - total_days: float       调整后总天数
                   - used_days: float        已使用天数（不变）
                   - remaining_days: float   调整后剩余天数
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        LeaveBalanceService.adjust_balance(db, employee_id, leave_type_id, adjustment, reason, year)
    """
    balance = await LeaveBalanceService.adjust_balance(
        db,
        employee_id=data.employee_id,
        leave_type_id=data.leave_type_id,
        adjustment=data.adjustment,
        reason=data.reason,
        year=year,
    )
    try:
        await AuditLogService.create(
            db,
            AuditLogCreate(
                operator_id=current_user.id,
                operator_name=current_user.name,
                module="leave",
                action="balance_adjust",
                resource_id=balance.id,
                resource_type="LeaveBalance",
                before_data=None,
                after_data=json.dumps(
                    {
                        "employee_id": data.employee_id,
                        "leave_type_id": data.leave_type_id,
                        "year": year,
                        "adjustment": data.adjustment,
                        "reason": data.reason,
                        "remaining_days": float(balance.remaining_days or 0),
                        "total_days": float(balance.total_days or 0),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    except Exception:
        # 审计日志失败不阻断业务
        pass
    return balance


@router.get("/calendar")
async def get_leave_calendar(
    year: int = Query(..., ge=2020, le=2100, description="年份"),
    month: int = Query(..., ge=1, le=12, description="月份"),
    department_id: Optional[int] = Query(None, description="部门ID，不传则全部门"),
    leave_type_id: Optional[int] = Query(None, description="假期类型ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /leave/calendar — 获取请假日历数据（按日期组织）

    用途：
        返回指定年月的请假分布数据，用于前端日历视图展示哪些员工在哪些天请假。
        数据按日期聚合，每天包含当日请假员工列表及假期类型信息。
        支持按部门过滤，便于部门管理者查看本部门的请假情况。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。

    查询参数 (Query)：
        - year: int                  日历年份（必填，2020-2100）
        - month: int                 日历月份（必填，1-12）
        - department_id: int | None  按部门过滤（不传则返回全公司数据）

    响应：
        200 OK — dict，由 LeaveCalendarService.get_calendar_data 决定，
                 典型结构为按日期键的字典：
                   {
                     "2025-03-10": [
                       {"employee_id": 1, "employee_name": "张三", "leave_type": "年假"},
                       ...
                     ],
                     ...
                   }

    对应 Service：
        LeaveCalendarService.get_calendar_data(db, year, month, department_id)
    """
    data = await LeaveCalendarService.get_calendar_data(
        db, year, month, department_id, leave_type_id
    )
    return data


@router.get("/integrations/attendance-approved")
async def approved_leave_for_attendance(
    start_date: date = Query(..., description="查询开始日期"),
    end_date: date = Query(..., description="查询结束日期"),
    employee_id: Optional[int] = Query(None, description="员工ID，不传返回范围内全部员工"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    考勤/月报预留接口：返回指定日期范围内审批通过的请假片段。

    下游考勤汇总、打卡异常判定和薪资计薪均应读取这份统一契约，而不是各自
    重复解释 LeaveRequest。返回字段已包含 on_leave 状态、半天标记、带薪属性、
    计时口径和 1 天折算小时数。
    """
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    return {
        "items": await LeaveIntegrationService.approved_leave_segments(
            db, start_date=start_date, end_date=end_date, employee_id=employee_id
        )
    }


@router.get("/integrations/punch-guard")
async def leave_punch_guard(
    target_date: date = Query(..., description="打卡日期"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    打卡预留接口：移动端打卡前可查询当天是否已有已批准请假覆盖。

    当前只返回当前登录员工的覆盖情况，避免员工窥探他人请假。后续打卡模块可用
    should_require_punch 决定是否提示仍需打卡（如半天假另一半仍需正常打卡）。
    """
    segments = await LeaveIntegrationService.approved_leave_segments(
        db, start_date=target_date, end_date=target_date, employee_id=current_user.id
    )
    full_day = any(item["half_day_text"] == "全天" for item in segments)
    return {
        "date": target_date.isoformat(),
        "employee_id": current_user.id,
        "covered": bool(segments),
        "should_require_punch": not full_day,
        "segments": segments,
    }


@router.get("/integrations/approval-context/{request_id}")
async def leave_approval_context(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    审批预留接口：为通用审批引擎提供请假业务摘要、表单快照和后续回写钩子。
    """
    context = await LeaveIntegrationService.approval_context(db, request_id)
    if context is None:
        raise HTTPException(status_code=404, detail="请假申请不存在")
    return context


@router.get("/balances", response_model=list[LeaveBalanceSummary], summary="获取所有员工假期余额（HR聚合视图）")
async def list_all_balances(
    year: int = Query(..., ge=2020, le=2100, description="年度"),
    department_id: Optional[int] = Query(None, description="部门ID"),
    employment_type: Optional[str] = Query(None, description="员工类型"),
    keyword: Optional[str] = Query(None, description="员工姓名/工号搜索"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /leave/balances — HR 聚合视图：所有员工假期余额汇总

    用途：
        一次性返回所有在职员工在指定年度的假期余额汇总，每行包含年假、病假、
        事假、调休、婚假、产假六类假期的总量/已用/剩余数据。
        专为 HR 假期管理页面设计，支持按员工姓名或工号关键字搜索。

        实现细节（避免 N+1 查询）：
        1. 一次性取所有假期类型的 code→id 映射
        2. 一次性取满足条件的所有在职员工
        3. 一次性取所有相关余额记录（按 employee_id + year 过滤）
        4. 在内存中按员工聚合成 LeaveBalanceSummary 对象

    权限：
        需要登录（Depends(get_current_user)），无角色限制。
        此接口返回全员数据，建议前端限制为 HR/admin 才可访问。

    注意：
        此路由必须在 GET /balances/me 和 GET /balances/{employee_id} 之前注册，
        否则 "me" 会被识别为 employee_id 参数。FastAPI 路由顺序敏感。

    查询参数 (Query)：
        - year: int              查询年度（必填，2020-2100）
        - keyword: str | None    搜索关键字：匹配员工姓名（模糊）或工号（模糊）

    响应：
        200 OK — list[LeaveBalanceSummary]，员工假期余额聚合列表，每条包含：
                   - employee_id: int               员工 ID
                   - employee_name: str             员工姓名
                   - employee_no: str               员工工号
                   - annual_total: float            年假总天数
                   - annual_used: float             年假已用天数
                   - annual_remaining: float        年假剩余天数
                   - sick_total: float              病假总天数
                   - sick_used: float               病假已用天数
                   - sick_remaining: float          病假剩余天数
                   - personal_used: float           事假已用天数
                   - comp_used: float               调休已用天数
                   - marriage_remaining: float|None 婚假剩余天数（无配置则为 None）
                   - maternity_remaining: float|None 产假剩余天数（无配置则为 None）

    对应 Service：
        本端点逻辑内联在路由函数中（查询 + 聚合），未抽出独立 Service 方法。
        读取：LeaveBalance 表 + Employee 表 + LeaveType 表
    """
    return await _build_leave_balance_summaries(
        db,
        year=year,
        department_id=department_id,
        employment_type=employment_type,
        keyword=keyword,
    )


@router.put(
    "/balances/employee-dates/{employee_id}",
    response_model=LeaveBalanceSummary,
    summary="在假期余额页补录员工入职/首次工作日期",
)
async def update_leave_employee_dates(
    employee_id: int,
    data: LeaveEmployeeDateUpdate,
    year: int = Query(..., ge=2020, le=2100, description="返回该年度余额"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    employee = await db.get(Employee, employee_id)
    if employee is None or not employee.is_active:
        raise HTTPException(status_code=404, detail="员工不存在")
    if data.hire_date is not None:
        employee.hire_date = data.hire_date
    if data.first_work_date is not None:
        employee.first_work_date = data.first_work_date
    if data.probation_end_date is not None:
        employee.probation_end_date = data.probation_end_date
    await db.flush()
    rows = await _build_leave_balance_summaries(db, year=year, employee_id=employee_id)
    if not rows:
        raise HTTPException(status_code=404, detail="员工不存在")
    return rows[0]


@router.get("/balances/me/options", response_model=list[LeaveBalanceOptionOut], summary="获取当前员工可选假期类型及余额")
async def my_leave_balance_options(
    year: int = Query(..., ge=2020, le=2100, description="年度"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """移动端请假审批发起页使用：返回当前员工可选择的假期类型及余额展示文案。"""
    rows = await _build_leave_balance_summaries(db, year=year, employee_id=current_user.id)
    if not rows:
        return []
    items = [_leave_balance_option_item(item) for item in rows[0].leave_balances]
    return [item for item in items if item["is_available"]]


@router.get("/balances/me", response_model=list[LeaveBalanceOut])
async def my_balances(
    year: int = Query(..., ge=2020, le=2100, description="年度"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /leave/balances/me — 查询当前登录用户的假期余额

    用途：
        员工自查本人当年各类假期的余额（总量/已用/剩余），
        是员工自助服务（ESS）请假申请页面的常用接口。
        使用 current_user.id 作为员工 ID，无需在路径或参数中传递，安全可靠。

    权限：
        需要登录（Depends(get_current_user)），员工查询自己的余额，无角色限制。

    注意：
        此路由必须在 GET /balances/{employee_id} 之前注册，
        否则路径中的 "me" 会被识别为 employee_id 参数（路由冲突）。

    查询参数 (Query)：
        - year: int   查询年度（必填，2020-2100）

    响应：
        200 OK — list[LeaveBalanceOut]，当前用户所有假期类型的余额记录列表，
                  每条包含：
                   - leave_type_id: int      假期类型 ID
                   - leave_type_name: str    假期类型名称
                   - total_days: float       年度总天数
                   - used_days: float        已使用天数
                   - remaining_days: float   剩余天数

    对应 Service：
        LeaveBalanceService.list_balances(db, current_user.id, year)
    """
    await _sync_comp_time_balances_for_leave_views(db, year=year, employee_ids=[current_user.id])
    return await LeaveBalanceService.list_balances(db, current_user.id, year)


@router.get("/balances/{employee_id}", response_model=list[LeaveBalanceOut])
async def get_employee_balances(
    employee_id: int,
    year: int = Query(..., ge=2020, le=2100, description="年度"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /leave/balances/{employee_id} — 查询指定员工的假期余额

    用途：
        HR 或管理员查询某位员工在指定年度的各类假期余额详情。
        员工也可以查询自己的余额（等同于 GET /balances/me，但需要传 employee_id）。

    权限：
        需要登录（Depends(get_current_user)），无额外角色限制。
        建议业务层或前端做权限校验，防止员工查询他人数据。

    路径参数 (Path)：
        - employee_id: int   目标员工主键 ID

    查询参数 (Query)：
        - year: int   查询年度（必填，2020-2100）

    响应：
        200 OK — list[LeaveBalanceOut]，该员工所有假期类型的余额记录列表
                  （结构同 GET /balances/me 响应）

    对应 Service：
        LeaveBalanceService.list_balances(db, employee_id, year)
    """
    await _sync_comp_time_balances_for_leave_views(db, year=year, employee_ids=[employee_id])
    return await LeaveBalanceService.list_balances(db, employee_id, year)


@router.post("/balances/adjust", response_model=LeaveBalanceOut)
async def adjust_balance(
    data: LeaveBalanceAdjust,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /leave/balances/adjust — 手动调整假期余额（旧版，HR/管理员专用）

    用途：
        HR 手动调整员工假期余额，适用于行政补偿、数据纠错等场景。
        与新版 POST /balances/adjust-v2 的区别：
          - 此接口年份在 Body 中指定，是旧版接口
          - adjust-v2 通过 Query 参数传年份，Body 包含调整原因
        两个接口功能基本等同，建议新代码使用 adjust-v2。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。

    请求体 (Body)：
        LeaveBalanceAdjust，主要字段：
            - employee_id: int      目标员工 ID
            - leave_type_id: int    假期类型 ID
            - year: int             调整年度
            - adjustment: float     调整天数（正数增加，负数减少）
            - reason: str | None    调整原因

    响应：
        200 OK — LeaveBalanceOut，调整后的假期余额记录
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        LeaveBalanceService.adjust(db, data)
    """
    return await LeaveBalanceService.adjust(db, data)


@router.post("/balances/init-annual")
async def init_annual_leave(
    data: AnnualLeaveInitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /leave/balances/init-annual — 按工龄自动计算并初始化年假（HR/管理员专用）

    用途：
        根据员工最初工作时间（first_work_date，缺失时回退 hire_date）自动计算工龄，按法定年假阶梯规则
        （工龄<1年=0天、1-10年=5天、10-20年=10天、20年以上=15天）
        初始化员工的年假余额。
        内部逻辑：先从 Employee 表批量查询 first_work_date/hire_date，再调用 LeaveBalanceService
        计算并写入 LeaveBalance 记录。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。

    请求体 (Body)：
        AnnualLeaveInitRequest，主要字段：
            - year: int                      初始化的年度
            - employee_ids: list[int] | None  指定员工 ID 列表（不传则处理全员在职员工）

    响应：
        200 OK — dict，包含：
                   - message: str   操作结果描述（如"已初始化 50 名员工的年假"）
                   - count: int     实际初始化的员工数量
        400 Bad Request — 未找到符合条件的员工（无有效 first_work_date/hire_date）时返回
                          {"detail": "未找到符合条件的员工"}
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        LeaveBalanceService.init_annual_leave(db, year, employee_records)
        employee_records 格式：[{"employee_id": int, "first_work_date": date, "hire_date": date}, ...]
    """
    # 在实际场景中, 这里应从 Employee 表批量查询 first_work_date/hire_date
    # 此处提供接口框架, 具体员工数据获取逻辑依赖 Employee 模型
    from sqlalchemy import select as sa_select
    from app.models.employee import Employee as EmpModel

    stmt = sa_select(EmpModel.id, EmpModel.hire_date, EmpModel.first_work_date)
    if data.employee_ids:
        stmt = stmt.where(EmpModel.id.in_(data.employee_ids))
    result = await db.execute(stmt)
    rows = result.all()

    employee_records = [
        {"employee_id": row[0], "hire_date": row[1], "first_work_date": row[2]}
        for row in rows
        if row[1] is not None or row[2] is not None
    ]

    if not employee_records:
        raise HTTPException(status_code=400, detail="未找到符合条件的员工")

    balances = await LeaveBalanceService.init_annual_leave(db, data.year, employee_records)
    return {
        "message": f"已初始化 {len(balances)} 名员工的年假",
        "count": len(balances),
    }


@router.post("/balances/expire-comp-time")
async def expire_comp_time(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /leave/balances/expire-comp-time — 处理调休假期过期（HR/管理员专用）

    用途：
        扫描并处理已过期的调休（comp_time）余额记录。
        调休假期通常有有效期限制（如加班后 3 个月内必须使用），
        超期未使用的调休余额需要自动清零或标记过期。
        此接口供定时任务或 HR 手动触发，通常配置为每月或每周定期执行。

    权限：
        需要 admin 或 hr 角色（Depends(require_roles("admin", "hr"))）。

    请求参数：
        无请求体，无查询参数。

    响应：
        200 OK — dict，包含：
                   - message: str        操作结果描述（如"已处理 3 条过期调休"）
                   - expired_count: int  实际处理的过期记录数
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        LeaveBalanceService.expire_comp_time(db)
    """
    count = await LeaveBalanceService.expire_comp_time(db)
    return {"message": f"已处理 {count} 条过期调休", "expired_count": count}


# ===================================================================
# 请假申请
# ===================================================================

@router.post("/requests", response_model=LeaveRequestOut, status_code=status.HTTP_201_CREATED)
async def create_leave_request(
    data: LeaveRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /leave/requests — 员工提交请假申请

    用途：
        员工发起请假申请，Service 内部会自动：
        1. 校验请假日期的合法性（开始日期不早于今天、结束日期不早于开始日期）
        2. 计算请假天数（考虑工作日历，排除节假日和周末）
        3. 验证员工对应假期类型的余额是否充足
        4. 创建 LeaveRequest 记录，初始状态为 "pending"（待审批）
        申请成功后通常会触发审批流通知（通过审批流模块或消息推送）。

    权限：
        需要登录（Depends(get_current_user)），员工本人提交，使用 current_user.id。

    请求体 (Body)：
        LeaveRequestCreate，主要字段：
            - leave_type_id: int     假期类型 ID
            - start_date: date       请假开始日期
            - end_date: date         请假结束日期
            - half_day: bool         是否半天假，默认 False
            - reason: str            请假原因说明
            - proxy_employee_id: int | None  代理人员工 ID（可选）

    响应：
        201 Created — LeaveRequestOut，新建的请假申请：
                       - id: int                 申请 ID
                       - status: str             初始状态 "pending"
                       - days: float             请假天数（计算后）
                       - employee_id: int        申请员工 ID
        400 Bad Request — 余额不足、日期不合法等业务校验失败时，
                          由 ValueError 转换为 {"detail": "具体错误信息"}

    对应 Service：
        LeaveRequestService.create(db, current_user.id, data)
    """
    try:
        request = await LeaveRequestService.create(db, current_user.id, data)
        return request
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/stats", response_model=LeaveStatsOut)
async def leave_stats(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)
    yesterday_start = (now - timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_end = yesterday_start + timedelta(days=1)

    pending = (
        await db.execute(
            select(func.count()).select_from(LeaveRequest).where(
                LeaveRequest.approval_status == ApprovalStatus.pending
            )
        )
    ).scalar() or 0
    yesterday_pending = (
        await db.execute(
            select(func.count()).select_from(LeaveRequest).where(
                and_(
                    LeaveRequest.approval_status == ApprovalStatus.pending,
                    LeaveRequest.created_at >= yesterday_start,
                    LeaveRequest.created_at < yesterday_end,
                )
            )
        )
    ).scalar() or 0

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month_end = month_start
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    month_leave_count = (
        await db.execute(
            select(func.count()).select_from(LeaveRequest).where(
                LeaveRequest.created_at >= month_start
            )
        )
    ).scalar() or 0
    prev_month_count = (
        await db.execute(
            select(func.count()).select_from(LeaveRequest).where(
                and_(
                    LeaveRequest.created_at >= prev_month_start,
                    LeaveRequest.created_at < prev_month_end,
                )
            )
        )
    ).scalar() or 0
    month_leave_mom_pct = (
        ((month_leave_count - prev_month_count) / prev_month_count) * 100
        if prev_month_count > 0
        else 0.0
    )

    annual_type_id = (
        await db.execute(select(LeaveType.id).where(LeaveType.code == "annual"))
    ).scalar_one_or_none()
    avg_annual_remaining = 0.0
    avg_annual_yoy_delta = 0.0
    if annual_type_id:
        curr_avg = (
            await db.execute(
                select(func.avg(LeaveBalance.remaining_days)).where(
                    and_(
                        LeaveBalance.leave_type_id == annual_type_id,
                        LeaveBalance.year == now.year,
                    )
                )
            )
        ).scalar() or 0
        prev_avg = (
            await db.execute(
                select(func.avg(LeaveBalance.remaining_days)).where(
                    and_(
                        LeaveBalance.leave_type_id == annual_type_id,
                        LeaveBalance.year == now.year - 1,
                    )
                )
            )
        ).scalar() or 0
        avg_annual_remaining = round(float(curr_avg), 1)
        avg_annual_yoy_delta = round(float(curr_avg) - float(prev_avg), 1)

    overdue_pending = (
        await db.execute(
            select(func.count()).select_from(LeaveRequest).where(
                and_(
                    LeaveRequest.approval_status == ApprovalStatus.pending,
                    LeaveRequest.created_at <= now - timedelta(hours=24),
                )
            )
        )
    ).scalar() or 0

    dist_rows = (
        await db.execute(
            select(LeaveType.name, func.count(LeaveRequest.id))
            .select_from(LeaveRequest)
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .where(LeaveRequest.created_at >= month_start)
            .group_by(LeaveType.name)
            .order_by(func.count(LeaveRequest.id).desc())
        )
    ).all()

    return LeaveStatsOut(
        pending=int(pending),
        pending_delta=int(pending - yesterday_pending),
        month_leave_count=int(month_leave_count),
        month_leave_mom_pct=round(float(month_leave_mom_pct), 1),
        avg_annual_remaining=avg_annual_remaining,
        avg_annual_yoy_delta=avg_annual_yoy_delta,
        overdue_pending=int(overdue_pending),
        type_distribution=[{"name": n, "value": int(v)} for n, v in dist_rows],
    )


@router.get("/requests/me", response_model=PaginatedResponse)
async def my_leave_requests(
    leave_type_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /leave/requests/me — 查询当前用户自己的请假记录

    用途：
        员工自查本人的历史请假申请，支持按假期类型、审批状态、
        日期范围过滤，分页返回。
        是员工自助服务（ESS）"我的请假"页面的主要数据接口。
        使用 current_user.id 固定为申请员工 ID，不可查询他人记录。

    权限：
        需要登录（Depends(get_current_user)），无角色限制，员工只能查自己。

    注意：
        此路由必须在 GET /requests（管理视角）之前注册且路径更具体（/me 固定），
        FastAPI 会优先匹配精确路径。

    查询参数 (Query)：
        - leave_type_id: int | None    按假期类型 ID 过滤（如只看年假记录）
        - approval_status: str | None  按审批状态过滤，对应 ApprovalStatus 枚举：
                                         pending（待审批）/ approved（已通过）/
                                         rejected（已拒绝）/ cancelled（已撤销）
        - start_date: date | None      按请假开始日期过滤（包含）
        - end_date: date | None        按请假结束日期过滤（包含）
        - page: int                    页码，从 1 开始，默认 1
        - page_size: int               每页条数，默认 20，最大 1000

    响应：
        200 OK — PaginatedResponse，包含：
                   - total: int                     总记录数
                   - page: int                      当前页码
                   - page_size: int                 每页条数
                   - items: list[LeaveRequestOut]   当页请假申请列表
        400 Bad Request — approval_status 参数值不在有效枚举范围内

    对应 Service：
        LeaveRequestService.query_requests(db, params)
        params.employee_id = current_user.id（自动固定）
    """
    parsed_status = None
    if approval_status:
        try:
            parsed_status = ApprovalStatus(approval_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态: {approval_status}")

    params = LeaveRequestQuery(
        employee_id=current_user.id,
        leave_type_id=leave_type_id,
        approval_status=parsed_status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        page_size=page_size,
    )
    requests, total = await LeaveRequestService.query_requests(db, params)
    items = await _serialize_leave_requests(db, requests)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


@router.get("/requests", response_model=PaginatedResponse)
async def list_leave_requests(
    employee_id: Optional[int] = Query(None),
    department_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None, description="申请人姓名/工号搜索"),
    leave_type_id: Optional[int] = Query(None),
    approval_status: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    overdue_only: bool = Query(False, description="仅看超时未审批（>24h）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /leave/requests — 查询请假记录（管理视角）

    用途：
        HR 或管理员查询全员请假申请列表，支持按员工、假期类型、
        审批状态、日期范围组合过滤，分页返回。
        与 GET /requests/me 的区别：此接口可查询任意员工（employee_id 可选），
        不传 employee_id 则返回全员数据。

    权限：
        需要登录（Depends(get_current_user)），无额外角色限制。
        建议前端对普通员工限制只能查询自己的数据（传 employee_id=自己）。

    查询参数 (Query)：
        - employee_id: int | None      按员工 ID 过滤（不传则返回全员请假记录）
        - leave_type_id: int | None    按假期类型 ID 过滤
        - approval_status: str | None  按审批状态过滤（pending/approved/rejected/cancelled）
        - start_date: date | None      按请假开始日期过滤
        - end_date: date | None        按请假结束日期过滤
        - page: int                    页码，从 1 开始，默认 1
        - page_size: int               每页条数，默认 20，最大 1000

    响应：
        200 OK — PaginatedResponse，包含：
                   - total: int                     总记录数
                   - page: int                      当前页码
                   - page_size: int                 每页条数
                   - items: list[LeaveRequestOut]   当页请假申请列表
        400 Bad Request — approval_status 参数值不在有效枚举范围内

    对应 Service：
        LeaveRequestService.query_requests(db, params)
    """
    parsed_status = None
    if approval_status:
        try:
            parsed_status = ApprovalStatus(approval_status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效的状态: {approval_status}")

    params = LeaveRequestQuery(
        employee_id=employee_id,
        department_id=department_id,
        keyword=keyword,
        leave_type_id=leave_type_id,
        approval_status=parsed_status,
        start_date=start_date,
        end_date=end_date,
        overdue_only=overdue_only,
        page=page,
        page_size=page_size,
    )
    requests, total = await LeaveRequestService.query_requests(db, params)
    items = await _serialize_leave_requests(db, requests)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


@router.get("/requests/pending", response_model=PaginatedResponse)
async def pending_approvals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    GET /leave/requests/pending — 获取待审批请假列表（管理员/HR/经理专用）

    用途：
        返回所有状态为 "pending"（待审批）的请假申请，供审批人集中处理。
        通常展示在审批工作台或消息通知弹窗中。
        数据按申请时间倒序排列，最新申请优先。

    权限：
        需要 admin、hr 或 manager 角色之一（Depends(require_roles("admin", "hr", "manager"))）。
        普通员工无权访问此接口。

    查询参数 (Query)：
        - page: int       页码，从 1 开始，默认 1
        - page_size: int  每页条数，默认 20，最大 1000

    响应：
        200 OK — PaginatedResponse，包含：
                   - total: int                     待审批总数
                   - page: int                      当前页码
                   - page_size: int                 每页条数
                   - items: list[LeaveRequestOut]   待审批请假申请列表
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        LeaveRequestService.list_pending_approvals(db, page, page_size)
    """
    requests, total = await LeaveRequestService.list_pending_approvals(db, page, page_size)
    items = await _serialize_leave_requests(db, requests)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


@router.get("/requests/{request_id}", response_model=LeaveRequestOut)
async def get_leave_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /leave/requests/{request_id} — 获取请假申请详情

    用途：
        按主键 ID 查询单条请假申请的完整详情，包含员工信息、
        假期类型、请假日期、天数、状态、审批意见等。

    权限：
        需要登录（Depends(get_current_user)），无角色限制。
        建议业务层校验：员工只能查自己的申请，HR/经理可查任意申请。

    路径参数 (Path)：
        - request_id: int   请假申请主键 ID

    响应：
        200 OK  — LeaveRequestOut，请假申请完整详情
        404 Not Found — 申请不存在时返回 {"detail": "请假申请不存在"}

    对应 Service：
        LeaveRequestService.get(db, request_id)
    """
    request = await LeaveRequestService.get(db, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="请假申请不存在")
    return request


@router.post("/requests/{request_id}/approve", response_model=LeaveRequestOut)
async def approve_leave_request(
    request_id: int,
    data: LeaveApprovalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """
    POST /leave/requests/{request_id}/approve — 审批请假申请

    用途：
        管理员/HR/经理对指定请假申请进行审批（通过或拒绝）。
        审批通过时，Service 会自动从员工的对应假期余额中扣减请假天数，
        将申请状态更新为 "approved"，并记录审批人和审批时间。
        审批拒绝时，状态变为 "rejected"，余额不扣减。

    权限：
        需要 admin、hr 或 manager 角色之一（Depends(require_roles("admin", "hr", "manager"))）。
        普通员工无权审批他人申请。

    路径参数 (Path)：
        - request_id: int   请假申请主键 ID

    请求体 (Body)：
        LeaveApprovalRequest，主要字段：
            - approved: bool      True 表示通过，False 表示拒绝
            - note: str | None    审批意见（可选，建议拒绝时必填）

    响应：
        200 OK  — LeaveRequestOut，审批后的请假申请：
                   - status: str           "approved" 或 "rejected"
                   - approver_id: int      审批人员工 ID（current_user.id）
                   - approved_at: datetime 审批时间
        404 Not Found — 申请不存在时返回 {"detail": "请假申请不存在"}
        400 Bad Request — 申请已被处理（非 pending 状态）等业务异常时，
                          由 ValueError 转换为 {"detail": "具体错误信息"}
        403 Forbidden — 角色不符合要求时由 require_roles 自动抛出

    对应 Service：
        LeaveRequestService.approve(db, request_id, current_user.id, data.approved, data.note)
    """
    try:
        request = await LeaveRequestService.approve(
            db, request_id, current_user.id, data.approved, data.note
        )
        if request is None:
            raise HTTPException(status_code=404, detail="请假申请不存在")
        return request
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/requests/{request_id}/cancel", response_model=LeaveRequestOut)
async def cancel_leave_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /leave/requests/{request_id}/cancel — 撤销请假申请

    用途：
        员工撤销自己的请假申请。
        - 若申请状态为 "pending"（待审批），直接撤销，余额不受影响
        - 若申请状态为 "approved"（已通过），撤销后 Service 会自动
          归还已扣减的假期余额，确保余额一致性
        Service 内部会校验 request_id 对应的员工是否为当前登录用户，
        防止越权撤销他人申请。

    权限：
        需要登录（Depends(get_current_user)），员工本人撤销，使用 current_user.id。

    路径参数 (Path)：
        - request_id: int   请假申请主键 ID

    响应：
        200 OK  — LeaveRequestOut，撤销后的请假申请（status 变为 "cancelled"）
        404 Not Found — 申请不存在或当前用户无权撤销（非本人申请）时返回
                        {"detail": "请假申请不存在或无权操作"}

    注意：
        已处于 "rejected" 或 "cancelled" 状态的申请无法再次撤销（Service 层校验）。

    对应 Service：
        LeaveRequestService.cancel(db, request_id, current_user.id)
    """
    request = await LeaveRequestService.cancel(db, request_id, current_user.id)
    if request is None:
        raise HTTPException(status_code=404, detail="请假申请不存在或无权操作")
    return request


@router.post("/requests/batch-approve")
async def batch_approve_leave_requests(
    data: LeaveBatchApprovalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    success = 0
    failed: list[dict] = []
    for request_id in data.request_ids:
        try:
            req = await LeaveRequestService.approve(
                db, request_id, current_user.id, data.approved, data.note
            )
            if req is None:
                failed.append({"id": request_id, "reason": "申请不存在"})
                continue
            success += 1
        except ValueError as e:
            failed.append({"id": request_id, "reason": str(e)})
    return {"success": success, "failed": failed, "total": len(data.request_ids)}
