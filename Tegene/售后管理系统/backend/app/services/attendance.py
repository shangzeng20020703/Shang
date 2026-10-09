"""
考勤模块业务逻辑
================

本模块是售后管理系统考勤功能的核心服务层，负责以下全部业务逻辑：

1. **考勤规则管理** (AttendanceRuleService)
   - 增删改查考勤规则，支持按地点/激活状态筛选
   - 规则包含：工时制类型、上下班时间、弹性分钟数、GPS/WiFi/照片校验配置

2. **排班管理** (ShiftScheduleService)
   - 为员工创建单条或批量排班（具有 upsert 语义，已排班的日期会被覆盖）
   - 支持按员工、部门、日期范围灵活查询

3. **打卡与考勤记录** (AttendanceRecordService)
   - 上班打卡 clock_in()、下班打卡 clock_out()
   - 打卡时自动执行 GPS/WiFi/照片/模拟定位校验
   - 下班打卡后自动计算实际工时和加班时长，并判定考勤状态
   - 支持考勤记录分页查询、异常记录列表

4. **工作日历** (WorkCalendarService)
   - 管理节假日、调休、特殊工作日等特殊日类型标记
   - get_day_type() 支持"地点特定 > 全局 > 按星期推断"三级回退逻辑

5. **月度汇总** (MonthSummaryService)
   - generate() 按月汇总所有员工考勤，统计出勤/迟到/早退/旷工/加班数据
   - 支持员工本人确认、HR 锁定月报（锁定后不可修改）
   - 月报数据直接供薪资模块消费

6. **考勤报表导出** (AttendanceReportService)
   - 生成 Excel 格式的考勤月报，支持按部门筛选，含表头美化和交替行色

工时制说明：
- **弹性工时** (flexible)：北京研发团队，参考时间 9:00-18:00，有打卡即视为正常
- **标准工时** (standard)：东莞工厂，8:00-17:30，严格按规则判定迟到/早退
- **综合工时** (comprehensive)：驻场人员，按周期核算，此处记录原始打卡数据

加班系数（由薪资模块消费月汇总时使用）：
- 工作日加班：× 1.5
- 休息日加班：× 2.0
- 法定节假日加班：× 3.0

依赖关系：
- 向上：被 app/api/v1/attendance.py 调用
- 向下：读写 app/models/attendance.py 中的 ORM 模型
"""

import asyncio
import hashlib
import io
import json
import logging
import math
import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.core.sql_compat import order_by_nulls_last_desc
from app.models.attendance import (
    AttendanceEffect,
    AttendanceMonthlyReportSnapshot,
    AttendanceMonthSummary,
    AttendancePunchTimeRecord,
    AttendanceRecord,
    AttendanceRule,
    AttendanceRuleLog,
    AttendanceStatus,
    ClockSource,
    CorrectionStatus,
    DayType,
    MonthlySummaryStatus,
    PunchCorrectionRequest,
    ShiftSchedule,
    ShiftType,
    WorkCalendar,
    WorkHourType,
)
from app.models.audit_log import AuditLog
from app.models.approval import ApprovalFlow, ApprovalNode, ApprovalType
from app.models.employee import Employee, EmployeeStatus
from app.models.employee_role import EmployeeRole
from app.models.leave import LeaveBalance, LeaveType
from app.models.organization import Department
from app.models.overtime import OvertimeRequest
from app.schemas.attendance import (
    AttendanceAppealCreate,
    AttendanceAppealReview,
    AttendanceOutsideApprovalCreate,
    AttendanceOutsideApprovalOut,
    AttendanceRecordQuery,
    AttendanceRuleCreate,
    AttendanceRuleUpdate,
    ClockInRequest,
    ClockOutRequest,
    ClockValidateRequest,
    CorrectionApproval,
    CorrectionRequest,
    MonthSummaryGenerateRequest,
    OvertimeApplicationCreate,
    PunchCorrectionCreate,
    PunchCorrectionEligibilityRequest,
    ShiftScheduleBatchCreate,
    ShiftScheduleCreate,
    ShiftScheduleUpdate,
    WorkCalendarCreate,
    WorkCalendarUpdate,
)
from app.schemas.approval import ApprovalInstanceCreate

logger = logging.getLogger(__name__)


OFFICIAL_HOLIDAY_PLANS: Dict[int, Dict[str, Any]] = {
    # 国办发明电〔2024〕12号
    2025: {
        "holidays": [
            ("元旦", date(2025, 1, 1), date(2025, 1, 1)),
            ("春节", date(2025, 1, 28), date(2025, 2, 4)),
            ("清明节", date(2025, 4, 4), date(2025, 4, 6)),
            ("劳动节", date(2025, 5, 1), date(2025, 5, 5)),
            ("端午节", date(2025, 5, 31), date(2025, 6, 2)),
            ("国庆节/中秋节", date(2025, 10, 1), date(2025, 10, 8)),
        ],
        "makeup_workdays": {
            date(2025, 1, 26),
            date(2025, 2, 8),
            date(2025, 4, 27),
            date(2025, 9, 28),
            date(2025, 10, 11),
        },
    },
    # 国办发明电〔2025〕7号
    2026: {
        "holidays": [
            ("元旦", date(2026, 1, 1), date(2026, 1, 3)),
            ("春节", date(2026, 2, 15), date(2026, 2, 23)),
            ("清明节", date(2026, 4, 4), date(2026, 4, 6)),
            ("劳动节", date(2026, 5, 1), date(2026, 5, 5)),
            ("端午节", date(2026, 6, 19), date(2026, 6, 21)),
            ("中秋节", date(2026, 9, 25), date(2026, 9, 27)),
            ("国庆节", date(2026, 10, 1), date(2026, 10, 7)),
        ],
        "makeup_workdays": {
            date(2026, 2, 14),
            date(2026, 2, 28),
            date(2026, 5, 9),
            date(2026, 9, 20),
            date(2026, 10, 10),
        },
    },
}


class AttendanceRuleService:
    """
    考勤规则管理服务。

    考勤规则定义了一组员工的上下班时间、弹性宽限分钟数、工时制类型，
    以及 GPS/WiFi/照片等打卡校验要求。每个地点通常对应一条或多条规则。

    典型规则示例：
    - 北京研发（弹性工时）：9:00-18:00，flexible_minutes=30，仅 GPS 校验
    - 东莞工厂（标准工时）：8:00-17:30，flexible_minutes=5，GPS + WiFi 双校验
    """

    @staticmethod
    def _operator(current_user: Optional[Employee]) -> tuple[Optional[int], Optional[str]]:
        if not current_user:
            return None, "system"
        return getattr(current_user, "id", None), getattr(current_user, "name", "system")

    @staticmethod
    async def _write_log(
        db: AsyncSession,
        rule_id: int,
        action: str,
        content: str,
        current_user: Optional[Employee] = None,
    ) -> None:
        operator_id, operator_name = AttendanceRuleService._operator(current_user)
        db.add(
            AttendanceRuleLog(
                rule_id=rule_id,
                action=action,
                operator_id=operator_id,
                operator_name=operator_name,
                content=content,
            )
        )

    @staticmethod
    def _approval_item_enabled(rule: AttendanceRule, item_key: str, extra: Dict[str, Any]) -> bool:
        if not getattr(rule, "is_active", False):
            return False
        if item_key == "patch_apply":
            return bool(extra.get("enable_patch_apply"))
        if item_key == "approve_punch":
            return bool(extra.get("enable_approve_punch"))
        if item_key == "leave_punch":
            return bool(extra.get("leave_need_punch_before_after"))
        if item_key == "overtime":
            return bool(extra.get("enable_overtime"))
        return False

    @staticmethod
    def _approval_item_description(item_key: str, extra: Dict[str, Any]) -> str:
        if item_key == "patch_apply":
            patch_types = extra.get("patch_types") if isinstance(extra.get("patch_types"), list) else []
            patch_type_text = "、".join(str(item) for item in patch_types if str(item or "").strip()) or "未配置"
            return (
                f"补卡申请：可补类型={patch_type_text}；"
                f"日期范围={extra.get('patch_time_limit') or '无限制'}；"
                f"每月次数={extra.get('patch_month_limit') or '无限制'}；"
                f"截止日={extra.get('patch_deadline') or '不设置'}"
            )
        if item_key == "approve_punch":
            return "审批打卡启用：定位不准等原因无法打卡时，可发起审批打卡流程"
        if item_key == "leave_punch":
            return (
                f"请假时打卡：{extra.get('leave_punch_rule') or '无需打卡'}；"
                f"离岗前最早：{extra.get('leave_before_max_advance') or '无限制'}；"
                f"返岗后最晚：{extra.get('leave_after_max_delay') or '无限制'}"
            )
        if item_key == "overtime":
            overtime_policy = extra.get("overtime_policy") if isinstance(extra.get("overtime_policy"), dict) else {}
            workday_method = (overtime_policy.get("workday") or {}).get("calc_method", "by_approval")
            restday_method = (overtime_policy.get("restday") or {}).get("calc_method", "by_approval")
            holiday_method = (overtime_policy.get("holiday") or {}).get("calc_method", "by_approval")
            return (
                f"加班审批口径：工作日={workday_method}；休息日={restday_method}；"
                f"节假日={holiday_method}"
            )
        return "考勤审批配置"

    @staticmethod
    async def _ensure_rule_approval_entry(
        db: AsyncSession,
        rule: AttendanceRule,
        item_key: str,
        item_title: str,
        enabled: bool,
        description: str,
    ) -> Dict[str, Any]:
        module_code = f"attendance_rule_{rule.id}_{item_key}"
        type_name = f"{rule.name}-{item_title}审批"
        flow_name = f"{type_name}流程"

        approval_type_result = await db.execute(
            select(ApprovalType).where(ApprovalType.business_code == module_code).order_by(ApprovalType.id.desc())
        )
        approval_type = approval_type_result.scalars().first()
        if approval_type is None and not enabled:
            return {
                "enabled": False,
                "module": module_code,
                "approval_type_id": None,
                "flow_id": None,
                "title": item_title,
                "description": description,
            }
        if approval_type is None:
            approval_type = ApprovalType(
                name=type_name,
                business_code=module_code,
                category="考勤",
                scope="web",
                is_active=enabled,
                status="enabled" if enabled else "draft",
                description=description,
                sort_order=0,
            )
            db.add(approval_type)
            await db.flush()
        else:
            approval_type.name = type_name
            approval_type.category = "考勤"
            approval_type.scope = "web"
            approval_type.is_active = enabled
            approval_type.status = "enabled" if enabled else "draft"
            approval_type.description = description

        flow_result = await db.execute(
            select(ApprovalFlow).where(ApprovalFlow.module == module_code).order_by(ApprovalFlow.id.desc())
        )
        flow = flow_result.scalars().first()
        if flow is None and not enabled:
            return {
                "enabled": False,
                "module": module_code,
                "approval_type_id": approval_type.id,
                "flow_id": None,
                "title": item_title,
                "description": description,
            }
        if flow is None:
            flow = ApprovalFlow(
                name=flow_name,
                module=module_code,
                description=description,
                is_active=enabled,
            )
            db.add(flow)
            await db.flush()
        else:
            flow.name = flow_name
            flow.description = description
            flow.is_active = enabled

        nodes_result = await db.execute(
            select(ApprovalNode)
            .where(ApprovalNode.flow_id == flow.id)
            .order_by(ApprovalNode.node_order.asc(), ApprovalNode.id.asc())
        )
        nodes = nodes_result.scalars().all()
        if not nodes:
            db.add_all(
                [
                    ApprovalNode(
                        flow_id=flow.id,
                        node_order=1,
                        approver_type="direct_manager",
                        node_type="approval",
                    ),
                    ApprovalNode(
                        flow_id=flow.id,
                        node_order=2,
                        approver_type="hr",
                        node_type="approval",
                    ),
                ]
            )
            await db.flush()

        return {
            "enabled": enabled,
            "module": module_code,
            "approval_type_id": approval_type.id,
            "flow_id": flow.id,
            "title": item_title,
            "description": description,
        }

    @staticmethod
    async def _sync_rule_approval_configs(db: AsyncSession, rule: AttendanceRule) -> None:
        extra = rule.extra_config if isinstance(rule.extra_config, dict) else {}
        approval_targets: list[tuple[str, str]] = [
            ("patch_apply", "补卡"),
            ("approve_punch", "审批打卡"),
            ("leave_punch", "请假打卡"),
            ("overtime", "加班"),
        ]
        bindings: Dict[str, Any] = {}
        try:
            for item_key, item_title in approval_targets:
                enabled = AttendanceRuleService._approval_item_enabled(rule, item_key, extra)
                description = AttendanceRuleService._approval_item_description(item_key, extra)
                bindings[item_key] = await AttendanceRuleService._ensure_rule_approval_entry(
                    db=db,
                    rule=rule,
                    item_key=item_key,
                    item_title=item_title,
                    enabled=enabled,
                    description=description,
                )
        except (ProgrammingError, OperationalError) as exc:
            # 开发环境可能只初始化了考勤模块，尚未初始化审批模块表。
            # 缺失审批表时不应阻断考勤规则保存，先降级为“仅保存规则本体”。
            error_text = str(exc).lower()
            missing_approval_tables = any(
                table_name in error_text for table_name in ("approval_types", "approval_flows", "approval_nodes")
            )
            if not missing_approval_tables:
                raise
            logger.warning("Skip attendance approval binding sync due to missing approval tables: %s", exc)
            bindings = extra.get("approval_bindings") if isinstance(extra.get("approval_bindings"), dict) else {}

        new_extra = dict(extra)
        new_extra["approval_bindings"] = bindings
        rule.extra_config = new_extra
        await db.flush()

    @staticmethod
    async def detect_conflicts(
        db: AsyncSession,
        data: dict,
        exclude_rule_id: Optional[int] = None,
    ) -> list[dict]:
        """按部门+员工类型+考勤组+生效日检测潜在冲突。"""
        stmt = select(AttendanceRule)
        if exclude_rule_id is not None:
            stmt = stmt.where(AttendanceRule.id != exclude_rule_id)
        result = await db.execute(stmt)
        existing_rules = result.scalars().all()

        target_dept = data.get("department_id")
        target_effective = data.get("effective_date")
        target_extra = data.get("extra_config") or {}
        # 项目按参与日期显式关联规则，不参与个人默认规则的范围竞争。
        if target_extra.get("scope_mode") == "project":
            return []
        target_employee_type = target_extra.get("employee_type")
        target_group = target_extra.get("attendance_group")

        conflicts: list[dict] = []
        for rule in existing_rules:
            source_extra = rule.extra_config or {}
            if source_extra.get("scope_mode") == "project":
                continue
            if target_dept not in (None, rule.department_id):
                continue
            if target_effective and rule.effective_date != target_effective:
                continue
            if target_employee_type and source_extra.get("employee_type") not in (None, target_employee_type):
                continue
            if target_group and source_extra.get("attendance_group") not in (None, target_group):
                continue
            conflicts.append(
                {
                    "id": rule.id,
                    "name": rule.name,
                    "reason": "相同范围（部门/员工类型/考勤组/生效日期）存在规则，可能冲突",
                }
            )
        return conflicts

    @staticmethod
    async def create(
        db: AsyncSession,
        data: AttendanceRuleCreate,
        current_user: Optional[Employee] = None,
    ) -> AttendanceRule:
        """
        创建考勤规则。

        参数:
            db: 异步数据库会话
            data: 考勤规则创建 Schema（包含工时制、上下班时间、校验配置等）

        返回:
            已持久化的 AttendanceRule 对象（含自增 id）
        """
        payload = data.model_dump()
        conflicts = await AttendanceRuleService.detect_conflicts(db, payload)
        if conflicts:
            raise ValueError(f"规则冲突: {conflicts[0]['name']}（ID={conflicts[0]['id']}）")

        rule = AttendanceRule(**payload)
        db.add(rule)
        await db.flush()
        await AttendanceRuleService._sync_rule_approval_configs(db, rule)
        await AttendanceRuleService._write_log(
            db, rule.id, "create", f"创建规则：{rule.name}", current_user
        )
        await db.refresh(rule)
        return rule

    @staticmethod
    async def get(db: AsyncSession, rule_id: int) -> Optional[AttendanceRule]:
        """
        按 ID 查询单条考勤规则。

        参数:
            db: 异步数据库会话
            rule_id: 规则主键 ID

        返回:
            AttendanceRule 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(AttendanceRule).where(AttendanceRule.id == rule_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_rules(
        db: AsyncSession,
        location_id: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> Sequence[AttendanceRule]:
        """
        查询考勤规则列表，支持按地点和激活状态筛选。

        参数:
            db: 异步数据库会话
            location_id: 地点 ID（可选，不传则不过滤）
            is_active: 是否启用（可选，不传则不过滤）

        返回:
            按 id 升序排列的 AttendanceRule 列表
        """
        stmt = select(AttendanceRule)
        if location_id is not None:
            stmt = stmt.where(AttendanceRule.location_id == location_id)
        if is_active is not None:
            stmt = stmt.where(AttendanceRule.is_active == is_active)
        stmt = stmt.order_by(AttendanceRule.priority.asc(), AttendanceRule.id.asc())
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession,
        rule_id: int,
        data: AttendanceRuleUpdate,
        current_user: Optional[Employee] = None,
    ) -> Optional[AttendanceRule]:
        """
        部分更新考勤规则（仅修改请求中传入的字段）。

        参数:
            db: 异步数据库会话
            rule_id: 目标规则 ID
            data: 考勤规则更新 Schema（使用 exclude_unset 实现局部更新）

        返回:
            更新后的 AttendanceRule 对象；若规则不存在则返回 None
        """
        rule = await AttendanceRuleService.get(db, rule_id)
        if rule is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        merged = {
            "department_id": update_data.get("department_id", rule.department_id),
            "effective_date": update_data.get("effective_date", rule.effective_date),
            "extra_config": update_data.get("extra_config", rule.extra_config or {}),
        }
        conflicts = await AttendanceRuleService.detect_conflicts(
            db, merged, exclude_rule_id=rule_id
        )
        if conflicts:
            raise ValueError(f"规则冲突: {conflicts[0]['name']}（ID={conflicts[0]['id']}）")

        before_name = rule.name
        for field, value in update_data.items():
            setattr(rule, field, value)
        from app.services.field_service import update_project_shifts
        await update_project_shifts(db, rule)
        await AttendanceRuleService._sync_rule_approval_configs(db, rule)
        await AttendanceRuleService._write_log(
            db,
            rule.id,
            "update",
            f"更新规则：{before_name} -> {rule.name}",
            current_user,
        )
        await db.flush()
        await db.refresh(rule)
        return rule

    @staticmethod
    async def delete(
        db: AsyncSession, rule_id: int, current_user: Optional[Employee] = None
    ) -> bool:
        """
        删除考勤规则。

        参数:
            db: 异步数据库会话
            rule_id: 目标规则 ID

        返回:
            True 表示删除成功；False 表示规则不存在
        """
        rule = await AttendanceRuleService.get(db, rule_id)
        if rule is None:
            return False
        await AttendanceRuleService._write_log(
            db, rule.id, "delete", f"删除规则：{rule.name}", current_user
        )
        await db.delete(rule)
        await db.flush()
        return True

    @staticmethod
    async def toggle_active(
        db: AsyncSession,
        rule_id: int,
        is_active: bool,
        current_user: Optional[Employee] = None,
    ) -> Optional[AttendanceRule]:
        rule = await AttendanceRuleService.get(db, rule_id)
        if rule is None:
            return None
        rule.is_active = is_active
        await AttendanceRuleService._sync_rule_approval_configs(db, rule)
        await AttendanceRuleService._write_log(
            db,
            rule.id,
            "toggle",
            f"{'启用' if is_active else '停用'}规则：{rule.name}",
            current_user,
        )
        await db.flush()
        await db.refresh(rule)
        return rule

    @staticmethod
    async def copy_rule(
        db: AsyncSession,
        rule_id: int,
        name: Optional[str],
        current_user: Optional[Employee] = None,
    ) -> Optional[AttendanceRule]:
        source = await AttendanceRuleService.get(db, rule_id)
        if source is None:
            return None
        payload = {
            "name": name or f"{source.name}-副本",
            "work_hour_type": source.work_hour_type,
            "location_id": source.location_id,
            "department_id": source.department_id,
            "clock_in_time": source.clock_in_time,
            "clock_out_time": source.clock_out_time,
            "flexible_minutes": source.flexible_minutes,
            "work_hours_per_day": source.work_hours_per_day,
            "overtime_weekday_rate": source.overtime_weekday_rate,
            "overtime_weekend_rate": source.overtime_weekend_rate,
            "overtime_holiday_rate": source.overtime_holiday_rate,
            "require_gps": source.require_gps,
            "require_wifi": source.require_wifi,
            "require_photo": source.require_photo,
            "gps_latitude": source.gps_latitude,
            "gps_longitude": source.gps_longitude,
            "gps_radius_meters": source.gps_radius_meters,
            "wifi_ssid": source.wifi_ssid,
            "priority": source.priority,
            "effective_date": source.effective_date,
            "extra_config": {**(source.extra_config or {}), "scope_mode": "project", "assigned_employee_ids": []},
            "is_active": False,
        }
        new_rule = AttendanceRule(**payload)
        db.add(new_rule)
        await db.flush()
        await AttendanceRuleService._sync_rule_approval_configs(db, new_rule)
        await AttendanceRuleService._write_log(
            db,
            new_rule.id,
            "copy",
            f"复制规则：来源#{source.id} {source.name}",
            current_user,
        )
        await db.refresh(new_rule)
        return new_rule

    @staticmethod
    async def list_logs(db: AsyncSession, rule_id: int) -> Sequence[AttendanceRuleLog]:
        result = await db.execute(
            select(AttendanceRuleLog)
            .where(AttendanceRuleLog.rule_id == rule_id)
            .order_by(AttendanceRuleLog.created_at.desc(), AttendanceRuleLog.id.desc())
        )
        return result.scalars().all()


class ShiftScheduleService:
    """
    排班管理服务。

    排班将员工与特定日期的考勤规则绑定。系统的考勤规则查找逻辑是：
    1. 优先查员工当日的排班记录，找到则取该排班绑定的规则
    2. 若没有排班，回退到第一条激活的全局考勤规则

    批量排班功能（batch_create）具有 upsert 语义：若指定日期已有排班，
    会先删除旧记录再插入新记录，保证幂等性。
    """

    @staticmethod
    async def create(db: AsyncSession, data: ShiftScheduleCreate) -> ShiftSchedule:
        """
        为单名员工在指定日期创建一条排班记录（upsert 语义）。

        参数:
            db: 异步数据库会话
            data: 排班创建 Schema（含 employee_id, date, shift_type, rule_id, start/end_time）

        返回:
            已持久化的 ShiftSchedule 对象（若该员工该日期已有排班，会先覆盖）
        """
        # 与 batch_create 保持一致：同员工+同日期只保留一条排班
        await db.execute(
            delete(ShiftSchedule).where(
                and_(
                    ShiftSchedule.employee_id == data.employee_id,
                    ShiftSchedule.date == data.date,
                )
            )
        )
        schedule = ShiftSchedule(**data.model_dump())
        db.add(schedule)
        await db.flush()
        await db.refresh(schedule)
        return schedule

    @staticmethod
    async def batch_create(
        db: AsyncSession, data: ShiftScheduleBatchCreate
    ) -> List[ShiftSchedule]:
        """
        批量排班：为多名员工在日期范围内创建排班，具有 upsert 语义。

        实现逻辑：
        - 遍历 [start_date, end_date] 内的每一天
        - 对每名员工先删除该日期的旧排班（若存在）
        - 再插入新排班记录

        参数:
            db: 异步数据库会话
            data: 批量排班创建 Schema（含 employee_ids 列表、日期范围、班次类型和规则）

        返回:
            本次创建的所有 ShiftSchedule 对象列表
        """
        schedules: List[ShiftSchedule] = []
        current = data.start_date
        while current <= data.end_date:
            for emp_id in data.employee_ids:
                # 先删除已有排班 (upsert 语义)
                await db.execute(
                    delete(ShiftSchedule).where(
                        and_(
                            ShiftSchedule.employee_id == emp_id,
                            ShiftSchedule.date == current,
                        )
                    )
                )
                schedule = ShiftSchedule(
                    employee_id=emp_id,
                    date=current,
                    shift_type=data.shift_type,
                    rule_id=data.rule_id,
                    start_time=data.start_time,
                    end_time=data.end_time,
                )
                db.add(schedule)
                schedules.append(schedule)
            current += timedelta(days=1)
        await db.flush()
        for s in schedules:
            await db.refresh(s)
        return schedules

    @staticmethod
    async def get(db: AsyncSession, schedule_id: int) -> Optional[ShiftSchedule]:
        """
        按 ID 查询单条排班记录。

        参数:
            db: 异步数据库会话
            schedule_id: 排班主键 ID

        返回:
            ShiftSchedule 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(ShiftSchedule).where(ShiftSchedule.id == schedule_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_employee(
        db: AsyncSession,
        employee_id: int,
        start_date: date,
        end_date: date,
    ) -> Sequence[ShiftSchedule]:
        """
        查询指定员工在日期范围内的所有排班记录，按日期升序排列。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            start_date: 查询起始日期（含）
            end_date: 查询结束日期（含）

        返回:
            按日期升序的 ShiftSchedule 列表
        """
        result = await db.execute(
            select(ShiftSchedule)
            .where(
                and_(
                    ShiftSchedule.employee_id == employee_id,
                    ShiftSchedule.date >= start_date,
                    ShiftSchedule.date <= end_date,
                )
            )
            .order_by(ShiftSchedule.date)
        )
        return result.scalars().all()

    @staticmethod
    async def list_shifts(
        db: AsyncSession,
        employee_id: Optional[int] = None,
        department_id: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Sequence[ShiftSchedule]:
        """
        灵活查询排班列表，支持按员工、部门和日期范围组合筛选。

        注意：若同时指定 employee_id 和 department_id，两个条件均生效（AND 关系）。
        若仅指定 department_id，会 JOIN Employee 表进行部门过滤。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID（可选）
            department_id: 部门 ID（可选，需 JOIN Employee 表）
            start_date: 查询起始日期（可选）
            end_date: 查询结束日期（可选）

        返回:
            按日期、员工 ID 升序排列的 ShiftSchedule 列表
        """
        stmt = select(ShiftSchedule)

        # 如果指定了部门, 需要 JOIN Employee 表按部门过滤
        if department_id is not None:
            stmt = stmt.join(
                Employee, ShiftSchedule.employee_id == Employee.id
            ).where(Employee.department_id == department_id)

        if employee_id is not None:
            stmt = stmt.where(ShiftSchedule.employee_id == employee_id)
        if start_date is not None:
            stmt = stmt.where(ShiftSchedule.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(ShiftSchedule.date <= end_date)

        stmt = stmt.order_by(ShiftSchedule.date, ShiftSchedule.employee_id)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession, schedule_id: int, data: ShiftScheduleUpdate
    ) -> Optional[ShiftSchedule]:
        """
        部分更新排班记录。

        参数:
            db: 异步数据库会话
            schedule_id: 目标排班 ID
            data: 排班更新 Schema（使用 exclude_unset 实现局部更新）

        返回:
            更新后的 ShiftSchedule 对象；若不存在则返回 None
        """
        schedule = await ShiftScheduleService.get(db, schedule_id)
        if schedule is None:
            return None
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(schedule, field, value)
        await db.flush()
        await db.refresh(schedule)
        return schedule

    @staticmethod
    async def delete(db: AsyncSession, schedule_id: int) -> bool:
        """
        删除排班记录。

        参数:
            db: 异步数据库会话
            schedule_id: 目标排班 ID

        返回:
            True 表示删除成功；False 表示记录不存在
        """
        schedule = await ShiftScheduleService.get(db, schedule_id)
        if schedule is None:
            return False
        await db.delete(schedule)
        await db.flush()
        return True


class AttendanceRecordService:
    """
    打卡与考勤记录核心服务。

    本服务负责处理所有与"打卡"相关的业务，是考勤模块的核心。
    主要职责：
    1. 执行打卡（上班/下班），包含校验、工时计算、状态判定
    2. 考勤记录的分页查询和异常列表
    3. 考勤异常的修正申请与审批

    关键设计决策：
    - 重复打卡策略：上班打卡保留最早时间，下班打卡保留最晚时间（取极值）
    - 无上班打卡时仍允许下班打卡，并记录"缺少上班打卡"异常
    - 工时和状态在下班打卡时统一计算（确保同时拥有上下班时间）
    - 审批通过的修正会将 status 强制改为 normal，清除 anomaly_type
    """

    @staticmethod
    async def _has_any_role(
        db: AsyncSession,
        current_user: Employee,
        roles: tuple[str, ...],
    ) -> bool:
        """统一按 is_superuser + employee_roles 判定扩展角色。"""
        if getattr(current_user, "is_superuser", False):
            return True
        result = await db.execute(
            select(EmployeeRole.role_name).where(
                EmployeeRole.employee_id == current_user.id,
                EmployeeRole.is_active == True,
                EmployeeRole.role_name.in_(roles),
            )
        )
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # 今日考勤统计
    # ------------------------------------------------------------------
    @staticmethod
    async def get_today_stats(db: AsyncSession) -> Dict[str, int]:
        """
        获取今日全公司考勤统计数据，用于管理驾驶舱首页大盘展示。

        统计维度：
        - total: 今日有考勤记录的员工总数
        - checked_in: 已签到人数（normal + late + early_leave 之和）
        - late: 迟到人数
        - absent: 旷工人数
        - on_leave: 请假人数

        参数:
            db: 异步数据库会话

        返回:
            包含以上统计字段的字典；若今日无记录则所有字段返回 0
        """
        today = date.today()

        # 查询今日所有考勤记录
        result = await db.execute(
            select(AttendanceRecord).where(AttendanceRecord.date == today)
        )
        records = result.scalars().all()

        if not records:
            return {
                "total": 0,
                "checked_in": 0,
                "late": 0,
                "absent": 0,
                "on_leave": 0,
            }

        total = len(records)
        # 已打卡 = 正常 + 迟到 + 早退 (即成功签到的人)
        checked_in = sum(
            1 for r in records
            if r.status in (
                AttendanceStatus.normal,
                AttendanceStatus.late,
                AttendanceStatus.early_leave,
            )
        )
        late = sum(1 for r in records if r.status == AttendanceStatus.late)
        absent = sum(1 for r in records if r.status == AttendanceStatus.absent)
        on_leave = sum(1 for r in records if r.status == AttendanceStatus.on_leave)

        return {
            "total": total,
            "checked_in": checked_in,
            "late": late,
            "absent": absent,
            "on_leave": on_leave,
        }

    # ------------------------------------------------------------------
    # GPS 距离校验 (Haversine)
    # ------------------------------------------------------------------
    @staticmethod
    def _haversine_meters(
        lat1: float, lng1: float, lat2: float, lng2: float
    ) -> float:
        """
        使用 Haversine 公式计算地球表面两点之间的距离（单位：米）。

        Haversine 公式适用于球面三角计算，精度足够用于打卡范围校验（误差 < 0.5%）。
        计算公式：
            a = sin²(Δφ/2) + cos(φ1)·cos(φ2)·sin²(Δλ/2)
            距离 = R × 2 × atan2(√a, √(1-a))
        其中 R = 6,371,000 米（地球平均半径）

        参数:
            lat1, lng1: 参考点纬度和经度（度）
            lat2, lng2: 打卡点纬度和经度（度）

        返回:
            两点之间的距离（米）
        """
        R = 6_371_000  # 地球半径 (米)
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lng2 - lng1)
        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _cst() -> timezone:
        return timezone(timedelta(hours=8))

    @staticmethod
    def _to_cst_datetime(dt: Optional[datetime]) -> Optional[datetime]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(AttendanceRecordService._cst())

    @staticmethod
    def _local_business_datetime_to_storage(value: Optional[datetime]) -> Optional[datetime]:
        """
        将审批表单选择的本地业务时间统一转为数据库存储口径。

        MySQL DATETIME 不保留时区；本项目现有打卡记录按 UTC naive 存储，
        读取时再由 _to_cst_datetime 转为北京时间。审批补卡表单传入的是
        员工选择的北京时间，因此写入考勤记录前必须先转 UTC naive，避免
        后续重算把 18:30 误当成 UTC 再加 8 小时。
        """
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=AttendanceRecordService._cst())
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _parse_hm(v: Any) -> Optional[time]:
        if isinstance(v, time):
            return v
        if not v:
            return None
        txt = str(v).strip()
        if ":" not in txt:
            return None
        hh, mm = txt.split(":", 1)
        try:
            h = int(hh)
            m = int(mm[:2])
        except ValueError:
            return None
        if h < 0 or h > 23 or m < 0 or m > 59:
            return None
        return time(hour=h, minute=m)

    @staticmethod
    def _to_hm_string(v: Optional[time]) -> str:
        if v is None:
            return ""
        return f"{v.hour:02d}:{v.minute:02d}"

    @staticmethod
    def _extra(rule: Optional[AttendanceRule]) -> Dict[str, Any]:
        raw = getattr(rule, "extra_config", None) or {}
        if isinstance(raw, dict):
            return raw
        return {}

    @staticmethod
    def _requires_photo_each_punch(rule: AttendanceRule) -> bool:
        extra = AttendanceRecordService._extra(rule)
        if "photo_each_punch" in extra:
            return bool(extra["photo_each_punch"])
        return bool(getattr(rule, "require_photo", False))

    @staticmethod
    def _infer_rule_type(rule: Optional[AttendanceRule], extra: Dict[str, Any]) -> str:
        t = str(extra.get("rule_type") or "").strip()
        if t in ("fixed", "shift", "free"):
            return t
        work_hour_type = getattr(rule, "work_hour_type", None)
        if work_hour_type == WorkHourType.comprehensive:
            return "shift"
        if work_hour_type == WorkHourType.flexible:
            return "free"
        return "fixed"

    @staticmethod
    def _late_allow_minutes(rule: AttendanceRule, flex_mode: str = "none") -> int:
        """
        固定上下班规则中，clock_in_time 是最后正常上班时间。

        旧页面默认把 flexible_minutes 填成 15，导致 09:30 规则下 09:41 仍被
        当作正常。这里把“弹性分钟数”和“迟到宽限”拆开：只有显式开启弹性或
        配置 late_threshold_minutes 时才允许宽限；普通固定班次默认为 0 分钟。
        """
        extra = AttendanceRecordService._extra(rule)
        explicit_threshold = extra.get("late_threshold_minutes")
        if explicit_threshold is not None:
            try:
                return max(0, int(explicit_threshold or 0))
            except (TypeError, ValueError):
                return 0
        if str(flex_mode or "none") == "none" and not bool(extra.get("enable_flexible")):
            return 0
        return max(0, int(getattr(rule, "flexible_minutes", 0) or 0))

    @staticmethod
    async def _get_employee(db: AsyncSession, employee_id: int) -> Optional[Employee]:
        result = await db.execute(select(Employee).where(Employee.id == employee_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_shift_for_employee_date(
        db: AsyncSession,
        employee_id: int,
        target_date: date,
    ) -> Optional[ShiftSchedule]:
        shift_result = await db.execute(
            select(ShiftSchedule).where(
                and_(
                    ShiftSchedule.employee_id == employee_id,
                    ShiftSchedule.date == target_date,
                )
            )
        )
        return shift_result.scalar_one_or_none()

    @staticmethod
    async def _department_descendants(
        db: AsyncSession,
        root_ids: List[int],
    ) -> set[int]:
        if not root_ids:
            return set()
        result = await db.execute(select(Department.id, Department.parent_id))
        rows = result.all()
        children: Dict[int, List[int]] = {}
        for dept_id, parent_id in rows:
            if parent_id is None:
                continue
            children.setdefault(parent_id, []).append(dept_id)
        seen = set(root_ids)
        stack = list(root_ids)
        while stack:
            node = stack.pop()
            for child in children.get(node, []):
                if child in seen:
                    continue
                seen.add(child)
                stack.append(child)
        return seen

    @staticmethod
    def _parse_int_set(raw: Any) -> set[int]:
        if not isinstance(raw, list):
            return set()
        out: set[int] = set()
        for item in raw:
            try:
                out.add(int(item))
            except Exception:
                continue
        return out

    @staticmethod
    def _parse_text_set(raw: Any) -> set[str]:
        out: set[str] = set()
        if isinstance(raw, list):
            for item in raw:
                text = str(item or "").strip()
                if text:
                    out.add(text)
        elif isinstance(raw, str):
            text = raw.strip()
            if text:
                out.add(text)
        return out

    @staticmethod
    def _rule_no_punch_employee_ids(extra: Dict[str, Any]) -> set[int]:
        return AttendanceRecordService._parse_int_set(extra.get("no_punch_employee_ids"))

    @staticmethod
    def _rule_report_target_ids(extra: Dict[str, Any]) -> set[int]:
        return AttendanceRecordService._parse_int_set(extra.get("report_target_ids"))

    @staticmethod
    def _rule_assist_manager_ids(extra: Dict[str, Any]) -> set[int]:
        return AttendanceRecordService._parse_int_set(extra.get("assist_manager_ids"))

    @staticmethod
    def _is_no_punch_employee(extra: Dict[str, Any], employee_id: Optional[int]) -> bool:
        if employee_id is None:
            return False
        try:
            normalized_id = int(employee_id)
        except Exception:
            return False
        return normalized_id in AttendanceRecordService._rule_no_punch_employee_ids(extra)

    @staticmethod
    def _employee_type_matches(rule_employee_type: str, employee_type: str) -> bool:
        """
        规则表单里的“固定工”是考勤域口径，员工档案里通常落为“正式/试用”。
        这里做同义归一，避免规则明明适用却无法参与打卡计算。
        """
        expected = str(rule_employee_type or "").strip()
        actual = str(employee_type or "").strip()
        if not expected:
            return True
        if expected == actual:
            return True

        aliases: Dict[str, set[str]] = {
            "固定工": {"正式", "试用", "试用期", "REGULAR", "PROBATION"},
            "正式": {"固定工", "REGULAR"},
            "试用": {"固定工", "试用期", "PROBATION"},
            "实习": {"实习生", "INTERN"},
            "实习生": {"实习", "INTERN"},
            "外包": {"劳务外包", "OUTSOURCE"},
            "劳务外包": {"外包", "OUTSOURCE"},
        }
        return actual in aliases.get(expected, set()) or expected in aliases.get(actual, set())

    @staticmethod
    def _extract_employee_tags(employee: Employee) -> set[str]:
        tags: set[str] = set()

        def push(v: Any) -> None:
            text = str(v or "").strip()
            if text:
                tags.add(text)

        def parse_value(raw: Any) -> None:
            if raw is None:
                return
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        push(item.get("name") or item.get("label") or item.get("tag"))
                    else:
                        push(item)
                return
            if isinstance(raw, dict):
                push(raw.get("name") or raw.get("label") or raw.get("tag"))
                return
            text = str(raw).strip()
            if not text:
                return
            try:
                parsed = json.loads(text)
                parse_value(parsed)
                return
            except Exception:
                pass
            for part in re.split(r"[，,;；]", text):
                push(part)

        for attr in ("tags", "talent_tags", "skill_tags"):
            parse_value(getattr(employee, attr, None))
        return tags

    @staticmethod
    def _employee_in_named_groups(extra: Dict[str, Any], employee_id: int, selected_groups: set[str]) -> bool:
        if not selected_groups:
            return False

        candidates: List[Tuple[str, Any]] = []
        for key in ("attendance_group_members", "group_member_map", "group_members_map"):
            mapping = extra.get(key)
            if isinstance(mapping, dict):
                candidates.extend((str(k), v) for k, v in mapping.items())
        if isinstance(extra.get("group_members"), list):
            for row in extra.get("group_members", []):
                if isinstance(row, dict):
                    name = str(row.get("group") or row.get("name") or "").strip()
                    members = row.get("member_ids") or row.get("employee_ids")
                    if name:
                        candidates.append((name, members))

        for group_name, members in candidates:
            if group_name not in selected_groups:
                continue
            if employee_id in AttendanceRecordService._parse_int_set(members):
                return True
        return False

    @staticmethod
    async def _rule_matches_employee(
        db: AsyncSession,
        rule: AttendanceRule,
        employee: Employee,
        target_date: date,
    ) -> bool:
        if not rule.is_active:
            return False
        if rule.effective_date and rule.effective_date > target_date:
            return False
        if rule.department_id and rule.department_id != getattr(employee, "department_id", None):
            return False
        if rule.location_id and rule.location_id != getattr(employee, "location_id", None):
            return False

        extra = AttendanceRecordService._extra(rule)

        if extra.get("scope_mode") == "project":
            return False

        employee_type = str(extra.get("employee_type") or "").strip()
        if employee_type and not AttendanceRecordService._employee_type_matches(
            employee_type,
            str(getattr(employee, "employment_type", "") or ""),
        ):
            return False

        exclude_ids = AttendanceRecordService._parse_int_set(extra.get("exclude_employee_ids"))
        if employee.id in exclude_ids:
            return False

        assigned_ids = AttendanceRecordService._parse_int_set(extra.get("assigned_employee_ids"))
        if assigned_ids and employee.id not in assigned_ids:
            return False

        scope_mode = str(extra.get("scope_mode") or "all")
        if scope_mode == "all":
            return True

        custom_types_raw = extra.get("scope_custom_types")
        custom_types: List[str] = custom_types_raw if isinstance(custom_types_raw, list) else []
        selected = set(str(x) for x in custom_types)
        if not selected:
            scope_type = str(extra.get("scope_type") or "all")
            if scope_type == "department":
                selected.add("department")
            elif scope_type == "group":
                selected.add("group")
            elif scope_type == "employee":
                selected.add("employee")
            elif scope_type == "tag":
                selected.add("tag")

        if not selected:
            return False

        dept_match = False
        if "department" in selected:
            dept_ids = AttendanceRecordService._parse_int_set(extra.get("department_ids"))
            include_sub = bool(extra.get("include_sub_departments", True))
            employee_dept = getattr(employee, "department_id", None)
            if employee_dept is not None and dept_ids:
                if include_sub:
                    all_ids = await AttendanceRecordService._department_descendants(db, list(dept_ids))
                    dept_match = employee_dept in all_ids
                else:
                    dept_match = employee_dept in dept_ids
            elif employee_dept is not None and rule.department_id:
                dept_match = employee_dept == rule.department_id

        member_match = False
        if "employee" in selected:
            member_ids = AttendanceRecordService._parse_int_set(extra.get("member_ids"))
            member_match = employee.id in member_ids

        # 目前后端未建立“员工-考勤组/标签”主数据，先只在配置存在时保守不命中。
        group_match = False
        tag_match = False
        if "group" in selected:
            groups = AttendanceRecordService._parse_text_set(extra.get("attendance_groups"))
            legacy_group = str(extra.get("attendance_group") or "").strip()
            if legacy_group:
                groups.add(legacy_group)
            group_match = AttendanceRecordService._employee_in_named_groups(extra, employee.id, groups)

        if "tag" in selected:
            target_tags = AttendanceRecordService._parse_text_set(extra.get("label_tags"))
            target_tags.update(AttendanceRecordService._parse_text_set(extra.get("tags")))
            if target_tags:
                employee_tags = AttendanceRecordService._extract_employee_tags(employee)
                tag_match = len(employee_tags.intersection(target_tags)) > 0

        return dept_match or member_match or group_match or tag_match

    @staticmethod
    def _rule_scope_specificity(rule: AttendanceRule, employee: Employee) -> int:
        """
        同优先级规则冲突时，按企业微信考勤规则口径选择更细粒度范围：
        员工 > 考勤组/标签/岗位 > 部门 > 全员。
        """
        extra = AttendanceRecordService._extra(rule)
        employee_id = int(getattr(employee, "id", 0) or 0)
        employee_dept = getattr(employee, "department_id", None)

        assigned_ids = AttendanceRecordService._parse_int_set(extra.get("assigned_employee_ids"))
        member_ids = AttendanceRecordService._parse_int_set(extra.get("member_ids"))
        if employee_id and (employee_id in assigned_ids or employee_id in member_ids):
            return 400

        scope_mode = str(extra.get("scope_mode") or "all")
        custom_types_raw = extra.get("scope_custom_types")
        custom_types = custom_types_raw if isinstance(custom_types_raw, list) else []
        selected = {str(x) for x in custom_types}
        if not selected:
            scope_type = str(extra.get("scope_type") or "all")
            if scope_type in {"department", "group", "employee", "tag", "position"}:
                selected.add(scope_type)

        if selected.intersection({"group", "tag", "position"}):
            return 300

        dept_ids = AttendanceRecordService._parse_int_set(extra.get("department_ids"))
        if (
            "department" in selected
            or dept_ids
            or (rule.department_id is not None and employee_dept == rule.department_id)
        ):
            return 200

        if scope_mode == "all" or str(extra.get("scope_type") or "all") == "all":
            return 100
        return 0

    @staticmethod
    async def _select_rule_for_employee(
        db: AsyncSession,
        employee: Employee,
        target_date: date,
    ) -> Optional[AttendanceRule]:
        result = await db.execute(
            select(AttendanceRule)
            .where(
                AttendanceRule.is_active.is_(True),
                AttendanceRule.effective_date <= target_date,
            )
            .order_by(AttendanceRule.priority.asc(), AttendanceRule.effective_date.desc(), AttendanceRule.id.asc())
        )
        rules = result.scalars().all()
        matches: List[Tuple[int, date, int, AttendanceRule]] = []
        for rule in rules:
            if await AttendanceRecordService._rule_matches_employee(db, rule, employee, target_date):
                effective = rule.effective_date or date.min
                matches.append(
                    (
                        int(rule.priority or 100),
                        effective,
                        int(getattr(rule, "id", 0) or 0),
                        rule,
                    )
                )
        if not matches:
            return None
        matches.sort(
            key=lambda item: (
                item[0],
                -AttendanceRecordService._rule_scope_specificity(item[3], employee),
                -item[1].toordinal(),
                item[2],
            )
        )
        return matches[0][3]

    @staticmethod
    def _configured_locations(rule: AttendanceRule, extra: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        locations = extra.get("locations")
        if isinstance(locations, list):
            for item in locations:
                if not isinstance(item, dict):
                    continue
                lat = item.get("latitude")
                lng = item.get("longitude")
                if lat is None or lng is None:
                    continue
                out.append(
                    {
                        "name": str(item.get("name") or item.get("address") or "打卡地点"),
                        "address": str(item.get("address") or item.get("name") or ""),
                        "latitude": lat,
                        "longitude": lng,
                        "radius": item.get("radius") or item.get("radius_meter") or item.get("gps_radius_meters") or 300,
                    }
                )
        if rule.gps_latitude is not None and rule.gps_longitude is not None:
            out.append(
                {
                    "name": "规则默认地点",
                    "address": "规则默认地点",
                    "latitude": rule.gps_latitude,
                    "longitude": rule.gps_longitude,
                    "radius": rule.gps_radius_meters or 300,
                }
            )
        return out

    @staticmethod
    def _configured_wifi_names(rule: AttendanceRule, extra: Dict[str, Any]) -> set[str]:
        names: set[str] = set()
        wifi_list = extra.get("wifi_list")
        if isinstance(wifi_list, list):
            for item in wifi_list:
                if not isinstance(item, dict):
                    continue
                for key in ("name", "ssid", "wifi_name"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        names.add(value)
        legacy_ssid = str(getattr(rule, "wifi_ssid", "") or "").strip()
        if legacy_ssid:
            names.add(legacy_ssid)
        return names

    @staticmethod
    def _weekday_for_date(d: date) -> int:
        # 周一=1 ... 周日=7
        return d.weekday() + 1

    @staticmethod
    def _is_even_week_from(anchor: date, target: date) -> bool:
        anchor_monday = anchor - timedelta(days=anchor.weekday())
        target_monday = target - timedelta(days=target.weekday())
        diff_weeks = (target_monday - anchor_monday).days // 7
        return abs(diff_weeks % 2) == 0

    @staticmethod
    def _resolve_segment_for_date(rule: AttendanceRule, target_date: date) -> Optional[Dict[str, Any]]:
        extra = AttendanceRecordService._extra(rule)
        segments = extra.get("time_segments")
        if not isinstance(segments, list) or not segments:
            return None
        weekday = AttendanceRecordService._weekday_for_date(target_date)
        anchor = getattr(rule, "effective_date", None) or target_date
        for raw in segments:
            if not isinstance(raw, dict):
                continue
            if bool(raw.get("biweekly_enabled")):
                this_days = raw.get("this_week_weekdays") if isinstance(raw.get("this_week_weekdays"), list) else []
                next_days = raw.get("next_week_weekdays") if isinstance(raw.get("next_week_weekdays"), list) else []
                use_this = AttendanceRecordService._is_even_week_from(anchor, target_date)
                selected_days = this_days if use_this else next_days
            else:
                selected_days = raw.get("weekdays") if isinstance(raw.get("weekdays"), list) else []
            day_set = AttendanceRecordService._parse_int_set(selected_days)
            if weekday in day_set:
                return raw
        return None

    @staticmethod
    def _rest_periods_from_segment(segment: Optional[Dict[str, Any]]) -> List[Tuple[time, time]]:
        out: List[Tuple[time, time]] = []
        if not segment:
            return out
        rest_periods = segment.get("rest_periods")
        if isinstance(rest_periods, list):
            for row in rest_periods:
                if not isinstance(row, dict):
                    continue
                st = AttendanceRecordService._parse_hm(row.get("start"))
                ed = AttendanceRecordService._parse_hm(row.get("end"))
                if st and ed:
                    out.append((st, ed))
        if out:
            return out
        st = AttendanceRecordService._parse_hm(segment.get("rest_start"))
        ed = AttendanceRecordService._parse_hm(segment.get("rest_end"))
        if st and ed:
            out.append((st, ed))
        return out

    @staticmethod
    def _half_day_pm_start_from_segment(segment: Optional[Dict[str, Any]]) -> Optional[time]:
        if not segment:
            return None
        rest_periods = segment.get("rest_periods")
        if isinstance(rest_periods, list) and rest_periods:
            first = rest_periods[0]
            if isinstance(first, dict):
                rest_end = AttendanceRecordService._parse_hm(first.get("end"))
                if rest_end is not None:
                    return rest_end
        rest_end = AttendanceRecordService._parse_hm(segment.get("rest_end"))
        if rest_end is not None:
            return rest_end
        raw = segment.get("half_day_pm")
        if isinstance(raw, list) and raw:
            return AttendanceRecordService._parse_hm(raw[0])
        return AttendanceRecordService._parse_hm(segment.get("half_day_pm_start"))

    @staticmethod
    def _half_day_absence_cutoff_from_segment(segment: Optional[Dict[str, Any]]) -> Optional[time]:
        if not segment:
            return None
        rest_periods = segment.get("rest_periods")
        if isinstance(rest_periods, list) and rest_periods:
            first = rest_periods[0]
            if isinstance(first, dict):
                rest_start = AttendanceRecordService._parse_hm(first.get("start"))
                if rest_start is not None:
                    return rest_start
        rest_start = AttendanceRecordService._parse_hm(segment.get("rest_start"))
        if rest_start is not None:
            return rest_start
        raw = segment.get("half_day_am")
        if isinstance(raw, list) and len(raw) >= 2:
            return AttendanceRecordService._parse_hm(raw[1])
        return AttendanceRecordService._parse_hm(segment.get("half_day_am_end"))

    @staticmethod
    def _half_day_pm_start_for_rule(
        rule: AttendanceRule,
        target_date: date,
        segment: Optional[Dict[str, Any]] = None,
    ) -> Optional[time]:
        pm_start = AttendanceRecordService._half_day_pm_start_from_segment(segment)
        if pm_start is not None:
            return pm_start
        return AttendanceRecordService._half_day_pm_start_from_segment(
            AttendanceRecordService._resolve_segment_for_date(rule, target_date)
        )

    @staticmethod
    def _half_day_absence_cutoff_for_rule(
        rule: AttendanceRule,
        target_date: date,
        segment: Optional[Dict[str, Any]] = None,
    ) -> Optional[time]:
        cutoff = AttendanceRecordService._half_day_absence_cutoff_from_segment(segment)
        if cutoff is not None:
            return cutoff
        return AttendanceRecordService._half_day_absence_cutoff_from_segment(
            AttendanceRecordService._resolve_segment_for_date(rule, target_date)
        )

    @staticmethod
    def _half_day_absence_late_minutes(
        actual_in: Optional[datetime],
        target_date: date,
        half_day_pm_start: Optional[time],
        late_allow: int = 0,
        half_day_absence_cutoff: Optional[time] = None,
    ) -> Optional[int]:
        """
        员工到岗时间已超过上午半天结束节点时，上午半天按旷工处理。

        准确口径来自休息时间：rest_start 是上午半天结束，rest_end 是下午
        上班开始。后续迟到分钟从 rest_end 重新起算，避免出现“迟到318分钟”
        这类把上午整段都算作迟到的展示。
        """
        if actual_in is None or half_day_pm_start is None:
            return None
        half_day_absence_cutoff = half_day_absence_cutoff or half_day_pm_start
        cst = AttendanceRecordService._cst()
        actual = AttendanceRecordService._truncate_datetime_to_minute(
            AttendanceRecordService._to_cst_datetime(actual_in)
        )
        if actual is None:
            return None
        cutoff_dt = AttendanceRecordService._truncate_datetime_to_minute(
            datetime.combine(target_date, half_day_absence_cutoff, tzinfo=cst)
        )
        pm_start_dt = AttendanceRecordService._truncate_datetime_to_minute(
            datetime.combine(target_date, half_day_pm_start, tzinfo=cst)
        )
        if actual < cutoff_dt:
            return None
        minutes = (actual - pm_start_dt).total_seconds() / 60.0 - max(0, int(late_allow or 0))
        return max(0, math.ceil(minutes))

    @staticmethod
    def _pick_shift_template(
        extra: Dict[str, Any],
        punch_at_cst: Optional[datetime],
    ) -> Optional[Dict[str, Any]]:
        templates = extra.get("shift_templates")
        if not isinstance(templates, list) or not templates:
            return None
        if punch_at_cst is None:
            for item in templates:
                if isinstance(item, dict) and str(item.get("name") or "") != "休息":
                    return item
            return templates[0] if isinstance(templates[0], dict) else None

        before = int(extra.get("shift_match_before_minutes") or 120)
        after = int(extra.get("shift_match_after_minutes") or 0)
        now_m = punch_at_cst.hour * 60 + punch_at_cst.minute
        best: Optional[Tuple[int, Dict[str, Any]]] = None
        for item in templates:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "") == "休息":
                continue
            cin = AttendanceRecordService._parse_hm(item.get("clock_in"))
            cout = AttendanceRecordService._parse_hm(item.get("clock_out"))
            if cin is None or cout is None:
                continue
            cin_m = cin.hour * 60 + cin.minute
            cout_m = cout.hour * 60 + cout.minute
            # 夜班跨天，按次日收班换算
            if cout_m <= cin_m:
                cout_m += 24 * 60
            candidates = [now_m, now_m + 24 * 60, now_m - 24 * 60]
            for n in candidates:
                if (cin_m - before) <= n <= (cout_m + after):
                    score = abs(n - cin_m)
                    if best is None or score < best[0]:
                        best = (score, item)
        return best[1] if best else None

    @staticmethod
    def _build_runtime(
        rule: AttendanceRule,
        shift: Optional[ShiftSchedule],
        target_date: date,
        punch_at_cst: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        extra = AttendanceRecordService._extra(rule)
        rule_type = AttendanceRecordService._infer_rule_type(rule, extra)
        segment = AttendanceRecordService._resolve_segment_for_date(rule, target_date)
        if segment is None and rule_type == "free":
            free_days = AttendanceRecordService._parse_int_set(extra.get("free_workdays"))
            if not free_days:
                free_days = {1, 2, 3, 4, 5}
            day_start_t = AttendanceRecordService._parse_hm(extra.get("free_day_start_time")) or time(hour=5, minute=0)
            day_start = AttendanceRecordService._to_hm_string(day_start_t)
            end_minutes = (day_start_t.hour * 60 + day_start_t.minute - 1) % (24 * 60)
            day_end = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
            segment = {
                "name": "自由上下班",
                "weekdays": sorted(list(free_days)),
                "clock_in": "00:00",
                "clock_out": "23:59",
                "check_in_required": False,
                "check_out_required": False,
                "punch_start": day_start,
                "punch_end": day_end,
                "flex_mode": "none",
                "rest_periods": [],
            }
        matched_shift_template: Optional[Dict[str, Any]] = None
        if shift is None and rule_type == "shift" and bool(extra.get("shift_no_schedule_system_match")):
            matched_shift_template = AttendanceRecordService._pick_shift_template(extra, punch_at_cst)
            if matched_shift_template is not None:
                segment = matched_shift_template

        expected_in = (
            shift.start_time
            if shift and shift.start_time
            else AttendanceRecordService._parse_hm(segment.get("clock_in") if segment else None) or rule.clock_in_time
        )
        expected_out = (
            shift.end_time
            if shift and shift.end_time
            else AttendanceRecordService._parse_hm(segment.get("clock_out") if segment else None) or rule.clock_out_time
        )
        check_in_required = bool(segment.get("check_in_required", True)) if segment else True
        check_out_required = bool(segment.get("check_out_required", True)) if segment else True
        flex_mode = str(segment.get("flex_mode") if segment else extra.get("flex_mode") or "none")
        early_threshold = int(extra.get("early_threshold_minutes") or 0)
        return {
            "rule": rule,
            "extra": extra,
            "segment": segment,
            "rule_type": rule_type,
            "expected_in": expected_in,
            "expected_out": expected_out,
            "check_in_required": check_in_required,
            "check_out_required": check_out_required,
            "flex_mode": flex_mode if flex_mode in ("none", "late_leave", "both") else "none",
            "early_threshold_minutes": max(0, early_threshold),
            "rest_periods": AttendanceRecordService._rest_periods_from_segment(segment),
            "half_day_pm_start": AttendanceRecordService._half_day_pm_start_from_segment(segment),
            "half_day_absence_cutoff": AttendanceRecordService._half_day_absence_cutoff_from_segment(segment),
            "matched_shift_template": matched_shift_template,
        }

    @staticmethod
    def _apply_rule_runtime_people(
        runtime: Dict[str, Any],
        employee_id: int,
    ) -> Dict[str, Any]:
        extra = runtime.get("extra") or {}
        no_punch = AttendanceRecordService._is_no_punch_employee(extra, employee_id)
        runtime["no_punch"] = no_punch
        runtime["report_target_ids"] = sorted(AttendanceRecordService._rule_report_target_ids(extra))
        runtime["assist_manager_ids"] = sorted(AttendanceRecordService._rule_assist_manager_ids(extra))
        if no_punch:
            runtime["check_in_required"] = False
            runtime["check_out_required"] = False
        return runtime

    @staticmethod
    async def _resolve_punch_target_date(
        db: AsyncSession,
        employee_id: int,
        cst_now: datetime,
    ) -> Tuple[date, Optional[Dict[str, Any]]]:
        from app.services.field_membership import managed_field_person, punch_context
        if await managed_field_person(db, employee_id):
            context = await punch_context(db, employee_id, cst_now)
            return (context["day"], context["runtime"]) if context else (cst_now.date(), None)
        today = cst_now.date()
        runtime = await AttendanceRecordService._get_rule_runtime(
            db,
            employee_id,
            today,
            punch_at_cst=cst_now,
        )
        # Explicit overnight shifts belong to the date the shift began, through a
        # four-hour checkout grace period. A new day's ordinary shift is unaffected.
        previous = today - timedelta(days=1)
        previous_shift = await db.scalar(select(ShiftSchedule).where(ShiftSchedule.employee_id == employee_id, ShiftSchedule.date == previous))
        if isinstance(previous_shift, ShiftSchedule) and previous_shift.start_time and previous_shift.end_time and previous_shift.end_time <= previous_shift.start_time:
            boundary = datetime.combine(today, previous_shift.end_time, tzinfo=cst_now.tzinfo) + timedelta(hours=4)
            if cst_now <= boundary:
                previous_runtime = await AttendanceRecordService._get_rule_runtime(db, employee_id, previous, punch_at_cst=cst_now)
                if previous_runtime: return previous, previous_runtime
        if runtime is None:
            return today, None
        rule_type = str((runtime or {}).get("rule_type") or "")
        if rule_type != "free":
            return today, runtime
        extra = (runtime or {}).get("extra") or {}
        cutoff = AttendanceRecordService._parse_hm(extra.get("free_day_start_time"))
        if cutoff is None:
            return today, runtime
        if cst_now.time() >= cutoff:
            return today, runtime
        prev_day = today - timedelta(days=1)
        prev_runtime = await AttendanceRecordService._get_rule_runtime(
            db,
            employee_id,
            prev_day,
            punch_at_cst=cst_now,
        )
        if prev_runtime is not None and str((prev_runtime or {}).get("rule_type") or "") == "free":
            return prev_day, prev_runtime
        return today, runtime

    @staticmethod
    async def _get_rule_for_employee(
        db: AsyncSession, employee_id: int, target_date: date
    ) -> Optional[AttendanceRule]:
        runtime = await AttendanceRecordService._get_rule_runtime(db, employee_id, target_date)
        return runtime["rule"] if runtime else None

    @staticmethod
    async def _get_rule_runtime(
        db: AsyncSession,
        employee_id: int,
        target_date: date,
        employee: Optional[Employee] = None,
        punch_at_cst: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        employee = employee or await AttendanceRecordService._get_employee(db, employee_id)
        if employee is None:
            return None
        from app.services.field_membership import managed_field_person, punch_context, segment_runtime, segment_view
        from app.models.field_service import FieldAttendanceSegment
        if await managed_field_person(db, employee_id, employee):
            if punch_at_cst:
                context = await punch_context(db, employee_id, punch_at_cst)
                return context["runtime"] if context else None
            segments = list((await db.scalars(select(FieldAttendanceSegment).where(FieldAttendanceSegment.employee_id == employee_id, FieldAttendanceSegment.work_date == target_date).order_by(FieldAttendanceSegment.id))).all())
            if segments:
                runtime = segment_runtime(segments[0].rule_snapshot, target_date)
                runtime.update(employee=employee, project_segments=[segment_view(s) for s in segments])
                return runtime
            from app.services.field_membership import day_runtime
            return await day_runtime(db, employee, target_date)
        shift = await AttendanceRecordService._get_shift_for_employee_date(db, employee_id, target_date)
        if shift is not None:
            shift_rule = await AttendanceRuleService.get(db, shift.rule_id)
            if shift_rule is not None:
                runtime = AttendanceRecordService._build_runtime(shift_rule, shift, target_date, punch_at_cst=punch_at_cst)
                AttendanceRecordService._apply_rule_runtime_people(runtime, int(employee.id))
                runtime["employee"] = employee
                runtime["shift"] = shift
                return runtime
        rule = await AttendanceRecordService._select_rule_for_employee(db, employee, target_date)
        if rule is None:
            return None
        runtime = AttendanceRecordService._build_runtime(rule, None, target_date, punch_at_cst=punch_at_cst)
        AttendanceRecordService._apply_rule_runtime_people(runtime, int(employee.id))
        runtime["employee"] = employee
        runtime["shift"] = None
        return runtime

    @staticmethod
    def _validate_clock(
        rule: AttendanceRule,
        gps_lat: Optional[Decimal],
        gps_lng: Optional[Decimal],
        wifi_ssid: Optional[str],
        photo_url: Optional[str],
        is_mock: bool,
    ) -> List[str]:
        """
        根据考勤规则校验打卡数据，返回所有发现的异常描述列表。

        校验逻辑（依规则配置按需执行）：
        1. 模拟定位检测：is_mock=True 时直接记录异常（无论规则配置）
        2. 手机打卡方式校验：
           - 默认对齐企业微信口径，GPS 或 Wi-Fi 任一满足即可通过
           - check_method_mode=all/both 时保留扩展能力，要求已配置方式均满足
           - 支持 extra_config.locations / wifi_list 多地点、多 Wi-Fi
        3. 照片校验：仅开启“每次打卡均需拍照”时，普通上下班卡无照片记为异常；
           旧规则没有该选项时沿用 require_photo。

        参数:
            rule: 适用的考勤规则
            gps_lat/gps_lng: 打卡时的 GPS 坐标
            wifi_ssid: 当前连接的 WiFi SSID
            photo_url: 打卡照片 URL
            is_mock: 是否检测到模拟定位

        返回:
            异常描述字符串列表（空列表表示校验通过）
        """
        errors: List[str] = []

        if is_mock:
            errors.append("检测到模拟定位")

        extra = AttendanceRecordService._extra(rule)
        configured_locations = AttendanceRecordService._configured_locations(rule, extra)
        configured_wifi_names = AttendanceRecordService._configured_wifi_names(rule, extra)
        check_methods = AttendanceRecordService._parse_text_set(extra.get("check_methods"))
        require_gps = bool(rule.require_gps) or "GPS" in check_methods
        require_wifi = bool(rule.require_wifi) or "WiFi" in check_methods
        method_mode = str(extra.get("check_method_mode") or "any").lower()
        require_all_methods = method_mode in {"all", "both", "and"}

        gps_errors: List[str] = []
        gps_valid = not require_gps
        geofence_outside_error: Optional[str] = None
        if gps_lat is not None and gps_lng is not None and configured_locations:
            nearest_any: Optional[Tuple[float, int]] = None
            geofence_passed = False
            for loc in configured_locations:
                radius = int(float(loc.get("radius") or 300))
                dist = AttendanceRecordService._haversine_meters(
                    float(loc.get("latitude")),
                    float(loc.get("longitude")),
                    float(gps_lat),
                    float(gps_lng),
                )
                if nearest_any is None or dist < nearest_any[0]:
                    nearest_any = (dist, radius)
                if dist <= radius:
                    geofence_passed = True
                    break
            if not geofence_passed and nearest_any is not None:
                geofence_outside_error = f"超出打卡范围: 距离{nearest_any[0]:.0f}米, 限制{nearest_any[1]}米"

        if require_gps:
            if gps_lat is None or gps_lng is None:
                gps_errors.append("缺少GPS定位信息")
            elif configured_locations:
                nearest: Optional[Tuple[float, int]] = None
                for loc in configured_locations:
                    radius = int(float(loc.get("radius") or 300))
                    dist = AttendanceRecordService._haversine_meters(
                        float(loc.get("latitude")),
                        float(loc.get("longitude")),
                        float(gps_lat),
                        float(gps_lng),
                    )
                    if nearest is None or dist < nearest[0]:
                        nearest = (dist, radius)
                    if dist <= radius:
                        gps_valid = True
                        break
                if not gps_valid and nearest is not None:
                    gps_errors.append(f"超出打卡范围: 距离{nearest[0]:.0f}米, 限制{nearest[1]}米")
            else:
                gps_valid = True

        wifi_errors: List[str] = []
        wifi_valid = not require_wifi
        if require_wifi:
            incoming_wifi = str(wifi_ssid or "").strip()
            if not incoming_wifi:
                wifi_errors.append("缺少WiFi信息")
            elif configured_wifi_names and incoming_wifi not in configured_wifi_names:
                wifi_errors.append(
                    f"WiFi不匹配: 需要{'/'.join(sorted(configured_wifi_names))}, 实际{incoming_wifi}"
                )
            else:
                wifi_valid = True

        if require_gps and require_wifi and not require_all_methods:
            if not (gps_valid or wifi_valid):
                errors.extend(gps_errors or ["GPS不符合打卡规则"])
                errors.extend(wifi_errors or ["WiFi不符合打卡规则"])
        else:
            if require_gps and not gps_valid:
                errors.extend(gps_errors or ["GPS不符合打卡规则"])
            if require_wifi and not wifi_valid:
                errors.extend(wifi_errors or ["WiFi不符合打卡规则"])

        has_alternative_method_passed = (
            require_gps
            and require_wifi
            and not require_all_methods
            and wifi_valid
        )
        if geofence_outside_error and geofence_outside_error not in errors and not has_alternative_method_passed:
            errors.append(geofence_outside_error)

        if AttendanceRecordService._requires_photo_each_punch(rule) and not photo_url:
            errors.append("缺少打卡照片")

        return errors

    @staticmethod
    def _determine_status(
        rule: AttendanceRule,
        clock_in_time: Optional[datetime],
        clock_out_time: Optional[datetime],
        target_date: date,
        *,
        expected_in: Optional[time] = None,
        expected_out: Optional[time] = None,
        check_in_required: bool = True,
        check_out_required: bool = True,
        flex_mode: str = "none",
        early_threshold_minutes: int = 0,
        half_day_pm_start: Optional[time] = None,
        half_day_absence_cutoff: Optional[time] = None,
    ) -> AttendanceStatus:
        """
        根据考勤规则和实际打卡时间，判定本日的考勤状态。

        状态判定规则（按工时制分支）：

        【弹性工时 flexible】（适用北京研发团队）：
        - 上下班打卡都有 → normal（只要打了卡即正常）
        - 缺任意一次打卡 → missed_clock（缺卡）

        【标准/综合工时】：
        - 上班时间超过"规则上班时间 + 显式迟到宽限分钟" → late（迟到）
        - 下班时间早于"规则下班时间" → early_leave（早退）
        - 缺任意一次打卡 → missed_clock（缺卡）
        - 上下班均无打卡 → absent（旷工，在方法入口处提前判断）
        - 无以上异常 → normal

        参数:
            rule: 适用的考勤规则（包含 clock_in_time, clock_out_time, flexible_minutes）
            clock_in_time: 实际上班打卡时间（UTC）
            clock_out_time: 实际下班打卡时间（UTC）
            target_date: 考勤日期（用于将规则时间转为完整 datetime 进行比较）

        返回:
            AttendanceStatus 枚举值
        """
        rule_type = AttendanceRecordService._infer_rule_type(rule, AttendanceRecordService._extra(rule))
        if clock_in_time is None and clock_out_time is None:
            return AttendanceStatus.absent

        if check_in_required and clock_in_time is None:
            return AttendanceStatus.missed_clock
        if check_out_required and clock_out_time is None:
            return AttendanceStatus.missed_clock
        if (clock_in_time is None) != (clock_out_time is None):
            return AttendanceStatus.missed_clock

        if clock_in_time is None or clock_out_time is None:
            return AttendanceStatus.missed_clock

        if rule.work_hour_type == WorkHourType.flexible or rule_type == "free":
            # 自由/弹性工时：两张必需卡齐全后，不按固定上下班时刻判迟到早退。
            return AttendanceStatus.normal

        expected_in = expected_in or rule.clock_in_time
        expected_out = expected_out or rule.clock_out_time
        if expected_in is None or expected_out is None:
            return AttendanceStatus.normal

        cst = AttendanceRecordService._cst()
        actual_in = AttendanceRecordService._truncate_datetime_to_minute(
            AttendanceRecordService._to_cst_datetime(clock_in_time)
        )
        actual_out = AttendanceRecordService._truncate_datetime_to_minute(
            AttendanceRecordService._to_cst_datetime(clock_out_time)
        )
        if actual_in is None or actual_out is None:
            return AttendanceStatus.missed_clock
        expected_in_dt = AttendanceRecordService._truncate_datetime_to_minute(
            datetime.combine(target_date, expected_in, tzinfo=cst)
        )
        expected_out_dt = AttendanceRecordService._truncate_datetime_to_minute(
            datetime.combine(target_date, expected_out, tzinfo=cst)
        )
        if expected_out_dt <= expected_in_dt:
            expected_out_dt += timedelta(days=1)
        if actual_out < actual_in:
            actual_out += timedelta(days=1)

        late_minutes = (actual_in - expected_in_dt).total_seconds() / 60.0
        early_minutes = (expected_out_dt - actual_out).total_seconds() / 60.0
        late_allow = AttendanceRecordService._late_allow_minutes(rule, flex_mode)
        early_allow = int(early_threshold_minutes or 0)
        half_day_pm_start = half_day_pm_start or AttendanceRecordService._half_day_pm_start_for_rule(rule, target_date)
        half_day_absence_cutoff = half_day_absence_cutoff or AttendanceRecordService._half_day_absence_cutoff_for_rule(rule, target_date)
        if check_in_required and AttendanceRecordService._half_day_absence_late_minutes(
            actual_in,
            target_date,
            half_day_pm_start,
            late_allow,
            half_day_absence_cutoff,
        ) is not None:
            return AttendanceStatus.absent

        if flex_mode == "late_leave":
            if early_minutes > early_allow:
                return AttendanceStatus.early_leave
            owed_late_minutes = max(0.0, late_minutes - late_allow)
            out_late_minutes = max(0.0, (actual_out - expected_out_dt).total_seconds() / 60.0)
            if owed_late_minutes > out_late_minutes:
                return AttendanceStatus.late
            return AttendanceStatus.normal
        if flex_mode == "both":
            # 允许“晚到晚走、早到早走”，只要偏移被同向补齐则判正常
            in_offset = (actual_in - expected_in_dt).total_seconds()
            out_offset = (actual_out - expected_out_dt).total_seconds()
            if in_offset >= 0 and out_offset >= 0 and out_offset >= in_offset:
                return AttendanceStatus.normal
            if in_offset <= 0 and out_offset <= 0 and abs(out_offset) <= abs(in_offset):
                return AttendanceStatus.normal

        if early_minutes > early_allow:
            return AttendanceStatus.early_leave
        if late_minutes > late_allow:
            return AttendanceStatus.late

        return AttendanceStatus.normal

    @staticmethod
    def _determine_clock_in_status(
        rule: AttendanceRule,
        clock_in_time: Optional[datetime],
        target_date: date,
        *,
        expected_in: Optional[time] = None,
        check_in_required: bool = True,
        flex_mode: str = "none",
        half_day_pm_start: Optional[time] = None,
        half_day_absence_cutoff: Optional[time] = None,
    ) -> AttendanceStatus:
        """
        上班打卡后立即给出阶段性状态，避免迟到员工在下班前被显示为正常。
        最终状态仍会在下班打卡后由 _determine_status 统一覆盖。
        """
        rule_type = AttendanceRecordService._infer_rule_type(rule, AttendanceRecordService._extra(rule))
        if clock_in_time is None:
            return AttendanceStatus.missed_clock if check_in_required else AttendanceStatus.normal
        if rule.work_hour_type == WorkHourType.flexible or rule_type == "free":
            return AttendanceStatus.normal
        if not check_in_required:
            return AttendanceStatus.normal

        expected_in = expected_in or rule.clock_in_time
        if expected_in is None:
            return AttendanceStatus.normal

        if flex_mode == "late_leave":
            return AttendanceStatus.normal

        cst = AttendanceRecordService._cst()
        actual_in = AttendanceRecordService._truncate_datetime_to_minute(
            AttendanceRecordService._to_cst_datetime(clock_in_time)
        )
        if actual_in is None:
            return AttendanceStatus.missed_clock
        expected_in_dt = AttendanceRecordService._truncate_datetime_to_minute(
            datetime.combine(target_date, expected_in, tzinfo=cst)
        )
        late_minutes = (actual_in - expected_in_dt).total_seconds() / 60.0
        late_allow = AttendanceRecordService._late_allow_minutes(rule, flex_mode)
        half_day_pm_start = half_day_pm_start or AttendanceRecordService._half_day_pm_start_for_rule(rule, target_date)
        half_day_absence_cutoff = half_day_absence_cutoff or AttendanceRecordService._half_day_absence_cutoff_for_rule(rule, target_date)
        if AttendanceRecordService._half_day_absence_late_minutes(
            clock_in_time,
            target_date,
            half_day_pm_start,
            late_allow,
            half_day_absence_cutoff,
        ) is not None:
            return AttendanceStatus.absent
        return AttendanceStatus.late if late_minutes > late_allow else AttendanceStatus.normal

    @staticmethod
    def _preview_punch_status(
        rule: AttendanceRule,
        phase: str,
        punch_time: datetime,
        target_date: date,
        *,
        expected_in: Optional[time] = None,
        expected_out: Optional[time] = None,
        check_in_required: bool = True,
        check_out_required: bool = True,
        flex_mode: str = "none",
        early_threshold_minutes: int = 0,
        half_day_pm_start: Optional[time] = None,
        half_day_absence_cutoff: Optional[time] = None,
    ) -> Dict[str, Any]:
        """预判当前按钮打卡的状态，供移动端按企业微信式体验做即时提示。"""
        rule_type = AttendanceRecordService._infer_rule_type(rule, AttendanceRecordService._extra(rule))
        if rule.work_hour_type == WorkHourType.flexible or rule_type == "free":
            return {"status": AttendanceStatus.normal, "minutes": 0}

        cst = AttendanceRecordService._cst()
        actual = AttendanceRecordService._truncate_datetime_to_minute(
            AttendanceRecordService._to_cst_datetime(punch_time)
        )
        if actual is None:
            return {"status": AttendanceStatus.normal, "minutes": 0}

        if phase == "clock_in":
            if not check_in_required:
                return {"status": AttendanceStatus.normal, "minutes": 0}
            expected_in = expected_in or rule.clock_in_time
            if expected_in is None or flex_mode in ("late_leave", "both"):
                return {"status": AttendanceStatus.normal, "minutes": 0}
            expected_dt = AttendanceRecordService._truncate_datetime_to_minute(
                datetime.combine(target_date, expected_in, tzinfo=cst)
            )
            late_allow = AttendanceRecordService._late_allow_minutes(rule, flex_mode)
            late_minutes = (actual - expected_dt).total_seconds() / 60.0
            half_day_pm_start = half_day_pm_start or AttendanceRecordService._half_day_pm_start_for_rule(rule, target_date)
            half_day_absence_cutoff = half_day_absence_cutoff or AttendanceRecordService._half_day_absence_cutoff_for_rule(rule, target_date)
            half_day_late_minutes = AttendanceRecordService._half_day_absence_late_minutes(
                punch_time,
                target_date,
                half_day_pm_start,
                late_allow,
                half_day_absence_cutoff,
            )
            if half_day_late_minutes is not None:
                return {"status": AttendanceStatus.absent, "minutes": half_day_late_minutes}
            if late_minutes > late_allow:
                return {"status": AttendanceStatus.late, "minutes": max(1, math.ceil(late_minutes - late_allow))}
            return {"status": AttendanceStatus.normal, "minutes": 0}

        if phase == "clock_out":
            if not check_out_required:
                return {"status": AttendanceStatus.normal, "minutes": 0}
            expected_out = expected_out or rule.clock_out_time
            if expected_out is None:
                return {"status": AttendanceStatus.normal, "minutes": 0}
            expected_dt = AttendanceRecordService._truncate_datetime_to_minute(
                datetime.combine(target_date, expected_out, tzinfo=cst)
            )
            expected_in = expected_in or rule.clock_in_time
            if expected_in and expected_out <= expected_in:
                expected_dt += timedelta(days=1)
            early_allow = int(early_threshold_minutes or 0)
            early_minutes = (expected_dt - actual).total_seconds() / 60.0
            if early_minutes <= 0:
                return {"status": AttendanceStatus.normal, "minutes": 0}
            if early_minutes > early_allow and flex_mode not in ("late_leave", "both"):
                return {"status": AttendanceStatus.early_leave, "minutes": max(1, math.ceil(early_minutes - early_allow))}
            return {"status": "before_off_duty", "minutes": max(1, math.ceil(early_minutes))}

        return {"status": AttendanceStatus.normal, "minutes": 0}

    @staticmethod
    def _truncate_datetime_to_minute(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        return value.replace(second=0, microsecond=0)

    @staticmethod
    def _status_label(status_value: Any) -> str:
        status = getattr(status_value, "value", status_value)
        return {
            AttendanceStatus.normal.value: "正常",
            AttendanceStatus.late.value: "迟到",
            AttendanceStatus.early_leave.value: "早退",
            AttendanceStatus.missed_clock.value: "缺卡",
            AttendanceStatus.absent.value: "旷工",
            "before_off_duty": "未到下班时间",
            "done": "已完成",
        }.get(str(status), str(status or "正常"))

    @staticmethod
    def _append_display_status_part(parts: List[str], part: Optional[str]) -> None:
        text = str(part or "").strip()
        if text and text not in {"-", "--", "正常"} and text not in parts:
            parts.append(text)

    @staticmethod
    def _display_status_parts_from_anomaly(record: AttendanceRecord) -> List[str]:
        anomaly_text = str(getattr(record, "anomaly_type", "") or "")
        parts: List[str] = []
        if not anomaly_text:
            return parts
        if "缺少上班打卡" in anomaly_text or "缺少下班打卡" in anomaly_text or "缺卡" in anomaly_text:
            AttendanceRecordService._append_display_status_part(parts, "缺卡")
        if "旷工半天" in anomaly_text or "旷工" in anomaly_text:
            AttendanceRecordService._append_display_status_part(parts, "旷工")
        if "工时不足" in anomaly_text:
            AttendanceRecordService._append_display_status_part(parts, "异常")
        for item in re.split(r"[;；/、,，]", anomaly_text):
            text = item.strip()
            if text.startswith("迟到"):
                AttendanceRecordService._append_display_status_part(parts, "迟到")
            elif text.startswith("早退"):
                AttendanceRecordService._append_display_status_part(parts, "早退")
        if AttendanceRecordService._has_location_anomaly(record):
            AttendanceRecordService._append_display_status_part(parts, "打卡位置异常")
        return parts

    @staticmethod
    def _format_display_status_parts(parts: List[str], default: str = "正常") -> str:
        cleaned: List[str] = []
        for part in parts:
            AttendanceRecordService._append_display_status_part(cleaned, part)
        if not cleaned:
            default_text = str(default or "").strip()
            return default_text if default_text and default_text not in {"-", "--"} else "正常"
        priority = ["缺卡", "旷工", "迟到", "早退", "打卡位置异常", "异常", "待上班打卡", "待下班打卡"]
        ordered = [part for part in priority if part in cleaned]
        ordered.extend(part for part in cleaned if part not in ordered)
        return "/".join(ordered)

    @staticmethod
    def _has_location_anomaly(record: AttendanceRecord) -> bool:
        text = str(getattr(record, "anomaly_type", "") or "")
        if not text:
            return False
        keywords = (
            "超出打卡范围",
            "GPS",
            "WiFi",
            "定位",
            "模拟定位",
            "不在可打卡时段",
            "打卡范围",
        )
        return any(word in text for word in keywords)

    @staticmethod
    def _is_outside_rule_geofence(rule: Optional[AttendanceRule], lat: Optional[Decimal], lng: Optional[Decimal]) -> bool:
        if rule is None or lat is None or lng is None:
            return False
        extra = AttendanceRecordService._extra(rule)
        locations = AttendanceRecordService._configured_locations(rule, extra)
        if not locations:
            return False
        in_range = False
        for loc in locations:
            radius = int(float(loc.get("radius") or 300))
            dist = AttendanceRecordService._haversine_meters(
                float(loc.get("latitude")),
                float(loc.get("longitude")),
                float(lat),
                float(lng),
            )
            if dist <= radius:
                in_range = True
                break
        return not in_range

    @staticmethod
    def _record_has_outside_geofence(record: AttendanceRecord, rule: Optional[AttendanceRule]) -> bool:
        return (
            AttendanceRecordService._is_outside_rule_geofence(rule, record.clock_in_gps_lat, record.clock_in_gps_lng)
            or AttendanceRecordService._is_outside_rule_geofence(rule, record.clock_out_gps_lat, record.clock_out_gps_lng)
        )

    @staticmethod
    def _punch_window_end_datetime(target_date: date, runtime: Optional[Dict[str, Any]]) -> Optional[datetime]:
        if runtime is None:
            return None
        start_hm, end_hm = AttendanceRecordService._window_from_runtime(runtime)
        start_t = AttendanceRecordService._parse_hm(start_hm)
        end_t = AttendanceRecordService._parse_hm(end_hm)
        cst = AttendanceRecordService._cst()
        if end_t is not None:
            end_dt = datetime.combine(target_date, end_t, tzinfo=cst)
            if start_t is not None and end_t < start_t:
                end_dt += timedelta(days=1)
            return AttendanceRecordService._truncate_datetime_to_minute(end_dt)

        rule = runtime.get("rule")
        expected_out = runtime.get("expected_out") or (rule.clock_out_time if rule else None)
        expected_in = runtime.get("expected_in") or (rule.clock_in_time if rule else None)
        if expected_out is None:
            return None
        end_dt = datetime.combine(target_date, expected_out, tzinfo=cst)
        if expected_in is not None and expected_out <= expected_in:
            end_dt += timedelta(days=1)
        return AttendanceRecordService._truncate_datetime_to_minute(end_dt)

    @staticmethod
    def _punch_window_has_ended(
        target_date: date,
        runtime: Optional[Dict[str, Any]],
        *,
        as_of: Optional[datetime] = None,
    ) -> bool:
        cst = AttendanceRecordService._cst()
        now = AttendanceRecordService._to_cst_datetime(as_of or datetime.now(timezone.utc))
        now = AttendanceRecordService._truncate_datetime_to_minute(now)
        if now is None:
            return False
        window_end = AttendanceRecordService._punch_window_end_datetime(target_date, runtime)
        if window_end is None:
            return target_date < now.date()
        return now > window_end

    @staticmethod
    def _pending_required_punch_phase(record: AttendanceRecord, runtime: Optional[Dict[str, Any]]) -> Optional[str]:
        if runtime is None or AttendanceRecordService._punch_window_has_ended(record.date, runtime):
            return None
        if AttendanceRecordService._record_phase_required(record, runtime, "clock_in") and record.clock_in_time is None:
            return "clock_in"
        if AttendanceRecordService._record_phase_required(record, runtime, "clock_out") and record.clock_out_time is None:
            return "clock_out"
        return None

    @staticmethod
    def _record_phase_required(record: AttendanceRecord, runtime: Optional[Dict[str, Any]], phase: str) -> bool:
        if runtime is None:
            return True
        key = "check_in_required" if phase == "clock_in" else "check_out_required"
        if bool(runtime.get("no_punch")):
            return False
        if bool(runtime.get(key, True)):
            return True
        if not bool(runtime.get("no_punch")):
            return False
        return False

    @staticmethod
    def _determine_incomplete_record_status(
        rule: AttendanceRule,
        record: AttendanceRecord,
        target_date: date,
        runtime: Optional[Dict[str, Any]],
        *,
        as_of: Optional[datetime] = None,
    ) -> AttendanceStatus:
        if bool((runtime or {}).get("no_punch")):
            return AttendanceStatus.normal
        window_ended = AttendanceRecordService._punch_window_has_ended(target_date, runtime, as_of=as_of)
        check_in_required = AttendanceRecordService._record_phase_required(record, runtime, "clock_in")
        check_out_required = AttendanceRecordService._record_phase_required(record, runtime, "clock_out")
        if window_ended:
            if check_in_required and record.clock_in_time is None:
                return AttendanceStatus.missed_clock
            if check_out_required and record.clock_out_time is None:
                return AttendanceStatus.missed_clock

        if record.clock_in_time:
            return AttendanceRecordService._determine_clock_in_status(
                rule,
                record.clock_in_time,
                target_date,
                expected_in=(runtime or {}).get("expected_in"),
                check_in_required=check_in_required,
                flex_mode=str((runtime or {}).get("flex_mode") or "none"),
                half_day_pm_start=(runtime or {}).get("half_day_pm_start"),
                half_day_absence_cutoff=(runtime or {}).get("half_day_absence_cutoff"),
            )
        if record.clock_out_time:
            return AttendanceStatus.normal if not window_ended else AttendanceStatus.missed_clock
        return AttendanceStatus.absent if window_ended else AttendanceStatus.normal

    @staticmethod
    def _record_display_status(record: AttendanceRecord, runtime: Optional[Dict[str, Any]]) -> str:
        """
        管理端状态列需要展示“当天发生过的异常组合”，例如上班迟到且下班早退时显示
        “迟到/早退”。数据库 status 仍保留主状态，展示态在响应阶段计算。
        """
        if bool((runtime or {}).get("no_punch")):
            return "免打卡"
        status_text = AttendanceRecordService._status_label(record.status)
        if status_text in {"请假", "出差"}:
            return status_text

        rule = runtime["rule"] if runtime else None
        has_location_anomaly = (
            AttendanceRecordService._has_location_anomaly(record)
            or AttendanceRecordService._record_has_outside_geofence(record, rule)
        )

        anomaly_parts = AttendanceRecordService._display_status_parts_from_anomaly(record)

        def fallback_display(extra_parts: Optional[List[str]] = None, *, include_status: bool = True) -> str:
            parts = list(anomaly_parts)
            if include_status and status_text in {"缺卡", "旷工", "迟到", "早退", "打卡位置异常", "异常"}:
                AttendanceRecordService._append_display_status_part(parts, status_text)
            for part in extra_parts or []:
                AttendanceRecordService._append_display_status_part(parts, part)
            if has_location_anomaly:
                AttendanceRecordService._append_display_status_part(parts, "打卡位置异常")
            return AttendanceRecordService._format_display_status_parts(parts, status_text or "正常")

        if rule is None:
            return fallback_display()

        rule_type = AttendanceRecordService._infer_rule_type(rule, AttendanceRecordService._extra(rule))
        if rule.work_hour_type == WorkHourType.flexible or rule_type == "free":
            return fallback_display()

        pending_phase = AttendanceRecordService._pending_required_punch_phase(record, runtime)
        if record.clock_in_time and not record.clock_out_time:
            clock_in_required = AttendanceRecordService._record_phase_required(record, runtime, "clock_in")
            clock_in_status = AttendanceRecordService._determine_clock_in_status(
                rule,
                record.clock_in_time,
                record.date,
                expected_in=(runtime or {}).get("expected_in"),
                check_in_required=clock_in_required,
                flex_mode=str((runtime or {}).get("flex_mode") or "none"),
                half_day_pm_start=(runtime or {}).get("half_day_pm_start"),
                half_day_absence_cutoff=(runtime or {}).get("half_day_absence_cutoff"),
            )
            clock_in_text = AttendanceRecordService._status_label(clock_in_status)
            parts = list(anomaly_parts)
            if pending_phase == "clock_out":
                if clock_in_text in {"迟到", "旷工"}:
                    AttendanceRecordService._append_display_status_part(parts, clock_in_text)
                if has_location_anomaly:
                    AttendanceRecordService._append_display_status_part(parts, "打卡位置异常")
                AttendanceRecordService._append_display_status_part(parts, "待下班打卡")
                return AttendanceRecordService._format_display_status_parts(parts, "待下班打卡")
            if clock_in_text in {"迟到", "缺卡", "旷工"}:
                AttendanceRecordService._append_display_status_part(parts, clock_in_text)
            if has_location_anomaly:
                AttendanceRecordService._append_display_status_part(parts, "打卡位置异常")
            return AttendanceRecordService._format_display_status_parts(parts, status_text or "正常")

        if pending_phase == "clock_in":
            parts = list(anomaly_parts)
            if has_location_anomaly:
                AttendanceRecordService._append_display_status_part(parts, "打卡位置异常")
            AttendanceRecordService._append_display_status_part(parts, "待上班打卡")
            return AttendanceRecordService._format_display_status_parts(parts, "待上班打卡")

        if not (record.clock_in_time and record.clock_out_time):
            return fallback_display()

        expected_in = (runtime or {}).get("expected_in") or rule.clock_in_time
        expected_out = (runtime or {}).get("expected_out") or rule.clock_out_time
        if expected_in is None or expected_out is None:
            return fallback_display()

        cst = AttendanceRecordService._cst()
        actual_in = AttendanceRecordService._truncate_datetime_to_minute(
            AttendanceRecordService._to_cst_datetime(record.clock_in_time)
        )
        actual_out = AttendanceRecordService._truncate_datetime_to_minute(
            AttendanceRecordService._to_cst_datetime(record.clock_out_time)
        )
        if actual_in is None or actual_out is None:
            return fallback_display()

        expected_in_dt = AttendanceRecordService._truncate_datetime_to_minute(
            datetime.combine(record.date, expected_in, tzinfo=cst)
        )
        expected_out_dt = AttendanceRecordService._truncate_datetime_to_minute(
            datetime.combine(record.date, expected_out, tzinfo=cst)
        )
        if expected_out_dt <= expected_in_dt:
            expected_out_dt += timedelta(days=1)
        if actual_out < actual_in:
            actual_out += timedelta(days=1)

        parts: List[str] = list(anomaly_parts)
        late_allow = AttendanceRecordService._late_allow_minutes(rule, str((runtime or {}).get("flex_mode") or "none"))
        early_allow = int((runtime or {}).get("early_threshold_minutes") or 0)
        check_in_required = AttendanceRecordService._record_phase_required(record, runtime, "clock_in")
        check_out_required = AttendanceRecordService._record_phase_required(record, runtime, "clock_out")
        half_day_absence_minutes = AttendanceRecordService._half_day_absence_late_minutes(
            record.clock_in_time,
            record.date,
            (runtime or {}).get("half_day_pm_start"),
            late_allow,
            (runtime or {}).get("half_day_absence_cutoff"),
        )

        if check_in_required and half_day_absence_minutes is not None:
            AttendanceRecordService._append_display_status_part(parts, "旷工")
        elif check_in_required and (actual_in - expected_in_dt).total_seconds() / 60.0 > late_allow:
            AttendanceRecordService._append_display_status_part(parts, "迟到")
        if check_out_required and (expected_out_dt - actual_out).total_seconds() / 60.0 > early_allow:
            AttendanceRecordService._append_display_status_part(parts, "早退")

        if has_location_anomaly:
            AttendanceRecordService._append_display_status_part(parts, "打卡位置异常")

        return AttendanceRecordService._format_display_status_parts(parts, "正常")

    @staticmethod
    def _record_clock_status(record: AttendanceRecord, runtime: Optional[Dict[str, Any]], phase: str) -> str:
        if bool((runtime or {}).get("no_punch")):
            return "免打卡"
        punch_time = record.clock_in_time if phase == "clock_in" else record.clock_out_time
        if punch_time is None:
            return "未打卡"
        rule = runtime["rule"] if runtime else None
        if rule is None:
            return "正常"
        phase_required = AttendanceRecordService._record_phase_required(record, runtime, phase)
        preview = AttendanceRecordService._preview_punch_status(
            rule,
            phase,
            punch_time,
            record.date,
            expected_in=(runtime or {}).get("expected_in"),
            expected_out=(runtime or {}).get("expected_out"),
            check_in_required=phase_required if phase == "clock_in" else bool((runtime or {}).get("check_in_required", True)),
            check_out_required=phase_required if phase == "clock_out" else bool((runtime or {}).get("check_out_required", True)),
            flex_mode=str((runtime or {}).get("flex_mode") or "none"),
            early_threshold_minutes=int((runtime or {}).get("early_threshold_minutes") or 0),
            half_day_pm_start=(runtime or {}).get("half_day_pm_start"),
            half_day_absence_cutoff=(runtime or {}).get("half_day_absence_cutoff"),
        )
        status_value = getattr(preview.get("status"), "value", preview.get("status"))
        if phase == "clock_in":
            if status_value == AttendanceStatus.absent.value:
                return "旷工"
            return "迟到" if status_value == AttendanceStatus.late.value else "正常"
        return "早退" if status_value == AttendanceStatus.early_leave.value else "正常"

    @staticmethod
    def _record_location_abnormal(record: AttendanceRecord, runtime: Optional[Dict[str, Any]]) -> bool:
        rule = runtime["rule"] if runtime else None
        return (
            AttendanceRecordService._has_location_anomaly(record)
            or AttendanceRecordService._record_has_outside_geofence(record, rule)
        )

    @staticmethod
    def _record_punch_location_abnormal(record: AttendanceRecord, runtime: Optional[Dict[str, Any]], phase: str) -> bool:
        rule = runtime["rule"] if runtime else None
        anomaly_text = str(getattr(record, "anomaly_type", "") or "")
        if "检测到模拟定位" in anomaly_text or "模拟定位" in anomaly_text:
            return True
        if phase == "clock_in":
            lat = record.clock_in_gps_lat
            lng = record.clock_in_gps_lng
            text = str(getattr(record, "clock_in_location", "") or "")
        else:
            lat = record.clock_out_gps_lat
            lng = record.clock_out_gps_lng
            text = str(getattr(record, "clock_out_location", "") or "")
        if lat is not None and lng is not None:
            return AttendanceRecordService._is_outside_rule_geofence(rule, lat, lng)
        location_keywords = ("超出打卡范围", "打卡位置异常", "不在打卡范围", "异地打卡")
        return any(keyword in text or keyword in anomaly_text for keyword in location_keywords)

    @staticmethod
    def _attach_display_status(record: AttendanceRecord, runtime: Optional[Dict[str, Any]]) -> None:
        if (runtime or {}).get("project_segments"):
            record.project_segments = runtime["project_segments"]
            record.display_status = "待核对" if any(s["needs_review"] for s in record.project_segments) else ("异常" if record.anomaly_type else "正常")
            record.clock_in_status = record.clock_out_status = "见项目分段"
            return
        if bool((runtime or {}).get("no_punch")):
            setattr(record, "display_status", "免打卡")
            setattr(record, "clock_in_status", "免打卡")
            setattr(record, "clock_out_status", "免打卡")
            setattr(record, "location_abnormal", False)
            setattr(record, "location_status", "免打卡")
            setattr(record, "clock_in_location_abnormal", False)
            setattr(record, "clock_out_location_abnormal", False)
            setattr(record, "clock_in_location_status", "免打卡")
            setattr(record, "clock_out_location_status", "免打卡")
            return
        clock_in_location_abnormal = AttendanceRecordService._record_punch_location_abnormal(record, runtime, "clock_in")
        clock_out_location_abnormal = AttendanceRecordService._record_punch_location_abnormal(record, runtime, "clock_out")
        location_abnormal = (
            AttendanceRecordService._record_location_abnormal(record, runtime)
            or clock_in_location_abnormal
            or clock_out_location_abnormal
        )
        setattr(record, "display_status", AttendanceRecordService._record_display_status(record, runtime))
        setattr(record, "clock_in_status", AttendanceRecordService._record_clock_status(record, runtime, "clock_in"))
        setattr(record, "clock_out_status", AttendanceRecordService._record_clock_status(record, runtime, "clock_out"))
        setattr(record, "location_abnormal", location_abnormal)
        setattr(record, "location_status", "打卡位置异常" if location_abnormal else "范围内")
        setattr(record, "clock_in_location_abnormal", clock_in_location_abnormal)
        setattr(record, "clock_out_location_abnormal", clock_out_location_abnormal)
        setattr(record, "clock_in_location_status", "打卡位置异常" if clock_in_location_abnormal else "范围内")
        setattr(record, "clock_out_location_status", "打卡位置异常" if clock_out_location_abnormal else "范围内")

    @staticmethod
    def _append_status_anomalies(
        record: AttendanceRecord,
        runtime: Optional[Dict[str, Any]],
        anomalies: Optional[List[str]],
    ) -> None:
        """把“迟到7分钟/早退90分钟”这类可展示原因同步进异常列表。"""
        if anomalies is None or runtime is None:
            return
        if bool(runtime.get("no_punch")):
            return
        rule = runtime.get("rule")
        if rule is None:
            return
        rule_type = AttendanceRecordService._infer_rule_type(rule, AttendanceRecordService._extra(rule))
        if rule.work_hour_type == WorkHourType.flexible or rule_type == "free":
            return

        expected_in = runtime.get("expected_in") or rule.clock_in_time
        expected_out = runtime.get("expected_out") or rule.clock_out_time
        cst = AttendanceRecordService._cst()
        flex_mode = str(runtime.get("flex_mode") or "none")
        late_allow = AttendanceRecordService._late_allow_minutes(rule, flex_mode)
        early_allow = int(runtime.get("early_threshold_minutes") or 0)
        window_ended = AttendanceRecordService._punch_window_has_ended(record.date, runtime)
        status_value = getattr(getattr(record, "status", None), "value", getattr(record, "status", None))

        def replace_status_issue(prefix: str, minutes: int) -> None:
            if minutes <= 0:
                return
            anomalies[:] = [item for item in anomalies if not str(item).strip().startswith(prefix)]
            anomalies.append(f"{prefix}{minutes}分钟")

        if window_ended and status_value == AttendanceStatus.missed_clock.value:
            if AttendanceRecordService._record_phase_required(record, runtime, "clock_in") and record.clock_in_time is None:
                if "缺少上班打卡" not in anomalies:
                    anomalies.append("缺少上班打卡")
            if AttendanceRecordService._record_phase_required(record, runtime, "clock_out") and record.clock_out_time is None:
                if "缺少下班打卡" not in anomalies:
                    anomalies.append("缺少下班打卡")

        if record.clock_in_time and expected_in and bool(runtime.get("check_in_required", True)):
            actual_in = AttendanceRecordService._truncate_datetime_to_minute(
                AttendanceRecordService._to_cst_datetime(record.clock_in_time)
            )
            if actual_in is not None:
                expected_in_dt = AttendanceRecordService._truncate_datetime_to_minute(
                    datetime.combine(record.date, expected_in, tzinfo=cst)
                )
                half_day_absence_minutes = AttendanceRecordService._half_day_absence_late_minutes(
                    record.clock_in_time,
                    record.date,
                    runtime.get("half_day_pm_start"),
                    late_allow,
                    runtime.get("half_day_absence_cutoff"),
                )
                if half_day_absence_minutes is not None:
                    anomalies[:] = [
                        item
                        for item in anomalies
                        if not str(item).strip().startswith("迟到")
                        and str(item).strip() != "旷工半天"
                    ]
                    anomalies.append("旷工半天")
                    replace_status_issue("迟到", half_day_absence_minutes)
                else:
                    late_minutes = (actual_in - expected_in_dt).total_seconds() / 60.0 - late_allow
                    replace_status_issue("迟到", math.ceil(late_minutes))

        if record.clock_out_time and expected_out and bool(runtime.get("check_out_required", True)):
            actual_out = AttendanceRecordService._truncate_datetime_to_minute(
                AttendanceRecordService._to_cst_datetime(record.clock_out_time)
            )
            if actual_out is not None:
                expected_out_dt = AttendanceRecordService._truncate_datetime_to_minute(
                    datetime.combine(record.date, expected_out, tzinfo=cst)
                )
                if expected_in and expected_out <= expected_in:
                    expected_out_dt += timedelta(days=1)
                early_minutes = (expected_out_dt - actual_out).total_seconds() / 60.0 - early_allow
                replace_status_issue("早退", math.ceil(early_minutes))

    @staticmethod
    def _rebuild_anomaly_text_with_status(
        record: AttendanceRecord,
        runtime: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if (runtime or {}).get("project_segments") or getattr(record, "project_segments", None):
            return record.anomaly_type
        parts = [
            part.strip()
            for part in str(getattr(record, "anomaly_type", "") or "").split(";")
            if part.strip()
        ]
        non_status_parts = [
            part
            for part in parts
            if not (
                part.startswith("迟到")
                or part.startswith("早退")
                or part.startswith("工时不足")
                or part in {"旷工半天", "缺少上班打卡", "缺少下班打卡"}
            )
        ]
        AttendanceRecordService._append_status_anomalies(record, runtime, non_status_parts)
        shortage = AttendanceRecordService._free_hours_shortage_issue(record, runtime)
        if shortage:
            non_status_parts.append(shortage)
        unique = list(dict.fromkeys(part for part in non_status_parts if part))
        return "; ".join(unique) if unique else None

    @staticmethod
    def _free_required_hours(rule: Optional[AttendanceRule], extra: Optional[Dict[str, Any]] = None) -> Optional[Decimal]:
        if rule is None:
            return None
        extra_cfg = extra if extra is not None else AttendanceRecordService._extra(rule)
        if AttendanceRecordService._infer_rule_type(rule, extra_cfg) != "free":
            return None
        mode = str(extra_cfg.get("free_work_hours_mode") or "unlimited")
        if not mode.startswith("limit:"):
            return None
        try:
            required = Decimal(mode.split(":", 1)[1])
        except (ValueError, ArithmeticError):
            return None
        return required if required > 0 else None

    @staticmethod
    def _free_hours_shortage_issue(record: AttendanceRecord, runtime: Optional[Dict[str, Any]]) -> Optional[str]:
        if not runtime or runtime.get("no_punch") or not (record.clock_in_time and record.clock_out_time):
            return None
        extra = runtime.get("extra") or {}
        if str(extra.get("rest_day_shift_mode") or "") == "record_only" and record.work_hours == 0:
            return None
        required = AttendanceRecordService._free_required_hours(runtime.get("rule"), extra)
        if required is None:
            return None
        actual = AttendanceRecordService._calculate_work_hours(
            record.clock_in_time,
            record.clock_out_time,
            target_date=record.date,
            rest_periods=runtime.get("rest_periods") or [],
            round_result=False,
        )
        if actual is None or actual >= required:
            return None
        shown = Decimal(str(record.work_hours if record.work_hours is not None else actual))
        return f"工时不足，实际{shown:.2f}小时，要求{required:g}小时"

    @staticmethod
    def _resolve_mobile_punch_phase(record: Optional[AttendanceRecord]) -> str:
        """
        移动端主按钮阶段只决定本次提交到哪个打卡入口，不表达“当天结束”。

        考勤业务允许员工随时再次打卡；已有上班卡后再次点击视为下班卡，
        后端按最新下班卡重算状态和异常。
        """
        if record is None or record.clock_in_time is None:
            return "clock_in"
        return "clock_out"

    @staticmethod
    async def get_mobile_runtime(
        db: AsyncSession,
        employee_id: int,
        target_date: date,
        inspect_date: bool = False,
    ) -> Dict[str, Any]:
        """
        移动端考勤运行态：解析命中规则、当天打卡阶段和按钮预判。

        该方法只读不写，供移动端页面定时刷新和打卡前展示使用。
        """
        from app.services.field_membership import managed_field_person, punch_context, segment_view, snapshot
        field_meta = {}
        context = None
        if await managed_field_person(db, employee_id):
            context = await punch_context(db, employee_id, datetime.combine(target_date, time(12), timezone(timedelta(hours=8))) if inspect_date else None)
            runtime = context["runtime"] if context else None
            target_date = context["day"] if context else target_date
            from app.models.field_service import FieldAttendanceSegment
            segments = (await db.scalars(select(FieldAttendanceSegment).where(FieldAttendanceSegment.employee_id == employee_id, FieldAttendanceSegment.work_date == target_date).order_by(FieldAttendanceSegment.id))).all()
            field_meta = {"field_managed": True, "project_context": context["revision"] if context else None,
                          "project": {"id": context["project"].id, "name": context["project"].name, "code": context["project"].code} if context else None,
                          "current_record": snapshot(context["record"]) if context and context["segment"] else None,
                          "project_segments": [segment_view(s) for s in segments]}
        else:
            runtime = await AttendanceRecordService._get_rule_runtime(db, employee_id, target_date)
        if runtime is None:
            return {
                **field_meta,
                "date": target_date.isoformat(),
                "has_rule": False,
                "rule": None,
                "expected": {},
                "window": {},
                "rest_periods": [],
            }

        rule = runtime["rule"]
        extra = runtime.get("extra") or AttendanceRecordService._extra(rule)
        segment = runtime.get("segment") if isinstance(runtime.get("segment"), dict) else {}
        expected_in = runtime.get("expected_in")
        expected_out = runtime.get("expected_out")
        today_record_result = await db.execute(
            select(AttendanceRecord).where(
                and_(
                    AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.date == target_date,
                )
            )
        )
        today_record = context["record"] if context else today_record_result.scalar_one_or_none()
        check_out_required = bool(runtime.get("check_out_required", True))
        punch_phase = AttendanceRecordService._resolve_mobile_punch_phase(today_record)

        raw_preview = AttendanceRecordService._preview_punch_status(
            rule,
            punch_phase,
            datetime.now(timezone.utc),
            target_date,
            expected_in=expected_in,
            expected_out=expected_out,
            check_in_required=bool(runtime.get("check_in_required", True)),
            check_out_required=check_out_required,
            flex_mode=str(runtime.get("flex_mode") or "none"),
            early_threshold_minutes=int(runtime.get("early_threshold_minutes") or 0),
            half_day_pm_start=runtime.get("half_day_pm_start"),
            half_day_absence_cutoff=runtime.get("half_day_absence_cutoff"),
        )
        preview_status = raw_preview["status"]
        preview = {
            "status": getattr(preview_status, "value", preview_status),
            "status_text": AttendanceRecordService._status_label(preview_status),
            "minutes": int(raw_preview.get("minutes") or 0),
            "phase": punch_phase,
        }

        return {
            **field_meta,
            "date": target_date.isoformat(),
            "has_rule": True,
            "rule": {
                "id": rule.id,
                "name": rule.name,
                "work_hour_type": str(rule.work_hour_type.value if getattr(rule.work_hour_type, "value", None) else rule.work_hour_type),
                "rule_type": str(runtime.get("rule_type") or ""),
                "require_gps": bool(getattr(rule, "require_gps", False)),
                "require_wifi": bool(getattr(rule, "require_wifi", False)),
                "require_photo": AttendanceRecordService._requires_photo_each_punch(rule),
                "watermark_photo_required": bool(extra.get("watermark_photo_required", False)),
                "check_method_mode": str(extra.get("check_method_mode") or "any"),
                "check_methods": list(AttendanceRecordService._parse_text_set(extra.get("check_methods"))),
                "flexible_minutes": int(getattr(rule, "flexible_minutes", 0) or 0),
                "priority": int(getattr(rule, "priority", 100) or 100),
                "effective_date": rule.effective_date.isoformat() if getattr(rule, "effective_date", None) else None,
                "gps_latitude": float(rule.gps_latitude) if rule.gps_latitude is not None else None,
                "gps_longitude": float(rule.gps_longitude) if rule.gps_longitude is not None else None,
                "gps_radius_meters": int(rule.gps_radius_meters) if rule.gps_radius_meters is not None else None,
                "locations": AttendanceRecordService._configured_locations(rule, extra),
                "wifi_names": sorted(AttendanceRecordService._configured_wifi_names(rule, extra)),
                "wifi_ssid": str(getattr(rule, "wifi_ssid", "") or "") or None,
                "no_punch": bool(runtime.get("no_punch")),
                "report_target_ids": list(runtime.get("report_target_ids") or []),
                "assist_manager_ids": list(runtime.get("assist_manager_ids") or []),
            },
            "expected": {
                "clock_in": expected_in.strftime("%H:%M") if expected_in else None,
                "clock_out": expected_out.strftime("%H:%M") if expected_out else None,
            },
            "window": {
                "punch_start": segment.get("punch_start") if segment else None,
                "punch_end": segment.get("punch_end") if segment else None,
            },
            "check": {
                "check_in_required": bool(runtime.get("check_in_required", True)),
                "check_out_required": bool(runtime.get("check_out_required", True)),
                "flex_mode": str(runtime.get("flex_mode") or "none"),
                "early_threshold_minutes": int(runtime.get("early_threshold_minutes") or 0),
                "no_punch": bool(runtime.get("no_punch")),
                "half_day_pm_start": runtime.get("half_day_pm_start").strftime("%H:%M") if runtime.get("half_day_pm_start") else None,
                "half_day_absence_cutoff": runtime.get("half_day_absence_cutoff").strftime("%H:%M") if runtime.get("half_day_absence_cutoff") else None,
            },
            "punch_preview": preview,
            "rest_periods": runtime.get("rest_periods") or [],
        }

    @staticmethod
    async def validate_clock(
        db: AsyncSession,
        employee_id: int,
        data: ClockValidateRequest,
    ) -> Dict[str, Any]:
        """
        打卡前校验接口的服务层实现。

        对齐产品文档中的 validateClockMethod / 预判结果要求：解析员工命中的规则，
        校验定位/Wi-Fi/拍照/模拟定位和可打卡时间窗，并给出当前按钮的迟到/早退预判。
        本方法只读不写，真正写入仍由 clock_in/clock_out 统一完成。
        """
        punch_time = data.punch_time or datetime.now(timezone.utc)
        if punch_time.tzinfo is None:
            punch_time = punch_time.replace(tzinfo=timezone.utc)
        punch_at_cst = AttendanceRecordService._to_cst_datetime(punch_time) or punch_time

        if data.target_date:
            target_date = data.target_date
            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                employee_id,
                target_date,
                punch_at_cst=punch_at_cst,
            )
        else:
            target_date, runtime = await AttendanceRecordService._resolve_punch_target_date(
                db,
                employee_id,
                punch_at_cst,
            )

        if runtime is None:
            return {
                "date": target_date.isoformat(),
                "has_rule": False,
                "phase": data.phase or "clock_in",
                "status": "normal",
                "status_text": "正常",
                "minutes": 0,
                "is_valid": True,
                "method_valid": True,
                "window_valid": True,
                "anomalies": [],
            }

        rule = runtime["rule"]
        record_result = await db.execute(
            select(AttendanceRecord).where(
                and_(
                    AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.date == target_date,
                )
            )
        )
        record = record_result.scalar_one_or_none()
        check_out_required = bool(runtime.get("check_out_required", True))
        if data.phase:
            phase = data.phase
        else:
            phase = AttendanceRecordService._resolve_mobile_punch_phase(record)

        method_anomalies = AttendanceRecordService._validate_clock(
            rule,
            data.gps_latitude,
            data.gps_longitude,
            data.wifi_ssid,
            data.photo_url,
            data.is_mock_location,
        )
        window_anomalies: List[str] = []
        win_start, win_end = AttendanceRecordService._window_from_runtime(runtime or {})
        if not AttendanceRecordService._within_punch_window(punch_at_cst, win_start, win_end):
            window_anomalies.append("不在可打卡时段")

        preview = AttendanceRecordService._preview_punch_status(
            rule,
            phase,
            punch_time,
            target_date,
            expected_in=runtime.get("expected_in"),
            expected_out=runtime.get("expected_out"),
            check_in_required=bool(runtime.get("check_in_required", True)),
            check_out_required=check_out_required,
            flex_mode=str(runtime.get("flex_mode") or "none"),
            early_threshold_minutes=int(runtime.get("early_threshold_minutes") or 0),
            half_day_pm_start=runtime.get("half_day_pm_start"),
            half_day_absence_cutoff=runtime.get("half_day_absence_cutoff"),
        )

        status_value = getattr(preview.get("status"), "value", preview.get("status"))
        anomalies = method_anomalies + window_anomalies
        return {
            "date": target_date.isoformat(),
            "has_rule": True,
            "rule_id": getattr(rule, "id", None),
            "rule_name": getattr(rule, "name", None),
            "phase": phase,
            "status": status_value,
            "status_text": AttendanceRecordService._status_label(status_value),
            "minutes": int(preview.get("minutes") or 0),
            "is_valid": not anomalies,
            "method_valid": not method_anomalies,
            "window_valid": not window_anomalies,
            "anomalies": anomalies,
            "expected": {
                "clock_in": runtime.get("expected_in").strftime("%H:%M") if runtime.get("expected_in") else None,
                "clock_out": runtime.get("expected_out").strftime("%H:%M") if runtime.get("expected_out") else None,
            },
        }

    @staticmethod
    async def _recalculate_record_after_punch(
        db: AsyncSession,
        record: AttendanceRecord,
        employee_id: int,
        target_date: date,
        runtime: Optional[Dict[str, Any]],
        anomalies: Optional[List[str]] = None,
    ) -> None:
        if (runtime or {}).get("project_segments") and not (runtime or {}).get("field_segment"):
            record.project_segments = runtime["project_segments"]
            issues = []
            for segment in record.project_segments:
                required = Decimal(str(segment.get("required_work_hours") or 0))
                actual = Decimal(str(segment.get("work_hours") or 0))
                if segment.get("clock_in_time") and segment.get("clock_out_time") and required > 0 and actual < required:
                    issues.append(f"{segment.get('project_name') or '项目'}：工时不足，实际{actual:.2f}小时，要求{required:g}小时")
            previous = [part.strip() for part in str(record.anomaly_type or "").split(";") if part.strip() and "工时不足" not in part]
            record.anomaly_type = "; ".join(dict.fromkeys([*previous, *issues])) or None
            return
        rule = runtime["rule"] if runtime else None
        if rule is None:
            return

        if bool((runtime or {}).get("no_punch")):
            if anomalies is not None:
                anomalies.clear()
            record.status = AttendanceStatus.normal
            record.overtime_hours = Decimal("0.00")
            record.anomaly_type = None
            return

        if (runtime or {}).get("field_segment") and record.clock_in_time and record.clock_out_time:
            from app.services.field_membership import utc
            if utc(record.clock_out_time) < utc(record.clock_in_time):
                record.work_hours = None
                record.overtime_hours = Decimal("0.00")
                record.status = AttendanceStatus.missed_clock
                if anomalies is not None:
                    anomalies.append("上下班顺序异常，待核对")
                return
        extra_cfg = (runtime or {}).get("extra") or {}
        is_free_record_only = (
            str((runtime or {}).get("rule_type") or "") == "free"
            and str(extra_cfg.get("rest_day_shift_mode") or "") == "record_only"
        )
        if is_free_record_only:
            record.work_hours = Decimal("0.00")
        else:
            record.work_hours = AttendanceRecordService._calculate_work_hours(
                record.clock_in_time,
                record.clock_out_time,
                target_date=target_date,
                rest_periods=(runtime or {}).get("rest_periods") or [],
            )

        if record.clock_in_time and record.clock_out_time:
            day_type = await WorkCalendarService.get_day_type(
                db,
                target_date,
                getattr((runtime or {}).get("employee"), "location_id", None),
            )
            approved_entries = await AttendanceRecordService._load_approved_overtime_entries(
                db,
                employee_id,
                target_date,
                day_type,
            )
            if day_type in (DayType.weekend, DayType.holiday) and not is_free_record_only:
                interval = int(extra_cfg.get("rest_day_shift_interval_minutes") or 0)
                if interval > 0:
                    in_cst = AttendanceRecordService._to_cst_datetime(record.clock_in_time)
                    out_cst = AttendanceRecordService._to_cst_datetime(record.clock_out_time)
                    if in_cst is not None and out_cst is not None and out_cst < in_cst:
                        out_cst += timedelta(days=1)
                    if in_cst is not None and out_cst is not None and (out_cst - in_cst) < timedelta(minutes=interval):
                        issue = f"休息日打卡间隔不足{interval}分钟"
                        if anomalies is not None and issue not in anomalies:
                            anomalies.append(issue)

            previous_overtime_hours = AttendanceRecordService._decimal_hours(getattr(record, "overtime_hours", None))
            new_overtime_hours = Decimal("0.00") if is_free_record_only else AttendanceRecordService._calculate_overtime(
                record.work_hours,
                rule,
                extra=extra_cfg,
                day_type=day_type,
                clock_in=record.clock_in_time,
                clock_out=record.clock_out_time,
                target_date=target_date,
                expected_in=(runtime or {}).get("expected_in"),
                expected_out=(runtime or {}).get("expected_out"),
                approved_entries=approved_entries,
                rest_periods=(runtime or {}).get("rest_periods") or [],
            )
            record.overtime_hours = new_overtime_hours
            await AttendanceRecordService._sync_comp_time_balance_delta(
                db,
                employee_id,
                target_date,
                extra_cfg,
                day_type,
                previous_overtime_hours,
                new_overtime_hours,
            )
            record.status = AttendanceRecordService._determine_status(
                rule,
                record.clock_in_time,
                record.clock_out_time,
                target_date,
                expected_in=(runtime or {}).get("expected_in"),
                expected_out=(runtime or {}).get("expected_out"),
                check_in_required=bool((runtime or {}).get("check_in_required", True)),
                check_out_required=bool((runtime or {}).get("check_out_required", True)),
                flex_mode=str((runtime or {}).get("flex_mode") or "none"),
                early_threshold_minutes=int((runtime or {}).get("early_threshold_minutes") or 0),
                half_day_pm_start=(runtime or {}).get("half_day_pm_start"),
                half_day_absence_cutoff=(runtime or {}).get("half_day_absence_cutoff"),
            )
            shortage = AttendanceRecordService._free_hours_shortage_issue(record, runtime)
            if shortage and anomalies is not None and shortage not in anomalies:
                anomalies.append(shortage)
            AttendanceRecordService._append_status_anomalies(record, runtime, anomalies)
            return

        record.overtime_hours = Decimal("0.00")
        record.status = AttendanceRecordService._determine_incomplete_record_status(
            rule,
            record,
            target_date,
            runtime,
        )
        AttendanceRecordService._append_status_anomalies(record, runtime, anomalies)

    @staticmethod
    def _calculate_work_hours(
        clock_in: Optional[datetime],
        clock_out: Optional[datetime],
        target_date: Optional[date] = None,
        rest_periods: Optional[List[Tuple[time, time]]] = None,
        round_result: bool = True,
    ) -> Optional[Decimal]:
        """
        计算实际工时（小时数），保留两位小数。

        计算公式：工时 = (下班时间 - 上班时间) 的总秒数 / 3600

        参数:
            clock_in: 上班打卡时间（UTC datetime）
            clock_out: 下班打卡时间（UTC datetime）

        返回:
            Decimal 类型的工时小时数（如 8.50）；任意一个时间为 None 则返回 None
        """
        if clock_in is None or clock_out is None:
            return None
        cst = AttendanceRecordService._cst()
        start = AttendanceRecordService._to_cst_datetime(clock_in)
        end = AttendanceRecordService._to_cst_datetime(clock_out)
        if start is None or end is None:
            return None
        if end < start:
            end += timedelta(days=1)
        total_seconds = max(0.0, (end - start).total_seconds())

        work_date = target_date or start.date()
        for rs, re in (rest_periods or []):
            rs_dt = datetime.combine(work_date, rs, tzinfo=cst)
            re_dt = datetime.combine(work_date, re, tzinfo=cst)
            if re_dt <= rs_dt:
                re_dt += timedelta(days=1)
            overlap_start = max(start, rs_dt)
            overlap_end = min(end, re_dt)
            if overlap_end > overlap_start:
                total_seconds -= (overlap_end - overlap_start).total_seconds()

        if total_seconds < 0:
            total_seconds = 0
        hours = Decimal(str(total_seconds)) / Decimal("3600")
        return hours.quantize(Decimal("0.01")) if round_result else hours

    @staticmethod
    def _calculate_overtime(
        work_hours: Optional[Decimal],
        rule: AttendanceRule,
        *,
        extra: Optional[Dict[str, Any]] = None,
        day_type: Optional[DayType] = None,
        clock_in: Optional[datetime] = None,
        clock_out: Optional[datetime] = None,
        target_date: Optional[date] = None,
        expected_in: Optional[time] = None,
        expected_out: Optional[time] = None,
        approved_entries: Optional[List[Dict[str, Any]]] = None,
        rest_periods: Optional[List[Tuple[time, time]]] = None,
    ) -> Decimal:
        """按规则策略计算加班时长（小时）。"""
        if work_hours is None:
            return Decimal("0")
        extra_cfg = extra or AttendanceRecordService._extra(rule)
        if AttendanceRecordService._infer_rule_type(rule, extra_cfg) == "free":
            if str(extra_cfg.get("free_work_hours_mode") or "unlimited") == "unlimited":
                return Decimal("0")
        if extra_cfg.get("enable_overtime") is False:
            return Decimal("0")
        policy = AttendanceRecordService._resolve_overtime_policy(extra_cfg, day_type)
        if policy.get("_new_policy_enabled") and not policy.get("enabled", True):
            return Decimal("0")
        method = str(policy.get("calc_method") or "by_approval")
        if (
            policy.get("_new_policy_enabled")
            and day_type in (DayType.workday, DayType.special_workday)
            and str(policy.get("period_mode") or "all") in {"all", "before_work", "after_work"}
            and (expected_in is None or expected_out is None)
            and not (approved_entries or [])
        ):
            return Decimal("0")

        # 兼容老数据：没有新策略结构时，继续使用“标准工时 + 阈值”方案
        if not policy.get("_new_policy_enabled"):
            standard = rule.work_hours_per_day
            if target_date is not None and expected_in is not None and expected_out is not None:
                cst = AttendanceRecordService._cst()
                expected_start = datetime.combine(target_date, expected_in, tzinfo=cst)
                expected_end = datetime.combine(target_date, expected_out, tzinfo=cst)
                if expected_end <= expected_start:
                    expected_end += timedelta(days=1)
                standard = AttendanceRecordService._calculate_work_hours(
                    expected_start,
                    expected_end,
                    target_date=target_date,
                    rest_periods=rest_periods or [],
                ) or standard
            threshold_minutes = int(extra_cfg.get("overtime_threshold_minutes") or 0)
            threshold = Decimal(str(threshold_minutes)) / Decimal("60")
            if work_hours > standard + threshold:
                return (work_hours - standard - threshold).quantize(Decimal("0.01"))
            return Decimal("0")

        minutes = Decimal("0")
        entries = approved_entries or []
        interval_policy = policy
        if method in {"by_approval", "by_approval_clock"} and not entries:
            interval_policy = dict(policy)
            interval_policy["calc_method"] = "by_clock"
        intervals = AttendanceRecordService._build_overtime_intervals(
            clock_in=clock_in,
            clock_out=clock_out,
            target_date=target_date,
            day_type=day_type,
            policy=interval_policy,
            expected_in=expected_in,
            expected_out=expected_out,
        )

        settlement_intervals: List[Tuple[datetime, datetime]] = intervals
        effective_method = method
        if method == "by_clock":
            minutes = AttendanceRecordService._sum_interval_minutes(intervals)
        else:
            if method == "by_approval":
                if entries:
                    minutes = AttendanceRecordService._sum_approved_minutes(entries)
                    settlement_intervals = AttendanceRecordService._approval_entry_intervals(entries)
                else:
                    # 没有关联加班审批时，自行打卡按规则时段自动核算加班。
                    minutes = AttendanceRecordService._sum_interval_minutes(intervals)
                    effective_method = "by_clock"
            elif method == "by_approval_clock":
                if entries and intervals:
                    scoped = AttendanceRecordService._intersect_approval_with_intervals(entries, intervals)
                    minutes = AttendanceRecordService._sum_interval_minutes(scoped)
                    settlement_intervals = scoped
                elif not entries:
                    minutes = AttendanceRecordService._sum_interval_minutes(intervals)
                    settlement_intervals = intervals
                    effective_method = "by_clock"
                else:
                    minutes = Decimal("0")
                    settlement_intervals = []
            else:
                minutes = AttendanceRecordService._sum_interval_minutes(intervals)
                effective_method = "by_clock"

        if policy.get("allow_rest_deduction"):
            rest_deduct = AttendanceRecordService._calc_overtime_rest_deduction_minutes(
                intervals=settlement_intervals if settlement_intervals else [],
                minutes=minutes,
                target_date=target_date,
                policy=policy,
            )
            minutes = max(Decimal("0"), minutes - rest_deduct)

        if effective_method == "by_clock":
            min_minutes = int(policy.get("min_minutes") or 0)
            max_minutes = int(policy.get("max_minutes") or 0)
            if min_minutes > 0 and minutes < Decimal(str(min_minutes)):
                minutes = Decimal("0")
            if max_minutes > 0 and minutes > Decimal(str(max_minutes)):
                minutes = Decimal(str(max_minutes))

        hours = (minutes / Decimal("60")) if minutes > 0 else Decimal("0")
        return AttendanceRecordService._round_overtime_hours(hours, extra_cfg)

    @staticmethod
    def _round_overtime_hours(hours: Decimal, extra_cfg: Dict[str, Any]) -> Decimal:
        duration = extra_cfg.get("overtime_duration") if isinstance(extra_cfg.get("overtime_duration"), dict) else {}
        mode = str(duration.get("rounding_mode") or "round")
        places = int(duration.get("decimal_places") if duration.get("decimal_places") is not None else 2)
        places = min(max(places, 0), 3)
        q = Decimal("1").scaleb(-places)
        rounding = ROUND_HALF_UP
        if mode == "ceil":
            rounding = ROUND_CEILING
        elif mode == "floor":
            rounding = ROUND_FLOOR
        return max(Decimal("0"), hours.quantize(q, rounding=rounding))

    @staticmethod
    def _resolve_overtime_policy(extra_cfg: Dict[str, Any], day_type: Optional[DayType]) -> Dict[str, Any]:
        policy_map = extra_cfg.get("overtime_policy")
        is_new = isinstance(policy_map, dict)
        day_key = "workday"
        if day_type == DayType.holiday:
            day_key = "holiday"
        elif day_type == DayType.weekend:
            day_key = "restday"
        selected = policy_map.get(day_key) if isinstance(policy_map, dict) else {}
        if not isinstance(selected, dict):
            selected = {}
        try:
            comp_time_ratio = Decimal(str(selected.get("comp_time_ratio") or 1))
        except Exception:
            comp_time_ratio = Decimal("1")
        out = {
            "enabled": bool(selected.get("enabled", True)),
            "period_mode": str(selected.get("period_mode") or "all"),
            "calc_method": str(selected.get("calc_method") or "by_approval"),
            "start_after_off_duty_minutes": int(selected.get("start_after_off_duty_minutes") or 0),
            "allow_rest_deduction": bool(selected.get("allow_rest_deduction", True)),
            "rest_deduction_mode": str(selected.get("rest_deduction_mode") or "period"),
            "rest_periods": selected.get("rest_periods") if isinstance(selected.get("rest_periods"), list) else [],
            "deduct_every_minutes": int(selected.get("deduct_every_minutes") or 0),
            "deduct_minutes": int(selected.get("deduct_minutes") or 0),
            "min_minutes": int(selected.get("min_minutes") or 0),
            "max_minutes": int(selected.get("max_minutes") or 0),
            "allow_convert": bool(selected.get("allow_convert", True)),
            "convert_mode": str(selected.get("convert_mode") or "comp_time"),
            "comp_time_ratio": comp_time_ratio,
            "sync_auto_leave_type": bool(selected.get("sync_auto_leave_type", True)),
            "_new_policy_enabled": is_new,
            "_day_key": day_key,
        }
        # 休息日/节假日不展示“加班时段”，后端统一按全天处理
        if day_key in ("restday", "holiday"):
            out["period_mode"] = "all"
            out["start_after_off_duty_minutes"] = 0
        return out

    @staticmethod
    def _is_overtime_payroll_eligible(extra_cfg: Dict[str, Any], day_type: Optional[DayType]) -> bool:
        _, pay_hours = AttendanceRecordService._split_overtime_settlement(
            extra_cfg,
            day_type,
            Decimal("1"),
        )
        return pay_hours > 0

    @staticmethod
    def _split_overtime_settlement(
        extra_cfg: Dict[str, Any],
        day_type: Optional[DayType],
        overtime_hours: Decimal | float | int,
    ) -> Tuple[Decimal, Decimal]:
        """Return (comp_time_hours, payroll_hours) for already calculated overtime."""
        try:
            hours = Decimal(str(overtime_hours or 0))
        except Exception:
            hours = Decimal("0")
        if hours <= 0:
            return Decimal("0"), Decimal("0")

        policy = AttendanceRecordService._resolve_overtime_policy(extra_cfg, day_type)
        if not policy.get("_new_policy_enabled"):
            return Decimal("0"), hours
        if not policy.get("enabled", True) or not policy.get("allow_convert", True):
            return Decimal("0"), Decimal("0")

        if str(policy.get("convert_mode") or "comp_time") == "comp_time":
            try:
                ratio = Decimal(str(policy.get("comp_time_ratio") or 1))
            except Exception:
                ratio = Decimal("1")
            if ratio <= 0:
                ratio = Decimal("1")
            return (hours * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), Decimal("0")
        return Decimal("0"), hours

    @staticmethod
    def _decimal_hours(value: Any) -> Decimal:
        try:
            return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            return Decimal("0.00")

    @staticmethod
    async def _get_comp_time_leave_type(db: AsyncSession) -> Optional[LeaveType]:
        for condition in (
            LeaveType.code == "comp_time",
            LeaveType.code == "comp",
            LeaveType.name == "调休",
        ):
            result = await db.execute(
                select(LeaveType)
                .where(and_(condition, LeaveType.is_active.is_(True)))
                .order_by(LeaveType.id.asc())
            )
            leave_type = result.scalars().first()
            if leave_type is not None:
                return leave_type
        return None

    @staticmethod
    def _comp_hours_to_balance_days(leave_type: LeaveType, comp_hours: Decimal | float | int) -> Decimal:
        """把调休小时换算成 LeaveBalance 的天数字段值。"""
        try:
            hours = Decimal(str(comp_hours or 0))
        except Exception:
            hours = Decimal("0")
        if hours <= 0:
            return Decimal("0.0")

        if str(getattr(leave_type, "leave_unit", "day") or "day") == "hour":
            try:
                hours_per_day = Decimal(str(getattr(leave_type, "hours_per_day", None) or 8))
            except Exception:
                hours_per_day = Decimal("8")
            if hours_per_day <= 0:
                hours_per_day = Decimal("8")
            return (hours / hours_per_day).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        return hours.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

    @staticmethod
    async def _sync_comp_time_balance_delta(
        db: AsyncSession,
        employee_id: int,
        target_date: date,
        extra_cfg: Dict[str, Any],
        day_type: Optional[DayType],
        previous_overtime_hours: Decimal | float | int,
        current_overtime_hours: Decimal | float | int,
    ) -> None:
        delta = AttendanceRecordService._comp_time_balance_delta(
            extra_cfg,
            day_type,
            previous_overtime_hours,
            current_overtime_hours,
        )
        if delta == 0:
            return

        leave_type = await AttendanceRecordService._get_comp_time_leave_type(db)
        if leave_type is None:
            logger.warning("未找到调休假期类型，员工 %s 的加班调休未同步", employee_id)
            return
        delta_days = AttendanceRecordService._comp_hours_to_balance_days(leave_type, delta)
        if delta_days == 0:
            return

        result = await db.execute(
            select(LeaveBalance).where(
                and_(
                    LeaveBalance.employee_id == employee_id,
                    LeaveBalance.leave_type_id == leave_type.id,
                    LeaveBalance.year == target_date.year,
                )
            )
        )
        balance = result.scalar_one_or_none()
        if balance is None:
            from app.services.leave import LeaveBalanceService

            balance = LeaveBalance(
                employee_id=employee_id,
                leave_type_id=leave_type.id,
                year=target_date.year,
                total_days=Decimal("0.0"),
                used_days=Decimal("0.0"),
                remaining_days=Decimal("0.0"),
                expired_days=Decimal("0.0"),
                expiry_date=LeaveBalanceService.calculate_expiry_date(leave_type, target_date.year),
            )
            db.add(balance)

        total = (Decimal(str(balance.total_days or 0)) + delta_days).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if total < 0:
            total = Decimal("0.0")
        used = Decimal(str(balance.used_days or 0))
        expired = Decimal(str(balance.expired_days or 0))
        remaining = (total - used - expired).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if remaining < 0:
            remaining = Decimal("0.0")

        balance.total_days = total
        balance.remaining_days = remaining
        await db.flush()

    @staticmethod
    async def sync_approved_overtime_to_attendance(
        db: AsyncSession,
        overtime_request: OvertimeRequest,
    ) -> Optional[AttendanceRecord]:
        """
        加班审批通过后刷新当天考勤记录，确保“按审批时长计算”的加班可同步到调休余额。
        """
        if getattr(overtime_request, "status", None) != "approved":
            return None

        employee_id = int(getattr(overtime_request, "employee_id", 0) or 0)
        target_date = getattr(overtime_request, "overtime_date", None)
        if not employee_id or target_date is None:
            return None

        runtime = await AttendanceRecordService._get_rule_runtime(db, employee_id, target_date)
        if runtime is None:
            return None

        result = await db.execute(
            select(AttendanceRecord).where(
                and_(
                    AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.date == target_date,
                )
            )
        )
        record = result.scalar_one_or_none()
        if record is None or not (record.clock_in_time and record.clock_out_time):
            return None

        await AttendanceRecordService._recalculate_record_after_punch(
            db,
            record,
            employee_id,
            target_date,
            runtime,
        )
        record.anomaly_type = AttendanceRecordService._rebuild_anomaly_text_with_status(record, runtime)
        await db.flush()
        await AttendanceRecordService._refresh_month_summary_for_date(db, employee_id, target_date)
        return record

    @staticmethod
    async def sync_comp_time_balances_from_attendance(
        db: AsyncSession,
        year: int,
        employee_ids: Optional[List[int]] = None,
        *,
        allow_decrease: bool = False,
        recalculate_records: bool = False,
    ) -> int:
        """
        从当前规则可追溯的加班来源同步调休余额。

        默认只补足低于自动计算值的余额，避免覆盖 HR 手动导入或修正过的余额。
        移动端个人视图传入 allow_decrease 时，会在没有手工调整审计的前提下按
        当前规则下调旧的自动余额，防止补班工作日等规则变化后继续残留错误调休。
        """
        leave_type = await AttendanceRecordService._get_comp_time_leave_type(db)
        if leave_type is None:
            return 0

        start = date(year, 1, 1)
        end = date(year, 12, 31)
        target_emp_ids = {int(emp_id) for emp_id in (employee_ids or []) if emp_id is not None}

        stmt = select(AttendanceRecord).where(
            and_(
                AttendanceRecord.date >= start,
                AttendanceRecord.date <= end,
                AttendanceRecord.overtime_hours > 0,
            )
        )
        if target_emp_ids:
            stmt = stmt.where(AttendanceRecord.employee_id.in_(list(target_emp_ids)))
        result = await db.execute(stmt)
        records = list(result.scalars().all())

        effect_stmt = select(AttendanceEffect).where(
            and_(
                AttendanceEffect.work_date >= start,
                AttendanceEffect.work_date <= end,
                AttendanceEffect.effect_type == "overtime",
                AttendanceEffect.effect_status == "active",
            )
        )
        if target_emp_ids:
            effect_stmt = effect_stmt.where(AttendanceEffect.employee_id.in_(list(target_emp_ids)))
        effect_result = await db.execute(effect_stmt)
        effects = list(effect_result.scalars().all())

        request_stmt = select(OvertimeRequest).where(
            and_(
                OvertimeRequest.overtime_date >= start,
                OvertimeRequest.overtime_date <= end,
                OvertimeRequest.status == "approved",
            )
        )
        if target_emp_ids:
            request_stmt = request_stmt.where(OvertimeRequest.employee_id.in_(list(target_emp_ids)))
        request_result = await db.execute(request_stmt)
        overtime_requests = list(request_result.scalars().all())

        emp_ids = sorted(
            target_emp_ids
            | {int(record.employee_id) for record in records}
            | {int(effect.employee_id) for effect in effects}
            | {int(request.employee_id) for request in overtime_requests}
        )
        if not emp_ids:
            return 0
        emp_result = await db.execute(select(Employee).where(Employee.id.in_(emp_ids)))
        employees = {int(emp.id): emp for emp in emp_result.scalars().all()}

        runtime_cache: Dict[Tuple[int, date], Optional[Dict[str, Any]]] = {}
        day_type_cache: Dict[Tuple[Optional[int], date], DayType] = {}

        async def _runtime_for(emp_id: int, work_day: date) -> Optional[Dict[str, Any]]:
            key = (emp_id, work_day)
            if key in runtime_cache:
                return runtime_cache[key]
            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                emp_id,
                work_day,
                employee=employees.get(emp_id),
            )
            runtime_cache[key] = runtime
            return runtime

        async def _day_type_for(emp_id: int, work_day: date) -> DayType:
            employee = employees.get(emp_id)
            location_id = getattr(employee, "location_id", None) if employee else None
            key = (location_id, work_day)
            if key in day_type_cache:
                return day_type_cache[key]
            day_type = await WorkCalendarService.get_day_type(db, work_day, location_id)
            day_type_cache[key] = day_type
            return day_type

        async def _comp_hours_for_source(
            emp_id: int,
            work_day: date,
            hours: Decimal | float | int,
            pay_policy: Optional[str] = None,
        ) -> Decimal:
            policy = str(pay_policy or "").strip()
            try:
                raw_hours = Decimal(str(hours or 0))
            except Exception:
                raw_hours = Decimal("0")
            if raw_hours <= 0:
                return Decimal("0")
            if policy in {"comp_time", "rest", "rest_time", "time_off", "lieu", "adjust_rest"}:
                return raw_hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if policy in {"overtime_pay", "payroll", "paid_overtime"}:
                return Decimal("0")

            runtime = await _runtime_for(emp_id, work_day)
            extra_cfg = (runtime or {}).get("extra") or {}
            day_type = await _day_type_for(emp_id, work_day)
            comp_hours, _ = AttendanceRecordService._split_overtime_settlement(
                extra_cfg,
                day_type,
                raw_hours,
            )
            return comp_hours

        totals: Dict[int, Decimal] = {emp_id: Decimal("0") for emp_id in emp_ids if allow_decrease and emp_id in target_emp_ids}
        covered_record_dates: set[Tuple[int, date]] = set()

        for effect in effects:
            effect_date = getattr(effect, "work_date", None)
            if effect_date is None:
                continue
            emp_id = int(effect.employee_id)
            raw_hours = Decimal(str(effect.minutes or 0)) / Decimal("60")
            comp_hours = await _comp_hours_for_source(
                emp_id,
                effect_date,
                raw_hours,
                getattr(effect, "pay_policy", None),
            )
            if comp_hours <= 0:
                continue
            totals[emp_id] = totals.get(emp_id, Decimal("0")) + comp_hours
            covered_record_dates.add((emp_id, effect_date))

        for request in overtime_requests:
            request_date = getattr(request, "overtime_date", None)
            if request_date is None:
                continue
            emp_id = int(request.employee_id)
            comp_hours = await _comp_hours_for_source(
                emp_id,
                request_date,
                getattr(request, "hours", 0),
            )
            if comp_hours <= 0:
                continue
            totals[emp_id] = totals.get(emp_id, Decimal("0")) + comp_hours
            covered_record_dates.add((emp_id, request_date))

        for record in records:
            emp_id = int(record.employee_id)
            if (emp_id, record.date) in covered_record_dates:
                continue
            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                emp_id,
                record.date,
                employee=employees.get(emp_id),
            )
            extra_cfg = (runtime or {}).get("extra") or {}
            day_type = await _day_type_for(emp_id, record.date)
            raw_overtime = Decimal(str(record.overtime_hours or 0))
            if recalculate_records and runtime is not None:
                approved_entries = await AttendanceRecordService._load_approved_overtime_entries(
                    db,
                    emp_id,
                    record.date,
                    day_type,
                )
                recalculated = AttendanceRecordService._calculate_overtime(
                    record.work_hours,
                    runtime.get("rule"),
                    extra=extra_cfg,
                    day_type=day_type,
                    clock_in=record.clock_in_time,
                    clock_out=record.clock_out_time,
                    target_date=record.date,
                    expected_in=runtime.get("expected_in"),
                    expected_out=runtime.get("expected_out"),
                    approved_entries=approved_entries,
                    rest_periods=runtime.get("rest_periods") or [],
                )
                if recalculated != raw_overtime:
                    record.overtime_hours = recalculated
                    raw_overtime = recalculated
            comp_hours, _ = AttendanceRecordService._split_overtime_settlement(extra_cfg, day_type, raw_overtime)
            if comp_hours <= 0:
                continue
            totals[emp_id] = totals.get(emp_id, Decimal("0")) + comp_hours

        if not totals:
            return 0

        balance_result = await db.execute(
            select(LeaveBalance).where(
                and_(
                    LeaveBalance.employee_id.in_(list(totals.keys())),
                    LeaveBalance.leave_type_id == leave_type.id,
                    LeaveBalance.year == year,
                )
            )
        )
        balance_map = {int(balance.employee_id): balance for balance in balance_result.scalars().all()}

        async def _has_manual_adjust_log(balance: LeaveBalance) -> bool:
            try:
                audit_result = await db.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(
                        and_(
                            AuditLog.resource_type == "LeaveBalance",
                            AuditLog.resource_id == balance.id,
                            AuditLog.module == "leave",
                            AuditLog.action.in_(["balance_adjust", "adjust", "UPDATE"]),
                        )
                    )
                )
                return int(audit_result.scalar() or 0) > 0
            except Exception:
                return False

        changed = 0
        for emp_id, total_comp in totals.items():
            auto_total = AttendanceRecordService._comp_hours_to_balance_days(leave_type, total_comp)
            balance = balance_map.get(emp_id)
            if balance is None:
                if auto_total <= 0:
                    continue
                from app.services.leave import LeaveBalanceService

                balance = LeaveBalance(
                    employee_id=emp_id,
                    leave_type_id=leave_type.id,
                    year=year,
                    total_days=auto_total,
                    used_days=Decimal("0.0"),
                    remaining_days=auto_total,
                    expired_days=Decimal("0.0"),
                    expiry_date=LeaveBalanceService.calculate_expiry_date(leave_type, year),
                )
                db.add(balance)
                changed += 1
                continue

            current_total = Decimal(str(balance.total_days or 0))
            if current_total == auto_total:
                continue
            if current_total > auto_total:
                if not allow_decrease or await _has_manual_adjust_log(balance):
                    continue
            balance.total_days = auto_total
            used = Decimal(str(balance.used_days or 0))
            expired = Decimal(str(balance.expired_days or 0))
            remaining = (auto_total - used - expired).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            balance.remaining_days = max(remaining, Decimal("0.0"))
            changed += 1

        if changed:
            await db.flush()
        return changed

    @staticmethod
    def _comp_time_balance_delta(
        extra_cfg: Dict[str, Any],
        day_type: Optional[DayType],
        previous_overtime_hours: Decimal | float | int,
        current_overtime_hours: Decimal | float | int,
    ) -> Decimal:
        policy = AttendanceRecordService._resolve_overtime_policy(extra_cfg, day_type)
        if not policy.get("_new_policy_enabled") or not policy.get("sync_auto_leave_type", True):
            return Decimal("0.0")

        previous_comp, _ = AttendanceRecordService._split_overtime_settlement(
            extra_cfg,
            day_type,
            previous_overtime_hours,
        )
        current_comp, _ = AttendanceRecordService._split_overtime_settlement(
            extra_cfg,
            day_type,
            current_overtime_hours,
        )
        return (current_comp - previous_comp).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _build_overtime_intervals(
        *,
        clock_in: Optional[datetime],
        clock_out: Optional[datetime],
        target_date: Optional[date],
        day_type: Optional[DayType],
        policy: Dict[str, Any],
        expected_in: Optional[time],
        expected_out: Optional[time],
    ) -> List[Tuple[datetime, datetime]]:
        if clock_in is None or clock_out is None or target_date is None:
            return []
        if not policy.get("enabled", True):
            return []
        cst = AttendanceRecordService._cst()
        start = AttendanceRecordService._to_cst_datetime(clock_in)
        end = AttendanceRecordService._to_cst_datetime(clock_out)
        if start is None or end is None:
            return []
        if end <= start:
            end += timedelta(days=1)
        intervals: List[Tuple[datetime, datetime]] = [(start, end)]
        period_mode = str(policy.get("period_mode") or "all")
        method = str(policy.get("calc_method") or "by_approval")

        if day_type in (DayType.weekend, DayType.holiday):
            period_mode = "all"

        expected_in_dt = datetime.combine(target_date, expected_in, tzinfo=cst) if expected_in is not None else None
        expected_out_dt = datetime.combine(target_date, expected_out, tzinfo=cst) if expected_out is not None else None
        if expected_in_dt is not None and expected_out_dt is not None and expected_out_dt <= expected_in_dt:
            expected_out_dt += timedelta(days=1)

        if period_mode == "before_work" and expected_in_dt is not None:
            intervals = AttendanceRecordService._clip_intervals(intervals, None, expected_in_dt)
        elif period_mode == "after_work" and expected_out_dt is not None:
            cutoff = expected_out_dt
            start_after = int(policy.get("start_after_off_duty_minutes") or 0)
            if method == "by_clock":
                cutoff += timedelta(minutes=max(0, start_after))
            intervals = AttendanceRecordService._clip_intervals(intervals, cutoff, None)
        elif period_mode == "all" and day_type not in (DayType.weekend, DayType.holiday):
            scoped: List[Tuple[datetime, datetime]] = []
            if expected_in_dt is not None:
                scoped.extend(AttendanceRecordService._clip_intervals(intervals, None, expected_in_dt))
            if expected_out_dt is not None:
                after_work_cutoff = expected_out_dt
                if method == "by_clock":
                    start_after = int(policy.get("start_after_off_duty_minutes") or 0)
                    after_work_cutoff += timedelta(minutes=max(0, start_after))
                scoped.extend(AttendanceRecordService._clip_intervals(intervals, after_work_cutoff, None))
            if expected_in_dt is not None or expected_out_dt is not None:
                intervals = scoped

        return intervals

    @staticmethod
    def _clip_intervals(
        intervals: List[Tuple[datetime, datetime]],
        min_start: Optional[datetime],
        max_end: Optional[datetime],
    ) -> List[Tuple[datetime, datetime]]:
        out: List[Tuple[datetime, datetime]] = []
        for st, ed in intervals:
            ns = max(st, min_start) if min_start is not None else st
            ne = min(ed, max_end) if max_end is not None else ed
            if ne > ns:
                out.append((ns, ne))
        return out

    @staticmethod
    def _sum_interval_minutes(intervals: List[Tuple[datetime, datetime]]) -> Decimal:
        total = Decimal("0")
        for st, ed in intervals:
            total += Decimal(str((ed - st).total_seconds())) / Decimal("60")
        return max(Decimal("0"), total)

    @staticmethod
    def _sum_approved_minutes(entries: List[Dict[str, Any]]) -> Decimal:
        total = Decimal("0")
        for item in entries:
            h = item.get("hours")
            if h is None:
                continue
            try:
                total += Decimal(str(h)) * Decimal("60")
            except Exception:
                continue
        return max(Decimal("0"), total)

    @staticmethod
    def _intersect_approval_with_intervals(
        entries: List[Dict[str, Any]],
        intervals: List[Tuple[datetime, datetime]],
    ) -> List[Tuple[datetime, datetime]]:
        out: List[Tuple[datetime, datetime]] = []
        for item in entries:
            st = item.get("start_dt")
            ed = item.get("end_dt")
            if not isinstance(st, datetime) or not isinstance(ed, datetime):
                continue
            for base_st, base_ed in intervals:
                x_st = max(st, base_st)
                x_ed = min(ed, base_ed)
                if x_ed > x_st:
                    out.append((x_st, x_ed))
        return out

    @staticmethod
    def _approval_entry_intervals(entries: List[Dict[str, Any]]) -> List[Tuple[datetime, datetime]]:
        out: List[Tuple[datetime, datetime]] = []
        for item in entries:
            st = item.get("start_dt")
            ed = item.get("end_dt")
            if isinstance(st, datetime) and isinstance(ed, datetime) and ed > st:
                out.append((st, ed))
        return out

    @staticmethod
    def _calc_overtime_rest_deduction_minutes(
        *,
        intervals: List[Tuple[datetime, datetime]],
        minutes: Decimal,
        target_date: Optional[date],
        policy: Dict[str, Any],
    ) -> Decimal:
        if minutes <= 0:
            return Decimal("0")
        mode = str(policy.get("rest_deduction_mode") or "period")
        if mode == "duration":
            every = int(policy.get("deduct_every_minutes") or 0)
            deduct = int(policy.get("deduct_minutes") or 0)
            if every <= 0 or deduct <= 0:
                return Decimal("0")
            times = int((minutes / Decimal(str(every))).to_integral_value(rounding=ROUND_FLOOR))
            return Decimal(str(times * deduct))

        # period
        if target_date is None:
            return Decimal("0")
        cst = AttendanceRecordService._cst()
        rest_rows = policy.get("rest_periods") if isinstance(policy.get("rest_periods"), list) else []
        rest_intervals: List[Tuple[datetime, datetime]] = []
        for row in rest_rows:
            if not isinstance(row, dict):
                continue
            st = AttendanceRecordService._parse_hm(row.get("start"))
            ed = AttendanceRecordService._parse_hm(row.get("end"))
            if st is None or ed is None:
                continue
            st_dt = datetime.combine(target_date, st, tzinfo=cst)
            ed_dt = datetime.combine(target_date, ed, tzinfo=cst)
            if ed_dt <= st_dt:
                ed_dt += timedelta(days=1)
            rest_intervals.append((st_dt, ed_dt))

        deduct_minutes = Decimal("0")
        for st, ed in intervals:
            for rst, red in rest_intervals:
                x_st = max(st, rst)
                x_ed = min(ed, red)
                if x_ed > x_st:
                    deduct_minutes += Decimal(str((x_ed - x_st).total_seconds())) / Decimal("60")
        return max(Decimal("0"), deduct_minutes)

    @staticmethod
    async def _load_approved_overtime_entries(
        db: AsyncSession,
        employee_id: int,
        target_date: date,
        day_type: DayType,
    ) -> List[Dict[str, Any]]:
        overtime_type = "weekday"
        if day_type == DayType.weekend:
            overtime_type = "weekend"
        elif day_type == DayType.holiday:
            overtime_type = "holiday"

        result = await db.execute(
            select(OvertimeRequest).where(
                and_(
                    OvertimeRequest.employee_id == employee_id,
                    OvertimeRequest.overtime_date == target_date,
                    OvertimeRequest.status == "approved",
                    OvertimeRequest.overtime_type == overtime_type,
                )
            )
        )
        rows = result.scalars().all()
        cst = AttendanceRecordService._cst()
        out: List[Dict[str, Any]] = []
        for r in rows:
            st = AttendanceRecordService._parse_hm(getattr(r, "start_time", None))
            ed = AttendanceRecordService._parse_hm(getattr(r, "end_time", None))
            if st is None or ed is None:
                continue
            st_dt = datetime.combine(target_date, st, tzinfo=cst)
            ed_dt = datetime.combine(target_date, ed, tzinfo=cst)
            if ed_dt <= st_dt:
                ed_dt += timedelta(days=1)
            out.append(
                {
                    "start_dt": st_dt,
                    "end_dt": ed_dt,
                    "hours": Decimal(str(getattr(r, "hours", 0) or 0)),
                }
            )
        return out

    @staticmethod
    def _within_punch_window(local_dt: datetime, start_hm: Optional[str], end_hm: Optional[str]) -> bool:
        st = AttendanceRecordService._parse_hm(start_hm)
        ed = AttendanceRecordService._parse_hm(end_hm)
        if not st or not ed:
            return True
        # 打卡窗口按“分钟”口径判定，避免 10:30:59 被界面显示为 10:30、
        # 后端却因秒数大于 10:30:00 误判为超窗异常。
        t = local_dt.replace(second=0, microsecond=0).time()
        if ed >= st:
            return st <= t <= ed
        # 跨日窗口，如 04:00-次日03:59
        return t >= st or t <= ed

    @staticmethod
    def _window_from_runtime(runtime: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        segment = runtime.get("segment") or {}
        if isinstance(segment, dict):
            return str(segment.get("punch_start") or "") or None, str(segment.get("punch_end") or "") or None
        return None, None

    @staticmethod
    def _source_to_text(source: Any) -> str:
        source_value = str(getattr(source, "value", source) or "").lower()
        if source_value == ClockSource.gate.value:
            return "考勤机"
        if source_value == ClockSource.manual.value:
            return "管理员补录"
        return "APP"

    @staticmethod
    def _detect_os_from_user_agent(user_agent: Optional[str]) -> str:
        ua = str(user_agent or "").lower()
        if not ua:
            return "未知系统"
        if "harmony" in ua:
            return "HarmonyOS"
        if "android" in ua:
            return "Android"
        if "iphone" in ua or "ipad" in ua or "ipod" in ua or "ios" in ua:
            return "iOS"
        if "windows" in ua:
            return "Windows"
        if "mac os" in ua or "macintosh" in ua:
            return "macOS"
        if "linux" in ua:
            return "Linux"
        return "未知系统"

    @staticmethod
    def _build_device_text(
        data: ClockInRequest | ClockOutRequest,
        request_user_agent: Optional[str],
    ) -> str:
        source_text = AttendanceRecordService._source_to_text(getattr(data, "source", None))
        source_value = str(getattr(getattr(data, "source", None), "value", getattr(data, "source", "")) or "").lower()
        if source_value == ClockSource.gate.value:
            return "考勤机"
        if source_value == ClockSource.manual.value:
            return "管理员补录"

        os_text = str(getattr(data, "device_os", "") or "").strip() or AttendanceRecordService._detect_os_from_user_agent(request_user_agent)
        model_text = str(getattr(data, "device_model", "") or "").strip()
        name_text = str(getattr(data, "device_name", "") or "").strip()
        device_id = str(getattr(data, "device_id", "") or "").strip()

        base = name_text or model_text or f"移动设备({os_text})"
        if device_id:
            return f"{source_text}/{base}#{device_id[:12]}"
        return f"{source_text}/{base}"

    @staticmethod
    async def _append_punch_time_record(
        db: AsyncSession,
        *,
        employee_id: int,
        record: AttendanceRecord,
        work_date: date,
        punch_time: datetime,
        punch_type: str,
        data: ClockInRequest | ClockOutRequest,
        device_info: Optional[str] = None,
        remark: Optional[str] = None,
    ) -> AttendancePunchTimeRecord:
        """
        追加一条不可覆盖的原始打卡流水。

        AttendanceRecord 用于日报/月报最终统计，重复打卡会按规则取头/取尾；
        AttendancePunchTimeRecord 用于"打卡时间记录"，每一次提交都必须保留。
        """
        if record.id is None:
            await db.flush()
        source = getattr(data.source, "value", data.source) or ClockSource.app.value
        item = AttendancePunchTimeRecord(
            employee_id=employee_id,
            attendance_record_id=record.id,
            work_date=work_date,
            punch_time=punch_time,
            punch_type=punch_type,
            source=str(source),
            gps_lat=data.gps_latitude,
            gps_lng=data.gps_longitude,
            wifi_ssid=data.wifi_ssid,
            device_info=(str(device_info or "").strip() or None),
            photo_url=data.photo_url,
            is_mock_location=bool(data.is_mock_location),
            remark=remark,
        )
        db.add(item)
        return item

    # ------------------------------------------------------------------
    # 上班打卡
    # ------------------------------------------------------------------
    @staticmethod
    async def clock_in(
        db: AsyncSession,
        employee_id: int,
        data: ClockInRequest,
        request_user_agent: Optional[str] = None,
    ) -> Tuple[AttendanceRecord, List[str]]:
        """
        处理上班打卡，校验打卡合法性，创建或更新今日考勤记录。

        业务流程：
        1. 获取当前时间（UTC）和本地日期
        2. 查询员工当日适用的考勤规则
        3. 执行打卡校验（GPS/WiFi/照片/模拟定位），收集异常
        4. 查询今日是否已有考勤记录：
           - 无记录：创建新记录，存入上班时间和校验异常
           - 有记录：若本次打卡时间更早则覆盖（保留最早上班时间）
        5. flush 并 refresh，返回记录和异常列表

        重复打卡策略：取最早的上班时间（对应"到岗最早记录为准"的业务规则）

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            data: 打卡请求 Schema（含 GPS、WiFi、照片、是否模拟定位等字段）

        返回:
            (AttendanceRecord, 异常列表)
            异常列表为空表示打卡合法，非空表示存在异常（记录仍会写入，由 HR 后续处理）
        """
        from app.services import field_membership as membership
        field_context = await membership.prepare_punch(db, employee_id, data) if await membership.managed_field_person(db, employee_id) else None
        now = field_context["now"] if field_context else datetime.now(timezone.utc)
        # 业务日期固定使用 UTC+8（中国标准时间），不依赖服务器本地时区设置
        _cst = timezone(timedelta(hours=8))
        cst_now = now.astimezone(_cst)
        today, runtime = (field_context["day"], field_context["runtime"]) if field_context else await AttendanceRecordService._resolve_punch_target_date(db, employee_id, cst_now)
        rule = runtime["rule"] if runtime else None
        if rule is None:
            raise ValueError("未配置有效考勤规则，请联系管理员安排班次")
        anomalies: List[str] = []
        if data.photo_url:
            from app.models.field_service import FieldFile
            uploaded = await db.scalar(select(FieldFile.id).where(FieldFile.path == data.photo_url, FieldFile.owner_id == employee_id))
            if not uploaded:
                raise ValueError("打卡照片必须使用本人上传的有效文件")
        device_text = AttendanceRecordService._build_device_text(data, request_user_agent)

        if rule:
            anomalies = AttendanceRecordService._validate_clock(
                rule,
                data.gps_latitude,
                data.gps_longitude,
                data.wifi_ssid,
                data.photo_url,
                data.is_mock_location,
            )
            win_start, win_end = AttendanceRecordService._window_from_runtime(runtime or {})
            if not AttendanceRecordService._within_punch_window(cst_now, win_start, win_end):
                anomalies.append("不在可打卡时段")

        # 查找当日已有记录
        result = await db.execute(
            select(AttendanceRecord).where(
                and_(
                    AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.date == today,
                )
            )
        )
        record = field_context["record"] if field_context else result.scalar_one_or_none()

        if record is None:
            record = AttendanceRecord(
                employee_id=employee_id,
                date=today,
                clock_in_time=now,
                clock_in_gps_lat=data.gps_latitude,
                clock_in_gps_lng=data.gps_longitude,
                clock_in_location=(str(data.location_name or "").strip() or str(data.location_address or "").strip() or None),
                clock_in_device=device_text,
                clock_in_wifi_ssid=data.wifi_ssid,
                clock_in_photo_url=data.photo_url,
                is_mock_location=data.is_mock_location,
                source=data.source,
                anomaly_type="; ".join(anomalies) if anomalies else None,
            )
            db.add(record)
        else:
            # 更新已有记录 (重复打卡取最早的上班时间)
            if record.clock_in_time is None or now < record.clock_in_time:
                record.clock_in_time = now
                record.clock_in_gps_lat = data.gps_latitude
                record.clock_in_gps_lng = data.gps_longitude
                record.clock_in_location = (str(data.location_name or "").strip() or str(data.location_address or "").strip() or record.clock_in_location)
                record.clock_in_device = device_text
                record.clock_in_wifi_ssid = data.wifi_ssid
                record.clock_in_photo_url = data.photo_url

        await AttendanceRecordService._recalculate_record_after_punch(
            db,
            record,
            employee_id,
            today,
            runtime,
            anomalies,
        )
        if anomalies:
            parts = [part.strip() for part in str(record.anomaly_type or "").split(";") if part.strip()]
            seen = set(parts)
            for item in anomalies:
                if item not in seen:
                    parts.append(item)
                    seen.add(item)
            record.anomaly_type = "; ".join(parts) if parts else None

        punch_event = await AttendanceRecordService._append_punch_time_record(
            db,
            employee_id=employee_id,
            record=record,
            work_date=today,
            punch_time=now,
            punch_type="clock_in",
            data=data,
            device_info=device_text,
            remark="; ".join(anomalies) if anomalies else None,
        )

        await db.flush()
        if field_context:
            record = await membership.finish_punch(db, field_context, punch_event.id)
        else:
            await db.refresh(record)
            AttendanceRecordService._attach_display_status(record, runtime)
        await AttendanceRecordService._refresh_month_summary_for_date(db, employee_id, today)
        return record, anomalies

    # ------------------------------------------------------------------
    # 下班打卡
    # ------------------------------------------------------------------
    @staticmethod
    async def clock_out(
        db: AsyncSession,
        employee_id: int,
        data: ClockOutRequest,
        request_user_agent: Optional[str] = None,
    ) -> Tuple[AttendanceRecord, List[str]]:
        """
        处理下班打卡，在更新打卡时间的同时计算工时、加班时长和考勤状态。

        业务流程：
        1. 获取当前时间（UTC）和本地日期
        2. 查询员工当日适用的考勤规则
        3. 执行打卡校验，收集异常
        4. 查询今日考勤记录：
           - 无记录：创建新记录，同时附加"缺少上班打卡"异常
           - 有记录：使用本次下班卡覆盖旧下班卡，支持移动端“更新下班卡”
        5. 调用 _calculate_work_hours() 计算实际工时
        6. 调用 _calculate_overtime() 计算加班时长
        7. 调用 _determine_status() 判定考勤状态（迟到/早退/缺卡/正常）
        8. 按本次下班卡结果重写异常说明，避免早退更新为正常后旧异常残留

        重复打卡策略：以最后一次提交为准，新的下班卡时间和定位覆盖旧值

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            data: 打卡请求 Schema（含 GPS、WiFi、照片、是否模拟定位等字段）

        返回:
            (AttendanceRecord, 异常列表)
            返回的 record.status 已更新为当日最终考勤状态
        """
        from app.services import field_membership as membership
        field_context = await membership.prepare_punch(db, employee_id, data) if await membership.managed_field_person(db, employee_id) else None
        now = field_context["now"] if field_context else datetime.now(timezone.utc)
        # 业务日期固定使用 UTC+8（中国标准时间），不依赖服务器本地时区设置
        _cst = timezone(timedelta(hours=8))
        cst_now = now.astimezone(_cst)
        today, runtime = (field_context["day"], field_context["runtime"]) if field_context else await AttendanceRecordService._resolve_punch_target_date(db, employee_id, cst_now)
        rule = runtime["rule"] if runtime else None
        if rule is None:
            raise ValueError("未配置有效考勤规则，请联系管理员安排班次")
        anomalies: List[str] = []
        if data.photo_url:
            from app.models.field_service import FieldFile
            uploaded = await db.scalar(select(FieldFile.id).where(FieldFile.path == data.photo_url, FieldFile.owner_id == employee_id))
            if not uploaded:
                raise ValueError("打卡照片必须使用本人上传的有效文件")
        device_text = AttendanceRecordService._build_device_text(data, request_user_agent)

        if rule:
            anomalies = AttendanceRecordService._validate_clock(
                rule,
                data.gps_latitude,
                data.gps_longitude,
                data.wifi_ssid,
                data.photo_url,
                data.is_mock_location,
            )
            win_start, win_end = AttendanceRecordService._window_from_runtime(runtime or {})
            if not AttendanceRecordService._within_punch_window(cst_now, win_start, win_end):
                anomalies.append("不在可打卡时段")

        result = await db.execute(
            select(AttendanceRecord).where(
                and_(
                    AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.date == today,
                )
            )
        )
        record = field_context["record"] if field_context else result.scalar_one_or_none()

        if record is None:
            # 没有上班打卡, 仅记录下班
            record = AttendanceRecord(
                employee_id=employee_id,
                date=today,
                clock_out_time=now,
                clock_out_gps_lat=data.gps_latitude,
                clock_out_gps_lng=data.gps_longitude,
                clock_out_location=(str(data.location_name or "").strip() or str(data.location_address or "").strip() or None),
                clock_out_device=device_text,
                clock_out_wifi_ssid=data.wifi_ssid,
                clock_out_photo_url=data.photo_url,
                is_mock_location=data.is_mock_location,
                source=data.source,
                anomaly_type="; ".join(["缺少上班打卡"] + anomalies) if anomalies else "缺少上班打卡",
            )
            db.add(record)
        else:
            # 下班卡允许员工反复更新，最新一次提交覆盖旧时间和位置。
            record.clock_out_time = now
            record.clock_out_gps_lat = data.gps_latitude
            record.clock_out_gps_lng = data.gps_longitude
            record.clock_out_location = (str(data.location_name or "").strip() or str(data.location_address or "").strip() or record.clock_out_location)
            record.clock_out_device = device_text
            record.clock_out_wifi_ssid = data.wifi_ssid
            record.clock_out_photo_url = data.photo_url
            record.is_mock_location = data.is_mock_location
            record.source = data.source

        await AttendanceRecordService._recalculate_record_after_punch(
            db,
            record,
            employee_id,
            today,
            runtime,
            anomalies,
        )

        current_anomalies = [] if bool((runtime or {}).get("no_punch")) else list(anomalies)
        if (
            not bool((runtime or {}).get("no_punch"))
            and record.clock_in_time is None
            and "缺少上班打卡" not in current_anomalies
        ):
            current_anomalies.insert(0, "缺少上班打卡")
        unique_anomalies = list(dict.fromkeys(item for item in current_anomalies if item))
        record.anomaly_type = "; ".join(unique_anomalies) if unique_anomalies else None

        punch_event = await AttendanceRecordService._append_punch_time_record(
            db,
            employee_id=employee_id,
            record=record,
            work_date=today,
            punch_time=now,
            punch_type="clock_out",
            data=data,
            device_info=device_text,
            remark="; ".join(unique_anomalies) if unique_anomalies else None,
        )

        await db.flush()
        if field_context:
            record = await membership.finish_punch(db, field_context, punch_event.id)
        else:
            await db.refresh(record)
            AttendanceRecordService._attach_display_status(record, runtime)
        await AttendanceRecordService._refresh_month_summary_for_date(db, employee_id, today)
        return record, anomalies

    # ------------------------------------------------------------------
    # 查询考勤记录
    # ------------------------------------------------------------------
    @staticmethod
    async def query_records(
        db: AsyncSession, params: AttendanceRecordQuery
    ) -> Tuple[List[AttendanceRecord], int]:
        """
        分页查询考勤记录，支持按员工、日期范围和状态筛选。

        参数:
            db: 异步数据库会话
            params: 查询参数 Schema（含 employee_id, start_date, end_date, status, page, page_size）

        返回:
            (考勤记录列表, 总记录数)
            列表按日期降序、id 降序排列
        """
        stmt = select(AttendanceRecord)
        count_stmt = select(func.count()).select_from(AttendanceRecord)

        needs_employee_join = params.department_id is not None or bool(params.keyword)
        if needs_employee_join:
            stmt = stmt.join(Employee, AttendanceRecord.employee_id == Employee.id)
            count_stmt = count_stmt.join(Employee, AttendanceRecord.employee_id == Employee.id)

        if params.work_hour_type is not None:
            stmt = (
                stmt
                .outerjoin(
                    ShiftSchedule,
                    and_(
                        ShiftSchedule.employee_id == AttendanceRecord.employee_id,
                        ShiftSchedule.date == AttendanceRecord.date,
                    ),
                )
                .outerjoin(AttendanceRule, AttendanceRule.id == ShiftSchedule.rule_id)
            )
            count_stmt = (
                count_stmt
                .outerjoin(
                    ShiftSchedule,
                    and_(
                        ShiftSchedule.employee_id == AttendanceRecord.employee_id,
                        ShiftSchedule.date == AttendanceRecord.date,
                    ),
                )
                .outerjoin(AttendanceRule, AttendanceRule.id == ShiftSchedule.rule_id)
            )

        conditions = []
        if params.employee_id is not None:
            conditions.append(AttendanceRecord.employee_id == params.employee_id)
        if params.department_id is not None:
            conditions.append(Employee.department_id == params.department_id)
        if params.keyword:
            like = f"%{params.keyword.strip()}%"
            conditions.append(
                or_(
                    Employee.name.ilike(like),
                    Employee.employee_no.ilike(like),
                )
            )
        if params.work_hour_type is not None:
            conditions.append(AttendanceRule.work_hour_type == params.work_hour_type)
        if params.start_date is not None:
            conditions.append(AttendanceRecord.date >= params.start_date)
        if params.end_date is not None:
            conditions.append(AttendanceRecord.date <= params.end_date)
        status_filters = list(params.statuses or [])
        if not status_filters and params.status is not None:
            status_filters = [params.status]
        if status_filters:
            normal_selected = AttendanceStatus.normal in status_filters
            other_statuses = [item for item in status_filters if item != AttendanceStatus.normal]
            status_conditions = []
            if normal_selected:
                status_conditions.append(and_(
                    AttendanceRecord.status == AttendanceStatus.normal,
                    AttendanceRecord.anomaly_type.is_(None),
                ))
            if other_statuses:
                status_conditions.append(AttendanceRecord.status.in_(other_statuses))
            conditions.append(or_(*status_conditions))
        if params.anomalies_only:
            conditions.append(or_(
                AttendanceRecord.status.notin_([
                    AttendanceStatus.normal,
                    AttendanceStatus.business_trip,
                    AttendanceStatus.on_leave,
                ]),
                AttendanceRecord.anomaly_type.isnot(None),
            ))

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = (
            stmt.order_by(AttendanceRecord.date.desc(), AttendanceRecord.id.desc())
            .offset((params.page - 1) * params.page_size)
            .limit(params.page_size)
        )
        result = await db.execute(stmt)
        records = list(result.scalars().all())

        cst_today = datetime.now(AttendanceRecordService._cst()).date()
        changed = False
        for record in records:
            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                record.employee_id,
                record.date,
            )
            runtime_rule = runtime.get("rule") if isinstance(runtime, dict) else None
            record.expected_hours = AttendanceReportService._standard_work_hours_from_runtime(
                runtime_rule,
                runtime,
                record.date,
            )
            if record.correction_status == CorrectionStatus.approved:
                AttendanceRecordService._attach_display_status(record, runtime)
                continue
            before = (
                record.status,
                record.work_hours,
                record.overtime_hours,
                record.anomaly_type,
            )
            await AttendanceRecordService._recalculate_record_after_punch(
                db,
                record,
                record.employee_id,
                record.date,
                runtime,
            )
            record.anomaly_type = AttendanceRecordService._rebuild_anomaly_text_with_status(record, runtime)
            after = (
                record.status,
                record.work_hours,
                record.overtime_hours,
                record.anomaly_type,
            )
            if before != after:
                changed = True
            AttendanceRecordService._attach_display_status(record, runtime)
        if changed:
            await db.flush()

        return records, total

    @staticmethod
    async def _refresh_month_summary_for_date(
        db: AsyncSession,
        employee_id: int,
        target_date: date,
    ) -> None:
        """
        打卡或申诉状态发生变更后，增量刷新该员工当月月报快照。
        若当月已锁定，则跳过刷新，保持薪资结算口径稳定。
        """
        locked_summary = await db.scalar(
            select(func.count())
            .select_from(AttendanceMonthSummary)
            .where(
                and_(
                    AttendanceMonthSummary.employee_id == employee_id,
                    AttendanceMonthSummary.year == target_date.year,
                    AttendanceMonthSummary.month == target_date.month,
                    AttendanceMonthSummary.status == MonthlySummaryStatus.locked,
                )
            )
        )
        if int(locked_summary or 0) > 0:
            return

        await MonthSummaryService.generate(
            db,
            MonthSummaryGenerateRequest(
                year=target_date.year,
                month=target_date.month,
                employee_ids=[employee_id],
            ),
        )

    @staticmethod
    async def get_record(db: AsyncSession, record_id: int) -> Optional[AttendanceRecord]:
        """
        按 ID 查询单条考勤记录。

        参数:
            db: 异步数据库会话
            record_id: 考勤记录主键 ID

        返回:
            AttendanceRecord 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(AttendanceRecord).where(AttendanceRecord.id == record_id)
        )
        record = result.scalar_one_or_none()
        if record is not None:
            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                record.employee_id,
                record.date,
            )
            record.expected_hours = AttendanceReportService._standard_work_hours_from_runtime(
                runtime.get("rule") if runtime else None, runtime, record.date,
            )
            if record.correction_status != CorrectionStatus.approved:
                await AttendanceRecordService._recalculate_record_after_punch(
                    db, record, record.employee_id, record.date, runtime,
                )
                record.anomaly_type = AttendanceRecordService._rebuild_anomaly_text_with_status(record, runtime)
                await db.flush()
            AttendanceRecordService._attach_display_status(record, runtime)
        return record

    # ------------------------------------------------------------------
    # 异常列表
    # ------------------------------------------------------------------
    @staticmethod
    async def list_anomalies(
        db: AsyncSession,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[AttendanceRecord], int]:
        """
        列出有异常的考勤记录（排除正常、出差、请假状态）。

        "异常"定义：考勤状态不在 [normal, business_trip, on_leave] 中的记录，
        即包含：late（迟到）、early_leave（早退）、absent（旷工）、missed_clock（缺卡）。

        参数:
            db: 异步数据库会话
            start_date: 查询起始日期（可选）
            end_date: 查询结束日期（可选）
            page: 页码（从 1 开始）
            page_size: 每页记录数

        返回:
            (异常考勤记录列表, 总记录数)，按日期降序排列
        """
        conditions = [
            or_(AttendanceRecord.status.notin_([
                AttendanceStatus.normal,
                AttendanceStatus.business_trip,
                AttendanceStatus.on_leave,
            ]), AttendanceRecord.anomaly_type.isnot(None))
        ]
        if start_date:
            conditions.append(AttendanceRecord.date >= start_date)
        if end_date:
            conditions.append(AttendanceRecord.date <= end_date)

        stmt = select(AttendanceRecord).where(and_(*conditions))
        count_stmt = select(func.count()).select_from(AttendanceRecord).where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = (
            stmt.order_by(AttendanceRecord.date.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    # ------------------------------------------------------------------
    # 补卡 / 异常修正
    # ------------------------------------------------------------------
    @staticmethod
    async def request_correction(
        db: AsyncSession, employee_id: int, data: CorrectionRequest
    ) -> Optional[AttendanceRecord]:
        """
        员工提交考勤修正申请（标记记录为“待审批”状态）。

        本方法仅保存修正说明并标记审批状态，不直接改写实际打卡时间。
        """
        record = await AttendanceRecordService.get_record(db, data.record_id)
        if record is None or record.employee_id != employee_id:
            return None
        record.correction_status = CorrectionStatus.pending
        record.correction_note = data.correction_note
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def approve_correction(
        db: AsyncSession, data: CorrectionApproval
    ) -> Optional[AttendanceRecord]:
        """HR/主管审批旧版考勤修正申请。"""
        record = await AttendanceRecordService.get_record(db, data.record_id)
        if record is None:
            return None
        if data.approved:
            record.correction_status = CorrectionStatus.approved
            record.status = AttendanceStatus.normal
            record.anomaly_type = None
        else:
            record.correction_status = CorrectionStatus.rejected
        if data.note:
            record.correction_note = (record.correction_note or "") + f"\n审批意见: {data.note}"
        await db.flush()
        await db.refresh(record)
        await AttendanceRecordService._refresh_month_summary_for_date(db, record.employee_id, record.date)
        return record

    @staticmethod
    async def submit_appeal(
        db: AsyncSession,
        data: AttendanceAppealCreate,
        current_user: Employee,
    ) -> Optional[AttendanceRecord]:
        """提交异常申诉（管理员可代员工提交，员工仅可提交本人记录）"""
        record = await AttendanceRecordService.get_record(db, data.record_id)
        if record is None:
            return None
        can_submit_for_others = await AttendanceRecordService._has_any_role(
            db,
            current_user,
            ("admin", "hr", "manager"),
        )
        if not can_submit_for_others and record.employee_id != current_user.id:
            return None
        extra_time = f"（实际时间:{data.appeal_time}）" if data.appeal_time else ""
        record.correction_status = CorrectionStatus.pending
        record.correction_note = f"[申诉]{data.reason}{extra_time}"
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def review_appeal(
        db: AsyncSession,
        record_id: int,
        data: AttendanceAppealReview,
    ) -> Optional[AttendanceRecord]:
        """审批异常申诉"""
        record = await AttendanceRecordService.get_record(db, record_id)
        if record is None:
            return None
        if data.approved:
            record.correction_status = CorrectionStatus.approved
            record.status = AttendanceStatus.normal
            record.anomaly_type = None
            record.correction_note = (record.correction_note or "") + f"\n[审批通过]{data.note or ''}"
        else:
            record.correction_status = CorrectionStatus.rejected
            record.correction_note = (record.correction_note or "") + f"\n[审批驳回]{data.note or ''}"
        await db.flush()
        await db.refresh(record)
        await AttendanceRecordService._refresh_month_summary_for_date(db, record.employee_id, record.date)
        return record


class PunchCorrectionEligibilityService:
    """补卡审批发起前的只读资格计算服务。"""

    DEFAULT_PUNCH_TYPES = ("check_in", "check_out")
    PATCH_TYPE_MISSING = "缺卡/旷工"
    PATCH_TYPE_LATE = "迟到"
    PATCH_TYPE_EARLY = "早退"
    PATCH_TYPE_OTHER = "其他异常（地点/设备异常）"
    PATCH_TYPE_NORMAL = "正常"

    BIZ_MESSAGES = {
        "OK": "可补卡",
        "PUNCH_CORRECTION_NOT_ALLOWED": "不可补卡",
        "PATCH_APPLY_DISABLED": "不可补卡：命中的考勤规则未开启补卡申请",
        "DATE_OUT_OF_PATCH_WINDOW": "不可补卡：目标日期不在允许补卡时间范围内",
        "MONTH_SUMMARY_LOCKED": "不可补卡：目标日期所在月度考勤已锁定",
        "MONTH_LIMIT_REACHED": "不可补卡：当月补卡次数已达上限",
        "DUPLICATE_PENDING_APPLICATION": "不可补卡：同一日期和卡点已有待审批补卡",
        "NO_ATTENDANCE_REQUIRED": "不可补卡：当天无需出勤或无需打卡",
        "NO_PATCHABLE_PUNCH": "不可补卡：当天没有可补的卡点",
        "ATTENDANCE_RULE_MISSING": "无法计算补卡资格：员工在目标日期没有可用考勤规则",
        "SHIFT_DATA_INCONSISTENT": "无法计算补卡资格：排班或班次时间配置冲突",
        "ATTENDANCE_POLICY_INVALID": "无法计算补卡资格：考勤规则补卡配置不完整或非法",
    }

    DENY_MESSAGES = {
        "PATCH_APPLY_DISABLED": "请先在考勤规则中开启补卡申请",
        "DATE_OUT_OF_PATCH_WINDOW": "目标日期不在允许补卡时间范围内",
        "MONTH_SUMMARY_LOCKED": "目标日期所在月度考勤已锁定",
        "MONTH_LIMIT_REACHED": "当月补卡次数已达上限",
        "DUPLICATE_PENDING_APPLICATION": "同一员工、日期、卡点已有待审批补卡",
        "NO_ATTENDANCE_REQUIRED": "当天无需出勤或无需打卡",
        "NO_PATCHABLE_PUNCH": "当天没有可补的卡点",
        "ATTENDANCE_RULE_MISSING": "请先配置并启用适用于该员工的考勤规则",
        "SHIFT_DATA_INCONSISTENT": "请检查当天排班和班次时间配置",
        "ATTENDANCE_POLICY_INVALID": "请检查考勤规则中的补卡类型、时间限制和班次时间",
    }

    @staticmethod
    def parse_target_date(raw: str) -> date:
        text = str(raw or "").strip()
        try:
            if re.fullmatch(r"\d{8}", text):
                return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
                return date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("日期格式错误，应为 YYYY-MM-DD") from exc
        raise ValueError("日期格式错误，应为 YYYY-MM-DD")

    @staticmethod
    def resolve_timezone(tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(str(tz_name or "Asia/Shanghai"))
        except ZoneInfoNotFoundError as exc:
            raise ValueError("时区格式错误") from exc

    @staticmethod
    def _request_now(request_at: Optional[datetime], tz: ZoneInfo) -> datetime:
        if request_at is None:
            return datetime.now(tz)
        if request_at.tzinfo is None:
            request_at = request_at.replace(tzinfo=tz)
        return request_at.astimezone(tz)

    @staticmethod
    def _punch_types(data: PunchCorrectionEligibilityRequest) -> list[str]:
        values = [str(item) for item in (data.punch_types or []) if item]
        return values or list(PunchCorrectionEligibilityService.DEFAULT_PUNCH_TYPES)

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return getattr(value, "value", value)

    @staticmethod
    def _time_text(value: Optional[time]) -> Optional[str]:
        return AttendanceRecordService._to_hm_string(value) if value else None

    @staticmethod
    def _local_iso(value: Optional[datetime], tz: ZoneInfo) -> Optional[str]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(tz).isoformat()

    @staticmethod
    def _parse_patch_day_limit(raw: Any) -> Optional[int]:
        text = str(raw or "").strip()
        if not text or text == "无限制":
            return None
        match = re.fullmatch(r"过去(\d{1,2})天内", text)
        if not match:
            return None
        days = int(match.group(1))
        return days if 1 <= days <= 31 else None

    @staticmethod
    def _parse_count_limit(raw: Any) -> Optional[int]:
        text = str(raw or "").strip()
        if not text or text == "无限制":
            return None
        match = re.fullmatch(r"(\d{1,2})次", text)
        if not match:
            return None
        limit = int(match.group(1))
        return limit if 1 <= limit <= 31 else None

    @staticmethod
    def _parse_deadline_day(raw: Any) -> Optional[int]:
        text = str(raw or "").strip()
        if not text or text == "不设置":
            return None
        match = re.fullmatch(r"下月(\d{1,2})日", text)
        if not match:
            return None
        day = int(match.group(1))
        return day if 1 <= day <= 31 else None

    @staticmethod
    def _month_bounds(year: int, month: int) -> tuple[date, date]:
        last_day = monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)

    @staticmethod
    def _next_month_day(target_date: date, day: int) -> date:
        year = target_date.year + (1 if target_date.month == 12 else 0)
        month = 1 if target_date.month == 12 else target_date.month + 1
        return date(year, month, min(day, monthrange(year, month)[1]))

    @staticmethod
    def _date_range(start_date: date, end_date: date) -> list[date]:
        if end_date < start_date:
            return []
        return [start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]

    @staticmethod
    def _deny(code: str, message: Optional[str] = None) -> dict[str, str]:
        return {
            "code": code,
            "message": message or PunchCorrectionEligibilityService.DENY_MESSAGES.get(code, code),
        }

    @staticmethod
    def _build_empty_data(
        employee_id: int,
        target_date: date,
        tz_name: str,
        *,
        deny_code: str,
        eligible_range: Optional[dict[str, Any]] = None,
        policy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "employee_id": employee_id,
            "target_date": target_date.isoformat(),
            "timezone": tz_name,
            "can_apply": False,
            "deny_reasons": [PunchCorrectionEligibilityService._deny(deny_code)],
            "eligible_range": eligible_range,
            "policy": policy,
            "summary": {
                "window_day_count": 0,
                "eligible_day_count": 0,
                "eligible_punch_count": 0,
            },
            "days": [],
        }

    @staticmethod
    def _policy_snapshot(
        rule: AttendanceRule,
        runtime: Dict[str, Any],
        used_count: int,
        remaining_count: Optional[int],
    ) -> dict[str, Any]:
        extra = runtime.get("extra") or AttendanceRecordService._extra(rule)
        patch_types = extra.get("patch_types") if isinstance(extra.get("patch_types"), list) else []
        return {
            "rule_id": getattr(rule, "id", None),
            "rule_name": getattr(rule, "name", None),
            "rule_type": str(runtime.get("rule_type") or AttendanceRecordService._infer_rule_type(rule, extra)),
            "work_hour_type": PunchCorrectionEligibilityService._enum_value(getattr(rule, "work_hour_type", None)),
            "enable_patch_apply": bool(extra.get("enable_patch_apply")),
            "patch_types": [str(item) for item in patch_types if str(item or "").strip()],
            "patch_time_limit": extra.get("patch_time_limit") or "无限制",
            "patch_month_limit": extra.get("patch_month_limit") or "无限制",
            "patch_deadline": extra.get("patch_deadline") or "不设置",
            "used_count_in_month": used_count,
            "remaining_count_in_month": remaining_count,
        }

    @staticmethod
    def _resolve_eligible_range(
        target_date: date,
        request_date: date,
        extra: Dict[str, Any],
    ) -> tuple[Optional[date], dict[str, Any]]:
        day_limit = PunchCorrectionEligibilityService._parse_patch_day_limit(extra.get("patch_time_limit"))
        if day_limit is None:
            display_start = min(target_date, request_date)
            display_end = min(target_date, request_date)
            description = "不限制补卡日期；本次仅返回目标日期"
            return None, {
                "start_date": display_start.isoformat(),
                "end_date": display_end.isoformat(),
                "source": "attendance_rule.extra_config.patch_time_limit",
                "description": description,
            }
        start = request_date - timedelta(days=day_limit - 1)
        return start, {
            "start_date": start.isoformat(),
            "end_date": request_date.isoformat(),
            "source": "attendance_rule.extra_config.patch_time_limit",
            "description": f"过去{day_limit}天内可提交补卡",
        }

    @staticmethod
    def _target_window_denies(
        target_date: date,
        request_date: date,
        window_start: Optional[date],
        extra: Dict[str, Any],
    ) -> list[dict[str, str]]:
        denies: list[dict[str, str]] = []
        if target_date > request_date or (window_start is not None and target_date < window_start):
            limit_text = str(extra.get("patch_time_limit") or "无限制")
            message = "目标日期不在允许补卡时间范围内"
            if limit_text != "无限制":
                message = f"仅允许{limit_text}提交补卡"
            denies.append(PunchCorrectionEligibilityService._deny("DATE_OUT_OF_PATCH_WINDOW", message))
        deadline_day = PunchCorrectionEligibilityService._parse_deadline_day(extra.get("patch_deadline"))
        if deadline_day is not None and request_date >= PunchCorrectionEligibilityService._next_month_day(target_date, deadline_day):
            denies.append(
                PunchCorrectionEligibilityService._deny(
                    "DATE_OUT_OF_PATCH_WINDOW",
                    f"已超过{target_date.year}年{target_date.month}月补卡截止日期",
                )
            )
        return denies

    @staticmethod
    def _validate_policy(runtime: Dict[str, Any]) -> Optional[str]:
        rule = runtime.get("rule")
        if rule is None:
            return "ATTENDANCE_RULE_MISSING"
        extra = runtime.get("extra") or AttendanceRecordService._extra(rule)
        if bool(extra.get("enable_patch_apply")):
            patch_types = extra.get("patch_types")
            if not isinstance(patch_types, list) or not [item for item in patch_types if str(item or "").strip()]:
                return "ATTENDANCE_POLICY_INVALID"
            patch_time_limit = str(extra.get("patch_time_limit") or "无限制")
            if patch_time_limit != "无限制" and PunchCorrectionEligibilityService._parse_patch_day_limit(patch_time_limit) is None:
                return "ATTENDANCE_POLICY_INVALID"
            patch_month_limit = str(extra.get("patch_month_limit") or "无限制")
            if patch_month_limit != "无限制" and PunchCorrectionEligibilityService._parse_count_limit(patch_month_limit) is None:
                return "ATTENDANCE_POLICY_INVALID"
            patch_deadline = str(extra.get("patch_deadline") or "不设置")
            if patch_deadline != "不设置" and PunchCorrectionEligibilityService._parse_deadline_day(patch_deadline) is None:
                return "ATTENDANCE_POLICY_INVALID"
        check_in_required = bool(runtime.get("check_in_required", True))
        check_out_required = bool(runtime.get("check_out_required", True))
        if check_in_required and runtime.get("expected_in") is None:
            return "ATTENDANCE_POLICY_INVALID"
        if check_out_required and runtime.get("expected_out") is None:
            return "ATTENDANCE_POLICY_INVALID"
        return None

    @staticmethod
    async def _load_records_by_date(
        db: AsyncSession,
        employee_id: int,
        start_date: date,
        end_date: date,
    ) -> dict[date, AttendanceRecord]:
        if end_date < start_date:
            return {}
        result = await db.execute(
            select(AttendanceRecord).where(
                and_(
                    AttendanceRecord.employee_id == employee_id,
                    AttendanceRecord.date >= start_date,
                    AttendanceRecord.date <= end_date,
                )
            )
        )
        return {item.date: item for item in result.scalars().all()}

    @staticmethod
    async def _load_pending_corrections_by_slot(
        db: AsyncSession,
        employee_id: int,
        start_date: date,
        end_date: date,
        punch_types: list[str],
    ) -> dict[tuple[date, str], int]:
        if end_date < start_date:
            return {}
        result = await db.execute(
            select(PunchCorrectionRequest).where(
                and_(
                    PunchCorrectionRequest.employee_id == employee_id,
                    PunchCorrectionRequest.correction_date >= start_date,
                    PunchCorrectionRequest.correction_date <= end_date,
                    PunchCorrectionRequest.status == "pending",
                    PunchCorrectionRequest.punch_type.in_(punch_types),
                )
            )
        )
        out: dict[tuple[date, str], int] = {}
        for item in result.scalars().all():
            out[(item.correction_date, str(item.punch_type))] = int(item.id)
        return out

    @staticmethod
    async def _count_month_corrections(
        db: AsyncSession,
        employee_id: int,
        year: int,
        month: int,
    ) -> int:
        start_date, end_date = PunchCorrectionEligibilityService._month_bounds(year, month)
        count = await db.scalar(
            select(func.count())
            .select_from(PunchCorrectionRequest)
            .where(
                and_(
                    PunchCorrectionRequest.employee_id == employee_id,
                    PunchCorrectionRequest.correction_date >= start_date,
                    PunchCorrectionRequest.correction_date <= end_date,
                    PunchCorrectionRequest.status.in_(["pending", "approved"]),
                )
            )
        )
        return int(count or 0)

    @staticmethod
    async def _is_month_locked(
        db: AsyncSession,
        employee_id: int,
        year: int,
        month: int,
    ) -> bool:
        count = await db.scalar(
            select(func.count())
            .select_from(AttendanceMonthSummary)
            .where(
                and_(
                    AttendanceMonthSummary.employee_id == employee_id,
                    AttendanceMonthSummary.year == year,
                    AttendanceMonthSummary.month == month,
                    AttendanceMonthSummary.status == MonthlySummaryStatus.locked,
                )
            )
        )
        return int(count or 0) > 0

    @staticmethod
    async def _expected_work_day(
        db: AsyncSession,
        employee: Employee,
        target_date: date,
        runtime: Optional[Dict[str, Any]],
    ) -> bool:
        shift = (runtime or {}).get("shift")
        if shift is not None and PunchCorrectionEligibilityService._enum_value(getattr(shift, "shift_type", None)) == ShiftType.rest.value:
            return False
        return await MonthSummaryService._expected_work_day(
            db,
            int(employee.id),
            target_date,
            getattr(employee, "location_id", None),
            employee=employee,
        )

    @staticmethod
    def _expected_datetimes(
        target_date: date,
        expected_in: Optional[time],
        expected_out: Optional[time],
        tz: ZoneInfo,
    ) -> tuple[Optional[datetime], Optional[datetime]]:
        in_dt = datetime.combine(target_date, expected_in, tzinfo=tz) if expected_in else None
        out_dt = datetime.combine(target_date, expected_out, tzinfo=tz) if expected_out else None
        if in_dt is not None and out_dt is not None and out_dt <= in_dt:
            out_dt += timedelta(days=1)
        return in_dt, out_dt

    @staticmethod
    def _punch_window(
        runtime: Dict[str, Any],
        target_date: date,
        tz: ZoneInfo,
    ) -> tuple[datetime, datetime]:
        expected_in = runtime.get("expected_in")
        expected_out = runtime.get("expected_out")
        expected_in_dt, expected_out_dt = PunchCorrectionEligibilityService._expected_datetimes(
            target_date,
            expected_in,
            expected_out,
            tz,
        )
        raw_start, raw_end = AttendanceRecordService._window_from_runtime(runtime)
        start_time = AttendanceRecordService._parse_hm(raw_start)
        end_time = AttendanceRecordService._parse_hm(raw_end)
        if start_time and end_time:
            start_dt = datetime.combine(target_date, start_time, tzinfo=tz)
            end_dt = datetime.combine(target_date, end_time, tzinfo=tz)
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            return start_dt, end_dt
        if expected_in_dt is not None:
            start_dt = expected_in_dt - timedelta(hours=2)
        else:
            start_dt = datetime.combine(target_date, time(0, 0), tzinfo=tz)
        if expected_out_dt is not None:
            end_dt = expected_out_dt + timedelta(hours=4)
        else:
            end_dt = datetime.combine(target_date, time(23, 59), tzinfo=tz)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    @staticmethod
    def _allowed_punch_time(
        runtime: Dict[str, Any],
        target_date: date,
        punch_type: str,
        tz: ZoneInfo,
    ) -> dict[str, Optional[str]]:
        expected_in_dt, expected_out_dt = PunchCorrectionEligibilityService._expected_datetimes(
            target_date,
            runtime.get("expected_in"),
            runtime.get("expected_out"),
            tz,
        )
        window_start, window_end = PunchCorrectionEligibilityService._punch_window(runtime, target_date, tz)
        if punch_type == "check_in":
            allowed_end = expected_out_dt or window_end
            suggested = expected_in_dt
        else:
            allowed_end = window_end
            suggested = expected_out_dt
        if allowed_end < window_start:
            allowed_end = window_end
        return {
            "start": window_start.isoformat(),
            "end": min(allowed_end, window_end).isoformat(),
            "suggested": suggested.isoformat() if suggested else None,
        }

    @staticmethod
    def _normal_result_time(
        rule: AttendanceRule,
        runtime: Dict[str, Any],
        target_date: date,
        punch_type: str,
        tz: ZoneInfo,
    ) -> dict[str, Optional[str]]:
        expected_in_dt, expected_out_dt = PunchCorrectionEligibilityService._expected_datetimes(
            target_date,
            runtime.get("expected_in"),
            runtime.get("expected_out"),
            tz,
        )
        window_start, window_end = PunchCorrectionEligibilityService._punch_window(runtime, target_date, tz)
        if punch_type == "check_in":
            late_allow = AttendanceRecordService._late_allow_minutes(rule, str(runtime.get("flex_mode") or "none"))
            end_dt = (expected_in_dt + timedelta(minutes=late_allow)) if expected_in_dt else window_end
            return {"start": window_start.isoformat(), "end": min(end_dt, window_end).isoformat()}
        early_allow = int(runtime.get("early_threshold_minutes") or 0)
        start_dt = (expected_out_dt - timedelta(minutes=early_allow)) if expected_out_dt else window_start
        return {"start": max(start_dt, window_start).isoformat(), "end": window_end.isoformat()}

    @staticmethod
    def _status_patch_reason(
        record: Optional[AttendanceRecord],
        punch_type: str,
        check_required: bool,
        patch_types: set[str],
    ) -> Optional[tuple[str, str]]:
        has_punch = bool(getattr(record, "clock_in_time", None)) if punch_type == "check_in" else bool(getattr(record, "clock_out_time", None))
        status_value = PunchCorrectionEligibilityService._enum_value(getattr(record, "status", None))
        anomaly_text = str(getattr(record, "anomaly_type", "") or "")
        if check_required and not has_punch and PunchCorrectionEligibilityService.PATCH_TYPE_MISSING in patch_types:
            if punch_type == "check_in":
                return "MISSING_CHECK_IN", "上班缺卡"
            return "MISSING_CHECK_OUT", "下班缺卡"
        if status_value == AttendanceStatus.absent.value and PunchCorrectionEligibilityService.PATCH_TYPE_MISSING in patch_types:
            return "ABSENT", "旷工补卡"
        if punch_type == "check_in" and status_value == AttendanceStatus.late.value and PunchCorrectionEligibilityService.PATCH_TYPE_LATE in patch_types:
            return "LATE_CHECK_IN", "上班迟到"
        if punch_type == "check_out" and status_value == AttendanceStatus.early_leave.value and PunchCorrectionEligibilityService.PATCH_TYPE_EARLY in patch_types:
            return "EARLY_CHECK_OUT", "下班早退"
        if anomaly_text and PunchCorrectionEligibilityService.PATCH_TYPE_OTHER in patch_types:
            return "OTHER_ATTENDANCE_ANOMALY", "地点或设备异常"
        if has_punch and status_value == AttendanceStatus.normal.value and PunchCorrectionEligibilityService.PATCH_TYPE_NORMAL in patch_types:
            return "NORMAL_PUNCH_ADJUSTMENT", "正常打卡更正"
        return None

    @staticmethod
    def _build_patchable_punch(
        record: Optional[AttendanceRecord],
        rule: AttendanceRule,
        runtime: Dict[str, Any],
        target_date: date,
        punch_type: str,
        tz: ZoneInfo,
        pending_id: Optional[int],
        check_required: bool,
        patch_types: set[str],
    ) -> dict[str, Any]:
        current_punch_time = getattr(record, "clock_in_time", None) if punch_type == "check_in" else getattr(record, "clock_out_time", None)
        reason = PunchCorrectionEligibilityService._status_patch_reason(
            record,
            punch_type,
            check_required,
            patch_types,
        )
        label = "上班卡" if punch_type == "check_in" else "下班卡"
        if pending_id is not None:
            return {
                "punch_type": punch_type,
                "label": label,
                "can_apply": False,
                "reason_code": "DUPLICATE_PENDING_APPLICATION",
                "reason": "已有待审批补卡",
                "current_punch_time": PunchCorrectionEligibilityService._local_iso(current_punch_time, tz),
                "pending_correction_id": pending_id,
                "allowed_punch_time": PunchCorrectionEligibilityService._allowed_punch_time(runtime, target_date, punch_type, tz),
                "normal_result_time": PunchCorrectionEligibilityService._normal_result_time(rule, runtime, target_date, punch_type, tz),
            }
        if reason is None:
            return {
                "punch_type": punch_type,
                "label": label,
                "can_apply": False,
                "reason_code": "NO_PATCHABLE_PUNCH",
                "reason": "当前卡点无需补卡",
                "current_punch_time": PunchCorrectionEligibilityService._local_iso(current_punch_time, tz),
                "pending_correction_id": None,
                "allowed_punch_time": PunchCorrectionEligibilityService._allowed_punch_time(runtime, target_date, punch_type, tz),
                "normal_result_time": PunchCorrectionEligibilityService._normal_result_time(rule, runtime, target_date, punch_type, tz),
            }
        reason_code, reason_text = reason
        return {
            "punch_type": punch_type,
            "label": label,
            "can_apply": True,
            "reason_code": reason_code,
            "reason": reason_text,
            "current_punch_time": PunchCorrectionEligibilityService._local_iso(current_punch_time, tz),
            "pending_correction_id": None,
            "allowed_punch_time": PunchCorrectionEligibilityService._allowed_punch_time(runtime, target_date, punch_type, tz),
            "normal_result_time": PunchCorrectionEligibilityService._normal_result_time(rule, runtime, target_date, punch_type, tz),
        }

    @staticmethod
    def _build_shift_payload(
        record: Optional[AttendanceRecord],
        rule: AttendanceRule,
        runtime: Dict[str, Any],
        target_date: date,
        punch_types: list[str],
        tz: ZoneInfo,
        pending_by_slot: dict[tuple[date, str], int],
        patch_types: set[str],
    ) -> dict[str, Any]:
        segment = runtime.get("segment") if isinstance(runtime.get("segment"), dict) else {}
        shift = runtime.get("shift")
        window_start, window_end = PunchCorrectionEligibilityService._punch_window(runtime, target_date, tz)
        punches = []
        for punch_type in punch_types:
            check_required = bool(runtime.get("check_in_required", True)) if punch_type == "check_in" else bool(runtime.get("check_out_required", True))
            punches.append(
                PunchCorrectionEligibilityService._build_patchable_punch(
                    record,
                    rule,
                    runtime,
                    target_date,
                    punch_type,
                    tz,
                    pending_by_slot.get((target_date, punch_type)),
                    check_required,
                    patch_types,
                )
            )
        shift_type = getattr(shift, "shift_type", None)
        return {
            "shift_id": getattr(shift, "id", None),
            "shift_type": PunchCorrectionEligibilityService._enum_value(shift_type) or str(runtime.get("rule_type") or "fixed"),
            "shift_name": str(segment.get("name") or segment.get("shift_name") or "默认班次"),
            "rule_id": getattr(rule, "id", None),
            "rule_name": getattr(rule, "name", None),
            "segment_key": str(segment.get("key") or segment.get("id") or f"{runtime.get('rule_type') or 'fixed'}-default"),
            "segment_name": str(segment.get("name") or "默认班次"),
            "expected": {
                "clock_in": PunchCorrectionEligibilityService._time_text(runtime.get("expected_in")),
                "clock_out": PunchCorrectionEligibilityService._time_text(runtime.get("expected_out")),
            },
            "punch_window": {
                "start": window_start.isoformat(),
                "end": window_end.isoformat(),
            },
            "patchable_punches": punches,
        }

    @staticmethod
    async def _build_day_payload(
        db: AsyncSession,
        employee: Employee,
        target_date: date,
        runtime: Optional[Dict[str, Any]],
        record: Optional[AttendanceRecord],
        punch_types: list[str],
        tz: ZoneInfo,
        pending_by_slot: dict[tuple[date, str], int],
        patch_types: set[str],
        month_locked: bool,
        month_limit_reached: bool,
        base_denies: list[dict[str, str]],
    ) -> dict[str, Any]:
        rule = runtime.get("rule") if runtime else None
        day_type = await WorkCalendarService.get_day_type(db, target_date, getattr(employee, "location_id", None))
        should_attend = await PunchCorrectionEligibilityService._expected_work_day(db, employee, target_date, runtime) if runtime else False
        denies = list(base_denies)
        if month_locked:
            denies.append(PunchCorrectionEligibilityService._deny("MONTH_SUMMARY_LOCKED"))
        if month_limit_reached:
            denies.append(PunchCorrectionEligibilityService._deny("MONTH_LIMIT_REACHED"))
        if not should_attend:
            denies.append(PunchCorrectionEligibilityService._deny("NO_ATTENDANCE_REQUIRED"))

        shifts: list[dict[str, Any]] = []
        if rule is not None and should_attend:
            shift_payload = PunchCorrectionEligibilityService._build_shift_payload(
                record,
                rule,
                runtime or {},
                target_date,
                punch_types,
                tz,
                pending_by_slot,
                patch_types,
            )
            if denies:
                for item in shift_payload["patchable_punches"]:
                    item["can_apply"] = False
                    first_deny = denies[0]
                    item["reason_code"] = first_deny["code"]
                    item["reason"] = first_deny["message"]
            shifts.append(shift_payload)

        eligible_punches = [
            punch
            for shift in shifts
            for punch in shift.get("patchable_punches", [])
            if punch.get("can_apply")
        ]
        duplicate_pending = any(
            punch.get("reason_code") == "DUPLICATE_PENDING_APPLICATION"
            for shift in shifts
            for punch in shift.get("patchable_punches", [])
        )
        if should_attend and not denies and not eligible_punches and duplicate_pending:
            denies.append(PunchCorrectionEligibilityService._deny("DUPLICATE_PENDING_APPLICATION"))
        if should_attend and not denies and not eligible_punches:
            denies.append(PunchCorrectionEligibilityService._deny("NO_PATCHABLE_PUNCH"))

        status_value = PunchCorrectionEligibilityService._enum_value(getattr(record, "status", None))
        if status_value is None and should_attend:
            status_value = AttendanceStatus.absent.value
        return {
            "date": target_date.isoformat(),
            "day_type": PunchCorrectionEligibilityService._enum_value(day_type),
            "should_attend": should_attend,
            "locked": month_locked,
            "record_id": getattr(record, "id", None),
            "current_status": status_value or "--",
            "correction_status": PunchCorrectionEligibilityService._enum_value(getattr(record, "correction_status", CorrectionStatus.none)),
            "can_apply": bool(eligible_punches),
            "deny_reasons": denies,
            "shifts": shifts,
        }

    @staticmethod
    def _pick_biz_code(target_day: Optional[dict[str, Any]], base_denies: list[dict[str, str]]) -> str:
        if target_day and target_day.get("can_apply"):
            return "OK"
        all_denies = list(base_denies)
        if target_day:
            all_denies.extend(target_day.get("deny_reasons") or [])
        priority = [
            "PATCH_APPLY_DISABLED",
            "DATE_OUT_OF_PATCH_WINDOW",
            "MONTH_SUMMARY_LOCKED",
            "MONTH_LIMIT_REACHED",
            "DUPLICATE_PENDING_APPLICATION",
            "NO_ATTENDANCE_REQUIRED",
            "NO_PATCHABLE_PUNCH",
        ]
        seen = {item.get("code") for item in all_denies if isinstance(item, dict)}
        for code in priority:
            if code in seen:
                return code
        return "PUNCH_CORRECTION_NOT_ALLOWED"

    @staticmethod
    async def calculate(
        db: AsyncSession,
        data: PunchCorrectionEligibilityRequest,
        *,
        target_date: date,
        target_employee: Employee,
    ) -> dict[str, Any]:
        tz = PunchCorrectionEligibilityService.resolve_timezone(data.timezone)
        request_now = PunchCorrectionEligibilityService._request_now(data.request_at, tz)
        request_date = request_now.date()
        punch_types = PunchCorrectionEligibilityService._punch_types(data)

        runtime = await AttendanceRecordService._get_rule_runtime(
            db,
            data.employee_id,
            target_date,
            employee=target_employee,
        )
        if runtime is None:
            biz_code = "ATTENDANCE_RULE_MISSING"
            return {
                "biz_success": False,
                "biz_code": biz_code,
                "message": PunchCorrectionEligibilityService.BIZ_MESSAGES[biz_code],
                "data": PunchCorrectionEligibilityService._build_empty_data(
                    data.employee_id,
                    target_date,
                    data.timezone,
                    deny_code=biz_code,
                ),
            }

        invalid_code = PunchCorrectionEligibilityService._validate_policy(runtime)
        rule = runtime["rule"]
        extra = runtime.get("extra") or AttendanceRecordService._extra(rule)
        used_count = await PunchCorrectionEligibilityService._count_month_corrections(
            db,
            data.employee_id,
            target_date.year,
            target_date.month,
        )
        month_limit = PunchCorrectionEligibilityService._parse_count_limit(extra.get("patch_month_limit"))
        remaining_count = None if month_limit is None else max(0, month_limit - used_count)
        policy = PunchCorrectionEligibilityService._policy_snapshot(rule, runtime, used_count, remaining_count)
        window_start, eligible_range = PunchCorrectionEligibilityService._resolve_eligible_range(
            target_date,
            request_date,
            extra,
        )
        if invalid_code:
            return {
                "biz_success": False,
                "biz_code": invalid_code,
                "message": PunchCorrectionEligibilityService.BIZ_MESSAGES[invalid_code],
                "data": PunchCorrectionEligibilityService._build_empty_data(
                    data.employee_id,
                    target_date,
                    data.timezone,
                    deny_code=invalid_code,
                    eligible_range=eligible_range,
                    policy=policy,
                ),
            }

        base_denies: list[dict[str, str]] = []
        if not bool(extra.get("enable_patch_apply")):
            base_denies.append(PunchCorrectionEligibilityService._deny("PATCH_APPLY_DISABLED"))
        base_denies.extend(
            PunchCorrectionEligibilityService._target_window_denies(target_date, request_date, window_start, extra)
        )

        if window_start is None:
            calc_start = target_date
            calc_end = target_date
            window_day_count = 1
        else:
            calc_start = window_start
            calc_end = request_date
            window_day_count = len(PunchCorrectionEligibilityService._date_range(window_start, request_date))
        if not data.include_window_days:
            calc_start = target_date
            calc_end = target_date
        skip_day_details = any(item["code"] == "DATE_OUT_OF_PATCH_WINDOW" for item in base_denies)
        if skip_day_details:
            calc_start = target_date
            calc_end = target_date

        records_by_date = await PunchCorrectionEligibilityService._load_records_by_date(
            db,
            data.employee_id,
            calc_start,
            calc_end,
        )
        pending_by_slot = await PunchCorrectionEligibilityService._load_pending_corrections_by_slot(
            db,
            data.employee_id,
            calc_start,
            calc_end,
            punch_types,
        )

        runtime_cache: dict[date, Optional[Dict[str, Any]]] = {target_date: runtime}
        month_locked_cache: dict[tuple[int, int], bool] = {}
        month_used_cache: dict[tuple[int, int], int] = {(target_date.year, target_date.month): used_count}
        days: list[dict[str, Any]] = []
        patch_types = {str(item) for item in policy["patch_types"]}
        detail_days = [] if skip_day_details else PunchCorrectionEligibilityService._date_range(calc_start, calc_end)
        for day in detail_days:
            day_runtime = runtime_cache.get(day)
            if day_runtime is None and day != target_date:
                day_runtime = await AttendanceRecordService._get_rule_runtime(
                    db,
                    data.employee_id,
                    day,
                    employee=target_employee,
                )
                runtime_cache[day] = day_runtime
            if day_runtime is None:
                continue

            month_key = (day.year, day.month)
            if month_key not in month_locked_cache:
                month_locked_cache[month_key] = await PunchCorrectionEligibilityService._is_month_locked(
                    db,
                    data.employee_id,
                    day.year,
                    day.month,
                )
            if month_key not in month_used_cache:
                month_used_cache[month_key] = await PunchCorrectionEligibilityService._count_month_corrections(
                    db,
                    data.employee_id,
                    day.year,
                    day.month,
                )
            day_month_limit = PunchCorrectionEligibilityService._parse_count_limit(
                (day_runtime.get("extra") or {}).get("patch_month_limit")
            )
            day_month_limit_reached = day_month_limit is not None and month_used_cache[month_key] >= day_month_limit
            day_payload = await PunchCorrectionEligibilityService._build_day_payload(
                db,
                target_employee,
                day,
                day_runtime,
                records_by_date.get(day),
                punch_types,
                tz,
                pending_by_slot,
                patch_types,
                month_locked_cache[month_key],
                day_month_limit_reached,
                base_denies if day == target_date else [],
            )
            days.append(day_payload)

        target_day = next((item for item in days if item["date"] == target_date.isoformat()), None)
        eligible_punch_count = sum(
            1
            for day_item in days
            for shift in day_item.get("shifts", [])
            for punch in shift.get("patchable_punches", [])
            if punch.get("can_apply")
        )
        eligible_day_count = sum(1 for item in days if item.get("can_apply"))
        biz_code = PunchCorrectionEligibilityService._pick_biz_code(target_day, base_denies)
        data_payload = {
            "employee_id": data.employee_id,
            "target_date": target_date.isoformat(),
            "timezone": data.timezone,
            "can_apply": biz_code == "OK",
            "deny_reasons": [] if biz_code == "OK" else (target_day.get("deny_reasons") if target_day else base_denies),
            "eligible_range": eligible_range,
            "policy": policy,
            "summary": {
                "window_day_count": window_day_count,
                "eligible_day_count": eligible_day_count,
                "eligible_punch_count": eligible_punch_count,
            },
            "days": days if data.include_window_days or target_day else ([target_day] if target_day else []),
        }
        return {
            "biz_success": True,
            "biz_code": biz_code,
            "message": PunchCorrectionEligibilityService.BIZ_MESSAGES.get(biz_code, "不可补卡"),
            "data": data_payload,
        }


async def create_outside_approval(
    db: AsyncSession,
    employee: Employee,
    data: AttendanceOutsideApprovalCreate,
) -> AttendanceOutsideApprovalOut:
    """
    创建外出打卡审批实例。

    移动端点击“提交审批”后会进入通用审批引擎，管理端审批管理可按
    module=attendance、business_type=attendance_outside 获取和处理该单据。
    """
    occurred_at = data.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    occurred_at_utc = occurred_at.astimezone(timezone.utc)
    business_id = int(occurred_at_utc.timestamp())
    form_data = {
        "address": data.address,
        "place_title": data.place_title,
        "latitude": float(data.latitude),
        "longitude": float(data.longitude),
        "client_name": data.client_name,
        "remark": data.remark,
        "photo_url": data.photo_url,
        "occurred_at": occurred_at_utc.isoformat(),
        "employee_id": employee.id,
        "employee_name": getattr(employee, "name", ""),
    }
    employee_name = getattr(employee, "name", "") or ""
    summary = f"外出打卡审批 - {employee_name}" if employee_name else "外出打卡审批"
    await ensure_attendance_outside_flow(db)
    create_data = ApprovalInstanceCreate(
        module="attendance",
        business_id=business_id,
        business_type="attendance_outside",
        summary=summary,
        form_data=form_data,
    )
    from app.services.approval import create_instance

    instance = await create_instance(db, create_data, applicant_id=employee.id)
    return AttendanceOutsideApprovalOut(
        approval_instance_id=instance.id,
        status=instance.status,
        summary=summary,
        address=data.address,
        latitude=float(data.latitude),
        longitude=float(data.longitude),
        client_name=data.client_name,
        remark=data.remark,
        photo_url=data.photo_url,
        created_at=instance.created_at,
    )


async def ensure_attendance_outside_flow(db: AsyncSession) -> None:
    """
    确保外出打卡审批可以被管理端审批管理接住。

    管理端后续仍可在审批管理里编辑 module=attendance 的流程；这里仅在没有
    任何可用流程时补一个默认流程，避免员工点击“提交审批”后因未配置流程失败。
    """
    result = await db.execute(
        select(ApprovalFlow)
        .where(ApprovalFlow.module == "attendance", ApprovalFlow.is_active.is_(True))
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return
    flow = ApprovalFlow(
        name="外出打卡审批",
        module="attendance",
        description="员工移动端外出打卡提交审批，进入管理端审批管理处理。",
        is_active=True,
    )
    flow.nodes = [
        ApprovalNode(node_order=1, approver_type="department_head", node_type="approval"),
        ApprovalNode(node_order=2, approver_type="hr", node_type="approval"),
    ]
    db.add(flow)
    await db.flush()


class WorkCalendarService:
    """
    工作日历管理服务。

    工作日历记录特殊日期（节假日、调休、特殊工作日），用于：
    1. 月度汇总时计算当月实际工作日天数
    2. get_day_type() 提供"某日是工作日还是休息日"的判断接口
    3. 薪资计算时区分工作日/休息日/节假日加班，应用不同加班系数

    日类型（DayType）说明：
    - workday: 普通工作日
    - weekend: 普通周末
    - holiday: 法定节假日（加班系数 3.0）
    - special_workday: 调休工作日（如法定假期前的补班）

    日类型查询优先级（三级回退）：
    1. 地点特定的日历条目（location_id = 指定地点）
    2. 全局日历条目（location_id = NULL）
    3. 国务院办公厅年度节假日安排
    4. 按星期推断（周六/日 → weekend，否则 → workday）
    """

    @staticmethod
    def _official_day_type(target_date: date) -> Optional[DayType]:
        plan = OFFICIAL_HOLIDAY_PLANS.get(target_date.year)
        if not plan:
            return None
        makeup_workdays = plan.get("makeup_workdays")
        if isinstance(makeup_workdays, set) and target_date in makeup_workdays:
            return DayType.special_workday
        for _, start, end in plan.get("holidays", []):
            if start <= target_date <= end:
                return DayType.holiday
        return None

    @staticmethod
    async def create(db: AsyncSession, data: WorkCalendarCreate) -> WorkCalendar:
        """
        创建单条工作日历条目。

        参数:
            db: 异步数据库会话
            data: 工作日历创建 Schema（含 date, day_type, location_id, remark 等）

        返回:
            已持久化的 WorkCalendar 对象
        """
        entry = WorkCalendar(**data.model_dump())
        db.add(entry)
        await db.flush()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def batch_create(
        db: AsyncSession, entries: List[WorkCalendarCreate]
    ) -> List[WorkCalendar]:
        """
        批量创建工作日历条目，一般用于年初批量导入全年节假日安排。

        参数:
            db: 异步数据库会话
            entries: 工作日历创建 Schema 列表

        返回:
            已持久化的 WorkCalendar 对象列表（顺序与输入一致）
        """
        items: List[WorkCalendar] = []
        for data in entries:
            entry = WorkCalendar(**data.model_dump())
            db.add(entry)
            items.append(entry)
        await db.flush()
        for item in items:
            await db.refresh(item)
        return items

    @staticmethod
    async def list_calendar(
        db: AsyncSession,
        start_date: date,
        end_date: date,
        location_id: Optional[int] = None,
    ) -> Sequence[WorkCalendar]:
        """
        查询日期范围内的工作日历条目。

        若指定 location_id，会同时返回该地点特定的条目和全局条目（location_id 为 NULL），
        以便调用方自行决定优先级。

        参数:
            db: 异步数据库会话
            start_date: 查询起始日期（含）
            end_date: 查询结束日期（含）
            location_id: 地点 ID（可选）

        返回:
            按日期升序排列的 WorkCalendar 列表
        """
        stmt = select(WorkCalendar).where(
            and_(
                WorkCalendar.date >= start_date,
                WorkCalendar.date <= end_date,
            )
        )
        if location_id is not None:
            stmt = stmt.where(
                (WorkCalendar.location_id == location_id) | (WorkCalendar.location_id.is_(None))
            )
        stmt = stmt.order_by(WorkCalendar.date)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def update(
        db: AsyncSession, calendar_id: int, data: WorkCalendarUpdate
    ) -> Optional[WorkCalendar]:
        """
        部分更新工作日历条目。

        参数:
            db: 异步数据库会话
            calendar_id: 日历条目主键 ID
            data: 更新 Schema（使用 exclude_unset 实现局部更新）

        返回:
            更新后的 WorkCalendar 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(WorkCalendar).where(WorkCalendar.id == calendar_id)
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            return None
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(entry, field, value)
        await db.flush()
        await db.refresh(entry)
        return entry

    @staticmethod
    async def delete(db: AsyncSession, calendar_id: int) -> bool:
        """
        删除工作日历条目。

        参数:
            db: 异步数据库会话
            calendar_id: 日历条目主键 ID

        返回:
            True 表示删除成功；False 表示条目不存在
        """
        result = await db.execute(
            select(WorkCalendar).where(WorkCalendar.id == calendar_id)
        )
        entry = result.scalar_one_or_none()
        if entry is None:
            return False
        await db.delete(entry)
        await db.flush()
        return True

    @staticmethod
    async def get_day_type(
        db: AsyncSession, target_date: date, location_id: Optional[int] = None
    ) -> DayType:
        """
        获取某一天的日类型，采用三级优先级回退策略。

        查找逻辑：
        1. 若指定了 location_id，优先查该地点特定的日历条目（location_id 匹配）
        2. 若没有地点特定条目，使用全局条目（location_id = NULL）
        3. 若日历中没有任何条目，按国务院办公厅年度节假日安排兜底
        4. 若无年度安排，再按星期推断（周六/日 = weekend，否则 = workday）

        排序方式：location_id DESC NULLS LAST，即地点特定（非 NULL）条目排在前面。

        参数:
            db: 异步数据库会话
            target_date: 目标日期
            location_id: 地点 ID（可选，指定后优先查地点特定条目）

        返回:
            DayType 枚举值（workday / weekend / holiday / special_workday）
        """
        stmt = select(WorkCalendar).where(WorkCalendar.date == target_date)
        if location_id is not None:
            stmt = stmt.where(
                (WorkCalendar.location_id == location_id) | (WorkCalendar.location_id.is_(None))
            ).order_by(
                # 地点特定优先
                *order_by_nulls_last_desc(WorkCalendar.location_id)
            )
        result = await db.execute(stmt.limit(1))
        entry = result.scalar_one_or_none()
        if entry:
            return entry.day_type
        official_day_type = WorkCalendarService._official_day_type(target_date)
        if official_day_type is not None:
            return official_day_type
        # 回退: 按星期推断
        return DayType.weekend if target_date.weekday() >= 5 else DayType.workday


class MonthSummaryService:
    """
    月度考勤汇总服务。

    月度汇总是考勤模块的关键输出，直接供薪资计算模块消费。
    每月生成一次，统计以下指标：

    出勤类：
    - work_days: 本月应出勤工作日天数（按日历，周一至周五）
    - actual_days: 实际出勤天数（normal + late + early_leave 之和）
    - leave_days: 请假天数（on_leave 状态记录数）
    - business_trip_days: 出差天数

    异常类：
    - late_count: 迟到次数
    - early_leave_count: 早退次数
    - absent_count: 旷工天数
    - missed_clock_count: 缺卡次数
    - anomaly_count: 有 anomaly_type 的记录总数

    加班类（小时）：
    - overtime_weekday_hours: 工作日加班小时数（薪资系数 × 1.5）
    - overtime_weekend_hours: 休息日加班小时数（薪资系数 × 2.0）
    - overtime_holiday_hours: 节假日加班小时数（薪资系数 × 3.0，当前简化为 0 需日历数据支持）

    汇总状态流转：
    draft → employee_confirmed → locked
    锁定后数据不可变更，薪资计算引用锁定状态的汇总数据。
    """

    @staticmethod
    def _is_date_hit_special(extra: Dict[str, Any], key: str, target_date: date) -> bool:
        rows = extra.get(key)
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict):
                continue
            mode = str(row.get("mode") or "single")
            repeat = str(row.get("repeat") or "none")
            if mode == "range":
                date_range = row.get("date_range") if isinstance(row.get("date_range"), list) else []
                if len(date_range) != 2:
                    continue
                try:
                    start = date.fromisoformat(str(date_range[0])[:10])
                    end = date.fromisoformat(str(date_range[1])[:10])
                except Exception:
                    continue
                if repeat == "yearly":
                    s = (start.month, start.day)
                    e = (end.month, end.day)
                    t = (target_date.month, target_date.day)
                    if s <= t <= e:
                        return True
                elif repeat == "monthly":
                    if start.day <= target_date.day <= end.day:
                        return True
                else:
                    if start <= target_date <= end:
                        return True
            else:
                raw_date = str(row.get("date") or "")[:10]
                if not raw_date:
                    continue
                try:
                    d = date.fromisoformat(raw_date)
                except Exception:
                    continue
                if repeat == "yearly":
                    if (d.month, d.day) == (target_date.month, target_date.day):
                        return True
                elif repeat == "monthly":
                    if d.day == target_date.day:
                        return True
                else:
                    if d == target_date:
                        return True
        return False

    @staticmethod
    def _segment_matches_date(segment: Dict[str, Any], anchor: date, target_date: date) -> bool:
        weekday = target_date.weekday() + 1
        if bool(segment.get("biweekly_enabled")):
            this_days = AttendanceRecordService._parse_int_set(segment.get("this_week_weekdays"))
            next_days = AttendanceRecordService._parse_int_set(segment.get("next_week_weekdays"))
            is_even = AttendanceRecordService._is_even_week_from(anchor, target_date)
            selected = this_days if is_even else next_days
            return weekday in selected
        return weekday in AttendanceRecordService._parse_int_set(segment.get("weekdays"))

    @staticmethod
    def _rule_has_work_segment_on_date(rule: AttendanceRule, target_date: date) -> bool:
        extra = AttendanceRecordService._extra(rule)
        segments = extra.get("time_segments")
        if not isinstance(segments, list) or not segments:
            return target_date.weekday() < 5
        anchor = rule.effective_date or target_date
        for seg in segments:
            if isinstance(seg, dict) and MonthSummaryService._segment_matches_date(seg, anchor, target_date):
                return True
        return False

    @staticmethod
    async def _expected_work_day(
        db: AsyncSession,
        employee_id: int,
        target_date: date,
        location_id: Optional[int],
        employee: Optional[Employee] = None,
    ) -> bool:
        runtime = await AttendanceRecordService._get_rule_runtime(
            db,
            employee_id,
            target_date,
            employee=employee,
        )
        rule = runtime["rule"] if runtime else None
        day_type = await WorkCalendarService.get_day_type(db, target_date, location_id)
        if bool((runtime or {}).get("no_punch")):
            return False
        if rule is None:
            from app.services.field_membership import managed_field_person
            if await managed_field_person(db, employee_id, employee):
                return False
            return day_type in (DayType.workday, DayType.special_workday)
        extra = AttendanceRecordService._extra(rule)
        rule_type = AttendanceRecordService._infer_rule_type(rule, extra)

        if MonthSummaryService._is_date_hit_special(extra, "special_no_dates", target_date):
            return False
        if MonthSummaryService._is_date_hit_special(extra, "special_must_dates", target_date):
            return True

        if rule_type == "shift" and runtime is not None:
            if runtime.get("shift") is not None or runtime.get("project_assigned"):
                return True
            # 按排班上下班：手动排班模式下，无排班默认不计应出勤
            if str(extra.get("shift_arrange_mode") or "manual") == "manual":
                return False
            # 无需事先排班模式：若既不允许自选、也不允许系统对班，则不计应出勤
            if not bool(extra.get("shift_no_schedule_employee_select")) and not bool(extra.get("shift_no_schedule_system_match")):
                return False

        if day_type == DayType.holiday and bool(extra.get("legal_holiday_no_punch", True)):
            return False
        if day_type == DayType.weekend and bool(extra.get("rest_day_no_punch", False)):
            return False
        if day_type in (DayType.workday, DayType.special_workday):
            return True
        return MonthSummaryService._rule_has_work_segment_on_date(rule, target_date)

    @staticmethod
    def _effect_minutes(effect: AttendanceEffect) -> int:
        try:
            minutes = int(getattr(effect, "minutes", None) or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes > 0:
            return minutes
        start_at = getattr(effect, "start_at", None)
        end_at = getattr(effect, "end_at", None)
        if not (start_at and end_at):
            return 0
        try:
            return max(0, int((end_at - start_at).total_seconds() // 60))
        except Exception:
            return 0

    @staticmethod
    def _leave_effect_day_amount(day_effects: Sequence[AttendanceEffect]) -> Decimal:
        total = Decimal("0")
        for effect in day_effects:
            if str(getattr(effect, "effect_type", "") or "") != "leave":
                continue
            minutes = MonthSummaryService._effect_minutes(effect)
            if minutes <= 0:
                minutes = 480
            total += Decimal(minutes) / Decimal("480")
        return min(total, Decimal("1"))

    @staticmethod
    def _has_active_effect(day_effects: Sequence[AttendanceEffect], effect_type: str) -> bool:
        return any(str(getattr(effect, "effect_type", "") or "") == effect_type for effect in day_effects)

    @staticmethod
    def _outside_effect_covers_day(day_effects: Sequence[AttendanceEffect]) -> bool:
        return any(
            str(getattr(effect, "effect_type", "") or "") == "outside"
            and MonthSummaryService._effect_minutes(effect) >= 480
            for effect in day_effects
        )

    @staticmethod
    def _effect_pay_policy(effect: AttendanceEffect) -> str:
        payload = getattr(effect, "payload_json", None)
        detail = payload.get("effect_detail") if isinstance(payload, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
        form_data = {}
        event_payload = payload.get("approval_event") if isinstance(payload, dict) else {}
        if isinstance(event_payload, dict) and isinstance(event_payload.get("form_data"), dict):
            form_data = event_payload["form_data"]
        return str(
            getattr(effect, "pay_policy", None)
            or detail.get("settlement")
            or detail.get("pay_policy")
            or form_data.get("settlement")
            or form_data.get("pay_policy")
            or "overtime_pay"
        )

    @staticmethod
    async def generate(
        db: AsyncSession, data: MonthSummaryGenerateRequest
    ) -> List[AttendanceMonthSummary]:
        """
        生成指定年月的月度考勤汇总。

        若该月汇总已存在，会先删除旧数据再重新生成（幂等操作）。

        业务流程：
        1. 计算目标月份的日期范围（first_day ~ last_day）
        2. 查询该月所有（或指定员工的）考勤记录
        3. 按员工 ID 分组考勤记录
        4. 对每名员工：
           a. 删除已存在的旧汇总（确保幂等）
           b. 遍历月内每天，调用 WorkCalendarService.get_day_type() 统计工作日天数
           c. 汇总各类考勤状态计数
           d. 分别汇总工作日和周末的加班小时数
           e. 创建新的 AttendanceMonthSummary 记录（status=draft）

        注意：节假日加班（overtime_holiday_hours）当前固定为 0，
        需要日历数据细化后才能正确计算。

        参数:
            db: 异步数据库会话
            data: 月汇总生成请求（含 year, month, employee_ids 可选列表）

        返回:
            生成的 AttendanceMonthSummary 对象列表
        """
        year, month = data.year, data.month

        # 确定月份日期范围
        first_day = date(year, month, 1)
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)

        # 获取员工范围（即使没有打卡记录，也要生成汇总）
        employee_stmt = select(Employee).where(
            Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_PROBATION])
        )
        if data.employee_ids:
            employee_stmt = employee_stmt.where(Employee.id.in_(data.employee_ids))
        emp_res = await db.execute(employee_stmt)
        employees = list(emp_res.scalars().all())
        if not employees:
            return []
        employee_ids = [e.id for e in employees]
        employee_map = {e.id: e for e in employees}
        locked_result = await db.execute(
            select(AttendanceMonthSummary.employee_id)
            .where(
                and_(
                    AttendanceMonthSummary.year == year,
                    AttendanceMonthSummary.month == month,
                    AttendanceMonthSummary.status == MonthlySummaryStatus.locked,
                    AttendanceMonthSummary.employee_id.in_(employee_ids),
                )
            )
        )
        locked_employee_ids = {int(x) for x in locked_result.scalars().all()}

        # 获取该月所有考勤记录
        conditions = [
            AttendanceRecord.date >= first_day,
            AttendanceRecord.date <= last_day,
            AttendanceRecord.employee_id.in_(employee_ids),
        ]

        result = await db.execute(
            select(AttendanceRecord).where(and_(*conditions))
        )
        records = result.scalars().all()

        # 按员工分组
        employee_records: Dict[int, List[AttendanceRecord]] = {}
        for r in records:
            employee_records.setdefault(r.employee_id, []).append(r)

        effects_by_emp_date: Dict[tuple[int, date], List[AttendanceEffect]] = {}
        try:
            effect_result = await db.execute(
                select(AttendanceEffect).where(
                    and_(
                        AttendanceEffect.employee_id.in_(employee_ids),
                        AttendanceEffect.effect_status == "active",
                        AttendanceEffect.work_date >= first_day,
                        AttendanceEffect.work_date <= last_day,
                        AttendanceEffect.effect_type.in_(
                            ["leave", "business_trip", "outside", "overtime"]
                        ),
                    )
                )
            )
            for effect in effect_result.scalars().all():
                if effect.work_date is None:
                    continue
                effects_by_emp_date.setdefault((int(effect.employee_id), effect.work_date), []).append(effect)
        except (OperationalError, ProgrammingError):
            effects_by_emp_date = {}

        summaries: List[AttendanceMonthSummary] = []
        for emp_id in employee_ids:
            if emp_id in locked_employee_ids:
                continue
            emp_records = employee_records.get(emp_id, [])
            employee = employee_map.get(emp_id)
            rec_by_date: Dict[date, AttendanceRecord] = {}
            for r in emp_records:
                # 同一天若有多条记录，优先保留有下班卡/工时的记录
                old = rec_by_date.get(r.date)
                if old is None:
                    rec_by_date[r.date] = r
                    continue
                old_score = int(old.clock_out_time is not None) + int(old.work_hours is not None)
                new_score = int(r.clock_out_time is not None) + int(r.work_hours is not None)
                if new_score >= old_score:
                    rec_by_date[r.date] = r
            # 删除旧汇总（保证幂等性），但保留已锁定的记录（locked 状态不可覆盖）
            await db.execute(
                delete(AttendanceMonthSummary).where(
                    and_(
                        AttendanceMonthSummary.employee_id == emp_id,
                        AttendanceMonthSummary.year == year,
                        AttendanceMonthSummary.month == month,
                        AttendanceMonthSummary.status != MonthlySummaryStatus.locked,
                    )
                )
            )

            # 逐日核算考勤结果。审批 effect 拥有对当日旷工/缺卡的解释权，
            # 因此请假、出差、全天外出先于原始打卡异常生效。
            work_days = 0
            late = 0
            early = 0
            absent = 0
            missed = 0
            actual = 0
            leave_d = Decimal("0")
            trip_d = Decimal("0")
            anomaly = 0
            current = first_day
            while current <= last_day:
                should_work = await MonthSummaryService._expected_work_day(
                    db,
                    emp_id,
                    current,
                    getattr(employee, "location_id", None) if employee else None,
                    employee=employee,
                )
                if should_work:
                    work_days += 1
                day_effects = effects_by_emp_date.get((emp_id, current), [])
                leave_amount = MonthSummaryService._leave_effect_day_amount(day_effects)
                has_trip_effect = MonthSummaryService._has_active_effect(day_effects, "business_trip")
                outside_covers_day = MonthSummaryService._outside_effect_covers_day(day_effects)
                record = rec_by_date.get(current)

                if leave_amount > 0:
                    leave_d += leave_amount
                    if leave_amount >= Decimal("1"):
                        current += timedelta(days=1)
                        continue
                if has_trip_effect:
                    trip_d += Decimal("1")
                    current += timedelta(days=1)
                    continue
                if record is None:
                    if should_work and not outside_covers_day:
                        absent += 1
                    current += timedelta(days=1)
                    continue
                if outside_covers_day and record.status in (AttendanceStatus.absent, AttendanceStatus.missed_clock):
                    current += timedelta(days=1)
                    continue

                anomaly_parts = AttendanceRecordService._display_status_parts_from_anomaly(record)
                if record.status == AttendanceStatus.late or "迟到" in anomaly_parts:
                    late += 1
                if record.status == AttendanceStatus.early_leave or "早退" in anomaly_parts:
                    early += 1
                if record.status == AttendanceStatus.absent:
                    absent += 1
                if record.status == AttendanceStatus.missed_clock:
                    missed += 1
                if record.status in (AttendanceStatus.normal, AttendanceStatus.late, AttendanceStatus.early_leave):
                    actual += 1
                if record.status == AttendanceStatus.on_leave:
                    leave_d += Decimal("1")
                if record.status == AttendanceStatus.business_trip:
                    trip_d += Decimal("1")
                if record.anomaly_type:
                    anomaly += 1
                current += timedelta(days=1)

            unique_records = list(rec_by_date.values())

            # 加班统计：按工作日历类型精确分类
            ot_weekday = 0.0
            ot_weekend = 0.0
            ot_holiday = 0.0
            day_type_cache: Dict[date, DayType] = {}
            overtime_effect_dates: set[date] = set()
            for (effect_emp_id, effect_date), day_effects in effects_by_emp_date.items():
                if effect_emp_id != emp_id:
                    continue
                for effect in day_effects:
                    if str(getattr(effect, "effect_type", "") or "") != "overtime":
                        continue
                    minutes = MonthSummaryService._effect_minutes(effect)
                    if minutes <= 0:
                        continue
                    pay_policy = MonthSummaryService._effect_pay_policy(effect)
                    if pay_policy in {"comp_time", "rest", "time_off", "lieu", "adjust_rest"}:
                        continue
                    overtime_effect_dates.add(effect_date)
                    if effect_date not in day_type_cache:
                        day_type_cache[effect_date] = await WorkCalendarService.get_day_type(
                            db,
                            effect_date,
                            getattr(employee, "location_id", None) if employee else None,
                        )
                    h = float(Decimal(minutes) / Decimal("60"))
                    dt = day_type_cache[effect_date]
                    if dt == DayType.holiday:
                        ot_holiday += h
                    elif dt == DayType.weekend:
                        ot_weekend += h
                    else:
                        ot_weekday += h
            for r in unique_records:
                if not r.overtime_hours:
                    continue
                d = r.date
                if d in overtime_effect_dates:
                    continue
                if d not in day_type_cache:
                    day_type_cache[d] = await WorkCalendarService.get_day_type(
                        db,
                        d,
                        getattr(employee, "location_id", None) if employee else None,
                    )
                dt = day_type_cache[d]
                runtime_for_day = await AttendanceRecordService._get_rule_runtime(
                    db,
                    emp_id,
                    d,
                    employee=employee,
                )
                extra_for_day = (runtime_for_day or {}).get("extra") or {}
                if not AttendanceRecordService._is_overtime_payroll_eligible(extra_for_day, dt):
                    continue
                h = float(r.overtime_hours or 0)
                if dt == DayType.holiday:
                    ot_holiday += h
                elif dt == DayType.weekend:
                    ot_weekend += h
                else:
                    ot_weekday += h

            summary = AttendanceMonthSummary(
                employee_id=emp_id,
                year=year,
                month=month,
                work_days=work_days,
                actual_days=actual,
                late_count=late,
                early_leave_count=early,
                absent_count=absent,
                missed_clock_count=missed,
                overtime_weekday_hours=Decimal(str(round(ot_weekday, 2))),
                overtime_weekend_hours=Decimal(str(round(ot_weekend, 2))),
                overtime_holiday_hours=Decimal(str(round(ot_holiday, 2))),
                leave_days=leave_d,
                business_trip_days=trip_d,
                anomaly_count=anomaly,
                status=MonthlySummaryStatus.draft,
            )
            db.add(summary)
            summaries.append(summary)

        await db.flush()
        for s in summaries:
            await db.refresh(s)
        return summaries

    @staticmethod
    async def get_summary(
        db: AsyncSession, employee_id: int, year: int, month: int
    ) -> Optional[AttendanceMonthSummary]:
        """
        查询指定员工指定年月的考勤汇总记录。

        参数:
            db: 异步数据库会话
            employee_id: 员工 ID
            year: 年份
            month: 月份（1-12）

        返回:
            AttendanceMonthSummary 对象；若不存在则返回 None
        """
        result = await db.execute(
            select(AttendanceMonthSummary).where(
                and_(
                    AttendanceMonthSummary.employee_id == employee_id,
                    AttendanceMonthSummary.year == year,
                    AttendanceMonthSummary.month == month,
                )
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def employee_confirm(
        db: AsyncSession, summary_id: int, employee_id: int
    ) -> Optional[AttendanceMonthSummary]:
        """
        员工本人确认月度考勤汇总（draft → employee_confirmed）。

        若汇总已处于 employee_confirmed 或 locked 状态，则直接返回当前对象，不再修改。
        员工确认后，HR 可以执行月报锁定（lock_month）。

        参数:
            db: 异步数据库会话
            summary_id: 月汇总 ID
            employee_id: 操作者员工 ID（权限校验：只能确认自己的汇总）

        返回:
            更新后的 AttendanceMonthSummary 对象；若不存在或不属于该员工则返回 None
        """
        result = await db.execute(
            select(AttendanceMonthSummary).where(
                and_(
                    AttendanceMonthSummary.id == summary_id,
                    AttendanceMonthSummary.employee_id == employee_id,
                )
            )
        )
        summary = result.scalar_one_or_none()
        if summary is None:
            return None
        if summary.status != MonthlySummaryStatus.draft:
            return summary  # 已确认或锁定, 不修改
        summary.status = MonthlySummaryStatus.employee_confirmed
        await db.flush()
        await db.refresh(summary)
        return summary

    @staticmethod
    async def lock_month(
        db: AsyncSession, year: int, month: int
    ) -> int:
        """
        HR 锁定某月所有员工的考勤汇总（employee_confirmed → locked）。

        锁定后数据不可修改，薪资计算模块消费的就是锁定状态的数据。
        已处于 locked 状态的记录不受影响（WHERE 条件排除）。

        使用 SQLAlchemy bulk update 批量执行，不逐条加载对象，效率高。

        参数:
            db: 异步数据库会话
            year: 年份
            month: 月份（1-12）

        返回:
            实际被锁定的记录数量
        """
        now = datetime.now(timezone.utc)
        # 只锁定员工已确认的记录（draft 状态需先经员工确认再锁定）
        result = await db.execute(
            update(AttendanceMonthSummary)
            .where(
                and_(
                    AttendanceMonthSummary.year == year,
                    AttendanceMonthSummary.month == month,
                    AttendanceMonthSummary.status == MonthlySummaryStatus.employee_confirmed,
                )
            )
            .values(status=MonthlySummaryStatus.locked, locked_at=now)
        )
        await db.flush()
        return result.rowcount  # type: ignore[return-value]

    @staticmethod
    async def list_summaries(
        db: AsyncSession,
        year: int,
        month: int,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[AttendanceMonthSummary], int]:
        """
        分页查询指定年月所有员工的考勤汇总列表。

        参数:
            db: 异步数据库会话
            year: 年份
            month: 月份（1-12）
            page: 页码（从 1 开始）
            page_size: 每页记录数

        返回:
            (月汇总列表, 总记录数)，按员工 ID 升序排列
        """
        conditions = [
            AttendanceMonthSummary.year == year,
            AttendanceMonthSummary.month == month,
        ]
        count_result = await db.execute(
            select(func.count())
            .select_from(AttendanceMonthSummary)
            .where(and_(*conditions))
        )
        total = count_result.scalar() or 0

        result = await db.execute(
            select(AttendanceMonthSummary)
            .where(and_(*conditions))
            .order_by(AttendanceMonthSummary.employee_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(result.scalars().all()), total


class PunchCorrectionService:
    """
    补卡申请流程服务。

    员工漏打卡或打卡时间有误时，可提交补卡申请；审批通过后自动同步
    AttendanceRecord 的上下班打卡时间，并重新计算当天考勤结论。
    """

    @staticmethod
    async def list_corrections(
        db: AsyncSession,
        employee_id: Optional[int] = None,
        status: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[PunchCorrectionRequest], int]:
        conditions = []
        if employee_id is not None:
            conditions.append(PunchCorrectionRequest.employee_id == employee_id)
        if status is not None:
            conditions.append(PunchCorrectionRequest.status == status)

        base_stmt = select(PunchCorrectionRequest)
        count_stmt = select(func.count()).select_from(PunchCorrectionRequest)
        if conditions:
            base_stmt = base_stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        result = await db.execute(
            base_stmt.order_by(PunchCorrectionRequest.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all()), total

    @staticmethod
    async def create_correction(
        db: AsyncSession, data: PunchCorrectionCreate
    ) -> PunchCorrectionRequest:
        req = PunchCorrectionRequest(
            employee_id=data.employee_id,
            correction_date=data.correction_date,
            punch_time=AttendanceRecordService._local_business_datetime_to_storage(data.punch_time)
            or data.punch_time,
            punch_type=data.punch_type,
            reason=data.reason,
            status="pending",
        )
        db.add(req)
        await db.flush()

        runtime = await AttendanceRecordService._get_rule_runtime(
            db=db,
            employee_id=data.employee_id,
            target_date=data.correction_date,
        )
        rule = runtime.get("rule") if isinstance(runtime, dict) else None
        rule_extra = (getattr(rule, "extra_config", None) or {}) if rule else {}
        approval_bindings = rule_extra.get("approval_bindings") if isinstance(rule_extra, dict) else {}
        patch_binding = approval_bindings.get("patch_apply") if isinstance(approval_bindings, dict) else None
        patch_module = patch_binding.get("module") if isinstance(patch_binding, dict) else None
        patch_enabled = bool(patch_binding.get("enabled")) if isinstance(patch_binding, dict) else False

        if patch_enabled and patch_module:
            from app.services.approval import maybe_create_approval_instance

            instance = await maybe_create_approval_instance(
                db=db,
                module=str(patch_module),
                business_id=req.id,
                business_type="attendance_punch_correction",
                applicant_id=data.employee_id,
            )
            if instance is not None:
                stamp = f"approval_instance#{instance.id}"
                req.review_comment = stamp if not req.review_comment else f"{req.review_comment}\n{stamp}"
                await db.flush()

        await db.refresh(req)
        return req

    @staticmethod
    async def review_correction(
        db: AsyncSession,
        correction_id: int,
        reviewer_id: int,
        status: str,
        comment: Optional[str],
    ) -> Optional[PunchCorrectionRequest]:
        result = await db.execute(
            select(PunchCorrectionRequest).where(PunchCorrectionRequest.id == correction_id)
        )
        req = result.scalar_one_or_none()
        if req is None:
            return None

        req.status = status
        req.reviewer_id = reviewer_id
        req.review_comment = comment
        req.reviewed_at = datetime.now(timezone.utc)

        if status == "approved":
            rec_result = await db.execute(
                select(AttendanceRecord).where(
                    and_(
                        AttendanceRecord.employee_id == req.employee_id,
                        AttendanceRecord.date == req.correction_date,
                    )
                )
            )
            record = rec_result.scalar_one_or_none()
            if record is None:
                record = AttendanceRecord(
                    employee_id=req.employee_id,
                    date=req.correction_date,
                    source=ClockSource.manual,
                )
                db.add(record)

            if req.punch_type == "check_in":
                record.clock_in_time = req.punch_time
                record.source = ClockSource.manual
            else:
                record.clock_out_time = req.punch_time
                record.source = ClockSource.manual

            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                req.employee_id,
                req.correction_date,
            )
            anomalies: List[str] = []
            await AttendanceRecordService._recalculate_record_after_punch(
                db,
                record,
                req.employee_id,
                req.correction_date,
                runtime,
                anomalies,
            )
            if anomalies:
                parts = [part.strip() for part in str(record.anomaly_type or "").split(";") if part.strip()]
                seen = set(parts)
                for item in anomalies:
                    if item not in seen:
                        parts.append(item)
                        seen.add(item)
                record.anomaly_type = "; ".join(parts) if parts else None
            record.correction_status = CorrectionStatus.approved
            await AttendanceRecordService._refresh_month_summary_for_date(
                db,
                req.employee_id,
                req.correction_date,
            )

        await db.flush()
        await db.refresh(req)
        return req


class AttendancePunchTimeRecordService:
    """
    原始打卡时间记录服务。

    读取不可覆盖的打卡流水，按企业微信的月历式报表组织成:
    员工基础信息 + 每日全部打卡时间。
    """

    WEEKDAY_LABELS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

    @staticmethod
    def validate_month_window(start_date: date, end_date: date) -> None:
        if end_date < start_date:
            raise ValueError("结束日期不能早于开始日期")
        if (end_date - start_date).days > 30:
            raise ValueError("打卡时间记录最大仅支持按月查询")

    @staticmethod
    def date_columns(start_date: date, end_date: date) -> list[dict[str, str]]:
        AttendancePunchTimeRecordService.validate_month_window(start_date, end_date)
        columns: list[dict[str, str]] = []
        cursor = start_date
        while cursor <= end_date:
            weekday = AttendancePunchTimeRecordService.WEEKDAY_LABELS[cursor.weekday()]
            columns.append(
                {
                    "date": cursor.isoformat(),
                    "day": str(cursor.day),
                    "weekday": weekday,
                    "title": f"{cursor.day}\n{weekday}",
                }
            )
            cursor += timedelta(days=1)
        return columns

    @staticmethod
    def _employee_base_conditions(
        *,
        department_id: Optional[int],
        keyword: Optional[str],
        include_recent_left: bool,
    ) -> list[Any]:
        conditions: list[Any] = [
            Employee.status.in_(
                [EmployeeStatus.ACTIVE, EmployeeStatus.ON_PROBATION, EmployeeStatus.LEFT]
                if include_recent_left
                else [EmployeeStatus.ACTIVE, EmployeeStatus.ON_PROBATION]
            )
        ]
        if department_id is not None:
            conditions.append(Employee.department_id == department_id)
        if keyword:
            key = keyword.strip()
            if key:
                like = f"%{key}%"
                conditions.append(
                    or_(
                        Employee.name.ilike(like),
                        Employee.employee_no.ilike(like),
                        Employee.phone.ilike(like),
                    )
                )
        return conditions

    @staticmethod
    def _base_punch_filters(start_date: date, end_date: date) -> list[Any]:
        return [
            AttendancePunchTimeRecord.work_date >= start_date,
            AttendancePunchTimeRecord.work_date <= end_date,
        ]

    @staticmethod
    def _apply_rule_filter(stmt: Any, rule_id: Optional[int]) -> Any:
        if rule_id is None:
            return stmt
        return stmt.join(
            ShiftSchedule,
            and_(
                ShiftSchedule.employee_id == AttendancePunchTimeRecord.employee_id,
                ShiftSchedule.date == AttendancePunchTimeRecord.work_date,
            ),
        ).where(ShiftSchedule.rule_id == rule_id)

    @staticmethod
    async def query_calendar(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
        page: Optional[int] = 1,
        page_size: Optional[int] = 20,
    ) -> dict[str, Any]:
        AttendancePunchTimeRecordService.validate_month_window(start_date, end_date)
        punch_filters = AttendancePunchTimeRecordService._base_punch_filters(start_date, end_date)
        employee_filters = AttendancePunchTimeRecordService._employee_base_conditions(
            department_id=department_id,
            keyword=keyword,
            include_recent_left=include_recent_left,
        )

        id_stmt = (
            select(AttendancePunchTimeRecord.employee_id, func.min(Employee.name).label("employee_name"))
            .join(Employee, AttendancePunchTimeRecord.employee_id == Employee.id)
            .where(and_(*(punch_filters + employee_filters)))
            .group_by(AttendancePunchTimeRecord.employee_id)
            .order_by(func.min(Employee.name), AttendancePunchTimeRecord.employee_id)
        )
        count_stmt = (
            select(func.count(func.distinct(AttendancePunchTimeRecord.employee_id)))
            .select_from(AttendancePunchTimeRecord)
            .join(Employee, AttendancePunchTimeRecord.employee_id == Employee.id)
            .where(and_(*(punch_filters + employee_filters)))
        )
        id_stmt = AttendancePunchTimeRecordService._apply_rule_filter(id_stmt, rule_id)
        count_stmt = AttendancePunchTimeRecordService._apply_rule_filter(count_stmt, rule_id)

        total = int((await db.execute(count_stmt)).scalar() or 0)
        if page is not None and page_size is not None:
            id_stmt = id_stmt.offset((page - 1) * page_size).limit(page_size)
        employee_ids = [int(row.employee_id) for row in (await db.execute(id_stmt)).all()]

        columns = AttendancePunchTimeRecordService.date_columns(start_date, end_date)
        if not employee_ids:
            return {
                "total": total,
                "page": page or 1,
                "page_size": page_size or total,
                "date_columns": columns,
                "items": [],
            }

        employee_rows = await db.execute(
            select(Employee, Department.name.label("department_name"))
            .outerjoin(Department, Employee.department_id == Department.id)
            .where(Employee.id.in_(employee_ids))
        )
        employees_by_id: dict[int, tuple[Employee, Optional[str]]] = {
            int(emp.id): (emp, dept_name) for emp, dept_name in employee_rows.all()
        }

        raw_stmt = (
            select(AttendancePunchTimeRecord)
            .where(
                and_(
                    AttendancePunchTimeRecord.employee_id.in_(employee_ids),
                    *punch_filters,
                )
            )
            .order_by(
                AttendancePunchTimeRecord.employee_id,
                AttendancePunchTimeRecord.work_date,
                AttendancePunchTimeRecord.punch_time,
                AttendancePunchTimeRecord.id,
            )
        )
        raw_stmt = AttendancePunchTimeRecordService._apply_rule_filter(raw_stmt, rule_id)
        raw_records = list((await db.execute(raw_stmt)).scalars().all())

        punch_map: dict[int, dict[str, list[str]]] = {employee_id: {} for employee_id in employee_ids}
        for item in raw_records:
            local_dt = AttendanceRecordService._to_cst_datetime(item.punch_time)
            if local_dt is None:
                continue
            day_key = item.work_date.isoformat()
            punch_map.setdefault(int(item.employee_id), {}).setdefault(day_key, []).append(local_dt.strftime("%H:%M"))

        items: list[dict[str, Any]] = []
        for employee_id in employee_ids:
            emp_info = employees_by_id.get(employee_id)
            if not emp_info:
                continue
            emp, dept_name = emp_info
            by_date = {col["date"]: punch_map.get(employee_id, {}).get(col["date"], []) for col in columns}
            items.append(
                {
                    "employee_id": employee_id,
                    "employee_name": emp.name,
                    "employee_no": emp.employee_no,
                    "account": emp.employee_no,
                    "department_name": dept_name or emp.third_level_dept or "-",
                    "position": emp.position or "-",
                    "photo_url": emp.photo_url,
                    "punch_times_by_date": by_date,
                }
            )

        return {
            "total": total,
            "page": page or 1,
            "page_size": page_size or total,
            "date_columns": columns,
            "items": items,
        }

    @staticmethod
    async def export_calendar(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
    ) -> bytes:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        data = await AttendancePunchTimeRecordService.query_calendar(
            db,
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
            keyword=keyword,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
            page=None,
            page_size=None,
        )
        columns = data["date_columns"]
        rows = data["items"]

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "打卡时间记录"
        border = Border(
            left=Side(style="thin", color="D9E2EC"),
            right=Side(style="thin", color="D9E2EC"),
            top=Side(style="thin", color="D9E2EC"),
            bottom=Side(style="thin", color="D9E2EC"),
        )
        header_fill = PatternFill("solid", fgColor="F2F4F7")
        group_fill = PatternFill("solid", fgColor="EEF2F7")
        header_font = Font(color="475569", bold=True, size=10)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)

        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=3 + len(columns))
        ws.cell(row=1, column=1, value=f"打卡时间记录 {start_date:%Y/%m/%d} - {end_date:%Y/%m/%d}")
        ws.cell(row=1, column=1).font = Font(color="111827", bold=True, size=13)
        ws.cell(row=1, column=1).alignment = center

        ws.merge_cells(start_row=2, start_column=1, end_row=3, end_column=1)
        ws.cell(row=2, column=1, value="姓名")
        ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=3)
        ws.cell(row=2, column=2, value="基础信息")
        ws.cell(row=3, column=2, value="部门")
        ws.cell(row=3, column=3, value="职务")
        if columns:
            ws.merge_cells(start_row=2, start_column=4, end_row=2, end_column=3 + len(columns))
        ws.cell(row=2, column=4, value="打卡时间记录")
        for idx, col in enumerate(columns, start=4):
            ws.cell(row=3, column=idx, value=col["title"])

        for row_idx in (2, 3):
            ws.row_dimensions[row_idx].height = 38
            for col_idx in range(1, 4 + len(columns)):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.fill = group_fill if row_idx == 2 else header_fill
                cell.font = header_font
                cell.alignment = center
                cell.border = border

        for row_idx, row in enumerate(rows, start=4):
            ws.row_dimensions[row_idx].height = 48
            values = [row["employee_name"], row["department_name"], row["position"]]
            for col in columns:
                times = row.get("punch_times_by_date", {}).get(col["date"], [])
                values.append("\n".join(times) if times else "-")
            for col_idx, value in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.alignment = center
                cell.border = border

        widths = [18, 24, 16] + [14 for _ in columns]
        for col_idx, width in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(col_idx)].width = width
        ws.freeze_panes = "D4"
        ws.sheet_view.showGridLines = False

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output.getvalue()


class AttendanceReportService:
    """
    考勤报表导出服务。

    负责生成 Excel 格式的考勤报表，供 HR 下载后用于查阅或归档。
    当前实现：月度考勤月报（export_monthly_report）。

    报表依赖 openpyxl 库，表头使用深蓝色背景（#4F46E5）白色字体，
    数据行使用交替浅灰色背景（偶数行 #F8FAFC），所有单元格带细边框。

    数据来源：直接查询 AttendanceRecord 和 Employee 表，不依赖月度汇总，
    因此即使月报未生成也可以导出。
    """

    DAILY_OVERVIEW_COLUMN_SPECS: list[dict[str, str]] = [
        {"key": "date_label", "title": "时间"},
        {"key": "employee_name", "title": "姓名"},
        {"key": "account", "title": "账号"},
        {"key": "department_name", "title": "部门"},
        {"key": "position", "title": "职务"},
        {"key": "rule_name", "title": "所属规则"},
        {"key": "shift_name", "title": "班次"},
        {"key": "earliest_clock", "title": "最早"},
        {"key": "latest_clock", "title": "最晚"},
        {"key": "clock_count", "title": "打卡次数(次)"},
        {"key": "standard_work_hours", "title": "标准工作时长(小时)"},
        {"key": "actual_work_hours", "title": "实际工作时长(小时)"},
        {"key": "leave_application", "title": "假勤申请"},
        {"key": "attendance_result", "title": "考勤结果"},
        {"key": "anomaly_total", "title": "异常合计(次)"},
        {"key": "late_count", "title": "迟到次数(次)"},
        {"key": "late_minutes", "title": "迟到时长(分钟)"},
        {"key": "early_count", "title": "早退次数(次)"},
        {"key": "early_minutes", "title": "早退时长(分钟)"},
        {"key": "absent_count", "title": "旷工次数(次)"},
        {"key": "absent_minutes", "title": "旷工时长(分钟)"},
        {"key": "missed_count", "title": "缺卡次数(次)"},
        {"key": "location_abnormal_count", "title": "地点异常(次)"},
        {"key": "device_abnormal_count", "title": "设备异常(次)"},
        {"key": "outside_clock_count", "title": "外出打卡次数(次)"},
        {"key": "outside_earliest", "title": "最早"},
        {"key": "outside_latest", "title": "最晚"},
        {"key": "overtime_status", "title": "加班状态"},
        {"key": "overtime_hours", "title": "加班时长(小时)"},
        {"key": "weekday_ot_to_rest", "title": "工作日加班计为调休(小时)"},
        {"key": "weekday_ot_to_pay", "title": "工作日加班计为加班费(小时)"},
        {"key": "weekend_ot_to_rest", "title": "休息日加班计为调休(小时)"},
        {"key": "weekend_ot_to_pay", "title": "休息日加班计为加班费(小时)"},
        {"key": "holiday_ot_to_rest", "title": "节假日加班计为调休(小时)"},
        {"key": "holiday_ot_to_pay", "title": "节假日加班计为加班费(小时)"},
        {"key": "approve_punch_count", "title": "审批打卡次数(次)"},
        {"key": "field_work_count", "title": "外勤次数(次)"},
        {"key": "outside_hours", "title": "外出(小时)"},
    ]

    DAILY_DETAIL_COLUMN_SPECS: list[dict[str, str]] = [
        {"key": "date_label", "title": "日期"},
        {"key": "employee_name", "title": "姓名"},
        {"key": "account", "title": "账号"},
        {"key": "department_name", "title": "部门"},
        {"key": "position", "title": "职务"},
        {"key": "rule_name", "title": "所属规则"},
        {"key": "clock_type", "title": "打卡类型"},
        {"key": "expected_clock_time", "title": "应打卡时间"},
        {"key": "actual_clock_time", "title": "实际打卡时间"},
        {"key": "clock_status", "title": "打卡状态"},
        {"key": "clock_place", "title": "打卡地点"},
        {"key": "clock_device", "title": "打卡设备"},
        {"key": "remark_content", "title": "备注内容"},
        {"key": "remark_image", "title": "备注图片"},
        {"key": "leave_application", "title": "假勤申请"},
    ]

    DAILY_OVERVIEW_COLUMNS = [item["title"] for item in DAILY_OVERVIEW_COLUMN_SPECS]
    DAILY_DETAIL_COLUMNS = [item["title"] for item in DAILY_DETAIL_COLUMN_SPECS]

    MONTHLY_OVERVIEW_COLUMN_SPECS: list[dict[str, str]] = [
        {"key": "employee_name", "title": "姓名"},
        {"key": "account", "title": "账号"},
        {"key": "rule_name", "title": "所属规则"},
        {"key": "department_name", "title": "部门"},
        {"key": "position", "title": "职务"},
        {"key": "expected_days", "title": "应出勤天数(天)"},
        {"key": "actual_days", "title": "实际出勤天数(天)"},
        {"key": "rest_days", "title": "休息天数(天)"},
        {"key": "normal_days", "title": "正常天数(天)"},
        {"key": "abnormal_days", "title": "异常天数(天)"},
        {"key": "standard_work_hours", "title": "标准工作时长(小时)"},
        {"key": "actual_work_hours", "title": "实际工作时长(小时)"},
        {"key": "anomaly_total", "title": "异常合计(次)"},
        {"key": "late_count", "title": "迟到次数(次)"},
        {"key": "late_minutes", "title": "迟到时长(分钟)"},
        {"key": "early_count", "title": "早退次数(次)"},
        {"key": "early_minutes", "title": "早退时长(分钟)"},
        {"key": "absent_count", "title": "旷工次数(次)"},
        {"key": "absent_minutes", "title": "旷工时长(分钟)"},
        {"key": "missed_count", "title": "缺卡次数(次)"},
        {"key": "location_abnormal_count", "title": "地点异常(次)"},
        {"key": "device_abnormal_count", "title": "设备异常(次)"},
        {"key": "punch_correction_count", "title": "补卡次数(次)"},
        {"key": "approve_punch_count", "title": "审批打卡次数(次)"},
        {"key": "field_work_count", "title": "外勤次数(次)"},
        {"key": "outside_hours", "title": "外出(小时)"},
        {"key": "business_trip_days", "title": "出差(天)"},
        {"key": "annual_leave_days", "title": "年假(天)"},
        {"key": "marriage_leave_days", "title": "婚假(天)"},
        {"key": "maternity_leave_hours", "title": "产休假(小时)"},
        {"key": "paternity_leave_days", "title": "陪产假(天)"},
        {"key": "sick_leave_days", "title": "病假(天)"},
        {"key": "lieu_leave_hours", "title": "调休(小时)"},
        {"key": "personal_leave_days", "title": "事假(天)"},
        {"key": "long_sick_leave_days", "title": "病假（长期）(天)"},
        {"key": "prenatal_leave_days", "title": "产检假(天)"},
        {"key": "bereavement_leave_days", "title": "丧假(天)"},
        {"key": "general_sick_leave_days", "title": "病假（通用）(天)"},
        {"key": "overtime_hours", "title": "加班时长(小时)"},
        {"key": "weekday_ot_hours", "title": "工作日加班时长(小时)"},
        {"key": "weekday_ot_to_rest", "title": "工作日加班计为调休(小时)"},
        {"key": "weekday_ot_to_pay", "title": "工作日加班计为加班费(小时)"},
        {"key": "weekend_ot_hours", "title": "休息日加班时长(小时)"},
        {"key": "weekend_ot_to_rest", "title": "休息日加班计为调休(小时)"},
        {"key": "weekend_ot_to_pay", "title": "休息日加班计为加班费(小时)"},
        {"key": "holiday_ot_hours", "title": "节假日加班时长(小时)"},
        {"key": "holiday_ot_to_rest", "title": "节假日加班计为调休(小时)"},
        {"key": "holiday_ot_to_pay", "title": "节假日加班计为加班费(小时)"},
    ]

    MONTHLY_DETAIL_BASE_COLUMN_SPECS: list[dict[str, str]] = [
        {"key": "employee_name", "title": "姓名"},
        {"key": "account", "title": "账号"},
        {"key": "rule_name", "title": "所属规则"},
        {"key": "department_name", "title": "部门"},
        {"key": "position", "title": "职务"},
    ]
    MONTHLY_LEAVE_FIELD_BY_CODE: dict[str, str] = {
        "annual": "annual_leave_days",
        "marriage": "marriage_leave_days",
        "maternity": "maternity_leave_hours",
        "paternity": "paternity_leave_days",
        "sick": "sick_leave_days",
        "comp_time": "lieu_leave_hours",
        "personal": "personal_leave_days",
        "sick_long": "long_sick_leave_days",
        "prenatal_check": "prenatal_leave_days",
        "bereavement": "bereavement_leave_days",
        "sick_general": "general_sick_leave_days",
    }
    MONTHLY_LEAVE_HOUR_FIELDS: set[str] = {"maternity_leave_hours", "lieu_leave_hours"}
    MONTHLY_LEAVE_FIELDS: tuple[str, ...] = tuple(MONTHLY_LEAVE_FIELD_BY_CODE.values())
    LEAVE_TYPE_CODE_BY_NAME: dict[str, str] = {
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

    MONTHLY_OVERVIEW_COLUMNS = [item["title"] for item in MONTHLY_OVERVIEW_COLUMN_SPECS]
    MONTHLY_DETAIL_BASE_COLUMNS = [item["title"] for item in MONTHLY_DETAIL_BASE_COLUMN_SPECS]

    DAILY_OVERVIEW_EXPORT_GROUPS: dict[str, Optional[str]] = {
        "date_label": None,
        "employee_name": None,
        "account": None,
        "department_name": "基础信息",
        "position": "基础信息",
        "rule_name": "考勤概况",
        "shift_name": "考勤概况",
        "earliest_clock": "考勤概况",
        "latest_clock": "考勤概况",
        "clock_count": "考勤概况",
        "standard_work_hours": "考勤概况",
        "actual_work_hours": "考勤概况",
        "leave_application": "考勤概况",
        "attendance_result": "考勤概况",
        "anomaly_total": "异常统计",
        "late_count": "异常统计",
        "late_minutes": "异常统计",
        "early_count": "异常统计",
        "early_minutes": "异常统计",
        "absent_count": "异常统计",
        "absent_minutes": "异常统计",
        "missed_count": "异常统计",
        "location_abnormal_count": "异常统计",
        "device_abnormal_count": "异常统计",
        "outside_clock_count": "外出打卡",
        "outside_earliest": "外出打卡",
        "outside_latest": "外出打卡",
        "overtime_status": "加班统计",
        "overtime_hours": "加班统计",
        "weekday_ot_to_rest": "加班统计",
        "weekday_ot_to_pay": "加班统计",
        "weekend_ot_to_rest": "加班统计",
        "weekend_ot_to_pay": "加班统计",
        "holiday_ot_to_rest": "加班统计",
        "holiday_ot_to_pay": "加班统计",
        "approve_punch_count": "假勤统计",
        "field_work_count": "假勤统计",
        "outside_hours": "假勤统计",
    }

    DAILY_DETAIL_EXPORT_GROUPS: dict[str, Optional[str]] = {
        "date_label": None,
        "employee_name": None,
        "account": None,
        "department_name": "基础信息",
        "position": "基础信息",
        "rule_name": "基础信息",
        "clock_type": "打卡信息",
        "expected_clock_time": "打卡信息",
        "actual_clock_time": "打卡信息",
        "clock_status": "打卡信息",
        "clock_place": "打卡信息",
        "clock_device": "打卡信息",
        "remark_content": "备注信息",
        "remark_image": "备注信息",
        "leave_application": "假勤信息",
    }

    MONTHLY_OVERVIEW_EXPORT_GROUPS: dict[str, Optional[str]] = {
        "employee_name": None,
        "account": None,
        "rule_name": None,
        "department_name": "基础信息",
        "position": "基础信息",
        "expected_days": "考勤概况",
        "actual_days": "考勤概况",
        "rest_days": "考勤概况",
        "normal_days": "考勤概况",
        "abnormal_days": "考勤概况",
        "standard_work_hours": "考勤概况",
        "actual_work_hours": "考勤概况",
        "anomaly_total": "异常统计",
        "late_count": "异常统计",
        "late_minutes": "异常统计",
        "early_count": "异常统计",
        "early_minutes": "异常统计",
        "absent_count": "异常统计",
        "absent_minutes": "异常统计",
        "missed_count": "异常统计",
        "location_abnormal_count": "异常统计",
        "device_abnormal_count": "异常统计",
        "punch_correction_count": "假勤统计",
        "approve_punch_count": "假勤统计",
        "field_work_count": "假勤统计",
        "outside_hours": "假勤统计",
        "business_trip_days": "假勤统计",
        "annual_leave_days": "假勤统计",
        "marriage_leave_days": "假勤统计",
        "maternity_leave_hours": "假勤统计",
        "paternity_leave_days": "假勤统计",
        "sick_leave_days": "假勤统计",
        "lieu_leave_hours": "假勤统计",
        "personal_leave_days": "假勤统计",
        "long_sick_leave_days": "假勤统计",
        "prenatal_leave_days": "假勤统计",
        "bereavement_leave_days": "假勤统计",
        "general_sick_leave_days": "假勤统计",
        "overtime_hours": "加班统计",
        "weekday_ot_hours": "加班统计",
        "weekday_ot_to_rest": "加班统计",
        "weekday_ot_to_pay": "加班统计",
        "weekend_ot_hours": "加班统计",
        "weekend_ot_to_rest": "加班统计",
        "weekend_ot_to_pay": "加班统计",
        "holiday_ot_hours": "加班统计",
        "holiday_ot_to_rest": "加班统计",
        "holiday_ot_to_pay": "加班统计",
    }

    MONTHLY_DETAIL_EXPORT_GROUPS: dict[str, Optional[str]] = {
        "employee_name": None,
        "account": None,
        "rule_name": None,
        "department_name": "基础信息",
        "position": "基础信息",
    }

    @staticmethod
    def _resolve_column_specs(
        default_specs: list[dict[str, str]],
        requested_keys: Optional[list[str]] = None,
    ) -> list[dict[str, str]]:
        if not requested_keys:
            return default_specs
        spec_map = {item["key"]: item for item in default_specs}
        resolved = [spec_map[key] for key in requested_keys if key in spec_map]
        return resolved or default_specs

    @staticmethod
    def _to_cst(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        cst = AttendanceRecordService._cst()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).astimezone(cst)
        return value.astimezone(cst)

    @staticmethod
    def _format_clock(value: Optional[datetime]) -> str:
        dt = AttendanceReportService._to_cst(value)
        return dt.strftime("%H:%M") if dt else "-"

    @staticmethod
    def _format_clock_or_empty(value: Optional[datetime]) -> str:
        dt = AttendanceReportService._to_cst(value)
        return dt.strftime("%H:%M") if dt else "-"

    @staticmethod
    def _format_shift_text(rule: Optional[AttendanceRule]) -> str:
        if not rule or not rule.clock_in_time or not rule.clock_out_time:
            return "-"
        return f"{rule.clock_in_time.strftime('%H:%M')}-{rule.clock_out_time.strftime('%H:%M')}"

    @staticmethod
    def _status_text(record: AttendanceRecord) -> str:
        text = getattr(record, "display_status", None) or getattr(record.status, "value", record.status) or "-"
        return str(text)

    @staticmethod
    def _status_parts(text: str) -> list[str]:
        return [
            part.strip()
            for part in re.split(r"[\/,，、;；]", str(text or ""))
            if part and part.strip()
        ]

    @staticmethod
    def _attendance_clock_count(record: Optional[AttendanceRecord]) -> int:
        if record is None:
            return 0
        return int(getattr(record, "clock_in_time", None) is not None) + int(
            getattr(record, "clock_out_time", None) is not None
        )

    @staticmethod
    def _standard_rest_periods_from_runtime(runtime: Optional[Dict[str, Any]]) -> List[Tuple[time, time]]:
        if not runtime:
            return []
        rest_periods = runtime.get("rest_periods")
        if isinstance(rest_periods, list) and rest_periods:
            return rest_periods
        return []

    @staticmethod
    def _standard_work_hours_from_runtime(
        rule: Optional[AttendanceRule],
        runtime: Optional[Dict[str, Any]],
        target_date: date,
    ) -> Decimal:
        if rule is not None and AttendanceRecordService._infer_rule_type(rule, (runtime or {}).get("extra") or AttendanceRecordService._extra(rule)) == "free":
            required = AttendanceRecordService._free_required_hours(rule, (runtime or {}).get("extra") or AttendanceRecordService._extra(rule))
            return required if required is not None else Decimal("0")
        expected_in = (runtime or {}).get("expected_in") or (rule.clock_in_time if rule else None)
        expected_out = (runtime or {}).get("expected_out") or (rule.clock_out_time if rule else None)
        if expected_in is None or expected_out is None:
            fallback = Decimal(str(getattr(rule, "work_hours_per_day", 0) or 0))
            return fallback.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        cst = AttendanceRecordService._cst()
        start_dt = datetime.combine(target_date, expected_in, tzinfo=cst)
        end_dt = datetime.combine(target_date, expected_out, tzinfo=cst)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        hours = AttendanceRecordService._calculate_work_hours(
            start_dt,
            end_dt,
            target_date=target_date,
            rest_periods=AttendanceReportService._standard_rest_periods_from_runtime(runtime),
        )
        return (hours or Decimal("0")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _extract_minutes(anomaly_text: Optional[str], keyword: str) -> int:
        if not anomaly_text:
            return 0
        match = re.search(fr"{keyword}\s*(\d+)\s*分钟", str(anomaly_text))
        if not match:
            return 0
        try:
            return int(match.group(1))
        except ValueError:
            return 0

    @staticmethod
    def _date_label(day: date) -> str:
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return f"{day.strftime('%Y/%m/%d')} {weekdays[day.weekday()]}"

    @staticmethod
    def _dept_full_name(department_id: Optional[int], dept_map: Dict[int, Department]) -> str:
        if not department_id or department_id not in dept_map:
            return "-"
        names: list[str] = []
        cursor = department_id
        visited: set[int] = set()
        while cursor and cursor in dept_map and cursor not in visited:
            visited.add(cursor)
            node = dept_map[cursor]
            names.append(node.name)
            cursor = node.parent_id
        names.reverse()
        return "/".join(names) if names else "-"

    @staticmethod
    def _format_work_hours(value: Optional[Decimal]) -> str:
        if value is None:
            return "-"
        return f"{float(value):.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _gps_text(record: AttendanceRecord) -> str:
        lat = record.clock_in_gps_lat if record.clock_in_gps_lat is not None else record.clock_out_gps_lat
        lng = record.clock_in_gps_lng if record.clock_in_gps_lng is not None else record.clock_out_gps_lng
        if lat is None or lng is None:
            return "-"
        return f"{float(lat):.6f},{float(lng):.6f}"

    @staticmethod
    def _device_text(record: AttendanceRecord) -> str:
        source_text = AttendanceRecordService._source_to_text(getattr(record, "source", None))
        device_text = record.clock_out_device or record.clock_in_device or "-"
        wifi_text = record.clock_in_wifi_ssid or record.clock_out_wifi_ssid or "-"
        return f"{source_text}/{device_text}/{wifi_text}"

    @staticmethod
    def _header_title(title: str) -> str:
        text = str(title or "")
        return re.sub(r"([（(][^）)]*[）)])$", r"\n\1", text)

    @staticmethod
    def _excel_value(value: Any) -> Any:
        if value is None:
            return "-"
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned in {"", "--"}:
                return "-"
            return cleaned.replace("--", "-")
        return value

    @staticmethod
    def _excel_column_width(spec: dict[str, str]) -> float:
        key = spec.get("key", "")
        title = str(spec.get("title", ""))
        if key in {"date_label", "employee_name", "account"}:
            return {"date_label": 22, "employee_name": 16, "account": 18}[key]
        if key in {"department_name", "rule_name", "clock_place", "clock_device", "remark_content"}:
            return 28
        if "\n" in title or len(title) > 14:
            return 24
        if len(title) > 8:
            return 18
        return 14

    @staticmethod
    def _apply_wecom_report_sheet(
        ws: Any,
        *,
        title: str,
        period_text: str,
        specs: list[dict[str, str]],
        rows: list[dict],
        group_map: dict[str, Optional[str]],
        freeze_panes: str = "A5",
    ) -> None:
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        header_fill = PatternFill("solid", fgColor="F2F4F7")
        group_fill = PatternFill("solid", fgColor="EEF2F7")
        title_fill = PatternFill("solid", fgColor="FFFFFF")
        title_font = Font(color="111827", bold=True, size=13)
        meta_font = Font(color="64748B", size=10)
        header_font = Font(color="475569", bold=True, size=10)
        thin = Side(style="thin", color="DDE5F0")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)

        max_col = max(len(specs), 1)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
        ws.cell(row=1, column=1, value=title)
        ws.cell(row=1, column=1).fill = title_fill
        ws.cell(row=1, column=1).font = title_font
        ws.cell(row=1, column=1).alignment = center
        ws.cell(row=2, column=1, value=period_text)
        ws.cell(row=2, column=1).font = meta_font
        ws.cell(row=2, column=1).alignment = center
        ws.row_dimensions[1].height = 28
        ws.row_dimensions[2].height = 24
        ws.row_dimensions[3].height = 34
        ws.row_dimensions[4].height = 58

        col_idx = 1
        while col_idx <= len(specs):
            spec = specs[col_idx - 1]
            group = group_map.get(spec["key"])
            if group:
                start_col = col_idx
                while col_idx <= len(specs) and group_map.get(specs[col_idx - 1]["key"]) == group:
                    col_idx += 1
                end_col = col_idx - 1
                if end_col > start_col:
                    ws.merge_cells(start_row=3, start_column=start_col, end_row=3, end_column=end_col)
                group_cell = ws.cell(row=3, column=start_col, value=group)
                group_cell.fill = group_fill
                group_cell.font = header_font
                group_cell.alignment = center
                group_cell.border = border
                for merge_col in range(start_col, end_col + 1):
                    cell = ws.cell(row=3, column=merge_col)
                    cell.fill = group_fill
                    cell.font = header_font
                    cell.alignment = center
                    cell.border = border
                    leaf = ws.cell(row=4, column=merge_col, value=AttendanceReportService._header_title(specs[merge_col - 1]["title"]))
                    leaf.fill = header_fill
                    leaf.font = header_font
                    leaf.alignment = center
                    leaf.border = border
            else:
                ws.merge_cells(start_row=3, start_column=col_idx, end_row=4, end_column=col_idx)
                cell = ws.cell(row=3, column=col_idx, value=AttendanceReportService._header_title(spec["title"]))
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = center
                cell.border = border
                ws.cell(row=4, column=col_idx).border = border
                col_idx += 1

        keys = [item["key"] for item in specs]
        for row_idx, row in enumerate(rows, start=5):
            ws.row_dimensions[row_idx].height = 30
            for data_col_idx, key in enumerate(keys, start=1):
                cell = ws.cell(row=row_idx, column=data_col_idx, value=AttendanceReportService._excel_value(row.get(key)))
                cell.border = border
                cell.alignment = center

        for width_idx, spec in enumerate(specs, start=1):
            ws.column_dimensions[get_column_letter(width_idx)].width = AttendanceReportService._excel_column_width(spec)
        ws.freeze_panes = freeze_panes
        ws.sheet_view.showGridLines = False

    @staticmethod
    def _outside_effect_minutes(day_effects: Sequence[AttendanceEffect]) -> int:
        return sum(
            AttendanceReportService._effect_minutes(effect)
            for effect in day_effects
            if str(getattr(effect, "effect_type", "") or "") == "outside"
        )

    @staticmethod
    def _outside_effect_covers_day(day_effects: Sequence[AttendanceEffect], standard_minutes: int) -> bool:
        outside_minutes = AttendanceReportService._outside_effect_minutes(day_effects)
        return outside_minutes >= max(standard_minutes, 480)

    @staticmethod
    def _daily_status_with_effects(
        status_text: str,
        day_effects: Sequence[AttendanceEffect],
        leave_items: Sequence[dict[str, Any]],
        standard_minutes: int,
    ) -> str:
        effect_types = AttendanceReportService._effect_types(day_effects)
        leave_covers_day = AttendanceReportService._leave_items_cover_day(leave_items, standard_minutes)
        outside_covers_day = AttendanceReportService._outside_effect_covers_day(day_effects, standard_minutes)
        if leave_covers_day:
            return "请假"
        if "business_trip" in effect_types:
            return "出差"
        if outside_covers_day:
            return "正常（外出）"
        if "outside" in effect_types and "外出" not in str(status_text or ""):
            return "正常（外出）" if status_text in {"正常", "--", "-"} else f"{status_text}（外出）"
        return status_text

    @staticmethod
    def _daily_overtime_effect_metrics(
        day_effects: Sequence[AttendanceEffect],
        *,
        day_type: DayType,
    ) -> dict[str, float]:
        metrics = {
            "overtime_hours": 0.0,
            "weekday_ot_to_rest": 0.0,
            "weekday_ot_to_pay": 0.0,
            "weekend_ot_to_rest": 0.0,
            "weekend_ot_to_pay": 0.0,
            "holiday_ot_to_rest": 0.0,
            "holiday_ot_to_pay": 0.0,
        }
        for effect in day_effects:
            if str(getattr(effect, "effect_type", "") or "") != "overtime":
                continue
            minutes = AttendanceReportService._effect_minutes(effect)
            if minutes <= 0:
                continue
            hours = float(Decimal(minutes) / Decimal("60"))
            metrics["overtime_hours"] += hours
            prefix = "holiday" if day_type == DayType.holiday else "weekend" if day_type == DayType.weekend else "weekday"
            target = (
                "rest"
                if AttendanceReportService._effect_pay_policy(effect)
                in {"comp_time", "rest", "time_off", "lieu", "adjust_rest"}
                else "pay"
            )
            metrics[f"{prefix}_ot_to_{target}"] += hours
        return metrics

    @staticmethod
    def _daily_status_matches(row: dict[str, Any], status_filter: str) -> bool:
        normalized_status = str(status_filter or "").strip()
        valid_statuses = {"正常", "迟到", "早退", "缺卡", "旷工", "请假", "出差", "外出", "异常"}
        if not normalized_status or normalized_status not in valid_statuses:
            return True
        if normalized_status == "异常":
            return row.get("anomaly_total") not in {None, "--", 0, "0", "0次"}
        text = str(row.get("attendance_result") or "")
        if normalized_status == "正常":
            return "正常" in text and row.get("anomaly_total") in {None, "--", 0, "0", "0次"}
        return normalized_status in text or normalized_status in str(row.get("leave_application") or "")

    @staticmethod
    async def _load_daily_rows(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int],
        keyword: Optional[str],
        status: Optional[str],
        rule_id: Optional[int],
        include_recent_left: bool,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> tuple[list[dict], list[dict], int]:
        status_filter = str(status or "").strip()
        status = None
        requested_page = page
        requested_page_size = page_size
        page = None
        page_size = None

        stmt = (
            select(AttendanceRecord, Employee)
            .join(Employee, AttendanceRecord.employee_id == Employee.id)
            .where(
                and_(
                    AttendanceRecord.date >= start_date,
                    AttendanceRecord.date <= end_date,
                )
            )
        )
        count_stmt = (
            select(func.count())
            .select_from(AttendanceRecord)
            .join(Employee, AttendanceRecord.employee_id == Employee.id)
            .where(
                and_(
                    AttendanceRecord.date >= start_date,
                    AttendanceRecord.date <= end_date,
                )
            )
        )

        if rule_id is not None:
            stmt = stmt.join(
                ShiftSchedule,
                and_(
                    ShiftSchedule.employee_id == AttendanceRecord.employee_id,
                    ShiftSchedule.date == AttendanceRecord.date,
                ),
            ).where(ShiftSchedule.rule_id == rule_id)
            count_stmt = count_stmt.join(
                ShiftSchedule,
                and_(
                    ShiftSchedule.employee_id == AttendanceRecord.employee_id,
                    ShiftSchedule.date == AttendanceRecord.date,
                ),
            ).where(ShiftSchedule.rule_id == rule_id)

        if department_id is not None:
            stmt = stmt.where(Employee.department_id == department_id)
            count_stmt = count_stmt.where(Employee.department_id == department_id)

        if keyword:
            key = keyword.strip()
            if key:
                like = f"%{key}%"
                stmt = stmt.where(
                    or_(
                        Employee.name.ilike(like),
                        Employee.employee_no.ilike(like),
                        Employee.phone.ilike(like),
                    )
                )
                count_stmt = count_stmt.where(
                    or_(
                        Employee.name.ilike(like),
                        Employee.employee_no.ilike(like),
                        Employee.phone.ilike(like),
                    )
                )

        if not include_recent_left:
            stmt = stmt.where(Employee.status != EmployeeStatus.LEFT)
            count_stmt = count_stmt.where(Employee.status != EmployeeStatus.LEFT)

        if status:
            if status == "异常":
                abnormal_values = [
                    AttendanceStatus.late,
                    AttendanceStatus.early_leave,
                    AttendanceStatus.absent,
                    AttendanceStatus.missed_clock,
                ]
                stmt = stmt.where(
                    or_(
                        AttendanceRecord.status.in_(abnormal_values),
                        AttendanceRecord.anomaly_type.isnot(None),
                    )
                )
                count_stmt = count_stmt.where(
                    or_(
                        AttendanceRecord.status.in_(abnormal_values),
                        AttendanceRecord.anomaly_type.isnot(None),
                    )
                )
            else:
                try:
                    enum_status = AttendanceStatus(status)
                    stmt = stmt.where(AttendanceRecord.status == enum_status)
                    count_stmt = count_stmt.where(AttendanceRecord.status == enum_status)
                except ValueError:
                    pass

        total = int((await db.execute(count_stmt)).scalar() or 0)

        stmt = stmt.order_by(AttendanceRecord.date.desc(), AttendanceRecord.id.desc())
        if page is not None and page_size is not None:
            stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        rows = list((await db.execute(stmt)).all())
        record_row_keys = {(int(record.employee_id), record.date) for record, _ in rows}

        effect_stmt = (
            select(AttendanceEffect, Employee)
            .join(Employee, AttendanceEffect.employee_id == Employee.id)
            .where(
                and_(
                    AttendanceEffect.effect_status == "active",
                    AttendanceEffect.work_date >= start_date,
                    AttendanceEffect.work_date <= end_date,
                    AttendanceEffect.effect_type.in_(["leave", "business_trip", "outside", "overtime"]),
                )
            )
        )
        if rule_id is not None:
            effect_stmt = effect_stmt.join(
                ShiftSchedule,
                and_(
                    ShiftSchedule.employee_id == AttendanceEffect.employee_id,
                    ShiftSchedule.date == AttendanceEffect.work_date,
                ),
            ).where(ShiftSchedule.rule_id == rule_id)
        if department_id is not None:
            effect_stmt = effect_stmt.where(Employee.department_id == department_id)
        if keyword:
            key = keyword.strip()
            if key:
                like = f"%{key}%"
                effect_stmt = effect_stmt.where(
                    or_(
                        Employee.name.ilike(like),
                        Employee.employee_no.ilike(like),
                        Employee.phone.ilike(like),
                    )
                )
        if not include_recent_left:
            effect_stmt = effect_stmt.where(Employee.status != EmployeeStatus.LEFT)

        effect_rows = list((await db.execute(effect_stmt)).all())
        effects_by_emp_date: dict[tuple[int, date], list[AttendanceEffect]] = {}
        effect_employee_map: dict[int, Employee] = {}
        for effect, employee in effect_rows:
            if effect.work_date is None:
                continue
            emp_id = int(effect.employee_id)
            effect_employee_map[emp_id] = employee
            effects_by_emp_date.setdefault((emp_id, effect.work_date), []).append(effect)

        effect_only_keys = sorted(
            key for key in effects_by_emp_date.keys() if key not in record_row_keys
        )
        if not rows and not effect_only_keys:
            return [], [], total

        employee_ids = sorted(
            {int(emp.id) for _, emp in rows}
            | {int(emp_id) for emp_id in effect_employee_map.keys()}
        )
        dept_result = await db.execute(select(Department))
        dept_map: Dict[int, Department] = {d.id: d for d in dept_result.scalars().all()}

        shift_result = await db.execute(
            select(ShiftSchedule).where(
                and_(
                    ShiftSchedule.employee_id.in_(employee_ids),
                    ShiftSchedule.date >= start_date,
                    ShiftSchedule.date <= end_date,
                )
            )
        )
        shift_map: Dict[tuple[int, date], ShiftSchedule] = {
            (int(item.employee_id), item.date): item for item in shift_result.scalars().all()
        }
        rule_ids = {int(item.rule_id) for item in shift_map.values() if item.rule_id}
        rule_map: Dict[int, AttendanceRule] = {}
        if rule_ids:
            rule_result = await db.execute(select(AttendanceRule).where(AttendanceRule.id.in_(rule_ids)))
            rule_map = {r.id: r for r in rule_result.scalars().all()}

        overview_rows: list[dict] = []
        detail_rows: list[dict] = []

        punch_stat_map: dict[tuple[int, date], dict[str, Any]] = {}
        if employee_ids and (record_row_keys or effect_only_keys):
            punch_rows = list(
                (
                    await db.execute(
                        select(AttendancePunchTimeRecord).where(
                            and_(
                                AttendancePunchTimeRecord.employee_id.in_(employee_ids),
                                AttendancePunchTimeRecord.work_date >= start_date,
                                AttendancePunchTimeRecord.work_date <= end_date,
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            for punch in punch_rows:
                key = (int(punch.employee_id), punch.work_date)
                if key not in record_row_keys and key not in effect_only_keys:
                    continue
                stats = punch_stat_map.setdefault(
                    key,
                    {
                        "outside_count": 0,
                        "approve_punch_count": 0,
                        "outside_times": [],
                    },
                )
                if str(punch.source or "").lower() in {ClockSource.manual.value, "approval"}:
                    stats["approve_punch_count"] += 1
                if "outside:" in str(punch.photo_url or ""):
                    stats["outside_count"] += 1
                    local_dt = AttendanceRecordService._to_cst_datetime(punch.punch_time)
                    if local_dt is not None:
                        stats["outside_times"].append(local_dt)

        for record, employee in rows:
            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                int(record.employee_id),
                record.date,
                employee=employee,
            )
            AttendanceRecordService._attach_display_status(record, runtime)
            runtime_rule = runtime.get("rule") if isinstance(runtime, dict) else None
            shift = shift_map.get((int(record.employee_id), record.date))
            rule = rule_map.get(int(shift.rule_id)) if shift and shift.rule_id else runtime_rule

            standard_hours_decimal = AttendanceReportService._standard_work_hours_from_runtime(rule, runtime, record.date)
            standard_hours = float(standard_hours_decimal)
            standard_minutes = int(standard_hours * 60)
            record_key = (int(record.employee_id), record.date)
            day_effects = effects_by_emp_date.get(record_key, [])
            day_effect_types = AttendanceReportService._effect_types(day_effects)
            day_leave_items = AttendanceReportService._leave_effect_day_items(day_effects)
            leave_covers_day = AttendanceReportService._leave_items_cover_day(day_leave_items, standard_minutes)
            outside_covers_day = AttendanceReportService._outside_effect_covers_day(day_effects, standard_minutes)

            status_text = AttendanceReportService._daily_status_with_effects(
                AttendanceReportService._status_text(record),
                day_effects,
                day_leave_items,
                standard_minutes,
            )
            status_parts = AttendanceReportService._status_parts(status_text)
            anomaly_text = "" if leave_covers_day or "business_trip" in day_effect_types or outside_covers_day else str(record.anomaly_type or "")

            late_count = 1 if "迟到" in status_parts else 0
            early_count = 1 if "早退" in status_parts else 0
            absent_count = 1 if "旷工" in status_parts else 0
            missed_count = 1 if "缺卡" in status_parts else 0
            location_abnormal_count = 0 if "outside" in day_effect_types else (1 if record.is_mock_location else 0)
            device_abnormal_count = 1 if "设备异常" in anomaly_text else 0
            late_minutes = AttendanceReportService._extract_minutes(anomaly_text, "迟到")
            early_minutes = AttendanceReportService._extract_minutes(anomaly_text, "早退")

            absent_minutes = int(standard_hours * 60) if absent_count else 0
            anomaly_total = (
                late_count
                + early_count
                + absent_count
                + missed_count
                + location_abnormal_count
                + device_abnormal_count
                + (1 if "工时不足" in anomaly_text else 0)
            )

            punch_stats = punch_stat_map.get(record_key)
            clock_count = AttendanceReportService._attendance_clock_count(record)

            raw_outside_count = int(punch_stats["outside_count"]) if punch_stats else 0
            fallback_outside_count = int(
                "outside:" in str(record.clock_in_photo_url or "")
                or "outside:" in str(record.clock_out_photo_url or "")
            )
            effect_outside_minutes = AttendanceReportService._outside_effect_minutes(day_effects)
            effect_outside_count = sum(1 for effect in day_effects if str(getattr(effect, "effect_type", "") or "") == "outside")
            outside_count = raw_outside_count or fallback_outside_count or effect_outside_count
            outside_times = list(punch_stats.get("outside_times", [])) if punch_stats else []
            outside_earliest = "--"
            outside_latest = "--"
            if outside_times:
                outside_earliest = min(outside_times).strftime("%H:%M")
                outside_latest = max(outside_times).strftime("%H:%M")
            elif outside_count:
                outside_earliest = AttendanceReportService._format_clock(record.clock_in_time)
                outside_latest = AttendanceReportService._format_clock(record.clock_out_time)
            outside_hours = (
                AttendanceReportService._format_number(float(Decimal(effect_outside_minutes) / Decimal("60")), digits=1)
                if effect_outside_minutes > 0
                else AttendanceReportService._format_work_hours(record.work_hours) if outside_count else "--"
            )
            field_work_count = outside_count

            day_type = await WorkCalendarService.get_day_type(db, record.date, employee.location_id)
            effect_overtime_metrics = AttendanceReportService._daily_overtime_effect_metrics(day_effects, day_type=day_type)
            overtime_hours = float(effect_overtime_metrics["overtime_hours"] or record.overtime_hours or 0)
            extra_for_day = (runtime or {}).get("extra") or (AttendanceRecordService._extra(rule) if rule else {})
            if effect_overtime_metrics["overtime_hours"] > 0:
                weekday_ot_rest = effect_overtime_metrics["weekday_ot_to_rest"]
                weekday_ot_pay = effect_overtime_metrics["weekday_ot_to_pay"]
                weekend_ot_rest = effect_overtime_metrics["weekend_ot_to_rest"]
                weekend_ot_pay = effect_overtime_metrics["weekend_ot_to_pay"]
                holiday_ot_rest = effect_overtime_metrics["holiday_ot_to_rest"]
                holiday_ot_pay = effect_overtime_metrics["holiday_ot_to_pay"]
            else:
                comp_ot, pay_ot = AttendanceRecordService._split_overtime_settlement(
                    extra_for_day,
                    day_type,
                    Decimal(str(overtime_hours)),
                )
                comp_hours = float(comp_ot)
                pay_hours = float(pay_ot)
                weekday_ot_rest = comp_hours if comp_hours > 0 and day_type in (DayType.workday, DayType.special_workday) else 0.0
                weekday_ot_pay = pay_hours if pay_hours > 0 and day_type in (DayType.workday, DayType.special_workday) else 0.0
                weekend_ot_rest = comp_hours if comp_hours > 0 and day_type == DayType.weekend else 0.0
                weekend_ot_pay = pay_hours if pay_hours > 0 and day_type == DayType.weekend else 0.0
                holiday_ot_rest = comp_hours if comp_hours > 0 and day_type == DayType.holiday else 0.0
                holiday_ot_pay = pay_hours if pay_hours > 0 and day_type == DayType.holiday else 0.0

            expected_in = rule.clock_in_time.strftime("%H:%M") if rule and rule.clock_in_time else "--"
            expected_out = rule.clock_out_time.strftime("%H:%M") if rule and rule.clock_out_time else "--"
            leave_apply = (
                "请假" if day_leave_items or "请假" in status_parts
                else "出差" if "business_trip" in day_effect_types or "出差" in status_parts
                else "外出" if "outside" in day_effect_types
                else "--"
            )
            raw_approve_punch_count = int(punch_stats["approve_punch_count"]) if punch_stats else 0
            record_source = getattr(record.source, "value", record.source)
            approve_punch_count = raw_approve_punch_count or int(record_source == ClockSource.manual.value)

            overview_rows.append(
                {
                    "_daily_key": f"{int(record.employee_id)}:{record.date.isoformat()}",
                    "_sort_date": record.date,
                    "_sort_id": int(getattr(record, "id", 0) or 0),
                    "date_label": AttendanceReportService._date_label(record.date),
                    "employee_name": employee.name,
                    "account": employee.employee_no or employee.phone or "--",
                    "department_name": AttendanceReportService._dept_full_name(employee.department_id, dept_map),
                    "position": employee.position or "--",
                    "rule_name": rule.name if rule else "--",
                    "shift_name": AttendanceReportService._format_shift_text(rule),
                    "earliest_clock": AttendanceReportService._format_clock(record.clock_in_time),
                    "latest_clock": AttendanceReportService._format_clock(record.clock_out_time),
                    "clock_count": clock_count,
                    "standard_work_hours": str(int(standard_hours_decimal)),
                    "actual_work_hours": AttendanceReportService._format_work_hours(record.work_hours),
                    "leave_application": leave_apply,
                    "attendance_result": status_text,
                    "anomaly_total": anomaly_total or "--",
                    "late_count": late_count or "--",
                    "late_minutes": late_minutes or "--",
                    "early_count": early_count or "--",
                    "early_minutes": early_minutes or "--",
                    "absent_count": absent_count or "--",
                    "absent_minutes": absent_minutes or "--",
                    "missed_count": missed_count or "--",
                    "location_abnormal_count": location_abnormal_count or "--",
                    "device_abnormal_count": device_abnormal_count or "--",
                    "outside_clock_count": outside_count or "--",
                    "outside_earliest": outside_earliest,
                    "outside_latest": outside_latest,
                    "overtime_status": "有加班" if overtime_hours > 0 else "--",
                    "overtime_hours": round(overtime_hours, 2) if overtime_hours > 0 else "--",
                    "weekday_ot_to_rest": round(weekday_ot_rest, 2) if weekday_ot_rest else "--",
                    "weekday_ot_to_pay": round(weekday_ot_pay, 2) if weekday_ot_pay else "--",
                    "weekend_ot_to_rest": round(weekend_ot_rest, 2) if weekend_ot_rest else "--",
                    "weekend_ot_to_pay": round(weekend_ot_pay, 2) if weekend_ot_pay else "--",
                    "holiday_ot_to_rest": round(holiday_ot_rest, 2) if holiday_ot_rest else "--",
                    "holiday_ot_to_pay": round(holiday_ot_pay, 2) if holiday_ot_pay else "--",
                    "approve_punch_count": approve_punch_count or "--",
                    "field_work_count": field_work_count or "--",
                    "outside_hours": outside_hours,
                }
            )

            detail_base = {
                "_daily_key": f"{int(record.employee_id)}:{record.date.isoformat()}",
                "_sort_date": record.date,
                "_sort_id": int(getattr(record, "id", 0) or 0),
                "date_label": AttendanceReportService._date_label(record.date),
                "employee_name": employee.name,
                "account": employee.employee_no or employee.phone or "--",
                "department_name": AttendanceReportService._dept_full_name(employee.department_id, dept_map),
                "position": employee.position or "--",
                "rule_name": rule.name if rule else "--",
                "clock_place": AttendanceReportService._gps_text(record),
                "clock_device": AttendanceReportService._device_text(record),
                "remark_content": anomaly_text or "--",
                "remark_image": "--",
                "leave_application": leave_apply,
            }

            check_in_status = "正常"
            if leave_covers_day or "business_trip" in day_effect_types or outside_covers_day:
                check_in_status = status_text
            elif record.clock_in_time is None:
                check_in_status = f"缺卡（{expected_in}）" if expected_in != "--" else "缺卡"
            elif "迟到" in status_parts:
                check_in_status = anomaly_text if "迟到" in anomaly_text else "迟到"
            detail_rows.append(
                {
                    **detail_base,
                    "clock_type": "上班",
                    "expected_clock_time": expected_in,
                    "actual_clock_time": AttendanceReportService._format_clock_or_empty(record.clock_in_time),
                    "clock_status": check_in_status,
                    "remark_image": record.clock_in_photo_url or "--",
                }
            )

            check_out_status = "正常"
            if leave_covers_day or "business_trip" in day_effect_types or outside_covers_day:
                check_out_status = status_text
            elif record.clock_out_time is None:
                check_out_status = f"缺卡（{expected_out}）" if expected_out != "--" else "缺卡"
            elif "早退" in status_parts:
                check_out_status = anomaly_text if "早退" in anomaly_text else "早退"
            detail_rows.append(
                {
                    **detail_base,
                    "clock_type": "下班",
                    "expected_clock_time": expected_out,
                    "actual_clock_time": AttendanceReportService._format_clock_or_empty(record.clock_out_time),
                    "clock_status": check_out_status,
                    "remark_image": record.clock_out_photo_url or "--",
                }
            )

        for emp_id, work_date in effect_only_keys:
            employee = effect_employee_map.get(emp_id)
            if employee is None:
                continue
            runtime = await AttendanceRecordService._get_rule_runtime(
                db,
                emp_id,
                work_date,
                employee=employee,
            )
            runtime_rule = runtime.get("rule") if isinstance(runtime, dict) else None
            shift = shift_map.get((emp_id, work_date))
            rule = rule_map.get(int(shift.rule_id)) if shift and shift.rule_id else runtime_rule
            standard_hours_decimal = AttendanceReportService._standard_work_hours_from_runtime(rule, runtime, work_date)
            standard_hours = float(standard_hours_decimal)
            standard_minutes = int(standard_hours * 60)
            day_effects = effects_by_emp_date.get((emp_id, work_date), [])
            day_effect_types = AttendanceReportService._effect_types(day_effects)
            day_leave_items = AttendanceReportService._leave_effect_day_items(day_effects)
            leave_covers_day = AttendanceReportService._leave_items_cover_day(day_leave_items, standard_minutes)
            outside_covers_day = AttendanceReportService._outside_effect_covers_day(day_effects, standard_minutes)
            expected_work_day = await MonthSummaryService._expected_work_day(
                db,
                emp_id,
                work_date,
                getattr(employee, "location_id", None),
                employee=employee,
            )
            if leave_covers_day:
                status_text = "请假"
            elif "business_trip" in day_effect_types:
                status_text = "出差"
            elif outside_covers_day:
                status_text = "正常（外出）"
            elif expected_work_day:
                status_text = "旷工"
            else:
                status_text = "正常"

            absent_count = 1 if status_text == "旷工" else 0
            absent_minutes = int(standard_hours * 60) if absent_count else 0
            anomaly_total = absent_count
            effect_outside_minutes = AttendanceReportService._outside_effect_minutes(day_effects)
            effect_outside_count = sum(1 for effect in day_effects if str(getattr(effect, "effect_type", "") or "") == "outside")
            outside_hours = (
                AttendanceReportService._format_number(float(Decimal(effect_outside_minutes) / Decimal("60")), digits=1)
                if effect_outside_minutes > 0
                else "--"
            )
            day_type = await WorkCalendarService.get_day_type(db, work_date, employee.location_id)
            effect_overtime_metrics = AttendanceReportService._daily_overtime_effect_metrics(day_effects, day_type=day_type)
            overtime_hours = float(effect_overtime_metrics["overtime_hours"])
            expected_in = rule.clock_in_time.strftime("%H:%M") if rule and rule.clock_in_time else "--"
            expected_out = rule.clock_out_time.strftime("%H:%M") if rule and rule.clock_out_time else "--"
            leave_apply = (
                "请假" if day_leave_items
                else "出差" if "business_trip" in day_effect_types
                else "外出" if "outside" in day_effect_types
                else "--"
            )
            daily_key = f"{emp_id}:{work_date.isoformat()}"
            overview_rows.append(
                {
                    "_daily_key": daily_key,
                    "_sort_date": work_date,
                    "_sort_id": 0,
                    "date_label": AttendanceReportService._date_label(work_date),
                    "employee_name": employee.name,
                    "account": employee.employee_no or employee.phone or "--",
                    "department_name": AttendanceReportService._dept_full_name(employee.department_id, dept_map),
                    "position": employee.position or "--",
                    "rule_name": rule.name if rule else "--",
                    "shift_name": AttendanceReportService._format_shift_text(rule),
                    "earliest_clock": "--",
                    "latest_clock": "--",
                    "clock_count": 0,
                    "standard_work_hours": str(int(standard_hours_decimal)),
                    "actual_work_hours": "-",
                    "leave_application": leave_apply,
                    "attendance_result": status_text,
                    "anomaly_total": anomaly_total or "--",
                    "late_count": "--",
                    "late_minutes": "--",
                    "early_count": "--",
                    "early_minutes": "--",
                    "absent_count": absent_count or "--",
                    "absent_minutes": absent_minutes or "--",
                    "missed_count": "--",
                    "location_abnormal_count": "--",
                    "device_abnormal_count": "--",
                    "outside_clock_count": effect_outside_count or "--",
                    "outside_earliest": "--",
                    "outside_latest": "--",
                    "overtime_status": "有加班" if overtime_hours > 0 else "--",
                    "overtime_hours": round(overtime_hours, 2) if overtime_hours > 0 else "--",
                    "weekday_ot_to_rest": round(effect_overtime_metrics["weekday_ot_to_rest"], 2) or "--",
                    "weekday_ot_to_pay": round(effect_overtime_metrics["weekday_ot_to_pay"], 2) or "--",
                    "weekend_ot_to_rest": round(effect_overtime_metrics["weekend_ot_to_rest"], 2) or "--",
                    "weekend_ot_to_pay": round(effect_overtime_metrics["weekend_ot_to_pay"], 2) or "--",
                    "holiday_ot_to_rest": round(effect_overtime_metrics["holiday_ot_to_rest"], 2) or "--",
                    "holiday_ot_to_pay": round(effect_overtime_metrics["holiday_ot_to_pay"], 2) or "--",
                    "approve_punch_count": "--",
                    "field_work_count": effect_outside_count or "--",
                    "outside_hours": outside_hours,
                }
            )

            detail_base = {
                "_daily_key": daily_key,
                "_sort_date": work_date,
                "_sort_id": 0,
                "date_label": AttendanceReportService._date_label(work_date),
                "employee_name": employee.name,
                "account": employee.employee_no or employee.phone or "--",
                "department_name": AttendanceReportService._dept_full_name(employee.department_id, dept_map),
                "position": employee.position or "--",
                "rule_name": rule.name if rule else "--",
                "clock_place": "-",
                "clock_device": "-",
                "remark_content": "; ".join(AttendanceReportService._day_effect_annotations(day_effects, day_leave_items)) or "--",
                "remark_image": "--",
                "leave_application": leave_apply,
            }
            for clock_type, expected_clock in (("上班", expected_in), ("下班", expected_out)):
                detail_rows.append(
                    {
                        **detail_base,
                        "clock_type": clock_type,
                        "expected_clock_time": expected_clock,
                        "actual_clock_time": "-",
                        "clock_status": status_text,
                        "remark_image": "--",
                    }
                )

        if status_filter:
            allowed_keys = {
                str(row.get("_daily_key"))
                for row in overview_rows
                if AttendanceReportService._daily_status_matches(row, status_filter)
            }
            overview_rows = [row for row in overview_rows if str(row.get("_daily_key")) in allowed_keys]
            detail_rows = [row for row in detail_rows if str(row.get("_daily_key")) in allowed_keys]

        overview_rows.sort(key=lambda row: (row.get("_sort_date"), row.get("_sort_id", 0)), reverse=True)
        ordered_keys = [str(row.get("_daily_key")) for row in overview_rows]
        key_order = {key: index for index, key in enumerate(ordered_keys)}
        detail_rows.sort(
            key=lambda row: (
                key_order.get(str(row.get("_daily_key")), 10**9),
                0 if row.get("clock_type") == "上班" else 1,
            )
        )

        total = len(overview_rows)
        if requested_page is not None and requested_page_size is not None:
            start = (requested_page - 1) * requested_page_size
            end = start + requested_page_size
            page_keys = {str(row.get("_daily_key")) for row in overview_rows[start:end]}
            overview_rows = overview_rows[start:end]
            detail_rows = [row for row in detail_rows if str(row.get("_daily_key")) in page_keys]

        hidden_keys = {"_daily_key", "_sort_date", "_sort_id"}
        overview_rows = [
            {key: value for key, value in row.items() if key not in hidden_keys}
            for row in overview_rows
        ]
        detail_rows = [
            {key: value for key, value in row.items() if key not in hidden_keys}
            for row in detail_rows
        ]
        return overview_rows, detail_rows, total

    @staticmethod
    async def get_daily_report_data(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        overview_rows, detail_rows, total = await AttendanceReportService._load_daily_rows(
            db,
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
            keyword=keyword,
            status=status,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
            page=page,
            page_size=page_size,
        )
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "page": page,
            "page_size": page_size,
            "total": total,
            "overview_columns": AttendanceReportService.DAILY_OVERVIEW_COLUMN_SPECS,
            "detail_columns": AttendanceReportService.DAILY_DETAIL_COLUMN_SPECS,
            "overview_rows": overview_rows,
            "detail_rows": detail_rows,
        }

    @staticmethod
    async def export_daily_report(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
        overview_columns: Optional[list[str]] = None,
        detail_columns: Optional[list[str]] = None,
    ) -> bytes:
        import openpyxl

        overview_rows, detail_rows, _ = await AttendanceReportService._load_daily_rows(
            db,
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
            keyword=keyword,
            status=status,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
            page=None,
            page_size=None,
        )

        wb = openpyxl.Workbook()
        ws_overview = wb.active
        ws_overview.title = "概况统计与打卡明细"
        ws_detail = wb.create_sheet("打卡详情")
        overview_specs = AttendanceReportService._resolve_column_specs(
            AttendanceReportService.DAILY_OVERVIEW_COLUMN_SPECS,
            overview_columns,
        )
        detail_specs = AttendanceReportService._resolve_column_specs(
            AttendanceReportService.DAILY_DETAIL_COLUMN_SPECS,
            detail_columns,
        )

        period_text = f"统计时间:{start_date.strftime('%Y/%m/%d')} - {end_date.strftime('%Y/%m/%d')}"
        AttendanceReportService._apply_wecom_report_sheet(
            ws_overview,
            title="日报概况统计",
            period_text=period_text,
            specs=overview_specs,
            rows=overview_rows,
            group_map=AttendanceReportService.DAILY_OVERVIEW_EXPORT_GROUPS,
        )
        AttendanceReportService._apply_wecom_report_sheet(
            ws_detail,
            title="日报打卡明细",
            period_text=period_text,
            specs=detail_specs,
            rows=detail_rows,
            group_map=AttendanceReportService.DAILY_DETAIL_EXPORT_GROUPS,
        )

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @staticmethod
    def _iter_dates(start_date: date, end_date: date) -> list[date]:
        days: list[date] = []
        cursor = start_date
        while cursor <= end_date:
            days.append(cursor)
            cursor += timedelta(days=1)
        return days

    @staticmethod
    def _month_day_column_specs(start_date: date, end_date: date) -> list[dict[str, str]]:
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return [
            {
                "key": f"day_{day.strftime('%Y%m%d')}",
                "title": f"{day.day}\n{weekdays[day.weekday()]}",
                "date": day.isoformat(),
            }
            for day in AttendanceReportService._iter_dates(start_date, end_date)
        ]

    @staticmethod
    def _format_number(value: float | int, digits: int = 1) -> str:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")

    @staticmethod
    def _format_metric(value: float | int, unit: str = "", zero_as_dash: bool = False, digits: int = 1) -> str:
        if zero_as_dash and not value:
            return "-"
        return f"{AttendanceReportService._format_number(value, digits)}{unit}"

    @staticmethod
    def _format_decimal_amount(value: Decimal | float | int, digits: int = 1) -> str:
        return AttendanceReportService._format_number(float(value or 0), digits)

    @staticmethod
    def _effect_payload(effect: AttendanceEffect) -> dict[str, Any]:
        payload = getattr(effect, "payload_json", None)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _effect_detail(effect: AttendanceEffect) -> dict[str, Any]:
        detail = AttendanceReportService._effect_payload(effect).get("effect_detail")
        return detail if isinstance(detail, dict) else {}

    @staticmethod
    def _effect_form_data(effect: AttendanceEffect) -> dict[str, Any]:
        event = AttendanceReportService._effect_payload(effect).get("approval_event")
        if not isinstance(event, dict):
            return {}
        form_data = event.get("form_data")
        return form_data if isinstance(form_data, dict) else {}

    @staticmethod
    def _effect_minutes(effect: AttendanceEffect) -> int:
        try:
            minutes = int(getattr(effect, "minutes", None) or 0)
        except (TypeError, ValueError):
            minutes = 0
        if minutes > 0:
            return minutes
        start_at = getattr(effect, "start_at", None)
        end_at = getattr(effect, "end_at", None)
        if not (start_at and end_at):
            return 0
        try:
            return max(0, int((end_at - start_at).total_seconds() // 60))
        except Exception:
            return 0

    @staticmethod
    def _leave_label_by_code(code: str) -> str:
        return {
            "annual": "年假",
            "marriage": "婚假",
            "maternity": "产休假",
            "paternity": "陪产假",
            "sick": "病假",
            "comp_time": "调休",
            "personal": "事假",
            "sick_long": "病假（长期）",
            "prenatal_check": "产检假",
            "bereavement": "丧假",
            "sick_general": "病假（通用）",
        }.get(str(code or "").lower(), "请假")

    @staticmethod
    def _leave_code_from_effect(effect: AttendanceEffect) -> str:
        detail = AttendanceReportService._effect_detail(effect)
        form_data = AttendanceReportService._effect_form_data(effect)
        for value in (
            detail.get("leave_type_code"),
            form_data.get("leave_type_code"),
            detail.get("leave_type_name"),
            form_data.get("leave_type_name"),
            form_data.get("leave_type"),
        ):
            raw = str(value or "").strip()
            if not raw:
                continue
            lowered = raw.lower()
            if lowered in AttendanceReportService.MONTHLY_LEAVE_FIELD_BY_CODE:
                return lowered
            mapped = AttendanceReportService.LEAVE_TYPE_CODE_BY_NAME.get(raw)
            if mapped:
                return mapped
        return ""

    @staticmethod
    def _effect_label(effect: AttendanceEffect) -> str:
        effect_type = str(getattr(effect, "effect_type", "") or "")
        detail = AttendanceReportService._effect_detail(effect)
        form_data = AttendanceReportService._effect_form_data(effect)
        if effect_type == "leave":
            code = AttendanceReportService._leave_code_from_effect(effect)
            return str(detail.get("leave_type_name") or form_data.get("leave_type_name") or AttendanceReportService._leave_label_by_code(str(code or "")))
        return {
            "punch_correction": "补卡",
            "business_trip": "出差",
            "outside": "外出",
            "overtime": "加班",
        }.get(effect_type, effect_type or "审批")

    @staticmethod
    def _format_effect_period(effect: AttendanceEffect) -> str:
        effect_type = str(getattr(effect, "effect_type", "") or "")

        def _effect_time(value: Optional[datetime]) -> Optional[datetime]:
            if value is None:
                return None
            if value.tzinfo is None and effect_type in {"leave", "business_trip", "outside", "overtime"}:
                return value.replace(tzinfo=AttendanceRecordService._cst())
            return AttendanceReportService._to_cst(value)

        start_at = _effect_time(getattr(effect, "start_at", None))
        end_at = _effect_time(getattr(effect, "end_at", None))

        def _fmt(dt: datetime) -> str:
            return f"{dt.month}/{dt.day} {dt.strftime('%H:%M')}"

        if start_at and end_at and start_at != end_at:
            return f"{_fmt(start_at)} - {_fmt(end_at)}"
        if start_at:
            return _fmt(start_at)
        work_date = getattr(effect, "work_date", None)
        if isinstance(work_date, date):
            return f"{work_date.month}/{work_date.day}"
        return ""

    @staticmethod
    def _monthly_effect_annotation(effect: AttendanceEffect) -> str:
        label = AttendanceReportService._effect_label(effect)
        effect_type = str(getattr(effect, "effect_type", "") or "")
        minutes = AttendanceReportService._effect_minutes(effect)
        amount_text = ""
        if effect_type in {"outside", "overtime", "leave"} and minutes > 0:
            amount_text = f"{AttendanceReportService._format_decimal_amount(Decimal(minutes) / Decimal('60'))}小时"
        elif effect_type == "business_trip":
            days = Decimal(minutes or 480) / Decimal("480")
            amount_text = f"{AttendanceReportService._format_decimal_amount(max(days, Decimal('1')))}天"
        period_text = AttendanceReportService._format_effect_period(effect)
        text = f"{label}{amount_text}"
        return f"{text}（{period_text}）" if period_text else text

    @staticmethod
    def _effect_pay_policy(effect: AttendanceEffect) -> str:
        detail = AttendanceReportService._effect_detail(effect)
        form_data = AttendanceReportService._effect_form_data(effect)
        return str(
            getattr(effect, "pay_policy", None)
            or detail.get("settlement")
            or detail.get("pay_policy")
            or form_data.get("settlement")
            or form_data.get("pay_policy")
            or "overtime_pay"
        )

    @staticmethod
    def _empty_monthly_effect_metrics() -> dict[str, Any]:
        return {
            "punch_correction_count": 0,
            "approve_punch_count": 0,
            "field_work_count": 0,
            "outside_hours": 0.0,
            "business_trip_dates": set(),
            "overtime_hours": 0.0,
            "weekday_ot_hours": 0.0,
            "weekday_ot_to_rest": 0.0,
            "weekday_ot_to_pay": 0.0,
            "weekend_ot_hours": 0.0,
            "weekend_ot_to_rest": 0.0,
            "weekend_ot_to_pay": 0.0,
            "holiday_ot_hours": 0.0,
            "holiday_ot_to_rest": 0.0,
            "holiday_ot_to_pay": 0.0,
        }

    @staticmethod
    def _add_overtime_metric(
        metrics: dict[str, Any],
        *,
        day_type: DayType,
        hours: float,
        pay_policy: str,
    ) -> None:
        if hours <= 0:
            return
        metrics["overtime_hours"] = float(metrics.get("overtime_hours", 0.0)) + hours
        if day_type == DayType.holiday:
            prefix = "holiday"
        elif day_type == DayType.weekend:
            prefix = "weekend"
        else:
            prefix = "weekday"
        metrics[f"{prefix}_ot_hours"] = float(metrics.get(f"{prefix}_ot_hours", 0.0)) + hours
        target = "rest" if pay_policy in {"comp_time", "rest", "time_off", "lieu", "adjust_rest"} else "pay"
        key = f"{prefix}_ot_to_{target}"
        metrics[key] = float(metrics.get(key, 0.0)) + hours

    @staticmethod
    async def _load_monthly_effect_context(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        employees: Sequence[Employee],
    ) -> tuple[dict[int, dict[str, Any]], dict[tuple[int, date], list[AttendanceEffect]]]:
        employee_map = {int(emp.id): emp for emp in employees}
        if not employee_map:
            return {}, {}
        result = await db.execute(
            select(AttendanceEffect)
            .where(
                and_(
                    AttendanceEffect.employee_id.in_(list(employee_map.keys())),
                    AttendanceEffect.effect_status == "active",
                    AttendanceEffect.work_date >= start_date,
                    AttendanceEffect.work_date <= end_date,
                    AttendanceEffect.effect_type.in_(
                        ["punch_correction", "outside", "business_trip", "overtime", "leave"]
                    ),
                )
            )
            .order_by(AttendanceEffect.employee_id, AttendanceEffect.work_date, AttendanceEffect.id)
        )
        effects = list(result.scalars().all())
        metrics_by_emp: dict[int, dict[str, Any]] = {}
        effects_by_emp_date: dict[tuple[int, date], list[AttendanceEffect]] = {}
        day_type_cache: dict[tuple[Optional[int], date], DayType] = {}

        async def _day_type(emp_id: int, work_date: date) -> DayType:
            employee = employee_map.get(emp_id)
            location_id = getattr(employee, "location_id", None) if employee else None
            key = (location_id, work_date)
            if key not in day_type_cache:
                day_type_cache[key] = await WorkCalendarService.get_day_type(db, work_date, location_id)
            return day_type_cache[key]

        for effect in effects:
            if effect.work_date is None:
                continue
            emp_id = int(effect.employee_id)
            metrics = metrics_by_emp.setdefault(emp_id, AttendanceReportService._empty_monthly_effect_metrics())
            effects_by_emp_date.setdefault((emp_id, effect.work_date), []).append(effect)
            effect_type = str(effect.effect_type or "")
            minutes = AttendanceReportService._effect_minutes(effect)
            if effect_type == "punch_correction":
                metrics["punch_correction_count"] += 1
                metrics["approve_punch_count"] += 1
            elif effect_type == "outside":
                metrics["field_work_count"] += 1
                metrics["outside_hours"] += float(Decimal(minutes) / Decimal("60")) if minutes > 0 else 0.0
            elif effect_type == "business_trip":
                metrics["business_trip_dates"].add(effect.work_date)
            elif effect_type == "overtime" and minutes > 0:
                AttendanceReportService._add_overtime_metric(
                    metrics,
                    day_type=await _day_type(emp_id, effect.work_date),
                    hours=float(Decimal(minutes) / Decimal("60")),
                    pay_policy=AttendanceReportService._effect_pay_policy(effect),
                )
        return metrics_by_emp, effects_by_emp_date

    @staticmethod
    def _leave_day_amount(segment: dict[str, Any], target_date: date) -> Decimal:
        try:
            start = date.fromisoformat(str(segment.get("start_date")))
            end = date.fromisoformat(str(segment.get("end_date")))
        except (TypeError, ValueError):
            return Decimal("0")
        if target_date < start or target_date > end:
            return Decimal("0")
        start_half = str(segment.get("start_half") or "am").lower()
        end_half = str(segment.get("end_half") or "pm").lower()
        if start == end:
            if start_half == end_half:
                return Decimal("0.5")
            if start_half == "pm" and end_half == "am":
                return Decimal("0")
            return Decimal("1")
        amount = Decimal("1")
        if target_date == start and start_half == "pm":
            amount -= Decimal("0.5")
        if target_date == end and end_half == "am":
            amount -= Decimal("0.5")
        return max(amount, Decimal("0"))

    @staticmethod
    def _leave_segment_period_text(segment: dict[str, Any]) -> str:
        try:
            start = date.fromisoformat(str(segment.get("start_date")))
            end = date.fromisoformat(str(segment.get("end_date")))
        except (TypeError, ValueError):
            return ""
        if start == end:
            return f"{start.month}/{start.day}"
        return f"{start.month}/{start.day} - {end.month}/{end.day}"

    @staticmethod
    def _build_monthly_leave_day_items(
        segments: Sequence[dict[str, Any]],
        start_date: date,
        end_date: date,
        employee_ids: set[int],
    ) -> dict[tuple[int, date], list[dict[str, Any]]]:
        items: dict[tuple[int, date], list[dict[str, Any]]] = {}
        for segment in segments:
            try:
                emp_id = int(segment.get("employee_id") or 0)
                original_start = date.fromisoformat(str(segment.get("start_date")))
                original_end = date.fromisoformat(str(segment.get("end_date")))
            except (TypeError, ValueError):
                continue
            if emp_id not in employee_ids:
                continue
            clipped_start = max(original_start, start_date)
            clipped_end = min(original_end, end_date)
            if clipped_start > clipped_end:
                continue
            code = str(segment.get("leave_type_code") or "").lower()
            field = AttendanceReportService.MONTHLY_LEAVE_FIELD_BY_CODE.get(code)
            label = str(segment.get("leave_type_name") or AttendanceReportService._leave_label_by_code(code))
            hours_per_day = Decimal(str(segment.get("hours_per_day") or 8))
            cursor = clipped_start
            while cursor <= clipped_end:
                days = AttendanceReportService._leave_day_amount(segment, cursor)
                if days > 0:
                    use_hours = field in AttendanceReportService.MONTHLY_LEAVE_HOUR_FIELDS
                    amount = days * hours_per_day if use_hours else days
                    unit = "小时" if use_hours else "天"
                    period = AttendanceReportService._leave_segment_period_text(segment)
                    annotation = f"{label}{AttendanceReportService._format_decimal_amount(amount)}{unit}"
                    if period:
                        annotation = f"{annotation}（{period}）"
                    items.setdefault((emp_id, cursor), []).append(
                        {
                            "label": label,
                            "days": days,
                            "minutes": int(days * hours_per_day * Decimal("60")),
                            "annotation": annotation,
                            "code": code,
                        }
                    )
                cursor += timedelta(days=1)
        return items

    @staticmethod
    def _leave_effect_day_item(effect: AttendanceEffect) -> Optional[dict[str, Any]]:
        if str(getattr(effect, "effect_type", "") or "") != "leave":
            return None
        minutes = AttendanceReportService._effect_minutes(effect)
        if minutes <= 0:
            minutes = 480
        days = Decimal(minutes) / Decimal("480")
        code = AttendanceReportService._leave_code_from_effect(effect)
        label = AttendanceReportService._effect_label(effect)
        field = AttendanceReportService.MONTHLY_LEAVE_FIELD_BY_CODE.get(code)
        use_hours = field in AttendanceReportService.MONTHLY_LEAVE_HOUR_FIELDS
        amount = days * Decimal("8") if use_hours else days
        unit = "小时" if use_hours else "天"
        period = AttendanceReportService._format_effect_period(effect)
        annotation = f"{label}{AttendanceReportService._format_decimal_amount(amount)}{unit}"
        if period:
            annotation = f"{annotation}（{period}）"
        return {
            "label": label,
            "days": days,
            "minutes": minutes,
            "annotation": annotation,
            "code": code,
        }

    @staticmethod
    def _leave_effect_day_items(day_effects: Sequence[AttendanceEffect]) -> list[dict[str, Any]]:
        return [
            item
            for effect in day_effects
            if (item := AttendanceReportService._leave_effect_day_item(effect)) is not None
        ]

    @staticmethod
    def _leave_items_cover_day(leave_items: Sequence[dict[str, Any]], standard_minutes: int) -> bool:
        if not leave_items:
            return False
        if any(Decimal(str(item.get("days") or 0)) >= Decimal("1") for item in leave_items):
            return True
        total_minutes = sum(int(item.get("minutes") or 0) for item in leave_items)
        return total_minutes >= max(standard_minutes, 480)

    @staticmethod
    def _effect_types(day_effects: Sequence[AttendanceEffect]) -> set[str]:
        return {str(getattr(effect, "effect_type", "") or "") for effect in day_effects}

    @staticmethod
    def _day_effect_annotations(
        day_effects: Sequence[AttendanceEffect],
        leave_items: Sequence[dict[str, Any]],
    ) -> list[str]:
        annotations: list[str] = [str(item["annotation"]) for item in leave_items if item.get("annotation")]
        annotations.extend(
            AttendanceReportService._monthly_effect_annotation(effect)
            for effect in day_effects
            if str(getattr(effect, "effect_type", "") or "") != "leave"
        )
        return [item for item in annotations if item]

    @staticmethod
    def _leave_segment_overlap_days(segment: dict[str, Any], start_date: date, end_date: date) -> Decimal:
        try:
            original_start = date.fromisoformat(str(segment.get("start_date")))
            original_end = date.fromisoformat(str(segment.get("end_date")))
        except (TypeError, ValueError):
            return Decimal(str(segment.get("days") or 0))

        clipped_start = max(original_start, start_date)
        clipped_end = min(original_end, end_date)
        if clipped_start > clipped_end:
            return Decimal("0")

        start_half = str(segment.get("start_half") or "am").lower() if clipped_start == original_start else "am"
        end_half = str(segment.get("end_half") or "pm").lower() if clipped_end == original_end else "pm"
        if clipped_start == clipped_end:
            if start_half == end_half:
                return Decimal("0.5")
            if start_half == "pm" and end_half == "am":
                return Decimal("0")
            return Decimal("1")

        days = Decimal(str((clipped_end - clipped_start).days + 1))
        if start_half == "pm":
            days -= Decimal("0.5")
        if end_half == "am":
            days -= Decimal("0.5")
        return max(days, Decimal("0"))

    @staticmethod
    def _aggregate_monthly_leave_metrics(
        segments: Sequence[dict[str, Any]],
        start_date: date,
        end_date: date,
        employee_ids: set[int],
    ) -> dict[int, dict[str, Decimal]]:
        metrics: dict[int, dict[str, Decimal]] = {}
        for segment in segments:
            try:
                emp_id = int(segment.get("employee_id") or 0)
            except (TypeError, ValueError):
                continue
            if emp_id not in employee_ids:
                continue
            field = AttendanceReportService.MONTHLY_LEAVE_FIELD_BY_CODE.get(
                str(segment.get("leave_type_code") or "").lower()
            )
            if not field:
                continue
            days = AttendanceReportService._leave_segment_overlap_days(segment, start_date, end_date)
            if days <= 0:
                continue
            hours_per_day = Decimal(str(segment.get("hours_per_day") or 8))
            amount = days * hours_per_day if field in AttendanceReportService.MONTHLY_LEAVE_HOUR_FIELDS else days
            employee_metrics = metrics.setdefault(emp_id, {})
            employee_metrics[field] = employee_metrics.get(field, Decimal("0")) + amount
        return metrics

    @staticmethod
    def _aggregate_monthly_leave_effect_metrics(
        effects_by_emp_date: dict[tuple[int, date], list[AttendanceEffect]],
        employee_ids: set[int],
    ) -> dict[int, dict[str, Decimal]]:
        metrics: dict[int, dict[str, Decimal]] = {}
        for (emp_id, _day), day_effects in effects_by_emp_date.items():
            if emp_id not in employee_ids:
                continue
            for effect in day_effects:
                item = AttendanceReportService._leave_effect_day_item(effect)
                if item is None:
                    continue
                field = AttendanceReportService.MONTHLY_LEAVE_FIELD_BY_CODE.get(str(item.get("code") or ""))
                if not field:
                    continue
                amount = Decimal(str(item.get("days") or 0))
                if field in AttendanceReportService.MONTHLY_LEAVE_HOUR_FIELDS:
                    amount *= Decimal("8")
                employee_metrics = metrics.setdefault(emp_id, {})
                employee_metrics[field] = employee_metrics.get(field, Decimal("0")) + amount
        return metrics

    @staticmethod
    def _leave_request_ids_from_segments(segments: Sequence[dict[str, Any]]) -> set[int]:
        request_ids: set[int] = set()
        for segment in segments:
            try:
                request_id = int(segment.get("request_id") or 0)
            except (TypeError, ValueError):
                continue
            if request_id > 0:
                request_ids.add(request_id)
        return request_ids

    @staticmethod
    def _leave_effect_request_id(effect: AttendanceEffect) -> Optional[int]:
        if str(getattr(effect, "effect_type", "") or "") != "leave":
            return None
        payload = getattr(effect, "payload_json", None)
        detail = payload.get("effect_detail") if isinstance(payload, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
        candidates = [
            detail.get("leave_request_id"),
            getattr(effect, "source_business_id", None)
            if str(getattr(effect, "source_business_type", "") or "") in {"leave", "leave_request"}
            else None,
        ]
        for candidate in candidates:
            try:
                request_id = int(candidate or 0)
            except (TypeError, ValueError):
                continue
            if request_id > 0:
                return request_id
        return None

    @staticmethod
    def _dedupe_leave_effects_already_in_segments(
        effects_by_emp_date: dict[tuple[int, date], list[AttendanceEffect]],
        leave_request_ids: set[int],
    ) -> dict[tuple[int, date], list[AttendanceEffect]]:
        if not leave_request_ids:
            return effects_by_emp_date
        deduped: dict[tuple[int, date], list[AttendanceEffect]] = {}
        for key, day_effects in effects_by_emp_date.items():
            kept = [
                effect
                for effect in day_effects
                if AttendanceReportService._leave_effect_request_id(effect) not in leave_request_ids
            ]
            if kept:
                deduped[key] = kept
        return deduped

    @staticmethod
    def _format_leave_metric(metrics: dict[str, Decimal], field: str) -> str:
        unit = "小时" if field in AttendanceReportService.MONTHLY_LEAVE_HOUR_FIELDS else "天"
        return AttendanceReportService._format_metric(metrics.get(field, Decimal("0")), unit, True, digits=1)

    @staticmethod
    def _monthly_status_matches(row: dict, status: Optional[str]) -> bool:
        normalized_status = str(status or "").strip()
        valid_statuses = {"正常", "迟到", "早退", "缺卡", "旷工", "请假", "出差", "异常"}
        if not normalized_status or normalized_status not in valid_statuses:
            return True
        if normalized_status == "异常":
            return row.get("_abnormal_days", 0) > 0
        if normalized_status == "正常":
            return row.get("_normal_days", 0) > 0 and row.get("_abnormal_days", 0) == 0
        return normalized_status in row.get("_status_tokens", set())

    @staticmethod
    def _monthly_day_detail(
        record: Optional[AttendanceRecord],
        *,
        expected_work_day: bool,
        status_text: str,
        day_effects: Optional[Sequence[AttendanceEffect]] = None,
        leave_items: Optional[Sequence[dict[str, Any]]] = None,
        standard_minutes: int = 0,
    ) -> str:
        day_effects = list(day_effects or [])
        leave_items = list(leave_items or [])
        effect_types = AttendanceReportService._effect_types(day_effects)
        leave_covers_day = AttendanceReportService._leave_items_cover_day(leave_items, standard_minutes)
        annotations = AttendanceReportService._day_effect_annotations(day_effects, leave_items)

        if record is None:
            if leave_covers_day:
                base_text = "请假"
            elif "business_trip" in effect_types:
                base_text = "正常（出差）"
            elif "outside" in effect_types:
                base_text = "正常（外出）"
            else:
                base_text = "旷工" if expected_work_day else "正常（休息）"
            line = f"{base_text} ;"
            return f"{line}\n " + "\n ".join(annotations) if annotations else line

        display_status = status_text
        if "outside" in effect_types and "外出" not in display_status:
            display_status = "正常（外出）" if display_status in {"正常", "--", "-"} else f"{display_status}（外出）"
        elif "business_trip" in effect_types and "出差" not in display_status:
            display_status = "正常（出差）" if display_status in {"正常", "--", "-"} else f"{display_status}（出差）"
        elif leave_covers_day and "请假" not in display_status:
            display_status = "请假" if display_status in {"正常", "--", "-"} else f"{display_status}/请假"

        if record.clock_in_time or record.clock_out_time:
            times = " ".join(
                item for item in [
                    AttendanceReportService._format_clock(record.clock_in_time),
                    AttendanceReportService._format_clock(record.clock_out_time),
                ]
                if item != "--"
            )
            line = f"{display_status} {times} ;" if times else f"{display_status} ;"
        else:
            line = f"{display_status} ;"
        return f"{line}\n " + "\n ".join(annotations) if annotations else line

    @staticmethod
    async def _load_monthly_report_rows(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int],
        keyword: Optional[str],
        status: Optional[str],
        rule_id: Optional[int],
        include_recent_left: bool,
        page: Optional[int] = None,
        page_size: Optional[int] = None,
    ) -> tuple[list[dict], list[dict], int, list[dict[str, str]]]:
        employee_conditions = [
            Employee.status.in_(
                [EmployeeStatus.ACTIVE, EmployeeStatus.ON_PROBATION, EmployeeStatus.LEFT]
                if include_recent_left
                else [EmployeeStatus.ACTIVE, EmployeeStatus.ON_PROBATION]
            )
        ]
        if department_id is not None:
            employee_conditions.append(Employee.department_id == department_id)
        if keyword:
            like = f"%{keyword.strip()}%"
            employee_conditions.append(
                or_(
                    Employee.name.ilike(like),
                    Employee.employee_no.ilike(like),
                    Employee.phone.ilike(like),
                )
            )
        if rule_id is not None:
            rule_emp_result = await db.execute(
                select(ShiftSchedule.employee_id)
                .where(
                    and_(
                        ShiftSchedule.date >= start_date,
                        ShiftSchedule.date <= end_date,
                        ShiftSchedule.rule_id == rule_id,
                    )
                )
                .distinct()
            )
            rule_employee_ids = [int(item) for item in rule_emp_result.scalars().all()]
            if not rule_employee_ids:
                return [], [], 0, AttendanceReportService._month_day_column_specs(start_date, end_date)
            employee_conditions.append(Employee.id.in_(rule_employee_ids))

        employee_stmt = select(Employee).where(and_(*employee_conditions)).order_by(Employee.id.asc())
        employees = list((await db.execute(employee_stmt)).scalars().all())
        if not employees:
            return [], [], 0, AttendanceReportService._month_day_column_specs(start_date, end_date)
        valid_statuses = {"正常", "迟到", "早退", "缺卡", "旷工", "请假", "出差", "异常"}
        normalized_status = str(status or "").strip()
        pre_paginated_mode = (
            page is not None
            and page_size is not None
            and (not normalized_status or normalized_status not in valid_statuses)
        )
        paged_total = len(employees)
        if pre_paginated_mode:
            start = (page - 1) * page_size
            end = start + page_size
            employees = employees[start:end]

        employee_ids = [int(emp.id) for emp in employees]
        from app.services.leave import LeaveIntegrationService

        leave_segments = await LeaveIntegrationService.approved_leave_segments(db, start_date, end_date)
        leave_metrics_by_emp = AttendanceReportService._aggregate_monthly_leave_metrics(
            leave_segments,
            start_date,
            end_date,
            set(employee_ids),
        )
        leave_day_items = AttendanceReportService._build_monthly_leave_day_items(
            leave_segments,
            start_date,
            end_date,
            set(employee_ids),
        )
        effect_metrics_by_emp, effects_by_emp_date = await AttendanceReportService._load_monthly_effect_context(
            db,
            start_date=start_date,
            end_date=end_date,
            employees=employees,
        )
        effects_by_emp_date = AttendanceReportService._dedupe_leave_effects_already_in_segments(
            effects_by_emp_date,
            AttendanceReportService._leave_request_ids_from_segments(leave_segments),
        )
        leave_effect_metrics_by_emp = AttendanceReportService._aggregate_monthly_leave_effect_metrics(
            effects_by_emp_date,
            set(employee_ids),
        )
        dept_result = await db.execute(select(Department))
        dept_map: Dict[int, Department] = {d.id: d for d in dept_result.scalars().all()}

        record_result = await db.execute(
            select(AttendanceRecord)
            .where(
                and_(
                    AttendanceRecord.employee_id.in_(employee_ids),
                    AttendanceRecord.date >= start_date,
                    AttendanceRecord.date <= end_date,
                )
            )
            .order_by(AttendanceRecord.date.asc(), AttendanceRecord.id.asc())
        )
        records = list(record_result.scalars().all())
        records_by_emp_date: dict[tuple[int, date], AttendanceRecord] = {}
        for record in records:
            emp_id = int(record.employee_id)
            key = (emp_id, record.date)
            old = records_by_emp_date.get(key)
            if old is None:
                records_by_emp_date[key] = record
                continue
            old_score = int(old.clock_out_time is not None) + int(old.work_hours is not None)
            new_score = int(record.clock_out_time is not None) + int(record.work_hours is not None)
            if new_score >= old_score:
                records_by_emp_date[key] = record

        days = AttendanceReportService._iter_dates(start_date, end_date)
        day_columns = AttendanceReportService._month_day_column_specs(start_date, end_date)
        overview_rows: list[dict] = []
        detail_rows: list[dict] = []
        runtime_cache: dict[tuple[int, date], dict[str, Any]] = {}
        day_type_cache: dict[tuple[Optional[int], date], DayType] = {}
        expected_day_cache: dict[tuple[int, date], bool] = {}

        async def _cached_runtime(emp_id: int, day: date, employee: Employee) -> dict[str, Any]:
            key = (emp_id, day)
            item = runtime_cache.get(key)
            if item is not None:
                return item
            runtime = await AttendanceRecordService._get_rule_runtime(db, emp_id, day, employee=employee)
            runtime_cache[key] = runtime
            return runtime

        async def _cached_day_type(location_id: Optional[int], day: date) -> DayType:
            key = (location_id, day)
            item = day_type_cache.get(key)
            if item is not None:
                return item
            day_type = await WorkCalendarService.get_day_type(db, day, location_id)
            day_type_cache[key] = day_type
            return day_type

        async def _cached_expected_work_day(employee: Employee, day: date) -> bool:
            emp_id = int(employee.id)
            key = (emp_id, day)
            cached = expected_day_cache.get(key)
            if cached is not None:
                return cached

            runtime = await _cached_runtime(emp_id, day, employee)
            rule = runtime.get("rule") if isinstance(runtime, dict) else None
            day_type = await _cached_day_type(employee.location_id, day)
            if bool((runtime or {}).get("no_punch")):
                expected_day_cache[key] = False
                return False
            if rule is None:
                from app.services.field_membership import managed_field_person
                expected = not await managed_field_person(db, emp_id, employee) and day_type in (DayType.workday, DayType.special_workday)
                expected_day_cache[key] = expected
                return expected

            extra = AttendanceRecordService._extra(rule)
            rule_type = AttendanceRecordService._infer_rule_type(rule, extra)

            if MonthSummaryService._is_date_hit_special(extra, "special_no_dates", day):
                expected_day_cache[key] = False
                return False
            if MonthSummaryService._is_date_hit_special(extra, "special_must_dates", day):
                expected_day_cache[key] = True
                return True

            if rule_type == "shift" and runtime is not None:
                if runtime.get("shift") is not None or runtime.get("project_assigned"):
                    expected_day_cache[key] = True
                    return True
                if str(extra.get("shift_arrange_mode") or "manual") == "manual":
                    expected_day_cache[key] = False
                    return False
                if not bool(extra.get("shift_no_schedule_employee_select")) and not bool(extra.get("shift_no_schedule_system_match")):
                    expected_day_cache[key] = False
                    return False

            if day_type == DayType.holiday and bool(extra.get("legal_holiday_no_punch", True)):
                expected_day_cache[key] = False
                return False
            if day_type == DayType.weekend and bool(extra.get("rest_day_no_punch", False)):
                expected_day_cache[key] = False
                return False
            if day_type in (DayType.workday, DayType.special_workday):
                expected_day_cache[key] = True
                return True

            expected = MonthSummaryService._rule_has_work_segment_on_date(rule, day)
            expected_day_cache[key] = expected
            return expected

        for employee in employees:
            emp_id = int(employee.id)
            expected_days = 0
            rest_days = 0
            normal_days = 0
            abnormal_dates: set[date] = set()
            actual_dates: set[date] = set()
            late_count = early_count = absent_count = missed_count = 0
            late_minutes = early_minutes = absent_minutes = 0
            location_abnormal_count = device_abnormal_count = insufficient_hours_count = 0
            effect_metrics = effect_metrics_by_emp.get(emp_id, AttendanceReportService._empty_monthly_effect_metrics())
            punch_correction_count = int(effect_metrics.get("punch_correction_count") or 0)
            approve_punch_count = int(effect_metrics.get("approve_punch_count") or 0)
            field_work_count = int(effect_metrics.get("field_work_count") or 0)
            outside_hours = float(effect_metrics.get("outside_hours") or 0.0)
            business_trip_dates: set[date] = set(effect_metrics.get("business_trip_dates") or set())
            leave_days = 0
            standard_hours = 0.0
            actual_hours = 0.0
            overtime_hours = float(effect_metrics.get("overtime_hours") or 0.0)
            weekday_ot = float(effect_metrics.get("weekday_ot_hours") or 0.0)
            weekend_ot = float(effect_metrics.get("weekend_ot_hours") or 0.0)
            holiday_ot = float(effect_metrics.get("holiday_ot_hours") or 0.0)
            weekday_ot_rest = float(effect_metrics.get("weekday_ot_to_rest") or 0.0)
            weekday_ot_pay = float(effect_metrics.get("weekday_ot_to_pay") or 0.0)
            weekend_ot_rest = float(effect_metrics.get("weekend_ot_to_rest") or 0.0)
            weekend_ot_pay = float(effect_metrics.get("weekend_ot_to_pay") or 0.0)
            holiday_ot_rest = float(effect_metrics.get("holiday_ot_to_rest") or 0.0)
            holiday_ot_pay = float(effect_metrics.get("holiday_ot_to_pay") or 0.0)
            status_tokens: set[str] = set()
            first_rule: Optional[AttendanceRule] = None
            detail_day_values: dict[str, str] = {}
            leave_metrics = dict(leave_metrics_by_emp.get(emp_id, {}))
            for field, amount in leave_effect_metrics_by_emp.get(emp_id, {}).items():
                leave_metrics[field] = leave_metrics.get(field, Decimal("0")) + amount

            for day, day_column in zip(days, day_columns):
                runtime = await _cached_runtime(emp_id, day, employee)
                rule = runtime.get("rule") if isinstance(runtime, dict) else None
                if first_rule is None and rule is not None:
                    first_rule = rule
                expected_work_day = await _cached_expected_work_day(employee, day)
                standard_hours_for_day = Decimal("0")
                if expected_work_day:
                    expected_days += 1
                    standard_hours_for_day = AttendanceReportService._standard_work_hours_from_runtime(rule, runtime, day)
                    standard_hours += float(standard_hours_for_day)
                else:
                    rest_days += 1

                standard_minutes_for_day = int(float(standard_hours_for_day) * 60)
                day_effects = effects_by_emp_date.get((emp_id, day), [])
                day_effect_types = AttendanceReportService._effect_types(day_effects)
                day_leave_items = [
                    *leave_day_items.get((emp_id, day), []),
                    *AttendanceReportService._leave_effect_day_items(day_effects),
                ]
                leave_covers_day = AttendanceReportService._leave_items_cover_day(
                    day_leave_items,
                    standard_minutes_for_day,
                )
                day_has_attendance_cover = (
                    leave_covers_day
                    or "business_trip" in day_effect_types
                    or "outside" in day_effect_types
                )

                record = records_by_emp_date.get((emp_id, day))
                if record is None:
                    if expected_work_day and not day_has_attendance_cover:
                        absent_count += 1
                        absent_minutes += standard_minutes_for_day
                        abnormal_dates.add(day)
                        status_tokens.add("旷工")
                    elif leave_covers_day:
                        leave_days += 1
                        status_tokens.add("请假")
                    elif "business_trip" in day_effect_types:
                        business_trip_dates.add(day)
                        status_tokens.add("出差")
                    elif "outside" in day_effect_types:
                        status_tokens.add("外出")
                    detail_day_values[day_column["key"]] = AttendanceReportService._monthly_day_detail(
                        None,
                        expected_work_day=expected_work_day,
                        status_text="--",
                        day_effects=day_effects,
                        leave_items=day_leave_items,
                        standard_minutes=standard_minutes_for_day,
                    )
                    continue

                AttendanceRecordService._attach_display_status(record, runtime)
                status_text = AttendanceReportService._status_text(record)
                parts = AttendanceReportService._status_parts(status_text)
                status_tokens.update(parts)
                if leave_covers_day:
                    status_tokens.add("请假")
                if "business_trip" in day_effect_types:
                    status_tokens.add("出差")
                if "outside" in day_effect_types:
                    status_tokens.add("外出")
                detail_day_values[day_column["key"]] = AttendanceReportService._monthly_day_detail(
                    record,
                    expected_work_day=expected_work_day,
                    status_text=status_text,
                    day_effects=day_effects,
                    leave_items=day_leave_items,
                    standard_minutes=standard_minutes_for_day,
                )
                if record.clock_in_time or record.clock_out_time:
                    actual_dates.add(day)
                if record.status == AttendanceStatus.normal and not parts and not record.anomaly_type:
                    parts = ["正常"]
                if ("正常" in parts or record.status == AttendanceStatus.normal) and not record.anomaly_type:
                    normal_days += 1
                if "请假" in parts or record.status == AttendanceStatus.on_leave:
                    leave_days += 1
                if "出差" in parts or record.status == AttendanceStatus.business_trip:
                    business_trip_dates.add(day)
                if "迟到" in parts or record.status == AttendanceStatus.late:
                    late_count += 1
                if "早退" in parts or record.status == AttendanceStatus.early_leave:
                    early_count += 1
                if "旷工" in parts or record.status == AttendanceStatus.absent:
                    absent_count += 1
                if "缺卡" in parts or record.status == AttendanceStatus.missed_clock:
                    missed_count += 1

                anomaly_text = str(record.anomaly_type or "")
                late_minutes += AttendanceReportService._extract_minutes(anomaly_text, "迟到")
                early_minutes += AttendanceReportService._extract_minutes(anomaly_text, "早退")
                if record.status == AttendanceStatus.absent:
                    absent_minutes += int(float(standard_hours_for_day) * 60)
                if record.is_mock_location:
                    location_abnormal_count += 1
                if "设备异常" in anomaly_text:
                    device_abnormal_count += 1
                if "工时不足" in anomaly_text:
                    insufficient_hours_count += 1
                if record.source == ClockSource.manual:
                    if "punch_correction" not in day_effect_types:
                        approve_punch_count += 1
                outside_clocked = "outside:" in str(record.clock_in_photo_url or "") or "outside:" in str(record.clock_out_photo_url or "")
                if outside_clocked and "outside" not in day_effect_types:
                    field_work_count += 1
                    outside_hours += float(record.work_hours or 0)
                actual_hours += float(record.work_hours or 0)

                day_type = await _cached_day_type(employee.location_id, day)
                current_ot = float(record.overtime_hours or 0)
                if current_ot and "overtime" not in day_effect_types:
                    overtime_hours += current_ot
                    if day_type in (DayType.workday, DayType.special_workday):
                        weekday_ot += current_ot
                    elif day_type == DayType.weekend:
                        weekend_ot += current_ot
                    elif day_type == DayType.holiday:
                        holiday_ot += current_ot
                    extra_for_day = (runtime or {}).get("extra") or (AttendanceRecordService._extra(rule) if rule else {})
                    comp_ot, pay_ot = AttendanceRecordService._split_overtime_settlement(
                        extra_for_day,
                        day_type,
                        Decimal(str(current_ot)),
                    )
                    comp_hours = float(comp_ot)
                    pay_hours = float(pay_ot)
                    if day_type in (DayType.workday, DayType.special_workday):
                        weekday_ot_rest += comp_hours
                        weekday_ot_pay += pay_hours
                    elif day_type == DayType.weekend:
                        weekend_ot_rest += comp_hours
                        weekend_ot_pay += pay_hours
                    elif day_type == DayType.holiday:
                        holiday_ot_rest += comp_hours
                        holiday_ot_pay += pay_hours

                if any(part in {"迟到", "早退", "旷工", "缺卡", "异常"} for part in parts) or record.is_mock_location:
                    abnormal_dates.add(day)

            anomaly_total = (
                late_count
                + early_count
                + absent_count
                + missed_count
                + location_abnormal_count
                + device_abnormal_count
                + insufficient_hours_count
            )
            row = {
                "employee_name": employee.name,
                "account": employee.employee_no or employee.phone or "--",
                "rule_name": first_rule.name if first_rule else "--",
                "department_name": AttendanceReportService._dept_full_name(employee.department_id, dept_map),
                "position": employee.position or "--",
                "expected_days": AttendanceReportService._format_metric(expected_days, "天", digits=1),
                "actual_days": AttendanceReportService._format_metric(len(actual_dates), "天", digits=1),
                "rest_days": AttendanceReportService._format_metric(rest_days, "天", digits=1),
                "normal_days": AttendanceReportService._format_metric(normal_days, "天", digits=1),
                "abnormal_days": AttendanceReportService._format_metric(len(abnormal_dates), "天", digits=1),
                "standard_work_hours": AttendanceReportService._format_metric(standard_hours, "小时", digits=1),
                "actual_work_hours": AttendanceReportService._format_metric(actual_hours, "小时", digits=1),
                "anomaly_total": AttendanceReportService._format_metric(anomaly_total, "次", True),
                "late_count": AttendanceReportService._format_metric(late_count, "次", True),
                "late_minutes": AttendanceReportService._format_metric(late_minutes, "分钟", True),
                "early_count": AttendanceReportService._format_metric(early_count, "次", True),
                "early_minutes": AttendanceReportService._format_metric(early_minutes, "分钟", True),
                "absent_count": AttendanceReportService._format_metric(absent_count, "次", True),
                "absent_minutes": AttendanceReportService._format_metric(absent_minutes, "分钟", True),
                "missed_count": AttendanceReportService._format_metric(missed_count, "次", True),
                "location_abnormal_count": AttendanceReportService._format_metric(location_abnormal_count, "次", True),
                "device_abnormal_count": AttendanceReportService._format_metric(device_abnormal_count, "次", True),
                "punch_correction_count": AttendanceReportService._format_metric(punch_correction_count, "次", True),
                "approve_punch_count": AttendanceReportService._format_metric(approve_punch_count, "次", True),
                "field_work_count": AttendanceReportService._format_metric(field_work_count, "次", True),
                "outside_hours": AttendanceReportService._format_metric(outside_hours, "小时", True, digits=1),
                "business_trip_days": AttendanceReportService._format_metric(len(business_trip_dates), "天", True, digits=1),
                "annual_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "annual_leave_days"),
                "marriage_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "marriage_leave_days"),
                "maternity_leave_hours": AttendanceReportService._format_leave_metric(leave_metrics, "maternity_leave_hours"),
                "paternity_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "paternity_leave_days"),
                "sick_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "sick_leave_days"),
                "lieu_leave_hours": AttendanceReportService._format_leave_metric(leave_metrics, "lieu_leave_hours"),
                "personal_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "personal_leave_days"),
                "long_sick_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "long_sick_leave_days"),
                "prenatal_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "prenatal_leave_days"),
                "bereavement_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "bereavement_leave_days"),
                "general_sick_leave_days": AttendanceReportService._format_leave_metric(leave_metrics, "general_sick_leave_days"),
                "overtime_hours": AttendanceReportService._format_metric(overtime_hours, "小时", True, digits=1),
                "weekday_ot_hours": AttendanceReportService._format_metric(weekday_ot, "小时", True, digits=1),
                "weekday_ot_to_rest": AttendanceReportService._format_metric(weekday_ot_rest, "小时", True, digits=1),
                "weekday_ot_to_pay": AttendanceReportService._format_metric(weekday_ot_pay, "小时", True, digits=1),
                "weekend_ot_hours": AttendanceReportService._format_metric(weekend_ot, "小时", True, digits=1),
                "weekend_ot_to_rest": AttendanceReportService._format_metric(weekend_ot_rest, "小时", True, digits=1),
                "weekend_ot_to_pay": AttendanceReportService._format_metric(weekend_ot_pay, "小时", True, digits=1),
                "holiday_ot_hours": AttendanceReportService._format_metric(holiday_ot, "小时", True, digits=1),
                "holiday_ot_to_rest": AttendanceReportService._format_metric(holiday_ot_rest, "小时", True, digits=1),
                "holiday_ot_to_pay": AttendanceReportService._format_metric(holiday_ot_pay, "小时", True, digits=1),
                "_abnormal_days": len(abnormal_dates),
                "_normal_days": normal_days,
                "_status_tokens": status_tokens,
            }
            if not AttendanceReportService._monthly_status_matches(row, status):
                continue
            overview_rows.append({k: v for k, v in row.items() if not k.startswith("_")})
            detail_rows.append(
                {
                    "employee_name": employee.name,
                    "account": employee.employee_no or employee.phone or "--",
                    "rule_name": first_rule.name if first_rule else "--",
                    "department_name": AttendanceReportService._dept_full_name(employee.department_id, dept_map),
                    "position": employee.position or "--",
                    **detail_day_values,
                }
            )

        total = paged_total if pre_paginated_mode else len(overview_rows)
        if (not pre_paginated_mode) and page is not None and page_size is not None:
            start = (page - 1) * page_size
            end = start + page_size
            overview_rows = overview_rows[start:end]
            detail_rows = detail_rows[start:end]
        return overview_rows, detail_rows, total, day_columns

    @staticmethod
    async def get_monthly_report_data(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        overview_rows, detail_rows, total, day_columns = await AttendanceReportService._load_monthly_report_rows(
            db,
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
            keyword=keyword,
            status=status,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
            page=page,
            page_size=page_size,
        )
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "page": page,
            "page_size": page_size,
            "total": total,
            "overview_columns": AttendanceReportService.MONTHLY_OVERVIEW_COLUMN_SPECS,
            "detail_base_columns": AttendanceReportService.MONTHLY_DETAIL_BASE_COLUMN_SPECS,
            "detail_day_columns": day_columns,
            "overview_rows": overview_rows,
            "detail_rows": detail_rows,
        }

    @staticmethod
    def _monthly_report_result_payload(
        *,
        start_date: date,
        end_date: date,
        page: int,
        page_size: int,
        total: int,
        day_columns: list[dict[str, str]],
        overview_rows: list[dict],
        detail_rows: list[dict],
    ) -> dict[str, Any]:
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "page": page,
            "page_size": page_size,
            "total": total,
            "overview_columns": AttendanceReportService.MONTHLY_OVERVIEW_COLUMN_SPECS,
            "detail_base_columns": AttendanceReportService.MONTHLY_DETAIL_BASE_COLUMN_SPECS,
            "detail_day_columns": day_columns,
            "overview_rows": overview_rows,
            "detail_rows": detail_rows,
        }

    @staticmethod
    def export_wecom_monthly_report_from_result(
        result: dict[str, Any],
        *,
        overview_columns: Optional[list[str]] = None,
        detail_columns: Optional[list[str]] = None,
    ) -> bytes:
        import openpyxl

        start_date = date.fromisoformat(str(result["start_date"]))
        end_date = date.fromisoformat(str(result["end_date"]))
        overview_rows = list(result.get("overview_rows") or [])
        detail_rows = list(result.get("detail_rows") or [])
        day_columns = list(result.get("detail_day_columns") or [])
        overview_specs = AttendanceReportService._resolve_column_specs(
            AttendanceReportService.MONTHLY_OVERVIEW_COLUMN_SPECS,
            overview_columns,
        )
        detail_base_specs = AttendanceReportService._resolve_column_specs(
            AttendanceReportService.MONTHLY_DETAIL_BASE_COLUMN_SPECS,
            detail_columns,
        )
        detail_base_keys = {item["key"] for item in detail_base_specs}
        detail_date_rows = [
            {key: value for key, value in row.items() if key not in detail_base_keys}
            for row in detail_rows
        ]
        combined_specs = [*overview_specs, *day_columns]
        combined_rows: list[dict[str, Any]] = []
        for idx, overview_row in enumerate(overview_rows):
            detail_day_row = detail_date_rows[idx] if idx < len(detail_date_rows) else {}
            combined_rows.append({**overview_row, **detail_day_row})

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "上下班打卡_月报"
        cst_now = datetime.now(AttendanceRecordService._cst())
        period_text = (
            f"统计时间:{start_date.strftime('%m-%d')} ～ {end_date.strftime('%m-%d')}"
            f"     制表时间:{cst_now.strftime('%Y-%m-%d %H:%M')}(UTC+8)"
        )
        monthly_group_map = {
            **AttendanceReportService.MONTHLY_OVERVIEW_EXPORT_GROUPS,
            **{item["key"]: "打卡明细" for item in day_columns},
        }
        AttendanceReportService._apply_wecom_report_sheet(
            ws,
            title="上下班打卡_月报",
            period_text=period_text,
            specs=combined_specs,
            rows=combined_rows,
            group_map=monthly_group_map,
            freeze_panes="B5",
        )

        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @staticmethod
    async def export_wecom_monthly_report(
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
        overview_columns: Optional[list[str]] = None,
        detail_columns: Optional[list[str]] = None,
    ) -> bytes:
        overview_rows, detail_rows, total, day_columns = await AttendanceReportService._load_monthly_report_rows(
            db,
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
            keyword=keyword,
            status=status,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
            page=None,
            page_size=None,
        )
        result = AttendanceReportService._monthly_report_result_payload(
            start_date=start_date,
            end_date=end_date,
            page=1,
            page_size=total,
            total=total,
            day_columns=day_columns,
            overview_rows=overview_rows,
            detail_rows=detail_rows,
        )
        return AttendanceReportService.export_wecom_monthly_report_from_result(
            result,
            overview_columns=overview_columns,
            detail_columns=detail_columns,
        )

    @staticmethod
    async def export_monthly_report(
        db: AsyncSession,
        year: int,
        month: int,
        department_id: Optional[int] = None,
    ) -> bytes:
        """
        生成考勤月报 Excel 文件，返回文件二进制内容。

        报表列说明：
        | 列名         | 数据来源                         |
        |------------|--------------------------------|
        | 姓名         | Employee.name                  |
        | 部门         | Department.name（通过 dept_id 关联） |
        | 工号         | Employee.employee_no           |
        | 工作日天数     | 本月周一至周五天数（简单计算，未使用日历）      |
        | 实出勤        | normal + late + early_leave 天数  |
        | 迟到次数       | late 状态记录数                   |
        | 早退次数       | early_leave 状态记录数            |
        | 旷工天数       | absent 状态记录数                 |
        | 加班小时数     | overtime_hours 字段求和           |

        注意：工作日天数采用简单的"周一至周五"计算，未调用 WorkCalendarService，
        因此不反映节假日和调休的调整。如需精确数据请使用 MonthSummaryService.generate()。

        参数:
            db: 异步数据库会话
            year: 年份
            month: 月份（1-12）
            department_id: 部门 ID（可选，指定后仅导出该部门员工）

        返回:
            Excel 文件的 bytes 内容，可直接作为 HTTP 响应体返回
        """
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        first_day = date(year, month, 1)
        last_day = date(year, month, monthrange(year, month)[1])

        # 查询该月所有考勤记录
        conditions = [
            AttendanceRecord.date >= first_day,
            AttendanceRecord.date <= last_day,
        ]
        result = await db.execute(
            select(AttendanceRecord).where(and_(*conditions))
        )
        records = result.scalars().all()

        # 查询员工信息（若指定部门则过滤）
        emp_conditions = []
        if department_id is not None:
            emp_conditions.append(Employee.department_id == department_id)
        emp_result = await db.execute(
            select(Employee).where(and_(*emp_conditions)) if emp_conditions else select(Employee)
        )
        employees = emp_result.scalars().all()
        emp_map: Dict[int, Employee] = {e.id: e for e in employees}

        # 查询所有部门信息用于显示部门名称
        from app.models.organization import Department
        dept_result = await db.execute(select(Department))
        dept_map: Dict[int, str] = {d.id: d.name for d in dept_result.scalars().all()}

        # 按员工 ID 分组考勤记录（只处理在 emp_map 中的员工，即满足部门过滤条件的）
        emp_records: Dict[int, List[AttendanceRecord]] = {}
        for r in records:
            if department_id is None or r.employee_id in emp_map:
                emp_records.setdefault(r.employee_id, []).append(r)

        # 构建报表行数据
        rows = []
        emp_ids = list(emp_records.keys()) if not department_id else [e.id for e in employees]
        for emp_id in emp_ids:
            emp = emp_map.get(emp_id)
            if emp is None:
                continue
            recs = emp_records.get(emp_id, [])
            rec_by_date: Dict[date, AttendanceRecord] = {}
            for r in recs:
                old = rec_by_date.get(r.date)
                if old is None:
                    rec_by_date[r.date] = r
                    continue
                old_score = int(old.clock_out_time is not None) + int(old.work_hours is not None)
                new_score = int(r.clock_out_time is not None) + int(r.work_hours is not None)
                if new_score >= old_score:
                    rec_by_date[r.date] = r
            unique_recs = list(rec_by_date.values())

            work_days = 0
            missing_absent = 0
            current = first_day
            while current <= last_day:
                should_work = await MonthSummaryService._expected_work_day(
                    db,
                    emp_id,
                    current,
                    emp.location_id,
                    employee=emp,
                )
                if should_work:
                    work_days += 1
                    if current not in rec_by_date:
                        missing_absent += 1
                current += timedelta(days=1)

            actual_days = sum(
                1 for r in unique_recs
                if r.status in (AttendanceStatus.normal, AttendanceStatus.late, AttendanceStatus.early_leave)
            )
            late_count = sum(1 for r in unique_recs if r.status == AttendanceStatus.late)
            early_count = sum(1 for r in unique_recs if r.status == AttendanceStatus.early_leave)
            absent_count = sum(1 for r in unique_recs if r.status == AttendanceStatus.absent) + missing_absent
            ot_hours = 0.0
            for r in unique_recs:
                if not r.overtime_hours:
                    continue
                dt = await WorkCalendarService.get_day_type(db, r.date, emp.location_id)
                runtime_for_day = await AttendanceRecordService._get_rule_runtime(
                    db,
                    emp_id,
                    r.date,
                    employee=emp,
                )
                extra_for_day = (runtime_for_day or {}).get("extra") or {}
                if not AttendanceRecordService._is_overtime_payroll_eligible(extra_for_day, dt):
                    continue
                ot_hours += float(r.overtime_hours or 0)

            dept_name = dept_map.get(emp.department_id or 0, "")
            rows.append({
                "name": emp.name,
                "dept": dept_name,
                "emp_no": emp.employee_no or "",
                "work_days": work_days,
                "actual_days": actual_days,
                "late_count": late_count,
                "early_count": early_count,
                "absent_count": absent_count,
                "ot_hours": round(ot_hours, 2),
            })

        # 创建 Excel 工作簿
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{year}年{month}月考勤月报"

        # 表头样式：深蓝色背景 (#4F46E5)，白色加粗字体，居中对齐，细边框
        headers = ["姓名", "部门", "工号", "工作日天数", "实出勤", "迟到次数", "早退次数", "旷工天数", "加班小时数"]
        header_fill = PatternFill("solid", fgColor="4F46E5")
        header_font = Font(color="FFFFFF", bold=True, size=11)
        thin = Side(style="thin", color="E2E8F0")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for col_idx, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border

        ws.row_dimensions[1].height = 22

        # 数据行：偶数行使用淡灰色背景 (#F8FAFC)
        for row_idx, row in enumerate(rows, start=2):
            values = [
                row["name"], row["dept"], row["emp_no"],
                row["work_days"], row["actual_days"],
                row["late_count"], row["early_count"],
                row["absent_count"], row["ot_hours"],
            ]
            for col_idx, val in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = border
                if row_idx % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F8FAFC")

        # 固定列宽（与列顺序一一对应）
        col_widths = [12, 16, 12, 12, 10, 10, 10, 10, 12]
        for col_idx, width in enumerate(col_widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width

        # 将 Excel 写入内存缓冲区并返回 bytes
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

class AttendanceMonthlyReportTaskService:
    """
    月报异步任务服务。

    任务状态和短期结果先放进进程内缓存；完成结果同步写入数据库快照。
    生产环境如接入 Redis，可将 _tasks/_result_cache/_running_by_cache_key
    替换为 Redis Hash/JSON 和分布式锁，外部 API 协议无需变化。
    """

    RESULT_TTL_SECONDS = 2 * 60 * 60
    _tasks: dict[str, dict[str, Any]] = {}
    _result_cache: dict[str, dict[str, Any]] = {}
    _running_by_cache_key: dict[str, str] = {}
    _lock = asyncio.Lock()

    @classmethod
    def _now(cls) -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _iso(cls, value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None

    @classmethod
    def normalize_query(
        cls,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
    ) -> dict[str, Any]:
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "department_id": int(department_id) if department_id is not None else None,
            "keyword": str(keyword or "").strip() or None,
            "status": str(status or "").strip() or None,
            "rule_id": int(rule_id) if rule_id is not None else None,
            "include_recent_left": bool(include_recent_left),
        }

    @classmethod
    def cache_key(cls, query: dict[str, Any]) -> str:
        raw = json.dumps(query, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _public_task(
        cls,
        task: dict[str, Any],
        *,
        deduplicated: bool = False,
        from_cache: bool = False,
    ) -> dict[str, Any]:
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "progress": int(task.get("progress") or 0),
            "message": task.get("message") or "",
            "error": task.get("error"),
            "cache_key": task.get("cache_key"),
            "query": task.get("query") or {},
            "result_ready": task.get("status") == "succeeded" and bool(task.get("result")),
            "row_count": int((task.get("result") or {}).get("total") or task.get("row_count") or 0),
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "completed_at": task.get("completed_at"),
            "updated_at": task.get("updated_at"),
            "expires_at": task.get("expires_at"),
            "snapshot_id": task.get("snapshot_id"),
            "deduplicated": deduplicated,
            "from_cache": from_cache or bool(task.get("from_cache")),
        }

    @classmethod
    def _new_task(
        cls,
        *,
        task_id: str,
        cache_key: str,
        query: dict[str, Any],
        requested_by_id: Optional[int],
        status: str = "pending",
        result: Optional[dict[str, Any]] = None,
        message: str = "月报任务已创建",
        from_cache: bool = False,
        snapshot_id: Optional[int] = None,
    ) -> dict[str, Any]:
        now = cls._now()
        expires_at = now + timedelta(seconds=cls.RESULT_TTL_SECONDS)
        return {
            "task_id": task_id,
            "cache_key": cache_key,
            "query": query,
            "requested_by_id": requested_by_id,
            "status": status,
            "progress": 100 if status == "succeeded" else 0,
            "message": message,
            "error": None,
            "result": result,
            "row_count": int((result or {}).get("total") or 0),
            "from_cache": from_cache,
            "snapshot_id": snapshot_id,
            "created_at": cls._iso(now),
            "started_at": None,
            "completed_at": cls._iso(now) if status == "succeeded" else None,
            "updated_at": cls._iso(now),
            "expires_at": cls._iso(expires_at),
            "_expires_at": expires_at,
        }

    @classmethod
    async def _cleanup_expired_locked(cls) -> None:
        now = cls._now()
        expired_task_ids = [
            task_id
            for task_id, task in cls._tasks.items()
            if task.get("_expires_at") and task["_expires_at"] <= now and task.get("status") not in {"pending", "running"}
        ]
        for task_id in expired_task_ids:
            task = cls._tasks.pop(task_id, None)
            if task:
                cls._running_by_cache_key.pop(str(task.get("cache_key")), None)
        expired_keys = [
            cache_key
            for cache_key, item in cls._result_cache.items()
            if item.get("expires_at") and item["expires_at"] <= now
        ]
        for cache_key in expired_keys:
            cls._result_cache.pop(cache_key, None)

    @classmethod
    async def create_task(
        cls,
        db: AsyncSession,
        *,
        start_date: date,
        end_date: date,
        department_id: Optional[int] = None,
        keyword: Optional[str] = None,
        status: Optional[str] = None,
        rule_id: Optional[int] = None,
        include_recent_left: bool = True,
        requested_by_id: Optional[int] = None,
        force_regenerate: bool = False,
    ) -> dict[str, Any]:
        query = cls.normalize_query(
            start_date=start_date,
            end_date=end_date,
            department_id=department_id,
            keyword=keyword,
            status=status,
            rule_id=rule_id,
            include_recent_left=include_recent_left,
        )
        cache_key = cls.cache_key(query)

        async with cls._lock:
            await cls._cleanup_expired_locked()
            if not force_regenerate:
                running_task_id = cls._running_by_cache_key.get(cache_key)
                running_task = cls._tasks.get(running_task_id or "")
                if running_task and running_task.get("status") in {"pending", "running"}:
                    return cls._public_task(running_task, deduplicated=True)

                cached = cls._result_cache.get(cache_key)
                if cached and cached.get("expires_at") and cached["expires_at"] > cls._now():
                    task = cls._tasks.get(str(cached.get("task_id")))
                    if task is None:
                        task = cls._new_task(
                            task_id=str(cached.get("task_id") or uuid4().hex),
                            cache_key=cache_key,
                            query=query,
                            requested_by_id=requested_by_id,
                            status="succeeded",
                            result=cached.get("result") or {},
                            message="已复用短期缓存结果",
                            from_cache=True,
                            snapshot_id=cached.get("snapshot_id"),
                        )
                        cls._tasks[task["task_id"]] = task
                    return cls._public_task(task, from_cache=True)

        if not force_regenerate:
            snapshot = await cls._load_recent_snapshot(db, cache_key)
            if snapshot is not None:
                result = snapshot.result_json if isinstance(snapshot.result_json, dict) else {}
                task = cls._new_task(
                    task_id=uuid4().hex,
                    cache_key=cache_key,
                    query=query,
                    requested_by_id=requested_by_id,
                    status="succeeded",
                    result=result,
                    message="已复用数据库快照结果",
                    from_cache=True,
                    snapshot_id=getattr(snapshot, "id", None),
                )
                async with cls._lock:
                    cls._tasks[task["task_id"]] = task
                    cls._result_cache[cache_key] = {
                        "task_id": task["task_id"],
                        "result": result,
                        "expires_at": task["_expires_at"],
                        "snapshot_id": task.get("snapshot_id"),
                    }
                return cls._public_task(task, from_cache=True)

        task_id = uuid4().hex
        task = cls._new_task(
            task_id=task_id,
            cache_key=cache_key,
            query=query,
            requested_by_id=requested_by_id,
        )
        async with cls._lock:
            running_task_id = cls._running_by_cache_key.get(cache_key)
            running_task = cls._tasks.get(running_task_id or "")
            if not force_regenerate and running_task and running_task.get("status") in {"pending", "running"}:
                return cls._public_task(running_task, deduplicated=True)
            cls._tasks[task_id] = task
            cls._running_by_cache_key[cache_key] = task_id
        cls._schedule_generation(task_id)
        return cls._public_task(task)

    @classmethod
    def _schedule_generation(cls, task_id: str) -> None:
        asyncio.create_task(cls._run_task(task_id))

    @classmethod
    async def _run_task(cls, task_id: str) -> None:
        async with cls._lock:
            task = cls._tasks.get(task_id)
            if task is None:
                return
            now = cls._now()
            task.update(
                {
                    "status": "running",
                    "progress": 10,
                    "message": "月报生成中",
                    "started_at": cls._iso(now),
                    "updated_at": cls._iso(now),
                }
            )
            query = dict(task.get("query") or {})
            cache_key = str(task.get("cache_key") or "")
            requested_by_id = task.get("requested_by_id")

        try:
            async with async_session() as db:
                result = await cls._generate_result(db, query)
                snapshot_id = await cls._save_snapshot(
                    db,
                    task_id=task_id,
                    cache_key=cache_key,
                    query=query,
                    result=result,
                    requested_by_id=requested_by_id,
                )
            completed_at = cls._now()
            expires_at = completed_at + timedelta(seconds=cls.RESULT_TTL_SECONDS)
            async with cls._lock:
                task = cls._tasks.get(task_id)
                if task is None:
                    return
                task.update(
                    {
                        "status": "succeeded",
                        "progress": 100,
                        "message": "月报生成完成",
                        "error": None,
                        "result": result,
                        "row_count": int(result.get("total") or 0),
                        "completed_at": cls._iso(completed_at),
                        "updated_at": cls._iso(completed_at),
                        "expires_at": cls._iso(expires_at),
                        "_expires_at": expires_at,
                        "snapshot_id": snapshot_id,
                    }
                )
                cls._running_by_cache_key.pop(cache_key, None)
                cls._result_cache[cache_key] = {
                    "task_id": task_id,
                    "result": result,
                    "expires_at": expires_at,
                    "snapshot_id": snapshot_id,
                }
        except Exception as exc:
            logger.exception("月报异步任务生成失败 task_id=%s", task_id)
            failed_at = cls._now()
            async with cls._lock:
                task = cls._tasks.get(task_id)
                if task is None:
                    return
                task.update(
                    {
                        "status": "failed",
                        "progress": 100,
                        "message": "月报生成失败",
                        "error": str(exc) or exc.__class__.__name__,
                        "updated_at": cls._iso(failed_at),
                        "completed_at": cls._iso(failed_at),
                    }
                )
                cls._running_by_cache_key.pop(str(task.get("cache_key") or ""), None)

    @classmethod
    async def _generate_result(cls, db: AsyncSession, query: dict[str, Any]) -> dict[str, Any]:
        start_date = date.fromisoformat(str(query["start_date"]))
        end_date = date.fromisoformat(str(query["end_date"]))
        overview_rows, detail_rows, total, day_columns = await AttendanceReportService._load_monthly_report_rows(
            db,
            start_date=start_date,
            end_date=end_date,
            department_id=query.get("department_id"),
            keyword=query.get("keyword"),
            status=query.get("status"),
            rule_id=query.get("rule_id"),
            include_recent_left=bool(query.get("include_recent_left", True)),
            page=None,
            page_size=None,
        )
        return AttendanceReportService._monthly_report_result_payload(
            start_date=start_date,
            end_date=end_date,
            page=1,
            page_size=total,
            total=total,
            day_columns=day_columns,
            overview_rows=overview_rows,
            detail_rows=detail_rows,
        )

    @classmethod
    async def get_task(cls, task_id: str, db: Optional[AsyncSession] = None) -> Optional[dict[str, Any]]:
        async with cls._lock:
            await cls._cleanup_expired_locked()
            task = cls._tasks.get(task_id)
            if task is not None:
                return cls._public_task(task)
        if db is None:
            return None
        snapshot = await cls._load_snapshot_by_task_id(db, task_id)
        if snapshot is None:
            return None
        result = snapshot.result_json if isinstance(snapshot.result_json, dict) else {}
        query = snapshot.query_json if isinstance(snapshot.query_json, dict) else {}
        task = cls._new_task(
            task_id=task_id,
            cache_key=str(snapshot.cache_key),
            query=query,
            requested_by_id=getattr(snapshot, "generated_by_id", None),
            status="succeeded",
            result=result,
            message="已读取数据库快照结果",
            from_cache=True,
            snapshot_id=getattr(snapshot, "id", None),
        )
        async with cls._lock:
            cls._tasks[task_id] = task
            cls._result_cache[str(snapshot.cache_key)] = {
                "task_id": task_id,
                "result": result,
                "expires_at": task["_expires_at"],
                "snapshot_id": getattr(snapshot, "id", None),
            }
        return cls._public_task(task, from_cache=True)

    @classmethod
    async def get_result(
        cls,
        task_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        db: Optional[AsyncSession] = None,
    ) -> Optional[dict[str, Any]]:
        task = None
        async with cls._lock:
            task = cls._tasks.get(task_id)
            result = task.get("result") if task else None
        if result is None and db is not None:
            snapshot = await cls._load_snapshot_by_task_id(db, task_id)
            if snapshot is not None:
                result = snapshot.result_json if isinstance(snapshot.result_json, dict) else None
        if not isinstance(result, dict):
            return None

        overview_rows = list(result.get("overview_rows") or [])
        detail_rows = list(result.get("detail_rows") or [])
        total = int(result.get("total") or len(overview_rows))
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return {
            **result,
            "page": page,
            "page_size": page_size,
            "total": total,
            "overview_rows": overview_rows[start:end],
            "detail_rows": detail_rows[start:end],
        }

    @classmethod
    async def export_result(
        cls,
        task_id: str,
        *,
        overview_columns: Optional[list[str]] = None,
        detail_columns: Optional[list[str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[bytes]:
        result = None
        async with cls._lock:
            task = cls._tasks.get(task_id)
            if task and task.get("status") == "succeeded":
                result = task.get("result")
        if result is None and db is not None:
            snapshot = await cls._load_snapshot_by_task_id(db, task_id)
            if snapshot is not None:
                result = snapshot.result_json if isinstance(snapshot.result_json, dict) else None
        if not isinstance(result, dict):
            return None
        return AttendanceReportService.export_wecom_monthly_report_from_result(
            result,
            overview_columns=overview_columns,
            detail_columns=detail_columns,
        )

    @classmethod
    async def _load_recent_snapshot(
        cls,
        db: AsyncSession,
        cache_key: str,
    ) -> Optional[AttendanceMonthlyReportSnapshot]:
        try:
            result = await db.execute(
                select(AttendanceMonthlyReportSnapshot)
                .where(
                    and_(
                        AttendanceMonthlyReportSnapshot.cache_key == cache_key,
                        AttendanceMonthlyReportSnapshot.status == "succeeded",
                        or_(
                            AttendanceMonthlyReportSnapshot.expires_at.is_(None),
                            AttendanceMonthlyReportSnapshot.expires_at > cls._now(),
                        ),
                    )
                )
                .order_by(
                    AttendanceMonthlyReportSnapshot.completed_at.desc(),
                    AttendanceMonthlyReportSnapshot.id.desc(),
                )
                .limit(1)
            )
            return result.scalar_one_or_none()
        except (OperationalError, ProgrammingError):
            await db.rollback()
            return None

    @classmethod
    async def _load_snapshot_by_task_id(
        cls,
        db: AsyncSession,
        task_id: str,
    ) -> Optional[AttendanceMonthlyReportSnapshot]:
        try:
            result = await db.execute(
                select(AttendanceMonthlyReportSnapshot)
                .where(AttendanceMonthlyReportSnapshot.task_id == task_id)
                .order_by(AttendanceMonthlyReportSnapshot.id.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()
        except (OperationalError, ProgrammingError):
            await db.rollback()
            return None

    @classmethod
    async def _save_snapshot(
        cls,
        db: AsyncSession,
        *,
        task_id: str,
        cache_key: str,
        query: dict[str, Any],
        result: dict[str, Any],
        requested_by_id: Optional[int],
    ) -> Optional[int]:
        now = cls._now()
        snapshot = AttendanceMonthlyReportSnapshot(
            task_id=task_id,
            cache_key=cache_key,
            query_json=query,
            result_json=result,
            row_count=int(result.get("total") or 0),
            status="succeeded",
            generated_by_id=requested_by_id,
            created_at=now,
            completed_at=now,
            expires_at=now + timedelta(seconds=cls.RESULT_TTL_SECONDS),
            updated_at=now,
        )
        try:
            db.add(snapshot)
            await db.flush()
            snapshot_id = int(snapshot.id)
            await db.commit()
            return snapshot_id
        except (OperationalError, ProgrammingError):
            await db.rollback()
            return None

    @classmethod
    def _reset_memory_store_for_tests(cls) -> None:
        cls._tasks.clear()
        cls._result_cache.clear()
        cls._running_by_cache_key.clear()
