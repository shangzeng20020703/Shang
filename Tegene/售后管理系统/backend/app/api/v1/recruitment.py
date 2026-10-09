"""
招聘管理模块 API 路由
=====================

路径前缀：/api/v1/recruitment
标签（Swagger）：招聘管理

本模块覆盖完整的招聘漏斗流程，共注册以下端点分组：

  【渠道管理】
    GET    /channels                        — 获取招聘渠道列表
    GET    /channels/{channel_id}           — 获取渠道详情
    POST   /channels                        — 创建招聘渠道
    PUT    /channels/{channel_id}           — 更新招聘渠道

  【招聘需求管理】
    GET    /demands                         — 获取招聘需求列表（分页+多维过滤）
    GET    /demands/{demand_id}             — 获取需求详情
    POST   /demands                         — 创建招聘需求
    PUT    /demands/{demand_id}             — 更新招聘需求
    POST   /demands/{demand_id}/approve     — 审批招聘需求

  【简历管理】（注意：固定路径端点在 /{resume_id} 之前注册）
    GET    /resumes                         — 获取简历列表（多维过滤）
    POST   /resumes/search                  — 高级搜索简历
    POST   /resumes/bulk-screen             — 批量筛选简历
    GET    /resumes/stats                   — 简历统计
    POST   /resumes/bulk-delete             — 批量删除简历
    GET    /resumes/export                  — 导出简历列表（Excel）
    GET    /resumes/{resume_id}             — 获取简历详情
    POST   /resumes                         — 创建简历
    PUT    /resumes/{resume_id}             — 更新简历（含内推状态同步）
    POST   /resumes/{resume_id}/screen      — 简历筛选（通过/拒绝）
    POST   /resumes/{resume_id}/toggle-talent-pool — 切换人才库状态

  【面试管理】
    GET    /interviews                      — 获取面试列表（含 has_evaluation 标记）
    GET    /interviews/{interview_id}       — 获取面试详情
    POST   /interviews                      — 创建面试
    PUT    /interviews/{interview_id}       — 更新面试
    POST   /interviews/{interview_id}/schedule    — 面试排期（自动发送面试官通知）
    POST   /interviews/{interview_id}/cancel      — 取消面试（自动发送面试官通知）
    POST   /interviews/{interview_id}/reschedule  — 面试改约（自动发送面试官通知）

  【面试评价】
    POST   /evaluations                          — 提交面试评价
    GET    /evaluations/by-interview/{id}        — 按面试ID查询评价
    GET    /evaluations/by-resume/{id}           — 按简历ID查询所有评价

  【Offer 管理】
    GET    /offers                          — 获取Offer列表
    POST   /offers/bulk-action              — 批量操作（批量审批/撤回）
    GET    /offers/{offer_id}               — 获取Offer详情
    POST   /offers                          — 创建Offer
    PUT    /offers/{offer_id}               — 更新Offer
    POST   /offers/{offer_id}/approve       — 审批Offer
    POST   /offers/{offer_id}/send          — 发送Offer（自动通知操作人）
    POST   /offers/{offer_id}/revoke        — 撤回Offer
    POST   /offers/{offer_id}/confirm       — 候选人确认Offer（接受/拒绝）

  【内推管理】（注意：固定路径端点在 /{referral_id} 之前注册）
    GET    /referrals                       — 获取内推列表（多维过滤）
    GET    /referrals/stats                 — 内推统计
    GET    /referrals/export                — 导出内推记录（Excel）
    GET    /referrals/export-archive        — 导出年度内推归档（Excel）
    POST   /referrals/bulk-mark-paid        — 批量标记奖金发放（admin/hr/finance）
    GET    /referrals/leaderboard           — 内推排行榜
    POST   /referrals/{referral_id}/trigger-approval — 手动发起奖金审批（admin/hr）
    GET    /referrals/{referral_id}         — 获取内推详情
    POST   /referrals                       — 创建内推记录
    PUT    /referrals/{referral_id}         — 更新内推记录

  【内推奖金规则】
    GET    /referral-rules                  — 获取规则列表
    POST   /referral-rules                  — 创建规则（admin/hr）
    PUT    /referral-rules/{rule_id}        — 更新规则（admin/hr）
    DELETE /referral-rules/{rule_id}        — 删除规则（admin/hr）

  【数据统计】
    GET    /stats                           — 招聘整体统计（漏斗+渠道效果）
    GET    /stats/funnel                    — 招聘漏斗转化率
    GET    /stats/channels                  — 渠道效果统计

  【Offer 模板】
    GET    /offer-templates                 — 获取模板列表
    GET    /offer-templates/{template_id}   — 获取模板详情
    POST   /offer-templates                 — 创建Offer模板
    PUT    /offer-templates/{template_id}   — 更新Offer模板

  【入职管理】
    GET    /onboarding                      — 获取入职记录列表
    GET    /onboarding/{record_id}          — 获取入职记录详情
    POST   /onboarding                      — 创建入职记录
    PUT    /onboarding/{record_id}          — 更新入职记录

权限说明：
  - 绝大多数端点使用 Depends(get_current_user)，即登录即可访问
  - 以下端点需要特定角色（Depends(require_roles(...))）：
      POST /referrals/bulk-mark-paid         — admin / hr / finance
      POST /referrals/{id}/trigger-approval  — admin / hr
      POST /referral-rules                   — admin / hr
      PUT  /referral-rules/{rule_id}         — admin / hr
      DELETE /referral-rules/{rule_id}       — admin / hr

路由注册顺序注意事项（FastAPI 按声明顺序匹配路由）：
  - /resumes/search、/resumes/stats、/resumes/bulk-screen、
    /resumes/bulk-delete、/resumes/export 均在 /resumes/{resume_id} 之前注册
  - /referrals/stats、/referrals/export、/referrals/export-archive、
    /referrals/bulk-mark-paid、/referrals/leaderboard 均在 /referrals/{referral_id} 之前注册
  - /offers/bulk-action 在 /offers/{offer_id} 之前注册

调用的 Service（均来自 app.services.recruitment）：
  - RecruitmentChannelService   — 招聘渠道 CRUD
  - RecruitmentDemandService    — 招聘需求 CRUD + 审批
  - ResumeService               — 简历 CRUD + 搜索 + 筛选 + 导出
  - InterviewService            — 面试 CRUD + 排期/取消/改约
  - InterviewEvaluationService  — 面试评价 CRUD
  - OfferService                — Offer CRUD + 审批 + 发送 + 撤回 + 候选人确认
  - InternalReferralService     — 内推 CRUD + 统计 + 导出 + 奖金管理
  - ReferralRuleService         — 内推奖金规则 CRUD
  - RecruitmentStatsService     — 招聘统计（漏斗 + 渠道效果）
  - OfferTemplateService        — Offer 模板 CRUD
  - OnboardingService           — 入职记录 CRUD

通知机制：
  面试排期/取消/改约、Offer 发送等关键操作会在完成后，
  通过 NotificationService.create() 向相关员工推送站内通知。
"""

import io
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, get_user_role_names, require_roles
from app.models.employee import Employee
from app.schemas.recruitment import (
    BulkDeleteRequest,
    BulkDeleteResult,
    BulkMarkPaidRequest,
    BulkOfferActionRequest,
    BulkOfferActionResult,
    BulkScreenRequest,
    BulkScreenResult,
    ChannelEffectivenessOut,
    DemandApprovalRequest,
    DemandPublishOut,
    DemandPublishRequest,
    DepartmentHeadcountOut,
    InterviewCancelRequest,
    InterviewCreate,
    InterviewEvaluationCreate,
    InterviewEvaluationOut,
    InterviewOut,
    InterviewRescheduleRequest,
    InterviewScheduleRequest,
    InterviewUpdate,
    InternalReferralCreate,
    InternalReferralOut,
    InternalReferralUpdate,
    OfferApprovalRequest,
    OfferCandidateConfirmRequest,
    OfferCreate,
    OfferOut,
    OfferSendRequest,
    OfferTemplateCreate,
    OfferTemplateOut,
    OfferTemplateUpdate,
    OfferUpdate,
    OnboardingRecordCreate,
    OnboardingRecordOut,
    OnboardingRecordUpdate,
    PaginatedResponse,
    RecruitmentChannelCreate,
    RecruitmentChannelOut,
    RecruitmentChannelUpdate,
    RecruitmentDemandCreate,
    RecruitmentDemandBusinessSubmitOut,
    RecruitmentDemandOut,
    RecruitmentDemandUpdate,
    RecruitmentPositionCreate,
    RecruitmentPositionOut,
    RecruitmentPositionUpdate,
    RecruitmentFunnelOut,
    RecruitmentStatsOut,
    LeaderboardOut,
    ReferralRuleCreate,
    ReferralRuleOut,
    ReferralStatsOut,
    ResumeCreate,
    ResumeOut,
    ResumeScreeningRequest,
    ResumeSearchParams,
    ResumeUpdate,
)
from app.services.recruitment import (
    InterviewEvaluationService,
    InterviewService,
    InternalReferralService,
    OfferService,
    OfferTemplateService,
    OnboardingService,
    RecruitmentChannelService,
    RecruitmentDemandService,
    RecruitmentPositionService,
    RecruitmentStatsService,
    ReferralRuleService,
    ResumeService,
)

router = APIRouter(
    tags=["招聘管理"],
    dependencies=[Depends(get_current_user)],
)


# ===========================================================================
# RecruitmentChannel 招聘渠道
# ===========================================================================

@router.get("/channels", response_model=list[RecruitmentChannelOut], summary="获取渠道列表")
async def list_channels(
    is_active: Optional[bool] = Query(default=None, description="是否启用"),  # None=全部，True=仅启用，False=仅禁用
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),  # 需要登录，任意角色可访问
):
    """
    获取招聘渠道列表。

    HTTP 方法：GET
    路径：/api/v1/recruitment/channels
    权限：登录用户（任意角色）

    查询参数：
        is_active (bool, 可选): 按启用状态过滤。
            True  = 仅返回启用的渠道
            False = 仅返回禁用的渠道
            不传  = 返回全部渠道

    响应（200 OK）：
        list[RecruitmentChannelOut] — 渠道对象列表（无分页，渠道数量通常较少）

    调用：RecruitmentChannelService.list_channels()
    """
    channels = await RecruitmentChannelService.list_channels(db, is_active=is_active)
    return channels


