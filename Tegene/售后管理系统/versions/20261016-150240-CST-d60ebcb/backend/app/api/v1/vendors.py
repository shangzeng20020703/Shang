"""
外包供应商管理 API 路由模块

路由前缀（注册于 router.py）: /api/v1/vendors

本模块涵盖售后管理系统中外包供应商的完整业务链，包含：

  1. 供应商主体管理（CRUD）
       POST   /vendors                  — 新建供应商
       GET    /vendors                  — 分页查询供应商列表（支持类型/状态/关键词过滤）
       GET    /vendors/stats            — 供应商汇总统计（总活跃数、猎头数、合同金额）
       GET    /vendors/{id}             — 获取单个供应商详情
       PUT    /vendors/{id}             — 更新供应商信息
       DELETE /vendors/{id}             — 删除供应商

  2. 供应商合同管理
       POST   /vendors/contracts        — 新建合同
       GET    /vendors/contracts        — 分页查询合同列表（支持 vendor_id/状态/类型过滤）
       GET    /vendors/contracts/{cid}  — 获取合同详情
       PUT    /vendors/contracts/{cid}  — 更新合同
       DELETE /vendors/contracts/{cid}  — 删除合同

  3. 猎头佣金管理
       POST   /vendors/commissions             — 新建佣金记录
       GET    /vendors/commissions             — 分页查询佣金列表
       GET    /vendors/commissions/{cid}       — 获取佣金详情
       PUT    /vendors/commissions/{cid}       — 更新佣金记录
       POST   /vendors/commissions/{cid}/payment  — 处理佣金付款
       POST   /vendors/commissions/{cid}/refund   — 保证期退款处理
       DELETE /vendors/commissions/{cid}       — 删除佣金记录

  4. 供应商绩效评估
       GET    /vendors/evaluations      — 评分历史列表
       POST   /vendors/evaluations      — 提交新评分
       POST   /vendors/{id}/evaluate    — 年度评估入口（更新供应商综合评分）
       GET    /vendors/{id}/score-summary — 各维度历史平均分汇总

  5. 黑名单管理
       GET    /vendors/blacklist        — 查询黑名单列表
       POST   /vendors/blacklist        — 将供应商加入黑名单
       DELETE /vendors/blacklist/{id}   — 将供应商移出黑名单

路由顺序说明：
  固定路径（/stats, /contracts, /commissions, /evaluations, /blacklist）
  必须在动态路径（/{vendor_id}）之前注册，否则 FastAPI 会将路径段误解析为 vendor_id。

权限说明：
  - 只读端点：任意已登录用户（get_current_user）
  - 写入端点：需要 admin 或 hr 角色（require_roles("admin", "hr")）
  - 评估端点：需要 admin / hr / manager 角色

对应 Service：app.services.vendor（模块别名 svc）
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.vendor import (
    BlacklistOut,
    BlacklistRequest,
    HeadhunterCommissionCreate,
    HeadhunterCommissionOut,
    HeadhunterCommissionPage,
    HeadhunterCommissionUpdate,
    HeadhunterPaymentRequest,
    HeadhunterRefundRequest,
    LaborDispatchProjectCreate,
    LaborDispatchProjectOut,
    LaborDispatchProjectPage,
    LaborDispatchProjectUpdate,
    LaborDispatchWorkerCreate,
    LaborDispatchWorkerOut,
    LaborDispatchWorkerPage,
    LaborDispatchWorkerUpdate,
    LaborSettlementCreate,
    LaborSettlementOut,
    LaborSettlementPage,
    LaborSettlementUpdate,
    VendorContractCreate,
    VendorContractOut,
    VendorContractPage,
    VendorContractUpdate,
    VendorCreate,
    VendorEvaluationCreate,
    VendorEvaluationOut,
    VendorEvaluationPage,
    VendorEvaluationRequest,
    VendorOut,
    VendorPage,
    VendorScoreSummary,
    VendorUpdate,
)
from app.services import vendor as svc

router = APIRouter(
    dependencies=[Depends(require_roles("admin", "asset_admin", "finance"))]
)


# ───────────────────── Vendor CRUD (collection routes) ─────────────────────


@router.post("", response_model=VendorOut, status_code=201)
async def create_vendor(
    data: VendorCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> VendorOut:
    """
    POST /vendors — 新建供应商

    用途：创建一条供应商主体记录。供应商类型可为劳务派遣、猎头服务、外包服务、培训机构等。

    请求体（VendorCreate）：
        - company_name: str        — 供应商公司全称（唯一）
        - vendor_type: str         — 供应商类型（中文或英文枚举均可）
        - contact_person: str      — 主要联系人姓名
        - contact_phone: str       — 联系电话
        - contact_email: str       — 联系邮箱
        - address: Optional[str]   — 公司地址
        - credit_code: Optional[str] — 统一社会信用代码
        - status: str              — 初始状态（通常为 active）
        - notes: Optional[str]     — 备注信息

    响应（201 Created）：VendorOut — 含 id、所有字段及时间戳

    权限：admin 或 hr 角色

    Service：svc.create_vendor(db, data)
    """
    vendor = await svc.create_vendor(db, data)
    return VendorOut.model_validate(vendor)


@router.get("", response_model=VendorPage)
async def list_vendors(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    vendor_type: Optional[str] = Query(None, description="供应商类型(英文或中文均可)"),
    status: Optional[str] = Query(None, description="状态"),
    keyword: Optional[str] = Query(None, description="公司名称关键词"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VendorPage:
    """
    GET /vendors — 分页查询供应商列表

    用途：返回供应商列表，支持多维度过滤。前端可传英文类型码，服务端自动映射为数据库中文存储值。

    Query 参数：
        - skip: int         — 跳过条数，用于分页（默认 0，最小 0）
        - limit: int        — 每页条数（默认 20，范围 1~1000）
        - vendor_type: str  — 供应商类型过滤；支持英文（labor_dispatch / headhunter /
                              outsource / training / other）或中文（劳务派遣/猎头服务/…）
        - status: str       — 状态过滤（active / inactive / blacklisted 等）
        - keyword: str      — 公司名称模糊搜索关键词

    响应（200 OK）：VendorPage { total: int, items: list[VendorOut] }

    权限：任意已登录用户（get_current_user）

    Service：svc.list_vendors(db, skip, limit, vendor_type, status_filter, keyword)

    注意：vendor_type 英文→中文映射在本函数内完成（不依赖 Service 层），兼容前端传参习惯。
    """
    # 将前端传来的英文类型码映射为中文（兼容DB中文存储）
    _type_en_to_zh = {
        "labor_dispatch": "劳务派遣",
        "headhunter": "猎头服务",
        "outsource": "外包服务",
        "training": "培训机构",
        "other": "其他",
    }
    normalized_type = _type_en_to_zh.get(vendor_type, vendor_type) if vendor_type else None
    total, items = await svc.list_vendors(
        db,
        skip=skip,
        limit=limit,
        vendor_type=normalized_type,
        status_filter=status,
        keyword=keyword,
    )
    return VendorPage(
        total=total,
        items=[VendorOut.model_validate(v) for v in items],
    )


# ───────────────────── Stats (fixed path, must be before /{vendor_id}) ─────────────────────


@router.get("/stats", summary="供应商统计汇总")
async def get_vendor_stats(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
):
    """
    GET /vendors/stats — 供应商统计汇总

    用途：返回供应商模块的关键汇总指标，用于管理驾驶舱或首页统计卡片展示。

    Query 参数：无

    响应（200 OK）：
        {
            "active": int         — 当前状态为 active 的供应商总数
            "headhunters": int    — 猎头类型且状态为 active 的供应商数量
            "contract_total": float — 所有状态为 active 的合同总金额（元）
        }

    权限：任意已登录用户（get_current_user）

    注意：此路由为固定路径，必须在 /{vendor_id} 动态路由之前注册，
          否则 "stats" 会被解析为 vendor_id 数字参数导致 422 错误。

    实现：直接使用 SQLAlchemy Core 聚合查询（count/sum），不调用 svc 方法。
    """
    from sqlalchemy import select as sa_select, func
    from app.models.vendor import Vendor, VendorContract

    active_count = await db.scalar(
        sa_select(func.count()).select_from(Vendor).where(Vendor.status == "active")
    )
    headhunter_count = await db.scalar(
        sa_select(func.count()).select_from(Vendor).where(
            Vendor.status == "active",
            Vendor.vendor_type.in_(["headhunter", "猎头服务", "猎头"]),
        )
    )
    contract_total = await db.scalar(
        sa_select(func.sum(VendorContract.total_amount)).where(
            VendorContract.status == "active"
        )
    )
    return {
        "active": active_count or 0,
        "headhunters": headhunter_count or 0,
        "contract_total": float(contract_total or 0),
    }


# ───────────────────── Contracts (fixed paths, must be before /{vendor_id}) ─────────────────────


@router.post("/contracts", response_model=VendorContractOut, status_code=201)
async def create_contract(
    data: VendorContractCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> VendorContractOut:
    """
    POST /vendors/contracts — 新建供应商合同

    用途：为指定供应商创建一份合同记录。合同可关联劳务派遣协议、猎头服务协议等多种类型。

    请求体（VendorContractCreate）：
        - vendor_id: int            — 关联供应商 ID
        - contract_no: str          — 合同编号（建议唯一）
        - contract_name: str        — 合同名称/标题
        - contract_type: str        — 合同类型（如 framework/service/nda）
        - start_date: date          — 合同生效日期
        - end_date: date            — 合同到期日期
        - total_amount: Decimal     — 合同总金额（元）
        - status: str               — 合同状态（draft/active/expired/terminated）
        - signed_date: Optional[date] — 签署日期
        - attachment_url: Optional[str] — 附件下载链接
        - notes: Optional[str]      — 备注

    响应（201 Created）：VendorContractOut — 含 id 及所有字段

    权限：admin 或 hr 角色

    Service：svc.create_contract(db, data)
    """
    contract = await svc.create_contract(db, data)
    return VendorContractOut.model_validate(contract)


@router.get("/contracts", response_model=VendorContractPage)
async def list_contracts(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    vendor_id: Optional[int] = Query(None, description="供应商ID"),
    status: Optional[str] = Query(None, description="合同状态"),
    contract_type: Optional[str] = Query(None, description="合同类型"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VendorContractPage:
    """
    GET /vendors/contracts — 分页查询供应商合同列表

    用途：查询所有供应商的合同列表，可按供应商、状态、类型过滤。

    Query 参数：
        - skip: int           — 跳过条数（分页用，默认 0）
        - limit: int          — 每页条数（默认 20，范围 1~1000）
        - vendor_id: int      — 只返回指定供应商的合同（可选）
        - status: str         — 按合同状态过滤（draft/active/expired/terminated，可选）
        - contract_type: str  — 按合同类型过滤（可选）

    响应（200 OK）：VendorContractPage { total: int, items: list[VendorContractOut] }

    权限：任意已登录用户（get_current_user）

    Service：svc.list_contracts(db, skip, limit, vendor_id, status_filter, contract_type)
    """
    total, items = await svc.list_contracts(
        db,
        skip=skip,
        limit=limit,
        vendor_id=vendor_id,
        status_filter=status,
        contract_type=contract_type,
    )
    return VendorContractPage(
        total=total,
        items=[VendorContractOut.model_validate(c) for c in items],
    )


@router.get("/contracts/{contract_id}", response_model=VendorContractOut)
async def get_contract(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VendorContractOut:
    """
    GET /vendors/contracts/{contract_id} — 获取单份合同详情

    用途：根据合同 ID 查询合同的完整信息，含关联的供应商信息。

    路径参数：
        - contract_id: int — 目标合同的主键 ID

    响应（200 OK）：VendorContractOut — 合同全字段
    响应（404 Not Found）：合同不存在时由 svc.get_contract 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.get_contract(db, contract_id)
    """
    contract = await svc.get_contract(db, contract_id)
    return VendorContractOut.model_validate(contract)


@router.put("/contracts/{contract_id}", response_model=VendorContractOut)
async def update_contract(
    contract_id: int,
    data: VendorContractUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> VendorContractOut:
    """
    PUT /vendors/contracts/{contract_id} — 更新合同信息

    用途：修改已有合同的字段（如续签、变更金额、更新状态等）。所有字段均为可选更新。

    路径参数：
        - contract_id: int — 目标合同的主键 ID

    请求体（VendorContractUpdate）：与 Create 字段相同但全部为可选，仅传入需变更的字段

    响应（200 OK）：VendorContractOut — 更新后的合同数据
    响应（404 Not Found）：合同不存在时由 svc 抛出

    权限：admin 或 hr 角色

    Service：svc.update_contract(db, contract_id, data)
    """
    contract = await svc.update_contract(db, contract_id, data)
    return VendorContractOut.model_validate(contract)


@router.delete("/contracts/{contract_id}", status_code=204)
async def delete_contract(
    contract_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> None:
    """
    DELETE /vendors/contracts/{contract_id} — 删除合同

    用途：物理删除指定合同记录。操作不可逆，请谨慎使用。

    路径参数：
        - contract_id: int — 目标合同的主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：合同不存在时由 svc 抛出

    权限：admin 或 hr 角色

    Service：svc.delete_contract(db, contract_id)
    """
    await svc.delete_contract(db, contract_id)


# ───────────────────── Headhunter Commissions (fixed paths, must be before /{vendor_id}) ─────────


@router.post(
    "/commissions",
    response_model=HeadhunterCommissionOut,
    status_code=201,
)
async def create_commission(
    data: HeadhunterCommissionCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> HeadhunterCommissionOut:
    """
    POST /vendors/commissions — 新建猎头佣金记录

    用途：为猎头供应商创建一条佣金记录。佣金通常关联某位新员工的入职，
          分阶段支付（如入职即付首期、保证期结束付尾款）。

    请求体（HeadhunterCommissionCreate）：
        - vendor_id: int           — 猎头供应商 ID
        - employee_id: int         — 被推荐并入职的员工 ID
        - stage: str               — 佣金阶段（first_payment / final_payment 等）
        - amount: Decimal          — 本期佣金金额（元）
        - due_date: date           — 应付款日期
        - guarantee_start: Optional[date] — 保证期开始日期
        - guarantee_end: Optional[date]   — 保证期结束日期
        - notes: Optional[str]     — 备注

    响应（201 Created）：HeadhunterCommissionOut — 含 id 及状态字段

    权限：任意已登录用户（get_current_user）

    Service：svc.create_commission(db, data)
    """
    commission = await svc.create_commission(db, data)
    return HeadhunterCommissionOut.model_validate(commission)


@router.get("/commissions", response_model=HeadhunterCommissionPage)
async def list_commissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    vendor_id: Optional[int] = Query(None, description="猎头供应商ID"),
    stage: Optional[str] = Query(None, description="佣金阶段"),
    payment_status: Optional[str] = Query(None, description="付款状态"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> HeadhunterCommissionPage:
    """
    GET /vendors/commissions — 分页查询猎头佣金列表

    用途：查询所有猎头佣金记录，可按供应商、阶段、付款状态过滤。
          常用于 HR 对账和财务付款跟进。

    Query 参数：
        - skip: int             — 跳过条数（分页用，默认 0）
        - limit: int            — 每页条数（默认 20，范围 1~1000）
        - vendor_id: int        — 只查询指定猎头供应商的佣金（可选）
        - stage: str            — 按阶段过滤（first_payment/final_payment，可选）
        - payment_status: str   — 按付款状态过滤（pending/paid/refunded，可选）

    响应（200 OK）：HeadhunterCommissionPage { total: int, items: list[HeadhunterCommissionOut] }

    权限：任意已登录用户（get_current_user）

    Service：svc.list_commissions(db, skip, limit, vendor_id, stage, payment_status)
    """
    total, items = await svc.list_commissions(
        db,
        skip=skip,
        limit=limit,
        vendor_id=vendor_id,
        stage=stage,
        payment_status=payment_status,
    )
    return HeadhunterCommissionPage(
        total=total,
        items=[HeadhunterCommissionOut.model_validate(c) for c in items],
    )


@router.get("/commissions/{commission_id}", response_model=HeadhunterCommissionOut)
async def get_commission(
    commission_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> HeadhunterCommissionOut:
    """
    GET /vendors/commissions/{commission_id} — 获取佣金记录详情

    用途：查询单条猎头佣金记录的完整信息，含关联员工与供应商。

    路径参数：
        - commission_id: int — 佣金记录主键 ID

    响应（200 OK）：HeadhunterCommissionOut
    响应（404 Not Found）：记录不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.get_commission(db, commission_id)
    """
    commission = await svc.get_commission(db, commission_id)
    return HeadhunterCommissionOut.model_validate(commission)


@router.put("/commissions/{commission_id}", response_model=HeadhunterCommissionOut)
async def update_commission(
    commission_id: int,
    data: HeadhunterCommissionUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> HeadhunterCommissionOut:
    """
    PUT /vendors/commissions/{commission_id} — 更新佣金记录

    用途：修改佣金金额、应付日期、备注等字段。通常用于金额变更或修正错误。

    路径参数：
        - commission_id: int — 佣金记录主键 ID

    请求体（HeadhunterCommissionUpdate）：与 Create 相同字段，但全部可选

    响应（200 OK）：HeadhunterCommissionOut — 更新后的记录
    响应（404 Not Found）：记录不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.update_commission(db, commission_id, data)
    """
    commission = await svc.update_commission(db, commission_id, data)
    return HeadhunterCommissionOut.model_validate(commission)


@router.post(
    "/commissions/{commission_id}/payment",
    response_model=HeadhunterCommissionOut,
)
async def process_payment(
    commission_id: int,
    data: HeadhunterPaymentRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> HeadhunterCommissionOut:
    """
    POST /vendors/commissions/{commission_id}/payment — 处理佣金付款

    用途：记录实际付款操作，将佣金状态从 pending 变更为 paid，并记录实付金额与付款日期。

    路径参数：
        - commission_id: int — 目标佣金记录主键 ID

    请求体（HeadhunterPaymentRequest）：
        - paid_amount: Decimal  — 实际支付金额（可与应付金额不同，如有折扣）
        - paid_date: date       — 实际付款日期
        - payment_method: str   — 付款方式（bank_transfer / check 等）
        - reference_no: Optional[str] — 付款凭证号/转账流水号
        - notes: Optional[str]  — 备注

    响应（200 OK）：HeadhunterCommissionOut — 含更新后的 payment_status = "paid"
    响应（404/409）：记录不存在或状态不允许付款时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.process_payment(db, commission_id, data)
    """
    commission = await svc.process_payment(db, commission_id, data)
    return HeadhunterCommissionOut.model_validate(commission)


@router.post(
    "/commissions/{commission_id}/refund",
    response_model=HeadhunterCommissionOut,
)
async def process_refund(
    commission_id: int,
    data: HeadhunterRefundRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> HeadhunterCommissionOut:
    """
    POST /vendors/commissions/{commission_id}/refund — 处理保证期退款

    用途：当被推荐员工在保证期内离职时，向猎头供应商发起退款或扣减佣金。
          执行后佣金状态变更为 refunded，并记录退款金额与原因。

    路径参数：
        - commission_id: int — 目标佣金记录主键 ID（需已处于 paid 状态且在保证期内）

    请求体（HeadhunterRefundRequest）：
        - refund_amount: Decimal   — 退款金额（元）
        - refund_date: date        — 退款日期
        - reason: str              — 退款原因（如"员工保证期内离职"）
        - notes: Optional[str]     — 补充说明

    响应（200 OK）：HeadhunterCommissionOut — 含更新后的 payment_status = "refunded"
    响应（404/409）：记录不存在或不在保证期/已付款状态时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.process_refund(db, commission_id, data)
    """
    commission = await svc.process_refund(db, commission_id, data)
    return HeadhunterCommissionOut.model_validate(commission)


@router.delete("/commissions/{commission_id}", status_code=204)
async def delete_commission(
    commission_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    DELETE /vendors/commissions/{commission_id} — 删除佣金记录

    用途：物理删除指定佣金记录，操作不可逆。

    路径参数：
        - commission_id: int — 目标佣金记录主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：记录不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.delete_commission(db, commission_id)
    """
    await svc.delete_commission(db, commission_id)


