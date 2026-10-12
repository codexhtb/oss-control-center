"""Validated configuration sourced only from environment variables."""

import os
from collections.abc import Mapping
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy.engine import URL


class Settings(BaseModel):
    app_env: Literal["dev", "test", "production"] = "dev"
    postgres_host: str = Field(default="postgres", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="oss_control", min_length=1)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr
    readiness_timeout_seconds: int = Field(default=2, ge=1, le=5)

    @field_validator("postgres_password")
    @classmethod
    def password_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("POSTGRES_PASSWORD must not be empty")
        return value

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
            }
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
