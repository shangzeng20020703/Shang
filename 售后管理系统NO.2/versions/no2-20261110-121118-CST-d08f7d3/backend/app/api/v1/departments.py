"""
部门管理 API 路由 - 部门 CRUD 与树形结构查询

本模块负责组织架构中"部门"实体的完整管理，涵盖：
  - 部门列表查询（支持按地点/上级部门筛选）
  - 部门树形结构查询（嵌套层级，适用于前端树组件）
  - 部门详情获取
  - 部门创建、更新、删除（软删除）

路由前缀：/api/v1/departments（由 router.py 统一注册）

部门数据结构说明
──────────────────────────────────────────────────────────────────────────
部门支持多层级嵌套（通过 parent_id 自引用）。
树形查询接口（/tree）会递归构建完整层级，返回带 children 字段的嵌套结构，
适合前端 el-tree 或级联选择器直接使用。

本公司当前组织结构示例：
  - 北京研发中心
      ├── 研发部
      │   ├── 算法组
      │   └── 软件组
      └── 行政部
  - 东莞凇湖工厂
      ├── 生产部
      └── 品控部

权限体系
──────────────────────────────────────────────────────────────────────────
  - 读操作（GET）：任何已登录用户（get_current_user）
  - 写操作（POST/PUT/DELETE）：仅 admin 或 hr 角色（require_roles）

服务层对应关系
──────────────────────────────────────────────────────────────────────────
  - DepartmentService.get_list()  : 平铺列表查询
  - DepartmentService.get_tree()  : 树形结构查询（递归构建）
  - DepartmentService.get_by_id() : 单部门详情
  - DepartmentService.create()    : 新建部门
  - DepartmentService.update()    : 更新部门信息
  - DepartmentService.delete()    : 软删除部门
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.schemas.organization import (
    DepartmentCreate,
    DepartmentOut,
    DepartmentTreeNode,
    DepartmentUpdate,
)
from app.services.organization import DepartmentService

router = APIRouter()


# 路由前缀由 router.py 统一设置为 /departments，
# 此处路径相对于该前缀。

@router.get("", response_model=List[DepartmentOut], summary="获取部门列表")
async def list_departments(
    location_id: Optional[int] = Query(None, description="按工作地点筛选"),
    parent_id: Optional[int] = Query(None, description="按上级部门筛选"),
    include_inactive: bool = Query(False, description="是否包含已停用的部门"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/departments

    以平铺方式查询部门列表（非树形），支持按工作地点和上级部门筛选。
    适用于需要简单列表展示的场景（如下拉框选项）。
    若需要树形嵌套结构，请使用 GET /departments/tree。

    Query 参数：
      - location_id      : 可选，按工作地点 ID 筛选归属于该地点的部门
      - parent_id        : 可选，按上级部门 ID 筛选，仅返回该部门的直接子部门；
                           传 0 或不传则返回顶级部门
      - include_inactive : 布尔值，默认 false；为 true 时一并返回已停用的部门

    响应（200）：
      List[DepartmentOut] — 部门列表，每条包含：
        id         : 部门 ID
        name       : 部门名称
        parent_id  : 上级部门 ID（顶级部门为 null）
        location_id: 所属工作地点 ID
        manager_id : 部门负责人员工 ID
        is_active  : 是否启用
        created_at : 创建时间

    权限：任何已登录用户（get_current_user）

    对应 Service：DepartmentService.get_list()
    """
    return await DepartmentService.get_list(
        db,
        location_id=location_id,
        parent_id=parent_id,
        include_inactive=include_inactive,
    )


