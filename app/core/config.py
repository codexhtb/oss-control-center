"""Validated configuration sourced only from environment variables."""

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy.engine import URL


class Settings(BaseModel):
    app_env: Literal["dev", "test", "production"] = "dev"
    postgres_host: str = Field(default="postgres", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="oss_control", min_length=1)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr
    readiness_timeout_seconds: int = Field(default=2, ge=1, le=5)
    auth_public_origin: str
    auth_allow_http_loopback: bool = False
    auth_hash_key: SecretStr

    @field_validator("postgres_password")
    @classmethod
    def password_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("POSTGRES_PASSWORD must not be empty")
        return value

    @field_validator("auth_hash_key")
    @classmethod
    def auth_key_must_be_long(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("AUTH_HASH_KEY must have at least 32 bytes")
        return value

    @model_validator(mode="after")
    def validate_auth_origin(self) -> "Settings":
        try:
            origin = urlsplit(self.auth_public_origin)
            port = origin.port
        except ValueError as exc:
            raise ValueError("AUTH_PUBLIC_ORIGIN is invalid") from exc
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
            or port == 0
            or self.auth_public_origin != origin.geturl()
        ):
            raise ValueError("AUTH_PUBLIC_ORIGIN must be an exact origin")
        if origin.scheme == "http" and not (
            self.app_env == "dev"
            and self.auth_allow_http_loopback
            and origin.hostname == "127.0.0.1"
        ):
            raise ValueError("HTTP authentication is allowed only on explicit dev loopback")
        if self.auth_allow_http_loopback and self.app_env != "dev":
            raise ValueError("AUTH_ALLOW_HTTP_LOOPBACK is dev-only")
        return self

    @property
    def secure_cookie(self) -> bool:
        return self.auth_public_origin.startswith("https://")

    @property
    def sqlalchemy_url(self) -> URL:
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if environment is None else environment
        return cls.model_validate(
            {
                "app_env": values.get("APP_ENV", "dev"),
                "postgres_host": values.get("POSTGRES_HOST", "postgres"),
                "postgres_port": values.get("POSTGRES_PORT", "5432"),
                "postgres_db": values.get("POSTGRES_DB", "oss_control"),
                "postgres_user": values.get("POSTGRES_USER"),
                "postgres_password": values.get("POSTGRES_PASSWORD"),
                "readiness_timeout_seconds": values.get("READINESS_TIMEOUT_SECONDS", "2"),
                "auth_public_origin": values.get("AUTH_PUBLIC_ORIGIN"),
                "auth_allow_http_loopback": values.get("AUTH_ALLOW_HTTP_LOOPBACK", "false"),
                "auth_hash_key": values.get("AUTH_HASH_KEY"),
            }
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