# ───────────────────── Evaluations (fixed paths, must be before /{vendor_id}) ─────────────────────


@router.get("/evaluations", response_model=VendorEvaluationPage)
async def list_evaluations(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    vendor_id: Optional[int] = Query(None, description="供应商ID"),
    year: Optional[int] = Query(None, description="评估年份"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VendorEvaluationPage:
    """
    GET /vendors/evaluations — 查询供应商评分历史列表

    用途：获取所有供应商的历史绩效评分记录，可按供应商 ID 或评估年份过滤。

    Query 参数：
        - skip: int       — 跳过条数（分页用，默认 0）
        - limit: int      — 每页条数（默认 20，范围 1~1000）
        - vendor_id: int  — 只查询指定供应商的评分（可选）
        - year: int       — 只查询指定年份的评分（可选，如 2024）

    响应（200 OK）：VendorEvaluationPage { total: int, items: list[VendorEvaluationOut] }

    权限：任意已登录用户（get_current_user）

    Service：svc.list_evaluations(db, vendor_id, year, skip, limit)
    """
    total, items = await svc.list_evaluations(
        db, vendor_id=vendor_id, year=year, skip=skip, limit=limit
    )
    return VendorEvaluationPage(total=total, items=items)


@router.post("/evaluations", response_model=VendorEvaluationOut, status_code=201)
async def create_evaluation(
    data: VendorEvaluationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> VendorEvaluationOut:
    """
    POST /vendors/evaluations — 提交供应商绩效评分

    用途：由 HR 或管理人员对供应商进行打分，记录各维度评分详情。
          与 POST /{vendor_id}/evaluate 的区别：本接口仅保存明细评分记录，
          不修改供应商主体的综合评分字段。

    请求体（VendorEvaluationCreate）：
        - vendor_id: int           — 被评估的供应商 ID
        - year: int                — 评估年份
        - quality_score: float     — 质量维度评分（0~100）
        - delivery_score: float    — 交付维度评分（0~100）
        - service_score: float     — 服务维度评分（0~100）
        - compliance_score: float  — 合规维度评分（0~100）
        - overall_score: float     — 综合评分（通常为各维度加权平均）
        - comments: Optional[str]  — 评价说明

    响应（201 Created）：VendorEvaluationOut

    权限：admin、hr 或 manager 角色

    Service：svc.create_evaluation(db, current_user.id, data)
    """
    return await svc.create_evaluation(db, current_user.id, data)


# ───────────────────── Blacklist (fixed paths, must be before /{vendor_id}) ─────────────────────


@router.get("/blacklist", response_model=list[BlacklistOut])
async def list_blacklist(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> list[BlacklistOut]:
    """
    GET /vendors/blacklist — 查询供应商黑名单列表

    用途：列出所有当前在黑名单中的供应商，含加入原因、加入时间、操作人等信息。

    Query 参数：
        - skip: int   — 跳过条数（分页用，默认 0）
        - limit: int  — 每页条数（默认 20，范围 1~1000）

    响应（200 OK）：list[BlacklistOut]
        每项含：vendor_id、vendor_name、reason、added_by（操作人ID）、added_at、removed_at 等

    权限：任意已登录用户（get_current_user）

    Service：svc.list_blacklist(db, skip, limit)
    """
    _total, items = await svc.list_blacklist(db, skip=skip, limit=limit)
    return items


@router.post("/blacklist", response_model=BlacklistOut, status_code=201)
async def add_to_blacklist(
    data: BlacklistRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> BlacklistOut:
    """
    POST /vendors/blacklist — 将供应商加入黑名单

    用途：对违规、失信或严重违约的供应商执行拉黑操作，加入后该供应商状态变为 blacklisted。

    请求体（BlacklistRequest）：
        - vendor_id: int  — 要加入黑名单的供应商 ID
        - reason: str     — 加入黑名单的原因说明（必填，用于审计留痕）

    响应（201 Created）：BlacklistOut — 包含黑名单记录详情及操作人信息

    权限：admin 或 hr 角色

    Service：svc.add_to_blacklist(db, vendor_id, operator_id, reason)
             操作人 ID 取自当前登录用户 current_user.id
    """
    return await svc.add_to_blacklist(db, data.vendor_id, current_user.id, data.reason)


@router.delete("/blacklist/{vendor_id}", response_model=BlacklistOut)
async def remove_from_blacklist(
    vendor_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> BlacklistOut:
    """
    DELETE /vendors/blacklist/{vendor_id} — 将供应商移出黑名单

    用途：解除供应商的黑名单状态，通常在供应商完成整改或问题解决后操作。
          移除后供应商状态恢复为 active，黑名单记录保留（软删除，记录 removed_at）。

    路径参数：
        - vendor_id: int — 要解除黑名单的供应商 ID

    响应（200 OK）：BlacklistOut — 更新后的黑名单记录（含 removed_at、removed_by）
    响应（404 Not Found）：供应商不在黑名单中时由 svc 抛出

    权限：admin 或 hr 角色

    Service：svc.remove_from_blacklist(db, vendor_id, operator_id)
             操作人 ID 取自当前登录用户 current_user.id
    """
    return await svc.remove_from_blacklist(db, vendor_id, current_user.id)


# ───────────────────── Labor dispatch fixed paths (must be before /{vendor_id}) ─────────────────────


@router.post("/dispatch-projects", response_model=LaborDispatchProjectOut, status_code=201)
async def create_dispatch_project(
    data: LaborDispatchProjectCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> LaborDispatchProjectOut:
    return await svc.create_dispatch_project(db, data)


@router.get("/dispatch-projects", response_model=LaborDispatchProjectPage)
async def list_dispatch_projects(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    vendor_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> LaborDispatchProjectPage:
    total, items = await svc.list_dispatch_projects(
        db, skip=skip, limit=limit, vendor_id=vendor_id, status_filter=status, keyword=keyword
    )
    return LaborDispatchProjectPage(total=total, items=items)


@router.get("/dispatch-projects/{project_id}", response_model=LaborDispatchProjectOut)
async def get_dispatch_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> LaborDispatchProjectOut:
    return await svc.get_dispatch_project(db, project_id)


@router.put("/dispatch-projects/{project_id}", response_model=LaborDispatchProjectOut)
async def update_dispatch_project(
    project_id: int,
    data: LaborDispatchProjectUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> LaborDispatchProjectOut:
    return await svc.update_dispatch_project(db, project_id, data)


@router.delete("/dispatch-projects/{project_id}", status_code=204)
async def delete_dispatch_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> None:
    await svc.delete_dispatch_project(db, project_id)


@router.post("/dispatch-workers", response_model=LaborDispatchWorkerOut, status_code=201)
async def create_dispatch_worker(
    data: LaborDispatchWorkerCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> LaborDispatchWorkerOut:
    return await svc.create_dispatch_worker(db, data)


@router.get("/dispatch-workers", response_model=LaborDispatchWorkerPage)
async def list_dispatch_workers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    vendor_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> LaborDispatchWorkerPage:
    total, items = await svc.list_dispatch_workers(
        db,
        skip=skip,
        limit=limit,
        vendor_id=vendor_id,
        project_id=project_id,
        status_filter=status,
        keyword=keyword,
    )
    return LaborDispatchWorkerPage(total=total, items=items)


@router.get("/dispatch-workers/{worker_id}", response_model=LaborDispatchWorkerOut)
async def get_dispatch_worker(
    worker_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> LaborDispatchWorkerOut:
    return await svc.get_dispatch_worker(db, worker_id)


@router.put("/dispatch-workers/{worker_id}", response_model=LaborDispatchWorkerOut)
async def update_dispatch_worker(
    worker_id: int,
    data: LaborDispatchWorkerUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> LaborDispatchWorkerOut:
    return await svc.update_dispatch_worker(db, worker_id, data)


@router.delete("/dispatch-workers/{worker_id}", status_code=204)
async def delete_dispatch_worker(
    worker_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> None:
    await svc.delete_dispatch_worker(db, worker_id)


@router.post("/labor-settlements", response_model=LaborSettlementOut, status_code=201)
async def create_labor_settlement(
    data: LaborSettlementCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> LaborSettlementOut:
    return await svc.create_labor_settlement(db, data)


@router.get("/labor-settlements", response_model=LaborSettlementPage)
async def list_labor_settlements(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    vendor_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    settlement_month: Optional[str] = Query(None),
    method: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> LaborSettlementPage:
    total, items = await svc.list_labor_settlements(
        db,
        skip=skip,
        limit=limit,
        vendor_id=vendor_id,
        project_id=project_id,
        settlement_month=settlement_month,
        method=method,
        status_filter=status,
    )
    return LaborSettlementPage(total=total, items=items)


@router.get("/labor-settlements/{settlement_id}", response_model=LaborSettlementOut)
async def get_labor_settlement(
    settlement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> LaborSettlementOut:
    return await svc.get_labor_settlement(db, settlement_id)


@router.put("/labor-settlements/{settlement_id}", response_model=LaborSettlementOut)
async def update_labor_settlement(
    settlement_id: int,
    data: LaborSettlementUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> LaborSettlementOut:
    return await svc.update_labor_settlement(db, settlement_id, data)


@router.delete("/labor-settlements/{settlement_id}", status_code=204)
async def delete_labor_settlement(
    settlement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> None:
    await svc.delete_labor_settlement(db, settlement_id)


# ───────────────────── Vendor by ID (dynamic paths, must be AFTER fixed paths) ─────────────────────


@router.get("/{vendor_id}", response_model=VendorOut)
async def get_vendor(
    vendor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VendorOut:
    """
    GET /vendors/{vendor_id} — 获取供应商详情

    用途：根据主键 ID 查询单个供应商的完整信息。

    路径参数：
        - vendor_id: int — 供应商主键 ID

    响应（200 OK）：VendorOut — 供应商全字段（含统计/状态字段）
    响应（404 Not Found）：供应商不存在时由 svc.get_vendor 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.get_vendor(db, vendor_id)

    注意：此为动态路径路由，必须在所有以 /vendors/<固定段> 开头的路由之后注册。
    """
    vendor = await svc.get_vendor(db, vendor_id)
    return VendorOut.model_validate(vendor)


@router.put("/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: int,
    data: VendorUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> VendorOut:
    """
    PUT /vendors/{vendor_id} — 更新供应商信息

    用途：修改供应商主体的基本资料，如联系方式、状态、备注等。

    路径参数：
        - vendor_id: int — 目标供应商主键 ID

    请求体（VendorUpdate）：与 VendorCreate 字段相同，全部可选，仅传入需变更的字段

    响应（200 OK）：VendorOut — 更新后的供应商数据
    响应（404 Not Found）：供应商不存在时由 svc 抛出

    权限：admin 或 hr 角色

    Service：svc.update_vendor(db, vendor_id, data)
    """
    vendor = await svc.update_vendor(db, vendor_id, data)
    return VendorOut.model_validate(vendor)


@router.delete("/{vendor_id}", status_code=204)
async def delete_vendor(
    vendor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> None:
    """
    DELETE /vendors/{vendor_id} — 删除供应商

    用途：物理删除供应商主体记录。若有关联合同/佣金数据，可能触发外键约束，
          建议先检查关联数据或使用逻辑删除（将 status 改为 inactive）。

    路径参数：
        - vendor_id: int — 目标供应商主键 ID

    响应（204 No Content）：删除成功，无响应体
    响应（404 Not Found）：供应商不存在时由 svc 抛出

    权限：admin 或 hr 角色

    Service：svc.delete_vendor(db, vendor_id)
    """
    await svc.delete_vendor(db, vendor_id)


@router.post("/{vendor_id}/evaluate", response_model=VendorOut)
async def evaluate_vendor(
    vendor_id: int,
    data: VendorEvaluationRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> VendorOut:
    """
    POST /vendors/{vendor_id}/evaluate — 执行年度供应商评估

    用途：对指定供应商进行年度综合评估，执行后会更新供应商主体记录中的
          综合评分（overall_score）字段，并新增评估明细记录。
          与 POST /vendors/evaluations 的区别：本接口会回写供应商主表评分字段。

    路径参数：
        - vendor_id: int — 目标供应商主键 ID

    请求体（VendorEvaluationRequest）：
        - year: int                — 评估年份（通常为当前年份）
        - quality_score: float     — 质量评分（0~100）
        - delivery_score: float    — 交付评分（0~100）
        - service_score: float     — 服务评分（0~100）
        - compliance_score: float  — 合规评分（0~100）
        - comments: Optional[str]  — 评估意见

    响应（200 OK）：VendorOut — 含更新后综合评分的供应商数据
    响应（404 Not Found）：供应商不存在时由 svc 抛出

    权限：admin、hr 或 manager 角色

    Service：svc.evaluate_vendor(db, vendor_id, data)
    """
    vendor = await svc.evaluate_vendor(db, vendor_id, data)
    return VendorOut.model_validate(vendor)


@router.get("/{vendor_id}/score-summary", response_model=VendorScoreSummary)
async def get_vendor_score_summary(
    vendor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VendorScoreSummary:
    """
    GET /vendors/{vendor_id}/score-summary — 获取供应商评分汇总

    用途：查询指定供应商历史所有评估记录中各维度的平均分，
          用于趋势分析和供应商评级管理。

    路径参数：
        - vendor_id: int — 目标供应商主键 ID

    响应（200 OK）：VendorScoreSummary
        {
            "vendor_id": int,
            "vendor_name": str,
            "avg_quality_score": float     — 历史质量维度平均分
            "avg_delivery_score": float    — 历史交付维度平均分
            "avg_service_score": float     — 历史服务维度平均分
            "avg_compliance_score": float  — 历史合规维度平均分
            "avg_overall_score": float     — 历史综合评分平均
            "evaluation_count": int        — 历史评估总次数
        }
    响应（404 Not Found）：供应商不存在时由 svc 抛出

    权限：任意已登录用户（get_current_user）

    Service：svc.get_vendor_avg_score(db, vendor_id)
    """
    return await svc.get_vendor_avg_score(db, vendor_id)
