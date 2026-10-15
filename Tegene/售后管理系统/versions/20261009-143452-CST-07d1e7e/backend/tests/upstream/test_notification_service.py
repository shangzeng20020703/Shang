"""
通知中心服务单元测试。
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.notification import NotificationService


class TestNotificationInboxVisibility:
    """消息中心不展示上下班打卡同步通知。"""

    def test_inbox_queries_filter_clock_punch_sync_notifications(self):
        list_source = inspect.getsource(NotificationService.list_for_user)
        count_source = inspect.getsource(NotificationService.get_unread_count)
        condition_sql = str(
            NotificationService._visible_inbox_condition().compile(
                compile_kwargs={"literal_binds": True}
            )
        )

        assert "_visible_inbox_condition" in list_source
        assert "_visible_inbox_condition" in count_source
        assert "notifications.notif_type = 'attendance'" in condition_sql
        assert "notifications.ref_type = 'attendance_record'" in condition_sql
        assert "考勤同步：%" in condition_sql


class TestNotificationReadByRef:
    """按业务关联清理未读通知，保证审批详情已读不影响其他消息。"""

    @pytest.mark.asyncio
    async def test_mark_read_by_ref_updates_only_current_user_ref_notifications(self):
        db = AsyncMock()
        result = MagicMock()
        result.rowcount = 2
        db.execute = AsyncMock(return_value=result)

        affected = await NotificationService.mark_read_by_ref(
            db,
            user_id=10,
            notif_type="approval",
            ref_type="approval_instance",
            ref_id=99,
        )
        compiled = str(
            db.execute.call_args.args[0].compile(
                compile_kwargs={"literal_binds": True}
            )
        )

        assert affected == 2
        assert "recipient_id = 10" in compiled
        assert "notif_type = 'approval'" in compiled
        assert "ref_type = 'approval_instance'" in compiled
        assert "ref_id = 99" in compiled
        assert "is_read IS false" in compiled
