"""
=============================================================================
模块：Core / Security
文件：app/core/security.py
=============================================================================
作用：
    安全工具函数集合。提供 JWT Token 生成和密码哈希/验证功能，
    是系统认证鉴权的底层工具层。

功能：
    - 生成 JWT Access Token（HS256 签名）
    - 验证明文密码与 bcrypt 哈希是否匹配
    - 将明文密码转换为 bcrypt 哈希存储

系统认证流程：
    【登录】
    用户输入手机号+密码
        ↓
    services/employee.py → authenticate()
        ↓
    verify_password(明文, DB中哈希)  ← 本文件
        ↓
    create_access_token(user_id)     ← 本文件
        ↓
    返回 JWT Token 给前端

    【请求鉴权】
    前端携带 Bearer Token
        ↓
    core/deps.py → get_current_user()
        ↓
    jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        ↓
    提取 sub (user_id) 查询数据库

调用关系：
    被调用方：
        app/services/employee.py → authenticate() 登录验证
        app/api/v1/auth.py       → 登录接口返回 token
    调用方：
        app/core/config.py → SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

依赖库：
    python-jose[cryptography]  → JWT 编解码（jose 库）
    passlib[bcrypt]            → 密码哈希（bcrypt 算法）
    bcrypt==3.2.2              → 固定版本，兼容 Python 3.13 + passlib

注意：
    bcrypt 版本已固定为 3.2.2，不可随意升级，
    更高版本与 passlib 在 Python 3.13 环境下存在兼容性问题。
=============================================================================
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

# ── 密码加密上下文 ─────────────────────────────────────────────────────────────
# schemes=["bcrypt"]：使用 bcrypt 算法（行业标准，自带盐值，抗彩虹表）
# deprecated="auto"：旧算法哈希会在验证时自动提示需要重新哈希（向前兼容）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 新建员工、批量导入和本地初始化普通账号的统一初始密码。
DEFAULT_INITIAL_PASSWORD = "585858"

# 本地测试环境与发布初始化后的固定管理员登录账号。
DEFAULT_ADMIN_EMPLOYEE_NO = "TG000001"
DEFAULT_ADMIN_LOGIN_ACCOUNT = "admin"
# 管理员密码由初始化环境变量提供，不保存在源码中。


def create_access_token(subject: str, expires_delta: Optional[timedelta] = None, *, identity: dict | None = None) -> str:
    """
    生成 JWT Access Token。

    Token Payload 结构：
        {
            "sub": "<user_id_str>",  # 用户 ID（字符串格式，JWT 标准字段）
            "exp": <unix_timestamp>  # 过期时间（UTC Unix 时间戳）
        }

    签名算法：HS256（HMAC-SHA256），使用 config.SECRET_KEY 作为密钥。

    Args:
        subject (str): Token 主体，通常为员工 ID（会被转换为字符串）。
                       注意：解码时需用 int() 转回整数，见 deps.py。
        expires_delta (Optional[timedelta]): 自定义有效期。
                       若为 None，使用配置中的 ACCESS_TOKEN_EXPIRE_MINUTES（默认 120 分钟）。

    Returns:
        str: 已签名的 JWT 字符串，格式为 "xxxxx.yyyyy.zzzzz"

    调用示例：
        # 登录成功后生成 token
        token = create_access_token(subject=str(employee.id))
        return {"access_token": token, "token_type": "bearer"}
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        # 使用全局默认过期时间（从配置读取）
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    # 构建 payload，sub 字段统一转为字符串（JWT 规范要求 sub 为字符串）
    to_encode = {"exp": expire, "sub": str(subject), "auth_method": "password"}
    if identity:
        to_encode.update(identity)
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    验证明文密码与存储的 bcrypt 哈希是否匹配。

    bcrypt 验证原理：
        从哈希字符串中提取盐值 → 对明文密码重新哈希 → 比较结果
        整个过程在 passlib 内部完成，抗时序攻击（constant-time 比较）。

    Args:
        plain_password (str):  用户输入的明文密码
        hashed_password (str): 数据库中存储的 bcrypt 哈希值
                               格式如：$2b$12$xxxxxx...

    Returns:
        bool: 密码匹配返回 True，不匹配返回 False

    调用方：
        app/services/employee.py → Employee.authenticate()
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    将明文密码转换为 bcrypt 哈希，用于注册/修改密码时安全存储。

    bcrypt 特点：
        - 自动生成随机盐值（每次哈希结果不同，相同明文哈希值不同）
        - 计算耗时可调（cost factor），默认 rounds=12，抗暴力破解
        - 哈希字符串中包含算法标识、盐值和哈希值，便于自描述

    Args:
        password (str): 明文密码字符串

    Returns:
        str: bcrypt 哈希字符串，格式为 "$2b$12$<salt><hash>"，长度固定 60 字符

    调用方：
        app/services/employee.py → EmployeeService.create_employee()
        app/api/v1/auth.py       → 修改密码接口
    """
    return pwd_context.hash(password)
