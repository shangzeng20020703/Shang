"""
假期模块业务逻辑
================

本模块是售后管理系统假期功能的核心服务层，负责以下全部业务逻辑：

1. **假期类型管理** (LeaveTypeService)
   - 系统内置 6 种假期：年假 / 病假 / 事假 / 婚假 / 产假 / 调休
   - 可以通过 code 字段按业务键（如 "annual"）查找假期类型
   - 支持按激活状态筛选，停用的假期类型不允许发起申请

2. **假期余额管理** (LeaveBalanceService)
   - 每名员工每种假期每年维护一条余额记录（三元唯一键）
   - 核心公式：remaining_days = total_days - used_days - expired_days
   - 年假余额按工龄阶梯计算（<1年=0天，1-10年=5天，10-20年=10天，≥20年=15天）
   - 批量初始化（bulk_init_balances）：年初 HR 操作，批量为所有在职员工创建年度余额
   - 调休余额支持设置过期日期，过期后自动归零（expire_comp_time）
   - 余额调整（adjust_balance）：正数增加额度，负数减少额度

3. **假期日历视图** (LeaveCalendarService)
   - 按月展示哪些员工在哪些日期有请假安排（pending + approved）
   - 将多天跨度的请假记录展开到每一天，按日期分组返回

4. **请假申请管理** (LeaveRequestService)
   - create()：申请校验（假期类型、证明材料、日期合法性、余额检查）→ 创建记录 → 触发审批流
   - approve()：审批通过时扣减余额，拒绝时不改变余额
   - cancel()：撤销申请，若已审批通过则恢复余额
   - 半天假逻辑：start_half/end_half 字段支持 am/pm/none 三种值，影响天数计算

假期申请状态流转：
    pending（待审批）→ approved（已批准）/ rejected（已拒绝）
    pending/approved → cancelled（已取消）

地区差异说明（产假天数，由假期类型配置决定，非代码硬编码）：
- 北京：产假 158 天
- 广东：产假 178 天
（不同 LeaveType 记录区分地区，或通过 LeaveType.max_days_per_year 字段差异化配置）

余额扣减时机：
- 审批通过时扣减（不是申请时），确保余额反映真实已批准的使用量
- 取消已批准的申请时恢复余额

依赖关系：
- 向上：被 app/api/v1/leave.py 调用
- 向下：读写 app/models/leave.py 中的 ORM 模型
- 横向：调用 app/services/approval.py 的 maybe_create_approval_instance()
         触发通用审批流引擎（若配置了请假审批流）
"""

import json
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.leave import (
    ApprovalStatus,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
    HalfDay,
)
from app.schemas.leave import (
    AnnualLeaveInitRequest,
    LeaveBalanceAdjust,
    LeaveRequestCreate,
    LeaveRequestQuery,
    LeaveTypeCreate,
    LeaveTypeUpdate,
)
from app.services.approval import maybe_create_approval_instance

# Import Employee model for bulk operations (imported lazily inside methods
# to avoid circular imports at module load time; we do a top-level import
# here because app.models.employee does not import from app.services.leave)
from app.models.employee import Employee


