"""
模块名称: notification.py
模块作用: 系统通知/消息中心的 Pydantic Schemas — 管理站内消息的创建与展示

业务背景:
    售后管理系统在以下场景自动推送通知给相关员工:
      - 审批流程节点流转（审批通过/驳回/待办提醒）
      - 合同/试用期到期预警
      - 请假申请状态变更
      - 绩效评估任务分配
      - 系统公告（由 HR 管理员发布）

    消息中心支持"未读/已读"状态管理，前端通过消息中心图标的红点数字
    展示未读消息数量。

数据流向:
    业务 Service 触发事件
        → NotificationCreate（Service 层构造）
        → notification_service.create_notification()
        → models/notification.py（Notification ORM）
        → PostgreSQL notifications 表

    前端请求消息列表
        → GET /api/v1/notifications
        → notification_service.list_notifications()
        → NotificationListResponse（含 NotificationOut 列表）

    前端标记已读
        → PATCH /api/v1/notifications/{id}/read
        → 更新 is_read=True（无独立 Update Schema，由 Service 直接更新）

被哪些端点使用:
    - GET   /api/v1/notifications           → NotificationListResponse（当前用户消息列表）
    - GET   /api/v1/notifications/unread-count → int（未读数量）
    - PATCH /api/v1/notifications/{id}/read → 标记单条已读（无请求体）
    - PATCH /api/v1/notifications/read-all  → 全部标记已读（无请求体）

设计约束:
    1. NotificationOut 不含 recipient_id / sender_id — 出于安全考虑，
       API 仅返回当前登录用户的消息，无需暴露接收人 ID
    2. 无 NotificationUpdate Schema — 通知不支持内容编辑，只能标记已读
    3. ref_type + ref_id 组合用于前端路由跳转，例如:
         ref_type="leave_request", ref_id=42 → 前端跳转到请假申请详情页
         ref_type="approval_instance", ref_id=7 → 跳转到审批详情页
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    """
    通知响应 Schema — 用于 GET /api/v1/notifications 接口的返回值

    每条记录代表发送给当前登录用户的一条站内消息。
    前端根据 is_read 决定是否高亮显示（未读消息通常加粗或标红点）。

    字段说明:
        id          — 通知主键，自增
        title       — 消息标题，简短摘要，前端列表行展示
        content     — 消息正文，详细说明；可为 None（纯标题通知）
        notif_type  — 通知类型，约定枚举值:
                        "system"   — 系统通知（默认）
                        "approval" — 审批流通知（待办/通过/驳回）
                        "leave"    — 请假相关
                        "contract" — 合同预警
                        "performance" — 绩效任务
        ref_type    — 关联资源类型，配合 ref_id 供前端生成跳转链接；可为 None
        ref_id      — 关联资源 ID，与 ref_type 配合使用；可为 None
        is_read     — 是否已读；False=未读（消息中心显示红点）
        created_at  — 消息创建/发送时间（带时区）

    注意: 出于安全隔离原则，recipient_id 和 sender_id 不在此响应中暴露
    """
    model_config = ConfigDict(from_attributes=True)
    # from_attributes=True: 支持从 SQLAlchemy ORM 对象直接序列化

    id: int
    title: str                              # 消息标题，前端列表行展示
    content: Optional[str] = None          # 消息正文；可为空（如纯标题提醒）
    notif_type: str                         # 通知类型: system/approval/leave/contract 等
    ref_type: Optional[str] = None         # 关联资源类型，前端路由跳转用，如 "leave_request"
    ref_id: Optional[int] = None           # 关联资源 ID，如请假申请 ID=42
    # ref_type + ref_id 使用示例（前端伪代码）:
    #   if (notif.ref_type === 'approval_instance') {
    #       router.push(`/approvals/${notif.ref_id}`)
    #   }
    is_read: bool                           # True=已读；False=未读（触发红点徽标）
    created_at: datetime                   # 消息发送时间（带时区）


class NotificationCreate(BaseModel):
    """
    通知创建 Schema — 由业务 Service 层在触发事件时构造，不对外暴露为 HTTP 接口

    调用场景示例:
        # 审批通过后通知申请人
        await notification_service.create_notification(NotificationCreate(
            recipient_id=leave_request.employee_id,
            sender_id=approver.id,          # 审批人 ID
            title="请假申请已通过",
            content=f"您的 {days} 天年假申请已由 {approver.name} 审批通过",
            notif_type="approval",
            ref_type="leave_request",
            ref_id=leave_request.id,
        ))

        # 系统自动发送合同到期预警
        await notification_service.create_notification(NotificationCreate(
            recipient_id=employee.id,
            sender_id=None,                 # 系统发送，无操作人
            title="合同即将到期",
            notif_type="contract",
            ref_type="contract",
            ref_id=contract.id,
        ))

    字段说明:
        recipient_id — 接收消息的员工 ID（必填）
        sender_id    — 发送人员工 ID；None 表示系统自动发送（无人工操作人）
        title        — 消息标题（必填）
        content      — 消息正文（可选）
        notif_type   — 通知类型，默认 "system"
        ref_type     — 关联资源类型，供前端路由跳转；可为 None
        ref_id       — 关联资源 ID；可为 None
    """
    recipient_id: int                       # 消息接收人员工 ID（必填，对应 employees.id）
    sender_id: Optional[int] = None        # 发送人 ID；None = 系统自动发送
    title: str                              # 消息标题
    content: Optional[str] = None          # 消息正文，支持换行符
    notif_type: str = "system"             # 通知类型，默认为系统通知
    ref_type: Optional[str] = None         # 关联资源类型（前端跳转用）
    ref_id: Optional[int] = None           # 关联资源 ID（前端跳转用）


class NotificationListResponse(BaseModel):
    """
    通知列表分页响应 Schema — 用于 GET /api/v1/notifications 的返回值

    包含分页元信息和当前页的通知列表。
    前端分页组件依赖 total / page / page_size 渲染翻页控件。

    字段说明:
        total     — 当前用户的通知总数（含已读和未读）
        page      — 当前页码，从 1 开始
        page_size — 每页条数
        items     — 当前页的通知列表（按 created_at 倒序排列）
    """
    total: int          # 通知总数（供前端分页组件使用）
    page: int           # 当前页码（1-indexed）
    page_size: int      # 每页返回条数
    items: list[NotificationOut]    # 当前页通知列表，按创建时间倒序
