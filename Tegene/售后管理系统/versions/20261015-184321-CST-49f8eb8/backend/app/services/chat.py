"""
移动端聊天服务。

第一阶段实现一对一文本聊天，使用 REST 轮询读取新消息。所有读取、发送、
标记已读操作都必须先校验当前员工是会话参与人。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chat import (
    ChatAnnouncement,
    ChatAnnouncementReceipt,
    ChatAuditLog,
    ChatCall,
    ChatCallSignal,
    ChatConversation,
    ChatDing,
    ChatMeeting,
    ChatMessage,
    ChatParticipant,
    ChatPinnedMessage,
    ChatSecurityEvent,
    ChatTodo,
)
from app.models.employee import Employee
from app.schemas.admin import BookingRoomCreate
from app.schemas.chat import (
    ChatAttachmentMessageCreate,
    ChatAnnouncementCreate,
    ChatAnnouncementListResponse,
    ChatAnnouncementOut,
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
    ChatConversationOut,
    ChatDingCreate,
    ChatDingListResponse,
    ChatDingOut,
    ChatEmployeeBrief,
    ChatRichMessageCreate,
    ChatMeetingCreate,
    ChatMeetingListResponse,
    ChatMeetingOut,
    ChatMessageBulkForwardCreate,
    ChatMessageForwardCreate,
    ChatMessageForwardResponse,
    ChatGroupConversationCreate,
    ChatGroupMembersUpdate,
    ChatGroupRoleUpdate,
    ChatGroupSettingsUpdate,
    ChatGroupTransferOwner,
    ChatGroupUpdate,
    ChatMessageReceiptResponse,
    ChatMessageOut,
    ChatMessageQuoteOut,
    ChatReceiptMemberOut,
    ChatSecuritySummaryResponse,
    ChatSyncResponse,
    ChatTodoCreate,
    ChatTodoListResponse,
    ChatTodoOut,
    ChatTodoUpdate,
)
from app.services import admin as admin_service


class ChatService:
    """聊天业务服务。"""

    TEXT_LIMIT = 4000
    RECALL_WINDOW = timedelta(hours=24)

    @staticmethod
    def direct_key(first_employee_id: int, second_employee_id: int) -> str:
        """生成单聊双方唯一键，避免 A-B 与 B-A 创建两条会话。"""
        first, second = sorted([int(first_employee_id), int(second_employee_id)])
        return f"{first}:{second}"

    @staticmethod
    async def get_or_create_direct_conversation(
        db: AsyncSession,
        *,
        current_user: Employee,
        target_employee_id: int,
    ) -> ChatConversationOut:
        """获取或创建一对一会话。"""
        if int(current_user.id) == int(target_employee_id):
            raise HTTPException(status_code=400, detail="不能和自己发起聊天")

        target = await ChatService._get_active_employee(db, target_employee_id)
        key = ChatService.direct_key(current_user.id, target.id)
        result = await db.execute(
            select(ChatConversation).where(
                ChatConversation.conversation_type == "direct",
                ChatConversation.direct_key == key,
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            now = datetime.now(timezone.utc)
            conversation = ChatConversation(
                conversation_type="direct",
                direct_key=key,
                created_by_id=current_user.id,
                created_at=now,
                updated_at=now,
            )
            db.add(conversation)
            await db.flush()
            db.add_all([
                ChatParticipant(conversation_id=conversation.id, employee_id=current_user.id, created_at=now, updated_at=now),
                ChatParticipant(conversation_id=conversation.id, employee_id=target.id, created_at=now, updated_at=now),
            ])
            await db.flush()
            await ChatService._audit(
                db,
                conversation_id=conversation.id,
                actor_id=current_user.id,
                action="create_direct",
                target_employee_id=target.id,
            )
        return await ChatService.get_conversation(db, current_user_id=current_user.id, conversation_id=conversation.id)

    @staticmethod
    async def create_group_conversation(
        db: AsyncSession,
        *,
        current_user: Employee,
        data: ChatGroupConversationCreate,
    ) -> ChatConversationOut:
        """创建群聊，当前员工自动成为群主。"""
        member_ids = sorted({int(member_id) for member_id in data.member_ids if int(member_id) != int(current_user.id)})
        if not member_ids:
            raise HTTPException(status_code=400, detail="群聊至少需要添加一名其他成员")
        members = await ChatService._load_active_employees(db, member_ids)
        if len(members) != len(member_ids):
            raise HTTPException(status_code=404, detail="部分群成员不存在或已停用")

        now = datetime.now(timezone.utc)
        conversation = ChatConversation(
            conversation_type="group",
            title=data.title.strip(),
            created_by_id=current_user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        await db.flush()
        db.add(
            ChatParticipant(
                conversation_id=conversation.id,
                employee_id=current_user.id,
                member_role="owner",
                created_at=now,
                updated_at=now,
            )
        )
        db.add_all([
            ChatParticipant(
                conversation_id=conversation.id,
                employee_id=member_id,
                member_role="member",
                created_at=now,
                updated_at=now,
            )
            for member_id in member_ids
        ])
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation.id,
            actor_id=current_user.id,
            action="create_group",
            detail={"title": data.title.strip(), "member_ids": member_ids},
        )
        return await ChatService.get_conversation(db, current_user_id=current_user.id, conversation_id=conversation.id)

    @staticmethod
    async def update_group_conversation(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatGroupUpdate,
    ) -> ChatConversationOut:
        """更新群名称或群公告。"""
        participant, conversation = await ChatService._require_group_manager(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        changes: dict[str, str] = {}
        if data.title is not None:
            new_title = data.title.strip()
            if new_title != (conversation.title or ""):
                changes["title"] = new_title
                conversation.title = new_title
        if data.announcement is not None:
            new_announcement = data.announcement.strip()
            if new_announcement != (conversation.announcement or ""):
                changes["announcement"] = new_announcement
                conversation.announcement = new_announcement
                if new_announcement:
                    db.add(ChatAnnouncement(
                        conversation_id=conversation_id,
                        content=new_announcement,
                        created_by_id=current_user_id,
                    ))
        conversation.updated_at = datetime.now(timezone.utc)
        await db.flush()
        if changes:
            await ChatService._audit(
                db,
                conversation_id=conversation_id,
                actor_id=current_user_id,
                action="update_group",
                detail=changes,
            )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def add_group_members(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatGroupMembersUpdate,
    ) -> ChatConversationOut:
        """添加群成员。"""
        participant, conversation = await ChatService._require_group_manager(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        requested_ids = sorted({int(member_id) for member_id in data.member_ids})
        if not requested_ids:
            raise HTTPException(status_code=400, detail="请选择要添加的群成员")
        if conversation.invite_requires_approval and (participant.member_role or "member") not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="该群已开启邀请确认")
        members = await ChatService._load_active_employees(db, requested_ids)
        if len(members) != len(requested_ids):
            raise HTTPException(status_code=404, detail="部分群成员不存在或已停用")

        existing_result = await db.execute(
            select(ChatParticipant.employee_id).where(ChatParticipant.conversation_id == conversation_id)
        )
        existing_ids = {int(row[0]) for row in existing_result.all()}
        now = datetime.now(timezone.utc)
        db.add_all([
            ChatParticipant(
                conversation_id=conversation_id,
                employee_id=member_id,
                member_role="member",
                created_at=now,
                updated_at=now,
            )
            for member_id in requested_ids
            if member_id not in existing_ids
        ])
        conversation.updated_at = now
        await db.flush()
        added_ids = [member_id for member_id in requested_ids if member_id not in existing_ids]
        if added_ids:
            await ChatService._audit(
                db,
                conversation_id=conversation_id,
                actor_id=current_user_id,
                action="add_members",
                detail={"member_ids": added_ids},
            )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def remove_group_members(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatGroupMembersUpdate,
    ) -> ChatConversationOut:
        """移除群成员。群主不能被移除。"""
        participant, conversation = await ChatService._require_group_manager(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        requested_ids = {int(member_id) for member_id in data.member_ids}
        if not requested_ids:
            raise HTTPException(status_code=400, detail="请选择要移除的群成员")
        if current_user_id in requested_ids and (participant.member_role or "member") == "owner":
            raise HTTPException(status_code=400, detail="群主不能移除自己")

        result = await db.execute(
            select(ChatParticipant).where(
                ChatParticipant.conversation_id == conversation_id,
                ChatParticipant.employee_id.in_(requested_ids),
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            if (row.member_role or "member") == "owner":
                raise HTTPException(status_code=400, detail="群主不能被移除")
        for row in rows:
            await db.delete(row)
        conversation.updated_at = datetime.now(timezone.utc)
        await db.flush()
        if rows:
            await ChatService._audit(
                db,
                conversation_id=conversation_id,
                actor_id=current_user_id,
                action="remove_members",
                detail={"member_ids": [row.employee_id for row in rows]},
            )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def update_personal_settings(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatConversationPersonalUpdate,
    ) -> ChatConversationOut:
        """更新当前员工自己的会话备注和群昵称。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        if data.conversation_alias is not None:
            participant.conversation_alias = data.conversation_alias.strip() or None
        if data.group_nickname is not None:
            if conversation.conversation_type != "group":
                raise HTTPException(status_code=400, detail="只有群聊支持群昵称")
            participant.group_nickname = data.group_nickname.strip() or None
        participant.updated_at = datetime.now(timezone.utc)
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="update_personal_settings",
            detail={
                "conversation_alias": participant.conversation_alias,
                "group_nickname": participant.group_nickname,
            },
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def update_group_settings(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatGroupSettingsUpdate,
    ) -> ChatConversationOut:
        """更新群聊权限开关。"""
        participant, conversation = await ChatService._require_owner(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        changes: dict[str, bool] = {}
        for field in (
            "only_owner_or_admin_can_manage",
            "invite_requires_approval",
            "only_owner_or_admin_can_at_all",
            "is_muted_all",
        ):
            value = getattr(data, field)
            if value is not None and getattr(conversation, field) != value:
                setattr(conversation, field, value)
                changes[field] = value
        conversation.updated_at = datetime.now(timezone.utc)
        await db.flush()
        if changes:
            await ChatService._audit(
                db,
                conversation_id=conversation_id,
                actor_id=current_user_id,
                action="update_group_settings",
                detail=changes,
            )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def set_group_member_role(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatGroupRoleUpdate,
    ) -> ChatConversationOut:
        """设置或取消群管理员。"""
        participant, conversation = await ChatService._require_owner(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        target = await ChatService._get_member_participant(
            db,
            conversation_id=conversation_id,
            employee_id=data.employee_id,
        )
        if (target.member_role or "member") == "owner":
            raise HTTPException(status_code=400, detail="不能修改群主角色")
        if data.member_role == "admin":
            count_result = await db.execute(
                select(func.count()).select_from(ChatParticipant).where(
                    ChatParticipant.conversation_id == conversation_id,
                    ChatParticipant.member_role == "admin",
                )
            )
            admin_count = int(count_result.scalar_one() or 0)
            if admin_count >= 3 and target.member_role != "admin":
                raise HTTPException(status_code=400, detail="每个群最多设置3名管理员")
        target.member_role = data.member_role
        target.updated_at = datetime.now(timezone.utc)
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="set_member_role",
            target_employee_id=data.employee_id,
            detail={"member_role": data.member_role},
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def transfer_group_owner(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatGroupTransferOwner,
    ) -> ChatConversationOut:
        """转让群主。"""
        participant, conversation = await ChatService._require_owner(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        target = await ChatService._get_member_participant(
            db,
            conversation_id=conversation_id,
            employee_id=data.employee_id,
        )
        if target.employee_id == current_user_id:
            raise HTTPException(status_code=400, detail="不能转让给自己")
        participant.member_role = "member"
        target.member_role = "owner"
        conversation.created_by_id = target.employee_id
        now = datetime.now(timezone.utc)
        participant.updated_at = now
        target.updated_at = now
        conversation.updated_at = now
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="transfer_owner",
            target_employee_id=target.employee_id,
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def leave_group(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> dict[str, bool]:
        """当前员工退出群聊。群主需要先转让或解散。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        if conversation.conversation_type != "group":
            raise HTTPException(status_code=400, detail="只有群聊支持退出")
        if (participant.member_role or "member") == "owner":
            raise HTTPException(status_code=400, detail="群主需先转让群主或解散群聊")
        await db.delete(participant)
        conversation.updated_at = datetime.now(timezone.utc)
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="leave_group",
            target_employee_id=current_user_id,
        )
        return {"ok": True}

    @staticmethod
    async def dissolve_group(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> ChatConversationOut:
        """解散群聊，保留历史记录但禁止继续发消息和管理成员。"""
        participant, conversation = await ChatService._require_owner(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        if conversation.is_dissolved:
            return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)
        now = datetime.now(timezone.utc)
        conversation.is_dissolved = True
        conversation.dissolved_at = now
        conversation.updated_at = now
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="dissolve_group",
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def list_conversations(
        db: AsyncSession,
        *,
        current_user_id: int,
        skip: int = 0,
        limit: int = 50,
        conversation_filter: str = "all",
    ) -> tuple[list[ChatConversationOut], int]:
        """查询当前员工的会话列表，支持效率层筛选。"""
        allowed_filters = {"all", "unread", "at_me", "todo", "meeting", "later", "direct", "group", "file", "muted"}
        if conversation_filter not in allowed_filters:
            raise HTTPException(status_code=400, detail="不支持的会话筛选")
        count_stmt = select(func.count()).select_from(ChatParticipant).where(
            ChatParticipant.employee_id == current_user_id,
            ChatParticipant.is_hidden.is_(False),
        )
        base_stmt = (
            select(ChatParticipant, ChatConversation)
            .join(ChatConversation, ChatConversation.id == ChatParticipant.conversation_id)
            .where(
                ChatParticipant.employee_id == current_user_id,
                ChatParticipant.is_hidden.is_(False),
            )
            .order_by(ChatParticipant.is_pinned.desc(), ChatConversation.updated_at.desc(), ChatConversation.id.desc())
        )

        if conversation_filter == "all":
            total = int((await db.execute(count_stmt)).scalar_one() or 0)
            result = await db.execute(base_stmt.offset(skip).limit(limit))
            rows = result.all()
        else:
            result = await db.execute(base_stmt.limit(300))
            candidate_rows = result.all()
            built = [
                await ChatService._build_conversation_out(db, current_user_id, participant, conversation)
                for participant, conversation in candidate_rows
            ]
            filtered = [item for item in built if ChatService._conversation_matches_filter(item, conversation_filter)]
            return filtered[skip: skip + limit], len(filtered)

        conversations = [
            await ChatService._build_conversation_out(db, current_user_id, participant, conversation)
            for participant, conversation in rows
        ]
        return conversations, total

    @staticmethod
    async def search_conversations(
        db: AsyncSession,
        *,
        current_user_id: int,
        keyword: str,
        limit: int = 50,
    ) -> tuple[list[ChatConversationOut], int]:
        """按会话名称、成员姓名或最近消息内容搜索当前员工会话。"""
        query = keyword.strip()
        if not query:
            return await ChatService.list_conversations(db, current_user_id=current_user_id, limit=limit)

        items, _total = await ChatService.list_conversations(db, current_user_id=current_user_id, limit=100)
        lowered = query.lower()
        matched = [
            item
            for item in items
            if lowered in item.display_name.lower()
            or any(lowered in member.name.lower() or lowered in member.employee_no.lower() for member in item.participants)
            or (item.last_message and lowered in item.last_message.content.lower())
        ]
        return matched[:limit], len(matched)

    @staticmethod
    async def get_conversation(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> ChatConversationOut:
        """读取单个会话，要求当前员工是参与人。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def list_messages(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        page: int = 1,
        page_size: int = 30,
    ) -> tuple[list[ChatMessageOut], int]:
        """分页读取会话消息，items 按时间正序返回。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        base = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await db.execute(
            base.order_by(ChatMessage.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        messages = list(reversed(result.scalars().all()))
        sender_ids = {message.sender_id for message in messages}
        employees = await ChatService._load_employees(db, sender_ids)
        receipts = await ChatService._receipt_map(
            db,
            conversation_id,
            [message.id for message in messages],
            current_user_id,
        )
        return [
            ChatService._message_out(message, employees.get(message.sender_id), current_user_id, receipts.get(message.id))
            for message in messages
        ], total

    @staticmethod
    async def send_text_message(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        content: str,
        mentioned_employee_ids: list[int] | None = None,
        mention_all: bool = False,
        reply_to_message_id: int | None = None,
        client_message_id: str | None = None,
    ) -> ChatMessageOut:
        """发送文本消息。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        text = content.strip()
        if not text:
            raise HTTPException(status_code=400, detail="消息内容不能为空")
        if len(text) > ChatService.TEXT_LIMIT:
            raise HTTPException(status_code=400, detail="消息内容不能超过4000字")

        return await ChatService._create_message(
            db,
            current_user=current_user,
            participant=participant,
            conversation=conversation,
            message_type="text",
            content=text,
            mentioned_employee_ids=mentioned_employee_ids,
            mention_all=mention_all,
            reply_to_message_id=reply_to_message_id,
            client_message_id=client_message_id,
        )

    @staticmethod
    async def send_attachment_message(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        data: ChatAttachmentMessageCreate,
    ) -> ChatMessageOut:
        """发送图片或文件消息。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        content = data.content.strip()
        if len(content) > ChatService.TEXT_LIMIT:
            raise HTTPException(status_code=400, detail="消息内容不能超过4000字")
        return await ChatService._create_message(
            db,
            current_user=current_user,
            participant=participant,
            conversation=conversation,
            message_type=data.message_type,
            content=content,
            attachment_url=data.attachment_url,
            attachment_name=data.attachment_name,
            attachment_size=data.attachment_size,
            attachment_mime_type=data.attachment_mime_type,
            mentioned_employee_ids=data.mentioned_employee_ids,
            mention_all=data.mention_all,
            reply_to_message_id=data.reply_to_message_id,
            client_message_id=data.client_message_id,
            metadata=data.metadata,
        )

    @staticmethod
    async def send_rich_message(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        data: ChatRichMessageCreate,
    ) -> ChatMessageOut:
        """发送位置、DING 卡片、业务卡片等结构化消息。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        content = data.content.strip()
        if len(content) > ChatService.TEXT_LIMIT:
            raise HTTPException(status_code=400, detail="消息内容不能超过4000字")
        return await ChatService._create_message(
            db,
            current_user=current_user,
            participant=participant,
            conversation=conversation,
            message_type=data.message_type,
            content=content,
            mentioned_employee_ids=data.mentioned_employee_ids,
            mention_all=data.mention_all,
            reply_to_message_id=data.reply_to_message_id,
            client_message_id=data.client_message_id,
            metadata=data.metadata,
        )

    @staticmethod
    async def forward_message(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        message_id: int,
        data: ChatMessageForwardCreate,
    ) -> ChatMessageForwardResponse:
        """将单条消息转发到一个或多个当前员工参与的会话。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        message = await ChatService._get_message_or_404(db, conversation_id=conversation_id, message_id=message_id)
        if message.is_deleted:
            raise HTTPException(status_code=400, detail="已撤回消息不能转发")
        return await ChatService._forward_messages_to_targets(
            db,
            current_user=current_user,
            source_conversation_id=conversation_id,
            source_messages=[message],
            target_conversation_ids=data.target_conversation_ids,
        )

    @staticmethod
    async def bulk_forward_messages(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        data: ChatMessageBulkForwardCreate,
    ) -> ChatMessageForwardResponse:
        """批量转发当前会话内的多条消息。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        requested_ids = [int(message_id) for message_id in data.message_ids]
        unique_ids = list(dict.fromkeys(requested_ids))
        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.id.in_(unique_ids),
            )
        )
        by_id = {message.id: message for message in result.scalars().all()}
        if len(by_id) != len(unique_ids):
            raise HTTPException(status_code=404, detail="部分消息不存在")
        messages = [by_id[message_id] for message_id in unique_ids]
        if any(message.is_deleted for message in messages):
            raise HTTPException(status_code=400, detail="已撤回消息不能转发")
        return await ChatService._forward_messages_to_targets(
            db,
            current_user=current_user,
            source_conversation_id=conversation_id,
            source_messages=messages,
            target_conversation_ids=data.target_conversation_ids,
        )

    @staticmethod
    async def list_attachments(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        kind: str = "all",
        page: int = 1,
        page_size: int = 30,
    ) -> tuple[list[ChatMessageOut], int]:
        """按图片、视频、语音或文件聚合当前会话真实附件消息。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        if kind not in {"all", "image", "video", "audio", "file"}:
            raise HTTPException(status_code=400, detail="附件类型仅支持 all/image/video/audio/file")
        base = select(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.is_deleted.is_(False),
            ChatMessage.attachment_url.isnot(None),
        )
        if kind in {"image", "video", "audio", "file"}:
            base = base.where(ChatMessage.message_type == kind)
        total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
        result = await db.execute(
            base.order_by(ChatMessage.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        messages = list(result.scalars().all())
        employees = await ChatService._load_employees(db, [message.sender_id for message in messages])
        receipts_by_message: dict[int, tuple[int, int]] = {}
        for item in messages:
            receipts = await ChatService._receipt_map(db, item.conversation_id, [item.id], current_user_id)
            receipts_by_message[item.id] = receipts.get(item.id, (0, 0))
        return [
            ChatService._message_out(
                item,
                employees.get(item.sender_id),
                current_user_id,
                receipts_by_message.get(item.id),
            )
            for item in messages
        ], total

    @staticmethod
    async def get_message_receipts(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        message_id: int,
    ) -> ChatMessageReceiptResponse:
        """读取单条消息的已读未读成员明细。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        message = await ChatService._get_message_or_404(db, conversation_id=conversation_id, message_id=message_id)
        role = participant.member_role or "member"
        can_view = message.sender_id == current_user_id or (
            conversation.conversation_type == "group" and role in {"owner", "admin"}
        )
        if not can_view:
            raise HTTPException(status_code=403, detail="只能查看自己消息的已读明细")

        participants_result = await db.execute(
            select(ChatParticipant).where(
                ChatParticipant.conversation_id == conversation_id,
                ChatParticipant.employee_id != message.sender_id,
            )
        )
        participants = list(participants_result.scalars().all())
        employees = await ChatService._load_employees(db, [item.employee_id for item in participants])
        read_members: list[ChatReceiptMemberOut] = []
        unread_members: list[ChatReceiptMemberOut] = []
        for item in participants:
            employee = employees.get(item.employee_id)
            if employee is None:
                continue
            row = ChatReceiptMemberOut(
                employee=ChatService._employee_brief(employee, item),
                is_read=(item.last_read_message_id or 0) >= message_id,
                read_at=item.last_read_at if (item.last_read_message_id or 0) >= message_id else None,
            )
            if row.is_read:
                read_members.append(row)
            else:
                unread_members.append(row)
        return ChatMessageReceiptResponse(
            message_id=message_id,
            read_count=len(read_members),
            unread_count=len(unread_members),
            read_members=read_members,
            unread_members=unread_members,
        )

    @staticmethod
    async def _create_message(
        db: AsyncSession,
        *,
        current_user: Employee,
        participant: ChatParticipant,
        conversation: ChatConversation,
        message_type: str,
        content: str,
        attachment_url: str | None = None,
        attachment_name: str | None = None,
        attachment_size: int | None = None,
        attachment_mime_type: str | None = None,
        mentioned_employee_ids: list[int] | None = None,
        mention_all: bool = False,
        reply_to_message_id: int | None = None,
        client_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatMessageOut:
        if conversation.is_dissolved:
            raise HTTPException(status_code=400, detail="群聊已解散，不能继续发送消息")
        if conversation.is_muted_all and (participant.member_role or "member") not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="当前群聊已开启全员禁言")
        if mention_all and conversation.only_owner_or_admin_can_at_all and (participant.member_role or "member") not in {"owner", "admin"}:
            raise HTTPException(status_code=403, detail="只有群主或管理员可以@所有人")
        dedupe_key = client_message_id.strip() if client_message_id else None
        if dedupe_key:
            existing_result = await db.execute(
                select(ChatMessage).where(
                    ChatMessage.conversation_id == conversation.id,
                    ChatMessage.sender_id == current_user.id,
                    ChatMessage.client_message_id == dedupe_key,
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                receipts = await ChatService._receipt_map(db, conversation.id, [existing.id], current_user.id)
                return ChatService._message_out(existing, current_user, current_user.id, receipts.get(existing.id))
        now = datetime.now(timezone.utc)
        mentioned_ids = sorted({int(employee_id) for employee_id in (mentioned_employee_ids or []) if int(employee_id) > 0})
        if mentioned_ids:
            member_result = await db.execute(
                select(ChatParticipant.employee_id).where(ChatParticipant.conversation_id == conversation.id)
            )
            member_ids = {int(row[0]) for row in member_result.all()}
            if any(employee_id not in member_ids for employee_id in mentioned_ids):
                raise HTTPException(status_code=400, detail="只能@当前会话成员")
        reply_snapshot = await ChatService._build_reply_snapshot(
            db,
            conversation_id=conversation.id,
            reply_to_message_id=reply_to_message_id,
        )
        next_seq = int(conversation.message_seq or 0) + 1
        scan_text = " ".join([content or "", attachment_name or ""])
        security_level, is_sensitive, sensitive_hits = ChatService._scan_sensitive_content(scan_text)
        message = ChatMessage(
            conversation_id=conversation.id,
            sender_id=current_user.id,
            message_seq=next_seq,
            client_message_id=dedupe_key,
            message_type=message_type,
            content=content,
            attachment_url=attachment_url,
            attachment_name=attachment_name,
            attachment_size=attachment_size,
            attachment_mime_type=attachment_mime_type,
            mentioned_employee_ids=mentioned_ids or None,
            mention_all=mention_all,
            reply_to_message_id=reply_to_message_id if reply_snapshot else None,
            reply_to_snapshot=reply_snapshot,
            metadata_json=metadata or None,
            security_level=security_level,
            is_sensitive=is_sensitive,
            created_at=now,
        )
        db.add(message)
        await db.flush()
        conversation.message_seq = next_seq
        conversation.last_message_id = message.id
        conversation.updated_at = now
        members_result = await db.execute(
            select(ChatParticipant).where(ChatParticipant.conversation_id == conversation.id)
        )
        for member in members_result.scalars().all():
            member.is_hidden = False
            member.hidden_at = None
            member.updated_at = now
            if member.employee_id == current_user.id:
                member.last_read_message_id = message.id
                member.last_read_at = now
                member.is_marked_unread = False
                member.marked_unread_at = None
                member.is_later = False
                member.later_note = None
                member.later_at = None
        participant.last_read_message_id = message.id
        participant.last_read_at = now
        participant.updated_at = now
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation.id,
            actor_id=current_user.id,
            action="send_message",
            message_id=message.id,
            detail={"message_type": message_type, "mention_all": mention_all, "message_seq": next_seq},
        )
        if is_sensitive:
            db.add(ChatSecurityEvent(
                conversation_id=conversation.id,
                actor_id=current_user.id,
                message_id=message.id,
                event_type="sensitive_message",
                risk_level="high",
                detail={"hits": sensitive_hits, "message_type": message_type},
                created_at=now,
            ))
        receipts = await ChatService._receipt_map(db, conversation.id, [message.id], current_user.id)
        return ChatService._message_out(message, current_user, current_user.id, receipts.get(message.id))

    @staticmethod
    async def _forward_messages_to_targets(
        db: AsyncSession,
        *,
        current_user: Employee,
        source_conversation_id: int,
        source_messages: list[ChatMessage],
        target_conversation_ids: list[int],
    ) -> ChatMessageForwardResponse:
        """复用真实发消息逻辑把源消息复制到目标会话。"""
        target_ids = list(dict.fromkeys(int(item) for item in target_conversation_ids if int(item) > 0))
        if not target_ids:
            raise HTTPException(status_code=400, detail="请选择转发目标会话")
        if len(target_ids) > 10:
            raise HTTPException(status_code=400, detail="一次最多转发到10个会话")

        created_items: list[ChatMessageOut] = []
        for target_id in target_ids:
            target_participant, target_conversation = await ChatService._get_participant_or_404(
                db,
                current_user_id=current_user.id,
                conversation_id=target_id,
            )
            for source in source_messages:
                created = await ChatService._create_message(
                    db,
                    current_user=current_user,
                    participant=target_participant,
                    conversation=target_conversation,
                    message_type=source.message_type,
                    content=source.content,
                    attachment_url=source.attachment_url,
                    attachment_name=source.attachment_name,
                    attachment_size=source.attachment_size,
                    attachment_mime_type=source.attachment_mime_type,
                    metadata=source.metadata_json,
                )
                created_items.append(created)
                await ChatService._audit(
                    db,
                    conversation_id=target_id,
                    actor_id=current_user.id,
                    action="forward_message",
                    message_id=created.id,
                    detail={
                        "source_conversation_id": source_conversation_id,
                        "source_message_id": source.id,
                        "message_type": source.message_type,
                    },
                )
        return ChatMessageForwardResponse(total=len(created_items), items=created_items)

    @staticmethod
    async def _build_reply_snapshot(
        db: AsyncSession,
        *,
        conversation_id: int,
        reply_to_message_id: int | None,
    ) -> dict | None:
        """生成引用消息快照，避免前端伪造引用内容。"""
        if not reply_to_message_id:
            return None
        message = await ChatService._get_message_or_404(
            db,
            conversation_id=conversation_id,
            message_id=reply_to_message_id,
        )
        if message.is_deleted:
            raise HTTPException(status_code=400, detail="不能回复已撤回消息")
        employees = await ChatService._load_employees(db, [message.sender_id])
        sender = employees.get(message.sender_id)
        sender_name = sender.name if sender else "未知员工"
        return {
            "id": message.id,
            "sender_name": sender_name,
            "message_type": message.message_type,
            "summary": ChatService._message_plain_summary(message),
        }

    @staticmethod
    async def mark_read(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> dict[str, int]:
        """将当前员工在会话中的消息标记为已读。"""
        participant, _conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        result = await db.execute(
            select(func.max(ChatMessage.id)).where(ChatMessage.conversation_id == conversation_id)
        )
        max_message_id = int(result.scalar_one_or_none() or 0)
        now = datetime.now(timezone.utc)
        participant.last_read_message_id = max_message_id
        participant.last_read_at = now
        participant.is_marked_unread = False
        participant.marked_unread_at = None
        participant.updated_at = now
        await db.flush()
        return {"last_read_message_id": max_message_id}

    @staticmethod
    async def update_conversation_setting(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        setting: str,
        enabled: bool,
    ) -> ChatConversationOut:
        """更新当前员工自己的会话置顶或免打扰设置。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        if setting == "pin":
            participant.is_pinned = enabled
        elif setting == "mute":
            participant.is_muted = enabled
        else:
            raise HTTPException(status_code=400, detail="不支持的会话设置")
        participant.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def update_conversation_efficiency(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        status_type: str,
        data: ChatConversationEfficiencyUpdate,
    ) -> ChatConversationOut:
        """更新标记未读或稍后处理状态。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        now = datetime.now(timezone.utc)
        if status_type == "mark_unread":
            participant.is_marked_unread = bool(data.enabled)
            participant.marked_unread_at = now if data.enabled else None
        elif status_type == "later":
            participant.is_later = bool(data.enabled)
            participant.later_note = data.note.strip() if data.enabled and data.note else None
            participant.later_at = now if data.enabled else None
        else:
            raise HTTPException(status_code=400, detail="不支持的效率状态")
        participant.updated_at = now
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action=f"update_{status_type}",
            detail={"enabled": data.enabled, "note": data.note},
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def hide_conversation(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> dict[str, bool]:
        """从当前员工的会话列表隐藏会话，收到新消息时自动重新出现。"""
        participant, _conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        now = datetime.now(timezone.utc)
        participant.is_hidden = True
        participant.hidden_at = now
        participant.updated_at = now
        await db.flush()
        return {"ok": True}

    @staticmethod
    async def create_ding(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        data: ChatDingCreate,
    ) -> ChatDingOut:
        """创建 DING 加急提醒，并同步生成一条会话卡片消息。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        recipient_ids = await ChatService._normalize_member_targets(
            db,
            conversation_id=conversation_id,
            requested_ids=data.recipient_ids,
            fallback_excluding=current_user.id,
        )
        if not recipient_ids:
            raise HTTPException(status_code=400, detail="DING 至少需要一名接收人")
        channels = [item for item in data.channels if item in {"app", "sms", "phone"}] or ["app"]
        now = datetime.now(timezone.utc)
        ding = ChatDing(
            conversation_id=conversation_id,
            sender_id=current_user.id,
            recipient_ids=recipient_ids,
            acked_employee_ids=[],
            channels=channels,
            content=data.content.strip(),
            status="active",
            created_at=now,
        )
        db.add(ding)
        await db.flush()
        message = await ChatService._create_message(
            db,
            current_user=current_user,
            participant=participant,
            conversation=conversation,
            message_type="ding",
            content=data.content.strip(),
            mentioned_employee_ids=recipient_ids,
            metadata={"ding_id": ding.id, "recipient_ids": recipient_ids, "channels": channels},
        )
        ding.message_id = message.id
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action="create_ding",
            message_id=message.id,
            detail={"ding_id": ding.id, "recipient_ids": recipient_ids, "channels": channels},
        )
        return ChatService._ding_out(ding, current_user)

    @staticmethod
    async def ack_ding(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        ding_id: int,
    ) -> ChatDingOut:
        """确认收到 DING。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        result = await db.execute(
            select(ChatDing).where(ChatDing.id == ding_id, ChatDing.conversation_id == conversation_id)
        )
        ding = result.scalar_one_or_none()
        if ding is None:
            raise HTTPException(status_code=404, detail="DING 不存在")
        recipient_ids = [int(item) for item in (ding.recipient_ids or [])]
        if current_user.id not in recipient_ids and current_user.id != ding.sender_id:
            raise HTTPException(status_code=403, detail="只有 DING 接收人可以确认")
        acked = {int(item) for item in (ding.acked_employee_ids or [])}
        acked.add(int(current_user.id))
        ding.acked_employee_ids = sorted(acked)
        if set(recipient_ids).issubset(acked):
            ding.status = "done"
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action="ack_ding",
            detail={"ding_id": ding.id},
        )
        return ChatService._ding_out(ding, current_user if current_user.id == ding.sender_id else None)

    @staticmethod
    async def list_dings(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        limit: int = 20,
    ) -> ChatDingListResponse:
        """读取当前会话 DING 记录。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        base = select(ChatDing).where(ChatDing.conversation_id == conversation_id)
        total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
        result = await db.execute(base.order_by(ChatDing.id.desc()).limit(limit))
        rows = list(result.scalars().all())
        employees = await ChatService._load_employees(db, [row.sender_id for row in rows])
        return ChatDingListResponse(
            total=total,
            items=[ChatService._ding_out(row, employees.get(row.sender_id)) for row in rows],
        )

    @staticmethod
    async def create_todo(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        data: ChatTodoCreate,
    ) -> ChatTodoOut:
        """创建群待办，并同步生成一条待办卡片消息。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        assignee_ids = await ChatService._normalize_member_targets(
            db,
            conversation_id=conversation_id,
            requested_ids=data.assignee_ids,
            fallback_excluding=0,
        )
        if not assignee_ids:
            raise HTTPException(status_code=400, detail="群待办至少需要一名负责人")
        now = datetime.now(timezone.utc)
        todo = ChatTodo(
            conversation_id=conversation_id,
            created_by_id=current_user.id,
            content=data.content.strip(),
            assignee_ids=assignee_ids,
            completed_by_ids=[],
            due_at=data.due_at,
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(todo)
        await db.flush()
        message = await ChatService._create_message(
            db,
            current_user=current_user,
            participant=participant,
            conversation=conversation,
            message_type="todo_card",
            content=data.content.strip(),
            mentioned_employee_ids=assignee_ids,
            metadata={"todo_id": todo.id, "assignee_ids": assignee_ids, "due_at": data.due_at.isoformat() if data.due_at else None},
        )
        todo.source_message_id = message.id
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action="create_todo",
            message_id=message.id,
            detail={"todo_id": todo.id, "assignee_ids": assignee_ids},
        )
        return ChatService._todo_out(todo, current_user)

    @staticmethod
    async def update_todo(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        todo_id: int,
        data: ChatTodoUpdate,
    ) -> ChatTodoOut:
        """更新或完成群待办。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        result = await db.execute(
            select(ChatTodo).where(ChatTodo.id == todo_id, ChatTodo.conversation_id == conversation_id)
        )
        todo = result.scalar_one_or_none()
        if todo is None:
            raise HTTPException(status_code=404, detail="群待办不存在")
        role = participant.member_role or "member"
        is_manager = role in {"owner", "admin"} or conversation.created_by_id == current_user.id
        if data.status in {"cancelled", "open"} and not is_manager and todo.created_by_id != current_user.id:
            raise HTTPException(status_code=403, detail="只有创建人或群管理员可以修改待办状态")
        completed = {int(item) for item in (todo.completed_by_ids or [])}
        if data.completed is not None:
            if current_user.id not in [int(item) for item in (todo.assignee_ids or [])] and not is_manager:
                raise HTTPException(status_code=403, detail="只有负责人可以完成待办")
            if data.completed:
                completed.add(int(current_user.id))
            else:
                completed.discard(int(current_user.id))
            todo.completed_by_ids = sorted(completed)
        if data.status:
            todo.status = data.status
        elif set(int(item) for item in (todo.assignee_ids or [])).issubset(completed):
            todo.status = "done"
        elif todo.status == "done":
            todo.status = "open"
        todo.updated_at = datetime.now(timezone.utc)
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action="update_todo",
            detail={"todo_id": todo.id, "status": todo.status, "completed": data.completed},
        )
        return ChatService._todo_out(todo, current_user if current_user.id == todo.created_by_id else None)

    @staticmethod
    async def list_todos(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        status_filter: str = "open",
        limit: int = 50,
    ) -> ChatTodoListResponse:
        """读取当前会话群待办。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        base = select(ChatTodo).where(ChatTodo.conversation_id == conversation_id)
        if status_filter in {"open", "done", "cancelled"}:
            base = base.where(ChatTodo.status == status_filter)
        total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
        result = await db.execute(base.order_by(ChatTodo.updated_at.desc(), ChatTodo.id.desc()).limit(limit))
        rows = list(result.scalars().all())
        employees = await ChatService._load_employees(db, [row.created_by_id for row in rows])
        return ChatTodoListResponse(
            total=total,
            items=[ChatService._todo_out(row, employees.get(row.created_by_id)) for row in rows],
        )

    @staticmethod
    async def create_meeting(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        data: ChatMeetingCreate,
    ) -> ChatMeetingOut:
        """在聊天会话中预约会议，并同步发送会议卡片。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        if conversation.is_dissolved:
            raise HTTPException(status_code=400, detail="群聊已解散，不能预约会议")
        starts_at = ChatService._normalize_meeting_datetime(data.starts_at)
        ends_at = ChatService._normalize_meeting_datetime(data.ends_at)
        if starts_at >= ends_at:
            raise HTTPException(status_code=400, detail="会议结束时间必须晚于开始时间")

        member_result = await db.execute(
            select(ChatParticipant.employee_id).where(ChatParticipant.conversation_id == conversation_id)
        )
        member_ids = {int(row[0]) for row in member_result.all()}
        requested_attendees = {int(item) for item in data.attendee_ids if int(item) > 0} or set(member_ids)
        requested_attendees.add(int(current_user.id))
        if requested_attendees - member_ids:
            raise HTTPException(status_code=400, detail="参会人必须属于当前会话")
        attendee_ids = sorted(requested_attendees)

        title = data.title.strip()
        room_name = data.room_name.strip() if data.room_name else None
        room_location = data.room_location.strip() if data.room_location else None
        equipment_needed = [item.strip() for item in data.equipment_needed if item.strip()]
        room_booking_id: int | None = None
        if data.meeting_type in {"offline", "hybrid"} and room_name:
            booking = await admin_service.create_room_booking(
                db,
                BookingRoomCreate(
                    room_name=room_name,
                    room_location=room_location,
                    date=starts_at.date(),
                    start_time=starts_at.time().replace(tzinfo=None),
                    end_time=ends_at.time().replace(tzinfo=None),
                    subject=title,
                    attendee_count=len(attendee_ids),
                    equipment_needed_json=json.dumps(equipment_needed, ensure_ascii=False) if equipment_needed else None,
                ),
                booker_id=current_user.id,
            )
            room_booking_id = int(booking.id)

        now = datetime.now(timezone.utc)
        meeting = ChatMeeting(
            conversation_id=conversation_id,
            organizer_id=current_user.id,
            room_booking_id=room_booking_id,
            meeting_no=ChatService._new_meeting_no(),
            title=title,
            meeting_type=data.meeting_type,
            starts_at=starts_at,
            ends_at=ends_at,
            attendee_ids=attendee_ids,
            room_name=room_name,
            room_location=room_location,
            join_url=data.join_url.strip() if data.join_url else None,
            reminder_minutes=data.reminder_minutes,
            agenda=data.agenda.strip() if data.agenda else None,
            equipment_needed=equipment_needed or None,
            status="scheduled",
            created_at=now,
            updated_at=now,
        )
        db.add(meeting)
        await db.flush()
        message = await ChatService._create_message(
            db,
            current_user=current_user,
            participant=participant,
            conversation=conversation,
            message_type="meeting_card",
            content=title,
            mentioned_employee_ids=[employee_id for employee_id in attendee_ids if employee_id != current_user.id],
            metadata=ChatService._meeting_card_metadata(meeting),
        )
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action="create_meeting",
            message_id=message.id,
            detail={
                "meeting_id": meeting.id,
                "meeting_no": meeting.meeting_no,
                "room_booking_id": room_booking_id,
                "attendee_ids": attendee_ids,
            },
        )
        return await ChatService._meeting_out(db, meeting)

    @staticmethod
    async def list_meetings(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        status_filter: str = "upcoming",
        limit: int = 50,
    ) -> ChatMeetingListResponse:
        """读取当前会话会议列表。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        now = datetime.now(timezone.utc)
        base = select(ChatMeeting).where(ChatMeeting.conversation_id == conversation_id)
        if status_filter == "upcoming":
            base = base.where(ChatMeeting.status == "scheduled", ChatMeeting.ends_at >= now)
        elif status_filter in {"scheduled", "cancelled"}:
            base = base.where(ChatMeeting.status == status_filter)
        total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
        result = await db.execute(base.order_by(ChatMeeting.starts_at.asc(), ChatMeeting.id.asc()).limit(limit))
        rows = list(result.scalars().all())
        return ChatMeetingListResponse(
            total=total,
            items=[await ChatService._meeting_out(db, row) for row in rows],
        )

    @staticmethod
    async def cancel_meeting(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        meeting_id: int,
    ) -> ChatMeetingOut:
        """取消聊天会议预约。"""
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        meeting = await ChatService._get_meeting_or_404(
            db,
            conversation_id=conversation_id,
            meeting_id=meeting_id,
        )
        is_manager = (participant.member_role or "member") in {"owner", "admin"} or conversation.created_by_id == current_user.id
        if int(meeting.organizer_id) != int(current_user.id) and not is_manager:
            raise HTTPException(status_code=403, detail="只有组织者或群管理员可以取消会议")
        if meeting.status == "cancelled":
            return await ChatService._meeting_out(db, meeting)
        if meeting.room_booking_id:
            await admin_service.cancel_room_booking(db, meeting.room_booking_id)
        meeting.status = "cancelled"
        meeting.updated_at = datetime.now(timezone.utc)
        await db.flush()
        message = await ChatService._create_message(
            db,
            current_user=current_user,
            participant=participant,
            conversation=conversation,
            message_type="meeting_card",
            content=f"会议已取消：{meeting.title}",
            mentioned_employee_ids=[employee_id for employee_id in (meeting.attendee_ids or []) if employee_id != current_user.id],
            metadata=ChatService._meeting_card_metadata(meeting),
        )
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action="cancel_meeting",
            message_id=message.id,
            detail={"meeting_id": meeting.id, "meeting_no": meeting.meeting_no},
        )
        return await ChatService._meeting_out(db, meeting)

    @staticmethod
    async def create_call(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        data: ChatCallCreate,
    ) -> ChatCallOut:
        """发起语音/视频通话并创建真实信令房间。"""
        _participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        if conversation.is_dissolved:
            raise HTTPException(status_code=400, detail="群聊已解散，不能发起通话")
        target_ids = await ChatService._normalize_member_targets(
            db,
            conversation_id=conversation_id,
            requested_ids=data.recipient_ids,
            fallback_excluding=current_user.id,
        )
        target_ids = [employee_id for employee_id in target_ids if int(employee_id) != int(current_user.id)]
        if not target_ids:
            raise HTTPException(status_code=400, detail="请选择通话对象")
        if len(target_ids) > 8:
            raise HTTPException(status_code=400, detail="当前版本最多支持 8 人通话")
        now = datetime.now(timezone.utc)
        participant_ids = sorted({int(current_user.id), *[int(item) for item in target_ids]})
        call = ChatCall(
            conversation_id=conversation_id,
            initiator_id=current_user.id,
            call_type=data.call_type,
            status="ringing",
            participant_ids=participant_ids,
            accepted_employee_ids=[int(current_user.id)],
            rejected_employee_ids=[],
            created_at=now,
            updated_at=now,
        )
        db.add(call)
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action="create_call",
            detail={"call_id": call.id, "call_type": data.call_type, "participant_ids": participant_ids},
        )
        return await ChatService._call_out(db, call)

    @staticmethod
    async def list_active_calls(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> ChatCallListResponse:
        """读取当前会话仍在响铃或进行中的通话。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        result = await db.execute(
            select(ChatCall).where(
                ChatCall.conversation_id == conversation_id,
                ChatCall.status.in_(["ringing", "ongoing"]),
            ).order_by(ChatCall.updated_at.desc(), ChatCall.id.desc())
        )
        rows = [
            call for call in result.scalars().all()
            if int(current_user_id) in {int(item) for item in (call.participant_ids or [])}
        ]
        return ChatCallListResponse(total=len(rows), items=[await ChatService._call_out(db, call) for call in rows])

    @staticmethod
    async def get_call(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        call_id: int,
    ) -> ChatCallOut:
        """读取单个通话房间。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        call = await ChatService._get_call_or_404(db, conversation_id=conversation_id, call_id=call_id)
        ChatService._ensure_call_participant(call, current_user_id)
        return await ChatService._call_out(db, call)

    @staticmethod
    async def update_call_status(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        call_id: int,
        data: ChatCallStatusUpdate,
    ) -> ChatCallOut:
        """接听、拒绝或挂断通话。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        call = await ChatService._get_call_or_404(db, conversation_id=conversation_id, call_id=call_id)
        ChatService._ensure_call_participant(call, current_user.id)
        now = datetime.now(timezone.utc)
        accepted = {int(item) for item in (call.accepted_employee_ids or [])}
        rejected = {int(item) for item in (call.rejected_employee_ids or [])}
        participants = {int(item) for item in (call.participant_ids or [])}
        if data.action == "accept":
            accepted.add(int(current_user.id))
            rejected.discard(int(current_user.id))
            call.accepted_employee_ids = sorted(accepted)
            call.rejected_employee_ids = sorted(rejected)
            if call.status == "ringing":
                call.status = "ongoing"
                call.started_at = call.started_at or now
        elif data.action == "reject":
            rejected.add(int(current_user.id))
            call.rejected_employee_ids = sorted(rejected)
            if participants - {int(call.initiator_id)} <= rejected:
                call.status = "rejected"
                call.ended_at = now
                call.ended_by_id = current_user.id
                call.ended_reason = data.reason or "rejected"
        elif data.action == "miss":
            call.status = "missed"
            call.ended_at = now
            call.ended_reason = data.reason or "missed"
        else:
            call.status = "ended"
            call.ended_at = now
            call.ended_by_id = current_user.id
            call.ended_reason = data.reason or "ended"
        call.updated_at = now
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user.id,
            action=f"call_{data.action}",
            detail={"call_id": call.id, "status": call.status, "reason": data.reason},
        )
        return await ChatService._call_out(db, call)

    @staticmethod
    async def create_call_signal(
        db: AsyncSession,
        *,
        current_user: Employee,
        conversation_id: int,
        call_id: int,
        data: ChatCallSignalCreate,
    ) -> ChatCallSignalOut:
        """写入 WebRTC offer/answer/ice 信令。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user.id,
            conversation_id=conversation_id,
        )
        call = await ChatService._get_call_or_404(db, conversation_id=conversation_id, call_id=call_id)
        ChatService._ensure_call_participant(call, current_user.id)
        if data.target_employee_id is not None:
            ChatService._ensure_call_participant(call, data.target_employee_id)
        signal = ChatCallSignal(
            call_id=call_id,
            conversation_id=conversation_id,
            sender_id=current_user.id,
            target_employee_id=data.target_employee_id,
            signal_type=data.signal_type,
            payload_json=data.payload,
            created_at=datetime.now(timezone.utc),
        )
        call.updated_at = datetime.now(timezone.utc)
        db.add(signal)
        await db.flush()
        return ChatService._signal_out(signal, current_user)

    @staticmethod
    async def poll_call(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        call_id: int,
        after_signal_id: int = 0,
    ) -> ChatCallPollResponse:
        """按游标拉取当前通话的新增信令。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        call = await ChatService._get_call_or_404(db, conversation_id=conversation_id, call_id=call_id)
        ChatService._ensure_call_participant(call, current_user_id)
        result = await db.execute(
            select(ChatCallSignal).where(
                ChatCallSignal.call_id == call_id,
                ChatCallSignal.id > after_signal_id,
                or_(
                    ChatCallSignal.target_employee_id.is_(None),
                    ChatCallSignal.target_employee_id == current_user_id,
                    ChatCallSignal.sender_id == current_user_id,
                ),
            ).order_by(ChatCallSignal.id.asc()).limit(100)
        )
        signals = list(result.scalars().all())
        employees = await ChatService._load_employees(db, [signal.sender_id for signal in signals])
        return ChatCallPollResponse(
            call=await ChatService._call_out(db, call),
            signals=[ChatService._signal_out(signal, employees.get(signal.sender_id)) for signal in signals],
        )

    @staticmethod
    async def search_messages(
        db: AsyncSession,
        *,
        current_user_id: int,
        keyword: str | None = None,
        conversation_id: int | None = None,
        message_type: str | None = None,
        sender_id: int | None = None,
        conversation_type: str | None = None,
        has_attachment: bool | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
    ) -> tuple[list[ChatMessageOut], int]:
        """在当前员工可见会话中做高级消息搜索。"""
        query = keyword.strip() if keyword else ""
        if conversation_id:
            _participant, conversation = await ChatService._get_participant_or_404(
                db,
                current_user_id=current_user_id,
                conversation_id=conversation_id,
            )
            if conversation_type and conversation.conversation_type != conversation_type:
                return [], 0
            conversation_ids = [conversation_id]
        else:
            participant_stmt = (
                select(ChatParticipant.conversation_id)
                .join(ChatConversation, ChatConversation.id == ChatParticipant.conversation_id)
                .where(
                    ChatParticipant.employee_id == current_user_id,
                    ChatParticipant.is_hidden.is_(False),
                )
            )
            if conversation_type in {"direct", "group"}:
                participant_stmt = participant_stmt.where(ChatConversation.conversation_type == conversation_type)
            participant_result = await db.execute(participant_stmt)
            conversation_ids = [int(row[0]) for row in participant_result.all()]
        if not conversation_ids:
            return [], 0
        if not query and not any([message_type, sender_id, has_attachment is not None, date_from, date_to]):
            return [], 0

        base = select(ChatMessage).where(
            ChatMessage.conversation_id.in_(conversation_ids),
            ChatMessage.is_deleted.is_(False),
        )
        if query:
            like_pattern = f"%{query}%"
            base = base.where(or_(ChatMessage.content.ilike(like_pattern), ChatMessage.attachment_name.ilike(like_pattern)))
        mention_only = message_type == "mention"
        if message_type and message_type != "all":
            if message_type == "media":
                base = base.where(ChatMessage.message_type.in_(["image", "video", "audio"]))
            elif message_type == "card":
                base = base.where(ChatMessage.message_type.in_(["ding", "todo_card", "meeting_card", "business_card", "approval_card", "attendance_card", "payslip_card"]))
            elif message_type == "mention":
                base = base.where(or_(ChatMessage.mention_all.is_(True), ChatMessage.mentioned_employee_ids.isnot(None)))
            else:
                base = base.where(ChatMessage.message_type == message_type)
        if sender_id:
            base = base.where(ChatMessage.sender_id == int(sender_id))
        if has_attachment is True:
            base = base.where(ChatMessage.attachment_url.isnot(None))
        elif has_attachment is False:
            base = base.where(ChatMessage.attachment_url.is_(None))
        if date_from:
            base = base.where(ChatMessage.created_at >= date_from)
        if date_to:
            base = base.where(ChatMessage.created_at <= date_to)
        if mention_only:
            result = await db.execute(base.order_by(ChatMessage.id.desc()))
            messages = [
                message
                for message in result.scalars().all()
                if message.mention_all or (message.mentioned_employee_ids or [])
            ]
            total = len(messages)
            messages = messages[:limit]
        else:
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
            result = await db.execute(base.order_by(ChatMessage.id.desc()).limit(limit))
            messages = list(result.scalars().all())
        employees = await ChatService._load_employees(db, [message.sender_id for message in messages])
        receipts_by_message: dict[int, tuple[int, int]] = {}
        for item in messages:
            receipts = await ChatService._receipt_map(db, item.conversation_id, [item.id], current_user_id)
            receipts_by_message[item.id] = receipts.get(item.id, (0, 0))
        return [
            ChatService._message_out(
                item,
                employees.get(item.sender_id),
                current_user_id,
                receipts_by_message.get(item.id),
            )
            for item in messages
        ], int(total or 0)

    @staticmethod
    async def publish_announcement(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        data: ChatAnnouncementCreate,
    ) -> ChatConversationOut:
        """发布群公告并写入公告历史。"""
        participant, conversation = await ChatService._require_group_manager(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        content = data.content.strip()
        conversation.announcement = content
        conversation.updated_at = datetime.now(timezone.utc)
        db.add(ChatAnnouncement(
            conversation_id=conversation_id,
            content=content,
            created_by_id=current_user_id,
        ))
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="publish_announcement",
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def list_announcements(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        limit: int = 20,
    ) -> ChatAnnouncementListResponse:
        """读取群公告历史。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        base = select(ChatAnnouncement).where(ChatAnnouncement.conversation_id == conversation_id)
        total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
        result = await db.execute(base.order_by(ChatAnnouncement.id.desc()).limit(limit))
        rows = list(result.scalars().all())
        employees = await ChatService._load_employees(db, [row.created_by_id for row in rows])
        return ChatAnnouncementListResponse(
            total=total,
            items=[
                ChatAnnouncementOut(
                    id=row.id,
                    conversation_id=row.conversation_id,
                    content=row.content,
                    created_by_id=row.created_by_id,
                    created_by_name=employees.get(row.created_by_id).name if employees.get(row.created_by_id) else "未知员工",
                    created_at=row.created_at,
                )
                for row in rows
            ],
        )

    @staticmethod
    async def mark_announcement_read(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        announcement_id: int,
    ) -> ChatAnnouncementReceiptResponse:
        """标记群公告已读并返回实时已读统计。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        announcement = await ChatService._get_announcement_or_404(
            db,
            conversation_id=conversation_id,
            announcement_id=announcement_id,
        )
        existing_result = await db.execute(
            select(ChatAnnouncementReceipt).where(
                ChatAnnouncementReceipt.announcement_id == announcement.id,
                ChatAnnouncementReceipt.employee_id == current_user_id,
            )
        )
        if existing_result.scalar_one_or_none() is None:
            db.add(ChatAnnouncementReceipt(
                announcement_id=announcement.id,
                conversation_id=conversation_id,
                employee_id=current_user_id,
                read_at=datetime.now(timezone.utc),
            ))
            await db.flush()
        return await ChatService.get_announcement_receipts(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
            announcement_id=announcement_id,
        )

    @staticmethod
    async def get_announcement_receipts(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        announcement_id: int,
    ) -> ChatAnnouncementReceiptResponse:
        """读取群公告已读未读统计。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        await ChatService._get_announcement_or_404(
            db,
            conversation_id=conversation_id,
            announcement_id=announcement_id,
        )
        members_result = await db.execute(
            select(ChatParticipant).where(ChatParticipant.conversation_id == conversation_id)
        )
        members = list(members_result.scalars().all())
        receipts_result = await db.execute(
            select(ChatAnnouncementReceipt.employee_id).where(ChatAnnouncementReceipt.announcement_id == announcement_id)
        )
        read_ids = {int(row[0]) for row in receipts_result.all()}
        employees = await ChatService._load_employees(db, [member.employee_id for member in members])
        read_members: list[ChatEmployeeBrief] = []
        unread_members: list[ChatEmployeeBrief] = []
        for member in members:
            employee = employees.get(member.employee_id)
            if employee is None:
                continue
            brief = ChatService._employee_brief(employee, member)
            if member.employee_id in read_ids:
                read_members.append(brief)
            else:
                unread_members.append(brief)
        return ChatAnnouncementReceiptResponse(
            announcement_id=announcement_id,
            read_count=len(read_members),
            unread_count=len(unread_members),
            read_members=read_members,
            unread_members=unread_members,
        )

    @staticmethod
    async def pin_message(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        message_id: int,
    ) -> ChatConversationOut:
        """置顶群消息，最多保留 5 条。"""
        participant, conversation = await ChatService._require_group_manager(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        message = await ChatService._get_message_or_404(db, conversation_id=conversation_id, message_id=message_id)
        existing = await db.execute(
            select(ChatPinnedMessage).where(
                ChatPinnedMessage.conversation_id == conversation_id,
                ChatPinnedMessage.message_id == message_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            count_result = await db.execute(
                select(func.count()).select_from(ChatPinnedMessage).where(ChatPinnedMessage.conversation_id == conversation_id)
            )
            if int(count_result.scalar_one() or 0) >= 5:
                raise HTTPException(status_code=400, detail="每个群最多置顶5条消息")
            db.add(ChatPinnedMessage(
                conversation_id=conversation_id,
                message_id=message_id,
                pinned_by_id=current_user_id,
            ))
        message.is_pinned = True
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="pin_message",
            message_id=message_id,
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def unpin_message(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        message_id: int,
    ) -> ChatConversationOut:
        """取消置顶群消息。"""
        participant, conversation = await ChatService._require_group_manager(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        result = await db.execute(
            select(ChatPinnedMessage).where(
                ChatPinnedMessage.conversation_id == conversation_id,
                ChatPinnedMessage.message_id == message_id,
            )
        )
        pinned = result.scalar_one_or_none()
        if pinned:
            await db.delete(pinned)
        message = await ChatService._get_message_or_404(db, conversation_id=conversation_id, message_id=message_id)
        message.is_pinned = False
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="unpin_message",
            message_id=message_id,
        )
        return await ChatService._build_conversation_out(db, current_user_id, participant, conversation)

    @staticmethod
    async def recall_message(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        message_id: int,
    ) -> ChatMessageOut:
        """撤回当前员工自己发送的 24 小时内消息。"""
        await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.conversation_id == conversation_id,
            )
        )
        message = result.scalar_one_or_none()
        if message is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        if message.sender_id != current_user_id:
            raise HTTPException(status_code=403, detail="只能撤回自己发送的消息")
        now = datetime.now(timezone.utc)
        created_at = message.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if now - created_at > ChatService.RECALL_WINDOW:
            raise HTTPException(status_code=400, detail="只能撤回24小时内的消息")
        message.is_deleted = True
        message.content = ""
        message.recalled_at = now
        await db.flush()
        await ChatService._audit(
            db,
            conversation_id=conversation_id,
            actor_id=current_user_id,
            action="recall_message",
            message_id=message.id,
        )
        employees = await ChatService._load_employees(db, [message.sender_id])
        receipts = await ChatService._receipt_map(db, conversation_id, [message.id], current_user_id)
        return ChatService._message_out(message, employees.get(message.sender_id), current_user_id, receipts.get(message.id))

    @staticmethod
    async def sync_conversation(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
        after_seq: int = 0,
        limit: int = 100,
    ) -> ChatSyncResponse:
        """弱网恢复时按服务端序号拉取增量消息。"""
        _participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        result = await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.message_seq > int(after_seq or 0),
            )
            .order_by(ChatMessage.message_seq.asc(), ChatMessage.id.asc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        employees = await ChatService._load_employees(db, [message.sender_id for message in messages])
        receipts = await ChatService._receipt_map(db, conversation_id, [message.id for message in messages], current_user_id)
        return ChatSyncResponse(
            conversation_id=conversation_id,
            latest_seq=int(conversation.message_seq or 0),
            items=[
                ChatService._message_out(message, employees.get(message.sender_id), current_user_id, receipts.get(message.id))
                for message in messages
            ],
        )

    @staticmethod
    async def security_summary(
        db: AsyncSession,
        *,
        current_user_id: int,
        limit: int = 20,
    ) -> ChatSecuritySummaryResponse:
        """读取当前员工可见会话范围内的聊天安全治理摘要。"""
        participant_result = await db.execute(
            select(ChatParticipant.conversation_id).where(
                ChatParticipant.employee_id == current_user_id,
                ChatParticipant.is_hidden.is_(False),
            )
        )
        conversation_ids = [int(row[0]) for row in participant_result.all()]
        if not conversation_ids:
            return ChatSecuritySummaryResponse(total_events=0, high_risk_events=0, recent_events=[])
        base = select(ChatSecurityEvent).where(ChatSecurityEvent.conversation_id.in_(conversation_ids))
        total = int((await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one() or 0)
        high_risk = int((
            await db.execute(
                select(func.count()).select_from(
                    base.where(ChatSecurityEvent.risk_level.in_(["high", "critical"])).subquery()
                )
            )
        ).scalar_one() or 0)
        result = await db.execute(base.order_by(ChatSecurityEvent.id.desc()).limit(limit))
        recent = [
            {
                "id": item.id,
                "conversation_id": item.conversation_id,
                "message_id": item.message_id,
                "event_type": item.event_type,
                "risk_level": item.risk_level,
                "detail": item.detail or {},
                "created_at": item.created_at.isoformat(),
            }
            for item in result.scalars().all()
        ]
        return ChatSecuritySummaryResponse(total_events=total, high_risk_events=high_risk, recent_events=recent)

    @staticmethod
    async def get_unread_count(db: AsyncSession, *, current_user_id: int) -> int:
        """统计当前员工所有会话未读消息数。"""
        result = await db.execute(
            select(ChatParticipant).where(
                ChatParticipant.employee_id == current_user_id,
                ChatParticipant.is_hidden.is_(False),
            )
        )
        participants = list(result.scalars().all())
        total = 0
        for participant in participants:
            total += await ChatService._unread_count(db, current_user_id, participant)
        return total

    @staticmethod
    async def _get_active_employee(db: AsyncSession, employee_id: int) -> Employee:
        result = await db.execute(
            select(Employee)
            .options(selectinload(Employee.department))
            .where(Employee.id == employee_id, Employee.is_active.is_(True))
        )
        employee = result.scalar_one_or_none()
        if employee is None:
            raise HTTPException(status_code=404, detail="员工不存在或已停用")
        return employee

    @staticmethod
    async def _get_participant_or_404(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> tuple[ChatParticipant, ChatConversation]:
        result = await db.execute(
            select(ChatParticipant, ChatConversation)
            .join(ChatConversation, ChatConversation.id == ChatParticipant.conversation_id)
            .where(
                ChatParticipant.conversation_id == conversation_id,
                ChatParticipant.employee_id == current_user_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在或无权访问")
        return row[0], row[1]

    @staticmethod
    async def _require_group_manager(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> tuple[ChatParticipant, ChatConversation]:
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        if conversation.conversation_type != "group":
            raise HTTPException(status_code=400, detail="该会话不是群聊")
        if conversation.is_dissolved:
            raise HTTPException(status_code=400, detail="群聊已解散")
        role = participant.member_role or "member"
        if role not in {"owner", "admin"} and conversation.created_by_id != current_user_id:
            raise HTTPException(status_code=403, detail="只有群主或管理员可以修改群聊")
        return participant, conversation

    @staticmethod
    async def _require_owner(
        db: AsyncSession,
        *,
        current_user_id: int,
        conversation_id: int,
    ) -> tuple[ChatParticipant, ChatConversation]:
        participant, conversation = await ChatService._get_participant_or_404(
            db,
            current_user_id=current_user_id,
            conversation_id=conversation_id,
        )
        if conversation.conversation_type != "group":
            raise HTTPException(status_code=400, detail="该会话不是群聊")
        if (participant.member_role or "member") != "owner" and conversation.created_by_id != current_user_id:
            raise HTTPException(status_code=403, detail="只有群主可以操作")
        return participant, conversation

    @staticmethod
    async def _get_member_participant(
        db: AsyncSession,
        *,
        conversation_id: int,
        employee_id: int,
    ) -> ChatParticipant:
        result = await db.execute(
            select(ChatParticipant).where(
                ChatParticipant.conversation_id == conversation_id,
                ChatParticipant.employee_id == employee_id,
            )
        )
        participant = result.scalar_one_or_none()
        if participant is None:
            raise HTTPException(status_code=404, detail="群成员不存在")
        return participant

    @staticmethod
    async def _get_message_or_404(db: AsyncSession, *, conversation_id: int, message_id: int) -> ChatMessage:
        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.conversation_id == conversation_id,
            )
        )
        message = result.scalar_one_or_none()
        if message is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        return message

    @staticmethod
    async def _build_conversation_out(
        db: AsyncSession,
        current_user_id: int,
        participant: ChatParticipant,
        conversation: ChatConversation,
    ) -> ChatConversationOut:
        members_result = await db.execute(
            select(ChatParticipant).where(ChatParticipant.conversation_id == conversation.id)
        )
        members = list(members_result.scalars().all())
        employees = await ChatService._load_employees(db, [member.employee_id for member in members])
        member_by_employee_id = {member.employee_id: member for member in members}
        participant_briefs = [
            ChatService._employee_brief(employee, member_by_employee_id.get(employee_id))
            for employee_id, employee in employees.items()
            if employee_id in {member.employee_id for member in members}
        ]
        other_employees = [
            employees[member.employee_id]
            for member in members
            if member.employee_id != current_user_id and member.employee_id in employees
        ]
        base_display_name = conversation.title or "、".join(employee.name for employee in other_employees) or "聊天"
        display_name = participant.conversation_alias or base_display_name
        display_avatar_text = ChatService._avatar_text(display_name)

        last_message = None
        if conversation.last_message_id:
            last_result = await db.execute(
                select(ChatMessage).where(ChatMessage.id == conversation.last_message_id)
            )
            last = last_result.scalar_one_or_none()
            if last:
                receipts = await ChatService._receipt_map(db, conversation.id, [last.id], current_user_id)
                last_message = ChatService._message_out(
                    last,
                    employees.get(last.sender_id),
                    current_user_id,
                    receipts.get(last.id),
                )

        pinned_messages: list[ChatMessageOut] = []
        pinned_result = await db.execute(
            select(ChatPinnedMessage, ChatMessage)
            .join(ChatMessage, ChatMessage.id == ChatPinnedMessage.message_id)
            .where(ChatPinnedMessage.conversation_id == conversation.id)
            .order_by(ChatPinnedMessage.created_at.desc())
            .limit(5)
        )
        pinned_rows = pinned_result.all()
        if pinned_rows:
            pinned_sender_ids = {message.sender_id for _pinned, message in pinned_rows}
            pinned_employees = await ChatService._load_employees(db, pinned_sender_ids)
            for _pinned, message in pinned_rows:
                receipts = await ChatService._receipt_map(db, conversation.id, [message.id], current_user_id)
                pinned_messages.append(ChatService._message_out(
                    message,
                    pinned_employees.get(message.sender_id),
                    current_user_id,
                    receipts.get(message.id),
                ))

        can_manage = conversation.conversation_type == "group" and (
            (participant.member_role or "member") in {"owner", "admin"} or conversation.created_by_id == current_user_id
        )
        is_owner = conversation.conversation_type == "group" and (
            (participant.member_role or "member") == "owner" or conversation.created_by_id == current_user_id
        )
        has_unread_mention = await ChatService._has_unread_mention(db, current_user_id, participant)
        pending_todo_count = await ChatService._pending_todo_count(db, current_user_id, conversation.id)
        upcoming_meeting_count = await ChatService._upcoming_meeting_count(db, conversation.id)
        return ChatConversationOut(
            id=conversation.id,
            conversation_type=conversation.conversation_type,
            title=conversation.title,
            announcement=conversation.announcement,
            current_member_role=participant.member_role or "member",
            conversation_alias=participant.conversation_alias,
            group_nickname=participant.group_nickname,
            display_name=display_name,
            display_avatar_text=display_avatar_text,
            participants=participant_briefs,
            pinned_messages=pinned_messages,
            last_message=last_message,
            unread_count=await ChatService._unread_count(db, current_user_id, participant),
            is_muted=participant.is_muted,
            is_pinned=participant.is_pinned,
            is_marked_unread=participant.is_marked_unread,
            is_later=participant.is_later,
            later_note=participant.later_note,
            later_at=participant.later_at,
            has_unread_mention=has_unread_mention,
            pending_todo_count=pending_todo_count,
            upcoming_meeting_count=upcoming_meeting_count,
            only_owner_or_admin_can_manage=conversation.only_owner_or_admin_can_manage,
            invite_requires_approval=conversation.invite_requires_approval,
            only_owner_or_admin_can_at_all=conversation.only_owner_or_admin_can_at_all,
            is_muted_all=conversation.is_muted_all,
            is_dissolved=conversation.is_dissolved,
            can_manage=can_manage,
            can_manage_owner_actions=is_owner,
            updated_at=conversation.updated_at,
        )

    @staticmethod
    async def _load_employees(db: AsyncSession, employee_ids: Iterable[int]) -> dict[int, Employee]:
        ids = {int(employee_id) for employee_id in employee_ids if employee_id}
        if not ids:
            return {}
        result = await db.execute(
            select(Employee).options(selectinload(Employee.department)).where(Employee.id.in_(ids))
        )
        return {employee.id: employee for employee in result.scalars().all()}

    @staticmethod
    async def _load_active_employees(db: AsyncSession, employee_ids: Iterable[int]) -> dict[int, Employee]:
        ids = {int(employee_id) for employee_id in employee_ids if employee_id}
        if not ids:
            return {}
        result = await db.execute(
            select(Employee)
            .options(selectinload(Employee.department))
            .where(Employee.id.in_(ids), Employee.is_active.is_(True))
        )
        return {employee.id: employee for employee in result.scalars().all()}

    @staticmethod
    async def _audit(
        db: AsyncSession,
        *,
        conversation_id: int,
        actor_id: int,
        action: str,
        target_employee_id: int | None = None,
        message_id: int | None = None,
        detail: dict | None = None,
    ) -> None:
        db.add(ChatAuditLog(
            conversation_id=conversation_id,
            actor_id=actor_id,
            action=action,
            target_employee_id=target_employee_id,
            message_id=message_id,
            detail=detail,
        ))

    @staticmethod
    async def _receipt_map(
        db: AsyncSession,
        conversation_id: int,
        message_ids: Iterable[int],
        current_user_id: int,
    ) -> dict[int, tuple[int, int]]:
        ids = [int(message_id) for message_id in message_ids]
        if not ids:
            return {}
        participants_result = await db.execute(
            select(ChatParticipant).where(ChatParticipant.conversation_id == conversation_id)
        )
        participants = list(participants_result.scalars().all())
        receipt_by_id: dict[int, tuple[int, int]] = {}
        for message_id in ids:
            read_count = 0
            unread_count = 0
            for participant in participants:
                if participant.employee_id == current_user_id:
                    continue
                if (participant.last_read_message_id or 0) >= message_id:
                    read_count += 1
                else:
                    unread_count += 1
            receipt_by_id[message_id] = (read_count, unread_count)
        return receipt_by_id

    @staticmethod
    async def _unread_count(db: AsyncSession, current_user_id: int, participant: ChatParticipant) -> int:
        result = await db.execute(
            select(func.count()).select_from(ChatMessage).where(
                ChatMessage.conversation_id == participant.conversation_id,
                ChatMessage.id > (participant.last_read_message_id or 0),
                ChatMessage.sender_id != current_user_id,
                ChatMessage.is_deleted.is_(False),
            )
        )
        actual_unread = int(result.scalar_one() or 0)
        if participant.is_marked_unread and actual_unread == 0:
            return 1
        return actual_unread

    @staticmethod
    async def _has_unread_mention(db: AsyncSession, current_user_id: int, participant: ChatParticipant) -> bool:
        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.conversation_id == participant.conversation_id,
                ChatMessage.id > (participant.last_read_message_id or 0),
                ChatMessage.sender_id != current_user_id,
                ChatMessage.is_deleted.is_(False),
            ).order_by(ChatMessage.id.desc()).limit(80)
        )
        for message in result.scalars().all():
            if message.mention_all:
                return True
            if current_user_id in [int(item) for item in (message.mentioned_employee_ids or [])]:
                return True
        return False

    @staticmethod
    async def _pending_todo_count(db: AsyncSession, current_user_id: int, conversation_id: int) -> int:
        result = await db.execute(
            select(ChatTodo).where(
                ChatTodo.conversation_id == conversation_id,
                ChatTodo.status == "open",
            )
        )
        total = 0
        for todo in result.scalars().all():
            assignees = {int(item) for item in (todo.assignee_ids or [])}
            completed = {int(item) for item in (todo.completed_by_ids or [])}
            if current_user_id in assignees and current_user_id not in completed:
                total += 1
        return total

    @staticmethod
    async def _upcoming_meeting_count(db: AsyncSession, conversation_id: int) -> int:
        result = await db.execute(
            select(func.count()).select_from(ChatMeeting).where(
                ChatMeeting.conversation_id == conversation_id,
                ChatMeeting.status == "scheduled",
                ChatMeeting.ends_at >= datetime.now(timezone.utc),
            )
        )
        return int(result.scalar_one() or 0)

    @staticmethod
    def _employee_brief(employee: Employee, participant: ChatParticipant | None = None) -> ChatEmployeeBrief:
        return ChatEmployeeBrief(
            id=employee.id,
            employee_no=employee.employee_no,
            name=employee.name,
            avatar_text=ChatService._avatar_text(employee.name),
            position=employee.position,
            department_id=employee.department_id,
            department_name=employee.department.name if employee.department else None,
            member_role=(participant.member_role if participant else "member") or "member",
            group_nickname=participant.group_nickname if participant else None,
        )

    @staticmethod
    def _message_out(
        message: ChatMessage,
        sender: Employee | None,
        current_user_id: int,
        receipt: tuple[int, int] | None = None,
    ) -> ChatMessageOut:
        sender_name = sender.name if sender else "未知员工"
        content = "消息已撤回" if message.is_deleted else message.content
        read_count, unread_count = receipt or (0, 0)
        is_own = message.sender_id == current_user_id
        read_status = "none"
        if is_own:
            read_status = "read" if read_count > 0 and unread_count == 0 else "unread"
        created_at = message.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        can_recall = is_own and not message.is_deleted and datetime.now(timezone.utc) - created_at <= ChatService.RECALL_WINDOW
        return ChatMessageOut(
            id=message.id,
            conversation_id=message.conversation_id,
            sender_id=message.sender_id,
            sender_name=sender_name,
            sender_avatar_text=ChatService._avatar_text(sender_name),
            message_seq=message.message_seq or 0,
            client_message_id=None if message.is_deleted else message.client_message_id,
            message_type=message.message_type,
            content=content,
            attachment_url=None if message.is_deleted else message.attachment_url,
            attachment_name=None if message.is_deleted else message.attachment_name,
            attachment_size=None if message.is_deleted else message.attachment_size,
            attachment_mime_type=None if message.is_deleted else message.attachment_mime_type,
            metadata=None if message.is_deleted else message.metadata_json,
            security_level=message.security_level or "normal",
            is_sensitive=False if message.is_deleted else bool(message.is_sensitive),
            mentioned_employee_ids=[] if message.is_deleted else (message.mentioned_employee_ids or []),
            mention_all=False if message.is_deleted else message.mention_all,
            reply_to_message_id=None if message.is_deleted else message.reply_to_message_id,
            reply_to=None if message.is_deleted else message.reply_to_snapshot,
            is_pinned=message.is_pinned,
            is_deleted=message.is_deleted,
            is_own=is_own,
            read_status=read_status,
            read_count=read_count,
            unread_count=unread_count,
            can_recall=can_recall,
            recalled_at=message.recalled_at,
            created_at=message.created_at,
        )

    @staticmethod
    def _avatar_text(name: str | None) -> str:
        value = str(name or "").strip()
        return value[-2:] if value else "员工"

    @staticmethod
    def _message_plain_summary(message: ChatMessage) -> str:
        if message.message_type == "image":
            return "[图片]" if not message.content else f"[图片] {message.content[:80]}"
        if message.message_type == "video":
            return f"[视频] {message.attachment_name or message.content or '视频'}"
        if message.message_type == "audio":
            return f"[语音] {message.attachment_name or message.content or '语音'}"
        if message.message_type == "file":
            return f"[文件] {message.attachment_name or '附件'}"
        if message.message_type == "location":
            return f"[位置] {message.content or '位置'}"
        if message.message_type == "ding":
            return f"[DING] {message.content or '加急提醒'}"
        if message.message_type == "todo_card":
            return f"[群待办] {message.content or '待办'}"
        if message.message_type == "meeting_card":
            return f"[会议] {message.content or '会议预约'}"
        if message.message_type in {"business_card", "approval_card", "attendance_card", "payslip_card"}:
            return f"[业务卡片] {message.content or '业务消息'}"
        text = (message.content or "").strip()
        return text[:120] if text else "消息"

    @staticmethod
    def _conversation_matches_filter(item: ChatConversationOut, conversation_filter: str) -> bool:
        if conversation_filter == "unread":
            return item.unread_count > 0 or item.is_marked_unread
        if conversation_filter == "at_me":
            return item.has_unread_mention
        if conversation_filter == "todo":
            return item.pending_todo_count > 0
        if conversation_filter == "meeting":
            return item.upcoming_meeting_count > 0
        if conversation_filter == "later":
            return item.is_later
        if conversation_filter in {"direct", "group"}:
            return item.conversation_type == conversation_filter
        if conversation_filter == "muted":
            return item.is_muted
        if conversation_filter == "file":
            return bool(item.last_message and item.last_message.message_type in {"file", "image", "video", "audio"})
        return True

    @staticmethod
    async def _normalize_member_targets(
        db: AsyncSession,
        *,
        conversation_id: int,
        requested_ids: Iterable[int],
        fallback_excluding: int = 0,
    ) -> list[int]:
        members_result = await db.execute(
            select(ChatParticipant.employee_id).where(ChatParticipant.conversation_id == conversation_id)
        )
        member_ids = {int(row[0]) for row in members_result.all()}
        requested = {int(item) for item in requested_ids if int(item) > 0}
        if not requested:
            requested = {member_id for member_id in member_ids if member_id != int(fallback_excluding or 0)}
        invalid = requested - member_ids
        if invalid:
            raise HTTPException(status_code=400, detail="只能选择当前会话成员")
        return sorted(requested)

    @staticmethod
    async def _get_announcement_or_404(
        db: AsyncSession,
        *,
        conversation_id: int,
        announcement_id: int,
    ) -> ChatAnnouncement:
        result = await db.execute(
            select(ChatAnnouncement).where(
                ChatAnnouncement.id == announcement_id,
                ChatAnnouncement.conversation_id == conversation_id,
            )
        )
        announcement = result.scalar_one_or_none()
        if announcement is None:
            raise HTTPException(status_code=404, detail="公告不存在")
        return announcement

    @staticmethod
    def _ding_out(ding: ChatDing, sender: Employee | None) -> ChatDingOut:
        return ChatDingOut(
            id=ding.id,
            conversation_id=ding.conversation_id,
            message_id=ding.message_id,
            sender_id=ding.sender_id,
            sender_name=sender.name if sender else "未知员工",
            recipient_ids=[int(item) for item in (ding.recipient_ids or [])],
            acked_employee_ids=[int(item) for item in (ding.acked_employee_ids or [])],
            channels=[str(item) for item in (ding.channels or [])],
            content=ding.content,
            status=ding.status,
            created_at=ding.created_at,
        )

    @staticmethod
    def _todo_out(todo: ChatTodo, creator: Employee | None) -> ChatTodoOut:
        return ChatTodoOut(
            id=todo.id,
            conversation_id=todo.conversation_id,
            source_message_id=todo.source_message_id,
            created_by_id=todo.created_by_id,
            created_by_name=creator.name if creator else "未知员工",
            content=todo.content,
            assignee_ids=[int(item) for item in (todo.assignee_ids or [])],
            completed_by_ids=[int(item) for item in (todo.completed_by_ids or [])],
            due_at=todo.due_at,
            status=todo.status,
            created_at=todo.created_at,
            updated_at=todo.updated_at,
        )

    @staticmethod
    def _normalize_meeting_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _new_meeting_no() -> str:
        return f"MTG-{uuid4().hex[:10].upper()}"

    @staticmethod
    def _meeting_card_metadata(meeting: ChatMeeting) -> dict[str, Any]:
        return {
            "title": meeting.title,
            "meeting_id": meeting.id,
            "meeting_no": meeting.meeting_no,
            "meeting_type": meeting.meeting_type,
            "starts_at": meeting.starts_at.isoformat(),
            "ends_at": meeting.ends_at.isoformat(),
            "attendee_ids": [int(item) for item in (meeting.attendee_ids or [])],
            "attendee_count": len(meeting.attendee_ids or []),
            "room_booking_id": meeting.room_booking_id,
            "room_name": meeting.room_name,
            "room_location": meeting.room_location,
            "join_url": meeting.join_url,
            "reminder_minutes": meeting.reminder_minutes,
            "agenda": meeting.agenda,
            "equipment_needed": [str(item) for item in (meeting.equipment_needed or [])],
            "status": meeting.status,
            "title_label": "会议预约",
        }

    @staticmethod
    async def _get_meeting_or_404(db: AsyncSession, *, conversation_id: int, meeting_id: int) -> ChatMeeting:
        result = await db.execute(
            select(ChatMeeting).where(ChatMeeting.id == meeting_id, ChatMeeting.conversation_id == conversation_id)
        )
        meeting = result.scalar_one_or_none()
        if meeting is None:
            raise HTTPException(status_code=404, detail="会议不存在")
        return meeting

    @staticmethod
    async def _meeting_out(db: AsyncSession, meeting: ChatMeeting) -> ChatMeetingOut:
        attendee_ids = [int(item) for item in (meeting.attendee_ids or [])]
        employees = await ChatService._load_employees(db, [meeting.organizer_id, *attendee_ids])
        organizer = employees.get(meeting.organizer_id)
        return ChatMeetingOut(
            id=meeting.id,
            conversation_id=meeting.conversation_id,
            organizer_id=meeting.organizer_id,
            organizer_name=organizer.name if organizer else "未知员工",
            room_booking_id=meeting.room_booking_id,
            meeting_no=meeting.meeting_no,
            title=meeting.title,
            meeting_type=meeting.meeting_type,
            starts_at=meeting.starts_at,
            ends_at=meeting.ends_at,
            attendee_ids=attendee_ids,
            attendees=[
                ChatService._employee_brief(employee)
                for employee_id in attendee_ids
                if (employee := employees.get(employee_id)) is not None
            ],
            room_name=meeting.room_name,
            room_location=meeting.room_location,
            join_url=meeting.join_url,
            reminder_minutes=meeting.reminder_minutes,
            agenda=meeting.agenda,
            equipment_needed=[str(item) for item in (meeting.equipment_needed or [])],
            status=meeting.status,
            created_at=meeting.created_at,
            updated_at=meeting.updated_at,
        )

    @staticmethod
    async def _get_call_or_404(db: AsyncSession, *, conversation_id: int, call_id: int) -> ChatCall:
        result = await db.execute(
            select(ChatCall).where(ChatCall.id == call_id, ChatCall.conversation_id == conversation_id)
        )
        call = result.scalar_one_or_none()
        if call is None:
            raise HTTPException(status_code=404, detail="通话不存在")
        return call

    @staticmethod
    def _ensure_call_participant(call: ChatCall, employee_id: int) -> None:
        if int(employee_id) not in {int(item) for item in (call.participant_ids or [])}:
            raise HTTPException(status_code=403, detail="无权访问该通话")

    @staticmethod
    async def _call_out(db: AsyncSession, call: ChatCall) -> ChatCallOut:
        employees = await ChatService._load_employees(db, [call.initiator_id])
        initiator = employees.get(call.initiator_id)
        return ChatCallOut(
            id=call.id,
            conversation_id=call.conversation_id,
            call_type=call.call_type,
            status=call.status,
            initiator_id=call.initiator_id,
            initiator_name=initiator.name if initiator else "未知员工",
            participant_ids=[int(item) for item in (call.participant_ids or [])],
            accepted_employee_ids=[int(item) for item in (call.accepted_employee_ids or [])],
            rejected_employee_ids=[int(item) for item in (call.rejected_employee_ids or [])],
            ended_by_id=call.ended_by_id,
            ended_reason=call.ended_reason,
            started_at=call.started_at,
            ended_at=call.ended_at,
            created_at=call.created_at,
            updated_at=call.updated_at,
        )

    @staticmethod
    def _signal_out(signal: ChatCallSignal, sender: Employee | None) -> ChatCallSignalOut:
        return ChatCallSignalOut(
            id=signal.id,
            call_id=signal.call_id,
            conversation_id=signal.conversation_id,
            sender_id=signal.sender_id,
            sender_name=sender.name if sender else "未知员工",
            target_employee_id=signal.target_employee_id,
            signal_type=signal.signal_type,
            payload=signal.payload_json or {},
            created_at=signal.created_at,
        )

    @staticmethod
    def _scan_sensitive_content(text: str) -> tuple[str, bool, list[str]]:
        lowered = (text or "").lower()
        rules = {
            "身份证": ["身份证", "id card"],
            "银行卡": ["银行卡", "bank card"],
            "密码": ["密码", "password", "secret"],
            "薪资敏感": ["薪资明细外发", "工资条外发", "salary export"],
        }
        hits = [label for label, keywords in rules.items() if any(keyword in lowered for keyword in keywords)]
        return ("restricted" if hits else "normal", bool(hits), hits)