class LeaveTypeService:
    """
    假期类型管理服务。

    假期类型定义了一类假期的名称、编码、是否需要证明材料、每年最大天数等规则。

    内置假期类型（code 字段）：
    - annual：年假（按工龄阶梯，5/10/15 天）
    - sick：病假（通常不限额，max_days_per_year=NULL）
    - personal：事假（通常有限额）
    - marriage：婚假（法定 3 天，各地不同）
    - maternity：产假（北京 158 天，广东 178 天，由具体记录配置）
    - comp_time：调休（由加班小时换算，有过期日期）

    注意：
    - is_active=False 的假期类型仍保留历史记录，但不允许新建申请
    - max_days_per_year=NULL 表示该假期不限额（如病假）
    - requires_proof=True 表示申请时必须上传证明材料（如病假需病历）
    """

    @staticmethod
    def default_wecom_type_payloads() -> List[Dict[str, Any]]:
        """企业微信假期管理默认类型，作为系统主数据自动补齐。"""
        annual_brackets = [
            {"min_years": 0, "max_years": 1, "days": 0},
            {"min_years": 1, "max_years": 10, "days": 5},
            {"min_years": 10, "max_years": 20, "days": 10},
            {"min_years": 20, "max_years": None, "days": 15},
        ]
        return [
            {
                "name": "年假",
                "code": "annual",
                "is_paid": True,
                "max_days_per_year": Decimal("15.0"),
                "requires_proof": False,
                "deduct_from": "annual_leave",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 4,
                "leave_unit": "half_day",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "quota_limited": True,
                "quota_rule_type": "work_tenure",
                "quota_rule": {"tenure_basis": "social_tenure", "tenure_brackets": annual_brackets, "validity_years": 1},
                "issuance_rule": "year_start",
                "issuance_month_day": "01-01",
                "validity_type": "from_issue_date",
                "expire_month_day": None,
                "allow_validity_extension": False,
                "extension_value": 0,
                "extension_unit": "day",
                "new_employee_limit_enabled": False,
                "new_employee_limit_months": 0,
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "carry_over_enabled": True,
                "max_carry_over_days": Decimal("0.0"),
                "approval_flow": "部门主管审批",
                "leave_color": "#3370ff",
            },
            {
                "name": "婚假",
                "code": "marriage",
                "is_paid": True,
                "max_days_per_year": None,
                "requires_proof": True,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 7,
                "leave_unit": "day",
                "time_calc": "natural_day",
                "hours_per_day": Decimal("8.0"),
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 0},
                "issuance_rule": "manual",
                "issuance_month_day": "01-01",
                "validity_type": "forever",
                "expire_month_day": None,
                "allow_validity_extension": False,
                "extension_value": 0,
                "extension_unit": "day",
                "new_employee_rule": "none",
                "new_employee_limit_enabled": False,
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#7b61ff",
            },
            {
                "name": "产假",
                "code": "maternity",
                "is_paid": False,
                "max_days_per_year": None,
                "requires_proof": False,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 5,
                "leave_unit": "day",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 0},
                "issuance_rule": "year_start",
                "issuance_month_day": "01-01",
                "validity_type": "forever",
                "expire_month_day": None,
                "allow_validity_extension": False,
                "extension_value": 0,
                "extension_unit": "day",
                "new_employee_rule": "none",
                "new_employee_limit_enabled": False,
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#ff7a45",
            },
            {
                "name": "陪产假",
                "code": "paternity",
                "is_paid": True,
                "max_days_per_year": None,
                "requires_proof": True,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 6,
                "leave_unit": "day",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "rounding_direction": "none",
                "rounding_unit": "none",
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 0},
                "issuance_rule": "manual",
                "issuance_month_day": "01-01",
                "validity_type": "forever",
                "expire_month_day": None,
                "allow_validity_extension": False,
                "extension_value": 1,
                "extension_unit": "day",
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#13c2c2",
            },
            {
                "name": "病假",
                "code": "sick",
                "is_paid": True,
                "max_days_per_year": None,
                "requires_proof": True,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 3,
                "leave_unit": "hour",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "rounding_direction": "none",
                "rounding_unit": "none",
                "quota_limited": False,
                "quota_rule_type": "company_tenure",
                "quota_rule": {"tenure_basis": "hire_date", "tenure_brackets": annual_brackets, "validity_years": 1},
                "issuance_rule": "manual",
                "issuance_month_day": "01-01",
                "validity_type": "from_issue_date",
                "expire_month_day": None,
                "allow_validity_extension": False,
                "extension_value": 1,
                "extension_unit": "month",
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#52c41a",
            },
            {
                "name": "调休",
                "code": "comp_time",
                "is_paid": False,
                "max_days_per_year": None,
                "requires_proof": False,
                "deduct_from": "comp_time",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 2,
                "leave_unit": "hour",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 0, "validity_years": 1},
                "issuance_rule": "year_start",
                "issuance_month_day": "01-01",
                "validity_type": "from_issue_date",
                "expire_month_day": None,
                "allow_validity_extension": False,
                "extension_value": 1,
                "extension_unit": "month",
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#faad14",
            },
            {
                "name": "事假",
                "code": "personal",
                "is_paid": False,
                "max_days_per_year": None,
                "requires_proof": False,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 1,
                "leave_unit": "hour",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "rounding_direction": "none",
                "rounding_unit": "half_hour",
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 0},
                "issuance_rule": "manual",
                "issuance_month_day": "01-01",
                "validity_type": "forever",
                "new_employee_rule": "after_regularization",
                "new_employee_limit_enabled": True,
                "new_employee_limit_months": 0,
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#8c8c8c",
            },
            {
                "name": "丧假",
                "code": "bereavement",
                "is_paid": True,
                "max_days_per_year": Decimal("3.0"),
                "requires_proof": False,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 9,
                "leave_unit": "day",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "quota_limited": True,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 3},
                "issuance_rule": "manual",
                "issuance_month_day": "01-01",
                "validity_type": "natural_year",
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#595959",
            },
            {
                "name": "例假",
                "code": "period_leave",
                "is_paid": True,
                "max_days_per_year": None,
                "requires_proof": False,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 8,
                "leave_unit": "half_day",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "rounding_direction": "none",
                "rounding_unit": "none",
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 0},
                "issuance_rule": "manual",
                "issuance_month_day": "01-01",
                "validity_type": "forever",
                "new_employee_rule": "none",
                "new_employee_limit_enabled": False,
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#f759ab",
            },
            {
                "name": "哺乳假",
                "code": "breastfeeding",
                "is_paid": True,
                "max_days_per_year": None,
                "requires_proof": False,
                "deduct_from": "none",
                "policy_region": "all",
                "is_active": True,
                "sort_order": 10,
                "leave_unit": "hour",
                "time_calc": "workday",
                "hours_per_day": Decimal("8.0"),
                "rounding_direction": "none",
                "rounding_unit": "none",
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 0},
                "issuance_rule": "manual",
                "issuance_month_day": "01-01",
                "validity_type": "forever",
                "new_employee_rule": "none",
                "new_employee_limit_enabled": False,
                "scope_type": "all",
                "scope_rules": {"scope_text": "全企业"},
                "leave_color": "#36cfc9",
            },
        ]

    @staticmethod
    async def ensure_default_wecom_types(db: AsyncSession) -> None:
        """确保年假、病假等企业微信默认类型在数据库中真实存在。"""
        defaults = LeaveTypeService.default_wecom_type_payloads()
        codes = [item["code"] for item in defaults]
        result = await db.execute(select(LeaveType).where(LeaveType.code.in_(codes)))
        existing_types = {item.code: item for item in result.scalars().all()}
        existing_codes = set(existing_types)
        patched_existing = False
        for payload in defaults:
            if payload["code"] in existing_codes:
                before = {
                    column.name: getattr(existing_types[payload["code"]], column.name)
                    for column in LeaveType.__table__.columns
                }
                LeaveTypeService._patch_observed_default_if_needed(existing_types[payload["code"]], payload)
                patched_existing = patched_existing or any(
                    getattr(existing_types[payload["code"]], column.name) != before[column.name]
                    for column in LeaveType.__table__.columns
                )
                continue
            db.add(LeaveType(**LeaveTypeService._normalize_payload(payload)))
        if len(existing_codes) < len(defaults) or patched_existing:
            await db.flush()

    @staticmethod
    def _patch_observed_default_if_needed(leave_type: LeaveType, default_payload: Dict[str, Any]) -> None:
        """把仍停留在旧内置值的假期类型升级到最新视频观察口径，不覆盖人工配置。"""
        if leave_type.code == "marriage":
            is_legacy_marriage_seed = (
                leave_type.name == "婚假"
                and leave_type.leave_unit == "day"
                and leave_type.time_calc == "natural_day"
                and bool(leave_type.quota_limited) is True
                and leave_type.max_days_per_year in {Decimal("3.0"), Decimal("3")}
                and leave_type.quota_rule_type == "fixed"
            )
            if is_legacy_marriage_seed:
                normalized = LeaveTypeService._normalize_payload(default_payload)
                for field, value in normalized.items():
                    if hasattr(leave_type, field):
                        setattr(leave_type, field, value)
            return

        if leave_type.code == "maternity":
            is_legacy_maternity_seed = (
                leave_type.name in {"产休假", "产假"}
                and leave_type.leave_unit == "day"
                and (
                    leave_type.time_calc == "natural_day"
                    or (
                        bool(leave_type.quota_limited) is True
                        and leave_type.max_days_per_year in {Decimal("98.0"), Decimal("98")}
                    )
                )
            )
            if is_legacy_maternity_seed:
                normalized = LeaveTypeService._normalize_payload(default_payload)
                for field, value in normalized.items():
                    if hasattr(leave_type, field):
                        setattr(leave_type, field, value)
            return

        is_legacy_sick_seed = (
            leave_type.code == "sick"
            and leave_type.name == "病假"
            and leave_type.leave_unit == "day"
            and leave_type.time_calc == "natural_day"
            and bool(leave_type.quota_limited) is True
            and leave_type.max_days_per_year == Decimal("15.0")
        )
        is_legacy_paternity_seed = (
            leave_type.code == "paternity"
            and leave_type.name == "陪产假"
            and leave_type.leave_unit == "day"
            and leave_type.time_calc == "natural_day"
            and bool(leave_type.quota_limited) is True
            and leave_type.max_days_per_year == Decimal("10.0")
            and leave_type.issuance_rule == "manual"
        )
        if not (is_legacy_sick_seed or is_legacy_paternity_seed):
            return

        normalized = LeaveTypeService._normalize_payload(default_payload)
        for field, value in normalized.items():
            if hasattr(leave_type, field):
                setattr(leave_type, field, value)

    @staticmethod
    def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """把企业微信式规则字段标准化，确保新旧接口都能写入一致的底层规则。"""
        payload = dict(payload)
        quota_limited = payload.get("quota_limited")
        if quota_limited is None:
            quota_limited = payload.get("max_days_per_year") is not None
        payload["quota_limited"] = bool(quota_limited)

        quota_rule_type = payload.get("quota_rule_type") or "fixed"
        if quota_rule_type == "tenure":
            legacy_basis = (payload.get("quota_rule") or {}).get("tenure_basis")
            quota_rule_type = "company_tenure" if legacy_basis == "hire_date" else "work_tenure"
        if quota_rule_type == "social_tenure":
            quota_rule_type = "work_tenure"
        if quota_rule_type == "company_plus_social":
            quota_rule_type = "social_plus_company"
        code = str(payload.get("code") or "").lower()
        is_sick_type = code == "sick" or str(payload.get("name") or "") == "病假"
        tenure_rule_types = {
            "fixed",
            "company_tenure",
            "work_tenure",
            "social_plus_company",
            "social_or_company_max",
            "social_or_company_min",
        }
        if is_sick_type and quota_rule_type not in tenure_rule_types:
            quota_rule_type = "company_tenure"
        payload["quota_rule_type"] = quota_rule_type
        quota_rule = dict(payload.get("quota_rule") or {})

        if quota_rule_type == "fixed":
            fixed_days = payload.get("max_days_per_year")
            if fixed_days is not None:
                quota_rule.setdefault("fixed_days", float(fixed_days))
        elif quota_rule_type in {"company_tenure", "work_tenure"}:
            quota_rule["tenure_basis"] = "hire_date" if quota_rule_type == "company_tenure" else "social_tenure"
            quota_rule["tenure_brackets"] = LeaveTypeService._normalize_tenure_brackets(
                quota_rule.get("tenure_brackets")
            )
            payload["max_days_per_year"] = max(
                (Decimal(str(item.get("days") or 0)) for item in quota_rule["tenure_brackets"]),
                default=Decimal("0"),
            )
        elif quota_rule_type in {"social_plus_company", "social_or_company_max", "social_or_company_min"}:
            social_brackets = LeaveTypeService._normalize_tenure_brackets(
                quota_rule.get("social_tenure_brackets") or quota_rule.get("tenure_brackets")
            )
            company_brackets = LeaveTypeService._normalize_tenure_brackets(
                quota_rule.get("company_tenure_brackets") or quota_rule.get("tenure_brackets")
            )
            quota_rule["social_tenure_brackets"] = social_brackets
            quota_rule["company_tenure_brackets"] = company_brackets
            social_max = max((Decimal(str(item.get("days") or 0)) for item in social_brackets), default=Decimal("0"))
            company_max = max((Decimal(str(item.get("days") or 0)) for item in company_brackets), default=Decimal("0"))
            payload["max_days_per_year"] = social_max + company_max if quota_rule_type == "social_plus_company" else max(social_max, company_max)
        elif quota_rule_type in {"cumulative", "overtime"}:
            payload["deduct_from"] = "comp_time"

        if code in {"comp_time", "comp"}:
            payload["leave_unit"] = "hour"
            payload["time_calc"] = "workday"
            payload["deduct_from"] = "comp_time"
            payload["is_paid"] = bool(payload.get("is_paid", False))
            payload["requires_proof"] = bool(payload.get("requires_proof", False))
            payload.setdefault("issuance_rule", "year_start")
            payload.setdefault("issuance_month_day", "01-01")
            payload.setdefault("validity_type", "from_issue_date")
            payload.setdefault("allow_validity_extension", False)
            payload["extension_value"] = int(payload.get("extension_value") or 1)
            if not payload.get("extension_unit") or payload.get("extension_unit") == "day":
                payload["extension_unit"] = "month"
            if payload.get("quota_limited") and payload.get("quota_rule_type") in {"cumulative", "overtime"}:
                quota_rule.setdefault("overtime_hours_per_day", 8)
                quota_rule.setdefault("overtime_max_accumulate_days", float(payload.get("max_days_per_year") or 20))

        if is_sick_type:
            payload.setdefault("leave_unit", "hour")
            payload.setdefault("time_calc", "workday")
            payload.setdefault("hours_per_day", Decimal("8.0"))
            payload["validity_type"] = "from_issue_date"
            quota_rule.setdefault("validity_years", 1)
            payload.setdefault("allow_validity_extension", False)
            payload.setdefault("extension_value", 1)
            payload.setdefault("extension_unit", "month")

        if code == "personal":
            payload["is_paid"] = False
            payload["leave_unit"] = payload.get("leave_unit") or "hour"
            payload["time_calc"] = payload.get("time_calc") or "workday"
            payload["quota_limited"] = False
            payload["validity_type"] = payload.get("validity_type") or "forever"

        if code == "maternity":
            if str(payload.get("name") or "") == "产休假":
                payload["name"] = "产假"
            payload["leave_unit"] = "day"
            payload["time_calc"] = payload.get("time_calc") or "workday"
            if payload["time_calc"] == "natural_day":
                payload["time_calc"] = "workday"
            payload.setdefault("issuance_rule", "year_start")
            payload.setdefault("issuance_month_day", "01-01")
            if not payload.get("quota_limited"):
                payload["max_days_per_year"] = None
                payload["validity_type"] = payload.get("validity_type") or "forever"
                payload["allow_validity_extension"] = False
                quota_rule.setdefault("fixed_days", 0)

        issuance_rule = str(payload.get("issuance_rule") or "year_start")
        payload["issuance_rule"] = {
            "yearly": "year_start",
            "annual": "year_start",
            "employee_hire_date": "hire_anniversary",
        }.get(issuance_rule, issuance_rule)

        rounding_direction = str(payload.get("rounding_direction") or "none")
        payload["rounding_direction"] = rounding_direction if rounding_direction in {"none", "up", "down"} else "none"
        rounding_unit = str(payload.get("rounding_unit") or "none")
        payload["rounding_unit"] = rounding_unit if rounding_unit in {"none", "half_hour", "hour", "half_day", "day"} else "none"

        new_employee_rule = str(payload.get("new_employee_rule") or "")
        if not new_employee_rule or (new_employee_rule == "none" and payload.get("new_employee_limit_enabled")):
            new_employee_rule = "after_months" if payload.get("new_employee_limit_enabled") else "none"
        if new_employee_rule not in {"none", "after_regularization", "after_months"}:
            new_employee_rule = "none"
        payload["new_employee_rule"] = new_employee_rule
        payload["new_employee_limit_enabled"] = new_employee_rule != "none"
        if new_employee_rule != "after_months":
            payload["new_employee_limit_months"] = 0

        if not payload["quota_limited"]:
            payload["max_days_per_year"] = None
        payload["quota_rule"] = quota_rule or None
        payload.setdefault("hours_per_day", Decimal("8.0"))
        payload.setdefault("leave_unit", "day")
        payload.setdefault("time_calc", "natural_day")
        payload.setdefault("rounding_direction", "none")
        payload.setdefault("rounding_unit", "none")
        payload.setdefault("scope_type", "all")
        payload.setdefault("scope_rules", None)
        payload.setdefault("issuance_rule", "year_start")
        payload.setdefault("issuance_month_day", "01-01")
        payload.setdefault("validity_type", "natural_year")
        payload.setdefault("extension_unit", "day")
        payload.setdefault("expiration_reminder_enabled", False)
        payload.setdefault("reminder_before_value", 1)
        payload.setdefault("reminder_before_unit", "month")
        payload.setdefault("reminder_notify_employee", True)
        payload.setdefault("reminder_notify_manager", False)
        payload.setdefault("sort_order", 99)
        payload.setdefault("leave_color", "#52c41a")
        return payload

    @staticmethod
    def _normalize_tenure_brackets(raw_brackets: Any) -> List[Dict[str, Any]]:
        """把司龄/工龄区间按上一条规则连续衔接，避免后端计算出现断档。"""
        default_brackets = [
            {"min_years": 0, "max_years": 1, "days": 0},
            {"min_years": 1, "max_years": 10, "days": 5},
            {"min_years": 10, "max_years": 20, "days": 10},
            {"min_years": 20, "max_years": None, "days": 15},
        ]
        source = raw_brackets if isinstance(raw_brackets, list) and raw_brackets else default_brackets
        normalized: List[Dict[str, Any]] = []
        next_min = Decimal("0")
        for index, item in enumerate(source):
            row = item if isinstance(item, dict) else {}
            is_last = index == len(source) - 1
            raw_max = row.get("max_years")
            max_years = None if raw_max in (None, "") else Decimal(str(raw_max))
            if max_years is None and not is_last:
                max_years = next_min + Decimal("1")
            if max_years is not None and max_years <= next_min:
                max_years = next_min + Decimal("1")
            normalized.append(
                {
                    "min_years": float(next_min),
                    "max_years": None if max_years is None else float(max_years),
                    "days": float(Decimal(str(row.get("days") or 0)).quantize(Decimal("0.1"))),
                }
            )
            if max_years is None:
                break
            next_min = max_years
        if normalized and normalized[-1]["max_years"] is not None:
            last = normalized[-1]
            normalized.append(
                {
                    "min_years": last["max_years"],
                    "max_years": None,
                    "days": last["days"],
                }
            )
        return normalized

    @staticmethod
    async def create(db: AsyncSession, data: LeaveTypeCreate) -> LeaveType:
        """
        创建新的假期类型。

        参数:
            db: 异步数据库会话
            data: 假期类型创建 Schema（含 name, code, max_days_per_year,
                  requires_proof, is_active 等字段）

        返回:
            已持久化的 LeaveType 对象（含自增 id）
        """
        payload = LeaveTypeService._normalize_payload(data.model_dump())
        existing_result = await db.execute(select(LeaveType).where(LeaveType.code == payload["code"]))
        leave_type = existing_result.scalar_one_or_none()
        if leave_type:
            for field, value in payload.items():
                if hasattr(leave_type, field):
                    setattr(leave_type, field, value)
            await db.flush()
            await db.refresh(leave_type)
            return leave_type

        leave_type = LeaveType(**payload)
        db.add(leave_type)
        await db.flush()
        await db.refresh(leave_type)
        return leave_type

    @staticmethod
    async def get(db: AsyncSession, type_id: int) -> Optional[LeaveType]:
        """
        按 ID 查询假期类型。

        参数:
            db: 异步数据库会话
            type_id: 假期类型主键 ID

        返回:
            LeaveType 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(LeaveType).where(LeaveType.id == type_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_code(db: AsyncSession, code: str) -> Optional[LeaveType]:
        """
        按业务编码查询假期类型（如 code="annual" 查年假）。

        code 是假期类型的唯一业务键，在年假初始化等场景下用于精确定位
        不依赖 ID 的特定假期类型。

        参数:
            db: 异步数据库会话
            code: 假期类型业务编码（如 "annual", "sick", "comp_time" 等）

        返回:
            LeaveType 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(LeaveType).where(LeaveType.code == code)
        )
        leave_type = result.scalar_one_or_none()
        if leave_type is not None:
            return leave_type
        if code in {item["code"] for item in LeaveTypeService.default_wecom_type_payloads()}:
            await LeaveTypeService.ensure_default_wecom_types(db)
            result = await db.execute(select(LeaveType).where(LeaveType.code == code))
            return result.scalar_one_or_none()
        return None

    @staticmethod
    async def list_types(
        db: AsyncSession, is_active: Optional[bool] = None
    ) -> Sequence[LeaveType]:
        """
        查询假期类型列表，支持按激活状态筛选。

        参数:
            db: 异步数据库会话
            is_active: 是否启用（True=仅查激活，False=仅查停用，None=查全部）

        返回:
            按 id 升序排列的 LeaveType 列表
        """
        await LeaveTypeService.ensure_default_wecom_types(db)
        stmt = select(LeaveType)
        if is_active is not None:
            stmt = stmt.where(LeaveType.is_active == is_active)
        stmt = stmt.order_by(LeaveType.sort_order, LeaveType.id)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession, type_id: int, data: LeaveTypeUpdate
    ) -> Optional[LeaveType]:
        """
        部分更新假期类型（仅修改请求中传入的字段）。

        参数:
            db: 异步数据库会话
            type_id: 目标假期类型 ID
            data: 假期类型更新 Schema（使用 exclude_unset 实现局部更新）

        返回:
            更新后的 LeaveType 对象；若不存在则返回 None
        """
        leave_type = await LeaveTypeService.get(db, type_id)
        if leave_type is None:
            return None
        payload = LeaveTypeService._normalize_payload(
            {**{column.name: getattr(leave_type, column.name) for column in LeaveType.__table__.columns}, **data.model_dump(exclude_unset=True)}
        )
        for field, value in payload.items():
            if not hasattr(leave_type, field):
                continue
            setattr(leave_type, field, value)
        await db.flush()
        await db.refresh(leave_type)
        return leave_type

    @staticmethod
    async def delete(db: AsyncSession, type_id: int) -> bool:
        """
        删除假期类型。

        注意：若该假期类型已有历史请假记录，建议仅将 is_active 设为 False 而非删除，
        以保留数据完整性。

        参数:
            db: 异步数据库会话
            type_id: 目标假期类型 ID

        返回:
            True 表示删除成功；False 表示假期类型不存在
        """
        leave_type = await LeaveTypeService.get(db, type_id)
        if leave_type is None:
            return False
        await db.delete(leave_type)
        await db.flush()
        return True


