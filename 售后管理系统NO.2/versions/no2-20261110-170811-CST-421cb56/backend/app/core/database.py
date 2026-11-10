"""
=============================================================================
模块：Core / Database
文件：app/core/database.py
=============================================================================
作用：
    数据库连接层。创建 SQLAlchemy 异步引擎和 Session 工厂，
    提供 FastAPI 依赖注入用的 get_db() 生成器，
    以及所有 ORM 模型继承的 Base 基类。

功能：
    - 初始化 SQLAlchemy 2.0 异步引擎（支持 asyncpg / aiomysql）
    - 管理数据库连接池（pool_size=20，最大溢出 10）
    - 提供事务自动提交 / 回滚的 Session 生命周期管理
    - 提供 DeclarativeBase 供所有 ORM 模型继承

调用关系：
    ┌──────────────────────────────────────────────────────┐
    │  config.py      → 提供 DATABASE_URL                 │
    │  models/*.py    → 继承 Base 定义表结构               │
    │  api/v1/*.py    → Depends(get_db) 注入 Session       │
    │  services/*.py  → 接收 Session 执行业务查询          │
    └──────────────────────────────────────────────────────┘

连接池参数说明：
    pool_size=20        常驻连接数，应对并发请求
    max_overflow=10     超出 pool_size 时允许额外创建的临时连接数
    pool_pre_ping=True  非 MySQL aiomysql 后端启用连接健康检查
    pool_recycle=3600   连接最长保活 1 小时后强制回收，防止 MySQL 服务端超时断连

注意：
    - 默认使用 MySQL + aiomysql；仅在显式配置 postgresql 时使用 asyncpg
    - DateTime(timezone=True) 在 MySQL 中映射为 DATETIME，当前业务按 MySQL 语义运行
    - expire_on_commit=False 避免 commit 后访问对象属性触发懒加载异常
=============================================================================
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# ── 异步数据库引擎 ─────────────────────────────────────────────────────────────
# echo=False: 关闭 SQL 语句日志输出（生产环境）
# 如需调试 SQL 可临时改为 echo=True
_engine_options = {
    "echo": False,
}

if not settings.DATABASE_URL.startswith("sqlite"):
    _engine_options.update(
        {
            "pool_size": 20,       # 连接池基础容量（并发 Worker 数应 ≤ pool_size + max_overflow）
            "max_overflow": 10,    # 超过 pool_size 后的临时扩展上限
            "pool_recycle": 3600,  # 每小时回收一次连接，防止 MySQL 服务端超时断连
        }
    )
    # SQLAlchemy 2.0.35 的 PyMySQL ping 调用与 aiomysql 适配层签名不一致，
    # 正式 MySQL 环境依赖 pool_recycle 回收连接，避免 pre_ping 触发 500。
    if not settings.DATABASE_URL.startswith("mysql+aiomysql"):
        _engine_options["pool_pre_ping"] = True

engine = create_async_engine(settings.DATABASE_URL, **_engine_options)
if settings.DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event
    @event.listens_for(engine.sync_engine, "connect")
    def sqlite_settings(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


# ── Session 工厂 ──────────────────────────────────────────────────────────────
# async_sessionmaker: SQLAlchemy 2.0 推荐的异步 Session 工厂
# expire_on_commit=False: commit 后 ORM 对象属性不过期，
#   避免在 endpoint 返回 response 时触发额外的数据库查询
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """
    所有 ORM 模型的基类。

    所有 app/models/*.py 中的数据模型都继承此类。
    DeclarativeBase 提供：
      - __tablename__ 映射
      - Column 字段定义
      - relationship 关联定义
      - Alembic 迁移自动检测（通过 Base.metadata）

    使用方式：
        from app.core.database import Base

        class MyModel(Base):
            __tablename__ = "my_table"
            id = Column(Integer, primary_key=True)
    """
    pass


async def get_db() -> AsyncSession:
    """
    FastAPI 依赖注入：提供数据库 Session，自动管理事务生命周期。

    这是一个异步生成器函数，配合 FastAPI 的 Depends() 使用。
    整个请求处理周期内只创建一个 Session，请求结束后自动清理。

    事务管理策略：
        - 正常流程：yield 后 await commit()（自动提交）
        - 异常流程：捕获异常后 await rollback()，再重新抛出
        - 无论如何：finally 块中 await close() 归还连接到连接池

    Args:
        无（由 FastAPI DI 框架自动调用）

    Yields:
        AsyncSession: SQLAlchemy 异步会话对象，可直接执行 ORM 操作

    调用示例（在 API 路由中）：
        async def my_endpoint(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(MyModel))
            return result.scalars().all()

    注意：
        Service 层接收 db 参数后不要自行 commit/rollback，
        统一由本函数在请求结束时处理，避免事务状态混乱。
        Service 层如需在同一请求中多步操作可使用 db.flush()。
    """
    async with async_session() as session:
        try:
            yield session          # 将 session 注入到路由处理函数
            await session.commit() # 请求正常结束，提交所有变更
        except Exception:
            await session.rollback()  # 发生异常，回滚所有未提交操作
            raise
        finally:
            await session.close()  # 归还连接到连接池
