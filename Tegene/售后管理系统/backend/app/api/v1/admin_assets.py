"""
资产管理 API 路由模块 — admin_assets.py

本模块负责公司固定资产的台账管理、领用出库及归还入库全流程，由 router.py 以前缀
/api/v1/admin/assets 挂载（具体前缀以 router.py 配置为准）。

功能概览：
  资产台账管理：
    - 新建资产条目（录入采购信息，初始状态 in_stock）
    - 查询资产列表（支持状态/类别/地点筛选，分页）
    - 获取资产详情
    - 更新资产信息或状态
    - 删除资产记录

  资产领用与归还（AssetCheckout）：
    - 领用出库：POST /checkout — 将资产分配给员工（in_stock → in_use）
    - 归还入库：POST /checkout/{id}/return — 员工归还资产（in_use → in_stock）
    - 查询员工领用记录：GET /checkout/employee/{employee_id} — 离职交接必查

资产状态机：
  in_stock（在库）
    ├──领用──> in_use（使用中）──归还──> in_stock
    ├──送修──> under_maintenance（维修中）──维修完成──> in_stock
    └──报废──> scrapped（已报废，终态）

路由顺序说明（重要）：
  FastAPI 按注册顺序匹配路由，固定路径必须在路径参数路由之前注册：
    /checkout              （固定路径，POST 领用）
    /checkout/{checkout_id}/return （含参数，POST 归还）
    /checkout/employee/{employee_id} （含参数，GET 按员工查询）
  以上三条均需在 /{asset_id} 之前注册，否则 "checkout" 会被误匹配为 asset_id。
  在本文件中通过在 /{asset_id} 路由之后注册 /checkout 系列来规避冲突
  （admin.py 综合版中已按正确顺序处理）。

认证与权限：
  所有端点均需 JWT 认证（Depends(get_current_user)）。
  写操作（创建/更新/删除/领用/归还）建议限制为 admin/hr 角色。
  查询操作（列表/详情）所有登录用户均可访问。

对应 Service：app.services.admin（以 svc 导入）
对应 Schema ：app.schemas.admin.Asset* / AssetCheckout*
关联模型    ：app.models.admin.Asset / AssetCheckout

注意：本文件与 admin.py 中的 /assets 路由组功能完全相同，
      是从综合文件拆分出来的独立版本，由 router.py 选择性挂载其中之一。
"""

import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole
from app.models.admin import AssetCheckout
from app.schemas.admin import (
    AssetCheckoutCreate,
    AssetCheckoutOut,
    AssetDepreciationRuleCreate,
    AssetDepreciationRuleOut,
    AssetDepreciationRulePage,
    AssetDepreciationRuleUpdate,
    AssetInventoryRecordCreate,
    AssetImportResult,
    AssetInventoryRecordOut,
    AssetInventoryRecordPage,
    AssetCreate,
    AssetMonthlySnapshotGenerateRequest,
    AssetMonthlySnapshotOut,
    AssetMonthlySnapshotPage,
    AssetOut,
    AssetPage,
    AssetReturnRequest,
    AssetStatsOut,
    AssetUpdate,
    ToolItemCreate,
    ToolItemOut,
    ToolItemPage,
    ToolItemUpdate,
)
from app.services import admin as svc

# 资产路由器，不含前缀（由 router.py 统一挂载时指定前缀）
router = APIRouter()


async def _ensure_asset_manager(db: AsyncSession, current_user: Employee) -> None:
    if getattr(current_user, "is_superuser", False):
        return
    result = await db.execute(
        select(EmployeeRole).where(
            EmployeeRole.employee_id == current_user.id,
            EmployeeRole.role_name.in_(["admin", "asset_admin"]),
            EmployeeRole.is_active == True,
        )
    )
    if not result.scalars().first():
        raise HTTPException(status_code=403, detail="需要权限: admin, asset_admin")


