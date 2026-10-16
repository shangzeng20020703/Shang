"""
工作地点 API 路由 (独立前缀 /locations)

本模块提供工作地点（Location）的完整 CRUD 操作，路由前缀为 /api/v1/locations。

设计背景
──────────────────────────────────────────────────────────────────────────
前端直接通过 GET /api/v1/locations 获取地点列表（下拉框等场景），因此此模块
提供一个独立的 /locations 前缀路由。这些端点与 organizations.py 中挂在
/organizations/locations 下的同名端点功能一致，是对前者的镜像（alias）。

两套路由并存是为了兼容前端调用约定，不会导致功能重复：
  - GET /api/v1/locations         → 本模块（前端主要使用的路径）
  - GET /api/v1/organizations/locations → organizations.py（架构层面的组织归属）

当前工作地点数据（本公司）
──────────────────────────────────────────────────────────────────────────
  ID=1  北京研发中心
  ID=2  东莞凇湖工厂
  ID=3  东莞茵茵工厂

权限体系
──────────────────────────────────────────────────────────────────────────
  - 读操作（GET）  ：任何已登录用户（get_current_user）
  - 写操作（POST/PUT/DELETE）：仅 admin 或 hr 角色（require_roles）

服务层对应关系
──────────────────────────────────────────────────────────────────────────
  - LocationService.get_all()   : 获取全量地点列表
  - LocationService.get_by_id() : 获取单个地点详情
  - LocationService.create()    : 新建地点
  - LocationService.update()    : 更新地点信息
  - LocationService.delete()    : 软删除地点

Frontend expects GET /api/v1/locations to list all locations.
This module provides a standalone /locations prefix that mirrors
the endpoints already available under /organizations/locations.
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
)
from app.services.organization import LocationService

router = APIRouter()


@router.get("", response_model=List[LocationOut], summary="获取所有工作地点")
async def list_locations(
    include_inactive: bool = Query(False, description="是否包含已停用的地点"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/locations

    获取所有工作地点列表。常用于员工创建/编辑表单中的地点下拉框选项，
    以及部门管理时指定所属地点。

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

    权限：任何已登录用户（get_current_user）

    对应 Service：LocationService.get_all()
    """
    return await LocationService.get_all(db, include_inactive=include_inactive)


@router.get("/{location_id}", response_model=LocationOut, summary="获取工作地点详情")
async def get_location(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/locations/{location_id}

    获取指定 ID 工作地点的详细信息。

    Path 参数：
      - location_id : int — 工作地点主键 ID

    响应（200）：
      LocationOut — 工作地点详情

    响应（404）：地点不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：LocationService.get_by_id()
    """
    return await LocationService.get_by_id(db, location_id)


@router.post("", response_model=LocationOut, status_code=201, summary="创建工作地点")
async def create_location(
    data: LocationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/locations

    创建新的工作地点。

    Body（JSON）：
      LocationCreate {
        name     : str  -- 地点名称（必填，如"深圳办公室"）
        address  : str  -- 详细地址
        city     : str  -- 城市
        province : str  -- 省份
      }

    响应（201）：
      LocationOut — 新建的工作地点详情

    响应（409）：同名地点已存在（IntegrityError）
    响应（403）：当前用户不具备 admin 或 hr 角色

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：LocationService.create()
    """
    return await LocationService.create(db, data)


@router.put("/{location_id}", response_model=LocationOut, summary="更新工作地点")
async def update_location(
    location_id: int,
    data: LocationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/locations/{location_id}

    更新指定工作地点的信息。

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

    响应（403）：当前用户不具备 admin 或 hr 角色
    响应（404）：地点不存在

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：LocationService.update()
    """
    return await LocationService.update(db, location_id, data)


@router.delete("/{location_id}", status_code=204, summary="删除工作地点（软删除）")
async def delete_location(
    location_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /api/v1/locations/{location_id}

    软删除指定工作地点（将 is_active 置为 False），不物理删除数据库记录。
    软删除后该地点不再出现在默认列表查询中（除非指定 include_inactive=true）。

    注意：若地点下仍有在职员工或启用的部门，Service 层可能会拒绝删除。

    Path 参数：
      - location_id : int — 工作地点主键 ID

    响应（204）：No Content（删除成功）

    响应（400）：地点下有在职员工或部门，无法删除
    响应（403）：当前用户不具备 admin 或 hr 角色
    响应（404）：地点不存在

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：LocationService.delete()
    """
    await LocationService.delete(db, location_id)
