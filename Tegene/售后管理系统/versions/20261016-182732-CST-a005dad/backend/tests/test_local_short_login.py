"""Three-digit demo logins stay inside the explicitly enabled local mode."""

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register tables for the isolated test database
from app.api.v1.auth import _authenticate_for_login
from app.core.config import settings
from app.core.database import Base
from app.core.security import get_password_hash
from app.models.employee import Employee
from app.schemas.employee import LoginRequest


def request_from(host: str) -> Request:
    return Request({"type": "http", "client": (host, 12345)})


@pytest.mark.asyncio
async def test_short_employee_numbers_work_only_for_local_passwordless_login(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "DATABASE_URL_OVERRIDE", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(settings, "LOCAL_PASSWORDLESS_LOGIN", True)
    monkeypatch.setattr(settings, "LOGIN_MODE", "password")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as db:
            db.add_all([
                Employee(employee_no="001", name="测试组员01", phone="19900008101",
                         hashed_password=get_password_hash("real-password"), status="在职"),
                Employee(employee_no="019", name="测试售后经理", phone="19900008401",
                         hashed_password=get_password_hash("real-password"), status="在职"),
                Employee(employee_no="020", name="非本批测试人员", phone="19900008402",
                         hashed_password=get_password_hash("real-password"), status="在职"),
            ])
            await db.commit()

            for account in ("001", "019"):
                employee, local_passwordless = await _authenticate_for_login(
                    request_from("127.0.0.1"), db, LoginRequest(phone=account)
                )
                assert employee.employee_no == account
                assert local_passwordless is True

            employee, local_passwordless = await _authenticate_for_login(
                request_from("::1"), db, LoginRequest(phone="19900008101")
            )
            assert employee.employee_no == "001"
            assert local_passwordless is True

            for account, host, password in (
                ("020", "127.0.0.1", ""),
                ("001", "192.0.2.1", ""),
                ("001", "127.0.0.1", "wrong-password"),
            ):
                with pytest.raises(HTTPException) as error:
                    await _authenticate_for_login(
                        request_from(host), db,
                        LoginRequest(phone=account, password=password),
                    )
                assert error.value.status_code == 401

            monkeypatch.setattr(settings, "LOCAL_PASSWORDLESS_LOGIN", False)
            with pytest.raises(HTTPException) as error:
                await _authenticate_for_login(
                    request_from("127.0.0.1"), db, LoginRequest(phone="001")
                )
            assert error.value.status_code == 401

            monkeypatch.setattr(settings, "LOCAL_PASSWORDLESS_LOGIN", True)
            monkeypatch.setattr(settings, "ENVIRONMENT", "production")
            with pytest.raises(HTTPException) as error:
                await _authenticate_for_login(
                    request_from("127.0.0.1"), db, LoginRequest(phone="001")
                )
            assert error.value.status_code == 401

            monkeypatch.setattr(settings, "ENVIRONMENT", "development")
            monkeypatch.setattr(settings, "LOGIN_MODE", "wecom_only")
            with pytest.raises(HTTPException) as error:
                await _authenticate_for_login(
                    request_from("127.0.0.1"), db, LoginRequest(phone="001")
                )
            assert error.value.status_code == 403
            monkeypatch.setattr(settings, "LOGIN_MODE", "password")

            employee, local_passwordless = await _authenticate_for_login(
                request_from("127.0.0.1"), db,
                LoginRequest(phone="19900008101", password="real-password"),
            )
            assert employee.employee_no == "001"
            assert local_passwordless is False
    finally:
        await engine.dispose()
