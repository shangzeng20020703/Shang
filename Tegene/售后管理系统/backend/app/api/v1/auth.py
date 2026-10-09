"""
认证模块 API 路由
=================

路径前缀：/api/v1/auth
标签（Swagger）：认证

本模块提供以下端点：
  POST /auth/login           — 员工端通用账号/手机号 + 密码 JSON 登录，返回 JWT Bearer Token
  POST /auth/management-login — 管理端账号/手机号 + 密码登录，需具备管理端 RBAC 角色
  GET  /auth/me              — 获取当前已认证用户的详细信息
  POST /auth/change-password — 当前登录用户修改自己的密码

权限说明：
  - /login      无需认证（公开端点），但有限流保护（5次/分钟，基于 slowapi）
  - /me         需要有效 JWT Token（Depends(get_current_user)）

调用的 Service：
  - EmployeeService.authenticate(db, phone, password) — 验证账号/手机号和密码，返回 Employee 对象或 None
  - create_access_token(subject)                      — 生成 JWT access_token（core/security.py）

重要注意事项：
  - 登录端点接受 **JSON 格式** 请求体（{"phone": "...", "password": "..."}），
    其中 phone 字段即员工档案中保存的手机号。
    而非 OAuth2 标准的 form-urlencoded 格式。
  - 登录限流：同一 IP 每分钟最多 5 次请求（@limiter.limit("5/minute")）。
    限流依赖 slowapi，处理函数第一个参数必须是 `request: Request`。
  - JWT sub 字段存储员工 ID 的字符串形式（str(employee.id)），
    解码时需用 int() 转换（见 core/deps.py）。
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, get_user_role_names, require_roles
from app.core.limiter import limiter
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models.employee import Employee
from app.schemas.employee import ChangePasswordRequest, EmployeeOut, LoginRequest, Token
from app.services.employee import EmployeeService

from app.api.v1.wecom_auth import router as wecom_router

router = APIRouter()
router.include_router(wecom_router)

MANAGEMENT_LOGIN_ROLES = {"admin", "hr", "manager"}


def has_management_login_role(role_names: set[str]) -> bool:
    """判断角色集合是否允许进入 Web 管理端。"""
    normalized = {str(role or "").strip().lower() for role in role_names if str(role or "").strip()}
    return bool(normalized & MANAGEMENT_LOGIN_ROLES)


async def _authenticate_for_login(
    db: AsyncSession,
    data: LoginRequest,
) -> Employee:
    if settings.LOGIN_MODE == "wecom_only":
        raise HTTPException(403, "正式环境仅允许企业微信登录")
    employee = await EmployeeService.authenticate(db, data.phone, data.password)
    if not employee:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="手机号或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not employee.is_active or employee.status not in {"在职", "试用"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被停用",
        )
    return employee


def get_login_token_expires_delta(remember_me: bool) -> timedelta | None:
    """根据登录端选择决定是否签发保持登录长效 Token。"""
    if not remember_me:
        return None
    return timedelta(days=settings.REMEMBER_ME_ACCESS_TOKEN_EXPIRE_DAYS)


def _issue_login_token(employee: Employee, remember_me: bool = False) -> Token:
    # 生成 JWT，sub 字段存储员工 ID 的字符串形式
    access_token = create_access_token(
        subject=str(employee.id),
        expires_delta=get_login_token_expires_delta(remember_me),
    )
    return Token(access_token=access_token)


@router.post("/login", response_model=Token, summary="账号/手机号+密码登录")
@limiter.limit("5/minute")  # 限流：每 IP 每分钟最多 5 次，防暴力破解
async def login(
    request: Request,        # slowapi 限流必须的参数，需作为第一个参数
    data: LoginRequest,      # 请求体：{"phone": "13800000001", "password": "明文密码"}
    db: AsyncSession = Depends(get_db),
):
    """
    用户登录接口。

    HTTP 方法：POST
    路径：/api/v1/auth/login
    权限：公开（无需 Token），但有限流保护（5次/分钟/IP）

    请求体（JSON）：
        phone       (str, 必填): 登录账号或员工手机号，字段名为历史兼容保留
        password    (str, 必填): 明文密码（服务端使用 bcrypt 验证哈希）
        remember_me (bool, 可选): 是否保持登录；移动端默认开启，避免频繁重复输入

    业务流程：
        1. 调用 EmployeeService.authenticate() 验证手机号和密码。
        2. 若认证失败（账号不存在或密码错误），返回 401 Unauthorized。
        3. 若账号已被停用（is_active=False），返回 403 Forbidden。
        4. 认证成功，调用 create_access_token() 生成 JWT。
           普通登录有效期由 ACCESS_TOKEN_EXPIRE_MINUTES 控制；
           remember_me 登录有效期由 REMEMBER_ME_ACCESS_TOKEN_EXPIRE_DAYS 控制。

    响应（200 OK）：
        {
            "access_token": "eyJ...",  # JWT Bearer Token
            "token_type": "bearer"
        }

    错误响应：
        401 Unauthorized — 手机号或密码错误（附带 WWW-Authenticate: Bearer 响应头）
        403 Forbidden    — 账号已被停用
        429 Too Many Requests — 触发限流（5次/分钟）
    """
    employee = await _authenticate_for_login(db, data)
    return _issue_login_token(employee, remember_me=data.remember_me)


@router.post("/management-login", response_model=Token, summary="管理端账号/手机号+密码登录")
@limiter.limit("5/minute")
async def management_login(
    request: Request,
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Web 管理端专用登录接口。

    与员工端通用 `/auth/login` 不同，本接口在账号密码验证通过后继续校验 RBAC：
    只有 admin / hr / manager 或超级管理员可以获得管理端 Token。
    普通 employee 账号应使用移动端入口，不能进入管理端。
    """
    employee = await _authenticate_for_login(db, data)
    role_names = await get_user_role_names(db, employee)
    if not has_management_login_role(role_names):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号未开通管理端权限，请使用员工端入口",
        )
    return _issue_login_token(employee)


