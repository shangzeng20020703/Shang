"""
行政管理综合 API 路由模块 — admin.py

本模块是行政管理域的综合路由文件，将以下五个子模块的所有端点集中注册在同一个
APIRouter 上，前缀由 router.py 统一挂载为 /api/v1/admin（或直接作为子路由使用）。

子模块及对应路径前缀：
  - 公告管理  (Announcements)  : /announcements
  - 资产管理  (Assets)          : /assets、/assets/checkout
  - 访客管理  (Visitors)        : /visitors
  - 会议室预约(Room Booking)    : /rooms/book、/rooms/bookings、/rooms/availability
  - 用车管理  (Vehicle Booking) : /vehicles/book、/vehicles/bookings

注意：本文件与 admin_announcements.py / admin_assets.py / admin_rooms.py /
admin_vehicles.py / admin_visitors.py 功能完全对应，区别在于本文件将所有子路由
直接平铺在同一个 APIRouter 实例中，便于作为单一路由挂载；子文件则各自独立，
由 router.py 以不同前缀分别挂载（二者只选其一注册，避免重复）。

认证与权限：
  所有端点均通过 Depends(get_current_user) 验证 JWT Token，要求用户处于激活状态。
  本模块暂不区分角色（admin/hr/manager），任何已登录用户均可操作。
  如需 RBAC 细分，可在各路由中替换为 Depends(require_roles("admin", "hr"))。

对应 Service：app.services.admin（以 svc 导入）
对应 Schema ：app.schemas.admin
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.employee import Employee
from app.schemas.admin import (
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementPage,
    AnnouncementUpdate,
    AssetCheckoutCreate,
    AssetCheckoutOut,
    AssetCreate,
    AssetOut,
    AssetPage,
    AssetReturnRequest,
    AssetUpdate,
    BookingRoomCreate,
    BookingRoomOut,
    BookingRoomPage,
    BookingRoomUpdate,
    BookingVehicleApproval,
    BookingVehicleCreate,
    BookingVehicleOut,
    BookingVehiclePage,
    BookingVehicleUpdate,
    VisitorCheckInRequest,
    VisitorCreate,
    VisitorOut,
    VisitorPage,
    VisitorUpdate,
)
from app.services import admin as svc

# 行政管理综合路由，tags 用于 OpenAPI 文档分组
router = APIRouter(tags=["行政管理"])


# ───────────────────── Announcements — 公告管理 ─────────────────────
# 公告状态机：draft（草稿）→ published（已发布）
# 公告对象：可面向全体员工、指定部门或指定个人（由 target_type + target_ids 控制）


@router.post("/announcements", response_model=AnnouncementOut, status_code=201)
async def create_announcement(
    data: AnnouncementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP POST /announcements
    用途：创建一条新公告，初始状态为草稿（draft），不立即对员工可见。

    请求体 (AnnouncementCreate)：
      - title       : str      — 公告标题（必填）
      - content     : str      — 公告正文（必填）
      - category    : str      — 公告类别（如：通知/规章/活动）（可选）
      - target_type : str      — 目标范围：all（全体）/department（部门）/individual（个人）
      - target_ids  : list[int]— 目标 ID 列表；target_type=all 时可为空

    响应 (AnnouncementOut, HTTP 201)：
      - 包含新建公告的完整信息，含自动生成的 id、publisher_id、created_at

    权限：需登录（get_current_user），发布人 publisher_id 自动取 current_user.id
    Service：svc.create_announcement(db, data, publisher_id)
    """
    ann = await svc.create_announcement(db, data, publisher_id=current_user.id)
    return AnnouncementOut.model_validate(ann)


