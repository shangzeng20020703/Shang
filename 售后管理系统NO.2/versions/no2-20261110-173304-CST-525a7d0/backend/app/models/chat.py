"""
移动端聊天模型
==============

本模块定义企业微信式站内聊天的基础数据结构。聊天与通知中心分离：
通知是系统到个人的业务事件流，聊天是员工之间的双向会话。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChatConversation(Base):
    """聊天会话主表。第一阶段只启用 direct 单聊，预留 group 群聊类型。"""

    __tablename__ = "chat_conversations"
    __table_args__ = (
        UniqueConstraint("direct_key", name="uq_chat_conversation_direct_key"),
        Index("idx_chat_conversation_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_type: Mapped[str] = mapped_column(String(20), default="direct", nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    announcement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    only_owner_or_admin_can_manage: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    invite_requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    only_owner_or_admin_can_at_all: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_muted_all: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_dissolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dissolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    direct_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_by_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="创建人员工ID",
    )
    last_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )


class ChatParticipant(Base):
    """会话参与人表，保存成员已读位点和个人会话设置。"""

    __tablename__ = "chat_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "employee_id", name="uq_chat_participant_member"),
        Index("idx_chat_participant_employee_updated", "employee_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    conversation_alias: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    group_nickname: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    last_read_message_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_marked_unread: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    marked_unread_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_later: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    later_note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    later_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hidden_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )


class ChatMessage(Base):
    """聊天消息表。第一阶段仅支持 text 文本消息。"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("idx_chat_message_conversation_id", "conversation_id", "id"),
        Index("idx_chat_message_conversation_created", "conversation_id", "created_at"),
        UniqueConstraint("conversation_id", "sender_id", "client_message_id", name="uq_chat_message_client_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    client_message_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    message_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    attachment_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    attachment_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attachment_mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    mentioned_employee_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    mention_all: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    reply_to_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reply_to_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    security_level: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recalled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ChatAnnouncement(Base):
    """群公告历史。"""

    __tablename__ = "chat_announcements"
    __table_args__ = (
        Index("idx_chat_announcement_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ChatAnnouncementReceipt(Base):
    """群公告阅读回执。"""

    __tablename__ = "chat_announcement_receipts"
    __table_args__ = (
        UniqueConstraint("announcement_id", "employee_id", name="uq_chat_announcement_receipt"),
        Index("idx_chat_announcement_receipt_announcement", "announcement_id", "read_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    announcement_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_announcements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ChatDing(Base):
    """DING/加急提醒记录。"""

    __tablename__ = "chat_dings"
    __table_args__ = (
        Index("idx_chat_ding_conversation", "conversation_id", "created_at"),
        Index("idx_chat_ding_sender", "sender_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    sender_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    acked_employee_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    channels: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ChatTodo(Base):
    """群待办。"""

    __tablename__ = "chat_todos"
    __table_args__ = (
        Index("idx_chat_todo_conversation", "conversation_id", "created_at"),
        Index("idx_chat_todo_status", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    completed_by_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)


class ChatCall(Base):
    """聊天语音/视频通话房间。"""

    __tablename__ = "chat_calls"
    __table_args__ = (
        Index("idx_chat_call_conversation_status", "conversation_id", "status", "updated_at"),
        Index("idx_chat_call_initiator", "initiator_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    initiator_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    call_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ringing", nullable=False, index=True)
    participant_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    accepted_employee_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    rejected_employee_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    ended_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True)
    ended_reason: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)


class ChatCallSignal(Base):
    """WebRTC 通话信令事件。"""

    __tablename__ = "chat_call_signals"
    __table_args__ = (
        Index("idx_chat_call_signal_call", "call_id", "id"),
        Index("idx_chat_call_signal_target", "target_employee_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_calls.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    target_employee_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True)
    signal_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ChatMeeting(Base):
    """聊天内会议预约。"""

    __tablename__ = "chat_meetings"
    __table_args__ = (
        Index("idx_chat_meeting_conversation_time", "conversation_id", "starts_at"),
        Index("idx_chat_meeting_organizer", "organizer_id", "starts_at"),
        UniqueConstraint("meeting_no", name="uq_chat_meeting_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organizer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    room_booking_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("booking_rooms.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    meeting_no: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    meeting_type: Mapped[str] = mapped_column(String(20), default="online", nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    attendee_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    room_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    room_location: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    join_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    reminder_minutes: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    agenda: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    equipment_needed: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now, nullable=False)


class ChatSecurityEvent(Base):
    """聊天安全治理事件。"""

    __tablename__ = "chat_security_events"
    __table_args__ = (
        Index("idx_chat_security_conversation", "conversation_id", "created_at"),
        Index("idx_chat_security_actor", "actor_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[int] = mapped_column(Integer, ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", nullable=False)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ChatPinnedMessage(Base):
    """群聊置顶消息。"""

    __tablename__ = "chat_pinned_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "message_id", name="uq_chat_pinned_message"),
        Index("idx_chat_pinned_conversation", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pinned_by_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)


class ChatAuditLog(Base):
    """聊天审计日志。"""

    __tablename__ = "chat_audit_logs"
    __table_args__ = (
        Index("idx_chat_audit_conversation", "conversation_id", "created_at"),
        Index("idx_chat_audit_actor", "actor_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("employees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    target_employee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, nullable=False)
