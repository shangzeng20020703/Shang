"""
供应商与外包管理服务 - Vendor Management Service
=================================================

本模块负责售后管理系统中所有与外部供应商相关的业务逻辑，涵盖：

1. **供应商主档（Vendor）**
   - 创建、查询、更新、删除供应商信息
   - 供应商类型：labor（劳务派遣）/ headhunter（猎头）/ outsourcing（外包）
   - 年度评估评分管理

2. **供应商合同（VendorContract）**
   - 合同的全生命周期 CRUD
   - 按供应商 ID 过滤、按合同类型过滤、按状态过滤

3. **猎头佣金（HeadhunterCommission）**
   - 仅 vendor_type == "headhunter" 的供应商才能创建佣金记录
   - 两期付款流程：入职付首款（offer_amount × 50%）→ 转正付尾款（final_amount × 50%）
   - 保证期退款：refund_status 流转（requested → completed）

4. **供应商绩效评分（VendorEvaluation）**
   - 四维度评分：质量（quality）/ 交付（delivery）/ 价格（price）/ 合作（cooperation）
   - overall_score = 四维度算术平均值，自动计算
   - 支持汇总查询某供应商的历史平均分

5. **黑名单（VendorBlacklist）**
   - 软删除实现：is_active=True 表示在黑名单中，is_active=False 表示已移除
   - 同一供应商只保留一条黑名单记录（复用已有行，不新建）
   - 防止重复加入、防止对不在黑名单的供应商执行移除

调用关系：
    api/v1/vendor.py → services/vendor.py（本文件）→ models/vendor.py + models/employee.py

依赖框架：FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.vendor import (
    HeadhunterCommission,
    LaborDispatchProject,
    LaborDispatchWorker,
    LaborSettlement,
    Vendor,
    VendorBlacklist,
    VendorContract,
    VendorEvaluation,
)
from app.schemas.vendor import (
    BlacklistOut,
    BlacklistRequest,
    HeadhunterCommissionCreate,
    HeadhunterCommissionUpdate,
    HeadhunterPaymentRequest,
    HeadhunterRefundRequest,
    LaborDispatchProjectCreate,
    LaborDispatchProjectOut,
    LaborDispatchProjectUpdate,
    LaborDispatchWorkerCreate,
    LaborDispatchWorkerOut,
    LaborDispatchWorkerUpdate,
    LaborSettlementCreate,
    LaborSettlementOut,
    LaborSettlementUpdate,
    VendorContractCreate,
    VendorContractUpdate,
    VendorCreate,
    VendorEvaluationCreate,
    VendorEvaluationOut,
    VendorEvaluationRequest,
    VendorScoreSummary,
    VendorUpdate,
)


# ───────────────────── Vendor CRUD ─────────────────────


async def create_vendor(db: AsyncSession, data: VendorCreate) -> Vendor:
    """
    新建供应商主档记录。

    业务说明：
        供应商类型（vendor_type）决定后续业务流程：
        - "labor"：劳务派遣，签劳务合同，派遣员工
        - "headhunter"：猎头，可创建 HeadhunterCommission 佣金记录
        - "outsourcing"：外包，整体项目外包

    参数：
        db   -- 异步数据库会话
        data -- VendorCreate schema，包含公司名称、联系人、类型等字段

    返回：
        新建的 Vendor ORM 对象（已 flush，包含自增 id）
    """
    vendor = Vendor(**data.model_dump())
    db.add(vendor)
    await db.flush()          # 写入数据库但不提交，由调用方统一提交
    await db.refresh(vendor)  # 刷新以获取数据库生成的字段（如 created_at）
    return vendor


async def get_vendor(db: AsyncSession, vendor_id: int) -> Vendor:
    """
    按 ID 查询供应商，并预加载关联的合同（contracts）和佣金（commissions）列表。

    参数：
        db        -- 异步数据库会话
        vendor_id -- 供应商主键 ID

    返回：
        Vendor ORM 对象（含 contracts、commissions 关联集合）

    异常：
        HTTPException 404 -- 供应商不存在
    """
    result = await db.execute(
        select(Vendor)
        .options(
            selectinload(Vendor.contracts),    # 预加载合同列表，避免 N+1 查询
            selectinload(Vendor.commissions),  # 预加载佣金列表
        )
        .where(Vendor.id == vendor_id)
    )
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return vendor


async def list_vendors(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    vendor_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    keyword: Optional[str] = None,
) -> tuple[int, list[Vendor]]:
    """
    分页查询供应商列表，支持多条件过滤。

    参数：
        db            -- 异步数据库会话
        skip          -- 分页偏移量（从第几条开始）
        limit         -- 每页最大记录数
        vendor_type   -- 按供应商类型过滤（labor/headhunter/outsourcing），None 表示不过滤
        status_filter -- 按状态过滤（active/inactive 等），None 表示不过滤
        keyword       -- 按公司名称模糊搜索（ILIKE，大小写不敏感）

    返回：
        (total, vendors) 元组：
        - total   -- 满足条件的总记录数（用于前端分页）
        - vendors -- 当前页的 Vendor 列表，按 ID 倒序排列（最新创建的在前）
    """
    stmt = select(Vendor)
    if vendor_type:
        stmt = stmt.where(Vendor.vendor_type == vendor_type)
    if status_filter:
        stmt = stmt.where(Vendor.status == status_filter)
    if keyword:
        stmt = stmt.where(Vendor.company_name.ilike(f"%{keyword}%"))

    # 先查总数（使用子查询确保过滤条件与列表查询保持一致）
    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(Vendor.id.desc()).offset(skip).limit(limit)
    )
    return total, list(result.scalars().all())


async def update_vendor(
    db: AsyncSession, vendor_id: int, data: VendorUpdate
) -> Vendor:
    """
    更新供应商信息（仅更新请求中提供的字段）。

    参数：
        db        -- 异步数据库会话
        vendor_id -- 供应商主键 ID
        data      -- VendorUpdate schema（Pydantic v2，未设置的字段不会出现在 model_dump 中）

    返回：
        更新后的 Vendor ORM 对象

    异常：
        HTTPException 404 -- 供应商不存在（由内部 get_vendor 抛出）
    """
    vendor = await get_vendor(db, vendor_id)
    # exclude_unset=True：只更新前端实际传入的字段，避免覆盖未修改字段为 None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(vendor, field, value)
    vendor.updated_at = datetime.now(timezone.utc)  # 手动更新修改时间
    await db.flush()
    await db.refresh(vendor)
    return vendor


async def delete_vendor(db: AsyncSession, vendor_id: int) -> None:
    """
    删除供应商主档（物理删除，会级联删除关联合同和佣金记录）。

    警告：删除前请确认该供应商没有进行中的合同或未结算佣金，
          否则应将其状态设为 inactive 而非直接删除。

    参数：
        db        -- 异步数据库会话
        vendor_id -- 供应商主键 ID

    异常：
        HTTPException 404 -- 供应商不存在
    """
    vendor = await get_vendor(db, vendor_id)
    await db.delete(vendor)
    await db.flush()


async def evaluate_vendor(
    db: AsyncSession, vendor_id: int, data: VendorEvaluationRequest
) -> Vendor:
    """
    年度供应商评估：更新供应商的年度评估总分（annual_eval_score）。

    注意：此方法仅更新供应商主档上的汇总分，详细评分记录由
    create_evaluation() 写入 VendorEvaluation 子表。

    参数：
        db        -- 异步数据库会话
        vendor_id -- 供应商主键 ID
        data      -- VendorEvaluationRequest，包含 score 字段

    返回：
        更新后的 Vendor ORM 对象
    """
    vendor = await get_vendor(db, vendor_id)
    vendor.annual_eval_score = data.score
    vendor.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(vendor)
    return vendor


def _vendor_is_labor_dispatch(vendor: Vendor) -> bool:
    return vendor.vendor_type in ("劳务派遣", "labor_dispatch", "service")


async def _ensure_labor_vendor_active(db: AsyncSession, vendor_id: int) -> Vendor:
    vendor = await get_vendor(db, vendor_id)
    if not _vendor_is_labor_dispatch(vendor):
        raise HTTPException(status_code=400, detail="该供应商不是劳务派遣公司")
    if vendor.status != "active":
        raise HTTPException(status_code=400, detail="当前劳务公司不是合作中状态，不能继续新增业务")
    if await is_vendor_blacklisted(db, vendor_id):
        raise HTTPException(status_code=400, detail="黑名单劳务公司不允许继续新增业务")
    return vendor


async def _ensure_labor_vendor_exists(db: AsyncSession, vendor_id: int) -> Vendor:
    vendor = await get_vendor(db, vendor_id)
    if not _vendor_is_labor_dispatch(vendor):
        raise HTTPException(status_code=400, detail="该供应商不是劳务派遣公司")
    return vendor


async def _refresh_vendor_dispatch_count(db: AsyncSession, vendor_id: int) -> None:
    vendor = await get_vendor(db, vendor_id)
    count_result = await db.execute(
        select(func.count(LaborDispatchWorker.id)).where(
            LaborDispatchWorker.vendor_id == vendor_id,
            LaborDispatchWorker.status == "在岗",
        )
    )
    vendor.dispatch_count = int(count_result.scalar() or 0)
    vendor.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def _get_dispatch_project_orm(db: AsyncSession, project_id: int) -> LaborDispatchProject:
    result = await db.execute(
        select(LaborDispatchProject)
        .options(
            selectinload(LaborDispatchProject.vendor),
            selectinload(LaborDispatchProject.workers),
        )
        .where(LaborDispatchProject.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="派遣项目不存在")
    return project


async def _get_dispatch_worker_orm(db: AsyncSession, worker_id: int) -> LaborDispatchWorker:
    result = await db.execute(
        select(LaborDispatchWorker)
        .options(
            selectinload(LaborDispatchWorker.vendor),
            selectinload(LaborDispatchWorker.project),
        )
        .where(LaborDispatchWorker.id == worker_id)
    )
    worker = result.scalar_one_or_none()
    if not worker:
        raise HTTPException(status_code=404, detail="派遣人员不存在")
    return worker


async def _get_labor_settlement_orm(db: AsyncSession, settlement_id: int) -> LaborSettlement:
    result = await db.execute(
        select(LaborSettlement)
        .options(
            selectinload(LaborSettlement.vendor),
            selectinload(LaborSettlement.project),
        )
        .where(LaborSettlement.id == settlement_id)
    )
    settlement = result.scalar_one_or_none()
    if not settlement:
        raise HTTPException(status_code=404, detail="劳务结算单不存在")
    return settlement


def _project_out(project: LaborDispatchProject) -> LaborDispatchProjectOut:
    return LaborDispatchProjectOut(
        id=project.id,
        vendor_id=project.vendor_id,
        project_name=project.project_name,
        client_company=project.client_company,
        department=project.department,
        owner=project.owner,
        start_date=project.start_date,
        end_date=project.end_date,
        status=project.status,
        vendor_name=project.vendor.company_name if project.vendor else "",
        worker_count=len([item for item in (project.workers or []) if item.status == "在岗"]),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _worker_out(worker: LaborDispatchWorker) -> LaborDispatchWorkerOut:
    return LaborDispatchWorkerOut(
        id=worker.id,
        vendor_id=worker.vendor_id,
        project_id=worker.project_id,
        name=worker.name,
        id_card=worker.id_card,
        phone=worker.phone,
        position=worker.position,
        entry_date=worker.entry_date,
        exit_date=worker.exit_date,
        status=worker.status,
        vendor_name=worker.vendor.company_name if worker.vendor else "",
        project_name=worker.project.project_name if worker.project else "",
        created_at=worker.created_at,
        updated_at=worker.updated_at,
    )


def _settlement_out(settlement: LaborSettlement) -> LaborSettlementOut:
    return LaborSettlementOut(
        id=settlement.id,
        vendor_id=settlement.vendor_id,
        project_id=settlement.project_id,
        settlement_month=settlement.settlement_month,
        method=settlement.method,
        dispatch_count=settlement.dispatch_count,
        work_hours=settlement.work_hours,
        service_fee=settlement.service_fee,
        payable_amount=settlement.payable_amount,
        status=settlement.status,
        vendor_name=settlement.vendor.company_name if settlement.vendor else "",
        project_name=settlement.project.project_name if settlement.project else "",
        created_at=settlement.created_at,
        updated_at=settlement.updated_at,
    )


async def create_dispatch_project(
    db: AsyncSession,
    data: LaborDispatchProjectCreate,
) -> LaborDispatchProjectOut:
    await _ensure_labor_vendor_active(db, data.vendor_id)
    project = LaborDispatchProject(**data.model_dump())
    db.add(project)
    await db.flush()
    project = await _get_dispatch_project_orm(db, project.id)
    return _project_out(project)


async def get_dispatch_project(db: AsyncSession, project_id: int) -> LaborDispatchProjectOut:
    project = await _get_dispatch_project_orm(db, project_id)
    return _project_out(project)


async def list_dispatch_projects(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    vendor_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    keyword: Optional[str] = None,
) -> tuple[int, list[LaborDispatchProjectOut]]:
    stmt = select(LaborDispatchProject).options(
        selectinload(LaborDispatchProject.vendor),
        selectinload(LaborDispatchProject.workers),
    )
    if vendor_id:
        stmt = stmt.where(LaborDispatchProject.vendor_id == vendor_id)
    if status_filter:
        stmt = stmt.where(LaborDispatchProject.status == status_filter)
    if keyword:
        stmt = stmt.where(
            LaborDispatchProject.project_name.ilike(f"%{keyword}%")
            | LaborDispatchProject.client_company.ilike(f"%{keyword}%")
        )
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(stmt.order_by(LaborDispatchProject.id.desc()).offset(skip).limit(limit))
    items = list(result.scalars().all())
    return total, [_project_out(item) for item in items]


async def update_dispatch_project(
    db: AsyncSession,
    project_id: int,
    data: LaborDispatchProjectUpdate,
) -> LaborDispatchProjectOut:
    project = await _get_dispatch_project_orm(db, project_id)
    payload = data.model_dump(exclude_unset=True)
    if "vendor_id" in payload:
        await _ensure_labor_vendor_active(db, payload["vendor_id"])
    for field, value in payload.items():
        setattr(project, field, value)
    project.updated_at = datetime.now(timezone.utc)
    await db.flush()
    project = await _get_dispatch_project_orm(db, project_id)
    return _project_out(project)


async def delete_dispatch_project(db: AsyncSession, project_id: int) -> None:
    project = await _get_dispatch_project_orm(db, project_id)
    for worker in project.workers or []:
        worker.project_id = None
        worker.updated_at = datetime.now(timezone.utc)
    await db.delete(project)
    await db.flush()


async def create_dispatch_worker(
    db: AsyncSession,
    data: LaborDispatchWorkerCreate,
) -> LaborDispatchWorkerOut:
    await _ensure_labor_vendor_active(db, data.vendor_id)
    if data.project_id:
        project = await _get_dispatch_project_orm(db, data.project_id)
        if project.vendor_id != data.vendor_id:
            raise HTTPException(status_code=400, detail="派遣项目与劳务公司不匹配")
    worker = LaborDispatchWorker(**data.model_dump())
    db.add(worker)
    await db.flush()
    await _refresh_vendor_dispatch_count(db, data.vendor_id)
    worker = await _get_dispatch_worker_orm(db, worker.id)
    return _worker_out(worker)


async def get_dispatch_worker(db: AsyncSession, worker_id: int) -> LaborDispatchWorkerOut:
    worker = await _get_dispatch_worker_orm(db, worker_id)
    return _worker_out(worker)


async def list_dispatch_workers(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    vendor_id: Optional[int] = None,
    project_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    keyword: Optional[str] = None,
) -> tuple[int, list[LaborDispatchWorkerOut]]:
    stmt = select(LaborDispatchWorker).options(
        selectinload(LaborDispatchWorker.vendor),
        selectinload(LaborDispatchWorker.project),
    )
    if vendor_id:
        stmt = stmt.where(LaborDispatchWorker.vendor_id == vendor_id)
    if project_id:
        stmt = stmt.where(LaborDispatchWorker.project_id == project_id)
    if status_filter:
        stmt = stmt.where(LaborDispatchWorker.status == status_filter)
    if keyword:
        stmt = stmt.where(
            LaborDispatchWorker.name.ilike(f"%{keyword}%")
            | LaborDispatchWorker.phone.ilike(f"%{keyword}%")
            | LaborDispatchWorker.id_card.ilike(f"%{keyword}%")
        )
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(stmt.order_by(LaborDispatchWorker.id.desc()).offset(skip).limit(limit))
    items = list(result.scalars().all())
    return total, [_worker_out(item) for item in items]


async def update_dispatch_worker(
    db: AsyncSession,
    worker_id: int,
    data: LaborDispatchWorkerUpdate,
) -> LaborDispatchWorkerOut:
    worker = await _get_dispatch_worker_orm(db, worker_id)
    old_vendor_id = worker.vendor_id
    payload = data.model_dump(exclude_unset=True)
    target_vendor_id = payload.get("vendor_id", worker.vendor_id)
    if target_vendor_id:
        await _ensure_labor_vendor_active(db, target_vendor_id)
    target_project_id = payload.get("project_id", worker.project_id)
    if target_project_id:
        project = await _get_dispatch_project_orm(db, target_project_id)
        if project.vendor_id != target_vendor_id:
            raise HTTPException(status_code=400, detail="派遣项目与劳务公司不匹配")
    for field, value in payload.items():
        setattr(worker, field, value)
    worker.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await _refresh_vendor_dispatch_count(db, old_vendor_id)
    if target_vendor_id != old_vendor_id:
        await _refresh_vendor_dispatch_count(db, target_vendor_id)
    worker = await _get_dispatch_worker_orm(db, worker_id)
    return _worker_out(worker)


async def delete_dispatch_worker(db: AsyncSession, worker_id: int) -> None:
    worker = await _get_dispatch_worker_orm(db, worker_id)
    vendor_id = worker.vendor_id
    await db.delete(worker)
    await db.flush()
    await _refresh_vendor_dispatch_count(db, vendor_id)


async def create_labor_settlement(
    db: AsyncSession,
    data: LaborSettlementCreate,
) -> LaborSettlementOut:
    await _ensure_labor_vendor_exists(db, data.vendor_id)
    if data.project_id:
        project = await _get_dispatch_project_orm(db, data.project_id)
        if project.vendor_id != data.vendor_id:
            raise HTTPException(status_code=400, detail="结算项目与劳务公司不匹配")
    settlement = LaborSettlement(**data.model_dump())
    db.add(settlement)
    await db.flush()
    settlement = await _get_labor_settlement_orm(db, settlement.id)
    return _settlement_out(settlement)


async def get_labor_settlement(db: AsyncSession, settlement_id: int) -> LaborSettlementOut:
    settlement = await _get_labor_settlement_orm(db, settlement_id)
    return _settlement_out(settlement)


async def list_labor_settlements(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    vendor_id: Optional[int] = None,
    project_id: Optional[int] = None,
    settlement_month: Optional[str] = None,
    method: Optional[str] = None,
    status_filter: Optional[str] = None,
) -> tuple[int, list[LaborSettlementOut]]:
    stmt = select(LaborSettlement).options(
        selectinload(LaborSettlement.vendor),
        selectinload(LaborSettlement.project),
    )
    if vendor_id:
        stmt = stmt.where(LaborSettlement.vendor_id == vendor_id)
    if project_id:
        stmt = stmt.where(LaborSettlement.project_id == project_id)
    if settlement_month:
        stmt = stmt.where(LaborSettlement.settlement_month == settlement_month)
    if method:
        stmt = stmt.where(LaborSettlement.method == method)
    if status_filter:
        stmt = stmt.where(LaborSettlement.status == status_filter)
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(stmt.order_by(LaborSettlement.id.desc()).offset(skip).limit(limit))
    items = list(result.scalars().all())
    return total, [_settlement_out(item) for item in items]


async def update_labor_settlement(
    db: AsyncSession,
    settlement_id: int,
    data: LaborSettlementUpdate,
) -> LaborSettlementOut:
    settlement = await _get_labor_settlement_orm(db, settlement_id)
    payload = data.model_dump(exclude_unset=True)
    target_vendor_id = payload.get("vendor_id", settlement.vendor_id)
    if target_vendor_id:
        await _ensure_labor_vendor_exists(db, target_vendor_id)
    target_project_id = payload.get("project_id", settlement.project_id)
    if target_project_id:
        project = await _get_dispatch_project_orm(db, target_project_id)
        if project.vendor_id != target_vendor_id:
            raise HTTPException(status_code=400, detail="结算项目与劳务公司不匹配")
    for field, value in payload.items():
        setattr(settlement, field, value)
    settlement.updated_at = datetime.now(timezone.utc)
    await db.flush()
    settlement = await _get_labor_settlement_orm(db, settlement_id)
    return _settlement_out(settlement)


async def delete_labor_settlement(db: AsyncSession, settlement_id: int) -> None:
    settlement = await _get_labor_settlement_orm(db, settlement_id)
    await db.delete(settlement)
    await db.flush()


# ───────────────────── VendorContract CRUD ─────────────────────


async def create_contract(
    db: AsyncSession, data: VendorContractCreate
) -> VendorContract:
    """
    新建供应商合同记录。

    业务规则：
        - 创建前验证 vendor_id 对应的供应商存在
        - 合同类型（contract_type）通常与供应商类型对应，但不强制限制

    参数：
        db   -- 异步数据库会话
        data -- VendorContractCreate schema，必须包含 vendor_id

    返回：
        新建的 VendorContract ORM 对象

    异常：
        HTTPException 404 -- vendor_id 对应的供应商不存在
    """
    # 验证关联供应商存在，如不存在则抛 404
    await get_vendor(db, data.vendor_id)
    contract = VendorContract(**data.model_dump())
    db.add(contract)
    await db.flush()
    await db.refresh(contract)
    return contract


async def get_contract(db: AsyncSession, contract_id: int) -> VendorContract:
    """
    按 ID 查询供应商合同。

    参数：
        db          -- 异步数据库会话
        contract_id -- 合同主键 ID

    返回：
        VendorContract ORM 对象

    异常：
        HTTPException 404 -- 合同不存在
    """
    result = await db.execute(
        select(VendorContract).where(VendorContract.id == contract_id)
    )
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="合同不存在")
    return contract


async def list_contracts(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    vendor_id: Optional[int] = None,
    status_filter: Optional[str] = None,
    contract_type: Optional[str] = None,
) -> tuple[int, list[VendorContract]]:
    """
    分页查询供应商合同列表，支持多条件过滤。

    参数：
        db            -- 异步数据库会话
        skip          -- 分页偏移量
        limit         -- 每页记录数
        vendor_id     -- 按供应商过滤，None 表示查所有供应商的合同
        status_filter -- 按合同状态过滤（active/expired/terminated 等）
        contract_type -- 按合同类型过滤

    返回：
        (total, contracts) 元组，合同列表按 ID 倒序排列
    """
    stmt = select(VendorContract)
    if vendor_id:
        stmt = stmt.where(VendorContract.vendor_id == vendor_id)
    if status_filter:
        stmt = stmt.where(VendorContract.status == status_filter)
    if contract_type:
        stmt = stmt.where(VendorContract.contract_type == contract_type)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(VendorContract.id.desc()).offset(skip).limit(limit)
    )
    return total, list(result.scalars().all())


async def update_contract(
    db: AsyncSession, contract_id: int, data: VendorContractUpdate
) -> VendorContract:
    """
    更新供应商合同（仅更新请求中提供的字段）。

    参数：
        db          -- 异步数据库会话
        contract_id -- 合同主键 ID
        data        -- VendorContractUpdate schema

    返回：
        更新后的 VendorContract ORM 对象

    异常：
        HTTPException 404 -- 合同不存在
    """
    contract = await get_contract(db, contract_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(contract, field, value)
    await db.flush()
    await db.refresh(contract)
    return contract


async def delete_contract(db: AsyncSession, contract_id: int) -> None:
    """
    删除供应商合同（物理删除）。

    参数：
        db          -- 异步数据库会话
        contract_id -- 合同主键 ID

    异常：
        HTTPException 404 -- 合同不存在
    """
    contract = await get_contract(db, contract_id)
    await db.delete(contract)
    await db.flush()


# ───────────────────── HeadhunterCommission ─────────────────────


async def create_commission(
    db: AsyncSession, data: HeadhunterCommissionCreate
) -> HeadhunterCommission:
    """
    创建猎头佣金记录。

    业务规则：
        - 只有 vendor_type == "headhunter" 的供应商才能创建佣金记录
        - 佣金通常与一份合同挂钩（contract_id 必须存在）
        - 佣金分两期：
            第一期（入职时）= offer_amount × 50%，状态由 first_payment_status 追踪
            第二期（转正时）= final_amount × 50%，状态由 second_payment_status 追踪
        - 实际付款操作通过 process_payment() 完成

    参数：
        db   -- 异步数据库会话
        data -- HeadhunterCommissionCreate schema，包含 vendor_id、contract_id、
                employee_id、offer_amount、final_amount、guarantee_period_days 等

    返回：
        新建的 HeadhunterCommission ORM 对象

    异常：
        HTTPException 400 -- 供应商不是猎头类型
        HTTPException 404 -- 供应商或合同不存在
    """
    # 验证关联供应商存在且类型为猎头
    vendor = await get_vendor(db, data.vendor_id)
    if vendor.vendor_type != "headhunter":
        raise HTTPException(
            status_code=400, detail="该供应商不是猎头类型"
        )
    # 验证关联合同存在
    await get_contract(db, data.contract_id)

    commission = HeadhunterCommission(**data.model_dump())
    db.add(commission)
    await db.flush()
    await db.refresh(commission)
    return commission


async def get_commission(
    db: AsyncSession, commission_id: int
) -> HeadhunterCommission:
    """
    按 ID 查询猎头佣金记录。

    参数：
        db            -- 异步数据库会话
        commission_id -- 佣金记录主键 ID

    返回：
        HeadhunterCommission ORM 对象

    异常：
        HTTPException 404 -- 佣金记录不存在
    """
    result = await db.execute(
        select(HeadhunterCommission).where(
            HeadhunterCommission.id == commission_id
        )
    )
    commission = result.scalar_one_or_none()
    if not commission:
        raise HTTPException(status_code=404, detail="佣金记录不存在")
    return commission


async def list_commissions(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    vendor_id: Optional[int] = None,
    stage: Optional[str] = None,
    payment_status: Optional[str] = None,
) -> tuple[int, list[HeadhunterCommission]]:
    """
    分页查询猎头佣金列表，支持多条件过滤。

    参数：
        db             -- 异步数据库会话
        skip           -- 分页偏移量
        limit          -- 每页记录数
        vendor_id      -- 按猎头供应商过滤
        stage          -- 按候选人阶段过滤（如 offered/onboarded/confirmed 等）
        payment_status -- 按首次付款状态过滤（pending/paid 等）

    返回：
        (total, commissions) 元组，按 ID 倒序排列

    注意：
        payment_status 过滤的是 first_payment_status 字段（首款状态）
    """
    stmt = select(HeadhunterCommission)
    if vendor_id:
        stmt = stmt.where(HeadhunterCommission.vendor_id == vendor_id)
    if stage:
        stmt = stmt.where(HeadhunterCommission.stage == stage)
    if payment_status:
        stmt = stmt.where(
            HeadhunterCommission.first_payment_status == payment_status
        )

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(HeadhunterCommission.id.desc()).offset(skip).limit(limit)
    )
    return total, list(result.scalars().all())


async def update_commission(
    db: AsyncSession, commission_id: int, data: HeadhunterCommissionUpdate
) -> HeadhunterCommission:
    """
    更新猎头佣金基本信息（仅更新请求中提供的字段）。

    注意：付款操作请使用 process_payment()，退款操作请使用 process_refund()，
          不要通过本方法直接修改付款状态字段。

    参数：
        db            -- 异步数据库会话
        commission_id -- 佣金记录主键 ID
        data          -- HeadhunterCommissionUpdate schema

    返回：
        更新后的 HeadhunterCommission ORM 对象

    异常：
        HTTPException 404 -- 佣金记录不存在
    """
    commission = await get_commission(db, commission_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(commission, field, value)
    await db.flush()
    await db.refresh(commission)
    return commission


async def process_payment(
    db: AsyncSession, commission_id: int, data: HeadhunterPaymentRequest
) -> HeadhunterCommission:
    """
    处理猎头佣金付款（两期付款流程控制器）。

    业务规则（强制顺序）：
        第一期付款（payment_order=1）：
            - 前置条件：first_payment_status != "paid"（未重复付款）
            - 操作：设置 first_payment_status="paid"，记录 first_payment_date
            - 可选：覆盖 first_payment_amount（实际付款金额可与预计金额不同）

        第二期付款（payment_order=2）：
            - 前置条件：first_payment_status == "paid"（第一期已完成）
            - 前置条件：second_payment_status != "paid"（未重复付款）
            - 操作：设置 second_payment_status="paid"，记录 second_payment_date
            - 可选：覆盖 second_payment_amount

    参数：
        db            -- 异步数据库会话
        commission_id -- 佣金记录主键 ID
        data          -- HeadhunterPaymentRequest，包含：
                         payment_order (1 或 2)、payment_date、amount（可选）

    返回：
        更新后的 HeadhunterCommission ORM 对象

    异常：
        HTTPException 400 -- 付款顺序违规（如首款已付/尾款在首款前付）
        HTTPException 404 -- 佣金记录不存在
    """
    commission = await get_commission(db, commission_id)

    if data.payment_order == 1:
        # 处理第一期付款
        if commission.first_payment_status == "paid":
            raise HTTPException(status_code=400, detail="首次付款已完成")
        commission.first_payment_status = "paid"
        commission.first_payment_date = data.payment_date
        if data.amount is not None:
            commission.first_payment_amount = data.amount  # 允许实际金额与预计不同
    elif data.payment_order == 2:
        # 处理第二期付款，必须在第一期完成之后
        if commission.first_payment_status != "paid":
            raise HTTPException(
                status_code=400, detail="首次付款尚未完成，不可进行第二次付款"
            )
        if commission.second_payment_status == "paid":
            raise HTTPException(status_code=400, detail="第二次付款已完成")
        commission.second_payment_status = "paid"
        commission.second_payment_date = data.payment_date
        if data.amount is not None:
            commission.second_payment_amount = data.amount

    await db.flush()
    await db.refresh(commission)
    return commission


async def process_refund(
    db: AsyncSession, commission_id: int, data: HeadhunterRefundRequest
) -> HeadhunterCommission:
    """
    处理猎头保证期退款（状态流转）。

    业务背景：
        猎头通常提供一定期限的保证期（guarantee_period_days），若被推荐员工在保证期内离职，
        猎头需退还部分或全部佣金。本方法用于记录退款流程状态。

    状态流转：
        （无退款）→ requested（申请退款）→ completed（退款完成）

    参数：
        db            -- 异步数据库会话
        commission_id -- 佣金记录主键 ID
        data          -- HeadhunterRefundRequest，包含 refund_status 字段

    返回：
        更新后的 HeadhunterCommission ORM 对象

    异常：
        HTTPException 400 -- refund_status 值不合法（只能为 requested 或 completed）
        HTTPException 404 -- 佣金记录不存在
    """
    commission = await get_commission(db, commission_id)
    # 只允许设置为合法的退款状态
    if data.refund_status not in ("requested", "completed"):
        raise HTTPException(
            status_code=400,
            detail="退款状态只能为 requested 或 completed",
        )
    commission.refund_status = data.refund_status
    await db.flush()
    await db.refresh(commission)
    return commission


async def delete_commission(db: AsyncSession, commission_id: int) -> None:
    """
    删除猎头佣金记录（物理删除）。

    参数：
        db            -- 异步数据库会话
        commission_id -- 佣金记录主键 ID

    异常：
        HTTPException 404 -- 佣金记录不存在
    """
    commission = await get_commission(db, commission_id)
    await db.delete(commission)
    await db.flush()


# ───────────────────── VendorEvaluation ─────────────────────


async def create_evaluation(
    db: AsyncSession, evaluator_id: int, data: VendorEvaluationCreate
) -> VendorEvaluationOut:
    """
    创建供应商绩效评分（季度/年度评估）。

    评分规则：
        overall_score = (quality_score + delivery_score + price_score + cooperation_score) / 4
        四个维度的算术平均值，保留两位小数。

    评分维度说明：
        - quality_score      -- 质量评分（服务/产品质量达标率）
        - delivery_score     -- 交付评分（按时交付率、响应速度）
        - price_score        -- 价格评分（价格竞争力、性价比）
        - cooperation_score  -- 合作评分（沟通协作、合规性）

    参数：
        db           -- 异步数据库会话
        evaluator_id -- 评估人的员工 ID（来自当前登录用户）
        data         -- VendorEvaluationCreate schema，包含四个维度评分、
                        eval_year、eval_quarter、comments

    返回：
        VendorEvaluationOut schema（DTO），含供应商名称和评估人姓名（富化字段）

    异常：
        HTTPException 404 -- 供应商不存在（vendor_id 无效）
    """
    # 验证供应商存在
    vendor = await get_vendor(db, data.vendor_id)

    # 计算综合评分（四维度算术平均，保留两位小数）
    overall = (
        data.quality_score + data.delivery_score + data.price_score + data.cooperation_score
    ) / 4.0

    evaluation = VendorEvaluation(
        vendor_id=data.vendor_id,
        evaluator_id=evaluator_id,
        eval_year=data.eval_year,
        eval_quarter=data.eval_quarter,
        quality_score=data.quality_score,
        delivery_score=data.delivery_score,
        price_score=data.price_score,
        cooperation_score=data.cooperation_score,
        overall_score=round(overall, 2),
        comments=data.comments,
    )
    db.add(evaluation)
    await db.flush()
    await db.refresh(evaluation)

    # 查询评估人姓名（用于返回富化的 DTO）
    from app.models.employee import Employee
    evaluator_result = await db.execute(
        select(Employee).where(Employee.id == evaluator_id)
    )
    evaluator = evaluator_result.scalar_one_or_none()
    evaluator_name = evaluator.name if evaluator else ""

    return VendorEvaluationOut(
        id=evaluation.id,
        vendor_id=evaluation.vendor_id,
        evaluator_id=evaluation.evaluator_id,
        eval_year=evaluation.eval_year,
        eval_quarter=evaluation.eval_quarter,
        quality_score=evaluation.quality_score,
        delivery_score=evaluation.delivery_score,
        price_score=evaluation.price_score,
        cooperation_score=evaluation.cooperation_score,
        overall_score=evaluation.overall_score,
        comments=evaluation.comments,
        created_at=evaluation.created_at,
        vendor_name=vendor.company_name,
        evaluator_name=evaluator_name,
    )


async def list_evaluations(
    db: AsyncSession,
    vendor_id: Optional[int] = None,
    year: Optional[int] = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[int, list[VendorEvaluationOut]]:
    """
    查询供应商评分列表（JOIN 供应商名称和评估人姓名）。

    参数：
        db        -- 异步数据库会话
        vendor_id -- 按供应商过滤，None 表示查所有供应商的评分
        year      -- 按评估年份过滤，None 表示不过滤
        skip      -- 分页偏移量
        limit     -- 每页记录数

    返回：
        (total, evaluations) 元组，评分列表按 ID 倒序排列
        每条记录为 VendorEvaluationOut DTO（含供应商名称和评估人姓名）
    """
    from app.models.employee import Employee

    # 联表查询：VendorEvaluation JOIN Vendor JOIN Employee（评估人）
    stmt = (
        select(VendorEvaluation, Vendor.company_name, Employee.name)
        .join(Vendor, Vendor.id == VendorEvaluation.vendor_id)
        .join(Employee, Employee.id == VendorEvaluation.evaluator_id)
    )
    if vendor_id:
        stmt = stmt.where(VendorEvaluation.vendor_id == vendor_id)
    if year:
        stmt = stmt.where(VendorEvaluation.eval_year == year)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(VendorEvaluation.id.desc()).offset(skip).limit(limit)
    )
    rows = result.all()

    # 将联表结果组装为 DTO 列表
    items = []
    for eval_obj, vendor_name, evaluator_name in rows:
        items.append(
            VendorEvaluationOut(
                id=eval_obj.id,
                vendor_id=eval_obj.vendor_id,
                evaluator_id=eval_obj.evaluator_id,
                eval_year=eval_obj.eval_year,
                eval_quarter=eval_obj.eval_quarter,
                quality_score=eval_obj.quality_score,
                delivery_score=eval_obj.delivery_score,
                price_score=eval_obj.price_score,
                cooperation_score=eval_obj.cooperation_score,
                overall_score=eval_obj.overall_score,
                comments=eval_obj.comments,
                created_at=eval_obj.created_at,
                vendor_name=vendor_name or "",
                evaluator_name=evaluator_name or "",
            )
        )
    return total, items


async def get_vendor_avg_score(db: AsyncSession, vendor_id: int) -> VendorScoreSummary:
    """
    查询某供应商的历史评分汇总（各维度均值 + 综合均值）。

    用途：供应商详情页的评分看板，展示历史平均表现。

    参数：
        db        -- 异步数据库会话
        vendor_id -- 供应商主键 ID

    返回：
        VendorScoreSummary schema，包含：
        - eval_count           -- 历史评估次数
        - avg_quality_score    -- 质量维度历史均值
        - avg_delivery_score   -- 交付维度历史均值
        - avg_price_score      -- 价格维度历史均值
        - avg_cooperation_score -- 合作维度历史均值
        - avg_overall_score    -- 综合评分历史均值
        所有均值保留两位小数，无评分记录时返回 0.0

    异常：
        HTTPException 404 -- 供应商不存在
    """
    vendor = await get_vendor(db, vendor_id)

    # 一次聚合查询取所有均值，避免多次查询
    result = await db.execute(
        select(
            func.count(VendorEvaluation.id),
            func.avg(VendorEvaluation.quality_score),
            func.avg(VendorEvaluation.delivery_score),
            func.avg(VendorEvaluation.price_score),
            func.avg(VendorEvaluation.cooperation_score),
            func.avg(VendorEvaluation.overall_score),
        ).where(VendorEvaluation.vendor_id == vendor_id)
    )
    row = result.one()
    count = row[0] or 0

    # 辅助函数：将 None 或浮点数转为保留两位小数的 float
    def _f(v: Optional[float]) -> float:
        return round(float(v), 2) if v is not None else 0.0

    return VendorScoreSummary(
        vendor_id=vendor_id,
        vendor_name=vendor.company_name,
        eval_count=count,
        avg_quality_score=_f(row[1]),
        avg_delivery_score=_f(row[2]),
        avg_price_score=_f(row[3]),
        avg_cooperation_score=_f(row[4]),
        avg_overall_score=_f(row[5]),
    )


# ───────────────────── VendorBlacklist ─────────────────────


async def add_to_blacklist(
    db: AsyncSession, vendor_id: int, operator_id: int, reason: str
) -> BlacklistOut:
    """
    将供应商加入黑名单。

    业务规则（软删除 + 复用模式）：
        - 如果该供应商已有 is_active=True 的黑名单记录，则报错（防止重复加入）
        - 如果该供应商存在历史黑名单记录（is_active=False，之前被移除过），
          则复用该记录：重置 reason、added_by、added_at，并将 is_active 改回 True
        - 如果从未有过黑名单记录，则新建一条记录
        此设计保证数据库中每个供应商只有一条黑名单记录（满足 UNIQUE 约束）

    参数：
        db          -- 异步数据库会话
        vendor_id   -- 要加黑名单的供应商 ID
        operator_id -- 操作人员工 ID（记录是谁加的黑名单）
        reason      -- 加入黑名单的原因说明

    返回：
        BlacklistOut DTO，包含供应商名称和操作人姓名（富化字段）

    异常：
        HTTPException 400 -- 供应商已在黑名单中（is_active=True 记录已存在）
        HTTPException 404 -- 供应商不存在
    """
    vendor = await get_vendor(db, vendor_id)

    # 检查是否已在黑名单（is_active=True 的活跃记录）
    existing_result = await db.execute(
        select(VendorBlacklist).where(
            VendorBlacklist.vendor_id == vendor_id,
            VendorBlacklist.is_active.is_(True),
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="该供应商已在黑名单中")

    # 查询是否有历史黑名单记录（is_active=False，可复用）
    inactive_result = await db.execute(
        select(VendorBlacklist).where(VendorBlacklist.vendor_id == vendor_id)
    )
    record = inactive_result.scalar_one_or_none()

    if record:
        # 复用历史记录：更新字段后重新激活，保持同一条记录
        record.reason = reason
        record.added_by = operator_id
        record.added_at = datetime.now(timezone.utc)
        record.removed_at = None  # 清除上次移除时间
        record.is_active = True
    else:
        # 首次加入黑名单：新建记录
        record = VendorBlacklist(
            vendor_id=vendor_id,
            reason=reason,
            added_by=operator_id,
            is_active=True,
        )
        db.add(record)

    await db.flush()
    await db.refresh(record)

    # 查询操作人姓名（用于富化返回 DTO）
    from app.models.employee import Employee
    op_result = await db.execute(select(Employee).where(Employee.id == operator_id))
    op_emp = op_result.scalar_one_or_none()

    return BlacklistOut(
        id=record.id,
        vendor_id=record.vendor_id,
        vendor_name=vendor.company_name,
        reason=record.reason,
        added_at=record.added_at,
        removed_at=record.removed_at,
        is_active=record.is_active,
        added_by_name=op_emp.name if op_emp else "",
    )


async def remove_from_blacklist(
    db: AsyncSession, vendor_id: int, operator_id: int
) -> BlacklistOut:
    """
    将供应商从黑名单移除（软删除：is_active=False，记录 removed_at 时间戳）。

    业务规则：
        - 移除操作不会物理删除记录，仅将 is_active 设为 False 并记录移除时间
        - 若该供应商不在黑名单（无 is_active=True 的记录），则报 404

    参数：
        db          -- 异步数据库会话
        vendor_id   -- 要从黑名单移除的供应商 ID
        operator_id -- 操作人员工 ID（当前仅记录在日志中，不写入黑名单表）

    返回：
        BlacklistOut DTO，is_active=False，removed_at 为当前时间

    异常：
        HTTPException 404 -- 供应商不在黑名单中，或供应商不存在
    """
    vendor = await get_vendor(db, vendor_id)

    # 查找活跃的黑名单记录
    result = await db.execute(
        select(VendorBlacklist).where(
            VendorBlacklist.vendor_id == vendor_id,
            VendorBlacklist.is_active.is_(True),
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="该供应商不在黑名单中")

    # 软删除：标记为非活跃并记录移除时间
    record.is_active = False
    record.removed_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(record)

    # 查询操作人姓名
    from app.models.employee import Employee
    op_result = await db.execute(select(Employee).where(Employee.id == operator_id))
    op_emp = op_result.scalar_one_or_none()

    return BlacklistOut(
        id=record.id,
        vendor_id=record.vendor_id,
        vendor_name=vendor.company_name,
        reason=record.reason,
        added_at=record.added_at,
        removed_at=record.removed_at,
        is_active=record.is_active,
        added_by_name=op_emp.name if op_emp else "",
    )


async def list_blacklist(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    active_only: bool = True,
) -> tuple[int, list[BlacklistOut]]:
    """
    查询黑名单列表（联表获取供应商名称和操作人姓名）。

    参数：
        db          -- 异步数据库会话
        skip        -- 分页偏移量
        limit       -- 每页记录数
        active_only -- True：只查询当前在黑名单中的（is_active=True）；
                       False：查询全部历史记录（含已移除）

    返回：
        (total, blacklist_items) 元组
        每条记录为 BlacklistOut DTO（含供应商名称和操作人姓名）
    """
    from app.models.employee import Employee

    # 联表：黑名单 JOIN 供应商 JOIN 操作人（员工表）
    stmt = (
        select(VendorBlacklist, Vendor.company_name, Employee.name)
        .join(Vendor, Vendor.id == VendorBlacklist.vendor_id)
        .join(Employee, Employee.id == VendorBlacklist.added_by)
    )
    if active_only:
        stmt = stmt.where(VendorBlacklist.is_active.is_(True))

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(VendorBlacklist.id.desc()).offset(skip).limit(limit)
    )
    rows = result.all()

    # 组装 DTO 列表
    items = []
    for bl, vendor_name, added_by_name in rows:
        items.append(
            BlacklistOut(
                id=bl.id,
                vendor_id=bl.vendor_id,
                vendor_name=vendor_name or "",
                reason=bl.reason,
                added_at=bl.added_at,
                removed_at=bl.removed_at,
                is_active=bl.is_active,
                added_by_name=added_by_name or "",
            )
        )
    return total, items


async def is_vendor_blacklisted(db: AsyncSession, vendor_id: int) -> bool:
    """
    检查供应商是否当前在黑名单中（轻量级判断，仅查主键）。

    用途：在创建合同、签发佣金等业务操作前，快速检查供应商是否被禁用。

    参数：
        db        -- 异步数据库会话
        vendor_id -- 供应商主键 ID

    返回：
        True  -- 供应商当前在黑名单中（is_active=True 的记录存在）
        False -- 供应商不在黑名单中
    """
    result = await db.execute(
        select(VendorBlacklist.id).where(
            VendorBlacklist.vendor_id == vendor_id,
            VendorBlacklist.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None