class LeaveBalanceService:
    """假期余额管理服务。"""

    @staticmethod
    def _new_employee_rule(leave_type: LeaveType) -> str:
        rule = getattr(leave_type, "new_employee_rule", None)
        if not rule:
            rule = "after_months" if getattr(leave_type, "new_employee_limit_enabled", False) else "none"
        return rule if rule in {"none", "after_regularization", "after_months"} else "none"

    @staticmethod
    def _new_employee_effective_date(leave_type: LeaveType, employee: Optional[Employee]) -> Optional[date]:
        if employee is None:
            return None

        rule = LeaveBalanceService._new_employee_rule(leave_type)
        hire_date = getattr(employee, "hire_date", None)
        if rule == "after_regularization":
            probation_end_date = getattr(employee, "probation_end_date", None)
            if probation_end_date is not None:
                return probation_end_date
            status = str(getattr(employee, "status", "") or "")
            employment_type = str(getattr(employee, "employment_type", "") or "")
            if status == "在职" and employment_type != "试用":
                return hire_date
            return None
        if rule == "after_months":
            if hire_date is None:
                return None
            months = int(getattr(leave_type, "new_employee_limit_months", 0) or 0)
            return LeaveBalanceService._add_months(hire_date, max(months, 0))
        return hire_date

    @staticmethod
    def _is_new_employee_rule_effective(
        leave_type: LeaveType,
        employee: Optional[Employee],
        target_date: date,
    ) -> bool:
        effective_date = LeaveBalanceService._new_employee_effective_date(leave_type, employee)
        return effective_date is not None and target_date >= effective_date

    @staticmethod
    def _months_between(start: date, end: date) -> int:
        months = (end.year - start.year) * 12 + (end.month - start.month)
        if end.day < start.day:
            months -= 1
        return max(months, 0)

    @staticmethod
    def _employee_tenure_years(employee: Employee, year: int, basis: str = "social_tenure") -> float:
        base_date = employee.first_work_date if basis == "social_tenure" else employee.hire_date
        base_date = base_date or employee.hire_date or employee.first_work_date
        if not base_date:
            return 0.0
        return max((date(year, 1, 1) - base_date).days / 365.25, 0.0)

    @staticmethod
    def _quota_from_tenure_brackets(employee: Employee, year: int, basis: str, brackets: list[dict]) -> Decimal:
        tenure_years = LeaveBalanceService._employee_tenure_years(employee, year, basis)
        for bracket in sorted(brackets, key=lambda item: float(item.get("min_years") or 0)):
            min_years = float(bracket.get("min_years") or 0)
            max_years = bracket.get("max_years")
            max_years_value = float(max_years) if max_years not in (None, "") else None
            if tenure_years >= min_years and (max_years_value is None or tenure_years < max_years_value):
                return Decimal(str(bracket.get("days") or 0))
        return Decimal("0")

    @staticmethod
    def calculate_quota_for_employee(
        leave_type: LeaveType,
        employee: Employee,
        year: int,
        as_of: Optional[date] = None,
    ) -> Decimal:
        """按假期类型中的企业微信式规则计算某员工某年度应发额度。"""
        if not getattr(leave_type, "quota_limited", True):
            return Decimal("0")
        reference_date = LeaveBalanceService._reference_date_for_year(year, as_of)
        if not LeaveBalanceService._is_new_employee_rule_effective(leave_type, employee, reference_date):
            return Decimal("0")

        rule_type = getattr(leave_type, "quota_rule_type", "fixed") or "fixed"
        rule = getattr(leave_type, "quota_rule", None) or {}

        if rule_type in {"tenure", "company_tenure", "work_tenure"}:
            if rule_type == "company_tenure":
                basis = "hire_date"
            elif rule_type == "work_tenure":
                basis = "social_tenure"
            else:
                basis = rule.get("tenure_basis") or "social_tenure"
            brackets = rule.get("tenure_brackets") or []
            quota = LeaveBalanceService._quota_from_tenure_brackets(employee, year, basis, brackets)
            return LeaveBalanceService._apply_issuance_rule(
                leave_type,
                employee,
                year,
                LeaveBalanceService._apply_actual_work_duration(leave_type, employee, year, quota, as_of),
                as_of,
            )

        if rule_type in {"social_plus_company", "social_or_company_max", "social_or_company_min"}:
            social_quota = LeaveBalanceService._quota_from_tenure_brackets(
                employee, year, "social_tenure", rule.get("social_tenure_brackets") or rule.get("tenure_brackets") or []
            )
            company_quota = LeaveBalanceService._quota_from_tenure_brackets(
                employee, year, "hire_date", rule.get("company_tenure_brackets") or rule.get("tenure_brackets") or []
            )
            if rule_type == "social_plus_company":
                quota = social_quota + company_quota
            elif rule_type == "social_or_company_min":
                quota = min(social_quota, company_quota)
            else:
                quota = max(social_quota, company_quota)
            return LeaveBalanceService._apply_issuance_rule(
                leave_type,
                employee,
                year,
                LeaveBalanceService._apply_actual_work_duration(leave_type, employee, year, quota, as_of),
                as_of,
            )

        if rule_type == "grade":
            employee_grade = (getattr(employee, "job_level", None) or "").strip()
            for item in rule.get("grade_rules") or []:
                grade_text = str(item.get("grade") or "")
                if employee_grade and employee_grade in grade_text:
                    return LeaveBalanceService._apply_issuance_rule(
                        leave_type,
                        employee,
                        year,
                        LeaveBalanceService._apply_actual_work_duration(
                            leave_type, employee, year, Decimal(str(item.get("days") or 0)), as_of
                        ),
                        as_of,
                    )
            return Decimal("0")

        if rule_type == "one_time":
            return LeaveBalanceService._apply_issuance_rule(
                leave_type,
                employee,
                year,
                LeaveBalanceService._apply_actual_work_duration(
                    leave_type,
                    employee,
                    year,
                    Decimal(str(rule.get("one_time_days") or getattr(leave_type, "max_days_per_year", 0) or 0)),
                    as_of,
                ),
                as_of,
            )

        if rule_type in {"cumulative", "overtime"}:
            return Decimal(str(getattr(leave_type, "max_days_per_year", 0) or 0))

        return LeaveBalanceService._apply_issuance_rule(
            leave_type,
            employee,
            year,
            LeaveBalanceService._apply_actual_work_duration(
                leave_type,
                employee,
                year,
                Decimal(str(getattr(leave_type, "max_days_per_year", 0) or rule.get("fixed_days") or 0)),
                as_of,
            ),
            as_of,
        )

    @staticmethod
    def _reference_date_for_year(year: int, as_of: Optional[date] = None) -> date:
        today = as_of or date.today()
        if year < today.year:
            return date(year, 12, 31)
        if year > today.year:
            return date(year, 12, 31)
        return today

    @staticmethod
    def _parse_month_day(value: Optional[str], default_month: int = 1, default_day: int = 1) -> tuple[int, int]:
        try:
            month_text, day_text = str(value or "").split("-", 1)
            month = min(max(int(month_text), 1), 12)
            day = min(max(int(day_text), 1), monthrange(2024, month)[1])
            return month, day
        except Exception:
            return default_month, default_day

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> date:
        return date(year, month, min(day, monthrange(year, month)[1]))

    @staticmethod
    def _add_months(source: date, months: int) -> date:
        month_index = source.month - 1 + months
        year = source.year + month_index // 12
        month = month_index % 12 + 1
        return LeaveBalanceService._safe_date(year, month, source.day)

    @staticmethod
    def _add_years(source: date, years: int) -> date:
        return LeaveBalanceService._safe_date(source.year + years, source.month, source.day)

    @staticmethod
    def _apply_actual_work_duration(
        leave_type: LeaveType,
        employee: Employee,
        year: int,
        annual_quota: Decimal,
        as_of: Optional[date] = None,
    ) -> Decimal:
        rule = getattr(leave_type, "quota_rule", None) or {}
        if not rule.get("actual_work_time_enabled"):
            return annual_quota

        reference_date = LeaveBalanceService._reference_date_for_year(year, as_of)
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)
        hire_date = getattr(employee, "hire_date", None)
        period_start = max(year_start, hire_date) if hire_date else year_start
        period_end = min(reference_date, year_end)
        if period_start > period_end:
            return Decimal("0")

        actual_days = Decimal((period_end - period_start).days + 1)
        total_days = Decimal((year_end - year_start).days + 1)
        return (annual_quota * actual_days / total_days).quantize(Decimal("0.1"))

    @staticmethod
    def _apply_issuance_rule(
        leave_type: LeaveType,
        employee: Employee,
        year: int,
        annual_quota: Decimal,
        as_of: Optional[date] = None,
    ) -> Decimal:
        issuance_rule = getattr(leave_type, "issuance_rule", "year_start") or "year_start"
        reference_date = LeaveBalanceService._reference_date_for_year(year, as_of)
        issue_month, issue_day = LeaveBalanceService._parse_month_day(
            getattr(leave_type, "issuance_month_day", "01-01"), 1, 1
        )
        issue_date = LeaveBalanceService._safe_date(year, issue_month, issue_day)

        if issuance_rule == "monthly":
            start_month = 1
            hire_date = getattr(employee, "hire_date", None)
            if hire_date and hire_date.year == year:
                start_month = hire_date.month
            issued_months = max(reference_date.month - start_month + 1, 0)
            if reference_date.day < issue_day:
                issued_months -= 1
            issued_months = min(max(issued_months, 0), 12)
            return (annual_quota * Decimal(issued_months) / Decimal("12")).quantize(Decimal("0.1"))

        if issuance_rule == "hire_anniversary":
            hire_date = getattr(employee, "hire_date", None)
            if not hire_date:
                return Decimal("0")
            anniversary = LeaveBalanceService._safe_date(year, hire_date.month, hire_date.day)
            return annual_quota if reference_date >= anniversary else Decimal("0")

        if issuance_rule not in {"manual", "overtime_auto"} and reference_date < issue_date:
            return Decimal("0")
        return annual_quota

    @staticmethod
    def calculate_expiry_date(leave_type: LeaveType, year: int) -> Optional[date]:
        validity_type = getattr(leave_type, "validity_type", "natural_year") or "natural_year"
        rule = getattr(leave_type, "quota_rule", None) or {}
        if validity_type == "forever":
            expiry = None
        elif validity_type == "fixed_date":
            month, day = LeaveBalanceService._parse_month_day(getattr(leave_type, "expire_month_day", None), 12, 31)
            expiry = LeaveBalanceService._safe_date(year, month, day)
        elif validity_type == "from_issue_date":
            issue_month, issue_day = LeaveBalanceService._parse_month_day(
                getattr(leave_type, "issuance_month_day", "01-01"), 1, 1
            )
            issue_date = LeaveBalanceService._safe_date(year, issue_month, issue_day)
            if rule.get("validity_unit") == "month":
                months = int(rule.get("validity_months") or rule.get("validity_value") or 12)
                expiry = LeaveBalanceService._add_months(issue_date, months) - timedelta(days=1)
            else:
                years = int(rule.get("validity_years") or rule.get("validity_value") or 1)
                expiry = LeaveBalanceService._add_years(issue_date, years) - timedelta(days=1)
        elif validity_type == "quarter_end":
            issue_month, issue_day = LeaveBalanceService._parse_month_day(
                getattr(leave_type, "issuance_month_day", "01-01"), 1, 1
            )
            issue_date = LeaveBalanceService._safe_date(year, issue_month, issue_day)
            quarter_end_month = ((issue_date.month - 1) // 3 + 1) * 3
            expiry = LeaveBalanceService._safe_date(year, quarter_end_month, monthrange(year, quarter_end_month)[1])
        elif validity_type == "after_overtime_days":
            issue_month, issue_day = LeaveBalanceService._parse_month_day(
                getattr(leave_type, "issuance_month_day", "01-01"), 1, 1
            )
            days = int(rule.get("overtime_valid_days") or rule.get("validity_days") or 30)
            expiry = LeaveBalanceService._safe_date(year, issue_month, issue_day) + timedelta(days=days)
        else:
            expiry = date(year, 12, 31)

        if expiry is not None and getattr(leave_type, "allow_validity_extension", False):
            value = int(getattr(leave_type, "extension_value", 0) or 0)
            unit = getattr(leave_type, "extension_unit", "day") or "day"
            if value > 0 and unit == "day":
                expiry = expiry + timedelta(days=value)
            elif value > 0 and unit == "month":
                expiry = LeaveBalanceService._add_months(expiry, value)
            elif value > 0 and unit == "year":
                expiry = LeaveBalanceService._add_years(expiry, value)
        return expiry

    @staticmethod
    def _is_auto_balance_type(leave_type: LeaveType) -> bool:
        if not getattr(leave_type, "quota_limited", getattr(leave_type, "max_days_per_year", None) is not None):
            return False
        return (getattr(leave_type, "issuance_rule", "year_start") or "year_start") not in {"manual", "overtime_auto"}

    @staticmethod
    def _dynamic_approval_usage_from_audit_payload(
        raw_payload: Any,
        *,
        employee_ids: set[int],
        leave_type_ids: set[int],
        year: int,
    ) -> Optional[tuple[int, int, Decimal]]:
        if not isinstance(raw_payload, str) or "dynamic_approval" not in raw_payload:
            return None
        try:
            payload = json.loads(raw_payload)
        except Exception:
            return None
        if payload.get("usage_source") != "dynamic_approval":
            return None
        try:
            employee_id = int(payload.get("employee_id"))
            leave_type_id = int(payload.get("leave_type_id"))
            payload_year = int(payload.get("year"))
        except (TypeError, ValueError):
            return None
        if employee_id not in employee_ids or leave_type_id not in leave_type_ids or payload_year != year:
            return None
        try:
            adjustment = Decimal(str(payload.get("adjustment") or 0))
        except Exception:
            return None
        if adjustment >= 0:
            return None
        return employee_id, leave_type_id, -adjustment

    @staticmethod
    def _manual_balance_adjustment_from_audit_payload(
        raw_payload: Any,
        *,
        employee_ids: set[int],
        leave_type_ids: set[int],
        year: int,
    ) -> Optional[tuple[int, int, Decimal]]:
        if not isinstance(raw_payload, str):
            return None
        try:
            payload = json.loads(raw_payload)
        except Exception:
            return None
        if payload.get("usage_source") == "dynamic_approval":
            return None
        try:
            employee_id = int(payload.get("employee_id"))
            leave_type_id = int(payload.get("leave_type_id"))
            payload_year = int(payload.get("year"))
        except (TypeError, ValueError):
            return None
        if employee_id not in employee_ids or leave_type_id not in leave_type_ids or payload_year != year:
            return None
        try:
            adjustment = Decimal(str(payload.get("adjustment") or 0))
        except Exception:
            return None
        if adjustment == 0:
            return None
        return employee_id, leave_type_id, adjustment

    @staticmethod
    async def _dynamic_approval_usage_map(
        db: AsyncSession,
        *,
        employee_ids: List[int],
        leave_type_ids: List[int],
        year: int,
    ) -> dict[tuple[int, int], Decimal]:
        if not employee_ids or not leave_type_ids:
            return {}
        employee_id_set = {int(item) for item in employee_ids}
        leave_type_id_set = {int(item) for item in leave_type_ids}
        result = await db.execute(
            select(AuditLog.after_data).where(
                and_(
                    AuditLog.module == "leave",
                    AuditLog.action == "balance_adjust",
                    AuditLog.after_data.contains("dynamic_approval"),
                )
            )
        )
        usage_map: dict[tuple[int, int], Decimal] = {}
        for raw_payload in result.scalars().all():
            parsed = LeaveBalanceService._dynamic_approval_usage_from_audit_payload(
                raw_payload,
                employee_ids=employee_id_set,
                leave_type_ids=leave_type_id_set,
                year=year,
            )
            if parsed is None:
                continue
            employee_id, leave_type_id, days = parsed
            key = (employee_id, leave_type_id)
            usage_map[key] = usage_map.get(key, Decimal("0")) + days
        return usage_map

    @staticmethod
    async def _manual_balance_adjustment_map(
        db: AsyncSession,
        *,
        employee_ids: List[int],
        leave_type_ids: List[int],
        year: int,
    ) -> dict[tuple[int, int], Decimal]:
        if not employee_ids or not leave_type_ids:
            return {}
        employee_id_set = {int(item) for item in employee_ids}
        leave_type_id_set = {int(item) for item in leave_type_ids}
        result = await db.execute(
            select(AuditLog.after_data).where(
                and_(
                    AuditLog.module == "leave",
                    AuditLog.action == "balance_adjust",
                    AuditLog.after_data.contains("adjustment"),
                )
            )
        )
        adjustment_map: dict[tuple[int, int], Decimal] = {}
        for raw_payload in result.scalars().all():
            parsed = LeaveBalanceService._manual_balance_adjustment_from_audit_payload(
                raw_payload,
                employee_ids=employee_id_set,
                leave_type_ids=leave_type_id_set,
                year=year,
            )
            if parsed is None:
                continue
            employee_id, leave_type_id, adjustment = parsed
            key = (employee_id, leave_type_id)
            adjustment_map[key] = adjustment_map.get(key, Decimal("0")) + adjustment
        return adjustment_map

    @staticmethod
    async def sync_rule_based_balances(
        db: AsyncSession,
        year: int,
        employee_ids: Optional[List[int]] = None,
        leave_type_ids: Optional[List[int]] = None,
        as_of: Optional[date] = None,
    ) -> int:
        emp_stmt = select(Employee).where(Employee.is_active == True)
        if employee_ids:
            emp_stmt = emp_stmt.where(Employee.id.in_(employee_ids))
        emp_result = await db.execute(emp_stmt)
        employees = list(emp_result.scalars().all())
        if not employees:
            return 0

        type_stmt = select(LeaveType).where(LeaveType.is_active == True)
        if leave_type_ids:
            type_stmt = type_stmt.where(LeaveType.id.in_(leave_type_ids))
        type_result = await db.execute(type_stmt)
        leave_types = [lt for lt in type_result.scalars().all() if LeaveBalanceService._is_auto_balance_type(lt)]
        if not leave_types:
            return 0

        emp_ids = [emp.id for emp in employees]
        type_ids = [lt.id for lt in leave_types]
        usage_result = await db.execute(
            select(
                LeaveRequest.employee_id,
                LeaveRequest.leave_type_id,
                func.coalesce(func.sum(LeaveRequest.days), 0),
            )
            .where(
                LeaveRequest.employee_id.in_(emp_ids),
                LeaveRequest.leave_type_id.in_(type_ids),
                LeaveRequest.approval_status == ApprovalStatus.approved,
                LeaveRequest.start_date >= date(year, 1, 1),
                LeaveRequest.start_date <= date(year, 12, 31),
            )
            .group_by(LeaveRequest.employee_id, LeaveRequest.leave_type_id)
        )
        usage_map = {
            (int(employee_id), int(leave_type_id)): Decimal(str(days or 0))
            for employee_id, leave_type_id, days in usage_result.all()
        }
        dynamic_usage_map = await LeaveBalanceService._dynamic_approval_usage_map(
            db,
            employee_ids=emp_ids,
            leave_type_ids=type_ids,
            year=year,
        )
        for key, days in dynamic_usage_map.items():
            usage_map[key] = usage_map.get(key, Decimal("0")) + days
        manual_adjustment_map = await LeaveBalanceService._manual_balance_adjustment_map(
            db,
            employee_ids=emp_ids,
            leave_type_ids=type_ids,
            year=year,
        )

        balance_result = await db.execute(
            select(LeaveBalance).where(
                LeaveBalance.employee_id.in_(emp_ids),
                LeaveBalance.leave_type_id.in_(type_ids),
                LeaveBalance.year == year,
            )
        )
        balance_map = {
            (balance.employee_id, balance.leave_type_id): balance
            for balance in balance_result.scalars().all()
        }

        changed = 0
        reference_date = LeaveBalanceService._reference_date_for_year(year, as_of)
        for employee in employees:
            for leave_type in leave_types:
                if not LeaveRequestService._employee_matches_scope(leave_type, employee):
                    continue
                rule_total_days = LeaveBalanceService.calculate_quota_for_employee(
                    leave_type, employee, year, as_of=as_of
                )
                manual_adjustment = manual_adjustment_map.get((employee.id, leave_type.id), Decimal("0"))
                total_days = rule_total_days + manual_adjustment
                if total_days < 0:
                    total_days = Decimal("0")
                used_days = usage_map.get((employee.id, leave_type.id), Decimal("0"))
                expiry_date = LeaveBalanceService.calculate_expiry_date(leave_type, year)
                expired_days = Decimal("0")
                remaining_days = total_days - used_days
                if expiry_date is not None and expiry_date < reference_date and remaining_days > 0:
                    expired_days = remaining_days
                    remaining_days = Decimal("0")
                if remaining_days < 0:
                    remaining_days = Decimal("0")

                balance = balance_map.get((employee.id, leave_type.id))
                if balance is None:
                    balance = LeaveBalance(
                        employee_id=employee.id,
                        leave_type_id=leave_type.id,
                        year=year,
                        total_days=total_days,
                        used_days=used_days,
                        remaining_days=remaining_days,
                        expired_days=expired_days,
                        expiry_date=expiry_date,
                    )
                    db.add(balance)
                    changed += 1
                    continue

                if (
                    balance.total_days != total_days
                    or balance.used_days != used_days
                    or balance.remaining_days != remaining_days
                    or balance.expired_days != expired_days
                    or balance.expiry_date != expiry_date
                ):
                    balance.total_days = total_days
                    balance.used_days = used_days
                    balance.remaining_days = remaining_days
                    balance.expired_days = expired_days
                    balance.expiry_date = expiry_date
                    changed += 1

        if changed:
            await db.flush()
        return changed

    @staticmethod
    async def get_balance(
        db: AsyncSession, employee_id: int, leave_type_id: int, year: int
    ) -> Optional[LeaveBalance]:
        """
        查询指定员工指定假期类型在指定年度的余额记录。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            leave_type_id: 假期类型 ID
            year: 年份（如 2024）

        返回:
            LeaveBalance 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(LeaveBalance).where(
                and_(
                    LeaveBalance.employee_id == employee_id,
                    LeaveBalance.leave_type_id == leave_type_id,
                    LeaveBalance.year == year,
                )
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_or_create(
        db: AsyncSession, employee_id: int, leave_type_id: int, year: int
    ) -> LeaveBalance:
        """
        查询余额记录，若不存在则自动创建（初始值全为 0）。

        这是一种"懒初始化"模式，在扣减/恢复余额时确保记录一定存在，
        避免在申请时因记录不存在而报错。

        注意：自动创建的记录 total_days=0，实际额度需要 HR 手动调整
        或通过 init_annual_leave / bulk_init_balances 批量初始化。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            leave_type_id: 假期类型 ID
            year: 年份

        返回:
            已存在或新创建的 LeaveBalance 对象
        """
        balance = await LeaveBalanceService.get_balance(
            db, employee_id, leave_type_id, year
        )
        if balance is not None:
            return balance
        balance = LeaveBalance(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            year=year,
            total_days=Decimal("0"),
            used_days=Decimal("0"),
            remaining_days=Decimal("0"),
            expired_days=Decimal("0"),
        )
        db.add(balance)
        await db.flush()
        await db.refresh(balance)
        return balance

    @staticmethod
    async def list_balances(
        db: AsyncSession, employee_id: int, year: int
    ) -> Sequence[LeaveBalance]:
        """
        查询指定员工在指定年度的所有假期类型余额。

        通常在员工个人中心或 HR 查看员工假期情况时调用，返回该员工
        所有假期类型的余额概览。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            year: 年份

        返回:
            按假期类型 ID 升序排列的 LeaveBalance 列表
        """
        result = await db.execute(
            select(LeaveBalance)
            .where(
                and_(
                    LeaveBalance.employee_id == employee_id,
                    LeaveBalance.year == year,
                )
            )
            .order_by(LeaveBalance.leave_type_id)
        )
        return result.scalars().all()

    @staticmethod
    async def adjust(
        db: AsyncSession, data: LeaveBalanceAdjust
    ) -> LeaveBalance:
        """
        HR 手动调整假期余额（通过调整 total_days 间接影响 remaining_days）。

        调整公式：
            total_days += adjustment_days
            remaining_days = total_days - used_days - expired_days
        若计算结果为负则 remaining_days 截断为 0（不允许出现负余额）。

        参数:
            db: 异步数据库会话
            data: 余额调整 Schema（含 employee_id, leave_type_id, year, adjustment_days）

        返回:
            更新后的 LeaveBalance 对象
        """
        balance = await LeaveBalanceService.get_or_create(
            db, data.employee_id, data.leave_type_id, data.year
        )
        balance.total_days += data.adjustment_days
        balance.remaining_days = balance.total_days - balance.used_days - balance.expired_days
        if balance.remaining_days < 0:
            balance.remaining_days = Decimal("0")
        await db.flush()
        await db.refresh(balance)
        return balance

    @staticmethod
    def _calculate_annual_leave_days(tenure_years: float) -> Decimal:
        """
        根据工龄计算年假天数（按《职工带薪年休假条例》阶梯规则）。

        工龄阶梯：
        - tenure_years < 1：0 天（未满一年不享有年假）
        - 1 ≤ tenure_years < 10：5 天
        - 10 ≤ tenure_years < 20：10 天
        - tenure_years ≥ 20：15 天

        参数:
            tenure_years: 工龄（年，浮点数，如 3.5 表示 3 年 6 个月）

        返回:
            Decimal 类型的年假天数（0 / 5 / 10 / 15）
        """
        if tenure_years < 1:
            return Decimal("0")
        elif tenure_years < 10:
            return Decimal("5")
        elif tenure_years < 20:
            return Decimal("10")
        else:
            return Decimal("15")

    @staticmethod
    async def init_annual_leave(
        db: AsyncSession,
        year: int,
        employee_records: List[Dict],
    ) -> List[LeaveBalance]:
        """
        按工龄批量初始化年假余额（通常由年初定时任务或 HR 手动触发）。

        业务流程：
        1. 查找 code="annual" 的年假类型（不存在则抛出 ValueError）
        2. 遍历 employee_records 列表，计算每名员工的工龄
           工龄计算：以目标年份 1 月 1 日为基准，减去入职日期，除以 365.25
        3. 按工龄阶梯计算年假天数（调用 _calculate_annual_leave_days）
        4. 获取或创建余额记录，更新 total_days 和 remaining_days
           （used_days 和 expired_days 不重置，保留当年已使用记录）

        参数:
            db: 异步数据库会话
            year: 目标年份（如 2024）
            employee_records: 员工信息列表，格式：
                [{"employee_id": int, "first_work_date": date, "hire_date": date}, ...]
                其中 first_work_date 优先用于工龄计算，缺失时回退到 hire_date。

        返回:
            更新或创建的 LeaveBalance 对象列表

        异常:
            ValueError: 系统中未配置 code="annual" 的年假类型
        """
        annual_type = await LeaveTypeService.get_by_code(db, "annual")
        if annual_type is None:
            raise ValueError("年假类型(code='annual')未配置")

        balances: List[LeaveBalance] = []
        for emp in employee_records:
            emp_id = emp["employee_id"]
            if not (emp.get("first_work_date") or emp.get("hire_date")):
                continue
            employee_snapshot = SimpleNamespace(
                first_work_date=emp.get("first_work_date"),
                hire_date=emp.get("hire_date"),
                job_level=emp.get("job_level"),
            )
            days = LeaveBalanceService.calculate_quota_for_employee(
                annual_type, employee_snapshot, year
            )

            balance = await LeaveBalanceService.get_or_create(
                db, emp_id, annual_type.id, year
            )
            balance.total_days = days
            # remaining = 总额 - 已用 - 已过期（保留当年已使用量）
            balance.remaining_days = days - balance.used_days - balance.expired_days
            if balance.remaining_days < 0:
                balance.remaining_days = Decimal("0")
            balances.append(balance)

        await db.flush()
        for b in balances:
            await db.refresh(b)
        return balances

    @staticmethod
    async def deduct(
        db: AsyncSession, employee_id: int, leave_type_id: int, year: int, days: Decimal
    ) -> LeaveBalance:
        """
        扣减假期余额（在请假审批通过时调用）。

        扣减公式：
            used_days += days
            remaining_days = total_days - used_days - expired_days
        若计算结果为负则 remaining_days 截断为 0。

        注意：本方法不检查余额是否充足，调用方（LeaveRequestService.approve）
        在调用前应已完成余额校验。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            leave_type_id: 假期类型 ID
            year: 请假起始日期所在年份
            days: 扣减天数（Decimal，支持半天 0.5）

        返回:
            更新后的 LeaveBalance 对象
        """
        balance = await LeaveBalanceService.get_or_create(
            db, employee_id, leave_type_id, year
        )
        balance.used_days += days
        balance.remaining_days = balance.total_days - balance.used_days - balance.expired_days
        if balance.remaining_days < 0:
            balance.remaining_days = Decimal("0")
        await db.flush()
        await db.refresh(balance)
        return balance

    @staticmethod
    async def restore(
        db: AsyncSession, employee_id: int, leave_type_id: int, year: int, days: Decimal
    ) -> LeaveBalance:
        """
        恢复假期余额（在取消已批准的请假申请时调用）。

        恢复公式：
            used_days -= days（最低不低于 0，防止数据异常导致负值）
            remaining_days = total_days - used_days - expired_days

        注意：remaining_days 不做负数截断，因为 total_days 通常 >= remaining_days，
        恢复操作不会导致 remaining_days 超过 total_days。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            leave_type_id: 假期类型 ID
            year: 请假起始日期所在年份
            days: 恢复天数（Decimal，应等于原申请的 days 字段）

        返回:
            更新后的 LeaveBalance 对象
        """
        balance = await LeaveBalanceService.get_or_create(
            db, employee_id, leave_type_id, year
        )
        balance.used_days -= days
        if balance.used_days < 0:
            balance.used_days = Decimal("0")
        balance.remaining_days = balance.total_days - balance.used_days - balance.expired_days
        await db.flush()
        await db.refresh(balance)
        return balance

    @staticmethod
    async def sync_approved_usage_for_leave_type(
        db: AsyncSession,
        employee_id: int,
        leave_type_id: int,
        year: int,
    ) -> LeaveBalance:
        """
        按已审批请假单重算某员工某假期类型的已用量。

        事件消费可能因重试被再次投递，不能简单累加 used_days。这里以
        LeaveRequest.approval_status=approved 作为事实来源回填余额，使审批事件
        消费天然幂等，同时保留 HR 手动调整 total_days 的结果。
        """
        usage = await db.scalar(
            select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
                and_(
                    LeaveRequest.employee_id == employee_id,
                    LeaveRequest.leave_type_id == leave_type_id,
                    LeaveRequest.approval_status == ApprovalStatus.approved,
                    LeaveRequest.start_date >= date(year, 1, 1),
                    LeaveRequest.start_date <= date(year, 12, 31),
                )
            )
        )
        used_days = Decimal(str(usage or 0))
        dynamic_usage = await LeaveBalanceService._dynamic_approval_usage_map(
            db,
            employee_ids=[employee_id],
            leave_type_ids=[leave_type_id],
            year=year,
        )
        used_days += dynamic_usage.get((employee_id, leave_type_id), Decimal("0"))

        result = await db.execute(
            select(LeaveBalance)
            .where(
                and_(
                    LeaveBalance.employee_id == employee_id,
                    LeaveBalance.leave_type_id == leave_type_id,
                    LeaveBalance.year == year,
                )
            )
            .with_for_update()
        )
        balance = result.scalar_one_or_none()
        if balance is None:
            leave_type = await LeaveTypeService.get(db, leave_type_id)
            balance = LeaveBalance(
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                year=year,
                total_days=Decimal("0"),
                used_days=Decimal("0"),
                remaining_days=Decimal("0"),
                expired_days=Decimal("0"),
                expiry_date=LeaveBalanceService.calculate_expiry_date(leave_type, year) if leave_type else None,
            )
            db.add(balance)

        balance.used_days = used_days
        expired_days = Decimal(str(balance.expired_days or 0))
        total_days = Decimal(str(balance.total_days or 0))
        remaining_days = total_days - used_days - expired_days
        balance.remaining_days = remaining_days if remaining_days > 0 else Decimal("0")
        await db.flush()
        await db.refresh(balance)
        return balance

    @staticmethod
    async def expire_comp_time(db: AsyncSession, reference_date: Optional[date] = None) -> int:
        """
        处理调休余额过期：将所有过期日期 <= reference_date 且剩余天数 > 0 的调休余额归零。

        通常由定时任务（如每天凌晨或每月初）调用。调休余额一般在申请时设置
        6 个月或 1 年的有效期，过期后自动失效。

        过期处理逻辑：
            expired_days += remaining_days（记录过期量）
            remaining_days = 0

        参数:
            db: 异步数据库会话
            reference_date: 参考日期（默认为今日），expiry_date <= 此日期的记录将被过期

        返回:
            本次被过期处理的余额记录数量
        """
        if reference_date is None:
            reference_date = date.today()
        result = await db.execute(
            select(LeaveBalance).where(
                and_(
                    LeaveBalance.expiry_date.isnot(None),
                    LeaveBalance.expiry_date <= reference_date,
                    LeaveBalance.remaining_days > 0,
                )
            )
        )
        expired_balances = result.scalars().all()
        count = 0
        for balance in expired_balances:
            # 将剩余天数全部记入 expired_days，remaining_days 清零
            balance.expired_days += balance.remaining_days
            balance.remaining_days = Decimal("0")
            count += 1
        await db.flush()
        return count


    @staticmethod
    async def bulk_init_balances(
        db: AsyncSession,
        year: int,
        employee_ids: Optional[List[int]] = None,
        reset_existing: bool = False,
    ) -> Dict:
        """
        按年度批量初始化所有在职员工的假期余额（年初 HR 操作）。

        初始化规则：
        - 遍历所有激活员工（或指定 employee_ids 子集）
        - 遍历所有激活假期类型中 max_days_per_year 不为 NULL 的类型
          （NULL 表示不限额如病假，无需初始化余额记录）
        - reset_existing=False（默认）：已存在的余额记录跳过，不覆盖
        - reset_existing=True：更新已有记录的 total_days，同时重新计算 remaining_days

        返回格式：
            {
                "success": 成功创建/更新的记录数,
                "skipped": 已存在且跳过的记录数,
                "errors": ["员工X 假期类型Y: 错误信息", ...]
            }

        参数:
            db: 异步数据库会话
            year: 目标年份
            employee_ids: 指定员工 ID 列表（可选，None 表示处理所有在职员工）
            reset_existing: 是否重置已有余额（True=覆盖，False=跳过）

        返回:
            包含 success / skipped / errors 字段的统计字典
        """
        # 查询所有激活员工（或指定ID列表）
        emp_stmt = select(Employee).where(Employee.is_active == True)
        if employee_ids:
            emp_stmt = emp_stmt.where(Employee.id.in_(employee_ids))
        emp_result = await db.execute(emp_stmt)
        employees: List[Employee] = list(emp_result.scalars().all())

        # 查询所有激活的假期类型。手动导入和加班自动调休不参与年度批量发放。
        type_result = await db.execute(
            select(LeaveType).where(LeaveType.is_active == True)
        )
        leave_types: List[LeaveType] = list(type_result.scalars().all())
        bounded_types = [lt for lt in leave_types if LeaveBalanceService._is_auto_balance_type(lt)]

        success = 0
        skipped = 0
        errors: List[str] = []

        for emp in employees:
            for lt in bounded_types:
                try:
                    existing = await LeaveBalanceService.get_balance(db, emp.id, lt.id, year)
                    if existing is not None:
                        if not reset_existing:
                            # 不重置模式：已有记录直接跳过
                            skipped += 1
                            continue
                        days = LeaveBalanceService.calculate_quota_for_employee(lt, emp, year)
                        # reset 模式：按规则重新计算 total_days，重新计算 remaining
                        existing.total_days = days
                        existing.remaining_days = (
                            days - existing.used_days - existing.expired_days
                        )
                        if existing.remaining_days < 0:
                            existing.remaining_days = Decimal("0")
                        success += 1
                    else:
                        # 新建余额记录，初始 used_days=0，remaining=total=规则计算额度
                        days = LeaveBalanceService.calculate_quota_for_employee(lt, emp, year)
                        balance = LeaveBalance(
                            employee_id=emp.id,
                            leave_type_id=lt.id,
                            year=year,
                            total_days=days,
                            used_days=Decimal("0"),
                            remaining_days=days,
                            expired_days=Decimal("0"),
                        )
                        db.add(balance)
                        success += 1
                except Exception as exc:
                    errors.append(f"员工{emp.id}({emp.name}) 假期类型{lt.id}({lt.name}): {exc}")

        await db.flush()
        return {"success": success, "skipped": skipped, "errors": errors}

    @staticmethod
    async def adjust_balance(
        db: AsyncSession,
        employee_id: int,
        leave_type_id: int,
        adjustment: float,
        reason: str,  # noqa: ARG004 – stored for audit; not a DB column, kept for API parity
        year: int,
    ) -> LeaveBalance:
        """
        HR 手动调整假期余额（正数增加额度，负数减少额度）。

        与 adjust() 的区别：此方法接受 float 类型的 adjustment，并额外接受
        reason 参数用于 API 层的审计追踪（当前未写入 DB，保留为接口参数）。

        调整公式：
            total_days += adjustment（total_days 最低不低于 0）
            remaining_days = total_days - used_days - expired_days

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            leave_type_id: 假期类型 ID
            adjustment: 调整量（正数增加，负数减少）
            reason: 调整原因（用于审计，暂未写入 DB）
            year: 年份

        返回:
            更新后的 LeaveBalance 对象
        """
        adj_decimal = Decimal(str(adjustment))
        balance = await LeaveBalanceService.get_or_create(db, employee_id, leave_type_id, year)
        balance.total_days += adj_decimal
        if balance.total_days < 0:
            balance.total_days = Decimal("0")
        balance.remaining_days = balance.total_days - balance.used_days - balance.expired_days
        if balance.remaining_days < 0:
            balance.remaining_days = Decimal("0")
        await db.flush()
        await db.refresh(balance)
        return balance


class LeaveCalendarService:
    """
    请假日历视图服务。

    提供按月展示的请假日历数据，供前端日历组件渲染。
    展示"本月哪些员工在哪些日期有请假安排"，包含 pending（待审批）
    和 approved（已批准）状态的申请，便于团队协作排期。

    数据结构：将多天连续请假展开为每一天的条目，按日期键分组：
    {
        "2024-01-15": [
            {"employee_id": 1, "employee_name": "张三", "leave_type": "年假", "days": 3.0},
            ...
        ],
        ...
    }
    """

    @staticmethod
    async def get_calendar_data(
        db: AsyncSession,
        year: int,
        month: int,
        department_id: Optional[int] = None,
        leave_type_id: Optional[int] = None,
    ) -> Dict[str, list]:
        """
        查询指定月份所有已批准或待审批的请假申请，按日期展开返回。

        实现逻辑：
        1. 计算目标月份的首日和末日
        2. 查询与本月有日期交叉的 pending/approved 请假申请
           （start_date <= 末日 AND end_date >= 首日 可捕获所有交叉记录）
        3. 若指定部门，JOIN Employee 进行部门过滤
        4. 遍历每条请假记录，展开到 [effective_start, effective_end] 的每一天
           （effective_start/end = 请假日期范围与本月日期范围的交集）
        5. 使用 _request_id 内部去重（同一申请在同一天只出现一次）
        6. 清理内部去重字段后返回

        返回格式:
        {
            "2024-01-15": [
                {"employee_id": 1, "employee_name": "张三", "leave_type": "年假", "days": 1.0}
            ],
            ...
        }

        参数:
            db: 异步数据库会话
            year: 年份
            month: 月份（1-12）
            department_id: 部门 ID（可选，指定后仅展示该部门员工的请假）

        返回:
            日期字符串（ISO 格式 "YYYY-MM-DD"）→ 请假信息列表的字典
        """
        from datetime import date as dt_date
        import calendar as cal_mod

        first_day = dt_date(year, month, 1)
        last_day = dt_date(year, month, cal_mod.monthrange(year, month)[1])

        # 查询本月有交叉的请假申请（pending + approved）
        # 条件：start_date <= 月末 AND end_date >= 月初（覆盖所有与本月有重叠的申请）
        conditions = [
            LeaveRequest.approval_status.in_([ApprovalStatus.pending, ApprovalStatus.approved]),
            LeaveRequest.start_date <= last_day,
            LeaveRequest.end_date >= first_day,
        ]

        # 关联 Employee 用于获取姓名和部门过滤
        stmt = (
            select(LeaveRequest, Employee, LeaveType)
            .join(Employee, Employee.id == LeaveRequest.employee_id)
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .where(and_(*conditions))
        )
        if department_id is not None:
            stmt = stmt.where(Employee.department_id == department_id)
        if leave_type_id is not None:
            stmt = stmt.where(LeaveRequest.leave_type_id == leave_type_id)

        result = await db.execute(stmt)
        rows = result.all()

        calendar_data: Dict[str, list] = {}

        for leave_req, emp, lt in rows:
            # 计算请假记录与本月的交集日期范围
            effective_start = max(leave_req.start_date, first_day)
            effective_end = min(leave_req.end_date, last_day)

            # 将多天请假展开为每一天的条目
            current = effective_start
            from datetime import timedelta
            while current <= effective_end:
                date_key = current.isoformat()
                if date_key not in calendar_data:
                    calendar_data[date_key] = []
                # 避免同一员工同一申请在同一天重复出现（防御性去重）
                already = any(
                    item["employee_id"] == emp.id and item.get("_request_id") == leave_req.id
                    for item in calendar_data[date_key]
                )
                if not already:
                    calendar_data[date_key].append({
                        "employee_id": emp.id,
                        "employee_name": emp.name,
                        "leave_type": lt.name,
                        "days": float(leave_req.days),
                        "_request_id": leave_req.id,  # 内部去重用，不返回给前端
                    })
                current += timedelta(days=1)

        # 清理内部去重字段（不暴露给前端）
        for date_key in calendar_data:
            for item in calendar_data[date_key]:
                item.pop("_request_id", None)

        return calendar_data


class LeaveRequestService:
    """请假申请管理服务。"""

    @staticmethod
    async def _get_employee(db: AsyncSession, employee_id: int) -> Optional[Employee]:
        result = await db.execute(select(Employee).where(Employee.id == employee_id))
        return result.scalar_one_or_none()

    @staticmethod
    def _employee_matches_scope(leave_type: LeaveType, employee: Optional[Employee]) -> bool:
        if employee is None:
            return True
        scope_type = getattr(leave_type, "scope_type", "all") or "all"
        scope_rules = getattr(leave_type, "scope_rules", None) or {}
        if scope_type == "dept":
            dept_ids = {int(item) for item in scope_rules.get("department_ids", []) if item is not None}
            return not dept_ids or getattr(employee, "department_id", None) in dept_ids
        if scope_type in {"department_or_employee", "dept_employee", "dept_or_employee"}:
            dept_ids = {int(item) for item in scope_rules.get("department_ids", []) if item is not None}
            employee_ids = {int(item) for item in scope_rules.get("employee_ids", []) if item is not None}
            if not dept_ids and not employee_ids:
                return False
            employee_id = int(getattr(employee, "id", 0) or 0)
            department_id = getattr(employee, "department_id", None)
            return employee_id in employee_ids or department_id in dept_ids
        if scope_type in {"employee", "member", "members"}:
            employee_ids = {int(item) for item in scope_rules.get("employee_ids", []) if item is not None}
            return int(getattr(employee, "id", 0) or 0) in employee_ids
        if scope_type == "gender":
            expected = str(scope_rules.get("gender_scope") or "").lower()
            gender = str(getattr(employee, "gender", "") or "").lower()
            if expected == "female":
                return gender in {"女", "female", "f"}
            if expected == "male":
                return gender in {"男", "male", "m"}
            return True
        if scope_type == "employee_type":
            allowed = {str(item) for item in scope_rules.get("employee_types", [])}
            return not allowed or str(getattr(employee, "employment_type", "") or "") in allowed
        return True

    @staticmethod
    def _validate_new_employee_limit(leave_type: LeaveType, employee: Optional[Employee], start_date: date) -> None:
        rule = LeaveBalanceService._new_employee_rule(leave_type)
        effective_date = LeaveBalanceService._new_employee_effective_date(leave_type, employee)
        if rule == "none":
            hire_date = getattr(employee, "hire_date", None) if employee is not None else None
            if hire_date is not None and start_date < hire_date:
                raise ValueError(f"{leave_type.name}需入职后才可申请")
            if hire_date is None:
                raise ValueError(f"请先维护入职日期后再申请{leave_type.name}")
            return
        if rule == "after_regularization":
            if effective_date is None or start_date < effective_date:
                raise ValueError(f"{leave_type.name}需转正后才可申请")
            return
        months = int(getattr(leave_type, "new_employee_limit_months", 0) or 0)
        if effective_date is None:
            raise ValueError(f"请先维护入职日期后再申请{leave_type.name}")
        if start_date < effective_date:
            raise ValueError(f"入职未满{months}个月不可申请{leave_type.name}")

    @staticmethod
    def _retroactive_month_delta(target_date: date, current_date: date) -> int:
        return (current_date.year - target_date.year) * 12 + current_date.month - target_date.month

    @staticmethod
    def _retroactive_rule_violation(start_date: date, current_date: date, extra_config: Optional[dict]) -> Optional[str]:
        extra = extra_config if isinstance(extra_config, dict) else {}
        if not extra.get("leave_retroactive_limit_enabled"):
            return None
        if start_date >= current_date:
            return None

        month_delta = LeaveRequestService._retroactive_month_delta(start_date, current_date)
        limit_months = max(0, int(extra.get("leave_retroactive_limit_months") or 0))
        if month_delta > limit_months:
            return f"请假日期超过考勤规则允许的{limit_months}个月补交期限"

        cutoff_day = int(extra.get("leave_retroactive_cutoff_day") or 0)
        if month_delta >= 1 and cutoff_day > 0 and current_date.day > cutoff_day:
            return f"每月{cutoff_day}日后不可补交上月及更早的请假审批"
        return None

    @staticmethod
    def _iter_year_months(start_date: date, end_date: date) -> List[Tuple[int, int]]:
        months: List[Tuple[int, int]] = []
        cursor = date(start_date.year, start_date.month, 1)
        end_cursor = date(end_date.year, end_date.month, 1)
        while cursor <= end_cursor:
            months.append((cursor.year, cursor.month))
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
        return months

    @staticmethod
    async def _validate_retroactive_submission(
        db: AsyncSession,
        employee: Optional[Employee],
        start_date: date,
        end_date: date,
    ) -> None:
        if employee is None:
            return
        from app.models.attendance import AttendanceMonthSummary, MonthlySummaryStatus
        from app.services.attendance import AttendanceRecordService

        rule = await AttendanceRecordService._select_rule_for_employee(db, employee, start_date)
        if rule is None:
            return
        extra = getattr(rule, "extra_config", None) or {}
        today = datetime.now(AttendanceRecordService._cst()).date()
        violation = LeaveRequestService._retroactive_rule_violation(start_date, today, extra)
        if violation:
            raise ValueError(violation)

        if not extra.get("leave_retroactive_block_locked_summary", True):
            return
        for year, month in LeaveRequestService._iter_year_months(start_date, end_date):
            locked_count = await db.scalar(
                select(func.count())
                .select_from(AttendanceMonthSummary)
                .where(
                    and_(
                        AttendanceMonthSummary.employee_id == employee.id,
                        AttendanceMonthSummary.year == year,
                        AttendanceMonthSummary.month == month,
                        AttendanceMonthSummary.status == MonthlySummaryStatus.locked,
                    )
                )
            )
            if int(locked_count or 0) > 0:
                raise ValueError(f"{year}年{month}月考勤月报已锁定，不允许补交该月份请假审批")

    @staticmethod
    async def _refresh_attendance_summaries_for_request(db: AsyncSession, request: LeaveRequest) -> None:
        from app.services.attendance import AttendanceRecordService

        for year, month in LeaveRequestService._iter_year_months(request.start_date, request.end_date):
            await AttendanceRecordService._refresh_month_summary_for_date(
                db,
                request.employee_id,
                date(year, month, 1),
            )

    @staticmethod
    async def _calculate_leave_days_by_rule(
        db: AsyncSession,
        employee: Optional[Employee],
        leave_type: LeaveType,
        start_date: date,
        end_date: date,
        start_half: HalfDay,
        end_half: HalfDay,
    ) -> Decimal:
        if getattr(leave_type, "time_calc", "natural_day") != "workday":
            return LeaveRequestService._calculate_leave_days(start_date, end_date, start_half, end_half)

        from app.models.attendance import DayType
        from app.services.attendance import WorkCalendarService

        location_id = getattr(employee, "location_id", None) if employee is not None else None
        total = Decimal("0")
        current = start_date
        while current <= end_date:
            day_type = await WorkCalendarService.get_day_type(db, current, location_id)
            if day_type in (DayType.workday, DayType.special_workday):
                if start_date == end_date:
                    total += LeaveRequestService._calculate_leave_days(current, current, start_half, end_half)
                elif current == start_date and start_half == HalfDay.pm:
                    total += Decimal("0.5")
                elif current == end_date and end_half == HalfDay.am:
                    total += Decimal("0.5")
                else:
                    total += Decimal("1")
            current += timedelta(days=1)
        if total <= 0:
            raise ValueError("请假区间内没有可计算的工作日")
        return total

    @staticmethod
    def _calculate_leave_days(
        start_date: date,
        end_date: date,
        start_half: HalfDay,
        end_half: HalfDay,
    ) -> Decimal:
        """
        计算请假天数，支持半天假逻辑。

        计算规则：
        【同一天】：
        - start_half == end_half（如上午到上午 = 半天）→ 0.5 天
        - start_half=pm, end_half=am（下午开始到上午结束）→ 抛出 ValueError（无效输入）
        - start_half=am, end_half=pm（整天）→ 1 天

        【跨天】：
        - base = (end_date - start_date).days + 1（自然天数）
        - start_half=pm（首日从下午开始）→ base - 0.5
        - end_half=am（末日到上午结束）→ base - 0.5
        - 两者都有 → base - 1

        参数:
            start_date: 请假起始日期
            end_date: 请假结束日期（须 >= start_date）
            start_half: 首日是从上午还是下午开始（HalfDay.am / pm / none）
            end_half: 末日是到上午还是下午结束（HalfDay.am / pm / none）

        返回:
            Decimal 类型的请假天数（如 0.5, 1, 2.5）

        异常:
            ValueError: 同一天内 start_half=pm 且 end_half=am（下午开始到上午结束，无效）
        """
        if start_date == end_date:
            if start_half == end_half:
                return Decimal("0.5")
            # pm→am 在同一天是无效输入（下午开始到上午结束）
            if start_half == HalfDay.pm and end_half == HalfDay.am:
                raise ValueError("同一天内开始半天不能晚于结束半天")
            return Decimal("1")

        total_days = (end_date - start_date).days + 1
        result = Decimal(str(total_days))

        # 首日如果从下午开始, 减半天（早上不算请假）
        if start_half == HalfDay.pm:
            result -= Decimal("0.5")
        # 末日如果到上午结束, 减半天（下午不算请假）
        if end_half == HalfDay.am:
            result -= Decimal("0.5")

        return result

    @staticmethod
    def _apply_duration_rounding(leave_type: LeaveType, days: Decimal) -> Decimal:
        direction = str(getattr(leave_type, "rounding_direction", "none") or "none")
        unit = str(getattr(leave_type, "rounding_unit", "none") or "none")
        if direction == "none" or unit == "none" or days <= 0:
            return days

        rounding = ROUND_CEILING if direction == "up" else ROUND_FLOOR
        if unit in {"half_day", "day"}:
            quantum = Decimal("0.5") if unit == "half_day" else Decimal("1")
            return (days / quantum).to_integral_value(rounding=rounding) * quantum

        hours_per_day = Decimal(str(getattr(leave_type, "hours_per_day", 8) or 8))
        if hours_per_day <= 0:
            hours_per_day = Decimal("8")
        quantum_hours = Decimal("0.5") if unit == "half_hour" else Decimal("1")
        hours = days * hours_per_day
        rounded_hours = (hours / quantum_hours).to_integral_value(rounding=rounding) * quantum_hours
        return (rounded_hours / hours_per_day).quantize(Decimal("0.1"))

    @staticmethod
    async def create(
        db: AsyncSession, employee_id: int, data: LeaveRequestCreate
    ) -> LeaveRequest:
        """
        创建请假申请，执行全面校验后写入数据库并触发审批流。

        校验流程（按顺序，任一失败立即抛出 ValueError）：
        1. 假期类型存在性校验（type_id 有效）
        2. 假期类型激活状态校验（is_active=True）
        3. 证明材料校验（requires_proof=True 时 proof_url 必须提供）
        4. 日期合法性校验（start_date <= end_date）
        5. 天数计算（调用 _calculate_leave_days）
        6. 余额充足性校验（仅对有 max_days_per_year 限制的假期类型）：
           - 余额记录不存在 → 提示 HR 初始化
           - 剩余天数 < 申请天数 → 余额不足

        成功后：
        - 创建 LeaveRequest 记录（status=pending）
        - 调用 maybe_create_approval_instance() 软触发审批流
          （若系统中配置了 "leave" 模块的审批流，则自动创建审批实例）

        参数:
            db: 异步数据库会话
            employee_id: 申请人员工 ID
            data: 请假申请创建 Schema（含假期类型、起止日期、半天标识、原因、证明材料）

        返回:
            已持久化的 LeaveRequest 对象（approval_status=pending）

        异常:
            ValueError: 各类校验失败（假期类型无效/已停用/证明材料缺失/日期异常/余额不足）
        """
        # 校验假期类型
        leave_type = await LeaveTypeService.get(db, data.leave_type_id)
        if leave_type is None:
            raise ValueError("假期类型不存在")
        if not leave_type.is_active:
            raise ValueError("该假期类型已停用")

        employee = await LeaveRequestService._get_employee(db, employee_id)
        if not LeaveRequestService._employee_matches_scope(leave_type, employee):
            raise ValueError(f"当前员工不在{leave_type.name}的适用范围内")
        LeaveRequestService._validate_new_employee_limit(leave_type, employee, data.start_date)

        # 校验证明材料（如病假需要病历，婚假需要结婚证等）
        if leave_type.requires_proof and not data.proof_url:
            raise ValueError(f"{leave_type.name}需要提供证明材料")

        # 校验日期（必须先于天数计算）
        if data.start_date > data.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        if not data.reason or len(data.reason.strip()) < 5:
            raise ValueError("请假事由至少填写5个字")
        await LeaveRequestService._validate_retroactive_submission(
            db,
            employee,
            data.start_date,
            data.end_date,
        )

        # 计算实际请假天数（考虑半天假逻辑）
        days = await LeaveRequestService._calculate_leave_days_by_rule(
            db, employee, leave_type, data.start_date, data.end_date, data.start_half, data.end_half
        )
        days = LeaveRequestService._apply_duration_rounding(leave_type, days)

        # 校验余额（仅对有额度限制的假期类型，如病假不限额则跳过）
        # 使用 FOR UPDATE 行锁防止并发请假竞态：两个请求同时读余额都通过检查导致超额
        year = data.start_date.year
        if getattr(leave_type, "quota_limited", getattr(leave_type, "max_days_per_year", None) is not None):
            if LeaveBalanceService._is_auto_balance_type(leave_type):
                await LeaveBalanceService.sync_rule_based_balances(
                    db,
                    year=year,
                    employee_ids=[employee_id],
                    leave_type_ids=[leave_type.id],
                    as_of=data.start_date,
                )
            lock_result = await db.execute(
                select(LeaveBalance)
                .where(
                    and_(
                        LeaveBalance.employee_id == employee_id,
                        LeaveBalance.leave_type_id == data.leave_type_id,
                        LeaveBalance.year == year,
                    )
                )
                .with_for_update()
            )
            balance = lock_result.scalar_one_or_none()
            if balance is None:
                raise ValueError("未找到该假期类型的余额记录，请联系HR初始化假期余额")
            if balance.remaining_days is None or balance.remaining_days < days:
                raise ValueError(
                    f"余额不足: 剩余{balance.remaining_days}天, 需要{days}天"
                )
        else:
            balance = None  # 无额度限制的假期类型（如病假）不需要锁余额

        request = LeaveRequest(
            employee_id=employee_id,
            leave_type_id=data.leave_type_id,
            start_date=data.start_date,
            end_date=data.end_date,
            start_half=data.start_half,
            end_half=data.end_half,
            days=days,
            reason=data.reason,
            proof_url=data.proof_url,
            approval_status=ApprovalStatus.pending,
        )
        db.add(request)
        await db.flush()
        await db.refresh(request)

        # 软触发审批流（如配置了请假审批流则自动创建审批实例）
        # "软触发"含义：若系统未配置该模块的审批流，此调用静默忽略，不报错
        await maybe_create_approval_instance(
            db,
            module="leave",
            business_id=request.id,
            business_type="leave_request",
            applicant_id=employee_id,
        )

        return request

    @staticmethod
    async def get(db: AsyncSession, request_id: int) -> Optional[LeaveRequest]:
        """
        按 ID 查询单条请假申请。

        参数:
            db: 异步数据库会话
            request_id: 请假申请主键 ID

        返回:
            LeaveRequest 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(LeaveRequest).where(LeaveRequest.id == request_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def query_requests(
        db: AsyncSession, params: LeaveRequestQuery
    ) -> Tuple[List[LeaveRequest], int]:
        """
        分页查询请假申请列表，支持多条件筛选。

        参数:
            db: 异步数据库会话
            params: 查询参数 Schema（含 employee_id, leave_type_id, approval_status,
                    start_date, end_date, page, page_size）
                    - start_date/end_date 过滤的是申请的 start_date 字段

        返回:
            (请假申请列表, 总记录数)，按创建时间降序排列
        """
        stmt = select(LeaveRequest)
        count_stmt = select(func.count()).select_from(LeaveRequest)
        join_employee = params.department_id is not None or bool(params.keyword)
        if join_employee:
            stmt = stmt.join(Employee, Employee.id == LeaveRequest.employee_id)
            count_stmt = count_stmt.join(Employee, Employee.id == LeaveRequest.employee_id)

        conditions = []
        if params.employee_id is not None:
            conditions.append(LeaveRequest.employee_id == params.employee_id)
        if params.leave_type_id is not None:
            conditions.append(LeaveRequest.leave_type_id == params.leave_type_id)
        if params.department_id is not None:
            conditions.append(Employee.department_id == params.department_id)
        if params.keyword:
            kw = f"%{params.keyword.strip()}%"
            conditions.append(
                or_(
                    Employee.name.ilike(kw),
                    Employee.employee_no.ilike(kw),
                )
            )
        if params.approval_status is not None:
            conditions.append(LeaveRequest.approval_status == params.approval_status)
        if params.start_date is not None:
            conditions.append(LeaveRequest.start_date >= params.start_date)
        if params.end_date is not None:
            conditions.append(LeaveRequest.end_date <= params.end_date)
        if params.overdue_only:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            conditions.append(LeaveRequest.approval_status == ApprovalStatus.pending)
            conditions.append(LeaveRequest.created_at <= cutoff)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = (
            stmt.order_by(LeaveRequest.created_at.desc())
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def approve(
        db: AsyncSession,
        request_id: int,
        approver_id: int,
        approved: bool,
        note: Optional[str] = None,
    ) -> Optional[LeaveRequest]:
        """
        审批请假申请（通过或拒绝）。

        业务规则：
        - 只有 pending 状态的申请可以被审批（已审批或已取消的申请会抛出 ValueError）
        - 审批通过时：调用 LeaveBalanceService.deduct() 扣减员工假期余额
        - 审批拒绝时：不影响余额，仅更新状态

        参数:
            db: 异步数据库会话
            request_id: 请假申请 ID
            approver_id: 审批人员工 ID（记录在申请上用于追溯）
            approved: True=批准，False=拒绝
            note: 审批意见（可选，仅用于记录，当前未写入 DB）

        返回:
            更新后的 LeaveRequest 对象；若申请不存在则返回 None

        异常:
            ValueError: 申请状态不是 pending（已审批或已取消）
        """
        request = await LeaveRequestService.get(db, request_id)
        if request is None:
            return None
        if request.approval_status != ApprovalStatus.pending:
            raise ValueError("该申请已审批或已取消")

        request.approver_id = approver_id
        request.approved_at = datetime.now(timezone.utc)

        if approved:
            request.approval_status = ApprovalStatus.approved
            leave_type = await LeaveTypeService.get(db, request.leave_type_id)
            if leave_type is None or getattr(leave_type, "quota_limited", getattr(leave_type, "max_days_per_year", None) is not None):
                # 审批通过时才扣减余额（确保余额数据准确反映已批准的使用量）
                await LeaveBalanceService.deduct(
                    db,
                    request.employee_id,
                    request.leave_type_id,
                    request.start_date.year,
                    request.days,
                )
            await LeaveRequestService._refresh_attendance_summaries_for_request(db, request)
        else:
            request.approval_status = ApprovalStatus.rejected

        await db.flush()
        await db.refresh(request)
        return request

    @staticmethod
    async def cancel(
        db: AsyncSession, request_id: int, employee_id: int
    ) -> Optional[LeaveRequest]:
        """
        员工取消自己的请假申请。

        业务规则：
        - 只有申请人本人可以取消自己的申请（employee_id 校验）
        - 已取消的申请直接返回当前对象（幂等）
        - 取消"已批准"的申请时，调用 LeaveBalanceService.restore() 恢复余额
        - 取消"待审批"或"已拒绝"的申请时，不影响余额

        参数:
            db: 异步数据库会话
            request_id: 请假申请 ID
            employee_id: 操作者员工 ID（权限校验：只能取消自己的申请）

        返回:
            更新后的 LeaveRequest 对象；若申请不存在或不属于该员工则返回 None
        """
        request = await LeaveRequestService.get(db, request_id)
        if request is None or request.employee_id != employee_id:
            return None
        if request.approval_status == ApprovalStatus.cancelled:
            return request  # 已取消，幂等返回

        # 记录取消前的状态（用于判断是否需要恢复余额）
        was_approved = request.approval_status == ApprovalStatus.approved
        request.approval_status = ApprovalStatus.cancelled

        # 仅当原状态是"已批准"时才恢复余额（未批准的申请未扣过余额）
        if was_approved:
            leave_type = await LeaveTypeService.get(db, request.leave_type_id)
            if leave_type is None or getattr(leave_type, "quota_limited", getattr(leave_type, "max_days_per_year", None) is not None):
                await LeaveBalanceService.restore(
                    db,
                    request.employee_id,
                    request.leave_type_id,
                    request.start_date.year,
                    request.days,
                )
            await LeaveRequestService._refresh_attendance_summaries_for_request(db, request)

        await db.flush()
        await db.refresh(request)
        return request

    @staticmethod
    async def list_pending_approvals(
        db: AsyncSession, page: int = 1, page_size: int = 20
    ) -> Tuple[List[LeaveRequest], int]:
        """
        获取所有待审批的请假申请列表（HR/主管审批工作台使用）。

        按创建时间升序排列（先进先出，优先处理最早提交的申请）。

        参数:
            db: 异步数据库会话
            page: 页码（从 1 开始）
            page_size: 每页记录数

        返回:
            (待审批申请列表, 总记录数)，按创建时间升序排列
        """
        conditions = [LeaveRequest.approval_status == ApprovalStatus.pending]
        count_result = await db.execute(
            select(func.count()).select_from(LeaveRequest).where(and_(*conditions))
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            select(LeaveRequest)
            .where(and_(*conditions))
            .order_by(LeaveRequest.created_at.asc())  # 先进先出
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total


class LeaveIntegrationService:
    """
    假期对外集成服务。

    这层不直接修改考勤、打卡或审批模块，而是提供稳定契约：
    - 考勤月报可按日期范围读取已批准请假，标记 on_leave。
    - 打卡页可查询某天是否已有请假覆盖，决定是否提示无需打卡。
    - 审批引擎可获取业务摘要和表单字段，做到流程配置与假期规则解耦。
    """

    @staticmethod
    def _half_day_text(start_half: HalfDay, end_half: HalfDay) -> str:
        if start_half == HalfDay.am and end_half == HalfDay.pm:
            return "全天"
        if start_half == HalfDay.am and end_half == HalfDay.am:
            return "上午"
        if start_half == HalfDay.pm and end_half == HalfDay.pm:
            return "下午"
        return "跨半天"

    @staticmethod
    async def approved_leave_segments(
        db: AsyncSession,
        start_date: date,
        end_date: date,
        employee_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        conditions = [
            LeaveRequest.approval_status == ApprovalStatus.approved,
            LeaveRequest.start_date <= end_date,
            LeaveRequest.end_date >= start_date,
        ]
        if employee_id is not None:
            conditions.append(LeaveRequest.employee_id == employee_id)

        result = await db.execute(
            select(LeaveRequest, LeaveType, Employee)
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .join(Employee, Employee.id == LeaveRequest.employee_id)
            .where(and_(*conditions))
            .order_by(LeaveRequest.start_date.asc(), LeaveRequest.id.asc())
        )

        payload: List[Dict[str, Any]] = []
        for request, leave_type, employee in result.all():
            approved_date = request.approved_at.date() if request.approved_at else None
            leave_month = f"{request.start_date.year:04d}-{request.start_date.month:02d}"
            approval_month = f"{approved_date.year:04d}-{approved_date.month:02d}" if approved_date else None
            payload.append(
                {
                    "request_id": request.id,
                    "employee_id": request.employee_id,
                    "employee_name": employee.name,
                    "department_id": employee.department_id,
                    "location_id": employee.location_id,
                    "leave_type_id": request.leave_type_id,
                    "leave_type_code": leave_type.code,
                    "leave_type_name": leave_type.name,
                    "start_date": request.start_date.isoformat(),
                    "end_date": request.end_date.isoformat(),
                    "start_half": getattr(request.start_half, "value", request.start_half),
                    "end_half": getattr(request.end_half, "value", request.end_half),
                    "half_day_text": LeaveIntegrationService._half_day_text(request.start_half, request.end_half),
                    "days": float(request.days or 0),
                    "approved_at": request.approved_at.isoformat() if request.approved_at else None,
                    "leave_month": leave_month,
                    "approval_month": approval_month,
                    "late_approval": bool(approval_month and approval_month != leave_month),
                    "payroll_adjustment_month": approval_month,
                    "leave_unit": getattr(leave_type, "leave_unit", "day"),
                    "time_calc": getattr(leave_type, "time_calc", "natural_day"),
                    "hours_per_day": float(getattr(leave_type, "hours_per_day", 8) or 8),
                    "rounding_direction": getattr(leave_type, "rounding_direction", "none"),
                    "rounding_unit": getattr(leave_type, "rounding_unit", "none"),
                    "is_paid": bool(leave_type.is_paid),
                    "attendance_status": "on_leave",
                    "payroll_paid_leave": bool(leave_type.is_paid),
                }
            )
        return payload

    @staticmethod
    async def approval_context(db: AsyncSession, request_id: int) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            select(LeaveRequest, LeaveType, Employee)
            .join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .join(Employee, Employee.id == LeaveRequest.employee_id)
            .where(LeaveRequest.id == request_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        request, leave_type, employee = row
        return {
            "module": "leave",
            "business_type": "leave_request",
            "business_id": request.id,
            "applicant_id": request.employee_id,
            "applicant_name": employee.name,
            "summary": f"{employee.name}申请{leave_type.name}{float(request.days or 0):g}天",
            "form_data": {
                "leave_type_id": request.leave_type_id,
                "leave_type_code": leave_type.code,
                "leave_type_name": leave_type.name,
                "start_date": request.start_date.isoformat(),
                "end_date": request.end_date.isoformat(),
                "start_half": getattr(request.start_half, "value", request.start_half),
                "end_half": getattr(request.end_half, "value", request.end_half),
                "days": float(request.days or 0),
                "reason": request.reason,
                "proof_url": request.proof_url,
                "requires_proof": bool(leave_type.requires_proof),
                "is_paid": bool(leave_type.is_paid),
                "approval_flow": getattr(leave_type, "approval_flow", None),
            },
            "reserved_hooks": {
                "approval_instance_module": "leave",
                "attendance_consumer": "/api/v1/leave/integrations/attendance-approved",
                "punch_guard_consumer": "/api/v1/leave/integrations/punch-guard",
            },
        }