@router.get("/announcements", response_model=AnnouncementPage)
async def list_announcements(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None, description="按状态筛选"),
    category: Optional[str] = Query(None, description="按类别筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementPage:
    """
    HTTP GET /announcements
    用途：分页查询公告列表，支持按状态和类别筛选。

    Query 参数：
      - skip     : int（默认 0）      — 分页偏移量
      - limit    : int（默认 20，上限 1000）— 每页条数
      - status   : str（可选）         — 筛选状态，如 draft / published / archived
      - category : str（可选）         — 筛选类别，如 通知 / 规章

    响应 (AnnouncementPage, HTTP 200)：
      - total : int              — 满足条件的公告总数
      - items : list[AnnouncementOut] — 当页公告列表

    权限：需登录
    Service：svc.list_announcements(db, skip, limit, status_filter, category)
    """
    total, items = await svc.list_announcements(
        db, skip=skip, limit=limit, status_filter=status, category=category
    )
    return AnnouncementPage(
        total=total,
        items=[AnnouncementOut.model_validate(a) for a in items],
    )


@router.get("/announcements/{announcement_id}", response_model=AnnouncementOut)
async def get_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP GET /announcements/{announcement_id}
    用途：获取指定公告的完整详情。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应 (AnnouncementOut, HTTP 200)：完整公告信息
    错误：公告不存在时抛出 404

    权限：需登录
    Service：svc.get_announcement(db, announcement_id)
    """
    ann = await svc.get_announcement(db, announcement_id)
    return AnnouncementOut.model_validate(ann)


@router.put("/announcements/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(
    announcement_id: int,
    data: AnnouncementUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP PUT /announcements/{announcement_id}
    用途：更新公告内容或元数据（仅草稿状态可编辑，已发布公告建议限制修改）。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    请求体 (AnnouncementUpdate)：可部分更新（Pydantic v2 partial）
      - title / content / category / target_type / target_ids 均可选填

    响应 (AnnouncementOut, HTTP 200)：更新后的公告信息
    错误：公告不存在时抛出 404

    权限：需登录（生产环境建议限制为发布人本人或 admin/hr 角色）
    Service：svc.update_announcement(db, announcement_id, data)
    """
    ann = await svc.update_announcement(db, announcement_id, data)
    return AnnouncementOut.model_validate(ann)


@router.post(
    "/announcements/{announcement_id}/publish",
    response_model=AnnouncementOut,
)
async def publish_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP POST /announcements/{announcement_id}/publish
    用途：将草稿公告正式发布，状态由 draft 变更为 published，员工即可看到。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应 (AnnouncementOut, HTTP 200)：发布后的公告信息（status=published）
    错误：公告不存在抛出 404；已发布/已归档时抛出 400

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.publish_announcement(db, announcement_id)
    """
    ann = await svc.publish_announcement(db, announcement_id)
    return AnnouncementOut.model_validate(ann)


@router.post(
    "/announcements/{announcement_id}/read",
    response_model=AnnouncementOut,
)
async def read_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AnnouncementOut:
    """
    HTTP POST /announcements/{announcement_id}/read
    用途：员工阅读公告后调用此接口，将公告的已读计数 (read_count) 加 1。
          用于统计公告触达率，不做幂等控制（同一用户多次调用会多次计数）。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应 (AnnouncementOut, HTTP 200)：含更新后 read_count 的公告信息
    错误：公告不存在时抛出 404

    权限：需登录
    Service：svc.increment_read_count(db, announcement_id)
    """
    ann = await svc.increment_read_count(db, announcement_id)
    return AnnouncementOut.model_validate(ann)


@router.delete("/announcements/{announcement_id}", status_code=204)
async def delete_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    HTTP DELETE /announcements/{announcement_id}
    用途：物理删除指定公告（慎用，建议对已发布公告改为归档操作）。

    Path 参数：
      - announcement_id : int — 公告主键 ID

    响应：HTTP 204 No Content（无响应体）
    错误：公告不存在时抛出 404

    权限：需登录（建议限制为 admin 角色）
    Service：svc.delete_announcement(db, announcement_id)
    """
    await svc.delete_announcement(db, announcement_id)


# ───────────────────── Assets — 资产管理 ─────────────────────
# 资产状态机：in_stock（在库）→ in_use（使用中）→ under_maintenance（维修中）→ scrapped（报废）
# 领用操作将资产从 in_stock 变为 in_use，归还将其从 in_use 变为 in_stock。


@router.post("/assets", response_model=AssetOut, status_code=201)
async def create_asset(
    data: AssetCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AssetOut:
    """
    HTTP POST /assets
    用途：录入新资产，初始状态为 in_stock（在库）。

    请求体 (AssetCreate)：
      - name          : str       — 资产名称（必填），如"联想笔记本 X1"
      - asset_code    : str       — 资产编码（必填，唯一）
      - category      : str       — 资产类别（必填），如 IT设备/办公家具/车辆
      - location_id   : int       — 所在地点 ID（可选，关联 organizations.Location）
      - purchase_date : date      — 采购日期（可选）
      - purchase_price: Decimal   — 采购价格（可选）
      - description   : str       — 备注说明（可选）

    响应 (AssetOut, HTTP 201)：包含新建资产的完整信息
    错误：asset_code 重复时抛出 409

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.create_asset(db, data)
    """
    asset = await svc.create_asset(db, data)
    return AssetOut.model_validate(asset)


@router.get("/assets", response_model=AssetPage)
async def list_assets(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None, description="按状态筛选"),
    category: Optional[str] = Query(None, description="按类别筛选"),
    location_id: Optional[int] = Query(None, description="按地点筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AssetPage:
    """
    HTTP GET /assets
    用途：分页查询资产台账，支持按状态、类别、所在地点筛选。

    Query 参数：
      - skip        : int（默认 0）         — 分页偏移量
      - limit       : int（默认 20，上限 1000）— 每页条数
      - status      : str（可选）            — 筛选资产状态：in_stock/in_use/under_maintenance/scrapped
      - category    : str（可选）            — 筛选资产类别
      - location_id : int（可选）            — 筛选所在地点 ID

    响应 (AssetPage, HTTP 200)：
      - total : int          — 满足条件的资产总数
      - items : list[AssetOut] — 当页资产列表

    权限：需登录
    Service：svc.list_assets(db, skip, limit, status_filter, category, location_id)
    """
    total, items = await svc.list_assets(
        db,
        skip=skip,
        limit=limit,
        status_filter=status,
        category=category,
        location_id=location_id,
    )
    return AssetPage(
        total=total,
        items=[AssetOut.model_validate(a) for a in items],
    )


@router.get("/assets/{asset_id}", response_model=AssetOut)
async def get_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AssetOut:
    """
    HTTP GET /assets/{asset_id}
    用途：获取指定资产的完整详情，包含当前持有人（若已领用）信息。

    Path 参数：
      - asset_id : int — 资产主键 ID

    响应 (AssetOut, HTTP 200)：完整资产信息
    错误：资产不存在时抛出 404

    权限：需登录
    Service：svc.get_asset(db, asset_id)
    """
    asset = await svc.get_asset(db, asset_id)
    return AssetOut.model_validate(asset)


@router.put("/assets/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: int,
    data: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AssetOut:
    """
    HTTP PUT /assets/{asset_id}
    用途：更新资产基本信息或状态（如手动标记为维修中/报废）。

    Path 参数：
      - asset_id : int — 资产主键 ID

    请求体 (AssetUpdate)：可部分更新
      - name / category / location_id / status / description 等均可选填
      - 直接修改 status 可用于手动推进状态机（如标记报废）

    响应 (AssetOut, HTTP 200)：更新后的资产信息
    错误：资产不存在时抛出 404

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.update_asset(db, asset_id, data)
    """
    asset = await svc.update_asset(db, asset_id, data)
    return AssetOut.model_validate(asset)


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    HTTP DELETE /assets/{asset_id}
    用途：物理删除指定资产记录（建议仅对未领用的在库资产执行，已领用资产应先归还）。

    Path 参数：
      - asset_id : int — 资产主键 ID

    响应：HTTP 204 No Content（无响应体）
    错误：资产不存在时抛出 404；资产仍在使用中时抛出 400

    权限：需登录（建议限制为 admin 角色）
    Service：svc.delete_asset(db, asset_id)
    """
    await svc.delete_asset(db, asset_id)


# ───────────────────── Asset Checkout — 资产领用与归还 ─────────────────────
# 领用记录（AssetCheckout）记录每次资产流出/流入，关联资产、员工、日期和备注。
# 注意路由顺序：/assets/checkout 和 /assets/checkout/employee/{id}
# 必须在 /assets/{asset_id} 之前注册，否则 FastAPI 会将 "checkout" 匹配为 asset_id。


@router.post("/assets/checkout", response_model=AssetCheckoutOut, status_code=201)
async def checkout_asset(
    data: AssetCheckoutCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AssetCheckoutOut:
    """
    HTTP POST /assets/checkout
    用途：办理资产领用（出库），将指定资产分配给指定员工，资产状态变为 in_use。

    请求体 (AssetCheckoutCreate)：
      - asset_id      : int  — 要领用的资产 ID（必填，资产需为 in_stock 状态）
      - employee_id   : int  — 领用员工 ID（必填）
      - checkout_date : date — 领用日期（必填）
      - expected_return_date : date — 预计归还日期（可选）
      - notes         : str  — 备注（可选）

    响应 (AssetCheckoutOut, HTTP 201)：新建的领用记录，含 checkout_id
    错误：资产不存在/非在库状态抛出 400；员工不存在抛出 404

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.checkout_asset(db, data)
    """
    checkout = await svc.checkout_asset(db, data)
    return AssetCheckoutOut.model_validate(checkout)


@router.post(
    "/assets/checkout/{checkout_id}/return",
    response_model=AssetCheckoutOut,
)
async def return_asset(
    checkout_id: int,
    data: AssetReturnRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> AssetCheckoutOut:
    """
    HTTP POST /assets/checkout/{checkout_id}/return
    用途：办理资产归还（入库），将资产状态从 in_use 变回 in_stock，
          并在领用记录上写入归还日期和备注。

    Path 参数：
      - checkout_id : int — 领用记录主键 ID

    请求体 (AssetReturnRequest)：
      - return_date : date — 实际归还日期（必填）
      - condition   : str  — 归还时资产状况（可选），如 良好/损坏
      - notes       : str  — 归还备注（可选）

    响应 (AssetCheckoutOut, HTTP 200)：更新后的领用记录（含归还信息）
    错误：领用记录不存在/已归还时抛出 404/400

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.return_asset(db, checkout_id, data)
    """
    checkout = await svc.return_asset(db, checkout_id, data)
    return AssetCheckoutOut.model_validate(checkout)


@router.get(
    "/assets/checkout/employee/{employee_id}",
    response_model=list[AssetCheckoutOut],
)
async def list_employee_checkouts(
    employee_id: int,
    active_only: bool = Query(False, description="仅显示在用资产"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> list[AssetCheckoutOut]:
    """
    HTTP GET /assets/checkout/employee/{employee_id}
    用途：查询指定员工的所有资产领用记录，离职交接时可用于核查未归还资产。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Query 参数：
      - active_only : bool（默认 false）— 为 true 时只返回尚未归还（仍在使用中）的领用记录

    响应 (list[AssetCheckoutOut], HTTP 200)：该员工的领用记录列表
    错误：员工不存在时抛出 404

    权限：需登录（员工可查自己，HR/admin 可查所有人）
    Service：svc.list_checkouts_by_employee(db, employee_id, active_only)
    """
    checkouts = await svc.list_checkouts_by_employee(
        db, employee_id, active_only=active_only
    )
    return [AssetCheckoutOut.model_validate(c) for c in checkouts]


# ───────────────────── Visitors — 访客管理 ─────────────────────
# 访客状态机（5个状态）：
#   pending（待确认）→ confirmed（已确认）→ checked_in（已签到）
#   → checked_out（已离开）
#   任意状态 → cancelled（已取消）
# 访客预约由员工（接待人 host_employee_id）发起，前台负责签到/签退操作。


@router.post("/visitors", response_model=VisitorOut, status_code=201)
async def create_visitor(
    data: VisitorCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP POST /visitors
    用途：预约登记访客来访信息，初始状态为 pending（待确认）。

    请求体 (VisitorCreate)：
      - visitor_name      : str  — 访客姓名（必填）
      - visitor_phone     : str  — 访客手机号（必填）
      - visitor_company   : str  — 访客所在公司（可选）
      - host_employee_id  : int  — 接待人（内部员工）ID（必填）
      - visit_date        : date — 预约来访日期（必填）
      - visit_purpose     : str  — 来访事由（可选）
      - expected_duration : int  — 预计停留时长（分钟，可选）

    响应 (VisitorOut, HTTP 201)：新建的访客预约记录
    错误：接待人员工不存在时抛出 404

    权限：需登录
    Service：svc.create_visitor(db, data)
    """
    visitor = await svc.create_visitor(db, data)
    return VisitorOut.model_validate(visitor)


@router.get("/visitors", response_model=VisitorPage)
async def list_visitors(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    visit_date: Optional[date] = Query(None, description="来访日期"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    host_employee_id: Optional[int] = Query(None, description="接待人ID"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorPage:
    """
    HTTP GET /visitors
    用途：分页查询访客预约记录，支持按来访日期、状态、接待人筛选。

    Query 参数：
      - skip             : int（默认 0）         — 分页偏移量
      - limit            : int（默认 20，上限 1000）— 每页条数
      - visit_date       : date（可选）           — 筛选特定来访日期
      - status           : str（可选）            — 筛选状态：pending/confirmed/checked_in/checked_out/cancelled
      - host_employee_id : int（可选）            — 筛选指定接待人的访客记录

    响应 (VisitorPage, HTTP 200)：
      - total : int           — 满足条件的访客记录总数
      - items : list[VisitorOut] — 当页访客记录

    权限：需登录
    Service：svc.list_visitors(db, skip, limit, visit_date, status_filter, host_employee_id)
    """
    total, items = await svc.list_visitors(
        db,
        skip=skip,
        limit=limit,
        visit_date=visit_date,
        status_filter=status,
        host_employee_id=host_employee_id,
    )
    return VisitorPage(
        total=total,
        items=[VisitorOut.model_validate(v) for v in items],
    )


@router.get("/visitors/{visitor_id}", response_model=VisitorOut)
async def get_visitor(
    visitor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP GET /visitors/{visitor_id}
    用途：获取指定访客预约记录的完整详情。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    响应 (VisitorOut, HTTP 200)：完整访客记录
    错误：记录不存在时抛出 404

    权限：需登录
    Service：svc.get_visitor(db, visitor_id)
    """
    visitor = await svc.get_visitor(db, visitor_id)
    return VisitorOut.model_validate(visitor)


@router.put("/visitors/{visitor_id}", response_model=VisitorOut)
async def update_visitor(
    visitor_id: int,
    data: VisitorUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP PUT /visitors/{visitor_id}
    用途：更新访客预约信息（如调整来访日期、事由等），建议仅在 pending/confirmed 状态时允许修改。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    请求体 (VisitorUpdate)：可部分更新
      - visitor_name / visit_date / visit_purpose / expected_duration 等均可选填

    响应 (VisitorOut, HTTP 200)：更新后的访客记录
    错误：记录不存在时抛出 404

    权限：需登录（建议限制为接待人本人或 admin/hr 角色）
    Service：svc.update_visitor(db, visitor_id, data)
    """
    visitor = await svc.update_visitor(db, visitor_id, data)
    return VisitorOut.model_validate(visitor)


@router.post("/visitors/{visitor_id}/check-in", response_model=VisitorOut)
async def visitor_check_in(
    visitor_id: int,
    data: VisitorCheckInRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP POST /visitors/{visitor_id}/check-in
    用途：访客到达时前台执行签到操作，状态由 confirmed 变为 checked_in，
          记录实际签到时间和证件信息。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    请求体 (VisitorCheckInRequest)：
      - check_in_time  : datetime — 签到时间（可选，默认取当前时间）
      - id_type        : str      — 证件类型（可选），如 身份证/护照
      - id_number      : str      — 证件号码（可选）
      - badge_number   : str      — 访客临时门牌/访客证号码（可选）

    响应 (VisitorOut, HTTP 200)：含签到时间的访客记录（status=checked_in）
    错误：记录不存在时抛出 404；状态不符（非 confirmed）时抛出 400

    权限：需登录（通常由前台员工操作）
    Service：svc.visitor_check_in(db, visitor_id, data)
    """
    visitor = await svc.visitor_check_in(db, visitor_id, data)
    return VisitorOut.model_validate(visitor)


@router.post("/visitors/{visitor_id}/check-out", response_model=VisitorOut)
async def visitor_check_out(
    visitor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> VisitorOut:
    """
    HTTP POST /visitors/{visitor_id}/check-out
    用途：访客离开时前台执行签退操作，状态由 checked_in 变为 checked_out，
          记录实际离开时间。无需请求体，离开时间取当前服务器时间。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    响应 (VisitorOut, HTTP 200)：含离开时间的访客记录（status=checked_out）
    错误：记录不存在时抛出 404；状态不符（非 checked_in）时抛出 400

    权限：需登录（通常由前台员工操作）
    Service：svc.visitor_check_out(db, visitor_id)
    """
    visitor = await svc.visitor_check_out(db, visitor_id)
    return VisitorOut.model_validate(visitor)


@router.delete("/visitors/{visitor_id}", status_code=204)
async def delete_visitor(
    visitor_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    HTTP DELETE /visitors/{visitor_id}
    用途：物理删除访客预约记录（通常用于清理测试数据或错误录入，已完成来访建议保留审计记录）。

    Path 参数：
      - visitor_id : int — 访客预约记录主键 ID

    响应：HTTP 204 No Content（无响应体）
    错误：记录不存在时抛出 404

    权限：需登录（建议限制为 admin 角色）
    Service：svc.delete_visitor(db, visitor_id)
    """
    await svc.delete_visitor(db, visitor_id)


# ───────────────────── Room Booking — 会议室预约 ─────────────────────
# 会议室通过 room_name 字符串标识（无独立 Room 实体表），时间冲突检测在 Service 层完成。
# 预约时段由 start_time / end_time 精确到分钟，同一会议室同日期相同时段不可重叠预约。
# 路由顺序重要：/rooms/book、/rooms/bookings、/rooms/availability
# 必须在 /rooms/bookings/{booking_id} 之前注册，避免路由冲突。


@router.post("/rooms/book", response_model=BookingRoomOut, status_code=201)
async def create_room_booking(
    data: BookingRoomCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP POST /rooms/book
    用途：预约会议室，Service 层会检测时间冲突，冲突时抛出 400。
          预约人 (booker_id) 自动取当前登录用户 ID。

    请求体 (BookingRoomCreate)：
      - room_name    : str      — 会议室名称（必填），如"A101"
      - booking_date : date     — 预约日期（必填）
      - start_time   : time     — 开始时间（必填），如 09:00
      - end_time     : time     — 结束时间（必填），如 10:30
      - title        : str      — 会议主题（必填）
      - attendees    : int      — 预计参会人数（可选）
      - description  : str      — 备注说明（可选）

    响应 (BookingRoomOut, HTTP 201)：新建的会议室预约记录，含 booking_id
    错误：时间冲突时抛出 400；会议室不存在（由 room_name 查询不到记录）时抛出 404

    权限：需登录，booker_id 自动取 current_user.id
    Service：svc.create_room_booking(db, data, booker_id)
    """
    booking = await svc.create_room_booking(db, data, booker_id=current_user.id)
    return BookingRoomOut.model_validate(booking)


@router.get("/rooms/bookings", response_model=BookingRoomPage)
async def list_room_bookings(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    room_name: Optional[str] = Query(None, description="会议室名称"),
    date: Optional[date] = Query(None, description="预约日期"),
    booker_id: Optional[int] = Query(None, description="预约人ID"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomPage:
    """
    HTTP GET /rooms/bookings
    用途：分页查询会议室预约列表，支持按会议室名称、日期、预约人筛选。

    Query 参数：
      - skip      : int（默认 0）         — 分页偏移量
      - limit     : int（默认 20，上限 1000）— 每页条数
      - room_name : str（可选）            — 按会议室名称模糊或精确筛选
      - date      : date（可选）           — 筛选特定预约日期（格式：YYYY-MM-DD）
      - booker_id : int（可选）            — 筛选指定预约人的预约记录

    响应 (BookingRoomPage, HTTP 200)：
      - total : int                — 满足条件的预约总数
      - items : list[BookingRoomOut] — 当页预约记录

    权限：需登录
    Service：svc.list_room_bookings(db, skip, limit, room_name, booking_date, booker_id)
    """
    total, items = await svc.list_room_bookings(
        db,
        skip=skip,
        limit=limit,
        room_name=room_name,
        booking_date=date,
        booker_id=booker_id,
    )
    return BookingRoomPage(
        total=total,
        items=[BookingRoomOut.model_validate(b) for b in items],
    )


@router.get("/rooms/availability", response_model=list[BookingRoomOut])
async def check_room_availability(
    room_name: str = Query(..., description="会议室名称"),
    date: date = Query(..., description="查询日期"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> list[BookingRoomOut]:
    """
    HTTP GET /rooms/availability
    用途：查询指定会议室在某天的所有已有预约（非 cancelled 状态），
          前端据此渲染时间轴，让用户直观看到哪些时段已被占用，选择空闲时段。

    Query 参数（均必填）：
      - room_name : str  — 会议室名称（必填）
      - date      : date — 查询日期（必填，格式：YYYY-MM-DD）

    响应 (list[BookingRoomOut], HTTP 200)：该会议室当天的预约列表（按开始时间排序）
    说明：返回空列表表示当天无预约，全天可用

    权限：需登录
    Service：svc.check_room_availability(db, room_name, date)
    """
    bookings = await svc.check_room_availability(db, room_name, date)
    return [BookingRoomOut.model_validate(b) for b in bookings]


@router.get("/rooms/bookings/{booking_id}", response_model=BookingRoomOut)
async def get_room_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP GET /rooms/bookings/{booking_id}
    用途：获取单条会议室预约记录的完整详情。

    Path 参数：
      - booking_id : int — 会议室预约记录主键 ID

    响应 (BookingRoomOut, HTTP 200)：完整预约信息
    错误：记录不存在时抛出 404

    权限：需登录
    Service：svc.get_room_booking(db, booking_id)
    """
    booking = await svc.get_room_booking(db, booking_id)
    return BookingRoomOut.model_validate(booking)


@router.put("/rooms/bookings/{booking_id}", response_model=BookingRoomOut)
async def update_room_booking(
    booking_id: int,
    data: BookingRoomUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP PUT /rooms/bookings/{booking_id}
    用途：更新会议室预约信息（如修改时段、主题），Service 层重新检测时间冲突。

    Path 参数：
      - booking_id : int — 会议室预约记录主键 ID

    请求体 (BookingRoomUpdate)：可部分更新
      - start_time / end_time / title / attendees / description 均可选填
      - 修改时间段时，Service 会排除本条记录后重新做冲突检测

    响应 (BookingRoomOut, HTTP 200)：更新后的预约记录
    错误：记录不存在时抛出 404；新时段与其他预约冲突时抛出 400

    权限：需登录（建议限制为预约人本人或 admin/hr 角色）
    Service：svc.update_room_booking(db, booking_id, data)
    """
    booking = await svc.update_room_booking(db, booking_id, data)
    return BookingRoomOut.model_validate(booking)


@router.post(
    "/rooms/bookings/{booking_id}/cancel",
    response_model=BookingRoomOut,
)
async def cancel_room_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingRoomOut:
    """
    HTTP POST /rooms/bookings/{booking_id}/cancel
    用途：取消会议室预约，将预约状态变更为 cancelled，释放该时段供其他人预约。
          使用 POST 而非 DELETE，以保留审计记录（软删除语义）。

    Path 参数：
      - booking_id : int — 会议室预约记录主键 ID

    响应 (BookingRoomOut, HTTP 200)：取消后的预约记录（status=cancelled）
    错误：记录不存在时抛出 404；已取消的预约重复取消时抛出 400

    权限：需登录（建议限制为预约人本人或 admin/hr 角色）
    Service：svc.cancel_room_booking(db, booking_id)
    """
    booking = await svc.cancel_room_booking(db, booking_id)
    return BookingRoomOut.model_validate(booking)


# ───────────────────── Vehicle Booking — 用车管理 ─────────────────────
# 用车申请流程：员工提交申请（pending）→ 管理员审批（approved/rejected）→ 使用完成（completed）
# approval_status 枚举值：pending / approved / rejected / completed
# 审批时可指定车辆和司机（若公司有专属车队），或仅作为用车记录（外部出行报销依据）。


@router.post("/vehicles/book", response_model=BookingVehicleOut, status_code=201)
async def create_vehicle_booking(
    data: BookingVehicleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP POST /vehicles/book
    用途：员工提交用车申请，初始审批状态为 pending，
          申请人 ID 自动取当前登录用户。

    请求体 (BookingVehicleCreate)：
      - booking_date  : date — 用车日期（必填）
      - start_time    : time — 出发时间（必填）
      - end_time      : time — 预计返回时间（可选）
      - destination   : str  — 目的地（必填）
      - purpose       : str  — 用车事由（必填）
      - passenger_count : int — 乘车人数（可选）
      - notes         : str  — 备注（可选）

    响应 (BookingVehicleOut, HTTP 201)：新建的用车申请记录，含 booking_id
    错误：参数验证失败时抛出 422

    权限：需登录，applicant_id 自动取 current_user.id
    Service：svc.create_vehicle_booking(db, data, applicant_id)
    """
    booking = await svc.create_vehicle_booking(
        db, data, applicant_id=current_user.id
    )
    return BookingVehicleOut.model_validate(booking)


@router.get("/vehicles/bookings", response_model=BookingVehiclePage)
async def list_vehicle_bookings(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    applicant_id: Optional[int] = Query(None, description="申请人ID"),
    approval_status: Optional[str] = Query(None, description="审批状态"),
    date: Optional[date] = Query(None, description="用车日期"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehiclePage:
    """
    HTTP GET /vehicles/bookings
    用途：分页查询用车申请列表，支持按申请人、审批状态、用车日期筛选。

    Query 参数：
      - skip            : int（默认 0）         — 分页偏移量
      - limit           : int（默认 20，上限 1000）— 每页条数
      - applicant_id    : int（可选）            — 筛选指定申请人的记录
      - approval_status : str（可选）            — 筛选审批状态：pending/approved/rejected/completed
      - date            : date（可选）           — 筛选特定用车日期

    响应 (BookingVehiclePage, HTTP 200)：
      - total : int                   — 满足条件的申请总数
      - items : list[BookingVehicleOut] — 当页用车申请列表

    权限：需登录（员工可查自己的，管理员可查全部）
    Service：svc.list_vehicle_bookings(db, skip, limit, applicant_id, approval_status, booking_date)
    """
    total, items = await svc.list_vehicle_bookings(
        db,
        skip=skip,
        limit=limit,
        applicant_id=applicant_id,
        approval_status=approval_status,
        booking_date=date,
    )
    return BookingVehiclePage(
        total=total,
        items=[BookingVehicleOut.model_validate(b) for b in items],
    )


@router.get("/vehicles/bookings/{booking_id}", response_model=BookingVehicleOut)
async def get_vehicle_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP GET /vehicles/bookings/{booking_id}
    用途：获取单条用车申请的完整详情，含审批意见和分配的车辆/司机信息。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    响应 (BookingVehicleOut, HTTP 200)：完整用车申请信息
    错误：记录不存在时抛出 404

    权限：需登录
    Service：svc.get_vehicle_booking(db, booking_id)
    """
    booking = await svc.get_vehicle_booking(db, booking_id)
    return BookingVehicleOut.model_validate(booking)


@router.put("/vehicles/bookings/{booking_id}", response_model=BookingVehicleOut)
async def update_vehicle_booking(
    booking_id: int,
    data: BookingVehicleUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP PUT /vehicles/bookings/{booking_id}
    用途：更新用车申请信息（建议仅在 pending 状态时允许申请人修改）。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    请求体 (BookingVehicleUpdate)：可部分更新
      - booking_date / start_time / end_time / destination / purpose / notes 均可选填

    响应 (BookingVehicleOut, HTTP 200)：更新后的用车申请
    错误：记录不存在时抛出 404；已审批的申请修改时抛出 400

    权限：需登录（建议限制为申请人本人或 admin/hr 角色）
    Service：svc.update_vehicle_booking(db, booking_id, data)
    """
    booking = await svc.update_vehicle_booking(db, booking_id, data)
    return BookingVehicleOut.model_validate(booking)


@router.post(
    "/vehicles/bookings/{booking_id}/approve",
    response_model=BookingVehicleOut,
)
async def approve_vehicle_booking(
    booking_id: int,
    data: BookingVehicleApproval,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> BookingVehicleOut:
    """
    HTTP POST /vehicles/bookings/{booking_id}/approve
    用途：管理员/行政专员对用车申请进行审批（同意或拒绝），
          审批通过时可同时分配具体车辆和司机。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    请求体 (BookingVehicleApproval)：
      - approved       : bool — 审批结果：true=同意，false=拒绝（必填）
      - approval_notes : str  — 审批意见（可选，拒绝时建议填写原因）
      - vehicle_plate  : str  — 分配的车牌号（可选，仅在 approved=true 时有意义）
      - driver_name    : str  — 分配的司机姓名（可选）
      - driver_phone   : str  — 司机联系电话（可选）

    响应 (BookingVehicleOut, HTTP 200)：审批后的用车申请（approval_status=approved/rejected）
    错误：记录不存在时抛出 404；非 pending 状态申请审批时抛出 400

    权限：需登录（建议限制为 admin/hr/manager 角色）
    Service：svc.approve_vehicle_booking(db, booking_id, data)
    """
    booking = await svc.approve_vehicle_booking(db, booking_id, data)
    return BookingVehicleOut.model_validate(booking)


@router.delete("/vehicles/bookings/{booking_id}", status_code=204)
async def delete_vehicle_booking(
    booking_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(get_current_user),
) -> None:
    """
    HTTP DELETE /vehicles/bookings/{booking_id}
    用途：物理删除用车申请记录（通常用于清理测试数据或撤销错误申请，
          已审批/已完成的申请建议保留审计记录，不做物理删除）。

    Path 参数：
      - booking_id : int — 用车申请记录主键 ID

    响应：HTTP 204 No Content（无响应体）
    错误：记录不存在时抛出 404

    权限：需登录（建议限制为申请人本人（仅 pending 状态）或 admin 角色）
    Service：svc.delete_vehicle_booking(db, booking_id)
    """
    await svc.delete_vehicle_booking(db, booking_id)
