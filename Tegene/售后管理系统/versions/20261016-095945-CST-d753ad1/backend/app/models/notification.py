"""
通知中心模型 - Notification Model
====================================

本模块定义站内消息/通知数据表，用于向员工推送系统内各类业务事件的实时提醒，
实现"业务发生 → 通知触发 → 用户感知"的闭环。

业务背景
--------
售后管理系统的通知中心（站内信）是各业务模块与用户之间的消息枢纽。
当以下业务事件发生时，对应的服务层会自动创建通知记录并推送给相关员工：

  招聘模块
    - 面试邀请确认（notif_type: interview）
    - Offer 发出/候选人接受/拒绝通知（notif_type: offer）

  假期模块
    - 请假申请状态变更（审批通过/驳回）（notif_type: leave）

  审批模块
    - 需要本人审批的事项待办提醒（notif_type: approval）
    - 自己发起的审批流程状态变更通知

  系统告警
    - 考勤异常提醒、合同到期提醒等（notif_type: alert）

  系统消息
    - 系统维护公告、HR 群发通知等（notif_type: system）

消息关联机制（ref_type + ref_id）
----------------------------------
每条通知可通过 ref_type + ref_id 关联到具体的业务对象，
前端收到通知后可据此跳转到对应的业务详情页：

  ref_type = "interview",        ref_id = 面试记录 ID
  ref_type = "offer",            ref_id = Offer 记录 ID
  ref_type = "leave_request",    ref_id = 请假申请 ID
  ref_type = "approval_instance", ref_id = 审批实例 ID

若通知无需关联业务对象（如纯文本系统公告），两者均为 NULL。

消息读取状态
------------
is_read 字段用于"未读消息数"角标展示和"全部已读"功能。
前端定期轮询 GET /api/v1/notifications/unread-count 查询未读数，
用户打开通知列表时批量调用 PATCH /api/v1/notifications/read-all 标记已读。

系统消息 vs 人工消息
--------------------
sender_id = NULL 表示系统自动产生的通知（无明确发件人），
如考勤异常告警、合同到期提醒等自动化触发的事件。
sender_id 有值时表示某员工手动发送的消息（如 HR 发布的定向通知）。

包含数据表
----------
- notifications      站内消息通知记录
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Notification(Base):
    """
    站内消息/通知记录，系统各模块向员工推送业务事件提醒的统一数据表。

    每条记录代表向某位员工（recipient_id）发送的一条通知消息。
    消息可由另一员工（sender_id）手动发送，也可由系统自动创建（sender_id=NULL）。

    通知类型（notif_type）
    ----------------------
    interview  — 面试相关通知（面试邀请、面试时间变更、面试结果）
    offer      — Offer 相关通知（Offer 已发出、候选人接受/拒绝确认）
    leave      — 请假相关通知（审批通过、审批驳回、补充材料要求）
    approval   — 审批流通知（新增待审批事项、审批完成回告）
    alert      — 系统告警（考勤异常、合同即将到期、证书到期提醒）
    system     — 系统公告（系统维护通知、全员 HR 公告）

    关联对象（ref_type + ref_id）
    -----------------------------
    用于前端消息点击后的智能跳转路由，例如：
      - ref_type="leave_request", ref_id=42 → 跳转到 /leave/requests/42 详情页
      - ref_type="interview",     ref_id=15 → 跳转到 /recruitment/interviews/15
      - ref_type=NULL             → 无跳转目标，仅显示消息文本

    已读状态管理
    ------------
    is_read 默认为 False（未读），建立了索引以支持高效查询：
      SELECT COUNT(*) WHERE recipient_id=? AND is_read=FALSE  — 未读数查询
      UPDATE SET is_read=TRUE WHERE recipient_id=?             — 全部标为已读

    消息删除策略
    ------------
    - 接收人（recipient_id）员工删除时，其所有通知级联删除（ondelete="CASCADE"）
    - 发送人（sender_id）员工删除时，发出的通知 sender_id 置 NULL，消息本身保留
      （避免系统通知因"发送人"不存在而丢失）
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 接收人员工 ID，接收人离职/删除时级联清除其所有通知记录
    recipient_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="CASCADE"), index=True, comment="接收人员工ID"
    )
    # 发送人员工 ID；NULL 表示系统自动触发的通知（无人工发送者）
    sender_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, comment="发送人员工ID(系统消息为NULL)"
    )
    # 通知标题，显示在消息列表中（如"您有一条待审批请假申请"）
    title: Mapped[str] = mapped_column(String(200), comment="通知标题")
    # 通知正文，可选，用于展示更详细的上下文信息
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="通知内容")
    # 通知类型，用于前端按类型筛选消息和渲染不同的图标/样式
    notif_type: Mapped[str] = mapped_column(
        String(50), index=True, comment="通知类型: interview/offer/leave/approval/alert/system"
    )
    # 关联业务对象的类型名称，配合 ref_id 实现消息点击跳转；无关联时为 NULL
    ref_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="关联对象类型: interview/offer/leave_request等"
    )
    # 关联业务对象的数据库主键 ID；配合 ref_type 唯一定位业务记录；无关联时为 NULL
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="关联对象ID")
    # 已读标志，默认 False（未读）；建立索引以支持高效的未读数统计查询
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="是否已读")
    # 通知创建时间，即消息发送时间
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
