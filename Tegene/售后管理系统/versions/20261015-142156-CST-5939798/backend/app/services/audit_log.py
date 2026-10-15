"""
操作审计日志服务 - Audit Log Service
======================================

本模块负责售后管理系统中所有关键操作的审计日志记录与查询，
为合规审计、问题追溯和安全监控提供数据支撑。

设计原则（Append-Only 架构）：
    审计日志一经写入，不可修改、不可删除。
    - 不提供 update 方法
    - 不提供 delete 方法
    - create 方法直接调用 db.commit()（而非 db.flush()），
      确保日志记录独立提交，不受业务事务回滚影响

核心数据结构（AuditLog 表字段）：
    operator_id  -- 操作人员工 ID，None 表示系统自动操作（定时任务/批量处理）
    module       -- 业务模块（employee/leave/payroll/attendance/approval 等）
    action       -- 操作类型（create/update/delete/approve/reject/login 等）
    resource_id  -- 被操作资源的主键 ID（如员工 ID、合同 ID）
    before_data  -- 操作前的数据快照（JSON 字符串，可为 None）
    after_data   -- 操作后的数据快照（JSON 字符串，可为 None）
    ip_address   -- 操作人的客户端 IP（可为 None，批量任务无 IP）
    remark       -- 操作备注（可为 None）

功能说明：

1. **记录操作（create）**
   - 直接提交到数据库（db.commit），不依赖外层事务
   - 其他 service 在关键操作后调用此方法记录日志
   - before_data/after_data 为 JSON 字符串，记录操作前后的完整数据快照

2. **查询日志（list_logs）**
   - 支持按操作人、操作类型、业务模块、时间段复合过滤
   - 默认倒序排列（最新操作在前），最多返回 limit 条
   - 无需分页总数（审计查询通常不需要精确总数）

3. **统计日志总数（count_logs）**
   - 返回全库日志条目总数（系统健康检查/报表用途）

调用关系：
    其他 service（employee/leave/payroll 等）→ AuditLogService.create() 记录日志
    api/v1/audit_log.py → AuditLogService.list_logs() / count_logs() 查询日志

典型调用场景：
    # 在 employee service 中记录员工信息修改
    await AuditLogService.create(db, AuditLogCreate(
        operator_id=current_user.id,
        module="employee",
        action="update",
        resource_id=employee_id,
        before_data=json.dumps(old_data),
        after_data=json.dumps(new_data),
        ip_address=request.client.host,
    ))

依赖框架：FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogCreate


class AuditLogService:
    """
    操作审计日志服务（无状态类，所有方法为 staticmethod）。

    重要：所有方法均设计为 staticmethod，便于在任何 service 中直接调用。
    """

    @staticmethod
    async def create(db: AsyncSession, data: AuditLogCreate) -> AuditLog:
        """
        记录一条操作审计日志（直接提交，不受外层事务影响）。

        重要设计决策：
            本方法调用 db.commit() 而非 db.flush()。
            这是审计日志特有的设计：即使业务操作随后失败回滚，
            审计日志本身也应保留（记录"曾经尝试过"的操作）。

            注意：这意味着调用此方法会提交当前会话中所有未提交的更改。
            建议在业务操作成功后再调用此方法（而非在事务中途调用）。

        operator_id 说明：
            - 普通用户操作：传入当前登录用户的员工 ID
            - 系统自动操作（定时任务/批量处理）：传入 None

        before_data / after_data 说明：
            - 建议格式：json.dumps(model.model_dump())
            - 对于 delete 操作：before_data 记录删除前的数据，after_data 为 None
            - 对于 create 操作：before_data 为 None，after_data 记录创建后的数据
            - 对于 update 操作：两者均记录，便于对比变更

        参数：
            db   -- 异步数据库会话
            data -- AuditLogCreate schema，包含：
                    operator_id  -- 操作人员工 ID（None 表示系统自动）
                    module       -- 业务模块标识符
                    action       -- 操作类型标识符
                    resource_id  -- 被操作资源主键（可选）
                    before_data  -- 操作前数据快照 JSON（可选）
                    after_data   -- 操作后数据快照 JSON（可选）
                    ip_address   -- 客户端 IP 地址（可选）
                    remark       -- 操作备注（可选）

        返回：
            已提交的 AuditLog ORM 对象
        """
        log = AuditLog(**data.model_dump())
        db.add(log)
        # 直接 commit，而非 flush。确保日志记录独立于业务事务，不可回滚
        await db.commit()
        return log

    @staticmethod
    async def list_logs(
        db: AsyncSession,
        operator_id: Optional[int] = None,
        action: Optional[str] = None,
        module: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
        resource_types: Optional[list[str]] = None,
    ):
        """
        查询操作审计日志列表，支持多条件复合过滤。

        查询条件说明：
            operator_id -- 精确匹配操作人（查某人的所有操作记录）
            action      -- 精确匹配操作类型（如 "delete" 查所有删除操作）
            module      -- 精确匹配业务模块（如 "payroll" 查薪资模块的所有操作）
            date_from   -- 操作时间下界（created_at >= date_from）
            date_to     -- 操作时间上界（created_at <= date_to）

        多条件均为 AND 关系（同时满足所有传入的过滤条件）。

        参数：
            db          -- 异步数据库会话
            operator_id -- 按操作人 ID 过滤，None 表示不过滤
            action      -- 按操作类型精确过滤，None 表示不过滤
            module      -- 按业务模块精确过滤，None 表示不过滤
            date_from   -- 查询时间范围起始（含），None 表示不限
            date_to     -- 查询时间范围结束（含），None 表示不限
            skip        -- 分页偏移量（跳过的记录数）
            limit       -- 最大返回记录数，默认 100（审计查询通常需要较多记录）

        返回：
            AuditLog ORM 对象列表，按 created_at 倒序排列（最新操作在前）
        """
        q = select(AuditLog)
        # 动态添加过滤条件（只有传入非 None 值时才添加）
        if operator_id is not None:
            q = q.where(AuditLog.operator_id == operator_id)
        if action:
            q = q.where(AuditLog.action == action)
        if module:
            q = q.where(AuditLog.module == module)
        if resource_types:
            q = q.where(AuditLog.resource_type.in_(resource_types))
        if date_from:
            q = q.where(AuditLog.created_at >= date_from)  # 时间范围下界
        if date_to:
            q = q.where(AuditLog.created_at <= date_to)    # 时间范围上界
        # 最新操作在前，按时间倒序排列
        q = q.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(q)
        return result.scalars().all()

    @staticmethod
    async def count_logs(db: AsyncSession) -> int:
        """
        统计审计日志总条数（不含任何过滤条件）。

        用途：
            - 系统健康监控（日志量是否异常增长）
            - 管理报表（日志系统使用情况）
            - 存储容量规划参考

        参数：
            db -- 异步数据库会话

        返回：
            audit_logs 表中的总记录数（整数）
        """
        q = select(func.count(AuditLog.id))
        result = await db.execute(q)
        return result.scalar() or 0
