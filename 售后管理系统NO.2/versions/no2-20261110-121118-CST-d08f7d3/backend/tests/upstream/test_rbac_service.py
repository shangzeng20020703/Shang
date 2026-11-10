"""
RBAC 角色管理 API 单元测试
覆盖: list_employee_roles / grant_role / revoke_role
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException


# ─── helpers ───────────────────────────────────────────────────────────


def _make_db():
    return AsyncMock()


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    r.scalars.return_value.first.return_value = value if value else None
    return r


def _scalars_result(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    r.scalars.return_value.first.return_value = items[0] if items else None
    return r


def _make_employee_role(role_id=1, employee_id=1, role_name="hr", is_active=True):
    role = MagicMock()
    role.id = role_id
    role.employee_id = employee_id
    role.role_name = role_name
    role.is_active = is_active
    role.granted_by = None
    role.created_at = datetime.now(timezone.utc)
    return role


def _make_employee(emp_id=1, is_active=True):
    emp = MagicMock()
    emp.id = emp_id
    emp.is_active = is_active
    return emp


def _make_admin_user(user_id=1):
    user = MagicMock()
    user.id = user_id
    user.is_active = True
    return user


# ─── list_employee_roles ───────────────────────────────────────────────


class TestListEmployeeRoles:

    @pytest.mark.asyncio
    async def test_returns_all_roles_for_employee(self):
        from app.api.v1.rbac import list_employee_roles
        roles = [
            _make_employee_role(1, 1, "admin"),
            _make_employee_role(2, 1, "hr"),
        ]
        db = _make_db()
        db.execute.return_value = _scalars_result(roles)

        result = await list_employee_roles(employee_id=1, db=db, _=_make_employee())
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_no_roles(self):
        from app.api.v1.rbac import list_employee_roles
        db = _make_db()
        db.execute.return_value = _scalars_result([])

        result = await list_employee_roles(employee_id=999, db=db, _=_make_employee())
        assert result == []

    @pytest.mark.asyncio
    async def test_includes_inactive_roles(self):
        from app.api.v1.rbac import list_employee_roles
        roles = [
            _make_employee_role(1, 1, "hr", is_active=True),
            _make_employee_role(2, 1, "admin", is_active=False),
        ]
        db = _make_db()
        db.execute.return_value = _scalars_result(roles)

        result = await list_employee_roles(employee_id=1, db=db, _=_make_employee())
        assert len(result) == 2


# ─── grant_role ───────────────────────────────────────────────────────


class TestGrantRole:

    def _make_grant_request(self, role_name="hr"):
        from app.api.v1.rbac import RoleGrantRequest
        return RoleGrantRequest(role_name=role_name)

    @pytest.mark.asyncio
    async def test_grant_role_success(self):
        from app.api.v1.rbac import grant_role
        new_role = _make_employee_role(1, 2, "hr")
        db = _make_db()

        # First call: employee exists, second call: no existing role
        def side_effect(*args, **kwargs):
            call_count = db.execute.call_count
            if call_count == 1:
                return _scalar_result(_make_employee(2))  # employee exists
            elif call_count == 2:
                return _scalars_result([])  # no duplicate role
            return _scalar_result(new_role)

        db.execute = AsyncMock(side_effect=side_effect)
        db.add = MagicMock()  # db.add() is synchronous in SQLAlchemy
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        admin = _make_admin_user()
        result = await grant_role(
            employee_id=2,
            data=self._make_grant_request("hr"),
            db=db,
            current_user=admin,
        )
        assert db.add.called

    @pytest.mark.asyncio
    async def test_grant_invalid_role_raises_400(self):
        from app.api.v1.rbac import grant_role
        db = _make_db()
        admin = _make_admin_user()

        with pytest.raises(HTTPException) as exc_info:
            await grant_role(
                employee_id=2,
                data=self._make_grant_request("superadmin"),
                db=db,
                current_user=admin,
            )
        assert exc_info.value.status_code == 400
        assert "无效角色" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_grant_to_nonexistent_employee_raises_404(self):
        from app.api.v1.rbac import grant_role
        db = _make_db()
        db.execute.return_value = _scalar_result(None)  # employee not found
        admin = _make_admin_user()

        with pytest.raises(HTTPException) as exc_info:
            await grant_role(
                employee_id=999,
                data=self._make_grant_request("hr"),
                db=db,
                current_user=admin,
            )
        assert exc_info.value.status_code == 404
        assert "员工不存在" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_grant_duplicate_role_raises_400(self):
        from app.api.v1.rbac import grant_role
        existing_role = _make_employee_role(1, 2, "hr", is_active=True)

        db = _make_db()

        def side_effect(*args, **kwargs):
            call_count = db.execute.call_count
            if call_count == 1:
                return _scalar_result(_make_employee(2))
            else:
                return _scalars_result([existing_role])  # duplicate exists

        db.execute = AsyncMock(side_effect=side_effect)
        admin = _make_admin_user()

        with pytest.raises(HTTPException) as exc_info:
            await grant_role(
                employee_id=2,
                data=self._make_grant_request("hr"),
                db=db,
                current_user=admin,
            )
        assert exc_info.value.status_code == 400
        assert "已拥有" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_roles_accepted(self):
        """当前产品角色集应包含行政管理员 asset_admin。"""
        from app.api.v1.rbac import grant_role, VALID_ROLES
        assert VALID_ROLES == {"admin", "asset_admin", "hr", "manager", "finance", "employee"}

    @pytest.mark.asyncio
    async def test_granted_by_set_to_current_user(self):
        from app.api.v1.rbac import grant_role

        captured_role = {}

        db = _make_db()

        def side_effect(*args, **kwargs):
            call_count = db.execute.call_count
            if call_count == 1:
                return _scalar_result(_make_employee(2))
            else:
                return _scalars_result([])

        db.execute = AsyncMock(side_effect=side_effect)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        original_add = db.add
        def capture_add(obj):
            captured_role["obj"] = obj
        db.add = capture_add

        admin = _make_admin_user(user_id=99)
        await grant_role(
            employee_id=2,
            data=self._make_grant_request("manager"),
            db=db,
            current_user=admin,
        )

        assert captured_role["obj"].granted_by == 99


# ─── revoke_role ───────────────────────────────────────────────────────


class TestRevokeRole:

    @pytest.mark.asyncio
    async def test_revoke_sets_is_active_false(self):
        from app.api.v1.rbac import revoke_role
        role = _make_employee_role(1, 1, "hr", is_active=True)
        db = _make_db()
        db.execute.return_value = _scalar_result(role)

        result = await revoke_role(role_id=1, db=db, _=_make_admin_user())
        assert role.is_active == False
        assert result == {"message": "角色已撤销"}

    @pytest.mark.asyncio
    async def test_revoke_not_found_raises_404(self):
        from app.api.v1.rbac import revoke_role
        db = _make_db()
        db.execute.return_value = _scalar_result(None)

        with pytest.raises(HTTPException) as exc_info:
            await revoke_role(role_id=999, db=db, _=_make_admin_user())
        assert exc_info.value.status_code == 404
        assert "角色记录不存在" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_revoke_already_inactive_is_idempotent(self):
        from app.api.v1.rbac import revoke_role
        role = _make_employee_role(1, 1, "hr", is_active=False)
        db = _make_db()
        db.execute.return_value = _scalar_result(role)

        # should not raise
        result = await revoke_role(role_id=1, db=db, _=_make_admin_user())
        assert role.is_active == False
        assert result["message"] == "角色已撤销"
