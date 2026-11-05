"""Strict contracts for editable Service Desk catalogs."""

from datetime import datetime
from enum import StrEnum
from unicodedata import category
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TemplatePurpose(StrEnum):
    DECISION = "decision"
    COMMENT = "comment"
    CALLBACK = "callback"


def _clean(value: str, *, multiline: bool = False) -> str:
    if not value or value != value.strip() or not value.strip():
        raise ValueError("text must be nonblank and trimmed")
    if any(category(char) == "Cc" and (not multiline or char not in "\n\t") for char in value):
        raise ValueError("text contains control characters")
    return value


class ReasonView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    code: str
    name: str
    sort_order: int
    is_active: bool
    version: int
    updated_at: datetime


class UpdateReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=160)
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        return _clean(value) if value is not None else None

    @model_validator(mode="after")
    def require_fields(self) -> "UpdateReason":
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("provide at least one non-null field")
        return self


class TemplateView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    body: str
    purpose: TemplatePurpose
    sort_order: int
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class CreateTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=160)
    body: str = Field(max_length=10000)
    purpose: TemplatePurpose
    sort_order: int = Field(default=0, ge=0, le=100000)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _clean(value)

    @field_validator("body")
    @classmethod
    def valid_body(cls, value: str) -> str:
        return _clean(value, multiline=True)


class UpdateTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=160)
    body: str | None = Field(default=None, max_length=10000)
    purpose: TemplatePurpose | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        return CreateTemplate.valid_name(value) if value is not None else None

    @field_validator("body")
    @classmethod
    def valid_body(cls, value: str | None) -> str | None:
        return CreateTemplate.valid_body(value) if value is not None else None

    @model_validator(mode="after")
    def require_fields(self) -> "UpdateTemplate":
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("provide at least one non-null field")
        return self
