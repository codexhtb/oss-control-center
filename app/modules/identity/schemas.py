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
    permissions: list[Permission] | None = None

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


class UpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=150)

    @field_validator("login")
    @classmethod
    def canonical_login(cls, value: str | None) -> str | None:
        return normalize_login(value) if value is not None else None

    @field_validator("display_name")
    @classmethod
    def clean_display_name(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value or any(char in value for char in "\r\n\x00"):
                raise ValueError("display_name is invalid")
        return value


class SetPermissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permissions: list[Permission] = Field(max_length=32)

    @field_validator("permissions")
    @classmethod
    def unique_permissions(cls, value: list[Permission]) -> list[Permission]:
        if len(value) != len(set(value)):
            raise ValueError("permissions must be unique")
        return value


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: SecretStr
    new_password: SecretStr

    @field_validator("new_password")
    @classmethod
    def strong_new_password(cls, value: SecretStr) -> SecretStr:
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
    must_change_password: bool = False
    created_at: datetime
    updated_at: datetime


class MeResponse(BaseModel):
    user: UserResponse
    permissions: list[Permission]


class ManagedUserResponse(UserResponse):
    permissions: list[Permission]


class CreatedUserResponse(ManagedUserResponse):
    temporary_password: str | None = None


class ResetPasswordResponse(BaseModel):
    temporary_password: str


class IdentityAuditView(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    actor_name: str | None = None
    target_user_id: UUID | None
    target_name: str | None = None
    action: str
    outcome: str
    details: dict | None
    occurred_at: datetime


class LoginResponse(MeResponse):
    expires_at: datetime


class PermissionsResponse(BaseModel):
    permissions: list[Permission]
