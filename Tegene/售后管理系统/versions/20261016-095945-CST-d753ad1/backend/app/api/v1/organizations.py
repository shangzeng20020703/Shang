"""
组织架构 - 工作地点 API 路由（前缀 /organizations）

本模块将工作地点（Location）的 CRUD 操作挂载在 /api/v1/organizations/locations 路径下，
是组织架构模块的子路由，从架构语义上体现"工作地点隶属于组织架构"的设计。

路由前缀说明
──────────────────────────────────────────────────────────────────────────
由 router.py 注册为 /organizations 前缀。
注意：本模块没有根路径 /organizations/，所有端点都以 /organizations/locations 开头。
  - 正确访问路径：GET /api/v1/organizations/locations
  - 不存在根路径：GET /api/v1/organizations/（会 404）

与 locations.py 的关系
──────────────────────────────────────────────────────────────────────────
本模块（organizations.py）与 locations.py 提供完全相同的功能，区别在于路径前缀：
  - organizations.py : /api/v1/organizations/locations（架构语义路由）
  - locations.py     : /api/v1/locations（前端约定路由，推荐前端使用）

两套路由共享同一个 LocationService，数据完全一致。

权限体系
──────────────────────────────────────────────────────────────────────────
注意：本模块的写操作（POST/PUT/DELETE）当前仅使用 get_current_user 校验（任何
已登录用户均可操作），与 locations.py 中的 require_roles("admin", "hr") 不同。
如需加强权限控制，可改为 require_roles("admin", "hr")。

  - 读操作（GET）  ：任何已登录用户（get_current_user）
  - 写操作（POST/PUT/DELETE）：任何已登录用户（get_current_user）

服务层对应关系
──────────────────────────────────────────────────────────────────────────
  - LocationService.get_all()   : 获取全量地点列表
  - LocationService.get_by_id() : 获取单个地点详情
  - LocationService.create()    : 新建地点
  - LocationService.update()    : 更新地点信息
  - LocationService.delete()    : 软删除地点
"""
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.organization import (
    LocationCreate,
    LocationOut,
    LocationUpdate,
    OrgCompanyLayoutOut,
    OrgCompanyLayoutUpdate,
)
from app.services.organization import LocationService, OrgCompanyLayoutService

router = APIRouter()


# 路由前缀由 router.py 统一设置为 /organizations，
# 此处路径相对于该前缀。

@router.get("/locations", response_model=List[LocationOut], summary="获取所有工作地点")
async def list_locations(
    include_inactive: bool = Query(False, description="是否包含已停用的地点"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/organizations/locations

    获取所有工作地点列表（架构语义路由）。
    与 GET /api/v1/locations 功能完全一致，供架构层面的调用方使用。

    Query 参数：
      - include_inactive : 布尔值，默认 false；
                           为 true 时一并返回已停用（is_active=False）的地点

    响应（200）：
      List[LocationOut] — 工作地点列表，每条包含：
        id          : 地点 ID
        name        : 地点名称（如"北京研发中心"）
        address     : 详细地址
        city        : 城市
        province    : 省份
        is_active   : 是否启用
        created_at  : 创建时间

    当前地点数据：
      ID=1 北京研发中心 / ID=2 东莞凇湖工厂 / ID=3 东莞茵茵工厂

    权限：任何已登录用户（get_current_user）

    对应 Service：LocationService.get_all()
    """
    return await LocationService.get_all(db, include_inactive=include_inactive)


@router.get("/locations/{location_id}", response_model=LocationOut, summary="获取工作地点详情")
async def get_location(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/organizations/locations/{location_id}

    获取指定 ID 工作地点的详细信息（架构语义路由）。

    Path 参数：
      - location_id : int — 工作地点主键 ID

    响应（200）：
      LocationOut — 工作地点详情

    响应（404）：地点不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：LocationService.get_by_id()
    """
    return await LocationService.get_by_id(db, location_id)


@router.post("/locations", response_model=LocationOut, status_code=201, summary="创建工作地点")
async def create_location(
    data: LocationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    POST /api/v1/organizations/locations

    创建新的工作地点（架构语义路由）。

    注意：与 POST /api/v1/locations 不同，此端点当前权限为任何已登录用户
    （get_current_user），而非仅限 admin/hr。如需收紧权限，应改为
    require_roles("admin", "hr")。

    Body（JSON）：
      LocationCreate {
        name     : str  -- 地点名称（必填）
        address  : str  -- 详细地址
        city     : str  -- 城市
        province : str  -- 省份
      }

    响应（201）：
      LocationOut — 新建的工作地点详情

    响应（409）：同名地点已存在（IntegrityError）

    权限：任何已登录用户（get_current_user）

    对应 Service：LocationService.create()
    """
    return await LocationService.create(db, data)


@router.put("/locations/{location_id}", response_model=LocationOut, summary="更新工作地点")
async def update_location(
    location_id: int,
    data: LocationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    PUT /api/v1/organizations/locations/{location_id}

    更新指定工作地点的信息（架构语义路由）。

    注意：与 PUT /api/v1/locations/{id} 不同，此端点当前权限为任何已登录用户
    （get_current_user），而非仅限 admin/hr。

    Path 参数：
      - location_id : int — 工作地点主键 ID

    Body（JSON）：
      LocationUpdate — 需要更新的字段（支持部分更新）：
        name     : str   -- 新名称
        address  : str   -- 新地址
        city     : str   -- 新城市
        province : str   -- 新省份
        is_active: bool  -- 启用/停用

    响应（200）：
      LocationOut — 更新后的工作地点详情

    响应（404）：地点不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：LocationService.update()
    """
    return await LocationService.update(db, location_id, data)


@router.delete("/locations/{location_id}", status_code=204, summary="删除工作地点（软删除）")
async def delete_location(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    DELETE /api/v1/organizations/locations/{location_id}

    软删除指定工作地点（架构语义路由）。
    将 is_active 置为 False，不物理删除数据库记录。

    注意：与 DELETE /api/v1/locations/{id} 不同，此端点当前权限为任何已登录用户
    （get_current_user），而非仅限 admin/hr。

    Path 参数：
      - location_id : int — 工作地点主键 ID

    响应（204）：No Content（删除成功）

    响应（400）：地点下有在职员工或部门，无法删除
    响应（404）：地点不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：LocationService.delete()
    """
    await LocationService.delete(db, location_id)


@router.get(
    "/company-layouts",
    response_model=List[OrgCompanyLayoutOut],
    summary="获取组织架构中的公司节点布局配置",
)
async def list_org_company_layouts(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await OrgCompanyLayoutService.get_all(db)


@router.put(
    "/company-layouts/{company_id}",
    response_model=OrgCompanyLayoutOut,
    summary="更新组织架构中的公司节点布局配置",
)
async def update_org_company_layout(
    company_id: int,
    data: OrgCompanyLayoutUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    return await OrgCompanyLayoutService.upsert(db, company_id=company_id, data=data)


@router.delete(
    "/company-layouts/{company_id}",
    response_model=OrgCompanyLayoutOut,
    summary="在组织架构中隐藏公司节点（不删除公司主数据）",
)
async def hide_org_company_layout(
    company_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    return await OrgCompanyLayoutService.hide(db, company_id=company_id)
