"""
加班申请 API 和业务逻辑单元测试
覆盖: OvertimeCreateRequest 验证 / 状态机 / 权限守卫
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import ValidationError

from fastapi import HTTPException


# ─── helpers ───────────────────────────────────────────────────────────


def _make_db():
    return AsyncMock()


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _scalars_result(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def _make_overtime_req(
    req_id=1,
    employee_id=1,
    status="pending",
    overtime_date=None,
):
    req = MagicMock()
    req.id = req_id
    req.employee_id = employee_id
    req.status = status
    req.approval_status = None
    req.approver_id = None
    req.approved_at = None
    req.reject_reason = None
    req.overtime_date = overtime_date or date(2026, 3, 5)
    return req


def _make_user(user_id=1, is_superuser=False):
    user = MagicMock()
    user.id = user_id
    user.is_superuser = is_superuser
    return user


# ─── Request schema validation ─────────────────────────────────────────


class TestOvertimeCreateRequestValidation:

    def test_valid_request_passes(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        req = OvertimeCreateRequest(
            overtime_date=date(2026, 3, 5),
            start_time="18:00",
            end_time="21:00",
            hours=3,
            overtime_type="weekday",
        )
        assert req.hours == 3

    def test_invalid_time_format_raises(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        with pytest.raises(ValidationError):
            OvertimeCreateRequest(
                overtime_date=date(2026, 3, 5),
                start_time="9:00",  # missing leading zero
                end_time="21:00",
                hours=3,
            )

    def test_start_ge_end_raises(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        with pytest.raises(ValidationError):
            OvertimeCreateRequest(
                overtime_date=date(2026, 3, 5),
                start_time="21:00",
                end_time="18:00",  # end before start
                hours=3,
            )

    def test_start_equals_end_raises(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        with pytest.raises(ValidationError):
            OvertimeCreateRequest(
                overtime_date=date(2026, 3, 5),
                start_time="18:00",
                end_time="18:00",
                hours=0.5,
            )

    def test_hours_exceeds_24_raises(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        with pytest.raises(ValidationError):
            OvertimeCreateRequest(
                overtime_date=date(2026, 3, 5),
                start_time="00:00",
                end_time="23:59",
                hours=25,
            )

    def test_hours_zero_raises(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        with pytest.raises(ValidationError):
            OvertimeCreateRequest(
                overtime_date=date(2026, 3, 5),
                start_time="18:00",
                end_time="21:00",
                hours=0,
            )

    def test_invalid_overtime_type_raises(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        with pytest.raises(ValidationError):
            OvertimeCreateRequest(
                overtime_date=date(2026, 3, 5),
                start_time="18:00",
                end_time="21:00",
                hours=3,
                overtime_type="invalid_type",
            )

    def test_all_valid_overtime_types_accepted(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        for ot in ("weekday", "weekend", "holiday"):
            req = OvertimeCreateRequest(
                overtime_date=date(2026, 3, 5),
                start_time="18:00",
                end_time="21:00",
                hours=3,
                overtime_type=ot,
            )
            assert req.overtime_type == ot

    def test_default_type_is_weekday(self):
        from app.api.v1.overtime import OvertimeCreateRequest
        req = OvertimeCreateRequest(
            overtime_date=date(2026, 3, 5),
            start_time="18:00",
            end_time="21:00",
            hours=3,
        )
        assert req.overtime_type == "weekday"


# ─── Approve action validation ─────────────────────────────────────────


class TestOvertimeApproveRequestValidation:

    def test_approve_action_valid(self):
        from app.api.v1.overtime import OvertimeApproveRequest
        req = OvertimeApproveRequest(action="approve")
        assert req.action == "approve"

    def test_reject_action_valid(self):
        from app.api.v1.overtime import OvertimeApproveRequest
        req = OvertimeApproveRequest(action="reject", reject_reason="不合规")
        assert req.action == "reject"
        assert req.reject_reason == "不合规"

    def test_invalid_action_raises(self):
        from app.api.v1.overtime import OvertimeApproveRequest
        with pytest.raises(ValidationError):
            OvertimeApproveRequest(action="cancel")


# ─── cancel_overtime logic ─────────────────────────────────────────────


class TestCancelOvertimeLogic:

    @pytest.mark.asyncio
    async def test_cancel_pending_success(self):
        from app.api.v1.overtime import cancel_overtime
        req = _make_overtime_req(status="pending")
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=1)
        result = await cancel_overtime(1, db=db, current_user=user)
        assert req.status == "cancelled"
        assert result == {"message": "已撤销"}

    @pytest.mark.asyncio
    async def test_cancel_others_request_raises_403(self):
        from app.api.v1.overtime import cancel_overtime
        req = _make_overtime_req(status="pending", employee_id=2)
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=1)  # different from req.employee_id
        with pytest.raises(HTTPException) as exc_info:
            await cancel_overtime(1, db=db, current_user=user)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_cancel_non_pending_raises_400(self):
        from app.api.v1.overtime import cancel_overtime
        req = _make_overtime_req(status="approved", employee_id=1)
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=1)
        with pytest.raises(HTTPException) as exc_info:
            await cancel_overtime(1, db=db, current_user=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_not_found_raises_404(self):
        from app.api.v1.overtime import cancel_overtime
        db = _make_db()
        db.execute.return_value = _scalar_result(None)

        user = _make_user(user_id=1)
        with pytest.raises(HTTPException) as exc_info:
            await cancel_overtime(999, db=db, current_user=user)
        assert exc_info.value.status_code == 404


# ─── approve_overtime logic ────────────────────────────────────────────


class TestApproveOvertimeLogic:

    @pytest.mark.asyncio
    async def test_approve_sets_all_fields(self):
        from app.api.v1.overtime import approve_overtime, OvertimeApproveRequest
        req = _make_overtime_req(status="pending")
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=5)
        data = OvertimeApproveRequest(action="approve")
        with patch(
            "app.api.v1.overtime.AttendanceRecordService.sync_approved_overtime_to_attendance",
            new=AsyncMock(),
        ) as sync_attendance:
            result = await approve_overtime(1, data=data, db=db, current_user=user)

        assert req.status == "approved"
        assert req.approval_status == "approved"
        assert req.approver_id == 5
        assert req.approved_at is not None
        assert req.approved_at.tzinfo is not None  # must be timezone-aware
        sync_attendance.assert_awaited_once_with(db, req)

    @pytest.mark.asyncio
    async def test_reject_sets_reason(self):
        from app.api.v1.overtime import approve_overtime, OvertimeApproveRequest
        req = _make_overtime_req(status="pending")
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=5)
        data = OvertimeApproveRequest(action="reject", reject_reason="工作量不足")
        with patch(
            "app.api.v1.overtime.AttendanceRecordService.sync_approved_overtime_to_attendance",
            new=AsyncMock(),
        ) as sync_attendance:
            await approve_overtime(1, data=data, db=db, current_user=user)

        assert req.status == "rejected"
        assert req.reject_reason == "工作量不足"
        sync_attendance.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_approve_non_pending_raises_400(self):
        from app.api.v1.overtime import approve_overtime, OvertimeApproveRequest
        req = _make_overtime_req(status="approved")
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=5)
        data = OvertimeApproveRequest(action="approve")
        with pytest.raises(HTTPException) as exc_info:
            await approve_overtime(1, data=data, db=db, current_user=user)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_approve_not_found_raises_404(self):
        from app.api.v1.overtime import approve_overtime, OvertimeApproveRequest
        db = _make_db()
        db.execute.return_value = _scalar_result(None)

        user = _make_user(user_id=5)
        data = OvertimeApproveRequest(action="approve")
        with pytest.raises(HTTPException) as exc_info:
            await approve_overtime(999, data=data, db=db, current_user=user)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_approved_at_is_timezone_utc(self):
        from app.api.v1.overtime import approve_overtime, OvertimeApproveRequest
        req = _make_overtime_req(status="pending")
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=5)
        data = OvertimeApproveRequest(action="approve")
        with patch(
            "app.api.v1.overtime.AttendanceRecordService.sync_approved_overtime_to_attendance",
            new=AsyncMock(),
        ):
            await approve_overtime(1, data=data, db=db, current_user=user)

        assert req.approved_at.tzinfo == timezone.utc


# ─── get_overtime detail logic ─────────────────────────────────────────


class TestGetOvertimeDetail:

    @pytest.mark.asyncio
    async def test_owner_can_view(self):
        from app.api.v1.overtime import get_overtime
        req = _make_overtime_req(employee_id=1)
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=1)
        result = await get_overtime(1, db=db, current_user=user)
        assert result is req

    @pytest.mark.asyncio
    async def test_superuser_can_view_any(self):
        from app.api.v1.overtime import get_overtime
        req = _make_overtime_req(employee_id=2)
        db = _make_db()
        db.execute.return_value = _scalar_result(req)

        user = _make_user(user_id=1, is_superuser=True)
        result = await get_overtime(1, db=db, current_user=user)
        assert result is req

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self):
        from app.api.v1.overtime import get_overtime
        db = _make_db()
        db.execute.return_value = _scalar_result(None)

        user = _make_user(user_id=1)
        with pytest.raises(HTTPException) as exc_info:
            await get_overtime(999, db=db, current_user=user)
        assert exc_info.value.status_code == 404
