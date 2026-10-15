"""Standalone configuration. No EHR host, database or credentials are inherited."""

import os
import secrets
from pathlib import Path
from typing import Optional, Literal
from urllib.parse import urlparse
from urllib.parse import quote_plus
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator

BACKEND_DIR = Path(__file__).resolve().parents[2]


def local_secret():
    path = BACKEND_DIR / "data" / ".session-key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
    return path.read_text().strip()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"), case_sensitive=True, extra="ignore"
    )
    PROJECT_NAME: str = "售后管理系统"
    VERSION: str = "0.1.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DATABASE_BACKEND: str = "sqlite"
    DATABASE_URL_OVERRIDE: Optional[str] = None
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "aftersales"
    MYSQL_PASSWORD: str = ""
    MYSQL_DB: str = "aftersales_attendance"
    MYSQL_CHARSET: str = "utf8mb4"
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REMEMBER_ME_ACCESS_TOKEN_EXPIRE_DAYS: int = 7
    ALLOWED_ORIGINS: list[str] = [
        "http://127.0.0.1:5186",
        "http://localhost:5186",
        "http://127.0.0.1:5187",
        "http://localhost:5187",
    ]
    LOGIN_MODE: Literal["password", "hybrid", "wecom_only"] = "password"
    TEGENE_API_BASE_URL: str = ""
    TEGENE_ACCESS_TOKEN: str = ""
    TEGENE_TOKEN_URL: str = ""
    TEGENE_CLIENT_ID: str = ""
    TEGENE_CLIENT_SECRET: str = ""
    TEGENE_RESOURCE: str = ""
    TEGENE_SCOPE: str = ""
    WECHAT_WEB_REDIRECT_URI: str = ""
    WECHAT_MOBILE_REDIRECT_URI: str = ""
    WECHAT_CORP_ID: Optional[str] = None
    WECHAT_AGENT_ID: Optional[str] = None
    WECHAT_SECRET: Optional[str] = None
    TENCENT_MAP_WEB_SERVICE_KEY: Optional[str] = None
    TENCENT_MAP_BROWSER_KEY: Optional[str] = None
    TENCENT_MAP_BASE_URL: str = "https://apis.map.qq.com"
    TENCENT_MAP_TIMEOUT_SECONDS: int = 5
    BACKGROUND_JOBS: bool = True

    @model_validator(mode="after")
    def validate_secret(self):
        if not self.SECRET_KEY:
            if self.ENVIRONMENT == "production":
                raise ValueError("Production requires an explicit SECRET_KEY")
            self.SECRET_KEY = local_secret()
        if len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 characters")
        if self.LOGIN_MODE == "wecom_only" and not self.wecom_ready:
            raise ValueError("WeCom-only login requires CorpID, AgentID, Secret and both HTTPS callback URLs")
        return self

    @property
    def wecom_ready(self):
        urls = (self.WECHAT_WEB_REDIRECT_URI, self.WECHAT_MOBILE_REDIRECT_URI)
        return bool(self.WECHAT_CORP_ID and self.WECHAT_AGENT_ID and self.WECHAT_SECRET
                    and all(urlparse(u).scheme == "https" and urlparse(u).hostname
                            and not urlparse(u).username and not urlparse(u).fragment
                            and not urlparse(u).query for u in urls))

    @property
    def DATABASE_URL(self):
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        if self.DATABASE_BACKEND == "sqlite":
            return f"sqlite+aiosqlite:///{BACKEND_DIR}/data/aftersales.db"
        if self.DATABASE_BACKEND != "mysql":
            raise ValueError("Use sqlite for local development or mysql for deployment")
        return f"mysql+aiomysql://{self.MYSQL_USER}:{quote_plus(self.MYSQL_PASSWORD)}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DB}?charset={self.MYSQL_CHARSET}"

    @property
    def IS_MYSQL(self):
        return self.DATABASE_URL.startswith("mysql")


settings = Settings()
