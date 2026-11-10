"""
移动端聊天 API。

路由前缀（注册于 router.py）: /api/v1/chat
"""

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.employee import Employee
from app.schemas.chat import (
    ChatAttachmentMessageCreate,
    ChatAnnouncementCreate,
    ChatAnnouncementListResponse,
    ChatAnnouncementReceiptResponse,
    ChatCallCreate,
    ChatCallListResponse,
    ChatCallOut,
    ChatCallPollResponse,
    ChatCallSignalCreate,
    ChatCallSignalOut,
    ChatCallStatusUpdate,
    ChatConversationEfficiencyUpdate,
    ChatConversationPersonalUpdate,
    ChatConversationListResponse,
    ChatConversationOut,
    ChatDirectConversationCreate,
    ChatDingCreate,
    ChatDingListResponse,
    ChatDingOut,
    ChatGroupConversationCreate,
    ChatGroupMembersUpdate,
    ChatGroupRoleUpdate,
    ChatGroupSettingsUpdate,
    ChatGroupTransferOwner,
    ChatGroupUpdate,
    ChatMessageBulkForwardCreate,
    ChatMessageCreate,
    ChatMessageForwardCreate,
    ChatMessageForwardResponse,
    ChatMessageListResponse,
    ChatMessageOut,
    ChatMessageReceiptResponse,
    ChatMeetingCreate,
    ChatMeetingListResponse,
    ChatMeetingOut,
    ChatRichMessageCreate,
    ChatSecuritySummaryResponse,
    ChatSyncResponse,
    ChatTodoCreate,
    ChatTodoListResponse,
    ChatTodoOut,
    ChatTodoUpdate,
)
from app.services.chat import ChatService

router = APIRouter(tags=["移动端聊天"])


