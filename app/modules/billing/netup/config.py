"""Dedicated NetUP read-only channel configuration; no shared research wrapper."""

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class NetupConfig(BaseModel):
    purpose: Literal["production", "lab"] = "production"
    host: str = Field(min_length=1)
    port: int = Field(default=3306, ge=1, le=65535)
    database: str = Field(default="UTM5", min_length=1)
    user: str = Field(min_length=1)
    password: SecretStr
    tls_ca: Path | None = None
    allow_plaintext_lab: bool = False
    connect_timeout: int = Field(default=3, ge=1, le=10)
    read_timeout: int = Field(default=5, ge=1, le=15)
    max_execution_ms: int = Field(default=3000, ge=100, le=10000)
    lab_as_of_utc: datetime | None = None

    @field_validator("password")
    @classmethod
    def require_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("NetUP password is required")
        return value

    @model_validator(mode="after")
    def validate_transport(self) -> "NetupConfig":
        if self.lab_as_of_utc is not None:
            if self.purpose != "lab":
                raise ValueError("Historical as_of is lab-only")
            if self.lab_as_of_utc.tzinfo is None or self.lab_as_of_utc.utcoffset() is None:
                raise ValueError("Lab as_of must have timezone")
            self.lab_as_of_utc = self.lab_as_of_utc.astimezone(UTC)
        if self.tls_ca is not None:
            if not self.tls_ca.is_file():
                raise ValueError("NetUP TLS CA file is required")
            return self
        try:
            lab_private_ip = ip_address(self.host).is_private
        except ValueError:
            lab_private_ip = False
        if self.purpose != "lab" or not self.allow_plaintext_lab or not lab_private_ip:
            raise ValueError("NetUP TLS CA is required outside explicit private lab mode")
        return self

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> "NetupConfig":
        values = os.environ if environment is None else environment
        return cls.model_validate(
            {
                "purpose": values.get("NETUP_PURPOSE", "production"),
                "host": values.get("NETUP_HOST"),
                "port": values.get("NETUP_PORT", "3306"),
                "database": values.get("NETUP_DATABASE", "UTM5"),
                "user": values.get("NETUP_USER"),
                "password": values.get("NETUP_PASSWORD"),
                "tls_ca": values.get("NETUP_TLS_CA"),
                "allow_plaintext_lab": values.get("NETUP_ALLOW_PLAINTEXT_LAB", "false"),
                "connect_timeout": values.get("NETUP_CONNECT_TIMEOUT", "3"),
                "read_timeout": values.get("NETUP_READ_TIMEOUT", "5"),
                "max_execution_ms": values.get("NETUP_MAX_EXECUTION_MS", "3000"),
                "lab_as_of_utc": values.get("NETUP_LAB_AS_OF_UTC") or None,
            }
        )

    def connect(self):
        """Open a single TLS connection; credentials never enter SQL or logs."""
        import pymysql

        tls_args = (
            {"ssl_ca": str(self.tls_ca), "ssl_verify_cert": True, "ssl_verify_identity": True}
            if self.tls_ca is not None
            else {}
        )
        return pymysql.connect(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password.get_secret_value(),
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            write_timeout=self.read_timeout,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
            **tls_args,
        )
