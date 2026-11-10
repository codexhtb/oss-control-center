"""Public outbound field dispatch contracts."""

from datetime import datetime
from typing import Literal
from unicodedata import category
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SendFieldWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    visit_address: str | None = Field(default=None, max_length=500)
    message_text: str | None = Field(default=None, max_length=4096)
    direction: Literal["SUKHUM", "OCHAMCHIRA"] | None = None

    @field_validator("visit_address")
    @classmethod
    def valid_visit_address(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or value != value.strip() or any(category(ch) == "Cc" for ch in value)
        ):
            raise ValueError("visit_address must be trimmed and contain no control characters")
        return value


class FieldWorkHandoffView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    reason: str
    delivery_status: Literal["DRAFT", "SENDING", "SENT", "FAILED", "UNCERTAIN"]
    work_status: Literal["AWAITING_REPORT", "REPORTED", "VERIFIED"]
    chat_id: str
    direction: Literal["SUKHUM", "OCHAMCHIRA"] | None = None
    group_name: str | None = None
    schedule_status: Literal["ORIGINAL", "DATE_PENDING", "SCHEDULED"]
    scheduled_for: datetime | None
    schedule_version: int
    schedule_reason: str | None = None
    scheduled_by_name: str | None = None
    schedule_changed_at: datetime | None = None
    telegram_message_id: int | None
    message_preview: str | None = None
    generated_preview: str | None = None
    visit_address: str | None = None
    ready_to_send: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    created_by: UUID
    sent_by: UUID | None
    sent_by_name: str | None = None
    last_error_code: str | None
    created_at: datetime
    send_started_at: datetime | None
    sent_at: datetime | None
    reported_at: datetime | None
    verified_by: UUID | None
    verified_by_name: str | None = None
    verified_at: datetime | None
    updated_at: datetime
    completion_prompt_status: (
        Literal["REQUESTING", "WAITING", "FAILED", "UNCERTAIN", "COMPLETED"] | None
    ) = None
    completion_prompt_sent_at: datetime | None = None


class LatestFieldWorkHandoff(BaseModel):
    handoff: FieldWorkHandoffView | None


class RescheduleFieldWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    scheduled_for: datetime
    expected_version: int = Field(ge=0)

    @field_validator("scheduled_for")
    @classmethod
    def abkhazia_time(cls, value: datetime) -> datetime:
        if (
            value.tzinfo is None or value.utcoffset() is None
            or value.utcoffset().total_seconds() != 10800
        ):
            raise ValueError("scheduled_for must include UTC+03:00 offset")
        return value


class FieldWorkRescheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    handoff_id: UUID
    schedule_version: int
    reason: str
    scheduled_for: datetime | None
    source: Literal["TELEGRAM", "OSS"]
    author_name: str
    created_at: datetime


class FieldWorkReschedulePage(BaseModel):
    items: list[FieldWorkRescheduleView]
    page: int
    size: int
    total: int


class FieldExternalMessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    handoff_id: UUID
    kind: Literal["TEXT_REPLY", "COMPLETION_CALLBACK", "COMPLETION_REPORT"]
    sender_name: str
    body: str | None
    telegram_created_at: datetime
    received_at: datetime


class FieldExternalMessagePage(BaseModel):
    items: list[FieldExternalMessageView]
    page: int
    size: int
    total: int