async def _get_checkout_with_relations(db: AsyncSession, checkout_id: int):
    result = await db.execute(
        select(AssetCheckout)
        .options(
            selectinload(AssetCheckout.asset),
            selectinload(AssetCheckout.employee),
        )
        .where(AssetCheckout.id == checkout_id)
    )
    return result.scalar_one_or_none()


def _checkout_out(checkout) -> AssetCheckoutOut:
    out = AssetCheckoutOut.model_validate(checkout)
    out.asset_no = checkout.asset.asset_no if checkout.asset else None
    out.asset_name = checkout.asset.name if checkout.asset else None
    out.usage_department = checkout.asset.usage_department if checkout.asset else None
    out.employee_name = checkout.employee.name if checkout.employee else None
    out.employee_no = checkout.employee.employee_no if checkout.employee else None
    out.department_name = getattr(checkout.employee, "department_name", None) if checkout.employee else None
    return out


@router.post("", response_model=AssetOut, status_code=201)
async def create_asset(
    data: AssetCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetOut:
    """
    HTTP POST /admin/assets
    用途：录入新资产条目，将资产纳入台账管理，初始状态为 in_stock（在库）。
          通常在资产采购入库后由 IT 或行政人员操作。

    请求体 (AssetCreate)：
      - name           : str     — 资产名称（必填），如 "联想 ThinkPad X1 Carbon"
      - asset_code     : str     — 资产编码（必填，系统内唯一），如 "IT-2024-001"
      - category       : str     — 资产类别（必填），如 IT设备 / 办公家具 / 车辆 / 其他
      - location_id    : int     — 所在地点 ID（可选，关联 organizations.Location 表）
      - purchase_date  : date    — 采购日期（可选）
      - purchase_price : Decimal — 采购价格（可选，单位：元）
      - description    : str     — 备注说明（可选），如品牌型号、配置详情

    响应 (AssetOut, HTTP 201)：
      - 新建资产完整信息，含：id / asset_code / status="in_stock" / created_at

    错误：
      - 409 Conflict   ：asset_code 重复
      - 422 Unprocessable：必填字段缺失或格式错误

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.create_asset(db, data)
    """
    asset = await svc.create_asset(db, data)
    return AssetOut.model_validate(asset)


@router.get("", response_model=AssetPage)
async def list_assets(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None, description="按状态筛选"),
    category: Optional[str] = Query(None, description="按类别筛选"),
    location_id: Optional[int] = Query(None, description="按地点筛选"),
    keyword: Optional[str] = Query(None, description="按资产编号/名称搜索"),
    usage_department: Optional[str] = Query(None, description="按使用部门筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetPage:
    """
    HTTP GET /admin/assets
    用途：分页查询资产台账，支持按状态、类别、所在地点多维度筛选，
          按资产编码或创建时间排序。

    Query 参数：
      - skip        : int（默认 0，最小 0）       — 分页偏移量
      - limit       : int（默认 20，范围 1-1000）  — 每页条数
      - status      : str（可选）                  — 筛选资产状态：
                                                       "in_stock"          = 在库
                                                       "in_use"            = 使用中
                                                       "under_maintenance" = 维修中
                                                       "scrapped"          = 已报废
      - category    : str（可选）                  — 筛选资产类别，精确匹配
      - location_id : int（可选）                  — 筛选所在地点 ID

    响应 (AssetPage, HTTP 200)：
      - total : int          — 满足条件的资产总数（用于前端分页组件）
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
        keyword=keyword,
        usage_department=usage_department,
    )
    return AssetPage(
        total=total,
        items=[AssetOut.model_validate(a) for a in items],
    )


@router.get("/stats", response_model=AssetStatsOut)
async def get_asset_stats(
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetStatsOut:
    return await svc.get_asset_stats(db)


@router.get("/depreciation-rules", response_model=AssetDepreciationRulePage)
async def list_asset_depreciation_rules(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    major_category: Optional[str] = Query(None, description="资产大类"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> AssetDepreciationRulePage:
    total, items = await svc.list_asset_depreciation_rules(
        db,
        skip=skip,
        limit=limit,
        major_category=major_category,
    )
    return AssetDepreciationRulePage(
        total=total,
        items=[AssetDepreciationRuleOut.model_validate(item) for item in items],
    )


@router.post("/depreciation-rules", response_model=AssetDepreciationRuleOut, status_code=201)
async def create_asset_depreciation_rule(
    data: AssetDepreciationRuleCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> AssetDepreciationRuleOut:
    rule = await svc.create_asset_depreciation_rule(db, data)
    return AssetDepreciationRuleOut.model_validate(rule)


@router.put("/depreciation-rules/{rule_id}", response_model=AssetDepreciationRuleOut)
async def update_asset_depreciation_rule(
    rule_id: int,
    data: AssetDepreciationRuleUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> AssetDepreciationRuleOut:
    rule = await svc.update_asset_depreciation_rule(db, rule_id, data)
    return AssetDepreciationRuleOut.model_validate(rule)


@router.delete("/depreciation-rules/{rule_id}", status_code=204)
async def delete_asset_depreciation_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> None:
    await svc.delete_asset_depreciation_rule(db, rule_id)


@router.post("/monthly-snapshots/generate")
async def generate_asset_monthly_snapshots(
    data: AssetMonthlySnapshotGenerateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> dict:
    return await svc.generate_asset_monthly_snapshots(db, data)


@router.get("/monthly-snapshots", response_model=AssetMonthlySnapshotPage)
async def list_asset_monthly_snapshots(
    report_year: int = Query(..., ge=2000, le=2100),
    report_month: int = Query(..., ge=1, le=12),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    major_category: Optional[str] = Query(None, description="资产大类"),
    keyword: Optional[str] = Query(None, description="资产编号/名称搜索"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> AssetMonthlySnapshotPage:
    total, items = await svc.list_asset_monthly_snapshots(
        db,
        report_year=report_year,
        report_month=report_month,
        skip=skip,
        limit=limit,
        major_category=major_category,
        keyword=keyword,
    )
    return AssetMonthlySnapshotPage(
        total=total,
        items=[AssetMonthlySnapshotOut.model_validate(item) for item in items],
    )


@router.post("/import", response_model=AssetImportResult)
async def import_assets(
    file: UploadFile = File(..., description="固定资产 Excel 文件（.xlsx）"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> AssetImportResult:
    file_bytes = await file.read()
    return await svc.import_assets_excel(db, file_bytes=file_bytes, filename=file.filename or "")


@router.get("/export")
async def export_assets(
    report_year: Optional[int] = Query(None, ge=2000, le=2100),
    report_month: Optional[int] = Query(None, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin", "finance")),
) -> StreamingResponse:
    data = await svc.export_assets_excel(db, report_year=report_year, report_month=report_month)
    suffix = f"_{report_year}{str(report_month).zfill(2)}" if report_year and report_month else ""
    filename = f"fixed_assets{suffix}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )


# ───── Asset Checkout — 资产领用与归还子路由 ─────
# 以下路由路径均以 /checkout 开头，注册在 /{asset_id} 之后。
# 由于 FastAPI 按注册顺序精确匹配，路径字面量 "checkout" 不会被 /{asset_id} 捕获。


@router.post("/checkout", response_model=AssetCheckoutOut, status_code=201)
async def checkout_asset(
    data: AssetCheckoutCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetCheckoutOut:
    """
    HTTP POST /admin/assets/checkout
    用途：办理资产领用出库，将指定资产从在库分配给员工，
          资产状态自动由 in_stock 变更为 in_use，并创建领用记录。

    请求体 (AssetCheckoutCreate)：
      - asset_id             : int  — 要领用的资产 ID（必填，资产须处于 in_stock 状态）
      - employee_id          : int  — 领用员工 ID（必填）
      - checkout_date        : date — 领用日期（必填）
      - expected_return_date : date — 预计归还日期（可选，用于提醒管理）
      - notes                : str  — 领用备注（可选）

    响应 (AssetCheckoutOut, HTTP 201)：
      - 新建的领用记录，含：checkout_id / asset_id / employee_id /
        checkout_date / expected_return_date / return_date（此时为 null）

    错误：
      - 404 Not Found  ：资产 ID 或员工 ID 不存在
      - 400 Bad Request：资产当前状态非 in_stock（如已被他人领用或报废）

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.checkout_asset(db, data)
    """
    checkout = await svc.checkout_asset(db, data)
    enriched = await _get_checkout_with_relations(db, checkout.id)
    return _checkout_out(enriched)


@router.post("/checkout/{checkout_id}/return", response_model=AssetCheckoutOut)
async def return_asset(
    checkout_id: int,
    data: AssetReturnRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetCheckoutOut:
    """
    HTTP POST /admin/assets/checkout/{checkout_id}/return
    用途：办理资产归还入库，员工将资产交回后由行政人员操作，
          资产状态自动由 in_use 变回 in_stock，并在领用记录上写入归还信息。

    Path 参数：
      - checkout_id : int — 领用记录主键 ID（由领用接口返回）

    请求体 (AssetReturnRequest)：
      - return_date : date — 实际归还日期（必填）
      - condition   : str  — 归还时资产状况（可选）：良好 / 轻微磨损 / 损坏 / 丢失
      - notes       : str  — 归还备注（可选），如描述损坏情况

    响应 (AssetCheckoutOut, HTTP 200)：
      - 更新后的领用记录，return_date 字段已填写，资产状态已变为 in_stock

    错误：
      - 404 Not Found  ：领用记录 ID 不存在
      - 400 Bad Request：该领用记录已归还（return_date 非空）

    权限：需登录（建议限制为 admin/hr 角色）
    Service：svc.return_asset(db, checkout_id, data)
    """
    checkout = await svc.return_asset(db, checkout_id, data)
    enriched = await _get_checkout_with_relations(db, checkout.id)
    return _checkout_out(enriched)


@router.get(
    "/checkout/employee/{employee_id}",
    response_model=list[AssetCheckoutOut],
)
async def list_employee_checkouts(
    employee_id: int,
    active_only: bool = Query(False, description="仅显示在用资产"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[AssetCheckoutOut]:
    """
    HTTP GET /admin/assets/checkout/employee/{employee_id}
    用途：查询指定员工的所有历史或当前资产领用记录。
          核心使用场景：员工离职交接时，HR 通过此接口确认该员工名下是否有未归还资产，
          确保离职手续完整（已归还 → 可继续办离职；未归还 → 需先催还）。

    Path 参数：
      - employee_id : int — 员工主键 ID

    Query 参数：
      - active_only : bool（默认 false）— 为 true 时只返回 return_date 为 null 的记录
                                          （即仍处于 in_use 状态、尚未归还的领用记录）

    响应 (list[AssetCheckoutOut], HTTP 200)：
      - 该员工的领用记录列表，包含资产名称、领用日期、归还日期等信息
      - 返回空列表表示该员工无任何领用记录（或无未归还记录）

    错误：
      - 404 Not Found：员工 ID 不存在

    权限：需登录（员工可查询自己的记录，HR/admin 可查任意员工）
    Service：svc.list_checkouts_by_employee(db, employee_id, active_only)
    """
    if current_user.id != employee_id:
        await _ensure_asset_manager(db, current_user)

    checkouts = await svc.list_checkouts_by_employee(
        db, employee_id, active_only=active_only
    )
    return [_checkout_out(c) for c in checkouts]


@router.get("/checkouts")
async def list_asset_checkouts(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = Query(None, description="领用状态"),
    employee_id: Optional[int] = Query(None, description="领用员工ID"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> dict:
    total, items = await svc.list_asset_checkouts(
        db, skip=skip, limit=limit, status_filter=status, employee_id=employee_id
    )
    return {
        "total": total,
        "items": [_checkout_out(c) for c in items],
    }


@router.post("/inventories", response_model=AssetInventoryRecordOut, status_code=201)
async def create_asset_inventory_record(
    data: AssetInventoryRecordCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetInventoryRecordOut:
    record = await svc.create_asset_inventory_record(db, data)
    asset = await svc.get_asset(db, record.asset_id)
    out = AssetInventoryRecordOut.model_validate(record)
    out.asset_no = asset.asset_no
    out.asset_name = asset.name
    return out


@router.get("/inventories", response_model=AssetInventoryRecordPage)
async def list_asset_inventory_records(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    result: Optional[str] = Query(None, description="盘点结果"),
    asset_id: Optional[int] = Query(None, description="资产ID"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetInventoryRecordPage:
    total, items = await svc.list_asset_inventory_records(
        db, skip=skip, limit=limit, result_filter=result, asset_id=asset_id
    )
    mapped: list[AssetInventoryRecordOut] = []
    for item in items:
        asset = await svc.get_asset(db, item.asset_id)
        out = AssetInventoryRecordOut.model_validate(item)
        out.asset_no = asset.asset_no
        out.asset_name = asset.name
        mapped.append(out)
    return AssetInventoryRecordPage(total=total, items=mapped)


@router.get("/tools", response_model=ToolItemPage)
async def list_tool_items(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    keyword: Optional[str] = Query(None, description="按工具编号/名称/保管人搜索"),
    usage_department: Optional[str] = Query(None, description="按使用部门筛选"),
    is_in_use: Optional[bool] = Query(None, description="按是否使用筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> ToolItemPage:
    total, items = await svc.list_tool_items(
        db,
        skip=skip,
        limit=limit,
        keyword=keyword,
        usage_department=usage_department,
        is_in_use=is_in_use,
    )
    return ToolItemPage(total=total, items=[ToolItemOut.model_validate(item) for item in items])


@router.post("/tools", response_model=ToolItemOut, status_code=201)
async def create_tool_item(
    data: ToolItemCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> ToolItemOut:
    item = await svc.create_tool_item(db, data)
    return ToolItemOut.model_validate(item)


@router.put("/tools/{tool_id}", response_model=ToolItemOut)
async def update_tool_item(
    tool_id: int,
    data: ToolItemUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> ToolItemOut:
    item = await svc.update_tool_item(db, tool_id, data)
    return ToolItemOut.model_validate(item)


@router.delete("/tools/{tool_id}", status_code=204)
async def delete_tool_item(
    tool_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> None:
    await svc.delete_tool_item(db, tool_id)


@router.get("/tools/export")
async def export_tool_items(
    keyword: Optional[str] = Query(None, description="按工具编号/名称/保管人搜索"),
    usage_department: Optional[str] = Query(None, description="按使用部门筛选"),
    is_in_use: Optional[bool] = Query(None, description="按是否使用筛选"),
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> StreamingResponse:
    data = await svc.export_tool_items_excel(
        db,
        keyword=keyword,
        usage_department=usage_department,
        is_in_use=is_in_use,
    )
    filename = f"tool_items_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetOut:
    """
    HTTP GET /admin/assets/{asset_id}
    用途：获取指定资产的完整详情，包含当前持有人信息（若状态为 in_use）
          和最近领用记录摘要。
    """
    asset = await svc.get_asset(db, asset_id)
    return AssetOut.model_validate(asset)


@router.put("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: int,
    data: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> AssetOut:
    """
    HTTP PUT /admin/assets/{asset_id}
    用途：更新资产基本信息或手动变更资产状态。
    """
    asset = await svc.update_asset(db, asset_id, data)
    return AssetOut.model_validate(asset)


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: Employee = Depends(require_roles("admin", "asset_admin")),
) -> None:
    """
    HTTP DELETE /admin/assets/{asset_id}
    用途：物理删除指定资产记录及其关联的领用记录。
    """
    await svc.delete_asset(db, asset_id)
