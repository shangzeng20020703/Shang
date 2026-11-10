"""
请假服务单元测试
覆盖: 天数计算、日期校验、半天逻辑、余额校验
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.models  # noqa: F401
from app.core.database import Base
from app.models.audit_log import AuditLog
from app.models.employee import Employee, EmployeeStatus
from app.models.leave import HalfDay, LeaveBalance, LeaveType
from app.services.leave import LeaveBalanceService, LeaveIntegrationService, LeaveRequestService, LeaveTypeService
from app.api.v1.leave import _leave_balance_item, _latest_datetime
from app.schemas.leave import LeaveTypeCreate


class AsyncLikeSession:
    def __init__(self, sync_session: Session):
        self._sync = sync_session

    def add(self, obj):
        self._sync.add(obj)

    async def execute(self, *args, **kwargs):
        return self._sync.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self._sync.scalar(*args, **kwargs)

    async def flush(self):
        self._sync.flush()

    async def refresh(self, obj):
        self._sync.refresh(obj)


class TestCalculateLeaveDays:
    """请假天数计算"""

    def test_same_day_same_half_am(self):
        # 同一天同半天 → 0.5天
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 1), HalfDay.am, HalfDay.am
        )
        assert result == Decimal("0.5")

    def test_same_day_same_half_pm(self):
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 1), HalfDay.pm, HalfDay.pm
        )
        assert result == Decimal("0.5")

    def test_same_day_am_to_pm(self):
        # 同一天 am→pm → 1天
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 1), HalfDay.am, HalfDay.pm
        )
        assert result == Decimal("1")

    def test_same_day_pm_to_am_invalid(self):
        # 同一天 pm→am → 无效输入，应抛出异常
        with pytest.raises(ValueError, match="开始半天不能晚于结束半天"):
            LeaveRequestService._calculate_leave_days(
                date(2026, 3, 1), date(2026, 3, 1), HalfDay.pm, HalfDay.am
            )

    def test_two_full_days(self):
        # 3月1日am → 3月2日pm → 2天
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 2), HalfDay.am, HalfDay.pm
        )
        assert result == Decimal("2")

    def test_start_half_pm(self):
        # 3月1日pm → 3月3日pm → 3-0.5=2.5天
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 3), HalfDay.pm, HalfDay.pm
        )
        assert result == Decimal("2.5")

    def test_end_half_am(self):
        # 3月1日am → 3月3日am → 3-0.5=2.5天
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 3), HalfDay.am, HalfDay.am
        )
        assert result == Decimal("2.5")

    def test_start_pm_end_am(self):
        # 3月1日pm → 3月3日am → 3-0.5-0.5=2天
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 3), HalfDay.pm, HalfDay.am
        )
        assert result == Decimal("2")

    def test_one_week(self):
        # 7天整假
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 7), HalfDay.am, HalfDay.pm
        )
        assert result == Decimal("7")

    def test_result_is_decimal(self):
        result = LeaveRequestService._calculate_leave_days(
            date(2026, 3, 1), date(2026, 3, 5), HalfDay.am, HalfDay.pm
        )
        assert isinstance(result, Decimal)


class TestLeaveDateValidation:
    """请假日期校验（通过 _calculate_leave_days 和 create 的前置校验测试）"""

    def test_invalid_same_day_pm_am_raises(self):
        with pytest.raises(ValueError):
            LeaveRequestService._calculate_leave_days(
                date(2026, 3, 5), date(2026, 3, 5), HalfDay.pm, HalfDay.am
            )


class TestWeComLeaveRules:
    """企业微信式假期底层规则"""

    def test_comp_time_balance_item_displays_remaining_hours_not_ratio(self):
        leave_type = SimpleNamespace(
            id=6,
            name="调休",
            code="comp_time",
            leave_unit="hour",
            hours_per_day=Decimal("8.0"),
            quota_limited=True,
            quota_rule_type="cumulative",
            quota_rule={},
            issuance_rule="overtime_auto",
            validity_type="natural_year",
            expire_month_day=None,
            expiration_reminder_enabled=False,
            reminder_before_value=1,
            reminder_before_unit="month",
            reminder_notify_employee=True,
            reminder_notify_manager=False,
            scope_type="all",
            scope_rules={},
        )
        employee = SimpleNamespace(
            id=9,
            department_id=10,
            hire_date=date(2020, 1, 1),
            first_work_date=date(2020, 1, 1),
        )
        balance = SimpleNamespace(
            total_days=Decimal("0.625"),
            used_days=Decimal("0"),
            remaining_days=Decimal("0.375"),
            updated_at=None,
        )

        item = _leave_balance_item(leave_type, employee, balance, 2026)

        assert item["actual_text"] == "5小时"
        assert item["remaining_text"] == "3小时"
        assert item["balance_text"] == "3小时"

    def test_non_annual_limited_balance_item_displays_remaining_amount_not_ratio(self):
        leave_type = SimpleNamespace(
            id=2,
            name="病假",
            code="sick",
            leave_unit="day",
            hours_per_day=Decimal("8.0"),
            quota_limited=True,
            quota_rule_type="fixed",
            quota_rule={},
            max_days_per_year=Decimal("5.0"),
            issuance_rule="year_start",
            issuance_month_day="01-01",
            validity_type="natural_year",
            expire_month_day=None,
            expiration_reminder_enabled=False,
            reminder_before_value=1,
            reminder_before_unit="month",
            reminder_notify_employee=True,
            reminder_notify_manager=False,
            scope_type="all",
            scope_rules={},
        )
        employee = SimpleNamespace(
            id=9,
            department_id=10,
            first_work_date=date(2020, 1, 1),
            hire_date=date(2020, 1, 1),
        )
        balance = SimpleNamespace(
            total_days=Decimal("5"),
            used_days=Decimal("0"),
            remaining_days=Decimal("5"),
            updated_at=None,
        )

        item = _leave_balance_item(leave_type, employee, balance, 2026)

        assert item["balance_text"] == "5天"

    def test_default_wecom_leave_types_are_seeded_catalog(self):
        rows = LeaveTypeService.default_wecom_type_payloads()
        names = [row["name"] for row in rows]
        assert {"事假", "调休", "病假", "年假", "产假", "陪产假", "婚假", "例假", "丧假", "哺乳假"}.issubset(set(names))
        assert "病假（长期）" not in names
        assert "产检假" not in names
        assert "病假（通用）" not in names
        annual = next(row for row in rows if row["code"] == "annual")
        assert annual["leave_unit"] == "half_day"
        assert annual["time_calc"] == "workday"
        assert annual["quota_rule_type"] == "work_tenure"
        marriage = next(row for row in rows if row["code"] == "marriage")
        assert marriage["leave_unit"] == "day"
        assert marriage["time_calc"] == "natural_day"
        assert marriage["quota_limited"] is False
        assert marriage["max_days_per_year"] is None
        assert marriage["validity_type"] == "forever"
        maternity = next(row for row in rows if row["code"] == "maternity")
        assert maternity["leave_unit"] == "day"
        assert maternity["time_calc"] == "workday"
        assert maternity["quota_limited"] is False
        assert maternity["is_paid"] is False
        assert maternity["issuance_rule"] == "year_start"
        comp_time = next(row for row in rows if row["code"] == "comp_time")
        assert comp_time["leave_unit"] == "hour"
        assert comp_time["is_paid"] is False
        assert comp_time["quota_limited"] is False
        assert comp_time["issuance_rule"] == "year_start"
        assert comp_time["time_calc"] == "workday"
        assert comp_time["validity_type"] == "from_issue_date"
        assert comp_time["extension_unit"] == "month"
        sick = next(row for row in rows if row["code"] == "sick")
        assert sick["leave_unit"] == "hour"
        assert sick["time_calc"] == "workday"
        assert sick["quota_limited"] is False
        assert sick["max_days_per_year"] is None
        assert sick["issuance_rule"] == "manual"
        assert sick["quota_rule_type"] == "company_tenure"
        assert sick["validity_type"] == "from_issue_date"
        assert sick["quota_rule"]["validity_years"] == 1
        assert sick["extension_unit"] == "month"
        paternity = next(row for row in rows if row["code"] == "paternity")
        assert paternity["leave_unit"] == "day"
        assert paternity["time_calc"] == "workday"
        assert paternity["quota_limited"] is False
        assert paternity["max_days_per_year"] is None
        assert paternity["issuance_rule"] == "manual"
        assert paternity["validity_type"] == "forever"
        personal = next(row for row in rows if row["code"] == "personal")
        assert personal["leave_unit"] == "hour"
        assert personal["time_calc"] == "workday"
        assert personal["is_paid"] is False
        assert personal["quota_limited"] is False
        assert personal["new_employee_rule"] == "after_regularization"
        period_leave = next(row for row in rows if row["code"] == "period_leave")
        assert period_leave["leave_unit"] == "half_day"
        assert period_leave["time_calc"] == "workday"
        assert period_leave["quota_limited"] is False
        breastfeeding = next(row for row in rows if row["code"] == "breastfeeding")
        assert breastfeeding["leave_unit"] == "hour"
        assert breastfeeding["time_calc"] == "workday"
        assert breastfeeding["quota_limited"] is False

    def test_legacy_paternity_seed_is_upgraded_to_dingtalk_observed_defaults(self):
        default_payload = next(row for row in LeaveTypeService.default_wecom_type_payloads() if row["code"] == "paternity")
        leave_type = SimpleNamespace(
            code="paternity",
            name="陪产假",
            leave_unit="day",
            time_calc="natural_day",
            quota_limited=True,
            max_days_per_year=Decimal("10.0"),
            issuance_rule="manual",
            quota_rule_type="fixed",
            quota_rule={"fixed_days": 10},
            expire_month_day="01-15",
            allow_validity_extension=True,
            validity_type="fixed_date",
        )

        LeaveTypeService._patch_observed_default_if_needed(leave_type, default_payload)

        assert leave_type.time_calc == "workday"
        assert leave_type.quota_limited is False
        assert leave_type.max_days_per_year is None
        assert leave_type.validity_type == "forever"

    def test_leave_type_schema_accepts_dingtalk_half_day_unit_and_rounding_fields(self):
        data = LeaveTypeCreate(
            name="例假",
            code="period_leave",
            leave_unit="half_day",
            rounding_direction="up",
            rounding_unit="half_day",
        )

        assert data.leave_unit == "half_day"
        assert data.rounding_direction == "up"

    def test_sick_payload_normalizes_to_supported_quota_and_validity_rules(self):
        payload = LeaveTypeService._normalize_payload(
            {
                "name": "病假",
                "code": "sick",
                "is_paid": True,
                "requires_proof": True,
                "max_days_per_year": Decimal("5.0"),
                "quota_limited": True,
                "quota_rule_type": "grade",
                "quota_rule": {},
                "validity_type": "natural_year",
            }
        )

        assert payload["quota_rule_type"] == "company_tenure"
        assert payload["leave_unit"] == "hour"
        assert payload["time_calc"] == "workday"
        assert payload["validity_type"] == "from_issue_date"
        assert payload["quota_rule"]["validity_years"] == 1
        assert payload["extension_unit"] == "month"

    def test_sick_payload_can_keep_unlimited_balance_like_dingtalk_list(self):
        payload = LeaveTypeService._normalize_payload(
            {
                "name": "病假",
                "code": "sick",
                "is_paid": True,
                "requires_proof": True,
                "max_days_per_year": None,
                "quota_limited": False,
                "quota_rule_type": "company_tenure",
                "quota_rule": {"validity_years": 1},
                "issuance_rule": "manual",
            }
        )

        assert payload["quota_limited"] is False
        assert payload["max_days_per_year"] is None
        assert payload["leave_unit"] == "hour"
        assert payload["time_calc"] == "workday"
        assert payload["issuance_rule"] == "manual"

    def test_comp_time_payload_keeps_dingtalk_limited_rule_instead_of_forcing_overtime(self):
        payload = LeaveTypeService._normalize_payload(
            {
                "name": "调休",
                "code": "comp_time",
                "is_paid": False,
                "max_days_per_year": Decimal("5.0"),
                "quota_limited": True,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 5, "validity_years": 1},
                "issuance_rule": "monthly",
                "issuance_month_day": "01-01",
                "validity_type": "from_issue_date",
                "extension_unit": "month",
            }
        )

        assert payload["leave_unit"] == "hour"
        assert payload["time_calc"] == "workday"
        assert payload["is_paid"] is False
        assert payload["quota_limited"] is True
        assert payload["quota_rule_type"] == "fixed"
        assert payload["issuance_rule"] == "monthly"

    def test_comp_time_payload_can_keep_overtime_auto_when_user_selects_it(self):
        payload = LeaveTypeService._normalize_payload(
            {
                "name": "调休",
                "code": "comp_time",
                "is_paid": False,
                "max_days_per_year": Decimal("5.0"),
                "quota_limited": True,
                "quota_rule_type": "overtime",
                "quota_rule": {},
                "issuance_rule": "overtime_auto",
            }
        )

        assert payload["quota_rule_type"] == "overtime"
        assert payload["issuance_rule"] == "overtime_auto"
        assert payload["quota_rule"]["overtime_hours_per_day"] == 8

    def test_maternity_payload_keeps_video_defaults_when_unlimited(self):
        payload = LeaveTypeService._normalize_payload(
            {
                "name": "产休假",
                "code": "maternity",
                "is_paid": False,
                "max_days_per_year": Decimal("98.0"),
                "quota_limited": False,
                "quota_rule_type": "fixed",
                "quota_rule": {"fixed_days": 98},
                "time_calc": "natural_day",
            }
        )

        assert payload["name"] == "产假"
        assert payload["leave_unit"] == "day"
        assert payload["time_calc"] == "workday"
        assert payload["quota_limited"] is False
        assert payload["max_days_per_year"] is None
        assert payload["validity_type"] == "forever"

    def test_social_plus_company_quota_adds_two_tenure_rule_results(self):
        leave_type = SimpleNamespace(
            quota_limited=True,
            quota_rule_type="social_plus_company",
            quota_rule={
                "social_tenure_brackets": [
                    {"min_years": 0, "max_years": 1, "days": 5},
                    {"min_years": 1, "max_years": None, "days": 8},
                ],
                "company_tenure_brackets": [
                    {"min_years": 0, "max_years": 1, "days": 2},
                    {"min_years": 1, "max_years": None, "days": 3},
                ],
            },
            issuance_rule="year_start",
            issuance_month_day="01-01",
        )
        employee = SimpleNamespace(first_work_date=date(2020, 1, 1), hire_date=date(2024, 1, 1))

        result = LeaveBalanceService.calculate_quota_for_employee(leave_type, employee, 2026)

        assert result == Decimal("11")

    def test_employee_scope_matches_only_configured_members(self):
        leave_type = SimpleNamespace(
            scope_type="employee",
            scope_rules={"employee_ids": [2, 5]},
        )

        assert LeaveRequestService._employee_matches_scope(leave_type, SimpleNamespace(id=5)) is True
        assert LeaveRequestService._employee_matches_scope(leave_type, SimpleNamespace(id=6)) is False

    def test_employee_scope_without_members_matches_no_one(self):
        leave_type = SimpleNamespace(scope_type="employee", scope_rules={"employee_ids": []})

        assert LeaveRequestService._employee_matches_scope(leave_type, SimpleNamespace(id=5)) is False

    def test_department_or_employee_scope_matches_either_condition(self):
        leave_type = SimpleNamespace(
            scope_type="department_or_employee",
            scope_rules={"department_ids": [3], "employee_ids": [9]},
        )

        assert LeaveRequestService._employee_matches_scope(
            leave_type, SimpleNamespace(id=4, department_id=3)
        ) is True
        assert LeaveRequestService._employee_matches_scope(
            leave_type, SimpleNamespace(id=9, department_id=8)
        ) is True
        assert LeaveRequestService._employee_matches_scope(
            leave_type, SimpleNamespace(id=4, department_id=8)
        ) is False

    def test_department_or_employee_scope_matches_either_dimension(self):
        leave_type = SimpleNamespace(
            scope_type="department_or_employee",
            scope_rules={"department_ids": [3], "employee_ids": [9]},
        )

        assert LeaveRequestService._employee_matches_scope(leave_type, SimpleNamespace(id=8, department_id=3)) is True
        assert LeaveRequestService._employee_matches_scope(leave_type, SimpleNamespace(id=9, department_id=4)) is True
        assert LeaveRequestService._employee_matches_scope(leave_type, SimpleNamespace(id=8, department_id=4)) is False

    def test_tenure_quota_uses_configured_brackets(self):
        leave_type = SimpleNamespace(
            quota_limited=True,
            quota_rule_type="work_tenure",
            quota_rule={
                "tenure_basis": "social_tenure",
                "tenure_brackets": [
                    {"min_years": 0, "max_years": 1, "days": 0},
                    {"min_years": 1, "max_years": 10, "days": 5},
                    {"min_years": 10, "max_years": 20, "days": 10},
                    {"min_years": 20, "max_years": None, "days": 15},
                ],
            },
        )
        employee = SimpleNamespace(first_work_date=date(2010, 1, 1), hire_date=date(2024, 1, 1))

        result = LeaveBalanceService.calculate_quota_for_employee(leave_type, employee, 2026)

        assert result == Decimal("10")

    def test_company_tenure_quota_uses_employee_hire_date(self):
        leave_type = SimpleNamespace(
            quota_limited=True,
            quota_rule_type="company_tenure",
            quota_rule={
                "tenure_brackets": [
                    {"min_years": 0, "max_years": 1, "days": 0},
                    {"min_years": 1, "max_years": 10, "days": 5},
                    {"min_years": 10, "max_years": 20, "days": 10},
                    {"min_years": 20, "max_years": None, "days": 15},
                ],
            },
        )
        employee = SimpleNamespace(first_work_date=date(2010, 1, 1), hire_date=date(2024, 1, 1))

        result = LeaveBalanceService.calculate_quota_for_employee(leave_type, employee, 2026)

        assert result == Decimal("5")

    def test_quota_starts_from_hire_date_when_rule_allows_from_onboarding(self):
        leave_type = SimpleNamespace(
            name="病假",
            quota_limited=True,
            quota_rule_type="fixed",
            quota_rule={"fixed_days": 5},
            max_days_per_year=Decimal("5"),
            issuance_rule="year_start",
            issuance_month_day="01-01",
            new_employee_rule="none",
        )
        employee = SimpleNamespace(hire_date=date(2026, 5, 12), probation_end_date=date(2026, 8, 12))

        before = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 5, 11)
        )
        after = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 5, 12)
        )

        assert before == Decimal("0")
        assert after == Decimal("5")

    def test_quota_starts_from_regularization_date_when_rule_requires_regularization(self):
        leave_type = SimpleNamespace(
            name="病假",
            quota_limited=True,
            quota_rule_type="fixed",
            quota_rule={"fixed_days": 5},
            max_days_per_year=Decimal("5"),
            issuance_rule="year_start",
            issuance_month_day="01-01",
            new_employee_rule="after_regularization",
        )
        employee = SimpleNamespace(
            hire_date=date(2026, 5, 12),
            probation_end_date=date(2026, 8, 12),
            status="待入职",
            employment_type="试用",
        )

        before = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 8, 11)
        )
        after = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 8, 12)
        )

        assert before == Decimal("0")
        assert after == Decimal("5")

    def test_quota_starts_after_configured_months_when_rule_requires_tenure_months(self):
        leave_type = SimpleNamespace(
            name="病假",
            quota_limited=True,
            quota_rule_type="fixed",
            quota_rule={"fixed_days": 5},
            max_days_per_year=Decimal("5"),
            issuance_rule="year_start",
            issuance_month_day="01-01",
            new_employee_rule="after_months",
            new_employee_limit_months=3,
        )
        employee = SimpleNamespace(hire_date=date(2026, 1, 15), probation_end_date=None)

        before = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 4, 14)
        )
        after = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 4, 15)
        )

        assert before == Decimal("0")
        assert after == Decimal("5")

    def test_balance_item_hides_stale_quota_before_rule_effective_date(self):
        leave_type = SimpleNamespace(
            id=2,
            name="病假",
            code="sick",
            leave_unit="day",
            hours_per_day=Decimal("8.0"),
            quota_limited=True,
            quota_rule_type="fixed",
            quota_rule={"fixed_days": 5},
            max_days_per_year=Decimal("5"),
            issuance_rule="year_start",
            issuance_month_day="01-01",
            validity_type="natural_year",
            expire_month_day=None,
            expiration_reminder_enabled=False,
            reminder_before_value=1,
            reminder_before_unit="month",
            reminder_notify_employee=True,
            reminder_notify_manager=False,
            scope_type="all",
            scope_rules={},
            new_employee_rule="after_regularization",
        )
        employee = SimpleNamespace(
            id=9,
            department_id=10,
            hire_date=date(2026, 5, 12),
            probation_end_date=date(2099, 8, 12),
            status="待入职",
            employment_type="试用",
        )
        balance = SimpleNamespace(
            total_days=Decimal("5"),
            used_days=Decimal("0"),
            remaining_days=Decimal("5"),
            updated_at=None,
        )

        item = _leave_balance_item(leave_type, employee, balance, 2026)

        assert item["actual_days"] == 0.0
        assert item["remaining_days"] == 0.0
        assert item["balance_text"] == "无"
        assert item["is_effective"] is False

    def test_work_tenure_quota_uses_employee_first_work_date(self):
        leave_type = SimpleNamespace(
            quota_limited=True,
            quota_rule_type="work_tenure",
            quota_rule={
                "tenure_brackets": [
                    {"min_years": 0, "max_years": 1, "days": 0},
                    {"min_years": 1, "max_years": 10, "days": 5},
                    {"min_years": 10, "max_years": 20, "days": 10},
                    {"min_years": 20, "max_years": None, "days": 15},
                ],
            },
        )
        employee = SimpleNamespace(first_work_date=date(2000, 1, 1), hire_date=date(2024, 1, 1))

        result = LeaveBalanceService.calculate_quota_for_employee(leave_type, employee, 2026)

        assert result == Decimal("15")

    def test_social_plus_company_quota_adds_two_tenure_rules(self):
        brackets = [
            {"min_years": 0, "max_years": 1, "days": 0},
            {"min_years": 1, "max_years": 10, "days": 5},
            {"min_years": 10, "max_years": None, "days": 10},
        ]
        leave_type = SimpleNamespace(
            quota_limited=True,
            quota_rule_type="social_plus_company",
            quota_rule={
                "social_tenure_brackets": brackets,
                "company_tenure_brackets": brackets,
            },
            issuance_rule="year_start",
            issuance_month_day="01-01",
        )
        employee = SimpleNamespace(first_work_date=date(2010, 1, 1), hire_date=date(2024, 1, 1))

        result = LeaveBalanceService.calculate_quota_for_employee(leave_type, employee, 2026)

        assert result == Decimal("15")

    def test_tenure_brackets_are_normalized_as_continuous_ranges(self):
        result = LeaveTypeService._normalize_tenure_brackets(
            [
                {"min_years": 0, "max_years": 1, "days": 0},
                {"min_years": 8, "max_years": 10, "days": 5},
                {"min_years": 99, "max_years": None, "days": 8},
            ]
        )

        assert result == [
            {"min_years": 0.0, "max_years": 1.0, "days": 0.0},
            {"min_years": 1.0, "max_years": 10.0, "days": 5.0},
            {"min_years": 10.0, "max_years": None, "days": 8.0},
        ]

    def test_monthly_issuance_prorates_quota_by_issued_months(self):
        leave_type = SimpleNamespace(
            quota_limited=True,
            quota_rule_type="fixed",
            quota_rule={"fixed_days": 12},
            max_days_per_year=Decimal("12"),
            issuance_rule="monthly",
            issuance_month_day="01-01",
        )
        employee = SimpleNamespace(first_work_date=date(2020, 1, 1), hire_date=date(2026, 3, 8))

        result = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 4, 27)
        )

        assert result == Decimal("2.0")

    def test_actual_work_time_prorates_quota_by_employee_hire_date(self):
        leave_type = SimpleNamespace(
            quota_limited=True,
            quota_rule_type="fixed",
            quota_rule={"fixed_days": 12, "actual_work_time_enabled": True},
            max_days_per_year=Decimal("12"),
            issuance_rule="year_start",
            issuance_month_day="01-01",
        )
        employee = SimpleNamespace(first_work_date=date(2020, 1, 1), hire_date=date(2026, 7, 1))

        result = LeaveBalanceService.calculate_quota_for_employee(
            leave_type, employee, 2026, as_of=date(2026, 12, 31)
        )

        assert result == Decimal("6.0")

    def test_validity_from_issue_date_applies_extension_text_rule(self):
        leave_type = SimpleNamespace(
            validity_type="from_issue_date",
            issuance_month_day="01-01",
            quota_rule={"validity_years": 1},
            allow_validity_extension=True,
            extension_value=1,
            extension_unit="month",
        )

        assert LeaveBalanceService.calculate_expiry_date(leave_type, 2026) == date(2027, 1, 31)

    def test_new_employee_after_regularization_blocks_probation_employee(self):
        leave_type = SimpleNamespace(
            name="事假",
            new_employee_rule="after_regularization",
            new_employee_limit_enabled=True,
        )
        employee = SimpleNamespace(status="试用期", employment_type="试用", probation_end_date=date(2026, 7, 1))

        with pytest.raises(ValueError, match="转正后"):
            LeaveRequestService._validate_new_employee_limit(leave_type, employee, date(2026, 5, 1))

    def test_duration_rounding_supports_hour_granularity(self):
        leave_type = SimpleNamespace(
            rounding_direction="up",
            rounding_unit="hour",
            hours_per_day=Decimal("8.0"),
        )

        result = LeaveRequestService._apply_duration_rounding(leave_type, Decimal("0.3"))

        assert result == Decimal("0.4")

    def test_retroactive_rule_blocks_after_month_limit(self):
        violation = LeaveRequestService._retroactive_rule_violation(
            date(2026, 1, 20),
            date(2026, 3, 1),
            {
                "leave_retroactive_limit_enabled": True,
                "leave_retroactive_limit_months": 1,
                "leave_retroactive_cutoff_day": 0,
            },
        )

        assert "1个月补交期限" in violation

    def test_retroactive_rule_blocks_after_cutoff_day(self):
        violation = LeaveRequestService._retroactive_rule_violation(
            date(2026, 2, 20),
            date(2026, 3, 11),
            {
                "leave_retroactive_limit_enabled": True,
                "leave_retroactive_limit_months": 1,
                "leave_retroactive_cutoff_day": 10,
            },
        )

        assert "每月10日后" in violation

    def test_retroactive_rule_allows_current_month(self):
        violation = LeaveRequestService._retroactive_rule_violation(
            date(2026, 3, 1),
            date(2026, 3, 11),
            {
                "leave_retroactive_limit_enabled": True,
                "leave_retroactive_limit_months": 0,
                "leave_retroactive_cutoff_day": 10,
            },
        )

        assert violation is None

    def test_latest_datetime_accepts_mixed_timezone_values(self):
        naive = datetime(2026, 4, 28, 9, 0, 0)
        aware = datetime(2026, 4, 28, 10, 0, 0, tzinfo=timezone.utc)

        assert _latest_datetime([naive, aware]) is aware

    @pytest.mark.asyncio
    async def test_workday_time_calc_excludes_weekend_and_holidays(self):
        db = AsyncMock()
        employee = SimpleNamespace(location_id=None)
        leave_type = SimpleNamespace(time_calc="workday")

        async def fake_day_type(_db, target_date, _location_id=None):
            from app.models.attendance import DayType

            if target_date == date(2026, 4, 6):
                return DayType.holiday
            return DayType.weekend if target_date.weekday() >= 5 else DayType.workday

        with patch("app.services.attendance.WorkCalendarService.get_day_type", new=fake_day_type):
            result = await LeaveRequestService._calculate_leave_days_by_rule(
                db,
                employee,
                leave_type,
                date(2026, 4, 3),
                date(2026, 4, 7),
                HalfDay.am,
                HalfDay.pm,
            )

        assert result == Decimal("2")

    @pytest.mark.asyncio
    async def test_approval_context_exposes_reserved_hooks(self):
        request = SimpleNamespace(
            id=9,
            employee_id=7,
            leave_type_id=1,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            start_half=HalfDay.am,
            end_half=HalfDay.pm,
            days=Decimal("2"),
            reason="家庭事务请假",
            proof_url=None,
        )
        leave_type = SimpleNamespace(
            id=1,
            code="personal",
            name="事假",
            requires_proof=False,
            is_paid=False,
            approval_flow="部门主管审批",
        )
        employee = SimpleNamespace(id=7, name="张三")
        result = SimpleNamespace(one_or_none=lambda: (request, leave_type, employee))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)

        context = await LeaveIntegrationService.approval_context(db, 9)

        assert context["module"] == "leave"
        assert context["form_data"]["leave_type_code"] == "personal"
        assert context["reserved_hooks"]["attendance_consumer"].endswith("/attendance-approved")

    @pytest.mark.asyncio
    async def test_rule_based_sync_keeps_manual_adjusted_annual_balance(self, tmp_path: Path):
        db_path = tmp_path / "manual-adjusted-annual-balance.sqlite3"
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

        try:
            with session_factory() as sync_session:
                db = AsyncLikeSession(sync_session)
                employee = Employee(
                    employee_no="TG-MANUAL-ANNUAL-001",
                    name="年假调整员工",
                    phone="13900009001",
                    status=EmployeeStatus.ACTIVE.value,
                    is_active=True,
                    hire_date=date(2026, 1, 1),
                    first_work_date=date(2026, 1, 1),
                )
                annual_type = LeaveType(
                    name="年假",
                    code="annual",
                    is_paid=True,
                    max_days_per_year=Decimal("5.0"),
                    requires_proof=False,
                    deduct_from="annual_leave",
                    policy_region="all",
                    is_active=True,
                    quota_limited=True,
                    quota_rule_type="work_tenure",
                    quota_rule={
                        "tenure_brackets": [
                            {"min_years": 0, "max_years": 1, "days": 0},
                            {"min_years": 1, "max_years": 10, "days": 5},
                        ]
                    },
                    issuance_rule="year_start",
                    issuance_month_day="01-01",
                    validity_type="natural_year",
                    leave_unit="day",
                    time_calc="workday",
                    hours_per_day=Decimal("8.0"),
                    scope_type="all",
                    scope_rules={},
                )
                sync_session.add_all([employee, annual_type])
                sync_session.flush()

                balance = LeaveBalance(
                    employee_id=employee.id,
                    leave_type_id=annual_type.id,
                    year=2026,
                    total_days=Decimal("5.0"),
                    used_days=Decimal("0.0"),
                    remaining_days=Decimal("5.0"),
                    expired_days=Decimal("0.0"),
                )
                sync_session.add(balance)
                sync_session.flush()

                sync_session.add(
                    AuditLog(
                        operator_id=99,
                        operator_name="HR",
                        module="leave",
                        action="balance_adjust",
                        resource_id=balance.id,
                        resource_type="LeaveBalance",
                        after_data=json.dumps(
                            {
                                "employee_id": employee.id,
                                "leave_type_id": annual_type.id,
                                "year": 2026,
                                "adjustment": 5.0,
                                "reason": "新员工年假补录",
                                "remaining_days": 5.0,
                                "total_days": 5.0,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
                sync_session.flush()

                assert LeaveBalanceService.calculate_quota_for_employee(
                    annual_type, employee, 2026, as_of=date(2026, 6, 9)
                ) == Decimal("0")

                await LeaveBalanceService.sync_rule_based_balances(
                    db,
                    year=2026,
                    employee_ids=[employee.id],
                    leave_type_ids=[annual_type.id],
                    as_of=date(2026, 6, 9),
                )
                sync_session.flush()
                sync_session.refresh(balance)

                assert balance.total_days == Decimal("5.0")
                assert balance.used_days == Decimal("0.0")
                assert balance.remaining_days == Decimal("5.0")
        finally:
            engine.dispose()
