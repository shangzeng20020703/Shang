"""
认证依赖单元测试
覆盖: JWT sub 非法值处理、凭据验证逻辑
"""

from datetime import timedelta

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.security import create_access_token
from jose import jwt
from app.core.config import settings


class TestJwtSubValidation:
    """验证 JWT sub 字段的合法性处理"""

    def test_integer_sub_decodes_as_string(self):
        """create_access_token 将整数 sub 转成字符串存入 JWT"""
        token = create_access_token(subject=42)
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert payload["sub"] == "42"

    def test_string_int_sub_is_valid(self):
        """字符串形式的整数 sub 可正常解码"""
        token = create_access_token(subject="123")
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert int(payload["sub"]) == 123

    def test_non_integer_sub_causes_value_error(self):
        """非整数字符串 sub（如 'admin'）调用 int() 时应抛 ValueError"""
        with pytest.raises(ValueError):
            int("admin")

    def test_none_sub_causes_type_error(self):
        """None sub 调用 int() 时应抛 TypeError"""
        with pytest.raises(TypeError):
            int(None)

    def test_int_conversion_guard_handles_alpha(self):
        """模拟 deps.py 中的 try/except 保护逻辑"""
        user_id = "not-a-number"
        converted = None
        try:
            converted = int(user_id)
        except (ValueError, TypeError):
            pass
        assert converted is None  # 没有崩溃，返回 None

    def test_int_conversion_guard_handles_none(self):
        """模拟 deps.py 中 sub=None 时的 try/except 保护逻辑"""
        user_id = None
        converted = None
        try:
            converted = int(user_id)
        except (ValueError, TypeError):
            pass
        assert converted is None

    def test_int_conversion_succeeds_for_valid_id(self):
        """正常整数字符串应成功转换"""
        user_id = "7"
        converted = int(user_id)
        assert converted == 7


class TestManagementLoginRolePolicy:
    """管理端登录必须由 RBAC 管理角色授权。"""

    def test_employee_role_cannot_enter_management_login(self):
        from app.api.v1.auth import has_management_login_role

        assert has_management_login_role({"employee"}) is False

    def test_management_roles_can_enter_management_login(self):
        from app.api.v1.auth import MANAGEMENT_LOGIN_ROLES, has_management_login_role

        for role in MANAGEMENT_LOGIN_ROLES:
            assert has_management_login_role({role}) is True

    def test_mixed_employee_and_manager_can_enter_management_login(self):
        from app.api.v1.auth import has_management_login_role

        assert has_management_login_role({"employee", "manager"}) is True


class TestRememberMeLoginToken:
    """移动端保持登录应使用显式长效 Token，而不是保存明文密码。"""

    def test_login_request_defaults_to_short_session(self):
        from app.schemas.employee import LoginRequest

        data = LoginRequest(phone="13800000000", password="123456")

        assert data.remember_me is False

    def test_login_request_accepts_remember_me(self):
        from app.schemas.employee import LoginRequest

        data = LoginRequest(phone="13800000000", password="123456", remember_me=True)

        assert data.remember_me is True

    def test_remember_me_uses_configured_long_delta(self):
        from app.api.v1.auth import get_login_token_expires_delta

        assert get_login_token_expires_delta(False) is None
        assert get_login_token_expires_delta(True) == timedelta(
            days=settings.REMEMBER_ME_ACCESS_TOKEN_EXPIRE_DAYS
        )
