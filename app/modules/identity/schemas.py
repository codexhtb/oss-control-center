"""Identity request and public response schemas; password hashes never serialize."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.modules.identity.crypto import normalize_login, validate_password
from app.modules.identity.roles import Permission, Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(max_length=1024)


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str
    display_name: str = Field(min_length=1, max_length=150)
    role: Role
    password: SecretStr

    @field_validator("login")
    @classmethod
    def canonical_login(cls, value: str) -> str:
        return normalize_login(value)

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char in value for char in "\r\n\x00"):
            raise ValueError("display_name is invalid")
        return value

    @field_validator("password")
    @classmethod
    def strong_password(cls, value: SecretStr) -> SecretStr:
        validate_password(value.get_secret_value())
        return value


class ChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class UserResponse(BaseModel):
    id: UUID
    login: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MeResponse(BaseModel):
    user: UserResponse
    permissions: list[Permission]


class LoginResponse(MeResponse):
    expires_at: datetime


class PermissionsResponse(BaseModel):
    permissions: list[Permission]