@router.get("/channels/{channel_id}", response_model=RecruitmentChannelOut, summary="获取渠道详情")
async def get_channel(
    channel_id: int,  # 路径参数：渠道 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单个招聘渠道详情。

    HTTP 方法：GET
    路径：/api/v1/recruitment/channels/{channel_id}
    权限：登录用户（任意角色）

    路径参数：
        channel_id (int): 渠道主键 ID

    响应（200 OK）：RecruitmentChannelOut
    错误响应：404 Not Found — 渠道不存在

    调用：RecruitmentChannelService.get_channel()
    """
    return await RecruitmentChannelService.get_channel(db, channel_id)


@router.post("/channels", response_model=RecruitmentChannelOut, status_code=201, summary="创建招聘渠道")
async def create_channel(
    data: RecruitmentChannelCreate,  # 请求体：渠道名称、类型、是否启用等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    创建新的招聘渠道（如：BOSS直聘、猎聘、内推、校招等）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/channels
    权限：登录用户（任意角色）

    请求体：RecruitmentChannelCreate
        name      (str, 必填): 渠道名称
        其他字段见 schema 定义

    响应（201 Created）：RecruitmentChannelOut

    调用：RecruitmentChannelService.create_channel()
    """
    return await RecruitmentChannelService.create_channel(db, data)


@router.put("/channels/{channel_id}", response_model=RecruitmentChannelOut, summary="更新招聘渠道")
async def update_channel(
    channel_id: int,  # 路径参数：渠道 ID
    data: RecruitmentChannelUpdate,  # 请求体：需要更新的字段（Pydantic v2 partial update）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    更新指定招聘渠道的信息。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/channels/{channel_id}
    权限：登录用户（任意角色）

    路径参数：
        channel_id (int): 渠道主键 ID

    请求体：RecruitmentChannelUpdate（所有字段均可选，仅更新传入字段）

    响应（200 OK）：RecruitmentChannelOut
    错误响应：404 Not Found — 渠道不存在

    调用：RecruitmentChannelService.update_channel()
    """
    return await RecruitmentChannelService.update_channel(db, channel_id, data)


# ===========================================================================
# RecruitmentPosition 职位字典
# ===========================================================================

@router.get("/positions", response_model=list[RecruitmentPositionOut], summary="获取职位字典列表")
async def list_positions(
    keyword: Optional[str] = Query(default=None, description="关键词（职位名称/类别/等级）"),
    is_active: Optional[bool] = Query(default=None, description="是否启用"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    return await RecruitmentPositionService.list_positions(
        db, keyword=keyword, is_active=is_active
    )


@router.post("/positions", response_model=RecruitmentPositionOut, status_code=201, summary="创建职位")
async def create_position(
    data: RecruitmentPositionCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    return await RecruitmentPositionService.create_position(db, data)


@router.put("/positions/{position_id}", response_model=RecruitmentPositionOut, summary="更新职位")
async def update_position(
    position_id: int,
    data: RecruitmentPositionUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    return await RecruitmentPositionService.update_position(db, position_id, data)


@router.delete("/positions/{position_id}", summary="删除职位")
async def delete_position(
    position_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    await RecruitmentPositionService.delete_position(db, position_id)
    return {"message": "删除成功"}


# ===========================================================================
# RecruitmentDemand 招聘需求
# ===========================================================================

@router.get("/demands", response_model=PaginatedResponse, summary="获取招聘需求列表")
async def list_demands(
    page: int = Query(default=1, ge=1, description="页码，从1开始"),
    page_size: int = Query(default=20, ge=1, le=1000, description="每页数量，最大1000"),
    status: Optional[str] = Query(default=None, alias="status", description="需求状态（如：open/closed/fulfilled）"),
    approval_status: Optional[str] = Query(default=None, description="审批状态（如：pending/approved/rejected）"),
    department_id: Optional[int] = Query(default=None, description="按部门ID过滤"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    分页获取招聘需求列表，支持多维度过滤。

    HTTP 方法：GET
    路径：/api/v1/recruitment/demands
    权限：登录用户（任意角色）

    查询参数：
        page          (int, 默认1):      页码，最小值1
        page_size     (int, 默认20):     每页数量，范围 [1, 1000]
        status        (str, 可选):       需求业务状态（open=招聘中/closed=已关闭/fulfilled=已满足等）
        approval_status (str, 可选):     审批状态（pending/approved/rejected）
        department_id (int, 可选):       按所属部门 ID 过滤

    响应（200 OK）：PaginatedResponse
        {
            "total": 100,          # 总记录数
            "page": 1,
            "page_size": 20,
            "items": [...]         # RecruitmentDemandOut 列表
        }

    调用：RecruitmentDemandService.list_demands()
    """
    items, total = await RecruitmentDemandService.list_demands(
        db,
        page=page,
        page_size=page_size,
        status_filter=status,
        approval_status=approval_status,
        department_id=department_id,
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[RecruitmentDemandOut.model_validate(d) for d in items],
    )


@router.get("/headcount", response_model=list[DepartmentHeadcountOut], summary="获取招聘编制余额")
async def list_headcount(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """管理端招聘模块与移动端负责人共用的部门编制余额。"""
    return await RecruitmentDemandService.list_department_headcounts(db)


@router.get("/headcount/{department_id}", response_model=DepartmentHeadcountOut, summary="校验部门编制余额")
async def get_headcount(
    department_id: int,
    requested_count: int = Query(default=0, ge=0, description="本次拟招聘人数"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """返回指定部门当前编制、在岗人数、已占用招聘需求和可用余额。"""
    return await RecruitmentDemandService.department_headcount(
        db,
        department_id,
        requested_count=requested_count,
    )


@router.post(
    "/demands/business-request",
    response_model=RecruitmentDemandBusinessSubmitOut,
    status_code=201,
    summary="移动端用人部门发起招聘需求",
)
async def submit_business_demand(
    data: RecruitmentDemandCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "manager")),
):
    """业务部门负责人使用移动端提交招聘需求，系统先校验编制余额。"""
    roles = await get_user_role_names(db, current_user)
    return await RecruitmentDemandService.submit_business_demand(
        db,
        data,
        requester=current_user,
        requester_roles=roles,
    )


@router.get("/demands/{demand_id}", response_model=RecruitmentDemandOut, summary="获取招聘需求详情")
async def get_demand(
    demand_id: int,  # 路径参数：需求 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单个招聘需求的详细信息。

    HTTP 方法：GET
    路径：/api/v1/recruitment/demands/{demand_id}
    权限：登录用户（任意角色）

    路径参数：
        demand_id (int): 招聘需求主键 ID

    响应（200 OK）：RecruitmentDemandOut
    错误响应：404 Not Found — 需求不存在

    调用：RecruitmentDemandService.get_demand()
    """
    return await RecruitmentDemandService.get_demand(db, demand_id)


@router.post("/demands", response_model=RecruitmentDemandOut, status_code=201, summary="创建招聘需求")
async def create_demand(
    data: RecruitmentDemandCreate,  # 请求体：岗位名称、招聘人数、所属部门、期望到岗日期等
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),  # 注意：此处用 current_user（非下划线前缀），需要传递 ID
):
    """
    创建新的招聘需求。

    HTTP 方法：POST
    路径：/api/v1/recruitment/demands
    权限：登录用户（任意角色）

    请求体：RecruitmentDemandCreate
        position_name  (str, 必填): 岗位名称
        department_id  (int, 必填): 所属部门 ID
        headcount      (int, 必填): 需求人数
        其他字段见 schema 定义

    业务说明：
        创建后的需求通常需要经过审批才能开始招聘（approval_status=pending）。
        requester_id 自动取当前登录用户的 ID。

    响应（201 Created）：RecruitmentDemandOut

    调用：RecruitmentDemandService.create_demand(db, data, requester_id=current_user.id)
    """
    return await RecruitmentDemandService.create_demand(
        db, data, requester_id=current_user.id
    )


@router.put("/demands/{demand_id}", response_model=RecruitmentDemandOut, summary="更新招聘需求")
async def update_demand(
    demand_id: int,  # 路径参数：需求 ID
    data: RecruitmentDemandUpdate,  # 请求体：需更新的字段
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    更新指定招聘需求的信息。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/demands/{demand_id}
    权限：登录用户（任意角色）

    路径参数：
        demand_id (int): 招聘需求主键 ID

    请求体：RecruitmentDemandUpdate（所有字段均可选）

    响应（200 OK）：RecruitmentDemandOut
    错误响应：404 Not Found — 需求不存在

    调用：RecruitmentDemandService.update_demand()
    """
    return await RecruitmentDemandService.update_demand(db, demand_id, data)


@router.post(
    "/demands/{demand_id}/approve",
    response_model=RecruitmentDemandOut,
    summary="审批招聘需求",
)
async def approve_demand(
    demand_id: int,  # 路径参数：需求 ID
    data: DemandApprovalRequest,  # 请求体：审批操作（approved/rejected）及审批意见
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    对招聘需求执行审批操作（通过或拒绝）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/demands/{demand_id}/approve
    权限：登录用户（任意角色，业务层可校验审批人权限）

    路径参数：
        demand_id (int): 招聘需求主键 ID

    请求体：DemandApprovalRequest
        action  (str, 必填): 审批动作，"approved"=通过 / "rejected"=拒绝
        comment (str, 可选): 审批意见

    业务说明：
        审批通过后，需求状态变为 open，可开始收集简历。
        审批拒绝后，需求状态变为 rejected，需求人可修改后重新提交。

    响应（200 OK）：RecruitmentDemandOut（含更新后的 approval_status）
    错误响应：404 Not Found — 需求不存在

    调用：RecruitmentDemandService.approve_demand()
    """
    return await RecruitmentDemandService.approve_demand(db, demand_id, data)


@router.post(
    "/demands/{demand_id}/publish",
    response_model=DemandPublishOut,
    summary="发布招聘需求到渠道",
)
async def publish_demand(
    demand_id: int,
    data: DemandPublishRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """HR 将已审批的招聘需求发布到渠道 API；未配置 API 时返回待发布载荷。"""
    return await RecruitmentDemandService.publish_demand(db, demand_id, data)


# ===========================================================================
# Resume 简历
# 注意：固定路径端点（/search、/stats、/bulk-screen、/bulk-delete、/export）
# 必须在 /{resume_id} 之前声明，避免 FastAPI 将字符串路径段误解为 resume_id 整数。
# ===========================================================================

@router.get("/resumes", response_model=PaginatedResponse, summary="获取简历列表")
async def list_resumes(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    demand_id: Optional[int] = Query(default=None, description="按招聘需求ID过滤"),
    channel_id: Optional[int] = Query(default=None, description="按投递渠道ID过滤"),
    screening_status: Optional[str] = Query(default=None, description="筛选状态（pending/passed/rejected/interview）"),
    education: Optional[str] = Query(default=None, description="学历要求（bachelor/master/phd等）"),
    keyword: Optional[str] = Query(default=None, description="模糊搜索关键词（匹配候选人姓名或手机号）"),
    work_years_min: Optional[int] = Query(default=None, ge=0, description="最少工作年限"),
    work_years_max: Optional[int] = Query(default=None, ge=0, description="最多工作年限"),
    created_at_from: Optional[str] = Query(default=None, description="投递日期起始，格式 YYYY-MM-DD"),
    created_at_to: Optional[str] = Query(default=None, description="投递日期截止，格式 YYYY-MM-DD"),
    talent_pool: Optional[bool] = Query(default=None, description="是否仅查看人才库简历"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    分页获取简历列表，支持多维度过滤。

    HTTP 方法：GET
    路径：/api/v1/recruitment/resumes
    权限：登录用户（任意角色）

    查询参数：
        page            (int, 默认1):       页码
        page_size       (int, 默认20):      每页数量，范围 [1, 1000]
        demand_id       (int, 可选):         按招聘需求 ID 过滤
        channel_id      (int, 可选):         按投递渠道 ID 过滤
        screening_status (str, 可选):        简历筛选状态
        education       (str, 可选):         学历过滤
        keyword         (str, 可选):         姓名或手机号模糊搜索
        work_years_min  (int, 可选, >=0):    工作年限下限
        work_years_max  (int, 可选, >=0):    工作年限上限
        created_at_from (str, 可选):         投递日期起始（YYYY-MM-DD）
        created_at_to   (str, 可选):         投递日期截止（YYYY-MM-DD）
        talent_pool     (bool, 可选):        True=仅人才库，False=非人才库，不传=全部

    响应（200 OK）：PaginatedResponse（items 为 ResumeOut 列表）

    调用：ResumeService.list_resumes()
    """
    items, total = await ResumeService.list_resumes(
        db,
        page=page,
        page_size=page_size,
        demand_id=demand_id,
        channel_id=channel_id,
        screening_status=screening_status,
        education=education,
        keyword=keyword,
        work_years_min=work_years_min,
        work_years_max=work_years_max,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
        talent_pool=talent_pool,
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ResumeOut.model_validate(r) for r in items],
    )


@router.post("/resumes/search", response_model=PaginatedResponse, summary="搜索简历")
async def search_resumes(
    params: ResumeSearchParams,  # 请求体：结构化搜索条件（支持技能标签、薪资期望等复杂条件）
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    高级搜索简历（使用 POST 方法传递复杂搜索条件）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/resumes/search
    权限：登录用户（任意角色）

    说明：
        与 GET /resumes 不同，本接口通过请求体传递结构化搜索参数，
        支持更复杂的组合查询条件（如技能标签、薪资期望区间等）。

    查询参数（URL）：
        page      (int, 默认1):   页码
        page_size (int, 默认20):  每页数量

    请求体：ResumeSearchParams（结构化搜索条件）

    响应（200 OK）：PaginatedResponse（items 为 ResumeOut 列表）

    调用：ResumeService.search_resumes()
    """
    items, total = await ResumeService.search_resumes(
        db, params, page=page, page_size=page_size
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ResumeOut.model_validate(r) for r in items],
    )


@router.post("/resumes/bulk-screen", response_model=BulkScreenResult, summary="简历批量筛选")
async def bulk_screen_resumes(
    data: BulkScreenRequest,  # 请求体：简历 ID 列表 + 筛选结果（passed/rejected）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    批量对多份简历执行筛选操作（全部通过或全部拒绝）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/resumes/bulk-screen
    权限：登录用户（任意角色）

    请求体：BulkScreenRequest
        resume_ids       (list[int]): 要操作的简历 ID 列表
        screening_status (str):       统一设置的筛选状态（passed/rejected）
        reason           (str, 可选): 批量拒绝原因

    响应（200 OK）：BulkScreenResult
        { "updated": 5, "failed": 0 }  # 成功更新数量和失败数量

    调用：ResumeService.bulk_screen()
    """
    return await ResumeService.bulk_screen(db, data)


@router.get("/resumes/stats", summary="简历统计")
async def get_resume_stats(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取简历维度的汇总统计数据。

    HTTP 方法：GET
    路径：/api/v1/recruitment/resumes/stats
    权限：登录用户（任意角色）

    响应（200 OK）：
        简历统计数据（具体字段由 ResumeService.get_stats() 决定），
        通常包含按筛选状态分组的简历数量等。

    调用：ResumeService.get_stats()
    """
    return await ResumeService.get_stats(db)


@router.post("/resumes/bulk-delete", response_model=BulkDeleteResult, summary="批量删除简历")
async def bulk_delete_resumes(
    data: BulkDeleteRequest,  # 请求体：{ "resume_ids": [1, 2, 3] }
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    批量删除指定简历记录。

    HTTP 方法：POST
    路径：/api/v1/recruitment/resumes/bulk-delete
    权限：登录用户（任意角色）

    请求体：BulkDeleteRequest
        resume_ids (list[int]): 要删除的简历 ID 列表

    响应（200 OK）：BulkDeleteResult
        { "deleted": 3 }  # 实际删除的数量

    调用：ResumeService.bulk_delete()
    """
    deleted = await ResumeService.bulk_delete(db, data.resume_ids)
    return BulkDeleteResult(deleted=deleted)


@router.get("/resumes/export", summary="导出简历列表 Excel")
async def export_resumes(
    keyword: Optional[str] = Query(default=None, description="姓名/手机号搜索"),
    screening_status: Optional[str] = Query(default=None, description="筛选状态"),
    channel_id: Optional[int] = Query(default=None, description="渠道ID"),
    education: Optional[str] = Query(default=None, description="学历"),
    work_years_min: Optional[int] = Query(default=None, ge=0),
    work_years_max: Optional[int] = Query(default=None, ge=0),
    created_at_from: Optional[str] = Query(default=None, description="投递日期起 YYYY-MM-DD"),
    created_at_to: Optional[str] = Query(default=None, description="投递日期止 YYYY-MM-DD"),
    talent_pool: Optional[bool] = Query(default=None, description="是否人才库"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    按过滤条件导出简历列表为 Excel 文件（.xlsx 格式）。

    HTTP 方法：GET
    路径：/api/v1/recruitment/resumes/export
    权限：登录用户（任意角色）

    查询参数：
        与 GET /resumes 相同的过滤参数（无分页，最多导出 10000 条）

    响应（200 OK）：
        Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
        Content-Disposition: attachment; filename=resumes.xlsx
        Excel 列：姓名 / 手机号 / 应聘职位 / 学历 / 工作年限 / 筛选状态 / 投递日期 / 简历附件 / 人才库

    实现说明：
        - 使用 openpyxl 在内存中构建 Excel（不写临时文件）
        - 表头使用加粗白字 + 蓝紫色填充（#4F46E5）
        - 通过 Response 直接返回字节流（非 StreamingResponse，文件较小时更高效）

    调用：ResumeService.list_resumes()（page_size=10000 获取全量数据）
    """
    from fastapi.responses import Response
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    import io

    # 最多导出 10000 条，调用 list_resumes 时不分页
    items, _ = await ResumeService.list_resumes(
        db,
        page=1,
        page_size=10000,
        keyword=keyword,
        screening_status=screening_status,
        channel_id=channel_id,
        education=education,
        work_years_min=work_years_min,
        work_years_max=work_years_max,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
        talent_pool=talent_pool,
    )

    # 在内存中构建 Excel 工作簿
    wb = Workbook()
    ws = wb.active
    ws.title = "简历列表"
    headers = ["姓名", "手机号", "应聘职位", "学历", "工作年限", "筛选状态", "投递日期", "简历附件", "人才库"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True, color="FFFFFF")           # 白色加粗字体
        cell.fill = PatternFill(fill_type="solid", fgColor="4F46E5")  # 蓝紫色背景
        cell.alignment = Alignment(horizontal="center")

    for row_idx, r in enumerate(items, 2):
        demand_name = r.demand.position_name if r.demand else ""
        ws.cell(row=row_idx, column=1, value=r.candidate_name)
        ws.cell(row=row_idx, column=2, value=r.phone)
        ws.cell(row=row_idx, column=3, value=demand_name)
        ws.cell(row=row_idx, column=4, value=r.education or "")
        ws.cell(row=row_idx, column=5, value=r.work_years or "")
        ws.cell(row=row_idx, column=6, value=r.screening_status)
        ws.cell(row=row_idx, column=7, value=str(r.created_at)[:10])  # 只取日期部分
        ws.cell(row=row_idx, column=8, value=r.resume_file_url or "")
        ws.cell(row=row_idx, column=9, value="是" if r.talent_pool else "否")

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=resumes.xlsx"},
    )


@router.get("/resumes/{resume_id}", response_model=ResumeOut, summary="获取简历详情")
async def get_resume(
    resume_id: int,  # 路径参数：简历 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单份简历的详细信息。

    HTTP 方法：GET
    路径：/api/v1/recruitment/resumes/{resume_id}
    权限：登录用户（任意角色）

    路径参数：
        resume_id (int): 简历主键 ID

    响应（200 OK）：ResumeOut（含候选人信息、投递渠道、面试记录关联等）
    错误响应：404 Not Found — 简历不存在

    调用：ResumeService.get_resume()
    """
    return await ResumeService.get_resume(db, resume_id)


@router.post("/resumes", response_model=ResumeOut, status_code=201, summary="创建简历")
async def create_resume(
    data: ResumeCreate,  # 请求体：候选人基本信息、学历、工作经历、投递渠道等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    创建新的候选人简历记录。

    HTTP 方法：POST
    路径：/api/v1/recruitment/resumes
    权限：登录用户（任意角色）

    请求体：ResumeCreate
        candidate_name (str, 必填): 候选人姓名
        phone          (str, 必填): 手机号
        demand_id      (int, 可选): 关联的招聘需求 ID
        channel_id     (int, 可选): 投递渠道 ID
        其他字段见 schema 定义

    响应（201 Created）：ResumeOut

    调用：ResumeService.create_resume()
    """
    return await ResumeService.create_resume(db, data)


@router.put("/resumes/{resume_id}", response_model=ResumeOut, summary="更新简历")
async def update_resume(
    resume_id: int,  # 路径参数：简历 ID
    data: ResumeUpdate,  # 请求体：需要更新的字段
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    更新简历信息，并在筛选状态变更时同步内推候选人状态。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/resumes/{resume_id}
    权限：登录用户（任意角色）

    路径参数：
        resume_id (int): 简历主键 ID

    请求体：ResumeUpdate（所有字段均可选）

    业务说明：
        若更新了 screening_status 字段，会自动调用
        InternalReferralService.sync_candidate_status() 同步关联内推记录的候选人状态，
        保持简历筛选状态与内推记录的状态一致。

    响应（200 OK）：ResumeOut
    错误响应：404 Not Found — 简历不存在

    调用：ResumeService.update_resume() + InternalReferralService.sync_candidate_status()
    """
    resume = await ResumeService.update_resume(db, resume_id, data)
    # 若本次更新包含筛选状态变更，同步内推候选人状态
    new_status = getattr(data, "screening_status", None)
    if new_status is not None:
        await InternalReferralService.sync_candidate_status(db, resume_id, new_status)
    return resume


@router.post(
    "/resumes/{resume_id}/screen",
    response_model=ResumeOut,
    summary="简历筛选(通过/拒绝)",
)
async def screen_resume(
    resume_id: int,  # 路径参数：简历 ID
    data: ResumeScreeningRequest,  # 请求体：筛选结果 + 拒绝原因（可选）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    对单份简历执行筛选操作，并同步内推候选人状态。

    HTTP 方法：POST
    路径：/api/v1/recruitment/resumes/{resume_id}/screen
    权限：登录用户（任意角色）

    路径参数：
        resume_id (int): 简历主键 ID

    请求体：ResumeScreeningRequest
        screening_status (str, 必填): 筛选结果（"passed"=通过 / "rejected"=拒绝）
        reject_reason    (str, 可选): 拒绝原因（status=rejected 时建议填写）

    业务说明：
        筛选完成后自动调用 InternalReferralService.sync_candidate_status()，
        将内推记录中的候选人状态同步为本次筛选结果。

    响应（200 OK）：ResumeOut（含更新后的 screening_status）
    错误响应：404 Not Found — 简历不存在

    调用：ResumeService.screen_resume() + InternalReferralService.sync_candidate_status()
    """
    resume = await ResumeService.screen_resume(db, resume_id, data)
    # 筛选状态变更后同步内推记录的候选人状态
    await InternalReferralService.sync_candidate_status(db, resume_id, data.screening_status)
    return resume


@router.post(
    "/resumes/{resume_id}/toggle-talent-pool",
    response_model=ResumeOut,
    summary="切换人才库状态",
)
async def toggle_talent_pool(
    resume_id: int,  # 路径参数：简历 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    切换简历的人才库标记（加入/移出人才库）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/resumes/{resume_id}/toggle-talent-pool
    权限：登录用户（任意角色）

    路径参数：
        resume_id (int): 简历主键 ID

    业务说明：
        人才库（talent_pool=True）的简历不会随需求关闭而删除，
        可在未来有相关岗位时重新使用。
        本接口对当前 talent_pool 布尔值取反（True→False，False→True）。

    响应（200 OK）：ResumeOut（含更新后的 talent_pool 字段）
    错误响应：404 Not Found — 简历不存在
    """
    resume = await ResumeService.get_resume(db, resume_id)
    resume.talent_pool = not resume.talent_pool  # 取反切换
    await db.flush()
    await db.refresh(resume)
    return resume


# ===========================================================================
# Interview 面试
# ===========================================================================

@router.get("/interviews", response_model=PaginatedResponse, summary="获取面试列表")
async def list_interviews(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    resume_id: Optional[int] = Query(default=None, description="按简历ID过滤（查看某候选人的所有面试）"),
    demand_id: Optional[int] = Query(default=None, description="按招聘需求ID过滤"),
    interviewer_id: Optional[int] = Query(default=None, description="按面试官员工ID过滤"),
    status: Optional[str] = Query(default=None, alias="status", description="面试状态（scheduled/completed/cancelled等）"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    分页获取面试列表，支持多维度过滤，并附带是否已有评价的标记。

    HTTP 方法：GET
    路径：/api/v1/recruitment/interviews
    权限：登录用户（任意角色）

    查询参数：
        page          (int, 默认1):   页码
        page_size     (int, 默认20):  每页数量
        resume_id     (int, 可选):    按候选人简历 ID 过滤
        demand_id     (int, 可选):    按招聘需求 ID 过滤
        interviewer_id (int, 可选):   按面试官 ID 过滤（常用于面试官查看自己的面试任务）
        status        (str, 可选):    面试状态过滤

    响应（200 OK）：PaginatedResponse（items 为 InterviewOut 列表）
        每条面试记录额外包含 has_evaluation (bool) 字段，
        标记该面试是否已提交评价，前端可据此显示"填写评价"或"查看评价"。

    调用：InterviewService.list_interviews()
    """
    items, total = await InterviewService.list_interviews(
        db,
        page=page,
        page_size=page_size,
        resume_id=resume_id,
        demand_id=demand_id,
        interviewer_id=interviewer_id,
        status_filter=status,
    )
    items_out = []
    for i in items:
        item_data = InterviewOut.model_validate(i)
        item_data.has_evaluation = (i.evaluation is not None)  # 注入是否已有评价标记
        items_out.append(item_data)
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items_out,
    )


@router.get("/interviews/{interview_id}", response_model=InterviewOut, summary="获取面试详情")
async def get_interview(
    interview_id: int,  # 路径参数：面试 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单次面试的详细信息。

    HTTP 方法：GET
    路径：/api/v1/recruitment/interviews/{interview_id}
    权限：登录用户（任意角色）

    路径参数：
        interview_id (int): 面试主键 ID

    响应（200 OK）：InterviewOut（含面试时间、面试官、面试类型、状态等）
    错误响应：404 Not Found — 面试记录不存在

    调用：InterviewService.get_interview()
    """
    return await InterviewService.get_interview(db, interview_id)


@router.post("/interviews", response_model=InterviewOut, status_code=201, summary="创建面试")
async def create_interview(
    data: InterviewCreate,  # 请求体：简历ID、面试官ID、面试类型、计划时间等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    创建新的面试安排记录。

    HTTP 方法：POST
    路径：/api/v1/recruitment/interviews
    权限：登录用户（任意角色）

    请求体：InterviewCreate
        resume_id      (int, 必填): 关联简历 ID
        interviewer_id (int, 可选): 面试官员工 ID
        interview_type (str, 必填): 面试类型（如：HR面/技术面/终面）
        scheduled_time (datetime, 可选): 计划面试时间
        其他字段见 schema 定义

    响应（201 Created）：InterviewOut

    调用：InterviewService.create_interview()
    """
    return await InterviewService.create_interview(db, data)


@router.put("/interviews/{interview_id}", response_model=InterviewOut, summary="更新面试")
async def update_interview(
    interview_id: int,  # 路径参数：面试 ID
    data: InterviewUpdate,  # 请求体：需要更新的字段
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    更新面试记录信息。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/interviews/{interview_id}
    权限：登录用户（任意角色）

    路径参数：
        interview_id (int): 面试主键 ID

    请求体：InterviewUpdate（所有字段均可选）

    响应（200 OK）：InterviewOut
    错误响应：404 Not Found — 面试记录不存在

    调用：InterviewService.update_interview()
    """
    return await InterviewService.update_interview(db, interview_id, data)


@router.post(
    "/interviews/{interview_id}/schedule",
    response_model=InterviewOut,
    summary="面试排期",
)
async def schedule_interview(
    interview_id: int,  # 路径参数：面试 ID
    data: InterviewScheduleRequest,  # 请求体：排期时间、地点、面试官等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    为面试安排具体时间（排期），并向面试官发送站内通知。

    HTTP 方法：POST
    路径：/api/v1/recruitment/interviews/{interview_id}/schedule
    权限：登录用户（任意角色）

    路径参数：
        interview_id (int): 面试主键 ID

    请求体：InterviewScheduleRequest
        scheduled_time (datetime, 必填): 面试具体时间
        location       (str, 可选):      面试地点或视频会议链接
        interviewer_id (int, 可选):      面试官员工 ID（可在此步骤指定或更换）

    业务说明：
        Service 层返回 (interview, should_notify) 元组。
        若 should_notify=True 且已指定面试官，则推送站内通知给面试官，
        通知内容包含候选人姓名、面试类型和排期时间。

    响应（200 OK）：InterviewOut（含更新后的 scheduled_time 和 status）
    错误响应：404 Not Found — 面试记录不存在

    调用：InterviewService.schedule_interview() + NotificationService.create()
    """
    interview, should_notify = await InterviewService.schedule_interview(
        db, interview_id, data
    )
    # 若需要通知且已分配面试官，向面试官推送站内消息
    if should_notify and interview.interviewer_id:
        from app.services.notification import NotificationService
        from app.schemas.notification import NotificationCreate
        candidate_name = interview.resume.candidate_name if interview.resume else "候选人"
        scheduled_time = interview.scheduled_time.strftime("%Y-%m-%d %H:%M") if interview.scheduled_time else ""
        await NotificationService.create(db, NotificationCreate(
            recipient_id=interview.interviewer_id,
            title=f"新面试任务：{candidate_name}",
            content=f"{interview.interview_type} | {scheduled_time}",
            notif_type="interview",
            ref_type="interview",
            ref_id=interview.id,
        ))
        await db.flush()
    return interview


@router.post(
    "/interviews/{interview_id}/cancel",
    response_model=InterviewOut,
    summary="取消面试",
)
async def cancel_interview(
    interview_id: int,  # 路径参数：面试 ID
    data: InterviewCancelRequest,  # 请求体：取消原因
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    取消已安排的面试，并向面试官发送取消通知。

    HTTP 方法：POST
    路径：/api/v1/recruitment/interviews/{interview_id}/cancel
    权限：登录用户（任意角色）

    路径参数：
        interview_id (int): 面试主键 ID

    请求体：InterviewCancelRequest
        cancel_reason (str, 可选): 取消原因说明

    业务说明：
        面试状态更新为 cancelled。
        若 should_notify=True 且已有面试官，向面试官推送取消通知。

    响应（200 OK）：InterviewOut（status=cancelled）
    错误响应：404 Not Found — 面试记录不存在

    调用：InterviewService.cancel_interview() + NotificationService.create()
    """
    interview, should_notify = await InterviewService.cancel_interview(
        db, interview_id, data
    )
    # 向面试官推送取消通知
    if should_notify and interview.interviewer_id:
        from app.services.notification import NotificationService
        from app.schemas.notification import NotificationCreate
        candidate_name = interview.resume.candidate_name if interview.resume else "候选人"
        await NotificationService.create(db, NotificationCreate(
            recipient_id=interview.interviewer_id,
            title=f"面试已取消：{candidate_name}",
            content=f"{interview.interview_type} 面试已取消",
            notif_type="interview",
            ref_type="interview",
            ref_id=interview.id,
        ))
        await db.flush()
    return interview


@router.post(
    "/interviews/{interview_id}/reschedule",
    response_model=InterviewOut,
    summary="面试改约",
)
async def reschedule_interview(
    interview_id: int,  # 路径参数：面试 ID
    data: InterviewRescheduleRequest,  # 请求体：新的面试时间（及原因）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    修改已安排面试的时间（改约），并向面试官发送时间变更通知。

    HTTP 方法：POST
    路径：/api/v1/recruitment/interviews/{interview_id}/reschedule
    权限：登录用户（任意角色）

    路径参数：
        interview_id (int): 面试主键 ID

    请求体：InterviewRescheduleRequest
        new_scheduled_time (datetime, 必填): 新的面试时间
        reschedule_reason  (str, 可选):      改约原因

    业务说明：
        更新面试时间并推送变更通知给面试官，通知内容包含候选人姓名和新时间。

    响应（200 OK）：InterviewOut（含更新后的 scheduled_time）
    错误响应：404 Not Found — 面试记录不存在

    调用：InterviewService.reschedule_interview() + NotificationService.create()
    """
    interview, should_notify = await InterviewService.reschedule_interview(
        db, interview_id, data
    )
    # 向面试官推送时间变更通知
    if should_notify and interview.interviewer_id:
        from app.services.notification import NotificationService
        from app.schemas.notification import NotificationCreate
        candidate_name = interview.resume.candidate_name if interview.resume else "候选人"
        scheduled_time = interview.scheduled_time.strftime("%Y-%m-%d %H:%M") if interview.scheduled_time else ""
        await NotificationService.create(db, NotificationCreate(
            recipient_id=interview.interviewer_id,
            title=f"面试时间已变更：{candidate_name}",
            content=f"{interview.interview_type} | 新时间：{scheduled_time}",
            notif_type="interview",
            ref_type="interview",
            ref_id=interview.id,
        ))
        await db.flush()
    return interview


# ===========================================================================
# InterviewEvaluation 面试评价
# ===========================================================================

@router.post(
    "/evaluations",
    response_model=InterviewEvaluationOut,
    status_code=201,
    summary="提交面试评价",
)
async def create_evaluation(
    data: InterviewEvaluationCreate,  # 请求体：面试ID、评分维度、综合评价、是否推荐等
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),  # 评价人即当前登录用户（面试官）
):
    """
    面试官提交面试评价。

    HTTP 方法：POST
    路径：/api/v1/recruitment/evaluations
    权限：登录用户（任意角色，通常由面试官本人提交）

    请求体：InterviewEvaluationCreate
        interview_id     (int, 必填):   关联面试 ID
        rating           (int, 必填):   综合评分（通常 1-5 分）
        technical_score  (int, 可选):   技术能力评分
        communication_score (int, 可选): 沟通能力评分
        overall_comment  (str, 可选):   综合评语
        recommendation   (str, 必填):   是否推荐（recommended/not_recommended/neutral）
        其他维度评分见 schema 定义

    业务说明：
        interviewer_id 自动取当前登录用户的 ID，无需前端传递。
        一次面试只能有一份评价（Service 层校验唯一性）。

    响应（201 Created）：InterviewEvaluationOut

    调用：InterviewEvaluationService.create_evaluation(db, data, interviewer_id=current_user.id)
    """
    return await InterviewEvaluationService.create_evaluation(
        db, data, interviewer_id=current_user.id
    )


@router.get(
    "/evaluations/by-interview/{interview_id}",
    response_model=InterviewEvaluationOut,
    summary="获取面试评价(按面试ID)",
)
async def get_evaluation_by_interview(
    interview_id: int,  # 路径参数：面试 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    通过面试 ID 查询对应的评价记录。

    HTTP 方法：GET
    路径：/api/v1/recruitment/evaluations/by-interview/{interview_id}
    权限：登录用户（任意角色）

    路径参数：
        interview_id (int): 面试主键 ID

    响应（200 OK）：InterviewEvaluationOut
    错误响应：404 Not Found — 该面试尚无评价

    调用：InterviewEvaluationService.get_evaluation_by_interview()
    """
    return await InterviewEvaluationService.get_evaluation_by_interview(
        db, interview_id
    )


@router.get(
    "/evaluations/by-resume/{resume_id}",
    response_model=list[InterviewEvaluationOut],
    summary="获取候选人所有面试评价",
)
async def list_evaluations_by_resume(
    resume_id: int,  # 路径参数：简历 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    查询某候选人（简历）在所有面试轮次中的评价列表。

    HTTP 方法：GET
    路径：/api/v1/recruitment/evaluations/by-resume/{resume_id}
    权限：登录用户（任意角色）

    路径参数：
        resume_id (int): 简历主键 ID

    响应（200 OK）：list[InterviewEvaluationOut]（按面试轮次排列，可能为空列表）

    调用：InterviewEvaluationService.list_evaluations_by_resume()
    """
    return await InterviewEvaluationService.list_evaluations_by_resume(db, resume_id)


# ===========================================================================
# Offer
# 注意：/offers/bulk-action 必须在 /offers/{offer_id} 之前注册
# ===========================================================================

@router.get("/offers", response_model=PaginatedResponse, summary="获取Offer列表")
async def list_offers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    demand_id: Optional[int] = Query(default=None, description="按招聘需求ID过滤"),
    approval_status: Optional[str] = Query(default=None, description="Offer审批状态（pending/approved/rejected）"),
    offer_status: Optional[str] = Query(default=None, description="Offer业务状态（draft/sent/accepted/declined/revoked）"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    分页获取 Offer 列表，支持按需求、审批状态、Offer 状态过滤。

    HTTP 方法：GET
    路径：/api/v1/recruitment/offers
    权限：登录用户（任意角色）

    查询参数：
        page            (int, 默认1):  页码
        page_size       (int, 默认20): 每页数量
        demand_id       (int, 可选):   按招聘需求 ID 过滤
        approval_status (str, 可选):   Offer 审批状态过滤
        offer_status    (str, 可选):   Offer 业务状态过滤

    响应（200 OK）：PaginatedResponse（items 为 OfferOut 列表）

    调用：OfferService.list_offers()
    """
    items, total = await OfferService.list_offers(
        db,
        page=page,
        page_size=page_size,
        demand_id=demand_id,
        approval_status=approval_status,
        offer_status=offer_status,
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[OfferOut.model_validate(o) for o in items],
    )


@router.post("/offers/bulk-action", response_model=BulkOfferActionResult, summary="Offer批量操作(批量审批/撤回)")
async def bulk_offer_action(
    data: BulkOfferActionRequest,  # 请求体：offer_ids 列表 + action（approve/revoke）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    对多个 Offer 执行批量操作（批量审批通过或批量撤回）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/offers/bulk-action
    权限：登录用户（任意角色，业务层可校验操作权限）

    注意：此路由必须在 /offers/{offer_id} 之前注册，避免 "bulk-action" 被误解为 offer_id。

    请求体：BulkOfferActionRequest
        offer_ids (list[int]): 要操作的 Offer ID 列表
        action    (str):       操作类型（"approve"=批量审批通过 / "revoke"=批量撤回）

    响应（200 OK）：BulkOfferActionResult
        { "updated": 3, "failed": 0 }

    调用：OfferService.bulk_action()
    """
    return await OfferService.bulk_action(db, data)


@router.get("/offers/{offer_id}", response_model=OfferOut, summary="获取Offer详情")
async def get_offer(
    offer_id: int,  # 路径参数：Offer ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单个 Offer 的详细信息。

    HTTP 方法：GET
    路径：/api/v1/recruitment/offers/{offer_id}
    权限：登录用户（任意角色）

    路径参数：
        offer_id (int): Offer 主键 ID

    响应（200 OK）：OfferOut（含薪资信息、状态、候选人关联等）
    错误响应：404 Not Found — Offer 不存在

    调用：OfferService.get_offer()
    """
    return await OfferService.get_offer(db, offer_id)


@router.post("/offers", response_model=OfferOut, status_code=201, summary="创建Offer")
async def create_offer(
    data: OfferCreate,  # 请求体：简历ID、岗位、薪资结构、入职日期、有效期等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    为候选人创建 Offer 记录。

    HTTP 方法：POST
    路径：/api/v1/recruitment/offers
    权限：登录用户（任意角色）

    请求体：OfferCreate
        resume_id     (int, 必填):    关联简历 ID
        demand_id     (int, 可选):    关联招聘需求 ID
        position_name (str, 必填):    岗位名称
        salary        (Decimal):      薪资（月薪）
        offer_date    (date, 必填):   Offer 发送日期
        expire_date   (date, 必填):   Offer 有效截止日期
        onboard_date  (date, 可选):   期望入职日期
        其他字段见 schema 定义

    响应（201 Created）：OfferOut

    调用：OfferService.create_offer()
    """
    return await OfferService.create_offer(db, data)


@router.put("/offers/{offer_id}", response_model=OfferOut, summary="更新Offer")
async def update_offer(
    offer_id: int,  # 路径参数：Offer ID
    data: OfferUpdate,  # 请求体：需要更新的字段（薪资、入职日期等）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    更新 Offer 信息（通常在 Offer 审批前可修改薪资等信息）。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/offers/{offer_id}
    权限：登录用户（任意角色）

    路径参数：
        offer_id (int): Offer 主键 ID

    请求体：OfferUpdate（所有字段均可选）

    响应（200 OK）：OfferOut
    错误响应：404 Not Found — Offer 不存在

    调用：OfferService.update_offer()
    """
    return await OfferService.update_offer(db, offer_id, data)


@router.post(
    "/offers/{offer_id}/approve",
    response_model=OfferOut,
    summary="审批Offer",
)
async def approve_offer(
    offer_id: int,  # 路径参数：Offer ID
    data: OfferApprovalRequest,  # 请求体：审批动作（approved/rejected）+ 意见
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    对 Offer 执行审批操作（通过或拒绝）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/offers/{offer_id}/approve
    权限：登录用户（任意角色，业务层校验审批人权限）

    路径参数：
        offer_id (int): Offer 主键 ID

    请求体：OfferApprovalRequest
        action  (str, 必填): "approved"=审批通过 / "rejected"=拒绝
        comment (str, 可选): 审批意见

    业务说明：
        审批通过后 Offer 的 approval_status 变为 approved，
        才可执行"发送Offer"操作。

    响应（200 OK）：OfferOut（含更新后的 approval_status）
    错误响应：404 Not Found — Offer 不存在

    调用：OfferService.approve_offer()
    """
    return await OfferService.approve_offer(db, offer_id, data)


@router.post(
    "/offers/{offer_id}/send",
    response_model=OfferOut,
    summary="发送Offer",
)
async def send_offer(
    offer_id: int,  # 路径参数：Offer ID
    data: OfferSendRequest,  # 请求体：发送方式（邮件/短信/系统内）等
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),  # 注意：需要 current_user.id 用于通知
):
    """
    向候选人发送 Offer，并向操作人推送发送成功的站内通知。

    HTTP 方法：POST
    路径：/api/v1/recruitment/offers/{offer_id}/send
    权限：登录用户（任意角色，通常为 HR 或招聘负责人）

    路径参数：
        offer_id (int): Offer 主键 ID

    请求体：OfferSendRequest（发送渠道、备注等）

    业务说明：
        Offer 必须先通过审批（approval_status=approved）才能发送。
        发送成功后 offer_status 更新为 sent。
        若 should_notify=True，向当前操作人（current_user）推送"Offer已发送"通知。

    响应（200 OK）：OfferOut（offer_status=sent）
    错误响应：
        404 Not Found — Offer 不存在
        422 Unprocessable Entity — Offer 尚未审批通过

    调用：OfferService.send_offer() + NotificationService.create()
    """
    offer, should_notify = await OfferService.send_offer(db, offer_id, data)
    # 向操作人发送"Offer已发送"确认通知
    if should_notify:
        from app.services.notification import NotificationService
        from app.schemas.notification import NotificationCreate
        candidate_name = offer.resume.candidate_name if offer.resume else "候选人"
        await NotificationService.create(db, NotificationCreate(
            recipient_id=current_user.id,
            title=f"Offer已发送：{candidate_name}",
            content=f"Offer已成功发送给候选人 {candidate_name}",
            notif_type="offer",
            ref_type="offer",
            ref_id=offer.id,
        ))
        await db.flush()
    return offer


@router.post(
    "/offers/{offer_id}/revoke",
    response_model=OfferOut,
    summary="撤回Offer",
)
async def revoke_offer(
    offer_id: int,  # 路径参数：Offer ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    撤回已发送的 Offer。

    HTTP 方法：POST
    路径：/api/v1/recruitment/offers/{offer_id}/revoke
    权限：登录用户（任意角色）

    路径参数：
        offer_id (int): Offer 主键 ID

    业务说明：
        只有处于 sent 状态的 Offer 才可撤回。
        撤回后 offer_status 变为 revoked，候选人将无法继续操作该 Offer。

    响应（200 OK）：OfferOut（offer_status=revoked）
    错误响应：404 Not Found — Offer 不存在

    调用：OfferService.revoke_offer()
    """
    return await OfferService.revoke_offer(db, offer_id)


@router.post(
    "/offers/{offer_id}/confirm",
    response_model=OfferOut,
    summary="候选人确认Offer(接受/拒绝)",
)
async def candidate_confirm_offer(
    offer_id: int,  # 路径参数：Offer ID
    data: OfferCandidateConfirmRequest,  # 请求体：候选人决定（accepted/declined）及拒绝原因
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    记录候选人对 Offer 的确认结果（接受或拒绝）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/offers/{offer_id}/confirm
    权限：登录用户（任意角色，通常由 HR 代为录入候选人的回复）

    路径参数：
        offer_id (int): Offer 主键 ID

    请求体：OfferCandidateConfirmRequest
        decision       (str, 必填): "accepted"=接受 / "declined"=拒绝
        decline_reason (str, 可选): 候选人拒绝原因（decision=declined 时填写）

    业务说明：
        候选人接受 Offer 后，offer_status 变为 accepted，
        可进一步创建入职记录（OnboardingRecord）或直接转换为员工档案。
        候选人拒绝后，offer_status 变为 declined，需求人数恢复可招募状态。

    响应（200 OK）：OfferOut（含更新后的 offer_status）
    错误响应：404 Not Found — Offer 不存在

    调用：OfferService.candidate_confirm_offer()
    """
    return await OfferService.candidate_confirm_offer(db, offer_id, data)


# ===========================================================================
# InternalReferral 内推
# 注意：固定路径端点（/stats、/export、/export-archive、/bulk-mark-paid、/leaderboard）
# 必须在 /{referral_id} 之前声明。
# ===========================================================================

@router.get("/referrals", response_model=PaginatedResponse, summary="获取内推列表")
async def list_referrals(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    referrer_id: Optional[int] = Query(default=None, description="按推荐人员工ID过滤"),
    candidate_status: Optional[str] = Query(default=None, description="候选人当前状态（screening/interview/offer/hired/rejected）"),
    payment_status: Optional[str] = Query(default=None, description="奖金发放状态（unpaid/first_paid/full_paid）"),
    date_from: Optional[date] = Query(default=None, description="推荐日期起始（含）"),
    date_to: Optional[date] = Query(default=None, description="推荐日期截止（含）"),
    keyword: Optional[str] = Query(default=None, description="按候选人姓名模糊搜索"),
    year: Optional[int] = Query(default=None, description="按年份过滤（用于归档视图）"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    分页获取内推记录列表，支持多维度过滤。

    HTTP 方法：GET
    路径：/api/v1/recruitment/referrals
    权限：登录用户（任意角色）

    查询参数：
        page             (int, 默认1):   页码
        page_size        (int, 默认20):  每页数量
        referrer_id      (int, 可选):    按推荐人（员工）ID 过滤
        candidate_status (str, 可选):    候选人当前招聘流程状态
        payment_status   (str, 可选):    奖金发放状态过滤
        date_from        (date, 可选):   推荐日期起始（YYYY-MM-DD）
        date_to          (date, 可选):   推荐日期截止（YYYY-MM-DD）
        keyword          (str, 可选):    候选人姓名关键词搜索
        year             (int, 可选):    按年份过滤（归档用，如 2024）

    响应（200 OK）：PaginatedResponse（items 为 InternalReferralOut 列表）

    调用：InternalReferralService.list_referrals()
    """
    items, total = await InternalReferralService.list_referrals(
        db, page=page, page_size=page_size,
        referrer_id=referrer_id, candidate_status=candidate_status,
        payment_status=payment_status, date_from=date_from, date_to=date_to, keyword=keyword,
        year=year,
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[InternalReferralOut.model_validate(r) for r in items],
    )


@router.get("/referrals/stats", response_model=ReferralStatsOut, summary="内推统计")
async def get_referral_stats(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取内推整体统计数据。

    HTTP 方法：GET
    路径：/api/v1/recruitment/referrals/stats
    权限：登录用户（任意角色）

    响应（200 OK）：ReferralStatsOut
        包含：总内推数、各状态数量、本月内推数、转化率、待发放奖金总额等

    调用：InternalReferralService.get_stats()
    """
    return await InternalReferralService.get_stats(db)


@router.get("/referrals/export", summary="导出内推记录Excel")
async def export_referrals(
    candidate_status: Optional[str] = Query(default=None, description="候选人状态过滤"),
    payment_status: Optional[str] = Query(default=None, description="发放状态过滤"),
    date_from: Optional[date] = Query(default=None, description="推荐日期起"),
    date_to: Optional[date] = Query(default=None, description="推荐日期止"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    按过滤条件导出内推记录为 Excel 文件（.xlsx 格式）。

    HTTP 方法：GET
    路径：/api/v1/recruitment/referrals/export
    权限：登录用户（任意角色）

    查询参数：
        candidate_status (str, 可选):  候选人状态过滤
        payment_status   (str, 可选):  奖金发放状态过滤
        date_from        (date, 可选): 推荐日期起始
        date_to          (date, 可选): 推荐日期截止

    响应（200 OK）：
        Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
        Content-Disposition: attachment; filename=referrals.xlsx

    调用：InternalReferralService.export_excel()
    """
    data = await InternalReferralService.export_excel(
        db, candidate_status=candidate_status, payment_status=payment_status,
        date_from=date_from, date_to=date_to,
    )
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=referrals.xlsx"},
    )


@router.get("/referrals/export-archive", summary="导出年度内推归档")
async def export_referral_archive(
    year: int = Query(..., description="年份（必填），如 2024"),  # ... 表示必填参数
    db: AsyncSession = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """
    导出指定年度的内推记录归档 Excel 文件。

    HTTP 方法：GET
    路径：/api/v1/recruitment/referrals/export-archive
    权限：登录用户（任意角色）

    查询参数：
        year (int, 必填): 要导出的年份（如 2024）

    响应（200 OK）：
        Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
        Content-Disposition: attachment; filename=referrals_{year}.xlsx

    调用：InternalReferralService.export_excel(db, year=year)
    """
    data = await InternalReferralService.export_excel(db, year=year)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=referrals_{year}.xlsx"},
    )


@router.post("/referrals/bulk-mark-paid", summary="批量标记奖金发放")
async def bulk_mark_paid(
    data: BulkMarkPaidRequest,  # 请求体：内推ID列表 + 发放类型（first/full）
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr", "finance")),  # 需要 admin/hr/finance 角色
):
    """
    批量标记内推奖金已发放（首次奖励或全额奖励）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/referrals/bulk-mark-paid
    权限：需要 admin / hr / finance 角色（Depends(require_roles("admin", "hr", "finance"))）

    请求体：BulkMarkPaidRequest
        referral_ids  (list[int]): 要操作的内推记录 ID 列表
        payment_type  (str):       发放类型（"first"=首次奖励 / "full"=全额奖励）

    业务说明：
        操作人 ID（current_user.id）会记录到发放记录中，用于审计追踪。

    响应（200 OK）：
        { "updated": 5 }  # 成功更新的记录数量

    调用：InternalReferralService.bulk_mark_paid()
    """
    count = await InternalReferralService.bulk_mark_paid(
        db, data.referral_ids, data.payment_type, current_user.id
    )
    return {"updated": count}


@router.get("/referrals/leaderboard", summary="内推排行榜", response_model=LeaderboardOut)
async def get_referral_leaderboard(
    year: int = Query(default=None),   # 年份，不传则取当前年
    month: int = Query(default=None),  # 月份，不传则统计全年
    db: AsyncSession = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """
    获取内推排行榜（按推荐人的成功内推数量排名）。

    HTTP 方法：GET
    路径：/api/v1/recruitment/referrals/leaderboard
    权限：登录用户（任意角色）

    查询参数：
        year  (int, 可选): 统计年份，默认为当前年
        month (int, 可选): 统计月份（1-12），不传则统计全年

    响应（200 OK）：LeaderboardOut
        包含按推荐成功数量降序排列的员工列表

    调用：InternalReferralService.get_leaderboard()
    """
    from datetime import date as date_cls
    if year is None:
        year = date_cls.today().year  # 默认取当前年份
    return await InternalReferralService.get_leaderboard(db, year=year, month=month)


@router.post("/referrals/{referral_id}/trigger-approval", summary="手动发起内推奖金审批")
async def trigger_referral_bonus_approval(
    referral_id: int,  # 路径参数：内推记录 ID
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),  # 需要 admin/hr 角色
):
    """
    手动触发内推奖金审批流程（通常在自动触发失败时使用）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/referrals/{referral_id}/trigger-approval
    权限：需要 admin / hr 角色（Depends(require_roles("admin", "hr"))）

    路径参数：
        referral_id (int): 内推记录主键 ID

    业务说明：
        正常情况下，候选人入职后系统自动触发奖金审批流程。
        本接口用于在审批流程未能自动触发时，由 admin/hr 手动发起。
        内部调用 InternalReferralService._trigger_bonus_approval()（私有方法）。

    响应（200 OK）：
        { "message": "审批通知已发送" }

    错误响应：404 Not Found — 内推记录不存在

    调用：InternalReferralService.get_referral() + _trigger_bonus_approval()
    """
    referral = await InternalReferralService.get_referral(db, referral_id)
    await InternalReferralService._trigger_bonus_approval(db, referral)
    return {"message": "审批通知已发送"}


@router.get("/referrals/{referral_id}", response_model=InternalReferralOut, summary="获取内推详情")
async def get_referral(
    referral_id: int,  # 路径参数：内推记录 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单条内推记录的详细信息。

    HTTP 方法：GET
    路径：/api/v1/recruitment/referrals/{referral_id}
    权限：登录用户（任意角色）

    路径参数：
        referral_id (int): 内推记录主键 ID

    响应（200 OK）：InternalReferralOut（含推荐人信息、候选人状态、奖金信息等）
    错误响应：404 Not Found — 内推记录不存在

    调用：InternalReferralService.get_referral()
    """
    return await InternalReferralService.get_referral(db, referral_id)


@router.post(
    "/referrals",
    response_model=InternalReferralOut,
    status_code=201,
    summary="创建内推记录",
)
async def create_referral(
    data: InternalReferralCreate,  # 请求体：推荐人ID、候选人信息、关联需求等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    创建新的内推记录（员工推荐候选人）。

    HTTP 方法：POST
    路径：/api/v1/recruitment/referrals
    权限：登录用户（任意角色，通常由推荐人本人或 HR 代为录入）

    请求体：InternalReferralCreate
        referrer_id     (int, 必填): 推荐人（员工）ID
        candidate_name  (str, 必填): 候选人姓名
        candidate_phone (str, 必填): 候选人手机号
        demand_id       (int, 可选): 关联的招聘需求 ID
        resume_id       (int, 可选): 关联的已创建简历 ID
        其他字段见 schema 定义

    响应（201 Created）：InternalReferralOut

    调用：InternalReferralService.create_referral()
    """
    return await InternalReferralService.create_referral(db, data)


@router.put(
    "/referrals/{referral_id}",
    response_model=InternalReferralOut,
    summary="更新内推记录(含奖励追踪)",
)
async def update_referral(
    referral_id: int,  # 路径参数：内推记录 ID
    data: InternalReferralUpdate,  # 请求体：更新字段（含奖金发放状态等）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    更新内推记录信息，包括奖励追踪状态。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/referrals/{referral_id}
    权限：登录用户（任意角色）

    路径参数：
        referral_id (int): 内推记录主键 ID

    请求体：InternalReferralUpdate（所有字段均可选）
        可更新字段包括：候选人状态、奖金发放状态（first_paid/full_paid）、备注等

    响应（200 OK）：InternalReferralOut
    错误响应：404 Not Found — 内推记录不存在

    调用：InternalReferralService.update_referral()
    """
    return await InternalReferralService.update_referral(db, referral_id, data)


# ===========================================================================
# ReferralRule 内推奖金规则
# ===========================================================================

@router.get("/referral-rules", response_model=List[ReferralRuleOut], summary="获取内推奖金规则列表")
async def list_referral_rules(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取全部内推奖金规则列表。

    HTTP 方法：GET
    路径：/api/v1/recruitment/referral-rules
    权限：登录用户（任意角色）

    响应（200 OK）：list[ReferralRuleOut]
        包含各岗位级别对应的奖金规则（首次奖励金额、全额奖励金额、发放条件等）

    调用：ReferralRuleService.list_rules()
    """
    return await ReferralRuleService.list_rules(db)


@router.post("/referral-rules", response_model=ReferralRuleOut, status_code=201, summary="创建内推奖金规则")
async def create_referral_rule(
    data: ReferralRuleCreate,  # 请求体：规则名称、奖金金额、适用岗位级别等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),  # 需要 admin/hr 角色
):
    """
    创建新的内推奖金规则。

    HTTP 方法：POST
    路径：/api/v1/recruitment/referral-rules
    权限：需要 admin / hr 角色（Depends(require_roles("admin", "hr"))）

    请求体：ReferralRuleCreate
        name          (str, 必填):     规则名称（如"P6以上岗位"）
        first_bonus   (Decimal, 必填): 首次奖励金额（候选人试用期满时发放）
        full_bonus    (Decimal, 必填): 全额奖励金额（候选人转正后发放）
        其他字段见 schema 定义

    响应（201 Created）：ReferralRuleOut

    调用：ReferralRuleService.create_rule()
    """
    return await ReferralRuleService.create_rule(db, data)


@router.put("/referral-rules/{rule_id}", response_model=ReferralRuleOut, summary="更新内推奖金规则")
async def update_referral_rule(
    rule_id: int,  # 路径参数：规则 ID
    data: ReferralRuleCreate,  # 请求体：使用 Create schema（全量更新）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),  # 需要 admin/hr 角色
):
    """
    更新指定内推奖金规则（全量更新，使用 Create schema）。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/referral-rules/{rule_id}
    权限：需要 admin / hr 角色（Depends(require_roles("admin", "hr"))）

    路径参数：
        rule_id (int): 奖金规则主键 ID

    请求体：ReferralRuleCreate（全量字段，非 partial update）

    响应（200 OK）：ReferralRuleOut
    错误响应：404 Not Found — 规则不存在

    调用：ReferralRuleService.update_rule()
    """
    return await ReferralRuleService.update_rule(db, rule_id, data)


@router.delete("/referral-rules/{rule_id}", status_code=204, summary="删除内推奖金规则")
async def delete_referral_rule(
    rule_id: int,  # 路径参数：规则 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "hr")),  # 需要 admin/hr 角色
):
    """
    删除指定内推奖金规则。

    HTTP 方法：DELETE
    路径：/api/v1/recruitment/referral-rules/{rule_id}
    权限：需要 admin / hr 角色（Depends(require_roles("admin", "hr"))）

    路径参数：
        rule_id (int): 奖金规则主键 ID

    响应（204 No Content）：无响应体
    错误响应：404 Not Found — 规则不存在

    调用：ReferralRuleService.delete_rule()
    """
    await ReferralRuleService.delete_rule(db, rule_id)


# ===========================================================================
# Statistics 数据分析
# ===========================================================================

@router.get("/stats", response_model=RecruitmentStatsOut, summary="招聘整体统计")
async def get_recruitment_stats(
    demand_id: Optional[int] = Query(default=None, description="按需求ID筛选(可选)，不传则统计全局数据"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取招聘整体统计数据（漏斗转化率 + 渠道效果）。

    HTTP 方法：GET
    路径：/api/v1/recruitment/stats
    权限：登录用户（任意角色）

    查询参数：
        demand_id (int, 可选): 按特定招聘需求 ID 筛选。不传则返回全局统计。

    响应（200 OK）：RecruitmentStatsOut
        {
            "funnel": {...},                  # 漏斗各阶段数量和转化率
            "channel_effectiveness": [...]    # 各渠道的简历量、转化率等
        }

    调用：
        RecruitmentStatsService.get_funnel()
        RecruitmentStatsService.get_channel_effectiveness()
    """
    funnel = await RecruitmentStatsService.get_funnel(db, demand_id=demand_id)
    channel_effectiveness = await RecruitmentStatsService.get_channel_effectiveness(db)
    return RecruitmentStatsOut(
        funnel=funnel,
        channel_effectiveness=channel_effectiveness,
    )


@router.get("/stats/funnel", response_model=RecruitmentFunnelOut, summary="招聘漏斗转化率")
async def get_funnel_stats(
    demand_id: Optional[int] = Query(default=None, description="按需求ID筛选(可选)"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取招聘漏斗各阶段的数量和转化率。

    HTTP 方法：GET
    路径：/api/v1/recruitment/stats/funnel
    权限：登录用户（任意角色）

    查询参数：
        demand_id (int, 可选): 按特定招聘需求 ID 筛选。不传则返回全局漏斗数据。

    响应（200 OK）：RecruitmentFunnelOut
        包含各阶段（简历→初筛→面试→Offer→入职）的数量及相邻阶段转化率

    调用：RecruitmentStatsService.get_funnel()
    """
    return await RecruitmentStatsService.get_funnel(db, demand_id=demand_id)


@router.get(
    "/stats/channels",
    response_model=list[ChannelEffectivenessOut],
    summary="渠道效果统计",
)
async def get_channel_effectiveness(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取各招聘渠道的效果统计数据。

    HTTP 方法：GET
    路径：/api/v1/recruitment/stats/channels
    权限：登录用户（任意角色）

    响应（200 OK）：list[ChannelEffectivenessOut]
        每个渠道包含：渠道名称、简历总数、通过率、面试通过率、Offer 接受率、平均招聘周期等

    调用：RecruitmentStatsService.get_channel_effectiveness()
    """
    return await RecruitmentStatsService.get_channel_effectiveness(db)


# ===========================================================================
# OfferTemplate Offer模板
# ===========================================================================

@router.get("/offer-templates", response_model=list[OfferTemplateOut], summary="获取Offer模板列表")
async def list_offer_templates(
    is_active: Optional[bool] = Query(default=None, description="是否启用（True=仅启用，False=仅禁用，不传=全部）"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取 Offer 模板列表，可按启用状态过滤。

    HTTP 方法：GET
    路径：/api/v1/recruitment/offer-templates
    权限：登录用户（任意角色）

    查询参数：
        is_active (bool, 可选): 按启用状态过滤

    响应（200 OK）：list[OfferTemplateOut]（无分页，模板数量通常较少）

    调用：OfferTemplateService.list_templates()
    """
    return await OfferTemplateService.list_templates(db, is_active=is_active)


@router.get("/offer-templates/{template_id}", response_model=OfferTemplateOut, summary="获取Offer模板详情")
async def get_offer_template(
    template_id: int,  # 路径参数：模板 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单个 Offer 模板的详细信息。

    HTTP 方法：GET
    路径：/api/v1/recruitment/offer-templates/{template_id}
    权限：登录用户（任意角色）

    路径参数：
        template_id (int): Offer 模板主键 ID

    响应（200 OK）：OfferTemplateOut（含模板名称、正文占位符等）
    错误响应：404 Not Found — 模板不存在

    调用：OfferTemplateService.get_template()
    """
    return await OfferTemplateService.get_template(db, template_id)


@router.post("/offer-templates", response_model=OfferTemplateOut, status_code=201, summary="创建Offer模板")
async def create_offer_template(
    data: OfferTemplateCreate,  # 请求体：模板名称、正文内容（含占位符）、适用岗位等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    创建新的 Offer 模板。

    HTTP 方法：POST
    路径：/api/v1/recruitment/offer-templates
    权限：登录用户（任意角色）

    请求体：OfferTemplateCreate
        name    (str, 必填): 模板名称
        content (str, 必填): Offer 正文内容（支持 {{candidate_name}} 等占位符）
        其他字段见 schema 定义

    响应（201 Created）：OfferTemplateOut

    调用：OfferTemplateService.create_template()
    """
    return await OfferTemplateService.create_template(db, data)


@router.put("/offer-templates/{template_id}", response_model=OfferTemplateOut, summary="更新Offer模板")
async def update_offer_template(
    template_id: int,  # 路径参数：模板 ID
    data: OfferTemplateUpdate,  # 请求体：需要更新的字段
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    更新指定 Offer 模板的内容。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/offer-templates/{template_id}
    权限：登录用户（任意角色）

    路径参数：
        template_id (int): Offer 模板主键 ID

    请求体：OfferTemplateUpdate（所有字段均可选）

    响应（200 OK）：OfferTemplateOut
    错误响应：404 Not Found — 模板不存在

    调用：OfferTemplateService.update_template()
    """
    return await OfferTemplateService.update_template(db, template_id, data)


# ===========================================================================
# OnboardingRecord 入职管理
# ===========================================================================

@router.get("/onboarding", response_model=PaginatedResponse, summary="获取入职记录列表")
async def list_onboarding_records(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    status: Optional[str] = Query(default=None, alias="status", description="入职状态（pending/in_progress/completed/cancelled）"),
    department_id: Optional[int] = Query(default=None, description="按部门ID过滤"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    分页获取入职记录列表，支持按状态和部门过滤。

    HTTP 方法：GET
    路径：/api/v1/recruitment/onboarding
    权限：登录用户（任意角色）

    查询参数：
        page          (int, 默认1):   页码
        page_size     (int, 默认20):  每页数量
        status        (str, 可选):    入职流程状态过滤
        department_id (int, 可选):    按入职部门 ID 过滤

    响应（200 OK）：PaginatedResponse（items 为 OnboardingRecordOut 列表）

    调用：OnboardingService.list_records()
    """
    items, total = await OnboardingService.list_records(
        db,
        page=page,
        page_size=page_size,
        status_filter=status,
        department_id=department_id,
    )
    return PaginatedResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[OnboardingRecordOut.model_validate(r) for r in items],
    )


@router.get("/onboarding/{record_id}", response_model=OnboardingRecordOut, summary="获取入职记录详情")
async def get_onboarding_record(
    record_id: int,  # 路径参数：入职记录 ID
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    获取单条入职记录的详细信息。

    HTTP 方法：GET
    路径：/api/v1/recruitment/onboarding/{record_id}
    权限：登录用户（任意角色）

    路径参数：
        record_id (int): 入职记录主键 ID

    响应（200 OK）：OnboardingRecordOut（含入职清单、完成进度、关联 Offer 信息等）
    错误响应：404 Not Found — 入职记录不存在

    调用：OnboardingService.get_record()
    """
    return await OnboardingService.get_record(db, record_id)


@router.post("/onboarding", response_model=OnboardingRecordOut, status_code=201, summary="创建入职记录")
async def create_onboarding_record(
    data: OnboardingRecordCreate,  # 请求体：关联Offer ID、预计入职日期、入职部门等
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    为接受 Offer 的候选人创建入职流程记录。

    HTTP 方法：POST
    路径：/api/v1/recruitment/onboarding
    权限：登录用户（任意角色）

    请求体：OnboardingRecordCreate
        offer_id      (int, 必填):   关联的 Offer ID（Offer 状态须为 accepted）
        onboard_date  (date, 必填):  计划入职日期
        department_id (int, 可选):   入职部门 ID
        其他字段见 schema 定义

    业务说明：
        创建入职记录后，HR 可通过更新接口逐步完成各项入职前置工作
        （背景调查、材料收集、IT 账号开通等），直至状态变为 completed。

    响应（201 Created）：OnboardingRecordOut

    调用：OnboardingService.create_record()
    """
    return await OnboardingService.create_record(db, data)


@router.put("/onboarding/{record_id}", response_model=OnboardingRecordOut, summary="更新入职记录")
async def update_onboarding_record(
    record_id: int,  # 路径参数：入职记录 ID
    data: OnboardingRecordUpdate,  # 请求体：需要更新的字段（状态、完成事项等）
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    更新入职记录信息（推进入职流程状态或更新入职事项完成情况）。

    HTTP 方法：PUT
    路径：/api/v1/recruitment/onboarding/{record_id}
    权限：登录用户（任意角色）

    路径参数：
        record_id (int): 入职记录主键 ID

    请求体：OnboardingRecordUpdate（所有字段均可选）
        可更新字段包括：入职状态、各项前置任务完成标记、备注等

    响应（200 OK）：OnboardingRecordOut（含更新后的状态和进度）
    错误响应：404 Not Found — 入职记录不存在

    调用：OnboardingService.update_record()
    """
    return await OnboardingService.update_record(db, record_id, data)