@router.get("/management-session", response_model=EmployeeOut, summary="复核管理后台访问权限")
async def management_session(
    current_user: Employee = Depends(require_roles(*MANAGEMENT_LOGIN_ROLES)),
):
    return await get_me(current_user)


@router.get("/me", response_model=EmployeeOut, summary="获取当前登录用户信息")
async def get_me(
    current_user: Employee = Depends(get_current_user),  # JWT 解码 + is_active 校验
):
    """
    获取当前已认证用户的详细档案信息。

    HTTP 方法：GET
    路径：/api/v1/auth/me
    权限：需要有效 JWT Token（Depends(get_current_user)）

    业务说明：
        从 JWT Token 中解析当前用户 ID，返回对应员工的完整档案。
        同时将关联的地点名称（location_name）和部门名称（department_name）
        补充到响应对象中，避免前端再次发起额外请求。

    响应（200 OK）：
        EmployeeOut 对象，包含员工基本信息、岗位信息，以及：
            location_name   (str | None): 工作地点名称（来自关联的 Location 对象）
            department_name (str | None): 部门名称（来自关联的 Department 对象）

    错误响应：
        401 Unauthorized — Token 缺失、无效或已过期
        403 Forbidden    — 账号已被停用
    """
    out = EmployeeOut.model_validate(current_user)
    # 手动填充关联对象的名称字段，避免前端额外查询
    out.location_name = current_user.location.name if current_user.location else None
    out.department_name = current_user.department.name if current_user.department else None
    return out


@router.post("/change-password", summary="修改当前用户密码")
async def change_password(
    data: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    当前登录用户修改自己的登录密码。

    业务规则：
      - 必须校验原密码，避免已登录设备被他人直接改密。
      - 新密码不能与原密码相同。
      - 只更新当前用户自己的 hashed_password，不暴露任何密码明文。
    """
    if not verify_password(data.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当前密码不正确",
        )
    if verify_password(data.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码不能与当前密码相同",
        )
    current_user.hashed_password = get_password_hash(data.new_password)
    await db.flush()
    return {"message": "密码修改成功"}
