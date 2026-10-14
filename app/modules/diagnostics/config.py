"""Monitoring endpoints and explicit NetUP-to-monitoring mappings.

Credentials are read from protected local files, never from request payloads.
"""

import json
import os
import stat
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def protected_text(path: str) -> str:
    file = Path(path)
    if file.is_symlink():
        raise ValueError("monitoring secret path must not be a symlink")
    metadata = file.stat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("monitoring secret file must be regular and mode 0600 or stricter")
    if metadata.st_size < 1 or metadata.st_size > 4096:
        raise ValueError("monitoring secret file size is invalid")
    return file.read_text(encoding="utf-8").strip()


class PortMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    netup_port_id: int = Field(gt=0)
    librenms_port_id: int = Field(gt=0)


class DeviceMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    netup_switch_id: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    librenms_device_id: int | None = Field(default=None, gt=0)
    match_basis: Literal["manual_verified", "unique_exact_sysname"] = "manual_verified"
    icinga_host: str | None = Field(default=None, min_length=1, max_length=200)
    ports: list[PortMapping] = Field(default_factory=list, max_length=4096)

    @model_validator(mode="after")
    def no_duplicate_ports(self) -> "DeviceMapping":
        ports = [port.netup_port_id for port in self.ports]
        if len(ports) != len(set(ports)):
            raise ValueError("duplicate NetUP port mapping")
        return self

    @field_validator("icinga_host")
    @classmethod
    def safe_host(cls, value: str | None) -> str | None:
        if value is not None and (
            not value
            or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for c in value
            )
        ):
            raise ValueError("Icinga host mapping contains unsupported characters")
        return value


class MappingRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    devices: list[DeviceMapping] = Field(default_factory=list, max_length=10000)

    @field_validator("devices")
    @classmethod
    def no_duplicates(cls, items: list[DeviceMapping]) -> list[DeviceMapping]:
        keys = [item.netup_switch_id for item in items]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate NetUP switch mapping")
        for item in items:
            ports = [port.netup_port_id for port in item.ports]
            if len(ports) != len(set(ports)):
                raise ValueError("duplicate NetUP port mapping")
        return items

    @classmethod
    def from_file(cls, path: str | None) -> "MappingRegistry":
        if not path:
            return cls()
        file = Path(path)
        metadata = file.stat()
        if (
            file.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size > 262144
        ):
            raise ValueError("monitoring mapping file is invalid")
        return cls.model_validate(json.loads(file.read_text(encoding="utf-8")))

    def device(self, switch_id: str) -> DeviceMapping | None:
        return next((item for item in self.devices if item.netup_switch_id == switch_id), None)


class MonitoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_env: Literal["dev", "test", "production"]
    librenms_url: str | None = None
    librenms_token_file: str | None = None
    icinga_url: str | None = None
    icinga_user: str | None = None
    icinga_password_file: str | None = None
    ca_file: str | None = None
    mapping_file: str | None = None
    allow_http_dev: bool = False
    timeout_seconds: float = Field(default=3.0, gt=0, le=10)

    @classmethod
    def from_env(cls) -> "MonitoringConfig":
        return cls(
            app_env=os.environ.get("APP_ENV", "dev"),
            librenms_url=os.environ.get("LIBRENMS_URL"),
            librenms_token_file=os.environ.get("LIBRENMS_TOKEN_FILE"),
            icinga_url=os.environ.get("ICINGA_URL"),
            icinga_user=os.environ.get("ICINGA_USER"),
            icinga_password_file=os.environ.get("ICINGA_PASSWORD_FILE"),
            ca_file=os.environ.get("MONITORING_CA_FILE"),
            mapping_file=os.environ.get("MONITORING_MAPPING_FILE"),
            allow_http_dev=os.environ.get("MONITORING_ALLOW_HTTP_DEV", "false").lower() == "true",
            timeout_seconds=float(os.environ.get("MONITORING_TIMEOUT_SECONDS", "3")),
        )

    def validate_endpoint(self, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError("monitoring endpoint must be an origin URL")
        if parts.scheme == "http" and not (self.app_env == "dev" and self.allow_http_dev):
            raise ValueError("plaintext monitoring endpoint is dev-only and requires opt-in")
        return value.rstrip("/")
