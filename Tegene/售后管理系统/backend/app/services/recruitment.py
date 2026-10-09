"""
招聘管理服务层 (Recruitment Service Layer)
==========================================

本模块是售后管理系统招聘管理的核心业务逻辑层，实现完整的招聘漏斗流程。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
招聘全流程（10步漏斗）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 招聘需求申请  → RecruitmentDemandService.create_demand()
2. 需求审批      → RecruitmentDemandService.approve_demand()
3. 简历投递      → ResumeService.create_resume()
4. 初筛          → ResumeService.screen_resume() / bulk_screen()
5. 面试安排      → InterviewService.create_interview() / schedule_interview()
6. 面试结果录入  → InterviewEvaluationService.create_evaluation()
7. Offer 创建    → OfferService.create_offer()
8. Offer 审批    → OfferService.approve_offer()
9. Offer 发送与确认 → OfferService.send_offer() / candidate_confirm_offer()
10. 入职记录     → OnboardingService.create_record()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
服务类一览
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- RecruitmentChannelService   — 招聘渠道管理（BOSS直聘/猎头/内推等）
- RecruitmentDemandService    — 招聘需求申请与审批
- ResumeService               — 简历投递、筛选、批量操作、统计
- InterviewService            — 面试安排、改约、取消（支持多轮：round 字段）
- InterviewEvaluationService  — 面试评价录入与查询
- OfferService                — Offer 创建、审批、发送、候选人确认、批量操作
- InternalReferralService     — 员工内推管理（含两期奖金、排行榜、Excel导出）
- ReferralRuleService         — 内推奖励规则配置
- RecruitmentStatsService     — 招聘漏斗转化率 & 渠道有效性分析
- OfferTemplateService        — Offer 模板管理
- OnboardingService           — 入职记录管理

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
关键业务规则
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 只有筛选状态为「推荐（RECOMMENDED）」的简历才能安排面试
- 只有已完成（COMPLETED）的面试才能提交评价，且一场面试只能有一条评价
- Offer 必须先审批通过（approval_status=APPROVED）才能发送给候选人
- 候选人接受 Offer 后，系统自动检查招聘需求的 headcount 是否已满员（_check_demand_fulfillment）
- 只有候选人已接受（ACCEPTED）的 Offer 才能创建入职记录
- 内推奖金分两期发放：候选人入职发第一期，转正发第二期
- 内推候选人入职后自动触发奖金审批通知（发给 HR 和推荐人）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
技术约定
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 所有方法均为异步（async/await），使用 SQLAlchemy 2.0 AsyncSession
- 方法均为静态方法（@staticmethod），服务类无状态
- 分页参数：page 从 1 开始，返回 (items, total) 或 (total, items) 元组
- 数据库写操作统一用 flush()（写入事务未提交）+ refresh()（刷新自动生成字段）
- 404/400/409 错误统一通过 HTTPException 向上抛出
- 审批流集成：create_demand() 通过 maybe_create_approval_instance() 软触发审批流

All database operations go through this layer. API routes should not
contain direct SQLAlchemy queries.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional, Sequence

import httpx
from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sql_compat import order_by_nulls_last_desc
from app.core.security import DEFAULT_INITIAL_PASSWORD, get_password_hash
from app.models.employee import Employee, EmployeeStatus, EmploymentType
from app.models.organization import Department
from app.models.recruitment import (
    ApprovalStatus,
    DemandStatus,
    InterviewEvaluation,
    InterviewStatus,
    InternalReferral,
    InternalReferralRule,
    Interview,
    Offer,
    OfferApprovalStatus,
    OfferStatus,
    OfferTemplate,
    OnboardingRecord,
    OnboardingStatus,
    OverallResult,
    RecruitmentChannel,
    RecruitmentDemand,
    RecruitmentPosition,
    Resume,
    ScreeningStatus,
)
from app.services.approval import maybe_create_approval_instance
from app.schemas.recruitment import (
    BulkDeleteRequest,
    BulkDeleteResult,
    BulkOfferActionRequest,
    BulkOfferActionResult,
    BulkScreenRequest,
    BulkScreenResult,
    ChannelEffectivenessOut,
    DemandApprovalRequest,
    DemandPublishRequest,
    DepartmentHeadcountOut,
    RecruitmentDemandBusinessSubmitOut,
    DemandPublishOut,
    FunnelStageStats,
    InterviewCancelRequest,
    InterviewEvaluationCreate,
    InterviewRescheduleRequest,
    InterviewScheduleRequest,
    InternalReferralCreate,
    InternalReferralUpdate,
    OfferApprovalRequest,
    OfferCandidateConfirmRequest,
    OfferCreate,
    OfferSendRequest,
    OfferUpdate,
    OfferTemplateCreate,
    OfferTemplateUpdate,
    OnboardingRecordCreate,
    OnboardingRecordUpdate,
    RecruitmentChannelCreate,
    RecruitmentChannelUpdate,
    RecruitmentDemandCreate,
    RecruitmentDemandOut,
    RecruitmentDemandUpdate,
    RecruitmentPositionCreate,
    RecruitmentPositionUpdate,
    RecruitmentFunnelOut,
    ResumeCreate,
    ResumeScreeningRequest,
    ResumeSearchParams,
    ResumeUpdate,
    InterviewCreate,
    InterviewUpdate,
    ReferralRuleCreate,
)


# ===========================================================================
# Helper
# ===========================================================================

def _safe_rate(numerator: int, denominator: int) -> Optional[float]:
    """
    安全计算百分比转化率，避免除以零错误。

    Args:
        numerator: 分子（如：面试通过人数）。
        denominator: 分母（如：面试安排人数）。

    Returns:
        百分比数值（保留2位小数），例如 66.67；
        当分母为 0 时返回 None，前端可据此显示 "N/A"。

    示例：
        _safe_rate(10, 20) -> 50.0
        _safe_rate(0, 0)   -> None
    """
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


# ===========================================================================
# RecruitmentChannel Service
# ===========================================================================

class RecruitmentChannelService:
    """
    招聘渠道管理服务。

    招聘渠道代表候选人来源，例如：BOSS直聘、猎头、内部推荐、校园招聘等。
    渠道数据用于简历来源统计和渠道有效性分析（RecruitmentStatsService.get_channel_effectiveness）。
    """

    @staticmethod
    async def list_channels(
        db: AsyncSession,
        *,
        is_active: Optional[bool] = None,
    ) -> Sequence[RecruitmentChannel]:
        """
        查询招聘渠道列表。

        Args:
            db: 异步数据库会话。
            is_active: 可选。True 只返回启用渠道，False 只返回停用渠道，None 返回全部。

        Returns:
            RecruitmentChannel 列表，按 id 升序排列。
        """
        stmt = select(RecruitmentChannel).order_by(RecruitmentChannel.id)
        if is_active is not None:
            stmt = stmt.where(RecruitmentChannel.is_active == is_active)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_channel(db: AsyncSession, channel_id: int) -> RecruitmentChannel:
        """
        按主键查询招聘渠道，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            channel_id: 渠道主键 ID。

        Returns:
            对应的 RecruitmentChannel 对象。

        Raises:
            HTTPException(404): 渠道不存在时抛出。
        """
        result = await db.execute(
            select(RecruitmentChannel).where(RecruitmentChannel.id == channel_id)
        )
        channel = result.scalar_one_or_none()
        if not channel:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"招聘渠道 {channel_id} 不存在",
            )
        return channel

    @staticmethod
    async def create_channel(
        db: AsyncSession, data: RecruitmentChannelCreate
    ) -> RecruitmentChannel:
        """
        创建新招聘渠道。

        Args:
            db: 异步数据库会话。
            data: 渠道创建 DTO，包含渠道名称、描述、是否启用等字段。

        Returns:
            已持久化的 RecruitmentChannel 对象（含自动生成的 id、created_at）。
        """
        channel = RecruitmentChannel(**data.model_dump())
        db.add(channel)
        await db.flush()
        await db.refresh(channel)
        return channel

    @staticmethod
    async def update_channel(
        db: AsyncSession, channel_id: int, data: RecruitmentChannelUpdate
    ) -> RecruitmentChannel:
        """
        更新招聘渠道信息（局部更新）。

        Args:
            db: 异步数据库会话。
            channel_id: 要更新的渠道主键 ID。
            data: 渠道更新 DTO，仅更新请求中传入的字段（exclude_unset=True）。

        Returns:
            更新后的 RecruitmentChannel 对象。

        Raises:
            HTTPException(404): 渠道不存在时抛出（由 get_channel 内部抛出）。
        """
        channel = await RecruitmentChannelService.get_channel(db, channel_id)
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(channel, field, value)
        await db.flush()
        await db.refresh(channel)
        return channel


# ===========================================================================
# RecruitmentPosition Service
# ===========================================================================

class RecruitmentPositionService:
    """职位字典管理服务。"""

    @staticmethod
    async def list_positions(
        db: AsyncSession,
        *,
        keyword: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Sequence[RecruitmentPosition]:
        stmt = select(RecruitmentPosition).order_by(RecruitmentPosition.created_at.desc(), RecruitmentPosition.id.desc())
        if keyword:
            kw = f"%{keyword.strip()}%"
            stmt = stmt.where(
                or_(
                    RecruitmentPosition.position_name.ilike(kw),
                    RecruitmentPosition.job_category.ilike(kw),
                    RecruitmentPosition.job_level.ilike(kw),
                )
            )
        if is_active is not None:
            stmt = stmt.where(RecruitmentPosition.is_active == is_active)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_position(db: AsyncSession, position_id: int) -> RecruitmentPosition:
        result = await db.execute(
            select(RecruitmentPosition).where(RecruitmentPosition.id == position_id)
        )
        position = result.scalar_one_or_none()
        if not position:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"职位 {position_id} 不存在",
            )
        return position

    @staticmethod
    async def create_position(
        db: AsyncSession, data: RecruitmentPositionCreate
    ) -> RecruitmentPosition:
        name = data.position_name.strip()
        exists = await db.execute(
            select(RecruitmentPosition.id).where(RecruitmentPosition.position_name == name)
        )
        if exists.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="职位名称已存在",
            )
        position = RecruitmentPosition(**data.model_dump(), position_name=name)
        db.add(position)
        await db.flush()
        await db.refresh(position)
        return position

    @staticmethod
    async def update_position(
        db: AsyncSession, position_id: int, data: RecruitmentPositionUpdate
    ) -> RecruitmentPosition:
        position = await RecruitmentPositionService.get_position(db, position_id)
        update_data = data.model_dump(exclude_unset=True)

        if "position_name" in update_data and update_data["position_name"] is not None:
            normalized_name = update_data["position_name"].strip()
            exists = await db.execute(
                select(RecruitmentPosition.id).where(
                    and_(
                        RecruitmentPosition.position_name == normalized_name,
                        RecruitmentPosition.id != position_id,
                    )
                )
            )
            if exists.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="职位名称已存在",
                )
            update_data["position_name"] = normalized_name

        for field, value in update_data.items():
            setattr(position, field, value)
        await db.flush()
        await db.refresh(position)
        return position

    @staticmethod
    async def delete_position(db: AsyncSession, position_id: int) -> None:
        position = await RecruitmentPositionService.get_position(db, position_id)
        await db.delete(position)
        await db.flush()


# ===========================================================================
# RecruitmentDemand Service
# ===========================================================================

class RecruitmentDemandService:
    """
    招聘需求申请与审批服务。

    招聘需求（RecruitmentDemand）是招聘流程的起点，由用人部门提交，
    经 HR 或相关负责人审批后，才能正式开始招聘（发布职位、接收简历）。

    状态流转：
        创建时 approval_status=PENDING, status=RECRUITING
        审批通过 → approval_status=APPROVED，status 保持 RECRUITING
        审批拒绝 → approval_status=REJECTED，status=CANCELLED
        招聘完成（接受 Offer 数达到 headcount）→ status=COMPLETED（由 _check_demand_fulfillment 自动触发）

    关键规则：
        - 已审批通过（APPROVED）的需求不可再修改，需重新创建
        - 创建需求时自动通过 maybe_create_approval_instance() 触发审批流（如已配置）
        - headcount 字段记录本次招聘所需人数，用于判断招聘是否完成
    """

    @staticmethod
    async def list_demands(
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        approval_status: Optional[str] = None,
        department_id: Optional[int] = None,
    ) -> tuple[Sequence[RecruitmentDemand], int]:
        """
        分页查询招聘需求列表，支持多条件过滤。

        Args:
            db: 异步数据库会话。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。
            status_filter: 可选，按需求状态筛选（如 "recruiting"/"completed"/"cancelled"）。
            approval_status: 可选，按审批状态筛选（如 "pending"/"approved"/"rejected"）。
            department_id: 可选，按用人部门 ID 筛选。

        Returns:
            (items, total) 元组：
              - items: 当前页需求列表，按创建时间倒序排列。
              - total: 符合过滤条件的总记录数。
        """
        stmt = select(RecruitmentDemand).order_by(RecruitmentDemand.created_at.desc())
        count_stmt = select(func.count(RecruitmentDemand.id))

        conditions: list[Any] = []
        if status_filter:
            conditions.append(RecruitmentDemand.status == status_filter)
        if approval_status:
            conditions.append(RecruitmentDemand.approval_status == approval_status)
        if department_id:
            conditions.append(RecruitmentDemand.department_id == department_id)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def get_demand(db: AsyncSession, demand_id: int) -> RecruitmentDemand:
        """
        按主键查询招聘需求，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            demand_id: 招聘需求主键 ID。

        Returns:
            对应的 RecruitmentDemand 对象。

        Raises:
            HTTPException(404): 需求记录不存在时抛出。
        """
        result = await db.execute(
            select(RecruitmentDemand).where(RecruitmentDemand.id == demand_id)
        )
        demand = result.scalar_one_or_none()
        if not demand:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"招聘需求 {demand_id} 不存在",
            )
        return demand

    @staticmethod
    async def department_headcount(
        db: AsyncSession,
        department_id: int,
        *,
        requested_count: int = 0,
    ) -> DepartmentHeadcountOut:
        """计算部门当前可用于招聘的编制余额。"""
        department_result = await db.execute(
            select(Department).where(Department.id == department_id)
        )
        department = department_result.scalar_one_or_none()
        if department is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"部门 {department_id} 不存在",
            )

        active_statuses = [EmployeeStatus.ACTIVE.value, EmployeeStatus.ON_PROBATION.value]
        onboard_result = await db.execute(
            select(func.count(Employee.id)).where(
                Employee.department_id == department_id,
                Employee.status.in_(active_statuses),
            )
        )
        onboard_count = int(onboard_result.scalar() or 0)

        reserved_result = await db.execute(
            select(func.coalesce(func.sum(RecruitmentDemand.headcount), 0)).where(
                RecruitmentDemand.department_id == department_id,
                RecruitmentDemand.approval_status != ApprovalStatus.REJECTED,
                RecruitmentDemand.status.in_([DemandStatus.RECRUITING, DemandStatus.PAUSED]),
            )
        )
        reserved_count = int(reserved_result.scalar() or 0)
        quota = int(getattr(department, "headcount_quota", 0) or 0)
        requested = max(int(requested_count or 0), 0)
        available = quota - onboard_count - reserved_count

        return DepartmentHeadcountOut(
            department_id=department.id,
            department_name=department.name,
            quota=quota,
            onboard_count=onboard_count,
            reserved_count=reserved_count,
            available_count=available,
            requested_count=requested,
            is_sufficient=available >= requested if requested else available > 0,
        )

    @staticmethod
    async def list_department_headcounts(db: AsyncSession) -> list[DepartmentHeadcountOut]:
        """为管理端编制页返回全量部门余额。"""
        departments_result = await db.execute(
            select(Department)
            .where(Department.is_active == True)
            .order_by(Department.sort_order, Department.id)
        )
        departments = list(departments_result.scalars().all())
        if not departments:
            return []

        department_ids = [int(item.id) for item in departments]
        active_statuses = [EmployeeStatus.ACTIVE.value, EmployeeStatus.ON_PROBATION.value]

        onboard_result = await db.execute(
            select(Employee.department_id, func.count(Employee.id))
            .where(
                Employee.department_id.in_(department_ids),
                Employee.status.in_(active_statuses),
            )
            .group_by(Employee.department_id)
        )
        onboard_map = {
            int(department_id): int(count or 0)
            for department_id, count in onboard_result.all()
            if department_id is not None
        }

        reserved_result = await db.execute(
            select(RecruitmentDemand.department_id, func.coalesce(func.sum(RecruitmentDemand.headcount), 0))
            .where(
                RecruitmentDemand.department_id.in_(department_ids),
                RecruitmentDemand.approval_status != ApprovalStatus.REJECTED,
                RecruitmentDemand.status.in_([DemandStatus.RECRUITING, DemandStatus.PAUSED]),
            )
            .group_by(RecruitmentDemand.department_id)
        )
        reserved_map = {
            int(department_id): int(count or 0)
            for department_id, count in reserved_result.all()
            if department_id is not None
        }

        rows: list[DepartmentHeadcountOut] = []
        for department in departments:
            quota = int(getattr(department, "headcount_quota", 0) or 0)
            onboard_count = onboard_map.get(int(department.id), 0)
            reserved_count = reserved_map.get(int(department.id), 0)
            available = quota - onboard_count - reserved_count
            rows.append(
                DepartmentHeadcountOut(
                    department_id=int(department.id),
                    department_name=str(department.name),
                    quota=quota,
                    onboard_count=onboard_count,
                    reserved_count=reserved_count,
                    available_count=available,
                    requested_count=0,
                    is_sufficient=available > 0,
                )
            )
        return rows

    @staticmethod
    async def submit_business_demand(
        db: AsyncSession,
        data: RecruitmentDemandCreate,
        requester: Employee,
        requester_roles: Optional[set[str]] = None,
    ) -> RecruitmentDemandBusinessSubmitOut:
        """移动端用人负责人提交招聘需求，先校验部门编制余额。"""
        department_id = int(data.department_id)
        effective_roles = requester_roles or {
            str(role or "").strip().lower()
            for role in (getattr(requester, "roles", None) or [])
            if str(role or "").strip()
        }

        if not getattr(requester, "is_superuser", False) and not (effective_roles & {"admin", "hr"}):
            managed_department = await db.execute(
                select(Department.id).where(
                    Department.id == department_id,
                    or_(
                        Department.manager_id == requester.id,
                        Department.id == requester.department_id,
                    ),
                )
            )
            if managed_department.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="只能为本人负责或所在部门提交招聘需求",
                )

        headcount = await RecruitmentDemandService.department_headcount(
            db,
            department_id,
            requested_count=data.headcount,
        )
        if not headcount.is_sufficient:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "当前部门编制不充足",
                    "headcount": headcount.model_dump(),
                },
            )

        demand = await RecruitmentDemandService.create_demand(
            db,
            data,
            requester_id=int(requester.id),
        )
        return RecruitmentDemandBusinessSubmitOut(
            demand=RecruitmentDemandOut.model_validate(demand),
            headcount=headcount,
            approval_message="招聘需求已提交，审批通过后将流转给 HR 执行发布",
        )

    @staticmethod
    async def create_demand(
        db: AsyncSession,
        data: RecruitmentDemandCreate,
        requester_id: int,
    ) -> RecruitmentDemand:
        """
        创建招聘需求申请。

        创建时自动设置初始状态：
          - approval_status = PENDING（待审批）
          - status = RECRUITING（招聘中）

        若系统已配置 "recruitment_demand" 类型的审批流，会通过
        maybe_create_approval_instance() 自动创建审批实例，流转至审批人。

        Args:
            db: 异步数据库会话。
            data: 招聘需求创建 DTO，包含职位名称、用人部门、招聘人数、期望到岗日期等。
            requester_id: 申请人的员工 ID（当前登录用户）。

        Returns:
            已持久化的 RecruitmentDemand 对象。

        调用下游：
            maybe_create_approval_instance() — 软触发审批流（无审批流配置时静默跳过）
        """
        demand = RecruitmentDemand(
            **data.model_dump(),
            requester_id=requester_id,
            approval_status=ApprovalStatus.PENDING,
            status=DemandStatus.RECRUITING,
        )
        db.add(demand)
        await db.flush()
        await db.refresh(demand)

        # 软触发审批流（如配置了招聘需求审批流则自动创建审批实例）
        await maybe_create_approval_instance(
            db,
            module="recruitment_demand",
            business_id=demand.id,
            business_type="recruitment_demand",
            applicant_id=requester_id,
        )

        return demand

    @staticmethod
    async def update_demand(
        db: AsyncSession, demand_id: int, data: RecruitmentDemandUpdate
    ) -> RecruitmentDemand:
        """
        更新招聘需求信息（局部更新）。

        业务规则：已审批通过（APPROVED）的需求不允许修改，需重新提交新需求。

        Args:
            db: 异步数据库会话。
            demand_id: 要更新的需求主键 ID。
            data: 招聘需求更新 DTO（仅更新传入字段）。

        Returns:
            更新后的 RecruitmentDemand 对象。

        Raises:
            HTTPException(404): 需求不存在。
            HTTPException(400): 需求已审批通过，不允许修改。
        """
        demand = await RecruitmentDemandService.get_demand(db, demand_id)
        if demand.approval_status == ApprovalStatus.APPROVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="已审批通过的需求不可修改，请重新创建",
            )
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(demand, field, value)
        await db.flush()
        await db.refresh(demand)
        return demand

    @staticmethod
    async def publish_demand(
        db: AsyncSession,
        demand_id: int,
        data: DemandPublishRequest,
    ) -> DemandPublishOut:
        """将已审批招聘需求发布到配置了 API 的招聘渠道。"""
        demand = await RecruitmentDemandService.get_demand(db, demand_id)
        if demand.approval_status != ApprovalStatus.APPROVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="招聘需求审批通过后才能发布岗位",
            )
        if demand.status not in (DemandStatus.RECRUITING, DemandStatus.PAUSED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前需求状态为「{demand.status}」，不能发布岗位",
            )

        channel = await RecruitmentChannelService.get_channel(db, data.channel_id)
        if not channel.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="招聘渠道已停用",
            )

        config = channel.config_json or {}
        payload: dict[str, Any] = {
            "external_job_id": data.external_job_id,
            "position_name": demand.position_name,
            "department_id": demand.department_id,
            "headcount": demand.headcount,
            "demand_type": demand.demand_type,
            "urgency": demand.urgency,
            "reason": demand.reason,
            "requirements_desc": demand.requirements_desc,
            "salary_range_min": float(demand.salary_range_min) if demand.salary_range_min is not None else None,
            "salary_range_max": float(demand.salary_range_max) if demand.salary_range_max is not None else None,
            "location_id": demand.location_id,
        }
        if data.publish_payload:
            payload.update(data.publish_payload)

        api_url = str(config.get("api_url") or "").strip()
        response_payload: Optional[dict[str, Any]] = None
        external_job_id = data.external_job_id
        if api_url:
            headers = dict(config.get("headers") or {})
            token = config.get("api_token") or config.get("access_token")
            if token and "Authorization" not in headers:
                headers["Authorization"] = f"Bearer {token}"
            timeout_seconds = float(config.get("timeout_seconds") or 10)
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.post(api_url, json=payload, headers=headers)
                    response.raise_for_status()
                    if response.content:
                        parsed = response.json()
                        response_payload = parsed if isinstance(parsed, dict) else {"data": parsed}
                    else:
                        response_payload = {}
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"招聘渠道发布失败：{exc}",
                ) from exc
            if response_payload:
                external_job_id = (
                    external_job_id
                    or response_payload.get("job_id")
                    or response_payload.get("external_job_id")
                )
            message = "岗位已通过渠道 API 发布"
        else:
            message = "渠道未配置 api_url，已生成待发布岗位载荷"

        return DemandPublishOut(
            demand_id=demand.id,
            channel_id=channel.id,
            channel_name=channel.name,
            status="published" if api_url else "ready",
            external_job_id=str(external_job_id) if external_job_id else None,
            message=message,
            request_payload=payload,
            response_payload=response_payload,
        )

    @staticmethod
    async def approve_demand(
        db: AsyncSession,
        demand_id: int,
        data: DemandApprovalRequest,
    ) -> RecruitmentDemand:
        """
        审批招聘需求（通过或拒绝）。

        业务规则：
          - 只有待审批（PENDING）状态的需求可以被审批，已审批的无法重复操作
          - 审批通过（APPROVED）：需求保持 RECRUITING 状态，可正式开始招聘
          - 审批拒绝（REJECTED）：需求状态自动变为 CANCELLED，招聘终止

        Args:
            db: 异步数据库会话。
            demand_id: 要审批的需求主键 ID。
            data: 审批请求 DTO，包含 approval_status（APPROVED/REJECTED）和审批意见。

        Returns:
            审批后的 RecruitmentDemand 对象。

        Raises:
            HTTPException(404): 需求不存在。
            HTTPException(400): 需求不在待审批状态，或审批状态值非法。
        """
        demand = await RecruitmentDemandService.get_demand(db, demand_id)
        if demand.approval_status != ApprovalStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前审批状态为「{demand.approval_status}」，无法再次审批",
            )
        if data.approval_status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="审批状态只能为「已通过」或「已拒绝」",
            )
        demand.approval_status = data.approval_status
        # 审批拒绝时同步将需求状态改为已取消
        if data.approval_status == ApprovalStatus.REJECTED:
            demand.status = DemandStatus.CANCELLED
        await db.flush()
        await db.refresh(demand)
        return demand


# ===========================================================================
# Resume Service
# ===========================================================================

class ResumeService:
    """
    简历管理服务。

    负责候选人简历的全生命周期管理：投递 → 筛选 → 进入面试流程 → 人才库。

    核心业务规则：
      - 简历筛选结果只能为「推荐（RECOMMENDED）」或「不合适（REJECTED）」
      - 只有筛选状态为 RECOMMENDED 的简历才能安排面试（由 InterviewService 校验）
      - 通过 talent_pool 字段标记优秀候选人进入人才库，供后续职位复用
      - 支持批量筛选（bulk_screen）和批量删除（bulk_delete），单条失败不中断整体

    统计方法：
      - get_stats() 提供简历总数、各筛选状态分布、人才库数量、渠道来源分布，
        供管理驾驶舱使用
    """

    @staticmethod
    async def list_resumes(
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        demand_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        screening_status: Optional[str] = None,
        education: Optional[str] = None,
        keyword: Optional[str] = None,
        work_years_min: Optional[int] = None,
        work_years_max: Optional[int] = None,
        created_at_from: Optional[str] = None,
        created_at_to: Optional[str] = None,
        talent_pool: Optional[bool] = None,
    ) -> tuple[Sequence[Resume], int]:
        """
        分页查询简历列表，支持多维度过滤。

        Args:
            db: 异步数据库会话。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。
            demand_id: 可选，按招聘需求 ID 筛选（查看某职位的所有简历）。
            channel_id: 可选，按招聘渠道 ID 筛选。
            screening_status: 可选，按筛选状态筛选（pending/recommended/rejected）。
            education: 可选，按学历筛选（如 "本科"/"硕士"/"博士"）。
            keyword: 可选，关键字模糊搜索候选人姓名（candidate_name）或手机号（phone）。
            work_years_min: 可选，最少工作年限（闭区间）。
            work_years_max: 可选，最多工作年限（闭区间）。
            created_at_from: 可选，简历投递开始日期，格式 "YYYY-MM-DD"（UTC 00:00:00）。
            created_at_to: 可选，简历投递结束日期，格式 "YYYY-MM-DD"（UTC 23:59:59）。
            talent_pool: 可选，True 只查人才库简历，False 只查非人才库简历。

        Returns:
            (items, total) 元组，items 按创建时间倒序。
        """
        stmt = select(Resume).order_by(Resume.created_at.desc())
        count_stmt = select(func.count(Resume.id))

        conditions: list[Any] = []
        if demand_id:
            conditions.append(Resume.demand_id == demand_id)
        if channel_id:
            conditions.append(Resume.channel_id == channel_id)
        if screening_status:
            conditions.append(Resume.screening_status == screening_status)
        if education:
            conditions.append(Resume.education == education)
        if keyword:
            kw = f"%{keyword}%"
            conditions.append(or_(Resume.candidate_name.ilike(kw), Resume.phone.ilike(kw)))
        if work_years_min is not None:
            conditions.append(Resume.work_years >= work_years_min)
        if work_years_max is not None:
            conditions.append(Resume.work_years <= work_years_max)
        if created_at_from:
            dt_from = datetime.strptime(created_at_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            conditions.append(Resume.created_at >= dt_from)
        if created_at_to:
            dt_to = datetime.strptime(created_at_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            conditions.append(Resume.created_at <= dt_to)
        if talent_pool is not None:
            conditions.append(Resume.talent_pool == talent_pool)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def search_resumes(
        db: AsyncSession,
        params: ResumeSearchParams,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[Sequence[Resume], int]:
        """
        高级简历搜索（比 list_resumes 支持更多字段的全文检索）。

        与 list_resumes 的区别：
          - keyword 字段会同时匹配候选人姓名、学校、当前公司、当前职位、专业（5个字段）
          - 参数通过 ResumeSearchParams 对象传入，便于扩展

        Args:
            db: 异步数据库会话。
            params: 搜索参数对象（ResumeSearchParams），包含 keyword、demand_id、
                    channel_id、screening_status、education、work_years_min/max。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。

        Returns:
            (items, total) 元组，items 按创建时间倒序。
        """
        stmt = select(Resume).order_by(Resume.created_at.desc())
        count_stmt = select(func.count(Resume.id))

        conditions: list[Any] = []

        if params.keyword:
            keyword = f"%{params.keyword}%"
            conditions.append(
                or_(
                    Resume.candidate_name.ilike(keyword),
                    Resume.school.ilike(keyword),
                    Resume.current_company.ilike(keyword),
                    Resume.current_position.ilike(keyword),
                    Resume.major.ilike(keyword),
                )
            )
        if params.demand_id:
            conditions.append(Resume.demand_id == params.demand_id)
        if params.channel_id:
            conditions.append(Resume.channel_id == params.channel_id)
        if params.screening_status:
            conditions.append(Resume.screening_status == params.screening_status)
        if params.education:
            conditions.append(Resume.education == params.education)
        if params.work_years_min is not None:
            conditions.append(Resume.work_years >= params.work_years_min)
        if params.work_years_max is not None:
            conditions.append(Resume.work_years <= params.work_years_max)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def get_resume(db: AsyncSession, resume_id: int) -> Resume:
        """
        按主键查询简历，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            resume_id: 简历主键 ID。

        Returns:
            对应的 Resume 对象。

        Raises:
            HTTPException(404): 简历不存在时抛出。
        """
        result = await db.execute(
            select(Resume).where(Resume.id == resume_id)
        )
        resume = result.scalar_one_or_none()
        if not resume:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"简历 {resume_id} 不存在",
            )
        return resume

    @staticmethod
    async def create_resume(db: AsyncSession, data: ResumeCreate) -> Resume:
        """
        创建简历记录（候选人投递）。

        简历创建后，screening_status 默认为 PENDING（待筛选），
        需通过 screen_resume() 更新筛选结果。

        Args:
            db: 异步数据库会话。
            data: 简历创建 DTO，包含候选人姓名、手机、学历、工作年限、
                  所属需求 ID（demand_id）、来源渠道 ID（channel_id）等。

        Returns:
            已持久化的 Resume 对象（含自动生成的 id、created_at）。
        """
        resume = Resume(**data.model_dump())
        db.add(resume)
        await db.flush()
        await db.refresh(resume)
        return resume

    @staticmethod
    async def update_resume(
        db: AsyncSession, resume_id: int, data: ResumeUpdate
    ) -> Resume:
        """
        更新简历信息（局部更新）。

        Args:
            db: 异步数据库会话。
            resume_id: 要更新的简历主键 ID。
            data: 简历更新 DTO，仅更新传入字段（exclude_unset=True）。

        Returns:
            更新后的 Resume 对象。

        Raises:
            HTTPException(404): 简历不存在时抛出。
        """
        resume = await ResumeService.get_resume(db, resume_id)
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(resume, field, value)
        await db.flush()
        await db.refresh(resume)
        return resume

    @staticmethod
    async def screen_resume(
        db: AsyncSession,
        resume_id: int,
        data: ResumeScreeningRequest,
    ) -> Resume:
        """
        对单份简历进行初筛（标记推荐或不合适）。

        业务规则：
          - 筛选结果只能为 RECOMMENDED（推荐进入面试）或 REJECTED（不合适，终止流程）
          - 筛选通过（RECOMMENDED）的简历才能在 InterviewService.create_interview() 中安排面试
          - 可附带 screening_note 记录筛选备注（如不合适的原因）

        Args:
            db: 异步数据库会话。
            resume_id: 要筛选的简历主键 ID。
            data: 筛选请求 DTO，包含 screening_status 和可选的 screening_note。

        Returns:
            更新筛选状态后的 Resume 对象。

        Raises:
            HTTPException(404): 简历不存在。
            HTTPException(400): 筛选状态不是 RECOMMENDED 或 REJECTED。

        被调用：
            bulk_screen() 在批量筛选时逐条调用此方法。
        """
        resume = await ResumeService.get_resume(db, resume_id)
        if data.screening_status not in (
            ScreeningStatus.RECOMMENDED,
            ScreeningStatus.REJECTED,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="筛选结果只能为「推荐」或「不合适」",
            )
        resume.screening_status = data.screening_status
        resume.screening_note = data.screening_note
        await db.flush()
        await db.refresh(resume)
        return resume

    @staticmethod
    async def bulk_screen(
        db: AsyncSession,
        data: BulkScreenRequest,
    ) -> BulkScreenResult:
        """批量筛选简历，逐一操作，捕获单条错误不中断整体"""
        success = 0
        failed = 0
        errors: list[str] = []
        screen_data = ResumeScreeningRequest(
            screening_status=data.screening_status,
            screening_note=data.screening_note,
        )
        for resume_id in data.resume_ids:
            try:
                await ResumeService.screen_resume(db, resume_id, screen_data)
                success += 1
            except HTTPException as exc:
                failed += 1
                errors.append(f"简历 {resume_id}: {exc.detail}")
            except Exception as exc:
                failed += 1
                errors.append(f"简历 {resume_id}: {str(exc)}")
        return BulkScreenResult(success=success, failed=failed, errors=errors)

    @staticmethod
    async def bulk_delete(db: AsyncSession, resume_ids: list[int]) -> int:
        """批量删除简历，忽略不存在的记录"""
        result = await db.execute(delete(Resume).where(Resume.id.in_(resume_ids)))
        return result.rowcount

    @staticmethod
    async def get_stats(db: AsyncSession) -> dict:
        """简历统计：总数、各筛选状态数量、人才库数量、渠道分布"""
        total = await db.scalar(select(func.count(Resume.id)))
        pending = await db.scalar(
            select(func.count(Resume.id)).where(Resume.screening_status == ScreeningStatus.PENDING)
        )
        recommended = await db.scalar(
            select(func.count(Resume.id)).where(Resume.screening_status == ScreeningStatus.RECOMMENDED)
        )
        rejected = await db.scalar(
            select(func.count(Resume.id)).where(Resume.screening_status == ScreeningStatus.REJECTED)
        )
        talent = await db.scalar(
            select(func.count(Resume.id)).where(Resume.talent_pool == True)  # noqa: E712
        )

        # 渠道分布
        channel_result = await db.execute(
            select(RecruitmentChannel.name, func.count(Resume.id))
            .join(Resume, Resume.channel_id == RecruitmentChannel.id, isouter=True)
            .group_by(RecruitmentChannel.id, RecruitmentChannel.name)
            .order_by(func.count(Resume.id).desc())
        )
        channel_dist = [{"name": row[0], "count": row[1]} for row in channel_result.fetchall()]

        return {
            "total": total or 0,
            "pending": pending or 0,
            "recommended": recommended or 0,
            "rejected": rejected or 0,
            "talent_pool": talent or 0,
            "channel_distribution": channel_dist,
        }


# ===========================================================================
# Interview Service
# ===========================================================================

class InterviewService:
    """
    面试管理服务。

    支持多轮面试流程（通过 round 字段区分轮次，如：1=初试、2=技术面、3=HR面）。
    面试状态机：
        UNSCHEDULED（待排期）
        ↓ schedule_interview()
        SCHEDULED（已排期）
        ↓ 面试官手动标记（通过 update_interview）
        IN_PROGRESS / COMPLETED（进行中/已完成）
        ↓ cancel_interview() / reschedule_interview()
        CANCELLED / RESCHEDULED（已取消/已改约）

    关键规则：
      - 只有 screening_status=RECOMMENDED 的简历才能创建面试（在 create_interview 中校验）
      - 已完成（COMPLETED）或已取消（CANCELLED）的面试不能再取消或改约
      - schedule_interview() / cancel_interview() / reschedule_interview() 均返回
        (interview, should_notify) 元组，调用方根据 should_notify 决定是否发送通知
      - 面试评价（InterviewEvaluation）在面试完成后由面试官提交，通过 InterviewEvaluationService 管理
    """

    @staticmethod
    async def list_interviews(
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        resume_id: Optional[int] = None,
        demand_id: Optional[int] = None,
        interviewer_id: Optional[int] = None,
        status_filter: Optional[str] = None,
    ) -> tuple[Sequence[Interview], int]:
        """
        分页查询面试列表，支持多条件过滤。

        Args:
            db: 异步数据库会话。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。
            resume_id: 可选，查询某位候选人的所有面试记录（含多轮）。
            demand_id: 可选，查询某个招聘需求下的所有面试。
            interviewer_id: 可选，查询某位面试官负责的所有面试。
            status_filter: 可选，按面试状态筛选。

        Returns:
            (items, total) 元组，items 按计划面试时间倒序（NULL 排最后）。
        """
        stmt = select(Interview).order_by(*order_by_nulls_last_desc(Interview.scheduled_time))
        count_stmt = select(func.count(Interview.id))

        conditions: list[Any] = []
        if resume_id:
            conditions.append(Interview.resume_id == resume_id)
        if demand_id:
            conditions.append(Interview.demand_id == demand_id)
        if interviewer_id:
            conditions.append(Interview.interviewer_id == interviewer_id)
        if status_filter:
            conditions.append(Interview.status == status_filter)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def get_interview(db: AsyncSession, interview_id: int) -> Interview:
        """
        按主键查询面试记录，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            interview_id: 面试主键 ID。

        Returns:
            对应的 Interview 对象。

        Raises:
            HTTPException(404): 面试不存在时抛出。
        """
        result = await db.execute(
            select(Interview).where(Interview.id == interview_id)
        )
        interview = result.scalar_one_or_none()
        if not interview:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"面试 {interview_id} 不存在",
            )
        return interview

    @staticmethod
    async def create_interview(
        db: AsyncSession, data: InterviewCreate
    ) -> Interview:
        """
        为候选人创建面试记录（初始状态为 UNSCHEDULED，需后续调用 schedule_interview 排期）。

        业务规则：
          - 简历必须存在，且 screening_status 必须为 RECOMMENDED（已筛选推荐）
          - 面试轮次通过 data.round 字段区分（1=初试、2=技术面、3=HR面等）
          - 创建后状态为 UNSCHEDULED，需单独调用 schedule_interview() 设置时间和面试官

        Args:
            db: 异步数据库会话。
            data: 面试创建 DTO，包含 resume_id、demand_id、round（轮次）、
                  interview_type（面试类型：现场/视频/电话）等。

        Returns:
            已持久化的 Interview 对象。

        Raises:
            HTTPException(404): 关联简历不存在。
            HTTPException(400): 简历尚未通过初筛（screening_status != RECOMMENDED）。
        """
        # 校验简历存在且已通过初筛
        resume_result = await db.execute(
            select(Resume).where(Resume.id == data.resume_id)
        )
        resume = resume_result.scalar_one_or_none()
        if not resume:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"简历 {data.resume_id} 不存在",
            )
        if resume.screening_status != ScreeningStatus.RECOMMENDED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有筛选通过(推荐)的简历才能安排面试",
            )
        interview = Interview(**data.model_dump())
        db.add(interview)
        await db.flush()
        await db.refresh(interview)
        return interview

    @staticmethod
    async def update_interview(
        db: AsyncSession, interview_id: int, data: InterviewUpdate
    ) -> Interview:
        """
        更新面试信息（局部更新，通用字段修改接口）。

        适用于更新面试类型、备注等不涉及状态流转的字段。
        涉及状态流转的操作（排期/取消/改约）请使用专用方法。

        Args:
            db: 异步数据库会话。
            interview_id: 要更新的面试主键 ID。
            data: 面试更新 DTO，仅更新传入字段（exclude_unset=True）。

        Returns:
            更新后的 Interview 对象。

        Raises:
            HTTPException(404): 面试不存在时抛出。
        """
        interview = await InterviewService.get_interview(db, interview_id)
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(interview, field, value)
        await db.flush()
        await db.refresh(interview)
        return interview

    @staticmethod
    async def schedule_interview(
        db: AsyncSession,
        interview_id: int,
        data: InterviewScheduleRequest,
    ) -> tuple[Interview, bool]:
        """
        为面试设定时间、面试官和地点（排期操作），状态变为 SCHEDULED。

        只有处于 UNSCHEDULED（待排期）或 RESCHEDULED（已改约）状态的面试才能排期。

        Args:
            db: 异步数据库会话。
            interview_id: 要排期的面试主键 ID。
            data: 排期请求 DTO，包含：
                  - scheduled_time: 面试时间（带时区的 datetime）
                  - interviewer_id: 面试官员工 ID
                  - location: 面试地点（现场面试用）
                  - meeting_url: 视频会议链接（线上面试用）
                  - notify: 是否发送通知给候选人和面试官

        Returns:
            (interview, should_notify) 元组：
              - interview: 已更新状态为 SCHEDULED 的面试对象
              - should_notify: 调用方据此决定是否触发通知逻辑

        Raises:
            HTTPException(404): 面试不存在。
            HTTPException(400): 当前状态不允许排期（非 UNSCHEDULED/RESCHEDULED）。
        """
        interview = await InterviewService.get_interview(db, interview_id)
        if interview.status not in (
            InterviewStatus.UNSCHEDULED,
            InterviewStatus.RESCHEDULED,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前状态为「{interview.status}」，不可排期",
            )
        interview.scheduled_time = data.scheduled_time
        interview.interviewer_id = data.interviewer_id
        interview.location = data.location
        interview.meeting_url = data.meeting_url
        interview.status = InterviewStatus.SCHEDULED
        await db.flush()
        await db.refresh(interview)
        return interview, data.notify

    @staticmethod
    async def cancel_interview(
        db: AsyncSession,
        interview_id: int,
        data: InterviewCancelRequest,
    ) -> tuple[Interview, bool]:
        """
        取消面试，状态变为 CANCELLED。

        业务规则：已完成（COMPLETED）或已取消（CANCELLED）的面试不可再取消。

        Args:
            db: 异步数据库会话。
            interview_id: 要取消的面试主键 ID。
            data: 取消请求 DTO，包含 cancel_reason（取消原因）和 notify（是否通知）。

        Returns:
            (interview, should_notify) 元组，interview 状态已更新为 CANCELLED。

        Raises:
            HTTPException(404): 面试不存在。
            HTTPException(400): 已完成或已取消的面试不可取消。
        """
        interview = await InterviewService.get_interview(db, interview_id)
        if interview.status in (
            InterviewStatus.COMPLETED,
            InterviewStatus.CANCELLED,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前状态为「{interview.status}」，不可取消",
            )
        interview.status = InterviewStatus.CANCELLED
        interview.cancel_reason = data.cancel_reason
        await db.flush()
        await db.refresh(interview)
        return interview, data.notify

    @staticmethod
    async def reschedule_interview(
        db: AsyncSession,
        interview_id: int,
        data: InterviewRescheduleRequest,
    ) -> tuple[Interview, bool]:
        """
        改约面试时间，状态变为 RESCHEDULED（改约后需重新调用 schedule_interview 确认新时间）。

        业务规则：已完成（COMPLETED）或已取消（CANCELLED）的面试不可改约。
        改约后状态变为 RESCHEDULED，候选人或面试官可等待 HR 重新排期。

        Args:
            db: 异步数据库会话。
            interview_id: 要改约的面试主键 ID。
            data: 改约请求 DTO，包含：
                  - cancel_reason: 改约原因（复用 cancel_reason 字段）
                  - scheduled_time: 新的面试时间
                  - location: 新地点（可选，None 则保留原值）
                  - meeting_url: 新会议链接（可选，None 则保留原值）
                  - notify: 是否通知候选人

        Returns:
            (interview, should_notify) 元组，interview 状态已更新为 RESCHEDULED。

        Raises:
            HTTPException(404): 面试不存在。
            HTTPException(400): 已完成或已取消的面试不可改约。
        """
        interview = await InterviewService.get_interview(db, interview_id)
        if interview.status in (
            InterviewStatus.COMPLETED,
            InterviewStatus.CANCELLED,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前状态为「{interview.status}」，不可改约",
            )
        interview.cancel_reason = data.cancel_reason
        interview.scheduled_time = data.scheduled_time
        # location 和 meeting_url 为可选更新，None 表示不改变原值
        if data.location is not None:
            interview.location = data.location
        if data.meeting_url is not None:
            interview.meeting_url = data.meeting_url
        interview.status = InterviewStatus.RESCHEDULED
        await db.flush()
        await db.refresh(interview)
        return interview, data.notify


# ===========================================================================
# InterviewEvaluation Service
# ===========================================================================

class InterviewEvaluationService:
    """
    面试评价管理服务。

    面试评价（InterviewEvaluation）由面试官在面试完成后提交，记录对候选人的综合评分。
    评价结果（overall_result）决定候选人能否进入下一轮面试或获得 Offer。

    关键规则：
      - 只有状态为 COMPLETED 的面试才能提交评价
      - 每场面试只能有一条评价记录（唯一约束，防止重复提交）
      - 评价记录中的 interviewer_id 由调用方（API 层）从当前登录用户注入，
        而非由客户端传入，确保评价归属准确
      - list_evaluations_by_resume() 按面试轮次（round）排序，便于查看完整面试历程
    """

    @staticmethod
    async def create_evaluation(
        db: AsyncSession,
        data: InterviewEvaluationCreate,
        interviewer_id: int,
    ) -> InterviewEvaluation:
        """
        提交面试评价。

        业务规则：
          - 面试状态必须为 COMPLETED，否则拒绝（面试未结束不能评价）
          - 一场面试只能有一条评价，重复提交返回 409 Conflict

        Args:
            db: 异步数据库会话。
            data: 面试评价创建 DTO，包含：
                  - interview_id: 关联面试 ID
                  - overall_result: 综合结论（如 RECOMMEND/HOLD/REJECT）
                  - score: 评分（可选）
                  - strengths: 优势描述
                  - weaknesses: 不足描述
                  - comment: 其他备注
            interviewer_id: 当前面试官的员工 ID（由 API 层从 JWT 注入，非客户端传入）。

        Returns:
            已持久化的 InterviewEvaluation 对象。

        Raises:
            HTTPException(404): 面试不存在（由 InterviewService.get_interview 抛出）。
            HTTPException(400): 面试未完成，不能提交评价。
            HTTPException(409): 该面试已有评价记录，不可重复提交。

        调用下游：
            InterviewService.get_interview() — 校验面试存在且已完成
        """
        # 校验面试存在且已完成
        interview = await InterviewService.get_interview(db, data.interview_id)
        if interview.status != InterviewStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有已完成的面试才能提交评价",
            )
        # 防止重复评价（一场面试唯一一条评价）
        existing = await db.execute(
            select(InterviewEvaluation).where(
                InterviewEvaluation.interview_id == data.interview_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该面试已有评价记录，不可重复提交",
            )
        evaluation = InterviewEvaluation(
            **data.model_dump(),
            interviewer_id=interviewer_id,
        )
        db.add(evaluation)
        await db.flush()
        await db.refresh(evaluation)
        return evaluation

    @staticmethod
    async def get_evaluation_by_interview(
        db: AsyncSession, interview_id: int
    ) -> InterviewEvaluation:
        """
        查询某场面试的评价记录，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            interview_id: 面试主键 ID。

        Returns:
            对应的 InterviewEvaluation 对象。

        Raises:
            HTTPException(404): 该面试暂无评价记录时抛出。
        """
        result = await db.execute(
            select(InterviewEvaluation).where(
                InterviewEvaluation.interview_id == interview_id
            )
        )
        evaluation = result.scalar_one_or_none()
        if not evaluation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"面试 {interview_id} 暂无评价记录",
            )
        return evaluation

    @staticmethod
    async def list_evaluations_by_resume(
        db: AsyncSession, resume_id: int
    ) -> Sequence[InterviewEvaluation]:
        """
        查询某位候选人所有面试轮次的评价记录，按面试轮次（round）升序排列。

        用于查看候选人完整面试历程（如初试评价、技术面评价、HR面评价），
        供 HR 综合判断是否发放 Offer。

        Args:
            db: 异步数据库会话。
            resume_id: 候选人简历主键 ID。

        Returns:
            该候选人所有面试评价列表，按 Interview.round 升序（第一轮到最后一轮）。
            若候选人暂无评价记录，返回空列表。
        """
        stmt = (
            select(InterviewEvaluation)
            .join(Interview, InterviewEvaluation.interview_id == Interview.id)
            .where(Interview.resume_id == resume_id)
            .order_by(Interview.round)
        )
        result = await db.execute(stmt)
        return result.scalars().all()


# ===========================================================================
# Offer Service
# ===========================================================================

class OfferService:
    """
    Offer 管理服务。

    管理候选人 Offer 从创建到最终确认的完整生命周期。

    Offer 双状态设计：
      1. approval_status — 内部审批状态（PENDING → APPROVED/REJECTED）
         HR 创建 Offer 草稿后，需经过内部审批才能发送给候选人
      2. offer_status — Offer 实体状态（DRAFT → SENT → ACCEPTED/REJECTED/WITHDRAWN）
         反映 Offer 在候选人侧的流转状态

    完整流转路径：
        create_offer()    → approval_status=PENDING, offer_status=DRAFT
        approve_offer()   → approval_status=APPROVED（审批通过后才能发送）
        send_offer()      → offer_status=SENT（发送 PDF 或签署链接给候选人）
        candidate_confirm_offer()
          → 接受: offer_status=ACCEPTED，触发 _check_demand_fulfillment() 检查招聘完成
          → 拒绝: offer_status=REJECTED
        revoke_offer()    → offer_status=WITHDRAWN（已发送但候选人未确认前可撤回）

    关键规则：
      - 每位候选人（resume_id）只能有一个 Offer（唯一约束，重复创建返回 409）
      - 只有 APPROVED 状态的 Offer 才能发送
      - 只有 SENT 状态的 Offer 才能被候选人确认（接受或拒绝）
      - 已接受（ACCEPTED）或已撤回（WITHDRAWN）的 Offer 不可再撤回
    """

    @staticmethod
    async def list_offers(
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        demand_id: Optional[int] = None,
        approval_status: Optional[str] = None,
        offer_status: Optional[str] = None,
    ) -> tuple[Sequence[Offer], int]:
        """
        分页查询 Offer 列表，支持多条件过滤。

        Args:
            db: 异步数据库会话。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。
            demand_id: 可选，按招聘需求 ID 筛选（查看某职位所有 Offer）。
            approval_status: 可选，按内部审批状态筛选（pending/approved/rejected）。
            offer_status: 可选，按 Offer 实体状态筛选（draft/sent/accepted/rejected/withdrawn）。

        Returns:
            (items, total) 元组，items 按创建时间倒序。
        """
        stmt = select(Offer).order_by(Offer.created_at.desc())
        count_stmt = select(func.count(Offer.id))

        conditions: list[Any] = []
        if demand_id:
            conditions.append(Offer.demand_id == demand_id)
        if approval_status:
            conditions.append(Offer.approval_status == approval_status)
        if offer_status:
            conditions.append(Offer.offer_status == offer_status)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def get_offer(db: AsyncSession, offer_id: int) -> Offer:
        """
        按主键查询 Offer，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            offer_id: Offer 主键 ID。

        Returns:
            对应的 Offer 对象。

        Raises:
            HTTPException(404): Offer 不存在时抛出。
        """
        result = await db.execute(
            select(Offer).where(Offer.id == offer_id)
        )
        offer = result.scalar_one_or_none()
        if not offer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Offer {offer_id} 不存在",
            )
        return offer

    @staticmethod
    async def create_offer(db: AsyncSession, data: OfferCreate) -> Offer:
        """
        创建 Offer 草稿记录。

        创建时 offer_status=DRAFT（草稿），approval_status=PENDING（待审批）。
        一位候选人只能有一个 Offer 记录，重复创建返回 409。

        Args:
            db: 异步数据库会话。
            data: Offer 创建 DTO，包含：
                  - resume_id: 候选人简历 ID（唯一约束）
                  - demand_id: 关联招聘需求 ID
                  - salary、position_name、start_date 等薪酬和职位信息

        Returns:
            已持久化的 Offer 对象（DRAFT 状态）。

        Raises:
            HTTPException(409): 该候选人已有 Offer 记录时抛出。
        """
        # 校验一位候选人只能有一个 Offer
        existing = await db.execute(
            select(Offer).where(Offer.resume_id == data.resume_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该候选人已有Offer记录",
            )
        offer = Offer(**data.model_dump())
        db.add(offer)
        await db.flush()
        await db.refresh(offer)
        return offer

    @staticmethod
    async def update_offer(
        db: AsyncSession, offer_id: int, data: OfferUpdate
    ) -> Offer:
        """
        更新 Offer 内容（局部更新）。

        只允许修改处于 DRAFT（草稿）状态的 Offer，已发送或已确认的不可修改。

        Args:
            db: 异步数据库会话。
            offer_id: 要更新的 Offer 主键 ID。
            data: Offer 更新 DTO，仅更新传入字段（exclude_unset=True）。

        Returns:
            更新后的 Offer 对象。

        Raises:
            HTTPException(404): Offer 不存在。
            HTTPException(400): Offer 不在 DRAFT 状态，不允许修改。
        """
        offer = await OfferService.get_offer(db, offer_id)
        if offer.offer_status not in (OfferStatus.DRAFT,):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有待发送状态的Offer可以修改",
            )
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(offer, field, value)
        await db.flush()
        await db.refresh(offer)
        return offer

    @staticmethod
    async def approve_offer(
        db: AsyncSession,
        offer_id: int,
        data: OfferApprovalRequest,
    ) -> Offer:
        """
        审批 Offer（通过或拒绝）。

        只有处于 PENDING（待审批）状态的 Offer 才能被审批。
        审批通过后，可调用 send_offer() 将 Offer 发送给候选人。

        Args:
            db: 异步数据库会话。
            offer_id: 要审批的 Offer 主键 ID。
            data: 审批请求 DTO，包含 approval_status（APPROVED/REJECTED）和审批意见。

        Returns:
            审批后的 Offer 对象（approval_status 已更新）。

        Raises:
            HTTPException(404): Offer 不存在。
            HTTPException(400): Offer 不在 PENDING 状态，或审批状态值非法。

        被调用：
            bulk_action() 在批量审批时逐条调用此方法。
        """
        offer = await OfferService.get_offer(db, offer_id)
        if offer.approval_status != OfferApprovalStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前审批状态为「{offer.approval_status}」，无法再次审批",
            )
        if data.approval_status not in (
            OfferApprovalStatus.APPROVED,
            OfferApprovalStatus.REJECTED,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="审批状态只能为「已通过」或「已拒绝」",
            )
        offer.approval_status = data.approval_status
        await db.flush()
        await db.refresh(offer)
        return offer

    @staticmethod
    async def send_offer(
        db: AsyncSession,
        offer_id: int,
        data: OfferSendRequest,
    ) -> tuple[Offer, bool]:
        """
        发送 Offer 给候选人，offer_status 变为 SENT。

        前置条件：
          - approval_status 必须为 APPROVED（已内部审批通过）
          - offer_status 必须为 DRAFT（草稿，尚未发送）

        发送时可同时更新 Offer 的 PDF 文件链接和电子签署链接。

        Args:
            db: 异步数据库会话。
            offer_id: 要发送的 Offer 主键 ID。
            data: 发送请求 DTO，包含：
                  - pdf_url: Offer PDF 文件链接（可选）
                  - sign_url: 电子签署链接（可选）
                  - notify: 是否通过系统通知候选人

        Returns:
            (offer, should_notify) 元组，offer 的 offer_status 已更新为 SENT。

        Raises:
            HTTPException(404): Offer 不存在。
            HTTPException(400): Offer 未审批通过，或已发送/已被撤回。
        """
        offer = await OfferService.get_offer(db, offer_id)
        if offer.approval_status != OfferApprovalStatus.APPROVED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Offer尚未审批通过，不可发送",
            )
        if offer.offer_status != OfferStatus.DRAFT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前Offer状态为「{offer.offer_status}」，不可发送",
            )
        # 可选更新 PDF 和签署链接
        if data.pdf_url:
            offer.pdf_url = data.pdf_url
        if data.sign_url:
            offer.sign_url = data.sign_url
        offer.offer_status = OfferStatus.SENT
        await db.flush()
        await db.refresh(offer)
        return offer, data.notify

    @staticmethod
    async def revoke_offer(db: AsyncSession, offer_id: int) -> Offer:
        """
        撤回 Offer，offer_status 变为 WITHDRAWN。

        适用场景：Offer 已发送但候选人尚未确认前，HR 因薪资调整等原因需要撤回重发。

        业务规则：已接受（ACCEPTED）或已撤回（WITHDRAWN）的 Offer 不可再次撤回。

        Args:
            db: 异步数据库会话。
            offer_id: 要撤回的 Offer 主键 ID。

        Returns:
            撤回后的 Offer 对象（offer_status=WITHDRAWN）。

        Raises:
            HTTPException(404): Offer 不存在。
            HTTPException(400): 已接受或已撤回的 Offer 不可撤回。

        被调用：
            bulk_action() 在批量撤回时逐条调用此方法。
        """
        offer = await OfferService.get_offer(db, offer_id)
        if offer.offer_status in (OfferStatus.ACCEPTED, OfferStatus.WITHDRAWN):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前Offer状态为「{offer.offer_status}」，不可撤回",
            )
        offer.offer_status = OfferStatus.WITHDRAWN
        await db.flush()
        await db.refresh(offer)
        return offer

    @staticmethod
    async def candidate_confirm_offer(
        db: AsyncSession,
        offer_id: int,
        data: OfferCandidateConfirmRequest,
    ) -> Offer:
        """
        记录候选人对 Offer 的确认结果（接受或拒绝）。

        只有处于 SENT（已发送）状态的 Offer 才能被候选人确认。
        候选人接受后，系统自动触发招聘完成检查（_check_demand_fulfillment），
        若已接受 Offer 数量达到招聘需求的 headcount，则将需求标记为 COMPLETED。

        Args:
            db: 异步数据库会话。
            offer_id: 候选人确认的 Offer 主键 ID。
            data: 候选人确认请求 DTO，包含：
                  - accepted: bool，True=接受，False=拒绝

        Returns:
            更新后的 Offer 对象（offer_status=ACCEPTED 或 REJECTED）。

        Raises:
            HTTPException(404): Offer 不存在。
            HTTPException(400): Offer 未处于 SENT 状态，不可确认。

        调用下游：
            _check_demand_fulfillment() — 候选人接受后检查招聘需求是否完成
        """
        offer = await OfferService.get_offer(db, offer_id)
        if offer.offer_status != OfferStatus.SENT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前Offer状态为「{offer.offer_status}」，不可确认",
            )
        offer.offer_status = (
            OfferStatus.ACCEPTED if data.accepted else OfferStatus.REJECTED
        )
        # 候选人接受 Offer 后，检查招聘需求的招聘名额是否已满员
        if data.accepted:
            await _check_demand_fulfillment(db, offer.demand_id)
            await _ensure_onboarding_record_for_offer(db, offer)
        await db.flush()
        await db.refresh(offer)
        return offer

    @staticmethod
    async def bulk_action(
        db: AsyncSession,
        data: BulkOfferActionRequest,
    ) -> BulkOfferActionResult:
        """
        批量操作 Offer（批量审批通过 或 批量撤回）。

        逐条处理每个 Offer，单条失败（如状态不允许）不会中断整体批量操作，
        错误信息会被收集在返回结果的 errors 列表中。

        支持的操作（data.action）：
          - "approve": 批量审批通过 Offer（调用 approve_offer）
          - "revoke": 批量撤回 Offer（调用 revoke_offer）
          - 其他值: 记录错误，跳过该条目

        Args:
            db: 异步数据库会话。
            data: 批量操作请求 DTO，包含：
                  - offer_ids: 要操作的 Offer ID 列表
                  - action: 操作类型（"approve" 或 "revoke"）
                  - comment: 审批意见（仅 approve 时使用）

        Returns:
            BulkOfferActionResult，包含：
              - success: 成功处理的数量
              - failed: 失败的数量
              - errors: 错误详情列表（格式："Offer {id}: {原因}"）

        调用下游：
            approve_offer() / revoke_offer() — 逐条调用
        """
        success = 0
        failed = 0
        errors: list[str] = []
        for offer_id in data.offer_ids:
            try:
                if data.action == "approve":
                    approval_data = OfferApprovalRequest(
                        approval_status=OfferApprovalStatus.APPROVED,
                        comment=data.comment,
                    )
                    await OfferService.approve_offer(db, offer_id, approval_data)
                elif data.action == "revoke":
                    await OfferService.revoke_offer(db, offer_id)
                else:
                    failed += 1
                    errors.append(f"Offer {offer_id}: 不支持的操作「{data.action}」")
                    continue
                success += 1
            except HTTPException as exc:
                failed += 1
                errors.append(f"Offer {offer_id}: {exc.detail}")
            except Exception as exc:
                failed += 1
                errors.append(f"Offer {offer_id}: {str(exc)}")
        return BulkOfferActionResult(success=success, failed=failed, errors=errors)


async def _check_demand_fulfillment(db: AsyncSession, demand_id: int) -> None:
    """
    检查招聘需求是否已满员，满员则自动将需求标记为 COMPLETED。

    在候选人接受 Offer（candidate_confirm_offer 中 accepted=True）后自动调用。
    业务逻辑：当某招聘需求下已接受（ACCEPTED）的 Offer 数量 >= 需求的招聘人数（headcount）时，
    将需求状态自动设置为 COMPLETED（招聘完成）。

    Args:
        db: 异步数据库会话。
        demand_id: 招聘需求主键 ID。

    注意：
        - 若 demand_id 对应的需求不存在，静默返回（不抛异常），避免影响 Offer 确认主流程。
        - 此函数为模块私有函数，仅由 candidate_confirm_offer() 调用。
    """
    demand_result = await db.execute(
        select(RecruitmentDemand).where(RecruitmentDemand.id == demand_id)
    )
    demand = demand_result.scalar_one_or_none()
    if not demand:
        return
    accepted_count_result = await db.execute(
        select(func.count(Offer.id)).where(
            and_(
                Offer.demand_id == demand_id,
                Offer.offer_status == OfferStatus.ACCEPTED,
            )
        )
    )
    accepted_count = accepted_count_result.scalar() or 0
    if accepted_count >= demand.headcount:
        demand.status = DemandStatus.COMPLETED


async def _ensure_onboarding_record_for_offer(
    db: AsyncSession, offer: Offer
) -> Optional[OnboardingRecord]:
    existing_result = await db.execute(
        select(OnboardingRecord).where(OnboardingRecord.offer_id == offer.id)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return existing

    resume_result = await db.execute(
        select(Resume).where(Resume.id == offer.resume_id)
    )
    resume = resume_result.scalar_one_or_none()
    if resume is None:
        return None

    record = OnboardingRecord(
        offer_id=offer.id,
        candidate_name=resume.candidate_name,
        position_name=offer.position_name,
        department_id=offer.department_id,
        planned_date=offer.start_date,
        status=OnboardingStatus.PENDING,
        checklist={
            "签署劳动合同": False,
            "资料核验": False,
            "系统账号开通": False,
            "设备发放": False,
        },
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def _generate_next_employee_no(db: AsyncSession) -> str:
    result = await db.execute(
        select(Employee.employee_no)
        .where(Employee.employee_no.like("TG%"))
        .order_by(Employee.employee_no.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    if latest:
        digits = "".join(ch for ch in str(latest) if ch.isdigit())
        if digits:
            return f"TG{int(digits) + 1:06d}"
    count_result = await db.execute(select(func.count()).select_from(Employee))
    seq = (count_result.scalar() or 0) + 1
    return f"TG{seq:06d}"


async def _materialize_employee_from_onboarding(
    db: AsyncSession, record: OnboardingRecord
) -> Employee:
    offer_result = await db.execute(select(Offer).where(Offer.id == record.offer_id))
    offer = offer_result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="关联 Offer 不存在")

    resume_result = await db.execute(select(Resume).where(Resume.id == offer.resume_id))
    resume = resume_result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=404, detail="关联简历不存在")

    conditions = [Employee.phone == resume.phone]
    if resume.email:
        conditions.append(Employee.email == resume.email)
    existing_result = await db.execute(
        select(Employee).where(or_(*conditions)).limit(1)
    )
    existing_employee = existing_result.scalar_one_or_none()
    if existing_employee:
        return existing_employee

    hire_date = record.actual_date or record.planned_date or offer.start_date or date.today()
    probation_months = max(int(offer.probation_months or 0), 0)
    has_probation = probation_months > 0
    probation_end_date = (
        hire_date + timedelta(days=30 * probation_months)
        if has_probation
        else None
    )
    employee = Employee(
        employee_no=await _generate_next_employee_no(db),
        name=resume.candidate_name,
        phone=resume.phone,
        email=resume.email,
        gender=resume.gender,
        birth_date=resume.birth_date,
        education=resume.education,
        school=resume.school,
        major=resume.major,
        department_id=record.department_id or offer.department_id,
        location_id=offer.location_id,
        position=record.position_name or offer.position_name,
        employment_type=(
            EmploymentType.PROBATION.value if has_probation else EmploymentType.REGULAR.value
        ),
        status=(
            EmployeeStatus.ON_PROBATION.value if has_probation else EmployeeStatus.ACTIVE.value
        ),
        hire_date=hire_date,
        probation_end_date=probation_end_date,
        probation_salary=(
            Decimal(str(offer.salary_base)) * Decimal(str(offer.probation_salary_ratio))
            if has_probation
            else None
        ),
        social_insurance_base=Decimal(str(offer.salary_base)),
        company="示例公司",
        company_id=None,
        hashed_password=get_password_hash(DEFAULT_INITIAL_PASSWORD),
        is_active=True,
    )
    db.add(employee)
    await db.flush()
    await db.refresh(employee)
    return employee


# ===========================================================================
# InternalReferral Service
# ===========================================================================

class InternalReferralService:
    """
    员工内推管理服务。

    管理员工推荐候选人的完整流程，包含：
      - 内推记录的 CRUD
      - 候选人状态与招聘流程的同步（sync_candidate_status）
      - 两期奖金自动发放流程：
          第一期（入职奖金）: 候选人入职后，奖金进入待发放状态，并触发审批通知
          第二期（转正奖金）: 候选人转正后，第二期奖金进入待发放状态
      - 批量标记奖金已发放（bulk_mark_paid），并发送站内通知给推荐人
      - 内推排行榜（get_leaderboard）：按年/月统计推荐数量和成功入职数量
      - Excel 导出（export_excel）：导出内推记录供 HR 离线处理

    内推候选人状态流转（candidate_status 字段）：
        简历筛选 → 面试中 → 已录用 → 已入职 / 不合适

    付款状态（first_payment_status / second_payment_status）：
        待发放 → 已发放

    关键业务规则：
      - 一份简历只能关联一条内推记录（resume_id 唯一约束）
      - 首次付款标记为"已发放"时，自动记录 paid_at 时间戳
      - 候选人入职后自动触发奖金审批通知（_trigger_bonus_approval），
        通知 HR 和推荐人，通知失败不影响主流程
    """

    @staticmethod
    async def list_referrals(
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        referrer_id: Optional[int] = None,
        candidate_status: Optional[str] = None,
        payment_status: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        keyword: Optional[str] = None,
        year: Optional[int] = None,
    ) -> tuple[Sequence[InternalReferral], int]:
        """
        分页查询内推记录列表，并注入关联的推荐人信息和职位名称。

        查询后会对每条记录进行数据注入（N+1 查询，数据量小时可接受）：
          - referrer_name: 推荐人姓名（从 Employee 表获取）
          - referrer_department: 推荐人所在部门名称（从 Department 表获取）
          - position_name: 推荐职位名称（从 Resume→RecruitmentDemand 获取）
          - candidate_name: 候选人姓名（优先取 referral.candidate_name，
                            若为空则回退到关联简历的 candidate_name）

        Args:
            db: 异步数据库会话。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。
            referrer_id: 可选，按推荐人员工 ID 筛选（员工查看自己的内推记录）。
            candidate_status: 可选，按候选人状态筛选（简历筛选/面试中/已录用/已入职/不合适）。
            payment_status: 可选，按第一期付款状态筛选（待发放/已发放）。
            date_from: 可选，内推记录创建开始日期（闭区间）。
            date_to: 可选，内推记录创建结束日期（闭区间，包含当天 23:59:59）。
            keyword: 可选，按候选人姓名模糊搜索。
            year: 可选，按内推记录创建年份筛选（用于排行榜统计）。

        Returns:
            (items, total) 元组，items 按创建时间倒序，已注入关联字段。
        """
        from app.models.employee import Employee as EmployeeModel
        from app.models.organization import Department

        stmt = select(InternalReferral).order_by(InternalReferral.created_at.desc())
        count_stmt = select(func.count(InternalReferral.id))

        filters = []
        if referrer_id:
            filters.append(InternalReferral.referrer_id == referrer_id)
        if candidate_status:
            filters.append(InternalReferral.candidate_status == candidate_status)
        if payment_status:
            filters.append(InternalReferral.first_payment_status == payment_status)
        if date_from:
            filters.append(InternalReferral.created_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            filters.append(InternalReferral.created_at <= datetime.combine(date_to, datetime.max.time()))
        if keyword:
            filters.append(
                or_(
                    InternalReferral.candidate_name.ilike(f"%{keyword}%"),
                )
            )
        if year:
            filters.append(func.extract("year", InternalReferral.created_at) == year)

        if filters:
            stmt = stmt.where(and_(*filters))
            count_stmt = count_stmt.where(and_(*filters))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        items = list(result.scalars().all())

        # Inject related fields: referrer_name, referrer_department, position_name, candidate_name
        for item in items:
            # referrer info
            referrer = await db.get(EmployeeModel, item.referrer_id)
            if referrer:
                item.referrer_name = getattr(referrer, 'name', None)
                dept_id = getattr(referrer, 'department_id', None)
                if dept_id:
                    dept = await db.get(Department, dept_id)
                    item.referrer_department = dept.name if dept else None
                else:
                    item.referrer_department = None
            else:
                item.referrer_name = None
                item.referrer_department = None
            # position_name from resume -> demand
            if item.resume and item.resume.demand:
                item.position_name = item.resume.demand.position_name
            else:
                item.position_name = None
            # candidate_name: prefer referral.candidate_name, fallback resume
            if not item.candidate_name and item.resume:
                item.candidate_name = item.resume.candidate_name

        return items, total

    @staticmethod
    async def get_referral(db: AsyncSession, referral_id: int) -> InternalReferral:
        """
        按主键查询内推记录，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            referral_id: 内推记录主键 ID。

        Returns:
            对应的 InternalReferral 对象。

        Raises:
            HTTPException(404): 内推记录不存在时抛出。
        """
        result = await db.execute(
            select(InternalReferral).where(InternalReferral.id == referral_id)
        )
        referral = result.scalar_one_or_none()
        if not referral:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"内推记录 {referral_id} 不存在",
            )
        return referral

    @staticmethod
    async def create_referral(
        db: AsyncSession, data: InternalReferralCreate
    ) -> InternalReferral:
        """
        创建内推记录（员工推荐候选人）。

        业务规则：一份简历只能关联一条内推记录（resume_id 唯一约束）。

        Args:
            db: 异步数据库会话。
            data: 内推记录创建 DTO，包含：
                  - referrer_id: 推荐人员工 ID
                  - resume_id: 关联的候选人简历 ID（唯一约束）
                  - candidate_name、candidate_phone: 候选人基本信息
                  - reward_amount: 奖励金额（根据 ReferralRule 配置）
                  - notes: 推荐备注

        Returns:
            已持久化的 InternalReferral 对象。

        Raises:
            HTTPException(409): 该简历已关联内推记录时抛出。
        """
        # 校验同一份简历不能重复关联内推记录
        existing = await db.execute(
            select(InternalReferral).where(
                InternalReferral.resume_id == data.resume_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该简历已关联内推记录",
            )
        referral = InternalReferral(**data.model_dump())
        db.add(referral)
        await db.flush()
        await db.refresh(referral)
        return referral

    @staticmethod
    async def update_referral(
        db: AsyncSession, referral_id: int, data: InternalReferralUpdate
    ) -> InternalReferral:
        """
        更新内推记录（局部更新）。

        特殊业务规则：
          - 当 first_payment_status 更新为"已发放"且之前未记录发放时间（paid_at 为 None）时，
            自动将当前 UTC 时间写入 paid_at 字段，确保发放时间的准确性。

        Args:
            db: 异步数据库会话。
            referral_id: 要更新的内推记录主键 ID。
            data: 内推记录更新 DTO，常见更新字段：
                  - candidate_status: 候选人进展状态
                  - first_payment_status / second_payment_status: 奖金发放状态
                  - notes: 备注

        Returns:
            更新后的 InternalReferral 对象。

        Raises:
            HTTPException(404): 内推记录不存在时抛出。
        """
        referral = await InternalReferralService.get_referral(db, referral_id)
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(referral, field, value)
        # 首次标记发放时自动记录发放时间戳（避免漏填）
        if data.first_payment_status == "已发放" and referral.paid_at is None:
            referral.paid_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(referral)
        return referral

    @staticmethod
    async def sync_candidate_status(db: AsyncSession, resume_id: int, new_status: str):
        """
        当招聘流程状态发生变化时，同步更新对应内推记录的候选人状态。

        由招聘流程的其他服务（如 InterviewService、OfferService）在关键节点调用，
        确保内推记录中的候选人状态与招聘系统保持一致。

        状态映射规则（STATUS_MAP）：
          - 待筛选/筛选中/已推荐 → 简历筛选
          - 面试中              → 面试中
          - 面试通过/已录用     → 已录用
          - 已入职             → 已入职（同时触发奖金审批通知）
          - 不合适/已拒绝      → 不合适

        副作用：
          候选人状态变为「已入职」且奖金为待发放状态时，
          自动调用 _trigger_bonus_approval() 发送奖金审批通知。

        Args:
            db: 异步数据库会话。
            resume_id: 候选人简历 ID（通过此 ID 查找关联的内推记录）。
            new_status: 招聘系统中的新状态字符串。

        注意：若该简历无关联内推记录，静默返回（不抛异常）。
        """
        STATUS_MAP = {
            "待筛选": "简历筛选",
            "筛选中": "简历筛选",
            "已推荐": "简历筛选",
            "面试中": "面试中",
            "面试通过": "已录用",
            "已录用": "已录用",
            "已入职": "已入职",
            "不合适": "不合适",
            "已拒绝": "不合适",
        }
        mapped = STATUS_MAP.get(new_status, new_status)
        result = await db.execute(
            select(InternalReferral).where(InternalReferral.resume_id == resume_id)
        )
        referral = result.scalars().first()
        if referral:
            referral.candidate_status = mapped
            # 如果候选人已入职且奖金审批未发起，自动发起审批
            if mapped == "已入职" and referral.first_payment_status == "待发放":
                await InternalReferralService._trigger_bonus_approval(db, referral)

    @staticmethod
    async def _trigger_bonus_approval(db: AsyncSession, referral: "InternalReferral"):
        """
        候选人入职后，自动发送内推奖金审批通知。

        发送两条站内通知（Notification）：
          1. 给 HR/Admin 人员：提醒审批奖金发放（包含奖金金额）
          2. 给推荐人：告知候选人已入职，奖金审批中（激励推荐人持续参与内推）

        查找 HR 逻辑：取 EmployeeRole 表中 role_name 为 "hr" 或 "admin" 且 is_active=True 的第一条记录。

        Args:
            db: 异步数据库会话。
            referral: 关联的 InternalReferral 对象（包含 referrer_id、candidate_name、reward_amount）。

        注意：
          - 此方法被 sync_candidate_status() 调用，通知失败（任何异常）会被静默捕获（pass），
            不影响主业务流程。
          - 通知类型（notif_type）为 "referral_bonus"，可通过此字段在前端过滤显示。
        """
        try:
            from app.models.notification import Notification
            from app.models.employee_role import EmployeeRole
            # 查询 HR 人员（role=hr 或 admin 的第一个员工）
            hr_role_q = await db.execute(
                select(EmployeeRole).where(
                    EmployeeRole.role_name.in_(["hr", "admin"]),
                    EmployeeRole.is_active == True
                ).limit(1)
            )
            hr_role = hr_role_q.scalars().first()
            if hr_role:
                # 发通知给 HR
                notif = Notification(
                    recipient_id=hr_role.employee_id,
                    sender_id=None,
                    title="内推奖金审批提醒",
                    content=f"候选人已入职，内推奖金（¥{float(referral.reward_amount or 0):.2f}）待审批发放，请及时处理。",
                    notif_type="referral_bonus",
                    ref_type="internal_referral",
                    ref_id=referral.id,
                )
                db.add(notif)
            # 同时通知推荐人
            referrer_notif = Notification(
                recipient_id=referral.referrer_id,
                sender_id=None,
                title="您的内推候选人已入职",
                content=f"恭喜！您推荐的候选人{referral.candidate_name or ''}已正式入职，奖金（¥{float(referral.reward_amount or 0):.2f}）正在审批中，请关注后续发放通知。",
                notif_type="referral_bonus",
                ref_type="internal_referral",
                ref_id=referral.id,
            )
            db.add(referrer_notif)
        except Exception:
            pass  # 通知失败不影响主流程

    @staticmethod
    async def get_stats(db: AsyncSession) -> dict:
        """
        获取内推统计数据（管理驾驶舱使用）。

        统计维度：
          - month_total: 本月新增内推记录数（以当前月第一天为起点）
          - pending_amount: 全部待发放的内推奖金总额（Decimal 类型）
          - success_count: 累计已入职的候选人数量（候选人状态="已入职"）

        Args:
            db: 异步数据库会话。

        Returns:
            包含以下键的字典：
              - month_total (int): 本月内推人数
              - pending_amount (Decimal): 待发放奖金总额
              - success_count (int): 成功入职人数
        """
        from datetime import date as date_cls
        today = date_cls.today()
        month_start = today.replace(day=1)
        # 本月内推数
        month_total = (await db.execute(
            select(func.count(InternalReferral.id))
            .where(InternalReferral.created_at >= month_start)
        )).scalar() or 0
        # 待发奖金
        pending_amount = (await db.execute(
            select(func.sum(InternalReferral.reward_amount))
            .where(InternalReferral.first_payment_status == "待发放")
        )).scalar() or Decimal("0")
        # 成功入职
        success_count = (await db.execute(
            select(func.count(InternalReferral.id))
            .where(InternalReferral.candidate_status == "已入职")
        )).scalar() or 0
        return {"month_total": month_total, "pending_amount": pending_amount, "success_count": success_count}

    @staticmethod
    async def bulk_mark_paid(db: AsyncSession, ids: List[int], payment_type: str, operator_id: int) -> int:
        """
        批量标记内推奖金已发放，并发送站内通知给推荐人。

        根据 payment_type 参数决定更新第一期还是第二期付款状态：
          - "first": 更新 first_payment_status → "已发放"
          - 其他值:  更新 second_payment_status → "已发放"

        同时记录 paid_at（发放时间）和 paid_by（操作人员工 ID）。

        发放后给每位推荐人发送站内通知（notif_type="referral_bonus_paid"），
        通知失败会被静默捕获，不影响主流程。

        Args:
            db: 异步数据库会话。
            ids: 要标记发放的内推记录 ID 列表。
            payment_type: 付款类型，"first" 表示第一期，其他值表示第二期。
            operator_id: 操作人员工 ID（记录为 paid_by 字段）。

        Returns:
            实际成功更新的记录数量（rowcount）。
        """
        now = datetime.now(timezone.utc)
        field = "first_payment_status" if payment_type == "first" else "second_payment_status"
        result = await db.execute(
            update(InternalReferral)
            .where(InternalReferral.id.in_(ids))
            .values(**{field: "已发放"}, paid_at=now, paid_by=operator_id)
        )
        await db.flush()
        count = result.rowcount

        # 给推荐人发站内通知
        try:
            from app.models.notification import Notification
            paid_result = await db.execute(
                select(InternalReferral).where(InternalReferral.id.in_(ids))
            )
            paid_referrals = paid_result.scalars().all()
            for ref in paid_referrals:
                notif = Notification(
                    recipient_id=ref.referrer_id,
                    title="内推奖金已发放",
                    content=f"您推荐的{ref.candidate_name or '候选人'}的内推奖金（¥{float(ref.reward_amount or 0):.2f}）已发放，请查收。",
                    notif_type="referral_bonus_paid",
                    ref_type="internal_referral",
                    ref_id=ref.id,
                )
                db.add(notif)
            await db.flush()
        except Exception:
            pass

        return count

    @staticmethod
    async def export_excel(db: AsyncSession, **filters) -> bytes:
        """
        将内推记录导出为 Excel 文件（.xlsx 格式）。

        一次性查询最多 10000 条记录（page_size=10000），通过 openpyxl 生成 Excel。
        导出列：推荐人、部门、候选人、手机号、推荐职位、候选人状态、奖金金额、发放状态、推荐日期、备注。

        Args:
            db: 异步数据库会话。
            **filters: 传入 list_referrals() 的过滤参数（如 referrer_id、year 等），
                       用于按需导出特定范围的数据。

        Returns:
            Excel 文件的二进制内容（bytes），调用方通过 StreamingResponse 返回给前端下载。

        调用下游：
            list_referrals() — 获取最多 10000 条内推记录（含关联字段注入）
        """
        import openpyxl
        from io import BytesIO
        items, _ = await InternalReferralService.list_referrals(db, page=1, page_size=10000, **filters)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "内推记录"
        headers = ["推荐人", "部门", "候选人", "手机号", "推荐职位", "候选人状态", "奖金金额", "发放状态", "推荐日期", "备注"]
        ws.append(headers)
        for item in items:
            ws.append([
                getattr(item, 'referrer_name', ''),
                getattr(item, 'referrer_department', ''),
                item.candidate_name or '',
                item.candidate_phone or '',
                getattr(item, 'position_name', ''),
                item.candidate_status or '',
                float(item.reward_amount or 0),
                item.first_payment_status,
                item.created_at.strftime('%Y-%m-%d') if item.created_at else '',
                item.notes or '',
            ])
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    @staticmethod
    async def get_leaderboard(db: AsyncSession, year: int, month: Optional[int] = None) -> dict:
        """
        内推排行榜：统计指定年份（或月份）内推荐最多的员工 Top 10。

        排行榜指标（按 total_count 推荐总数倒序，取前 10 名）：
          - total_count: 期间内总推荐数
          - success_count: 已入职候选人数（衡量推荐质量）
          - pending_amount: 该推荐人名下待发放的奖金总额

        Args:
            db: 异步数据库会话。
            year: 统计年份（必填）。
            month: 统计月份（可选，None 表示统计全年）。

        Returns:
            包含以下键的字典：
              - leaderboard: 列表，每项包含 referrer_id、referrer_name、department、
                             total_count、success_count、pending_amount
              - year: 查询年份
              - month: 查询月份（None 表示全年）

        注意：查询采用 N+1 方式逐个获取推荐人姓名和部门，数据量（Top 10）有限，
              性能可接受。若后续推荐人数量大幅增加，建议改用 JOIN 查询优化。
        """
        from sqlalchemy import case

        # 基础查询条件（年份过滤）
        base_q = select(
            InternalReferral.referrer_id,
            func.count(InternalReferral.id).label("total_count"),
            func.count(
                case((InternalReferral.candidate_status == "已入职", 1))
            ).label("success_count"),
            func.sum(
                case((InternalReferral.first_payment_status == "待发放", InternalReferral.reward_amount), else_=0)
            ).label("pending_amount"),
        ).where(
            func.extract("year", InternalReferral.created_at) == year
        )

        if month:
            base_q = base_q.where(func.extract("month", InternalReferral.created_at) == month)

        base_q = base_q.group_by(InternalReferral.referrer_id).order_by(
            func.count(InternalReferral.id).desc()
        ).limit(10)

        result = await db.execute(base_q)
        rows = result.fetchall()

        # 查询推荐人信息
        from app.models.employee import Employee
        from app.models.organization import Department
        leaderboard = []
        for row in rows:
            emp = await db.get(Employee, row.referrer_id)
            dept_name = ""
            if emp and emp.department_id:
                dept = await db.get(Department, emp.department_id)
                dept_name = dept.name if dept else ""
            leaderboard.append({
                "referrer_id": row.referrer_id,
                "referrer_name": emp.name if emp else f"员工{row.referrer_id}",
                "department": dept_name,
                "total_count": row.total_count,
                "success_count": row.success_count,
                "pending_amount": float(row.pending_amount or 0),
            })

        return {"leaderboard": leaderboard, "year": year, "month": month}


class ReferralRuleService:
    """
    内推奖励规则管理服务。

    管理内推奖励规则（InternalReferralRule），规则定义了不同职位类别或条件下的奖励金额，
    HR 在创建内推记录时参考规则设定 reward_amount。

    删除操作采用软删除（is_active=False），避免历史内推记录的奖金金额参考失效。
    """

    @staticmethod
    async def list_rules(db: AsyncSession) -> List[InternalReferralRule]:
        """
        查询所有启用状态（is_active=True）的内推奖励规则，按 id 升序排列。

        Args:
            db: 异步数据库会话。

        Returns:
            启用的 InternalReferralRule 列表。
        """
        result = await db.execute(
            select(InternalReferralRule).where(InternalReferralRule.is_active == True).order_by(InternalReferralRule.id)
        )
        return result.scalars().all()

    @staticmethod
    async def create_rule(db: AsyncSession, data: ReferralRuleCreate) -> InternalReferralRule:
        """
        创建内推奖励规则。

        Args:
            db: 异步数据库会话。
            data: 规则创建 DTO，包含职位类别、奖励金额、生效条件等字段。

        Returns:
            已持久化的 InternalReferralRule 对象。
        """
        rule = InternalReferralRule(**data.model_dump())
        db.add(rule)
        await db.flush()
        await db.refresh(rule)
        return rule

    @staticmethod
    async def update_rule(db: AsyncSession, rule_id: int, data: ReferralRuleCreate) -> InternalReferralRule:
        """
        更新内推奖励规则（全量更新，仅跳过 None 值字段）。

        Args:
            db: 异步数据库会话。
            rule_id: 要更新的规则主键 ID。
            data: 规则更新 DTO（复用 ReferralRuleCreate，使用 exclude_none=True 过滤 None）。

        Returns:
            更新后的 InternalReferralRule 对象。

        Raises:
            HTTPException(404): 规则不存在时抛出。
        """
        rule = await db.get(InternalReferralRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="规则不存在")
        for k, v in data.model_dump(exclude_none=True).items():
            setattr(rule, k, v)
        await db.flush()
        await db.refresh(rule)
        return rule

    @staticmethod
    async def delete_rule(db: AsyncSession, rule_id: int):
        """
        软删除内推奖励规则（将 is_active 设为 False）。

        使用软删除而非物理删除，保留规则历史记录，
        避免已创建的内推记录失去奖金规则参考。

        Args:
            db: 异步数据库会话。
            rule_id: 要停用的规则主键 ID。

        注意：若规则不存在，静默返回（不抛异常）。
        """
        rule = await db.get(InternalReferralRule, rule_id)
        if rule:
            rule.is_active = False
            await db.flush()


# ===========================================================================
# Recruitment Statistics Service
# ===========================================================================

class RecruitmentStatsService:
    """
    招聘统计分析服务（管理驾驶舱数据源）。

    提供两类招聘数据分析：
      1. 招聘漏斗分析（get_funnel）：各阶段候选人数量及相邻阶段转化率
      2. 渠道有效性分析（get_channel_effectiveness）：各渠道的简历到 Offer 全链路转化

    辅助函数：_safe_rate() — 安全计算转化率（分母为 0 时返回 None）
    """

    @staticmethod
    async def get_funnel(
        db: AsyncSession,
        *,
        demand_id: Optional[int] = None,
    ) -> RecruitmentFunnelOut:
        """
        计算招聘漏斗各阶段的候选人数量和转化率。

        漏斗六阶段（每阶段转化率 = 本阶段人数 / 上阶段人数）：
          1. 简历投递    — 所有简历总数（转化率基准：100%）
          2. 简历筛选通过 — screening_status=RECOMMENDED 的简历数
          3. 面试安排    — 至少有一场已排期/已完成面试的候选人数（去重）
          4. 面试通过    — 至少有一场评价结论为 RECOMMEND 的候选人数（去重）
          5. Offer 发放  — offer_status 为 SENT/ACCEPTED/REJECTED 的 Offer 数
          6. Offer 接受  — offer_status=ACCEPTED 的 Offer 数

        Args:
            db: 异步数据库会话。
            demand_id: 可选，指定招聘需求 ID 只查该职位的漏斗数据；
                       None 则查询全局漏斗数据。

        Returns:
            RecruitmentFunnelOut 对象，包含各阶段统计（stages 列表）及汇总字段。

        调用工具：
            _safe_rate() — 计算相邻阶段转化率
        """

        def _base_resume_filter():
            if demand_id:
                return Resume.demand_id == demand_id
            return True  # noqa: simplify

        # Total resumes
        total_q = select(func.count(Resume.id))
        if demand_id:
            total_q = total_q.where(Resume.demand_id == demand_id)
        total_resumes = (await db.execute(total_q)).scalar() or 0

        # Screened (recommended)
        screened_q = select(func.count(Resume.id)).where(
            Resume.screening_status == ScreeningStatus.RECOMMENDED
        )
        if demand_id:
            screened_q = screened_q.where(Resume.demand_id == demand_id)
        screened = (await db.execute(screened_q)).scalar() or 0

        # Interview scheduled (at least one interview scheduled or beyond)
        interview_scheduled_q = select(
            func.count(func.distinct(Interview.resume_id))
        ).where(
            Interview.status.in_([
                InterviewStatus.SCHEDULED,
                InterviewStatus.IN_PROGRESS,
                InterviewStatus.COMPLETED,
                InterviewStatus.RESCHEDULED,
            ])
        )
        if demand_id:
            interview_scheduled_q = interview_scheduled_q.where(
                Interview.demand_id == demand_id
            )
        interview_scheduled = (await db.execute(interview_scheduled_q)).scalar() or 0

        # Interview passed (has an evaluation with overall_result = 推荐录用)
        interview_passed_q = (
            select(func.count(func.distinct(Interview.resume_id)))
            .join(
                InterviewEvaluation,
                InterviewEvaluation.interview_id == Interview.id,
            )
            .where(InterviewEvaluation.overall_result == OverallResult.RECOMMEND)
        )
        if demand_id:
            interview_passed_q = interview_passed_q.where(
                Interview.demand_id == demand_id
            )
        interview_passed = (await db.execute(interview_passed_q)).scalar() or 0

        # Offer sent
        offer_sent_q = select(func.count(Offer.id)).where(
            Offer.offer_status.in_([
                OfferStatus.SENT,
                OfferStatus.ACCEPTED,
                OfferStatus.REJECTED,
            ])
        )
        if demand_id:
            offer_sent_q = offer_sent_q.where(Offer.demand_id == demand_id)
        offer_sent = (await db.execute(offer_sent_q)).scalar() or 0

        # Offer accepted
        offer_accepted_q = select(func.count(Offer.id)).where(
            Offer.offer_status == OfferStatus.ACCEPTED
        )
        if demand_id:
            offer_accepted_q = offer_accepted_q.where(Offer.demand_id == demand_id)
        offer_accepted = (await db.execute(offer_accepted_q)).scalar() or 0

        stages = [
            FunnelStageStats(
                stage="简历投递",
                count=total_resumes,
                conversion_rate=100.0 if total_resumes else None,
            ),
            FunnelStageStats(
                stage="简历筛选通过",
                count=screened,
                conversion_rate=_safe_rate(screened, total_resumes),
            ),
            FunnelStageStats(
                stage="面试安排",
                count=interview_scheduled,
                conversion_rate=_safe_rate(interview_scheduled, screened),
            ),
            FunnelStageStats(
                stage="面试通过",
                count=interview_passed,
                conversion_rate=_safe_rate(interview_passed, interview_scheduled),
            ),
            FunnelStageStats(
                stage="Offer发放",
                count=offer_sent,
                conversion_rate=_safe_rate(offer_sent, interview_passed),
            ),
            FunnelStageStats(
                stage="Offer接受",
                count=offer_accepted,
                conversion_rate=_safe_rate(offer_accepted, offer_sent),
            ),
        ]

        return RecruitmentFunnelOut(
            demand_id=demand_id,
            total_resumes=total_resumes,
            screened=screened,
            interview_scheduled=interview_scheduled,
            interview_passed=interview_passed,
            offer_sent=offer_sent,
            offer_accepted=offer_accepted,
            stages=stages,
        )

    @staticmethod
    async def get_channel_effectiveness(
        db: AsyncSession,
    ) -> list[ChannelEffectivenessOut]:
        """
        按招聘渠道统计各渠道的招聘有效性（全链路转化分析）。

        对每个启用的渠道（is_active=True）分别计算：
          - total_resumes: 该渠道来源的简历总数
          - screened_pass: 初筛通过数（screening_status=RECOMMENDED）
          - interview_pass: 面试通过数（有评价且 overall_result=RECOMMEND 的候选人，去重）
          - offer_accepted: Offer 接受数（offer_status=ACCEPTED）
          - screening_pass_rate: 初筛通过率 = screened_pass / total_resumes
          - offer_accept_rate: Offer 接受率 = offer_accepted / total_resumes
            （衡量渠道整体质量：从简历到最终接 Offer 的漏斗转化）

        Args:
            db: 异步数据库会话。

        Returns:
            ChannelEffectivenessOut 列表，每个元素对应一个渠道的分析结果。
            若某渠道无简历，转化率字段为 None。

        注意：
            对每个渠道分别执行 4 次 SQL 查询（N×4 查询），渠道数量通常较少（<20），性能可接受。
        """

        channels_result = await db.execute(
            select(RecruitmentChannel).where(RecruitmentChannel.is_active == True)  # noqa: E712
        )
        channels = channels_result.scalars().all()

        results: list[ChannelEffectivenessOut] = []

        for channel in channels:
            # Total resumes from this channel
            total_q = select(func.count(Resume.id)).where(
                Resume.channel_id == channel.id
            )
            total_resumes = (await db.execute(total_q)).scalar() or 0

            # Screened pass
            screened_q = select(func.count(Resume.id)).where(
                and_(
                    Resume.channel_id == channel.id,
                    Resume.screening_status == ScreeningStatus.RECOMMENDED,
                )
            )
            screened_pass = (await db.execute(screened_q)).scalar() or 0

            # Interview pass
            interview_pass_q = (
                select(func.count(func.distinct(Interview.resume_id)))
                .join(Resume, Interview.resume_id == Resume.id)
                .join(
                    InterviewEvaluation,
                    InterviewEvaluation.interview_id == Interview.id,
                )
                .where(
                    and_(
                        Resume.channel_id == channel.id,
                        InterviewEvaluation.overall_result == OverallResult.RECOMMEND,
                    )
                )
            )
            interview_pass = (await db.execute(interview_pass_q)).scalar() or 0

            # Offer accepted
            offer_accepted_q = (
                select(func.count(Offer.id))
                .join(Resume, Offer.resume_id == Resume.id)
                .where(
                    and_(
                        Resume.channel_id == channel.id,
                        Offer.offer_status == OfferStatus.ACCEPTED,
                    )
                )
            )
            offer_accepted = (await db.execute(offer_accepted_q)).scalar() or 0

            results.append(
                ChannelEffectivenessOut(
                    channel_id=channel.id,
                    channel_name=channel.name,
                    total_resumes=total_resumes,
                    screened_pass=screened_pass,
                    interview_pass=interview_pass,
                    offer_accepted=offer_accepted,
                    screening_pass_rate=_safe_rate(screened_pass, total_resumes),
                    offer_accept_rate=_safe_rate(
                        offer_accepted, total_resumes
                    ),
                )
            )

        return results


# ===========================================================================
# OfferTemplate Service
# ===========================================================================

class OfferTemplateService:
    """
    Offer 模板管理服务。

    Offer 模板用于快速生成标准化的 Offer 信函，包含公司名称、职位描述、薪酬结构、
    福利待遇等预设内容。HR 创建 Offer 时可选择合适的模板作为基础，再按实际情况调整。

    模板支持启用/停用（is_active 字段），停用的模板不在创建 Offer 时的下拉列表中显示。
    """

    @staticmethod
    async def list_templates(
        db: AsyncSession,
        *,
        is_active: Optional[bool] = None,
    ) -> Sequence[OfferTemplate]:
        """
        查询 Offer 模板列表。

        Args:
            db: 异步数据库会话。
            is_active: 可选。True 只返回启用模板，False 只返回停用模板，None 返回全部。

        Returns:
            OfferTemplate 列表，按 id 升序排列。
        """
        stmt = select(OfferTemplate).order_by(OfferTemplate.id)
        if is_active is not None:
            stmt = stmt.where(OfferTemplate.is_active == is_active)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_template(db: AsyncSession, template_id: int) -> OfferTemplate:
        """
        按主键查询 Offer 模板，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            template_id: 模板主键 ID。

        Returns:
            对应的 OfferTemplate 对象。

        Raises:
            HTTPException(404): 模板不存在时抛出。
        """
        result = await db.execute(
            select(OfferTemplate).where(OfferTemplate.id == template_id)
        )
        template = result.scalar_one_or_none()
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Offer模板 {template_id} 不存在",
            )
        return template

    @staticmethod
    async def create_template(
        db: AsyncSession, data: OfferTemplateCreate
    ) -> OfferTemplate:
        """
        创建新 Offer 模板。

        Args:
            db: 异步数据库会话。
            data: 模板创建 DTO，包含模板名称、正文内容（支持占位符）、是否启用等。

        Returns:
            已持久化的 OfferTemplate 对象（含自动生成的 id、created_at）。
        """
        template = OfferTemplate(**data.model_dump())
        db.add(template)
        await db.flush()
        await db.refresh(template)
        return template

    @staticmethod
    async def update_template(
        db: AsyncSession, template_id: int, data: OfferTemplateUpdate
    ) -> OfferTemplate:
        """
        更新 Offer 模板（局部更新）。

        Args:
            db: 异步数据库会话。
            template_id: 要更新的模板主键 ID。
            data: 模板更新 DTO，仅更新传入字段（exclude_unset=True）。

        Returns:
            更新后的 OfferTemplate 对象。

        Raises:
            HTTPException(404): 模板不存在（由 get_template 内部抛出）。
        """
        template = await OfferTemplateService.get_template(db, template_id)
        update_data = data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(template, field, value)
        await db.flush()
        await db.refresh(template)
        return template


# ===========================================================================
# OnboardingRecord Service
# ===========================================================================

class OnboardingService:
    """
    入职记录管理服务。

    管理候选人正式入职后的入职记录（OnboardingRecord），是招聘漏斗的最后一个环节。
    入职记录与 Offer 关联（offer_id），只有候选人已接受（ACCEPTED）的 Offer 才能创建入职记录。

    入职记录追踪：
      - 入职日期（onboarding_date）
      - 入职状态（status）：PENDING（待入职）→ COMPLETED（已完成）→ CANCELLED（已取消）
      - 入职部门、岗位等信息（与 Offer 可能有微调）
      - 入职材料清单、工号分配等（根据实际 model 字段）

    注意：
      入职完成后的员工档案创建（Employee 记录生成）由 EmployeeLifecycleService 负责，
      而非在此服务中直接创建，保持服务边界清晰。
    """

    @staticmethod
    async def list_records(
        db: AsyncSession,
        *,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        department_id: Optional[int] = None,
    ) -> tuple[Sequence[OnboardingRecord], int]:
        """
        分页查询入职记录列表，支持状态和部门过滤。

        Args:
            db: 异步数据库会话。
            page: 当前页码，从 1 开始。
            page_size: 每页记录数，默认 20。
            status_filter: 可选，按入职状态筛选（pending/completed/cancelled）。
            department_id: 可选，按入职部门 ID 筛选。

        Returns:
            (items, total) 元组，items 按创建时间倒序排列。
        """
        stmt = select(OnboardingRecord).order_by(OnboardingRecord.created_at.desc())
        count_stmt = select(func.count(OnboardingRecord.id))

        conditions: list[Any] = []
        if status_filter:
            conditions.append(OnboardingRecord.status == status_filter)
        if department_id:
            conditions.append(OnboardingRecord.department_id == department_id)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        result = await db.execute(stmt)
        return result.scalars().all(), total

    @staticmethod
    async def get_record(db: AsyncSession, record_id: int) -> OnboardingRecord:
        """
        按主键查询入职记录，不存在则抛 404。

        Args:
            db: 异步数据库会话。
            record_id: 入职记录主键 ID。

        Returns:
            对应的 OnboardingRecord 对象。

        Raises:
            HTTPException(404): 入职记录不存在时抛出。
        """
        result = await db.execute(
            select(OnboardingRecord).where(OnboardingRecord.id == record_id)
        )
        record = result.scalar_one_or_none()
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"入职记录 {record_id} 不存在",
            )
        return record

    @staticmethod
    async def create_record(
        db: AsyncSession, data: OnboardingRecordCreate
    ) -> OnboardingRecord:
        """
        创建入职记录（招聘漏斗最终环节）。

        业务规则：只有候选人已接受（offer_status=ACCEPTED）的 Offer 才能创建入职记录。
        此规则确保入职记录与真实接受 Offer 的候选人严格对应，防止数据错误。

        Args:
            db: 异步数据库会话。
            data: 入职记录创建 DTO，包含：
                  - offer_id: 关联的 Offer ID（必须处于 ACCEPTED 状态）
                  - onboarding_date: 预计或实际入职日期
                  - department_id: 入职部门 ID
                  - position: 入职岗位
                  - 以及其他入职相关信息字段

        Returns:
            已持久化的 OnboardingRecord 对象（初始状态 PENDING）。

        Raises:
            HTTPException(404): 关联的 Offer 不存在。
            HTTPException(400): Offer 未被候选人接受，不能创建入职记录。
        """
        # 校验关联 Offer 存在且候选人已接受
        offer_result = await db.execute(
            select(Offer).where(Offer.id == data.offer_id)
        )
        offer = offer_result.scalar_one_or_none()
        if not offer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Offer {data.offer_id} 不存在",
            )
        if offer.offer_status != OfferStatus.ACCEPTED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只有候选人已接受(已接受状态)的Offer才能创建入职记录",
            )
        existing_result = await db.execute(
            select(OnboardingRecord).where(OnboardingRecord.offer_id == data.offer_id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该 Offer 已存在入职记录",
            )
        record = OnboardingRecord(**data.model_dump())
        db.add(record)
        await db.flush()
        await db.refresh(record)
        return record

    @staticmethod
    async def update_record(
        db: AsyncSession, record_id: int, data: OnboardingRecordUpdate
    ) -> OnboardingRecord:
        """
        更新入职记录（局部更新）。

        常用场景：
          - 更新入职状态（如从 PENDING 改为 COMPLETED 或 CANCELLED）
          - 更新实际入职日期（候选人推迟报到）
          - 记录入职材料核验情况

        Args:
            db: 异步数据库会话。
            record_id: 要更新的入职记录主键 ID。
            data: 入职记录更新 DTO，仅更新传入字段（exclude_unset=True）。

        Returns:
            更新后的 OnboardingRecord 对象。

        Raises:
            HTTPException(404): 入职记录不存在（由 get_record 内部抛出）。
        """
        record = await OnboardingService.get_record(db, record_id)
        update_data = data.model_dump(exclude_unset=True)
        target_status = update_data.get("status")
        target_checklist = update_data.get("checklist", record.checklist)
        if target_status == OnboardingStatus.COMPLETED and isinstance(target_checklist, dict):
            pending_items = [key for key, done in target_checklist.items() if not done]
            if pending_items:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"入职清单未完成：{', '.join(pending_items)}",
                )

        for field, value in update_data.items():
            setattr(record, field, value)

        if target_status == OnboardingStatus.COMPLETED:
            if not record.actual_date:
                record.actual_date = record.planned_date or date.today()
            if not record.employee_id:
                employee = await _materialize_employee_from_onboarding(db, record)
                record.employee_id = employee.id

        await db.flush()
        await db.refresh(record)
        return record
