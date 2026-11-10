"""
安全模块单元测试
覆盖: JWT 生成/验证、密码哈希/校验
"""

from datetime import timedelta
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.security import create_access_token, verify_password, get_password_hash
from jose import jwt
from app.core.config import settings


class TestPasswordHashing:

    def test_hash_is_not_plain(self):
        pw = "TestPassword123"
        hashed = get_password_hash(pw)
        assert hashed != pw

    def test_verify_correct_password(self):
        pw = "TestPassword123"
        hashed = get_password_hash(pw)
        assert verify_password(pw, hashed) is True

    def test_verify_wrong_password(self):
        hashed = get_password_hash("correct")
        assert verify_password("wrong", hashed) is False

    def test_two_hashes_differ(self):
        pw = "SamePassword"
        h1 = get_password_hash(pw)
        h2 = get_password_hash(pw)
        # bcrypt 加盐，两次哈希值不同
        assert h1 != h2

    def test_verify_empty_password(self):
        hashed = get_password_hash("notempty")
        assert verify_password("", hashed) is False


class TestJWTToken:

    def test_token_contains_sub(self):
        token = create_access_token(subject="42")
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert payload["sub"] == "42"

    def test_token_expires(self):
        token = create_access_token(subject="1", expires_delta=timedelta(minutes=30))
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert "exp" in payload

    def test_expired_token_raises(self):
        from jose import JWTError
        token = create_access_token(subject="1", expires_delta=timedelta(seconds=-1))
        with pytest.raises(JWTError):
            jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    def test_wrong_secret_raises(self):
        from jose import JWTError
        token = create_access_token(subject="1")
        with pytest.raises(JWTError):
            jwt.decode(token, "wrong-secret", algorithms=[settings.ALGORITHM])

    def test_subject_is_string(self):
        token = create_access_token(subject=99)
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert payload["sub"] == "99"
