"""
通知中心业务逻辑服务 - Notification Service
============================================

本模块负责售后管理系统中系统通知的全生命周期管理，
为员工提供站内消息通知功能（类似微信消息的已读/未读机制）。

核心功能：

1. **创建通知（create）**
   - 将系统事件转化为具体通知消息，写入数据库
   - sender_id=None 表示系统自动发送（如合同到期提醒、生日祝福）
   - ref_type + ref_id 用于前端跳转（如 ref_type="leave", ref_id=123 → 跳转到请假详情）
   - notif_type 区分通知类型（如 "approval"/"system"/"reminder"）

2. **查询通知列表（list_for_user）**
   - 按接收人 user_id 过滤，支持仅看未读（unread_only=True）
   - 分页查询，按创建时间倒序（最新通知在前）

3. **获取未读数（get_unread_count）**
   - 轻量级查询，只统计未读数量（is_read=False）
   - 前端轮询此接口更新小红点/Badge 数字

4. **标记单条已读（mark_read）**
   - 更新单条通知 is_read=True
   - 同时校验归属权（recipient_id == user_id），防止越权标记他人通知

5. **标记全部已读（mark_all_read）**
   - 批量 UPDATE 该用户所有未读通知
   - 返回实际影响的行数

数据流（典型场景）：
    请假申请审批通过
    → approval service 调用 NotificationService.create()
    → Notification 记录写入数据库
    → 员工下次打开系统，前端调用 get_unread_count 显示红点
    → 员工点击通知，前端调用 list_for_user 展示通知列表
    → 员工阅读后，前端调用 mark_read 或 mark_all_read

调用关系：
    api/v1/notification.py → services/notification.py（本文件）
    其他 service（如 approval、leave）可直接调用 NotificationService.create()

依赖框架：FastAPI + SQLAlchemy 2.0 Async + PostgreSQL + Pydantic v2
"""

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification
from app.schemas.notification import NotificationCreate


