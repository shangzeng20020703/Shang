"""
猎头公司管理 API 路由模块

路由前缀（注册于 router.py）: /api/v1/headhunter

本模块管理与猎头公司的合作关系，包括猎头公司档案的维护以及简历推荐记录的跟踪，
是售后管理系统招聘模块的组成部分之一。

核心概念：
  - 猎头公司（Headhunter）：
      合作的外部猎头服务机构，记录公司基本信息、联系方式、合作状态及历史合作数据。
      一家猎头公司可以有多条候选人推荐记录（recommendations）。

  - 推荐记录（Recommendation）：
      猎头公司向本公司推荐的候选人记录，关联猎头公司、候选人姓名、推荐职位、
      推荐状态（推荐中 / 面试中 / 已录用 / 未录用）及推荐费用等信息。

端点清单：

  猎头公司管理：
       GET    /headhunter           — 分页查询猎头公司列表（含推荐数量）
       POST   /headhunter           — 新建猎头公司档案
       GET    /headhunter/{id}      — 获取猎头公司详情（含推荐数量）
       PUT    /headhunter/{id}      — 更新猎头公司信息
       DELETE /headhunter/{id}      — 删除猎头公司

  推荐记录管理：
       GET    /headhunter/recommendations/list   — 分页查询推荐记录列表
       POST   /headhunter/recommendations        — 新增推荐记录
       PUT    /headhunter/recommendations/{id}   — 更新推荐记录（如状态变更）
       DELETE /headhunter/recommendations/{id}   — 删除推荐记录

路由顺序说明：
  GET /headhunter/recommendations/list 和 POST /headhunter/recommendations 为固定路径，
  在代码中必须先于 /{headhunter_id} 动态路由声明（否则 "recommendations" 会被误解析为 ID）。

权限说明：
  - 只读端点（GET）：任意已登录用户（get_current_user）
  - 写入端点（POST / PUT / DELETE）：需要 admin 或 hr 角色（require_roles）
  - recommendation_count 字段在路由层由关联关系 h.recommendations 的长度计算，而非 Service 层统计

对应 Service（Service Class 模式）：
  - HeadhunterService     — app.services.headhunter.HeadhunterService
  - RecommendationService — app.services.headhunter.RecommendationService
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.headhunter import (
    HeadhunterCreate, HeadhunterListResponse, HeadhunterOut, HeadhunterUpdate,
    RecommendationCreate, RecommendationListResponse, RecommendationOut, RecommendationUpdate,
)
from app.services.headhunter import HeadhunterService, RecommendationService

router = APIRouter()


# ==================== 猎头公司 CRUD ====================

@router.get("", response_model=HeadhunterListResponse, summary="获取猎头公司列表")
async def list_headhunters(
    keyword: Optional[str] = Query(None, description="搜索关键字（公司名称/联系人）"),
    status: Optional[str] = Query(None, description="状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /headhunter — 分页查询猎头公司列表

    用途：获取与本公司合作的猎头公司列表，支持名称/联系人模糊搜索和状态过滤。
          每条记录附带该公司累计推荐候选人数量（recommendation_count）。

    Query 参数：
        - keyword: str    — 模糊搜索关键字，匹配公司名称或联系人姓名（可选）
        - status: str     — 按合作状态筛选（active / inactive，可选）
        - page: int       — 页码（从 1 开始，默认 1）
        - page_size: int  — 每页条数（默认 20，范围 1~100）

    响应（200 OK）：HeadhunterListResponse
        { "total": int, "items": list[HeadhunterOut] }
        HeadhunterOut 含：id、name、contact_person、contact_phone、status、recommendation_count

    权限：任意已登录用户（get_current_user）

    Service：HeadhunterService.get_list(db, keyword, status, page, page_size)
    注意：recommendation_count 在路由层通过 len(h.recommendations) 计算（需 Service 预加载关联）
    """
    total, items = await HeadhunterService.get_list(
        db, keyword=keyword, status=status, page=page, page_size=page_size
    )
    # 补充推荐数量（简单计算）
    out_items = []
    for h in items:
        out = HeadhunterOut.model_validate(h)
        out = out.model_copy(update={"recommendation_count": len(h.recommendations) if h.recommendations else 0})
        out_items.append(out)
    return HeadhunterListResponse(total=total, items=out_items)


