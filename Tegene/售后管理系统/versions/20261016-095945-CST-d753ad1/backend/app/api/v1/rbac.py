"""
RBAC 角色权限管理 API 路由

本模块负责基于角色的访问控制（Role-Based Access Control）的员工角色管理，
涵盖查询员工当前角色、授予新角色、以及撤销角色。

路由前缀：/api/v1/rbac（由 router.py 统一注册）

角色体系说明
──────────────────────────────────────────────────────────────────────────
系统支持 5 种角色（存储在 employee_roles 表中）：

  | 角色名     | 说明                           | 权限范围         |
  |------------|--------------------------------|------------------|
  | admin       | 系统管理员                    | 全部功能           |
  | asset_admin | 行政管理员                    | 固定资产/行政资产  |
  | hr          | 人力资源专员                  | 员工/合同/组织架构  |
  | manager     | 部门经理                      | 下属员工审批/查看   |
  | finance     | 财务人员                      | 薪资/税务模块      |
  | employee    | 普通员工（默认角色）           | 个人信息自助服务   |

同一员工可以拥有多个角色（多条 EmployeeRole 记录）。
角色判定在 core/deps.py 的 require_roles() 依赖函数中执行。

数据模型说明
──────────────────────────────────────────────────────────────────────────
EmployeeRole 表字段：
  - id          : 主键
  - employee_id : 关联员工 ID（外键 → employees.id）
  - role_name   : 角色名称（admin/asset_admin/hr/manager/finance/employee）
  - is_active   : 是否生效（撤销时置 False，不物理删除）
  - granted_by  : 授权操作者的员工 ID
  - created_at  : 授权时间

撤销实现：软删除（is_active = False），保留授权历史记录。

权限要求
──────────────────────────────────────────────────────────────────────────
  - 查询角色（GET）：任何已登录用户（get_current_user）
  - 授予角色（POST）：仅 admin 角色（require_roles("admin")）
  - 撤销角色（DELETE）：仅 admin 角色（require_roles("admin")）

内联 Schema 说明（本模块直接定义，未使用 schemas/ 目录）
──────────────────────────────────────────────────────────────────────────
  - RoleGrantRequest : 授权请求体，包含 role_name 字段
  - RoleOut          : 角色记录响应体，包含 id/employee_id/role_name/is_active/
                       granted_by/created_at
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, require_roles
from app.models.employee import Employee
from app.models.employee_role import EmployeeRole

router = APIRouter()

# 系统支持的合法角色集合（用于入参校验）
VALID_ROLES = {"admin", "asset_admin", "hr", "manager", "finance", "employee"}


class RoleGrantRequest(BaseModel):
    """
    授予角色请求体。

    字段：
      - role_name : str — 要授予的角色名称，必须是 VALID_ROLES 中的合法值
                          （admin/asset_admin/hr/manager/finance/employee）
    """
    role_name: str


class RoleOut(BaseModel):
    """
    角色记录响应体。

    字段：
      - id          : int            — 角色记录主键 ID
      - employee_id : int            — 被授权的员工 ID
      - role_name   : str            — 角色名称
      - is_active   : bool           — 是否生效（False 表示已被撤销）
      - granted_by  : int | null     — 执行授权操作的管理员员工 ID
      - created_at  : datetime       — 授权时间（含时区）
    """
    id: int
    employee_id: int
    role_name: str
    is_active: bool
    granted_by: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/employees/{employee_id}/roles", response_model=list[RoleOut], summary="查询员工角色")
async def list_employee_roles(
    employee_id: int,
    db: AsyncSession = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """
    GET /api/v1/rbac/employees/{employee_id}/roles

    查询指定员工当前持有的所有角色记录，包含已撤销（is_active=False）的历史记录。
    前端可通过过滤 is_active=True 的记录来展示当前有效角色。

    Path 参数：
      - employee_id : int — 员工主键 ID

    响应（200）：
      List[RoleOut] — 该员工的所有角色记录列表（含已撤销的历史记录）

    响应（200，空列表）：员工存在但尚未被授予任何角色

    权限：任何已登录用户（get_current_user）；
    使用 _ 命名约定表示当前用户对象在本函数中不被直接使用，仅作身份验证用途。

    直接操作数据库（未封装 Service 层）：
      SELECT * FROM employee_roles WHERE employee_id = {employee_id}
    """
    result = await db.execute(
        select(EmployeeRole).where(EmployeeRole.employee_id == employee_id)
    )
    return list(result.scalars().all())


@router.post("/employees/{employee_id}/roles", response_model=RoleOut, summary="授予员工角色")
async def grant_role(
    employee_id: int,
    data: RoleGrantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(require_roles("admin")),
):
    """
    POST /api/v1/rbac/employees/{employee_id}/roles

    向指定员工授予一个新角色。执行前会进行以下校验：
      1. role_name 必须是合法角色值（admin/asset_admin/hr/manager/finance/employee）
      2. 目标员工必须存在
      3. 员工不能已持有同名的有效角色（防止重复授权）

    Path 参数：
      - employee_id : int — 被授权员工的主键 ID

    Body（JSON）：
      RoleGrantRequest {
        role_name : str  -- 要授予的角色名（必填）
      }

    响应（200）：
      RoleOut — 新建的角色授权记录，granted_by 字段为当前操作者（admin）的员工 ID

    响应（400）：
      - role_name 不在合法角色集合中
      - 员工已持有该角色（is_active=True 的同名记录已存在）

    响应（403）：当前用户不具备 admin 角色

    响应（404）：目标员工不存在

    权限：仅 admin 角色（require_roles("admin")）

    直接操作数据库（未封装 Service 层）：
      INSERT INTO employee_roles (employee_id, role_name, granted_by, is_active) VALUES (...)
    """
    if data.role_name not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"无效角色，支持: {', '.join(sorted(VALID_ROLES))}")

    # 检查员工是否存在
    emp_result = await db.execute(select(Employee).where(Employee.id == employee_id))
    if not emp_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="员工不存在")

    # 防止重复授权同一角色
    existing_result = await db.execute(
        select(EmployeeRole).where(
            EmployeeRole.employee_id == employee_id,
            EmployeeRole.role_name == data.role_name,
            EmployeeRole.is_active == True,
        )
    )
    if existing_result.scalars().first():
        raise HTTPException(status_code=400, detail=f"员工已拥有 {data.role_name} 角色")

    role = EmployeeRole(
        employee_id=employee_id,
        role_name=data.role_name,
        granted_by=current_user.id,
        is_active=True,
    )
    db.add(role)
    await db.flush()
    await db.refresh(role)
    return role


@router.delete("/roles/{role_id}", summary="撤销员工角色")
async def revoke_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    _: Employee = Depends(require_roles("admin")),
):
    """
    DELETE /api/v1/rbac/roles/{role_id}

    撤销指定 ID 的角色授权记录（软删除：将 is_active 置为 False）。
    不物理删除数据库记录，保留授权历史可追溯。

    Path 参数：
      - role_id : int — 角色记录的主键 ID（EmployeeRole.id，非员工 ID）

    响应（200）：
      { "message": "角色已撤销" }

    响应（403）：当前用户不具备 admin 角色

    响应（404）：角色记录不存在（role_id 无效）

    权限：仅 admin 角色（require_roles("admin")）；
    使用 _ 命名约定表示当前用户对象在本函数中不被直接使用，仅作权限校验用途。

    注意：此操作不会验证被撤销角色的 is_active 状态，即使已撤销的记录再次调用
    本接口也不会报错（幂等性操作）。

    直接操作数据库（未封装 Service 层）：
      UPDATE employee_roles SET is_active = False WHERE id = {role_id}
    """
    result = await db.execute(select(EmployeeRole).where(EmployeeRole.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色记录不存在")
    role.is_active = False
    await db.flush()
    return {"message": "角色已撤销"}