async def _get_current_user_from_stream_token(db: AsyncSession, token: str) -> Employee:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise credentials_exception
    result = await db.execute(select(Employee).where(Employee.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被停用")
    return user


@router.get("/unread-count", summary="获取聊天未读消息数")
async def get_chat_unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    count = await ChatService.get_unread_count(db, current_user_id=current_user.id)
    return {"count": count}


@router.get("/stream", summary="聊天实时事件流")
async def stream_chat_events(
    token: str = Query(..., min_length=1),
    db: AsyncSession = Depends(get_db),
):
    current_user = await _get_current_user_from_stream_token(db, token)

    async def event_generator():
        try:
            while True:
                conversations, _total = await ChatService.list_conversations(
                    db,
                    current_user_id=current_user.id,
                    skip=0,
                    limit=1,
                )
                payload = {
                    "type": "snapshot",
                    "unread_count": await ChatService.get_unread_count(db, current_user_id=current_user.id),
                    "latest_conversation_id": conversations[0].id if conversations else None,
                    "latest_updated_at": conversations[0].updated_at.isoformat() if conversations else None,
                }
                yield f"event: chat\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            return

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/security/summary", response_model=ChatSecuritySummaryResponse, summary="聊天安全治理摘要")
async def get_security_summary(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.security_summary(
        db,
        current_user_id=current_user.id,
        limit=limit,
    )


@router.get("/conversations", response_model=ChatConversationListResponse, summary="获取聊天会话列表")
async def list_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    filter: str = Query("all", pattern="^(all|unread|at_me|todo|meeting|later|direct|group|file|muted)$"),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    items, total = await ChatService.list_conversations(
        db,
        current_user_id=current_user.id,
        skip=skip,
        limit=limit,
        conversation_filter=filter,
    )
    return ChatConversationListResponse(total=total, items=items)


@router.get("/conversations/search", response_model=ChatConversationListResponse, summary="搜索聊天会话")
async def search_conversations(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    items, total = await ChatService.search_conversations(
        db,
        current_user_id=current_user.id,
        keyword=keyword,
        limit=limit,
    )
    return ChatConversationListResponse(total=total, items=items)


@router.get("/messages/search", response_model=ChatMessageListResponse, summary="搜索聊天消息")
async def search_messages(
    keyword: str | None = Query(None),
    conversation_id: int | None = Query(None, gt=0),
    message_type: str | None = Query(None, pattern="^(all|text|media|file|card|mention|meeting_card|todo_card|ding|location|business_card|approval_card|attendance_card|payslip_card)$"),
    sender_id: int | None = Query(None, gt=0),
    conversation_type: str | None = Query(None, pattern="^(direct|group)$"),
    has_attachment: bool | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    try:
        parsed_from = datetime.fromisoformat(date_from) if date_from else None
        parsed_to = datetime.fromisoformat(date_to) if date_to else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式需为 ISO 8601") from exc
    items, total = await ChatService.search_messages(
        db,
        current_user_id=current_user.id,
        keyword=keyword,
        conversation_id=conversation_id,
        message_type=message_type,
        sender_id=sender_id,
        conversation_type=conversation_type,
        has_attachment=has_attachment,
        date_from=parsed_from,
        date_to=parsed_to,
        limit=limit,
    )
    return ChatMessageListResponse(total=total, page=1, page_size=limit, items=items)


@router.post("/conversations/direct", response_model=ChatConversationOut, summary="创建或打开一对一会话")
async def create_direct_conversation(
    data: ChatDirectConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.get_or_create_direct_conversation(
        db,
        current_user=current_user,
        target_employee_id=data.target_employee_id,
    )


@router.post("/conversations/group", response_model=ChatConversationOut, summary="创建群聊")
async def create_group_conversation(
    data: ChatGroupConversationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.create_group_conversation(
        db,
        current_user=current_user,
        data=data,
    )


@router.get("/conversations/{conversation_id}", response_model=ChatConversationOut, summary="获取聊天会话详情")
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.get_conversation(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.put("/conversations/{conversation_id}/group", response_model=ChatConversationOut, summary="更新群聊设置")
async def update_group_conversation(
    conversation_id: int,
    data: ChatGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_group_conversation(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.put("/conversations/{conversation_id}/personal", response_model=ChatConversationOut, summary="更新会话备注和群昵称")
async def update_personal_settings(
    conversation_id: int,
    data: ChatConversationPersonalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_personal_settings(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.put("/conversations/{conversation_id}/settings", response_model=ChatConversationOut, summary="更新群聊权限开关")
async def update_group_settings(
    conversation_id: int,
    data: ChatGroupSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_group_settings(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.put("/conversations/{conversation_id}/members/role", response_model=ChatConversationOut, summary="设置群管理员")
async def set_group_member_role(
    conversation_id: int,
    data: ChatGroupRoleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.set_group_member_role(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.put("/conversations/{conversation_id}/owner", response_model=ChatConversationOut, summary="转让群主")
async def transfer_group_owner(
    conversation_id: int,
    data: ChatGroupTransferOwner,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.transfer_group_owner(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.post("/conversations/{conversation_id}/leave", summary="退出群聊")
async def leave_group(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.leave_group(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.post("/conversations/{conversation_id}/dissolve", response_model=ChatConversationOut, summary="解散群聊")
async def dissolve_group(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.dissolve_group(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.post("/conversations/{conversation_id}/announcements", response_model=ChatConversationOut, summary="发布群公告")
async def publish_announcement(
    conversation_id: int,
    data: ChatAnnouncementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.publish_announcement(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.get("/conversations/{conversation_id}/announcements", response_model=ChatAnnouncementListResponse, summary="获取群公告历史")
async def list_announcements(
    conversation_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.list_announcements(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post(
    "/conversations/{conversation_id}/announcements/{announcement_id}/read",
    response_model=ChatAnnouncementReceiptResponse,
    summary="标记群公告已读",
)
async def mark_announcement_read(
    conversation_id: int,
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.mark_announcement_read(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        announcement_id=announcement_id,
    )


@router.get(
    "/conversations/{conversation_id}/announcements/{announcement_id}/receipts",
    response_model=ChatAnnouncementReceiptResponse,
    summary="获取群公告已读统计",
)
async def get_announcement_receipts(
    conversation_id: int,
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.get_announcement_receipts(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        announcement_id=announcement_id,
    )


@router.post("/conversations/{conversation_id}/members", response_model=ChatConversationOut, summary="添加群成员")
async def add_group_members(
    conversation_id: int,
    data: ChatGroupMembersUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.add_group_members(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.post("/conversations/{conversation_id}/members/remove", response_model=ChatConversationOut, summary="移除群成员")
async def remove_group_members(
    conversation_id: int,
    data: ChatGroupMembersUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.remove_group_members(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.delete("/conversations/{conversation_id}", summary="隐藏当前用户的聊天会话")
async def hide_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.hide_conversation(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessageListResponse,
    summary="获取会话消息",
)
async def list_messages(
    conversation_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    items, total = await ChatService.list_messages(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        page=page,
        page_size=page_size,
    )
    return ChatMessageListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get(
    "/conversations/{conversation_id}/sync",
    response_model=ChatSyncResponse,
    summary="按服务端序号同步增量消息",
)
async def sync_conversation(
    conversation_id: int,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.sync_conversation(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        after_seq=after_seq,
        limit=limit,
    )


@router.get(
    "/conversations/{conversation_id}/attachments",
    response_model=ChatMessageListResponse,
    summary="获取会话附件聚合",
)
async def list_conversation_attachments(
    conversation_id: int,
    kind: str = Query("all", pattern="^(all|image|video|audio|file)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    items, total = await ChatService.list_attachments(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        kind=kind,
        page=page,
        page_size=page_size,
    )
    return ChatMessageListResponse(total=total, page=page, page_size=page_size, items=items)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessageOut,
    summary="发送文本消息",
)
async def send_message(
    conversation_id: int,
    data: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.send_text_message(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        content=data.content,
        mentioned_employee_ids=data.mentioned_employee_ids,
        mention_all=data.mention_all,
        reply_to_message_id=data.reply_to_message_id,
        client_message_id=data.client_message_id,
    )


@router.post(
    "/conversations/{conversation_id}/attachments",
    response_model=ChatMessageOut,
    summary="发送图片或文件消息",
)
async def send_attachment(
    conversation_id: int,
    data: ChatAttachmentMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.send_attachment_message(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        data=data,
    )


@router.post(
    "/conversations/{conversation_id}/rich-messages",
    response_model=ChatMessageOut,
    summary="发送位置、DING 卡片或业务卡片消息",
)
async def send_rich_message(
    conversation_id: int,
    data: ChatRichMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.send_rich_message(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        data=data,
    )


@router.post(
    "/conversations/{conversation_id}/messages/forward",
    response_model=ChatMessageForwardResponse,
    summary="批量转发消息",
)
async def bulk_forward_messages(
    conversation_id: int,
    data: ChatMessageBulkForwardCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.bulk_forward_messages(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        data=data,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/forward",
    response_model=ChatMessageForwardResponse,
    summary="转发单条消息",
)
async def forward_message(
    conversation_id: int,
    message_id: int,
    data: ChatMessageForwardCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.forward_message(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        message_id=message_id,
        data=data,
    )


@router.get(
    "/conversations/{conversation_id}/messages/{message_id}/receipts",
    response_model=ChatMessageReceiptResponse,
    summary="获取消息已读未读明细",
)
async def get_message_receipts(
    conversation_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.get_message_receipts(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


@router.post("/conversations/{conversation_id}/read", summary="标记会话已读")
async def mark_read(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.mark_read(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.put("/conversations/{conversation_id}/pin", response_model=ChatConversationOut, summary="置顶或取消置顶会话")
async def update_pin(
    conversation_id: int,
    enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_conversation_setting(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        setting="pin",
        enabled=enabled,
    )


@router.put("/conversations/{conversation_id}/mute", response_model=ChatConversationOut, summary="开启或关闭会话免打扰")
async def update_mute(
    conversation_id: int,
    enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_conversation_setting(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        setting="mute",
        enabled=enabled,
    )


@router.put("/conversations/{conversation_id}/later", response_model=ChatConversationOut, summary="稍后处理或取消稍后处理")
async def update_later(
    conversation_id: int,
    data: ChatConversationEfficiencyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_conversation_efficiency(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        status_type="later",
        data=data,
    )


@router.put("/conversations/{conversation_id}/mark-unread", response_model=ChatConversationOut, summary="标记未读或取消标记未读")
async def update_mark_unread(
    conversation_id: int,
    data: ChatConversationEfficiencyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_conversation_efficiency(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        status_type="mark_unread",
        data=data,
    )


@router.post("/conversations/{conversation_id}/ding", response_model=ChatDingOut, summary="创建 DING 加急提醒")
async def create_ding(
    conversation_id: int,
    data: ChatDingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.create_ding(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        data=data,
    )


@router.get("/conversations/{conversation_id}/ding", response_model=ChatDingListResponse, summary="获取 DING 记录")
async def list_dings(
    conversation_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.list_dings(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/conversations/{conversation_id}/ding/{ding_id}/ack", response_model=ChatDingOut, summary="确认 DING")
async def ack_ding(
    conversation_id: int,
    ding_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.ack_ding(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        ding_id=ding_id,
    )


@router.post("/conversations/{conversation_id}/todos", response_model=ChatTodoOut, summary="创建群待办")
async def create_todo(
    conversation_id: int,
    data: ChatTodoCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.create_todo(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        data=data,
    )


@router.get("/conversations/{conversation_id}/todos", response_model=ChatTodoListResponse, summary="获取群待办")
async def list_todos(
    conversation_id: int,
    status_filter: str = Query("open", pattern="^(all|open|done|cancelled)$"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.list_todos(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        status_filter=status_filter,
        limit=limit,
    )


@router.put("/conversations/{conversation_id}/todos/{todo_id}", response_model=ChatTodoOut, summary="更新群待办")
async def update_todo(
    conversation_id: int,
    todo_id: int,
    data: ChatTodoUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_todo(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        todo_id=todo_id,
        data=data,
    )


@router.post("/conversations/{conversation_id}/meetings", response_model=ChatMeetingOut, summary="在聊天中预约会议")
async def create_meeting(
    conversation_id: int,
    data: ChatMeetingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.create_meeting(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        data=data,
    )


@router.get("/conversations/{conversation_id}/meetings", response_model=ChatMeetingListResponse, summary="获取当前会话会议")
async def list_meetings(
    conversation_id: int,
    status_filter: str = Query("upcoming", pattern="^(all|upcoming|scheduled|cancelled)$"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.list_meetings(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        status_filter=status_filter,
        limit=limit,
    )


@router.post("/conversations/{conversation_id}/meetings/{meeting_id}/cancel", response_model=ChatMeetingOut, summary="取消聊天会议")
async def cancel_meeting(
    conversation_id: int,
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.cancel_meeting(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        meeting_id=meeting_id,
    )


@router.post("/conversations/{conversation_id}/calls", response_model=ChatCallOut, summary="发起语音/视频通话")
async def create_call(
    conversation_id: int,
    data: ChatCallCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.create_call(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        data=data,
    )


@router.get("/conversations/{conversation_id}/calls/active", response_model=ChatCallListResponse, summary="获取当前会话活跃通话")
async def list_active_calls(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.list_active_calls(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.get("/conversations/{conversation_id}/calls/{call_id}", response_model=ChatCallOut, summary="获取通话详情")
async def get_call(
    conversation_id: int,
    call_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.get_call(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        call_id=call_id,
    )


@router.post("/conversations/{conversation_id}/calls/{call_id}/status", response_model=ChatCallOut, summary="接听、拒绝或挂断通话")
async def update_call_status(
    conversation_id: int,
    call_id: int,
    data: ChatCallStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.update_call_status(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        call_id=call_id,
        data=data,
    )


@router.post("/conversations/{conversation_id}/calls/{call_id}/signals", response_model=ChatCallSignalOut, summary="写入 WebRTC 通话信令")
async def create_call_signal(
    conversation_id: int,
    call_id: int,
    data: ChatCallSignalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.create_call_signal(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        call_id=call_id,
        data=data,
    )


@router.get("/conversations/{conversation_id}/calls/{call_id}/poll", response_model=ChatCallPollResponse, summary="轮询通话状态和新增信令")
async def poll_call(
    conversation_id: int,
    call_id: int,
    after_signal_id: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.poll_call(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        call_id=call_id,
        after_signal_id=after_signal_id,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/recall",
    response_model=ChatMessageOut,
    summary="撤回自己发送的消息",
)
async def recall_message(
    conversation_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.recall_message(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/pin",
    response_model=ChatConversationOut,
    summary="置顶群消息",
)
async def pin_message(
    conversation_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.pin_message(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )


@router.delete(
    "/conversations/{conversation_id}/messages/{message_id}/pin",
    response_model=ChatConversationOut,
    summary="取消置顶群消息",
)
async def unpin_message(
    conversation_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    return await ChatService.unpin_message(
        db,
        current_user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