@router.get(
    "/tree",
    response_model=List[DepartmentTreeNode],
    summary="获取部门树形结构",
)
async def get_department_tree(
    location_id: Optional[int] = Query(None, description="按工作地点筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/departments/tree

    返回完整的部门层级树结构，每个节点带有嵌套的 children 字段。
    适用于前端树形组件（如 el-tree、Cascader）直接渲染。
    只返回启用状态（is_active=True）的部门。

    Query 参数：
      - location_id : 可选，按工作地点 ID 筛选，只返回该地点下的部门树；
                      不传则返回全公司所有地点的完整部门树

    响应（200）：
      List[DepartmentTreeNode] — 顶级部门列表（根节点），每个节点结构：
        DepartmentTreeNode {
          id         : int
          name       : str
          parent_id  : int | null
          location_id: int | null
          manager_id : int | null
          children   : List[DepartmentTreeNode]  -- 递归嵌套子部门
        }

    权限：任何已登录用户（get_current_user）

    对应 Service：DepartmentService.get_tree()
    """
    return await DepartmentService.get_tree(db, location_id=location_id)


@router.get(
    "/{department_id}",
    response_model=DepartmentOut,
    summary="获取部门详情",
)
async def get_department(
    department_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/departments/{department_id}

    获取指定部门的详细信息。

    Path 参数：
      - department_id : int — 部门主键 ID

    响应（200）：
      DepartmentOut — 部门详情，包含所有字段

    响应（404）：部门不存在

    权限：任何已登录用户（get_current_user）

    对应 Service：DepartmentService.get_by_id()
    """
    return await DepartmentService.get_by_id(db, department_id)


@router.post(
    "",
    response_model=DepartmentOut,
    status_code=201,
    summary="创建部门",
)
async def create_department(
    data: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    POST /api/v1/departments

    创建新部门。支持指定上级部门实现多层级结构。

    Body（JSON）：
      DepartmentCreate {
        name        : str       -- 部门名称（必填，同一父级下唯一）
        parent_id   : int | null -- 上级部门 ID；为 null 则创建顶级部门
        location_id : int | null -- 所属工作地点 ID
        manager_id  : int | null -- 部门负责人员工 ID
        description : str        -- 部门描述
      }

    响应（201）：
      DepartmentOut — 新建的部门详情

    响应（409）：同名部门已存在（IntegrityError）
    响应（403）：当前用户不具备 admin 或 hr 角色

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：DepartmentService.create()
    """
    return await DepartmentService.create(db, data)


@router.put(
    "/{department_id}",
    response_model=DepartmentOut,
    summary="更新部门",
)
async def update_department(
    department_id: int,
    data: DepartmentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    PUT /api/v1/departments/{department_id}

    更新指定部门的信息，支持修改名称、归属地点、负责人、上级部门等。

    Path 参数：
      - department_id : int — 部门主键 ID

    Body（JSON）：
      DepartmentUpdate — 需要更新的字段（支持部分更新，未传字段保持原值）：
        name        : str        -- 新名称
        parent_id   : int | null  -- 新上级部门（调整层级时使用）
        location_id : int | null  -- 新工作地点
        manager_id  : int | null  -- 新负责人
        description : str         -- 新描述
        is_active   : bool        -- 启用/停用

    响应（200）：
      DepartmentOut — 更新后的部门详情

    响应（403）：当前用户不具备 admin 或 hr 角色
    响应（404）：部门不存在

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：DepartmentService.update()
    """
    return await DepartmentService.update(db, department_id, data)


@router.delete(
    "/{department_id}",
    status_code=204,
    summary="删除部门（软删除）",
)
async def delete_department(
    department_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin", "hr")),
):
    """
    DELETE /api/v1/departments/{department_id}

    软删除指定部门（将 is_active 置为 False），不物理删除数据库记录。
    软删除后该部门不再出现在默认列表查询中（除非指定 include_inactive=true）。

    注意：若部门下仍有在职员工或子部门，Service 层可能会拒绝删除并返回 400 错误。

    Path 参数：
      - department_id : int — 部门主键 ID

    响应（204）：No Content（删除成功）

    响应（400）：部门下有在职员工或子部门，无法删除
    响应（403）：当前用户不具备 admin 或 hr 角色
    响应（404）：部门不存在

    权限：仅 admin 或 hr 角色（require_roles("admin", "hr")）

    对应 Service：DepartmentService.delete()
    """
    await DepartmentService.delete(db, department_id)