@router.post("", response_model=HeadhunterOut, status_code=status.HTTP_201_CREATED, summary="新增猎头公司")
async def create_headhunter(
    data: HeadhunterCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /headhunter — 新建猎头公司档案

    用途：录入新合作猎头公司的基本信息，建立档案以便后续进行推荐记录关联和佣金管理。

    请求体（HeadhunterCreate）：
        - name: str              — 猎头公司全称（系统内应唯一）
        - contact_person: str    — 主要对接联系人姓名
        - contact_phone: str     — 联系电话
        - contact_email: Optional[str] — 联系邮箱
        - service_area: Optional[str]  — 擅长领域/服务范围（如"互联网/AI方向"）
        - commission_rate: Optional[float] — 标准佣金比例（0.0~1.0，如 0.15 表示 15%）
        - status: str            — 合作状态（active / inactive，默认 active）
        - notes: Optional[str]   — 备注

    响应（201 Created）：HeadhunterOut — 含 id 及所有字段

    权限：admin 或 hr 角色（require_roles）

    Service：HeadhunterService.create(db, data)
    """
    obj = await HeadhunterService.create(db, data)
    return HeadhunterOut.model_validate(obj)


@router.get("/{headhunter_id}", response_model=HeadhunterOut, summary="获取猎头公司详情")
async def get_headhunter(
    headhunter_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /headhunter/{headhunter_id} — 获取猎头公司详情

    用途：根据主键 ID 查询猎头公司的完整信息，包含累计推荐数量。

    路径参数：
        - headhunter_id: int — 猎头公司主键 ID

    响应（200 OK）：HeadhunterOut — 含所有字段及 recommendation_count
    响应（404 Not Found）：猎头公司不存在

    权限：任意已登录用户（get_current_user）

    Service：HeadhunterService.get_by_id(db, headhunter_id)
    注意：recommendation_count 在路由层通过 len(obj.recommendations) 计算
    """
    obj = await HeadhunterService.get_by_id(db, headhunter_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="猎头公司不存在")
    out = HeadhunterOut.model_validate(obj)
    return out.model_copy(update={"recommendation_count": len(obj.recommendations) if obj.recommendations else 0})


@router.put("/{headhunter_id}", response_model=HeadhunterOut, summary="更新猎头公司信息")
async def update_headhunter(
    headhunter_id: int,
    data: HeadhunterUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /headhunter/{headhunter_id} — 更新猎头公司信息

    用途：修改猎头公司的联系方式、状态、佣金比例等资料。

    路径参数：
        - headhunter_id: int — 目标猎头公司主键 ID

    请求体（HeadhunterUpdate）：与 HeadhunterCreate 字段相同，全部可选，仅传需变更的字段

    响应（200 OK）：HeadhunterOut — 更新后的公司数据
    响应（404 Not Found）：猎头公司不存在

    权限：admin 或 hr 角色（require_roles）

    Service：HeadhunterService.update(db, headhunter_id, data)
    """
    obj = await HeadhunterService.update(db, headhunter_id, data)
    if obj is None:
        raise HTTPException(status_code=404, detail="猎头公司不存在")
    return HeadhunterOut.model_validate(obj)


@router.delete("/{headhunter_id}", summary="删除猎头公司")
async def delete_headhunter(
    headhunter_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /headhunter/{headhunter_id} — 删除猎头公司

    用途：删除指定猎头公司档案。若存在关联推荐记录，需先删除推荐记录或由 Service 级联处理。

    路径参数：
        - headhunter_id: int — 目标猎头公司主键 ID

    响应（200 OK）：{ "detail": "删除成功" }
    响应（404 Not Found）：猎头公司不存在

    权限：admin 或 hr 角色（require_roles）

    Service：HeadhunterService.delete(db, headhunter_id)
             返回 True 表示成功，False 表示不存在（触发 404）
    """
    ok = await HeadhunterService.delete(db, headhunter_id)
    if not ok:
        raise HTTPException(status_code=404, detail="猎头公司不存在")
    return {"detail": "删除成功"}


# ==================== 推荐记录 ====================

@router.get("/recommendations/list", response_model=RecommendationListResponse, summary="获取推荐记录列表")
async def list_recommendations(
    headhunter_id: Optional[int] = Query(None, description="按猎头公司筛选"),
    status: Optional[str] = Query(None, description="按推荐状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /headhunter/recommendations/list — 查询猎头推荐记录列表

    用途：获取所有猎头公司的候选人推荐记录，可按猎头公司或推荐状态过滤。
          用于招聘进度跟踪和候选人管线管理。

    注意：此路由使用 /recommendations/list 路径（而非 /recommendations），
          是为了避免与 /{headhunter_id} 动态路由冲突（需在动态路由之前注册）。

    Query 参数：
        - headhunter_id: int — 只查询指定猎头公司的推荐记录（可选）
        - status: str        — 按推荐状态过滤（submitted / interviewing / offered /
                               hired / rejected，可选）
        - page: int          — 页码（从 1 开始，默认 1）
        - page_size: int     — 每页条数（默认 20，范围 1~100）

    响应（200 OK）：RecommendationListResponse
        { "total": int, "items": list[RecommendationOut] }
        RecommendationOut 含：id、headhunter_id、headhunter_name、candidate_name、
                              position、status、commission_amount、recommendation_date

    权限：任意已登录用户（get_current_user）

    Service：RecommendationService.get_list(db, headhunter_id, status, page, page_size)
    注意：headhunter_name 在路由层通过关联对象 rec.headhunter.name 填充
    """
    total, items = await RecommendationService.get_list(
        db, headhunter_id=headhunter_id, status=status, page=page, page_size=page_size
    )
    out_items = []
    for rec in items:
        out = RecommendationOut.model_validate(rec)
        hn = rec.headhunter.name if rec.headhunter else None
        out_items.append(out.model_copy(update={"headhunter_name": hn}))
    return RecommendationListResponse(total=total, items=out_items)


@router.post(
    "/recommendations",
    response_model=RecommendationOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增推荐记录",
)
async def create_recommendation(
    data: RecommendationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /headhunter/recommendations — 新增猎头推荐记录

    用途：记录某猎头公司推荐了一位候选人来应聘指定职位。
          创建时会校验猎头公司是否存在，若不存在则返回 404。

    请求体（RecommendationCreate）：
        - headhunter_id: int         — 来源猎头公司 ID（必须已存在）
        - candidate_name: str        — 候选人姓名
        - candidate_phone: Optional[str] — 候选人联系电话
        - candidate_email: Optional[str] — 候选人邮箱
        - position: str              — 应聘职位
        - department_id: Optional[int] — 目标部门 ID
        - status: str                — 推荐初始状态（通常为 submitted）
        - commission_amount: Optional[Decimal] — 预计推荐费金额（元）
        - recommendation_date: date  — 推荐日期
        - resume_url: Optional[str]  — 简历附件链接
        - notes: Optional[str]       — 备注

    响应（201 Created）：RecommendationOut — 含 id、headhunter_name 及所有字段
    响应（404 Not Found）：猎头公司不存在时返回

    权限：admin 或 hr 角色（require_roles）

    Service：RecommendationService.create(db, data)
    注意：路由层先查询猎头公司以校验存在性，同时用于填充响应中的 headhunter_name
    """
    # Validate headhunter exists
    hh = await HeadhunterService.get_by_id(db, data.headhunter_id)
    if hh is None:
        raise HTTPException(status_code=404, detail="猎头公司不存在")
    obj = await RecommendationService.create(db, data)
    out = RecommendationOut.model_validate(obj)
    return out.model_copy(update={"headhunter_name": hh.name})


@router.put("/recommendations/{rec_id}", response_model=RecommendationOut, summary="更新推荐记录")
async def update_recommendation(
    rec_id: int,
    data: RecommendationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /headhunter/recommendations/{rec_id} — 更新推荐记录

    用途：更新候选人推荐进度（如状态从 submitted 变为 interviewing 或 hired），
          或修改推荐费用、备注等信息。是推荐流程跟踪的核心操作。

    路径参数：
        - rec_id: int — 推荐记录主键 ID

    请求体（RecommendationUpdate）：
        - status: Optional[str]            — 新状态（submitted / interviewing /
                                             offered / hired / rejected）
        - commission_amount: Optional[Decimal] — 更新后的推荐费金额
        - notes: Optional[str]             — 更新备注
        - （其他可变更字段）

    响应（200 OK）：RecommendationOut — 更新后的推荐记录（含 headhunter_name）
    响应（404 Not Found）：推荐记录不存在

    权限：admin 或 hr 角色（require_roles）

    Service：RecommendationService.update(db, rec_id, data)
    """
    obj = await RecommendationService.update(db, rec_id, data)
    if obj is None:
        raise HTTPException(status_code=404, detail="推荐记录不存在")
    hn = obj.headhunter.name if obj.headhunter else None
    out = RecommendationOut.model_validate(obj)
    return out.model_copy(update={"headhunter_name": hn})


@router.delete("/recommendations/{rec_id}", summary="删除推荐记录")
async def delete_recommendation(
    rec_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /headhunter/recommendations/{rec_id} — 删除推荐记录

    用途：物理删除指定的猎头推荐记录，通常用于删除录入错误的数据。

    路径参数：
        - rec_id: int — 推荐记录主键 ID

    响应（200 OK）：{ "detail": "删除成功" }
    响应（404 Not Found）：推荐记录不存在

    权限：admin 或 hr 角色（require_roles）

    Service：RecommendationService.delete(db, rec_id)
             返回 True 表示成功，False 表示不存在（触发 404）
    """
    ok = await RecommendationService.delete(db, rec_id)
    if not ok:
        raise HTTPException(status_code=404, detail="推荐记录不存在")
    return {"detail": "删除成功"}
