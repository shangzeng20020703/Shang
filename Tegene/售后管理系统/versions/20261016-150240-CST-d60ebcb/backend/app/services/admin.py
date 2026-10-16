"""
行政管理业务逻辑服务 - Admin Module Service
============================================

本模块负责售后管理系统中所有行政管理相关的业务逻辑，涵盖 6 个子模块：

1. **公告管理（Announcement）**
   - 支持草稿（draft）→ 发布（published）状态流转
   - 发布时自动记录 publish_time
   - 查询时按置顶（is_top）+ 发布时间倒序排列
   - 记录已读数（read_count，每次访问详情时递增）

2. **资产管理（Asset）**
   - 资产状态机（status 字段）：
       available（库存中）→ checked_out（领用中）→ available（归还后）
       available → under_maintenance（维修中）→ available
       任意状态 → scrapped（报废，终态）
   - 领用（checkout）时自动将 asset.status 改为 checked_out
   - 归还（return）时自动将 asset.status 改回 available

3. **访客管理（Visitor）**
   - 访客状态机（5 种状态）：
       pending（预约待审）→ approved（已审核）→ checked_in（已入场）→ checked_out（已离场）
       任意状态 → cancelled（已取消）
   - 签到时自动记录 actual_arrival（实际到达时间）
   - 签退时自动记录 actual_departure（实际离开时间）

4. **会议室预约（BookingRoom）**
   - 预约时进行时间段冲突检测（同一房间、同一日期、状态为 confirmed 的记录）
   - 时间段重叠判断：new_start < existing_end AND new_end > existing_start
   - 更新预约时若涉及日期/时间字段，重新触发冲突检测（排除自身 ID）
   - 取消操作仅修改 status 为 cancelled，不物理删除

5. **用车申请（BookingVehicle）**
   - 申请初始状态为 pending
   - 只有 pending 状态的申请可以修改
   - 审批操作：approved（批准，可分配司机）/ rejected（拒绝）

6. **（预留扩展）其他行政事务**
   - 可扩展餐厅预订、办公用品申请等

调用关系：
    api/v1/admin.py → services/admin.py（本文件）→ models/admin.py

依赖框架：FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

import io
from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.sql_compat import order_by_nulls_last_desc
from app.models.admin import (
    Announcement,
    Asset,
    AssetCheckout,
    AssetDepreciationRule,
    AssetInventoryRecord,
    AssetMonthlySnapshot,
    ToolItem,
    BookingRoom,
    BookingVehicle,
    Visitor,
)
from app.models.employee import Employee
from app.models.organization import Department
from app.schemas.admin import (
    AnnouncementCreate,
    AnnouncementUpdate,
    AssetCheckoutCreate,
    AssetDepreciationRuleCreate,
    AssetDepreciationRuleUpdate,
    AssetInventoryRecordCreate,
    AssetImportResult,
    AssetMonthlySnapshotGenerateRequest,
    AssetCreate,
    AssetReturnRequest,
    AssetStatsOut,
    AssetUpdate,
    ToolItemCreate,
    ToolItemUpdate,
    BookingRoomCreate,
    BookingRoomUpdate,
    BookingVehicleApproval,
    BookingVehicleCreate,
    BookingVehicleUpdate,
    VisitorCheckInRequest,
    VisitorCreate,
    VisitorUpdate,
)


# ───────────────────── Announcement（公告管理）─────────────────────


async def create_announcement(
    db: AsyncSession, data: AnnouncementCreate, publisher_id: int
) -> Announcement:
    """
    创建公告，初始状态为草稿（draft）。

    业务说明：
        公告创建后处于草稿状态，不对员工可见。
        需通过 publish_announcement() 发布后才能显示。

    参数：
        db           -- 异步数据库会话
        data         -- AnnouncementCreate schema，包含标题、内容、分类、是否置顶等
        publisher_id -- 发布人员工 ID（来自当前登录用户）

    返回：
        新建的 Announcement ORM 对象，status="draft"
    """
    announcement = Announcement(
        **data.model_dump(),
        publisher_id=publisher_id,
        status="draft",  # 初始状态固定为草稿
    )
    db.add(announcement)
    await db.flush()
    await db.refresh(announcement)
    return announcement


async def get_announcement(db: AsyncSession, announcement_id: int) -> Announcement:
    """
    按 ID 查询公告。

    参数：
        db              -- 异步数据库会话
        announcement_id -- 公告主键 ID

    返回：
        Announcement ORM 对象

    异常：
        HTTPException 404 -- 公告不存在
    """
    result = await db.execute(
        select(Announcement).where(Announcement.id == announcement_id)
    )
    ann = result.scalar_one_or_none()
    if not ann:
        raise HTTPException(status_code=404, detail="公告不存在")
    return ann


async def list_announcements(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    status_filter: Optional[str] = None,
    category: Optional[str] = None,
) -> tuple[int, list[Announcement]]:
    """
    分页查询公告列表，支持按状态和分类过滤。

    排序规则（优先级从高到低）：
        1. 置顶（is_top=True）优先显示
        2. publish_time 倒序（最新发布的在前），NULL 排在最后
        3. created_at 倒序

    参数：
        db            -- 异步数据库会话
        skip          -- 分页偏移量
        limit         -- 每页记录数
        status_filter -- 按状态过滤：draft（草稿）/ published（已发布）
        category      -- 按公告分类过滤（如"公司新闻"/"规章制度"等）

    返回：
        (total, announcements) 元组
    """
    stmt = select(Announcement)
    if status_filter:
        stmt = stmt.where(Announcement.status == status_filter)
    if category:
        stmt = stmt.where(Announcement.category == category)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(
            Announcement.is_top.desc(),                        # 置顶优先
            *order_by_nulls_last_desc(Announcement.publish_time),  # 发布时间倒序，NULL 在最后
            Announcement.created_at.desc(),                    # 创建时间兜底排序
        )
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def update_announcement(
    db: AsyncSession, announcement_id: int, data: AnnouncementUpdate
) -> Announcement:
    """
    更新公告内容或属性（仅更新请求中提供的字段）。

    注意：若要修改发布状态，请使用专用的 publish_announcement() 方法，
         不要通过本方法直接修改 status 字段。

    参数：
        db              -- 异步数据库会话
        announcement_id -- 公告主键 ID
        data            -- AnnouncementUpdate schema

    返回：
        更新后的 Announcement ORM 对象

    异常：
        HTTPException 404 -- 公告不存在
    """
    ann = await get_announcement(db, announcement_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(ann, field, value)
    await db.flush()
    await db.refresh(ann)
    return ann


async def publish_announcement(
    db: AsyncSession, announcement_id: int
) -> Announcement:
    """
    发布公告（状态从 draft 变更为 published，并记录发布时间）。

    业务规则：
        - 已发布的公告不可重复发布（防止重置 publish_time）

    参数：
        db              -- 异步数据库会话
        announcement_id -- 公告主键 ID

    返回：
        更新后的 Announcement ORM 对象，status="published"，publish_time=当前时间

    异常：
        HTTPException 400 -- 公告已发布
        HTTPException 404 -- 公告不存在
    """
    ann = await get_announcement(db, announcement_id)
    if ann.status == "published":
        raise HTTPException(status_code=400, detail="公告已发布")
    ann.status = "published"
    ann.publish_time = datetime.now(timezone.utc)  # 记录实际发布时间
    await db.flush()
    await db.refresh(ann)
    return ann


async def increment_read_count(
    db: AsyncSession, announcement_id: int
) -> Announcement:
    """
    增加公告的已读计数（每次员工查看详情时调用）。

    注意：此操作不做防重复点击处理，调用方（API 层）负责控制调用时机。

    参数：
        db              -- 异步数据库会话
        announcement_id -- 公告主键 ID

    返回：
        更新后的 Announcement ORM 对象，read_count +1

    异常：
        HTTPException 404 -- 公告不存在
    """
    ann = await get_announcement(db, announcement_id)
    ann.read_count += 1
    await db.flush()
    await db.refresh(ann)
    return ann


async def delete_announcement(db: AsyncSession, announcement_id: int) -> None:
    """
    删除公告（物理删除）。

    参数：
        db              -- 异步数据库会话
        announcement_id -- 公告主键 ID

    异常：
        HTTPException 404 -- 公告不存在
    """
    ann = await get_announcement(db, announcement_id)
    await db.delete(ann)
    await db.flush()


# ───────────────────── Asset（资产管理）─────────────────────


def _round2(value: Optional[float]) -> Optional[float]:
    return round(float(value), 2) if value is not None else None


def _clean_str(value: object, max_len: Optional[int] = None) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if max_len is not None:
        return text[:max_len]
    return text


async def _find_employee_by_name(
    db: AsyncSession,
    *,
    employee_name: Optional[str],
    usage_department: Optional[str] = None,
) -> Optional[Employee]:
    name = _clean_str(employee_name, 50)
    if not name:
        return None

    stmt = (
        select(Employee)
        .outerjoin(Department, Department.id == Employee.department_id)
        .where(Employee.name == name, Employee.is_active == True)
    )

    if usage_department:
        dept_rows = list((await db.execute(stmt.where(Department.name == usage_department))).scalars().all())
        if len(dept_rows) == 1:
            return dept_rows[0]

    rows = list((await db.execute(stmt)).scalars().all())
    if len(rows) == 1:
        return rows[0]
    return None


async def _sync_asset_checkout_from_holder(
    db: AsyncSession,
    *,
    asset: Asset,
    holder_name: Optional[str],
    usage_department: Optional[str],
    checkout_date: Optional[date],
) -> None:
    holder = _clean_str(holder_name, 50)
    asset.usage_employee_name_manual = holder

    active_checkout = (
        await db.execute(
            select(AssetCheckout)
            .where(
                AssetCheckout.asset_id == asset.id,
                AssetCheckout.status.in_(["active", "overdue"]),
            )
            .order_by(AssetCheckout.id.desc())
        )
    ).scalar_one_or_none()

    if asset.status == "retired":
        if active_checkout:
            active_checkout.status = "returned"
            active_checkout.actual_return_date = asset.scrap_date or checkout_date or date.today()
            active_checkout.return_condition = active_checkout.return_condition or "报废"
        return

    employee = await _find_employee_by_name(
        db,
        employee_name=holder,
        usage_department=usage_department,
    )
    if not employee:
        return

    actual_checkout_date = checkout_date or asset.acceptance_date or asset.purchase_date or date.today()
    asset.status = "checked_out"

    if active_checkout:
        active_checkout.employee_id = employee.id
        active_checkout.checkout_date = actual_checkout_date
        active_checkout.expected_return_date = None
        active_checkout.actual_return_date = None
        active_checkout.return_condition = None
        active_checkout.status = "active"
    else:
        db.add(
            AssetCheckout(
                asset_id=asset.id,
                employee_id=employee.id,
                checkout_date=actual_checkout_date,
                status="active",
            )
        )


def _normalize_asset_status(status: Optional[str]) -> str:
    raw = str(status or "").strip()
    mapping = {
        "在用": "checked_out",
        "闲置": "available",
        "空闲": "available",
        "维修中": "maintenance",
        "损坏": "maintenance",
        "报废": "retired",
        "#N/A": "available",
    }
    return mapping.get(raw, raw or "available")


def _month_elapsed(start_date: date, report_date: date) -> int:
    if report_date < start_date:
        return 0
    return (report_date.year - start_date.year) * 12 + (report_date.month - start_date.month) + 1


async def _find_matching_depreciation_rule(
    db: AsyncSession,
    *,
    major_category: Optional[str],
    minor_category: Optional[str],
) -> Optional[AssetDepreciationRule]:
    if not major_category:
        return None
    result = await db.execute(
        select(AssetDepreciationRule)
        .where(
            AssetDepreciationRule.major_category == major_category,
            AssetDepreciationRule.is_active == True,
        )
        .order_by(AssetDepreciationRule.minor_category.desc())
    )
    rules = list(result.scalars().all())
    exact = next((rule for rule in rules if (rule.minor_category or "") == (minor_category or "")), None)
    if exact:
        return exact
    return next((rule for rule in rules if not rule.minor_category), None)


async def _sync_asset_financial_fields(
    db: AsyncSession,
    asset: Asset,
    *,
    report_date: Optional[date] = None,
) -> None:
    report_date = report_date or date.today()
    rule = await _find_matching_depreciation_rule(
        db,
        major_category=asset.major_category or asset.category,
        minor_category=asset.minor_category,
    )

    if asset.amount_excluding_tax is None and asset.purchase_price is not None:
        if asset.tax_rate is not None and asset.tax_rate > 0:
            asset.amount_excluding_tax = _round2(asset.purchase_price / (1 + asset.tax_rate))
        else:
            asset.amount_excluding_tax = _round2(asset.purchase_price)

    if asset.depreciation_months is None and rule:
        asset.depreciation_months = rule.depreciation_months

    if asset.residual_value is None and rule and asset.amount_excluding_tax is not None and rule.residual_rate is not None:
        asset.residual_value = _round2(asset.amount_excluding_tax * rule.residual_rate)

    if asset.residual_value is None and asset.amount_excluding_tax is not None:
        asset.residual_value = 0.0

    depreciable_amount = None
    if asset.amount_excluding_tax is not None and asset.residual_value is not None:
        depreciable_amount = max(float(asset.amount_excluding_tax) - float(asset.residual_value), 0.0)

    if asset.monthly_depreciation_amount is None and depreciable_amount is not None and asset.depreciation_months:
        asset.monthly_depreciation_amount = _round2(depreciable_amount / asset.depreciation_months)

    start_date = asset.acceptance_date or asset.purchase_date
    effective_report_date = report_date
    if asset.status == "retired" and asset.scrap_date and asset.scrap_date < report_date:
        effective_report_date = asset.scrap_date

    if not start_date or not asset.depreciation_months or asset.depreciation_months <= 0 or depreciable_amount is None:
        asset.accumulated_depreciation_months = 0
        asset.remaining_depreciation_months = asset.depreciation_months
        asset.accumulated_depreciation_amount = 0.0 if depreciable_amount is not None else asset.accumulated_depreciation_amount
        asset.remaining_depreciation_amount = _round2(depreciable_amount) if depreciable_amount is not None else asset.remaining_depreciation_amount
        asset.current_month_depreciation_amount = 0.0
        asset.previous_month_accumulated_depreciation = 0.0
        return

    accumulated_months = min(_month_elapsed(start_date, effective_report_date), asset.depreciation_months)
    previous_months = max(accumulated_months - 1, 0)
    monthly_amount = float(asset.monthly_depreciation_amount or 0.0)
    accumulated_amount = min(_round2(monthly_amount * accumulated_months) or 0.0, depreciable_amount)
    previous_amount = min(_round2(monthly_amount * previous_months) or 0.0, depreciable_amount)
    remaining_amount = max(_round2(depreciable_amount - accumulated_amount) or 0.0, 0.0)
    current_amount = max(_round2(accumulated_amount - previous_amount) or 0.0, 0.0)

    asset.accumulated_depreciation_months = accumulated_months
    asset.remaining_depreciation_months = max(asset.depreciation_months - accumulated_months, 0)
    asset.accumulated_depreciation_amount = accumulated_amount
    asset.remaining_depreciation_amount = remaining_amount
    asset.current_month_depreciation_amount = current_amount
    asset.previous_month_accumulated_depreciation = previous_amount


async def create_asset(db: AsyncSession, data: AssetCreate) -> Asset:
    """
    新建资产记录（初始状态通常为 available 库存中）。

    参数：
        db   -- 异步数据库会话
        data -- AssetCreate schema，包含资产编号、名称、分类、购置日期、存放位置等

    返回：
        新建的 Asset ORM 对象
    """
    payload = data.model_dump()
    payload["status"] = _normalize_asset_status(payload.get("status"))
    asset = Asset(**payload)
    db.add(asset)
    await db.flush()
    await _sync_asset_financial_fields(db, asset)
    await db.flush()
    return await get_asset(db, asset.id)


async def get_asset(db: AsyncSession, asset_id: int) -> Asset:
    """
    按 ID 查询资产。

    参数：
        db       -- 异步数据库会话
        asset_id -- 资产主键 ID

    返回：
        Asset ORM 对象

    异常：
        HTTPException 404 -- 资产不存在
    """
    result = await db.execute(
        select(Asset)
        .options(
            selectinload(Asset.checkouts).selectinload(AssetCheckout.employee),
        )
        .where(Asset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    return asset


async def list_assets(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    status_filter: Optional[str] = None,
    category: Optional[str] = None,
    location_id: Optional[int] = None,
    keyword: Optional[str] = None,
    usage_department: Optional[str] = None,
) -> tuple[int, list[Asset]]:
    """
    分页查询资产列表，支持多条件过滤。

    参数：
        db            -- 异步数据库会话
        skip          -- 分页偏移量
        limit         -- 每页记录数
        status_filter -- 按资产状态过滤（available/checked_out/under_maintenance/scrapped）
        category      -- 按资产分类过滤（如"电脑设备"/"办公家具"等）
        location_id   -- 按存放地点过滤（关联 organizations.locations 表）

    返回：
        (total, assets) 元组，资产列表按 ID 倒序排列
    """
    stmt = select(Asset).options(
        selectinload(Asset.checkouts).selectinload(AssetCheckout.employee),
    )
    if status_filter:
        stmt = stmt.where(Asset.status == status_filter)
    if category:
        stmt = stmt.where(
            or_(
                Asset.category == category,
                Asset.major_category == category,
                Asset.minor_category == category,
            )
        )
    if location_id:
        stmt = stmt.where(Asset.location_id == location_id)
    if usage_department:
        stmt = stmt.where(Asset.usage_department == usage_department)
    if keyword:
        stmt = stmt.where(
            Asset.asset_no.ilike(f"%{keyword}%")
            | Asset.name.ilike(f"%{keyword}%")
        )

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(Asset.id.desc()).offset(skip).limit(limit)
    )
    return total, list(result.scalars().all())


async def update_asset(
    db: AsyncSession, asset_id: int, data: AssetUpdate
) -> Asset:
    """
    更新资产信息（仅更新请求中提供的字段）。

    注意：资产状态变更涉及业务规则时（如领用、归还），请使用专用方法：
         - checkout_asset()：领用
         - return_asset()：归还

    参数：
        db       -- 异步数据库会话
        asset_id -- 资产主键 ID
        data     -- AssetUpdate schema

    返回：
        更新后的 Asset ORM 对象

    异常：
        HTTPException 404 -- 资产不存在
    """
    asset = await get_asset(db, asset_id)
    payload = data.model_dump(exclude_unset=True)
    if "status" in payload:
        payload["status"] = _normalize_asset_status(payload.get("status"))
    if asset.status == "retired" and payload.get("status") not in (None, "retired"):
        raise HTTPException(status_code=400, detail="已报废资产不能恢复为其他状态")
    for field, value in payload.items():
        setattr(asset, field, value)
    await _sync_asset_financial_fields(db, asset)
    await db.flush()
    return await get_asset(db, asset.id)


async def delete_asset(db: AsyncSession, asset_id: int) -> None:
    """
    删除资产记录（物理删除）。

    警告：若资产当前状态为 checked_out（已领用），删除前应先执行归还操作。

    参数：
        db       -- 异步数据库会话
        asset_id -- 资产主键 ID

    异常：
        HTTPException 404 -- 资产不存在
    """
    asset = await get_asset(db, asset_id)
    active_checkout = await db.execute(
        select(AssetCheckout).where(
            AssetCheckout.asset_id == asset_id,
            AssetCheckout.status == "active",
        )
    )
    if active_checkout.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="资产仍在领用中，不能删除")
    await db.delete(asset)
    await db.flush()


# ───────────────────── AssetCheckout（资产领用与归还）─────────────────────


async def checkout_asset(
    db: AsyncSession, data: AssetCheckoutCreate
) -> AssetCheckout:
    """
    资产领用操作（状态机：available → checked_out）。

    业务流程：
        1. 检查资产当前状态是否为 available（可领用）
        2. 将资产状态改为 checked_out
        3. 创建 AssetCheckout 领用记录，status="active"

    参数：
        db   -- 异步数据库会话
        data -- AssetCheckoutCreate schema，包含 asset_id、employee_id、
                checkout_date、expected_return_date、purpose 等

    返回：
        新建的 AssetCheckout ORM 对象，status="active"

    异常：
        HTTPException 400 -- 资产当前状态不为 available，不可领用
        HTTPException 404 -- 资产不存在
    """
    asset = await get_asset(db, data.asset_id)
    if asset.status != "available":
        raise HTTPException(status_code=400, detail="该资产当前不可领用")
    employee = await db.get(Employee, data.employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="领用员工不存在")
    # 状态机转换：库存 → 使用中
    asset.status = "checked_out"
    asset.usage_department = getattr(employee, "department_name", None) or asset.usage_department
    checkout = AssetCheckout(**data.model_dump(), status="active")
    db.add(checkout)
    await db.flush()
    await db.refresh(checkout)
    return checkout


async def return_asset(
    db: AsyncSession, checkout_id: int, data: AssetReturnRequest
) -> AssetCheckout:
    """
    资产归还操作（状态机：checked_out → available）。

    业务流程：
        1. 查找领用记录，验证未重复归还
        2. 更新 AssetCheckout 记录：actual_return_date、return_condition、status="returned"
        3. 将对应资产状态改回 available

    参数：
        db          -- 异步数据库会话
        checkout_id -- AssetCheckout 领用记录主键 ID
        data        -- AssetReturnRequest schema，包含：
                       actual_return_date（实际归还日期）、return_condition（归还时状况）

    返回：
        更新后的 AssetCheckout ORM 对象，status="returned"

    异常：
        HTTPException 400 -- 该资产已归还（防止重复归还）
        HTTPException 404 -- 领用记录不存在
    """
    result = await db.execute(
        select(AssetCheckout).where(AssetCheckout.id == checkout_id)
    )
    checkout = result.scalar_one_or_none()
    if not checkout:
        raise HTTPException(status_code=404, detail="领用记录不存在")
    if checkout.status == "returned":
        raise HTTPException(status_code=400, detail="该资产已归还")

    # 更新领用记录
    checkout.actual_return_date = data.actual_return_date
    checkout.return_condition = data.return_condition
    checkout.status = "returned"

    # 同步更新资产状态（归还后资产重新可用）
    asset = await get_asset(db, checkout.asset_id)
    asset.status = "maintenance" if (data.return_condition and any(flag in data.return_condition for flag in ["损坏", "维修"])) else "available"

    await db.flush()
    await db.refresh(checkout)
    return checkout


async def list_checkouts_by_employee(
    db: AsyncSession,
    employee_id: int,
    active_only: bool = False,
) -> list[AssetCheckout]:
    """
    查询指定员工的资产领用记录列表。

    典型使用场景：
        - 员工详情页展示当前持有资产
        - 员工离职交接时核查需归还的资产（active_only=True）

    参数：
        db          -- 异步数据库会话
        employee_id -- 员工主键 ID
        active_only -- True：只查询 status="active" 的未归还记录；
                       False：查询该员工的所有历史领用记录（含已归还）

    返回：
        AssetCheckout 列表，按领用日期倒序排列
    """
    stmt = select(AssetCheckout).where(AssetCheckout.employee_id == employee_id)
    stmt = stmt.options(
        selectinload(AssetCheckout.asset),
        selectinload(AssetCheckout.employee),
    )
    if active_only:
        stmt = stmt.where(AssetCheckout.status == "active")  # 只查未归还的资产
    result = await db.execute(stmt.order_by(AssetCheckout.checkout_date.desc()))
    return list(result.scalars().all())


async def list_asset_checkouts(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    status_filter: Optional[str] = None,
    employee_id: Optional[int] = None,
) -> tuple[int, list[AssetCheckout]]:
    stmt = select(AssetCheckout).options(
        selectinload(AssetCheckout.asset),
        selectinload(AssetCheckout.employee),
    )
    if status_filter:
        stmt = stmt.where(AssetCheckout.status == status_filter)
    if employee_id:
        stmt = stmt.where(AssetCheckout.employee_id == employee_id)

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(
        stmt.order_by(AssetCheckout.checkout_date.desc()).offset(skip).limit(limit)
    )
    return total, list(result.scalars().all())


async def create_asset_inventory_record(
    db: AsyncSession,
    data: AssetInventoryRecordCreate,
) -> AssetInventoryRecord:
    asset = await get_asset(db, data.asset_id)
    record = AssetInventoryRecord(**data.model_dump())
    db.add(record)
    if data.result == "丢失":
        asset.status = "retired"
        asset.scrap_date = data.inventory_date
        asset.scrap_reason = "盘点丢失"
        asset.inventory_loss_flag = True
    elif data.result == "损坏":
        asset.status = "maintenance"
    await _sync_asset_financial_fields(db, asset, report_date=data.inventory_date)
    await db.flush()
    await db.refresh(record)
    return record


async def list_asset_inventory_records(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    result_filter: Optional[str] = None,
    asset_id: Optional[int] = None,
) -> tuple[int, list[AssetInventoryRecord]]:
    stmt = select(AssetInventoryRecord)
    if result_filter:
        stmt = stmt.where(AssetInventoryRecord.result == result_filter)
    if asset_id:
        stmt = stmt.where(AssetInventoryRecord.asset_id == asset_id)
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(
        stmt.order_by(AssetInventoryRecord.inventory_date.desc(), AssetInventoryRecord.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def get_asset_stats(db: AsyncSession) -> AssetStatsOut:
    total_assets = int(await db.scalar(select(func.count()).select_from(Asset)) or 0)
    total_value = float(
        await db.scalar(
            select(func.coalesce(func.sum(func.coalesce(Asset.amount_excluding_tax, Asset.purchase_price, 0.0)), 0.0))
        ) or 0.0
    )
    in_use_count = int(await db.scalar(select(func.count()).select_from(Asset).where(Asset.status == "checked_out")) or 0)
    idle_count = int(await db.scalar(select(func.count()).select_from(Asset).where(Asset.status == "available")) or 0)
    maintenance_count = int(await db.scalar(select(func.count()).select_from(Asset).where(Asset.status == "maintenance")) or 0)
    retired_count = int(await db.scalar(select(func.count()).select_from(Asset).where(Asset.status == "retired")) or 0)
    asset_rows = list(
        (
            await db.execute(
                select(
                    Asset.remaining_depreciation_amount,
                    Asset.residual_value,
                    Asset.current_month_depreciation_amount,
                )
            )
        ).all()
    )
    total_net_value = _round2(
        sum(float(row[0] or 0.0) + float(row[1] or 0.0) for row in asset_rows)
    ) or 0.0
    total_current_month_depreciation = _round2(
        sum(float(row[2] or 0.0) for row in asset_rows)
    ) or 0.0

    category_rows = await db.execute(
        select(
            func.coalesce(Asset.major_category, Asset.category),
            func.count(Asset.id),
            func.coalesce(func.sum(func.coalesce(Asset.amount_excluding_tax, Asset.purchase_price, 0.0)), 0.0),
        ).group_by(func.coalesce(Asset.major_category, Asset.category))
    )
    department_rows = await db.execute(
        select(
            Asset.usage_department,
            func.count(Asset.id),
            func.coalesce(func.sum(func.coalesce(Asset.amount_excluding_tax, Asset.purchase_price, 0.0)), 0.0),
        ).group_by(Asset.usage_department)
    )

    return AssetStatsOut(
        total_assets=total_assets,
        total_value=total_value,
        total_net_value=total_net_value,
        total_current_month_depreciation=total_current_month_depreciation,
        in_use_count=in_use_count,
        idle_count=idle_count,
        maintenance_count=maintenance_count,
        retired_count=retired_count,
        by_category=[
            {"name": row[0] or "未分类", "count": int(row[1] or 0), "value": float(row[2] or 0)}
            for row in category_rows.all()
        ],
        by_department=[
            {"name": row[0] or "未分配", "count": int(row[1] or 0), "value": float(row[2] or 0)}
            for row in department_rows.all()
        ],
    )


async def create_asset_depreciation_rule(
    db: AsyncSession,
    data: AssetDepreciationRuleCreate,
) -> AssetDepreciationRule:
    rule = AssetDepreciationRule(**data.model_dump())
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


async def list_asset_depreciation_rules(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    major_category: Optional[str] = None,
) -> tuple[int, list[AssetDepreciationRule]]:
    stmt = select(AssetDepreciationRule)
    if major_category:
        stmt = stmt.where(AssetDepreciationRule.major_category == major_category)
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    result = await db.execute(
        stmt.order_by(
            AssetDepreciationRule.major_category.asc(),
            AssetDepreciationRule.minor_category.asc(),
            AssetDepreciationRule.id.desc(),
        ).offset(skip).limit(limit)
    )
    return total, list(result.scalars().all())


async def update_asset_depreciation_rule(
    db: AsyncSession,
    rule_id: int,
    data: AssetDepreciationRuleUpdate,
) -> AssetDepreciationRule:
    rule = await db.get(AssetDepreciationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="折旧规则不存在")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.flush()
    await db.refresh(rule)
    return rule


async def delete_asset_depreciation_rule(db: AsyncSession, rule_id: int) -> None:
    rule = await db.get(AssetDepreciationRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="折旧规则不存在")
    await db.delete(rule)
    await db.flush()


async def generate_asset_monthly_snapshots(
    db: AsyncSession,
    data: AssetMonthlySnapshotGenerateRequest,
) -> dict:
    report_date = date(data.report_year, data.report_month, monthrange(data.report_year, data.report_month)[1])
    if data.overwrite:
        existing = await db.execute(
            select(AssetMonthlySnapshot).where(
                AssetMonthlySnapshot.report_year == data.report_year,
                AssetMonthlySnapshot.report_month == data.report_month,
            )
        )
        for row in existing.scalars().all():
            await db.delete(row)
        await db.flush()
    else:
        exists = await db.scalar(
            select(func.count()).select_from(AssetMonthlySnapshot).where(
                AssetMonthlySnapshot.report_year == data.report_year,
                AssetMonthlySnapshot.report_month == data.report_month,
            )
        )
        if exists:
            raise HTTPException(status_code=400, detail="该月份快照已存在，请勾选覆盖后重试")

    result = await db.execute(
        select(Asset).options(selectinload(Asset.checkouts).selectinload(AssetCheckout.employee))
    )
    assets = list(result.scalars().all())

    created = 0
    for asset in assets:
        await _sync_asset_financial_fields(db, asset, report_date=report_date)
        snapshot = AssetMonthlySnapshot(
            asset_id=asset.id,
            report_year=data.report_year,
            report_month=data.report_month,
            report_date=report_date,
            asset_no=asset.asset_no,
            name=asset.name,
            major_category=asset.major_category or asset.category,
            minor_category=asset.minor_category,
            asset_description=asset.asset_description,
            supplier=asset.supplier,
            purchase_price=asset.purchase_price,
            tax_rate=asset.tax_rate,
            amount_excluding_tax=asset.amount_excluding_tax,
            quantity=asset.quantity or 1,
            unit=asset.unit,
            acceptance_date=asset.acceptance_date,
            custodian=asset.custodian,
            usage_department=asset.usage_department,
            usage_employee_name=asset.usage_employee_name,
            storage_location=asset.storage_location,
            depreciation_months=asset.depreciation_months,
            residual_value=asset.residual_value,
            monthly_depreciation_amount=asset.monthly_depreciation_amount,
            accumulated_depreciation_months=asset.accumulated_depreciation_months or 0,
            remaining_depreciation_months=asset.remaining_depreciation_months,
            accumulated_depreciation_amount=asset.accumulated_depreciation_amount,
            remaining_depreciation_amount=asset.remaining_depreciation_amount,
            current_month_depreciation_amount=asset.current_month_depreciation_amount,
            previous_month_accumulated_depreciation=asset.previous_month_accumulated_depreciation,
            status=asset.status,
            matched_ledger_code=asset.matched_ledger_code,
            matched_ledger_department=asset.matched_ledger_department,
            inventory_loss_flag=asset.inventory_loss_flag,
        )
        db.add(snapshot)
        created += 1
    await db.flush()
    return {
        "created": created,
        "report_year": data.report_year,
        "report_month": data.report_month,
        "report_date": str(report_date),
    }


async def list_asset_monthly_snapshots(
    db: AsyncSession,
    report_year: int,
    report_month: int,
    skip: int = 0,
    limit: int = 1000,
    major_category: Optional[str] = None,
    keyword: Optional[str] = None,
) -> tuple[int, list[AssetMonthlySnapshot]]:
    stmt = select(AssetMonthlySnapshot).where(
        AssetMonthlySnapshot.report_year == report_year,
        AssetMonthlySnapshot.report_month == report_month,
    )
    if major_category:
        stmt = stmt.where(AssetMonthlySnapshot.major_category == major_category)
    if keyword:
        stmt = stmt.where(
            AssetMonthlySnapshot.asset_no.ilike(f"%{keyword}%")
            | AssetMonthlySnapshot.name.ilike(f"%{keyword}%")
        )
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    result = await db.execute(
        stmt.order_by(AssetMonthlySnapshot.major_category.asc(), AssetMonthlySnapshot.asset_no.asc())
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


def _asset_export_headers() -> list[str]:
    return [
        "资产编号", "资产大类", "资产小类", "资产说明", "供应商", "购买金额", "税率", "不含税金额",
        "数量", "单位", "验收日期", "保管人", "所属部门", "存放地点", "折旧时间", "残值",
        "每月折旧金额", "累计折旧月份", "剩余折旧月份", "累计折旧金额", "剩余折旧金额",
        "当月折旧金额", "上月累计折旧", "东莞行政台账编号", "匹配行政台账最新编码",
        "匹配行政台账最新部门", "使用人", "状态\n(有报废)", "盘亏",
    ]


def _asset_row_from_obj(item) -> list:
    status_map = {
        "available": "闲置",
        "checked_out": "在用",
        "maintenance": "维修中",
        "retired": "报废",
    }
    return [
        getattr(item, "asset_no", None),
        getattr(item, "major_category", None) or getattr(item, "category", None),
        getattr(item, "minor_category", None),
        getattr(item, "asset_description", None) or getattr(item, "name", None),
        getattr(item, "supplier", None),
        getattr(item, "purchase_price", None),
        getattr(item, "tax_rate", None),
        getattr(item, "amount_excluding_tax", None),
        getattr(item, "quantity", None),
        getattr(item, "unit", None),
        getattr(item, "acceptance_date", None),
        getattr(item, "custodian", None),
        getattr(item, "usage_department", None),
        getattr(item, "storage_location", None),
        getattr(item, "depreciation_months", None),
        getattr(item, "residual_value", None),
        getattr(item, "monthly_depreciation_amount", None),
        getattr(item, "accumulated_depreciation_months", None),
        getattr(item, "remaining_depreciation_months", None),
        getattr(item, "accumulated_depreciation_amount", None),
        getattr(item, "remaining_depreciation_amount", None),
        getattr(item, "current_month_depreciation_amount", None),
        getattr(item, "previous_month_accumulated_depreciation", None),
        getattr(item, "dongguan_ledger_no", None),
        getattr(item, "matched_ledger_code", None),
        getattr(item, "matched_ledger_department", None),
        getattr(item, "usage_employee_name", None),
        status_map.get(getattr(item, "status", None), getattr(item, "status", None)),
        "是" if getattr(item, "inventory_loss_flag", False) else "",
    ]


async def export_assets_excel(
    db: AsyncSession,
    *,
    report_year: Optional[int] = None,
    report_month: Optional[int] = None,
) -> bytes:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws_summary = wb.active
    ws_summary.title = "汇总"
    ws_detail = wb.create_sheet("固定资产")
    ws_rules = wb.create_sheet("折旧规则")

    if report_year and report_month:
        _, rows = await list_asset_monthly_snapshots(db, report_year=report_year, report_month=report_month, limit=100000)
        report_date = date(report_year, report_month, monthrange(report_year, report_month)[1])
    else:
        result = await db.execute(
            select(Asset).options(selectinload(Asset.checkouts).selectinload(AssetCheckout.employee))
        )
        rows = list(result.scalars().all())
        report_date = date.today()
        for item in rows:
            await _sync_asset_financial_fields(db, item, report_date=report_date)

    ws_detail.append(["报告日期：", report_date])
    headers = _asset_export_headers()
    ws_detail.append(headers)
    for cell in ws_detail[2]:
        cell.font = Font(bold=True)
    for item in rows:
        ws_detail.append(_asset_row_from_obj(item))

    summary_map: dict[str, dict[str, float]] = {}
    for item in rows:
        key = getattr(item, "major_category", None) or getattr(item, "category", None) or "未分类"
        bucket = summary_map.setdefault(key, {"amount": 0.0, "accumulated": 0.0, "current": 0.0})
        bucket["amount"] += float(getattr(item, "amount_excluding_tax", None) or getattr(item, "purchase_price", None) or 0.0)
        bucket["accumulated"] += float(getattr(item, "accumulated_depreciation_amount", None) or 0.0)
        bucket["current"] += float(getattr(item, "current_month_depreciation_amount", None) or 0.0)
    ws_summary.append(["行标签", "求和项:不含税金额", "求和项:累计折旧金额", "求和项:当月折旧金额"])
    for key, bucket in summary_map.items():
        ws_summary.append([key, _round2(bucket["amount"]), _round2(bucket["accumulated"]), _round2(bucket["current"])])
    ws_summary.append([
        "总计",
        _round2(sum(item["amount"] for item in summary_map.values())),
        _round2(sum(item["accumulated"] for item in summary_map.values())),
        _round2(sum(item["current"] for item in summary_map.values())),
    ])

    ws_rules.append(["资产大类", "资产小类", "折旧月份", "残值率", "备注"])
    rules_result = await db.execute(
        select(AssetDepreciationRule).order_by(AssetDepreciationRule.major_category.asc(), AssetDepreciationRule.minor_category.asc())
    )
    for rule in rules_result.scalars().all():
        ws_rules.append([rule.major_category, rule.minor_category, rule.depreciation_months, rule.residual_rate, rule.remark])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


async def import_assets_excel(
    db: AsyncSession,
    *,
    file_bytes: bytes,
    filename: str,
) -> AssetImportResult:
    import openpyxl

    if not file_bytes:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    imported_assets = 0
    updated_assets = 0
    imported_rules = 0
    errors: list[str] = []

    if "折旧规则" in wb.sheetnames:
        ws_rules = wb["折旧规则"]
        rule_headers = [cell for cell in next(ws_rules.iter_rows(min_row=1, max_row=1, values_only=True))]
        rule_idx = {str(h).strip(): i for i, h in enumerate(rule_headers) if h is not None}
        for row_no, row in enumerate(ws_rules.iter_rows(min_row=2, values_only=True), start=2):
            if not any(row):
                continue
            major = str(row[rule_idx.get("资产大类", 0)] or "").strip()
            minor = str(row[rule_idx.get("资产小类", 1)] or "").strip() or None
            months = row[rule_idx.get("折旧月份", 2)]
            if not major or not months:
                continue
            result = await db.execute(
                select(AssetDepreciationRule).where(
                    AssetDepreciationRule.major_category == major,
                    AssetDepreciationRule.minor_category == minor,
                )
            )
            rule = result.scalar_one_or_none()
            if not rule:
                rule = AssetDepreciationRule(
                    major_category=major,
                    minor_category=minor,
                    depreciation_months=int(months),
                )
                db.add(rule)
                imported_rules += 1
            else:
                rule.depreciation_months = int(months)
            residual_rate_idx = rule_idx.get("残值率")
            if residual_rate_idx is not None and row[residual_rate_idx] not in (None, ""):
                rule.residual_rate = float(row[residual_rate_idx])
            remark_idx = rule_idx.get("备注")
            if remark_idx is not None:
                rule.remark = str(row[remark_idx] or "").strip() or None

    if "固定资产" not in wb.sheetnames:
        raise HTTPException(status_code=400, detail="Excel 中缺少“固定资产”sheet")

    ws = wb["固定资产"]
    header_row = next(ws.iter_rows(min_row=2, max_row=2, values_only=True))
    idx = {str(h).strip(): i for i, h in enumerate(header_row) if h is not None}
    for row_no, row in enumerate(ws.iter_rows(min_row=3, values_only=True), start=3):
        if not any(row):
            continue
        try:
            async with db.begin_nested():
                asset_no = _clean_str(row[idx.get("资产编号", 0)], 50)
                if not asset_no:
                    errors.append(f"第 {row_no} 行缺少资产编号")
                    continue
                result = await db.execute(select(Asset).where(Asset.asset_no == asset_no))
                asset = result.scalar_one_or_none()
                is_new = asset is None
                if is_new:
                    asset = Asset(asset_no=asset_no, name=asset_no, category="未分类")
                    db.add(asset)

                def pick(name: str):
                    col = idx.get(name)
                    return row[col] if col is not None and col < len(row) else None

                def pick_date(name: str):
                    value = pick(name)
                    if isinstance(value, str):
                        text = value.strip()
                        if not text:
                            return None
                        try:
                            return date.fromisoformat(text)
                        except ValueError:
                            for fmt in ("%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S"):
                                try:
                                    return datetime.strptime(text, fmt).date()
                                except ValueError:
                                    continue
                            return value
                    if hasattr(value, "date"):
                        return value.date()
                    return value

                description = _clean_str(pick("资产说明"))
                major_category = _clean_str(pick("资产大类"), 50)
                minor_category = _clean_str(pick("资产小类"), 50)
                short_name = _clean_str(pick("资产说明"), 100) or minor_category or major_category or asset_no

                asset.major_category = major_category
                asset.minor_category = minor_category
                asset.category = minor_category or major_category or asset.category
                asset.asset_description = description
                asset.name = short_name
                asset.supplier = _clean_str(pick("供应商"), 100)
                asset.purchase_price = float(pick("购买金额")) if pick("购买金额") not in (None, "") else None
                asset.tax_rate = float(pick("税率")) if pick("税率") not in (None, "") else None
                asset.amount_excluding_tax = float(pick("不含税金额")) if pick("不含税金额") not in (None, "") else None
                asset.quantity = int(pick("数量") or 1)
                asset.unit = _clean_str(pick("单位"), 20)
                asset.acceptance_date = pick_date("验收日期")
                asset.custodian = _clean_str(pick("保管人"), 50)
                asset.usage_department = _clean_str(pick("所属部门"), 100)
                asset.storage_location = _clean_str(pick("存放地点"), 100)
                asset.depreciation_months = int(pick("折旧时间")) if pick("折旧时间") not in (None, "") else asset.depreciation_months
                asset.residual_value = float(pick("残值")) if pick("残值") not in (None, "") else asset.residual_value
                asset.monthly_depreciation_amount = float(pick("每月折旧金额")) if pick("每月折旧金额") not in (None, "") else asset.monthly_depreciation_amount
                asset.accumulated_depreciation_months = int(pick("累计折旧月份") or 0)
                asset.remaining_depreciation_months = int(pick("剩余折旧月份")) if pick("剩余折旧月份") not in (None, "") else asset.remaining_depreciation_months
                asset.accumulated_depreciation_amount = float(pick("累计折旧金额")) if pick("累计折旧金额") not in (None, "") else asset.accumulated_depreciation_amount
                asset.remaining_depreciation_amount = float(pick("剩余折旧金额")) if pick("剩余折旧金额") not in (None, "") else asset.remaining_depreciation_amount
                asset.current_month_depreciation_amount = float(pick("当月折旧金额")) if pick("当月折旧金额") not in (None, "") else asset.current_month_depreciation_amount
                asset.previous_month_accumulated_depreciation = float(pick("上月累计折旧")) if pick("上月累计折旧") not in (None, "") else asset.previous_month_accumulated_depreciation
                asset.dongguan_ledger_no = _clean_str(pick("东莞行政台账编号"), 100)
                asset.matched_ledger_code = _clean_str(pick("匹配行政台账最新编码"), 100)
                asset.matched_ledger_department = _clean_str(pick("匹配行政台账最新部门"), 100)
                asset.usage_employee_name_manual = _clean_str(pick("使用人"), 50)
                asset.purchase_date = pick_date("购买日期") if idx.get("购买日期") is not None else asset.purchase_date
                asset.scrap_date = pick_date("报废时间") if idx.get("报废时间") is not None else asset.scrap_date
                asset.status = _normalize_asset_status(pick("状态\n(有报废)"))
                asset.inventory_loss_flag = str(pick("盘亏") or "").strip() in {"是", "true", "True", "1"}
                await _sync_asset_financial_fields(db, asset)
                await _sync_asset_checkout_from_holder(
                    db,
                    asset=asset,
                    holder_name=asset.custodian or pick("使用人"),
                    usage_department=asset.usage_department,
                    checkout_date=asset.acceptance_date or asset.purchase_date,
                )
                await db.flush()

                if is_new:
                    imported_assets += 1
                else:
                    updated_assets += 1
        except Exception as exc:
            errors.append(f"第 {row_no} 行导入失败: {exc}")

    return AssetImportResult(
        imported_assets=imported_assets,
        imported_rules=imported_rules,
        updated_assets=updated_assets,
        errors=errors,
    )


async def create_tool_item(db: AsyncSession, data: ToolItemCreate) -> ToolItem:
    tool = ToolItem(**data.model_dump())
    db.add(tool)
    await db.flush()
    await db.refresh(tool)
    return tool


async def get_tool_item(db: AsyncSession, tool_id: int) -> ToolItem:
    tool = await db.get(ToolItem, tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    return tool


async def list_tool_items(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    keyword: Optional[str] = None,
    usage_department: Optional[str] = None,
    is_in_use: Optional[bool] = None,
) -> tuple[int, list[ToolItem]]:
    stmt = select(ToolItem)
    if keyword:
        stmt = stmt.where(
            or_(
                ToolItem.tool_no.ilike(f"%{keyword}%"),
                ToolItem.name.ilike(f"%{keyword}%"),
                ToolItem.custodian.ilike(f"%{keyword}%"),
            )
        )
    if usage_department:
        stmt = stmt.where(ToolItem.usage_department == usage_department)
    if is_in_use is not None:
        stmt = stmt.where(ToolItem.is_in_use == is_in_use)

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(
        stmt.order_by(ToolItem.id.desc()).offset(skip).limit(limit)
    )
    return total, list(result.scalars().all())


async def update_tool_item(db: AsyncSession, tool_id: int, data: ToolItemUpdate) -> ToolItem:
    tool = await get_tool_item(db, tool_id)
    payload = data.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(tool, field, value)
    await db.flush()
    await db.refresh(tool)
    return tool


async def delete_tool_item(db: AsyncSession, tool_id: int) -> None:
    tool = await get_tool_item(db, tool_id)
    await db.delete(tool)
    await db.flush()


async def export_tool_items_excel(
    db: AsyncSession,
    keyword: Optional[str] = None,
    usage_department: Optional[str] = None,
    is_in_use: Optional[bool] = None,
) -> bytes:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    _, items = await list_tool_items(
        db,
        skip=0,
        limit=10000,
        keyword=keyword,
        usage_department=usage_department,
        is_in_use=is_in_use,
    )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "工具管理"
    ws.append(["报告日期：", datetime.now(timezone.utc).astimezone().date()])
    headers = [
        "工具编号",
        "使用部门",
        "工具名称",
        "工具说明",
        "品牌",
        "购买金额",
        "验收日期",
        "保管人",
        "是否使用",
        "废弃时间",
        "废弃原因",
    ]
    ws.append(headers)

    header_fill = PatternFill("solid", fgColor="4F46E5")
    header_font = Font(color="FFFFFF", bold=True)
    center = Alignment(horizontal="center", vertical="center")
    for column in range(1, len(headers) + 1):
        cell = ws.cell(row=2, column=column)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center

    for item in items:
        ws.append(
            [
                item.tool_no,
                item.usage_department,
                item.name,
                item.description,
                item.brand,
                item.purchase_price,
                item.acceptance_date,
                item.custodian,
                "是" if item.is_in_use else "否",
                item.discard_date,
                item.discard_reason,
            ]
        )

    widths = [18, 18, 18, 42, 18, 14, 14, 14, 10, 14, 28]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.alignment = Alignment(vertical="center")

    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


# ───────────────────── Visitor（访客管理）─────────────────────


async def create_visitor(db: AsyncSession, data: VisitorCreate) -> Visitor:
    """
    新建访客预约记录（初始状态通常为 pending 待审）。

    参数：
        db   -- 异步数据库会话
        data -- VisitorCreate schema，包含访客姓名、来访公司、来访目的、
                预约到访时间、接待人（host_employee_id）等

    返回：
        新建的 Visitor ORM 对象
    """
    visitor = Visitor(**data.model_dump())
    db.add(visitor)
    await db.flush()
    await db.refresh(visitor)
    return visitor


async def get_visitor(db: AsyncSession, visitor_id: int) -> Visitor:
    """
    按 ID 查询访客记录。

    参数：
        db         -- 异步数据库会话
        visitor_id -- 访客记录主键 ID

    返回：
        Visitor ORM 对象

    异常：
        HTTPException 404 -- 访客记录不存在
    """
    result = await db.execute(select(Visitor).where(Visitor.id == visitor_id))
    visitor = result.scalar_one_or_none()
    if not visitor:
        raise HTTPException(status_code=404, detail="访客记录不存在")
    return visitor


async def list_visitors(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    visit_date: Optional[date] = None,
    status_filter: Optional[str] = None,
    host_employee_id: Optional[int] = None,
) -> tuple[int, list[Visitor]]:
    """
    分页查询访客列表，支持多条件过滤。

    参数：
        db               -- 异步数据库会话
        skip             -- 分页偏移量
        limit            -- 每页记录数
        visit_date       -- 按来访日期过滤（精确到天）
        status_filter    -- 按访客状态过滤（pending/approved/checked_in/checked_out/cancelled）
        host_employee_id -- 按接待人过滤（查询某员工接待的所有访客）

    返回：
        (total, visitors) 元组，按来访日期倒序 + ID 倒序排列
    """
    stmt = select(Visitor)
    if visit_date:
        stmt = stmt.where(Visitor.visit_date == visit_date)
    if status_filter:
        stmt = stmt.where(Visitor.status == status_filter)
    if host_employee_id:
        stmt = stmt.where(Visitor.host_employee_id == host_employee_id)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(Visitor.visit_date.desc(), Visitor.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def update_visitor(
    db: AsyncSession, visitor_id: int, data: VisitorUpdate
) -> Visitor:
    """
    更新访客预约信息（仅更新请求中提供的字段）。

    注意：签到和签退操作有专用方法（visitor_check_in/visitor_check_out），
         不要通过本方法直接修改 status 字段。

    参数：
        db         -- 异步数据库会话
        visitor_id -- 访客记录主键 ID
        data       -- VisitorUpdate schema

    返回：
        更新后的 Visitor ORM 对象

    异常：
        HTTPException 404 -- 访客记录不存在
    """
    visitor = await get_visitor(db, visitor_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(visitor, field, value)
    await db.flush()
    await db.refresh(visitor)
    return visitor


async def visitor_check_in(
    db: AsyncSession, visitor_id: int, data: VisitorCheckInRequest
) -> Visitor:
    """
    访客签到操作（状态机：pending/approved → checked_in）。

    业务流程：
        1. 检查访客当前状态（只有 pending 或 approved 状态可签到）
        2. 将状态更新为 checked_in
        3. 记录实际到达时间（actual_arrival = 当前时间）
        4. 可选更新身份证号（id_card_no）和临时通行证号（temp_pass_no）

    参数：
        db         -- 异步数据库会话
        visitor_id -- 访客记录主键 ID
        data       -- VisitorCheckInRequest schema，包含可选的 id_card_no、temp_pass_no

    返回：
        更新后的 Visitor ORM 对象，status="checked_in"

    异常：
        HTTPException 400 -- 当前状态不可签到（如已签到、已签退、已取消）
        HTTPException 404 -- 访客记录不存在
    """
    visitor = await get_visitor(db, visitor_id)
    if visitor.status not in ("pending", "approved"):
        raise HTTPException(status_code=400, detail="当前状态不可签到")
    visitor.status = "checked_in"
    visitor.actual_arrival = datetime.now(timezone.utc)  # 记录实际到达时间
    # 更新可选字段（如前台登记时补录）
    if data.id_card_no:
        visitor.id_card_no = data.id_card_no
    if data.temp_pass_no:
        visitor.temp_pass_no = data.temp_pass_no
    await db.flush()
    await db.refresh(visitor)
    return visitor


async def visitor_check_out(db: AsyncSession, visitor_id: int) -> Visitor:
    """
    访客签退操作（状态机：checked_in → checked_out）。

    业务流程：
        1. 验证访客当前处于 checked_in 状态（已在场）
        2. 将状态更新为 checked_out
        3. 记录实际离开时间（actual_departure = 当前时间）

    参数：
        db         -- 异步数据库会话
        visitor_id -- 访客记录主键 ID

    返回：
        更新后的 Visitor ORM 对象，status="checked_out"

    异常：
        HTTPException 400 -- 访客尚未签到（不能对未入场的访客进行签退）
        HTTPException 404 -- 访客记录不存在
    """
    visitor = await get_visitor(db, visitor_id)
    if visitor.status != "checked_in":
        raise HTTPException(status_code=400, detail="访客尚未签到")
    visitor.status = "checked_out"
    visitor.actual_departure = datetime.now(timezone.utc)  # 记录实际离开时间
    await db.flush()
    await db.refresh(visitor)
    return visitor


async def delete_visitor(db: AsyncSession, visitor_id: int) -> None:
    """
    删除访客记录（物理删除）。

    参数：
        db         -- 异步数据库会话
        visitor_id -- 访客记录主键 ID

    异常：
        HTTPException 404 -- 访客记录不存在
    """
    visitor = await get_visitor(db, visitor_id)
    await db.delete(visitor)
    await db.flush()


# ───────────────────── BookingRoom（会议室预约）─────────────────────


async def create_room_booking(
    db: AsyncSession, data: BookingRoomCreate, booker_id: int
) -> BookingRoom:
    """
    预约会议室（含时间段冲突检测）。

    业务规则：
        - 同一会议室、同一日期、status="confirmed" 的预约不可时间段重叠
        - 时间段重叠定义：new_start < existing_end AND new_end > existing_start
          （即任意交叉均视为冲突，包括首尾相接不算冲突）
        - 冲突检测由内部 _check_room_conflict() 完成

    参数：
        db        -- 异步数据库会话
        data      -- BookingRoomCreate schema，包含 room_name、date、
                     start_time、end_time、title、attendees_count 等
        booker_id -- 预约人员工 ID（来自当前登录用户）

    返回：
        新建的 BookingRoom ORM 对象，status="confirmed"

    异常：
        HTTPException 409 -- 该时间段会议室已被预约（时间冲突）
    """
    # 预约前检测时间段冲突
    conflict = await _check_room_conflict(
        db, data.room_name, data.date, data.start_time, data.end_time
    )
    if conflict:
        raise HTTPException(
            status_code=409, detail="该时间段会议室已被预约"
        )
    booking = BookingRoom(**data.model_dump(), booker_id=booker_id)
    db.add(booking)
    await db.flush()
    await db.refresh(booking)
    return booking


async def _check_room_conflict(
    db: AsyncSession,
    room_name: str,
    booking_date: date,
    start_time,
    end_time,
    exclude_id: Optional[int] = None,
) -> bool:
    """
    检查会议室指定时间段是否存在冲突（内部方法）。

    冲突条件：
        同一会议室（room_name）、同一日期（date）、status="confirmed" 且
        existing_start < new_end AND existing_end > new_start
        （等价于：两个时间段有任何交叉）

    参数：
        db           -- 异步数据库会话
        room_name    -- 会议室名称
        booking_date -- 预约日期
        start_time   -- 新预约开始时间
        end_time     -- 新预约结束时间
        exclude_id   -- 更新时排除自身 ID（避免将当前记录视为冲突）

    返回：
        True  -- 存在冲突
        False -- 无冲突，可预约
    """
    stmt = select(BookingRoom).where(
        BookingRoom.room_name == room_name,
        BookingRoom.date == booking_date,
        BookingRoom.status == "confirmed",  # 只与已确认的预约比较（已取消的不算冲突）
        BookingRoom.start_time < end_time,  # 现有预约在新预约结束前开始
        BookingRoom.end_time > start_time,  # 现有预约在新预约开始后结束
    )
    if exclude_id:
        stmt = stmt.where(BookingRoom.id != exclude_id)  # 更新时排除自身
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_room_booking(db: AsyncSession, booking_id: int) -> BookingRoom:
    """
    按 ID 查询会议室预约记录。

    参数：
        db         -- 异步数据库会话
        booking_id -- 预约记录主键 ID

    返回：
        BookingRoom ORM 对象

    异常：
        HTTPException 404 -- 会议室预约不存在
    """
    result = await db.execute(
        select(BookingRoom).where(BookingRoom.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="会议室预约不存在")
    return booking


async def list_room_bookings(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    room_name: Optional[str] = None,
    booking_date: Optional[date] = None,
    booker_id: Optional[int] = None,
) -> tuple[int, list[BookingRoom]]:
    """
    分页查询会议室预约列表，支持多条件过滤。

    参数：
        db           -- 异步数据库会话
        skip         -- 分页偏移量
        limit        -- 每页记录数
        room_name    -- 按会议室名称过滤
        booking_date -- 按预约日期过滤（精确到天）
        booker_id    -- 按预约人过滤（查询某员工的所有预约）

    返回：
        (total, bookings) 元组，按预约日期倒序 + 开始时间正序排列
    """
    stmt = select(BookingRoom)
    if room_name:
        stmt = stmt.where(BookingRoom.room_name == room_name)
    if booking_date:
        stmt = stmt.where(BookingRoom.date == booking_date)
    if booker_id:
        stmt = stmt.where(BookingRoom.booker_id == booker_id)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(BookingRoom.date.desc(), BookingRoom.start_time)
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def update_room_booking(
    db: AsyncSession, booking_id: int, data: BookingRoomUpdate
) -> BookingRoom:
    """
    更新会议室预约信息（若时间相关字段变更则重新做冲突检测）。

    冲突检测触发条件：
        当更新数据中包含 date、start_time、end_time 任意一个字段时，
        重新执行冲突检测（使用更新后的新值），并在检测时排除自身 ID。

    参数：
        db         -- 异步数据库会话
        booking_id -- 预约记录主键 ID
        data       -- BookingRoomUpdate schema

    返回：
        更新后的 BookingRoom ORM 对象

    异常：
        HTTPException 409 -- 更新后的时间段与已有预约冲突
        HTTPException 404 -- 预约不存在
    """
    booking = await get_room_booking(db, booking_id)
    update_data = data.model_dump(exclude_unset=True)

    # 若时间相关字段有变化，用新值（或当前值）重新做冲突检测
    new_date = update_data.get("date", booking.date)
    new_start = update_data.get("start_time", booking.start_time)
    new_end = update_data.get("end_time", booking.end_time)
    if any(k in update_data for k in ("date", "start_time", "end_time")):
        conflict = await _check_room_conflict(
            db, booking.room_name, new_date, new_start, new_end,
            exclude_id=booking.id,  # 排除自身，避免误报
        )
        if conflict:
            raise HTTPException(
                status_code=409, detail="更新后的时间段与已有预约冲突"
            )

    for field, value in update_data.items():
        setattr(booking, field, value)
    await db.flush()
    await db.refresh(booking)
    return booking


async def cancel_room_booking(db: AsyncSession, booking_id: int) -> BookingRoom:
    """
    取消会议室预约（status 改为 cancelled，不物理删除记录）。

    参数：
        db         -- 异步数据库会话
        booking_id -- 预约记录主键 ID

    返回：
        更新后的 BookingRoom ORM 对象，status="cancelled"

    异常：
        HTTPException 404 -- 预约不存在
    """
    booking = await get_room_booking(db, booking_id)
    booking.status = "cancelled"
    await db.flush()
    await db.refresh(booking)
    return booking


async def check_room_availability(
    db: AsyncSession, room_name: str, query_date: date
) -> list[BookingRoom]:
    """
    查询指定日期某会议室的所有已确认预约（用于日历视图/空闲时段展示）。

    参数：
        db         -- 异步数据库会话
        room_name  -- 会议室名称
        query_date -- 查询日期

    返回：
        该日期所有 status="confirmed" 的预约列表，按开始时间正序排列
    """
    result = await db.execute(
        select(BookingRoom).where(
            BookingRoom.room_name == room_name,
            BookingRoom.date == query_date,
            BookingRoom.status == "confirmed",  # 只返回有效预约（已取消的不显示）
        ).order_by(BookingRoom.start_time)  # 按时间顺序排列，方便前端展示时间轴
    )
    return list(result.scalars().all())


# ───────────────────── BookingVehicle（用车申请）─────────────────────


async def create_vehicle_booking(
    db: AsyncSession, data: BookingVehicleCreate, applicant_id: int
) -> BookingVehicle:
    """
    新建用车申请（初始状态为 pending 待审批）。

    参数：
        db           -- 异步数据库会话
        data         -- BookingVehicleCreate schema，包含用车日期、起止时间、
                        目的地、用车事由、预计人数等
        applicant_id -- 申请人员工 ID（来自当前登录用户）

    返回：
        新建的 BookingVehicle ORM 对象，approval_status="pending"
    """
    booking = BookingVehicle(**data.model_dump(), applicant_id=applicant_id)
    db.add(booking)
    await db.flush()
    await db.refresh(booking)
    return booking


async def get_vehicle_booking(db: AsyncSession, booking_id: int) -> BookingVehicle:
    """
    按 ID 查询用车申请记录。

    参数：
        db         -- 异步数据库会话
        booking_id -- 用车申请主键 ID

    返回：
        BookingVehicle ORM 对象

    异常：
        HTTPException 404 -- 用车申请不存在
    """
    result = await db.execute(
        select(BookingVehicle).where(BookingVehicle.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if not booking:
        raise HTTPException(status_code=404, detail="用车申请不存在")
    return booking


async def list_vehicle_bookings(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    applicant_id: Optional[int] = None,
    approval_status: Optional[str] = None,
    booking_date: Optional[date] = None,
) -> tuple[int, list[BookingVehicle]]:
    """
    分页查询用车申请列表，支持多条件过滤。

    参数：
        db              -- 异步数据库会话
        skip            -- 分页偏移量
        limit           -- 每页记录数
        applicant_id    -- 按申请人过滤（查询某员工的所有用车申请）
        approval_status -- 按审批状态过滤（pending/approved/rejected）
        booking_date    -- 按用车日期过滤（精确到天）

    返回：
        (total, bookings) 元组，按用车日期倒序 + 开始时间正序排列
    """
    stmt = select(BookingVehicle)
    if applicant_id:
        stmt = stmt.where(BookingVehicle.applicant_id == applicant_id)
    if approval_status:
        stmt = stmt.where(BookingVehicle.approval_status == approval_status)
    if booking_date:
        stmt = stmt.where(BookingVehicle.date == booking_date)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        stmt.order_by(BookingVehicle.date.desc(), BookingVehicle.start_time)
        .offset(skip)
        .limit(limit)
    )
    return total, list(result.scalars().all())


async def update_vehicle_booking(
    db: AsyncSession, booking_id: int, data: BookingVehicleUpdate
) -> BookingVehicle:
    """
    更新用车申请信息（仅 pending 状态的申请可修改）。

    业务规则：
        - 已审批（approved/rejected）的申请不可修改，防止篡改已审批内容

    参数：
        db         -- 异步数据库会话
        booking_id -- 用车申请主键 ID
        data       -- BookingVehicleUpdate schema

    返回：
        更新后的 BookingVehicle ORM 对象

    异常：
        HTTPException 400 -- 已审批的用车申请不可修改
        HTTPException 404 -- 用车申请不存在
    """
    booking = await get_vehicle_booking(db, booking_id)
    if booking.approval_status != "pending":
        raise HTTPException(
            status_code=400, detail="已审批的用车申请不可修改"
        )
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(booking, field, value)
    await db.flush()
    await db.refresh(booking)
    return booking


async def approve_vehicle_booking(
    db: AsyncSession, booking_id: int, data: BookingVehicleApproval
) -> BookingVehicle:
    """
    审批用车申请（批准或拒绝）。

    业务规则：
        - 只有 pending 状态的申请可以审批（防止重复审批）
        - approval_status 只能设置为 approved 或 rejected
        - 批准时可选择指定司机（driver_assigned 字段）

    参数：
        db         -- 异步数据库会话
        booking_id -- 用车申请主键 ID
        data       -- BookingVehicleApproval schema，包含：
                      approval_status（approved/rejected）、
                      driver_assigned（可选，批准时分配的司机姓名）

    返回：
        审批后的 BookingVehicle ORM 对象

    异常：
        HTTPException 400 -- 该申请已处理（非 pending 状态）
        HTTPException 400 -- approval_status 值不合法
        HTTPException 404 -- 用车申请不存在
    """
    booking = await get_vehicle_booking(db, booking_id)
    if booking.approval_status != "pending":
        raise HTTPException(status_code=400, detail="该申请已处理")
    # 校验审批状态值合法性
    if data.approval_status not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400, detail="审批状态只能为 approved 或 rejected"
        )
    booking.approval_status = data.approval_status
    if data.driver_assigned:
        booking.driver_assigned = data.driver_assigned  # 批准时可分配司机
    await db.flush()
    await db.refresh(booking)
    return booking


async def delete_vehicle_booking(db: AsyncSession, booking_id: int) -> None:
    """
    删除用车申请（物理删除）。

    参数：
        db         -- 异步数据库会话
        booking_id -- 用车申请主键 ID

    异常：
        HTTPException 404 -- 用车申请不存在
    """
    booking = await get_vehicle_booking(db, booking_id)
    await db.delete(booking)
    await db.flush()
