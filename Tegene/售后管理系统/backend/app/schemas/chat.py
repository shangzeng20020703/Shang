"""
移动端聊天接口 Schema。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatEmployeeBrief(BaseModel):
    """聊天中展示的员工轻量信息。"""

    id: int
    employee_no: str
    name: str
    avatar_text: str
    position: Optional[str] = None
    department_id: Optional[int] = None
    department_name: Optional[str] = None
    member_role: str = "member"
    group_nickname: Optional[str] = None


class ChatMessageCreate(BaseModel):
    """发送文本消息请求。"""

    content: str = Field(..., min_length=1, max_length=4000)
    mentioned_employee_ids: list[int] = Field(default_factory=list)
    mention_all: bool = False
    reply_to_message_id: Optional[int] = Field(None, gt=0)
    client_message_id: Optional[str] = Field(None, max_length=80)


class ChatAttachmentMessageCreate(BaseModel):
    """发送附件消息请求。附件文件先通过 /upload?category=chat 上传。"""

    message_type: str = Field(..., pattern="^(image|file|video|audio)$")
    content: str = Field("", max_length=4000)
    attachment_url: str = Field(..., min_length=1, max_length=500)
    attachment_name: str = Field(..., min_length=1, max_length=255)
    attachment_size: int = Field(0, ge=0)
    attachment_mime_type: str = Field("", max_length=120)
    mentioned_employee_ids: list[int] = Field(default_factory=list)
    mention_all: bool = False
    reply_to_message_id: Optional[int] = Field(None, gt=0)
    client_message_id: Optional[str] = Field(None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatRichMessageCreate(BaseModel):
    """发送富媒体或业务卡片消息。"""

    message_type: str = Field(
        ...,
        pattern="^(location|ding|todo_card|meeting_card|business_card|approval_card|attendance_card|payslip_card)$",
    )
    content: str = Field(..., min_length=1, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    mentioned_employee_ids: list[int] = Field(default_factory=list)
    mention_all: bool = False
    reply_to_message_id: Optional[int] = Field(None, gt=0)
    client_message_id: Optional[str] = Field(None, max_length=80)


class ChatMessageForwardCreate(BaseModel):
    """转发消息请求。"""

    target_conversation_ids: list[int] = Field(..., min_length=1, max_length=10)


class ChatMessageBulkForwardCreate(ChatMessageForwardCreate):
    """批量转发消息请求。"""

    message_ids: list[int] = Field(..., min_length=1, max_length=20)


class ChatMessageQuoteOut(BaseModel):
    """引用消息快照。"""

    id: int
    sender_name: str
    message_type: str
    summary: str


class ChatDirectConversationCreate(BaseModel):
    """创建或打开一对一会话请求。"""

    target_employee_id: int = Field(..., gt=0)


class ChatGroupConversationCreate(BaseModel):
    """创建群聊请求。"""

    title: str = Field(..., min_length=1, max_length=120)
    member_ids: list[int] = Field(..., min_length=1)


class ChatGroupUpdate(BaseModel):
    """群聊设置更新请求。"""

    title: Optional[str] = Field(None, min_length=1, max_length=120)
    announcement: Optional[str] = Field(None, max_length=4000)


class ChatGroupMembersUpdate(BaseModel):
    """群成员增删请求。"""

    member_ids: list[int] = Field(..., min_length=1)


class ChatConversationPersonalUpdate(BaseModel):
    """当前员工的会话备注和群昵称。"""

    conversation_alias: Optional[str] = Field(None, max_length=120)
    group_nickname: Optional[str] = Field(None, max_length=80)


class ChatGroupSettingsUpdate(BaseModel):
    """群聊权限开关。"""

    only_owner_or_admin_can_manage: Optional[bool] = None
    invite_requires_approval: Optional[bool] = None
    only_owner_or_admin_can_at_all: Optional[bool] = None
    is_muted_all: Optional[bool] = None


class ChatConversationEfficiencyUpdate(BaseModel):
    """会话效率层个人状态。"""

    enabled: bool
    note: Optional[str] = Field(None, max_length=255)


class ChatGroupRoleUpdate(BaseModel):
    """设置群管理员请求。"""

    employee_id: int = Field(..., gt=0)
    member_role: str = Field(..., pattern="^(admin|member)$")


class ChatGroupTransferOwner(BaseModel):
    """转让群主请求。"""

    employee_id: int = Field(..., gt=0)


class ChatAnnouncementCreate(BaseModel):
    """发布群公告请求。"""

    content: str = Field(..., min_length=1, max_length=4000)


class ChatDingCreate(BaseModel):
    """创建 DING 加急提醒。"""

    content: str = Field(..., min_length=1, max_length=4000)
    recipient_ids: list[int] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=lambda: ["app"])


class ChatTodoCreate(BaseModel):
    """创建群待办。"""

    content: str = Field(..., min_length=1, max_length=4000)
    assignee_ids: list[int] = Field(default_factory=list)
    due_at: Optional[datetime] = None


class ChatTodoUpdate(BaseModel):
    """更新群待办状态。"""

    status: Optional[str] = Field(None, pattern="^(open|done|cancelled)$")
    completed: Optional[bool] = None


class ChatCallCreate(BaseModel):
    """发起语音/视频通话。"""

    call_type: str = Field(..., pattern="^(voice|video)$")
    recipient_ids: list[int] = Field(default_factory=list, max_length=8)


class ChatCallStatusUpdate(BaseModel):
    """更新通话状态。"""

    action: str = Field(..., pattern="^(accept|reject|end|miss)$")
    reason: Optional[str] = Field(None, max_length=80)


class ChatCallSignalCreate(BaseModel):
    """写入 WebRTC 信令。"""

    signal_type: str = Field(..., pattern="^(offer|answer|ice|ringing|renegotiate)$")
    payload: dict[str, Any] = Field(default_factory=dict)
    target_employee_id: Optional[int] = Field(None, gt=0)


class ChatCallOut(BaseModel):
    """通话房间响应。"""

    id: int
    conversation_id: int
    call_type: str
    status: str
    initiator_id: int
    initiator_name: str
    participant_ids: list[int]
    accepted_employee_ids: list[int] = Field(default_factory=list)
    rejected_employee_ids: list[int] = Field(default_factory=list)
    ended_by_id: Optional[int] = None
    ended_reason: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ChatCallSignalOut(BaseModel):
    """WebRTC 信令响应。"""

    id: int
    call_id: int
    conversation_id: int
    sender_id: int
    sender_name: str
    target_employee_id: Optional[int] = None
    signal_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ChatCallListResponse(BaseModel):
    """通话列表响应。"""

    total: int
    items: list[ChatCallOut]


class ChatCallPollResponse(BaseModel):
    """通话轮询响应。"""

    call: ChatCallOut
    signals: list[ChatCallSignalOut] = Field(default_factory=list)


class ChatMeetingCreate(BaseModel):
    """在当前聊天会话中预约会议。"""

    title: str = Field(..., min_length=1, max_length=200)
    starts_at: datetime
    ends_at: datetime
    meeting_type: str = Field("online", pattern="^(online|offline|hybrid)$")
    attendee_ids: list[int] = Field(default_factory=list)
    room_name: Optional[str] = Field(None, max_length=80)
    room_location: Optional[str] = Field(None, max_length=120)
    join_url: Optional[str] = Field(None, max_length=500)
    reminder_minutes: int = Field(10, ge=0, le=1440)
    agenda: Optional[str] = Field(None, max_length=4000)
    equipment_needed: list[str] = Field(default_factory=list, max_length=20)


class ChatMeetingOut(BaseModel):
    """聊天会议预约响应。"""

    id: int
    conversation_id: int
    organizer_id: int
    organizer_name: str
    room_booking_id: Optional[int] = None
    meeting_no: str
    title: str
    meeting_type: str
    starts_at: datetime
    ends_at: datetime
    attendee_ids: list[int]
    attendees: list[ChatEmployeeBrief] = Field(default_factory=list)
    room_name: Optional[str] = None
    room_location: Optional[str] = None
    join_url: Optional[str] = None
    reminder_minutes: int
    agenda: Optional[str] = None
    equipment_needed: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime


class ChatMeetingListResponse(BaseModel):
    """聊天会议列表响应。"""

    total: int
    items: list[ChatMeetingOut]


class ChatMessageOut(BaseModel):
    """聊天消息响应。"""

    id: int
    conversation_id: int
    sender_id: int
    sender_name: str
    sender_avatar_text: str
    message_seq: int = 0
    client_message_id: Optional[str] = None
    message_type: str
    content: str
    attachment_url: Optional[str] = None
    attachment_name: Optional[str] = None
    attachment_size: Optional[int] = None
    attachment_mime_type: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    security_level: str = "normal"
    is_sensitive: bool = False
    mentioned_employee_ids: list[int] = Field(default_factory=list)
    mention_all: bool = False
    reply_to_message_id: Optional[int] = None
    reply_to: Optional[ChatMessageQuoteOut] = None
    is_pinned: bool = False
    is_deleted: bool
    is_own: bool
    read_status: str
    read_count: int
    unread_count: int
    can_recall: bool
    recalled_at: Optional[datetime] = None
    created_at: datetime


class ChatConversationOut(BaseModel):
    """会话列表响应项。"""

    id: int
    conversation_type: str
    title: Optional[str] = None
    announcement: Optional[str] = None
    current_member_role: str
    conversation_alias: Optional[str] = None
    group_nickname: Optional[str] = None
    display_name: str
    display_avatar_text: str
    participants: list[ChatEmployeeBrief]
    pinned_messages: list[ChatMessageOut] = Field(default_factory=list)
    last_message: Optional[ChatMessageOut] = None
    unread_count: int
    is_muted: bool
    is_pinned: bool
    is_marked_unread: bool = False
    is_later: bool = False
    later_note: Optional[str] = None
    later_at: Optional[datetime] = None
    has_unread_mention: bool = False
    pending_todo_count: int = 0
    upcoming_meeting_count: int = 0
    only_owner_or_admin_can_manage: bool = True
    invite_requires_approval: bool = False
    only_owner_or_admin_can_at_all: bool = True
    is_muted_all: bool = False
    is_dissolved: bool = False
    can_manage: bool
    can_manage_owner_actions: bool = False
    updated_at: datetime


class ChatConversationListResponse(BaseModel):
    """会话分页响应。"""

    total: int
    items: list[ChatConversationOut]


class ChatMessageListResponse(BaseModel):
    """消息分页响应，items 按时间正序返回。"""

    total: int
    page: int
    page_size: int
    items: list[ChatMessageOut]


class ChatMessageForwardResponse(BaseModel):
    """转发消息响应。"""

    total: int
    items: list[ChatMessageOut]


class ChatReceiptMemberOut(BaseModel):
    """消息回执中的成员状态。"""

    employee: ChatEmployeeBrief
    is_read: bool
    read_at: Optional[datetime] = None


class ChatMessageReceiptResponse(BaseModel):
    """单条消息已读未读明细。"""

    message_id: int
    read_count: int
    unread_count: int
    read_members: list[ChatReceiptMemberOut]
    unread_members: list[ChatReceiptMemberOut]


class ChatAnnouncementOut(BaseModel):
    """群公告历史响应。"""

    id: int
    conversation_id: int
    content: str
    created_by_id: int
    created_by_name: str
    created_at: datetime


class ChatAnnouncementListResponse(BaseModel):
    """群公告历史列表。"""

    total: int
    items: list[ChatAnnouncementOut]


class ChatAnnouncementReceiptResponse(BaseModel):
    """公告已读统计。"""

    announcement_id: int
    read_count: int
    unread_count: int
    read_members: list[ChatEmployeeBrief]
    unread_members: list[ChatEmployeeBrief]


class ChatDingOut(BaseModel):
    """DING 加急提醒响应。"""

    id: int
    conversation_id: int
    message_id: Optional[int] = None
    sender_id: int
    sender_name: str
    recipient_ids: list[int]
    acked_employee_ids: list[int] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    content: str
    status: str
    created_at: datetime


class ChatDingListResponse(BaseModel):
    """DING 列表。"""

    total: int
    items: list[ChatDingOut]


class ChatTodoOut(BaseModel):
    """群待办响应。"""

    id: int
    conversation_id: int
    source_message_id: Optional[int] = None
    created_by_id: int
    created_by_name: str
    content: str
    assignee_ids: list[int]
    completed_by_ids: list[int] = Field(default_factory=list)
    due_at: Optional[datetime] = None
    status: str
    created_at: datetime
    updated_at: datetime


class ChatTodoListResponse(BaseModel):
    """群待办列表。"""

    total: int
    items: list[ChatTodoOut]


class ChatSecuritySummaryResponse(BaseModel):
    """聊天治理与安全摘要。"""

    total_events: int
    high_risk_events: int
    recent_events: list[dict[str, Any]]


class ChatSyncResponse(BaseModel):
    """弱网恢复增量同步响应。"""

    conversation_id: int
    latest_seq: int
    items: list[ChatMessageOut]
