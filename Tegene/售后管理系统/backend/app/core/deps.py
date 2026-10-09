"""
=============================================================================
模块：Core / Dependencies
文件：app/core/deps.py
=============================================================================
作用：
    FastAPI 依赖注入中心。提供身份认证和权限控制两个核心依赖，
    所有需要鉴权的 API 端点通过 Depends() 注入这些函数。

功能：
    - get_current_user()：解析 JWT Token → 查询员工 → 校验账号状态
    - require_roles()：角色权限工厂函数 → 返回检查指定角色的依赖函数

系统鉴权流程：
    HTTP Request
        ↓
    Authorization: Bearer <token>
        ↓
    OAuth2PasswordBearer 提取 token
        ↓
    jwt.decode() 解析 payload，获取 user_id (sub 字段)
        ↓
    SELECT * FROM employees WHERE id=user_id
        ↓
    检查 is_active（账号是否停用）
        ↓
    [可选] require_roles() 检查 employee_roles 表角色权限
        ↓
    返回 Employee 对象注入路由函数

调用关系：
    被调用方（所有需要鉴权的 API 端点）：
        from app.core.deps import get_current_user, require_roles
        Depends(get_current_user)          → 仅验证登录状态
        Depends(require_roles("admin","hr"))→ 验证登录 + 角色权限

    调用方（本文件依赖）：
        app/core/config.py    → SECRET_KEY, ALGORITHM, API_V1_STR
        app/core/database.py  → get_db (Session 注入)
        app/models/employee.py→ Employee ORM 模型
        app/models/employee_role.py → EmployeeRole RBAC 模型

角色说明（role_name 取值）：
    admin       - 系统管理员，绕过所有角色检查
    asset_admin - 行政管理员，可操作固定资产等行政功能
    hr          - 人力资源，可操作员工/请假/薪资等核心 HR 功能
    manager     - 部门经理，可审批下属的请假/考勤等
    finance     - 财务，可操作薪资发放和财务相关功能
    employee    - 普通员工（默认角色）

注意：
    is_superuser=True 的员工绕过所有角色检查（系统超级管理员）。
    ImportError 不再静默忽略，会抛出 RuntimeError 确保 RBAC 配置正确。
=============================================================================
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.models.employee import Employee

# ── OAuth2 Bearer Token 提取器 ────────────────────────────────────────────────
# tokenUrl 指向登录接口，Swagger UI 会使用该地址显示"登录"按钮
# 注意：登录接口接受 JSON 格式，非 OAuth2 标准的 form-urlencoded 格式
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> Employee:
    """
    FastAPI 依赖：从请求头 Authorization: Bearer <token> 中提取并验证用户身份。

    验证步骤：
        1. jwt.decode() 解析 token，获取 payload 中的 sub（用户 ID）
        2. 将 sub 转换为整数 user_id（JWT sub 字段存储为字符串）
        3. 从数据库查询 Employee 记录
        4. 检查 is_active 确保账号未被停用

    Args:
        db (AsyncSession):  数据库会话，由 Depends(get_db) 自动注入
        token (str):        JWT 字符串，由 OAuth2PasswordBearer 从请求头提取

    Returns:
        Employee: 已验证的当前登录员工 ORM 对象

    Raises:
        HTTPException(401): token 无效、过期或 sub 字段缺失
        HTTPException(403): 账号已被停用（is_active=False）

    调用示例：
        @router.get("/me")
        async def get_profile(current_user: Employee = Depends(get_current_user)):
            return current_user
    """
    # 统一的认证失败异常，不暴露具体失败原因（安全最佳实践）
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 第一步：解析 JWT，提取用户 ID
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception  # token 中没有 sub 字段
    except JWTError:
        raise credentials_exception      # token 签名错误或已过期

    # 第二步：sub 字段为字符串，需显式转换为整数
    # 历史原因：早期代码曾存在类型混用 bug，此处做防御性处理
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        raise credentials_exception

    # 第三步：查询数据库确认用户存在
    result = await db.execute(select(Employee).where(Employee.id == uid))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception

    # 第四步：检查账号是否处于激活状态（离职/停用员工无法登录）
    if not user.is_active or user.status not in {"在职", "试用"}:
        raise HTTPException(status_code=403, detail="账号已被停用")

    from app.services.wecom_auth import validate_session_identity
    await validate_session_identity(db, user, payload)
    return user


async def get_user_role_names(
    db: AsyncSession,
    current_user: Employee,
) -> set[str]:
    """
    返回当前用户的有效角色集合。

    规则：
    - 超级管理员直接视为 admin
    - employee_roles 中存在启用角色则返回全部启用角色
    - 无显式角色时回退为 employee
    """
    if getattr(current_user, "is_superuser", False):
        return {"admin"}

    try:
        from app.models.employee_role import EmployeeRole
    except ImportError as e:
        raise RuntimeError(f"EmployeeRole model not found - RBAC will not work: {e}")

    result = await db.execute(
        select(EmployeeRole.role_name).where(
            EmployeeRole.employee_id == current_user.id,
            EmployeeRole.is_active == True,
        )
    )
    roles = {
        str(role_name or "").strip().lower()
        for (role_name,) in result.all()
        if str(role_name or "").strip()
    }
    return roles or {"employee"}


def require_roles(*roles: str):
    """
    RBAC 权限控制工厂函数：返回一个检查用户是否具有指定角色的 FastAPI 依赖。

    这是一个高阶函数（工厂模式），接收角色名称列表，
    返回一个可被 FastAPI Depends() 使用的异步依赖函数。

    权限检查逻辑：
        1. 先调用 get_current_user 完成身份认证
        2. 若 is_superuser=True，直接通过（超级管理员免检）
        3. 查询 employee_roles 表，检查是否存在匹配的活跃角色记录
        4. 无匹配角色 → 抛出 403 异常

    Args:
        *roles (str): 允许访问的角色名称，可传多个（OR 关系）
                      取值：admin / asset_admin / hr / manager / finance / employee

    Returns:
        Callable: 一个异步依赖函数，可直接用于 Depends()

    Raises:
        HTTPException(403): 用户无任何指定角色
        RuntimeError:       EmployeeRole 模型导入失败（配置错误）

    调用示例：
        # 只允许 admin 或 hr 访问
        @router.post("/employees")
        async def create_employee(
            _user: Employee = Depends(require_roles("admin", "hr"))
        ):
            ...

        # 只允许 admin 访问
        @router.delete("/employees/{id}")
        async def delete_employee(
            _user: Employee = Depends(require_roles("admin"))
        ):
            ...

    注意：
        require_roles() 内部已调用 get_current_user，无需重复注入，
        但如果路由函数需要使用 current_user 对象，应同时注入两者。
    """
    async def dependency(
        current_user: Employee = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> Employee:
        """实际执行角色检查的依赖函数（由 require_roles 工厂生成）。"""

        # 超级管理员（is_superuser=True）绕过所有角色检查
        if getattr(current_user, "is_superuser", False):
            return current_user

        # 延迟导入避免循环引用；导入失败时抛出 RuntimeError（不再静默忽略）
        try:
            from app.models.employee_role import EmployeeRole
        except ImportError as e:
            raise RuntimeError(f"EmployeeRole model not found - RBAC will not work: {e}")

        # 查询 employee_roles 表：员工 ID + 角色名在指定列表中 + 角色处于激活状态
        result = await db.execute(
            select(EmployeeRole).where(
                EmployeeRole.employee_id == current_user.id,
                EmployeeRole.role_name.in_(list(roles)),  # OR 关系：满足任意一个角色即可
                EmployeeRole.is_active == True,
            )
        )
        if not result.scalars().first():
            raise HTTPException(
                status_code=403,
                detail=f"需要权限: {', '.join(roles)}"
            )
        return current_user

    return dependency