class NotificationService:
    """
    通知中心服务（无状态类，所有方法为 staticmethod）。

    设计为 staticmethod 的原因：
        通知操作不依赖实例状态，便于在其他 service 中直接调用而无需实例化。
    """

    @staticmethod
    def _visible_inbox_condition():
        """
        消息中心不展示上下班打卡同步历史消息。

        这些消息只说明某员工完成了一次打卡，业务数据已经沉淀到考勤记录和报表，
        不应再作为主管、HR 或管理员的站内通知待处理。
        """
        return ~or_(
            Notification.notif_type == "attendance",
            Notification.ref_type == "attendance_record",
            Notification.title.like("考勤同步：%"),
        )

    @staticmethod
    async def create(db: AsyncSession, data: NotificationCreate) -> Notification:
        """
        创建一条系统通知记录。

        业务说明：
            - 可由其他 service 调用（如审批通过后发送通知）
            - sender_id=None 表示系统自动发送（无具体发件人），前端展示为"系统消息"
            - ref_type + ref_id 组合用于前端路由跳转，格式与前端路由规则约定一致

        参数：
            db   -- 异步数据库会话
            data -- NotificationCreate schema，包含：
                    recipient_id  -- 接收人员工 ID（必填）
                    sender_id     -- 发送人员工 ID（可选，None 表示系统）
                    title         -- 通知标题（简短）
                    content       -- 通知正文（详细内容）
                    notif_type    -- 通知类型（approval/system/reminder/alert 等）
                    ref_type      -- 关联业务类型（leave/attendance/payroll 等，可选）
                    ref_id        -- 关联业务记录 ID（与 ref_type 配合使用，可选）

        返回：
            新建的 Notification ORM 对象（is_read=False，created_at 由数据库自动填充）
        """
        notif = Notification(
            recipient_id=data.recipient_id,
            sender_id=data.sender_id,    # None 表示系统自动发送
            title=data.title,
            content=data.content,
            notif_type=data.notif_type,
            ref_type=data.ref_type,      # 关联业务类型，用于前端跳转
            ref_id=data.ref_id,          # 关联业务 ID，与 ref_type 配合
        )
        db.add(notif)
        await db.flush()
        await db.refresh(notif)
        return notif

    @staticmethod
    async def list_for_user(
        db: AsyncSession,
        user_id: int,
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False,
    ) -> tuple[list[Notification], int]:
        """
        分页查询某用户的通知列表。

        参数：
            db          -- 异步数据库会话
            user_id     -- 接收人员工 ID（只能查询自己的通知）
            page        -- 页码，从 1 开始
            page_size   -- 每页记录数，默认 20
            unread_only -- True：只返回未读通知；False：返回全部通知

        返回：
            (items, total) 元组：
            - items -- 当前页的 Notification 列表，按创建时间倒序（最新在前）
            - total -- 满足条件的通知总数（用于前端分页控件）
        """
        q = select(Notification).where(
            Notification.recipient_id == user_id,
            NotificationService._visible_inbox_condition(),
        )
        if unread_only:
            q = q.where(Notification.is_read.is_(False))  # 只查未读通知

        # 计算满足条件的总数（用子查询确保 WHERE 条件一致）
        count_q = select(func.count()).select_from(q.subquery())
        total = (await db.execute(count_q)).scalar_one()

        # 分页查询，按创建时间倒序排列
        items_q = (
            q.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)  # 计算跳过的记录数
            .limit(page_size)
        )
        result = await db.execute(items_q)
        items = list(result.scalars().all())
        return items, total

    @staticmethod
    async def get_unread_count(db: AsyncSession, user_id: int) -> int:
        """
        获取某用户的未读通知数量（用于前端红点/Badge 显示）。

        性能说明：
            此方法设计为高频调用接口（前端可能每隔 30 秒轮询一次），
            使用 COUNT 查询而非加载完整记录，性能更优。

        参数：
            db      -- 异步数据库会话
            user_id -- 接收人员工 ID

        返回：
            该用户当前未读通知数量（整数），0 表示无未读通知
        """
        q = select(func.count()).where(
            Notification.recipient_id == user_id,
            Notification.is_read.is_(False),  # 只统计未读通知
            NotificationService._visible_inbox_condition(),
        )
        count = (await db.execute(q)).scalar_one()
        return count

    @staticmethod
    async def mark_read(db: AsyncSession, notif_id: int, user_id: int) -> bool:
        """
        将指定通知标记为已读（含归属权校验）。

        安全说明：
            同时校验 id == notif_id AND recipient_id == user_id，
            防止用户 A 将用户 B 的通知标记为已读（越权访问）。

        参数：
            db       -- 异步数据库会话
            notif_id -- 通知主键 ID
            user_id  -- 当前登录用户的员工 ID

        返回：
            True  -- 成功标记为已读（找到并更新了记录）
            False -- 通知不存在或不属于当前用户（更新影响行数为 0）
        """
        stmt = (
            update(Notification)
            .where(
                Notification.id == notif_id,
                Notification.recipient_id == user_id,  # 归属权校验，防越权
            )
            .values(is_read=True)
        )
        result = await db.execute(stmt)
        return result.rowcount > 0  # rowcount > 0 表示实际更新了记录

    @staticmethod
    async def mark_read_by_ref(
        db: AsyncSession,
        *,
        user_id: int,
        notif_type: str,
        ref_type: str,
        ref_id: int,
    ) -> int:
        """
        按业务关联批量标记当前用户通知为已读。

        用于“进入业务详情即视为读过对应消息”的场景，例如用户打开某个审批详情后，
        只清理该审批单关联的审批通知，保留公告、考勤、系统消息等其他未读提醒。
        """
        stmt = (
            update(Notification)
            .where(
                Notification.recipient_id == user_id,
                Notification.notif_type == notif_type,
                Notification.ref_type == ref_type,
                Notification.ref_id == ref_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )
        result = await db.execute(stmt)
        return result.rowcount or 0

    @staticmethod
    async def mark_all_read(db: AsyncSession, user_id: int) -> int:
        """
        将某用户的所有未读通知批量标记为已读。

        性能说明：
            使用 bulk UPDATE（一条 SQL），不逐条加载通知对象，
            效率远高于循环调用 mark_read()。

        参数：
            db      -- 异步数据库会话
            user_id -- 当前登录用户的员工 ID

        返回：
            实际被标记为已读的通知数量（影响行数）。
            若该用户本无未读通知，返回 0。
        """
        stmt = (
            update(Notification)
            .where(
                Notification.recipient_id == user_id,
                Notification.is_read.is_(False),  # 只更新未读的，已读的跳过
            )
            .values(is_read=True)
        )
        result = await db.execute(stmt)
        return result.rowcount  # 返回实际更新的行数
